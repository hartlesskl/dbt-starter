"""`uv run --locked --extra fabric verify-fabric`: drive and observe the native PORT-04 builds.

The verification command turns each native build into evidence without a hand-typed result
(D-08 to D-10, D-12). Its subcommands, each with `--label NAME` (evidence in
target/verify-fabric/<label>/):

    init [--fresh]          create the verification tables the results hook writes to; with
                            --fresh, first drop the layer schemas, the verification schema and the
                            two fixture landing tables, so the sequence starts from nothing
    land-fixture [--variant]
                            land the synthetic fixture into the Lakehouse through the sink; with
                            --variant, re-land only the example source's added-column variant
                            file (the same rows plus one undeclared column, D-09)
    start --build N         start native build N (1 to 4) over REST when a REST start is proven,
                            otherwise print the portal settings; record when it started
    wait --build N          find the run in the job's instance list, read its results from the
                            verification tables (or, when the run wrote no hook row, from its
                            downloaded log in build-N-log/) and write build-N.json
    self-hosted             run build-fabric's deps and `dbt build --target fabric --selector
                            fabric_fixture` from this machine and write self-hosted.json from
                            target/run_results.json and the invocation's hook row
    parity                  build the fixture on DuckDB, count every report relation there and on
                            the Lakehouse, and write parity.json (PORT-04)
    sequence [--fresh]      init, land-fixture, native builds 1 to 3, land-fixture --variant,
                            build 4, self-hosted and parity, in that order, stopping at the first
                            failure (`make verify-fabric`)

The native job cannot be started over REST today (plan 05-04: 400 InvalidJobType), so `start`
prints what to set in the portal and `wait` polls the read-only instance list for the run that
started after the recorded time. The run is matched to its verification.invocations row by the
job instance's own start and end times (Fabric's clock) widened by WINDOW_SECONDS, never by this
machine's clock against Spark's.

A small trial capacity fits one Spark session at a time and a session keeps the capacity for a
while after its call returns, so every call that opens one (a dbt run-operation, a query) first
waits until the workspace has no active Livy session and then CAPACITY_SETTLE_SECONDS more.

Messages name variables, relations, builds and counts, never an id, a token or a Location URL.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from starter_cli import build_fabric, fabric_query, fabric_rest, fabric_sink, gate
from starter_cli.landing import DownloadError, LandingError, load_entity
from starter_cli.project import discover_sources, repo_root

VERIFY_FABRIC_COMMAND = "uv run --locked --extra fabric verify-fabric"
EXTRA_HINT = (
    "verify-fabric needs the fabric extra (dbt-fabricspark and the Lakehouse sink). "
    f"Run it as `{VERIFY_FABRIC_COMMAND} ...`."
)

REQUIRED_JOB_ENV: tuple[str, ...] = ("FABRIC_DBT_JOB_ID",)
BUILDS: tuple[int, ...] = (1, 2, 3, 4)
# Build 3 is the `--full-refresh` build of the sequence (D-08), started in the portal with the
# job's Full refresh switch on; every other build runs with it off.
FULL_REFRESH_BUILD = 3
# The build that runs after the added-column variant is landed (D-09).
ADDED_COLUMN_BUILD = 4
EXIT_STILL_RUNNING = 75
POLL_SECONDS = 30.0
DEFAULT_TIMEOUT_MINUTES = 9  # one Bash call stays under ten minutes
WINDOW_SECONDS = 120
CAPACITY_POLL_SECONDS = 15.0
CAPACITY_POLLS = 60  # fifteen minutes
CAPACITY_SETTLE_SECONDS = 75.0

TERMINAL_STATUSES: tuple[str, ...] = ("Completed", "Failed", "Cancelled")
START_MODES: tuple[str, ...] = ("rest", "manual")
NATIVE_SELECTOR = "fabric_fixture"

# What `init --fresh` drops: the three layer schemas and the verification schema (with cascade),
# and the fixture's own landing tables. The landing schema itself is never dropped.
DROPPED_SCHEMAS: tuple[str, ...] = ("bronze", "silver", "gold", "verification")
LANDING_SCHEMA = fabric_sink.LANDING_SCHEMA


def fixture_sources() -> list[tuple[str, Any]]:
    """Every discovered source that ships a fixture (`write_fixture`), sorted by name.

    The driver never names a source, as the gate does not: it finds the example through
    `discover_sources()`, so removing the example leaves it working on what remains.
    """
    return [
        (name, module)
        for name, module in sorted(discover_sources().items())
        if hasattr(module, "write_fixture")
    ]


# Derived from the entity specs of the fixture sources, never typed, so it is the names the loader
# writes (`EntitySpec.table`).
FIXTURE_LANDING_TABLES: tuple[str, ...] = tuple(
    sorted(
        f"{LANDING_SCHEMA}.{spec.table}"
        for _, module in fixture_sources()
        for spec in module.ENTITIES.values()
    )
)

# The one node-status vocabulary of the record: a node whose status is not one of these (skipped
# included) is a failing node (D-14). Plans 05-09, 05-10, 05-11 and 05-21 import it.
NODE_OK_STATUSES: tuple[str, ...] = ("success", "pass", "warn")

# The pinned evidence schema. Plans 05-09, 05-11, 05-15 and 05-21 read these names and no others.
INVOCATION_KEYS: tuple[str, ...] = (
    "dbt_version",
    "engine_version",
    "session_time_zone",
    "command",
    "full_refresh",
)
NODE_KEYS: tuple[str, ...] = ("unique_id", "resource_type", "status", "failures", "execution_time")
BUILD_KEYS: tuple[str, ...] = (
    "build",
    "mode",
    "full_refresh_expected",
    "started_at",
    "job_status",
    "failure_code",
    "results_source",
    "invocation",
    "status_counts",
    "nodes",
)
STATE_BUILD_KEYS: tuple[str, ...] = ("started_at", "mode")
RESULTS_SOURCES: tuple[str, ...] = ("hook", "log")

LABEL_RE = re.compile(r"^[a-z0-9-]+$")
DEFAULT_LABEL = "adhoc"
ERROR_CODE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
FABRIC_TIME_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]00:?00)?$"
)


class VerifyError(Exception):
    """A step could not go on. The message names what is wrong, never an id or a value."""

    def __init__(self, message: str, code: int = 1) -> None:
        self.code = code
        super().__init__(message)


def _now() -> datetime:
    return datetime.now(UTC)


def _evidence_root() -> Path:
    return repo_root() / "target" / "verify-fabric"


def _run_dbt(command: list[str], root: Path, env: dict[str, str]) -> int:
    return build_fabric.RUNNER(command, root, env)


def _run_queries(
    queries: Mapping[str, str], env: Mapping[str, str]
) -> dict[str, list[list[str | None]]]:
    return fabric_query.run_queries(queries, target="fabric", env=env)


# Module-level seams, so a test replaces the clock, the sleeps, the evidence folder, dbt, the query
# bridge, the sink and the loader, and never reaches a network or a Lakehouse.
NOW: Callable[[], datetime] = _now
SLEEP: Callable[[float], None] = time.sleep
EVIDENCE_ROOT: Callable[[], Path] = _evidence_root
RUNNER: Callable[[list[str], Path, dict[str, str]], int] = _run_dbt
RUN_QUERIES: Callable[[Mapping[str, str], Mapping[str, str]], dict[str, list[list[str | None]]]] = (
    _run_queries
)
SINK_FACTORY: Callable[[str, str], Any] = fabric_sink.LakehouseSink
LOAD_ENTITY: Callable[..., int] = load_entity
BUILD_FIXTURE: Callable[[Path, Path], gate.FixtureBuild] = gate.build_fixture
PROMPT: Callable[[str], str] = input


def extra_installed() -> bool:
    """True when both halves of the fabric extra are importable: the adapter and the sink."""
    return build_fabric.fabric_adapter_installed() and fabric_sink.sink_installed()


def environment_problems(env: Mapping[str, str], *, needs_job: bool) -> list[str]:
    """Return what is wrong with the variables, naming variables and never their values."""
    problems = build_fabric.check_environment(env)
    if needs_job:
        for name in REQUIRED_JOB_ENV:
            value = env.get(name, "").strip()
            if not value:
                problems.append(f"{name} is not set")
            elif not build_fabric.UUID_RE.match(value):
                problems.append(f"{name} is not a UUID")
    return problems


def _say(message: str, *, error: bool = False) -> None:
    print(f"verify-fabric: {message}", file=sys.stderr if error else sys.stdout, flush=True)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sql_time(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def parse_time(text: object) -> datetime | None:
    """Read a Fabric or state time: ISO-8601, UTC when it carries no zone, any fraction length."""
    if not isinstance(text, str):
        return None
    match = FABRIC_TIME_RE.match(text.strip())
    if not match:
        return None
    day, clock, fraction, _zone = match.groups()
    micro = int((fraction or "0")[:6].ljust(6, "0"))
    moment = datetime.strptime(f"{day} {clock}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    return moment + timedelta(microseconds=micro)


def _as_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return None


def _as_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(float(str(value)))
    except ValueError:
        return None


def _as_float(value: object) -> float:
    try:
        return float(str(value)) if value is not None else 0.0
    except ValueError:
        return 0.0


def status_counts(nodes: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Return {resource_type: {status: count}} over the nodes, keys sorted."""
    counts: dict[str, dict[str, int]] = {}
    for node in nodes:
        by_status = counts.setdefault(str(node["resource_type"]), {})
        by_status[str(node["status"])] = by_status.get(str(node["status"]), 0) + 1
    return {kind: dict(sorted(by_status.items())) for kind, by_status in sorted(counts.items())}


# ---------------------------------------------------------------------------------------------
# Evidence files


def label_dir(label: str) -> Path:
    return EVIDENCE_ROOT() / label


def state_path(label: str) -> Path:
    return label_dir(label) / "state.json"


def build_path(label: str, build: int) -> Path:
    return label_dir(label) / f"build-{build}.json"


def read_state(label: str) -> dict[str, Any]:
    path = state_path(label)
    if not path.exists():
        return {"builds": {}}
    state = json.loads(path.read_text(encoding="utf-8"))
    builds = state.get("builds") if isinstance(state, dict) else None
    return {"builds": builds if isinstance(builds, dict) else {}}


def record_start(label: str, build: int, started_at: datetime, mode: str) -> None:
    """Record build N's start under state.json `builds`, keeping the other builds."""
    state = read_state(label)
    state["builds"][str(build)] = {"started_at": _iso(started_at), "mode": mode}
    path = state_path(label)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_build(label: str, record: dict[str, Any]) -> Path:
    """Write build-N.json with exactly BUILD_KEYS; refuse any other shape."""
    if tuple(record) != BUILD_KEYS:
        raise ValueError("a build record must carry exactly the pinned keys")
    if tuple(record["invocation"]) != INVOCATION_KEYS:
        raise ValueError("an invocation must carry exactly the pinned keys")
    if any(tuple(node) != NODE_KEYS for node in record["nodes"]):
        raise ValueError("a node must carry exactly the pinned keys")
    if record["results_source"] not in RESULTS_SOURCES:
        raise ValueError("results_source must be hook or log")
    path = build_path(label, record["build"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------------------------
# Capacity


def await_capacity(env: Mapping[str, str]) -> None:
    """Wait until the workspace runs no Livy session, then CAPACITY_SETTLE_SECONDS more."""
    workspace = env["FABRIC_WORKSPACE_ID"].strip()
    for _ in range(CAPACITY_POLLS):
        if fabric_rest.active_livy_sessions(workspace) == 0:
            SLEEP(CAPACITY_SETTLE_SECONDS)
            return
        SLEEP(CAPACITY_POLL_SECONDS)
    raise VerifyError(
        "the capacity still runs a Spark session after 15 minutes; rerun when it is free"
    )


def query(queries: Mapping[str, str], env: Mapping[str, str]) -> dict[str, list[list[str | None]]]:
    """One query-bridge call (one Spark session) after the capacity wait."""
    await_capacity(env)
    return RUN_QUERIES(queries, env)


# ---------------------------------------------------------------------------------------------
# init


def cmd_init(args: argparse.Namespace, env: dict[str, str], root: Path) -> int:
    if args.fresh:
        listed = query({"landing_tables": f"show tables in {LANDING_SCHEMA}"}, env)
        present = {
            f"{LANDING_SCHEMA}.{row[1]}".lower()
            for row in listed["landing_tables"]
            if len(row) > 1 and isinstance(row[1], str)
        }
        for relation in FIXTURE_LANDING_TABLES:
            if relation.lower() not in present:
                raise VerifyError(
                    f"{relation} is not in the Lakehouse, so --fresh has nothing to reset; "
                    "run init without --fresh"
                )
        drops: dict[str, str] = {}
        for schema in DROPPED_SCHEMAS:
            _say(f"dropping schema {schema}")
            drops[f"drop_schema_{schema}"] = f"drop schema if exists {schema} cascade"
        for relation in FIXTURE_LANDING_TABLES:
            _say(f"dropping table {relation}")
            drops[f"drop_table_{relation.split('.', 1)[1]}"] = f"drop table {relation}"
        query(drops, env)
    await_capacity(env)
    code = RUNNER(
        build_fabric.dbt_command(
            "run-operation", "starter.create_verification_tables", "--target", "fabric"
        ),
        root,
        env,
    )
    if code != 0:
        raise VerifyError(f"creating the verification tables failed (exit {code})")
    _say("the verification tables exist")
    return 0


# ---------------------------------------------------------------------------------------------
# land-fixture


def _sink(env: Mapping[str, str]) -> Any:
    return SINK_FACTORY(env["FABRIC_WORKSPACE_ID"].strip(), env["FABRIC_LAKEHOUSE_ID"].strip())


# A source offers the build-4 variant (D-09) with `write_added_column_variant(dest) -> Path`, one
# file of a monthly entity under its published name, and names that entity in
# `ADDED_COLUMN_ENTITY`. Exactly one fixture source must offer it.
VARIANT_WRITER = "write_added_column_variant"
VARIANT_ENTITY = "ADDED_COLUMN_ENTITY"


def _land_variant(label: str, env: Mapping[str, str], root: Path) -> int:
    providers = [
        (name, module) for name, module in fixture_sources() if hasattr(module, VARIANT_WRITER)
    ]
    if len(providers) != 1:
        raise VerifyError(
            f"exactly one fixture source must offer {VARIANT_WRITER}; found {len(providers)}"
        )
    source, module = providers[0]
    folder = label_dir(label) / "variant" / source
    path = getattr(module, VARIANT_WRITER)(folder)
    spec = module.ENTITIES[getattr(module, VARIANT_ENTITY)]
    month = spec.file_name_pattern.match(path.name)
    if month is None:
        raise VerifyError(f"the variant file of {source} does not follow {spec.table}'s file name")
    count = LOAD_ENTITY(spec, root, months=[month.group(1)], from_dir=folder, sink=_sink(env))
    _say(f"landed {count} rows into {LANDING_SCHEMA}.{spec.table} (the added-column variant)")
    return 0


def cmd_land_fixture(args: argparse.Namespace, env: dict[str, str], root: Path) -> int:
    if args.variant:
        return _land_variant(args.label, env, root)
    sink = _sink(env)
    for source, module in fixture_sources():
        folder = label_dir(args.label) / "fixture" / source
        written = module.write_fixture(folder)
        for entity in sorted(module.ENTITIES):
            if entity not in written:
                continue
            spec = module.ENTITIES[entity]
            count = LOAD_ENTITY(spec, root, from_dir=folder, sink=sink)
            _say(f"landed {count} rows into {LANDING_SCHEMA}.{spec.table}")
    return 0


# ---------------------------------------------------------------------------------------------
# start


def portal_settings(build: int) -> list[str]:
    """The exact portal steps for build N: what to check, and the switch's position."""
    full_refresh = "ON" if build == FULL_REFRESH_BUILD else "OFF"
    lines = [
        f"start build {build} in the Fabric portal (no REST start is proven for the native job):",
        "  1. open the workspace, then the dbt job",
        (
            f"  2. check: operation build, selector {NATIVE_SELECTOR}, threads 1, "
            f"Seed data ticked, Full refresh {full_refresh}"
        ),
        "  3. select Run, then come back here",
        f"  4. {VERIFY_FABRIC_COMMAND} wait --build {build} --label <label>",
    ]
    if build == FULL_REFRESH_BUILD:
        lines.append(
            "  after this run finishes, switch Full refresh back OFF before build "
            f"{FULL_REFRESH_BUILD + 1}"
        )
    if build == ADDED_COLUMN_BUILD:
        lines.insert(
            1,
            f"  before you start: {VERIFY_FABRIC_COMMAND} land-fixture --variant --label <label>",
        )
    return lines


def cmd_start(args: argparse.Namespace, env: dict[str, str], root: Path) -> int:
    build = args.build
    started_at = NOW()
    if build == FULL_REFRESH_BUILD or fabric_rest.DBT_JOB_TYPE is None:
        record_start(args.label, build, started_at, "manual")
        for line in portal_settings(build):
            _say(line)
        _say(f"recorded build {build} start at {_iso(started_at)} (mode manual)")
        return 0
    location = fabric_rest.run_item_job(
        env["FABRIC_WORKSPACE_ID"].strip(),
        env["FABRIC_DBT_JOB_ID"].strip(),
        fabric_rest.DBT_JOB_TYPE,
    )
    del location  # it carries the workspace and item ids, so it is never written (T-05-31)
    record_start(args.label, build, started_at, "rest")
    _say(f"started build {build} over REST at {_iso(started_at)}")
    return 0


# ---------------------------------------------------------------------------------------------
# wait


def _failure_code(instance: Mapping[str, Any]) -> str | None:
    reason = instance.get("failureReason")
    code = reason.get("errorCode") if isinstance(reason, dict) else None
    if code is None:
        return None
    return code if isinstance(code, str) and ERROR_CODE_RE.match(code) else "unknown"


def find_instance(instances: list[dict[str, Any]], started_at: datetime) -> dict[str, Any] | None:
    """Return the newest instance that started at or after started_at minus WINDOW_SECONDS."""
    earliest = started_at - timedelta(seconds=WINDOW_SECONDS)
    candidates = [
        (moment, instance)
        for instance in instances
        if (moment := parse_time(instance.get("startTimeUtc"))) is not None and moment >= earliest
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda pair: pair[0])[1]


def result_queries(start: datetime, end: datetime) -> dict[str, str]:
    """The invocation rows inside the run's window and the per-node rows of those invocations."""
    low = _sql_time(start - timedelta(seconds=WINDOW_SECONDS))
    high = _sql_time(end + timedelta(seconds=WINDOW_SECONDS))
    window = (
        f"recorded_at >= cast('{low}' as timestamp) and recorded_at <= cast('{high}' as timestamp)"
    )
    return {
        "invocations": (
            "select invocation_id, dbt_version, engine_version, session_time_zone, command, "
            "cast(full_refresh as string), cast(recorded_at as string) "
            f"from verification.invocations where {window}"
        ),
        "run_results": (
            "select invocation_id, unique_id, resource_type, status, cast(failures as string), "
            "cast(execution_time as string) from verification.run_results "
            f"where invocation_id in (select invocation_id from verification.invocations "
            f"where {window})"
        ),
    }


def hook_evidence(
    rows: Mapping[str, list[list[str | None]]],
) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    """Return (invocation, nodes) from the one invocation row, None when there is no row."""
    invocations = rows["invocations"]
    if not invocations:
        return None
    if len(invocations) > 1:
        raise VerifyError(
            f"{len(invocations)} verification.invocations rows fall in the run's window, so the "
            "run's results are ambiguous; rerun the build alone"
        )
    row = invocations[0]
    invocation_id = row[0]
    invocation = {
        "dbt_version": row[1],
        "engine_version": row[2],
        "session_time_zone": row[3],
        "command": row[4],
        "full_refresh": _as_bool(row[5]),
    }
    nodes = sorted(
        (
            {
                "unique_id": result[1],
                "resource_type": result[2],
                "status": result[3],
                "failures": _as_int(result[4]),
                "execution_time": _as_float(result[5]),
            }
            for result in rows["run_results"]
            if result[0] == invocation_id
        ),
        key=lambda node: str(node["unique_id"]),
    )
    return invocation, nodes


def no_hook_row(label: str, build: int, root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The run wrote no invocation row: read its downloaded log, or stop with no evidence (D-10).

    The log folder is the author's Download Logs of that run. With no folder, or a log that does
    not describe the run, nothing is written and the build stays missing.
    """
    folder = log_dir(label, build)
    if not folder.is_dir() or not any(path.is_file() for path in folder.rglob("*")):
        raise VerifyError(
            f"build {build} wrote no hook row; download its logs in the portal (the dbt job, this "
            f"run, Download Logs) into {log_dir_display(label, build)} and rerun wait --build "
            f"{build}"
        )
    try:
        invocation, _counts, nodes = parse_run_log(folder, MANIFEST_NODES(label, root))
    except LogParseError as error:
        raise VerifyError(f"build {build}'s log does not parse: {error}") from None
    return invocation, nodes


def log_dir(label: str, build: int) -> Path:
    return label_dir(label) / f"build-{build}-log"


def log_dir_display(label: str, build: int) -> str:
    return f"target/verify-fabric/{label}/build-{build}-log/"


def cmd_wait(args: argparse.Namespace, env: dict[str, str], root: Path) -> int:
    build = args.build
    stale = build_path(args.label, build)
    if stale.exists():
        stale.unlink()  # a failed wait never leaves an earlier attempt's evidence behind
    entry = read_state(args.label)["builds"].get(str(build))
    started_at = parse_time(entry.get("started_at")) if isinstance(entry, dict) else None
    mode = entry.get("mode") if isinstance(entry, dict) else None
    if started_at is None or mode not in START_MODES:
        raise VerifyError(f"build {build} has no recorded start; run start --build {build}", 2)

    workspace = env["FABRIC_WORKSPACE_ID"].strip()
    job = env["FABRIC_DBT_JOB_ID"].strip()
    polls = max(1, int(args.timeout_minutes * 60 // POLL_SECONDS))
    instance: dict[str, Any] | None = None
    for poll in range(polls):
        found = find_instance(fabric_rest.list_job_instances(workspace, job), started_at)
        if found is not None and found.get("status") in TERMINAL_STATUSES:
            instance = found
            break
        if poll + 1 < polls:
            SLEEP(POLL_SECONDS)
    if instance is None:
        what = "has not started" if found is None else f"is {found.get('status')}"
        _say(
            f"build {build} {what} after {args.timeout_minutes} minutes; rerun wait --build {build}"
        )
        return EXIT_STILL_RUNNING

    job_status = str(instance.get("status"))
    failure_code = _failure_code(instance)
    run_start = parse_time(instance.get("startTimeUtc"))
    run_end = parse_time(instance.get("endTimeUtc")) or (run_start + timedelta(days=1))
    _say(f"build {build}'s run ended {job_status}; reading its results")
    evidence = hook_evidence(query(result_queries(run_start, run_end), env))
    source = "hook"
    if evidence is None:
        evidence = no_hook_row(args.label, build, root)
        source = "log"
    invocation, nodes = evidence
    expected = build == FULL_REFRESH_BUILD
    path = write_build(
        args.label,
        {
            "build": build,
            "mode": mode,
            "full_refresh_expected": expected,
            "started_at": _iso(started_at),
            "job_status": job_status,
            "failure_code": failure_code,
            "results_source": source,
            "invocation": invocation,
            "status_counts": status_counts(nodes),
            "nodes": nodes,
        },
    )
    failing = sum(1 for node in nodes if node["status"] not in NODE_OK_STATUSES)
    _say(
        f"wrote {path.name} from the {source}: {len(nodes)} nodes, {failing} not in "
        f"{'/'.join(NODE_OK_STATUSES)}"
    )
    code = 0
    if invocation["full_refresh"] is not expected:
        _say(
            f"build {build} ran with full_refresh {json.dumps(invocation['full_refresh'])}, "
            f"expected {json.dumps(expected)}: check the job's Full refresh switch and rerun "
            f"build {build}",
            error=True,
        )
        code = 1
    if job_status != "Completed":
        _say(f"build {build}'s job instance ended {job_status} ({failure_code})", error=True)
        code = 1
    return code


# ---------------------------------------------------------------------------------------------
# The D-10 log fallback: a run that wrote no hook row is read from its own downloaded log.

# dbt's result words and the run_results status each stands for.
LOG_STATUS: dict[str, str] = {
    "OK": "success",
    "PASS": "pass",
    "WARN": "warn",
    "FAIL": "fail",
    "ERROR": "error",
    "SKIP": "skipped",
}
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
RESULT_RE = re.compile(r"(?<!\S)(\d+) of (\d+) (OK|PASS|WARN|FAIL|ERROR|SKIP)(?=\s|$)(.*)$")
BRACKET_RE = re.compile(r"\[([^\[\]]*)\]\s*$")
LEADER_RE = re.compile(r"\s*\.{2,}\s*$")
FAILURES_RE = re.compile(r"^(\d+)\s+(.*)$")
BRACKET_FAILURES_RE = re.compile(r"^(?:WARN|FAIL)\s+(\d+)\b")
SECONDS_RE = re.compile(r"\bin (\d+(?:\.\d+)?)s\b")
VERSION_RE = re.compile(r"Running with dbt=(\S+)")
ARGUMENTS_RE = re.compile(r"running dbt with arguments", re.IGNORECASE)
WHICH_RE = re.compile(r"""['"]which['"]\s*:\s*['"]([a-z_-]+)['"]""")
FULL_REFRESH_RE = re.compile(r"""['"]full_refresh['"]\s*:\s*['"]?(True|False|true|false)\b""")
DONE_RE = re.compile(
    r"Done\. PASS=(\d+) WARN=(\d+) ERROR=(\d+) SKIP=(\d+)(?: NO-OP=(\d+))? TOTAL=(\d+)"
)
HOOK_PREFIX = "hook:"


class LogParseError(Exception):
    """A run log does not describe the run. The message names a line number or a count only."""


def manifest_log_names(manifest: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """Return {unique_id: {resource_type, log_name}}: the name dbt prints for each node.

    Models, seeds and snapshots print `schema.alias`, data tests their name, unit tests
    `model::name`. Other nodes (operations, analyses) print no result line.
    """
    names: dict[str, dict[str, str]] = {}
    for unique_id, node in (manifest.get("nodes") or {}).items():
        kind = node.get("resource_type")
        if kind in ("model", "seed", "snapshot"):
            name = f"{node.get('schema')}.{node.get('alias') or node.get('name')}"
        elif kind == "test":
            name = str(node.get("name"))
        else:
            continue
        names[unique_id] = {"resource_type": str(kind), "log_name": name}
    for unique_id, node in (manifest.get("unit_tests") or {}).items():
        names[unique_id] = {
            "resource_type": "unit_test",
            "log_name": f"{node.get('model')}::{node.get('name')}",
        }
    return names


def _line_text(line: str) -> str:
    """The message of one log line: a JSON event's info.msg (dbt log_version 3), else the line."""
    stripped = line.strip()
    if stripped.startswith("{"):
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            return ANSI_RE.sub("", line)
        info = event.get("info") if isinstance(event, dict) else None
        if isinstance(info, dict):
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            if info.get("name") == "MainReportArgs" and isinstance(data.get("args"), dict):
                return "running dbt with arguments " + json.dumps(data["args"])
            return ANSI_RE.sub("", str(info.get("msg") or ""))
    return ANSI_RE.sub("", line)


def _parse_one_log(
    path: Path, index: Mapping[str, list[str]], kinds: Mapping[str, str]
) -> tuple[dict[str, Any], dict[str, dict[str, int]], list[dict[str, Any]]] | None:
    """Parse one log file; None when it holds no Done line."""
    nodes: dict[str, dict[str, Any]] = {}
    hooks: list[str] = []
    version: str | None = None
    command: str | None = None
    full_refresh: bool | None = None
    done: re.Match[str] | None = None
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for number, raw in enumerate(lines, start=1):
        text = _line_text(raw)
        if found := VERSION_RE.search(text):
            version = found.group(1)
        if ARGUMENTS_RE.search(text):
            which = WHICH_RE.search(text)
            refresh = FULL_REFRESH_RE.search(text)
            command = which.group(1) if which else None
            full_refresh = refresh.group(1).lower() == "true" if refresh else None
        if found := DONE_RE.search(text):
            done = found
            continue
        result = RESULT_RE.search(text)
        if not result:
            continue
        status = LOG_STATUS[result.group(3)]
        rest = result.group(4)
        bracket = BRACKET_RE.search(rest)
        inner = bracket.group(1).strip() if bracket else ""
        body = LEADER_RE.sub("", rest[: bracket.start()] if bracket else rest).strip()
        if body.startswith(HOOK_PREFIX):
            hooks.append(status)
            continue
        failures: int | None = None
        if status in ("warn", "fail"):
            if leading := FAILURES_RE.match(body):
                failures, body = int(leading.group(1)), leading.group(2)
            elif from_bracket := BRACKET_FAILURES_RE.match(inner):
                failures = int(from_bracket.group(1))
        seconds = SECONDS_RE.search(inner)
        token = body.split()[-1].strip("`\"'") if body.split() else ""
        candidates = [token]
        if token.count(".") >= 2:
            candidates.append(".".join(token.split(".")[-2:]))
        matches: list[str] = []
        for candidate in candidates:
            matches = index.get(candidate, [])
            if matches:
                break
        if len(matches) != 1:
            raise LogParseError(
                f"log line {number}: the node name matches {len(matches)} manifest nodes, "
                "not exactly one"
            )
        unique_id = matches[0]
        if unique_id in nodes:
            raise LogParseError(f"log line {number} repeats a node")
        nodes[unique_id] = {
            "unique_id": unique_id,
            "resource_type": kinds[unique_id],
            "status": status,
            "failures": failures,
            "execution_time": float(seconds.group(1)) if seconds else 0.0,
        }
    if done is None:
        return None
    ordered = sorted(nodes.values(), key=lambda node: str(node["unique_id"]))
    statuses = [node["status"] for node in ordered] + hooks
    computed = {
        "PASS": sum(1 for status in statuses if status in ("success", "pass")),
        "WARN": statuses.count("warn"),
        "ERROR": sum(1 for status in statuses if status in ("fail", "error")),
        "SKIP": statuses.count("skipped"),
        "NO-OP": 0,
        "TOTAL": len(statuses),
    }
    stated = dict(
        zip(("PASS", "WARN", "ERROR", "SKIP", "NO-OP", "TOTAL"), done.groups(), strict=True)
    )
    for field, value in stated.items():
        if value is not None and int(value) != computed[field]:
            raise LogParseError(
                f"dbt's Done line says {field}={value} but the result lines give {computed[field]}"
            )
    invocation = {
        "dbt_version": version,
        "engine_version": None,
        "session_time_zone": None,
        "command": command,
        "full_refresh": full_refresh,
    }
    return invocation, status_counts(ordered), ordered


def parse_run_log(
    log_dir: Path, manifest_nodes: Mapping[str, Mapping[str, str]]
) -> tuple[dict[str, Any], dict[str, dict[str, int]], list[dict[str, Any]]]:
    """Return (invocation, status_counts, nodes) from a run's downloaded log (D-10).

    Every file under log_dir that holds dbt's `Done. PASS=` line is read and must give the same
    parse. Each result line `N of M <OK|PASS|WARN|FAIL|ERROR|SKIP> ... <name>` resolves to exactly
    one manifest node, hook lines count against the Done line but are not nodes, and the counts
    must equal the Done line. engine_version and session_time_zone are never in a log.
    """
    index: dict[str, list[str]] = {}
    kinds: dict[str, str] = {}
    for unique_id, node in manifest_nodes.items():
        index.setdefault(str(node["log_name"]), []).append(unique_id)
        kinds[unique_id] = str(node["resource_type"])
    parses = [
        parsed
        for path in sorted(log_dir.rglob("*"))
        if path.is_file() and (parsed := _parse_one_log(path, index, kinds)) is not None
    ]
    if not parses:
        raise LogParseError("no log file holds dbt's Done line")
    if any(parsed != parses[0] for parsed in parses[1:]):
        raise LogParseError(f"the {len(parses)} log files with a Done line disagree")
    return parses[0]


def _manifest_nodes(label: str, root: Path) -> dict[str, dict[str, str]]:
    """Parse the checkout offline for the Fabric target and return manifest_log_names of it.

    The gate's dummy FABRIC_* values stand in for the real ones, so no connection is made.
    """
    from starter_cli.gate import DUMMY_FABRIC_ENV

    target = label_dir(label) / "manifest"
    env = {key: value for key, value in os.environ.items() if not key.startswith("FABRIC_")}
    env.update(DUMMY_FABRIC_ENV)
    code = RUNNER(
        build_fabric.dbt_command(
            "--quiet", "parse", "--target", "fabric", "--target-path", str(target)
        ),
        root,
        env,
    )
    manifest_file = target / "manifest.json"
    if code != 0 or not manifest_file.exists():
        raise VerifyError(
            f"the offline dbt parse for the log's node names failed (exit {code}); run dbt deps"
        )
    return manifest_log_names(json.loads(manifest_file.read_text(encoding="utf-8")))


MANIFEST_NODES: Callable[[str, Path], Mapping[str, Mapping[str, str]]] = _manifest_nodes


# ---------------------------------------------------------------------------------------------
# self-hosted: the same fixture build from this machine through build-fabric's commands (D-08)

SELF_HOSTED_MODE = "self-hosted"
SELF_HOSTED_KEYS: tuple[str, ...] = (
    "mode",
    "selector",
    "exit_code",
    "invocation",
    "status_counts",
    "nodes",
)
INVOCATION_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def run_results_path(root: Path) -> Path:
    """Where `dbt build` from the repo root writes its run results (the default target path)."""
    return root / "target" / "run_results.json"


def run_results_nodes(path: Path) -> tuple[str | None, list[dict[str, Any]]]:
    """Return (invocation_id, nodes) from a run_results.json, nodes in build-N.json's shape.

    A missing file (dbt stopped before it wrote one) gives (None, []).

    The project's hooks are left out: dbt 1.11 lists on-run-start and on-run-end as `operation.*`
    results, while the native evidence holds none (neither the on-run-end hook's results nor the
    log fallback through manifest_log_names), so self-hosted and native count the same nodes. A
    failed hook still shows in dbt's exit code, which the caller keeps as it is.
    """
    if not path.exists():
        return None, []
    data = json.loads(path.read_text(encoding="utf-8"))
    metadata = data.get("metadata") if isinstance(data, dict) else None
    invocation_id = metadata.get("invocation_id") if isinstance(metadata, dict) else None
    nodes = sorted(
        (
            {
                "unique_id": str(result["unique_id"]),
                "resource_type": str(result["unique_id"]).split(".", 1)[0],
                "status": str(result.get("status")),
                "failures": _as_int(result.get("failures")),
                "execution_time": _as_float(result.get("execution_time")),
            }
            for result in (data.get("results") or [])
            if isinstance(result, dict)
            and result.get("unique_id")
            and not str(result["unique_id"]).startswith("operation.")
        ),
        key=lambda node: node["unique_id"],
    )
    return invocation_id if isinstance(invocation_id, str) else None, nodes


def invocation_query(invocation_id: str) -> dict[str, str]:
    """The newest verification.invocations row of this invocation (the hook's facts)."""
    if not INVOCATION_ID_RE.match(invocation_id):
        raise VerifyError("run_results.json carries an invocation id that is not a dbt UUID")
    return {
        "invocation": (
            "select dbt_version, engine_version, session_time_zone, command, "
            "cast(full_refresh as string) from verification.invocations "
            f"where invocation_id = '{invocation_id}' order by recorded_at desc limit 1"
        )
    }


def write_self_hosted(label: str, record: dict[str, Any]) -> Path:
    """Write self-hosted.json with exactly SELF_HOSTED_KEYS; refuse any other shape."""
    if tuple(record) != SELF_HOSTED_KEYS:
        raise ValueError("a self-hosted record must carry exactly the pinned keys")
    if tuple(record["invocation"]) != INVOCATION_KEYS:
        raise ValueError("an invocation must carry exactly the pinned keys")
    if any(tuple(node) != NODE_KEYS for node in record["nodes"]):
        raise ValueError("a node must carry exactly the pinned keys")
    path = label_dir(label) / "self-hosted.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path


# A seam, so a test never touches the checkout's own target/run_results.json.
RUN_RESULTS: Callable[[Path], Path] = run_results_path


def cmd_self_hosted(args: argparse.Namespace, env: dict[str, str], root: Path) -> int:
    results = RUN_RESULTS(root)
    if results.exists():
        results.unlink()  # never read an earlier run's results as this build's
    code = RUNNER(build_fabric.dbt_command("deps"), root, env)
    if code != 0:
        raise VerifyError(f"dbt deps failed (exit {code})", code)
    await_capacity(env)
    _say(f"building the fixture on the Lakehouse with the {NATIVE_SELECTOR} selector")
    exit_code = RUNNER(
        [
            *build_fabric.dbt_command("build", "--target", "fabric"),
            "--selector",
            NATIVE_SELECTOR,
        ],
        root,
        env,
    )
    invocation_id, nodes = run_results_nodes(results)
    invocation: dict[str, Any] = dict.fromkeys(INVOCATION_KEYS)
    code = exit_code
    if invocation_id is None:
        _say("the build wrote no run_results.json, so it has no hook row to read", error=True)
        code = code or 1
    else:
        try:
            rows = query(invocation_query(invocation_id), env)["invocation"]
        except fabric_query.FabricQueryError as error:
            _say(f"reading the build's hook row failed: {error}", error=True)
            rows = []
            code = code or 1
        if rows:
            row = rows[0]
            invocation = {
                "dbt_version": row[0],
                "engine_version": row[1],
                "session_time_zone": row[2],
                "command": row[3],
                "full_refresh": _as_bool(row[4]),
            }
        else:
            _say("the build wrote no verification.invocations row", error=True)
            code = code or 1
    path = write_self_hosted(
        args.label,
        {
            "mode": SELF_HOSTED_MODE,
            "selector": NATIVE_SELECTOR,
            "exit_code": exit_code,
            "invocation": invocation,
            "status_counts": status_counts(nodes),
            "nodes": nodes,
        },
    )
    failing = sum(1 for node in nodes if node["status"] not in NODE_OK_STATUSES)
    _say(
        f"wrote {path.name}: dbt exit {exit_code}, {len(nodes)} nodes, {failing} not in "
        f"{'/'.join(NODE_OK_STATUSES)}, session zone {invocation['session_time_zone']}"
    )
    return code


# ---------------------------------------------------------------------------------------------
# parity: the same fixture's report relations counted on DuckDB and on the Lakehouse (PORT-04)

PARITY_KEYS: tuple[str, ...] = ("relations", "all_equal", "schema_tables")
PARITY_RELATION_KEYS: tuple[str, ...] = ("relation", "duckdb", "fabric", "equal")
PARITY_SCHEMAS: tuple[str, ...] = ("bronze", "silver", "gold", "dbo")
RELATION_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")


def report_relations() -> list[str]:
    """Every source's REPORT_RELATIONS, as lowercase `schema.table` names, sorted and unique."""
    relations: set[str] = set()
    for _, module in sorted(discover_sources().items()):
        for _label, relation in getattr(module, "REPORT_RELATIONS", []):
            name = str(relation).strip().lower()
            if not RELATION_RE.match(name):
                raise VerifyError(f"report relation {name} is not a schema.table identifier")
            relations.add(name)
    return sorted(relations)


def _query_name(prefix: str, relation: str) -> str:
    return f"{prefix}_{relation.replace('.', '__')}"


def duckdb_counts(database: Path, relations: list[str]) -> dict[str, int | None]:
    """Count each relation on the DuckDB file; a relation that is not there counts None."""
    import duckdb

    counts: dict[str, int | None] = {}
    con = duckdb.connect(str(database), read_only=True)
    try:
        for relation in relations:
            schema, table = relation.split(".", 1)
            present = con.execute(
                "select count(*) from information_schema.tables "
                "where lower(table_schema) = ? and lower(table_name) = ?",
                [schema, table],
            ).fetchone()
            if not present or not present[0]:
                counts[relation] = None
                continue
            row = con.execute(f"select cast(count(*) as bigint) from {relation}").fetchone()
            counts[relation] = _as_int(row[0]) if row else None
    finally:
        con.close()
    return counts


def parity_queries(relations: list[str]) -> dict[str, str]:
    """One bridge call: every relation's count and the tables of each PARITY_SCHEMAS schema."""
    queries = {
        _query_name("count", relation): f"select cast(count(*) as bigint) from {relation}"
        for relation in relations
    }
    for schema in PARITY_SCHEMAS:
        queries[f"tables_{schema}"] = f"show tables in {schema}"
    return queries


def _table_count(rows: list[list[str | None]]) -> int:
    """Rows of `show tables` that are tables of the schema (a session temp view is not)."""
    return sum(1 for row in rows if not (len(row) > 2 and str(row[2]).strip().lower() == "true"))


def parity_record(
    relations: list[str],
    duckdb: Mapping[str, int | None],
    fabric_rows: Mapping[str, list[list[str | None]]],
) -> dict[str, Any]:
    """Build parity.json: counts as integers, a relation missing on either side is unequal."""
    entries = []
    for relation in relations:
        rows = fabric_rows.get(_query_name("count", relation)) or []
        fabric = _as_int(rows[0][0]) if rows and rows[0] else None
        local = _as_int(duckdb.get(relation))
        entries.append(
            {
                "relation": relation.lower(),
                "duckdb": local,
                "fabric": fabric,
                "equal": local is not None and fabric is not None and local == fabric,
            }
        )
    entries.sort(key=lambda entry: entry["relation"])
    return {
        "relations": entries,
        "all_equal": all(entry["equal"] for entry in entries),
        "schema_tables": {
            schema: _table_count(fabric_rows.get(f"tables_{schema}") or [])
            for schema in PARITY_SCHEMAS
        },
    }


def write_parity(label: str, record: dict[str, Any]) -> Path:
    """Write parity.json with exactly PARITY_KEYS; refuse any other shape."""
    if tuple(record) != PARITY_KEYS:
        raise ValueError("a parity record must carry exactly the pinned keys")
    if any(tuple(entry) != PARITY_RELATION_KEYS for entry in record["relations"]):
        raise ValueError("a parity relation must carry exactly the pinned keys")
    if tuple(record["schema_tables"]) != PARITY_SCHEMAS:
        raise ValueError("schema_tables must carry exactly the pinned schemas")
    path = label_dir(label) / "parity.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path


def cmd_parity(args: argparse.Namespace, env: dict[str, str], root: Path) -> int:
    relations = report_relations()
    _say(f"building the fixture on DuckDB into target/verify-fabric/{args.label}/duckdb/")
    try:
        build = BUILD_FIXTURE(root, label_dir(args.label) / "duckdb")
    except (RuntimeError, OSError) as error:
        raise VerifyError(f"the DuckDB fixture build failed: {error}") from None
    local = duckdb_counts(build.database, relations)
    _say(f"counting {len(relations)} relations on the Lakehouse in one query call")
    record = parity_record(relations, local, query(parity_queries(relations), env))
    path = write_parity(args.label, record)
    unequal = [entry["relation"] for entry in record["relations"] if not entry["equal"]]
    _say(
        f"wrote {path.name}: {len(relations) - len(unequal)} of {len(relations)} relations equal"
        + (f"; unequal: {', '.join(unequal)}" if unequal else "")
    )
    return 0 if record["all_equal"] else 1


# ---------------------------------------------------------------------------------------------
# sequence: the whole PORT-04 sequence in the author's terminal (`make verify-fabric`)


def _wait_until_done(label: str, build: int, env: dict[str, str], root: Path) -> int:
    """Wait for build N with no overall cap: rerun wait while it reports the run still going."""
    while True:
        code = cmd_wait(
            argparse.Namespace(label=label, build=build, timeout_minutes=DEFAULT_TIMEOUT_MINUTES),
            env,
            root,
        )
        if code != EXIT_STILL_RUNNING:
            return code


def _native_build(label: str, build: int, env: dict[str, str], root: Path) -> int:
    code = cmd_start(argparse.Namespace(label=label, build=build), env, root)
    if code != 0:
        return code
    entry = read_state(label)["builds"].get(str(build)) or {}
    if entry.get("mode") == "manual":
        PROMPT(f"verify-fabric: start build {build} in the portal as above, then press Enter ")
    return _wait_until_done(label, build, env, root)


def sequence_steps(
    args: argparse.Namespace,
) -> list[tuple[str, Callable[[dict[str, str], Path], int]]]:
    """The PORT-04 order: init, land, builds 1 to 3, the variant, build 4, self-hosted, parity."""
    label = args.label

    def namespace(**extra: Any) -> argparse.Namespace:
        return argparse.Namespace(label=label, **extra)

    steps: list[tuple[str, Callable[[dict[str, str], Path], int]]] = [
        (
            "init --fresh" if args.fresh else "init",
            lambda env, root: cmd_init(namespace(fresh=args.fresh), env, root),
        ),
        ("land-fixture", lambda env, root: cmd_land_fixture(namespace(variant=False), env, root)),
    ]
    for build in BUILDS:
        if build == ADDED_COLUMN_BUILD:
            steps.append(
                (
                    "land-fixture --variant",
                    lambda env, root: cmd_land_fixture(namespace(variant=True), env, root),
                )
            )
        steps.append(
            (
                f"build {build}",
                lambda env, root, build=build: _native_build(label, build, env, root),
            )
        )
    steps.append(("self-hosted", lambda env, root: cmd_self_hosted(namespace(), env, root)))
    steps.append(("parity", lambda env, root: cmd_parity(namespace(), env, root)))
    return steps


def cmd_sequence(args: argparse.Namespace, env: dict[str, str], root: Path) -> int:
    for name, step in sequence_steps(args):
        _say(f"step: {name}")
        code = step(env, root)
        if code != 0:
            _say(f"the sequence stopped at {name} (exit {code})", error=True)
            return code
    _say(f"the sequence finished; evidence in target/verify-fabric/{args.label}/")
    return 0


# ---------------------------------------------------------------------------------------------
# main


def _label(value: str) -> str:
    if not LABEL_RE.match(value):
        raise argparse.ArgumentTypeError("a label is lower-case letters, digits and -")
    return value


def _build(value: str) -> int:
    if value not in {str(build) for build in BUILDS}:
        raise argparse.ArgumentTypeError("a build is 1, 2, 3 or 4")
    return int(value)


def _minutes(value: str) -> int:
    if not value.isdigit() or not 1 <= int(value) <= 60:
        raise argparse.ArgumentTypeError("minutes is a whole number from 1 to 60")
    return int(value)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--label",
        type=_label,
        default=DEFAULT_LABEL,
        help=f"names the evidence folder target/verify-fabric/<label>/ (default {DEFAULT_LABEL})",
    )
    parser = argparse.ArgumentParser(
        prog="verify-fabric",
        description=(
            "Drive and observe the native dbt job's PORT-04 builds on a Fabric Lakehouse and "
            "write their evidence. Requires `az login`, the fabric extra and "
            + ", ".join(build_fabric.REQUIRED_ENV)
            + "; start, wait and sequence also need "
            + ", ".join(REQUIRED_JOB_ENV)
            + "."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser(
        "init", parents=[common], help="create the verification tables the results hook fills"
    )
    init.add_argument(
        "--fresh",
        action="store_true",
        help="first drop the layer and verification schemas and the two fixture landing tables",
    )
    land = commands.add_parser(
        "land-fixture", parents=[common], help="land the synthetic fixture through the sink"
    )
    land.add_argument(
        "--variant",
        action="store_true",
        help="re-land only the example source's variant file: the same rows plus one column "
        "nobody declared, for build 4",
    )
    start = commands.add_parser("start", parents=[common], help="start native build N")
    start.add_argument("--build", type=_build, required=True, help="the build, 1 to 4")
    wait = commands.add_parser(
        "wait", parents=[common], help="wait for native build N and write build-N.json"
    )
    wait.add_argument("--build", type=_build, required=True, help="the build, 1 to 4")
    wait.add_argument(
        "--timeout-minutes",
        type=_minutes,
        default=DEFAULT_TIMEOUT_MINUTES,
        help=f"stop waiting after this many minutes, exit {EXIT_STILL_RUNNING} "
        f"(default {DEFAULT_TIMEOUT_MINUTES})",
    )
    commands.add_parser(
        "self-hosted",
        parents=[common],
        help=f"build the fixture on the Lakehouse from here (--selector {NATIVE_SELECTOR}) and "
        "write self-hosted.json",
    )
    commands.add_parser(
        "parity",
        parents=[common],
        help="count the report relations on a DuckDB fixture build and on the Lakehouse and "
        "write parity.json",
    )
    sequence = commands.add_parser(
        "sequence",
        parents=[common],
        help="init, land-fixture, builds 1 to 4 with the variant, self-hosted and parity, "
        "stopping at the first failure",
    )
    sequence.add_argument("--fresh", action="store_true", help="run init with --fresh")
    return parser


COMMANDS: dict[str, Callable[[argparse.Namespace, dict[str, str], Path], int]] = {
    "init": cmd_init,
    "land-fixture": cmd_land_fixture,
    "start": cmd_start,
    "wait": cmd_wait,
    "self-hosted": cmd_self_hosted,
    "parity": cmd_parity,
    "sequence": cmd_sequence,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not extra_installed():
        print(EXTRA_HINT, file=sys.stderr, flush=True)
        return 2
    env = {**os.environ, **build_fabric.normalised_env(os.environ)}
    problems = environment_problems(env, needs_job=args.command in ("start", "wait", "sequence"))
    for problem in problems:
        _say(problem, error=True)
    if problems:
        return 2
    root = repo_root()
    try:
        return COMMANDS[args.command](args, env, root)
    except VerifyError as error:
        _say(str(error), error=True)
        return error.code
    except fabric_rest.FabricAuthError as error:
        _say(str(error), error=True)
        return 2
    except (
        fabric_rest.FabricApiError,
        fabric_query.FabricQueryError,
        DownloadError,
        LandingError,
        ValueError,
    ) as error:
        _say(str(error), error=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())

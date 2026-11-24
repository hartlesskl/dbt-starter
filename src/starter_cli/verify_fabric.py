"""`uv run --locked --extra fabric verify-fabric`: drive and observe the native PORT-04 builds.

The verification command turns each native build into evidence without a hand-typed result
(D-08 to D-10, D-12). Its subcommands, each with `--label NAME` (evidence in
target/verify-fabric/<label>/):

    init [--fresh]          create the verification tables the results hook writes to; with
                            --fresh, first drop the layer schemas, the verification schema and the
                            two fixture landing tables, so the sequence starts from nothing
    land-fixture [--variant]
                            land the synthetic fixture into the Lakehouse through the sink; with
                            --variant, re-land only the 2026-09 file with one added column (D-09)
    start --build N         start native build N (1 to 4) over REST when a REST start is proven,
                            otherwise print the portal settings; record when it started
    wait --build N          find the run in the job's instance list, read its results from the
                            verification tables and write build-N.json

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

from starter_cli import build_fabric, fabric_query, fabric_rest, fabric_sink, nyc_taxi
from starter_cli.landing import DownloadError, LandingError, load_entity
from starter_cli.nyc_taxi.fixture import write_added_column_variant, write_fixture
from starter_cli.project import repo_root

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
# Derived from the entity specs, never typed, so it is the names the loader writes.
FIXTURE_LANDING_TABLES: tuple[str, ...] = tuple(
    sorted(f"{LANDING_SCHEMA}.{spec.table}" for spec in nyc_taxi.ENTITIES.values())
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


# The month the build-4 variant re-lands (the variant file is FILE_2026_09 plus one column).
VARIANT_MONTH = "2026-09"


def cmd_land_fixture(args: argparse.Namespace, env: dict[str, str], root: Path) -> int:
    if args.variant:
        folder = label_dir(args.label) / "variant"
        write_added_column_variant(folder)
        spec = nyc_taxi.ENTITIES["yellow_trips"]
        count = LOAD_ENTITY(spec, root, months=[VARIANT_MONTH], from_dir=folder, sink=_sink(env))
        _say(f"landed {count} rows into {LANDING_SCHEMA}.{spec.table} (the added-column variant)")
        return 0
    folder = label_dir(args.label) / "fixture"
    write_fixture(folder)
    sink = _sink(env)
    for entity in ("taxi_zones", "yellow_trips"):
        spec = nyc_taxi.ENTITIES[entity]
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


def no_hook_row(label: str, build: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The run wrote no invocation row: read its downloaded log, or stop with no evidence."""
    raise VerifyError(
        f"build {build} wrote no hook row; download its logs in the portal (the dbt job, this "
        f"run, Download Logs) into {log_dir_display(label, build)} and rerun wait --build {build}"
    )


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
        evidence = no_hook_row(args.label, build)
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
            + "; start and wait also need "
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
        help="re-land only yellow_tripdata_2026-09.parquet with the same rows plus the column "
        "added_note, for build 4",
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
    return parser


COMMANDS: dict[str, Callable[[argparse.Namespace, dict[str, str], Path], int]] = {
    "init": cmd_init,
    "land-fixture": cmd_land_fixture,
    "start": cmd_start,
    "wait": cmd_wait,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not extra_installed():
        print(EXTRA_HINT, file=sys.stderr, flush=True)
        return 2
    env = {**os.environ, **build_fabric.normalised_env(os.environ)}
    problems = environment_problems(env, needs_job=args.command in ("start", "wait"))
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

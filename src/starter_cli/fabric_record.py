"""The dated Fabric verification record: derived from evidence files, never typed (D-10, D-12, D-13).

`verify-fabric checks` and `verify-fabric record` use this module. It reads the evidence the
producing plans pin (build-N.json, self-hosted.json, parity.json, volume.json, commit.txt in
target/verify-fabric/<label>/; native-first-run.json, sink-spike.json, fallback-sink.json,
vendoring.json, ci.json and known-limit-approvals.json in target/fabric-live/) and no other key. It
makes no Fabric, Livy or REST call. What the project is expected to hold (seeds, gold models, the
bronze model of the added-column variant, the undeclared columns a fixture plants) comes from
`expected_project`, which reads the registered sources and the tracked tree, so a copy of the
template that has removed the example still writes its own record (D-22).

Three things leave this module:

- `derive_checks`: an outcome for each of the 14 live checks, with the file and key that settle it.
- `render_record`: deterministic Markdown holding aggregates only.
- `write_record`: an exclusive create, so a dated record is never overwritten (D-12).

One vocabulary decides what may be published. `NODE_OK_STATUSES` comes from verify_fabric, the
check outcomes are `CHECK_OUTCOMES`, and `publishable` is the only rule for which outcome a record
may show. A known limit is a node excluded from every Fabric run with an upstream issue link
(D-14); it is never a failing node that is waved through.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date as _date
from decimal import Decimal
from pathlib import Path
from typing import Any, NamedTuple

from starter_cli import build_fabric
from starter_cli.project import discover_sources, repo_root
from starter_cli.verify_fabric import BUILDS, NODE_OK_STATUSES, RESULTS_SOURCES

# ---------------------------------------------------------------------------------------------
# Vocabulary

LIVE_CHECKS: tuple[str, ...] = (
    "native-job-deps",
    "schema-layout",
    "session-time-zone",
    "undeclared-columns-live",
    "bronze-plan-cost",
    "conversion-fixture-spark",
    "contracts-live",
    "native-job-profile-schema",
    "seed-load",
    "unit-tests-live",
    "sink-discovery",
    "sql-endpoint-read",
    "oidc-live",
    "job-rest-run",
)
# The checks decided over nodes: each can be `excluded` when a declared known limit covers its
# whole subject. The other eight cannot.
NODE_CHECKS: tuple[str, ...] = (
    "undeclared-columns-live",
    "bronze-plan-cost",
    "conversion-fixture-spark",
    "contracts-live",
    "seed-load",
    "unit-tests-live",
)
CHECK_OUTCOMES: tuple[str, ...] = ("pass", "fail", "missing", "documented-fallback", "excluded")

# One source of the upstream issue rule: build_fabric validates the same link when it lists the
# declared known limits.
ISSUE_URL_RE = build_fabric.ISSUE_URL_RE

# The layers of the record, in the order its tables list them. The quarantine tables live in the
# silver schema and are told apart by their name.
LAYER_ORDER: tuple[str, ...] = ("landing", "bronze", "silver", "quarantine", "gold")
QUARANTINE_SUFFIX = "_quarantine"

# What the project's live checks count comes from `expected_project`, never from a constant here.
GOLD_MODEL_PREFIX = "gold__"
CONVERSION_FIXTURE_TEST = "shim_conversion_fixture"
UNDECLARED_COLUMNS_TEST_RE = re.compile(r"undeclared_(landing_)?columns")
FULL_REFRESH_EXPECTED: tuple[bool, ...] = (False, False, True, False)

SELF_HOSTED = "self-hosted"
RUN_LABELS: tuple[str, ...] = (*(f"build-{build}" for build in BUILDS), SELF_HOSTED)
NATIVE_LABELS: tuple[str, ...] = RUN_LABELS[:-1]

# The Fabric runtime each Spark line ships in. Only Runtime 2.0 is verified (D-11).
RUNTIME_BY_SPARK: dict[str, str] = {"4.1": "2.0"}

PENDING = "pending"
ERROR_CODE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")


class RecordRefused(Exception):
    """The record cannot be written. The message names the item or the field, never a value."""


# ---------------------------------------------------------------------------------------------
# What the project is expected to hold (D-22)


class ProjectExpectations(NamedTuple):
    """The counts and names the node checks hold a run against."""

    seed_count: int
    gold_model_count: int
    bronze_variant_model: str | None
    undeclared_failures: dict[int, int]


def _tracked_files(root: Path) -> list[str]:
    try:
        listing = subprocess.run(
            ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        raise RecordRefused("the tracked files could not be listed with git ls-files") from None
    return [raw.decode() for raw in listing.split(b"\0") if raw]


def expected_project(root: Path) -> ProjectExpectations:
    """Read what a run of this project must hold from the registered sources and the tracked tree.

    Seeds are the tracked `seeds/**/*.csv` and gold models the tracked `models/gold/**/gold__*.sql`.
    The bronze model is the one of the entity a source names in ADDED_COLUMN_ENTITY. The undeclared
    columns a build reports are the sum of what each source's fixture plants
    (`FIXTURE_UNDECLARED_COLUMNS`, default 0); the variant file landed before build 4 adds one more
    when some source names such an entity (D-09).
    """
    from starter_cli.verify_fabric import VARIANT_ENTITY, bronze_trips_model

    tracked = _tracked_files(root)
    seeds = [path for path in tracked if path.startswith("seeds/") and path.endswith(".csv")]
    golds = [
        path
        for path in tracked
        if path.startswith("models/gold/")
        and path.endswith(".sql")
        and path.rsplit("/", 1)[-1].startswith(GOLD_MODEL_PREFIX)
    ]
    modules = [module for _, module in sorted(discover_sources().items())]
    planted = sum(int(getattr(module, "FIXTURE_UNDECLARED_COLUMNS", 0)) for module in modules)
    variant = any(getattr(module, VARIANT_ENTITY, None) for module in modules)
    return ProjectExpectations(
        seed_count=len(seeds),
        gold_model_count=len(golds),
        bronze_variant_model=bronze_trips_model(),
        undeclared_failures={
            **{build: planted for build in BUILDS if build < 4},
            4: planted + (1 if variant else 0),
        },
    )


# ---------------------------------------------------------------------------------------------
# Evidence files


def _read_json(path: Path) -> Any:
    """Return the parsed file, None when it does not exist."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise RecordRefused(f"{path.name} is not valid JSON") from None


def _read_object(path: Path) -> dict[str, Any] | None:
    data = _read_json(path)
    if data is None:
        return None
    if not isinstance(data, dict):
        raise RecordRefused(f"{path.name} is not a JSON object")
    return data


def load_evidence(evidence_dir: Path, live_dir: Path) -> dict[str, Any]:
    """Read every evidence file; a file that does not exist is None."""
    commit_file = evidence_dir / "commit.txt"
    commit = commit_file.read_text(encoding="utf-8").strip() if commit_file.exists() else None
    return {
        "builds": {build: _read_object(evidence_dir / f"build-{build}.json") for build in BUILDS},
        "self_hosted": _read_object(evidence_dir / "self-hosted.json"),
        "parity": _read_object(evidence_dir / "parity.json"),
        "volume": _read_object(evidence_dir / "volume.json"),
        "commit": commit,
        "known_limits": _read_object(evidence_dir / "known-limits.json"),
        "native_first_run": _read_object(live_dir / "native-first-run.json"),
        "sink_spike": _read_object(live_dir / "sink-spike.json"),
        "fallback_sink": _read_object(live_dir / "fallback-sink.json"),
        "vendoring": _read_object(live_dir / "vendoring.json"),
        "ci": _read_object(live_dir / "ci.json"),
    }


def _is_iso_date(value: object) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        _date.fromisoformat(value)
    except ValueError:
        return False
    return True


def known_limits_with_approvals(root: Path, live_dir: Path) -> dict[str, dict[str, Any]]:
    """The declared known limits and the author's approval of each (D-14).

    `build_fabric.declared_known_limits` reads the tagged nodes, their issue, reason and
    descendants. The two approval keys come from known-limit-approvals.json, which plan 05-19
    writes only from the author's reply at the checkpoint. A declared node with no approval gets
    false and null, and an approval for a node that is not declared is ignored.
    """
    declared = build_fabric.declared_known_limits(root)
    approvals = _read_object(live_dir / "known-limit-approvals.json") or {}
    limits: dict[str, dict[str, Any]] = {}
    for unique_id in sorted(declared):
        entry = declared[unique_id]
        approval = approvals.get(unique_id)
        approval = approval if isinstance(approval, dict) else {}
        approved = approval.get("approved_by_author") is True
        approved_on = approval.get("approved_on")
        limits[unique_id] = {
            "resource_type": entry["resource_type"],
            "issue": entry["issue"],
            "reason": entry["reason"],
            "descendants": sorted(entry["descendants"]),  # type: ignore[arg-type]
            "approved_by_author": approved,
            "approved_on": approved_on if approved and _is_iso_date(approved_on) else None,
        }
    return limits


def write_json(path: Path, data: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------------------------
# The checks


def excluded_owners(known_limits: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    """Map every excluded node to the declared node it is excluded with.

    A declared node maps to itself, and each of its descendants (D-14: they cannot build without
    it) to it.
    """
    owners: dict[str, str] = {}
    for declared in sorted(known_limits):
        owners.setdefault(declared, declared)
        for descendant in known_limits[declared].get("descendants") or []:
            owners.setdefault(str(descendant), declared)
    return owners


def _declared_is_valid(entry: Mapping[str, Any]) -> bool:
    issue, reason = entry.get("issue"), entry.get("reason")
    return (
        isinstance(issue, str)
        and ISSUE_URL_RE.match(issue) is not None
        and isinstance(reason, str)
        and bool(reason.strip())
        and len(reason.splitlines()) == 1
    )


def publishable(name: str, entry: Mapping[str, Any], known_limits: Mapping[str, Any]) -> bool:
    """The one rule for which outcome a record may show (D-14, clarified 2026-11-23).

    - pass, always.
    - documented-fallback, for job-rest-run alone (REST refused with a recorded status and
      errorCode, research Pattern 3).
    - excluded, for a node check only, when every node it excludes is a declared known limit with
      an upstream issue link or a descendant excluded with one.
    - fail and missing, never.
    """
    outcome = entry.get("outcome")
    if outcome == "pass":
        return True
    if outcome == "documented-fallback":
        return name == "job-rest-run"
    if outcome != "excluded" or name not in NODE_CHECKS:
        return False
    nodes = entry.get("excluded") or []
    if not nodes:
        return False
    owners = excluded_owners(known_limits)
    for unique_id in nodes:
        declared = known_limits.get(owners.get(str(unique_id), ""))
        if declared is None or not _declared_is_valid(declared):
            return False
    return True


def _entry(outcome: str, evidence: str, excluded: list[str] | None = None) -> dict[str, Any]:
    return {"outcome": outcome, "evidence": evidence, "excluded": sorted(excluded or [])}


def _kind(unique_id: str) -> str:
    return unique_id.split(".", 1)[0]


def _parts(unique_id: str) -> list[str]:
    return unique_id.split(".")


def _runs(evidence: Mapping[str, Any]) -> dict[str, dict[str, Any] | None]:
    runs: dict[str, dict[str, Any] | None] = {
        f"build-{build}": evidence["builds"].get(build) for build in BUILDS
    }
    runs[SELF_HOSTED] = evidence["self_hosted"]
    return runs


def _run_nodes(run: Mapping[str, Any] | None) -> list[dict[str, Any]] | None:
    if run is None:
        return None
    nodes = run.get("nodes")
    if not isinstance(nodes, list) or not all(isinstance(node, dict) for node in nodes):
        return None
    return nodes


def _invocation(run: Mapping[str, Any] | None) -> Mapping[str, Any]:
    invocation = run.get("invocation") if run else None
    return invocation if isinstance(invocation, dict) else {}


def _outcome_from(value: object, allowed: tuple[str, ...] = ("pass", "fail")) -> str:
    if value is None:
        return "missing"
    return value if value in allowed else "fail"


# Node checks: each defines its subject as a predicate over unique_id, the runs it reads, and a
# verdict over the subject nodes that ran in one run.


@dataclass(frozen=True)
class NodeCheck:
    runs: tuple[str, ...]
    subject: Callable[[str, ProjectExpectations], bool]
    verdict: Callable[[str, list[dict[str, Any]], list[str], ProjectExpectations], str]
    evidence: str
    after: Callable[[Mapping[str, Any]], str] | None = None


def _all_status(nodes: list[dict[str, Any]], allowed: tuple[str, ...]) -> bool:
    return all(node.get("status") in allowed for node in nodes)


def _undeclared_verdict(
    label: str, nodes: list[dict[str, Any]], excluded: list[str], expect: ProjectExpectations
) -> str:
    if not _all_status(nodes, NODE_OK_STATUSES):
        return "fail"
    if any(not isinstance(node.get("failures"), int) for node in nodes):
        return "missing"
    total = sum(int(node["failures"]) for node in nodes)
    wanted = expect.undeclared_failures.get(int(label.rsplit("-", 1)[1]))
    return "ok" if total == wanted else "fail"


def _is_undeclared_test(unique_id: str) -> bool:
    return _kind(unique_id) == "test" and UNDECLARED_COLUMNS_TEST_RE.search(unique_id) is not None


def _is_bronze_trips(unique_id: str, expect: ProjectExpectations) -> bool:
    """The bronze model of the added-column entity, or one of its unit tests; None names nothing."""
    wanted = expect.bronze_variant_model
    if wanted is None:
        return False
    parts = _parts(unique_id)
    kind = _kind(unique_id)
    if kind == "model":
        return parts[-1] == wanted
    return kind == "unit_test" and len(parts) > 2 and parts[2] == wanted


def _bronze_verdict(
    label: str, nodes: list[dict[str, Any]], excluded: list[str], expect: ProjectExpectations
) -> str:
    models = [node for node in nodes if _kind(str(node["unique_id"])) == "model"]
    units = [node for node in nodes if _kind(str(node["unique_id"])) == "unit_test"]
    if not models:
        return "missing"
    if _all_status(models, ("success",)) and _all_status(units, ("pass",)):
        return "ok"
    return "fail"


def _bronze_volume(evidence: Mapping[str, Any]) -> str:
    """The bronze trips model and its unit tests on the Fabric driver at the volume month."""
    volume = evidence["volume"]
    if volume is None:
        return "missing"
    trips = volume.get("bronze_trips")
    units = volume.get("unit_tests")
    if not isinstance(trips, dict) or not isinstance(units, list) or not units:
        return "missing"
    statuses = [unit.get("status") for unit in units if isinstance(unit, dict)]
    return "ok" if trips.get("status") == "success" and set(statuses) == {"pass"} else "fail"


def _is_conversion_fixture(unique_id: str) -> bool:
    return _kind(unique_id) == "test" and _parts(unique_id)[-1] == CONVERSION_FIXTURE_TEST


def _conversion_verdict(
    label: str, nodes: list[dict[str, Any]], excluded: list[str], expect: ProjectExpectations
) -> str:
    return "ok" if _all_status(nodes, ("pass",)) else "fail"


def _is_gold_model(unique_id: str) -> bool:
    return _kind(unique_id) == "model" and _parts(unique_id)[-1].startswith(GOLD_MODEL_PREFIX)


def _contracts_verdict(
    label: str, nodes: list[dict[str, Any]], excluded: list[str], expect: ProjectExpectations
) -> str:
    if not _all_status(nodes, ("success",)):
        return "fail"
    return "ok" if len(nodes) + len(excluded) == expect.gold_model_count else "fail"


def _is_seed(unique_id: str) -> bool:
    return _kind(unique_id) == "seed"


def _seed_verdict(
    label: str, nodes: list[dict[str, Any]], excluded: list[str], expect: ProjectExpectations
) -> str:
    if not _all_status(nodes, ("success",)):
        return "fail"
    return "ok" if len(nodes) + len(excluded) == expect.seed_count else "fail"


def _is_unit_test(unique_id: str) -> bool:
    return _kind(unique_id) == "unit_test"


def _unit_verdict(
    label: str, nodes: list[dict[str, Any]], excluded: list[str], expect: ProjectExpectations
) -> str:
    return "ok" if _all_status(nodes, ("pass",)) else "fail"


def _by_name(predicate: Callable[[str], bool]) -> Callable[[str, ProjectExpectations], bool]:
    """A subject that depends on the node alone, not on what the project holds."""
    return lambda unique_id, expect: predicate(unique_id)


NODE_CHECK_SPECS: dict[str, NodeCheck] = {
    "undeclared-columns-live": NodeCheck(
        runs=NATIVE_LABELS,
        subject=_by_name(_is_undeclared_test),
        verdict=_undeclared_verdict,
        evidence="build-1..4.json:nodes.failures",
    ),
    "bronze-plan-cost": NodeCheck(
        runs=NATIVE_LABELS,
        subject=_is_bronze_trips,
        verdict=_bronze_verdict,
        evidence="build-1..4.json+volume.json:bronze_trips.status",
        after=_bronze_volume,
    ),
    "conversion-fixture-spark": NodeCheck(
        runs=RUN_LABELS,
        subject=_by_name(_is_conversion_fixture),
        verdict=_conversion_verdict,
        evidence="build-1..4.json+self-hosted.json:nodes.status",
    ),
    "contracts-live": NodeCheck(
        runs=RUN_LABELS,
        subject=_by_name(_is_gold_model),
        verdict=_contracts_verdict,
        evidence="build-1..4.json+self-hosted.json:nodes.status",
    ),
    "seed-load": NodeCheck(
        runs=("build-1", "build-2"),
        subject=_by_name(_is_seed),
        verdict=_seed_verdict,
        evidence="build-1.json+build-2.json:nodes.status",
    ),
    "unit-tests-live": NodeCheck(
        runs=("build-1",),
        subject=_by_name(_is_unit_test),
        verdict=_unit_verdict,
        evidence="build-1.json:nodes.status",
    ),
}


def _node_check(
    name: str, evidence: Mapping[str, Any], owners: Mapping[str, str], expect: ProjectExpectations
) -> dict[str, Any]:
    """Evaluate a node check over the subject nodes that ran; list the excluded subject nodes.

    A count the check names (the seeds, the gold models) counts the excluded nodes as present.
    The outcome is `excluded` only when no subject node ran and some subject node is excluded.
    """
    spec = NODE_CHECK_SPECS[name]
    excluded = sorted(unique_id for unique_id in owners if spec.subject(unique_id, expect))
    runs = _runs(evidence)
    verdicts: list[str] = []
    for label in spec.runs:
        nodes = _run_nodes(runs[label])
        if nodes is None:
            return _entry("missing", spec.evidence, excluded)
        subject = [node for node in nodes if spec.subject(str(node.get("unique_id")), expect)]
        if subject:
            verdicts.append(spec.verdict(label, subject, excluded, expect))
        else:
            verdicts.append("empty" if excluded else "missing")
    if "fail" in verdicts:
        return _entry("fail", spec.evidence, excluded)
    if "missing" in verdicts:
        return _entry("missing", spec.evidence, excluded)
    if all(verdict == "empty" for verdict in verdicts):
        return _entry("excluded", spec.evidence, excluded)
    if spec.after is not None:
        later = spec.after(evidence)
        if later != "ok":
            return _entry(later, spec.evidence, excluded)
    return _entry("pass", spec.evidence, excluded)


def _native_job_deps(evidence: Mapping[str, Any]) -> dict[str, Any]:
    pointer = "build-1.json:invocation"
    build = evidence["builds"].get(1)
    version = _invocation(build).get("dbt_version")
    return _entry("pass" if isinstance(version, str) and version else "missing", pointer)


def _schema_layout(evidence: Mapping[str, Any]) -> dict[str, Any]:
    pointer = "parity.json:schema_tables"
    parity = evidence["parity"]
    tables = parity.get("schema_tables") if parity else None
    if not isinstance(tables, dict):
        return _entry("missing", pointer)
    try:
        ok = all(tables[layer] > 0 for layer in ("bronze", "silver", "gold")) and tables["dbo"] == 0
    except (KeyError, TypeError):
        return _entry("missing", pointer)
    return _entry("pass" if ok else "fail", pointer)


def _session_time_zone(evidence: Mapping[str, Any]) -> dict[str, Any]:
    pointer = "build-1..4.json+self-hosted.json:invocation.session_time_zone"
    zones: list[object] = []
    for run in _runs(evidence).values():
        if run is None:
            return _entry("missing", pointer)
        zones.append(_invocation(run).get("session_time_zone"))
    if any(zone is not None and zone != "UTC" for zone in zones):
        return _entry("fail", pointer)
    return _entry("missing" if any(zone is None for zone in zones) else "pass", pointer)


def _profile_schema(evidence: Mapping[str, Any]) -> dict[str, Any]:
    pointer = "native-first-run.json:checks.native-job-profile-schema.outcome"
    first = evidence["native_first_run"] or {}
    checks = first.get("checks")
    check = checks.get("native-job-profile-schema") if isinstance(checks, dict) else None
    outcome = check.get("outcome") if isinstance(check, dict) else None
    return _entry(_outcome_from(outcome), pointer)


def _sink_triggered(evidence: Mapping[str, Any]) -> bool:
    fallback = evidence["fallback_sink"]
    return isinstance(fallback, dict) and fallback.get("triggered") is True


def _sink_discovery(evidence: Mapping[str, Any]) -> dict[str, Any]:
    if _sink_triggered(evidence):
        pointer = "fallback-sink.json:record.lakehouse_counts_second"
        record = evidence["fallback_sink"].get("record")
        counts = (
            [record.get(key) for key in ("duckdb_counts", "lakehouse_counts_first")]
            + [record.get("lakehouse_counts_second")]
            if isinstance(record, dict)
            else [None]
        )
        if any(count is None for count in counts):
            return _entry("missing", pointer)
        return _entry(
            "pass" if counts[0] == counts[1] == counts[2] and counts[0] else "fail", pointer
        )
    pointer = "sink-spike.json:pass_bar.discovery"
    bar = (evidence["sink_spike"] or {}).get("pass_bar")
    return _entry(_outcome_from(bar.get("discovery") if isinstance(bar, dict) else None), pointer)


def _sql_endpoint(evidence: Mapping[str, Any]) -> dict[str, Any]:
    if _sink_triggered(evidence):
        pointer = "fallback-sink.json:sql_endpoint.outcome"
        endpoint = evidence["fallback_sink"].get("sql_endpoint")
        value = endpoint.get("outcome") if isinstance(endpoint, dict) else None
        return _entry(_outcome_from(value), pointer)
    pointer = "sink-spike.json:pass_bar.sql_endpoint"
    bar = (evidence["sink_spike"] or {}).get("pass_bar")
    return _entry(
        _outcome_from(bar.get("sql_endpoint") if isinstance(bar, dict) else None), pointer
    )


def _oidc_live(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Bound to the record's commit: a green run of another commit never settles it (D-17, D-20)."""
    pointer = "ci.json+commit.txt:headSha"
    ci, commit = evidence["ci"], evidence["commit"]
    if ci is None or not commit:
        return _entry("missing", pointer)
    same = ci.get("conclusion") == "success" and ci.get("headSha") == commit
    return _entry("pass" if same else "fail", pointer)


def _job_rest_run(evidence: Mapping[str, Any]) -> dict[str, Any]:
    pointer = "native-first-run.json:job_rest_run.outcome"
    rest = (evidence["native_first_run"] or {}).get("job_rest_run")
    if not isinstance(rest, dict) or rest.get("outcome") is None:
        return _entry("missing", pointer)
    outcome = rest["outcome"]
    if outcome == "pass":
        return _entry("pass", pointer)
    if outcome == "documented-fallback":
        status, code = rest.get("http_status"), rest.get("error_code")
        recorded = (
            isinstance(status, int)
            and not isinstance(status, bool)
            and status >= 400
            and isinstance(code, str)
            and ERROR_CODE_RE.match(code) is not None
        )
        return _entry("documented-fallback" if recorded else "fail", pointer)
    return _entry("fail", pointer)


NON_NODE_CHECKS: dict[str, Callable[[Mapping[str, Any]], dict[str, Any]]] = {
    "native-job-deps": _native_job_deps,
    "schema-layout": _schema_layout,
    "session-time-zone": _session_time_zone,
    "native-job-profile-schema": _profile_schema,
    "sink-discovery": _sink_discovery,
    "sql-endpoint-read": _sql_endpoint,
    "oidc-live": _oidc_live,
    "job-rest-run": _job_rest_run,
}


def derive(
    evidence: Mapping[str, Any],
    known_limits: Mapping[str, Mapping[str, Any]],
    expect: ProjectExpectations | None = None,
) -> dict[str, dict[str, Any]]:
    """Derive each live check from loaded evidence, keyed by check name in sorted order.

    `expect` is what the project must hold; it defaults to `expected_project` of this checkout.
    """
    expect = expect if expect is not None else expected_project(repo_root())
    owners = excluded_owners(known_limits)
    checks: dict[str, dict[str, Any]] = {}
    for name in sorted(LIVE_CHECKS):
        if name in NODE_CHECKS:
            checks[name] = _node_check(name, evidence, owners, expect)
        else:
            checks[name] = NON_NODE_CHECKS[name](evidence)
    return checks


def derive_checks(
    evidence_dir: Path,
    live_dir: Path,
    known_limits: Mapping[str, Mapping[str, Any]] | None = None,
    *,
    expect: ProjectExpectations | None = None,
) -> dict[str, dict[str, Any]]:
    """Read the evidence folders and return {check: {outcome, evidence, excluded}} (D-10, D-12)."""
    evidence = load_evidence(evidence_dir, live_dir)
    if known_limits is None:
        known_limits = evidence["known_limits"] or {}
    return derive(evidence, known_limits, expect)


def assemble(
    evidence_dir: Path,
    live_dir: Path,
    date: str,
    *,
    draft: bool,
    expect: ProjectExpectations | None = None,
) -> dict[str, Any]:
    """Load the evidence and add what a rendering needs: the date, the checks and the draft flag."""
    evidence = load_evidence(evidence_dir, live_dir)
    evidence["date"] = date
    evidence["draft"] = draft
    evidence["checks"] = derive(evidence, evidence["known_limits"] or {}, expect)
    return evidence


# ---------------------------------------------------------------------------------------------
# Rendering


# The words a status cell may hold: the check outcomes, `pending` for a draft, the run statuses of
# a node and of a job instance, and where a build's results came from. Nothing else is rendered
# as a status.
STATUS_WORDS: tuple[str, ...] = (
    *CHECK_OUTCOMES,
    PENDING,
    "success",
    "pass",
    "warn",
    "error",
    "skipped",
    "Completed",
    "Failed",
    "Cancelled",
    *RESULTS_SOURCES,
    "run_results.json",
)

# Every interpolated value passes one of these, by kind (T-05-33). A value that fits none is
# refused, never escaped.
VALUE_PATTERNS: dict[str, re.Pattern[str]] = {
    "version": re.compile(r"^\d+(?:\.\d+){1,3}[A-Za-z0-9.+-]{0,20}$"),
    "status": re.compile("^(?:" + "|".join(re.escape(word) for word in STATUS_WORDS) + ")$"),
    "node": re.compile(r"^[a-z0-9_]+(?:\.[a-z0-9_]+)*$"),
    "relation": re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$"),
    "integer": re.compile(r"^\d{1,12}$"),
    "rate": re.compile(r"^\d+(?:\.\d{1,6})?$"),
    "date": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    "month": re.compile(r"^\d{4}-\d{2}$"),
    "sha": re.compile(r"^[0-9a-f]{40}$"),
    "run_id": re.compile(r"^\d{1,20}$"),
    "issue": ISSUE_URL_RE,
    "reason": re.compile(r"^[ -~]{1,200}$"),
    "errorcode": ERROR_CODE_RE,
    "identifier": re.compile(r"^[a-z_][a-z0-9_]{0,63}$"),
    "entity": re.compile(r"^[a-z][a-z0-9_]{0,63}$"),
    "sink": re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$"),
    "check": re.compile(r"^[a-z][a-z0-9-]{0,63}$"),
    "evidence": re.compile(r"^[A-Za-z0-9_.+-]+:[A-Za-z0-9_.-]+$"),
}
INTEGER_KINDS = ("integer", "run_id")


def field(kind: str, name: str, value: object) -> str:
    """Return `value` for the Markdown when it fits the pattern of its kind.

    The refusal names the field and the kind, never the value (T-05-33).
    """
    if kind in INTEGER_KINDS and isinstance(value, int) and not isinstance(value, bool):
        text: str | None = str(value) if value >= 0 else None
    else:
        text = value if isinstance(value, str) else None
    if (
        text is None
        or VALUE_PATTERNS[kind].match(text) is None
        or (kind == "date" and not _is_iso_date(text))
    ):
        raise RecordRefused(f"the {name} value is outside its {kind} pattern")
    return text


UUID_TEXT_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
EMAIL_TEXT_RE = re.compile(r"[^\s@`]+@[^\s@`]+\.[^\s@`]+")
TENANT_HOST_RE = re.compile(r"onmicrosoft\.com", re.IGNORECASE)


def sweep(text: str) -> None:
    """Refuse a rendered record that holds anything shaped like a tenant value (T-05-33)."""
    if UUID_TEXT_RE.search(text):
        raise RecordRefused("the rendered record holds UUID-shaped text")
    if TENANT_HOST_RE.search(text):
        raise RecordRefused("the rendered record holds a tenant host name")
    if EMAIL_TEXT_RE.search(text):
        raise RecordRefused("the rendered record holds an email address")


def layer_of(relation: str) -> str:
    schema, _, table = relation.partition(".")
    if schema == "silver" and table.endswith(QUARANTINE_SUFFIX):
        return "quarantine"
    return schema if schema in LAYER_ORDER else LAYER_ORDER[-1]


def _relation_order(relation: str) -> tuple[int, str]:
    return LAYER_ORDER.index(layer_of(relation)), relation


def _count(value: object) -> str:
    if value is None:
        return "none"
    return field("integer", "count", value)


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(" --- " for _ in header) + "|"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


def _on_off(value: object) -> str:
    return {True: "on", False: "off"}.get(value, PENDING)  # type: ignore[call-overload]


def _node_counts(run: Mapping[str, Any]) -> dict[str, int]:
    nodes = _run_nodes(run) or []
    counts = {"model": 0, "seed": 0, "unit_test": 0, "pass": 0, "warn": 0, "fail": 0}
    seconds = 0.0
    for node in nodes:
        kind = str(node.get("resource_type"))
        status = str(node.get("status"))
        if kind in ("model", "seed", "unit_test"):
            counts[kind] += 1
        if kind == "test":
            if status == "pass":
                counts["pass"] += 1
            elif status == "warn":
                counts["warn"] += 1
            elif status in ("fail", "error"):
                counts["fail"] += 1
        seconds += float(node.get("execution_time") or 0.0)
    counts["tenths"] = round(seconds * 10)
    return counts


def _builds_section(evidence: Mapping[str, Any]) -> list[str]:
    header = [
        "Run",
        "Full refresh",
        "Job status",
        "Results source",
        "Models",
        "Tests pass",
        "Tests warn",
        "Tests fail",
        "Seeds",
        "Unit tests",
        "Duration (s)",
    ]
    rows: list[list[str]] = []
    for label, run in _runs(evidence).items():
        if run is None:
            rows.append([label, *([PENDING] * (len(header) - 1))])
            continue
        counts = _node_counts(run)
        if label == SELF_HOSTED:
            status = f"exit {field('integer', 'exit_code', run.get('exit_code'))}"
            source = "run_results.json"
        else:
            status = field("status", "job_status", run.get("job_status"))
            source = field("status", "results_source", run.get("results_source"))
        rows.append(
            [
                label,
                _on_off(_invocation(run).get("full_refresh")),
                status,
                source,
                _count(counts["model"]),
                _count(counts["pass"]),
                _count(counts["warn"]),
                _count(counts["fail"]),
                _count(counts["seed"]),
                _count(counts["unit_test"]),
                f"{counts['tenths'] // 10}.{counts['tenths'] % 10}",
            ]
        )
    return _table(header, rows)


def _spark_version(build: Mapping[str, Any] | None) -> str:
    engine = _invocation(build).get("engine_version")
    token = engine.split()[0] if isinstance(engine, str) and engine.split() else None
    return field("version", "engine_version", token) if token else PENDING


def _versions(evidence: Mapping[str, Any]) -> dict[str, str]:
    build = evidence["builds"].get(1)
    core = _invocation(build).get("dbt_version")
    adapter = ((evidence["native_first_run"] or {}).get("log_lines") or {}).get("adapter")
    found = re.search(r"fabricspark=(\S+)", adapter) if isinstance(adapter, str) else None
    spark = _spark_version(build)
    line = ".".join(spark.split(".")[:2]) if spark != PENDING else ""
    runtime = RUNTIME_BY_SPARK.get(line)
    return {
        "core": field("version", "dbt_version", core) if isinstance(core, str) else PENDING,
        "fabricspark": field("version", "dbt-fabricspark", found.group(1)) if found else PENDING,
        "spark": spark,
        "runtime": runtime if runtime else PENDING,
    }


def _sink_name(evidence: Mapping[str, Any]) -> str:
    source = evidence["fallback_sink"] if _sink_triggered(evidence) else evidence["sink_spike"]
    decision = (source or {}).get("decision")
    return field("sink", "sink", decision) if decision else PENDING


MONTH_PLACEHOLDER = "<month>"


def _volume_month(evidence: Mapping[str, Any]) -> str:
    """The month volume.json names; a draft with no volume.json names none."""
    volume = evidence["volume"]
    if volume is None:
        return MONTH_PLACEHOLDER
    return field("month", "month", volume.get("month"))


def _run_modes(evidence: Mapping[str, Any]) -> list[str]:
    first = evidence["native_first_run"] or {}
    schema = (first.get("profile") or {}).get("schema")
    commit = evidence["commit"]
    rest = first.get("job_rest_run") or {}
    if rest.get("outcome") == "pass":
        started = "started over REST"
    elif rest.get("outcome") == "documented-fallback":
        started = (
            "started in the portal (the REST start was refused with HTTP "
            f"{field('integer', 'http_status', rest.get('http_status'))}, "
            f"{field('errorcode', 'error_code', rest.get('error_code'))})"
        )
    else:
        started = PENDING
    native = (
        "- Native job: operation build, selector `fabric_fixture`, Seed data on, profile schema "
        f"`{field('identifier', 'profile.schema', schema) if schema else PENDING}`, branch main "
        f"at commit `{field('sha', 'commit', commit) if commit else PENDING}`, {started}, landing "
        f"sink `{_sink_name(evidence)}`"
    )
    if (evidence["vendoring"] or {}).get("triggered") is True:
        native += ", vendored package layout"
    ci = evidence["ci"]
    run_id = field("run_id", "run_id", ci.get("run_id")) if ci else PENDING
    return [
        native + ".",
        "- Self-hosted: `build-fabric --selector fabric_fixture` from the verifying machine.",
        (
            f"- Self-hosted volume: `demo --target fabric --months {_volume_month(evidence)}` "
            "in its own Lakehouse."
        ),
        f"- CI: GitHub Actions run `{run_id}`, signed in through OIDC.",
    ]


def _parity_section(evidence: Mapping[str, Any]) -> list[str]:
    parity = evidence["parity"]
    if parity is None:
        return [PENDING]
    relations = sorted(
        (entry for entry in parity.get("relations") or [] if isinstance(entry, dict)),
        key=lambda entry: _relation_order(str(entry["relation"]).lower()),
    )
    rows = []
    for entry in relations:
        name = field("relation", "relation", str(entry["relation"]).lower())
        rows.append(
            [
                name,
                _count(entry.get("duckdb")),
                _count(entry.get("fabric")),
                "yes" if entry.get("equal") is True else "no",
            ]
        )
    return _table(["Relation", "DuckDB", "Fabric", "Equal"], rows)


VOLUME_SCOPE = (
    "Scope: counts per layer and entity and the per-entity and worst-file quarantine rates on both "
    "engines, the bronze trips model and its unit tests on the Fabric driver, and the Fabric "
    "demo's exit code with no failing node, on real levels with declared known limits excluded."
)


def _volume_section(evidence: Mapping[str, Any]) -> list[str]:
    lines = [VOLUME_SCOPE, ""]
    volume = evidence["volume"]
    if volume is None:
        return [*lines, PENDING]
    month = field("month", "month", volume.get("month"))
    fabric, local = volume.get("fabric") or {}, volume.get("duckdb") or {}
    counts_fabric, counts_local = fabric.get("counts") or {}, local.get("counts") or {}
    rows = [
        [
            field("relation", "relation", relation),
            _count(counts_local.get(relation)),
            _count(counts_fabric.get(relation)),
        ]
        for relation in sorted(set(counts_fabric) | set(counts_local), key=_relation_order)
    ]
    lines += [f"Month {month}.", "", *_table(["Relation", "DuckDB", "Fabric"], rows), ""]
    rates_fabric, rates_local = fabric.get("rates") or {}, local.get("rates") or {}
    rate_rows = []
    for entity in sorted(set(rates_fabric) | set(rates_local)):
        mine, theirs = rates_local.get(entity) or {}, rates_fabric.get(entity) or {}
        rate_rows.append(
            [
                field("entity", "entity", entity),
                field("rate", "quarantine_rate", mine.get("quarantine_rate")),
                field("rate", "quarantine_rate", theirs.get("quarantine_rate")),
                field("rate", "worst_file_rate", mine.get("worst_file_rate")),
                field("rate", "worst_file_rate", theirs.get("worst_file_rate")),
            ]
        )
    lines += _table(
        [
            "Entity",
            "Quarantine rate, DuckDB",
            "Quarantine rate, Fabric",
            "Worst file rate, DuckDB",
            "Worst file rate, Fabric",
        ],
        rate_rows,
    )
    trips = volume.get("bronze_trips") or {}
    units = [unit for unit in volume.get("unit_tests") or [] if isinstance(unit, dict)]
    passing = sum(1 for unit in units if unit.get("status") == "pass")
    failed = volume.get("fabric_failed_nodes") or []
    lines += [
        "",
        f"- Bronze trips model on the Fabric driver: {field('status', 'status', trips.get('status'))}",
        f"- Bronze unit tests passing: {_count(passing)} of {_count(len(units))}",
        f"- Fabric demo exit code: {_count(volume.get('fabric_exit_code'))}",
        f"- DuckDB demo exit code: {_count(volume.get('duckdb_exit_code'))}",
        f"- Fabric nodes outside {'/'.join(NODE_OK_STATUSES)}: {_count(len(failed))}",
    ]
    return lines


def _conversion_section(evidence: Mapping[str, Any]) -> list[str]:
    checks = evidence["checks"]
    rows = []
    for label, run in _runs(evidence).items():
        nodes = _run_nodes(run) or []
        found = [n for n in nodes if _is_conversion_fixture(str(n.get("unique_id")))]
        if found:
            status = field("status", "status", found[0].get("status"))
        else:
            status = (
                "excluded"
                if checks["conversion-fixture-spark"]["outcome"] == "excluded"
                else PENDING
            )
        rows.append([label, status])
    outcome = checks["conversion-fixture-spark"]["outcome"]
    return [
        (
            "The conversion fixture test (`shim_conversion_fixture`) runs the shim grammar on live "
            "Spark and returns no rows when it agrees with DuckDB. "
            f"Outcome: {_shown(evidence, outcome)}."
        ),
        "",
        *_table(["Run", "Test status"], rows),
    ]


def _shown(evidence: Mapping[str, Any], outcome: str) -> str:
    """A draft shows an outcome with no evidence yet as pending; a record never reaches it."""
    if evidence["draft"] and outcome == "missing":
        return PENDING
    return field("status", "outcome", outcome)


def _checks_section(evidence: Mapping[str, Any]) -> list[str]:
    rows = []
    for name, entry in sorted(evidence["checks"].items()):
        excluded = ", ".join(f"`{field('node', 'node', uid)}`" for uid in entry["excluded"])
        rows.append(
            [
                field("check", "check", name),
                _shown(evidence, entry["outcome"]),
                field("evidence", "evidence", entry["evidence"]),
                excluded or "none",
            ]
        )
    return _table(["Check", "Outcome", "Evidence", "Excluded nodes"], rows)


def _matrix_section(evidence: Mapping[str, Any], versions: Mapping[str, str]) -> list[str]:
    core = versions["core"]
    minor = ".".join(core.split(".")[:2]) if core != PENDING else PENDING
    date = field("date", "date", evidence["date"])
    rows = [
        [
            f"dbt Core {minor} (the job's patch {core})",
            f"dbt-fabricspark {versions['fabricspark']}",
            f"Fabric Runtime {versions['runtime']} (Spark {versions['spark']})",
            f"verified {date}, native job and self-hosted",
        ],
        [
            f"dbt Core {minor}",
            f"dbt-fabricspark {versions['fabricspark']}",
            "Fabric Runtime 1.3 (Spark 3.5)",
            (
                "not verified live; end of support announced; the shims passed a local Spark "
                "3.5.5 run in Phase 04.1"
            ),
        ],
        ["CI sign-in with a client secret", "n/a", "n/a", "not run live"],
    ]
    return _table(["dbt Core", "Adapter", "Fabric runtime", "Status"], rows)


def _known_limits_section(evidence: Mapping[str, Any]) -> list[str]:
    limits = evidence["known_limits"]
    if limits is None:
        return [PENDING]
    if not limits:
        return ["none"]
    lines: list[str] = []
    for unique_id in sorted(limits):
        entry = limits[unique_id]
        approved = entry.get("approved_on")
        when = field("date", "approved_on", approved) if approved else "not yet approved"
        lines.append(
            f"- `{field('node', 'node', unique_id)}` ({field('node', 'resource_type', entry.get('resource_type'))}): "
            f"issue {field('issue', 'issue', entry['issue'])}. Reason: "
            f"{field('reason', 'reason', entry['reason'])}. Exclusion approved by the author on {when}."
        )
        descendants = sorted(str(d) for d in entry.get("descendants") or [])
        listed = ", ".join(f"`{field('node', 'node', d)}`" for d in descendants)
        lines.append(f"  - Excluded with it: {listed or 'none'}")
    return lines


CLOSING_NOTE = (
    "This record holds aggregates only: counts, statuses, versions and issue links. Every tenant "
    "value is a stand-in such as `<workspace-id>`. It is never edited; a later verification adds "
    "a new dated file."
)


def render_record(evidence: Mapping[str, Any]) -> str:
    """Render the Markdown record from assembled evidence. The same evidence gives the same text."""
    date = field("date", "date", evidence["date"])
    versions = _versions(evidence)
    lines = [f"# Fabric verification, {date}", ""]
    if evidence["draft"]:
        lines += [
            (
                "> Draft rendered from rehearsal evidence. Items with no evidence yet are "
                "pending. This is not a record."
            ),
            "",
        ]
    lines += [
        (
            f"dbt Core {versions['core']} with dbt-fabricspark {versions['fabricspark']} on "
            f"Fabric Runtime {versions['runtime']} (Spark {versions['spark']}), verified on "
            f"{date} in the native-job and self-hosted modes."
        ),
        "",
        "## Versions",
        "",
        f"- dbt Core (the native job's patch): {versions['core']}",
        f"- dbt-fabricspark: {versions['fabricspark']}",
        f"- Fabric runtime: {versions['runtime']}",
        f"- Spark: {versions['spark']}",
        f"- Date: {date}",
        "",
        "## Run modes",
        "",
        *_run_modes(evidence),
        "",
        "## Builds",
        "",
        *_builds_section(evidence),
        "",
        "## Row counts on the fixture",
        "",
        *_parity_section(evidence),
        "",
        f"## {_volume_month(evidence)}",
        "",
        *_volume_section(evidence),
        "",
        "## Conversion fixture",
        "",
        *_conversion_section(evidence),
        "",
        "## Live checks",
        "",
        *_checks_section(evidence),
        "",
        "## Compatibility matrix",
        "",
        *_matrix_section(evidence, versions),
        "",
        "## Known limits",
        "",
        *_known_limits_section(evidence),
        "",
        CLOSING_NOTE,
        "",
    ]
    text = "\n".join(lines)
    sweep(text)
    return text


# ---------------------------------------------------------------------------------------------
# Refusals: what the evidence must prove before a record is written (D-08, D-12 to D-14)


def _name(unique_id: object) -> str:
    """A node name for a message when it fits the node pattern, never a raw value."""
    text = str(unique_id)
    if VALUE_PATTERNS["node"].match(text):
        return text
    return "(a node whose name is outside the allowed pattern)"


def _ints_equal(left: object, right: object) -> bool:
    try:
        return left is not None and right is not None and int(left) == int(right)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return False


def _missing_evidence(evidence: Mapping[str, Any]) -> list[str]:
    problems = [
        f"build-{build}.json is missing"
        for build in BUILDS
        if evidence["builds"].get(build) is None
    ]
    for key, name in (
        ("self_hosted", "self-hosted.json"),
        ("parity", "parity.json"),
        ("volume", "volume.json"),
        ("known_limits", "known-limits.json"),
        ("ci", "ci.json"),
        ("commit", "commit.txt"),
    ):
        if evidence[key] is None:
            problems.append(f"{name} is missing")
    for build in BUILDS:
        record = evidence["builds"].get(build)
        if record is not None and record.get("results_source") not in RESULTS_SOURCES:
            problems.append(f"build-{build}.json has no results source of hook or log")
    return problems


def _known_limit_problems(known_limits: Mapping[str, Mapping[str, Any]]) -> list[str]:
    problems: list[str] = []
    for unique_id in sorted(known_limits):
        entry, name = known_limits[unique_id], _name(unique_id)
        if not _declared_is_valid(entry):
            problems.append(f"declared known limit {name} has no valid issue link or reason")
        if not (entry.get("approved_by_author") is True and _is_iso_date(entry.get("approved_on"))):
            problems.append(f"known limit {name} was excluded without the author's approval")
    return problems


def _node_problems(
    evidence: Mapping[str, Any], known_limits: Mapping[str, Mapping[str, Any]]
) -> list[str]:
    problems: list[str] = []
    runs = _runs(evidence)
    for label in RUN_LABELS:
        nodes = _run_nodes(runs[label])
        if nodes is None:
            continue
        ran = {str(node.get("unique_id")) for node in nodes}
        for node in nodes:
            if node.get("status") not in NODE_OK_STATUSES:
                problems.append(
                    f"node {_name(node.get('unique_id'))} is not in an ok status in {label}"
                )
        for declared in sorted(known_limits):
            if declared in ran:
                problems.append(
                    f"node {_name(declared)} is declared as excluded but it ran in {label}"
                )
            for descendant in sorted(known_limits[declared].get("descendants") or []):
                if descendant in ran:
                    problems.append(
                        f"node {_name(descendant)} is excluded as a descendant of "
                        f"{_name(declared)} but it ran in {label}"
                    )
    return problems


def _run_problems(evidence: Mapping[str, Any]) -> list[str]:
    problems: list[str] = []
    for build in BUILDS:
        record = evidence["builds"].get(build)
        if record is None:
            continue
        label = f"build-{build}"
        if record.get("full_refresh_expected") is not FULL_REFRESH_EXPECTED[build - 1]:
            problems.append(f"{label} does not expect the sequence's full refresh state")
        if _invocation(record).get("full_refresh") is not record.get("full_refresh_expected"):
            problems.append(
                f"{label} ran with a full refresh state that differs from the expected one"
            )
        if record.get("job_status") != "Completed":
            problems.append(f"{label} job did not complete")
    hosted = evidence["self_hosted"]
    if hosted is not None and hosted.get("exit_code") != 0:
        problems.append("self-hosted build exited non-zero")
    return problems


def _parity_problems(parity: Mapping[str, Any] | None) -> list[str]:
    if parity is None:
        return []
    problems: list[str] = []
    if parity.get("all_equal") is not True:
        problems.append("parity differs (the report relations are not all equal)")
    for entry in parity.get("relations") or []:
        if isinstance(entry, dict) and not _ints_equal(entry.get("duckdb"), entry.get("fabric")):
            problems.append(f"parity differs for {_name(str(entry.get('relation')).lower())}")
    return problems


def _rates_equal(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Compare the per-entity rates as decimals at six places, never as float text."""
    if set(left) != set(right):
        return False
    try:
        for entity in left:
            for key in ("quarantine_rate", "worst_file_rate"):
                one = Decimal(str(left[entity][key])).quantize(Decimal("0.000001"))
                other = Decimal(str(right[entity][key])).quantize(Decimal("0.000001"))
                if one != other:
                    return False
    except (KeyError, TypeError, ArithmeticError):
        return False
    return True


def _volume_problems(volume: Mapping[str, Any] | None) -> list[str]:
    if volume is None:
        return []
    problems: list[str] = []
    if volume.get("fabric_exit_code") != 0 or volume.get("duckdb_exit_code") != 0:
        problems.append("volume run exited non-zero")
    if volume.get("fabric_failed_nodes"):
        problems.append("volume run has a failing node")
    counts_fabric = (volume.get("fabric") or {}).get("counts") or {}
    counts_local = (volume.get("duckdb") or {}).get("counts") or {}
    unequal = set(counts_fabric) != set(counts_local) or any(
        not _ints_equal(counts_local[name], counts_fabric[name])
        for name in counts_fabric
        if name in counts_local
    )
    if volume.get("counts_all_equal") is not True or unequal:
        problems.append("volume counts differ")
    rates_fabric = (volume.get("fabric") or {}).get("rates") or {}
    rates_local = (volume.get("duckdb") or {}).get("rates") or {}
    if volume.get("rates_all_equal") is not True or not _rates_equal(rates_fabric, rates_local):
        problems.append("volume rates differ")
    return problems


def refusals(evidence: Mapping[str, Any], head: str | None) -> list[str]:
    """Everything that stops a non-draft record, each naming the item and never a value."""
    known_limits = evidence["known_limits"] or {}
    problems = _missing_evidence(evidence)
    problems += _known_limit_problems(known_limits)
    problems += _node_problems(evidence, known_limits)
    problems += _run_problems(evidence)
    problems += _parity_problems(evidence["parity"])
    problems += _volume_problems(evidence["volume"])
    for name, entry in sorted(evidence["checks"].items()):
        if entry["outcome"] == "missing":
            problems.append(f"check {name} is missing its evidence ({entry['evidence']})")
        elif not publishable(name, entry, known_limits):
            problems.append(f"check {name} is not publishable ({entry['outcome']})")
    commit = evidence["commit"]
    if commit is not None:
        if VALUE_PATTERNS["sha"].match(commit) is None:
            problems.append("commit.txt does not hold a 40-hex commit SHA")
        elif head is not None and commit != head:
            problems.append(
                "commit.txt differs from HEAD (the declared exclusions must be read at the "
                "run's commit)"
            )
    for part, version in _versions(evidence).items():
        if version == PENDING:
            problems.append(f"the {part} version is not in the evidence")
    return problems


def check_record(evidence: Mapping[str, Any], head: str | None) -> None:
    """Raise RecordRefused unless the evidence proves a run with no failing node (D-14)."""
    problems = refusals(evidence, head)
    if problems:
        raise RecordRefused("; ".join(problems))


def write_record(path: Path, text: str) -> None:
    """Write the record with an exclusive create; an existing file is never touched (D-12)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "x", encoding="utf-8") as handle:
            handle.write(text)
    except FileExistsError:
        raise RecordRefused(
            f"{path} already exists; a later verification adds a new dated file"
        ) from None

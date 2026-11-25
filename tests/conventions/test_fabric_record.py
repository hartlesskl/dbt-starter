"""The Fabric verification record writer, on synthetic evidence (D-10, D-12 to D-14).

Every test builds its own evidence folder in `tmp_path` with the keys the producing plans pin and
no id anywhere in it: the commit is a made-up 40-hex SHA and every node name is invented by the
builder below. The writer reads files only, so nothing here reaches a Fabric service.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from starter_cli import fabric_record, verify_fabric

SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER_SHA = "fedcba9876543210fedcba9876543210fedcba98"
DATE = "2026-12-01"
ISSUE = "https://github.com/example-org/example-repo/issues/42"

GOLD = [f"gold__{name}" for name in ("agg", "dim_a", "dim_b", "dim_c", "dim_d", "dim_e", "fct")]
SEEDS = [f"seed_{number}" for number in range(6)]
BRONZE_UNITS = ["trip_rules", "trip_dupes"]


def node(unique_id: str, status: str, failures: int | None = None) -> dict[str, Any]:
    return {
        "unique_id": unique_id,
        "resource_type": unique_id.split(".", 1)[0],
        "status": status,
        "failures": failures,
        "execution_time": 1.5,
    }


def build_nodes(build: int) -> list[dict[str, Any]]:
    undeclared_failures = {1: 1, 2: 1, 3: 1, 4: 2}[build]
    nodes = [node(f"seed.proj.{name}", "success") for name in SEEDS]
    nodes += [node(f"model.proj.{name}", "success") for name in GOLD]
    nodes += [
        node("model.proj.bronze__nyc_taxi__yellow_trips", "success"),
        node("model.proj.silver__yellow_trips", "success"),
        node(
            "test.proj.yellow_trips_undeclared_landing_columns.abc123", "warn", undeclared_failures
        ),
        node("test.proj.taxi_zones_undeclared_landing_columns.def456", "pass", 0),
        node("test.starter.shim_conversion_fixture", "pass", 0),
        node("test.proj.some_other_test.789abc", "pass", 0),
    ]
    nodes += [
        node(f"unit_test.proj.bronze__nyc_taxi__yellow_trips.{n}", "pass", 0) for n in BRONZE_UNITS
    ]
    nodes += [node("unit_test.proj.silver__yellow_trips.silver_rule", "pass", 0)]
    return sorted(nodes, key=lambda item: item["unique_id"])


def counts(nodes: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    return verify_fabric.status_counts(nodes)


def build_record(build: int) -> dict[str, Any]:
    nodes = build_nodes(build)
    return {
        "build": build,
        "mode": "manual",
        "full_refresh_expected": build == 3,
        "started_at": "2026-12-01T09:00:00Z",
        "job_status": "Completed",
        "failure_code": None,
        "results_source": "hook",
        "invocation": {
            "dbt_version": "1.11.8",
            "engine_version": "4.1.1 c9df16047cb2570aec895cd4718f04025913e19f",
            "session_time_zone": "UTC",
            "command": "build",
            "full_refresh": build == 3,
        },
        "status_counts": counts(nodes),
        "nodes": nodes,
    }


def self_hosted_record() -> dict[str, Any]:
    nodes = build_nodes(4)
    return {
        "mode": "self-hosted",
        "selector": "fabric_fixture",
        "exit_code": 0,
        "invocation": build_record(4)["invocation"],
        "status_counts": counts(nodes),
        "nodes": nodes,
    }


RELATIONS = [
    "landing.entity_a",
    "bronze.entity_a",
    "silver.entity_a",
    "silver.entity_a_quarantine",
    "gold.dim_a",
    "gold.fct",
]


def parity_record() -> dict[str, Any]:
    return {
        "relations": [
            {"relation": relation, "duckdb": number, "fabric": number, "equal": True}
            for number, relation in enumerate(RELATIONS)
        ],
        "all_equal": True,
        "schema_tables": {"bronze": 8, "silver": 7, "gold": 7, "dbo": 0},
    }


def volume_record() -> dict[str, Any]:
    side = {
        "counts": {relation: 10 + number for number, relation in enumerate(RELATIONS)},
        "rates": {"entity_a": {"quarantine_rate": "0.010000", "worst_file_rate": "0.020000"}},
    }
    return {
        "month": "2024-01",
        "date": "2026-12-01",
        "platform": "test",
        "fabric_seconds": 12.5,
        "fabric_exit_code": 0,
        "duckdb_exit_code": 0,
        "fabric_status_counts": {},
        "fabric_failed_nodes": [],
        "fabric": side,
        "duckdb": side,
        "counts_all_equal": True,
        "rates_all_equal": True,
        "bronze_trips": {
            "unique_id": "model.proj.bronze__nyc_taxi__yellow_trips",
            "status": "success",
            "execution_time": 3.0,
        },
        "unit_tests": [
            {"unique_id": f"unit_test.proj.bronze__nyc_taxi__yellow_trips.{n}", "status": "pass"}
            for n in BRONZE_UNITS
        ],
    }


def native_first_run() -> dict[str, Any]:
    return {
        "profile": {"schema": "dbo"},
        "log_lines": {"adapter": "Registered adapter: fabricspark=1.12.2"},
        "checks": {"native-job-profile-schema": {"outcome": "pass", "evidence": "text"}},
        "job_rest_run": {
            "outcome": "documented-fallback",
            "http_status": 400,
            "error_code": "InvalidJobType",
        },
    }


SINK_PASS_BAR = {
    "discovery": "pass",
    "replace_by_file": "pass",
    "schema_merge": "pass",
    "sql_endpoint": "pass",
    "volume": "pass",
}


class Evidence:
    """A complete synthetic evidence folder and the helpers to doctor one file of it."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.evidence = root / "target" / "verify-fabric" / "final"
        self.live = root / "target" / "fabric-live"
        for build in verify_fabric.BUILDS:
            self.write(f"build-{build}.json", build_record(build))
        self.write("self-hosted.json", self_hosted_record())
        self.write("parity.json", parity_record())
        self.write("volume.json", volume_record())
        self.write("known-limits.json", {})
        (self.evidence / "commit.txt").write_text(SHA + "\n", encoding="utf-8")
        self.write("native-first-run.json", native_first_run(), live=True)
        self.write(
            "sink-spike.json", {"decision": "delta-rs", "pass_bar": SINK_PASS_BAR}, live=True
        )
        self.write("fallback-sink.json", {"triggered": False, "decision": "delta-rs"}, live=True)
        self.write("vendoring.json", {"triggered": False}, live=True)
        self.write(
            "ci.json", {"run_id": 123456, "conclusion": "success", "headSha": SHA}, live=True
        )

    def path(self, name: str, *, live: bool = False) -> Path:
        return (self.live if live else self.evidence) / name

    def write(self, name: str, data: Any, *, live: bool = False) -> None:
        target = self.path(name, live=live)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def read(self, name: str, *, live: bool = False) -> Any:
        return json.loads(self.path(name, live=live).read_text(encoding="utf-8"))

    def edit(self, name: str, change: Callable[[Any], None], *, live: bool = False) -> None:
        data = self.read(name, live=live)
        change(data)
        self.write(name, data, live=live)

    def remove(self, name: str, *, live: bool = False) -> None:
        self.path(name, live=live).unlink()

    def checks(self) -> dict[str, dict[str, Any]]:
        limits = self.read("known-limits.json") if self.path("known-limits.json").exists() else {}
        return fabric_record.derive_checks(self.evidence, self.live, limits)

    def render(self, *, draft: bool = False) -> str:
        evidence = fabric_record.assemble(self.evidence, self.live, DATE, draft=draft)
        return fabric_record.render_record(evidence)


@pytest.fixture
def folder(tmp_path: Path) -> Evidence:
    return Evidence(tmp_path)


# ---------------------------------------------------------------------------------------------
# Tracer: a complete evidence folder becomes a dated record


def test_a_complete_folder_derives_all_fourteen_checks_as_publishable(folder: Evidence) -> None:
    checks = folder.checks()
    assert sorted(checks) == sorted(fabric_record.LIVE_CHECKS)
    assert len(checks) == 14
    for name, entry in checks.items():
        assert sorted(entry) == ["evidence", "excluded", "outcome"]
        assert entry["outcome"] in fabric_record.CHECK_OUTCOMES, name
        assert ":" in entry["evidence"], name
        assert entry["excluded"] == []
    assert {entry["outcome"] for entry in checks.values()} == {"pass", "documented-fallback"}
    assert checks["job-rest-run"]["outcome"] == "documented-fallback"


def test_job_rest_run_is_pass_when_the_rest_start_worked(folder: Evidence) -> None:
    folder.edit(
        "native-first-run.json", lambda d: d["job_rest_run"].update(outcome="pass"), live=True
    )
    assert folder.checks()["job-rest-run"]["outcome"] == "pass"


def test_the_rendered_record_holds_every_d12_item(folder: Evidence) -> None:
    text = folder.render()
    assert text.startswith(f"# Fabric verification, {DATE}\n")
    for heading in (
        "## Versions",
        "## Run modes",
        "## Builds",
        "## Row counts on the fixture",
        "## 2024-01",
        "## Conversion fixture",
        "## Live checks",
        "## Compatibility matrix",
        "## Known limits",
    ):
        assert heading in text, heading
    for name in fabric_record.LIVE_CHECKS:
        assert f"| {name} |" in text, name
    # Versions, read from the evidence and nowhere else.
    assert "dbt Core (the native job's patch): 1.11.8" in text
    assert "dbt-fabricspark: 1.12.2" in text
    assert "Fabric runtime: 2.0" in text
    assert "Spark: 4.1.1" in text
    # Both run modes, the sink, the commit and the CI run id.
    assert "`build-fabric --selector fabric_fixture`" in text
    assert "`demo --target fabric --months 2024-01`" in text
    assert "started in the portal" in text
    assert "landing sink `delta-rs`" in text
    assert f"commit `{SHA}`" in text
    assert "run `123456`" in text
    # The four builds and the self-hosted build, with the results source of each native build.
    for label in ("build-1", "build-2", "build-3", "build-4", "self-hosted"):
        assert f"| {label} |" in text
    assert "| build-3 | on | Completed | hook |" in text
    assert "| self-hosted | off | exit 0 | run_results.json |" in text
    # The 2024-01 section states its scope.
    assert "Scope: counts per layer and entity and the per-entity and worst-file quarantine" in text
    # The matrix.
    assert "dbt Core 1.11 (the job's patch 1.11.8)" in text
    assert "Fabric Runtime 1.3 (Spark 3.5)" in text
    assert "not verified live; end of support announced" in text
    assert "CI sign-in with a client secret" in text and "not run live" in text
    # No known limit declared.
    assert text.split("## Known limits\n\n", 1)[1].startswith("none\n")


def test_layers_list_in_landing_bronze_silver_quarantine_gold_order(folder: Evidence) -> None:
    text = folder.render()
    order = [
        "landing.entity_a",
        "bronze.entity_a",
        "silver.entity_a |",
        "silver.entity_a_quarantine",
        "gold.dim_a",
        "gold.fct",
    ]
    positions = [text.index(f"| {relation}") for relation in order]
    assert positions == sorted(positions)


def test_a_zero_count_renders_as_zero(folder: Evidence) -> None:
    text = folder.render()
    assert "| landing.entity_a | 0 | 0 | yes |" in text


def test_write_record_creates_the_file_and_a_second_write_is_refused(
    folder: Evidence, tmp_path: Path
) -> None:
    target = tmp_path / "out" / f"fabric-{DATE}.md"
    text = folder.render()
    fabric_record.write_record(target, text)
    assert target.read_text(encoding="utf-8") == text
    before = target.read_bytes()
    with pytest.raises(fabric_record.RecordRefused, match="already exists"):
        fabric_record.write_record(target, "a different record\n")
    assert target.read_bytes() == before


def test_publishable_is_pass_and_the_job_rest_fallback_only() -> None:
    def entry(outcome: str, excluded: list[str] | None = None) -> dict[str, Any]:
        return {"outcome": outcome, "evidence": "f.json:k", "excluded": excluded or []}

    assert fabric_record.publishable("schema-layout", entry("pass"), {})
    assert fabric_record.publishable("job-rest-run", entry("documented-fallback"), {})
    assert not fabric_record.publishable("schema-layout", entry("documented-fallback"), {})
    for outcome in ("fail", "missing"):
        assert not fabric_record.publishable("job-rest-run", entry(outcome), {})
    # excluded needs a node check and a declared node with an upstream issue link.
    limits = {
        "model.proj.a": {"issue": ISSUE, "reason": "upstream bug", "descendants": ["model.proj.b"]}
    }
    assert fabric_record.publishable("contracts-live", entry("excluded", ["model.proj.b"]), limits)
    assert not fabric_record.publishable(
        "schema-layout", entry("excluded", ["model.proj.b"]), limits
    )
    assert not fabric_record.publishable(
        "contracts-live", entry("excluded", ["model.proj.z"]), limits
    )
    assert not fabric_record.publishable("contracts-live", entry("excluded"), limits)


# ---------------------------------------------------------------------------------------------
# The commands: checks and record, from evidence files only


def _args(folder: Evidence, **extra: Any) -> argparse.Namespace:
    return argparse.Namespace(label="final", **extra)


@pytest.fixture
def seams(folder: Evidence, monkeypatch: pytest.MonkeyPatch) -> Evidence:
    monkeypatch.setattr(verify_fabric, "EVIDENCE_ROOT", lambda: folder.evidence.parent)
    monkeypatch.setattr(verify_fabric, "LIVE_DIR", lambda root: folder.live)
    monkeypatch.setattr(verify_fabric, "repo_root", lambda: folder.root)
    monkeypatch.setattr(
        fabric_record.build_fabric, "declared_known_limits", lambda root: {}, raising=True
    )
    return folder


def test_checks_writes_known_limits_and_checks_json(seams: Evidence) -> None:
    seams.remove("known-limits.json")
    code = verify_fabric.cmd_checks(_args(seams), {}, seams.root)
    assert code == 0
    assert seams.read("known-limits.json") == {}
    checks = seams.read("checks.json")
    assert sorted(checks) == sorted(fabric_record.LIVE_CHECKS)
    assert checks["seed-load"]["outcome"] == "pass"


def test_checks_merges_the_authors_approval_into_each_declared_node(
    seams: Evidence, monkeypatch: pytest.MonkeyPatch
) -> None:
    declared = {
        "model.proj.a": {
            "resource_type": "model",
            "issue": ISSUE,
            "reason": "an upstream bug",
            "descendants": ["model.proj.b"],
        },
        "model.proj.c": {
            "resource_type": "model",
            "issue": ISSUE,
            "reason": "another upstream bug",
            "descendants": [],
        },
    }
    monkeypatch.setattr(fabric_record.build_fabric, "declared_known_limits", lambda root: declared)
    seams.write(
        "known-limit-approvals.json",
        {
            "model.proj.a": {"approved_by_author": True, "approved_on": "2026-11-30"},
            "model.proj.not_declared": {"approved_by_author": True, "approved_on": "2026-11-30"},
        },
        live=True,
    )
    verify_fabric.cmd_checks(_args(seams), {}, seams.root)
    limits = seams.read("known-limits.json")
    assert sorted(limits) == ["model.proj.a", "model.proj.c"]
    assert limits["model.proj.a"]["approved_by_author"] is True
    assert limits["model.proj.a"]["approved_on"] == "2026-11-30"
    assert limits["model.proj.c"]["approved_by_author"] is False
    assert limits["model.proj.c"]["approved_on"] is None
    assert sorted(limits["model.proj.a"]) == [
        "approved_by_author",
        "approved_on",
        "descendants",
        "issue",
        "reason",
        "resource_type",
    ]


def test_record_writes_fabric_date_md_and_prints_its_path(
    seams: Evidence, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _args(seams, date=DATE, out_dir="target/records", draft=True)
    assert verify_fabric.cmd_record(args, {}, seams.root) == 0
    written = seams.root / "target" / "records" / f"fabric-{DATE}.md"
    assert written.exists()
    assert f"fabric-{DATE}.md" in capsys.readouterr().out
    with pytest.raises(fabric_record.RecordRefused, match="already exists"):
        verify_fabric.cmd_record(args, {}, seams.root)


def test_main_runs_checks_and_record_with_no_fabric_variable_set(
    seams: Evidence, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in [name for name in os.environ if name.startswith("FABRIC_")]:
        monkeypatch.delenv(name)
    assert verify_fabric.main(["checks", "--label", "final"]) == 0
    code = verify_fabric.main(
        ["record", "--label", "final", "--date", DATE, "--out-dir", "target/rec", "--draft"]
    )
    assert code == 0
    assert (seams.root / "target" / "rec" / f"fabric-{DATE}.md").exists()

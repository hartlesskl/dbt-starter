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
        node("model.proj.bronze__sample__yellow_trips", "success"),
        node("model.proj.silver__yellow_trips", "success"),
        node(
            "test.proj.yellow_trips_undeclared_landing_columns.abc123", "warn", undeclared_failures
        ),
        node("test.proj.zones_undeclared_landing_columns.def456", "pass", 0),
        node("test.starter.shim_conversion_fixture", "pass", 0),
        node("test.proj.some_other_test.789abc", "pass", 0),
    ]
    nodes += [
        node(f"unit_test.proj.bronze__sample__yellow_trips.{n}", "pass", 0) for n in BRONZE_UNITS
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
            "unique_id": "model.proj.bronze__sample__yellow_trips",
            "status": "success",
            "execution_time": 3.0,
        },
        "unit_tests": [
            {"unique_id": f"unit_test.proj.bronze__sample__yellow_trips.{n}", "status": "pass"}
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

    def build(self, *, head: str = SHA, draft: bool = False) -> str:
        """What `record` does: assemble, refuse what the evidence does not prove, render."""
        evidence = fabric_record.assemble(self.evidence, self.live, DATE, draft=draft)
        if not draft:
            fabric_record.check_record(evidence, head)
        return fabric_record.render_record(evidence)

    def declare(
        self,
        unique_id: str,
        descendants: tuple[str, ...] = (),
        *,
        approved: bool = True,
        **overrides: Any,
    ) -> None:
        """Declare a known limit and take it, and its descendants, out of every run."""
        entry = {
            "resource_type": unique_id.split(".", 1)[0],
            "issue": ISSUE,
            "reason": "an upstream adapter bug with no workaround",
            "descendants": sorted(descendants),
            "approved_by_author": approved,
            "approved_on": "2026-11-30" if approved else None,
        }
        entry.update(overrides)

        def add(limits: dict[str, Any]) -> None:
            limits[unique_id] = entry

        self.edit("known-limits.json", add)
        self.drop((unique_id, *descendants))

    def drop(self, unique_ids: tuple[str, ...]) -> None:
        """Remove nodes from every build and the self-hosted build, as an excluded node is."""

        def remove(record: dict[str, Any]) -> None:
            record["nodes"] = [n for n in record["nodes"] if n["unique_id"] not in unique_ids]
            record["status_counts"] = counts(record["nodes"])

        for build in verify_fabric.BUILDS:
            self.edit(f"build-{build}.json", remove)
        self.edit("self-hosted.json", remove)

    def set_status(self, name: str, unique_id: str, status: str) -> None:
        def change(record: dict[str, Any]) -> None:
            for item in record["nodes"]:
                if item["unique_id"] == unique_id:
                    item["status"] = status

        self.edit(name, change)


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


# ---------------------------------------------------------------------------------------------
# Derivation: each check from its pinned keys


def test_a_check_with_no_evidence_is_missing_never_pass(folder: Evidence) -> None:
    folder.remove("native-first-run.json", live=True)
    checks = folder.checks()
    assert checks["native-job-profile-schema"]["outcome"] == "missing"
    assert checks["job-rest-run"]["outcome"] == "missing"


def test_native_job_deps_is_missing_when_build_one_has_no_invocation(folder: Evidence) -> None:
    folder.edit("build-1.json", lambda d: d.update(invocation=None))
    assert folder.checks()["native-job-deps"]["outcome"] == "missing"


@pytest.mark.parametrize(
    ("change", "outcome"),
    [
        (lambda d: d["schema_tables"].update(dbo=1), "fail"),
        (lambda d: d["schema_tables"].update(gold=0), "fail"),
        (lambda d: d.pop("schema_tables"), "missing"),
    ],
)
def test_schema_layout_needs_three_layers_above_zero_and_nothing_in_dbo(
    folder: Evidence, change: Callable[[Any], None], outcome: str
) -> None:
    folder.edit("parity.json", change)
    assert folder.checks()["schema-layout"]["outcome"] == outcome


def test_session_time_zone_is_fail_off_utc_and_missing_when_a_build_carries_null(
    folder: Evidence,
) -> None:
    folder.edit("build-2.json", lambda d: d["invocation"].update(session_time_zone=None))
    assert folder.checks()["session-time-zone"]["outcome"] == "missing"
    folder.edit("build-3.json", lambda d: d["invocation"].update(session_time_zone="Asia/Tokyo"))
    assert folder.checks()["session-time-zone"]["outcome"] == "fail"


def test_undeclared_columns_must_sum_to_1_1_1_2_across_the_four_builds(folder: Evidence) -> None:
    def fewer(record: dict[str, Any]) -> None:
        for item in record["nodes"]:
            if "yellow_trips_undeclared" in item["unique_id"]:
                item["failures"] = 1

    folder.edit("build-4.json", fewer)
    assert folder.checks()["undeclared-columns-live"]["outcome"] == "fail"


def test_bronze_plan_cost_reads_volume_json_and_is_missing_without_it(folder: Evidence) -> None:
    folder.edit("volume.json", lambda d: d["bronze_trips"].update(status="fail"))
    assert folder.checks()["bronze-plan-cost"]["outcome"] == "fail"
    folder.remove("volume.json")
    assert folder.checks()["bronze-plan-cost"]["outcome"] == "missing"


def test_seed_load_counts_six_seeds_each_success_in_builds_one_and_two(folder: Evidence) -> None:
    folder.drop(("seed.proj.seed_0",))
    assert folder.checks()["seed-load"]["outcome"] == "fail"


def test_unit_tests_live_fails_on_one_unit_test_not_passing_in_build_one(folder: Evidence) -> None:
    folder.set_status("build-1.json", "unit_test.proj.silver__yellow_trips.silver_rule", "fail")
    assert folder.checks()["unit-tests-live"]["outcome"] == "fail"


def test_conversion_fixture_and_contracts_cover_the_self_hosted_build_too(folder: Evidence) -> None:
    folder.set_status("self-hosted.json", "test.starter.shim_conversion_fixture", "fail")
    folder.set_status("self-hosted.json", "model.proj.gold__fct", "error")
    checks = folder.checks()
    assert checks["conversion-fixture-spark"]["outcome"] == "fail"
    assert checks["contracts-live"]["outcome"] == "fail"


def test_job_rest_run_fallback_needs_a_recorded_status_and_error_code(folder: Evidence) -> None:
    folder.edit(
        "native-first-run.json", lambda d: d["job_rest_run"].update(http_status=200), live=True
    )
    assert folder.checks()["job-rest-run"]["outcome"] == "fail"
    folder.edit(
        "native-first-run.json", lambda d: d["job_rest_run"].update(outcome="fail"), live=True
    )
    assert folder.checks()["job-rest-run"]["outcome"] == "fail"


def test_sink_checks_read_the_fallback_when_it_was_triggered(folder: Evidence) -> None:
    same = {"a": 1, "b": 2}
    folder.write(
        "fallback-sink.json",
        {
            "triggered": True,
            "decision": "fallback",
            "record": {
                "duckdb_counts": same,
                "lakehouse_counts_first": same,
                "lakehouse_counts_second": same,
            },
            "sql_endpoint": {"outcome": "pass"},
        },
        live=True,
    )
    folder.edit(
        "sink-spike.json",
        lambda d: d["pass_bar"].update(discovery="fail", sql_endpoint="fail"),
        live=True,
    )
    checks = folder.checks()
    assert checks["sink-discovery"] == {
        "outcome": "pass",
        "evidence": "fallback-sink.json:record.lakehouse_counts_second",
        "excluded": [],
    }
    assert checks["sql-endpoint-read"]["outcome"] == "pass"
    assert "landing sink `fallback`" in folder.render()
    folder.edit(
        "fallback-sink.json",
        lambda d: d["record"].update(lakehouse_counts_second={"a": 1, "b": 3}),
        live=True,
    )
    assert folder.checks()["sink-discovery"]["outcome"] == "fail"


def test_sink_checks_read_the_spike_when_the_fallback_was_not_triggered(folder: Evidence) -> None:
    folder.edit("sink-spike.json", lambda d: d["pass_bar"].update(sql_endpoint="fail"), live=True)
    checks = folder.checks()
    assert checks["sink-discovery"]["outcome"] == "pass"
    assert checks["sql-endpoint-read"]["outcome"] == "fail"


def test_oidc_live_is_bound_to_the_commit_the_record_describes(folder: Evidence) -> None:
    assert folder.checks()["oidc-live"]["outcome"] == "pass"
    folder.edit("ci.json", lambda d: d.update(headSha=OTHER_SHA), live=True)
    assert folder.checks()["oidc-live"]["outcome"] == "fail"
    folder.edit("ci.json", lambda d: d.update(headSha=SHA, conclusion="failure"), live=True)
    assert folder.checks()["oidc-live"]["outcome"] == "fail"
    folder.edit("ci.json", lambda d: d.update(conclusion="success"), live=True)
    folder.remove("commit.txt")
    assert folder.checks()["oidc-live"]["outcome"] == "missing"
    folder.path("commit.txt").write_text(SHA, encoding="utf-8")
    folder.remove("ci.json", live=True)
    assert folder.checks()["oidc-live"]["outcome"] == "missing"


# ---------------------------------------------------------------------------------------------
# Known limits: a node excluded from the run, listed with its issue and approval date (D-14)

GOLD_DIM_E = "model.proj.gold__dim_e"
SILVER = "model.proj.silver__yellow_trips"


def test_one_gold_model_declared_gives_a_pass_with_it_excluded_and_listed(folder: Evidence) -> None:
    folder.declare(GOLD_DIM_E)
    entry = folder.checks()["contracts-live"]
    assert entry["outcome"] == "pass"
    assert entry["excluded"] == [GOLD_DIM_E]
    assert fabric_record.publishable("contracts-live", entry, folder.read("known-limits.json"))
    text = folder.build()
    assert f"- `{GOLD_DIM_E}` (model): issue {ISSUE}." in text
    assert "approved by the author on 2026-11-30" in text
    assert "Excluded with it: none" in text


def test_a_declared_silver_model_excludes_its_gold_descendants_with_it(folder: Evidence) -> None:
    descendants = ("model.proj.gold__fct", "model.proj.gold__dim_d")
    folder.declare(
        SILVER,
        descendants,
    )
    folder.drop(("unit_test.proj.silver__yellow_trips.silver_rule",))
    entry = folder.checks()["contracts-live"]
    assert entry["outcome"] == "pass"
    assert entry["excluded"] == sorted(descendants)
    text = folder.build()
    assert "Excluded with it: `model.proj.gold__dim_d`, `model.proj.gold__fct`" in text


def test_a_check_whose_whole_subject_is_declared_is_excluded_and_the_record_is_written(
    folder: Evidence,
) -> None:
    units = tuple(f"unit_test.proj.bronze__sample__yellow_trips.{n}" for n in BRONZE_UNITS)
    folder.declare("model.proj.bronze__sample__yellow_trips", units)
    checks = folder.checks()
    assert checks["bronze-plan-cost"]["outcome"] == "excluded"
    assert checks["bronze-plan-cost"]["excluded"] == sorted(
        ("model.proj.bronze__sample__yellow_trips", *units)
    )
    limits = folder.read("known-limits.json")
    assert fabric_record.publishable("bronze-plan-cost", checks["bronze-plan-cost"], limits)
    assert "| bronze-plan-cost | excluded |" in folder.build()


def test_a_genuine_failure_among_the_nodes_that_ran_is_fail_not_excluded(folder: Evidence) -> None:
    folder.declare(GOLD_DIM_E)
    folder.set_status("build-2.json", "model.proj.gold__fct", "error")
    assert folder.checks()["contracts-live"]["outcome"] == "fail"


def test_the_excluded_outcome_is_not_publishable_without_a_declared_issue(folder: Evidence) -> None:
    entry = {"outcome": "excluded", "evidence": "f:k", "excluded": [GOLD_DIM_E]}
    bad = {GOLD_DIM_E: {"issue": "http://example.com/1", "reason": "r", "descendants": []}}
    assert not fabric_record.publishable("contracts-live", entry, bad)
    assert not fabric_record.publishable("contracts-live", entry, {})


# ---------------------------------------------------------------------------------------------
# Refusals (D-08, D-12 to D-14): each doctored input must raise, and the message names the item


def refused(folder: Evidence, *, head: str = SHA) -> str:
    with pytest.raises(fabric_record.RecordRefused) as caught:
        folder.build(head=head)
    return str(caught.value)


def test_the_complete_synthetic_folder_is_not_refused(folder: Evidence) -> None:
    assert folder.build().startswith(f"# Fabric verification, {DATE}")


@pytest.mark.parametrize(
    "name",
    [
        "build-1.json",
        "build-2.json",
        "build-3.json",
        "build-4.json",
        "self-hosted.json",
        "parity.json",
        "volume.json",
        "known-limits.json",
        "commit.txt",
    ],
)
def test_a_missing_label_file_is_refused_by_name(folder: Evidence, name: str) -> None:
    folder.remove(name)
    assert f"{name} is missing" in refused(folder)


def test_a_missing_ci_json_is_refused_naming_oidc_live(folder: Evidence) -> None:
    folder.remove("ci.json", live=True)
    message = refused(folder)
    assert "ci.json is missing" in message and "oidc-live" in message


@pytest.mark.parametrize(
    ("name", "check"),
    [
        ("native-first-run.json", "native-job-profile-schema"),
        ("native-first-run.json", "job-rest-run"),
        ("sink-spike.json", "sink-discovery"),
        ("sink-spike.json", "sql-endpoint-read"),
    ],
)
def test_a_missing_check_source_file_is_refused_naming_the_check(
    folder: Evidence, name: str, check: str
) -> None:
    folder.remove(name, live=True)
    assert f"check {check} is missing its evidence" in refused(folder)


@pytest.mark.parametrize("status", ["fail", "error", "skipped", "unknown"])
def test_any_node_outside_the_ok_statuses_refuses_the_record(folder: Evidence, status: str) -> None:
    folder.set_status("build-2.json", "test.proj.some_other_test.789abc", status)
    assert "node test.proj.some_other_test.789abc is not in an ok status in build-2" in refused(
        folder
    )


def test_a_failing_node_in_the_self_hosted_build_refuses_the_record(folder: Evidence) -> None:
    folder.set_status("self-hosted.json", "test.proj.some_other_test.789abc", "fail")
    assert "in self-hosted" in refused(folder)


def test_a_failing_node_refuses_the_record_also_when_it_is_declared(folder: Evidence) -> None:
    folder.edit(
        "known-limits.json",
        lambda d: d.update(
            {
                "test.proj.some_other_test.789abc": {
                    "resource_type": "test",
                    "issue": ISSUE,
                    "reason": "an upstream bug",
                    "descendants": [],
                    "approved_by_author": True,
                    "approved_on": "2026-11-30",
                }
            }
        ),
    )
    folder.set_status("build-1.json", "test.proj.some_other_test.789abc", "fail")
    message = refused(folder)
    assert "test.proj.some_other_test.789abc" in message


def test_a_declared_node_that_ran_is_refused(folder: Evidence) -> None:
    folder.declare(GOLD_DIM_E)
    folder.edit("build-3.json", lambda d: d["nodes"].append(node(GOLD_DIM_E, "success")))
    assert f"node {GOLD_DIM_E} is declared as excluded but it ran" in refused(folder)


def test_a_descendant_of_a_declared_node_that_ran_is_refused(folder: Evidence) -> None:
    folder.declare(SILVER, ("model.proj.gold__fct",))
    folder.edit(
        "build-1.json", lambda d: d["nodes"].append(node("model.proj.gold__fct", "success"))
    )
    assert (
        f"node model.proj.gold__fct is excluded as a descendant of {SILVER} but it ran"
        in refused(folder)
    )


def test_a_full_refresh_that_differs_from_the_expected_state_is_refused(folder: Evidence) -> None:
    folder.edit("build-4.json", lambda d: d["invocation"].update(full_refresh=True))
    assert "build-4 ran with a full refresh state that differs from the expected one" in refused(
        folder
    )


def test_a_full_refresh_expectation_outside_the_sequence_is_refused(folder: Evidence) -> None:
    folder.edit("build-4.json", lambda d: d.update(full_refresh_expected=True))
    folder.edit("build-4.json", lambda d: d["invocation"].update(full_refresh=True))
    assert "build-4 does not expect the sequence's full refresh state" in refused(folder)


@pytest.mark.parametrize(
    "overrides",
    [{"issue": "http://example.com/issues/1"}, {"reason": ""}, {"reason": "one\ntwo"}],
)
def test_a_declared_node_without_a_valid_issue_and_reason_is_refused(
    folder: Evidence, overrides: dict[str, Any]
) -> None:
    folder.declare(GOLD_DIM_E, **overrides)
    assert f"declared known limit {GOLD_DIM_E} has no valid issue link or reason" in refused(folder)


@pytest.mark.parametrize(
    "overrides",
    [
        {"approved_by_author": False, "approved_on": None},
        {"approved_on": None},
        {"approved_on": "2026-13-45"},
        {"approved_on": "yesterday"},
    ],
)
def test_a_known_limit_without_the_authors_approval_is_refused(
    folder: Evidence, overrides: dict[str, Any]
) -> None:
    folder.declare(GOLD_DIM_E, **overrides)
    assert f"known limit {GOLD_DIM_E} was excluded without the author's approval" in refused(folder)


def test_a_draft_does_not_refuse_an_unapproved_known_limit(folder: Evidence) -> None:
    folder.declare(GOLD_DIM_E, approved=False)
    assert "not yet approved" in folder.build(draft=True)


@pytest.mark.parametrize(
    ("name", "change", "check"),
    [
        ("parity.json", lambda d: d["schema_tables"].update(dbo=3), "schema-layout"),
        ("build-1.json", lambda d: d.update(invocation=None), "native-job-deps"),
        ("ci.json", lambda d: d.update(conclusion="failure"), "oidc-live"),
        ("ci.json", lambda d: d.update(headSha=OTHER_SHA), "oidc-live"),
        (
            "native-first-run.json",
            lambda d: d["job_rest_run"].update(outcome="fail"),
            "job-rest-run",
        ),
        ("volume.json", lambda d: d["bronze_trips"].update(status="fail"), "bronze-plan-cost"),
    ],
)
def test_a_check_that_is_not_publishable_refuses_the_record_naming_it(
    folder: Evidence, name: str, change: Callable[[Any], None], check: str
) -> None:
    folder.edit(name, change, live=name in ("ci.json", "native-first-run.json"))
    message = refused(folder)
    assert f"check {check} is not publishable" in message or (
        f"check {check} is missing its evidence" in message
    )


def test_an_oidc_run_on_another_commit_never_settles_oidc_live(folder: Evidence) -> None:
    folder.edit("ci.json", lambda d: d.update(headSha=OTHER_SHA), live=True)
    assert "check oidc-live is not publishable (fail)" in refused(folder)
    folder.edit("ci.json", lambda d: d.update(headSha=SHA), live=True)
    folder.remove("commit.txt")
    assert "check oidc-live is missing its evidence" in refused(folder)


def test_documented_fallback_is_written_and_the_run_modes_say_portal(folder: Evidence) -> None:
    text = folder.build()
    assert (
        "started in the portal (the REST start was refused with HTTP 400, InvalidJobType)" in text
    )
    assert "| job-rest-run | documented-fallback |" in text


@pytest.mark.parametrize("results_source", [None, "download", ""])
def test_a_native_build_with_no_hook_or_log_results_source_is_refused(
    folder: Evidence, results_source: str | None
) -> None:
    folder.edit("build-2.json", lambda d: d.update(results_source=results_source))
    assert "build-2.json has no results source of hook or log" in refused(folder)


def test_a_log_build_has_no_session_zone_so_session_time_zone_is_missing_and_refused(
    folder: Evidence,
) -> None:
    def from_the_log(record: dict[str, Any]) -> None:
        record["results_source"] = "log"
        record["invocation"].update(engine_version=None, session_time_zone=None)

    folder.edit("build-2.json", from_the_log)
    assert folder.checks()["session-time-zone"]["outcome"] == "missing"
    assert "check session-time-zone is missing its evidence" in refused(folder)


def test_the_builds_table_names_hook_or_log_for_each_native_build(folder: Evidence) -> None:
    folder.edit("build-2.json", lambda d: d.update(results_source="log"))
    folder.edit("build-2.json", lambda d: d["invocation"].update(session_time_zone="UTC"))
    text = folder.build()
    assert "| build-1 | off | Completed | hook |" in text
    assert "| build-2 | off | Completed | log |" in text


@pytest.mark.parametrize(
    ("name", "change", "item"),
    [
        ("build-3.json", lambda d: d.update(job_status="Failed"), "build-3 job did not complete"),
        ("self-hosted.json", lambda d: d.update(exit_code=1), "self-hosted build exited non-zero"),
        ("parity.json", lambda d: d.update(all_equal=False), "parity differs"),
        (
            "parity.json",
            lambda d: d["relations"][1].update(fabric=99),
            "parity differs for bronze.entity_a",
        ),
        ("volume.json", lambda d: d.update(fabric_exit_code=1), "volume run exited non-zero"),
        ("volume.json", lambda d: d.update(duckdb_exit_code=2), "volume run exited non-zero"),
        (
            "volume.json",
            lambda d: d.update(fabric_failed_nodes=["test.proj.some_other_test.789abc"]),
            "volume run has a failing node",
        ),
        ("volume.json", lambda d: d.update(counts_all_equal=False), "volume counts differ"),
        ("volume.json", lambda d: d.update(rates_all_equal=False), "volume rates differ"),
    ],
)
def test_a_job_self_hosted_parity_or_volume_failure_refuses_the_record(
    folder: Evidence, name: str, change: Callable[[Any], None], item: str
) -> None:
    folder.edit(name, change)
    assert item in refused(folder)


def test_a_commit_txt_that_differs_from_head_is_refused(folder: Evidence) -> None:
    message = refused(folder, head=OTHER_SHA)
    assert "commit.txt differs from HEAD" in message
    assert SHA not in message and OTHER_SHA not in message


def test_a_draft_renders_missing_evidence_as_pending_and_never_refuses_on_it(
    folder: Evidence,
) -> None:
    for name in ("volume.json", "known-limits.json", "commit.txt", "build-3.json"):
        folder.remove(name)
    folder.remove("ci.json", live=True)
    text = folder.build(draft=True)
    assert "Draft rendered from rehearsal evidence" in text
    assert "| bronze-plan-cost | pending |" in text
    assert "| oidc-live | pending |" in text
    assert "| build-3 | pending |" in text
    assert "commit `pending`" in text


def test_a_draft_shows_an_unpublishable_check_with_its_outcome(folder: Evidence) -> None:
    folder.edit("parity.json", lambda d: d["schema_tables"].update(dbo=3))
    assert "| schema-layout | fail |" in folder.build(draft=True)


def test_a_draft_out_dir_outside_target_is_refused_before_anything_is_written(
    seams: Evidence,
) -> None:
    args = _args(seams, date=DATE, out_dir="docs/verification", draft=True)
    with pytest.raises(fabric_record.RecordRefused, match="under target/"):
        verify_fabric.cmd_record(args, {}, seams.root)
    assert not (seams.root / "docs").exists()


def test_a_refused_record_writes_nothing(seams: Evidence, monkeypatch: pytest.MonkeyPatch) -> None:
    seams.edit("parity.json", lambda d: d["schema_tables"].update(dbo=3))
    monkeypatch.setattr(verify_fabric, "HEAD_SHA", lambda root: SHA)
    args = _args(seams, date=DATE, out_dir="docs/verification", draft=False)
    with pytest.raises(fabric_record.RecordRefused):
        verify_fabric.cmd_record(args, {}, seams.root)
    assert not (seams.root / "docs").exists()


def test_record_writes_to_docs_verification_by_default_and_never_overwrites(
    seams: Evidence, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(verify_fabric, "HEAD_SHA", lambda root: SHA)
    args = _args(seams, date=DATE, out_dir="docs/verification", draft=False)
    assert verify_fabric.cmd_record(args, {}, seams.root) == 0
    written = seams.root / "docs" / "verification" / f"fabric-{DATE}.md"
    before = written.read_bytes()
    with pytest.raises(fabric_record.RecordRefused, match="already exists"):
        verify_fabric.cmd_record(args, {}, seams.root)
    assert written.read_bytes() == before


# ---------------------------------------------------------------------------------------------
# Values: every interpolated value matches a pattern, and a message names the field, never the value

UUID = "12345678-aaaa-bbbb-cccc-1234567890ab"


@pytest.mark.parametrize(
    ("name", "change", "field_name"),
    [
        ("build-1.json", lambda d: d["invocation"].update(dbt_version=UUID), "dbt_version"),
        (
            "build-1.json",
            lambda d: d["invocation"].update(dbt_version="1.11.8 admin@contoso.onmicrosoft.com"),
            "dbt_version",
        ),
        ("build-1.json", lambda d: d.update(job_status="https://tenant.example/x"), "job_status"),
        ("parity.json", lambda d: d["relations"][0].update(relation="Bad Name!"), "relation"),
        ("volume.json", lambda d: d.update(month="last month"), "month"),
    ],
)
def test_a_value_outside_its_pattern_is_refused_by_field_not_by_value(
    folder: Evidence, name: str, change: Callable[[Any], None], field_name: str
) -> None:
    folder.edit(name, change)
    with pytest.raises(fabric_record.RecordRefused) as caught:
        folder.render()
    message = str(caught.value)
    assert field_name in message
    for fragment in (UUID, "contoso", "tenant.example", "Bad Name"):
        assert fragment not in message


def test_a_profile_schema_outside_its_pattern_is_refused(folder: Evidence) -> None:
    folder.edit(
        "native-first-run.json", lambda d: d["profile"].update(schema=f"s {UUID}"), live=True
    )
    with pytest.raises(fabric_record.RecordRefused, match="profile.schema") as caught:
        folder.render()
    assert UUID not in str(caught.value)


@pytest.mark.parametrize(
    "reason",
    [f"bug {UUID}", "see someone@example.com", "tenant contoso.onmicrosoft.com is affected"],
)
def test_a_reason_that_carries_a_uuid_email_or_tenant_host_is_refused(
    folder: Evidence, reason: str
) -> None:
    folder.declare(GOLD_DIM_E, reason=reason)
    with pytest.raises(fabric_record.RecordRefused) as caught:
        folder.build()
    assert (
        "UUID-shaped" in str(caught.value)
        or "tenant" in str(caught.value)
        or "email" in str(caught.value)
    )
    assert UUID not in str(caught.value) and "contoso" not in str(caught.value)
    assert "someone@example.com" not in str(caught.value)


def test_a_reason_longer_than_two_hundred_characters_is_refused(folder: Evidence) -> None:
    folder.declare(GOLD_DIM_E, reason="x" * 201)
    with pytest.raises(fabric_record.RecordRefused, match="reason"):
        folder.build()


def test_the_final_sweep_refuses_uuid_shaped_text_anywhere_in_the_record(folder: Evidence) -> None:
    text = folder.render() + f"\n{UUID}\n"
    with pytest.raises(fabric_record.RecordRefused, match="UUID-shaped"):
        fabric_record.sweep(text)


def test_no_node_message_is_rendered(folder: Evidence) -> None:
    def add_message(record: dict[str, Any]) -> None:
        for item in record["nodes"]:
            item["message"] = f"relation owned by {UUID}"

    folder.edit("build-1.json", add_message)
    assert UUID not in folder.build()


# ---------------------------------------------------------------------------------------------
# Determinism and ordering


def test_rendering_the_same_evidence_twice_is_byte_identical(folder: Evidence) -> None:
    assert folder.render() == folder.render()
    assert folder.build() == folder.build()


def test_the_order_of_the_evidence_does_not_change_the_text(folder: Evidence) -> None:
    before = folder.render()
    folder.edit("parity.json", lambda d: d["relations"].reverse())
    folder.edit("volume.json", lambda d: d.update(unit_tests=list(reversed(d["unit_tests"]))))
    assert folder.render() == before


def test_volume_rates_compare_as_decimals_not_as_text(folder: Evidence) -> None:
    def spelled_differently(volume: dict[str, Any]) -> None:
        volume["duckdb"]["rates"]["entity_a"]["quarantine_rate"] = "0.01"

    folder.edit("volume.json", spelled_differently)
    assert folder.build().startswith("# Fabric verification")
    folder.edit(
        "volume.json",
        lambda d: d["duckdb"]["rates"]["entity_a"].update(quarantine_rate="0.010001"),
    )
    with pytest.raises(fabric_record.RecordRefused, match="volume rates differ"):
        folder.build()

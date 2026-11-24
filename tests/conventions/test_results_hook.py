"""The on-run-end results hook records every node of a build, only where its table exists.

D-10: the native dbt job exposes no `run_results.json`, so a hook writes each node and the
invocation into `verification.*`. The hook is opt-in by table existence (research Pattern 2):
without `verification.run_results` it writes nothing and creates no schema. These tests run dbt
as a subprocess against a temporary DuckDB file, then read the file back.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest
from support.dbt_cli import DUMMY_FABRIC_ENV, REPO_ROOT, require_fabric_adapter, run_dbt

SEED_BUILD = ["build", "--select", "resource_type:seed", "--target", "duckdb"]


def _build(database: Path, target_path: Path, args: list[str] = SEED_BUILD) -> None:
    run_dbt(
        [*args, "--target-path", str(target_path)],
        env={"DBT_DUCKDB_PATH": str(database)},
    )


def _rows(database: Path, sql: str) -> list[tuple]:
    connection = duckdb.connect(str(database), read_only=True)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


@pytest.mark.usefixtures("dbt_packages_ready")
def test_a_build_with_the_marker_table_records_every_result_and_the_invocation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "hook.duckdb"
    run_dbt(
        ["run-operation", "starter.create_verification_tables", "--target", "duckdb"],
        env={"DBT_DUCKDB_PATH": str(database)},
    )
    _build(database, tmp_path / "build")

    results = json.loads((tmp_path / "build" / "run_results.json").read_text())["results"]
    # run_results.json also lists the project hooks themselves (`operation.*`). The hook cannot
    # record its own result, which does not exist until it returns, so those are left out.
    expected = sorted(
        (item["unique_id"], item["status"], item["failures"])
        for item in results
        if not item["unique_id"].startswith("operation.")
    )
    assert expected, "the seed build ran no node, so the comparison would prove nothing"

    recorded = _rows(
        database,
        "select unique_id, status, failures from verification.run_results order by unique_id, status",
    )
    assert recorded == expected

    invocations = _rows(
        database,
        "select dbt_version, target_type, command, full_refresh, engine_version, "
        "session_time_zone from verification.invocations",
    )
    assert len(invocations) == 1, invocations
    dbt_version, target_type, command, full_refresh, engine_version, zone = invocations[0]
    assert dbt_version.startswith("1.11"), dbt_version
    assert (target_type, command, full_refresh) == ("duckdb", "build", False)
    assert engine_version, "the engine version was not recorded"
    assert zone, "the session time zone was not recorded"

    resource_types = {
        row[0]
        for row in _rows(database, "select distinct resource_type from verification.run_results")
    }
    assert resource_types == {"seed", "test"}, resource_types
    ids = {
        row[0]
        for row in _rows(database, "select distinct invocation_id from verification.run_results")
    }
    assert ids == {
        row[0] for row in _rows(database, "select invocation_id from verification.invocations")
    }


@pytest.mark.usefixtures("dbt_packages_ready")
def test_a_build_without_the_marker_table_creates_no_verification_schema(tmp_path: Path) -> None:
    database = tmp_path / "plain.duckdb"
    _build(database, tmp_path / "build")
    schemas = {
        row[0] for row in _rows(database, "select schema_name from information_schema.schemata")
    }
    assert "verification" not in schemas, schemas
    assert "bronze" in schemas, "the seed build did not run, so the check proved nothing"


@pytest.mark.usefixtures("dbt_packages_ready")
def test_string_literal_escapes_a_quote_and_a_backslash_per_engine() -> None:
    """DuckDB doubles a quote and keeps a backslash; Spark escapes both with a backslash."""
    inline = 'select {{ starter.string_literal("it\'s a \\\\ b") }} as v, {{ starter.string_literal(none) }} as n'
    duckdb_run = run_dbt(["compile", "--target", "duckdb", "--inline", inline])
    assert "select 'it''s a \\ b' as v, null as n" in duckdb_run.stdout

    require_fabric_adapter()
    fabric_run = run_dbt(
        ["compile", "--target", "fabric", "--no-populate-cache", "--inline", inline],
        env=DUMMY_FABRIC_ENV,
    )
    assert "select 'it\\'s a \\\\ b' as v, null as n" in fabric_run.stdout
    assert REPO_ROOT.is_dir()


@pytest.mark.usefixtures("dbt_packages_ready")
def test_a_compile_records_nothing_even_with_the_marker_table(tmp_path: Path) -> None:
    """A compile executes no node, and the gate's offline Fabric compile has no connection."""
    database = tmp_path / "compile.duckdb"
    run_dbt(
        ["run-operation", "starter.create_verification_tables", "--target", "duckdb"],
        env={"DBT_DUCKDB_PATH": str(database)},
    )
    _build(
        database,
        tmp_path / "compile",
        ["compile", "--select", "resource_type:seed", "--target", "duckdb"],
    )
    assert _rows(database, "select count(*) from verification.run_results") == [(0,)]
    assert _rows(database, "select count(*) from verification.invocations") == [(0,)]

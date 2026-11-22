"""The cross-entity data quality tables agree with the bronze flags (QUAR-02, QUAR-06, QUAR-07).

Three generic silver models read every registered entity through `dq_entities()`:
`silver.dq_rule_failures` (one row per failed rule and row key), `silver.dq_quarantine_summary`
(counts and rates by entity and rule) and `silver.dq_rule_catalog` (every rule and its data
problem). The checks here compare each table with the flag columns of the bronze tables on the
planted build, so the test keeps no rule list of its own.

Builds run on temporary DuckDB files chosen through `DBT_DUCKDB_PATH`; the demo database is never
written (T-02-03). Nothing here downloads anything.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import duckdb
import pytest
from support.dbt_cli import REPO_ROOT, run_dbt

# entity name in dq_entities() -> (bronze table, row key column). The two example entities.
BRONZE = {
    "taxi_zones": ("nyc_taxi__taxi_zones", "zone_key"),
    "yellow_trips": ("nyc_taxi__yellow_trips", "trip_key"),
}
DQ_TABLES = ("dq_rule_failures", "dq_quarantine_summary", "dq_rule_catalog")
DQ_MODELS = ("silver__dq_rule_failures", "silver__dq_quarantine_summary", "silver__dq_rule_catalog")
CATALOG_COLUMNS = ["code", "entity", "severity", "on_null", "description", "rationale"]
FAILURES_COLUMNS = ["entity", "row_key", "_source_file", "_batch_id", "rule_code", "severity"]
SUMMARY_COLUMNS = [
    "entity",
    "rule_code",
    "severity",
    "failed_rows",
    "failed_rate",
    "entity_rows",
    "entity_quarantined_rows",
    "entity_quarantine_rate",
    "entity_warned_rows",
    "entity_warned_rate",
]
EXAMPLE_MARKER = re.compile(r"^.*example:begin.*\n(.*\n)*?.*example:end.*\n", re.MULTILINE)

COPY_IGNORE = shutil.ignore_patterns(
    ".git",
    ".venv",
    ".claude",
    ".planning",
    "target",
    "data",
    "logs",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
)


def query(database: Path, sql: str) -> list[tuple]:
    con = duckdb.connect(str(database), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def scalar(database: Path, sql: str):
    return query(database, sql)[0][0]


def column_names(database: Path, schema: str, table: str) -> list[str]:
    rows = query(
        database,
        "select column_name from information_schema.columns "
        f"where table_schema = '{schema}' and table_name = '{table}' order by ordinal_position",
    )
    return [row[0] for row in rows]


def flag_columns(database: Path, entity: str) -> list[str]:
    """The `dq_fail_` columns of an entity's bronze table (the underscore is escaped)."""
    table, _ = BRONZE[entity]
    rows = query(
        database,
        "select column_name from information_schema.columns "
        f"where table_schema = 'bronze' and table_name = '{table}' "
        "and column_name like 'dq\\_fail\\_%' escape '\\'",
    )
    return sorted(row[0] for row in rows)


def catalog_rows(database: Path) -> list[tuple]:
    return query(
        database,
        "select code, entity, severity, on_null, description, rationale "
        "from silver.dq_rule_catalog order by entity, code",
    )


def test_the_catalog_holds_every_bronze_flag_of_every_entity(planted_trips_db) -> None:
    db = planted_trips_db.database
    rows = catalog_rows(db)
    assert {row[1] for row in rows} == set(BRONZE)
    for entity in BRONZE:
        from_catalog = sorted(f"dq_fail_{row[0].lower()}" for row in rows if row[1] == entity)
        assert from_catalog == flag_columns(db, entity), entity


def test_every_catalog_row_has_a_rationale_and_a_known_severity(planted_trips_db) -> None:
    rows = catalog_rows(planted_trips_db.database)
    assert rows
    for code, _entity, severity, on_null, description, rationale in rows:
        assert rationale and rationale.strip(), code
        assert description and description.strip(), code
        assert severity in ("error", "warn"), code
        assert on_null in ("pass", "fail"), code


def test_the_three_tables_have_the_documented_columns(planted_trips_db) -> None:
    db = planted_trips_db.database
    assert column_names(db, "silver", "dq_rule_catalog") == CATALOG_COLUMNS
    assert column_names(db, "silver", "dq_rule_failures") == FAILURES_COLUMNS
    assert column_names(db, "silver", "dq_quarantine_summary") == SUMMARY_COLUMNS


def test_failures_per_severity_equal_the_flag_sums_in_bronze(planted_trips_db) -> None:
    db = planted_trips_db.database
    rows = catalog_rows(db)
    for entity, (table, _key) in BRONZE.items():
        for severity in ("error", "warn"):
            flags = [
                f"dq_fail_{code.lower()}"
                for code, ent, sev, *_ in rows
                if ent == entity and sev == severity
            ]
            expected = (
                scalar(db, f"select coalesce(sum({' + '.join(flags)}), 0) from bronze.{table}")
                if flags
                else 0
            )
            counted = scalar(
                db,
                "select count(*) from silver.dq_rule_failures "
                f"where entity = '{entity}' and severity = '{severity}'",
            )
            assert counted == expected, (entity, severity)


def test_the_planted_build_has_failures_to_count(planted_trips_db) -> None:
    db = planted_trips_db.database
    assert scalar(db, "select count(*) from silver.dq_rule_failures") > 0
    assert scalar(db, "select count(*) from silver.dq_rule_failures where severity = 'error'") > 0
    assert scalar(db, "select count(*) from silver.dq_rule_failures where severity = 'warn'") > 0


def test_every_failure_row_key_exists_in_its_bronze_table(planted_trips_db) -> None:
    db = planted_trips_db.database
    for entity, (table, key) in BRONZE.items():
        orphans = scalar(
            db,
            "select count(*) from silver.dq_rule_failures f "
            f"where f.entity = '{entity}' and f.row_key not in (select {key} from bronze.{table})",
        )
        assert orphans == 0, entity


def test_failures_carry_the_source_file_and_batch_of_their_row(planted_trips_db) -> None:
    db = planted_trips_db.database
    table, key = BRONZE["yellow_trips"]
    mismatched = scalar(
        db,
        "select count(*) from silver.dq_rule_failures f "
        f"join bronze.{table} b on b.{key} = f.row_key "
        "where f.entity = 'yellow_trips' "
        "and (f._source_file <> b._source_file or f._batch_id <> b._batch_id)",
    )
    assert mismatched == 0
    files = {
        row[0]
        for row in query(
            db,
            "select distinct _source_file from silver.dq_rule_failures "
            "where entity = 'yellow_trips'",
        )
    }
    assert "yellow_tripdata_2024-01.parquet" in files


def test_the_summary_has_one_row_per_catalog_rule(planted_trips_db) -> None:
    db = planted_trips_db.database
    assert scalar(db, "select count(*) from silver.dq_quarantine_summary") == len(catalog_rows(db))
    missing = scalar(
        db,
        "select count(*) from silver.dq_rule_catalog c "
        "left join silver.dq_quarantine_summary s on s.entity = c.entity and s.rule_code = c.code "
        "where s.rule_code is null",
    )
    assert missing == 0


def test_summary_counts_and_rates_agree_with_the_long_form_and_bronze(planted_trips_db) -> None:
    db = planted_trips_db.database
    summary = query(
        db,
        "select entity, rule_code, failed_rows, failed_rate, entity_rows, "
        "entity_quarantined_rows, entity_quarantine_rate "
        "from silver.dq_quarantine_summary",
    )
    assert summary
    for entity, rule, failed, rate, entity_rows, quarantined, quarantine_rate in summary:
        table, _key = BRONZE[entity]
        long_form = scalar(
            db,
            "select count(*) from silver.dq_rule_failures "
            f"where entity = '{entity}' and rule_code = '{rule}'",
        )
        assert failed == long_form, (entity, rule)
        assert entity_rows == scalar(db, f"select count(*) from bronze.{table}"), entity
        assert quarantined == scalar(
            db, f"select count(*) from bronze.{table} where dq_is_valid = 0"
        ), entity
        assert abs(float(rate) - failed / entity_rows) <= 0.000001, (entity, rule)
        assert abs(float(quarantine_rate) - quarantined / entity_rows) <= 0.000001, entity


def test_warned_rows_count_rows_not_failures(planted_trips_db) -> None:
    db = planted_trips_db.database
    table, _key = BRONZE["yellow_trips"]
    warned_in_bronze = scalar(db, f"select count(*) from bronze.{table} where dq_warning_count > 0")
    values = {
        row[0]
        for row in query(
            db,
            "select entity_warned_rows from silver.dq_quarantine_summary "
            "where entity = 'yellow_trips'",
        )
    }
    assert values == {warned_in_bronze}
    assert warned_in_bronze > 0


def test_an_entity_without_warn_rules_has_zero_warned_rows(planted_trips_db) -> None:
    db = planted_trips_db.database
    warned = query(
        db,
        "select distinct entity_warned_rows, entity_warned_rate "
        "from silver.dq_quarantine_summary where entity = 'taxi_zones'",
    )
    assert len(warned) == 1
    assert warned[0][0] == 0
    assert float(warned[0][1]) == 0.0


def test_two_builds_of_the_same_data_give_identical_counts(planted_trips_db, tmp_path) -> None:
    """The DQ tables are full-rebuild tables, so a second build changes nothing (QUAR-06)."""
    before = {
        table: query(planted_trips_db.database, f"select * from silver.{table} order by all")
        for table in ("dq_rule_failures", "dq_quarantine_summary")
    }
    database = tmp_path / "rebuild.duckdb"
    shutil.copy(planted_trips_db.database, database)
    run_dbt(
        ["build", "--select", *DQ_MODELS, "--target-path", str(tmp_path / "target")],
        env={"DBT_DUCKDB_PATH": str(database)},
    )
    for table, rows in before.items():
        assert query(database, f"select * from silver.{table} order by all") == rows, table


# --- the docs (QUAR-02, DOCS-04) -------------------------------------------------------------


def test_the_generated_docs_hold_the_rule_catalog_with_its_rationale(
    planted_trips_db, tmp_path_factory
) -> None:
    """`dbt docs generate` on the planted database puts the catalog model in both files.

    Publishing this site to GitHub Pages is DOCS-02 (Phase 3); here the generated files are the
    proof that the catalog, its description and its rationale column reach the docs.
    """
    docs = tmp_path_factory.mktemp("docs") / "docs"
    run_dbt(
        ["docs", "generate", "--target", "duckdb", "--target-path", str(docs)],
        env={"DBT_DUCKDB_PATH": str(planted_trips_db.database)},
    )
    uid = "model.dbt_starter.silver__dq_rule_catalog"
    catalog = json.loads((docs / "catalog.json").read_text())
    manifest = json.loads((docs / "manifest.json").read_text())

    columns = [name.lower() for name in catalog["nodes"][uid]["columns"]]
    assert sorted(columns) == sorted(CATALOG_COLUMNS)

    node = manifest["nodes"][uid]
    description = node["description"].lower()
    assert "unusable" in description
    assert "suspicious" in description
    assert node["columns"]["rationale"]["description"].strip()


# --- project copies: an empty registry and an apostrophe in a rationale ---------------------


@pytest.fixture(scope="module")
def project_copy(dbt_packages_ready: None, tmp_path_factory: pytest.TempPathFactory) -> Path:
    destination = tmp_path_factory.mktemp("dq_proj")
    shutil.copytree(REPO_ROOT, destination, symlinks=True, ignore=COPY_IGNORE, dirs_exist_ok=True)
    return destination


def build_dq_models(project: Path, database: Path, *models: str) -> None:
    # A catalog-only build leaves the failures table unbuilt, so the relationships test that
    # reads both tables is left out.
    run_dbt(
        [
            "build",
            "--select",
            *models,
            "--exclude",
            "test_name:relationships",
            "--target-path",
            str(project / "t_dq"),
        ],
        project_dir=project,
        env={"DBT_DUCKDB_PATH": str(database)},
    )


def test_with_the_example_entities_removed_the_tables_build_empty(
    project_copy: Path, tmp_path
) -> None:
    entities = project_copy / "macros" / "dq" / "dq_entities.sql"
    original = entities.read_text()
    stripped = EXAMPLE_MARKER.sub("", original)
    assert stripped != original, "the example markers were not found in dq_entities.sql"
    assert "rules__nyc_taxi" not in stripped
    database = tmp_path / "empty.duckdb"
    entities.write_text(stripped)
    try:
        build_dq_models(project_copy, database, *DQ_MODELS)
    finally:
        entities.write_text(original)
    expected = {
        "dq_rule_failures": FAILURES_COLUMNS,
        "dq_quarantine_summary": SUMMARY_COLUMNS,
        "dq_rule_catalog": CATALOG_COLUMNS,
    }
    for table, columns in expected.items():
        assert scalar(database, f"select count(*) from silver.{table}") == 0, table
        assert column_names(database, "silver", table) == columns, table


def test_a_quote_in_a_rationale_reaches_the_catalog_unchanged(project_copy: Path, tmp_path) -> None:
    registry = project_copy / "macros" / "rules" / "nyc_taxi" / "taxi_zones.sql"
    original = registry.read_text()
    text = "It's a quoted rationale with an apostrophe."
    rule = (
        "{'code': 'ZONE_QUOTE_PROBE', 'description': 'probe', 'severity': 'error', "
        f"'on_null': 'pass', 'rationale': \"{text}\", 'fail_when': '1 = 0'}},"
    )
    injected = original.replace("return([", "return([" + rule, 1)
    assert injected != original
    database = tmp_path / "quote.duckdb"
    registry.write_text(injected)
    try:
        build_dq_models(project_copy, database, "silver__dq_rule_catalog")
    finally:
        registry.write_text(original)
    rows = query(
        database,
        "select rationale from silver.dq_rule_catalog where code = 'ZONE_QUOTE_PROBE'",
    )
    assert rows == [(text,)]


# --- undeclared landing columns (D-11, LAND-05) ---------------------------------------------


def test_a_new_landing_column_warns_once_and_a_declared_table_passes(planted_trips_db) -> None:
    """The 2026-09 file adds `request_source`, which no source YAML declares."""
    results = json.loads((planted_trips_db.target / "run_results.json").read_text())["results"]
    by_name = {
        name: result
        for result in results
        for name in (
            "yellow_trips_undeclared_landing_columns",
            "taxi_zones_undeclared_landing_columns",
        )
        if name in result["unique_id"]
    }
    assert set(by_name) == {
        "yellow_trips_undeclared_landing_columns",
        "taxi_zones_undeclared_landing_columns",
    }
    trips = by_name["yellow_trips_undeclared_landing_columns"]
    assert trips["status"] == "warn"
    assert trips["failures"] == 1
    zones = by_name["taxi_zones_undeclared_landing_columns"]
    assert zones["status"] == "pass"
    assert zones["failures"] == 0


def test_a_clean_parse_succeeds_with_the_undeclared_columns_test(tmp_path) -> None:
    """The graph lookup is guarded by `execute`, so a clean parse needs no graph."""
    result = run_dbt(
        ["parse", "--no-partial-parse", "--target-path", str(tmp_path / "parse")],
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output[-2000:]
    assert "has no attribute 'sources'" not in output

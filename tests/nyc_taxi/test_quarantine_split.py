"""Planted bad rows land in quarantine with their reasons, and nothing is dropped (QUAR-04).

Every build here runs on a temporary DuckDB file chosen through `DBT_DUCKDB_PATH`, so the demo
database never receives a planted row (D-01). The zone file is fed through the real loader
functions, then the real dbt project builds it.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from support.dbt_cli import REPO_ROOT, run_dbt

from starter_cli.landing import ensure_landing_table, land_file
from starter_cli.nyc_taxi import ENTITIES
from starter_cli.project import duckdb_path

HEADER = '"LocationID","Borough","Zone","service_zone"'

# Four valid rows (ids 1, 2, 264 and 9, the last with no service zone) and four bad ones.
PLANTED_ROWS = [
    "1,EWR,Newark Airport,EWR",
    "2,Queens,Jamaica Bay,Boro Zone",
    "264,Unknown,N/A,N/A",
    "abc,Queens,Bad Id,Boro Zone",
    ",Queens,Missing Id,Boro Zone",
    "7,,No Borough,Boro Zone",
    "8,Atlantis,Nowhere,Mars Zone",
    "9,Queens,No Service Zone,",
]

ALL_VALID_ROWS = [
    "1,EWR,Newark Airport,EWR",
    "2,Queens,Jamaica Bay,Boro Zone",
    "3,Manhattan,Alphabet City,Yellow Zone",
]


def build_on_temporary_database(tmp_path: Path, rows: list[str]) -> Path:
    """Land `rows` in a temporary database, run `dbt build` on it, and return its path."""
    csv_path = tmp_path / "planted_zones.csv"
    csv_path.write_text("\n".join([HEADER, *rows]) + "\n")
    database = tmp_path / "split.duckdb"
    assert database.resolve() != duckdb_path(REPO_ROOT).resolve()

    spec = ENTITIES["taxi_zones"]
    con = duckdb.connect(str(database))
    try:
        ensure_landing_table(con, spec)
        assert land_file(con, spec, csv_path) == len(rows)
        # The whole project builds, trips included, so the trips landing table must exist.
        # It stays empty: this test is about zones only.
        ensure_landing_table(con, ENTITIES["yellow_trips"])
        assert scalar(con, "select count(*) from landing.nyc_taxi__yellow_trips") == 0
    finally:
        con.close()

    run_dbt(
        ["build", "--target-path", str(tmp_path / "target")],
        env={"DBT_DUCKDB_PATH": str(database)},
    )
    return database


def scalar(con: duckdb.DuckDBPyConnection, sql: str) -> int:
    return con.execute(sql).fetchone()[0]


@pytest.fixture(scope="module")
def planted_db(dbt_packages_ready: None, tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_on_temporary_database(tmp_path_factory.mktemp("planted"), PLANTED_ROWS)


@pytest.fixture(scope="module")
def valid_db(dbt_packages_ready: None, tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_on_temporary_database(tmp_path_factory.mktemp("valid"), ALL_VALID_ROWS)


def test_nothing_is_dropped_between_landing_and_the_split(planted_db: Path) -> None:
    con = duckdb.connect(str(planted_db), read_only=True)
    try:
        landing = scalar(con, "select count(*) from landing.nyc_taxi__taxi_zones")
        bronze = scalar(con, "select count(*) from bronze.nyc_taxi__taxi_zones")
        silver = scalar(con, "select count(*) from silver.taxi_zones")
        quarantine = scalar(con, "select count(*) from silver.taxi_zones_quarantine")
        gold = scalar(con, "select count(*) from gold.dim_zone")
    finally:
        con.close()
    assert (landing, bronze) == (8, 8)
    assert (silver, quarantine) == (4, 4)
    assert silver + quarantine == bronze == landing
    assert gold == 4


def test_no_row_is_in_both_silver_and_quarantine(planted_db: Path) -> None:
    con = duckdb.connect(str(planted_db), read_only=True)
    try:
        overlap = scalar(
            con,
            "select count(*) from silver.taxi_zones s "
            "join silver.taxi_zones_quarantine q on s.zone_key = q.zone_key",
        )
        keys = scalar(
            con,
            "select count(distinct zone_key) from "
            "(select zone_key from silver.taxi_zones "
            "union all select zone_key from silver.taxi_zones_quarantine) u",
        )
    finally:
        con.close()
    assert overlap == 0
    assert keys == 8


def test_each_bad_row_carries_its_reason_codes(planted_db: Path) -> None:
    con = duckdb.connect(str(planted_db), read_only=True)
    try:
        rows = con.execute(
            "select location_id__raw, dq_reason_codes, dq_failed_rule_count "
            "from silver.taxi_zones_quarantine"
        ).fetchall()
    finally:
        con.close()
    by_id = {raw: (reasons, count) for raw, reasons, count in rows}
    assert by_id == {
        "abc": ("ZONE_LOCATION_ID_UNPARSEABLE", 1),
        None: ("ZONE_LOCATION_ID_MISSING", 1),
        "7": ("ZONE_BOROUGH_MISSING", 1),
        "8": ("ZONE_BOROUGH_UNKNOWN|ZONE_SERVICE_ZONE_UNKNOWN", 2),
    }


def test_a_row_failing_two_rules_appears_once_and_never_in_silver(planted_db: Path) -> None:
    con = duckdb.connect(str(planted_db), read_only=True)
    try:
        in_quarantine = scalar(
            con,
            "select count(*) from silver.taxi_zones_quarantine where location_id__raw = '8'",
        )
        in_silver = scalar(con, "select count(*) from silver.taxi_zones where location_id = 8")
    finally:
        con.close()
    assert in_quarantine == 1
    assert in_silver == 0


def test_valid_rows_reach_silver_and_gold_with_their_markers(planted_db: Path) -> None:
    con = duckdb.connect(str(planted_db), read_only=True)
    try:
        silver_ids = sorted(
            row[0] for row in con.execute("select location_id from silver.taxi_zones").fetchall()
        )
        unknown = scalar(
            con, "select is_unknown_location from gold.dim_zone where location_id = 264"
        )
        airport = scalar(con, "select is_airport from gold.dim_zone where location_id = 1")
    finally:
        con.close()
    assert silver_ids == [1, 2, 9, 264]
    assert unknown == 1
    assert airport == 1


def test_an_empty_service_zone_is_valid(planted_db: Path) -> None:
    con = duckdb.connect(str(planted_db), read_only=True)
    try:
        valid = scalar(con, "select count(*) from silver.taxi_zones where location_id = 9")
    finally:
        con.close()
    assert valid == 1


def test_all_valid_rows_give_an_empty_quarantine_table(valid_db: Path) -> None:
    con = duckdb.connect(str(valid_db), read_only=True)
    try:
        bronze = scalar(con, "select count(*) from bronze.nyc_taxi__taxi_zones")
        silver = scalar(con, "select count(*) from silver.taxi_zones")
        quarantine = scalar(con, "select count(*) from silver.taxi_zones_quarantine")
    finally:
        con.close()
    assert quarantine == 0
    assert silver == bronze == len(ALL_VALID_ROWS)


def test_planted_rows_never_reach_the_demo_database(planted_db: Path) -> None:
    demo_database = duckdb_path(REPO_ROOT)
    assert planted_db.resolve() != demo_database.resolve()
    if not demo_database.is_file():
        return  # no demo database exists, so no planted row can be in it
    con = duckdb.connect(str(demo_database), read_only=True)
    try:
        planted = scalar(
            con,
            "select count(*) from landing.nyc_taxi__taxi_zones where _source_file = 'planted_zones.csv'",
        )
    finally:
        con.close()
    assert planted == 0

"""Planted trips travel the whole path and split into silver and quarantine (Phase 2 tracer).

The fixture lands two synthetic Parquet files (published-style column spellings, one month that
lacks a declared column, one that adds an undeclared column) through the real loader from a
local folder, then builds the real dbt project on a temporary database. Nothing is downloaded.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from unittest import mock

import duckdb
from support.dbt_cli import REPO_ROOT

from starter_cli.landing import load_entity
from starter_cli.nyc_taxi import ENTITIES
from starter_cli.project import duckdb_path


def query(database: Path, sql: str) -> list[tuple]:
    con = duckdb.connect(str(database), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def scalar(database: Path, sql: str):
    return query(database, sql)[0][0]


def columns_of(database: Path, relation: str) -> list[str]:
    return [row[0] for row in query(database, f"describe {relation}")]


def test_landing_holds_every_row_of_both_files(planted_trips_db) -> None:
    db = planted_trips_db.database
    assert scalar(db, "select count(*) from landing.nyc_taxi__yellow_trips") == 5
    files = dict(
        query(
            db,
            "select _source_file, count(*) from landing.nyc_taxi__yellow_trips group by 1",
        )
    )
    assert files == {"yellow_tripdata_2024-01.parquet": 4, "yellow_tripdata_2026-09.parquet": 1}


def test_source_row_numbers_count_from_1_in_file_order(planted_trips_db) -> None:
    rows = query(
        planted_trips_db.database,
        "select _source_row_number, tpep_pickup_datetime from landing.nyc_taxi__yellow_trips "
        "where _source_file = 'yellow_tripdata_2024-01.parquet' order by 1",
    )
    assert [row[0] for row in rows] == [1, 2, 3, 4]
    assert rows[3][1] is None


def test_a_declared_column_a_month_lacks_lands_as_null(planted_trips_db) -> None:
    db = planted_trips_db.database
    missing = scalar(
        db,
        "select count(*) from landing.nyc_taxi__yellow_trips "
        "where _source_file = 'yellow_tripdata_2024-01.parquet' and cbd_congestion_fee is null",
    )
    present = scalar(
        db,
        "select cbd_congestion_fee from landing.nyc_taxi__yellow_trips "
        "where _source_file = 'yellow_tripdata_2026-09.parquet'",
    )
    assert missing == 4
    assert present == "0.75"


def test_mixed_case_column_names_land_lowercased(planted_trips_db) -> None:
    db = planted_trips_db.database
    columns = columns_of(db, "landing.nyc_taxi__yellow_trips")
    assert "airport_fee" in columns
    assert "Airport_fee" not in columns
    values = dict(
        query(
            db,
            "select tpep_pickup_datetime, airport_fee from landing.nyc_taxi__yellow_trips "
            "where _source_file = 'yellow_tripdata_2024-01.parquet' "
            "and tpep_pickup_datetime in ('2024-01-15 08:00:00', '2024-01-16 09:00:00')",
        )
    )
    assert values == {"2024-01-15 08:00:00": "0.0", "2024-01-16 09:00:00": "1.75"}


def test_an_undeclared_column_is_kept_in_landing_and_left_out_of_bronze(planted_trips_db) -> None:
    db = planted_trips_db.database
    assert "request_source" in columns_of(db, "landing.nyc_taxi__yellow_trips")
    assert "request_source" not in columns_of(db, "bronze.nyc_taxi__yellow_trips")
    kept = scalar(
        db,
        "select request_source from landing.nyc_taxi__yellow_trips "
        "where _source_file = 'yellow_tripdata_2026-09.parquet'",
    )
    assert kept == "app"


def test_every_landed_row_appears_once_in_bronze(planted_trips_db) -> None:
    db = planted_trips_db.database
    assert scalar(db, "select count(*) from bronze.nyc_taxi__yellow_trips") == 5
    assert scalar(db, "select count(distinct trip_key) from bronze.nyc_taxi__yellow_trips") == 5


def test_a_missing_pickup_keeps_its_raw_value_and_has_no_typed_value(planted_trips_db) -> None:
    rows = query(
        planted_trips_db.database,
        "select pickup_datetime__raw, pickup_datetime, dropoff_datetime "
        "from bronze.nyc_taxi__yellow_trips where _source_row_number = 4 "
        "and _source_file = 'yellow_tripdata_2024-01.parquet'",
    )
    assert len(rows) == 1
    raw, typed, dropoff = rows[0]
    assert raw is None
    assert typed is None
    assert dropoff is not None


def test_the_split_gives_three_in_silver_and_two_in_quarantine(planted_trips_db) -> None:
    db = planted_trips_db.database
    silver = scalar(db, "select count(*) from silver.yellow_trips")
    quarantine = scalar(db, "select count(*) from silver.yellow_trips_quarantine")
    bronze = scalar(db, "select count(*) from bronze.nyc_taxi__yellow_trips")
    assert (silver, quarantine) == (3, 2)
    assert silver + quarantine == bronze


def test_no_trip_is_in_both_silver_and_quarantine(planted_trips_db) -> None:
    db = planted_trips_db.database
    overlap = scalar(
        db,
        "select count(*) from silver.yellow_trips s "
        "join silver.yellow_trips_quarantine q on s.trip_key = q.trip_key",
    )
    keys = scalar(
        db,
        "select count(distinct trip_key) from (select trip_key from silver.yellow_trips "
        "union all select trip_key from silver.yellow_trips_quarantine) u",
    )
    assert overlap == 0
    assert keys == 5


def test_the_quarantined_trips_carry_their_reason_codes(planted_trips_db) -> None:
    rows = query(
        planted_trips_db.database,
        "select _source_row_number, dq_reason_codes, dq_failed_rule_count "
        "from silver.yellow_trips_quarantine order by 1",
    )
    assert rows == [
        (3, "TRIP_DROPOFF_BEFORE_PICKUP", 1),
        (4, "TRIP_PICKUP_DATETIME_MISSING", 1),
    ]


def test_a_zero_length_trip_stays_in_silver(planted_trips_db) -> None:
    db = planted_trips_db.database
    rows = query(
        db,
        "select trip_duration_seconds from silver.yellow_trips "
        "where _source_file = 'yellow_tripdata_2024-01.parquet' and _source_row_number = 2",
    )
    assert rows == [(0,)]


def test_warning_columns_exist_with_their_empty_values(planted_trips_db) -> None:
    db = planted_trips_db.database
    for relation in ("silver.yellow_trips", "silver.yellow_trips_quarantine"):
        assert "dq_warning_count" in columns_of(db, relation)
        assert "dq_warning_codes" in columns_of(db, relation)
        # Plan 02-03 changes this one expectation for the zero-length trip (first warn rule).
        assert scalar(db, f"select count(*) from {relation} where dq_warning_count <> 0") == 0
        assert (
            scalar(db, f"select count(*) from {relation} where dq_warning_codes is not null") == 0
        )


def test_the_valid_trip_has_the_planted_duration(planted_trips_db) -> None:
    rows = query(
        planted_trips_db.database,
        "select trip_duration_seconds, vendor_id, passenger_count, pu_location_id "
        "from silver.yellow_trips where _source_file = 'yellow_tripdata_2024-01.parquet' "
        "and _source_row_number = 1",
    )
    assert rows == [(600, 1, 1, 1)]


def test_landing_the_folder_twice_keeps_five_rows(planted_trips_db, tmp_path: Path) -> None:
    again = tmp_path / "again.duckdb"
    shutil.copy(planted_trips_db.database, again)
    assert again.resolve() != duckdb_path(REPO_ROOT).resolve()
    folder = planted_trips_db.database.parent / "files"
    with mock.patch.dict(os.environ, {"DBT_DUCKDB_PATH": str(again)}):
        landed = load_entity(ENTITIES["yellow_trips"], REPO_ROOT, from_dir=folder)
    assert landed == 5
    assert scalar(again, "select count(*) from landing.nyc_taxi__yellow_trips") == 5


def test_the_planted_trips_never_reach_the_demo_database(planted_trips_db) -> None:
    demo_database = duckdb_path(REPO_ROOT)
    assert planted_trips_db.database.resolve() != demo_database.resolve()
    if not demo_database.is_file():
        return  # no demo database exists, so no planted row can be in it
    con = duckdb.connect(str(demo_database), read_only=True)
    try:
        planted = con.execute(
            "select count(*) from landing.nyc_taxi__yellow_trips where request_source = 'app'"
        ).fetchone()[0]
    except duckdb.Error:
        return  # the demo database has no trips landing table, or no such column, yet
    finally:
        con.close()
    assert planted == 0

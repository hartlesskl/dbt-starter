"""The synthetic fixture plants every rule and the build reproduces its exact counts (TEST-04).

The fixture rows live in `starter_cli.nyc_taxi.fixture`, each tagged with the rule codes it
plants, next to a hand-written `EXPECTED`. Three things are proven here:

1. Double entry: `EXPECTED` equals the counts derived from the tags, with no database.
2. Coverage: every code the catalog registers is planted, and every tag is a registered code.
3. The build: landing the fixture through the real loader and running `dbt build` gives the
   expected counts in `silver.dq_quarantine_summary`, in the silver and quarantine tables and
   per row, with no failing data test and exactly one warning.

The build runs on its own DuckDB file under `target/gate/` (or a temporary folder), never on the
demo database, and nothing is downloaded.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest import mock

import duckdb
import pytest
from support.dbt_cli import REPO_ROOT

from starter_cli.gate import FixtureBuild, land_fixture
from starter_cli.nyc_taxi import write_fixture as exported_write_fixture
from starter_cli.nyc_taxi.fixture import (
    EXPECTED,
    REVERSE_PAIR_PICKUP,
    TRIP_FILES,
    ZONE_FILE,
    row_tags,
    tag_counts,
    write_fixture,
)
from starter_cli.project import DEFAULT_DUCKDB_PATH

pytestmark = pytest.mark.gate_counts

ENTITIES = ("taxi_zones", "yellow_trips")
LANDING = {
    "taxi_zones": "landing.nyc_taxi__taxi_zones",
    "yellow_trips": "landing.nyc_taxi__yellow_trips",
}
SILVER = {"taxi_zones": "silver.taxi_zones", "yellow_trips": "silver.yellow_trips"}
QUARANTINE = {
    "taxi_zones": "silver.taxi_zones_quarantine",
    "yellow_trips": "silver.yellow_trips_quarantine",
}
BRONZE = {
    "taxi_zones": "bronze.nyc_taxi__taxi_zones",
    "yellow_trips": "bronze.nyc_taxi__yellow_trips",
}
UNDECLARED_WARN_TEST = "yellow_trips_undeclared_landing_columns"
REGISTERED_ZONE_CODES = 6
REGISTERED_TRIP_CODES = 38


def scalar(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> object:
    return con.execute(sql, params or []).fetchone()[0]


@pytest.fixture(scope="module")
def con(fixture_build: FixtureBuild):
    connection = duckdb.connect(str(fixture_build.database), read_only=True)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture(scope="module")
def catalog(con: duckdb.DuckDBPyConnection) -> dict[str, dict[str, str]]:
    """Registered rule codes per entity with their severity, read from the built catalog."""
    rows = con.execute("select entity, code, severity from silver.dq_rule_catalog").fetchall()
    by_entity: dict[str, dict[str, str]] = {entity: {} for entity in ENTITIES}
    for entity, code, severity in rows:
        by_entity[entity][code] = severity
    return by_entity


# --- Double entry: the tags and the expected counts agree, with no database ---


def test_expected_rule_counts_equal_the_counts_derived_from_the_tags() -> None:
    derived = tag_counts()
    for entity in ENTITIES:
        assert EXPECTED[entity]["rules"] == derived[entity], entity


def test_expected_row_counts_follow_from_the_tags_and_the_catalog_severities() -> None:
    """Landing is the number of tagged rows; a row with no tag is valid and stays in silver."""
    tags = row_tags()
    for entity in ENTITIES:
        landing = len(tags[entity])
        assert EXPECTED[entity]["landing"] == landing, entity
        assert EXPECTED[entity]["silver"] + EXPECTED[entity]["quarantine"] == landing, entity
    untagged_trips = sum(1 for plants in tags["yellow_trips"].values() if not plants)
    assert untagged_trips <= EXPECTED["yellow_trips"]["silver"]


def test_every_one_of_the_44_registered_codes_is_planted_by_a_row() -> None:
    derived = tag_counts()
    assert len(derived["taxi_zones"]) == REGISTERED_ZONE_CODES
    assert len(derived["yellow_trips"]) == REGISTERED_TRIP_CODES
    assert all(count >= 1 for rules in derived.values() for count in rules.values())


def test_the_zone_and_trip_codes_keep_to_their_prefixes() -> None:
    derived = tag_counts()
    assert all(code.startswith("ZONE_") for code in derived["taxi_zones"])
    assert all(code.startswith("TRIP_") for code in derived["yellow_trips"])


def test_the_fixture_names_the_five_era_files_and_the_zone_file() -> None:
    assert [file.name for file in TRIP_FILES] == [
        "yellow_tripdata_2019-01.parquet",
        "yellow_tripdata_2024-01.parquet",
        "yellow_tripdata_2024-02.parquet",
        "yellow_tripdata_2025-01.parquet",
        "yellow_tripdata_2026-09.parquet",
    ]
    assert ZONE_FILE == "taxi_zone_lookup.csv"


def test_the_package_exports_the_fixture_writer() -> None:
    assert exported_write_fixture is write_fixture


def test_a_trip_row_that_plants_nothing_is_valid_and_a_planted_row_names_its_codes() -> None:
    trips = row_tags()["yellow_trips"]
    assert any(plants == () for plants in trips.values())
    assert ("TRIP_PICKUP_OUT_OF_MONTH", "TRIP_EXACT_DUPLICATE") in trips.values()


# --- Landing: the real loader, no HTTP request ---


def test_landing_the_fixture_makes_no_http_request(tmp_path: Path) -> None:
    database = tmp_path / "landed_fixture.duckdb"
    assert database.resolve() != (REPO_ROOT / DEFAULT_DUCKDB_PATH).resolve()
    with mock.patch("starter_cli.landing.requests.get", side_effect=AssertionError("HTTP used")):
        land_fixture(REPO_ROOT, tmp_path / "work", database)
    connection = duckdb.connect(str(database), read_only=True)
    try:
        for entity in ENTITIES:
            landed = scalar(connection, f"select count(*) from {LANDING[entity]}")
            assert landed == EXPECTED[entity]["landing"]
    finally:
        connection.close()


def test_the_fixture_database_is_not_the_demo_database(fixture_build: FixtureBuild) -> None:
    assert fixture_build.database.resolve() != (REPO_ROOT / DEFAULT_DUCKDB_PATH).resolve()


# --- The build reproduces the expected counts ---


def test_every_catalog_code_has_a_tag_and_every_tag_is_a_catalog_code(
    catalog: dict[str, dict[str, str]],
) -> None:
    derived = tag_counts()
    for entity in ENTITIES:
        assert sorted(derived[entity]) == sorted(catalog[entity]), entity
    assert sum(len(codes) for codes in catalog.values()) == 44


def test_the_summary_failed_rows_equal_the_expected_counts_per_entity_and_rule(
    con: duckdb.DuckDBPyConnection, catalog: dict[str, dict[str, str]]
) -> None:
    rows = con.execute(
        "select entity, rule_code, failed_rows from silver.dq_quarantine_summary"
    ).fetchall()
    actual: dict[str, dict[str, int]] = {entity: {} for entity in ENTITIES}
    for entity, code, failed in rows:
        actual[entity][code] = int(failed)
    for entity in ENTITIES:
        planted = {code: actual[entity][code] for code in catalog[entity] if actual[entity][code]}
        assert planted == EXPECTED[entity]["rules"], entity
        assert not [code for code in catalog[entity] if code not in actual[entity]]


def test_landing_silver_and_quarantine_counts_equal_the_expected_counts(
    con: duckdb.DuckDBPyConnection,
) -> None:
    for entity in ENTITIES:
        landing = scalar(con, f"select count(*) from {LANDING[entity]}")
        silver = scalar(con, f"select count(*) from {SILVER[entity]}")
        quarantine = scalar(con, f"select count(*) from {QUARANTINE[entity]}")
        assert (landing, silver, quarantine) == (
            EXPECTED[entity]["landing"],
            EXPECTED[entity]["silver"],
            EXPECTED[entity]["quarantine"],
        ), entity


def test_trip_rows_with_warnings_equal_the_expected_count(con: duckdb.DuckDBPyConnection) -> None:
    warned = scalar(con, "select count(*) from silver.yellow_trips where dq_warning_count > 0")
    assert warned == EXPECTED["yellow_trips"]["silver_warned"]


def test_the_trip_quarantine_count_equals_the_rows_tagged_with_an_error_code(
    con: duckdb.DuckDBPyConnection, catalog: dict[str, dict[str, str]]
) -> None:
    for entity in ENTITIES:
        errors = {code for code, severity in catalog[entity].items() if severity == "error"}
        tagged = sum(1 for plants in row_tags()[entity].values() if errors.intersection(plants))
        assert scalar(con, f"select count(*) from {QUARANTINE[entity]}") == tagged, entity


def test_every_row_fails_exactly_the_codes_it_is_tagged_with(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Per row, not per count: a count can match by luck, a row-by-row match cannot."""
    for entity in ENTITIES:
        cursor = con.execute(f"select * from {BRONZE[entity]}")
        names = [column[0] for column in cursor.description]
        flags = {
            name: name.removeprefix("dq_fail_").upper()
            for name in names
            if name.startswith("dq_fail_") and name != "dq_failed_rule_count"
        }
        actual: dict[tuple[str, int], tuple[str, ...]] = {}
        for record in cursor.fetchall():
            row = dict(zip(names, record, strict=True))
            failed = sorted(code for name, code in flags.items() if row[name] == 1)
            actual[(row["_source_file"], int(row["_source_row_number"]))] = tuple(failed)
        expected = {key: tuple(sorted(plants)) for key, plants in row_tags()[entity].items()}
        assert actual == expected, entity


def test_landing_source_files_are_exactly_the_fixture_file_names(
    con: duckdb.DuckDBPyConnection,
) -> None:
    trip_files = {
        row[0]
        for row in con.execute(
            "select distinct _source_file from landing.nyc_taxi__yellow_trips"
        ).fetchall()
    }
    zone_files = {
        row[0]
        for row in con.execute(
            "select distinct _source_file from landing.nyc_taxi__taxi_zones"
        ).fetchall()
    }
    assert trip_files == {file.name for file in TRIP_FILES}
    assert zone_files == {ZONE_FILE}


def test_an_empty_cell_lands_as_null_and_fails_only_the_missing_rule(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """An empty string is NULL in landing, so MISSING fires and no UNPARSEABLE rule does."""
    empty_text = scalar(
        con,
        "select count(*) from landing.nyc_taxi__yellow_trips where "
        "tpep_pickup_datetime = '' or tpep_dropoff_datetime = '' or pulocationid = '' "
        "or dolocationid = '' or passenger_count = '' or cbd_congestion_fee = ''",
    )
    assert empty_text == 0
    missing = {
        key: plants
        for key, plants in row_tags()["yellow_trips"].items()
        if any(code.endswith("_MISSING") for code in plants)
    }
    assert len(missing) == 4
    for plants in missing.values():
        assert len(plants) == 1 and plants[0].endswith("_MISSING")
    assert (
        scalar(
            con,
            "select count(*) from bronze.nyc_taxi__yellow_trips where "
            "dq_fail_trip_pickup_datetime_missing = 1 and dq_failed_rule_count = 1 "
            "and dq_fail_trip_pickup_datetime_unparseable = 0",
        )
        == 1
    )


def test_the_reverse_cross_file_pair_keeps_the_in_month_copy_in_silver(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """D-09 as amended on 2026-11-22: the 2025-01 copy is in month, so it wins its rank."""
    picked = datetime.fromisoformat(REVERSE_PAIR_PICKUP)
    silver = con.execute(
        "select _source_file from silver.yellow_trips where pickup_datetime = ?", [picked]
    ).fetchall()
    assert silver == [("yellow_tripdata_2025-01.parquet",)]
    quarantine = con.execute(
        "select _source_file, dq_failed_rule_count, dq_reason_codes "
        "from silver.yellow_trips_quarantine where pickup_datetime = ?",
        [picked],
    ).fetchall()
    assert len(quarantine) == 1
    source_file, failed_count, reason_codes = quarantine[0]
    assert source_file == "yellow_tripdata_2024-01.parquet"
    assert failed_count == 2
    assert "TRIP_PICKUP_OUT_OF_MONTH" in reason_codes
    assert "TRIP_EXACT_DUPLICATE" in reason_codes


def test_two_rows_differing_only_in_an_undeclared_column_are_exact_duplicates(
    con: duckdb.DuckDBPyConnection,
) -> None:
    rows = con.execute(
        "select request_source, dq_fail_trip_exact_duplicate "
        "from bronze.nyc_taxi__yellow_trips b join landing.nyc_taxi__yellow_trips l "
        "on b._source_file = l._source_file and b._source_row_number = l._source_row_number "
        "where b._source_file = 'yellow_tripdata_2026-09.parquet' "
        "order by b._source_row_number"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0][0] != rows[1][0]
    assert [flag for _, flag in rows] == [0, 1]


def test_the_build_has_no_failing_test_and_exactly_the_one_expected_warning(
    fixture_build: FixtureBuild,
) -> None:
    results = json.loads(fixture_build.run_results.read_text())["results"]
    bad = [r["unique_id"] for r in results if r["status"] in ("error", "fail", "skipped")]
    assert bad == []
    warned = [r["unique_id"].split(".")[2] for r in results if r["status"] == "warn"]
    assert warned == [UNDECLARED_WARN_TEST]
    assert EXPECTED["warn_tests"] == len(warned)

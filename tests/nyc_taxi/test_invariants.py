"""The conservation invariants fail when the data drifts (TEST-02, T-02-20).

Six named tests say that nothing is lost between landing and the split: landing equals bronze,
bronze equals silver plus quarantine (per batch and in total), and silver and quarantine never
share a key. A test that has never been seen to fail proves nothing, so each case here copies
the planted database, breaks it in one way, and runs only the named test on the copy.

Every dbt run uses its own `--target-path` and a copy of the database chosen through
`DBT_DUCKDB_PATH`, so the demo database and the shared planted database are never changed.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import duckdb
import pytest
from support.dbt_cli import run_dbt

ENTITIES = ("taxi_zones", "yellow_trips")
INVARIANTS = (
    "landing_equals_bronze",
    "bronze_equals_silver_plus_quarantine",
    "silver_and_quarantine_do_not_overlap",
)
ALL_SIX = [f"{entity}_{invariant}" for entity in ENTITIES for invariant in INVARIANTS]


def run_invariants(database: Path, work: Path, names: list[str]) -> dict[str, str]:
    """Run `dbt test` for the named tests on `database`; return each test's status by name."""
    target = work / "target"
    run_dbt(
        ["test", "--select", " ".join(names), "--target-path", str(target)],
        env={"DBT_DUCKDB_PATH": str(database)},
        check=False,
    )
    results = json.loads((target / "run_results.json").read_text())["results"]
    return {result["unique_id"].split(".")[2]: result["status"] for result in results}


@pytest.fixture
def copy_of_planted(planted_trips_db: Any, tmp_path: Path) -> Path:
    """A fresh copy of the planted database file that a case may break."""
    database = tmp_path / "copy.duckdb"
    shutil.copyfile(planted_trips_db.database, database)
    return database


def mutate(database: Path, *statements: str) -> None:
    con = duckdb.connect(str(database))
    try:
        for statement in statements:
            con.execute(statement)
    finally:
        con.close()


def test_the_untouched_copy_passes_all_six(copy_of_planted: Path, tmp_path: Path) -> None:
    statuses = run_invariants(copy_of_planted, tmp_path, ALL_SIX)
    assert statuses == {name: "pass" for name in ALL_SIX}


def test_a_deleted_landing_trip_fails_landing_equals_bronze(
    copy_of_planted: Path, tmp_path: Path
) -> None:
    mutate(
        copy_of_planted,
        "delete from landing.nyc_taxi__yellow_trips "
        "where _source_file like '%2026-09%' and _source_row_number = 1",
    )
    statuses = run_invariants(copy_of_planted, tmp_path, ["yellow_trips_landing_equals_bronze"])
    assert statuses == {"yellow_trips_landing_equals_bronze": "fail"}


def test_a_deleted_silver_trip_fails_the_split(copy_of_planted: Path, tmp_path: Path) -> None:
    mutate(
        copy_of_planted,
        "delete from silver.yellow_trips "
        "where _source_file like '%2026-09%' and _source_row_number = 1",
    )
    statuses = run_invariants(
        copy_of_planted, tmp_path, ["yellow_trips_bronze_equals_silver_plus_quarantine"]
    )
    assert statuses == {"yellow_trips_bronze_equals_silver_plus_quarantine": "fail"}


def test_a_silver_key_copied_into_quarantine_fails_the_overlap_test(
    copy_of_planted: Path, tmp_path: Path
) -> None:
    mutate(
        copy_of_planted,
        "insert into silver.yellow_trips_quarantine "
        "(trip_key, _source_file, _source_row_number, _batch_id, _loaded_at) "
        "select trip_key, _source_file, _source_row_number, _batch_id, _loaded_at "
        "from silver.yellow_trips where _source_file like '%2026-09%' and _source_row_number = 1",
    )
    statuses = run_invariants(
        copy_of_planted, tmp_path, ["yellow_trips_silver_and_quarantine_do_not_overlap"]
    )
    assert statuses == {"yellow_trips_silver_and_quarantine_do_not_overlap": "fail"}


def test_an_overlap_is_found_even_when_every_batch_count_still_adds_up(
    copy_of_planted: Path, tmp_path: Path
) -> None:
    """Move one key into quarantine while a different silver row of the same file is deleted.

    The per-batch counts are unchanged (one row in, one row out), so only the overlap test
    can see that the key is now on both sides.
    """
    mutate(
        copy_of_planted,
        "insert into silver.yellow_trips_quarantine "
        "(trip_key, _source_file, _source_row_number, _batch_id, _loaded_at) "
        "select trip_key, _source_file, _source_row_number, _batch_id, _loaded_at "
        "from silver.yellow_trips where _source_file like '%2024-01%' and _source_row_number = 1",
        "delete from silver.yellow_trips "
        "where _source_file like '%2024-01%' and _source_row_number = 2",
    )
    statuses = run_invariants(
        copy_of_planted,
        tmp_path,
        [
            "yellow_trips_bronze_equals_silver_plus_quarantine",
            "yellow_trips_silver_and_quarantine_do_not_overlap",
        ],
    )
    assert statuses == {
        "yellow_trips_bronze_equals_silver_plus_quarantine": "pass",
        "yellow_trips_silver_and_quarantine_do_not_overlap": "fail",
    }


def test_a_deleted_zone_landing_row_fails_landing_equals_bronze(
    copy_of_planted: Path, tmp_path: Path
) -> None:
    mutate(
        copy_of_planted,
        "delete from landing.nyc_taxi__taxi_zones where _source_row_number = 1",
    )
    statuses = run_invariants(copy_of_planted, tmp_path, ["taxi_zones_landing_equals_bronze"])
    assert statuses == {"taxi_zones_landing_equals_bronze": "fail"}


def test_a_deleted_zone_silver_row_fails_the_zone_split(
    copy_of_planted: Path, tmp_path: Path
) -> None:
    mutate(
        copy_of_planted,
        "delete from silver.taxi_zones where location_id = 1",
    )
    statuses = run_invariants(
        copy_of_planted, tmp_path, ["taxi_zones_bronze_equals_silver_plus_quarantine"]
    )
    assert statuses == {"taxi_zones_bronze_equals_silver_plus_quarantine": "fail"}

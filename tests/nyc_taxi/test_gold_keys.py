"""Every fact key test can fail, and a missing rate code is allowed (GOLD-01, D-06).

A key test that has never failed proves nothing. Each case here copies the planted database,
gives one fact row a code the dimension does not hold, and runs only the matching relationships
test on the copy. The NULL case shows the opposite: a Flex Fare trip has no rate code, and the
relationships test skips NULL, so the fact stays valid.

The invariant helpers are imported from `nyc_taxi.test_invariants` (`--import-mode=importlib`
with `pythonpath = ["tests"]` resolves that path), so the dbt-run and mutate mechanics stay in
one place. Every dbt run uses its own `--target-path` and a copy of the database.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import duckdb
import pytest
from nyc_taxi.test_invariants import mutate, run_invariants

KEY_TESTS = {
    "vendor_id": "fct_trips_vendor_id_has_a_vendor",
    "rate_code_id": "fct_trips_rate_code_id_has_a_rate_code",
    "payment_type": "fct_trips_payment_type_has_a_payment_type",
    "pu_location_id": "fct_trips_pu_location_id_has_a_zone",
    "do_location_id": "fct_trips_do_location_id_has_a_zone",
    "pickup_date_key": "fct_trips_pickup_date_key_has_a_date",
}
ALL_SIX = list(KEY_TESTS.values())
BAD_VALUES = [
    ("vendor_id", 999),
    ("rate_code_id", 42),
    ("payment_type", 99),
    ("pu_location_id", 999),
    ("do_location_id", 999),
    ("pickup_date_key", 20991231),
]
FIRST_ROW = "trip_key = (select min(trip_key) from gold.fct_trips)"


@pytest.fixture
def copy_of_planted(planted_trips_db: Any, tmp_path: Path) -> Path:
    """A fresh copy of the planted database that a case may break, with a non-empty fact."""
    database = tmp_path / "copy.duckdb"
    shutil.copyfile(planted_trips_db.database, database)
    con = duckdb.connect(str(database), read_only=True)
    try:
        rows = con.execute("select count(*) from gold.fct_trips").fetchone()[0]
    finally:
        con.close()
    assert rows >= 1, "the planted database has no fact row, so every case below would be vacuous"
    return database


def test_the_untouched_copy_passes_all_six_key_tests(copy_of_planted: Path, tmp_path: Path) -> None:
    statuses = run_invariants(copy_of_planted, tmp_path, ALL_SIX)
    assert statuses == {name: "pass" for name in ALL_SIX}


@pytest.mark.parametrize(("column", "bad_value"), BAD_VALUES, ids=[c for c, _ in BAD_VALUES])
def test_a_code_the_dimension_lacks_fails_exactly_its_key_test(
    copy_of_planted: Path, tmp_path: Path, column: str, bad_value: int
) -> None:
    mutate(copy_of_planted, f"update gold.fct_trips set {column} = {bad_value} where {FIRST_ROW}")
    statuses = run_invariants(copy_of_planted, tmp_path, ALL_SIX)
    expected = {name: "pass" for name in ALL_SIX}
    expected[KEY_TESTS[column]] = "fail"
    assert statuses == expected


def test_a_null_rate_code_still_passes_its_key_test(copy_of_planted: Path, tmp_path: Path) -> None:
    mutate(copy_of_planted, f"update gold.fct_trips set rate_code_id = null where {FIRST_ROW}")
    statuses = run_invariants(copy_of_planted, tmp_path, [KEY_TESTS["rate_code_id"]])
    assert statuses == {KEY_TESTS["rate_code_id"]: "pass"}

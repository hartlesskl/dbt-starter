"""The daily aggregate reconciliation can fail (GOLD-03, D-10).

A reconciliation that has never failed proves nothing. Each case copies the planted database,
changes the aggregate by the smallest amount that matters, and runs only the reconciliation test
on the copy: one cent in a total, one trip in a count, and one whole pickup day missing. The
clean copy shows the test passes when the aggregate and the fact agree.

The dbt-run and mutate helpers are imported from `nyc_taxi.test_invariants`, as in
`test_gold_keys.py`, so the mechanics stay in one place. Every dbt run uses its own
`--target-path` and a copy of the database.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import duckdb
import pytest
from nyc_taxi.test_invariants import mutate, run_invariants

TEST = "agg_trips_daily_reconciles_to_fct_trips"
FIRST_DAY = "pickup_date_key = (select min(pickup_date_key) from gold.agg_trips_daily)"
FIRST_ROW = (
    f"{FIRST_DAY} and pickup_borough = "
    f"(select min(pickup_borough) from gold.agg_trips_daily where {FIRST_DAY})"
)
BREAKS = [
    (
        "one_cent_in_a_total",
        "update gold.agg_trips_daily set total_amount_sum = total_amount_sum + 0.01 "
        + f"where {FIRST_ROW}",
    ),
    (
        "one_trip_in_a_count",
        f"update gold.agg_trips_daily set trip_count = trip_count + 1 where {FIRST_ROW}",
    ),
    ("a_missing_pickup_day", f"delete from gold.agg_trips_daily where {FIRST_DAY}"),
]


@pytest.fixture
def copy_of_planted(planted_trips_db: Any, tmp_path: Path) -> Path:
    """A fresh copy of the planted database that a case may break, with a non-empty aggregate."""
    database = tmp_path / "copy.duckdb"
    shutil.copyfile(planted_trips_db.database, database)
    con = duckdb.connect(str(database), read_only=True)
    try:
        rows = con.execute("select count(*) from gold.agg_trips_daily").fetchone()[0]
    finally:
        con.close()
    assert rows >= 1, "the planted database has no aggregate row, so every case would be vacuous"
    return database


def test_the_untouched_copy_reconciles(copy_of_planted: Path, tmp_path: Path) -> None:
    assert run_invariants(copy_of_planted, tmp_path, [TEST]) == {TEST: "pass"}


@pytest.mark.parametrize(("statement"), [sql for _, sql in BREAKS], ids=[n for n, _ in BREAKS])
def test_a_change_to_the_aggregate_fails_the_reconciliation(
    copy_of_planted: Path, tmp_path: Path, statement: str
) -> None:
    mutate(copy_of_planted, statement)
    assert run_invariants(copy_of_planted, tmp_path, [TEST]) == {TEST: "fail"}

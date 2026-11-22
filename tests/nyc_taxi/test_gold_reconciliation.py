"""The daily aggregate reconciliation can fail (GOLD-03, D-10).

A reconciliation that has never failed proves nothing. Each case copies the planted database,
changes the aggregate by the smallest amount that matters, and runs only the two reconciliation
tests on the copy: one cent in a total, one trip in a count, one whole pickup day missing, and
one change to each other additive measure and to the borough split. Each case names which of the
two tests must fail, so a measure or a split that only the by-borough test covers cannot go
unchecked. The clean copy shows both tests pass when the aggregate and the fact agree.

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

PER_DAY = "agg_trips_daily_reconciles_to_fct_trips"
BY_BOROUGH = "agg_trips_daily_reconciles_by_borough_to_fct_trips"
BOTH = [PER_DAY, BY_BOROUGH]
FIRST_DAY = "pickup_date_key = (select min(pickup_date_key) from gold.agg_trips_daily)"
FIRST_ROW = (
    f"{FIRST_DAY} and pickup_borough = "
    f"(select min(pickup_borough) from gold.agg_trips_daily where {FIRST_DAY})"
)


def _bump(column: str, amount: str) -> str:
    return f"update gold.agg_trips_daily set {column} = {column} + {amount} where {FIRST_ROW}"


# (case id, statement, the tests that must fail). The per-day test checks trips and the total
# amount; the by-borough test checks every additive measure per day and borough.
BREAKS = [
    ("one_cent_in_a_total", _bump("total_amount_sum", "0.01"), BOTH),
    ("one_trip_in_a_count", _bump("trip_count", "1"), BOTH),
    ("a_missing_pickup_day", f"delete from gold.agg_trips_daily where {FIRST_DAY}", BOTH),
    ("one_warned_trip", _bump("warned_trip_count", "1"), [BY_BOROUGH]),
    ("one_passenger", _bump("total_passengers", "1"), [BY_BOROUGH]),
    ("one_cent_in_the_distance", _bump("total_distance", "0.01"), [BY_BOROUGH]),
    ("one_cent_in_the_fare", _bump("fare_amount_sum", "0.01"), [BY_BOROUGH]),
    ("one_cent_in_the_tip", _bump("tip_amount_sum", "0.01"), [BY_BOROUGH]),
    (
        "a_trip_under_the_wrong_borough",
        f"update gold.agg_trips_daily set pickup_borough = 'Nowhere' where {FIRST_ROW}",
        [BY_BOROUGH],
    ),
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
    assert run_invariants(copy_of_planted, tmp_path, BOTH) == {name: "pass" for name in BOTH}


@pytest.mark.parametrize(
    ("statement", "failing"), [(sql, f) for _, sql, f in BREAKS], ids=[n for n, _, _ in BREAKS]
)
def test_a_change_to_the_aggregate_fails_the_reconciliation(
    copy_of_planted: Path, tmp_path: Path, statement: str, failing: list[str]
) -> None:
    mutate(copy_of_planted, statement)
    expected = {name: "fail" if name in failing else "pass" for name in BOTH}
    assert run_invariants(copy_of_planted, tmp_path, BOTH) == expected

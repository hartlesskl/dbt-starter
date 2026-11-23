"""The quarantine rate levels fire where they should and stay quiet elsewhere (TEST-05, D-05).

The levels test is proven on copies of the gate fixture database, whose rates are known exactly,
so every firing and non-firing case is planted rather than hoped for. The fixture's rates are:
trips 41 of 59 = 0.694915 (worst file `yellow_tripdata_2024-02.parquet`, 24 of 26 = 0.923077) and
zones 7 of 12 = 0.583333. The demo database and the gate's own fixture database are never
written: every case works on a copy or a scratch file under pytest's tmp folder, and each dbt run
has its own `--target-path`.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import duckdb
import pytest
from support.dbt_cli import REPO_ROOT, run_dbt

from starter_cli.project import duckdb_path

ZONES_TEST = "taxi_zones_quarantine_rate_within_levels"
TRIPS_TEST = "yellow_trips_quarantine_rate_within_levels"
LEVEL_TESTS = (ZONES_TEST, TRIPS_TEST)

# The 7 of 12 zone rate, and the worst trip file's 24 of 26, one resolution step (0.000001) apart.
ZONE_RATE = 0.583333
ZONE_RATE_BELOW = 0.583332
FILE_RATE = 0.923077
FILE_RATE_BELOW = 0.923076


def override(warn: Any, error: Any) -> list[str]:
    """The dbt args that replace both levels of every levels test."""
    payload = {"quarantine_rate_override": {"warn_above": warn, "error_above": error}}
    return ["--vars", json.dumps(payload)]


def level_statuses(database: Path, work: Path, extra: list[str]) -> dict[str, str]:
    """Run the levels tests on `database`; return each test's status by name."""
    target = work / "target"
    run_dbt(
        [
            "test",
            "--select",
            "test_name:quarantine_rate_levels",
            "--target-path",
            str(target),
            *extra,
        ],
        env={"DBT_DUCKDB_PATH": str(database)},
        check=False,
    )
    results = json.loads((target / "run_results.json").read_text())["results"]
    statuses = {result["unique_id"].split(".")[2]: result["status"] for result in results}
    assert set(statuses) == set(LEVEL_TESTS)
    return statuses


def copy_database(source: Path, destination: Path) -> Path:
    """Copy `source` to `destination`, refusing to touch the demo database."""
    assert destination.resolve() != source.resolve()
    assert destination.resolve() != duckdb_path(REPO_ROOT).resolve()
    shutil.copyfile(source, destination)
    return destination


def mutate(database: Path, *statements: str) -> None:
    con = duckdb.connect(str(database))
    try:
        for statement in statements:
            con.execute(statement)
    finally:
        con.close()


@pytest.fixture(scope="module")
def levels_db(fixture_build: Any, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One copy of the fixture database for every `dbt test` case: the levels tests only read."""
    folder = tmp_path_factory.mktemp("levels_db")
    return copy_database(fixture_build.database, folder / "levels.duckdb")


# (warn, error) -> (zones, trips). None means no override, so the YAML levels 0.001 and 0.01 apply.
LEVEL_CASES = [
    pytest.param((1, 1), ("pass", "pass"), id="relaxed-baseline"),
    pytest.param((0.8, 0.99), ("pass", "warn"), id="per-file-only"),
    pytest.param((0.5, 0.99), ("warn", "warn"), id="warn-on-both"),
    pytest.param((0.1, 0.9), ("warn", "fail"), id="error-through-one-file"),
    pytest.param(None, ("fail", "fail"), id="yaml-levels-fire-on-fixture"),
    pytest.param((ZONE_RATE, 0.99), ("pass", "warn"), id="zone-at-warn-equality"),
    pytest.param((ZONE_RATE_BELOW, 0.99), ("warn", "warn"), id="zone-one-step-above-warn"),
    pytest.param((0.1, ZONE_RATE), ("warn", "fail"), id="zone-at-error-equality"),
    pytest.param((0.1, ZONE_RATE_BELOW), ("fail", "fail"), id="zone-one-step-above-error"),
    pytest.param((FILE_RATE, 0.99), ("pass", "pass"), id="trip-file-at-warn-equality"),
    pytest.param((FILE_RATE_BELOW, 0.99), ("pass", "warn"), id="trip-file-one-step-above"),
]


@pytest.mark.parametrize(("levels", "expected"), LEVEL_CASES)
def test_each_level_fires_exactly_where_the_rate_is_above_it(
    levels_db: Path,
    tmp_path: Path,
    levels: tuple[Any, Any] | None,
    expected: tuple[str, str],
) -> None:
    extra = [] if levels is None else override(*levels)
    statuses = level_statuses(levels_db, tmp_path, extra)
    assert (statuses[ZONES_TEST], statuses[TRIPS_TEST]) == expected


def test_an_entity_with_no_rows_passes_its_real_levels(fixture_build: Any, tmp_path: Path) -> None:
    """With every trip table emptied the trip test has no scope, while the zones still fail."""
    database = copy_database(fixture_build.database, tmp_path / "empty_trips.duckdb")
    mutate(
        database,
        "delete from bronze.nyc_taxi__yellow_trips",
        "delete from silver.yellow_trips",
        "delete from silver.yellow_trips_quarantine",
    )
    statuses = level_statuses(database, tmp_path, [])
    assert statuses == {ZONES_TEST: "fail", TRIPS_TEST: "pass"}


BAD_LEVELS = [
    pytest.param(
        {"warn_above": "0.1", "error_above": 0.9}, "quarantine_rate_levels", id="string-level"
    ),
    pytest.param(
        {"warn_above": True, "error_above": 0.9}, "quarantine_rate_levels", id="boolean-level"
    ),
    pytest.param(
        {"warn_above": 0.1, "error_above": 1.5}, "quarantine_rate_levels", id="level-above-one"
    ),
    pytest.param(
        {"warn_above": -0.1, "error_above": 0.9}, "quarantine_rate_levels", id="negative-level"
    ),
    pytest.param(
        {"warn_above": 0.5, "error_above": 0.2}, "quarantine_rate_levels", id="warn-above-error"
    ),
    pytest.param({"warn_above": 0.1}, "quarantine_rate_override", id="override-missing-error"),
]


@pytest.mark.parametrize(("payload", "message"), BAD_LEVELS)
def test_a_bad_level_stops_compilation_and_names_the_var(
    dbt_packages_ready: None, tmp_path: Path, payload: dict[str, Any], message: str
) -> None:
    result = run_dbt(
        [
            "compile",
            "--select",
            "test_name:quarantine_rate_levels",
            "--target-path",
            str(tmp_path / "target"),
            "--vars",
            json.dumps({"quarantine_rate_override": payload}),
        ],
        env={"DBT_DUCKDB_PATH": str(tmp_path / "compile.duckdb")},
        check=False,
    )
    assert result.returncode != 0
    # The compiler error starts with the var or test name and a colon, so a test name alone (which
    # also holds `quarantine_rate_levels`) cannot satisfy this.
    assert f"{message}:" in result.stdout + result.stderr

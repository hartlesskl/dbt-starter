"""The demo's error-breach path, end to end in process, and proof that its rates are dbt's.

On the invented fixture files, with the real levels from the project YAML, the real loader and
the real `dbt build` (only `dbt deps` is skipped, because the packages are already installed),
`demo.main` has to fail the way a user's run does: both quarantine-rate levels tests fail at their
error level, gold is skipped, the closing summary is still printed with the skipped relations
named, the docs are not generated and the exit code is dbt's. The same test then checks that the
rates the demo prints are the rates the levels tests compute: the rendered `SAFE_DIVIDE_SQL`
is in both compiled tests, and the rows those tests return equal the printer's scope rates.

Nothing is downloaded and the demo database is never opened: the run uses its own DuckDB file and
its own dbt target folder under the test's temporary directory.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

import duckdb
from support.dbt_cli import REPO_ROOT

from starter_cli import demo
from starter_cli.nyc_taxi import REPORT_CONSERVATION
from starter_cli.nyc_taxi.fixture import EXPECTED, write_fixture
from starter_cli.project import duckdb_path

COUNT_LINE = re.compile(
    r"^(landing|bronze|silver|quarantine|gold|warnings)\s+(\S+)\s+(\d+|skipped|not built)$",
    re.MULTILINE,
)
CONSERVATION_LINE = re.compile(
    r"^\s+(\S+)\s+bronze ([\d,]+) = silver ([\d,]+) \+ quarantine ([\d,]+)\s+(OK|FAIL)$",
    re.MULTILINE,
)
RATE_LINE = re.compile(
    r"^\s+(\S+)\s+quarantine rate [\d.]+% \(([\d,]+) of ([\d,]+) rows\)"
    r"\s+warn [\d.]+%\s+error [\d.]+%\s+(OK|WARN|ERROR)$",
    re.MULTILINE,
)
WORST_LINE = re.compile(
    r"^\s+worst file of (\d+): (\S+)\s+[\d.]+% \(([\d,]+) of ([\d,]+) rows\)\s+(OK|WARN|ERROR)$",
    re.MULTILINE,
)
LEVELS_TESTS = (
    "yellow_trips_quarantine_rate_within_levels",
    "taxi_zones_quarantine_rate_within_levels",
)
SKIPPED_GOLD = ["gold.agg_trips_daily", "gold.dim_zone", "gold.fct_trips"]
RATE_COLUMNS = ("scope", "rejected_rows", "total_rows", "quarantine_rate")


def counts(out: str) -> dict[str, str]:
    """The closing table as `{relation: count text}`; the warnings lines are left out."""
    return {
        relation: count for label, relation, count in COUNT_LINE.findall(out) if label != "warnings"
    }


def test_the_error_breach_stops_gold_prints_the_summary_and_the_rates_are_the_tests(
    dbt_packages_ready, tmp_path, monkeypatch, capsys
):
    write_fixture(tmp_path / "in")
    database = tmp_path / "breach.duckdb"
    target = tmp_path / "target"
    assert database != duckdb_path(REPO_ROOT)
    monkeypatch.setenv("DBT_DUCKDB_PATH", str(database))
    monkeypatch.setenv("DBT_TARGET_PATH", str(target))
    monkeypatch.chdir(REPO_ROOT)
    real_run_dbt = demo._run_dbt
    steps: list[str] = []

    def run_dbt_step(step: str, *args: str) -> int:
        steps.append(step)
        # The packages are installed by the fixture; every other step runs for real.
        return 0 if step == "deps" else real_run_dbt(step, *args)

    monkeypatch.setattr(demo, "_run_dbt", run_dbt_step)
    monkeypatch.setattr(demo, "git_remote_url", lambda _root: None)

    code = demo.main(["--from-dir", str(tmp_path / "in")])
    out = capsys.readouterr()

    # The demo fails like dbt: exit code 1, no docs, the failing step named on stderr.
    assert code == 1
    assert steps == ["deps", "build"]
    assert "demo failed at: dbt build (exit 1)" in out.err
    assert demo.RATE_DISAGREEMENT_NOTE not in out.out
    assert not list(target.rglob("static_index.html"))
    assert "  not generated: dbt build failed" in out.out.splitlines()

    # The counts for what was built, `skipped` for the gold models dbt did not run.
    shown = counts(out.out)
    zones, trips = EXPECTED["taxi_zones"], EXPECTED["yellow_trips"]
    assert shown["silver.taxi_zones"] == str(zones["silver"])
    assert shown["silver.taxi_zones_quarantine"] == str(zones["quarantine"])
    assert shown["silver.yellow_trips"] == str(trips["silver"])
    assert shown["silver.yellow_trips_quarantine"] == str(trips["quarantine"])
    for relation in SKIPPED_GOLD:
        assert shown[relation] == "skipped", f"{relation}: {shown[relation]}"
    lines = out.out.splitlines()
    start = lines.index("skipped")
    assert lines[start + 1 : start + 4] == [f"  {relation}" for relation in SKIPPED_GOLD]

    # Both conservation lines hold, and the rates are past their error levels.
    conservation = CONSERVATION_LINE.findall(out.out)
    assert [(entity, status) for entity, _b, _s, _q, status in conservation] == [
        ("taxi_zones", "OK"),
        ("yellow_trips", "OK"),
    ]
    assert [status for *_rest, status in RATE_LINE.findall(out.out)] == ["ERROR", "ERROR"]
    worst = WORST_LINE.findall(out.out)
    assert [status for *_rest, status in worst] == ["ERROR", "ERROR"]
    assert ("yellow_tripdata_2024-02.parquet", "24", "26") in [(w[1], w[2], w[3]) for w in worst]
    assert "yellow_tripdata_2024-02.parquet" in out.out
    assert "(24 of 26 rows)" in out.out

    # What the build left in the database, read without dbt.
    with duckdb.connect(str(database), read_only=True) as con:
        assert con.execute("select count(*) from silver.yellow_trips").fetchone()[0] == 18
        assert (
            con.execute("select count(*) from silver.yellow_trips_quarantine").fetchone()[0] == 41
        )
        assert_printed_rates_equal_test_rates(con, target)


def assert_printed_rates_equal_test_rates(con: duckdb.DuckDBPyConnection, target: Path) -> None:
    """The rendered SAFE_DIVIDE_SQL is in both compiled levels tests, and the rates are equal.

    The levels tests run in dbt, the printer runs in Python, and the printer carries a copy of the
    macro's SQL. This is the check that keeps the copy honest.
    """
    results = json.loads((target / "run_results.json").read_text(encoding="utf-8"))["results"]
    compiled = {
        result["unique_id"].split(".")[2]: result["compiled_code"]
        for result in results
        if result["unique_id"].split(".")[2] in LEVELS_TESTS
    }
    assert sorted(compiled) == sorted(LEVELS_TESTS)
    fragment = demo.SAFE_DIVIDE_SQL.format(numerator="rejected_rows", denominator="total_rows")
    for name, sql in compiled.items():
        assert fragment in sql, f"{name} does not contain the printer's rate expression"

    expected_scopes = {"taxi_zones": 2, "yellow_trips": 6}
    for entity, test_name in (
        ("taxi_zones", "taxi_zones_quarantine_rate_within_levels"),
        ("yellow_trips", "yellow_trips_quarantine_rate_within_levels"),
    ):
        cursor = con.execute(compiled[test_name])
        names = [column[0] for column in cursor.description]
        assert set(RATE_COLUMNS) <= set(names), names
        picks = [names.index(column) for column in RATE_COLUMNS]
        from_test = {tuple(row[i] for i in picks) for row in cursor.fetchall()}
        bronze, silver, _quarantine = REPORT_CONSERVATION[entity]
        whole, files = demo.scope_rates(con, bronze, silver)
        from_printer = {
            (scope.scope, scope.rejected, scope.total, scope.rate) for scope in [whole, *files]
        }
        assert from_test == from_printer, (
            f"{entity}: the test returns {sorted(from_test, key=str)} "
            f"but the printer computes {sorted(from_printer, key=str)}"
        )
        assert len(from_test) == expected_scopes[entity]
        assert all(isinstance(rate, Decimal) for *_rest, rate in from_test)

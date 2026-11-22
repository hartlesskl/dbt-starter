"""The calendar dimension is right on every row and refuses a short or malformed span (GOLD-02).

The calendar attributes come from sequential CTEs over the `digits` seed and from the starter
shims, never from an engine weekday or locale function. So each attribute is compared with
DuckDB's own date functions over every row of the fixture build. A mismatch would mean the
shim arithmetic is wrong, which would be wrong on Fabric too.

The span test breaks a copy of the built database, so a test that has never been seen to fail
is not trusted. Every dbt run uses its own `--target-path` and its own database file, so the
demo database and the gate database are never changed.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import duckdb
from conventions.test_portability import JINJA_COMMENT_RE
from support.dbt_cli import run_dbt

DIM_DATE_ID = "model.dbt_starter.gold__dim_date"
SPAN_TEST = "dim_date_covers_the_configured_span"
EXPECTED_ROWS = 8035

# One query that counts rows where a calendar column differs from DuckDB's own function.
ATTRIBUTE_MISMATCHES = """
select
    count(*) as total_rows,
    count(*) filter (where day_of_week <> isodow(date_day)) as weekday_wrong,
    count(*) filter (where week_start_date <> cast(date_trunc('week', date_day) as date))
        as week_start_wrong,
    count(*) filter (where month_name <> monthname(date_day)) as month_name_wrong,
    count(*) filter (where day_name <> dayname(date_day)) as day_name_wrong,
    count(*) filter (where calendar_year <> year(date_day)) as year_wrong,
    count(*) filter (where calendar_quarter <> quarter(date_day)) as quarter_wrong,
    count(*) filter (where calendar_month <> month(date_day)) as month_wrong,
    count(*) filter (where day_of_month <> day(date_day)) as day_wrong,
    count(*) filter (
        where date_key <> year(date_day) * 10000 + month(date_day) * 100 + day(date_day)
    ) as date_key_wrong,
    count(*) filter (
        where is_weekend <> case when isodow(date_day) in (6, 7) then 1 else 0 end
    ) as weekend_wrong
from gold.dim_date
"""


def _query_one(database: Path, sql: str) -> tuple[Any, ...]:
    con = duckdb.connect(str(database), read_only=True)
    try:
        row = con.execute(sql).fetchone()
    finally:
        con.close()
    assert row is not None
    return row


def _span_status(database: Path, work: Path) -> str:
    """Run only the span test on `database` and return its status."""
    target = work / "target"
    run_dbt(
        ["test", "--select", SPAN_TEST, "--target-path", str(target)],
        env={"DBT_DUCKDB_PATH": str(database)},
        check=False,
    )
    results = json.loads((target / "run_results.json").read_text())["results"]
    statuses = {result["unique_id"].split(".")[2]: result["status"] for result in results}
    assert list(statuses) == [SPAN_TEST], f"expected only {SPAN_TEST}, got {list(statuses)}"
    return statuses[SPAN_TEST]


def _compile_with_vars(work: Path, variables: dict[str, str]) -> tuple[int, str]:
    """Compile only the calendar model with overridden vars; return the exit code and output."""
    result = run_dbt(
        [
            "compile",
            "--select",
            "gold__dim_date",
            "--vars",
            json.dumps(variables),
            "--target-path",
            str(work / "target"),
        ],
        env={"DBT_DUCKDB_PATH": str(work / "guard.duckdb")},
        check=False,
    )
    return result.returncode, result.stdout + result.stderr


def test_every_calendar_attribute_matches_duckdb_on_every_row(fixture_build: Any) -> None:
    row = _query_one(fixture_build.database, ATTRIBUTE_MISMATCHES)
    columns = [
        "total_rows",
        "weekday_wrong",
        "week_start_wrong",
        "month_name_wrong",
        "day_name_wrong",
        "year_wrong",
        "quarter_wrong",
        "month_wrong",
        "day_wrong",
        "date_key_wrong",
        "weekend_wrong",
    ]
    counts = dict(zip(columns, row, strict=True))
    assert counts["total_rows"] == EXPECTED_ROWS
    wrong = {name: count for name, count in counts.items() if name != "total_rows" and count}
    assert wrong == {}


def test_the_calendar_is_built_from_the_digits_seed_and_no_package_macro(
    duckdb_manifest: dict,
) -> None:
    node = duckdb_manifest["nodes"][DIM_DATE_ID]
    depends = node["depends_on"]
    assert depends["nodes"] == ["seed.dbt_starter.digits"]
    package_macros = [
        macro
        for macro in depends["macros"]
        if macro.startswith(("macro.dbt_utils.", "macro.dbt_date."))
    ]
    assert package_macros == []
    code = JINJA_COMMENT_RE.sub("", node["raw_code"])
    assert "recursive" not in code.lower()


def test_the_span_test_passes_on_the_build(fixture_build: Any, tmp_path: Path) -> None:
    database = tmp_path / "copy.duckdb"
    shutil.copyfile(fixture_build.database, database)
    assert _span_status(database, tmp_path) == "pass"


def test_the_span_test_fails_when_one_day_is_deleted(fixture_build: Any, tmp_path: Path) -> None:
    database = tmp_path / "copy.duckdb"
    shutil.copyfile(fixture_build.database, database)
    con = duckdb.connect(str(database))
    try:
        con.execute("delete from gold.dim_date where date_key = 20240229")
    finally:
        con.close()
    assert _span_status(database, tmp_path) == "fail"


def test_a_span_above_ten_thousand_days_stops_compilation(
    dbt_packages_ready: None, tmp_path: Path
) -> None:
    code, output = _compile_with_vars(tmp_path, {"dim_date_end": "2040-01-01"})
    assert code != 0
    assert "10,000" in output


def test_a_var_that_is_not_a_date_stops_compilation_and_is_named(
    dbt_packages_ready: None, tmp_path: Path
) -> None:
    code, output = _compile_with_vars(tmp_path, {"dim_date_start": "2009-13-01"})
    assert code != 0
    assert "dim_date_start" in output
    assert "2009-13-01" in output


def test_a_day_that_does_not_exist_stops_compilation_and_is_named(
    dbt_packages_ready: None, tmp_path: Path
) -> None:
    code, output = _compile_with_vars(tmp_path, {"dim_date_end": "2023-02-31"})
    assert code != 0
    assert "dim_date_end" in output
    assert "2023-02-31" in output


def test_the_last_year_stops_compilation_with_a_message_that_names_the_var(
    dbt_packages_ready: None, tmp_path: Path
) -> None:
    code, output = _compile_with_vars(tmp_path, {"dim_date_end": "9999-12-31"})
    assert code != 0
    assert "var dim_date_end must have a year from 1000 to 9998, not 9999-12-31" in output
    assert "out of range" not in output


def test_an_end_before_the_start_stops_compilation(
    dbt_packages_ready: None, tmp_path: Path
) -> None:
    code, output = _compile_with_vars(tmp_path, {"dim_date_end": "2008-12-31"})
    assert code != 0
    assert "before" in output
    assert "dim_date_end" in output


def test_a_valid_span_still_compiles(dbt_packages_ready: None, tmp_path: Path) -> None:
    code, output = _compile_with_vars(
        tmp_path, {"dim_date_start": "2020-01-01", "dim_date_end": "2020-12-31"}
    )
    assert code == 0, output

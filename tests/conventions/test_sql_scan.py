"""The scan must catch every Spark-rejected construct the portable subset bans, and pass clean SQL.

Each bad case proves one Spark rule fires. The clean cases prove that the rules dropped with the
old Warehouse target (nested WITH, mixed-case names, one-value CONCAT) no longer fire. Without
these, an empty or broken scan would look the same as a clean project.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from support.sql_scan import BANNED_TYPES, scan, scan_compiled_dir

BAD_CASES = {
    "qualify": "select a from t qualify row_number() over (order by a) = 1",
    "star_exclude": "select * exclude (a) from t",
    "star_replace": "select * replace (1 as a) from t",
    "union_by_name": "select a from t union all by name select a from u",
    "distinct_on": "select distinct on (a) a, b from t",
    "unknown_function": "select list_agg(a) from t",
    "timestamp_ntz": "select cast(x as timestamp_ntz) as y from t",
    "hugeint": "select cast(x as hugeint) as y from t",
    "ubigint": "select cast(x as ubigint) as y from t",
    "uuid": "select cast(x as uuid) as y from t",
    "time": "select cast(x as time) as y from t",
    "interval": "select cast(x as interval) as y from t",
    "bit": "select cast(x as bit) as y from t",
    "unparseable": "select from from where",
}

CLEAN_CASES = {
    "sequential_ctes": ("with a as (select 1 as x), b as (select x from a) select x from b"),
    "smallint_and_decimal": "select cast(x as smallint) as a, cast(y as decimal(18, 6)) as b from t",
    "concat_ws_two_values": "select concat_ws('-', a, b) from t",
    "concat_two_values": "select concat(a, b) from t",
    "nested_with_in_cte": ("with a as (with b as (select 1 as x) select x from b) select x from a"),
    "nested_with_in_subquery": ("select x from (with b as (select 1 as x) select x from b) as s"),
    "mixed_case_identifiers": "select MyCol from MySchema.MyTable",
    "backtick_names": "select a from `bronze`.`t`",
    "one_value_concat": "select concat(a) from t",
    "one_value_concat_ws": "select concat_ws('-', a) from t",
    "string_and_varchar_casts": (
        "select cast(x as string) as a, cast(y as varchar(10)) as b from t"
    ),
    "timestamp_cast": "select cast(x as timestamp) as y from t",
    "group_by_all": "select a, count(*) as n from t group by all",
    "regexp_like": "select regexp_like(a, '^[0-9]+$') as ok from t",
    "timestampdiff_second": (
        "select timestampdiff(second, date_trunc('second', a), date_trunc('second', b)) from t"
    ),
    "translate_guard": "select translate(a, ' 0123456789', '') = '' as ok from t",
    "try_cast_decimal": "select try_cast(a as decimal(18, 6)) as y from t",
    "md5_concat_ws": "select md5(concat_ws('-', coalesce(cast(a as string), '_null_'))) from t",
}


def test_the_case_tables_meet_the_minimum_counts() -> None:
    assert len(BAD_CASES) >= 14
    assert len(CLEAN_CASES) >= 15


@pytest.mark.parametrize("sql", BAD_CASES.values(), ids=BAD_CASES.keys())
def test_bad_sql_yields_at_least_one_problem(sql: str) -> None:
    assert scan(sql), f"the scan found nothing in: {sql}"


@pytest.mark.parametrize("sql", CLEAN_CASES.values(), ids=CLEAN_CASES.keys())
def test_clean_sql_yields_no_problem(sql: str) -> None:
    assert scan(sql) == []


def test_the_banned_types_are_the_spark_set() -> None:
    names = {t.name for t in BANNED_TYPES}
    assert names == {
        "BIT",
        "ENUM",
        "INT128",
        "INTERVAL",
        "TIME",
        "TIMESTAMPNTZ",
        "UBIGINT",
        "UINT",
        "UINT128",
        "USMALLINT",
        "UTINYINT",
        "UUID",
    }
    # Spark's `string` parses as TEXT, so banning it would reject every compiled model.
    assert "TEXT" not in names


@pytest.mark.parametrize(
    ("case", "needle"),
    [
        ("qualify", "QUALIFY"),
        ("star_exclude", "DuckDB-only"),
        ("star_replace", "DuckDB-only"),
        ("union_by_name", "UNION BY NAME"),
        ("distinct_on", "DISTINCT ON"),
        ("unknown_function", "list_agg"),
        ("timestamp_ntz", "TIMESTAMPNTZ"),
        ("hugeint", "INT128"),
        ("uuid", "UUID"),
        ("time", "TIME"),
        ("interval", "INTERVAL"),
        ("bit", "BIT"),
        ("unparseable", "unparseable SQL"),
    ],
)
def test_problem_text_names_the_rule(case: str, needle: str) -> None:
    assert any(needle in problem for problem in scan(BAD_CASES[case]))


def test_scan_compiled_dir_reports_only_files_with_problems(tmp_path: Path) -> None:
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "good.sql").write_text(CLEAN_CASES["sequential_ctes"])
    (tmp_path / "models" / "bad.sql").write_text(BAD_CASES["qualify"])
    result = scan_compiled_dir(tmp_path)
    assert list(result) == [str(Path("models") / "bad.sql")]
    assert result[str(Path("models") / "bad.sql")]


def test_scan_compiled_dir_is_empty_for_a_clean_tree(tmp_path: Path) -> None:
    (tmp_path / "a.sql").write_text(CLEAN_CASES["timestamp_cast"])
    assert scan_compiled_dir(tmp_path) == {}

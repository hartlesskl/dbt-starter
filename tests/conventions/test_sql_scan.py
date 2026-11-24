"""The scan must catch every Spark-rejected construct the portable subset bans, and pass clean SQL.

Each bad case proves one Spark rule fires. The clean cases prove that the rules dropped with the
old Warehouse target (nested WITH, mixed-case names, one-value CONCAT) no longer fire. Without
these, an empty or broken scan would look the same as a clean project.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from support.sql_scan import ALLOWED_FUNCTIONS, BANNED_TYPES, scan, scan_compiled_dir

BAD_CASES = {
    "qualify": "select a from t qualify row_number() over (order by a) = 1",
    "star_exclude": "select * exclude (a) from t",
    "star_replace": "select * replace (1 as a) from t",
    "union_by_name": "select a from t union all by name select a from u",
    "distinct_on": "select distinct on (a) a, b from t",
    "unknown_function": "select list_agg(a) from t",
    # The WR-02 probes of the 04.1 review that are DuckDB-only. Each passed the old scan.
    "dcolon_cast": "select a::int as y from t",
    "dcolon_cast_in_expression": "select (a + 1)::decimal(18, 6) as y from t",
    "string_agg": "select string_agg(a, ',') from t",
    "list_function": "select list(a) from t",
    "unnest": "select unnest(a) from t",
    "generate_series": "select generate_series(1, 3)",
    "asof_join": "select * from a asof join b on a.x >= b.x",
    "using_sample": "select * from t using sample 10%",
    "limit_percent": "select * from t limit 10%",
    "duckdb_pivot_statement": "pivot t on b using sum(a)",
    "date_diff_unit_first": "select date_diff('day', a, b) from t",
    "datediff_unit_first": "select datediff('day', a, b) from t",
    "unlisted_duckdb_function": "select list_aggr(a, 'sum') from t",
    "timestamp_ntz": "select cast(x as timestamp_ntz) as y from t",
    "hugeint": "select cast(x as hugeint) as y from t",
    "ubigint": "select cast(x as ubigint) as y from t",
    "uuid": "select cast(x as uuid) as y from t",
    "time": "select cast(x as time) as y from t",
    "interval": "select cast(x as interval) as y from t",
    "bit": "select cast(x as bit) as y from t",
    "information_schema_columns": (
        "select column_name from information_schema.columns where table_name = 't'"
    ),
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
    # Spark has date_part, so the old "not in the shared subset" report was a false positive.
    "date_part": "select date_part('year', a) as y from t",
    # These WR-02 probes turned out to be valid Spark: Spark accepts a from-first select, a
    # PIVOT clause after a table, TABLESAMPLE with a percent, and datediff(end, start).
    "from_first_select": "from t select a",
    "spark_pivot_clause": "select * from t pivot (sum(a) for b in ('x', 'y'))",
    "tablesample_percent": "select * from t tablesample (10 percent)",
    "datediff_two_arguments": "select datediff(b, a) from t",
    "join_using": "select * from a join b using (x)",
    "cast_function": "select cast(a as int) as y from t",
}


def test_the_case_tables_meet_the_minimum_counts() -> None:
    assert len(BAD_CASES) >= 28
    assert len(CLEAN_CASES) >= 24


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
        ("dcolon_cast", "::"),
        ("dcolon_cast_in_expression", "cast operator"),
        ("string_agg", "not in the allowed functions"),
        ("list_function", "function list "),
        ("unnest", "function unnest "),
        ("generate_series", "function generate_series "),
        ("asof_join", "ASOF JOIN"),
        ("using_sample", "USING SAMPLE"),
        ("limit_percent", "PERCENT"),
        ("duckdb_pivot_statement", "PIVOT"),
        ("date_diff_unit_first", "unit"),
        ("datediff_unit_first", "unit"),
        ("unlisted_duckdb_function", "list_aggr"),
        ("timestamp_ntz", "TIMESTAMPNTZ"),
        ("hugeint", "INT128"),
        ("uuid", "UUID"),
        ("time", "TIME"),
        ("interval", "INTERVAL"),
        ("bit", "BIT"),
        ("information_schema_columns", "information_schema"),
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


def test_every_allowed_function_name_is_lower_case_and_sorted() -> None:
    names = sorted(ALLOWED_FUNCTIONS)
    assert names == [name.lower() for name in names]
    assert len(names) >= 20
    assert {"date_part", "try_cast", "regexp_like", "concat_ws"} <= set(names)


def test_a_function_outside_the_list_is_named_in_the_problem() -> None:
    problems = scan("select some_new_function(a) from t")
    assert problems == [
        "function some_new_function is not in the allowed functions; add it to ALLOWED_FUNCTIONS "
        "only after checking that Spark has it"
    ]

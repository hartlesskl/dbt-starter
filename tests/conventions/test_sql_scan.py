"""The scan must catch every construct the portable subset bans, and pass clean SQL.

Each bad case proves one rule fires. Without these, an empty or broken scan would look the
same as a clean project.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from support.sql_scan import BANNED_TYPES, scan, scan_compiled_dir

BAD_CASES = {
    "nested_with_in_cte": ("with a as (with b as (select 1 as x) select x from b) select x from a"),
    "nested_with_in_subquery": ("select x from (with b as (select 1 as x) select x from b) as s"),
    "qualify": "select a from t qualify row_number() over (order by a) = 1",
    "nvarchar": "select cast(x as nvarchar(10)) as y from t",
    "nchar": "select cast(x as nchar(2)) as y from t",
    "tinyint": "select cast(x as tinyint) as y from t",
    "datetime": "select cast(x as datetime) as y from t",
    "smalldatetime": "select cast(x as smalldatetime) as y from t",
    "money": "select cast(x as money) as y from t",
    "smallmoney": "select cast(x as smallmoney) as y from t",
    "bit": "select cast(x as bit) as y from t",
    "text": "select cast(x as text) as y from t",
    "mixed_case_column": "select MyCol from t",
    "mixed_case_table": "select a from dbo.MyTable",
    "mixed_case_schema": "select a from MySchema.t",
    "quoted_mixed_case": "select [MyCol] from t",
    "single_value_concat": "select concat(a) from t",
    "single_value_concat_ws": "select concat_ws('-', a) from t",
    "unparseable": "select from from where",
}

CLEAN_CASES = {
    "sequential_ctes": ("with a as (select 1 as x), b as (select x from a) select x from b"),
    "datetime2": "select cast(x as datetime2(6)) as y from t",
    "varchar_max": "select cast(x as varchar(max)) as y from t",
    "smallint_and_decimal": "select cast(x as smallint) as a, cast(y as decimal(18, 6)) as b from t",
    "catalog_part_is_exempt": "select a from [MyWarehouse].[bronze].[t]",
    "concat_ws_two_values": "select concat_ws('-', a, b) from t",
    "concat_two_values": "select concat(a, b) from t",
    "typed_null_padding": "select concat_ws('-', a, cast(null as varchar(1))) from t",
    "quoted_lowercase": "select [a] from [bronze].[t]",
}


def test_the_case_tables_meet_the_minimum_counts() -> None:
    assert len(BAD_CASES) >= 15
    assert len(CLEAN_CASES) >= 5


@pytest.mark.parametrize("sql", BAD_CASES.values(), ids=BAD_CASES.keys())
def test_bad_sql_yields_at_least_one_problem(sql: str) -> None:
    assert scan(sql), f"the scan found nothing in: {sql}"


@pytest.mark.parametrize("sql", CLEAN_CASES.values(), ids=CLEAN_CASES.keys())
def test_clean_sql_yields_no_problem(sql: str) -> None:
    assert scan(sql) == []


def test_every_banned_type_has_a_bad_case() -> None:
    names = {t.name for t in BANNED_TYPES}
    for needed in ("NVARCHAR", "NCHAR", "DATETIME", "SMALLDATETIME", "MONEY", "BIT", "TEXT"):
        assert needed in names


def test_problem_text_names_the_rule() -> None:
    assert any("nested WITH" in p for p in scan(BAD_CASES["nested_with_in_cte"]))
    assert any("QUALIFY" in p for p in scan(BAD_CASES["qualify"]))
    assert any("NVARCHAR" in p for p in scan(BAD_CASES["nvarchar"]))
    assert any("MyCol" in p for p in scan(BAD_CASES["mixed_case_column"]))
    assert any("CONCAT" in p for p in scan(BAD_CASES["single_value_concat"]))
    assert any("unparseable SQL" in p for p in scan(BAD_CASES["unparseable"]))


def test_scan_compiled_dir_reports_only_files_with_problems(tmp_path: Path) -> None:
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "good.sql").write_text(CLEAN_CASES["sequential_ctes"])
    (tmp_path / "models" / "bad.sql").write_text(BAD_CASES["qualify"])
    result = scan_compiled_dir(tmp_path)
    assert list(result) == [str(Path("models") / "bad.sql")]
    assert result[str(Path("models") / "bad.sql")]


def test_scan_compiled_dir_is_empty_for_a_clean_tree(tmp_path: Path) -> None:
    (tmp_path / "a.sql").write_text(CLEAN_CASES["datetime2"])
    assert scan_compiled_dir(tmp_path) == {}

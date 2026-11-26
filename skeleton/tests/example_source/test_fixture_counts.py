"""The placeholder source's fixture reproduces its exact counts on the gate's build (TEST-04).

The fixture rows are `FIXTURE_ROWS` in `starter_cli.example_source`. Each row's expected failures
are derived here by plain Python predicates, written apart from the SQL rules, and every count
follows from those derived failures: there are no hand-typed totals. Three things are proven:

1. Coverage: every code the catalog registers for the entity fails at least one fixture row, and
   every derived code is a registered code.
2. The build: landing the fixture through the real loader and running `dbt build` gives the
   derived counts in landing, bronze, silver, quarantine and gold, and per row.
3. The build has no failing data test and no warning.

The build runs on the gate's own DuckDB file (`GATE_DATABASE`) or a fresh one under a temporary
folder, never on the demo database, and nothing is downloaded. When you replace this source with
your own, copy the pattern: tag each fixture row with the rules it plants, derive the counts, and
compare them with what the build holds.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

import duckdb
import pytest
from support.dbt_cli import node_results

from starter_cli import example_source
from starter_cli.gate import FixtureBuild

pytestmark = pytest.mark.gate_counts

FIXTURE_ROWS = example_source.FIXTURE_ROWS
FIXTURE_UNDECLARED_COLUMNS = example_source.FIXTURE_UNDECLARED_COLUMNS
ROWS = example_source.ROWS

ENTITY = "items"
LANDING = "landing.example_source__items"
BRONZE = "bronze.example_source__items"
SILVER = "silver.items"
QUARANTINE = "silver.items_quarantine"
GOLD = "gold.fct_items"
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


def _is_integer(text: str) -> bool:
    return re.fullmatch(r"[0-9]+", text) is not None


def _is_number(text: str) -> bool:
    try:
        Decimal(text)
    except InvalidOperation:
        return False
    return re.fullmatch(r"-?[0-9]+(\.[0-9]+)?", text) is not None


def planted(row: dict[str, str]) -> tuple[str, ...]:
    """The rule codes this row should fail, in rule order, from the row's text alone."""
    codes = []
    if row["id"] == "":
        codes.append("ITEM_ID_MISSING")
    elif not _is_integer(row["id"]):
        codes.append("ITEM_ID_UNPARSEABLE")
    if row["amount"] != "" and not _is_number(row["amount"]):
        codes.append("ITEM_AMOUNT_UNPARSEABLE")
    if row["updated_at"] != "" and not TIMESTAMP_RE.match(row["updated_at"]):
        codes.append("ITEM_UPDATED_AT_UNPARSEABLE")
    return tuple(codes)


TAGS = [planted(row) for row in FIXTURE_ROWS]
EXPECTED_LANDING = len(FIXTURE_ROWS)
EXPECTED_QUARANTINE = sum(1 for codes in TAGS if codes)
EXPECTED_SILVER = EXPECTED_LANDING - EXPECTED_QUARANTINE
EXPECTED_GOLD = EXPECTED_SILVER
EXPECTED_RULES: dict[str, int] = {}
for _codes in TAGS:
    for _code in _codes:
        EXPECTED_RULES[_code] = EXPECTED_RULES.get(_code, 0) + 1


def scalar(con: duckdb.DuckDBPyConnection, sql: str) -> object:
    return con.execute(sql).fetchone()[0]


@pytest.fixture(scope="module")
def con(fixture_build: FixtureBuild):
    connection = duckdb.connect(str(fixture_build.database), read_only=True)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture(scope="module")
def catalog(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    """The registered rule codes of the entity with their severity, from the built catalog."""
    rows = con.execute(
        f"select code, severity from silver.dq_rule_catalog where entity = '{ENTITY}'"
    ).fetchall()
    return dict(rows)


# --- The fixture rows: no database needed ---


def test_the_demo_rows_hold_exactly_one_bad_row() -> None:
    assert sum(1 for row in ROWS if planted(row)) == 1


def test_the_fixture_is_the_demo_rows_plus_planted_rows() -> None:
    assert FIXTURE_ROWS[: len(ROWS)] == ROWS
    assert len(FIXTURE_ROWS) > len(ROWS)


def test_the_fixture_plants_no_undeclared_column() -> None:
    assert FIXTURE_UNDECLARED_COLUMNS == 0


def test_a_planted_row_names_one_code_and_a_clean_row_names_none() -> None:
    assert TAGS[: len(ROWS) - 1] == [()] * (len(ROWS) - 1)
    assert all(len(codes) <= 1 for codes in TAGS)


# --- The build reproduces the derived counts ---


def test_every_registered_code_fails_at_least_one_fixture_row_and_every_tag_is_registered(
    catalog: dict[str, str],
) -> None:
    assert sorted(EXPECTED_RULES) == sorted(catalog)
    assert catalog, "the catalog holds no rule for the entity"
    assert all(count >= 1 for count in EXPECTED_RULES.values())
    assert all(code.startswith("ITEM_") for code in catalog)


def test_the_summary_failed_rows_equal_the_derived_counts_per_rule(
    con: duckdb.DuckDBPyConnection, catalog: dict[str, str]
) -> None:
    rows = con.execute(
        f"select rule_code, failed_rows from silver.dq_quarantine_summary where entity = '{ENTITY}'"
    ).fetchall()
    actual = {code: int(failed) for code, failed in rows}
    assert not [code for code in catalog if code not in actual]
    assert {code: count for code, count in actual.items() if count} == EXPECTED_RULES


def test_landing_bronze_silver_quarantine_and_gold_counts_equal_the_derived_counts(
    con: duckdb.DuckDBPyConnection,
) -> None:
    assert scalar(con, f"select count(*) from {LANDING}") == EXPECTED_LANDING
    assert scalar(con, f"select count(*) from {BRONZE}") == EXPECTED_LANDING
    assert scalar(con, f"select count(*) from {SILVER}") == EXPECTED_SILVER
    assert scalar(con, f"select count(*) from {QUARANTINE}") == EXPECTED_QUARANTINE
    assert scalar(con, f"select count(*) from {GOLD}") == EXPECTED_GOLD


def test_every_row_fails_exactly_the_codes_it_is_tagged_with(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Per row, not per count: a count can match by luck, a row-by-row match cannot."""
    cursor = con.execute(f"select * from {BRONZE}")
    names = [column[0] for column in cursor.description]
    flags = {
        name: name.removeprefix("dq_fail_").upper()
        for name in names
        if name.startswith("dq_fail_") and name != "dq_failed_rule_count"
    }
    actual: dict[int, tuple[str, ...]] = {}
    for record in cursor.fetchall():
        row = dict(zip(names, record, strict=True))
        actual[int(row["_source_row_number"])] = tuple(
            sorted(code for name, code in flags.items() if row[name] == 1)
        )
    expected = {number: tuple(sorted(codes)) for number, codes in enumerate(TAGS, start=1)}
    assert actual == expected


def test_silver_and_quarantine_share_no_row_and_hold_every_row(
    con: duckdb.DuckDBPyConnection,
) -> None:
    both = scalar(
        con,
        f"select count(*) from {SILVER} s join {QUARANTINE} q on s.item_key = q.item_key",
    )
    assert both == 0
    assert EXPECTED_SILVER + EXPECTED_QUARANTINE == EXPECTED_LANDING


def test_the_gold_fact_has_the_same_keys_as_silver(con: duckdb.DuckDBPyConnection) -> None:
    gold_only = con.execute(f"select item_key from {GOLD} except select item_key from {SILVER}")
    silver_only = con.execute(f"select item_key from {SILVER} except select item_key from {GOLD}")
    assert gold_only.fetchall() == []
    assert silver_only.fetchall() == []


def test_the_build_has_no_failing_test_and_no_warning(fixture_build: FixtureBuild) -> None:
    results = node_results(fixture_build.run_results)
    bad = [r["unique_id"] for r in results if r["status"] in ("error", "fail", "skipped")]
    assert bad == []
    warned = [r["unique_id"].split(".")[2] for r in results if r["status"] == "warn"]
    assert len(warned) == FIXTURE_UNDECLARED_COLUMNS

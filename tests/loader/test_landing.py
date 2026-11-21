"""Landing guarantees (LAND-02, LAND-03, LAND-04) proven on a synthetic source.

No network and no example dataset: every test lands a small CSV built in tmp_path into a
temporary DuckDB file.
"""

from __future__ import annotations

import csv
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from starter_cli.landing import EntitySpec, LandingError, ensure_landing_table, land_file

SPEC = EntitySpec(
    source="acme",
    entity="widgets",
    url="https://example.invalid/widgets.csv",
    file_name="widgets.csv",
    columns=("code", "name", "note"),
    header_prefix=b'"Code"',
)
TABLE = f"landing.{SPEC.table}"
METADATA = ("_source_file", "_source_row_number", "_batch_id", "_loaded_at")


def write_csv(path: Path, header: list[str], rows: list[list[str]]) -> Path:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_ALL)
        writer.writerow(header)
        writer.writerows(rows)
    return path


@pytest.fixture
def con(tmp_path):
    connection = duckdb.connect(str(tmp_path / "t.duckdb"))
    ensure_landing_table(connection, SPEC)
    yield connection
    connection.close()


def land(con, tmp_path, rows, *, header=None, name="widgets.csv") -> int:
    path = write_csv(tmp_path / name, header or ["Code", "Name", "Note"], rows)
    return land_file(con, SPEC, path)


def test_headers_land_in_lowercase_columns(con, tmp_path):
    land(con, tmp_path, [["A1", "Alpha", "first"]])
    columns = [row[0] for row in con.execute(f"describe {TABLE}").fetchall()]
    assert columns[:3] == ["code", "name", "note"]
    assert con.execute(f"select code, name, note from {TABLE}").fetchall() == [
        ("A1", "Alpha", "first")
    ]


def test_header_differing_only_by_case_lands_in_the_lowercase_column(con, tmp_path):
    land(con, tmp_path, [["A1", "Alpha", "first"]], header=["CODE", "nAmE", "NOTE"])
    assert con.execute(f"select code, name, note from {TABLE}").fetchall() == [
        ("A1", "Alpha", "first")
    ]


def test_empty_cells_land_as_null_quoted_or_not(con, tmp_path):
    path = tmp_path / "widgets.csv"
    # Row 1 has an unquoted empty cell, row 2 a quoted empty cell.
    path.write_text('"Code","Name","Note"\nA1,,x\nA2,"",y\n', encoding="utf-8")
    land_file(con, SPEC, path)
    rows = con.execute(f"select code, name, note from {TABLE} order by code").fetchall()
    assert rows == [("A1", None, "x"), ("A2", None, "y")]


def test_published_text_is_kept_as_written(con, tmp_path):
    land(
        con,
        tmp_path,
        [["N/A", "Unknown", "EWR"], ["Z1", "Zürich", "naïve café"]],
    )
    rows = con.execute(f"select code, name, note from {TABLE} order by code").fetchall()
    assert rows == [("N/A", "Unknown", "EWR"), ("Z1", "Zürich", "naïve café")]


def test_source_row_number_follows_file_order_not_sorted_order(con, tmp_path):
    rows = [[f"C{n:03d}", f"name{n}", ""] for n in range(50, 0, -1)]
    land(con, tmp_path, rows)
    landed = con.execute(
        f"select _source_row_number, code from {TABLE} order by _source_row_number"
    ).fetchall()
    assert [number for number, _ in landed] == list(range(1, 51))
    assert landed[0][1] == "C050"
    assert landed[-1][1] == "C001"


def test_all_metadata_columns_are_non_null_even_for_an_all_empty_row(con, tmp_path):
    land(con, tmp_path, [["", "", ""], ["A1", "Alpha", "first"]])
    for column in METADATA:
        nulls = con.execute(f"select count(*) from {TABLE} where {column} is null").fetchone()[0]
        assert nulls == 0, column
    assert con.execute(f"select count(*) from {TABLE}").fetchone()[0] == 2


def test_batch_id_is_the_sha256_prefix_of_the_file_bytes(con, tmp_path):
    path = write_csv(tmp_path / "widgets.csv", ["Code", "Name", "Note"], [["A1", "Alpha", "x"]])
    land_file(con, SPEC, path)
    expected = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    assert con.execute(f"select distinct _batch_id from {TABLE}").fetchall() == [(expected,)]


def test_identical_bytes_share_a_batch_id_and_a_one_byte_change_does_not(con, tmp_path):
    rows = [["A1", "Alpha", "x"]]
    first = write_csv(tmp_path / "one.csv", ["Code", "Name", "Note"], rows)
    same = write_csv(tmp_path / "two.csv", ["Code", "Name", "Note"], rows)
    changed = write_csv(tmp_path / "three.csv", ["Code", "Name", "Note"], [["A1", "Alpha", "y"]])
    for path in (first, same, changed):
        land_file(con, SPEC, path)
    ids = dict(con.execute(f"select distinct _source_file, _batch_id from {TABLE}").fetchall())
    assert ids["one.csv"] == ids["two.csv"]
    assert ids["one.csv"] != ids["three.csv"]


def test_every_row_of_one_load_shares_one_naive_utc_loaded_at(con, tmp_path):
    land(con, tmp_path, [[f"C{n}", "n", ""] for n in range(20)])
    values = con.execute(f"select distinct _loaded_at from {TABLE}").fetchall()
    assert len(values) == 1
    loaded_at = values[0][0]
    assert loaded_at.tzinfo is None
    now_utc = datetime.now(UTC).replace(tzinfo=None)
    assert abs(now_utc - loaded_at) < timedelta(seconds=60)


def test_landing_the_same_file_twice_keeps_the_same_count(con, tmp_path):
    rows = [["A1", "Alpha", "x"], ["A2", "Beta", "y"]]
    assert land(con, tmp_path, rows) == 2
    assert land(con, tmp_path, rows) == 2
    assert con.execute(f"select count(*) from {TABLE}").fetchone()[0] == 2


def test_changed_content_replaces_only_that_files_rows(con, tmp_path):
    land(con, tmp_path, [["A1", "Alpha", "x"], ["A2", "Beta", "y"]], name="one.csv")
    land(con, tmp_path, [["B1", "Other", "z"]], name="two.csv")
    assert land(con, tmp_path, [["A9", "Replaced", "w"]], name="one.csv") == 1
    by_file = dict(con.execute(f"select _source_file, count(*) from {TABLE} group by 1").fetchall())
    assert by_file == {"one.csv": 1, "two.csv": 1}
    codes = con.execute(f"select code from {TABLE} order by code").fetchall()
    assert codes == [("A9",), ("B1",)]


def test_header_only_file_removes_that_files_earlier_rows(con, tmp_path):
    land(con, tmp_path, [["A1", "Alpha", "x"]], name="one.csv")
    land(con, tmp_path, [["B1", "Other", "z"]], name="two.csv")
    assert land(con, tmp_path, [], name="one.csv") == 0
    by_file = dict(con.execute(f"select _source_file, count(*) from {TABLE} group by 1").fetchall())
    assert by_file == {"two.csv": 1}


def test_headers_that_collide_after_lowercasing_name_both(con, tmp_path):
    path = write_csv(
        tmp_path / "widgets.csv", ["Code", "CODE", "Name", "Note"], [["A", "B", "C", "D"]]
    )
    with pytest.raises(LandingError) as excinfo:
        land_file(con, SPEC, path)
    assert "'Code'" in str(excinfo.value)
    assert "'CODE'" in str(excinfo.value)
    assert con.execute(f"select count(*) from {TABLE}").fetchone()[0] == 0


def test_a_missing_column_is_named_in_the_error(con, tmp_path):
    path = write_csv(tmp_path / "widgets.csv", ["Code", "Name"], [["A1", "Alpha"]])
    with pytest.raises(LandingError) as excinfo:
        land_file(con, SPEC, path)
    assert "'note'" in str(excinfo.value)


class InsertFailsConnection:
    """Delegates to a real connection but raises on the insert statement."""

    def __init__(self, real):
        self._real = real

    def execute(self, sql, params=None):
        if sql.strip().lower().startswith("insert"):
            raise RuntimeError("boom")
        if params is None:
            return self._real.execute(sql)
        return self._real.execute(sql, params)


def test_a_failed_insert_rolls_back_the_delete(con, tmp_path):
    path = write_csv(
        tmp_path / "widgets.csv", ["Code", "Name", "Note"], [["A1", "Alpha", "x"], ["A2", "B", "y"]]
    )
    assert land_file(con, SPEC, path) == 2

    changed = write_csv(tmp_path / "widgets.csv", ["Code", "Name", "Note"], [["Z9", "New", "n"]])
    with pytest.raises(RuntimeError, match="boom"):
        land_file(InsertFailsConnection(con), SPEC, changed)

    rows = con.execute(f"select code from {TABLE} order by code").fetchall()
    assert rows == [("A1",), ("A2",)]


def test_source_row_number_stays_in_file_order_for_a_multi_chunk_file(con, tmp_path):
    # DuckDB reads large files in parallel chunks; row numbers must still follow the file.
    total = 150_000
    path = tmp_path / "widgets.csv"
    with open(path, "w", newline="", encoding="utf-8") as handle:
        handle.write("Code,Name,Note\n")
        handle.writelines(f"C{n},n{n},\n" for n in range(total, 0, -1))
    assert land_file(con, SPEC, path) == total
    out_of_order = con.execute(
        f"select count(*) from {TABLE} where code != 'C' || ({total + 1} - _source_row_number)"
    ).fetchone()[0]
    assert out_of_order == 0

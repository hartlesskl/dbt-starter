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

import starter_cli.landing
from starter_cli.landing import (
    EntitySpec,
    LandingError,
    ensure_landing_table,
    land_file,
    load_entity,
    parse_months,
)

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


def _cache_a_good_file(root: Path) -> None:
    cached = root / "data" / "raw" / SPEC.source / SPEC.file_name
    cached.parent.mkdir(parents=True)
    write_csv(cached, ["Code", "Name", "Note"], [["A1", "Alpha", "x"]])


def test_load_entity_lands_a_cached_file(tmp_path, monkeypatch):
    monkeypatch.delenv("DBT_DUCKDB_PATH", raising=False)
    _cache_a_good_file(tmp_path)
    assert load_entity(SPEC, tmp_path) == 1


def test_an_older_table_missing_a_declared_column_gets_it_added_before_the_load(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("DBT_DUCKDB_PATH", raising=False)
    _cache_a_good_file(tmp_path)
    database = duckdb.connect(str(tmp_path / "data" / "dbt_starter.duckdb"))
    database.execute("create schema landing")
    database.execute(
        f"create table {TABLE} (code varchar, _source_file varchar, "
        "_source_row_number bigint, _batch_id varchar, _loaded_at timestamp)"
    )
    database.close()
    assert load_entity(SPEC, tmp_path) == 1
    database = duckdb.connect(str(tmp_path / "data" / "dbt_starter.duckdb"), read_only=True)
    try:
        assert database.execute(f"select code, name, note from {TABLE}").fetchall() == [
            ("A1", "Alpha", "x")
        ]
    finally:
        database.close()


def test_a_declared_column_stored_with_an_incompatible_type_is_a_landing_error(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("DBT_DUCKDB_PATH", raising=False)
    _cache_a_good_file(tmp_path)
    database = duckdb.connect(str(tmp_path / "data" / "dbt_starter.duckdb"))
    database.execute("create schema landing")
    database.execute(
        f"create table {TABLE} (code integer, name varchar, note varchar, _source_file varchar, "
        "_source_row_number bigint, _batch_id varchar, _loaded_at timestamp)"
    )
    database.close()
    with pytest.raises(LandingError, match="^DuckDB could not land widgets.csv"):
        load_entity(SPEC, tmp_path)


def test_a_database_file_that_is_not_a_database_is_reported_as_a_landing_error(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("DBT_DUCKDB_PATH", raising=False)
    _cache_a_good_file(tmp_path)
    (tmp_path / "data" / "dbt_starter.duckdb").write_text("this is not a duckdb file")
    with pytest.raises(LandingError, match="DuckDB could not land widgets.csv"):
        load_entity(SPEC, tmp_path)


def test_a_file_that_is_not_utf8_is_reported_as_a_landing_error(tmp_path, monkeypatch):
    monkeypatch.delenv("DBT_DUCKDB_PATH", raising=False)
    cached = tmp_path / "data" / "raw" / SPEC.source / SPEC.file_name
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b'"Code","Name","Note"\n"\xff\xfe","b","c"\n')
    with pytest.raises(LandingError):
        load_entity(SPEC, tmp_path)


# --- Parquet landing, months, folders and the large-month guard ---------------------------

PQ = EntitySpec(
    source="acme",
    entity="gizmos",
    url="https://example.invalid/gizmos_{month}.parquet",
    file_name="gizmos_{month}.parquet",
    columns=("code", "qty", "ratio"),
    header_prefix=b"PAR1",
    file_format="parquet",
    default_months=("2024-01",),
    null_missing_columns=True,
)
PQ_TABLE = f"landing.{PQ.table}"


def write_parquet(path: Path, select_sql: str) -> Path:
    """Write the result of `select_sql` as a Parquet file through DuckDB."""
    con = duckdb.connect()
    try:
        target = str(path).replace("'", "''")
        con.execute(f"copy ({select_sql}) to '{target}' (format parquet)")
    finally:
        con.close()
    return path


GOOD_SQL = "select 'A1' as code, 3 as qty, 17.7 as ratio"


@pytest.fixture
def pq_con(tmp_path):
    connection = duckdb.connect(str(tmp_path / "pq.duckdb"))
    ensure_landing_table(connection, PQ)
    yield connection
    connection.close()


def land_pq(con, tmp_path, select_sql=GOOD_SQL, name="gizmos_2024-01.parquet", spec=PQ) -> int:
    return land_file(con, spec, write_parquet(tmp_path / name, select_sql))


def test_mixed_case_parquet_names_land_in_lowercase_columns(pq_con, tmp_path):
    land_pq(pq_con, tmp_path, 'select \'A1\' as "Code", 3 as "QTY", 1.5 as "Ratio"')
    assert pq_con.execute(f"select code, qty, ratio from {PQ_TABLE}").fetchall() == [
        ("A1", "3", "1.5")
    ]


def test_two_parquet_names_equal_once_lowercased_raise_naming_both(pq_con, tmp_path):
    # DuckDB renames a repeated name when it writes, so the file is written with a spare name
    # of the same length and the bytes are patched. The footer then really holds both names.
    path = write_parquet(
        tmp_path / "gizmos_2024-01.parquet",
        'select 1 as "Code", 2 as "Codx", 3 as qty, 4 as ratio',
    )
    path.write_bytes(path.read_bytes().replace(b"Codx", b"CODE"))
    with pytest.raises(LandingError) as excinfo:
        land_file(pq_con, PQ, path)
    assert "'Code'" in str(excinfo.value)
    assert "'CODE'" in str(excinfo.value)


def test_a_parquet_name_that_is_not_a_plain_identifier_raises_naming_it(pq_con, tmp_path):
    with pytest.raises(LandingError, match="Bad Name"):
        land_pq(pq_con, tmp_path, 'select 1 as code, 2 as qty, 3 as ratio, 4 as "Bad Name"')


def test_a_declared_column_missing_from_the_file_lands_null(pq_con, tmp_path):
    land_pq(pq_con, tmp_path, "select 'A1' as code, 3 as qty")
    assert pq_con.execute(f"select code, qty, ratio from {PQ_TABLE}").fetchall() == [
        ("A1", "3", None)
    ]


def test_a_missing_declared_column_raises_when_nulls_are_not_allowed(tmp_path):
    strict = EntitySpec(
        source="acme",
        entity="gizmos",
        url=PQ.url,
        file_name=PQ.file_name,
        columns=PQ.columns,
        header_prefix=b"PAR1",
        file_format="parquet",
    )
    con = duckdb.connect(str(tmp_path / "strict.duckdb"))
    try:
        ensure_landing_table(con, strict)
        with pytest.raises(LandingError, match="'ratio'"):
            land_pq(con, tmp_path, "select 'A1' as code, 3 as qty", spec=strict)
    finally:
        con.close()


def test_an_undeclared_column_is_added_as_text_and_a_later_file_lands_null_there(pq_con, tmp_path):
    land_pq(pq_con, tmp_path, "select 'A1' as code, 3 as qty, 1.5 as ratio, 'app' as extra_col")
    land_pq(pq_con, tmp_path, GOOD_SQL, name="gizmos_2024-02.parquet")
    types = dict(
        pq_con.execute(
            f"select column_name, data_type from information_schema.columns "
            f"where table_name = '{PQ.table}'"
        ).fetchall()
    )
    assert types["extra_col"] == "VARCHAR"
    rows = pq_con.execute(
        f"select _source_file, extra_col from {PQ_TABLE} order by _source_file"
    ).fetchall()
    assert rows == [("gizmos_2024-01.parquet", "app"), ("gizmos_2024-02.parquet", None)]


@pytest.mark.parametrize(
    ("literal", "landed"),
    [
        ("1.5e-05", "0.000015"),
        ("1e15", "1000000000000000"),
        ("-2.5e-07", "-0.00000025"),
        ("17.7", "17.7"),
        ("1.0", "1.0"),
        ("0.0", "0.0"),
        ("123456.789", "123456.789"),
    ],
)
def test_doubles_render_in_fixed_notation_only_when_tiny_or_huge(pq_con, tmp_path, literal, landed):
    land_pq(pq_con, tmp_path, f"select 'A1' as code, 3 as qty, {literal}::double as ratio")
    assert pq_con.execute(f"select ratio from {PQ_TABLE}").fetchone()[0] == landed


def test_a_null_double_and_an_empty_string_both_land_null(pq_con, tmp_path):
    land_pq(pq_con, tmp_path, "select '' as code, 3 as qty, null::double as ratio")
    assert pq_con.execute(f"select code, ratio from {PQ_TABLE}").fetchall() == [(None, None)]


def test_a_float_column_is_formatted_too(pq_con, tmp_path):
    land_pq(pq_con, tmp_path, "select 'A1' as code, 3 as qty, 0.00001::float as ratio")
    value = pq_con.execute(f"select ratio from {PQ_TABLE}").fetchone()[0]
    assert "e" not in value.lower()
    assert value.startswith("0.00001")


def test_parquet_row_numbers_follow_file_order_and_a_second_landing_keeps_the_count(
    pq_con, tmp_path
):
    sql = "select 'C' || i as code, i as qty, 1.5 as ratio from range(1, 6) t(i)"
    assert land_pq(pq_con, tmp_path, sql) == 5
    assert land_pq(pq_con, tmp_path, sql) == 5
    rows = pq_con.execute(
        f"select _source_row_number, code from {PQ_TABLE} order by _source_row_number"
    ).fetchall()
    assert rows == [(n, f"C{n}") for n in range(1, 6)]


def folder_load(tmp_path, folder, spec=PQ, **kwargs):
    return load_entity(spec, tmp_path / "root", from_dir=folder, **kwargs)


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.delenv("DBT_DUCKDB_PATH", raising=False)
    (tmp_path / "root").mkdir()
    return tmp_path / "root"


def landed_rows(root):
    con = duckdb.connect(str(root / "data" / "dbt_starter.duckdb"), read_only=True)
    try:
        return con.execute(
            f"select _source_file, _batch_id, count(*) from {PQ_TABLE} group by 1, 2 order by 1"
        ).fetchall()
    finally:
        con.close()


def test_a_folder_load_ignores_files_that_do_not_match_the_pattern(root, tmp_path):
    folder = tmp_path / "in"
    folder.mkdir()
    write_parquet(folder / "gizmos_2024-01.parquet", GOOD_SQL)
    write_parquet(folder / "gizmos_2024-13.parquet", GOOD_SQL)
    (folder / "notes.txt").write_text("not data")
    assert load_entity(PQ, root, from_dir=folder) == 1
    assert [row[0] for row in landed_rows(root)] == ["gizmos_2024-01.parquet"]


def test_a_requested_month_with_no_file_raises_naming_the_expected_file(root, tmp_path):
    folder = tmp_path / "in"
    folder.mkdir()
    write_parquet(folder / "gizmos_2024-01.parquet", GOOD_SQL)
    with pytest.raises(LandingError, match="gizmos_2024-02.parquet"):
        load_entity(PQ, root, from_dir=folder, months=["2024-01", "2024-02"])
    assert not (root / "data" / "dbt_starter.duckdb").exists()


def test_a_folder_file_without_magic_bytes_at_both_ends_raises(root, tmp_path):
    folder = tmp_path / "in"
    folder.mkdir()
    good = write_parquet(tmp_path / "good.parquet", GOOD_SQL).read_bytes()
    (folder / "gizmos_2024-01.parquet").write_bytes(good[:-6])
    with pytest.raises(LandingError, match="gizmos_2024-01.parquet"):
        load_entity(PQ, root, from_dir=folder)


def test_a_symlink_pointing_outside_the_folder_is_rejected(root, tmp_path):
    folder = tmp_path / "in"
    folder.mkdir()
    outside = write_parquet(tmp_path / "outside.parquet", GOOD_SQL)
    (folder / "gizmos_2024-01.parquet").symlink_to(outside)
    with pytest.raises(LandingError, match="outside the folder"):
        load_entity(PQ, root, from_dir=folder)


def test_loading_a_folder_twice_gives_the_same_count_and_writes_nothing_to_the_cache(
    root, tmp_path
):
    folder = tmp_path / "in"
    folder.mkdir()
    write_parquet(folder / "gizmos_2024-01.parquet", GOOD_SQL)
    assert load_entity(PQ, root, from_dir=folder) == 1
    assert load_entity(PQ, root, from_dir=folder) == 1
    assert landed_rows(root)[0][2] == 1
    assert not (root / "data" / "raw").exists()


def test_the_same_bytes_through_the_cache_and_through_a_folder_land_identically(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("DBT_DUCKDB_PATH", raising=False)
    source = write_parquet(tmp_path / "source.parquet", GOOD_SQL).read_bytes()

    via_cache = tmp_path / "cache_root"
    cached = via_cache / "data" / "raw" / "acme" / "gizmos_2024-01.parquet"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(source)
    assert load_entity(PQ, via_cache, months=["2024-01"]) == 1

    via_folder = tmp_path / "folder_root"
    via_folder.mkdir()
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "gizmos_2024-01.parquet").write_bytes(source)
    assert load_entity(PQ, via_folder, from_dir=folder, months=["2024-01"]) == 1

    assert landed_rows(via_cache) == landed_rows(via_folder)


def test_a_malformed_month_is_a_landing_error_before_any_request(root, monkeypatch):
    monkeypatch.setattr(starter_cli.landing.requests, "get", _no_requests)
    with pytest.raises(LandingError, match="2024-13"):
        load_entity(PQ, root, months=["2024-13"])


def _no_requests(*_args, **_kwargs):
    raise AssertionError("a request was made")


def test_parse_months_accepts_a_list_a_range_and_a_mix():
    assert parse_months("2024-01,2024-03") == ("2024-01", "2024-03")
    assert parse_months("2024-01..2024-03") == ("2024-01", "2024-02", "2024-03")
    assert parse_months("2024-03,2023-12..2024-01,2024-01") == ("2023-12", "2024-01", "2024-03")


def test_parse_months_crosses_a_year_boundary_and_a_single_month_range():
    assert parse_months("2023-11..2024-02") == ("2023-11", "2023-12", "2024-01", "2024-02")
    assert parse_months("2024-05..2024-05") == ("2024-05",)


@pytest.mark.parametrize(
    ("text", "named"),
    [
        ("2024-13", "2024-13"),
        ("24-01", "24-01"),
        ("2024-03..2024-01", "2024-03..2024-01"),
        ("2024-01,2024-1", "2024-1"),
        ("2024-01..2024-xx", "2024-xx"),
        ("", ""),
        ("2024-01,,2024-02", ""),
    ],
)
def test_parse_months_rejects_a_bad_part_and_names_it(text, named):
    with pytest.raises(ValueError) as excinfo:
        parse_months(text)
    assert repr(named) in str(excinfo.value)


LARGE = EntitySpec(
    source="acme",
    entity="gizmos",
    url=PQ.url,
    file_name=PQ.file_name,
    columns=PQ.columns,
    header_prefix=b"PAR1",
    file_format="parquet",
    default_months=("2024-01",),
    null_missing_columns=True,
    large_before="2022-01",
)


def test_a_month_before_large_before_needs_allow_large_and_makes_no_request(root, monkeypatch):
    monkeypatch.setattr(starter_cli.landing.requests, "get", _no_requests)
    with pytest.raises(LandingError, match="--allow-large"):
        load_entity(LARGE, root, months=["2021-12"])


def test_allow_large_lets_an_early_month_download_and_land(root, tmp_path, monkeypatch):
    body = write_parquet(tmp_path / "served.parquet", GOOD_SQL).read_bytes()
    requested = []

    class Served:
        def __init__(self):
            self.status_code = 200
            self.headers = {"Content-Length": str(len(body))}

        def iter_content(self, _size):
            yield body

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def fake_get(url, **_kwargs):
        requested.append(url)
        return Served()

    monkeypatch.setattr(starter_cli.landing.requests, "get", fake_get)
    assert load_entity(LARGE, root, months=["2021-12"], allow_large=True) == 1
    assert requested == [LARGE.url_for("2021-12")]


def test_a_folder_load_of_an_early_month_never_needs_allow_large(root, tmp_path):
    folder = tmp_path / "in"
    folder.mkdir()
    write_parquet(folder / "gizmos_2021-12.parquet", GOOD_SQL)
    assert load_entity(LARGE, root, from_dir=folder, months=["2021-12"]) == 1


def test_a_month_on_or_after_large_before_needs_no_flag(root, monkeypatch):
    monkeypatch.setattr(starter_cli.landing.requests, "get", _no_requests)
    with pytest.raises(AssertionError, match="a request was made"):
        load_entity(LARGE, root, months=["2022-01"])


def test_large_before_must_be_a_month():
    with pytest.raises(ValueError, match="large_before"):
        EntitySpec(
            source="acme",
            entity="gizmos",
            url=PQ.url,
            file_name=PQ.file_name,
            columns=PQ.columns,
            header_prefix=b"PAR1",
            file_format="parquet",
            large_before="2022-13",
        )

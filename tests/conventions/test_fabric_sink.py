"""The Lakehouse landing sink, tested on local Delta folders with the real delta-rs (D-04 to D-07).

Nothing here touches the network or needs `az login`: `LakehouseSink(base_uri=<folder>)` writes the
same Delta commits to a temporary folder that the sink writes to OneLake. The tests hold the
contract of plan 05-02: one commit per file, replace-by-file, new columns merged as strings,
protocol (1,2) with the Delta type `timestamp` for `_loaded_at` (research Surprise 3), no id in any
message (T-05-08), a quote in a file name kept inside its string (T-05-07), a URL built only from
checked parts (T-05-10), and a default DuckDB path that never imports the Delta package (D-06).
"""

from __future__ import annotations

import calendar
import json
import subprocess
import sys
import types
from pathlib import Path

import duckdb
import pytest
from support.dbt_cli import REPO_ROOT

from starter_cli import fabric_sink, landing, load, project
from starter_cli.build_fabric import REQUIRED_ENV
from starter_cli.landing import EntitySpec, LandingError

deltalake = pytest.importorskip("deltalake")

WORKSPACE_ID = "aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb"
LAKEHOUSE_ID = "cccccccc-4444-5555-6666-dddddddddddd"

SPEC = EntitySpec(
    source="acme",
    entity="parts",
    url="https://example.invalid/parts_{month}.parquet",
    file_name="parts_{month}.parquet",
    columns=("code", "name"),
    header_prefix=b"PAR1",
    file_format="parquet",
    default_months=("2024-01",),
)
SINGLE = EntitySpec(
    source="acme",
    entity="zones",
    url="https://example.invalid/zones.csv",
    file_name="zones.csv",
    columns=("code",),
    header_prefix=b'"code"',
)


def write_parquet(path: Path, rows: list[tuple], columns: tuple[str, ...]) -> Path:
    """Write a small Parquet file with every column as text."""
    con = duckdb.connect()
    definition = ", ".join(f"{name} varchar" for name in columns)
    con.execute(f"create table t ({definition})")
    for row in rows:
        con.execute(f"insert into t values ({', '.join('?' for _ in columns)})", list(row))
    target = str(path).replace("'", "''")
    con.execute(f"copy t to '{target}' (format parquet)")
    con.close()
    return path


@pytest.fixture
def sink(tmp_path: Path) -> fabric_sink.LakehouseSink:
    return fabric_sink.LakehouseSink(WORKSPACE_ID, LAKEHOUSE_ID, base_uri=tmp_path / "delta")


@pytest.fixture
def con():
    connection = duckdb.connect()
    landing.ensure_landing_table(connection, SPEC)
    yield connection
    connection.close()


def ship_file(con, sink, path: Path) -> int:
    landing.land_file(con, SPEC, path)
    return sink.ship(con, SPEC, path.name)


def table_rows(sink: fabric_sink.LakehouseSink, columns: str) -> list[tuple]:
    """Read the Delta table back with DuckDB over its live data files."""
    table = deltalake.DeltaTable(sink.location(SPEC.table))
    reader = duckdb.connect()
    files = [uri.removeprefix("file://") for uri in table.file_uris()]
    return reader.execute(
        f"select {columns} from read_parquet(?, union_by_name=true) order by 1, 2", [files]
    ).fetchall()


def first_commit(sink: fabric_sink.LakehouseSink) -> dict[str, dict]:
    log = Path(sink.location(SPEC.table)) / "_delta_log" / "00000000000000000000.json"
    entries: dict[str, dict] = {}
    for line in log.read_text().splitlines():
        entry = json.loads(line)
        entries.update(entry)
    return entries


# --- the write contract ------------------------------------------------------------------------


def test_the_first_ship_creates_the_table_and_returns_the_rows_written(tmp_path, con, sink):
    path = write_parquet(
        tmp_path / "parts_2024-01.parquet", [("a", "x"), ("b", "y")], ("code", "name")
    )
    assert ship_file(con, sink, path) == 2
    assert table_rows(sink, "_source_file, code") == [
        ("parts_2024-01.parquet", "a"),
        ("parts_2024-01.parquet", "b"),
    ]


def test_a_second_file_coexists_with_the_first(tmp_path, con, sink):
    one = write_parquet(
        tmp_path / "parts_2024-01.parquet", [("a", "x"), ("b", "y")], ("code", "name")
    )
    two = write_parquet(tmp_path / "parts_2024-02.parquet", [("c", "z")], ("code", "name"))
    ship_file(con, sink, one)
    assert ship_file(con, sink, two) == 1
    assert len(table_rows(sink, "_source_file, code")) == 3


def test_reshipping_a_file_replaces_only_its_own_rows(tmp_path, con, sink, capsys):
    one = write_parquet(
        tmp_path / "parts_2024-01.parquet", [("a", "x"), ("b", "y")], ("code", "name")
    )
    two = write_parquet(tmp_path / "parts_2024-02.parquet", [("c", "z")], ("code", "name"))
    ship_file(con, sink, one)
    ship_file(con, sink, two)
    capsys.readouterr()
    write_parquet(one, [("q", "w")], ("code", "name"))
    assert ship_file(con, sink, one) == 1
    assert table_rows(sink, "_source_file, code") == [
        ("parts_2024-01.parquet", "q"),
        ("parts_2024-02.parquet", "c"),
    ]
    assert "Replaced parts_2024-01.parquet in the Lakehouse" in capsys.readouterr().out


def test_a_first_ship_prints_no_replace_line(tmp_path, con, sink, capsys):
    path = write_parquet(tmp_path / "parts_2024-01.parquet", [("a", "x")], ("code", "name"))
    ship_file(con, sink, path)
    assert "Replaced" not in capsys.readouterr().out


def test_a_rerun_of_identical_content_converges_to_the_same_rows(tmp_path, con, sink):
    path = write_parquet(
        tmp_path / "parts_2024-01.parquet", [("a", "x"), ("b", "y")], ("code", "name")
    )
    ship_file(con, sink, path)
    before = table_rows(sink, "_source_file, code, name")
    ship_file(con, sink, path)
    assert table_rows(sink, "_source_file, code, name") == before


def test_a_new_column_is_merged_as_a_string_and_older_rows_read_null(tmp_path, con, sink):
    one = write_parquet(tmp_path / "parts_2024-01.parquet", [("a", "x")], ("code", "name"))
    ship_file(con, sink, one)
    two = write_parquet(
        tmp_path / "parts_2024-02.parquet", [("b", "y", "app")], ("code", "name", "channel")
    )
    ship_file(con, sink, two)
    schema = {
        field.name: str(field.type)
        for field in deltalake.DeltaTable(sink.location(SPEC.table)).schema().fields
    }
    assert "string" in schema["channel"]
    assert table_rows(sink, "code, channel") == [("a", None), ("b", "app")]


def test_the_table_is_protocol_1_2_and_loaded_at_is_the_delta_timestamp(tmp_path, con, sink):
    path = write_parquet(tmp_path / "parts_2024-01.parquet", [("a", "x")], ("code", "name"))
    ship_file(con, sink, path)
    commit = first_commit(sink)
    protocol = commit["protocol"]
    assert protocol["minReaderVersion"] == 1
    assert protocol["minWriterVersion"] == 2
    assert not protocol.get("readerFeatures")
    assert not protocol.get("writerFeatures")
    fields = {
        f["name"]: f["type"] for f in json.loads(commit["metaData"]["schemaString"])["fields"]
    }
    assert fields["_loaded_at"] == "timestamp"
    assert fields["_source_row_number"] == "long"
    assert fields["code"] == "string"


def test_loaded_at_keeps_the_same_utc_instant_whatever_the_session_zone(tmp_path, con, sink):
    path = write_parquet(tmp_path / "parts_2024-01.parquet", [("a", "x")], ("code", "name"))
    landing.land_file(con, SPEC, path)
    con.execute("update landing.acme__parts set _loaded_at = timestamp '2024-01-15 12:00:00'")
    con.execute("set TimeZone = 'America/New_York'")
    sink.ship(con, SPEC, path.name)
    table = deltalake.DeltaTable(sink.location(SPEC.table))
    reader = duckdb.connect()
    files = [uri.removeprefix("file://") for uri in table.file_uris()]
    (epoch,) = reader.execute("select epoch(_loaded_at) from read_parquet(?)", [files]).fetchone()
    assert int(epoch) == calendar.timegm((2024, 1, 15, 12, 0, 0))


def test_a_file_name_with_a_quote_stays_inside_its_predicate_string(tmp_path, con, sink):
    quoted = write_parquet(tmp_path / "parts_o'brien.parquet", [("a", "x")], ("code", "name"))
    other = write_parquet(tmp_path / "parts_2024-01.parquet", [("b", "y")], ("code", "name"))
    ship_file(con, sink, other)
    ship_file(con, sink, quoted)
    write_parquet(quoted, [("c", "z"), ("d", "z")], ("code", "name"))
    ship_file(con, sink, quoted)
    assert table_rows(sink, "_source_file, code") == [
        ("parts_2024-01.parquet", "b"),
        ("parts_o'brien.parquet", "c"),
        ("parts_o'brien.parquet", "d"),
    ]


# --- the pure helpers --------------------------------------------------------------------------


def test_delta_predicate_doubles_a_quote():
    assert fabric_sink.delta_predicate("a'b.parquet") == "_source_file = 'a''b.parquet'"
    assert fabric_sink.delta_predicate("plain.parquet") == "_source_file = 'plain.parquet'"


def test_table_uri_is_built_from_the_two_ids_and_the_table():
    assert fabric_sink.table_uri(WORKSPACE_ID, LAKEHOUSE_ID, "acme__parts") == (
        f"abfss://{WORKSPACE_ID}@onelake.dfs.fabric.microsoft.com/{LAKEHOUSE_ID}"
        "/Tables/landing/acme__parts"
    )


@pytest.mark.parametrize(
    ("workspace", "lakehouse", "table"),
    [
        ("not-a-uuid", LAKEHOUSE_ID, "acme__parts"),
        (WORKSPACE_ID, "../escape", "acme__parts"),
        (WORKSPACE_ID, LAKEHOUSE_ID, "../../Files/x"),
        (WORKSPACE_ID, LAKEHOUSE_ID, "Upper"),
    ],
)
def test_table_uri_rejects_anything_that_is_not_a_uuid_or_an_identifier(
    workspace, lakehouse, table
):
    with pytest.raises(ValueError):
        fabric_sink.table_uri(workspace, lakehouse, table)


def test_redact_removes_both_ids_in_any_letter_case():
    text = f"failed at {WORKSPACE_ID} and {LAKEHOUSE_ID.upper()} and {WORKSPACE_ID}"
    out = fabric_sink.redact(text, {"workspace-id": WORKSPACE_ID, "lakehouse-id": LAKEHOUSE_ID})
    assert WORKSPACE_ID not in out
    assert LAKEHOUSE_ID.lower() not in out.lower()
    assert out == "failed at <workspace-id> and <lakehouse-id> and <workspace-id>"


def test_redact_hides_an_id_after_text_whose_lower_case_changes_length():
    # "İ" lower-cases to two characters, so offsets found in a lower-cased copy drift off the text.
    text = "İ" * 40 + WORKSPACE_ID.upper() + " and " + LAKEHOUSE_ID
    out = fabric_sink.redact(text, {"workspace-id": WORKSPACE_ID, "lakehouse-id": LAKEHOUSE_ID})
    assert "<workspace-id>" in out and "<lakehouse-id>" in out
    for secret in (WORKSPACE_ID, LAKEHOUSE_ID):
        for start in range(len(secret) - 7):
            assert secret[start : start + 8].lower() not in out.lower()
    assert out == "İ" * 40 + "<workspace-id> and <lakehouse-id>"


def test_a_writer_failure_becomes_a_landing_error_without_the_ids(tmp_path, con, monkeypatch):
    path = write_parquet(tmp_path / "parts_2024-01.parquet", [("a", "x")], ("code", "name"))
    landing.land_file(con, SPEC, path)

    def boom(*args, **kwargs):
        raise OSError(f"cannot reach abfss://{WORKSPACE_ID}@host/{LAKEHOUSE_ID}/Tables")

    monkeypatch.setattr(deltalake, "write_deltalake", boom)
    online = fabric_sink.LakehouseSink(WORKSPACE_ID, LAKEHOUSE_ID)
    with pytest.raises(LandingError) as caught:
        online.ship(con, SPEC, path.name)
    message = str(caught.value)
    assert WORKSPACE_ID not in message and LAKEHOUSE_ID not in message
    assert "<workspace-id>" in message and "<lakehouse-id>" in message
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


def test_the_online_sink_logs_in_through_the_az_cli():
    online = fabric_sink.LakehouseSink(WORKSPACE_ID, LAKEHOUSE_ID)
    assert online.storage_options == {"azure_use_azure_cli": "true"}
    assert online.location("acme__parts").startswith(f"abfss://{WORKSPACE_ID}@")


# --- load_entity and the load command ----------------------------------------------------------


def test_load_entity_with_a_sink_normalises_in_a_temporary_file_and_never_opens_the_demo_db(
    tmp_path, sink
):
    folder = tmp_path / "in"
    folder.mkdir()
    write_parquet(folder / "parts_2024-01.parquet", [("a", "x"), ("b", "y")], ("code", "name"))
    write_parquet(folder / "parts_2024-02.parquet", [("c", "z")], ("code", "name"))
    root = tmp_path / "root"
    total = landing.load_entity(SPEC, root, from_dir=folder, sink=sink)
    assert total == 3
    assert len(table_rows(sink, "_source_file, code")) == 3
    assert not (root / "data").exists()


def test_a_one_file_csv_entity_ships_too(tmp_path):
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "zones.csv").write_text('"code"\n1\n2\n3\n')
    sink = fabric_sink.LakehouseSink(WORKSPACE_ID, LAKEHOUSE_ID, base_uri=tmp_path / "delta")
    assert landing.load_entity(SINGLE, tmp_path / "root", from_dir=folder, sink=sink) == 3


class Recorded(list):
    """The load_entity calls, in order, with the patched sources they were made against."""

    sources: dict


@pytest.fixture
def cli(monkeypatch, tmp_path):
    """Patch discovery, the root and load_entity; record each load_entity call."""
    recorded = Recorded()

    def fake_load_entity(spec, root, **kwargs):
        recorded.append({"table": spec.table, **kwargs})
        return 3

    sources = {
        "acme": types.SimpleNamespace(SOURCE="acme", ENTITIES={"parts": SPEC, "zones": SINGLE})
    }
    recorded.sources = sources
    monkeypatch.setattr(load, "discover_sources", lambda: sources)
    monkeypatch.setattr(load, "load_entity", fake_load_entity)
    monkeypatch.setattr(load, "repo_root", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    for name in REQUIRED_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("FABRIC_SCHEMA", raising=False)
    return recorded


def set_fabric_env(monkeypatch) -> None:
    monkeypatch.setenv("FABRIC_WORKSPACE_ID", WORKSPACE_ID)
    monkeypatch.setenv("FABRIC_LAKEHOUSE_ID", LAKEHOUSE_ID)
    monkeypatch.setenv("FABRIC_LAKEHOUSE", "lake")


def test_target_fabric_without_the_variables_exits_2_naming_each_and_lands_nothing(cli, capsys):
    assert load.main(["acme", "parts", "--target", "fabric"]) == 2
    err = capsys.readouterr().err
    for name in REQUIRED_ENV:
        assert err.count(f"{name} is not set") == 1
    assert cli == []


def test_target_fabric_without_the_sink_package_prints_the_hint_and_exits_2(
    cli, capsys, monkeypatch
):
    monkeypatch.setattr(fabric_sink, "sink_installed", lambda: False)
    assert load.main(["acme", "parts", "--target", "fabric"]) == 2
    assert fabric_sink.SINK_HINT in capsys.readouterr().err
    assert cli == []


def test_a_bad_id_is_reported_by_variable_name_only(cli, capsys, monkeypatch):
    set_fabric_env(monkeypatch)
    monkeypatch.setenv("FABRIC_WORKSPACE_ID", "secret-but-not-a-uuid")
    assert load.main(["acme", "parts", "--target", "fabric"]) == 2
    err = capsys.readouterr().err
    assert "FABRIC_WORKSPACE_ID is not a UUID" in err
    assert "secret-but-not-a-uuid" not in err


def test_target_fabric_lands_the_months_the_source_declares(cli, capsys, monkeypatch):
    set_fabric_env(monkeypatch)
    cli.sources["acme"].FABRIC_DEFAULT_MONTHS = ("2025-03",)
    assert load.main(["acme", "parts", "--target", "fabric"]) == 0
    assert not hasattr(load, "FABRIC_DEFAULT_MONTHS")
    assert cli[0]["months"] == ("2025-03",)
    assert isinstance(cli[0]["sink"], fabric_sink.LakehouseSink)
    assert capsys.readouterr().out.strip() == "landed 3 rows into landing.acme__parts"


def test_target_fabric_without_a_declaration_lands_the_entity_default(cli, monkeypatch):
    set_fabric_env(monkeypatch)
    assert not hasattr(cli.sources["acme"], "FABRIC_DEFAULT_MONTHS")
    assert load.main(["acme", "parts", "--target", "fabric"]) == 0
    assert cli[0]["months"] is None  # the loader then uses the spec's own default months


def test_target_fabric_leaves_a_one_file_entity_without_months(cli, monkeypatch):
    set_fabric_env(monkeypatch)
    assert load.main(["acme", "zones", "--target", "fabric"]) == 0
    assert cli[0]["months"] is None


def test_target_fabric_with_from_dir_passes_no_months(cli, monkeypatch, tmp_path):
    set_fabric_env(monkeypatch)
    folder = tmp_path / "files"
    folder.mkdir()
    assert load.main(["acme", "parts", "--target", "fabric", "--from-dir", str(folder)]) == 0
    assert cli[0]["months"] is None


def test_target_fabric_with_explicit_months_keeps_them(cli, monkeypatch):
    set_fabric_env(monkeypatch)
    assert load.main(["acme", "parts", "--target", "fabric", "--months", "2024-03"]) == 0
    assert cli[0]["months"] == ("2024-03",)


def test_without_a_target_nothing_changes(cli):
    assert load.main(["acme", "parts"]) == 0
    assert cli[0]["months"] is None
    assert "sink" not in cli[0]


def test_without_a_target_the_loader_lands_into_duckdb(tmp_path, monkeypatch):
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "zones.csv").write_text('"code"\n1\n2\n')
    sources = {"acme": types.SimpleNamespace(SOURCE="acme", ENTITIES={"zones": SINGLE})}
    monkeypatch.setattr(load, "discover_sources", lambda: sources)
    monkeypatch.setattr(load, "repo_root", lambda: tmp_path)
    monkeypatch.setenv("DBT_DUCKDB_PATH", str(tmp_path / "data" / "demo.duckdb"))
    monkeypatch.chdir(tmp_path)
    assert load.main(["acme", "zones", "--from-dir", str(folder)]) == 0
    count = (
        duckdb.connect(str(tmp_path / "data" / "demo.duckdb"))
        .execute("select count(*) from landing.acme__zones")
        .fetchone()
    )
    assert count == (2,)


def test_the_default_path_never_imports_the_delta_package():
    code = (
        "import sys; import starter_cli.load, starter_cli.landing, starter_cli.demo; "
        "assert not [m for m in sys.modules if m.split('.')[0] in ('deltalake', 'arro3')], "
        "'the default path imported the Delta package'"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


# --- the Fabric month defaults come from the sources (D-22, D-07) ------------------------------


def source_declaring(*months):
    """A fake source module, with FABRIC_DEFAULT_MONTHS only when months are given."""
    module = types.SimpleNamespace(SOURCE="acme", ENTITIES={})
    if months:
        module.FABRIC_DEFAULT_MONTHS = tuple(months)
    return module


def test_fabric_default_months_is_none_when_the_source_declares_nothing():
    assert project.fabric_default_months(source_declaring()) is None


def test_fabric_default_months_returns_the_declared_tuple_unchanged():
    assert project.fabric_default_months(source_declaring("2025-03", "2025-04")) == (
        "2025-03",
        "2025-04",
    )


def test_fabric_default_months_rejects_an_empty_tuple_naming_the_source():
    module = types.SimpleNamespace(SOURCE="acme", ENTITIES={}, FABRIC_DEFAULT_MONTHS=())
    with pytest.raises(ValueError, match="acme"):
        project.fabric_default_months(module)


@pytest.mark.parametrize("bad", [("2025-13",), ("2025-3",), ("2025-03", "x"), "2025-03", None])
def test_fabric_default_months_rejects_a_value_that_is_not_year_month(bad):
    module = types.SimpleNamespace(SOURCE="acme", ENTITIES={}, FABRIC_DEFAULT_MONTHS=bad)
    with pytest.raises(ValueError, match="acme"):
        project.fabric_default_months(module)


def test_default_volume_month_is_the_earliest_month_any_source_declares():
    sources = {
        "b": source_declaring("2025-05", "2025-04"),
        "a": source_declaring("2025-03"),
        "c": source_declaring(),
    }
    assert project.default_volume_month(sources) == "2025-03"


def test_default_volume_month_is_none_when_no_source_declares_one():
    assert project.default_volume_month({"a": source_declaring()}) is None
    assert project.default_volume_month({}) is None


# example:begin
def test_the_example_declares_its_verified_fabric_month():
    sources = project.discover_sources()
    assert sources["nyc_taxi"].FABRIC_DEFAULT_MONTHS == ("2024-01",)
    assert project.default_volume_month(sources) == "2024-01"


# example:end

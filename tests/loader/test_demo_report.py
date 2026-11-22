"""The demo's printer and flags without dbt, a download or the example data."""

from __future__ import annotations

import duckdb
import pytest

from starter_cli import demo
from starter_cli.demo import FABRIC_LABEL, report_lines


@pytest.fixture(autouse=True)
def restore_working_directory(monkeypatch, tmp_path):
    """`demo.main` changes into the repo root; monkeypatch puts the working directory back."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def con():
    connection = duckdb.connect()
    connection.execute("create schema silver")
    connection.execute("create table silver.parts (id integer, dq_warning_count integer)")
    connection.execute("insert into silver.parts values (1, 0), (2, 1), (3, 2), (4, 0)")
    connection.execute("create table silver.parts_quarantine (id integer)")
    yield connection
    connection.close()


def test_relation_lines_come_first_then_a_warnings_line_then_the_fabric_label(con):
    lines = report_lines(
        con,
        [("silver", "silver.parts"), ("quarantine", "silver.parts_quarantine")],
        ["silver.parts"],
    )
    assert lines[0].split() == ["layer", "relation", "rows"]
    assert lines[1].split() == ["silver", "silver.parts", "4"]
    assert lines[2].split() == ["quarantine", "silver.parts_quarantine", "0"]
    assert lines[3].split() == ["warnings", "silver.parts", "2"]
    assert lines[4] == FABRIC_LABEL
    assert len(lines) == 5


def test_a_source_without_warnings_still_prints_its_lines(con):
    lines = report_lines(con, [("silver", "silver.parts")], [])
    assert [line.split()[0] for line in lines] == ["layer", "silver", FABRIC_LABEL.split()[0]]


def test_the_rows_column_holds_nine_digits(con):
    line = report_lines(con, [("silver", "silver.parts")])[1]
    assert line.endswith(f"{4:>9}")


@pytest.mark.parametrize("bad", ["silver.parts; drop table silver.parts", "Silver.Parts", "parts"])
def test_a_relation_name_that_is_not_plain_is_rejected_before_any_query(con, bad):
    with pytest.raises(ValueError, match="must match"):
        report_lines(con, [("silver", bad)])
    with pytest.raises(ValueError, match="must match"):
        report_lines(con, [], [bad])


def test_a_malformed_month_exits_2_before_anything_is_loaded(monkeypatch, capsys):
    def must_not_load(*_args, **_kwargs):
        raise AssertionError("something was loaded")

    monkeypatch.setattr(demo, "load_entity", must_not_load)
    with pytest.raises(SystemExit) as excinfo:
        demo.main(["--months", "2024-13"])
    assert excinfo.value.code == 2
    assert "2024-13" in capsys.readouterr().err


def test_a_from_dir_that_is_not_a_directory_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(demo, "load_entity", lambda *_a, **_k: pytest.fail("loaded"))
    with pytest.raises(SystemExit) as excinfo:
        demo.main(["--from-dir", str(tmp_path / "nope")])
    assert excinfo.value.code == 2
    assert "--from-dir" in capsys.readouterr().err


def test_a_failed_load_stops_the_demo_before_dbt_runs(monkeypatch, capsys):
    from starter_cli.landing import DownloadError

    def failing(*_args, **_kwargs):
        raise DownloadError("Could not download it. See README.md#troubleshooting.")

    monkeypatch.setattr(demo, "load_entity", failing)
    monkeypatch.setattr(demo, "_run_dbt", lambda *_a: pytest.fail("dbt ran"))
    assert demo.main([]) == 2
    assert "README.md#troubleshooting" in capsys.readouterr().err


def test_months_reach_only_monthly_entities_and_the_folder_reaches_all(tmp_path, monkeypatch):
    seen = []

    def recording(spec, root, **kwargs):
        seen.append((spec, kwargs))
        return 1

    monkeypatch.setattr(demo, "load_entity", recording)
    monkeypatch.setattr(demo, "_run_dbt", lambda *_a: 1)  # stop right after the loads
    folder = tmp_path / "files"
    folder.mkdir()
    demo.main(["--months", "2024-01..2024-02", "--from-dir", str(folder), "--allow-large"])
    assert seen
    for spec, kwargs in seen:
        assert kwargs["from_dir"] == folder
        assert kwargs["allow_large"] is True
        assert kwargs["months"] == (("2024-01", "2024-02") if spec.is_monthly else None)

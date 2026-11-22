"""The `load` command: whitelisted arguments, exit codes and messages. No network, no database."""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from starter_cli import load
from starter_cli.landing import DownloadError, EntitySpec

SPEC = EntitySpec(
    source="acme",
    entity="widgets",
    url="https://example.invalid/widgets.csv",
    file_name="widgets.csv",
    columns=("code", "name"),
    header_prefix=b'"Code"',
)
MONTHLY = EntitySpec(
    source="acme",
    entity="parts",
    url="https://example.invalid/parts_{month}.parquet",
    file_name="parts_{month}.parquet",
    columns=("code",),
    header_prefix=b"PAR1",
    file_format="parquet",
    default_months=("2024-01",),
)


@pytest.fixture
def calls(monkeypatch, tmp_path):
    """Patch discovery, the project root and load_entity; record each load_entity call."""
    recorded: list[tuple] = []

    def fake_load_entity(spec, root, **kwargs):
        recorded.append((spec, root, kwargs))
        return 3

    sources = {
        "acme": types.SimpleNamespace(SOURCE="acme", ENTITIES={"widgets": SPEC, "parts": MONTHLY})
    }
    monkeypatch.setattr(load, "discover_sources", lambda: sources)
    monkeypatch.setattr(load, "load_entity", fake_load_entity)
    monkeypatch.setattr(load, "repo_root", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    return recorded


def test_an_unknown_source_exits_2_and_lists_the_valid_sources(calls, capsys):
    with pytest.raises(SystemExit) as excinfo:
        load.main(["nosuch", "x"])
    assert excinfo.value.code == 2
    assert "acme" in capsys.readouterr().err
    assert calls == []


def test_an_unknown_entity_exits_2_and_lists_the_valid_entities(calls, capsys):
    with pytest.raises(SystemExit) as excinfo:
        load.main(["acme", "nosuch"])
    assert excinfo.value.code == 2
    assert "widgets" in capsys.readouterr().err
    assert calls == []


def test_a_valid_call_lands_the_entity_and_reports_the_count(calls, capsys, tmp_path):
    assert load.main(["acme", "widgets"]) == 0
    assert capsys.readouterr().out.strip() == "landed 3 rows into landing.acme__widgets"
    assert calls == [
        (
            SPEC,
            tmp_path,
            {"refresh": False, "months": None, "from_dir": None, "allow_large": False},
        )
    ]
    assert (tmp_path / "data").is_dir()


def test_refresh_is_passed_through(calls):
    assert load.main(["acme", "widgets", "--refresh"]) == 0
    assert calls[0][2]["refresh"] is True


def test_months_from_dir_and_allow_large_reach_load_entity(calls, tmp_path):
    folder = tmp_path / "files"
    folder.mkdir()
    code = load.main(
        [
            "acme",
            "parts",
            "--months",
            "2024-01..2024-02",
            "--from-dir",
            str(folder),
            "--allow-large",
        ]
    )
    assert code == 0
    kwargs = calls[0][2]
    assert kwargs["months"] == ("2024-01", "2024-02")
    assert kwargs["from_dir"] == Path(folder)
    assert kwargs["allow_large"] is True


def test_a_relative_from_dir_is_made_absolute_before_the_working_directory_changes(
    calls, tmp_path, monkeypatch
):
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "files").mkdir(parents=True)
    monkeypatch.chdir(elsewhere)
    assert load.main(["acme", "parts", "--from-dir", "files"]) == 0
    assert calls[0][2]["from_dir"] == elsewhere / "files"


def test_a_malformed_month_exits_2_naming_it_before_load_entity(calls, capsys):
    with pytest.raises(SystemExit) as excinfo:
        load.main(["acme", "parts", "--months", "2024-13"])
    assert excinfo.value.code == 2
    assert "2024-13" in capsys.readouterr().err
    assert calls == []


def test_months_on_a_one_file_entity_exits_2(calls, capsys):
    with pytest.raises(SystemExit) as excinfo:
        load.main(["acme", "widgets", "--months", "2024-01"])
    assert excinfo.value.code == 2
    assert "--months" in capsys.readouterr().err
    assert calls == []


def test_a_from_dir_that_is_not_a_directory_exits_2(calls, capsys, tmp_path):
    missing = tmp_path / "nope"
    with pytest.raises(SystemExit) as excinfo:
        load.main(["acme", "parts", "--from-dir", str(missing)])
    assert excinfo.value.code == 2
    assert "--from-dir" in capsys.readouterr().err
    assert calls == []


def test_a_download_error_returns_2_and_writes_the_message_to_stderr(monkeypatch, calls, capsys):
    def failing(spec, root, **kwargs):
        raise DownloadError("Could not download it. See README.md#troubleshooting.")

    monkeypatch.setattr(load, "load_entity", failing)
    assert load.main(["acme", "widgets"]) == 2
    captured = capsys.readouterr()
    assert "README.md#troubleshooting" in captured.err
    assert captured.out == ""


def test_help_exits_0_and_shows_the_arguments(calls, capsys):
    with pytest.raises(SystemExit) as excinfo:
        load.main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for word in ("source", "entity", "--refresh", "--months", "--from-dir", "--allow-large"):
        assert word in out

"""The `load` command: whitelisted arguments, exit codes and messages. No network, no database."""

from __future__ import annotations

import types

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


@pytest.fixture
def calls(monkeypatch, tmp_path):
    """Patch discovery, the project root and load_entity; record load_entity calls."""
    recorded: list[tuple] = []

    def fake_load_entity(spec, root, *, refresh=False):
        recorded.append((spec, root, refresh))
        return 3

    sources = {"acme": types.SimpleNamespace(SOURCE="acme", ENTITIES={"widgets": SPEC})}
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
    assert calls == [(SPEC, tmp_path, False)]
    assert (tmp_path / "data").is_dir()


def test_refresh_is_passed_through(calls):
    assert load.main(["acme", "widgets", "--refresh"]) == 0
    assert calls[0][2] is True


def test_a_download_error_returns_2_and_writes_the_message_to_stderr(monkeypatch, calls, capsys):
    def failing(spec, root, *, refresh=False):
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
    assert "source" in out
    assert "entity" in out
    assert "--refresh" in out

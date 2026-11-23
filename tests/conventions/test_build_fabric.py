"""`build-fabric`: the self-hosted Fabric run mode checks its environment, then runs dbt (D-21).

`uv run --locked --extra fabric build-fabric` builds the project on a Fabric Lakehouse from this
machine or CI. These tests replace the subprocess runner with a recorder, so they run in a second
and never start dbt or reach Fabric. They hold the fail-fast check (each missing variable named
once, ids that are not UUIDs, a schema that equals the Lakehouse name), the order and arguments of
the two dbt runs, the stop at the first failure and the rule that no variable's value is ever
printed (T-04.1-08, T-04.1-09).
"""

from __future__ import annotations

import os

import pytest
from support.dbt_cli import REPO_ROOT

from starter_cli import build_fabric

WORKSPACE_ID = "aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb"
LAKEHOUSE_ID = "cccccccc-4444-5555-6666-dddddddddddd"
LAKEHOUSE = "lake_secret_name"

VALID_ENV = {
    "FABRIC_WORKSPACE_ID": WORKSPACE_ID,
    "FABRIC_LAKEHOUSE_ID": LAKEHOUSE_ID,
    "FABRIC_LAKEHOUSE": LAKEHOUSE,
}


@pytest.fixture
def isolated(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """No FABRIC_* variable set, the adapter present and a recording runner; return its calls."""
    for key in [key for key in os.environ if key.startswith("FABRIC_")]:
        monkeypatch.delenv(key)
    monkeypatch.chdir(REPO_ROOT)  # main() changes directory; monkeypatch restores it afterwards
    monkeypatch.setattr(build_fabric, "fabric_adapter_installed", lambda: True)
    calls: list[list[str]] = []

    def record(command: list[str], _root, _env) -> int:
        calls.append(command)
        return 0

    monkeypatch.setattr(build_fabric, "RUNNER", record)
    return calls


def _set(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_the_required_names_are_the_profiles_no_default_variables() -> None:
    assert build_fabric.REQUIRED_ENV == (
        "FABRIC_WORKSPACE_ID",
        "FABRIC_LAKEHOUSE_ID",
        "FABRIC_LAKEHOUSE",
    )
    assert build_fabric.UUID_ENV == ("FABRIC_WORKSPACE_ID", "FABRIC_LAKEHOUSE_ID")
    assert build_fabric.DEFAULT_SCHEMA == "dbo"
    assert build_fabric.BUILD_FABRIC_COMMAND == "uv run --locked --extra fabric build-fabric"


def test_with_nothing_set_each_missing_variable_is_named_once_and_nothing_runs(
    isolated: list[list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    assert build_fabric.main([]) == 2
    err = capsys.readouterr().err
    for name in build_fabric.REQUIRED_ENV:
        assert err.count(f"build-fabric: {name} is not set") == 1
    assert isolated == []


def test_a_blank_lakehouse_name_is_the_only_variable_named(
    isolated: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _set(monkeypatch, {**VALID_ENV, "FABRIC_LAKEHOUSE": ""})
    assert build_fabric.main([]) == 2
    problems = [line for line in capsys.readouterr().err.splitlines() if line.startswith("build-")]
    assert problems == ["build-fabric: FABRIC_LAKEHOUSE is not set"]
    assert isolated == []


@pytest.mark.parametrize("name", ["FABRIC_WORKSPACE_ID", "FABRIC_LAKEHOUSE_ID"])
def test_an_id_that_is_not_a_uuid_is_reported_and_nothing_runs(
    name: str,
    isolated: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _set(monkeypatch, {**VALID_ENV, name: "not-a-uuid"})
    assert build_fabric.main([]) == 2
    err = capsys.readouterr().err
    assert f"build-fabric: {name} is not a UUID" in err
    assert "not-a-uuid" not in err
    assert isolated == []


@pytest.mark.parametrize(
    ("lakehouse", "schema"),
    [("DBO", None), ("lake", "Lake"), ("dbo", "dbo")],
)
def test_a_schema_equal_to_the_lakehouse_name_is_reported(
    lakehouse: str,
    schema: str | None,
    isolated: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _set(monkeypatch, {**VALID_ENV, "FABRIC_LAKEHOUSE": lakehouse})
    if schema is not None:
        monkeypatch.setenv("FABRIC_SCHEMA", schema)
    assert build_fabric.main([]) == 2
    assert "FABRIC_SCHEMA must differ from FABRIC_LAKEHOUSE" in capsys.readouterr().err
    assert isolated == []


def test_check_environment_orders_problems_and_returns_nothing_for_a_valid_environment() -> None:
    assert build_fabric.check_environment(VALID_ENV) == []
    problems = build_fabric.check_environment(
        {"FABRIC_WORKSPACE_ID": "x", "FABRIC_LAKEHOUSE_ID": LAKEHOUSE_ID, "FABRIC_LAKEHOUSE": "dbo"}
    )
    assert problems[0] == "FABRIC_WORKSPACE_ID is not a UUID"
    assert problems[1].startswith("FABRIC_SCHEMA must differ from FABRIC_LAKEHOUSE")
    assert len(problems) == 2


def test_a_valid_environment_runs_deps_then_build_on_the_fabric_target(
    isolated: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _set(monkeypatch, VALID_ENV)
    assert build_fabric.main([]) == 0
    assert len(isolated) == 2
    deps, build = isolated
    assert "deps" in deps
    assert "build" in build
    assert build[build.index("--target") + 1] == "fabric"
    assert build[build.index("--profiles-dir") + 1] == "."
    assert build[build.index("--project-dir") + 1] == "."
    captured = capsys.readouterr()
    for value in (WORKSPACE_ID, LAKEHOUSE_ID, LAKEHOUSE):
        assert value not in captured.out
        assert value not in captured.err


def test_the_runner_gets_the_repo_root_and_a_copy_of_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[object, dict[str, str]]] = []

    def record(_command: list[str], root, env: dict[str, str]) -> int:
        seen.append((root, env))
        return 0

    for key in list(build_fabric.REQUIRED_ENV):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setattr(build_fabric, "fabric_adapter_installed", lambda: True)
    monkeypatch.setattr(build_fabric, "RUNNER", record)
    _set(monkeypatch, VALID_ENV)
    assert build_fabric.main([]) == 0
    assert [root for root, _ in seen] == [REPO_ROOT, REPO_ROOT]
    assert all(env["FABRIC_LAKEHOUSE_ID"] == LAKEHOUSE_ID for _, env in seen)


def test_a_failing_deps_step_stops_the_run_and_returns_its_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[list[str]] = []

    def failing(command: list[str], _root, _env) -> int:
        calls.append(command)
        return 3

    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setattr(build_fabric, "fabric_adapter_installed", lambda: True)
    monkeypatch.setattr(build_fabric, "RUNNER", failing)
    _set(monkeypatch, VALID_ENV)
    monkeypatch.delenv("FABRIC_SCHEMA", raising=False)
    assert build_fabric.main([]) == 3
    assert len(calls) == 1
    assert "deps" in calls[0]
    output = capsys.readouterr()
    assert "build-fabric failed at: deps (exit 3)" in output.out + output.err


def test_with_the_adapter_missing_the_run_stops_before_any_command(
    isolated: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(build_fabric, "fabric_adapter_installed", lambda: False)
    _set(monkeypatch, VALID_ENV)
    assert build_fabric.main([]) == 2
    assert build_fabric.BUILD_FABRIC_COMMAND in capsys.readouterr().err
    assert isolated == []


def test_help_exits_zero_with_no_variable_set_and_lists_them(
    isolated: list[list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as raised:
        build_fabric.main(["--help"])
    assert raised.value.code == 0
    out = capsys.readouterr().out
    for name in build_fabric.REQUIRED_ENV:
        assert name in out
    assert isolated == []

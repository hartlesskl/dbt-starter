"""`build-fabric`: the self-hosted Fabric run mode checks its environment, then runs dbt (D-21).

`uv run --locked --extra fabric build-fabric` builds the project on a Fabric Lakehouse from this
machine or CI. These tests replace the subprocess runner with a recorder, so they run in a second
and never start dbt or reach Fabric. They hold the fail-fast check (each missing variable named
once, ids that are not UUIDs, a schema that equals the Lakehouse name), the order and arguments of
the two dbt runs, the stop at the first failure and the rule that no variable's value is ever
printed (T-04.1-08, T-04.1-09). They also hold `--selector`, the WR-06 normalisation (padded
values, an empty schema, a non-CLI authentication) and `declared_known_limits` (D-14, D-25).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

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


# --- --selector and the known-limit exclusion (D-14, D-16) -----------------------------------


def test_a_selector_is_passed_to_dbt_build_and_no_exclude_is_added(
    isolated: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _set(monkeypatch, VALID_ENV)
    assert build_fabric.main(["--selector", "fabric_fixture"]) == 0
    build = isolated[1]
    assert build[-2:] == ["--selector", "fabric_fixture"]
    assert "--exclude" not in build


def test_without_a_selector_the_build_excludes_declared_known_limits_and_descendants(
    isolated: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _set(monkeypatch, VALID_ENV)
    assert build_fabric.main([]) == 0
    build = isolated[1]
    assert build[-2:] == ["--exclude", "tag:fabric_known_limit+"]
    assert "--selector" not in build
    assert "--exclude" not in isolated[0]


def test_the_known_limit_names_are_pinned() -> None:
    assert build_fabric.KNOWN_LIMIT_TAG == "fabric_known_limit"
    assert build_fabric.KNOWN_LIMIT_EXCLUDE == "tag:fabric_known_limit+"


@pytest.mark.parametrize("name", ["Bad Name", "1abc", "a-b", "", "UPPER"])
def test_an_invalid_selector_name_exits_2_before_anything_runs(
    name: str, isolated: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _set(monkeypatch, VALID_ENV)
    with pytest.raises(SystemExit) as raised:
        build_fabric.main(["--selector", name])
    assert raised.value.code == 2
    assert isolated == []


# --- WR-06: one normalised environment for the check and for dbt -----------------------------


def test_normalised_env_strips_every_fabric_value_and_leaves_the_rest_alone() -> None:
    result = build_fabric.normalised_env(
        {"FABRIC_LAKEHOUSE": "  lake \n", "FABRIC_SCHEMA": "  ", "OTHER": " keep "}
    )
    assert result == {"FABRIC_LAKEHOUSE": "lake", "FABRIC_SCHEMA": "", "OTHER": " keep "}


def test_a_padded_id_passes_the_check_and_the_runner_receives_the_stripped_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, str]] = []

    def record(_command: list[str], _root, env: dict[str, str]) -> int:
        seen.append(env)
        return 0

    for key in [key for key in os.environ if key.startswith("FABRIC_")]:
        monkeypatch.delenv(key)
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setattr(build_fabric, "fabric_adapter_installed", lambda: True)
    monkeypatch.setattr(build_fabric, "RUNNER", record)
    _set(monkeypatch, {**VALID_ENV, "FABRIC_WORKSPACE_ID": f"  {WORKSPACE_ID}\t"})
    assert build_fabric.main([]) == 0
    assert seen
    assert all(env["FABRIC_WORKSPACE_ID"] == WORKSPACE_ID for env in seen)


def test_an_empty_schema_is_reported_by_name_and_nothing_runs(
    isolated: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _set(monkeypatch, {**VALID_ENV, "FABRIC_SCHEMA": ""})
    assert build_fabric.main([]) == 2
    problems = [line for line in capsys.readouterr().err.splitlines() if line.startswith("build-")]
    assert problems == ["build-fabric: FABRIC_SCHEMA is set but empty (unset it to use dbo)"]
    assert isolated == []


def test_an_authentication_other_than_cli_is_reported_and_cli_passes(
    isolated: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _set(monkeypatch, {**VALID_ENV, "FABRIC_AUTHENTICATION": "az"})
    assert build_fabric.main([]) == 2
    err = capsys.readouterr().err
    assert err.count("FABRIC_AUTHENTICATION must be CLI") == 1
    assert "FABRIC_AUTHENTICATION=" not in err
    assert isolated == []
    for accepted in ("cli", "CLI", " Cli "):
        assert (
            build_fabric.check_environment({**VALID_ENV, "FABRIC_AUTHENTICATION": accepted}) == []
        )


def test_no_problem_message_holds_a_value(
    isolated: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _set(monkeypatch, {**VALID_ENV, "FABRIC_AUTHENTICATION": "secret_mode", "FABRIC_SCHEMA": " "})
    assert build_fabric.main([]) == 2
    err = capsys.readouterr().err
    for value in ("secret_mode", WORKSPACE_ID, LAKEHOUSE_ID, LAKEHOUSE):
        assert value not in err


# --- declared_known_limits --------------------------------------------------------------------

ISSUE = "https://github.com/example-org/example-repo/issues/42"
TAGGED = "model.dbt_starter.bronze__thing"


def _listing(**meta_overrides: object) -> list[dict]:
    meta = {"issue": ISSUE, "reason": "An invented limit."}
    meta.update(meta_overrides)
    return [
        {
            "unique_id": TAGGED,
            "resource_type": "model",
            "tags": ["fabric_known_limit"],
            "meta": {"fabric_known_limit": meta},
            "depends_on": {"nodes": ["source.dbt_starter.s.t"]},
        },
        {
            "unique_id": "test.dbt_starter.not_null_thing",
            "resource_type": "test",
            "tags": [],
            "meta": {},
            "depends_on": {"nodes": [TAGGED]},
        },
        {
            "unique_id": "model.dbt_starter.silver__thing",
            "resource_type": "model",
            "tags": [],
            "meta": {},
            "depends_on": {"nodes": [TAGGED]},
        },
        {
            "unique_id": "model.dbt_starter.gold__thing",
            "resource_type": "model",
            "tags": [],
            "meta": {},
            "depends_on": {"nodes": ["model.dbt_starter.silver__thing"]},
        },
        {
            "unique_id": "seed.dbt_starter.no_depends",
            "resource_type": "seed",
            "tags": [],
            "meta": {},
        },
    ]


def _stub_listing(monkeypatch: pytest.MonkeyPatch, nodes: list[dict]) -> list[list[str]]:
    commands: list[list[str]] = []

    def printing(command: list[str], _root: Path, _env: dict[str, str]) -> int:
        commands.append(command)
        print("a log line that is not json")
        for node in nodes:
            print(json.dumps(node))
        return 0

    monkeypatch.setattr(build_fabric, "RUNNER", printing)
    return commands


def test_the_real_project_declares_no_known_limit() -> None:
    assert build_fabric.declared_known_limits(REPO_ROOT) == {}


def test_a_declared_limit_lists_every_descendant_sorted(monkeypatch: pytest.MonkeyPatch) -> None:
    commands = _stub_listing(monkeypatch, _listing())
    limits = build_fabric.declared_known_limits(REPO_ROOT)
    assert limits == {
        TAGGED: {
            "resource_type": "model",
            "issue": ISSUE,
            "reason": "An invented limit.",
            "descendants": [
                "model.dbt_starter.gold__thing",
                "model.dbt_starter.silver__thing",
                "test.dbt_starter.not_null_thing",
            ],
        }
    }
    (command,) = commands
    assert "--target" not in command  # the default DuckDB target, so no Fabric variable is needed
    assert command[command.index("--select") + 1] == build_fabric.KNOWN_LIMIT_EXCLUDE


@pytest.mark.parametrize(
    "override",
    [
        {"issue": "https://example.com/issues/1"},
        {"issue": "http://github.com/o/r/issues/1"},
        {"issue": "https://github.com/o/r/pull/1"},
        {"issue": None},
        {"reason": ""},
        {"reason": "  "},
        {"reason": "two\nlines"},
        {"reason": None},
    ],
)
def test_a_tagged_node_without_a_valid_issue_or_reason_raises_naming_the_node(
    override: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_listing(monkeypatch, _listing(**override))
    with pytest.raises(ValueError, match=TAGGED) as raised:
        build_fabric.declared_known_limits(REPO_ROOT)
    for value in override.values():
        if isinstance(value, str) and value.strip():
            assert value not in str(raised.value)


def test_a_tagged_node_with_no_meta_at_all_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    nodes = _listing()
    nodes[0]["meta"] = {}
    _stub_listing(monkeypatch, nodes)
    with pytest.raises(ValueError, match=TAGGED):
        build_fabric.declared_known_limits(REPO_ROOT)


def test_a_failing_ls_raises_instead_of_reporting_no_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(build_fabric, "RUNNER", lambda *_args: 1)
    with pytest.raises(RuntimeError):
        build_fabric.declared_known_limits(REPO_ROOT)

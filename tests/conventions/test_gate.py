"""The PR gate runs its checks in one fixed order and stops at the first failure (CI-01, D-18).

`uv run gate` is the command a pull request runs. These tests replace every step runner with a
recorder, so they run in a second and never start dbt. The step order is the D-18 order as the
user amended it on 2026-11-22: the fixture database is built before the scan, convention and
lint steps, because several of those read a built database.
"""

from __future__ import annotations

import re
import uuid

import pytest
import yaml
from conventions.test_entrypoints import makefile_recipe
from conventions.test_handle import HANDLE_RE
from support.dbt_cli import REPO_ROOT

from starter_cli import gate
from starter_cli.project import DEFAULT_DUCKDB_PATH

EXPECTED_STEPS = (
    "deps",
    "parse",
    "compile-fabric",
    "fixture-build",
    "scan",
    "tests",
    "lint",
    "counts",
)
GATE_COMMAND = "uv run --locked --extra fabric gate"


def recorders(calls: list[str], fail_at: str | None = None, code: int = 3) -> dict:
    """One runner per step that records its name and fails with `code` at `fail_at`."""

    def make(step: str):
        def run(_context: dict) -> int:
            calls.append(step)
            return code if step == fail_at else 0

        return run

    return {step: make(step) for step in EXPECTED_STEPS}


def test_the_steps_are_the_approved_d18_order() -> None:
    assert gate.STEPS == EXPECTED_STEPS


def test_every_step_has_a_runner() -> None:
    assert set(gate.RUNNERS) == set(gate.STEPS)


def test_with_the_fabric_adapter_missing_the_gate_stops_before_any_step(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(gate, "fabric_adapter_installed", lambda: False)
    monkeypatch.setattr(gate, "RUNNERS", recorders(calls))
    code = gate.main([])
    captured = capsys.readouterr()
    assert code != 0
    assert calls == []
    assert GATE_COMMAND in captured.out + captured.err


def test_a_failing_step_stops_the_run_and_is_named(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(gate, "fabric_adapter_installed", lambda: True)
    monkeypatch.setattr(gate, "RUNNERS", recorders(calls, fail_at="scan", code=7))
    code = gate.main([])
    output = capsys.readouterr().out
    assert code == 7
    assert calls == ["deps", "parse", "compile-fabric", "fixture-build", "scan"]
    assert "tests" not in calls
    assert "gate failed at: scan" in output
    assert "gate: all steps passed" not in output


def test_a_run_where_every_step_passes_runs_them_all_in_order_and_says_so(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(gate, "fabric_adapter_installed", lambda: True)
    monkeypatch.setattr(gate, "RUNNERS", recorders(calls))
    assert gate.main([]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert calls == list(EXPECTED_STEPS)
    assert [line for line in lines if line.startswith("gate: ") and "passed" not in line] == [
        f"gate: {step}" for step in EXPECTED_STEPS
    ]
    assert lines[-1] == "gate: all steps passed"


def test_an_unknown_argument_is_rejected_before_any_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(gate, "fabric_adapter_installed", lambda: True)
    monkeypatch.setattr(gate, "RUNNERS", recorders(calls))
    with pytest.raises(SystemExit) as raised:
        gate.main(["--nope"])
    assert raised.value.code == 2
    assert calls == []


def test_the_fabric_compile_step_stays_offline_and_in_its_own_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """T-04.1-07: an interrupted or repeated offline compile never reaches the network."""
    recorded: list[tuple[list[str], dict[str, str]]] = []

    def record(command: list[str], _root, env: dict[str, str]) -> int:
        recorded.append((command, env))
        return 0

    monkeypatch.setattr(gate, "_run", record)
    assert gate._step_compile_fabric({"root": tmp_path, "env": {}}) == 0
    assert len(recorded) == 1
    command, env = recorded[0]
    assert "compile" in command
    assert command[command.index("--target") + 1] == "fabric"
    assert "--no-populate-cache" in command
    assert command[command.index("--target-path") + 1] == "target/gate/fabric"
    for key, value in gate.DUMMY_FABRIC_ENV.items():
        assert env[key] == value
    for key in ("FABRIC_WORKSPACE_ID", "FABRIC_LAKEHOUSE_ID"):
        uuid.UUID(gate.DUMMY_FABRIC_ENV[key])


def test_the_gate_database_is_under_target_gate_and_is_not_the_demo_database() -> None:
    database = gate.gate_database(REPO_ROOT)
    assert database.is_relative_to(REPO_ROOT / "target" / "gate")
    assert database.resolve() != (REPO_ROOT / DEFAULT_DUCKDB_PATH).resolve()
    assert database.suffix == ".duckdb"


# --- The workflow (CI-01, T-02-22, T-02-23) ---

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "gate.yml"
FULL_SHA = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _triggers(workflow: dict) -> dict:
    """The `on:` mapping. PyYAML follows YAML 1.1 and loads a bare `on` key as boolean True."""
    present = [key for key in (True, "on") if key in workflow]
    assert present, "the workflow has no trigger section"
    triggers = workflow[present[0]]
    assert triggers, "the workflow trigger section is empty"
    return triggers


def _run_steps(workflow: dict) -> list[str]:
    return [step["run"] for step in workflow["jobs"]["gate"]["steps"] if "run" in step]


def _makefile_gate_recipe() -> list[str]:
    return makefile_recipe((REPO_ROOT / "Makefile").read_text(), "gate")


def test_the_workflow_has_one_job_named_gate() -> None:
    workflow = _workflow()
    assert list(workflow["jobs"]) == ["gate"]
    assert workflow["jobs"]["gate"]["name"] == "gate"
    assert workflow["name"] == "gate"


def test_the_workflow_runs_on_every_pull_request_and_on_pushes_to_main() -> None:
    triggers = _triggers(_workflow())
    assert "pull_request" in triggers
    assert "push" in triggers
    assert triggers["push"]["branches"] == ["main"]


def test_no_trigger_has_a_path_filter_so_a_documentation_only_change_still_runs() -> None:
    triggers = _triggers(_workflow())
    for name, settings in triggers.items():
        filters = set(settings or {})
        assert not filters & {"paths", "paths-ignore"}, name
    assert "paths" not in WORKFLOW.read_text()


def test_the_workflow_token_is_read_only() -> None:
    workflow = _workflow()
    assert workflow["permissions"] == {"contents": "read"}
    assert "permissions" not in workflow["jobs"]["gate"]


def test_every_action_is_pinned_to_a_full_commit_sha() -> None:
    uses = [step["uses"] for step in _workflow()["jobs"]["gate"]["steps"] if "uses" in step]
    assert len(uses) == 2
    for value in uses:
        assert FULL_SHA.match(value), value


def test_the_pins_are_the_researched_commits_and_carry_their_version_comment() -> None:
    text = WORKFLOW.read_text()
    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1" in text
    assert "astral-sh/setup-uv@1db35dd8eee11c5894872e0b383ae6c406cf738d # v10.4.0" in text


def test_the_workflow_has_no_privileged_trigger_and_no_secret() -> None:
    text = WORKFLOW.read_text()
    assert "pull_request_target" not in text
    assert "secrets." not in text
    assert "persist-credentials: false" in text


def test_the_workflow_cancels_a_superseded_run_on_the_same_ref() -> None:
    concurrency = _workflow()["concurrency"]
    assert concurrency == {"group": "gate-${{ github.ref }}", "cancel-in-progress": True}


def test_the_workflow_installs_with_the_lockfile_then_runs_the_gate_command() -> None:
    runs = _run_steps(_workflow())
    assert runs == ["uv sync --locked --extra fabric", GATE_COMMAND]


def test_the_workflow_gate_step_is_the_makefile_gate_recipe() -> None:
    recipe = _makefile_gate_recipe()
    assert recipe == [GATE_COMMAND]
    assert recipe[0] in _run_steps(_workflow())


def test_the_workflow_names_no_example_dataset() -> None:
    assert not HANDLE_RE.search(WORKFLOW.read_text())

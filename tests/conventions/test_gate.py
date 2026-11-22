"""The PR gate runs its checks in one fixed order and stops at the first failure (CI-01, D-18).

`uv run gate` is the command a pull request runs. These tests replace every step runner with a
recorder, so they run in a second and never start dbt. The step order is the D-18 order as the
user amended it on 2026-11-22: the fixture database is built before the scan, convention and
lint steps, because several of those read a built database.
"""

from __future__ import annotations

import pytest
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


def test_the_gate_database_is_under_target_gate_and_is_not_the_demo_database() -> None:
    database = gate.gate_database(REPO_ROOT)
    assert database.is_relative_to(REPO_ROOT / "target" / "gate")
    assert database.resolve() != (REPO_ROOT / DEFAULT_DUCKDB_PATH).resolve()
    assert database.suffix == ".duckdb"

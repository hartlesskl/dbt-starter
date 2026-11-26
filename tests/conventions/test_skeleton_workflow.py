"""The skeleton workflow runs `uv run skeleton` on every pull request and every push to main.

CI-05 and D-09: `skeleton.yml` has one job, `skeleton`, that runs the command which copies the
tracked tree, removes the example there and runs the copy's gate, demo and site rule. It runs on
every pull request and every push to main, with a read-only token, pinned actions and no
expression inside a run script, because a fork's pull request runs it. remove-example deletes the
workflow and this test in a copy, so a copy does not repeat the proof. These tests read the file as
data: they cannot prove a live run (the run on GitHub does), only that the design is not edited
away. `skeleton_problems` returns every broken rule, so each rule has a fail-first doctored copy.
This file is template-only (`TEMPLATE_ONLY_PATHS` in `starter_cli.remove_example`).
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable

import pytest
import yaml
from conventions.test_gate import FULL_SHA, _triggers
from conventions.test_gate import _workflow as gate_workflow
from conventions.test_handle import HANDLE_RE
from support.dbt_cli import REPO_ROOT

from starter_cli import remove_example

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "skeleton.yml"
GATE = REPO_ROOT / ".github" / "workflows" / "gate.yml"
PINS = (
    "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1",
    "astral-sh/setup-uv@1db35dd8eee11c5894872e0b383ae6c406cf738d # v10.4.0",
)
SYNC_RUN = "uv sync --locked --extra fabric"
SKELETON_RUN = "uv run --locked --extra fabric skeleton"
STEP_KINDS = ["actions/checkout", "astral-sh/setup-uv", "run", "run"]
# Built from parts so this file does not itself name the example dataset.
HANDLE_WORD = "ta" + "xi"
Doctor = Callable[[dict, str], tuple[dict, str]]


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _kind(step: dict) -> str:
    return step["uses"].split("@")[0] if "uses" in step else "run"


def _trigger_key(workflow: dict) -> object:
    """PyYAML follows YAML 1.1 and loads a bare `on` key as the boolean True."""
    return True if True in workflow else "on"


def _trigger_problems(workflow: dict) -> list[str]:
    triggers = _triggers(workflow)
    problems = []
    if set(triggers) != {"pull_request", "push"}:
        problems.append(
            f"triggers are {sorted(map(str, triggers))}, not pull_request and push to main"
        )
    if (triggers.get("push") or {}).get("branches") != ["main"]:
        problems.append("the push trigger is not limited to main")
    for name, settings in triggers.items():
        filters = set(settings) if isinstance(settings, dict) else set()
        if filters & {"paths", "paths-ignore", "branches-ignore", "tags"}:
            problems.append(f"the {name} trigger has a filter that can skip a run")
    if triggers.get("pull_request"):
        problems.append("the pull_request trigger has settings, so some pull requests may skip it")
    return problems


def _job_problems(workflow: dict) -> list[str]:
    problems = []
    if workflow.get("name") != "skeleton":
        problems.append("the workflow is not named skeleton")
    if workflow.get("permissions") != {"contents": "read"}:
        problems.append("workflow permissions are not exactly contents: read")
    if list(workflow["jobs"]) != ["skeleton"]:
        return [*problems, "the workflow must have exactly one job, skeleton"]
    job = workflow["jobs"]["skeleton"]
    if job.get("name") != "skeleton":
        problems.append("the job is not named skeleton")
    if "permissions" in job:
        problems.append("the skeleton job has permissions of its own")
    if "if" in job:
        problems.append("the skeleton job has an if: of its own")
    if job.get("runs-on") != "ubuntu-latest":
        problems.append("skeleton.runs-on is not ubuntu-latest")
    if not isinstance(job.get("timeout-minutes"), int) or job["timeout-minutes"] > 60:
        problems.append("skeleton.timeout-minutes is missing or above 60")
    if workflow.get("concurrency") != {
        "group": "skeleton-${{ github.ref }}",
        "cancel-in-progress": True,
    }:
        problems.append("concurrency is not group skeleton-<ref> with cancel-in-progress true")
    return problems


def _step_problems(steps: list[dict]) -> list[str]:
    if [_kind(step) for step in steps] != STEP_KINDS:
        return ["the steps are not checkout, setup-uv, sync, skeleton in order"]
    problems = []
    gate_steps = gate_workflow()["jobs"]["gate"]["steps"]
    if steps[0] != gate_steps[0]:
        problems.append("the checkout step differs from gate.yml")
    if steps[1] != gate_steps[1]:
        problems.append("the setup-uv step differs from gate.yml")
    if steps[0].get("with", {}).get("persist-credentials") is not False:
        problems.append("the checkout step does not set persist-credentials: false")
    if steps[2]["run"] != SYNC_RUN:
        problems.append(f"the sync step is not `{SYNC_RUN}`")
    if steps[3]["run"] != SKELETON_RUN:
        problems.append(f"the skeleton step is not `{SKELETON_RUN}`")
    for step in steps:
        if "if" in step or "env" in step or "continue-on-error" in step:
            problems.append(f"a step has if, env or continue-on-error: {step}")
    return problems


def _text_problems(workflow: dict, text: str) -> list[str]:
    problems = []
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            if "uses" in step and not FULL_SHA.match(step["uses"]):
                problems.append(f"{step['uses']} is not pinned to a full commit SHA")
            if "${{" in step.get("run", ""):
                problems.append(f"a run script holds an expression: {step['run']}")
    problems += [f"the pin {pin} is missing" for pin in PINS if pin not in text]
    if len(re.findall(r"@[0-9a-f]{40} # v", text)) != len(PINS):
        problems.append("the number of pinned actions is not 2")
    for banned in ("secrets.", "pull_request_target", "vars."):
        if banned in text:
            problems.append(f"the file holds {banned!r}")
    if HANDLE_RE.search(text):
        problems.append("the file names the example dataset")
    return problems


def skeleton_problems(workflow: dict, text: str | None = None) -> list[str]:
    """Return every rule the workflow breaks (CI-05, D-09); an empty list is correct."""
    text = WORKFLOW.read_text() if text is None else text
    problems = _trigger_problems(workflow) + _job_problems(workflow)
    if "skeleton" in workflow["jobs"]:
        problems += _step_problems(workflow["jobs"]["skeleton"]["steps"])
    return problems + _text_problems(workflow, text)


def test_the_real_workflow_breaks_no_rule() -> None:
    assert skeleton_problems(_workflow(), WORKFLOW.read_text()) == []


def test_the_workflow_is_named_skeleton_with_one_job_called_skeleton() -> None:
    workflow = _workflow()
    assert workflow["name"] == "skeleton"
    assert list(workflow["jobs"]) == ["skeleton"]
    assert workflow["jobs"]["skeleton"]["name"] == "skeleton"


def test_it_runs_on_every_pull_request_and_every_push_to_main() -> None:
    triggers = _triggers(_workflow())
    assert set(triggers) == {"pull_request", "push"}
    assert triggers["push"]["branches"] == ["main"]
    assert not triggers["pull_request"]


def test_the_only_expression_is_the_concurrency_group() -> None:
    text = WORKFLOW.read_text()
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert code.count("${{") == 1
    assert "group: skeleton-${{ github.ref }}" in code


def test_the_pins_are_the_commits_gate_uses_with_their_version_comments() -> None:
    text = WORKFLOW.read_text()
    gate = GATE.read_text()
    for pin in PINS:
        assert pin in text
        assert pin in gate


def test_gate_keeps_its_single_job() -> None:
    workflow = gate_workflow()
    assert list(workflow["jobs"]) == ["gate"]
    assert workflow["jobs"]["gate"]["name"] == "gate"


def test_the_header_says_what_it_proves_and_that_a_copy_does_not_keep_it() -> None:
    header = WORKFLOW.read_text().split("\nname:")[0]
    assert "every pull request and every push to main" in header
    assert "remove-example deletes" in header


def test_remove_example_deletes_this_workflow_and_this_test_in_a_copy() -> None:
    template_only = remove_example.TEMPLATE_ONLY_PATHS
    assert ".github/workflows/skeleton.yml" in template_only
    assert "tests/conventions/test_skeleton_workflow.py" in template_only
    assert (REPO_ROOT / ".github" / "workflows" / "skeleton.yml").is_file()
    assert (REPO_ROOT / "tests" / "conventions" / "test_skeleton_workflow.py").is_file()


def _edit_job(workflow: dict, text: str, change: Callable[[dict], None]) -> tuple[dict, str]:
    doctored = copy.deepcopy(workflow)
    change(doctored["jobs"]["skeleton"])
    return doctored, text


def _edit_step(index: int, change: Callable[[dict], None]) -> Doctor:
    return lambda workflow, text: _edit_job(workflow, text, lambda job: change(job["steps"][index]))


def _edit_workflow(change: Callable[[dict], None]) -> Doctor:
    def doctor(workflow: dict, text: str) -> tuple[dict, str]:
        doctored = copy.deepcopy(workflow)
        change(doctored)
        return doctored, text

    return doctor


def _add_trigger(name: str, settings: object = None) -> Doctor:
    return _edit_workflow(lambda wf: wf[_trigger_key(wf)].update({name: settings}))


def _append_text(extra: str) -> Doctor:
    return lambda workflow, text: (workflow, text + extra)


def _append_to_run(extra: str) -> Callable[[dict], None]:
    return lambda step: step.update(run=step["run"] + extra)


DOCTORED = {
    "a schedule trigger": (_add_trigger("schedule", [{"cron": "1 1 * * *"}]), "triggers are"),
    "a dispatch trigger": (_add_trigger("workflow_dispatch"), "triggers are"),
    "a privileged trigger": (_add_trigger("pull_request_target"), "triggers are"),
    "no push trigger": (
        _edit_workflow(lambda wf: wf[_trigger_key(wf)].pop("push")),
        "triggers are",
    ),
    "a push to another branch": (
        _edit_workflow(lambda wf: wf[_trigger_key(wf)]["push"].update(branches=["dev"])),
        "the push trigger is not limited to main",
    ),
    "a path filter": (
        _edit_workflow(lambda wf: wf[_trigger_key(wf)]["push"].update(paths=["src/**"])),
        "a filter that can skip a run",
    ),
    "a pull request branch filter": (
        _edit_workflow(lambda wf: wf[_trigger_key(wf)].update(pull_request={"branches": ["main"]})),
        "the pull_request trigger has settings",
    ),
    "a wrong workflow name": (
        _edit_workflow(lambda wf: wf.update(name="skel")),
        "the workflow is not named skeleton",
    ),
    "write permissions": (
        _edit_workflow(lambda wf: wf["permissions"].update(contents="write")),
        "workflow permissions are not exactly contents: read",
    ),
    "permissions on the job": (
        lambda wf, tx: _edit_job(wf, tx, lambda job: job.update(permissions={"contents": "read"})),
        "the skeleton job has permissions of its own",
    ),
    "a second job": (
        _edit_workflow(lambda wf: wf["jobs"].update(other=copy.deepcopy(wf["jobs"]["skeleton"]))),
        "exactly one job, skeleton",
    ),
    "a renamed job": (
        lambda wf, tx: _edit_job(wf, tx, lambda job: job.update(name="proof")),
        "the job is not named skeleton",
    ),
    "a condition on the job": (
        lambda wf, tx: _edit_job(wf, tx, lambda job: job.update({"if": "${{ true }}"})),
        "the skeleton job has an if: of its own",
    ),
    "no timeout": (
        lambda wf, tx: _edit_job(wf, tx, lambda job: job.pop("timeout-minutes")),
        "timeout-minutes is missing or above 60",
    ),
    "a very long timeout": (
        lambda wf, tx: _edit_job(wf, tx, lambda job: job.update({"timeout-minutes": 360})),
        "timeout-minutes is missing or above 60",
    ),
    "a run that does not cancel": (
        _edit_workflow(lambda wf: wf["concurrency"].update({"cancel-in-progress": False})),
        "concurrency is not group skeleton",
    ),
    "an action pinned by tag": (
        _edit_step(0, lambda step: step.update(uses="actions/checkout@v7")),
        "is not pinned to a full commit SHA",
    ),
    "credentials kept": (
        _edit_step(0, lambda step: step.pop("with")),
        "persist-credentials: false",
    ),
    "an expression in a run": (
        _edit_step(3, _append_to_run(" ${{ github.ref }}")),
        "a run script holds an expression",
    ),
    "no sync step": (
        lambda wf, tx: _edit_job(wf, tx, lambda job: job["steps"].pop(2)),
        "the steps are not checkout",
    ),
    "a sync without the lock": (
        _edit_step(2, lambda step: step.update(run="uv sync --extra fabric")),
        "the sync step is not",
    ),
    "a skeleton step without locked": (
        _edit_step(3, lambda step: step.update(run="uv run --extra fabric skeleton")),
        "the skeleton step is not",
    ),
    "a skeleton step without the fabric extra": (
        _edit_step(3, lambda step: step.update(run="uv run --locked skeleton")),
        "the skeleton step is not",
    ),
    "a flag on the skeleton step": (
        _edit_step(3, _append_to_run(" --keep")),
        "the skeleton step is not",
    ),
    "a failure ignored": (
        _edit_step(3, lambda step: step.update({"continue-on-error": True})),
        "a step has if, env or continue-on-error",
    ),
    "a different setup step": (
        _edit_step(1, lambda step: step.update({"with": {"enable-cache": False}})),
        "the setup-uv step differs from gate.yml",
    ),
    "a secret reference": (_append_text("\n# ${{ secrets.TOKEN }}\n"), "'secrets.'"),
    "a privileged trigger word": (
        _append_text("\n# pull_request_target\n"),
        "'pull_request_target'",
    ),
    "a variable reference": (_append_text("\n# ${{ vars.X }}\n"), "'vars.'"),
    "an example dataset word": (
        _append_text("\n# the " + HANDLE_WORD + " data\n"),
        "names the example dataset",
    ),
}


@pytest.mark.parametrize("name", list(DOCTORED))
def test_a_doctored_copy_is_reported(name: str) -> None:
    doctor, expected = DOCTORED[name]
    workflow, text = doctor(_workflow(), WORKFLOW.read_text())
    problems = skeleton_problems(workflow, text)
    assert any(expected in problem for problem in problems), (name, problems)

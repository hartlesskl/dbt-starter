"""The fabric workflow builds the fixture on Fabric on demand and runs only where opted in.

CI-04 and D-16 to D-19: `fabric.yml` starts only by hand (`workflow_dispatch`), never on a pull
request, push or schedule, so it cannot be a required check and spends no capacity unasked. Its one
job is skipped until the repository variable `ENABLE_FABRIC` is `true`; with the variable but a
missing setting it writes a one-line note and stays green. With every setting present it signs in
through azure/login (OIDC, no stored secret), lands the fixture and builds it. This file is new
rather than an edit of the nightly test because the Fabric job needs `id-token: write` and
`secrets.`, which that test forbids (research Pitfall 10). The workflow is held as data: these
tests cannot prove a live run (plan 05-13 does), only that its opt-in, least-privilege and pinning
design is not edited away. `fabric_problems` returns every broken rule, so each rule has a
fail-first doctored copy.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable

import pytest
import yaml
from conventions.test_gate import FULL_SHA, _triggers
from conventions.test_nightly_workflow import PINS as NIGHTLY_PINS
from support.dbt_cli import REPO_ROOT

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "fabric.yml"
VARIABLE_GUARD = "vars.ENABLE_FABRIC == 'true'"
SETTINGS_GUARD = "env.HAVE_SETTINGS == 'true'"
SKIPPED_GUARD = "env.HAVE_SETTINGS != 'true'"
AZURE_LOGIN_PIN = "azure/login@a641126d1b8aa4d1fa005f4f92df94a3a4c4c906 # v3.1.0"
# Checkout and setup-uv are the nightly workflow's pins, taken by import, never copied.
PINS = (*[pin for pin in NIGHTLY_PINS if not pin.startswith("actions/cache@")], AZURE_LOGIN_PIN)
LAND_RUN = "uv run --locked --extra fabric verify-fabric land-fixture --label ci"
BUILD_RUN = (
    "uv run --locked --extra fabric build-fabric --selector fabric_fixture "
    '--summary-file "$GITHUB_STEP_SUMMARY"'
)
NOTE_TEXT = "Fabric settings are not set, so nothing ran. See docs/fabric-native-job.md."
DOCS_PATH_RE = re.compile(r"docs/[A-Za-z0-9_./-]+\.md")
Doctor = Callable[[dict, str], tuple[dict, str]]


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _trigger_key(workflow: dict) -> object:
    """PyYAML follows YAML 1.1 and loads a bare `on` key as the boolean True."""
    return True if True in workflow else "on"


def _steps(workflow: dict) -> list[dict]:
    return workflow["jobs"]["fabric"]["steps"]


def _kind(step: dict) -> str:
    return step["uses"].split("@")[0] if "uses" in step else "run"


def _trigger_problems(workflow: dict) -> list[str]:
    triggers = _triggers(workflow)
    if set(triggers) != {"workflow_dispatch"}:
        return [f"triggers are {sorted(map(str, triggers))}, not workflow_dispatch alone"]
    return []


def _job_problems(workflow: dict) -> list[str]:
    if list(workflow["jobs"]) != ["fabric"]:
        return ["the workflow must have exactly one job, fabric"]
    job = workflow["jobs"]["fabric"]
    problems = []
    if job.get("if") != "${{ " + VARIABLE_GUARD + " }}":
        problems.append("fabric.if is not the ENABLE_FABRIC guard")
    return problems


def _step_problems(steps: list[dict]) -> list[str]:
    kinds = [_kind(step) for step in steps]
    if "astral-sh/setup-uv" not in kinds:
        return ["the workflow has no setup-uv step"]
    after = steps[kinds.index("astral-sh/setup-uv") + 1 :]
    problems = []
    notes = [step for step in after if step.get("if") == SKIPPED_GUARD]
    if len(notes) != 1:
        problems.append("exactly one step must carry the settings-missing condition")
    for note in notes:
        if "uses" in note or NOTE_TEXT not in note.get("run", ""):
            problems.append("the settings-missing step does not write the one-line note")
        if '>> "$GITHUB_STEP_SUMMARY"' not in note.get("run", ""):
            problems.append("the settings-missing step does not append to the job summary")
    for step in after:
        if step not in notes and step.get("if") != SETTINGS_GUARD:
            problems.append(f"a step after setup-uv lacks the HAVE_SETTINGS condition: {step}")
    runs = [step.get("run") for step in after]
    for command in (LAND_RUN, BUILD_RUN):
        if runs.count(command) != 1:
            problems.append(f"the workflow does not run `{command}` exactly once")
    if LAND_RUN in runs and BUILD_RUN in runs and runs.index(LAND_RUN) > runs.index(BUILD_RUN):
        problems.append("the fixture is built before it is landed")
    logins = [step for step in steps if _kind(step) == "azure/login"]
    if len(logins) != 1:
        problems.append("the workflow must sign in through exactly one azure/login step")
    for login in logins:
        if login["uses"] != AZURE_LOGIN_PIN.split(" # ")[0]:
            problems.append("azure/login is not at the re-peeled v3.1.0 commit")
    return problems


def _text_problems(workflow: dict, text: str) -> list[str]:
    problems = []
    for pin in PINS:
        if pin not in text:
            problems.append(f"the pin {pin} is missing")
    for path in DOCS_PATH_RE.findall(text):
        if not (REPO_ROOT / path).exists():
            problems.append(f"the workflow names {path}, which does not exist")
    return problems


def fabric_problems(workflow: dict, text: str) -> list[str]:
    """Return every rule the workflow breaks (D-16 to D-19); an empty list is correct."""
    problems = _trigger_problems(workflow) + _job_problems(workflow)
    if "fabric" in workflow["jobs"]:
        problems += _step_problems(_steps(workflow))
    return problems + _text_problems(workflow, text)


def test_the_real_workflow_breaks_no_rule() -> None:
    assert fabric_problems(_workflow(), WORKFLOW.read_text()) == []


def test_the_workflow_is_named_fabric_with_one_job_called_fabric() -> None:
    workflow = _workflow()
    assert workflow["name"] == "fabric"
    assert list(workflow["jobs"]) == ["fabric"]


def test_every_docs_path_the_workflow_names_exists() -> None:
    paths = DOCS_PATH_RE.findall(WORKFLOW.read_text())
    assert "docs/fabric-native-job.md" in paths
    for path in paths:
        assert (REPO_ROOT / path).exists(), path


def test_the_two_commands_are_the_ones_the_live_proof_runs() -> None:
    runs = [step.get("run") for step in _steps(_workflow())]
    assert LAND_RUN in runs
    assert BUILD_RUN in runs
    assert runs.index(LAND_RUN) < runs.index(BUILD_RUN)


def _edit_job(workflow: dict, text: str, change: Callable[[dict], None]) -> tuple[dict, str]:
    doctored = copy.deepcopy(workflow)
    change(doctored["jobs"]["fabric"])
    return doctored, text


def _edit_step(index: int, change: Callable[[dict], None]) -> Doctor:
    return lambda workflow, text: _edit_job(workflow, text, lambda job: change(job["steps"][index]))


def _add_trigger(name: str) -> Doctor:
    def doctor(workflow: dict, text: str) -> tuple[dict, str]:
        doctored = copy.deepcopy(workflow)
        doctored[_trigger_key(doctored)][name] = {"branches": ["main"]}
        return doctored, text

    return doctor


def _edit_workflow(change: Callable[[dict], None]) -> Doctor:
    def doctor(workflow: dict, text: str) -> tuple[dict, str]:
        doctored = copy.deepcopy(workflow)
        change(doctored)
        return doctored, text

    return doctor


DOCTORED = {
    "a pull request trigger": (_add_trigger("pull_request"), "triggers are"),
    "a push trigger": (_add_trigger("push"), "triggers are"),
    "a schedule trigger": (_add_trigger("schedule"), "triggers are"),
    "no variable guard": (
        lambda wf, tx: _edit_job(wf, tx, lambda job: job.update({"if": "${{ true }}"})),
        "fabric.if is not the ENABLE_FABRIC guard",
    ),
    "a second job": (
        _edit_workflow(lambda wf: wf["jobs"].update(other=copy.deepcopy(wf["jobs"]["fabric"]))),
        "exactly one job",
    ),
    "a build step without the settings condition": (
        _edit_step(6, lambda step: step.pop("if")),
        "lacks the HAVE_SETTINGS condition",
    ),
    "a sign-in step without the settings condition": (
        _edit_step(3, lambda step: step.pop("if")),
        "lacks the HAVE_SETTINGS condition",
    ),
    "no settings-missing note": (
        _edit_step(2, lambda step: step.update({"if": SETTINGS_GUARD})),
        "exactly one step must carry the settings-missing condition",
    ),
    "a note that points nowhere": (
        _edit_step(2, lambda step: step.update(run='echo "nothing ran" >> "$GITHUB_STEP_SUMMARY"')),
        "does not write the one-line note",
    ),
    "a build without the summary file": (
        _edit_step(6, lambda step: step.update(run="uv run --locked --extra fabric build-fabric")),
        "does not run",
    ),
    "a build without the fixture selector": (
        _edit_step(
            6,
            lambda step: step.update(
                run='uv run --locked --extra fabric build-fabric --summary-file "$GITHUB_STEP_SUMMARY"'
            ),
        ),
        "does not run",
    ),
    "no landing of the fixture": (
        _edit_step(5, lambda step: step.update(run="echo landed")),
        "does not run",
    ),
    "a build before the landing": (
        lambda wf, tx: _edit_job(wf, tx, lambda job: job["steps"].insert(5, job["steps"].pop(6))),
        "built before it is landed",
    ),
    "azure/login at another commit": (
        _edit_step(3, lambda step: step.update(uses="azure/login@" + "0" * 40)),
        "azure/login is not at the re-peeled v3.1.0 commit",
    ),
}


@pytest.mark.parametrize("name", list(DOCTORED))
def test_a_doctored_copy_is_reported(name: str) -> None:
    doctor, expected = DOCTORED[name]
    workflow, text = doctor(_workflow(), WORKFLOW.read_text())
    problems = fabric_problems(workflow, text)
    assert any(expected in problem for problem in problems), (name, problems)


def test_a_doc_path_that_does_not_exist_is_reported() -> None:
    problems = fabric_problems(_workflow(), WORKFLOW.read_text() + "\n# docs/no-such-page.md\n")
    assert any("docs/no-such-page.md" in problem for problem in problems)


def test_the_pins_are_full_commit_shas_with_a_version_comment() -> None:
    for pin in PINS:
        assert FULL_SHA.match(pin.split(" # ")[0]), pin
        assert pin.split(" # ")[1].startswith("v"), pin

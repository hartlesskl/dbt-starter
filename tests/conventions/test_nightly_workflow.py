"""The nightly workflow builds the pinned months on a schedule and runs only where opted in.

CI-03 and D-14 to D-17: `nightly.yml` runs on one cron entry and by hand, never on a pull request,
so merges never wait for it and it cannot be a required check. Its one job is skipped until the
repository variable `ENABLE_NIGHTLY` is `true`, so a copy of the template runs nothing and stays
green. It builds only the pinned default months with the real levels: no month list, local folder
or vars override reaches the demo. The checks that read the demo database run only after a demo
that exited 0, and a red demo already carries its reason in the job summary. These tests read the
file as data: they cannot prove a live run (plan 04-10 does), only that its design is not edited
away. `nightly_problems` returns every broken rule, so each rule has a fail-first doctored copy.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable

import pytest
import yaml
from conventions.test_entrypoints import makefile_recipe
from conventions.test_gate import FULL_SHA, _triggers
from conventions.test_handle import HANDLE_RE
from support.dbt_cli import REPO_ROOT

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "nightly.yml"
PAGES = REPO_ROOT / ".github" / "workflows" / "pages.yml"
MAKEFILE = REPO_ROOT / "Makefile"
VARIABLE_GUARD = "vars.ENABLE_NIGHTLY == 'true'"
PINS = (
    "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1",
    "astral-sh/setup-uv@1db35dd8eee11c5894872e0b383ae6c406cf738d # v10.4.0",
    "actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9 # v6.1.0",
)
SYNC_RUN = "uv sync --locked --extra fabric"
DEMO_RUN = 'uv run --locked demo --summary-file "$GITHUB_STEP_SUMMARY"'
STEP_KINDS = [
    "actions/checkout",
    "astral-sh/setup-uv",
    "run",
    "run",
    "actions/cache",
    "run",
    "run",
]
# Built from parts so this file does not itself name the example dataset.
HANDLE_WORD = "ta" + "xi"
BANNED_DEMO_FLAGS = ("--months", "--from-dir", "--vars")
Doctor = Callable[[dict, str], tuple[dict, str]]


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _steps(workflow: dict) -> list[dict]:
    return workflow["jobs"]["nightly"]["steps"]


def _kind(step: dict) -> str:
    return step["uses"].split("@")[0] if "uses" in step else "run"


def _trigger_key(workflow: dict) -> object:
    """PyYAML follows YAML 1.1 and loads a bare `on` key as the boolean True."""
    return True if True in workflow else "on"


def _pages_build_steps() -> list[dict]:
    return yaml.safe_load(PAGES.read_text())["jobs"]["build"]["steps"]


def _pages_step(pages: list[dict], matches: Callable[[dict], bool]) -> dict:
    found = [step for step in pages if matches(step)]
    assert len(found) == 1
    return found[0]


def _check_recipe() -> str:
    recipe = makefile_recipe(MAKEFILE.read_text(), "check")
    assert len(recipe) == 1, "the Makefile check recipe must be one line"
    return recipe[0].replace("uv run", "uv run --locked", 1)


def _trigger_problems(workflow: dict) -> list[str]:
    problems = []
    triggers = _triggers(workflow)
    if set(triggers) != {"schedule", "workflow_dispatch"}:
        problems.append(f"triggers are {sorted(map(str, triggers))}, not schedule and dispatch")
    schedule = triggers.get("schedule") or []
    crons = [entry.get("cron", "") for entry in schedule if isinstance(entry, dict)]
    if len(crons) != 1:
        problems.append("the schedule must hold exactly one cron entry")
    for cron in crons:
        minute = cron.split()[0] if cron.split() else ""
        if not minute.isdigit() or int(minute) == 0:
            problems.append(f"the cron minute of {cron!r} is on the hour or not a fixed minute")
    return problems


def _job_problems(workflow: dict) -> list[str]:
    problems = []
    if workflow.get("permissions") != {"contents": "read"}:
        problems.append("workflow permissions are not exactly contents: read")
    if list(workflow["jobs"]) != ["nightly"]:
        return [*problems, "the workflow must have exactly one job, nightly"]
    job = workflow["jobs"]["nightly"]
    if "permissions" in job:
        problems.append("the nightly job has permissions of its own")
    if VARIABLE_GUARD not in str(job.get("if", "")):
        problems.append("nightly.if lacks the ENABLE_NIGHTLY guard")
    if job.get("timeout-minutes") != 30:
        problems.append("nightly.timeout-minutes is not 30")
    if workflow.get("concurrency") != {"group": "nightly", "cancel-in-progress": False}:
        problems.append("concurrency is not group nightly with cancel-in-progress false")
    return problems


def _step_problems(steps: list[dict]) -> list[str]:
    if [_kind(step) for step in steps] != STEP_KINDS:
        return ["the steps are not checkout, setup-uv, sync, key, cache, demo, check in order"]
    problems = []
    pages = _pages_build_steps()
    shared = {
        "the checkout step": (steps[0], pages[0]),
        "the setup-uv step": (steps[1], pages[1]),
        "the cache key step": (
            steps[3],
            _pages_step(pages, lambda step: step.get("id") == "raw"),
        ),
        "the cache step": (
            steps[4],
            _pages_step(pages, lambda step: str(step.get("uses", "")).startswith("actions/cache@")),
        ),
    }
    for label, (ours, theirs) in shared.items():
        if ours != theirs:
            problems.append(f"{label} differs from pages.yml's build job")
    if steps[2]["run"] != SYNC_RUN:
        problems.append(f"the sync step is not `{SYNC_RUN}`")
    demo = steps[5]["run"]
    if demo != DEMO_RUN:
        problems.append(f"the demo step is not `{DEMO_RUN}`")
    for flag in BANNED_DEMO_FLAGS:
        if flag in demo:
            problems.append(f"the demo step passes {flag}")
    check = steps[6]
    if "if" in check:
        problems.append("the check step has an if: of its own")
    if check["run"] != _check_recipe():
        problems.append("the check step is not the Makefile check recipe run with --locked")
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
        problems.append("the number of pinned actions is not 3")
    for banned in ("secrets.", "pull_request", "issues:"):
        if banned in text:
            problems.append(f"the file holds {banned!r}")
    if HANDLE_RE.search(text):
        problems.append("the file names the example dataset")
    return problems


def nightly_problems(workflow: dict, text: str) -> list[str]:
    """Return every rule the workflow breaks (D-14 to D-17); an empty list is correct."""
    problems = _trigger_problems(workflow) + _job_problems(workflow)
    if "nightly" in workflow["jobs"]:
        problems += _step_problems(_steps(workflow))
    return problems + _text_problems(workflow, text)


def test_the_real_workflow_breaks_no_rule() -> None:
    assert nightly_problems(_workflow(), WORKFLOW.read_text()) == []


def test_the_workflow_is_named_nightly_with_one_job_called_nightly() -> None:
    workflow = _workflow()
    assert workflow["name"] == "nightly"
    assert list(workflow["jobs"]) == ["nightly"]
    assert workflow["jobs"]["nightly"]["name"] == "nightly"


def test_it_runs_on_one_cron_entry_off_the_hour_and_by_hand() -> None:
    triggers = _triggers(_workflow())
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert triggers["schedule"] == [{"cron": "23 5 * * *"}]


def test_the_job_is_bounded_and_runs_on_ubuntu() -> None:
    job = _workflow()["jobs"]["nightly"]
    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] == 30


def test_the_demo_step_is_the_only_one_that_reads_the_job_summary_path_and_has_no_expression() -> (
    None
):
    steps = _steps(_workflow())
    assert [s["run"] for s in steps if "GITHUB_STEP_SUMMARY" in s.get("run", "")] == [DEMO_RUN]
    assert all("${{" not in s.get("run", "") for s in steps)


def test_the_check_step_is_the_makefile_check_recipe_run_with_locked() -> None:
    check = _steps(_workflow())[-1]
    assert check["run"] == _check_recipe()
    assert check["run"] == 'uv run --locked --extra fabric pytest tests -q -m "not network"'
    assert "if" not in check


def test_the_cache_key_step_and_the_cache_step_are_the_ones_pages_uses() -> None:
    steps = _steps(_workflow())
    assert steps[3] == _pages_step(_pages_build_steps(), lambda s: s.get("id") == "raw")
    assert "$GITHUB_OUTPUT" in steps[3]["run"]
    assert steps[4]["with"] == {"path": "data/raw", "key": "${{ steps.raw.outputs.key }}"}


def test_the_three_pins_are_the_commits_pages_uses_with_their_version_comments() -> None:
    text = WORKFLOW.read_text()
    pages = PAGES.read_text()
    for pin in PINS:
        assert pin in text
        assert pin in pages
    assert len(re.findall(r"@[0-9a-f]{40} # v", text)) == 3


def test_the_header_says_who_runs_it_and_that_it_never_blocks_a_merge() -> None:
    header = WORKFLOW.read_text().split("\nname:")[0]
    assert "ENABLE_NIGHTLY" in header
    assert "required check" in header
    assert "60 days" in header


def _edit_job(workflow: dict, text: str, change: Callable[[dict], None]) -> tuple[dict, str]:
    doctored = copy.deepcopy(workflow)
    change(doctored["jobs"]["nightly"])
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


def _append_text(extra: str) -> Doctor:
    return lambda workflow, text: (workflow, text + extra)


def _append_to_run(extra: str) -> Callable[[dict], None]:
    return lambda step: step.update(run=step["run"] + extra)


DOCTORED = {
    "no variable guard": (
        lambda wf, tx: _edit_job(wf, tx, lambda job: job.update({"if": "${{ true }}"})),
        "nightly.if lacks the ENABLE_NIGHTLY guard",
    ),
    "a pull request trigger": (_add_trigger("pull_request"), "triggers are"),
    "a push trigger": (_add_trigger("push"), "triggers are"),
    "a cron on the hour": (
        _edit_workflow(lambda wf: wf[_trigger_key(wf)]["schedule"][0].update(cron="0 5 * * *")),
        "the cron minute",
    ),
    "two cron entries": (
        _edit_workflow(lambda wf: wf[_trigger_key(wf)]["schedule"].append({"cron": "41 9 * * *"})),
        "exactly one cron entry",
    ),
    "issues write": (
        _edit_workflow(lambda wf: wf["permissions"].update(issues="write")),
        "workflow permissions are not exactly contents: read",
    ),
    "permissions on the job": (
        lambda wf, tx: _edit_job(wf, tx, lambda job: job.update(permissions={"contents": "read"})),
        "the nightly job has permissions of its own",
    ),
    "no timeout": (
        lambda wf, tx: _edit_job(wf, tx, lambda job: job.pop("timeout-minutes")),
        "timeout-minutes is not 30",
    ),
    "a run that cancels": (
        _edit_workflow(lambda wf: wf["concurrency"].update({"cancel-in-progress": True})),
        "concurrency is not group nightly",
    ),
    "an action pinned by tag": (
        _edit_step(0, lambda step: step.update(uses="actions/checkout@v7")),
        "is not pinned to a full commit SHA",
    ),
    "an expression in a run": (
        _edit_step(2, _append_to_run(" ${{ github.ref }}")),
        "a run script holds an expression",
    ),
    "months on the demo": (
        _edit_step(5, _append_to_run(" --months 2024-01")),
        "the demo step passes --months",
    ),
    "a local folder on the demo": (
        _edit_step(5, _append_to_run(" --from-dir files")),
        "the demo step passes --from-dir",
    ),
    "a vars override on the demo": (
        _edit_step(5, _append_to_run(" --vars '{}'")),
        "the demo step passes --vars",
    ),
    "no summary file argument": (
        _edit_step(5, lambda step: step.update(run="uv run --locked demo")),
        "the demo step is not",
    ),
    "always on the check step": (
        _edit_step(6, lambda step: step.update({"if": "always()"})),
        "the check step has an if: of its own",
    ),
    "a check step without locked": (
        _edit_step(6, lambda step: step.update(run="uv run --extra fabric pytest tests -q")),
        "the check step is not the Makefile check recipe",
    ),
    "a different cache key step": (
        _edit_step(3, lambda step: step.update(run="echo key=x >> $GITHUB_OUTPUT")),
        "the cache key step differs from pages.yml",
    ),
    "a missing cache step": (
        lambda wf, tx: _edit_job(wf, tx, lambda job: job["steps"].pop(4)),
        "the steps are not checkout",
    ),
    "a secret reference": (_append_text("\n# ${{ secrets.TOKEN }}\n"), "'secrets.'"),
    "a pull request word in a comment": (_append_text("\n# pull_request\n"), "'pull_request'"),
    "an issues permission in the text": (_append_text("\n# issues: write\n"), "'issues:'"),
    "an example dataset word": (
        _append_text("\n# the " + HANDLE_WORD + " data\n"),
        "names the example dataset",
    ),
}


@pytest.mark.parametrize("name", list(DOCTORED))
def test_a_doctored_copy_is_reported(name: str) -> None:
    doctor, expected = DOCTORED[name]
    workflow, text = doctor(_workflow(), WORKFLOW.read_text())
    problems = nightly_problems(workflow, text)
    assert any(expected in problem for problem in problems), (name, problems)

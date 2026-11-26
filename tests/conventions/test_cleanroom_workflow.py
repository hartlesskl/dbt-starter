"""The clean-room workflow runs the demo on three systems and runs only where opted in.

DEMO-08 and D-16 to D-18: `cleanroom.yml` runs the one-command demo from a fresh checkout, with
no cache, on ubuntu, macos and windows, twice per system (the second run reuses the DuckDB file
and the downloads, where a Windows file lock would bite). It runs on one weekly cron entry and by
hand, never on a pull request, so merges never wait for it. Its one job is skipped until the
repository variable `ENABLE_CLEANROOM` is `true`, so a copy of the template runs nothing and stays
green. The Python is the one `.python-version` names, installed by `uv sync --locked`, so no step
names another. No step lists the git configuration, because that prints runner paths into a public
log. These tests read the file as data: they cannot prove a live run (plan 06-19 does), only that
its design is not edited away. `cleanroom_problems` returns every broken rule, so each rule has a
fail-first doctored copy.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable

import pytest
import yaml
from conventions.test_gate import FULL_SHA, _triggers
from conventions.test_handle import HANDLE_RE
from support.dbt_cli import REPO_ROOT

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "cleanroom.yml"
PYTHON_VERSION_FILE = REPO_ROOT / ".python-version"
VARIABLE_GUARD = "vars.ENABLE_CLEANROOM == 'true'"
MATRIX_OS = ("ubuntu-latest", "macos-latest", "windows-latest")
PINS = (
    "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1",
    "astral-sh/setup-uv@1db35dd8eee11c5894872e0b383ae6c406cf738d # v10.4.0",
)
SYNC_RUN = "uv sync --locked"
VERSION_RUN = "uv run --locked python --version"
DEMO_RUN = "uv run --locked demo --summary-file $env:GITHUB_STEP_SUMMARY"
STEP_KINDS = [
    "actions/checkout",
    "astral-sh/setup-uv",
    "run",
    "run",
    "run",
    "run",
]
# Built from parts so this file does not itself name the example dataset.
HANDLE_WORD = "ta" + "xi"
GIT_CONFIG_RE = re.compile(r"\bgit\s+(config|var)\b")
UV_PYTHON_RE = re.compile(r"\bUV_PYTHON")
Doctor = Callable[[dict], dict]


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _job(workflow: dict) -> dict:
    return workflow["jobs"]["cleanroom"]


def _kind(step: dict) -> str:
    return step["uses"].split("@")[0] if "uses" in step else "run"


def _trigger_key(workflow: dict) -> object:
    """PyYAML follows YAML 1.1 and loads a bare `on` key as the boolean True."""
    return True if True in workflow else "on"


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
        fields = cron.split()
        if len(fields) != 5:
            problems.append(f"the cron {cron!r} does not have five fields")
            continue
        minute, _hour, _day, _month, weekday = fields
        if not minute.isdigit() or int(minute) == 0:
            problems.append(f"the cron minute of {cron!r} is on the hour or not a fixed minute")
        if not re.fullmatch(r"[0-7]", weekday):
            problems.append(f"the cron {cron!r} does not run once a week on one day")
    return problems


def _job_problems(workflow: dict) -> list[str]:
    problems = []
    if workflow.get("permissions") != {"contents": "read"}:
        problems.append("workflow permissions are not exactly contents: read")
    if workflow.get("concurrency") != {"group": "cleanroom", "cancel-in-progress": False}:
        problems.append("concurrency is not group cleanroom with cancel-in-progress false")
    if list(workflow["jobs"]) != ["cleanroom"]:
        return [*problems, "the workflow must have exactly one job, cleanroom"]
    job = _job(workflow)
    if "permissions" in job:
        problems.append("the cleanroom job has permissions of its own")
    if job.get("name") != "cleanroom":
        problems.append("the job name is not cleanroom")
    guard = str(job.get("if", ""))
    if VARIABLE_GUARD not in guard:
        problems.append("cleanroom.if lacks the ENABLE_CLEANROOM guard")
    if guard.count("ENABLE_CLEANROOM") != 1:
        problems.append("the ENABLE_CLEANROOM guard must appear exactly once")
    if not isinstance(job.get("timeout-minutes"), int):
        problems.append("cleanroom.timeout-minutes is not set")
    strategy = job.get("strategy") or {}
    if strategy.get("fail-fast") is not False:
        problems.append("strategy.fail-fast is not false")
    if tuple((strategy.get("matrix") or {}).get("os") or ()) != MATRIX_OS:
        problems.append(f"strategy.matrix.os is not exactly {list(MATRIX_OS)}")
    if job.get("runs-on") != "${{ matrix.os }}":
        problems.append("runs-on is not ${{ matrix.os }}")
    if (job.get("defaults") or {}).get("run", {}).get("shell") != "pwsh":
        problems.append("defaults.run.shell is not pwsh")
    return problems


def _step_problems(steps: list[dict]) -> list[str]:
    if [_kind(step) for step in steps] != STEP_KINDS:
        return ["the steps are not checkout, setup-uv, sync, version, demo, demo in order"]
    problems = []
    if steps[0].get("with") != {"persist-credentials": False}:
        problems.append("the checkout step is not exactly persist-credentials: false")
    if steps[1].get("with") != {"enable-cache": False}:
        problems.append("the setup-uv step is not exactly enable-cache: false")
    expected = (SYNC_RUN, VERSION_RUN, DEMO_RUN, DEMO_RUN)
    for label, step, run in zip(("sync", "version", "demo", "second demo"), steps[2:], expected):
        if step["run"].strip() != run:
            problems.append(f"the {label} step is not `{run}`")
    return problems


def _text_problems(workflow: dict) -> list[str]:
    problems = []
    for step in _job(workflow)["steps"] if "cleanroom" in workflow["jobs"] else []:
        if "uses" in step and not FULL_SHA.match(step["uses"]):
            problems.append(f"{step['uses']} is not pinned to a full commit SHA")
        run = step.get("run", "")
        if "${{" in run:
            problems.append(f"a run script holds an expression: {run}")
        if GIT_CONFIG_RE.search(run):
            problems.append(f"a run script lists the git configuration: {run}")
        if "--extra" in run:
            problems.append(f"a run script passes --extra: {run}")
        if "python-version" in step.get("with", {}):
            problems.append("a step passes a python-version input")
    dumped = json.dumps(workflow, default=str)
    if "actions/cache" in dumped:
        problems.append("the workflow uses actions/cache")
    if "python-version" in dumped:
        problems.append("the workflow names a python-version")
    if UV_PYTHON_RE.search(dumped):
        problems.append("the workflow sets a UV_PYTHON variable")
    for banned in ("secrets.", "pull_request"):
        if banned in dumped:
            problems.append(f"the workflow holds {banned!r}")
    if HANDLE_RE.search(dumped):
        problems.append("the workflow names the example dataset")
    return problems


def cleanroom_problems(workflow: dict) -> list[str]:
    """Return every rule the workflow breaks (D-16 to D-18); an empty list is correct."""
    problems = _trigger_problems(workflow) + _job_problems(workflow)
    if "cleanroom" in workflow["jobs"]:
        problems += _step_problems(_job(workflow)["steps"])
    return problems + _text_problems(workflow)


def test_the_real_workflow_breaks_no_rule() -> None:
    assert cleanroom_problems(_workflow()) == []


def test_the_workflow_is_named_cleanroom_with_one_job_called_cleanroom() -> None:
    workflow = _workflow()
    assert workflow["name"] == "cleanroom"
    assert list(workflow["jobs"]) == ["cleanroom"]
    assert _job(workflow)["name"] == "cleanroom"


def test_it_runs_on_one_weekly_cron_entry_off_the_hour_and_by_hand() -> None:
    triggers = _triggers(_workflow())
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert triggers["schedule"] == [{"cron": "41 6 * * 1"}]


def test_the_matrix_covers_the_three_systems_and_never_stops_early() -> None:
    job = _job(_workflow())
    assert job["strategy"]["matrix"]["os"] == list(MATRIX_OS)
    assert "windows-latest" in MATRIX_OS
    assert job["strategy"]["fail-fast"] is False
    assert job["runs-on"] == "${{ matrix.os }}"
    assert job["defaults"]["run"]["shell"] == "pwsh"


def test_the_guard_appears_once_in_the_file() -> None:
    assert WORKFLOW.read_text(encoding="utf-8").count(VARIABLE_GUARD) == 1


def test_the_demo_runs_twice_and_each_run_writes_the_job_summary_from_the_runner_variable() -> None:
    steps = _job(_workflow())["steps"]
    demos = [step["run"].strip() for step in steps if "demo" in step.get("run", "")]
    assert demos == [DEMO_RUN, DEMO_RUN]
    assert all("${{" not in step.get("run", "") for step in steps)


def test_the_pins_are_the_commits_the_nightly_uses_with_their_version_comments() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    nightly = (REPO_ROOT / ".github" / "workflows" / "nightly.yml").read_text(encoding="utf-8")
    for pin in PINS:
        assert pin in text
        assert pin in nightly
    assert len(re.findall(r"@[0-9a-f]{40} # v", text)) == len(PINS)


def test_the_real_file_text_holds_no_banned_word() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    for banned in ("secrets.", "pull_request", "UV_PYTHON", "python-version", "actions/cache"):
        assert banned not in text, banned
    assert not HANDLE_RE.search(text)


def test_the_header_says_who_runs_it_and_that_it_never_runs_per_pull_request() -> None:
    header = WORKFLOW.read_text(encoding="utf-8").split("\nname:")[0]
    assert "ENABLE_CLEANROOM" in header
    assert "never" in header
    assert "60 days" in header
    assert "PowerShell" in header


def test_the_clean_room_python_is_pinned_to_3_12() -> None:
    """`uv sync --locked` installs the Python `.python-version` names, so no step names one."""
    assert PYTHON_VERSION_FILE.read_text(encoding="utf-8").strip() == "3.12"


def _edit_job(change: Callable[[dict], None]) -> Doctor:
    def doctor(workflow: dict) -> dict:
        doctored = copy.deepcopy(workflow)
        change(_job(doctored))
        return doctored

    return doctor


def _edit_step(index: int, change: Callable[[dict], None]) -> Doctor:
    return _edit_job(lambda job: change(job["steps"][index]))


def _edit_workflow(change: Callable[[dict], None]) -> Doctor:
    def doctor(workflow: dict) -> dict:
        doctored = copy.deepcopy(workflow)
        change(doctored)
        return doctored

    return doctor


def _add_trigger(name: str) -> Doctor:
    return _edit_workflow(lambda wf: wf[_trigger_key(wf)].update({name: {"branches": ["main"]}}))


def _append_to_run(extra: str) -> Callable[[dict], None]:
    return lambda step: step.update(run=step["run"] + extra)


def _insert_step(index: int, step: dict) -> Doctor:
    return _edit_job(lambda job: job["steps"].insert(index, step))


DOCTORED = {
    "no variable guard": (_edit_job(lambda job: job.update({"if": "${{ true }}"})), "lacks the"),
    "the guard twice": (
        _edit_job(
            lambda job: job.update(
                {"if": "${{ vars.ENABLE_CLEANROOM == 'true' || vars.ENABLE_CLEANROOM }}"}
            )
        ),
        "exactly once",
    ),
    "a pull request trigger": (_add_trigger("pull_request"), "triggers are"),
    "a push trigger": (_add_trigger("push"), "triggers are"),
    "a cron on the hour": (
        _edit_workflow(lambda wf: wf[_trigger_key(wf)]["schedule"][0].update(cron="0 6 * * 1")),
        "the cron minute",
    ),
    "a daily cron": (
        _edit_workflow(lambda wf: wf[_trigger_key(wf)]["schedule"][0].update(cron="41 6 * * *")),
        "once a week",
    ),
    "two cron entries": (
        _edit_workflow(lambda wf: wf[_trigger_key(wf)]["schedule"].append({"cron": "41 9 * * 2"})),
        "exactly one cron entry",
    ),
    "issues write": (
        _edit_workflow(lambda wf: wf["permissions"].update(issues="write")),
        "workflow permissions are not exactly contents: read",
    ),
    "permissions on the job": (
        _edit_job(lambda job: job.update(permissions={"contents": "read"})),
        "the cleanroom job has permissions of its own",
    ),
    "no timeout": (_edit_job(lambda job: job.pop("timeout-minutes")), "timeout-minutes is not set"),
    "a run that cancels": (
        _edit_workflow(lambda wf: wf["concurrency"].update({"cancel-in-progress": True})),
        "concurrency is not group cleanroom",
    ),
    "fail-fast on": (
        _edit_job(lambda job: job["strategy"].update({"fail-fast": True})),
        "fail-fast is not false",
    ),
    "no windows runner": (
        _edit_job(lambda job: job["strategy"]["matrix"].update(os=list(MATRIX_OS[:2]))),
        "strategy.matrix.os is not exactly",
    ),
    "one fixed runner": (
        _edit_job(lambda job: job.update({"runs-on": "ubuntu-latest"})),
        "runs-on is not",
    ),
    "bash as the shell": (
        _edit_job(lambda job: job["defaults"]["run"].update(shell="bash")),
        "defaults.run.shell is not pwsh",
    ),
    "an action pinned by tag": (
        _edit_step(0, lambda step: step.update(uses="actions/checkout@v7")),
        "is not pinned to a full commit SHA",
    ),
    "credentials kept by checkout": (
        _edit_step(0, lambda step: step.pop("with")),
        "the checkout step is not exactly persist-credentials: false",
    ),
    "a cache on setup-uv": (
        _edit_step(1, lambda step: step.update({"with": {"enable-cache": True}})),
        "the setup-uv step is not exactly enable-cache: false",
    ),
    "an expression in a run": (
        _edit_step(2, _append_to_run(" ${{ github.ref }}")),
        "a run script holds an expression",
    ),
    "the fabric extra": (
        _edit_step(2, lambda step: step.update(run="uv sync --locked --extra fabric")),
        "the sync step is not",
    ),
    "a sync that may relock": (
        _edit_step(2, lambda step: step.update(run="uv sync")),
        "the sync step is not",
    ),
    "a git configuration listing": (
        _insert_step(3, {"run": "git config --list"}),
        "lists the git configuration",
    ),
    "a git config read in the version step": (
        _edit_step(3, _append_to_run("; git config --get core.autocrlf")),
        "lists the git configuration",
    ),
    "a python-version input": (
        _edit_step(1, lambda step: step.update({"with": {"enable-cache": False, "python-version": "3.13"}})),
        "python-version",
    ),
    "a UV_PYTHON variable": (
        _edit_job(lambda job: job.update(env={"UV_PYTHON": "3.13"})),
        "UV_PYTHON",
    ),
    "a UV_PYTHON_PREFERENCE variable": (
        _edit_workflow(lambda wf: wf.update(env={"UV_PYTHON_PREFERENCE": "system"})),
        "UV_PYTHON",
    ),
    "a version step that does more": (
        _edit_step(3, _append_to_run("; uv --version")),
        "the version step is not",
    ),
    "a missing second demo": (_edit_job(lambda job: job["steps"].pop()), "the steps are not"),
    "a different demo step": (
        _edit_step(5, lambda step: step.update(run="uv run --locked demo")),
        "the second demo step is not",
    ),
    "months on the demo": (
        _edit_step(4, _append_to_run(" --months 2024-01")),
        "the demo step is not",
    ),
    "a cache action": (
        _insert_step(2, {"uses": "actions/cache@" + "0" * 40, "with": {"path": "x", "key": "y"}}),
        "actions/cache",
    ),
    "a secret reference": (
        _edit_step(3, _append_to_run("; echo secrets.TOKEN")),
        "'secrets.'",
    ),
    "an example dataset word": (
        _edit_step(3, _append_to_run("; echo " + HANDLE_WORD)),
        "names the example dataset",
    ),
}


@pytest.mark.parametrize("name", list(DOCTORED))
def test_a_doctored_copy_is_reported(name: str) -> None:
    doctor, expected = DOCTORED[name]
    problems = cleanroom_problems(doctor(_workflow()))
    assert any(expected in problem for problem in problems), (name, problems)

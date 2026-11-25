"""The fabric workflow builds the fixture on Fabric on demand and runs only where opted in.

CI-04 and D-16 to D-19: `fabric.yml` starts only by hand (`workflow_dispatch`, no inputs), never on
a pull request, push or schedule, so it cannot be a required check and spends no capacity unasked.
Its one job is skipped until the repository variable `ENABLE_FABRIC` is `true`; with the variable
but a missing setting it writes a one-line note and stays green. With every setting present it
signs in through azure/login (OIDC, no stored secret), lands the fixture into the CI Lakehouse
and builds it there, never into the record Lakehouse. This file is new rather than an edit of the
nightly test because the Fabric job needs `id-token: write` and `secrets.`, which that test
forbids (research Pitfall 10). The workflow is held as data: these tests cannot prove a live run
(plan 05-13 does), only that its opt-in, least-privilege and pinning design is not edited away.
`fabric_problems` returns every broken rule, so each rule has a fail-first doctored copy (T-05-36
to T-05-40).
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Iterator

import pytest
import yaml
from conventions.test_gate import FULL_SHA, _triggers
from conventions.test_handle import HANDLE_RE
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
# The five settings the job needs before it does anything past the note (D-19).
SETTINGS = (
    "secrets.AZURE_CLIENT_ID",
    "secrets.AZURE_TENANT_ID",
    "secrets.FABRIC_WORKSPACE_ID",
    "secrets.FABRIC_CI_LAKEHOUSE_ID",
    "vars.FABRIC_CI_LAKEHOUSE",
)
# Every secret and variable the file may name. The record Lakehouse's settings are not among them
# (D-18): CI builds only into the Lakehouse the CI variable and secret name.
ALLOWED_REFERENCES = frozenset((*SETTINGS, "vars.ENABLE_FABRIC"))
# What the job env must hold, by name (D-17, D-18).
ENV_RULES = {
    "FABRIC_WORKSPACE_ID": "${{ secrets.FABRIC_WORKSPACE_ID }}",
    "FABRIC_LAKEHOUSE_ID": "${{ secrets.FABRIC_CI_LAKEHOUSE_ID }}",
    "FABRIC_LAKEHOUSE": "${{ vars.FABRIC_CI_LAKEHOUSE }}",
    "FABRIC_AUTHENTICATION": "CLI",
    # CI signs in as a service principal, whose token high-concurrency sessions refuse.
    "FABRIC_HIGH_CONCURRENCY": "false",
}
LOGIN_WITH = {
    "client-id": "${{ secrets.AZURE_CLIENT_ID }}",
    "tenant-id": "${{ secrets.AZURE_TENANT_ID }}",
    "allow-no-subscriptions": True,
}
REFERENCE_RE = re.compile(r"\b(secrets|vars)\.([A-Za-z0-9_]+)")
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
BANNED_TEXT = ("issues:", "pull_request", "schedule:")
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


def _walk(value: object, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], str]]:
    """Yield (path, text) for every string in a parsed workflow, list items by position."""
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk(child, (*path, str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, (*path, str(index)))
    elif isinstance(value, str):
        yield path, value


def _trigger_problems(workflow: dict) -> list[str]:
    triggers = _triggers(workflow)
    if set(triggers) != {"workflow_dispatch"}:
        return [f"triggers are {sorted(map(str, triggers))}, not workflow_dispatch alone"]
    if triggers["workflow_dispatch"]:
        return ["workflow_dispatch takes inputs: a dispatch must carry nothing into a script"]
    return []


def _workflow_level_problems(workflow: dict) -> list[str]:
    problems = []
    if workflow.get("permissions") != {}:
        problems.append("workflow permissions are not empty")
    if workflow.get("concurrency") != {"group": "fabric", "cancel-in-progress": False}:
        problems.append("concurrency is not group fabric with cancel-in-progress false")
    return problems


def _job_problems(workflow: dict) -> list[str]:
    if list(workflow["jobs"]) != ["fabric"]:
        return ["the workflow must have exactly one job, fabric"]
    job = workflow["jobs"]["fabric"]
    problems = []
    if job.get("if") != "${{ " + VARIABLE_GUARD + " }}":
        problems.append("fabric.if is not the ENABLE_FABRIC guard")
    if job.get("permissions") != {"id-token": "write", "contents": "read"}:
        problems.append("the job permissions are not exactly id-token write and contents read")
    if job.get("timeout-minutes") != 60:
        problems.append("fabric.timeout-minutes is not 60")
    if job.get("runs-on") != "ubuntu-latest":
        problems.append("fabric.runs-on is not ubuntu-latest")
    return problems


def _env_problems(job: dict) -> list[str]:
    env = job.get("env") or {}
    problems = []
    gate = str(env.get("HAVE_SETTINGS", ""))
    for setting in SETTINGS:
        if f"{setting} != ''" not in gate:
            problems.append(f"HAVE_SETTINGS does not require {setting}")
    for name, expected in ENV_RULES.items():
        if env.get(name) != expected:
            problems.append(f"the job env {name} is not `{expected}`")
    return problems


def _step_problems(steps: list[dict]) -> list[str]:
    kinds = [_kind(step) for step in steps]
    if "astral-sh/setup-uv" not in kinds:
        return ["the workflow has no setup-uv step"]
    problems = []
    for step in steps:
        if "uses" in step and not FULL_SHA.match(step["uses"]):
            problems.append(f"{step['uses']} is not pinned to a full commit SHA")
        if "${{" in step.get("run", ""):
            problems.append(f"a run script holds an expression: {step['run']}")
    after = steps[kinds.index("astral-sh/setup-uv") + 1 :]
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
    return problems + _action_problems(steps)


def _action_problems(steps: list[dict]) -> list[str]:
    problems = []
    checkouts = [step for step in steps if _kind(step) == "actions/checkout"]
    for checkout in checkouts:
        if checkout.get("with") != {"persist-credentials": False}:
            problems.append("checkout does not set persist-credentials false")
    if len(checkouts) != 1:
        problems.append("the workflow must check out exactly once")
    pinned = {pin.split(" # ")[0] for pin in PINS}
    for step in steps:
        if "uses" in step and _kind(step) != "azure/login" and step["uses"] not in pinned:
            problems.append(f"{step['uses']} is not one of the pinned actions")
    logins = [step for step in steps if _kind(step) == "azure/login"]
    if len(logins) != 1:
        problems.append("the workflow must sign in through exactly one azure/login step")
    for login in logins:
        if login["uses"] != AZURE_LOGIN_PIN.split(" # ")[0]:
            problems.append("azure/login is not at the re-peeled v3.1.0 commit")
        with_ = login.get("with") or {}
        if "creds" in with_:
            problems.append("azure/login takes a creds input, which is a stored secret")
        if with_.get("allow-no-subscriptions") is not True:
            problems.append("azure/login does not set allow-no-subscriptions true")
        if with_ != LOGIN_WITH:
            problems.append("azure/login's with: is not the client id, tenant id and the flag")
    return problems


def _secret_problems(workflow: dict) -> list[str]:
    """`secrets.` may appear only in the job env and in azure/login's client and tenant ids."""
    problems = []
    steps = _steps(workflow) if "fabric" in workflow["jobs"] else []
    login_indexes = {str(i) for i, step in enumerate(steps) if _kind(step) == "azure/login"}
    for path, value in _walk(workflow):
        if "secrets." not in value:
            continue
        in_env = path[:3] == ("jobs", "fabric", "env")
        in_login = (
            path[:3] == ("jobs", "fabric", "steps")
            and path[3] in login_indexes
            and path[4:5] == ("with",)
            and path[5:6] in (("client-id",), ("tenant-id",))
        )
        if not (in_env or in_login):
            problems.append(f"secrets are referenced outside the job env and azure/login: {path}")
    return problems


def _text_problems(workflow: dict, text: str) -> list[str]:
    problems = []
    for pin in PINS:
        if pin not in text:
            problems.append(f"the pin {pin} is missing")
    if len(re.findall(r"@[0-9a-f]{40} # v", text)) != len(PINS):
        problems.append(f"the number of pinned actions is not {len(PINS)}")
    for path in DOCS_PATH_RE.findall(text):
        if not (REPO_ROOT / path).exists():
            problems.append(f"the workflow names {path}, which does not exist")
    for kind, name in REFERENCE_RE.findall(text):
        if f"{kind}.{name}" not in ALLOWED_REFERENCES:
            problems.append(f"the file reads {kind}.{name}, which is not a CI Fabric setting")
    for banned in BANNED_TEXT:
        if banned in text:
            problems.append(f"the file holds {banned!r}")
    if UUID_RE.search(text):
        problems.append("the file holds an id: settings are named, never written")
    if HANDLE_RE.search(text):
        problems.append("the file names the example dataset")
    return problems


def fabric_problems(workflow: dict, text: str) -> list[str]:
    """Return every rule the workflow breaks (D-16 to D-19); an empty list is correct."""
    problems = _trigger_problems(workflow) + _workflow_level_problems(workflow)
    problems += _job_problems(workflow)
    if "fabric" in workflow["jobs"]:
        problems += _env_problems(workflow["jobs"]["fabric"])
        problems += _step_problems(_steps(workflow))
    return problems + _secret_problems(workflow) + _text_problems(workflow, text)


def test_the_real_workflow_breaks_no_rule() -> None:
    assert fabric_problems(_workflow(), WORKFLOW.read_text()) == []


def test_the_workflow_is_named_fabric_with_one_job_called_fabric() -> None:
    workflow = _workflow()
    assert workflow["name"] == "fabric"
    assert list(workflow["jobs"]) == ["fabric"]
    assert workflow["jobs"]["fabric"]["name"] == "fabric"


def test_it_runs_only_by_hand_and_takes_no_inputs() -> None:
    triggers = _triggers(_workflow())
    assert list(triggers) == ["workflow_dispatch"]
    assert not triggers["workflow_dispatch"]


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


def test_the_summary_path_is_the_runners_variable_with_no_expression_in_any_script() -> None:
    steps = _steps(_workflow())
    assert all("${{" not in step.get("run", "") for step in steps)
    assert [s["run"] for s in steps if "--summary-file" in s.get("run", "")] == [BUILD_RUN]


def test_the_header_says_who_runs_it_and_that_it_never_blocks_a_merge() -> None:
    header = WORKFLOW.read_text().split("\nname:")[0]
    for phrase in ("ENABLE_FABRIC", "required check", "workflow_dispatch", "CI Lakehouse"):
        assert phrase in header, phrase


def test_the_pins_are_the_nightly_ones_plus_the_azure_login_commit() -> None:
    text = WORKFLOW.read_text()
    assert len(PINS) == 3
    for pin in PINS:
        assert pin in text
        assert FULL_SHA.match(pin.split(" # ")[0]), pin
        assert pin.split(" # ")[1].startswith("v"), pin
    assert text.count("a641126d1b8aa4d1fa005f4f92df94a3a4c4c906") == 1


def _edit_job(workflow: dict, text: str, change: Callable[[dict], None]) -> tuple[dict, str]:
    doctored = copy.deepcopy(workflow)
    change(doctored["jobs"]["fabric"])
    return doctored, text


def _edit_step(index: int, change: Callable[[dict], None]) -> Doctor:
    return lambda workflow, text: _edit_job(workflow, text, lambda job: change(job["steps"][index]))


def _edit_env(name: str, value: str) -> Doctor:
    return lambda workflow, text: _edit_job(
        workflow, text, lambda job: job["env"].update({name: value})
    )


def _edit_workflow(change: Callable[[dict], None]) -> Doctor:
    def doctor(workflow: dict, text: str) -> tuple[dict, str]:
        doctored = copy.deepcopy(workflow)
        change(doctored)
        return doctored, text

    return doctor


def _add_trigger(name: str) -> Doctor:
    return _edit_workflow(lambda wf: wf[_trigger_key(wf)].update({name: {"branches": ["main"]}}))


def _append_text(extra: str) -> Doctor:
    return lambda workflow, text: (workflow, text + extra)


def _append_to_run(extra: str) -> Callable[[dict], None]:
    return lambda step: step.update(run=step["run"] + extra)


# Step positions in the real file: checkout 0, setup-uv 1, note 2, sign-in 3, sync 4, land 5, build 6.
CHECKOUT, NOTE, LOGIN, SYNC, LAND, BUILD = 0, 2, 3, 4, 5, 6

DOCTORED = {
    "a pull request trigger": (_add_trigger("pull_request"), "triggers are"),
    "a push trigger": (_add_trigger("push"), "triggers are"),
    "a schedule trigger": (_add_trigger("schedule"), "triggers are"),
    "dispatch inputs": (
        _edit_workflow(
            lambda wf: wf[_trigger_key(wf)].update(
                workflow_dispatch={"inputs": {"ref": {"type": "string"}}}
            )
        ),
        "workflow_dispatch takes inputs",
    ),
    "no variable guard": (
        lambda wf, tx: _edit_job(wf, tx, lambda job: job.update({"if": "${{ true }}"})),
        "fabric.if is not the ENABLE_FABRIC guard",
    ),
    "a changed variable guard": (
        lambda wf, tx: _edit_job(
            wf, tx, lambda job: job.update({"if": "${{ vars.ENABLE_NIGHTLY == 'true' }}"})
        ),
        "fabric.if is not the ENABLE_FABRIC guard",
    ),
    "a second job": (
        _edit_workflow(lambda wf: wf["jobs"].update(other=copy.deepcopy(wf["jobs"]["fabric"]))),
        "exactly one job",
    ),
    "workflow permissions that are not empty": (
        _edit_workflow(lambda wf: wf.update(permissions={"contents": "read"})),
        "workflow permissions are not empty",
    ),
    "workflow permissions of issues write": (
        _edit_workflow(lambda wf: wf.update(permissions={"issues": "write"})),
        "workflow permissions are not empty",
    ),
    "job permissions with contents write": (
        lambda wf, tx: _edit_job(wf, tx, lambda job: job["permissions"].update(contents="write")),
        "the job permissions are not exactly",
    ),
    "job permissions without the id token": (
        lambda wf, tx: _edit_job(wf, tx, lambda job: job["permissions"].pop("id-token")),
        "the job permissions are not exactly",
    ),
    "job permissions with an extra scope": (
        lambda wf, tx: _edit_job(wf, tx, lambda job: job["permissions"].update(actions="write")),
        "the job permissions are not exactly",
    ),
    "an expression in a run script": (
        _edit_step(SYNC, _append_to_run(" ${{ github.ref }}")),
        "a run script holds an expression",
    ),
    "an action pinned by tag": (
        _edit_step(CHECKOUT, lambda step: step.update(uses="actions/checkout@v7")),
        "is not pinned to a full commit SHA",
    ),
    "an action pinned without a version comment": (
        lambda wf, tx: (wf, tx.replace(AZURE_LOGIN_PIN, AZURE_LOGIN_PIN.split(" # ")[0])),
        "the number of pinned actions is not",
    ),
    "azure/login at another commit": (
        _edit_step(LOGIN, lambda step: step.update(uses="azure/login@" + "0" * 40)),
        "azure/login is not at the re-peeled v3.1.0 commit",
    ),
    "azure/login with a creds input": (
        _edit_step(LOGIN, lambda step: step["with"].update(creds="{}")),
        "takes a creds input",
    ),
    "azure/login without allow-no-subscriptions": (
        _edit_step(LOGIN, lambda step: step["with"].pop("allow-no-subscriptions")),
        "does not set allow-no-subscriptions true",
    ),
    "azure/login with the flag false": (
        _edit_step(LOGIN, lambda step: step["with"].update({"allow-no-subscriptions": False})),
        "does not set allow-no-subscriptions true",
    ),
    "a secret in a step env": (
        _edit_step(
            BUILD, lambda step: step.update(env={"TOKEN": "${{ secrets.FABRIC_WORKSPACE_ID }}"})
        ),
        "secrets are referenced outside the job env and azure/login",
    ),
    "a secret in the azure/login subscription": (
        _edit_step(
            LOGIN,
            lambda step: step["with"].update({"subscription-id": "${{ secrets.AZURE_CLIENT_ID }}"}),
        ),
        "secrets are referenced outside the job env and azure/login",
    ),
    "a secret in the workflow env": (
        _edit_workflow(lambda wf: wf.update(env={"TOKEN": "${{ secrets.AZURE_CLIENT_ID }}"})),
        "secrets are referenced outside the job env and azure/login",
    ),
    "a build step without the settings condition": (
        _edit_step(BUILD, lambda step: step.pop("if")),
        "lacks the HAVE_SETTINGS condition",
    ),
    "a sign-in step without the settings condition": (
        _edit_step(LOGIN, lambda step: step.pop("if")),
        "lacks the HAVE_SETTINGS condition",
    ),
    "an install step on the wrong condition": (
        _edit_step(SYNC, lambda step: step.update({"if": "always()"})),
        "lacks the HAVE_SETTINGS condition",
    ),
    "no settings-missing note": (
        _edit_step(NOTE, lambda step: step.update({"if": SETTINGS_GUARD})),
        "exactly one step must carry the settings-missing condition",
    ),
    "a note that points nowhere": (
        _edit_step(
            NOTE, lambda step: step.update(run='echo "nothing ran" >> "$GITHUB_STEP_SUMMARY"')
        ),
        "does not write the one-line note",
    ),
    "a note that is not written to the summary": (
        _edit_step(NOTE, lambda step: step.update(run=f'echo "{NOTE_TEXT}"')),
        "does not append to the job summary",
    ),
    "a gate that skips a setting": (
        _edit_env(
            "HAVE_SETTINGS",
            "${{ secrets.AZURE_CLIENT_ID != '' && secrets.AZURE_TENANT_ID != '' "
            "&& secrets.FABRIC_WORKSPACE_ID != '' && vars.FABRIC_CI_LAKEHOUSE != '' }}",
        ),
        "HAVE_SETTINGS does not require secrets.FABRIC_CI_LAKEHOUSE_ID",
    ),
    "a Lakehouse name from the record variable": (
        _edit_env("FABRIC_LAKEHOUSE", "${{ vars.FABRIC_LAKEHOUSE }}"),
        "the job env FABRIC_LAKEHOUSE is not",
    ),
    "a Lakehouse id from the record secret": (
        _edit_env("FABRIC_LAKEHOUSE_ID", "${{ secrets.FABRIC_LAKEHOUSE_ID }}"),
        "the job env FABRIC_LAKEHOUSE_ID is not",
    ),
    "a workspace id typed into the env": (
        _edit_env("FABRIC_WORKSPACE_ID", "workspace"),
        "the job env FABRIC_WORKSPACE_ID is not",
    ),
    "a service-principal authentication": (
        _edit_env("FABRIC_AUTHENTICATION", "SPN"),
        "the job env FABRIC_AUTHENTICATION is not",
    ),
    "high-concurrency sessions left on in CI": (
        _edit_env("FABRIC_HIGH_CONCURRENCY", "true"),
        "the job env FABRIC_HIGH_CONCURRENCY is not",
    ),
    "a build without the summary file": (
        _edit_step(
            BUILD, lambda step: step.update(run="uv run --locked --extra fabric build-fabric")
        ),
        "does not run",
    ),
    "a build without the fixture selector": (
        _edit_step(
            BUILD,
            lambda step: step.update(
                run='uv run --locked --extra fabric build-fabric --summary-file "$GITHUB_STEP_SUMMARY"'
            ),
        ),
        "does not run",
    ),
    "no landing of the fixture": (
        _edit_step(LAND, lambda step: step.update(run="echo landed")),
        "does not run",
    ),
    "a build before the landing": (
        lambda wf, tx: _edit_job(
            wf, tx, lambda job: job["steps"].insert(LAND, job["steps"].pop(BUILD))
        ),
        "built before it is landed",
    ),
    "a checkout that persists credentials": (
        _edit_step(CHECKOUT, lambda step: step.pop("with")),
        "checkout does not set persist-credentials false",
    ),
    "a checkout with persist-credentials true": (
        _edit_step(CHECKOUT, lambda step: step["with"].update({"persist-credentials": True})),
        "checkout does not set persist-credentials false",
    ),
    "a run that cancels": (
        _edit_workflow(lambda wf: wf["concurrency"].update({"cancel-in-progress": True})),
        "concurrency is not group fabric",
    ),
    "a different concurrency group": (
        _edit_workflow(lambda wf: wf["concurrency"].update({"group": "other"})),
        "concurrency is not group fabric",
    ),
    "no timeout": (
        lambda wf, tx: _edit_job(wf, tx, lambda job: job.pop("timeout-minutes")),
        "timeout-minutes is not 60",
    ),
    "another runner": (
        lambda wf, tx: _edit_job(wf, tx, lambda job: job.update({"runs-on": "self-hosted"})),
        "runs-on is not ubuntu-latest",
    ),
    "an issues permission in the text": (_append_text("\n# issues: write\n"), "'issues:'"),
    "a pull request word in a comment": (_append_text("\n# pull_request\n"), "'pull_request'"),
    "a schedule word in a comment": (_append_text("\n# schedule:\n"), "'schedule:'"),
    "the record Lakehouse variable in a comment": (
        _append_text("\n# ${{ vars.FABRIC_LAKEHOUSE }}\n"),
        "which is not a CI Fabric setting",
    ),
    "an id written into the file": (
        _append_text("\n# 11111111-2222-3333-4444-555555555555\n"),
        "the file holds an id",
    ),
    "an example dataset word": (
        _append_text("\n# the " + "ta" + "xi data\n"),
        "names the example dataset",
    ),
    "a docs path that does not exist": (
        _append_text("\n# docs/no-such-page.md\n"),
        "docs/no-such-page.md, which does not exist",
    ),
}


def test_there_is_a_doctored_copy_for_every_behaviour_rule() -> None:
    assert len(DOCTORED) >= 18


@pytest.mark.parametrize("name", list(DOCTORED))
def test_a_doctored_copy_is_reported(name: str) -> None:
    doctor, expected = DOCTORED[name]
    workflow, text = doctor(_workflow(), WORKFLOW.read_text())
    problems = fabric_problems(workflow, text)
    assert any(expected in problem for problem in problems), (name, problems)


@pytest.mark.parametrize("name", list(DOCTORED))
def test_a_doctor_edits_a_copy_and_never_the_workflow_it_was_given(name: str) -> None:
    doctor, _ = DOCTORED[name]
    original = _workflow()
    snapshot = copy.deepcopy(original)
    doctor(original, WORKFLOW.read_text())
    assert original == snapshot

"""The Pages workflow builds the docs from a real download and deploys only where opted in.

DOCS-02, D-12, D-13 and D-17: `pages.yml` runs on pushes to `main` and by hand, never on a pull
request. Both jobs are skipped until the repository variable `ENABLE_PAGES` is `true`, so a copy
of the template runs nothing and stays green. The deploy job carries its own guards (the variable
and the `main` ref) and is the only job with `pages: write` and `id-token: write`. These tests
read the file as data: they cannot prove the live deploy, only that its design is not edited away.
"""

from __future__ import annotations

import copy
import re

import pytest
import yaml
from conventions.test_entrypoints import makefile_recipe
from conventions.test_gate import FULL_SHA, _triggers
from conventions.test_handle import HANDLE_RE
from support.dbt_cli import REPO_ROOT

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "pages.yml"
VARIABLE_GUARD = "vars.ENABLE_PAGES == 'true'"
MAIN_GUARD = "github.ref == 'refs/heads/main'"
PINS = (
    "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1",
    "astral-sh/setup-uv@1db35dd8eee11c5894872e0b383ae6c406cf738d # v10.4.0",
    "actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9 # v6.1.0",
    "actions/upload-pages-artifact@fc324d3547104276b827a68afc52ff2a11cc49c9 # v5.0.0",
    "actions/configure-pages@45bfe0192ca1faeb007ade9deae92b16b8254a0d # v6.0.0",
    "actions/deploy-pages@368f82528645a54fb793d4d04e342629a3f51346 # v5.0.1",
)


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _steps(workflow: dict, job: str) -> list[dict]:
    return workflow["jobs"][job]["steps"]


def _runs(workflow: dict, job: str) -> list[str]:
    return [step["run"] for step in _steps(workflow, job) if "run" in step]


def _step_using(workflow: dict, job: str, action: str) -> dict:
    matches = [s for s in _steps(workflow, job) if s.get("uses", "").startswith(action + "@")]
    assert len(matches) == 1, f"{job} must use {action} exactly once"
    return matches[0]


def guard_problems(workflow: dict) -> list[str]:
    """Return what is missing from the two job conditions (D-13); an empty list is correct."""
    problems = []
    build_if = str(workflow["jobs"]["build"].get("if", ""))
    deploy_if = str(workflow["jobs"]["deploy"].get("if", ""))
    if VARIABLE_GUARD not in build_if:
        problems.append("build.if lacks the ENABLE_PAGES guard")
    if VARIABLE_GUARD not in deploy_if:
        problems.append("deploy.if lacks the ENABLE_PAGES guard")
    if MAIN_GUARD not in deploy_if:
        problems.append("deploy.if lacks the main-branch guard")
    return problems


def test_the_workflow_is_named_pages_and_has_a_build_and_a_deploy_job() -> None:
    workflow = _workflow()
    assert workflow["name"] == "pages"
    assert list(workflow["jobs"]) == ["build", "deploy"]


def test_it_runs_on_pushes_to_main_and_by_hand_and_never_on_a_pull_request() -> None:
    triggers = _triggers(_workflow())
    assert set(triggers) == {"push", "workflow_dispatch"}
    assert triggers["push"]["branches"] == ["main"]
    assert "pull_request" not in WORKFLOW.read_text()


def test_the_workflow_token_is_read_only_and_only_deploy_asks_for_more() -> None:
    workflow = _workflow()
    assert workflow["permissions"] == {"contents": "read"}
    assert "permissions" not in workflow["jobs"]["build"]
    assert workflow["jobs"]["deploy"]["permissions"] == {"pages": "write", "id-token": "write"}


def test_a_deploy_in_flight_is_never_cancelled() -> None:
    assert _workflow()["concurrency"] == {"group": "pages", "cancel-in-progress": False}


def test_both_jobs_are_skipped_until_the_owner_sets_enable_pages() -> None:
    assert guard_problems(_workflow()) == []


def test_deploy_needs_build_and_deploys_to_the_github_pages_environment() -> None:
    deploy = _workflow()["jobs"]["deploy"]
    assert deploy["needs"] == "build"
    assert deploy["environment"]["name"] == "github-pages"
    assert deploy["environment"]["url"] == "${{ steps.deployment.outputs.page_url }}"


@pytest.mark.parametrize("removed", [VARIABLE_GUARD, MAIN_GUARD])
def test_a_deploy_job_without_one_of_its_guards_is_reported(removed: str) -> None:
    doctored = copy.deepcopy(_workflow())
    deploy = doctored["jobs"]["deploy"]
    deploy["if"] = deploy["if"].replace(removed, "true")
    problems = guard_problems(doctored)
    assert len(problems) == 1
    assert problems[0].startswith("deploy.if")


def test_a_build_job_without_the_variable_guard_is_reported() -> None:
    doctored = copy.deepcopy(_workflow())
    del doctored["jobs"]["build"]["if"]
    assert guard_problems(doctored) == ["build.if lacks the ENABLE_PAGES guard"]


def test_every_action_is_pinned_to_a_full_commit_sha() -> None:
    workflow = _workflow()
    uses = [
        step["uses"] for job in workflow["jobs"] for step in _steps(workflow, job) if "uses" in step
    ]
    assert len(uses) == 6
    for value in uses:
        assert FULL_SHA.match(value), value


def test_the_six_pins_are_the_researched_commits_with_their_version_comments() -> None:
    text = WORKFLOW.read_text()
    for pin in PINS:
        assert pin in text, pin
    assert len(re.findall(r"@[0-9a-f]{40} # v", text)) == len(PINS)


def test_the_build_installs_locked_computes_the_cache_key_then_runs_demo_and_site() -> None:
    runs = _runs(_workflow(), "build")
    assert len(runs) == 4
    assert runs[0] == "uv sync --locked"
    assert "uv run --locked site --cache-key" in runs[1]
    assert runs[2] == "uv run --locked demo"
    assert runs[3] == "uv run --locked site"


def test_the_cache_key_step_writes_its_output_and_the_cache_reads_it() -> None:
    workflow = _workflow()
    key_step = [s for s in _steps(workflow, "build") if s.get("id") == "raw"]
    assert len(key_step) == 1
    assert "$GITHUB_OUTPUT" in key_step[0]["run"]
    cache = _step_using(workflow, "build", "actions/cache")
    assert cache["with"]["path"] == "data/raw"
    assert cache["with"]["key"] == "${{ steps.raw.outputs.key }}"


def test_the_cache_is_restored_before_the_demo_and_the_site_is_uploaded_last() -> None:
    steps = _steps(_workflow(), "build")
    uses_at = {s["uses"].split("@")[0]: i for i, s in enumerate(steps) if "uses" in s}
    demo_at = next(i for i, s in enumerate(steps) if s.get("run") == "uv run --locked demo")
    assert uses_at["actions/cache"] < demo_at
    assert uses_at["actions/upload-pages-artifact"] == len(steps) - 1
    upload = _step_using(_workflow(), "build", "actions/upload-pages-artifact")
    assert upload["with"]["path"] == "site"


def test_the_published_docs_never_come_from_the_fixture_or_a_local_folder() -> None:
    for job in ("build", "deploy"):
        for script in _runs(_workflow(), job):
            assert "gate" not in script
            assert "--from-dir" not in script
            assert "fixture" not in script


def test_the_site_command_in_the_workflow_is_the_makefile_site_recipe() -> None:
    recipe = makefile_recipe((REPO_ROOT / "Makefile").read_text(), "site")
    assert recipe == ["uv run site"]
    assert recipe[0].replace("uv run", "uv run --locked") in _runs(_workflow(), "build")


def test_the_deploy_steps_are_configure_deploy_then_a_smoke_test() -> None:
    steps = _steps(_workflow(), "deploy")
    assert [s.get("uses", "").split("@")[0] or s["name"] for s in steps] == [
        "actions/configure-pages",
        "actions/deploy-pages",
        "smoke test",
    ]
    assert steps[1]["id"] == "deployment"
    assert "enablement" not in steps[0].get("with", {})


def test_the_smoke_test_reads_the_url_from_env_and_fetches_the_three_pages_with_retries() -> None:
    smoke = _steps(_workflow(), "deploy")[2]
    assert smoke["env"] == {"PAGE_URL": "${{ steps.deployment.outputs.page_url }}"}
    script = smoke["run"]
    assert script.count("curl --fail") == 3
    assert script.count("--retry 5") == 3
    assert "${PAGE_URL%/}" in script
    assert "dbt-docs/index.html" in script
    assert "What we test where" in script
    assert 'href="dbt-docs/"' in script
    assert "guide/index.html" in script
    assert 'href="guide/"' in script


def test_no_run_script_holds_an_expression() -> None:
    workflow = _workflow()
    for job in workflow["jobs"]:
        for script in _runs(workflow, job):
            assert "${{" not in script, script


def test_the_workflow_has_no_secret_no_privileged_trigger_and_keeps_no_credentials() -> None:
    text = WORKFLOW.read_text()
    assert "secrets." not in text
    assert "pull_request_target" not in text
    assert "persist-credentials: false" in text


def test_the_workflow_names_no_example_dataset() -> None:
    assert not HANDLE_RE.search(WORKFLOW.read_text())


def test_the_header_describes_a_demo_build_of_every_registered_source() -> None:
    first = WORKFLOW.read_text().splitlines()[0]
    assert first.startswith("# Publishes the dbt docs to GitHub Pages")
    assert "every registered source" in first
    assert "DuckDB" in first

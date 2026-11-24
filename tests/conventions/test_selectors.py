"""`selectors.yml`: the `fabric_fixture` selector for the native job, build-fabric and CI (D-14, D-16).

The synthetic fixture breaches the real quarantine levels by design, so a fixture build leaves out
the `quarantine_rate_levels` tests. The same selector leaves out every node tagged
`fabric_known_limit` (a declared known limit, D-14) together with everything downstream of it,
because a node downstream of an excluded one cannot build without it. `dbt ls` against DuckDB needs
no database, so these tests compare what dbt lists with and without the selector.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from support.dbt_cli import REPO_ROOT, run_dbt

from starter_cli import build_fabric

SELECTORS_FILE = REPO_ROOT / "selectors.yml"
LEVELS_TEST = "quarantine_rate_levels"
TAGGED_MODEL = "bronze__nyc_taxi__yellow_trips"

COPY_IGNORE = shutil.ignore_patterns(
    ".git",
    ".venv",
    ".claude",
    ".planning",
    "target",
    "data",
    "logs",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
)


def _ls_ids(project_dir: Path, *args: str) -> set[str]:
    """The unique ids `dbt ls` lists for the project, every resource type, read from JSON output."""
    result = run_dbt(
        [
            "--quiet",
            "ls",
            "--resource-type",
            "all",
            "--output",
            "json",
            "--output-keys",
            "unique_id",
            *args,
        ],
        project_dir=project_dir,
    )
    ids = {json.loads(line)["unique_id"] for line in result.stdout.splitlines() if line[:1] == "{"}
    assert ids, f"dbt ls listed nothing for {args}"
    return ids


def _levels_tests(manifest: dict) -> set[str]:
    return {
        unique_id
        for unique_id, node in manifest["nodes"].items()
        if node["resource_type"] == "test"
        and node.get("test_metadata", {}).get("name") == LEVELS_TEST
    }


def test_selectors_file_holds_one_selector_named_fabric_fixture_and_no_default() -> None:
    document = yaml.safe_load(SELECTORS_FILE.read_text(encoding="utf-8"))
    selectors = document["selectors"]
    assert [selector["name"] for selector in selectors] == ["fabric_fixture"]
    assert "default" not in selectors[0]
    assert selectors[0]["description"].strip()


def test_the_selector_excludes_the_known_limit_tag_with_its_children() -> None:
    document = yaml.safe_load(SELECTORS_FILE.read_text(encoding="utf-8"))
    union = document["selectors"][0]["definition"]["union"]
    excludes = [item["exclude"] for item in union if isinstance(item, dict) and "exclude" in item]
    assert len(excludes) == 1
    tag_excludes = [item for item in excludes[0] if item["method"] == "tag"]
    assert len(tag_excludes) == 1
    assert tag_excludes[0]["value"] == build_fabric.KNOWN_LIMIT_TAG
    assert tag_excludes[0]["children"] is True
    assert build_fabric.KNOWN_LIMIT_EXCLUDE == f"tag:{build_fabric.KNOWN_LIMIT_TAG}+"
    assert SELECTORS_FILE.read_text(encoding="utf-8").count("children: true") == 1


def test_the_selector_leaves_out_exactly_the_levels_tests_when_no_limit_is_declared(
    duckdb_manifest: dict,
) -> None:
    everything = _ls_ids(REPO_ROOT)
    selected = _ls_ids(REPO_ROOT, "--selector", "fabric_fixture")
    levels = _levels_tests(duckdb_manifest)
    assert levels, "the project holds no quarantine_rate_levels test"
    assert everything - selected == levels
    assert selected <= everything
    assert build_fabric.declared_known_limits(REPO_ROOT) == {}


def test_a_plain_ls_lists_everything_because_the_selector_is_not_the_default() -> None:
    plain = _ls_ids(REPO_ROOT)
    assert any(unique_id.startswith("model.") for unique_id in plain)
    assert any("quarantine_rate_within_levels" in unique_id for unique_id in plain)


@pytest.fixture(scope="module")
def scratch_project(dbt_packages_ready: None, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A copy of the project with one bronze model declared a known limit."""
    destination = tmp_path_factory.mktemp("known_limit")
    shutil.copytree(REPO_ROOT, destination, symlinks=True, ignore=COPY_IGNORE, dirs_exist_ok=True)
    project_file = destination / "dbt_project.yml"
    project = yaml.safe_load(project_file.read_text(encoding="utf-8"))
    project["models"]["dbt_starter"]["bronze"]["nyc_taxi"] = {
        TAGGED_MODEL: {
            "+tags": [build_fabric.KNOWN_LIMIT_TAG],
            "+meta": {
                build_fabric.KNOWN_LIMIT_TAG: {
                    "issue": "https://github.com/example-org/example-repo/issues/1",
                    "reason": "An invented adapter limit for this test.",
                }
            },
        }
    }
    project_file.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
    return destination


def test_a_tagged_model_leaves_with_its_tests_and_every_node_downstream(
    scratch_project: Path,
) -> None:
    everything = _ls_ids(scratch_project)
    model_and_descendants = _ls_ids(scratch_project, "--select", f"{TAGGED_MODEL}+")
    # The tag and the model's own name select the same set: the model, its tests, its children.
    assert _ls_ids(scratch_project, "--select", build_fabric.KNOWN_LIMIT_EXCLUDE) == (
        model_and_descendants
    )
    assert f"model.dbt_starter.{TAGGED_MODEL}" in model_and_descendants
    assert any(unique_id.startswith("test.") for unique_id in model_and_descendants)
    assert any(
        unique_id.startswith("model.dbt_starter.silver") for unique_id in model_and_descendants
    )
    assert any(
        unique_id.startswith("model.dbt_starter.gold") for unique_id in model_and_descendants
    )

    by_exclude = everything - _ls_ids(
        scratch_project, "--exclude", build_fabric.KNOWN_LIMIT_EXCLUDE
    )
    assert by_exclude == model_and_descendants

    by_selector = everything - _ls_ids(scratch_project, "--selector", "fabric_fixture")
    # The selector also leaves out the levels tests that no declared limit already removed.
    assert model_and_descendants <= by_selector
    assert all(
        "quarantine_rate_within_levels" in unique_id for unique_id in by_selector - by_exclude
    )
    assert by_selector - by_exclude, "the other entity's levels test should also be left out"


def test_declared_known_limits_reads_the_scratch_copy(scratch_project: Path) -> None:
    limits = build_fabric.declared_known_limits(scratch_project)
    model_id = f"model.dbt_starter.{TAGGED_MODEL}"
    assert list(limits) == [model_id]
    entry = limits[model_id]
    assert entry["issue"] == "https://github.com/example-org/example-repo/issues/1"
    assert entry["resource_type"] == "model"
    expected = _ls_ids(scratch_project, "--select", f"{TAGGED_MODEL}+") - {model_id}
    assert set(entry["descendants"]) == expected
    assert entry["descendants"] == sorted(entry["descendants"])

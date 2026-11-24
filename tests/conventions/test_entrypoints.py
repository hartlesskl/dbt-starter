"""The one-command entry points (DEMO-02, D-14).

`make demo` is `uv run demo`; its recipe adds `--months` only when MONTHS is set and `--allow-large`
only when ALLOW_LARGE is 1, so a user without make pastes `uv run demo` with the same flags. The
console scripts `demo`, `load`, `gate`, `site`, `build-fabric` and `setup-fabric` are installed next to the
interpreter and run. `make check` runs the offline suite with the Fabric extra, which the compile
checks need.
"""

from __future__ import annotations

import importlib.metadata
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from support.dbt_cli import REPO_ROOT

from starter_cli import build_fabric, setup_fabric


def makefile_recipe(text: str, target: str) -> list[str]:
    """Return the tab-indented recipe lines under `<target>:`, with the leading tab removed."""
    lines = text.splitlines()
    header = re.compile(rf"^{re.escape(target)}\s*:(?!=)")
    for index, line in enumerate(lines):
        if not header.match(line):
            continue
        recipe = []
        for following in lines[index + 1 :]:
            if following.startswith("\t"):
                recipe.append(following[1:])
            elif following.strip():
                break
        return recipe
    return []


def _makefile() -> str:
    return (REPO_ROOT / "Makefile").read_text()


def test_makefile_recipe_returns_the_tab_indented_lines_without_the_tab() -> None:
    text = "demo:\n\tuv run demo\n\techo done\n\ncheck: demo\n\tuv run pytest\n"
    assert makefile_recipe(text, "demo") == ["uv run demo", "echo done"]
    assert makefile_recipe(text, "check") == ["uv run pytest"]


def test_makefile_recipe_for_a_missing_target_is_empty() -> None:
    assert makefile_recipe("demo:\n\tuv run demo\n", "docs") == []


def test_makefile_recipe_does_not_match_a_target_that_only_starts_the_same() -> None:
    assert makefile_recipe("demo-fast:\n\tuv run fast\n", "demo") == []


def test_makefile_recipe_stops_at_the_next_unindented_line() -> None:
    text = ".PHONY: demo\n\ndemo:\n\tuv run demo\nOTHER = 1\n\tnot part of it\n"
    assert makefile_recipe(text, "demo") == ["uv run demo"]


DEMO_RECIPE = (
    "uv run demo $(if $(MONTHS),--months $(MONTHS)) $(if $(filter 1,$(ALLOW_LARGE)),--allow-large)"
)


def test_the_demo_recipe_passes_months_and_allow_large_through() -> None:
    text = _makefile()
    assert makefile_recipe(text, "demo") == [DEMO_RECIPE]
    assert "MONTHS ?=" in text.splitlines()
    assert "ALLOW_LARGE ?=" in text.splitlines()


@pytest.mark.parametrize(
    ("assignments", "expected"),
    [
        ([], "uv run demo"),
        (["MONTHS=2024-01..2024-03"], "uv run demo --months 2024-01..2024-03"),
        (
            ["MONTHS=2019-01..2019-03", "ALLOW_LARGE=1"],
            "uv run demo --months 2019-01..2019-03 --allow-large",
        ),
        (["ALLOW_LARGE=0"], "uv run demo"),
    ],
)
def test_make_demo_prints_the_command_the_variables_ask_for(
    assignments: list[str], expected: str
) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is not installed")
    result = subprocess.run(
        ["make", "-n", "demo", *assignments],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert " ".join(result.stdout.split()) == expected


def test_the_check_recipe_runs_the_offline_suite_with_the_fabric_extra() -> None:
    assert makefile_recipe(_makefile(), "check") == [
        'uv run --extra fabric pytest tests -q -m "not network"'
    ]


def test_the_gate_recipe_is_exactly_the_locked_gate_command() -> None:
    assert makefile_recipe(_makefile(), "gate") == ["uv run --locked --extra fabric gate"]


def _phony_targets() -> list[str]:
    declared = [
        line.split(":", 1)[1].split()
        for line in _makefile().splitlines()
        if line.startswith(".PHONY:")
    ]
    return [name for names in declared for name in names]


def test_gate_is_a_phony_target() -> None:
    assert "gate" in _phony_targets()


def test_the_site_recipe_is_exactly_uv_run_site() -> None:
    assert makefile_recipe(_makefile(), "site") == ["uv run site"]


def test_site_is_a_phony_target() -> None:
    assert "site" in _phony_targets()


def test_the_build_fabric_recipe_is_exactly_the_locked_build_fabric_command() -> None:
    assert makefile_recipe(_makefile(), "build-fabric") == [build_fabric.BUILD_FABRIC_COMMAND]


def test_build_fabric_is_a_phony_target() -> None:
    assert "build-fabric" in _phony_targets()


def test_the_setup_fabric_recipe_is_exactly_the_locked_setup_fabric_command() -> None:
    assert makefile_recipe(_makefile(), "setup-fabric") == [setup_fabric.SETUP_FABRIC_COMMAND]
    assert setup_fabric.SETUP_FABRIC_COMMAND == "uv run --locked --extra fabric setup-fabric"


def test_setup_fabric_is_a_phony_target() -> None:
    assert "setup-fabric" in _phony_targets()


def test_the_console_scripts_are_declared_in_pyproject() -> None:
    scripts = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts["demo"] == "starter_cli.demo:main"
    assert scripts["load"] == "starter_cli.load:main"
    assert scripts["gate"] == "starter_cli.gate:main"
    assert scripts["site"] == "starter_cli.site:main"
    assert scripts["build-fabric"] == "starter_cli.build_fabric:main"
    assert scripts["setup-fabric"] == "starter_cli.setup_fabric:main"


def test_the_console_scripts_are_registered_in_the_installed_environment() -> None:
    entry_points = {
        ep.name: ep.value for ep in importlib.metadata.entry_points(group="console_scripts")
    }
    assert entry_points.get("demo") == "starter_cli.demo:main"
    assert entry_points.get("load") == "starter_cli.load:main"
    assert entry_points.get("gate") == "starter_cli.gate:main"
    assert entry_points.get("site") == "starter_cli.site:main"
    assert entry_points.get("build-fabric") == "starter_cli.build_fabric:main"
    assert entry_points.get("setup-fabric") == "starter_cli.setup_fabric:main"


@pytest.mark.parametrize("script", ["demo", "load", "gate", "site", "build-fabric", "setup-fabric"])
def test_the_console_script_is_installed_next_to_the_interpreter(script: str) -> None:
    assert shutil.which(script, path=str(Path(sys.executable).parent)) is not None


def test_load_help_exits_zero_and_names_its_arguments() -> None:
    load = shutil.which("load", path=str(Path(sys.executable).parent))
    assert load is not None
    result = subprocess.run(
        [load, "--help"], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "source" in result.stdout
    assert "entity" in result.stdout


def test_site_help_exits_zero_and_names_the_cache_key_flag() -> None:
    site = shutil.which("site", path=str(Path(sys.executable).parent))
    assert site is not None
    result = subprocess.run(
        [site, "--help"], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "--cache-key" in result.stdout


def test_build_fabric_help_exits_zero_with_no_fabric_variable_set() -> None:
    script = shutil.which("build-fabric", path=str(Path(sys.executable).parent))
    assert script is not None
    env = {key: value for key, value in os.environ.items() if not key.startswith("FABRIC_")}
    result = subprocess.run(
        [script, "--help"], cwd=REPO_ROOT, capture_output=True, text=True, check=False, env=env
    )
    assert result.returncode == 0, result.stderr
    assert "FABRIC_WORKSPACE_ID" in result.stdout


def test_setup_fabric_help_exits_zero_with_no_fabric_variable_set() -> None:
    script = shutil.which("setup-fabric", path=str(Path(sys.executable).parent))
    assert script is not None
    env = {key: value for key, value in os.environ.items() if not key.startswith("FABRIC_")}
    result = subprocess.run(
        [script, "--help"], cwd=REPO_ROOT, capture_output=True, text=True, check=False, env=env
    )
    assert result.returncode == 0, result.stderr
    assert "FABRIC_LAKEHOUSE" in result.stdout

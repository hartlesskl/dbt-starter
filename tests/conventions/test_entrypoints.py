"""The one-command entry points (DEMO-02, D-14).

`make demo` is exactly `uv run demo`, so a user without make can paste the same command. The
console scripts `demo` and `load` are installed next to the interpreter and run. `make check`
runs the offline suite with the Fabric extra, which the compile checks need.
"""

from __future__ import annotations

import importlib.metadata
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from support.dbt_cli import REPO_ROOT


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


def test_the_demo_recipe_is_exactly_uv_run_demo() -> None:
    assert makefile_recipe(_makefile(), "demo") == ["uv run demo"]


def test_the_check_recipe_runs_the_offline_suite_with_the_fabric_extra() -> None:
    assert makefile_recipe(_makefile(), "check") == [
        'uv run --extra fabric pytest tests -q -m "not network"'
    ]


def test_the_console_scripts_are_declared_in_pyproject() -> None:
    scripts = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts["demo"] == "starter_cli.demo:main"
    assert scripts["load"] == "starter_cli.load:main"


def test_the_console_scripts_are_registered_in_the_installed_environment() -> None:
    entry_points = {
        ep.name: ep.value for ep in importlib.metadata.entry_points(group="console_scripts")
    }
    assert entry_points.get("demo") == "starter_cli.demo:main"
    assert entry_points.get("load") == "starter_cli.load:main"


@pytest.mark.parametrize("script", ["demo", "load"])
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

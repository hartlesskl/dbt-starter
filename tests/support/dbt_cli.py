"""Run dbt as a subprocess for the test suites, with a separate target path per run.

dbt always runs in a subprocess: an in-process dbt run leaves DuckDB's connection cached and
a later open of the same file in this process would fail.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Lowercase dummy values: the offline compile never connects, and the scan rejects
# mixed-case identifiers outside the catalog part of a table name.
DUMMY_FABRIC_ENV: dict[str, str] = {
    "FABRIC_WORKSPACE_NAME": "wh_workspace",
    "FABRIC_DATABASE": "wh_compile",
}

FABRIC_EXTRA_HINT = (
    "The Fabric adapter (dbt-fabric) is not installed, so the offline Fabric compile cannot "
    "run. Run the suite with the extra: `uv run --extra fabric pytest`."
)


def fabric_adapter_installed() -> bool:
    """Return True when the dbt Fabric adapter can be imported."""
    return importlib.util.find_spec("dbt.adapters.fabric") is not None


def require_fabric_adapter() -> None:
    """Fail the test, never skip it, when the Fabric adapter is missing."""
    if not fabric_adapter_installed():
        pytest.fail(FABRIC_EXTRA_HINT, pytrace=False)


@dataclass(frozen=True)
class FabricCompile:
    """The result of one offline Fabric compile: its manifest and the compiled SQL folder."""

    manifest: dict
    compiled_dir: Path


def run_dbt(
    args: list[str],
    *,
    project_dir: Path = REPO_ROOT,
    env: dict[str, str] | None = None,
    profiles: bool = True,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run `dbt <args>` against `project_dir` and return the completed process.

    With `check` on, a non-zero exit raises AssertionError carrying the command, the exit
    code and the last 60 lines of combined output.
    """
    command = [sys.executable, "-m", "dbt.cli.main", *args, "--project-dir", str(project_dir)]
    if profiles:
        command += ["--profiles-dir", str(project_dir)]
    result = subprocess.run(
        command,
        cwd=project_dir,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        check=False,
    )
    if check and result.returncode != 0:
        tail = "\n".join((result.stdout + result.stderr).splitlines()[-60:])
        raise AssertionError(
            f"dbt command failed with exit code {result.returncode}: {' '.join(command)}\n{tail}"
        )
    return result

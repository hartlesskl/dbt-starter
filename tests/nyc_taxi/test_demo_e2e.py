"""End-to-end proof of the one-command demo (DEMO-01, DEMO-02). Needs internet on a cold cache."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.network

REPO_ROOT = Path(__file__).resolve().parents[2]
LAYER_LINE = re.compile(r"^(landing|bronze|silver|quarantine|gold)\s+(\S+)\s+(\d+)$", re.MULTILINE)
LAYERS = ["landing", "bronze", "silver", "quarantine", "gold"]
FABRIC_LABEL = "Fabric: compile-checked only"


def run_demo(demo: str, db_path: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "DBT_DUCKDB_PATH": str(db_path)}
    return subprocess.run(
        [demo],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def layer_counts(stdout: str) -> dict[str, int]:
    return {label: int(count) for label, _relation, count in LAYER_LINE.findall(stdout)}


def test_the_demo_command_runs_twice_with_identical_counts(tmp_path):
    demo = shutil.which("demo", path=str(Path(sys.executable).parent))
    assert demo is not None, "the `demo` console script is not installed (run uv sync)"
    db_path = tmp_path / "e2e.duckdb"

    first = run_demo(demo, db_path)
    second = run_demo(demo, db_path)

    for run in (first, second):
        assert run.returncode == 0, run.stdout + run.stderr
        labels = [label for label, _relation, _count in LAYER_LINE.findall(run.stdout)]
        assert labels == LAYERS
        non_empty = [line for line in run.stdout.splitlines() if line.strip()]
        assert non_empty[-1] == FABRIC_LABEL
        counts = layer_counts(run.stdout)
        assert counts["quarantine"] == 0
        assert counts["landing"] == counts["bronze"] == counts["silver"] == counts["gold"]
        assert counts["landing"] > 0

    assert layer_counts(second.stdout) == layer_counts(first.stdout)

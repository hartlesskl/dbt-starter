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
REPORT_LINE = re.compile(
    r"^(landing|bronze|silver|quarantine|gold|warnings)\s+(\S+)\s+(\d+)$", re.MULTILINE
)
ZONE_LABELS = ["landing", "bronze", "silver", "quarantine", "gold"]
TRIP_LABELS = ["landing", "bronze", "silver", "quarantine"]
EXPECTED_LABELS = [*ZONE_LABELS, *TRIP_LABELS, "warnings"]
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


def report(stdout: str) -> list[tuple[str, str, int]]:
    """The demo's closing table as ordered (label, relation, count) triples."""
    return [(label, relation, int(count)) for label, relation, count in REPORT_LINE.findall(stdout)]


def test_the_demo_command_runs_twice_with_identical_lines(tmp_path):
    demo = shutil.which("demo", path=str(Path(sys.executable).parent))
    assert demo is not None, "the `demo` console script is not installed (run uv sync)"
    db_path = tmp_path / "e2e.duckdb"

    first = run_demo(demo, db_path)
    second = run_demo(demo, db_path)

    for run in (first, second):
        assert run.returncode == 0, run.stdout + run.stderr
        lines = report(run.stdout)
        assert [label for label, _relation, _count in lines] == EXPECTED_LABELS
        non_empty = [line for line in run.stdout.splitlines() if line.strip()]
        assert non_empty[-1] == FABRIC_LABEL

        zones = {label: count for label, _relation, count in lines[:5]}
        assert zones["quarantine"] == 0
        assert zones["landing"] == zones["bronze"] == zones["silver"] == zones["gold"]
        assert zones["landing"] > 0

        trips = {label: count for label, _relation, count in lines[5:9]}
        assert trips["landing"] > 0
        assert trips["landing"] == trips["bronze"] == trips["silver"] + trips["quarantine"]
        assert trips["quarantine"] > 0
        warnings = lines[9][2]
        assert 0 < warnings <= trips["silver"]

    assert report(second.stdout) == report(first.stdout)

"""End-to-end proof of the one-command demo (DEMO-01 to DEMO-03). Needs internet on a cold cache."""

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
CONSERVATION_LINE = re.compile(
    r"^\s+(\S+)\s+bronze ([\d,]+) = silver ([\d,]+) \+ quarantine ([\d,]+)\s+(OK|FAIL)$",
    re.MULTILINE,
)
GOLD_LINE = re.compile(
    r"^\s+gold\s+(\S+) ([\d,]+) = sum\((\S+)\) ([\d,]+)\s+(OK|FAIL)$", re.MULTILINE
)
RATE_LINE = re.compile(r"^\s+(\S+)\s+quarantine rate [\d.]+% \(([\d,]+) of ([\d,]+) rows\)$")
CODE_LINE = re.compile(r"^ {4}(error|warn) +(\S+) +([\d,]+)$")
ZONE_LABELS = ["landing", "bronze", "silver", "quarantine", "gold"]
TRIP_LABELS = ["landing", "bronze", "silver", "quarantine"]
GOLD_LABELS = ["gold"] * 6
EXPECTED_LABELS = [*ZONE_LABELS, *TRIP_LABELS, *GOLD_LABELS, "warnings"]
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


def number(text: str) -> int:
    return int(text.replace(",", ""))


def summary_section(stdout: str, header: str) -> list[str]:
    """The lines of one closing-summary section: after its header up to the next blank line."""
    lines = stdout.splitlines()
    start = lines.index(header) + 1
    end = start
    while end < len(lines) and lines[end].strip():
        end += 1
    return lines[start:end]


def reconciliation(stdout: str) -> list[tuple[str, ...]]:
    return CONSERVATION_LINE.findall(stdout) + GOLD_LINE.findall(stdout)


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
        warnings = lines[15][2]
        assert 0 < warnings <= trips["silver"]

        gold = {relation: count for _label, relation, count in lines[9:15]}
        assert list(gold) == [
            "gold.dim_date",
            "gold.dim_vendor",
            "gold.dim_rate_code",
            "gold.dim_payment_type",
            "gold.fct_trips",
            "gold.agg_trips_daily",
        ]
        assert gold["gold.fct_trips"] == trips["silver"]
        assert gold["gold.agg_trips_daily"] > 0

        conservation = CONSERVATION_LINE.findall(run.stdout)
        assert [entity for entity, *_rest in conservation] == ["taxi_zones", "yellow_trips"]
        assert [status for *_rest, status in conservation] == ["OK", "OK"]
        counts = {
            "taxi_zones": (zones["bronze"], zones["silver"], zones["quarantine"]),
            "yellow_trips": (trips["bronze"], trips["silver"], trips["quarantine"]),
        }
        for entity, bronze, silver, quarantine, _status in conservation:
            assert (number(bronze), number(silver), number(quarantine)) == counts[entity]
        gold_lines = GOLD_LINE.findall(run.stdout)
        assert len(gold_lines) == 1
        fact, fact_rows, aggregate, aggregate_rows, status = gold_lines[0]
        assert (fact, aggregate, status) == ("fct_trips", "agg_trips_daily.trip_count", "OK")
        assert number(fact_rows) == number(aggregate_rows) == trips["silver"]

        shown: dict[str, dict[str, int]] = {}
        entity = ""
        for line in summary_section(run.stdout, "quarantine summary"):
            rate = RATE_LINE.match(line)
            if rate:
                entity = rate.group(1)
                shown[entity] = {"error": 0, "warn": 0}
            code = CODE_LINE.match(line)
            if code:
                shown[entity][code.group(1)] += 1
        assert sorted(shown) == ["taxi_zones", "yellow_trips"]
        assert all(counts["error"] <= 5 and counts["warn"] <= 3 for counts in shown.values())
        assert shown["yellow_trips"]["error"] > 0
        assert shown["yellow_trips"]["warn"] > 0

        docs = summary_section(run.stdout, "docs")
        static_path = Path(docs[0].split(maxsplit=2)[2])
        assert static_path.name == "static_index.html"
        assert static_path.is_file()
        assert "  or serve       uv run dbt docs serve" in docs
        # The Pages address is never presented as published: when the checkout has a GitHub
        # remote it is followed by the condition, and otherwise it is not printed at all.
        assert not any("published" in line for line in docs)
        for index, line in enumerate(docs):
            if line.startswith("  on Pages"):
                assert "once Pages is enabled with ENABLE_PAGES=true" in docs[index + 1]

    assert report(second.stdout) == report(first.stdout)
    assert reconciliation(second.stdout) == reconciliation(first.stdout)

"""The decision records agree on the Lakehouse retarget (D-22).

ADR 0006 records the retarget from a SQL warehouse to one schema-enabled Fabric Lakehouse run through
dbt-fabricspark. Three older records stay as history but say they are superseded in part, and two
stay `accepted` and unchanged. The check reads each record's `- **Status:**` line, the headings of
0006, and the Phase 5 live checks 0006 must name, so a record cannot drift from the others, and a
live-only item cannot be stated as fact instead of being named as a check. ADR 0007 records the
Phase 5 sink spike: exactly one record, accepted, with the headings, the deltalake pin, the two
live checks it settled and no UUID-shaped text (it is a public file holding aggregates only).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

import pytest
from support.dbt_cli import REPO_ROOT

DECISIONS_DIR = REPO_ROOT / "docs" / "decisions"
RETARGET_RECORD = "0006-fabric-lakehouse-retarget.md"
SINK_SPIKE_NAME = "0007-lakehouse-landing-sink.md"
SINK_SPIKE_RECORD = "0007"
SINK_SPIKE_CHECKS = ("sink-discovery", "sql-endpoint-read")
UUID_SHAPE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
REQUIRED_HEADINGS = ("## Context", "## Evidence", "## Decision", "## Consequences")
STATUS_PREFIX = "- **Status:**"
ACCEPTED = "accepted"
SUPERSEDED_STATUS = "accepted; superseded in part by 0006"
PHASE5_CHECKS = (
    "native-job-deps",
    "schema-layout",
    "session-time-zone",
    "undeclared-columns-live",
    "bronze-plan-cost",
    "conversion-fixture-spark",
    "contracts-live",
    "native-job-profile-schema",
)
SUPERSEDED_IN_PART = (
    "0002-starter-shims.md",
    "0004-gold-contracts.md",
    "0005-quarantine-thresholds.md",
)
UNCHANGED_RECORDS = ("0001-duckdb-pin.md", "0003-committed-profile.md")


def _status(text: str) -> str | None:
    """Return the text of the record's Status bullet, or None when it has none."""
    for line in text.splitlines():
        if line.startswith(STATUS_PREFIX):
            return line[len(STATUS_PREFIX) :].strip()
    return None


def _check_sink_spike_record(records: Mapping[str, str]) -> list[str]:
    """Return the problems of the sink-spike record, which must be exactly one file named 0007-*."""
    names = sorted(name for name in records if name.startswith(f"{SINK_SPIKE_RECORD}-"))
    if len(names) != 1:
        return [f"ADR {SINK_SPIKE_RECORD}: expected exactly one record, found {len(names)}"]
    name = names[0]
    text = records[name]
    problems: list[str] = []
    if _status(text) != ACCEPTED:
        problems.append(f"{name}: the Status line must be exactly `{ACCEPTED}`")
    problems += [
        f"{name}: the heading `{heading}` is missing"
        for heading in REQUIRED_HEADINGS
        if not any(line.strip() == heading for line in text.splitlines())
    ]
    if "deltalake==" not in text:
        problems.append(f"{name}: the spiked deltalake pin (`deltalake==`) is not named")
    problems += [
        f"{name}: the check `{check}` is not named"
        for check in SINK_SPIKE_CHECKS
        if check not in text
    ]
    if UUID_SHAPE.search(text):
        problems.append(f"{name}: it holds UUID-shaped text, and a record holds aggregates only")
    return problems


def check_decision_records(records: Mapping[str, str]) -> list[str]:
    """Return one problem per way the records disagree about the retarget."""
    problems: list[str] = []
    retarget = records.get(RETARGET_RECORD)
    if retarget is None:
        problems.append(f"{RETARGET_RECORD}: the retarget record is missing")
    else:
        if _status(retarget) != ACCEPTED:
            problems.append(f"{RETARGET_RECORD}: the Status line must be exactly `{ACCEPTED}`")
        problems += [
            f"{RETARGET_RECORD}: the heading `{heading}` is missing"
            for heading in REQUIRED_HEADINGS
            if not any(line.strip() == heading for line in retarget.splitlines())
        ]
        problems += [
            f"{RETARGET_RECORD}: the Phase 5 check `{check}` is not named"
            for check in PHASE5_CHECKS
            if check not in retarget
        ]
        if SINK_SPIKE_RECORD not in retarget:
            problems.append(
                f"{RETARGET_RECORD}: it must name ADR {SINK_SPIKE_RECORD} as the Phase 5 "
                "sink-spike record"
            )
    problems += _check_sink_spike_record(records)
    for name in SUPERSEDED_IN_PART:
        status = _status(records.get(name, ""))
        if status is None or "superseded in part by 0006" not in status:
            problems.append(f"{name}: the Status line must say `{SUPERSEDED_STATUS}`")
    for name in UNCHANGED_RECORDS:
        if _status(records.get(name, "")) != ACCEPTED:
            problems.append(f"{name}: the Status line must stay exactly `{ACCEPTED}`")
    return problems


def _real_records() -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8") for path in DECISIONS_DIR.glob("*.md")}


def _valid_records() -> dict[str, str]:
    """A small tree that passes, built so each doctored test changes exactly one thing."""
    body = "\n".join(
        [
            "# 0006. A retarget",
            f"{STATUS_PREFIX} {ACCEPTED}",
            *REQUIRED_HEADINGS,
            *PHASE5_CHECKS,
            f"The sink spike is ADR {SINK_SPIKE_RECORD}.",
        ]
    )
    sink_body = "\n".join(
        [
            "# 0007. A sink",
            f"{STATUS_PREFIX} {ACCEPTED}",
            *REQUIRED_HEADINGS,
            "deltalake==1.0.0",
            *SINK_SPIKE_CHECKS,
        ]
    )
    records = {RETARGET_RECORD: body, SINK_SPIKE_NAME: sink_body}
    records |= {name: f"# x\n{STATUS_PREFIX} {SUPERSEDED_STATUS}\n" for name in SUPERSEDED_IN_PART}
    records |= {name: f"# x\n{STATUS_PREFIX} {ACCEPTED}\n" for name in UNCHANGED_RECORDS}
    return records


def test_the_real_decision_records_agree_on_the_retarget() -> None:
    records = _real_records()
    assert len(records) >= 5, "the decisions folder holds fewer records than expected"
    assert check_decision_records(records) == []


def test_the_valid_fixture_tree_passes() -> None:
    assert check_decision_records(_valid_records()) == []


def test_a_missing_retarget_record_is_reported() -> None:
    records = _valid_records()
    del records[RETARGET_RECORD]
    problems = check_decision_records(records)
    assert problems == [f"{RETARGET_RECORD}: the retarget record is missing"]


@pytest.mark.parametrize("status", ["proposed", "accepted; superseded in part by 0007", ""])
def test_a_retarget_record_whose_status_is_not_accepted_is_reported(status: str) -> None:
    records = _valid_records()
    records[RETARGET_RECORD] = records[RETARGET_RECORD].replace(
        f"{STATUS_PREFIX} {ACCEPTED}", f"{STATUS_PREFIX} {status}"
    )
    problems = check_decision_records(records)
    assert any("Status line must be exactly" in problem for problem in problems)


@pytest.mark.parametrize("heading", REQUIRED_HEADINGS)
def test_a_retarget_record_missing_a_heading_is_reported_by_heading(heading: str) -> None:
    records = _valid_records()
    records[RETARGET_RECORD] = records[RETARGET_RECORD].replace(heading, "## Renamed")
    problems = check_decision_records(records)
    assert [p for p in problems if heading in p], problems


@pytest.mark.parametrize("check", PHASE5_CHECKS)
def test_a_retarget_record_that_leaves_out_a_phase5_check_is_reported(check: str) -> None:
    records = _valid_records()
    records[RETARGET_RECORD] = records[RETARGET_RECORD].replace(check, "removed")
    problems = check_decision_records(records)
    assert [p for p in problems if f"`{check}`" in p], problems


def test_a_retarget_record_that_leaves_out_the_sink_spike_record_is_reported() -> None:
    records = _valid_records()
    records[RETARGET_RECORD] = records[RETARGET_RECORD].replace(SINK_SPIKE_RECORD, "none")
    problems = check_decision_records(records)
    assert [p for p in problems if "sink-spike" in p], problems


def test_a_missing_sink_spike_record_is_reported() -> None:
    records = _valid_records()
    del records[SINK_SPIKE_NAME]
    problems = check_decision_records(records)
    assert problems == [f"ADR {SINK_SPIKE_RECORD}: expected exactly one record, found 0"]


def test_two_sink_spike_records_are_reported() -> None:
    records = _valid_records()
    records["0007-another.md"] = records[SINK_SPIKE_NAME]
    problems = check_decision_records(records)
    assert problems == [f"ADR {SINK_SPIKE_RECORD}: expected exactly one record, found 2"]


@pytest.mark.parametrize("check", SINK_SPIKE_CHECKS)
def test_a_sink_spike_record_that_leaves_out_a_check_name_is_reported(check: str) -> None:
    records = _valid_records()
    records[SINK_SPIKE_NAME] = records[SINK_SPIKE_NAME].replace(check, "removed")
    problems = check_decision_records(records)
    assert [p for p in problems if f"`{check}`" in p], problems


def test_a_sink_spike_record_whose_status_is_not_accepted_is_reported() -> None:
    records = _valid_records()
    records[SINK_SPIKE_NAME] = records[SINK_SPIKE_NAME].replace(ACCEPTED, "proposed")
    problems = check_decision_records(records)
    assert any(p.startswith(SINK_SPIKE_NAME) and "Status line" in p for p in problems), problems


@pytest.mark.parametrize("heading", REQUIRED_HEADINGS)
def test_a_sink_spike_record_missing_a_heading_is_reported(heading: str) -> None:
    records = _valid_records()
    records[SINK_SPIKE_NAME] = records[SINK_SPIKE_NAME].replace(heading, "## Renamed")
    problems = check_decision_records(records)
    assert [p for p in problems if p.startswith(SINK_SPIKE_NAME) and heading in p], problems


def test_a_sink_spike_record_without_the_deltalake_pin_is_reported() -> None:
    records = _valid_records()
    records[SINK_SPIKE_NAME] = records[SINK_SPIKE_NAME].replace("deltalake==", "deltalake ")
    problems = check_decision_records(records)
    assert [p for p in problems if "deltalake==" in p], problems


def test_a_sink_spike_record_with_a_uuid_in_the_text_is_reported() -> None:
    records = _valid_records()
    records[SINK_SPIKE_NAME] += "\nworkspace 0a1b2c3d-4e5f-6789-abcd-ef0123456789\n"
    problems = check_decision_records(records)
    assert [p for p in problems if "UUID-shaped" in p], problems


@pytest.mark.parametrize("name", SUPERSEDED_IN_PART)
def test_a_record_that_lacks_the_superseded_status_is_reported_by_file(name: str) -> None:
    records = _valid_records()
    records[name] = f"# x\n{STATUS_PREFIX} {ACCEPTED}\n"
    problems = check_decision_records(records)
    assert [p for p in problems if p.startswith(name)], problems


@pytest.mark.parametrize("name", UNCHANGED_RECORDS)
def test_an_unchanged_record_whose_status_moved_is_reported_by_file(name: str) -> None:
    records = _valid_records()
    records[name] = f"# x\n{STATUS_PREFIX} {SUPERSEDED_STATUS}\n"
    problems = check_decision_records(records)
    assert [p for p in problems if p.startswith(name)], problems


def test_a_record_with_no_status_line_is_reported() -> None:
    records = _valid_records()
    records[UNCHANGED_RECORDS[0]] = "# x\nno status here\n"
    assert [p for p in check_decision_records(records) if p.startswith(UNCHANGED_RECORDS[0])]


def test_the_decisions_folder_is_where_the_test_looks() -> None:
    assert Path(DECISIONS_DIR).is_dir()

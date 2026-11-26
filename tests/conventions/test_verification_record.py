"""The published Fabric verification record agrees with the README and the demo's label (D-12, D-14, D-15).

The record is a generated file, docs/verification/fabric-YYYY-MM-DD.md. Two states are checked over
`newest_record()`:

- No record: the README Status line does not claim a verification, and the demo's closing label
  says Fabric is not verified live yet.
- A record: the README Status line starts with `Verified on Fabric <date>`, links the file and names
  the versions the record names; the demo's label holds the same date and path; every live check in
  the record is one the writer's own rule (`fabric_record.publishable`) accepts given the record's
  Known limits section; every build in the Builds table shows no failed or errored test; and the
  record holds no UUID-shaped text (it is a public file holding aggregates only).

The checks are plain functions over text, so each doctored case below changes exactly one thing.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest
from support.dbt_cli import REPO_ROOT

from starter_cli import demo, fabric_record

VERIFICATION_DIR = REPO_ROOT / "docs" / "verification"
README = REPO_ROOT / "README.md"
RECORD_NAME_RE = re.compile(r"^fabric-(\d{4}-\d{2}-\d{2})\.md$")
VERIFIED_PREFIX = "Verified on Fabric"
UUID_SHAPE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
# What the record's Builds table must hold a row for, in the order the writer lists them.
BUILD_ROWS = ("build-1", "build-2", "build-3", "build-4", "self-hosted")
KNOWN_LIMIT_RE = re.compile(
    r"^- `(?P<node>[^`]+)` \([^)]*\): issue (?P<issue>\S+?)\. Reason: (?P<reason>.+?)\. "
    r"Exclusion approved by the author on .+\.$"
)
EXCLUDED_WITH_PREFIX = "  - Excluded with it:"


def newest_record(folder: Path = VERIFICATION_DIR) -> Path | None:
    """The latest docs/verification/fabric-YYYY-MM-DD.md by date, or None when there is none."""
    if not folder.is_dir():
        return None
    dated = sorted(
        (found.group(1), path)
        for path in folder.iterdir()
        if (found := RECORD_NAME_RE.match(path.name))
    )
    return dated[-1][1] if dated else None


def record_date(path: Path) -> str:
    found = RECORD_NAME_RE.match(path.name)
    assert found, path.name
    return found.group(1)


def readme_label(text: str | None = None) -> str:
    """The README Status line: the first non-empty line under the `## Status` heading."""
    lines = (README.read_text(encoding="utf-8") if text is None else text).splitlines()
    start = next(index for index, line in enumerate(lines) if line.strip() == "## Status")
    return next(line.strip() for line in lines[start + 1 :] if line.strip())


def _section(text: str, heading: str) -> list[str]:
    """The lines of one `## heading` section, up to the next heading."""
    lines = text.splitlines()
    start = lines.index(f"## {heading}") + 1
    end = start
    while end < len(lines) and not lines[end].startswith("## "):
        end += 1
    return lines[start:end]


def _table(lines: list[str]) -> list[dict[str, str]]:
    """The rows of a Markdown table as dicts keyed by the header cells."""
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in lines
        if line.startswith("|")
    ]
    if len(rows) < 2:
        return []
    header, body = rows[0], rows[2:]
    return [dict(zip(header, row, strict=True)) for row in body if len(row) == len(header)]


def _nodes(cell: str) -> list[str]:
    return [] if cell == "none" else re.findall(r"`([^`]+)`", cell)


def _versions(record: str) -> tuple[str | None, str | None]:
    core = re.search(r"^- dbt Core \(the native job's patch\): (\S+)$", record, re.MULTILINE)
    adapter = re.search(r"^- dbt-fabricspark: (\S+)$", record, re.MULTILINE)
    return (core.group(1) if core else None, adapter.group(1) if adapter else None)


def _known_limits(record: str) -> dict[str, dict[str, object]]:
    """The declared nodes of the Known limits section, as the writer's `publishable` reads them."""
    limits: dict[str, dict[str, object]] = {}
    current: str | None = None
    for line in _section(record, "Known limits"):
        found = KNOWN_LIMIT_RE.match(line)
        if found:
            current = found.group("node")
            limits[current] = {
                "issue": found.group("issue"),
                "reason": found.group("reason"),
                "descendants": [],
            }
        elif line.startswith(EXCLUDED_WITH_PREFIX) and current is not None:
            limits[current]["descendants"] = _nodes(line[len(EXCLUDED_WITH_PREFIX) :].strip())
    return limits


def check_unverified(readme_text: str, label: str) -> list[str]:
    """The problems of the no-record state."""
    problems: list[str] = []
    if readme_label(readme_text).startswith(VERIFIED_PREFIX):
        problems.append("the README Status line claims a verification, but no record exists")
    if label != demo.FABRIC_LABEL_UNVERIFIED:
        problems.append("the demo's Fabric label is not the unverified label, but no record exists")
    return problems


def check_record(record: str, date: str, readme_text: str, label: str) -> list[str]:
    """The problems of the record-published state, for a record dated `date`."""
    path = f"docs/verification/fabric-{date}.md"
    problems: list[str] = []
    status = readme_label(readme_text)
    if not status.startswith(f"{VERIFIED_PREFIX} {date}"):
        problems.append(f"the README Status line must start with `{VERIFIED_PREFIX} {date}`")
    if path not in status:
        problems.append(f"the README Status line must link {path}")
    core, adapter = _versions(record)
    for name, version in (("dbt-core", core), ("dbt-fabricspark", adapter)):
        if version is None:
            problems.append(f"the record names no {name} version")
        elif version not in status:
            problems.append(f"the README Status line must name {name} {version}")
    if date not in label or path not in label:
        problems.append(f"the demo's Fabric label must hold the date {date} and the path {path}")

    known_limits = _known_limits(record)
    checks = _table(_section(record, "Live checks"))
    listed = {row["Check"] for row in checks}
    problems += [
        f"the Live checks table lacks `{name}`"
        for name in fabric_record.LIVE_CHECKS
        if name not in listed
    ]
    for row in checks:
        name, outcome = row["Check"], row["Outcome"]
        if outcome not in fabric_record.CHECK_OUTCOMES:
            problems.append(f"check `{name}`: the outcome `{outcome}` is not a known outcome")
            continue
        entry = {"outcome": outcome, "excluded": _nodes(row["Excluded nodes"])}
        if not fabric_record.publishable(name, entry, known_limits):
            problems.append(f"check `{name}`: the outcome `{outcome}` cannot be published")

    builds = {row["Run"]: row for row in _table(_section(record, "Builds"))}
    for run in BUILD_ROWS:
        if run not in builds:
            problems.append(f"the Builds table has no `{run}` row")
        elif builds[run].get("Tests fail") != "0":
            problems.append(f"the Builds table shows failed or errored tests for `{run}`")
    if UUID_SHAPE.search(record):
        problems.append("the record holds UUID-shaped text, and a record holds aggregates only")
    return problems


# ---------------------------------------------------------------------------------------------
# The repository as it is


def test_the_repository_state_agrees_with_its_newest_record() -> None:
    newest = newest_record()
    readme = README.read_text(encoding="utf-8")
    if newest is None:
        assert check_unverified(readme, demo.FABRIC_LABEL) == []
        return
    problems = check_record(
        newest.read_text(encoding="utf-8"), record_date(newest), readme, demo.FABRIC_LABEL
    )
    assert problems == []


def test_the_label_is_computed_from_the_newest_record() -> None:
    assert demo.FABRIC_LABEL == demo.fabric_label(REPO_ROOT)
    newest = newest_record()
    if newest is None:
        assert demo.FABRIC_LABEL == demo.FABRIC_LABEL_UNVERIFIED
    else:
        date = record_date(newest)
        assert demo.FABRIC_LABEL == f"Fabric: verified on {date} (docs/verification/{newest.name})"


def test_this_file_never_holds_the_old_status_label() -> None:
    old = "compile" + "-checked only"
    assert old not in Path(__file__).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------------------------
# The label and the newest-record rule, on temporary folders


def test_with_no_record_the_label_is_the_unverified_one(tmp_path: Path) -> None:
    assert demo.fabric_label(tmp_path) == demo.FABRIC_LABEL_UNVERIFIED
    (tmp_path / "docs" / "verification").mkdir(parents=True)
    assert demo.fabric_label(tmp_path) == demo.FABRIC_LABEL_UNVERIFIED


def test_the_newest_record_by_date_gives_the_label(tmp_path: Path) -> None:
    folder = tmp_path / "docs" / "verification"
    folder.mkdir(parents=True)
    for name in (
        "fabric-2026-12-01.md",
        "fabric-2027-01-09.md",
        "fabric-2026-12-30.md",
        "notes.md",
    ):
        (folder / name).write_text("x", encoding="utf-8")
    assert newest_record(folder) == folder / "fabric-2027-01-09.md"
    assert demo.fabric_label(tmp_path) == (
        "Fabric: verified on 2027-01-09 (docs/verification/fabric-2027-01-09.md)"
    )


def test_a_file_that_is_not_a_dated_record_is_not_a_record(tmp_path: Path) -> None:
    folder = tmp_path / "docs" / "verification"
    folder.mkdir(parents=True)
    (folder / "fabric-latest.md").write_text("x", encoding="utf-8")
    assert newest_record(folder) is None
    assert demo.fabric_label(tmp_path) == demo.FABRIC_LABEL_UNVERIFIED


# ---------------------------------------------------------------------------------------------
# The record-published state, on a built record, with one thing doctored per test

DATE = "2026-12-01"
PATH = f"docs/verification/fabric-{DATE}.md"
ISSUE = "https://github.com/example-org/example-repo/issues/42"
LABEL = f"Fabric: verified on {DATE} ({PATH})"
STATUS = (
    f"{VERIFIED_PREFIX} {DATE}: dbt Core 1.11.8 and dbt-fabricspark 1.12.2 on Fabric Runtime 2.0, "
    f"see [the record]({PATH})."
)
README_TEXT = f"# x\n\n## Status\n\n{STATUS}\n\n## Quickstart\n"
CHECK_ROWS = {name: ("pass", "none") for name in fabric_record.LIVE_CHECKS}
CHECK_ROWS["job-rest-run"] = ("documented-fallback", "none")


def build_record(
    *,
    checks: dict[str, tuple[str, str]] | None = None,
    limits: list[str] | None = None,
    fail_in: str | None = None,
    extra: str = "",
) -> str:
    rows = checks if checks is not None else CHECK_ROWS
    check_lines = [
        f"| {name} | {outcome} | evidence.json:key | {excluded} |"
        for name, (outcome, excluded) in sorted(rows.items())
    ]
    build_lines = [
        f"| {run} | off | Completed | hook | 16 | 288 | 1 | {1 if run == fail_in else 0} | 6 | 11 | 9.0 |"
        for run in BUILD_ROWS
    ]
    return "\n".join(
        [
            f"# Fabric verification, {DATE}",
            "",
            "## Versions",
            "",
            "- dbt Core (the native job's patch): 1.11.8",
            "- dbt-fabricspark: 1.12.2",
            "",
            "## Builds",
            "",
            (
                "| Run | Full refresh | Job status | Results source | Models | Tests pass "
                "| Tests warn | Tests fail | Seeds | Unit tests | Duration (s) |"
            ),
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            *build_lines,
            "",
            "## Live checks",
            "",
            "| Check | Outcome | Evidence | Excluded nodes |",
            "| --- | --- | --- | --- |",
            *check_lines,
            "",
            "## Known limits",
            "",
            *(limits if limits is not None else ["none"]),
            "",
            extra,
        ]
    )


LIMIT_LINES = [
    (
        f"- `seed.proj.broken` (seed): issue {ISSUE}. Reason: an upstream bug. "
        "Exclusion approved by the author on 2026-12-01."
    ),
    "  - Excluded with it: `model.proj.child`",
]


def problems_of(record: str, *, readme: str = README_TEXT, label: str = LABEL) -> list[str]:
    return check_record(record, DATE, readme, label)


def test_a_valid_record_passes() -> None:
    assert problems_of(build_record()) == []


def test_an_excluded_node_check_listed_under_known_limits_passes() -> None:
    checks = {**CHECK_ROWS, "seed-load": ("excluded", "`seed.proj.broken`")}
    assert problems_of(build_record(checks=checks, limits=LIMIT_LINES)) == []


def test_an_excluded_descendant_listed_under_known_limits_passes() -> None:
    checks = {**CHECK_ROWS, "unit-tests-live": ("excluded", "`model.proj.child`")}
    assert problems_of(build_record(checks=checks, limits=LIMIT_LINES)) == []


def test_a_readme_dated_differently_is_reported() -> None:
    readme = README_TEXT.replace(f"{VERIFIED_PREFIX} {DATE}", f"{VERIFIED_PREFIX} 2026-11-30")
    problems = problems_of(build_record(), readme=readme)
    assert any("must start with" in problem for problem in problems), problems


def test_a_readme_that_does_not_link_the_record_is_reported() -> None:
    readme = README_TEXT.replace(PATH, "docs/verification/other.md")
    problems = problems_of(build_record(), readme=readme)
    assert any("must link" in problem for problem in problems), problems


def test_a_readme_that_names_another_version_is_reported() -> None:
    readme = README_TEXT.replace("1.12.2", "1.13.0")
    problems = problems_of(build_record(), readme=readme)
    assert any("dbt-fabricspark 1.12.2" in problem for problem in problems), problems


def test_a_label_naming_another_file_is_reported() -> None:
    label = "Fabric: verified on 2026-11-30 (docs/verification/fabric-2026-11-30.md)"
    problems = problems_of(build_record(), label=label)
    assert any("demo's Fabric label" in problem for problem in problems), problems


def test_the_unverified_label_with_a_record_is_reported() -> None:
    problems = problems_of(build_record(), label=demo.FABRIC_LABEL_UNVERIFIED)
    assert any("demo's Fabric label" in problem for problem in problems), problems


def test_a_record_whose_builds_table_shows_one_failed_test_is_reported() -> None:
    problems = problems_of(build_record(fail_in="build-3"))
    assert problems == ["the Builds table shows failed or errored tests for `build-3`"]


def test_a_record_with_a_failed_check_is_reported() -> None:
    checks = {**CHECK_ROWS, "schema-layout": ("fail", "none")}
    problems = problems_of(build_record(checks=checks))
    assert problems == ["check `schema-layout`: the outcome `fail` cannot be published"]


def test_a_missing_outcome_is_reported() -> None:
    checks = {**CHECK_ROWS, "oidc-live": ("missing", "none")}
    problems = problems_of(build_record(checks=checks))
    assert problems == ["check `oidc-live`: the outcome `missing` cannot be published"]


def test_a_fallback_outside_the_job_rest_run_is_reported() -> None:
    checks = {**CHECK_ROWS, "sink-discovery": ("documented-fallback", "none")}
    problems = problems_of(build_record(checks=checks))
    assert problems == [
        "check `sink-discovery`: the outcome `documented-fallback` cannot be published"
    ]


def test_an_excluded_check_whose_node_is_missing_from_known_limits_is_reported() -> None:
    checks = {**CHECK_ROWS, "seed-load": ("excluded", "`seed.proj.unlisted`")}
    problems = problems_of(build_record(checks=checks, limits=LIMIT_LINES))
    assert problems == ["check `seed-load`: the outcome `excluded` cannot be published"]


def test_an_excluded_check_with_no_node_is_reported() -> None:
    checks = {**CHECK_ROWS, "seed-load": ("excluded", "none")}
    problems = problems_of(build_record(checks=checks))
    assert problems == ["check `seed-load`: the outcome `excluded` cannot be published"]


def test_a_known_limit_without_an_issue_link_does_not_publish_an_exclusion() -> None:
    limits = [LIMIT_LINES[0].replace(ISSUE, "https://example.com/nope"), LIMIT_LINES[1]]
    checks = {**CHECK_ROWS, "seed-load": ("excluded", "`seed.proj.broken`")}
    problems = problems_of(build_record(checks=checks, limits=limits))
    assert problems == ["check `seed-load`: the outcome `excluded` cannot be published"]


def test_an_exclusion_on_a_check_that_cannot_be_excluded_is_reported() -> None:
    checks = {**CHECK_ROWS, "schema-layout": ("excluded", "`seed.proj.broken`")}
    problems = problems_of(build_record(checks=checks, limits=LIMIT_LINES))
    assert problems == ["check `schema-layout`: the outcome `excluded` cannot be published"]


def test_a_record_that_leaves_out_a_check_is_reported() -> None:
    checks = {name: row for name, row in CHECK_ROWS.items() if name != "contracts-live"}
    problems = problems_of(build_record(checks=checks))
    assert problems == ["the Live checks table lacks `contracts-live`"]


def test_a_record_with_an_unknown_outcome_is_reported() -> None:
    checks = {**CHECK_ROWS, "seed-load": ("pending", "none")}
    problems = problems_of(build_record(checks=checks))
    assert any("not a known outcome" in problem for problem in problems), problems


def test_a_record_with_a_uuid_is_reported() -> None:
    problems = problems_of(build_record(extra="workspace 0a1b2c3d-4e5f-6789-abcd-ef0123456789"))
    assert problems == ["the record holds UUID-shaped text, and a record holds aggregates only"]


def test_a_record_that_leaves_out_a_build_row_is_reported() -> None:
    record = build_record()
    record = "\n".join(line for line in record.splitlines() if not line.startswith("| self-hosted"))
    assert problems_of(record) == ["the Builds table has no `self-hosted` row"]


# ---------------------------------------------------------------------------------------------
# The no-record state, doctored


def test_the_no_record_state_passes_with_the_unverified_label() -> None:
    readme = "# x\n\n## Status\n\nFabric: not verified live yet\n"
    assert check_unverified(readme, demo.FABRIC_LABEL_UNVERIFIED) == []


def test_a_verified_readme_without_a_record_is_reported() -> None:
    problems = check_unverified(README_TEXT, demo.FABRIC_LABEL_UNVERIFIED)
    assert problems == ["the README Status line claims a verification, but no record exists"]


def test_a_dated_label_without_a_record_is_reported() -> None:
    readme = "# x\n\n## Status\n\nFabric: not verified live yet\n"
    problems = check_unverified(readme, LABEL)
    assert problems == ["the demo's Fabric label is not the unverified label, but no record exists"]


@pytest.mark.parametrize("line", ["", "   "])
def test_readme_label_skips_blank_lines(line: str) -> None:
    assert readme_label(f"## Status\n{line}\nFabric: x\n") == "Fabric: x"


# ---------------------------------------------------------------------------------------------
# The published record is never edited (CI-05)

# example:begin
PUBLISHED_RECORD = VERIFICATION_DIR / "fabric-2026-11-25.md"
# The sha256 of the bytes of the record as published. A later verification adds a new dated file.
PUBLISHED_RECORD_SHA256 = "4cc988450747a44bb785c3496e17e88389240a714fea54a04cca8173f313f616"


def test_the_published_record_is_never_edited() -> None:
    if not PUBLISHED_RECORD.is_file():
        pytest.skip("the published record is not in this copy of the template")
    digest = hashlib.sha256(PUBLISHED_RECORD.read_bytes()).hexdigest()
    assert digest == PUBLISHED_RECORD_SHA256, (
        "docs/verification/fabric-2026-11-25.md differs from the record as published; "
        "a later verification adds a new dated file instead"
    )


# example:end

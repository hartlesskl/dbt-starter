"""The README's Troubleshooting section explains the known first-run failures (DEMO-07, D-20).

An adopter who hits a failure looks it up by its message. Each `###` entry under
`## Troubleshooting` has to hold the real error text and the fix, so the test pins, per entry, the
strings it must contain. It also holds three more rules:

- the section tells no history of the dropped macOS TLS-library workaround, so it holds no
  mention of that library;
- the quoted error text uses placeholders (`<path>`, `<pid>`, `<user>`), never a real PID, user
  name or home folder (T-06-35), because the repository is public;
- every entry in the table is present exactly once.

`troubleshooting_problems` is a pure function over the README text and returns one line per
problem, so each rule has a doctored case that must be reported.
"""

from __future__ import annotations

import re

from support.dbt_cli import REPO_ROOT

SECTION_HEADING = "## Troubleshooting"
# The strings each entry must hold, keyed by its `###` heading.
TROUBLESHOOTING_ENTRIES: dict[str, tuple[str, ...]] = {
    "### Download blocked or unavailable (HTTP 403 or 404)": ("--from-dir", "cleanroom"),
    "### The DuckDB file is locked": (
        "Could not set lock on file",
        "dbt docs serve",
        "DBT_DUCKDB_PATH",
    ),
    "### Wrong Python version": (
        "is incompatible with the project's Python requirement",
        "3.12",
        "3.14",
        "uv sync",
    ),
    "### Fabric: az login missing or expired": (
        "could not get a token from az login",
        "az login",
        "docs/fabric-setup.md",
    ),
    "### Fabric: capacity busy (HTTP 430)": (
        "430",
        "TooManyRequestsForCapacity",
        "docs/fabric-setup.md",
    ),
    "### Command run outside the repo root": ("dbt_project.yml",),
}
# The library name is built from parts so this file does not hold the word itself.
FORBIDDEN_WORD = "open" + "ssl"
# Quoted error text must not hold a real process id, a user's home folder or a Windows user path.
PRIVATE_TEXT = (
    (re.compile(r"\(PID\s+\d+\)"), "a real process id (write `(PID <pid>)`)"),
    (re.compile(r"/Users/"), "a macOS home folder (write `<path>`)"),
    (re.compile(r"/home/(?!<)"), "a Linux home folder (write `<path>`)"),
    (re.compile(r"C:\\Users\\", re.IGNORECASE), "a Windows user folder (write `<path>`)"),
)


def troubleshooting_section(text: str) -> str:
    """Return the text of the Troubleshooting section, or an empty string when it is missing."""
    lines = text.splitlines()
    if SECTION_HEADING not in lines:
        return ""
    start = lines.index(SECTION_HEADING) + 1
    end = next(
        (i for i in range(start, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    return "\n".join(lines[start:end])


def _entries(section: str) -> dict[str, str]:
    """Split a section into its `###` entries, keyed by heading line."""
    entries: dict[str, list[str]] = {}
    current: str | None = None
    for line in section.splitlines():
        if line.startswith("### "):
            current = line
            entries.setdefault(current, [])
        elif current is not None:
            entries[current].append(line)
    return {heading: "\n".join(body) for heading, body in entries.items()}


def troubleshooting_problems(text: str) -> list[str]:
    """Return every way the README's Troubleshooting section breaks the contract."""
    section = troubleshooting_section(text)
    if not section:
        return [f"README.md has no `{SECTION_HEADING}` section"]
    problems: list[str] = []
    headings = [line for line in section.splitlines() if line.startswith("### ")]
    entries = _entries(section)
    for heading, required in TROUBLESHOOTING_ENTRIES.items():
        count = headings.count(heading)
        if count != 1:
            problems.append(f"{heading}: the entry is present {count} times, expected once")
            continue
        for needle in required:
            if needle not in entries[heading]:
                problems.append(f"{heading}: the entry does not hold `{needle}`")
    if FORBIDDEN_WORD in section.lower():
        problems.append("the Troubleshooting section names the dropped TLS-library workaround")
    for number, line in enumerate(section.splitlines(), start=1):
        for pattern, what in PRIVATE_TEXT:
            if pattern.search(line):
                problems.append(f"Troubleshooting line {number} holds {what}")
    return sorted(problems)


def _readme() -> str:
    return (REPO_ROOT / "README.md").read_text(encoding="utf-8")


def test_the_readme_troubleshooting_holds_every_known_entry() -> None:
    assert troubleshooting_problems(_readme()) == []


def _doctored(old: str, new: str) -> str:
    text = _readme()
    assert old in text, f"the doctored case needs `{old}` in the README"
    return text.replace(old, new, 1)


def test_a_readme_without_the_lock_entry_is_reported_by_name() -> None:
    text = _doctored("### The DuckDB file is locked", "### Something else")
    problems = troubleshooting_problems(text)
    assert any(p.startswith("### The DuckDB file is locked") for p in problems)


def test_a_430_entry_without_its_error_name_is_reported() -> None:
    text = _readme().replace("TooManyRequestsForCapacity", "TooMany")
    problems = troubleshooting_problems(text)
    assert any(
        p.startswith("### Fabric: capacity busy (HTTP 430)") and "TooManyRequestsForCapacity" in p
        for p in problems
    )


def test_a_repeated_entry_is_reported() -> None:
    text = _readme() + "\n### Wrong Python version\n\nAgain.\n"
    problems = troubleshooting_problems(text)
    assert any("Wrong Python version" in p and "2 times" in p for p in problems)


def test_a_missing_section_is_reported() -> None:
    assert troubleshooting_problems("# README\n\nNothing here.\n") != []


def test_a_tls_library_entry_is_reported() -> None:
    text = _readme() + f"\n### The {FORBIDDEN_WORD.capitalize()} library\n\nInstall it.\n"
    problems = troubleshooting_problems(text)
    assert any("TLS-library" in p for p in problems)


def test_a_real_pid_user_or_home_folder_is_reported() -> None:
    for private in ("(PID 4242)", "/Users/someone/work", "C:\\Users\\someone\\work"):
        text = _readme() + f"\nError: held {private}\n"
        problems = troubleshooting_problems(text)
        assert any("Troubleshooting line" in p for p in problems), private


def test_placeholders_are_not_reported() -> None:
    text = _readme() + "\nError: `<path>` held by `(PID <pid>)` by user `<user>`\n"
    assert troubleshooting_problems(text) == troubleshooting_problems(_readme())

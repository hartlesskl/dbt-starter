"""The example-dataset removal handle (ADPT-02).

Everything specific to the example dataset must be removable by deleting `nyc_taxi`
directories and the text between `example:begin` and `example:end` markers. So every tracked
file whose path or text matches `taxi`, `tlc` or `nyc` (any case) must sit under a path
component named exactly `nyc_taxi`, or match only between the markers. The framework package
`packages/starter/` must contain no match at all, markers or not, so a remove-example run can
never strip framework code (D-10).

The check reads `git ls-files`, so an untracked new file is checked only after `git add`. The
phase verifier runs it on the committed tree. This file holds the pattern and its negative
fixtures, so it is exempt. Dated records in docs/verification/ are exempt too: they are generated
from a verified run of the example, never edited, and stay as history after the example is removed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from support.dbt_cli import REPO_ROOT

from starter_cli import markers

# The handle and the marker words live in starter_cli.markers, the one definition that the guide
# renderer and remove-example share. They are re-exported here for the tests that import them.
HANDLE_RE = markers.HANDLE_RE
HANDLE_DIR = markers.HANDLE_DIR
BEGIN = markers.BEGIN
END = markers.END
EXEMPT_PREFIXES = (".planning/", ".claude/", "docs/verification/")
MARKERS_FILE = "src/starter_cli/markers.py"
EXEMPT_FILES = {"uv.lock", "package-lock.yml", "tests/conventions/test_handle.py", MARKERS_FILE}
STRICT_PREFIX = "packages/starter/"


def _strict_problems(path: str, text: str) -> list[str]:
    """Any match in the framework package is a problem, markers or not."""
    problems = []
    if HANDLE_RE.search(path):
        problems.append(f"{path}: the framework package must not name the example dataset")
    for number, line in enumerate(text.splitlines(), start=1):
        if HANDLE_RE.search(line):
            problems.append(
                f"{path}:{number}: the framework package must not name the example dataset"
            )
    return problems


def _marker_problems(path: str, text: str) -> list[str]:
    """Walk the lines with a begin/end state machine and report matches outside the markers."""
    problems = []
    begun_at = 0
    for number, line in enumerate(text.splitlines(), start=1):
        if markers.BEGIN_RE.match(line):
            if begun_at:
                problems.append(
                    f"{path}:{number}: {BEGIN} inside the block opened on line {begun_at}"
                )
            else:
                begun_at = number
        elif markers.END_RE.match(line):
            if begun_at:
                begun_at = 0
            else:
                problems.append(f"{path}:{number}: {END} with no {BEGIN} before it")
        elif not begun_at and HANDLE_RE.search(line):
            problems.append(
                f"{path}:{number}: names the example dataset outside a {HANDLE_DIR} directory "
                f"and outside {BEGIN} / {END}"
            )
    if begun_at:
        problems.append(f"{path}:{begun_at}: {BEGIN} is never closed by {END}")
    return problems


def check_handle(files: dict[str, str]) -> list[str]:
    """Return every place the example dataset leaks outside its removal handle."""
    problems: list[str] = []
    for path, text in files.items():
        if path in EXEMPT_FILES or path.startswith(EXEMPT_PREFIXES):
            continue
        if path.startswith(STRICT_PREFIX):
            problems += _strict_problems(path, text)
        elif HANDLE_DIR in path.split("/"):
            continue
        elif HANDLE_RE.search(path):
            problems.append(
                f"{path}: path names the example dataset outside a {HANDLE_DIR} directory"
            )
        else:
            problems += _marker_problems(path, text)
    return sorted(problems)


def tracked_files(root: Path = REPO_ROOT) -> dict[str, str]:
    """Map every tracked path to its UTF-8 text, skipping files that do not decode."""
    listing = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True
    ).stdout
    files: dict[str, str] = {}
    for raw in listing.split(b"\0"):
        if not raw:
            continue
        path = raw.decode()
        full = root / path
        if not full.is_file():
            continue
        try:
            files[path] = full.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            continue
    return files


def test_the_real_tracked_tree_keeps_the_example_inside_the_handle() -> None:
    files = tracked_files()
    assert files, "git ls-files returned nothing, so the check would pass vacuously"
    assert check_handle(files) == []


def test_the_real_tracked_tree_has_example_files_under_the_handle() -> None:
    tracked = tracked_files()
    if not any(path.startswith("skeleton/") for path in tracked):
        pytest.skip("the example has been removed from this tree, so no file sits under the handle")
    names = [path for path in tracked if "nyc_taxi" in path.split("/")]
    assert names, "no file sits under a nyc_taxi directory, so the handle is not exercised"


def test_a_matching_line_outside_the_handle_is_reported_with_its_line_number() -> None:
    files = {"models/x.sql": "select 1\nselect * from taxi_trips\n"}
    problems = check_handle(files)
    assert len(problems) == 1
    assert problems[0].startswith("models/x.sql:2")


def test_the_report_gives_the_path_and_line_for_the_first_line() -> None:
    problems = check_handle({"models/x.sql": "taxi\n"})
    assert len(problems) == 1
    assert problems[0].startswith("models/x.sql:1")


def test_matching_is_case_insensitive() -> None:
    problems = check_handle({"docs/a.md": "Rides are published by the TLC.\n"})
    assert len(problems) == 1
    assert problems[0].startswith("docs/a.md:1")


@pytest.mark.parametrize("word", ["taxi", "TAXI", "Tlc", "nyc", "NYC", "xtaxiy"])
def test_the_pattern_is_a_substring_search(word: str) -> None:
    assert check_handle({"a.txt": f"before {word} after\n"})


def test_every_violation_is_reported_not_only_the_first() -> None:
    files = {"a.txt": "taxi\nfine\nnyc\n", "b.txt": "tlc\n"}
    problems = check_handle(files)
    assert [p.split(":")[0:2] for p in problems] == [["a.txt", "1"], ["a.txt", "3"], ["b.txt", "1"]]
    assert problems == sorted(problems)


def test_a_path_with_a_near_miss_of_the_handle_name_fails() -> None:
    assert check_handle({"models/nyc_taxis/a.sql": "select 1\n"})
    assert check_handle({"seeds/nyctaxi.csv": "a,b\n"})
    assert check_handle({"models/nyc_taxi_extra/a.sql": "select 1\n"})


def test_a_path_with_a_component_named_exactly_the_handle_passes_whole() -> None:
    files = {
        "models/nyc_taxi/a.sql": "select * from taxi_trips -- nyc tlc\n",
        "seeds/nyc_taxi/zones.csv": "taxi,zone\n",
        "nyc_taxi/top.md": "taxi\n",
    }
    assert check_handle(files) == []


def test_a_matching_line_between_the_markers_passes() -> None:
    text = "intro\n<!-- example:begin -->\nthe taxi demo\n<!-- example:end -->\noutro\n"
    assert check_handle({"README.md": text}) == []


def test_a_matching_line_one_line_before_the_begin_marker_fails() -> None:
    text = "the taxi demo\n<!-- example:begin -->\nbody\n<!-- example:end -->\n"
    problems = check_handle({"README.md": text})
    assert len(problems) == 1
    assert problems[0].startswith("README.md:1")


def test_a_matching_line_one_line_after_the_end_marker_fails() -> None:
    text = "<!-- example:begin -->\nbody\n<!-- example:end -->\nthe taxi demo\n"
    problems = check_handle({"README.md": text})
    assert len(problems) == 1
    assert problems[0].startswith("README.md:4")


def test_an_end_marker_with_no_begin_fails() -> None:
    problems = check_handle({"README.md": "body\n<!-- example:end -->\n"})
    assert len(problems) == 1
    assert problems[0].startswith("README.md:2")


def test_a_nested_begin_marker_fails() -> None:
    text = "<!-- example:begin -->\n<!-- example:begin -->\n<!-- example:end -->\n"
    problems = check_handle({"README.md": text})
    assert len(problems) == 1
    assert problems[0].startswith("README.md:2")


def test_an_unclosed_begin_marker_fails_at_the_begin_line() -> None:
    problems = check_handle({"README.md": "intro\n<!-- example:begin -->\nbody\n"})
    assert len(problems) == 1
    assert problems[0].startswith("README.md:2")


def test_two_separate_marked_blocks_pass() -> None:
    text = "<!-- example:begin -->\ntaxi\n<!-- example:end -->\nmid\n# example:begin\nnyc\n# example:end\n"
    assert check_handle({"a.md": text}) == []


def test_an_empty_file_passes() -> None:
    assert check_handle({"models/empty.sql": ""}) == []


def test_the_framework_package_may_not_match_even_between_markers() -> None:
    text = "# example:begin\nselect * from nyc_zones\n# example:end\n"
    problems = check_handle({"packages/starter/m.sql": text})
    assert len(problems) == 1
    assert problems[0].startswith("packages/starter/m.sql:2")


def test_the_framework_package_may_not_match_even_under_a_handle_directory() -> None:
    assert check_handle({"packages/starter/nyc_taxi/m.sql": "select 1\n"})


def test_the_framework_package_path_itself_is_checked() -> None:
    assert check_handle({"packages/starter/macros/tlc.sql": "select 1\n"})


def test_a_clean_framework_file_passes() -> None:
    assert check_handle({"packages/starter/macros/m.sql": "select 1\n"}) == []


@pytest.mark.parametrize(
    "path",
    [
        ".planning/phases/x.md",
        ".claude/CLAUDE.md",
        "uv.lock",
        "package-lock.yml",
        "docs/verification/fabric-2026-11-25.md",
    ],
)
def test_planning_notes_and_lock_files_are_exempt(path: str) -> None:
    assert check_handle({path: "taxi tlc nyc\n"}) == []


def test_this_file_is_exempt_because_it_holds_the_pattern() -> None:
    assert "tests/conventions/test_handle.py" in EXEMPT_FILES
    assert check_handle({"tests/conventions/test_handle.py": "taxi\n"}) == []


def test_markers_py_is_the_only_exempt_file_under_src() -> None:
    exempt_in_src = sorted(
        path
        for path in EXEMPT_FILES | {p for p in tracked_files() if p.startswith(EXEMPT_PREFIXES)}
        if path.startswith("src/")
    )
    assert exempt_in_src == [MARKERS_FILE]
    assert check_handle({MARKERS_FILE: "taxi\n"}) == []
    assert check_handle({"src/starter_cli/other.py": "taxi\n"})


def test_marker_text_inside_a_string_is_not_a_marker() -> None:
    text = 'x = "<!-- example:begin -->"\nthe taxi demo\n'
    problems = check_handle({"src/a.py": text})
    assert len(problems) == 1
    assert problems[0].startswith("src/a.py:2")


def test_a_marker_mentioned_in_prose_is_not_a_marker() -> None:
    text = "Wrap it in <!-- example:begin --> and the end marker.\ntaxi\n"
    problems = check_handle({"README.md": text})
    assert len(problems) == 1
    assert problems[0].startswith("README.md:2")


def test_the_handle_is_the_one_in_markers() -> None:
    assert HANDLE_DIR == "nyc_taxi"
    assert HANDLE_RE is markers.HANDLE_RE
    assert HANDLE_RE.search("TLC")


# example:begin
# The sweep below names the example's models and keys, so it means nothing once the example is gone:
# removal strips this whole block, and the copy's gate runs without it (CI-05, D-22).
EXAMPLE_NAMES = ("yellow_trips", "fct_trips", "taxi_zones", "trip_key", "dim_zone", "zone_key")
KEPT_ROOTS = ("src/starter_cli/", "tests/conventions/", "tests/loader/", "tests/support/")


def example_names_problems(files: dict[str, str]) -> list[str]:
    """Return `<path>: <name>` for each example name in a kept Python file outside its markers.

    A kept file is a `.py` file under KEPT_ROOTS that has no `nyc_taxi` path component. Its text
    is stripped of the example marker blocks before the names are searched as substrings.
    """
    problems = []
    for path, text in files.items():
        if not path.endswith(".py") or not path.startswith(KEPT_ROOTS):
            continue
        if HANDLE_DIR in path.split("/"):
            continue
        kept = markers.strip_example_blocks(text)
        problems.extend(f"{path}: {name}" for name in EXAMPLE_NAMES if name in kept)
    return sorted(problems)


def test_example_names_stay_inside_markers_in_every_kept_file() -> None:
    files = tracked_files()
    scanned = [p for p in files if p.endswith(".py") and p.startswith(KEPT_ROOTS)]
    assert scanned, "no kept Python file was scanned, so the sweep would pass on nothing"
    assert example_names_problems(files) == []


def test_example_names_outside_markers_are_reported() -> None:
    files = {
        "tests/conventions/test_x.py": 'ROW = "gold__fct_trips"\nKEY = "zone_key"\n',
        "src/starter_cli/y.py": 'table = "silver.yellow_trips"\n',
        "tests/loader/clean.py": "VALUE = 1\n",
    }
    assert example_names_problems(files) == [
        "src/starter_cli/y.py: yellow_trips",
        "tests/conventions/test_x.py: fct_trips",
        "tests/conventions/test_x.py: zone_key",
    ]


def test_example_names_inside_markers_are_allowed() -> None:
    text = 'A = 1\n# example:begin\nROW = "gold__fct_trips"\n# example:end\nB = 2\n'
    assert example_names_problems({"tests/loader/test_x.py": text}) == []
    after = text.replace("# example:end\n", "# example:end\nC = 'dim_zone'\n")
    assert example_names_problems({"tests/loader/test_x.py": after}) == [
        "tests/loader/test_x.py: dim_zone"
    ]


def test_example_names_under_the_handle_directory_are_not_scanned() -> None:
    files = {
        "src/starter_cli/nyc_taxi/__init__.py": "NAME = 'yellow_trips'\n",
        "tests/conventions/nyc_taxi/test_a.py": "NAME = 'taxi_zones'\n",
    }
    assert example_names_problems(files) == []


def test_example_names_outside_the_kept_roots_are_not_scanned() -> None:
    files = {
        "docs/guide.py": "NAME = 'yellow_trips'\n",
        "tests/other/test_a.py": "NAME = 'fct_trips'\n",
        "src/starter_cli/data.txt": "yellow_trips\n",
        "skeleton/tests/test_b.py": "NAME = 'trip_key'\n",
    }
    assert example_names_problems(files) == []


# example:end

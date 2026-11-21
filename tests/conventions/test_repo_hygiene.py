"""Tracked-file hygiene: no data, database or secret file can sit in git unnoticed.

The repo is public and is copied as a template. Raw feed data, extracts and DuckDB databases
stay out of it (data terms and size), and so do local secrets. The one CSV location is
`seeds/`, for small code tables. The rules are checked on the list of tracked paths and on the
`.gitignore` that keeps those files from being added by accident.
"""

from __future__ import annotations

import subprocess

import pytest
from support.dbt_cli import REPO_ROOT

# GREEN: check_tracked_files goes here.

REQUIRED_IGNORE_LINES = ("data/*", "!data/.gitkeep", "target/", "dbt_packages/", ".env", ".user.yml")


def _tracked_paths() -> list[str]:
    listing = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, check=True
    ).stdout
    return [raw.decode() for raw in listing.split(b"\0") if raw]


def test_the_real_tracked_tree_holds_no_data_database_or_secret_file() -> None:
    paths = _tracked_paths()
    assert paths, "git ls-files returned nothing, so the check would pass vacuously"
    assert check_tracked_files(paths) == []


@pytest.mark.parametrize(
    "path",
    [
        "data/dbt_starter.duckdb",
        "data/dbt_starter.duckdb.wal",
        "data/raw/x/y.csv",
        "data/notes.txt",
        "exports/a.parquet",
        "EXPORTS/A.PARQUET",
        "models/a.csv",
        "docs/zones.csv",
        "local.duckdb",
        ".env",
        "sub/.env",
        ".user.yml",
    ],
)
def test_a_forbidden_tracked_path_is_reported_by_name(path: str) -> None:
    problems = check_tracked_files(["README.md", path])
    assert len(problems) == 1
    assert problems[0].startswith(path)


@pytest.mark.parametrize(
    "path",
    ["seeds/a/b.csv", "seeds/codes.csv", "data/.gitkeep", ".env.example", "src/data.py", "README.md"],
)
def test_an_allowed_tracked_path_passes(path: str) -> None:
    assert check_tracked_files([path]) == []


def test_a_path_that_breaks_two_rules_is_reported_once() -> None:
    assert len(check_tracked_files(["data/raw/x/y.csv"])) == 1


def test_every_forbidden_path_is_reported_in_sorted_order() -> None:
    problems = check_tracked_files(["z.parquet", "a.duckdb", "m/n.csv"])
    assert len(problems) == 3
    assert problems == sorted(problems)


def test_the_empty_list_passes() -> None:
    assert check_tracked_files([]) == []


def test_gitignore_keeps_data_builds_and_local_files_out_of_git() -> None:
    lines = {line.strip() for line in (REPO_ROOT / ".gitignore").read_text().splitlines()}
    missing = [line for line in REQUIRED_IGNORE_LINES if line not in lines]
    assert missing == []

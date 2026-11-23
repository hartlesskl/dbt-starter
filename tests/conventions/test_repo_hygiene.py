"""Tracked-file hygiene: no data, database or secret file can sit in git unnoticed.

The repo is public and is copied as a template. Raw feed data, extracts and DuckDB databases
stay out of it (data terms and size), and so do local secrets. The one CSV location is
`seeds/`, for small code tables. The rules are checked on the list of tracked paths and on the
`.gitignore` that keeps those files from being added by accident.

One more rule reads file text: no tracked file outside the ADR history may hold a Warehouse or
T-SQL trace (D-13). The template targets DuckDB and Fabric Lakehouse (Spark SQL), so old-adapter
names and T-SQL reasoning would teach an adopter rules that no longer apply.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping
from pathlib import PurePosixPath

import pytest
from support.dbt_cli import REPO_ROOT

REQUIRED_IGNORE_LINES = (
    "data/*",
    "!data/.gitkeep",
    "target/",
    "dbt_packages/",
    ".env",
    ".user.yml",
    "livy-session-id.txt",
)
DATABASE_SUFFIXES = (".duckdb", ".duckdb.wal", ".parquet")
SECRET_NAMES = {".env", ".user.yml"}


def _reasons(path: str) -> list[str]:
    pure = PurePosixPath(path)
    lowered = path.lower()
    reasons = []
    if lowered.endswith(DATABASE_SUFFIXES):
        reasons.append("a database or Parquet file must not be tracked")
    if lowered.endswith(".csv") and pure.parts[0] != "seeds":
        reasons.append("a CSV file may only be tracked under seeds/")
    if pure.parts[0] == "data" and path != "data/.gitkeep":
        reasons.append("only data/.gitkeep may be tracked under data/")
    if pure.name in SECRET_NAMES:
        reasons.append("a local settings or secret file must not be tracked")
    return reasons


def check_tracked_files(paths: list[str]) -> list[str]:
    """Return one problem per tracked path that is data, a database or a local secret file."""
    return sorted(f"{path}: {'; '.join(reasons)}" for path in paths if (reasons := _reasons(path)))


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
    [
        "seeds/a/b.csv",
        "seeds/codes.csv",
        "data/.gitkeep",
        ".env.example",
        "src/data.py",
        "README.md",
    ],
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


def _is_ignored(path: str) -> bool:
    """Ask git itself whether `path` is ignored: 0 means ignored, 1 means not ignored."""
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", path],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    assert result.returncode in (0, 1), result.stderr.decode()
    return result.returncode == 0


@pytest.mark.parametrize(
    "path",
    [
        "data/dbt_starter.duckdb",
        "data/dbt_starter.duckdb.wal",
        "data/raw/acme/widgets.csv",
        "packages/starter/data/raw/x.csv",
        "packages/starter/data/dbt_starter.duckdb",
        "scratch.duckdb",
        "elsewhere/scratch.duckdb.wal",
        "exports/a.parquet",
        ".env",
        ".env.local",
        "sub/.env.production",
        "target/manifest.json",
        "dbt_packages/dbt_utils/dbt_project.yml",
        ".user.yml",
    ],
)
def test_git_ignores_data_databases_and_local_files_wherever_they_sit(path: str) -> None:
    assert _is_ignored(path), f"{path} is not ignored, so it could be committed by accident"


@pytest.mark.parametrize(
    "path",
    [
        "data/.gitkeep",
        "seeds/acme/acme__known_codes.csv",
        ".env.example",
        "src/starter_cli/landing.py",
        "README.md",
    ],
)
def test_git_does_not_ignore_files_that_belong_in_the_repo(path: str) -> None:
    assert not _is_ignored(path), f"{path} is ignored, but it belongs in the repo"


# A Warehouse or T-SQL trace (D-13). The lookaheads keep the Lakehouse adapter's names
# (dbt-fabricspark, dbt.adapters.fabricspark, fabricspark__ macros) clean.
TRACE_RE = re.compile(
    r"t-sql|\btsql\b|dbt-fabric(?!spark)|dbt_fabric(?!spark)|adapters\.fabric(?!spark)"
    r"|\bmssql|\bodbc\b|hashbytes|datetime2|\bfabric__|nvarchar|datefirst"
    r"|wrapped[-_ ]cte|fabric warehouse|datawarehouse",
    re.IGNORECASE,
)
# The ADRs keep the history of the old adapter; this file holds the pattern itself.
TRACE_EXEMPT_PREFIXES = ("docs/decisions/",)
TRACE_EXEMPT_FILES = {"tests/conventions/test_repo_hygiene.py"}


def tracked_texts() -> dict[str, str]:
    """Read every tracked file as text, replacing bytes that are not UTF-8."""
    return {
        path: (REPO_ROOT / path).read_text(encoding="utf-8", errors="replace")
        for path in _tracked_paths()
        if (REPO_ROOT / path).is_file()
    }


def check_no_warehouse_traces(files: Mapping[str, str]) -> list[str]:
    """Return one problem per line of a non-exempt file that names the old Warehouse adapter."""
    problems = []
    for path, text in files.items():
        if path in TRACE_EXEMPT_FILES or path.startswith(TRACE_EXEMPT_PREFIXES):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            for match in TRACE_RE.finditer(line):
                problems.append(
                    f"{path}:{number}: {match.group(0)} is a Warehouse or T-SQL trace (D-13)"
                )
    return sorted(problems)


def test_the_tracked_tree_holds_no_warehouse_trace() -> None:
    texts = tracked_texts()
    assert texts, "tracked_texts() returned nothing, so the check would pass vacuously"
    assert check_no_warehouse_traces(texts) == []


@pytest.mark.parametrize(
    ("line", "token"),
    [
        ("-- the old adapter used T-SQL", "T-SQL"),
        ("-- a tsql rule", "tsql"),
        ("dbt-fabric==1.12.0", "dbt-fabric"),
        ("pip install dbt_fabric", "dbt_fabric"),
        ("import dbt.adapters.fabric", "adapters.fabric"),
        ("driver: mssql-python", "mssql"),
        ("ODBC Driver 18", "ODBC"),
        ("select hashbytes('md5', x)", "hashbytes"),
        ("cast(x as datetime2(6))", "datetime2"),
        ("{% macro fabric__hash(x) %}", "fabric__"),
        ("cast(x as nvarchar(10))", "nvarchar"),
        ("set DATEFIRST 1", "DATEFIRST"),
        ("-- wrapped-cte test wrapper", "wrapped-cte"),
        ("WRAPPED_CTE = True", "WRAPPED_CTE"),
        ("-- built on a Fabric Warehouse", "Fabric Warehouse"),
        ("-- the datawarehouse layer", "datawarehouse"),
    ],
)
def test_a_warehouse_trace_is_reported_with_its_line(line: str, token: str) -> None:
    problems = check_no_warehouse_traces({"models/a.sql": f"select 1\n{line}\n"})
    assert len(problems) == 1
    assert problems[0].startswith("models/a.sql:2: ")
    assert token.lower() in problems[0].lower()


def test_matching_is_case_insensitive() -> None:
    assert check_no_warehouse_traces({"a.md": "the Old DbT-FaBrIc adapter\n"}) != []


@pytest.mark.parametrize(
    "line",
    [
        "dbt-fabricspark==1.12.2",
        "import dbt.adapters.fabricspark",
        "{% macro fabricspark__parse_date(x) %}",
    ],
)
def test_the_new_adapter_names_are_not_traces(line: str) -> None:
    assert check_no_warehouse_traces({"a.txt": f"{line}\n"}) == []


def test_the_adr_history_and_this_file_are_exempt() -> None:
    files = {
        "docs/decisions/0002-starter-shims.md": "the T-SQL shims and dbt-fabric\n",
        "tests/conventions/test_repo_hygiene.py": "TRACE = 'hashbytes'\n",
    }
    assert check_no_warehouse_traces(files) == []
    assert check_no_warehouse_traces({"docs/other.md": "dbt-fabric\n"}) != []

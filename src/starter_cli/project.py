"""Locate the repo, the DuckDB file and the raw-file cache, and discover source specs."""

from __future__ import annotations

import importlib
import os
import pkgutil
import re
import subprocess
import types
from pathlib import Path

import starter_cli

DEFAULT_DUCKDB_PATH = "data/dbt_starter.duckdb"

# The same `YYYY-MM` rule as landing.MONTH_RE, kept here because landing imports this module.
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _is_checkout(candidate: Path) -> bool:
    """Return True when `candidate` holds both the dbt project file and the Python project file.

    Nested dbt projects (`packages/starter`, `dbt_packages/*`) have a `dbt_project.yml` but no
    `pyproject.toml`, so they are never mistaken for the checkout.
    """
    return (candidate / "dbt_project.yml").is_file() and (candidate / "pyproject.toml").is_file()


def repo_root() -> Path:
    """Return the dbt-starter checkout that this installed package belongs to.

    The package is installed editable inside the checkout, so the location of this file is
    authoritative and is searched first. The current directory is only a fallback. Either way a
    directory counts only when it holds both `dbt_project.yml` and `pyproject.toml`, so the
    commands work from any sub-folder of the checkout, including the nested dbt packages, and
    never pick up an unrelated dbt project.
    """
    cwd = Path.cwd().resolve()
    here = Path(__file__).resolve()
    for start in (here, cwd):
        for candidate in [start, *start.parents]:
            if _is_checkout(candidate):
                return candidate
    raise SystemExit(
        "Could not find the dbt-starter checkout (a folder holding dbt_project.yml and "
        "pyproject.toml). Run this command from inside it."
    )


def duckdb_path(root: Path) -> Path:
    """Return the DuckDB file path: `DBT_DUCKDB_PATH`, resolved against `root` when relative.

    `profiles.yml` reads the same variable with the same default.
    """
    path = Path(os.environ.get("DBT_DUCKDB_PATH", DEFAULT_DUCKDB_PATH))
    return path if path.is_absolute() else root / path


def raw_dir(root: Path) -> Path:
    """Return the gitignored directory that caches downloaded files."""
    return root / "data" / "raw"


def discover_sources() -> dict[str, types.ModuleType]:
    """Import every sub-package of `starter_cli` that defines `SOURCE` and `ENTITIES`.

    The key is the module's `SOURCE`. Generic code never names a specific source.
    """
    found: dict[str, types.ModuleType] = {}
    for info in pkgutil.iter_modules(starter_cli.__path__):
        if not info.ispkg:
            continue
        module = importlib.import_module(f"{starter_cli.__name__}.{info.name}")
        if hasattr(module, "SOURCE") and hasattr(module, "ENTITIES"):
            found[module.SOURCE] = module
    return found


def fabric_default_months(module: types.ModuleType) -> tuple[str, ...] | None:
    """Return the months a source's monthly entities land on Fabric by default, or None.

    A source may declare `FABRIC_DEFAULT_MONTHS`, a non-empty tuple of `YYYY-MM` months. None means
    the source declares nothing, so the loader uses each entity's own default months, as on DuckDB.
    A declaration that is empty, not a tuple, or holds a value that is not `YYYY-MM` raises a
    ValueError naming the source, so a typo never reaches a file name or the Lakehouse.
    """
    if not hasattr(module, "FABRIC_DEFAULT_MONTHS"):
        return None
    name = getattr(module, "SOURCE", None) or getattr(module, "__name__", "?")
    months = module.FABRIC_DEFAULT_MONTHS
    if not isinstance(months, tuple) or not months:
        raise ValueError(
            f"source {name!r}: FABRIC_DEFAULT_MONTHS must be a non-empty tuple of YYYY-MM months"
        )
    for month in months:
        if not isinstance(month, str) or not _MONTH_RE.match(month):
            raise ValueError(
                f"source {name!r}: FABRIC_DEFAULT_MONTHS holds {month!r}, which is not a "
                "YYYY-MM month"
            )
    return months


def default_volume_month(sources: dict[str, types.ModuleType]) -> str | None:
    """Return the earliest month any given source declares in FABRIC_DEFAULT_MONTHS, or None."""
    declared = [
        month for module in sources.values() for month in (fabric_default_months(module) or ())
    ]
    return min(declared) if declared else None


# The one line every license statement echoes: the license covers this project's code, and each
# source's data keeps its publisher's terms. `docs/data-terms.md` holds it word for word.
LICENSE_NOTE = (
    "The MIT license covers this project's code only; each data source keeps its publisher's "
    "terms (see docs/data-terms.md)."
)


def data_terms_epilog(sources: dict[str, types.ModuleType]) -> str:
    """Return the credit text a command's `--help` ends with.

    One `DATA_TERMS["credit_line"]` per source that declares `DATA_TERMS`, in source-name order,
    then `LICENSE_NOTE`. A source that declares none adds no line, and the license note is always
    present. Generic code never names a specific source.
    """
    lines = [
        module.DATA_TERMS["credit_line"]
        for _, module in sorted(sources.items())
        if hasattr(module, "DATA_TERMS")
    ]
    return "\n".join([*lines, LICENSE_NOTE])


# One pattern per remote form that names a GitHub repository. The owner is letters, digits and
# hyphens (at most 39, not starting with a hyphen); the repository is letters, digits, `.`, `_`
# and `-`. The lazy repository group leaves a trailing `.git` to the optional suffix.
_OWNER = r"(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))"
_REPO = r"(?P<repo>[A-Za-z0-9._-]+?)"
_REMOTE_FORMS = (
    re.compile(rf"https://github\.com/{_OWNER}/{_REPO}(?:\.git)?/?"),
    re.compile(rf"git@github\.com:{_OWNER}/{_REPO}(?:\.git)?"),
    re.compile(rf"ssh://git@github\.com/{_OWNER}/{_REPO}(?:\.git)?"),
)


def github_repo(remote: str | None) -> tuple[str, str] | None:
    """Return (owner, repository) for a remote that names a GitHub repository, else None.

    `https://github.com/<owner>/<repo>(.git)`, `git@github.com:<owner>/<repo>.git` and
    `ssh://git@github.com/<owner>/<repo>.git` are the forms read. The owner keeps its case. Another
    host, an empty or missing remote, a malformed owner or repository and the names `.` and `..`
    give None.
    """
    if not remote:
        return None
    for form in _REMOTE_FORMS:
        match = form.fullmatch(remote)
        if match is None:
            continue
        owner, repo = match.group("owner"), match.group("repo")
        if repo in {".", ".."}:
            return None
        return owner, repo
    return None


def git_remote_url(root: Path) -> str | None:
    """Return `remote.origin.url` of the checkout at `root`, or None when there is none.

    git runs with an argument list, no shell and a 5 second limit. A missing git binary, a
    timeout and a non-zero exit all give None.
    """
    try:
        result = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None

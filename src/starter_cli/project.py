"""Locate the repo, the DuckDB file and the raw-file cache, and discover source specs."""

from __future__ import annotations

import importlib
import os
import pkgutil
import re
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

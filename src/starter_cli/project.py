"""Locate the repo, the DuckDB file and the raw-file cache, and discover source specs."""

from __future__ import annotations

import importlib
import os
import pkgutil
import types
from pathlib import Path

import starter_cli

DEFAULT_DUCKDB_PATH = "data/dbt_starter.duckdb"


def repo_root() -> Path:
    """Return the nearest directory holding `dbt_project.yml`.

    Walks upward from the current directory first, then from this file, so the commands
    work from any sub-folder of the checkout.
    """
    cwd = Path.cwd().resolve()
    here = Path(__file__).resolve()
    for start in (cwd, here):
        for candidate in [start, *start.parents]:
            if (candidate / "dbt_project.yml").is_file():
                return candidate
    raise SystemExit(
        "Could not find dbt_project.yml. Run this command from inside the dbt-starter checkout."
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

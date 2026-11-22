"""`uv run gate`: the pull request checks, in a fixed order, on a database built from a fixture.

The gate is generic: it never names a data source. A source that ships a fixture defines
`write_fixture(folder)` in its sub-package; the gate finds it through `discover_sources()`,
writes the files, lands them with the real loader (`--from-dir`, so no request goes out) into
its own DuckDB file under `target/gate/`, and builds the dbt project on that file. The demo
database is never opened.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

from starter_cli import load
from starter_cli.project import DEFAULT_DUCKDB_PATH, discover_sources

# Lowercase dummy values: the offline Fabric compile never connects, and the SQL scan rejects
# mixed-case identifiers outside the catalog part of a table name.
DUMMY_FABRIC_ENV: dict[str, str] = {
    "FABRIC_WORKSPACE_NAME": "wh_workspace",
    "FABRIC_DATABASE": "wh_compile",
}


class FixtureBuild(NamedTuple):
    """Where one fixture build is: its DuckDB file, its dbt target folder, its run results."""

    database: Path
    target: Path
    run_results: Path


def _check_workdir(root: Path, workdir: Path) -> None:
    """Refuse a working folder that `build_fixture` would be unsafe to delete."""
    resolved = workdir.resolve()
    if resolved == resolved.parent:
        raise ValueError(f"{workdir} is a filesystem root and cannot be a gate working folder")
    if root.resolve().is_relative_to(resolved):
        raise ValueError(f"{workdir} holds the checkout, so it cannot be a gate working folder")
    for marker in ("dbt_project.yml", "pyproject.toml"):
        if (resolved / marker).exists():
            raise ValueError(f"{workdir} holds {marker}, so it cannot be a gate working folder")


def land_fixture(root: Path, workdir: Path, database: Path) -> None:
    """Write every source's fixture under `workdir/in/<source>` and land it into `database`.

    Each file goes in through `load.main(... --from-dir ...)`, the same path a user's own files
    take, so nothing is downloaded. `DBT_DUCKDB_PATH` and the working directory are set only for
    the duration of the call.
    """
    previous_database = os.environ.get("DBT_DUCKDB_PATH")
    previous_cwd = Path.cwd()
    os.environ["DBT_DUCKDB_PATH"] = str(database)
    try:
        for source, module in sorted(discover_sources().items()):
            write = getattr(module, "write_fixture", None)
            if write is None:
                continue
            folder = workdir / "in" / source
            written = write(folder)
            for entity in sorted(module.ENTITIES):
                if entity not in written:
                    continue
                code = load.main([source, entity, "--from-dir", str(folder)])
                if code != 0:
                    raise RuntimeError(f"landing {source}.{entity} from {folder} exited {code}")
    finally:
        os.chdir(previous_cwd)
        if previous_database is None:
            os.environ.pop("DBT_DUCKDB_PATH", None)
        else:
            os.environ["DBT_DUCKDB_PATH"] = previous_database


def build_fixture(root: Path, workdir: Path) -> FixtureBuild:
    """Recreate `workdir`, land the fixture into `workdir/fixture.duckdb` and run `dbt build`.

    The database is never the default demo database. dbt runs as a subprocess with its own
    target folder, so the build cannot overwrite another run's manifest.
    """
    _check_workdir(root, workdir)
    database = workdir / "fixture.duckdb"
    if database.resolve() == (root / DEFAULT_DUCKDB_PATH).resolve():
        raise ValueError(f"{database} is the demo database, which the gate never opens")
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)

    land_fixture(root, workdir, database)

    target = workdir / "build"
    command = [
        sys.executable,
        "-m",
        "dbt.cli.main",
        "build",
        "--target-path",
        str(target),
        "--profiles-dir",
        ".",
        "--project-dir",
        ".",
    ]
    result = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        env={**os.environ, "DBT_DUCKDB_PATH": str(database)},
        check=False,
    )
    if result.returncode != 0:
        tail = "\n".join((result.stdout + result.stderr).splitlines()[-60:])
        raise RuntimeError(f"dbt build on the fixture exited {result.returncode}\n{tail}")
    return FixtureBuild(database=database, target=target, run_results=target / "run_results.json")


# Stubs until the gate command is written.
STEPS: tuple[str, ...] = ()
RUNNERS: dict = {}


def fabric_adapter_installed() -> bool:
    return False


def gate_database(root: Path) -> Path:
    return root / DEFAULT_DUCKDB_PATH


def main(argv: list[str] | None = None) -> int:
    return 0

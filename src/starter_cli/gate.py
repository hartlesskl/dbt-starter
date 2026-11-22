"""`uv run gate`: the pull request checks, in a fixed order, on a database built from a fixture.

The gate is generic: it never names a data source. A source that ships a fixture defines
`write_fixture(folder)` in its sub-package; the gate finds it through `discover_sources()`,
writes the files, lands them with the real loader (`--from-dir`, so no request goes out) into
its own DuckDB file under `target/gate/`, and builds the dbt project on that file. The demo
database is never opened.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from starter_cli import load
from starter_cli.project import DEFAULT_DUCKDB_PATH, discover_sources, repo_root

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


# --- The gate command -------------------------------------------------------------------------

# The step order is fixed. It is the D-18 order as the user amended it on 2026-11-22: D-18 first
# listed the fixture build after the lint step, but several convention checks read a built
# database, so the fixture build now comes before the scan, the convention tests and the lint.
# The steps themselves are unchanged and the exact-count check stays last. `deps` is setup.
STEPS: tuple[str, ...] = (
    "deps",
    "parse",
    "compile-fabric",
    "fixture-build",
    "scan",
    "tests",
    "lint",
    "counts",
)

GATE_COMMAND = "uv run --locked --extra fabric gate"
FABRIC_HINT = (
    "The Fabric adapter (dbt-fabric) is not installed, so the offline Fabric compile cannot "
    f"run. Run the gate with its extra: `{GATE_COMMAND}`."
)


def fabric_adapter_installed() -> bool:
    """Return True when the dbt Fabric adapter can be imported."""
    return importlib.util.find_spec("dbt.adapters.fabric") is not None


def gate_database(root: Path) -> Path:
    """The gate's own DuckDB file. Every step uses it, so the demo database is never opened."""
    return root / "target" / "gate" / "fixture.duckdb"


def _run(command: list[str], root: Path, env: dict[str, str]) -> int:
    """Run one command from the repo root, streaming its output. Return its exit code."""
    return subprocess.run(command, cwd=root, env=env, check=False).returncode


def _dbt(*args: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "dbt.cli.main",
        *args,
        "--profiles-dir",
        ".",
        "--project-dir",
        ".",
    ]


def _python(*args: str) -> list[str]:
    return [sys.executable, "-m", *args]


def _step_deps(ctx: dict) -> int:
    return _run(_dbt("deps"), ctx["root"], ctx["env"])


def _step_parse(ctx: dict) -> int:
    command = _dbt("parse", "--target", "duckdb", "--no-partial-parse")
    return _run([*command, "--target-path", "target/gate/parse"], ctx["root"], ctx["env"])


def _step_compile_fabric(ctx: dict) -> int:
    command = _dbt("compile", "--target", "fabric", "--no-populate-cache")
    env = {**ctx["env"], **DUMMY_FABRIC_ENV}
    return _run([*command, "--target-path", "target/gate/fabric"], ctx["root"], env)


def _step_fixture_build(ctx: dict) -> int:
    """Build the fixture database, then point every later step at it."""
    try:
        build = build_fixture(ctx["root"], ctx["root"] / "target" / "gate")
    except (RuntimeError, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 1
    ctx["env"]["DBT_DUCKDB_PATH"] = str(build.database)
    ctx["env"]["GATE_DATABASE"] = str(build.database)
    ctx["env"]["GATE_RUN_RESULTS"] = str(build.run_results)
    return 0


def _step_scan(ctx: dict) -> int:
    command = _python(
        "pytest",
        "tests/conventions/test_sql_scan.py",
        "tests/conventions/test_fabric_compile.py",
        "-q",
    )
    return _run(command, ctx["root"], ctx["env"])


def _step_tests(ctx: dict) -> int:
    command = _python("pytest", "tests", "-q", "-m", "not network and not gate_counts")
    return _run(command, ctx["root"], ctx["env"])


def _step_lint(ctx: dict) -> int:
    for command in (
        _python("sqlfluff", "lint", "models"),
        _python("ruff", "check", "src", "tests"),
        _python("ruff", "format", "--check", "src", "tests"),
    ):
        code = _run(command, ctx["root"], ctx["env"])
        if code != 0:
            return code
    return 0


def _step_counts(ctx: dict) -> int:
    return _run(_python("pytest", "-q", "-m", "gate_counts"), ctx["root"], ctx["env"])


# One runner per step, in a dict so a test can replace them. A runner takes the shared context
# (the repo root and the environment every child gets) and returns an exit code.
RUNNERS: dict[str, Callable[[dict], int]] = {
    "deps": _step_deps,
    "parse": _step_parse,
    "compile-fabric": _step_compile_fabric,
    "fixture-build": _step_fixture_build,
    "scan": _step_scan,
    "tests": _step_tests,
    "lint": _step_lint,
    "counts": _step_counts,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gate",
        description=(
            "Run the pull request checks in a fixed order, on a database built from an "
            "invented fixture: " + ", ".join(STEPS) + ". Stops at the first failing step."
        ),
    )
    parser.parse_args(argv)

    if not fabric_adapter_installed():
        print(FABRIC_HINT, file=sys.stderr, flush=True)
        return 2

    root = repo_root()
    os.chdir(root)  # profiles.yml and every relative path resolve against the repo root
    # Every child, from the first, uses the gate's own database: the demo database is never opened.
    context = {"root": root, "env": {**os.environ, "DBT_DUCKDB_PATH": str(gate_database(root))}}
    for step in STEPS:
        print(f"gate: {step}", flush=True)
        code = RUNNERS[step](context)
        if code != 0:
            print(f"gate failed at: {step} (exit {code})", flush=True)
            return code
    print("gate: all steps passed", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

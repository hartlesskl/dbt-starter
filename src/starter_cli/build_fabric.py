"""`uv run --locked --extra fabric build-fabric`: build the project on a Fabric Lakehouse (D-21).

`build-fabric` (or `make build-fabric`) runs dbt against a Fabric Lakehouse from this machine or CI
through dbt-fabricspark. It needs `az login` (FABRIC_AUTHENTICATION defaults to CLI), the three
required variables and one schema-enabled Lakehouse:

    FABRIC_WORKSPACE_ID    the workspace id, a UUID
    FABRIC_LAKEHOUSE_ID    the Lakehouse id, a UUID
    FABRIC_LAKEHOUSE       the Lakehouse name

It checks the environment first and names every problem before any dbt command starts, then runs
`dbt deps` and `dbt build --target fabric --profiles-dir . --project-dir .` from the repo root and
stops at the first failure. The messages name variables only, never their values. DuckDB stays the
default everywhere else, and Fabric is compile-checked only until Phase 5 verifies it live.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from starter_cli.project import repo_root

REQUIRED_ENV: tuple[str, ...] = ("FABRIC_WORKSPACE_ID", "FABRIC_LAKEHOUSE_ID", "FABRIC_LAKEHOUSE")
UUID_ENV: tuple[str, ...] = ("FABRIC_WORKSPACE_ID", "FABRIC_LAKEHOUSE_ID")
# The rule the adapter's own UUID validation applies: 8-4-4-4-12 hex digits, either case.
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
# The profile's FABRIC_SCHEMA default (profiles.yml).
DEFAULT_SCHEMA = "dbo"

BUILD_FABRIC_COMMAND = "uv run --locked --extra fabric build-fabric"
ADAPTER_HINT = (
    "The Fabric adapter (dbt-fabricspark) is not installed, so build-fabric cannot run. "
    f"Run it with its extra: `{BUILD_FABRIC_COMMAND}`."
)


def fabric_adapter_installed() -> bool:
    """Return True when the dbt Fabric adapter can be imported."""
    return importlib.util.find_spec("dbt.adapters.fabricspark") is not None


def check_environment(env: Mapping[str, str]) -> list[str]:
    """Return what is wrong with the Fabric variables, naming variables and never their values."""
    problems = [f"{name} is not set" for name in REQUIRED_ENV if not env.get(name, "").strip()]
    problems += [
        f"{name} is not a UUID"
        for name in UUID_ENV
        if env.get(name, "").strip() and not UUID_RE.match(env[name].strip())
    ]
    lakehouse = env.get("FABRIC_LAKEHOUSE", "").strip()
    schema = env.get("FABRIC_SCHEMA", "").strip() or DEFAULT_SCHEMA
    if lakehouse and schema.lower() == lakehouse.lower():
        problems.append(
            "FABRIC_SCHEMA must differ from FABRIC_LAKEHOUSE on a schema-enabled Lakehouse "
            f"(FABRIC_SCHEMA defaults to {DEFAULT_SCHEMA})"
        )
    return problems


def dbt_command(*args: str) -> list[str]:
    """One dbt invocation from the repo root, with the committed profile."""
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


def _run(command: list[str], root: Path, env: dict[str, str]) -> int:
    """Run one command from the repo root, streaming its output. Return its exit code."""
    return subprocess.run(command, cwd=root, env=env, check=False).returncode


# A module-level callable so a test can replace it. It takes the command, the working folder and
# the environment, and returns an exit code.
RUNNER: Callable[[list[str], Path, dict[str, str]], int] = _run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="build-fabric",
        description=(
            "Run `dbt deps` and `dbt build --target fabric` against a Fabric Lakehouse, stopping "
            "at the first failure. Requires `az login` and these variables: "
            + ", ".join(REQUIRED_ENV)
            + f". FABRIC_SCHEMA (default {DEFAULT_SCHEMA}) must differ from FABRIC_LAKEHOUSE."
        ),
    )
    parser.parse_args(argv)

    if not fabric_adapter_installed():
        print(ADAPTER_HINT, file=sys.stderr, flush=True)
        return 2

    problems = check_environment(os.environ)
    for problem in problems:
        print(f"build-fabric: {problem}", file=sys.stderr, flush=True)
    if problems:
        return 2

    root = repo_root()
    os.chdir(root)  # profiles.yml and every relative path resolve against the repo root
    steps = (
        ("deps", dbt_command("deps")),
        ("build", dbt_command("build", "--target", "fabric")),
    )
    for step, command in steps:
        print(f"build-fabric: {step}", flush=True)
        code = RUNNER(command, root, dict(os.environ))
        if code != 0:
            print(f"build-fabric failed at: {step} (exit {code})", flush=True)
            return code
    print("build-fabric: all steps passed", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

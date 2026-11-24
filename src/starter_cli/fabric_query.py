"""Read named SQL from a dbt target through `dbt run-operation starter.query_json` (D-10, D-12).

Every live check reads its counts back from the Lakehouse, never from what the loader reported.
The bridge reuses dbt's own connection, so it gets the build's authentication, session settings
and retries, and the same macro runs on DuckDB, which lets the gate test it offline. Each call
opens one session, so a caller batches every query of a step into one `run_queries` call.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from starter_cli.project import repo_root

SENTINEL = "@@STARTER_JSON@@"
QUERY_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class FabricQueryError(RuntimeError):
    """A query could not be run or its output could not be read.

    Messages name the query or the exit code, never the SQL or any setting's value.
    """


def parse_output(text: str) -> dict[str, list[list[str | None]]]:
    """Return {name: rows} from every sentinel line in dbt's output, ignoring all other lines."""
    results: dict[str, list[list[str | None]]] = {}
    for line in text.splitlines():
        position = line.find(SENTINEL)
        if position < 0:
            continue
        try:
            payload = json.loads(line[position + len(SENTINEL) :])
        except json.JSONDecodeError as error:
            raise FabricQueryError("malformed query output: a sentinel line is not JSON") from error
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("name"), str)
            or not isinstance(payload.get("rows"), list)
        ):
            raise FabricQueryError("malformed query output: a sentinel line lacks name or rows")
        name = payload["name"]
        if name in results:
            raise FabricQueryError(f"query {name!r} appears twice in the output")
        results[name] = payload["rows"]
    return results


def _run(command: list[str], root: Path, env: dict[str, str]) -> tuple[int, str]:
    """Run one command from the repo root and return its exit code and its standard output."""
    completed = subprocess.run(
        command, cwd=root, env=env, capture_output=True, text=True, check=False
    )
    return completed.returncode, completed.stdout


# A module-level callable so a test can replace it. It takes the command, the working folder and
# the environment, and returns the exit code and the standard output.
RUNNER: Callable[[list[str], Path, dict[str, str]], tuple[int, str]] = _run


def run_queries(
    queries: Mapping[str, str],
    *,
    target: str = "fabric",
    env: Mapping[str, str] | None = None,
) -> dict[str, list[list[str | None]]]:
    """Run every named SQL statement in one dbt call and return {name: rows}.

    Each value in a row is text, and a NULL is None. Keep `%` out of the SQL: the Spark adapter
    applies `%` formatting only when parameters are passed, and none are.
    """
    for name, sql in queries.items():
        if not QUERY_NAME_RE.fullmatch(name):
            raise FabricQueryError(f"query name {name!r} must match {QUERY_NAME_RE.pattern}")
        if "%" in sql:
            raise FabricQueryError(f"query {name!r} contains a percent sign, which is not allowed")

    root = repo_root()
    command = [
        sys.executable,
        "-m",
        "dbt.cli.main",
        "run-operation",
        "starter.query_json",
        "--args",
        json.dumps({"queries": dict(queries)}),
        "--target",
        target,
        "--profiles-dir",
        ".",
        "--project-dir",
        ".",
    ]
    exit_code, output = RUNNER(command, root, {**os.environ, **(env or {})})
    if exit_code != 0:
        raise FabricQueryError(
            f"dbt run-operation failed with exit code {exit_code}; "
            "rerun the same dbt command by hand to read its log"
        )
    results = parse_output(output)
    for name in queries:
        if name not in results:
            raise FabricQueryError(f"query {name!r} is missing from the dbt output")
    return {name: results[name] for name in queries}

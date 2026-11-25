"""`uv run --locked --extra fabric build-fabric`: build the project on a Fabric Lakehouse (D-21).

`build-fabric` (or `make build-fabric`) runs dbt against a Fabric Lakehouse from this machine or CI
through dbt-fabricspark. It needs `az login` (FABRIC_AUTHENTICATION defaults to CLI), the three
required variables and one schema-enabled Lakehouse:

    FABRIC_WORKSPACE_ID    the workspace id, a UUID
    FABRIC_LAKEHOUSE_ID    the Lakehouse id, a UUID
    FABRIC_LAKEHOUSE       the Lakehouse name

It strips every FABRIC_* value, checks the environment first and names every problem before any
dbt command starts, then runs `dbt deps` and `dbt build --target fabric --profiles-dir .
--project-dir .` from the repo root and stops at the first failure. The messages name variables
only, never their values. DuckDB stays the default everywhere else, and Fabric is compile-checked
only until Phase 5 verifies it live.

`--selector NAME` builds with a named selector from selectors.yml (`fabric_fixture` for the
synthetic fixture). Without it the build leaves out every declared known limit and its descendants
(KNOWN_LIMIT_EXCLUDE); the selector already does that, so the two flags are never passed together.

`--summary-file FILE` (CI-04, D-16) appends a Markdown block to FILE after the run, pass or fail:
the status counts per resource type from target/run_results.json, each node whose status is not
one of the record's OK statuses, and the exit status. It first removes a stale run_results.json, so
a build that wrote none is never summarised from an older run. It never changes the exit status,
and it writes node names and counts only, never a FABRIC_* value.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
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

# A selector name as selectors.yml writes it.
SELECTOR_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# A node carries this tag only when it is a declared known limit (an adapter, runtime or upstream
# bug with no workaround, D-14). The tag is set in dbt_project.yml by the node's path, together
# with `meta.fabric_known_limit` holding `issue` (an https link to the upstream GitHub issue) and
# `reason` (one line). selectors.yml, build-fabric and the Fabric demo exclude the tag together
# with its descendants, because a node downstream of an excluded one cannot build without it, and
# the verification record lists each such node with the descendants excluded with it.
KNOWN_LIMIT_TAG = "fabric_known_limit"
KNOWN_LIMIT_EXCLUDE = "tag:" + KNOWN_LIMIT_TAG + "+"
ISSUE_URL_RE = re.compile(r"^https://github\.com/[A-Za-z0-9-]+/[A-Za-z0-9._-]+/issues/[0-9]+$")
AUTHENTICATION_PROBLEM = (
    "FABRIC_AUTHENTICATION must be CLI: the committed profile has no service-principal fields; "
    "sign in with az login, or azure/login in CI"
)

BUILD_FABRIC_COMMAND = "uv run --locked --extra fabric build-fabric"
SUMMARY_HEADING = "### build-fabric summary"
# Where `dbt build` from the repo root writes its run results. A module-level callable so a test
# never touches the checkout's own target/run_results.json.
RUN_RESULTS_PATH: Callable[[Path], Path] = lambda root: root / "target" / "run_results.json"
# Where a run stopped, for the summary: before dbt started, or at one of the two dbt steps.
STOPPED_BEFORE_DBT = "checks"
ADAPTER_HINT = (
    "The Fabric adapter (dbt-fabricspark) is not installed, so build-fabric cannot run. "
    f"Run it with its extra: `{BUILD_FABRIC_COMMAND}`."
)


def fabric_adapter_installed() -> bool:
    """Return True when the dbt Fabric adapter can be imported."""
    return importlib.util.find_spec("dbt.adapters.fabricspark") is not None


def normalised_env(environ: Mapping[str, str]) -> dict[str, str]:
    """Return a copy of `environ` with every FABRIC_* value stripped of surrounding whitespace.

    The check and dbt then see the same values, so a padded id that passes the check is not
    rejected by the adapter (WR-06).
    """
    return {
        key: value.strip() if key.startswith("FABRIC_") else value for key, value in environ.items()
    }


def check_environment(env: Mapping[str, str]) -> list[str]:
    """Return what is wrong with the Fabric variables, naming variables and never their values."""
    problems = [f"{name} is not set" for name in REQUIRED_ENV if not env.get(name, "").strip()]
    problems += [
        f"{name} is not a UUID"
        for name in UUID_ENV
        if env.get(name, "").strip() and not UUID_RE.match(env[name].strip())
    ]
    if "FABRIC_SCHEMA" in env and not env["FABRIC_SCHEMA"].strip():
        problems.append(f"FABRIC_SCHEMA is set but empty (unset it to use {DEFAULT_SCHEMA})")
    if "FABRIC_AUTHENTICATION" in env and env["FABRIC_AUTHENTICATION"].strip().lower() != "cli":
        problems.append(AUTHENTICATION_PROBLEM)
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
    """Run one command from the repo root, streaming its output. Return its exit code.

    Output goes straight to this process's stdout. When a caller has redirected `sys.stdout` to
    read a command's output (declared_known_limits), it is copied there instead.
    """
    if sys.stdout is sys.__stdout__:
        return subprocess.run(command, cwd=root, env=env, check=False).returncode
    result = subprocess.run(
        command, cwd=root, env=env, check=False, stdout=subprocess.PIPE, text=True
    )
    sys.stdout.write(result.stdout)
    return result.returncode


# A module-level callable so a test can replace it. It takes the command, the working folder and
# the environment, and returns an exit code.
RUNNER: Callable[[list[str], Path, dict[str, str]], int] = _run


def declared_known_limits(root: Path) -> dict[str, dict[str, object]]:
    """Return every node tagged KNOWN_LIMIT_TAG as {unique_id: {resource_type, issue, reason, descendants}}.

    One offline `dbt ls` on the default DuckDB target (no Fabric variable needed) lists each tagged
    node with everything downstream of it. `descendants` is the sorted set of nodes that depend on
    a declared node directly or transitively. A tagged node whose meta lacks an https GitHub issue
    link or a one-line reason raises ValueError naming the node, never the value.
    """
    command = dbt_command(
        "--quiet",
        "ls",
        "--select",
        KNOWN_LIMIT_EXCLUDE,
        "--resource-type",
        "all",
        "--output",
        "json",
        "--output-keys",
        "unique_id",
        "resource_type",
        "tags",
        "meta",
        "depends_on",
    )
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        code = RUNNER(command, root, dict(os.environ))
    if code != 0:
        raise RuntimeError(f"dbt ls for the declared known limits failed (exit {code})")
    nodes: dict[str, dict[str, object]] = {}
    for line in captured.getvalue().splitlines():
        if line.startswith("{"):
            node = json.loads(line)
            nodes[node["unique_id"]] = node

    children: dict[str, set[str]] = {unique_id: set() for unique_id in nodes}
    for unique_id, node in nodes.items():
        for parent in (node.get("depends_on") or {}).get("nodes", []):
            if parent in children:
                children[parent].add(unique_id)

    limits: dict[str, dict[str, object]] = {}
    for unique_id, node in nodes.items():
        if KNOWN_LIMIT_TAG not in (node.get("tags") or []):
            continue
        declared = (node.get("meta") or {}).get(KNOWN_LIMIT_TAG)
        declared = declared if isinstance(declared, dict) else {}
        issue = declared.get("issue")
        reason = declared.get("reason")
        if not isinstance(issue, str) or not ISSUE_URL_RE.match(issue):
            raise ValueError(
                f"{unique_id} is tagged {KNOWN_LIMIT_TAG} but meta.{KNOWN_LIMIT_TAG}.issue is not "
                "an https GitHub issue link"
            )
        if not isinstance(reason, str) or not reason.strip() or len(reason.splitlines()) != 1:
            raise ValueError(
                f"{unique_id} is tagged {KNOWN_LIMIT_TAG} but meta.{KNOWN_LIMIT_TAG}.reason is "
                "not a one-line reason"
            )
        seen: set[str] = set()
        pending = list(children[unique_id])
        while pending:
            current = pending.pop()
            if current not in seen:
                seen.add(current)
                pending.extend(children[current])
        limits[unique_id] = {
            "resource_type": node["resource_type"],
            "issue": issue,
            "reason": reason.strip(),
            "descendants": sorted(seen),
        }
    return limits


def write_build_summary(
    path: Path, run_results: Path, status: int, failed_at: str | None = None
) -> None:
    """Append the build's Markdown block to `path`: counts, the nodes that failed, the exit status.

    The file is opened for appending, never truncated, so a CI job summary keeps what earlier
    steps wrote. Counts are per resource type from `run_results`; every node whose status is
    outside verify_fabric.NODE_OK_STATUSES is listed as `<status> <unique_id>`, so a warn node
    (the undeclared_columns test) is counted and never listed as failing. A file that cannot be
    written gives one line on stderr and nothing else: the exit status is never changed. Only node
    names, statuses and counts are written, never an environment value.
    """
    # Imported here because verify_fabric imports build_fabric at module level.
    from starter_cli.verify_fabric import NODE_OK_STATUSES, run_results_nodes

    try:
        _, nodes = run_results_nodes(run_results)
    except (OSError, ValueError, KeyError):
        nodes = []
        unreadable = True
    else:
        unreadable = False

    lines: list[str] = []
    if nodes:
        by_type: dict[str, dict[str, int]] = {}
        for node in nodes:
            counts = by_type.setdefault(node["resource_type"], {})
            counts[node["status"]] = counts.get(node["status"], 0) + 1
        for resource_type, counts in sorted(by_type.items()):
            parts = ", ".join(f"{name} {counts[name]}" for name in sorted(counts))
            lines.append(f"{resource_type}: {parts}")
        lines += [
            f"{node['status']} {node['unique_id']}"
            for node in nodes
            if node["status"] not in NODE_OK_STATUSES
        ]
        lines.append(f"build-fabric exit status {status}")
    elif failed_at == STOPPED_BEFORE_DBT:
        lines.append(f"build-fabric stopped before dbt ran (exit {status})")
    elif failed_at:
        lines.append(f"build-fabric failed at: {failed_at} (exit {status})")
    else:
        lines.append(
            "build-fabric could not read run_results.json"
            if unreadable
            else "build-fabric wrote no run_results.json"
        )
        lines.append(f"build-fabric exit status {status}")
    block = [SUMMARY_HEADING, "", "```text", *lines, "```", ""]
    try:
        with Path(path).open("a", encoding="utf-8") as handle:
            handle.write("\n".join(block) + "\n")
    except OSError as exc:
        print(f"build-fabric: could not write the summary file {path}: {exc}", file=sys.stderr)


def _selector_name(value: str) -> str:
    if not SELECTOR_RE.match(value):
        raise argparse.ArgumentTypeError("a selector name is lower-case letters, digits and _")
    return value


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
    parser.add_argument(
        "--selector",
        metavar="NAME",
        type=_selector_name,
        help=(
            "build with this selector from selectors.yml (fabric_fixture for the synthetic "
            "fixture); without it the build leaves out every declared known limit and its "
            f"descendants ({KNOWN_LIMIT_EXCLUDE})"
        ),
    )
    parser.add_argument(
        "--summary-file",
        metavar="FILE",
        type=Path,
        help=(
            "append a Markdown summary of the build to FILE (status counts per resource type, "
            "the nodes that did not pass and the exit status), also when the build fails; the "
            "exit status is unchanged"
        ),
    )
    args = parser.parse_args(argv)
    # Resolved now: the run changes into the repo root, and a relative path means the caller's.
    summary_file = args.summary_file.absolute() if args.summary_file else None
    code, failed_at = _build(args.selector, clear_results=summary_file is not None)
    if summary_file is not None:
        write_build_summary(summary_file, RUN_RESULTS_PATH(repo_root()), code, failed_at)
    return code


def _build(selector: str | None, *, clear_results: bool) -> tuple[int, str | None]:
    """Check the environment, then run deps and build. Return (exit code, where it stopped).

    The second value is None on success, STOPPED_BEFORE_DBT when a check failed, else the failing
    step's name.
    """
    if not fabric_adapter_installed():
        print(ADAPTER_HINT, file=sys.stderr, flush=True)
        return 2, STOPPED_BEFORE_DBT

    environment = {**os.environ, **normalised_env(os.environ)}
    problems = check_environment(environment)
    for problem in problems:
        print(f"build-fabric: {problem}", file=sys.stderr, flush=True)
    if problems:
        return 2, STOPPED_BEFORE_DBT

    root = repo_root()
    os.chdir(root)  # profiles.yml and every relative path resolve against the repo root
    if clear_results:
        RUN_RESULTS_PATH(root).unlink(missing_ok=True)
    selection = ["--selector", selector] if selector else ["--exclude", KNOWN_LIMIT_EXCLUDE]
    steps = (
        ("deps", dbt_command("deps")),
        ("build", [*dbt_command("build", "--target", "fabric"), *selection]),
    )
    for step, command in steps:
        print(f"build-fabric: {step}", flush=True)
        code = RUNNER(command, root, environment)
        if code != 0:
            print(f"build-fabric failed at: {step} (exit {code})", flush=True)
            return code, step
    print("build-fabric: all steps passed", flush=True)
    return 0, None


if __name__ == "__main__":
    sys.exit(main())

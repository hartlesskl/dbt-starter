"""`uv run demo`: land every source file, run dbt on DuckDB, then print a count per layer.

Order matters. The loader runs in this process and closes its DuckDB connection first. dbt
then runs as separate processes, never in-process: an in-process dbt run leaves DuckDB's
connection cached, and the printer's read-only open of the same file would then fail. The
printer opens the file only after dbt has exited.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

import duckdb

from starter_cli.landing import DownloadError, LandingError, load_entity
from starter_cli.project import discover_sources, duckdb_path, repo_root

RELATION_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
FABRIC_LABEL = "Fabric: compile-checked only"


def report_lines(con: duckdb.DuckDBPyConnection, relations: list[tuple[str, str]]) -> list[str]:
    """Return the demo's closing table: a header, one row count per relation, the Fabric label.

    Layers are printed in the order given. A relation with 0 rows is still printed. Relation
    names are checked against a strict pattern before they reach the count query.
    """
    lines = [f"{'layer':<11} {'relation':<34} {'rows':>6}"]
    for label, relation in relations:
        if not RELATION_RE.match(relation):
            raise ValueError(f"relation {relation!r} must match {RELATION_RE.pattern}")
        row = con.execute(f"select count(*) from {relation}").fetchone()
        lines.append(f"{label:<11} {relation:<34} {int(row[0]):>6}")
    lines.append(FABRIC_LABEL)
    return lines


def _run_dbt(step: str, *args: str) -> int:
    """Run one dbt command as a subprocess from the repo root. Returns its exit code."""
    command = [
        sys.executable,
        "-m",
        "dbt.cli.main",
        step,
        *args,
        "--profiles-dir",
        ".",
        "--project-dir",
        ".",
    ]
    print(f"\n== dbt {step} ==", flush=True)
    return subprocess.run(command, check=False).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="demo",
        description="Land the example data, build every layer with dbt on DuckDB, print counts.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="download the source files again instead of using the cached copies",
    )
    args = parser.parse_args(argv)

    root = repo_root()
    os.chdir(root)  # the DuckDB path and profiles.yml both resolve against the working directory
    (root / "data").mkdir(exist_ok=True)

    sources = discover_sources()
    for source in sorted(sources):
        module = sources[source]
        for entity in sorted(module.ENTITIES):
            spec = module.ENTITIES[entity]
            try:
                count = load_entity(spec, root, refresh=args.refresh)
            except (DownloadError, LandingError) as exc:
                print(str(exc), file=sys.stderr)
                return 2
            print(f"landed {count} rows into landing.{spec.table}", flush=True)

    for step, extra in (("deps", ()), ("build", ()), ("docs", ("generate",))):
        label = "docs generate" if step == "docs" else step
        code = _run_dbt(step, *extra)
        if code != 0:
            print(f"demo failed at: dbt {label} (exit {code})", file=sys.stderr)
            return code

    relations: list[tuple[str, str]] = []
    for source in sorted(sources):
        relations.extend(sources[source].REPORT_RELATIONS)

    print(flush=True)
    con = duckdb.connect(str(duckdb_path(root)), read_only=True)
    try:
        lines = report_lines(con, relations)
    finally:
        con.close()
    print("\n".join(lines), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

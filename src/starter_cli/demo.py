"""`uv run demo`: land every source file, run dbt on DuckDB, then print a count per layer.

Every entity is loaded before any dbt step, so a download that fails after its retries stops
the demo with a message and exit code 2 instead of building on a partial set of months.

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
from pathlib import Path

import duckdb

from starter_cli.landing import DownloadError, LandingError, load_entity
from starter_cli.load import months_argument
from starter_cli.project import discover_sources, duckdb_path, repo_root

RELATION_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
FABRIC_LABEL = "Fabric: compile-checked only"


def _checked(relation: str) -> str:
    if not RELATION_RE.match(relation):
        raise ValueError(f"relation {relation!r} must match {RELATION_RE.pattern}")
    return relation


def report_lines(
    con: duckdb.DuckDBPyConnection,
    relations: list[tuple[str, str]],
    warnings: list[str] | None = None,
) -> list[str]:
    """Return the demo's closing table: a header, one row count per relation, the Fabric label.

    Relations are printed in the order given, and a relation with 0 rows is still printed. After
    them comes one `warnings` line per relation in `warnings`: the number of its rows whose
    `dq_warning_count` is above 0, which stay in silver. Relation names are checked against a
    strict pattern before they reach a query.
    """
    lines = [f"{'layer':<11} {'relation':<34} {'rows':>9}"]
    for label, relation in relations:
        row = con.execute(f"select count(*) from {_checked(relation)}").fetchone()
        lines.append(f"{label:<11} {relation:<34} {int(row[0]):>9}")
    for relation in warnings or []:
        row = con.execute(
            f"select count(*) from {_checked(relation)} where dq_warning_count > 0"
        ).fetchone()
        lines.append(f"{'warnings':<11} {relation:<34} {int(row[0]):>9}")
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
    parser.add_argument(
        "--months",
        type=months_argument,
        help="months of each monthly source to land: a list (2024-01,2024-03), a range "
        "(2024-01..2024-03) or both. Default: each source's default month.",
    )
    parser.add_argument(
        "--from-dir",
        type=Path,
        metavar="DIR",
        help="land the files in DIR instead of downloading them (see README.md#troubleshooting)",
    )
    parser.add_argument(
        "--allow-large",
        action="store_true",
        help="allow downloading months the source marks as large (hundreds of MB each)",
    )
    args = parser.parse_args(argv)
    from_dir = None
    if args.from_dir is not None:
        # Made absolute before the working directory changes to the repo root below.
        from_dir = args.from_dir.expanduser().absolute()
        if not from_dir.is_dir():
            parser.error(f"argument --from-dir: {from_dir} is not a directory")

    root = repo_root()
    os.chdir(root)  # the DuckDB path and profiles.yml both resolve against the working directory
    (root / "data").mkdir(exist_ok=True)

    sources = discover_sources()
    for source in sorted(sources):
        module = sources[source]
        for entity in sorted(module.ENTITIES):
            spec = module.ENTITIES[entity]
            try:
                count = load_entity(
                    spec,
                    root,
                    refresh=args.refresh,
                    months=args.months if spec.is_monthly else None,
                    from_dir=from_dir,
                    allow_large=args.allow_large,
                )
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
    warnings: list[str] = []
    for source in sorted(sources):
        module = sources[source]
        relations.extend(module.REPORT_RELATIONS)
        warnings.extend(getattr(module, "REPORT_WARNINGS", []))

    print(flush=True)
    con = duckdb.connect(str(duckdb_path(root)), read_only=True)
    try:
        lines = report_lines(con, relations, warnings)
    finally:
        con.close()
    print("\n".join(lines), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

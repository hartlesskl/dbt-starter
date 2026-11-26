"""`uv run load <source> <entity> [--months ...] [--from-dir DIR]`: land one entity.

The source and entity are whitelisted against the discovered source specs, and every month
must match `YYYY-MM`, all before any download or database access. A typed value therefore
reaches a URL, a file path or SQL only through a validated `EntitySpec`.
"""

from __future__ import annotations

import argparse
import os
import sys
import types
from pathlib import Path

from starter_cli import fabric_sink
from starter_cli.build_fabric import check_environment
from starter_cli.landing import DownloadError, LandingError, load_entity, parse_months
from starter_cli.project import discover_sources, fabric_default_months, repo_root


def months_argument(text: str) -> tuple[str, ...]:
    """Argparse `type` for `--months`: a bad month exits 2 with the message naming it."""
    try:
        return parse_months(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def build_parser(sources: dict[str, types.ModuleType]) -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="load",
        description=(
            "Land one source entity, all text, into the landing schema. Files are downloaded "
            "once into data/raw/, or read from a folder you already have with --from-dir."
        ),
    )
    parser.add_argument("source", choices=sorted(sources), help="the data source to load")
    parser.add_argument("entity", help="the entity of that source to load")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="download the file again instead of using the cached copy, and print what changed",
    )
    parser.add_argument(
        "--months",
        type=months_argument,
        help="months of a monthly entity to land: a list (2024-01,2024-03), a range "
        "(2024-01..2024-03) or both. Default: the entity's default months; with --target "
        "fabric, the months its source declares in FABRIC_DEFAULT_MONTHS, else the entity's "
        "defaults.",
    )
    parser.add_argument(
        "--from-dir",
        type=Path,
        metavar="DIR",
        help="land files you already have from DIR instead of downloading; nothing is copied "
        "into data/raw/ and no network is used",
    )
    parser.add_argument(
        "--allow-large",
        action="store_true",
        help="allow downloading months the entity marks as large (hundreds of MB each)",
    )
    parser.add_argument(
        "--target",
        choices=("duckdb", "fabric"),
        default="duckdb",
        help="where to land: the local DuckDB database (default) or the Fabric Lakehouse "
        "named by FABRIC_WORKSPACE_ID and FABRIC_LAKEHOUSE_ID (needs `az login` and the "
        "fabric extra)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    sources = discover_sources()
    parser = build_parser(sources)
    args = parser.parse_args(argv)

    entities = sources[args.source].ENTITIES
    if args.entity not in entities:
        parser.error(
            f"argument entity: invalid choice: {args.entity!r} for source {args.source!r} "
            f"(choose from {', '.join(repr(name) for name in sorted(entities))})"
        )
    spec = entities[args.entity]
    if args.months is not None and not spec.is_monthly:
        parser.error(f"argument --months: {spec.table} has a single file, so it takes no months")
    from_dir = None
    if args.from_dir is not None:
        # Made absolute here, before the working directory changes to the repo root below.
        from_dir = args.from_dir.expanduser().absolute()
        if not from_dir.is_dir():
            parser.error(f"argument --from-dir: {from_dir} is not a directory")

    extra: dict = {}  # only `sink`, and only for the Lakehouse: the default call is unchanged
    months = args.months
    if args.target == "fabric":
        if not fabric_sink.sink_installed():
            print(fabric_sink.SINK_HINT, file=sys.stderr)
            return 2
        problems = check_environment(os.environ)
        for problem in problems:
            print(f"load: {problem}", file=sys.stderr)
        if problems:
            return 2
        extra["sink"] = fabric_sink.LakehouseSink(
            os.environ["FABRIC_WORKSPACE_ID"].strip(), os.environ["FABRIC_LAKEHOUSE_ID"].strip()
        )
        if months is None and from_dir is None and spec.is_monthly:
            # None (nothing declared) leaves the loader on the spec's own default months.
            months = fabric_default_months(sources[args.source])

    root = repo_root()
    os.chdir(root)  # the DuckDB path and profiles.yml both resolve against the working directory
    (root / "data").mkdir(exist_ok=True)

    try:
        count = load_entity(
            spec,
            root,
            refresh=args.refresh,
            months=months,
            from_dir=from_dir,
            allow_large=args.allow_large,
            **extra,
        )
    except (DownloadError, LandingError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"landed {count} rows into landing.{spec.table}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

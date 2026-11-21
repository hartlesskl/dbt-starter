"""`uv run load <source> <entity> [--refresh]`: download one entity once and land it.

Both arguments are whitelisted against the discovered source specs before any download or
database access, so a typed value never reaches a file path or SQL except through a validated
`EntitySpec`.
"""

from __future__ import annotations

import argparse
import os
import sys
import types

from starter_cli.landing import DownloadError, LandingError, load_entity
from starter_cli.project import discover_sources, repo_root


def build_parser(sources: dict[str, types.ModuleType]) -> argparse.ArgumentParser:
    """Build the argument parser. Later phases add options here (for example a month range)."""
    parser = argparse.ArgumentParser(
        prog="load",
        description="Download one source entity once and land it, all text, into the landing schema.",
    )
    parser.add_argument("source", choices=sorted(sources), help="the data source to load")
    parser.add_argument("entity", help="the entity of that source to load")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="download the file again instead of using the cached copy",
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

    root = repo_root()
    os.chdir(root)  # the DuckDB path and profiles.yml both resolve against the working directory
    (root / "data").mkdir(exist_ok=True)

    try:
        count = load_entity(spec, root, refresh=args.refresh)
    except (DownloadError, LandingError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"landed {count} rows into landing.{spec.table}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

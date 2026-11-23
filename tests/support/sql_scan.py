"""Scan compiled Fabric SQL for constructs outside the portable subset.

The scan reads the SQL dbt compiles for the `fabric` target (Spark SQL on dbt-fabricspark) and
catches what DuckDB accepts but Spark rejects. The gate's DuckDB build catches the reverse, so
between them a model is checked by both engines. The scan parses with sqlglot's Spark dialect
and walks the syntax tree. It reports:

- QUALIFY
- a star modifier (EXCLUDE, REPLACE, RENAME), UNION BY NAME and DISTINCT ON, all DuckDB-only
- any function call sqlglot does not know, because a DuckDB-only function would be one
- banned data types: TIMESTAMP_NTZ (the SQL analytics endpoint does not support it) and the
  DuckDB-only types (HUGEINT, UBIGINT, UUID, TIME, INTERVAL, ENUM, BIT and the unsigned ints)
- SQL that does not parse

Nested WITH, mixed-case identifiers and one-argument CONCAT are fine on Spark, so none is
reported. TEXT is not banned either: Spark's `string` parses as TEXT.

`parse_one` alone rejects none of these constructs, which is why the scan walks the tree.
"""

from __future__ import annotations

from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

_TYPE = exp.DataType.Type

BANNED_TYPES: frozenset[exp.DataType.Type] = frozenset(
    {
        _TYPE.TIMESTAMPNTZ,
        _TYPE.INT128,
        _TYPE.UINT128,
        _TYPE.UBIGINT,
        _TYPE.UINT,
        _TYPE.USMALLINT,
        _TYPE.UTINYINT,
        _TYPE.UUID,
        _TYPE.TIME,
        _TYPE.BIT,
        _TYPE.INTERVAL,
        _TYPE.ENUM,
    }
)


def scan(sql: str) -> list[str]:
    """Return a human-readable problem for every banned construct in `sql`."""
    try:
        trees = sqlglot.parse(sql, read="spark")
    except SqlglotError as error:
        return [f"unparseable SQL: {error}"]

    problems: list[str] = []
    for tree in trees:
        if tree is None:
            continue
        problems.extend(_scan_tree(tree))
    return problems


def _scan_tree(tree: exp.Expression) -> list[str]:
    problems: list[str] = []

    if tree.find(exp.Qualify):
        problems.append("QUALIFY is not supported on Spark")

    for star in tree.find_all(exp.Star):
        if star.args.get("except_") or star.args.get("replace") or star.args.get("rename"):
            problems.append("star modifier (EXCLUDE/REPLACE/RENAME) is DuckDB-only")

    for union in tree.find_all(exp.Union):
        if union.args.get("by_name"):
            problems.append("UNION BY NAME is DuckDB-only")

    for distinct in tree.find_all(exp.Distinct):
        if distinct.args.get("on"):
            problems.append("DISTINCT ON is DuckDB-only")

    for call in tree.find_all(exp.Anonymous):
        problems.append(f"function {call.name} is not in the shared subset")

    for datatype in tree.find_all(exp.DataType):
        if datatype.this in BANNED_TYPES:
            problems.append(f"banned data type {datatype.this.name}")

    return problems


def scan_compiled_dir(root: Path) -> dict[str, list[str]]:
    """Scan every `.sql` file under `root`. Return only files with problems, keyed by
    path relative to `root`."""
    result: dict[str, list[str]] = {}
    for path in sorted(Path(root).rglob("*.sql")):
        problems = scan(path.read_text())
        if problems:
            result[str(path.relative_to(root))] = problems
    return result

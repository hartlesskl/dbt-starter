"""Scan compiled Fabric SQL for constructs outside the portable subset.

The scan parses with sqlglot's T-SQL dialect and walks the syntax tree. It reports:

- a WITH clause nested inside a select, a subquery, another CTE or a union
- QUALIFY
- banned data types (NVARCHAR, NCHAR, DATETIME, SMALLDATETIME, MONEY, SMALLMONEY, TEXT,
  TINYINT, BOOLEAN, BIT)
- identifiers with upper-case letters, quoted or not, except the catalog part of a
  three-part table name, because the Fabric database name is set by the user
- CONCAT with fewer than two arguments, and CONCAT_WS with fewer than two values after
  the separator, because T-SQL rejects both
- SQL that does not parse

`parse_one` alone rejects none of these constructs, which is why the scan walks the tree.
"""

from __future__ import annotations

from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

_TYPE = exp.DataType.Type

# TINYINT parses as UTINYINT in the T-SQL dialect, so both are listed.
BANNED_TYPES: frozenset[exp.DataType.Type] = frozenset(
    {
        _TYPE.NVARCHAR,
        _TYPE.NCHAR,
        _TYPE.DATETIME,
        _TYPE.SMALLDATETIME,
        _TYPE.MONEY,
        _TYPE.SMALLMONEY,
        _TYPE.TEXT,
        _TYPE.TINYINT,
        _TYPE.UTINYINT,
        _TYPE.BOOLEAN,
        _TYPE.BIT,
    }
)


def scan(sql: str) -> list[str]:
    """Return a human-readable problem for every banned construct in `sql`."""
    try:
        trees = sqlglot.parse(sql, read="tsql")
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

    for clause in tree.find_all(exp.With):
        parent = clause.parent
        if parent is not None and parent.find_ancestor(
            exp.Select, exp.Subquery, exp.CTE, exp.Union
        ):
            problems.append("nested WITH (a CTE inside a CTE or a subquery)")

    if tree.find(exp.Qualify):
        problems.append("QUALIFY is not supported on Fabric")

    for datatype in tree.find_all(exp.DataType):
        if datatype.this in BANNED_TYPES:
            problems.append(f"banned data type {datatype.this.name}")

    for ident in tree.find_all(exp.Identifier):
        name = ident.name
        if name == name.lower():
            continue
        parent = ident.parent
        if isinstance(parent, exp.Table) and ident is parent.args.get("catalog"):
            continue
        problems.append(f"mixed-case identifier {name!r}")

    for node in tree.find_all(exp.Concat):
        if len(node.expressions) < 2:
            problems.append("CONCAT with fewer than two arguments")

    for node in tree.find_all(exp.ConcatWs):
        # the first argument is the separator
        if len(node.expressions) < 3:
            problems.append("CONCAT_WS with fewer than two value arguments")

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

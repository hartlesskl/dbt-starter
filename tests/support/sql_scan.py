"""Scan compiled Fabric SQL for constructs outside the portable subset.

The scan reads the SQL dbt compiles for the `fabric` target (Spark SQL on dbt-fabricspark) and
catches DuckDB-only syntax before a live Fabric run does. It is one half of the check: the
gate's DuckDB build runs every model on DuckDB, and this scan reads what the Spark bodies
rendered, so DuckDB-only syntax and Spark-only syntax are each caught by one half. Neither half
runs Spark; the live Fabric builds do. The scan tokenizes and parses with sqlglot's Spark
dialect and reports:

- the `::` cast operator (a token check, because sqlglot parses it into a plain cast)
- QUALIFY
- a star modifier (EXCLUDE, REPLACE, RENAME), UNION BY NAME and DISTINCT ON
- ASOF JOIN, USING SAMPLE, LIMIT n PERCENT, the statement form of PIVOT, and datediff with the
  unit as its first argument
- any function outside ALLOWED_FUNCTIONS, the names the shims and models are known to emit, so
  a new function fails until a person has checked that Spark has it
- banned data types: TIMESTAMP_NTZ (the SQL analytics endpoint does not support it) and the
  DuckDB-only types (HUGEINT, UBIGINT, UUID, TIME, INTERVAL, ENUM, BIT and the unsigned ints)
- a reference to information_schema, which a Fabric Lakehouse does not have
- SQL that does not parse

Nested WITH, mixed-case identifiers, one-argument CONCAT, a from-first select, a PIVOT clause
and TABLESAMPLE are valid Spark, so none is reported. TEXT is not banned either: Spark's
`string` parses as TEXT.

`parse_one` alone rejects none of these constructs, which is why the scan walks the tree.
"""

from __future__ import annotations

from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.tokens import TokenType

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

# The functions a compiled Fabric model may call, as sqlglot names them (a call it does not
# know is named as written, in lower case). sqlglot normalizes some Spark calls, so Spark's
# `date_add` is `ts_or_ds_add`, `date_trunc` is `timestamp_trunc` and `to_date` is
# `ts_or_ds_to_date`. The list is the names found in the offline Fabric compile of the shims and
# models, each checked against Spark, plus `cast`, `concat` and `date_part`, which Spark has. Add a name only after
# checking that Spark has the function, and that it means what DuckDB's does.
ALLOWED_FUNCTIONS: frozenset[str] = frozenset(
    {
        "abs",
        "cast",
        "chr",
        "coalesce",
        "concat",
        "concat_ws",
        "count",
        "date_part",
        "datediff",
        "day",
        "floor",
        "if",
        "max",
        "md5",
        "min",
        "month",
        "nullif",
        "quarter",
        "regexp_like",
        "row_number",
        "substring",
        "sum",
        "timestamp_trunc",
        "timestampdiff",
        "translate",
        "trim",
        "try_cast",
        "ts_or_ds_add",
        "ts_or_ds_to_date",
        "year",
    }
)


def scan(sql: str) -> list[str]:
    """Return a human-readable problem for every banned construct in `sql`."""
    try:
        tokens = sqlglot.tokenize(sql, read="spark")
        trees = sqlglot.parse(sql, read="spark")
    except SqlglotError as error:
        return [f"unparseable SQL: {error}"]

    problems = _scan_tokens(tokens)
    for tree in trees:
        if tree is None:
            continue
        problems.extend(_scan_tree(tree))
    return problems


def _scan_tokens(tokens: list) -> list[str]:
    """Report the DuckDB syntax that the parse tree cannot show."""
    problems: list[str] = []
    if any(token.token_type == TokenType.DCOLON for token in tokens):
        problems.append("the :: cast operator is DuckDB syntax; use cast(x as type)")
    for token, following in zip(tokens, tokens[1:], strict=False):
        if token.token_type == TokenType.USING and following.text.lower() == "sample":
            problems.append("USING SAMPLE is DuckDB-only; use TABLESAMPLE")
    return problems


def _function_name(call: exp.Func) -> str:
    if isinstance(call, exp.Anonymous):
        return str(call.name).lower()
    return call.sql_name().lower()


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

    for join in tree.find_all(exp.Join):
        if str(join.args.get("method") or "").upper() == "ASOF":
            problems.append("ASOF JOIN is DuckDB-only")

    for limit in tree.find_all(exp.Limit):
        options = limit.args.get("limit_options")
        if options is not None and options.args.get("percent"):
            problems.append("LIMIT n PERCENT is DuckDB-only")

    for pivot in tree.find_all(exp.Pivot):
        if pivot.args.get("using"):
            problems.append("the statement form of PIVOT (PIVOT t ON ... USING ...) is DuckDB-only")

    for call in tree.find_all(exp.Func):
        # Operators and CASE are Func nodes in sqlglot; they are not function calls.
        if isinstance(call, (exp.Binary, exp.Unary, exp.Case)):
            continue
        if isinstance(call, exp.DateDiff) and call.args.get("unit") is not None:
            problems.append(
                "datediff with the unit first is DuckDB order; Spark's datediff takes (end, start)"
            )
        name = _function_name(call)
        if name not in ALLOWED_FUNCTIONS:
            problems.append(
                f"function {name} is not in the allowed functions; add it to ALLOWED_FUNCTIONS "
                "only after checking that Spark has it"
            )

    for table in tree.find_all(exp.Table):
        if table.db.lower() == "information_schema":
            problems.append("information_schema is not available on Fabric Lakehouse Spark")

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

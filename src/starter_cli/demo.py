"""`uv run demo`: land every source file, run dbt on DuckDB, then print the closing summary.

Every entity is loaded before any dbt step, so a download that fails after its retries stops
the demo with a message and exit code 2 instead of building on a partial set of months.

Order matters. The loader runs in this process and closes its DuckDB connection first. dbt
then runs as separate processes, never in-process: an in-process dbt run leaves DuckDB's
connection cached, and the printer's read-only open of the same file would then fail. The
printer opens the file only after dbt has exited.

The closing summary has these parts, in order: a row count per layer, a `warnings` line, a
`reconciliation` section, a `quarantine summary` section, a `docs` section and the Fabric
label. Every number in the reconciliation section is recomputed by the printer's own read-only
queries. A reconciliation line says OK when its two sides are equal and FAIL when they differ,
and the demo exits 1 when any line says FAIL, so those words and the exit status never disagree.

The quarantine summary prints each entity's rate, and the rate of its worst file, next to the
entity's warn and error levels, marked OK, WARN or ERROR. Those lines only report. dbt's levels
tests enforce the levels, and dbt's exit status carries their verdict: an error-level breach
fails `dbt build`, and the demo returns dbt's code. The printer reads the levels from dbt's
`manifest.json` and computes each rate with the SQL expression the levels test renders, so a
printed word matches dbt's status. The one case where the two could disagree, an ERROR line
after a build dbt passed, prints `RATE_DISAGREEMENT_NOTE` and leaves the exit status alone.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple

import duckdb

from starter_cli.landing import DownloadError, LandingError, load_entity
from starter_cli.load import months_argument
from starter_cli.project import discover_sources, duckdb_path, repo_root

RELATION_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
IDENT_RE = re.compile(r"^[a-z][a-z0-9_]*$")
FABRIC_LABEL = "Fabric: compile-checked only"
DQ_SUMMARY = "silver.dq_quarantine_summary"
NONE_REGISTERED = "  none registered"
FAIL_MESSAGE = (
    "demo: a reconciliation line shows FAIL. "
    "The dbt tests in the build output above are the enforcing check."
)
RATE_DISAGREEMENT_NOTE = (
    "note: a quarantine rate above is past its error level, "
    "but dbt's levels tests passed; dbt's result stands"
)
LEVELS_TEST = "quarantine_rate_levels"
ALL_FILES = "(all files)"

# This is `starter.safe_divide`'s DuckDB rendering (`duckdb__safe_divide` in
# packages/starter/macros/shim/misc.sql), copied character for character. The project's rule is
# that rates go through `starter.safe_divide`, never a bare division. This is the one exception:
# the printer queries DuckDB from Python, outside dbt, so it cannot call the macro and carries its
# text instead. The numerator is cast to decimal before dividing, so integers are never divided as
# integers, and a zero denominator gives NULL. The levels test renders the same text, so a printed
# rate equals the test's rate; a fixture test of the demo breach path (plan 04-06) proves that
# equality, so the copy cannot drift silently. This constant holds the only division the printer's
# SQL contains.
SAFE_DIVIDE_SQL = (
    "cast(cast({numerator} as decimal(18, 6)) / nullif({denominator}, 0) as decimal(18, 6))"
)

# One pattern per remote form that names a GitHub repository. The owner is letters, digits and
# hyphens (at most 39, not starting with a hyphen); the repository is letters, digits, `.`, `_`
# and `-`. The lazy repository group leaves a trailing `.git` to the optional suffix.
_OWNER = r"(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))"
_REPO = r"(?P<repo>[A-Za-z0-9._-]+?)"
_REMOTE_FORMS = (
    re.compile(rf"https://github\.com/{_OWNER}/{_REPO}(?:\.git)?/?"),
    re.compile(rf"git@github\.com:{_OWNER}/{_REPO}(?:\.git)?"),
    re.compile(rf"ssh://git@github\.com/{_OWNER}/{_REPO}(?:\.git)?"),
)


def _checked(relation: str) -> str:
    if not RELATION_RE.match(relation):
        raise ValueError(f"relation {relation!r} must match {RELATION_RE.pattern}")
    return relation


def _checked_ident(name: str) -> str:
    if not IDENT_RE.match(name):
        raise ValueError(f"column {name!r} must match {IDENT_RE.pattern}")
    return name


class Levels(NamedTuple):
    """One entity's quarantine rate levels, as fractions of rows (0.01 is 1%)."""

    warn_above: Decimal
    error_above: Decimal


class ScopeRate(NamedTuple):
    """The rows rejected before silver, out of all landed rows, for one file or for the entity."""

    scope: str
    rejected: int
    total: int
    rate: Decimal | None


def dbt_target_dir(root: Path) -> Path:
    """Return dbt's target folder: `DBT_TARGET_PATH` when set, else `target`, under `root`."""
    target = Path(os.environ.get("DBT_TARGET_PATH") or "target")
    return target if target.is_absolute() else Path(root) / target


def _is_level(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def read_levels(manifest_path: Path) -> dict[str, Levels]:
    """Return each entity's levels from dbt's manifest, keyed by its silver relation.

    Every `quarantine_rate_levels` test node gives one entry, keyed `schema.alias` of the silver
    model the test is attached to. Each level is quantized as `'%.6f'`, the literal the test
    renders, so the printer and the test compare against the same number. A node whose level is
    not a number (a boolean or text) or whose model is missing is skipped. A missing or unreadable
    file gives `{}`. Values from the manifest are only looked up and printed, never put in SQL.
    """
    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    nodes = manifest.get("nodes") if isinstance(manifest, dict) else None
    if not isinstance(nodes, dict):
        return {}
    levels: dict[str, Levels] = {}
    for node in nodes.values():
        if not isinstance(node, dict) or node.get("resource_type") != "test":
            continue
        metadata = node.get("test_metadata")
        if not isinstance(metadata, dict) or metadata.get("name") != LEVELS_TEST:
            continue
        kwargs = metadata.get("kwargs")
        if not isinstance(kwargs, dict):
            continue
        warn, error = kwargs.get("warn_above"), kwargs.get("error_above")
        model = nodes.get(node.get("attached_node"))
        if not (_is_level(warn) and _is_level(error) and isinstance(model, dict)):
            continue
        schema, alias = model.get("schema"), model.get("alias")
        if not (isinstance(schema, str) and isinstance(alias, str)):
            continue
        levels[f"{schema}.{alias}"] = Levels(Decimal(f"{warn:.6f}"), Decimal(f"{error:.6f}"))
    return levels


def format_level(level: Decimal) -> str:
    """Return a level as a percentage without trailing zeros: 0.001 is `0.1%`, 0.01 is `1%`."""
    text = f"{level * 100:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text}%"


def level_status(rate: Decimal | None, levels: Levels | None) -> str:
    """Return OK, WARN or ERROR for a rate, or `no levels`. A rate fires only strictly above."""
    if levels is None:
        return "no levels"
    if rate is None:
        return "OK"
    if rate > levels.error_above:
        return "ERROR"
    if rate > levels.warn_above:
        return "WARN"
    return "OK"


def scope_rates(
    con: duckdb.DuckDBPyConnection, bronze: str, silver: str
) -> tuple[ScopeRate, list[ScopeRate]]:
    """Return the entity's rate and its rate per `_source_file`, in file name order.

    The rate is bronze rows minus silver rows over bronze rows, so it counts only rows that left
    silver. A NULL file is `(none)`. The division is `SAFE_DIVIDE_SQL`, run by DuckDB, so a rate
    rounds to 6 decimals exactly as the levels test's does. An entity with no rows has a None rate
    and no files.
    """
    bronze, silver = _checked(bronze), _checked(silver)
    per_file = (
        "select b.source_file as source_file, b.total_rows as total_rows, "
        "b.total_rows - coalesce(s.kept_rows, 0) as rejected_rows "
        "from (select coalesce(_source_file, '(none)') as source_file, count(*) as total_rows "
        f"from {bronze} group by coalesce(_source_file, '(none)')) as b "
        "left join (select coalesce(_source_file, '(none)') as source_file, count(*) as kept_rows "
        f"from {silver} group by coalesce(_source_file, '(none)')) as s "
        "on b.source_file = s.source_file"
    )
    rate = SAFE_DIVIDE_SQL.format(numerator="rejected_rows", denominator="total_rows")
    rows = con.execute(
        f"select grain, scope, rejected_rows, total_rows, {rate} as quarantine_rate from ("
        "select 0 as grain, source_file as scope, rejected_rows, total_rows "
        f"from ({per_file}) as per_file "
        "union all "
        f"select 1 as grain, '{ALL_FILES}' as scope, coalesce(sum(rejected_rows), 0) as rejected_rows, "
        f"coalesce(sum(total_rows), 0) as total_rows from ({per_file}) as all_files"
        ") as scopes order by grain, scope"
    ).fetchall()
    scopes = [ScopeRate(scope, int(rej), int(tot), rt) for _g, scope, rej, tot, rt in rows]
    return scopes[-1], scopes[:-1]


def report_lines(
    con: duckdb.DuckDBPyConnection,
    relations: list[tuple[str, str]],
    warnings: list[str] | None = None,
) -> list[str]:
    """Return the demo's count table: a header, one row count per relation, the warnings lines.

    Relations are printed in the order given, and a relation with 0 rows is still printed. After
    them comes one `warnings` line per relation in `warnings`: the number of its rows whose
    `dq_warning_count` is above 0, which stay in silver. Relation names are checked against a
    strict pattern before they reach a query. The Fabric label is not part of this table: it
    comes last from `closing_lines`.
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
    return lines


def _status(left: int, right: int) -> str:
    return "OK" if left == right else "FAIL"


def _count(con: duckdb.DuckDBPyConnection, relation: str) -> int:
    return int(con.execute(f"select count(*) from {relation}").fetchone()[0])


def conservation_lines(
    con: duckdb.DuckDBPyConnection, conservation: dict[str, tuple[str, str, str]]
) -> tuple[list[str], bool]:
    """Return one `bronze N = silver N + quarantine N` line per entity, and whether all are OK.

    `conservation` maps an entity to its bronze, silver and quarantine relations. Each count is
    recomputed with a read-only `count(*)`. Every relation name is checked before any query runs.
    """
    checked = {
        entity: tuple(_checked(relation) for relation in relations)
        for entity, relations in conservation.items()
    }
    lines: list[str] = []
    all_ok = True
    for entity, (bronze, silver, quarantine) in checked.items():
        b, s, q = _count(con, bronze), _count(con, silver), _count(con, quarantine)
        status = _status(b, s + q)
        all_ok = all_ok and status == "OK"
        lines.append(f"  {entity:<14} bronze {b:,} = silver {s:,} + quarantine {q:,}  {status}")
    return lines, all_ok


def gold_lines(
    con: duckdb.DuckDBPyConnection, gold: list[tuple[str, str, str]]
) -> tuple[list[str], bool]:
    """Return one `fact N = sum(aggregate.column) N` line per entry, and whether all are OK.

    Each entry is a fact relation, an aggregate relation and the aggregate's count column. An
    empty aggregate sums to 0. Relation and column names are checked before any query runs.
    """
    checked = [
        (_checked(fact), _checked(aggregate), _checked_ident(column))
        for fact, aggregate, column in gold
    ]
    lines: list[str] = []
    all_ok = True
    for fact, aggregate, column in checked:
        f = _count(con, fact)
        a = int(con.execute(f"select coalesce(sum({column}), 0) from {aggregate}").fetchone()[0])
        status = _status(f, a)
        all_ok = all_ok and status == "OK"
        fact_table, agg_table = fact.split(".")[1], aggregate.split(".")[1]
        lines.append(
            f"  {'gold':<14} {fact_table} {f:,} = sum({agg_table}.{column}) {a:,}  {status}"
        )
    return lines, all_ok


def _rate_text(rate: Decimal | None) -> str:
    return "n/a" if rate is None else f"{rate:.3%}"


def quarantine_summary_lines(
    con: duckdb.DuckDBPyConnection,
    conservation: dict[str, tuple[str, str, str]],
    levels: dict[str, Levels],
    summary: str = DQ_SUMMARY,
    top_errors: int = 5,
    top_warnings: int = 3,
) -> list[str]:
    """Return each entity's quarantine rate against its levels, then its top error and warn codes.

    Entities come from the quarantine summary relation, in name order. An entity listed in
    `conservation` gets its rate and its worst file's rate recomputed from its bronze and silver
    relations, each marked OK, WARN or ERROR against `levels` (keyed by silver relation), or
    `no levels` when it has none. The worst file has the highest rate, ties broken by file name.
    An entity with no rows prints `n/a` and no worst-file line; an entity not in `conservation`
    keeps the summary's own numbers. Rate words only report: they set no exit status. Codes with
    0 failed rows are skipped, the order is failed rows descending then code ascending, and a
    severity with no fired code prints `none`. An empty summary prints `none registered`.
    """
    relation = _checked(summary)
    entities = con.execute(
        "select entity, max(entity_rows), max(entity_quarantined_rows), "
        f"max(entity_quarantine_rate) from {relation} group by entity order by entity"
    ).fetchall()
    if not entities:
        return [NONE_REGISTERED]
    lines: list[str] = []
    for entity, rows, quarantined, rate in entities:
        entity_levels = None
        files: list[ScopeRate] = []
        if entity in conservation:
            bronze, silver, _quarantine = conservation[entity]
            whole, files = scope_rates(con, bronze, silver)
            rejected, total, rate = whole.rejected, whole.total, whole.rate
            entity_levels = levels.get(silver)
        else:
            rejected, total = int(quarantined), int(rows)
            rate = Decimal(0) if rate is None else rate
        if entity_levels is None:
            tail = "  no levels"
        else:
            tail = (
                f"  warn {format_level(entity_levels.warn_above)}"
                f"  error {format_level(entity_levels.error_above)}"
                f"  {level_status(rate, entity_levels)}"
            )
        lines.append(
            f"  {entity:<14} quarantine rate {_rate_text(rate)} ({rejected:,} of {total:,} rows){tail}"
        )
        if files:
            worst = min(files, key=lambda f: (-(f.rate if f.rate is not None else -1), f.scope))
            status = f"  {level_status(worst.rate, entity_levels)}" if entity_levels else ""
            lines.append(
                f"    worst file of {len(files)}: {worst.scope}  {_rate_text(worst.rate)} "
                f"({worst.rejected:,} of {worst.total:,} rows){status}"
            )
        for severity, limit in (("error", top_errors), ("warn", top_warnings)):
            codes = con.execute(
                f"select rule_code, failed_rows from {relation} "
                "where entity = ? and severity = ? and failed_rows > 0 "
                f"order by failed_rows desc, rule_code asc limit {int(limit)}",
                [entity, severity],
            ).fetchall()
            if not codes:
                lines.append(f"    {severity:<6} none")
            for code, failed in codes:
                lines.append(f"    {severity:<6} {code:<40} {int(failed):>12,}")
    return lines


def pages_url(remote: str | None) -> str | None:
    """Return the GitHub Pages docs URL for a GitHub remote, or None for anything else.

    `https://github.com/<owner>/<repo>(.git)`, `git@github.com:<owner>/<repo>.git` and
    `ssh://git@github.com/<owner>/<repo>.git` map to `https://<owner>.github.io/<repo>/dbt-docs/`
    with the owner in lower case. A repository named `<owner>.github.io` is the user site, served
    at the root: `https://<owner>.github.io/dbt-docs/`. Another host, an empty or missing remote
    and a malformed owner or repository give None.
    """
    if not remote:
        return None
    for form in _REMOTE_FORMS:
        match = form.fullmatch(remote)
        if match is None:
            continue
        owner, repo = match.group("owner").lower(), match.group("repo")
        if repo in {".", ".."}:
            return None
        if repo.lower() == f"{owner}.github.io":
            return f"https://{owner}.github.io/dbt-docs/"
        return f"https://{owner}.github.io/{repo}/dbt-docs/"
    return None


def git_remote_url(root: Path) -> str | None:
    """Return `remote.origin.url` of the checkout at `root`, or None when there is none.

    git runs with an argument list, no shell and a 5 second limit. A missing git binary, a
    timeout and a non-zero exit all give None.
    """
    try:
        result = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def docs_lines(root: Path, url: str | None) -> list[str]:
    """Return the `docs` section: the offline static file, the serve command, the Pages URL.

    The static file opens without the DuckDB file, so it never competes with the next run for
    the database lock that `dbt docs serve` would hold. The Pages address is a derived address,
    not a fact: a copy of the template publishes nothing until its owner sets the repository
    variable ENABLE_PAGES and picks GitHub Actions as the Pages source, so the line says so and
    never claims the docs are already published. Both lines are left out when there is no URL.
    """
    static = Path(root).absolute() / "target" / "static_index.html"
    lines = ["docs", f"  open offline   {static}", "  or serve       uv run dbt docs serve"]
    if url:
        lines.append(f"  on Pages       {url}")
        lines.append(f"  {'':<14} once Pages is enabled with ENABLE_PAGES=true (see README)")
    return lines


def closing_lines(
    con: duckdb.DuckDBPyConnection,
    root: Path,
    relations: list[tuple[str, str]],
    warnings: list[str],
    conservation: dict[str, tuple[str, str, str]],
    gold: list[tuple[str, str, str]],
    url: str | None,
    levels: dict[str, Levels] | None = None,
) -> tuple[list[str], bool]:
    """Return the whole closing summary and whether every reconciliation line says OK.

    The flag is False only for a reconciliation or gold FAIL; a rate word in the quarantine
    summary never changes it.

    Order: the count table, `reconciliation`, `quarantine summary`, `docs`, the Fabric label.
    Sections are separated by one blank line and indented, so the count-line pattern never
    matches them.
    """
    conservation_out, conservation_ok = conservation_lines(con, conservation)
    gold_out, gold_ok = gold_lines(con, gold)
    reconciliation = [*conservation_out, *gold_out] or [NONE_REGISTERED]
    lines = [
        *report_lines(con, relations, warnings),
        "",
        "reconciliation",
        *reconciliation,
        "",
        "quarantine summary",
        *quarantine_summary_lines(con, conservation, levels or {}),
        "",
        *docs_lines(root, url),
        "",
        FABRIC_LABEL,
    ]
    return lines, conservation_ok and gold_ok


def _is_error_rate_line(line: str) -> bool:
    """True for an entity or worst-file rate line that ends with ERROR."""
    return ("quarantine rate" in line or "worst file of" in line) and line.endswith("ERROR")


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
        description="Land the example data, build every layer with dbt on DuckDB, print a closing summary.",
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

    levels: dict[str, Levels] = {}
    for step, extra in (("deps", ()), ("build", ()), ("docs", ("generate", "--static"))):
        label = "docs generate" if step == "docs" else step
        code = _run_dbt(step, *extra)
        if code != 0:
            print(f"demo failed at: dbt {label} (exit {code})", file=sys.stderr)
            return code
        if step == "build":
            # Read before `docs generate`, which rewrites the target folder.
            levels = read_levels(dbt_target_dir(root) / "manifest.json")

    relations: list[tuple[str, str]] = []
    warnings: list[str] = []
    conservation: dict[str, tuple[str, str, str]] = {}
    gold: list[tuple[str, str, str]] = []
    for source in sorted(sources):
        module = sources[source]
        relations.extend(module.REPORT_RELATIONS)
        warnings.extend(getattr(module, "REPORT_WARNINGS", []))
        conservation.update(getattr(module, "REPORT_CONSERVATION", {}))
        gold.extend(getattr(module, "REPORT_GOLD", []))
    url = pages_url(git_remote_url(root))

    print(flush=True)
    con = duckdb.connect(str(duckdb_path(root)), read_only=True)
    try:
        lines, ok = closing_lines(con, root, relations, warnings, conservation, gold, url, levels)
    finally:
        con.close()
    print("\n".join(lines), flush=True)
    if any(_is_error_rate_line(line) for line in lines):
        # dbt's levels tests passed, yet a printed rate is past its error level: say so.
        print(RATE_DISAGREEMENT_NOTE, flush=True)
    if not ok:
        print(FAIL_MESSAGE, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

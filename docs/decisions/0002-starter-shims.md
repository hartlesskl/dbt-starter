# 0002. Models call `starter.*` shims, never dbt's cross-database macros

- **Date:** 2026-11-21 (spike 1 ran in plan 01-01 the same day; this record was written in plan 01-03)
- **Status:** accepted; superseded in part by 0006
- **Superseded in part:** by 0006. The `fabric__` T-SQL bodies and the Warehouse rationale are history. The shims now have `duckdb__` and `fabricspark__` bodies, and the template's Fabric target is a Lakehouse.
- **Spike:** verification spike 1 from the roadmap note, "dbt cross-database macros versus starter shims"

## Context

The template promises one codebase that builds on DuckDB and on Microsoft Fabric Warehouse
(T-SQL). dbt ships cross-database macros (`dbt.safe_cast`, `dbt.concat`, `dbt.hash`,
`dbt.type_string`, `dbt.date_trunc`) that are meant to hide dialect differences. The question
was whether models can call them, or whether the project needs its own `starter.*` shim layer.

Spike 1 ran in plan 01-01 before any shim or model existed, so the evidence was not shaped by
code written to fit it. This record publishes that evidence and the decision it supports. It
does not re-decide anything.

## Evidence

Source: `01-01-SUMMARY.md`, section "Spike 1 evidence".

- **Date and platform:** 2026-11-21, `macOS-26.5.1-arm64-arm-64bit`.
- **Versions:** dbt-core 1.12.7, dbt-duckdb 1.11.1, dbt-fabric 1.12.0 (only with the `fabric`
  extra), duckdb 2.0.1, Python 3.12.15 (uv-managed).

Commands run in plan 01-01 (each from the repo root with `--profiles-dir . --project-dir .`):

1. DuckDB, executed: `dbt show --inline` of `{{ dbt.safe_cast("'abc'", dbt.type_int()) }}`.
2. DuckDB, compiled: `dbt compile --inline` of one select calling `dbt.safe_cast`,
   `dbt.concat`, `dbt.hash`, `dbt.type_string` and `dbt.date_trunc`.
3. Fabric, compiled offline: the same select with
   `FABRIC_WORKSPACE_NAME=wh_workspace FABRIC_DATABASE=wh_compile uv run --extra fabric dbt compile --target fabric --no-populate-cache --inline ...`.
4. DuckDB, executed: `typeof()` of `dbt.date_trunc('month', <date>)`, `dbt.hash` of a NULL and
   `dbt.concat` with a NULL part.

| Macro | DuckDB | Fabric | Verdict |
|-------|--------|--------|---------|
| `dbt.safe_cast('abc', type_int())` | `cast('abc' as integer)`. Executed: `Conversion Error: Could not convert string 'abc' to INT32` | `try_cast('abc' as INT)` | Not safe on DuckDB: a plain cast that raises on bad text, so it cannot drive quarantine |
| `dbt.concat(['a','b'])` | `'a' \|\| 'b'`; a NULL part makes the whole result NULL | `concat('a', 'b')`; T-SQL skips NULL | Different form and different NULL handling |
| `dbt.hash("'abc'")` | `md5(cast('abc' as TEXT))`; NULL input gives NULL | `lower(convert(varchar(50), hashbytes('md5', coalesce(convert(varchar(max), 'abc'), '')), 2))`; NULL input hashes the empty string | Different NULL handling |
| `cast('x' as dbt.type_string())` | `cast('x' as TEXT)` | `cast('x' as VARCHAR(MAX))` | `TEXT` is on the portable-subset ban list; `varchar(n)` is what both share |
| `dbt.date_trunc('month', <date>)` | `date_trunc('month', ...)`, result type `TIMESTAMP` | `CAST(DATEADD(month, DATEDIFF(month, 0, ...), 0) AS DATE)`, result type `DATE` | Different result type |

### Re-run on the current tree (plan 01-03, 2026-11-21)

The DuckDB `dbt show` and the Fabric compile were run again after the shims existed. The result
is the same as the plan 01-01 evidence, row for row:

- DuckDB execution of `dbt.safe_cast("'abc'", dbt.type_int())` still stops with
  `Conversion Error: Could not convert string 'abc' to INT32` (`cast('abc' as integer)`).
- The Fabric compile of the same call still gives `try_cast('abc' as INT)`.
- The compile of `dbt.concat`, `dbt.hash`, `dbt.type_string` and `dbt.date_trunc` still gives the
  texts in the table above on both targets.

### Evidence added now, because it needs the shims

`dbt show --inline` on DuckDB of `starter.parse_int` over three inputs:

| Input | Result |
|-------|--------|
| `'abc'` | NULL |
| `'1.5'` | NULL |
| `'1.0'` | 1 |

`starter.parse_int` goes through `starter.parse_decimal` and casts only when `floor(D) = D`, so
`'1.5'` is NULL on every target instead of 2 (DuckDB rounds a decimal to int) or 1 (T-SQL
truncates it). This is the no-rounding rule. The same rule is a row in the shared conversion
fixture (`packages/starter/tests/conversion/shim_conversion_fixture.sql`, tag
`conversion_fixture`), which runs in every DuckDB `dbt build`.

### Assumption that stays open until Phase 5

T-SQL `CONCAT` and `CONCAT_WS` need at least two value arguments. The shims `starter.concat`,
`starter.concat_ws` and `starter.hash_key` pad a shorter list with `cast(null as varchar(1))`,
which both engines skip. The rule comes from Microsoft's documentation and the padding is
unverified on a real Warehouse until the Phase 5 run of the same fixture on Fabric (research
assumption A3). The sqlglot scan in `tests/support/sql_scan.py` flags any single-value `CONCAT`
or `CONCAT_WS` left in compiled Fabric SQL.

## Decision

Models and project macros call only `starter.*` shims for every dialect difference:
`parse_decimal`, `parse_int`, `parse_date`, `safe_divide`, `concat`, `concat_ws`, `hash_key`,
`date_trunc` and `type_varchar`. They never call dbt's own cross-database macros and never
branch on `target`. Each shim has a public dispatching macro, a `default__` that raises a
compiler error naming the target, and explicit `duckdb__` and `fabric__` versions.

The convention check in plan 01-06 enforces the ban on the manifest.

## Consequences

- A new dialect difference means a new shim plus a fixture row, not an `if target.type` in a model.
- An unsupported target fails at compile time through the raising `default__`.
- The conversion fixture is the single place where shim behaviour is pinned. It runs on DuckDB
  now and on Fabric in Phase 5, unchanged.
- Fabric behaviour of the shims is compile-checked only until Phase 5.

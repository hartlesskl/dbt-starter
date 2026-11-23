# 0004. Gold models carry enforced contracts, with one timestamp exception

- **Date:** 2026-11-22
- **Status:** accepted; superseded in part by 0006
- **Superseded in part:** by 0006. The D-21 target-conditional exception and the T-SQL type reasoning are history. The gold contracts now use one `timestamp` type and no YAML target conditional.
- **Spike:** do `data_type` strings exist that both adapters accept, and do `not_null` plus named key constraints build on DuckDB and compile offline for Fabric
- **Platform:** `macOS-26.5.1-arm64-arm-64bit` (macOS on Apple silicon), Python 3.12.15 (uv-managed), uv 0.13.5

## Context

Gold is the layer an analyst queries, so its shape is a promise: which columns exist, what
type each one has, and which column identifies a row. A dbt model contract states that promise
in YAML and makes dbt compare it with the real table at build time, so a type that drifts
fails the build instead of reaching a report.

The template builds the same models on DuckDB and on Fabric Warehouse. A contract is written
once, in YAML, where Jinja macros do not run, so the question is whether one set of type
strings and constraints works on both engines. Fabric adds two limits: it records primary
keys as `NOT ENFORCED`, and it needs every key constraint to have a name.

## Evidence

Source: the contract spike of plan 03-01 and the checks written in plan 03-02.

| Finding | Result |
|---------|--------|
| Portable type strings | `int`, `bigint`, `smallint`, `date`, `varchar(n)` and `decimal(p,s)` are accepted by DuckDB and render unchanged for Fabric |
| Timestamps | There is no string for a timestamp that both engines accept: DuckDB names it `timestamp`, Fabric needs `datetime2(6)` |
| Macros in YAML | A macro cannot run in a YAML `data_type`. The `target` variable can |
| DuckDB enforcement | A primary key and `not_null` are enforced when the table is created |
| Fabric offline | `dbt compile --target fabric --no-populate-cache` works with no connection, and the adapter renders the key as `add constraint pk_fct_trips primary key nonclustered(trip_key) not enforced` |
| Key name | Fabric needs a constraint name, so every key is named `pk_<table>` |
| Type comparison | dbt compares the declared types with the real ones when it creates the table, so the select must cast each column to its declared type |

## Decision

- Every gold model has an enforced contract, and only gold does. Bronze and silver change
  shape while sources are being adopted, and a contract there would only add friction.
- Every gold select casts each column to its declared type. The two timestamp columns are the
  exception to the cast, because `starter.parse_timestamp` already returns each engine's own
  timestamp type.
- Each model has one primary key named `pk_<table>`, and every column of the key carries a
  `not_null` constraint, because Fabric rejects a nullable key column.
- There are no foreign key, check or unique constraints. DuckDB enforces a foreign key and that
  makes a rebuild depend on the order of the tables, and Fabric does not support `check`.
  The `relationships` and `unique` data tests do that job on both engines.
- The type of `pickup_datetime` and `dropoff_datetime` in `gold__fct_trips` is the only
  place YAML branches on the target:
  `{{ 'timestamp' if target.type == 'duckdb' else 'datetime2(6)' }}`. The user chose this on
  2026-11-22 (decision D-21) over leaving the fact without a contract. `tests/conventions/test_contracts.py`
  allows that exact text on those two columns and nowhere else in tracked YAML, and names the
  file and the key when a third use appears.
- Data tests remain the real enforcement. Fabric records keys as `NOT ENFORCED`, so the
  contract documents the shape and catches type drift, while `unique`, `not_null` and
  `relationships` tests catch bad data on both engines.

## Consequences

- A declared type that differs from the select fails the DuckDB build, which is the point.
- A `varchar(n)` that is too short truncates silently on T-SQL, so widths are generous.
- Adding a gold model means writing its contract: the gate fails and names the model when a
  column has no type, a type is outside the portable list, the key has no `pk_` name, or a key
  column is nullable.
- Fabric contract problems show up in the gate without a Fabric workspace, because the gate
  renders each model's column definitions and key with the adapter's own functions, offline.
- The rejected fallback was no contract on `fct_trips`. The fact is the largest table and the
  one most likely to drift, so leaving it out would remove the check where it matters most.

## Revisit triggers

- The Phase 5 live Fabric run: whether `ALTER TABLE ... add constraint` works with the key
  columns as declared, whether the declared types match the real warehouse types, and whether
  the `varchar` widths hold against real data.
- A dbt or dbt-fabric release that lets a macro run in YAML, or maps `timestamp` to
  `datetime2(6)` itself. Either would remove the one conditional.

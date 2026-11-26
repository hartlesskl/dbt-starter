# 0006. Fabric runs on one schema-enabled Lakehouse through dbt-fabricspark on dbt Core 1.11

- **Date:** 2026-11-23
- **Status:** accepted
- **Spike:** the phase research (an offline fabricspark compile, a local Spark 3.5.5 and 4.1.1 conformance run with ANSI off and on, and the probes of plans 04.1-01 to 04.1-03)
- **Platform:** `macOS-26.5.1-arm64-arm-64bit` (macOS on Apple silicon), Python 3.12.15 (uv-managed), dbt-core 1.11.17, dbt-duckdb 1.11.1, duckdb 2.0.1, dbt-fabricspark 1.12.2
- **Reversibility:** costly. The record fixes the one-Lakehouse layout and the `FABRIC_*` variable names as the adopter setup contract that the Phase 5 guide and the Phase 5 CI inherit, and returning to a Warehouse would mean rewriting every shim, the scan and the contracts again.

## Context

On 2026-11-23 the author moved the template's Fabric half from dbt-fabric on a Fabric Warehouse
(T-SQL) to Fabric's native dbt job on a Lakehouse (dbt-fabricspark, Spark SQL over Delta). Three
things in the earlier records stopped being true:

- the portable subset was DuckDB plus T-SQL, so the shims, the scan and the contract types were
  written against a Warehouse (ADR 0002, ADR 0004, ADR 0005);
- the template promised dbt Core 1.12, while the native job runs dbt Core 1.11, and a template that
  adopters run in that job must support the job's line;
- the Fabric target needed a Warehouse, an ODBC driver and a test wrapper that no longer exist on a
  Lakehouse.

Fabric stays compile-checked only. Nothing in this record claims that the Fabric half has run: every
item that only a live run can settle is named below as a Phase 5 check.

## Evidence

Source: the phase research and the results of plans 04.1-01 to 04.1-03. The labels A1 to A9 name the numbered assumptions of that research, which live in the project's planning notes and not in this repository.

| Finding | Result |
|---------|--------|
| The native job's runtime | Runtime V1.0 ships dbt Core 1.11, dbt-fabricspark 1.12.2 for a Fabric Lakehouse and Python 3.12. The runtime table gives no dbt-core patch version. The user approved dbt-fabricspark 1.12.2 at the package-legitimacy checkpoint of plan 04.1-01 on 2026-11-23, after reading PyPI and the Microsoft-owned repository |
| Dispatch prefix | `fabricspark__` only. The adapter lists no dbt-spark dependency and passes no `dependencies` to its plugin, so a `spark__` macro is never searched. A shim with only `duckdb__` and `fabric__` bodies failed with "not implemented for target type fabricspark" |
| Cross-Lakehouse writes | The adapter's supported write path across Lakehouses is a model-level `database` config that needs schema-enabled Lakehouses and three-part names. In non-schema mode the adapter pins every model to the profile's Lakehouse, and a Livy session binds to one Lakehouse. The D-05 fallback therefore applies |
| Offline compile | `dbt compile --target fabric --no-populate-cache` runs with three dummy UUID and name values and no connection. Plan 04.1-01 compiled 307 files and the Spark scan reported nothing. Without `--no-populate-cache` the compile calls the Fabric API and runs on for minutes |
| Dispatch block removed | Compiling with and without the root `dispatch:` block gave byte-identical output for all 307 files, because dbt-fabricspark ships no `dbt_utils` overrides |
| ANSI default | Off on Fabric Runtime 2.0 (Spark 4.1, which Fabric overrides from true) and on Runtime 1.3 (Spark 3.5). The shims were verified on Spark 3.5.5 and 4.1.1 with ANSI off and on, and `try_cast` of text agreed in all four configurations |
| Divergences found | A bare `try_cast` differs between DuckDB and Spark on `1_000`, `epoch`, `infinity`, `2024/03/15`, `2024`, `2024-03`, Arabic-Indic digits, a zone offset on a timestamp, hour 24 and no-break-space padding, and Spark `concat` returns NULL when any argument is NULL |
| The grammar that closes them | A character whitelist, then an anchored regex, then the native `try_cast`, written twice (`regexp_matches` on DuckDB, `regexp_like` on Spark). Plan 04.1-02 pinned it with a conversion fixture of 102 rows, and the DuckDB run of that fixture passes |
| Constraint support | `CONSTRAINT_SUPPORT` marks every constraint `NOT_ENFORCED`. The table materialization renders only `check` constraints, so named primary keys and `not_null` are skipped with no error. One declared `timestamp` matches the engine-reported type on Spark 3.5.5 and 4.1.1 and on DuckDB, while `datetime2(6)` is an unsupported type on Spark |
| The scan's constructs | DuckDB accepts and Spark rejects `QUALIFY`, star modifiers (`EXCLUDE`, `REPLACE`), `UNION ALL BY NAME`, `DISTINCT ON` and DuckDB-only functions and types. Nested `WITH`, mixed-case identifiers and a one-argument `CONCAT` work on both engines, so those T-SQL bans are removed |
| `information_schema` | Fabric Lakehouse Spark has no `information_schema.columns`, so the two source tests that read it needed a per-engine body (plan 04.1-02) |
| `dbt deps` in the native job | Undocumented. The job's command list names `build`, `run`, `seed`, `test`, `compile`, `snapshot` and `docs generate`, and no Microsoft page mentions `packages.yml` or `dbt_packages` |
| The lock | `uv lock` resolves dbt-core 1.11.17, dbt-duckdb 1.11.1, duckdb 2.0.1 and, in the `fabric` extra only, dbt-fabricspark 1.12.2. The default export holds no azure, msal, dbt-fabric or mssql package (plan 04.1-01) |

## Decision

1. **Layout: one schema-enabled Lakehouse.**

   The Fabric target is one Lakehouse created with schemas enabled. It holds the schemas `landing`,
   `bronze`, `silver` and `gold` and the default `dbo`. The profile `schema` is `FABRIC_SCHEMA`,
   default `dbo`, and it must differ from the Lakehouse name, because the adapter raises
   otherwise. This is the D-05 fallback, and it replaces D-01's four Lakehouses. Layer, folder and
   schema are still the same word (LAYR-01), and `generate_schema_name`, `generate_alias_name` and
   the adapter's relation macros are unchanged.

   D-02's bare layer names hold as schema names. D-03's landing Lakehouse becomes the `landing`
   schema of the same Lakehouse, which the Phase 5 loader writes and the sources read. D-04's
   two-part names are what the offline compile renders (`` `bronze`.table ``), because the adapter
   adds the Lakehouse part only once a connection opens. A live run on a schema-enabled Lakehouse
   renders three parts with the Lakehouse name first, and still no workspace name.

2. **Version line: dbt Core 1.11.**

   dbt-core is `>=1.11.0,<1.12`, `require-dbt-version` is `[">=1.11.0", "<1.12.0"]` in both
   `dbt_project.yml` files, dbt-fabricspark is `==1.12.2`, and duckdb stays at 2.0.1 (ADR 0001 is
   unchanged). The 1.12 promise is dropped, and no 1.12-only feature is used.

   Deviation from D-09: D-09 asked the `fabric` extra to pin the native job's dbt-core as well as
   its adapter, but Microsoft's runtime table for the native job names dbt Core 1.11 and gives no
   patch version (phase research, assumption A9). So dbt-core is the range `>=1.11.0,<1.12` in the
   default dependencies rather than an exact pin in the `fabric` extra. The lock resolved
   dbt-core 1.11.17 when this record was written, and plan 05-04 moved it to 1.11.8, the native
   job's patch, within the same range. dbt-fabricspark is pinned exactly at 1.12.2 in the `fabric`
   extra, because the table names that version. Phase 5's first native-job run (check
   `native-job-deps`) reads the job's dbt-core patch from its run log and, if it differs from the
   lock, the lock moves to it within the 1.11 line by the upgrade steps below.

   The upgrade steps when the job moves (D-11): read the job's runtime table on Microsoft Learn,
   edit the dbt-core range and the dbt-fabricspark pin in `pyproject.toml` and `require-dbt-version`
   in both `dbt_project.yml` files, run `uv lock`, run `make gate`, rerun the Phase 5 live
   verification and update the compatibility matrix. No automation ties the lock to the job.

3. **Shims: explicit bodies, one grammar.**

   Each shim has an explicit `duckdb__` body and an explicit `fabricspark__` body, and no `spark__`
   body. The four parse shims share one whitelist-then-regex grammar that does not depend on the
   ANSI setting. Exponent text such as `1e3` is rejected on both engines. `concat` is
   `concat_ws('')` on Spark, which skips NULLs as DuckDB does, so the T-SQL arity padding is gone.

4. **Scan: the Spark dialect.**

   The scan parses the compiled Fabric SQL with sqlglot's Spark dialect and reports what DuckDB
   accepts and Spark rejects: `QUALIFY`, star modifiers, `UNION BY NAME`, `DISTINCT ON`, functions
   the shims did not produce, `information_schema` and the types Spark or the SQL analytics
   endpoint does not support. The Warehouse-era bans (nested `WITH`, mixed case, `CONCAT` arity,
   varchar-only types) are removed. `check_materializations` is kept, banning ephemeral because it
   has no relation in a layer schema, and incremental because dbt-duckdb and dbt-fabricspark
   implement merge and delete+insert differently.

5. **Offline compile: no proxy.**

   D-17's fallback, compiling with the plain dbt-spark adapter as a proxy, was not needed:
   dbt-fabricspark compiles offline with dummy values and `--no-populate-cache`. The gate step
   stays `compile-fabric`, and a convention test keeps `--no-populate-cache`, the UUID dummy
   environment and the separate target folder in that step.

6. **Contracts on Delta.**

   The gold contracts use one `timestamp` type, so the YAML has no target conditional. The named
   `pk_<table>` keys and `not_null` constraints stay in the YAML. They are enforced on DuckDB, and
   dbt-fabricspark records and skips them with no error, so the `unique` and `not_null` data tests
   carry the keys on Fabric.

7. **Packages for the native job: not vendored.**

   Whether the job runs `dbt deps` is undocumented, so 04.1 does not vendor `dbt_packages/`. The
   user confirmed this on 2026-11-23 (assumption A1). If Phase 5 shows that the job does not run
   `dbt deps`, the recipe is: commit the resolved packages as real directories, not the `local:`
   symlink that `dbt deps` writes, un-ignore them for the job only, and add a gate check that the
   copy matches `package-lock.yml`. The DuckDB flow keeps running `dbt deps`.

8. **The setup-command contract (D-06, built live in Phase 5).**

   The command is `setup-fabric` (`make setup-fabric` and `uv run setup-fabric`). Its inputs are
   `FABRIC_WORKSPACE_ID` and `FABRIC_LAKEHOUSE`, plus an `az login` identity with Contributor on
   the workspace. It looks up a Lakehouse by display name and, only if none exists, creates one with
   `POST https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/lakehouses` and a body of
   `displayName` plus `creationPayload.enableSchemas: true`. It prints the Lakehouse id to set as
   `FABRIC_LAKEHOUSE_ID`, refuses an existing Lakehouse without schemas, is idempotent, and never
   deletes or renames anything. dbt creates the `bronze`, `silver` and `gold` schemas, and the
   Phase 5 loader creates `landing`.

9. **Three run modes.**

   DuckDB (`make demo`, the default, no account), self-hosted dbt on the Lakehouse
   (`make build-fabric`, reading `FABRIC_WORKSPACE_ID`, `FABRIC_LAKEHOUSE_ID`, `FABRIC_LAKEHOUSE`,
   `FABRIC_SCHEMA` and `FABRIC_AUTHENTICATION`), and Fabric's native dbt job
   ([docs/fabric-native-job.md](../fabric-native-job.md)).

10. **The Phase 5 sink-spike record is ADR 0007.**

    The loader's Lakehouse sink is re-discussed in Phase 5, and its spike gets the number 0007, not
    0006.

## Consequences

- This record supersedes in part ADR 0002 (the T-SQL bodies and the Warehouse rationale), ADR 0004
  (the timestamp branch and the T-SQL type reasoning, including the D-21 exception) and ADR 0005
  (the wrapped-cte-tests check, removed with dbt-fabric). Their bodies stay as history, and
  `tests/conventions/test_decision_records.py` holds that their status lines say so. ADR 0001 and
  ADR 0003 are unchanged.
- The README keeps the label `Fabric: compile-checked only` until a verification record exists.
- The Spark `undeclared_columns` body returns no rows on purpose, so a new landed column is not
  reported on Fabric until the live lookup below exists. The DuckDB gate still reports it.
- The dbt line follows the native job. When the job moves, the upgrade steps in decision 2 run by
  hand.

### Phase 5 live checks

Each check below is an item the offline work cannot settle. None is stated as fact above.

- `native-job-deps`: the native job runs `dbt deps` and resolves `packages/starter` and `dbt_utils`
  (A1). The same first run reads the job's dbt-core patch from its log.
- `schema-layout`: dbt writes and reads across the four schemas of one Lakehouse with profile
  schema `dbo` (A3), plus an optional spike of the per-layer layout with non-schema Lakehouses.
- `session-time-zone`: `select current_timezone()` returns UTC in self-hosted and in native-job
  sessions (A4), so a timestamp without a zone parses as it does on DuckDB.
- `undeclared-columns-live`: a live column lookup for the `fabricspark__undeclared_columns_sql`
  body, which is a zero-row stub today (A5).
- `bronze-plan-cost`: the largest bronze model plans and runs on the Fabric driver (A6). A local
  Spark with a default heap ran out of memory planning it.
- `conversion-fixture-spark`: `dbt test --select tag:conversion_fixture --target fabric` returns
  no rows, which proves that the shim grammar agrees on a live Spark.
- `contracts-live`: dbt's contract check passes on Delta for every gold model.
- `native-job-profile-schema`: how the job's UI profile maps `schema` on a schema-enabled
  Lakehouse (a question the phase research left open).

<!-- example:begin -->
The largest bronze model is the example's trip model, `bronze__nyc_taxi__yellow_trips`. The
`bronze-plan-cost` check names it, and the conversion fixture's compiled Spark SQL is the
`singular.shim_conversion_fixture` test.
<!-- example:end -->

<!-- example:begin -->
## Phase 5 outcome (2026-11-25)

This section is appended after Phase 5 ran the live checks, and the record above it is unchanged history. The verified results are in [docs/verification/fabric-2026-11-25.md](../verification/fabric-2026-11-25.md), and the sink spike is [ADR 0007](0007-lakehouse-landing-sink.md).

The eight live checks of this record, with the outcomes the verification record carries:

- `native-job-deps`: pass. The native job ran `dbt deps` itself.
- `schema-layout`: pass. The schemas of one Lakehouse hold the report relations, and all fifteen equal DuckDB's counts.
- `session-time-zone`: pass. Every native build and the self-hosted build ran in UTC.
- `undeclared-columns-live`: pass. The live lookup ran in every native build, and the trips test warned with the expected failure counts.
- `bronze-plan-cost`: pass. The bronze trips model and its unit tests ran on the Fabric driver, in the builds and on the real 2024-01 month.
- `conversion-fixture-spark`: pass. The conversion fixture test returned no rows in every run.
- `contracts-live`: pass. No contract failed in any native or self-hosted build.
- `native-job-profile-schema`: pass. The job's profile schema `dbo` works on the schema-enabled Lakehouse.

Related checks outside this list: `job-rest-run` is a documented fallback, because the REST start of the dbt job was refused (InvalidJobType) and the runs started in the portal.

The native job reported dbt Core 1.11.8, while the lock first resolved 1.11.17 when this record was written. Plan 05-04 moved the lock down to 1.11.8 within the same range, so the verified patch and the locked patch agree, and decision 2's range `>=1.11.0,<1.12` and its upgrade steps are unchanged.

The scan now reports functions outside its allow-list (WR-02), so decision 4's wording holds.
<!-- example:end -->

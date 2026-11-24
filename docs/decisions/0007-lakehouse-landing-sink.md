# 0007. The loader lands Lakehouse tables as Delta through delta-rs

- **Date:** 2026-11-24
- **Status:** accepted
- **Spike:** this phase's live spike on two schema-enabled Lakehouses of one Fabric trial workspace: a small fixture on the first (plan 05-02) and replace-by-file, schema merge, the SQL analytics endpoint read and real 2024-01 on the second (plan 05-06)
- **Platform:** `macOS-26.5.1-arm64-arm-64bit` (macOS on Apple silicon), Python 3.12.15 (uv-managed), deltalake==1.6.6, duckdb 2.0.1, dbt-fabricspark 1.12.2, Fabric Runtime 2.0 with Spark 4.1.1
- **Reversibility:** costly. Once the guide, the CI workflow and the first builds on the record Lakehouse rest on the sink, moving to another write path means changing the loader, its extra and every live evidence row, which is why the spike ran before any of them.

## Context

ADR 0006 moved the Fabric half to one schema-enabled Lakehouse and left the loader's sink open: the
loader has to land the DuckDB-normalised rows as a Delta table that Spark reads as
`landing.<table>`, one source file at a time, so a re-run replaces that file's rows and a file with
a new column adds the column. Two paths were possible. The first writes Delta from the Mac with
delta-rs straight to OneLake. The second stages Parquet under the Lakehouse Files area and loads
`landing` with Spark SQL over Livy. The first needs no Spark session to load and keeps the loader
small; the second is kept as the documented fallback.

The pass bar was fixed before the spike: the delta-rs path ships only if all five items below pass
on a real Lakehouse, and any failure means the fallback is built. Replace-by-file and schema merge
ran on a Lakehouse that the record never uses, because the build against the record Lakehouse must
start from a clean landing schema.

## Evidence

Source: `target/fabric-live/sink-record.json` and `target/fabric-live/sink-spike.json`, written by plans 05-02 and 05-06. The folder is not committed. Counts are read back from the Lakehouse through Spark or the SQL analytics endpoint, never from what the loader printed.

| Finding | Result | Outcome |
|---------|--------|---------|
| `sink-discovery` | Step 1 of the discovery ladder worked: after the first load, with no `create schema` beforehand, `show tables in landing` on Spark 4.1.1 (Runtime 2.0) listed both loader tables, and `select count(*)` returned the DuckDB counts (12 zone rows, 59 trip rows). The `landing` schema appeared by itself when delta-rs wrote `Tables/landing/<table>` | pass |
| `replace-by-file` | Repeating identical loads of the fixture left the counts at 12 and 59 on both Lakehouses (one repeat on the first, two on the second). On the first, every repeated file printed a `Replaced` line, so one predicate overwrite per source file replaced rows in place | pass |
| `schema-merge` | The 2026-09 fixture file re-landed with one extra text column added that column as `string` on the table, with a value on 2 rows (the 2 rows of that file) and null on the rest. The table total stayed 59 before and after | pass |
| `sql-endpoint-read` | Read by the author in the portal on the second Lakehouse's SQL analytics endpoint after a metadata refresh (HTTP 200): 59 trip rows, 2 with the new column, 12 zone rows, `last_loaded` shown as a datetime, nothing under "Unidentified". All four numbers equal Spark's | pass |
| Protocol and `_loaded_at` type | Both tables report Delta reader 1, writer 2 and no table features. `_loaded_at` is `timestamp`, read as a datetime by Spark and by the endpoint. The loader casts it with `at time zone 'UTC'` to an instant, so no `timestamp_ntz` feature raises the protocol | pass |
| 2024-01 volume | Real 2024-01 landed with 2,964,624 trip rows, and the 265 zone rows, in 25.28 s for the trips and 8.95 s for the zones (`/usr/bin/time -p`, whole load command, 2026-11-24, the download cache already in place). Spark read back 2,964,624 and 265, equal to the same two loads into DuckDB. The Mac's uplink was not measured, so this is a sanity check and not a benchmark | pass |
| Capacity of the trial | The trial capacity is a small SKU (FTL4) that allows one Spark (Livy) session at a time. A dbt or query call that started before the previous session had ended was refused with HTTP 430 `TooManyRequestsForCapacity`, and a session stayed active for about 3 to 5 minutes after its client returned. delta-rs writes go straight to OneLake and open no session | observed |

## Decision

1. **The loader writes Delta with delta-rs.**

   `load <source> <entity> --target fabric` writes `Tables/landing/<table>` over the OneLake abfss
   path, using the az CLI's storage-audience token. Each source file is one predicate overwrite
   (`_source_file = '<file>'`), so a repeated file replaces its own rows and nothing else. A new
   column arrives through delta-rs schema merge, as a string, with null on the other files' rows.
   `_loaded_at` is written as a UTC instant, which keeps the Delta protocol at reader 1, writer 2
   and keeps the table readable by the SQL analytics endpoint. DuckDB normalisation is unchanged:
   the rows are landed and normalised in DuckDB first and shipped from there.

2. **Nobody creates the landing schema.**

   Step 1 of the ladder passed, so `setup-fabric` has no landing-schema step. The schema appears
   when the first table is written, and Spark and the endpoint list it as `landing`.

3. **The fallback stays documented and unbuilt.**

   If a later runtime stops reading these tables, the fallback is to stage normalised Parquet under
   `Files/landing/<table>/` and load `landing` with Spark SQL over Livy, one replace per source
   file and a new column added as a string first. It is not built, and nothing in the loader
   depends on it.

## Consequences

- The `fabric` extra carries `deltalake==1.6.6`; the default environment does not, so the DuckDB
  flow installs nothing new.
- Loading needs an `az login` identity with write access to the Lakehouse's OneLake folder, not a
  Spark session, so a load does not compete with a dbt run for Spark capacity.
- One load at a time per Lakehouse: two writers to one table race on its transaction log.
- Volume runs use their own Lakehouse, because the fixture's file names (`yellow_tripdata_2026-09`
  and the rest) can collide with real months. After this spike the volume Lakehouse holds only the
  real zones and real 2024-01, ready for the volume run.
- Live steps on a small trial capacity must run one at a time. Each dbt or query call holds the
  capacity's one Spark session for a few minutes after it returns, so a caller waits for the
  previous session to end before starting the next, or gets HTTP 430. Adopters on a larger
  capacity do not meet this.
- Nothing here changes the claim on the README: Fabric stays `compile-checked only` until the
  verification record of the end of Phase 5 exists.

### Phase 5 live checks

- `sink-discovery`: Spark lists and counts the delta-rs tables with no landing schema created first
  (passed at step 1 on Spark 4.1.1).
- `sql-endpoint-read`: the SQL analytics endpoint reads the same tables with equal counts and shows
  nothing under "Unidentified" (passed, read by the author in the portal).

# 0005. Quarantine rates are checked against per-entity levels by a test whose second parent is the bronze model

- **Date:** 2026-11-22
- **Status:** accepted
- **Spike:** which parent of a levels test makes dbt skip gold when the test fails, and which levels the measured rates of real months support
- **Platform:** `macOS-26.5.1-arm64-arm-64bit` (macOS on Apple silicon), Python 3.12.15 (uv-managed), dbt-core 1.12.7, dbt-duckdb 1.11.1, duckdb 2.0.1

## Context

A quarantine split can drift without anyone noticing. A bad month can quarantine a large share of
its rows and the build stays green, because every row is still accounted for: it sits in silver or
in quarantine. The template must say so. A rate above a first level is a warning. A rate above a
stronger level must stop gold for that entity, while silver and quarantine still build, so the bad
rows stay countable and the fix is visible.

The levels belong to the entity, not to the project. A trip feed and a small lookup table have very
different normal rates, so each entity states its own, in its own YAML.

## Evidence

Source: the research and the tracer of plan 04-01, and the checks of plans 04-02 and 04-03.

| Finding | Result |
|---------|--------|
| How dbt chooses what to skip | dbt adds an edge from a test to a downstream model only when the model descends from every parent of the test (`dbt/compilation.py`). A test with two parents therefore stops a model only if that model descends from both |
| Second parent is the quarantine model | The test failed (rank 2), the build ended `PASS=321 WARN=1 ERROR=2 SKIP=0`, and the gold models were rebuilt right after the failing test. Gold never descends from the quarantine model, so nothing was skipped |
| Second parent is the bronze model | The test failed (rank 2), `gold__fct_trips` and `gold__agg_trips_daily` and their tests were skipped (14 skips with the trip entity failing, 17 with both entities failing), and silver, quarantine and the three data quality models were `success` |
| `run_results.json` | `dbt docs generate` overwrites it, so a reader of skipped models must read it right after `dbt build` and never after the docs step |
| Rate resolution | Rates are `decimal(18, 6)`, so the smallest meaningful level is 0.000001 and a rate equal to a level is not above it |
| Fabric wrapper | dbt-fabric sends every test body inside `with test_main_sql as (...)`, so a body that opens a CTE nests a WITH on a Warehouse. The offline compile writes the body unwrapped and cannot see it |

## Decision

- One generic test, `starter.quarantine_rate_levels(model, all_rows_model, warn_above, error_above)`,
  sits on each entity's silver model. `model` is the silver model, `all_rows_model` is the entity's
  bronze model, and the two levels are fractions of rows. These four argument names and their place
  in the entity's YAML are the adopter contract. Changing them is costly, because the Phase 6
  swap-out guide documents them.
- The second parent is the bronze model, never the quarantine model, for the reason in the Evidence
  table. The macro's own doc block says so, and `tests/conventions/test_quarantine_levels.py` fails
  a levels test whose second parent is anything but a registered bronze model.
- The rate is the rows that failed an error rule, divided by all rows: bronze rows minus silver
  rows, over bronze rows. The warned rate is never thresholded, because warned rows are usable and
  must never stop gold (D-01).
- The rate is checked at two grains with the same levels: per source file, and over all files
  together (D-02). A bad month cannot hide inside a multi-month average.
- A scope fires a level only when its rate is strictly above it, compared at 6 decimal places. The
  levels reach the SQL only as `'%.6f'` literals, so text such as `1e-05` never does. A level that
  is not a number, is a boolean, lies outside 0 to 1, or a warn level above the error level, stops
  compilation with a message that starts `quarantine_rate_levels:`.
- The test returns one row per scope above the warn level, with a rank of 1 (above warn) or 2
  (above error). `fail_calc` is the highest rank among the rows, with `warn_if` `>= 1` and
  `error_if` `>= 2`, so rank 1 is a dbt warning and rank 2 fails the test. The outcome does not
  depend on row or file order. An entity with no rows has no scope, so its test passes.
- The body is one SELECT over derived tables, not a chain of CTEs, because dbt-fabric wraps every
  test body in a CTE and a CTE inside it would be a nested WITH. The wrapped text is checked offline
  by `fabric_test_wrapped` in `tests/conventions/test_fabric_test_wrapper.py`.
- Rates go through `starter.safe_divide`, so the numerator is cast to `decimal(18, 6)` before the
  division on both engines. The demo printer recomputes the same rates outside dbt, because Python
  cannot call a dbt macro. It holds the DuckDB rendering of `starter.safe_divide` in one constant,
  `SAFE_DIVIDE_SQL` in `src/starter_cli/demo.py` (plan 04-04), and a fixture test proves that the
  printed rates equal the levels test's rates (plan 04-06). This is the one recorded exception to
  "rates go through `starter.safe_divide`", and it still never divides integers. The printer only
  reports: dbt's levels tests and dbt's exit status enforce (D-06).

## Levels

Both example entities use warn 0.001 (0.1% of rows) and error 0.01 (1% of rows). They sit well above
the measured rates, so the default demo is quiet (D-03), and well below a rate that would mean a
broken feed.

| Entity and file | Rows | Quarantined | Rate |
|-----------------|------|-------------|------|
| The trip entity, 2024-01 | 2,964,624 | 90 | 0.0030% |
| The trip entity, 2025-01 | 3,475,226 | 166 | 0.0048% |
| The trip entity, ten more months from 2022-06 to 2026-09 | 35,887,787 | peak in 2022-06 | 0.0494% |
| The zone lookup | 265 | 0 | 0% |

Sources: the pinned-month and zone rows come from the measured runs of 2026-11-23 recorded under
`target/measure/` (see Confirmation). The ten-month row and the 2022-06 figures below come from one
earlier build of those ten months on 2026-11-22, on the same machine (Apple M5, 10 cores, 16 GB,
macOS 26.5.1) with the same dbt-core, dbt-duckdb and duckdb versions; 0.0494% is the rate of its
worst month, 2022-06, not of all ten. That build's records were not kept under `target/measure/`.

On the two pinned months the worst rate is more than 20 times under the warn level. On the worst
month seen (2022-06, 1,759 rows) the rate is about half the warn level and about 20 times under the
error level. One quarantined zone out of 265 is 0.38%, so any bad zone warns and three or more
fail the build, which is intended for a lookup table.

The folklore levels (1% warn, 5% error) were rejected, because nobody measured them against real
data and they would never fire on it (D-03).

## Synthetic builds

The fixture plants every rule, so its quarantine rate is far above any real level. A whole-project
build of synthetic data would therefore fail on every run. `RELAXED_LEVELS_ARGS`, defined once in
`src/starter_cli/gate.py` and shared with `tests/support/dbt_cli.py`, passes the dbt var
`quarantine_rate_override` with both levels set to 1. A rate cannot be above 1, so nothing fires.
The var replaces the levels inside the same compiled test, so the test stays in play, which an
`--exclude` would not do.

The var turns the levels off, so it is confined: it may be named only in the generic test macro, in
`src/starter_cli/gate.py`, in the README, and under `tests/` and `docs/`. The demo, the nightly and
the Pages build never pass it. `tests/conventions/test_quarantine_levels.py` fails, naming the path,
when the var appears in the demo, the Makefile, a workflow or the project config. The threshold
tests of plan 04-02 prove each level on planted data instead, with the real levels.

## Consequences

- Adding an entity means adding one levels test beside its silver model. The convention check fails
  an entity registered in `dq_entities()` that has none, or two, and a level that could never fire
  or would always fire.
- A level change keeps the test id, because each test has a `name:`.
- A new generic test that must run on Fabric is written as one SELECT over derived tables, like
  this one, unless it is added to the list below together with this ADR.

### Phase 5 check: wrapped-cte-tests

An open item for the live Fabric phase. dbt-fabric's test wrapper starts with
`with test_main_sql as (`, so every test whose body opens a CTE nests a WITH on a live Warehouse.
The offline compile and the scan of the compiled files cannot see this, because those files hold
the body unwrapped.

The kinds that nest once wrapped, by owner, with their node counts on the offline Fabric manifest
of plan 04-03:

- Built into dbt-core: `dbt.accepted_values` (95 nodes) and `dbt.relationships` (8).
- From `dbt_utils`: `dbt_utils.accepted_range` (7), `dbt_utils.equal_rowcount` (1) and
  `dbt_utils.unique_combination_of_columns` (5).
- From the starter package: `starter.rows_equal_by_batch` (2) and `starter.rows_split_by_batch` (2)
  in `invariants.sql`, `starter.totals_reconcile` (2) in `reconciliation.sql`, and
  `starter.date_span_complete` (1) in `calendar.sql`.
- Singular, from the starter package: `singular.shim_conversion_fixture` (1).

dbt-fabric's own `fabric__get_unit_test_sql` also nests a WITH in every unit test (11 in the
project). That suggests the adapter expects the Warehouse to accept the shape, which stays an
assumption until the live run.

How the list is held: `tests/conventions/test_fabric_test_wrapper.py` fails the PR gate when a kind
that is not listed nests, and when a listed kind stops nesting or is no longer run. The list
therefore changes only by an edit to that file and to this subsection, and removing the example's
tests in Phase 6 means pruning both.

The check, for the Phase 5 verification window: run at least one test of each listed kind, and the
unit tests, on the Warehouse. If the Warehouse rejects the shape, rewrite each starter kind as
derived tables, as `quarantine_rate_levels` already is, override the dbt-core and `dbt_utils` kinds
through dispatch or replace them, and shrink the list as each is fixed.

## Alternatives rejected

- Two tests with explicit severities per entity: it doubles the YAML, and two numbers become four
  nodes.
- A quarantine parent plus a `-- depends_on` hint in every gold model: it adds a fake lineage edge
  to each gold model and relies on every author remembering it.
- A per-file rate model: it breaks the layer direction for a single check and creates a second
  place that holds the levels.

## Confirmation

Measured on 2026-11-23 (UTC) on an Apple M5 (10 cores, 16 GB, macOS 26.5.1), with dbt-core 1.12.7,
dbt-duckdb 1.11.1 and duckdb 2.0.1. The run built a new DuckDB file with the two pinned months
(2024-01 and 2025-01) from freshly downloaded files. The levels stand: warn 0.001 (0.1%) and error
0.01 (1%) for both entities. Plan 04-09 measured them against the pinned months and changed nothing.

| Entity and file | Rows | Quarantined | Rate |
|-----------------|------|-------------|------|
| The trip entity, 2024-01 | 2,964,624 | 90 | 0.0030% |
| The trip entity, 2025-01 | 3,475,226 | 166 | 0.0048% |
| The trip entity, both files | 6,439,850 | 256 | 0.0040% |
| The zone lookup | 265 | 0 | 0.0000% |

- **Headroom.** The worst pinned file (0.0048%) is about 21 times under the warn level (0.1%) and
  about 209 times under the error level (1%). Every pinned rate is below one tenth of the warn level
  (0.01%), the reading of "well above the measured rates" (D-03) used for this check. The closing
  summary of the run shows every entity line and worst-file line as `OK`.
- **Run time and size.** The first run, with `--refresh` and a new database file, took 29.3 s end
  to end and left 2,658,152,448 bytes of database and 109,132,976 bytes of downloads in `data/raw/`
  (2,767,285,424 bytes in all, about 2.58 GiB). A second run on the same file took 24.1 s and the
  total was 2,864,802,992 bytes (about 2.67 GiB). Both are inside the budget of 5 minutes and 6 GB
  (D-11), so the two pinned months stay and the one-month fallback (D-10) was not needed.
- **Checks.** The offline suite (`make check`'s command) passed against the measured database
  (963 passed), and the end-to-end test passed.
- **Effect of a fix, rehearsed on a copy.** With the line for payment type 0 removed from the
  payment type seed, `dbt build` quarantined 680,400 of the 6,439,850 trips (10.57%), the trip
  levels test failed, and the two trip gold models and their 12 tests were skipped while silver and
  quarantine still built. With the line restored, the next build gave back the measured counts
  (6,439,594 in silver and 256 in quarantine) and every test passed. The levels need no change for
  this.
- **What this does not prove.** The rates are a dated measurement of two published files. The levels
  tests are the live check on the same rates, and the publisher may re-publish a file. A pinned
  file that reaches the warn level makes the demo print `WARN`, and one above the error level fails
  the build.

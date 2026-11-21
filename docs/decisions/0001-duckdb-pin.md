# 0001. Pin DuckDB to exactly 2.0.1

- **Date:** 2026-11-21 (spike 2 ran in plan 01-02 the same day; this record was written in plan 01-03)
- **Status:** accepted
- **Spike:** verification spike 2 from the roadmap note, "does the demo run on DuckDB 2.0 with dbt-duckdb"
- **Platform:** `macOS-26.5.1-arm64-arm-64bit` (macOS on Apple silicon), Python 3.12.15 (uv-managed), uv 0.13.5

## Context

DuckDB 2.0.0 became the long-term-support line on 2026-10-21. DuckDB 1.5 support ends in March
2027, which is inside the life of a template meant to be copied and kept for a year. The
catch is that `dbt-duckdb` 1.11.1 declares only `duckdb>=1.0.0` in its metadata and is
CI-tested mainly against 1.5, so the pairing with 2.0 needed a real run before it could be
pinned. The fallback rule written into plan 01-02 was: if any step fails for a DuckDB 2.0
specific reason, pin `duckdb==1.5.6` instead.

This record publishes a decision taken in plan 01-02 on the evidence below. It does not
re-decide it.

## Evidence

Source: `01-02-SUMMARY.md`, section "Spike 2 evidence", and `01-01-SUMMARY.md` for the
registry check.

Versions on the platform above, confirmed again in plan 01-03 with
`uv run dbt --version` and `uv run python -c "import duckdb; print(duckdb.__version__)"`:

| Package | Version |
|---------|---------|
| dbt-core | 1.12.7 |
| dbt-duckdb (plugin) | 1.11.1 |
| duckdb (Python and engine) | 2.0.1 |
| dbt-fabric (only with `--extra fabric`) | 1.12.0 |

From plan 01-01 (2026-11-21): no stable duckdb release newer than 2.0.1 exists on PyPI.
2.0.1 was uploaded on 2026-11-20; the index holds only `2.0.1.devN` and `2.1.0.devN`
pre-release builds. So there was no newer patch to pick up.

From plan 01-02 (2026-11-21), all on the locked pin:

1. `uv sync --locked` succeeded, and `uv lock --check` found no drift.
2. `uv run demo`, run twice, exited 0 both times. `dbt deps` worked, `dbt build` finished with
   PASS=5 WARN=0 ERROR=0, and `dbt docs generate` wrote `target/catalog.json`.
3. The read-only open of the DuckDB file in a fresh process, after dbt had exited, succeeded.
4. `dbt source freshness` passed.
5. The offline Fabric compile (`--extra fabric`, dummy lowercase env values,
   `--no-populate-cache`) exited 0.
6. `uv sync --locked` afterwards removed the 12 Fabric extra packages again.

Confirmation run in plan 01-03 (2026-11-21), same platform and versions:

- `uv run dbt docs generate --static --profiles-dir .` exited 0 and wrote
  `target/static_index.html`, **2,678,279 bytes** (about 2.6 MB), a single self-contained file.
- `uv run demo` exited 0, and its `dbt build` passed 6 of 6 nodes, which now include the shared
  conversion fixture.
- `tests/conventions/test_harness.py` runs `dbt docs generate` into its own target path and then
  opens the built database with `duckdb.connect(path, read_only=True)` in a new pytest process.
  It passed. This is the fresh-process open from spike 2.

The confirmation agrees with the plan 01-02 evidence on every point. Nothing differed.

## Decision

`pyproject.toml` pins `duckdb==2.0.1` exactly. The 1.5.6 fallback **did not fire**: no step
failed for any DuckDB 2.0 specific reason.

## Consequences

- Database files written by duckdb 2.x cannot be opened by 1.x. The demo database is always
  rebuilt from the landing files, never migrated, so this costs nothing.
- DuckDB 2.0 uses the PEG parser by default and needs Python 3.11 or newer. The project
  requires Python 3.11 to 3.13 and uses 3.12.
- The exact pin means a duckdb upgrade is always a deliberate change with a rerun of the demo,
  not something a resolver does silently.
- Not exercised: a second writer against the same DuckDB file (the single-writer lock is
  DuckDB's own and is accepted as a backstop), and any Linux or Windows run.

## Revisit triggers

- A `dbt-duckdb` release that changes its supported duckdb range.
- A regression in the demo, the build, `docs generate` or the read-only open on a new duckdb
  patch.
- March 2027, when DuckDB 1.5 support ends, only if the pin ever has to move back to the 1.x line.

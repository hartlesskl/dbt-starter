# dbt-starter

A starting point for dbt teams, built on medallion layers. Raw files are landed as text in **landing**, typed and checked in **bronze**, split in **silver** into good rows and a **quarantine** of rejected rows with reason codes, and published in **gold**. The same models target DuckDB (the default, no account needed) and a Microsoft Fabric Lakehouse, in Spark SQL through dbt-fabricspark, run from your machine or by Fabric's native dbt job.

Click "Use this template", run the demo to see the conventions work on public data, then swap in your own sources.

## Status

Verified on Fabric 2026-11-25: dbt Core 1.11.8, dbt-fabricspark 1.12.2 and Fabric Runtime 2.0, in Fabric's native dbt job and self-hosted, see [docs/verification/fabric-2026-11-25.md](docs/verification/fabric-2026-11-25.md).

The label is dated: newer dbt Core, adapter or Fabric runtime versions are covered by the offline compile and the Spark scan only, and a later verification adds a new dated record.

## Quickstart

Every command below runs from the repo root, the directory that holds `dbt_project.yml`.

1. Install [uv](https://docs.astral.sh/uv/).
2. Install the locked environment. This also fetches Python 3.12, and it locks dbt Core 1.11, the line Fabric's native dbt job runs:

   ```bash
   uv sync
   ```

3. Run the demo. It downloads the example files once (two months of the example by default, see "What the demo builds"), lands them, drops and rebuilds the `bronze`, `silver` and `gold` schemas of its own DuckDB file (never `landing`), builds every layer with dbt on DuckDB and generates the static docs. It ends with a closing summary: a row count per layer, the reconciliation lines, the quarantine summary (each entity's quarantine rate and its worst file, shown against their levels) and where to open the docs:

   ```bash
   make demo
   ```

   Without make, run the same command directly: `uv run demo`. To load other months, pass them: `make demo MONTHS=2024-01..2024-06`, or `uv run demo --months 2024-01..2024-06` (see "What the demo builds").

   The exit status follows dbt, then the reconciliation lines. It is non-zero when `dbt build` fails, which is what a quarantine rate above its error level causes, because that entity's levels test fails: the demo returns dbt's code, still prints the closing summary with the skipped gold models listed, and does not regenerate the docs. It is 1 when a reconciliation line in the closing summary says FAIL, and 2 when a download or the landing fails before dbt runs. A rate above only its warn level prints `WARN` and still exits 0. The rate words in the summary only report: dbt's tests decide.

4. To run only the loader for one entity, use `uv run load <source> <entity> [--months MONTHS] [--from-dir DIR] [--allow-large] [--refresh]`. `--months` takes a list (`2024-01,2024-03`), a range (`2024-01..2024-03`) or both. An unknown source or entity, a malformed month, or a `--from-dir` that is not a folder exits with code 2 before anything is downloaded.

5. Run the checks. They need the built database and the installed dbt packages, so run the demo (step 3) first. The demo and the first `--extra fabric` install are the only steps that need the network. Once they are done, the checks make no download:

   ```bash
   make check
   ```

   Without make: `uv run --extra fabric pytest tests -q -m "not network"`. This installs dbt-fabricspark, the optional Fabric adapter, for the offline compile; run `uv sync --locked` afterwards to return to the default environment. On a fresh clone, before the demo has run, the checks that read the database stop with "Run `uv run demo` first".

## Run modes

The same models run three ways. DuckDB is the default and the only mode that needs no account. The two Fabric modes need a Fabric workspace, which [docs/fabric-setup.md](docs/fabric-setup.md) walks through from the trial to teardown.

1. **DuckDB, the default.** `make demo` (or `uv run demo`). It needs no account and no network beyond the first download.
2. **Self-hosted on a Fabric Lakehouse.** Run `make setup-fabric` once to create the schema-enabled Lakehouse, then `make demo-fabric` to land `2024-01`, build and print the closing summary, or `make build-fabric` to build only. Without make, use `uv run --locked --extra fabric setup-fabric`, `demo --target fabric` and `build-fabric`. They need `az login` and these variables, each explained in the guide's [environment variables](docs/fabric-setup.md#environment-variables) section:

   - `FABRIC_WORKSPACE_ID` and `FABRIC_LAKEHOUSE_ID`, both UUIDs, and `FABRIC_LAKEHOUSE`, the Lakehouse name. All three are required.
   - `FABRIC_SCHEMA`, default `dbo`, which must differ from the Lakehouse name.
   - `FABRIC_AUTHENTICATION`, default `CLI`.
   - `FABRIC_HIGH_CONCURRENCY`, default `true`, which uses the adapter's high-concurrency sessions. Set it to `false` for standard sessions. The opt-in CI workflow sets `false` because high-concurrency sessions refuse a service principal's token.

   They name any missing or malformed variable and run nothing. Otherwise `build-fabric` runs `dbt deps` and then `dbt build --target fabric`. The target is one schema-enabled Lakehouse that holds the `landing`, `bronze`, `silver` and `gold` schemas; `setup-fabric` creates it.
3. **Fabric's native dbt job.** One guide step: [the native dbt job](docs/fabric-setup.md#the-native-dbt-job) says how to point the job at this repository.

The Status line at the top says which versions were verified and when. The decisions behind the Fabric modes, and the checks only a live run can settle, are in [docs/decisions/0006-fabric-lakehouse-retarget.md](docs/decisions/0006-fabric-lakehouse-retarget.md).

## Contributing

Run `uv run pre-commit install` once per clone. After that, every commit runs the hooks on the files you stage. They block:

- secrets, found by gitleaks
- files over 1 MB
- Parquet, DuckDB and CSV files outside `seeds/`, and anything under `data/` except `data/.gitkeep`

They also run ruff on Python files, sqlfluff on staged model SQL, and the fast loader tests. To check the whole tree at once, run `uv run pre-commit run --all-files`.

The hooks are a first layer. The PR gate runs the full checks, so a commit that passes locally can still fail there.

## The PR gate

`make gate` (or `uv run --locked --extra fabric gate` without make) runs exactly what every pull request runs, in this order, and stops at the first step that fails, naming it (`gate failed at: <step>`):

1. `deps`: install the dbt packages.
2. `parse`: parse the project for DuckDB.
3. `compile-fabric`: compile every model and test for a Fabric Lakehouse (dbt-fabricspark) offline, with dummy connection values and no network.
4. `fixture-build`: land an invented fixture and run `dbt build` on it.
5. `scan`: the Spark SQL scan of the compiled Fabric SQL. It looks for DuckDB-only syntax (QUALIFY, star modifiers, UNION BY NAME, DISTINCT ON, the `::` operator, ASOF JOIN, USING SAMPLE), for functions outside the list the shims and models use, for banned types and for `information_schema`. It does not run Spark; the DuckDB build and this scan are the two offline halves, and the Fabric builds run the SQL for real.
6. `tests`: the convention and unit test suite, run against the fixture database.
7. `lint`: sqlfluff on the models, then ruff check and ruff format check.
8. `counts`: the exact-count check, which proves that the build reproduces the counts written next to the fixture rows.

The fixture is a small set of invented rows that plants at least one failure for every quarantine rule, so the gate proves the split into silver and quarantine without downloading anything. The gate builds its own database under `target/gate/` and never opens `data/dbt_starter.duckdb`. The `--extra fabric` option installs dbt-fabricspark, the optional Fabric adapter, for the compile step; run `uv sync --locked` afterwards to return to the default environment.

The fixture's quarantine rate is far above any real level by design, so the gate's fixture build and the test suites pass the dbt var `quarantine_rate_override` with both levels set to 1. The threshold tests then prove that each level fires on planted data (including an error that skips gold), the lifecycle test proves re-release and re-delivery, and a convention check keeps the override out of the demo, the nightly, the Pages build and the project config.

## What the demo builds

<!-- example:begin -->
The demo loads the NYC taxi zone lookup and two months of yellow taxi trips, 2024-01 and 2025-01, then ends with a closing summary. The two months are pinned on purpose: they come from two schema eras (2025-01 adds the `cbd_congestion_fee` column and carries far more Flex Fare trips), so real schema drift and a per-file quarantine rate both show on real data. They were chosen together with the quarantine levels, from measured rates, and they are small enough for a laptop (see "Measured cost and rates" below). Its first part is one line per layer for the zones, one line per layer for the trips, one line per gold relation, and a `warnings` line:

| Line | Relation |
| --- | --- |
| landing | `landing.nyc_taxi__taxi_zones` |
| bronze | `bronze.nyc_taxi__taxi_zones` |
| silver | `silver.taxi_zones` |
| quarantine | `silver.taxi_zones_quarantine` |
| gold | `gold.dim_zone` |
| landing | `landing.nyc_taxi__yellow_trips` |
| bronze | `bronze.nyc_taxi__yellow_trips` |
| silver | `silver.yellow_trips` |
| quarantine | `silver.yellow_trips_quarantine` |
| gold | `gold.dim_date` |
| gold | `gold.dim_vendor` |
| gold | `gold.dim_rate_code` |
| gold | `gold.dim_payment_type` |
| gold | `gold.fct_trips` |
| gold | `gold.agg_trips_daily` |
| warnings | `silver.yellow_trips` |

For the trips, bronze holds every landed row, and silver plus quarantine hold the same rows between them. The `warnings` line counts the silver trips with at least one warning. A warning does not remove a trip from silver; it records a reason code beside it.

The rest of the closing summary has three sections, each with the numbers recomputed by the printer's own read-only queries (the dbt tests that ran earlier stay the enforcing check):

- `reconciliation` has two kinds of line, each ending in `OK` or `FAIL`. One conservation line per entity shows that nothing was dropped between bronze and silver, for example `yellow_trips   bronze 6,439,850 = silver 6,439,594 + quarantine 256  OK`. One gold line shows that the daily aggregate adds up to the fact table, for example `gold           fct_trips 6,439,594 = sum(agg_trips_daily.trip_count) 6,439,594  OK`. The demo exits 1 if any line says `FAIL`, so the word and the exit status always agree.
- `quarantine summary` gives each entity's quarantine rate next to its levels, then its worst file (the file with the highest rate, and how many files the entity holds), then its top 5 error codes and top 3 warn codes with the number of rows that failed each, or `none` when no code of that severity fired. Each rate line ends in `OK`, `WARN` (above the warn level) or `ERROR` (above the error level), and a relation dbt skipped shows `skipped` instead of a count. For example:

  ```text
  yellow_trips   quarantine rate 0.004% (256 of 6,439,850 rows)  warn 0.1%  error 1%  OK
    worst file of 2: yellow_tripdata_2025-01.parquet  0.005% (166 of 3,475,226 rows)  OK
  ```

  The levels printed are the ones the levels test uses (see "Quarantine levels" below), and the printed words only report: dbt's tests decide whether the build passes.
- `docs` names the file to open offline, `target/static_index.html`, the alternative `uv run dbt docs serve` (which holds the DuckDB file open until you stop it), and the address your docs would have on GitHub Pages. The address comes from your `origin` remote and is left out when there is no GitHub remote. It is followed by a line saying the address is live only once Pages is enabled: a copy of the template publishes nothing until you opt in (see "Publish the docs on GitHub Pages" below).

### Measured cost and rates

Measured on 2026-11-23 (UTC) on an Apple M5 with 10 cores and 16 GB of memory, macOS 26.5.1, dbt-core 1.12.7, dbt-duckdb 1.11.1 and duckdb 2.0.1. The records are in `target/measure/` (gitignored) and the same figures are in ADR 0005 (`docs/decisions/0005-quarantine-thresholds.md`).

| Run | Wall time | Database file | Database plus downloads |
| --- | --- | --- | --- |
| First: new database file, files downloaded again with `--refresh` | 29.3 s | 2,658,152,448 bytes | 2.767 GB |
| Second: same file, downloads cached | 24.1 s | 2,755,670,016 bytes | 2.865 GB |

The budget is at most 5 minutes end to end and at most 6 GB on disk, and both runs are well inside it. The second run stays near the first because the demo drops and rebuilds its `bronze`, `silver` and `gold` schemas on every run, and never `landing`.

Each extra month adds about 1.33 GB of database (the first run's database file divided by its two months) and about 55 MB of download (the average of the two pinned files, 49,961,641 and 59,158,238 bytes), for about 1.38 GB in all. Months before 2022-01 download far more than that (see below).

The quarantine rate is the share of rows that failed an error rule. The levels are 0.1% (warn) and 1% (error), for both entities:

| Entity and file | Rows | Quarantined | Rate | Warn level | Error level |
| --- | --- | --- | --- | --- | --- |
| Trips, `yellow_tripdata_2024-01.parquet` | 2,964,624 | 90 | 0.0030% | 0.1% | 1% |
| Trips, `yellow_tripdata_2025-01.parquet` | 3,475,226 | 166 | 0.0048% | 0.1% | 1% |
| Trips, both files | 6,439,850 | 256 | 0.0040% | 0.1% | 1% |
| Zones, `taxi_zone_lookup.csv` | 265 | 0 | 0.0000% | 0.1% | 1% |

Every pinned rate is below one tenth of the warn level, so the demo is quiet: every entity line and worst-file line ends in `OK`. These figures are a dated measurement. The live check on the same rates is the levels test: a pinned file above its warn level prints `WARN` in the demo and in the nightly's job summary, and above its error level the build fails. When a publisher re-publishes a pinned month, or you change a rule, measure again and update this table.

### Other months

To land more months, pass a list or a range, with make or without it:

```bash
make demo MONTHS=2024-01..2024-06
uv run demo --months 2024-01..2024-06
```

Months before 2022-01 are 110 to 470 MB each and also need the opt-in, `--allow-large`, which `make` takes as `ALLOW_LARGE=1` (only the value 1 turns it on):

```bash
make demo MONTHS=2019-01..2019-03 ALLOW_LARGE=1
uv run demo --months 2019-01..2019-03 --allow-large
```

Landed months stay in `data/dbt_starter.duckdb` until you delete that file. The number in the worst-file line (`worst file of K`) is how many files each entity holds, so a month left from an earlier run shows up there as a count above 2. Deleting the database file returns you to exactly the pinned pair on the next `make demo`. The files in `data/raw/` are only a download cache and are not landed unless you ask for their months.

To land files you already downloaded, put them in one folder and point the loader at it. The folder needs `taxi_zone_lookup.csv` for the demo, and one `yellow_tripdata_YYYY-MM.parquet` per month. Nothing is downloaded and nothing is copied into `data/raw/`:

```bash
uv run load nyc_taxi taxi_zones --from-dir DIR
uv run load nyc_taxi yellow_trips --from-dir DIR --months 2024-01
uv run demo --from-dir DIR
```

Source: NYC Taxi and Limousine Commission (TLC) Trip Record Data, <https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page>. The data is downloaded at run time into `data/raw/` and is never committed.
<!-- example:end -->

## Quarantine levels

A quarantine split can drift without anyone noticing: a bad month can quarantine a large share of its rows and the build stays green, because every row still sits in silver or in quarantine. So each entity carries levels for its quarantine rate. The rate is the rows that failed an error rule over all rows; the warned rate is never thresholded, because warned rows are usable and must never stop gold.

- Each entity's silver model carries one `starter.quarantine_rate_levels` test in the entity's own YAML, with the arguments `all_rows_model` (the entity's bronze model), `warn_above` and `error_above`, both as fractions of rows. There is no project-wide default.
- The rate is checked at two grains with the same levels: per source file and over all files together, so one bad month cannot hide inside a multi-month average.
- A rate strictly above `warn_above` makes the build warn. A rate above `error_above` fails the test, and `dbt build` then skips that entity's gold models while silver and quarantine still build, so the rejected rows stay countable and the fix is visible. Rates are compared at 6 decimal places.
- The second parent of the test must be the bronze model, never the quarantine model; otherwise dbt would not skip gold (see `docs/decisions/0005-quarantine-thresholds.md`). A convention check fails any entity registered in `dq_entities()` that has no levels, or a level that could never fire or would always fire.
- The demo prints each rate next to its levels and marks it `OK`, `WARN` or `ERROR`. The words only report. dbt's tests and dbt's exit status decide.

<!-- example:begin -->
The example sets both levels the same for its two entities, in `models/silver/nyc_taxi/_models.yml`: warn above 0.1% (`warn_above: 0.001`) and error above 1% (`error_above: 0.01`). They were chosen from measured rates (see "Measured cost and rates"): the worst pinned file sits at 0.0048%, about 21 times under the warn level and 209 times under the error level, so the demo is quiet. They are still tight enough to catch a real fault: one bad zone out of 265 is 0.38% and already warns, and three fail the build.
<!-- example:end -->

## Re-release after a fix

A rule fix or a code-mapping fix releases rows by itself. Bronze, silver and quarantine are rebuilt in full from landing on every build, so a row that was quarantined for a reason that no longer applies lands in silver on the next `uv run dbt build`, with no manual step and no extra command. After a fix, watch these lines of the closing summary (`uv run demo` prints them): the quarantine count falls, the silver count grows by the same amount, and `bronze = silver + quarantine` stays `OK`.

<!-- example:begin -->
A walkthrough with the example data. It was run on a copy of the project against the measured two-month database (see "Measured cost and rates"), never on the working tree.

1. A code the seed does not know yet. Delete the line `0,Flex Fare trip` from `seeds/nyc_taxi/nyc_taxi__payment_types.csv` and run `uv run demo`. Every Flex Fare trip now has a payment type that the list does not hold, so it is quarantined: 680,400 of the 6,439,850 trips (10.57%) instead of 256, which is far above the error level. The trip levels test fails, `dbt build` skips `gold__fct_trips`, `gold__agg_trips_daily` and the 12 tests that read them, silver (5,759,450 trips) and quarantine still build, the summary prints `ERROR` on the trip rate line and `skipped` for the gold relations, and the demo exits 1 without generating the docs.
2. The fix. Put the line back and run `uv run dbt build`. Nothing else is needed, because every layer rebuilds from landing: the exit code is 0, silver is back to 6,439,594 trips, quarantine to 256, and the gold models are built again. Run `uv run demo` to see the summary lines: quarantine fell by 680,144, silver grew by the same number, and `yellow_trips   bronze 6,439,850 = silver 6,439,594 + quarantine 256  OK`.
3. A rule fix. The longest allowed trip, 24 hours, is written once, in `nyc_taxi__max_trip_seconds()` in `macros/rules/nyc_taxi/yellow_trips.sql`. Raise it, and the trips that failed only `TRIP_DURATION_TOO_LONG` (the rule fired for 36 trips on the two pinned months) move to silver on the next build. The unit test `trip_time_rules` in `models/bronze/nyc_taxi/_unit_tests.yml` pins the limit with a trip of exactly 86,400 seconds that passes and one of 86,401 that fails, so a build stops at that unit test until you move its two boundary rows with the limit. That is the test doing its job.
4. A corrected file. A file that is delivered again under the same name replaces the earlier copy in landing, so its rows are never counted twice, and the loader prints one line when the content changed: `Re-landed <file>: content changed, rows A -> B, batch X -> Y`. A file that is identical to the one already landed prints nothing. A corrected file delivered under a new name lands as a separate file and is counted alongside the old one.
<!-- example:end -->

## Publish the docs on GitHub Pages

A copy of the template publishes nothing by default. The Pages workflow (`.github/workflows/pages.yml`) builds the example on DuckDB and deploys the static docs, but both of its jobs are skipped, and the workflow stays green, until you opt in:

1. In the repository settings, open Pages and set the source to **GitHub Actions**.
2. In Settings, Secrets and variables, Actions, add the repository variable `ENABLE_PAGES` with the value `true`.
3. Push to `main`, or run the `pages` workflow by hand.

The site then lives at `https://<owner>.github.io/<repo>/` with the dbt docs under `dbt-docs/`; the demo's closing summary prints that address, conditionally, from your `origin` remote. The workflow runs on pushes to `main` and deploys only from `main`, so a copy whose default branch has another name deploys nothing until you change `branches` and the `github.ref` check in the workflow. `make site` assembles the same site locally into `site/` from the static docs the demo writes.

## Run the example nightly

`.github/workflows/nightly.yml` builds the pinned example months every night at 05:23 UTC, with the downloads cached, and then, when the demo exited 0, runs the same checks as `make check`. A copy of the template runs nothing until you opt in:

1. In the repository settings, open Secrets and variables, Actions, and add the repository variable `ENABLE_NIGHTLY` with the value `true` (the same place as `ENABLE_PAGES`).
2. Wait for the schedule, or run the `nightly` workflow by hand (`workflow_dispatch`).

A failure turns the run red, and the job summary shows the demo's closing summary, with each rate next to its levels, or the reason the demo stopped. The nightly is never a required check and it opens no issues. GitHub runs schedules only on the default branch, and pauses the schedule of a public repository after 60 days without activity. The cache key comes from the file names, so bump `CACHE_VERSION` in `src/starter_cli/site.py` when the publisher re-publishes a pinned month under the same name.

## Troubleshooting

### Download blocked or unavailable (HTTP 403 or 404)

The data host's CDN answers 403 both when a month is not published yet and when it is rate limiting a machine, and some networks, VPNs and cloud hosts are blocked outright. The loader makes up to 4 attempts, waiting about 2, 4 and 8 seconds between them (and as long as the server's `Retry-After` asks, up to 60 seconds). If every attempt fails it stops with a message that names the URL, and `demo` stops before dbt runs.

What to do:

- Retry in a few minutes, or from another network.
- Download the files another way and land them with `--from-dir DIR` (the file names are listed under "What the demo builds").
- Months before 2022-01 are large downloads and need `--allow-large`.

A download is checked before it is kept: the HTTP status, the byte count against `Content-Length`, and the file's magic bytes. A partial file or an error page is never cached. Each cached file has a `.meta.json` next to it with its URL, size, `Last-Modified` and `ETag`. After a successful download the file is reused on every later run, after a check of its magic bytes and recorded size. If that check fails, delete the file (and its `.meta.json`) or run with `--refresh`. `--refresh` downloads again and prints the old and new size and `Last-Modified`; a refresh that fails keeps the previous copy. Never commit a downloaded file.

### Command run outside the repo root

The commands find the project by looking for `dbt_project.yml`. Run them from the directory that holds that file.

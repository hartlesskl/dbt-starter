# dbt-starter

A starting point for dbt teams, built on medallion layers. Raw files are landed as text in **landing**, typed and checked in **bronze**, split in **silver** into good rows and a **quarantine** of rejected rows with reason codes, and published in **gold**. The same models target DuckDB (the default, no account needed) and Microsoft Fabric Warehouse.

Click "Use this template", run the demo to see the conventions work on public data, then swap in your own sources.

## Status

Fabric: compile-checked only

The project compiles offline for Fabric Warehouse but has not run on a live Warehouse. A dated verification record will replace this label once that run exists.

## Quickstart

Every command below runs from the repo root, the directory that holds `dbt_project.yml`.

1. Install [uv](https://docs.astral.sh/uv/).
2. Install the locked environment. This also fetches Python 3.12:

   ```bash
   uv sync
   ```

3. Run the demo. It downloads the example files once, lands them, builds every layer with dbt on DuckDB and generates the static docs. It ends with a closing summary: a row count per layer, the reconciliation lines, the quarantine summary and where to open the docs:

   ```bash
   make demo
   ```

   Without make, run the same command directly: `uv run demo`. It exits 1 if a reconciliation line in the closing summary says FAIL.

4. To run only the loader for one entity, use `uv run load <source> <entity> [--months MONTHS] [--from-dir DIR] [--allow-large] [--refresh]`. `--months` takes a list (`2024-01,2024-03`), a range (`2024-01..2024-03`) or both. An unknown source or entity, a malformed month, or a `--from-dir` that is not a folder exits with code 2 before anything is downloaded.

5. Run the checks. They need the built database and the installed dbt packages, so run the demo (step 3) first. The demo and the first `--extra fabric` install are the only steps that need the network. Once they are done, the checks make no download:

   ```bash
   make check
   ```

   Without make: `uv run --extra fabric pytest tests -q -m "not network"`. This installs the optional Fabric packages for the compile checks; run `uv sync --locked` afterwards to return to the default environment. On a fresh clone, before the demo has run, the checks that read the database stop with "Run `uv run demo` first".

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
3. `compile-fabric`: compile every model and test for Fabric Warehouse offline, with dummy connection values.
4. `fixture-build`: land an invented fixture and run `dbt build` on it.
5. `scan`: the SQL scan for syntax Fabric rejects, on the compiled Fabric SQL.
6. `tests`: the convention and unit test suite, run against the fixture database.
7. `lint`: sqlfluff on the models, then ruff check and ruff format check.
8. `counts`: the exact-count check, which proves that the build reproduces the counts written next to the fixture rows.

The fixture is a small set of invented rows that plants at least one failure for every quarantine rule, so the gate proves the split into silver and quarantine without downloading anything. The gate builds its own database under `target/gate/` and never opens `data/dbt_starter.duckdb`. The `--extra fabric` option installs the optional Fabric packages for the compile step; run `uv sync --locked` afterwards to return to the default environment.

## What the demo builds

<!-- example:begin -->
The demo loads the NYC taxi zone lookup and one month of yellow taxi trips (2024-01 by default), then ends with a closing summary. Its first part is one line per layer for the zones, one line per layer for the trips, one line per gold relation, and a `warnings` line:

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

- `reconciliation` has two kinds of line, each ending in `OK` or `FAIL`. One conservation line per entity shows that nothing was dropped between bronze and silver, for example `yellow_trips   bronze 2,964,624 = silver 2,964,534 + quarantine 90  OK`. One gold line shows that the daily aggregate adds up to the fact table, for example `gold           fct_trips 2,964,534 = sum(agg_trips_daily.trip_count) 2,964,534  OK`. The demo exits 1 if any line says `FAIL`, so the word and the exit status always agree.
- `quarantine summary` gives each entity's quarantine rate, then its top 5 error codes and top 3 warn codes with the number of rows that failed each, or `none` when no code of that severity fired. It reads `silver.dq_quarantine_summary`.
- `docs` names the file to open offline, `target/static_index.html`, the alternative `uv run dbt docs serve` (which holds the DuckDB file open until you stop it), and the address your docs would have on GitHub Pages. The address comes from your `origin` remote and is left out when there is no GitHub remote. It is followed by a line saying the address is live only once Pages is enabled: a copy of the template publishes nothing until you opt in (see "Publish the docs on GitHub Pages" below).

To land more months, pass a list or a range. Months before 2022-01 are 110 to 470 MB each and also need `--allow-large`:

```bash
uv run demo --months 2024-01..2024-03
```

To land files you already downloaded, put them in one folder and point the loader at it. The folder needs `taxi_zone_lookup.csv` for the demo, and one `yellow_tripdata_YYYY-MM.parquet` per month. Nothing is downloaded and nothing is copied into `data/raw/`:

```bash
uv run load nyc_taxi taxi_zones --from-dir DIR
uv run load nyc_taxi yellow_trips --from-dir DIR --months 2024-01
uv run demo --from-dir DIR
```

Source: NYC Taxi and Limousine Commission (TLC) Trip Record Data, <https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page>. The data is downloaded at run time into `data/raw/` and is never committed.
<!-- example:end -->

## Publish the docs on GitHub Pages

A copy of the template publishes nothing by default. The Pages workflow (`.github/workflows/pages.yml`) builds the example on DuckDB and deploys the static docs, but both of its jobs are skipped, and the workflow stays green, until you opt in:

1. In the repository settings, open Pages and set the source to **GitHub Actions**.
2. In Settings, Secrets and variables, Actions, add the repository variable `ENABLE_PAGES` with the value `true`.
3. Push to `main`, or run the `pages` workflow by hand.

The site then lives at `https://<owner>.github.io/<repo>/` with the dbt docs under `dbt-docs/`; the demo's closing summary prints that address, conditionally, from your `origin` remote. The workflow runs on pushes to `main` and deploys only from `main`, so a copy whose default branch has another name deploys nothing until you change `branches` and the `github.ref` check in the workflow. `make site` assembles the same site locally into `site/` from the static docs the demo writes.

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

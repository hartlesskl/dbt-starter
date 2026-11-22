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

3. Run the demo. It downloads the example files once, lands them, builds every layer with dbt on DuckDB, generates the docs and prints a row count per layer:

   ```bash
   make demo
   ```

   Without make, run the same command directly: `uv run demo`.

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

## What the demo builds

<!-- example:begin -->
The demo loads the NYC taxi zone lookup and one month of yellow taxi trips (2024-01 by default), then prints one line per layer for the zones, one line per layer for the trips, and a `warnings` line:

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
| warnings | `silver.yellow_trips` |

For the trips, bronze holds every landed row, and silver plus quarantine hold the same rows between them. The `warnings` line counts the silver trips with at least one warning. A warning does not remove a trip from silver; it records a reason code beside it.

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

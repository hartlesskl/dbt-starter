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

3. Run the demo. It downloads the example file once, lands it, builds every layer with dbt on DuckDB, generates the docs and prints a row count per layer:

   ```bash
   make demo
   ```

   Without make, run the same command directly: `uv run demo`.

4. To run only the loader for one entity, use `uv run load <source> <entity> [--refresh]`. An unknown source or entity exits with code 2 and lists the valid choices.

5. Run the checks. They need the built database and the installed dbt packages, so run the demo (step 3) first. The demo and the first `--extra fabric` install are the only steps that need the network. Once they are done, the checks make no download:

   ```bash
   make check
   ```

   Without make: `uv run --extra fabric pytest tests -q -m "not network"`. This installs the optional Fabric packages for the compile checks; run `uv sync --locked` afterwards to return to the default environment. On a fresh clone, before the demo has run, the checks that read the database stop with "Run `uv run demo` first".

## What the demo builds

<!-- example:begin -->
The demo loads the NYC taxi zone lookup and prints one line per layer, from landing to gold:

| Layer | Relation |
| --- | --- |
| landing | `landing.nyc_taxi__taxi_zones` |
| bronze | `bronze.nyc_taxi__taxi_zones` |
| silver | `silver.taxi_zones` |
| quarantine | `silver.taxi_zones_quarantine` |
| gold | `gold.dim_zone` |

Source: NYC Taxi and Limousine Commission (TLC) Trip Record Data, <https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page>. The data is downloaded at run time into `data/raw/` and is never committed.
<!-- example:end -->

## Troubleshooting

### Download blocked or unavailable (HTTP 403 or 404)

Some networks, VPNs and cloud hosts are blocked by the data host's CDN. The loader makes one attempt and stops with a message that names the URL. Retry from another network. After one successful download the file is cached under `data/raw/` and reused on every later run, and `--refresh` forces a new download. Never commit the downloaded file.

### Command run outside the repo root

The commands find the project by looking for `dbt_project.yml`. Run them from the directory that holds that file.

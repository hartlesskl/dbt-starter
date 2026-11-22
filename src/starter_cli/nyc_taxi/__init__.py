"""The example source: NYC TLC trip record data. Everything source-specific lives here."""

from __future__ import annotations

from starter_cli.landing import EntitySpec
from starter_cli.nyc_taxi.fixture import write_fixture

SOURCE = "nyc_taxi"

# The public data the published docs credit on their root page (the project's data terms): the
# name and the https link that the data publisher gives for it. `uv run site` refuses a source
# that declares none, so docs of undeclared data are never assembled.
DOCS_ATTRIBUTION = (
    "NYC Taxi and Limousine Commission (TLC) Trip Record Data",
    "https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page",
)

__all__ = [
    "DOCS_ATTRIBUTION",
    "ENTITIES",
    "REPORT_CONSERVATION",
    "REPORT_GOLD",
    "REPORT_RELATIONS",
    "REPORT_WARNINGS",
    "SOURCE",
    "write_fixture",
]

ENTITIES: dict[str, EntitySpec] = {
    "taxi_zones": EntitySpec(
        source=SOURCE,
        entity="taxi_zones",
        url="https://d37ci6vzurychx.cloudfront.net/misc/taxi_zone_lookup.csv",
        file_name="taxi_zone_lookup.csv",
        columns=("locationid", "borough", "zone", "service_zone"),
        header_prefix=b'"LocationID"',
    ),
    # One Parquet file per month, named yellow_tripdata_YYYY-MM.parquet. Column spellings and
    # sets drift between months (Airport_fee, cbd_congestion_fee, request_source), so a declared
    # column a month lacks lands as NULL and a column nobody declared is kept as text (D-11).
    "yellow_trips": EntitySpec(
        source=SOURCE,
        entity="yellow_trips",
        url="https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_{month}.parquet",
        file_name="yellow_tripdata_{month}.parquet",
        columns=(
            "vendorid",
            "tpep_pickup_datetime",
            "tpep_dropoff_datetime",
            "passenger_count",
            "trip_distance",
            "ratecodeid",
            "store_and_fwd_flag",
            "pulocationid",
            "dolocationid",
            "payment_type",
            "fare_amount",
            "extra",
            "mta_tax",
            "tip_amount",
            "tolls_amount",
            "improvement_surcharge",
            "total_amount",
            "congestion_surcharge",
            "airport_fee",
            "cbd_congestion_fee",
        ),
        header_prefix=b"PAR1",
        file_format="parquet",
        default_months=("2024-01",),
        null_missing_columns=True,
        # Files before 2022 are 110 to 470 MB each, so they need --allow-large.
        large_before="2022-01",
    ),
}

# Ordered (label, relation) pairs the demo printer shows: the zone lines, the trip lines, then the
# gold lines.
REPORT_RELATIONS: list[tuple[str, str]] = [
    ("landing", "landing.nyc_taxi__taxi_zones"),
    ("bronze", "bronze.nyc_taxi__taxi_zones"),
    ("silver", "silver.taxi_zones"),
    ("quarantine", "silver.taxi_zones_quarantine"),
    ("gold", "gold.dim_zone"),
    ("landing", "landing.nyc_taxi__yellow_trips"),
    ("bronze", "bronze.nyc_taxi__yellow_trips"),
    ("silver", "silver.yellow_trips"),
    ("quarantine", "silver.yellow_trips_quarantine"),
    ("gold", "gold.dim_date"),
    ("gold", "gold.dim_vendor"),
    ("gold", "gold.dim_rate_code"),
    ("gold", "gold.dim_payment_type"),
    ("gold", "gold.fct_trips"),
    ("gold", "gold.agg_trips_daily"),
]

# Silver relations with a `dq_warning_count` column: the demo counts the rows with a warning.
REPORT_WARNINGS: list[str] = ["silver.yellow_trips"]

# Per entity, the bronze, silver and quarantine relations the printer checks as bronze = silver +
# quarantine (nothing dropped between bronze and silver).
REPORT_CONSERVATION: dict[str, tuple[str, str, str]] = {
    "taxi_zones": (
        "bronze.nyc_taxi__taxi_zones",
        "silver.taxi_zones",
        "silver.taxi_zones_quarantine",
    ),
    "yellow_trips": (
        "bronze.nyc_taxi__yellow_trips",
        "silver.yellow_trips",
        "silver.yellow_trips_quarantine",
    ),
}

# (fact, aggregate, aggregate count column): the printer checks fact rows = sum of the count column.
REPORT_GOLD: list[tuple[str, str, str]] = [
    ("gold.fct_trips", "gold.agg_trips_daily", "trip_count"),
]

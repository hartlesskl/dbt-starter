"""The example source: NYC TLC trip record data. Everything source-specific lives here."""

from __future__ import annotations

from starter_cli.landing import EntitySpec
from starter_cli.nyc_taxi.fixture import write_fixture

SOURCE = "nyc_taxi"

__all__ = ["ENTITIES", "REPORT_RELATIONS", "REPORT_WARNINGS", "SOURCE", "write_fixture"]

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

# Ordered (label, relation) pairs the demo printer shows: the zone lines, then the trip lines.
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
]

# Silver relations with a `dq_warning_count` column: the demo counts the rows with a warning.
REPORT_WARNINGS: list[str] = ["silver.yellow_trips"]

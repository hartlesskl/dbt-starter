"""The example source: NYC TLC trip record data. Everything source-specific lives here."""

from __future__ import annotations

from starter_cli.landing import EntitySpec

SOURCE = "nyc_taxi"

ENTITIES: dict[str, EntitySpec] = {
    "taxi_zones": EntitySpec(
        source=SOURCE,
        entity="taxi_zones",
        url="https://d37ci6vzurychx.cloudfront.net/misc/taxi_zone_lookup.csv",
        file_name="taxi_zone_lookup.csv",
        columns=("locationid", "borough", "zone", "service_zone"),
        header_prefix=b'"LocationID"',
    ),
}

# Ordered (label, relation) pairs the demo printer shows, one line per layer.
REPORT_RELATIONS: list[tuple[str, str]] = [
    ("landing", "landing.nyc_taxi__taxi_zones"),
    ("bronze", "bronze.nyc_taxi__taxi_zones"),
    ("silver", "silver.taxi_zones"),
    ("quarantine", "silver.taxi_zones_quarantine"),
    ("gold", "gold.dim_zone"),
]

"""Fixtures for the example source: a planted trip build on a temporary database.

The build goes through the real loader (`load_entity(..., from_dir=...)`) and the real dbt
project, on a DuckDB file chosen through `DBT_DUCKDB_PATH`, so a planted row never reaches the
demo database (T-02-03). Nothing here downloads anything.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import NamedTuple
from unittest import mock

import duckdb
import pytest
from support.dbt_cli import REPO_ROOT, run_dbt

from starter_cli.landing import load_entity
from starter_cli.nyc_taxi import ENTITIES
from starter_cli.project import duckdb_path

ZONE_HEADER = '"LocationID","Borough","Zone","service_zone"'
# Ids 1, 2, 264 and 265 are valid zones (264 and 265 are the published unknown and outside-NYC
# zones). The `abc` row only has to exist: it is quarantined in the zone pipeline.
ZONE_ROWS = [
    "1,EWR,Newark Airport,EWR",
    "2,Queens,Jamaica Bay,Boro Zone",
    "264,Unknown,N/A,N/A",
    "265,N/A,Outside of NYC,N/A",
    "abc,Queens,Bad Id,Boro Zone",
]

# Published-style column spellings and types. The 2024 files have no cbd_congestion_fee.
TRIP_COLUMNS_2024: list[tuple[str, str]] = [
    ("VendorID", "INTEGER"),
    ("tpep_pickup_datetime", "TIMESTAMP"),
    ("tpep_dropoff_datetime", "TIMESTAMP"),
    ("passenger_count", "BIGINT"),
    ("trip_distance", "DOUBLE"),
    ("RatecodeID", "BIGINT"),
    ("store_and_fwd_flag", "VARCHAR"),
    ("PULocationID", "INTEGER"),
    ("DOLocationID", "INTEGER"),
    ("payment_type", "BIGINT"),
    ("fare_amount", "DOUBLE"),
    ("extra", "DOUBLE"),
    ("mta_tax", "DOUBLE"),
    ("tip_amount", "DOUBLE"),
    ("tolls_amount", "DOUBLE"),
    ("improvement_surcharge", "DOUBLE"),
    ("total_amount", "DOUBLE"),
    ("congestion_surcharge", "DOUBLE"),
    ("Airport_fee", "DOUBLE"),
]
TRIP_COLUMNS_2026: list[tuple[str, str]] = [
    *TRIP_COLUMNS_2024,
    ("cbd_congestion_fee", "DOUBLE"),
    ("request_source", "VARCHAR"),
]


def _trip(
    pickup: datetime | None,
    dropoff: datetime | None,
    *,
    vendor: int = 1,
    distance: float = 2.5,
    fare: float = 12.5,
    airport_fee: float = 0.0,
) -> tuple:
    """One trip that breaks no rule except the one its pickup and dropoff plant.

    Zones 1 and 2 are valid in the zone file above; every amount is positive; the code
    columns hold values that are valid in the TLC data dictionary.
    """
    return (
        vendor,
        pickup,
        dropoff,
        1,  # passenger_count
        distance,
        1,  # RatecodeID
        "N",
        1,  # PULocationID
        2,  # DOLocationID
        1,  # payment_type
        fare,
        1.0,  # extra
        0.5,  # mta_tax
        2.0,  # tip_amount
        0.0,  # tolls_amount
        1.0,  # improvement_surcharge
        fare + 4.5,
        2.5,  # congestion_surcharge
        airport_fee,
    )


# yellow_tripdata_2024-01.parquet: a valid trip, a zero-length trip, a dropoff before the
# pickup, and a trip with no pickup. Row order is the file order, so _source_row_number is 1..4.
TRIPS_2024_01 = [
    _trip(datetime(2024, 1, 15, 8, 0, 0), datetime(2024, 1, 15, 8, 10, 0), airport_fee=0.0),
    _trip(
        datetime(2024, 1, 16, 9, 0, 0),
        datetime(2024, 1, 16, 9, 0, 0),
        vendor=2,
        distance=0.8,
        fare=7.0,
        airport_fee=1.75,
    ),
    _trip(
        datetime(2024, 1, 17, 10, 0, 0),
        datetime(2024, 1, 17, 9, 59, 59),
        vendor=2,
        distance=3.1,
        fare=15.0,
    ),
    _trip(None, datetime(2024, 1, 18, 11, 0, 0), vendor=2, distance=4.2, fare=18.0),
]

# yellow_tripdata_2026-09.parquet: one valid trip, in a file that has two columns the 2024
# file lacks (cbd_congestion_fee is declared, request_source is not).
TRIPS_2026_09 = [
    (
        *_trip(datetime(2026, 9, 10, 12, 0, 0), datetime(2026, 9, 10, 12, 20, 0), fare=21.0),
        0.75,
        "app",
    ),
]


class PlantedBuild(NamedTuple):
    database: Path
    target: Path


def write_parquet(path: Path, columns: list[tuple[str, str]], rows: list[tuple]) -> None:
    """Write `rows` as typed Parquet through DuckDB, keeping the column spellings as given."""
    ddl = ", ".join(f'"{name}" {sql_type}' for name, sql_type in columns)
    placeholders = ", ".join("?" for _ in columns)
    con = duckdb.connect()
    try:
        con.execute(f"create table trips ({ddl})")
        con.executemany(f"insert into trips values ({placeholders})", rows)
        target = str(path).replace("'", "''")
        con.execute(f"copy trips to '{target}' (format parquet)")
    finally:
        con.close()


def write_planted_files(folder: Path) -> None:
    """Write the zone CSV and both trip files into `folder`."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "taxi_zone_lookup.csv").write_text("\n".join([ZONE_HEADER, *ZONE_ROWS]) + "\n")
    write_parquet(folder / "yellow_tripdata_2024-01.parquet", TRIP_COLUMNS_2024, TRIPS_2024_01)
    write_parquet(folder / "yellow_tripdata_2026-09.parquet", TRIP_COLUMNS_2026, TRIPS_2026_09)


@pytest.fixture(scope="session")
def planted_trips_db(
    dbt_packages_ready: None, tmp_path_factory: pytest.TempPathFactory
) -> PlantedBuild:
    """Land the planted files from a folder, run `dbt build`, and return where it all is."""
    demo_database = duckdb_path(REPO_ROOT)
    work = tmp_path_factory.mktemp("planted_trips")
    folder = work / "files"
    database = work / "planted.duckdb"
    target = work / "target"
    assert database.resolve() != demo_database.resolve()

    write_planted_files(folder)
    with mock.patch.dict(os.environ, {"DBT_DUCKDB_PATH": str(database)}):
        load_entity(ENTITIES["taxi_zones"], REPO_ROOT, from_dir=folder)
        load_entity(ENTITIES["yellow_trips"], REPO_ROOT, from_dir=folder)
    run_dbt(
        ["build", "--target-path", str(target)],
        env={"DBT_DUCKDB_PATH": str(database)},
    )
    return PlantedBuild(database=database, target=target)

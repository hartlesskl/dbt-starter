"""Synthetic rows that plant one failure per quarantine rule, with their expected counts.

Every value here is invented. No row comes from a TLC file, and none is a sample of one. The
file names follow the published pattern (`yellow_tripdata_YYYY-MM.parquet`) because the
out-of-month rule reads the month from `_source_file`.

Each row carries a `plants` tuple: the rule codes the row is meant to fail. The expected counts
(`EXPECTED`) are written out by hand next to the rows, and `tag_counts()` derives the same
counts from the tags, so a test can show that the two agree before any database is built.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple

import duckdb

ZONE_FILE = "taxi_zone_lookup.csv"
ZONE_HEADER = '"LocationID","Borough","Zone","service_zone"'


class Row(NamedTuple):
    """One planted row: its values by landing column name, and the rule codes it fails."""

    values: dict[str, object]
    plants: tuple[str, ...]


class TripFile(NamedTuple):
    """One monthly Parquet file: published column spellings with types, and its rows."""

    name: str
    columns: tuple[tuple[str, str, str], ...]  # (landing name, published spelling, DuckDB type)
    rows: tuple[Row, ...]


_TYPES = {
    "vendorid": "INTEGER",
    "tpep_pickup_datetime": "TIMESTAMP",
    "tpep_dropoff_datetime": "TIMESTAMP",
    "passenger_count": "BIGINT",
    "trip_distance": "DOUBLE",
    "ratecodeid": "BIGINT",
    "store_and_fwd_flag": "VARCHAR",
    "pulocationid": "INTEGER",
    "dolocationid": "INTEGER",
    "payment_type": "BIGINT",
    "fare_amount": "DOUBLE",
    "extra": "DOUBLE",
    "mta_tax": "DOUBLE",
    "tip_amount": "DOUBLE",
    "tolls_amount": "DOUBLE",
    "improvement_surcharge": "DOUBLE",
    "total_amount": "DOUBLE",
    "congestion_surcharge": "DOUBLE",
    "airport_fee": "DOUBLE",
    "cbd_congestion_fee": "DOUBLE",
}
_PUBLISHED = {
    "vendorid": "VendorID",
    "ratecodeid": "RatecodeID",
    "pulocationid": "PULocationID",
    "dolocationid": "DOLocationID",
    "airport_fee": "Airport_fee",
}


def _columns(
    types: dict[str, str] | None = None,
    *,
    cbd: bool = True,
    spelled: dict[str, str] | None = None,
    extra: tuple[tuple[str, str, str], ...] = (),
) -> tuple[tuple[str, str, str], ...]:
    """The column list of one file era: the declared columns, then any extra columns."""
    types = types or _TYPES
    spelled = {**_PUBLISHED, **(spelled or {})}
    declared = [
        (name, spelled.get(name, name), sql_type)
        for name, sql_type in types.items()
        if cbd or name != "cbd_congestion_fee"
    ]
    return (*declared, *extra)


def _trip(month_start: datetime, n: int, *plants: str, **over: object) -> Row:
    """A trip that breaks no rule, unless `plants` says which it breaks.

    `n` is the row's place in its file and makes its pickup unique (so two rows are never
    exact duplicates by accident). `pickup` and `dropoff` override the times; any other
    keyword replaces the landing column of that name.
    """
    pickup = over.pop("pickup", month_start + timedelta(hours=1, minutes=7 * n))
    dropoff = over.pop("dropoff", pickup + timedelta(minutes=10))  # type: ignore[operator]
    values: dict[str, object] = {
        "vendorid": 1,
        "tpep_pickup_datetime": pickup,
        "tpep_dropoff_datetime": dropoff,
        "passenger_count": 1,
        "trip_distance": 2.5,
        "ratecodeid": 1,
        "store_and_fwd_flag": "N",
        "pulocationid": 1,
        "dolocationid": 2,
        "payment_type": 1,
        "fare_amount": 12.5,
        "extra": 1.0,
        "mta_tax": 0.5,
        "tip_amount": 2.0,
        "tolls_amount": 0.0,
        "improvement_surcharge": 1.0,
        "total_amount": 19.5,
        "congestion_surcharge": 2.5,
        "airport_fee": 0.0,
        "cbd_congestion_fee": None,
    }
    unknown = set(over) - set(values)
    if unknown:
        raise KeyError(f"not a landing column: {sorted(unknown)}")
    values.update(over)
    return Row(values, plants)


JAN_2024 = datetime(2024, 1, 1)

TRIP_FILES: tuple[TripFile, ...] = (
    TripFile("yellow_tripdata_2024-01.parquet", _columns(cbd=False), (_trip(JAN_2024, 1),)),
)

# (id, borough, zone, service_zone) as the CSV holds them; an empty string is an empty cell.
ZONE_ROWS: tuple[tuple[tuple[str, str, str, str], tuple[str, ...]], ...] = (
    (("1", "EWR", "Newark Airport", "EWR"), ()),
    (("2", "Queens", "Jamaica Bay", "Boro Zone"), ()),
    (("3", "Manhattan", "Alphabet City", "Yellow Zone"), ()),
    (("264", "Unknown", "N/A", "N/A"), ()),
    (("265", "N/A", "Outside of NYC", "N/A"), ()),
)

EXPECTED: dict = {
    "taxi_zones": {"landing": 5, "silver": 5, "quarantine": 0, "rules": {}},
    "yellow_trips": {
        "landing": 1,
        "silver": 1,
        "quarantine": 0,
        "silver_warned": 0,
        "rules": {},
    },
    "warn_tests": 1,
}

REVERSE_PAIR_PICKUP = "2025-01-20 14:00:00"


def row_tags() -> dict[str, dict[tuple[str, int], tuple[str, ...]]]:
    """The planted codes of every row, by entity, keyed by (file name, 1-based row number)."""
    zones = {(ZONE_FILE, number): plants for number, (_, plants) in enumerate(ZONE_ROWS, start=1)}
    trips = {
        (file.name, number): row.plants
        for file in TRIP_FILES
        for number, row in enumerate(file.rows, start=1)
    }
    return {"taxi_zones": zones, "yellow_trips": trips}


def tag_counts() -> dict[str, dict[str, int]]:
    """Per entity, how many rows plant each rule code, derived from the row tags."""
    return {
        entity: dict(sorted(Counter(code for plants in tags.values() for code in plants).items()))
        for entity, tags in row_tags().items()
    }


def _write_zone_csv(path: Path) -> None:
    lines = [ZONE_HEADER, *(",".join(cells) for cells, _ in ZONE_ROWS)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_trip_parquet(path: Path, file: TripFile) -> None:
    ddl = ", ".join(f'"{spelled}" {sql_type}' for _, spelled, sql_type in file.columns)
    marks = ", ".join("?" for _ in file.columns)
    rows = [tuple(row.values.get(name) for name, _, _ in file.columns) for row in file.rows]
    con = duckdb.connect()
    try:
        con.execute(f"create table trips ({ddl})")
        con.executemany(f"insert into trips values ({marks})", rows)
        target = str(path).replace("'", "''")
        con.execute(f"copy trips to '{target}' (format parquet)")
    finally:
        con.close()


def write_fixture(dest: Path) -> dict[str, tuple[str, ...]]:
    """Write the zone CSV and every trip file into `dest`; return the months written."""
    dest.mkdir(parents=True, exist_ok=True)
    _write_zone_csv(dest / ZONE_FILE)
    for file in TRIP_FILES:
        _write_trip_parquet(dest / file.name, file)
    return {"taxi_zones": (), "yellow_trips": tuple(file.name[16:23] for file in TRIP_FILES)}

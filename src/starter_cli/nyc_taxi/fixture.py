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
from decimal import Decimal
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
        "request_source": None,  # only the 2026-09 file has this column
    }
    unknown = set(over) - set(values)
    if unknown:
        raise KeyError(f"not a landing column: {sorted(unknown)}")
    values.update(over)
    return Row(values, plants)


# The rule code a garbage value in each declared column plants.
_UNPARSEABLE = {
    "vendorid": "TRIP_VENDOR_ID_UNPARSEABLE",
    "tpep_pickup_datetime": "TRIP_PICKUP_DATETIME_UNPARSEABLE",
    "tpep_dropoff_datetime": "TRIP_DROPOFF_DATETIME_UNPARSEABLE",
    "passenger_count": "TRIP_PASSENGER_COUNT_UNPARSEABLE",
    "trip_distance": "TRIP_DISTANCE_UNPARSEABLE",
    "ratecodeid": "TRIP_RATE_CODE_UNPARSEABLE",
    "pulocationid": "TRIP_PU_LOCATION_UNPARSEABLE",
    "dolocationid": "TRIP_DO_LOCATION_UNPARSEABLE",
    "payment_type": "TRIP_PAYMENT_TYPE_UNPARSEABLE",
    "fare_amount": "TRIP_FARE_AMOUNT_UNPARSEABLE",
    "extra": "TRIP_EXTRA_UNPARSEABLE",
    "mta_tax": "TRIP_MTA_TAX_UNPARSEABLE",
    "tip_amount": "TRIP_TIP_AMOUNT_UNPARSEABLE",
    "tolls_amount": "TRIP_TOLLS_AMOUNT_UNPARSEABLE",
    "improvement_surcharge": "TRIP_IMPROVEMENT_SURCHARGE_UNPARSEABLE",
    "total_amount": "TRIP_TOTAL_AMOUNT_UNPARSEABLE",
    "congestion_surcharge": "TRIP_CONGESTION_SURCHARGE_UNPARSEABLE",
    "airport_fee": "TRIP_AIRPORT_FEE_UNPARSEABLE",
    "cbd_congestion_fee": "TRIP_CBD_CONGESTION_FEE_UNPARSEABLE",
}
# The text that is not a value of its column. Everything else in the row stays valid.
_GARBAGE = {
    "tpep_pickup_datetime": "garbage",
    "tpep_dropoff_datetime": "2024-13-45 99:99:99",
    "passenger_count": "1.5",  # not a whole number
}
_TIMESTAMP_TEXT = "%Y-%m-%d %H:%M:%S"

# The pickup of the trip published in a 2024-01 file and again, as an exact copy, in 2025-01.
REVERSE_PAIR_PICKUP = "2025-01-20 14:00:00"

OUT_OF_MONTH = "TRIP_PICKUP_OUT_OF_MONTH"
DUPLICATE = "TRIP_EXACT_DUPLICATE"
VENDOR_UNKNOWN = "TRIP_VENDOR_ID_UNKNOWN"


def _text_trip(month_start: datetime, n: int, *plants: str, **over: str) -> Row:
    """Like `_trip`, for a file where every column is text. `over` holds text values."""
    row = _trip(month_start, n)
    values = {
        name: value.strftime(_TIMESTAMP_TEXT) if isinstance(value, datetime) else value
        for name, value in row.values.items()
    }
    values = {name: (None if value is None else str(value)) for name, value in values.items()}
    values["cbd_congestion_fee"] = "0.75"
    unknown = set(over) - set(values)
    if unknown:
        raise KeyError(f"not a landing column: {sorted(unknown)}")
    values.update(over)
    return Row(values, plants)


def _copy(row: Row, *plants: str) -> Row:
    """The same published values again, with the codes the copy is meant to fail."""
    return Row(dict(row.values), plants)


JAN_2019 = datetime(2019, 1, 1)
JAN_2024 = datetime(2024, 1, 1)
FEB_2024 = datetime(2024, 2, 1)
JAN_2025 = datetime(2025, 1, 1)
SEP_2026 = datetime(2026, 9, 1)

# 2019: passenger count and rate code are DOUBLE (they land as 1.0), airport_fee is lower case
# and an INTEGER, and the file has no cbd_congestion_fee.
_TYPES_2019 = {
    **_TYPES,
    "passenger_count": "DOUBLE",
    "ratecodeid": "DOUBLE",
    "airport_fee": "INTEGER",
}
_PICKUP_IN_2001 = datetime(2001, 1, 5, 9, 0, 0)
_PICKUP_IN_2088 = datetime(2088, 1, 24, 9, 0, 0)

FILE_2019_01 = TripFile(
    "yellow_tripdata_2019-01.parquet",
    _columns(_TYPES_2019, cbd=False, spelled={"airport_fee": "airport_fee"}),
    (
        _trip(JAN_2019, 1),
        _trip(JAN_2019, 2, OUT_OF_MONTH, pickup=_PICKUP_IN_2001),
        _trip(JAN_2019, 3, OUT_OF_MONTH, pickup=_PICKUP_IN_2088),
        # A 2001 pickup with a 2088 dropoff: bronze clamps the dropoff to 2050, so the duration
        # (about 49 years) stays inside a 32-bit int and the trip is still too long.
        _trip(
            JAN_2019,
            4,
            OUT_OF_MONTH,
            "TRIP_DURATION_TOO_LONG",
            pickup=datetime(2001, 2, 2, 9, 0, 0),
            dropoff=datetime(2088, 1, 24, 9, 0, 0),
        ),
    ),
)

# The first valid 2024-01 trip is also copied into the 2025-01 file below (the forward pair).
_FIRST_VALID_2024 = _trip(JAN_2024, 1)
_DUPLICATED_IN_FILE = _trip(JAN_2024, 20)
_VENDOR_3_TWICE = _trip(JAN_2024, 22, VENDOR_UNKNOWN, vendorid=3)
_REVERSE_PAIR_ORIGINAL = _trip(
    JAN_2024,
    24,
    OUT_OF_MONTH,
    DUPLICATE,
    pickup=datetime.fromisoformat(REVERSE_PAIR_PICKUP),
)

FILE_2024_01 = TripFile(
    "yellow_tripdata_2024-01.parquet",
    _columns(cbd=False),
    (
        _FIRST_VALID_2024,
        _trip(JAN_2024, 2, pulocationid=264, dolocationid=265),
        # Flex Fare: payment type 0 with no passenger count and no rate code. All valid.
        _trip(JAN_2024, 3, payment_type=0, passenger_count=None, ratecodeid=None),
        _trip(JAN_2024, 4, VENDOR_UNKNOWN, vendorid=3),
        _trip(JAN_2024, 5, "TRIP_RATE_CODE_UNKNOWN", ratecodeid=7),
        _trip(JAN_2024, 6, "TRIP_PAYMENT_TYPE_UNKNOWN", payment_type=99),
        _trip(JAN_2024, 7, "TRIP_PU_LOCATION_UNKNOWN", pulocationid=999),
        # Zone 5 is on two zone rows, so both are quarantined and no trip may point at it.
        _trip(JAN_2024, 8, "TRIP_DO_LOCATION_UNKNOWN", dolocationid=5),
        _trip(JAN_2024, 9, "TRIP_ZERO_DISTANCE", trip_distance=0.0),
        _trip(JAN_2024, 10, "TRIP_ZERO_TOTAL", fare_amount=0.0, total_amount=0.0),
        _trip(
            JAN_2024,
            11,
            "TRIP_NEGATIVE_FARE",
            "TRIP_NEGATIVE_TOTAL",
            fare_amount=-5.0,
            total_amount=-5.0,
        ),
        _trip(JAN_2024, 12, "TRIP_NEGATIVE_TOTAL", total_amount=-4.0),
        _trip(JAN_2024, 13, "TRIP_PASSENGER_COUNT_ZERO", passenger_count=0),
        _trip(
            JAN_2024,
            14,
            "TRIP_ZERO_LENGTH",
            pickup=datetime(2024, 1, 9, 9, 0, 0),
            dropoff=datetime(2024, 1, 9, 9, 0, 0),
        ),
        _trip(
            JAN_2024,
            15,
            "TRIP_DROPOFF_BEFORE_PICKUP",
            pickup=datetime(2024, 1, 8, 9, 0, 0),
            dropoff=datetime(2024, 1, 8, 8, 59, 59),
        ),
        # Exactly 24 hours is valid; one second more is too long.
        _trip(
            JAN_2024,
            16,
            pickup=datetime(2024, 1, 10, 0, 0, 0),
            dropoff=datetime(2024, 1, 11, 0, 0, 0),
        ),
        _trip(
            JAN_2024,
            17,
            "TRIP_DURATION_TOO_LONG",
            pickup=datetime(2024, 1, 12, 0, 0, 0),
            dropoff=datetime(2024, 1, 13, 0, 0, 1),
        ),
        # The first second of the month is in month; the second before it is not.
        _trip(
            JAN_2024,
            18,
            pickup=datetime(2024, 1, 1, 0, 0, 0),
            dropoff=datetime(2024, 1, 1, 0, 10, 0),
        ),
        _trip(
            JAN_2024,
            19,
            OUT_OF_MONTH,
            pickup=datetime(2023, 12, 31, 23, 59, 59),
            dropoff=datetime(2024, 1, 1, 0, 10, 0),
        ),
        # An exact duplicate inside one file: the later row is the copy.
        _DUPLICATED_IN_FILE,
        _copy(_DUPLICATED_IN_FILE, DUPLICATE),
        # An exact duplicate that also fails another rule: the copy fails both.
        _VENDOR_3_TWICE,
        _copy(_VENDOR_3_TWICE, VENDOR_UNKNOWN, DUPLICATE),
        # The reverse pair, first half: a January 2025 trip published in the 2024-01 file. Its
        # exact copy in the 2025-01 file is in month, so the copy there is kept (D-09 as amended
        # on 2026-11-22) and this one fails both rules.
        _REVERSE_PAIR_ORIGINAL,
    ),
)


def _garbage_rows() -> tuple[Row, ...]:
    """One 2024-02 row per UNPARSEABLE rule, each with a single garbage value."""
    rows = []
    for number, (column, code) in enumerate(_UNPARSEABLE.items(), start=3):
        rows.append(_text_trip(FEB_2024, number, code, **{column: _GARBAGE.get(column, "abc")}))
    return tuple(rows)


# 2024-02: every column is text, as a damaged or hand-edited file would be.
FILE_2024_02 = TripFile(
    "yellow_tripdata_2024-02.parquet",
    tuple((name, spelled, "VARCHAR") for name, spelled, _ in _columns()),
    (
        # '1.0' is a whole number: these pass.
        _text_trip(
            FEB_2024,
            1,
            vendorid="1.0",
            passenger_count="1.0",
            ratecodeid="1.0",
            pulocationid="1.0",
            dolocationid="2.0",
            payment_type="1.0",
        ),
        # Empty cells in columns no rule requires land as NULL and pass.
        _text_trip(
            FEB_2024,
            2,
            passenger_count="",
            ratecodeid="",
            congestion_surcharge="",
            airport_fee="",
            cbd_congestion_fee="",
        ),
        *_garbage_rows(),
        # An impossible date, not only a word.
        _text_trip(
            FEB_2024,
            22,
            "TRIP_PICKUP_DATETIME_UNPARSEABLE",
            tpep_pickup_datetime="2024-13-45 99:99:99",
        ),
        # Empty cells in required columns land as NULL: MISSING, never UNPARSEABLE.
        _text_trip(FEB_2024, 23, "TRIP_PICKUP_DATETIME_MISSING", tpep_pickup_datetime=""),
        _text_trip(FEB_2024, 24, "TRIP_DROPOFF_DATETIME_MISSING", tpep_dropoff_datetime=""),
        _text_trip(FEB_2024, 25, "TRIP_PU_LOCATION_MISSING", pulocationid=""),
        _text_trip(FEB_2024, 26, "TRIP_DO_LOCATION_MISSING", dolocationid=""),
    ),
)

FILE_2025_01 = TripFile(
    "yellow_tripdata_2025-01.parquet",
    _columns(),
    (
        _trip(JAN_2025, 1, cbd_congestion_fee=0.75),
        # The forward pair: an exact copy of the first valid 2024-01 trip. Its pickup is out of
        # month here and its in-month original outranks it. Its declared values equal those of a
        # valid row, so it plants no warn code.
        _copy(_FIRST_VALID_2024, OUT_OF_MONTH, DUPLICATE),
        # The reverse pair, second half: in month here, so it takes rank 1 and is valid.
        _copy(_REVERSE_PAIR_ORIGINAL),
    ),
)

# The request_source values differ from the marker the planted-trip tests use ("app"), so the
# check that a planted row never reaches the demo database stays meaningful when the gate points
# it at this fixture database.
_FIRST_2026 = _trip(SEP_2026, 1, cbd_congestion_fee=0.75, request_source="kiosk")

FILE_2026_09 = TripFile(
    "yellow_tripdata_2026-09.parquet",
    _columns(extra=(("request_source", "request_source", "VARCHAR"),)),
    (
        _FIRST_2026,
        # Identical in every declared column, different only in the undeclared request_source:
        # the fingerprint covers declared landed columns only (D-10 as clarified 2026-11-22), so
        # this is an exact duplicate, and the undeclared column raises the one expected warning.
        Row({**_FIRST_2026.values, "request_source": "phone"}, (DUPLICATE,)),
    ),
)

TRIP_FILES: tuple[TripFile, ...] = (
    FILE_2019_01,
    FILE_2024_01,
    FILE_2024_02,
    FILE_2025_01,
    FILE_2026_09,
)

# (id, borough, zone, service_zone) as the CSV holds them; an empty string is an empty cell.
ZONE_ROWS: tuple[tuple[tuple[str, str, str, str], tuple[str, ...]], ...] = (
    (("1", "EWR", "Newark Airport", "EWR"), ()),
    (("2", "Queens", "Jamaica Bay", "Boro Zone"), ()),
    (("3", "Manhattan", "Alphabet City", "Yellow Zone"), ()),
    (("264", "Unknown", "N/A", "N/A"), ()),
    (("265", "N/A", "Outside of NYC", "N/A"), ()),
    (("abc", "Queens", "Bad Id", "Boro Zone"), ("ZONE_LOCATION_ID_UNPARSEABLE",)),
    (("", "Queens", "Missing Id", "Boro Zone"), ("ZONE_LOCATION_ID_MISSING",)),
    (("7", "", "No Borough", "Boro Zone"), ("ZONE_BOROUGH_MISSING",)),
    (("8", "Atlantis", "Nowhere", "Boro Zone"), ("ZONE_BOROUGH_UNKNOWN",)),
    (("9", "Queens", "Odd Service", "Mars Zone"), ("ZONE_SERVICE_ZONE_UNKNOWN",)),
    # Id 5 on two rows: both are quarantined, so trips that point at 5 have an unknown zone.
    (("5", "Bronx", "Twin Zone A", "Boro Zone"), ("ZONE_LOCATION_ID_DUPLICATE",)),
    (("5", "Bronx", "Twin Zone B", "Boro Zone"), ("ZONE_LOCATION_ID_DUPLICATE",)),
)

# Written by hand, row by row, from the tags above. test_fixture_counts.py proves that these
# equal the counts derived from the tags (double entry), and then that the build reproduces them.
# Trips: 59 landed rows (4 + 24 + 26 + 3 + 2). 41 fail an error rule. The 18 in silver include
# 6 rows with a warning (zero distance, zero total, negative fare and total, negative total,
# passenger count zero, zero length). No quarantined row carries a warn code.
# warn_tests is the undeclared request_source column, the only warning the build may show.
# gold counts the star built over those silver rows: the fact equals the 18 silver trips; the
# calendar is the configured span 2009-01-01 to 2030-12-31 (8,035 days); the zones are the 5
# valid fixture zones; two valid fixture trips carry no rate code (Flex Fare); each code
# dimension equals its seed's row count (4 vendors, 7 rate codes, 7 payment types). The daily
# aggregate rolls the 18 silver trips up to 8 pickup days and 9 day-borough pairs, and its totals
# equal the fact's: 18 trips and 283.500000 dollars, the sum of their total_amount values.
EXPECTED: dict = {
    "taxi_zones": {
        "landing": 12,
        "silver": 5,
        "quarantine": 7,
        "rules": {
            "ZONE_BOROUGH_MISSING": 1,
            "ZONE_BOROUGH_UNKNOWN": 1,
            "ZONE_LOCATION_ID_DUPLICATE": 2,
            "ZONE_LOCATION_ID_MISSING": 1,
            "ZONE_LOCATION_ID_UNPARSEABLE": 1,
            "ZONE_SERVICE_ZONE_UNKNOWN": 1,
        },
    },
    "yellow_trips": {
        "landing": 59,
        "silver": 18,
        "quarantine": 41,
        "silver_warned": 6,
        "rules": {
            "TRIP_AIRPORT_FEE_UNPARSEABLE": 1,
            "TRIP_CBD_CONGESTION_FEE_UNPARSEABLE": 1,
            "TRIP_CONGESTION_SURCHARGE_UNPARSEABLE": 1,
            "TRIP_DISTANCE_UNPARSEABLE": 1,
            "TRIP_DO_LOCATION_MISSING": 1,
            "TRIP_DO_LOCATION_UNKNOWN": 1,
            "TRIP_DO_LOCATION_UNPARSEABLE": 1,
            "TRIP_DROPOFF_BEFORE_PICKUP": 1,
            "TRIP_DROPOFF_DATETIME_MISSING": 1,
            "TRIP_DROPOFF_DATETIME_UNPARSEABLE": 1,
            "TRIP_DURATION_TOO_LONG": 2,
            "TRIP_EXACT_DUPLICATE": 5,
            "TRIP_EXTRA_UNPARSEABLE": 1,
            "TRIP_FARE_AMOUNT_UNPARSEABLE": 1,
            "TRIP_IMPROVEMENT_SURCHARGE_UNPARSEABLE": 1,
            "TRIP_MTA_TAX_UNPARSEABLE": 1,
            "TRIP_NEGATIVE_FARE": 1,
            "TRIP_NEGATIVE_TOTAL": 2,
            "TRIP_PASSENGER_COUNT_UNPARSEABLE": 1,
            "TRIP_PASSENGER_COUNT_ZERO": 1,
            "TRIP_PAYMENT_TYPE_UNKNOWN": 1,
            "TRIP_PAYMENT_TYPE_UNPARSEABLE": 1,
            "TRIP_PICKUP_DATETIME_MISSING": 1,
            "TRIP_PICKUP_DATETIME_UNPARSEABLE": 2,
            "TRIP_PICKUP_OUT_OF_MONTH": 6,
            "TRIP_PU_LOCATION_MISSING": 1,
            "TRIP_PU_LOCATION_UNKNOWN": 1,
            "TRIP_PU_LOCATION_UNPARSEABLE": 1,
            "TRIP_RATE_CODE_UNKNOWN": 1,
            "TRIP_RATE_CODE_UNPARSEABLE": 1,
            "TRIP_TIP_AMOUNT_UNPARSEABLE": 1,
            "TRIP_TOLLS_AMOUNT_UNPARSEABLE": 1,
            "TRIP_TOTAL_AMOUNT_UNPARSEABLE": 1,
            "TRIP_VENDOR_ID_UNKNOWN": 3,
            "TRIP_VENDOR_ID_UNPARSEABLE": 1,
            "TRIP_ZERO_DISTANCE": 1,
            "TRIP_ZERO_LENGTH": 1,
            "TRIP_ZERO_TOTAL": 1,
        },
    },
    "warn_tests": 1,
    "gold": {
        "dim_zone": 5,
        "dim_date": 8035,
        "fct_trips": 18,
        "fct_trips_null_rate_code": 2,
        "dim_vendor": 4,
        "dim_rate_code": 7,
        "dim_payment_type": 7,
        "agg_trips_daily": 9,
        "agg_trip_count_sum": 18,
        "agg_total_amount_sum": Decimal("283.500000"),
    },
}


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

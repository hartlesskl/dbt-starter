"""A placeholder source: rename it or delete it when you add your own (docs/guide/swap-out.md).

This is not a worked example. It is the smallest source the conventions accept, so a copy of the
template has something real to build while you replace it: eight invented rows that land through
the real loader, flow landing -> bronze -> silver and quarantine -> gold, and one row that fails a
rule on purpose so the quarantine path runs. Every value is made up, and nothing is downloaded.
"""

from __future__ import annotations

import csv
from pathlib import Path

from starter_cli.landing import EntitySpec

SOURCE = "example_source"

# Landed as text, the way every source lands. Row 8 is the one bad row: its amount is not a number,
# so it fails ITEM_AMOUNT_UNPARSEABLE and goes to quarantine. The timestamps carry no zone
# designator, which is the text the bronze parse accepts.
ROWS: list[dict[str, str]] = [
    {
        "id": str(number),
        "name": f"Placeholder item {number}",
        "amount": "twelve" if number == 8 else f"{number * 10 + 2}.50",
        "updated_at": f"2026-01-0{number} 09:00:00",
    }
    for number in range(1, 9)
]

# The gate's fixture is ROWS plus one planted failing row for each rule ROWS leaves unplanted, so
# every ITEM_ rule fails at least one fixture row. The demo lands ROWS only.
FIXTURE_ROWS: list[dict[str, str]] = [
    *ROWS,
    # Row 9 has no id, so only ITEM_ID_MISSING fires.
    {
        "id": "",
        "name": "Placeholder item 9",
        "amount": "92.50",
        "updated_at": "2026-01-09 09:00:00",
    },
    # Row 10 has an id that is not a whole number, so only ITEM_ID_UNPARSEABLE fires.
    {
        "id": "ten",
        "name": "Placeholder item 10",
        "amount": "102.50",
        "updated_at": "2026-01-10 09:00:00",
    },
    # Row 11 has a timestamp the parse does not accept, so only ITEM_UPDATED_AT_UNPARSEABLE fires.
    {
        "id": "11",
        "name": "Placeholder item 11",
        "amount": "112.50",
        "updated_at": "11 January 2026",
    },
]

COLUMNS = ("id", "name", "amount", "updated_at")
FILE_NAME = "items.csv"

ENTITIES: dict[str, EntitySpec] = {
    # No url: this source never downloads. `provide` writes the file from ROWS, so the demo lands
    # it through the same loader as any source (the loader calls it instead of making a request).
    "items": EntitySpec(
        source=SOURCE,
        entity="items",
        url="",
        file_name=FILE_NAME,
        columns=COLUMNS,
        header_prefix=b"id,name",
        provide=lambda part: write_rows(part, ROWS),
    ),
}

# Ordered (label, relation) pairs the demo printer shows.
REPORT_RELATIONS: list[tuple[str, str]] = [
    ("landing", "landing.example_source__items"),
    ("bronze", "bronze.example_source__items"),
    ("silver", "silver.items"),
    ("quarantine", "silver.items_quarantine"),
    ("gold", "gold.fct_items"),
]

# Per entity, the bronze, silver and quarantine relations the printer checks as bronze = silver +
# quarantine (nothing dropped between bronze and silver).
REPORT_CONSERVATION: dict[str, tuple[str, str, str]] = {
    "items": (
        "bronze.example_source__items",
        "silver.items",
        "silver.items_quarantine",
    ),
}

# The entity whose file `write_added_column_variant` re-lands with one undeclared column, for build 4
# of the Fabric verification, and the undeclared columns this fixture plants in every build.
ADDED_COLUMN_ENTITY = "items"
FIXTURE_UNDECLARED_COLUMNS = 0
ADDED_COLUMN = "note"

__all__ = [
    "ADDED_COLUMN_ENTITY",
    "COLUMNS",
    "ENTITIES",
    "FIXTURE_ROWS",
    "FIXTURE_UNDECLARED_COLUMNS",
    "REPORT_CONSERVATION",
    "REPORT_RELATIONS",
    "ROWS",
    "SOURCE",
    "write_added_column_variant",
    "write_fixture",
    "write_rows",
]


def write_rows(path: Path, rows: list[dict[str, str]], columns: tuple[str, ...] = COLUMNS) -> None:
    """Write `rows` to `path` as a CSV file with a header line, one row per dict."""
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_fixture(folder: Path) -> dict[str, tuple[str, ...]]:
    """Write the gate's fixture file into `folder`; return the months written per entity (none)."""
    folder.mkdir(parents=True, exist_ok=True)
    write_rows(folder / FILE_NAME, FIXTURE_ROWS)
    return {"items": ()}


def write_added_column_variant(dest: Path) -> Path:
    """Write the fixture file plus one undeclared column `note` into `dest`; return its path."""
    dest.mkdir(parents=True, exist_ok=True)
    rows = [{**row, ADDED_COLUMN: f"n{number}"} for number, row in enumerate(FIXTURE_ROWS, 1)]
    path = dest / FILE_NAME
    write_rows(path, rows, (*COLUMNS, ADDED_COLUMN))
    return path

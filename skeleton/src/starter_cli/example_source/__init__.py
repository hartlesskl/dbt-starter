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

COLUMNS = ("id", "name", "amount", "updated_at")
FILE_NAME = "items.csv"

ENTITIES: dict[str, EntitySpec] = {
    # The url is empty because this source never downloads: the fixture writes the file.
    "items": EntitySpec(
        source=SOURCE,
        entity="items",
        url="",
        file_name=FILE_NAME,
        columns=COLUMNS,
        header_prefix=b"id,name",
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

__all__ = [
    "COLUMNS",
    "ENTITIES",
    "REPORT_CONSERVATION",
    "REPORT_RELATIONS",
    "ROWS",
    "SOURCE",
    "write_fixture",
    "write_rows",
]


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    """Write `rows` to `path` as a CSV file with a header line, one row per dict."""
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_fixture(folder: Path) -> dict[str, tuple[str, ...]]:
    """Write the one file into `folder`; return the months written per entity (none here)."""
    folder.mkdir(parents=True, exist_ok=True)
    write_rows(folder / FILE_NAME, ROWS)
    return {"items": ()}

"""The example's pinned demo months and what they do to the Pages cache key (DEMO-06, D-10, D-13).

The pinned list is the example's, so its tests live beside the example. `tests/loader/test_site.py`
keeps testing the cache key on synthetic sources and is not edited for this list.
"""

from __future__ import annotations

import dataclasses
import types

from starter_cli import site
from starter_cli.nyc_taxi import ENTITIES
from starter_cli.project import discover_sources

PINNED = ("2024-01", "2025-01")


def _real_sources_with(default_months: tuple[str, ...]) -> dict[str, types.ModuleType]:
    """The real sources, with the trip entity's pinned months replaced."""
    variant = dict(ENTITIES)
    variant["yellow_trips"] = dataclasses.replace(
        ENTITIES["yellow_trips"], default_months=default_months
    )
    module = types.SimpleNamespace(SOURCE="nyc_taxi", ENTITIES=variant)
    return {"nyc_taxi": module}  # type: ignore[dict-item]


def test_the_trip_entity_pins_two_months_from_two_schema_eras() -> None:
    assert ENTITIES["yellow_trips"].default_months == PINNED


def test_both_pinned_months_are_on_or_after_the_large_file_boundary() -> None:
    spec = ENTITIES["yellow_trips"]
    assert spec.large_before is not None
    assert all(month >= spec.large_before for month in spec.default_months)


def test_the_real_cache_key_differs_from_the_key_with_only_the_first_month_pinned() -> None:
    real = site.raw_cache_key(discover_sources())
    assert real != site.raw_cache_key(_real_sources_with(("2024-01",)))


def test_the_real_cache_key_does_not_depend_on_the_order_of_the_pinned_months() -> None:
    real = site.raw_cache_key(discover_sources())
    assert real == site.raw_cache_key(_real_sources_with(("2025-01", "2024-01")))

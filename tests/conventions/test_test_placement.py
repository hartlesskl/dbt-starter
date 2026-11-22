"""Test placement: every test sits in the layer where its issue can be fixed (TEST-01, D-15).

The policy, in one place so an adopter can read it and change one entry:

- Landing and bronze hold row-key tests, because a duplicated or missing row is a loading fault
  and the fix is in the loader.
- Silver holds accepted-values tests, because an unknown code is fixed by a rule or a seed.
- Bronze holds accepted-values tests only as the 0/1 domain check of a rule flag that bronze
  computes itself. This is the one stated exception to "accepted values in silver", and it
  follows the same principle: a flag outside 0/1 is fixed in the bronze rule that computes it.
  A code list belongs in silver.
- Silver holds uniqueness after deduplication.
- Gold holds relationships and reconciliation, because a missing dimension member or a lost trip
  is fixed in the gold model.

Classification uses the resource type of the node a test is attached to, not its schema: seeds
build into the `bronze` schema but are not a layer, so a test on a seed counts under `seeds`.
The checks are pure functions over a manifest dict, like `test_layer_direction.py`.
"""

from __future__ import annotations

import copy

import pytest

PACKAGE = "dbt_starter"

# The five places a test can sit, in pipeline order. The overview table lists them in this order.
LAYER_ORDER = ("landing", "bronze", "silver", "gold", "seeds")

# Test kinds that may sit only in gold, because their issue is fixed in a gold model.
GOLD_ONLY_KINDS = frozenset({"relationships", "totals_reconcile"})

# The layers that may hold an accepted-values test at all.
ACCEPTED_VALUES_LAYERS = frozenset({"bronze", "silver"})

# The only accepted values a bronze test may check: the 0/1 domain of a rule flag.
BRONZE_FLAG_VALUES = [0, 1]

# Named tests that the placement rule permits where it otherwise would not.
PLACEMENT_EXCEPTIONS: frozenset[str] = frozenset()


def layer_of(test_node: dict, manifest: dict) -> str:
    return "unknown"


def kind_of(test_node: dict) -> str:
    return ""


def check_placement(manifest: dict, exceptions: frozenset[str] | None = None) -> list[str]:
    return []


def _test(name: str, kind: str, attached: str | None = None, source: str | None = None) -> dict:
    """A generic test node as dbt 1.12 writes it, reduced to the fields the policy reads."""
    return {
        "resource_type": "test",
        "package_name": PACKAGE,
        "name": name,
        "attached_node": attached,
        "depends_on": {"nodes": [source] if source else [attached] if attached else []},
        "test_metadata": {"name": kind, "kwargs": {}},
    }


def _model(name: str, layer: str) -> tuple[str, dict]:
    uid = f"model.{PACKAGE}.{name}"
    return uid, {
        "resource_type": "model",
        "package_name": PACKAGE,
        "name": name,
        "fqn": [PACKAGE, layer, "group", name],
    }


SOURCE_ID = f"source.{PACKAGE}.feed.table"
SEED_ID = f"seed.{PACKAGE}.codes"
BRONZE_ID, BRONZE_NODE = _model("bronze__a", "bronze")
SILVER_ID, SILVER_NODE = _model("silver__a", "silver")
GOLD_ID, GOLD_NODE = _model("gold__a", "gold")


def _manifest(tests: list[dict]) -> dict:
    """A small manifest that satisfies the row-key rules, plus the given tests."""
    key_tests = [
        _test("landing_key", "unique_combination_of_columns", source=SOURCE_ID),
        _test("bronze_key", "unique", attached=BRONZE_ID),
        _test("gold_key", "unique", attached=GOLD_ID),
    ]
    nodes = {
        BRONZE_ID: BRONZE_NODE,
        SILVER_ID: SILVER_NODE,
        GOLD_ID: GOLD_NODE,
        SEED_ID: {
            "resource_type": "seed",
            "package_name": PACKAGE,
            "name": "codes",
            "fqn": [PACKAGE, "codes"],
            "schema": "bronze",
        },
    }
    for node in key_tests + tests:
        nodes[f"test.{PACKAGE}.{node['name']}"] = node
    sources = {SOURCE_ID: {"resource_type": "source", "package_name": PACKAGE, "name": "table"}}
    return {"nodes": nodes, "sources": sources}


def test_a_small_manifest_with_the_key_tests_passes() -> None:
    assert check_placement(_manifest([])) == []


def test_a_test_on_a_seed_is_classified_seeds_although_the_seed_builds_into_bronze() -> None:
    manifest = _manifest([_test("seed_unique", "unique", attached=SEED_ID)])
    node = manifest["nodes"][f"test.{PACKAGE}.seed_unique"]
    assert manifest["nodes"][SEED_ID]["schema"] == "bronze"
    assert layer_of(node, manifest) == "seeds"


def test_a_test_on_a_source_is_classified_landing() -> None:
    manifest = _manifest([])
    node = manifest["nodes"][f"test.{PACKAGE}.landing_key"]
    assert layer_of(node, manifest) == "landing"
    assert kind_of(node) == "unique_combination_of_columns"


def test_a_test_on_a_model_is_classified_by_the_layer_folder_of_the_model() -> None:
    manifest = _manifest([_test("silver_nn", "not_null", attached=SILVER_ID)])
    assert layer_of(manifest["nodes"][f"test.{PACKAGE}.silver_nn"], manifest) == "silver"
    assert layer_of(manifest["nodes"][f"test.{PACKAGE}.bronze_key"], manifest) == "bronze"
    assert layer_of(manifest["nodes"][f"test.{PACKAGE}.gold_key"], manifest) == "gold"


def _with_kwargs(node: dict, **kwargs: object) -> dict:
    node["test_metadata"]["kwargs"].update(kwargs)
    return node


def test_a_relationships_test_on_a_silver_model_is_reported_with_its_name_and_gold() -> None:
    manifest = _manifest([_test("silver_rel", "relationships", attached=SILVER_ID)])
    problems = check_placement(manifest)
    assert len(problems) == 1
    assert "silver_rel" in problems[0]
    assert "gold" in problems[0]


def test_a_totals_reconcile_test_outside_gold_is_reported() -> None:
    manifest = _manifest([_test("bronze_totals", "totals_reconcile", attached=BRONZE_ID)])
    problems = check_placement(manifest)
    assert len(problems) == 1
    assert "bronze_totals" in problems[0]


def test_a_relationships_test_in_gold_passes() -> None:
    manifest = _manifest([_test("gold_rel", "relationships", attached=GOLD_ID)])
    assert check_placement(manifest) == []


def test_a_named_exception_permits_that_test_and_no_other_silver_relationships_test() -> None:
    manifest = _manifest(
        [
            _test("silver_rel_ok", "relationships", attached=SILVER_ID),
            _test("silver_rel_bad", "relationships", attached=SILVER_ID),
        ]
    )
    problems = check_placement(manifest, exceptions=frozenset({"silver_rel_ok"}))
    assert len(problems) == 1
    assert "silver_rel_bad" in problems[0]


@pytest.mark.parametrize("attached", [GOLD_ID, SEED_ID])
def test_an_accepted_values_test_in_gold_or_on_a_seed_is_reported(attached: str) -> None:
    manifest = _manifest([_test("misplaced_av", "accepted_values", attached=attached)])
    problems = check_placement(manifest)
    assert len(problems) == 1
    assert "misplaced_av" in problems[0]
    assert "silver" in problems[0]


def test_an_accepted_values_test_on_a_landing_source_is_reported() -> None:
    manifest = _manifest([_test("landing_av", "accepted_values", source=SOURCE_ID)])
    problems = check_placement(manifest)
    assert len(problems) == 1
    assert "landing_av" in problems[0]


def test_a_bronze_accepted_values_test_on_the_zero_one_flag_domain_passes() -> None:
    node = _with_kwargs(_test("bronze_flag", "accepted_values", attached=BRONZE_ID), values=[0, 1])
    assert check_placement(_manifest([node])) == []


def test_a_bronze_accepted_values_code_list_is_reported_with_its_name_and_silver() -> None:
    node = _with_kwargs(
        _test("bronze_codes", "accepted_values", attached=BRONZE_ID), values=["a", "b"]
    )
    problems = check_placement(_manifest([node]))
    assert len(problems) == 1
    assert "bronze_codes" in problems[0]
    assert "silver" in problems[0]


def test_a_silver_accepted_values_code_list_passes() -> None:
    node = _with_kwargs(
        _test("silver_codes", "accepted_values", attached=SILVER_ID), values=["error", "warn"]
    )
    assert check_placement(_manifest([node])) == []


def test_a_gold_model_with_no_grain_test_is_reported_by_name() -> None:
    manifest = _manifest([])
    del manifest["nodes"][f"test.{PACKAGE}.gold_key"]
    problems = check_placement(manifest)
    assert len(problems) == 1
    assert "gold__a" in problems[0]


def test_a_gold_model_with_a_combination_grain_test_passes() -> None:
    manifest = _manifest([])
    manifest["nodes"][f"test.{PACKAGE}.gold_key"]["test_metadata"]["name"] = (
        "unique_combination_of_columns"
    )
    assert check_placement(manifest) == []


def test_a_bronze_model_with_no_unique_test_is_reported_by_name() -> None:
    manifest = _manifest([])
    del manifest["nodes"][f"test.{PACKAGE}.bronze_key"]
    problems = check_placement(manifest)
    assert len(problems) == 1
    assert "bronze__a" in problems[0]


def test_a_landing_source_with_no_key_test_is_reported_by_name() -> None:
    manifest = _manifest([])
    del manifest["nodes"][f"test.{PACKAGE}.landing_key"]
    problems = check_placement(manifest)
    assert len(problems) == 1
    assert "feed.table" in problems[0]


def test_tests_of_other_packages_are_not_checked() -> None:
    node = _test("foreign_rel", "relationships", attached=SILVER_ID)
    node["package_name"] = "dbt_utils"
    assert check_placement(_manifest([node])) == []


def test_the_only_placement_exception_is_the_rule_catalog_relationship() -> None:
    assert PLACEMENT_EXCEPTIONS == {
        "relationships_silver__dq_rule_failures_rule_code__code__ref_silver__dq_rule_catalog_"
    }


def test_the_real_manifest_places_every_test_where_its_issue_is_fixed(
    duckdb_manifest: dict,
) -> None:
    assert check_placement(duckdb_manifest) == []


def test_the_real_manifest_has_a_test_in_every_layer(duckdb_manifest: dict) -> None:
    seen = {
        layer_of(node, duckdb_manifest)
        for node in duckdb_manifest["nodes"].values()
        if node["resource_type"] == "test" and node["package_name"] == PACKAGE
        if node.get("test_metadata")
    }
    assert seen == set(LAYER_ORDER), "a layer with no test would make its row check vacuous"


def test_the_placement_exception_names_a_test_that_exists_in_the_real_manifest(
    duckdb_manifest: dict,
) -> None:
    names = {n["name"] for n in duckdb_manifest["nodes"].values() if n["resource_type"] == "test"}
    assert PLACEMENT_EXCEPTIONS <= names


def test_the_real_manifest_moved_relationships_test_is_reported(duckdb_manifest: dict) -> None:
    doctored = copy.deepcopy(duckdb_manifest)
    node = doctored["nodes"][
        next(
            uid
            for uid, n in doctored["nodes"].items()
            if n["resource_type"] == "test" and n["name"] == "fct_trips_pu_location_id_has_a_zone"
        )
    ]
    node["attached_node"] = "model.dbt_starter.silver__yellow_trips"
    problems = check_placement(doctored)
    assert any("fct_trips_pu_location_id_has_a_zone" in p and "gold" in p for p in problems)

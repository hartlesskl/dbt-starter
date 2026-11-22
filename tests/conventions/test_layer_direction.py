"""Layer direction: a model reads only from the layer before it (LAYR-04).

Bronze reads landing sources, silver reads bronze models, gold reads silver models, and seeds
are allowed upstream of every layer. Same-layer and skip-layer edges both fail in Phase 1.
The rule lives in one table, `ALLOWED_UPSTREAM`, so a later phase changes one entry. A same-layer
read that is wanted anyway is not a relaxed layer rule: it is one named pair in `ALLOWED_EDGES`.
"""

from __future__ import annotations

from typing import Any

import pytest

PACKAGE = "dbt_starter"

# The table of layer rules. Each value is the set of upstream classes a model of that layer may
# read, as returned by `classify_upstream`. It stays as the Phase 1 rule: relaxing `bronze` to
# accept every `model:bronze` edge would hide future mistakes.
ALLOWED_UPSTREAM: dict[str, set[str]] = {
    "bronze": {"source:landing", "seed"},
    "silver": {"model:bronze", "seed"},
    "gold": {"model:silver", "seed"},
}

# Named exceptions, as (model id, upstream id) pairs. A pair here permits that one edge and
# nothing else, so a second same-layer read still fails the check.
ALLOWED_EDGES: set[tuple[str, str]] = set()
# example:begin
# The trips model reads the valid zones of the zones model (a same-layer edge in bronze). Reading
# the zones model, filtered to its valid rows, means a zone that is quarantined in silver is
# unknown to the trips as well, so a later relationship test from trips to zones cannot meet a
# trip whose zone is missing. Reading silver would invert the layers, and reading the landing
# source would accept zones that silver rejects.
ALLOWED_EDGES.add(
    (
        "model.dbt_starter.bronze__nyc_taxi__yellow_trips",
        "model.dbt_starter.bronze__nyc_taxi__taxi_zones",
    )
)
# example:end


def classify_upstream(uid: str, manifest: dict) -> str:
    """Name the kind of an upstream node: `source:<schema>`, `seed`, `model:<layer>` or its type."""
    source = manifest["sources"].get(uid)
    if source is not None:
        return f"source:{source['schema']}"
    node = manifest["nodes"].get(uid)
    if node is None:
        return "unknown"
    if node["resource_type"] == "seed":
        return "seed"
    if node["resource_type"] == "model":
        return f"model:{node['fqn'][1]}"
    return node["resource_type"]


def check_layer_direction(
    manifest: dict,
    package: str = "dbt_starter",
    allowed_edges: set[tuple[str, str]] | None = None,
) -> list[str]:
    """Return every model-to-upstream edge that the allowed-edge tables do not permit.

    An edge is permitted when its upstream class is in `ALLOWED_UPSTREAM` for the model's layer,
    or when the exact (model, upstream) pair is in `allowed_edges` (default: `ALLOWED_EDGES`).
    A model outside the three layers has no entry in the table; the layer-naming check
    reports it, so it is skipped here rather than reported twice.
    """
    named = ALLOWED_EDGES if allowed_edges is None else allowed_edges
    problems = []
    for uid, node in manifest["nodes"].items():
        if node["resource_type"] != "model" or node.get("package_name") != package:
            continue
        layer = node["fqn"][1]
        if layer not in ALLOWED_UPSTREAM:
            continue
        for upstream in node["depends_on"]["nodes"]:
            if (uid, upstream) in named:
                continue
            kind = classify_upstream(upstream, manifest)
            if kind not in ALLOWED_UPSTREAM[layer]:
                problems.append(f"{uid} ({layer}) reads {upstream} ({kind})")
    return sorted(problems)


def _model(name: str, layer: str, upstream: list[str]) -> tuple[str, dict[str, Any]]:
    uid = f"model.{PACKAGE}.{name}"
    node = {
        "resource_type": "model",
        "package_name": PACKAGE,
        "fqn": [PACKAGE, layer, "group", name],
        "depends_on": {"nodes": upstream},
    }
    return uid, node


def _manifest(models: list[tuple[str, dict[str, Any]]], extra_nodes=None, sources=None) -> dict:
    nodes = dict(models)
    nodes.update(extra_nodes or {})
    return {"nodes": nodes, "sources": sources or {}}


LANDING_SOURCE = "source.dbt_starter.feed.table"
SOURCES = {
    LANDING_SOURCE: {"resource_type": "source", "schema": "landing", "package_name": PACKAGE},
    "source.dbt_starter.feed.other": {
        "resource_type": "source",
        "schema": "raw",
        "package_name": PACKAGE,
    },
}
SEED = "seed.dbt_starter.codes"
SEED_NODE = {SEED: {"resource_type": "seed", "package_name": PACKAGE, "fqn": [PACKAGE, "codes"]}}


def _check(models: list[tuple[str, dict[str, Any]]]) -> list[str]:
    return check_layer_direction(_manifest(models, SEED_NODE, SOURCES))


def test_the_allowed_edge_table_is_the_phase_one_rule() -> None:
    assert ALLOWED_UPSTREAM == {
        "bronze": {"source:landing", "seed"},
        "silver": {"model:bronze", "seed"},
        "gold": {"model:silver", "seed"},
    }


# example:begin
def test_the_one_named_edge_is_the_trips_read_of_the_valid_zones() -> None:
    assert ALLOWED_EDGES == {
        (
            "model.dbt_starter.bronze__nyc_taxi__yellow_trips",
            "model.dbt_starter.bronze__nyc_taxi__taxi_zones",
        )
    }


# example:end


def test_the_real_manifest_reads_one_layer_back(duckdb_manifest: dict) -> None:
    assert check_layer_direction(duckdb_manifest) == []


def test_the_real_manifest_has_edges_to_check(duckdb_manifest: dict) -> None:
    edges = [
        upstream
        for node in duckdb_manifest["nodes"].values()
        if node["resource_type"] == "model" and node["package_name"] == PACKAGE
        for upstream in node["depends_on"]["nodes"]
    ]
    assert edges, "no model has an upstream node, so the direction check would pass vacuously"


def test_the_chain_landing_to_bronze_to_silver_to_gold_passes() -> None:
    models = [
        _model("bronze__a", "bronze", [LANDING_SOURCE]),
        _model("silver__a", "silver", ["model.dbt_starter.bronze__a"]),
        _model("gold__a", "gold", ["model.dbt_starter.silver__a"]),
    ]
    assert _check(models) == []


def test_a_model_with_no_upstream_passes() -> None:
    assert _check([_model("silver__lonely", "silver", [])]) == []


@pytest.mark.parametrize("layer", ["bronze", "silver", "gold"])
def test_seeds_are_allowed_upstream_of_every_layer(layer: str) -> None:
    assert _check([_model(f"{layer}__a", layer, [SEED])]) == []


def test_silver_reading_silver_is_flagged() -> None:
    models = [
        _model("silver__a", "silver", []),
        _model("silver__b", "silver", ["model.dbt_starter.silver__a"]),
    ]
    problems = _check(models)
    assert problems == [
        "model.dbt_starter.silver__b (silver) reads model.dbt_starter.silver__a (model:silver)"
    ]


def test_gold_reading_bronze_is_flagged() -> None:
    models = [
        _model("bronze__a", "bronze", []),
        _model("gold__b", "gold", ["model.dbt_starter.bronze__a"]),
    ]
    assert _check(models) == [
        "model.dbt_starter.gold__b (gold) reads model.dbt_starter.bronze__a (model:bronze)"
    ]


def test_bronze_reading_a_silver_model_is_flagged() -> None:
    models = [
        _model("silver__a", "silver", []),
        _model("bronze__b", "bronze", ["model.dbt_starter.silver__a"]),
    ]
    assert _check(models) == [
        "model.dbt_starter.bronze__b (bronze) reads model.dbt_starter.silver__a (model:silver)"
    ]


def test_silver_reading_a_source_is_flagged() -> None:
    assert _check([_model("silver__a", "silver", [LANDING_SOURCE])]) == [
        f"model.dbt_starter.silver__a (silver) reads {LANDING_SOURCE} (source:landing)"
    ]


def test_bronze_reading_a_source_outside_landing_is_flagged() -> None:
    other = "source.dbt_starter.feed.other"
    assert _check([_model("bronze__a", "bronze", [other])]) == [
        f"model.dbt_starter.bronze__a (bronze) reads {other} (source:raw)"
    ]


def test_every_violating_edge_is_reported_sorted_by_model_id() -> None:
    models = [
        _model("bronze__b", "bronze", []),
        _model("silver__ok", "silver", ["model.dbt_starter.bronze__b"]),
        _model("silver__z", "silver", ["model.dbt_starter.silver__ok"]),
        _model("silver__a", "silver", ["model.dbt_starter.silver__ok", SEED]),
        _model("gold__m", "gold", ["model.dbt_starter.silver__ok", "model.dbt_starter.bronze__b"]),
    ]
    problems = _check(models)
    assert len(problems) == 3
    assert problems == sorted(problems)
    assert problems[0].startswith("model.dbt_starter.gold__m")
    assert problems[1].startswith("model.dbt_starter.silver__a")
    assert problems[2].startswith("model.dbt_starter.silver__z")


def test_a_model_reading_two_bad_upstreams_reports_both() -> None:
    models = [
        _model("silver__x", "silver", []),
        _model("silver__y", "silver", []),
        _model(
            "silver__z", "silver", ["model.dbt_starter.silver__x", "model.dbt_starter.silver__y"]
        ),
    ]
    assert len(_check(models)) == 2


def test_models_of_other_packages_are_not_checked() -> None:
    uid, node = _model("silver__a", "silver", [LANDING_SOURCE])
    node["package_name"] = "dbt_utils"
    assert check_layer_direction(_manifest([(uid, node)], SEED_NODE, SOURCES)) == []


def test_classify_upstream_names_the_kind_of_each_upstream() -> None:
    manifest = _manifest(
        [_model("silver__a", "silver", [])],
        {
            SEED: SEED_NODE[SEED],
            "snapshot.dbt_starter.snap": {"resource_type": "snapshot", "package_name": PACKAGE},
        },
        SOURCES,
    )
    assert classify_upstream(LANDING_SOURCE, manifest) == "source:landing"
    assert classify_upstream(SEED, manifest) == "seed"
    assert classify_upstream("model.dbt_starter.silver__a", manifest) == "model:silver"
    assert classify_upstream("snapshot.dbt_starter.snap", manifest) == "snapshot"
    assert classify_upstream("model.dbt_starter.gone", manifest) == "unknown"


def test_a_named_edge_permits_that_pair_and_nothing_else() -> None:
    models = [
        _model("bronze__a", "bronze", []),
        _model("bronze__b", "bronze", ["model.dbt_starter.bronze__a"]),
        _model("bronze__c", "bronze", ["model.dbt_starter.bronze__a"]),
    ]
    named = {("model.dbt_starter.bronze__b", "model.dbt_starter.bronze__a")}
    problems = check_layer_direction(_manifest(models, SEED_NODE, SOURCES), allowed_edges=named)
    assert problems == [
        "model.dbt_starter.bronze__c (bronze) reads model.dbt_starter.bronze__a (model:bronze)"
    ]


def test_a_named_edge_does_not_permit_the_reverse_read() -> None:
    models = [
        _model("bronze__a", "bronze", ["model.dbt_starter.bronze__b"]),
        _model("bronze__b", "bronze", []),
    ]
    named = {("model.dbt_starter.bronze__b", "model.dbt_starter.bronze__a")}
    problems = check_layer_direction(_manifest(models, SEED_NODE, SOURCES), allowed_edges=named)
    assert problems == [
        "model.dbt_starter.bronze__a (bronze) reads model.dbt_starter.bronze__b (model:bronze)"
    ]


def test_a_second_bronze_model_reading_a_bronze_model_is_still_flagged() -> None:
    # No named edge is passed, so the real ALLOWED_EDGES applies: it names one pair of models
    # and these are not them.
    models = [
        _model("bronze__a", "bronze", []),
        _model("bronze__b", "bronze", ["model.dbt_starter.bronze__a"]),
    ]
    assert _check(models) == [
        "model.dbt_starter.bronze__b (bronze) reads model.dbt_starter.bronze__a (model:bronze)"
    ]

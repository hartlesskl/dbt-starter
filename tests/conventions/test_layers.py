"""Layer convention: folder, schema, file prefix and table name agree (LAYR-01).

The landing folder holds only source declarations, because the loader writes landing and dbt
never does. So every layer folder (landing, bronze, silver, gold) maps to the schema of the
same name. Models live in bronze, silver or gold; the file name starts with its layer; the
table name is the file name without that prefix; seeds build into bronze.

The checks are pure functions over a manifest dict. The tests run them against the real
DuckDB manifest and the Fabric compile manifest (expect no problems) and against doctored
copies (expect a problem that names the node).
"""

from __future__ import annotations

import copy
from typing import Any

import duckdb
import pytest
from support.dbt_cli import REPO_ROOT, FabricCompile

from starter_cli.project import duckdb_path

PACKAGE = "dbt_starter"

LAYERS = ("bronze", "silver", "gold")
LANDING = "landing"
LAYER_SCHEMAS = frozenset({LANDING, *LAYERS})
DEFAULT_SCHEMA = "main"
SEED_SCHEMA = "bronze"
LANDING_DIR = "models/landing/"


def _model_problems(uid: str, node: dict[str, Any]) -> list[str]:
    folder = node["fqn"][1]
    if folder == LANDING:
        return [f"{uid}: the landing folder holds source declarations only, not models"]
    if folder not in LAYERS:
        return [f"{uid}: folder {folder!r} is not one of {', '.join(LAYERS)}"]
    problems = []
    name = node["name"]
    prefix, separator, rest = name.partition("__")
    if node["schema"] != folder:
        problems.append(f"{uid}: schema {node['schema']!r} differs from its folder {folder!r}")
    if not separator or prefix != folder:
        problems.append(f"{uid}: file name {name!r} must start with {folder + '__'!r}")
    elif node["alias"] != rest:
        problems.append(
            f"{uid}: table name {node['alias']!r} must be {rest!r}, the file name without its prefix"
        )
    return problems


def check_layer_naming(manifest: dict, package: str = "dbt_starter") -> list[str]:
    """Return every disagreement between a node's folder, schema, file name and table name."""
    problems: list[str] = []
    for uid, node in manifest["nodes"].items():
        if node.get("package_name") != package:
            continue
        if node["resource_type"] == "model":
            problems.extend(_model_problems(uid, node))
        elif node["resource_type"] == "seed" and node["schema"] != SEED_SCHEMA:
            problems.append(f"{uid}: seed schema {node['schema']!r} must be {SEED_SCHEMA!r}")
    for uid, source in manifest["sources"].items():
        if source.get("package_name") != package:
            continue
        if not source["original_file_path"].startswith(LANDING_DIR):
            problems.append(
                f"{uid}: sources are declared under {LANDING_DIR}, not in {source['original_file_path']}"
            )
        if source["schema"] != LANDING:
            problems.append(f"{uid}: source schema {source['schema']!r} must be {LANDING!r}")
    return sorted(problems)


def check_built_schemas(names: set[str]) -> list[str]:
    """Return problems with the schemas of a built database, compared case-sensitively."""
    problems = [f"schema {name!r} is missing" for name in sorted(LAYER_SCHEMAS - names)]
    allowed = LAYER_SCHEMAS | {DEFAULT_SCHEMA}
    problems += [
        f"schema {name!r} is not one of {sorted(allowed)}" for name in sorted(names - allowed)
    ]
    return problems


def _model(name: str, folder: str, schema: str, alias: str | None = None) -> dict[str, Any]:
    rest = name.partition("__")[2] or name
    return {
        "resource_type": "model",
        "package_name": PACKAGE,
        "name": name,
        "fqn": [PACKAGE, folder, "group", name],
        "schema": schema,
        "alias": rest if alias is None else alias,
    }


def _seed(name: str, schema: str) -> dict[str, Any]:
    return {
        "resource_type": "seed",
        "package_name": PACKAGE,
        "name": name,
        "fqn": [PACKAGE, name],
        "schema": schema,
        "alias": name,
    }


def _source(path: str, schema: str, package: str = PACKAGE) -> dict[str, Any]:
    return {
        "resource_type": "source",
        "package_name": package,
        "original_file_path": path,
        "schema": schema,
    }


def _doctored(
    manifest: dict,
    nodes: dict[str, dict[str, Any]] | None = None,
    sources: dict[str, dict[str, Any]] | None = None,
) -> dict:
    """Return a deep copy of the manifest with extra nodes and sources added."""
    copied = copy.deepcopy(manifest)
    copied["nodes"].update(nodes or {})
    copied["sources"].update(sources or {})
    return copied


def test_the_real_duckdb_manifest_follows_the_layer_convention(duckdb_manifest: dict) -> None:
    assert check_layer_naming(duckdb_manifest) == []


def test_the_fabric_compile_manifest_follows_the_layer_convention(
    fabric_compiled: FabricCompile,
) -> None:
    assert check_layer_naming(fabric_compiled.manifest) == []


def test_the_real_manifest_has_models_in_every_layer(duckdb_manifest: dict) -> None:
    folders = {
        node["fqn"][1]
        for node in duckdb_manifest["nodes"].values()
        if node["resource_type"] == "model" and node["package_name"] == PACKAGE
    }
    assert folders == set(LAYERS)


@pytest.mark.parametrize(
    ("nodes", "sources", "needle"),
    [
        pytest.param(
            {"model.dbt_starter.silver__a": _model("silver__a", "silver", "gold")},
            {},
            "silver__a",
            id="model-in-folder-silver-with-schema-gold",
        ),
        pytest.param(
            {"model.dbt_starter.gold__b": _model("gold__b", "silver", "silver")},
            {},
            "gold__b",
            id="file-prefix-differs-from-folder",
        ),
        pytest.param(
            {"model.dbt_starter.silver__c": _model("silver__c", "silver", "silver", "silver__c")},
            {},
            "silver__c",
            id="alias-kept-the-prefix",
        ),
        pytest.param(
            {"model.dbt_starter.staging__d": _model("staging__d", "staging", "staging")},
            {},
            "staging__d",
            id="folder-is-not-a-layer",
        ),
        pytest.param(
            {"model.dbt_starter.landing__e": _model("landing__e", "landing", "landing")},
            {},
            "landing__e",
            id="model-in-the-landing-folder",
        ),
        pytest.param(
            {"model.dbt_starter.plain": _model("plain", "silver", "silver")},
            {},
            "plain",
            id="no-layer-prefix-in-the-file-name",
        ),
        pytest.param(
            {"model.dbt_starter.bronze__f": _model("bronze__f", "bronze", "Bronze")},
            {},
            "bronze__f",
            id="schema-case-variant",
        ),
        pytest.param(
            {"seed.dbt_starter.codes": _seed("codes", "main")},
            {},
            "seed.dbt_starter.codes",
            id="seed-outside-bronze",
        ),
        pytest.param(
            {},
            {"source.dbt_starter.s.t": _source("models/bronze/group/_sources.yml", "landing")},
            "source.dbt_starter.s.t",
            id="source-declared-outside-the-landing-folder",
        ),
        pytest.param(
            {},
            {"source.dbt_starter.s.u": _source("models/landing/group/_sources.yml", "raw")},
            "source.dbt_starter.s.u",
            id="source-in-the-landing-folder-with-another-schema",
        ),
    ],
)
def test_a_layer_naming_violation_is_reported_with_the_node_name(
    duckdb_manifest: dict,
    nodes: dict[str, dict[str, Any]],
    sources: dict[str, dict[str, Any]],
    needle: str,
) -> None:
    problems = check_layer_naming(_doctored(duckdb_manifest, nodes, sources))
    assert problems
    assert any(needle in problem for problem in problems)


def test_a_bronze_seed_and_a_well_formed_model_pass(duckdb_manifest: dict) -> None:
    nodes = {
        "seed.dbt_starter.codes": _seed("codes", "bronze"),
        "model.dbt_starter.gold__fine": _model("gold__fine", "gold", "gold"),
    }
    assert check_layer_naming(_doctored(duckdb_manifest, nodes)) == []


def test_a_name_with_two_underscore_runs_keeps_everything_after_the_first_prefix(
    duckdb_manifest: dict,
) -> None:
    nodes = {
        "model.dbt_starter.bronze__a__b": _model("bronze__a__b", "bronze", "bronze"),
        "model.dbt_starter.bronze__c__d": _model("bronze__c__d", "bronze", "bronze", "c"),
    }
    problems = check_layer_naming(_doctored(duckdb_manifest, nodes))
    assert len(problems) == 1
    assert "bronze__c__d" in problems[0]


def test_other_packages_are_not_checked(duckdb_manifest: dict) -> None:
    foreign = _model("plain", "staging", "staging")
    foreign["package_name"] = "dbt_utils"
    foreign_source = _source("anywhere.yml", "raw", package="dbt_utils")
    doctored = _doctored(
        duckdb_manifest,
        {"model.dbt_utils.plain": foreign},
        {"source.dbt_utils.s.t": foreign_source},
    )
    assert check_layer_naming(doctored) == []


def test_every_problem_is_reported_in_sorted_order(duckdb_manifest: dict) -> None:
    nodes = {
        "model.dbt_starter.silver__z": _model("silver__z", "silver", "gold"),
        "model.dbt_starter.silver__a": _model("silver__a", "silver", "gold"),
    }
    problems = check_layer_naming(_doctored(duckdb_manifest, nodes))
    assert len(problems) == 2
    assert problems == sorted(problems)


BUILT = {"landing", "bronze", "silver", "gold"}


def test_the_built_schemas_pass_with_or_without_the_default_schema() -> None:
    assert check_built_schemas({*BUILT, "main"}) == []
    assert check_built_schemas(set(BUILT)) == []


def test_a_missing_layer_schema_is_flagged() -> None:
    problems = check_built_schemas({"landing", "bronze", "silver", "main"})
    assert problems
    assert any("gold" in problem for problem in problems)


def test_a_case_variant_of_a_layer_schema_is_flagged() -> None:
    problems = check_built_schemas({"landing", "Bronze", "silver", "gold", "main"})
    assert problems
    assert any("Bronze" in problem for problem in problems)


def test_a_schema_outside_the_four_layers_is_flagged() -> None:
    problems = check_built_schemas({*BUILT, "main", "scratch"})
    assert any("scratch" in problem for problem in problems)


def test_the_built_database_holds_exactly_the_four_layer_schemas() -> None:
    path = duckdb_path(REPO_ROOT)
    if not path.is_file():
        pytest.fail(
            "Run `uv run demo` first: this check reads the built DuckDB database", pytrace=False
        )
    connection = duckdb.connect(str(path), read_only=True)
    try:
        rows = connection.execute(
            "select schema_name from information_schema.schemata "
            "where catalog_name = current_database()"
        ).fetchall()
    finally:
        connection.close()
    assert check_built_schemas({row[0] for row in rows}) == []

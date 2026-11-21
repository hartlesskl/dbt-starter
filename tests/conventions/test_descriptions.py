"""Every model, seed and source table, and every column it really has, must be described.

A manifest only lists the columns someone wrote down, so a manifest-only check lets an
undocumented column through. The catalog lists the columns the database really holds, so the
check compares the two.
"""

from __future__ import annotations

import copy

NODE_KINDS = ("model", "seed")


def _blank(text: object) -> bool:
    return not isinstance(text, str) or not text.strip()


def _check_relation(
    uid: str, entry: dict, catalog_entry: dict | None, catalog_label: str
) -> list[str]:
    """Return the problems of one manifest entry compared with its catalog entry."""
    problems = []
    if _blank(entry.get("description")):
        problems.append(f"{uid}: description is missing or blank")

    documented = {}
    for name, column in (entry.get("columns") or {}).items():
        documented[name.lower()] = column
        if _blank(column.get("description")):
            problems.append(f"{uid}.{name}: description is missing or blank")

    if catalog_entry is None:
        problems.append(f"{uid}: not found in the catalog ({catalog_label}); build it first")
        return problems
    for name in catalog_entry.get("columns") or {}:
        if name.lower() not in documented:
            problems.append(f"{uid}.{name}: column is in the catalog but not documented in YAML")
    return problems


def check_descriptions(manifest: dict, catalog: dict, package: str = "dbt_starter") -> list[str]:
    """Return every missing description of the package's models, seeds and source tables.

    A description counts as missing when it is absent, empty or only whitespace. A column in
    the catalog that the YAML does not document is a problem too. All problems are returned,
    sorted, so one run shows everything that needs fixing.
    """
    problems: list[str] = []
    catalog_nodes = catalog.get("nodes") or {}
    catalog_sources = catalog.get("sources") or {}

    for uid, node in (manifest.get("nodes") or {}).items():
        if node.get("resource_type") not in NODE_KINDS or node.get("package_name") != package:
            continue
        problems += _check_relation(uid, node, catalog_nodes.get(uid), "catalog nodes")

    for uid, source in (manifest.get("sources") or {}).items():
        if source.get("package_name") != package:
            continue
        problems += _check_relation(uid, source, catalog_sources.get(uid), "catalog sources")

    return sorted(problems)


# --- synthetic fixtures: a small project that is fully described ---------------------------


def _good_pair() -> tuple[dict, dict]:
    manifest = {
        "nodes": {
            "model.proj.alpha": {
                "resource_type": "model",
                "package_name": "proj",
                "description": "Alpha rows.",
                "columns": {
                    "id": {"description": "Row id."},
                    "label": {"description": "Row label."},
                },
            },
            "seed.proj.beta": {
                "resource_type": "seed",
                "package_name": "proj",
                "description": "Beta values.",
                "columns": {"value": {"description": "The value."}},
            },
            "model.other.gamma": {
                "resource_type": "model",
                "package_name": "other",
                "description": "",
                "columns": {},
            },
            "test.proj.some_test": {
                "resource_type": "test",
                "package_name": "proj",
                "description": "",
            },
        },
        "sources": {
            "source.proj.raw.delta": {
                "package_name": "proj",
                "description": "Delta landing table.",
                "columns": {"Code": {"description": "Code as landed."}},
            }
        },
    }
    catalog = {
        "nodes": {
            "model.proj.alpha": {"columns": {"id": {}, "label": {}}},
            "seed.proj.beta": {"columns": {"value": {}}},
        },
        "sources": {"source.proj.raw.delta": {"columns": {"code": {}}}},
    }
    return manifest, catalog


def test_a_fully_described_project_has_no_problems() -> None:
    manifest, catalog = _good_pair()
    assert check_descriptions(manifest, catalog, package="proj") == []


def test_other_packages_and_tests_are_ignored() -> None:
    manifest, catalog = _good_pair()
    # `model.other.gamma` has no description and is not in the catalog, but is not ours.
    assert check_descriptions(manifest, catalog, package="proj") == []
    assert check_descriptions(manifest, catalog, package="other") != []


def test_a_whitespace_only_model_description_names_the_model() -> None:
    manifest, catalog = _good_pair()
    manifest["nodes"]["model.proj.alpha"]["description"] = "   "
    problems = check_descriptions(manifest, catalog, package="proj")
    assert len(problems) == 1
    assert "model.proj.alpha" in problems[0]


def test_an_empty_seed_description_names_the_seed() -> None:
    manifest, catalog = _good_pair()
    manifest["nodes"]["seed.proj.beta"]["description"] = ""
    problems = check_descriptions(manifest, catalog, package="proj")
    assert len(problems) == 1
    assert "seed.proj.beta" in problems[0]


def test_an_empty_source_table_description_names_the_source() -> None:
    manifest, catalog = _good_pair()
    manifest["sources"]["source.proj.raw.delta"]["description"] = " \n "
    problems = check_descriptions(manifest, catalog, package="proj")
    assert len(problems) == 1
    assert "source.proj.raw.delta" in problems[0]


def test_an_empty_column_description_names_model_and_column() -> None:
    manifest, catalog = _good_pair()
    manifest["nodes"]["model.proj.alpha"]["columns"]["label"]["description"] = ""
    problems = check_descriptions(manifest, catalog, package="proj")
    assert len(problems) == 1
    assert "model.proj.alpha.label" in problems[0]


def test_a_whitespace_only_source_column_description_fails() -> None:
    manifest, catalog = _good_pair()
    manifest["sources"]["source.proj.raw.delta"]["columns"]["Code"]["description"] = "\t"
    problems = check_descriptions(manifest, catalog, package="proj")
    assert len(problems) == 1
    assert "Code" in problems[0]


def test_a_catalog_column_missing_from_the_yaml_is_named() -> None:
    manifest, catalog = _good_pair()
    catalog["nodes"]["model.proj.alpha"]["columns"]["surprise_col"] = {}
    problems = check_descriptions(manifest, catalog, package="proj")
    assert len(problems) == 1
    assert "surprise_col" in problems[0]


def test_a_source_catalog_column_missing_from_the_yaml_is_named() -> None:
    manifest, catalog = _good_pair()
    catalog["sources"]["source.proj.raw.delta"]["columns"]["extra"] = {}
    problems = check_descriptions(manifest, catalog, package="proj")
    assert problems == [
        "source.proj.raw.delta.extra: column is in the catalog but not documented in YAML"
    ]


def test_column_names_compare_ignoring_case() -> None:
    manifest, catalog = _good_pair()
    catalog["nodes"]["model.proj.alpha"]["columns"] = {"ID": {}, "Label": {}}
    assert check_descriptions(manifest, catalog, package="proj") == []


def test_a_model_missing_from_the_catalog_is_a_problem() -> None:
    manifest, catalog = _good_pair()
    del catalog["nodes"]["seed.proj.beta"]
    problems = check_descriptions(manifest, catalog, package="proj")
    assert len(problems) == 1
    assert "seed.proj.beta" in problems[0]


def test_every_problem_is_reported_and_sorted() -> None:
    manifest, catalog = copy.deepcopy(_good_pair())
    manifest["nodes"]["seed.proj.beta"]["description"] = ""
    manifest["nodes"]["model.proj.alpha"]["columns"]["id"]["description"] = " "
    catalog["nodes"]["model.proj.alpha"]["columns"]["surprise_col"] = {}
    problems = check_descriptions(manifest, catalog, package="proj")
    assert len(problems) == 3
    assert problems == sorted(problems)
    joined = "\n".join(problems)
    assert "seed.proj.beta" in joined
    assert "model.proj.alpha.id" in joined
    assert "surprise_col" in joined


# --- the real project ------------------------------------------------------------------------


def test_the_real_project_is_fully_described(duckdb_manifest: dict, duckdb_catalog: dict) -> None:
    problems = check_descriptions(duckdb_manifest, duckdb_catalog)
    assert problems == [], "\n".join(problems)


def test_the_real_check_looked_at_something(duckdb_manifest: dict, duckdb_catalog: dict) -> None:
    kinds = {
        node["resource_type"]
        for node in duckdb_manifest["nodes"].values()
        if node.get("package_name") == "dbt_starter" and node["resource_type"] in NODE_KINDS
    }
    assert kinds == {"model", "seed"}
    assert any(
        source.get("package_name") == "dbt_starter"
        for source in duckdb_manifest["sources"].values()
    )
    assert duckdb_catalog["nodes"] and duckdb_catalog["sources"]

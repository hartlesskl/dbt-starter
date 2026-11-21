"""Landing source contract (LAND-08).

Every source the project declares must say where it lives and how fresh it is, and must prove
that a landed row is identified by its file and its position in that file. Concretely, each
source has schema `landing`, `loaded_at_field` `_loaded_at`, a warn and an error freshness
threshold, a description, and a uniqueness test over (`_source_file`, `_source_row_number`).
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from support.dbt_cli import REPO_ROOT, run_dbt

from starter_cli.project import duckdb_path

PACKAGE = "dbt_starter"

LANDING = "landing"
LOADED_AT_FIELD = "_loaded_at"
ROW_IDENTITY = ["_source_file", "_source_row_number"]
THRESHOLDS = ("warn_after", "error_after")


def _field(source: dict[str, Any], key: str) -> Any:
    """Read a source field wherever dbt stores it: top-level or under `config`."""
    return source.get(key) or (source.get("config") or {}).get(key)


def _threshold_problem(freshness: dict[str, Any] | None, key: str) -> str | None:
    threshold = (freshness or {}).get(key)
    if not threshold or threshold.get("count") is None or not threshold.get("period"):
        return f"freshness {key} needs a count and a period"
    return None


def _has_row_identity_test(manifest: dict, uid: str) -> bool:
    for node in manifest["nodes"].values():
        metadata = node.get("test_metadata") or {}
        if (
            node["resource_type"] == "test"
            and metadata.get("name") == "unique_combination_of_columns"
            and uid in node["depends_on"]["nodes"]
            and sorted(metadata["kwargs"].get("combination_of_columns") or [])
            == sorted(ROW_IDENTITY)
        ):
            return True
    return False


def check_sources(manifest: dict, package: str = "dbt_starter") -> list[str]:
    """Return every way a source of `package` breaks the landing source contract."""
    problems = []
    for uid, source in manifest["sources"].items():
        if source.get("package_name") != package:
            continue
        if source.get("schema") != LANDING:
            problems.append(f"{uid}: schema {source.get('schema')!r} must be {LANDING!r}")
        if _field(source, "loaded_at_field") != LOADED_AT_FIELD:
            problems.append(f"{uid}: loaded_at_field must be {LOADED_AT_FIELD!r}")
        freshness = _field(source, "freshness")
        if not freshness:
            problems.append(f"{uid}: freshness is missing (warn_after and error_after)")
        else:
            problems += [
                f"{uid}: {problem}"
                for problem in (_threshold_problem(freshness, key) for key in THRESHOLDS)
                if problem
            ]
        if not (source.get("description") or "").strip():
            problems.append(f"{uid}: description is blank")
        if not _has_row_identity_test(manifest, uid):
            problems.append(
                f"{uid}: needs a unique_combination_of_columns test over {', '.join(ROW_IDENTITY)}"
            )
    return sorted(problems)


def _first_source_uid(manifest: dict) -> str:
    return next(
        uid for uid, source in manifest["sources"].items() if source["package_name"] == PACKAGE
    )


def _set_both(source: dict[str, Any], key: str, value: object) -> None:
    """Set a field both top-level and under `config`, whichever dbt reads."""
    source[key] = value
    source.setdefault("config", {})[key] = value


def _doctor(manifest: dict, change) -> dict:
    copied = copy.deepcopy(manifest)
    uid = _first_source_uid(copied)
    change(copied, uid, copied["sources"][uid])
    return copied


def _is_uniqueness_test(node: dict[str, Any], uid: str) -> bool:
    metadata = node.get("test_metadata") or {}
    return (
        node["resource_type"] == "test"
        and metadata.get("name") == "unique_combination_of_columns"
        and uid in node["depends_on"]["nodes"]
    )


def test_the_real_manifest_meets_the_source_contract(duckdb_manifest: dict) -> None:
    assert check_sources(duckdb_manifest) == []


def test_the_real_manifest_declares_a_source_to_check(duckdb_manifest: dict) -> None:
    assert any(s["package_name"] == PACKAGE for s in duckdb_manifest["sources"].values())


def test_the_check_reads_freshness_wherever_dbt_stores_it(duckdb_manifest: dict) -> None:
    def top_level_only(_manifest: dict, _uid: str, source: dict[str, Any]) -> None:
        source["freshness"] = copy.deepcopy(_freshness_of(source))
        source["config"] = {}
        source["loaded_at_field"] = "_loaded_at"

    def config_only(_manifest: dict, _uid: str, source: dict[str, Any]) -> None:
        config = source.setdefault("config", {})
        config["freshness"] = copy.deepcopy(_freshness_of(source))
        config["loaded_at_field"] = "_loaded_at"
        source["freshness"] = None
        source["loaded_at_field"] = None

    assert check_sources(_doctor(duckdb_manifest, top_level_only)) == []
    assert check_sources(_doctor(duckdb_manifest, config_only)) == []


def _freshness_of(source: dict[str, Any]) -> Any:
    return source.get("freshness") or source["config"]["freshness"]


def _no_freshness(_manifest: dict, _uid: str, source: dict[str, Any]) -> None:
    _set_both(source, "freshness", None)


def _warn_only(_manifest: dict, _uid: str, source: dict[str, Any]) -> None:
    freshness = {"warn_after": {"count": 1, "period": "day"}, "error_after": None}
    _set_both(source, "freshness", freshness)


def _error_only(_manifest: dict, _uid: str, source: dict[str, Any]) -> None:
    freshness = {"warn_after": None, "error_after": {"count": 1, "period": "day"}}
    _set_both(source, "freshness", freshness)


def _threshold_without_period(_manifest: dict, _uid: str, source: dict[str, Any]) -> None:
    freshness = {"warn_after": {"count": 1}, "error_after": {"count": 2, "period": "day"}}
    _set_both(source, "freshness", freshness)


def _wrong_loaded_at(_manifest: dict, _uid: str, source: dict[str, Any]) -> None:
    _set_both(source, "loaded_at_field", "_updated_at")


def _no_loaded_at(_manifest: dict, _uid: str, source: dict[str, Any]) -> None:
    _set_both(source, "loaded_at_field", None)


def _wrong_schema(_manifest: dict, _uid: str, source: dict[str, Any]) -> None:
    source["schema"] = "raw"


def _blank_description(_manifest: dict, _uid: str, source: dict[str, Any]) -> None:
    source["description"] = "   \n"


def _drop_uniqueness_test(manifest: dict, uid: str, _source: dict[str, Any]) -> None:
    manifest["nodes"] = {
        key: node for key, node in manifest["nodes"].items() if not _is_uniqueness_test(node, uid)
    }


def _uniqueness_on_the_wrong_columns(manifest: dict, uid: str, _source: dict[str, Any]) -> None:
    for node in manifest["nodes"].values():
        if _is_uniqueness_test(node, uid):
            node["test_metadata"]["kwargs"]["combination_of_columns"] = ["_source_file"]


def _uniqueness_test_on_another_source(manifest: dict, uid: str, _source: dict[str, Any]) -> None:
    for node in manifest["nodes"].values():
        if _is_uniqueness_test(node, uid):
            node["depends_on"]["nodes"] = ["source.dbt_starter.elsewhere.table"]


@pytest.mark.parametrize(
    ("change", "needle"),
    [
        pytest.param(_no_freshness, "freshness", id="missing-freshness"),
        pytest.param(_warn_only, "error_after", id="no-error-threshold"),
        pytest.param(_error_only, "warn_after", id="no-warn-threshold"),
        pytest.param(_threshold_without_period, "warn_after", id="threshold-without-period"),
        pytest.param(_wrong_loaded_at, "loaded_at_field", id="wrong-loaded-at-field"),
        pytest.param(_no_loaded_at, "loaded_at_field", id="missing-loaded-at-field"),
        pytest.param(_wrong_schema, "schema", id="schema-other-than-landing"),
        pytest.param(_blank_description, "description", id="blank-description"),
        pytest.param(
            _drop_uniqueness_test, "unique_combination_of_columns", id="no-uniqueness-test"
        ),
        pytest.param(
            _uniqueness_on_the_wrong_columns,
            "unique_combination_of_columns",
            id="uniqueness-over-the-wrong-columns",
        ),
        pytest.param(
            _uniqueness_test_on_another_source,
            "unique_combination_of_columns",
            id="uniqueness-test-for-another-source",
        ),
    ],
)
def test_a_broken_source_contract_is_reported(duckdb_manifest: dict, change, needle: str) -> None:
    problems = check_sources(_doctor(duckdb_manifest, change))
    assert problems
    assert any(needle in problem for problem in problems)
    uid = _first_source_uid(duckdb_manifest)
    assert all(uid in problem for problem in problems)


def test_the_uniqueness_columns_may_come_in_either_order(duckdb_manifest: dict) -> None:
    def swapped(manifest: dict, uid: str, _source: dict[str, Any]) -> None:
        for node in manifest["nodes"].values():
            if _is_uniqueness_test(node, uid):
                node["test_metadata"]["kwargs"]["combination_of_columns"] = [
                    "_source_row_number",
                    "_source_file",
                ]

    assert check_sources(_doctor(duckdb_manifest, swapped)) == []


def test_sources_of_other_packages_are_not_checked(duckdb_manifest: dict) -> None:
    doctored = copy.deepcopy(duckdb_manifest)
    doctored["sources"]["source.other.feed.table"] = {
        "package_name": "other",
        "schema": "raw",
        "description": "",
    }
    assert check_sources(doctored) == []


def test_source_freshness_passes_on_the_built_database() -> None:
    if not duckdb_path(REPO_ROOT).is_file():
        pytest.fail(
            "Run `uv run demo` first: freshness reads the built DuckDB database", pytrace=False
        )
    result = run_dbt(
        ["source", "freshness", "--target", "duckdb", "--target-path", "target/freshness"]
    )
    assert "ERROR STALE" not in result.stdout

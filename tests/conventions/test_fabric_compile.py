"""The compiled Fabric SQL of the project's own packages must be clean.

The compile is offline: dummy lowercase env values and `--no-populate-cache`, so no
connection is attempted. Third-party package output under `compiled/` is not asserted on,
because the project does not own that SQL.
"""

from __future__ import annotations

import pytest
from support.dbt_cli import FabricCompile
from support.sql_scan import scan_compiled_dir

PROJECT_PACKAGES = ("dbt_starter", "starter")


def check_materializations(manifest: dict) -> list[str]:
    """Return one problem per project model that is ephemeral or incremental.

    Both are outside the portable subset: ephemeral models inline as nested CTEs, and
    incremental models need merge or delete+insert behaviour the Fabric adapter handles
    differently.
    """
    problems = []
    for node_id, node in manifest.get("nodes", {}).items():
        if node.get("resource_type") != "model" or node.get("package_name") != "dbt_starter":
            continue
        materialized = (node.get("config") or {}).get("materialized")
        if materialized in ("ephemeral", "incremental"):
            problems.append(f"{node_id} is materialized as {materialized}")
    return problems


def test_models_were_compiled(fabric_compiled: FabricCompile) -> None:
    models = list((fabric_compiled.compiled_dir / "dbt_starter" / "models").rglob("*.sql"))
    assert models, "the Fabric compile produced no model SQL under dbt_starter/models"


@pytest.mark.parametrize("package", PROJECT_PACKAGES)
def test_project_package_compiled_sql_is_clean(
    fabric_compiled: FabricCompile, package: str
) -> None:
    root = fabric_compiled.compiled_dir / package
    assert root.is_dir(), f"no compiled output for package {package}: {root}"
    assert list(root.rglob("*.sql")), (
        f"{root} holds no .sql file, so the scan would pass on nothing"
    )
    problems = scan_compiled_dir(root)
    assert problems == {}, f"{package}: {problems}"


def test_no_project_model_is_ephemeral_or_incremental(fabric_compiled: FabricCompile) -> None:
    assert check_materializations(fabric_compiled.manifest) == []


@pytest.mark.parametrize("materialized", ["ephemeral", "incremental"])
def test_check_materializations_flags_a_doctored_manifest(materialized: str) -> None:
    manifest = {
        "nodes": {
            "model.dbt_starter.m": {
                "resource_type": "model",
                "package_name": "dbt_starter",
                "config": {"materialized": materialized},
            },
            "model.other.n": {
                "resource_type": "model",
                "package_name": "other",
                "config": {"materialized": materialized},
            },
        }
    }
    problems = check_materializations(manifest)
    assert len(problems) == 1
    assert "model.dbt_starter.m" in problems[0]


def test_fabric_shims_were_dispatched(fabric_compiled: FabricCompile) -> None:
    files = list((fabric_compiled.compiled_dir / "dbt_starter" / "models").rglob("*.sql"))
    texts = [f.read_text().lower() for f in files]
    assert any("hashbytes" in t for t in texts), "no compiled model uses the fabric hash shim"
    assert not any("md5(" in t for t in texts), "a compiled model carries the DuckDB md5 shim"

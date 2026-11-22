"""Session fixtures shared by every pytest suite.

None of them is autouse, so a suite that does not ask for one (the loader tests, for example)
stays offline and fast. Each dbt run writes to its own `--target-path`, so no run overwrites
another run's manifest.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from support.dbt_cli import (
    DUMMY_FABRIC_ENV,
    REPO_ROOT,
    FabricCompile,
    require_fabric_adapter,
    run_dbt,
)

from starter_cli.gate import FixtureBuild, build_fixture
from starter_cli.project import duckdb_path


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def dbt_packages_ready() -> None:
    """Install the dbt packages once, only when `dbt_packages/` is missing."""
    if not (REPO_ROOT / "dbt_packages").is_dir():
        run_dbt(["deps"], profiles=False)


@pytest.fixture(scope="session")
def duckdb_manifest(dbt_packages_ready: None) -> dict:
    """The manifest of `dbt parse` on the DuckDB target."""
    run_dbt(
        [
            "parse",
            "--target",
            "duckdb",
            "--target-path",
            "target/duckdb",
            "--no-partial-parse",
        ]
    )
    return json.loads((REPO_ROOT / "target" / "duckdb" / "manifest.json").read_text())


@pytest.fixture(scope="session")
def fabric_compiled(dbt_packages_ready: None) -> FabricCompile:
    """Compile every model and test for Fabric offline, from dummy lowercase env values."""
    require_fabric_adapter()
    run_dbt(
        [
            "compile",
            "--target",
            "fabric",
            "--no-populate-cache",
            "--target-path",
            "target/fabric",
        ],
        env=DUMMY_FABRIC_ENV,
    )
    manifest = json.loads((REPO_ROOT / "target" / "fabric" / "manifest.json").read_text())
    return FabricCompile(
        manifest=manifest, compiled_dir=REPO_ROOT / "target" / "fabric" / "compiled"
    )


@pytest.fixture(scope="session")
def duckdb_catalog(dbt_packages_ready: None) -> dict:
    """The catalog of `dbt docs generate`, which reads the built DuckDB database."""
    if not duckdb_path(REPO_ROOT).is_file():
        pytest.fail(
            "Run `uv run demo` first: this check reads the built DuckDB database",
            pytrace=False,
        )
    run_dbt(["docs", "generate", "--target", "duckdb", "--target-path", "target/docs-check"])
    return json.loads((REPO_ROOT / "target" / "docs-check" / "catalog.json").read_text())


@pytest.fixture(scope="session")
def fixture_build(
    dbt_packages_ready: None, tmp_path_factory: pytest.TempPathFactory
) -> FixtureBuild:
    """The gate's fixture database: the one the gate already built, else a fresh build.

    The gate exports `GATE_DATABASE` and `GATE_RUN_RESULTS` after its fixture-build step, so the
    checks that run later read that build instead of making a second one.
    """
    database = os.environ.get("GATE_DATABASE")
    run_results = os.environ.get("GATE_RUN_RESULTS")
    if database and run_results and Path(database).is_file() and Path(run_results).is_file():
        return FixtureBuild(Path(database), Path(run_results).parent, Path(run_results))
    return build_fixture(REPO_ROOT, tmp_path_factory.mktemp("fixture_build"))

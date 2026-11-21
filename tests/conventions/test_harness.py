"""Smoke tests for the shared session fixtures other test modules build on."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from support import dbt_cli

from starter_cli.project import duckdb_path


def test_fabric_hint_names_the_documented_command() -> None:
    assert "uv run --extra fabric pytest" in dbt_cli.FABRIC_EXTRA_HINT


def test_dummy_fabric_env_is_lowercase_and_has_no_credentials() -> None:
    env = dbt_cli.DUMMY_FABRIC_ENV
    assert set(env) == {"FABRIC_WORKSPACE_NAME", "FABRIC_DATABASE"}
    assert all(v == v.lower() for v in env.values())


def test_a_missing_fabric_adapter_fails_with_the_hint_never_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dbt_cli, "fabric_adapter_installed", lambda: False)
    with pytest.raises(pytest.fail.Exception, match="uv run --extra fabric pytest"):
        dbt_cli.require_fabric_adapter()


def test_require_fabric_adapter_passes_when_the_adapter_is_there(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dbt_cli, "fabric_adapter_installed", lambda: True)
    dbt_cli.require_fabric_adapter()


def test_run_dbt_raises_with_the_command_and_exit_code_on_failure() -> None:
    with pytest.raises(AssertionError) as info:
        dbt_cli.run_dbt(["not-a-real-subcommand"], profiles=False)
    assert "not-a-real-subcommand" in str(info.value)
    assert "exit" in str(info.value).lower()


def test_run_dbt_returns_the_result_when_check_is_off() -> None:
    result = dbt_cli.run_dbt(["not-a-real-subcommand"], profiles=False, check=False)
    assert result.returncode != 0


def test_repo_root_fixture_points_at_the_project(repo_root: Path) -> None:
    assert (repo_root / "dbt_project.yml").is_file()
    assert repo_root == dbt_cli.REPO_ROOT


def test_duckdb_manifest_lists_the_project_models(duckdb_manifest: dict) -> None:
    models = [
        n
        for n in duckdb_manifest["nodes"].values()
        if n["resource_type"] == "model" and n["package_name"] == "dbt_starter"
    ]
    assert models
    assert "unit_tests" in duckdb_manifest


def test_each_run_writes_to_its_own_target_path(duckdb_manifest: dict, repo_root: Path) -> None:
    assert (repo_root / "target" / "duckdb" / "manifest.json").is_file()


def test_duckdb_catalog_has_nodes(duckdb_catalog: dict) -> None:
    assert duckdb_catalog["nodes"]


def test_database_opens_read_only_after_the_catalog_run(
    duckdb_catalog: dict, repo_root: Path
) -> None:
    con = duckdb.connect(str(duckdb_path(repo_root)), read_only=True)
    try:
        assert con.execute("select 1").fetchone() == (1,)
    finally:
        con.close()

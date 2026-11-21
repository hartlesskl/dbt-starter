"""The committed profile holds no secret, host or tenant (DEMO-05, T-01-18).

`profiles.yml` is committed on purpose (decision record 0003): the DuckDB target needs no
setup, and the Fabric target reads every setting from an environment variable. So every Fabric
field except a short list of non-secret driver settings must be one `env_var()` call, the
fields that identify a tenant or carry a secret must have no default, and the file must hold
no GUID or Fabric host literal. A user with no FABRIC_* variables set must still run on DuckDB.
"""

from __future__ import annotations

import copy
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from support.dbt_cli import REPO_ROOT

PROFILE = "dbt_starter"

# GREEN: constants and check_fabric_profile go here.


def _raw_text() -> str:
    return (REPO_ROOT / "profiles.yml").read_text()


def _fabric_output() -> dict:
    return yaml.safe_load(_raw_text())[PROFILE]["outputs"]["fabric"]


def test_the_default_target_is_duckdb_and_its_path_can_be_overridden() -> None:
    profile = yaml.safe_load(_raw_text())[PROFILE]
    assert profile["target"] == "duckdb"
    assert profile["outputs"]["duckdb"]["type"] == "duckdb"
    assert "env_var('DBT_DUCKDB_PATH'" in profile["outputs"]["duckdb"]["path"]


def test_the_real_fabric_output_reads_only_environment_variables() -> None:
    fabric = _fabric_output()
    assert fabric["type"] == "fabric"
    assert check_fabric_profile(fabric, _raw_text()) == []


def test_the_real_profile_has_env_var_fields_to_check() -> None:
    checked = [key for key in _fabric_output() if key not in LITERAL_KEYS]
    assert {"workspace_name", "database"} <= set(checked)


def _with(**changes: object) -> dict:
    output = copy.deepcopy(_fabric_output())
    output.update(changes)
    return output


@pytest.mark.parametrize(
    ("changes", "extra_text", "needle"),
    [
        pytest.param({"database": "mywarehouse"}, "", "database", id="literal-database"),
        pytest.param(
            {"client_secret": "{{ env_var('FABRIC_CLIENT_SECRET', 'hunter2') }}"},
            "",
            "client_secret",
            id="secret-with-a-default",
        ),
        pytest.param(
            {"tenant_id": "{{ env_var('FABRIC_TENANT_ID', '') }}"},
            "",
            "tenant_id",
            id="tenant-with-an-empty-default",
        ),
        pytest.param(
            {"workspace_name": "{{ env_var('FABRIC_WORKSPACE_NAME', 'dev') }}"},
            "",
            "workspace_name",
            id="workspace-with-a-default",
        ),
        pytest.param(
            {"host": "abc.datawarehouse.fabric.microsoft.com"},
            "host: abc.datawarehouse.fabric.microsoft.com\n",
            "host",
            id="literal-host",
        ),
        pytest.param(
            {"host": "{{ env_var('FABRIC_HOST', 'abc.datawarehouse.fabric.microsoft.com') }}"},
            "",
            "host",
            id="host-default",
        ),
        pytest.param(
            {"client_id": "12345678-90ab-cdef-1234-567890abcdef"},
            "client_id: 12345678-90AB-cdef-1234-567890abcdef\n",
            "client_id",
            id="guid-literal",
        ),
        pytest.param(
            {"schema": "{{ env_var('fabric_schema', 'dbo') }}"},
            "",
            "schema",
            id="lowercase-variable-name",
        ),
        pytest.param({"port": 1433}, "", "port", id="non-string-value"),
        pytest.param(
            {"schema": "prefix_{{ env_var('FABRIC_SCHEMA') }}"}, "", "schema", id="mixed-literal"
        ),
    ],
)
def test_a_literal_or_defaulted_fabric_field_is_reported(
    changes: dict, extra_text: str, needle: str
) -> None:
    problems = check_fabric_profile(_with(**changes), _raw_text() + extra_text)
    assert problems
    assert any(needle in problem for problem in problems)


def test_a_guid_or_host_literal_anywhere_in_the_text_is_reported() -> None:
    guid = "# tenant 12345678-90ab-cdef-1234-567890abcdef\n"
    assert check_fabric_profile(_fabric_output(), _raw_text() + guid)
    host = "# host abc.fabric.microsoft.com\n"
    assert check_fabric_profile(_fabric_output(), _raw_text() + host)
    warehouse = "# host abc.datawarehouse.example\n"
    assert check_fabric_profile(_fabric_output(), _raw_text() + warehouse)


def test_the_driver_settings_may_be_literals() -> None:
    output = _with(driver="ODBC Driver 18 for SQL Server", threads=2, retries=3, login_timeout=30)
    output["query_timeout"] = 600
    assert check_fabric_profile(output, _raw_text()) == []


def test_an_env_var_without_a_default_is_fine_for_a_secret_bearing_key() -> None:
    output = _with(client_secret="{{ env_var('FABRIC_CLIENT_SECRET') }}")
    assert check_fabric_profile(output, _raw_text()) == []


def test_a_default_is_fine_for_a_key_that_is_not_secret_bearing() -> None:
    output = _with(schema="{{ env_var('FABRIC_SCHEMA', 'dbo') }}")
    assert check_fabric_profile(output, _raw_text()) == []


def test_dbt_parse_on_duckdb_works_with_every_fabric_variable_unset(
    tmp_path: Path, dbt_packages_ready: None
) -> None:
    clean_env = {k: v for k, v in os.environ.items() if not k.startswith("FABRIC_")}
    clean_env["DBT_DUCKDB_PATH"] = str(tmp_path / "parse.duckdb")
    assert not any(key.startswith("FABRIC_") for key in clean_env)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "dbt.cli.main",
            "parse",
            "--target",
            "duckdb",
            "--target-path",
            str(tmp_path / "t"),
            "--profiles-dir",
            str(REPO_ROOT),
            "--project-dir",
            str(REPO_ROOT),
        ],
        env=clean_env,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    tail = "\n".join((result.stdout + result.stderr).splitlines()[-30:])
    assert result.returncode == 0, f"dbt parse failed with every FABRIC_* variable unset:\n{tail}"

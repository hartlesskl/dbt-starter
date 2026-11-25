"""The committed profile holds no secret, host or tenant (DEMO-05, T-01-18).

`profiles.yml` is committed on purpose (decision record 0003): the DuckDB target needs no
setup, and the Fabric target reads every setting from an environment variable. So every Fabric
field except a short list of non-secret Livy settings must be one `env_var()` call, the
fields that identify a workspace, a Lakehouse, a tenant or carry a secret must have no default,
and the file must hold no GUID or Fabric host literal. A user with no FABRIC_* variables set
must still run on DuckDB.
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

LITERAL_KEYS = frozenset({"type", "method", "threads", "spark_config"})
NO_DEFAULT_KEYS = frozenset(
    {
        "workspaceid",
        "lakehouseid",
        "lakehouse",
        "endpoint",
        "tenant_id",
        "client_id",
        "client_secret",
        "access_token",
    }
)
SPARK_CONFIG_KEYS = frozenset({"name", "conf"})
# The session mode is a flag, so it is one env_var() call compared with 'true'; the default is on
# (plan 05-13: CI turns it off because high-concurrency sessions refuse a service principal).
SESSION_MODE_KEY = "high_concurrency"
SESSION_MODE_VALUE = "{{ env_var('FABRIC_HIGH_CONCURRENCY', 'true') | lower == 'true' }}"
ENV_VAR_RE = re.compile(r"^\{\{\s*env_var\('([A-Z0-9_]+)'(?:\s*,\s*'([^']*)')?\s*\)\s*\}\}$")
GUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
HOST_RE = re.compile(r"\.fabric\.microsoft\.com", re.IGNORECASE)


def check_fabric_profile(fabric_output: dict, raw_text: str) -> list[str]:
    """Return every Fabric field that is not a bare `env_var()` call, plus GUID or host literals."""
    problems = []
    for key, value in fabric_output.items():
        if key in LITERAL_KEYS:
            continue
        if key == SESSION_MODE_KEY:
            if value != SESSION_MODE_VALUE:
                problems.append(f"{key}: must be exactly `{SESSION_MODE_VALUE}`")
            continue
        match = ENV_VAR_RE.match(value) if isinstance(value, str) else None
        if match is None:
            problems.append(f"{key}: must be a single env_var() call, not a literal")
        elif key in NO_DEFAULT_KEYS and match.group(2) is not None:
            problems.append(f"{key}: env_var('{match.group(1)}') must have no default")
    if GUID_RE.search(raw_text):
        problems.append("profiles.yml contains a GUID literal")
    if HOST_RE.search(raw_text):
        problems.append("profiles.yml contains a Fabric host literal")
    return problems


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
    assert fabric["type"] == "fabricspark"
    assert check_fabric_profile(fabric, _raw_text()) == []


def test_the_real_profile_has_env_var_fields_to_check() -> None:
    checked = [key for key in _fabric_output() if key not in LITERAL_KEYS]
    assert {"workspaceid", "lakehouseid", "lakehouse"} <= set(checked)


def _with(**changes: object) -> dict:
    output = copy.deepcopy(_fabric_output())
    output.update(changes)
    return output


@pytest.mark.parametrize(
    ("changes", "extra_text", "needle"),
    [
        pytest.param({"lakehouse": "mylakehouse"}, "", "lakehouse", id="literal-lakehouse"),
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
            {"workspaceid": "{{ env_var('FABRIC_WORKSPACE_ID', 'dev') }}"},
            "",
            "workspaceid",
            id="workspaceid-with-a-default",
        ),
        pytest.param(
            {"endpoint": "https://api.fabric.microsoft.com/v1"},
            "endpoint: https://api.fabric.microsoft.com/v1\n",
            "endpoint",
            id="literal-endpoint",
        ),
        pytest.param(
            {"endpoint": "{{ env_var('FABRIC_ENDPOINT', 'https://api.fabric.microsoft.com/v1') }}"},
            "",
            "endpoint",
            id="endpoint-default",
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
        pytest.param({"lakehouseid": 42}, "", "lakehouseid", id="non-string-value"),
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


def test_the_livy_settings_may_be_literals() -> None:
    output = _with(
        method="livy",
        threads=2,
        spark_config={"name": "x", "conf": {"spark.sql.session.timeZone": "UTC"}},
    )
    assert check_fabric_profile(output, _raw_text()) == []


def check_spark_config(spark_config: dict) -> list[str]:
    """Return a problem for a Spark config that holds more than a name and `spark.` settings."""
    problems = [
        f"unexpected spark_config key {key!r}"
        for key in spark_config
        if key not in SPARK_CONFIG_KEYS
    ]
    problems += [
        f"conf key {key!r} does not start with spark."
        for key in spark_config.get("conf", {})
        if not str(key).startswith("spark.")
    ]
    if "name" not in spark_config:
        problems.append("spark_config has no name")
    return problems


def test_spark_config_holds_only_a_name_and_spark_settings() -> None:
    spark_config = _fabric_output()["spark_config"]
    assert set(spark_config) == SPARK_CONFIG_KEYS
    assert check_spark_config(spark_config) == []
    assert check_spark_config({**spark_config, "driver_memory": "8g"})
    assert check_spark_config({**spark_config, "conf": {"timeZone": "UTC"}})


def test_the_session_mode_must_default_to_high_concurrency_on() -> None:
    assert _fabric_output()[SESSION_MODE_KEY] == SESSION_MODE_VALUE
    off = "{{ env_var('FABRIC_HIGH_CONCURRENCY', 'false') | lower == 'true' }}"
    assert check_fabric_profile(_with(high_concurrency=off), _raw_text())
    assert check_fabric_profile(_with(high_concurrency=False), _raw_text())


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

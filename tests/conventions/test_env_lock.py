"""The locked environment (DEMO-04, T-01-SC, D-07, D-09, D-10).

`uv sync --locked` must give a user DuckDB at the exact pinned version and nothing for Fabric:
no Azure, MSAL or Fabric adapter package. Those arrive only with `--extra fabric`. The check
reads `uv export --locked`, which is offline and deterministic, instead of inspecting `.venv`
(a venv that once held the extra keeps its packages, RESEARCH Pitfall 6). `uv lock --check`
keeps `uv.lock` and `pyproject.toml` in agreement, so an unreviewed dependency change fails.

The checks on the dbt line test consistency, not a copy of someone else's version: the locked
dbt-core must satisfy the range both projects require, the two projects must require the same
line, the adapter pin in the extra must be one exact `==` version that the lock agrees with,
and no file may use a feature that belongs to the dbt 1.12 line (D-07). No test says what
version the native Fabric job runs; that lives in pyproject.toml, uv.lock and ADR 0006 (D-11).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tomllib
from collections.abc import Mapping

import pytest
import yaml
from packaging.specifiers import SpecifierSet
from packaging.version import Version
from support.dbt_cli import REPO_ROOT

FORBIDDEN_PREFIXES = ("azure", "msal", "dbt-fabricspark")
EXPORT_ARGS = ("export", "--locked", "--no-hashes", "--no-emit-project")
DBT_LINE = [">=1.11.0", "<1.12.0"]

EXPORT_LINE_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+)")

# What a 1.12-line identity column looks like in a model YAML (`identity:` under a column's
# meta) or in hand-written DDL (`identity(` and the two `generated ... as identity` spellings).
IDENTITY_RE = re.compile(
    r"\bidentity\s*[:(]|\bgenerated\s+(?:always|by\s+default)\s+as\s+identity\b",
    re.IGNORECASE,
)


def normalize(name: str) -> str:
    """Return the PEP 503 normalized package name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_export(text: str) -> dict[str, str]:
    """Map package name to version from `uv export` output, ignoring comments and markers."""
    pins: dict[str, str] = {}
    for line in text.splitlines():
        match = EXPORT_LINE_RE.match(line)
        if match:
            pins[normalize(match.group(1))] = match.group(2)
    return pins


def forbidden_packages(pins: dict[str, str]) -> list[str]:
    """Return one problem per Azure, MSAL or Fabric adapter package in a default-environment lock."""
    return sorted(
        f"{name}=={version} must only arrive with --extra fabric"
        for name, version in pins.items()
        if name.startswith(FORBIDDEN_PREFIXES)
    )


def _pyproject() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())


def _requirement_names(requirements: list[str]) -> list[str]:
    names = []
    for requirement in requirements:
        match = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", requirement)
        assert match, f"cannot read the requirement {requirement!r}"
        names.append(normalize(match.group(0)))
    return names


def _run_uv(*args: str) -> str:
    uv = shutil.which("uv")
    if uv is None:
        pytest.fail("uv is required for this check", pytrace=False)
    result = subprocess.run([uv, *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 0, (
        f"`uv {' '.join(args)}` exited {result.returncode}:\n{result.stderr}"
    )
    return result.stdout


@pytest.fixture(scope="module")
def default_export() -> dict[str, str]:
    return parse_export(_run_uv(*EXPORT_ARGS))


@pytest.fixture(scope="module")
def fabric_export() -> dict[str, str]:
    return parse_export(_run_uv(*EXPORT_ARGS, "--extra", "fabric"))


def _duckdb_pin() -> str:
    dependencies = _pyproject()["project"]["dependencies"]
    pins = [d for d in dependencies if _requirement_names([d])[0] == "duckdb"]
    assert len(pins) == 1, f"expected exactly one duckdb requirement, found {pins}"
    match = re.fullmatch(r"duckdb\s*==\s*(\d+\.\d+\.\d+)", pins[0])
    assert match, f"duckdb must be pinned with == to an exact version, found {pins[0]!r}"
    return match.group(1)


def test_duckdb_is_pinned_to_one_exact_version() -> None:
    assert _duckdb_pin()


def _fabricspark_pin() -> str:
    extra = _pyproject()["project"]["optional-dependencies"]["fabric"]
    found = [r for r in extra if _requirement_names([r])[0] == "dbt-fabricspark"]
    assert len(found) == 1, (
        f"the fabric extra must hold exactly one dbt-fabricspark requirement: {extra}"
    )
    match = re.fullmatch(r"dbt-fabricspark\s*==\s*(\d+\.\d+\.\d+)", found[0])
    assert match, f"dbt-fabricspark must be pinned with == to an exact version, found {found[0]!r}"
    return match.group(1)


def test_the_fabric_adapter_is_only_in_the_fabric_extra() -> None:
    project = _pyproject()
    assert "dbt-fabricspark" not in _requirement_names(project["project"]["dependencies"])
    for group, requirements in project.get("dependency-groups", {}).items():
        names = _requirement_names([r for r in requirements if isinstance(r, str)])
        assert "dbt-fabricspark" not in names, f"dbt-fabricspark must not be in the {group} group"
    extras = project["project"]["optional-dependencies"]
    assert set(extras) == {"fabric"}
    assert "dbt-fabricspark" in _requirement_names(extras["fabric"])


def test_the_python_version_file_selects_3_12() -> None:
    assert (REPO_ROOT / ".python-version").read_text().strip().startswith("3.12")


def test_the_default_lock_has_the_pinned_duckdb(default_export: dict[str, str]) -> None:
    assert default_export.get("duckdb") == _duckdb_pin()


def test_the_default_lock_has_no_mssql_azure_or_fabric_package(
    default_export: dict[str, str],
) -> None:
    assert default_export, "the export listed no packages, so the check would pass vacuously"
    assert forbidden_packages(default_export) == []


def test_the_fabric_extra_adds_the_pinned_adapter(fabric_export: dict[str, str]) -> None:
    assert fabric_export.get("dbt-fabricspark") == _fabricspark_pin()
    assert "azure-identity" in fabric_export
    assert fabric_export.get("duckdb") == _duckdb_pin()


def _project_yaml(relative: str) -> dict:
    return yaml.safe_load((REPO_ROOT / relative).read_text())


def test_the_locked_dbt_core_satisfies_require_dbt_version(default_export: dict[str, str]) -> None:
    required = _project_yaml("dbt_project.yml")["require-dbt-version"]
    allowed = SpecifierSet(",".join(required))
    locked = default_export["dbt-core"]
    assert Version(locked) in allowed, f"locked dbt-core {locked} is outside {required}"


@pytest.mark.parametrize("relative", ["dbt_project.yml", "packages/starter/dbt_project.yml"])
def test_both_projects_require_the_native_job_dbt_line(relative: str) -> None:
    assert _project_yaml(relative)["require-dbt-version"] == DBT_LINE


def identity_column_declarations(texts: Mapping[str, str]) -> list[str]:
    """Return, sorted, one problem per line that declares an identity column."""
    problems = []
    for path, text in texts.items():
        for number, line in enumerate(text.splitlines(), start=1):
            if IDENTITY_RE.search(line):
                problems.append(
                    f"{path}:{number}: identity column declaration (a dbt 1.12-line feature, D-07)"
                )
    return sorted(problems)


def _tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    return result.stdout.splitlines()


def test_no_dbt_1_12_only_feature_is_used() -> None:
    tracked = _tracked_files()
    assert tracked, "git listed no files, so the check would pass vacuously"
    assert not [f for f in tracked if f.rsplit("/", 1)[-1] == "vars.yml"], "vars.yml is 1.12-only"
    yaml_files = [f for f in tracked if f.endswith((".yml", ".yaml"))]
    on_error = [
        f
        for f in yaml_files
        if re.search(r"^\s*on_error\s*:", (REPO_ROOT / f).read_text(), re.MULTILINE)
    ]
    assert on_error == [], f"on_error is a dbt 1.12 key: {on_error}"
    sources = [f for f in tracked if f.endswith((".sql", ".yml", ".yaml"))]
    texts = {f: (REPO_ROOT / f).read_text() for f in sources}
    assert len(texts) >= 20, "too few files read, so the identity check would pass vacuously"
    assert identity_column_declarations(texts) == []


IDENTITY_CASES = {
    "meta-identity-key": (
        "models/gold/_models.yml",
        (
            "models:\n  - name: t\n    columns:\n      - name: row_key\n"
            "        data_type: bigint\n        meta:\n          identity: auto\n"
        ),
    ),
    "generated-always": (
        "models/gold/example.sql",
        "create table t (id bigint generated always as identity)\n",
    ),
    "identity-property": (
        "macros/example.sql",
        "create table t (id bigint identity(1, 1) not null)\n",
    ),
}


@pytest.mark.parametrize("case", sorted(IDENTITY_CASES))
def test_an_identity_column_declaration_is_reported(case: str) -> None:
    path, text = IDENTITY_CASES[case]
    problems = identity_column_declarations({path: text})
    assert len(problems) == 1
    assert problems[0].startswith(f"{path}:")


def test_a_name_that_only_contains_identity_is_not_reported() -> None:
    text = "select row_identity, workload_identity from t\n"
    assert identity_column_declarations({"models/a.sql": text}) == []


def test_the_lock_file_matches_pyproject() -> None:
    _run_uv("lock", "--check")


def test_parse_export_reads_names_and_versions_and_skips_comments() -> None:
    text = (
        "# autogenerated\n"
        "duckdb==2.0.1\n"
        "    # via dbt-duckdb\n"
        "Azure_Core==1.41.0 ; sys_platform == 'linux'\n"
        "networkx==3.7 ; python_full_version >= '3.12'\n"
        "\n"
    )
    assert parse_export(text) == {"duckdb": "2.0.1", "azure-core": "1.41.0", "networkx": "3.7"}


@pytest.mark.parametrize(
    "package",
    ["azure-identity", "Azure_Core", "msal", "dbt-fabricspark"],
)
def test_a_forbidden_package_in_the_default_lock_is_reported(package: str) -> None:
    pins = parse_export(f"duckdb==2.0.1\n{package}==1.0.0\n")
    problems = forbidden_packages(pins)
    assert len(problems) == 1
    assert normalize(package) in problems[0]


def test_a_lock_with_only_allowed_packages_passes() -> None:
    assert forbidden_packages(parse_export("duckdb==2.0.1\ndbt-core==1.11.17\n")) == []

"""The compiled Fabric SQL of the project's own packages must be clean.

The compile is offline: dummy UUID values and `--no-populate-cache`, so no
connection is attempted. Third-party package output under `compiled/` is not asserted on,
because the project does not own that SQL.
"""

from __future__ import annotations

import copy
import re

import pytest
from support.dbt_cli import DUMMY_FABRIC_ENV, FabricCompile
from support.sql_scan import scan, scan_compiled_dir

PROJECT_PACKAGES = ("dbt_starter", "starter")

# The fabricspark body of `undeclared_columns` is a declared stub: a typed result with no rows.
# These markers are its shape. A change to the body (a regression, or the Phase 5 live lookup
# replacing it) fails the test until the test changes with it.
UNDECLARED_COLUMNS_STUB_MARKERS = (
    "select cast(null as string) as column_name",
    "as no_columns where 1 = 0",
)


def undeclared_columns_stub_problems(code: str) -> list[str]:
    """Return one problem per way the compiled fabricspark `undeclared_columns` SQL is not the stub."""
    flat = " ".join((code or "").split()).lower()
    if not flat:
        return ["the compiled undeclared_columns SQL is empty"]
    problems = []
    if "information_schema" in flat:
        problems.append("the compiled SQL reads information_schema, which Fabric does not have")
    for marker in UNDECLARED_COLUMNS_STUB_MARKERS:
        if marker not in flat:
            problems.append(f"the compiled SQL lacks the stub marker: {marker}")
    problems.extend(scan(code))
    return problems


def check_materializations(manifest: dict) -> list[str]:
    """Return one problem per project model that is ephemeral or incremental.

    Both stay banned. An ephemeral model has no relation, so it never appears as a table in its
    layer schema, which LAYR-01 requires of every model. An incremental model needs merge or
    delete+insert behaviour that dbt-duckdb and dbt-fabricspark implement differently.
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
    assert any("regexp_like(" in t for t in texts), "no compiled model uses the Spark parse guard"
    assert any("md5(" in t for t in texts), "no compiled model uses the Spark hash shim"
    assert not any("regexp_matches(" in t for t in texts), "a compiled model carries a DuckDB body"


def _undeclared_columns_nodes(manifest: dict) -> list[dict]:
    return [
        node
        for node in manifest.get("nodes", {}).values()
        if node.get("resource_type") == "test"
        and (node.get("test_metadata") or {}).get("name") == "undeclared_columns"
    ]


def test_undeclared_columns_compiles_for_spark_without_information_schema(
    fabric_compiled: FabricCompile,
) -> None:
    """The warn test compiles for fabricspark to the declared zero-row stub.

    The limit: on Fabric this test returns no rows by design, so it cannot report a new landed
    column there. The DuckDB gate still reports new columns. The live lookup is a
    Phase 5 check, `undeclared-columns-live` in ADR 0006 (threat T-04.1-06). Pinning the
    stub's shape means a changed body fails here until this test changes with it.
    """
    nodes = _undeclared_columns_nodes(fabric_compiled.manifest)
    assert nodes, "the Fabric manifest holds no undeclared_columns test, so nothing was checked"
    for node in nodes:
        problems = undeclared_columns_stub_problems(node.get("compiled_code") or "")
        assert problems == [], f"{node['unique_id']}: {problems}"


@pytest.mark.parametrize(
    ("body", "needle"),
    [
        (
            "select column_name from information_schema.columns where table_name = 't'",
            "information_schema",
        ),
        (
            "select column_name from (select cast(null as string) as column_name) as no_columns",
            "where 1 = 0",
        ),
    ],
    ids=["information_schema_query", "stub_without_its_zero_row_filter"],
)
def test_a_regressed_undeclared_columns_body_is_reported(body: str, needle: str) -> None:
    problems = undeclared_columns_stub_problems(body)
    assert any(needle in problem for problem in problems), problems


def test_the_declared_stub_is_accepted() -> None:
    stub = (
        "select column_name from (select cast(null as string) as column_name) "
        "as no_columns where 1 = 0"
    )
    assert undeclared_columns_stub_problems(stub) == []


def test_the_conversion_fixture_compiles_with_the_spark_guards(
    fabric_compiled: FabricCompile,
) -> None:
    found = list((fabric_compiled.compiled_dir / "starter").rglob("shim_conversion_fixture.sql"))
    assert len(found) == 1, f"expected one compiled conversion fixture, found {found}"
    text = found[0].read_text().lower()
    assert "regexp_like(" in text
    assert "'1_000'" in text
    assert "regexp_matches(" not in text


def test_cross_layer_references_name_the_layer_schema_and_no_workspace(
    fabric_compiled: FabricCompile,
) -> None:
    """D-04 under the D-05 fallback: a silver model reads bronze through the `bronze` schema.

    Only the layer schema is asserted, never the full relation shape: a live run adds the
    Lakehouse name as a third part, which ADR 0006 records. No workspace or Lakehouse id ever
    reaches the SQL.
    """
    nodes = fabric_compiled.manifest["nodes"]
    readers = [
        node
        for node in nodes.values()
        if node.get("resource_type") == "model"
        and node.get("package_name") == "dbt_starter"
        and node.get("original_file_path", "").replace("\\", "/").startswith("models/silver/")
        and any(
            nodes.get(parent, {}).get("resource_type") == "model"
            and nodes[parent]
            .get("original_file_path", "")
            .replace("\\", "/")
            .startswith("models/bronze/")
            for parent in node["depends_on"]["nodes"]
        )
    ]
    checked = 0
    for node in readers:
        path = fabric_compiled.compiled_dir / "dbt_starter" / node["original_file_path"]
        text = path.read_text().lower()
        if not re.search(r"(?:from|join)\s+`?\w+`?\.", text):
            continue  # a bronze parent for ordering only: this model reads no relation
        checked += 1
        assert re.search(r"(?:from|join)\s+`?bronze`?\.", text), (
            f"{path.name} does not read bronze through the bronze schema qualifier"
        )
    assert checked >= 2, (
        "fewer than two silver models read a qualified relation, so the check is vacuous"
    )
    for path in (fabric_compiled.compiled_dir / "dbt_starter").rglob("*.sql"):
        text = path.read_text().lower()
        for key in ("FABRIC_WORKSPACE_ID", "FABRIC_LAKEHOUSE_ID"):
            assert DUMMY_FABRIC_ENV[key] not in text, f"{path.name} holds the value of {key}"


# WR-04: the Spark session zone must be UTC in every run mode, the native dbt job included,
# because the job does not read profiles.yml. The guard is `starter.ensure_utc_session()`, called
# on run start and before each bronze model. These checks read the manifest of the offline
# compile, so a hook that was dropped from dbt_project.yml fails here.
GUARD = "ensure_utc_session"
GUARD_MACRO = "macro.starter.fabricspark__ensure_utc_session"
DUCKDB_GUARD_MACRO = "macro.starter.duckdb__ensure_utc_session"


def _is_bronze_model(node: dict) -> bool:
    return (
        node.get("resource_type") == "model"
        and node.get("package_name") == "dbt_starter"
        and node.get("original_file_path", "").replace("\\", "/").startswith("models/bronze/")
    )


def utc_guard_problems(manifest: dict) -> list[str]:
    """Return one problem per place the UTC session guard is missing or weaker than required."""
    problems = []
    nodes = manifest.get("nodes", {})
    run_start = [
        node
        for uid, node in nodes.items()
        if node.get("resource_type") == "operation" and uid.endswith("on-run-start-0")
    ]
    if not any(GUARD in (node.get("raw_code") or "") for node in run_start):
        problems.append(f"on-run-start does not call {GUARD}")
    bronze = [(uid, node) for uid, node in nodes.items() if _is_bronze_model(node)]
    if len(bronze) < 2:
        problems.append("fewer than two bronze models were found, so the pre-hook check is vacuous")
    for uid, node in bronze:
        hooks = (node.get("config") or {}).get("pre-hook") or []
        if not any(GUARD in (hook.get("sql") or "") for hook in hooks):
            problems.append(f"{uid} has no {GUARD} pre-hook")
    macros = manifest.get("macros", {})
    spark = (macros.get(GUARD_MACRO) or {}).get("macro_sql", "")
    flat = " ".join(spark.lower().split())
    for needle in (
        "if execute",
        "set spark.sql.session.timezone = utc",
        "current_timezone()",
        "raise_compiler_error",
        "ensure_utc_session:",
    ):
        if needle not in flat:
            problems.append(f"the fabricspark guard body lacks `{needle}`")
    if "not utc" not in flat:
        problems.append("the fabricspark guard message does not say the zone is not UTC")
    duckdb_body = (macros.get(DUCKDB_GUARD_MACRO) or {}).get("macro_sql", "")
    if not duckdb_body:
        problems.append("the duckdb guard body is missing")
    elif "run_query" in duckdb_body or "raise_compiler_error" in duckdb_body:
        problems.append("the duckdb guard body runs SQL or raises, so it is not a no-op")
    return problems


def test_the_utc_session_guard_is_wired_into_every_fabric_run_mode(
    fabric_compiled: FabricCompile,
) -> None:
    assert utc_guard_problems(fabric_compiled.manifest) == []


def test_a_dropped_or_weakened_utc_guard_is_reported(fabric_compiled: FabricCompile) -> None:
    manifest = copy.deepcopy(fabric_compiled.manifest)
    for uid, node in manifest["nodes"].items():
        if uid.endswith("on-run-start-0"):
            node["raw_code"] = ""
        if _is_bronze_model(node):
            node["config"]["pre-hook"] = []
    manifest["macros"][GUARD_MACRO]["macro_sql"] = "{% macro x() %}{% endmacro %}"
    problems = utc_guard_problems(manifest)
    assert any("on-run-start" in p for p in problems), problems
    assert any("pre-hook" in p for p in problems), problems
    assert any("guard body lacks" in p for p in problems), problems


# WR-05: Spark reads `''` as two adjacent literals and drops the quote, so a doubled quote between
# two letters inside a compiled Spark literal is the DuckDB escape leaking into Spark SQL.
DOUBLED_QUOTE_IN_TEXT = re.compile(r"[A-Za-z]''[A-Za-z]")


def doubled_quote_problems(compiled: dict[str, str]) -> list[str]:
    """Return one problem per compiled file that holds a DuckDB-style doubled quote in text."""
    return [
        f"{name} holds a doubled quote between letters, which Spark reads as two literals"
        for name, text in compiled.items()
        if DOUBLED_QUOTE_IN_TEXT.search(text)
    ]


def escaped_literal_problems(fixture_text: str) -> list[str]:
    """Return one problem per Spark escape the compiled conversion fixture does not show."""
    problems = []
    if "\\'" not in fixture_text:
        problems.append("the fixture holds no backslash-escaped single quote")
    if "\\\\" not in fixture_text:
        problems.append("the fixture holds no doubled backslash")
    return problems


def test_the_spark_fixture_escapes_a_quote_and_a_backslash_with_a_backslash(
    fabric_compiled: FabricCompile,
) -> None:
    found = list((fabric_compiled.compiled_dir / "starter").rglob("shim_conversion_fixture.sql"))
    assert len(found) == 1, f"expected one compiled conversion fixture, found {found}"
    assert escaped_literal_problems(found[0].read_text()) == []


def test_no_compiled_spark_file_renders_text_with_a_doubled_quote(
    fabric_compiled: FabricCompile,
) -> None:
    files = list(fabric_compiled.compiled_dir.rglob("*.sql"))
    assert files, "the Fabric compile produced no SQL, so nothing was checked"
    compiled = {str(f.relative_to(fabric_compiled.compiled_dir)): f.read_text() for f in files}
    assert doubled_quote_problems(compiled) == []


def test_a_doubled_quote_or_a_missing_escape_is_reported() -> None:
    assert doubled_quote_problems({"m.sql": "select 'it''s'"}) != []
    assert doubled_quote_problems({"m.sql": "select 'it\\'s', '' as e, concat_ws('', a)"}) == []
    assert escaped_literal_problems("select 'it''s a \\ b'") != []
    assert escaped_literal_problems("select 'it\\'s a \\\\ b'") == []

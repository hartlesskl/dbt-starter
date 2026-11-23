"""Every test Fabric runs is scanned inside the wrapper dbt-fabric puts around it.

dbt-fabric sends each data test as `with test_main_sql as ( <body> ), dbt_internal_test as (...)
select ...`. A test body that opens its own CTE therefore becomes a nested WITH on the Warehouse,
which the portable subset bans. The scan of the compiled files cannot see it, because those files
hold the body unwrapped.

This module scans every compiled test body inside that wrapper and holds the kinds that nest to
one exact list, `WRAPPED_CTE_TESTS`, named in ADR 0005 as the Phase 5 check `wrapped-cte-tests`.
A new kind that nests fails here, and so does a listed kind that has stopped nesting or is no
longer run, so the list changes only by an edit to this file and to the ADR.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from pathlib import Path

import pytest
from support.dbt_cli import FabricCompile
from support.sql_scan import scan

NESTED_WITH = "nested WITH"


def fabric_test_wrapped(body: str) -> str:
    """Wrap a compiled test body the way the installed adapter wraps it.

    This mirrors `fabric__get_test_sql` in dbt-fabric's `materializations/tests/helpers.sql`.
    Only the structure of the wrapper matters to the scan, so the select list is simplified.
    """
    return (
        "with test_main_sql as (\n"
        + body
        + "\n),\ndbt_internal_test as (\n    select * from test_main_sql\n)\n"
        "select count(*) as failures from dbt_internal_test"
    )


def kind_of(node: dict) -> str:
    """The kind of a test node: `<namespace or dbt>.<name>` for a generic test, else singular."""
    metadata = node.get("test_metadata")
    if metadata:
        return f"{metadata.get('namespace') or 'dbt'}.{metadata['name']}"
    return f"singular.{node['name']}"


# Every kind here nests a WITH inside dbt-fabric's test wrapper. Whether a Warehouse accepts that
# is the Phase 5 check `wrapped-cte-tests` in ADR 0005. A kind leaves this list when its test is
# rewritten as one SELECT over derived tables (as `starter.quarantine_rate_levels` is) or is no
# longer run, and joins it only together with the ADR.
WRAPPED_CTE_TESTS: dict[str, str] = {
    "dbt.accepted_values": "dbt-core built-in generic test",
    "dbt.relationships": "dbt-core built-in generic test",
    "dbt_utils.accepted_range": "dbt_utils package",
    "dbt_utils.equal_rowcount": "dbt_utils package",
    "dbt_utils.unique_combination_of_columns": "dbt_utils package",
    "starter.date_span_complete": "packages/starter/macros/tests/calendar.sql",
    "starter.rows_equal_by_batch": "packages/starter/macros/tests/invariants.sql",
    "starter.rows_split_by_batch": "packages/starter/macros/tests/invariants.sql",
    "starter.totals_reconcile": "packages/starter/macros/tests/reconciliation.sql",
    "singular.shim_conversion_fixture": (
        "packages/starter/tests/conversion/shim_conversion_fixture.sql"
    ),
}

# Kinds that must run on Fabric and must not nest, so a regression to a CTE body is caught.
CLEAN_KINDS = (
    "starter.no_shared_keys",
    "starter.undeclared_columns",
    "starter.quarantine_rate_levels",
    "dbt.not_null",
    "dbt.unique",
)


def check_wrapped_ctes(manifest: dict, allowed: Mapping[str, str]) -> list[str]:
    """Return a sorted problem for every mismatch between nesting kinds and `allowed`.

    Every test node of every package is wrapped and scanned. A node with no compiled SQL is a
    problem, so nothing passes unscanned. Only a nested WITH counts here: the other banned
    constructs are the job of the compiled-file scan in `test_fabric_compile.py`.
    """
    problems: list[str] = []
    present: dict[str, str] = {}
    nesting: dict[str, str] = {}
    for uid, node in manifest.get("nodes", {}).items():
        if node.get("resource_type") != "test":
            continue
        code = node.get("compiled_code")
        if not code or not code.strip():
            problems.append(f"{uid}: the test has no compiled SQL, so it cannot be scanned")
            continue
        kind = kind_of(node)
        present.setdefault(kind, uid)
        if any(NESTED_WITH in problem for problem in scan(fabric_test_wrapped(code))):
            nesting.setdefault(kind, uid)
    for kind, uid in sorted(nesting.items()):
        if kind not in allowed:
            problems.append(
                f"{kind} nests a WITH inside dbt-fabric's test wrapper but is not in "
                f"WRAPPED_CTE_TESTS (for example {uid}); rewrite it as derived tables, or list "
                f"it there and in ADR 0005"
            )
    for kind in sorted(allowed):
        if kind in nesting:
            continue
        reason = "no longer nests a WITH" if kind in present else "is no longer run"
        problems.append(
            f"{kind} is in WRAPPED_CTE_TESTS but {reason}; remove it from the list and from ADR 0005"
        )
    return sorted(problems)


def test_the_wrapper_turns_a_cte_body_into_a_nested_with() -> None:
    problems = scan(fabric_test_wrapped("with a as (select 1 as x) select x from a"))
    assert any(NESTED_WITH in problem for problem in problems)


def test_the_wrapper_leaves_a_derived_table_body_clean() -> None:
    assert scan(fabric_test_wrapped("select x from (select 1 as x) as s")) == []


def test_the_installed_adapter_still_wraps_a_test_body_in_a_cte() -> None:
    fabric = pytest.importorskip("dbt.include.fabric")
    helpers = Path(fabric.__file__).parent / "macros" / "materializations" / "tests" / "helpers.sql"
    text = helpers.read_text()
    marker = "macro fabric__get_test_sql"
    assert marker in text, "the adapter no longer defines fabric__get_test_sql"
    after_tag = text.split(marker, 1)[1].split("%}", 1)[1]
    assert " ".join(after_tag.split()).startswith("with test_main_sql as ("), (
        "the adapter changed its test wrapper, so fabric_test_wrapped must follow it"
    )


def test_the_real_fabric_manifest_nests_exactly_the_listed_kinds(
    fabric_compiled: FabricCompile,
) -> None:
    problems = check_wrapped_ctes(fabric_compiled.manifest, WRAPPED_CTE_TESTS)
    assert problems == [], "\n".join(problems)


@pytest.mark.parametrize("kind", CLEAN_KINDS)
def test_a_clean_kind_runs_on_fabric_and_does_not_nest(
    fabric_compiled: FabricCompile, kind: str
) -> None:
    nodes = [
        node
        for node in fabric_compiled.manifest["nodes"].values()
        if node.get("resource_type") == "test" and kind_of(node) == kind
    ]
    assert nodes, f"{kind} is not in the Fabric manifest, so this check would pass on nothing"
    for node in nodes:
        problems = scan(fabric_test_wrapped(node["compiled_code"]))
        assert not any(NESTED_WITH in problem for problem in problems), node["unique_id"]


def _node(name: str, kind: str, body: str | None) -> dict:
    """A test node reduced to the fields the check reads."""
    namespace, test_name = kind.split(".", 1)
    node: dict = {
        "resource_type": "test",
        "name": name,
        "unique_id": f"test.dbt_starter.{name}",
        "test_metadata": {"name": test_name, "namespace": namespace, "kwargs": {}},
    }
    if body is not None:
        node["compiled_code"] = body
    return node


CTE_BODY = "with a as (select 1 as x) select x from a"
DERIVED_BODY = "select x from (select 1 as x) as s"
ALLOWED = {"starter.listed": "a package"}


def _manifest(*nodes: dict) -> dict:
    return {"nodes": {node["unique_id"]: node for node in nodes}}


def _valid() -> dict:
    return _manifest(
        _node("listed_one", "starter.listed", CTE_BODY),
        _node("clean_one", "starter.clean", DERIVED_BODY),
    )


def test_a_manifest_whose_nesting_kinds_are_exactly_the_listed_ones_passes() -> None:
    assert check_wrapped_ctes(_valid(), ALLOWED) == []


def _add_unlisted(manifest: dict) -> None:
    node = _node("unlisted_one", "starter.unlisted", CTE_BODY)
    manifest["nodes"][node["unique_id"]] = node


def _rewrite_listed_as_derived(manifest: dict) -> None:
    manifest["nodes"]["test.dbt_starter.listed_one"]["compiled_code"] = DERIVED_BODY


def _remove_listed(manifest: dict) -> None:
    del manifest["nodes"]["test.dbt_starter.listed_one"]


def _add_node_without_sql(manifest: dict) -> None:
    node = _node("no_sql", "starter.clean", None)
    manifest["nodes"][node["unique_id"]] = node


@pytest.mark.parametrize(
    ("doctor", "expected"),
    [
        (_add_unlisted, "starter.unlisted"),
        (_rewrite_listed_as_derived, "starter.listed is in WRAPPED_CTE_TESTS but no longer nests"),
        (_remove_listed, "starter.listed is in WRAPPED_CTE_TESTS but is no longer run"),
        (_add_node_without_sql, "test.dbt_starter.no_sql"),
    ],
    ids=["unlisted-kind", "listed-no-longer-nests", "listed-no-longer-run", "no-compiled-sql"],
)
def test_a_doctored_manifest_is_reported_once_by_name(doctor, expected: str) -> None:
    manifest = copy.deepcopy(_valid())
    doctor(manifest)
    problems = check_wrapped_ctes(manifest, ALLOWED)
    assert len(problems) == 1, problems
    assert expected in problems[0]

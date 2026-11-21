"""Portability bans: no dbt cross-database macros and no target branching outside `starter`.

dbt's own `safe_cast`, `concat`, `hash`, `type_string` and `date_trunc` compile to different
SQL on DuckDB and on Fabric, and `safe_cast` is a plain cast that raises on DuckDB. Models and
root-project macros call the `starter` shims instead (PORT-01), and never branch on the target.
Calls inside the `starter` package itself are the one place the engine difference is allowed.

`depends_on.macros` lists directly called macros only, so the check walks each model and each
root-project macro breadth-first through root-project macros. It stops at any other package:
calls inside `starter`, `dbt` and `dbt_utils` internals are not the project's own.
"""

from __future__ import annotations

import re
from collections import deque
from typing import Any

import pytest

PACKAGE = "dbt_starter"

BANNED_DBT_MACROS = frozenset(
    {
        "macro.dbt.safe_cast",
        "macro.dbt.concat",
        "macro.dbt.hash",
        "macro.dbt.type_string",
        "macro.dbt.date_trunc",
    }
)
TARGET_BRANCH_RE = re.compile(r"\btarget\.(type|name)\b|\badapter\.type\s*\(")
JINJA_COMMENT_RE = re.compile(r"\{#.*?#\}", re.DOTALL)


def _banned_paths(start: str, calls: list[str], macros: dict, package: str) -> list[str]:
    """Walk breadth-first from `calls`, descending only into macros of `package`."""
    found = []
    seen = set(calls)
    queue = deque((call, [start, call]) for call in calls)
    while queue:
        macro_id, path = queue.popleft()
        if macro_id in BANNED_DBT_MACROS:
            found.append(" -> ".join(path))
            continue
        macro = macros.get(macro_id)
        if macro is None or macro.get("package_name") != package:
            continue
        for nxt in macro["depends_on"]["macros"]:
            if nxt not in seen:
                seen.add(nxt)
                queue.append((nxt, [*path, nxt]))
    return found


def check_banned_macros(manifest: dict, package: str = "dbt_starter") -> list[str]:
    """Return every path from a model or root macro to a banned dbt macro."""
    macros = manifest["macros"]
    starts = [
        (uid, node["depends_on"]["macros"])
        for uid, node in manifest["nodes"].items()
        if node["resource_type"] == "model" and node.get("package_name") == package
    ]
    starts += [
        (uid, macro["depends_on"]["macros"])
        for uid, macro in macros.items()
        if macro.get("package_name") == package
    ]
    problems = []
    for uid, calls in starts:
        problems += [
            f"{path}: call a starter shim instead of a dbt cross-database macro"
            for path in _banned_paths(uid, calls, macros, package)
        ]
    return sorted(problems)


def check_target_branching(manifest: dict, package: str = "dbt_starter") -> list[str]:
    """Return every model or root macro that branches on `target.type`, `target.name` or `adapter.type()`.

    Jinja comments are removed first, so a comment that warns against the pattern is not a hit.
    """
    texts = [
        (uid, node["raw_code"])
        for uid, node in manifest["nodes"].items()
        if node["resource_type"] == "model" and node.get("package_name") == package
    ]
    texts += [
        (uid, macro["macro_sql"])
        for uid, macro in manifest["macros"].items()
        if macro.get("package_name") == package
    ]
    problems = []
    for uid, text in texts:
        match = TARGET_BRANCH_RE.search(JINJA_COMMENT_RE.sub("", text))
        if match:
            problems.append(
                f"{uid}: branches on {match.group(0)!r}; put the difference in a starter shim"
            )
    return sorted(problems)


def _macro(package: str, name: str, calls: list[str], sql: str = "") -> tuple[str, dict[str, Any]]:
    uid = f"macro.{package}.{name}"
    return uid, {"package_name": package, "macro_sql": sql, "depends_on": {"macros": calls}}


def _model(name: str, calls: list[str], raw_code: str = "", package: str = PACKAGE):
    uid = f"model.{package}.{name}"
    node = {
        "resource_type": "model",
        "package_name": package,
        "raw_code": raw_code,
        "depends_on": {"macros": calls},
    }
    return uid, node


def _manifest(models: list, macros: list) -> dict:
    return {"nodes": dict(models), "macros": dict(macros)}


def test_the_banned_set_is_the_five_dbt_macros() -> None:
    assert BANNED_DBT_MACROS == {
        "macro.dbt.safe_cast",
        "macro.dbt.concat",
        "macro.dbt.hash",
        "macro.dbt.type_string",
        "macro.dbt.date_trunc",
    }


def test_the_real_manifest_calls_no_banned_macro(duckdb_manifest: dict) -> None:
    assert check_banned_macros(duckdb_manifest) == []


def test_the_real_manifest_does_not_branch_on_the_target(duckdb_manifest: dict) -> None:
    assert check_target_branching(duckdb_manifest) == []


def test_the_real_manifest_has_macros_and_models_to_walk(duckdb_manifest: dict) -> None:
    root_macros = [m for m in duckdb_manifest["macros"].values() if m["package_name"] == PACKAGE]
    models = [
        n
        for n in duckdb_manifest["nodes"].values()
        if n["resource_type"] == "model" and n["package_name"] == PACKAGE
    ]
    assert root_macros
    assert models


@pytest.mark.parametrize("banned", sorted(BANNED_DBT_MACROS))
def test_a_model_calling_a_banned_macro_directly_is_flagged(banned: str) -> None:
    manifest = _manifest([_model("silver__a", [banned])], [])
    problems = check_banned_macros(manifest)
    assert len(problems) == 1
    assert "model.dbt_starter.silver__a" in problems[0]
    assert banned in problems[0]


def test_a_model_reaching_a_banned_macro_through_a_root_macro_is_flagged() -> None:
    manifest = _manifest(
        [_model("silver__a", ["macro.dbt_starter.helper"])],
        [
            _macro(PACKAGE, "helper", ["macro.dbt_starter.inner"]),
            _macro(PACKAGE, "inner", ["macro.dbt.safe_cast"]),
        ],
    )
    problems = check_banned_macros(manifest)
    model_problems = [p for p in problems if p.startswith("model.dbt_starter.silver__a")]
    assert len(model_problems) == 1
    path = model_problems[0]
    assert "macro.dbt_starter.helper" in path
    assert "macro.dbt_starter.inner" in path
    assert "macro.dbt.safe_cast" in path


def test_a_root_macro_calling_a_banned_macro_is_flagged_on_its_own() -> None:
    manifest = _manifest([], [_macro(PACKAGE, "helper", ["macro.dbt.hash"])])
    problems = check_banned_macros(manifest)
    assert len(problems) == 1
    assert problems[0].startswith("macro.dbt_starter.helper")


def test_the_same_call_inside_a_starter_macro_is_allowed() -> None:
    manifest = _manifest(
        [_model("silver__a", ["macro.starter.parse_int"])],
        [_macro("starter", "parse_int", ["macro.dbt.safe_cast", "macro.dbt.concat"])],
    )
    assert check_banned_macros(manifest) == []


def test_the_walk_does_not_descend_into_other_packages() -> None:
    manifest = _manifest(
        [_model("silver__a", ["macro.dbt_utils.helper"])],
        [_macro("dbt_utils", "helper", ["macro.dbt.safe_cast"])],
    )
    assert check_banned_macros(manifest) == []


def test_a_call_cycle_between_root_macros_terminates() -> None:
    manifest = _manifest(
        [_model("silver__a", ["macro.dbt_starter.one"])],
        [
            _macro(PACKAGE, "one", ["macro.dbt_starter.two"]),
            _macro(PACKAGE, "two", ["macro.dbt_starter.one", "macro.dbt.concat"]),
        ],
    )
    problems = check_banned_macros(manifest)
    assert any(p.startswith("model.dbt_starter.silver__a") for p in problems)


def test_models_of_other_packages_are_not_checked_for_banned_macros() -> None:
    manifest = _manifest([_model("x", ["macro.dbt.safe_cast"], package="dbt_utils")], [])
    assert check_banned_macros(manifest) == []


@pytest.mark.parametrize(
    "snippet",
    [
        "{% if target.type == 'fabric' %}x{% endif %}",
        "{% if target.name == 'prod' %}x{% endif %}",
        "{% if adapter.type() == 'duckdb' %}x{% endif %}",
        "{% if adapter.type ( ) == 'duckdb' %}x{% endif %}",
    ],
)
def test_branching_on_the_target_in_a_model_is_flagged(snippet: str) -> None:
    manifest = _manifest([_model("silver__a", [], raw_code=snippet)], [])
    problems = check_target_branching(manifest)
    assert len(problems) == 1
    assert "model.dbt_starter.silver__a" in problems[0]


def test_branching_on_the_target_in_a_root_macro_is_flagged() -> None:
    manifest = _manifest(
        [],
        [
            _macro(PACKAGE, "one", [], sql="{% if target.name == 'x' %}1{% endif %}"),
            _macro(PACKAGE, "two", [], sql="{% if adapter.type() == 'x' %}1{% endif %}"),
        ],
    )
    problems = check_target_branching(manifest)
    assert len(problems) == 2
    assert problems == sorted(problems)


def test_reading_the_target_schema_is_not_branching() -> None:
    manifest = _manifest(
        [_model("silver__a", [], raw_code="select '{{ target.schema }}', '{{ target.database }}'")],
        [_macro(PACKAGE, "one", [], sql="{{ target.schema }} {{ target.typeless }}")],
    )
    assert check_target_branching(manifest) == []


def test_a_jinja_comment_that_names_the_target_is_not_branching() -> None:
    comment = "{#- Never branch on target.type here; call a starter shim. -#}\nselect 1"
    manifest = _manifest([_model("silver__a", [], raw_code=comment)], [])
    assert check_target_branching(manifest) == []


def test_target_branching_inside_a_starter_macro_is_not_scanned() -> None:
    manifest = _manifest([], [_macro("starter", "shim", [], sql="{% if target.type == 'x' %}")])
    assert check_target_branching(manifest) == []

"""The quarantine-levels convention (QUAR-08), in one place so an adopter can read it.

Every entity registered in `dq_entities()` carries exactly one `starter.quarantine_rate_levels`
test, written in the entity's own YAML beside its silver model:

- the test is attached to the entity's silver model, and its second parent (`all_rows_model`) is
  the entity's bronze model, because dbt skips a downstream model only when it descends from
  every parent of the test, and gold never descends from the quarantine model;
- `warn_above` and `error_above` are numbers, fractions of rows, with
  `0 < warn_above < error_above < 1`, and no finer than 6 decimal places, because the test
  compares rates of type `decimal(18, 6)`. A level of 0 would always fire and a level of 1 would
  never fire, so both are rejected.

The registered entities are read from the manifest: the bronze models that
`silver__dq_quarantine_summary` reads, which is the model that reads the `dq_entities()` list.
All checks are pure functions over a manifest dict. Each has a small synthetic manifest that is
valid and one doctored copy per way it can go wrong, so the check is proven to fail.
"""

from __future__ import annotations

import copy
import json

import pytest
from conventions.test_handle import tracked_files
from support import dbt_cli
from support.dbt_cli import FabricCompile
from support.sql_scan import scan

from starter_cli import gate

PACKAGE = "dbt_starter"
LEVELS_TEST = "quarantine_rate_levels"
SUMMARY_MODEL = f"model.{PACKAGE}.silver__dq_quarantine_summary"
RESOLUTION_PLACES = 6


def registered_bronze(manifest: dict) -> set[str]:
    """The model ids the quarantine summary reads, which are the registered entities' bronze models.

    A missing summary node raises KeyError, so the check cannot pass on an empty registry.
    """
    summary = manifest["nodes"][SUMMARY_MODEL]
    return {uid for uid in summary["depends_on"]["nodes"] if uid.startswith("model.")}


def levels_tests(manifest: dict) -> list[dict]:
    """The levels test nodes of the project package."""
    return [
        node
        for node in manifest["nodes"].values()
        if node.get("resource_type") == "test"
        and node.get("package_name") == PACKAGE
        and (node.get("test_metadata") or {}).get("name") == LEVELS_TEST
    ]


def _level_problem(name: str, key: str, value: object) -> str | None:
    """Why one level cannot work, or None when it can."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return f"{name}: {key} must be a number, got {value!r}"
    if not 0 < value < 1:
        return f"{name}: {key} must be above 0 and below 1, got {value}"
    if round(value, RESOLUTION_PLACES) != value:
        return f"{name}: {key} has more than {RESOLUTION_PLACES} decimal places, got {value}"
    return None


def _test_problems(test: dict, manifest: dict, bronze: set[str]) -> list[str]:
    name = test["name"]
    problems: list[str] = []
    attached = test.get("attached_node")
    parents = [uid for uid in test["depends_on"]["nodes"] if uid.startswith("model.")]
    others = [uid for uid in parents if uid != attached]
    second = others[0] if len(others) == 1 else None
    if second is None or second not in bronze:
        problems.append(
            f"{name}: the second parent must be one registered bronze model, got {others}"
        )
    node = manifest["nodes"].get(attached or "")
    if node is None or node.get("resource_type") != "model" or node["fqn"][1] != "silver":
        problems.append(f"{name}: the test must be attached to a silver model, got {attached}")
    elif second in bronze and second not in node["depends_on"]["nodes"]:
        problems.append(f"{name}: the silver model {attached} does not read {second}")
    kwargs = (test.get("test_metadata") or {}).get("kwargs", {})
    warn, error = kwargs.get("warn_above"), kwargs.get("error_above")
    level_problems = [
        found
        for key, value in (("warn_above", warn), ("error_above", error))
        if (found := _level_problem(name, key, value))
    ]
    problems.extend(level_problems)
    if not level_problems and not warn < error:
        problems.append(f"{name}: warn_above ({warn}) must be strictly below error_above ({error})")
    return problems


def check_levels(manifest: dict) -> list[str]:
    """Return a sorted problem for every registered entity or levels test that is wrong."""
    bronze = registered_bronze(manifest)
    tests = levels_tests(manifest)
    problems: list[str] = []
    for uid in sorted(bronze):
        count = sum(1 for test in tests if uid in test["depends_on"]["nodes"])
        if count != 1:
            problems.append(
                f"{uid}: the registered entity needs exactly one {LEVELS_TEST} test, found {count}"
            )
    for test in tests:
        problems.extend(_test_problems(test, manifest, bronze))
    return sorted(problems)


def test_the_real_manifest_gives_every_registered_entity_sane_levels(
    duckdb_manifest: dict,
) -> None:
    assert check_levels(duckdb_manifest) == []


def test_the_real_manifest_has_one_levels_test_per_registered_entity(
    duckdb_manifest: dict,
) -> None:
    bronze = registered_bronze(duckdb_manifest)
    assert bronze, "no registered entity, so the check would pass on nothing"
    assert len(levels_tests(duckdb_manifest)) == len(bronze)


def _model(name: str, layer: str, reads: list[str]) -> tuple[str, dict]:
    uid = f"model.{PACKAGE}.{name}"
    return uid, {
        "resource_type": "model",
        "package_name": PACKAGE,
        "name": name,
        "fqn": [PACKAGE, layer, "group", name],
        "depends_on": {"nodes": reads},
    }


def _levels_test(name: str, silver: str, second: str, warn: object, error: object) -> dict:
    return {
        "resource_type": "test",
        "package_name": PACKAGE,
        "name": name,
        "attached_node": silver,
        "depends_on": {"nodes": [silver, second]},
        "test_metadata": {
            "name": LEVELS_TEST,
            "namespace": "starter",
            "kwargs": {"warn_above": warn, "error_above": error},
        },
    }


BRONZE = f"model.{PACKAGE}.bronze__things"
SILVER = f"model.{PACKAGE}.silver__things"
QUARANTINE = f"model.{PACKAGE}.silver__things_quarantine"
GOLD = f"model.{PACKAGE}.gold__fct"
OTHER_BRONZE = f"model.{PACKAGE}.bronze__others"
OTHER_SILVER = f"model.{PACKAGE}.silver__others"
TEST_ID = f"test.{PACKAGE}.things_levels"


def _valid_manifest() -> dict:
    """One entity: bronze, silver and quarantine models, gold, the summary and one levels test."""
    nodes: dict[str, dict] = {}
    for uid, node in (
        _model("bronze__things", "bronze", []),
        _model("silver__things", "silver", [BRONZE]),
        _model("silver__things_quarantine", "silver", [BRONZE]),
        _model("gold__fct", "gold", [SILVER]),
        _model("silver__dq_quarantine_summary", "silver", [BRONZE]),
        _model("bronze__others", "bronze", []),
        _model("silver__others", "silver", [OTHER_BRONZE]),
    ):
        nodes[uid] = node
    nodes[TEST_ID] = _levels_test("things_levels", SILVER, BRONZE, 0.001, 0.01)
    return {"nodes": nodes}


def test_a_valid_synthetic_manifest_passes() -> None:
    assert check_levels(_valid_manifest()) == []


def _test_node(manifest: dict) -> dict:
    return manifest["nodes"][TEST_ID]


def _without_the_test(manifest: dict) -> None:
    del manifest["nodes"][TEST_ID]


def _with_a_second_test(manifest: dict) -> None:
    manifest["nodes"][f"{TEST_ID}_again"] = _levels_test(
        "things_again", SILVER, BRONZE, 0.001, 0.01
    )


def _with_a_quarantine_parent(manifest: dict) -> None:
    manifest["nodes"][f"{TEST_ID}_q"] = _levels_test(
        "things_quarantine_parent", SILVER, QUARANTINE, 0.001, 0.01
    )


def _attached_to_gold(manifest: dict) -> None:
    test = _test_node(manifest)
    test["attached_node"] = GOLD
    test["depends_on"]["nodes"] = [GOLD, BRONZE]


def _attached_to_a_silver_model_that_does_not_read_the_bronze_model(manifest: dict) -> None:
    test = _test_node(manifest)
    test["attached_node"] = OTHER_SILVER
    test["depends_on"]["nodes"] = [OTHER_SILVER, BRONZE]


def _reading_an_unregistered_bronze_model(manifest: dict) -> None:
    manifest["nodes"][f"{TEST_ID}_o"] = _levels_test(
        "others_levels", OTHER_SILVER, OTHER_BRONZE, 0.001, 0.01
    )


def _set(key: str, value: object):
    def doctor(manifest: dict) -> None:
        _test_node(manifest)["test_metadata"]["kwargs"][key] = value

    return doctor


def _remove(key: str):
    def doctor(manifest: dict) -> None:
        del _test_node(manifest)["test_metadata"]["kwargs"][key]

    return doctor


def _set_both(warn: float, error: float):
    def doctor(manifest: dict) -> None:
        kwargs = _test_node(manifest)["test_metadata"]["kwargs"]
        kwargs["warn_above"], kwargs["error_above"] = warn, error

    return doctor


DOCTORED = [
    pytest.param(_without_the_test, "bronze__things", id="no-levels-test"),
    pytest.param(_with_a_second_test, "bronze__things", id="two-levels-tests"),
    pytest.param(_with_a_quarantine_parent, "things_quarantine_parent", id="quarantine-parent"),
    pytest.param(_attached_to_gold, "things_levels", id="attached-to-gold"),
    pytest.param(
        _attached_to_a_silver_model_that_does_not_read_the_bronze_model,
        "things_levels",
        id="silver-does-not-read-bronze",
    ),
    pytest.param(_reading_an_unregistered_bronze_model, "others_levels", id="unregistered-bronze"),
    pytest.param(_remove("warn_above"), "warn_above", id="warn-missing"),
    pytest.param(_set("warn_above", True), "warn_above", id="warn-boolean"),
    pytest.param(_set("warn_above", "0.001"), "warn_above", id="warn-text"),
    pytest.param(_set("warn_above", 0), "warn_above", id="warn-zero"),
    pytest.param(_set("error_above", 1), "error_above", id="error-one"),
    pytest.param(_set_both(0.01, 0.01), "strictly below", id="warn-equals-error"),
    pytest.param(_set("warn_above", 0.0000005), "decimal places", id="warn-finer-than-resolution"),
]


@pytest.mark.parametrize(("doctor", "expected"), DOCTORED)
def test_a_doctored_manifest_is_reported_once_by_name(doctor, expected: str) -> None:
    manifest = copy.deepcopy(_valid_manifest())
    doctor(manifest)
    problems = check_levels(manifest)
    assert len(problems) == 1, problems
    assert expected in problems[0]


def test_each_fabric_levels_test_is_clean_and_holds_both_levels(
    fabric_compiled: FabricCompile, duckdb_manifest: dict
) -> None:
    nodes = levels_tests(fabric_compiled.manifest)
    assert len(nodes) >= len(levels_tests(duckdb_manifest)) > 0
    for node in nodes:
        code = node["compiled_code"]
        name = node["name"]
        assert code.strip(), f"{name}: no compiled SQL"
        assert scan(code) == [], name
        assert "nullif(" in code, f"{name}: the rate is not null-guarded"
        assert "decimal(18, 6)" in code, f"{name}: the numerator is not cast to decimal(18, 6)"
        kwargs = node["test_metadata"]["kwargs"]
        for key in ("warn_above", "error_above"):
            assert f"{kwargs[key]:.6f}" in code, f"{name}: {key} is not a 6-decimal literal"


# The var turns the levels off, so it may live only where synthetic data is built or where it is
# documented: the generic test that reads it, the gate that builds the fixture, the README, the
# tests and the docs. It must never reach the demo, the Makefile, a workflow or the project config,
# because there it would hide a real breach.
OVERRIDE_VAR = "quarantine_rate_override"
OVERRIDE_ALLOWED_FILES = frozenset(
    {"packages/starter/macros/tests/quarantine_rate.sql", "src/starter_cli/gate.py", "README.md"}
)
OVERRIDE_ALLOWED_PREFIXES = ("tests/", "docs/")
OVERRIDE_REQUIRED = (
    "packages/starter/macros/tests/quarantine_rate.sql",
    "src/starter_cli/gate.py",
)


def check_override_confined(files: dict[str, str]) -> list[str]:
    """Return, sorted, one problem per path outside the allowlist that names the override var."""
    return sorted(
        f"{path}: names {OVERRIDE_VAR}, which may live only in the generic test, the gate, "
        f"the README, tests/ and docs/"
        for path, text in files.items()
        if path not in OVERRIDE_ALLOWED_FILES
        and not path.startswith(OVERRIDE_ALLOWED_PREFIXES)
        and OVERRIDE_VAR in text
    )


def test_the_real_tracked_tree_confines_the_override_var() -> None:
    files = tracked_files()
    assert files, "git ls-files returned nothing, so the check would pass on nothing"
    assert check_override_confined(files) == []


@pytest.mark.parametrize("path", OVERRIDE_REQUIRED)
def test_the_override_var_is_present_where_it_is_defined_and_passed(path: str) -> None:
    files = tracked_files()
    assert path in files, f"{path} is not tracked"
    assert OVERRIDE_VAR in files[path], f"{path} no longer names {OVERRIDE_VAR}"


@pytest.mark.parametrize(
    "path",
    [
        "src/starter_cli/demo.py",
        "Makefile",
        "dbt_project.yml",
        "pyproject.toml",
        ".github/workflows/nightly.yml",
        "models/silver/x.sql",
    ],
)
def test_the_override_var_in_a_forbidden_place_is_reported_by_path(path: str) -> None:
    problems = check_override_confined({path: f"--vars '{{\"{OVERRIDE_VAR}\": 1}}'\n"})
    assert len(problems) == 1
    assert problems[0].startswith(f"{path}:")


@pytest.mark.parametrize(
    "path",
    [
        "tests/anything.py",
        "docs/decisions/x.md",
        "README.md",
        "packages/starter/macros/tests/quarantine_rate.sql",
        "src/starter_cli/gate.py",
    ],
)
def test_the_override_var_in_an_allowed_place_is_not_reported(path: str) -> None:
    assert check_override_confined({path: f"{OVERRIDE_VAR}\n"}) == []


def test_the_tests_and_the_gate_share_one_relaxed_levels_definition() -> None:
    assert dbt_cli.RELAXED_LEVELS_ARGS == list(gate.RELAXED_LEVELS_ARGS)
    args = dbt_cli.RELAXED_LEVELS_ARGS
    assert args[0] == "--vars"
    assert json.loads(args[1]) == {OVERRIDE_VAR: {"warn_above": 1, "error_above": 1}}

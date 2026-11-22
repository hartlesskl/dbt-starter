"""Gold contracts hold the same rules on both engines (GOLD-05), and silver and gold hold no float (GOLD-04).

A gold model is the one an analyst queries, so it carries an enforced contract:

- every column declares a `data_type` from a short portable list (`int`, `bigint`, `smallint`,
  `date`, `varchar(n)`, `decimal(p,s)`), because those strings mean the same on DuckDB and on
  Fabric T-SQL;
- one primary key, named `pk_<table>` (Fabric needs a constraint name), whose columns carry a
  `not_null` constraint, because Fabric rejects a nullable key column;
- no foreign key, check or unique constraint: DuckDB enforces a foreign key and that makes a
  rebuild order-sensitive, and Fabric does not support `check`.

Fabric records keys as `NOT ENFORCED`, so the data tests (`unique`, `not_null`,
`relationships`) stay the real enforcement on both engines. The contract documents the shape
and makes a type drift fail the build.

There is one deliberate exception. No string names a timestamp on both engines, and a macro
cannot run in YAML, so the two timestamp columns of `gold__fct_trips` pick their type with one
`target.type` conditional (decision D-21, recorded in docs/decisions/0004-gold-contracts.md).
`check_yaml_target_conditionals` allows exactly that text at exactly those two columns, so a
third use anywhere in tracked YAML fails the gate and names the file and the key. Like the
handle check, it reads `git ls-files`, so new YAML is checked after `git add`.

The Fabric checks render each model's column DDL and its key offline, with the adapter's own
renderers and no connection, so an adopter without a Fabric workspace still sees a Fabric
contract problem in the gate.
"""

from __future__ import annotations

import copy
import re
from typing import Any

import pytest
import yaml
from conventions.test_handle import tracked_files
from conventions.test_portability import JINJA_COMMENT_RE, TARGET_BRANCH_RE
from support.dbt_cli import require_fabric_adapter

PACKAGE = "dbt_starter"

# The portable type strings: the same text builds on DuckDB and compiles for Fabric.
PORTABLE_TYPE_RE = re.compile(
    r"^(int|bigint|smallint|date|varchar\(\d+\)|decimal\(\d+,\s?\d+\))$", re.IGNORECASE
)

# The two columns whose type depends on the target (D-21), and the only text allowed there.
D21_COLUMNS = {("gold__fct_trips", "pickup_datetime"), ("gold__fct_trips", "dropoff_datetime")}
D21_TYPES = {"timestamp", "datetime2(6)"}
ALLOWED_CONDITIONAL = "{{ 'timestamp' if target.type == 'duckdb' else 'datetime2(6)' }}"

FORBIDDEN_CONSTRAINTS = {"foreign_key", "check", "unique"}
FLOAT_TYPE_RE = re.compile(r"^(double|float|real)", re.IGNORECASE)
YAML_SUFFIXES = (".yml", ".yaml")
YAML_EXEMPT_PREFIXES = (".planning/", ".claude/")


def gold_models(manifest: dict) -> dict[str, dict]:
    """Return the project's gold models by unique id: the models whose layer folder is gold."""
    return {
        uid: node
        for uid, node in manifest["nodes"].items()
        if node["resource_type"] == "model"
        and node.get("package_name") == PACKAGE
        and len(node["fqn"]) > 2
        and node["fqn"][1] == "gold"
    }


def check_gold_contracts(manifest: dict) -> list[str]:
    """Return every way a gold model breaks the contract rules, sorted."""
    problems = []
    for uid, node in gold_models(manifest).items():
        name = node["name"]
        if not node["config"].get("contract", {}).get("enforced"):
            problems.append(f"{uid}: the contract is not enforced")
        for column_name, column in node["columns"].items():
            data_type = column.get("data_type")
            if not data_type:
                problems.append(f"{uid}: column {column_name} has no data_type")
            elif (name, column_name) in D21_COLUMNS:
                if data_type.lower() not in D21_TYPES:
                    problems.append(
                        f"{uid}: column {column_name} has type {data_type}, "
                        "expected timestamp or datetime2(6)"
                    )
            elif not PORTABLE_TYPE_RE.match(data_type):
                problems.append(
                    f"{uid}: column {column_name} has type {data_type}, which is not portable "
                    "(use int, bigint, smallint, date, varchar(n) or decimal(p,s))"
                )
            for constraint in column.get("constraints", []):
                if constraint["type"] in FORBIDDEN_CONSTRAINTS:
                    problems.append(
                        f"{uid}: column {column_name} has a {constraint['type']} constraint"
                    )
        keys = [c for c in node.get("constraints", []) if c["type"] == "primary_key"]
        if len(keys) != 1:
            problems.append(f"{uid}: expected one primary key, found {len(keys)}")
        for key in keys:
            expected = f"pk_{node['alias']}"
            if key.get("name") != expected:
                problems.append(
                    f"{uid}: the primary key is named {key.get('name')!r}, not {expected}"
                )
            for column_name in key.get("columns", []):
                column = node["columns"].get(column_name, {})
                if not any(c["type"] == "not_null" for c in column.get("constraints", [])):
                    problems.append(
                        f"{uid}: primary key column {column_name} has no not_null constraint"
                    )
        for constraint in node.get("constraints", []):
            if constraint["type"] in FORBIDDEN_CONSTRAINTS:
                problems.append(f"{uid}: has a model-level {constraint['type']} constraint")
    return sorted(problems)


def _walk(value: Any, path: tuple) -> Any:
    """Yield `(path, text)` for every string key and string value in a loaded YAML document."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str):
                yield (*path, f"key:{key}"), key
            yield from _walk(child, (*path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, (*path, index))


def _is_allowed_site(document: Any, path: tuple) -> bool:
    """True when `path` is `models[i].columns[j].data_type` of a D-21 column."""
    if len(path) != 5 or path[0] != "models" or path[2] != "columns" or path[4] != "data_type":
        return False
    try:
        model = document["models"][path[1]]
        column = model["columns"][path[3]]
    except (KeyError, IndexError, TypeError):
        return False
    return (model.get("name"), column.get("name")) in D21_COLUMNS


def check_yaml_target_conditionals(documents: dict[str, Any]) -> list[str]:
    """Return every branch on the target in YAML that is not one of the two D-21 columns."""
    problems, _ = _scan_yaml(documents)
    return problems


def _scan_yaml(documents: dict[str, Any]) -> tuple[list[str], int]:
    """Return the problems and the number of allowed conditionals found."""
    problems = []
    allowed = 0
    for file, document in documents.items():
        for path, text in _walk(document, ()):
            if not TARGET_BRANCH_RE.search(JINJA_COMMENT_RE.sub("", text)):
                continue
            if text == ALLOWED_CONDITIONAL and _is_allowed_site(document, path):
                allowed += 1
                continue
            where = ".".join(str(part) for part in path)
            problems.append(
                f"{file}: {where} branches on the target; only the data_type of the two "
                "fct_trips timestamp columns may (D-21)"
            )
    return sorted(problems), allowed


def check_fabric_ddl(fabric_manifest: dict) -> list[str]:
    """Return every gold model whose column DDL or primary key does not render for Fabric.

    Uses the adapter's own renderers, so it needs the `fabric` extra and no connection.
    """
    from dbt.adapters.fabric import FabricAdapter
    from dbt_common.contracts.constraints import ModelLevelConstraint

    problems = []
    for uid, node in gold_models(fabric_manifest).items():
        columns = FabricAdapter.render_raw_columns_constraints(node["columns"])
        if len(columns) != len(node["columns"]):
            problems.append(
                f"{uid}: rendered {len(columns)} column definitions for "
                f"{len(node['columns'])} columns"
            )
        by_name = dict(zip(node["columns"], columns, strict=False))
        keys = [c for c in node.get("constraints", []) if c["type"] == "primary_key"]
        if not keys:
            problems.append(f"{uid}: has no primary key to render")
        for key in keys:
            for column_name in key["columns"]:
                if "not null" not in by_name.get(column_name, "").lower():
                    problems.append(
                        f"{uid}: primary key column {column_name} does not render as not null"
                    )
            rendered = FabricAdapter.render_model_constraint(ModelLevelConstraint(**key)) or ""
            if "primary key nonclustered" not in rendered or "not enforced" not in rendered:
                problems.append(f"{uid}: the primary key renders as {rendered!r}")
    return sorted(problems)


def check_no_float_columns(catalog: dict) -> list[str]:
    """Return every DOUBLE, FLOAT or REAL column in a silver or gold relation (GOLD-04)."""
    problems = []
    for uid, node in {**catalog.get("nodes", {}), **catalog.get("sources", {})}.items():
        if node["metadata"]["schema"] not in {"silver", "gold"}:
            continue
        for column_name, column in node["columns"].items():
            if FLOAT_TYPE_RE.match(column["type"]):
                problems.append(f"{uid}: column {column_name} has the float type {column['type']}")
    return sorted(problems)


def _checked_relations(catalog: dict, schema: str) -> int:
    nodes = {**catalog.get("nodes", {}), **catalog.get("sources", {})}
    return sum(1 for node in nodes.values() if node["metadata"]["schema"] == schema)


# --- hand-built manifests for the doctored cases --------------------------------------------


def _column(data_type: str | None, *, not_null: bool = False) -> dict:
    column: dict[str, Any] = {"constraints": []}
    if data_type is not None:
        column["data_type"] = data_type
    if not_null:
        column["constraints"].append({"type": "not_null"})
    return column


def _good_model() -> dict:
    return {
        "resource_type": "model",
        "package_name": PACKAGE,
        "name": "gold__fct_x",
        "alias": "fct_x",
        "fqn": [PACKAGE, "gold", "gold__fct_x"],
        "config": {"contract": {"enforced": True}},
        "constraints": [{"type": "primary_key", "name": "pk_fct_x", "columns": ["x_key"]}],
        "columns": {
            "x_key": _column("int", not_null=True),
            "amount": _column("decimal(18, 6)"),
            "label": _column("varchar(20)"),
            "day": _column("date"),
        },
    }


def _manifest_of(node: dict) -> dict:
    return {"nodes": {f"model.{PACKAGE}.{node['name']}": node}}


def test_a_good_model_has_no_problem() -> None:
    assert check_gold_contracts(_manifest_of(_good_model())) == []


def test_a_model_outside_gold_is_not_checked() -> None:
    node = _good_model()
    node["fqn"] = [PACKAGE, "silver", "gold__fct_x"]
    node["config"]["contract"]["enforced"] = False
    assert check_gold_contracts(_manifest_of(node)) == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda n: n["config"]["contract"].update(enforced=False), "contract is not enforced"),
        (lambda n: n["columns"]["label"].pop("data_type"), "column label has no data_type"),
        (lambda n: n["columns"]["amount"].update(data_type="double"), "not portable"),
        (lambda n: n["columns"]["amount"].update(data_type="float"), "not portable"),
        (lambda n: n["columns"]["label"].update(data_type="nvarchar(20)"), "not portable"),
        (lambda n: n["columns"]["day"].update(data_type="timestamp"), "not portable"),
        (lambda n: n["constraints"][0].update(name=None), "is named None"),
        (lambda n: n["constraints"][0].update(name="pk_other"), "is named 'pk_other'"),
        (
            lambda n: n["columns"]["x_key"].update(constraints=[]),
            "primary key column x_key has no not_null",
        ),
        (lambda n: n.update(constraints=[]), "expected one primary key, found 0"),
        (
            lambda n: n["constraints"].append({"type": "foreign_key", "columns": ["x_key"]}),
            "model-level foreign_key",
        ),
        (
            lambda n: n["constraints"].append({"type": "check", "columns": []}),
            "model-level check",
        ),
        (
            lambda n: n["constraints"].append({"type": "unique", "columns": ["label"]}),
            "model-level unique",
        ),
        (
            lambda n: n["columns"]["label"]["constraints"].append({"type": "unique"}),
            "column label has a unique constraint",
        ),
        (
            lambda n: n["columns"]["label"]["constraints"].append({"type": "check"}),
            "column label has a check constraint",
        ),
    ],
)
def test_a_doctored_model_is_reported(change: Any, message: str) -> None:
    node = _good_model()
    change(node)
    problems = check_gold_contracts(_manifest_of(node))
    assert any(message in problem for problem in problems), problems
    assert all("gold__fct_x" in problem for problem in problems)


def test_a_timestamp_is_allowed_only_on_the_two_exception_columns() -> None:
    node = _good_model()
    node["name"] = "gold__fct_trips"
    node["alias"] = "fct_trips"
    node["constraints"][0]["name"] = "pk_fct_trips"
    node["columns"]["pickup_datetime"] = _column("timestamp")
    node["columns"]["dropoff_datetime"] = _column("datetime2(6)")
    assert check_gold_contracts(_manifest_of(node)) == []
    node["columns"]["dropoff_datetime"]["data_type"] = "datetime"
    assert any("dropoff_datetime" in p for p in check_gold_contracts(_manifest_of(node)))


def test_the_real_manifest_has_no_contract_problem(duckdb_manifest: dict) -> None:
    assert check_gold_contracts(duckdb_manifest) == []
    assert len(gold_models(duckdb_manifest)) >= 3


def test_a_doctored_copy_of_the_real_manifest_is_reported(duckdb_manifest: dict) -> None:
    doctored = copy.deepcopy(duckdb_manifest)
    uid = "model.dbt_starter.gold__dim_date"
    doctored["nodes"][uid]["columns"]["month_name"]["data_type"] = "double"
    problems = check_gold_contracts(doctored)
    assert len(problems) == 1
    assert "month_name" in problems[0]


# --- the D-21 conditional ---------------------------------------------------------------------


def _fct_document(pickup: str, dropoff: str = ALLOWED_CONDITIONAL, other: str = "date") -> dict:
    return {
        "models": [
            {
                "name": "gold__fct_trips",
                "columns": [
                    {"name": "pickup_datetime", "data_type": pickup},
                    {"name": "dropoff_datetime", "data_type": dropoff},
                    {"name": "day", "data_type": other},
                ],
            }
        ]
    }


def test_the_two_exception_columns_are_allowed() -> None:
    documents = {"models/gold/_models.yml": _fct_document(ALLOWED_CONDITIONAL)}
    assert _scan_yaml(documents) == ([], 2)


def test_a_conditional_on_a_third_column_is_reported() -> None:
    documents = {
        "models/gold/_models.yml": _fct_document(ALLOWED_CONDITIONAL, other=ALLOWED_CONDITIONAL)
    }
    problems = check_yaml_target_conditionals(documents)
    assert len(problems) == 1
    assert "models/gold/_models.yml" in problems[0]
    assert "models.0.columns.2.data_type" in problems[0]


def test_a_conditional_on_another_model_is_reported() -> None:
    document = _fct_document(ALLOWED_CONDITIONAL)
    document["models"][0]["name"] = "gold__fct_other"
    problems = check_yaml_target_conditionals({"models/gold/_models.yml": document})
    assert len(problems) == 2


def test_a_conditional_in_a_config_value_is_reported() -> None:
    document = {
        "models": [
            {"name": "m", "config": {"schema": "{{ 'a' if target.name == 'dev' else 'b' }}"}}
        ]
    }
    problems = check_yaml_target_conditionals({"dbt_project.yml": document})
    assert len(problems) == 1
    assert "dbt_project.yml" in problems[0]
    assert "config.schema" in problems[0]


def test_a_changed_conditional_text_and_adapter_type_are_reported() -> None:
    changed = "{{ 'timestamp' if target.type == 'fabric' else 'datetime2(6)' }}"
    documents = {
        "models/a.yml": _fct_document(changed),
        "models/b.yml": {"vars": {"x": "{{ adapter.type() }}"}},
    }
    problems = check_yaml_target_conditionals(documents)
    assert len(problems) == 2
    assert any("models/a.yml" in p for p in problems)
    assert any("models/b.yml" in p for p in problems)


def test_a_conditional_inside_a_jinja_comment_is_not_a_hit() -> None:
    document = {"description": "{# never write target.type here #} plain"}
    assert check_yaml_target_conditionals({"models/a.yml": document}) == []


def test_tracked_yaml_has_only_the_two_allowed_conditionals() -> None:
    documents = {}
    for path, text in tracked_files().items():
        if path.endswith(YAML_SUFFIXES) and not path.startswith(YAML_EXEMPT_PREFIXES):
            documents[path] = yaml.safe_load(text)
    problems, allowed = _scan_yaml(documents)
    assert problems == []
    assert allowed == 2


# --- the Fabric target, offline ---------------------------------------------------------------


def test_every_gold_contract_renders_for_fabric_offline(fabric_compiled: Any) -> None:
    require_fabric_adapter()
    assert check_fabric_ddl(fabric_compiled.manifest) == []
    assert len(gold_models(fabric_compiled.manifest)) >= 3


def test_the_fabric_manifest_has_no_contract_problem(fabric_compiled: Any) -> None:
    assert check_gold_contracts(fabric_compiled.manifest) == []


def test_the_exception_columns_take_the_type_of_each_target(
    duckdb_manifest: dict, fabric_compiled: Any
) -> None:
    for name, column in sorted(D21_COLUMNS):
        uid = f"model.{PACKAGE}.{name}"
        assert duckdb_manifest["nodes"][uid]["columns"][column]["data_type"] == "timestamp"
        assert fabric_compiled.manifest["nodes"][uid]["columns"][column]["data_type"] == (
            "datetime2(6)"
        )


def test_a_nullable_key_column_is_reported_for_fabric(fabric_compiled: Any) -> None:
    doctored = copy.deepcopy(fabric_compiled.manifest)
    uid = f"model.{PACKAGE}.gold__dim_date"
    doctored["nodes"][uid]["constraints"][0]["columns"] = ["calendar_year"]
    problems = check_fabric_ddl(doctored)
    assert any("calendar_year" in problem and "not null" in problem for problem in problems), (
        problems
    )


# --- no float in silver or gold ---------------------------------------------------------------


def _catalog(*columns: tuple[str, str, str]) -> dict:
    nodes: dict[str, Any] = {}
    for schema, name, data_type in columns:
        node = nodes.setdefault(
            f"model.{PACKAGE}.{schema}__t",
            {"metadata": {"schema": schema, "name": "t"}, "columns": {}},
        )
        node["columns"][name] = {"type": data_type}
    return {"nodes": nodes}


def test_no_float_column_in_a_clean_catalog() -> None:
    catalog = _catalog(("gold", "a", "DECIMAL(18,6)"), ("silver", "b", "INTEGER"))
    assert check_no_float_columns(catalog) == []


@pytest.mark.parametrize("data_type", ["DOUBLE", "FLOAT", "REAL", "double precision", "FLOAT4"])
def test_a_float_column_in_gold_or_silver_is_reported(data_type: str) -> None:
    for schema in ("gold", "silver"):
        problems = check_no_float_columns(_catalog((schema, "amount", data_type)))
        assert len(problems) == 1
        assert "amount" in problems[0]


def test_a_float_column_in_another_schema_is_not_reported() -> None:
    assert check_no_float_columns(_catalog(("bronze", "amount", "DOUBLE"))) == []


def test_silver_and_gold_of_the_built_catalog_have_no_float_column(duckdb_catalog: dict) -> None:
    assert check_no_float_columns(duckdb_catalog) == []
    assert _checked_relations(duckdb_catalog, "silver") >= 1
    assert _checked_relations(duckdb_catalog, "gold") >= 1

"""Test placement: every test sits in the layer where its issue can be fixed (TEST-01, D-15).

The policy, in one place so an adopter can read it and change one entry:

- Landing and bronze hold row-key tests, because a duplicated or missing row is a loading fault
  and the fix is in the loader.
- Silver holds accepted-values tests, because an unknown code is fixed by a rule or a seed.
- Bronze holds accepted-values tests only as the 0/1 domain check of a rule flag that bronze
  computes itself. This is the one stated exception to "accepted values in silver", and it
  follows the same principle: a flag outside 0/1 is fixed in the bronze rule that computes it.
  A code list belongs in silver.
- Silver holds uniqueness after deduplication.
- Gold holds relationships and reconciliation, because a missing dimension member or a lost trip
  is fixed in the gold model.

Classification uses the resource type of the node a test is attached to, not its schema: seeds
build into the `bronze` schema but are not a layer, so a test on a seed counts under `seeds`.
The checks are pure functions over a manifest dict, like `test_layer_direction.py`.
"""

from __future__ import annotations

import copy
import fnmatch
import re

import pytest
from support.dbt_cli import REPO_ROOT

PACKAGE = "dbt_starter"

# The five places a test can sit, in pipeline order. The overview table lists them in this order.
LAYER_ORDER = ("landing", "bronze", "silver", "gold", "seeds")

# Test kinds that may sit only in gold, because their issue is fixed in a gold model.
GOLD_ONLY_KINDS = frozenset({"relationships", "totals_reconcile"})

# The layers that may hold an accepted-values test at all.
ACCEPTED_VALUES_LAYERS = frozenset({"bronze", "silver"})

# The only accepted values a bronze test may check: the 0/1 domain of a rule flag.
BRONZE_FLAG_VALUES = [0, 1]

# Named tests that the placement rule permits where it otherwise would not. Each needs a reason.
PLACEMENT_EXCEPTIONS: frozenset[str] = frozenset(
    {
        # A data-quality failure row must name a registered rule. The fix is in the rule
        # registry, which silver reads, so this relationships test sits in silver.
        "relationships_silver__dq_rule_failures_rule_code__code__ref_silver__dq_rule_catalog_",
    }
)


def layer_of(test_node: dict, manifest: dict) -> str:
    """Name where a test sits: `landing`, `bronze`, `silver`, `gold` or `seeds`.

    The resource type of the attached node decides, not its schema: a seed builds into the
    `bronze` schema but is not a layer. A test with no attached node that depends on a source
    sits in landing. Anything else is `unknown`.
    """
    attached = test_node.get("attached_node")
    if attached:
        node = manifest["nodes"].get(attached)
        if node is None:
            return "unknown"
        if node["resource_type"] == "seed":
            return "seeds"
        return node["fqn"][1]
    for upstream in test_node["depends_on"]["nodes"]:
        if upstream.startswith("source."):
            return "landing"
    return "unknown"


def kind_of(test_node: dict) -> str:
    """The test kind: `unique`, `relationships`, `accepted_values`, and so on."""
    return test_node["test_metadata"]["name"]


def package_tests(manifest: dict, package: str = PACKAGE) -> list[dict]:
    """The generic tests the starter package owns. Singular tests and other packages are skipped."""
    return [
        node
        for node in manifest["nodes"].values()
        if node["resource_type"] == "test"
        and node.get("package_name") == package
        and node.get("test_metadata")
    ]


def _keyed(manifest: dict, kinds: set[str]) -> set[str]:
    """The ids of the models, seeds and sources that carry a test of one of the given kinds."""
    keyed = set()
    for node in package_tests(manifest):
        if kind_of(node) not in kinds:
            continue
        keyed.add(node.get("attached_node") or next(iter(node["depends_on"]["nodes"]), ""))
    return keyed


def _placement_problem(node: dict, layer: str, exceptions: frozenset[str]) -> str | None:
    name, kind = node["name"], kind_of(node)
    if kind in GOLD_ONLY_KINDS and layer != "gold" and name not in exceptions:
        return f"{name}: a {kind} test sits in {layer}; it belongs in gold"
    if kind == "accepted_values":
        if layer not in ACCEPTED_VALUES_LAYERS:
            return f"{name}: an accepted_values test sits in {layer}; it belongs in silver"
        values = node["test_metadata"]["kwargs"].get("values")
        if layer == "bronze" and values != BRONZE_FLAG_VALUES:
            return (
                f"{name}: a bronze accepted_values test must check a 0/1 rule flag; "
                f"a code list belongs in silver"
            )
    return None


def check_placement(manifest: dict, exceptions: frozenset[str] | None = None) -> list[str]:
    """Return every test that sits against the policy and every model that lacks its key test."""
    allowed = PLACEMENT_EXCEPTIONS if exceptions is None else exceptions
    problems = []
    for node in package_tests(manifest):
        layer = layer_of(node, manifest)
        if layer in LAYER_ORDER:
            found = _placement_problem(node, layer, allowed)
            if found:
                problems.append(found)
    row_keyed = _keyed(manifest, {"unique"})
    grain_keyed = _keyed(manifest, {"unique", "unique_combination_of_columns"})
    combo_keyed = _keyed(manifest, {"unique_combination_of_columns"})
    for uid, source in manifest.get("sources", {}).items():
        if source.get("package_name") == PACKAGE and uid not in combo_keyed:
            label = f"{source.get('source_name', '')}.{source['name']}".lstrip(".")
            problems.append(
                f"{label}: the landing source table has no unique_combination_of_columns"
            )
    for uid, node in manifest["nodes"].items():
        if node["resource_type"] != "model" or node.get("package_name") != PACKAGE:
            continue
        if node["fqn"][1] == "bronze" and uid not in row_keyed:
            problems.append(f"{node['name']}: the bronze model has no unique row-key test")
        if node["fqn"][1] == "gold" and uid not in grain_keyed:
            problems.append(
                f"{node['name']}: the gold model has no unique or unique_combination_of_columns test"
            )
    return sorted(problems)


OVERVIEW = REPO_ROOT / "models" / "overview.md"
TABLE_HEADING = "## What we test where"
INVENTORY_HEADING = "## Example test inventory"


CODE_SPAN = re.compile(r"`([^`]+)`")
SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")


def _table_rows(text: str, heading: str) -> list[list[str]]:
    """The data rows of the first Markdown table under a heading, as lists of stripped cells.

    The table starts at the first line that begins with `|` after the heading and ends at the
    next line that does not. The header row and the separator row are dropped.
    """
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == heading)
    except StopIteration:
        return []
    rows: list[list[str]] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("|"):
            rows.append([cell.strip() for cell in stripped.strip("|").split("|")])
        elif rows or stripped.startswith("#"):
            break
    return [row for row in rows[1:] if not all(SEPARATOR_CELL.match(cell) for cell in row)]


def _spans(cell: str) -> list[str]:
    return CODE_SPAN.findall(cell)


def parse_overview(text: str) -> list[tuple[str, list[str], str]]:
    """Read the `What we test where` table as (layer, test kinds in code spans, reason) rows."""
    rows = []
    for cells in _table_rows(text, TABLE_HEADING):
        if len(cells) < 3:
            continue
        rows.append((cells[0].strip("`"), _spans(cells[1]), cells[2]))
    return rows


def _kinds_by_layer(manifest: dict) -> dict[str, set[str]]:
    used: dict[str, set[str]] = {layer: set() for layer in LAYER_ORDER}
    for node in package_tests(manifest):
        layer = layer_of(node, manifest)
        if layer in used:
            used[layer].add(kind_of(node))
    return used


def check_overview_table(rows: list[tuple[str, list[str], str]], manifest: dict) -> list[str]:
    """Compare the table with the manifest in both directions, row by row.

    The rows must be the five layers in pipeline order. A row must name at least one kind and
    give a reason, name every kind the manifest has in that layer, and name no other kind.
    """
    problems = []
    layers = [layer for layer, _, _ in rows]
    if layers != list(LAYER_ORDER):
        problems.append(f"the rows are {layers}, expected {list(LAYER_ORDER)} in that order")
    used = _kinds_by_layer(manifest)
    for layer, kinds, reason in rows:
        if not kinds:
            problems.append(f"{layer}: the row names no test kind")
        if not reason.strip():
            problems.append(f"{layer}: the row gives no reason")
        known = used.get(layer, set())
        for kind in sorted(known - set(kinds)):
            problems.append(f"{layer}: the row omits {kind}, which a {layer} test uses")
        for kind in sorted(set(kinds) - known):
            problems.append(f"{layer}: the row names {kind}, which no {layer} test uses")
    return problems


def _relation_of(node: dict, manifest: dict) -> str:
    """The name of the model, seed or source table a test belongs to."""
    attached = node.get("attached_node")
    if attached:
        return manifest["nodes"][attached]["name"]
    source = manifest["sources"][next(iter(node["depends_on"]["nodes"]))]
    return f"{source['source_name']}.{source['name']}"


def _dimension_of(to: str) -> str:
    """The relation a relationships test points at, from its `ref('x')` or `source('a', 'b')`."""
    match = re.match(r"(?:ref|source)\((.*)\)$", to.strip())
    if not match:
        return to
    return ".".join(part.strip().strip("'\"") for part in match.group(1).split(","))


def _expected_inventory(manifest: dict) -> dict[str, dict[str, set[str]]]:
    """Per relation: its key tests, relationships tests and accepted-values columns."""
    expected: dict[str, dict[str, set[str]]] = {}
    for node in package_tests(manifest):
        if layer_of(node, manifest) not in LAYER_ORDER:
            continue
        kind, kwargs = kind_of(node), node["test_metadata"]["kwargs"]
        relation = _relation_of(node, manifest)
        entry = expected.setdefault(relation, {"keys": set(), "rels": set(), "values": set()})
        if kind == "unique":
            entry["keys"].add(kwargs["column_name"])
        elif kind == "unique_combination_of_columns":
            entry["keys"].add(" + ".join(kwargs["combination_of_columns"]))
        elif kind == "relationships":
            target = _dimension_of(kwargs["to"])
            entry["rels"].add(f"{relation}.{kwargs['column_name']} -> {target}")
        elif kind == "accepted_values":
            entry["values"].add(kwargs["column_name"])
    return expected


def _covers(spans: list[str], column: str) -> bool:
    """A column is covered by its own code span or by a code span glob such as `dq_fail_*`."""
    return any(
        span == column or ("*" in span and fnmatch.fnmatchcase(column, span)) for span in spans
    )


def check_inventory(text: str, manifest: dict) -> list[str]:
    """Compare the example inventory with the manifest's key, relationships and value tests.

    Every relation that has such a test needs a row; every key test, relationships test and
    accepted-values column must appear in it (a glob code span covers many columns); and a row
    may not name a key or relationship that no test has.
    """
    rows = {
        cells[0].strip("`"): cells
        for cells in _table_rows(text, INVENTORY_HEADING)
        if len(cells) >= 4
    }
    if not rows:
        return [f"the '{INVENTORY_HEADING.lstrip('# ')}' table is missing or has no rows"]
    problems = []
    expected = _expected_inventory(manifest)
    for relation, entry in sorted(expected.items()):
        cells = rows.get(relation)
        if cells is None:
            problems.append(f"{relation}: the inventory has no row for this relation")
            continue
        keys, rels, values = _spans(cells[1]), _spans(cells[2]), _spans(cells[3])
        for key in sorted(entry["keys"] - set(keys)):
            problems.append(f"{relation}: the inventory omits the key test on {key}")
        for rel in sorted(entry["rels"] - set(rels)):
            problems.append(f"{relation}: the inventory omits the relationships test {rel}")
        for column in sorted(c for c in entry["values"] if not _covers(values, c)):
            problems.append(f"{relation}: the inventory omits the accepted_values test on {column}")
        for key in sorted(set(keys) - entry["keys"]):
            problems.append(
                f"{relation}: the inventory names a key test on {key} that does not exist"
            )
        for rel in sorted(set(rels) - entry["rels"]):
            problems.append(f"{relation}: the inventory names {rel}, which no test has")
    return problems


def _test(name: str, kind: str, attached: str | None = None, source: str | None = None) -> dict:
    """A generic test node as dbt 1.12 writes it, reduced to the fields the policy reads."""
    return {
        "resource_type": "test",
        "package_name": PACKAGE,
        "name": name,
        "attached_node": attached,
        "depends_on": {"nodes": [source] if source else [attached] if attached else []},
        "test_metadata": {"name": kind, "kwargs": {}},
    }


def _model(name: str, layer: str) -> tuple[str, dict]:
    uid = f"model.{PACKAGE}.{name}"
    return uid, {
        "resource_type": "model",
        "package_name": PACKAGE,
        "name": name,
        "fqn": [PACKAGE, layer, "group", name],
    }


SOURCE_ID = f"source.{PACKAGE}.feed.table"
SEED_ID = f"seed.{PACKAGE}.codes"
BRONZE_ID, BRONZE_NODE = _model("bronze__a", "bronze")
SILVER_ID, SILVER_NODE = _model("silver__a", "silver")
GOLD_ID, GOLD_NODE = _model("gold__a", "gold")


def _manifest(tests: list[dict]) -> dict:
    """A small manifest that satisfies the row-key rules, plus the given tests."""
    key_tests = [
        _test("landing_key", "unique_combination_of_columns", source=SOURCE_ID),
        _test("bronze_key", "unique", attached=BRONZE_ID),
        _test("gold_key", "unique", attached=GOLD_ID),
    ]
    nodes = {
        BRONZE_ID: BRONZE_NODE,
        SILVER_ID: SILVER_NODE,
        GOLD_ID: GOLD_NODE,
        SEED_ID: {
            "resource_type": "seed",
            "package_name": PACKAGE,
            "name": "codes",
            "fqn": [PACKAGE, "codes"],
            "schema": "bronze",
        },
    }
    for node in key_tests + tests:
        nodes[f"test.{PACKAGE}.{node['name']}"] = node
    sources = {
        SOURCE_ID: {
            "resource_type": "source",
            "package_name": PACKAGE,
            "source_name": "feed",
            "name": "table",
        }
    }
    return {"nodes": nodes, "sources": sources}


def test_a_small_manifest_with_the_key_tests_passes() -> None:
    assert check_placement(_manifest([])) == []


def test_a_test_on_a_seed_is_classified_seeds_although_the_seed_builds_into_bronze() -> None:
    manifest = _manifest([_test("seed_unique", "unique", attached=SEED_ID)])
    node = manifest["nodes"][f"test.{PACKAGE}.seed_unique"]
    assert manifest["nodes"][SEED_ID]["schema"] == "bronze"
    assert layer_of(node, manifest) == "seeds"


def test_a_test_on_a_source_is_classified_landing() -> None:
    manifest = _manifest([])
    node = manifest["nodes"][f"test.{PACKAGE}.landing_key"]
    assert layer_of(node, manifest) == "landing"
    assert kind_of(node) == "unique_combination_of_columns"


def test_a_test_on_a_model_is_classified_by_the_layer_folder_of_the_model() -> None:
    manifest = _manifest([_test("silver_nn", "not_null", attached=SILVER_ID)])
    assert layer_of(manifest["nodes"][f"test.{PACKAGE}.silver_nn"], manifest) == "silver"
    assert layer_of(manifest["nodes"][f"test.{PACKAGE}.bronze_key"], manifest) == "bronze"
    assert layer_of(manifest["nodes"][f"test.{PACKAGE}.gold_key"], manifest) == "gold"


def _with_kwargs(node: dict, **kwargs: object) -> dict:
    node["test_metadata"]["kwargs"].update(kwargs)
    return node


def test_a_relationships_test_on_a_silver_model_is_reported_with_its_name_and_gold() -> None:
    manifest = _manifest([_test("silver_rel", "relationships", attached=SILVER_ID)])
    problems = check_placement(manifest)
    assert len(problems) == 1
    assert "silver_rel" in problems[0]
    assert "gold" in problems[0]


def test_a_totals_reconcile_test_outside_gold_is_reported() -> None:
    manifest = _manifest([_test("bronze_totals", "totals_reconcile", attached=BRONZE_ID)])
    problems = check_placement(manifest)
    assert len(problems) == 1
    assert "bronze_totals" in problems[0]


def test_a_relationships_test_in_gold_passes() -> None:
    manifest = _manifest([_test("gold_rel", "relationships", attached=GOLD_ID)])
    assert check_placement(manifest) == []


def test_a_named_exception_permits_that_test_and_no_other_silver_relationships_test() -> None:
    manifest = _manifest(
        [
            _test("silver_rel_ok", "relationships", attached=SILVER_ID),
            _test("silver_rel_bad", "relationships", attached=SILVER_ID),
        ]
    )
    problems = check_placement(manifest, exceptions=frozenset({"silver_rel_ok"}))
    assert len(problems) == 1
    assert "silver_rel_bad" in problems[0]


@pytest.mark.parametrize("attached", [GOLD_ID, SEED_ID])
def test_an_accepted_values_test_in_gold_or_on_a_seed_is_reported(attached: str) -> None:
    manifest = _manifest([_test("misplaced_av", "accepted_values", attached=attached)])
    problems = check_placement(manifest)
    assert len(problems) == 1
    assert "misplaced_av" in problems[0]
    assert "silver" in problems[0]


def test_an_accepted_values_test_on_a_landing_source_is_reported() -> None:
    manifest = _manifest([_test("landing_av", "accepted_values", source=SOURCE_ID)])
    problems = check_placement(manifest)
    assert len(problems) == 1
    assert "landing_av" in problems[0]


def test_a_bronze_accepted_values_test_on_the_zero_one_flag_domain_passes() -> None:
    node = _with_kwargs(_test("bronze_flag", "accepted_values", attached=BRONZE_ID), values=[0, 1])
    assert check_placement(_manifest([node])) == []


def test_a_bronze_accepted_values_code_list_is_reported_with_its_name_and_silver() -> None:
    node = _with_kwargs(
        _test("bronze_codes", "accepted_values", attached=BRONZE_ID), values=["a", "b"]
    )
    problems = check_placement(_manifest([node]))
    assert len(problems) == 1
    assert "bronze_codes" in problems[0]
    assert "silver" in problems[0]


def test_a_silver_accepted_values_code_list_passes() -> None:
    node = _with_kwargs(
        _test("silver_codes", "accepted_values", attached=SILVER_ID), values=["error", "warn"]
    )
    assert check_placement(_manifest([node])) == []


def test_a_gold_model_with_no_grain_test_is_reported_by_name() -> None:
    manifest = _manifest([])
    del manifest["nodes"][f"test.{PACKAGE}.gold_key"]
    problems = check_placement(manifest)
    assert len(problems) == 1
    assert "gold__a" in problems[0]


def test_a_gold_model_with_a_combination_grain_test_passes() -> None:
    manifest = _manifest([])
    manifest["nodes"][f"test.{PACKAGE}.gold_key"]["test_metadata"]["name"] = (
        "unique_combination_of_columns"
    )
    assert check_placement(manifest) == []


def test_a_bronze_model_with_no_unique_test_is_reported_by_name() -> None:
    manifest = _manifest([])
    del manifest["nodes"][f"test.{PACKAGE}.bronze_key"]
    problems = check_placement(manifest)
    assert len(problems) == 1
    assert "bronze__a" in problems[0]


def test_a_landing_source_with_no_key_test_is_reported_by_name() -> None:
    manifest = _manifest([])
    del manifest["nodes"][f"test.{PACKAGE}.landing_key"]
    problems = check_placement(manifest)
    assert len(problems) == 1
    assert "feed.table" in problems[0]


def test_tests_of_other_packages_are_not_checked() -> None:
    node = _test("foreign_rel", "relationships", attached=SILVER_ID)
    node["package_name"] = "dbt_utils"
    assert check_placement(_manifest([node])) == []


def test_the_only_placement_exception_is_the_rule_catalog_relationship() -> None:
    assert PLACEMENT_EXCEPTIONS == {
        "relationships_silver__dq_rule_failures_rule_code__code__ref_silver__dq_rule_catalog_"
    }


def test_the_real_manifest_places_every_test_where_its_issue_is_fixed(
    duckdb_manifest: dict,
) -> None:
    assert check_placement(duckdb_manifest) == []


def test_the_real_manifest_has_a_test_in_every_layer(duckdb_manifest: dict) -> None:
    seen = {
        layer_of(node, duckdb_manifest)
        for node in duckdb_manifest["nodes"].values()
        if node["resource_type"] == "test" and node["package_name"] == PACKAGE
        if node.get("test_metadata")
    }
    assert seen == set(LAYER_ORDER), "a layer with no test would make its row check vacuous"


def test_the_placement_exception_names_a_test_that_exists_in_the_real_manifest(
    duckdb_manifest: dict,
) -> None:
    names = {n["name"] for n in duckdb_manifest["nodes"].values() if n["resource_type"] == "test"}
    assert PLACEMENT_EXCEPTIONS <= names


def test_the_real_manifest_moved_relationships_test_is_reported(duckdb_manifest: dict) -> None:
    doctored = copy.deepcopy(duckdb_manifest)
    node = doctored["nodes"][
        next(
            uid
            for uid, n in doctored["nodes"].items()
            if n["resource_type"] == "test" and n["name"] == "fct_trips_pu_location_id_has_a_zone"
        )
    ]
    node["attached_node"] = "model.dbt_starter.silver__yellow_trips"
    problems = check_placement(doctored)
    assert any("fct_trips_pu_location_id_has_a_zone" in p and "gold" in p for p in problems)


def _overview() -> str:
    assert OVERVIEW.is_file(), f"{OVERVIEW} does not exist, so the docs have no landing page"
    return OVERVIEW.read_text()


def test_the_overview_is_the_docs_landing_block_and_renders_no_jinja() -> None:
    text = _overview()
    assert "{% docs __overview__ %}" in text
    assert "{% enddocs %}" in text
    assert "{{" not in text
    assert TABLE_HEADING in text
    assert "<!-- example:begin -->" in text
    assert "<!-- example:end -->" in text


def test_the_overview_table_has_five_rows_in_pipeline_order_each_with_kinds_and_a_reason() -> None:
    rows = parse_overview(_overview())
    assert [layer for layer, _, _ in rows] == list(LAYER_ORDER)
    for layer, kinds, reason in rows:
        assert kinds, f"{layer} names no test kind"
        assert reason.strip(), f"{layer} gives no reason"


def test_the_overview_table_matches_the_kinds_of_the_real_manifest(duckdb_manifest: dict) -> None:
    assert check_overview_table(parse_overview(_overview()), duckdb_manifest) == []


def test_parse_overview_reads_a_small_table() -> None:
    text = (
        "intro\n\n## What we test where\n\n"
        "| Layer | Tests | Why here |\n| --- | --- | --- |\n"
        "| landing | `unique`, `not_null` | The loader fixes it. |\n"
        "| `gold` | `relationships` | The model fixes it. |\n\n"
        "A sentence after the table.\n\n## Another heading\n| x | `y` | z |\n"
    )
    assert parse_overview(text) == [
        ("landing", ["unique", "not_null"], "The loader fixes it."),
        ("gold", ["relationships"], "The model fixes it."),
    ]


def test_parse_overview_finds_no_rows_when_the_heading_is_missing() -> None:
    assert parse_overview("| landing | `unique` | why |\n") == []


def _real_rows() -> list[tuple[str, list[str], str]]:
    return parse_overview(_overview())


def test_a_manifest_kind_missing_from_its_row_is_reported(duckdb_manifest: dict) -> None:
    rows = [
        (layer, [k for k in kinds if k != "totals_reconcile"], why)
        for layer, kinds, why in _real_rows()
    ]
    problems = check_overview_table(rows, duckdb_manifest)
    assert len(problems) == 1
    assert "gold" in problems[0]
    assert "totals_reconcile" in problems[0]


def test_a_row_naming_a_kind_no_test_of_the_layer_uses_is_reported(duckdb_manifest: dict) -> None:
    rows = [
        (layer, kinds + ["accepted_range"] if layer == "gold" else kinds, why)
        for layer, kinds, why in _real_rows()
    ]
    problems = check_overview_table(rows, duckdb_manifest)
    assert len(problems) == 1
    assert "gold" in problems[0]
    assert "accepted_range" in problems[0]


def test_a_row_with_no_kind_is_reported(duckdb_manifest: dict) -> None:
    rows = [(layer, [] if layer == "seeds" else kinds, why) for layer, kinds, why in _real_rows()]
    problems = check_overview_table(rows, duckdb_manifest)
    assert any("seeds" in p and "no test kind" in p for p in problems)


def test_rows_out_of_pipeline_order_are_reported(duckdb_manifest: dict) -> None:
    rows = _real_rows()
    rows[0], rows[1] = rows[1], rows[0]
    problems = check_overview_table(rows, duckdb_manifest)
    assert any("order" in p for p in problems)


def test_a_missing_row_is_reported(duckdb_manifest: dict) -> None:
    problems = check_overview_table(_real_rows()[:-1], duckdb_manifest)
    assert any("order" in p for p in problems)


def test_the_example_inventory_matches_the_real_manifest(duckdb_manifest: dict) -> None:
    assert check_inventory(_overview(), duckdb_manifest) == []


@pytest.mark.parametrize(
    ("omitted", "expected"),
    [
        ("`gold__fct_trips.pu_location_id -> gold__dim_zone`", "gold__fct_trips.pu_location_id"),
        ("`pickup_date_key + pickup_borough`", "pickup_date_key + pickup_borough"),
        ("`is_airport`", "is_airport"),
    ],
)
def test_an_inventory_that_omits_a_real_test_is_reported(
    duckdb_manifest: dict, omitted: str, expected: str
) -> None:
    text = _overview()
    assert omitted in text, f"the inventory does not hold {omitted}, so this case is vacuous"
    problems = check_inventory(text.replace(omitted, ""), duckdb_manifest)
    assert any(expected in p for p in problems)


def test_an_inventory_with_no_row_for_a_relation_is_reported(duckdb_manifest: dict) -> None:
    lines = [ln for ln in _overview().splitlines() if "| `gold__dim_vendor` |" not in ln]
    problems = check_inventory("\n".join(lines), duckdb_manifest)
    assert any("gold__dim_vendor" in p for p in problems)


def test_an_inventory_naming_a_relationship_no_test_has_is_reported(duckdb_manifest: dict) -> None:
    text = _overview().replace(
        "`gold__fct_trips.pu_location_id -> gold__dim_zone`",
        "`gold__fct_trips.pu_location_id -> gold__dim_zone`, `gold__fct_trips.nope -> gold__dim_zone`",
    )
    problems = check_inventory(text, duckdb_manifest)
    assert any("gold__fct_trips.nope" in p for p in problems)

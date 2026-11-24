"""The demo's printer and flags without dbt, a download or the example data."""

from __future__ import annotations

import json
import re
import subprocess
import types
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from starter_cli import demo
from starter_cli.demo import FABRIC_LABEL, Levels, report_lines

GITHUB_REMOTE = "https://github.com/Owner/repo.git"
CONSERVATION = {"things": ("bronze.things", "silver.things", "silver.things_quarantine")}
GOLD = [("gold.fct", "gold.agg", "trip_count")]


@pytest.fixture(autouse=True)
def restore_working_directory(monkeypatch, tmp_path):
    """`demo.main` changes into the repo root; monkeypatch puts the working directory back."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DBT_TARGET_PATH", raising=False)


@pytest.fixture
def con():
    connection = duckdb.connect()
    connection.execute("create schema silver")
    connection.execute("create table silver.parts (id integer, dq_warning_count integer)")
    connection.execute("insert into silver.parts values (1, 0), (2, 1), (3, 2), (4, 0)")
    connection.execute("create table silver.parts_quarantine (id integer)")
    yield connection
    connection.close()


def fill(connection, relation: str, rows: int, column: str = "id") -> None:
    """Create `relation` with `rows` rows of one integer column."""
    connection.execute(f"create table {relation} as select range as {column} from range({rows})")


def fill_files(connection, relation: str, per_file: dict[str | None, int]) -> None:
    """Create `relation` with `_source_file` and the given number of rows per file (None is NULL)."""
    selects = (
        " union all ".join(
            f"select range as id, {'null' if name is None else repr(name)} as _source_file "
            f"from range({rows})"
            for name, rows in per_file.items()
        )
        or "select 1 as id, 'x' as _source_file where false"
    )
    connection.execute(f"create table {relation} as {selects}")


@pytest.fixture
def layers():
    """Bronze 10 = silver 7 + quarantine 3 over files a.csv (6 = 5 + 1) and b.csv (4 = 2 + 2).

    Gold has a fact of 5 rows that the aggregate matches.
    """
    connection = duckdb.connect()
    for schema in ("bronze", "silver", "gold"):
        connection.execute(f"create schema {schema}")
    fill_files(connection, "bronze.things", {"a.csv": 6, "b.csv": 4})
    fill_files(connection, "silver.things", {"a.csv": 5, "b.csv": 2})
    fill(connection, "silver.things_quarantine", 3)
    fill(connection, "gold.fct", 5)
    connection.execute(
        "create table gold.agg as "
        "select 1 as day_key, cast(2 as bigint) as trip_count "
        "union all select 2, cast(3 as bigint)"
    )
    connection.execute(
        "create table silver.dq_quarantine_summary ("
        "entity varchar, rule_code varchar, severity varchar, failed_rows bigint, "
        "failed_rate decimal(18, 6), entity_rows bigint, entity_quarantined_rows bigint, "
        "entity_quarantine_rate decimal(18, 6), entity_warned_rows bigint, "
        "entity_warned_rate decimal(18, 6))"
    )
    yield connection
    connection.close()


def add_summary_row(connection, entity, code, severity, failed, rows, quarantined, rate):
    connection.execute(
        "insert into silver.dq_quarantine_summary values (?, ?, ?, ?, 0, ?, ?, ?, 0, 0)",
        [entity, code, severity, failed, rows, quarantined, rate],
    )


def test_relation_lines_come_first_then_a_warnings_line_and_no_fabric_label(con):
    lines = report_lines(
        con,
        [("silver", "silver.parts"), ("quarantine", "silver.parts_quarantine")],
        ["silver.parts"],
    )
    assert lines[0].split() == ["layer", "relation", "rows"]
    assert lines[1].split() == ["silver", "silver.parts", "4"]
    assert lines[2].split() == ["quarantine", "silver.parts_quarantine", "0"]
    assert lines[3].split() == ["warnings", "silver.parts", "2"]
    assert FABRIC_LABEL not in lines
    assert len(lines) == 4


def test_a_source_without_warnings_still_prints_its_lines(con):
    lines = report_lines(con, [("silver", "silver.parts")], [])
    assert [line.split()[0] for line in lines] == ["layer", "silver"]


def test_the_rows_column_holds_nine_digits(con):
    line = report_lines(con, [("silver", "silver.parts")])[1]
    assert line.endswith(f"{4:>9}")


@pytest.mark.parametrize("bad", ["silver.parts; drop table silver.parts", "Silver.Parts", "parts"])
def test_a_relation_name_that_is_not_plain_is_rejected_before_any_query(con, bad):
    with pytest.raises(ValueError, match="must match"):
        report_lines(con, [("silver", bad)])
    with pytest.raises(ValueError, match="must match"):
        report_lines(con, [], [bad])


def test_conservation_line_says_ok_when_bronze_equals_silver_plus_quarantine(layers):
    lines, ok = demo.conservation_lines(layers, CONSERVATION)
    assert lines == [f"  {'things':<14} bronze 10 = silver 7 + quarantine 3  OK"]
    assert ok is True


def test_conservation_line_says_fail_and_returns_false_when_a_row_is_missing(layers):
    layers.execute("delete from silver.things_quarantine where id = 0")
    lines, ok = demo.conservation_lines(layers, CONSERVATION)
    assert lines == [f"  {'things':<14} bronze 10 = silver 7 + quarantine 2  FAIL"]
    assert ok is False


def test_conservation_numbers_carry_thousands_separators(layers):
    layers.execute("drop table bronze.things")
    layers.execute("drop table silver.things")
    fill(layers, "bronze.things", 1234567)
    fill(layers, "silver.things", 1234567)
    layers.execute("delete from silver.things_quarantine")
    lines, ok = demo.conservation_lines(layers, CONSERVATION)
    assert "bronze 1,234,567 = silver 1,234,567 + quarantine 0" in lines[0]
    assert ok is True


def test_one_failing_entity_fails_the_whole_conservation_set(layers):
    fill(layers, "bronze.others", 4)
    fill(layers, "silver.others", 4)
    fill(layers, "silver.others_quarantine", 1)
    both = {
        **CONSERVATION,
        "others": ("bronze.others", "silver.others", "silver.others_quarantine"),
    }
    lines, ok = demo.conservation_lines(layers, both)
    assert [line.rsplit(" ", 1)[1] for line in lines] == ["OK", "FAIL"]
    assert ok is False


def test_gold_line_says_ok_when_the_aggregate_sums_to_the_fact(layers):
    lines, ok = demo.gold_lines(layers, GOLD)
    assert lines == [f"  {'gold':<14} fct 5 = sum(agg.trip_count) 5  OK"]
    assert ok is True


def test_gold_line_says_fail_when_the_aggregate_sums_to_less(layers):
    layers.execute("update gold.agg set trip_count = 1 where day_key = 2")
    lines, ok = demo.gold_lines(layers, GOLD)
    assert lines == [f"  {'gold':<14} fct 5 = sum(agg.trip_count) 3  FAIL"]
    assert ok is False


def test_an_empty_aggregate_counts_as_zero(layers):
    layers.execute("delete from gold.agg")
    lines, ok = demo.gold_lines(layers, GOLD)
    assert lines == [f"  {'gold':<14} fct 5 = sum(agg.trip_count) 0  FAIL"]
    assert ok is False


class QueryTrap:
    """A connection that fails the test if anything is executed on it."""

    def execute(self, *_args, **_kwargs):
        pytest.fail("a query ran before the names were checked")


@pytest.mark.parametrize(
    "bad",
    [
        {"things": ("bronze.things", "silver.things", "silver.things_quarantine; drop")},
        {"things": ("Bronze.things", "silver.things", "silver.things_quarantine")},
        {"things": ("bronze.things", "things", "silver.things_quarantine")},
    ],
)
def test_conservation_rejects_a_bad_relation_before_any_query(bad):
    with pytest.raises(ValueError, match="must match"):
        demo.conservation_lines(QueryTrap(), {**CONSERVATION, "second": bad["things"]})


@pytest.mark.parametrize(
    "bad",
    [
        ("gold.fct; drop", "gold.agg", "trip_count"),
        ("gold.fct", "agg", "trip_count"),
        ("gold.fct", "gold.agg", "trip_count) from gold.fct; --"),
        ("gold.fct", "gold.agg", "Trip_Count"),
    ],
)
def test_gold_rejects_a_bad_relation_or_count_column_before_any_query(bad):
    with pytest.raises(ValueError, match="must match"):
        demo.gold_lines(QueryTrap(), [GOLD[0], bad])


def test_the_quarantine_rate_prints_once_per_entity_with_three_decimals(layers):
    add_summary_row(layers, "parts", "RULE_A", "error", 2, 100000, 3, "0.000030")
    add_summary_row(layers, "parts", "RULE_B", "error", 1, 100000, 3, "0.000030")
    lines = demo.quarantine_summary_lines(layers, {}, {})
    rate_lines = [line for line in lines if "quarantine rate" in line]
    assert rate_lines == [f"  {'parts':<14} quarantine rate 0.003% (3 of 100,000 rows)  no levels"]


def test_top_codes_are_capped_ordered_and_skip_codes_that_did_not_fire(layers):
    for code, failed in [("E1", 7), ("E2", 9), ("E3", 7), ("E4", 1), ("E5", 8), ("E6", 3)]:
        add_summary_row(layers, "parts", code, "error", failed, 50, 5, "0.1")
    add_summary_row(layers, "parts", "E7", "error", 0, 50, 5, "0.1")
    for code, failed in [("W1", 4), ("W2", 6), ("W3", 5), ("W4", 2), ("W5", 0)]:
        add_summary_row(layers, "parts", code, "warn", failed, 50, 5, "0.1")
    lines = demo.quarantine_summary_lines(layers, {}, {})
    codes = [line.split() for line in lines if line.startswith("    ")]
    assert [(c[0], c[1], c[2]) for c in codes] == [
        ("error", "E2", "9"),
        ("error", "E5", "8"),
        ("error", "E1", "7"),
        ("error", "E3", "7"),
        ("error", "E6", "3"),
        ("warn", "W2", "6"),
        ("warn", "W3", "5"),
        ("warn", "W1", "4"),
    ]


def test_a_code_line_has_the_documented_columns(layers):
    add_summary_row(layers, "parts", "RULE_A", "error", 1234, 100000, 3, "0.1")
    lines = demo.quarantine_summary_lines(layers, {}, {})
    assert f"    {'error':<6} {'RULE_A':<40} {1234:>12,}" in lines


def test_a_severity_with_no_fired_code_prints_none_and_entities_stay_apart(layers):
    add_summary_row(layers, "parts", "E1", "error", 2, 50, 2, "0.04")
    add_summary_row(layers, "parts", "W1", "warn", 0, 50, 2, "0.04")
    add_summary_row(layers, "zones", "E9", "error", 0, 20, 0, "0")
    lines = demo.quarantine_summary_lines(layers, {}, {})
    parts = lines.index(f"  {'parts':<14} quarantine rate 4.000% (2 of 50 rows)  no levels")
    zones = lines.index(f"  {'zones':<14} quarantine rate 0.000% (0 of 20 rows)  no levels")
    assert parts < zones
    assert lines[parts + 1].split() == ["error", "E1", "2"]
    assert lines[parts + 2] == f"    {'warn':<6} none"
    assert lines[zones + 1] == f"    {'error':<6} none"
    assert lines[zones + 2] == f"    {'warn':<6} none"


def test_an_empty_summary_prints_none_registered(layers):
    assert demo.quarantine_summary_lines(layers, {}, {}) == ["  none registered"]


def test_a_bad_summary_relation_is_rejected_before_any_query():
    with pytest.raises(ValueError, match="must match"):
        demo.quarantine_summary_lines(QueryTrap(), {}, {}, "silver.summary; drop")


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ("https://github.com/Owner/repo.git", "https://owner.github.io/repo/dbt-docs/"),
        ("https://github.com/Owner/repo", "https://owner.github.io/repo/dbt-docs/"),
        ("https://github.com/Owner/repo/", "https://owner.github.io/repo/dbt-docs/"),
        ("git@github.com:Owner/repo.git", "https://owner.github.io/repo/dbt-docs/"),
        ("ssh://git@github.com/Owner/repo.git", "https://owner.github.io/repo/dbt-docs/"),
        (
            "https://github.com/hartlesskl/dbt-starter.git",
            "https://hartlesskl.github.io/dbt-starter/dbt-docs/",
        ),
        ("https://github.com/Owner/Owner.github.io", "https://owner.github.io/dbt-docs/"),
        ("git@github.com:owner/OWNER.GITHUB.IO.git", "https://owner.github.io/dbt-docs/"),
        (
            "https://github.com/Owner/my.repo_1-x.git",
            "https://owner.github.io/my.repo_1-x/dbt-docs/",
        ),
    ],
)
def test_pages_url_maps_each_github_remote_form(remote, expected):
    assert demo.pages_url(remote) == expected


@pytest.mark.parametrize(
    "remote",
    [
        "https://gitlab.com/a/b.git",
        "git@gitlab.com:a/b.git",
        "",
        None,
        "https://github.com/ow ner/repo.git",
        "https://github.com/-owner/repo.git",
        "https://github.com/" + "o" * 40 + "/repo.git",
        "https://github.com/owner/..",
        "https://github.com/owner/.",
        "https://github.com/owner",
        "https://github.com/owner/repo/extra",
        "https://user@github.com/owner/repo.git",
        "http://github.com/owner/repo.git",
        "https://github.com.evil.example/owner/repo.git",
        "https://github.com/owner/repo.git; rm -rf /",
    ],
)
def test_pages_url_gives_none_for_anything_else(remote):
    assert demo.pages_url(remote) is None


def test_docs_lines_name_the_static_file_the_serve_command_and_the_url(tmp_path):
    lines = demo.docs_lines(tmp_path, "https://owner.github.io/repo/dbt-docs/")
    assert lines == [
        "docs",
        f"  open offline   {tmp_path / 'target' / 'static_index.html'}",
        "  or serve       uv run dbt docs serve",
        "  on Pages       https://owner.github.io/repo/dbt-docs/",
        "                 once Pages is enabled with ENABLE_PAGES=true (see README)",
    ]
    assert Path(lines[1].split()[-1]).is_absolute()


def test_docs_lines_never_say_the_docs_are_published(tmp_path):
    lines = demo.docs_lines(tmp_path, "https://owner.github.io/repo/dbt-docs/")
    assert not any("published" in line for line in lines)
    assert lines[-1].endswith("once Pages is enabled with ENABLE_PAGES=true (see README)")


def test_docs_lines_omit_the_pages_lines_without_a_url(tmp_path):
    lines = demo.docs_lines(tmp_path, None)
    assert len(lines) == 3
    assert not any("Pages" in line for line in lines)


def run_git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_git_remote_url_reads_origin(tmp_path):
    run_git("init", "-q", cwd=tmp_path)
    run_git("remote", "add", "origin", GITHUB_REMOTE, cwd=tmp_path)
    assert demo.git_remote_url(tmp_path) == GITHUB_REMOTE


def test_git_remote_url_is_none_without_an_origin(tmp_path):
    run_git("init", "-q", cwd=tmp_path)
    assert demo.git_remote_url(tmp_path) is None


@pytest.mark.parametrize(
    "failure",
    [FileNotFoundError("git"), subprocess.TimeoutExpired("git", 5), PermissionError("git")],
)
def test_git_remote_url_is_none_when_git_cannot_run(tmp_path, monkeypatch, failure):
    def failing(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(demo.subprocess, "run", failing)
    assert demo.git_remote_url(tmp_path) is None


def closing(connection, tmp_path, url=None, levels=None):
    return demo.closing_lines(
        connection,
        tmp_path,
        [("silver", "silver.things")],
        [],
        CONSERVATION,
        GOLD,
        url,
        levels,
    )


def test_closing_lines_hold_the_sections_in_order_with_the_fabric_label_last(layers, tmp_path):
    add_summary_row(layers, "things", "E1", "error", 3, 10, 3, "0.3")
    lines, ok = closing(layers, tmp_path, "https://owner.github.io/repo/dbt-docs/")
    assert ok is True
    assert lines[-1] == FABRIC_LABEL
    assert lines[:2] == report_lines(layers, [("silver", "silver.things")], [])
    positions = [lines.index(h) for h in ("reconciliation", "quarantine summary", "docs")]
    assert positions == sorted(positions)
    assert lines.count("") == 4
    assert [lines[i - 1] for i in positions] == ["", "", ""]
    assert lines[-2] == ""


def test_closing_lines_report_the_conservation_line_before_the_gold_line(layers, tmp_path):
    lines, _ok = closing(layers, tmp_path)
    start = lines.index("reconciliation")
    assert lines[start + 1].endswith("bronze 10 = silver 7 + quarantine 3  OK")
    assert lines[start + 2].endswith("fct 5 = sum(agg.trip_count) 5  OK")


def test_closing_lines_return_false_and_print_fail_when_gold_does_not_reconcile(layers, tmp_path):
    layers.execute("delete from gold.agg where day_key = 2")
    lines, ok = closing(layers, tmp_path)
    assert ok is False
    assert any(line.endswith("fct 5 = sum(agg.trip_count) 2  FAIL") for line in lines)
    assert not any(line.endswith("sum(agg.trip_count) 2  OK") for line in lines)


def test_closing_lines_without_report_config_say_none_registered(con, tmp_path):
    con.execute("create schema if not exists silver")
    con.execute(
        "create table silver.dq_quarantine_summary (entity varchar, rule_code varchar, "
        "severity varchar, failed_rows bigint, failed_rate decimal(18, 6), entity_rows bigint, "
        "entity_quarantined_rows bigint, entity_quarantine_rate decimal(18, 6), "
        "entity_warned_rows bigint, entity_warned_rate decimal(18, 6))"
    )
    lines, ok = demo.closing_lines(con, tmp_path, [("silver", "silver.parts")], [], {}, [], None)
    assert ok is True
    assert lines[lines.index("reconciliation") + 1] == "  none registered"


def test_a_malformed_month_exits_2_before_anything_is_loaded(monkeypatch, capsys):
    def must_not_load(*_args, **_kwargs):
        raise AssertionError("something was loaded")

    monkeypatch.setattr(demo, "load_entity", must_not_load)
    with pytest.raises(SystemExit) as excinfo:
        demo.main(["--months", "2024-13"])
    assert excinfo.value.code == 2
    assert "2024-13" in capsys.readouterr().err


def test_a_from_dir_that_is_not_a_directory_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(demo, "load_entity", lambda *_a, **_k: pytest.fail("loaded"))
    with pytest.raises(SystemExit) as excinfo:
        demo.main(["--from-dir", str(tmp_path / "nope")])
    assert excinfo.value.code == 2
    assert "--from-dir" in capsys.readouterr().err


def test_a_failed_load_stops_the_demo_before_dbt_runs(monkeypatch, capsys):
    from starter_cli.landing import DownloadError

    def failing(*_args, **_kwargs):
        raise DownloadError("Could not download it. See README.md#troubleshooting.")

    monkeypatch.setattr(demo, "load_entity", failing)
    monkeypatch.setattr(demo, "_run_dbt", lambda *_a: pytest.fail("dbt ran"))
    assert demo.main([]) == 2
    assert "README.md#troubleshooting" in capsys.readouterr().err


def test_months_reach_only_monthly_entities_and_the_folder_reaches_all(tmp_path, monkeypatch):
    seen = []

    def recording(spec, root, **kwargs):
        seen.append((spec, kwargs))
        return 1

    monkeypatch.setattr(demo, "load_entity", recording)
    monkeypatch.setattr(demo, "drop_derived_schemas", lambda _database: None)
    monkeypatch.setattr(demo, "_run_dbt", lambda *_a: 1)  # stop right after the loads
    folder = tmp_path / "files"
    folder.mkdir()
    demo.main(["--months", "2024-01..2024-02", "--from-dir", str(folder), "--allow-large"])
    assert seen
    for spec, kwargs in seen:
        assert kwargs["from_dir"] == folder
        assert kwargs["allow_large"] is True
        assert kwargs["months"] == (("2024-01", "2024-02") if spec.is_monthly else None)


def stub_demo(monkeypatch, tmp_path, connection, module=None, codes=None):
    """Replace everything `main` does outside the printer; return the recorded calls.

    The calls are `("load", ())`, `("drop", (database,))` and one `(step, args)` per dbt step, in
    the order they happen. `codes` maps a dbt step to the exit code `_run_dbt` returns for it
    (default 0). The schema drop is a recorder: the in-memory connection keeps its schemas, and a
    test can never drop anything in the real demo database.
    """
    calls = []
    codes = codes or {}
    source = module or types.SimpleNamespace(
        ENTITIES={"things": types.SimpleNamespace(is_monthly=False, table="things")},
        REPORT_RELATIONS=[("silver", "silver.things")],
        REPORT_WARNINGS=[],
        REPORT_CONSERVATION=CONSERVATION,
        REPORT_GOLD=GOLD,
    )
    monkeypatch.setattr(demo, "discover_sources", lambda: {"src": source})
    monkeypatch.setattr(demo, "load_entity", lambda *_a, **_k: calls.append(("load", ())) or 1)
    monkeypatch.setattr(
        demo, "drop_derived_schemas", lambda database: calls.append(("drop", (database,)))
    )
    monkeypatch.setattr(
        demo, "_run_dbt", lambda step, *args: calls.append((step, args)) or codes.get(step, 0)
    )
    monkeypatch.setattr(demo, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(demo, "git_remote_url", lambda _root: GITHUB_REMOTE)
    monkeypatch.setattr(demo.duckdb, "connect", lambda *_a, **_k: connection)
    return calls


def test_main_exits_0_and_prints_every_section_when_all_lines_say_ok(
    layers, tmp_path, monkeypatch, capsys
):
    dbt_calls = stub_demo(monkeypatch, tmp_path, layers)
    assert demo.main([]) == 0
    out = capsys.readouterr()
    lines = out.out.splitlines()
    assert lines[-1] == FABRIC_LABEL
    assert "https://owner.github.io/repo/dbt-docs/" in out.out
    assert "once Pages is enabled with ENABLE_PAGES=true (see README)" in out.out
    assert "published" not in out.out
    assert "FAIL" not in out.out
    assert out.err == ""
    assert ("docs", ("generate", "--static")) in dbt_calls


def test_main_exits_1_with_a_message_when_a_reconciliation_line_says_fail(
    layers, tmp_path, monkeypatch, capsys
):
    layers.execute("delete from silver.things where id = 0 and _source_file = 'a.csv'")
    stub_demo(monkeypatch, tmp_path, layers)
    assert demo.main([]) == 1
    out = capsys.readouterr()
    assert "bronze 10 = silver 6 + quarantine 3  FAIL" in out.out
    assert "demo: a reconciliation line shows FAIL." in out.err
    assert "dbt tests" in out.err


def test_main_exits_1_when_only_the_gold_line_says_fail(layers, tmp_path, monkeypatch, capsys):
    layers.execute("delete from gold.agg")
    stub_demo(monkeypatch, tmp_path, layers)
    assert demo.main([]) == 1
    assert "sum(agg.trip_count) 0  FAIL" in capsys.readouterr().out


def test_main_reads_the_report_config_with_defaults_for_a_source_that_has_none(
    layers, tmp_path, monkeypatch, capsys
):
    bare = types.SimpleNamespace(
        ENTITIES={"things": types.SimpleNamespace(is_monthly=False, table="things")},
        REPORT_RELATIONS=[("silver", "silver.things")],
    )
    stub_demo(monkeypatch, tmp_path, layers, module=bare)
    assert demo.main([]) == 0
    out = capsys.readouterr().out
    assert "none registered" in out
    assert out.splitlines()[-1] == FABRIC_LABEL


def test_main_omits_the_pages_lines_when_there_is_no_usable_remote(
    layers, tmp_path, monkeypatch, capsys
):
    stub_demo(monkeypatch, tmp_path, layers)
    monkeypatch.setattr(demo, "git_remote_url", lambda _root: None)
    assert demo.main([]) == 0
    out = capsys.readouterr().out
    assert "on Pages" not in out
    assert "ENABLE_PAGES" not in out
    assert "uv run dbt docs serve" in out


# --- quarantine rate against levels (D-06): the printer reports, dbt's levels tests enforce ---


LEVEL_NODE = "model.pkg.{alias}"


def manifest_with(*entries, extra_tests=()):
    """A small manifest. Each entry is (schema, alias, warn_above, error_above) for one levels test."""
    nodes = {}
    for index, (schema, alias, warn, error) in enumerate(entries):
        model = LEVEL_NODE.format(alias=alias)
        nodes[model] = {"resource_type": "model", "schema": schema, "alias": alias}
        nodes[f"test.pkg.levels_{index}"] = {
            "resource_type": "test",
            "attached_node": model,
            "test_metadata": {
                "name": "quarantine_rate_levels",
                "kwargs": {"warn_above": warn, "error_above": error},
            },
        }
    for index, node in enumerate(extra_tests):
        nodes[f"test.pkg.extra_{index}"] = node
    return {"nodes": nodes}


def write_manifest(folder: Path, manifest) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def levels_for(warn: str, error: str, relation: str = "silver.things"):
    return {relation: Levels(Decimal(warn), Decimal(error))}


def things_entity_row(connection) -> None:
    add_summary_row(connection, "things", "E1", "error", 3, 10, 3, "0.3")


def entity_line(rate: str, rejected: int, total: int, tail: str, name: str = "things") -> str:
    return f"  {name:<14} quarantine rate {rate} ({rejected:,} of {total:,} rows){tail}"


def test_levels_is_a_pair_of_decimals():
    levels = Levels(Decimal("0.001"), Decimal("0.01"))
    assert (levels.warn_above, levels.error_above) == (Decimal("0.001"), Decimal("0.01"))


def test_the_entity_line_and_the_worst_file_line_show_the_rates_per_source_file(layers):
    things_entity_row(layers)
    lines = demo.quarantine_summary_lines(layers, CONSERVATION, {})
    assert lines[0] == entity_line("30.000%", 3, 10, "  no levels")
    assert lines[1] == "    worst file of 2: b.csv  50.000% (2 of 4 rows)"
    assert lines[2:] == [f"    {'error':<6} {'E1':<40} {3:>12,}", f"    {'warn':<6} none"]


@pytest.mark.parametrize(
    ("warn", "error", "warn_text", "error_text", "entity_word", "worst_word"),
    [
        ("0.1", "0.4", "10%", "40%", "WARN", "ERROR"),
        ("0.5", "0.9", "50%", "90%", "OK", "OK"),
        ("0.31", "0.9", "31%", "90%", "OK", "WARN"),
        ("0.3", "0.9", "30%", "90%", "OK", "WARN"),
    ],
)
def test_each_rate_is_marked_against_its_levels_and_equal_to_a_level_is_ok(
    layers, warn, error, warn_text, error_text, entity_word, worst_word
):
    things_entity_row(layers)
    lines = demo.quarantine_summary_lines(layers, CONSERVATION, levels_for(warn, error))
    tail = f"  warn {warn_text}  error {error_text}  {entity_word}"
    assert lines[0] == entity_line("30.000%", 3, 10, tail)
    assert lines[1] == f"    worst file of 2: b.csv  50.000% (2 of 4 rows)  {worst_word}"


def test_a_rate_equal_to_the_error_level_is_warn_not_error(layers):
    things_entity_row(layers)
    lines = demo.quarantine_summary_lines(layers, CONSERVATION, levels_for("0.1", "0.5"))
    assert lines[1].endswith("(2 of 4 rows)  WARN")


@pytest.mark.parametrize(
    ("warn", "error"), [("0.1", "0.4"), ("0.5", "0.9"), ("0.31", "0.9"), ("0.0", "0.1")]
)
def test_a_rate_word_never_changes_the_ok_flag_of_the_closing_lines(layers, tmp_path, warn, error):
    things_entity_row(layers)
    lines, ok = closing(layers, tmp_path, levels=levels_for(warn, error))
    assert ok is True
    assert any(line.endswith("  OK") and "bronze 10 = silver 7" in line for line in lines)


def test_a_reconciliation_fail_is_still_the_only_thing_that_clears_the_ok_flag(layers, tmp_path):
    things_entity_row(layers)
    layers.execute("delete from silver.things_quarantine where id = 0")
    _lines, ok = closing(layers, tmp_path, levels=levels_for("0.5", "0.9"))
    assert ok is False


def test_the_worst_file_is_the_one_first_in_name_order_when_rates_tie(layers):
    for relation in ("bronze.things", "silver.things"):
        layers.execute(f"drop table {relation}")
    fill_files(layers, "bronze.things", {"c.csv": 4, "b.csv": 4, "a.csv": 4})
    fill_files(layers, "silver.things", {"c.csv": 4, "b.csv": 2, "a.csv": 2})
    things_entity_row(layers)
    lines = demo.quarantine_summary_lines(layers, CONSERVATION, levels_for("0.1", "0.9"))
    assert lines[0] == entity_line("33.333%", 4, 12, "  warn 10%  error 90%  WARN")
    assert lines[1] == "    worst file of 3: a.csv  50.000% (2 of 4 rows)  WARN"


def test_a_null_source_file_is_a_file_named_none(layers):
    for relation in ("bronze.things", "silver.things"):
        layers.execute(f"drop table {relation}")
    fill_files(layers, "bronze.things", {"a.csv": 4, None: 4})
    fill_files(layers, "silver.things", {"a.csv": 4, None: 1})
    things_entity_row(layers)
    lines = demo.quarantine_summary_lines(layers, CONSERVATION, {})
    assert lines[1] == "    worst file of 2: (none)  75.000% (3 of 4 rows)"


def test_an_entity_with_no_rows_prints_n_a_and_no_worst_file_line(layers):
    for relation in ("bronze.things", "silver.things"):
        layers.execute(f"drop table {relation}")
    fill_files(layers, "bronze.things", {})
    fill_files(layers, "silver.things", {})
    things_entity_row(layers)
    lines = demo.quarantine_summary_lines(layers, CONSERVATION, levels_for("0.1", "0.9"))
    assert (
        lines[0] == f"  {'things':<14} quarantine rate n/a (0 of 0 rows)  warn 10%  error 90%  OK"
    )
    assert not any("worst file" in line for line in lines)


def test_an_entity_without_levels_prints_no_levels_and_no_status_on_the_worst_file(layers):
    things_entity_row(layers)
    lines = demo.quarantine_summary_lines(
        layers, CONSERVATION, levels_for("0.1", "0.9", "silver.x")
    )
    assert lines[0].endswith("(3 of 10 rows)  no levels")
    assert lines[1] == "    worst file of 2: b.csv  50.000% (2 of 4 rows)"


def test_an_entity_without_a_conservation_entry_keeps_the_summary_numbers(layers):
    add_summary_row(layers, "other", "E1", "error", 2, 50, 2, "0.04")
    things_entity_row(layers)
    lines = demo.quarantine_summary_lines(layers, CONSERVATION, levels_for("0.1", "0.9"))
    assert entity_line("4.000%", 2, 50, "  no levels", "other") in lines
    other = lines.index(entity_line("4.000%", 2, 50, "  no levels", "other"))
    assert not lines[other + 1].startswith("    worst file")


def test_entities_print_in_name_order_with_their_own_levels(layers):
    layers.execute("create table bronze.aaa as select 'f.csv' as _source_file from range(10)")
    layers.execute("create table silver.aaa as select 'f.csv' as _source_file from range(10)")
    layers.execute("create table silver.aaa_quarantine as select 1 as id where false")
    both = {**CONSERVATION, "aaa": ("bronze.aaa", "silver.aaa", "silver.aaa_quarantine")}
    add_summary_row(layers, "aaa", "E1", "error", 0, 10, 0, "0")
    things_entity_row(layers)
    levels = {**levels_for("0.1", "0.9"), **levels_for("0.0", "0.0", "silver.aaa")}
    lines = demo.quarantine_summary_lines(layers, both, levels)
    assert lines[0] == entity_line("0.000%", 0, 10, "  warn 0%  error 0%  OK", "aaa")
    assert lines[1] == "    worst file of 1: f.csv  0.000% (0 of 10 rows)  OK"
    assert any(line.startswith(f"  {'things':<14} quarantine rate 30.000%") for line in lines)


@pytest.mark.parametrize(
    ("level", "text"),
    [
        ("0.001", "0.1%"),
        ("0.01", "1%"),
        ("0.583333", "58.3333%"),
        ("0.5", "50%"),
        ("1", "100%"),
        ("0", "0%"),
        ("0.000001", "0.0001%"),
    ],
)
def test_format_level_prints_a_percentage_without_trailing_zeros(level, text):
    assert demo.format_level(Decimal(level)) == text


@pytest.mark.parametrize(
    ("rate", "levels", "word"),
    [
        (Decimal("0.5"), Levels(Decimal("0.5"), Decimal("0.9")), "OK"),
        (Decimal("0.500001"), Levels(Decimal("0.5"), Decimal("0.9")), "WARN"),
        (Decimal("0.9"), Levels(Decimal("0.5"), Decimal("0.9")), "WARN"),
        (Decimal("0.900001"), Levels(Decimal("0.5"), Decimal("0.9")), "ERROR"),
        (Decimal(0), Levels(Decimal(0), Decimal(0)), "OK"),
        (None, Levels(Decimal(0), Decimal(0)), "OK"),
        (Decimal("0.5"), None, "no levels"),
        (None, None, "no levels"),
    ],
)
def test_level_status_fires_only_strictly_above_a_level(rate, levels, word):
    assert demo.level_status(rate, levels) == word


def test_the_safe_divide_text_is_the_duckdb_rendering_of_the_macro():
    assert demo.SAFE_DIVIDE_SQL.format(numerator="a", denominator="b") == (
        "cast(cast(divide(2 * cast(cast(cast(a as decimal(18, 6)) as decimal(38, 6)) "
        "* 1000000 as hugeint) * 1000000 + sign(cast(cast(cast(a as decimal(18, 6)) "
        "as decimal(38, 6)) * 1000000 as hugeint)) * abs(cast(cast(cast(b as decimal(18, 6)) "
        "as decimal(38, 6)) * 1000000 as hugeint)), 2 * nullif(cast(cast(cast(b "
        "as decimal(18, 6)) as decimal(38, 6)) * 1000000 as hugeint), 0)) "
        "as decimal(18, 0)) * 0.000001 as decimal(18, 6))"
    )


@pytest.mark.parametrize(
    ("numerator", "denominator", "expected"),
    [
        ("2.01", "32", Decimal("0.062813")),
        ("-2.01", "32", Decimal("-0.062813")),
        ("2.01", "-32", Decimal("-0.062813")),
        ("1", "3", Decimal("0.333333")),
        ("2", "3", Decimal("0.666667")),
        ("-2", "3", Decimal("-0.666667")),
        ("3", "2", Decimal("1.500000")),
        ("0", "5", Decimal("0.000000")),
        ("3", "0", None),
    ],
)
def test_the_safe_divide_text_rounds_half_away_from_zero_on_duckdb(
    numerator, denominator, expected
):
    connection = duckdb.connect()
    value = connection.execute(
        "select " + demo.SAFE_DIVIDE_SQL.format(numerator=numerator, denominator=denominator)
    ).fetchone()[0]
    connection.close()
    assert value == expected


# Real row counts are millions (one month of the example is about 3 million rows), and the scaled
# millionths of a decimal(18, 6) value need up to 24 digits, so the scaling must not happen
# in a decimal(18, ...) product. The expected values are Spark's: the exact quotient rounded
# half away from zero to six decimals. The last rows are the largest magnitude decimal(18, 6)
# holds, as numerator and as denominator.
@pytest.mark.parametrize(
    ("numerator", "denominator", "expected"),
    [
        ("3000000", "7", Decimal("428571.428571")),
        ("12345678", "3", Decimal("4115226.000000")),
        ("12345678", "7", Decimal("1763668.285714")),
        ("-12345678", "7", Decimal("-1763668.285714")),
        ("12345678", "-7", Decimal("-1763668.285714")),
        ("7", "3000000", Decimal("0.000002")),
        ("3000000", "12345678", Decimal("0.243000")),
        ("12345678", "3000000", Decimal("4.115226")),
        ("2500000", "1000000", Decimal("2.500000")),
        ("1", "2000000", Decimal("0.000001")),
        ("-1", "2000000", Decimal("-0.000001")),
        ("12345678.000001", "2", Decimal("6172839.000001")),
        ("-12345678.000001", "2", Decimal("-6172839.000001")),
        ("12345678", "0", None),
        ("999999999999.999999", "1", Decimal("999999999999.999999")),
        ("-999999999999.999999", "1", Decimal("-999999999999.999999")),
        ("999999999999.999999", "2", Decimal("500000000000.000000")),
        ("-999999999999.999999", "2", Decimal("-500000000000.000000")),
        ("999999999999.999999", "3", Decimal("333333333333.333333")),
        ("999999999999.999999", "999999999999.999999", Decimal("1.000000")),
        ("1", "999999999999.999999", Decimal("0.000000")),
        ("500000", "999999999999.999999", Decimal("0.000001")),
    ],
)
def test_the_safe_divide_text_holds_real_row_counts_and_the_decimal_18_6_range_on_duckdb(
    numerator, denominator, expected
):
    connection = duckdb.connect()
    value = connection.execute(
        "select " + demo.SAFE_DIVIDE_SQL.format(numerator=numerator, denominator=denominator)
    ).fetchone()[0]
    connection.close()
    assert value == expected


def test_the_safe_divide_text_matches_the_macro_in_the_project():
    macro = (Path(demo.__file__).parents[2] / "packages/starter/macros/shim/misc.sql").read_text()
    found = re.search(
        r"macro duckdb__safe_divide\(numerator, denominator\) -%\}\n(.*?)\n\{%- endmacro",
        macro,
        re.DOTALL,
    )
    assert found is not None
    rendered = found.group(1).replace("{{ numerator }}", "{numerator}")
    assert rendered.replace("{{ denominator }}", "{denominator}") == demo.SAFE_DIVIDE_SQL


def test_scope_rates_divide_as_decimals_and_round_to_six_places():
    connection = duckdb.connect()
    connection.execute("create schema bronze")
    connection.execute("create schema silver")
    fill_files(connection, "bronze.t", {"a.csv": 12})
    fill_files(connection, "silver.t", {"a.csv": 5})
    entity, files = demo.scope_rates(connection, "bronze.t", "silver.t")
    connection.close()
    assert entity == demo.ScopeRate("(all files)", 7, 12, Decimal("0.583333"))
    assert files == [demo.ScopeRate("a.csv", 7, 12, Decimal("0.583333"))]


def test_scope_rates_count_a_file_missing_from_silver_as_all_rejected():
    connection = duckdb.connect()
    connection.execute("create schema bronze")
    connection.execute("create schema silver")
    fill_files(connection, "bronze.t", {"a.csv": 3, "b.csv": 2})
    fill_files(connection, "silver.t", {"a.csv": 3})
    entity, files = demo.scope_rates(connection, "bronze.t", "silver.t")
    connection.close()
    assert entity == demo.ScopeRate("(all files)", 2, 5, Decimal("0.400000"))
    assert files == [
        demo.ScopeRate("a.csv", 0, 3, Decimal(0)),
        demo.ScopeRate("b.csv", 2, 2, Decimal(1)),
    ]


def test_scope_rates_of_an_empty_entity_have_no_rate_and_no_files():
    connection = duckdb.connect()
    connection.execute("create schema bronze")
    connection.execute("create schema silver")
    fill_files(connection, "bronze.t", {})
    fill_files(connection, "silver.t", {})
    entity, files = demo.scope_rates(connection, "bronze.t", "silver.t")
    connection.close()
    assert entity == demo.ScopeRate("(all files)", 0, 0, None)
    assert files == []


def test_scope_rates_reject_a_bad_relation_before_any_query():
    with pytest.raises(ValueError, match="must match"):
        demo.scope_rates(QueryTrap(), "bronze.t; drop", "silver.t")
    with pytest.raises(ValueError, match="must match"):
        demo.scope_rates(QueryTrap(), "bronze.t", "t")


def test_read_levels_keys_each_levels_test_by_its_silver_relation(tmp_path):
    other = {
        "resource_type": "test",
        "attached_node": "model.pkg.things",
        "test_metadata": {"name": "not_null", "kwargs": {"column_name": "id"}},
    }
    manifest = manifest_with(
        ("silver", "things", 0.001, 0.01), ("silver", "zones", 0, 1), extra_tests=[other]
    )
    path = write_manifest(tmp_path, manifest)
    assert demo.read_levels(path) == {
        "silver.things": Levels(Decimal("0.001000"), Decimal("0.010000")),
        "silver.zones": Levels(Decimal("0.000000"), Decimal("1.000000")),
    }


def test_read_levels_quantizes_each_level_like_the_test_renders_it(tmp_path):
    path = write_manifest(tmp_path, manifest_with(("silver", "things", 0.00001, 0.1234567)))
    levels = demo.read_levels(path)["silver.things"]
    assert (levels.warn_above, levels.error_above) == (Decimal("0.000010"), Decimal("0.123457"))
    assert "E" not in str(levels.warn_above)


@pytest.mark.parametrize(("warn", "error"), [(True, 0.5), (0.1, "0.5"), (None, 0.5), (0.1, False)])
def test_read_levels_skips_a_boolean_or_text_level(tmp_path, warn, error):
    path = write_manifest(tmp_path, manifest_with(("silver", "things", warn, error)))
    assert demo.read_levels(path) == {}


@pytest.mark.parametrize("content", [None, "", "{not json", "[]", '{"nodes": []}', '{"nodes": 3}'])
def test_read_levels_gives_nothing_for_a_missing_or_unreadable_manifest(tmp_path, content):
    path = tmp_path / "manifest.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    assert demo.read_levels(path) == {}


def test_read_levels_skips_a_levels_test_whose_model_is_not_in_the_manifest(tmp_path):
    manifest = manifest_with(("silver", "things", 0.1, 0.5))
    del manifest["nodes"]["model.pkg.things"]
    assert demo.read_levels(write_manifest(tmp_path, manifest)) == {}


def test_dbt_target_dir_defaults_to_target_under_the_root_and_honours_the_env_var(
    tmp_path, monkeypatch
):
    assert demo.dbt_target_dir(tmp_path) == tmp_path / "target"
    monkeypatch.setenv("DBT_TARGET_PATH", "build/out")
    assert demo.dbt_target_dir(tmp_path) == tmp_path / "build/out"
    monkeypatch.setenv("DBT_TARGET_PATH", str(tmp_path / "elsewhere"))
    assert demo.dbt_target_dir(Path("/somewhere/else")) == tmp_path / "elsewhere"


def run_main_with_levels(layers, tmp_path, monkeypatch, levels):
    things_entity_row(layers)
    stub_demo(monkeypatch, tmp_path, layers)
    if levels is not None:
        write_manifest(tmp_path / "target", manifest_with(("silver", "things", *levels)))
    return demo.main([])


def test_main_prints_the_levels_it_reads_from_the_target_folder_and_exits_0(
    layers, tmp_path, monkeypatch, capsys
):
    assert run_main_with_levels(layers, tmp_path, monkeypatch, (0.5, 0.9)) == 0
    out = capsys.readouterr()
    assert entity_line("30.000%", 3, 10, "  warn 50%  error 90%  OK") in out.out.splitlines()
    assert "    worst file of 2: b.csv  50.000% (2 of 4 rows)  OK" in out.out.splitlines()
    assert demo.RATE_DISAGREEMENT_NOTE not in out.out
    assert out.err == ""


def test_main_without_a_manifest_prints_no_levels_and_still_exits_0(
    layers, tmp_path, monkeypatch, capsys
):
    assert run_main_with_levels(layers, tmp_path, monkeypatch, None) == 0
    out = capsys.readouterr()
    assert entity_line("30.000%", 3, 10, "  no levels") in out.out.splitlines()
    assert out.err == ""


def test_main_reads_the_manifest_from_the_target_path_env_var(
    layers, tmp_path, monkeypatch, capsys
):
    things_entity_row(layers)
    stub_demo(monkeypatch, tmp_path, layers)
    monkeypatch.setenv("DBT_TARGET_PATH", str(tmp_path / "custom"))
    write_manifest(tmp_path / "custom", manifest_with(("silver", "things", 0.5, 0.9)))
    assert demo.main([]) == 0
    assert "warn 50%  error 90%  OK" in capsys.readouterr().out


def test_main_reads_the_manifest_before_docs_generate_rewrites_the_target_folder(
    layers, tmp_path, monkeypatch, capsys
):
    things_entity_row(layers)
    dbt_calls = stub_demo(monkeypatch, tmp_path, layers)
    manifest_path = write_manifest(
        tmp_path / "target", manifest_with(("silver", "things", 0.5, 0.9))
    )

    def docs_rewrites(step, *args):
        dbt_calls.append((step, args))
        if step == "docs":
            manifest_path.unlink()
        return 0

    monkeypatch.setattr(demo, "_run_dbt", docs_rewrites)
    assert demo.main([]) == 0
    assert "warn 50%  error 90%  OK" in capsys.readouterr().out


def test_main_after_a_passed_build_with_an_error_rate_prints_the_note_and_exits_0(
    layers, tmp_path, monkeypatch, capsys
):
    assert run_main_with_levels(layers, tmp_path, monkeypatch, (0.1, 0.4)) == 0
    out = capsys.readouterr()
    lines = out.out.splitlines()
    assert "    worst file of 2: b.csv  50.000% (2 of 4 rows)  ERROR" in lines
    assert lines[-1] == demo.RATE_DISAGREEMENT_NOTE
    assert lines[-2] == FABRIC_LABEL
    assert out.err == ""


def test_main_with_a_warn_only_rate_prints_no_note_and_exits_0(
    layers, tmp_path, monkeypatch, capsys
):
    assert run_main_with_levels(layers, tmp_path, monkeypatch, (0.31, 0.9)) == 0
    out = capsys.readouterr()
    assert "  WARN" in out.out
    assert demo.RATE_DISAGREEMENT_NOTE not in out.out
    assert out.out.splitlines()[-1] == FABRIC_LABEL


def test_main_with_an_entity_error_rate_prints_the_note_too(layers, tmp_path, monkeypatch, capsys):
    assert run_main_with_levels(layers, tmp_path, monkeypatch, (0.1, 0.25)) == 0
    assert capsys.readouterr().out.splitlines()[-1] == demo.RATE_DISAGREEMENT_NOTE


def test_main_with_a_reconciliation_fail_still_writes_the_fail_message_and_exits_1(
    layers, tmp_path, monkeypatch, capsys
):
    layers.execute("delete from silver.things_quarantine where id = 0")
    assert run_main_with_levels(layers, tmp_path, monkeypatch, (0.5, 0.9)) == 1
    out = capsys.readouterr()
    assert demo.FAIL_MESSAGE in out.err
    assert demo.RATE_DISAGREEMENT_NOTE not in out.out


def test_the_note_says_dbt_s_result_stands():
    assert "past its error level" in demo.RATE_DISAGREEMENT_NOTE
    assert "dbt's levels tests passed" in demo.RATE_DISAGREEMENT_NOTE
    assert "dbt's result stands" in demo.RATE_DISAGREEMENT_NOTE


# --- a failed build (D-06) and a fresh start for every build (D-11) ---


def derived_database(path: Path) -> None:
    """A DuckDB file with one table in each of the landing and the derived schemas."""
    connection = duckdb.connect(str(path))
    for schema in ("landing", "bronze", "silver", "gold"):
        connection.execute(f"create schema {schema}")
        connection.execute(f"create table {schema}.t as select 1 as id")
    connection.close()


def table_names(path: Path) -> set[str]:
    connection = duckdb.connect(str(path), read_only=True)
    try:
        rows = connection.execute(
            "select table_schema || '.' || table_name from information_schema.tables"
        ).fetchall()
        return {row[0] for row in rows}
    finally:
        connection.close()


def test_the_derived_schemas_are_gold_silver_and_bronze_and_never_landing():
    assert demo.DERIVED_SCHEMAS == ("gold", "silver", "bronze")


def test_drop_derived_schemas_leaves_only_the_landing_table(tmp_path):
    database = tmp_path / "demo.duckdb"
    derived_database(database)
    demo.drop_derived_schemas(database)
    assert table_names(database) == {"landing.t"}


def test_drop_derived_schemas_does_not_create_a_missing_file(tmp_path):
    database = tmp_path / "absent.duckdb"
    demo.drop_derived_schemas(database)
    assert not database.exists()


def test_drop_derived_schemas_on_a_file_without_the_schemas_is_quiet(tmp_path):
    database = tmp_path / "demo.duckdb"
    connection = duckdb.connect(str(database))
    connection.execute("create schema landing")
    connection.execute("create table landing.t as select 1 as id")
    connection.close()
    demo.drop_derived_schemas(database)
    assert table_names(database) == {"landing.t"}


def test_main_drops_after_every_load_and_before_dbt_deps(layers, tmp_path, monkeypatch):
    calls = stub_demo(monkeypatch, tmp_path, layers)
    assert demo.main([]) == 0
    assert [name for name, _args in calls] == ["load", "drop", "deps", "build", "docs"]
    assert calls[1] == ("drop", (demo.duckdb_path(tmp_path),))


def test_a_duckdb_error_from_the_drop_exits_2_naming_the_file_and_runs_no_dbt(
    layers, tmp_path, monkeypatch, capsys
):
    calls = stub_demo(monkeypatch, tmp_path, layers)

    def failing(_database):
        raise duckdb.IOException("file is locked")

    monkeypatch.setattr(demo, "drop_derived_schemas", failing)
    assert demo.main([]) == 2
    err = capsys.readouterr().err
    assert f"demo could not prepare {demo.duckdb_path(tmp_path)}: " in err
    assert "file is locked" in err
    assert [name for name, _args in calls] == ["load"]


def write_run_results(folder: Path, *results: tuple[str, str, str | None]) -> Path:
    """Write a run results file. Each result is (unique_id, status, relation_name or None)."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "run_results.json"
    path.write_text(
        json.dumps(
            {
                "results": [
                    {"unique_id": uid, "status": status, "relation_name": relation}
                    for uid, status, relation in results
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def test_skipped_relations_keeps_only_skipped_results_that_name_a_relation(tmp_path):
    path = write_run_results(
        tmp_path,
        ("model.pkg.fct_trips", "skipped", '"db"."gold"."fct_trips"'),
        ("test.pkg.not_null_fct", "skipped", None),
        ("model.pkg.things", "success", '"db"."silver"."things"'),
        ("model.pkg.broken", "error", '"db"."gold"."broken"'),
    )
    assert demo.skipped_relations(path) == {"gold.fct_trips"}


def test_skipped_relations_of_a_seed_and_a_relation_without_a_database(tmp_path):
    path = write_run_results(
        tmp_path,
        ("seed.pkg.codes", "skipped", '"db"."gold"."codes"'),
        ("model.pkg.agg", "skipped", '"gold"."agg"'),
    )
    assert demo.skipped_relations(path) == {"gold.codes", "gold.agg"}


@pytest.mark.parametrize(
    "content", [None, "", "{not json", "[]", '{"results": 3}', '{"results": [3]}']
)
def test_skipped_relations_gives_an_empty_set_for_a_missing_or_unreadable_file(tmp_path, content):
    path = tmp_path / "run_results.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    assert demo.skipped_relations(path) == set()


def test_report_lines_print_skipped_for_a_skipped_relation_even_when_its_table_exists(layers):
    lines = report_lines(
        layers,
        [("silver", "silver.things"), ("gold", "gold.fct")],
        skipped=frozenset({"gold.fct"}),
    )
    assert lines[1].split() == ["silver", "silver.things", "7"]
    assert lines[2].split() == ["gold", "gold.fct", "skipped"]
    assert lines[2].endswith(f"{'skipped':>9}")


def test_report_lines_print_not_built_for_a_missing_relation(layers):
    lines = report_lines(layers, [("gold", "gold.absent"), ("silver", "silver.things")])
    assert lines[1].split() == ["gold", "gold.absent", "not", "built"]
    assert lines[1].endswith(f"{'not built':>9}")
    assert lines[2].split() == ["silver", "silver.things", "7"]


def test_the_warnings_line_says_skipped_or_not_built_too(con):
    lines = report_lines(
        con,
        [],
        ["silver.parts", "silver.absent", "gold.fct"],
        skipped=frozenset({"gold.fct"}),
    )
    assert lines[1].split() == ["warnings", "silver.parts", "2"]
    assert lines[2].endswith(f"{'not built':>9}")
    assert lines[3].endswith(f"{'skipped':>9}")


def test_a_relation_name_is_still_checked_before_any_query_when_skipped_is_given():
    with pytest.raises(ValueError, match="must match"):
        report_lines(QueryTrap(), [("silver", "silver.parts; drop")], skipped=frozenset())


def test_a_conservation_line_whose_relation_is_skipped_says_so_and_is_not_ok(layers):
    lines, ok = demo.conservation_lines(
        layers, CONSERVATION, skipped=frozenset({"silver.things_quarantine"})
    )
    assert lines == [f"  {'things':<14} not checked: silver.things_quarantine skipped"]
    assert ok is False


def test_a_conservation_line_whose_relation_is_missing_says_not_built_and_is_not_ok(layers):
    layers.execute("drop table silver.things_quarantine")
    lines, ok = demo.conservation_lines(layers, CONSERVATION)
    assert lines == [f"  {'things':<14} not checked: silver.things_quarantine not built"]
    assert ok is False


def test_a_gold_line_whose_relation_is_skipped_or_missing_says_so_and_is_not_ok(layers):
    lines, ok = demo.gold_lines(layers, GOLD, skipped=frozenset({"gold.fct"}))
    assert lines == [f"  {'gold':<14} not checked: gold.fct skipped"]
    assert ok is False
    layers.execute("drop table gold.agg")
    lines, ok = demo.gold_lines(layers, GOLD)
    assert lines == [f"  {'gold':<14} not checked: gold.agg not built"]
    assert ok is False


def test_the_summary_prints_not_built_when_its_relation_is_missing(layers):
    layers.execute("drop table silver.dq_quarantine_summary")
    assert demo.quarantine_summary_lines(layers, CONSERVATION, {}) == ["  not built"]


def test_an_entity_whose_silver_relation_is_missing_or_skipped_prints_rate_not_built(layers):
    things_entity_row(layers)
    skipped = demo.quarantine_summary_lines(
        layers, CONSERVATION, levels_for("0.1", "0.4"), skipped=frozenset({"silver.things"})
    )
    assert skipped[0] == f"  {'things':<14} quarantine rate not built"
    assert not any("worst file" in line for line in skipped)
    layers.execute("drop table silver.things")
    missing = demo.quarantine_summary_lines(layers, CONSERVATION, levels_for("0.1", "0.4"))
    assert missing[0] == f"  {'things':<14} quarantine rate not built"


def closing_with_skips(connection, tmp_path, **kwargs):
    return demo.closing_lines(
        connection,
        tmp_path,
        [("silver", "silver.things"), ("gold", "gold.fct")],
        [],
        CONSERVATION,
        GOLD,
        None,
        **kwargs,
    )


def test_closing_lines_list_the_skipped_relations_in_order_after_the_quarantine_summary(
    layers, tmp_path
):
    things_entity_row(layers)
    lines, ok = closing_with_skips(
        layers, tmp_path, skipped=frozenset({"gold.fct", "gold.agg"}), docs_built=False
    )
    assert ok is False
    start = lines.index("skipped")
    assert lines[start - 1] == ""
    assert lines[start + 1 : start + 3] == ["  gold.agg", "  gold.fct"]
    assert lines[start + 3] == ""
    assert lines.index("quarantine summary") < start < lines.index("docs")
    assert any(line.split()[:3] == ["gold", "gold.fct", "skipped"] for line in lines)


def test_closing_lines_have_no_skipped_section_when_nothing_was_skipped(layers, tmp_path):
    things_entity_row(layers)
    lines, _ok = closing_with_skips(layers, tmp_path)
    assert "skipped" not in lines


def test_closing_lines_say_the_docs_were_not_generated_after_a_failed_build(layers, tmp_path):
    things_entity_row(layers)
    lines, _ok = closing_with_skips(layers, tmp_path, docs_built=False)
    start = lines.index("docs")
    assert lines[start : start + 2] == ["docs", "  not generated: dbt build failed"]
    assert lines[start + 2 :] == ["", FABRIC_LABEL]
    assert not any("static_index" in line or "dbt docs serve" in line for line in lines)


def failed_build_run(layers, tmp_path, monkeypatch, levels=(0.1, 0.4), code=1, argv=()):
    """Run `main` with a build that exits `code`, a run results file with two skipped gold models."""
    things_entity_row(layers)
    calls = stub_demo(monkeypatch, tmp_path, layers, codes={"build": code})
    write_manifest(tmp_path / "target", manifest_with(("silver", "things", *levels)))
    write_run_results(
        tmp_path / "target",
        ("model.pkg.fct", "skipped", '"db"."gold"."fct"'),
        ("model.pkg.agg", "skipped", '"db"."gold"."agg"'),
        ("test.pkg.levels", "error", None),
    )
    return calls, demo.main(list(argv))


def test_a_failed_build_still_prints_the_summary_skips_the_docs_and_returns_dbts_code(
    layers, tmp_path, monkeypatch, capsys
):
    calls, code = failed_build_run(layers, tmp_path, monkeypatch)
    out = capsys.readouterr()
    assert code == 1
    assert [name for name, _args in calls] == ["load", "drop", "deps", "build"]
    lines = out.out.splitlines()
    assert f"  {'gold':<14} not checked: gold.fct skipped" in lines
    start = lines.index("skipped")
    assert lines[start + 1 : start + 3] == ["  gold.agg", "  gold.fct"]
    assert "  not generated: dbt build failed" in lines
    assert "worst file of 2: b.csv  50.000% (2 of 4 rows)  ERROR" in out.out
    assert lines[-1] == FABRIC_LABEL
    assert out.err.splitlines() == ["demo failed at: dbt build (exit 1)"]


def test_a_failed_build_prints_no_rate_disagreement_note_even_with_an_error_rate_line(
    layers, tmp_path, monkeypatch, capsys
):
    failed_build_run(layers, tmp_path, monkeypatch, levels=(0.1, 0.25))
    out = capsys.readouterr().out
    assert "  ERROR" in out
    assert demo.RATE_DISAGREEMENT_NOTE not in out


def test_the_exit_code_after_a_failed_build_is_dbts_whatever_the_rate_words_say(
    layers, tmp_path, monkeypatch, capsys
):
    _calls, code = failed_build_run(layers, tmp_path, monkeypatch, levels=(0.9, 0.95), code=3)
    out = capsys.readouterr()
    assert "  ERROR" not in out.out
    assert code == 3
    assert "demo failed at: dbt build (exit 3)" in out.err


def test_a_failed_build_without_a_run_results_file_still_prints_the_summary(
    layers, tmp_path, monkeypatch, capsys
):
    things_entity_row(layers)
    stub_demo(monkeypatch, tmp_path, layers, codes={"build": 1})
    assert demo.main([]) == 1
    out = capsys.readouterr()
    assert "skipped" not in out.out.splitlines()
    assert "  not generated: dbt build failed" in out.out
    assert "demo failed at: dbt build (exit 1)" in out.err


def test_a_dbt_deps_failure_still_stops_at_once_with_no_summary(
    layers, tmp_path, monkeypatch, capsys
):
    calls = stub_demo(monkeypatch, tmp_path, layers, codes={"deps": 2})
    assert demo.main([]) == 2
    out = capsys.readouterr()
    assert out.err.splitlines() == ["demo failed at: dbt deps (exit 2)"]
    assert "layer" not in out.out
    assert [name for name, _args in calls] == ["load", "drop", "deps"]


def test_a_docs_failure_after_a_passed_build_stops_at_once_with_its_code(
    layers, tmp_path, monkeypatch, capsys
):
    stub_demo(monkeypatch, tmp_path, layers, codes={"docs": 4})
    assert demo.main([]) == 4
    out = capsys.readouterr()
    assert "demo failed at: dbt docs generate (exit 4)" in out.err
    assert "layer" not in out.out


# --- `--summary-file`: the closing summary, or the reason the demo stopped, for a job summary (D-16) ---

SUMMARY_HEADING = "### demo closing summary"


def summary_block(path: Path) -> list[str]:
    """Return the lines inside the last fenced `text` block of a summary file, fence lines left out."""
    lines = path.read_text(encoding="utf-8").splitlines()
    opening = max(i for i, line in enumerate(lines) if line == "```text")
    closing = lines.index("```", opening + 1)
    return lines[opening + 1 : closing]


def test_a_green_run_appends_a_heading_a_text_fence_the_summary_and_the_exit_status(
    layers, tmp_path, monkeypatch, capsys
):
    stub_demo(monkeypatch, tmp_path, layers)
    target = tmp_path / "out.md"
    assert not target.exists()
    assert demo.main(["--summary-file", str(target)]) == 0
    out = capsys.readouterr().out.splitlines()
    text = target.read_text(encoding="utf-8").splitlines()
    assert text[:3] == [SUMMARY_HEADING, "", "```text"]
    assert text[-2:] == ["```", ""]
    block = summary_block(target)
    assert block[-1] == "demo exit status 0"
    # stdout is the landed line, a blank line, then the summary, exactly as without the flag
    assert out[0].startswith("landed ")
    assert out[1] == ""
    assert block[:-1] == out[2:]
    assert block[-2] == FABRIC_LABEL


def test_the_summary_file_flag_changes_nothing_on_stdout_or_stderr(
    layers, tmp_path, monkeypatch, capsys
):
    stub_demo(monkeypatch, tmp_path, layers)
    assert demo.main(["--summary-file", str(tmp_path / "out.md")]) == 0
    assert capsys.readouterr().err == ""


def test_an_existing_summary_file_is_appended_to_never_truncated(layers, tmp_path, monkeypatch):
    stub_demo(monkeypatch, tmp_path, layers)
    target = tmp_path / "out.md"
    target.write_text("earlier step\n", encoding="utf-8")
    assert demo.main(["--summary-file", str(target)]) == 0
    text = target.read_text(encoding="utf-8")
    assert text.startswith("earlier step\n" + SUMMARY_HEADING + "\n")
    assert text.count(SUMMARY_HEADING) == 1


def test_a_failed_build_writes_the_summary_and_its_exit_status(
    layers, tmp_path, monkeypatch, capsys
):
    target = tmp_path / "out.md"
    _calls, code = failed_build_run(
        layers, tmp_path, monkeypatch, argv=["--summary-file", str(target)]
    )
    assert code == 1
    out = capsys.readouterr().out.splitlines()
    block = summary_block(target)
    assert block[-1] == "demo exit status 1"
    assert block[:-1] == out[out.index("") + 1 :]
    assert "  not generated: dbt build failed" in block


def test_a_load_error_writes_the_message_and_exit_status_2(layers, tmp_path, monkeypatch, capsys):
    from starter_cli.landing import DownloadError

    stub_demo(monkeypatch, tmp_path, layers)

    def failing(*_args, **_kwargs):
        raise DownloadError("Could not download it. See README.md#troubleshooting.")

    monkeypatch.setattr(demo, "load_entity", failing)
    target = tmp_path / "out.md"
    assert demo.main(["--summary-file", str(target)]) == 2
    assert summary_block(target) == [
        "Could not download it. See README.md#troubleshooting.",
        "demo exit status 2",
    ]
    assert "README.md#troubleshooting" in capsys.readouterr().err


def test_a_failed_schema_drop_writes_the_message_and_exit_status_2(layers, tmp_path, monkeypatch):
    stub_demo(monkeypatch, tmp_path, layers)

    def failing(_database):
        raise duckdb.IOException("file is locked")

    monkeypatch.setattr(demo, "drop_derived_schemas", failing)
    target = tmp_path / "out.md"
    assert demo.main(["--summary-file", str(target)]) == 2
    block = summary_block(target)
    assert block[0].startswith(f"demo could not prepare {demo.duckdb_path(tmp_path)}: ")
    assert block[-1] == "demo exit status 2"


def test_a_dbt_deps_failure_writes_the_stop_line_and_its_exit_status(layers, tmp_path, monkeypatch):
    stub_demo(monkeypatch, tmp_path, layers, codes={"deps": 2})
    target = tmp_path / "out.md"
    assert demo.main(["--summary-file", str(target)]) == 2
    assert summary_block(target) == ["demo failed at: dbt deps (exit 2)", "demo exit status 2"]


def test_a_docs_failure_writes_the_stop_line_and_its_exit_status(layers, tmp_path, monkeypatch):
    stub_demo(monkeypatch, tmp_path, layers, codes={"docs": 4})
    target = tmp_path / "out.md"
    assert demo.main(["--summary-file", str(target)]) == 4
    assert summary_block(target) == [
        "demo failed at: dbt docs generate (exit 4)",
        "demo exit status 4",
    ]


def test_a_reconciliation_fail_writes_the_summary_with_exit_status_1(layers, tmp_path, monkeypatch):
    layers.execute("delete from gold.agg")
    stub_demo(monkeypatch, tmp_path, layers)
    target = tmp_path / "out.md"
    assert demo.main(["--summary-file", str(target)]) == 1
    block = summary_block(target)
    assert any(line.endswith("FAIL") for line in block)
    assert block[-1] == "demo exit status 1"


def test_a_summary_file_that_cannot_be_written_warns_once_and_changes_no_exit_status(
    layers, tmp_path, monkeypatch, capsys
):
    stub_demo(monkeypatch, tmp_path, layers)
    target = tmp_path / "missing" / "out.md"
    assert demo.main(["--summary-file", str(target)]) == 0
    err = capsys.readouterr().err.splitlines()
    assert len(err) == 1
    assert err[0].startswith(f"demo: could not write the summary file {target}: ")
    assert not target.parent.exists()


def test_an_unwritable_summary_file_leaves_a_failed_build_s_exit_status_alone(
    layers, tmp_path, monkeypatch, capsys
):
    target = tmp_path / "missing" / "out.md"
    _calls, code = failed_build_run(
        layers, tmp_path, monkeypatch, code=3, argv=["--summary-file", str(target)]
    )
    assert code == 3
    err = capsys.readouterr().err
    assert err.count("could not write the summary file") == 1


def test_a_relative_summary_file_is_resolved_before_the_working_directory_changes(
    layers, tmp_path, monkeypatch
):
    here = tmp_path / "here"
    here.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    stub_demo(monkeypatch, root, layers)
    monkeypatch.chdir(here)
    assert demo.main(["--summary-file", "out.md"]) == 0
    assert (here / "out.md").is_file()
    assert not (root / "out.md").exists()


def test_the_help_text_lists_the_summary_file_flag(capsys):
    with pytest.raises(SystemExit) as excinfo:
        demo.main(["--help"])
    assert excinfo.value.code == 0
    assert "--summary-file FILE" in capsys.readouterr().out

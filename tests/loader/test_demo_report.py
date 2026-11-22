"""The demo's printer and flags without dbt, a download or the example data."""

from __future__ import annotations

import subprocess
import types
from pathlib import Path

import duckdb
import pytest

from starter_cli import demo
from starter_cli.demo import FABRIC_LABEL, report_lines

GITHUB_REMOTE = "https://github.com/Owner/repo.git"
CONSERVATION = {"things": ("bronze.things", "silver.things", "silver.things_quarantine")}
GOLD = [("gold.fct", "gold.agg", "trip_count")]


@pytest.fixture(autouse=True)
def restore_working_directory(monkeypatch, tmp_path):
    """`demo.main` changes into the repo root; monkeypatch puts the working directory back."""
    monkeypatch.chdir(tmp_path)


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


@pytest.fixture
def layers():
    """Bronze 10 = silver 7 + quarantine 3, and a gold fact of 5 rows that the aggregate matches."""
    connection = duckdb.connect()
    for schema in ("bronze", "silver", "gold"):
        connection.execute(f"create schema {schema}")
    fill(connection, "bronze.things", 10)
    fill(connection, "silver.things", 7)
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
    lines = demo.quarantine_summary_lines(layers)
    rate_lines = [line for line in lines if "quarantine rate" in line]
    assert rate_lines == [f"  {'parts':<14} quarantine rate 0.003% (3 of 100,000 rows)"]


def test_top_codes_are_capped_ordered_and_skip_codes_that_did_not_fire(layers):
    for code, failed in [("E1", 7), ("E2", 9), ("E3", 7), ("E4", 1), ("E5", 8), ("E6", 3)]:
        add_summary_row(layers, "parts", code, "error", failed, 50, 5, "0.1")
    add_summary_row(layers, "parts", "E7", "error", 0, 50, 5, "0.1")
    for code, failed in [("W1", 4), ("W2", 6), ("W3", 5), ("W4", 2), ("W5", 0)]:
        add_summary_row(layers, "parts", code, "warn", failed, 50, 5, "0.1")
    lines = demo.quarantine_summary_lines(layers)
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
    lines = demo.quarantine_summary_lines(layers)
    assert f"    {'error':<6} {'RULE_A':<40} {1234:>12,}" in lines


def test_a_severity_with_no_fired_code_prints_none_and_entities_stay_apart(layers):
    add_summary_row(layers, "parts", "E1", "error", 2, 50, 2, "0.04")
    add_summary_row(layers, "parts", "W1", "warn", 0, 50, 2, "0.04")
    add_summary_row(layers, "zones", "E9", "error", 0, 20, 0, "0")
    lines = demo.quarantine_summary_lines(layers)
    parts = lines.index(f"  {'parts':<14} quarantine rate 4.000% (2 of 50 rows)")
    zones = lines.index(f"  {'zones':<14} quarantine rate 0.000% (0 of 20 rows)")
    assert parts < zones
    assert lines[parts + 1].split() == ["error", "E1", "2"]
    assert lines[parts + 2] == f"    {'warn':<6} none"
    assert lines[zones + 1] == f"    {'error':<6} none"
    assert lines[zones + 2] == f"    {'warn':<6} none"


def test_an_empty_summary_prints_none_registered(layers):
    assert demo.quarantine_summary_lines(layers) == ["  none registered"]


def test_a_bad_summary_relation_is_rejected_before_any_query():
    with pytest.raises(ValueError, match="must match"):
        demo.quarantine_summary_lines(QueryTrap(), "silver.summary; drop")


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
        "  published      https://owner.github.io/repo/dbt-docs/",
    ]
    assert Path(lines[1].split()[-1]).is_absolute()


def test_docs_lines_omit_the_published_line_without_a_url(tmp_path):
    lines = demo.docs_lines(tmp_path, None)
    assert len(lines) == 3
    assert not any("published" in line for line in lines)


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


def closing(connection, tmp_path, url=None):
    return demo.closing_lines(
        connection,
        tmp_path,
        [("silver", "silver.things")],
        [],
        CONSERVATION,
        GOLD,
        url,
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
    monkeypatch.setattr(demo, "_run_dbt", lambda *_a: 1)  # stop right after the loads
    folder = tmp_path / "files"
    folder.mkdir()
    demo.main(["--months", "2024-01..2024-02", "--from-dir", str(folder), "--allow-large"])
    assert seen
    for spec, kwargs in seen:
        assert kwargs["from_dir"] == folder
        assert kwargs["allow_large"] is True
        assert kwargs["months"] == (("2024-01", "2024-02") if spec.is_monthly else None)


def stub_demo(monkeypatch, tmp_path, connection, module=None):
    """Replace everything `main` does outside the printer; return the recorded dbt calls."""
    dbt_calls = []
    source = module or types.SimpleNamespace(
        ENTITIES={"things": types.SimpleNamespace(is_monthly=False, table="things")},
        REPORT_RELATIONS=[("silver", "silver.things")],
        REPORT_WARNINGS=[],
        REPORT_CONSERVATION=CONSERVATION,
        REPORT_GOLD=GOLD,
    )
    monkeypatch.setattr(demo, "discover_sources", lambda: {"src": source})
    monkeypatch.setattr(demo, "load_entity", lambda *_a, **_k: 1)
    monkeypatch.setattr(demo, "_run_dbt", lambda step, *args: dbt_calls.append((step, args)) or 0)
    monkeypatch.setattr(demo, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(demo, "git_remote_url", lambda _root: GITHUB_REMOTE)
    monkeypatch.setattr(demo.duckdb, "connect", lambda *_a, **_k: connection)
    return dbt_calls


def test_main_exits_0_and_prints_every_section_when_all_lines_say_ok(
    layers, tmp_path, monkeypatch, capsys
):
    dbt_calls = stub_demo(monkeypatch, tmp_path, layers)
    assert demo.main([]) == 0
    out = capsys.readouterr()
    lines = out.out.splitlines()
    assert lines[-1] == FABRIC_LABEL
    assert "https://owner.github.io/repo/dbt-docs/" in out.out
    assert "FAIL" not in out.out
    assert out.err == ""
    assert ("docs", ("generate", "--static")) in dbt_calls


def test_main_exits_1_with_a_message_when_a_reconciliation_line_says_fail(
    layers, tmp_path, monkeypatch, capsys
):
    layers.execute("delete from silver.things where id = 0")
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


def test_main_omits_the_published_line_when_there_is_no_usable_remote(
    layers, tmp_path, monkeypatch, capsys
):
    stub_demo(monkeypatch, tmp_path, layers)
    monkeypatch.setattr(demo, "git_remote_url", lambda _root: None)
    assert demo.main([]) == 0
    out = capsys.readouterr().out
    assert "  published      " not in out
    assert "uv run dbt docs serve" in out

"""`demo --target fabric`: the self-hosted Fabric run mode in one command (PORT-07, D-23, D-24).

Nothing here reaches Fabric, Livy or OneLake. The Lakehouse sink, `fabric_query.run_queries` and the
dbt runner are replaced by recorders, and one list of events keeps their order, so the tests hold
the contract of the command: the extra and the FABRIC_* variables are checked before anything is
landed, dropped or built; the call order is land, `dbt deps`, (drop), `dbt build`, summary; `--clean`
drops exactly gold, silver and bronze and only after `dbt deps` succeeded, and never `landing`; the
build leaves out the declared known limits and their descendants; and without `--months` the
monthly entities land only 2024-01, the one month verified on Fabric (D-22).
"""

from __future__ import annotations

import os
import types
from decimal import Decimal
from pathlib import Path
from typing import ClassVar

import duckdb
import pytest

from starter_cli import build_fabric, demo, fabric_query, fabric_sink

WORKSPACE_ID = "aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb"
LAKEHOUSE_ID = "cccccccc-4444-5555-6666-dddddddddddd"
LAKEHOUSE = "lake_secret_name"
FABRIC_ENV = {
    "FABRIC_WORKSPACE_ID": WORKSPACE_ID,
    "FABRIC_LAKEHOUSE_ID": LAKEHOUSE_ID,
    "FABRIC_LAKEHOUSE": LAKEHOUSE,
}


# Placeholder names in the shape the Lakehouse lists: a hyphenated catalog and a lakehouse.
CATALOG = "placeholder-catalog"
LAKE = "placeholder_lake"

CONSERVATION = {"parts": ("bronze.acme__parts", "silver.parts", "silver.parts_quarantine")}
GOLD = [("gold.fct_parts", "gold.agg_parts", "trip_count")]


class FakeLakehouse:
    """Answers the SQL the demo sends through `run_queries`, and notes every call in `events`.

    The Lakehouse is an in-memory DuckDB with the demo's layers, so the summary's own SQL runs for
    real and every answer comes back as text, as `query_json` returns it. `show schemas` and
    `show tables in` are answered from DuckDB's catalog, and `show tables in` a schema that does
    not exist fails, as it does on Spark. Bronze 30 = silver 27 + quarantine 3 over a.csv (20 = 19
    + 1) and b.csv (10 = 8 + 2); the gold fact of 27 rows matches its aggregate.
    """

    LANDING = (
        "create table landing.acme__parts as select range as id from range(30)",
        "create table landing.acme__zones as select range as id from range(5)",
    )
    DERIVED: ClassVar[dict[str, tuple[str, ...]]] = {
        "bronze": (
            (
                "create table bronze.acme__parts as select range as id, "
                "case when range < 20 then 'a.csv' else 'b.csv' end as _source_file from range(30)"
            ),
            "create table bronze.acme__zones as select range as id from range(5)",
        ),
        "silver": (
            (
                "create table silver.parts as select id, _source_file, "
                "case when id < 4 then 1 else 0 end as dq_warning_count "
                "from bronze.acme__parts where id not in (0, 20, 21)"
            ),
            (
                "create table silver.parts_quarantine as select id from bronze.acme__parts "
                "where id in (0, 20, 21)"
            ),
            "create table silver.zones as select id from bronze.acme__zones",
            (
                "create table silver.dq_quarantine_summary ("
                "entity varchar, rule_code varchar, severity varchar, failed_rows bigint, "
                "failed_rate decimal(18, 6), entity_rows bigint, entity_quarantined_rows bigint, "
                "entity_quarantine_rate decimal(18, 6), entity_warned_rows bigint, "
                "entity_warned_rate decimal(18, 6))"
            ),
            (
                "insert into silver.dq_quarantine_summary values "
                "('parts', 'missing_id', 'error', 3, 0.1, 30, 3, 0.1, 3, 0.1), "
                "('parts', 'odd_zone', 'warn', 2, 0.066667, 30, 3, 0.1, 3, 0.1)"
            ),
        ),
        "gold": (
            "create table gold.fct_parts as select id from silver.parts",
            "create table gold.agg_parts as select 1 as day_key, cast(27 as bigint) as trip_count",
        ),
    }

    def __init__(self, events: list[tuple]) -> None:
        self.events = events
        self.con = duckdb.connect()
        self.con.execute("create schema landing")
        for statement in self.LANDING:
            self.con.execute(statement)
        self.build()
        self.rebuilds = True
        self.fail_with: Exception | None = None
        self.envs: list[dict[str, str]] = []

    def build(self) -> None:
        """What `dbt build` does: create every derived schema that is missing, with its tables."""
        for schema, statements in self.DERIVED.items():
            if schema in self.schemas():
                continue
            self.con.execute(f"create schema {schema}")
            for statement in statements:
                self.con.execute(statement)

    def run_queries(self, queries, *, target="fabric", env=None):
        assert target == "fabric"
        self.envs.append(dict(env or {}))
        if self.fail_with is not None:
            raise self.fail_with
        sqls = list(queries.values())
        if sqls and all(sql.startswith("drop schema") for sql in sqls):
            names = sorted(sql.split()[4] for sql in sqls)
            self.events.append(("drop", tuple(names)))
        else:
            self.events.append(("query", tuple(sqls)))
        return {name: self.answer(sql) for name, sql in queries.items()}

    def schemas(self) -> list[str]:
        rows = self.con.execute(
            "select schema_name from information_schema.schemata "
            "where schema_name not in ('main', 'information_schema', 'pg_catalog') "
            "order by schema_name"
        ).fetchall()
        return [row[0] for row in rows]

    def answer(self, sql: str) -> list[list[str | None]]:
        if sql == "show schemas":
            # The live layout: `<catalog>.<lakehouse>.<schema>`, as the Lakehouse lists them.
            return [[f"{CATALOG}.{LAKE}.{schema}"] for schema in self.schemas()]
        if sql.startswith("show tables in "):
            schema = sql.removeprefix("show tables in ")
            if schema not in self.schemas():
                raise AssertionError(f"show tables in {schema}: the schema does not exist")
            rows = self.con.execute(
                "select table_name from information_schema.tables "
                f"where table_schema = '{schema}' order by table_name"
            ).fetchall()
            return [[f"`{CATALOG}`.{LAKE}.{schema}", row[0], "False"] for row in rows]
        try:
            rows = self.con.execute(sql).fetchall()
        except duckdb.Error:
            # A missing relation fails the whole dbt call, as it does on Spark.
            raise fabric_query.FabricQueryError(
                "dbt run-operation failed with exit code 1"
            ) from None
        return [[None if value is None else str(value) for value in row] for row in rows]


@pytest.fixture
def lakehouse(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """A Fabric demo with every outside call replaced. Returns an object holding the recorders."""
    for key in [key for key in os.environ if key.startswith("FABRIC_")]:
        monkeypatch.delenv(key)
    monkeypatch.delenv("DBT_TARGET_PATH", raising=False)
    monkeypatch.chdir(tmp_path)  # `_run_fabric` changes into the repo root; this restores it
    for key, value in FABRIC_ENV.items():
        monkeypatch.setenv(key, value)

    events: list[tuple] = []
    fake = FakeLakehouse(events)
    loads: list[dict] = []
    commands: list[list[str]] = []
    dbt_envs: list[dict[str, str]] = []
    dbt_codes: dict[str, int] = {}

    source = types.SimpleNamespace(
        ENTITIES={
            "parts": types.SimpleNamespace(is_monthly=True, table="acme__parts"),
            "zones": types.SimpleNamespace(is_monthly=False, table="acme__zones"),
        },
        REPORT_RELATIONS=[
            ("landing", "landing.acme__parts"),
            ("bronze", "bronze.acme__parts"),
            ("silver", "silver.parts"),
            ("quarantine", "silver.parts_quarantine"),
            ("gold", "gold.fct_parts"),
            ("landing", "landing.acme__zones"),
        ],
        REPORT_WARNINGS=["silver.parts"],
        REPORT_CONSERVATION=CONSERVATION,
        REPORT_GOLD=GOLD,
    )

    def load_entity(spec, root, **kwargs):
        loads.append({"table": spec.table, **kwargs})
        events.append(("load", spec.table))
        return 3

    def runner(command, root, env):
        commands.append(command)
        dbt_envs.append(dict(env))
        step = command[command.index("dbt.cli.main") + 1]
        events.append(("dbt", step))
        if step == "build" and fake.rebuilds:
            fake.build()
        return dbt_codes.get(step, 0)

    monkeypatch.setattr(fabric_sink, "sink_installed", lambda: True)
    monkeypatch.setattr(build_fabric, "fabric_adapter_installed", lambda: True)
    monkeypatch.setattr(build_fabric, "RUNNER", runner)
    monkeypatch.setattr(demo, "discover_sources", lambda: {"acme": source})
    monkeypatch.setattr(demo, "load_entity", load_entity)
    monkeypatch.setattr(demo, "run_queries", fake.run_queries)
    waits: list[tuple[int, str]] = []
    monkeypatch.setattr(
        demo,
        "AWAIT_CAPACITY",
        lambda env: waits.append((len(events), env["FABRIC_WORKSPACE_ID"])),
    )
    monkeypatch.setattr(demo, "repo_root", lambda: tmp_path)
    return types.SimpleNamespace(
        events=events,
        waits=waits,
        fake=fake,
        loads=loads,
        commands=commands,
        dbt_envs=dbt_envs,
        dbt_codes=dbt_codes,
        source=source,
        root=tmp_path,
    )


def kinds(events: list[tuple]) -> list[str]:
    """The order of the demo's steps: a load, a dbt step, a drop or a summary query batch."""
    out: list[str] = []
    for event in events:
        label = f"dbt {event[1]}" if event[0] == "dbt" else event[0]
        if label == "query" and out and out[-1] == "query":
            continue
        out.append(label)
    return out


# --- the checks before anything runs (edge probe PORT-07, empty) ---


def test_missing_variables_exit_2_naming_each_one_and_nothing_runs(lakehouse, monkeypatch, capsys):
    for key in FABRIC_ENV:
        monkeypatch.delenv(key)
    assert demo.main(["--target", "fabric", "--clean"]) == 2
    err = capsys.readouterr().err
    for key in FABRIC_ENV:
        assert f"demo: {key} is not set" in err
    assert lakehouse.events == []
    assert lakehouse.loads == []


def test_a_malformed_id_exits_2_naming_the_variable_and_never_its_value(
    lakehouse, monkeypatch, capsys
):
    monkeypatch.setenv("FABRIC_WORKSPACE_ID", "secret-but-not-a-uuid")
    assert demo.main(["--target", "fabric"]) == 2
    out = capsys.readouterr()
    assert "demo: FABRIC_WORKSPACE_ID is not a UUID" in out.err
    assert "secret-but-not-a-uuid" not in out.out + out.err
    assert lakehouse.events == []


def test_a_missing_extra_exits_2_with_the_command_before_anything_runs(
    lakehouse, monkeypatch, capsys
):
    monkeypatch.setattr(fabric_sink, "sink_installed", lambda: False)
    assert demo.main(["--target", "fabric"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("demo: ")
    assert demo.FABRIC_DEMO_COMMAND in err
    assert lakehouse.events == []


def test_the_variables_are_read_after_stripping_the_padding(lakehouse, monkeypatch):
    monkeypatch.setenv("FABRIC_WORKSPACE_ID", f"  {WORKSPACE_ID}  ")
    assert demo.main(["--target", "fabric"]) == 0
    assert lakehouse.dbt_envs[0]["FABRIC_WORKSPACE_ID"] == WORKSPACE_ID
    assert lakehouse.fake.envs[0]["FABRIC_WORKSPACE_ID"] == WORKSPACE_ID


# --- the order of the steps and --clean (D-24, T-05-44) ---


def test_the_calls_run_in_the_order_land_deps_build_summary(lakehouse, capsys):
    assert demo.main(["--target", "fabric"]) == 0
    assert kinds(lakehouse.events) == ["load", "load", "dbt deps", "dbt build", "query"]
    out = capsys.readouterr().out
    assert "demo: rebuilding in place (every model is a table)" in out
    assert "dropped" not in out
    assert not any(event[0] == "drop" for event in lakehouse.events)


def test_clean_drops_after_deps_and_before_the_build(lakehouse, capsys):
    assert demo.main(["--target", "fabric", "--clean"]) == 0
    assert kinds(lakehouse.events) == [
        "load",
        "load",
        "dbt deps",
        "drop",
        "dbt build",
        "query",
    ]
    assert "demo: dropped bronze, silver and gold before rebuilding" in capsys.readouterr().out


def test_clean_drops_exactly_gold_silver_and_bronze_and_never_landing(lakehouse):
    assert demo.main(["--target", "fabric", "--clean"]) == 0
    drops = [event for event in lakehouse.events if event[0] == "drop"]
    assert drops == [("drop", ("bronze", "gold", "silver"))]
    assert demo.DERIVED_SCHEMAS == ("gold", "silver", "bronze")
    assert "landing" not in drops[0][1]


def test_a_failing_deps_never_drops_and_never_builds(lakehouse, capsys):
    lakehouse.dbt_codes["deps"] = 1
    assert demo.main(["--target", "fabric", "--clean"]) == 1
    assert kinds(lakehouse.events) == ["load", "load", "dbt deps"]
    assert "demo failed at: dbt deps (exit 1)" in capsys.readouterr().err


def test_an_interrupted_demo_drops_nothing_and_leaves_landing(lakehouse, monkeypatch):
    def interrupted(command, root, env):
        raise KeyboardInterrupt

    monkeypatch.setattr(build_fabric, "RUNNER", interrupted)
    with pytest.raises(KeyboardInterrupt):
        demo.main(["--target", "fabric", "--clean"])
    assert kinds(lakehouse.events) == ["load", "load"]


def test_an_interrupted_load_drops_nothing(lakehouse, monkeypatch):
    def interrupted(spec, root, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(demo, "load_entity", interrupted)
    with pytest.raises(KeyboardInterrupt):
        demo.main(["--target", "fabric", "--clean"])
    assert lakehouse.events == []


def test_a_failed_drop_stops_before_the_build(lakehouse, capsys):
    from starter_cli.fabric_query import FabricQueryError

    lakehouse.fake.fail_with = FabricQueryError("dbt run-operation failed with exit code 1")
    assert demo.main(["--target", "fabric", "--clean"]) == 2
    assert kinds(lakehouse.events) == ["load", "load", "dbt deps"]
    assert "could not drop the derived schemas" in capsys.readouterr().err


@pytest.mark.parametrize("target", [[], ["--target", "duckdb"]])
def test_clean_with_the_duckdb_target_is_an_argparse_error(monkeypatch, capsys, target):
    monkeypatch.setattr(demo, "load_entity", lambda *_a, **_k: pytest.fail("loaded"))
    with pytest.raises(SystemExit) as excinfo:
        demo.main([*target, "--clean"])
    assert excinfo.value.code == 2
    assert "--clean" in capsys.readouterr().err


# --- the build (D-14) ---


def test_the_build_command_leaves_out_the_declared_known_limits_and_their_descendants(lakehouse):
    assert demo.main(["--target", "fabric"]) == 0
    build = next(command for command in lakehouse.commands if "build" in command)
    assert build_fabric.KNOWN_LIMIT_EXCLUDE == "tag:fabric_known_limit+"
    position = build.index("--exclude")
    assert build[position + 1] == "tag:fabric_known_limit+"
    assert build[build.index("build") :][:3] == ["build", "--target", "fabric"]
    assert "--select" not in build
    assert "--selector" not in build
    assert build[-4:] == ["--profiles-dir", ".", "--project-dir", "."]


def test_the_deps_command_is_the_plain_dbt_deps(lakehouse):
    assert demo.main(["--target", "fabric"]) == 0
    assert lakehouse.commands[0] == build_fabric.dbt_command("deps")


def test_a_failed_build_still_prints_the_summary_and_returns_dbts_code(lakehouse, capsys):
    lakehouse.dbt_codes["build"] = 3
    assert demo.main(["--target", "fabric"]) == 3
    out = capsys.readouterr()
    assert out.out.splitlines()[-1] == demo.FABRIC_BUILT_LABEL
    assert "demo failed at: dbt build (exit 3)" in out.err
    assert kinds(lakehouse.events)[-1] == "query"


def test_the_docs_are_left_to_the_duckdb_demo(lakehouse, capsys):
    assert demo.main(["--target", "fabric"]) == 0
    assert "demo: docs are generated by the DuckDB demo" in capsys.readouterr().out
    assert all("docs" not in command for command in lakehouse.commands)


# --- the months of the Fabric default (D-22) ---


def months_by_table(lakehouse) -> dict[str, object]:
    return {load["table"]: load["months"] for load in lakehouse.loads}


def test_the_fabric_default_month_list_is_2024_01_only(lakehouse):
    assert demo.FABRIC_DEFAULT_MONTHS == ("2024-01",)
    assert demo.main(["--target", "fabric"]) == 0
    assert months_by_table(lakehouse) == {"acme__parts": ("2024-01",), "acme__zones": None}


def test_an_explicit_months_wins_on_fabric(lakehouse):
    assert demo.main(["--target", "fabric", "--months", "2024-03"]) == 0
    assert months_by_table(lakehouse) == {"acme__parts": ("2024-03",), "acme__zones": None}


def test_a_folder_on_fabric_lands_every_file_it_holds(lakehouse, tmp_path):
    folder = tmp_path / "files"
    folder.mkdir()
    assert demo.main(["--target", "fabric", "--from-dir", str(folder)]) == 0
    assert months_by_table(lakehouse) == {"acme__parts": None, "acme__zones": None}
    assert all(load["from_dir"] == folder for load in lakehouse.loads)


def test_every_fabric_load_goes_through_the_lakehouse_sink(lakehouse):
    assert demo.main(["--target", "fabric"]) == 0
    sinks = [load["sink"] for load in lakehouse.loads]
    assert all(isinstance(sink, fabric_sink.LakehouseSink) for sink in sinks)
    assert sinks[0].workspace_id == WORKSPACE_ID
    assert sinks[0].lakehouse_id == LAKEHOUSE_ID


def test_the_duckdb_target_with_no_months_still_passes_none(monkeypatch, tmp_path):
    seen: list[object] = []
    source = types.SimpleNamespace(
        ENTITIES={"parts": types.SimpleNamespace(is_monthly=True, table="acme__parts")},
        REPORT_RELATIONS=[],
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(demo, "discover_sources", lambda: {"acme": source})
    monkeypatch.setattr(demo, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(demo, "load_entity", lambda spec, root, **kwargs: seen.append(kwargs) or 1)
    monkeypatch.setattr(demo, "drop_derived_schemas", lambda _database: None)
    monkeypatch.setattr(demo, "_run_dbt", lambda *_a: 1)  # stop right after the loads
    demo.main([])
    assert seen[0]["months"] is None
    assert "sink" not in seen[0]


# --- what the run prints ---


def test_no_id_or_lakehouse_name_is_ever_printed(lakehouse, capsys):
    assert demo.main(["--target", "fabric", "--clean"]) == 0
    out = capsys.readouterr()
    for value in FABRIC_ENV.values():
        assert value not in out.out + out.err


def test_the_closing_label_says_the_run_built_on_the_lakehouse(lakehouse, capsys):
    assert demo.main(["--target", "fabric"]) == 0
    last = capsys.readouterr().out.splitlines()[-1]
    assert last == demo.FABRIC_BUILT_LABEL
    assert last != demo.FABRIC_LABEL


def test_a_relation_that_is_not_in_the_lakehouse_prints_not_built(lakehouse, capsys):
    lakehouse.fake.con.execute("drop table silver.parts_quarantine")
    lakehouse.fake.rebuilds = False
    demo.main(["--target", "fabric"])
    out = capsys.readouterr().out
    line = next(line for line in out.splitlines() if "silver.parts_quarantine" in line)
    assert line.split()[-2:] == ["not", "built"]


def test_a_schema_that_was_never_created_prints_not_built_for_its_relations(lakehouse, capsys):
    lakehouse.fake.con.execute("drop schema gold cascade")
    lakehouse.fake.rebuilds = False
    assert demo.main(["--target", "fabric"]) == 1  # the gold reconciliation line cannot be checked
    line = next(line for line in capsys.readouterr().out.splitlines() if "gold.fct_parts" in line)
    assert line.split()[-2:] == ["not", "built"]


def test_a_relation_dbt_skipped_prints_skipped(lakehouse, tmp_path, capsys):
    results = tmp_path / "target"
    results.mkdir()
    (results / "run_results.json").write_text(
        '{"results": [{"status": "skipped", "relation_name": "`lake`.`gold`.`fct_parts`"}]}'
    )
    demo.main(["--target", "fabric"])
    line = next(line for line in capsys.readouterr().out.splitlines() if "gold.fct_parts" in line)
    assert line.split()[-1] == "skipped"


def test_a_summary_query_that_fails_stops_with_a_message_and_the_builds_code(lakehouse, capsys):
    from starter_cli.fabric_query import FabricQueryError

    original = lakehouse.fake.answer

    def failing(sql):
        if sql == "show schemas" or sql.startswith("select count(*)"):
            raise FabricQueryError("dbt run-operation failed with exit code 1")
        return original(sql)

    lakehouse.fake.answer = failing
    assert demo.main(["--target", "fabric"]) == 2
    assert "demo could not print the summary" in capsys.readouterr().err


def test_skipped_relations_reads_the_spark_relation_name_with_backticks(tmp_path):
    path = tmp_path / "run_results.json"
    path.write_text(
        '{"results": [{"status": "skipped", "relation_name": "`lake`.`gold`.`fct_trips`"},'
        '{"status": "success", "relation_name": "`lake`.`gold`.`dim_zone`"}]}'
    )
    assert demo.skipped_relations(path) == {"gold.fct_trips"}


# --- the closing summary through the QueryRunner seam: the same lines on both engines ---

RELATIONS = [
    ("landing", "landing.acme__parts"),
    ("bronze", "bronze.acme__parts"),
    ("silver", "silver.parts"),
    ("quarantine", "silver.parts_quarantine"),
    ("gold", "gold.fct_parts"),
    ("landing", "landing.acme__zones"),
]
WARNINGS = ["silver.parts"]
LEVELS = {"silver.parts": demo.Levels(Decimal("0.05"), Decimal("0.2"))}


def fabric_runner(lakehouse) -> demo.FabricQueryRunner:
    return demo.FabricQueryRunner(env={})


def closing(runner, root: Path, **kwargs):
    return demo.closing_lines(
        runner, root, RELATIONS, WARNINGS, CONSERVATION, GOLD, None, LEVELS, **kwargs
    )


def without_docs_and_label(lines: list[str]) -> list[str]:
    """The summary up to its `docs` section: the two things that differ between the engines."""
    return lines[: lines.index("docs")]


def count_batches(events: list[tuple], needle: str) -> list[tuple]:
    return [event for event in events if event[0] == "query" and needle in " ".join(event[1])]


def test_the_duckdb_runner_gives_the_lines_the_bare_connection_gave(lakehouse, tmp_path):
    con = lakehouse.fake.con
    assert closing(demo.DuckDBQueryRunner(con), tmp_path) == closing(con, tmp_path)


def test_the_same_data_prints_the_same_lines_in_the_same_order_on_both_engines(lakehouse, tmp_path):
    duckdb_lines, duckdb_ok = closing(demo.DuckDBQueryRunner(lakehouse.fake.con), tmp_path)
    fabric_lines, fabric_ok = closing(fabric_runner(lakehouse), tmp_path)
    assert fabric_lines == duckdb_lines
    assert fabric_ok is duckdb_ok is True
    assert "reconciliation" in fabric_lines
    assert any("bronze 30 = silver 27 + quarantine 3  OK" in line for line in fabric_lines)
    assert any("quarantine rate 10.000% (3 of 30 rows)" in line for line in fabric_lines)
    assert any("worst file of 2: b.csv  20.000%" in line for line in fabric_lines)
    assert any(line.startswith("    error  missing_id") for line in fabric_lines)


def test_a_failing_reconciliation_fails_the_same_way_on_both_engines(lakehouse, tmp_path):
    lakehouse.fake.con.execute("delete from silver.parts where id = 1")
    duckdb_lines, duckdb_ok = closing(demo.DuckDBQueryRunner(lakehouse.fake.con), tmp_path)
    fabric_lines, fabric_ok = closing(fabric_runner(lakehouse), tmp_path)
    assert fabric_lines == duckdb_lines
    assert fabric_ok is duckdb_ok is False
    assert any("bronze 30 = silver 26 + quarantine 3  FAIL" in line for line in fabric_lines)


def test_skipped_and_unbuilt_relations_read_the_same_on_both_engines(lakehouse, tmp_path):
    lakehouse.fake.con.execute("drop table silver.parts_quarantine")
    skipped = frozenset({"gold.fct_parts"})
    duckdb_lines, _ = closing(demo.DuckDBQueryRunner(lakehouse.fake.con), tmp_path, skipped=skipped)
    fabric_lines, _ = closing(fabric_runner(lakehouse), tmp_path, skipped=skipped)
    assert fabric_lines == duckdb_lines
    assert any("not checked: silver.parts_quarantine not built" in line for line in fabric_lines)
    assert any("not checked: gold.fct_parts skipped" in line for line in fabric_lines)


def test_the_demo_prints_the_duckdb_summary_apart_from_the_docs_and_the_label(
    lakehouse, tmp_path, capsys, monkeypatch
):
    monkeypatch.setattr(demo, "read_levels", lambda _path: LEVELS)
    assert demo.main(["--target", "fabric"]) == 0
    out = capsys.readouterr().out
    printed = out[out.index("layer       relation") :].splitlines()
    expected, _ok = closing(demo.DuckDBQueryRunner(lakehouse.fake.con), tmp_path)
    assert without_docs_and_label(printed) == without_docs_and_label(expected)
    assert printed[-1] == demo.FABRIC_BUILT_LABEL
    assert expected[-1] == demo.FABRIC_LABEL
    assert printed[printed.index("docs") + 1 : -2] != expected[expected.index("docs") + 1 : -2]
    assert "https://" not in "\n".join(printed[printed.index("docs") :])


def test_a_reconciliation_fail_on_fabric_exits_1_with_the_message(lakehouse, capsys):
    lakehouse.fake.con.execute("delete from silver.parts where id = 1")
    assert demo.main(["--target", "fabric"]) == 1
    out = capsys.readouterr()
    assert "FAIL" in out.out
    assert "demo: a reconciliation line shows FAIL." in out.err


def test_an_error_rate_after_a_passed_build_prints_the_note_on_fabric_too(
    lakehouse, tmp_path, monkeypatch, capsys
):
    manifest = tmp_path / "target" / "manifest.json"
    manifest.parent.mkdir()
    manifest.write_text("{}")
    monkeypatch.setattr(
        demo,
        "read_levels",
        lambda _path: {"silver.parts": demo.Levels(Decimal("0.01"), Decimal("0.02"))},
    )
    assert demo.main(["--target", "fabric"]) == 0
    assert demo.RATE_DISAGREEMENT_NOTE in capsys.readouterr().out


# --- one batch per function (each Fabric call opens a session) ---


def test_report_lines_reads_every_count_in_one_batch(lakehouse):
    demo.report_lines(fabric_runner(lakehouse), RELATIONS, WARNINGS)
    batches = count_batches(lakehouse.events, "select count(*)")
    assert len(batches) == 1
    assert len(batches[0][1]) == len(RELATIONS) + len(WARNINGS)


def test_conservation_lines_reads_every_count_in_one_batch(lakehouse):
    demo.conservation_lines(fabric_runner(lakehouse), CONSERVATION)
    batches = count_batches(lakehouse.events, "select count(*)")
    assert len(batches) == 1
    assert len(batches[0][1]) == 3


def test_gold_lines_reads_every_figure_in_one_batch(lakehouse):
    demo.gold_lines(fabric_runner(lakehouse), GOLD)
    batches = count_batches(lakehouse.events, "select count(*)")
    assert len(batches) == 1
    assert len(batches[0][1]) == 2


def test_the_rates_of_all_entities_are_read_in_one_batch(lakehouse):
    demo.quarantine_summary_lines(fabric_runner(lakehouse), CONSERVATION, LEVELS)
    scopes = count_batches(lakehouse.events, "grain")
    assert len(scopes) == 1
    assert len(scopes[0][1]) == len(CONSERVATION)


def test_the_whole_closing_summary_opens_seven_sessions_without_a_batch(lakehouse, tmp_path):
    closing(fabric_runner(lakehouse), tmp_path)
    # The long way: show schemas; show tables; the counts; the conservation counts; the gold
    # figures; the summary's entities and codes; the rates.
    assert len([event for event in lakehouse.events if event[0] == "query"]) == 7


def prefetched(lakehouse, **kwargs) -> demo.FabricQueryRunner:
    runner = demo.FabricQueryRunner(env={}, **kwargs)
    queries, read = demo.summary_queries(RELATIONS, WARNINGS, CONSERVATION, GOLD, runner)
    assert runner.prefetch(queries, read) is True
    return runner


def test_the_whole_closing_summary_opens_one_session_when_it_reads_in_one_batch(
    lakehouse, tmp_path
):
    runner = prefetched(lakehouse)
    lines, ok = closing(runner, tmp_path)
    assert [event[0] for event in lakehouse.events] == ["query"]
    assert ok is True
    # The same lines as the long way and as DuckDB.
    assert lines == closing(demo.DuckDBQueryRunner(lakehouse.fake.con), tmp_path)[0]


def test_the_batch_holds_no_listing_and_no_query_twice(lakehouse):
    runner = demo.FabricQueryRunner(env={})
    queries, read = demo.summary_queries(RELATIONS, WARNINGS, CONSERVATION, GOLD, runner)
    assert "show schemas" not in queries and not any(q.startswith("show tables") for q in queries)
    assert len(queries) == len(set(queries))
    assert set(read) >= {"bronze.acme__parts", "silver.parts", "gold.agg_parts"}
    assert demo.DQ_SUMMARY in read


def test_a_failing_batch_leaves_the_summary_to_the_long_way(lakehouse, tmp_path):
    lakehouse.fake.con.execute("drop table silver.parts_quarantine")
    runner = demo.FabricQueryRunner(env={})
    queries, read = demo.summary_queries(RELATIONS, WARNINGS, CONSERVATION, GOLD, runner)
    assert runner.prefetch(queries, read) is False
    lines, ok = closing(runner, tmp_path)
    assert ok is False
    assert any("silver.parts_quarantine" in line and line.endswith("not built") for line in lines)


def test_a_skipped_relation_is_left_out_of_the_batch_with_what_needs_it(lakehouse):
    runner = demo.FabricQueryRunner(env={})
    queries, read = demo.summary_queries(
        RELATIONS, WARNINGS, CONSERVATION, GOLD, runner, frozenset({"gold.fct_parts"})
    )
    assert "gold.fct_parts" not in read and "gold.agg_parts" not in read
    assert not any("gold." in query for query in queries)
    assert "silver.parts" in read


def test_a_capacity_wait_runs_before_each_session_the_runner_opens(lakehouse, tmp_path):
    waited: list[int] = []
    runner = prefetched(lakehouse, before_session=lambda: waited.append(len(lakehouse.events)))
    closing(runner, tmp_path)
    assert waited == [0]  # one wait, before the one summary session


def test_show_schemas_values_are_read_by_their_last_part_whatever_the_catalog():
    assert demo._schema_name("placeholder-catalog.placeholder_lake.Gold") == "gold"
    assert demo._schema_name("`placeholder-catalog`.placeholder_lake.silver") == "silver"
    assert demo._schema_name("bronze") == "bronze"


def test_the_live_shaped_listing_finds_the_relations_of_the_lakehouse(lakehouse):
    runner = fabric_runner(lakehouse)
    assert runner.exists("silver.parts") is True
    assert runner.exists("landing.acme__zones") is True
    assert runner.exists("silver.nothing") is False
    assert runner.exists("platinum.parts") is False


def test_the_relations_of_a_schema_are_listed_once_for_the_whole_summary(lakehouse, tmp_path):
    closing(fabric_runner(lakehouse), tmp_path)
    listings = [
        sql
        for event in lakehouse.events
        for sql in event[1]
        if sql == "show schemas" or sql.startswith("show tables in")
    ]
    assert listings.count("show schemas") == 1
    assert len(listings) == len(set(listings))


# --- the rate SQL of each engine (the macro's own text) ---


def test_each_runner_carries_its_own_safe_divide_rendering(lakehouse):
    assert demo.DuckDBQueryRunner(lakehouse.fake.con).safe_divide == demo.SAFE_DIVIDE_SQL
    assert fabric_runner(lakehouse).safe_divide == demo.SAFE_DIVIDE_SQL_SPARK
    assert demo.SAFE_DIVIDE_SQL_SPARK != demo.SAFE_DIVIDE_SQL


def test_the_fabric_rate_query_uses_the_spark_rendering_and_never_the_duckdb_one(lakehouse):
    demo.scope_rates(fabric_runner(lakehouse), "bronze.acme__parts", "silver.parts")
    sent = " ".join(sql for event in lakehouse.events for sql in event[1])
    rate = demo.SAFE_DIVIDE_SQL_SPARK.format(numerator="rejected_rows", denominator="total_rows")
    assert rate in sent
    assert "hugeint" not in sent


def test_the_duckdb_rate_query_still_uses_the_duckdb_rendering(lakehouse):
    sent: list[str] = []

    class Recording(demo.DuckDBQueryRunner):
        def fetch(self, sql):
            sent.append(sql)
            return super().fetch(sql)

    demo.scope_rates(Recording(lakehouse.fake.con), "bronze.acme__parts", "silver.parts")
    rate = demo.SAFE_DIVIDE_SQL.format(numerator="rejected_rows", denominator="total_rows")
    assert any(rate in sql for sql in sent)


def test_the_fabric_rates_equal_the_duckdb_rates_on_ties_and_zero_denominators(lakehouse):
    lakehouse.fake.con.execute("create table bronze.tie (_source_file varchar)")
    lakehouse.fake.con.execute(
        "insert into bronze.tie select 'x.csv' from range(32)"
    )  # 1 rejected of 32 is 0.03125, a tie at the sixth decimal when scaled
    lakehouse.fake.con.execute("create table silver.tie (_source_file varchar)")
    lakehouse.fake.con.execute("insert into silver.tie select 'x.csv' from range(31)")
    duck = demo.scope_rates(demo.DuckDBQueryRunner(lakehouse.fake.con), "bronze.tie", "silver.tie")
    fabric = demo.scope_rates(fabric_runner(lakehouse), "bronze.tie", "silver.tie")
    assert fabric == duck
    assert isinstance(fabric[0].rate, Decimal)


def test_a_percent_sign_never_reaches_a_fabric_query(lakehouse, tmp_path):
    closing(fabric_runner(lakehouse), tmp_path)
    assert all("%" not in sql for event in lakehouse.events for sql in event[1])


# --- plan 05-22: one summary session, one wait before it, the build's own run_results ---


def test_the_fabric_demo_reads_the_whole_summary_in_one_session_after_one_wait(lakehouse, capsys):
    assert demo.main(["--target", "fabric"]) == 0
    capsys.readouterr()
    sessions = [event for event in lakehouse.events if event[0] == "query"]
    assert len(sessions) == 1
    assert "show schemas" not in sessions[0][1]
    # One wait, after every load and dbt step and right before that session.
    assert lakehouse.waits == [(len(lakehouse.events) - 1, WORKSPACE_ID)]
    assert kinds(lakehouse.events)[-2:] == ["dbt build", "query"]


def test_a_summary_that_cannot_batch_waits_before_each_session_it_opens(lakehouse, capsys):
    lakehouse.fake.con.execute("drop table silver.parts_quarantine")
    lakehouse.fake.rebuilds = False
    demo.main(["--target", "fabric"])
    capsys.readouterr()
    sessions = [event for event in lakehouse.events if event[0] == "query"]
    assert len(sessions) > 1
    assert len(lakehouse.waits) == len(sessions)


def test_clean_waits_for_capacity_before_the_drop_and_before_the_build(lakehouse, capsys):
    assert demo.main(["--target", "fabric", "--clean"]) == 0
    capsys.readouterr()
    positions = [position for position, _ in lakehouse.waits]
    drop = lakehouse.events.index(("drop", ("bronze", "gold", "silver")))
    build = lakehouse.events.index(("dbt", "build"))
    assert positions[0] == drop and positions[1] == build  # a wait just before each
    assert len(positions) == 3  # and the one before the summary


def test_a_rebuild_in_place_waits_only_before_the_summary(lakehouse, capsys):
    demo.main(["--target", "fabric"])
    capsys.readouterr()
    assert len(lakehouse.waits) == 1


def test_the_build_run_results_are_copied_before_the_summary_reads(lakehouse, tmp_path, capsys):
    target = tmp_path / "target"
    target.mkdir()
    (target / "run_results.json").write_text('{"results": []}')
    demo.main(["--target", "fabric"])
    capsys.readouterr()
    assert (target / demo.BUILD_RESULTS_NAME).read_text() == '{"results": []}'


def test_without_run_results_nothing_is_copied(lakehouse, tmp_path, capsys):
    demo.main(["--target", "fabric"])
    capsys.readouterr()
    assert not (tmp_path / "target" / demo.BUILD_RESULTS_NAME).exists()


def test_the_capacity_wait_goes_on_when_the_workspace_cannot_be_asked(monkeypatch, capsys):
    from starter_cli import fabric_rest, verify_fabric

    def refused(env):
        raise fabric_rest.FabricApiError(403, "Forbidden")

    monkeypatch.setattr(verify_fabric, "await_capacity", refused)
    demo._await_capacity(FABRIC_ENV)  # does not raise
    assert "not waiting for free capacity (FabricApiError)" in capsys.readouterr().out

    def busy(env):
        raise verify_fabric.VerifyError("the capacity still runs a Spark session")

    monkeypatch.setattr(verify_fabric, "await_capacity", busy)
    demo._await_capacity(FABRIC_ENV)
    assert "not waiting for free capacity (VerifyError)" in capsys.readouterr().out


def test_the_capacity_wait_is_verify_fabrics_own(monkeypatch):
    from starter_cli import verify_fabric

    seen: list[object] = []
    monkeypatch.setattr(verify_fabric, "await_capacity", seen.append)
    demo._await_capacity(FABRIC_ENV)
    assert seen == [FABRIC_ENV]

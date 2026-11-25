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
from pathlib import Path

import pytest

from starter_cli import build_fabric, demo, fabric_sink

WORKSPACE_ID = "aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb"
LAKEHOUSE_ID = "cccccccc-4444-5555-6666-dddddddddddd"
LAKEHOUSE = "lake_secret_name"
FABRIC_ENV = {
    "FABRIC_WORKSPACE_ID": WORKSPACE_ID,
    "FABRIC_LAKEHOUSE_ID": LAKEHOUSE_ID,
    "FABRIC_LAKEHOUSE": LAKEHOUSE,
}


class FakeLakehouse:
    """Answers the SQL the demo sends through `run_queries`, and notes every call in `events`.

    `tables` maps a schema to the tables it holds; a schema not listed does not exist. `counts`
    maps a relation to its row count, and `warned` maps a relation to its count of warned rows.
    """

    def __init__(self, events: list[tuple]) -> None:
        self.events = events
        self.tables: dict[str, list[str]] = {
            "landing": ["acme__parts", "acme__zones"],
            "bronze": ["acme__parts", "acme__zones"],
            "silver": ["parts", "parts_quarantine", "zones"],
            "gold": ["fct_parts"],
        }
        self.counts: dict[str, int] = {
            "landing.acme__parts": 30,
            "landing.acme__zones": 5,
            "bronze.acme__parts": 30,
            "bronze.acme__zones": 5,
            "silver.parts": 27,
            "silver.parts_quarantine": 3,
            "silver.zones": 5,
            "gold.fct_parts": 27,
        }
        self.warned: dict[str, int] = {"silver.parts": 4}
        self.fail_with: Exception | None = None
        self.envs: list[dict[str, str]] = []

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

    def answer(self, sql: str) -> list[list[str | None]]:
        if sql.startswith("drop schema"):
            return []
        if sql == "show schemas":
            return [[schema] for schema in self.tables]
        if sql.startswith("show tables in "):
            schema = sql.removeprefix("show tables in ")
            return [[schema, table, "false"] for table in self.tables[schema]]
        if sql.startswith("select count(*) from "):
            relation = sql.removeprefix("select count(*) from ")
            if relation.endswith(demo.WARNING_WHERE):
                return [[str(self.warned.get(relation.removesuffix(demo.WARNING_WHERE), 0))]]
            return [[str(self.counts[relation])]]
        raise AssertionError(f"the fake Lakehouse was asked an unknown query: {sql}")


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
        REPORT_CONSERVATION={},
        REPORT_GOLD=[],
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
        return dbt_codes.get(step, 0)

    monkeypatch.setattr(fabric_sink, "sink_installed", lambda: True)
    monkeypatch.setattr(build_fabric, "fabric_adapter_installed", lambda: True)
    monkeypatch.setattr(build_fabric, "RUNNER", runner)
    monkeypatch.setattr(demo, "discover_sources", lambda: {"acme": source})
    monkeypatch.setattr(demo, "load_entity", load_entity)
    monkeypatch.setattr(demo, "run_queries", fake.run_queries)
    monkeypatch.setattr(demo, "repo_root", lambda: tmp_path)
    return types.SimpleNamespace(
        events=events,
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
    lakehouse.fake.tables["silver"].remove("parts_quarantine")
    assert demo.main(["--target", "fabric"]) == 0
    out = capsys.readouterr().out
    line = next(line for line in out.splitlines() if "silver.parts_quarantine" in line)
    assert line.split()[-2:] == ["not", "built"]


def test_a_schema_that_was_never_created_prints_not_built_for_its_relations(lakehouse, capsys):
    del lakehouse.fake.tables["gold"]
    assert demo.main(["--target", "fabric"]) == 0
    line = next(line for line in capsys.readouterr().out.splitlines() if "gold.fct_parts" in line)
    assert line.split()[-2:] == ["not", "built"]


def test_a_relation_dbt_skipped_prints_skipped(lakehouse, tmp_path, capsys):
    results = tmp_path / "target"
    results.mkdir()
    (results / "run_results.json").write_text(
        '{"results": [{"status": "skipped", "relation_name": "`lake`.`gold`.`fct_parts`"}]}'
    )
    assert demo.main(["--target", "fabric"]) == 0
    line = next(line for line in capsys.readouterr().out.splitlines() if "gold.fct_parts" in line)
    assert line.split()[-1] == "skipped"


def test_a_summary_query_that_fails_stops_with_a_message_and_the_builds_code(lakehouse, capsys):
    from starter_cli.fabric_query import FabricQueryError

    original = lakehouse.fake.answer

    def failing(sql):
        if sql == "show schemas":
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

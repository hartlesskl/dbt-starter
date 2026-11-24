"""`verify-fabric`: the native build driver, with every seam stubbed (D-08, D-09, D-10, D-12).

These tests replace `fabric_rest.HTTP`, `TOKEN` and `SLEEP`, and the driver's own `NOW`, `SLEEP`,
`EVIDENCE_ROOT`, `RUNNER`, `RUN_QUERIES`, `SINK_FACTORY` and `LOAD_ENTITY`, so they never reach the
network, a Spark session or a Lakehouse and never sleep. They hold the pinned evidence schema
(build-N.json and state.json key sets), the REST and portal starts, the 0, 75 and 1 exits of wait
(including the full-refresh comparison, D-08), the match of a run to its invocation row by the job
instance's own times (T-05-92), the Location host guard (T-05-29), `init --fresh`'s limits
(T-05-30) and that no output line holds an id (T-05-31).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from starter_cli import fabric_rest, nyc_taxi, verify_fabric

WORKSPACE_ID = "aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb"
LAKEHOUSE_ID = "cccccccc-4444-5555-6666-dddddddddddd"
JOB_ID = "eeeeeeee-7777-8888-9999-ffffffffffff"
INSTANCE_ID = "12121212-3434-5656-7878-909090909090"
INVOCATION_ID = "abababab-cdcd-efef-0101-232323232323"
OTHER_INVOCATION = "45454545-6767-8989-0a0a-bcbcbcbcbcbc"
LAKEHOUSE = "lake_secret_name"
IDS = (WORKSPACE_ID, LAKEHOUSE_ID, JOB_ID, INSTANCE_ID, INVOCATION_ID, OTHER_INVOCATION)

VALID_ENV = {
    "FABRIC_WORKSPACE_ID": WORKSPACE_ID,
    "FABRIC_LAKEHOUSE_ID": LAKEHOUSE_ID,
    "FABRIC_LAKEHOUSE": LAKEHOUSE,
    "FABRIC_DBT_JOB_ID": JOB_ID,
}

# The Mac clock the driver records starts with, and Fabric's own instance times.
STARTED = datetime(2026, 11, 25, 9, 0, 0, tzinfo=UTC)
RUN_START = "2026-11-25T09:01:10.0955748"
RUN_END = "2026-11-25T09:22:40.6530291"
JOBS_URL = f"{fabric_rest.FABRIC_API_BASE}/workspaces/{WORKSPACE_ID}/items/{JOB_ID}/jobs/instances"
LIVY_URL = f"{fabric_rest.FABRIC_API_BASE}/workspaces/{WORKSPACE_ID}/spark/livySessions"


def reply(status: int, body: dict[str, Any] | None = None, **headers: str):
    return fabric_rest.HttpResponse(
        status, {k.replace("_", "-"): v for k, v in headers.items()}, body
    )


def instance(status: str = "Completed", start: str = RUN_START, end: str | None = RUN_END, **extra):
    entry: dict[str, Any] = {
        "id": INSTANCE_ID,
        "itemId": JOB_ID,
        "jobType": "DBTItem",
        "invokeType": "Manual",
        "status": status,
        "startTimeUtc": start,
        "endTimeUtc": end,
    }
    entry.update(extra)
    return entry


def invocation_row(full_refresh: str = "false", invocation_id: str = INVOCATION_ID):
    return [
        invocation_id,
        "1.11.8",
        "4.1.1 c9df16047cb2570aec895cd4718f04025913e19f",
        "UTC",
        "build",
        full_refresh,
        "2026-11-25 09:22:03.337571",
    ]


RESULT_ROWS = [
    [INVOCATION_ID, "seed.dbt_starter.digits", "seed", "success", None, "31.68"],
    [INVOCATION_ID, "model.dbt_starter.fct_trips", "model", "success", None, "11.3"],
    [INVOCATION_ID, "test.dbt_starter.undeclared.0c389c21d1", "test", "warn", "1", "3.03"],
    [INVOCATION_ID, "unit_test.dbt_starter.fct_trips.case", "unit_test", "pass", "0", "1.5"],
]


class Stub:
    """Recorders for every seam and the canned answers the tests set."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "evidence"
        self.http: list[tuple[str, str]] = []
        self.handlers: list[Callable[[str, str], fabric_rest.HttpResponse | None]] = []
        self.sleeps: list[float] = []
        self.queries: list[dict[str, str]] = []
        self.answers: list[dict[str, list[list[str | None]]]] = []
        self.dbt: list[list[str]] = []
        self.dbt_code = 0
        self.loads: list[dict[str, Any]] = []
        self.instances: list[list[dict[str, Any]]] = []
        self.livy_active = 0
        self.now = STARTED

    def answer(self, **rows: list[list[str | None]]) -> None:
        self.answers.append(dict(rows))

    def state(self) -> dict[str, Any]:
        return json.loads((self.root / "adhoc" / "state.json").read_text())

    def build(self, number: int) -> dict[str, Any]:
        return json.loads((self.root / "adhoc" / f"build-{number}.json").read_text())


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Stub:
    for key in [key for key in os.environ if key.startswith("FABRIC_")]:
        monkeypatch.delenv(key)
    for key, value in VALID_ENV.items():
        monkeypatch.setenv(key, value)
    result = Stub(tmp_path)

    def http(method, url, headers, json_body):
        result.http.append((method, url))
        for handler in result.handlers:
            response = handler(method, url)
            if response is not None:
                return response
        if method == "GET" and url.startswith(LIVY_URL):
            state = "Running" if result.livy_active else "Succeeded"
            return reply(200, {"value": [{"state": state}]})
        if method == "GET" and url.startswith(JOBS_URL):
            batch = result.instances.pop(0) if len(result.instances) > 1 else result.instances[0]
            return reply(200, {"value": batch})
        raise AssertionError(f"unexpected request: {method}")

    def run_queries(queries: Mapping[str, str], env: Mapping[str, str]):
        result.queries.append(dict(queries))
        answer = result.answers.pop(0)
        return {name: answer.get(name, []) for name in queries}

    def runner(command: list[str], _root, _env) -> int:
        result.dbt.append(command)
        return result.dbt_code

    def load(spec, root, **kwargs):
        result.loads.append({"table": spec.table, **kwargs})
        return 7

    monkeypatch.setattr(fabric_rest, "HTTP", http)
    monkeypatch.setattr(fabric_rest, "TOKEN", lambda: "token-value")
    monkeypatch.setattr(fabric_rest, "SLEEP", result.sleeps.append)
    monkeypatch.setattr(verify_fabric, "SLEEP", result.sleeps.append)
    monkeypatch.setattr(verify_fabric, "NOW", lambda: result.now)
    monkeypatch.setattr(verify_fabric, "EVIDENCE_ROOT", lambda: result.root)
    monkeypatch.setattr(verify_fabric, "RUNNER", runner)
    monkeypatch.setattr(verify_fabric, "RUN_QUERIES", run_queries)
    monkeypatch.setattr(verify_fabric, "SINK_FACTORY", lambda ws, lh: ("sink",))
    monkeypatch.setattr(verify_fabric, "LOAD_ENTITY", load)
    monkeypatch.setattr(verify_fabric, "extra_installed", lambda: True)
    return result


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = verify_fabric.main(list(argv))
    captured = capsys.readouterr()
    for text in (captured.out, captured.err):
        for value in (*IDS, LAKEHOUSE):
            assert value not in text, "an id or the Lakehouse name reached the output"
    return code, captured.out, captured.err


def started(stub: Stub, build: int, mode: str = "manual") -> None:
    verify_fabric.record_start("adhoc", build, STARTED, mode)


# ---------------------------------------------------------------------------------------------
# Environment and extra


def test_a_missing_job_id_exits_2_naming_it(stub: Stub, monkeypatch, capsys) -> None:
    monkeypatch.delenv("FABRIC_DBT_JOB_ID")
    code, _, err = run(capsys, "start", "--build", "1")
    assert code == 2
    assert err.count("FABRIC_DBT_JOB_ID is not set") == 1
    assert not (stub.root / "adhoc" / "state.json").exists()


def test_a_job_id_that_is_not_a_uuid_exits_2(stub: Stub, monkeypatch, capsys) -> None:
    monkeypatch.setenv("FABRIC_DBT_JOB_ID", "not-a-uuid")
    code, _, err = run(capsys, "wait", "--build", "1")
    assert code == 2
    assert "FABRIC_DBT_JOB_ID is not a UUID" in err


def test_init_does_not_need_the_job_id(stub: Stub, monkeypatch, capsys) -> None:
    monkeypatch.delenv("FABRIC_DBT_JOB_ID")
    code, _, _ = run(capsys, "init")
    assert code == 0


def test_a_missing_lakehouse_variable_exits_2_for_every_subcommand(stub, monkeypatch, capsys):
    monkeypatch.delenv("FABRIC_LAKEHOUSE_ID")
    for argv in (["init"], ["land-fixture"], ["start", "--build", "1"], ["wait", "--build", "1"]):
        code, _, err = run(capsys, *argv)
        assert code == 2
        assert "FABRIC_LAKEHOUSE_ID is not set" in err
    assert stub.http == [] and stub.dbt == [] and stub.loads == []


def test_without_the_extra_every_subcommand_exits_2(stub, monkeypatch, capsys) -> None:
    monkeypatch.setattr(verify_fabric, "extra_installed", lambda: False)
    code, _, err = run(capsys, "init")
    assert code == 2
    assert "--extra fabric" in err


def test_a_label_outside_the_pattern_is_refused(stub: Stub, capsys) -> None:
    with pytest.raises(SystemExit) as raised:
        verify_fabric.main(["init", "--label", "../up"])
    assert raised.value.code == 2


# ---------------------------------------------------------------------------------------------
# REST client additions


def test_the_dbt_job_type_matches_plan_05_04_documented_fallback() -> None:
    path = Path(__file__).resolve().parents[2] / "target/fabric-live/native-first-run.json"
    if path.exists():
        record = json.loads(path.read_text())
        proven = record["job_rest_run"]["outcome"] == "pass"
        assert (fabric_rest.DBT_JOB_TYPE is None) is not proven
    assert fabric_rest.DBT_JOB_TYPE is None
    assert fabric_rest.RUN_PATH_FORM is None


def test_a_location_on_another_host_is_refused(stub: Stub) -> None:
    for location in (
        "https://evil.example.com/v1/workspaces/x/items/y/jobs/instances/z",
        "https://api.fabric.microsoft.com.evil.example/v1/x",
        "http://api.fabric.microsoft.com/v1/x",
    ):
        with pytest.raises(ValueError, match="not on the Fabric API host"):
            fabric_rest.get_job_instance(location)
    assert stub.http == []


def test_a_location_on_the_api_host_is_followed(stub: Stub) -> None:
    stub.handlers.append(lambda m, u: reply(200, {"status": "InProgress"}) if "/x/" in u else None)
    assert fabric_rest.get_job_instance(f"{fabric_rest.FABRIC_API_BASE}/x/1") == {
        "status": "InProgress"
    }


def test_run_item_job_returns_the_location_of_the_202(stub: Stub) -> None:
    location = f"{JOBS_URL}/{INSTANCE_ID}"
    stub.handlers.append(lambda m, u: reply(202, None, location=location) if m == "POST" else None)
    assert fabric_rest.run_item_job(WORKSPACE_ID, JOB_ID, "DBTItem", "path") == location
    assert stub.http[-1][1] == f"{JOBS_URL.rsplit('/', 1)[0]}/DBTItem/instances"
    fabric_rest.run_item_job(WORKSPACE_ID, JOB_ID, "DBTItem", "query")
    assert stub.http[-1][1] == f"{JOBS_URL}?jobType=DBTItem"


def test_run_item_job_without_a_proven_form_is_refused(stub: Stub) -> None:
    with pytest.raises(ValueError, match="portal"):
        fabric_rest.run_item_job(WORKSPACE_ID, JOB_ID, "DBTItem")
    assert stub.http == []


def test_list_job_instances_follows_the_continuation_token(stub: Stub) -> None:
    stub.handlers.append(
        lambda m, u: reply(200, {"value": [{"n": 2}]}) if "continuationToken=t%2F1" in u else None
    )
    stub.handlers.append(
        lambda m, u: (
            reply(200, {"value": [{"n": 1}], "continuationToken": "t/1"}) if u == JOBS_URL else None
        )
    )
    assert fabric_rest.list_job_instances(WORKSPACE_ID, JOB_ID) == [{"n": 1}, {"n": 2}]


def test_get_item_definition_decodes_the_dbt_content_part(stub: Stub) -> None:
    import base64

    payload = base64.b64encode(json.dumps({"profile": {"schema": "dbo"}}).encode()).decode()
    body = {
        "definition": {
            "parts": [
                {"path": ".platform", "payload": "e30="},
                {"path": "dbt-content.json", "payload": payload},
            ]
        }
    }
    stub.handlers.append(lambda m, u: reply(200, body) if u.endswith("/getDefinition") else None)
    assert fabric_rest.get_item_definition(WORKSPACE_ID, JOB_ID) == {"profile": {"schema": "dbo"}}


def test_active_livy_sessions_counts_only_live_states(stub: Stub) -> None:
    sessions = [{"state": s} for s in ("Running", "Starting", "Succeeded", "Dead", "Cancelled")]
    stub.handlers.append(lambda m, u: reply(200, {"value": sessions}) if u == LIVY_URL else None)
    assert fabric_rest.active_livy_sessions(WORKSPACE_ID) == 2


# ---------------------------------------------------------------------------------------------
# init


def test_init_without_fresh_issues_no_drop_and_creates_the_tables(stub: Stub, capsys) -> None:
    code, out, _ = run(capsys, "init")
    assert code == 0
    assert stub.queries == []
    assert len(stub.dbt) == 1
    assert "starter.create_verification_tables" in stub.dbt[0]
    assert stub.dbt[0][stub.dbt[0].index("--target") + 1] == "fabric"
    assert "dropping" not in out


def test_init_waits_for_a_free_capacity_before_the_dbt_call(stub: Stub, capsys) -> None:
    sessions = iter([1, 1, 0])
    stub.handlers.append(
        lambda m, u: (
            reply(200, {"value": [{"state": "Running" if next(sessions) else "Succeeded"}]})
            if u == LIVY_URL
            else None
        )
    )
    code, _, _ = run(capsys, "init")
    assert code == 0
    assert stub.sleeps == [
        verify_fabric.CAPACITY_POLL_SECONDS,
        verify_fabric.CAPACITY_POLL_SECONDS,
        verify_fabric.CAPACITY_SETTLE_SECONDS,
    ]


def test_init_stops_when_the_capacity_stays_busy(stub: Stub, capsys) -> None:
    stub.livy_active = 1
    code, _, err = run(capsys, "init")
    assert code == 1
    assert "capacity" in err
    assert stub.dbt == []


def test_init_fresh_drops_exactly_the_listed_objects(stub: Stub, capsys) -> None:
    stub.answer(
        landing_tables=[
            ["`lake`.landing", "nyc_taxi__taxi_zones", "False"],
            ["`lake`.landing", "nyc_taxi__yellow_trips", "False"],
            ["`lake`.landing", "someone_elses_table", "False"],
        ]
    )
    stub.answer()
    code, out, _ = run(capsys, "init", "--fresh")
    assert code == 0
    assert stub.queries[0] == {"landing_tables": "show tables in landing"}
    drops = stub.queries[1]
    assert sorted(drops.values()) == sorted(
        [f"drop schema if exists {schema} cascade" for schema in verify_fabric.DROPPED_SCHEMAS]
        + [f"drop table {table}" for table in verify_fabric.FIXTURE_LANDING_TABLES]
    )
    assert not any("someone_elses_table" in sql for sql in drops.values())
    assert not any(re.search(r"drop schema .*landing", sql) for sql in drops.values())
    for name in (*verify_fabric.DROPPED_SCHEMAS, *verify_fabric.FIXTURE_LANDING_TABLES):
        assert name in out
    assert len(stub.dbt) == 1


def test_init_fresh_without_a_fixture_table_refuses_and_drops_nothing(stub: Stub, capsys) -> None:
    stub.answer(landing_tables=[["`lake`.landing", "nyc_taxi__taxi_zones", "False"]])
    code, _, err = run(capsys, "init", "--fresh")
    assert code == 1
    assert (
        "landing.nyc_taxi__yellow_trips is not in the Lakehouse, so --fresh has nothing to reset; "
        "run init without --fresh"
    ) in err
    assert len(stub.queries) == 1
    assert stub.dbt == []


def test_the_fixture_landing_tables_are_derived_from_the_entity_specs() -> None:
    landing = sorted(rel for label, rel in nyc_taxi.REPORT_RELATIONS if label == "landing")
    assert list(verify_fabric.FIXTURE_LANDING_TABLES) == landing
    assert verify_fabric.FIXTURE_LANDING_TABLES == (
        "landing.nyc_taxi__taxi_zones",
        "landing.nyc_taxi__yellow_trips",
    )
    assert "landing" not in verify_fabric.DROPPED_SCHEMAS


# ---------------------------------------------------------------------------------------------
# land-fixture


def test_land_fixture_lands_both_entities_from_the_written_fixture(stub: Stub, capsys) -> None:
    code, out, _ = run(capsys, "land-fixture")
    assert code == 0
    folder = stub.root / "adhoc" / "fixture"
    assert [load["table"] for load in stub.loads] == [
        "nyc_taxi__taxi_zones",
        "nyc_taxi__yellow_trips",
    ]
    assert all(load["from_dir"] == folder and load["sink"] == ("sink",) for load in stub.loads)
    assert (folder / "taxi_zone_lookup.csv").exists()
    assert "landed 7 rows into landing.nyc_taxi__yellow_trips" in out


# ---------------------------------------------------------------------------------------------
# start


def test_start_build_1_uses_rest_when_a_job_type_is_proven(stub, monkeypatch, capsys) -> None:
    monkeypatch.setattr(fabric_rest, "DBT_JOB_TYPE", "DBTItem")
    monkeypatch.setattr(fabric_rest, "RUN_PATH_FORM", "path")
    stub.handlers.append(
        lambda m, u: reply(202, None, location=f"{JOBS_URL}/{INSTANCE_ID}") if m == "POST" else None
    )
    code, out, _ = run(capsys, "start", "--build", "1")
    assert code == 0
    assert [m for m, _ in stub.http] == ["POST"]
    assert stub.state() == {"builds": {"1": {"started_at": "2026-11-25T09:00:00Z", "mode": "rest"}}}
    assert "Location" not in out and "instances" not in out
    state_text = (stub.root / "adhoc" / "state.json").read_text()
    assert "http" not in state_text


def test_start_build_3_prints_the_portal_settings_even_with_rest(stub, monkeypatch, capsys):
    monkeypatch.setattr(fabric_rest, "DBT_JOB_TYPE", "DBTItem")
    monkeypatch.setattr(fabric_rest, "RUN_PATH_FORM", "path")
    code, out, _ = run(capsys, "start", "--build", "3")
    assert code == 0
    assert stub.http == []
    assert "Full refresh ON" in out
    assert "switch Full refresh back OFF before build 4" in out
    assert stub.state()["builds"]["3"]["mode"] == "manual"


def test_start_without_a_proven_rest_form_prints_the_portal_settings(stub: Stub, capsys) -> None:
    code, out, _ = run(capsys, "start", "--build", "1")
    assert code == 0
    assert stub.http == []
    assert (
        "operation build, selector fabric_fixture, threads 1, Seed data ticked, Full refresh OFF"
        in out
    )
    assert stub.state()["builds"]["1"] == {"started_at": "2026-11-25T09:00:00Z", "mode": "manual"}


def test_start_keeps_the_other_builds_in_state(stub: Stub, capsys) -> None:
    run(capsys, "start", "--build", "1")
    stub.now = STARTED + timedelta(hours=1)
    run(capsys, "start", "--build", "2")
    builds = stub.state()["builds"]
    assert set(builds) == {"1", "2"}
    for entry in builds.values():
        assert tuple(entry) == verify_fabric.STATE_BUILD_KEYS or set(entry) == set(
            verify_fabric.STATE_BUILD_KEYS
        )
    assert set(stub.state()) == {"builds"}


def test_start_build_4_reminds_to_land_the_variant(stub: Stub, capsys) -> None:
    _, out, _ = run(capsys, "start", "--build", "4")
    assert "land-fixture --variant" in out


# ---------------------------------------------------------------------------------------------
# wait


def test_wait_writes_build_n_from_the_hook_rows(stub: Stub, capsys) -> None:
    started(stub, 1)
    stub.instances = [[instance()]]
    stub.answer(invocations=[invocation_row()], run_results=RESULT_ROWS)
    code, out, _ = run(capsys, "wait", "--build", "1")
    assert code == 0, out
    record = stub.build(1)
    assert tuple(record) == verify_fabric.BUILD_KEYS
    assert tuple(record["invocation"]) == verify_fabric.INVOCATION_KEYS
    assert all(tuple(node) == verify_fabric.NODE_KEYS for node in record["nodes"])
    assert record["results_source"] == "hook"
    assert record["build"] == 1 and record["mode"] == "manual"
    assert record["full_refresh_expected"] is False
    assert record["job_status"] == "Completed" and record["failure_code"] is None
    assert record["invocation"] == {
        "dbt_version": "1.11.8",
        "engine_version": "4.1.1 c9df16047cb2570aec895cd4718f04025913e19f",
        "session_time_zone": "UTC",
        "command": "build",
        "full_refresh": False,
    }
    assert record["status_counts"] == {
        "model": {"success": 1},
        "seed": {"success": 1},
        "test": {"warn": 1},
        "unit_test": {"pass": 1},
    }
    warn = next(node for node in record["nodes"] if node["status"] == "warn")
    assert warn["failures"] == 1 and warn["execution_time"] == 3.03
    seed = next(node for node in record["nodes"] if node["resource_type"] == "seed")
    assert seed["failures"] is None
    text = (stub.root / "adhoc" / "build-1.json").read_text()
    assert not any(value in text for value in IDS)
    assert "http" not in text


def test_wait_matches_the_invocation_by_the_instance_times_not_the_mac_clock(stub, capsys):
    # The Mac clock is hours off: the window still comes from Fabric's instance times.
    verify_fabric.record_start("adhoc", 1, STARTED - timedelta(hours=5), "manual")
    stub.now = STARTED + timedelta(hours=7)
    stub.instances = [[instance()]]
    stub.answer(invocations=[invocation_row()], run_results=RESULT_ROWS)
    code, _, _ = run(capsys, "wait", "--build", "1")
    assert code == 0
    sql = stub.queries[0]["invocations"]
    assert "recorded_at >= cast('2026-11-25 08:59:10' as timestamp)" in sql
    assert "recorded_at <= cast('2026-11-25 09:24:40' as timestamp)" in sql
    assert "invocation_id in (select invocation_id" in stub.queries[0]["run_results"]
    assert "message" not in stub.queries[0]["run_results"]


def test_wait_picks_only_an_instance_started_after_the_recorded_start(stub, capsys) -> None:
    started(stub, 2)
    old = instance(start="2026-11-25T08:40:00.1", end="2026-11-25T08:55:00.1")
    stub.instances = [[old], [old, instance("InProgress")], [old, instance()]]
    stub.answer(invocations=[invocation_row()], run_results=RESULT_ROWS)
    code, _, _ = run(capsys, "wait", "--build", "2")
    assert code == 0
    assert stub.sleeps.count(verify_fabric.POLL_SECONDS) == 2
    assert "09:24:40" in stub.queries[0]["invocations"]


def test_wait_exits_75_while_the_run_is_still_running(stub: Stub, capsys) -> None:
    started(stub, 1)
    stub.instances = [[instance("InProgress", end=None)]]
    code, out, _ = run(capsys, "wait", "--build", "1", "--timeout-minutes", "2")
    assert code == verify_fabric.EXIT_STILL_RUNNING == 75
    assert "InProgress" in out
    assert stub.queries == []
    assert not (stub.root / "adhoc" / "build-1.json").exists()


def test_wait_exits_75_when_no_run_has_started(stub: Stub, capsys) -> None:
    started(stub, 1)
    stub.instances = [[]]
    code, out, _ = run(capsys, "wait", "--build", "1", "--timeout-minutes", "1")
    assert code == 75
    assert "has not started" in out


def test_wait_exits_1_on_a_failed_instance_and_still_writes_evidence(stub, capsys) -> None:
    started(stub, 1)
    stub.instances = [[instance("Failed", failureReason={"errorCode": "Failed"})]]
    stub.answer(invocations=[invocation_row()], run_results=RESULT_ROWS)
    code, _, err = run(capsys, "wait", "--build", "1")
    assert code == 1
    assert "ended Failed (Failed)" in err
    record = stub.build(1)
    assert record["job_status"] == "Failed" and record["failure_code"] == "Failed"


def test_wait_exits_1_when_two_invocation_rows_fall_in_the_window(stub, capsys) -> None:
    started(stub, 1)
    stub.instances = [[instance()]]
    stub.answer(
        invocations=[invocation_row(), invocation_row(invocation_id=OTHER_INVOCATION)],
        run_results=RESULT_ROWS,
    )
    code, _, err = run(capsys, "wait", "--build", "1")
    assert code == 1
    assert "2 verification.invocations rows" in err
    assert not (stub.root / "adhoc" / "build-1.json").exists()


@pytest.mark.parametrize(("build", "actual"), [(3, "false"), (4, "true")])
def test_wait_flags_a_full_refresh_mismatch_and_still_writes_the_file(
    stub: Stub, capsys, build: int, actual: str
) -> None:
    started(stub, build)
    stub.instances = [[instance()]]
    stub.answer(invocations=[invocation_row(actual)], run_results=RESULT_ROWS)
    code, _, err = run(capsys, "wait", "--build", str(build))
    assert code == 1
    expected = "true" if build == 3 else "false"
    assert (
        f"build {build} ran with full_refresh {actual}, expected {expected}: check the job's Full "
        f"refresh switch and rerun build {build}"
    ) in err
    record = stub.build(build)
    assert record["full_refresh_expected"] is (build == 3)


def test_wait_on_build_3_with_full_refresh_true_passes(stub: Stub, capsys) -> None:
    started(stub, 3)
    stub.instances = [[instance()]]
    stub.answer(invocations=[invocation_row("true")], run_results=RESULT_ROWS)
    code, _, _ = run(capsys, "wait", "--build", "3")
    assert code == 0
    assert stub.build(3)["invocation"]["full_refresh"] is True


def test_wait_removes_a_stale_build_file_before_polling(stub: Stub, capsys) -> None:
    started(stub, 1)
    stale = stub.root / "adhoc" / "build-1.json"
    stale.write_text('{"stale": true}')
    stub.instances = [[instance("InProgress", end=None)]]
    code, _, _ = run(capsys, "wait", "--build", "1", "--timeout-minutes", "1")
    assert code == 75
    assert not stale.exists()


def test_wait_without_a_recorded_start_exits_2(stub: Stub, capsys) -> None:
    code, _, err = run(capsys, "wait", "--build", "2")
    assert code == 2
    assert "run start --build 2" in err
    assert stub.http == []


def test_wait_waits_for_the_capacity_before_the_query(stub: Stub, capsys) -> None:
    started(stub, 1)
    stub.instances = [[instance()]]
    stub.answer(invocations=[invocation_row()], run_results=RESULT_ROWS)
    run(capsys, "wait", "--build", "1")
    livy = [i for i, (_, url) in enumerate(stub.http) if url.startswith(LIVY_URL)]
    jobs = [i for i, (_, url) in enumerate(stub.http) if url.startswith(JOBS_URL)]
    assert livy and jobs and max(jobs) < min(livy)
    assert verify_fabric.CAPACITY_SETTLE_SECONDS in stub.sleeps


def test_the_build_record_writer_refuses_another_key_set(stub: Stub) -> None:
    record = dict.fromkeys(verify_fabric.BUILD_KEYS)
    record.update(
        build=1,
        results_source="hook",
        invocation=dict.fromkeys(verify_fabric.INVOCATION_KEYS),
        nodes=[],
    )
    record["extra"] = 1
    with pytest.raises(ValueError, match="pinned keys"):
        verify_fabric.write_build("adhoc", record)


def test_the_pinned_vocabularies() -> None:
    assert verify_fabric.NODE_OK_STATUSES == ("success", "pass", "warn")
    assert verify_fabric.REQUIRED_JOB_ENV == ("FABRIC_DBT_JOB_ID",)
    assert verify_fabric.FULL_REFRESH_BUILD == 3
    assert verify_fabric.EXIT_STILL_RUNNING == 75
    assert verify_fabric.RESULTS_SOURCES == ("hook", "log")


# ---------------------------------------------------------------------------------------------
# The build-4 variant (D-09)


def test_the_variant_is_file_2026_09_plus_the_added_column(tmp_path: Path) -> None:
    import duckdb

    from starter_cli.nyc_taxi import fixture

    path = fixture.write_added_column_variant(tmp_path / "variant")
    assert path == tmp_path / "variant" / "yellow_tripdata_2026-09.parquet"
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]
    base = tmp_path / "base.parquet"
    fixture._write_trip_parquet(base, fixture.FILE_2026_09)
    con = duckdb.connect()
    try:
        columns = con.execute(f"describe select * from '{path}'").fetchall()
        names = [row[0] for row in columns]
        published = [spelled for _, spelled, _ in fixture.FILE_2026_09.columns]
        assert names == [*published, fixture.ADDED_COLUMN]
        assert {row[0]: row[1] for row in columns}[fixture.ADDED_COLUMN] == "VARCHAR"
        variant_rows = con.execute(
            f"select * exclude ({fixture.ADDED_COLUMN}) from '{path}' order by all"
        ).fetchall()
        base_rows = con.execute(f"select * from '{base}' order by all").fetchall()
        assert variant_rows == base_rows
        assert len(variant_rows) == len(fixture.FILE_2026_09.rows)
        notes = con.execute(f"select {fixture.ADDED_COLUMN} from '{path}'").fetchall()
        assert all(isinstance(note, str) and note for (note,) in notes)
    finally:
        con.close()


def test_the_variant_changes_no_base_fixture_file() -> None:
    from starter_cli.nyc_taxi import fixture

    assert fixture.ADDED_COLUMN == "added_note"
    assert len(fixture.TRIP_FILES) == 5
    for file in fixture.TRIP_FILES:
        assert fixture.ADDED_COLUMN not in {name for name, _, _ in file.columns}


def test_land_fixture_variant_lands_only_the_2026_09_file(stub: Stub, capsys) -> None:
    code, out, _ = run(capsys, "land-fixture", "--variant")
    assert code == 0
    folder = stub.root / "adhoc" / "variant"
    assert stub.loads == [
        {
            "table": "nyc_taxi__yellow_trips",
            "months": ["2026-09"],
            "from_dir": folder,
            "sink": ("sink",),
        }
    ]
    assert sorted(p.name for p in folder.iterdir()) == ["yellow_tripdata_2026-09.parquet"]
    assert "landed 7 rows into landing.nyc_taxi__yellow_trips" in out


# ---------------------------------------------------------------------------------------------
# The D-10 log fallback

ESC = "\x1b"
# A synthetic dbt console log: invented node names, a timestamp prefix, colour codes on some
# lines, START lines that must be ignored, and no real id.
SAMPLE_LOG = "\n".join(
    [
        "09:01:00  Running with dbt=1.11.8",
        (
            "09:01:00  running dbt with arguments {'printer_width': '80', 'which': 'build', "
            "'full_refresh': 'False', 'log_format': 'default'}"
        ),
        "09:01:02  Registered adapter: fabricspark=1.12.2",
        "09:01:05  1 of 7 START seed file bronze.alpha ...................... [RUN]",
        "09:01:06  1 of 7 OK loaded seed file bronze.alpha ................. [INSERT 10 in 1.50s]",
        "09:01:07  2 of 7 OK loaded seed file bronze.beta .................. [INSERT 4 in 1.20s]",
        (
            f"09:01:08  {ESC}[32m3 of 7 OK created sql table model silver.gamma{ESC}[0m ..... "
            f"[{ESC}[32mOK{ESC}[0m in 2.00s]"
        ),
        "09:01:09  4 of 7 OK created sql table model gold.delta ............ [OK in 3.25s]",
        "09:01:10  5 of 7 PASS not_null_delta_id ........................... [PASS in 0.50s]",
        (
            f"09:01:11  {ESC}[33m6 of 7 WARN 1 delta_undeclared_columns{ESC}[0m ........ "
            f"[{ESC}[33mWARN 1{ESC}[0m in 0.40s]"
        ),
        "09:01:12  7 of 7 PASS delta::checks_the_rules ...................... [PASS in 0.30s]",
        "09:01:13  Completed with 1 warning:",
        "09:01:13  Done. PASS=6 WARN=1 ERROR=0 SKIP=0 TOTAL=7",
    ]
)

LOG_MANIFEST = {
    "seed.proj.alpha": {"resource_type": "seed", "log_name": "bronze.alpha"},
    "seed.proj.beta": {"resource_type": "seed", "log_name": "bronze.beta"},
    "model.proj.gamma": {"resource_type": "model", "log_name": "silver.gamma"},
    "model.proj.delta": {"resource_type": "model", "log_name": "gold.delta"},
    "test.proj.not_null_delta_id.1a": {"resource_type": "test", "log_name": "not_null_delta_id"},
    "test.proj.delta_undeclared_columns.2b": {
        "resource_type": "test",
        "log_name": "delta_undeclared_columns",
    },
    "unit_test.proj.delta.checks_the_rules": {
        "resource_type": "unit_test",
        "log_name": "delta::checks_the_rules",
    },
}


def write_log(folder: Path, text: str, name: str = "dbt.log") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text, encoding="utf-8")
    return folder


def test_log_sample_holds_no_uuid_shaped_text() -> None:
    uuid = re.compile(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
    )
    assert not uuid.search(SAMPLE_LOG)


def test_log_sample_parses_into_nodes_counts_and_invocation(tmp_path: Path) -> None:
    folder = write_log(tmp_path / "log", SAMPLE_LOG)
    invocation, counts, nodes = verify_fabric.parse_run_log(folder, LOG_MANIFEST)
    assert invocation == {
        "dbt_version": "1.11.8",
        "engine_version": None,
        "session_time_zone": None,
        "command": "build",
        "full_refresh": False,
    }
    assert {node["unique_id"] for node in nodes} == set(LOG_MANIFEST)
    assert all(tuple(node) == verify_fabric.NODE_KEYS for node in nodes)
    by_id = {node["unique_id"]: node for node in nodes}
    assert by_id["test.proj.delta_undeclared_columns.2b"]["status"] == "warn"
    assert by_id["test.proj.delta_undeclared_columns.2b"]["failures"] == 1
    assert by_id["model.proj.gamma"]["status"] == "success"
    assert by_id["model.proj.gamma"]["execution_time"] == 2.0
    assert by_id["unit_test.proj.delta.checks_the_rules"]["resource_type"] == "unit_test"
    assert all(
        node["failures"] is None
        for node in nodes
        if node["unique_id"] != "test.proj.delta_undeclared_columns.2b"
    )
    assert counts == {
        "model": {"success": 2},
        "seed": {"success": 2},
        "test": {"pass": 1, "warn": 1},
        "unit_test": {"pass": 1},
    }


def test_log_without_the_arguments_line_has_no_command_or_full_refresh(tmp_path: Path) -> None:
    text = "\n".join(line for line in SAMPLE_LOG.splitlines() if "with arguments" not in line)
    invocation, _, _ = verify_fabric.parse_run_log(write_log(tmp_path / "log", text), LOG_MANIFEST)
    assert invocation["command"] is None and invocation["full_refresh"] is None
    assert invocation["dbt_version"] == "1.11.8"


FAIL_ERROR_SKIP_LOG = (
    "Running with dbt=1.11.8",
    "1 of 4 OK loaded seed file bronze.alpha ...... [INSERT 10 in 1.50s]",
    "2 of 4 ERROR creating sql table model silver.gamma ...... [ERROR in 0.70s]",
    "3 of 4 SKIP relation gold.delta ....................... [SKIP]",
    "4 of 4 FAIL 3 not_null_delta_id ....................... [FAIL 3 in 0.20s]",
    "Done. PASS=1 WARN=0 ERROR=2 SKIP=1 NO-OP=0 TOTAL=4",
)


def test_log_fail_error_and_skip_map_to_their_statuses(tmp_path: Path) -> None:
    text = "\n".join(FAIL_ERROR_SKIP_LOG)
    _, counts, nodes = verify_fabric.parse_run_log(write_log(tmp_path / "log", text), LOG_MANIFEST)
    by_id = {node["unique_id"]: node for node in nodes}
    assert by_id["model.proj.gamma"]["status"] == "error"
    assert by_id["model.proj.delta"]["status"] == "skipped"
    assert by_id["model.proj.delta"]["execution_time"] == 0.0
    assert by_id["test.proj.not_null_delta_id.1a"]["status"] == "fail"
    assert by_id["test.proj.not_null_delta_id.1a"]["failures"] == 3
    assert counts["test"] == {"fail": 1}


def test_log_hook_lines_count_against_the_done_line_but_are_not_nodes(tmp_path: Path) -> None:
    # The native job writes JSON lines (dbt log_version 3); hooks count in dbt's Done line.
    def event(name: str, msg: str) -> str:
        return json.dumps({"info": {"name": name, "msg": msg, "level": "info"}, "data": {}})

    text = "\n".join(
        [
            event("MainReportVersion", "Running with dbt=1.11.8"),
            event("LogHookEndLine", "1 of 1 OK hook: proj.on-run-start.0 ...... [OK in 15.05s]"),
            event("LogSeedResult", "1 of 2 OK loaded seed file bronze.alpha .. [INSERT 1 in 1.0s]"),
            event("LogTestResult", "2 of 2 WARN 1 delta_undeclared_columns .... [WARN 1 in 3.03s]"),
            event("LogHookEndLine", "1 of 1 OK hook: proj.on-run-end.0 ...... [OK in 11.63s]"),
            event("StatsLine", "Done. PASS=3 WARN=1 ERROR=0 SKIP=0 NO-OP=0 TOTAL=4"),
        ]
    )
    invocation, counts, nodes = verify_fabric.parse_run_log(
        write_log(tmp_path / "log", text), LOG_MANIFEST
    )
    assert [node["unique_id"] for node in nodes] == [
        "seed.proj.alpha",
        "test.proj.delta_undeclared_columns.2b",
    ]
    assert counts == {"seed": {"success": 1}, "test": {"warn": 1}}
    assert invocation["dbt_version"] == "1.11.8"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda t: t.replace("bronze.beta", "bronze.unknown_relation"), "line 6"),
        (lambda t: t.replace("Done. PASS=6", "Done. PASS=5"), "PASS"),
        (lambda t: t.replace("TOTAL=7", "TOTAL=8"), "TOTAL"),
        (lambda t: "\n".join(t.splitlines()[:-1]), "Done line"),
    ],
)
def test_log_that_does_not_describe_the_run_raises(tmp_path: Path, change, reason) -> None:
    folder = write_log(tmp_path / "log", change(SAMPLE_LOG))
    with pytest.raises(verify_fabric.LogParseError, match=reason) as raised:
        verify_fabric.parse_run_log(folder, LOG_MANIFEST)
    assert "unknown_relation" not in str(raised.value)


def test_log_name_that_matches_two_manifest_nodes_raises(tmp_path: Path) -> None:
    manifest = {
        **LOG_MANIFEST,
        "snapshot.proj.alpha": {"resource_type": "snapshot", "log_name": "bronze.alpha"},
    }
    folder = write_log(tmp_path / "log", SAMPLE_LOG)
    with pytest.raises(verify_fabric.LogParseError, match="line 5") as raised:
        verify_fabric.parse_run_log(folder, manifest)
    assert "alpha" not in str(raised.value)


def test_log_files_that_disagree_raise(tmp_path: Path) -> None:
    folder = write_log(tmp_path / "log", SAMPLE_LOG, "one.log")
    other = SAMPLE_LOG.replace("[OK in 3.25s]", "[OK in 9.99s]")
    write_log(folder, other, "two.log")
    write_log(folder, "no done line here", "three.txt")
    with pytest.raises(verify_fabric.LogParseError, match="disagree"):
        verify_fabric.parse_run_log(folder, LOG_MANIFEST)


def test_log_files_that_agree_parse_once(tmp_path: Path) -> None:
    folder = write_log(tmp_path / "log", SAMPLE_LOG, "one.log")
    write_log(folder, SAMPLE_LOG, "two.log")
    _, _, nodes = verify_fabric.parse_run_log(folder, LOG_MANIFEST)
    assert len(nodes) == 7


def test_log_names_come_from_the_manifest() -> None:
    manifest = {
        "nodes": {
            "model.p.m": {"resource_type": "model", "schema": "gold", "alias": "fct", "name": "m"},
            "seed.p.s": {"resource_type": "seed", "schema": "bronze", "alias": None, "name": "s"},
            "test.p.t.1": {"resource_type": "test", "schema": "x", "name": "not_null_fct_id"},
            "operation.p.hook": {"resource_type": "operation", "name": "hook"},
        },
        "unit_tests": {"unit_test.p.m.case": {"name": "case", "model": "m"}},
    }
    assert verify_fabric.manifest_log_names(manifest) == {
        "model.p.m": {"resource_type": "model", "log_name": "gold.fct"},
        "seed.p.s": {"resource_type": "seed", "log_name": "bronze.s"},
        "test.p.t.1": {"resource_type": "test", "log_name": "not_null_fct_id"},
        "unit_test.p.m.case": {"resource_type": "unit_test", "log_name": "m::case"},
    }


@pytest.fixture
def log_wait(stub: Stub, monkeypatch: pytest.MonkeyPatch) -> Stub:
    """A Completed build 1 whose window holds no invocation row, with a stubbed manifest."""
    monkeypatch.setattr(verify_fabric, "MANIFEST_NODES", lambda label, root: LOG_MANIFEST)
    started(stub, 1)
    stub.instances = [[instance()]]
    stub.answer(invocations=[], run_results=[])
    return stub


def test_log_fallback_writes_build_n_with_results_source_log(log_wait: Stub, capsys) -> None:
    write_log(log_wait.root / "adhoc" / "build-1-log", SAMPLE_LOG)
    code, out, _ = run(capsys, "wait", "--build", "1")
    assert code == 0, out
    record = log_wait.build(1)
    assert tuple(record) == verify_fabric.BUILD_KEYS
    assert tuple(record["invocation"]) == verify_fabric.INVOCATION_KEYS
    assert record["results_source"] == "log"
    assert record["invocation"]["engine_version"] is None
    assert len(record["nodes"]) == 7


def test_log_fallback_without_full_refresh_is_a_mismatch(log_wait: Stub, capsys) -> None:
    text = "\n".join(line for line in SAMPLE_LOG.splitlines() if "with arguments" not in line)
    write_log(log_wait.root / "adhoc" / "build-1-log", text)
    code, _, err = run(capsys, "wait", "--build", "1")
    assert code == 1
    assert "build 1 ran with full_refresh null, expected false" in err
    assert log_wait.build(1)["results_source"] == "log"


def test_log_fallback_without_a_log_folder_writes_nothing(log_wait: Stub, capsys) -> None:
    code, _, err = run(capsys, "wait", "--build", "1")
    assert code == 1
    assert (
        "build 1 wrote no hook row; download its logs in the portal (the dbt job, this run, "
        "Download Logs) into target/verify-fabric/adhoc/build-1-log/ and rerun wait --build 1"
    ) in err
    assert not (log_wait.root / "adhoc" / "build-1.json").exists()


def test_log_fallback_with_an_unparseable_log_writes_nothing(log_wait: Stub, capsys) -> None:
    write_log(log_wait.root / "adhoc" / "build-1-log", SAMPLE_LOG.replace("TOTAL=7", "TOTAL=9"))
    code, _, err = run(capsys, "wait", "--build", "1")
    assert code == 1
    assert "build 1's log does not parse: " in err
    assert not (log_wait.root / "adhoc" / "build-1.json").exists()

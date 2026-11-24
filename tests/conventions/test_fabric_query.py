"""`fabric_query`: read named SQL through `dbt run-operation starter.query_json` (D-10, D-12).

Every live check reads its counts back from the Lakehouse, so the reading path is one small bridge:
a macro prints one sentinel line of JSON per named query and Python parses those lines. The parse
and the argument rules are tested here with a recording runner. One case runs real dbt on a
temporary DuckDB file, so the macro itself is proven offline, before any Lakehouse is involved.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from support.dbt_cli import REPO_ROOT

from starter_cli import fabric_query


def _line(name: str, rows: list[list[str | None]], columns: list[str] | None = None) -> str:
    payload = {"name": name, "columns": columns or ["c"], "rows": rows}
    return f"{fabric_query.SENTINEL} {json.dumps(payload)}"


def test_the_sentinel_is_the_documented_prefix() -> None:
    assert fabric_query.SENTINEL == "@@STARTER_JSON@@"


def test_parse_output_finds_lines_behind_any_prefix_and_ignores_the_rest() -> None:
    text = "\n".join(
        [
            "11:33:12  Running with dbt=1.11.17",
            "11:33:13  " + _line("one", [["1", None]]),
            "unrelated line",
            _line("two", [["a"], ["b"]]),
            "",
        ]
    )
    assert fabric_query.parse_output(text) == {"one": [["1", None]], "two": [["a"], ["b"]]}


def test_parse_output_rejects_malformed_json() -> None:
    with pytest.raises(fabric_query.FabricQueryError, match="malformed"):
        fabric_query.parse_output(f"{fabric_query.SENTINEL} {{not json")


def test_parse_output_rejects_a_duplicate_name() -> None:
    text = _line("one", [["1"]]) + "\n" + _line("one", [["2"]])
    with pytest.raises(fabric_query.FabricQueryError, match="one"):
        fabric_query.parse_output(text)


def test_parse_output_rejects_a_line_without_a_name_or_rows() -> None:
    with pytest.raises(fabric_query.FabricQueryError, match="malformed"):
        fabric_query.parse_output(f'{fabric_query.SENTINEL} {{"name": "x"}}')


@pytest.mark.parametrize("name", ["One", "1one", "a-b", "a b", "", "a;b", "a'b"])
def test_a_bad_query_name_is_rejected_before_dbt_runs(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        fabric_query, "RUNNER", lambda command, _root, _env: calls.append(command) or (0, "")
    )
    with pytest.raises(fabric_query.FabricQueryError, match="name"):
        fabric_query.run_queries({name: "select 1"})
    assert calls == []


def test_run_queries_builds_one_run_operation_call_for_the_whole_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], Path, dict[str, str]]] = []

    def runner(command: list[str], root: Path, env: dict[str, str]) -> tuple[int, str]:
        calls.append((command, root, env))
        return 0, _line("a", [["1"]]) + "\n" + _line("b", [["2"]])

    monkeypatch.setattr(fabric_query, "RUNNER", runner)
    result = fabric_query.run_queries(
        {"a": "select 1", "b": "select 2"}, target="fabric", env={"EXTRA": "x"}
    )

    assert result == {"a": [["1"]], "b": [["2"]]}
    assert len(calls) == 1
    command, root, env = calls[0]
    assert root == REPO_ROOT
    assert env["EXTRA"] == "x"
    assert command[1:4] == ["-m", "dbt.cli.main", "run-operation"]
    assert command[4] == "starter.query_json"
    assert command[command.index("--target") + 1] == "fabric"
    assert command[command.index("--profiles-dir") + 1] == "."
    assert command[command.index("--project-dir") + 1] == "."
    args = json.loads(command[command.index("--args") + 1])
    assert args == {"queries": {"a": "select 1", "b": "select 2"}}


def test_run_queries_names_the_exit_code_when_dbt_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fabric_query, "RUNNER", lambda *_: (2, "boom"))
    with pytest.raises(fabric_query.FabricQueryError, match="exit code 2"):
        fabric_query.run_queries({"a": "select 1"})


def test_run_queries_names_a_missing_query(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fabric_query, "RUNNER", lambda *_: (0, _line("a", [["1"]])))
    with pytest.raises(fabric_query.FabricQueryError, match="missing"):
        fabric_query.run_queries({"a": "select 1", "later": "select 2"})
    # The message names the query that is missing, never its SQL.
    with pytest.raises(fabric_query.FabricQueryError) as info:
        fabric_query.run_queries({"a": "select 1", "later": "select 2 as secret_marker"})
    assert "later" in str(info.value)
    assert "secret_marker" not in str(info.value)


def test_the_macro_runs_on_duckdb_and_returns_every_value_as_text(
    dbt_packages_ready: None, tmp_path: Path
) -> None:
    database = tmp_path / "bridge.duckdb"
    result = fabric_query.run_queries(
        {
            "one": "select 1 as n, cast(null as varchar) as m",
            "quote": "select 'it''s' as s",
            "two_rows": "select * from (values (1), (2)) as t(v) order by v",
        },
        target="duckdb",
        env={"DBT_DUCKDB_PATH": str(database)},
    )
    assert result == {"one": [["1", None]], "quote": [["it's"]], "two_rows": [["1"], ["2"]]}


def test_a_percent_sign_in_the_sql_is_rejected_before_dbt_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        fabric_query, "RUNNER", lambda command, _root, _env: calls.append(command) or (0, "")
    )
    with pytest.raises(fabric_query.FabricQueryError, match="percent"):
        fabric_query.run_queries({"a": "select 'x' like '%y'"})
    assert calls == []

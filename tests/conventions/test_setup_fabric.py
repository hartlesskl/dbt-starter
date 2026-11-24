"""`setup-fabric`: find or create the schema-enabled Lakehouse, with the HTTP layer stubbed (D-18).

These tests replace `fabric_rest.HTTP`, `TOKEN` and `SLEEP`, so they never reach the network, never
need `az login` and never sleep. They hold the contract of ADR 0006 decision 8: one stdout line
`FABRIC_LAKEHOUSE_ID=<id>`, idempotent, only GET and POST ever sent, a refusal (exit 2, no POST)
for an unusable existing Lakehouse or a bad input, and no id or name on stderr (T-05-01, T-05-02,
T-05-04).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any

import pytest

from starter_cli import fabric_rest, setup_fabric

WORKSPACE_ID = "aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb"
LAKEHOUSE_ID = "cccccccc-4444-5555-6666-dddddddddddd"
OTHER_ID = "eeeeeeee-7777-8888-9999-ffffffffffff"
OPERATION_ID = "12121212-3434-5656-7878-909090909090"
NAME = "lake_secret_name"

Call = tuple[str, str, Mapping[str, str], dict[str, Any] | None]
Handler = Callable[[str, str, dict[str, Any] | None], fabric_rest.HttpResponse]

BASE = f"{fabric_rest.FABRIC_API_BASE}/workspaces/{WORKSPACE_ID}/lakehouses"


def reply(status: int, body: dict[str, Any] | None = None, **headers: str):
    return fabric_rest.HttpResponse(
        status, {k.replace("_", "-"): v for k, v in headers.items()}, body
    )


def item(lakehouse_id: str = LAKEHOUSE_ID, name: str = NAME, *, schemas: bool = True):
    properties: dict[str, Any] = {"defaultSchema": "dbo"} if schemas else {}
    return {"id": lakehouse_id, "displayName": name, "properties": properties}


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch):
    """A recorder for every HTTP call, a recorder for sleeps and a valid environment."""
    for key in [key for key in os.environ if key.startswith("FABRIC_")]:
        monkeypatch.delenv(key)
    monkeypatch.setenv("FABRIC_WORKSPACE_ID", WORKSPACE_ID)
    monkeypatch.setenv("FABRIC_LAKEHOUSE", NAME)
    monkeypatch.setattr(setup_fabric, "azure_identity_installed", lambda: True)
    calls: list[Call] = []
    sleeps: list[float] = []
    handlers: list[Handler] = []

    def http(method, url, headers, json_body):
        calls.append((method, url, headers, json_body))
        for handler in handlers:
            response = handler(method, url, json_body)
            if response is not None:
                return response
        raise AssertionError(f"unexpected request: {method} {url}")

    monkeypatch.setattr(fabric_rest, "HTTP", http)
    monkeypatch.setattr(fabric_rest, "TOKEN", lambda: "token-value")
    monkeypatch.setattr(fabric_rest, "SLEEP", sleeps.append)

    class Stub:
        pass

    result = Stub()
    result.calls = calls
    result.sleeps = sleeps
    result.on = handlers.append
    result.methods = lambda: [call[0] for call in calls]
    return result


def run(capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = setup_fabric.main([])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def lakehouse_lists(*pages: dict[str, Any]):
    """A handler serving the list call page by page (a page is a body)."""
    served = iter(pages)

    def handler(method, url, _body):
        if method == "GET" and url.startswith(BASE) and url.count("/") == BASE.count("/"):
            return reply(200, next(served))
        return None

    return handler


def get_one(lakehouse: dict[str, Any]):
    def handler(method, url, _body):
        if method == "GET" and url == f"{BASE}/{lakehouse['id']}":
            return reply(200, lakehouse)
        return None

    return handler


def assert_no_secret_on_stderr(err: str) -> None:
    for secret in (WORKSPACE_ID, LAKEHOUSE_ID, OTHER_ID, NAME, "token-value"):
        assert secret not in err


def test_an_existing_schema_enabled_lakehouse_is_found_without_a_post(stub, capsys) -> None:
    stub.on(lakehouse_lists({"value": [item(OTHER_ID, "other"), item()]}))
    stub.on(get_one(item()))
    code, out, err = run(capsys)
    assert code == 0
    assert out == f"FABRIC_LAKEHOUSE_ID={LAKEHOUSE_ID}\n"
    assert "setup-fabric: found the Lakehouse" in err
    assert "POST" not in stub.methods()
    assert_no_secret_on_stderr(err)


def test_a_second_run_prints_the_same_id_and_sends_no_post(stub, capsys) -> None:
    stub.on(lakehouse_lists({"value": [item()]}, {"value": [item()]}))
    stub.on(get_one(item()))
    first = run(capsys)
    second = run(capsys)
    assert first[:2] == second[:2] == (0, f"FABRIC_LAKEHOUSE_ID={LAKEHOUSE_ID}\n")
    assert "POST" not in stub.methods()


def test_an_existing_lakehouse_without_schemas_is_refused_and_nothing_is_posted(
    stub, capsys
) -> None:
    stub.on(lakehouse_lists({"value": [item(schemas=False)]}))
    stub.on(get_one(item(schemas=False)))
    code, out, err = run(capsys)
    assert code == 2
    assert out == ""
    assert "exists without schemas; choose another name" in err
    assert "POST" not in stub.methods()
    assert_no_secret_on_stderr(err)


def test_a_name_that_differs_only_in_case_is_refused_and_nothing_is_posted(stub, capsys) -> None:
    stub.on(lakehouse_lists({"value": [item(name=NAME.upper())]}))
    code, out, err = run(capsys)
    assert code == 2
    assert out == ""
    assert "only in case" in err
    assert stub.methods() == ["GET"]
    assert_no_secret_on_stderr(err)


def test_a_missing_lakehouse_is_created_with_schemas_enabled(stub, capsys) -> None:
    stub.on(lakehouse_lists({"value": [item(OTHER_ID, "other")]}))
    stub.on(lambda m, u, b: reply(201, item()) if m == "POST" and u == BASE else None)
    stub.on(get_one(item()))
    code, out, err = run(capsys)
    assert code == 0
    assert out == f"FABRIC_LAKEHOUSE_ID={LAKEHOUSE_ID}\n"
    assert "setup-fabric: created the Lakehouse" in err
    posts = [call for call in stub.calls if call[0] == "POST"]
    assert len(posts) == 1
    assert posts[0][3] == {"displayName": NAME, "creationPayload": {"enableSchemas": True}}
    assert posts[0][2]["Authorization"] == "Bearer token-value"
    assert_no_secret_on_stderr(err)


def test_a_created_lakehouse_that_reports_no_schemas_is_an_error(stub, capsys) -> None:
    stub.on(lakehouse_lists({"value": []}))
    stub.on(lambda m, u, b: reply(201, item(schemas=False)) if m == "POST" else None)
    stub.on(get_one(item(schemas=False)))
    code, out, err = run(capsys)
    assert code == 1
    assert out == ""
    assert "does not report schemas" in err


def test_a_202_is_polled_with_retry_after_and_the_result_is_read(stub, capsys) -> None:
    polls = iter(
        [
            reply(200, {"status": "Running"}, retry_after="7"),
            reply(200, {"status": "Succeeded"}),
        ]
    )
    stub.on(lakehouse_lists({"value": []}))
    stub.on(
        lambda m, u, b: (
            reply(202, None, x_ms_operation_id=OPERATION_ID, retry_after="3")
            if m == "POST"
            else None
        )
    )
    stub.on(
        lambda m, u, b: (
            next(polls)
            if m == "GET" and u == f"{fabric_rest.FABRIC_API_BASE}/operations/{OPERATION_ID}"
            else None
        )
    )
    stub.on(
        lambda m, u, b: (
            reply(200, item())
            if m == "GET" and u == f"{fabric_rest.FABRIC_API_BASE}/operations/{OPERATION_ID}/result"
            else None
        )
    )
    stub.on(get_one(item()))
    code, out, _err = run(capsys)
    assert code == 0
    assert out == f"FABRIC_LAKEHOUSE_ID={LAKEHOUSE_ID}\n"
    assert stub.sleeps == [3.0, 7.0]
    assert set(stub.methods()) <= {"GET", "POST"}


def test_a_retry_after_is_capped_at_thirty_seconds() -> None:
    assert fabric_rest._wait_seconds({"retry-after": "900"}) == 30.0
    assert fabric_rest._wait_seconds({}) == 5.0
    assert fabric_rest._wait_seconds({"retry-after": "soon"}) == 5.0


def test_a_failed_operation_exits_1_with_the_error_code_only(stub, capsys) -> None:
    stub.on(lakehouse_lists({"value": []}))
    stub.on(
        lambda m, u, b: reply(202, None, x_ms_operation_id=OPERATION_ID) if m == "POST" else None
    )
    stub.on(
        lambda m, u, b: (
            reply(
                200,
                {"status": "Failed", "error": {"errorCode": "CapacityNotActive", "message": NAME}},
            )
            if m == "GET" and u.endswith(f"/operations/{OPERATION_ID}")
            else None
        )
    )
    code, out, err = run(capsys)
    assert code == 1
    assert out == ""
    assert "setup-fabric: Fabric API 200 CapacityNotActive" in err
    assert_no_secret_on_stderr(err)


def test_an_api_error_exits_1_with_the_status_and_code_only(stub, capsys) -> None:
    stub.on(lambda m, u, b: reply(403, {"errorCode": "InsufficientPrivileges", "message": NAME}))
    code, out, err = run(capsys)
    assert code == 1
    assert out == ""
    assert err.strip() == "setup-fabric: Fabric API 403 InsufficientPrivileges"


def test_the_list_follows_the_continuation_token_across_pages(stub, capsys) -> None:
    pages = iter(
        [
            {"value": [item(OTHER_ID, "other")], "continuationToken": "page two/token"},
            {"value": [item()]},
        ]
    )
    urls: list[str] = []

    def handler(method, url, _body):
        if method == "GET" and url.startswith(BASE) and url.count("/") == BASE.count("/"):
            urls.append(url)
            return reply(200, next(pages))
        return None

    stub.on(handler)
    stub.on(get_one(item()))
    code, out, _err = run(capsys)
    assert code == 0
    assert out == f"FABRIC_LAKEHOUSE_ID={LAKEHOUSE_ID}\n"
    assert urls == [BASE, f"{BASE}?continuationToken=page%20two%2Ftoken"]


@pytest.mark.parametrize(
    ("variable", "value", "message"),
    [
        ("FABRIC_WORKSPACE_ID", "not-a-uuid", "FABRIC_WORKSPACE_ID is not a UUID"),
        ("FABRIC_WORKSPACE_ID", "", "FABRIC_WORKSPACE_ID is not set"),
        ("FABRIC_LAKEHOUSE", "has space", "FABRIC_LAKEHOUSE must start with a letter"),
        ("FABRIC_LAKEHOUSE", 'say"quote', "FABRIC_LAKEHOUSE must start with a letter"),
        ("FABRIC_LAKEHOUSE", "1starts_with_digit", "FABRIC_LAKEHOUSE must start with a letter"),
        ("FABRIC_LAKEHOUSE", "a" * 257, "FABRIC_LAKEHOUSE must start with a letter"),
        ("FABRIC_LAKEHOUSE", "DBO", "FABRIC_SCHEMA must differ from FABRIC_LAKEHOUSE"),
    ],
)
def test_a_bad_input_exits_2_before_any_http_call(
    variable, value, message, stub, monkeypatch, capsys
) -> None:
    monkeypatch.setenv(variable, value)
    code, out, err = run(capsys)
    assert code == 2
    assert out == ""
    assert message in err
    assert stub.calls == []
    if value:
        assert value not in err


def test_the_values_are_stripped_before_they_are_checked(stub, monkeypatch, capsys) -> None:
    monkeypatch.setenv("FABRIC_WORKSPACE_ID", f"  {WORKSPACE_ID}\n")
    monkeypatch.setenv("FABRIC_LAKEHOUSE", f" {NAME} ")
    stub.on(lakehouse_lists({"value": [item()]}))
    stub.on(get_one(item()))
    code, out, _err = run(capsys)
    assert (code, out) == (0, f"FABRIC_LAKEHOUSE_ID={LAKEHOUSE_ID}\n")


def test_without_the_azure_library_the_hint_names_the_locked_command(
    stub, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(setup_fabric, "azure_identity_installed", lambda: False)
    code, out, err = run(capsys)
    assert code == 2
    assert out == ""
    assert setup_fabric.SETUP_FABRIC_COMMAND in err
    assert stub.calls == []


def test_a_missing_az_login_exits_1_with_a_fixed_message(stub, monkeypatch, capsys) -> None:
    def no_token() -> str:
        raise fabric_rest.FabricAuthError("could not get a token from az login; run `az login`")

    monkeypatch.setattr(fabric_rest, "TOKEN", no_token)
    code, out, err = run(capsys)
    assert code == 1
    assert out == ""
    assert "az login" in err
    assert stub.calls == []


def test_a_network_failure_exits_1_without_the_url(stub, monkeypatch, capsys) -> None:
    def broken(method, url, headers, json_body):
        raise ConnectionError(url)

    monkeypatch.setattr(fabric_rest, "HTTP", broken)
    code, out, err = run(capsys)
    assert code == 1
    assert out == ""
    assert err.strip() == "setup-fabric: could not reach the Fabric API"


def test_only_get_and_post_are_ever_sent_and_the_host_is_the_fabric_api(stub, capsys) -> None:
    stub.on(lakehouse_lists({"value": []}))
    stub.on(lambda m, u, b: reply(201, item()) if m == "POST" else None)
    stub.on(get_one(item()))
    run(capsys)
    assert set(stub.methods()) <= {"GET", "POST"}
    assert all(call[1].startswith("https://api.fabric.microsoft.com/v1/") for call in stub.calls)


def test_a_bad_id_never_enters_a_url_and_the_error_names_the_argument_only() -> None:
    with pytest.raises(ValueError) as raised:
        fabric_rest.get_lakehouse(WORKSPACE_ID, "../../etc")
    assert str(raised.value) == "lakehouse id is not a UUID"
    with pytest.raises(ValueError) as raised:
        fabric_rest.list_lakehouses("x/y")
    assert str(raised.value) == "workspace id is not a UUID"


def test_schema_enabled_means_a_default_schema_property() -> None:
    assert fabric_rest.is_schema_enabled(item())
    assert not fabric_rest.is_schema_enabled(item(schemas=False))
    assert not fabric_rest.is_schema_enabled({"id": LAKEHOUSE_ID})


def test_help_exits_zero_and_names_the_variables(capsys) -> None:
    with pytest.raises(SystemExit) as raised:
        setup_fabric.main(["--help"])
    assert raised.value.code == 0
    out = capsys.readouterr().out
    assert "FABRIC_LAKEHOUSE" in out
    assert "FABRIC_WORKSPACE_ID" in out

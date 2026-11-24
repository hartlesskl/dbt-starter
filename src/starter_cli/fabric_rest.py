"""A small Fabric REST client for `setup-fabric` (ADR 0006 decision 8, D-18).

It does the one thing the setup command needs: list, read and create Lakehouses in a workspace,
with the token from `az login`. Three module-level seams (`HTTP`, `TOKEN`, `SLEEP`) are what the
tests replace, so no test ever reaches the network.

Nothing here prints. Errors carry the HTTP status and Fabric's `errorCode` and nothing else, so a
message never holds a workspace id, a Lakehouse name or a token (T-05-01). The API host is a
constant and is never read from the environment (T-05-03). Every id is checked against the UUID
rule before it enters a URL path, and a bad id raises ValueError naming the argument, never the
value (T-05-02).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any, NamedTuple
from urllib.parse import quote

from starter_cli.build_fabric import UUID_RE

FABRIC_API_BASE = "https://api.fabric.microsoft.com/v1"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"

DEFAULT_POLL_SECONDS = 5.0
MAX_POLL_SECONDS = 30.0
MAX_POLLS = 120
REQUEST_TIMEOUT = (10, 60)  # connect and read, in seconds


class HttpResponse(NamedTuple):
    """One response: the status, the headers (names in lower case) and the JSON body if any."""

    status: int
    headers: Mapping[str, str]
    body: dict[str, Any] | None


class FabricApiError(Exception):
    """A Fabric API failure. The message names the HTTP status and the error code only."""

    def __init__(self, status: int, code: str) -> None:
        self.status = status
        self.code = code
        super().__init__(f"Fabric API {status} {code}")


class FabricAuthError(Exception):
    """No token could be had from `az login`. The message is fixed and holds no detail."""


def _http(
    method: str, url: str, headers: Mapping[str, str], json_body: dict[str, Any] | None
) -> HttpResponse:
    """Send one request with requests and return its status, lower-cased headers and JSON body."""
    import requests

    response = requests.request(
        method, url, headers=dict(headers), json=json_body, timeout=REQUEST_TIMEOUT
    )
    try:
        body = response.json() if response.content else None
    except ValueError:
        body = None
    if not isinstance(body, dict):
        body = None
    return HttpResponse(
        response.status_code, {key.lower(): value for key, value in response.headers.items()}, body
    )


def _token() -> str:
    """Return a bearer token for the Fabric API from the `az login` identity."""
    try:
        from azure.identity import AzureCliCredential

        return AzureCliCredential().get_token(FABRIC_SCOPE).token
    except Exception:  # noqa: BLE001 - the original text can name the tenant, so it is dropped
        raise FabricAuthError(
            "could not get a token from az login; run `az login` and retry"
        ) from None


# Module-level callables so a test can replace them.
HTTP: Callable[[str, str, Mapping[str, str], dict[str, Any] | None], HttpResponse] = _http
TOKEN: Callable[[], str] = _token
SLEEP: Callable[[float], None] = time.sleep


def _check_id(value: str, argument: str) -> str:
    if not isinstance(value, str) or not UUID_RE.match(value):
        raise ValueError(f"{argument} is not a UUID")
    return value


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None


def _error_code(body: dict[str, Any] | None) -> str:
    code = (body or {}).get("errorCode")
    return code if isinstance(code, str) and code.isascii() and len(code) <= 100 else "unknown"


def request_json(method: str, path: str, body: dict[str, Any] | None = None) -> HttpResponse:
    """Send one request to the Fabric API with the bearer token; raise on status 400 and above."""
    headers = {
        "Authorization": f"Bearer {TOKEN()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    response = HTTP(method, f"{FABRIC_API_BASE}{path}", headers, body)
    if response.status >= 400:
        raise FabricApiError(response.status, _error_code(response.body))
    return response


def _wait_seconds(headers: Mapping[str, str]) -> float:
    raw = _header(headers, "retry-after")
    try:
        seconds = float(raw) if raw is not None else DEFAULT_POLL_SECONDS
    except ValueError:
        seconds = DEFAULT_POLL_SECONDS
    return min(max(seconds, 0.0), MAX_POLL_SECONDS)


def wait_operation(response: HttpResponse) -> dict[str, Any] | None:
    """Follow a long-running operation (202) to its result.

    A 202 with `x-ms-operation-id` is polled at `GET /operations/{id}`, waiting what `Retry-After`
    asks (5 s by default, at most 30 s), for at most 120 polls. `Succeeded` returns the body of
    `GET /operations/{id}/result`; `Failed` raises FabricApiError with the operation's error code.
    Any other response is returned as its own body.
    """
    operation_id = _header(response.headers, "x-ms-operation-id")
    if response.status != 202 or not operation_id:
        return response.body
    _check_id(operation_id, "operation id")
    wait = _wait_seconds(response.headers)
    for _ in range(MAX_POLLS):
        SLEEP(wait)
        status_response = request_json("GET", f"/operations/{operation_id}")
        status = (status_response.body or {}).get("status")
        if status == "Succeeded":
            return request_json("GET", f"/operations/{operation_id}/result").body
        if status == "Failed":
            error = (status_response.body or {}).get("error")
            code = error.get("errorCode") if isinstance(error, dict) else None
            raise FabricApiError(200, _error_code({"errorCode": code}))
        wait = _wait_seconds(status_response.headers)
    raise FabricApiError(408, "OperationTimedOut")


def list_lakehouses(workspace_id: str) -> list[dict[str, Any]]:
    """Return every Lakehouse of the workspace, following `continuationToken`."""
    workspace = _check_id(workspace_id, "workspace id")
    items: list[dict[str, Any]] = []
    token: str | None = None
    while True:
        path = f"/workspaces/{workspace}/lakehouses"
        if token:
            path += f"?continuationToken={quote(token, safe='')}"
        body = request_json("GET", path).body or {}
        items.extend(item for item in body.get("value", []) if isinstance(item, dict))
        token = body.get("continuationToken")
        if not token:
            return items


def get_lakehouse(workspace_id: str, lakehouse_id: str) -> dict[str, Any]:
    """Return one Lakehouse with its properties."""
    workspace = _check_id(workspace_id, "workspace id")
    lakehouse = _check_id(lakehouse_id, "lakehouse id")
    return request_json("GET", f"/workspaces/{workspace}/lakehouses/{lakehouse}").body or {}


def create_lakehouse(workspace_id: str, name: str) -> dict[str, Any]:
    """Create a schema-enabled Lakehouse and return the item (a 201 body or the LRO result)."""
    workspace = _check_id(workspace_id, "workspace id")
    response = request_json(
        "POST",
        f"/workspaces/{workspace}/lakehouses",
        {"displayName": name, "creationPayload": {"enableSchemas": True}},
    )
    if response.status == 202:
        return wait_operation(response) or {}
    return response.body or {}


def is_schema_enabled(lakehouse: Mapping[str, Any]) -> bool:
    """True when the Lakehouse has a `defaultSchema` property, the adapter's own marker."""
    properties = lakehouse.get("properties")
    return isinstance(properties, dict) and "defaultSchema" in properties

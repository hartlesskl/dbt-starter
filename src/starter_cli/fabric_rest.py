"""A small Fabric REST client for `setup-fabric` and `verify-fabric` (ADR 0006 decision 8, D-18).

It lists, reads and creates Lakehouses in a workspace for the setup command, and lists, starts and
reads native dbt job runs and the job definition for the verification command, with the token from
`az login`. Three module-level seams (`HTTP`, `TOKEN`, `SLEEP`) are what the
tests replace, so no test ever reaches the network.

Nothing here prints. Errors carry the HTTP status and Fabric's `errorCode` and nothing else, so a
message never holds a workspace id, a Lakehouse name or a token (T-05-01). The API host is a
constant and is never read from the environment (T-05-03). Every id is checked against the UUID
rule before it enters a URL path, and a bad id raises ValueError naming the argument, never the
value (T-05-02).
"""

from __future__ import annotations

import base64
import json
import re
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


# The native dbt job (plan 05-04, target/fabric-live/native-first-run.json). list-instances
# reports its job type as `DBTItem`, but Run On Demand Item Job refused that type in both the path
# and the query form (400 InvalidJobType), so job-rest-run is documented-fallback: no REST start is
# proven, DBT_JOB_TYPE and RUN_PATH_FORM are None, and native runs are started in the portal. When
# a later runtime accepts a REST start, set both to the job type and the form ("path" or "query")
# that worked, from a fresh native-first-run.json.
DBT_JOB_TYPE: str | None = None
RUN_PATH_FORM: str | None = None
RUN_PATH_FORMS = ("path", "query")
# The part of the job's item definition that holds its profile and command (plan 05-04).
DBT_DEFINITION_PART = "dbt-content.json"
JOB_TYPE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")


def list_job_instances(workspace_id: str, item_id: str) -> list[dict[str, Any]]:
    """Return every job instance of the item, following `continuationToken`."""
    workspace = _check_id(workspace_id, "workspace id")
    item = _check_id(item_id, "item id")
    instances: list[dict[str, Any]] = []
    token: str | None = None
    while True:
        path = f"/workspaces/{workspace}/items/{item}/jobs/instances"
        if token:
            path += f"?continuationToken={quote(token, safe='')}"
        body = request_json("GET", path).body or {}
        instances.extend(entry for entry in body.get("value", []) if isinstance(entry, dict))
        token = body.get("continuationToken")
        if not token:
            return instances


def run_item_job(workspace_id: str, item_id: str, job_type: str, form: str | None = None) -> str:
    """Start one on-demand run of the item and return the Location URL of the 202.

    The Location carries the workspace and item ids, so a caller keeps it in memory and never
    writes it anywhere (T-05-31). `form` defaults to RUN_PATH_FORM, the form plan 05-04 proved.
    """
    workspace = _check_id(workspace_id, "workspace id")
    item = _check_id(item_id, "item id")
    if not isinstance(job_type, str) or not JOB_TYPE_RE.match(job_type):
        raise ValueError("job type is not a plain name")
    chosen = RUN_PATH_FORM if form is None else form
    if chosen not in RUN_PATH_FORMS:
        raise ValueError("no REST start form is proven for this job; start the run in the portal")
    if chosen == "path":
        path = f"/workspaces/{workspace}/items/{item}/jobs/{job_type}/instances"
    else:
        path = f"/workspaces/{workspace}/items/{item}/jobs/instances?jobType={job_type}"
    response = request_json("POST", path)
    location = _header(response.headers, "location")
    if response.status != 202 or not location:
        raise FabricApiError(response.status, "NoJobLocation")
    return location


def get_job_instance(location: str) -> dict[str, Any]:
    """Return the job instance a Location URL names; any URL off the Fabric API host is refused.

    The bearer token goes wherever the URL points, so only FABRIC_API_BASE is followed (T-05-29).
    """
    if not isinstance(location, str) or not location.startswith(FABRIC_API_BASE + "/"):
        raise ValueError("the job Location is not on the Fabric API host")
    return request_json("GET", location[len(FABRIC_API_BASE) :]).body or {}


def get_item_definition(workspace_id: str, item_id: str) -> dict[str, Any]:
    """Return the job's decoded DBT_DEFINITION_PART as a dict (the job profile and command)."""
    workspace = _check_id(workspace_id, "workspace id")
    item = _check_id(item_id, "item id")
    response = request_json("POST", f"/workspaces/{workspace}/items/{item}/getDefinition")
    body = wait_operation(response) if response.status == 202 else response.body
    parts = ((body or {}).get("definition") or {}).get("parts") or []
    for part in parts:
        if isinstance(part, dict) and part.get("path") == DBT_DEFINITION_PART:
            decoded = json.loads(base64.b64decode(part.get("payload") or ""))
            if not isinstance(decoded, dict):
                raise ValueError(f"{DBT_DEFINITION_PART} is not a JSON object")
            return decoded
    raise ValueError(f"the item definition has no {DBT_DEFINITION_PART} part")


# Livy session states that no longer hold the capacity (the workspace's livySessions list).
LIVY_ENDED_STATES = ("Succeeded", "Failed", "Cancelled", "Dead", "Killed")


def active_livy_sessions(workspace_id: str) -> int:
    """Return how many Livy sessions of the workspace are still active (read-only).

    A small trial capacity fits one Spark session at a time, so a caller checks this before it
    opens one, and a second session started meanwhile fails with HTTP 430.
    """
    workspace = _check_id(workspace_id, "workspace id")
    active = 0
    token: str | None = None
    while True:
        path = f"/workspaces/{workspace}/spark/livySessions"
        if token:
            path += f"?continuationToken={quote(token, safe='')}"
        body = request_json("GET", path).body or {}
        active += sum(
            1
            for session in body.get("value", [])
            if isinstance(session, dict) and session.get("state") not in LIVY_ENDED_STATES
        )
        token = body.get("continuationToken")
        if not token:
            return active

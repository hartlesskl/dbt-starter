"""`uv run --locked --extra fabric setup-fabric`: find or create the schema-enabled Lakehouse.

The command looks up the Lakehouse named by FABRIC_LAKEHOUSE in the workspace named by
FABRIC_WORKSPACE_ID, creates it with schemas enabled only when none exists, and prints one stdout
line, `FABRIC_LAKEHOUSE_ID=<id>`, for the user to set (ADR 0006 decision 8). It needs an
`az login` identity with Contributor (or Member or Admin) on the workspace.

It only lists, reads and creates. It refuses, with exit 2, an existing Lakehouse without schemas or
a name that differs from an existing one only in case, and it never deletes, renames or alters
anything. Every progress and error line goes to stderr with the prefix `setup-fabric:`, names
variables only and never prints the workspace id, the Lakehouse name or a token.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import sys
from collections.abc import Mapping

from starter_cli import fabric_rest
from starter_cli.build_fabric import DEFAULT_SCHEMA, UUID_RE

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,255}$")
SETUP_FABRIC_COMMAND = "uv run --locked --extra fabric setup-fabric"
ADAPTER_HINT = (
    "the Azure sign-in library (azure-identity) is not installed, so setup-fabric cannot run. "
    f"Run it with its extra: `{SETUP_FABRIC_COMMAND}`."
)


def azure_identity_installed() -> bool:
    """Return True when the Azure sign-in library can be imported."""
    return importlib.util.find_spec("azure.identity") is not None


def check_setup_environment(env: Mapping[str, str]) -> list[str]:
    """Return what is wrong with the setup variables, naming variables and never their values."""
    workspace = env.get("FABRIC_WORKSPACE_ID", "").strip()
    lakehouse = env.get("FABRIC_LAKEHOUSE", "").strip()
    schema = env.get("FABRIC_SCHEMA", "").strip() or DEFAULT_SCHEMA
    problems = []
    if not workspace:
        problems.append("FABRIC_WORKSPACE_ID is not set")
    elif not UUID_RE.match(workspace):
        problems.append("FABRIC_WORKSPACE_ID is not a UUID")
    if not lakehouse:
        problems.append("FABRIC_LAKEHOUSE is not set")
    elif not NAME_RE.match(lakehouse):
        problems.append(
            "FABRIC_LAKEHOUSE must start with a letter and hold only letters, digits and "
            "underscores (at most 256 characters)"
        )
    elif schema.lower() == lakehouse.lower():
        problems.append(
            "FABRIC_SCHEMA must differ from FABRIC_LAKEHOUSE on a schema-enabled Lakehouse "
            f"(FABRIC_SCHEMA defaults to {DEFAULT_SCHEMA})"
        )
    return problems


def _say(message: str) -> None:
    print(f"setup-fabric: {message}", file=sys.stderr, flush=True)


def _print_id(lakehouse_id: object) -> int:
    if not isinstance(lakehouse_id, str) or not UUID_RE.match(lakehouse_id):
        _say("Fabric returned a Lakehouse without a usable id")
        return 1
    print(f"FABRIC_LAKEHOUSE_ID={lakehouse_id}", flush=True)
    return 0


def run(workspace_id: str, name: str) -> int:
    """Find or create the Lakehouse and print its id. Return the exit code."""
    items = fabric_rest.list_lakehouses(workspace_id)
    exact = [item for item in items if item.get("displayName") == name]
    if not exact and any(
        str(item.get("displayName", "")).lower() == name.lower() for item in items
    ):
        _say(
            "a Lakehouse whose name differs from FABRIC_LAKEHOUSE only in case exists; choose another name"
        )
        return 2
    if exact:
        found = fabric_rest.get_lakehouse(workspace_id, str(exact[0].get("id", "")))
        if not fabric_rest.is_schema_enabled(found):
            _say(
                "the Lakehouse named by FABRIC_LAKEHOUSE exists without schemas; choose another name"
            )
            return 2
        _say("found the Lakehouse")
        return _print_id(found.get("id"))

    created = fabric_rest.create_lakehouse(workspace_id, name)
    confirmed = fabric_rest.get_lakehouse(workspace_id, str(created.get("id", "")))
    if not fabric_rest.is_schema_enabled(confirmed):
        _say("the created Lakehouse does not report schemas; it was not changed or deleted")
        return 1
    _say("created the Lakehouse")
    return _print_id(confirmed.get("id"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="setup-fabric",
        description=(
            "Find or create the schema-enabled Lakehouse named by FABRIC_LAKEHOUSE in the "
            "workspace named by FABRIC_WORKSPACE_ID, and print FABRIC_LAKEHOUSE_ID=<id> on "
            "stdout. Requires `az login` with Contributor on the workspace. It only lists, reads "
            "and creates; it never deletes or renames anything."
        ),
    )
    parser.parse_args(argv)

    if not azure_identity_installed():
        _say(ADAPTER_HINT)
        return 2
    problems = check_setup_environment(os.environ)
    for problem in problems:
        _say(problem)
    if problems:
        return 2

    try:
        return run(
            os.environ["FABRIC_WORKSPACE_ID"].strip(), os.environ["FABRIC_LAKEHOUSE"].strip()
        )
    except fabric_rest.FabricApiError as error:
        _say(str(error))
        return 1
    except fabric_rest.FabricAuthError as error:
        _say(str(error))
        return 1
    except OSError:
        _say("could not reach the Fabric API")
        return 1


if __name__ == "__main__":
    sys.exit(main())

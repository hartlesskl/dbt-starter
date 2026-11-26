"""The Lakehouse landing sink: one source file, normalised by DuckDB, written to OneLake as Delta.

`load <source> <entity> --target fabric` lands each file exactly as the DuckDB path does (D-06):
the unchanged landing code normalises it in a temporary DuckDB file, and this sink then writes
that file's rows to `Tables/landing/<table>` with delta-rs (D-04). The write is one Delta commit
per file: an overwrite with the predicate `_source_file = '<file>'`, so a re-shipped file replaces
only its own rows and an interrupted or repeated load leaves the table holding either the old rows
or the new rows of that file (D-07). `schema_mode="merge"` carries a newly declared column into the
table as a string, and older rows read NULL.

`_loaded_at` leaves DuckDB as a `timestamp` and would become the Delta type `timestamp_ntz`, which
needs table protocol (3,7) and is refused by the SQL analytics endpoint. It is therefore cast with
`at time zone 'UTC'` to an instant, which keeps the protocol at (1,2) and the same UTC moment
(research Surprise 3). A `::timestamptz` cast would read the naive value in the session zone.

Nothing here is imported by the default DuckDB path: `deltalake` is imported inside `ship`, and
the package arrives only with `--extra fabric`. Messages name files and counts, never the ids.
"""

from __future__ import annotations

import importlib.util
import re
from collections.abc import Mapping
from pathlib import Path

import duckdb

from starter_cli.build_fabric import UUID_RE
from starter_cli.landing import IDENT_RE, EntitySpec, LandingError

ONELAKE_HOST = "onelake.dfs.fabric.microsoft.com"
# object_store runs `az account get-access-token` for the Storage audience, the only audience
# OneLake accepts. The adapter's own token (Power BI audience) comes from the same `az login`.
STORAGE_OPTIONS: dict[str, str] = {"azure_use_azure_cli": "true"}
LANDING_SCHEMA = "landing"

SINK_COMMAND = "uv run --locked --extra fabric load <source> <entity> --target fabric"
SINK_HINT = (
    "The Lakehouse sink (deltalake) is not installed, so --target fabric cannot run. "
    f"Run the loader with its extra: `{SINK_COMMAND}`."
)


def sink_installed() -> bool:
    """Return True when the sink's write package can be imported.

    Named for the sink, not the package, so the write path can change without renaming callers.
    """
    return importlib.util.find_spec("deltalake") is not None


def _check_table(table: str) -> None:
    if not IDENT_RE.match(table):
        raise ValueError(f"table {table!r} must match {IDENT_RE.pattern}")


def table_uri(workspace_id: str, lakehouse_id: str, table: str) -> str:
    """Return the OneLake URL of `Tables/landing/<table>`, built only from checked parts.

    Both ids must be UUIDs and the table a plain identifier, and everything else in the path is
    fixed, so nothing typed can point the write outside `Tables/landing/` (T-05-10).
    """
    for label, value in (("workspace id", workspace_id), ("Lakehouse id", lakehouse_id)):
        if not UUID_RE.match(value):
            raise ValueError(f"the {label} is not a UUID")
    _check_table(table)
    return f"abfss://{workspace_id}@{ONELAKE_HOST}/{lakehouse_id}/Tables/{LANDING_SCHEMA}/{table}"


def delta_predicate(source_file: str) -> str:
    """Return the DataFusion predicate that selects one file's rows, quotes doubled (T-05-07)."""
    return "_source_file = '" + source_file.replace("'", "''") + "'"


def redact(text: str, secrets: Mapping[str, str]) -> str:
    """Replace every secret value in `text` with `<label>`, whatever its letter case.

    `secrets` maps a label (`workspace-id`) to the value to hide. Empty values are skipped. Each
    secret is matched on the original text with a case-insensitive pattern, never by offsets found
    in a lower-cased copy: some characters change length when lower-cased, which shifts every
    later offset and leaves part of an id in view.
    """
    redacted = text
    for label, value in secrets.items():
        if not value:
            continue
        placeholder = f"<{label}>"
        redacted = re.sub(
            re.escape(value), lambda _match, text=placeholder: text, redacted, flags=re.IGNORECASE
        )
    return redacted


class LakehouseSink:
    """Ships one landed source file at a time to a Delta table under `Tables/landing/`.

    `base_uri` (a local folder, used by the tests) replaces the OneLake location. Without it the
    sink writes to the workspace and Lakehouse named by the two ids, with the az CLI as the login.
    """

    def __init__(
        self,
        workspace_id: str,
        lakehouse_id: str,
        *,
        base_uri: str | Path | None = None,
        storage_options: Mapping[str, str] | None = None,
    ) -> None:
        self.workspace_id = workspace_id
        self.lakehouse_id = lakehouse_id
        self.base_uri = None if base_uri is None else str(base_uri)
        if storage_options is not None:
            self.storage_options: dict[str, str] | None = dict(storage_options)
        else:
            self.storage_options = dict(STORAGE_OPTIONS) if self.base_uri is None else None

    def location(self, table: str) -> str:
        if self.base_uri is None:
            return table_uri(self.workspace_id, self.lakehouse_id, table)
        _check_table(table)
        return f"{self.base_uri.rstrip('/')}/{table}"

    def _secrets(self) -> dict[str, str]:
        return {"workspace-id": self.workspace_id, "lakehouse-id": self.lakehouse_id}

    def ship(self, con: duckdb.DuckDBPyConnection, spec: EntitySpec, source_file: str) -> int:
        """Write this file's rows to the Lakehouse in one Delta commit and return the rows written.

        The rows come from `landing.<table>` in `con`, after the unchanged landing code wrote
        them there. A failure becomes a LandingError whose text has the ids removed.
        """
        try:
            from deltalake import DeltaTable, write_deltalake

            location = self.location(spec.table)
            relation = con.sql(
                f"select * replace ((_loaded_at at time zone 'UTC') as _loaded_at) "
                f"from landing.{spec.table} where _source_file = ?",
                params=[source_file],
            )
            expected = int(
                con.execute(
                    f"select count(*) from landing.{spec.table} where _source_file = ?",
                    [source_file],
                ).fetchall()[0][0]  # fetchall, so the connection is free for the stream below
            )
            write_deltalake(
                location,
                relation,
                mode="overwrite",
                predicate=delta_predicate(source_file),
                schema_mode="merge",
                storage_options=self.storage_options,
            )
            commit = DeltaTable(location, storage_options=self.storage_options).history(1)[0]
        except LandingError:
            raise
        except Exception as exc:  # noqa: BLE001 - delta-rs raises several types; all are redacted
            message = redact(f"{type(exc).__name__}: {exc}", self._secrets())
            raise LandingError(
                f"The Lakehouse could not take {source_file} for landing.{spec.table}: {message}"
            ) from None

        metrics = commit.get("operationMetrics") or {}
        if int(metrics.get("num_removed_files", 0) or 0) > 0:
            print(f"Replaced {source_file} in the Lakehouse")
        added = metrics.get("num_added_rows")
        return int(added) if added is not None else expected

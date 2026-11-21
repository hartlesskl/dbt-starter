"""Generic landing loader: download a file once, then replace that file's rows in landing.

Nothing in this module names a data source. A source describes itself with an `EntitySpec`
(one per source sub-package under `starter_cli`) and this module does the rest.

Landing convention: every data column is text (an empty cell becomes NULL, every other value
keeps its published text), and four lineage columns are added: `_source_file`,
`_source_row_number`, `_batch_id` and `_loaded_at`.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import requests

from starter_cli.project import duckdb_path, raw_dir

IDENT_RE = re.compile(r"^[a-z][a-z0-9_]*$")

USER_AGENT = "dbt-starter-loader/0.1"
REQUEST_TIMEOUT_SECONDS = 60


class DownloadError(RuntimeError):
    """The file could not be downloaded or did not look like the expected file."""


class LandingError(RuntimeError):
    """The file could not be landed (for example a missing or ambiguous column)."""


@dataclass(frozen=True)
class EntitySpec:
    """Describes one landable file: where it comes from and which columns it carries."""

    source: str
    entity: str
    url: str
    file_name: str
    columns: tuple[str, ...]
    header_prefix: bytes

    def __post_init__(self) -> None:
        # These names become SQL identifiers and path parts, so they are validated here
        # once and never interpolated from anywhere else.
        for label, value in (("source", self.source), ("entity", self.entity)):
            if not IDENT_RE.match(value):
                raise ValueError(f"{label} {value!r} must match {IDENT_RE.pattern}")
        for column in self.columns:
            if not IDENT_RE.match(column):
                raise ValueError(f"column {column!r} must match {IDENT_RE.pattern}")
        if not self.columns:
            raise ValueError("an entity needs at least one column")
        if not self.file_name or Path(self.file_name).name != self.file_name:
            raise ValueError(f"file_name {self.file_name!r} must be a plain file name")

    @property
    def table(self) -> str:
        return f"{self.source}__{self.entity}"


def _blocked_message(spec: EntitySpec, reason: str) -> str:
    return (
        f"Could not download {spec.url} ({reason}). "
        "The file is blocked or unavailable from this machine. "
        "See README.md#troubleshooting."
    )


def download(spec: EntitySpec, raw_root: Path, *, refresh: bool = False) -> Path:
    """Download the entity's file into the cache and return its path.

    A cached file is returned without any network access unless `refresh` is true. Only an
    HTTP 200 body that starts with the expected header reaches the cache, and it is written
    to a temporary name first, so an error page or partial body is never cached.
    """
    dest = raw_root / spec.source / spec.file_name
    if dest.exists() and not refresh:
        return dest

    try:
        response = requests.get(
            spec.url,
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
        )
    except requests.RequestException as exc:
        raise DownloadError(_blocked_message(spec, type(exc).__name__)) from exc

    if response.status_code != 200:
        raise DownloadError(_blocked_message(spec, f"HTTP {response.status_code}"))
    if not response.content.startswith(spec.header_prefix):
        raise DownloadError(
            _blocked_message(spec, "the response did not start with the expected CSV header")
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    try:
        part.write_bytes(response.content)
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)
    return dest


def ensure_landing_table(con: duckdb.DuckDBPyConnection, spec: EntitySpec) -> None:
    """Create the landing schema and table if they do not exist.

    DuckDB sink only. A Fabric sink needs `datetime2(6)` and sized `varchar(n)` instead,
    so keep all landing DDL in this one function.
    """
    data_columns = ", ".join(f"{column} varchar" for column in spec.columns)
    con.execute("create schema if not exists landing")
    con.execute(
        f"create table if not exists landing.{spec.table} ("
        f"{data_columns}, "
        "_source_file varchar, "
        "_source_row_number bigint, "
        "_batch_id varchar, "
        "_loaded_at timestamp)"
    )


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _map_columns(spec: EntitySpec, headers: list[str]) -> list[str]:
    """Return, for each spec column in order, the CSV header whose lowercase equals it."""
    by_lower: dict[str, str] = {}
    for header in headers:
        lower = header.lower()
        if lower in by_lower:
            raise LandingError(
                f"Headers {by_lower[lower]!r} and {header!r} are the same column once lowercased; "
                "landing lowercases every column name."
            )
        by_lower[lower] = header
    mapped = []
    for column in spec.columns:
        if column not in by_lower:
            raise LandingError(f"The file has no column named {column!r} (case-insensitive).")
        mapped.append(by_lower[column])
    return mapped


def land_file(con: duckdb.DuckDBPyConnection, spec: EntitySpec, path: Path) -> int:
    """Replace this file's rows in landing in one transaction and return their count."""
    described = con.execute(
        "describe select * from read_csv(?, header=true, all_varchar=true)", [str(path)]
    ).fetchall()
    headers = [row[0] for row in described]
    mapped = _map_columns(spec, headers)

    source_file = path.name
    batch_id = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    loaded_at = datetime.now(UTC).replace(tzinfo=None)

    target_columns = ", ".join(
        [*spec.columns, "_source_file", "_source_row_number", "_batch_id", "_loaded_at"]
    )
    select_values = ", ".join(f"nullif({_quote(header)}, '')" for header in mapped)

    con.execute("begin")
    try:
        con.execute(f"delete from landing.{spec.table} where _source_file = ?", [source_file])
        con.execute(
            f"insert into landing.{spec.table} ({target_columns}) "
            f"select {select_values}, ?, row_number() over (), ?, ? "
            "from read_csv(?, header=true, all_varchar=true)",
            [source_file, batch_id, loaded_at, str(path)],
        )
        con.execute("commit")
    except Exception:
        con.execute("rollback")
        raise

    row = con.execute(
        f"select count(*) from landing.{spec.table} where _source_file = ?", [source_file]
    ).fetchone()
    return int(row[0])


def load_entity(spec: EntitySpec, root: Path, *, refresh: bool = False) -> int:
    """Download (or reuse the cached file) and land it in DuckDB. Returns the landed row count."""
    path = download(spec, raw_dir(root), refresh=refresh)
    db_file = duckdb_path(root)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_file))
    try:
        ensure_landing_table(con, spec)
        return land_file(con, spec, path)
    finally:
        con.close()

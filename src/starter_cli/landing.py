"""Generic landing loader: download a file once, then replace that file's rows in landing.

Nothing in this module names a data source. A source describes itself with an `EntitySpec`
(one per source sub-package under `starter_cli`) and this module does the rest.

Landing convention: every data column is text (an empty cell becomes NULL, every other value
keeps its published text), and four lineage columns are added: `_source_file`,
`_source_row_number`, `_batch_id` and `_loaded_at`.

A file reaches landing in one of two ways: it is downloaded into the cache, or it is read from
a local folder (`from_dir`). Either way `land_file` is the one place that writes landing rows.

A month appears in generic code only inside a format example in prose, a month list or range
(`YYYY-MM,YYYY-MM` or `YYYY-MM..YYYY-MM`) in a docstring, an argparse help text or a raised error
message.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import re
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import requests

from starter_cli.project import duckdb_path, raw_dir

IDENT_RE = re.compile(r"^[a-z][a-z0-9_]*$")
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
MONTH_TOKEN = "{month}"
FILE_FORMATS = ("csv", "parquet")
PARQUET_MAGIC = b"PAR1"

USER_AGENT = "dbt-starter-loader/0.1"
MAX_ATTEMPTS = 4
BACKOFF_SECONDS = (2.0, 4.0, 8.0)
JITTER_FRACTION = 0.25
MAX_RETRY_AFTER_SECONDS = 60.0
CONNECT_TIMEOUT_SECONDS = 10
READ_TIMEOUT_SECONDS = 60
CHUNK_BYTES = 1048576


class DownloadError(RuntimeError):
    """The file could not be downloaded or did not look like the expected file."""


class LandingError(RuntimeError):
    """The file could not be landed (for example a missing or ambiguous column)."""


@dataclass(frozen=True)
class EntitySpec:
    """Describes one landable file: where it comes from and which columns it carries.

    A spec is monthly when `file_name` and `url` both contain `{month}`: the entity then has
    one file per `YYYY-MM` month. A spec with no `{month}` has exactly one file.

    A spec has exactly one source for its file: a non-empty `url` to download, or a `provide`
    callable that writes the file itself (D-07), so a source that needs no download, such as the
    placeholder source of a copy of this template, lands through the same loader. `provide`
    receives the temporary file path and writes the file there; the usual checks then run on it.
    A provided file is one file, so a provide spec is never monthly.
    """

    source: str
    entity: str
    url: str
    file_name: str
    columns: tuple[str, ...]
    header_prefix: bytes
    file_format: str = "csv"
    default_months: tuple[str, ...] = ()
    # When true, a declared column that a file lacks lands as NULL instead of failing the load.
    null_missing_columns: bool = False
    # Months before this one (YYYY-MM) are large, so a download of them needs `allow_large`.
    large_before: str | None = None
    # Writes the entity's file to the path it is given, instead of a download (D-07).
    provide: Callable[[Path], None] | None = None

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
        if self.file_format not in FILE_FORMATS:
            raise ValueError(f"file_format {self.file_format!r} must be one of {FILE_FORMATS}")
        if self.provide is not None and (MONTH_TOKEN in self.file_name or MONTH_TOKEN in self.url):
            raise ValueError(f"{self.table} provides its file, so it cannot be monthly")
        if bool(self.url) == (self.provide is not None):
            raise ValueError(
                f"{self.table} needs exactly one of a non-empty url or a provide callable"
            )
        if (MONTH_TOKEN in self.url) != (MONTH_TOKEN in self.file_name):
            raise ValueError(f"{MONTH_TOKEN} must appear in both url and file_name, or in neither")
        if self.file_name.count(MONTH_TOKEN) > 1 or self.url.count(MONTH_TOKEN) > 1:
            raise ValueError(f"{MONTH_TOKEN} may appear only once in url and in file_name")
        for month in self.default_months:
            if not MONTH_RE.match(month):
                raise ValueError(f"default month {month!r} must look like YYYY-MM")
        if self.large_before is not None:
            if not MONTH_RE.match(self.large_before):
                raise ValueError(f"large_before {self.large_before!r} must look like YYYY-MM")
            if not self.is_monthly:
                raise ValueError("large_before only applies to a monthly entity")
        # The placeholder text, not a month, keeps month values out of generic code (D-22).
        sample = self.file_name.replace(MONTH_TOKEN, "YYYY-MM")
        if Path(sample).name != sample:
            raise ValueError(f"file_name {self.file_name!r} must give a plain file name")

    @property
    def table(self) -> str:
        return f"{self.source}__{self.entity}"

    @property
    def is_monthly(self) -> bool:
        return MONTH_TOKEN in self.file_name and MONTH_TOKEN in self.url

    def _fill(self, template: str, month: str | None) -> str:
        if not self.is_monthly:
            if month is not None:
                raise ValueError(f"{self.table} has a single file, so it takes no month")
            return template
        if month is None or not MONTH_RE.match(month):
            raise ValueError(f"month {month!r} must look like YYYY-MM")
        return template.replace(MONTH_TOKEN, month)

    def file_name_for(self, month: str | None) -> str:
        """Return the published file name for `month` (None for a one-file entity)."""
        return self._fill(self.file_name, month)

    def url_for(self, month: str | None) -> str:
        """Return the download URL for `month` (None for a one-file entity)."""
        return self._fill(self.url, month)

    @property
    def file_name_pattern(self) -> re.Pattern[str]:
        """Compiled regex for this entity's file names, with one group holding `YYYY-MM`."""
        if not self.is_monthly:
            raise ValueError(f"{self.table} has a single file, so it has no month pattern")
        month_group = r"(\d{4}-(?:0[1-9]|1[0-2]))"
        parts = [re.escape(part) for part in self.file_name.split(MONTH_TOKEN)]
        return re.compile("^" + month_group.join(parts) + "$")


def _expected_start(spec: EntitySpec) -> str:
    return "CSV header" if spec.file_format == "csv" else "Parquet magic bytes (PAR1)"


def _magic_problem(spec: EntitySpec, path: Path) -> str | None:
    """Return why the file does not look like the expected file, or None when it does.

    A CSV must start with the spec's header prefix. A Parquet file must carry `PAR1` in its
    first and its last four bytes, so a body cut short is caught as well as an error page.
    """
    try:
        with open(path, "rb") as handle:
            start = handle.read(len(spec.header_prefix))
            end = b""
            if spec.file_format == "parquet":
                handle.seek(0, os.SEEK_END)
                if handle.tell() < 2 * len(PARQUET_MAGIC):
                    return (
                        f"it is too short to be a Parquet file (expected {_expected_start(spec)})"
                    )
                handle.seek(-len(PARQUET_MAGIC), os.SEEK_END)
                end = handle.read(len(PARQUET_MAGIC))
    except OSError as exc:
        return f"it could not be read ({exc})"
    if not start.startswith(spec.header_prefix):
        return f"it does not start with the expected {_expected_start(spec)}"
    if spec.file_format == "parquet" and end != PARQUET_MAGIC:
        return "it does not end with the Parquet magic bytes (PAR1): it may be cut short"
    return None


def _sidecar(path: Path) -> Path:
    return path.with_name(path.name + ".meta.json")


def _read_sidecar(path: Path) -> dict | None:
    """Return the sidecar's content, `{}` when it exists but cannot be read, None when absent."""
    sidecar = _sidecar(path)
    if not sidecar.is_file():
        return None
    try:
        content = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return content if isinstance(content, dict) else {}


class _FailedAttempt(Exception):
    """One download attempt failed in a way that is worth retrying."""

    def __init__(self, reason: str, retry_after: float | None = None):
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after


def _retry_after_seconds(headers) -> float | None:
    """Return the `Retry-After` header as seconds (capped), or None when it is absent or a date."""
    raw = headers.get("Retry-After")
    if raw is None:
        return None
    try:
        seconds = float(int(str(raw).strip()))
    except ValueError:
        return None
    return min(max(seconds, 0.0), MAX_RETRY_AFTER_SECONDS)


def _attempt(spec: EntitySpec, url: str, part: Path) -> dict:
    """Make one request and stream the body into `part`. Return the response metadata.

    Raises `_FailedAttempt` for anything that should be retried: a non-200 status, a transport
    error, a body that does not match its Content-Length, or a body that is not the expected file.
    """
    try:
        with requests.get(
            url,
            stream=True,
            timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
        ) as response:
            if response.status_code != 200:
                raise _FailedAttempt(
                    f"HTTP {response.status_code}", _retry_after_seconds(response.headers)
                )
            written = 0
            with open(part, "wb") as handle:
                for chunk in response.iter_content(CHUNK_BYTES):
                    handle.write(chunk)
                    written += len(chunk)
            headers = response.headers
    except requests.RequestException as exc:
        raise _FailedAttempt(type(exc).__name__) from exc

    declared = headers.get("Content-Length")
    # `requests` decodes a compressed body, so its length is only comparable when it is not.
    if declared is not None and not headers.get("Content-Encoding"):
        try:
            expected = int(declared)
        except ValueError:
            expected = written
        if written != expected:
            raise _FailedAttempt(f"received {written} of {expected} bytes")
    problem = _magic_problem(spec, part)
    if problem is not None:
        raise _FailedAttempt(f"the response was not the expected file: {problem}")
    return {
        "url": url,
        "size": written,
        "last_modified": headers.get("Last-Modified"),
        "etag": headers.get("ETag"),
        "downloaded_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _failure_message(url: str, attempts: int, reason: str, what: str) -> str:
    return (
        f"Could not download {url} after {attempts} attempts (last problem: {reason}). "
        f"The file is blocked or unavailable from this machine: the {what} may not be "
        "published yet, or the server may be rate limiting this machine "
        "(the CDN answers 403 for both). "
        "Try again in a few minutes, or download the file another way and load it with "
        "--from-dir DIR. "
        "See README.md#troubleshooting."
    )


def _check_cached(spec: EntitySpec, dest: Path) -> None:
    """Raise DownloadError unless the cached file passes its magic and size checks."""
    advice = f"Delete {dest.name} (and its .meta.json), or run again with --refresh."
    problem = _magic_problem(spec, dest)
    if problem is not None:
        raise DownloadError(f"The cached file {dest} is not usable: {problem}. {advice}")
    meta = _read_sidecar(dest)
    if meta is None:
        return
    if not isinstance(meta.get("size"), int):
        raise DownloadError(
            f"The metadata file next to the cached file {dest} cannot be read. {advice}"
        )
    actual = dest.stat().st_size
    if meta["size"] != actual:
        raise DownloadError(
            f"The cached file {dest} has {actual} bytes but was recorded with {meta['size']}. "
            f"{advice}"
        )


def _describe_old(dest: Path) -> tuple[int | None, str]:
    """Return the cached file's size and recorded Last-Modified, before a refresh replaces it."""
    if not dest.is_file():
        return None, "unknown"
    meta = _read_sidecar(dest) or {}
    return dest.stat().st_size, str(meta.get("last_modified") or "unknown")


def download(
    spec: EntitySpec,
    raw_root: Path,
    *,
    refresh: bool = False,
    month: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
) -> Path:
    """Download the entity's file into the cache and return its path.

    `month` picks one file of a monthly entity (`YYYY-MM`); a one-file entity takes none.

    A cached file is returned without any network access unless `refresh` is true, after its
    magic bytes (and the size recorded in its `.meta.json` sidecar) are checked. Otherwise the
    file is streamed into `<name>.part`, up to `MAX_ATTEMPTS` times with a growing wait between
    attempts (the server's `Retry-After` wins, capped), and only a body that has the right
    status, length and magic bytes is renamed into the cache, so an error page or a partial
    body is never cached. A failed refresh leaves the previous file and sidecar untouched.
    """
    file_name = spec.file_name_for(month)
    url = spec.url_for(month)
    dest = raw_root / spec.source / file_name
    if dest.exists() and not refresh:
        _check_cached(spec, dest)
        return dest

    old_size, old_modified = _describe_old(dest) if refresh else (None, "unknown")
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    meta_part = _sidecar(dest).with_name(_sidecar(dest).name + ".part")
    reason = "no attempt was made"
    if spec.provide is not None:
        return _provide(spec, dest, part, meta_part, file_name, old_size, old_modified)
    try:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                meta = _attempt(spec, url, part)
            except _FailedAttempt as failed:
                reason = failed.reason
                if attempt == MAX_ATTEMPTS:
                    break
                if failed.retry_after is not None:
                    wait = failed.retry_after
                else:
                    wait = BACKOFF_SECONDS[attempt - 1] * (1 + JITTER_FRACTION * jitter())
                sleep(wait)
                continue
            meta_part.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
            os.replace(part, dest)
            os.replace(meta_part, _sidecar(dest))
            if old_size is not None:
                print(
                    f"Refreshed {file_name}: size {old_size} -> {meta['size']} bytes, "
                    f"Last-Modified {old_modified} -> {meta['last_modified'] or 'unknown'}",
                    flush=True,
                )
            return dest
    except OSError as exc:
        raise DownloadError(f"Could not write {dest} while downloading {url}: {exc}") from exc
    finally:
        part.unlink(missing_ok=True)
        meta_part.unlink(missing_ok=True)
    raise DownloadError(
        _failure_message(url, MAX_ATTEMPTS, reason, "month" if spec.is_monthly else "file")
    )


def _provide(
    spec: EntitySpec,
    dest: Path,
    part: Path,
    meta_part: Path,
    file_name: str,
    old_size: int | None,
    old_modified: str,
) -> Path:
    """Write the entity's file with `spec.provide`, check it like a download, and cache it.

    No request is made. The callable writes the temporary part file; the same magic-byte check a
    download gets runs on it, and only a file that passes is renamed into the cache, so a
    provider that wrote the wrong thing is never cached.
    """
    assert spec.provide is not None
    try:
        spec.provide(part)
        problem = _magic_problem(spec, part)
        if problem is not None:
            raise DownloadError(
                f"The file provided for {spec.table} is not the expected file: {problem}"
            )
        size = part.stat().st_size
        meta = {
            "url": "",
            "size": size,
            "last_modified": None,
            "etag": None,
            "downloaded_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        meta_part.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        os.replace(part, dest)
        os.replace(meta_part, _sidecar(dest))
    except OSError as exc:
        raise DownloadError(f"Could not write {dest} for {spec.table}: {exc}") from exc
    finally:
        part.unlink(missing_ok=True)
        meta_part.unlink(missing_ok=True)
    if old_size is not None:
        print(
            f"Refreshed {file_name}: size {old_size} -> {size} bytes, "
            f"Last-Modified {old_modified} -> unknown",
            flush=True,
        )
    return dest


def ensure_landing_table(con: duckdb.DuckDBPyConnection, spec: EntitySpec) -> None:
    """Create the landing schema and table if they do not exist.

    DuckDB sink only. The Phase 5 Lakehouse sink writes Delta tables through OneLake, so keep
    all landing DDL in this one function.
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
    # `create table if not exists` keeps an older table as it is, so a column declared since
    # then is added here, as text, before any rows are written.
    existing = {
        row[0]
        for row in con.execute(
            "select column_name from information_schema.columns "
            "where table_schema = 'landing' and table_name = ?",
            [spec.table],
        ).fetchall()
    }
    for column in spec.columns:
        if column not in existing:
            con.execute(f"alter table landing.{spec.table} add column {_quote(column)} varchar")


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


def _read_headers(path: Path) -> list[str]:
    """Return the CSV's header cells exactly as written.

    DuckDB renames a repeated header (`Code`, `CODE` become `Code`, `CODE_1`), which would hide
    a collision, so the header line is read here with the csv module instead.
    """
    with open(path, newline="", encoding="utf-8-sig") as handle:
        first_row = next(csv.reader(handle), None)
    if first_row is None:
        raise LandingError(f"{path.name} is empty: it has no header line.")
    return first_row


def _land_csv(con: duckdb.DuckDBPyConnection, spec: EntitySpec, path: Path, batch_id: str) -> None:
    mapped = _map_columns(spec, _read_headers(path))

    source_file = path.name
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


def _parquet_file_columns(
    con: duckdb.DuckDBPyConnection, path: Path
) -> tuple[dict[str, str], dict[str, str]]:
    """Return ({lowercase name: name as written}, {name as written: DuckDB type}), in file order.

    Leaf names come from `parquet_schema` with `type is not null`: the root element has no
    type, and its name differs by writer (`schema` or `duckdb_schema`), so it is never
    filtered by name. DuckDB renames a repeated name (`Code`, `CODE` become `Code`, `CODE_1`)
    when it reads the file, which would hide a collision, so collisions are checked on the
    leaf names first. Every lowercase name must be a plain identifier, because it becomes a
    column name in landing.
    """
    leaves = [
        row[0]
        for row in con.execute(
            "select name from parquet_schema(?) where type is not null", [str(path)]
        ).fetchall()
    ]
    by_lower: dict[str, str] = {}
    for name in leaves:
        lower = name.lower()
        if lower in by_lower:
            raise LandingError(
                f"Columns {by_lower[lower]!r} and {name!r} are the same column once lowercased; "
                "landing lowercases every column name."
            )
        by_lower[lower] = name
    for lower, name in by_lower.items():
        if not IDENT_RE.match(lower):
            raise LandingError(
                f"Column {name!r} in {path.name} cannot be landed: its lowercase name "
                f"{lower!r} must match {IDENT_RE.pattern}."
            )
    described_rows = con.execute("describe select * from read_parquet(?)", [str(path)]).fetchall()
    described = [row[0] for row in described_rows]
    if described != leaves:
        raise LandingError(
            f"{path.name} has nested or repeated columns, which landing does not support "
            f"(the file lists {leaves!r} but reads as {described!r})."
        )
    types = {row[0]: str(row[1]) for row in described_rows}
    return by_lower, types


# A double shown in exponent form (`1e-05`) is text the parse shims reject on both engines,
# because exponent text is outside the shared grammar, so a value that is tiny or huge is
# written in fixed notation with trailing zeros trimmed. Every other value keeps DuckDB's own
# text (`17.7`, `1.0`), which is what the published file shows.
SMALL_DOUBLE = "1e-4"
LARGE_DOUBLE = "1e15"


def _text_expression(quoted: str, sql_type: str) -> str:
    """Return the SQL that turns one Parquet column into landing text (empty becomes NULL)."""
    if sql_type in ("DOUBLE", "FLOAT"):
        fixed = f"rtrim(rtrim(printf('%.9f', {quoted}), '0'), '.')"
        value = (
            f"case when {quoted} is null then null "
            f"when {quoted} <> 0 and (abs({quoted}) < {SMALL_DOUBLE} "
            f"or abs({quoted}) >= {LARGE_DOUBLE}) then {fixed} "
            f"else cast({quoted} as varchar) end"
        )
    else:
        value = f"cast({quoted} as varchar)"
    return f"nullif({value}, '')"


def _land_parquet(
    con: duckdb.DuckDBPyConnection, spec: EntitySpec, path: Path, batch_id: str
) -> None:
    by_lower, types = _parquet_file_columns(con, path)
    for column in spec.columns:
        if column not in by_lower and not spec.null_missing_columns:
            raise LandingError(f"The file has no column named {column!r} (case-insensitive).")
    # A column nobody has declared is kept, as text, so landing holds everything as delivered.
    undeclared = [lower for lower in by_lower if lower not in spec.columns]
    for lower in undeclared:
        con.execute(
            f"alter table landing.{spec.table} add column if not exists {_quote(lower)} varchar"
        )

    source_file = path.name
    loaded_at = datetime.now(UTC).replace(tzinfo=None)

    data_columns = [*spec.columns, *undeclared]
    target_columns = ", ".join(
        [
            *(_quote(column) for column in data_columns),
            "_source_file",
            "_source_row_number",
            "_batch_id",
            "_loaded_at",
        ]
    )
    select_values = ", ".join(
        _text_expression(_quote(by_lower[column]), types[by_lower[column]])
        if column in by_lower
        else "cast(null as varchar)"
        for column in data_columns
    )

    con.execute("begin")
    try:
        con.execute(f"delete from landing.{spec.table} where _source_file = ?", [source_file])
        con.execute(
            f"insert into landing.{spec.table} ({target_columns}) "
            f"select {select_values}, ?, file_row_number + 1, ?, ? "
            "from read_parquet(?, file_row_number = true)",
            [source_file, batch_id, loaded_at, str(path)],
        )
        con.execute("commit")
    except Exception:
        con.execute("rollback")
        raise


def _batch_id(path: Path) -> str:
    """Return the first 16 hex characters of the sha256 of the file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _landed_batches(
    con: duckdb.DuckDBPyConnection, spec: EntitySpec, source_file: str
) -> list[tuple[str, int]]:
    """Return (batch id, row count) for the rows landing already holds for this file name."""
    rows = con.execute(
        f"select _batch_id, count(*) from landing.{spec.table} "
        "where _source_file = ? group by _batch_id order by _batch_id",
        [source_file],
    ).fetchall()
    return [(str(batch_id), int(count)) for batch_id, count in rows]


def land_file(con: duckdb.DuckDBPyConnection, spec: EntitySpec, path: Path) -> int:
    """Replace this file's rows in landing in one transaction and return their count.

    When the file name already holds rows from different content, one line says so after the
    landing: `Re-landed <file>: content changed, rows <old> -> <new>, batch <old> -> <new>`.
    The first landing of a file, and a file with identical bytes, print nothing. The lookup of
    the earlier rows never fails a load.
    """
    batch_id = _batch_id(path)
    try:
        earlier = _landed_batches(con, spec, path.name)
    except duckdb.Error:
        earlier = []
    if spec.file_format == "parquet":
        _land_parquet(con, spec, path, batch_id)
    else:
        _land_csv(con, spec, path, batch_id)
    row = con.execute(
        f"select count(*) from landing.{spec.table} where _source_file = ?", [path.name]
    ).fetchone()
    landed = int(row[0])
    if earlier and [old_id for old_id, _ in earlier] != [batch_id]:
        old_rows = sum(count for _, count in earlier)
        old_ids = "+".join(old_id for old_id, _ in earlier)
        print(
            f"Re-landed {path.name}: content changed, "
            f"rows {old_rows:,} -> {landed:,}, batch {old_ids} -> {batch_id}"
        )
    return landed


def _check_file_start(spec: EntitySpec, path: Path) -> None:
    """Raise LandingError unless the file starts (and, for Parquet, ends) as expected."""
    problem = _magic_problem(spec, path)
    if problem is not None:
        raise LandingError(f"{path.name} cannot be landed: {problem}.")


def _next_month(month: str) -> str:
    year, number = int(month[:4]), int(month[5:])
    return f"{year + 1:04d}-01" if number == 12 else f"{year:04d}-{number + 1:02d}"


def parse_months(text: str) -> tuple[str, ...]:
    """Parse `2024-01,2024-03`, `2024-01..2024-03` or a mix into sorted, unique `YYYY-MM` months.

    Raises ValueError naming the first part that is not a month, a range of months, or whose
    start is after its end. Every month matches `MONTH_RE`, so it is safe in a URL or file name.
    """
    months: set[str] = set()
    for part in (piece.strip() for piece in text.split(",")):
        if ".." in part:
            first, _, last = part.partition("..")
            for end in (first, last):
                if not MONTH_RE.match(end):
                    raise ValueError(
                        f"Month {end!r} in the range {part!r} must look like YYYY-MM "
                        "(for example 2024-01..2024-03)."
                    )
            if first > last:
                raise ValueError(f"Month range {part!r} starts after it ends.")
            month = first
            while month <= last:
                months.add(month)
                month = _next_month(month)
        elif MONTH_RE.match(part):
            months.add(part)
        else:
            raise ValueError(
                f"Month {part!r} must look like YYYY-MM, a list such as 2024-01,2024-03 "
                "or a range such as 2024-01..2024-03."
            )
    return tuple(sorted(months))


def _check_months(months: list[str]) -> list[str]:
    """Return the months without repeats, in the order given, after validating each one."""
    for month in months:
        if not MONTH_RE.match(month):
            raise LandingError(f"Month {month!r} must look like YYYY-MM.")
    return list(dict.fromkeys(months))


def _folder_file(folder: Path, name: str) -> Path:
    """Return `folder/name` after checking it is a regular file that really lives in `folder`."""
    path = folder / name
    if not path.is_file():
        raise LandingError(f"Expected the file {name} in {folder}, but it is not there.")
    if path.resolve().parent != folder.resolve():
        raise LandingError(f"{name} in {folder} points outside the folder, so it was not read.")
    return path


def _files_in_folder(spec: EntitySpec, folder: Path, months: list[str] | None) -> list[Path]:
    """Resolve the files to land from a local folder. Nothing is copied or downloaded."""
    if not folder.is_dir():
        raise LandingError(f"The folder {folder} does not exist.")
    if not spec.is_monthly:
        return [_folder_file(folder, spec.file_name)]

    pattern = spec.file_name_pattern
    available: dict[str, str] = {}
    for entry in sorted(folder.iterdir()):
        match = pattern.match(entry.name)
        if match and entry.is_file():
            available[match.group(1)] = entry.name
    if months is None:
        if not available:
            raise LandingError(f"No file named like {spec.file_name} was found in {folder}.")
        return [_folder_file(folder, name) for name in available.values()]
    return [_folder_file(folder, spec.file_name_for(month)) for month in months]


def _resolve_files(
    spec: EntitySpec,
    root: Path,
    *,
    refresh: bool,
    months: list[str] | None,
    from_dir: Path | None,
    allow_large: bool = False,
) -> list[Path]:
    """Return every file to land, in landing order, before anything is landed."""
    if months is not None:
        if not spec.is_monthly:
            raise LandingError(f"{spec.table} has a single file, so --months does not apply.")
        months = _check_months(list(months))
    if from_dir is not None:
        paths = _files_in_folder(spec, Path(from_dir), months)
    elif spec.is_monthly:
        wanted = months if months is not None else _check_months(list(spec.default_months))
        if not wanted:
            raise LandingError(f"{spec.table} needs at least one month.")
        large = [m for m in wanted if spec.large_before is not None and m < spec.large_before]
        if large and not allow_large:
            raise LandingError(
                f"The files for {', '.join(large)} are 110 to 470 MB each, so they are not "
                "downloaded by default. Pass --allow-large to download them, or load files "
                "you already have with --from-dir DIR."
            )
        paths = [download(spec, raw_dir(root), refresh=refresh, month=month) for month in wanted]
    else:
        paths = [download(spec, raw_dir(root), refresh=refresh)]
    for path in paths:
        _check_file_start(spec, path)
    return paths


def load_entity(
    spec: EntitySpec,
    root: Path,
    *,
    refresh: bool = False,
    months: list[str] | None = None,
    from_dir: Path | None = None,
    allow_large: bool = False,
    sink=None,
) -> int:
    """Resolve the files, land them, and return the rows landed over all files.

    Files come from `from_dir` (every matching file, or only `months` when given) or from the
    download cache (`months`, else the spec's default months). All files are resolved before
    any is landed, so one missing month stops the call before landing begins. A download of a
    month before the spec's `large_before` needs `allow_large`; a folder load never does.

    Without a `sink` the files land in the DuckDB database. With one (the Lakehouse sink), each
    file is normalised by the same landing code in a temporary DuckDB file and then handed to
    `sink.ship(con, spec, file_name)`, which returns the rows it wrote.
    """
    paths = _resolve_files(
        spec,
        root,
        refresh=refresh,
        months=months,
        from_dir=from_dir,
        allow_large=allow_large,
    )
    if sink is not None:
        return _load_through_sink(spec, paths, sink)
    db_file = duckdb_path(root)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    current = paths[0].name if paths else spec.file_name
    total = 0
    try:
        con = duckdb.connect(str(db_file))
        try:
            ensure_landing_table(con, spec)
            for path in paths:
                current = path.name
                total += land_file(con, spec, path)
            return total
        finally:
            con.close()
    except duckdb.Error as exc:
        raise LandingError(f"DuckDB could not land {current} into {db_file}: {exc}") from exc
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise LandingError(f"{current} could not be read: {exc}") from exc


def _load_through_sink(spec: EntitySpec, paths: list[Path], sink) -> int:
    """Normalise each file in a temporary DuckDB file, then ship it. Return the rows shipped.

    The temporary database is a file, not `:memory:`, so a month of millions of rows spills to
    disk instead of staying in memory. It is removed when the load ends.
    """
    current = paths[0].name if paths else spec.file_name
    total = 0
    try:
        with tempfile.TemporaryDirectory(prefix="starter-landing-") as folder:
            con = duckdb.connect(str(Path(folder) / "normalised.duckdb"))
            try:
                ensure_landing_table(con, spec)
                for path in paths:
                    current = path.name
                    land_file(con, spec, path)
                    total += sink.ship(con, spec, path.name)
                return total
            finally:
                con.close()
    except duckdb.Error as exc:
        raise LandingError(f"DuckDB could not normalise {current} for the sink: {exc}") from exc
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise LandingError(f"{current} could not be read: {exc}") from exc

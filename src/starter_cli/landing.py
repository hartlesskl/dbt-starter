"""Generic landing loader: download a file once, then replace that file's rows in landing.

Nothing in this module names a data source. A source describes itself with an `EntitySpec`
(one per source sub-package under `starter_cli`) and this module does the rest.

Landing convention: every data column is text (an empty cell becomes NULL, every other value
keeps its published text), and four lineage columns are added: `_source_file`,
`_source_row_number`, `_batch_id` and `_loaded_at`.

A file reaches landing in one of two ways: it is downloaded into the cache, or it is read from
a local folder (`from_dir`). Either way `land_file` is the one place that writes landing rows.
"""

from __future__ import annotations

import csv
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
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
MONTH_TOKEN = "{month}"
FILE_FORMATS = ("csv", "parquet")
PARQUET_MAGIC = b"PAR1"

USER_AGENT = "dbt-starter-loader/0.1"
REQUEST_TIMEOUT_SECONDS = 60
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
        if (MONTH_TOKEN in self.url) != (MONTH_TOKEN in self.file_name):
            raise ValueError(f"{MONTH_TOKEN} must appear in both url and file_name, or in neither")
        if self.file_name.count(MONTH_TOKEN) > 1 or self.url.count(MONTH_TOKEN) > 1:
            raise ValueError(f"{MONTH_TOKEN} may appear only once in url and in file_name")
        for month in self.default_months:
            if not MONTH_RE.match(month):
                raise ValueError(f"default month {month!r} must look like YYYY-MM")
        sample = self.file_name_for("2024-01" if self.is_monthly else None)
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


def _blocked_message(url: str, reason: str) -> str:
    return (
        f"Could not download {url} ({reason}). "
        "The file is blocked or unavailable from this machine. "
        "See README.md#troubleshooting."
    )


def _expected_start(spec: EntitySpec) -> str:
    return "CSV header" if spec.file_format == "csv" else "Parquet magic bytes (PAR1)"


def download(
    spec: EntitySpec,
    raw_root: Path,
    *,
    refresh: bool = False,
    month: str | None = None,
    sleep=None,
    jitter=None,
) -> Path:
    """Download the entity's file into the cache and return its path.

    `month` picks one file of a monthly entity (`YYYY-MM`); a one-file entity takes none.

    A cached file is returned without any network access unless `refresh` is true, after a
    check that it still starts with the expected header (an empty or hand-edited cache file
    stops here with a message, not later inside the database). Only an HTTP 200 body that
    starts with the expected header reaches the cache, and it is written to a temporary name
    first, so an error page or partial body is never cached.
    """
    file_name = spec.file_name_for(month)
    url = spec.url_for(month)
    dest = raw_root / spec.source / file_name
    if dest.exists() and not refresh:
        with open(dest, "rb") as handle:
            start = handle.read(len(spec.header_prefix))
        if not start.startswith(spec.header_prefix):
            raise DownloadError(
                f"The cached file {dest} does not start with the expected {_expected_start(spec)}. "
                "Delete it, or run again with --refresh to download it again."
            )
        return dest

    try:
        response = requests.get(
            url,
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
        )
    except requests.RequestException as exc:
        raise DownloadError(_blocked_message(url, type(exc).__name__)) from exc

    if response.status_code != 200:
        raise DownloadError(_blocked_message(url, f"HTTP {response.status_code}"))
    if not response.content.startswith(spec.header_prefix):
        raise DownloadError(
            _blocked_message(
                url, f"the response did not start with the expected {_expected_start(spec)}"
            )
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


def _land_csv(con: duckdb.DuckDBPyConnection, spec: EntitySpec, path: Path) -> None:
    mapped = _map_columns(spec, _read_headers(path))

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


def _parquet_file_columns(con: duckdb.DuckDBPyConnection, path: Path) -> dict[str, str]:
    """Return {lowercase name: name as written} for the file's columns, in file order.

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
    described = [
        row[0]
        for row in con.execute("describe select * from read_parquet(?)", [str(path)]).fetchall()
    ]
    if described != leaves:
        raise LandingError(
            f"{path.name} has nested or repeated columns, which landing does not support "
            f"(the file lists {leaves!r} but reads as {described!r})."
        )
    return by_lower


def _land_parquet(con: duckdb.DuckDBPyConnection, spec: EntitySpec, path: Path) -> None:
    by_lower = _parquet_file_columns(con, path)
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
    batch_id = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
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
        f"nullif(cast({_quote(by_lower[column])} as varchar), '')"
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


def land_file(con: duckdb.DuckDBPyConnection, spec: EntitySpec, path: Path) -> int:
    """Replace this file's rows in landing in one transaction and return their count."""
    if spec.file_format == "parquet":
        _land_parquet(con, spec, path)
    else:
        _land_csv(con, spec, path)
    row = con.execute(
        f"select count(*) from landing.{spec.table} where _source_file = ?", [path.name]
    ).fetchone()
    return int(row[0])


def _check_file_start(spec: EntitySpec, path: Path) -> None:
    """Raise LandingError unless the file starts (and, for Parquet, ends) as expected."""
    try:
        with open(path, "rb") as handle:
            start = handle.read(len(spec.header_prefix))
            end = b""
            if spec.file_format == "parquet":
                handle.seek(0, os.SEEK_END)
                if handle.tell() < 2 * len(PARQUET_MAGIC):
                    start = b""
                else:
                    handle.seek(-len(PARQUET_MAGIC), os.SEEK_END)
                    end = handle.read(len(PARQUET_MAGIC))
    except OSError as exc:
        raise LandingError(f"{path.name} could not be read: {exc}") from exc
    if not start.startswith(spec.header_prefix):
        raise LandingError(f"{path.name} does not start with the expected {_expected_start(spec)}.")
    if spec.file_format == "parquet" and end != PARQUET_MAGIC:
        raise LandingError(
            f"{path.name} does not end with the Parquet magic bytes (PAR1): it may be cut short."
        )


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
) -> int:
    """Resolve the files, land them in DuckDB, and return the rows landed over all files.

    Files come from `from_dir` (every matching file, or only `months` when given) or from the
    download cache (`months`, else the spec's default months). All files are resolved before
    any is landed, so one missing month stops the call before landing begins.
    """
    paths = _resolve_files(spec, root, refresh=refresh, months=months, from_dir=from_dir)
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

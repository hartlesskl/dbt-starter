"""The swap-out guide builds: its files, applied to a skeleton copy, land a sample and run (ADPT-04).

`docs/guide/swap-out.md` asks the reader to create files. Every one is a fenced block whose info
string is `<language> file=<repo path>`. This test reads those fences with the Markdown parser,
writes exactly those files into a copy of the template (after `remove-example` when the copy still
holds the example), lands a committed sample of the guide's data, builds every layer on DuckDB,
compiles the project offline for Fabric and scans the compiled Spark SQL of the new models. So the
guide cannot drift from the code: a step that stops building fails here (D-14).

Nothing is downloaded. The sample is a few dozen rows kept as Python literals in
`tests/guide/usgs_sample.py`; this test writes them to a temporary folder and lands them with
`--from-dir`, and the copy must hold no download cache afterwards. The dbt packages come from the
checkout's `dbt_packages/` when it exists; otherwise `dbt deps` runs in the copy.

The real counts are derived from the sample, never typed: the real rows are kept, the planted rows
go to quarantine with the reason codes the sample names, and the real rows with a depth below zero
are the warnings.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

import duckdb
import pytest
from guide import usgs_sample
from markdown_it import MarkdownIt
from support.dbt_cli import (
    DUMMY_FABRIC_ENV,
    RELAXED_LEVELS_ARGS,
    REPO_ROOT,
    node_results,
    require_fabric_adapter,
)
from support.sql_scan import scan

from starter_cli.skeleton import COMMIT_IDENTITY, copy_env, make_copy

GUIDE = REPO_ROOT / "docs" / "guide" / "swap-out.md"

# The guide's `## ` headings, in order. test_guides.py holds the guide to this tuple.
GUIDE_HEADINGS: tuple[str, ...] = (
    "Before you start",
    "1. Describe the source",
    "2. Declare the landing table",
    "3. Type it in bronze",
    "4. Write the rules",
    "5. Split silver and quarantine",
    "6. Test it",
    "7. Document it",
    "8. Build and check",
    "Download the real data",
)

FILE_KEY = "file="
SOURCE = "usgs"
ENTITY = "quakes"
LANDING = "landing.usgs__quakes"
BRONZE = "bronze.usgs__quakes"
SILVER = "silver.quakes"
QUARANTINE = "silver.quakes_quarantine"
UNIT_TESTS = (
    "quake_id_missing_rule",
    "quake_time_unparseable_rule",
    "quake_magnitude_missing_rule",
    "quake_latitude_invalid_rule",
    "quake_depth_negative_rule",
)


def guide_files(text: str) -> dict[str, str]:
    """Return `{path: content}` for every fence whose info string is `<lang> file=<path>`.

    A fence without `file=` is illustrative and is skipped. A path that is absolute, has a `..`
    part, is empty or repeats an earlier path raises ValueError, so a guide can write only inside
    the folder it is applied to.
    """
    files: dict[str, str] = {}
    for token in MarkdownIt("commonmark").parse(text):
        if token.type != "fence":
            continue
        paths = [part[len(FILE_KEY) :] for part in token.info.split() if part.startswith(FILE_KEY)]
        if not paths:
            continue
        path = paths[0]
        parts = PurePosixPath(path).parts
        if not path or path.startswith(("/", "\\")) or PurePosixPath(path).is_absolute():
            raise ValueError(f"the guide path {path!r} is absolute or empty")
        if ".." in parts or ":" in parts[0] or "\\" in path:
            raise ValueError(f"the guide path {path!r} leaves the folder it is applied to")
        if path in files:
            raise ValueError(f"the guide writes {path!r} twice")
        files[path] = token.content
    return files


# --- guide_files, no build needed ---


def fence(info: str, body: str = "x\n") -> str:
    return f"```{info}\n{body}```\n"


def test_guide_files_reads_the_path_after_file_equals() -> None:
    text = fence("sql file=models/a.sql", "select 1\n") + fence("yaml file=models/b.yml", "a: 1\n")
    assert guide_files(text) == {"models/a.sql": "select 1\n", "models/b.yml": "a: 1\n"}


def test_guide_files_skips_a_fence_with_no_file_and_text_outside_a_fence() -> None:
    text = "file=models/x.sql in prose\n\n" + fence("text") + fence("sql") + fence("")
    assert guide_files(text) == {}


@pytest.mark.parametrize("path", ["/etc/passwd", "../outside.sql", "models/../../x.sql", "C:/x"])
def test_guide_files_refuses_a_path_that_leaves_the_folder(path: str) -> None:
    with pytest.raises(ValueError, match="absolute|leaves"):
        guide_files(fence(f"sql file={path}"))


def test_guide_files_refuses_a_duplicate_path() -> None:
    text = fence("sql file=models/a.sql") + fence("sql file=models/a.sql")
    with pytest.raises(ValueError, match="twice"):
        guide_files(text)


def test_the_real_guide_holds_the_files_it_names() -> None:
    files = guide_files(GUIDE.read_text(encoding="utf-8"))
    assert "src/starter_cli/usgs/__init__.py" in files
    assert "macros/dq/dq_entities.sql" in files
    assert len(files) >= 10


def test_the_real_guide_has_its_headings_in_order() -> None:
    tokens = MarkdownIt("commonmark").parse(GUIDE.read_text(encoding="utf-8"))
    headings = tuple(
        tokens[index + 1].content
        for index, token in enumerate(tokens)
        if token.type == "heading_open" and token.tag == "h2"
    )
    assert headings == GUIDE_HEADINGS


# --- the sample, no build needed ---


def test_the_sample_rows_have_the_published_shape() -> None:
    width = len(usgs_sample.HEADER)
    assert width == 22
    assert all(len(row) == width for row in [*usgs_sample.SAMPLE_ROWS, *usgs_sample.PLANTED_ROWS])
    assert 1 <= len(usgs_sample.PLANTED_ROWS) <= 3
    assert len(usgs_sample.PLANTED_ROWS) == len(usgs_sample.PLANTED_REASON_CODES)


def test_write_sample_writes_header_then_real_then_planted_rows(tmp_path: Path) -> None:
    path = usgs_sample.write_sample(tmp_path / "in")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert path.name == "quakes.csv"
    assert lines[0].startswith("time,latitude,longitude,depth,mag,magType")
    assert len(lines) == 1 + len(usgs_sample.SAMPLE_ROWS) + len(usgs_sample.PLANTED_ROWS)


# --- the build ---


@dataclass
class Built:
    """What the scripted guide produced in one copy."""

    copy: Path
    database: Path
    steps: dict[str, subprocess.CompletedProcess[str]] = field(default_factory=dict)
    probe: dict[str, object] = field(default_factory=dict)

    def tail(self, step: str) -> str:
        result = self.steps[step]
        return "\n".join((result.stdout + result.stderr).splitlines()[-60:])


def _run(copy: Path, args: list[str], extra_env: dict[str, str] | None = None):
    return subprocess.run(
        [sys.executable, *args],
        cwd=copy,
        env={**copy_env(copy), **(extra_env or {})},
        capture_output=True,
        text=True,
        check=False,
    )


def _dbt(*args: str) -> list[str]:
    return ["-m", "dbt.cli.main", *args, "--profiles-dir", ".", "--project-dir", "."]


def _git(copy: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *COMMIT_IDENTITY, *args], cwd=copy, capture_output=True, text=True, check=False
    )


PROBE = (
    "import json; from starter_cli.usgs import DATA_TERMS, DOCS_ATTRIBUTION, ENTITIES; "
    "spec = ENTITIES['quakes']; "
    "print(json.dumps({'url': spec.url, 'columns': list(spec.columns), "
    "'file_name': spec.file_name, 'terms_name': DATA_TERMS['name'], "
    "'attribution': list(DOCS_ATTRIBUTION)}))"
)


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Built:
    """Apply the guide to a copy, land the sample and build, once for the module."""
    require_fabric_adapter()
    base = tmp_path_factory.mktemp("swap_out")
    copy = make_copy(REPO_ROOT, base / "copy")
    built = Built(copy=copy, database=copy / "target" / "skeleton" / "demo.duckdb")
    built.database.parent.mkdir(parents=True, exist_ok=True)

    if (copy / "skeleton").is_dir():
        built.steps["remove"] = _run(copy, ["-m", "starter_cli.remove_example", "--apply"])
        assert built.steps["remove"].returncode == 0, built.tail("remove")
        committed = _git(copy, "commit", "-q", "--allow-empty", "-m", "remove the example")
        assert committed.returncode == 0, committed.stderr

    for relative, content in guide_files(GUIDE.read_text(encoding="utf-8")).items():
        target = copy / relative
        assert target.resolve().is_relative_to(copy.resolve()), relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")

    if (REPO_ROOT / "dbt_packages").is_dir():
        shutil.copytree(REPO_ROOT / "dbt_packages", copy / "dbt_packages")
    else:
        built.steps["deps"] = _run(copy, _dbt("deps"))
        assert built.steps["deps"].returncode == 0, built.tail("deps")

    sample_folder = base / "in"
    usgs_sample.write_sample(sample_folder)
    built.steps["load_placeholder"] = _run(
        copy, ["-m", "starter_cli.load", "example_source", "items"]
    )
    built.steps["load"] = _run(
        copy, ["-m", "starter_cli.load", SOURCE, ENTITY, "--from-dir", str(sample_folder)]
    )
    built.steps["probe"] = _run(copy, ["-c", PROBE])
    if built.steps["probe"].returncode == 0:
        built.probe = json.loads(built.steps["probe"].stdout)
    built.steps["build"] = _run(copy, _dbt("build", *RELAXED_LEVELS_ARGS))
    built.steps["compile"] = _run(
        copy,
        _dbt(
            "compile", "--target", "fabric", "--no-populate-cache", "--target-path", "target/fabric"
        ),
        DUMMY_FABRIC_ENV,
    )
    return built


@pytest.fixture(scope="module")
def con(built: Built):
    connection = duckdb.connect(str(built.database), read_only=True)
    try:
        yield connection
    finally:
        connection.close()


def scalar(con: duckdb.DuckDBPyConnection, sql: str) -> object:
    return con.execute(sql).fetchone()[0]


REAL = usgs_sample.SAMPLE_ROWS
PLANTED = usgs_sample.PLANTED_ROWS
ALL_ROWS = len(REAL) + len(PLANTED)
DEPTH_COLUMN = usgs_sample.HEADER.index("depth")
BELOW_DATUM = sum(1 for row in REAL if float(row[DEPTH_COLUMN]) < 0)


def test_the_guide_files_land_and_build_on_duckdb(built: Built) -> None:
    for step in ("load_placeholder", "load", "build"):
        assert built.steps[step].returncode == 0, f"{step} failed:\n{built.tail(step)}"


def test_the_source_module_matches_the_sample(built: Built) -> None:
    assert built.probe, built.tail("probe")
    assert built.probe["url"] == usgs_sample.SAMPLE_URL
    assert built.probe["file_name"] == usgs_sample.FILE_NAME
    assert tuple(built.probe["columns"]) == tuple(name.lower() for name in usgs_sample.HEADER)
    assert "U.S. Geological Survey" in str(built.probe["terms_name"])


def test_landing_bronze_silver_and_quarantine_counts_come_from_the_sample(
    con: duckdb.DuckDBPyConnection,
) -> None:
    assert scalar(con, f"select count(*) from {LANDING}") == ALL_ROWS
    assert scalar(con, f"select count(*) from {BRONZE}") == ALL_ROWS
    assert scalar(con, f"select count(*) from {SILVER}") == len(REAL)
    assert scalar(con, f"select count(*) from {QUARANTINE}") == len(PLANTED)


def test_each_planted_row_fails_exactly_its_reason_codes(con: duckdb.DuckDBPyConnection) -> None:
    rows = con.execute(
        f"select dq_reason_codes from {QUARANTINE} order by _source_row_number"
    ).fetchall()
    assert [code for (code,) in rows] == list(usgs_sample.PLANTED_REASON_CODES)
    planted_ids = [row[usgs_sample.HEADER.index("id")] for row in PLANTED]
    named = [pid for pid in planted_ids if pid]
    kept = con.execute(
        f"select count(*) from {SILVER} where quake_id in ({', '.join('?' * len(named))})", named
    ).fetchone()[0]
    assert kept == 0


def test_the_real_rows_with_a_negative_depth_are_warnings_and_stay_in_silver(
    con: duckdb.DuckDBPyConnection,
) -> None:
    assert BELOW_DATUM >= 1, "the sample no longer holds a real row above the datum"
    warned = con.execute(
        f"select dq_warning_codes from {SILVER} where dq_warning_count > 0"
    ).fetchall()
    assert [codes for (codes,) in warned] == ["QUAKE_DEPTH_NEGATIVE"] * BELOW_DATUM


def test_the_z_is_removed_and_the_time_lands_as_a_utc_timestamp(
    con: duckdb.DuckDBPyConnection,
) -> None:
    first = REAL[0][usgs_sample.HEADER.index("id")]
    stamp = scalar(con, f"select event_time from {SILVER} where quake_id = '{first}'")
    assert stamp == datetime(2024, 1, 1, 4, 5, 2, 992000)
    raw = scalar(con, f"select time__raw from {BRONZE} where quake_id = '{first}'")
    assert str(raw).endswith("Z")


def test_the_offset_time_is_kept_zoned_and_quarantined(con: duckdb.DuckDBPyConnection) -> None:
    raw = scalar(
        con, f"select time__raw from {QUARANTINE} where dq_fail_quake_time_unparseable = 1"
    )
    assert raw == usgs_sample.PLANTED_ROWS[1][0]


def test_every_usgs_test_passes_and_none_warns(built: Built) -> None:
    results = node_results(built.copy / "target" / "run_results.json")
    mine = [item for item in results if "usgs" in item["unique_id"] or "quake" in item["unique_id"]]
    assert len(mine) >= 30, "far fewer usgs nodes than the guide defines"
    assert {item["status"] for item in mine} <= {"success", "pass"}


def test_the_five_rule_unit_tests_ran_and_passed(built: Built) -> None:
    results = {
        item["unique_id"]: item["status"]
        for item in node_results(built.copy / "target" / "run_results.json")
    }
    for name in UNIT_TESTS:
        matches = [status for uid, status in results.items() if uid.endswith(f".{name}")]
        assert matches == ["pass"], f"{name}: {matches}"


def test_the_copy_compiles_for_fabric_and_the_new_models_pass_the_spark_scan(
    built: Built,
) -> None:
    assert built.steps["compile"].returncode == 0, built.tail("compile")
    compiled = built.copy / "target" / "fabric" / "compiled"
    mine = [
        path
        for path in sorted(compiled.rglob("*.sql"))
        if SOURCE in path.relative_to(compiled).parts
    ]
    names = {path.name for path in mine}
    assert {
        "bronze__usgs__quakes.sql",
        "silver__quakes.sql",
        "silver__quakes_quarantine.sql",
    } <= names
    problems = {
        str(path.relative_to(compiled)): found
        for path in mine
        if (found := scan(path.read_text(encoding="utf-8")))
    }
    assert problems == {}


def test_no_download_cache_appears_for_the_guide_source(built: Built) -> None:
    assert not (built.copy / "data" / "raw" / SOURCE).exists()

"""Re-release and re-delivery, proven on the invented fixture (QUAR-09, TEST-06).

Re-release is a property of full-rebuild tables: bronze, silver and quarantine are rebuilt from
landing on every `dbt build`, so a fixed code mapping or a fixed rule releases rows on the next
build with no other step. The proof is a lifecycle on the fixture: fix a code mapping, fix a
rule, re-deliver a file, and after each step only `dbt build` runs and every count is exact.

The fixes go into a temporary copy of the project; the committed seed and rule macro never
change (the test compares their hashes before and after). Landing and builds use one DuckDB file
under pytest's tmp folder, never the demo database and never the gate's fixture database.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import duckdb
from nyc_taxi.test_dq_models import COPY_IGNORE
from support.dbt_cli import RELAXED_LEVELS_ARGS, REPO_ROOT, run_dbt

from starter_cli.gate import gate_database
from starter_cli.landing import load_entity
from starter_cli.nyc_taxi import ENTITIES
from starter_cli.nyc_taxi.fixture import (
    FILE_2025_01,
    JAN_2025,
    _trip,
    _write_trip_parquet,
    write_fixture,
)
from starter_cli.project import duckdb_path

VENDOR_SEED = Path("seeds") / "nyc_taxi" / "nyc_taxi__vendors.csv"
RULE_MACRO = Path("macros") / "rules" / "nyc_taxi" / "yellow_trips.sql"
FILE_NAME = "yellow_tripdata_2025-01.parquet"
LANDING = "landing.nyc_taxi__yellow_trips"
BRONZE = "bronze.nyc_taxi__yellow_trips"
SILVER = "silver.yellow_trips"
QUARANTINE = "silver.yellow_trips_quarantine"
FACT = "gold.fct_trips"
NO_UNIT_TESTS = ["--exclude", "resource_type:unit_test"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_a_seed_fix_and_a_rule_fix_release_rows_and_a_redelivered_file_counts_once(
    dbt_packages_ready: None, tmp_path: Path, capsys, monkeypatch
) -> None:
    committed = {path: sha256(REPO_ROOT / path) for path in (VENDOR_SEED, RULE_MACRO)}

    files = tmp_path / "in"
    write_fixture(files)
    database = tmp_path / "lifecycle.duckdb"
    assert database.resolve() != duckdb_path(REPO_ROOT).resolve()
    assert database.resolve() != gate_database(REPO_ROOT).resolve()
    monkeypatch.setenv("DBT_DUCKDB_PATH", str(database))
    load_entity(ENTITIES["taxi_zones"], REPO_ROOT, from_dir=files)
    load_entity(ENTITIES["yellow_trips"], REPO_ROOT, from_dir=files)

    copy = tmp_path / "proj"
    shutil.copytree(REPO_ROOT, copy, symlinks=True, ignore=COPY_IGNORE, dirs_exist_ok=True)

    def build(step: str, *extra: str) -> None:
        run_dbt(
            ["build", "--target-path", str(copy / f"t_{step}"), *RELAXED_LEVELS_ARGS, *extra],
            project_dir=copy,
            env={"DBT_DUCKDB_PATH": str(database)},
        )

    def counts() -> dict[str, int]:
        con = duckdb.connect(str(database), read_only=True)
        try:
            return {
                name: con.execute(f"select count(*) from {table}").fetchone()[0]
                for name, table in {
                    "landing": LANDING,
                    "bronze": BRONZE,
                    "silver": SILVER,
                    "quarantine": QUARANTINE,
                    "fact": FACT,
                }.items()
            }
        finally:
            con.close()

    def check(silver: int, quarantine: int, bronze: int) -> None:
        found = counts()
        assert found == {
            "landing": bronze,
            "bronze": bronze,
            "silver": silver,
            "quarantine": quarantine,
            "fact": silver,
        }
        assert found["silver"] + found["quarantine"] == found["bronze"]

    # Step 0: the baseline on the unchanged copy.
    build("base")
    check(silver=18, quarantine=41, bronze=59)

    # Step 1: a code mapping is fixed. Vendor 3 becomes known, so its rows leave quarantine
    # (the third vendor-3 row stays there as an exact duplicate).
    seed = copy / VENDOR_SEED
    seed.write_text(seed.read_text().rstrip("\n") + "\n3,Test Vendor Three\n")
    build("seed")
    check(silver=20, quarantine=39, bronze=59)

    # Step 2: a rule is fixed. The longest allowed trip goes from 24 to 25 hours.
    macro = copy / RULE_MACRO
    text = macro.read_text()
    assert "return(86400)" in text
    macro.write_text(text.replace("return(86400)", "return(90000)", 1))
    build("rule", *NO_UNIT_TESTS)  # one unit test pins the 86400 limit
    check(silver=21, quarantine=38, bronze=59)

    # Step 3a: the identical file comes back. Nothing changes and nothing is said.
    capsys.readouterr()
    load_entity(ENTITIES["yellow_trips"], REPO_ROOT, from_dir=files, months=["2025-01"])
    assert "Re-landed" not in capsys.readouterr().out
    assert counts()["landing"] == 59

    # Step 3b: a changed copy of the file comes back under the same name, with one more trip.
    changed = tmp_path / "changed"
    changed.mkdir()
    extra_trip = _trip(JAN_2025, 4, cbd_congestion_fee=0.75)
    _write_trip_parquet(
        changed / FILE_NAME, FILE_2025_01._replace(rows=(*FILE_2025_01.rows, extra_trip))
    )
    new_batch = hashlib.sha256((changed / FILE_NAME).read_bytes()).hexdigest()[:16]
    old_batch = hashlib.sha256((files / FILE_NAME).read_bytes()).hexdigest()[:16]
    capsys.readouterr()
    load_entity(ENTITIES["yellow_trips"], REPO_ROOT, from_dir=changed, months=["2025-01"])
    notices = [line for line in capsys.readouterr().out.splitlines() if "Re-landed" in line]
    assert notices == [
        f"Re-landed {FILE_NAME}: content changed, rows 3 -> 4, batch {old_batch} -> {new_batch}"
    ]
    con = duckdb.connect(str(database), read_only=True)
    try:
        landed = con.execute(
            f"select count(*), count(distinct _batch_id), min(_batch_id) from {LANDING} "
            "where _source_file = ?",
            [FILE_NAME],
        ).fetchone()
    finally:
        con.close()
    assert landed == (4, 1, new_batch)
    build("redelivered", *NO_UNIT_TESTS)
    check(silver=22, quarantine=38, bronze=60)

    # The fixes lived in the copy only.
    assert {path: sha256(REPO_ROOT / path) for path in committed} == committed

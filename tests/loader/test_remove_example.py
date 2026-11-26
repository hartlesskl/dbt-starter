"""remove-example on temporary git repositories only, never on this checkout (ADPT-03, D-08, D-10).

Every test builds its own repository under `tmp_path`, so nothing here can touch the real tree.
The example's directory name and the marker words come from `starter_cli.markers`, the one
definition, so this file names no source and needs no handle-test exemption.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from starter_cli import markers, remove_example

HANDLE = markers.HANDLE_DIR
MD_BEGIN = f"<!-- {markers.BEGIN} -->"
MD_END = f"<!-- {markers.END} -->"
PY_BEGIN = f"# {markers.BEGIN}"
PY_END = f"# {markers.END}"
SQL_BEGIN = f"{{#- {markers.BEGIN} -#}}"
SQL_END = f"{{#- {markers.END} -#}}"

README = f"""# Project

## Status

Verified on Fabric 2026-01-02: something, see [the record](docs/verification/fabric-2026-01-02.md).

The label is dated.

## Run

Generic text.

{MD_BEGIN}
Text only the example needs.
{MD_END}

## Done

End.
"""

PY_FILE = f"""def keep() -> int:
    return 1


{PY_BEGIN}
def only_example() -> int:
    return 2
{PY_END}


def keep_too() -> int:
    return 3
"""

SQL_FILE = f"""select 1 as a
{SQL_BEGIN}
union all
select 2 as a
{SQL_END}
"""

FILES = {
    "README.md": README,
    "dbt_project.yml": "name: t\n",
    "pyproject.toml": "[project]\nname = 't'\n",
    f"models/{HANDLE}/m.sql": "select 1\n",
    f"a/{HANDLE}/x.txt": "x\n",
    f"a/{HANDLE}x/y.txt": "kept: the name is not exactly the handle\n",
    "docs/verification/fabric-2026-01-02.md": "record\n",
    "src/mod.py": PY_FILE,
    "models/q.sql": SQL_FILE,
    "macros/dq/x.sql": "-- generic\nselect 'old'\n",
    "skeleton/macros/dq/x.sql": "-- stub\nselect 'new'\n",
    "skeleton/models/stub.sql": "select 'stub'\n",
    ".github/workflows/skeleton.yml": "name: skeleton\n",
    "tests/conventions/test_skeleton_workflow.py": "def test_it() -> None: ...\n",
    ".github/workflows/gate.yml": "name: gate\n",
    "only_blocks.md": f"{MD_BEGIN}\nonly example text\n{MD_END}\n",
}


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@localhost", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def make_repo(root: Path, files: dict[str, str]) -> Path:
    """Write `files` under `root`, then `git init`, add everything and commit."""
    root.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "start")
    return root


def snapshot(root: Path) -> dict[str, bytes]:
    """Every file under `root` outside `.git`, as bytes."""
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "repo", FILES)


def test_a_dry_run_lists_the_plan_and_changes_nothing(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = snapshot(repo)
    status_before = git(repo, "status", "--porcelain")
    code = remove_example.run(repo, apply=False)
    out = capsys.readouterr().out
    assert code == 0
    assert f"models/{HANDLE}/m.sql" in out
    assert f"a/{HANDLE}/x.txt" in out
    assert "docs/verification/fabric-2026-01-02.md" in out
    assert "README.md:13-15" in out
    assert "src/mod.py:5-8" in out
    assert "models/q.sql:2-5" in out
    assert "skeleton/macros/dq/x.sql -> macros/dq/x.sql" in out
    assert "README Status" in out
    assert "dry run" in out
    assert snapshot(repo) == before
    assert git(repo, "status", "--porcelain") == status_before == ""


def test_the_template_only_workflow_and_its_test_are_listed_and_deleted(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert remove_example.TEMPLATE_ONLY_PATHS == (
        ".github/workflows/skeleton.yml",
        "tests/conventions/test_skeleton_workflow.py",
    )
    assert remove_example.run(repo, apply=False) == 0
    out = capsys.readouterr().out
    for path in remove_example.TEMPLATE_ONLY_PATHS:
        assert path in out
        assert (repo / path).is_file()
    assert remove_example.run(repo, apply=True) == 0
    for path in remove_example.TEMPLATE_ONLY_PATHS:
        assert not (repo / path).exists()
    assert (repo / ".github" / "workflows" / "gate.yml").is_file()


def test_a_name_that_only_starts_with_the_handle_is_not_a_target(repo: Path) -> None:
    removal = remove_example.plan_removal(repo)
    assert f"a/{HANDLE}x/y.txt" not in removal.deletes
    assert f"a/{HANDLE}/x.txt" in removal.deletes


def test_apply_removes_strips_overlays_and_stages_without_committing(repo: Path) -> None:
    head = git(repo, "rev-parse", "HEAD")
    code = remove_example.run(repo, apply=True)
    assert code == 0

    assert not (repo / "models" / HANDLE).exists()
    assert not (repo / "a" / HANDLE).exists()
    assert (repo / "a" / f"{HANDLE}x" / "y.txt").is_file()
    assert not (repo / "docs" / "verification").exists()

    readme = (repo / "README.md").read_text(encoding="utf-8")
    assert markers.BEGIN not in readme
    assert "Text only the example needs." not in readme
    assert "Generic text." in readme
    assert remove_example.UNVERIFIED_STATUS in readme
    assert "Verified on Fabric" not in readme

    py = (repo / "src" / "mod.py").read_text(encoding="utf-8")
    assert "only_example" not in py and "def keep_too" in py
    sql = (repo / "models" / "q.sql").read_text(encoding="utf-8")
    assert sql == "select 1 as a\n"

    assert (repo / "macros" / "dq" / "x.sql").read_text(encoding="utf-8").endswith("'new'\n")
    assert (repo / "models" / "stub.sql").read_text(encoding="utf-8") == "select 'stub'\n"
    assert not (repo / "skeleton").exists()
    assert not (repo / "only_blocks.md").exists()  # left empty, so deleted

    staged = set(git(repo, "diff", "--cached", "--name-only", "--no-renames").split())
    assert staged == {
        f"models/{HANDLE}/m.sql",
        f"a/{HANDLE}/x.txt",
        "docs/verification/fabric-2026-01-02.md",
        "README.md",
        "src/mod.py",
        "models/q.sql",
        "macros/dq/x.sql",
        "models/stub.sql",
        "skeleton/macros/dq/x.sql",
        "skeleton/models/stub.sql",
        "only_blocks.md",
        ".github/workflows/skeleton.yml",
        "tests/conventions/test_skeleton_workflow.py",
    }
    porcelain = git(repo, "status", "--porcelain", "--no-renames").splitlines()
    assert len(porcelain) == len(staged)
    assert all(line[1] == " " for line in porcelain), "nothing is left unstaged"
    assert git(repo, "rev-parse", "HEAD") == head


def status_line(text: str) -> str:
    """The first non-empty line under `## Status`, the way the verification-record test reads it."""
    lines = text.splitlines()
    start = next(index for index, line in enumerate(lines) if line.strip() == "## Status")
    return next(line.strip() for line in lines[start + 1 :] if line.strip())


def test_the_readme_status_reads_as_unverified_after_apply(repo: Path) -> None:
    assert status_line(README).startswith("Verified on Fabric")
    assert remove_example.run(repo, apply=True) == 0
    label = status_line((repo / "README.md").read_text(encoding="utf-8"))
    assert label.startswith("Fabric: not verified live yet in this copy")
    assert remove_example.UNVERIFIED_STATUS.startswith(label)


def test_a_second_apply_after_committing_says_nothing_to_remove(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert remove_example.run(repo, apply=True) == 0
    git(repo, "commit", "-q", "-m", "removed")
    before = snapshot(repo)
    head = git(repo, "rev-parse", "HEAD")
    capsys.readouterr()
    assert remove_example.run(repo, apply=True) == 0
    assert "nothing to remove" in capsys.readouterr().out
    assert snapshot(repo) == before
    assert git(repo, "rev-parse", "HEAD") == head
    assert git(repo, "status", "--porcelain") == ""


# ---------------------------------------------------------------------------------------------
# Refusals and safety (T-06-18 to T-06-21)


def test_a_folder_that_is_not_a_git_work_tree_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plain = tmp_path / "plain"
    (plain / "models" / HANDLE).mkdir(parents=True)
    (plain / "models" / HANDLE / "m.sql").write_text("select 1\n", encoding="utf-8")
    before = snapshot(plain)
    for apply in (False, True):
        assert remove_example.run(plain, apply=apply) == 2
        assert "not a git work tree" in capsys.readouterr().err
    assert snapshot(plain) == before


@pytest.mark.parametrize("apply", [False, True])
def test_an_untracked_file_is_refused_and_named(
    repo: Path, apply: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "notes.txt").write_text("mine\n", encoding="utf-8")
    before = snapshot(repo)
    assert remove_example.run(repo, apply=apply) == 2
    err = capsys.readouterr().err
    assert "?? notes.txt" in err
    assert "commit or stash" in err.lower()
    assert snapshot(repo) == before


@pytest.mark.parametrize("apply", [False, True])
def test_an_unstaged_edit_is_refused_and_named(
    repo: Path, apply: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "src" / "mod.py").write_text("changed = 1\n", encoding="utf-8")
    before = snapshot(repo)
    assert remove_example.run(repo, apply=apply) == 2
    err = capsys.readouterr().err
    assert "M src/mod.py" in err
    assert "commit or stash" in err.lower()
    assert snapshot(repo) == before


def test_a_staged_edit_is_refused_too(repo: Path) -> None:
    (repo / "src" / "mod.py").write_text("changed = 1\n", encoding="utf-8")
    git(repo, "add", "src/mod.py")
    assert remove_example.run(repo, apply=True) == 2
    assert (repo / "models" / HANDLE / "m.sql").is_file()


def test_a_broken_marker_block_is_refused_and_names_the_file(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "models" / "broken.sql").write_text(f"select 1\n{SQL_BEGIN}\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "broken")
    before = snapshot(repo)
    assert remove_example.run(repo, apply=True) == 2
    assert "models/broken.sql" in capsys.readouterr().err
    assert snapshot(repo) == before


def test_a_symlink_among_the_deletes_is_refused_and_nothing_changes(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("not yours\n", encoding="utf-8")
    (repo / "a" / HANDLE / "link.txt").symlink_to(outside)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "link")
    before = snapshot(repo)
    assert remove_example.run(repo, apply=True) == 2
    assert f"a/{HANDLE}/link.txt" in capsys.readouterr().err
    assert outside.read_text(encoding="utf-8") == "not yours\n"
    assert snapshot(repo) == before
    assert git(repo, "status", "--porcelain") == ""


def test_a_symlink_among_the_overlays_is_refused(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("not yours\n", encoding="utf-8")
    (repo / "skeleton" / "models" / "link.sql").symlink_to(outside)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "link")
    assert remove_example.run(repo, apply=True) == 2
    assert "skeleton/models/link.sql" in capsys.readouterr().err
    assert not (repo / "models" / "link.sql").exists()


def test_an_overlay_that_would_land_outside_the_root_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = make_repo(tmp_path / "root", {"skeleton/linked/f.txt": "payload\n", "keep.txt": "k\n"})
    (root / "linked").symlink_to(outside)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "linked folder")
    assert remove_example.run(root, apply=True) == 2
    assert "linked/f.txt" in capsys.readouterr().err
    assert list(outside.iterdir()) == []


def test_a_symlink_to_a_marked_file_is_never_followed(repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "marked.md"
    outside.write_text(f"{MD_BEGIN}\nx\n{MD_END}\n", encoding="utf-8")
    (repo / "docs-link.md").symlink_to(outside)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "link")
    assert remove_example.run(repo, apply=True) == 0
    assert outside.read_text(encoding="utf-8") == f"{MD_BEGIN}\nx\n{MD_END}\n"
    assert (repo / "docs-link.md").is_symlink()


def test_untracked_data_survives_apply_and_is_named_in_the_advice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    files = {**FILES, ".gitignore": "data/*\ntarget/\n"}
    root = make_repo(tmp_path / "repo", files)
    raw = root / "data" / "raw" / HANDLE
    raw.mkdir(parents=True)
    (raw / "x.parquet").write_bytes(b"PAR1 bytes")
    (root / "data" / "dbt_starter.duckdb").write_bytes(b"duck")
    assert git(root, "status", "--porcelain") == ""
    assert remove_example.run(root, apply=True) == 0
    out = capsys.readouterr().out
    assert (raw / "x.parquet").read_bytes() == b"PAR1 bytes"
    assert (root / "data" / "dbt_starter.duckdb").read_bytes() == b"duck"
    assert "data/dbt_starter.duckdb" in out
    assert f"data/raw/{HANDLE}/" in out
    assert not any(path.startswith("data/") for path in git(root, "ls-files").split())


def test_the_removal_is_undone_by_resetting_the_staged_changes(repo: Path) -> None:
    before = snapshot(repo)
    assert remove_example.run(repo, apply=True) == 0
    assert snapshot(repo) != before
    git(repo, "reset", "--hard", "-q", "HEAD")
    assert snapshot(repo) == before
    assert git(repo, "status", "--porcelain") == ""


def test_main_runs_in_the_checkout_that_repo_root_finds(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(remove_example, "repo_root", lambda: repo)
    before = snapshot(repo)
    assert remove_example.main([]) == 0
    assert "dry run" in capsys.readouterr().out
    assert snapshot(repo) == before
    assert remove_example.main(["--apply"]) == 0
    assert not (repo / "skeleton").exists()

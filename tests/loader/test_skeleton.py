"""The skeleton copy helper, fast: temporary git repositories and stubbed runners (CI-05, D-08).

The real run, `uv run --locked --extra fabric skeleton`, is slow and needs the network for
`dbt deps`, so it is not part of this file.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from starter_cli import skeleton

REPO_ROOT = Path(__file__).resolve().parents[2]


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
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "start")
    return root


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = make_repo(
        tmp_path / "source",
        {
            "dbt_project.yml": "name: t\n",
            "pyproject.toml": "[project]\nname = 't'\n",
            "src/starter_cli/__init__.py": "",
            "run.sh": "#!/bin/sh\n",
        },
    )
    (root / "run.sh").chmod(0o755)
    git(root, "add", "run.sh")
    git(root, "commit", "-q", "-m", "mode")
    (root / "untracked.txt").write_text("local data\n", encoding="utf-8")
    return root


def test_the_steps_are_pinned() -> None:
    assert skeleton.STEPS == (
        "copy",
        "remove",
        "gate",
        "demo",
    )
    assert set(skeleton.RUNNERS) == set(skeleton.STEPS)


def test_make_copy_copies_tracked_files_only_and_commits(source: Path, tmp_path: Path) -> None:
    dest = tmp_path / "copy"
    copy = skeleton.make_copy(source, dest)
    assert copy == dest
    assert (copy / "dbt_project.yml").read_text(encoding="utf-8") == "name: t\n"
    assert not (copy / "untracked.txt").exists()
    assert os.access(copy / "run.sh", os.X_OK)
    assert git(copy, "status", "--porcelain") == ""
    assert git(copy, "rev-list", "--count", "HEAD").strip() == "1"
    assert git(copy, "ls-files").split() == git(source, "ls-files").split()


def test_make_copy_keeps_a_working_tree_edit_of_a_tracked_file(
    source: Path, tmp_path: Path
) -> None:
    (source / "dbt_project.yml").write_text("name: edited\n", encoding="utf-8")
    copy = skeleton.make_copy(source, tmp_path / "copy")
    assert (copy / "dbt_project.yml").read_text(encoding="utf-8") == "name: edited\n"


def test_make_copy_refuses_a_symlink(source: Path, tmp_path: Path) -> None:
    (source / "link").symlink_to("dbt_project.yml")
    git(source, "add", "link")
    git(source, "commit", "-q", "-m", "link")
    with pytest.raises(skeleton.SkeletonError, match="symlink"):
        skeleton.make_copy(source, tmp_path / "copy")


def test_make_copy_refuses_a_folder_that_is_not_empty(source: Path, tmp_path: Path) -> None:
    dest = tmp_path / "copy"
    dest.mkdir()
    (dest / "x").write_text("x", encoding="utf-8")
    with pytest.raises(skeleton.SkeletonError, match="not empty"):
        skeleton.make_copy(source, dest)


def test_the_copy_environment_runs_the_copys_own_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIRTUAL_ENV", "/somewhere/else")
    monkeypatch.setenv("DBT_DUCKDB_PATH", "/original/data.duckdb")
    env = skeleton.copy_env(tmp_path)
    assert "VIRTUAL_ENV" not in env
    assert env["PYTHONPATH"].split(os.pathsep)[0] == str(tmp_path / "src")
    assert env["DBT_DUCKDB_PATH"] == str(tmp_path / "target" / "skeleton" / "demo.duckdb")


def test_run_in_copy_uses_the_copy_as_the_working_folder(tmp_path: Path) -> None:
    out = tmp_path / "cwd.txt"
    code = skeleton.run_in_copy(
        tmp_path,
        ["-c", "import os, pathlib; pathlib.Path(os.environ['OUT']).write_text(os.getcwd())"],
        {"OUT": str(out)},
    )
    assert code == 0
    assert Path(out.read_text(encoding="utf-8")).resolve() == tmp_path.resolve()


def _stub_runners(monkeypatch: pytest.MonkeyPatch, fail_at: str | None) -> list[str]:
    ran: list[str] = []

    def make(step: str):
        def runner(ctx: dict) -> int:
            ran.append(step)
            if step == "copy":
                ctx["copy"] = ctx["root"] / "never-used"
            return 1 if step == fail_at else 0

        return runner

    monkeypatch.setattr(skeleton, "RUNNERS", {step: make(step) for step in skeleton.STEPS})
    monkeypatch.setattr(skeleton, "fabric_adapter_installed", lambda: True)
    monkeypatch.setattr(skeleton, "_cleanup", lambda ctx: None)
    monkeypatch.setattr(skeleton, "repo_root", lambda: REPO_ROOT)
    return ran


def test_the_first_failing_step_is_named_and_later_steps_do_not_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = _stub_runners(monkeypatch, fail_at="gate")
    assert skeleton.main([]) == 1
    out = capsys.readouterr().out
    assert "skeleton failed at: gate (exit 1)" in out
    assert ran == ["copy", "remove", "gate"]


def test_every_step_is_printed_and_a_pass_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran = _stub_runners(monkeypatch, fail_at=None)
    assert skeleton.main([]) == 0
    out = capsys.readouterr().out
    for step in skeleton.STEPS:
        assert f"skeleton: {step}" in out
    assert ran == list(skeleton.STEPS)


def test_the_copy_folder_is_outside_the_checkout() -> None:
    folder = skeleton.new_copy_folder(REPO_ROOT)
    try:
        assert not folder.resolve().is_relative_to(REPO_ROOT.resolve())
        assert folder.name.startswith(skeleton.COPY_PREFIX)
    finally:
        folder.rmdir()


def test_a_temporary_folder_inside_the_checkout_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inside = tmp_path / "checkout"
    (inside / "tmp").mkdir(parents=True)
    monkeypatch.setattr("tempfile.tempdir", str(inside / "tmp"))
    with pytest.raises(skeleton.SkeletonError, match="inside the checkout"):
        skeleton.new_copy_folder(inside)
    assert list((inside / "tmp").iterdir()) == []

"""`repo_root()` finds the checkout from any folder, never a nested dbt project. No network."""

from __future__ import annotations

from pathlib import Path

import pytest

from starter_cli.project import repo_root

CHECKOUT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "folder",
    [
        ".",
        "tests",
        "packages/starter",
        "packages/starter/macros",
    ],
)
def test_the_checkout_is_found_from_any_folder_inside_it(
    folder: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(CHECKOUT / folder)
    assert repo_root() == CHECKOUT


def test_the_checkout_is_found_from_outside_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unrelated folder, even one holding its own dbt project, does not capture the run."""
    (tmp_path / "dbt_project.yml").write_text("name: other\n")
    monkeypatch.chdir(tmp_path)
    assert repo_root() == CHECKOUT

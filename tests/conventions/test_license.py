"""The project's code is MIT licensed (D-01).

LICENSE is the standard MIT text with the author's copyright line, and `pyproject.toml` declares
`license = "MIT"`. The license covers code only; the data's terms are in docs/data-terms.md.
"""

from __future__ import annotations

import tomllib

from support.dbt_cli import REPO_ROOT

LICENSE = REPO_ROOT / "LICENSE"
COPYRIGHT_LINE = "Copyright (c) 2026 Karen Hartless"
GRANT = "Permission is hereby granted, free of charge"


def test_license_is_the_mit_text_with_the_copyright_line() -> None:
    text = LICENSE.read_text(encoding="utf-8")
    assert text.startswith("MIT License")
    assert text.count(COPYRIGHT_LINE) == 1
    assert GRANT in text


def test_pyproject_declares_the_mit_license() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["license"] == "MIT"

"""The Fabric profile reads the session mode from FABRIC_HIGH_CONCURRENCY and defaults to on.

High-concurrency Livy sessions are the adapter's default and stay the default for self-hosted runs.
High-concurrency sessions refuse a service principal's token, so the CI workflow turns the
variable off and builds through a standard session. Plan 05-13 found this live; these tests keep
both halves from being edited away (the workflow half is in `test_fabric_workflow.py`).
"""

from __future__ import annotations

import os

import pytest
import yaml
from dbt.config.renderer import ProfileRenderer
from dbt_common.context import set_invocation_context
from support.dbt_cli import REPO_ROOT

PROFILES = REPO_ROOT / "profiles.yml"


def _rendered_high_concurrency(monkeypatch: pytest.MonkeyPatch, value: str | None) -> object:
    raw = yaml.safe_load(PROFILES.read_text())["dbt_starter"]["outputs"]["fabric"]
    monkeypatch.delenv("FABRIC_HIGH_CONCURRENCY", raising=False)
    if value is not None:
        monkeypatch.setenv("FABRIC_HIGH_CONCURRENCY", value)
    set_invocation_context(dict(os.environ))
    rendered = ProfileRenderer({}).render_data({"high_concurrency": raw["high_concurrency"]})
    return rendered["high_concurrency"]


def test_the_profile_keeps_high_concurrency_on_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _rendered_high_concurrency(monkeypatch, None) is True


@pytest.mark.parametrize("value", ["true", "TRUE", "True"])
def test_the_profile_keeps_high_concurrency_on_when_the_variable_says_true(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    assert _rendered_high_concurrency(monkeypatch, value) is True


@pytest.mark.parametrize("value", ["false", "FALSE", "False"])
def test_the_profile_turns_high_concurrency_off_when_the_variable_says_false(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    assert _rendered_high_concurrency(monkeypatch, value) is False

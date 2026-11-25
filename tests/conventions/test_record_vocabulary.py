"""The vocabulary that decides what a Fabric verification record may publish, held as literals.

Each test writes the constant out in full and compares it with the module's value. The literals
are the ones plan 05-15's verify pins. This file is on the triage gates' protected list (plan
05-19): a triage edit cannot widen what counts as green, so a change to the vocabulary is an
author decision made here and in the module together.
"""

from __future__ import annotations

from starter_cli import fabric_record, verify_fabric


def test_node_ok_statuses_are_success_pass_and_warn() -> None:
    assert verify_fabric.NODE_OK_STATUSES == ("success", "pass", "warn")


def test_invocation_keys_are_the_five_pinned_names() -> None:
    assert verify_fabric.INVOCATION_KEYS == (
        "dbt_version",
        "engine_version",
        "session_time_zone",
        "command",
        "full_refresh",
    )


def test_node_keys_are_the_five_pinned_names() -> None:
    assert verify_fabric.NODE_KEYS == (
        "unique_id",
        "resource_type",
        "status",
        "failures",
        "execution_time",
    )


def test_check_outcomes_are_the_five_shared_words() -> None:
    assert fabric_record.CHECK_OUTCOMES == (
        "pass",
        "fail",
        "missing",
        "documented-fallback",
        "excluded",
    )


def test_the_record_module_uses_the_driver_node_vocabulary_not_a_copy() -> None:
    assert fabric_record.NODE_OK_STATUSES is verify_fabric.NODE_OK_STATUSES

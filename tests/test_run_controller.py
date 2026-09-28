
"""Contract tests for the run controller service."""

from __future__ import annotations

from langharness_core.contracts import (
    ALL_PLUGIN_SPECS,
    SPEC_RUN_CONTROLLER,
    AwaitUser,
    Continue,
    RunControllerProvider,
    RunDecision,
    Stop,
)
from langharness_plugin.validation import contract_for


def test_specification_is_pinned() -> None:
    assert contract_for(SPEC_RUN_CONTROLLER) is RunControllerProvider
    assert SPEC_RUN_CONTROLLER in ALL_PLUGIN_SPECS


def test_continue_carries_the_followup_and_round() -> None:
    decision = Continue(message="keep going", round=2)
    assert decision.message == "keep going"
    assert decision.round == 2


def test_await_user_carries_the_reason() -> None:
    assert AwaitUser(reason="needs a choice").reason == "needs a choice"


def test_stop_carries_status_reason_and_wrapup() -> None:
    decision = Stop(status="blocked", reason="round-limit", wrapup="ran out of rounds")
    assert decision.status == "blocked"
    assert decision.reason == "round-limit"
    assert decision.wrapup == "ran out of rounds"


def test_decisions_are_distinct_types_not_dicts() -> None:
    # A dict-shaped decision would let a typo silently mean "stop".
    decision: RunDecision = Continue(message="x", round=1)
    assert isinstance(decision, Continue)
    assert not isinstance(decision, (AwaitUser, Stop))
    assert not isinstance(decision, dict)

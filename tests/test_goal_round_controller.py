
"""Tests for the goal round controller."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

from pathlib import Path

from langharness_core.contracts import (
    AwaitUser,
    Continue,
    RunControllerProvider,
    Stop,
)
from langharness_core.plugins.controller.goal_round import GoalRoundController
from langharness_core.plugins.goals.sqlite import SQLiteGoalPlugin
from langharness_plugin.validation import validate


def build(tmp_path: Path, *, max_rounds: int = 32) -> GoalRoundController:
    instance = GoalRoundController()
    goals = SQLiteGoalPlugin()
    goals._path = str(tmp_path / "goals.sqlite3")
    goals._max_rounds = max_rounds
    instance._goals = goals
    return instance


def test_plugin_conforms_to_its_contract(tmp_path: Path) -> None:
    assert validate(build(tmp_path), RunControllerProvider) == ()


def test_no_goal_stops_immediately(tmp_path: Path) -> None:
    decision = build(tmp_path).decide(rounds_run=0, checkpointer_available=True)
    assert isinstance(decision, Stop)
    assert decision.reason == "no-goal"


def test_an_active_goal_continues(tmp_path: Path) -> None:
    instance = build(tmp_path)
    goal = instance._goals.create_goal("fix it", [])
    decision = instance.decide(rounds_run=0, checkpointer_available=True)
    assert isinstance(decision, Continue)
    assert decision.round == 1
    assert goal.id in decision.message
    # The revision quoted is the one in force when the model reads it, i.e.
    # after starting the round bumped it.
    stored = instance._goals.get_goal(goal.id)
    assert f"revision {stored.revision}" in decision.message
    # Continuing consumes a round.
    assert stored.rounds_started == 1


def test_the_round_limit_blocks_the_goal(tmp_path: Path) -> None:
    instance = build(tmp_path)
    goal = instance._goals.create_goal("fix it", [], max_rounds=2)
    for _ in range(2):
        assert isinstance(
            instance.decide(rounds_run=0, checkpointer_available=True), Continue
        )
    decision = instance.decide(rounds_run=0, checkpointer_available=True)
    assert isinstance(decision, Stop)
    assert decision.reason == "round-limit"
    assert instance._goals.get_goal(goal.id).status == "blocked"


def test_the_store_default_applies_when_the_goal_passes_no_limit(
    tmp_path: Path,
) -> None:
    instance = build(tmp_path, max_rounds=1)
    instance._goals.create_goal("fix it", [])
    assert isinstance(
        instance.decide(rounds_run=0, checkpointer_available=True), Continue
    )
    assert isinstance(
        instance.decide(rounds_run=0, checkpointer_available=True), Stop
    )


def test_awaiting_user_stops_without_consuming_a_round(tmp_path: Path) -> None:
    instance = build(tmp_path)
    goal = instance._goals.create_goal("fix it", [])
    instance._goals.update_goal(goal.id, expected_revision=1, status="awaiting_user")
    decision = instance.decide(rounds_run=0, checkpointer_available=True)
    assert isinstance(decision, AwaitUser)
    assert instance._goals.get_goal(goal.id).status == "awaiting_user"


def test_a_complete_goal_stops(tmp_path: Path) -> None:
    instance = build(tmp_path)
    goal = instance._goals.create_goal("fix it", [])
    instance._goals.update_goal(goal.id, expected_revision=1, status="complete")
    decision = instance.decide(rounds_run=0, checkpointer_available=True)
    assert isinstance(decision, Stop)
    assert "complete" in decision.wrapup.lower()


def test_a_blocked_goal_stops(tmp_path: Path) -> None:
    instance = build(tmp_path)
    goal = instance._goals.create_goal("fix it", [])
    instance._goals.update_goal(goal.id, expected_revision=1, status="blocked")
    assert isinstance(
        instance.decide(rounds_run=0, checkpointer_available=True), Stop
    )


def test_without_a_checkpointer_it_refuses_to_continue(tmp_path: Path) -> None:
    instance = build(tmp_path)
    instance._goals.create_goal("fix it", [])
    decision = instance.decide(rounds_run=0, checkpointer_available=False)
    assert isinstance(decision, Stop)
    assert decision.reason == "no-checkpointer"
    assert "conversation" in decision.wrapup.lower()


def test_the_wrapup_asks_for_facts_not_claims(tmp_path: Path) -> None:
    instance = build(tmp_path)
    instance._goals.create_goal("fix it", [])
    instance._goals.update_goal(
        instance._goals.current_goal().id, expected_revision=1, status="complete"
    )
    decision = instance.decide(rounds_run=0, checkpointer_available=True)
    assert "tool" in decision.wrapup.lower()
    assert "risk" in decision.wrapup.lower()


def test_plugin_info_reports_the_component_identity(tmp_path: Path) -> None:
    assert build(tmp_path).get_plugin_info() == {
        "name": "goal-round-controller",
        "version": "1.0.0",
    }

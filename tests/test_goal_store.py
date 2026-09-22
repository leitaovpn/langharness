
"""Tests for the SQLite goal store."""
# mypy: ignore-errors
# pyright: reportOptionalMemberAccess=false

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from langharness_core.contracts import GoalProvider
from langharness_core.plugins.goals.sqlite import (
    BLOCKER_THRESHOLD,
    GoalConflictError,
    SQLiteGoalPlugin,
)
from langharness_plugin.validation import validate


def store(tmp_path: Path) -> SQLiteGoalPlugin:
    plugin = SQLiteGoalPlugin()
    plugin._path = str(tmp_path / "goals.sqlite3")
    return plugin


def test_plugin_conforms_to_its_contract(tmp_path: Path) -> None:
    assert validate(store(tmp_path), GoalProvider) == ()


def test_a_new_goal_starts_active_at_revision_one(tmp_path: Path) -> None:
    goal = store(tmp_path).create_goal("fix the bug", [{"id": "c1", "text": "t"}])
    assert goal.status == "active"
    assert goal.revision == 1
    assert goal.rounds_started == 0
    assert goal.acceptance_criteria[0].id == "c1"
    assert goal.blocker_history == ()


def test_goals_round_trip_through_sqlite(tmp_path: Path) -> None:
    plugin = store(tmp_path)
    created = plugin.create_goal("objective", [{"id": "c1", "text": "t"}])
    assert plugin.get_goal(created.id) == created
    assert plugin.current_goal() == created


def test_an_unknown_goal_is_none(tmp_path: Path) -> None:
    assert store(tmp_path).get_goal("nope") is None


def test_current_goal_is_the_latest_active_one(tmp_path: Path) -> None:
    plugin = store(tmp_path)
    first = plugin.create_goal("one", [])
    second = plugin.create_goal("two", [])
    assert plugin.current_goal().id == second.id
    plugin.update_goal(first.id, expected_revision=1, status="complete")
    assert plugin.current_goal().id == second.id


def test_completing_a_goal_removes_it_from_current(tmp_path: Path) -> None:
    plugin = store(tmp_path)
    goal = plugin.create_goal("only", [])
    plugin.update_goal(goal.id, expected_revision=1, status="complete")
    assert plugin.current_goal() is None
    assert plugin.get_goal(goal.id).status == "complete"


def test_a_stale_revision_is_refused(tmp_path: Path) -> None:
    plugin = store(tmp_path)
    goal = plugin.create_goal("o", [])
    plugin.update_goal(goal.id, expected_revision=1, status="verifying")
    with pytest.raises(GoalConflictError):
        plugin.update_goal(goal.id, expected_revision=1, status="complete")


def test_each_update_advances_the_revision(tmp_path: Path) -> None:
    plugin = store(tmp_path)
    goal = plugin.create_goal("o", [])
    second = plugin.update_goal(goal.id, expected_revision=1, status="verifying")
    third = plugin.update_goal(goal.id, expected_revision=2, status="active")
    assert (second.revision, third.revision) == (2, 3)


def test_rounds_can_be_incremented(tmp_path: Path) -> None:
    plugin = store(tmp_path)
    goal = plugin.create_goal("o", [])
    updated = plugin.update_goal(goal.id, expected_revision=1, start_round=True)
    assert updated.rounds_started == 1


def test_blocker_history_accumulates(tmp_path: Path) -> None:
    plugin = store(tmp_path)
    goal = plugin.create_goal("o", [])
    first = plugin.update_goal(goal.id, expected_revision=1, blocker="no network")
    assert first.blocker_history == ("no network",)
    second = plugin.update_goal(goal.id, expected_revision=2, blocker="no network")
    assert second.blocker_history == ("no network", "no network")


def test_the_blocker_threshold_is_three() -> None:
    assert BLOCKER_THRESHOLD == 3


def test_state_survives_a_new_plugin_instance(tmp_path: Path) -> None:
    plugin = store(tmp_path)
    goal = plugin.create_goal("durable", [{"id": "c1", "text": "t"}])
    reopened = store(tmp_path)
    assert reopened.get_goal(goal.id) == goal


def test_concurrent_updates_never_lose_a_revision(tmp_path: Path) -> None:
    plugin = store(tmp_path)
    goal = plugin.create_goal("o", [])

    def bump(index: int) -> int:
        # Each worker retries once on conflict, so the revision must end up
        # exactly equal to the number of successful updates.
        for _ in range(50):
            current = plugin.get_goal(goal.id)
            try:
                return plugin.update_goal(
                    goal.id, expected_revision=current.revision, status="active"
                ).revision
            except GoalConflictError:
                continue
        raise AssertionError("could not make progress")

    with ThreadPoolExecutor(max_workers=8) as pool:
        revisions = list(pool.map(bump, range(16)))
    assert len(set(revisions)) == 16
    assert plugin.get_goal(goal.id).revision == 17


def test_criterion_evidence_is_recorded_after_a_completion(tmp_path: Path) -> None:
    plugin = store(tmp_path)
    goal = plugin.create_goal("o", [{"id": "c1", "text": "t"}, {"id": "c2", "text": "u"}])
    updated = plugin.set_criterion_evidence(goal.id, {"c1": "tool:call_7"})
    assert updated.acceptance_criteria[0].evidence == "tool:call_7"
    assert updated.acceptance_criteria[1].evidence is None


def test_recording_evidence_does_not_advance_the_revision(tmp_path: Path) -> None:
    # Evidence is what the completion was granted on, not a revision of the
    # goal; advancing it would invalidate the report that just passed.
    plugin = store(tmp_path)
    goal = plugin.create_goal("o", [{"id": "c1", "text": "t"}])
    updated = plugin.set_criterion_evidence(goal.id, {"c1": "tool:call_7"})
    assert updated.revision == goal.revision


def test_updating_an_unknown_goal_is_a_conflict(tmp_path: Path) -> None:
    with pytest.raises(GoalConflictError):
        store(tmp_path).update_goal("nope", expected_revision=1, status="complete")


def test_recording_evidence_for_an_unknown_goal_is_a_conflict(tmp_path: Path) -> None:
    with pytest.raises(GoalConflictError):
        store(tmp_path).set_criterion_evidence("nope", {"c1": "x"})


def test_invalidate_closes_the_connection_and_is_idempotent(tmp_path: Path) -> None:
    plugin = store(tmp_path)
    plugin.create_goal("o", [])
    assert plugin._connection is not None
    plugin._invalidate(None)
    assert plugin._connection is None
    # A second call must not raise on the already-closed connection.
    plugin._invalidate(None)


def test_the_store_reopens_after_invalidate(tmp_path: Path) -> None:
    plugin = store(tmp_path)
    goal = plugin.create_goal("durable", [])
    plugin._invalidate(None)
    assert plugin.get_goal(goal.id).objective == "durable"


def test_plugin_info_reports_the_component_identity(tmp_path: Path) -> None:
    assert store(tmp_path).get_plugin_info() == {
        "name": "sqlite-goal",
        "version": "1.0.0",
    }

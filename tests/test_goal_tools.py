
"""Tests for the goal tools."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

from pathlib import Path

from langharness_core.contracts import ToolProvider
from langharness_core.plugins.goals.sqlite import SQLiteGoalPlugin
from langharness_core.plugins.goals.tools import GoalToolsPlugin
from langharness_core.plugins.verifier.project import ProjectVerifier
from langharness_core.plugins.workspace.root import WorkspaceRootPlugin
from langharness_core.plugins.workspace.state import WorkspaceStatePlugin
from langharness_plugin.validation import validate


def build(tmp_path: Path, *, failing: bool = False) -> GoalToolsPlugin:
    instance = GoalToolsPlugin()
    goal_store = SQLiteGoalPlugin()
    goal_store._path = str(tmp_path / "goals.sqlite3")
    state = WorkspaceStatePlugin()
    root = WorkspaceRootPlugin()
    root._root = str(tmp_path)
    verifier = ProjectVerifier()
    verifier._workspace = root
    verifier._state = state
    exit_code = 1 if failing else 0
    verifier._commands = [
        ["python3", "-c", f"import sys; sys.exit({exit_code})"]
    ]
    instance._goals = goal_store
    instance._verifier = verifier
    instance._state = state
    return instance


def tools(instance: GoalToolsPlugin) -> dict[str, object]:
    return {item.name: item for item in instance.get_tools()}


def test_plugin_conforms_to_its_tool_contract(tmp_path: Path) -> None:
    assert validate(build(tmp_path), ToolProvider) == ()


def test_goal_dependencies_are_scoped(tmp_path: Path) -> None:
    from langharness_plugin.scoped_dependencies import scoped_fields

    assert scoped_fields(GoalToolsPlugin) == frozenset(
        {"_goals", "_verifier", "_state", "_reviewer"}
    )


def test_get_goal_without_one_reports_none(tmp_path: Path) -> None:
    result = tools(build(tmp_path))["get_goal"].invoke({})
    assert result["goal"] is None


def test_get_goal_returns_the_current_goal(tmp_path: Path) -> None:
    instance = build(tmp_path)
    goal = instance._goals.create_goal("fix it", [{"id": "c1", "text": "tests pass"}])
    result = tools(instance)["get_goal"].invoke({})
    assert result["goal"]["id"] == goal.id
    assert result["goal"]["status"] == "active"
    assert result["goal"]["revision"] == 1
    assert result["goal"]["acceptance_criteria"][0]["id"] == "c1"


def test_finish_goal_without_a_goal_is_rejected(tmp_path: Path) -> None:
    result = tools(build(tmp_path))["finish_goal"].invoke(
        {"status": "complete", "summary": "done"}
    )
    assert result["error"]["code"] == "no_active_goal"


def test_complete_is_rejected_without_evidence(tmp_path: Path) -> None:
    instance = build(tmp_path)
    instance._goals.create_goal("fix it", [{"id": "c1", "text": "tests pass"}])
    result = tools(instance)["finish_goal"].invoke(
        {"status": "complete", "summary": "done", "evidence": {}}
    )
    assert result["error"]["code"] == "completion_rejected"
    assert "criterion_without_evidence" in str(result["error"]["details"])
    # The goal must not move on a failed attempt.
    assert instance._goals.current_goal().status == "active"


def test_complete_is_rejected_when_a_gate_fails(tmp_path: Path) -> None:
    instance = build(tmp_path, failing=True)
    instance._goals.create_goal("fix it", [{"id": "c1", "text": "tests pass"}])
    instance._state.record_mutation("a.py", "apply_patch", after_sha256="sha256:x")
    result = tools(instance)["finish_goal"].invoke(
        {
            "status": "complete",
            "summary": "done",
            "evidence": {"c1": "tool:call_1"},
        }
    )
    assert result["error"]["code"] == "completion_rejected"
    assert "check_failed" in str(result["error"]["details"])
    assert instance._goals.current_goal().status == "active"


def test_complete_is_accepted_when_every_gate_passes(tmp_path: Path) -> None:
    instance = build(tmp_path)
    goal = instance._goals.create_goal("fix it", [{"id": "c1", "text": "tests pass"}])
    instance._state.record_mutation("a.py", "apply_patch", after_sha256="sha256:x")
    result = tools(instance)["finish_goal"].invoke(
        {"status": "complete", "summary": "fixed it", "evidence": {"c1": "tool:call_1"}}
    )
    assert result["status"] == "complete"
    assert result["report_id"].startswith("vr_")
    stored = instance._goals.get_goal(goal.id)
    assert stored.status == "complete"
    assert stored.acceptance_criteria[0].evidence == "tool:call_1"


def test_optional_review_runs_after_gate_and_cannot_grant_completion(tmp_path: Path) -> None:
    instance = build(tmp_path, failing=True)
    instance._goals.create_goal("fix it", [{"id": "c1", "text": "tests pass"}])
    instance._state.record_mutation("a.py", "apply_patch", after_sha256="sha256:x")
    subjects = []

    class Reviewer:
        def is_enabled(self):
            return True

        def review(self, subject):
            subjects.append(subject)
            return {"status": "completed", "artifact_id": "review-1",
                    "completion_authority": "verifier"}

    instance._reviewer = Reviewer()
    rejected = tools(instance)["finish_goal"].invoke(
        {"status": "complete", "summary": "done", "evidence": {"c1": "tool:call_1"}}
    )
    assert rejected["error"]["code"] == "completion_rejected"
    assert subjects == []

    instance._verifier._commands = [["python3", "-c", "pass"]]
    accepted = tools(instance)["finish_goal"].invoke(
        {"status": "complete", "summary": "done", "evidence": {"c1": "tool:call_1"}}
    )
    assert accepted["status"] == "complete"
    assert accepted["review"]["artifact_id"] == "review-1"
    assert subjects[0]["goal"]["objective"] == "fix it"
    assert subjects[0]["verification"]["passed"] is True


def test_review_failure_does_not_reverse_verified_completion(tmp_path: Path) -> None:
    instance = build(tmp_path)
    goal = instance._goals.create_goal("fix it", [{"id": "c1", "text": "tests pass"}])
    instance._state.record_mutation("a.py", "apply_patch", after_sha256="sha256:x")

    class BrokenReviewer:
        def is_enabled(self):
            return True

        def review(self, subject):
            raise RuntimeError("review is down")

    instance._reviewer = BrokenReviewer()
    result = tools(instance)["finish_goal"].invoke(
        {"status": "complete", "summary": "done", "evidence": {"c1": "tool:call_1"}}
    )
    assert result["status"] == "complete"
    assert result["review"]["status"] == "error"
    assert instance._goals.get_goal(goal.id).status == "complete"


def test_editing_after_verification_blocks_completion(tmp_path: Path) -> None:
    instance = build(tmp_path)
    instance._goals.create_goal("fix it", [{"id": "c1", "text": "tests pass"}])
    state_generation = instance._state
    # A report produced now, then another edit before finishing.
    instance._verify_then_edit = True  # marker for the shim below
    original = instance._verifier.verify

    def verify_then_edit(goal_revision: int):
        report = original(goal_revision)
        state_generation.record_mutation("late.py", "apply_patch", after_sha256="sha256:z")
        return report

    instance._verifier.verify = verify_then_edit
    result = tools(instance)["finish_goal"].invoke(
        {"status": "complete", "summary": "done", "evidence": {"c1": "tool:call_1"}}
    )
    assert result["error"]["code"] == "completion_rejected"
    assert "verification_stale" in str(result["error"]["details"])


def test_blocked_needs_the_same_blocker_repeatedly(tmp_path: Path) -> None:
    instance = build(tmp_path)
    instance._goals.create_goal("fix it", [{"id": "c1", "text": "t"}])
    finish = tools(instance)["finish_goal"]
    first = finish.invoke(
        {"status": "blocked", "summary": "stuck", "blocker": "no network"}
    )
    assert first["error"]["code"] == "blocker_not_established"
    assert "1 of 3" in first["error"]["message"]
    finish.invoke({"status": "blocked", "summary": "stuck", "blocker": "no network"})
    third = finish.invoke(
        {"status": "blocked", "summary": "stuck", "blocker": "no network"}
    )
    assert third["status"] == "blocked"
    assert instance._goals.current_goal() is None


def test_a_different_blocker_resets_the_count(tmp_path: Path) -> None:
    instance = build(tmp_path)
    instance._goals.create_goal("fix it", [{"id": "c1", "text": "t"}])
    finish = tools(instance)["finish_goal"]
    finish.invoke({"status": "blocked", "summary": "s", "blocker": "no network"})
    finish.invoke({"status": "blocked", "summary": "s", "blocker": "no network"})
    result = finish.invoke({"status": "blocked", "summary": "s", "blocker": "no disk"})
    assert result["error"]["code"] == "blocker_not_established"
    assert "1 of 3" in result["error"]["message"]


def test_blocked_requires_a_blocker_text(tmp_path: Path) -> None:
    instance = build(tmp_path)
    instance._goals.create_goal("fix it", [{"id": "c1", "text": "t"}])
    result = tools(instance)["finish_goal"].invoke(
        {"status": "blocked", "summary": "stuck"}
    )
    assert result["error"]["code"] == "invalid_arguments"


def test_presentations_skip_finish_goal(tmp_path: Path) -> None:
    presentations = build(tmp_path).get_tool_presentations()
    assert presentations == {"get_goal": "get_goal()"}


"""Tests for the standalone verify tool."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

from pathlib import Path

from langharness_core.contracts import ToolProvider
from langharness_core.plugins.goals.sqlite import SQLiteGoalPlugin
from langharness_core.plugins.goals.verify_tool import VerifyToolPlugin
from langharness_core.plugins.verifier.project import ProjectVerifier
from langharness_core.plugins.workspace.root import WorkspaceRootPlugin
from langharness_core.plugins.workspace.state import WorkspaceStatePlugin
from langharness_plugin.validation import validate


def build(tmp_path: Path, *, failing: bool = False) -> VerifyToolPlugin:
    instance = VerifyToolPlugin()
    goals = SQLiteGoalPlugin()
    goals._path = str(tmp_path / "goals.sqlite3")
    state = WorkspaceStatePlugin()
    root = WorkspaceRootPlugin()
    root._root = str(tmp_path)
    verifier = ProjectVerifier()
    verifier._workspace = root
    verifier._state = state
    code = 1 if failing else 0
    verifier._commands = [["python3", "-c", f"import sys; sys.exit({code})"]]
    instance._goals = goals
    instance._verifier = verifier
    instance._state = state
    return instance


def verify(instance: VerifyToolPlugin) -> dict:
    return {t.name: t for t in instance.get_tools()}["verify"].invoke({})


def test_plugin_conforms_to_its_contract(tmp_path: Path) -> None:
    assert validate(build(tmp_path), ToolProvider) == ()


def test_verify_without_a_goal_is_reported(tmp_path: Path) -> None:
    assert verify(build(tmp_path))["error"]["code"] == "no_active_goal"


def test_verify_returns_a_signed_report_without_changing_the_goal(
    tmp_path: Path,
) -> None:
    instance = build(tmp_path)
    goal = instance._goals.create_goal("fix it", [{"id": "c1", "text": "t"}])
    instance._state.record_mutation("a.py", "apply_patch", after_sha256="sha256:x")
    result = verify(instance)
    assert result["report_id"].startswith("vr_")
    assert result["goal_revision"] == goal.revision
    assert result["mutation_generation"] == 1
    assert result["passed"] is True
    # Querying must not move the goal.
    assert instance._goals.current_goal().status == "active"
    assert instance._goals.current_goal().revision == goal.revision


def test_verify_says_whether_completion_would_be_accepted(tmp_path: Path) -> None:
    instance = build(tmp_path)
    instance._goals.create_goal("fix it", [{"id": "c1", "text": "t"}])
    instance._state.record_mutation("a.py", "apply_patch", after_sha256="sha256:x")
    # The criterion has no evidence yet, so completion would be refused.
    result = verify(instance)
    assert result["would_complete"] is False
    assert any("c1" in item for item in result["blockers"])


def test_verify_lists_a_failing_check(tmp_path: Path) -> None:
    instance = build(tmp_path, failing=True)
    instance._goals.create_goal("fix it", [{"id": "c1", "text": "t"}])
    instance._state.record_mutation("a.py", "apply_patch", after_sha256="sha256:x")
    result = verify(instance)
    assert result["passed"] is False
    assert result["would_complete"] is False
    assert result["checks"][0]["exit_code"] == 1


def test_verify_reports_the_checks_it_ran(tmp_path: Path) -> None:
    instance = build(tmp_path)
    instance._goals.create_goal("fix it", [])
    result = verify(instance)
    assert result["checks"][0]["name"].startswith("python3")
    assert result["checks"][0]["exit_code"] == 0


def test_verify_always_reports_a_freshly_run_report(tmp_path: Path) -> None:
    # verify re-runs the gates on every call, so the report it hands back is
    # never the stale one. Staleness is what finish_goal catches instead, when
    # an edit lands between a verify and the submission.
    instance = build(tmp_path)
    instance._goals.create_goal(
        "fix it", [{"id": "c1", "text": "t", "evidence": "tool:x"}]
    )
    instance._state.record_mutation("a.py", "apply_patch", after_sha256="sha256:x")
    first = verify(instance)
    assert first["would_complete"] is True
    assert first["mutation_generation"] == 1

    instance._state.record_mutation("a.py", "apply_patch", after_sha256="sha256:y")
    second = verify(instance)
    assert second["mutation_generation"] == 2
    assert second["report_id"] != first["report_id"]
    assert second["would_complete"] is True


def test_plugin_info_reports_the_component_identity(tmp_path: Path) -> None:
    assert build(tmp_path).get_plugin_info() == {
        "name": "verify-tool",
        "version": "1.0.0",
    }

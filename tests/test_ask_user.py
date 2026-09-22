
"""Tests for the ask_user tool."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

from pathlib import Path

from langharness_core.contracts import ToolProvider
from langharness_core.plugins.goals.ask_user import AskUserToolPlugin
from langharness_core.plugins.goals.sqlite import SQLiteGoalPlugin
from langharness_plugin.validation import validate


def build(tmp_path: Path) -> AskUserToolPlugin:
    instance = AskUserToolPlugin()
    goals = SQLiteGoalPlugin()
    goals._path = str(tmp_path / "goals.sqlite3")
    instance._goals = goals
    return instance


def ask(instance: AskUserToolPlugin, question: str) -> dict:
    return {t.name: t for t in instance.get_tools()}["ask_user"].invoke(
        {"question": question}
    )


def test_plugin_conforms_to_its_contract(tmp_path: Path) -> None:
    assert validate(build(tmp_path), ToolProvider) == ()


def test_asking_without_a_goal_is_reported(tmp_path: Path) -> None:
    assert ask(build(tmp_path), "which one?")["error"]["code"] == "no_active_goal"


def test_asking_moves_the_goal_to_awaiting_user(tmp_path: Path) -> None:
    instance = build(tmp_path)
    goal = instance._goals.create_goal("fix it", [])
    result = ask(instance, "Should the API change or the caller?")
    assert result["status"] == "awaiting_user"
    assert result["question"] == "Should the API change or the caller?"
    stored = instance._goals.get_goal(goal.id)
    assert stored.status == "awaiting_user"
    assert stored.revision == goal.revision + 1


def test_the_result_says_the_run_will_pause(tmp_path: Path) -> None:
    instance = build(tmp_path)
    instance._goals.create_goal("fix it", [])
    result = ask(instance, "which?")
    assert "pause" in result["note"].lower()


def test_a_blank_question_is_refused(tmp_path: Path) -> None:
    instance = build(tmp_path)
    instance._goals.create_goal("fix it", [])
    result = ask(instance, "   ")
    assert result["error"]["code"] == "invalid_arguments"
    assert instance._goals.current_goal().status == "active"


def test_asking_twice_is_still_awaiting_user(tmp_path: Path) -> None:
    instance = build(tmp_path)
    instance._goals.create_goal("fix it", [])
    ask(instance, "first?")
    second = ask(instance, "second?")
    assert second["status"] == "awaiting_user"
    assert instance._goals.current_goal().status == "awaiting_user"


def test_plugin_info_reports_the_component_identity(tmp_path: Path) -> None:
    assert build(tmp_path).get_plugin_info() == {
        "name": "ask-user",
        "version": "1.0.0",
    }

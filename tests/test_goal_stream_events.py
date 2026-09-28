"""Tests for goal events reaching the CLI renderer."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

from langharness_cli.plugins.rich_renderer import RichInteractiveRenderer


def renderer() -> RichInteractiveRenderer:
    return RichInteractiveRenderer()


def test_goal_events_do_not_crash_the_renderer() -> None:
    instance = renderer()
    for event in (
        {"type": "goal_round_started", "round": 2, "message": "keep going"},
        {"type": "goal_status", "status": "complete"},
        {"type": "goal_wrapup", "text": "summarise"},
    ):
        instance.render_event(event)


def test_a_goal_round_shows_up_in_the_transcript() -> None:
    instance = renderer()
    instance.render_event({"type": "goal_round_started", "round": 2, "message": "go"})
    assert instance.status_line() == "goal · round 2"


def test_a_terminal_status_shows_up() -> None:
    instance = renderer()
    instance.render_event({"type": "goal_status", "status": "complete"})
    assert instance.status_line() == "goal · complete"


def test_the_wrapup_does_not_leak_into_the_answer() -> None:
    instance = renderer()
    instance.render_event({"type": "assistant", "content": "actual answer"})
    instance.render_event({"type": "goal_wrapup", "text": "summarise"})
    assert instance.answer_text() == "actual answer"


def test_starting_a_response_clears_an_old_goal_status() -> None:
    instance = renderer()
    instance.render_event({"type": "goal_status", "status": "complete"})
    instance.start_response()
    assert instance.status_line() is None


def test_the_renderer_still_ignores_unknown_events() -> None:
    instance = renderer()
    instance.render_event({"type": "something_new", "x": 1})

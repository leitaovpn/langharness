
"""Tests for job event forwarding through the agent loop."""
# mypy: ignore-errors
# pyright: reportOptionalMemberAccess=false, reportAttributeAccessIssue=false

from __future__ import annotations

from langharness_core.plugins.loop.agent_loop import PluginAgentLoop


class FakeJobs:
    def __init__(self, batches: list[list[dict]]) -> None:
        self._batches = batches
        self.calls = 0

    def drain_events(self) -> list[dict]:
        self.calls += 1
        if self._batches:
            return self._batches.pop(0)
        return []


def test_no_job_manager_means_no_events() -> None:
    loop = PluginAgentLoop()
    assert loop._drain_job_events() == []


def test_queued_events_are_returned_once() -> None:
    loop = PluginAgentLoop()
    loop._job_manager = FakeJobs([[{"type": "job_started", "job_id": "j1"}]])
    assert loop._drain_job_events() == [{"type": "job_started", "job_id": "j1"}]
    assert loop._drain_job_events() == []


def test_a_failing_job_manager_does_not_break_the_stream() -> None:
    class Boom:
        def drain_events(self) -> list[dict]:
            raise RuntimeError("queue exploded")

    loop = PluginAgentLoop()
    loop._job_manager = Boom()
    assert loop._drain_job_events() == []


def test_the_loop_declares_the_job_field_as_scoped() -> None:
    from langharness_plugin.scoped_dependencies import scoped_fields

    assert "_job_manager" in scoped_fields(PluginAgentLoop)

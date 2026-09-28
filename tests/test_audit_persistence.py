"""Acceptance tests for append-only audit persistence."""

from __future__ import annotations

import asyncio
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from langharness_core.plugins.loop.agent_loop import PluginAgentLoop
from langharness_core.plugins.presentation.runtime import NodePTCRuntime
from langharness_core.plugins.sessions.audit import SQLiteAuditPlugin
from langharness_core.plugins.tools.tools import ToolPlugin


def test_audit_events_round_trip_and_preserve_sequence(tmp_path: Path) -> None:
    plugin = SQLiteAuditPlugin()
    plugin._path = str(tmp_path / "audit.sqlite3")
    events = [
        {"type": "ptc_audit", "root_call_id": "root-1", "sequence": 1, "status": "started"},
        {"type": "ptc_audit", "root_call_id": "root-1", "sequence": 2, "status": "completed", "result": 3},
    ]

    async def scenario() -> list[dict[str, Any]]:
        await plugin.append_events(events)
        return await plugin.list_events("root-1")

    assert asyncio.run(scenario()) == events
    plugin._invalidate(cast(Any, None))


def test_audit_round_trip_across_independent_processes(tmp_path: Path) -> None:
    path = tmp_path / "cross-process-audit.sqlite3"
    script = """
import asyncio, json, sys
from langharness_core.plugins.sessions.audit import SQLiteAuditPlugin

async def main():
    plugin = SQLiteAuditPlugin()
    plugin._path = sys.argv[1]
    if sys.argv[2] == 'write':
        await plugin.append_events([{'root_call_id': 'cross', 'sequence': 1, 'status': 'ok'}])
    else:
        print(json.dumps(await plugin.list_events('cross')))
    plugin._invalidate(None)

asyncio.run(main())
"""
    subprocess.run([sys.executable, "-c", script, str(path), "write"], check=True)
    read = subprocess.run(
        [sys.executable, "-c", script, str(path), "read"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert read.stdout.strip() == '[{"root_call_id": "cross", "sequence": 1, "status": "ok"}]'


def test_audit_events_can_be_queried_by_agent_and_session(tmp_path: Path) -> None:
    plugin = SQLiteAuditPlugin()
    plugin._path = str(tmp_path / "audit.sqlite3")
    events = [
        {"root_call_id": "root-1", "sequence": 1, "agent_id": "alpha", "session_id": "one"},
        {"root_call_id": "root-2", "sequence": 1, "agent_id": "alpha", "session_id": "two"},
        {"root_call_id": "root-3", "sequence": 1, "agent_id": "beta", "session_id": "one"},
    ]

    async def scenario() -> list[dict[str, Any]]:
        await plugin.append_events(events)
        return await plugin.list_session_events("alpha", "one")

    assert asyncio.run(scenario()) == [events[0]]
    plugin._invalidate(cast(Any, None))


def test_stream_audit_is_scoped_to_agent_and_session(tmp_path: Path) -> None:
    plugin = SQLiteAuditPlugin()
    plugin._path = str(tmp_path / "audit.sqlite3")
    loop = PluginAgentLoop()
    loop._audit_provider = plugin
    loop._agent_id = "alpha"
    event = {"type": "ptc_audit", "root_call_id": "root-1", "sequence": 1}

    async def empty_stream() -> Any:
        if False:
            yield ("messages", None)

    async def scenario() -> list[dict[str, Any]]:
        emitted = [
            item
            async for item in loop._forward_stream(
                empty_stream(), {}, {}, [event], session_id="session-1"
            )
        ]
        stored = await plugin.list_session_events("alpha", "session-1")
        assert stored == emitted
        return emitted

    assert asyncio.run(scenario())[0]["session_id"] == "session-1"
    plugin._invalidate(cast(Any, None))


def test_existing_audit_database_is_migrated(tmp_path: Path) -> None:
    path = tmp_path / "audit.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE audit_events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "root_call_id TEXT NOT NULL, sequence INTEGER NOT NULL, "
            "event_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO audit_events(root_call_id, sequence, event_json) "
            "VALUES ('old-root', 1, '{\"root_call_id\": \"old-root\"}')"
        )
    plugin = SQLiteAuditPlugin()
    plugin._path = str(path)

    async def scenario() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        old = await plugin.list_events("old-root")
        new_event = {"root_call_id": "new-root", "agent_id": "a", "session_id": "s"}
        await plugin.append_events([new_event])
        return old, await plugin.list_session_events("a", "s")

    assert asyncio.run(scenario()) == (
        [{"root_call_id": "old-root"}],
        [{"root_call_id": "new-root", "agent_id": "a", "session_id": "s"}],
    )
    plugin._invalidate(cast(Any, None))


def test_exec_call_and_result_are_persisted_without_command_output(tmp_path: Path) -> None:
    plugin = SQLiteAuditPlugin()
    plugin._path = str(tmp_path / "audit.sqlite3")
    loop = PluginAgentLoop()
    loop._audit_provider = plugin
    loop._agent_id = "alpha"
    call = AIMessage(
        content="",
        tool_calls=[{"name": "exec_command", "args": {"command": ["echo", "secret"]}, "id": "call-1"}],
    )
    result = ToolMessage(content="secret", name="exec_command", tool_call_id="call-1")

    async def stream() -> Any:
        yield "updates", {"model": {"messages": [call]}}
        yield "updates", {"tools": {"messages": [result]}}

    async def scenario() -> list[dict[str, Any]]:
        async for _ in loop._forward_stream(stream(), {}, {}, session_id="session-1"):
            pass
        return await plugin.list_events("call-1")

    events = asyncio.run(scenario())
    assert [event["status"] for event in events] == ["started", "completed"]
    assert all(event["agent_id"] == "alpha" for event in events)
    assert all(event["session_id"] == "session-1" for event in events)
    assert all("secret" not in str(event) for event in events)
    plugin._invalidate(cast(Any, None))


def test_job_lifecycle_is_persisted_without_output(tmp_path: Path) -> None:
    plugin = SQLiteAuditPlugin()
    plugin._path = str(tmp_path / "audit.sqlite3")
    loop = PluginAgentLoop()
    loop._audit_provider = plugin
    loop._agent_id = "alpha"

    class Jobs:
        def drain_events(self) -> list[dict[str, Any]]:
            return [
                {"type": "job_started", "job_id": "job-1", "command": ["echo", "secret"]},
                {"type": "job_output", "job_id": "job-1", "text": "secret"},
                {"type": "job_finished", "job_id": "job-1", "state": "exited", "exit_code": 0},
            ]

    loop._job_manager = Jobs()

    async def stream() -> Any:
        yield "updates", {}

    async def scenario() -> list[dict[str, Any]]:
        async for _ in loop._forward_stream(stream(), {}, {}, session_id="session-1"):
            pass
        return await plugin.list_events("job-1")

    events = asyncio.run(scenario())
    assert [event["status"] for event in events] == ["started", "exited"]
    assert all("secret" not in str(event) for event in events)
    plugin._invalidate(cast(Any, None))


def test_approval_request_is_persisted_without_action_arguments(tmp_path: Path) -> None:
    plugin = SQLiteAuditPlugin()
    plugin._path = str(tmp_path / "audit.sqlite3")
    loop = PluginAgentLoop()
    loop._audit_provider = plugin
    loop._agent_id = "alpha"
    request = {"action_requests": [{"name": "exec_command", "args": {"secret": "hidden"}}]}

    async def stream() -> Any:
        yield "updates", {"__interrupt__": (SimpleNamespace(value=request),)}

    async def scenario() -> list[dict[str, Any]]:
        async for _ in loop._forward_stream(stream(), {}, {}, session_id="session-1"):
            pass
        return await plugin.list_session_events("alpha", "session-1")

    events = asyncio.run(scenario())
    assert len(events) == 1
    assert events[0]["type"] == "approval_audit"
    assert events[0]["status"] == "required"
    assert "hidden" not in str(events[0])
    plugin._invalidate(cast(Any, None))


def test_approval_decision_is_linked_after_audit_store_reopen(tmp_path: Path) -> None:
    path = str(tmp_path / "audit.sqlite3")
    first = SQLiteAuditPlugin()
    first._path = path
    loop = PluginAgentLoop()
    loop._audit_provider = first
    loop._agent_id = "alpha"
    request = {"action_requests": [{"name": "exec_command", "args": {"secret": "hidden"}}]}

    async def interrupted() -> Any:
        yield "updates", {"__interrupt__": (SimpleNamespace(value=request),)}

    async def first_turn() -> None:
        async for _ in loop._forward_stream(interrupted(), {}, {}, session_id="session-1"):
            pass

    asyncio.run(first_turn())
    first._invalidate(cast(Any, None))

    second = SQLiteAuditPlugin()
    second._path = path
    resumed_loop = PluginAgentLoop()
    resumed_loop._audit_provider = second
    resumed_loop._agent_id = "alpha"
    graph_calls: list[Any] = []

    class EmptyGraph:
        async def astream(self, input_data: Any, config: Any = None, stream_mode: Any = None) -> Any:
            graph_calls.append(input_data)
            if False:
                yield "updates", {}

    resumed_loop._graph = EmptyGraph()

    async def resumed_turn() -> list[dict[str, Any]]:
        async for _ in resumed_loop.astream(
            "", thread_id="session-1",
            resume={"decisions": [{"type": "approve", "secret": "hidden"}]},
        ):
            pass
        with pytest.raises(RuntimeError, match="No pending approval"):
            async for _ in resumed_loop.astream(
                "", thread_id="session-1",
                resume={"decisions": [{"type": "approve"}]},
            ):
                pass
        assert len(graph_calls) == 1
        assert await second.find_pending_approval("alpha", "session-1") is None
        return await second.list_session_events("alpha", "session-1")

    events = asyncio.run(resumed_turn())
    second._invalidate(cast(Any, None))
    assert [event["status"] for event in events] == [
        "required", "decision_submitted", "decision_applied"
    ]
    assert len({event["root_call_id"] for event in events}) == 1
    assert events[1]["decisions"] == ["approve"]
    assert all("hidden" not in str(event) for event in events)


def test_pending_approval_lookup_is_not_limited_by_session_list_page(tmp_path: Path) -> None:
    plugin = SQLiteAuditPlugin()
    plugin._path = str(tmp_path / "audit.sqlite3")

    async def scenario() -> dict[str, Any] | None:
        await plugin.append_events([{
            "type": "approval_audit", "root_call_id": "approval-1", "sequence": 1,
            "status": "required", "agent_id": "alpha", "session_id": "session-1",
        }])
        await plugin.append_events([
            {"type": "tool_audit", "root_call_id": f"tool-{index}", "sequence": 1,
             "agent_id": "alpha", "session_id": "session-1"}
            for index in range(150)
        ])
        assert await plugin.find_pending_approval("beta", "session-1") is None
        return await plugin.find_pending_approval("alpha", "session-1")

    pending = asyncio.run(scenario())
    plugin._invalidate(cast(Any, None))
    assert pending is not None
    assert pending["root_call_id"] == "approval-1"


def test_only_one_concurrent_resume_can_claim_an_approval(tmp_path: Path) -> None:
    path = str(tmp_path / "audit.sqlite3")
    first = SQLiteAuditPlugin()
    first._path = path
    second = SQLiteAuditPlugin()
    second._path = path

    async def scenario() -> tuple[list[str | None], list[dict[str, Any]]]:
        await first.append_events([{
            "type": "approval_audit", "root_call_id": "approval-1", "sequence": 1,
            "status": "required", "agent_id": "alpha", "session_id": "session-1",
        }])
        claims = await asyncio.gather(
            first.claim_pending_approval("alpha", "session-1", ["approve"]),
            second.claim_pending_approval("alpha", "session-1", ["approve"]),
        )
        return list(claims), await first.list_events("approval-1")

    claims, events = asyncio.run(scenario())
    first._invalidate(cast(Any, None))
    second._invalidate(cast(Any, None))
    assert sorted(str(claim) for claim in claims) == ["None", "approval-1"]
    assert [event["status"] for event in events] == ["required", "decision_submitted"]


def test_audit_survives_store_reopen_and_sync_agent_path(tmp_path: Path) -> None:
    path = str(tmp_path / "audit.sqlite3")
    first = SQLiteAuditPlugin()
    first._path = path
    loop = PluginAgentLoop()
    loop._ptc_runtime = NodePTCRuntime()
    loop._tool_providers = [ToolPlugin()]
    loop._audit_provider = first

    async def scenario() -> str:
        result = await loop._run_code("return await tools.add({a: 2, b: 3});")
        assert result["result"] == 5
        assert "audit" not in result
        assert first._connection is not None
        async with first._connection.execute(
            "SELECT root_call_id FROM audit_events LIMIT 1"
        ) as cursor:
            row = await cursor.fetchone()
        assert row is not None
        return str(row[0])

    root_id = asyncio.run(scenario())
    first._invalidate(cast(Any, None))
    second = SQLiteAuditPlugin()
    second._path = path

    async def reopen() -> list[dict[str, Any]]:
        return await second.list_events(root_id)

    events = asyncio.run(reopen())
    second._invalidate(cast(Any, None))
    assert [event["status"] for event in events] == ["started", "completed"]
    assert events[1]["result"] == 5


def test_sync_invoke_scopes_ptc_audit_to_agent_and_session(tmp_path: Path) -> None:
    plugin = SQLiteAuditPlugin()
    plugin._path = str(tmp_path / "audit.sqlite3")
    loop = PluginAgentLoop()
    loop._agent_id = "alpha"
    loop._ptc_runtime = NodePTCRuntime()
    loop._tool_providers = [ToolPlugin()]
    loop._audit_provider = plugin

    class DirectGraph:
        def invoke(self, input_data: Any, config: Any = None) -> Any:
            return asyncio.run(loop._run_code("return await tools.add({a: 2, b: 3});"))

    loop._graph = DirectGraph()
    assert loop.invoke("add", thread_id="session-1")["result"] == 5

    async def stored() -> list[dict[str, Any]]:
        return await plugin.list_session_events("alpha", "session-1")

    events = asyncio.run(stored())
    plugin._invalidate(cast(Any, None))
    assert [event["status"] for event in events] == ["started", "completed"]
    assert all(event["agent_id"] == "alpha" for event in events)
    assert all(event["session_id"] == "session-1" for event in events)

"""Process, protocol and cancellation tests for the Node PTC runtime."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from langharness_core.plugins.loop.agent_loop import PluginAgentLoop
from langharness_core.plugins.presentation.runtime import (
    MAX_OUTPUT_BYTES,
    NodePTCRuntime,
)
from langharness_core.plugins.tools.tools import add


class _ToolProvider:
    def get_tools(self) -> list[Any]:
        return [add]


class _Mode:
    def __init__(self, mode: str) -> None:
        self.mode = mode

    def get_mode(self) -> str:
        return self.mode


@pytest.mark.asyncio
async def test_runtime_returns_only_program_output_and_process_isolation() -> None:
    runtime = NodePTCRuntime()

    result = await runtime.execute(
        code="const value: number = await tools.add({a: 2, b: 3}); return value;",
        sdk="type Args = { a: number; b: number };",
        dispatch=lambda name, args: args["a"] + args["b"] if name == "add" else None,
    )

    assert result["result"] == 5
    assert result["isolation"] == "process-only"
    assert result["logs"] == []
    assert result["audit"][0]["status"] == "completed"
    assert result["audit"][0]["name"] == "add"
    assert result["audit"][0]["call_id"].startswith("ptc-")


@pytest.mark.asyncio
async def test_child_can_catch_a_failed_host_call() -> None:
    runtime = NodePTCRuntime()

    result = await runtime.execute(
        code=("try { await tools.fail({}); } catch (error) { return error.message; }"),
        sdk="",
        dispatch=lambda _name, _args: (_ for _ in ()).throw(ValueError("denied")),
    )

    assert result["result"] == "denied"


@pytest.mark.asyncio
async def test_program_can_run_independent_host_calls_concurrently() -> None:
    runtime = NodePTCRuntime()

    async def dispatch(_name: str, args: Any) -> Any:
        await asyncio.sleep(0.12)
        return args["value"]

    started = asyncio.get_running_loop().time()
    result = await runtime.execute(
        code=(
            "const values = await Promise.all(["
            "tools.wait({value: 1}), tools.wait({value: 2})]); return values;"
        ),
        sdk="",
        dispatch=dispatch,
    )

    assert result["result"] == [1, 2]
    assert asyncio.get_running_loop().time() - started < 0.22


@pytest.mark.asyncio
async def test_timeout_kills_the_child_process_group() -> None:
    runtime = NodePTCRuntime()
    result = await runtime.execute(
        code="await new Promise(() => {});",
        sdk="",
        dispatch=lambda _name, _args: None,
        timeout_ms=50,
    )
    assert result["error"]["name"] == "TimeoutError"
    assert result["duration_ms"] < 2000


@pytest.mark.asyncio
async def test_cancellation_kills_the_child() -> None:
    runtime = NodePTCRuntime()
    task = asyncio.create_task(
        runtime.execute(
            code="await new Promise(() => {});",
            sdk="",
            dispatch=lambda _name, _args: None,
        )
    )
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_output_cap_stops_excessive_program_logs() -> None:
    runtime = NodePTCRuntime()
    with pytest.raises(OverflowError, match="output limit"):
        await runtime.execute(
            code=f'console.log("x".repeat({MAX_OUTPUT_BYTES * 2})); return 1;',
            sdk="",
            dispatch=lambda _name, _args: None,
        )


@pytest.mark.asyncio
async def test_invalid_deadline_is_rejected_before_starting_node() -> None:
    with pytest.raises(ValueError, match="positive"):
        await NodePTCRuntime().execute(
            code="return 1;", sdk="", dispatch=lambda _name, _args: None, timeout_ms=0
        )


@pytest.mark.asyncio
async def test_loop_ptc_mode_exposes_run_code_and_dispatches_scoped_tools() -> None:
    loop = PluginAgentLoop()
    loop._tool_providers = [_ToolProvider()]
    loop._presentation_provider = _Mode("ptc")
    loop._ptc_runtime = NodePTCRuntime()

    tools = loop._collect_tools()

    assert [tool.name for tool in tools] == ["run_code"]
    result = await tools[0].ainvoke(
        {"code": "return await tools.add({a: 4, b: 6});", "description": "sum"}
    )
    assert result["result"] == 10


def test_loop_both_mode_keeps_native_tools_and_adds_run_code() -> None:
    loop = PluginAgentLoop()
    loop._tool_providers = [_ToolProvider()]
    loop._presentation_provider = _Mode("both")
    loop._ptc_runtime = NodePTCRuntime()

    assert [tool.name for tool in loop._collect_tools()] == ["add", "run_code"]

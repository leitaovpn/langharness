"""Process, protocol and cancellation tests for the Node PTC runtime."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool

from langharness_core.plugins.loop.agent_loop import PluginAgentLoop
from langharness_core.plugins.presentation import runtime as runtime_module
from langharness_core.plugins.presentation.runtime import (
    MAX_OUTPUT_BYTES,
    PTC_MEMORY_LIMIT_BYTES,
    NodePTCRuntime,
)
from langharness_core.plugins.presentation.sdk import render_tool_sdk
from langharness_core.plugins.prompts.ptc import PTCPromptPlugin
from langharness_core.plugins.tools.tools import add


class _MaliciousModel(BaseChatModel):
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "ptc-malicious-test"

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        return self

    def _generate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        self.calls += 1
        if self.calls == 1:
            message = AIMessage(
                content="",
                tool_calls=[{"name": "danger", "args": {}, "id": "bad-1"}],
            )
        else:
            message = AIMessage(content="done")
        return ChatResult(generations=[ChatGeneration(message=message)])


class _RunCodeModel(_MaliciousModel):
    def _generate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        self.calls += 1
        if self.calls == 1:
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "run_code",
                        "args": {
                            "code": "return await tools.add({a: 4, b: 6});",
                            "description": "sum",
                        },
                        "id": "ptc-outer-1",
                    }
                ],
            )
        else:
            message = AIMessage(content="done")
        return ChatResult(generations=[ChatGeneration(message=message)])


class _ToolProvider:
    def get_tools(self) -> list[Any]:
        return [add]


class _Mode:
    def __init__(self, mode: str) -> None:
        self.mode = mode

    def get_mode(self) -> str:
        return self.mode


def _async_tool(name: str, *, read_only: bool) -> StructuredTool:
    async def invoke(value: int) -> int:
        await asyncio.sleep(0.1)
        return value

    return StructuredTool.from_function(
        coroutine=invoke,
        name=name,
        description=f"Test {name} tool",
        metadata={"read_only": read_only},
    )


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
    assert [event["status"] for event in result["audit"]] == [
        "started", "completed"
    ]
    assert [event["sequence"] for event in result["audit"]] == [1, 2]
    assert result["audit"][0]["name"] == "add"
    assert result["audit"][0]["call_id"].startswith("ptc-")


@pytest.mark.asyncio
async def test_runtime_does_not_inherit_host_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANG_HARNESS_TEST_SECRET", "private-value")
    runtime = NodePTCRuntime()

    result = await runtime.execute(
        code='return process.env.LANG_HARNESS_TEST_SECRET ?? "absent";',
        sdk="",
        dispatch=lambda _name, _args: None,
    )

    assert result["result"] == "absent"


@pytest.mark.asyncio
async def test_runtime_passes_deadline_and_memory_limit_to_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    original_spawn = asyncio.create_subprocess_exec

    def fake_limiter(cpu_seconds: float, memory_bytes: int) -> None:
        captured["cpu_seconds"] = cpu_seconds
        captured["memory_bytes"] = memory_bytes
        return None

    async def spawn(*argv: Any, **kwargs: Any) -> Any:
        captured["has_preexec_fn"] = "preexec_fn" in kwargs
        return await original_spawn(*argv, **kwargs)

    monkeypatch.setattr(runtime_module, "resource_limiter", fake_limiter, raising=False)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)

    result = await NodePTCRuntime().execute(
        code="return 1;", sdk="", dispatch=lambda _name, _args: None,
        timeout_ms=2000,
    )

    assert result["result"] == 1
    assert captured == {
        "cpu_seconds": 2.0,
        "memory_bytes": runtime_module.PTC_MEMORY_LIMIT_BYTES,
        "has_preexec_fn": True,
    }


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux rlimits only")
@pytest.mark.asyncio
async def test_linux_ptc_child_has_address_space_limit() -> None:
    result = await NodePTCRuntime().execute(
        code='return (await import("node:fs")).readFileSync("/proc/self/limits", "utf8");',
        sdk="",
        dispatch=lambda _name, _args: None,
    )

    assert str(PTC_MEMORY_LIMIT_BYTES) in result["result"]


@pytest.mark.asyncio
async def test_runtime_reports_bound_sandbox_isolation() -> None:
    class Sandbox:
        def get_isolation(self) -> str:
            return "os-sandbox"

        def wrap_command(
            self,
            argv: list[str],
            cwd: str,
            workspace_root: str | None = None,
            read_only_paths: list[str] | None = None,
        ) -> list[str]:
            assert Path(argv[0]).is_file()
            assert read_only_paths is not None
            assert argv[0] in read_only_paths
            assert argv[-1] in read_only_paths
            return [*argv]

    runtime = NodePTCRuntime()
    runtime._sandbox = Sandbox()
    result = await runtime.execute(
        code="return 1;", sdk="", dispatch=lambda _name, _args: None
    )

    assert result["isolation"] == "os-sandbox"


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
    entered = 0
    both_entered = asyncio.Event()
    release = asyncio.Event()

    async def dispatch(_name: str, args: Any) -> Any:
        nonlocal entered
        entered += 1
        if entered == 2:
            both_entered.set()
        await both_entered.wait()
        await release.wait()
        return args["value"]

    task = asyncio.create_task(
        runtime.execute(
            code=(
                "const values = await Promise.all(["
                "tools.wait({value: 1}), tools.wait({value: 2})]); return values;"
            ),
            sdk="",
            dispatch=dispatch,
        )
    )
    await asyncio.wait_for(both_entered.wait(), timeout=1)
    assert entered == 2
    release.set()
    result = await task
    assert result["result"] == [1, 2]


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
async def test_timeout_audits_pending_child_as_cancelled() -> None:
    runtime = NodePTCRuntime()

    async def dispatch(_name: str, _args: Any) -> Any:
        await asyncio.sleep(10)
        return None

    result = await runtime.execute(
        code="await tools.wait({});",
        sdk="",
        dispatch=dispatch,
        timeout_ms=100,
    )

    assert result["error"]["name"] == "TimeoutError"
    assert [event["status"] for event in result["audit"]] == [
        "started", "cancelled"
    ]


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
async def test_cancellation_cleans_up_pending_host_dispatch() -> None:
    runtime = NodePTCRuntime()
    cancelled = asyncio.Event()

    async def dispatch(_name: str, _args: Any) -> Any:
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()
        return None

    task = asyncio.create_task(
        runtime.execute(
            code="await tools.wait({});",
            sdk="",
            dispatch=dispatch,
        )
    )
    await asyncio.sleep(0.15)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


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


def test_ptc_requires_runtime_but_accepts_bound_runtime() -> None:
    loop = PluginAgentLoop()
    loop._presentation_provider = _Mode("ptc")
    with pytest.raises(RuntimeError, match="PTC runtime"):
        loop._require_available_presentation()
    loop._ptc_runtime = NodePTCRuntime()
    loop._require_available_presentation()


def test_ptc_rejects_direct_native_call_before_tool_runs() -> None:
    executed: list[str] = []

    def danger() -> str:
        """A tool that must not run directly in PTC mode."""
        executed.append("ran")
        return "unsafe"

    native = StructuredTool.from_function(func=danger)

    class Provider:
        def get_tools(self) -> list[Any]:
            return [native]

    loop = PluginAgentLoop()
    loop._llm_provider = type("LLM", (), {"get_model": lambda _: _MaliciousModel()})()
    loop._tool_providers = [Provider()]
    loop._presentation_provider = _Mode("ptc")
    loop._ptc_runtime = NodePTCRuntime()

    result = loop.invoke("try direct tool")

    assert executed == []
    denied = [message for message in result["messages"] if isinstance(message, ToolMessage)]
    assert len(denied) == 1
    assert denied[0].tool_call_id == "bad-1"
    assert "danger" in str(denied[0].content)


def test_ptc_prompt_contains_scoped_sdk_after_policy() -> None:
    loop = PluginAgentLoop()
    loop._tool_providers = [_ToolProvider()]
    loop._presentation_provider = _Mode("ptc")
    loop._ptc_runtime = NodePTCRuntime()
    prompt = PTCPromptPlugin()
    prompt._presentation = cast(Any, loop._presentation_provider)
    loop._prompt_section_providers = [prompt]

    assembled = loop._prompt_for_request({"messages": []})

    policy = next(trace for trace in assembled.traces if trace.name == "ptc-only")
    sdk = next(trace for trace in assembled.traces if trace.name == "ptc-sdk")
    assert policy.order == 800
    assert sdk.order == 5000
    assert assembled.text.index("run_code is the only tool") < assembled.text.index(
        "type ToolArgsMap"
    )
    assert '"add"' in assembled.text
    assert '"run_code"' not in assembled.text


@pytest.mark.asyncio
async def test_ptc_stream_emits_audit_separately_from_model_tool_output() -> None:
    loop = PluginAgentLoop()
    loop._llm_provider = type("LLM", (), {"get_model": lambda _: _RunCodeModel()})()
    loop._tool_providers = [_ToolProvider()]
    loop._presentation_provider = _Mode("ptc")
    loop._ptc_runtime = NodePTCRuntime()

    events = [event async for event in loop.astream("sum")]

    audit = [event for event in events if event["type"] == "ptc_audit"]
    outputs = [event for event in events if event["type"] == "tool_output"]
    assert len(audit) == 2
    assert [event["status"] for event in audit] == ["started", "completed"]
    assert audit[0]["call_id"] == audit[1]["call_id"]
    assert audit[1]["result"] == 10
    assert len(outputs) == 1
    assert "audit" not in outputs[0]["output"]


@pytest.mark.asyncio
async def test_ptc_scope_switch_changes_sdk_and_dispatch_target() -> None:
    async def alpha_tool(value: int) -> int:
        return value + 10

    async def beta_tool(value: int) -> int:
        return value + 20

    alpha = StructuredTool.from_function(
        coroutine=alpha_tool, name="alpha_tool", description="Alpha tool"
    )
    beta = StructuredTool.from_function(
        coroutine=beta_tool, name="beta_tool", description="Beta tool"
    )

    class Provider:
        def __init__(self, scope: str, key: str, tool: Any) -> None:
            self._plugin_scope_id = scope
            self._plugin_key = key
            self._tool = tool

        def get_tools(self) -> list[Any]:
            return [self._tool]

    loop = PluginAgentLoop()
    loop._tool_providers = [
        Provider("agent:alpha", "alpha-tools", alpha),
        Provider("agent:beta", "beta-tools", beta),
    ]
    loop._presentation_provider = _Mode("ptc")
    loop._ptc_runtime = NodePTCRuntime()

    loop._scope_chain = ["agent:alpha"]
    alpha_sdk = render_tool_sdk(loop._collect_native_tools())
    alpha_result = await loop._run_code("return await tools.alpha_tool({value: 1});")
    loop._scope_chain = ["agent:beta"]
    beta_sdk = render_tool_sdk(loop._collect_native_tools())
    beta_result = await loop._run_code("return await tools.beta_tool({value: 1});")

    assert '"alpha_tool"' in alpha_sdk and '"beta_tool"' not in alpha_sdk
    assert '"beta_tool"' in beta_sdk and '"alpha_tool"' not in beta_sdk
    assert alpha_result["result"] == 11
    assert beta_result["result"] == 21


@pytest.mark.asyncio
async def test_loop_serializes_writes_but_allows_read_only_calls() -> None:
    read_entered = 0
    both_reads_entered = asyncio.Event()
    release_reads = asyncio.Event()
    first_write_started = asyncio.Event()
    release_first_write = asyncio.Event()
    second_write_started = asyncio.Event()

    async def read(value: int) -> int:
        nonlocal read_entered
        read_entered += 1
        if read_entered == 2:
            both_reads_entered.set()
        await both_reads_entered.wait()
        await release_reads.wait()
        return value

    write_count = 0

    async def write(value: int) -> int:
        nonlocal write_count
        write_count += 1
        if write_count == 1:
            first_write_started.set()
            await release_first_write.wait()
        else:
            second_write_started.set()
        return value

    reads = StructuredTool.from_function(
        coroutine=read, name="read", description="Read", metadata={"read_only": True}
    )
    writes = StructuredTool.from_function(
        coroutine=write, name="write", description="Write", metadata={"read_only": False}
    )

    class Provider:
        def get_tools(self) -> list[Any]:
            return [reads, writes]

    loop = PluginAgentLoop()
    loop._tool_providers = [Provider()]
    loop._presentation_provider = _Mode("ptc")
    loop._ptc_runtime = NodePTCRuntime()

    read_task = asyncio.create_task(
        loop._run_code(
            "return await Promise.all([tools.read({value: 1}), tools.read({value: 2})]);"
        )
    )
    await asyncio.wait_for(both_reads_entered.wait(), timeout=1)
    assert read_entered == 2
    release_reads.set()
    result = await read_task
    assert result["result"] == [1, 2]

    write_task = asyncio.create_task(
        loop._run_code(
            "return await Promise.all([tools.write({value: 1}), tools.write({value: 2})]);"
        )
    )
    await asyncio.wait_for(first_write_started.wait(), timeout=1)
    await asyncio.sleep(0)
    assert not second_write_started.is_set()
    release_first_write.set()
    result = await write_task
    assert result["result"] == [1, 2]
    assert second_write_started.is_set()


@pytest.mark.asyncio
async def test_ptc_writer_does_not_overlap_an_earlier_reader() -> None:
    active = 0
    overlap = False

    async def read(value: int) -> int:
        nonlocal active
        active += 1
        await asyncio.sleep(0.08)
        active -= 1
        return value

    async def write(value: int) -> int:
        nonlocal overlap
        overlap = active > 0
        return value

    reader = StructuredTool.from_function(
        coroutine=read,
        name="read",
        description="Read",
        metadata={"read_only": True},
    )
    writer = StructuredTool.from_function(
        coroutine=write,
        name="write",
        description="Write",
        metadata={"read_only": False},
    )

    class Provider:
        def get_tools(self) -> list[Any]:
            return [reader, writer]

    loop = PluginAgentLoop()
    loop._tool_providers = [Provider()]
    loop._presentation_provider = _Mode("ptc")
    loop._ptc_runtime = NodePTCRuntime()
    result = await loop._run_code(
        "return await Promise.all([tools.read({value: 1}), "
        "tools.write({value: 2})]);"
    )

    assert result["result"] == [1, 2]
    assert not overlap

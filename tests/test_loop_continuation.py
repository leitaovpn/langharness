
"""Tests for automatic goal continuation in the agent loop."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportArgumentType=false

from __future__ import annotations

from types import SimpleNamespace

from langharness_core.contracts import Continue, Stop
from langharness_core.plugins.loop.agent_loop import PluginAgentLoop


class FakeController:
    def __init__(self, decisions: list[object]) -> None:
        self._decisions = list(decisions)
        self.calls: list[tuple[int, bool]] = []

    def decide(self, *, rounds_run: int, checkpointer_available: bool):
        self.calls.append((rounds_run, checkpointer_available))
        return self._decisions.pop(0) if self._decisions else Stop(
            status="complete", reason="done", wrapup="w"
        )


def test_without_a_controller_there_is_no_decision() -> None:
    assert PluginAgentLoop()._decide(0) is None


def test_the_controller_sees_the_round_count_and_checkpointer_state() -> None:
    loop = PluginAgentLoop()
    controller = FakeController([Continue(message="go", round=1)])
    loop._run_controller = controller
    loop._checkpointer_provider = SimpleNamespace()
    decision = loop._decide(3)
    assert isinstance(decision, Continue)
    assert controller.calls == [(3, True)]


def test_no_checkpointer_is_reported_as_unavailable() -> None:
    loop = PluginAgentLoop()
    controller = FakeController([Stop(status="active", reason="no-checkpointer", wrapup="w")])
    loop._run_controller = controller
    loop._decide(0)
    assert controller.calls == [(0, False)]


def test_a_failing_controller_stops_the_run() -> None:
    class Boom:
        def decide(self, **kwargs: object) -> object:
            raise RuntimeError("controller exploded")

    loop = PluginAgentLoop()
    loop._run_controller = Boom()
    decision = loop._decide(0)
    assert isinstance(decision, Stop)
    assert decision.reason == "controller-error"


def test_the_loop_declares_the_controller_field_as_scoped() -> None:
    from langharness_plugin.scoped_dependencies import scoped_fields

    assert "_run_controller" in scoped_fields(PluginAgentLoop)


# --- through the real framework -------------------------------------------


import asyncio  # noqa: E402

from langchain_core.language_models.chat_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402

from langharness_core.contracts import (  # noqa: E402
    SPEC_AGENT_LOOP,
    SPEC_CHECKPOINTER,
    SPEC_LLM,
)
from langharness_plugin.plugin_manager import PluginManager  # noqa: E402
from langharness_plugin.registry import PluginDescriptor, PluginRegistry  # noqa: E402
from langharness_scope import ROOT_SCOPE_ID  # noqa: E402

DESCRIPTION = (
    "Continuation test plugin. Implements a test specification. Properties "
    "vary per instance. Requires a restart for property changes. Uninstall "
    "when tests finish."
)

PROMPTS: list[list[str]] = []


class ScriptedContinuationModel(BaseChatModel):
    """Answers once, then again if asked — recording what it was asked."""

    calls: int = 0

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        PROMPTS.append(
            [str(m.content) for m in messages if isinstance(m, HumanMessage)]
        )
        self.calls += 1
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=f"reply {self.calls}"))]
        )

    @property
    def _llm_type(self) -> str:
        return "scripted-continuation"

    def bind_tools(self, tools, **kwargs):
        return self


class OnceController:
    """Continues exactly once, then stops."""

    def __init__(self) -> None:
        self.calls = 0

    def decide(self, *, rounds_run: int, checkpointer_available: bool):
        self.calls += 1
        if self.calls == 1:
            return Continue(message="keep going, round 2", round=1)
        return Stop(status="complete", reason="done", wrapup="write the summary")

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": "once", "version": "1.0.0"}


def descriptor(name: str, module: str, factory: str, spec: str) -> PluginDescriptor:
    return PluginDescriptor(
        name=name,
        version="1.0.0",
        module=module,
        factory=factory,
        specification=spec,
        description=DESCRIPTION,
    )


class Installed:
    def __init__(self, name: str, module: str, factory: str, spec: str) -> None:
        self.descriptor = descriptor(name, module, factory, spec)


def test_an_active_goal_runs_another_round_through_the_loop() -> None:
    manager = PluginManager(PluginRegistry())
    manager.start()
    try:
        model = ScriptedContinuationModel()
        installs = [
            Installed("llm", "langharness_core.plugins.llm.llm", "llm-plugin-factory", SPEC_LLM),
            Installed(
                "agent-loop",
                "langharness_core.plugins.loop.agent_loop",
                "agent-loop-factory",
                SPEC_AGENT_LOOP,
            ),
        ]
        for item in installs:
            manager.install_descriptor(item.descriptor)
        manager.create_instance(
            "llm-plugin-factory",
            "langharness_core.plugins.llm.llm",
            ROOT_SCOPE_ID,
            properties={"plugin.model.instance": model},
        )
        manager.create_instance(
            "agent-loop-factory",
            "langharness_core.plugins.loop.agent_loop",
            ROOT_SCOPE_ID,
        )
        loop = manager.find_service(SPEC_AGENT_LOOP)
        loop._run_controller = OnceController()
        PROMPTS.clear()

        async def collect() -> list[dict]:
            return [event async for event in loop.astream("do the thing")]

        events = asyncio.run(collect())
        kinds = [item["type"] for item in events]
        # No checkpointer is bound, so the loop must refuse to continue even
        # though the controller asked for another round.
        assert "goal_round_started" not in kinds
        assert "goal_status" in kinds
        assert model.calls == 1
    finally:
        manager.stop()


def test_with_a_checkpointer_the_conversation_accumulates(tmp_path) -> None:
    manager = PluginManager(PluginRegistry())
    manager.start()
    try:
        model = ScriptedContinuationModel()
        for item in (
            Installed(
                "llm", "langharness_core.plugins.llm.llm", "llm-plugin-factory", SPEC_LLM
            ),
            Installed(
                "checkpointer",
                "langharness_core.plugins.checkpointer.sqlite",
                "sqlite-checkpointer-plugin-factory",
                SPEC_CHECKPOINTER,
            ),
            Installed(
                "agent-loop",
                "langharness_core.plugins.loop.agent_loop",
                "agent-loop-factory",
                SPEC_AGENT_LOOP,
            ),
        ):
            manager.install_descriptor(item.descriptor)
        manager.create_instance(
            "llm-plugin-factory",
            "langharness_core.plugins.llm.llm",
            ROOT_SCOPE_ID,
            properties={"plugin.model.instance": model},
        )
        manager.create_instance(
            "sqlite-checkpointer-plugin-factory",
            "langharness_core.plugins.checkpointer.sqlite",
            ROOT_SCOPE_ID,
            properties={"plugin.checkpoint.path": str(tmp_path / "cp.sqlite3")},
        )
        manager.create_instance(
            "agent-loop-factory",
            "langharness_core.plugins.loop.agent_loop",
            ROOT_SCOPE_ID,
        )
        loop = manager.find_service(SPEC_AGENT_LOOP)
        loop._run_controller = OnceController()
        PROMPTS.clear()

        async def collect() -> list[dict]:
            return [
                event
                async for event in loop.astream("do the thing", thread_id="t1")
            ]

        events = asyncio.run(collect())
        kinds = [item["type"] for item in events]
        assert kinds.count("goal_round_started") == 1
        # The whole point: round two sees round one's conversation, and the
        # wrapup step sees both.
        assert PROMPTS[0] == ["do the thing"]
        assert PROMPTS[1] == ["do the thing", "keep going, round 2"]
        assert PROMPTS[2][-2:] == ["keep going, round 2", "write the summary"]
        assert any(item.get("content") == "reply 3" for item in events)
    finally:
        manager.stop()

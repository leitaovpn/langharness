"""End-to-end assembly and isolation of the code agent preset."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from fixtures.failing_repo import build as build_failing_repo
from langharness_core.contracts import (
    SPEC_AGENT_LOOP,
    SPEC_CHECKPOINTER,
    SPEC_GOAL,
    SPEC_PROMPT_SECTION,
    SPEC_TOOL,
    SPEC_WORKSPACE,
    SPEC_WORKSPACE_STATE,
    PromptContext,
)
from langharness_core.plugin import (
    agent_loop_descriptor,
    agent_loop_properties,
    agent_plugin_descriptor,
    agent_preset_bindings,
    agent_scoped_specifications,
)
from langharness_plugin.plugin_manager import PluginManager
from langharness_plugin.registry import PluginRegistry
from langharness_plugin.scope_const import AGENT_SCOPE_ID, agent_instance_scope_id


def install_preset(manager: PluginManager) -> None:
    """Install each code preset bundle once for all agents."""
    for name in agent_preset_bindings("code"):
        manager.install_descriptor(agent_plugin_descriptor(name))
    manager.install_descriptor(agent_loop_descriptor())


def build_agent(manager: PluginManager, agent_id: str, workspace: Path) -> None:
    scope = agent_instance_scope_id(agent_id)
    manager.add_scope(scope, name=agent_id, parent_id=AGENT_SCOPE_ID)
    for name in agent_preset_bindings("code"):
        descriptor = agent_plugin_descriptor(name)
        properties: dict[str, Any] = {"plugin.agent_id": agent_id}
        if name == "workspace":
            properties["plugin.workspace.root_dir"] = str(workspace)
        elif name == "goal":
            properties["plugin.goal.path"] = str(
                workspace.parent / f"{agent_id}-goals.sqlite3"
            )
        elif name == "verifier":
            properties["plugin.verify.commands"] = [["python3", "-c", "pass"]]
        manager.create_instance(
            descriptor.factory, descriptor.module, scope, properties=properties
        )
    loop = agent_loop_descriptor()
    manager.create_instance(
        loop.factory,
        loop.module,
        scope,
        properties=agent_loop_properties(
            agent_id,
            agent_scoped_specifications(),
            visibility_filter=manager.scope_filter(scope),
        ),
    )


def services_for(manager: PluginManager, spec: str, agent_id: str) -> list[Any]:
    return manager.find_services(spec, filter=f"(plugin.scope_id=agent:{agent_id})")


def tools_for(manager: PluginManager, agent_id: str) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for provider in services_for(manager, SPEC_TOOL, agent_id):
        merged.update({item.name: item for item in provider.get_tools()})
    return merged


def test_two_code_agents_keep_prompts_tools_workspaces_and_goals_separate(
    tmp_path: Path,
) -> None:
    manager = PluginManager(PluginRegistry())
    manager.start()
    try:
        alpha_dir = tmp_path / "alpha"
        beta_dir = tmp_path / "beta"
        alpha_dir.mkdir()
        beta_dir.mkdir()
        (alpha_dir / "only-alpha.py").write_text("x\n", encoding="utf-8")
        (alpha_dir / "AGENTS.md").write_text("alpha-only instruction", encoding="utf-8")
        (beta_dir / "AGENTS.md").write_text("beta-only instruction", encoding="utf-8")

        install_preset(manager)
        build_agent(manager, "alpha", alpha_dir)
        build_agent(manager, "beta", beta_dir)

        alpha, beta = tools_for(manager, "alpha"), tools_for(manager, "beta")
        assert {
            "list_directory",
            "read_file",
            "search_text",
            "apply_patch",
            "workspace_status",
            "exec_command",
            "verify",
            "ask_user",
            "get_goal",
            "finish_goal",
        } <= set(alpha)
        assert set(alpha) == set(beta)
        assert {
            item["name"] for item in alpha["list_directory"].invoke({})["entries"]
        } == {"AGENTS.md", "only-alpha.py"}
        assert [
            item["name"] for item in beta["list_directory"].invoke({})["entries"]
        ] == ["AGENTS.md"]
        assert (
            beta["read_file"].invoke({"path": "only-alpha.py"})["error"]["code"]
            == "path_not_found"
        )

        alpha_goal = services_for(manager, SPEC_GOAL, "alpha")[0]
        beta_goal = services_for(manager, SPEC_GOAL, "beta")[0]
        alpha_goal.create_goal("alpha objective", [{"id": "c1", "text": "t"}])
        assert beta_goal.current_goal() is None

        alpha_state = services_for(manager, SPEC_WORKSPACE_STATE, "alpha")[0]
        beta_state = services_for(manager, SPEC_WORKSPACE_STATE, "beta")[0]
        alpha["apply_patch"].invoke(
            {"patch": "*** Begin Patch\n*** Add File: made.py\n+x\n*** End Patch\n"}
        )
        assert alpha_state.get_generation() == 1
        assert beta_state.get_generation() == 0
        assert not (beta_dir / "made.py").exists()

        alpha_ws = services_for(manager, SPEC_WORKSPACE, "alpha")[0]
        beta_ws = services_for(manager, SPEC_WORKSPACE, "beta")[0]
        assert Path(alpha_ws.get_workspace()) == alpha_dir.resolve()
        assert Path(beta_ws.get_workspace()) == beta_dir.resolve()

        def prompt_for(agent_id: str, workspace: Path) -> str:
            context = PromptContext(model="test-model", workspace=str(workspace))
            return "\n".join(
                section.text
                for provider in services_for(manager, SPEC_PROMPT_SECTION, agent_id)
                for section in provider.get_prompt_sections(context)
            )

        alpha_prompt = prompt_for("alpha", alpha_dir)
        beta_prompt = prompt_for("beta", beta_dir)
        assert "alpha-only instruction" in alpha_prompt
        assert "beta-only instruction" not in alpha_prompt
        assert "beta-only instruction" in beta_prompt
        assert "alpha-only instruction" not in beta_prompt
    finally:
        manager.stop()


class ScriptedModel(BaseChatModel):
    """Make two tool calls in one turn, then finish."""

    step: int = 0

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.step += 1
        if self.step == 1:
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"path": "input.txt"},
                        "id": "read-1",
                        "type": "tool_call",
                    },
                    {
                        "name": "apply_patch",
                        "args": {
                            "patch": "*** Begin Patch\n*** Add File: made.py\n+x\n*** End Patch\n"
                        },
                        "id": "patch-1",
                        "type": "tool_call",
                    },
                ],
            )
        else:
            message = AIMessage(content="done")
        return ChatResult(generations=[ChatGeneration(message=message)])

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self


@pytest.mark.parametrize("protocol", ("chat", "anthropic", "responses"))
def test_multi_tool_turn_streams_on_each_protocol(
    protocol: str, tmp_path: Path
) -> None:
    manager = PluginManager(PluginRegistry())
    manager.start()
    try:
        install_preset(manager)
        llm = agent_plugin_descriptor("llm")
        manager.install_descriptor(llm)
        workspace = tmp_path / protocol
        workspace.mkdir()
        (workspace / "input.txt").write_text("input\n", encoding="utf-8")
        build_agent(manager, "alpha", workspace)
        manager.create_instance(
            llm.factory,
            llm.module,
            agent_instance_scope_id("alpha"),
            properties={
                "plugin.model.instance": ScriptedModel(),
                "plugin.model.protocol": protocol,
                "plugin.agent_id": "alpha",
            },
        )
        loop = manager.find_service(
            SPEC_AGENT_LOOP, filter="(plugin.scope_id=agent:alpha)"
        )
        assert loop is not None

        async def collect() -> list[dict[str, Any]]:
            return [event async for event in loop.astream("Read and edit a file")]

        events = asyncio.run(collect())
        kinds = [event["type"] for event in events]
        assert kinds.count("tool_call") >= 2, events
        assert kinds.count("tool_output") >= 2, events
        assert "error" not in kinds, events
        assert (workspace / "made.py").read_text(encoding="utf-8") == "x\n"
    finally:
        manager.stop()


@pytest.mark.real_llm
def test_real_model_fixes_a_failing_repository(tmp_path: Path) -> None:
    """Optional acceptance test of the code agent's full goal workflow."""
    model_name = os.environ.get("LANG_HARNESS_REAL_LLM_PROVIDER")
    if not model_name:
        pytest.skip("set LANG_HARNESS_REAL_LLM_PROVIDER to run real LLM smoke")

    workspace = build_failing_repo(tmp_path / "repo")
    manager = PluginManager(PluginRegistry())
    manager.start()
    try:
        install_preset(manager)
        llm = agent_plugin_descriptor("llm")
        manager.install_descriptor(llm)
        manager.create_instance(
            llm.factory,
            llm.module,
            AGENT_SCOPE_ID,
            properties={
                "plugin.model.name": model_name,
                "plugin.model.protocol": os.environ.get(
                    "LANG_HARNESS_REAL_LLM_PROTOCOL", "chat"
                ),
            },
        )
        from langharness_core.plugin import sqlite_checkpointer_descriptor

        checkpointer = sqlite_checkpointer_descriptor()
        manager.install_descriptor(checkpointer)
        manager.create_instance(
            checkpointer.factory,
            checkpointer.module,
            AGENT_SCOPE_ID,
            properties={
                "plugin.checkpoint.path": str(tmp_path / "checkpoints.sqlite3")
            },
        )
        build_agent(manager, "alpha", workspace)
        verifier = agent_plugin_descriptor("verifier")
        installed = manager.list_instance(
            factory=verifier.factory, scope_id=agent_instance_scope_id("alpha")
        )
        manager.update_instance(
            installed[0].instance,
            properties={"plugin.verify.commands": [["python", "-m", "pytest", "-q"]]},
        )
        goal_store = services_for(manager, SPEC_GOAL, "alpha")[0]
        goal_store.create_goal(
            "Fix calc.total so the test passes",
            [{"id": "c1", "text": "python -m pytest -q exits 0"}],
        )
        assert manager.find_service(SPEC_CHECKPOINTER) is not None
        loop = manager.find_service(
            SPEC_AGENT_LOOP, filter="(plugin.scope_id=agent:alpha)"
        )

        async def collect() -> list[dict[str, Any]]:
            return [
                event
                async for event in loop.astream(
                    "The test in test_calc.py fails. Fix the bug."
                )
            ]

        events = asyncio.run(collect())
        assert not any(event["type"] == "error" for event in events), events
        result = subprocess.run(
            ["python", "-m", "pytest", "-q"],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout
        assert goal_store.current_goal() is None
    finally:
        manager.stop()

"""Real Pelix/iPOPO lifecycle coverage for hierarchical plugin scopes."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from langharness_core.contracts import SPEC_AGENT_LOOP, SPEC_LLM, SPEC_TOOL
from langharness_core.plugin import agent_loop_descriptor, agent_loop_properties
from langharness_plugin.plugin_manager import PluginManager
from langharness_plugin.registry import PluginDescriptor, PluginRegistry
from langharness_scope import InMemoryScopeStore, ScopeId, ScopeTree

DESCRIPTION = (
    "Scope e2e test plugin. Implements a test specification. Properties: "
    "plugin.model.instance or plugin.tools.functions. Requires a restart "
    "for property changes. Uninstall when tests finish."
)


class ScopeModel(BaseChatModel):
    label: str

    def _generate(
        self,
        messages: Any,
        stop: Any = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=self.label))]
        )

    @property
    def _llm_type(self) -> str:
        return "scope-model"

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScopeModel:
        return self


def root_shared() -> str:
    """Return the root implementation."""
    return "root"


def agent_shared() -> str:
    """Return the nearer agent implementation."""
    return "agent"


def root_other() -> str:
    """Return a non-shadowed root implementation."""
    return "other"


def ui_tool() -> str:
    """Identify the UI scope."""
    return "ui"


def server_tool() -> str:
    """Identify the server scope."""
    return "server"


def agent_tool() -> str:
    """Identify the agent scope."""
    return "agent"


def agent_a_tool() -> str:
    """Identify one agent instance scope."""
    return "agent-a"


def agent_b_tool() -> str:
    """Identify a sibling agent instance scope."""
    return "agent-b"


def template(
    name: str, module: str, factory: str, specification: str
) -> PluginDescriptor:
    return PluginDescriptor(
        name=name,
        version="1.0.0",
        module=module,
        factory=factory,
        specification=specification,
        description=DESCRIPTION,
    )


def test_scope_and_plugin_full_lifecycle_with_best_and_aggregate_shadowing() -> None:
    scope_store = InMemoryScopeStore()
    manager = PluginManager(PluginRegistry(), scope_tree=ScopeTree(scope_store))
    manager.start()
    try:
        for item in [
            template(
                "llm-template",
                "langharness_core.plugins.llm.llm",
                "llm-plugin-factory",
                SPEC_LLM,
            ),
            template(
                "tools-template",
                "langharness_core.plugins.tools.tools",
                "tools-plugin-factory",
                SPEC_TOOL,
            ),
            template(
                "loop-template",
                "langharness_core.plugins.loop.agent_loop",
                "agent-loop-factory",
                SPEC_AGENT_LOOP,
            ),
        ]:
            manager.install_descriptor(item)
        manager.add_scope(
            ScopeId("session"), name="Session", parent_id=ScopeId("agent")
        )
        restored = ScopeTree(scope_store)
        assert [scope.id for scope in restored.ancestors(ScopeId("session"))] == [
            ScopeId("agent"),
            ScopeId("root"),
        ]

        manager.create_instance(
            "llm-plugin-factory",
            "langharness_core.plugins.llm.llm",
            ScopeId("root"),
            ranking=999,
            properties={"plugin.model.instance": ScopeModel(label="root")},
        )
        agent_model = ScopeModel(label="agent")
        manager.create_instance(
            "llm-plugin-factory",
            "langharness_core.plugins.llm.llm",
            ScopeId("agent"),
            ranking=1,
            properties={"plugin.model.instance": agent_model},
        )
        manager.create_instance(
            "tools-plugin-factory",
            "langharness_core.plugins.tools.tools",
            ScopeId("root"),
            properties={"plugin.tools.functions": [root_shared]},
        )
        agent_tools = manager.create_instance(
            "tools-plugin-factory",
            "langharness_core.plugins.tools.tools",
            ScopeId("agent"),
            properties={"plugin.tools.functions": [agent_shared]},
        )

        visible = manager.scope_filter(ScopeId("session"))
        loop_descriptor = agent_loop_descriptor()
        manager.create_instance(
            loop_descriptor.factory,
            loop_descriptor.module,
            ScopeId("session"),
            properties=agent_loop_properties(
                "session", [SPEC_LLM, SPEC_TOOL], visibility_filter=visible
            ),
        )
        loop = manager.find_service(
            SPEC_AGENT_LOOP, "(plugin.scope_id=session)"
        )

        llm_properties = manager.service_properties(SPEC_LLM)
        assert [
            (item.get("plugin.scope_id"), item.get("service.ranking"))
            for item in llm_properties
        ] == [("agent", 1_000_001), ("root", 999)]
        assert manager.find_service(SPEC_LLM, visible).get_model() is agent_model
        assert loop._llm_provider.get_model() is agent_model, llm_properties
        # Same factory instances shadow each other per nearest scope: the
        # loop's aggregate sees only the agent instance.
        assert [tool.name for tool in loop._collect_tools()] == [
            "agent_shared",
        ], (loop._scope_chain, loop._service_metadata)

        manager.delete_instance(agent_tools.instance)
        assert [tool.name for tool in loop._collect_tools()] == [
            "root_shared",
        ]

        manager.remove_scope(ScopeId("agent"), recursive=True)
        assert manager.scope_tree.get(ScopeId("agent")) is None
        assert ScopeTree(scope_store).get(ScopeId("agent")) is None
        assert manager.find_service(
            SPEC_AGENT_LOOP, "(plugin.scope_id=session)"
        ) is None
        assert {item.factory for item in manager.list_instance()} == {
            "llm-plugin-factory",
            "tools-plugin-factory",
        }
        remaining = {
            (item.factory, str(item.scope_id)) for item in manager.list_instance()
        }
        assert remaining == {
            ("llm-plugin-factory", "root"),
            ("tools-plugin-factory", "root"),
        }

        for snapshot in tuple(manager.list_instance()):
            manager.delete_instance(snapshot.instance)
        for plugin in tuple(manager.list_plugin()):
            manager.uninstall_plugin(plugin.descriptor.factory)
        assert manager.list_plugin() == ()
        assert manager.installed_modules() == set()
    finally:
        manager.stop()


def test_runtime_scope_visibility_matrix_with_real_service_registry() -> None:
    tools_template = template(
        "visibility-tools-template",
        "langharness_core.plugins.tools.tools",
        "tools-plugin-factory",
        SPEC_TOOL,
    )
    manager = PluginManager(PluginRegistry())
    manager.start()
    try:
        manager.install_descriptor(tools_template)
        manager.add_scope(ScopeId("agent:a"), name="A", parent_id=ScopeId("agent"))
        manager.add_scope(ScopeId("agent:b"), name="B", parent_id=ScopeId("agent"))
        registrations = [
            ("root", root_other),
            ("ui", ui_tool),
            ("server", server_tool),
            ("agent", agent_tool),
            ("agent:a", agent_a_tool),
            ("agent:b", agent_b_tool),
        ]
        for scope, function in registrations:
            manager.create_instance(
                "tools-plugin-factory",
                "langharness_core.plugins.tools.tools",
                ScopeId(scope),
                properties={"plugin.tools.functions": [function]},
            )

        def visible(scope: str) -> set[str]:
            providers = manager.find_services(
                SPEC_TOOL, manager.scope_filter(ScopeId(scope))
            )
            return {provider.get_tools()[0].name for provider in providers}

        assert visible("root") == {"root_other"}
        assert visible("ui") == {"root_other", "ui_tool"}
        assert visible("server") == {"root_other", "server_tool"}
        assert visible("agent") == {"root_other", "agent_tool"}
        assert visible("agent:a") == {
            "root_other",
            "agent_tool",
            "agent_a_tool",
        }
        assert "agent_b_tool" not in visible("agent:a")
        assert "ui_tool" not in visible("agent:a")
    finally:
        manager.stop()

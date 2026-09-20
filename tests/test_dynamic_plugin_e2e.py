"""Real Pelix/iPOPO e2e for dynamic plugin discovery and tool exports."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel

from langharness_core.contracts import (
    SPEC_AGENT_LOOP,
    SPEC_LLM,
    SPEC_TOOL,
)
from langharness_core.plugin import (
    agent_loop_descriptor,
    agent_loop_properties,
    agent_loop_template_descriptor,
    agent_tools_package,
    dynamic_package,
    tool_export_adapter_template_descriptor,
)
from langharness_plugin.contracts import (
    SPEC_TOOL_EXPORT_TARGET,
    DynamicPluginManager,
)
from langharness_plugin.coordinator import (
    RuntimeMutationCoordinator,
)
from langharness_plugin.discovery import PluginDiscovery
from langharness_plugin.package import PluginContribution, PluginPackage, ToolExport
from langharness_plugin.plugin_manager import PluginManager
from langharness_plugin.registry import PluginDescriptor, PluginRegistry
from langharness_plugin.state_store import (
    InMemoryPluginHistoryStore,
    InMemoryRuntimeStateStore,
)
from langharness_scope import ScopeId


class EchoArgs(BaseModel):
    text: str


class EntryPoint:
    def __init__(self, package: PluginPackage, name: str) -> None:
        self.package = package
        self.name = name
        self.value = f"{name}:plugin"

    def load(self):
        return lambda: self.package


ECHO_DESCRIPTION = (
    "Echo tool export target. Implements plugin.tool_export.target. "
    "No properties. Uninstall when the echo tools are not needed."
)


def echo_descriptor(name: str, version: str = "1.0.0") -> PluginDescriptor:
    return PluginDescriptor(
        name=name,
        version=version,
        module="dynamic_plugins.echo",
        factory="dynamic-echo-factory",
        specification=SPEC_TOOL_EXPORT_TARGET,
        description=ECHO_DESCRIPTION,
    )


def server_echo_package(version: str = "1.0.0") -> PluginPackage:
    return PluginPackage(
        "example.echo",
        version,
        (
            PluginContribution(
                "echo",
                "server",
                echo_descriptor("server-echo", version=version),
                tool_exports=(ToolExport("server_echo", "Server echo", "echo", EchoArgs),),
            ),
        ),
    )


def agent_echo_package() -> PluginPackage:
    return PluginPackage(
        "example.agent-echo",
        "1.0.0",
        (
            PluginContribution(
                "echo",
                "agent_instance",
                echo_descriptor("agent-echo"),
                tool_exports=(
                    ToolExport(
                        "server_echo",
                        "Agent echo",
                        "echo",
                        EchoArgs,
                        target_scope="agent_instance",
                    ),
                ),
            ),
        ),
    )


def tool_names(manager: PluginManager, scope: str) -> list[str]:
    providers = manager.find_services(
        SPEC_TOOL, manager.scope_filter(ScopeId(scope))
    )
    names: list[str] = []
    for provider in providers:
        names.extend(tool.name for tool in provider.get_tools())
    return names


def make_manager() -> PluginManager:
    manager = PluginManager(PluginRegistry())
    manager.start()
    return manager


def install_templates(manager: PluginManager) -> None:
    for descriptor in [
        dynamic_template_tools_descriptor(),
        tool_export_adapter_template_descriptor(),
    ]:
        manager.install_descriptor(descriptor)
    manager.add_scope(ScopeId("agent:a"), name="A", parent_id=ScopeId("agent"))


def dynamic_template_tools_descriptor() -> PluginDescriptor:
    return PluginDescriptor(
        name="tools-template",
        version="1.0.0",
        module="langharness_core.plugins.tools.tools",
        factory="tools-plugin-factory",
        specification=SPEC_TOOL,
        description=(
            "Template for the tools plugin bundle. Implements agent.plugin.tools. "
            "Install to preload the tools bundle for agent scopes."
        ),
    )


def make_discovery() -> PluginDiscovery:
    return PluginDiscovery(
        lambda: [
            EntryPoint(server_echo_package(), "echo"),
            EntryPoint(agent_echo_package(), "agent-echo"),
        ]
    )


def make_coordinator(
    manager: PluginManager, discovery: PluginDiscovery
) -> tuple[RuntimeMutationCoordinator, InMemoryRuntimeStateStore]:
    store = InMemoryRuntimeStateStore()
    manager.bind_state(store, InMemoryPluginHistoryStore())
    coordinator = RuntimeMutationCoordinator(manager, discovery=discovery)
    coordinator.rescan()
    return coordinator, store


def test_dynamic_discovery_install_visibility_and_restore() -> None:
    manager = make_manager()
    try:
        install_templates(manager)
        coordinator, store = make_coordinator(manager, make_discovery())

        coordinator.install("example.echo", "echo")
        coordinator.install(
            "example.agent-echo", "echo", scope_id=ScopeId("agent:a")
        )

        assert "server_echo" in tool_names(manager, "agent")
        assert "server_echo" not in tool_names(manager, "ui")

        agent_properties = manager.service_properties(SPEC_TOOL)
        assert {item.get("plugin.scope_id") for item in agent_properties} == {
            "agent",
            "agent:a",
        }
        ranked = sorted(
            agent_properties,
            key=lambda item: item.get("service.ranking", 0),
            reverse=True,
        )
        assert [item.get("plugin.scope_id") for item in ranked] == [
            "agent:a",
            "agent",
        ]
        tools = []
        for item in ranked:
            provider = manager.find_service(
                SPEC_TOOL,
                f"(plugin.scope_id={item.get('plugin.scope_id')})",
            )
            tools.extend(tool.name for tool in provider.get_tools())
        assert tools.count("server_echo") == 2
    finally:
        manager.stop()

    restored = make_manager()
    try:
        install_templates(restored)
        restored.bind_state(store, InMemoryPluginHistoryStore())
        restarted = RuntimeMutationCoordinator(restored, discovery=make_discovery())
        restarted.rescan()
        registrations = restarted.restore()
        assert {item.scope_id for item in registrations} == {
            ScopeId("server"),
            ScopeId("agent:a"),
        }
        for scope_id in ("ui", "server", "agent", "agent:a"):
            assert restored.scope_tree.get(ScopeId(scope_id)) is not None
    finally:
        restored.stop()


def test_dynamic_discovery_full_lifecycle() -> None:
    manager = make_manager()
    try:
        install_templates(manager)
        coordinator, _ = make_coordinator(manager, make_discovery())

        assert [item.id for item in coordinator.discovered()] == [
            "example.agent-echo",
            "example.echo",
        ]

        installed = coordinator.install("example.echo", "echo")
        assert installed.factory == "dynamic-echo-factory"
        assert installed.scope_id == ScopeId("server")
        assert "server_echo" in tool_names(manager, "agent")

        disabled = coordinator.set_enabled(
            "server-echo", False, scope_id=ScopeId("server")
        )
        assert disabled.enabled is False
        assert "server_echo" not in tool_names(manager, "agent")

        enabled = coordinator.set_enabled(
            "server-echo", True, scope_id=ScopeId("server")
        )
        assert enabled.enabled is True
        assert "server_echo" in tool_names(manager, "agent")

        coordinator._catalog["example.echo"] = server_echo_package("1.1.0")
        upgraded = coordinator.upgrade("server-echo", scope_id=ScopeId("server"))
        assert upgraded.package_version == "1.1.0"

        coordinator.uninstall("server-echo", scope_id=ScopeId("server"))
        assert coordinator.registrations() == ()
        assert "server_echo" not in tool_names(manager, "agent")
    finally:
        manager.stop()


MANAGEMENT_TOOLS = {
    "list_scope_tree",
    "list_runtime_plugins",
    "discover_plugins",
    "install_plugin",
    "enable_plugin",
    "disable_plugin",
    "upgrade_plugin",
    "uninstall_plugin",
    "update_plugin_properties",
}

AGENT_TOOLS = {"list_agents", "get_agent", "create_agent", "update_agent"}


class StaticModel(BaseChatModel):
    response: str = "ok"

    def _generate(
        self,
        messages: Any,
        stop: Any = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=self.response))]
        )

    @property
    def _llm_type(self) -> str:
        return "static-model"

    def bind_tools(self, tools: Any, **kwargs: Any) -> StaticModel:
        return self


def make_loop_manager() -> PluginManager:
    manager = PluginManager(PluginRegistry())
    manager.start()
    return manager


def materialize_loop(manager: PluginManager) -> None:
    scope = ScopeId("agent:a")
    manager.add_scope(scope, name="A", parent_id=ScopeId("agent"))
    manager.install_descriptor(
        PluginDescriptor(
            name="llm-template",
            version="1.0.0",
            module="langharness_core.plugins.llm.llm",
            factory="llm-plugin-factory",
            specification=SPEC_LLM,
            description=(
                "Template for the LLM bundle. Implements agent.plugin.llm. "
                "Install to preload the LLM bundle for agent scopes."
            ),
        )
    )
    manager.install_descriptor(agent_loop_template_descriptor())
    manager.create_instance(
        "llm-plugin-factory",
        "langharness_core.plugins.llm.llm",
        scope,
        properties={
            "plugin.model.instance": StaticModel(),
            "plugin.agent_id": "a",
        },
    )
    loop_descriptor = agent_loop_descriptor()
    manager.create_instance(
        loop_descriptor.factory,
        loop_descriptor.module,
        scope,
        properties=agent_loop_properties(
            "a",
            [SPEC_LLM, SPEC_TOOL],
            visibility_filter=manager.scope_filter(scope),
        ),
    )


def loop_tool_names(manager: PluginManager) -> list[str]:
    loops = manager.get_services(SPEC_AGENT_LOOP)
    assert len(loops) == 1
    return [str(tool) for tool in loops[0].describe()["tools"]]


def management_discovery() -> PluginDiscovery:
    return PluginDiscovery(lambda: [EntryPoint(dynamic_package(), "dynamic-core")])


def test_management_tools_install_enable_disable_visibility() -> None:
    manager = make_manager()
    try:
        install_templates(manager)
        coordinator, _ = make_coordinator(manager, management_discovery())
        manager.register_runtime_service(DynamicPluginManager, coordinator)

        registration = coordinator.install(
            "dynamic.core",
            "management-tools-plugin-template",
            scope_id=ScopeId("agent"),
        )
        assert registration.factory == "management-tools-plugin-factory"
        # Installing creates an enabled instance immediately.
        assert MANAGEMENT_TOOLS <= set(tool_names(manager, "agent"))

        enabled = coordinator.set_enabled(
            "management-tools-plugin-template", True, scope_id=ScopeId("agent")
        )
        assert enabled.enabled is True
        assert MANAGEMENT_TOOLS <= set(tool_names(manager, "agent"))
        assert MANAGEMENT_TOOLS.isdisjoint(tool_names(manager, "ui"))

        disabled = coordinator.set_enabled(
            "management-tools-plugin-template", False, scope_id=ScopeId("agent")
        )
        assert disabled.enabled is False
        assert MANAGEMENT_TOOLS.isdisjoint(tool_names(manager, "agent"))
    finally:
        manager.stop()


def test_management_tools_agent_instance_and_module_guard() -> None:
    manager = make_manager()
    try:
        install_templates(manager)
        coordinator, _ = make_coordinator(manager, management_discovery())
        manager.register_runtime_service(DynamicPluginManager, coordinator)

        registration = coordinator.install(
            "dynamic.core",
            "management-tools-plugin-instance",
            scope_id=ScopeId("agent:a"),
        )
        assert registration.factory == "management-tools-plugin-factory"
        assert registration.scope_id == ScopeId("agent:a")

        # The same definition may serve multiple scopes: a second instance of
        # the same factory is allowed at the agent scope.
        template = coordinator.install(
            "dynamic.core",
            "management-tools-plugin-template",
            scope_id=ScopeId("agent"),
        )
        assert template.instance != registration.instance
        assert len(manager.list_instance()) == 2

        enabled = coordinator.set_enabled(
            "management-tools-plugin-template", True, scope_id=ScopeId("agent")
        )
        assert enabled.enabled is True
        assert MANAGEMENT_TOOLS <= set(tool_names(manager, "agent:a"))
        assert MANAGEMENT_TOOLS <= set(tool_names(manager, "agent"))
    finally:
        manager.stop()


def test_management_tools_reach_an_agent_loop_and_leave_on_disable() -> None:
    manager = make_loop_manager()
    try:
        install_templates(manager)
        materialize_loop(manager)
        coordinator, _ = make_coordinator(manager, management_discovery())
        manager.register_runtime_service(DynamicPluginManager, coordinator)

        assert MANAGEMENT_TOOLS.isdisjoint(loop_tool_names(manager))

        coordinator.install(
            "dynamic.core",
            "management-tools-plugin-template",
            scope_id=ScopeId("agent"),
        )
        assert MANAGEMENT_TOOLS <= set(loop_tool_names(manager))

        coordinator.set_enabled(
            "management-tools-plugin-template", False, scope_id=ScopeId("agent")
        )
        assert MANAGEMENT_TOOLS.isdisjoint(loop_tool_names(manager))
    finally:
        manager.stop()


def test_agent_tools_install_enable_disable_visibility() -> None:
    manager = make_manager()
    try:
        install_templates(manager)
        coordinator, _ = make_coordinator(
            manager,
            PluginDiscovery(lambda: [EntryPoint(agent_tools_package(), "agent-tools")]),
        )
        manager.register_runtime_service(DynamicPluginManager, coordinator)

        registration = coordinator.install(
            "agent.tools", "agent-operations", scope_id=ScopeId("agent")
        )
        assert registration.factory == "agent-operations-export-factory"
        assert AGENT_TOOLS <= set(tool_names(manager, "agent"))
        assert AGENT_TOOLS.isdisjoint(tool_names(manager, "ui"))

        disabled = coordinator.set_enabled(
            "agent-operations-export", False, scope_id=ScopeId("agent")
        )
        assert disabled.enabled is False
        assert AGENT_TOOLS.isdisjoint(tool_names(manager, "agent"))
    finally:
        manager.stop()

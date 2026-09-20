"""End-to-end tests that start the real Pelix/iPOPO framework."""
# mypy: ignore-errors
# pyright: reportOptionalMemberAccess=false

from __future__ import annotations

import asyncio
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from rich.console import Console

from langharness_api.contracts import (
    SPEC_API_SERVER,
    SPEC_AUTH,
    SPEC_DB,
    SPEC_RATE_LIMIT,
    SPEC_ROUTE,
)
from langharness_cli.plugins.rich_renderer import RichInteractiveRenderer
from langharness_core.contracts import (
    SPEC_AGENT_LOOP,
    SPEC_CACHE,
    SPEC_CHECKPOINTER,
    SPEC_CONTEXT_SCHEMA,
    SPEC_DEBUG,
    SPEC_INTERRUPT_AFTER,
    SPEC_INTERRUPT_BEFORE,
    SPEC_NAME,
    SPEC_RESPONSE_FORMAT,
    SPEC_STATE_SCHEMA,
    SPEC_STORE,
    SPEC_SYSTEM_PROMPT,
    SPEC_TRANSFORMERS,
    ToolProvider,
)
from langharness_plugin.plugin_manager import PluginManager
from langharness_plugin.registry import PluginDescriptor, PluginRegistry
from langharness_scope import ROOT_SCOPE_ID

DESCRIPTION = (
    "E2E test plugin. Implements a test specification. Properties vary per "
    "instance. Requires a restart for property changes. Uninstall when "
    "tests finish."
)

CAPTURED_MESSAGES: list[list] = []


class ScriptedToolCallModel(BaseChatModel):
    responses: list[AIMessage]
    i: int = 0

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        CAPTURED_MESSAGES.append([message for message in messages])
        response = self.responses[self.i]
        self.i = (self.i + 1) % len(self.responses)
        return ChatResult(generations=[ChatGeneration(message=response)])

    @property
    def _llm_type(self) -> str:
        return "scripted-tool-call-model"

    def bind_tools(self, tools, **kwargs):
        return self


def scripted_tool_call_model() -> ScriptedToolCallModel:
    return ScriptedToolCallModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "add",
                        "args": {"a": 2, "b": 3},
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="The answer is 5."),
        ]
    )


def plugin(
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


def assemble(manager: PluginManager) -> None:
    """Install the core definitions and create the base instances."""
    model = scripted_tool_call_model()
    installs = [
        (
            plugin(
                "llm",
                "langharness_core.plugins.llm.llm",
                "llm-plugin-factory",
                "agent.plugin.llm",
            ),
            ROOT_SCOPE_ID,
            {"plugin.model.instance": model},
            100,
        ),
        (
            plugin(
                "tools",
                "langharness_core.plugins.tools.tools",
                "tools-plugin-factory",
                "agent.plugin.tools",
            ),
            ROOT_SCOPE_ID,
            {},
            0,
        ),
        (
            plugin(
                "middleware",
                "langharness_core.plugins.middleware.template_middleware",
                "middleware-plugin-factory",
                "agent.plugin.middleware",
            ),
            ROOT_SCOPE_ID,
            {},
            0,
        ),
        (
            plugin(
                "system-prompt",
                "langharness_core.plugins.system_prompt.template_system_prompt",
                "system-prompt-plugin-factory",
                SPEC_SYSTEM_PROMPT,
            ),
            ROOT_SCOPE_ID,
            {"plugin.system_prompt": "You are A."},
            0,
        ),
        (
            plugin(
                "agent-loop",
                "langharness_core.plugins.loop.agent_loop",
                "agent-loop-factory",
                SPEC_AGENT_LOOP,
            ),
            ROOT_SCOPE_ID,
            {},
            0,
        ),
    ]
    for descriptor, _, _, _ in installs:
        manager.install_descriptor(descriptor)
    for descriptor, scope, properties, ranking in installs:
        manager.create_instance(
            descriptor.factory, descriptor.module, scope,
            properties=properties, ranking=ranking,
        )


def assemble_manager() -> PluginManager:
    manager = PluginManager(PluginRegistry())
    manager.start()
    assemble(manager)
    return manager


def test_plugin_lifecycle_and_agent_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    CAPTURED_MESSAGES.clear()
    manager = assemble_manager()
    try:
        loop = manager.get_service(SPEC_AGENT_LOOP)
        assert loop.describe()["tools"] == ["add"]

        result = loop.invoke("What is 2 + 3?")
        assert result["messages"][-1].content == "The answer is 5."
        assert isinstance(CAPTURED_MESSAGES[0][0], SystemMessage)
        assert CAPTURED_MESSAGES[0][0].content == "You are A."

        llm_props = manager.service_properties("agent.plugin.llm")
        assert llm_props[0]["plugin.version"] == "1.0.0"
        assert manager.get_service("agent.plugin.llm").get_plugin_info()["version"] == "1.0.0"

        tools_instance = manager.list_instance(factory="tools-plugin-factory")[0]
        manager.update_instance(tools_instance.instance, enabled=False)
        assert loop.describe()["tools"] == []

        manager.update_instance(tools_instance.instance, enabled=True)
        assert loop.describe()["tools"] == ["add"]

        middleware_factory = "middleware-plugin-factory"
        manager.delete_instance(
            manager.list_instance(factory=middleware_factory)[0].instance
        )
        manager.uninstall_plugin(middleware_factory)
        assert {item.descriptor.factory for item in manager.list_plugin()} == {
            "llm-plugin-factory",
            "tools-plugin-factory",
            "agent-loop-factory",
            "system-prompt-plugin-factory",
        }

        workspace = plugin(
            "workspace-tools",
            "langharness_core.plugins.tools.workspace",
            "workspace-tools-plugin-factory",
            "agent.plugin.tools",
        )
        model = manager.get_service("agent.plugin.llm").get_model()
        original_responses = model.responses
        manager.install_descriptor(workspace)
        manager.create_instance(
            workspace.factory, workspace.module, ROOT_SCOPE_ID,
            properties={"plugin.tools.root_dir": str(tmp_path)},
        )
        try:
            model.responses = [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_file",
                            "args": {
                                "file_path": "tool-proof.txt",
                                "text": "file-tool-ok",
                            },
                            "id": "write-call",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "read_file",
                            "args": {"file_path": "tool-proof.txt"},
                            "id": "read-call",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "bash",
                            "args": {"commands": "printf bash-tool-ok"},
                            "id": "bash-call",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="tools completed"),
            ]
            model.i = 0

            tool_result = loop.invoke("Exercise the workspace tools")
            tool_messages = [
                message
                for message in tool_result["messages"]
                if isinstance(message, ToolMessage)
            ]
            assert (tmp_path / "tool-proof.txt").read_text() == "file-tool-ok"
            assert [message.name for message in tool_messages] == [
                "write_file",
                "read_file",
                "bash",
            ]
            assert "file-tool-ok" in str(tool_messages[1].content)
            assert "bash-tool-ok" in str(tool_messages[2].content)
        finally:
            model.responses = original_responses
            model.i = 0
            manager.delete_instance(
                manager.list_instance(factory="workspace-tools-plugin-factory")[0].instance
            )
            manager.uninstall_plugin("workspace-tools-plugin-factory")

        async def verify_sqlite_memory() -> None:
            sqlite_path = tmp_path / "checkpoints.sqlite3"
            checkpointer = plugin(
                "sqlite-checkpointer",
                "langharness_core.plugins.checkpointer.sqlite",
                "sqlite-checkpointer-plugin-factory",
                SPEC_CHECKPOINTER,
            )
            manager.install_descriptor(checkpointer)
            manager.create_instance(
                checkpointer.factory, checkpointer.module, ROOT_SCOPE_ID,
                properties={"plugin.checkpoint.path": str(sqlite_path)},
            )
            try:
                first_start = len(CAPTURED_MESSAGES)
                _ = [
                    chunk
                    async for chunk in loop.astream(
                        "Remember sqlite-memory", thread_id="memory-thread"
                    )
                ]
                second_start = len(CAPTURED_MESSAGES)
                _ = [
                    chunk
                    async for chunk in loop.astream(
                        "What should you remember?", thread_id="memory-thread"
                    )
                ]

                first_messages = CAPTURED_MESSAGES[first_start]
                second_messages = CAPTURED_MESSAGES[second_start]
                assert len(second_messages) > len(first_messages)
                assert any(
                    getattr(message, "content", "") == "Remember sqlite-memory"
                    for message in second_messages
                )
                assert sqlite_path.exists()
            finally:
                manager.delete_instance(
                    manager.list_instance(
                        factory="sqlite-checkpointer-plugin-factory"
                    )[0].instance
                )
                manager.uninstall_plugin("sqlite-checkpointer-plugin-factory")
                await asyncio.sleep(0)

        asyncio.run(verify_sqlite_memory())

        captured_kwargs: dict[str, object] = {}

        def fake_create_agent(model, tools=None, *, system_prompt=None, **kwargs):
            captured_kwargs["model"] = model
            captured_kwargs["tools"] = tools
            captured_kwargs["system_prompt"] = system_prompt
            captured_kwargs.update(kwargs)
            return object()

        # Pelix drops a bundle's module from sys.modules on uninstall and
        # re-executes it when another framework installs it again, so patch the
        # module of the class actually in use instead of the imported one.
        monkeypatch.setitem(
            loop._rebuild.__globals__, "create_agent", fake_create_agent
        )

        response_format = object()
        state_schema = object()
        context_schema = object()
        checkpointer = object()
        store = object()
        cache = object()

        parameter_installs = [
            (
                plugin(
                    "response-format",
                    "langharness_core.plugins.response_format.template_response_format",
                    "response-format-plugin-factory",
                    SPEC_RESPONSE_FORMAT,
                ),
                {"plugin.response_format": response_format},
            ),
            (
                plugin(
                    "state-schema",
                    "langharness_core.plugins.state_schema.template_state_schema",
                    "state-schema-plugin-factory",
                    SPEC_STATE_SCHEMA,
                ),
                {"plugin.state_schema": state_schema},
            ),
            (
                plugin(
                    "context-schema",
                    "langharness_core.plugins.context_schema.template_context_schema",
                    "context-schema-plugin-factory",
                    SPEC_CONTEXT_SCHEMA,
                ),
                {"plugin.context_schema": context_schema},
            ),
            (
                plugin(
                    "checkpointer",
                    "langharness_core.plugins.checkpointer.template_checkpointer",
                    "checkpointer-plugin-factory",
                    SPEC_CHECKPOINTER,
                ),
                {"plugin.checkpointer": checkpointer},
            ),
            (
                plugin(
                    "store",
                    "langharness_core.plugins.store.template_store",
                    "store-plugin-factory",
                    SPEC_STORE,
                ),
                {"plugin.store": store},
            ),
            (
                plugin(
                    "interrupt-before",
                    "langharness_core.plugins.interrupt_before.template_interrupt_before",
                    "interrupt-before-plugin-factory",
                    SPEC_INTERRUPT_BEFORE,
                ),
                {"plugin.interrupt_before": ["before_a", "before_b"]},
            ),
            (
                plugin(
                    "interrupt-after",
                    "langharness_core.plugins.interrupt_after.template_interrupt_after",
                    "interrupt-after-plugin-factory",
                    SPEC_INTERRUPT_AFTER,
                ),
                {"plugin.interrupt_after": ["after_a"]},
            ),
            (
                plugin(
                    "debug",
                    "langharness_core.plugins.debug.template_debug",
                    "debug-plugin-factory",
                    SPEC_DEBUG,
                ),
                {"plugin.debug": True},
            ),
            (
                plugin(
                    "agent-name",
                    "langharness_core.plugins.name.template_name",
                    "agent-name-plugin-factory",
                    SPEC_NAME,
                ),
                {"plugin.agent_name": "my-agent"},
            ),
            (
                plugin(
                    "cache",
                    "langharness_core.plugins.cache.template_cache",
                    "cache-plugin-factory",
                    SPEC_CACHE,
                ),
                {"plugin.cache": cache},
            ),
            (
                plugin(
                    "transformers",
                    "langharness_core.plugins.transformers.template_transformers",
                    "transformers-plugin-factory",
                    SPEC_TRANSFORMERS,
                ),
                {"plugin.transformers": ["transformer_a"]},
            ),
        ]

        for descriptor, properties in parameter_installs:
            manager.install_descriptor(descriptor)
            manager.create_instance(
                descriptor.factory, descriptor.module, ROOT_SCOPE_ID,
                properties=properties,
            )

        # With a checkpointer the graph builds on the API event loop.
        async def rebuild_on_loop() -> None:
            loop._rebuild()

        asyncio.run(rebuild_on_loop())

        assert captured_kwargs["response_format"] is response_format
        assert captured_kwargs["state_schema"] is state_schema
        assert captured_kwargs["context_schema"] is context_schema
        assert captured_kwargs["checkpointer"] is checkpointer
        assert captured_kwargs["store"] is store
        assert captured_kwargs["interrupt_before"] == ["before_a", "before_b"]
        assert captured_kwargs["interrupt_after"] == ["after_a"]
        assert captured_kwargs["debug"] is True
        assert captured_kwargs["name"] == "my-agent"
        assert captured_kwargs["cache"] is cache
        assert captured_kwargs["transformers"] == ["transformer_a"]

        api_installs = [
            (
                plugin(
                    "api-auth",
                    "langharness_api.plugins.auth.auth",
                    "api-auth-plugin-factory",
                    SPEC_AUTH,
                ),
                {"plugin.token": "secret"},
            ),
            (
                plugin(
                    "api-rate-limit",
                    "langharness_api.plugins.rate_limit.rate_limit",
                    "api-rate-limit-plugin-factory",
                    SPEC_RATE_LIMIT,
                ),
                {"plugin.limit": 3},
            ),
            (
                plugin(
                    "api-db",
                    "langharness_api.plugins.db.db",
                    "api-db-plugin-factory",
                    SPEC_DB,
                ),
                {},
            ),
            (
                plugin(
                    "api-health",
                    "langharness_api.plugins.routes.health",
                    "api-health-plugin-factory",
                    SPEC_ROUTE,
                ),
                {},
            ),
            (
                plugin(
                    "api-server",
                    "langharness_api.plugins.server.app",
                    "api-server-factory",
                    SPEC_API_SERVER,
                ),
                {},
            ),
        ]

        for descriptor, properties in api_installs:
            manager.install_descriptor(descriptor)
            manager.create_instance(
                descriptor.factory, descriptor.module, ROOT_SCOPE_ID,
                properties=properties,
            )

        api_server = manager.get_service(SPEC_API_SERVER)
        app = api_server.build_app()
        client = TestClient(app)

        assert client.get("/health").status_code == 401
        response = client.get(
            "/health", headers={"Authorization": "Bearer secret"}
        )
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "db": True}

        client.get("/health", headers={"Authorization": "Bearer secret"})
        client.get("/health", headers={"Authorization": "Bearer secret"})
        too_many = client.get(
            "/health", headers={"Authorization": "Bearer secret"}
        )
        assert too_many.status_code == 429

        manager.get_service(SPEC_RATE_LIMIT)._hits.clear()

        echo = plugin(
            "api-echo",
            "langharness_api.plugins.routes.echo",
            "api-echo-plugin-factory",
            SPEC_ROUTE,
        )
        manager.install_descriptor(echo)
        manager.create_instance(echo.factory, echo.module, ROOT_SCOPE_ID)
        app = api_server.build_app()
        dynamic_client = TestClient(app)
        assert dynamic_client.get(
            "/echo", headers={"Authorization": "Bearer secret"}
        ).status_code == 200

        manager.delete_instance(
            manager.list_instance(factory="api-echo-plugin-factory")[0].instance
        )
        manager.uninstall_plugin("api-echo-plugin-factory")
        app = api_server.build_app()
        after_remove_client = TestClient(app)
        assert after_remove_client.get(
            "/echo", headers={"Authorization": "Bearer secret"}
        ).status_code == 404
    finally:
        manager.stop()


def test_real_plugin_agent_stream_renders_response_once(tmp_path: Path) -> None:
    manager = assemble_manager()
    try:
        loop = manager.get_service(SPEC_AGENT_LOOP)

        async def collect():
            return [event async for event in loop.astream("What is 2 + 3?")]

        events = asyncio.run(collect())
        output = StringIO()
        renderer = RichInteractiveRenderer()
        renderer.console = Console(file=output, force_terminal=False, width=100)
        renderer.start_response()
        for event in events:
            renderer.render_event(event)
        renderer.finish_response()

        assert output.getvalue().count("The answer is 5.") == 1
    finally:
        manager.stop()


@pytest.mark.parametrize("protocol", ["chat", "anthropic", "responses"])
def test_agent_loop_invocation_is_unchanged_for_all_protocols(
    tmp_path: Path, protocol: str
) -> None:
    manager = PluginManager(PluginRegistry())
    manager.start()
    try:
        model = scripted_tool_call_model()
        llm = plugin(
            "llm",
            "langharness_core.plugins.llm.llm",
            "llm-plugin-factory",
            "agent.plugin.llm",
        )
        tools = plugin(
            "tools",
            "langharness_core.plugins.tools.tools",
            "tools-plugin-factory",
            "agent.plugin.tools",
        )
        loop_desc = plugin(
            "agent-loop",
            "langharness_core.plugins.loop.agent_loop",
            "agent-loop-factory",
            SPEC_AGENT_LOOP,
        )
        for descriptor in (llm, tools, loop_desc):
            manager.install_descriptor(descriptor)
        manager.create_instance(
            llm.factory, llm.module, ROOT_SCOPE_ID,
            properties={
                "plugin.model.instance": model,
                "plugin.model.protocol": protocol,
            },
        )
        manager.create_instance(tools.factory, tools.module, ROOT_SCOPE_ID)
        manager.create_instance(loop_desc.factory, loop_desc.module, ROOT_SCOPE_ID)

        loop = manager.get_service(SPEC_AGENT_LOOP)
        result = loop.invoke("What is 2 + 3?")

        assert manager.get_service("agent.plugin.llm").get_protocol() == protocol
        assert result["messages"][-1].content == "The answer is 5."
    finally:
        manager.stop()


def test_raw_registered_bad_provider_is_quarantined(tmp_path: Path) -> None:
    manager = assemble_manager()
    try:
        loop = manager.get_service(SPEC_AGENT_LOOP)
        assert loop.describe()["tools"] == ["add"]

        class RawBadToolProvider:
            def get_tools(self, root: str) -> list[Any]:
                return []

            def get_plugin_info(self) -> dict[str, str]:
                return {"name": "raw-bad"}

        manager._context.register_service(
            ToolProvider, RawBadToolProvider(), {"plugin.scope_id": "root"}
        )

        assert manager.get_service("agent.plugin.tools") is not None
        assert loop.describe()["tools"] == ["add"]
        assert len(loop._guards["_tool_providers"].rejected()) == 1
    finally:
        manager.stop()


def test_all_agent_loop_guards_quarantine_raw_bad_services(tmp_path: Path) -> None:
    manager = assemble_manager()
    try:
        loop = manager.get_service(SPEC_AGENT_LOOP)

        fields = [field for field in loop._guards if field != "_llm_provider"]
        fields.append("_llm_provider")  # A rejected required LLM invalidates the loop; run last.
        for index, field in enumerate(fields):
            guard = loop._guards[field]
            bad_service = object()
            manager._context.register_service(
                guard.protocol,
                bad_service,
                {
                    "service.ranking": 10_000 + index,
                    "plugin.scope_id": "root",
                },
            )
            assert id(bad_service) in guard.rejected(), field
    finally:
        manager.stop()


def test_all_api_and_config_guards_quarantine_raw_bad_services(tmp_path: Path) -> None:
    manager = assemble_manager()
    try:
        extra_installs = [
            (
                plugin(
                    "configs",
                    "langharness_config.plugins.configs",
                    "configs-plugin-factory",
                    "configs",
                ),
                {},
            ),
            (
                plugin(
                    "api-stream",
                    "langharness_api.plugins.routes.stream",
                    "api-stream-route-factory",
                    SPEC_ROUTE,
                ),
                {},
            ),
            (
                plugin(
                    "api-server",
                    "langharness_api.plugins.server.app",
                    "api-server-factory",
                    SPEC_API_SERVER,
                ),
                {},
            ),
        ]
        for descriptor, properties in extra_installs:
            manager.install_descriptor(descriptor)
            manager.create_instance(
                descriptor.factory, descriptor.module, ROOT_SCOPE_ID,
                properties=properties,
            )

        api_server = manager.get_service(SPEC_API_SERVER)
        stream_route = next(
            provider
            for provider in manager.get_services(SPEC_ROUTE)
            if hasattr(provider, "get_plugin_info")
            and provider.get_plugin_info()["name"] == "stream"
        )
        configs = manager.get_service("configs")

        for field, guard in api_server._guards.items():
            bad_service = object()
            registration = manager._context.register_service(
                guard.protocol,
                bad_service,
                {"service.ranking": 10_000},
            )
            assert id(bad_service) in guard.rejected(), field
            value = getattr(api_server, field)
            assert bad_service is not value
            assert not isinstance(value, list) or all(
                item is not bad_service for item in value
            )
            registration.unregister()

        for field, guard in stream_route._guards.items():
            bad_service = object()
            registration = manager._context.register_service(
                guard.protocol,
                bad_service,
                {"service.ranking": 10_000},
            )
            assert id(bad_service) in guard.rejected(), field
            assert getattr(stream_route, field) is not bad_service
            registration.unregister()

        bad_config_provider = object()
        registration = manager._context.register_service(
            configs._guard.protocol,
            bad_config_provider,
            {},
        )
        assert id(bad_config_provider) in configs._guard.rejected()
        assert all(item is not bad_config_provider for item in configs._providers)
        registration.unregister()
    finally:
        manager.stop()

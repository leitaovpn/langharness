"""Public service specifications shared by all plugins."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Literal, Protocol, runtime_checkable

from langchain_core.language_models.chat_models import BaseChatModel

from langharness_plugin.validation import service_contract

SPEC_LLM = "agent.plugin.llm"
SPEC_TOOL = "agent.plugin.tools"
SPEC_MIDDLEWARE = "agent.plugin.middleware"
SPEC_SYSTEM_PROMPT = "agent.plugin.system_prompt"
SPEC_RESPONSE_FORMAT = "agent.plugin.response_format"
SPEC_STATE_SCHEMA = "agent.plugin.state_schema"
SPEC_CONTEXT_SCHEMA = "agent.plugin.context_schema"
SPEC_CHECKPOINTER = "agent.plugin.checkpointer"
SPEC_STORE = "agent.plugin.store"
SPEC_INTERRUPT_BEFORE = "agent.plugin.interrupt_before"
SPEC_INTERRUPT_AFTER = "agent.plugin.interrupt_after"
SPEC_DEBUG = "agent.plugin.debug"
SPEC_NAME = "agent.plugin.name"
SPEC_CACHE = "agent.plugin.cache"
SPEC_TRANSFORMERS = "agent.plugin.transformers"
SPEC_AGENT_LOOP = "agent.loop"
SPEC_SESSION_INDEX = "session.index"
SPEC_AGENT_REGISTRY = "agent.registry"
SPEC_AGENT_DIRECTORY = "agent.directory"
SPEC_AGENT_SERVER = "agent.server"
ModelProtocol = Literal["anthropic", "chat", "responses"]

ALL_PLUGIN_SPECS = (
    SPEC_LLM,
    SPEC_TOOL,
    SPEC_MIDDLEWARE,
    SPEC_SYSTEM_PROMPT,
    SPEC_RESPONSE_FORMAT,
    SPEC_STATE_SCHEMA,
    SPEC_CONTEXT_SCHEMA,
    SPEC_CHECKPOINTER,
    SPEC_STORE,
    SPEC_INTERRUPT_BEFORE,
    SPEC_INTERRUPT_AFTER,
    SPEC_DEBUG,
    SPEC_NAME,
    SPEC_CACHE,
    SPEC_TRANSFORMERS,
    SPEC_AGENT_LOOP,
    SPEC_SESSION_INDEX,
    SPEC_AGENT_REGISTRY,
    SPEC_AGENT_DIRECTORY,
)


@service_contract(SPEC_LLM)
@runtime_checkable
class LLMProvider(Protocol):
    """Contract implemented by every ``agent.plugin.llm`` service."""

    def get_model(self) -> BaseChatModel: ...

    def get_protocol(self) -> ModelProtocol: ...

    def get_plugin_info(self) -> dict[str, str]: ...


@service_contract(SPEC_TOOL)
@runtime_checkable
class ToolProvider(Protocol):
    """Contract implemented by every ``agent.plugin.tools`` service."""

    def get_tools(self) -> list[Any]: ...

    def get_tool_presentations(self) -> dict[str, str]: ...

    def get_plugin_info(self) -> dict[str, str]: ...


@service_contract(SPEC_MIDDLEWARE)
@runtime_checkable
class MiddlewareProvider(Protocol):
    """Contract implemented by every ``agent.plugin.middleware`` service."""

    def get_middlewares(self) -> list[Any]: ...

    def get_plugin_info(self) -> dict[str, str]: ...


@service_contract(SPEC_SYSTEM_PROMPT)
@runtime_checkable
class SystemPromptProvider(Protocol):
    """Contract implemented by every ``agent.plugin.system_prompt`` service."""

    def get_system_prompt(self) -> str: ...

    def get_plugin_info(self) -> dict[str, str]: ...


@service_contract(SPEC_RESPONSE_FORMAT)
@runtime_checkable
class ResponseFormatProvider(Protocol):
    def get_response_format(self) -> Any: ...


@service_contract(SPEC_STATE_SCHEMA)
@runtime_checkable
class StateSchemaProvider(Protocol):
    def get_state_schema(self) -> Any: ...


@service_contract(SPEC_CONTEXT_SCHEMA)
@runtime_checkable
class ContextSchemaProvider(Protocol):
    def get_context_schema(self) -> Any: ...


@service_contract(SPEC_CHECKPOINTER)
@runtime_checkable
class CheckpointerProvider(Protocol):
    def get_checkpointer(self) -> Any: ...


@service_contract(SPEC_STORE)
@runtime_checkable
class StoreProvider(Protocol):
    def get_store(self) -> Any: ...


@service_contract(SPEC_INTERRUPT_BEFORE)
@runtime_checkable
class InterruptBeforeProvider(Protocol):
    def get_interrupt_before(self) -> list[str]: ...


@service_contract(SPEC_INTERRUPT_AFTER)
@runtime_checkable
class InterruptAfterProvider(Protocol):
    def get_interrupt_after(self) -> list[str]: ...


@service_contract(SPEC_DEBUG)
@runtime_checkable
class DebugProvider(Protocol):
    def get_debug(self) -> bool: ...


@service_contract(SPEC_NAME)
@runtime_checkable
class NameProvider(Protocol):
    def get_name(self) -> str | None: ...


@service_contract(SPEC_CACHE)
@runtime_checkable
class CacheProvider(Protocol):
    def get_cache(self) -> Any: ...


@service_contract(SPEC_TRANSFORMERS)
@runtime_checkable
class TransformersProvider(Protocol):
    def get_transformers(self) -> list[Any]: ...


@service_contract(SPEC_AGENT_LOOP)
@runtime_checkable
class AgentLoopProvider(Protocol):
    """Contract implemented by every ``agent.loop`` service."""

    def invoke(self, message: str, *, thread_id: str | None = None) -> Any: ...

    def astream(
        self, message: str, *, thread_id: str | None = None
    ) -> AsyncIterator[dict[str, Any]]: ...

    def describe(self) -> dict[str, Any]: ...


@service_contract(SPEC_SESSION_INDEX)
@runtime_checkable
class SessionIndexProvider(Protocol):
    """Contract implemented by every ``session.index`` service."""

    async def new_session(self, user_id: str) -> str: ...

    async def touch(self, user_id: str, session_id: str, agent_id: str) -> None: ...

    async def get_session(
        self, user_id: str, session_id: str
    ) -> dict[str, Any] | None: ...

    async def list_sessions(
        self, user_id: str, limit: int = 50
    ) -> list[dict[str, Any]]: ...


@service_contract(SPEC_AGENT_REGISTRY)
@runtime_checkable
class AgentRegistryProvider(Protocol):
    """Contract implemented by every ``agent.registry`` service."""

    def list_agents(self) -> list[dict[str, Any]]: ...

    def get_agent(self, agent_id: str) -> dict[str, Any] | None: ...

    def create_agent(
        self, agent_id: str, name: str, description: str
    ) -> dict[str, Any]: ...

    def update_agent(
        self,
        agent_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]: ...

    def delete_agent(self, agent_id: str) -> None: ...


@service_contract(SPEC_AGENT_DIRECTORY)
@runtime_checkable
class AgentDirectoryProvider(Protocol):
    """Contract implemented by every ``agent.directory`` service."""

    def list_agents(self) -> list[dict[str, Any]]: ...

    def get_loop(self, agent_id: str) -> Any | None: ...

    def ensure_plugin_instance(
        self, agent_id: str, plugin: str, properties: dict[str, Any]
    ) -> None: ...

    def binding_properties(self, agent_id: str, plugin: str) -> dict[str, Any]: ...

    def apply_agent_config(
        self, agent_id: str, plugins: dict[str, Any]
    ) -> None: ...

    def remove_agent(self, agent_id: str) -> None: ...

    def reload(self, agent_id: str | None = None) -> None: ...


@service_contract(SPEC_AGENT_SERVER)
@runtime_checkable
class AgentServerProvider(Protocol):
    def list_agents(self) -> list[dict[str, Any]]: ...

    def get_loop(self, agent_id: str) -> Any | None: ...

    def reload(self, agent_id: str | None = None) -> None: ...

    def replace_loop_package(self, package_id: str, contribution_id: str) -> None: ...

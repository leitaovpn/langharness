"""Agent loop component assembled from injected plugin services."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from pelix.ipopo.decorators import (
    BindField,
    ComponentFactory,
    HiddenProperty,
    Invalidate,
    Provides,
    Requires,
    RequiresBest,
    UnbindField,
    Validate,
)

from langharness_core.contracts import (
    AgentLoopProvider,
    CacheProvider,
    CheckpointerProvider,
    ContextSchemaProvider,
    DebugProvider,
    InterruptAfterProvider,
    InterruptBeforeProvider,
    LLMProvider,
    MiddlewareProvider,
    NameProvider,
    ResponseFormatProvider,
    StateSchemaProvider,
    StoreProvider,
    SystemPromptProvider,
    ToolProvider,
    TransformersProvider,
)
from langharness_plugin.scope_policy import (
    resolve_scoped_aggregate,
    resolve_scoped_best,
)
from langharness_plugin.scoped_dependencies import ScopedDependencies
from langharness_plugin.validation import ContractGuard

LOGGER = logging.getLogger("langharness.agent")


def _extract_usage(message: Any) -> dict[str, int] | None:
    """Return token usage carried by a streamed message chunk, if any."""
    usage = getattr(message, "usage_metadata", None)
    if not usage:
        usage = (getattr(message, "response_metadata", None) or {}).get("token_usage")
    if not usage:
        return None
    extracted = {
        "input_tokens": int(usage.get("input_tokens", 0)),
        "output_tokens": int(usage.get("output_tokens", 0)),
        "total_tokens": int(usage.get("total_tokens", 0)),
    }
    return extracted if sum(extracted.values()) else None


def _extract_content(message: Any) -> str:
    """Normalize plain and structured LangChain message content to text."""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") in {"text", "output_text"}
        )
    return str(content) if content else ""


def _strip_orphan_tool_use(message: Any) -> None:
    """Drop streamed tool_use fragments that never became completable tool calls.

    Anthropic-style streaming keeps every partial ``tool_use`` block in
    ``content`` while only completed calls are parsed into ``tool_calls``.
    Replaying an orphan makes the provider reject the next request because
    every ``tool_use`` id needs a matching ``tool_result``.
    """
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return
    completed_ids = {
        tool_call.get("id") for tool_call in getattr(message, "tool_calls", None) or []
    }
    message.content = [
        block
        for block in content
        if not (
            isinstance(block, dict)
            and block.get("type") == "tool_use"
            and block.get("id") not in completed_ids
        )
    ]


def _iter_interrupts(container: Any) -> Iterator[Any]:
    """Yield the value payload of every interrupt carried by a stream chunk.

    The ``__interrupt__`` entry holds a tuple of ``Interrupt`` objects; its
    value field carries the human-facing request payload.
    """
    if not isinstance(container, dict):
        return
    for interrupt in container.get("__interrupt__", ()):
        yield getattr(interrupt, "value", interrupt)


@ComponentFactory("agent-loop-factory")
@Provides(AgentLoopProvider)
@HiddenProperty("_scope_chain", "plugin.scope_chain", None)
@RequiresBest("_llm_provider", LLMProvider, optional=False, immediate_rebind=True)
@Requires("_scoped_llm_providers", LLMProvider, aggregate=True, optional=True)
@Requires("_tool_providers", ToolProvider, aggregate=True, optional=True)
@Requires("_middleware_providers", MiddlewareProvider, aggregate=True, optional=True)
@Requires("_system_prompt_providers", SystemPromptProvider, aggregate=True, optional=True)
@RequiresBest(
    "_response_format_provider",
    ResponseFormatProvider,
    optional=True,
    immediate_rebind=True,
)
@RequiresBest(
    "_state_schema_provider",
    StateSchemaProvider,
    optional=True,
    immediate_rebind=True,
)
@RequiresBest(
    "_context_schema_provider",
    ContextSchemaProvider,
    optional=True,
    immediate_rebind=True,
)
@RequiresBest(
    "_checkpointer_provider",
    CheckpointerProvider,
    optional=True,
    immediate_rebind=True,
)
@RequiresBest("_store_provider", StoreProvider, optional=True, immediate_rebind=True)
@Requires(
    "_interrupt_before_providers",
    InterruptBeforeProvider,
    aggregate=True,
    optional=True,
)
@Requires(
    "_interrupt_after_providers",
    InterruptAfterProvider,
    aggregate=True,
    optional=True,
)
@RequiresBest("_debug_provider", DebugProvider, optional=True, immediate_rebind=True)
@RequiresBest("_name_provider", NameProvider, optional=True, immediate_rebind=True)
@RequiresBest("_cache_provider", CacheProvider, optional=True, immediate_rebind=True)
@Requires(
    "_transformers_providers",
    TransformersProvider,
    aggregate=True,
    optional=True,
)
@ScopedDependencies(
    "_llm_provider",
    "_scoped_llm_providers",
    "_tool_providers",
    "_middleware_providers",
    "_system_prompt_providers",
    "_name_provider",
    "_response_format_provider",
    "_cache_provider",
    "_transformers_providers",
    "_interrupt_before_providers",
    "_interrupt_after_providers",
)
class PluginAgentLoop:
    """Rebuilds a LangChain agent graph when injected services change."""

    def __init__(self) -> None:
        self._scope_chain: list[str] = []
        self._service_metadata: dict[int, tuple[str, str, int]] = {}
        self._llm_provider: Any = None
        self._scoped_llm_providers: list[Any] = []
        self._tool_providers: list[Any] = []
        self._middleware_providers: list[Any] = []
        self._system_prompt_providers: list[Any] = []
        self._response_format_provider: Any = None
        self._state_schema_provider: Any = None
        self._context_schema_provider: Any = None
        self._checkpointer_provider: Any = None
        self._store_provider: Any = None
        self._interrupt_before_providers: list[Any] = []
        self._interrupt_after_providers: list[Any] = []
        self._debug_provider: Any = None
        self._name_provider: Any = None
        self._cache_provider: Any = None
        self._transformers_providers: list[Any] = []
        self._guards: dict[str, ContractGuard] = {
            "_llm_provider": ContractGuard(self, "_llm_provider", LLMProvider),
            "_scoped_llm_providers": ContractGuard(
                self, "_scoped_llm_providers", LLMProvider
            ),
            "_tool_providers": ContractGuard(self, "_tool_providers", ToolProvider),
            "_middleware_providers": ContractGuard(
                self, "_middleware_providers", MiddlewareProvider
            ),
            "_system_prompt_providers": ContractGuard(
                self, "_system_prompt_providers", SystemPromptProvider
            ),
            "_response_format_provider": ContractGuard(
                self, "_response_format_provider", ResponseFormatProvider
            ),
            "_state_schema_provider": ContractGuard(
                self, "_state_schema_provider", StateSchemaProvider
            ),
            "_context_schema_provider": ContractGuard(
                self, "_context_schema_provider", ContextSchemaProvider
            ),
            "_checkpointer_provider": ContractGuard(
                self, "_checkpointer_provider", CheckpointerProvider
            ),
            "_store_provider": ContractGuard(self, "_store_provider", StoreProvider),
            "_interrupt_before_providers": ContractGuard(
                self, "_interrupt_before_providers", InterruptBeforeProvider
            ),
            "_interrupt_after_providers": ContractGuard(
                self, "_interrupt_after_providers", InterruptAfterProvider
            ),
            "_debug_provider": ContractGuard(self, "_debug_provider", DebugProvider),
            "_name_provider": ContractGuard(self, "_name_provider", NameProvider),
            "_cache_provider": ContractGuard(self, "_cache_provider", CacheProvider),
            "_transformers_providers": ContractGuard(
                self, "_transformers_providers", TransformersProvider
            ),
        }
        self._graph: Any = None
        self._excluded_services: set[int] = set()

    @contextmanager
    def _excluding_service(self, service: Any) -> Iterator[None]:
        self._excluded_services.add(id(service))
        try:
            yield
        finally:
            self._excluded_services.discard(id(service))

    def _without_excluded(self, providers: list[Any]) -> list[Any]:
        if not self._excluded_services:
            return list(providers or [])
        return [
            provider
            for provider in providers or []
            if id(provider) not in self._excluded_services
        ]

    def _remember_service(self, service: Any, reference: Any) -> None:
        if reference is None or not hasattr(reference, "get_property"):
            return
        scope_id = reference.get_property("plugin.scope_id")
        plugin_key = reference.get_property("plugin.key")
        if scope_id is None or plugin_key is None:
            return
        ranking = reference.get_property("plugin.ranking") or 0
        self._service_metadata[id(service)] = (
            str(scope_id),
            str(plugin_key),
            int(ranking),
        )

    def _forget_service(self, service: Any) -> None:
        self._service_metadata.pop(id(service), None)

    def _effective(self, providers: list[Any]) -> list[Any]:
        return resolve_scoped_aggregate(
            self._scope_chain or [],
            self._without_excluded(providers),
            self._service_metadata,
        )

    def _resolve_scoped_llm(self) -> None:
        selected = resolve_scoped_best(
            self._scope_chain or [],
            self._without_excluded(self._scoped_llm_providers),
            self._service_metadata,
        )
        if selected is not None:
            self._llm_provider = selected

    @Validate
    def _validate(self, bundle_context: Any) -> None:
        self._resolve_scoped_llm()
        self._rebuild()

    @Invalidate
    def _invalidate(self, bundle_context: Any) -> None:
        self._graph = None

    @BindField("_llm_provider", if_valid=True)
    def _on_llm_bind(self, field: str, service: Any, reference: Any) -> None:
        if not self._guards[field].admit(service):
            self._graph = None
            return
        self._rebuild()

    @UnbindField("_llm_provider", if_valid=True)
    def _on_llm_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)
        self._graph = None

    @BindField("_scoped_llm_providers", if_valid=True)
    def _on_scoped_llm_bind(self, field: str, service: Any, reference: Any) -> None:
        if not self._guards[field].admit(service):
            return
        self._remember_service(service, reference)
        self._resolve_scoped_llm()
        self._rebuild()

    @UnbindField("_scoped_llm_providers", if_valid=True)
    def _on_scoped_llm_unbind(
        self, field: str, service: Any, reference: Any
    ) -> None:
        with self._excluding_service(service):
            self._guards[field].release(service)
            self._forget_service(service)
            self._resolve_scoped_llm()
            self._rebuild()

    @BindField("_tool_providers", if_valid=True)
    def _on_tool_bind(self, field: str, service: Any, reference: Any) -> None:
        if not self._guards[field].admit(service):
            return
        self._remember_service(service, reference)
        self._rebuild()

    @UnbindField("_tool_providers", if_valid=True)
    def _on_tool_unbind(self, field: str, service: Any, reference: Any) -> None:
        with self._excluding_service(service):
            self._guards[field].release(service)
            self._forget_service(service)
            self._rebuild()

    @BindField("_middleware_providers", if_valid=True)
    def _on_middleware_bind(self, field: str, service: Any, reference: Any) -> None:
        if not self._guards[field].admit(service):
            return
        self._remember_service(service, reference)
        self._rebuild()

    @UnbindField("_middleware_providers", if_valid=True)
    def _on_middleware_unbind(self, field: str, service: Any, reference: Any) -> None:
        with self._excluding_service(service):
            self._guards[field].release(service)
            self._forget_service(service)
            self._rebuild()

    @BindField("_system_prompt_providers", if_valid=True)
    def _on_system_prompt_bind(self, field: str, service: Any, reference: Any) -> None:
        if not self._guards[field].admit(service):
            return
        self._remember_service(service, reference)
        self._rebuild()

    @UnbindField("_system_prompt_providers", if_valid=True)
    def _on_system_prompt_unbind(self, field: str, service: Any, reference: Any) -> None:
        with self._excluding_service(service):
            self._guards[field].release(service)
            self._forget_service(service)
            self._rebuild()

    @BindField("_response_format_provider", if_valid=True)
    def _on_response_format_bind(self, field: str, service: Any, reference: Any) -> None:
        if not self._guards[field].admit(service):
            return
        self._rebuild()

    @UnbindField("_response_format_provider")
    def _on_response_format_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)
        self._graph = None

    @BindField("_state_schema_provider", if_valid=True)
    def _on_state_schema_bind(self, field: str, service: Any, reference: Any) -> None:
        if not self._guards[field].admit(service):
            return
        self._rebuild()

    @UnbindField("_state_schema_provider")
    def _on_state_schema_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)
        self._graph = None

    @BindField("_context_schema_provider", if_valid=True)
    def _on_context_schema_bind(self, field: str, service: Any, reference: Any) -> None:
        if not self._guards[field].admit(service):
            return
        self._rebuild()

    @UnbindField("_context_schema_provider")
    def _on_context_schema_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)
        self._graph = None

    @BindField("_checkpointer_provider", if_valid=True)
    def _on_checkpointer_bind(self, field: str, service: Any, reference: Any) -> None:
        if not self._guards[field].admit(service):
            return
        self._rebuild()

    @UnbindField("_checkpointer_provider")
    def _on_checkpointer_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)
        self._graph = None

    @BindField("_store_provider", if_valid=True)
    def _on_store_bind(self, field: str, service: Any, reference: Any) -> None:
        if not self._guards[field].admit(service):
            return
        self._rebuild()

    @UnbindField("_store_provider")
    def _on_store_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)
        self._graph = None

    @BindField("_interrupt_before_providers", if_valid=True)
    def _on_interrupt_before_bind(self, field: str, service: Any, reference: Any) -> None:
        if not self._guards[field].admit(service):
            return
        self._remember_service(service, reference)
        self._rebuild()

    @UnbindField("_interrupt_before_providers", if_valid=True)
    def _on_interrupt_before_unbind(
        self, field: str, service: Any, reference: Any
    ) -> None:
        with self._excluding_service(service):
            self._guards[field].release(service)
            self._forget_service(service)
            self._rebuild()

    @BindField("_interrupt_after_providers", if_valid=True)
    def _on_interrupt_after_bind(self, field: str, service: Any, reference: Any) -> None:
        if not self._guards[field].admit(service):
            return
        self._remember_service(service, reference)
        self._rebuild()

    @UnbindField("_interrupt_after_providers", if_valid=True)
    def _on_interrupt_after_unbind(
        self, field: str, service: Any, reference: Any
    ) -> None:
        with self._excluding_service(service):
            self._guards[field].release(service)
            self._forget_service(service)
            self._rebuild()

    @BindField("_debug_provider", if_valid=True)
    def _on_debug_bind(self, field: str, service: Any, reference: Any) -> None:
        if not self._guards[field].admit(service):
            return
        self._rebuild()

    @UnbindField("_debug_provider")
    def _on_debug_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)
        self._graph = None

    @BindField("_name_provider", if_valid=True)
    def _on_name_bind(self, field: str, service: Any, reference: Any) -> None:
        if not self._guards[field].admit(service):
            return
        self._rebuild()

    @UnbindField("_name_provider")
    def _on_name_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)
        self._graph = None

    @BindField("_cache_provider", if_valid=True)
    def _on_cache_bind(self, field: str, service: Any, reference: Any) -> None:
        if not self._guards[field].admit(service):
            return
        self._rebuild()

    @UnbindField("_cache_provider")
    def _on_cache_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)
        self._graph = None

    @BindField("_transformers_providers", if_valid=True)
    def _on_transformers_bind(self, field: str, service: Any, reference: Any) -> None:
        if not self._guards[field].admit(service):
            return
        self._remember_service(service, reference)
        self._rebuild()

    @UnbindField("_transformers_providers", if_valid=True)
    def _on_transformers_unbind(
        self, field: str, service: Any, reference: Any
    ) -> None:
        with self._excluding_service(service):
            self._guards[field].release(service)
            self._forget_service(service)
            self._rebuild()

    def _collect_tools(self) -> list[Any]:
        tools: list[Any] = []
        for provider in self._effective(self._tool_providers):
            tools.extend(provider.get_tools())
        return tools

    def _collect_middlewares(self) -> list[Any]:
        middlewares: list[Any] = []
        for provider in self._effective(self._middleware_providers):
            middlewares.extend(provider.get_middlewares())
        return middlewares

    def _collect_system_prompt(self) -> str | None:
        parts = [
            provider.get_system_prompt()
            for provider in self._effective(self._system_prompt_providers)
        ]
        parts = [part for part in parts if part]
        if not parts:
            return None
        return "\n".join(parts)

    def _collect_interrupt_before(self) -> list[str]:
        values: list[str] = []
        for provider in self._effective(self._interrupt_before_providers):
            values.extend(provider.get_interrupt_before())
        return values

    def _collect_interrupt_after(self) -> list[str]:
        values: list[str] = []
        for provider in self._effective(self._interrupt_after_providers):
            values.extend(provider.get_interrupt_after())
        return values

    def _collect_transformers(self) -> list[Any]:
        values: list[Any] = []
        for provider in self._effective(self._transformers_providers):
            values.extend(provider.get_transformers())
        return values

    def _rebuild(self) -> None:
        # A provider failure here must never escape: _rebuild runs inside
        # iPOPO validation/bind callbacks, and a raised exception puts the
        # instance into the permanent ERRONEOUS state ("Agent loop
        # unavailable" until restart). Clear the graph instead; the next
        # successful rebind rebuilds it.
        try:
            model = self._llm_provider.get_model() if self._llm_provider else None
            if model is None:
                self._graph = None
                return
            if self._checkpointer_provider is not None:
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    # The async checkpointer can only be created on the API
                    # event loop; defer the graph build until the first
                    # request, which rebuilds lazily on a running loop.
                    LOGGER.debug("Graph build deferred to the API event loop")
                    self._graph = None
                    return
            self._graph = create_agent(
                model,
                tools=self._collect_tools(),
                middleware=self._collect_middlewares(),
                system_prompt=self._collect_system_prompt(),
                response_format=(
                    self._response_format_provider.get_response_format()
                    if self._response_format_provider
                    else None
                ),
                state_schema=(
                    self._state_schema_provider.get_state_schema()
                    if self._state_schema_provider
                    else None
                ),
                context_schema=(
                    self._context_schema_provider.get_context_schema()
                    if self._context_schema_provider
                    else None
                ),
                checkpointer=(
                    self._checkpointer_provider.get_checkpointer()
                    if self._checkpointer_provider
                    else None
                ),
                store=self._store_provider.get_store() if self._store_provider else None,
                interrupt_before=self._collect_interrupt_before() or None,
                interrupt_after=self._collect_interrupt_after() or None,
                debug=self._debug_provider.get_debug() if self._debug_provider else False,
                name=self._name_provider.get_name() if self._name_provider else None,
                cache=self._cache_provider.get_cache() if self._cache_provider else None,
                transformers=self._collect_transformers() or None,
            )
        except Exception:
            LOGGER.exception("Agent graph rebuild failed; graph is unavailable")
            self._graph = None

    def invoke(self, message: str, *, thread_id: str | None = None) -> Any:
        if self._graph is None:
            self._rebuild()
        if self._graph is None:
            raise RuntimeError("Agent graph is not built; no LLM plugin is available")
        config = {"configurable": {"thread_id": thread_id}} if thread_id else None
        return self._graph.invoke(
            {"messages": [{"role": "user", "content": message}]}, config=config
        )

    async def astream(
        self, message: str, *, thread_id: str | None = None, resume: Any = None
    ) -> AsyncIterator[dict[str, Any]]:
        if self._graph is None:
            self._rebuild()
        if self._graph is None:
            raise RuntimeError("Agent graph is not built; no LLM plugin is available")
        config = {"configurable": {"thread_id": thread_id}} if thread_id else None
        previous_content: dict[str, str] = {}
        usage_totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        input_value: Any = Command(resume=resume) if resume is not None else {
            "messages": [{"role": "user", "content": message}]
        }
        async for stream_type, chunk in self._graph.astream(
            input_value,
            config=config,
            stream_mode=["messages", "updates"],
        ):
            if stream_type == "messages":
                streamed_message = chunk[0]
                usage = _extract_usage(streamed_message)
                if usage is not None:
                    for key in usage_totals:
                        usage_totals[key] += usage[key]
                if isinstance(streamed_message, ToolMessage):
                    continue
                if getattr(streamed_message, "tool_calls", None) or getattr(
                    streamed_message, "tool_call_chunks", None
                ):
                    continue
                content = _extract_content(streamed_message)
                message_id = str(getattr(streamed_message, "id", None) or "default")
                previous = previous_content.get(message_id, "")
                # Chunks may be cumulative (anthropic) or per-token deltas
                # (openai-compatible); compare against the accumulated text so
                # an identical consecutive delta is never sliced to nothing.
                if content.startswith(previous):
                    delta = content[len(previous) :]
                else:
                    delta = content
                previous_content[message_id] = previous + delta
                if delta:
                    yield {"type": "assistant", "content": delta}
                continue

            # LangGraph emits interrupts either at the top level of the updates
            # chunk ({"__interrupt__": (Interrupt(...),)}) or nested under the
            # node name ({"tools": {"__interrupt__": (...), "messages": [...]}}).
            # The interrupt container is a tuple, never a node-update dict.
            for interrupt in _iter_interrupts(chunk):
                yield {"type": "approval_required", "request": interrupt}
            for update in chunk.values():
                if not isinstance(update, dict):
                    continue
                for interrupt in _iter_interrupts(update):
                    yield {"type": "approval_required", "request": interrupt}
                for updated_message in update.get("messages", []):
                    _strip_orphan_tool_use(updated_message)
                    if isinstance(updated_message, ToolMessage):
                        yield {
                            "type": "tool_output",
                            "name": updated_message.name or "tool",
                            "tool_call_id": updated_message.tool_call_id,
                            "output": str(updated_message.content),
                        }
                    for tool_call in getattr(updated_message, "tool_calls", []):
                        yield {
                            "type": "tool_call",
                            "name": tool_call["name"],
                            "tool_call_id": tool_call["id"],
                            "args": tool_call["args"],
                        }
        if usage_totals["total_tokens"]:
            yield {"type": "usage", **usage_totals}

    def describe(self) -> dict[str, Any]:
        llm_info = self._llm_provider.get_plugin_info() if self._llm_provider else None
        return {
            "llm": llm_info,
            "tools": [getattr(tool, "name", str(tool)) for tool in self._collect_tools()],
            "middleware": [middleware.name for middleware in self._collect_middlewares()],
        }

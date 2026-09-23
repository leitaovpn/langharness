"""Agent loop component assembled from injected plugin services."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from langchain.agents import create_agent
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
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

from langharness_core.contracts import (  # noqa: I001 - grouped with the block above
    AgentLoopProvider,
    AwaitUser,
    CacheProvider,
    CheckpointerProvider,
    ContextSchemaProvider,
    Continue,
    DebugProvider,
    InterruptAfterProvider,
    InterruptBeforeProvider,
    JobManagerProvider,
    LLMProvider,
    MiddlewareProvider,
    NameProvider,
    PromptSection,
    PromptSectionProvider,
    PTCRuntimeProvider,
    ResponseFormatProvider,
    RunControllerProvider,
    RunDecision,
    StateSchemaProvider,
    Stop,
    StoreProvider,
    SystemPromptProvider,
    ToolPresentationProvider,
    ToolProvider,
    TransformersProvider,
    WorkspaceProvider,
)
from langharness_core.plugins.presentation.sdk import render_tool_sdk
from langharness_core.plugins.prompts.context import (
    build_prompt_context,
    model_display_name,
)
from langharness_core.plugins.prompts.legacy import legacy_sections
from langharness_core.plugins.prompts.middleware import PromptMiddleware
from langharness_core.plugins.prompts.sections import (
    AssembledPrompt,
    assemble_sections,
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
@RequiresBest(
    "_presentation_provider",
    ToolPresentationProvider,
    optional=True,
    immediate_rebind=True,
)
@RequiresBest("_ptc_runtime", PTCRuntimeProvider, optional=True, immediate_rebind=True)
@Requires("_middleware_providers", MiddlewareProvider, aggregate=True, optional=True)
@Requires(
    "_system_prompt_providers", SystemPromptProvider, aggregate=True, optional=True
)
@Requires(
    "_prompt_section_providers", PromptSectionProvider, aggregate=True, optional=True
)
@RequiresBest(
    "_workspace_provider", WorkspaceProvider, optional=True, immediate_rebind=True
)
@RequiresBest("_job_manager", JobManagerProvider, optional=True, immediate_rebind=True)
@RequiresBest(
    "_run_controller", RunControllerProvider, optional=True, immediate_rebind=True
)
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
    "_presentation_provider",
    "_ptc_runtime",
    "_middleware_providers",
    "_system_prompt_providers",
    "_prompt_section_providers",
    "_workspace_provider",
    "_job_manager",
    "_run_controller",
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
        self._presentation_provider: Any = None
        self._ptc_runtime: Any = None
        self._middleware_providers: list[Any] = []
        self._system_prompt_providers: list[Any] = []
        self._prompt_section_providers: list[Any] = []
        self._workspace_provider: Any = None
        self._job_manager: Any = None
        self._run_controller: Any = None
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
            "_presentation_provider": ContractGuard(
                self, "_presentation_provider", ToolPresentationProvider
            ),
            "_ptc_runtime": ContractGuard(self, "_ptc_runtime", PTCRuntimeProvider),
            "_middleware_providers": ContractGuard(
                self, "_middleware_providers", MiddlewareProvider
            ),
            "_system_prompt_providers": ContractGuard(
                self, "_system_prompt_providers", SystemPromptProvider
            ),
            "_prompt_section_providers": ContractGuard(
                self, "_prompt_section_providers", PromptSectionProvider
            ),
            "_workspace_provider": ContractGuard(
                self, "_workspace_provider", WorkspaceProvider
            ),
            "_job_manager": ContractGuard(self, "_job_manager", JobManagerProvider),
            "_run_controller": ContractGuard(
                self, "_run_controller", RunControllerProvider
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
    def _on_scoped_llm_unbind(self, field: str, service: Any, reference: Any) -> None:
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
    def _on_system_prompt_unbind(
        self, field: str, service: Any, reference: Any
    ) -> None:
        with self._excluding_service(service):
            self._guards[field].release(service)
            self._forget_service(service)
            self._rebuild()

    @BindField("_prompt_section_providers", if_valid=True)
    def _on_prompt_section_bind(self, field: str, service: Any, reference: Any) -> None:
        if not self._guards[field].admit(service):
            return
        self._remember_service(service, reference)
        self._rebuild()

    @UnbindField("_prompt_section_providers", if_valid=True)
    def _on_prompt_section_unbind(
        self, field: str, service: Any, reference: Any
    ) -> None:
        with self._excluding_service(service):
            self._guards[field].release(service)
            self._forget_service(service)
            self._rebuild()

    @BindField("_workspace_provider", if_valid=True)
    def _on_workspace_bind(self, field: str, service: Any, reference: Any) -> None:
        if not self._guards[field].admit(service):
            return
        self._rebuild()

    @UnbindField("_workspace_provider")
    def _on_workspace_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)
        self._graph = None

    @BindField("_presentation_provider", if_valid=True)
    def _on_presentation_bind(self, field: str, service: Any, reference: Any) -> None:
        if not self._guards[field].admit(service):
            return
        self._rebuild()

    @UnbindField("_presentation_provider")
    def _on_presentation_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)
        self._graph = None

    @BindField("_ptc_runtime", if_valid=True)
    def _on_ptc_runtime_bind(self, field: str, service: Any, reference: Any) -> None:
        if not self._guards[field].admit(service):
            return
        self._rebuild()

    @UnbindField("_ptc_runtime")
    def _on_ptc_runtime_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)
        self._graph = None

    @BindField("_job_manager", if_valid=True)
    def _on_job_manager_bind(self, field: str, service: Any, reference: Any) -> None:
        if not self._guards[field].admit(service):
            return
        self._rebuild()

    @UnbindField("_job_manager")
    def _on_job_manager_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guards[field].release(service)
        self._graph = None

    @BindField("_run_controller", if_valid=True)
    def _on_run_controller_bind(self, field: str, service: Any, reference: Any) -> None:
        if not self._guards[field].admit(service):
            return
        self._rebuild()

    @UnbindField("_run_controller")
    def _on_run_controller_unbind(
        self, field: str, service: Any, reference: Any
    ) -> None:
        self._guards[field].release(service)
        self._graph = None

    @BindField("_response_format_provider", if_valid=True)
    def _on_response_format_bind(
        self, field: str, service: Any, reference: Any
    ) -> None:
        if not self._guards[field].admit(service):
            return
        self._rebuild()

    @UnbindField("_response_format_provider")
    def _on_response_format_unbind(
        self, field: str, service: Any, reference: Any
    ) -> None:
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
    def _on_context_schema_unbind(
        self, field: str, service: Any, reference: Any
    ) -> None:
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
    def _on_interrupt_before_bind(
        self, field: str, service: Any, reference: Any
    ) -> None:
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
    def _on_interrupt_after_bind(
        self, field: str, service: Any, reference: Any
    ) -> None:
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
    def _on_transformers_unbind(self, field: str, service: Any, reference: Any) -> None:
        with self._excluding_service(service):
            self._guards[field].release(service)
            self._forget_service(service)
            self._rebuild()

    def _collect_tools(self) -> list[Any]:
        tools: list[Any] = []
        for provider in self._effective(self._tool_providers):
            tools.extend(provider.get_tools())
        mode = self._presentation_mode()
        if mode == "native":
            return tools
        if self._ptc_runtime is None:
            raise RuntimeError("PTC runtime is required for this presentation mode")
        run_code = StructuredTool.from_function(
            coroutine=self._run_code,
            name="run_code",
            description=(
                "Run TypeScript code to call the tools declared in the program SDK. "
                "Return only the program result and explicit logs."
            ),
        )
        return [run_code] if mode == "ptc" else [*tools, run_code]

    async def _run_code(
        self, code: str, description: str = "", timeout_ms: int = 120_000
    ) -> dict[str, Any]:
        del description
        tools = {
            str(getattr(tool, "name")): tool
            for tool in self._collect_native_tools()
            if getattr(tool, "name", None) != "run_code"
        }

        async def dispatch(name: str, args: Any) -> Any:
            tool = tools.get(name)
            if tool is None:
                raise ValueError(f"Tool {name!r} is not available in this scope")
            if not isinstance(args, Mapping):
                raise TypeError("Tool arguments must be an object")
            return await tool.ainvoke(dict(args))

        return cast(
            dict[str, Any],
            await self._ptc_runtime.execute(
            code=code,
            sdk=render_tool_sdk(tools.values()),
            dispatch=dispatch,
            timeout_ms=timeout_ms,
            ),
        )

    def _collect_native_tools(self) -> list[Any]:
        tools: list[Any] = []
        for provider in self._effective(self._tool_providers):
            tools.extend(provider.get_tools())
        return tools

    def _presentation_mode(self) -> str:
        if self._presentation_provider is None:
            return "native"
        return str(self._presentation_provider.get_mode())

    def _require_available_presentation(self) -> None:
        if self._presentation_mode() != "native":
            raise RuntimeError("PTC runtime is not available for this agent")

    def _collect_middlewares(self) -> list[Any]:
        middlewares: list[Any] = []
        for provider in self._effective(self._middleware_providers):
            middlewares.extend(provider.get_middlewares())
        return middlewares

    def _decide(self, rounds_run: int) -> RunDecision | None:
        """Ask the controller what to do now that the graph has gone quiet.

        Returns ``None`` when no controller is bound — the plain-agent path,
        where one graph run is the whole turn. A controller that raises must
        not kill the stream: the run stops and the failure is logged.
        """
        if self._run_controller is None:
            return None
        try:
            decision: RunDecision = self._run_controller.decide(
                rounds_run=rounds_run,
                checkpointer_available=self._checkpointer_provider is not None,
            )
            return decision
        except Exception:
            LOGGER.exception("Run controller failed; stopping the run")
            return Stop(
                status="active",
                reason="controller-error",
                wrapup="The run controller failed; stopping here.",
            )

    def _drain_job_events(self) -> list[dict[str, Any]]:
        """Take whatever background-job events have queued up.

        Drained between graph chunks rather than concurrently: output from a
        long-running job surfaces on the next chunk, not the instant it is
        produced. That is a deliberate trade of immediacy for a stream that
        stays a single, testable iterator.
        """
        if self._job_manager is None:
            return []
        try:
            return list(self._job_manager.drain_events())
        except Exception:
            LOGGER.exception("Job event drain failed; continuing without events")
            return []

    def _workspace_root(self) -> str:
        """The realpath every path-taking plugin resolves against."""
        if self._workspace_provider is not None:
            return str(self._workspace_provider.get_workspace())
        return str(Path.cwd())

    def _model_name(self) -> str | None:
        if self._llm_provider is None:
            return None
        return model_display_name(self._llm_provider.get_model())

    def _prompt_for_request(self, state: Mapping[str, Any]) -> AssembledPrompt:
        """Assemble the system prompt for one model step.

        A failing section provider must not abort the model call: its section is
        dropped, the failure is logged, and the remaining sections still reach
        the model.  Prompt assembly runs on the request path, so degrading beats
        propagating.
        """
        workspace = self._workspace_root()
        context = build_prompt_context(
            workspace=workspace,
            messages=state.get("messages") or (),
            model=self._model_name(),
        )
        sections: list[PromptSection] = list(
            legacy_sections(self._effective(self._system_prompt_providers))
        )
        for provider in self._effective(self._prompt_section_providers):
            try:
                sections.extend(provider.get_prompt_sections(context))
            except Exception:
                LOGGER.exception(
                    "Prompt section provider %s failed; its section was dropped",
                    getattr(provider, "_plugin_key", provider),
                )
        assembled = assemble_sections(sections)
        if assembled.traces:
            LOGGER.debug(
                "prompt sections: %s",
                ", ".join(
                    f"{trace.name}[{trace.bytes}B {trace.sha256[7:19]}]"
                    for trace in assembled.traces
                ),
            )
        return assembled

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
                middleware=[
                    *self._collect_middlewares(),
                    # Innermost: the assembled prompt is the last word on the
                    # request's system message.
                    PromptMiddleware(self._prompt_for_request),
                ],
                system_prompt=None,
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
                store=self._store_provider.get_store()
                if self._store_provider
                else None,
                interrupt_before=self._collect_interrupt_before() or None,
                interrupt_after=self._collect_interrupt_after() or None,
                debug=self._debug_provider.get_debug()
                if self._debug_provider
                else False,
                name=self._name_provider.get_name() if self._name_provider else None,
                cache=self._cache_provider.get_cache()
                if self._cache_provider
                else None,
                transformers=self._collect_transformers() or None,
            )
        except Exception:
            LOGGER.exception("Agent graph rebuild failed; graph is unavailable")
            self._graph = None

    def invoke(self, message: str, *, thread_id: str | None = None) -> Any:
        self._require_available_presentation()
        if self._graph is None:
            self._rebuild()
        if self._graph is None:
            raise RuntimeError("Agent graph is not built; no LLM plugin is available")
        config = {"configurable": {"thread_id": thread_id}} if thread_id else None
        return self._graph.invoke(
            {"messages": [{"role": "user", "content": message}]}, config=config
        )

    async def _forward_stream(
        self,
        stream: AsyncIterator[tuple[str, Any]],
        previous_content: dict[str, str],
        usage_totals: dict[str, int],
    ) -> AsyncIterator[dict[str, Any]]:
        """Translate one graph run's chunks into stream events.

        Pulled out of ``astream`` so the continuation loop and the wrapup round
        share exactly one translator instead of two copies that can drift.
        """
        async for stream_type, chunk in stream:
            for job_event in self._drain_job_events():
                yield job_event
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

    async def astream(
        self, message: str, *, thread_id: str | None = None, resume: Any = None
    ) -> AsyncIterator[dict[str, Any]]:
        self._require_available_presentation()
        if self._graph is None:
            self._rebuild()
        if self._graph is None:
            raise RuntimeError("Agent graph is not built; no LLM plugin is available")
        config = {"configurable": {"thread_id": thread_id}} if thread_id else None
        previous_content: dict[str, str] = {}
        usage_totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        input_value: Any = (
            Command(resume=resume)
            if resume is not None
            else {"messages": [{"role": "user", "content": message}]}
        )

        def run_graph(payload: Any) -> Any:
            return self._graph.astream(
                payload, config=config, stream_mode=["messages", "updates"]
            )

        rounds_run = 0
        while True:
            async for event in self._forward_stream(
                run_graph(input_value), previous_content, usage_totals
            ):
                yield event
            decision = self._decide(rounds_run)
            if decision is None:
                break
            if isinstance(decision, Continue):
                if self._checkpointer_provider is None:
                    # Enforced here rather than trusted to the controller: a
                    # second astream without a checkpointer starts from an
                    # empty conversation, so "continuing" would silently throw
                    # away everything the run had established.
                    yield {"type": "goal_status", "status": "active"}
                    yield {
                        "type": "goal_wrapup",
                        "text": (
                            "This run cannot continue automatically: no "
                            "checkpointer is configured, so the conversation "
                            "would restart and lose its context."
                        ),
                    }
                    break
                rounds_run += 1
                yield {
                    "type": "goal_round_started",
                    "round": decision.round,
                    "message": decision.message,
                }
                # Same thread_id, so the checkpointer carries the conversation
                # forward instead of starting over.
                input_value = {
                    "messages": [{"role": "user", "content": decision.message}]
                }
                continue
            if isinstance(decision, AwaitUser):
                yield {"type": "goal_status", "status": "awaiting_user"}
                break
            yield {"type": "goal_status", "status": decision.status}
            yield {"type": "goal_wrapup", "text": decision.wrapup}
            # One final model step, so the user gets prose rather than a stop.
            async for event in self._forward_stream(
                run_graph({"messages": [{"role": "user", "content": decision.wrapup}]}),
                previous_content,
                usage_totals,
            ):
                yield event
            break
        if usage_totals["total_tokens"]:
            yield {"type": "usage", **usage_totals}

    def describe(self) -> dict[str, Any]:
        llm_info = self._llm_provider.get_plugin_info() if self._llm_provider else None
        available = self._presentation_mode() == "native"
        return {
            "llm": llm_info,
            "tools": (
                [getattr(tool, "name", str(tool)) for tool in self._collect_tools()]
                if available
                else []
            ),
            "tool_headlines": self._collect_tool_headlines() if available else {},
            "middleware": [
                middleware.name for middleware in self._collect_middlewares()
            ],
        }

    def _collect_tool_headlines(self) -> dict[str, str]:
        """Template per tool name, for clients that render calls.

        Names are kept in a separate key rather than folded into ``tools`` so
        that consumers reading the plain name list keep working.
        """
        headlines: dict[str, str] = {}
        for provider in self._effective(self._tool_providers):
            headlines.update(provider.get_tool_presentations())
        return headlines

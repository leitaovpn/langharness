"""Middleware that assembles the system prompt on every model step."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import SystemMessage

from langharness_core.plugins.prompts.sections import AssembledPrompt

PromptBuilder = Callable[[Mapping[str, Any]], AssembledPrompt]


class PromptMiddleware(AgentMiddleware):
    """Replaces the model request's system message with freshly assembled sections.

    Both hooks are implemented on purpose: the agent loop streams through the
    async path, and ``AgentMiddleware`` raises when only the synchronous one
    exists.  Recomputing per request is what lets a provider react to what the
    conversation has already touched without rebuilding the graph.
    """

    def __init__(self, build: PromptBuilder) -> None:
        super().__init__()
        self._build = build

    def _apply(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        prompt = self._build(request.state)
        if prompt.empty:
            return request
        return request.override(system_message=SystemMessage(content=prompt.text))

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        return handler(self._apply(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        return await handler(self._apply(request))

"""Tests for the real before_model compression middleware."""

from typing import Any, cast

from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.graph.message import add_messages

from langharness_core.plugins.context.middleware import (
    ContextCompressionMiddlewarePlugin,
)


def test_context_middleware_is_disabled_by_default() -> None:
    assert ContextCompressionMiddlewarePlugin().get_middlewares() == []


def test_context_middleware_returns_state_updates() -> None:
    plugin = ContextCompressionMiddlewarePlugin()
    plugin._budget_bytes = 1000
    middleware = plugin.get_middlewares()[0]
    result = middleware.before_model(
        {"messages": [HumanMessage(content="goal: keep this")]}, None
    )
    assert result == {}


def test_context_middleware_replaces_checkpoint_history() -> None:
    plugin = ContextCompressionMiddlewarePlugin()
    plugin._budget_bytes = 130
    middleware = plugin.get_middlewares()[0]
    original = [
        HumanMessage(content="old unrelated " + "x" * 200),
        HumanMessage(content="goal: keep"),
    ]

    update = middleware.before_model({"messages": original}, None)
    assert update is not None
    merged = cast(list[BaseMessage], add_messages(cast(Any, original), update["messages"]))

    assert [message.content for message in merged] == ["goal: keep"]

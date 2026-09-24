"""Tests for the real before_model compression middleware."""

from langchain_core.messages import HumanMessage

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
    assert result is not None
    assert result["messages"][0].content == "goal: keep this"

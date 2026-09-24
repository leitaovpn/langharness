"""Tests for optional context compression assembly."""

from langharness_core.plugins.context.transformer import ContextCompressionPlugin


def test_context_compression_is_disabled_by_default() -> None:
    plugin = ContextCompressionPlugin()
    assert plugin.get_transformers() == []
    assert plugin.get_plugin_info()["enabled"] == "false"


def test_context_compression_is_opt_in() -> None:
    plugin = ContextCompressionPlugin()
    plugin._budget_bytes = 1024
    transformers = plugin.get_transformers()
    assert len(transformers) == 1
    assert transformers[0].budget_bytes == 1024
    assert plugin.get_plugin_info()["enabled"] == "true"

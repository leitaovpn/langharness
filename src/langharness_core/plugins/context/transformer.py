"""Optional transformer-provider assembly for bounded context."""

from __future__ import annotations

from typing import Any

from pelix.ipopo.decorators import ComponentFactory, Property, Provides

from langharness_core.contracts import TransformersProvider
from langharness_core.plugins.context.compression import ContextCompressionTransformer


@ComponentFactory("context-compression-plugin-factory")
@Provides(TransformersProvider)
@Property("_plugin_name", "plugin.name", "context-compression")
@Property("_plugin_version", "plugin.version", "1.0.0")
@Property("_budget_bytes", "plugin.context.budget_bytes", 0)
class ContextCompressionPlugin:
    """Inject compression only when an explicit positive budget is configured."""

    def __init__(self) -> None:
        self._plugin_name = "context-compression"
        self._plugin_version = "1.0.0"
        self._budget_bytes = 0

    def get_transformers(self) -> list[Any]:
        budget = int(self._budget_bytes)
        return [ContextCompressionTransformer(budget)] if budget > 0 else []

    def get_plugin_info(self) -> dict[str, str]:
        return {
            "name": self._plugin_name,
            "version": self._plugin_version,
            "enabled": str(int(self._budget_bytes) > 0).lower(),
        }

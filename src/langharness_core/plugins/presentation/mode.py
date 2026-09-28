"""Select the agent's native, PTC, or combined tool presentation mode."""

from __future__ import annotations

from typing import cast

from pelix.ipopo.decorators import ComponentFactory, Property, Provides

from langharness_core.contracts import ToolPresentationMode, ToolPresentationProvider

VALID_MODES = frozenset(("native", "ptc", "both"))


@ComponentFactory("tool-presentation-plugin-factory")
@Provides(ToolPresentationProvider)
@Property("_plugin_name", "plugin.name", "tool-presentation")
@Property("_plugin_version", "plugin.version", "1.0.0")
@Property("_mode", "plugin.tools.presentation", "native")
class ToolPresentationPlugin:
    """Configuration service; runtime presentation is wired in a later task."""

    def __init__(self) -> None:
        self._plugin_name = "tool-presentation"
        self._plugin_version = "1.0.0"
        self._mode = "native"

    def get_mode(self) -> ToolPresentationMode:
        if self._mode not in VALID_MODES:
            raise ValueError(f"Unknown tool presentation mode: {self._mode!r}")
        return cast(ToolPresentationMode, self._mode)

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}

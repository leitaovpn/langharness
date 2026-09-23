"""Prompt sections that describe the programmatic tool calling contract."""

from __future__ import annotations

from pelix.ipopo.decorators import ComponentFactory, Provides, RequiresBest

from langharness_core.contracts import (
    PromptContext,
    PromptSection,
    PromptSectionProvider,
    ToolPresentationProvider,
)


@ComponentFactory("ptc-prompt-plugin-factory")
@Provides(PromptSectionProvider)
@RequiresBest("_presentation", ToolPresentationProvider, optional=True)
class PTCPromptPlugin:
    def __init__(self) -> None:
        self._presentation = None

    def get_prompt_sections(self, context: PromptContext) -> list[PromptSection]:
        del context
        if self._presentation is None or self._presentation.get_mode() == "native":
            return []
        return [
            PromptSection(
                name="ptc-only",
                order=800,
                text=(
                    "run_code is the only tool you can call directly. "
                    "Call every other tool through the typed tools SDK inside "
                    "your program."
                ),
                source="langharness_core.plugins.prompts.ptc",
            )
        ]

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": "ptc-prompt", "version": "1.0.0"}

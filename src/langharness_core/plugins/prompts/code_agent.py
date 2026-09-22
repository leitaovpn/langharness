"""Static prompt sections of the code-agent persona."""

from __future__ import annotations

from importlib import resources
from importlib.resources.abc import Traversable

from pelix.ipopo.decorators import ComponentFactory, Property, Provides

from langharness_core.contracts import (
    PromptContext,
    PromptSection,
    PromptSectionProvider,
)

IDENTITY_ORDER = 0
SAFETY_ORDER = 100
WORKFLOW_ORDER = 200
COMPLETION_ORDER = 300

SECTIONS: tuple[tuple[str, int], ...] = (
    ("identity", IDENTITY_ORDER),
    ("safety", SAFETY_ORDER),
    ("workflow", WORKFLOW_ORDER),
    ("completion", COMPLETION_ORDER),
)

RESOURCE_PACKAGE = "langharness_core"
RESOURCE_DIRECTORY = ("prompts", "code_agent")


def load_section(name: str) -> str:
    """Read one shipped prompt file relative to the installed package."""
    resource: Traversable = resources.files(RESOURCE_PACKAGE)
    for part in RESOURCE_DIRECTORY:
        resource = resource / part
    return resource.joinpath(f"{name}.md").read_text(encoding="utf-8")


@ComponentFactory("code-agent-prompt-plugin-factory")
@Provides(PromptSectionProvider)
@Property("_plugin_name", "plugin.name", "code-agent-prompt")
@Property("_plugin_version", "plugin.version", "1.0.0")
class CodeAgentPromptPlugin:
    """Contributes the static persona, safety, workflow and completion sections."""

    def __init__(self) -> None:
        self._plugin_name = "code-agent-prompt"
        self._plugin_version = "1.0.0"
        self._cache: dict[str, str] = {}

    def get_prompt_sections(self, context: PromptContext) -> list[PromptSection]:
        replacements = {
            "{{model}}": context.model or "unknown",
            "{{workspace}}": context.workspace,
        }
        sections: list[PromptSection] = []
        for name, order in SECTIONS:
            text = self._text(name)
            for placeholder, value in replacements.items():
                text = text.replace(placeholder, value)
            if not text.strip():
                continue
            sections.append(
                PromptSection(
                    name=name,
                    order=order,
                    text=text,
                    source="/".join(
                        (RESOURCE_PACKAGE, *RESOURCE_DIRECTORY, f"{name}.md")
                    ),
                )
            )
        return sections

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}

    def _text(self, name: str) -> str:
        cached = self._cache.get(name)
        if cached is None:
            cached = load_section(name)
            self._cache[name] = cached
        return cached

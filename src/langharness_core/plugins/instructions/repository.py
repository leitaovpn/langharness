"""Prompt sections carrying repository instructions such as ``AGENTS.md``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pelix.ipopo.decorators import ComponentFactory, Property, Provides

from langharness_core.contracts import (
    PromptContext,
    PromptSection,
    PromptSectionProvider,
)
from langharness_core.plugins.instructions.resolver import (
    DEFAULT_BUDGET_BYTES,
    DEFAULT_NAMES,
    InstructionResolver,
    InstructionSelection,
)

INSTRUCTION_ORDER = 500
OMISSION_ORDER = 599
# Keeps the deepest instruction sections ahead of the mode-policy slot (700).
MAX_INSTRUCTION_DEPTH = 90


@ComponentFactory("repository-instructions-plugin-factory")
@Provides(PromptSectionProvider)
@Property("_plugin_name", "plugin.name", "repository-instructions")
@Property("_plugin_version", "plugin.version", "1.0.0")
@Property("_include", "plugin.instructions.include", None)
@Property("_budget", "plugin.instructions.budget_bytes", DEFAULT_BUDGET_BYTES)
class RepositoryInstructionsPlugin:
    """Contributes the instruction files between the workspace root and the target.

    Only the root instruction is loaded before the conversation names a deeper
    path; each tool call that reaches a new directory brings that directory's
    instructions into the next model step, because the prompt is assembled per
    request rather than baked into the graph.
    """

    def __init__(self) -> None:
        self._plugin_name = "repository-instructions"
        self._plugin_version = "1.0.0"
        self._include: Any = None
        self._budget = DEFAULT_BUDGET_BYTES
        self._resolvers: dict[str, InstructionResolver] = {}

    def get_prompt_sections(self, context: PromptContext) -> list[PromptSection]:
        resolver = self._resolver(Path(context.workspace))
        if resolver is None:
            return []
        return self._sections(resolver.resolve(context.touched_paths))

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}

    def _resolver(self, workspace: Path) -> InstructionResolver | None:
        root = workspace.resolve()
        if not root.is_dir():
            return None
        key = str(root)
        resolver = self._resolvers.get(key)
        if resolver is None:
            resolver = InstructionResolver(
                root, names=self._names(), budget=int(self._budget)
            )
            self._resolvers[key] = resolver
        return resolver

    def _names(self) -> tuple[str, ...]:
        extra = self._include
        if isinstance(extra, str):
            extra = [item.strip() for item in extra.split(",") if item.strip()]
        if isinstance(extra, (list, tuple)):
            return tuple(
                dict.fromkeys([*DEFAULT_NAMES, *(str(item) for item in extra)])
            )
        return DEFAULT_NAMES

    def _sections(self, selection: InstructionSelection) -> list[PromptSection]:
        sections = [
            PromptSection(
                name=f"repository-instructions:{item.relative}",
                order=INSTRUCTION_ORDER + min(item.depth, MAX_INSTRUCTION_DEPTH),
                text=item.text,
                source=str(item.path),
            )
            for item in selection.files
        ]
        notice = self._notice(selection)
        if notice is not None:
            sections.append(
                PromptSection(
                    name="repository-instructions-omitted",
                    order=OMISSION_ORDER,
                    text=notice,
                    source=self._plugin_name,
                )
            )
        return sections

    def _notice(self, selection: InstructionSelection) -> str | None:
        truncated = [item.relative for item in selection.files if item.truncated]
        if not selection.omitted and not truncated:
            return None
        parts = [
            f"Some repository instructions did not fit the {selection.budget}-byte "
            "budget and are not reproduced above."
        ]
        if selection.omitted:
            parts.append("Omitted: " + ", ".join(selection.omitted) + ".")
        if truncated:
            parts.append("Truncated: " + ", ".join(truncated) + ".")
        parts.append("Read those files directly if their directory becomes relevant.")
        return " ".join(parts)

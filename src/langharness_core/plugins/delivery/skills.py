"""Optional read-only workspace skill inventory."""

from __future__ import annotations

import hashlib
from typing import Any

from langchain_core.tools import StructuredTool
from pelix.ipopo.decorators import (
    BindField,
    ComponentFactory,
    Property,
    Provides,
    RequiresBest,
    UnbindField,
)
from pydantic import BaseModel, Field

from langharness_core.contracts import ToolProvider, WorkspaceProvider
from langharness_core.plugins.workspace.paths import (
    WorkspacePathError,
    resolve_workspace_path,
)
from langharness_plugin.scoped_dependencies import ScopedDependencies
from langharness_plugin.validation import ContractGuard


class SkillsArgs(BaseModel):
    root: str = Field(default=".codex/skills", min_length=1)


@ComponentFactory("skills-tools-plugin-factory")
@Provides(ToolProvider)
@RequiresBest("_workspace", WorkspaceProvider, optional=True, immediate_rebind=True)
@ScopedDependencies("_workspace")
@Property("_plugin_name", "plugin.name", "skills-tools")
@Property("_plugin_version", "plugin.version", "1.0.0")
class SkillsToolsPlugin:
    """List skill metadata without loading or executing instructions."""

    def __init__(self) -> None:
        self._plugin_name = "skills-tools"
        self._plugin_version = "1.0.0"
        self._workspace: Any = None
        self._guard = ContractGuard(self, "_workspace", WorkspaceProvider)

    @BindField("_workspace", if_valid=True)
    def _on_workspace_bind(self, field: str, service: Any, reference: Any) -> None:
        self._guard.admit(service)

    @UnbindField("_workspace")
    def _on_workspace_unbind(self, field: str, service: Any, reference: Any) -> None:
        self._guard.release(service)

    def get_tools(self) -> list[Any]:
        if self._workspace is None:
            return []
        return [StructuredTool.from_function(
            func=self._list_skills,
            name="list_skills",
            description="List workspace skill metadata; never execute or fetch skill content.",
            args_schema=SkillsArgs,
            metadata={"permission": "workspace-read", "artifact": "skill-manifest"},
        )]

    def _list_skills(self, root: str = ".codex/skills") -> dict[str, Any]:
        try:
            target = resolve_workspace_path(self._workspace.get_workspace(), root)
        except WorkspacePathError as exc:
            return {"error": {"code": exc.code, "message": exc.message}}
        if not target.is_dir:
            return {"error": {"code": "path_not_found", "message": f"{root!r} is not a directory"}}
        skills: list[dict[str, Any]] = []
        for marker in sorted(target.absolute.glob("*/SKILL.md")):
            if not marker.is_file():
                continue
            raw = marker.read_bytes()
            skills.append({
                "name": marker.parent.name,
                "path": marker.relative_to(target.root).as_posix(),
                "size_bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            })
        return {"skills": skills, "permission": "workspace-read"}

    def get_tool_presentations(self) -> dict[str, str]:
        return {"list_skills": "list_skills({root})"}

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}

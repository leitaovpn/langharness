"""Read-only manifest of explicit workspace deliverables."""

from __future__ import annotations

import hashlib
from pathlib import Path
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

from langharness_core.common.tool_error import tool_error
from langharness_core.contracts import ToolProvider, WorkspaceProvider
from langharness_core.plugins.workspace.paths import (
    WorkspacePathError,
    resolve_workspace_path,
)
from langharness_plugin.scoped_dependencies import ScopedDependencies
from langharness_plugin.validation import ContractGuard

MAX_ARTIFACTS = 32
MAX_ARTIFACT_BYTES = 100 * 1024 * 1024


class ManifestArgs(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=MAX_ARTIFACTS)


@ComponentFactory("delivery-tools-plugin-factory")
@Provides(ToolProvider)
@RequiresBest("_workspace", WorkspaceProvider, optional=True, immediate_rebind=True)
@ScopedDependencies("_workspace")
@Property("_plugin_name", "plugin.name", "delivery-tools")
@Property("_plugin_version", "plugin.version", "1.0.0")
class DeliveryToolsPlugin:
    """Expose a manifest only when an agent-scoped workspace is bound."""

    def __init__(self) -> None:
        self._plugin_name = "delivery-tools"
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
            func=self._manifest,
            name="delivery_manifest",
            description=(
                "Hash explicitly named workspace files for delivery. Read-only; "
                "does not upload, publish, or package files."
            ),
            args_schema=ManifestArgs,
            metadata={"permission": "workspace-read", "artifact": "sha256-manifest"},
        )]

    def _manifest(self, paths: list[str]) -> dict[str, Any]:
        root = Path(self._workspace.get_workspace())
        artifacts: list[dict[str, Any]] = []
        for raw in paths:
            try:
                target = resolve_workspace_path(root, raw)
            except WorkspacePathError as exc:
                return tool_error(exc.code, exc.message)
            if not target.is_file:
                return tool_error("path_not_found", f"{raw!r} is not a regular file")
            size = target.absolute.stat().st_size
            if size > MAX_ARTIFACT_BYTES:
                return tool_error("invalid_arguments", f"{raw!r} exceeds delivery size limit")
            digest = hashlib.sha256()
            with target.absolute.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1_048_576), b""):
                    digest.update(chunk)
            artifacts.append({
                "path": target.relative,
                "size_bytes": size,
                "sha256": digest.hexdigest(),
            })
        return {"artifacts": artifacts, "permission": "workspace-read"}

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}

    def get_tool_presentations(self) -> dict[str, str]:
        return {"delivery_manifest": "delivery_manifest({paths})"}

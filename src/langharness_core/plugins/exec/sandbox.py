"""Optional OS sandbox capability detection and command wrapping."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pelix.ipopo.decorators import ComponentFactory, Property, Provides

from langharness_core.contracts import SandboxProvider


@ComponentFactory("sandbox-plugin-factory")
@Provides(SandboxProvider)
@Property("_plugin_name", "plugin.name", "sandbox")
@Property("_plugin_version", "plugin.version", "1.0.0")
@Property("_backend", "plugin.sandbox.backend", "")
class OSSandboxPlugin:
    """Use a sandbox backend only when the operator explicitly configured it."""

    def __init__(self) -> None:
        self._plugin_name = "sandbox"
        self._plugin_version = "1.0.0"
        self._backend = ""

    def _validated_backend(self) -> str | None:
        if not self._backend:
            return None
        path = Path(self._backend)
        if path.name != "bwrap" or not path.is_file():
            raise RuntimeError("Only an explicitly configured bwrap executable is supported")
        if not os.access(path, os.X_OK):
            raise RuntimeError("Configured bwrap must be executable")
        return str(path)

    def get_isolation(self) -> Literal["os-sandbox", "process-only"]:
        return "os-sandbox" if self._validated_backend() else "process-only"

    def wrap_command(self, argv: list[str], cwd: str) -> list[str]:
        backend = self._validated_backend()
        if backend is None:
            return list(argv)
        return [
            backend,
            "--die-with-parent",
            "--unshare-net",
            "--ro-bind",
            "/",
            "/",
            "--bind",
            cwd,
            cwd,
            "--chdir",
            cwd,
            "--",
            *argv,
        ]

    def get_plugin_info(self) -> dict[str, str]:
        return {
            "name": self._plugin_name,
            "version": self._plugin_version,
            "isolation": self.get_isolation(),
        }

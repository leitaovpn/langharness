"""Optional OS sandbox capability detection and command wrapping."""

from __future__ import annotations

import shutil

from pelix.ipopo.decorators import ComponentFactory, Property, Provides

from langharness_core.contracts import SandboxProvider


@ComponentFactory("sandbox-plugin-factory")
@Provides(SandboxProvider)
@Property("_plugin_name", "plugin.name", "sandbox")
@Property("_plugin_version", "plugin.version", "1.0.0")
class OSSandboxPlugin:
    """Use a host sandbox executable when explicitly available."""

    def __init__(self) -> None:
        self._plugin_name = "sandbox"
        self._plugin_version = "1.0.0"
        self._backend = self._detect_backend()

    @staticmethod
    def _detect_backend() -> str | None:
        for candidate in ("bwrap", "sandbox-exec"):
            path = shutil.which(candidate)
            if path:
                return path
        return None

    def get_isolation(self) -> str:
        return "os-sandbox" if self._backend else "process-only"

    def wrap_command(self, argv: list[str], cwd: str) -> list[str]:
        if not self._backend:
            return list(argv)
        name = self._backend.rsplit("/", 1)[-1]
        if name == "sandbox-exec":
            return [self._backend, "-p", "(version 1) (allow process*) (allow file-read*)", *argv]
        return [
            self._backend,
            "--die-with-parent",
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

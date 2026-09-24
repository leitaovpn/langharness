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

    def wrap_command(
        self,
        argv: list[str],
        cwd: str,
        workspace_root: str | None = None,
        read_only_paths: list[str] | None = None,
    ) -> list[str]:
        backend = self._validated_backend()
        if backend is None:
            return list(argv)
        workspace = Path(workspace_root or cwd).resolve(strict=True)
        working_dir = Path(cwd).resolve(strict=True)
        if not workspace.is_dir() or not working_dir.is_dir():
            raise RuntimeError("Sandbox workspace and cwd must be directories")
        if workspace == Path("/") or not working_dir.is_relative_to(workspace):
            raise RuntimeError("Sandbox cwd must remain inside a non-root workspace")
        system_roots = [
            root for root in (Path("/usr"), Path("/bin"), Path("/lib"), Path("/lib64"))
            if root.exists()
        ]
        if any(workspace.is_relative_to(root) for root in system_roots):
            raise RuntimeError("Sandbox workspace overlaps a system mount")
        wrapped = [
            backend,
            "--die-with-parent",
            "--unshare-net",
            "--unshare-pid",
            "--unshare-ipc",
            "--new-session",
            "--clearenv",
            "--setenv", "PATH", "/usr/bin:/bin",
            "--setenv", "HOME", "/nonexistent",
            "--setenv", "TMPDIR", "/tmp",
            "--tmpfs", "/tmp",
            "--dev", "/dev",
            "--proc", "/proc",
        ]
        for root in system_roots:
            wrapped.extend(("--ro-bind", str(root), str(root)))
        for parent in reversed(workspace.parents):
            if parent != Path("/"):
                wrapped.extend(("--dir", str(parent)))
        for raw in read_only_paths or []:
            path = Path(raw).resolve(strict=True)
            if not path.is_file():
                raise RuntimeError("Sandbox read-only mount must be a file")
            if path.is_relative_to(workspace) or any(
                path.is_relative_to(root) for root in system_roots
            ):
                continue
            for parent in reversed(path.parents):
                if parent != Path("/"):
                    wrapped.extend(("--dir", str(parent)))
            wrapped.extend(("--ro-bind", str(path), str(path)))
        wrapped.extend((
            "--bind", str(workspace), str(workspace),
            "--chdir", str(working_dir), "--", *argv,
        ))
        return wrapped

    def get_plugin_info(self) -> dict[str, str]:
        return {
            "name": self._plugin_name,
            "version": self._plugin_version,
            "isolation": self.get_isolation(),
        }

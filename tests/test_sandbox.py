"""Acceptance tests for optional OS sandbox capability reporting."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from langharness_core.plugins.exec.sandbox import OSSandboxPlugin


def test_unavailable_sandbox_is_explicitly_process_only() -> None:
    plugin = OSSandboxPlugin()
    plugin._backend = ""

    assert plugin.get_isolation() == "process-only"
    assert plugin.wrap_command(["node", "script.js"], "/tmp/work") == [
        "node",
        "script.js",
    ]
    assert plugin.get_plugin_info()["isolation"] == "process-only"


def test_explicit_bwrap_wraps_command_and_reports_os_sandbox(
    tmp_path: Path,
) -> None:
    plugin = OSSandboxPlugin()
    backend = tmp_path / "bwrap"
    backend.touch()
    backend.chmod(backend.stat().st_mode | stat.S_IXUSR)
    plugin._backend = str(backend)

    wrapped = plugin.wrap_command(["node", "script.js"], "/tmp/work")

    assert wrapped[:3] == [str(backend), "--die-with-parent", "--unshare-net"]
    assert wrapped[-2:] == ["node", "script.js"]
    assert plugin.get_isolation() == "os-sandbox"


def test_non_executable_bwrap_is_rejected(tmp_path: Path) -> None:
    plugin = OSSandboxPlugin()
    backend = tmp_path / "bwrap"
    backend.touch(mode=0o600)
    backend.chmod(0o600)
    plugin._backend = str(backend)

    with pytest.raises(RuntimeError, match="executable"):
        plugin.get_isolation()


def test_incomplete_macos_policy_is_rejected() -> None:
    plugin = OSSandboxPlugin()
    plugin._backend = "/usr/bin/sandbox-exec"
    with pytest.raises(RuntimeError, match="bwrap"):
        plugin.get_isolation()

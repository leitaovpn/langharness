"""Acceptance tests for optional OS sandbox capability reporting."""

from __future__ import annotations

from langharness_core.plugins.exec.sandbox import OSSandboxPlugin


def test_unavailable_sandbox_is_explicitly_process_only() -> None:
    plugin = OSSandboxPlugin()
    plugin._backend = None

    assert plugin.get_isolation() == "process-only"
    assert plugin.wrap_command(["node", "script.js"], "/tmp/work") == [
        "node",
        "script.js",
    ]
    assert plugin.get_plugin_info()["isolation"] == "process-only"


def test_supported_backend_wraps_command_and_reports_os_sandbox() -> None:
    plugin = OSSandboxPlugin()
    plugin._backend = "/usr/bin/sandbox-exec"

    wrapped = plugin.wrap_command(["node", "script.js"], "/tmp/work")

    assert wrapped[:3] == [
        "/usr/bin/sandbox-exec",
        "-p",
        "(version 1) (allow process*) (allow file-read*)",
    ]
    assert wrapped[-2:] == ["node", "script.js"]
    assert plugin.get_isolation() == "os-sandbox"

"""Acceptance tests for optional OS sandbox capability reporting."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from langharness_core.plugins.exec import sandbox as sandbox_module
from langharness_core.plugins.exec.sandbox import OSSandboxPlugin


@pytest.fixture
def linux_bwrap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox_module, "_IS_LINUX", True)


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
    linux_bwrap: None,
) -> None:
    plugin = OSSandboxPlugin()
    backend = tmp_path / "bwrap"
    backend.touch()
    backend.chmod(backend.stat().st_mode | stat.S_IXUSR)
    plugin._backend = str(backend)

    workspace = tmp_path / "workspace"
    nested = workspace / "src"
    nested.mkdir(parents=True)
    wrapped = plugin.wrap_command(
        ["node", "script.js"], str(nested), workspace_root=str(workspace)
    )

    assert wrapped[:2] == [str(backend), "--die-with-parent"]
    assert "--unshare-net" in wrapped
    assert "--unshare-pid" in wrapped
    assert "--new-session" in wrapped
    assert "--clearenv" in wrapped
    assert all(
        wrapped[index + 1 : index + 3] != ["/", "/"]
        for index, part in enumerate(wrapped)
        if part == "--ro-bind"
    )
    assert wrapped[wrapped.index("--bind") + 1:wrapped.index("--bind") + 3] == [
        str(workspace), str(workspace)
    ]
    assert wrapped[wrapped.index("--chdir") + 1] == str(nested)
    assert wrapped[-2:] == ["node", "script.js"]
    assert plugin.get_isolation() == "os-sandbox"


def test_bwrap_rejects_cwd_outside_workspace(tmp_path: Path, linux_bwrap: None) -> None:
    plugin = OSSandboxPlugin()
    backend = tmp_path / "bwrap"
    backend.touch(mode=0o700)
    backend.chmod(0o700)
    plugin._backend = str(backend)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(RuntimeError, match="workspace"):
        plugin.wrap_command(["pwd"], str(outside), workspace_root=str(workspace))

    (workspace / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(RuntimeError, match="workspace"):
        plugin.wrap_command(
            ["pwd"], str(workspace / "escape"), workspace_root=str(workspace)
        )


def test_bwrap_mounts_only_explicit_trusted_read_only_file(
    tmp_path: Path, linux_bwrap: None
) -> None:
    plugin = OSSandboxPlugin()
    backend = tmp_path / "bwrap"
    backend.touch(mode=0o700)
    backend.chmod(0o700)
    plugin._backend = str(backend)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    bootstrap = tmp_path / "package" / "bootstrap.mjs"
    bootstrap.parent.mkdir()
    bootstrap.write_text("", encoding="utf-8")

    wrapped = plugin.wrap_command(
        ["node", str(bootstrap)], str(workspace),
        read_only_paths=[str(bootstrap)],
    )

    assert ["--ro-bind", str(bootstrap), str(bootstrap)] == wrapped[
        wrapped.index(str(bootstrap)) - 1 : wrapped.index(str(bootstrap)) + 2
    ]

    with pytest.raises(RuntimeError, match="file"):
        plugin.wrap_command(
            ["node"], str(workspace), read_only_paths=[str(tmp_path / "package")]
        )


def test_non_executable_bwrap_is_rejected(tmp_path: Path, linux_bwrap: None) -> None:
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


def test_bwrap_is_rejected_on_non_linux_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sandbox_module, "_IS_LINUX", False)
    backend = tmp_path / "bwrap"
    backend.touch(mode=0o700)
    backend.chmod(0o700)
    plugin = OSSandboxPlugin()
    plugin._backend = str(backend)

    with pytest.raises(RuntimeError, match="Linux"):
        plugin.get_isolation()

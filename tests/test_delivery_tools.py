"""Scoped, read-only delivery manifest tests."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

from langharness_core.contracts import ToolProvider
from langharness_core.plugin import agent_plugin_template_descriptor
from langharness_core.plugins.delivery.manifest import DeliveryToolsPlugin
from langharness_plugin.validation import validate


def test_delivery_is_optional_and_requires_workspace() -> None:
    plugin = DeliveryToolsPlugin()
    assert validate(plugin, ToolProvider) == ()
    assert plugin.get_tools() == []
    descriptor = agent_plugin_template_descriptor("delivery")
    assert descriptor.factory == "delivery-tools-plugin-factory"


def test_delivery_manifest_hashes_only_explicit_workspace_files(tmp_path: Path) -> None:
    (tmp_path / "answer.txt").write_text("answer", encoding="utf-8")
    plugin = DeliveryToolsPlugin()
    plugin._workspace = SimpleNamespace(get_workspace=lambda: str(tmp_path))
    tool = plugin.get_tools()[0]
    result = tool.invoke({"paths": ["answer.txt"]})
    assert result == {
        "artifacts": [{
            "path": "answer.txt",
            "size_bytes": 6,
            "sha256": hashlib.sha256(b"answer").hexdigest(),
        }],
        "permission": "workspace-read",
    }


def test_delivery_rejects_escape_and_missing_file(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-secret.txt"
    plugin = DeliveryToolsPlugin()
    plugin._workspace = SimpleNamespace(get_workspace=lambda: str(tmp_path))
    tool = plugin.get_tools()[0]
    assert tool.invoke({"paths": [str(outside)]})["error"]["code"] == "path_outside_workspace"
    assert tool.invoke({"paths": ["missing.txt"]})["error"]["code"] == "path_not_found"

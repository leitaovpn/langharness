"""Create an isolated dynamic plugin distribution for real CLI tests."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent


def create_dynamic_plugin(root: Path) -> Path:
    """Create ``real.echo`` under ``root`` and return that directory.

    The fixture is intentionally isolated from the installed environment: the
    generated ``.dist-info`` is placed on ``PYTHONPATH`` for subprocesses.
    """

    package = root / "real_dynamic_plugin"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text(
        dedent(
            """\
            from langharness_plugin.contracts import SPEC_TOOL_EXPORT_TARGET
            from langharness_plugin.package import (
                PluginContribution,
                PluginPackage,
                ToolExport,
            )
            from langharness_plugin.registry import PluginDescriptor
            from pydantic import BaseModel


            class EchoArgs(BaseModel):
                text: str


            def package() -> PluginPackage:
                return PluginPackage(
                    "real.echo",
                    "1.0.0",
                    (
                        PluginContribution(
                            "echo",
                            "server",
                            PluginDescriptor(
                                name="real-echo",
                                version="1.0.0",
                                module="real_dynamic_plugin.echo",
                                factory="real-echo-factory",
                                specification=SPEC_TOOL_EXPORT_TARGET,
                                description=(
                                    "Echoes text back to the user. Implements "
                                    "plugin.tool_export.target. No properties. "
                                    "Uninstall when the echo tools are not needed."
                                ),
                            ),
                            tool_exports=(
                                ToolExport(
                                    "real_echo",
                                    "Echo text back to the user.",
                                    "echo",
                                    EchoArgs,
                                ),
                            ),
                        ),
                    ),
                )
            """
        ),
        encoding="utf-8",
    )
    (package / "echo.py").write_text(
        dedent(
            """\
            from typing import Any

            from pelix.ipopo.decorators import ComponentFactory, Provides

            from langharness_plugin.contracts import ToolExportTarget


            @ComponentFactory("real-echo-factory")
            @Provides(ToolExportTarget)
            class RealEcho:
                def invoke_export(
                    self, operation: str, arguments: dict[str, Any]
                ) -> Any:
                    return {"operation": operation, "arguments": arguments}
            """
        ),
        encoding="utf-8",
    )

    dist_info = root / "langharness_real_dynamic_plugin-1.0.0.dist-info"
    dist_info.mkdir(parents=True, exist_ok=True)
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\n"
        "Name: langharness-real-dynamic-plugin\n"
        "Version: 1.0.0\n",
        encoding="utf-8",
    )
    (dist_info / "entry_points.txt").write_text(
        "[langharness.plugins]\n"
        "real-echo = real_dynamic_plugin:package\n",
        encoding="utf-8",
    )
    return root

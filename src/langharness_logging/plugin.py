"""Descriptors and assembly helpers for the built-in logging plugins."""

from __future__ import annotations

from typing import Any

from langharness_logging.contracts import SPEC_LOG
from langharness_plugin.package import PluginContribution, PluginPackage
from langharness_plugin.registry import PluginDescriptor

LOG_DESCRIPTION = (
    "Writes process logs to a file. Implements log.provider. Properties: "
    "plugin.log.directory, plugin.log.name, plugin.log.filename, "
    "plugin.log.capture. Requires a restart for property changes. "
    "Uninstall to disable file logging."
)


def log_descriptor() -> PluginDescriptor:
    """Static definition of the file logging component.

    The cli and server log outputs are two instances of this one
    definition with role-specific properties.
    """
    return PluginDescriptor(
        name="file-log",
        version="1.0.0",
        module="langharness_logging.plugins.log",
        factory="file-log-plugin-factory",
        specification=SPEC_LOG,
        description=LOG_DESCRIPTION,
    )


def log_properties(role: str, directory: str) -> dict[str, Any]:
    if role not in {"cli", "server"}:
        raise ValueError(f"Unsupported log role: {role}")
    return {
        "plugin.log.directory": directory,
        "plugin.log.name": f"langharness.{role}",
        "plugin.log.filename": f"langharness_{role}.log",
        "plugin.log.capture": (
            "uvicorn.error,uvicorn.access" if role == "server" else ""
        ),
    }


def builtin_package() -> PluginPackage:
    """Describe the CLI and server logging plugin instances."""
    return PluginPackage(
        id="builtin.logging",
        version="1.0.0",
        contributions=(
            PluginContribution("server-log", "root", log_descriptor()),
            PluginContribution("cli-log", "root", log_descriptor()),
        ),
    )

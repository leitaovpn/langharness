"""Descriptors and assembly helpers for the built-in CLI plugins."""

from __future__ import annotations

from langharness_cli.contracts import (
    SPEC_CLI_COMMAND,
    SPEC_CLI_RENDERER,
    SPEC_UI_SERVER,
)
from langharness_plugin.package import PluginContribution, PluginPackage
from langharness_plugin.registry import PluginDescriptor

UI_SERVER_DESCRIPTION = (
    "Runs the interactive CLI user interface. Implements ui.plugin.server. "
    "No properties. Uninstall to run the CLI without the interactive UI."
)

COMMAND_DESCRIPTION = (
    "Provides one CLI command. Implements ui.plugin.command. Properties: "
    "plugin.ui.locale. Requires a restart for property changes. Uninstall "
    "to remove the command."
)

RENDERER_DESCRIPTION = (
    "Renders CLI output tables and panels. Implements ui.plugin.renderer. "
    "Properties: plugin.ui.locale. Requires a restart for property changes. "
    "Uninstall to disable rich rendering."
)


def cli_descriptors() -> list[PluginDescriptor]:
    return [
        PluginDescriptor(
            name="ui-server",
            version="1.0.0",
            module="langharness_cli.plugins.server",
            factory="ui-server-factory",
            specification=SPEC_UI_SERVER,
            description=UI_SERVER_DESCRIPTION,
        ),
        PluginDescriptor(
            name="cli-health",
            version="1.0.0",
            module="langharness_cli.plugins.commands.health",
            factory="cli-health-plugin-factory",
            specification=SPEC_CLI_COMMAND,
            description=COMMAND_DESCRIPTION,
        ),
        PluginDescriptor(
            name="cli-model",
            version="1.0.0",
            module="langharness_cli.plugins.commands.model",
            factory="cli-model-command-factory",
            specification=SPEC_CLI_COMMAND,
            description=COMMAND_DESCRIPTION,
        ),
        PluginDescriptor(
            name="cli-rich-renderer",
            version="1.0.0",
            module="langharness_cli.plugins.rich_renderer",
            factory="rich-cli-renderer-factory",
            specification=SPEC_CLI_RENDERER,
            description=RENDERER_DESCRIPTION,
        ),
        PluginDescriptor(
            name="cli-session",
            version="1.0.0",
            module="langharness_cli.plugins.commands.session",
            factory="cli-session-command-factory",
            specification=SPEC_CLI_COMMAND,
            description=COMMAND_DESCRIPTION,
        ),
        PluginDescriptor(
            name="cli-plugins",
            version="1.0.0",
            module="langharness_cli.plugins.commands.plugins",
            factory="cli-plugins-command-factory",
            specification=SPEC_CLI_COMMAND,
            description=COMMAND_DESCRIPTION,
        ),
        PluginDescriptor(
            name="cli-scope",
            version="1.0.0",
            module="langharness_cli.plugins.commands.scope",
            factory="cli-scope-command-factory",
            specification=SPEC_CLI_COMMAND,
            description=COMMAND_DESCRIPTION,
        ),
        PluginDescriptor(
            name="cli-shell",
            version="1.0.0",
            module="langharness_cli.plugins.commands.shell",
            factory="cli-shell-command-factory",
            specification=SPEC_CLI_COMMAND,
            description=COMMAND_DESCRIPTION,
        ),
    ]


def builtin_package() -> PluginPackage:
    """Describe the CLI plugins already installed by the CLI assembly."""
    return PluginPackage(
        id="builtin.cli",
        version="1.0.0",
        contributions=tuple(
            PluginContribution(descriptor.name, "ui", descriptor)
            for descriptor in cli_descriptors()
        ),
    )

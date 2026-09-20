"""Descriptors and assembly helpers for the built-in configuration plugins."""

from __future__ import annotations

from langharness_config.contracts import SPEC_CONFIG_PROVIDER, SPEC_CONFIGS
from langharness_plugin.package import PluginContribution, PluginPackage
from langharness_plugin.registry import PluginDescriptor

CONFIG_TOML_DESCRIPTION = (
    "Loads plugin configuration from a TOML file. Implements "
    "config.provider. Properties: plugin.config.path (the file to read). "
    "Requires a restart for property changes. Uninstall to disable "
    "file-based configuration."
)

CONFIGS_DESCRIPTION = (
    "Serves the merged configuration store to other plugins. Implements "
    "config.service. No properties. Do not uninstall while any plugin "
    "reads configuration."
)


def config_descriptors() -> list[PluginDescriptor]:
    """Static definitions of the built-in configuration plugins."""
    return [
        PluginDescriptor(
            name="config-toml",
            version="1.0.0",
            module="langharness_config.plugins.toml",
            factory="toml-config-plugin-factory",
            specification=SPEC_CONFIG_PROVIDER,
            description=CONFIG_TOML_DESCRIPTION,
        ),
        PluginDescriptor(
            name="configs",
            version="1.0.0",
            module="langharness_config.plugins.configs",
            factory="configs-plugin-factory",
            specification=SPEC_CONFIGS,
            description=CONFIGS_DESCRIPTION,
        ),
    ]


def builtin_package() -> PluginPackage:
    """Describe the built-in configuration plugins."""
    config_toml, configs = config_descriptors()
    return PluginPackage(
        id="builtin.config",
        version="1.0.0",
        contributions=(
            PluginContribution("config-toml", "root", config_toml),
            PluginContribution("configs", "root", configs),
        ),
    )

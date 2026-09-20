"""Configuration plugin tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from langharness_config.contracts import SPEC_CONFIGS
from langharness_config.plugin import config_descriptors
from langharness_config.plugins.configs import ConfigsPlugin
from langharness_config.plugins.toml import TOMLConfigPlugin
from langharness_plugin.plugin_manager import PluginManager
from langharness_plugin.registry import PluginRegistry
from langharness_scope import ROOT_SCOPE_ID


def test_toml_config_plugin_creates_and_reads_user_config(tmp_path: Path) -> None:
    path = tmp_path / ".langharness" / "langharness.toml"
    plugin = TOMLConfigPlugin()
    plugin._config_path = str(path)

    config = plugin.get_config()

    assert path.is_file()
    assert config["DEFAULT"] == {}
    assert "providers" not in config


def test_toml_config_plugin_reads_toml_strings(tmp_path: Path) -> None:
    path = tmp_path / "langharness.toml"
    path.write_text(
        '[providers.demo]\nbase_url = "https://example.test/v1"\nmodel = "demo"\n',
        encoding="utf-8",
    )
    plugin = TOMLConfigPlugin()
    plugin._config_path = str(path)

    config = plugin.get_config()

    assert config["providers"]["demo"] == {
        "base_url": "https://example.test/v1",
        "model": "demo",
    }


def test_configs_plugin_merges_registered_config_plugins() -> None:
    configs = ConfigsPlugin()
    configs._providers = [
        SimpleNamespace(get_config=lambda: {"DEFAULT": {"color": "blue"}}),
        SimpleNamespace(
            get_config=lambda: {
                "DEFAULT": {"color": "green"},
                "providers": {
                    "demo": {
                        "model": "demo-model",
                        "api_key": "key",
                        "base_url": "https://example.test/v1",
                    }
                },
            }
        ),
    ]

    assert configs.get("DEFAULT", "color") == "green"
    assert configs.get("DEFAULT", "missing", "fallback") == "fallback"
    assert configs.get_section("providers.demo")["model"] == "demo-model"
    assert configs.get_provider("demo")["api_key"] == "key"
    assert configs.get_provider("demo")["protocol"] == "chat"
    assert configs.list_providers() == ["demo"]


def test_configs_get_returns_fallback_for_non_string_toml_values() -> None:
    configs = ConfigsPlugin()
    configs._providers = [
        SimpleNamespace(get_config=lambda: {"runtime": {"retries": 3}})
    ]

    assert configs.get("runtime", "retries", "fallback") == "fallback"


def test_configs_rejects_duplicate_provider_names() -> None:
    configs = ConfigsPlugin()
    provider = {"providers": {"demo": {"model": "same"}}}
    configs._providers = [
        SimpleNamespace(get_config=lambda: provider),
        SimpleNamespace(get_config=lambda: provider),
    ]
    with pytest.raises(ValueError, match="Duplicate provider name: demo"):
        configs.list_providers()


def test_configs_allows_different_providers_to_share_a_model() -> None:
    configs = ConfigsPlugin()
    configs._providers = [
        SimpleNamespace(
            get_config=lambda: {
                "providers": {
                    "primary": {"model": "shared-model"},
                    "fallback": {"model": "shared-model"},
                }
            }
        )
    ]

    assert configs.list_providers() == ["fallback", "primary"]


def test_configs_validates_provider_protocol() -> None:
    configs = ConfigsPlugin()
    configs._providers = [
        SimpleNamespace(
            get_config=lambda: {
                "providers": {"bad": {"model": "", "protocol": "unknown"}}
            }
        )
    ]
    with pytest.raises(ValueError, match="Unsupported protocol"):
        configs.get_provider("bad")
    assert configs.list_providers() == []


def test_configs_requires_a_non_empty_provider_model() -> None:
    configs = ConfigsPlugin()
    configs._providers = [
        SimpleNamespace(get_config=lambda: {"providers": {"bad": {"model": ""}}})
    ]

    with pytest.raises(ValueError, match="requires a non-empty model"):
        configs.get_provider("bad")
    assert configs.list_providers() == []


def test_configs_service_aggregates_toml_plugin_in_ipopo(tmp_path: Path) -> None:
    (tmp_path / "langharness.toml").write_text(
        '[providers.default]\nbase_url = "https://example.test/v1"\n'
        'model = "default-model"\napi_key = "k"\n',
        encoding="utf-8",
    )
    manager = PluginManager(PluginRegistry())
    manager.start()
    try:
        for descriptor in config_descriptors():
            manager.install_descriptor(descriptor)
        manager.create_instance(
            "toml-config-plugin-factory",
            "langharness_config.plugins.toml",
            ROOT_SCOPE_ID,
            properties={"plugin.config.path": str(tmp_path / "langharness.toml")},
        )
        manager.create_instance(
            "configs-plugin-factory",
            "langharness_config.plugins.configs",
            ROOT_SCOPE_ID,
        )
        configs = manager.get_service(SPEC_CONFIGS)
        assert configs is not None
        assert configs.get_default_provider()["model"] == "default-model"
    finally:
        manager.stop()


def test_configs_list_providers_skips_invalid_entries() -> None:
    configs = ConfigsPlugin()
    configs._providers = [
        SimpleNamespace(
            get_config=lambda: {
                "providers": {
                    "good": {"model": "demo", "api_key": "k"},
                    "broken": {"model": "demo", "api_key": "k", "protocol": "ftp"},
                }
            }
        )
    ]

    assert configs.list_providers() == ["good"]


def test_configs_get_default_provider_returns_validated_section() -> None:
    configs = ConfigsPlugin()
    configs._providers = [
        SimpleNamespace(
            get_config=lambda: {
                "providers": {
                    "default": {
                        "model": "default-model",
                        "api_key": "key",
                        "base_url": "https://example.test/v1",
                    }
                }
            }
        )
    ]

    assert configs.get_default_provider() == {
        "model": "default-model",
        "api_key": "key",
        "base_url": "https://example.test/v1",
        "protocol": "chat",
    }


def test_configs_get_default_provider_raises_when_missing() -> None:
    configs = ConfigsPlugin()
    configs._providers = [
        SimpleNamespace(get_config=lambda: {"providers": {"demo": {"model": "m"}}})
    ]

    with pytest.raises(ValueError, match="No default model is configured"):
        configs.get_default_provider()


def test_configs_get_default_provider_raises_when_section_empty() -> None:
    configs = ConfigsPlugin()
    configs._providers = [
        SimpleNamespace(get_config=lambda: {"providers": {"default": {}}})
    ]

    with pytest.raises(ValueError, match="No default model is configured"):
        configs.get_default_provider()


def test_configs_get_default_provider_raises_when_invalid() -> None:
    configs = ConfigsPlugin()
    configs._providers = [
        SimpleNamespace(
            get_config=lambda: {
                "providers": {"default": {"model": "", "api_key": "k"}}
            }
        )
    ]

    with pytest.raises(ValueError, match="requires a non-empty model"):
        configs.get_default_provider()


def test_configs_get_provider_rejects_reserved_default_name() -> None:
    configs = ConfigsPlugin()
    configs._providers = [
        SimpleNamespace(
            get_config=lambda: {
                "providers": {"default": {"model": "m", "api_key": "k"}}
            }
        )
    ]

    with pytest.raises(ValueError, match="reserved"):
        configs.get_provider("default")


def test_configs_list_providers_excludes_reserved_default() -> None:
    configs = ConfigsPlugin()
    configs._providers = [
        SimpleNamespace(
            get_config=lambda: {
                "providers": {
                    "default": {"model": "m", "api_key": "k"},
                    "demo": {"model": "m", "api_key": "k"},
                }
            }
        )
    ]

    assert configs.list_providers() == ["demo"]

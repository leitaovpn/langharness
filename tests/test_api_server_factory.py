"""Tests for the standalone API server factory."""
# mypy: ignore-errors
# pyright: reportArgumentType=false

from __future__ import annotations

from types import SimpleNamespace

import pytest

import langharness_api.common.server as server_module


def test_create_app_uses_plugin_manager_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    monkeypatch.setattr(server_module, "_MANAGER", None)
    monkeypatch.setenv("LANG_HARNESS_DIR", str(tmp_path))

    configs = SimpleNamespace(get=lambda section, key: None)
    api_server = SimpleNamespace(build_app=lambda: SimpleNamespace(title="ok"))
    manager = SimpleNamespace(
        get_service=lambda spec: configs if spec == "configs" else api_server,
        register_runtime_service=lambda specification, service: None,
    )

    def fake_manager(registry):
        manager.start = lambda: None
        manager.install_descriptor = lambda descriptor, **kwargs: None
        manager.ensure_instance = (
            lambda factory, module, scope, *, properties, enabled: None
        )
        manager.bind_state = lambda store, history: None
        manager.discover = lambda: None
        return manager

    monkeypatch.setattr(server_module, "PluginManager", fake_manager)
    monkeypatch.setattr(server_module, "PluginRegistry", lambda: SimpleNamespace())
    monkeypatch.setattr(
        server_module,
        "RuntimeMutationCoordinator",
        lambda manager, discovery: SimpleNamespace(
            rescan=lambda: None, restore=lambda: None
        ),
    )

    app = server_module.create_app()
    assert app.title == "ok"
    assert server_module._MANAGER is manager

    second_app = server_module.create_app()
    assert second_app.title == "ok"


def test_apply_agent_configs_without_files_is_a_noop(tmp_path) -> None:
    manager = SimpleNamespace(get_service=lambda spec: None)
    server_module._apply_agent_configs(manager, str(tmp_path))


def test_apply_agent_configs_forwards_stored_scopes(tmp_path) -> None:
    from langharness_plugin.config_store import PluginConfigStore, scope_path

    store = PluginConfigStore.load(scope_path(str(tmp_path), "agent:alpha"), "agent:alpha")
    store.update({"tools": {"enabled": False}}, actor="cli")
    applied = []
    directory = SimpleNamespace(
        apply_agent_config=lambda agent_id, plugins: applied.append((agent_id, plugins))
    )
    manager = SimpleNamespace(get_service=lambda spec: directory)

    server_module._apply_agent_configs(manager, str(tmp_path))

    assert applied == [("alpha", {"tools": {"enabled": False}})]


def test_apply_agent_configs_without_directory_is_a_noop(tmp_path) -> None:
    from langharness_plugin.config_store import PluginConfigStore, scope_path

    store = PluginConfigStore.load(scope_path(str(tmp_path), "agent:alpha"), "agent:alpha")
    store.update({"tools": {"enabled": False}}, actor="cli")
    manager = SimpleNamespace(get_service=lambda spec: None)

    server_module._apply_agent_configs(manager, str(tmp_path))

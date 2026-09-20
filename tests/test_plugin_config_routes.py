"""Tests for the plugin configuration route."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from langharness_api.plugins.routes.plugins import PluginsRoutePlugin
from langharness_plugin.config_store import PluginConfigStore, scope_path


class FakeScopeRegistrar:
    def __init__(self) -> None:
        self.applied: list[dict[str, Any]] = []

    def instantiate_instance(self, descriptor: Any) -> None:
        return None

    def kill_instance(self, instance: str) -> None:
        return None

    def find_service(self, specification: str, filter: str | None = None) -> Any:
        return None

    def installed_modules(self) -> set[str]:
        return set()

    def apply_config(self, overrides: dict[str, Any]) -> dict[str, list[str]]:
        self.applied.append(overrides)
        return {"applied": sorted(overrides), "restart_required": []}


class FakeDirectory:
    def __init__(self) -> None:
        self.configs: list[tuple[str, dict[str, Any]]] = []

    def list_agents(self) -> list[dict[str, Any]]:
        return []

    def get_loop(self, agent_id: str) -> Any:
        return None

    def ensure_plugin_instance(
        self, agent_id: str, plugin: str, properties: dict[str, Any]
    ) -> None:
        return None

    def binding_properties(self, agent_id: str, plugin: str) -> dict[str, Any]:
        return {}

    def apply_agent_config(self, agent_id: str, plugins: dict[str, Any]) -> None:
        self.configs.append((agent_id, plugins))

    def reload(self, agent_id: str | None = None) -> None:
        return None


class FakeContribution:
    def __init__(self, id: str, target: str) -> None:
        self.id = id
        self.target = target
        self.descriptor = type(
            "Descriptor",
            (),
            {
                "name": f"fake-{id}",
                "module": f"dynamic_plugins.{id}",
                "factory": f"fake-{id}-factory",
                "specification": "plugin.tool_export.target",
                "description": "Fake contribution for route tests.",
            },
        )()


class FakePackage:
    def __init__(self, id: str, version: str) -> None:
        self.id = id
        self.version = version
        self.contributions = (FakeContribution("echo", "server"),)


class FakeRegistration:
    def __init__(self, name: str) -> None:
        self.instance = f"uuid-{name}"
        self.factory = f"{name}-factory"
        self.module = "dynamic_plugins.echo"
        self.package_id = "example.package"
        self.contribution_id = "echo"
        self.package_version = "1"
        self.scope_id = "server"
        self.enabled = True
        self.status = "installed"


class FakeDiscoveryResult:
    def __init__(self) -> None:
        self.packages = (FakePackage("example.package", "1"),)
        self.failures = ()


class FakeDynamic:
    def __init__(self) -> None:
        self.packages = (FakePackage("example.package", "1"),)
        self.failures = ()
        self.registrations_list = (FakeRegistration("echo"),)
        self.installs: list[tuple[str, str, str | None]] = []
        self.enabled: list[tuple[str, bool, str | None]] = []
        self.uninstalled: list[tuple[str, str | None]] = []
        self.upgraded: list[tuple[str, str | None]] = []

    def discovered(self):
        return self.packages

    def rescan(self):
        return self

    def registrations(self):
        return self.registrations_list

    def install(self, package_id, contribution_id, *, scope_id=None):
        self.installs.append((package_id, contribution_id, scope_id))
        return FakeRegistration(f"{contribution_id}@{scope_id or 'server'}")

    def set_enabled(self, name, enabled, *, scope_id=None):
        self.enabled.append((name, enabled, str(scope_id) if scope_id else None))
        return FakeRegistration(name)

    def update_properties(self, name, properties, *, scope_id=None):
        self.properties = (name, properties, str(scope_id) if scope_id else None)
        return FakeRegistration(name)

    def uninstall(self, name, *, scope_id=None):
        self.uninstalled.append((name, str(scope_id) if scope_id else None))

    def upgrade(self, name, *, scope_id=None):
        self.upgraded.append((name, str(scope_id) if scope_id else None))
        return FakeRegistration(name)

    def scopes(self):
        return ()


def make_plugin(tmp_path: Path) -> PluginsRoutePlugin:
    plugin = PluginsRoutePlugin()
    plugin._config_dir = str(tmp_path)
    plugin._scope = FakeScopeRegistrar()
    plugin._directory = FakeDirectory()
    plugin._dynamic = FakeDynamic()
    return plugin


def make_client(plugin: PluginsRoutePlugin) -> TestClient:
    app = FastAPI()
    app.include_router(plugin.get_router())
    return TestClient(app)


def test_get_plugins_returns_current_scope_config(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)
    store = PluginConfigStore.load(scope_path(str(tmp_path), "api"), "api")
    store.update(
        {"api-rate-limit": {"enabled": True, "properties": {"plugin.limit": 5}}},
        actor="cli",
    )

    response = make_client(plugin).get("/plugins", params={"scope": "api"})

    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "api"
    assert body["version"] == 2
    assert body["plugins"] == {
        "api-rate-limit": {"enabled": True, "properties": {"plugin.limit": 5}}
    }


def test_get_plugins_creates_missing_scope_file(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)
    response = make_client(plugin).get("/plugins", params={"scope": "agent:alpha"})
    assert response.status_code == 200
    assert response.json()["plugins"] == {}
    assert scope_path(str(tmp_path), "agent:alpha").exists()


def test_get_plugins_rejects_unknown_scope(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)
    assert make_client(plugin).get("/plugins", params={"scope": "wat"}).status_code == 400


def test_put_plugins_applies_api_scope_overrides(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)

    response = make_client(plugin).put(
        "/plugins",
        params={"scope": "api"},
        json={
            "actor": "cli",
            "plugins": {
                "api-rate-limit": {"enabled": True, "properties": {"plugin.limit": 9}}
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["applied"] == ["api-rate-limit"]
    assert body["restart_required"] == []
    assert body["version"] == 2
    assert plugin._scope.applied == [
        {"api-rate-limit": {"enabled": True, "properties": {"plugin.limit": 9}}}
    ]
    stored = json.loads(scope_path(str(tmp_path), "api").read_text(encoding="utf-8"))
    assert stored["history"][-1]["actor"] == "cli"


def test_put_plugins_reroutes_agent_scope_to_the_directory(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)

    response = make_client(plugin).put(
        "/plugins",
        params={"scope": "agent:alpha"},
        json={"actor": "cli", "plugins": {"tools": {"enabled": False}}},
    )

    assert response.status_code == 200
    assert response.json()["applied"] == ["tools"]
    assert plugin._directory.configs == [("alpha", {"tools": {"enabled": False}})]
    assert plugin._scope.applied == []


def test_put_plugins_reports_restart_for_cli_scope(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)

    response = make_client(plugin).put(
        "/plugins",
        params={"scope": "cli"},
        json={"actor": "cli", "plugins": {"cli-rich-renderer": {"enabled": False}}},
    )

    assert response.status_code == 200
    assert response.json()["applied"] == []
    assert response.json()["restart_required"] == ["cli-rich-renderer"]
    assert plugin._scope.applied == []


def test_put_plugins_reports_unknown_plugin(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)

    def reject(overrides: dict[str, Any]) -> dict[str, list[str]]:
        raise ValueError("Unknown plugin: ghost")

    plugin._scope.apply_config = reject
    response = make_client(plugin).put(
        "/plugins",
        params={"scope": "api"},
        json={"actor": "cli", "plugins": {"ghost": {"enabled": True}}},
    )

    assert response.status_code == 400
    assert "ghost" in response.json()["detail"]
    store = PluginConfigStore.load(scope_path(str(tmp_path), "api"), "api")
    assert store.plugins() == {}
    assert store.current_seq() == 1


def test_put_plugins_requires_plugins_payload(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)
    response = make_client(plugin).put(
        "/plugins", params={"scope": "api"}, json={"actor": "cli"}
    )
    assert response.status_code == 400


def test_history_and_rollback_round_trip(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)
    client = make_client(plugin)
    client.put(
        "/plugins",
        params={"scope": "api"},
        json={"actor": "cli", "plugins": {"api-auth": {"enabled": True}}},
    )
    client.put(
        "/plugins",
        params={"scope": "api"},
        json={"actor": "cli", "plugins": {"api-auth": {"enabled": False}}},
    )

    history = client.get("/plugins/history", params={"scope": "api"}).json()["history"]
    assert [entry["seq"] for entry in history] == [1, 2, 3]
    assert [entry["action"] for entry in history] == ["init", "set", "set"]

    rollback = client.post(
        "/plugins/rollback",
        params={"scope": "api"},
        json={"actor": "cli", "seq": 2},
    )

    assert rollback.status_code == 200
    assert rollback.json()["version"] == 4
    assert rollback.json()["applied"] == ["api-auth"]
    current = client.get("/plugins", params={"scope": "api"}).json()["plugins"]
    assert current == {"api-auth": {"enabled": True}}
    last = client.get("/plugins/history", params={"scope": "api"}).json()["history"][-1]
    assert last["action"] == "rollback"
    assert last["target_seq"] == 2


def test_rollback_rejects_unknown_version(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)
    response = make_client(plugin).post(
        "/plugins/rollback",
        params={"scope": "api"},
        json={"actor": "cli", "seq": 99},
    )
    assert response.status_code == 404


def test_plugin_info(tmp_path: Path) -> None:
    assert make_plugin(tmp_path).get_plugin_info() == {
        "name": "plugins",
        "version": "1.0.0",
    }


def test_put_plugins_rejects_unknown_scope(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)
    response = make_client(plugin).put(
        "/plugins", params={"scope": "wat"}, json={"actor": "cli", "plugins": {}}
    )
    assert response.status_code == 400


def test_agent_scope_requires_directory(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)
    plugin._directory = None
    response = make_client(plugin).put(
        "/plugins",
        params={"scope": "agent:alpha"},
        json={"actor": "cli", "plugins": {"tools": {"enabled": False}}},
    )
    assert response.status_code == 503


def test_agent_scope_reports_directory_errors(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)

    def reject(agent_id: str, plugins: dict[str, Any]) -> None:
        raise ValueError("Unknown agent: alpha")

    plugin._directory.apply_agent_config = reject
    response = make_client(plugin).put(
        "/plugins",
        params={"scope": "agent:alpha"},
        json={"actor": "cli", "plugins": {"tools": {"enabled": False}}},
    )
    assert response.status_code == 400
    assert "Unknown agent" in response.json()["detail"]


def test_api_scope_requires_scope_service(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)
    plugin._scope = None
    response = make_client(plugin).put(
        "/plugins",
        params={"scope": "api"},
        json={"actor": "cli", "plugins": {"api-auth": {"enabled": True}}},
    )
    assert response.status_code == 503


def test_route_bind_and_unbind_callbacks(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)
    scope = plugin._scope
    directory = plugin._directory

    plugin._on_scope_bind("_scope", scope, None)
    plugin._on_directory_bind("_directory", directory, None)
    plugin._on_scope_unbind("_scope", scope, None)
    plugin._on_directory_unbind("_directory", directory, None)

    # iPOPO clears the injected field before firing the callback, so the route
    # keeps working once the service is bound again.
    plugin._on_scope_bind("_scope", scope, None)
    plugin._on_directory_bind("_directory", directory, None)
    assert make_client(plugin).get("/plugins", params={"scope": "api"}).status_code == 200


def test_dynamic_discovered_and_rescan(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)
    client = make_client(plugin)

    discovered = client.get("/plugins/discovered")
    assert discovered.status_code == 200
    assert discovered.json()["packages"] == [
        {
            "id": "example.package",
            "version": "1",
            "source": "external",
            "contributions": [
                {
                    "id": "echo",
                    "name": "fake-echo",
                    "target": "server",
                    "module": "dynamic_plugins.echo",
                    "factory": "fake-echo-factory",
                    "specification": "plugin.tool_export.target",
                    "description": "Fake contribution for route tests.",
                }
            ],
        }
    ]

    rescan = client.post("/plugins/rescan")
    assert rescan.status_code == 200
    assert rescan.json()["packages"][0]["id"] == "example.package"


def test_dynamic_runtime_install_enable_upgrade_uninstall(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)
    client = make_client(plugin)

    runtime = client.get("/plugins/runtime")
    assert runtime.status_code == 200
    assert runtime.json()["plugins"][0]["factory"] == "echo-factory"

    install = client.post(
        "/plugins/install",
        json={
            "package_id": "example.package",
            "contribution_id": "echo",
            "scope_id": "server",
        },
    )
    assert install.status_code == 200
    assert install.json()["factory"] == "echo@server-factory"
    assert install.json()["instance"] == "uuid-echo@server"
    assert plugin._dynamic.installs == [("example.package", "echo", "server")]

    enable = client.put(
        "/plugins/runtime/echo/enabled",
        json={"enabled": False},
        params={"scope": "server"},
    )
    assert enable.status_code == 200
    assert plugin._dynamic.enabled == [("echo", False, "server")]

    properties = client.put(
        "/plugins/runtime/echo/properties",
        json={"properties": {"plugin.value": "x"}},
        params={"scope": "server"},
    )
    assert properties.status_code == 200
    assert plugin._dynamic.properties == ("echo", {"plugin.value": "x"}, "server")

    upgrade = client.post("/plugins/runtime/echo/upgrade", params={"scope": "server"})
    assert upgrade.status_code == 200
    assert plugin._dynamic.upgraded == [("echo", "server")]

    delete = client.delete("/plugins/runtime/echo", params={"scope": "server"})
    assert delete.status_code == 200
    assert delete.json() == {"removed": True}
    assert plugin._dynamic.uninstalled == [("echo", "server")]


def test_runtime_plugins_filters_by_scope(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)
    agent_registration = FakeRegistration("agent-echo")
    agent_registration.scope_id = "agent"
    plugin._dynamic.registrations_list = (
        FakeRegistration("server-echo"),
        agent_registration,
    )

    response = make_client(plugin).get("/plugins/runtime", params={"scope": "server"})

    assert response.status_code == 200
    assert [item["factory"] for item in response.json()["plugins"]] == [
        "server-echo-factory"
    ]


def test_dynamic_endpoints_require_dynamic_manager(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)
    plugin._dynamic = None
    client = make_client(plugin)

    for method, path, kwargs in [
        ("get", "/plugins/discovered", {}),
        ("post", "/plugins/rescan", {}),
        ("get", "/plugins/runtime", {}),
        ("post", "/plugins/install", {"json": {"package_id": "p", "contribution_id": "c", "scope_id": "server"}}),
        ("put", "/plugins/runtime/x/enabled", {"json": {"enabled": True}, "params": {"scope": "server"}}),
        ("delete", "/plugins/runtime/x", {"params": {"scope": "server"}}),
        ("post", "/plugins/runtime/x/upgrade", {"params": {"scope": "server"}}),
    ]:
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code == 503


def test_dynamic_endpoints_report_errors(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)

    def fail(*args, **kwargs):
        raise KeyError("missing")

    plugin._dynamic.install = fail
    plugin._dynamic.set_enabled = fail
    plugin._dynamic.uninstall = fail
    plugin._dynamic.upgrade = fail
    client = make_client(plugin)

    install_response = client.post(
        "/plugins/install",
        json={"package_id": "p", "contribution_id": "c", "scope_id": "server"},
    )
    assert install_response.status_code == 400
    assert install_response.json()["detail"] == "'missing'"
    assert install_response.headers["x-error-code"] == "PLUGIN_NOT_FOUND"
    assert install_response.headers["x-error-type"] == "KeyError"
    assert client.put(
        "/plugins/runtime/x/enabled",
        json={"enabled": True},
        params={"scope": "server"},
    ).status_code == 404
    assert client.delete(
        "/plugins/runtime/x", params={"scope": "server"}
    ).status_code == 404
    assert client.post(
        "/plugins/runtime/x/upgrade", params={"scope": "server"}
    ).status_code == 404


def test_runtime_mutations_require_scope(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)
    client = make_client(plugin)

    assert client.put(
        "/plugins/runtime/x/enabled", json={"enabled": True}
    ).status_code == 422
    assert client.put(
        "/plugins/runtime/x/properties", json={"properties": {}}
    ).status_code == 422
    assert client.delete("/plugins/runtime/x").status_code == 422
    assert client.post("/plugins/runtime/x/upgrade").status_code == 422


def test_runtime_mutations_reject_unknown_scope(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)
    client = make_client(plugin)

    response = client.put(
        "/plugins/runtime/x/enabled",
        json={"enabled": True},
        params={"scope": "mars"},
    )
    assert response.status_code == 400
    assert "Unknown runtime scope: mars" in response.json()["detail"]


def test_runtime_list_rejects_unknown_scope(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)
    client = make_client(plugin)

    response = client.get("/plugins/runtime", params={"scope": "mars"})
    assert response.status_code == 400
    assert "Unknown runtime scope: mars" in response.json()["detail"]


def test_runtime_mutation_not_found_in_scope_returns_404(tmp_path: Path) -> None:
    plugin = make_plugin(tmp_path)

    def fail(*args, **kwargs):
        raise KeyError("plugin echo not found in scope server")

    plugin._dynamic.set_enabled = fail
    client = make_client(plugin)

    response = client.put(
        "/plugins/runtime/echo/enabled",
        json={"enabled": False},
        params={"scope": "server"},
    )
    assert response.status_code == 404
    assert "not found in scope" in response.json()["detail"]
    assert response.headers["x-error-code"] == "PLUGIN_NOT_FOUND"

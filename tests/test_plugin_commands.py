"""Tests for the interactive plugin configuration commands."""

from __future__ import annotations

from argparse import ArgumentParser
from typing import Any

import httpx
import pytest

from langharness_cli.plugins.commands.plugins import PluginCommandPlugin

SCOPE_CONFIG = {
    "scope": "api",
    "version": 3,
    "plugins": {
        "api-rate-limit": {"enabled": True, "properties": {"plugin.limit": 100}},
        "api-auth": {"enabled": True, "properties": {}},
    },
}

HISTORY = {
    "scope": "api",
    "version": 3,
    "history": [
        {"seq": 1, "action": "init", "actor": "system", "ts": "t1"},
        {"seq": 2, "action": "set", "actor": "cli", "ts": "t2"},
        {"seq": 3, "action": "rollback", "actor": "cli", "ts": "t3", "target_seq": 1},
    ],
}

APPLY_RESULT = {
    "scope": "api",
    "version": 4,
    "applied": ["api-rate-limit"],
    "restart_required": ["api-log"],
}


class Context:
    def __init__(self) -> None:
        self.base_url = "http://api"
        self.token = "secret"
        self.user_id = "local_user"
        self.agent_id = "simple_agent"
        self.session_id = "s1"

    def refresh_status(self) -> None:
        return None


class Response:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://api/plugins")
            raise httpx.HTTPStatusError(
                str(self._payload),
                request=request,
                response=httpx.Response(self.status_code, json=self._payload),
            )


def make_plugin() -> PluginCommandPlugin:
    plugin = PluginCommandPlugin()
    plugin._locale = "en"
    return plugin


def handler_for(name: str) -> Any:
    commands = {item.name: item for item in make_plugin().get_interactive_commands()}
    return commands[name].handler


def test_plugin_exposes_plugin_commands() -> None:
    plugin = make_plugin()
    assert [item.name for item in plugin.get_interactive_commands()] == ["plugins"]
    assert [item.name for item in plugin.get_commands()] == ["plugins"]
    assert plugin.get_plugin_info() == {"name": "plugin-command", "version": "1.0.0"}


def test_noninteractive_plugin_install_calls_runtime_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = make_plugin()
    command = plugin.get_commands()[0]
    parser = ArgumentParser()
    assert command.add_arguments is not None
    command.add_arguments(parser)
    args = parser.parse_args(
        ["install", "example.package", "echo", "--scope", "agent:a"]
    )
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_post(url: str, **kwargs: Any) -> Response:
        calls.append((url, kwargs))
        return Response({"status": "installed"})

    monkeypatch.setattr(httpx, "post", fake_post)

    assert command.handler(args) == 0
    assert calls[0][0].endswith("/plugins/install")
    assert calls[0][1]["json"] == {
        "package_id": "example.package",
        "contribution_id": "echo",
        "scope_id": "agent:a",
    }


def run_command(
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
    *,
    methods: dict[str, Any],
) -> tuple[int, list[tuple[str, str, dict[str, Any]]]]:
    plugin = make_plugin()
    command = plugin.get_commands()[0]
    parser = ArgumentParser()
    assert command.add_arguments is not None
    command.add_arguments(parser)
    parsed = parser.parse_args(args)
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake_request(method: str) -> Any:
        def handler(url: str, **kwargs: Any) -> Response:
            calls.append((method, url, kwargs))
            return Response(methods.get(method, {"status": "ok"}))
        return handler

    monkeypatch.setattr(httpx, "get", fake_request("get"))
    monkeypatch.setattr(httpx, "post", fake_request("post"))
    monkeypatch.setattr(httpx, "put", fake_request("put"))
    monkeypatch.setattr(httpx, "delete", fake_request("delete"))
    return command.handler(parsed), calls


def test_noninteractive_discover_and_list_runtime(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, calls = run_command(
        monkeypatch,
        ["discover"],
        methods={"post": {"packages": []}},
    )
    assert code == 0
    assert len(calls) == 2
    assert calls[0][0] == "post"
    assert calls[0][1].endswith("/plugins/rescan")
    assert calls[1][0] == "get"
    assert calls[1][1].endswith("/plugins/discovered")

    capsys.readouterr()
    code, calls = run_command(
        monkeypatch,
        ["list"],
        methods={"get": {"plugins": []}},
    )
    assert code == 0
    assert calls[0][0] == "get"
    assert calls[0][1].endswith("/plugins/runtime")


def test_noninteractive_config_scope_reads_plugin_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code, calls = run_command(
        monkeypatch,
        ["config", "--scope", "cli"],
        methods={"get": {"plugins": {}}},
    )
    assert code == 0
    assert calls[0][0] == "get"
    assert calls[0][1].endswith("/plugins")
    assert calls[0][2]["params"] == {"scope": "cli"}


def test_noninteractive_enable_disable_upgrade_uninstall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code, calls = run_command(
        monkeypatch,
        ["enable", "api-rate-limit", "--scope", "server"],
        methods={"put": {"enabled": True}},
    )
    assert code == 0
    assert calls[0] == (
        "put",
        "http://127.0.0.1:11534/plugins/runtime/api-rate-limit/enabled",
        {
            "json": {"enabled": True},
            "headers": {"Authorization": "Bearer secret"},
            "timeout": 10.0,
            "params": {"scope": "server"},
        },
    )

    code, calls = run_command(
        monkeypatch,
        ["disable", "api-rate-limit", "--scope", "server"],
        methods={"put": {"enabled": False}},
    )
    assert code == 0
    assert calls[0][2]["json"] == {"enabled": False}
    assert calls[0][2]["params"] == {"scope": "server"}

    code, calls = run_command(
        monkeypatch,
        ["upgrade", "api-rate-limit", "--scope", "server"],
        methods={"post": {"status": "upgraded"}},
    )
    assert code == 0
    assert calls[0][0] == "post"
    assert calls[0][1].endswith("/plugins/runtime/api-rate-limit/upgrade")
    assert calls[0][2]["params"] == {"scope": "server"}

    code, calls = run_command(
        monkeypatch,
        ["uninstall", "api-rate-limit", "--scope", "server"],
        methods={"delete": {"removed": True}},
    )
    assert code == 0
    assert calls[0][0] == "delete"
    assert calls[0][1].endswith("/plugins/runtime/api-rate-limit")
    assert calls[0][2]["params"] == {"scope": "server"}


def test_noninteractive_mutation_rejects_wrong_arguments(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for args, message in [
        (["install", "only-package"], "install requires"),
        (["enable"], "enable requires"),
        (["upgrade"], "upgrade requires"),
        (["uninstall"], "uninstall requires"),
    ]:
        code, _ = run_command(monkeypatch, args, methods={})
        assert code == 1
        assert message in capsys.readouterr().out


def test_noninteractive_mutation_reports_http_failures(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plugin = make_plugin()
    command = plugin.get_commands()[0]
    parser = ArgumentParser()
    assert command.add_arguments is not None
    command.add_arguments(parser)
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: Response({"detail": "boom"}, 500),
    )
    assert command.handler(parser.parse_args(["discover"])) == 1
    output = capsys.readouterr().out
    assert "Plugin request failed" in output
    assert "boom" in output


def test_plugins_lists_runtime_plugins_across_all_scopes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> Response:
        captured["url"] = url
        captured.update(kwargs)
        return Response(
            {
                "plugins": [
                    {
                        "factory": "api-factory",
                        "instance": "uuid-1",
                        "package_id": "builtin.api",
                        "contribution_id": "server",
                        "package_version": "1.0.0",
                        "scope_id": "agent:a",
                        "enabled": True,
                        "status": "installed",
                    },
                    {
                        "factory": "api-web",
                        "instance": "uuid-2",
                        "package_id": "builtin.api",
                        "contribution_id": "ui",
                        "package_version": "1.0.0",
                        "scope_id": "ui",
                        "enabled": True,
                        "status": "installed",
                    },
                ]
            }
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    assert handler_for("plugins")(Context(), "list") is False

    output = capsys.readouterr().out
    assert "Runtime plugins · all scopes" in output
    assert "Scope" in output
    assert "api-factory" in output
    assert "agent:a" in output
    assert "api-web" in output
    assert "builtin.api/server" in output
    assert "uuid-1" in output
    assert captured["url"].endswith("/plugins/runtime")
    assert captured["params"] is None


def test_plugins_lists_runtime_plugins_for_parent_agent_scope(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> Response:
        captured["url"] = url
        captured.update(kwargs)
        return Response({"plugins": []})

    monkeypatch.setattr(httpx, "get", fake_get)
    handler_for("plugins")(Context(), "list agent")
    assert captured["params"] == {"scope": "agent"}
    assert "no registered plugins" in capsys.readouterr().out


def test_plugins_config_lists_persisted_overrides(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> Response:
        captured["url"] = url
        captured.update(kwargs)
        return Response(SCOPE_CONFIG)

    monkeypatch.setattr(httpx, "get", fake_get)

    assert handler_for("plugins")(Context(), "config api") is False

    output = capsys.readouterr().out
    assert "Configuration overrides · scope api · version 3" in output
    assert "api-rate-limit" in output
    assert "plugin.limit=100" in output
    assert captured["url"].endswith("/plugins")
    assert captured["params"] == {"scope": "api"}


def test_plugins_discover_rescans_and_prints_catalog(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake_post(url: str, **kwargs: Any) -> Response:
        calls.append(("post", url, kwargs))
        return Response({"packages": []})

    def fake_get(url: str, **kwargs: Any) -> Response:
        calls.append(("get", url, kwargs))
        return Response(
            {
                "packages": [
                    {
                        "id": "builtin.api",
                        "version": "1.0.0",
                        "source": "builtin",
                        "contributions": [
                            {
                                "id": "auth",
                                "name": "api-auth",
                                "specification": "api.plugin.auth",
                                "module": "langharness_api.plugins.auth.auth",
                            }
                        ],
                    }
                ]
            }
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)

    assert handler_for("plugins")(Context(), "discover") is False

    assert calls[0][0] == "post"
    assert calls[0][1].endswith("/plugins/rescan")
    assert calls[1][0] == "get"
    assert calls[1][1].endswith("/plugins/discovered")
    output = capsys.readouterr().out
    assert "Discovered plugins" in output
    assert "Package" in output
    assert "builtin" in output
    assert "api-auth" in output
    assert "Spec" in output
    assert "Module" in output


def test_plugins_install_posts_dynamic_install(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_post(url: str, **kwargs: Any) -> Response:
        calls.append((url, kwargs))
        return Response({"name": "real-echo", "status": "installed"})

    monkeypatch.setattr(httpx, "post", fake_post)

    assert handler_for("plugins")(Context(), "install real.echo echo agent:a") is False

    assert calls[0][0].endswith("/plugins/install")
    assert calls[0][1]["json"] == {
        "package_id": "real.echo",
        "contribution_id": "echo",
        "scope_id": "agent:a",
    }
    output = capsys.readouterr().out
    assert "installed" in output


def test_plugins_uninstall_deletes_dynamic_plugin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_delete(url: str, **kwargs: Any) -> Response:
        calls.append((url, kwargs))
        return Response({"removed": True})

    monkeypatch.setattr(httpx, "delete", fake_delete)

    assert handler_for("plugins")(Context(), "uninstall server real-echo") is False

    assert calls[0][0].endswith("/plugins/runtime/real-echo")
    assert "Removed" in capsys.readouterr().out


def test_plugins_install_and_uninstall_require_arguments(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert handler_for("plugins")(Context(), "install real.echo") is False
    assert "usage" in capsys.readouterr().out.lower()

    assert handler_for("plugins")(Context(), "uninstall") is False
    assert "usage" in capsys.readouterr().out.lower()


def test_plugins_set_merges_with_stored_configuration(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_get(url: str, **kwargs: Any) -> Response:
        return Response(SCOPE_CONFIG)

    def fake_put(url: str, **kwargs: Any) -> Response:
        calls.append((url, kwargs))
        return Response(APPLY_RESULT)

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "put", fake_put)

    assert handler_for("plugins")(Context(), "set api api-rate-limit plugin.limit=5") is False

    payload = calls[0][1]["json"]
    assert payload["plugins"]["api-rate-limit"]["properties"] == {"plugin.limit": 5}
    assert payload["plugins"]["api-auth"] == {"enabled": True, "properties": {}}
    output = capsys.readouterr().out
    assert "Applied" in output
    assert "api-rate-limit" in output
    assert "Restart required" in output
    assert "api-log" in output


def test_plugins_config_disable_sets_configuration_override_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    monkeypatch.setattr(httpx, "get", lambda *a, **k: Response(SCOPE_CONFIG))
    def fake_put(url: str, **kwargs: Any) -> Response:
        calls.append(kwargs)
        return Response(APPLY_RESULT)

    monkeypatch.setattr(httpx, "put", fake_put)

    handler_for("plugins")(Context(), "config disable api api-auth")

    assert calls[0]["json"]["plugins"]["api-auth"]["enabled"] is False


def test_plugins_enable_sets_runtime_plugin_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_put(url: str, **kwargs: Any) -> Response:
        calls.append((url, kwargs))
        return Response(
            {
                "name": "system-prompt-plugin-template",
                "scope_id": "agent",
                "enabled": True,
                "status": "installed",
                "specification": "agent.plugin.system_prompt",
            }
        )

    monkeypatch.setattr(httpx, "put", fake_put)

    handler_for("plugins")(Context(), "enable agent system-prompt-plugin-template")

    assert calls[0][0].endswith(
        "/plugins/runtime/system-prompt-plugin-template/enabled"
    )
    assert calls[0][1]["json"] == {"enabled": True}


def test_plugins_runtime_set_updates_dynamic_properties(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_put(url: str, **kwargs: Any) -> Response:
        calls.append((url, kwargs))
        return Response(
            {
                "name": "system-prompt-plugin-template",
                "scope_id": "agent",
                "enabled": True,
                "status": "installed",
                "specification": "agent.plugin.system_prompt",
            }
        )

    monkeypatch.setattr(httpx, "put", fake_put)

    handler_for("plugins")(
        Context(),
        'runtime set agent system-prompt-plugin-template plugin.system_prompt="你是李明"',
    )

    assert calls[0][0].endswith(
        "/plugins/runtime/system-prompt-plugin-template/properties"
    )
    assert calls[0][1]["json"] == {"properties": {"plugin.system_prompt": "你是李明"}}


def test_plugins_set_rejects_missing_pair(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert handler_for("plugins")(Context(), "set api-auth") is False
    output = capsys.readouterr().out
    assert "Scope is required" in output
    assert "usage" in output.lower()


def test_plugins_set_rejects_malformed_pair(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert handler_for("plugins")(Context(), "set api-auth broken") is False
    output = capsys.readouterr().out
    assert "Scope is required" in output
    assert "usage" in output.lower()


def test_plugins_history_lists_versions(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: Response(HISTORY))
    assert handler_for("plugins")(Context(), "history api") is False
    output = capsys.readouterr().out
    assert "Version" in output
    assert "rollback" in output
    assert "cli" in output
    assert "1" in output


def test_plugins_rollback_posts_the_target_version(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> Response:
        calls.append(kwargs)
        return Response(APPLY_RESULT)

    monkeypatch.setattr(httpx, "post", fake_post)

    assert handler_for("plugins")(Context(), "rollback api 1") is False

    assert calls[0]["json"] == {"seq": 1, "actor": "cli"}
    assert calls[0]["params"] == {"scope": "api"}
    output = capsys.readouterr().out
    assert "Applied" in output
    assert "api-rate-limit" in output


def test_plugins_rollback_requires_version(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert handler_for("plugins")(Context(), "rollback") is False
    assert "usage" in capsys.readouterr().out.lower()


def test_plugins_reports_http_failures(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: Response({"detail": "boom"}, 500))
    assert handler_for("plugins")(Context(), "list") is False
    output = capsys.readouterr().out
    assert "Plugin request failed" in output
    assert "boom" in output


def test_plugins_without_arguments_prints_usage(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert handler_for("plugins")(Context(), "") is False
    assert "usage" in capsys.readouterr().out.lower()


def test_plugins_reports_unknown_action(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert handler_for("plugins")(Context(), "explode") is False
    output = capsys.readouterr().out
    assert "Unknown /plugins action: explode" in output
    assert "usage" in output.lower()


def test_plugins_disable_requires_plugin_name(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert handler_for("plugins")(Context(), "disable") is False
    assert "usage" in capsys.readouterr().out.lower()


def test_plugins_rollback_rejects_non_numeric_version(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert handler_for("plugins")(Context(), "rollback api latest") is False
    assert "usage" in capsys.readouterr().out.lower()


def test_plugins_rollback_requires_scope(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> Response:
        calls.append(kwargs)
        return Response(APPLY_RESULT)

    monkeypatch.setattr(httpx, "post", fake_post)
    assert handler_for("plugins")(Context(), "rollback 2") is False
    assert calls == []
    output = capsys.readouterr().out
    assert "Scope is required" in output
    assert "usage" in output.lower()


def test_plugins_list_without_overrides_and_apply_without_targets(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: Response({"scope": "api", "version": 1, "plugins": {}})
    )
    monkeypatch.setattr(
        httpx,
        "put",
        lambda url, **kwargs: Response({"scope": "api", "version": 2}),
    )
    handler_for("plugins")(Context(), "list")
    handler_for("plugins")(Context(), "set api api-auth plugin.token=x")
    output = capsys.readouterr().out
    assert "no registered plugins" in output
    assert "Applied" in output


def test_noninteractive_runtime_set_requires_scope(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, calls = run_command(
        monkeypatch,
        ["runtime", "set", "api-rate-limit", "plugin.limit=5"],
        methods={},
    )
    assert code == 1
    assert "runtime requires" in capsys.readouterr().out
    assert calls == []


def test_noninteractive_runtime_set_updates_dynamic_properties(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code, calls = run_command(
        monkeypatch,
        ["runtime", "set", "echo", "plugin.value=x", "--scope", "server"],
        methods={"put": {"status": "installed"}},
    )
    assert code == 0
    assert calls[0][0] == "put"
    assert calls[0][1].endswith("/plugins/runtime/echo/properties")
    assert calls[0][2]["params"] == {"scope": "server"}
    assert calls[0][2]["json"] == {"properties": {"plugin.value": "x"}}


def test_noninteractive_mutations_reject_invalid_scope(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for args, message in [
        (["enable", "api-rate-limit", "--scope", "mars"], "enable requires"),
        (["disable", "api-rate-limit", "--scope", "mars"], "disable requires"),
        (["upgrade", "api-rate-limit", "--scope", "mars"], "upgrade requires"),
        (["uninstall", "api-rate-limit", "--scope", "mars"], "uninstall requires"),
        (
            ["runtime", "set", "api-rate-limit", "plugin.limit=5", "--scope", "mars"],
            "runtime requires",
        ),
        (["list", "--scope", "mars"], "list --scope must be"),
        (["config", "--scope", "mars"], "config --scope must be"),
    ]:
        code, _ = run_command(monkeypatch, args, methods={})
        assert code == 1
        assert message in capsys.readouterr().out


def test_noninteractive_rejects_bare_agent_scope(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for args, message in [
        (["enable", "api-rate-limit", "--scope", "agent:"], "enable requires"),
        (["config", "--scope", "agent:"], "config --scope must be"),
    ]:
        code, _ = run_command(monkeypatch, args, methods={})
        assert code == 1
        assert message in capsys.readouterr().out


def test_plugins_history_requires_scope(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_get(url: str, **kwargs: Any) -> Response:
        calls.append((url, kwargs))
        return Response(HISTORY)

    monkeypatch.setattr(httpx, "get", fake_get)
    assert handler_for("plugins")(Context(), "history") is False
    assert calls == []
    output = capsys.readouterr().out
    assert "Scope is required" in output


def test_plugins_history_rejects_unknown_scope(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: Response(HISTORY))
    assert handler_for("plugins")(Context(), "history mars") is False
    output = capsys.readouterr().out
    assert "Scope is required" in output
    assert "usage" in output.lower()


def test_plugins_runtime_operations_require_valid_scope(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    put_calls: list[tuple[str, dict[str, Any]]] = []
    delete_calls: list[tuple[str, dict[str, Any]]] = []

    def fake_put(url: str, **kwargs: Any) -> Response:
        put_calls.append((url, kwargs))
        return Response({"status": "installed"})

    def fake_delete(url: str, **kwargs: Any) -> Response:
        delete_calls.append((url, kwargs))
        return Response({"removed": True})

    monkeypatch.setattr(httpx, "put", fake_put)
    monkeypatch.setattr(httpx, "delete", fake_delete)
    for line in (
        "enable mars echo",
        "disable mars echo",
        "uninstall mars echo",
        "runtime set mars echo plugin.value=1",
        "enable echo",
    ):
        assert handler_for("plugins")(Context(), line) is False
        assert put_calls == []
        assert delete_calls == []
        assert "Scope is required" in capsys.readouterr().out


def test_plugins_lists_runtime_plugins_for_a_scope_without_scope_column(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_get(url: str, **kwargs: Any) -> Response:
        return Response(
            {
                "plugins": [
                    {
                        "name": "api-server",
                        "package_id": "builtin.api",
                        "contribution_id": "server",
                        "version": "1.0.0",
                        "scope_id": "server",
                        "enabled": True,
                        "status": "installed",
                        "specification": "api.server",
                    }
                ]
            }
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    assert handler_for("plugins")(Context(), "list server") is False

    output = capsys.readouterr().out
    assert "Runtime plugins · scope server" in output
    assert "Scope" not in output


def test_noninteractive_list_without_scope_renders_scope_column(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, calls = run_command(
        monkeypatch,
        ["list"],
        methods={
            "get": {
                "plugins": [
                    {
                        "name": "api-server",
                        "package_id": "builtin.api",
                        "contribution_id": "server",
                        "version": "1.0.0",
                        "scope_id": "agent:a",
                        "enabled": True,
                        "status": "installed",
                        "specification": "api.server",
                    }
                ]
            }
        },
    )
    assert code == 0
    output = capsys.readouterr().out
    assert "Runtime plugins · all scopes" in output
    assert "agent:a" in output

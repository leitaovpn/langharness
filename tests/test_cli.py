"""Unit tests for the plugin-driven CLI."""
# mypy: ignore-errors
# pyright: reportArgumentType=false

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import pytest

import langharness_cli.common.cli as main_module
from langharness_cli.common.runner import CLIRunner
from langharness_cli.contracts import CLICommandProvider, CommandSpec
from langharness_cli.plugins.commands.health import HealthCommandPlugin


def test_command_spec_defaults() -> None:
    def handler(_args) -> int:
        return 0

    spec = CommandSpec(name="health", help="check health", handler=handler)
    assert spec.name == "health"
    assert spec.help == "check health"
    assert spec.handler({}) == 0


def test_runner_registers_and_executes_plugin_command() -> None:
    def handler(args) -> int:
        assert args.message == "hello"
        return 7

    def add_arguments(parser):
        parser.add_argument("--message", default="hello")

    spec = CommandSpec(
        name="greet",
        help="greet",
        handler=handler,
        add_arguments=add_arguments,
    )
    runner = CLIRunner([spec])
    assert runner.run(["greet"]) == 7
    help_text = runner.build_parser().format_help()
    assert "--dir" in help_text
    assert "--log" not in help_text


def test_health_command_provider_conforms() -> None:
    plugin = HealthCommandPlugin()
    assert isinstance(plugin, CLICommandProvider)
    commands = plugin.get_commands()
    assert [command.name for command in commands] == ["health"]


def test_api_server_entrypoint_runs_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    import langharness_api.__main__ as api_main_module
    import langharness_api.common.server as server_module

    app = object()
    captured = {}
    monkeypatch.setattr(server_module, "create_app", lambda: app)
    monkeypatch.setattr(uvicorn, "run", lambda target, **kwargs: captured.update(
        target=target, **kwargs
    ))
    monkeypatch.setattr(
        sys, "argv", ["langharness_api", "--host", "0.0.0.0", "--port", "9123"]
    )
    assert api_main_module.main() == 0
    assert captured == {
        "target": app,
        "host": "0.0.0.0",
        "port": 9123,
        "log_config": None,
    }


def test_health_command_handler(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import langharness_cli.plugins.commands.health as health_module

    class Response:
        status_code = 200

        def json(self):
            return {"status": "ok"}

    urls = []
    monkeypatch.setattr(
        health_module.httpx,
        "get",
        lambda url, *a, **k: urls.append(url) or Response(),
    )

    plugin = HealthCommandPlugin()
    plugin._base_url = "http://api:9000"
    assert plugin._handler(type("Args", (), {})()) == 0
    assert urls == ["http://api:9000/health"]
    assert "{'status': 'ok'}" in capsys.readouterr().out


def test_main_runs_plugin_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["langharness", "health"])

    command = CommandSpec(name="health", help="check", handler=lambda args: 9)
    manager = SimpleNamespace(
        start=lambda: None,
        stop=lambda: None,
        install_descriptor=lambda descriptor, **kw: None,
        create_instance=lambda factory, module, scope, **kw: None,
        get_services=lambda spec: [SimpleNamespace(get_commands=lambda: [command])],
    )
    monkeypatch.setattr(
        main_module,
        "PluginManager",
        lambda registry=None: manager,
    )
    monkeypatch.setattr(
        main_module,
        "PluginRegistry",
        lambda *args: SimpleNamespace(list=lambda: args),
    )
    monkeypatch.setattr(main_module, "CLIRunner", lambda commands: CLIRunner(commands))

    assert main_module.main() == 9


def test_main_runs_interactive_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["langharness"])
    monkeypatch.setenv("LANG_HARNESS_MODEL", "env-model")
    monkeypatch.setenv("LANG_HARNESS_API_KEY", "env-key")
    monkeypatch.setenv("LANG_HARNESS_BASE_URL", "https://models.example/v1")

    provider = SimpleNamespace(
        get_commands=lambda: [],
        get_interactive_commands=lambda: [],
    )
    manager = SimpleNamespace(
        start=lambda: None,
        stop=lambda: None,
        install_descriptor=lambda descriptor, **kw: None,
        create_instance=lambda factory, module, scope, **kw: None,
        get_services=lambda spec: [provider],
    )

    captured = {}

    class FakeInteractive:
        def __init__(self, **kwargs):
            self.commands = kwargs["commands"]
            captured.update(
                model=kwargs["model"],
                provider_name=kwargs["provider_name"],
                model_protocol=kwargs["model_protocol"],
                api_key=kwargs["api_key"],
                model_base_url=kwargs["model_base_url"],
                user_id=kwargs["user_id"],
                agent_id=kwargs["agent_id"],
                session_id=kwargs["session_id"],
            )

        def cmdloop(self):
            self.called = True

    monkeypatch.setattr(main_module, "PluginManager", lambda registry=None: manager)
    monkeypatch.setattr(
        main_module,
        "PluginRegistry",
        lambda *args: SimpleNamespace(list=lambda: args),
    )
    monkeypatch.setattr(main_module, "InteractiveCLIRunner", FakeInteractive)
    monkeypatch.setattr(
        main_module, "resolve_identity", lambda *a, **k: ("resumed-session", "researcher")
    )

    assert main_module.main() == 0
    assert captured == {
        "model": "env-model",
        "provider_name": "environment",
        "model_protocol": "chat",
        "api_key": "env-key",
        "model_base_url": "https://models.example/v1",
        "user_id": "local_user",
        "agent_id": "researcher",
        "session_id": "resumed-session",
    }


def test_main_warns_when_removed_base_url_flag_is_used(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        sys, "argv", ["langharness", "interactive", "--base-url", "http://api:9000"]
    )
    provider = SimpleNamespace(
        get_commands=lambda: [], get_interactive_commands=lambda: []
    )
    manager = SimpleNamespace(
        start=lambda: None,
        stop=lambda: None,
        install_descriptor=lambda descriptor, **kw: None,
        create_instance=lambda factory, module, scope, **kw: None,
        get_services=lambda spec: [provider],
    )
    monkeypatch.setattr(main_module, "PluginManager", lambda registry=None: manager)
    monkeypatch.setattr(
        main_module,
        "PluginRegistry",
        lambda *args: SimpleNamespace(list=lambda: args),
    )
    monkeypatch.setattr(
        main_module,
        "InteractiveCLIRunner",
        lambda **kwargs: SimpleNamespace(cmdloop=lambda: None),
    )
    monkeypatch.setattr(
        main_module, "resolve_identity", lambda *a, **k: ("s", "a")
    )

    assert main_module.main() == 0
    assert "--base-url is removed" in capsys.readouterr().err


def test_interactive_options_parse_identity_flags() -> None:
    options = main_module._interactive_options(
        [
            "--user-id",
            "alice",
            "--agent-id=researcher",
            "--session-id",
            "s1",
            "--new-session",
            "--token",
            "tok",
        ]
    )
    assert options == {
        "token": "tok",
        "user_id": "alice",
        "agent_id": "researcher",
        "session_id": "s1",
        "new_session": True,
    }


def test_interactive_options_default_to_local_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LANG_HARNESS_USER_ID", raising=False)
    options = main_module._interactive_options([])
    assert options["user_id"] == "local_user"
    assert options["agent_id"] is None
    assert options["session_id"] is None
    assert options["new_session"] is False


def test_interactive_options_read_user_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANG_HARNESS_USER_ID", "env_user")
    assert main_module._interactive_options([])["user_id"] == "env_user"


def test_main_forwards_identity_flags_to_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "langharness",
            "interactive",
            "--user-id",
            "alice",
            "--agent-id",
            "researcher",
            "--session-id",
            "pinned",
            "--new-session",
        ],
    )
    manager = SimpleNamespace(
        start=lambda: None,
        stop=lambda: None,
        install_descriptor=lambda descriptor, **kw: None,
        create_instance=lambda factory, module, scope, **kw: None,
        get_services=lambda spec: [],
    )
    monkeypatch.setattr(main_module, "PluginManager", lambda registry=None: manager)
    captured: dict[str, Any] = {}

    def fake_resolve(base_url, token, user_id, **kwargs):
        captured.update(
            base_url=base_url, user_id=user_id, token=token, **kwargs
        )
        return "pinned", "researcher"

    monkeypatch.setattr(main_module, "resolve_identity", fake_resolve)
    runner_kwargs: dict[str, Any] = {}

    class FakeInteractive:
        def __init__(self, **kwargs):
            runner_kwargs.update(kwargs)

        def cmdloop(self):
            return None

    monkeypatch.setattr(main_module, "InteractiveCLIRunner", FakeInteractive)

    assert main_module.main() == 0
    assert captured == {
        "base_url": "http://127.0.0.1:11534",
        "token": "secret",
        "user_id": "alice",
        "agent_id": "researcher",
        "session_id": "pinned",
        "new_session": True,
    }
    assert runner_kwargs["user_id"] == "alice"
    assert runner_kwargs["agent_id"] == "researcher"
    assert runner_kwargs["session_id"] == "pinned"


def test_main_passes_locale_flag_to_plugins_and_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LANG_HARNESS_LOCALE", raising=False)
    monkeypatch.setattr(sys, "argv", ["langharness", "interactive", "--locale", "zh"])
    created = []
    manager = SimpleNamespace(
        start=lambda: None,
        stop=lambda: None,
        install_descriptor=lambda descriptor, **kw: None,
        create_instance=lambda factory, module, scope, **kw: created.append(
            (factory, kw.get("properties"))
        ),
        get_services=lambda spec: [],
    )
    monkeypatch.setattr(main_module, "PluginManager", lambda registry=None: manager)
    captured = {}

    class FakeInteractive:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def cmdloop(self):
            return None

    monkeypatch.setattr(main_module, "InteractiveCLIRunner", FakeInteractive)
    assert main_module.main() == 0
    by_factory = dict(created)
    assert by_factory["rich-cli-renderer-factory"] == {"plugin.ui.locale": "zh"}
    assert by_factory["cli-shell-command-factory"] == {"plugin.ui.locale": "zh"}
    assert captured["locale"] == "zh"


def test_main_installs_shell_command_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["langharness"])
    installed = []
    manager = SimpleNamespace(
        start=lambda: None,
        stop=lambda: None,
        install_descriptor=installed.append,
        create_instance=lambda factory, module, scope, **kw: None,
        get_services=lambda spec: [],
    )
    monkeypatch.setattr(main_module, "PluginManager", lambda registry=None: manager)
    monkeypatch.setattr(main_module.InteractiveCLIRunner, "cmdloop", lambda self: None)

    assert main_module.main() == 0
    assert {descriptor.name for descriptor in installed} == {
        "cli-health",
        "file-log",
        "cli-model",
        "cli-plugins",
        "cli-rich-renderer",
        "cli-scope",
        "cli-session",
        "cli-shell",
        "config-toml",
        "configs",
        "ui-server",
    }


def test_main_errors_when_default_provider_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys
) -> None:
    monkeypatch.setattr(
        sys, "argv", ["langharness", "--dir", str(tmp_path), "interactive"]
    )

    def missing_default() -> dict[str, str]:
        raise ValueError("No default model is configured")

    configs = SimpleNamespace(get_default_provider=missing_default)
    manager = SimpleNamespace(
        start=lambda: None,
        stop=lambda: None,
        install_descriptor=lambda descriptor, **kw: None,
        create_instance=lambda factory, module, scope, **kw: None,
        get_services=lambda spec: [],
        get_service=lambda spec: configs if spec == "configs" else None,
    )
    started = []

    class FakeInteractive:
        def __init__(self, **kwargs):
            started.append(kwargs)

        def cmdloop(self):
            return None

    monkeypatch.setattr(main_module, "PluginManager", lambda registry=None: manager)
    monkeypatch.setattr(main_module, "InteractiveCLIRunner", FakeInteractive)

    assert main_module.main() == 2
    assert started == []
    assert "No default model is configured" in capsys.readouterr().err


def test_main_rejects_reserved_default_provider_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["langharness", "--provider", "default", "--dir", str(tmp_path), "interactive"],
    )

    def reserved(name: str) -> dict[str, str]:
        raise ValueError("Provider name 'default' is reserved")

    configs = SimpleNamespace(get_provider=reserved)
    manager = SimpleNamespace(
        start=lambda: None,
        stop=lambda: None,
        install_descriptor=lambda descriptor, **kw: None,
        create_instance=lambda factory, module, scope, **kw: None,
        get_services=lambda spec: [],
        get_service=lambda spec: configs if spec == "configs" else None,
    )

    monkeypatch.setattr(main_module, "PluginManager", lambda registry=None: manager)

    assert main_module.main() == 2
    assert "reserved" in capsys.readouterr().err


def test_main_survives_broken_provider_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langharness_config.plugins.configs import ConfigsPlugin

    monkeypatch.setattr(sys, "argv", ["langharness"])

    configs = ConfigsPlugin()
    configs._providers = [
        SimpleNamespace(
            get_config=lambda: {
                "providers": {
                    "default": {"model": "demo", "api_key": "k"},
                    "broken": {"model": "demo", "api_key": "k", "protocol": "ftp"},
                }
            }
        )
    ]
    provider = SimpleNamespace(
        get_commands=lambda: [], get_interactive_commands=lambda: []
    )
    manager = SimpleNamespace(
        start=lambda: None,
        stop=lambda: None,
        install_descriptor=lambda descriptor, **kw: None,
        create_instance=lambda factory, module, scope, **kw: None,
        get_services=lambda spec: [provider],
        get_service=lambda spec: configs,
    )

    monkeypatch.setattr(main_module, "PluginManager", lambda registry=None: manager)
    monkeypatch.setattr(
        main_module,
        "PluginRegistry",
        lambda *args: SimpleNamespace(list=lambda: args),
    )
    monkeypatch.setattr(
        main_module,
        "InteractiveCLIRunner",
        lambda **kwargs: SimpleNamespace(cmdloop=lambda: None),
    )

    assert main_module.main() == 0

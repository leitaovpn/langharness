"""Tests for the unified four-module bootstrap."""
# mypy: ignore-errors
# pyright: reportArgumentType=false

from __future__ import annotations

from types import SimpleNamespace

import pytest

import langharness.bootstrap as bootstrap_module
from langharness.bootstrap import (
    DEFAULT_PACKAGE_PATHS,
    BootstrapError,
    load_package,
    parse_options,
    selected_package_paths,
)
from langharness_config.plugin import builtin_package as config_package
from langharness_plugin.package import PluginPackage


def test_base_url_for_maps_wildcard_bind_to_loopback() -> None:
    assert bootstrap_module.base_url_for(
        SimpleNamespace(server_ip="0.0.0.0", server_port=11534)
    ) == "http://127.0.0.1:11534"
    assert bootstrap_module.base_url_for(
        SimpleNamespace(server_ip="::", server_port=9000)
    ) == "http://127.0.0.1:9000"
    assert bootstrap_module.base_url_for(
        SimpleNamespace(server_ip="127.0.0.2", server_port=19000)
    ) == "http://127.0.0.2:19000"


def test_parse_options_uses_documented_defaults(tmp_path) -> None:
    options, remainder = parse_options([])

    assert options.mode == "all"
    assert options.server_ip == "127.0.0.1"
    assert options.server_port == 11534
    assert options.config_dir.endswith(".langharness")
    assert remainder == []


def test_parse_options_accepts_config_dir_alias_and_ui_arguments(tmp_path) -> None:
    options, remainder = parse_options(
        ["--mode", "ui", "--config_dir", str(tmp_path), "--provider", "demo"]
    )

    assert options.mode == "ui"
    assert options.config_dir == str(tmp_path)
    assert remainder == ["--provider", "demo"]


def test_load_package_validates_import_path_and_result() -> None:
    package = load_package(
        "langharness_config.plugin:builtin_package",
        config_dir="/tmp/config",
        section="plugins.config",
    )
    assert isinstance(package, PluginPackage)

    with pytest.raises(BootstrapError, match="plugins.ui"):
        load_package("missing", config_dir="/tmp/config", section="plugins.ui")
    with pytest.raises(BootstrapError, match="not callable"):
        load_package(
            "langharness_config.contracts:SPEC_CONFIGS",
            config_dir="/tmp/config",
            section="plugins.ui",
        )
    with pytest.raises(BootstrapError, match="PluginPackage"):
        load_package(
            "pathlib:Path",
            config_dir="/tmp/config",
            section="plugins.ui",
        )


def test_selected_package_paths_fall_back_per_missing_section() -> None:
    configs = SimpleNamespace(
        get_section=lambda section: {
            "builtin_package": "example.ui:package"
        }
        if section == "plugins.ui"
        else {}
    )

    paths = selected_package_paths(configs, mode="ui")

    assert paths["ui"] == "example.ui:package"
    assert paths["sdk"] == DEFAULT_PACKAGE_PATHS["sdk"]
    assert paths["log"] == DEFAULT_PACKAGE_PATHS["log"]


def test_selected_package_paths_for_server_include_agent() -> None:
    configs = SimpleNamespace(get_section=lambda section: {})

    paths = selected_package_paths(configs, mode="server")

    assert set(paths) == {"server", "agent", "log"}


def test_config_entry_point_discovery_success_and_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_point = SimpleNamespace(
        name="config", load=lambda: config_package
    )
    monkeypatch.setattr(
        bootstrap_module, "_config_entry_points", lambda: [entry_point]
    )
    assert bootstrap_module._discover_config_package("/tmp/config").id == (
        "builtin.config"
    )

    entry_point.load = lambda: lambda: object()
    with pytest.raises(BootstrapError, match="did not return PluginPackage"):
        bootstrap_module._discover_config_package("/tmp/config")

    def broken_load():
        raise RuntimeError("broken")

    entry_point.load = broken_load
    with pytest.raises(BootstrapError, match="broken"):
        bootstrap_module._discover_config_package("/tmp/config")

    monkeypatch.setattr(bootstrap_module, "_config_entry_points", lambda: [])
    assert bootstrap_module._discover_config_package("/tmp/config").id == (
        "builtin.config"
    )


def test_assembly_requests_retarget_persistent_paths_and_locale(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from langharness_api.plugin import builtin_package as api_package
    from langharness_cli.plugin import builtin_package as ui_package
    from langharness_core.plugin import builtin_package as agent_package
    from langharness_logging.plugin import builtin_package as log_package

    requests = bootstrap_module._assembly_requests(
        [config_package(), ui_package(), api_package(), agent_package(), log_package()],
        config_dir=str(tmp_path),
        locale="zh",
        override_scope="api",
        base_url="http://10.0.0.1:4321",
    )
    by_name = {request.name: request for request in requests}

    assert by_name["config-toml"].properties["plugin.config.path"] == str(
        tmp_path / "langharness.toml"
    )
    assert by_name["api-plugins"].properties["plugin.config_dir"] == str(tmp_path)
    assert by_name["sqlite-checkpointer"].properties["plugin.checkpoint.path"] == str(
        tmp_path / "langharness_checkpoints.sqlite3"
    )
    assert by_name["session-index"].properties["plugin.sessions.path"] == str(
        tmp_path / "sessions.sqlite3"
    )
    assert by_name["agent-registry"].properties["plugin.agents.path"] == str(
        tmp_path / "agents.json"
    )
    assert by_name["server-log"].properties["plugin.log.directory"] == str(tmp_path)
    assert by_name["cli-model"].properties["plugin.ui.locale"] == "zh"
    assert by_name["cli-health"].properties["plugin.base_url"] == "http://10.0.0.1:4321"
    assert (
        by_name["cli-plugins"].properties["plugin.base_url"] == "http://10.0.0.1:4321"
    )
    # The api scope never assembles the cli log instance.
    assert "cli-log" not in by_name

    monkeypatch_overrides = {"configs": {"enabled": False}}
    monkeypatch.setattr(
        bootstrap_module,
        "load_overrides",
        lambda directory, scope: monkeypatch_overrides,
    )
    duplicate_requests = bootstrap_module._assembly_requests(
        [config_package(), config_package()],
        config_dir=str(tmp_path),
        locale="en",
        override_scope="api",
        base_url="http://127.0.0.1:11534",
    )
    configs = next(item for item in duplicate_requests if item.name == "configs")
    assert configs.enabled is False


def test_assembly_requests_launcher_base_url_beats_stored_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from langharness_cli.plugin import builtin_package as ui_package

    monkeypatch.setattr(
        bootstrap_module,
        "load_overrides",
        lambda directory, scope: {
            "cli-health": {
                "enabled": True,
                "properties": {"plugin.base_url": "http://override:1"},
            }
        },
    )
    requests = bootstrap_module._assembly_requests(
        [config_package(), ui_package()],
        config_dir=str(tmp_path),
        locale="en",
        override_scope="cli",
        base_url="http://launcher:2",
    )
    by_name = {request.name: request for request in requests}

    assert by_name["cli-health"].properties["plugin.base_url"] == "http://launcher:2"


def test_select_packages_reads_config_service(tmp_path) -> None:
    packages = bootstrap_module._select_packages(
        config_package(),
        SimpleNamespace(
            config_dir=str(tmp_path),
            mode="server",
            server_ip="127.0.0.1",
            server_port=11534,
        ),
    )

    assert [package.id for package in packages] == [
        "builtin.api",
        "builtin.core",
        "builtin.logging",
    ]


def test_select_packages_requires_configs_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    manager = SimpleNamespace(
        start=lambda: None,
        stop=lambda: None,
        install_descriptor=lambda descriptor, **kwargs: None,
        create_instance=lambda factory, module, scope, **kwargs: None,
        get_service=lambda specification: None,
    )
    monkeypatch.setattr(bootstrap_module, "PluginManager", lambda registry: manager)
    with pytest.raises(BootstrapError, match="did not provide configs"):
        bootstrap_module._select_packages(
            config_package(),
            SimpleNamespace(
                config_dir=str(tmp_path),
                mode="ui",
                server_ip="127.0.0.1",
                server_port=11534,
            ),
        )


def test_run_assembles_real_ui_and_server_managers(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from langharness_api.plugins.server.runtime import ServerServerService
    from langharness_cli.plugins.server import UIServerService

    calls = []

    def server(self, host: str, port: int) -> None:
        calls.append(("server", host, port))

    def run(self, config) -> int:
        calls.append(("ui", dict(config)))
        return 8

    monkeypatch.setattr(ServerServerService, "server", server)
    monkeypatch.setattr(UIServerService, "run", run)
    base = {
        "server_ip": "127.0.0.2",
        "server_port": 19000,
        "config_dir": str(tmp_path),
    }

    assert bootstrap_module._run(SimpleNamespace(mode="server", **base), []) == 0
    assert calls[0] == ("server", "127.0.0.2", 19000)
    assert bootstrap_module._run(
        SimpleNamespace(mode="ui", **base), ["interactive"]
    ) == 8
    assert calls[1][0] == "ui"
    assert calls[1][1]["base_url"] == "http://127.0.0.2:19000"


def _guard_spy(
    monkeypatch: pytest.MonkeyPatch, mode: str, tmp_path
) -> list[tuple[str, dict]]:
    """Run one UI process mode with the guard and the UI itself captured.

    Each mode gets its own `_run` call: a second call re-executes the plugin
    module for a new framework, which would discard the patched `run`.
    """
    from langharness_cli.plugins.server import UIServerService

    guards: list[tuple[str, dict]] = []

    def guard(base_url: str, **kwargs) -> SimpleNamespace:
        guards.append((base_url, kwargs))
        return SimpleNamespace(ensure_api_server=lambda: None)

    def run(self, config) -> int:
        return 0

    monkeypatch.setattr(bootstrap_module, "APIGuard", guard)
    monkeypatch.setattr(UIServerService, "run", run)
    options = SimpleNamespace(
        mode=mode,
        server_ip="127.0.0.2",
        server_port=19000,
        config_dir=str(tmp_path),
    )
    assert bootstrap_module._run(options, []) == 0
    return guards


def test_run_does_not_guard_api_server_in_ui_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    assert _guard_spy(monkeypatch, "ui", tmp_path) == []


def test_run_guards_api_server_in_all_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    assert _guard_spy(monkeypatch, "all", tmp_path) == [
        ("http://127.0.0.2:19000", {"config_dir": str(tmp_path)})
    ]


def test_main_restores_environment_and_reports_bootstrap_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys
) -> None:
    monkeypatch.setenv("LANG_HARNESS_DIR", "before")
    monkeypatch.setattr(bootstrap_module, "_run", lambda options, remainder: 6)
    assert bootstrap_module.main(["--config-dir", str(tmp_path)]) == 6
    assert bootstrap_module.os.environ["LANG_HARNESS_DIR"] == "before"

    def fail(options, remainder):
        raise BootstrapError("bad bootstrap")

    monkeypatch.delenv("LANG_HARNESS_DIR")
    monkeypatch.setattr(bootstrap_module, "_run", fail)
    assert bootstrap_module.main(["--config-dir", str(tmp_path)]) == 2
    assert "bad bootstrap" in capsys.readouterr().err
    assert "LANG_HARNESS_DIR" not in bootstrap_module.os.environ

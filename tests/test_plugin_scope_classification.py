"""Every built-in contribution maps into the canonical runtime scope tree."""
# mypy: ignore-errors
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

from langharness.bootstrap import TARGET_SCOPES
from langharness_api.plugin import builtin_package as api_package
from langharness_cli.plugin import builtin_package as cli_package
from langharness_config.plugin import builtin_package as config_package
from langharness_core.plugin import builtin_package as core_package
from langharness_logging.plugin import builtin_package as log_package
from langharness_scope import ROOT_SCOPE_ID, ScopeId


def contributions(package):
    return {item.id: item for item in package.contributions}


def targets(package, ids):
    return {item.target for item in package.contributions if item.id in ids}


def test_common_plugins_are_in_root_scope() -> None:
    assert targets(config_package(), {"config-toml", "configs"}) == {"root"}
    assert targets(log_package(), {"server-log", "cli-log"}) == {"root"}
    assert targets(api_package(), {"api-db"}) == {"root"}
    assert TARGET_SCOPES["root"] == ROOT_SCOPE_ID


def test_cli_plugins_are_in_ui_scope() -> None:
    assert targets(
        cli_package(),
        {
            "ui-server",
            "cli-health",
            "cli-model",
            "cli-rich-renderer",
            "cli-session",
            "cli-plugins",
            "cli-scope",
            "cli-shell",
        },
    ) == {"ui"}
    assert TARGET_SCOPES["ui"] == ScopeId("ui")


def test_server_plugins_and_checkpointer_are_in_server_scope() -> None:
    server_ids = {
        "api-auth",
        "api-rate-limit",
        "api-health",
        "api-stream",
        "api-resume",
        "api-plugins",
        "api-scopes",
        "api-sessions",
        "api-agents",
        "api-server",
        "server-server",
        "sqlite-checkpointer",
        "session-index",
        "agent-registry",
        "agent-directory",
        "agent-server",
    }
    assert targets(api_package(), server_ids) == {"server"}
    assert targets(core_package(), server_ids) == {"server"}
    assert TARGET_SCOPES["server"] == ScopeId("server")


def test_agent_templates_live_in_agent_scope_and_instances_are_created_per_agent() -> None:
    template_ids = {
        "llm-template",
        "tools-template",
        "name-template",
        "agent-loop-template",
        "tool-export-adapter-template",
    }
    assert targets(core_package(), template_ids) == {"agent"}
    assert TARGET_SCOPES["agent"] == ScopeId("agent")

    # Per-agent materialization no longer bakes scope into descriptors:
    # the directory creates instances at agent:<id> scopes at runtime.
    from langharness_core.plugin import agent_plugin_descriptor

    descriptor = agent_plugin_descriptor("llm")
    assert not hasattr(descriptor, "scope")
    assert not hasattr(descriptor, "scope_parent")

"""Command-line assembly and dispatch."""

from __future__ import annotations

import os
import sys
from argparse import ArgumentParser, Namespace
from pathlib import Path
from typing import Any

from langharness_cli.common.i18n import get_locale
from langharness_cli.common.interactive import InteractiveCLIRunner
from langharness_cli.common.runner import CLIRunner
from langharness_cli.common.session import DEFAULT_USER_ID, resolve_identity
from langharness_cli.contracts import SPEC_CLI_COMMAND, SPEC_CLI_RENDERER
from langharness_cli.plugin import builtin_package as cli_builtin_package
from langharness_cli.plugin import cli_descriptors
from langharness_config.contracts import SPEC_CONFIGS
from langharness_config.plugin import builtin_package as config_builtin_package
from langharness_config.plugin import config_descriptors
from langharness_logging.contracts import SPEC_LOG
from langharness_logging.plugin import builtin_package as log_builtin_package
from langharness_logging.plugin import log_descriptor
from langharness_plugin.plugin_manager import PluginManager
from langharness_plugin.registry import PluginDescriptor, PluginRegistry


def _global_options(argv: list[str]) -> tuple[Namespace, list[str]]:
    parser = ArgumentParser(add_help=False)
    parser.add_argument("--provider")
    parser.add_argument("--dir", default=str(Path.home() / ".langharness"))
    return parser.parse_known_args(argv)


def _interactive_options(argv: list[str]) -> dict[str, Any]:
    """Extract interactive-mode flags from argv, mirroring the global scan."""
    values: dict[str, Any] = {
        "token": "secret",
        "user_id": os.environ.get("LANG_HARNESS_USER_ID") or DEFAULT_USER_ID,
        "agent_id": None,
        "session_id": None,
        "new_session": False,
    }
    flags = {
        "--token": "token",
        "--user-id": "user_id",
        "--agent-id": "agent_id",
        "--session-id": "session_id",
    }
    for index, arg in enumerate(argv):
        if arg == "--new-session":
            values["new_session"] = True
            continue
        for flag, key in flags.items():
            if arg == flag and index + 1 < len(argv):
                values[key] = argv[index + 1]
            elif arg.startswith(f"{flag}="):
                values[key] = arg.split("=", 1)[1]
    return values


def _locale_option(argv: list[str], *, default: str) -> str:
    """Extract the --locale flag from argv, falling back to `default`."""
    for index, arg in enumerate(argv):
        if arg == "--locale" and index + 1 < len(argv):
            return argv[index + 1].lower()
        if arg.startswith("--locale="):
            return arg.split("=", 1)[1].lower()
    return default


def main(
    argv: list[str] | None = None,
    *,
    descriptors: list[PluginDescriptor] | None = None,
    manager: PluginManager | None = None,
    base_url: str | None = None,
) -> int:
    options, argv = _global_options(sys.argv[1:] if argv is None else argv)
    locale = _locale_option(argv, default=get_locale())
    directory = str(Path(options.dir).expanduser().resolve())
    inherited_directory = os.environ.get("LANG_HARNESS_DIR")
    os.environ["LANG_HARNESS_DIR"] = directory
    selected_descriptors = descriptors or (
        config_descriptors() + [log_descriptor()] + cli_descriptors()
    )
    active_manager = manager or PluginManager(PluginRegistry(selected_descriptors))
    owns_manager = manager is None
    if owns_manager:
        active_manager.start()
        from langharness.bootstrap import _assembly_requests

        requests = _assembly_requests(
            [
                config_builtin_package(),
                cli_builtin_package(),
                log_builtin_package(),
            ],
            config_dir=directory,
            locale=locale,
            override_scope="cli",
            base_url=base_url or "http://127.0.0.1:11534",
        )
        installed_keys: set[tuple[str, str]] = set()
        for request in requests:
            key = (request.descriptor.module, request.descriptor.factory)
            if key not in installed_keys:
                active_manager.install_descriptor(request.descriptor)
                installed_keys.add(key)
        for request in requests:
            active_manager.create_instance(
                request.descriptor.factory,
                request.descriptor.module,
                request.scope_id,
                properties=request.properties,
                enabled=request.enabled,
            )

    try:
        providers = active_manager.get_services(SPEC_CLI_COMMAND)
        get_service = getattr(active_manager, "get_service", lambda specification: None)
        configs = get_service(SPEC_CONFIGS)
        log_provider = get_service(SPEC_LOG)
        renderer = get_service(SPEC_CLI_RENDERER)
        if log_provider is not None and hasattr(log_provider, "get_logger"):
            log_provider.get_logger().info("CLI started")

        commands = [
            command for provider in providers for command in provider.get_commands()
        ]

        if not argv or argv[0] == "interactive":
            interactive_args = argv[1:] if argv and argv[0] == "interactive" else argv
            if any(
                arg == "--base-url" or arg.startswith("--base-url=")
                for arg in interactive_args
            ):
                print(
                    "--base-url is removed; use --server-ip/--server-port",
                    file=sys.stderr,
                )
            interactive_options = _interactive_options(interactive_args)
            base_url = base_url or "http://127.0.0.1:11534"
            token = str(interactive_options["token"])
            user_id = str(interactive_options["user_id"])
            session_id, agent_id = resolve_identity(
                base_url,
                token,
                user_id,
                agent_id=interactive_options["agent_id"],
                session_id=interactive_options["session_id"],
                new_session=bool(interactive_options["new_session"]),
            )
            interactive_commands = [
                command
                for provider in providers
                for command in provider.get_interactive_commands()
            ]
            selected_provider = options.provider
            provider_config: dict[str, Any] = {}
            if configs is not None:
                try:
                    if selected_provider:
                        provider_config = dict(configs.get_provider(selected_provider))
                    else:
                        provider_config = dict(configs.get_default_provider())
                        selected_provider = "default"
                except ValueError as exc:
                    print(str(exc), file=sys.stderr)
                    return 2
            if options.provider and not provider_config:
                print(f"Unknown provider: {options.provider}", file=sys.stderr)
                return 2
            interactive_runner = InteractiveCLIRunner(
                base_url=base_url,
                token=token,
                model=provider_config.get(
                    "model", os.environ.get("LANG_HARNESS_MODEL", "gpt-4o-mini")
                ),
                provider_name=selected_provider or "environment",
                model_protocol=provider_config.get("protocol", "chat"),
                api_key=provider_config.get(
                    "api_key", os.environ.get("LANG_HARNESS_API_KEY", "")
                ),
                model_base_url=provider_config.get(
                    "base_url", os.environ.get("LANG_HARNESS_BASE_URL", "")
                ),
                commands=interactive_commands,
                renderer=renderer,
                history_file=str(Path(directory) / "history"),
                locale=locale,
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
            )
            interactive_runner.configs = configs
            interactive_runner.log = log_provider
            interactive_runner.cmdloop()
            return 0

        cli_runner = CLIRunner(commands)
        cli_runner.configs = configs
        cli_runner.log = log_provider
        return cli_runner.run(argv)
    finally:
        if owns_manager:
            active_manager.stop()
        if inherited_directory is None:
            os.environ.pop("LANG_HARNESS_DIR", None)
        else:
            os.environ["LANG_HARNESS_DIR"] = inherited_directory

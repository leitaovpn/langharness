"""Public contracts for CLI command plugins."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from langharness_plugin.validation import service_contract

SPEC_CLI_COMMAND = "cli.plugin.command"
SPEC_CLI_RUNNER = "cli.runner"
SPEC_CLI_RENDERER = "cli.plugin.renderer"
SPEC_UI_SERVER = "ui.server"


@dataclass(frozen=True)
class CommandSpec:
    name: str
    help: str
    handler: Callable[[Any], int]
    add_arguments: Callable[[Any], None] | None = None


@dataclass(frozen=True)
class InteractiveCommandSpec:
    name: str
    help: str
    handler: Callable[[InteractiveCommandContext, str], bool | None]


@runtime_checkable
class InteractiveCommandContext(Protocol):
    """Runtime data exposed to interactive command plugins."""

    base_url: str
    token: str
    commands: Mapping[str, InteractiveCommandSpec]
    user_id: str
    agent_id: str
    session_id: str | None

    def list_providers(self) -> list[str]: ...

    def switch_provider(self, name: str) -> bool: ...

    def refresh_status(self) -> None: ...


@service_contract(SPEC_CLI_COMMAND)
@runtime_checkable
class CLICommandProvider(Protocol):
    def get_commands(self) -> list[CommandSpec]: ...

    def get_interactive_commands(self) -> list[InteractiveCommandSpec]: ...

    def get_plugin_info(self) -> dict[str, str]: ...


@service_contract(SPEC_CLI_RENDERER)
@runtime_checkable
class InteractiveRenderer(Protocol):
    def show_welcome(self, text: str, *, highlights: Sequence[str] = ()) -> None: ...

    def start_response(self) -> None: ...

    def render_event(self, event: Mapping[str, Any]) -> None: ...

    def finish_response(self) -> None: ...

    def show_error(self, message: str) -> None: ...

    def set_model(self, name: str) -> None: ...

    def get_status_text(self) -> str: ...


@service_contract(SPEC_UI_SERVER)
@runtime_checkable
class UIServerProvider(Protocol):
    def run(self, config: Mapping[str, Any]) -> int: ...

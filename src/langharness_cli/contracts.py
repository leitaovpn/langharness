"""Public contracts for CLI command plugins."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
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
    #: Optional. Declared by the command that owns the grammar rather than
    #: by the shell, so a command's actions and their completion cannot drift
    #: apart the way a shared lookup table would let them.
    complete: ArgumentCompleter | None = None


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


#: Supplies candidates for one argument slot of an interactive command.
#: Receives the command's context, the argument words already typed before
#: the slot, and the word being typed. An implementation that cannot answer
#: a slot returns nothing rather than guessing.
ArgumentCompleter = Callable[
    ["InteractiveCommandContext", Sequence[str], str], Iterable[str]
]


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

    #: The escape hatch for whatever the transcript collapsed: the detail is
    #: given back as text, so that the caller can draw it somewhere it can
    #: take away again. Printing it would be a one-way door.
    def expansion_text(self) -> str: ...

    def show_error(self, message: str) -> None: ...

    def set_model(self, name: str) -> None: ...

    def get_status_text(self) -> str: ...


@service_contract(SPEC_UI_SERVER)
@runtime_checkable
class UIServerProvider(Protocol):
    def run(self, config: Mapping[str, Any]) -> int: ...

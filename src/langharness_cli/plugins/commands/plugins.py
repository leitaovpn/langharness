"""Interactive plugin-configuration command plugin."""

from __future__ import annotations

import json
from argparse import ArgumentParser, Namespace
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import httpx
from pelix.ipopo.decorators import ComponentFactory, Property, Provides
from rich.console import Console
from rich.table import Table

from langharness_cli.contracts import (
    CLICommandProvider,
    CommandSpec,
    InteractiveCommandContext,
    InteractiveCommandSpec,
)
from langharness_plugin.validation import is_runtime_scope as _is_runtime_scope


def _coerce(value: str) -> Any:
    """Typed values when the text looks like JSON: 5 stays an int, abc a str."""
    try:
        return json.loads(value)
    except ValueError:
        return value


CONFIG_SCOPES = ("api", "cli")


def _is_config_scope(value: str) -> bool:
    return value in CONFIG_SCOPES or (
        value.startswith("agent:") and len(value) > len("agent:")
    )


# --- grammar ---------------------------------------------------------------
#
# One description of what /plugins accepts, consumed by four things: the
# handler's action lookup, the usage text, the argument completion, and the
# non-interactive parser's action list. Before this table the action set was
# written out three times and had drifted -- `upgrade` existed only on the
# argparse side, `set`/`history`/`rollback` only on the interactive one.


@dataclass(frozen=True)
class Position:
    """One argument slot. ``kind`` says what may stand there."""

    name: str
    kind: str
    literal: str = ""
    variadic: bool = False


@dataclass(frozen=True)
class Action:
    """An action and its alternative argument lists.

    Several forms rather than one optional-argument list, because ``config``
    genuinely takes two shapes (``config [scope]`` and
    ``config enable|disable <scope> <plugin>``) and a single positional list
    cannot say that without also accepting half-typed hybrids.
    """

    name: str
    summary: str
    forms: tuple[tuple[Position, ...], ...]
    #: Which front-ends implement it. Declaring this is what makes the
    #: drift visible: before, an action present on one side and absent on
    #: the other looked exactly like an action nobody had needed yet.
    interactive: bool = True
    cli: bool = True


def _slot(name: str, kind: str) -> Position:
    return Position(name, kind)


ACTIONS: tuple[Action, ...] = (
    Action("discover", "Rescan and list discovered plugin packages", ((),)),
    Action(
        "list",
        "List registered runtime plugins",
        ((), (_slot("runtime_scope", "runtime_scope"),)),
    ),
    Action(
        "config",
        "Show or change persisted plugin configuration",
        (
            (),
            (_slot("config_scope", "config_scope"),),
            (
                Position("verb", "config_verb"),
                _slot("config_scope", "config_scope"),
                _slot("plugin", "plugin"),
            ),
        ),
    ),
    Action(
        "runtime",
        "Update properties of an installed plugin instance",
        (
            (
                Position("set", "literal", literal="set"),
                _slot("runtime_scope", "runtime_scope"),
                _slot("plugin", "plugin"),
                Position("KEY=VALUE", "key_value", variadic=True),
            ),
        ),
    ),
    Action(
        "install",
        "Install a discovered contribution",
        (
            (
                _slot("package_id", "package_id"),
                _slot("contribution_id", "contribution_id"),
                _slot("scope", "scope_id"),
            ),
        ),
    ),
    Action(
        "uninstall",
        "Remove an installed plugin",
        ((_slot("runtime_scope", "runtime_scope"), _slot("plugin", "plugin")),),
    ),
    Action(
        "set",
        "Merge configuration into a plugin",
        (
            (
                _slot("config_scope", "config_scope"),
                _slot("plugin", "plugin"),
                Position("KEY=VALUE", "key_value", variadic=True),
            ),
        ),
        cli=False,
    ),
    Action(
        "enable",
        "Enable a runtime plugin",
        ((_slot("runtime_scope", "runtime_scope"), _slot("plugin", "plugin")),),
    ),
    Action(
        "disable",
        "Disable a runtime plugin",
        ((_slot("runtime_scope", "runtime_scope"), _slot("plugin", "plugin")),),
    ),
    Action(
        "upgrade",
        "Upgrade an installed plugin",
        ((_slot("runtime_scope", "runtime_scope"), _slot("plugin", "plugin")),),
    ),
    Action(
        "history",
        "Show configuration history for a scope",
        ((_slot("config_scope", "config_scope"),),),
        cli=False,
    ),
    Action(
        "rollback",
        "Roll a scope back to an earlier version",
        (
            (
                _slot("config_scope", "config_scope"),
                _slot("version", "version"),
            ),
        ),
        cli=False,
    ),
)

ACTION_BY_NAME = {action.name: action for action in ACTIONS}

#: What completion may offer for a slot kind. Kinds that are absent --
#: plugin names, package ids, versions, KEY=VALUE pairs -- live behind the
#: API, and a candidate list must never block the keystroke on a round trip.
SLOT_CANDIDATES: dict[str, tuple[str, ...]] = {
    # Bare `agent` is a runtime scope but not a config scope: the config
    # vocabulary is api|cli|agent:<id>, so offering `agent` there would
    # insert a value the handler immediately rejects.
    "runtime_scope": ("root", "server", "ui", "agent"),
    "config_scope": ("api", "cli"),
    "config_verb": ("enable", "disable"),
}

#: The action set each front-end offers, derived rather than restated.
HANDLED_ACTIONS = frozenset(a.name for a in ACTIONS if a.interactive)
CLI_ACTIONS = tuple(a.name for a in ACTIONS if a.cli)


def _slot_at(form: Sequence[Position], index: int) -> Position | None:
    if index < len(form):
        return form[index]
    if form and form[-1].variadic:
        return form[-1]
    return None


def _accepts(position: Position, word: str) -> bool:
    """Whether a typed word could stand in this slot.

    Only the kinds this module can judge locally are checked; package ids,
    plugin names and KEY=VALUE pairs are accepted as typed.
    """
    if position.kind == "literal":
        return word == position.literal
    if position.kind == "runtime_scope":
        return _is_runtime_scope(word)
    if position.kind == "config_scope":
        return _is_config_scope(word)
    if position.kind == "config_verb":
        return word in SLOT_CANDIDATES["config_verb"]
    return True


def _form_still_fits(form: Sequence[Position], arguments: Sequence[str]) -> bool:
    for index, word in enumerate(arguments):
        position = _slot_at(form, index)
        if position is None or not _accepts(position, word):
            return False
    return True


def _slot_text(position: Position) -> str:
    if position.kind == "literal":
        return position.literal
    return f"<{position.name}>" + ("..." if position.variadic else "")


def _narrowed(values: Iterable[str], prefix: str) -> list[str]:
    """Plain prefix filter, matching what the palette does to any source."""
    wanted = prefix.lower()
    return [value for value in values if value.lower().startswith(wanted)]


@ComponentFactory("cli-plugins-command-factory")
@Provides(CLICommandProvider)
@Property("_plugin_name", "plugin.name", "plugin-command")
@Property("_plugin_version", "plugin.version", "1.0.0")
@Property("_locale", "plugin.ui.locale", "en")
@Property("_base_url", "plugin.base_url", "http://127.0.0.1:11534")
class PluginCommandPlugin:
    """Reads and edits versioned plugin configuration through the API."""

    def __init__(self) -> None:
        self._plugin_name = "plugin-command"
        self._plugin_version = "1.0.0"
        self._locale = "en"
        self._base_url = "http://127.0.0.1:11534"

    def get_commands(self) -> list[CommandSpec]:
        return [
            CommandSpec(
                name="plugins",
                help="Discover and manage runtime plugins",
                handler=self._command_handler,
                add_arguments=self._add_arguments,
            )
        ]

    @staticmethod
    def _add_arguments(parser: ArgumentParser) -> None:
        parser.add_argument(
            "action",
            choices=CLI_ACTIONS,
        )
        parser.add_argument("values", nargs="*")
        parser.add_argument("--scope")
        parser.add_argument("--token", default="secret")

    def _command_handler(self, args: Namespace) -> int:
        context = SimpleNamespace(base_url=self._base_url, token=args.token)
        try:
            if args.action == "discover":
                self._post_raw(context, "/plugins/rescan", {})
                payload = self._get_raw(context, "/plugins/discovered")
            elif args.action == "list":
                if args.scope and not _is_runtime_scope(args.scope):
                    raise ValueError(
                        "list --scope must be root|server|ui|agent|agent:<id>"
                    )
                params = {"scope": args.scope} if args.scope else None
                payload = self._get_raw(context, "/plugins/runtime", params=params)
            elif args.action == "config":
                if args.scope and not _is_config_scope(args.scope):
                    raise ValueError("config --scope must be api|cli|agent:<id>")
                payload = self._get_raw(
                    context,
                    "/plugins",
                    params={"scope": args.scope} if args.scope else None,
                )
            elif args.action == "runtime":
                if (
                    len(args.values) < 3
                    or args.values[0] != "set"
                    or not _is_runtime_scope(args.scope or "")
                ):
                    raise ValueError(
                        "runtime requires set PLUGIN_NAME KEY=VALUE "
                        "--scope root|server|ui|agent|agent:<id>"
                    )
                properties = self._properties(args.values[2:])
                payload = self._put_raw(
                    context,
                    f"/plugins/runtime/{args.values[1]}/properties",
                    {"properties": properties},
                    params={"scope": args.scope} if args.scope else None,
                )
            elif args.action == "install":
                if len(args.values) != 2 or not args.scope:
                    raise ValueError("install requires PACKAGE_ID CONTRIBUTION_ID --scope SCOPE")
                payload = self._post_raw(
                    context,
                    "/plugins/install",
                    {
                        "package_id": args.values[0],
                        "contribution_id": args.values[1],
                        "scope_id": args.scope,
                    },
                )
            elif args.action in {"enable", "disable"}:
                if len(args.values) != 1 or not _is_runtime_scope(args.scope or ""):
                    raise ValueError(
                        f"{args.action} requires PLUGIN_NAME "
                        "--scope root|server|ui|agent|agent:<id>"
                    )
                payload = self._put_raw(
                    context,
                    f"/plugins/runtime/{args.values[0]}/enabled",
                    {"enabled": args.action == "enable"},
                    params={"scope": args.scope},
                )
            elif args.action == "upgrade":
                if len(args.values) != 1 or not _is_runtime_scope(args.scope or ""):
                    raise ValueError(
                        "upgrade requires PLUGIN_NAME "
                        "--scope root|server|ui|agent|agent:<id>"
                    )
                payload = self._post_raw(
                    context, f"/plugins/runtime/{args.values[0]}/upgrade", {}, params={"scope": args.scope}
                )
            else:
                if len(args.values) != 1 or not _is_runtime_scope(args.scope or ""):
                    raise ValueError(
                        "uninstall requires PLUGIN_NAME "
                        "--scope root|server|ui|agent|agent:<id>"
                    )
                payload = self._delete_raw(
                    context, f"/plugins/runtime/{args.values[0]}", params={"scope": args.scope}
                )
        except (httpx.HTTPError, ValueError) as exc:
            print(f"Plugin request failed: {self._error_message(exc)}")
            return 1
        self._render_noninteractive_result(args.action, args.scope, payload)
        return 0

    def get_interactive_commands(self) -> list[InteractiveCommandSpec]:
        return [
            InteractiveCommandSpec(
                name="plugins",
                help=(
                    "Plugin management: "
                    + "|".join(action.name for action in ACTIONS)
                    + " [scope]; scopes are root, server, ui, api, cli, or agent:<id>"
                ),
                handler=self._handle,
                complete=self._complete,
            )
        ]

    def _complete(
        self,
        context: InteractiveCommandContext,
        words: Sequence[str],
        prefix: str,
    ) -> list[str]:
        """Candidates for the slot the cursor is in.

        Walks the grammar table, so a word is offered only where it could
        legally stand: `/plugins` offers actions, `/plugins runtime` offers
        ``set``, and a slot holding a plugin name offers nothing, because
        names live behind the API.
        """
        del context  # The grammar is static; it needs no runtime state.
        if not words:
            return _narrowed((action.name for action in ACTIONS), prefix)
        action = ACTION_BY_NAME.get(words[0])
        if action is None:
            return []
        arguments = list(words[1:])
        offered: list[str] = []
        for form in action.forms:
            if not _form_still_fits(form, arguments):
                continue
            position = _slot_at(form, len(arguments))
            if position is None:
                continue
            values = (
                (position.literal,)
                if position.kind == "literal"
                else SLOT_CANDIDATES.get(position.kind, ())
            )
            for value in values:
                # No pair of forms overlaps on a slot today, so this is a
                # guard against a future table edit rather than a live path.
                if value not in offered:  # pragma: no cover
                    offered.append(value)
        return _narrowed(offered, prefix)

    def usage_text(self) -> str:
        """The accepted spellings, generated from the grammar table."""
        lines: list[str] = []
        for action in ACTIONS:
            for form in action.forms:
                slots = " ".join(_slot_text(position) for position in form)
                lines.append(f"/plugins {action.name}" + (f" {slots}" if slots else ""))
        return "\n".join(lines)

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}

    def _handle(self, context: InteractiveCommandContext, line: str) -> bool:
        words = line.split()
        if not words:
            self._usage()
            return False
        action, arguments = words[0].lower(), words[1:]
        if action not in HANDLED_ACTIONS:
            print(f"Unknown /plugins action: {action}")
            self._usage()
            return False
        try:
            if action == "list":
                self._list(context, arguments)
            elif action == "config":
                self._config(context, arguments)
            elif action == "runtime":
                self._runtime(context, arguments)
            elif action == "discover":
                self._discover(context)
            elif action == "install":
                self._install(context, arguments)
            elif action == "uninstall":
                self._uninstall(context, arguments)
            elif action == "set":
                self._update(context, action, arguments)
            elif action in ("enable", "disable", "upgrade"):
                self._mutate_runtime_plugin(context, action, arguments)
            elif action == "history":
                self._history(context, arguments)
            elif action == "rollback":
                self._rollback(context, arguments)
            else:
                # The guard above already rejected anything outside the
                # table, so reaching here means an action was advertised
                # without a branch -- the `upgrade` bug, caught by
                # test_every_advertised_action_reaches_a_branch.
                print(f"Unknown /plugins action: {action}")
                self._usage()
        except (httpx.HTTPError, ValueError) as exc:
            print(f"Plugin request failed: {self._error_message(exc)}")
        return False

    def _usage(self) -> None:
        print("usage:\n" + self.usage_text())

    def _list(self, context: InteractiveCommandContext, arguments: list[str]) -> None:
        scope = arguments[0] if arguments else None
        payload = self._get_raw(
            context, "/plugins/runtime", params={"scope": scope} if scope else None
        )
        plugins = payload.get("plugins") or []
        if not plugins:
            print(f"runtime scope {scope or 'all scopes'}: no registered plugins")
            return
        self._runtime_table(scope, plugins)

    def _config(self, context: InteractiveCommandContext, arguments: list[str]) -> None:
        """Render persisted configuration overrides, distinct from runtime state."""
        if arguments and arguments[0] in {"enable", "disable"}:
            self._update(context, arguments[0], arguments[1:])
            return
        scope = arguments[0] if arguments else None
        payload = self._get_raw(
            context, "/plugins", params={"scope": scope} if scope else None
        )
        if scope is None:
            self._config_all_table(payload.get("scopes") or [])
            return
        plugins = payload.get("plugins") or {}
        if not plugins:
            print(f"configuration scope {scope}: no plugin overrides")
            return
        self._config_table(scope, payload)

    def _runtime(self, context: InteractiveCommandContext, arguments: list[str]) -> None:
        """Update properties of an installed runtime plugin instance."""
        if (
            len(arguments) < 4
            or arguments[0] != "set"
            or not _is_runtime_scope(arguments[1])
        ):
            print("Scope is required. Specify root, server, ui, agent, or agent:<id>.")
            self._usage()
            return
        scope, name = arguments[1], arguments[2]
        properties: dict[str, Any] = {}
        for pair in arguments[3:]:
            key, separator, value = pair.partition("=")
            if not separator or not key:
                print(f"Expected KEY=VALUE, got: {pair}")
                return
            properties[key] = _coerce(value)
        payload = self._put_raw(
            context,
            f"/plugins/runtime/{name}/properties",
            {"properties": properties},
            params={"scope": scope},
        )
        self._registration_table("Runtime plugin properties updated", payload)

    def _discover(self, context: InteractiveCommandContext) -> None:
        self._post_raw(context, "/plugins/rescan", {})
        payload = self._get_raw(context, "/plugins/discovered")
        packages = payload.get("packages") or []
        if not packages:
            print("no discovered plugins")
            return
        rows = [
            (
                package.get("id", "-"),
                package.get("version", "-"),
                package.get("source", "external"),
                contribution.get("id", "-"),
                contribution.get("name", "-"),
                contribution.get("specification", "-"),
                contribution.get("module", "-"),
            )
            for package in packages
            for contribution in package.get("contributions") or [{}]
        ]
        self._table(
            "Discovered plugins",
            ("Package", "Version", "Source", "Contribution", "Name", "Spec", "Module"),
            rows,
        )

    def _install(
        self, context: InteractiveCommandContext, arguments: list[str]
    ) -> None:
        if len(arguments) != 3:
            self._usage()
            return
        package_id, contribution_id, scope_id = arguments
        payload = self._post_raw(
            context,
            "/plugins/install",
            {
                "package_id": package_id,
                "contribution_id": contribution_id,
                "scope_id": scope_id,
            },
        )
        self._registration_table("Installed plugin", payload)

    def _mutate_runtime_plugin(
        self, context: InteractiveCommandContext, action: str, arguments: list[str]
    ) -> None:
        """The three two-argument runtime actions: enable, disable, upgrade."""
        if len(arguments) != 2 or not _is_runtime_scope(arguments[0]):
            print("Scope is required. Specify root, server, ui, agent, or agent:<id>.")
            self._usage()
            return
        scope, name = arguments
        if action == "upgrade":
            payload = self._post_raw(
                context,
                f"/plugins/runtime/{name}/upgrade",
                {},
                params={"scope": scope},
            )
        else:
            payload = self._put_raw(
                context,
                f"/plugins/runtime/{name}/enabled",
                {"enabled": action == "enable"},
                params={"scope": scope},
            )
        self._registration_table("Runtime plugin updated", payload)

    def _uninstall(
        self, context: InteractiveCommandContext, arguments: list[str]
    ) -> None:
        if len(arguments) != 2 or not _is_runtime_scope(arguments[0]):
            print("Scope is required. Specify root, server, ui, agent, or agent:<id>.")
            self._usage()
            return
        payload = self._delete_raw(
            context, f"/plugins/runtime/{arguments[1]}", params={"scope": arguments[0]}
        )
        self._table("Plugin removal", ("Scope", "Plugin", "Removed"), ((arguments[0], arguments[1], payload.get("removed", False)),))

    def _update(
        self, context: InteractiveCommandContext, action: str, arguments: list[str]
    ) -> None:
        if not arguments or not _is_config_scope(arguments[0]):
            print("Scope is required. Specify api, cli, or agent:<id>.")
            self._usage()
            return
        scope, plugin, rest = self._plugin_arguments(arguments)
        if plugin is None:
            self._usage()
            return
        updates: list[tuple[str, str]] = []
        if action == "set":
            if not rest:
                self._usage()
                return
            for pair in rest:
                key, separator, value = pair.partition("=")
                if not separator or not key:
                    print(f"Expected KEY=VALUE, got: {pair}")
                    return
                updates.append((key, _coerce(value)))
        current = self._get(context, "/plugins", scope=scope)
        plugins = dict(current.get("plugins") or {})
        entry = dict(plugins.get(plugin) or {})
        properties = dict(entry.get("properties") or {})
        properties.update(updates)
        if action != "set":
            entry["enabled"] = action == "enable"
        entry["properties"] = properties
        entry.setdefault("enabled", True)
        plugins[plugin] = entry
        self._apply(
            self._put(context, "/plugins", {"plugins": plugins}, scope=scope)
        )

    def _history(
        self, context: InteractiveCommandContext, arguments: list[str]
    ) -> None:
        if not arguments or not _is_config_scope(arguments[0]):
            print("Scope is required. Specify api, cli, or agent:<id>.")
            self._usage()
            return
        scope = arguments[0]
        payload = self._get(context, "/plugins/history", scope=scope)
        self._table(
            f"Configuration history · {scope}",
            ("Version", "Action", "Actor", "Target version"),
            (
                (entry.get("seq"), entry.get("action"), entry.get("actor"), entry.get("target_seq", "-"))
                for entry in payload.get("history") or []
            ),
        )

    def _rollback(
        self, context: InteractiveCommandContext, arguments: list[str]
    ) -> None:
        if len(arguments) != 2 or not _is_config_scope(arguments[0]):
            print("Scope is required. Specify api, cli, or agent:<id>.")
            self._usage()
            return
        scope, version_text = arguments
        try:
            version = int(version_text)
        except ValueError:
            self._usage()
            return
        payload = self._post(
            context, "/plugins/rollback", {"seq": version, "actor": "cli"}, scope=scope
        )
        self._apply(payload)

    def _apply(self, payload: dict[str, Any]) -> None:
        applied = ", ".join(payload.get("applied") or []) or "-"
        restart = ", ".join(payload.get("restart_required") or []) or "-"
        self._table(
            "Plugin configuration applied",
            ("Version", "Applied", "Restart required"),
            ((payload.get("version"), applied, restart),),
        )

    @staticmethod
    def _table(
        title: str, columns: tuple[str, ...], rows: Any
    ) -> None:
        table = Table(title=title, header_style="bold cyan")
        for column in columns:
            # Keep headers whole in narrow consoles, so the column reads as its label
            # instead of folding it (and short values with it) across two lines.
            table.add_column(column, overflow="fold", min_width=len(column))
        for row in rows:
            table.add_row(*(str(value) for value in row))
        Console().print(table)

    def _runtime_table(
        self, scope: str | None, plugins: list[dict[str, Any]]
    ) -> None:
        def row(entry: dict[str, Any]) -> tuple[str, ...]:
            base = (
                entry.get("factory", "-"),
                "enabled" if entry.get("enabled", True) else "disabled",
                entry.get("status", "-"),
                f"{entry.get('package_id', '-')}/{entry.get('contribution_id', '-')}",
                entry.get("instance", "-"),
            )
            if scope is None:
                return (str(entry.get("scope_id", "-")), *base)
            return base

        self._table(
            f"Runtime plugins · {'all scopes' if scope is None else f'scope {scope}'}",
            (
                "Scope", "Factory", "State", "Status",
                "Package / contribution", "Instance",
            )
            if scope is None
            else (
                "Factory", "State", "Status",
                "Package / contribution", "Instance",
            ),
            (row(entry) for entry in sorted(plugins, key=lambda item: str(item.get("factory", "")))),
        )

    def _config_table(self, scope: str, payload: dict[str, Any]) -> None:
        plugins = payload.get("plugins") or {}
        self._table(
            f"Configuration overrides · scope {scope} · version {payload.get('version')}",
            ("Plugin", "State", "Properties"),
            (
                (
                    name,
                    "enabled" if entry.get("enabled", True) else "disabled",
                    " ".join(
                        f"{key}={value}"
                        for key, value in sorted((entry.get("properties") or {}).items())
                    )
                    or "-",
                )
                for name, entry in sorted(plugins.items())
            ),
        )

    def _config_all_table(self, configs: list[dict[str, Any]]) -> None:
        rows = []
        for config in configs:
            scope = config.get("scope", "-")
            version = config.get("version", "-")
            for name, entry in (config.get("plugins") or {}).items():
                rows.append(
                    (
                        scope,
                        version,
                        name,
                        "enabled" if entry.get("enabled", True) else "disabled",
                        " ".join(
                            f"{key}={value}"
                            for key, value in sorted(
                                (entry.get("properties") or {}).items()
                            )
                        )
                        or "-",
                    )
                )
        if not rows:
            print("configuration scopes: no plugin overrides")
            return
        self._table(
            "Configuration overrides · all scopes",
            ("Scope", "Version", "Plugin", "State", "Properties"),
            rows,
        )

    def _registration_table(self, title: str, payload: dict[str, Any]) -> None:
        self._table(
            title,
            ("Factory", "Scope", "State", "Status", "Instance"),
            ((
                payload.get("factory", "-"),
                payload.get("scope_id", "-"),
                "enabled" if payload.get("enabled", True) else "disabled",
                payload.get("status", "-"),
                payload.get("instance", "-"),
            ),),
        )

    def _render_noninteractive_result(
        self, action: str, scope: str | None, payload: dict[str, Any]
    ) -> None:
        if action == "list":
            plugins = payload.get("plugins") or []
            if plugins:
                self._runtime_table(scope, plugins)
            else:
                print(f"runtime scope {scope or 'all scopes'}: no registered plugins")
        elif action == "config":
            if scope is None:
                self._config_all_table(payload.get("scopes") or [])
            else:
                self._config_table(scope, payload)
        elif action == "discover":
            self._table("Discovered plugins", ("Package",), ((item.get("id", "-"),) for item in payload.get("packages") or []))
        else:
            self._registration_table("Plugin result", payload)

    @staticmethod
    def _properties(pairs: list[str]) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        for pair in pairs:
            key, separator, value = pair.partition("=")
            if not separator or not key:
                raise ValueError(f"Expected KEY=VALUE, got: {pair}")
            properties[key] = _coerce(value)
        return properties

    def _plugin_arguments(
        self, arguments: list[str]
    ) -> tuple[str, str | None, list[str]]:
        if len(arguments) < 2:
            return arguments[0], None, []
        return arguments[0], arguments[1], arguments[2:]

    @staticmethod
    def _error_message(exc: Exception) -> str:
        if isinstance(exc, httpx.HTTPStatusError):
            try:
                detail = exc.response.json().get("detail")
            except Exception:
                return str(exc)
            if isinstance(detail, str):
                return detail
            if isinstance(detail, dict):
                message = detail.get("message")
                return str(message) if message else str(detail)
            return str(detail)
        return str(exc)

    def _headers(self, context: InteractiveCommandContext) -> dict[str, str]:
        return {"Authorization": f"Bearer {context.token}"}

    def _get(
        self, context: InteractiveCommandContext, path: str, *, scope: str
    ) -> dict[str, Any]:
        response = httpx.get(
            f"{context.base_url.rstrip('/')}{path}",
            params={"scope": scope},
            headers=self._headers(context),
            timeout=10.0,
        )
        response.raise_for_status()
        return dict(response.json())

    def _put(
        self,
        context: InteractiveCommandContext,
        path: str,
        body: dict[str, Any],
        *,
        scope: str,
    ) -> dict[str, Any]:
        response = httpx.put(
            f"{context.base_url.rstrip('/')}{path}",
            params={"scope": scope},
            json={**body, "actor": "cli"},
            headers=self._headers(context),
            timeout=10.0,
        )
        response.raise_for_status()
        return dict(response.json())

    def _post(
        self,
        context: InteractiveCommandContext,
        path: str,
        body: dict[str, Any],
        *,
        scope: str,
    ) -> dict[str, Any]:
        response = httpx.post(
            f"{context.base_url.rstrip('/')}{path}",
            params={"scope": scope},
            json={**body, "actor": "cli"},
            headers=self._headers(context),
            timeout=10.0,
        )
        response.raise_for_status()
        return dict(response.json())

    def _get_raw(
        self,
        context: Any,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = httpx.get(
            f"{context.base_url.rstrip('/')}{path}",
            params=params,
            headers=self._headers(context),
            timeout=10.0,
        )
        response.raise_for_status()
        return dict(response.json())

    def _post_raw(
        self, context: Any, path: str, body: dict[str, Any], *, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        response = httpx.post(
            f"{context.base_url.rstrip('/')}{path}",
            params=params,
            json=body,
            headers=self._headers(context),
            timeout=10.0,
        )
        response.raise_for_status()
        return dict(response.json())

    def _put_raw(
        self, context: Any, path: str, body: dict[str, Any], *, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        response = httpx.put(
            f"{context.base_url.rstrip('/')}{path}",
            params=params,
            json=body,
            headers=self._headers(context),
            timeout=10.0,
        )
        response.raise_for_status()
        return dict(response.json())

    def _delete_raw(
        self, context: Any, path: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        response = httpx.delete(
            f"{context.base_url.rstrip('/')}{path}",
            params=params,
            headers=self._headers(context),
            timeout=10.0,
        )
        response.raise_for_status()
        return dict(response.json())

"""Interactive plugin-configuration command plugin."""

from __future__ import annotations

import json
from argparse import ArgumentParser, Namespace
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
            choices=(
                "discover",
                "list",
                "runtime",
                "config",
                "install",
                "enable",
                "disable",
                "upgrade",
                "uninstall",
            ),
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
                    "Plugin management: discover|list|runtime|config|install|uninstall|"
                    "set|enable|disable|history|rollback "
                    "[scope]; list accepts runtime scopes such as server, ui, agent, or agent:<id>"
                ),
                handler=self._handle,
            )
        ]

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}

    def _handle(self, context: InteractiveCommandContext, line: str) -> bool:
        words = line.split()
        if not words:
            self._usage()
            return False
        action, arguments = words[0].lower(), words[1:]
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
            elif action in ("enable", "disable"):
                self._set_runtime_enabled(context, action, arguments)
            elif action == "history":
                self._history(context, arguments)
            elif action == "rollback":
                self._rollback(context, arguments)
            else:
                print(f"Unknown /plugins action: {action}")
                self._usage()
        except (httpx.HTTPError, ValueError) as exc:
            print(f"Plugin request failed: {self._error_message(exc)}")
        return False

    def _usage(self) -> None:
        print(
            "usage: /plugins discover"
            " | /plugins list [runtime_scope]"
            " | /plugins runtime set <scope> <plugin> KEY=VALUE [KEY=VALUE ...]"
            " | /plugins config [config_scope]"
            " | /plugins config enable|disable <config_scope> <plugin>"
            " | /plugins history <config_scope>"
            " | /plugins rollback <config_scope> <version>"
            " | /plugins install <package_id> <contribution_id> <scope>"
            " | /plugins uninstall <scope> <plugin_name>"
            " | /plugins set <config_scope> <plugin> KEY=VALUE"
            " | /plugins enable|disable <runtime_scope> <plugin>"
        )

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

    def _set_runtime_enabled(
        self, context: InteractiveCommandContext, action: str, arguments: list[str]
    ) -> None:
        if len(arguments) != 2 or not _is_runtime_scope(arguments[0]):
            print("Scope is required. Specify root, server, ui, agent, or agent:<id>.")
            self._usage()
            return
        scope, name = arguments
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

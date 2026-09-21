"""Interactive command-line shell."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterable, Mapping
from functools import partial
from pathlib import Path
from typing import Any

import httpx
from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer
from prompt_toolkit.history import FileHistory, History, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.shortcuts import CompleteStyle

from langharness_cli.common.completion import ArgumentSource, PaletteCompleter
from langharness_cli.common.i18n import tr
from langharness_cli.common.session import DEFAULT_AGENT_ID, DEFAULT_USER_ID
from langharness_cli.common.theme import build_palette_style
from langharness_cli.common.toolview import render_headline
from langharness_cli.contracts import InteractiveCommandSpec, InteractiveRenderer
from langharness_cli.plugins.rich_renderer import RichInteractiveRenderer


class InteractiveCLIRunner:
    intro = "Type a message to start a conversation.\n/help shows commands · /exit leaves safely"
    prompt = "langharness> "

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        commands: Iterable[InteractiveCommandSpec],
        model: str = "gpt-4o-mini",
        provider_name: str = "environment",
        model_protocol: str = "chat",
        api_key: str = "",
        model_base_url: str = "",
        configs: Any = None,
        renderer: InteractiveRenderer | None = None,
        history_file: str | None = None,
        session: Any = None,
        locale: str = "en",
        user_id: str = DEFAULT_USER_ID,
        agent_id: str = DEFAULT_AGENT_ID,
        session_id: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.model = model
        self.provider_name = provider_name
        self.model_protocol = model_protocol
        self.api_key = api_key
        self.model_base_url = model_base_url.rstrip("/")
        self.configs = configs
        self.log: Any = None
        self.renderer = renderer or RichInteractiveRenderer()
        self.locale = locale
        self.user_id = user_id
        self.agent_id = agent_id
        self.session_id = session_id
        self._tool_headlines: dict[str, dict[str, str]] = {}
        self._update_model_status()
        self.commands: Mapping[str, InteractiveCommandSpec] = {
            command.name: command for command in commands
        }
        self._interactive_input = sys.stdin.isatty()
        history: History = InMemoryHistory()
        if history_file:
            path = Path(history_file).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            history = FileHistory(str(path))
        self._session = session
        if self._session is None and self._interactive_input:
            self._session = PromptSession(
                history=history,
                auto_suggest=AutoSuggestFromHistory(),
                style=build_palette_style(),
                key_bindings=self._key_bindings(),
            )

    def _key_bindings(self) -> KeyBindings:
        """Shell-level keys, which win over prompt_toolkit's defaults.

        ctrl+o is taken from readline's operate-and-get-next. The transcript
        is the thing worth acting on here, and that binding is obscure enough
        to be worth the trade.
        """
        bindings = KeyBindings()

        @bindings.add("c-o")
        def _expand(event: Any) -> None:
            self.renderer.expand_last()

        return bindings

    def cmdloop(self, intro: str | None = None) -> None:
        self.renderer.show_welcome(intro or tr(self.locale, "intro"))
        if hasattr(self.renderer, "get_status_text"):
            toolbar: Any = self.renderer.get_status_text
        else:
            toolbar = f" {self.model} · {tr(self.locale, 'toolbar_hint')} "
        while True:
            try:
                line = (
                    self._session.prompt(
                        [("class:prompt", self.prompt)],
                        completer=self._command_completer(),
                        complete_style=CompleteStyle.COLUMN,
                        bottom_toolbar=toolbar,
                    )
                    if self._interactive_input and self._session is not None
                    else input(self.prompt)
                )
            except EOFError:
                self.do_EOF("")
                return
            except KeyboardInterrupt:
                self.renderer.show_error(tr(self.locale, "cancelled"))
                continue
            if self.onecmd(line):
                return

    def _command_completer(self) -> Completer:
        return PaletteCompleter(self.commands, self._argument_sources())

    def _argument_sources(self) -> Mapping[str, ArgumentSource]:
        """Completers the command plugins declared, bound to this runner.

        The shell owns no grammar of its own: each command hands in a callable
        next to the handler that consumes the same arguments. Sources answer
        from local state only, so a candidate never blocks the keystroke on a
        server round trip.
        """
        return {
            name: partial(spec.complete, self)
            for name, spec in self.commands.items()
            if spec.complete is not None
        }

    def do_stream(self, line: str) -> None:
        self.renderer.start_response()
        self._tool_headlines.clear()
        payload: dict[str, Any] = {
            "input": line,
            "model": self.model,
            "protocol": self.model_protocol,
            "api_key": self.api_key,
            "base_url": self.model_base_url,
            "user_id": self.user_id,
            "agent_id": self.agent_id,
        }
        if self.session_id is not None:
            payload["session_id"] = self.session_id
        if self._stream_usage_disabled():
            payload["stream_usage"] = False
        try:
            with httpx.stream(
                "POST",
                f"{self.base_url}/stream",
                json=payload,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=None,
            ) as response:
                response.raise_for_status()
                for line_text in response.iter_lines():
                    event = json.loads(line_text)
                    if self._apply_session_event(event):
                        continue
                    if event.get("type") == "approval_required":
                        request = event.get("request") or {}
                        action_requests = (
                            request.get("action_requests") or []
                            if isinstance(request, dict)
                            else []
                        )
                        tools = [
                            str(action.get("name", "tool"))
                            for action in action_requests
                            if isinstance(action, dict)
                        ]
                        choice = input(
                            f"Approve {', '.join(tools) or 'tool'}? [y]es/[n]o/[e]dit: "
                        ).strip().lower()
                        self._resume_approval(
                            self._build_approval_decisions(action_requests, choice)
                        )
                        continue
                    self.renderer.render_event(
                        self._with_headline(event)
                    )
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            self.renderer.show_error(str(exc))
        except KeyboardInterrupt:
            self.renderer.show_error(tr(self.locale, "cancelled"))
        finally:
            self.renderer.finish_response()

    @staticmethod
    def _build_approval_decisions(
        action_requests: list[dict[str, Any]], choice: str
    ) -> dict[str, Any]:
        """Translate the CLI answer into a LangChain HITLResponse payload.

        The server-side middleware validates that one decision is sent per
        interrupted tool call, in the same order as the action requests.
        """
        if choice in {"", "y", "yes"}:
            return {"decisions": [{"type": "approve"} for _ in action_requests]}
        if choice in {"e", "edit"}:
            decisions: list[dict[str, Any]] = []
            for action in action_requests:
                name = str(action.get("name", "tool"))
                edited = input(f"New args for {name} (JSON): ").strip()
                try:
                    args = json.loads(edited) if edited else action.get("args") or {}
                except json.JSONDecodeError:
                    decisions.append(
                        {"type": "reject", "message": "Invalid edited args JSON"}
                    )
                    continue
                decisions.append(
                    {"type": "edit", "edited_action": {"name": name, "args": args}}
                )
            return {"decisions": decisions}
        return {"decisions": [{"type": "reject"} for _ in action_requests]}

    def _resume_approval(self, decision: dict[str, Any]) -> None:
        payload = {"input": "", "decision": decision, "session_id": self.session_id,
                   "user_id": self.user_id, "agent_id": self.agent_id,
                   "model": self.model, "protocol": self.model_protocol,
                   "api_key": self.api_key, "base_url": self.model_base_url}
        with httpx.stream("POST", f"{self.base_url}/resume", json=payload,
                          headers={"Authorization": f"Bearer {self.token}"}, timeout=None) as response:
            response.raise_for_status()
            for line_text in response.iter_lines():
                event = json.loads(line_text)
                if not self._apply_session_event(event):
                    self.renderer.render_event(self._with_headline(event))

    def _apply_session_event(self, event: Mapping[str, Any]) -> bool:
        if event.get("type") != "session":
            return False
        session_id = event.get("session_id")
        if session_id:
            self.session_id = str(session_id)
        agent_id = event.get("agent_id")
        if agent_id:
            self.agent_id = str(agent_id)
        user_id = event.get("user_id")
        if user_id:
            self.user_id = str(user_id)
        self._update_model_status()
        return True

    @staticmethod
    def _stream_usage_disabled() -> bool:
        return os.environ.get("LANG_HARNESS_STREAM_USAGE", "").lower() in ("false", "0")

    def do_exit(self, line: str) -> bool:
        return True

    def do_quit(self, line: str) -> bool:
        return True

    def do_EOF(self, line: str) -> bool:
        self.renderer.finish_response()
        return True

    def onecmd(self, line: str) -> bool:
        command_line_value = line.strip()
        if command_line_value == "exit":
            return self.do_exit("")
        if command_line_value == "quit":
            return self.do_quit("")
        if command_line_value == "EOF":
            return self.do_EOF("")
        if command_line_value == "help" and "help" in self.commands:
            return bool(self.commands["help"].handler(self, ""))
        if line.startswith("/"):
            command_line = line[1:].strip()
            name, _, arguments = command_line.partition(" ")
            command = self.commands.get(name)
            if command is None:
                self.renderer.show_error(tr(self.locale, "unknown_command", name=name))
                return False
            return bool(command.handler(self, arguments.strip()))
        self.default(line)
        return False

    def default(self, line: str) -> None:
        if not line.strip():
            return
        self.do_stream(line)

    def list_providers(self) -> list[str]:
        if self.configs is None:
            return []
        return [str(name) for name in self.configs.list_providers()]

    def switch_provider(self, name: str) -> bool:
        if self.configs is None:
            self.renderer.show_error(tr(self.locale, "model_unknown", name=name))
            return False
        try:
            provider = self.configs.get_provider(name)
        except ValueError as exc:
            self.renderer.show_error(str(exc))
            return False
        if not provider:
            self.renderer.show_error(tr(self.locale, "model_unknown", name=name))
            return False
        self.provider_name = name
        self.model = str(provider["model"])
        self.model_protocol = str(provider.get("protocol", "chat"))
        self.api_key = str(provider.get("api_key", ""))
        self.model_base_url = str(provider.get("base_url", "")).rstrip("/")
        self._update_model_status()
        print(
            tr(
                self.locale,
                "model_switched",
                name=name,
                model=self.model,
                protocol=self.model_protocol,
            )
        )
        return True

    def refresh_status(self) -> None:
        """Re-render the status toolbar after identity changes."""
        self._update_model_status()

    def _headlines_for(self, agent_id: str) -> dict[str, str]:
        """Tool templates for one agent, fetched at most once per response.

        A failed lookup is cached as empty so an unreachable server costs one
        attempt rather than one per tool call; the cache is dropped at the
        start of each response.
        """
        if agent_id not in self._tool_headlines:
            self._tool_headlines[agent_id] = self._fetch_headlines(agent_id)
        return self._tool_headlines[agent_id]

    def _fetch_headlines(self, agent_id: str) -> dict[str, str]:
        try:
            response = httpx.get(
                f"{self.base_url}/agents/{agent_id}/tools",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=5.0,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, json.JSONDecodeError):
            return {}
        return {
            str(entry.get("name", "")): str(entry.get("headline", ""))
            for entry in payload.get("tools") or []
            if entry.get("name") and entry.get("headline")
        }

    def _with_headline(self, event: dict[str, Any]) -> dict[str, Any]:
        """Stamp the rendered headline onto a tool_call event.

        The renderer reads ``headline`` and never learns that templates
        exist, so a command's presentation stays out of its way.
        """
        if event.get("type") != "tool_call":
            return event
        template = self._headlines_for(self.agent_id).get(str(event.get("name", "")))
        if not template:
            return event
        rendered = render_headline(template, event.get("args") or {})
        if rendered:
            event["headline"] = rendered
        return event

    def _update_model_status(self) -> None:
        if not hasattr(self.renderer, "set_model"):
            return
        session = (
            self.session_id[:8]
            if self.session_id
            else tr(self.locale, "session_new")
        )
        self.renderer.set_model(
            f"{self.user_id}@{self.agent_id} · {session}"
            f" · {self.provider_name} · {self.model} · {self.model_protocol}"
        )

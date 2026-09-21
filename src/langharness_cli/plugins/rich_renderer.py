"""Claude Code-style rich renderer for the interactive CLI."""

from __future__ import annotations

import time as time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pelix.ipopo.decorators import ComponentFactory, Property, Provides
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from langharness_cli.common.i18n import tr
from langharness_cli.contracts import InteractiveRenderer

SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
THROTTLE_SECONDS = 0.125
ARG_SUMMARY_MAX = 60
# Status line plus slack: the live frame must stay shorter than the terminal,
# otherwise rich cannot move the cursor back to the top and each re-render
# duplicates the whole frame into the scrollback.
LIVE_MARGIN = 3
#: Below this width the two halves of the welcome banner are stacked instead
#: of placed side by side; the highlights column gets unreadable otherwise.
BANNER_MIN_WIDTH = 80

APP_LOGO = (
    " ██╗     ██╗  ██╗\n"
    " ██║     ██║  ██║\n"
    " ██║     ███████║\n"
    " ███████╗██╔══██║\n"
    " ╚══════╝╚═╝  ╚═╝"
)


@dataclass
class _ToolRun:
    """One tool invocation as tracked by the renderer."""

    name: str
    args_summary: str
    started: float
    status: str = "running"
    duration_ms: int = 0
    output_bytes: int = 0


@ComponentFactory("rich-cli-renderer-factory")
@Provides(InteractiveRenderer)
@Property("_plugin_name", "plugin.name", "rich-renderer")
@Property("_plugin_version", "plugin.version", "1.0.0")
@Property("_locale", "plugin.ui.locale", "en")
class RichInteractiveRenderer:
    """Renders conversation events while keeping transport details out of the UI."""

    def __init__(self) -> None:
        self._plugin_name = "rich-renderer"
        self._plugin_version = "1.0.0"
        self._locale = "en"
        self.console = Console()
        self._model = ""
        self._segments: list[str | _ToolRun] = []
        self._tool_runs: dict[str, _ToolRun] = {}
        self._session_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        self._response_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        self._status = "idle"
        self._live: Live | None = None
        self._last_frame = float("-inf")
        self._spinner_idx = 0

    def show_welcome(self, text: str, *, highlights: Sequence[str] = ()) -> None:
        """Welcome screen: identity on the left, what's new on the right.

        ``highlights`` is supplied by the caller rather than read from
        anywhere here -- the renderer has no business knowing what counts as
        news, and an empty list is the common case.
        """
        identity = Panel(
            Group(Text(APP_LOGO, style="cyan"), Text(""), Text(text, style="dim")),
            title="[bold cyan]langharness[/bold cyan]",
            border_style="cyan",
        )
        if not highlights:
            self.console.print(identity)
            return
        news = Panel(
            Group(*(Text(f"· {item}") for item in highlights)),
            title="[bold cyan]What's new[/bold cyan]",
            border_style="cyan",
        )
        if self.console.width < BANNER_MIN_WIDTH:
            self.console.print(identity)
            self.console.print(news)
            return
        grid = Table.grid(padding=(0, 2))
        grid.add_column()
        grid.add_column(ratio=1)
        grid.add_row(identity, news)
        self.console.print(grid)

    def set_model(self, name: str) -> None:
        self._model = name

    def get_status_text(self) -> str:
        parts = []
        if self._model:
            parts.append(self._model)
        if self._session_usage["total_tokens"]:
            parts.append(
                tr(
                    self._locale,
                    "tokens",
                    count=self._format_tokens(self._session_usage["total_tokens"]),
                )
            )
        parts.append(tr(self._locale, "toolbar_hint"))
        return " " + " · ".join(parts) + " "

    def start_response(self) -> None:
        self._segments = []
        self._tool_runs = {}
        self._response_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        self._status = "thinking"
        self._live = None
        self._last_frame = float("-inf")
        self._spinner_idx = 0

    def render_event(self, event: Mapping[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "assistant":
            self._on_assistant(str(event.get("content", "")))
        elif event_type == "tool_call":
            self._on_tool_call(event)
        elif event_type == "tool_output":
            self._on_tool_output(event)
        elif event_type == "usage":
            self._on_usage(event)
        elif event_type == "error":
            self.show_error(str(event.get("message", "Unknown stream error")))
        # Unknown event types are ignored.

    def finish_response(self) -> None:
        self._flush_live()
        self._status = "idle"

    def show_error(self, message: str) -> None:
        self._flush_live()
        self._status = "error"
        prefix = tr(self._locale, "error_prefix")
        self.console.print(Text.assemble((f"{prefix}: ", "bold red"), message))

    def _on_assistant(self, content: str) -> None:
        if not content:
            return
        if not self.console.is_terminal:
            self.console.print(content, end="", highlight=False, markup=False)
            return
        if self._segments and isinstance(self._segments[-1], str):
            self._segments[-1] += content
        else:
            self._segments.append(content)
        self._refresh()

    def _on_tool_call(self, event: Mapping[str, Any]) -> None:
        name = str(event.get("name", "tool"))
        tool_call_id = str(event.get("tool_call_id") or f"run-{len(self._tool_runs)}")
        args_summary = self._summarize_args(event.get("args"))
        run = _ToolRun(name=name, args_summary=args_summary, started=time.monotonic())
        self._tool_runs[tool_call_id] = run
        self._segments.append(run)
        self._status = "tool"
        if not self.console.is_terminal:
            self.console.print(f"+ {name} {args_summary}".rstrip())
            return
        self._refresh(force=True)

    def _on_tool_output(self, event: Mapping[str, Any]) -> None:
        name = str(event.get("name", "tool"))
        tool_call_id = str(event.get("tool_call_id", ""))
        output = str(event.get("output", ""))
        run = self._tool_runs.get(tool_call_id) or self._find_running_run(name)
        if run is None:
            run = _ToolRun(name=name, args_summary="", started=time.monotonic())
            self._segments.append(run)
        run.status = "error" if self._is_error(output) else "done"
        run.duration_ms = max(0, int((time.monotonic() - run.started) * 1000))
        run.output_bytes = len(output.encode("utf-8"))
        if not self.console.is_terminal:
            self.console.print(self._tool_result_line(run))
            return
        self._refresh(force=True)

    def _on_usage(self, event: Mapping[str, Any]) -> None:
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            value = int(event.get(key, 0))
            self._response_usage[key] += value
            self._session_usage[key] += value
        if self.console.is_terminal and self._live is not None:
            self._refresh(force=True)

    def _find_running_run(self, name: str) -> _ToolRun | None:
        for run in self._tool_runs.values():
            if run.status == "running" and run.name == name:
                return run
        return None

    def _refresh(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_frame < THROTTLE_SECONDS:
            return
        self._last_frame = now
        committed = self._commit_overflow()
        if self._live is not None and committed:
            self._live.update(self._build_frame())
        for chunk in committed:
            self.console.print(chunk)
        self._render_frame()

    def _live_budget(self) -> int:
        return max(1, self.console.height - LIVE_MARGIN)

    def _commit_overflow(self) -> list[Any]:
        """Trim segments that no longer fit the live frame; return them for printing."""
        committed: list[Any] = []
        while True:
            rendered = self.console.render_lines(
                self._build_frame(), self.console.options
            )
            excess = len(rendered) - self._live_budget()
            if excess <= 0 or not self._segments:
                return committed
            head = self._segments[0]
            if not isinstance(head, str):
                committed.append(self._tool_line(head))
                self._segments.pop(0)
                continue
            lines = head.split("\n")
            if len(lines) > 1:
                cut = min(excess, len(lines) - 1)
                for boundary in range(cut, 0, -1):
                    if lines[boundary - 1].strip() == "":
                        cut = boundary
                        break
                committed.append(Markdown("\n".join(lines[:cut])))
                self._segments[0] = "\n".join(lines[cut:])
            else:
                width = max(20, self.console.width - 2)
                keep_chars = max(width, len(head) - (excess + 1) * width)
                committed.append(Markdown(head[:keep_chars]))
                self._segments[0] = head[keep_chars:]
            if not self._segments[0]:
                self._segments.pop(0)

    def _render_frame(self) -> None:
        self._spinner_idx += 1
        if self._live is None:
            self._live = Live(
                self._build_frame(),
                console=self.console,
                refresh_per_second=8,
                vertical_overflow="crop",
            )
            self._live.start(refresh=True)
        else:
            self._live.update(self._build_frame(), refresh=True)

    def _flush_live(self) -> None:
        if self._live is not None:
            self._refresh(force=True)
            self._live.stop()
        self._live = None

    def _build_frame(self) -> Group:
        items: list[Any] = []
        for segment in self._segments:
            if isinstance(segment, str):
                items.append(Markdown(segment))
            else:
                items.append(self._tool_line(segment))
        items.append(Text(self._status_line(), style="dim"))
        return Group(*items)

    def _tool_line(self, run: _ToolRun) -> Text:
        if run.status == "running":
            return Text.assemble(
                (self._spinner(), "cyan"),
                (" ", ""),
                (run.name, "bold cyan"),
                (f" {run.args_summary}" if run.args_summary else "", "dim"),
            )
        if run.status == "error":
            return Text.assemble(
                ("✗ ", "red"),
                (run.name, "bold"),
                (f" ({tr(self._locale, 'tool_error')})", "red"),
            )
        meta = self._result_meta(run)
        return Text.assemble(
            ("✓ ", "green"),
            (run.name, "bold"),
            (f" ({meta})" if meta else "", "dim"),
        )

    def _tool_result_line(self, run: _ToolRun) -> str:
        if run.status == "error":
            return f"✗ {run.name} ({tr(self._locale, 'tool_error')})"
        meta = self._result_meta(run)
        return f"✓ {run.name} ({meta})" if meta else f"✓ {run.name}"

    def _result_meta(self, run: _ToolRun) -> str:
        parts = []
        if run.duration_ms > 0:
            parts.append(f"{run.duration_ms}ms")
        if run.output_bytes > 0:
            parts.append(self._format_size(run.output_bytes))
        return " · ".join(parts)

    def _status_line(self) -> str:
        if self._status == "tool":
            run = next(
                (run for run in self._tool_runs.values() if run.status == "running"),
                None,
            )
            label = f"{run.name} {run.args_summary}".rstrip() if run else tr(
                self._locale, "thinking"
            )
        else:
            label = tr(self._locale, "thinking")
        parts = [f"{self._spinner()} {label}"]
        if self._model:
            parts.append(self._model)
        usage = self._response_usage
        if usage["total_tokens"]:
            parts.append(
                tr(
                    self._locale,
                    "usage_io",
                    in_tokens=self._format_tokens(usage["input_tokens"]),
                    out_tokens=self._format_tokens(usage["output_tokens"]),
                )
            )
        return " · ".join(parts)

    def _spinner(self) -> str:
        return SPINNER_FRAMES[self._spinner_idx % len(SPINNER_FRAMES)]

    def _summarize_args(self, args: Any) -> str:
        summary = " ".join(str(args).split())
        return summary[:ARG_SUMMARY_MAX] + "…" if len(summary) > ARG_SUMMARY_MAX else summary

    def _is_error(self, output: str) -> bool:
        return output.lstrip().lower().startswith("error")

    def _format_tokens(self, count: int) -> str:
        if count < 1000:
            return str(count)
        return f"{count / 1000:.1f}k"

    def _format_size(self, size: int) -> str:
        if size < 1024:
            return f"{size}B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f}KB"
        return f"{size / (1024 * 1024):.1f}MB"

    def get_plugin_info(self) -> dict[str, str]:
        return {"name": self._plugin_name, "version": self._plugin_version}

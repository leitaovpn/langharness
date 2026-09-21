"""Rich interactive renderer tests."""

from __future__ import annotations

import re
from io import StringIO
from typing import Any

import pytest
from rich.console import Console

import langharness_cli.plugins.rich_renderer as renderer_module
from langharness_cli.contracts import InteractiveRenderer
from langharness_cli.plugins.rich_renderer import RichInteractiveRenderer

USAGE = {"type": "usage", "input_tokens": 10, "output_tokens": 2, "total_tokens": 12}


class _TerminalEmulator:
    """Minimal VT100 emulator covering the control codes rich Live emits.

    Tracks the visible pane plus scrolled history so tests can assert what a
    real terminal would show after a stream of live re-renders.
    """

    def __init__(self, rows: int, cols: int) -> None:
        self.rows = rows
        self.cols = cols
        self.screen = [[" "] * cols for _ in range(rows)]
        self.history: list[str] = []
        self.row = 0
        self.col = 0

    def _scroll(self) -> None:
        self.history.append("".join(self.screen[0]).rstrip())
        self.screen.pop(0)
        self.screen.append([" "] * self.cols)

    def _csi(self, params: str, final: str) -> None:
        arg = int(params) if params.isdigit() else 1
        if final == "A":
            self.row = max(0, self.row - arg)
        elif final == "B":
            self.row = min(self.rows - 1, self.row + arg)
        elif final == "C":
            self.col = min(self.cols - 1, self.col + arg)
        elif final == "D":
            self.col = max(0, self.col - arg)
        elif final == "K":
            mode = params or "0"
            start, end = (self.col, self.cols) if mode == "0" else (0, self.cols)
            if mode == "1":
                start, end = 0, self.col + 1
            for x in range(start, end):
                self.screen[self.row][x] = " "
        elif final == "J" and params == "2":
            self.screen = [[" "] * self.cols for _ in range(self.rows)]

    def feed(self, data: str) -> None:
        i = 0
        n = len(data)
        while i < n:
            ch = data[i]
            if ch == "\x1b":
                if i + 1 < n and data[i + 1] == "[":
                    j = i + 2
                    while j < n and not ("@" <= data[j] <= "~"):
                        j += 1
                    self._csi(data[i + 2 : j], data[j] if j < n else "")
                    i = j + 1
                else:
                    i += 2
            elif ch == "\r":
                self.col = 0
                i += 1
            elif ch == "\n":
                if self.row == self.rows - 1:
                    self._scroll()
                else:
                    self.row += 1
                self.col = 0
                i += 1
            else:
                if self.col < self.cols:
                    self.screen[self.row][self.col] = ch
                self.col += 1
                i += 1

    def lines(self) -> list[str]:
        return self.history + ["".join(row).rstrip() for row in self.screen]


def terminal_lines(raw: str, rows: int = 24, cols: int = 100) -> list[str]:
    emulator = _TerminalEmulator(rows, cols)
    emulator.feed(raw)
    return emulator.lines()


def make_renderer(
    terminal: bool = False, width: int = 100
) -> tuple[RichInteractiveRenderer, StringIO]:
    output = StringIO()
    renderer = RichInteractiveRenderer()
    renderer.console = Console(file=output, force_terminal=terminal, width=width)
    return renderer, output


def render_plain(renderable: Any, width: int = 100) -> str:
    output = StringIO()
    Console(file=output, force_terminal=False, color_system=None, width=width).print(
        renderable
    )
    return output.getvalue()


def patch_clock(monkeypatch: pytest.MonkeyPatch, values: list[float]) -> None:
    clock = iter(values)
    monkeypatch.setattr(renderer_module.time, "monotonic", lambda: next(clock))


def test_renderer_conforms_to_protocol() -> None:
    assert isinstance(RichInteractiveRenderer(), InteractiveRenderer)


def test_renderer_renders_welcome_and_localized_errors() -> None:
    renderer, output = make_renderer()
    renderer.show_welcome("Use /help")
    renderer.show_error("broken")
    rendered = output.getvalue()
    assert "langharness" in rendered
    assert "Use /help" in rendered
    assert "Error: broken" in rendered

    zh, zh_output = make_renderer()
    zh._locale = "zh"
    zh.show_error("坏了")
    assert "错误: 坏了" in zh_output.getvalue()


def test_renderer_renders_stream_error_events() -> None:
    renderer, output = make_renderer()
    renderer.start_response()
    renderer.render_event(
        {"type": "error", "error_type": "RuntimeError", "message": "upstream failed"}
    )
    renderer.finish_response()

    assert "Error: upstream failed" in output.getvalue()


def test_plain_path_appends_assistant_text_without_reprinting() -> None:
    renderer, output = make_renderer()
    renderer.start_response()
    renderer.render_event({"type": "assistant", "content": "hel"})
    renderer.render_event({"type": "assistant", "content": "lo"})
    renderer.finish_response()
    rendered = output.getvalue()
    assert rendered.count("hello") == 1
    assert rendered.endswith("hello")


def test_plain_path_does_not_interpret_markup() -> None:
    renderer, output = make_renderer()
    renderer.start_response()
    renderer.render_event({"type": "assistant", "content": "**bold** [x]"})
    renderer.finish_response()
    assert "**bold** [x]" in output.getvalue()


def test_plain_path_renders_tool_rows_with_duration_and_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_clock(monkeypatch, [100.0, 100.25])
    renderer, output = make_renderer()
    renderer.start_response()
    renderer.render_event(
        {"type": "tool_call", "name": "bash", "tool_call_id": "call-1", "args": {"commands": "pwd"}}
    )
    renderer.render_event(
        {"type": "tool_output", "name": "bash", "tool_call_id": "call-1", "output": "f" * 2400}
    )
    renderer.finish_response()
    rendered = output.getvalue()
    assert "● bash" in rendered
    assert "⎿" in rendered
    assert "f" * 50 in rendered


def test_plain_path_matches_output_to_running_tool_without_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_clock(monkeypatch, [0.0, 0.01])
    renderer, output = make_renderer()
    renderer.start_response()
    renderer.render_event({"type": "tool_call", "name": "bash", "args": {"commands": "pwd"}})
    renderer.render_event({"type": "tool_output", "name": "bash", "output": "/workspace"})
    renderer.finish_response()
    rendered = output.getvalue()
    assert "● bash" in rendered
    assert "⎿ /workspace" in rendered


def test_plain_path_marks_error_outputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_clock(monkeypatch, [0.0, 0.01])
    renderer, output = make_renderer()
    renderer.start_response()
    renderer.render_event(
        {"type": "tool_call", "name": "bash", "tool_call_id": "c1", "args": "x"}
    )
    renderer.render_event(
        {"type": "tool_output", "name": "bash", "tool_call_id": "c1", "output": "Error: boom"}
    )
    renderer.finish_response()
    assert "● bash" in output.getvalue()
    assert "⎿ Error: boom" in output.getvalue()


def test_unknown_events_are_ignored() -> None:
    renderer, output = make_renderer()
    renderer.start_response()
    renderer.render_event({"type": "mystery", "content": "??"})
    renderer.finish_response()
    assert output.getvalue() == ""


def test_usage_accumulates_across_responses_into_status_text() -> None:
    renderer, _ = make_renderer()
    renderer.set_model("deepseek-v4-flash")
    assert renderer.get_status_text() == " deepseek-v4-flash · /help for commands "
    renderer.start_response()
    renderer.render_event(USAGE)
    renderer.finish_response()
    renderer.start_response()
    renderer.render_event(USAGE)
    renderer.finish_response()
    assert (
        renderer.get_status_text()
        == " deepseek-v4-flash · 24 tokens · /help for commands "
    )


def test_status_text_is_localized() -> None:
    renderer, _ = make_renderer()
    renderer._locale = "zh"
    renderer.set_model("deepseek-v4-flash")
    renderer.start_response()
    renderer.render_event(USAGE)
    renderer.finish_response()
    assert (
        renderer.get_status_text()
        == " deepseek-v4-flash · 12 令牌 · /help 查看命令 "
    )


def test_status_line_shows_thinking_then_usage() -> None:
    renderer, _ = make_renderer()
    renderer.set_model("m1")
    renderer.start_response()
    assert renderer._status_line() == "⠋ thinking… · m1"
    renderer.render_event(USAGE)
    assert renderer._status_line() == "⠋ thinking… · m1 · 10 in / 2 out"


def test_status_line_shows_running_tool() -> None:
    renderer, _ = make_renderer()
    renderer.set_model("m1")
    renderer.start_response()
    renderer.render_event(
        {"type": "tool_call", "name": "bash", "tool_call_id": "c1", "args": {"commands": "pwd"}}
    )
    assert renderer._status_line() == "⠋ bash · m1"


def test_tool_lines_render_running_done_error_states() -> None:
    renderer, _ = make_renderer()
    running = renderer_module._ToolRun(
        name="bash", headline="Bash(pwd)", args={"commands": "pwd"}, started=0.0
    )
    running_line = render_plain(renderer._tool_line(running))
    assert "Bash(pwd)" in running_line
    done = renderer_module._ToolRun(
        name="bash",
        headline="Bash(pwd)",
        args={"commands": "pwd"},
        started=0.0,
        status="done",
        duration_ms=123,
        output_bytes=2048,
    )
    # Duration and size moved to the ctrl+o expansion, so the row is the
    # headline alone.
    assert "● Bash(pwd)" in render_plain(renderer._tool_line(done))
    assert "123ms" not in render_plain(renderer._tool_line(done))
    failed = renderer_module._ToolRun(
        name="bash", headline="Bash(pwd)", args={}, started=0.0, status="error"
    )
    assert "● Bash(pwd) (error)" in render_plain(renderer._tool_line(failed))


def test_token_and_size_formatting() -> None:
    renderer, _ = make_renderer()
    assert renderer._format_tokens(999) == "999"
    assert renderer._format_tokens(1000) == "1.0k"
    assert renderer._format_tokens(12400) == "12.4k"
    assert renderer._format_size(10) == "10B"
    assert renderer._format_size(2048) == "2.0KB"
    assert renderer._format_size(1572864) == "1.5MB"


def test_tty_path_runs_live_during_stream_and_stops_on_finish() -> None:
    renderer, output = make_renderer(terminal=True)
    renderer.start_response()
    renderer.render_event({"type": "assistant", "content": "hi"})
    assert renderer._live is not None
    assert renderer._status == "thinking"
    renderer.render_event(
        {"type": "tool_call", "name": "bash", "tool_call_id": "c1", "args": "pwd"}
    )
    assert renderer._status == "tool"
    renderer.render_event(
        {"type": "tool_output", "name": "bash", "tool_call_id": "c1", "output": "/w"}
    )
    renderer.finish_response()
    assert renderer._live is None
    assert renderer._status == "idle"
    assert renderer._tool_runs["c1"].status == "done"


def test_frame_contains_markdown_tool_rows_and_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    renderer, _ = make_renderer(terminal=True)
    monkeypatch.setattr(renderer, "_refresh", lambda force=False: None)
    renderer.set_model("m1")
    renderer.start_response()
    renderer.render_event({"type": "assistant", "content": "**hello**"})
    assert "thinking" in renderer._status_line()
    renderer.render_event(
        {"type": "tool_call", "name": "bash", "tool_call_id": "c1", "args": {"commands": "pwd"}}
    )
    rendered = render_plain(renderer._build_frame())
    assert "hello" in rendered
    assert "bash" in rendered
    assert "m1" in rendered


def test_assistant_events_are_throttled_but_flushes_force(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_clock(monkeypatch, [0.0, 0.05, 0.05, 0.13, 0.26, 0.26])
    renderer, _ = make_renderer(terminal=True)
    frames = 0
    original = renderer._render_frame

    def counting() -> None:
        nonlocal frames
        frames += 1
        original()

    monkeypatch.setattr(renderer, "_render_frame", counting)
    renderer.start_response()
    renderer.render_event({"type": "assistant", "content": "a"})  # first always renders
    renderer.render_event({"type": "assistant", "content": "b"})  # throttled
    renderer.render_event(  # forces a frame
        {"type": "tool_call", "name": "bash", "tool_call_id": "c1", "args": "pwd"}
    )
    renderer.render_event({"type": "assistant", "content": "c"})  # window elapsed
    renderer.finish_response()  # flush forces a final frame
    assert frames == 4


def test_long_stream_renders_each_segment_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = StringIO()
    renderer = RichInteractiveRenderer()
    renderer.console = Console(file=output, force_terminal=True, width=100, height=24)
    patch_clock(monkeypatch, [i * 0.2 for i in range(200)])
    renderer.start_response()
    for index in range(30):
        renderer.render_event({"type": "assistant", "content": f"\n\nSEG-{index:04d}"})
    renderer.finish_response()

    visible = "\n".join(terminal_lines(output.getvalue()))
    segments = re.findall(r"SEG-\d{4}", visible)
    assert segments.count("SEG-0001") == 1
    assert len(segments) == 30


def test_overflow_commit_moves_tool_row_out_of_the_frame() -> None:
    output = StringIO()
    renderer = RichInteractiveRenderer()
    renderer.console = Console(file=output, force_terminal=True, width=40, height=6)
    renderer.start_response()
    renderer._segments = [
        renderer_module._ToolRun(
            name="bash", headline="Bash(pwd)", args={}, started=0.0, status="done",
            duration_ms=5, output_bytes=10,
        ),
        "tail\n" * 10,
    ]

    committed = renderer._commit_overflow()

    assert all(
        not isinstance(segment, renderer_module._ToolRun) for segment in renderer._segments
    )
    assert "Bash(pwd)" in render_plain(committed[0])
    # A finished run commits its output line too, or the committed row would
    # be a headline with no result under it.
    assert any("⎿" in render_plain(chunk) for chunk in committed)


def test_overflow_commit_splits_unbroken_long_line() -> None:
    output = StringIO()
    renderer = RichInteractiveRenderer()
    renderer.console = Console(file=output, force_terminal=True, width=40, height=6)
    renderer.start_response()
    long_text = "x" * 500
    renderer._segments = [long_text, "tail"]

    committed = renderer._commit_overflow()

    assert len(renderer._segments) == 2
    assert renderer._segments[1] == "tail"
    head = renderer._segments[0]
    assert isinstance(head, str)
    assert len(head) < len(long_text)
    assert "x" in render_plain(committed[0])


# --- welcome banner -------------------------------------------------------


def test_welcome_banner_places_highlights_beside_the_identity() -> None:
    renderer, output = make_renderer(width=110)

    renderer.show_welcome("Type a message", highlights=["Added /goal"])

    lines = output.getvalue().splitlines()
    # Side by side means the two panels share their opening row, so both
    # titles land on one line.
    news_row = next(line for line in lines if "What's new" in line)
    assert "langharness" in news_row
    assert any("Added /goal" in line for line in lines)


def test_welcome_banner_stacks_when_the_terminal_is_narrow() -> None:
    renderer, output = make_renderer(width=60)

    renderer.show_welcome("Type a message", highlights=["Added /goal"])

    lines = output.getvalue().splitlines()
    news_row = next(i for i, line in enumerate(lines) if "What's new" in line)
    identity_row = next(i for i, line in enumerate(lines) if "langharness" in line)
    assert news_row > identity_row


def test_welcome_banner_titles_the_highlights_panel() -> None:
    renderer, output = make_renderer(width=110)

    renderer.show_welcome("Type a message", highlights=["Added /goal"])

    assert "What's new" in output.getvalue()


def test_welcome_banner_omits_the_highlights_panel_when_empty() -> None:
    renderer, output = make_renderer(width=110)

    renderer.show_welcome("Type a message")

    rendered = output.getvalue()
    assert "Type a message" in rendered
    assert "What's new" not in rendered


# --- tool call rows -------------------------------------------------------


def test_plain_path_renders_a_call_from_its_headline() -> None:
    renderer, output = make_renderer()
    renderer.start_response()
    renderer.render_event(
        {
            "type": "tool_call",
            "name": "bash",
            "tool_call_id": "c1",
            "args": {"commands": "pwd"},
            "headline": "Bash(pwd)",
        }
    )
    renderer.render_event(
        {"type": "tool_output", "name": "bash", "tool_call_id": "c1", "output": "/work"}
    )
    renderer.finish_response()
    rendered = output.getvalue()

    assert "● Bash(pwd)" in rendered
    assert "⎿ /work" in rendered


def test_a_call_without_a_headline_falls_back_to_the_bare_name() -> None:
    renderer, output = make_renderer()
    renderer.start_response()
    renderer.render_event(
        {"type": "tool_call", "name": "mystery", "tool_call_id": "c1", "args": {"x": 1}}
    )
    renderer.finish_response()

    assert "● mystery" in output.getvalue()
    assert "{'x': 1}" not in output.getvalue()


def _frame_after_output(
    monkeypatch: pytest.MonkeyPatch, output: str
) -> str:
    renderer, _ = make_renderer(terminal=True)
    monkeypatch.setattr(renderer, "_refresh", lambda force=False: None)
    renderer.start_response()
    renderer.render_event(
        {"type": "tool_call", "name": "bash", "tool_call_id": "c1", "args": {}}
    )
    renderer.render_event(
        {"type": "tool_output", "name": "bash", "tool_call_id": "c1", "output": output}
    )
    return render_plain(renderer._build_frame())


def test_the_expand_hint_is_absent_when_nothing_was_cut(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert "ctrl+o" not in _frame_after_output(monkeypatch, "short")


def test_cut_output_advertises_the_expand_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    long_output = "\n".join(f"line {index}" for index in range(30))

    assert "ctrl+o" in _frame_after_output(monkeypatch, long_output)

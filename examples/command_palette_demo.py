"""Standalone demo of the interactive command palette.

Run it in a terminal to try the palette by hand::

    python examples/command_palette_demo.py

Or capture what a terminal would actually show, without a human typing::

    python examples/command_palette_demo.py --capture

The capture path drives a real ``PromptSession`` through a pipe, records the
VT100 byte stream it writes, and replays that stream through a small
terminal emulator. What it prints is therefore the rendered screen, not a
mock of one.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Sequence
from io import StringIO

from prompt_toolkit import PromptSession
from prompt_toolkit.data_structures import Size
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output.vt100 import Vt100_Output
from prompt_toolkit.shortcuts import CompleteStyle

from langharness_cli.common.completion import PaletteCompleter
from langharness_cli.common.theme import build_palette_style
from langharness_cli.contracts import InteractiveCommandSpec

#: Stand-in for the command plugins the real CLI loads. Descriptions are
#: deliberately of realistic length, since their width drives the menu layout.
COMMANDS: dict[str, str] = {
    "help": "List the available commands",
    "model": "Switch the active model provider",
    "plugins": "Inspect the loaded plugins",
    "scope": "Manage agent scopes",
    "scroll-speed": "Tune how fast the transcript scrolls",
    "session": "List or resume sessions",
    "whoami": "Show the current identity",
    "exit": "Leave the shell",
}

PROVIDERS = ["default", "demo", "prod"]
PLUGIN_NAMES = ["builtin-api", "builtin-cli", "builtin-core", "rich-renderer"]

PROMPT = "langharness> "
TOOLBAR = " demo · /help for commands "


def _noop(context: object, line: str) -> bool:
    return False


def build_completer() -> PaletteCompleter:
    """Assemble the palette from a fake command table and argument sources."""
    specs = {
        name: InteractiveCommandSpec(name=name, help=help_text, handler=_noop)
        for name, help_text in COMMANDS.items()
    }
    return PaletteCompleter(
        specs,
        {
            "model": lambda prefix: PROVIDERS,
            "plugins": lambda prefix: PLUGIN_NAMES,
        },
    )


def build_session(**kwargs: object) -> PromptSession[str]:
    """A session configured exactly the way the palette needs it."""
    return PromptSession(
        history=InMemoryHistory(),
        completer=build_completer(),
        style=build_palette_style(),
        complete_style=CompleteStyle.COLUMN,
        complete_while_typing=True,
        reserve_space_for_menu=8,
        bottom_toolbar=TOOLBAR,
        **kwargs,  # type: ignore[arg-type]
    )


def run_interactive() -> None:
    """The human path: a normal prompt in the current terminal."""
    session = build_session()
    print("Ctrl-D or /exit to leave.\n")
    while True:
        try:
            line = session.prompt(PROMPT)
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if line.strip() in {"", "/exit", "exit"}:
            if line.strip():
                return
            continue
        print(f"  → would send {line!r} to the agent loop")


# --------------------------------------------------------------------------
# Evidence capture
# --------------------------------------------------------------------------


class Screen:
    """Enough of a VT100 to reconstruct what prompt_toolkit painted.

    prompt_toolkit positions the cursor with ``CSI row;col H`` and toggles
    the cursor with private modes such as ``CSI ?25l``, neither of which the
    emulator in ``tests/test_rich_renderer.py`` handles -- that one only ever
    replays rich ``Live`` frames.
    """

    def __init__(self, rows: int, cols: int) -> None:
        self.rows = rows
        self.cols = cols
        self.grid = [[" "] * cols for _ in range(rows)]
        #: SGR parameters in force for each cell, so the selection and the
        #: fuzzy-match highlight survive into the captured evidence.
        self.sgr = ""
        self.cell_sgr = [[""] * cols for _ in range(rows)]
        self.row = 0
        self.col = 0

    def _put(self, character: str) -> None:
        if self.row < self.rows and self.col < self.cols:
            self.grid[self.row][self.col] = character
            self.cell_sgr[self.row][self.col] = self.sgr
        self.col += 1

    def _erase_line(self, mode: str) -> None:
        start, end = (self.col, self.cols)
        if mode == "1":
            start, end = 0, min(self.col + 1, self.cols)
        elif mode == "2":
            start, end = 0, self.cols
        for column in range(start, end):
            self.grid[self.row][column] = " "
            self.cell_sgr[self.row][column] = ""

    def _erase_characters(self, count: int, *, shift: bool) -> None:
        """ECH (``CSI X``) blanks cells in place; DCH (``CSI P``) pulls the
        rest of the line left. prompt_toolkit uses these to trim a line that
        got shorter, so ignoring them leaves the tail of the previous text on
        screen -- a capture artefact that reads like a product bug."""
        row = self.grid[self.row]
        end = min(self.col + count, self.cols)
        if shift:
            row[self.col :] = row[end:] + [" "] * (end - self.col)
            self.cell_sgr[self.row][self.col :] = (
                self.cell_sgr[self.row][end:] + [""] * (end - self.col)
            )
            return
        for column in range(self.col, end):
            row[column] = " "
            self.cell_sgr[self.row][column] = ""

    def _erase_display(self, mode: str) -> None:
        if mode != "2":
            return
        self.grid = [[" "] * self.cols for _ in range(self.rows)]
        self.cell_sgr = [[""] * self.cols for _ in range(self.rows)]

    def _move(self, final: str, params: str) -> None:
        first, _, second = params.partition(";")
        step = int(first) if first.isdigit() else 1
        if final in "Hf":
            row = int(first) - 1 if first.isdigit() else 0
            col = int(second) - 1 if second.isdigit() else 0
            self.row = max(0, min(self.rows - 1, row))
            self.col = max(0, min(self.cols - 1, col))
        elif final == "A":
            self.row = max(0, self.row - step)
        elif final == "B":
            self.row = min(self.rows - 1, self.row + step)
        elif final == "C":
            self.col = min(self.cols - 1, self.col + step)
        elif final == "D":
            self.col = max(0, self.col - step)
        elif final == "G":
            self.col = max(0, min(self.cols - 1, step - 1))

    def _scroll(self) -> None:
        self.grid.pop(0)
        self.grid.append([" "] * self.cols)
        self.cell_sgr.pop(0)
        self.cell_sgr.append([""] * self.cols)

    def feed(self, data: str) -> None:
        index = 0
        while index < len(data):
            character = data[index]
            if character == "\x1b":
                index = self._feed_escape(data, index)
            elif character == "\r":
                self.col = 0
                index += 1
            elif character == "\n":
                if self.row == self.rows - 1:
                    self._scroll()
                else:
                    self.row += 1
                self.col = 0
                index += 1
            elif character == "\b":
                self.col = max(0, self.col - 1)
                index += 1
            elif character < " ":
                index += 1  # Tab, bell and friends: not part of the picture.
            else:
                self._put(character)
                index += 1

    def _feed_escape(self, data: str, index: int) -> int:
        if index + 1 >= len(data) or data[index + 1] != "[":
            return index + 2
        end = index + 2
        while end < len(data) and not ("@" <= data[end] <= "~"):
            end += 1
        params, final = data[index + 2 : end], data[end] if end < len(data) else ""
        if final in "HfABCDG":
            self._move(final, params)
        elif final == "K":
            self._erase_line(params or "0")
        elif final == "J":
            self._erase_display(params or "0")
        elif final in "XP":
            count = int(params) if params.isdigit() else 1
            self._erase_characters(count, shift=final == "P")
        elif final == "m":
            self.sgr = params or "0"
        # Private modes ('h'/'l') and the rest do not move ink.
        return end + 1

    def lines(self) -> list[str]:
        return [line.rstrip() for line in ("".join(row) for row in self.grid)]

    def markers(
        self, selection_bg: Sequence[str], match_fg: Sequence[str]
    ) -> list[str]:
        """One marker line per row: '~' on the selected row, '^' on hits."""
        marked = []
        for row in range(self.rows):
            line = []
            for col in range(self.cols):
                sgr = self.cell_sgr[row][col]
                if _carries(sgr, selection_bg):
                    line.append("~")
                elif _carries(sgr, match_fg):
                    line.append("^")
                else:
                    line.append(" ")
            marked.append("".join(line).rstrip())
        return marked


def _quiesce(sink: StringIO, quiet: float, timeout: float) -> None:
    """Block until the renderer has stopped writing for ``quiet`` seconds."""
    deadline = time.monotonic() + timeout
    previous, changed_at = -1, time.monotonic()
    while time.monotonic() < deadline:
        current = len(sink.getvalue())
        if current != previous:
            previous, changed_at = current, time.monotonic()
        elif time.monotonic() - changed_at >= quiet:
            return
        time.sleep(0.005)


def capture(keystrokes: Sequence[str], rows: int = 14, cols: int = 74) -> list[str]:
    """Type ``keystrokes`` into a real session and return the screen each time."""
    frames: list[str] = []
    sink = StringIO()
    with create_pipe_input() as pipe:
        output = Vt100_Output(
            sink,
            lambda: Size(rows=rows, columns=cols),
            term="xterm-256color",
        )
        session = build_session(input=pipe, output=output)
        worker = threading.Thread(
            target=lambda: _prompt_quietly(session), daemon=True
        )
        worker.start()
        _quiesce(sink, quiet=0.15, timeout=5.0)
        for chunk in keystrokes:
            pipe.send_text(chunk)
            _quiesce(sink, quiet=0.15, timeout=5.0)
            frames.append(sink.getvalue())
        pipe.send_text("\r")
        _quiesce(sink, quiet=0.15, timeout=5.0)
        worker.join(timeout=5.0)
    return frames


def _prompt_quietly(session: PromptSession[str]) -> None:
    try:
        session.prompt(PROMPT)
    except BaseException:  # The demo thread must never mask the capture.
        pass


KILL_LINE = "\x15"
DOWN = "\x1b[B"

#: One entry per screen worth looking at, as (keys to type, what it shows).
CAPTURE_STEPS: list[tuple[str, str]] = [
    ("/", "every command, name-ordered"),
    ("s", "the prefix narrows the list"),
    ("s", "'/ss' matches as a subsequence"),
    (DOWN, "Down moves onto the current row"),
    (KILL_LINE, "cleared"),
    ("/model ", "argument completion"),
    ("de", "argument candidates filtered by prefix"),
]

ROWS, COLS = 16, 78
#: SGR parameter runs prompt_toolkit emits for the two styles worth proving,
#: as token sequences. Matched as whole parameters, since a plain substring
#: test for "48;5;25" would also fire on "48;5;250", and one for
#: ";48;5;25;" would miss a cell where that run ends the sequence.
MATCH_FG = ("38", "5", "81")
SELECTION_BG = ("48", "5", "25")


def _carries(sgr: str, code: Sequence[str]) -> bool:
    parts = sgr.split(";")
    return any(
        tuple(parts[start : start + len(code)]) == tuple(code)
        for start in range(len(parts) - len(code) + 1)
    )


def render(raw: str) -> str:
    """Replay a captured byte stream into the screen it painted."""
    screen = Screen(rows=ROWS, cols=COLS)
    screen.feed(raw)
    out: list[str] = []
    for line, marker in zip(screen.lines(), screen.markers(SELECTION_BG, MATCH_FG)):
        if not line.strip():
            continue
        out.append(line)
        if marker.strip():
            out.append(marker)
    return "\n".join(out)


def main(argv: Sequence[str]) -> int:
    if "--capture" not in argv:
        run_interactive()
        return 0

    raw_frames = capture([keys for keys, _ in CAPTURE_STEPS], rows=ROWS, cols=COLS)
    for (_, label), raw in zip(CAPTURE_STEPS, raw_frames):
        print(f"\n── {label} " + "─" * max(0, 56 - len(label)))
        print(render(raw))
    print(
        "\n marker line: '~' current menu row, '^' fuzzy-matched character"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

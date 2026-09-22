#!/usr/bin/env python3
"""ctrl+o has to take the detail back when it is pressed again.

    .venv/bin/python docs/problem/repro/repro_ctrl_o_no_collapse.py

Witness for ``docs/problem/2026-09-21-ctrl-o-expansion-never-collapses.md``.

What it does: starts a fake model that answers the first prompt with a bash
call producing more output than one row can hold, boots the real interactive
CLI inside a pty, sends one prompt and then presses ctrl+o twice. The screen is
rebuilt from the byte stream, so "the detail is still there" is read off what a
person would see rather than off a mock. Expect ``BUG PRESENT: False``: the
first press shows the detail, the second takes it away.

Before the fix the first press printed the block into the scrollback and the
second printed a second copy of it, so the detail could never be closed and the
byte count for it grew with every press.

Why a pty: ``InteractiveCLIRunner`` only builds its prompt_toolkit session when
``sys.stdin.isatty()``, and the ctrl+o binding lives on that session. Piping the
same input takes the ``input()`` fallback, where ctrl+o is a stray byte and
nothing expands at all.

Caveat: the screen model is the repo's ``command_palette_demo.Screen``, which is
enough for "is this text on screen" but not for character positions -- a frame
prompt_toolkit paints over itself can come out with the first characters of a
few rows eaten, which reads like a bug and is not one (checking the same run
through ``pyte`` showed a clean screen). Trust the presence checks here; do not
trust the alignment of a single capture.
"""

from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import pathlib
import pty
import select
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import termios
import time

ROOT = pathlib.Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
TESTS = ROOT / "tests"
ROWS, COLS = 40, 100
TOKEN = "secret"
PROMPT = "USE_TOOL please"
#: More lines than the collapsed row shows, still few enough that the whole
#: expansion fits one screen, where it can be looked for afterwards.
OUTPUT_LINES = 12
COMMAND = f"printf 'output-line-%02d\\n' $(seq 1 {OUTPUT_LINES})"

sys.path.insert(0, str(TESTS))


def fake_model() -> object:
    """A fake model that calls ``bash`` with arguments this repro chose.

    The shipped fixture hardcodes ``{"text": "ping"}``, which only fits its own
    echo tool; the built-in tool needs its own argument names.
    """
    from fixtures.fake_llm_server import FakeLLMServer  # noqa: PLC0415

    class ScriptedBashModel(FakeLLMServer):
        def _tool_stream_events(self) -> list[dict[str, object]]:
            chunk = self._chunk
            return [
                chunk(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_repro_bash",
                                "type": "function",
                                "function": {"name": self.tool_name, "arguments": ""},
                            }
                        ],
                    },
                    None,
                ),
                chunk(
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "function": {
                                    "arguments": json.dumps({"commands": COMMAND})
                                },
                            }
                        ]
                    },
                    None,
                ),
                chunk({}, "tool_calls"),
            ]

    return ScriptedBashModel(tool_name="bash")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def cli_command() -> list[str]:
    bundled = ROOT / ".venv" / "bin" / "langharness"
    if bundled.exists():
        return [str(bundled)]
    return [sys.executable, "-m", "langharness_cli"]


def load_screen_type() -> type:
    path = ROOT / "examples" / "command_palette_demo.py"
    spec = importlib.util.spec_from_file_location("command_palette_demo", path)
    if spec is None or spec.loader is None:  # pragma: no cover - packaging bug
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Screen


class Pty:
    """The CLI on one end, a terminal emulator on the other.

    The emulator answers prompt_toolkit's cursor-position request (``CSI 6n``)
    the way a real terminal does. Without the reply prompt_toolkit gives up on
    placing its own layout -- it says as much on the terminal -- and the
    screen below would be an artefact of the harness rather than what a person
    would see.
    """

    def __init__(self, argv: list[str], env: dict[str, str], screen_type: type) -> None:
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
        self.process = subprocess.Popen(
            argv,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=str(ROOT),
            env={
                **env,
                "TERM": "xterm-256color",
                "COLUMNS": str(COLS),
                "LINES": str(ROWS),
            },
            start_new_session=True,
        )
        os.close(slave)
        self.raw = ""
        self.screen = screen_type(rows=ROWS, cols=COLS)

    def drain(self, seconds: float) -> str:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.master], [], [], 0.05)
            if not ready:
                continue
            try:
                chunk = os.read(self.master, 65536)
            except OSError:
                break
            if not chunk:
                break
            text = chunk.decode("utf-8", "replace")
            self.raw += text
            self.screen.feed(text)
            if "\x1b[6n" in text:
                os.write(
                    self.master,
                    f"\x1b[{self.screen.row + 1};{self.screen.col + 1}R".encode(),
                )
        return self.raw

    def drain_until(self, needle: str, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.drain(0.25)
            if needle in self.raw:
                return True
        return False

    def send(self, keys: str) -> None:
        os.write(self.master, keys.encode())

    def view(self) -> str:
        return "\n".join(line for line in self.screen.lines() if line.strip())

    def close(self) -> None:
        try:
            os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
        os.close(self.master)


def report(title: str, body: str) -> None:
    print(f"\n─── {title} " + "─" * max(0, 64 - len(title)))
    print(body)


def main() -> int:
    work = pathlib.Path(tempfile.mkdtemp(prefix="langharness-ctrlo-"))
    screen_type = load_screen_type()
    fake = fake_model()
    fake.start()
    cli: Pty | None = None
    try:
        (work / "langharness.toml").write_text(
            "\n".join(
                [
                    "[providers.fake]",
                    'protocol = "chat"',
                    f'base_url = "{fake.base_url}"',
                    'model = "fake-model"',
                    'api_key = "test-key"',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        env = {**os.environ, "LANG_HARNESS_DIR": str(work)}
        cli = Pty(
            [
                *cli_command(),
                "--dir",
                str(work),
                "--provider",
                "fake",
                "interactive",
                "--server-port",
                str(free_port()),
                "--token",
                TOKEN,
                "--user-id",
                "ctrlo_user",
                "--agent-id",
                "simple_agent",
                "--session-id",
                "ctrlo_session",
            ],
            env,
            screen_type,
        )
        cli.drain_until("langharness>", 25.0)

        cli.send(PROMPT + "\r")
        answered = cli.drain_until("final:", 40.0)
        cli.drain(1.5)
        report("after the response (collapsed row)", cli.view())

        cli.send("\x0f")  # ctrl+o
        cli.drain(2.0)
        after_first = cli.view()
        report("after the 1st ctrl+o", after_first)

        cli.send("\x0f")  # ctrl+o again
        cli.drain(2.0)
        after_second = cli.view()
        report("after the 2nd ctrl+o", after_second)

        header = "Tool calls from the last response:"
        rows_printed = cli.raw.count(f"… ({OUTPUT_LINES} lines)")
        # The header is what only the detail carries: the model echoes the
        # tool output back in its reply, so the output text alone would read
        # as "still on screen" long after the pane is gone.
        pane_after_first = header in after_first
        pane_after_second = header in after_second
        pane_drawn = cli.raw.count(header)

        print("\n=== evidence ===")
        print(f"model answered                   : {answered}")
        print(f"collapsed row shown              : {rows_printed}x")
        print(f"detail pane on screen after 1st  : {pane_after_first}")
        print(f"detail pane on screen after 2nd  : {pane_after_second}")
        # Informational only: a repaintable pane is expected to be drawn again
        # on every repaint, so the byte count says nothing about stacking.
        print(f"pane drawn (repaints included)   : {pane_drawn}x")
        print(f"bytes written to the terminal    : {len(cli.raw)}")

        print(f"BUG PRESENT                      : {pane_after_second}")
        if pane_after_second:
            print("  the detail is still on screen after the key that should close it")
        elif not pane_after_first:
            print("  the first press showed nothing: the key is not wired up")
        else:
            print("  the second press took the detail back")
        return 0
    finally:
        if cli is not None:
            cli.close()
        fake.stop()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

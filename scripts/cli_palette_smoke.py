#!/usr/bin/env python3
"""Smoke-test the interactive command palette against the real binary.

    python scripts/cli_palette_smoke.py

Why this drives a pty rather than a pipe
----------------------------------------
``InteractiveCLIRunner`` only builds its prompt_toolkit session when
``sys.stdin.isatty()`` is true. Piping commands in -- the way
``tests/manual_cli_smoke.sh`` does -- silently takes the ``input()`` fallback
instead and never reaches the palette, so it cannot catch a regression here.
This script allocates a pty, types at the prompt and reconstructs the screen
from the byte stream, which is what a person would see.

The run needs no network and no API key: the model provider points at a dead
port, and the script only types at the prompt without submitting a message.

Scope note: the same dead port means no message can be sent, so this script
covers the command palette and nothing about the transcript. Tool call rows
and ctrl+o need a model to emit a tool_call in the first place; they are
covered by tests and were last verified end to end against a live provider by
hand.
"""

from __future__ import annotations

import fcntl
import importlib.util
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

ROOT = pathlib.Path(__file__).resolve().parents[1]
ROWS, COLS = 22, 96

#: Reserved name rule: `default` is a valid [providers.default] section but
#: must never show up in the palette, so `demo`/`staging` are its witnesses.
CONFIG = """[DEFAULT]

[providers.default]
protocol = "chat"
base_url = "http://127.0.0.1:9/v1"
model = "smoke-model"
api_key = "not-used"

[providers.demo]
protocol = "chat"
base_url = "http://127.0.0.1:9/v1"
model = "demo-model"
api_key = "not-used"

[providers.staging]
protocol = "anthropic"
base_url = "http://127.0.0.1:9/v1"
model = "staging-model"
api_key = "not-used"
"""

#: (keys to type, must appear in the menu, must not appear in the menu).
STEPS: list[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = [
    (
        "/",
        "a bare slash lists every command",
        ("/help", "/exit", "/model"),
        (),
    ),
    (
        "s",
        "a prefix narrows the list",
        ("/scope", "/sessions"),
        (),
    ),
    (
        "s",
        "'/ss' matches a subsequence rather than a prefix",
        ("/sessions",),
        ("/agents", "/scope"),
    ),
    (
        "\x15/model ",
        "/model offers the configured providers",
        ("demo", "staging"),
        ("default",),
    ),
    (
        "\x15/plugins d",
        "/plugins offers its own actions",
        ("discover", "disable"),
        (),
    ),
    (
        "\x15/plugins runtime s",
        "/plugins completes a nested slot",
        ("set",),
        (),
    ),
    (
        "et ",
        "and the slot after it is a scope",
        ("root", "server"),
        (),
    ),
]


def load_screen_type() -> type:
    """Reuse the demo's terminal emulator instead of keeping a third copy."""
    path = ROOT / "examples" / "command_palette_demo.py"
    spec = importlib.util.spec_from_file_location("command_palette_demo", path)
    if spec is None or spec.loader is None:  # pragma: no cover - packaging bug
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Screen


def resolve_cli() -> list[str]:
    override = os.environ.get("LANGHARNESS_BIN")
    if override:
        return [override]
    bundled = ROOT / ".venv" / "bin" / "langharness"
    if bundled.exists():
        return [str(bundled)]
    found = shutil.which("langharness")
    if found:
        return [found]
    return [sys.executable, "-m", "langharness"]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def drain(master: int, sink: list[str], seconds: float) -> None:
    """Collect pty output until it goes quiet for ``seconds``."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        ready, _, _ = select.select([master], [], [], 0.05)
        if not ready:
            continue
        try:
            chunk = os.read(master, 65536)
        except OSError:
            return
        if not chunk:
            return
        sink.append(chunk.decode("utf-8", "replace"))


def render(screen_type: type, raw: str) -> str:
    screen = screen_type(rows=ROWS, cols=COLS)
    screen.feed(raw)
    return "\n".join(line for line in screen.lines() if line.strip())


def menu_of(rendered: str) -> str:
    """Everything below the prompt line, where the completion menu sits."""
    lines = rendered.splitlines()
    for index, line in enumerate(lines):
        if "langharness>" in line:
            return "\n".join(lines[index + 1 :])
    return ""


def main() -> int:
    work = tempfile.mkdtemp(prefix="langharness-palette-")
    (pathlib.Path(work) / "langharness.toml").write_text(CONFIG)
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
    process = subprocess.Popen(
        [
            *resolve_cli(),
            "--dir", work,
            "interactive",
            "--server-port", str(free_port()),
            "--token", "secret",
        ],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd=str(ROOT),
        env={
            **os.environ,
            "LANG_HARNESS_DIR": work,
            "TERM": "xterm-256color",
            "COLUMNS": str(COLS),
            "LINES": str(ROWS),
        },
        start_new_session=True,
    )
    os.close(slave)
    sink: list[str] = []
    failures: list[str] = []
    try:
        drain(master, sink, 15.0)  # server boot, then banner and prompt
        rendered = render(load_screen_type(), "".join(sink))
        print("\n─── startup " + "─" * 60)
        print(rendered)
        # The welcome screen and the prompt both have to have survived the
        # real bootstrap, not just been constructed in someone's unit test.
        for needle in ("langharness>", "Type a message"):
            if needle not in rendered:
                failures.append(f"startup: screen is missing {needle!r}")

        for keys, label, wanted, unwanted in STEPS:
            os.write(master, keys.encode())
            drain(master, sink, 1.5)
            rendered = render(load_screen_type(), "".join(sink))
            menu = menu_of(rendered)
            print(f"\n─── {label} " + "─" * max(0, 60 - len(label)))
            print(rendered)
            for needle in wanted:
                if needle not in menu:
                    failures.append(f"{label}: menu is missing {needle!r}")
            for needle in unwanted:
                if needle in menu:
                    failures.append(f"{label}: menu should not offer {needle!r}")

        os.write(master, b"\x15")
        drain(master, sink, 1.0)
        os.write(master, b"/exit")
        drain(master, sink, 1.0)
        os.write(master, b"\r")
        drain(master, sink, 5.0)
        code = process.wait(timeout=15)
        if code != 0:
            failures.append(f"the shell exited with {code}")
    except subprocess.TimeoutExpired:
        failures.append("the shell did not exit after /exit")
    finally:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        os.close(master)
        shutil.rmtree(work, ignore_errors=True)

    print()
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print("OK: palette behaved as expected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

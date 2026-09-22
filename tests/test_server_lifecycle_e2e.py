"""End-to-end tests for the multi-client server lifecycle."""
# mypy: ignore-errors

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parent.parent

#: Grace for the auto-shutdown tests. It has to dwarf everything a test does
#: before its first client lands -- server boot, the 0.1s health poll, two
#: websocket handshakes -- because the registry arms the timer at lifespan
#: startup, before uvicorn accepts anything. See
#: docs/problem/2026-09-21-auto-shutdown-grace-race.md: at 0.3s the window was
#: spent before the test could use it, and the attach then failed with an HTTP
#: 500 from uvicorn's shutdown drain, or a refused connection. The lifecycle
#: semantics have nothing to do with sub-second timing, so nothing is gained
#: by running the window this close to the edge.
GRACE = 2.0

#: How long to wait to watch a grace window go by. It has to outlast GRACE, or
#: an assertion about the server still being up checks nothing at all.
GRACE_ELAPSED = GRACE + 0.5


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def make_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    paths = [str(ROOT / "src"), *env.get("PYTHONPATH", "").split(os.pathsep)]
    env["PYTHONPATH"] = os.pathsep.join(path for path in paths if path)
    env["LANG_HARNESS_DIR"] = str(tmp_path)
    return env


def wait_for_health(base_url: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{base_url}/health", timeout=1.0).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise AssertionError("API server did not become ready")


def wait_for_exit(process: subprocess.Popen, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return True
        time.sleep(0.05)
    return False


def spawn_server(
    tmp_path: Path, port: int, *, auto_shutdown: bool, grace: float
) -> subprocess.Popen:
    command = [
        sys.executable,
        "-m",
        "langharness",
        "--mode",
        "server",
        "--server-ip",
        "127.0.0.1",
        "--server-port",
        str(port),
        "--config-dir",
        str(tmp_path),
    ]
    if auto_shutdown:
        command += ["--auto-shutdown", "--auto-shutdown-grace", str(grace)]
    return subprocess.Popen(
        command,
        cwd=ROOT,
        env=make_env(tmp_path),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )


def attach_client(port: int):
    return connect(
        f"ws://127.0.0.1:{port}/clients/attach",
        additional_headers={"Authorization": "Bearer secret"},
    )


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=10)


def test_server_exits_after_last_client_leaves(tmp_path: Path) -> None:
    port = free_port()
    process = spawn_server(tmp_path, port, auto_shutdown=True, grace=GRACE)
    try:
        wait_for_health(f"http://127.0.0.1:{port}")
        first = attach_client(port)
        second = attach_client(port)

        first.close()
        time.sleep(GRACE_ELAPSED)  # grace elapsed; one client still attached
        assert process.poll() is None

        second.close()
        assert wait_for_exit(process)
    finally:
        stop_process(process)


def test_new_client_during_grace_cancels_shutdown(tmp_path: Path) -> None:
    port = free_port()
    process = spawn_server(tmp_path, port, auto_shutdown=True, grace=GRACE)
    try:
        wait_for_health(f"http://127.0.0.1:{port}")
        first = attach_client(port)
        first.close()

        time.sleep(0.1)  # still within the grace window
        second = attach_client(port)
        time.sleep(GRACE_ELAPSED)  # grace would have elapsed
        assert process.poll() is None

        second.close()
        assert wait_for_exit(process)
    finally:
        stop_process(process)


def test_server_without_auto_shutdown_stays_up(tmp_path: Path) -> None:
    port = free_port()
    process = spawn_server(tmp_path, port, auto_shutdown=False, grace=0.0)
    try:
        wait_for_health(f"http://127.0.0.1:{port}")
        client = attach_client(port)
        client.close()
        time.sleep(0.8)
        assert process.poll() is None
    finally:
        stop_process(process)

"""Detects and auto-starts the API server, then holds a client lifeline."""

from __future__ import annotations

import atexit
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import FrameType
from typing import Any
from urllib.parse import urlparse

import httpx
from websockets.exceptions import ConnectionClosedOK
from websockets.sync.client import connect

LOGGER = logging.getLogger("langharness.api_guard")


class APIGuard:
    """Starts the API server on demand and keeps it alive while attached.

    Each CLI process opens one websocket "lifeline" to the server after
    ensuring it runs; the server shuts itself down when the last lifeline
    closes. The guard never terminates a server it started.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11534",
        *,
        config_dir: str | None = None,
        startup_timeout: float = 30.0,
        token: str = "secret",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.config_dir = config_dir
        self.startup_timeout = startup_timeout
        self.token = token
        self._process: subprocess.Popen[bytes] | None = None
        self._ws: Any = None
        self._hooks_installed = False

    def is_running(self) -> bool:
        try:
            response = httpx.get(f"{self.base_url}/health", timeout=1.0)
            return response.status_code < 500
        except httpx.HTTPError:
            return False

    def ensure_api_server(self) -> None:
        if self.is_running():
            return
        lock = self._acquire_startup_lock()
        try:
            if self.is_running():
                return
            parsed = urlparse(self.base_url)
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or 11534

            command = [sys.executable]
            if not getattr(sys, "frozen", False):
                command += ["-m", "langharness"]
            command += [
                "--mode",
                "server",
                "--server-ip",
                host,
                "--server-port",
                str(port),
                "--auto-shutdown",
            ]
            if self.config_dir is not None:
                command += ["--config-dir", self.config_dir]
            self._process = subprocess.Popen(command)

            deadline = time.monotonic() + self.startup_timeout
            while time.monotonic() < deadline:
                if self.is_running():
                    return
                time.sleep(0.1)

            self._process.terminate()
            self._process = None
            raise RuntimeError("API server did not become ready in time")
        finally:
            self._release_startup_lock(lock)

    def attach_client(self) -> None:
        """Open the client lifeline websocket; failures only warn."""
        if self._ws is not None:
            return
        parsed = urlparse(self.base_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 11534
        ws_url = f"ws://{host}:{port}/clients/attach"
        try:
            self._ws = connect(
                ws_url,
                additional_headers={
                    "Authorization": f"Bearer {self.token}"
                },
                open_timeout=5.0,
            )
        except Exception as exc:
            LOGGER.warning("client lifeline failed to attach: %s", exc)
            return
        threading.Thread(
            target=self._drain_ws, name="langharness-lifeline", daemon=True
        ).start()
        self._install_exit_hooks()

    def detach_client(self) -> None:
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    def _drain_ws(self) -> None:
        try:
            for _message in self._ws:
                pass
        except ConnectionClosedOK:
            LOGGER.debug("client lifeline closed")
        except Exception as exc:
            LOGGER.warning("client lifeline lost: %s", exc)

    def _install_exit_hooks(self) -> None:
        if self._hooks_installed:
            return
        self._hooks_installed = True
        atexit.register(self.detach_client)
        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(signum, self._signal_handler)
            except (ValueError, OSError):
                pass  # not the main thread

    def _signal_handler(self, signum: int, _frame: FrameType | None) -> None:
        self.detach_client()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    def _acquire_startup_lock(self) -> Path | None:
        if self.config_dir is None:
            return None
        directory = Path(self.config_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "api_server.lock"
        deadline = time.monotonic() + self.startup_timeout
        while True:
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if self._lock_is_stale(path):
                    path.unlink(missing_ok=True)
                    continue
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "another client is starting the API server"
                    )
                time.sleep(0.1)
                continue
            with os.fdopen(fd, "w") as handle:
                handle.write(str(os.getpid()))
            return path

    def _release_startup_lock(self, path: Path | None) -> None:
        if path is not None:
            path.unlink(missing_ok=True)

    @staticmethod
    def _lock_is_stale(path: Path) -> bool:
        try:
            pid = int(path.read_text().strip())
        except (OSError, ValueError):
            return True
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        return False

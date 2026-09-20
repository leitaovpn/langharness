"""Client connection registry that drives server auto-shutdown."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any


class ClientRegistry:
    """Counts attached client websockets and schedules shutdown at zero.

    A shutdown is scheduled only when all three conditions hold: the server
    is ready (``mark_ready`` ran), auto-shutdown is enabled, and a shutdown
    callback was provided. After the last client detaches, the callback runs
    once the grace window elapses with no new client attaching.
    """

    def __init__(self, grace: float = 10.0) -> None:
        self._grace = grace
        self._clients: set[Any] = set()
        self._ready = False
        self._enabled = False
        self._shutdown_callback: Callable[[], None] | None = None
        self._timer: asyncio.Task[None] | None = None

    def set_shutdown_callback(self, callback: Callable[[], None]) -> None:
        self._shutdown_callback = callback

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def mark_ready(self) -> None:
        self._ready = True
        if not self._clients:
            self._schedule_shutdown()

    def attach(self, client: Any) -> None:
        self._clients.add(client)
        self._cancel_timer()

    def detach(self, client: Any) -> None:
        self._clients.discard(client)
        if not self._clients:
            self._schedule_shutdown()

    def active_count(self) -> int:
        return len(self._clients)

    def _cancel_timer(self) -> None:
        if self._timer is not None and not self._timer.done():
            self._timer.cancel()
        self._timer = None

    def _schedule_shutdown(self) -> None:
        if not self._ready or not self._enabled or self._shutdown_callback is None:
            return
        if self._timer is not None and not self._timer.done():
            return
        self._timer = asyncio.create_task(self._run_timer())

    async def _run_timer(self) -> None:
        await asyncio.sleep(self._grace)
        if not self._clients and self._ready and self._enabled:
            assert self._shutdown_callback is not None
            self._shutdown_callback()

"""Fair, bounded read/write admission for one PTC program."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class PTCDispatchGate:
    """Admit reads concurrently and writes alone in submission order."""

    def __init__(self, *, max_readers: int = 4) -> None:
        self._condition = asyncio.Condition()
        self._waiting: deque[tuple[object, bool]] = deque()
        self._readers = 0
        self._writer = False
        self._max_readers = max_readers

    @asynccontextmanager
    async def acquire(self, *, read_only: bool) -> AsyncIterator[None]:
        ticket = object()
        entry = (ticket, read_only)
        async with self._condition:
            self._waiting.append(entry)
            try:
                while not self._can_enter(ticket, read_only):
                    await self._condition.wait()
                self._waiting.remove(entry)
                if read_only:
                    self._readers += 1
                else:
                    self._writer = True
                self._condition.notify_all()
            except BaseException:
                if entry in self._waiting:
                    self._waiting.remove(entry)
                    self._condition.notify_all()
                raise
        try:
            yield
        finally:
            async with self._condition:
                if read_only:
                    self._readers -= 1
                else:
                    self._writer = False
                self._condition.notify_all()

    def _can_enter(self, ticket: object, read_only: bool) -> bool:
        if self._writer:
            return False
        if not read_only:
            return self._readers == 0 and self._waiting[0][0] is ticket
        if self._readers >= self._max_readers:
            return False
        for waiting_ticket, waiting_read_only in self._waiting:
            if waiting_ticket is ticket:
                return True
            if not waiting_read_only:
                return False
        return False

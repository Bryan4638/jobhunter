from __future__ import annotations

import asyncio


class RateLimiter:
    """Espacia el inicio de las llamadas para no superar ``per_minute``."""

    def __init__(self, per_minute: int) -> None:
        self._interval = 60.0 / per_minute
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            delay = self._next - now
            if delay > 0:
                await asyncio.sleep(delay)
            self._next = max(now, self._next) + self._interval

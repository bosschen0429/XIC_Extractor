"""Keep browser tabs alive across reloads and retire abandoned desktop sessions."""

from __future__ import annotations

import time
from collections.abc import Callable
from threading import Lock


class BrowserLifetime:
    def __init__(
        self,
        *,
        grace: float = 20,
        stale_after: float = 180,
        startup_timeout: float | None = 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = clock
        self._grace = grace
        self._stale_after = stale_after
        self._startup_timeout = startup_timeout
        self._started_at = clock()
        self._windows: dict[str, float] = {}
        self._connected = False
        self._empty_since: float | None = None
        self._lock = Lock()

    def update(self, window: str, *, closed: bool = False) -> None:
        with self._lock:
            now = self._clock()
            if closed:
                self._windows.pop(window, None)
                if not self._windows and self._empty_since is None:
                    self._empty_since = now
            else:
                self._windows[window] = now
                self._connected = True
                self._empty_since = None

    def should_exit(self) -> bool:
        with self._lock:
            now = self._clock()
            if not self._connected:
                return (
                    self._startup_timeout is not None
                    and now - self._started_at >= self._startup_timeout
                )
            self._windows = {
                window: seen
                for window, seen in self._windows.items()
                if now - seen < self._stale_after
            }
            if self._windows:
                return False
            if self._empty_since is None:
                # After sleep/crash, allow a resumed tab to reconnect first.
                self._empty_since = now
            return now - self._empty_since >= self._grace

"""Per-tenant, per-tool sliding window."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable


class RateLimiter:
    def __init__(
        self,
        limit: int,
        window_seconds: float,
        clock: Callable[[], float] | None = None,
    ):
        if limit < 1:
            raise ValueError("rate limit must be at least 1")
        if window_seconds <= 0:
            raise ValueError("rate window must be positive")
        self.limit = limit
        self.window = window_seconds
        self._clock = clock or time.monotonic
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, tenant_id: str, tool: str) -> float | None:
        """Record a hit. Return retry-after seconds when the call is over the limit."""
        now = self._clock()
        key = (tenant_id, tool)
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(0.0, self.window - (now - hits[0]))
            hits.append(now)
            return None

from __future__ import annotations

import asyncio
import hashlib
from collections import defaultdict, deque
from time import monotonic


class RateLimitExceeded(RuntimeError):
    def __init__(self, retry_after: int) -> None:
        super().__init__("request rate limit exceeded")
        self.retry_after = retry_after


class SlidingWindowRateLimiter:
    """Per-instance abuse guard; Firebase UID/App Check quotas remain authoritative."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._events: dict[str, deque[float]] = defaultdict(deque)

    async def check(self, key: str, *, limit: int, window_seconds: int = 60) -> None:
        now = monotonic()
        async with self._lock:
            events = self._events[key]
            cutoff = now - window_seconds
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                retry_after = max(1, int(window_seconds - (now - events[0])))
                raise RateLimitExceeded(retry_after)
            events.append(now)


def request_identity(*values: str | None) -> str:
    joined = "|".join(value or "missing" for value in values)
    return hashlib.sha256(joined.encode()).hexdigest()

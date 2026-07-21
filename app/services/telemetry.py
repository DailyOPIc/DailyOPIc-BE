from __future__ import annotations

import hashlib
import json
import logging
from time import perf_counter
from typing import Any


logger = logging.getLogger("dailyopic.telemetry")


def stable_hash(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def emit(event: str, **fields: Any) -> None:
    safe = {
        key: value
        for key, value in fields.items()
        if value is not None
        and key not in {"transcript", "audio", "prompt", "correctedAnswer", "topic"}
    }
    logger.info(json.dumps({"event": event, **safe}, default=str, sort_keys=True))


class RequestTimer:
    def __init__(self) -> None:
        self._started = perf_counter()

    @property
    def latency_ms(self) -> int:
        return int((perf_counter() - self._started) * 1_000)

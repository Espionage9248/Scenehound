"""Per-indexer token bucket gating all tracker-bound search queries."""
from __future__ import annotations

import time
from typing import Callable


class TokenBucket:
    def __init__(
        self,
        burst: int,
        refill_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._refill_seconds = refill_seconds
        self._clock = clock
        self._updated = clock()

    def try_acquire(self) -> bool:
        now = self._clock()
        elapsed = now - self._updated
        self._updated = now
        self._tokens = min(self._capacity, self._tokens + elapsed / self._refill_seconds)
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False

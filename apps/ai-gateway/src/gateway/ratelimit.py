"""Per-tenant token bucket rate limiter (in-process).

Single replica only: with more replicas each pod enforces its own bucket.
A shared store (Redis) is on the roadmap.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock


@dataclass
class _Bucket:
    tokens: float
    updated: float


class TokenBucketLimiter:
    def __init__(self, rate_per_s: float, burst: int, clock=time.monotonic) -> None:
        self.rate = rate_per_s
        self.burst = float(burst)
        self._clock = clock
        self._buckets: dict[str, _Bucket] = {}
        self._lock = Lock()

    def acquire(self, key: str) -> tuple[bool, float]:
        """Take one token. Returns (allowed, retry_after_seconds)."""
        now = self._clock()
        with self._lock:
            b = self._buckets.get(key)
            if b is None:
                b = self._buckets[key] = _Bucket(self.burst, now)
            b.tokens = min(self.burst, b.tokens + (now - b.updated) * self.rate)
            b.updated = now
            if b.tokens >= 1.0:
                b.tokens -= 1.0
                return True, 0.0
            return False, (1.0 - b.tokens) / self.rate if self.rate > 0 else 60.0

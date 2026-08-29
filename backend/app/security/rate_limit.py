"""Per-client rate limiting — token bucket, no external state.

Public API and unauthenticated paths get a strict bucket; authenticated
sessions a generous one. 429 with Retry-After on exhaustion. Buckets are
in-memory per process (single-node deployment); enough to stop request
storms and brute-force probing of /api/auth/handshake.
"""
import time
from collections import defaultdict

DEFAULTS = {
    "capacity": 60,        # burst size
    "refill_per_s": 6.0,   # sustained rate
}
AUTH_PATHS = ("/api/auth/handshake",)


class TokenBucket:
    __slots__ = ("tokens", "last", "capacity", "refill")

    def __init__(self, capacity, refill, now=None):
        self.capacity = float(capacity)
        self.refill = float(refill)
        self.tokens = float(capacity)
        self.last = now if now is not None else time.monotonic()

    def take(self, now=None, amount=1.0):
        """Try to consume `amount` tokens. `now` is injectable for tests;
        a backwards/jumping clock never yields extra tokens (elapsed clamped >= 0)."""
        now = time.monotonic() if now is None else now
        elapsed = max(0.0, now - self.last)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill)
        self.last = now
        if self.tokens >= amount:
            self.tokens -= amount
            return True
        return False

    def retry_after_s(self, now=None):
        now = time.monotonic() if now is None else now
        need = 1.0 - self.tokens
        return max(0.0, need / self.refill if self.refill else 0.0)


class RateLimiter:
    def __init__(self, settings=None):
        cfg = (settings or {}).get("security", {}).get("rate_limit", {})
        self.capacity = int(cfg.get("capacity", DEFAULTS["capacity"]))
        self.refill = float(cfg.get("refill_per_s", DEFAULTS["refill_per_s"]))
        self.auth_capacity = int(cfg.get("auth_capacity", 10))
        self.auth_refill = float(cfg.get("auth_refill_per_s", 0.2))
        self.buckets: dict[tuple, TokenBucket] = defaultdict(
            lambda: TokenBucket(self.capacity, self.refill))
        self.auth_buckets: dict[str, TokenBucket] = defaultdict(
            lambda: TokenBucket(self.auth_capacity, self.auth_refill))

    def check(self, client: str, path: str, authenticated: bool = False,
              now=None) -> tuple[bool, float]:
        """-> (allowed, retry_after_s). Never blocks localhost health checks? No:
        health is rate-limited too — DoS protection beats polling convenience."""
        if path in AUTH_PATHS:
            b = self.auth_buckets[client]
            if not b.take(now):
                return False, b.retry_after_s(now)
            return True, 0.0
        key = (client, "auth" if authenticated else "anon")
        b = self.buckets[key]
        if not b.take(now):
            return False, b.retry_after_s(now)
        return True, 0.0

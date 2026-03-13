from __future__ import annotations

import time
from threading import Lock

from services.cache_backend import CacheBackend

_LOCK = Lock()


class RateLimiter:
    """
    Simple fixed-window rate limiter with Redis/in-memory backend.
    """

    def __init__(self, namespace: str = "ratelimit"):
        self.cache = CacheBackend(namespace=namespace, default_ttl=60)

    def _bucket_key(self, key: str, window_sec: int) -> tuple[str, int]:
        safe_window = max(1, int(window_sec or 60))
        now = int(time.time())
        bucket = now // safe_window
        reset_at = (bucket + 1) * safe_window
        return f"{key}|{safe_window}|{bucket}", reset_at

    def hit(self, key: str, limit: int, window_sec: int = 60) -> dict:
        safe_limit = max(1, int(limit or 1))
        bucket_key, reset_at = self._bucket_key(str(key or "anon"), window_sec)
        with _LOCK:
            row = self.cache.get(bucket_key) or {}
            count = int(row.get("count", 0) or 0) + 1
            self.cache.set(bucket_key, {"count": count}, ttl=max(1, int(reset_at - time.time())))
        allowed = count <= safe_limit
        remaining = max(0, safe_limit - count)
        return {
            "allowed": allowed,
            "limit": safe_limit,
            "count": count,
            "remaining": remaining,
            "reset_at": int(reset_at),
            "retry_after": max(1, int(reset_at - time.time())),
        }

    def clear(self, prefix: str = "") -> None:
        self.cache.clear_prefix(prefix)

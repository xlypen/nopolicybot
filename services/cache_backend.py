from __future__ import annotations

import json
import os
import time
from threading import Lock

_MEMORY_STORE: dict[str, tuple[float, str]] = {}
_MEMORY_LOCK = Lock()


class CacheBackend:
    """
    Simple cache adapter with optional Redis backend and in-memory fallback.
    """

    def __init__(self, namespace: str = "default", default_ttl: int = 60):
        self.namespace = str(namespace or "default").strip()
        self.default_ttl = max(1, int(default_ttl or 60))
        self.redis = None
        redis_url = (os.getenv("REDIS_URL") or "").strip()
        if redis_url:
            try:
                import redis

                self.redis = redis.Redis.from_url(redis_url, decode_responses=True)
                # Probe connectivity once.
                self.redis.ping()
            except Exception:
                self.redis = None

    def _k(self, key: str) -> str:
        return f"{self.namespace}:{key}"

    def get(self, key: str):
        full_key = self._k(key)
        if self.redis is not None:
            try:
                raw = self.redis.get(full_key)
                if raw is None:
                    return None
                return json.loads(raw)
            except Exception:
                return None
        with _MEMORY_LOCK:
            item = _MEMORY_STORE.get(full_key)
            if not item:
                return None
            expires_at, raw = item
            if expires_at <= time.time():
                _MEMORY_STORE.pop(full_key, None)
                return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    def set(self, key: str, value, ttl: int | None = None) -> None:
        full_key = self._k(key)
        safe_ttl = max(1, int(ttl or self.default_ttl))
        raw = json.dumps(value, ensure_ascii=False)
        if self.redis is not None:
            try:
                self.redis.setex(full_key, safe_ttl, raw)
                return
            except Exception:
                pass
        with _MEMORY_LOCK:
            _MEMORY_STORE[full_key] = (time.time() + safe_ttl, raw)

    def delete(self, key: str) -> None:
        full_key = self._k(key)
        if self.redis is not None:
            try:
                self.redis.delete(full_key)
            except Exception:
                pass
        with _MEMORY_LOCK:
            _MEMORY_STORE.pop(full_key, None)

    def clear_prefix(self, prefix: str = "") -> None:
        pref = self._k(prefix)
        if self.redis is not None:
            try:
                keys = list(self.redis.scan_iter(match=f"{pref}*"))
                if keys:
                    self.redis.delete(*keys)
            except Exception:
                pass
        with _MEMORY_LOCK:
            keys = [k for k in _MEMORY_STORE if k.startswith(pref)]
            for key in keys:
                _MEMORY_STORE.pop(key, None)

from __future__ import annotations

import time

from services.cache_backend import CacheBackend


def test_cache_backend_memory_set_get_delete():
    cache = CacheBackend(namespace="test_cache", default_ttl=10)
    cache.set("k1", {"ok": True}, ttl=10)
    assert cache.get("k1") == {"ok": True}
    cache.delete("k1")
    assert cache.get("k1") is None


def test_cache_backend_memory_expiry():
    cache = CacheBackend(namespace="test_cache_expire", default_ttl=1)
    cache.set("k1", {"v": 1}, ttl=1)
    assert cache.get("k1") == {"v": 1}
    time.sleep(1.1)
    assert cache.get("k1") is None


def test_cache_backend_clear_prefix():
    cache = CacheBackend(namespace="test_cache_prefix", default_ttl=60)
    cache.set("a:1", {"v": 1})
    cache.set("a:2", {"v": 2})
    cache.set("b:1", {"v": 3})
    cache.clear_prefix("a:")
    assert cache.get("a:1") is None
    assert cache.get("a:2") is None
    assert cache.get("b:1") == {"v": 3}

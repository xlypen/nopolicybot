from __future__ import annotations

import time

from services.rate_limiter import RateLimiter


def test_rate_limiter_allows_within_limit():
    limiter = RateLimiter(namespace="test_rl_within")
    limiter.clear()
    r1 = limiter.hit("ip:1.1.1.1", limit=2, window_sec=30)
    r2 = limiter.hit("ip:1.1.1.1", limit=2, window_sec=30)
    assert r1["allowed"] is True
    assert r2["allowed"] is True
    assert int(r2["remaining"]) == 0


def test_rate_limiter_blocks_after_limit():
    limiter = RateLimiter(namespace="test_rl_block")
    limiter.clear()
    limiter.hit("ip:2.2.2.2", limit=1, window_sec=30)
    blocked = limiter.hit("ip:2.2.2.2", limit=1, window_sec=30)
    assert blocked["allowed"] is False
    assert int(blocked["retry_after"]) >= 1


def test_rate_limiter_resets_after_window():
    limiter = RateLimiter(namespace="test_rl_reset")
    limiter.clear()
    limiter.hit("ip:3.3.3.3", limit=1, window_sec=1)
    blocked = limiter.hit("ip:3.3.3.3", limit=1, window_sec=1)
    assert blocked["allowed"] is False
    time.sleep(1.05)
    fresh = limiter.hit("ip:3.3.3.3", limit=1, window_sec=1)
    assert fresh["allowed"] is True

"""Rate limiting.

Two distinct uses:
  * `ApiRateLimiter`   protects sensitive endpoints (login, exports).
  * `PluginRateLimiter` makes plugins honour their declared quotas so we do not
    overload the services being queried. This is not a circumvention mechanism:
    it deliberately slows our own requests down.

Implementation: an approximate sliding window backed by an expiring Redis
counter, falling back to memory when Redis is unavailable (dev and tests).
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

import redis

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class _MemoryBackend:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, window: int) -> int:
        now = time.time()
        with self._lock:
            bucket = self._hits[key]
            while bucket and now - bucket[0] > window:
                bucket.popleft()
            bucket.append(now)
            return len(bucket)


class _RedisBackend:
    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    def hit(self, key: str, window: int) -> int:
        pipe = self._client.pipeline()
        pipe.incr(key, 1)
        pipe.expire(key, window)
        count, _ = pipe.execute()
        return int(count)


def _make_backend():
    try:
        client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=1)
        client.ping()
        return _RedisBackend(client)
    except Exception as exc:  # pragma: no cover - infrastructure dependent
        logger.warning("Redis unavailable for rate limiting (%s), using memory", exc)
        return _MemoryBackend()


_backend = None


def _get_backend():
    global _backend
    if _backend is None:
        _backend = _make_backend()
    return _backend


def reset_backend(in_memory: bool = True) -> None:
    """Reset the counters. Used by the test suite."""
    global _backend
    _backend = _MemoryBackend() if in_memory else None


class ApiRateLimiter:
    """Allow `limit` calls per `window` seconds per identity."""

    def __init__(self, limit: int, window: int, namespace: str) -> None:
        self.limit = limit
        self.window = window
        self.namespace = namespace

    def allow(self, identity: str) -> bool:
        key = f"rl:{self.namespace}:{identity}:{int(time.time() // self.window)}"
        return _get_backend().hit(key, self.window) <= self.limit

    def retry_after(self) -> int:
        return self.window


class PluginRateLimiter:
    """Block (sleep) until the plugin quota allows the next request."""

    def __init__(self, plugin: str, requests_per_minute: int) -> None:
        self.plugin = plugin
        self.rpm = max(1, requests_per_minute)

    def acquire(self, cost: int = 1) -> None:
        backend = _get_backend()
        for _ in range(cost):
            while True:
                bucket = int(time.time() // 60)
                key = f"rl:plugin:{self.plugin}:{bucket}"
                if backend.hit(key, 60) <= self.rpm:
                    break
                # Wait for the next window instead of pushing the service harder.
                time.sleep(min(60 - (time.time() % 60), 5))


# Pre-configured limiters for sensitive endpoints.
login_limiter = ApiRateLimiter(limit=10, window=300, namespace="login")
search_limiter = ApiRateLimiter(limit=60, window=60, namespace="search")
export_limiter = ApiRateLimiter(limit=20, window=300, namespace="export")

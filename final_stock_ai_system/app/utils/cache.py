"""Cache in-memory có TTL — giảm số lần gọi API ngoài (vnstock, Google News,
Gemini) để tránh rate limit và tăng tốc load trang. Không dùng Redis — cache
nằm trong RAM tiến trình Python, mất khi restart server (đủ dùng cho app cá
nhân chạy local)."""
from __future__ import annotations
import time
import threading
import hashlib
import json
import functools

from app.config import CACHE_TTL_PRICE, CACHE_TTL_NEWS, CACHE_TTL_SENTIMENT, CACHE_TTL_GEMINI


class TTLCache:
    def __init__(self, default_ttl: int = 60, max_entries: int = 500):
        self._store: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()
        self.default_ttl = default_ttl
        self.max_entries = max_entries
        self.hits = 0
        self.misses = 0

    def get(self, key: str):
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                return None
            expire_at, value = entry
            if time.time() >= expire_at:
                del self._store[key]
                self.misses += 1
                return None
            self.hits += 1
            return value

    def set(self, key: str, value, ttl: int | None = None):
        with self._lock:
            if len(self._store) >= self.max_entries:
                oldest_key = min(self._store, key=lambda k: self._store[k][0], default=None)
                if oldest_key is not None:
                    del self._store[oldest_key]
            self._store[key] = (time.time() + (ttl or self.default_ttl), value)

    def clear(self):
        with self._lock:
            self._store.clear()
            self.hits = 0
            self.misses = 0

    def stats(self) -> dict:
        with self._lock:
            total = self.hits + self.misses
            return dict(
                entries=len(self._store), hits=self.hits, misses=self.misses,
                hit_rate=round(self.hits / total, 3) if total else 0.0,
                ttl_seconds=self.default_ttl,
            )


price_cache = TTLCache(default_ttl=CACHE_TTL_PRICE)
news_cache = TTLCache(default_ttl=CACHE_TTL_NEWS)
sentiment_cache = TTLCache(default_ttl=CACHE_TTL_SENTIMENT)
gemini_cache = TTLCache(default_ttl=CACHE_TTL_GEMINI)

ALL_CACHES = {"price": price_cache, "news": news_cache, "sentiment": sentiment_cache, "gemini": gemini_cache}


def _make_key(prefix: str, args, kwargs) -> str:
    raw = prefix + "|" + json.dumps([args, kwargs], ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def cached(cache: TTLCache, prefix: str, ttl: int | None = None):
    """Decorator cache kết quả hàm theo (tên + tham số)."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = _make_key(prefix, args, kwargs)
            hit = cache.get(key)
            if hit is not None:
                return hit
            result = fn(*args, **kwargs)
            cache.set(key, result, ttl)
            return result
        return wrapper
    return decorator


def cache_stats_all() -> dict:
    return {name: c.stats() for name, c in ALL_CACHES.items()}


def clear_all_caches():
    for c in ALL_CACHES.values():
        c.clear()
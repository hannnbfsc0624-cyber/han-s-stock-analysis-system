"""Cache TTL đơn giản trong bộ nhớ — tránh tính lại phân tích mỗi lần bấm,
giúp web không lag (screener 8 mã từ vài giây xuống vài ms ở lần gọi sau)."""
from __future__ import annotations
import time
import threading
from functools import wraps

_lock = threading.Lock()
_store: dict = {}


def ttl_cache(seconds: int = 60):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            # bỏ qua tham số đầu là provider object khi tạo key: dùng id() của chúng
            key = (fn.__name__,) + tuple(
                a if isinstance(a, (str, int, float, tuple)) else id(a) for a in args
            ) + tuple(sorted(kwargs.items()))
            now = time.time()
            with _lock:
                hit = _store.get(key)
                if hit and now - hit[0] < seconds:
                    return hit[1]
            val = fn(*args, **kwargs)
            with _lock:
                _store[key] = (now, val)
            return val
        return wrapper
    return deco


def clear_cache():
    with _lock:
        _store.clear()


# ---------- Cache ra đĩa (sống qua các lần restart) — dùng cho BCTC, ít đổi ----------
import json
import os
import re

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache")


def disk_cache(seconds: int = 6 * 3600):
    """Cache kết quả JSON-able của method (bỏ qua self). BCTC đổi theo quý nên
    cache nhiều giờ: lần đầu chậm, các lần sau (kể cả sau khi tắt/mở server) gần như tức thì."""
    def deco(fn):
        @wraps(fn)
        def wrapper(self, *args, **kwargs):
            os.makedirs(CACHE_DIR, exist_ok=True)
            name = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{fn.__name__}_{'_'.join(map(str, args))}") + ".json"
            path = os.path.join(CACHE_DIR, name)
            try:
                if os.path.exists(path) and time.time() - os.path.getmtime(path) < seconds:
                    with open(path, encoding="utf-8") as f:
                        return json.load(f)
            except Exception:
                pass
            val = fn(self, *args, **kwargs)
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(val, f, ensure_ascii=False)
            except Exception:
                pass
            return val
        return wrapper
    return deco

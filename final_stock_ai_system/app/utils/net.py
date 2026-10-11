"""Tiện ích dùng chung cho mọi lệnh gọi mạng tới vnstock (giá, BCTC, thông tin
doanh nghiệp). Gom về 1 chỗ để sửa 1 lần, dùng ở mọi provider.

Trước đây mỗi provider (price_provider, fundamental_provider) tự có 1 lock
giãn cách RIÊNG, còn company_provider không có gì cả — tổng số request/giây
thực tế gửi sang vnstock KHÔNG được kiểm soát tập trung, dễ dính rate-limit
(vnstock ném SystemExit/429) → request bị treo hoặc lỗi 500 → frontend thấy
"Có lỗi xảy ra ở server" / "Failed to fetch". `vnstock_call()` ở đây là CỔNG
DUY NHẤT mọi provider phải đi qua khi gọi vnstock, đảm bảo giãn cách đúng vd
dù nhiều request HTTP chạy song song (nhờ threaded=True ở run.py)."""
from __future__ import annotations
import time
import threading
import concurrent.futures

import os

_gate_lock = threading.Lock()
_last_call = 0.0
# 20 request/phút (gói Khách) = 1 lệnh/3s; 60/phút (có API key) = 1 lệnh/1s.
# Đặt VNSTOCK_MIN_INTERVAL trong .env nếu muốn tự chỉnh (vd =1.1 khi đã có key).
MIN_INTERVAL = float(os.getenv("VNSTOCK_MIN_INTERVAL", "1.0"))


class Breaker:
    """Cầu dao: nguồn lỗi/timeout -> tạm ngắt `cooldown` giây, các lần gọi sau bỏ qua NGAY thay vì
    mỗi request lại chờ hết timeout (nguyên nhân chính làm web lag khi VCI không phản hồi)."""
    def __init__(self, cooldown: float = 180.0):
        self.cooldown = cooldown
        self._until = 0.0

    def is_open(self) -> bool:
        return time.time() < self._until

    def seconds_left(self) -> int:
        return max(0, int(self._until - time.time()))

    def trip(self):
        self._until = time.time() + self.cooldown

    def reset(self):
        self._until = 0.0


def vnstock_call(fn, *args, retries: int = 1, retry_wait: float = 15.0, **kwargs):
    """Gọi 1 hàm vnstock với giãn cách tối thiểu MIN_INTERVAL (toàn cục, mọi
    provider dùng chung) + tự retry khi dính rate limit."""
    global _last_call
    for attempt in range(retries + 1):
        with _gate_lock:
            wait = MIN_INTERVAL - (time.time() - _last_call)
            if wait > 0:
                time.sleep(wait)
            _last_call = time.time()
        try:
            return fn(*args, **kwargs)
        except BaseException as e:          # vnstock có thể ném SystemExit khi vượt giới hạn
            msg = str(e).lower()
            rate_limited = "rate" in msg or "429" in msg or "too many" in msg or isinstance(e, SystemExit)
            if rate_limited and attempt < retries:
                time.sleep(retry_wait)
                continue
            if rate_limited:
                raise RuntimeError(f"vnstock bị giới hạn tốc độ, vui lòng thử lại sau ít giây: {e}") from e
            raise RuntimeError(f"vnstock lỗi: {e}") from e


def call_with_timeout(fn, *args, timeout: int = 15, **kwargs):
    """Giới hạn thời gian chờ 1 lệnh gọi mạng/blocking bất kỳ. LƯU Ý: không dùng
    `with ThreadPoolExecutor(...)` vì __exit__ của nó chờ thread chạy xong ->
    timeout không có tác dụng thực sự (nguyên nhân treo/lag nếu copy sai kiểu
    này). Ở đây shutdown(wait=False) để trả lỗi ngay khi quá hạn, thread chạy
    dở sẽ tự kết thúc sau ở nền."""
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = ex.submit(fn, *args, **kwargs)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        raise TimeoutError(f"Quá thời gian chờ ({timeout}s) khi lấy dữ liệu.")
    finally:
        ex.shutdown(wait=False)

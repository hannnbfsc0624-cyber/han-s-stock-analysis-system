"""Thông tin doanh nghiệp thật: hồ sơ công ty, cổ đông lớn, ban lãnh đạo.
Dùng thư viện vnstock (nguồn VCI) — cần Internet.

Cache ra đĩa (app/.cache/company_<MÃ>.json):
  - có cổ đông hoặc ban lãnh đạo -> nhớ 6 giờ
  - thiếu cả hai (thường do timeout / rate limit) -> chỉ nhớ 10 phút rồi tự thử lại
    (trước đây kết quả thiếu vẫn bị nhớ 6 giờ nên cứ hiện "Không có dữ liệu" mãi).
Lỗi từng phần được ghi vào log và trả về trong trường "errors" để biết lý do.
"""
from __future__ import annotations
import json
import logging
import os
import time

from app.utils.net import vnstock_call, call_with_timeout, Breaker
from app.utils.fcache import CACHE_DIR

_log = logging.getLogger(__name__)
_TTL_FULL = 6 * 3600
_TTL_PARTIAL = 600
_vci = Breaker(180)      # VCI (iq.vietcap.com.vn) hay timeout -> lỗi 1 lần là bỏ qua 3 phút

# com_group_code của VCI là MÃ CHỈ SỐ, không phải sàn -> đổi sang tên sàn
_EXCHANGES = {"VNINDEX": "HOSE", "HNXINDEX": "HNX", "HNXUPCOMINDEX": "UPCoM", "UPCOMINDEX": "UPCoM"}


def _safe(fn, timeout: int = 20):
    """Trả (danh sách bản ghi, lỗi|None). Không nuốt lỗi âm thầm nữa."""
    try:
        df = call_with_timeout(lambda: vnstock_call(fn), timeout=timeout)
        return df.to_dict(orient="records"), None
    except Exception as e:
        _log.warning("company: %s", e)
        return [], f"{type(e).__name__}: {e}"


def _fetch(ticker: str) -> dict:
    if _vci.is_open():
        return dict(ticker=ticker, overview={}, shareholders=[], officers=[],
                    error=f"Máy chủ VietCap đang chậm/không phản hồi — tạm bỏ qua, thử lại sau {_vci.seconds_left()}s")
    try:
        from vnstock import Vnstock
        c = call_with_timeout(lambda: Vnstock().stock(symbol=ticker, source="VCI").company, timeout=15)
    except Exception as e:
        _vci.trip()
        return dict(ticker=ticker, overview={}, shareholders=[], officers=[], error=str(e))

    ov, err_ov = _safe(c.overview)
    if err_ov:                                   # overview lỗi -> không tốn thêm 2 lần timeout nữa
        _vci.trip()
        return dict(ticker=ticker, overview={}, shareholders=[], officers=[], errors=dict(overview=err_ov), error=err_ov)
    sh, err_sh = _safe(c.shareholders)
    of, err_of = _safe(lambda: c.officers(filter_by="working"))
    errors = {k: v for k, v in dict(overview=err_ov, shareholders=err_sh, officers=err_of).items() if v}
    return dict(ticker=ticker, overview=ov[0] if ov else {}, shareholders=sh, officers=of, errors=errors)


def _decorate(data: dict) -> dict:
    """Thêm tên sàn đúng + ngành tiếng Việt của hệ thống (VCI chỉ có ngành tiếng Anh)."""
    from app.data.fundamental_provider import sector_of
    ov = data.get("overview") or {}
    code = str(ov.get("exchange") or ov.get("com_group_code") or "").upper()
    ov["exchange_name"] = _EXCHANGES.get(code, ov.get("exchange") or code or None)
    ov["sector_vi"] = sector_of(data.get("ticker", ""))
    data["overview"] = ov
    return data


def _path(ticker: str) -> str:
    return os.path.join(CACHE_DIR, f"company_{ticker}.json")


def get_company_profile(ticker: str) -> dict:
    ticker = ticker.upper()
    cached = None
    try:
        if os.path.exists(_path(ticker)):
            with open(_path(ticker), encoding="utf-8") as f:
                cached = json.load(f)
            age = time.time() - os.path.getmtime(_path(ticker))
            full = bool(cached.get("shareholders") or cached.get("officers"))
            if cached.get("overview") and age < (_TTL_FULL if full else _TTL_PARTIAL):
                return _decorate(cached)
    except Exception:
        cached = None

    fresh = _fetch(ticker)
    if fresh.get("overview"):
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(_path(ticker), "w", encoding="utf-8") as f:
                json.dump(fresh, f, ensure_ascii=False)
        except Exception:
            pass
        return _decorate(fresh)
    if cached and cached.get("overview"):            # mạng lỗi: dùng bản cũ còn hơn không có
        return _decorate(cached)
    return dict(ticker=ticker, overview={}, shareholders=[], officers=[], error=fresh.get("error") or f"{ticker}: không lấy được thông tin doanh nghiệp")


def get_issue_shares(ticker: str) -> float | None:
    """Số cổ phiếu đang lưu hành (cp) từ hồ sơ VCI — dùng để tính EPS/BVPS/P/E/P/B chính xác
    thay vì suy ra từ vốn góp / mệnh giá. Chỉ 1 request, cache 24 giờ; lỗi -> None."""
    ticker = ticker.upper()
    try:                                              # 1) đã có hồ sơ đầy đủ trong cache
        if os.path.exists(_path(ticker)):
            with open(_path(ticker), encoding="utf-8") as f:
                v = (json.load(f).get("overview") or {}).get("issue_share")
            if v and float(v) > 0:
                return float(v)
    except Exception:
        pass
    sp = os.path.join(CACHE_DIR, f"shares_{ticker}.json")
    try:                                              # 2) cache riêng 24 giờ
        if os.path.exists(sp) and time.time() - os.path.getmtime(sp) < 24 * 3600:
            with open(sp, encoding="utf-8") as f:
                v = json.load(f).get("issue_share")
            return float(v) if v else None
    except Exception:
        pass
    if _vci.is_open():
        return None
    try:                                              # 3) gọi vnstock (chỉ overview)
        from vnstock import Vnstock
        c = call_with_timeout(lambda: Vnstock().stock(symbol=ticker, source="VCI").company, timeout=15)
        rec, err = _safe(c.overview)
        if err:
            _vci.trip()
        v = float(rec[0].get("issue_share") or 0) if rec else 0
        if v > 0:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(sp, "w", encoding="utf-8") as f:
                json.dump(dict(issue_share=v), f)
            return v
    except Exception as e:
        _vci.trip()
        _log.warning("issue_share %s: %s", ticker, e)
    return None

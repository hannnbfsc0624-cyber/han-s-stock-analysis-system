"""
Price data providers.

Mọi provider đều implement cùng 1 interface:
    get_ohlcv(ticker, days) -> pandas.DataFrame[date, open, high, low, close, volume]
    get_quote(ticker)       -> dict{ticker, price, change_pct, volume}

=> Muốn đổi nguồn dữ liệu (VPS/SSI/vnstock...) chỉ cần viết thêm 1 class kế thừa
   BasePriceProvider, không phải sửa phần còn lại của hệ thống (Screener, API...).
"""
from __future__ import annotations
import abc
import random
import datetime as dt
import pandas as pd
import requests

from app.utils.cache import price_cache, cached
from app.utils.net import vnstock_call


class BasePriceProvider(abc.ABC):
    @abc.abstractmethod
    def get_ohlcv(self, ticker: str, days: int = 120) -> pd.DataFrame:
        ...

    @abc.abstractmethod
    def get_quote(self, ticker: str) -> dict:
        ...

    def get_quotes(self, tickers: list[str]) -> list[dict]:
        return [self.get_quote(t) for t in tickers]


class MockPriceProvider(BasePriceProvider):
    """Sinh dữ liệu giả lập có xu hướng ngẫu nhiên (random walk) để dev/test
    offline mà không cần internet hay API key. Kết quả deterministic theo ticker
    (seed theo tên mã) để mỗi lần chạy ra dữ liệu ổn định."""

    def _seeded_random(self, ticker: str) -> random.Random:
        return random.Random(sum(ord(c) for c in ticker))

    def get_ohlcv(self, ticker: str, days: int = 120) -> pd.DataFrame:
        rng = self._seeded_random(ticker)
        base_price = rng.uniform(15, 150)
        rows = []
        price = base_price
        today = dt.date.today()
        for i in range(days, 0, -1):
            date = today - dt.timedelta(days=i)
            drift = rng.uniform(-0.02, 0.022)  # nhích lên nhẹ theo thời gian
            price = max(1.0, price * (1 + drift))
            open_ = price * (1 + rng.uniform(-0.01, 0.01))
            high = max(open_, price) * (1 + rng.uniform(0, 0.015))
            low = min(open_, price) * (1 - rng.uniform(0, 0.015))
            volume = int(rng.uniform(0.5, 15) * 1_000_000)
            rows.append(
                dict(date=date, open=round(open_, 2), high=round(high, 2),
                     low=round(low, 2), close=round(price, 2), volume=volume)
            )
        return pd.DataFrame(rows)

    def get_quote(self, ticker: str) -> dict:
        df = self.get_ohlcv(ticker, days=2)
        prev, last = df.iloc[-2], df.iloc[-1]
        change_pct = (last["close"] - prev["close"]) / prev["close"] * 100
        return dict(
            ticker=ticker,
            price=last["close"],
            change_pct=round(change_pct, 2),
            volume=int(last["volume"]),
        )

class VnstockRealProvider(BasePriceProvider):
    """Giá THẬT từ thư viện vnstock (nguồn VCI) — miễn phí, không cần API key.
    Cần: pip install vnstock, và máy có Internet lúc chạy.
    Lưu ý: đây là giá đóng cửa theo ngày (EOD), không phải khớp lệnh theo tick
    từng giây trong phiên — với hầu hết use-case phân tích kỹ thuật là đủ dùng."""

    def __init__(self, source: str | None = None):
        import os
        self.source = source or os.getenv("VNSTOCK_SOURCE", "VCI")

    def _throttled(self, fn, *args, **kwargs):
        """Đi qua cổng giãn cách DÙNG CHUNG cho mọi lệnh gọi vnstock trong cả hệ
        thống (giá + BCTC + thông tin doanh nghiệp) — xem app/utils/net.py."""
        return vnstock_call(fn, *args, **kwargs)

    def _client(self, ticker: str):
        from vnstock import Vnstock
        return Vnstock().stock(symbol=ticker.upper(), source=self.source)

    @cached(price_cache, "ohlcv")
    def get_ohlcv(self, ticker: str, days: int = 120) -> pd.DataFrame:
        end = dt.date.today()
        start = end - dt.timedelta(days=int(days * 1.6) + 10)
        client = self._client(ticker)
        df = self._throttled(
            client.quote.history, start=start.isoformat(), end=end.isoformat(), interval="1D"
        )
        df = df.rename(columns={c: str(c).lower() for c in df.columns}).rename(columns={"time": "date"})
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df[["date", "open", "high", "low", "close", "volume"]].dropna().sort_values("date")
        if df["close"].median() > 1000:            # nguồn trả về đồng -> đổi sang nghìn đồng cho thống nhất
            for c in ("open", "high", "low", "close"):
                df[c] = df[c] / 1000
        return df.tail(days).reset_index(drop=True)

    @cached(price_cache, "quote")
    def get_quote(self, ticker: str) -> dict:
        df = self.get_ohlcv(ticker, days=3)
        prev, last = df.iloc[-2], df.iloc[-1]
        change_pct = (last["close"] - prev["close"]) / prev["close"] * 100
        return dict(
            ticker=ticker.upper(),
            price=float(last["close"]),
            change_pct=round(float(change_pct), 2),
            volume=int(last["volume"]),
        )

    def get_quotes(self, tickers: list[str]) -> list[dict]:
        """vnstock không có endpoint batch nhiều mã thật sự — mỗi mã vẫn 1
        request, nhưng đã kiểm soát an toàn: cache trước (60s), giãn cách
        0.1s/request, tự retry khi rate limit."""
        return [self.get_quote(t) for t in tickers]
class VnstockLikeProvider(BasePriceProvider):
    """STUB — khung sẵn để cắm nguồn thật (vnstock-js server riêng, SSI FastConnect,
    VPS/Entrade, hoặc bất kỳ REST API nào trả về OHLCV).

    Cách dùng thực tế:
      1. Deploy 1 service nhỏ (Node/Python) bọc quanh vnstock / SSI SDK, expose
         REST endpoint dạng GET /ohlcv?ticker=FPT&days=120
      2. Set PRICE_PROVIDER=vnstock và PRICE_API_BASE_URL trong .env
      3. Class này sẽ gọi sang service đó.

    KHÔNG gọi thẳng SSI/VPS SDK ở đây để tránh phụ thuộc SDK độc quyền — tách
    riêng 1 lớp adapter service giúp dễ thay nguồn dữ liệu sau này.
    """

    def __init__(self, base_url: str, timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def get_ohlcv(self, ticker: str, days: int = 120) -> pd.DataFrame:
        resp = requests.get(
            f"{self.base_url}/ohlcv",
            params={"ticker": ticker, "days": days},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return pd.DataFrame(resp.json())

    def get_quote(self, ticker: str) -> dict:
        resp = requests.get(
            f"{self.base_url}/quote", params={"ticker": ticker}, timeout=self.timeout
        )
        resp.raise_for_status()
        return resp.json()


def get_price_provider(name: str, base_url: str | None = None) -> BasePriceProvider:
    name = (name or "mock").lower()
    if name == "mock":
        return MockPriceProvider()
    if name == "vnstock_real":
        import os
        return VnstockRealProvider(os.getenv("VNSTOCK_SOURCE", "VCI"))
    if name in ("vnstock", "ssi", "vps"):
        if not base_url:
            raise ValueError(
                f"Provider '{name}' cần PRICE_API_BASE_URL trong .env "
                f"(URL của service adapter bạn tự deploy)."
            )
        return VnstockLikeProvider(base_url)
    raise ValueError(f"Unknown price provider: {name}")
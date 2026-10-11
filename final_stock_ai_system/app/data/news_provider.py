"""
News providers — thu thập tin tức liên quan tới 1 mã cổ phiếu.

Interface chung:
    get_news(ticker, limit) -> list[dict(title, url, source, published_at, raw_text)]

Có 2 loại:
  - MockNewsProvider: sinh tin giả để test/dev offline.
  - CafeFNewsProvider: scraper thật (cần internet, chạy trên máy của bạn, không
    chạy được trong môi trường sandbox này vì không có network access).
"""
from __future__ import annotations
import abc
import random
import datetime as dt
import requests
from bs4 import BeautifulSoup

from app.utils.cache import news_cache, cached


class BaseNewsProvider(abc.ABC):
    @abc.abstractmethod
    def get_news(self, ticker: str, limit: int = 10) -> list[dict]:
        ...


class MockNewsProvider(BaseNewsProvider):
    """Tạo tin tức giả lập với tiêu đề mang sắc thái tích cực/tiêu cực/trung lập
    rõ ràng, để bạn test pipeline sentiment + screener mà không cần internet."""

    _TEMPLATES = [
        ("{t} bứt phá mạnh, khối ngoại mua ròng phiên thứ 3 liên tiếp", "CafeF"),
        ("{t} báo lãi quý tăng trưởng vượt kỳ vọng thị trường", "Vietstock"),
        ("Cổ phiếu {t} lập đỉnh mới nhờ dòng tiền bắt đáy", "FireAnt"),
        ("{t} bị khối ngoại xả hàng mạnh, áp lực bán gia tăng", "CafeF"),
        ("Cảnh báo rủi ro: {t} giảm sàn 2 phiên liên tiếp", "Vietstock"),
        ("{t} đi ngang tích lũy, thanh khoản thấp", "FireAnt"),
        ("Doanh nghiệp {t} công bố kế hoạch mở rộng nhà máy", "CafeF"),
        ("{t}: Lợi nhuận sụt giảm do chi phí nguyên liệu tăng", "Vietstock"),
    ]

    def get_news(self, ticker: str, limit: int = 10) -> list[dict]:
        rng = random.Random(sum(ord(c) for c in ticker) + limit)
        items = []
        now = dt.datetime.now()
        for i in range(limit):
            title_tpl, source = rng.choice(self._TEMPLATES)
            title = title_tpl.format(t=ticker)
            items.append(
                dict(
                    ticker=ticker,
                    title=title,
                    url=f"https://example.com/news/{ticker.lower()}-{i}",
                    source=source,
                    published_at=(now - dt.timedelta(hours=i * 6)).isoformat(),
                    raw_text=title,
                )
            )
        return items

class GoogleNewsProvider(BaseNewsProvider):
    """Tin THẬT qua Google News RSS — gộp từ nhiều báo kinh tế uy tín (CafeF,
    Vietstock, VnEconomy, Đầu tư Chứng khoán...), có link bấm ra bài gốc.
    Không cần API key, chỉ cần Internet."""

    COMPANY_HINTS = {
        "FPT": "Tập đoàn FPT", "HPG": "Hòa Phát", "VCB": "Vietcombank",
        "TCB": "Techcombank", "VIC": "Vingroup", "VNM": "Vinamilk",
        "MSN": "Masan", "MWG": "Thế Giới Di Động", "VHM": "Vinhomes",
        "GAS": "PV Gas", "CTG": "VietinBank", "BID": "BIDV",
        "VPB": "VPBank", "SSI": "Chứng khoán SSI", "PNJ": "PNJ",
    }

    @cached(news_cache, "google_news")
    def get_news(self, ticker: str, limit: int = 10) -> list[dict]:
        import xml.etree.ElementTree as ET
        import urllib.parse

        ticker = ticker.upper()
        hint = self.COMPANY_HINTS.get(ticker, "")
        query = f"cổ phiếu {ticker}" + (f' OR "{hint}"' if hint else "")
        url = ("https://news.google.com/rss/search?q="
               + urllib.parse.quote(query) + "&hl=vi&gl=VN&ceid=VN:vi")
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        items = []
        for item in root.findall(".//item")[:limit]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_raw = item.findtext("pubDate") or ""
            source_el = item.find("source")
            source = source_el.text.strip() if source_el is not None and source_el.text else "Google News"
            try:
                published = dt.datetime.strptime(pub_raw, "%a, %d %b %Y %H:%M:%S %Z").isoformat()
            except ValueError:
                published = dt.datetime.now().isoformat()
            if not title or not link:
                continue
            items.append(dict(ticker=ticker, title=title, url=link, source=source,
                               published_at=published, raw_text=title))
        return items
class CafeFNewsProvider(BaseNewsProvider):
    """Scraper thật cho trang tìm kiếm tin theo mã trên CafeF.
    CẦN INTERNET — chạy trên máy/server của bạn, không chạy được trong sandbox
    này (không có network access ở đây).

    Ghi chú: CafeF có thể đổi cấu trúc HTML bất kỳ lúc nào — nếu scraper lỗi,
    kiểm tra lại selector bên dưới hoặc chuyển sang dùng Firecrawl API
    (ổn định hơn, tự xử lý JS-rendered page)."""

    SEARCH_URL = "https://cafef.vn/tim-kiem/trang-1.chn"

    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self.headers = {"User-Agent": "Mozilla/5.0 (compatible; StockAI/1.0)"}

    @cached(news_cache, "cafef")
    def get_news(self, ticker: str, limit: int = 10) -> list[dict]:
        resp = requests.get(
            self.SEARCH_URL, params={"keywords": ticker}, headers=self.headers,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        items = []
        for a in soup.select("h3 a[href]")[:limit]:
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if not title:
                continue
            url = href if href.startswith("http") else f"https://cafef.vn{href}"
            items.append(
                dict(
                    ticker=ticker,
                    title=title,
                    url=url,
                    source="CafeF",
                    published_at=dt.datetime.now().isoformat(),
                    raw_text=title,
                )
            )
        return items


def get_news_provider(name: str) -> BaseNewsProvider:
    name = (name or "mock").lower()
    if name == "mock":
        return MockNewsProvider()
    if name == "cafef":
        return CafeFNewsProvider()
    if name == "google_news":
        return GoogleNewsProvider()
    raise ValueError(f"Unknown news provider: {name}. Thêm class mới tương tự cho nguồn khác.")
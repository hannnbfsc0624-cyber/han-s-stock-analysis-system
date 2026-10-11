"""Lấy báo cáo + giá mục tiêu của NHIỀU CTCK cùng lúc từ Simplize.

Trang https://simplize.vn/co-phieu/<MÃ>/bao-cao có bảng "Báo cáo phân tích về <MÃ>":
Ngày | Tiêu đề | Nguồn (CTCK) | Khuyến nghị | Giá mục tiêu điều chỉnh | Tải về.
Bảng này được render sẵn trong HTML nên chỉ cần requests + html.parser (stdlib),
không cần thêm thư viện.

Best-effort: lỗi mạng / đổi giao diện -> trả [] (hệ thống vẫn dùng CSV + VietCap).
"""
from __future__ import annotations
import datetime as dt
import json
import re
import threading
import unicodedata
from difflib import SequenceMatcher
import time
from html.parser import HTMLParser
from urllib.parse import urljoin

import requests

BASE = "https://simplize.vn"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
}
_DATE = re.compile(r"^\d{2}/\d{2}/\d{4}$")

# khoá chuẩn -> tên hiển thị
BROKER_NAMES = {
    "kafi": "KAFI", "mbs": "MBS", "kbsv": "KBSV", "shinhan": "Shinhan (SSV)",
    "miraeasset": "Mirae Asset (MAS)", "acbs": "ACBS", "vds": "VDS", "dsc": "DSC",
    "vietcap": "Vietcap", "vnds": "VNDirect (VNDS)", "shs": "SHS", "nhsv": "NHSV",
    "fpts": "FPTS", "bsc": "BSC", "ssi": "SSI", "hsc": "HSC", "vcbs": "VCBS",
    "bvsc": "BVSC", "agr": "Agriseco (AGR)", "ctbc": "CTBC", "tps": "TPS", "psi": "PSI",
    "abs": "ABS", "aseansecurities": "ASEAN Securities", "hdbs": "HDBS", "vpbs": "VPBankS",
}
# cách viết khác -> khoá chuẩn
_ALIASES = {
    "mas": "miraeasset", "miraeassetmas": "miraeasset", "ssv": "shinhan",
    "shinhansecurities": "shinhan", "shinhanssv": "shinhan", "vci": "vietcap",
    "vietcaptudongvnstock": "vietcap", "vndirect": "vnds", "vndirectvnds": "vnds",
    "aseansc": "aseansecurities", "agriseco": "agr", "agrisecoagr": "agr",
    "vpbankssecurities": "vpbs", "vpbanks": "vpbs",
}


def broker_key(name: str) -> str:
    """Khoá chuẩn hoá để gộp cùng 1 CTCK dù viết khác nhau (MAS = Mirae Asset...)."""
    k = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    return _ALIASES.get(k, k)


def pretty_broker(name: str) -> str:
    return BROKER_NAMES.get(broker_key(name), (name or "").strip())


class _TableParser(HTMLParser):
    """Gom mọi <tr>: danh sách ô (text) + href đầu tiên trong từng ô."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[list[tuple[str, str]]] = []
        self._row: list[tuple[str, str]] | None = None
        self._cell_text: list[str] | None = None
        self._cell_href = ""
        self._skip = 0                      # đang ở trong <style>/<script> -> bỏ qua text

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script"):
            self._skip += 1
            return
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell_text, self._cell_href = [], ""
        elif tag == "a" and self._cell_text is not None and not self._cell_href:
            self._cell_href = dict(attrs).get("href") or ""

    def handle_data(self, data):
        if self._skip:
            return
        if self._cell_text is not None:
            self._cell_text.append(data)

    def handle_endtag(self, tag):
        if tag in ("style", "script"):
            self._skip = max(0, self._skip - 1)
            return
        if tag in ("td", "th") and self._row is not None and self._cell_text is not None:
            self._row.append((" ".join("".join(self._cell_text).split()), self._cell_href))
            self._cell_text = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


_CSS = re.compile(r"[.#@][\w\-]+\s*\{[^}]*\}")


def _clean(txt: str) -> str:
    """Xoá đoạn CSS (.css-xxx{...}) nếu lỡ lọt vào text của ô."""
    return " ".join(_CSS.sub(" ", txt or "").split())


def _parse_price(txt: str) -> float | None:
    digits = re.sub(r"[^\d]", "", txt or "")
    if not digits:
        return None
    v = float(digits)
    return v / 1000 if v > 1000 else v            # đồng -> nghìn đồng (đơn vị của app)


_PDF_RE = re.compile(r"https?://[^\s\"'<>\\]+?\.pdf", re.I)
_NEXT_RE = re.compile(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def _norm(s: str) -> str:
    """Bỏ dấu tiếng Việt, chỉ giữ chữ + số, viết thường: để so khớp tiêu đề <-> tên file."""
    s = unicodedata.normalize("NFD", (s or "").lower().replace("đ", "d"))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]", "", s)


def _is_pdf(v) -> bool:
    return isinstance(v, str) and ".pdf" in v.lower()


def _walk_pdf_records(node, out: dict):
    """Duyệt JSON (__NEXT_DATA__): mỗi object có ĐÚNG 1 link .pdf -> ghi nhận
    {chuẩn_hoá(mọi chuỗi trong object): link}. Nhờ vậy ghép được tiêu đề <-> PDF thật."""
    if isinstance(node, dict):
        pdfs, texts = [], []
        for v in node.values():
            if _is_pdf(v):
                pdfs.append(v)
            elif isinstance(v, str):
                texts.append(v)
            elif isinstance(v, dict):                  # object lồng 1 cấp: {"title":..,"file":{"url":..pdf}}
                for v2 in v.values():
                    if _is_pdf(v2):
                        pdfs.append(v2)
                    elif isinstance(v2, str):
                        texts.append(v2)
        if len(set(pdfs)) == 1:
            link = pdfs[0]
            for t in texts:
                k = _norm(t)
                if len(k) >= 12:
                    out.setdefault(k, link)
        for v in node.values():
            _walk_pdf_records(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk_pdf_records(v, out)


def extract_pdf_links(html: str) -> tuple[dict, list]:
    """Trả (map chuẩn_hoá(text)->link pdf từ JSON, danh sách mọi link .pdf trong trang)."""
    mapping: dict = {}
    m = _NEXT_RE.search(html)
    if m:
        try:
            _walk_pdf_records(json.loads(m.group(1)), mapping)
        except Exception:
            pass
    all_pdfs = list(dict.fromkeys(_PDF_RE.findall(html.replace("\\u002F", "/").replace("\\/", "/"))))
    return mapping, all_pdfs


def match_pdf(title: str, mapping: dict, all_pdfs: list) -> str:
    """Tìm link PDF đúng của 1 báo cáo; không chắc chắn thì trả '' (để dùng link trang)."""
    t = _norm(title)
    if len(t) < 8:
        return ""
    if t in mapping:                                    # 1) trùng hoàn toàn tiêu đề
        return mapping[t]
    for k, link in mapping.items():                     # 2) tiêu đề nằm trong / chứa text của record
        if len(k) >= 15 and (t in k or k in t):
            return link
    best, best_r = "", 0.0                              # 3) tên file ~ tiêu đề (slug của tiêu đề)
    for link in all_pdfs:
        stem = _norm(link.rsplit("/", 1)[-1].rsplit(".", 1)[0])
        if not stem:
            continue
        r = 1.0 if (len(stem) >= 15 and (stem in t or t in stem)) else SequenceMatcher(None, t, stem).ratio()
        if r > best_r:
            best, best_r = link, r
    return best if best_r >= 0.8 else ""


def parse_reports_html(html: str, ticker: str) -> list[dict]:
    p = _TableParser()
    p.feed(html)
    page_url = f"{BASE}/co-phieu/{ticker.upper()}/bao-cao"
    pdf_map, all_pdfs = extract_pdf_links(html)
    out = []
    for row in p.rows:
        cells = [_clean(c[0]) for c in row]
        if len(cells) < 5 or not _DATE.match(cells[0]):
            continue
        tp = _parse_price(cells[4])
        if tp is None:                             # báo cáo không có giá mục tiêu (rating "KHÁC")
            continue
        d, m, y = cells[0].split("/")
        # link PDF gốc nếu bảng có, nếu không thì dùng chính trang Simplize
        href = next((h for _, h in row if h and re.search(r"\.pdf|download|file", h, re.I)), "") \
            or match_pdf(cells[1], pdf_map, all_pdfs)
        # giá mục tiêu GỐC ghi trong tiêu đề báo cáo ("Giá MT VND 92,400"); cột bảng là giá ĐIỀU CHỈNH
        mo = re.search(r"Giá\s*MT[^\d]{0,12}(\d[\d.,]*)", cells[1], re.I)
        tp_orig = _parse_price(mo.group(1)) if mo else None
        if tp_orig is not None and not (1 <= tp_orig < 1000):
            tp_orig = None
        out.append(dict(
            broker=pretty_broker(cells[2]), date=f"{y}-{m}-{d}", rating=cells[3].strip().upper(),
            target_price=round(tp, 2), title=cells[1],
            target_price_original=round(tp_orig, 2) if tp_orig else None,
            url=urljoin(BASE, href) if href else page_url, is_mock=False,
        ))
    return out


def fetch_simplize_reports(ticker: str, timeout: int = 8) -> list[dict]:
    """Trả về các báo cáo (đã có giá mục tiêu) của mã, mới -> cũ. Lỗi -> []."""
    try:
        r = requests.get(f"{BASE}/co-phieu/{ticker.upper()}/bao-cao", headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        rows = parse_reports_html(r.text, ticker)
    except Exception:
        return []
    return sorted(rows, key=lambda x: x["date"], reverse=True)


def latest_per_broker(rows: list[dict]) -> dict[str, dict]:
    best: dict[str, dict] = {}
    for row in rows:
        k = broker_key(row["broker"])
        if k and (k not in best or row["date"] > best[k]["date"]):
            best[k] = row
    return best


# ---- cache RAM: có dữ liệu -> nhớ 6 giờ; rỗng/lỗi mạng -> chỉ nhớ 60s rồi thử lại ----
_cache: dict[str, tuple[float, list[dict]]] = {}
_cache_lock = threading.Lock()


def fetch_simplize_cached(ticker: str) -> list[dict]:
    t = ticker.upper()
    now = time.time()
    with _cache_lock:
        hit = _cache.get(t)
        if hit and now < hit[0]:
            return hit[1]
    rows = fetch_simplize_reports(t)
    with _cache_lock:
        _cache[t] = (now + (6 * 3600 if rows else 60), rows)
    return rows

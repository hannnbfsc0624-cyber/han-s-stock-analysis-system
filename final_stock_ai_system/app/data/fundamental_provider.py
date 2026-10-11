"""
Dữ liệu cơ bản (BCTC) + báo cáo của các CTCK.

Interface:
    get_financials(ticker) -> dict   (số liệu 4 năm gần nhất + ngành + giá/cp)
    get_reports(ticker)    -> list[dict(broker, date, rating, target_price, url, summary)]

MOCK: sinh số liệu giả lập có quy luật theo ngành, deterministic theo mã.
KHÔNG phải số liệu thật. Muốn dùng số thật: viết class kế thừa
BaseFundamentalProvider (vd bọc vnstock / CafeF / Vietstock) rồi đăng ký trong
get_fundamental_provider(). Link báo cáo của MOCK chỉ là link trang chủ CTCK.
"""
from __future__ import annotations
import abc
import random
import time
import datetime as dt

from app.utils.fcache import disk_cache
from app.data.simplize_provider import fetch_simplize_cached as _simplize_cached, broker_key, pretty_broker
from app.utils.net import call_with_timeout, vnstock_call

# mã -> ngành
SECTORS = {
    "FPT": "Công nghệ", "CMG": "Công nghệ", "ELC": "Công nghệ",
    "HPG": "Thép", "HSG": "Thép", "NKG": "Thép",
    "TCB": "Ngân hàng", "VCB": "Ngân hàng", "MBB": "Ngân hàng", "ACB": "Ngân hàng", "CTG": "Ngân hàng", "BID": "Ngân hàng",
    "VNM": "Thực phẩm & Đồ uống", "MSN": "Thực phẩm & Đồ uống", "SAB": "Thực phẩm & Đồ uống",
    "MWG": "Bán lẻ", "PNJ": "Bán lẻ", "DGW": "Bán lẻ", "FRT": "Bán lẻ",
    "VHM": "Bất động sản", "VIC": "Bất động sản", "NVL": "Bất động sản", "KDH": "Bất động sản",
    "SSI": "Chứng khoán", "VND": "Chứng khoán", "HCM": "Chứng khoán", "VCI": "Chứng khoán",
    "VPB": "Ngân hàng", "STB": "Ngân hàng", "HDB": "Ngân hàng", "TPB": "Ngân hàng", "VIB": "Ngân hàng", "SHB": "Ngân hàng", "SSB": "Ngân hàng", "LPB": "Ngân hàng",
    "DGC": "Hóa chất", "DPM": "Hóa chất", "DCM": "Hóa chất",
    "PDR": "Bất động sản", "DXG": "Bất động sản", "DIG": "Bất động sản", "BCM": "Bất động sản", "VRE": "Bất động sản",
    "POW": "Điện", "REE": "Điện", "PC1": "Điện", "GEG": "Điện",
    "GMD": "Cảng & Vận tải", "HAH": "Cảng & Vận tải", "VSC": "Cảng & Vận tải",
    "GVR": "Cao su & Hóa chất", "DPR": "Cao su & Hóa chất",
    "PVT": "Dầu khí", "GAS": "Dầu khí", "PLX": "Dầu khí",
}
DEFAULT_SECTOR = "Khác"

# (ROE%, biên LN ròng %, D/E, tăng trưởng LN %/năm, hệ số P/B hợp lý) theo ngành
_SECTOR_PROFILE = {
    "Công nghệ": (24, 14, 0.9, 20, 5.0),
    "Thép": (11, 7, 0.8, 12, 1.4),
    "Ngân hàng": (18, 30, 9.5, 17, 1.5),
    "Thực phẩm & Đồ uống": (26, 14, 0.4, 7, 5.0),
    "Bán lẻ": (19, 3.5, 1.4, 14, 3.0),
    "Bất động sản": (10, 16, 1.6, 10, 1.7),
    "Chứng khoán": (13, 38, 2.1, 15, 1.7),
    "Dầu khí": (12, 6, 0.7, 8, 1.6),
    "Hóa chất": (16, 12, 0.5, 10, 2.0), "Điện": (11, 10, 1.5, 8, 1.6),
    "Cảng & Vận tải": (14, 15, 0.6, 10, 1.8), "Cao su & Hóa chất": (9, 12, 0.5, 8, 1.8),
    DEFAULT_SECTOR: (13, 8, 1.0, 10, 1.8),
}

BROKERS = [
    ("SSI Research", "https://www.ssi.com.vn"),
    ("VNDIRECT", "https://www.vndirect.com.vn"),
    ("HSC", "https://www.hsc.com.vn"),
    ("VCBS", "https://www.vcbs.com.vn"),
    ("BSC", "https://www.bsc.com.vn"),
    ("MBS", "https://www.mbs.com.vn"),
    ("Rồng Việt (VDSC)", "https://www.vdsc.com.vn"),
]


def sector_of(ticker: str) -> str:
    return SECTORS.get(ticker.upper(), DEFAULT_SECTOR)


def peers_of(ticker: str) -> list[str]:
    s = sector_of(ticker)
    return [t for t, sec in SECTORS.items() if sec == s and t != ticker.upper()]


def _rng(ticker: str, salt: int = 0) -> random.Random:
    return random.Random(sum(ord(c) * (i + 3) for i, c in enumerate(ticker)) + salt)


class BaseFundamentalProvider(abc.ABC):
    @abc.abstractmethod
    def get_financials(self, ticker: str) -> dict: ...

    @abc.abstractmethod
    def get_reports(self, ticker: str, price: float | None = None) -> list[dict]: ...


class MockFundamentalProvider(BaseFundamentalProvider):
    def get_financials(self, ticker: str) -> dict:
        ticker = ticker.upper()
        sector = sector_of(ticker)
        roe0, margin0, de0, growth0, pb_fair = _SECTOR_PROFILE[sector]
        r = _rng(ticker)
        # mỗi mã lệch nhẹ so với mặt bằng ngành
        roe = roe0 * r.uniform(0.7, 1.3)
        margin = margin0 * r.uniform(0.7, 1.3)
        de = de0 * r.uniform(0.7, 1.3)
        growth = growth0 * r.uniform(0.5, 1.5)

        equity = r.uniform(8_000, 90_000)                 # tỷ đồng
        liabilities = equity * de
        assets = equity + liabilities
        bvps0 = r.uniform(10_000, 32_000)                 # đồng/cp
        shares = equity * 1000 / bvps0                    # triệu cp
        years = []
        year0 = dt.date.today().year - 1
        net_income_last = equity * roe / 100
        for k in range(3, -1, -1):                        # 4 năm: y-3 ... y
            f = (1 + growth / 100) ** (-k) * r.uniform(0.94, 1.06)
            ni = net_income_last * f
            rev = ni / (margin / 100)
            eq = equity * (1 + roe / 100 * 0.6) ** (-k)
            asset = eq * (1 + de)
            years.append(dict(
                year=year0 - k, revenue=round(rev), net_income=round(ni),
                equity=round(eq), assets=round(asset),
                gross_margin=round(min(60, margin * r.uniform(1.6, 2.6)), 1),
                eps=round(ni / shares * 1000),            # đồng/cp
            ))
        last = years[-1]
        bvps = last["equity"] / shares * 1000
        # giá thị trường quanh P/B "hợp lý" của ngành
        price_k = bvps * pb_fair * r.uniform(0.7, 1.4) / 1000
        return dict(
            ticker=ticker, sector=sector, shares_mil=round(shares),
            price=round(price_k, 2),                      # nghìn đồng
            bvps=round(bvps),
            current_ratio=round(r.uniform(0.9, 2.2), 2) if sector != "Ngân hàng" else None,
            interest_coverage=round(r.uniform(2, 12), 1) if sector != "Ngân hàng" else None,
            dividend_yield=round(r.uniform(0, 4.5), 1),
            years=years,
            is_mock=True,
        )

    def get_reports(self, ticker: str, price: float | None = None) -> list[dict]:
        ticker = ticker.upper()
        fin = self.get_financials(ticker)
        price = price or fin["price"]
        r = _rng(ticker, 99)
        n = r.randint(4, 6)
        picked = r.sample(BROKERS, n)
        today = dt.date.today()
        out = []
        for i, (name, url) in enumerate(picked):
            upside = r.uniform(-0.06, 0.34)
            tp = round(price * (1 + upside), 2)
            rating = "MUA" if upside > 0.15 else "KHẢ QUAN" if upside > 0.05 else "TRUNG LẬP" if upside > -0.03 else "GIẢM TỶ TRỌNG"
            out.append(dict(
                broker=name, date=(today - dt.timedelta(days=r.randint(3, 75))).isoformat(),
                rating=rating, target_price=tp, url=url, is_mock=True,
            ))
        out.sort(key=lambda x: x["date"], reverse=True)
        return out


# =====================================================================
#  DỮ LIỆU THẬT (vnstock)  —  CHỖ CẦN SỬA NẾU CẤU TRÚC BẢNG ĐỔI
# =====================================================================
# vnstock 4.0.x trả BCTC dạng "DỌC": mỗi DÒNG là 1 chỉ tiêu (cột 'item_id' =
# mã chỉ tiêu tiếng Anh, 'item' = tên tiếng Việt), mỗi CỘT năm ('2025',...)
# là 1 kỳ báo cáo.
KEYS = {
    "revenue":    {"item_id": ["net_sales", "revenue", "net_interest_income", "total_operating_income", "net_revenue"],
                   "text": ["doanh thu thuan", "thu nhap lai thuan", "tong doanh thu hoat dong"]},
    "gross":      {"item_id": ["gross_profit"], "text": ["loi nhuan gop"]},
    "net_income": {"item_id": ["attributable_to_parent_company", "profit_after_tax_for_shareholders_of_parent_company",
                                "net_profit_loss_after_tax", "net_profit"],
                   "text": ["loi nhuan sau thue cua co dong cua cong ty me", "loi nhuan cua co dong cua cong ty me", "loi nhuan sau thue"]},
    "assets":     {"item_id": ["total_assets"], "text": ["tong cong tai san", "tong tai san"]},
    "equity":     {"item_id": ["owners_equity_3", "owners_equity"], "text": ["von chu so huu"]},
    "paid_in":    {"item_id": ["paid_in_capital", "owners_capital", "common_shares"], "text": ["von gop", "von dieu le"]},
    "cur_assets": {"item_id": ["current_assets"], "text": ["tai san ngan han"]},
    "cur_liab":   {"item_id": ["current_liabilities"], "text": ["no ngan han"]},
}
PAR_VALUE = 10_000   # mệnh giá cổ phiếu VN (đồng) — dùng để suy ra số cổ phiếu từ vốn điều lệ


def _norm(x: str) -> str:
    import unicodedata
    x = unicodedata.normalize("NFD", str(x).lower().replace("đ", "d"))
    return "".join(c for c in x if unicodedata.category(c) != "Mn")


def _flat_cols(df):
    import pandas as pd
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = [" ".join(str(p) for p in c if str(p) and not str(p).startswith("Unnamed")) for c in df.columns]
    return df


import os
_REVERSE_YEAR_LABELS = os.getenv("VNSTOCK_REVERSE_YEAR_LABELS", "false").strip().lower() in ("1", "true", "yes")


def _year_pairs(df):
    """Trả về [(năm_đúng, tên_cột)]. NGUỒN KBS CÓ BUG ĐÃ XÁC NHẬN (test trực
    tiếp vnstock, không qua code này): vị trí cột đúng thứ tự thời gian thật,
    nhưng NHÃN năm in trên cột bị gán NGƯỢC so với số liệu bên dưới. Khi
    VNSTOCK_REVERSE_YEAR_LABELS=true (đặt trong .env), bỏ qua con số trong
    tên cột để ghép năm — chỉ dùng THỨ TỰ CỘT GỐC mà API trả về (đáng tin
    cậy hơn nhãn chữ)."""
    import re
    raw = []
    for c in df.columns:
        m = re.search(r"(19|20)\d{2}", str(c).strip())
        if m:
            raw.append((int(m.group()), c))
    if not raw:
        return []
    if _REVERSE_YEAR_LABELS:
        cols_in_order = [c for _, c in raw]          # thứ tự cột gốc, KHÔNG đảo
        years_ascending = sorted(y for y, _ in raw)
        return list(zip(years_ascending, cols_in_order))
    raw.sort(key=lambda x: x[0])
    return raw


def _year_cols(df):
    return [c for _, c in _year_pairs(df)]


def _find_row(df, key: str):
    """Tìm ĐÚNG 1 DÒNG (Series) khớp chỉ tiêu `key`. 1 vài nguồn (vd KBS) dùng
    TRÙNG item_id cho cả dòng TIÊU ĐỀ MỤC (không có số liệu) lẫn dòng TỔNG
    THẬT (có số liệu) — nếu lấy nhầm dòng tiêu đề sẽ ra "chưa đủ 2 năm BCTC".
    Khi trùng, lọc tiếp bằng từ khoá text để chọn đúng dòng có số liệu thật."""
    spec = KEYS[key]
    if "item_id" in df.columns:
        for iid in spec["item_id"]:
            hits = df[df["item_id"] == iid]
            if hits.empty:
                continue
            if len(hits) == 1:
                return hits.iloc[0]
            for col in ("item", "item_en"):
                if col not in hits.columns:
                    continue
                normed = hits[col].map(_norm)
                for kw in spec["text"]:
                    sub = hits[normed.str.contains(kw, na=False)]
                    if not sub.empty:
                        return sub.iloc[0]
            return hits.iloc[-1]
    for col in ("item", "item_en"):
        if col not in df.columns:
            continue
        normed = df[col].map(_norm)
        for kw in spec["text"]:
            hit = df[normed.str.contains(kw, na=False)]
            if not hit.empty:
                return hit.iloc[0]
    return None


def _series_from_row(df, row):
    if row is None:
        return None
    import pandas as pd
    pairs = _year_pairs(df)
    if not pairs:
        return None
    vals = pd.to_numeric(row[[c for _, c in pairs]], errors="coerce")
    s = pd.Series(list(vals), index=[y for y, _ in pairs])
    s = s[s.notna()]
    return s[~s.index.duplicated()].sort_index()


class VnstockFundamentalProvider(BaseFundamentalProvider):
    _vci_coverage_down_until = 0.0     # cầu dao tạm ngắt VietCap khi đang lỗi mạng
    """BCTC thật từ vnstock + giá hiện tại từ price_provider.
    Báo cáo CTCK/giá mục tiêu: đọc từ file CSV (REPORTS_CSV) vì vnstock KHÔNG có dữ liệu này."""

    def __init__(self, price_provider, source: str = "VCI", reports_csv: str = "data/broker_reports.csv"):
        self.price_provider = price_provider
        self.source = source
        self.reports_csv = reports_csv

    # ---- đọc 1 bảng BCTC năm — đi qua cổng giãn cách DÙNG CHUNG (app/utils/net.py)
    # để phối hợp đúng với các provider khác (giá, thông tin doanh nghiệp) cùng
    # gọi vnstock, tránh tổng tốc độ request vượt rate limit thực tế.
    def _throttled(self, fn):
        return vnstock_call(fn)

    def _statement(self, ticker: str, kind: str):
        def call():
            import sys
            import io
            _old_stdout = sys.stdout
            sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="ignore")
            try:
                try:
                    from vnstock import Finance
                    fin = Finance(symbol=ticker, source=self.source)
                except ImportError:
                    from vnstock import Vnstock
                    fin = Vnstock().stock(symbol=ticker, source=self.source).finance
                return getattr(fin, kind)(period="year", lang="vi")
            finally:
                sys.stdout = _old_stdout
        return _flat_cols(self._throttled(call))

    @staticmethod
    def _series(df, key):
        return _series_from_row(df, _find_row(df, key))

    @disk_cache(6 * 3600)
    def _raw_financials(self, ticker: str) -> dict:
        inc, bal = self._statement(ticker, "income_statement"), self._statement(ticker, "balance_sheet")
        rev, gross, ni = (self._series(inc, k) for k in ("revenue", "gross", "net_income"))
        assets, equity, paid, ca, cl = (self._series(bal, k) for k in ("assets", "equity", "paid_in", "cur_assets", "cur_liab"))
        missing = [n for n, v in dict(revenue=rev, net_income=ni, assets=assets, equity=equity).items() if v is None]
        if missing:
            raise ValueError(f"{ticker}: không tìm thấy cột {missing} — chạy scripts/inspect_vnstock.py và sửa KEYS")
        # đơn vị: nguồn trả đồng -> đổi sang tỷ đồng
        k = 1e9 if float(abs(assets).median()) > 1e8 else 1.0
        years = sorted(set(rev.index) & set(ni.index) & set(assets.index) & set(equity.index))[-4:]
        if len(years) < 2:
            raise ValueError(f"{ticker}: chưa đủ 2 năm BCTC")
        shares_mil = None
        if paid is not None and paid.get(years[-1]) and paid.get(years[-1]) > 0:
            paid_vnd = float(paid[years[-1]]) * (1.0 if k == 1e9 else 1e9)       # về đơn vị đồng
            shares_mil = paid_vnd / PAR_VALUE / 1e6                             # triệu cp
        rows = []
        for y in years:
            r_, n_ = float(rev[y]) / k, float(ni[y]) / k
            g_ = float(gross[y]) / k if gross is not None and y in gross.index and rev[y] else None
            rows.append(dict(
                year=int(y), revenue=round(r_), net_income=round(n_),
                equity=round(float(equity[y]) / k), assets=round(float(assets[y]) / k),
                gross_margin=round(g_ / r_ * 100, 1) if g_ is not None and r_ else None,
                eps=round(n_ * 1e9 / (shares_mil * 1e6)) if shares_mil else None,
            ))
        cur = None
        if ca is not None and cl is not None and years[-1] in ca.index and years[-1] in cl.index and cl[years[-1]]:
            cur = round(float(ca[years[-1]]) / float(cl[years[-1]]), 2)
        return dict(years=rows, shares_mil=round(shares_mil) if shares_mil else None, current_ratio=cur)

    # ---- bảng chỉ số ĐÃ TÍNH SẴN của nguồn (EPS 4 quý, BVPS, ROE/ROA bình quân, biên LN...) —
    # cùng cách tính với các trang như CafeF/Vietstock nên số khớp hơn tự tính từ BCTC.
    RATIO_IDS = dict(eps="trailing_eps", bvps="book_value_per_share_bvps", roe="roe", roa="roa",
                     net_margin="net_margin", gross_margin="gross_margin",
                     current_ratio="short_term_ratio", interest_coverage="interest_coverage",
                     dividend_yield="dividend_yield")

    @disk_cache(6 * 3600)
    def _raw_ratios(self, ticker: str) -> dict:
        import re
        period = os.getenv("RATIO_PERIOD", "year")          # "year" (mặc định) hoặc "quarter" (EPS TTM mới nhất)
        def call():
            import sys, io
            old = sys.stdout
            sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="ignore")
            try:
                from vnstock import Finance
                return Finance(symbol=ticker, source=self.source).ratio(period=period, lang="vi")
            finally:
                sys.stdout = old
        df = _flat_cols(self._throttled(call))
        if "item_id" not in df.columns:
            raise ValueError("bảng ratio không có cột item_id")
        cols = []
        for c in df.columns:
            m = re.search(r"((?:19|20)\d{2})\D*?(\d)?\b", str(c))
            if m and str(c) not in ("item", "item_id", "item_en"):
                cols.append(((int(m.group(1)), int(m.group(2) or 0)), c))
        if not cols:
            raise ValueError("bảng ratio không có cột năm")
        cols.sort()
        out = {"_period": str(cols[-1][1])}
        import pandas as pd
        for key, iid in self.RATIO_IDS.items():
            hit = df[df["item_id"] == iid]
            if hit.empty:
                continue
            for _, c in reversed(cols):                       # cột mới nhất có số liệu
                v = pd.to_numeric(hit.iloc[0][c], errors="coerce")
                if pd.notna(v) and v != 0:
                    out[key] = float(v)
                    break
        return out

    def get_financials(self, ticker: str) -> dict:
        ticker = ticker.upper()
        raw = self._raw_financials(ticker)
        shares_mil = raw["shares_mil"]                                 # suy từ vốn góp / mệnh giá (dễ lệch)
        # Ưu tiên SỐ CP LƯU HÀNH thật từ hồ sơ VCI; lệch > 5% so với suy ra thì dùng số thật.
        try:
            from app.data.company_provider import get_issue_shares
            iss = get_issue_shares(ticker)
        except Exception:
            iss = None
        if iss and iss > 0:
            shares_mil = iss / 1e6
        if not shares_mil:
            raise ValueError(f"{ticker}: thiếu số cổ phiếu để tính EPS/BVPS — không lấy được từ hồ sơ VCI lẫn vốn góp")
        price = self.price_provider.get_quote(ticker)["price"]        # nghìn đồng, real-time
        # EPS từng năm tính lại theo số CP đã chốt (net_income: tỷ đồng, shares_mil: triệu cp)
        years = [dict(y, eps=round(y["net_income"] * 1000 / shares_mil)) for y in raw["years"]]
        last = years[-1]
        ref = {}
        try:
            ref = self._raw_ratios(ticker)
        except Exception as e:                                          # không có bảng ratio -> tự tính từ BCTC
            import logging
            logging.getLogger(__name__).warning("ratio %s: %s (dùng số tự tính)", ticker, e)
        bvps = ref.get("bvps") or round(last["equity"] * 1000 / shares_mil)
        src = f"vnstock ({self.source})" + (f" + bảng chỉ số {ref['_period']}" if ref.get("eps") else "")
        return dict(
            ticker=ticker, sector=sector_of(ticker), shares_mil=round(shares_mil), price=price,
            bvps=round(bvps),                                           # đồng/cp
            eps_ttm=ref.get("eps"), ratio_ref={k: ref[k] for k in ("roe", "roa", "net_margin", "gross_margin") if k in ref},
            current_ratio=ref.get("current_ratio") or raw["current_ratio"],
            interest_coverage=ref.get("interest_coverage"), dividend_yield=ref.get("dividend_yield"),
            years=years, is_mock=False, data_source=src,
        )

    # ---- báo cáo CTCK: đọc CSV do bạn cập nhật
    def get_reports(self, ticker: str, price: float | None = None) -> list[dict]:
        import csv
        import os
        latest: dict[str, dict] = {}                 # mỗi CTCK chỉ lấy báo cáo MỚI NHẤT
        csv_rows = []
        if os.path.exists(self.reports_csv):
            with open(self.reports_csv, encoding="utf-8-sig", newline="") as f:
                csv_rows = list(csv.DictReader(f))
        for r in csv_rows:
            if (r.get("ticker") or "").strip().upper() != ticker.upper():
                continue
            try:
                tp = float(str(r["target_price"]).replace(",", "").strip())
            except (KeyError, ValueError):
                continue
            if tp > 1000:                          # nhập theo đồng -> đổi nghìn đồng
                tp /= 1000
            row = dict(broker=(r.get("broker") or "").strip(), date=(r.get("date") or "").strip(),
                       rating=(r.get("rating") or "").strip().upper(), target_price=round(tp, 2),
                       url=(r.get("url") or "").strip(), is_mock=False)
            row["broker"] = pretty_broker(row["broker"])
            key = broker_key(row["broker"])
            if key not in latest or row["date"] > latest[key]["date"]:
                latest[key] = row

        # Bổ sung TỰ ĐỘNG từ Simplize: gom sẵn ~10 CTCK/mã (KBSV, MBS, ACBS, Shinhan,
        # Mirae, VDS, DSC, FPTS, BSC, Vietcap...). Mỗi CTCK lấy báo cáo MỚI NHẤT; nếu
        # CSV (nhập tay) cũng có CTCK đó thì lấy bản nào ngày mới hơn.
        for row in _simplize_cached(ticker.upper()):
            k = broker_key(row["broker"])
            if k not in latest or row["date"] >= latest[k]["date"]:
                latest[k] = dict(row)

        # Bổ sung TỰ ĐỘNG, không cần CSV: VietCap (nguồn VCI) tự kèm sẵn 1
        # khuyến nghị phân tích nội bộ trong company.overview() — lấy miễn phí
        # qua chính vnstock. Best-effort: lỗi/không có dữ liệu thì bỏ qua êm,
        # không ảnh hưởng các báo cáo CSV đã nhập tay.
        if "vietcap" not in latest and time.time() >= VnstockFundamentalProvider._vci_coverage_down_until:
            try:
                from app.data.company_provider import get_company_profile
                # Giới hạn thời gian RIÊNG, chặt (4s) — nếu VCI không kết nối
                # được, bỏ qua nhanh, không kéo sập cả phần BCTC (nguồn khác,
                # vd KBS) và không làm nghẽn cổng giãn cách dùng chung, ảnh
                # hưởng tới Screener/các mã khác đang chờ lấy giá.
                ov = call_with_timeout(get_company_profile, ticker, timeout=8).get("overview") or {}
                tp = ov.get("target_price")
                if tp:
                    tp_k = float(tp) / 1000 if float(tp) > 1000 else float(tp)
                    date_raw = str(ov.get("rating_as_of") or "")
                    try:
                        import datetime
                        date_iso = datetime.datetime.strptime(date_raw, "%d-%b-%y").strftime("%Y-%m-%d")
                    except ValueError:
                        date_iso = date_raw
                    auto_key = "VietCap (tự động, vnstock)"
                    vci_url = "https://trading.vietcap.com.vn/iq?language=vi"
                    latest["vietcap"] = dict(broker=auto_key, date=date_iso,
                                             rating=str(ov.get("rating") or "").strip().upper(),
                                             target_price=round(tp_k, 2), url=vci_url, is_mock=False)
            except Exception:
                # VietCap đang lỗi (mạng/timeout) — TẠM NGỪNG thử lại trong 5
                # phút tới, tránh mỗi mã trong danh mục đều phải chờ hết 4s
                # thất bại riêng lẻ, dồn lại làm nghẽn cả hệ thống.
                VnstockFundamentalProvider._vci_coverage_down_until = time.time() + 300

        # Chỉ giữ REPORTS_MAX CTCK có báo cáo mới nhất (mặc định 6) cho gọn bảng.
        import os as _os
        max_n = int(_os.getenv("REPORTS_MAX", "6"))
        rows = sorted(latest.values(), key=lambda x: x["date"], reverse=True)
        # ưu tiên báo cáo trong 180 ngày gần đây (báo cáo quá cũ kéo giá mục tiêu bình quân xuống);
        # nếu còn < 3 báo cáo "tươi" thì giữ nguyên danh sách để bảng không bị trống
        cutoff = (dt.date.today() - dt.timedelta(days=180)).isoformat()
        fresh = [r for r in rows if r["date"] >= cutoff]
        return (fresh if len(fresh) >= 3 else rows)[:max_n]


def get_fundamental_provider(name: str = "mock", price_provider=None, source: str = "VCI",
                             reports_csv: str = "data/broker_reports.csv") -> BaseFundamentalProvider:
    name = (name or "mock").lower()
    if name == "mock":
        return MockFundamentalProvider()
    if name in ("vnstock", "real"):
        return VnstockFundamentalProvider(price_provider, source, reports_csv)
    raise ValueError(f"Unknown fundamental provider: {name}")

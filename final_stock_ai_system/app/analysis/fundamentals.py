"""Phân tích cơ bản: ROE/ROA/PEG..., so sánh ngành, luận điểm đầu tư,
tóm tắt định giá từ các CTCK. Toàn bộ rule-based, minh bạch."""
from __future__ import annotations
import logging
import statistics
from app.utils.fcache import ttl_cache
from app.utils.net import call_with_timeout
from app.data.fundamental_provider import peers_of, sector_of, _SECTOR_PROFILE

_log = logging.getLogger(__name__)

# key: (nhãn, đơn vị, True nếu CÀNG CAO CÀNG TỐT, nhóm)
# Khoảng hợp lý để 1 mã cùng ngành được tính vào TRUNG VỊ (ngoài khoảng = nhiều khả năng dữ liệu lỗi,
# ví dụ sai số cổ phiếu làm P/E 2,6x). Chỉnh số ở đây nếu muốn chặt/lỏng hơn. Áp dụng cho mọi mã.
PEER_SANE = {"pe": (3, 100), "pb": (0.3, 30), "roe": (-50, 80), "peg": (-50, 50)}

METRICS = {
    "pe": ("P/E", "x", False, "Định giá"),
    "pb": ("P/B", "x", False, "Định giá"),
    "peg": ("PEG", "x", False, "Định giá"),
    "roe": ("ROE", "%", True, "Khả năng sinh lời"),
    "roa": ("ROA", "%", True, "Khả năng sinh lời"),
    "net_margin": ("Biên LN ròng", "%", True, "Khả năng sinh lời"),
    "gross_margin": ("Biên LN gộp", "%", True, "Khả năng sinh lời"),
    "eps_growth": ("Tăng trưởng EPS (CAGR)", "%", True, "Tăng trưởng"),
    "revenue_growth": ("Tăng trưởng doanh thu (YoY)", "%", True, "Tăng trưởng"),
    "net_income_growth": ("Tăng trưởng LN ròng (YoY)", "%", True, "Tăng trưởng"),
    "de": ("Nợ/Vốn chủ (D/E)", "x", False, "Cấu trúc vốn & thanh khoản"),
    "current_ratio": ("Thanh toán hiện hành", "x", True, "Cấu trúc vốn & thanh khoản"),
}


def _ratios(fin: dict) -> dict:
    y = fin["years"]
    last, first = y[-1], y[0]
    price_d = fin["price"] * 1000                       # đồng
    if len(y) < 2:
        raise ValueError("Không đủ dữ liệu BCTC (cần tối thiểu 2 năm)")
    eps = fin.get("eps_ttm") or last["eps"] or None     # EPS công bố (4 quý) nếu có, giống CafeF
    pe = price_d / eps if eps else None
    ref = fin.get("ratio_ref") or {}
    n = len(y) - 1
    if first["eps"] and last["eps"] and first["eps"] > 0 and last["eps"] > 0:
        eps_growth = ((last["eps"] / first["eps"]) ** (1 / n) - 1) * 100
    elif first["net_income"] > 0 and last["net_income"] > 0:      # fallback khi thiếu EPS
        eps_growth = ((last["net_income"] / first["net_income"]) ** (1 / n) - 1) * 100
    else:
        eps_growth = None
    # Luôn tính PEG khi có đủ dữ liệu (kể cả tăng trưởng ÂM) — PEG âm KHÔNG có
    # nghĩa "định giá rẻ" như PEG dương thông thường (đang suy giảm, không
    # phải đang tăng trưởng). Được lọc khỏi trung vị ngành + đánh dấu cảnh
    # báo rõ ràng ở nơi hiển thị (xem _compare() và frontend).
    peg = pe / eps_growth if pe and eps_growth else None
    avg_eq = (last["equity"] + y[-2]["equity"]) / 2
    avg_as = (last["assets"] + y[-2]["assets"]) / 2
    return dict(
        roe=ref.get("roe") if ref.get("roe") is not None else last["net_income"] / avg_eq * 100,
        roa=ref.get("roa") if ref.get("roa") is not None else last["net_income"] / avg_as * 100,
        net_margin=ref.get("net_margin") if ref.get("net_margin") is not None else last["net_income"] / last["revenue"] * 100,
        gross_margin=ref.get("gross_margin") if ref.get("gross_margin") is not None else last.get("gross_margin"),
        eps_growth=eps_growth,
        pe=pe, pb=price_d / fin["bvps"], peg=peg,
        de=(last["assets"] - last["equity"]) / last["equity"],
        eps=eps, bvps=fin["bvps"],
        revenue_growth=(last["revenue"] / y[-2]["revenue"] - 1) * 100,
        net_income_growth=(last["net_income"] / y[-2]["net_income"] - 1) * 100,
        current_ratio=fin.get("current_ratio"),
        interest_coverage=fin.get("interest_coverage"),
        dividend_yield=fin.get("dividend_yield"),
    )


def _r(v, d=2):
    return None if v is None else round(v, d)


def _compare(ticker: str, ratios: dict, provider) -> dict:
    from concurrent.futures import ThreadPoolExecutor
    peers = peers_of(ticker)[:2]            # 2 mã cùng ngành: đủ để so sánh, ít request (vnstock giới hạn ~20 request/phút)

    failed: dict[str, str] = {}

    def one(p):
        try:
            # timeout riêng cho từng mã cùng ngành: 1 mã bị treo (mạng chậm, vnstock
            # không phản hồi) không được kéo treo luôn toàn bộ trang chi tiết.
            # 60s (trước là 25s) vì lần đầu mỗi mã cần ~3 request vnstock bị giãn cách 1,1s
            # và có thể dính chờ rate limit; kết quả được cache đĩa nên các lần sau rất nhanh.
            fin_p = call_with_timeout(provider.get_financials, p, timeout=60)
            return dict(ticker=p, **{k: _r(_ratios(fin_p)[k]) for k in METRICS})
        except Exception as e:                # 1 mã lỗi dữ liệu thì bỏ qua, không vỡ cả trang
            failed[p] = f"{type(e).__name__}: {e}"
            _log.warning("So sánh ngành: bỏ qua %s (%s)", p, failed[p])
            return None

    with ThreadPoolExecutor(max_workers=max(1, min(3, len(peers)))) as pool:
        peer_rows = [r for r in pool.map(one, peers) if r]
    # Không tải được mã cùng ngành nào -> dùng CHUẨN NGÀNH ước tính (_SECTOR_PROFILE) cho các
    # chỉ số có trong bảng đó, để bảng + định giá nội bộ không trống trơn. Có đánh dấu rõ.
    use_benchmark = not peer_rows
    bench = {}
    if use_benchmark:
        roe0, margin0, de0, growth0, pb_fair = _SECTOR_PROFILE.get(sector_of(ticker), _SECTOR_PROFILE["Khác"])
        bench = dict(roe=roe0, net_margin=margin0, de=de0, eps_growth=growth0, pb=pb_fair)
    comp = []
    for k, (label, unit, higher_better, group) in METRICS.items():
        vals = [row[k] for row in peer_rows if row[k] is not None]
        lo_hi = PEER_SANE.get(k)
        if lo_hi:
            kept = [v for v in vals if lo_hi[0] <= v <= lo_hi[1]]
            if len(kept) != len(vals):
                _log.warning("So sánh ngành %s: loại giá trị bất thường của %s khỏi trung vị: %s",
                             ticker, k, [v for v in vals if v not in kept])
            vals = kept
        if use_benchmark and k in bench:
            vals = [bench[k]]
        mine = ratios[k]
        peg_negative = k == "peg" and mine is not None and mine <= 0
        if k == "peg":
            # PEG âm (từ tăng trưởng âm) không có ý nghĩa "rẻ/đắt" như PEG
            # dương thông thường -> loại khỏi trung vị ngành VÀ khỏi phép so
            # sánh. Giá trị thật vẫn hiển thị ở "value" (không phải lỗi, chỉ
            # là không đưa vào so sánh) — đánh dấu rõ bằng "note".
            vals = [v for v in vals if v > 0]
        med = statistics.median(vals) if vals else None
        mine_cmp = None if peg_negative else mine
        verdict = None
        if med is not None and mine_cmp is not None:
            better = mine_cmp >= med if higher_better else mine_cmp <= med
            verdict = "Tốt hơn ngành" if better else "Kém hơn ngành"
        comp.append(dict(key=k, label=label, unit=unit, group=group, value=_r(mine), sector_median=_r(med),
                         higher_better=higher_better, verdict=verdict,
                         note=("Tăng trưởng âm — không so sánh PEG" if peg_negative else
                               ("Không tính được: EPS năm gốc ≤ 0 (lỗ) nên không có tốc độ tăng trưởng" if k == "peg" and mine is None else None))))
    groups = []
    for g in dict.fromkeys(m["group"] for m in comp):
        items = [m for m in comp if m["group"] == g]
        judged = [m for m in items if m["verdict"]]
        good = sum(1 for m in judged if m["verdict"] == "Tốt hơn ngành")
        if not judged:
            note = "Chưa đủ dữ liệu so sánh"
        else:
            pct = good / len(judged)
            note = "Tốt" if pct >= 0.67 else "Trung bình" if pct >= 0.34 else "Yếu"
        groups.append(dict(name=g, good=good, total=len(judged), note=note))
    return dict(peers=[r["ticker"] for r in peer_rows], peer_rows=peer_rows, metrics=comp, groups=groups,
                benchmark=use_benchmark, failed=failed)


def _swot(ticker: str, r: dict, comp: dict, sector: str = "") -> dict:
    is_bank = sector == "Ngân hàng"
    strengths, weaknesses = [], []
    v = {m["key"]: m for m in comp["metrics"]}

    def beat(k): return v[k]["verdict"] == "Tốt hơn ngành"

    if r["roe"] is not None and r["roe"] >= 15 and beat("roe"):
        strengths.append(f"ROE {r['roe']:.1f}% cao, vượt trung vị ngành ({v['roe']['sector_median']}%) — sử dụng vốn chủ hiệu quả.")
    elif r["roe"] is not None and r["roe"] < 10:
        weaknesses.append(f"ROE chỉ {r['roe']:.1f}% — hiệu quả sinh lời trên vốn chủ thấp.")
    if r["net_income_growth"] is None:
        pass
    elif r["net_income_growth"] > 12:
        strengths.append(f"Lợi nhuận ròng tăng {r['net_income_growth']:.1f}% so với năm trước.")
    elif r["net_income_growth"] < 0:
        weaknesses.append(f"Lợi nhuận ròng giảm {abs(r['net_income_growth']):.1f}% so với năm trước.")
    if v["net_margin"]["verdict"] is None:
        pass
    elif beat("net_margin"):
        strengths.append(f"Biên LN ròng {r['net_margin']:.1f}% tốt hơn mặt bằng ngành.")
    else:
        weaknesses.append(f"Biên LN ròng {r['net_margin']:.1f}% thấp hơn trung vị ngành.")
    if r["peg"] is not None and r["peg"] < 0:
        weaknesses.append(f"PEG {r['peg']:.2f} âm do EPS đang giảm — không phản ánh 'rẻ', cần thận trọng.")
    elif r["peg"] is not None and 0 < r["peg"] < 1:
        strengths.append(f"PEG {r['peg']:.2f} < 1 — giá chưa phản ánh hết tốc độ tăng trưởng.")
    elif r["peg"] is not None and r["peg"] > 2:
        weaknesses.append(f"PEG {r['peg']:.2f} > 2 — định giá cao so với tăng trưởng.")
    if r["pe"] is None:
        pass
    elif v["pe"]["verdict"] == "Kém hơn ngành" and v["pb"]["verdict"] == "Kém hơn ngành":
        weaknesses.append("P/E và P/B đều cao hơn trung vị ngành — biên an toàn về giá mỏng.")
    elif beat("pe") and beat("pb"):
        strengths.append("P/E và P/B đều thấp hơn trung vị ngành — định giá hấp dẫn tương đối.")
    if r["current_ratio"] is not None and not is_bank:
        if r["current_ratio"] < 1:
            weaknesses.append(f"Thanh toán hiện hành {r['current_ratio']} < 1 — áp lực thanh khoản ngắn hạn.")
        elif r["current_ratio"] >= 1.5:
            strengths.append(f"Thanh toán hiện hành {r['current_ratio']} — thanh khoản lành mạnh.")
    if is_bank or v["de"]["verdict"] is None:      # ngân hàng: đòn bẩy cao là đặc thù, không xét
        pass
    elif not beat("de"):
        weaknesses.append(f"Đòn bẩy D/E {r['de']:.2f} cao hơn trung vị ngành.")
    else:
        strengths.append(f"Đòn bẩy D/E {r['de']:.2f} thấp hơn trung vị ngành.")
    return dict(strengths=strengths or ["Chưa có điểm nổi bật rõ rệt."],
                weaknesses=weaknesses or ["Chưa phát hiện điểm yếu đáng kể."])


def _score(comp: dict) -> tuple[int, int]:
    good = sum(1 for m in comp["metrics"] if m["verdict"] == "Tốt hơn ngành")
    total = sum(1 for m in comp["metrics"] if m["verdict"])
    return good, total


def _thesis(ticker: str, sector: str, r: dict, comp: dict, valuation: dict) -> dict:
    good, total = _score(comp)
    ratio = good / total if total else 0
    up = valuation.get("avg_upside_pct")
    if ratio >= 0.65 and (up is None or up > 8):
        verdict, tone = "Hấp dẫn", "up"
    elif ratio <= 0.35 or (up is not None and up < -3):
        verdict, tone = "Thận trọng", "down"
    else:
        verdict, tone = "Trung lập", "flat"
    growth_txt = (f"tăng trưởng EPS ~{r['eps_growth']:.1f}%/năm" if r["eps_growth"] is not None
                  else "tăng trưởng EPS chưa tính được (EPS năm gốc âm/thiếu)")
    text = (f"{ticker} thuộc ngành {sector}, hơn trung vị ngành ở {good}/{total} chỉ số. "
            f"ROE {r['roe']:.1f}%, {growth_txt}"
            + (f", P/E {r['pe']:.1f}x." if r["pe"] else "."))
    if up is not None:
        text += f" Giá mục tiêu bình quân của các CTCK cho mức {'tăng' if up >= 0 else 'giảm'} {abs(up):.1f}% so với giá hiện tại."
    return dict(verdict=verdict, tone=tone, text=text, score=f"{good}/{total}")


def _overview_table(ticker: str, sector: str, fin: dict, r: dict, thesis: dict, valuation: dict) -> list[dict]:
    """1 bảng DUY NHẤT tổng hợp mọi thứ ở trên (chỉ số, so ngành ngầm định qua
    luận điểm, định giá CTCK) — giống khung "Chỉ số tài chính" tóm tắt kiểu
    CafeF để xem nhanh không cần kéo hết trang."""
    market_cap = None
    if fin.get("shares_mil") and fin.get("price"):
        market_cap = round(fin["shares_mil"] * fin["price"] / 1000, 1)     # tỷ đồng (giá tính theo nghìn đồng)
    rows = [
        dict(label="Mã cổ phiếu", value=ticker),
        dict(label="Ngành", value=sector),
        dict(label="Giá hiện tại (nghìn đồng)", value=_r(fin.get("price"))),
        dict(label="Vốn hoá ước tính (tỷ đồng)", value=_r(market_cap, 0)),
        dict(label="P/E", value=_r(r.get("pe"))),
        dict(label="P/B", value=_r(r.get("pb"))),
        dict(label="PEG", value=_r(r.get("peg"))),
        dict(label="ROE (%)", value=_r(r.get("roe"))),
        dict(label="ROA (%)", value=_r(r.get("roa"))),
        dict(label="EPS (đồng/cp)", value=_r(r.get("eps"), 0)),
        dict(label="Biên LN ròng (%)", value=_r(r.get("net_margin"))),
        dict(label="Tăng trưởng LN ròng YoY (%)", value=_r(r.get("net_income_growth"))),
        dict(label="Tăng trưởng doanh thu YoY (%)", value=_r(r.get("revenue_growth"))),
        dict(label="Nợ/Vốn chủ (D/E)", value=_r(r.get("de"))),
        dict(label="Giá mục tiêu bình quân CTCK (nghìn đồng)", value=valuation.get("avg_target")),
        dict(label="Chênh lệch so với giá hiện tại (%)", value=valuation.get("avg_upside_pct")),
        dict(label="Số CTCK đã định giá", value=valuation.get("count")),
        dict(label="Điểm so với trung vị ngành", value=thesis.get("score")),
        dict(label="Luận điểm đầu tư", value=thesis.get("verdict")),
    ]
    return rows


def _valuation(fin: dict, comp: dict, reports: list[dict]) -> dict:
    price = fin["price"]
    rows = []
    for i, rp in enumerate(reports, 1):
        rows.append(dict(stt=i, **rp, upside_pct=round((rp["target_price"] / price - 1) * 100, 1)))
    tps = [rp["target_price"] for rp in reports]
    out = dict(rows=rows, price=price, count=len(rows))
    if tps:
        avg = statistics.mean(tps)
        out.update(avg_target=round(avg, 2), median_target=round(statistics.median(tps), 2),
                   high_target=max(tps), low_target=min(tps),
                   avg_upside_pct=round((avg / price - 1) * 100, 1))
    # định giá nội bộ: trung vị P/E, P/B ngành × EPS, BVPS
    v = {m["key"]: m for m in comp["metrics"]}
    est = []
    eps_now = fin.get("eps_ttm") or fin["years"][-1]["eps"]
    if v["pe"]["sector_median"] and eps_now:
        est.append(v["pe"]["sector_median"] * eps_now / 1000)
    if v["pb"]["sector_median"]:
        est.append(v["pb"]["sector_median"] * fin["bvps"] / 1000)
    if est:
        fv = statistics.mean(est)
        out.update(own_fair_value=round(fv, 2), own_upside_pct=round((fv / price - 1) * 100, 1))
    return out


@ttl_cache(300)
def analyze_fundamental(ticker: str, provider) -> dict:
    ticker = ticker.upper()
    # timeout riêng cho mã chính: tránh 1 request HTTP bị treo vô thời hạn khi
    # vnstock không phản hồi (nguyên nhân phổ biến của "Failed to fetch" phía
    # trình duyệt — trình duyệt tự bỏ cuộc sau một lúc trong khi server thì vẫn
    # đang chờ mãi mãi).
    fin = call_with_timeout(provider.get_financials, ticker, timeout=120)
    r = _ratios(fin)
    comp = _compare(ticker, r, provider)
    reports = call_with_timeout(provider.get_reports, ticker, fin["price"], timeout=20)
    valuation = _valuation(fin, comp, reports)
    swot = _swot(ticker, r, comp, fin["sector"])
    thesis = _thesis(ticker, fin["sector"], r, comp, valuation)
    overview = _overview_table(ticker, fin["sector"], fin, r, thesis, valuation)
    return dict(
        ticker=ticker, sector=fin["sector"], price=fin["price"], is_mock=fin.get("is_mock", False),
        data_source=fin.get("data_source", "Mô phỏng (mock)"),
        years=fin["years"],
        ratios={k: _r(v) for k, v in r.items()},
        comparison=comp, swot=swot, thesis=thesis, valuation=valuation,
        overview=overview,
    )

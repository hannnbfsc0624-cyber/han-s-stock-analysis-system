from flask import Blueprint, jsonify, request, current_app

from app.analysis.screener import analyze_ticker, screen_universe
from app import db as dbmod
from app.config import (
    DEFAULT_WATCHLIST, PRICE_PROVIDER, NEWS_PROVIDER, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    USE_GEMINI, GEMINI_API_KEY,
)

api_bp = Blueprint("api", __name__, url_prefix="/api")


def _providers():
    return current_app.config["PRICE_PROVIDER"], current_app.config["NEWS_PROVIDER"]


def _current_watchlist() -> list[str]:
    """Watchlist ưu tiên lấy từ DB (người dùng tự thêm/xoá); nếu DB trống thì
    gieo sẵn từ DEFAULT_WATCHLIST trong config."""
    dbmod.seed_watchlist_if_empty(DEFAULT_WATCHLIST)
    return dbmod.get_watchlist()


@api_bp.get("/health")
def health():
    return jsonify(status="ok")


@api_bp.get("/config")
def config_info():
    """Thông tin cấu hình hiện tại (không lộ secret) — hiển thị ở mục Cài đặt."""
    return jsonify(
        price_provider=PRICE_PROVIDER,
        news_provider=NEWS_PROVIDER,
        telegram_configured=bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID),
        default_watchlist=DEFAULT_WATCHLIST,
        gemini_enabled=USE_GEMINI and bool(GEMINI_API_KEY),
        gemini_use=USE_GEMINI, gemini_key=bool(GEMINI_API_KEY),
        gemini_auto=__import__("app.config", fromlist=["GEMINI_AUTO"]).GEMINI_AUTO,
        vnstock_source=__import__("app.config", fromlist=["VNSTOCK_SOURCE"]).VNSTOCK_SOURCE,
        fundamental_provider=current_app.config["FUNDAMENTAL_PROVIDER"].__class__.__name__,
    )


@api_bp.get("/cache/stats")
def cache_stats():
    from app.utils.cache import cache_stats_all
    return jsonify(cache_stats_all())


@api_bp.post("/cache/clear")
def cache_clear():
    from app.utils.cache import clear_all_caches
    clear_all_caches()
    return jsonify(status="cleared")


@api_bp.get("/watchlist")
def get_watchlist():
    return jsonify(data=_current_watchlist())


@api_bp.post("/watchlist")
def add_watchlist():
    body = request.get_json(silent=True) or {}
    ticker = (body.get("ticker") or "").strip().upper()
    if not ticker:
        return jsonify(error="Thiếu 'ticker'"), 400
    dbmod.add_to_watchlist(ticker)
    return jsonify(data=_current_watchlist())


@api_bp.delete("/watchlist/<ticker>")
def delete_watchlist(ticker: str):
    dbmod.remove_from_watchlist(ticker)
    return jsonify(data=_current_watchlist())


@api_bp.get("/screener")
def screener():
    """Bảng tổng hợp nhiều mã — giống bảng Live Screener trong ảnh."""
    tickers_param = request.args.get("tickers")
    tickers = tickers_param.split(",") if tickers_param else _current_watchlist()
    price_provider, news_provider = _providers()
    rows = screen_universe([t.strip().upper() for t in tickers if t.strip()], price_provider, news_provider)
    return jsonify(data=rows)


@api_bp.get("/market/overview")
def market_overview():
    """Tổng quan thị trường cho danh mục theo dõi: số mã tăng/giảm/đứng giá,
    phân bổ sentiment tin tức, top tăng/giảm — dùng cho Dashboard summary."""
    tickers_param = request.args.get("tickers")
    tickers = tickers_param.split(",") if tickers_param else _current_watchlist()
    price_provider, news_provider = _providers()
    rows = screen_universe([t.strip().upper() for t in tickers if t.strip()], price_provider, news_provider)

    gainers = [r for r in rows if r["change_pct"] > 0]
    losers = [r for r in rows if r["change_pct"] < 0]
    unchanged = [r for r in rows if r["change_pct"] == 0]

    sentiment_bias = dict(positive=0, negative=0, neutral=0)
    for r in rows:
        s = r["sentiment_net"]
        if s > 0.15:
            sentiment_bias["positive"] += 1
        elif s < -0.15:
            sentiment_bias["negative"] += 1
        else:
            sentiment_bias["neutral"] += 1

    strong_buy_count = sum(1 for r in rows if r["trend"] == "MUA MẠNH")
    strong_sell_count = sum(1 for r in rows if r["trend"] == "BÁN MẠNH")

    top_gainers = sorted(gainers, key=lambda r: r["change_pct"], reverse=True)[:5]
    top_losers = sorted(losers, key=lambda r: r["change_pct"])[:5]

    return jsonify(
        total=len(rows),
        gainers=len(gainers),
        losers=len(losers),
        unchanged=len(unchanged),
        strong_buy_count=strong_buy_count,
        strong_sell_count=strong_sell_count,
        sentiment_bias=sentiment_bias,
        top_gainers=top_gainers,
        top_losers=top_losers,
        rows=rows,
    )


@api_bp.get("/news-feed")
def news_feed():
    """Gộp tin tức (đã phân loại nguồn + sentiment) của nhiều mã thành 1 dòng
    thời gian chung — dùng cho mục 'Tin tức & Sentiment'."""
    from app.analysis.sentiment import classify_batch

    tickers_param = request.args.get("tickers")
    tickers = tickers_param.split(",") if tickers_param else _current_watchlist()
    limit_per_ticker = int(request.args.get("limit_per_ticker", 6))
    _, news_provider = _providers()

    all_news = []
    for t in [t.strip().upper() for t in tickers if t.strip()]:
        raw = news_provider.get_news(t, limit=limit_per_ticker)
        all_news.extend(classify_batch(raw))

    all_news.sort(key=lambda n: n.get("published_at") or "", reverse=True)
    return jsonify(data=all_news)


@api_bp.post("/copilot")
def copilot_ask():
    from app.analysis.copilot import answer

    body = request.get_json(silent=True) or {}
    message = (body.get("message") or "").strip()
    if not message:
        return jsonify(error="Thiếu 'message'"), 400
    price_provider, news_provider = _providers()
    result = answer(message, _current_watchlist(), price_provider, news_provider)
    return jsonify(result)

@api_bp.get("/company/<ticker>")
def company_info(ticker: str):
    from app.data.company_provider import get_company_profile
    return jsonify(get_company_profile(ticker.upper()))
@api_bp.get("/analyze/<ticker>")
def analyze(ticker: str):
    """Phân tích đầy đủ 1 mã: kỹ thuật + tin tức đã phân loại nguồn/sentiment."""
    ticker = ticker.upper()
    price_provider, news_provider = _providers()
    result = analyze_ticker(ticker, price_provider, news_provider, news_limit=10)

    # lưu lịch sử để phục vụ backtest / xem lại
    dbmod.save_news(ticker, result["news"])
    dbmod.save_signal(ticker, result["technical_signal"], result["quote"]["price"])

    return jsonify(result)


@api_bp.get("/fundamental/<ticker>")
def fundamental(ticker: str):
    """Chỉ số tài chính, so sánh ngành, luận điểm/điểm mạnh-yếu, bảng định giá CTCK."""
    from app.analysis.fundamentals import analyze_fundamental
    return jsonify(analyze_fundamental(ticker.upper(), current_app.config["FUNDAMENTAL_PROVIDER"]))


@api_bp.get("/ai/<ticker>")
def ai_recommendation(ticker: str):
    """Khuyến nghị bằng Gemini — gọi NỀN sau khi trang đã hiện (không chặn giao diện)."""
    from app.config import USE_GEMINI
    if not USE_GEMINI:
        return jsonify(enabled=False, text=None)
    ticker = ticker.upper()
    price_provider, news_provider = _providers()
    r = analyze_ticker(ticker, price_provider, news_provider, news_limit=10)
    try:
        from app.analysis.gemini_client import generate_recommendation_gemini
        text = generate_recommendation_gemini(ticker, r["technical_signal"], [n["title"] for n in r["news"]],
                                              r["sentiment_summary"]["net_score"])
        return jsonify(enabled=True, text=text)
    except Exception as e:
        return jsonify(enabled=True, text=None, error=str(e))


@api_bp.get("/ai-report/<ticker>")
def ai_report(ticker: str):
    """Báo cáo phân tích do Gemini viết từ SỐ LIỆU THẬT đã tính (không để AI tự bịa số)."""
    import json
    from app.config import USE_GEMINI
    from app.analysis.fundamentals import analyze_fundamental
    if not USE_GEMINI:
        return jsonify(enabled=False, report=None)
    ticker = ticker.upper()
    d = analyze_fundamental(ticker, current_app.config["FUNDAMENTAL_PROVIDER"])
    facts = dict(ticker=ticker, sector=d["sector"], price_nghin_dong=d["price"], ratios=d["ratios"],
                 so_voi_nganh=[{k: m[k] for k in ("label", "value", "sector_median", "verdict")} for m in d["comparison"]["metrics"]],
                 dinh_gia_ctck=dict(gia_muc_tieu_binh_quan=d["valuation"].get("avg_target"),
                                    upside_pct=d["valuation"].get("avg_upside_pct"), so_ctck=d["valuation"]["count"]),
                 ket_qua_kinh_doanh=d["years"])
    try:
        from app.analysis.gemini_client import generate_fundamental_report_gemini
        return jsonify(enabled=True, report=generate_fundamental_report_gemini(ticker, json.dumps(facts, ensure_ascii=False)))
    except Exception as e:
        return jsonify(enabled=True, report=None, error=str(e))


@api_bp.get("/news/<ticker>")
def news(ticker: str):
    ticker = ticker.upper()
    _, news_provider = _providers()
    limit = int(request.args.get("limit", 10))
    from app.analysis.sentiment import classify_batch
    raw = news_provider.get_news(ticker, limit=limit)
    return jsonify(data=classify_batch(raw))


@api_bp.get("/quote/<ticker>")
def quote(ticker: str):
    ticker = ticker.upper()
    price_provider, _ = _providers()
    return jsonify(data=price_provider.get_quote(ticker))


@api_bp.get("/history/signals/<ticker>")
def signal_history(ticker: str):
    return jsonify(data=dbmod.get_signal_history(ticker.upper()))


@api_bp.get("/history/news/<ticker>")
def news_history(ticker: str):
    return jsonify(data=dbmod.get_news_history(ticker.upper()))
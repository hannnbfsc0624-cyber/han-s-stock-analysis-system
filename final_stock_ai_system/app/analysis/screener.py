"""Screener tổng hợp: với mỗi mã, gộp (1) tín hiệu kỹ thuật, (2) tin tức mới nhất
đã phân loại nguồn + sentiment, thành 1 báo cáo đầy đủ.

Bản này tối ưu cho DỮ LIỆU THẬT (vnstock/google_news, tốn thời gian gọi mạng):
  - Chỉ gọi get_ohlcv 1 LẦN cho mỗi mã, tự tính quote từ dữ liệu đó thay vì
    gọi thêm get_quote() riêng (giảm 1/3 số lệnh gọi mạng).
  - Có timeout cho mỗi lệnh gọi mạng, tránh treo vô thời hạn.
  - screen_universe() chạy SONG SONG nhiều mã cùng lúc (ThreadPoolExecutor)
    thay vì tuần tự — nhanh hơn nhiều lần.
  - 1 mã bị lỗi/timeout KHÔNG làm sập cả bảng — chỉ mã đó hiện dòng lỗi.
"""
from __future__ import annotations
import math
import concurrent.futures
from app.analysis.indicators import enrich_with_indicators, detect_signal
from app.analysis.sentiment import classify_batch
from app.utils.fcache import ttl_cache
from app.utils.net import call_with_timeout as _call_with_timeout


def _quote_from_ohlcv(ticker: str, df) -> dict:
    """Tự tính quote (giá/% thay đổi/khối lượng) từ dữ liệu OHLCV đã có sẵn,
    KHÔNG gọi thêm 1 lệnh mạng riêng nữa (trước đây gọi get_quote() tốn thêm
    1 lệnh mạng/mã)."""
    if len(df) < 2:
        raise ValueError(f"Không đủ dữ liệu giá cho mã {ticker} (chỉ có {len(df)} phiên).")
    prev, last = df.iloc[-2], df.iloc[-1]
    change_pct = (last["close"] - prev["close"]) / prev["close"] * 100
    return dict(
        ticker=ticker,
        price=float(last["close"]),
        change_pct=round(float(change_pct), 2),
        volume=int(last["volume"]),
    )


def _ohlcv_to_json_safe(enriched_df) -> list[dict]:
    records = enriched_df.to_dict(orient="records")
    out = []
    for r in records:
        clean = {}
        for k, v in r.items():
            if hasattr(v, "isoformat"):
                clean[k] = v.isoformat()
            elif isinstance(v, float) and math.isnan(v):
                clean[k] = None
            else:
                clean[k] = v
        out.append(clean)
    return out


def _investment_plan(enriched_df, quote: dict, signal: dict, net_sentiment: float) -> dict:
    price = quote["price"]
    closes = enriched_df["close"].tail(20)
    pct_changes = closes.pct_change().dropna()
    volatility = float(pct_changes.std()) if len(pct_changes) > 1 else 0.02
    volatility = max(0.012, min(volatility, 0.08))

    strength = signal.get("strength", "neutral")

    if strength == "strong_buy":
        action = "CÂN NHẮC MUA"
        entry_low, entry_high = price * 0.99, price * 1.01
        target = price * (1 + volatility * 6)
        stoploss = price * (1 - volatility * 2.5)
        confidence = "Cao" if net_sentiment > 0.15 else "Trung bình"
    elif strength == "buy":
        action = "THEO DÕI ĐỂ MUA"
        entry_low, entry_high = price * 0.985, price * 1.0
        target = price * (1 + volatility * 4)
        stoploss = price * (1 - volatility * 3)
        confidence = "Trung bình" if net_sentiment >= 0 else "Thấp"
    elif strength == "strong_sell":
        action = "TRÁNH MUA / CÂN NHẮC BÁN"
        entry_low, entry_high = None, None
        target = None
        stoploss = price * (1 - volatility * 2)
        confidence = "Cao" if net_sentiment < -0.15 else "Trung bình"
    elif strength == "sell":
        action = "HẠN CHẾ GIẢI NGÂN MỚI"
        entry_low, entry_high = None, None
        target = None
        stoploss = price * (1 - volatility * 2.5)
        confidence = "Trung bình"
    else:
        action = "QUAN SÁT THÊM"
        entry_low, entry_high = price * 0.98, price * 1.02
        target = price * (1 + volatility * 3)
        stoploss = price * (1 - volatility * 3)
        confidence = "Thấp"

    def _r(v):
        return round(v, 2) if v is not None else None

    risk_reward = None
    if target is not None and stoploss is not None and price > stoploss:
        potential_gain = target - price
        potential_loss = price - stoploss
        if potential_loss > 0:
            risk_reward = round(potential_gain / potential_loss, 2)

    return dict(
        action=action,
        entry_zone=[_r(entry_low), _r(entry_high)] if entry_low else None,
        target_price=_r(target),
        stoploss_price=_r(stoploss),
        risk_reward_ratio=risk_reward,
        volatility_pct=round(volatility * 100, 2),
        confidence=confidence,
        disclaimer=(
            "Đây là kế hoạch tham khảo tự động tạo từ dữ liệu kỹ thuật + tin tức, "
            "KHÔNG phải khuyến nghị đầu tư chính thức. Vui lòng tự đánh giá rủi ro "
            "hoặc tham khảo chuyên gia trước khi ra quyết định."
        ),
    )


@ttl_cache(45)
def analyze_ticker(ticker: str, price_provider, news_provider, news_limit: int = 8) -> dict:
    ohlcv = _call_with_timeout(price_provider.get_ohlcv, ticker, days=120, timeout=20)
    enriched = enrich_with_indicators(ohlcv)
    signal = detect_signal(enriched)
    quote = _quote_from_ohlcv(ticker, enriched)  # không gọi mạng lần 2 nữa

    raw_news = _call_with_timeout(news_provider.get_news, ticker, limit=news_limit, timeout=15)
    news_with_sentiment = classify_batch(raw_news, text_field="title")

    by_source: dict[str, int] = {}
    sentiment_counts = {"Tích cực": 0, "Tiêu cực": 0, "Trung lập": 0}
    for n in news_with_sentiment:
        by_source[n["source"]] = by_source.get(n["source"], 0) + 1
        sentiment_counts[n["sentiment"]["label"]] += 1

    total_news = len(news_with_sentiment) or 1
    net_sentiment_score = round(
        (sentiment_counts["Tích cực"] - sentiment_counts["Tiêu cực"]) / total_news, 2
    )

    # Khuyến nghị rule-based trả NGAY; bản Gemini (chậm 3-20s) tải nền qua /api/ai/<ticker>
    combined_note = _combine_technical_and_sentiment(signal, net_sentiment_score)
    investment_plan = _investment_plan(enriched, quote, signal, net_sentiment_score)

    return dict(
        ticker=ticker,
        quote=quote,
        technical_signal=signal,
        news=news_with_sentiment,
        news_by_source=by_source,
        sentiment_summary=dict(counts=sentiment_counts, net_score=net_sentiment_score),
        combined_recommendation=combined_note,
        investment_plan=investment_plan,
        ohlcv=_ohlcv_to_json_safe(enriched.tail(90)),
    )


def _combine_technical_and_sentiment(signal: dict, net_sentiment: float) -> str:
    tech_positive = signal.get("strength") in ("strong_buy", "buy")
    tech_negative = signal.get("strength") in ("strong_sell", "sell")

    if tech_positive and net_sentiment > 0.2:
        return "Đồng thuận TÍCH CỰC: cả kỹ thuật và tin tức đều ủng hộ xu hướng tăng."
    if tech_negative and net_sentiment < -0.2:
        return "Đồng thuận TIÊU CỰC: cả kỹ thuật và tin tức đều cho tín hiệu xấu, cân nhắc thận trọng."
    if tech_positive and net_sentiment < -0.2:
        return "PHÂN KỲ: kỹ thuật tích cực nhưng tin tức tiêu cực — nên theo dõi thêm trước khi vào lệnh."
    if tech_negative and net_sentiment > 0.2:
        return "PHÂN KỲ: kỹ thuật tiêu cực nhưng tin tức tích cực — có thể là tín hiệu sớm, cần xác nhận thêm."
    return "Chưa đủ tín hiệu rõ ràng, khuyến nghị theo dõi thêm."

def _generate_recommendation(ticker: str, signal: dict, news_with_sentiment: list[dict], net_sentiment: float) -> str:
    """Ưu tiên Gemini (nếu USE_GEMINI=true), fallback rule-based nếu Gemini
    tắt/lỗi/hết quota. Không bao giờ raise lỗi ra ngoài."""
    from app.config import USE_GEMINI, GEMINI_AUTO
    if USE_GEMINI and GEMINI_AUTO:
        try:
            from app.analysis.gemini_client import generate_recommendation_gemini
            titles = [n["title"] for n in news_with_sentiment]
            return generate_recommendation_gemini(ticker, signal, titles, net_sentiment)
        except Exception:
            pass
    return _combine_technical_and_sentiment(signal, net_sentiment)


def _screen_one(ticker: str, price_provider, news_provider) -> dict:
    try:
        result = analyze_ticker(ticker, price_provider, news_provider, news_limit=5)
        return dict(
            ticker=ticker,
            price=result["quote"]["price"],
            change_pct=result["quote"]["change_pct"],
            volume=result["quote"]["volume"],
            trend=result["technical_signal"]["trend"],
            sentiment_net=result["sentiment_summary"]["net_score"],
            recommendation=result["combined_recommendation"],
            error=None,
        )
    except Exception as e:
        # 1 mã lỗi (vd sai mã, nguồn dữ liệu chậm/treo) KHÔNG được làm sập cả bảng
        return dict(
            ticker=ticker, price=None, change_pct=0, volume=None, trend="LỖI",
            sentiment_net=0, recommendation=f"Không tải được dữ liệu: {e}", error=str(e),
        )


def screen_universe(tickers: list[str], price_provider, news_provider) -> list[dict]:
    """Chạy song song nhiều mã cùng lúc (nhanh hơn nhiều so với tuần tự khi
    dùng dữ liệu thật cần gọi mạng)."""
    if not tickers:
        return []
    rows = [None] * len(tickers)
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(tickers))) as ex:
        future_to_idx = {
            ex.submit(_screen_one, t, price_provider, news_provider): i
            for i, t in enumerate(tickers)
        }
        for future in concurrent.futures.as_completed(future_to_idx):
            rows[future_to_idx[future]] = future.result()
    return rows
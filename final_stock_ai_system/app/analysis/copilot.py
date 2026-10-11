"""
Trợ lý phân tích dạng chatbot (mục 'AI Copilot Chat' trên giao diện).

QUAN TRỌNG — minh bạch về bản chất: đây là bộ dò từ khoá tiếng Việt +
trích xuất mã cổ phiếu (rule-based), KHÔNG phải mô hình ngôn ngữ lớn (LLM).
Lý do chọn cách này cho bản chạy mặc định:
  1. Môi trường dev/sandbox không có internet để gọi API LLM.
  2. Cách này 100% minh bạch, không có rủi ro AI "bịa" số liệu — mọi câu trả
     lời đều lấy trực tiếp từ analyze_ticker() (dữ liệu thật của hệ thống).

NÂNG CẤP LÊN LLM THẬT (Claude API) khi bạn có API key:
  - Giữ nguyên phần trích xuất ticker + gọi analyze_ticker() bên dưới để lấy
    "context" (đúng tinh thần RAG: truy xuất dữ liệu thật trước khi trả lời).
  - Thay khối if/elif ở cuối hàm `answer()` bằng 1 lời gọi:
        anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            messages=[{"role": "user", "content": f"Dữ liệu: {result}\n\nCâu hỏi: {message}"}],
        )
    Việc này giữ nguyên toàn bộ pipeline, chỉ đổi cách "diễn giải" dữ liệu
    thành câu trả lời tự nhiên hơn.
"""
from __future__ import annotations
import re

# Loại trừ các từ tiếng Việt viết hoa ngắn hay bị nhận nhầm thành mã CK.
_STOPWORD_TICKERS = {"CHO", "QUA", "LAM", "XEM", "NEN", "CAC", "VOI", "TAI", "HAY", "LA", "CO"}

_TECH_KEYWORDS = ["kỹ thuật", "ky thuat", "rsi", "macd", "sma", "ema",
                   "xu hướng", "xu huong", "tín hiệu", "tin hieu", "golden cross", "death cross"]
_NEWS_KEYWORDS = ["tin tức", "tin tuc", "báo chí", "bao chi", "sentiment", "tích cực", "tieu cuc", "tiêu cực"]
_PLAN_KEYWORDS = ["khuyến nghị", "khuyen nghi", "đầu tư", "dau tu", "nên mua", "nen mua",
                   "nên bán", "nen ban", "mua hay bán", "mua hay ban", "target",
                   "cắt lỗ", "cat lo", "chốt lời", "chot loi", "entry", "vào lệnh", "vao lenh"]
_PRICE_KEYWORDS = ["giá bao nhiêu", "gia bao nhieu", "thị giá", "thi gia", "giá hiện tại", "gia hien tai"]
_MARKET_KEYWORDS = ["thị trường", "thi truong", "tổng quan", "tong quan", "toàn cảnh", "toan canh"]


def _extract_ticker(message: str, watchlist: list[str]) -> str | None:
    tokens = re.findall(r"[A-Za-zÀ-ỹ]+", message)
    upper_tokens = [t.upper() for t in tokens]

    # Ưu tiên khớp với danh mục đang theo dõi (độ tin cậy cao nhất).
    for t in upper_tokens:
        if t in watchlist:
            return t

    # Fallback: mã được gõ liền, viết hoa rõ ràng, thuần ASCII, 2-4 ký tự
    # (đúng quy ước mã CK Việt Nam) và không nằm trong danh sách loại trừ.
    for raw in tokens:
        if raw.isupper() and 2 <= len(raw) <= 4 and raw.isascii() and raw not in _STOPWORD_TICKERS:
            return raw
    return None


def _detect_intent(message_lower: str) -> str:
    if any(k in message_lower for k in _PLAN_KEYWORDS):
        return "plan"
    if any(k in message_lower for k in _TECH_KEYWORDS):
        return "technical"
    if any(k in message_lower for k in _NEWS_KEYWORDS):
        return "news"
    if any(k in message_lower for k in _PRICE_KEYWORDS):
        return "price"
    return "summary"


def answer(message: str, watchlist: list[str], price_provider, news_provider) -> dict:
    from app.analysis.screener import analyze_ticker, screen_universe

    message = (message or "").strip()
    msg_lower = message.lower()
    ticker = _extract_ticker(message, watchlist)

    if any(k in msg_lower for k in _MARKET_KEYWORDS) and not ticker:
        rows = screen_universe(watchlist, price_provider, news_provider)
        gainers = sum(1 for r in rows if r["change_pct"] > 0)
        losers = sum(1 for r in rows if r["change_pct"] < 0)
        strong_buy = sum(1 for r in rows if r["trend"] == "MUA MẠNH")
        reply = (
            f"Danh mục theo dõi có {len(rows)} mã: {gainers} mã tăng giá, {losers} mã giảm giá, "
            f"{strong_buy} mã đang có tín hiệu MUA MẠNH. Bạn hỏi cụ thể 1 mã (vd \"phân tích FPT\") "
            f"để mình xem chi tiết hơn nhé."
        )
        return dict(reply=reply, ticker=None)

    if not ticker:
        reply = (
            "Bạn cho mình biết muốn hỏi về mã cổ phiếu nào nhé? Ví dụ: "
            "\"Phân tích FPT\", \"RSI của HPG bao nhiêu\", \"Có nên mua VCB không\", "
            "\"Tin tức về TCB\"."
        )
        return dict(reply=reply, ticker=None)

    intent = _detect_intent(msg_lower)
    result = analyze_ticker(ticker, price_provider, news_provider, news_limit=5)
    q, sig, plan = result["quote"], result["technical_signal"], result["investment_plan"]

    if intent == "price":
        reply = f"Giá {ticker} hiện tại: {q['price']:,} ({q['change_pct']:+.2f}%), khối lượng khớp lệnh {q['volume']:,}."
    elif intent == "technical":
        reply = (f"Tín hiệu kỹ thuật {ticker}: {sig['trend']} (điểm số {sig['score']}). "
                  f"Chi tiết: {'; '.join(sig['detail'])}.")
    elif intent == "news":
        c = result["sentiment_summary"]["counts"]
        reply = (f"Tin tức gần đây về {ticker}: {c['Tích cực']} tích cực, {c['Tiêu cực']} tiêu cực, "
                  f"{c['Trung lập']} trung lập (net score {result['sentiment_summary']['net_score']}). "
                  f"Nguồn: {', '.join(result['news_by_source'].keys()) or 'không có'}.")
    elif intent == "plan":
        entry = f"{plan['entry_zone'][0]:,} – {plan['entry_zone'][1]:,}" if plan["entry_zone"] else "chưa xác định"
        target = f"{plan['target_price']:,}" if plan["target_price"] is not None else "chưa xác định"
        reply = (f"Với {ticker}: {plan['action']} (độ tin cậy: {plan['confidence']}). "
                  f"Vùng vào lệnh tham khảo: {entry}. Mục tiêu chốt lời: {target}. "
                  f"Cắt lỗ tham khảo: {plan['stoploss_price']:,}. {plan['disclaimer']}")
    else:
        reply = (f"{ticker}: giá {q['price']:,} ({q['change_pct']:+.2f}%). "
                  f"Tín hiệu kỹ thuật: {sig['trend']}. Sentiment tin tức: net {result['sentiment_summary']['net_score']}. "
                  f"Khuyến nghị tổng hợp: {result['combined_recommendation']}")

    return dict(reply=reply, ticker=ticker)

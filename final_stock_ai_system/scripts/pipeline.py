"""
Chạy pipeline thu thập + phân tích + lưu DB mà KHÔNG cần bật web server.
Dùng cho cron job (vd: chạy mỗi 15 phút để cập nhật dữ liệu + gửi cảnh báo).

Cách chạy:
    python scripts/pipeline.py FPT HPG TCB

Cron ví dụ (Linux, mỗi 15 phút):
    */15 * * * * cd /path/to/stock_ai_system && /usr/bin/python3 scripts/pipeline.py FPT HPG TCB VCB >> pipeline.log 2>&1
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import PRICE_PROVIDER, PRICE_API_BASE_URL, NEWS_PROVIDER, DEFAULT_WATCHLIST
from app.data.price_provider import get_price_provider
from app.data.news_provider import get_news_provider
from app.analysis.screener import analyze_ticker
from app.db import init_db, save_news, save_signal


def send_telegram_alert(message: str):
    """Gửi cảnh báo qua Telegram Bot API nếu đã cấu hình TELEGRAM_BOT_TOKEN/CHAT_ID."""
    import os
    import requests
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("  (Bỏ qua Telegram alert — chưa cấu hình TELEGRAM_BOT_TOKEN/CHAT_ID)")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": message}, timeout=10)


def run(tickers: list[str]):
    init_db()
    price_provider = get_price_provider(PRICE_PROVIDER, PRICE_API_BASE_URL or None)
    news_provider = get_news_provider(NEWS_PROVIDER)

    print(f"=== Pipeline chạy lúc: {__import__('datetime').datetime.now()} ===")
    for ticker in tickers:
        print(f"\n--- {ticker} ---")
        result = analyze_ticker(ticker, price_provider, news_provider, news_limit=8)

        save_news(ticker, result["news"])
        save_signal(ticker, result["technical_signal"], result["quote"]["price"])

        signal = result["technical_signal"]
        print(f"  Giá: {result['quote']['price']} ({result['quote']['change_pct']}%)")
        print(f"  Tín hiệu KT: {signal['trend']} (score={signal['score']})")
        print(f"  Sentiment tin tức: {result['sentiment_summary']}")
        print(f"  Khuyến nghị: {result['combined_recommendation']}")

        # Cảnh báo khi có tín hiệu mạnh
        if signal["strength"] in ("strong_buy", "strong_sell"):
            send_telegram_alert(
                f"🔔 {ticker}: {signal['trend']} — {result['combined_recommendation']}"
            )

    print("\n=== Hoàn tất ===")


if __name__ == "__main__":
    tickers = sys.argv[1:] or DEFAULT_WATCHLIST
    run(tickers)

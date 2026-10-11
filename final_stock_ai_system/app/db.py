"""Lưu lịch sử tin tức đã phân loại + tín hiệu kỹ thuật vào SQLite, để:
  - Không phải gọi lại provider mỗi lần xem lại lịch sử.
  - Làm dữ liệu cho phần Backtest / thống kê hiệu quả tín hiệu về sau.
Dùng sqlite3 chuẩn của Python, không cần cài driver ngoài."""
from __future__ import annotations
import sqlite3
import json
import datetime as dt
from contextlib import contextmanager

from app.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT,
    source TEXT,
    published_at TEXT,
    sentiment_label TEXT,
    sentiment_score REAL,
    saved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    trend TEXT,
    strength TEXT,
    score INTEGER,
    detail_json TEXT,
    price REAL,
    saved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watchlist (
    ticker TEXT PRIMARY KEY,
    added_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_news_ticker ON news(ticker);
CREATE INDEX IF NOT EXISTS idx_signals_ticker ON signals(ticker);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def seed_watchlist_if_empty(default_tickers: list[str]):
    """Nếu bảng watchlist chưa có gì (lần đầu chạy), gieo sẵn danh sách mặc định
    để người dùng có dữ liệu ngay, nhưng vẫn cho phép họ tự thêm/xoá về sau."""
    now = dt.datetime.now().isoformat()
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM watchlist").fetchone()["c"]
        if count == 0:
            for t in default_tickers:
                conn.execute(
                    "INSERT OR IGNORE INTO watchlist (ticker, added_at) VALUES (?, ?)",
                    (t.upper(), now),
                )


def get_watchlist() -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT ticker FROM watchlist ORDER BY added_at ASC"
        ).fetchall()
        return [r["ticker"] for r in rows]


def add_to_watchlist(ticker: str):
    now = dt.datetime.now().isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (ticker, added_at) VALUES (?, ?)",
            (ticker.upper(), now),
        )


def remove_from_watchlist(ticker: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker.upper(),))


def save_news(ticker: str, news_items: list[dict]):
    now = dt.datetime.now().isoformat()
    with get_conn() as conn:
        for n in news_items:
            conn.execute(
                """INSERT INTO news (ticker, title, url, source, published_at,
                       sentiment_label, sentiment_score, saved_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ticker, n["title"], n.get("url"), n.get("source"),
                    n.get("published_at"),
                    n.get("sentiment", {}).get("label"),
                    n.get("sentiment", {}).get("score"),
                    now,
                ),
            )


def save_signal(ticker: str, signal: dict, price: float):
    now = dt.datetime.now().isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO signals (ticker, trend, strength, score, detail_json, price, saved_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                ticker, signal.get("trend"), signal.get("strength"),
                signal.get("score"), json.dumps(signal.get("detail", []), ensure_ascii=False),
                price, now,
            ),
        )


def get_signal_history(ticker: str, limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM signals WHERE ticker = ? ORDER BY saved_at DESC LIMIT ?",
            (ticker, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_news_history(ticker: str, limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM news WHERE ticker = ? ORDER BY saved_at DESC LIMIT ?",
            (ticker, limit),
        ).fetchall()
        return [dict(r) for r in rows]

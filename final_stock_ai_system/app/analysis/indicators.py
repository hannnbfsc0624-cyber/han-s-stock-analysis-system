"""Chỉ báo kỹ thuật cơ bản, dùng pandas/numpy thuần — không phụ thuộc thư viện
ngoài (ta-lib khó cài, nhiều lỗi build) nên tự viết công thức chuẩn."""
from __future__ import annotations
import pandas as pd
import numpy as np


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=window, min_periods=window).mean()
    avg_loss = loss.rolling(window=window, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50)


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def enrich_with_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Nhận DataFrame OHLCV, trả về thêm các cột chỉ báo."""
    out = df.copy()
    out["sma20"] = sma(out["close"], 20)
    out["sma50"] = sma(out["close"], 50)
    out["ema20"] = ema(out["close"], 20)
    out["rsi14"] = rsi(out["close"], 14)
    macd_line, signal_line, hist = macd(out["close"])
    out["macd"] = macd_line
    out["macd_signal"] = signal_line
    out["macd_hist"] = hist
    return out


def detect_signal(enriched_df: pd.DataFrame) -> dict:
    """Trả về tín hiệu tổng hợp tại phiên gần nhất dựa trên SMA cross, RSI, MACD.
    Đây là logic rule-based đơn giản, minh bạch (không phải hộp đen) — dễ chỉnh
    ngưỡng theo khẩu vị của bạn."""
    if len(enriched_df) < 2:
        return dict(trend="Không đủ dữ liệu", strength="N/A", detail=[])

    last = enriched_df.iloc[-1]
    prev = enriched_df.iloc[-2]
    detail = []
    score = 0

    # 1. Golden / Death cross SMA20 vs SMA50
    if pd.notna(last["sma20"]) and pd.notna(last["sma50"]):
        if prev["sma20"] <= prev["sma50"] and last["sma20"] > last["sma50"]:
            detail.append("Golden Cross (SMA20 cắt lên SMA50)")
            score += 2
        elif prev["sma20"] >= prev["sma50"] and last["sma20"] < last["sma50"]:
            detail.append("Death Cross (SMA20 cắt xuống SMA50)")
            score -= 2
        elif last["sma20"] > last["sma50"]:
            detail.append("Xu hướng tăng ngắn hạn (SMA20 > SMA50)")
            score += 1
        else:
            detail.append("Xu hướng giảm ngắn hạn (SMA20 < SMA50)")
            score -= 1

    # 2. RSI
    if pd.notna(last["rsi14"]):
        if last["rsi14"] < 30:
            detail.append(f"RSI={last['rsi14']:.1f} — Vùng quá bán")
            score += 1
        elif last["rsi14"] > 70:
            detail.append(f"RSI={last['rsi14']:.1f} — Vùng quá mua")
            score -= 1
        else:
            detail.append(f"RSI={last['rsi14']:.1f} — Trung tính")

    # 3. MACD histogram
    if pd.notna(last["macd_hist"]) and pd.notna(prev["macd_hist"]):
        if prev["macd_hist"] <= 0 and last["macd_hist"] > 0:
            detail.append("MACD cắt lên đường tín hiệu (Bullish)")
            score += 2
        elif prev["macd_hist"] >= 0 and last["macd_hist"] < 0:
            detail.append("MACD cắt xuống đường tín hiệu (Bearish)")
            score -= 2

    if score >= 3:
        trend, strength = "MUA MẠNH", "strong_buy"
    elif score >= 1:
        trend, strength = "TĂNG", "buy"
    elif score <= -3:
        trend, strength = "BÁN MẠNH", "strong_sell"
    elif score <= -1:
        trend, strength = "GIẢM", "sell"
    else:
        trend, strength = "TRUNG LẬP", "neutral"

    return dict(trend=trend, strength=strength, score=score, detail=detail)

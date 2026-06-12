#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""簡易バックテスト（signal_detect と同一ルール）"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

import pandas as pd

from signal_detect import detect_golden_crosses, parse_rule

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
WIN_DAYS = 5
WIN_THRESHOLD_PCT = 1.0


@lru_cache(maxsize=64)
def _fetch_daily(code: str, period: str = "6mo", suffix: str = ".T") -> pd.DataFrame | None:
    try:
        import yfinance as yf
        ticker = code if "." in code else f"{code}{suffix}"
        df = yf.Ticker(ticker).history(period=period)
        if df is None or len(df) < 30:
            return None
        df.index = pd.to_datetime(df.index).tz_localize(None)
        return df[["Close", "Low", "High"]]
    except Exception:
        return None


def run_backtest(code: str, rule: str = "EMA9/21", period: str = "6mo",
                 market: str = "jp") -> dict:
    suffix = ".T" if market == "jp" else ""
    df = _fetch_daily(code.upper().replace(".T", ""), period, suffix)
    if df is None:
        return {"ok": False, "error": "データ取得失敗", "code": code, "rule": rule}

    fast, slow, ma_type = parse_rule(rule)
    close = df["Close"]
    crosses = detect_golden_crosses(close, fast, slow, ma_type)
    wins = losses = 0
    total_pnl_pct = 0.0
    trades = []

    for ci in crosses:
        if ci + WIN_DAYS >= len(close):
            continue
        entry = float(close.iloc[ci])
        exit_p = float(close.iloc[ci + WIN_DAYS])
        ret = (exit_p / entry - 1) * 100
        win = ret >= WIN_THRESHOLD_PCT
        if win:
            wins += 1
        else:
            losses += 1
        total_pnl_pct += ret
        trades.append({
            "date": close.index[ci].strftime("%Y-%m-%d"),
            "entry": round(entry, 2),
            "ret_5d": round(ret, 2),
            "win": win,
        })

    count = wins + losses
    return {
        "ok": True,
        "code": code,
        "rule": rule,
        "period": period,
        "wins": wins,
        "losses": losses,
        "count": count,
        "win_rate": round(wins / count * 100, 1) if count else None,
        "total_return_pct": round(total_pnl_pct, 2),
        "avg_return_pct": round(total_pnl_pct / count, 2) if count else None,
        "trades": trades[-10:],
    }


def avg_lag_pct(code: str, rule: str = "EMA9/21", period: str = "1y",
                market: str = "jp") -> float | None:
    """銘柄の平均出遅れコスト%（おすすめカード用）"""
    try:
        import signal_lag as sl
        suffix = ".T" if market == "jp" else ""
        ticker = code.upper().replace(".T", "")
        df = sl.fetch_prices(ticker, period) if market == "jp" else _fetch_daily(ticker, period, "")
        if df is None:
            return None
        events = sl.analyze_stock(ticker, df, rule)
        if not events:
            return None
        return round(sum(e.lag_cost for e in events) / len(events), 2)
    except Exception:
        return None

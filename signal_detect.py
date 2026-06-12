#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""シグナル検出の共通ロジック（スキャナ・lag・バックテストで共用）"""

from __future__ import annotations

import pandas as pd

RULE_SPECS = {
    "SMA5/25": {"fast": 5, "slow": 25, "ma_type": "sma"},
    "SMA50/200": {"fast": 50, "slow": 200, "ma_type": "sma"},
    "EMA9/21": {"fast": 9, "slow": 21, "ma_type": "ema"},
}


def compute_ma(close: pd.Series, fast: int, slow: int,
               ma_type: str = "sma") -> tuple[pd.Series, pd.Series]:
    if ma_type == "ema":
        ma_f = close.ewm(span=fast, adjust=False).mean()
        ma_s = close.ewm(span=slow, adjust=False).mean()
    else:
        ma_f = close.rolling(fast).mean()
        ma_s = close.rolling(slow).mean()
    return ma_f, ma_s


def detect_golden_crosses(close: pd.Series, fast: int, slow: int,
                          ma_type: str = "sma") -> list[int]:
    ma_f, ma_s = compute_ma(close, fast, slow, ma_type)
    crosses = []
    for i in range(slow, len(close)):
        prev_f, prev_s = ma_f.iloc[i - 1], ma_s.iloc[i - 1]
        cur_f, cur_s = ma_f.iloc[i], ma_s.iloc[i]
        if pd.notna(prev_f) and pd.notna(prev_s) and prev_f <= prev_s and cur_f > cur_s:
            crosses.append(i)
    return crosses


def find_trough(close: pd.Series, cross_idx: int, fast: int, slow: int,
                ma_type: str = "sma", max_lookback: int = 120) -> int:
    ma_f, ma_s = compute_ma(close, fast, slow, ma_type)
    start = cross_idx - 1
    while (start > 0 and cross_idx - start < max_lookback
           and pd.notna(ma_f.iloc[start]) and pd.notna(ma_s.iloc[start])
           and ma_f.iloc[start] <= ma_s.iloc[start]):
        start -= 1
    lo, hi = max(0, start), cross_idx
    if hi - lo < 3:
        lo = max(0, cross_idx - 20)
    window = close.iloc[lo:hi + 1]
    return lo + int(window.values.argmin())


def parse_rule(rule: str) -> tuple[int, int, str]:
    spec = RULE_SPECS.get(rule, RULE_SPECS["SMA5/25"])
    return spec["fast"], spec["slow"], spec["ma_type"]

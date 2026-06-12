# -*- coding: utf-8 -*-
"""
signal_lag.py — サインの「出遅れコスト」計測モジュール

目的:
  クロス系サインの交換条件(早い=だまし多い ⇔ 遅い=出遅れる)を
  数字で見えるようにする。

計測する指標(1サインごと):
  ① 出遅れコスト: 直近の安値(下落局面の底)からサイン発生日の終値まで、
                   すでに何%上がってしまっていたか
  ② 先行リターン: サイン発生から5日後・20日後のリターン(答え合わせ)
  ③ 勝率:         20日後リターンがプラスだったサインの割合

使い方:
  バックテスト: python signal_lag.py 6367          (1銘柄を過去2年で検証)
                python signal_lag.py 6367 --compare (SMA5/25 と 50/200 を比較)
  オフライン:   python signal_lag.py --mock --compare
  ライブ記録:   record_live_signal() をデモ取引スキャンから呼ぶ
                update_pending() を起動時に呼んで答え合わせを反映
"""

from __future__ import annotations

import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "signal_log.db"

# ルール定義: (短期, 長期)
RULES = {
    "SMA5/25":   (5, 25),    # 既存アプリの判定(早い・敏感)
    "SMA50/200": (50, 200),  # 中長期(遅い・手堅い)
}
HORIZONS = (5, 20)           # 答え合わせの日数
WIN_HORIZON = 20             # 勝率判定に使う日数


# ============================================================
# データ構造
# ============================================================

@dataclass
class SignalEvent:
    code: str
    rule: str
    date: str            # サイン発生日 YYYY-MM-DD
    signal_price: float  # サイン発生日の終値
    trough_date: str     # 直近安値の日
    trough_price: float  # 直近安値
    lag_cost: float      # 出遅れコスト % = (signal/trough - 1)*100
    fwd: dict            # {5: +2.3, 20: -1.1} 日数→リターン%(未確定はNone)

    @property
    def is_win(self):
        r = self.fwd.get(WIN_HORIZON)
        return None if r is None else r > 0


# ============================================================
# 価格データ取得
# ============================================================

def fetch_prices(code: str, period: str = "2y") -> pd.DataFrame | None:
    """yfinanceから日足を取得。列: Close, Low"""
    try:
        import yfinance as yf
        df = yf.Ticker(f"{code}.T").history(period=period)
        if df is None or len(df) < 60:
            return None
        df.index = pd.to_datetime(df.index).tz_localize(None)
        return df[["Close", "Low"]]
    except Exception as e:
        print(f"[signal_lag] 価格取得失敗 {code}: {e}", file=sys.stderr)
        return None


def make_mock_prices(seed: int = 1, days: int = 500) -> pd.DataFrame:
    """オフライン確認用: 上下動する擬似株価(決定的に生成)"""
    import math
    closes = []
    p = 1000.0
    for i in range(days):
        wave = math.sin(i / 35 + seed) * 0.006 + math.sin(i / 9 + seed * 2) * 0.004
        drift = 0.0004
        noise = math.sin(i * 12.9898 + seed * 78.233) * 0.005  # 擬似乱数
        p *= 1 + wave + drift + noise
        closes.append(round(p, 1))
    idx = pd.bdate_range(end=datetime.now().date(), periods=days)
    df = pd.DataFrame({"Close": closes}, index=idx)
    df["Low"] = df["Close"] * 0.995
    return df


# ============================================================
# サイン検出と出遅れコスト計算
# ============================================================

def detect_golden_crosses(close: pd.Series, fast: int, slow: int) -> list[int]:
    """ゴールデンクロスが発生した日のインデックス位置を返す"""
    ma_f = close.rolling(fast).mean()
    ma_s = close.rolling(slow).mean()
    crosses = []
    for i in range(slow, len(close)):
        prev_f, prev_s = ma_f.iloc[i - 1], ma_s.iloc[i - 1]
        cur_f, cur_s = ma_f.iloc[i], ma_s.iloc[i]
        if pd.notna(prev_f) and pd.notna(prev_s) and prev_f <= prev_s and cur_f > cur_s:
            crosses.append(i)
    return crosses


def find_trough(close: pd.Series, cross_idx: int, fast: int, slow: int,
                max_lookback: int = 120) -> int:
    """
    出遅れコストの基準になる「直近安値」の位置を返す。
    クロス発生日から遡って、短期MAが長期MAの下にいた期間(=下落局面)の
    最安値を探す。下落局面が見つからない場合は直近20日の最安値。
    """
    ma_f = close.rolling(fast).mean()
    ma_s = close.rolling(slow).mean()
    start = cross_idx - 1
    while (start > 0 and cross_idx - start < max_lookback
           and pd.notna(ma_f.iloc[start]) and pd.notna(ma_s.iloc[start])
           and ma_f.iloc[start] <= ma_s.iloc[start]):
        start -= 1
    lo, hi = max(0, start), cross_idx
    if hi - lo < 3:   # 下落局面が短すぎる場合のフォールバック
        lo = max(0, cross_idx - 20)
    window = close.iloc[lo:hi + 1]
    return lo + int(window.values.argmin())


def analyze_stock(code: str, df: pd.DataFrame | None = None,
                  rule: str = "SMA5/25") -> list[SignalEvent]:
    """1銘柄×1ルールの全サインについて出遅れコストと先行リターンを計算"""
    if df is None:
        df = fetch_prices(code)
    if df is None:
        return []
    fast, slow = RULES[rule]
    close = df["Close"]
    events = []
    for ci in detect_golden_crosses(close, fast, slow):
        ti = find_trough(close, ci, fast, slow)
        sig_p, trough_p = float(close.iloc[ci]), float(close.iloc[ti])
        lag = (sig_p / trough_p - 1) * 100 if trough_p else 0.0
        fwd = {}
        for h in HORIZONS:
            if ci + h < len(close):
                fwd[h] = round((float(close.iloc[ci + h]) / sig_p - 1) * 100, 2)
            else:
                fwd[h] = None   # まだ日数が経っていない=未確定
        events.append(SignalEvent(
            code=code, rule=rule,
            date=close.index[ci].strftime("%Y-%m-%d"),
            signal_price=round(sig_p, 1),
            trough_date=close.index[ti].strftime("%Y-%m-%d"),
            trough_price=round(trough_p, 1),
            lag_cost=round(lag, 2),
            fwd=fwd,
        ))
    return events


def summarize(events: list[SignalEvent]) -> dict:
    """サイン群を集計: 件数 / 平均出遅れコスト / 勝率 / 平均20日リターン"""
    if not events:
        return {"count": 0, "avg_lag": None, "win_rate": None, "avg_fwd20": None}
    lags = [e.lag_cost for e in events]
    settled = [e for e in events if e.is_win is not None]
    wins = [e for e in settled if e.is_win]
    fwd20 = [e.fwd[WIN_HORIZON] for e in settled]
    return {
        "count": len(events),
        "avg_lag": round(sum(lags) / len(lags), 2),
        "win_rate": round(len(wins) / len(settled) * 100, 1) if settled else None,
        "avg_fwd20": round(sum(fwd20) / len(fwd20), 2) if fwd20 else None,
        "settled": len(settled),
    }


def compare_rules(code: str, df: pd.DataFrame | None = None) -> dict:
    """SMA5/25 と SMA50/200 の交換条件を並べて返す"""
    if df is None:
        df = fetch_prices(code)
    out = {}
    for rule in RULES:
        events = analyze_stock(code, df, rule)
        out[rule] = {"summary": summarize(events), "events": events}
    return out


# ============================================================
# デモ取引のライブ記録(SQLite)
# ============================================================

def _conn():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS signal_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT, name TEXT, rule TEXT,
        signal_date TEXT, signal_price REAL,
        trough_date TEXT, trough_price REAL,
        lag_cost REAL,
        fwd5 REAL, fwd20 REAL,
        status TEXT DEFAULT 'pending',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(code, rule, signal_date)
    )""")
    return con


def record_live_signal(code: str, name: str = "", rule: str = "SMA5/25") -> dict | None:
    """
    デモ取引スキャンから呼ぶ。
    「今日」ゴールデンクロスが発生していれば出遅れコストを計算して記録。
    発生していなければ None を返す。
    """
    df = fetch_prices(code, period="1y")
    if df is None:
        return None
    fast, slow = RULES[rule]
    crosses = detect_golden_crosses(df["Close"], fast, slow)
    if not crosses or crosses[-1] != len(df) - 1:
        return None   # 直近の足でクロスしていない
    ev = analyze_stock(code, df, rule)[-1]
    con = _conn()
    try:
        con.execute(
            """INSERT OR IGNORE INTO signal_log
               (code, name, rule, signal_date, signal_price,
                trough_date, trough_price, lag_cost)
               VALUES (?,?,?,?,?,?,?,?)""",
            (code, name, rule, ev.date, ev.signal_price,
             ev.trough_date, ev.trough_price, ev.lag_cost))
        con.commit()
    finally:
        con.close()
    return {"code": code, "rule": rule, "date": ev.date, "lag_cost": ev.lag_cost}


def update_pending() -> int:
    """
    アプリ起動時などに呼ぶ。pending のサインについて、
    サイン発生からの経過日数が足りていれば5日後・20日後リターンを記録。
    """
    con = _conn()
    updated = 0
    try:
        rows = con.execute(
            "SELECT id, code, rule, signal_date, signal_price, fwd5, fwd20 "
            "FROM signal_log WHERE status='pending'").fetchall()
        for rid, code, rule, sig_date, sig_price, fwd5, fwd20 in rows:
            df = fetch_prices(code, period="6mo")
            if df is None:
                continue
            close = df["Close"]
            dates = [d.strftime("%Y-%m-%d") for d in close.index]
            if sig_date not in dates:
                continue
            i = dates.index(sig_date)
            new5, new20 = fwd5, fwd20
            if fwd5 is None and i + 5 < len(close):
                new5 = round((float(close.iloc[i + 5]) / sig_price - 1) * 100, 2)
            if fwd20 is None and i + 20 < len(close):
                new20 = round((float(close.iloc[i + 20]) / sig_price - 1) * 100, 2)
            status = "settled" if new20 is not None else "pending"
            if (new5, new20) != (fwd5, fwd20):
                con.execute(
                    "UPDATE signal_log SET fwd5=?, fwd20=?, status=? WHERE id=?",
                    (new5, new20, status, rid))
                updated += 1
        con.commit()
    finally:
        con.close()
    return updated


def get_lag_report() -> dict:
    """画面表示用: ライブ記録の集計をルール別に返す"""
    con = _conn()
    try:
        out = {"rules": [], "recent": []}
        for rule in RULES:
            row = con.execute(
                """SELECT COUNT(*), AVG(lag_cost),
                          AVG(CASE WHEN fwd20 > 0 THEN 100.0 ELSE 0 END),
                          AVG(fwd20)
                   FROM signal_log WHERE rule=? AND status='settled'""",
                (rule,)).fetchone()
            pending = con.execute(
                "SELECT COUNT(*) FROM signal_log WHERE rule=? AND status='pending'",
                (rule,)).fetchone()[0]
            out["rules"].append({
                "rule": rule, "settled": row[0] or 0, "pending": pending,
                "avg_lag": round(row[1], 2) if row[1] is not None else None,
                "win_rate": round(row[2], 1) if row[2] is not None else None,
                "avg_fwd20": round(row[3], 2) if row[3] is not None else None,
            })
        for r in con.execute(
                """SELECT code, name, rule, signal_date, lag_cost, fwd5, fwd20, status
                   FROM signal_log ORDER BY signal_date DESC LIMIT 10"""):
            out["recent"].append(dict(zip(
                ["code", "name", "rule", "date", "lag_cost", "fwd5", "fwd20", "status"], r)))
        return out
    finally:
        con.close()


# ============================================================
# CLI
# ============================================================

def _print_compare(code: str, result: dict):
    print(f"\n===== {code} ルール別の交換条件 =====")
    print(f"{'ルール':<12}{'サイン数':>6}{'平均出遅れ':>10}{'勝率(20日)':>11}{'平均20日リターン':>14}")
    for rule, data in result.items():
        s = data["summary"]
        lag = f"{s['avg_lag']}%" if s["avg_lag"] is not None else "-"
        win = f"{s['win_rate']}%" if s["win_rate"] is not None else "-"
        fwd = f"{s['avg_fwd20']:+}%" if s["avg_fwd20"] is not None else "-"
        print(f"{rule:<14}{s['count']:>6}{lag:>11}{win:>11}{fwd:>13}")
    print("\n  ※ 出遅れコスト = 直近安値からサイン発生日までに既に上がっていた率")
    print("  ※ 早いルールほど出遅れは小さいが勝率が落ちる…はず。それを確認するのがこの表")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    mock = "--mock" in sys.argv
    do_compare = "--compare" in sys.argv

    code = args[0] if args else "MOCK"
    df = make_mock_prices() if mock else None

    if do_compare:
        _print_compare(code, compare_rules(code, df))
    else:
        events = analyze_stock(code, df)
        print(f"{code} SMA5/25 のサイン一覧:")
        for e in events:
            f20 = f"{e.fwd[20]:+}%" if e.fwd[20] is not None else "未確定"
            print(f"  {e.date}  出遅れ{e.lag_cost:>6}%  (底 {e.trough_date} {e.trough_price})"
                  f"  → 20日後 {f20}")
        s = summarize(events)
        print(f"\n  集計: {s['count']}回 / 平均出遅れ {s['avg_lag']}% / "
              f"勝率 {s['win_rate']}% / 平均20日リターン {s['avg_fwd20']}%")

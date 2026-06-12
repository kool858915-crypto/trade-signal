#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""統合トレードロジック (日本株/米国株 × デイトレ/スイング/中長期 + ntfy通知 + SQLite)"""

import json
import os
import sqlite3
import threading
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd

try:
    import yfinance as yf
except ImportError:
    yf = None

# ============================================================
# 設定 (環境変数で上書き可能)
# ============================================================
MARKETS = {
    "jp": {
        "name": "日本株",
        "watchlist": ["7203", "6758", "9984", "8306", "6861"],
        "suffix": ".T",
        "currency": "円",
        "capital": 1_000_000,
        "unit": 100,
        "price_fmt": "{:,.1f}",
    },
    "us": {
        "name": "米国株",
        "watchlist": ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN"],
        "suffix": "",
        "currency": "$",
        "capital": 10_000,
        "unit": 1,
        "price_fmt": "{:,.2f}",
    },
}

STYLES = {
    "day": {
        "name": "デイトレード",
        "interval": "5m", "period": "5d",
        "ma_type": "ema", "fast": 9, "slow": 21,
        "use_vwap": True,
        "atr_stop_mult": 1.5, "reward_risk": 2.0,
        "risk_per_trade": 0.01,
        "vol_surge_ratio": 1.5,
    },
    "swing": {
        "name": "スイング",
        "interval": "1d", "period": "1y",
        "ma_type": "ema", "fast": 20, "slow": 50,
        "use_vwap": False,
        "atr_stop_mult": 2.0, "reward_risk": 2.5,
        "risk_per_trade": 0.015,
        "vol_surge_ratio": 1.5,
    },
    "long": {
        "name": "中長期",
        "interval": "1d", "period": "5y",
        "ma_type": "sma", "fast": 50, "slow": 200,
        "use_vwap": False,
        "atr_stop_mult": 3.0, "reward_risk": 3.0,
        "risk_per_trade": 0.02,
        "vol_surge_ratio": 1.3,
    },
}

COMMON = {
    "rsi_period": 14,
    "atr_period": 14,
    "vol_ma_period": 20,
    "rsi_overbought": 70,
    "rsi_oversold": 30,
    "min_score": 3,
}

CONFIG = {
    "notify_backend": "ntfy",
    "ntfy_server": "https://ntfy.sh",
    "ntfy_topic": os.environ.get("NTFY_TOPIC", "my-trade-alerts-x7k2"),
    "web_pin": os.environ.get("WEB_PIN", "1234"),
    "data_dir": os.environ.get("DATA_DIR", "data"),
    "monitor_interval_sec": int(os.environ.get("MONITOR_INTERVAL", "300")),
}

_db_lock = threading.Lock()


class Ctx:
    def __init__(self, market: str, style: str):
        if market not in MARKETS or style not in STYLES:
            raise ValueError(f"invalid market/style: {market}/{style}")
        self.m = MARKETS[market]
        self.s = STYLES[style]
        self.market, self.style = market, style
        self.key = f"{market}_{style}"

    def fmt(self, price: float) -> str:
        p = self.m["price_fmt"].format(price)
        return f"{p}{self.m['currency']}" if self.market == "jp" else f"${p}"

    def fmt_pnl(self, pnl: float) -> str:
        if self.market == "jp":
            return f"{pnl:+,.0f}円"
        return f"{pnl:+,.2f}$"


def _db_path() -> str:
    os.makedirs(CONFIG["data_dir"], exist_ok=True)
    return os.path.join(CONFIG["data_dir"], "trade.db")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _db_lock:
        conn = _conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market TEXT, style TEXT,
                ticker TEXT, side TEXT,
                entry_time TEXT, entry_price REAL, qty INTEGER,
                stop_loss REAL, take_profit REAL,
                stop_alerted INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market TEXT, style TEXT,
                ticker TEXT, side TEXT,
                entry_time TEXT, entry_price REAL, qty INTEGER,
                stop_loss REAL, take_profit REAL,
                exit_time TEXT, exit_price REAL,
                pnl REAL, result TEXT
            );
            CREATE TABLE IF NOT EXISTS daily_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market TEXT, style TEXT,
                session_date TEXT,
                started_at TEXT, ended_at TEXT,
                trades_count INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                total_pnl REAL DEFAULT 0,
                tuning_applied TEXT
            );
            CREATE TABLE IF NOT EXISTS signal_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER,
                market TEXT, style TEXT,
                logged_at TEXT,
                ticker TEXT, signal TEXT,
                price REAL, rsi REAL, score INTEGER,
                conds_json TEXT
            );
        """)
        _migrate_columns(conn)
        conn.commit()
        conn.close()


def _migrate_columns(conn):
    migrations = [
        ("positions", "session_id", "INTEGER"),
        ("positions", "entry_conds_json", "TEXT"),
        ("positions", "entry_signal", "TEXT"),
        ("trades", "session_id", "INTEGER"),
        ("trades", "entry_conds_json", "TEXT"),
        ("trades", "entry_signal", "TEXT"),
        ("trades", "followed_signal", "INTEGER DEFAULT 0"),
        ("positions", "is_demo", "INTEGER DEFAULT 0"),
        ("trades", "is_demo", "INTEGER DEFAULT 0"),
        ("trades", "exit_reason", "TEXT"),
    ]
    for table, col, typ in migrations:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")


def _get_setting(key: str, default=None):
    with _db_lock:
        conn = _conn()
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        conn.close()
        return row["value"] if row else default


def _set_setting(key: str, value: str):
    with _db_lock:
        conn = _conn()
        conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()
        conn.close()


def notification_enabled() -> bool:
    return _get_setting("notify_enabled", "0") == "1"


def set_notification_enabled(enabled: bool):
    _set_setting("notify_enabled", "1" if enabled else "0")
    _set_setting("notify_updated_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


def demo_mode_enabled() -> bool:
    return _get_setting("demo_mode", "0") == "1"


def _session_context() -> tuple[str, str]:
    return (
        _get_setting("session_market", "jp"),
        _get_setting("session_style", "day"),
    )


def _has_open_position(market: str, style: str, ticker: str) -> bool:
    with _db_lock:
        conn = _conn()
        row = conn.execute(
            "SELECT 1 FROM positions WHERE market=? AND style=? AND ticker=? LIMIT 1",
            (market, style, ticker.upper()),
        ).fetchone()
        conn.close()
    return row is not None


# ============================================================
# 市場データ
# ============================================================
def to_yf_ticker(code: str, ctx: Ctx) -> str:
    code = code.strip().upper()
    if ctx.m["suffix"] and "." not in code:
        return code + ctx.m["suffix"]
    return code


def fetch_data(code: str, ctx: Ctx) -> pd.DataFrame:
    if yf is None:
        raise RuntimeError("yfinanceが未インストールです")
    df = yf.download(
        to_yf_ticker(code, ctx),
        interval=ctx.s["interval"],
        period=ctx.s["period"],
        progress=False,
        auto_adjust=True,
    )
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna()


def add_indicators(df: pd.DataFrame, ctx: Ctx) -> pd.DataFrame:
    df = df.copy()
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    s = ctx.s

    if s["ma_type"] == "ema":
        df["ma_fast"] = c.ewm(span=s["fast"], adjust=False).mean()
        df["ma_slow"] = c.ewm(span=s["slow"], adjust=False).mean()
    else:
        df["ma_fast"] = c.rolling(s["fast"]).mean()
        df["ma_slow"] = c.rolling(s["slow"]).mean()

    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / COMMON["rsi_period"], adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / COMMON["rsi_period"], adjust=False).mean()
    rs = gain / loss.replace(0, pd.NA)
    df["rsi"] = 100 - 100 / (1 + rs)

    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / COMMON["atr_period"], adjust=False).mean()

    if s["use_vwap"]:
        tp = (h + l + c) / 3
        day = df.index.date
        pv_cum = (tp * v).groupby(day).cumsum()
        v_cum = v.groupby(day).cumsum()
        df["vwap"] = pv_cum / v_cum.replace(0, pd.NA)

    df["vol_ma"] = v.rolling(COMMON["vol_ma_period"]).mean()
    return df


def _get_params(ctx: Ctx) -> dict:
    try:
        import trade_analytics as ta
        return ta.get_effective_params(ctx.market, ctx.style)
    except ImportError:
        return {
            "min_score": COMMON["min_score"],
            "vol_surge_ratio": ctx.s["vol_surge_ratio"],
            "require_volume": False,
        }


def _active_session_id() -> int | None:
    raw = _get_setting("active_session_id")
    return int(raw) if raw else None


def _create_session(market: str, style: str) -> int:
    now = datetime.now()
    with _db_lock:
        conn = _conn()
        cur = conn.execute(
            """INSERT INTO daily_sessions
               (market, style, session_date, started_at)
               VALUES (?,?,?,?)""",
            (market, style, now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d %H:%M")),
        )
        sid = cur.lastrowid
        conn.commit()
        conn.close()
    _set_setting("active_session_id", str(sid))
    return sid


def _log_signal(session_id, market, style, ticker, r: dict):
    conds_json = json.dumps({k: bool(v) for k, v in r["conds"].items()}, ensure_ascii=False)
    with _db_lock:
        conn = _conn()
        conn.execute(
            """INSERT INTO signal_logs
               (session_id, market, style, logged_at, ticker, signal,
                price, rsi, score, conds_json)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (session_id, market, style,
             datetime.now().strftime("%Y-%m-%d %H:%M"),
             ticker, r["signal"], r["price"], r["rsi"],
             r.get("score", 0), conds_json),
        )
        conn.commit()
        conn.close()


def _snapshot_entry(ctx: Ctx, ticker: str, side: str) -> dict:
    try:
        df = add_indicators(fetch_data(ticker, ctx), ctx)
        if len(df) < ctx.s["slow"] + 6:
            return {"signal": "", "conds": {}, "followed": False}
        r = evaluate(df, ctx)
        followed = (side == "買い" and r["signal"] == "買い") or \
                   (side == "売り" and r["signal"] == "売り")
        return {
            "signal": r["signal"],
            "conds": {k: bool(v) for k, v in r["conds"].items()},
            "followed": followed,
        }
    except Exception:
        return {"signal": "", "conds": {}, "followed": False}


def _close_session(market: str, style: str) -> dict:
    sid = _active_session_id()
    if not sid:
        return {"trades": 0, "pnl": 0.0, "wins": 0, "losses": 0}

    with _db_lock:
        conn = _conn()
        rows = conn.execute(
            """SELECT pnl, result FROM trades
               WHERE session_id=? AND market=? AND style=?""",
            (sid, market, style),
        ).fetchall()
        conn.close()

    pnls = [float(r["pnl"]) for r in rows]
    wins = sum(1 for r in rows if r["result"] == "win")
    losses = len(rows) - wins
    total = sum(pnls)

    tuning_note = ""
    try:
        import trade_analytics as ta
        opt = ta.optimize_and_apply(market, style)
        if opt.get("applied"):
            tuning_note = "; ".join(opt.get("changes", []))
    except ImportError:
        pass

    with _db_lock:
        conn = _conn()
        conn.execute(
            """UPDATE daily_sessions SET
               ended_at=?, trades_count=?, wins=?, losses=?,
               total_pnl=?, tuning_applied=?
               WHERE id=?""",
            (datetime.now().strftime("%Y-%m-%d %H:%M"),
             len(rows), wins, losses, total, tuning_note, sid),
        )
        conn.commit()
        conn.close()

    _set_setting("active_session_id", "")
    return {"trades": len(rows), "pnl": total, "wins": wins, "losses": losses,
            "tuning_note": tuning_note}


def evaluate(df: pd.DataFrame, ctx: Ctx) -> dict:
    last, prev = df.iloc[-1], df.iloc[-2]
    price = float(last["Close"])
    s = ctx.s
    params = _get_params(ctx)
    min_score = params["min_score"]
    vol_ratio = params["vol_surge_ratio"]

    cross_up = prev["ma_fast"] <= prev["ma_slow"] and last["ma_fast"] > last["ma_slow"]
    cross_dn = prev["ma_fast"] >= prev["ma_slow"] and last["ma_fast"] < last["ma_slow"]

    if s["use_vwap"]:
        trend_up = price > last["vwap"]
        trend_dn = price < last["vwap"]
        trend_label_up, trend_label_dn = "VWAPより上", "VWAPより下"
    else:
        slope = last["ma_slow"] - df["ma_slow"].iloc[-6]
        trend_up = pd.notna(slope) and slope > 0
        trend_dn = pd.notna(slope) and slope < 0
        trend_label_up = f"長期MA({s['slow']})が上向き"
        trend_label_dn = f"長期MA({s['slow']})が下向き"

    vol_surge = bool(last["Volume"] > vol_ratio * last["vol_ma"]) \
        if pd.notna(last["vol_ma"]) else False
    if params.get("require_volume") and not vol_surge:
        cross_up = False
        cross_dn = False
    rsi = float(last["rsi"]) if pd.notna(last["rsi"]) else 50.0

    ma_name = f"{s['ma_type'].upper()}{s['fast']}/{s['slow']}"
    long_conds = {
        f"{ma_name}ゴールデンクロス": cross_up,
        trend_label_up: trend_up,
        f"RSI<{COMMON['rsi_overbought']}(過熱でない)": rsi < COMMON["rsi_overbought"],
        "出来高増加": vol_surge,
    }
    short_conds = {
        f"{ma_name}デッドクロス": cross_dn,
        trend_label_dn: trend_dn,
        f"RSI>{COMMON['rsi_oversold']}(売られ過ぎでない)": rsi > COMMON["rsi_oversold"],
        "出来高増加": vol_surge,
    }

    long_score = sum(long_conds.values())
    short_score = sum(short_conds.values())

    if cross_up and long_score >= min_score:
        signal, conds = "買い", long_conds
    elif cross_dn and short_score >= min_score:
        signal, conds = "売り", short_conds
    else:
        signal = "様子見"
        conds = long_conds if long_score >= short_score else short_conds

    result = {
        "signal": signal,
        "price": price,
        "rsi": rsi,
        "atr": float(last["atr"]),
        "time": last.name,
        "conds": conds,
        "score": long_score if signal != "売り" else short_score,
    }
    if s["use_vwap"]:
        result["vwap"] = float(last["vwap"])
    return result


def plan_trade(price: float, atr: float, side: str, ctx: Ctx) -> dict:
    s, m = ctx.s, ctx.m
    risk_width = s["atr_stop_mult"] * atr
    if side == "買い":
        stop = price - risk_width
        target = price + risk_width * s["reward_risk"]
    else:
        stop = price + risk_width
        target = price - risk_width * s["reward_risk"]

    risk_amount = m["capital"] * s["risk_per_trade"]
    raw_qty = risk_amount / risk_width if risk_width > 0 else 0
    qty = int(raw_qty // m["unit"]) * m["unit"]

    return {"stop": stop, "target": target, "qty": qty,
            "risk_amount": risk_amount, "risk_width": risk_width}


# ============================================================
# 通知
# ============================================================
def _http_post(url: str, data: bytes, headers: dict) -> bool:
    req = Request(url, data=data, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=10):
            return True
    except (HTTPError, URLError):
        return False


def send_notification(title: str, message: str, force: bool = False,
                      priority: str = "default") -> bool:
    if not force and not notification_enabled():
        return False

    topic = CONFIG["ntfy_topic"]
    if not topic:
        return False

    safe_title = title.encode("ascii", "ignore").decode() or "Trade Alert"
    headers = {"Title": safe_title, "Tags": "chart_with_upwards_trend"}
    if priority == "urgent":
        headers["Priority"] = "urgent"

    server = CONFIG["ntfy_server"].rstrip("/")
    return _http_post(f"{server}/{topic}", message.encode("utf-8"), headers)


# ============================================================
# API アクション
# ============================================================
def _ctx(market: str, style: str) -> Ctx:
    return Ctx(market, style)


def get_scan_tickers(market: str, style: str) -> list:
    """おすすめキャッシュがあればそれを、なければデフォルトwatchlist"""
    try:
        import trade_recommend as tr
        return tr.get_recommended_tickers(market, style)
    except ImportError:
        return MARKETS[market]["watchlist"]


def action_status(market: str = "jp", style: str = "day") -> dict:
    ctx = _ctx(market, style)
    tuning = {}
    try:
        import trade_analytics as ta
        tuning = ta.get_effective_params(market, style)
    except ImportError:
        pass
    scan_tickers = get_scan_tickers(market, style)
    return {
        "notify_enabled": notification_enabled(),
        "market": market,
        "style": style,
        "market_name": ctx.m["name"],
        "style_name": ctx.s["name"],
        "watchlist": scan_tickers,
        "default_watchlist": ctx.m["watchlist"],
        "recommended_at": _get_setting(f"recommended_{market}_{style}_at"),
        "interval": ctx.s["interval"],
        "markets": {k: v["name"] for k, v in MARKETS.items()},
        "styles": {k: v["name"] for k, v in STYLES.items()},
        "tuning": tuning,
        "session_active": _active_session_id() is not None,
        "demo_mode": demo_mode_enabled(),
    }


def action_scan(market: str = "jp", style: str = "day", tickers=None) -> dict:
    ctx = _ctx(market, style)
    tickers = tickers or get_scan_tickers(market, style)
    results = []

    for code in tickers:
        item = {"ticker": code}
        try:
            df = add_indicators(fetch_data(code, ctx), ctx)
        except Exception as e:
            item["error"] = f"データ取得失敗: {e}"
            results.append(item)
            continue
        if len(df) < ctx.s["slow"] + 6:
            item["error"] = "データ不足"
            results.append(item)
            continue

        r = evaluate(df, ctx)
        t = r["time"]
        time_str = t.strftime("%m/%d %H:%M") if hasattr(t, "strftime") else str(t)
        item.update({
            "signal": r["signal"],
            "price": r["price"],
            "rsi": r["rsi"],
            "atr": r["atr"],
            "time": time_str,
            "conds": [{"name": k, "ok": bool(v)} for k, v in r["conds"].items()],
        })
        if "vwap" in r:
            item["vwap"] = r["vwap"]
        if r["signal"] in ("買い", "売り"):
            p = plan_trade(r["price"], r["atr"], r["signal"], ctx)
            item["plan"] = {
                "stop": p["stop"], "target": p["target"], "qty": p["qty"],
                "risk_amount": p["risk_amount"],
            }
        results.append(item)

        sid = _active_session_id()
        if sid:
            _log_signal(sid, market, style, code, r)

    demo_entries = _process_demo_entries(market, style, results)
    if market == "jp" and demo_mode_enabled():
        _record_signal_lag_on_scan(results)
    return {
        "scanned_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "market": market,
        "style": style,
        "interval": ctx.s["interval"],
        "results": results,
        "demo_entries": demo_entries,
    }


def _record_signal_lag_on_scan(results: list):
    """買いシグナル検出時に出遅れコストをライブ記録"""
    try:
        import signal_lag as sl
        for item in results:
            if item.get("signal") != "買い" or item.get("error"):
                continue
            ticker = str(item["ticker"]).upper()
            name = item.get("name", "")
            sl.record_live_signal(ticker, name)
    except Exception:
        pass


def _process_demo_entries(market: str, style: str, results: list) -> list:
    """シグナル検出時にデモ建玉を自動作成"""
    if not demo_mode_enabled():
        return []
    entries = []
    for item in results:
        if item.get("signal") not in ("買い", "売り") or "plan" not in item:
            continue
        ticker = item["ticker"].upper()
        if _has_open_position(market, style, ticker):
            continue
        entered = _demo_open_position(
            market, style, ticker, item["signal"],
            item["price"], item["plan"], item.get("conds", []),
        )
        if entered:
            entries.append(entered)
    return entries


def _demo_open_position(market: str, style: str, ticker: str, side: str,
                        price: float, plan: dict, conds: list) -> dict | None:
    ctx = _ctx(market, style)
    qty = plan.get("qty") or ctx.m["unit"]
    if qty <= 0:
        qty = ctx.m["unit"]
    stop_val, target_val = plan.get("stop"), plan.get("target")
    conds_dict = {c["name"]: c["ok"] for c in conds} if conds else {}
    conds_json = json.dumps(conds_dict, ensure_ascii=False)
    sid = _active_session_id()

    with _db_lock:
        conn = _conn()
        conn.execute(
            """INSERT INTO positions
               (market,style,ticker,side,entry_time,entry_price,qty,
                stop_loss,take_profit,session_id,entry_conds_json,
                entry_signal,is_demo)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1)""",
            (market, style, ticker, side,
             datetime.now().strftime("%Y-%m-%d %H:%M"),
             price, qty, stop_val, target_val,
             sid, conds_json, side),
        )
        conn.commit()
        conn.close()

    lines = [f"[デモ] {ticker} {side}", f"価格: {ctx.fmt(price)}", f"数量: {qty}"]
    if stop_val:
        lines.append(f"損切り: {ctx.fmt(stop_val)}")
    if target_val:
        lines.append(f"利確: {ctx.fmt(target_val)}")
    send_notification("Demo Entry", "\n".join(lines))
    return {"ticker": ticker, "side": side, "price": price, "qty": qty}


def _close_position_row(row, exit_price: float, reason: str = "manual",
                        notify: bool = True) -> dict:
    """建玉を決済して trades に記録"""
    ctx = _ctx(row["market"], row["style"])
    entry = float(row["entry_price"])
    qty = int(row["qty"])
    sign = 1 if row["side"] == "買い" else -1
    pnl = (exit_price - entry) * qty * sign
    is_demo = int(row["is_demo"] if row["is_demo"] is not None else 0)

    followed = 0
    if row["entry_signal"] and row["side"]:
        followed = int(
            (row["side"] == "買い" and row["entry_signal"] == "買い") or
            (row["side"] == "売り" and row["entry_signal"] == "売り")
        )

    with _db_lock:
        conn = _conn()
        conn.execute(
            """INSERT INTO trades
               (market,style,ticker,side,entry_time,entry_price,qty,
                stop_loss,take_profit,exit_time,exit_price,pnl,result,
                session_id,entry_conds_json,entry_signal,followed_signal,
                is_demo,exit_reason)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (row["market"], row["style"], row["ticker"], row["side"],
             row["entry_time"], entry, qty, row["stop_loss"], row["take_profit"],
             datetime.now().strftime("%Y-%m-%d %H:%M"), exit_price, pnl,
             "win" if pnl > 0 else "loss",
             row["session_id"], row["entry_conds_json"],
             row["entry_signal"], followed, is_demo, reason),
        )
        conn.execute("DELETE FROM positions WHERE id=?", (row["id"],))
        conn.commit()
        conn.close()

    if notify:
        prefix = "[デモ] " if is_demo else ""
        send_notification(
            "Demo Exit" if is_demo else "Exit",
            "\n".join([
                f"{prefix}{row['ticker']} {row['side']} ({reason})",
                f"エントリー: {ctx.fmt(entry)}",
                f"決済: {ctx.fmt(exit_price)}",
                f"損益: {ctx.fmt_pnl(pnl)}",
            ]),
        )
    return {"ok": True, "ticker": row["ticker"], "pnl": pnl, "qty": qty,
            "is_demo": bool(is_demo), "reason": reason}


def _demo_check_exits() -> list:
    """デモ建玉の損切り・利確を自動決済"""
    closed = []
    with _db_lock:
        conn = _conn()
        rows = conn.execute(
            "SELECT * FROM positions WHERE is_demo=1"
        ).fetchall()
        conn.close()

    for row in rows:
        ctx = _ctx(row["market"], row["style"])
        try:
            cur = float(fetch_data(row["ticker"], ctx)["Close"].iloc[-1])
        except Exception:
            continue

        side = row["side"]
        reason = None
        exit_price = cur

        if row["stop_loss"]:
            stop = float(row["stop_loss"])
            if side == "買い" and cur <= stop:
                reason, exit_price = "損切り", stop
            elif side == "売り" and cur >= stop:
                reason, exit_price = "損切り", stop

        if not reason and row["take_profit"]:
            target = float(row["take_profit"])
            if side == "買い" and cur >= target:
                reason, exit_price = "利確", target
            elif side == "売り" and cur <= target:
                reason, exit_price = "利確", target

        if reason:
            result = _close_position_row(row, exit_price, reason=reason, notify=True)
            closed.append(result)
    return closed


def _close_all_demo_positions(market: str, style: str) -> list:
    """セッション終了時に残りデモ建玉を現値で決済"""
    closed = []
    with _db_lock:
        conn = _conn()
        rows = conn.execute(
            "SELECT * FROM positions WHERE is_demo=1 AND market=? AND style=?",
            (market, style),
        ).fetchall()
        conn.close()

    for row in rows:
        ctx = _ctx(market, style)
        try:
            cur = float(fetch_data(row["ticker"], ctx)["Close"].iloc[-1])
        except Exception:
            cur = float(row["entry_price"])
        closed.append(_close_position_row(
            row, cur, reason="セッション終了", notify=False,
        ))
    return closed


def action_start(market: str = "jp", style: str = "day", demo: bool = True,
                 tickers: list | None = None) -> dict:
    set_notification_enabled(True)
    _set_setting("demo_mode", "1" if demo else "0")
    _set_setting("session_market", market)
    _set_setting("session_style", style)
    _create_session(market, style)
    label = "デモ取引" if demo else "取引"
    message = f"{label}を開始します"
    if demo:
        message += "\nシグナル検出時に仮想売買を自動記録します"

    scan_tickers = None
    if tickers:
        scan_tickers = [str(t).upper() for t in tickers if t]
        _set_setting(f"recommended_{market}_{style}", json.dumps(scan_tickers))
        _set_setting(
            f"recommended_{market}_{style}_at",
            datetime.now().strftime("%Y-%m-%d %H:%M"),
        )

    recommend_summary = ""
    if scan_tickers:
        message += f"\nスキャン対象: {', '.join(scan_tickers)}"
        recommend_summary = ", ".join(scan_tickers[:3])
    else:
        try:
            import trade_recommend as tr
            rec = tr.action_recommend(market, style, limit=5)
            tops = rec.get("recommendations", [])
            if tops:
                lines = []
                for i, r in enumerate(tops[:3], 1):
                    code = r.get("ticker") or r.get("code", "")
                    sig = r.get("signal", "様子見")
                    lines.append(f"{i}.{code}({r.get('name', '')}) {sig}")
                recommend_summary = " / ".join(lines)
                message += "\nおすすめ: " + recommend_summary
                scan_tickers = [
                    r.get("ticker") or r.get("code")
                    for r in tops if r.get("ticker") or r.get("code")
                ]
        except Exception:
            pass

    send_notification("Trade Start", message, force=True)
    if demo:
        action_scan(market, style, scan_tickers)
    return {
        "ok": True, "message": message, "notify_enabled": True,
        "demo_mode": demo, "recommend_summary": recommend_summary,
        "scan_tickers": scan_tickers or get_scan_tickers(market, style),
    }


def action_end(market: str = "jp", style: str = "day") -> dict:
    demo_closed = _close_all_demo_positions(market, style)
    summary = _close_session(market, style)
    summary["demo_closed"] = len(demo_closed)
    message = "終わります"
    if summary["trades"]:
        ctx = _ctx(market, style)
        message += (
            f"\n本日: {summary['trades']}回 "
            f"勝{summary['wins']}敗{summary['losses']} "
            f"損益{ctx.fmt_pnl(summary['pnl'])}"
        )
    if summary.get("tuning_note"):
        message += f"\n精度調整: {summary['tuning_note']}"
    if demo_closed:
        demo_pnl = sum(d["pnl"] for d in demo_closed)
        ctx = _ctx(market, style)
        message += f"\nデモ決済: {len(demo_closed)}件 損益{ctx.fmt_pnl(demo_pnl)}"
    send_notification("Trade End", message, force=True)
    set_notification_enabled(False)
    _set_setting("demo_mode", "0")
    return {"ok": True, "message": message, "notify_enabled": False,
            "daily_summary": summary}


def action_notify_test() -> dict:
    ok = send_notification(
        "Test",
        "trade app からのテスト通知です",
        force=True,
    )
    return {"ok": ok, "message": "通知を送信しました" if ok else "通知失敗"}


def action_buy(market: str, style: str, ticker: str, price: float, qty: int,
               short: bool = False, stop=None, target=None) -> dict:
    ctx = _ctx(market, style)
    side = "売り" if short else "買い"
    ticker = ticker.upper()

    if stop is not None:
        stop_val, target_val = stop, target
    else:
        try:
            df = add_indicators(fetch_data(ticker, ctx), ctx)
            atr = float(df["atr"].iloc[-1])
            p = plan_trade(price, atr, side, ctx)
            stop_val, target_val = p["stop"], p["target"]
        except Exception:
            stop_val = target_val = None

    snap = _snapshot_entry(ctx, ticker, side)
    sid = _active_session_id()
    conds_json = json.dumps(snap["conds"], ensure_ascii=False)

    with _db_lock:
        conn = _conn()
        conn.execute(
            """INSERT INTO positions
               (market,style,ticker,side,entry_time,entry_price,qty,
                stop_loss,take_profit,session_id,entry_conds_json,entry_signal,is_demo)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0)""",
            (market, style, ticker, side,
             datetime.now().strftime("%Y-%m-%d %H:%M"),
             price, qty, stop_val, target_val,
             sid, conds_json, snap["signal"]),
        )
        conn.commit()
        conn.close()

    msg = [f"銘柄: {ticker}", f"売買: {side}", f"数量: {qty}",
           f"価格: {ctx.fmt(price)}", f"({ctx.m['name']}/{ctx.s['name']})"]
    if stop_val:
        msg.append(f"損切り: {ctx.fmt(stop_val)}")
    if target_val:
        msg.append(f"利確: {ctx.fmt(target_val)}")
    send_notification("Entry", "\n".join(msg))

    return {"ok": True, "ticker": ticker, "side": side, "qty": qty,
            "stop_loss": stop_val, "take_profit": target_val}


def _check_position_row(row, ctx: Ctx, notify: bool = True) -> dict:
    entry = float(row["entry_price"])
    qty = int(row["qty"])
    item = {
        "id": row["id"],
        "ticker": row["ticker"], "side": row["side"], "qty": qty,
        "entry_price": entry, "entry_time": row["entry_time"],
        "stop_loss": row["stop_loss"],
        "take_profit": row["take_profit"],
        "market": row["market"], "style": row["style"],
        "is_demo": bool(row["is_demo"] if row["is_demo"] is not None else 0),
    }
    try:
        cur = float(fetch_data(row["ticker"], ctx)["Close"].iloc[-1])
        sign = 1 if row["side"] == "買い" else -1
        pnl = (cur - entry) * qty * sign
        item["current_price"] = cur
        item["pnl"] = pnl
        if row["stop_loss"]:
            stop = float(row["stop_loss"])
            hit = cur <= stop if row["side"] == "買い" else cur >= stop
            item["stop_hit"] = hit
            if hit and notify and not row["stop_alerted"]:
                is_demo = bool(row["is_demo"] if row["is_demo"] is not None else 0)
                if is_demo:
                    _close_position_row(row, stop, reason="損切り", notify=True)
                    item["closed"] = True
                else:
                    send_notification(
                        "Stop Loss",
                        "\n".join([
                            f"銘柄: {row['ticker']}", f"売買: {row['side']}",
                            f"数量: {qty}株", f"現値: {ctx.fmt(cur)}",
                            f"損切り: {ctx.fmt(stop)}", f"含み損益: {ctx.fmt_pnl(pnl)}",
                        ]),
                        priority="urgent",
                    )
                    with _db_lock:
                        conn = _conn()
                        conn.execute(
                            "UPDATE positions SET stop_alerted=1 WHERE id=?",
                            (row["id"],),
                        )
                        conn.commit()
                        conn.close()
    except Exception:
        item["error"] = "現値取得失敗"
    return item


def action_positions(market: str = "jp", style: str = "day") -> dict:
    ctx = _ctx(market, style)
    with _db_lock:
        conn = _conn()
        rows = conn.execute(
            "SELECT * FROM positions WHERE market=? AND style=? ORDER BY id",
            (market, style),
        ).fetchall()
        conn.close()

    positions = [_check_position_row(r, ctx) for r in rows]
    return {"positions": positions, "count": len(positions),
            "market": market, "style": style,
            "currency": ctx.m["currency"]}


def action_sell(market: str, style: str, ticker: str, price: float) -> dict:
    ctx = _ctx(market, style)
    ticker = ticker.upper()

    with _db_lock:
        conn = _conn()
        row = conn.execute(
            "SELECT * FROM positions WHERE market=? AND style=? AND ticker=? LIMIT 1",
            (market, style, ticker),
        ).fetchone()
        if not row:
            conn.close()
            return {"ok": False, "error": f"{ticker} の建玉が見つかりません"}
        conn.close()

    result = _close_position_row(row, price, reason="手動決済", notify=True)
    result["currency"] = ctx.m["currency"]
    return result


def action_review(market: str = "jp", style: str = "day") -> dict:
    ctx = _ctx(market, style)
    with _db_lock:
        conn = _conn()
        rows = conn.execute(
            "SELECT * FROM trades WHERE market=? AND style=? ORDER BY id",
            (market, style),
        ).fetchall()
        conn.close()

    if not rows:
        return {"ok": True, "count": 0,
                "message": "決済済みトレードがまだありません",
                "market": market, "style": style}

    demo_rows = [r for r in rows if r["is_demo"]]
    real_rows = [r for r in rows if not r["is_demo"]]

    def _stats(subset):
        if not subset:
            return None
        pnls = [float(r["pnl"]) for r in subset]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        total = sum(pnls)
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        return {
            "count": len(pnls),
            "total_pnl": total,
            "win_rate": len(wins) / len(pnls) * 100,
            "wins": len(wins),
            "losses": len(losses),
            "expectancy": total / len(pnls),
            "profit_factor": gross_win / gross_loss if gross_loss > 0 else None,
        }

    all_stats = _stats(rows)
    by_ticker = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], []).append(float(r["pnl"]))

    return {
        "ok": True,
        "count": all_stats["count"],
        "total_pnl": all_stats["total_pnl"],
        "win_rate": all_stats["win_rate"],
        "wins": all_stats["wins"],
        "losses": all_stats["losses"],
        "avg_win": (sum(float(r["pnl"]) for r in rows if float(r["pnl"]) > 0) /
                    all_stats["wins"]) if all_stats["wins"] else None,
        "avg_loss": (abs(sum(float(r["pnl"]) for r in rows if float(r["pnl"]) <= 0)) /
                     all_stats["losses"]) if all_stats["losses"] else None,
        "profit_factor": all_stats["profit_factor"],
        "expectancy": all_stats["expectancy"],
        "demo_stats": _stats(demo_rows),
        "real_stats": _stats(real_rows),
        "currency": ctx.m["currency"],
        "market": market,
        "style": style,
        "by_ticker": [
            {"ticker": code, "pnl": sum(ps), "trades": len(ps)}
            for code, ps in sorted(by_ticker.items(), key=lambda x: -sum(x[1]))
        ],
        "recent": [
            {"exit_time": r["exit_time"], "ticker": r["ticker"],
             "side": r["side"], "pnl": float(r["pnl"]),
             "is_demo": bool(r["is_demo"]), "exit_reason": r["exit_reason"]}
            for r in rows[-8:]
        ],
    }


def action_validate(market: str = "jp", style: str = "day") -> dict:
    try:
        import trade_analytics as ta
        return ta.analyze_performance(market, style)
    except ImportError:
        return {"ok": False, "error": "analytics module not found"}


def action_journal(market: str = "jp", style: str = "day") -> dict:
    with _db_lock:
        conn = _conn()
        rows = conn.execute(
            """SELECT * FROM daily_sessions
               WHERE market=? AND style=? ORDER BY id DESC LIMIT 30""",
            (market, style),
        ).fetchall()
        conn.close()
    sessions = [dict(r) for r in rows]
    return {"ok": True, "sessions": sessions, "market": market, "style": style}


def action_monitor_all() -> dict:
    """損切り監視 + デモの自動スキャン・決済"""
    if not notification_enabled():
        return {"ok": True, "checked": 0, "alerts": 0, "skipped": "notify_off"}

    demo_closed = _demo_check_exits()
    demo_entries = []
    if demo_mode_enabled():
        market, style = _session_context()
        scan = action_scan(market, style)
        demo_entries = scan.get("demo_entries", [])

    with _db_lock:
        conn = _conn()
        rows = conn.execute("SELECT * FROM positions WHERE is_demo=0 OR is_demo IS NULL").fetchall()
        conn.close()

    alerts = 0
    for row in rows:
        ctx = _ctx(row["market"], row["style"])
        before = row["stop_alerted"]
        item = _check_position_row(row, ctx, notify=True)
        if item.get("stop_hit") and not before and not item.get("closed"):
            alerts += 1

    return {
        "ok": True, "checked": len(rows), "alerts": alerts,
        "demo_closed": len(demo_closed), "demo_entries": len(demo_entries),
        "notify_enabled": True,
    }


# 起動時にDB初期化
init_db()

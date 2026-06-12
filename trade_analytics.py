#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""取引記録の検証とシグナル精度の自動改善"""

import json
from collections import defaultdict
from datetime import datetime

import trade_core as tc

MIN_TRADES_FOR_TUNE = 5


def _ensure_score_history():
    with tc._db_lock:
        conn = tc._conn()
        conn.execute("""CREATE TABLE IF NOT EXISTS score_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT, style TEXT,
            changed_at TEXT,
            old_min_score INTEGER,
            new_min_score INTEGER,
            reason TEXT
        )""")
        conn.commit()
        conn.close()


def log_score_change(market: str, style: str, old_min: int, new_min: int, reason: str):
    _ensure_score_history()
    with tc._db_lock:
        conn = tc._conn()
        conn.execute(
            """INSERT INTO score_history
               (market, style, changed_at, old_min_score, new_min_score, reason)
               VALUES (?,?,?,?,?,?)""",
            (market, style, datetime.now().strftime("%Y-%m-%d %H:%M"),
             old_min, new_min, reason),
        )
        conn.commit()
        conn.close()


def _tuning_key(market: str, style: str) -> str:
    return f"tuning_{market}_{style}"


def load_tuning(market: str, style: str) -> dict:
    raw = tc._get_setting(_tuning_key(market, style))
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def save_tuning(market: str, style: str, tuning: dict):
    tuning["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    tc._set_setting(_tuning_key(market, style), json.dumps(tuning, ensure_ascii=False))


def get_effective_params(market: str, style: str) -> dict:
    """evaluate() 用の有効パラメータ"""
    tuning = load_tuning(market, style)
    return {
        "min_score": tuning.get("min_score", tc.COMMON["min_score"]),
        "vol_surge_ratio": tuning.get("vol_surge_ratio", tc.STYLES[style]["vol_surge_ratio"]),
        "require_volume": tuning.get("require_volume", False),
    }


def _fetch_trades_with_meta(market: str, style: str):
    with tc._db_lock:
        conn = tc._conn()
        rows = conn.execute(
            """SELECT * FROM trades
               WHERE market=? AND style=? ORDER BY id""",
            (market, style),
        ).fetchall()
        conn.close()
    return rows


def analyze_performance(market: str, style: str) -> dict:
    rows = _fetch_trades_with_meta(market, style)
    if not rows:
        return {"ok": True, "count": 0, "message": "検証用の取引データがまだありません"}

    cond_stats = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0.0})
    signal_follow = {"followed": {"wins": 0, "losses": 0}, "other": {"wins": 0, "losses": 0}}
    daily = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "wins": 0})

    for r in rows:
        pnl = float(r["pnl"])
        win = pnl > 0
        day = (r["exit_time"] or "")[:10]
        daily[day]["pnl"] += pnl
        daily[day]["trades"] += 1
        if win:
            daily[day]["wins"] += 1

        bucket = "followed" if r["followed_signal"] else "other"
        if win:
            signal_follow[bucket]["wins"] += 1
        else:
            signal_follow[bucket]["losses"] += 1

        conds_raw = r["entry_conds_json"]
        if not conds_raw:
            continue
        try:
            conds = json.loads(conds_raw)
        except json.JSONDecodeError:
            continue
        for name, ok in conds.items():
            if not ok:
                continue
            if win:
                cond_stats[name]["wins"] += 1
            else:
                cond_stats[name]["losses"] += 1
            cond_stats[name]["pnl"] += pnl

    total = len(rows)
    wins = sum(1 for r in rows if float(r["pnl"]) > 0)
    win_rate = wins / total * 100

    condition_ranking = []
    for name, s in cond_stats.items():
        n = s["wins"] + s["losses"]
        if n == 0:
            continue
        condition_ranking.append({
            "condition": name,
            "trades": n,
            "win_rate": s["wins"] / n * 100,
            "pnl": s["pnl"],
        })
    condition_ranking.sort(key=lambda x: (-x["win_rate"], -x["trades"]))

    recommendations = _build_recommendations(
        market, style, win_rate, condition_ranking, signal_follow, total,
    )

    journal = [
        {"date": d, "pnl": v["pnl"], "trades": v["trades"],
         "wins": v["wins"], "win_rate": v["wins"] / v["trades"] * 100 if v["trades"] else 0}
        for d, v in sorted(daily.items(), reverse=True)
    ]

    tuning = load_tuning(market, style)

    ctx = tc.Ctx(market, style)
    return {
        "ok": True,
        "count": total,
        "win_rate": win_rate,
        "currency": ctx.m["currency"],
        "condition_ranking": condition_ranking[:8],
        "signal_follow": signal_follow,
        "daily_journal": journal[:30],
        "recommendations": recommendations,
        "current_tuning": tuning,
        "market": market,
        "style": style,
    }


def _build_recommendations(market, style, win_rate, cond_rank, signal_follow, total):
    recs = []
    tuning = load_tuning(market, style)
    min_score = tuning.get("min_score", tc.COMMON["min_score"])

    if total < MIN_TRADES_FOR_TUNE:
        recs.append(f"あと{MIN_TRADES_FOR_TUNE - total}回の取引で自動チューニングが有効になります")
        return recs

    if win_rate < 45:
        recs.append("勝率が低めです。シグナル条件を厳しくすることを検討しています")
    elif win_rate > 60:
        recs.append("好調です。現在の設定を維持します")

    if cond_rank:
        best = cond_rank[0]
        worst = cond_rank[-1] if len(cond_rank) > 1 else None
        if best["win_rate"] >= 55 and best["trades"] >= 3:
            recs.append(f"有効な条件: 「{best['condition']}」勝率{best['win_rate']:.0f}%")
        if worst and worst["win_rate"] < 40 and worst["trades"] >= 3:
            recs.append(f"弱い条件: 「{worst['condition']}」勝率{worst['win_rate']:.0f}%")

    fol = signal_follow["followed"]
    oth = signal_follow["other"]
    fol_n = fol["wins"] + fol["losses"]
    fol_wr = None
    if fol_n >= 3:
        fol_wr = fol["wins"] / fol_n * 100
        recs.append(f"シグナル通りの取引 勝率{fol_wr:.0f}% ({fol_n}回)")
    oth_n = oth["wins"] + oth["losses"]
    if oth_n >= 3 and fol_n >= 3 and fol_wr is not None:
        oth_wr = oth["wins"] / oth_n * 100
        if fol_wr > oth_wr + 10:
            recs.append("シグナルに従った取引の方が成績が良いです")

    if min_score > tc.COMMON["min_score"]:
        recs.append(f"現在 min_score={min_score} に厳格化中")

    return recs


def optimize_and_apply(market: str, style: str) -> dict:
    """過去取引を検証し、パラメータを自動調整"""
    rows = _fetch_trades_with_meta(market, style)
    if len(rows) < MIN_TRADES_FOR_TUNE:
        return {
            "ok": True, "applied": False,
            "reason": f"取引数不足 ({len(rows)}/{MIN_TRADES_FOR_TUNE})",
        }

    analysis = analyze_performance(market, style)
    tuning = load_tuning(market, style)
    changes = []

    win_rate = analysis["win_rate"]
    old_min = tuning.get("min_score", tc.COMMON["min_score"])
    old_vol = tuning.get("vol_surge_ratio", tc.STYLES[style]["vol_surge_ratio"])
    require_volume = tuning.get("require_volume", False)

    if win_rate < 40:
        new_min = min(4, old_min + 1) if old_min < 4 else 4
        if new_min != old_min:
            tuning["min_score"] = new_min
            changes.append(f"min_score {old_min}→{new_min} (勝率{win_rate:.0f}%のため厳格化)")
    elif win_rate > 65 and old_min > tc.COMMON["min_score"]:
        new_min = old_min - 1
        tuning["min_score"] = new_min
        changes.append(f"min_score {old_min}→{new_min} (好調のため緩和)")

    vol_conds = [c for c in analysis["condition_ranking"]
                 if "出来高" in c["condition"]]
    if vol_conds:
        vc = vol_conds[0]
        if vc["win_rate"] < 35 and vc["trades"] >= 3:
            tuning["require_volume"] = True
            new_vol = round(old_vol + 0.2, 1)
            tuning["vol_surge_ratio"] = new_vol
            changes.append(f"出来高条件を強化 (比率{old_vol}→{new_vol})")
        elif vc["win_rate"] > 60 and require_volume:
            tuning["require_volume"] = False
            changes.append("出来高必須を解除 (成績良好)")

    if changes:
        tuning["last_reason"] = "; ".join(changes)
        if tuning.get("min_score") != old_min:
            log_score_change(market, style, old_min, tuning.get("min_score", old_min),
                             changes[0] if changes else "")
        save_tuning(market, style, tuning)
        return {"ok": True, "applied": True, "changes": changes, "tuning": tuning}

    return {"ok": True, "applied": False, "reason": "調整不要", "tuning": tuning}


def analyze_condition_stats(market: str, style: str) -> dict:
    """条件別勝率・RSI帯・スコア帯の集計（Phase 9）"""
    rows = _fetch_trades_with_meta(market, style)
    cross_stats = {"with_vol": {"w": 0, "l": 0}, "no_vol": {"w": 0, "l": 0}}
    rsi_bands = {
        "lt30": {"w": 0, "l": 0}, "30_50": {"w": 0, "l": 0},
        "50_70": {"w": 0, "l": 0}, "gt70": {"w": 0, "l": 0},
    }
    score_bands = {"low": {"w": 0, "l": 0}, "mid": {"w": 0, "l": 0}, "high": {"w": 0, "l": 0}}

    for r in rows:
        win = float(r["pnl"]) > 0
        bucket = "w" if win else "l"
        conds_raw = r["entry_conds_json"]
        has_cross = has_vol = False
        if conds_raw:
            try:
                conds = json.loads(conds_raw)
                for name, ok in conds.items():
                    if not ok:
                        continue
                    if "クロス" in name:
                        has_cross = True
                    if "出来高" in name:
                        has_vol = True
            except json.JSONDecodeError:
                pass
        if has_cross:
            key = "with_vol" if has_vol else "no_vol"
            cross_stats[key][bucket] += 1

    with tc._db_lock:
        conn = tc._conn()
        sig_rows = conn.execute(
            """SELECT conditions_json, signal FROM signal_logs
               WHERE market=? AND style=? AND signal IN ('買い','売り')""",
            (market, style),
        ).fetchall()
        conn.close()

    for sr in sig_rows:
        try:
            cj = json.loads(sr["conditions_json"] or "{}")
        except json.JSONDecodeError:
            continue
        rsi = float(cj.get("rsi", 50))
        score = int(cj.get("score", 0))
        if rsi < 30:
            band = "lt30"
        elif rsi < 50:
            band = "30_50"
        elif rsi < 70:
            band = "50_70"
        else:
            band = "gt70"
        rsi_bands[band]["w"] += 1  # signal occurrence count

    def _wr(d):
        n = d["w"] + d["l"]
        return {"trades": n, "win_rate": round(d["w"] / n * 100, 1) if n else None,
                "wins": d["w"], "losses": d["l"]}

    _ensure_score_history()
    with tc._db_lock:
        conn = tc._conn()
        history = [dict(row) for row in conn.execute(
            """SELECT changed_at, old_min_score, new_min_score, reason
               FROM score_history WHERE market=? AND style=?
               ORDER BY id DESC LIMIT 10""",
            (market, style),
        ).fetchall()]
        conn.close()

    tuning = load_tuning(market, style)
    return {
        "ok": True,
        "market": market,
        "style": style,
        "cross_volume": {
            "with_vol": _wr(cross_stats["with_vol"]),
            "no_vol": _wr(cross_stats["no_vol"]),
        },
        "rsi_bands": {k: {"signals": v["w"]} for k, v in rsi_bands.items()},
        "current_min_score": tuning.get("min_score", tc.COMMON["min_score"]),
        "score_history": history,
        "trade_count": len(rows),
    }

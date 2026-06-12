#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""週次レポート通知（毎週日曜18:00 JST）"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def weekly_report_enabled() -> bool:
    try:
        import trade_core as tc
        return tc._get_setting("weekly_report_enabled", "1") == "1"
    except Exception:
        return True


def should_send_now(dt: datetime | None = None) -> bool:
    if not weekly_report_enabled():
        return False
    dt = dt or datetime.now(JST)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    else:
        dt = dt.astimezone(JST)
    if dt.weekday() != 6:  # Sunday
        return False
    return dt.hour == 18 and dt.minute < 10


def _already_sent_this_week(dt: datetime) -> bool:
    try:
        import trade_core as tc
        key = f"weekly_report_{dt.strftime('%Y-W%W')}"
        return tc._get_setting(key) == "1"
    except Exception:
        return False


def _mark_sent(dt: datetime):
    try:
        import trade_core as tc
        key = f"weekly_report_{dt.strftime('%Y-W%W')}"
        tc._set_setting(key, "1")
    except Exception:
        pass


def build_report(market: str = "jp", style: str = "day") -> str:
    import trade_core as tc
    lines = ["=== 週次レポート ==="]

    demo = tc.action_performance(market, style, mode="demo")
    manual = tc.action_performance(market, style, mode="manual")
    ds = demo.get("stats")
    ms = manual.get("stats")
    if ds and ds.get("count"):
        lines.append(f"デモ: {ds['count']}回 勝率{ds['win_rate']:.1f}% 損益{ds['total_pnl']:,.0f}")
    else:
        lines.append("デモ: 今週の決済なし")
    if ms and ms.get("count"):
        lines.append(f"手動: {ms['count']}回 勝率{ms['win_rate']:.1f}% 損益{ms['total_pnl']:,.0f}")

    try:
        with tc._db_lock:
            conn = tc._conn()
            sig_cnt = conn.execute(
                """SELECT COUNT(*) FROM signal_logs
                   WHERE market=? AND style=? AND signal IN ('買い','売り')
                   AND logged_at >= date('now', '-7 days')""",
                (market, style),
            ).fetchone()[0]
            conn.close()
        lines.append(f"シグナル: 過去7日 {sig_cnt}件")
    except Exception:
        pass

    try:
        import signal_lag as sl
        sl.update_pending()
        rep = sl.get_lag_report()
        for r in rep.get("rules", []):
            if r.get("rule") == "EMA9/21" and r.get("settled"):
                lines.append(
                    f"EMA9/21: 勝率{r.get('win_rate')}% 20日平均{r.get('avg_fwd20')}%"
                )
    except Exception:
        pass

    try:
        if market == "jp":
            from recommend_news import get_recommendations
            rec = get_recommendations()
            themes = rec.get("themes", [])[:3]
            if themes:
                lines.append("来週の注目テーマ:")
                for t in themes:
                    lines.append(f"  - {t.get('theme')}")
    except Exception:
        pass

    try:
        import trade_analytics as ta
        opt = ta.optimize_and_apply(market, style)
        if opt.get("applied"):
            lines.append("改善提案: " + "; ".join(opt.get("changes", [])[:1]))
    except Exception:
        pass

    return "\n".join(lines)


def maybe_send_weekly_report(market: str = "jp", style: str = "day") -> dict:
    dt = datetime.now(JST)
    if not should_send_now(dt):
        return {"ok": True, "sent": False}
    if _already_sent_this_week(dt):
        return {"ok": True, "sent": False, "reason": "already_sent"}

    body = build_report(market, style)
    try:
        import notifier as nf
        ok = nf.notify("weekly", "Weekly Report", body)
    except ImportError:
        import trade_core as tc
        ok = tc.send_notification("Weekly Report", body, force=True, kind="weekly")
    if ok:
        _mark_sent(dt)
    return {"ok": True, "sent": ok, "body": body}

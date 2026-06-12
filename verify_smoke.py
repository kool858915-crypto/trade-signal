#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""手動スモークテスト（IMPLEMENTATION_PLAN 全体ルール #3）"""

import sys


def main() -> int:
    errors = []
    modules = [
        "trade_core", "server", "market_hours", "notifier",
        "signal_detect", "signal_lag", "backtest", "weekly_report",
        "trade_analytics", "trade_news", "app_integration",
    ]
    for name in modules:
        try:
            __import__(name)
        except Exception as e:
            errors.append(f"import {name}: {e}")

    try:
        import trade_core as tc
        tc.init_db()
        assert tc.action_settings_get()["ok"]
        assert tc.action_watchlist("jp")["ok"]
        assert tc.action_signal_history("jp", "day", limit=5)["ok"]
        assert tc.action_performance("jp", "day")["ok"]
    except Exception as e:
        errors.append(f"trade_core api: {e}")

    try:
        import trade_analytics as ta
        ta.analyze_condition_stats("jp", "day")
    except Exception as e:
        errors.append(f"condition_stats: {e}")

    try:
        import market_hours as mh
        assert mh.market_phase("jp") in (
            "pre_open", "morning", "lunch", "afternoon",
            "closing", "after_close", "closed",
        )
    except Exception as e:
        errors.append(f"market_hours: {e}")

    if errors:
        print("FAIL")
        for err in errors:
            print(" -", err)
        return 1
    print("OK: smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ntfy 通知規約（Phase 4）"""

import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# kind -> (prefix, tags, priority ntfy 1-5)
NOTIFY_RULES = {
    "production_signal": ("[本番]", "chart_with_upwards_trend", "4"),
    "demo_entry": ("[デモ]", "robot", "3"),
    "demo_exit": ("[デモ]", "robot", "3"),
    "stop_warning": ("[注意]", "warning", "4"),
    "stop_executed": ("[注意]", "rotating_light", "5"),
    "session": ("", "information_source", "3"),
    "weekly": ("[週報]", "calendar", "2"),
    "system": ("[システム]", "wrench", "4"),
    "entry": ("[本番]", "chart_with_upwards_trend", "4"),
    "exit": ("[本番]", "chart_with_upwards_trend", "3"),
    "test": ("[テスト]", "test_tube", "3"),
}

NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "my-trade-alerts-x7k2")


def _get_setting(key: str, default: str = "1") -> str:
    try:
        import trade_core as tc
        return tc._get_setting(key, default) or default
    except Exception:
        return default


def notification_enabled() -> bool:
    return _get_setting("notify_enabled", "0") == "1"


def demo_notify_enabled() -> bool:
    return _get_setting("demo_notify_enabled", "1") == "1"


def alerts_only_mode() -> bool:
    """[注意]のみモード（Phase 8 先行キー）"""
    return _get_setting("alerts_only", "0") == "1"


def _http_post(url: str, data: bytes, headers: dict) -> bool:
    req = Request(url, data=data, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=10):
            return True
    except (HTTPError, URLError):
        return False


def should_send(kind: str, force: bool = False) -> bool:
    if force:
        return True
    if not notification_enabled():
        return False
    if alerts_only_mode() and kind not in (
        "stop_warning", "stop_executed", "production_signal", "system",
    ):
        return False
    if kind in ("demo_entry", "demo_exit") and not demo_notify_enabled():
        return False
    return True


def notify(kind: str, title: str, message: str, force: bool = False,
           priority_override: str | None = None) -> bool:
    if not should_send(kind, force=force):
        return False
    if not NTFY_TOPIC:
        return False

    prefix, tags, priority = NOTIFY_RULES.get(kind, ("", "bell", "3"))
    body_title = f"{prefix} {title}".strip() if prefix else title
    safe_title = body_title.encode("ascii", "ignore").decode() or "Trade Alert"
    headers = {
        "Title": safe_title,
        "Tags": tags,
        "Priority": priority_override or priority,
    }
    return _http_post(
        f"{NTFY_SERVER}/{NTFY_TOPIC}",
        message.encode("utf-8"),
        headers,
    )


def notify_test(kind: str = "test") -> bool:
    rule = NOTIFY_RULES.get(kind, NOTIFY_RULES["test"])
    return notify(
        kind,
        "Notify Test",
        f"種別: {kind}\ntags: {rule[1]}\npriority: {rule[2]}",
        force=True,
    )

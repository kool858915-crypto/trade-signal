#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""東証・NYSE の市場時間判定"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ET = ZoneInfo("America/New_York")

TSE_MORNING_OPEN = time(9, 0)
TSE_MORNING_CLOSE = time(11, 30)
TSE_AFTERNOON_OPEN = time(12, 30)
TSE_AFTERNOON_CLOSE = time(15, 30)
NYSE_OPEN = time(9, 30)
NYSE_CLOSE = time(16, 0)


def _as_jst(dt: datetime | None) -> datetime:
    dt = dt or datetime.now(JST)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=JST)
    return dt.astimezone(JST)


def _as_et(dt: datetime | None) -> datetime:
    dt = dt or datetime.now(ET)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=ET)
    return dt.astimezone(ET)


def _is_year_end(d: date) -> bool:
    return (d.month == 12 and d.day == 31) or (d.month == 1 and d.day <= 3)


def is_jp_holiday(d: date) -> bool:
    if _is_year_end(d):
        return True
    try:
        import jpholiday
        return bool(jpholiday.is_holiday(d))
    except ImportError:
        return False


def is_weekday(d: date) -> bool:
    return d.weekday() < 5


def is_tse_open(dt: datetime | None = None) -> bool:
    dt = _as_jst(dt)
    d = dt.date()
    if not is_weekday(d) or is_jp_holiday(d):
        return False
    t = dt.time()
    if TSE_MORNING_OPEN <= t < TSE_MORNING_CLOSE:
        return True
    if TSE_AFTERNOON_OPEN <= t < TSE_AFTERNOON_CLOSE:
        return True
    return False


def is_nyse_open(dt: datetime | None = None) -> bool:
    dt = _as_et(dt)
    d = dt.date()
    if not is_weekday(d):
        return False
    t = dt.time()
    return NYSE_OPEN <= t < NYSE_CLOSE


def should_scan(market: str = "jp", dt: datetime | None = None) -> bool:
    if market == "us":
        return is_nyse_open(dt)
    return is_tse_open(dt)


def market_phase(market: str = "jp", dt: datetime | None = None) -> str:
    """pre_open / morning / lunch / afternoon / closing / after_close / closed"""
    if market == "us":
        return _nyse_phase(dt)
    return _tse_phase(dt)


def _tse_phase(dt: datetime | None) -> str:
    dt = _as_jst(dt)
    d, t = dt.date(), dt.time()
    if not is_weekday(d) or is_jp_holiday(d):
        return "closed"
    if t < time(8, 0):
        return "closed"
    if time(8, 0) <= t < TSE_MORNING_OPEN:
        return "pre_open"
    if TSE_MORNING_OPEN <= t < time(9, 30):
        return "morning"
    if time(9, 30) <= t < TSE_MORNING_CLOSE:
        return "afternoon"
    if TSE_MORNING_CLOSE <= t < TSE_AFTERNOON_OPEN:
        return "lunch"
    if TSE_AFTERNOON_OPEN <= t < time(15, 0):
        return "afternoon"
    if time(15, 0) <= t < TSE_AFTERNOON_CLOSE:
        return "closing"
    return "after_close"


def _nyse_phase(dt: datetime | None) -> str:
    dt = _as_et(dt)
    d, t = dt.date(), dt.time()
    if not is_weekday(d):
        return "closed"
    if t < time(9, 0):
        return "pre_open"
    if time(9, 0) <= t < NYSE_OPEN:
        return "pre_open"
    if NYSE_OPEN <= t < time(10, 0):
        return "morning"
    if time(10, 0) <= t < time(15, 30):
        return "afternoon"
    if time(15, 30) <= t < NYSE_CLOSE:
        return "closing"
    return "after_close"


def notify_priority_boost(market: str = "jp", dt: datetime | None = None) -> bool:
    """寄付直後・大引け前は通知優先度を上げる"""
    return market_phase(market, dt) in ("morning", "closing")


def monitor_sleep_sec(market: str = "jp", open_interval: int = 300) -> int:
    """開場中は通常間隔、閉場中は長めにスリープ"""
    if should_scan(market):
        return max(60, int(open_interval))
    return 900

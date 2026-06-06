"""
Chinese date parsing utilities for crawler data.

Handles common date formats found on Chinese government procurement websites:
- ISO 8601: 2024-06-15, 2024-06-15T10:30:00
- Chinese numeric: 2024年06月15日, 2024年6月15日
- Slash/dot separated: 2024/06/15, 2024.06.15
- Compact: 20240615
- Relative: 今天, 昨天, 3天前, 刚刚
"""

import re
from datetime import datetime, timedelta, date
from typing import Optional, Union


_CN_NUM = {
    "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    "十": 10, "十一": 11, "十二": 12,
}

_CN_RELATIVE = {
    "今天": 0,
    "昨天": 1,
    "前天": 2,
    "刚刚": 0,
    "今日": 0,
    "昨日": 1,
}


def parse_date(text: str) -> Optional[datetime]:
    """Parse a date/time string into a datetime object.

    Returns None if parsing fails.
    """
    if not text or not isinstance(text, str):
        return None

    text = text.strip()

    # ---- ISO 8601 with timezone / time ----
    # 2024-06-15T10:30:00+08:00, 2024-06-15 10:30:00
    m = re.match(
        r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})[T ](\d{1,2}):(\d{2})(?::(\d{2}))?",
        text,
    )
    if m:
        y, mo, d, h, mi, s = m.groups()
        try:
            return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0))
        except ValueError:
            pass

    # ---- Chinese format: 2024年06月15日 10:30 ----
    m = re.match(
        r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?\s*(\d{1,2}):(\d{2})(?::(\d{2}))?",
        text,
    )
    if m:
        y, mo, d, h, mi, s = m.groups()
        try:
            return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0))
        except ValueError:
            pass

    # ---- Chinese date only: 2024年06月15日 ----
    m = re.match(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?", text)
    if m:
        y, mo, d = m.groups()
        try:
            return datetime(int(y), int(mo), int(d))
        except ValueError:
            pass

    # ---- Plain date: 2024-06-15, 2024/06/15, 2024.06.15 ----
    m = re.match(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$", text)
    if m:
        y, mo, d = m.groups()
        try:
            return datetime(int(y), int(mo), int(d))
        except ValueError:
            pass

    # ---- Compact: 20240615 ----
    m = re.match(r"(\d{4})(\d{2})(\d{2})$", text)
    if m:
        y, mo, d = m.groups()
        try:
            return datetime(int(y), int(mo), int(d))
        except ValueError:
            pass

    # ---- Relative dates ----
    if text in _CN_RELATIVE:
        return datetime.combine(
            date.today() - timedelta(days=_CN_RELATIVE[text]),
            datetime.min.time(),
        )

    # ---- N天前, N小时前, N分钟前 ----
    m = re.match(r"(\d+)\s*天前", text)
    if m:
        return datetime.combine(
            date.today() - timedelta(days=int(m.group(1))),
            datetime.min.time(),
        )
    m = re.match(r"(\d+)\s*小时前", text)
    if m:
        return datetime.now() - timedelta(hours=int(m.group(1)))
    m = re.match(r"(\d+)\s*分钟前", text)
    if m:
        return datetime.now() - timedelta(minutes=int(m.group(1)))

    return None


def is_within_days(dt: Union[datetime, str], max_days: int) -> bool:
    """Return True if *dt* is within *max_days* days from now."""
    if isinstance(dt, str):
        dt = parse_date(dt)
    if dt is None:
        return False
    return (datetime.now() - dt).days <= max_days


def format_date(dt: datetime, fmt: str = "%Y-%m-%d") -> str:
    """Format a datetime to string with given format."""
    return dt.strftime(fmt)

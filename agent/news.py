"""Enforce the recent-news window in code, independent of what the model returns."""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from typing import Any

NEWS_WINDOW_DAYS = 90
MAX_NEWS_ITEMS = 15

_FULL_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%m/%d/%Y")
_MONTH_FORMATS = ("%Y-%m", "%B %Y", "%b %Y")


def _parse(value: Any) -> tuple[date | None, bool]:
    """Return (date, is_exact_day)."""
    if not value or not isinstance(value, str):
        return None, False
    s = value.strip()
    if "T" in s[:11]:
        s = s.split("T", 1)[0]
    for fmt in _FULL_FORMATS:
        try:
            return datetime.strptime(s, fmt).date(), True
        except ValueError:
            pass
    for fmt in _MONTH_FORMATS:
        try:
            d = datetime.strptime(s, fmt).date()
            return d.replace(day=calendar.monthrange(d.year, d.month)[1]), False
        except ValueError:
            pass
    return None, False


def parse_news_date(value: Any) -> date | None:
    """Parse common date strings. Month-only dates resolve to the month's last day
    (lenient: a story from 'October' counts if any part of October is in the window)."""
    return _parse(value)[0]


def news_cutoff(today: date | None = None, days: int = NEWS_WINDOW_DAYS) -> date:
    return (today or date.today()) - timedelta(days=days)


def filter_recent_news(
    items: list[dict[str, Any]],
    today: date | None = None,
    days: int = NEWS_WINDOW_DAYS,
    limit: int = MAX_NEWS_ITEMS,
) -> tuple[list[dict[str, Any]], int]:
    """Keep items dated within the window, newest first, capped at `limit`.

    Items with a missing/unparseable (or future) date are kept but flagged
    `date_unverified=True` and sorted last. Duplicate URLs/headlines are dropped.
    Returns (kept_items, number_dropped_as_too_old).
    """
    today = today or date.today()
    cutoff = news_cutoff(today, days)
    dated: list[tuple[date, dict]] = []
    undated: list[dict] = []
    dropped_old = 0
    seen: set[str] = set()

    for item in items or []:
        if not isinstance(item, dict):
            continue
        key = (item.get("url") or item.get("headline") or "").strip().lower()
        if key and key in seen:
            continue
        seen.add(key)
        d, exact = _parse(item.get("date"))
        if d is None or d > today + timedelta(days=1):
            undated.append({**item, "date_unverified": True})
        elif d < cutoff:
            dropped_old += 1
        else:
            shown = d.isoformat() if exact else d.strftime("%Y-%m")
            dated.append((d, {**item, "date": shown, "date_unverified": False}))

    dated.sort(key=lambda x: x[0], reverse=True)
    kept = [i for _, i in dated] + undated
    return kept[:limit], dropped_old

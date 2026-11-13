"""News: free Google News RSS headlines, plus a recent-news window enforced in code."""

from __future__ import annotations

import calendar
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

NEWS_WINDOW_DAYS = 90
MAX_NEWS_ITEMS = 15
RSS_HEADLINES_TO_RANK = 40  # headlines sent to Claude to pick from


def parse_rss(xml_bytes: bytes) -> list[dict[str, Any]]:
    """Parse a Google News RSS document into news items (headline, url, source, date)."""
    root = ET.fromstring(xml_bytes)
    items = []
    for it in root.iter("item"):
        try:
            published = parsedate_to_datetime(it.findtext("pubDate", ""))
        except (TypeError, ValueError):
            published = None
        title = (it.findtext("title") or "").strip()
        source = (it.findtext("source") or "").strip()
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]  # Google appends " - Publisher"
        if title:
            items.append({
                "headline": title,
                "url": (it.findtext("link") or "").strip(),
                "source": source,
                "date": published.date().isoformat() if published else "",
            })
    return items


def fetch_google_news(company: str, days: int = NEWS_WINDOW_DAYS, timeout: int = 10) -> list[dict[str, Any]]:
    """Free headlines from the Google News RSS feed, newest first. Empty list on any failure
    (the feed is unofficial and can block cloud servers; callers fall back to web search)."""
    q = urllib.parse.quote(f'"{company}" when:{days}d')
    url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            items = parse_rss(r.read())
    except Exception:  # noqa: BLE001 - network, HTTP or XML errors all mean "no free news"
        return []
    kept, _ = filter_recent_news(items, days=days, limit=RSS_HEADLINES_TO_RANK)
    return kept

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

"""Free job postings from public Greenhouse, Lever and Ashby job-board APIs.

No API key and no Claude cost. Every posting is real (it comes straight from the
company's board), so roles found here can't be hallucinated. Companies on Workday
or custom career sites aren't covered; research.py falls back to web search for them.
"""

from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

TIMEOUT = 25  # Ashby boards with full descriptions can be ~2 MB
UA = "company-research-agent/1.0"


def _clean(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())


def _ms_to_date(ms: Any) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""


def slug_candidates(company: str, override: str | None = None) -> list[str]:
    """Board names to try, e.g. 'Scale AI, Inc.' -> ['scaleai', 'scale-ai']."""
    if override and override.strip():
        return [override.strip().lower()]
    base = company.lower().strip()
    base = re.sub(r"[,.]?\s*\b(inc|llc|corp|corporation|co|ltd|gmbh|plc)\.?$", "", base).strip()
    out: list[str] = []
    for s in (re.sub(r"[^a-z0-9]", "", base), re.sub(r"[^a-z0-9]+", "-", base).strip("-")):
        if s and s not in out:
            out.append(s)
    return out


# Every parser returns the same keys. Besides the primary `location` (for display), jobs carry
# `locations` (primary + secondary + office names), `countries`, `remote` (True/False/None when
# the board doesn't say), `employment` (e.g. "FullTime") and `compensation` (text, may be "").
# The watcher filters on all of them: many US-eligible jobs list the US only as a secondary
# location or give a city ("San Francisco") as the primary one.


def _uniq(items: list[str]) -> list[str]:
    out: list[str] = []
    for i in items:
        i = (i or "").strip()
        if i and i not in out:
            out.append(i)
    return out


def _greenhouse(slug: str) -> list[dict]:
    data = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
    out = []
    for j in data.get("jobs", []):
        primary = (j.get("location") or {}).get("name", "")
        offices = [o.get("name", "") for o in j.get("offices") or []] + [o.get("location") or "" for o in j.get("offices") or []]
        out.append({
            "title": j.get("title", ""), "location": primary, "locations": _uniq([primary, *offices]),
            "countries": [], "remote": None, "employment": "", "compensation": "",
            "url": j.get("absolute_url", ""), "description": _clean(j.get("content", "")),
            "posted": (j.get("first_published") or j.get("updated_at") or "")[:10], "source": "Greenhouse",
        })
    return out


def _lever(slug: str) -> list[dict]:
    data = _get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    if not isinstance(data, list):
        return []
    out = []
    for j in data:
        cat = j.get("categories") or {}
        lists = " ".join(f"{li.get('text', '')}: {_clean(li.get('content', ''))}" for li in j.get("lists", []))
        sal = j.get("salaryRange") or {}
        comp = (f"{sal.get('currency', '')} {sal.get('min', ''):,}-{sal.get('max', ''):,} {sal.get('interval', '')}".strip()
                if isinstance(sal.get("min"), (int, float)) and isinstance(sal.get("max"), (int, float)) else "")
        wt = (j.get("workplaceType") or "").lower()
        out.append({
            "title": j.get("text", ""), "location": cat.get("location", ""),
            "locations": _uniq([cat.get("location", ""), *(cat.get("allLocations") or [])]),
            "countries": _uniq([j.get("country") or ""]),
            "remote": True if wt == "remote" else False if wt in ("onsite", "on-site") else None,
            "employment": cat.get("commitment", ""), "compensation": comp,
            "url": j.get("hostedUrl", ""), "description": f"{_clean(j.get('descriptionPlain', ''))} {lists}".strip(),
            "posted": _ms_to_date(j.get("createdAt")), "source": "Lever",
        })
    return out


def _ashby_country(addr: dict | None) -> str:
    return (((addr or {}).get("postalAddress") or {}).get("addressCountry")) or ""


def _ashby(slug: str) -> list[dict]:
    data = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true")
    out = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        sec = j.get("secondaryLocations") or []
        wt = (j.get("workplaceType") or "").lower()
        remote = True if (j.get("isRemote") or wt == "remote") else False if wt == "onsite" else None
        out.append({
            "title": j.get("title", ""), "location": j.get("location", ""),
            "locations": _uniq([j.get("location", ""), *(s.get("location", "") for s in sec)]),
            "countries": _uniq([_ashby_country(j.get("address")), *(_ashby_country(s.get("address")) for s in sec)]),
            "remote": remote, "employment": j.get("employmentType") or "",
            "compensation": ((j.get("compensation") or {}).get("compensationTierSummary") or ""),
            "url": j.get("jobUrl", ""), "description": _clean(j.get("descriptionPlain") or j.get("descriptionHtml", "")),
            "posted": (j.get("publishedAt") or "")[:10], "source": "Ashby",
        })
    return out


BOARDS = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby}


def fetch_jobs(company: str, slug_override: str | None = None) -> tuple[list[dict], str | None]:
    """Return (jobs, label like 'Greenhouse (stripe)'). Empty list if no supported board has postings."""
    for slug in slug_candidates(company, slug_override):
        for fn in (_greenhouse, _lever, _ashby):
            try:
                jobs = [j for j in fn(slug) if j.get("title")]
            except (urllib.error.URLError, ValueError, TimeoutError, OSError):
                continue
            if jobs:
                return jobs, f"{jobs[0]['source']} ({slug})"
    return [], None


def has_job_board(companies: list[str]) -> dict[str, bool]:
    """Check several companies in parallel; True if a supported board has postings."""
    names = [c for c in companies if c]
    if not names:
        return {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = pool.map(lambda c: bool(fetch_jobs(c)[0]), names)
    return dict(zip(names, results))

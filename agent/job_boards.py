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
from typing import Any

TIMEOUT = 10
UA = "company-research-agent/1.0"


def _clean(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())


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


def _greenhouse(slug: str) -> list[dict]:
    data = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
    return [
        {"title": j.get("title", ""), "location": (j.get("location") or {}).get("name", ""),
         "url": j.get("absolute_url", ""), "description": _clean(j.get("content", "")),
         "posted": (j.get("updated_at") or "")[:10], "source": "Greenhouse"}
        for j in data.get("jobs", [])
    ]


def _lever(slug: str) -> list[dict]:
    data = _get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    if not isinstance(data, list):
        return []
    out = []
    for j in data:
        lists = " ".join(f"{li.get('text', '')}: {_clean(li.get('content', ''))}" for li in j.get("lists", []))
        out.append({
            "title": j.get("text", ""), "location": (j.get("categories") or {}).get("location", ""),
            "url": j.get("hostedUrl", ""), "description": f"{_clean(j.get('descriptionPlain', ''))} {lists}".strip(),
            "posted": "", "source": "Lever",
        })
    return out


def _ashby(slug: str) -> list[dict]:
    data = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    return [
        {"title": j.get("title", ""), "location": j.get("location", ""), "url": j.get("jobUrl", ""),
         "description": _clean(j.get("descriptionPlain") or j.get("descriptionHtml", "")),
         "posted": (j.get("publishedAt") or "")[:10], "source": "Ashby"}
        for j in data.get("jobs", [])
    ]


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

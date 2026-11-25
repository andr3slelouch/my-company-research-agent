"""Hourly job watcher: new postings from watchlist companies, scored against your resume, emailed.

    python job_watcher.py              # fetch, score, email new matches, remember them
    python job_watcher.py --dry-run    # same, but print the digest instead of emailing/saving
    python job_watcher.py --check      # show which watchlist boards resolve

Env vars: ANTHROPIC_API_KEY, SMTP_USER, SMTP_PASSWORD (Gmail app password), ALERT_TO,
optional MIN_FIT (default 55), RESUME_PATH (default resume.pdf), SEEN_PATH, WATCHLIST_PATH.

Cost: one batched Claude Haiku call per run (no web search), ~$0.01 or less.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import smtplib
import sys
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from agent.job_boards import BOARDS, fetch_jobs
from agent.llm import DEFAULT_MODEL, ResearchLLM, cost_of
from agent.resume import extract_text

ROOT = Path(__file__).resolve().parent
MAX_SCORED_PER_RUN = 30      # one Claude call; the rest roll over to the next run
DESC_CHARS = 1500            # per-job description sent to the model
SEEN_KEEP_DAYS = 120         # forget seen IDs after this long

BLOCKER_PATTERNS = {
    "US citizenship required": r"\b(u\.?s\.?|united states) citizen(ship)?\b[^.]{0,60}\b(required|only|must)\b"
                               r"|\bmust be (a )?(u\.?s\.?|united states) citizen",
    "Security clearance": r"\b(security|secret|ts/sci|top secret) clearance\b|\bactive clearance\b",
    "ITAR / export control": r"\bitar\b|\bexport control(led)? (regulations|restrictions)\b",
}
NO_SPONSOR_PATTERN = (r"\b(not|unable to|cannot|can't|won't|will not|do not|does not)\b[^.]{0,40}\bsponsor"
                      r"|\bno (visa )?sponsorship\b|\bwithout (visa )?sponsorship\b")


# ---------------------------------------------------------------- config / state
def load_watchlist(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("filters", {})
    data.setdefault("companies", [])
    return data


class SeenStore:
    """Job IDs already emailed, as {id: 'YYYY-MM-DD'} in a JSON file."""

    def __init__(self, path: Path):
        self.path = path
        try:
            self.data: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = {}

    def __contains__(self, job_id: str) -> bool:
        return job_id in self.data

    def add(self, ids: list[str]) -> None:
        today = date.today().isoformat()
        for i in ids:
            self.data[i] = today

    def save(self) -> None:
        cutoff = (date.today() - timedelta(days=SEEN_KEEP_DAYS)).isoformat()
        self.data = {k: v for k, v in self.data.items() if v >= cutoff}
        self.path.write_text(json.dumps(self.data, indent=0, sort_keys=True), encoding="utf-8")


# ---------------------------------------------------------------- fetching
def fetch_company(entry: dict) -> tuple[str, list[dict], str | None]:
    """Return (company, jobs, error)."""
    name = entry["name"]
    board, slug = entry.get("board"), entry.get("slug")
    try:
        if board in BOARDS and slug:
            jobs = [j for j in BOARDS[board](slug) if j.get("title")]
        else:
            jobs, _ = fetch_jobs(name, slug)
    except (urllib.error.URLError, ValueError, TimeoutError, OSError) as e:
        return name, [], str(e)
    for j in jobs:
        j["company"] = name
        j["id"] = j.get("url") or f"{name}|{j['title']}|{j.get('location', '')}"
    return name, jobs, None


def fetch_all(companies: list[dict]) -> tuple[list[dict], list[str]]:
    jobs: list[dict] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        for name, js, err in pool.map(fetch_company, companies):
            if err or not js:
                errors.append(f"{name}: {err or 'no postings found'}")
            jobs.extend(js)
    return jobs, errors


# ---------------------------------------------------------------- filters
def _has_term(text: str, terms: list[str]) -> bool:
    """Whole-word, case-insensitive match of any term (so 'us' doesn't match 'business')."""
    t = text.lower()
    return any(re.search(rf"(?<![a-z0-9]){re.escape(term.lower())}(?![a-z0-9])", t) for term in terms)


def title_ok(title: str, f: dict) -> bool:
    inc, exc = f.get("title_include", []), f.get("title_exclude", [])
    return (not inc or _has_term(title, inc)) and not _has_term(title, exc)


def location_ok(location: str, f: dict) -> bool:
    loc = (location or "").strip()
    if not loc:
        return True  # unknown: let scoring decide
    us, inc = f.get("location_us", []), f.get("location_include", [])
    if _has_term(loc, us):
        return True
    if _has_term(loc, f.get("location_exclude", [])) or not _has_term(loc, inc):
        return False
    # "Remote", "Remote, Global" pass; "Remote - Estonia" doesn't (a non-US place is left over).
    rest = loc.lower()
    for term in inc + ["global", "worldwide", "flexible", "hybrid", "or"]:
        rest = re.sub(rf"(?<![a-z0-9]){re.escape(term.lower())}(?![a-z0-9])", " ", rest)
    return not re.sub(r"[\W_]+", "", rest)


def age_ok(posted: str, f: dict, today: date | None = None) -> bool:
    max_age = f.get("max_age_days")
    if not max_age or not posted:
        return True
    try:
        d = date.fromisoformat(posted[:10])
    except ValueError:
        return True
    return ((today or date.today()) - d).days <= int(max_age)


def flags_for(description: str) -> tuple[list[str], bool]:
    """(hard blockers found, has a 'no sponsorship' note)."""
    text = description or ""
    blockers = [label for label, pat in BLOCKER_PATTERNS.items() if re.search(pat, text, re.I)]
    return blockers, bool(re.search(NO_SPONSOR_PATTERN, text, re.I))


def select_new(jobs: list[dict], f: dict, seen: SeenStore) -> tuple[list[dict], dict[str, int]]:
    stats = {"fetched": len(jobs), "title": 0, "location": 0, "age": 0, "seen": 0, "blocked": 0}
    out, ids = [], set()
    for j in jobs:
        if j["id"] in ids:
            continue
        ids.add(j["id"])
        if not title_ok(j["title"], f):
            stats["title"] += 1
        elif not location_ok(j.get("location", ""), f):
            stats["location"] += 1
        elif not age_ok(j.get("posted", ""), f):
            stats["age"] += 1
        elif j["id"] in seen:
            stats["seen"] += 1
        else:
            j["blockers"], j["no_sponsorship"] = flags_for(j.get("description", ""))
            if j["blockers"] and f.get("skip_blocked", True):
                stats["blocked"] += 1
                seen.add([j["id"]])  # never look at it again
                continue
            out.append(j)
    out.sort(key=lambda j: j.get("posted") or "", reverse=True)
    return out, stats


# ---------------------------------------------------------------- scoring
SCORE_SYSTEM = (
    "You are a precise technical recruiter. Score how well a candidate fits each job posting. "
    "Be calibrated: 85+ = strong match the candidate should apply to today; 65-84 = good, worth applying; "
    "50-64 = possible stretch; below 50 = poor fit. Penalize clear gaps in required years or core stack. "
    "Output only JSON."
)


def score_jobs(jobs: list[dict], resume: str, api_key: str, profile_note: str = "") -> dict[str, int]:
    """Score jobs in one call. Sets fit/why/gaps on each job; returns token usage."""
    if not jobs:
        return {"input_tokens": 0, "output_tokens": 0, "searches": 0}
    listing = [
        {"id": i, "company": j["company"], "title": j["title"], "location": j.get("location", ""),
         "description": (j.get("description") or "")[:DESC_CHARS]}
        for i, j in enumerate(jobs)
    ]
    prompt = (
        f"CANDIDATE RESUME:\n{resume}\n\n"
        + (f"CANDIDATE PREFERENCES:\n{profile_note}\n\n" if profile_note else "")
        + f"JOBS (JSON):\n{json.dumps(listing, ensure_ascii=False)}\n\n"
        'Return {"scores": [{"id": <int>, "fit": <0-100>, "why": "<one sentence on the strongest match>", '
        '"gaps": "<one short phrase on the biggest gap, or empty>"}]} with one entry per job.'
    )
    llm = ResearchLLM(api_key, model=os.environ.get("MODEL", DEFAULT_MODEL), use_web=False)
    res = llm.run_json(prompt, SCORE_SYSTEM, max_tokens=8000, web=False)
    scores = res.data.get("scores", []) if isinstance(res.data, dict) else []
    for s in scores:
        try:
            j = jobs[int(s["id"])]
        except (KeyError, ValueError, TypeError, IndexError):
            continue
        j["fit"] = max(0, min(100, int(s.get("fit", 0) or 0)))
        j["why"] = str(s.get("why", ""))
        j["gaps"] = str(s.get("gaps", ""))
    return res.usage


# ---------------------------------------------------------------- digest
def build_digest(matches: list[dict], stats: dict[str, int], cost: float) -> tuple[str, str, str]:
    """Return (subject, text, html)."""
    matches = sorted(matches, key=lambda j: j.get("fit", 0), reverse=True)
    top = matches[0]
    subject = f"{len(matches)} new job match{'es' if len(matches) != 1 else ''}: {top['title']} at {top['company']} ({top['fit']})"
    lines, rows = [], []
    for j in matches:
        note = " | Says no visa sponsorship" if j.get("no_sponsorship") else ""
        lines.append(
            f"[{j['fit']}] {j['title']} at {j['company']} ({j.get('location') or 'location n/a'}, "
            f"posted {j.get('posted') or 'n/a'}){note}\n    {j.get('why', '')}"
            + (f"\n    Gap: {j['gaps']}" if j.get("gaps") else "") + f"\n    {j['url']}\n"
        )
        color = "#1a7f37" if j["fit"] >= 80 else "#0969da" if j["fit"] >= 65 else "#9a6700"
        e = html.escape
        rows.append(
            f'<tr><td style="padding:10px 12px;vertical-align:top;font:bold 18px sans-serif;color:{color}">{j["fit"]}</td>'
            f'<td style="padding:10px 12px;font:14px/1.45 sans-serif">'
            f'<a href="{e(j["url"])}" style="font-weight:600;color:#0969da">{e(j["title"])}</a> at <b>{e(j["company"])}</b><br>'
            f'<span style="color:#555">{e(j.get("location") or "location n/a")} · posted {e(j.get("posted") or "n/a")} · {e(j.get("source", ""))}</span>'
            + ('<br><span style="color:#cf222e">Says no visa sponsorship</span>' if j.get("no_sponsorship") else "")
            + f'<br>{e(j.get("why", ""))}'
            + (f'<br><span style="color:#555">Gap: {e(j["gaps"])}</span>' if j.get("gaps") else "")
            + "</td></tr>"
        )
    footer = (f"Checked {stats['fetched']} postings; {stats.get('scored', 0)} new ones scored, "
              f"{stats['blocked']} skipped for citizenship/clearance. Claude cost this run: ${cost:.4f}.")
    text = "\n".join(lines) + "\n" + footer
    body = (f'<div style="max-width:720px"><h2 style="font:600 20px sans-serif">New jobs that fit you</h2>'
            f'<table style="border-collapse:collapse">{"".join(rows)}</table>'
            f'<p style="font:12px sans-serif;color:#777">{html.escape(footer)}</p></div>')
    return subject, text, body


def send_email(subject: str, text: str, body_html: str) -> None:
    user, pwd = os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"]
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, os.environ.get("ALERT_TO", user)
    msg.set_content(text)
    msg.add_alternative(body_html, subtype="html")
    with smtplib.SMTP_SSL(os.environ.get("SMTP_HOST", "smtp.gmail.com"), int(os.environ.get("SMTP_PORT", 465))) as s:
        s.login(user, pwd)
        s.send_message(msg)


# ---------------------------------------------------------------- main
def load_resume() -> str:
    path = Path(os.environ.get("RESUME_PATH", ROOT / "resume.pdf")).expanduser()
    return extract_text(path.name, path.read_bytes())


def check(watchlist: dict) -> int:
    rows = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        for name, jobs, err in pool.map(fetch_company, watchlist["companies"]):
            rows.append((name, len(jobs), err))
    dead = 0
    for name, n, err in sorted(rows):
        status = f"{n} postings" if n else f"DEAD ({err or 'no postings'})"
        dead += not n
        print(f"{name:28s} {status}")
    print(f"\n{len(rows) - dead}/{len(rows)} boards resolve.")
    return 0


def run(dry_run: bool = False) -> int:
    watchlist = load_watchlist(Path(os.environ.get("WATCHLIST_PATH", ROOT / "watchlist.json")))
    f = watchlist["filters"]
    seen = SeenStore(Path(os.environ.get("SEEN_PATH", ROOT / ".seen_jobs.json")))
    min_fit = int(os.environ.get("MIN_FIT") or f.get("min_fit", 55))

    jobs, errors = fetch_all(watchlist["companies"])
    new, stats = select_new(jobs, f, seen)
    batch = new[:MAX_SCORED_PER_RUN]
    stats["scored"], stats["rolled_over"] = len(batch), len(new) - len(batch)
    print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC | stats: {stats}")
    if errors:
        print(f"{len(errors)} boards failed: " + "; ".join(errors[:5]))

    usage = score_jobs(batch, load_resume(), os.environ.get("ANTHROPIC_API_KEY", ""),
                       watchlist.get("profile_note", "")) if batch else {}
    cost = cost_of(usage) if usage else 0.0
    matches = [j for j in batch if j.get("fit", 0) >= min_fit]
    print(f"{len(matches)} of {len(batch)} scored jobs at or above {min_fit}. Claude cost ${cost:.4f}")

    if matches:
        subject, text, body = build_digest(matches, stats, cost)
        if dry_run:
            print(f"\nSUBJECT: {subject}\n\n{text}")
        else:
            send_email(subject, text, body)
            print(f"Emailed: {subject}")
    if not dry_run:
        seen.add([j["id"] for j in batch if "fit" in j])  # unscored jobs get retried next run
        seen.save()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="print the digest; don't email or save state")
    ap.add_argument("--check", action="store_true", help="show which watchlist boards resolve")
    args = ap.parse_args(argv)
    if args.check:
        return check(load_watchlist(Path(os.environ.get("WATCHLIST_PATH", ROOT / "watchlist.json"))))
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())

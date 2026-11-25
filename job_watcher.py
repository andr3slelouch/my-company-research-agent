"""Hourly job watcher: new postings from watchlist companies, scored against your resume, emailed.

    python job_watcher.py              # fetch, score, email new matches, remember them
    python job_watcher.py --dry-run    # same, but print the digest instead of emailing/saving
    python job_watcher.py --check      # show which watchlist boards resolve
    python job_watcher.py --spend      # show what it has spent on Claude so far

Env vars: ANTHROPIC_API_KEY, SMTP_USER, SMTP_PASSWORD (Gmail app password), ALERT_TO,
optional MIN_FIT (default 55), RESUME_PATH (default resume.pdf), SEEN_PATH, WATCHLIST_PATH,
BUDGET_USD (console credit balance when tracking started, plus later top-ups), LOW_BALANCE_USD (warning email, default 1),
SPEND_PATH (default .spend.json).

An email is sent only when there are new jobs at or above MIN_FIT; quiet hours send nothing.

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
MAX_SCORED_PER_RUN = 30      # default; MAX_SCORE env raises it for a one-off catch-up run
JOBS_PER_CALL = 30           # jobs per Claude call; the rest roll over to the next run
DESC_CHARS = 4000            # per-job description sent to the model (requirements often sit past the intro)
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
        j["company_note"] = entry.get("note", "")
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


US_STATE_CODES = ("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM "
                  "NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC").split()
US_PLACES = ["california", "new york", "texas", "washington", "massachusetts", "illinois", "colorado", "georgia",
             "florida", "oregon", "virginia", "north carolina", "pennsylvania", "new jersey", "arizona", "utah",
             "san francisco", "bay area", "seattle", "austin", "boston", "chicago", "los angeles", "denver",
             "atlanta", "dallas", "miami", "palo alto", "mountain view", "menlo park", "san jose", "nyc"]
US_COUNTRY_NAMES = {"united states", "us", "usa", "united states of america", "u.s.", "u.s.a."}


def _is_us_place(text: str) -> bool:
    """True for US cities/states too ('San Francisco', 'Austin, TX', 'US-WA-Bellevue')."""
    codes = "|".join(US_STATE_CODES)
    return bool(re.search(rf",\s*({codes})\b|\bUS-({codes})\b", text)) or _has_term(text, US_PLACES)


def location_ok(job: dict | str, f: dict) -> bool:
    """Does any of the job's locations work for a US-authorized candidate who wants remote or OKC?

    Boards often list the US only as a *secondary* location (main location 'United Kingdom') or
    give a city as the main one ('San Francisco'), so every location and country is checked.
    """
    if isinstance(job, str):
        job = {"location": job}
    locs = [l for l in (job.get("locations") or [job.get("location", "")]) if (l or "").strip()]
    countries = [c.lower() for c in job.get("countries") or [] if c]
    if not locs and not countries:
        return True  # unknown: let scoring decide
    us_terms = f.get("location_us", [])
    remote_ok = job.get("remote") is True or bool(f.get("us_onsite"))
    # 1. A location that names the US (or Oklahoma) outright: "United States", "Remote - US", "Yukon, OK".
    if any(_has_term(l, us_terms) for l in locs):
        return True
    # 2. US city/state or US country code, and the job is remote (or on-site US is allowed).
    if (any(_is_us_place(l) for l in locs) or any(c in US_COUNTRY_NAMES for c in countries)) and remote_ok:
        return True
    # 3. Plain "Remote"/"Anywhere", but only if no other country is named
    #    ("Remote (also: Poland, United Kingdom)" is remote-in-Europe, not open to the US).
    remote_only = [l for l in locs if _place_ok(l, f)]
    foreign = [l for l in locs if l not in remote_only] + [c for c in countries if c not in US_COUNTRY_NAMES]
    return bool(remote_only) and not foreign


def _place_ok(location: str, f: dict) -> bool:
    """One location string: names the US/Oklahoma, or is remote without naming another country."""
    loc = (location or "").strip()
    if not loc:
        return False
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


def employment_ok(employment: str, f: dict) -> bool:
    """Drop e.g. part-time or internships. Boards spell these differently ('PartTime', 'Part-time')."""
    norm = re.sub(r"[^a-z]", "", (employment or "").lower())
    return not norm or not any(re.sub(r"[^a-z]", "", x.lower()) in norm for x in f.get("employment_exclude", []))


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
    stats = {"fetched": len(jobs), "title": 0, "employment": 0, "location": 0, "age": 0, "seen": 0, "blocked": 0}
    out, ids = [], set()
    for j in jobs:
        if j["id"] in ids:
            continue
        ids.add(j["id"])
        if not title_ok(j["title"], f):
            stats["title"] += 1
        elif not employment_ok(j.get("employment", ""), f):
            stats["employment"] += 1
        elif not location_ok(j, f):
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
    "Every job listed has already passed a location check: it is open to someone in the US (remote, "
    "US-listed, or Oklahoma), even when its main location is another country, so do not count location as a gap. "
    "Be calibrated: 85+ = strong match the candidate should apply to today; 65-84 = good, worth applying; "
    "50-64 = possible stretch; below 50 = poor fit. Penalize clear gaps in required years or core stack. "
    "Output only JSON."
)


def score_jobs(jobs: list[dict], resume: str, api_key: str, profile_note: str = "") -> dict[str, int]:
    """Score jobs in one call. Sets fit/why/gaps on each job; returns token usage."""
    if not jobs:
        return {"input_tokens": 0, "output_tokens": 0, "searches": 0}
    listing = [
        {"id": i, "company": j["company"], "title": j["title"], "location": _place_line(j),
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
def _place_line(j: dict) -> str:
    """'United Kingdom (also: United States, Poland) · Remote' so US-as-secondary jobs are clear."""
    main = j.get("location") or "location n/a"
    others = [l for l in j.get("locations") or [] if l != main][:4]
    work = {True: "Remote", False: "On-site"}.get(j.get("remote"), "")
    return main + (f" (also: {', '.join(others)})" if others else "") + (f" · {work}" if work else "")


def build_digest(matches: list[dict], stats: dict[str, int], cost: float,
                 spend_line: str = "") -> tuple[str, str, str]:
    """Return (subject, text, html)."""
    matches = sorted(matches, key=lambda j: j.get("fit", 0), reverse=True)
    top = matches[0]
    subject = f"{len(matches)} new job match{'es' if len(matches) != 1 else ''}: {top['title']} at {top['company']} ({top['fit']})"
    lines, rows = [], []
    e = html.escape
    for j in matches:
        place = _place_line(j)
        extras = [x for x in (j.get("compensation"),) if x]
        flags = (["Says no visa sponsorship"] if j.get("no_sponsorship") else []) + \
                ([j["company_note"]] if j.get("company_note") else [])
        lines.append(
            f"[{j['fit']}] {j['title']} at {j['company']}\n    {place} · posted {j.get('posted') or 'n/a'}"
            + "".join(f"\n    {x}" for x in extras + flags)
            + f"\n    {j.get('why', '')}"
            + (f"\n    Gap: {j['gaps']}" if j.get("gaps") else "") + f"\n    {j['url']}\n"
        )
        color = "#1a7f37" if j["fit"] >= 80 else "#0969da" if j["fit"] >= 65 else "#9a6700"
        rows.append(
            f'<tr><td style="padding:10px 12px;vertical-align:top;font:bold 18px sans-serif;color:{color}">{j["fit"]}</td>'
            f'<td style="padding:10px 12px;font:14px/1.45 sans-serif">'
            f'<a href="{e(j["url"])}" style="font-weight:600;color:#0969da">{e(j["title"])}</a> at <b>{e(j["company"])}</b><br>'
            f'<span style="color:#555">{e(place)} · posted {e(j.get("posted") or "n/a")} · {e(j.get("source", ""))}</span>'
            + "".join(f'<br><span style="color:#555">{e(x)}</span>' for x in extras)
            + "".join(f'<br><span style="color:#cf222e">{e(x)}</span>' for x in flags)
            + f'<br>{e(j.get("why", ""))}'
            + (f'<br><span style="color:#555">Gap: {e(j["gaps"])}</span>' if j.get("gaps") else "")
            + "</td></tr>"
        )
    footer = (f"Checked {stats['fetched']} postings; {stats.get('scored', 0)} new ones scored, "
              f"{stats['blocked']} skipped for citizenship/clearance. Claude cost this run: ${cost:.4f}."
              + (f" {spend_line}" if spend_line else ""))
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


class SpendLedger:
    """Running total of what this watcher has spent on Claude, kept next to the seen-jobs state.

    Anthropic has no API that returns your prepaid balance (cost reports need an org Admin key),
    so we add up the exact token cost of every run ourselves. Set BUDGET_USD to the console
    balance when the ledger started (plus any top-ups since) to see what's left. Other uses of the same API key (the Streamlit app, other
    projects, a second copy of the watcher) aren't counted here.
    """

    def __init__(self, path: Path):
        self.path = path
        try:
            self.data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = {}
        self.data.setdefault("since", date.today().isoformat())
        self.data.setdefault("total_usd", 0.0)
        self.data.setdefault("runs", 0)
        self.data.setdefault("by_month", {})

    def add(self, usd: float) -> None:
        month = date.today().strftime("%Y-%m")
        self.data["total_usd"] = round(self.data["total_usd"] + usd, 6)
        self.data["by_month"][month] = round(self.data["by_month"].get(month, 0.0) + usd, 6)
        self.data["runs"] += 1

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=1, sort_keys=True), encoding="utf-8")

    @staticmethod
    def budget() -> float | None:
        try:
            return float(os.environ["BUDGET_USD"]) if os.environ.get("BUDGET_USD") else None
        except ValueError:
            return None

    def remaining(self) -> float | None:
        b = self.budget()
        return None if b is None else b - self.data["total_usd"]

    def summary(self) -> str:
        d = self.data
        month = d["by_month"].get(date.today().strftime("%Y-%m"), 0.0)
        line = (f"Claude spend by this watcher: ${d['total_usd']:.4f} over {d['runs']} runs since {d['since']} "
                f"(${month:.4f} this month)")
        rem = self.remaining()
        if rem is not None:
            line += f"; about ${rem:.2f} of credit left (${self.budget():.2f} at start)"
        return line + "."


def low_balance_alert(ledger: SpendLedger, dry_run: bool) -> None:
    """Email once when the estimated balance drops below LOW_BALANCE_USD (default $1)."""
    rem = ledger.remaining()
    threshold = float(os.environ.get("LOW_BALANCE_USD") or 1.0)
    if rem is None:
        return
    if rem >= threshold:
        ledger.data.pop("low_alert_sent", None)  # re-arm after a top-up
        return
    if ledger.data.get("low_alert_sent") or dry_run:
        return
    subject = f"Job watcher: about ${rem:.2f} of Claude credit left"
    text = (f"{ledger.summary()}\n\nTop up at https://console.anthropic.com/settings/billing, then raise "
            "BUDGET_USD to the new total you've loaded. Runs stop working when the credit runs out.")
    send_email(subject, text, f"<p>{html.escape(text)}</p>".replace("\n\n", "</p><p>"))
    ledger.data["low_alert_sent"] = True
    print(f"Emailed: {subject}")


def run(dry_run: bool = False) -> int:
    watchlist = load_watchlist(Path(os.environ.get("WATCHLIST_PATH", ROOT / "watchlist.json")))
    f = watchlist["filters"]
    seen = SeenStore(Path(os.environ.get("SEEN_PATH", ROOT / ".seen_jobs.json")))
    ledger = SpendLedger(Path(os.environ.get("SPEND_PATH", ROOT / ".spend.json")))
    min_fit = int(os.environ.get("MIN_FIT") or f.get("min_fit", 55))

    jobs, errors = fetch_all(watchlist["companies"])
    new, stats = select_new(jobs, f, seen)
    batch = new[:int(os.environ.get("MAX_SCORE") or MAX_SCORED_PER_RUN)]
    stats["scored"], stats["rolled_over"] = len(batch), len(new) - len(batch)
    print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC | stats: {stats}")
    if errors:
        print(f"{len(errors)} boards failed: " + "; ".join(errors[:5]))

    usage: dict[str, int] = {}
    if batch:
        resume, key, note = load_resume(), os.environ.get("ANTHROPIC_API_KEY", ""), watchlist.get("profile_note", "")
        for i in range(0, len(batch), JOBS_PER_CALL):
            u = score_jobs(batch[i:i + JOBS_PER_CALL], resume, key, note)
            usage = {k: usage.get(k, 0) + v for k, v in u.items()}
    cost = cost_of(usage) if usage else 0.0
    if usage:
        ledger.add(cost)  # dry runs cost real money too, so they're counted
    matches = [j for j in batch if j.get("fit", 0) >= min_fit]
    print(f"{len(matches)} of {len(batch)} scored jobs at or above {min_fit}. Claude cost ${cost:.4f}")
    print(ledger.summary())

    if not matches:
        print("No new matching jobs, so no email.")
    else:
        subject, text, body = build_digest(matches, stats, cost, ledger.summary())
        if dry_run:
            print(f"\nSUBJECT: {subject}\n\n{text}")
        else:
            send_email(subject, text, body)
            print(f"Emailed: {subject}")
    low_balance_alert(ledger, dry_run)
    ledger.save()
    if not dry_run:
        seen.add([j["id"] for j in batch if "fit" in j])  # unscored jobs get retried next run
        seen.save()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="print the digest; don't email or save state")
    ap.add_argument("--check", action="store_true", help="show which watchlist boards resolve")
    ap.add_argument("--spend", action="store_true", help="show what the watcher has spent on Claude")
    args = ap.parse_args(argv)
    if args.spend:
        print(SpendLedger(Path(os.environ.get("SPEND_PATH", ROOT / ".spend.json"))).summary())
        return 0
    if args.check:
        return check(load_watchlist(Path(os.environ.get("WATCHLIST_PATH", ROOT / "watchlist.json"))))
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())

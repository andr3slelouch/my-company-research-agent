"""Orchestrates the research steps. Pure Python; the Streamlit layer handles caching/UI.

Cost strategy (merged from the forked project): free data first, web search as fallback.
- Open roles: real postings from Greenhouse / Lever / Ashby (free). Web search only if the
  company has no supported board. A pasted job description skips both.
- News: Google News RSS headlines (free); Claude only ranks and annotates them. Web search
  only if the feed returns nothing.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from . import prompts
from .job_boards import fetch_jobs, has_job_board
from .llm import LLMResult, ResearchLLM, add_usage, new_usage
from .news import NEWS_WINDOW_DAYS, fetch_google_news, filter_recent_news
from .scoring import compute_overall

Progress = Callable[[str], None]

ROLE_CANDIDATES = 5     # board postings whose full description is scored
MAX_BOARD_TITLES = 400  # titles sent to the cheap title-matching call (~7K tokens, <$0.001 on Haiku)

_STOP = {"and", "the", "for", "with", "of", "in", "to", "a", "an", "ii", "iii", "i", "sr", "jr"}


def _relevance_order(jobs: list[dict], profile: dict) -> list[dict]:
    """Put titles sharing words with the candidate's target roles / skills first, so big
    boards (hundreds of sales roles) don't push the relevant ones past the title cap."""
    words: set[str] = set()
    for field in ("suggested_target_roles", "core_skills"):
        for v in profile.get(field) or []:
            words |= {w for w in re.findall(r"[a-z0-9+#.]+", str(v).lower()) if len(w) > 1 and w not in _STOP}
    def score(j: dict) -> int:
        title = set(re.findall(r"[a-z0-9+#.]+", j.get("title", "").lower()))
        return len(title & words)
    return sorted(jobs, key=score, reverse=True)  # stable: board order kept within ties
JD_CHARS = 4000         # description characters per posting in the scoring call

# Web-search budgets (each search is about $0.01)
SEARCHES_COMPANY_WITH_FREE_NEWS = 1   # product overview only
SEARCHES_COMPANY_FALLBACK = 5         # product + news when the RSS feed is empty
SEARCHES_ROLES_FALLBACK = 5           # only when no supported job board exists
SEARCHES_PREP = 1
SEARCHES_DISCOVER = 5


def _noop(_: str) -> None:
    pass


def _merge_sources(*results: LLMResult) -> list[dict[str, str]]:
    seen: dict[str, dict[str, str]] = {}
    for r in results:
        for s in r.sources:
            seen.setdefault(s["url"], s)
    return list(seen.values())


def _as_dict(data: Any) -> dict:
    return data if isinstance(data, dict) else {}


def _index(v: Any, n: int) -> int | None:
    try:
        i = int(v)
    except (TypeError, ValueError):
        return None
    return i if 0 <= i < n else None


def build_profile(llm: ResearchLLM, resume_text: str) -> dict:
    res = llm.run_json(prompts.profile_prompt(resume_text), prompts.system_prompt(), max_tokens=3000, web=False)
    return _as_dict(res.data)


def suggest_companies(
    llm: ResearchLLM, profile: dict, resume_text: str, prefs: dict, n: int = 6, exclude: list[str] | None = None
) -> dict[str, Any]:
    res = llm.run_json(
        prompts.discover_prompt(profile, resume_text, prefs, n, exclude or []),
        prompts.system_prompt(),
        max_tokens=6000,
        max_searches=SEARCHES_DISCOVER,
    )
    data = res.data if isinstance(res.data, dict) else {"companies": res.data}
    companies = [c for c in (data.get("companies") or []) if isinstance(c, dict)]
    boards = has_job_board([c.get("name", "") for c in companies])  # free check
    for c in companies:
        c["job_board"] = boards.get(c.get("name", ""), False)
    # Live job board first (deep-dive is cheaper and roles are guaranteed real), then by fit
    companies.sort(key=lambda c: (not c["job_board"], -(c.get("estimated_fit", 0) or 0)))
    return {"companies": companies, "sources": res.sources, "searches": res.searches, "usage": res.usage}


# --------------------------------------------------------------------------- #
def _pick_board_postings(
    llm: ResearchLLM, company: str, profile: dict, resume_text: str, jobs: list[dict]
) -> tuple[list[dict], LLMResult]:
    """Cheap title-only match (no web search), then attach full descriptions."""
    jobs = _relevance_order(jobs, profile)[:MAX_BOARD_TITLES]
    res = llm.run_json(
        prompts.board_match_prompt(company, profile, resume_text, jobs, ROLE_CANDIDATES),
        prompts.system_prompt(),
        max_tokens=3000,
        web=False,
    )
    postings: list[dict] = []
    for m in _as_dict(res.data).get("matches") or []:
        i = _index(m.get("index") if isinstance(m, dict) else None, len(jobs))
        if i is not None and all(p["board_index"] != i for p in postings):
            postings.append({**jobs[i], "board_index": i, "index": len(postings)})
    return postings[:ROLE_CANDIDATES], res


def _attach_posting_facts(roles: list[dict], postings: list[dict]) -> list[dict]:
    """Title / URL / location come from the real posting, never from the model."""
    out = []
    for r in roles:
        if not isinstance(r, dict):
            continue
        i = _index(r.get("index"), len(postings))
        if i is None:
            continue  # the model referenced a posting that doesn't exist: drop it
        p = postings[i]
        out.append({
            **r,
            "title": p.get("title", ""),
            "location": p.get("location", ""),
            "url": p.get("url", ""),
            "posted": p.get("posted", "") or r.get("posted", ""),
            "description": p.get("description", ""),
            "source": p.get("source", ""),
        })
    return out


def _attach_headlines(picks: list[Any], headlines: list[dict]) -> list[dict]:
    out = []
    for n in picks:
        if not isinstance(n, dict):
            continue
        i = _index(n.get("index"), len(headlines))
        if i is not None:
            out.append({**headlines[i], **{k: n.get(k, "") for k in ("summary", "why_it_matters", "sentiment")}})
    return out


def research_company(
    llm: ResearchLLM,
    company: str,
    profile: dict,
    resume_text: str,
    prefs: dict,
    progress: Progress = _noop,
    slug: str | None = None,
    pasted_role: dict | None = None,
) -> dict[str, Any]:
    """Free data first, then company/news + roles/fit in parallel, then interview prep."""
    progress("Checking free sources: job board and news feed…")
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_jobs = pool.submit(lambda: ([], None) if pasted_role else fetch_jobs(company, slug))
        f_news = pool.submit(fetch_google_news, company)
        jobs, board_label = f_jobs.result()
        headlines = f_news.result()

    if pasted_role:
        roles_from = "Pasted job description"
    elif jobs:
        roles_from = f"{board_label} · {len(jobs)} open postings"
    else:
        roles_from = "Web search (no Greenhouse / Lever / Ashby board found)"
    news_from = f"Google News RSS · {len(headlines)} headlines" if headlines else "Web search (news feed returned nothing)"
    progress(f"Roles: {roles_from}. News: {news_from}.")

    def company_step() -> LLMResult:
        return llm.run_json(
            prompts.company_prompt(company, profile, prefs, headlines=headlines or None),
            prompts.system_prompt(),
            max_tokens=7000,
            max_searches=SEARCHES_COMPANY_WITH_FREE_NEWS if headlines else SEARCHES_COMPANY_FALLBACK,
        )

    def roles_step() -> tuple[list[dict] | None, list[LLMResult]]:
        calls: list[LLMResult] = []
        if pasted_role:
            postings = [{**pasted_role, "index": 0}]
        elif jobs:
            postings, match_res = _pick_board_postings(llm, company, profile, resume_text, jobs)
            calls.append(match_res)
        else:
            postings = None
        if postings:  # real postings: score them, no web search
            res = llm.run_json(
                prompts.roles_prompt(company, profile, resume_text, prefs, postings=postings, jd_chars=JD_CHARS),
                prompts.system_prompt(),
                max_tokens=7000,
                web=False,
            )
        else:  # no board (or nothing close on it): search the web for roles
            res = llm.run_json(
                prompts.roles_prompt(company, profile, resume_text, prefs),
                prompts.system_prompt(),
                max_tokens=7000,
                max_searches=SEARCHES_ROLES_FALLBACK,
            )
        calls.append(res)
        return postings, calls

    progress(f"Researching {company}: products, news, and open roles (in parallel)…")
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_company = pool.submit(company_step)
        f_roles = pool.submit(roles_step)
        company_res = f_company.result()
        postings, role_calls = f_roles.result()
    roles_res = role_calls[-1]

    company_data = _as_dict(company_res.data)
    roles_data = _as_dict(roles_res.data)
    roles = roles_data.get("roles") or []
    if postings:
        roles = _attach_posting_facts(roles, postings)
    roles = sorted((r for r in roles if isinstance(r, dict)),
                   key=lambda r: r.get("match_score", 0) or 0, reverse=True)
    roles_data["roles"] = roles

    raw_news = company_data.get("news") or []
    if headlines:
        raw_news = _attach_headlines(raw_news, headlines)
    news, news_dropped = filter_recent_news(raw_news)
    company_data["news"] = news  # interview prep only sees in-window news

    fit = roles_data.get("fit") or {}
    fit["overall"] = compute_overall(fit.get("breakdown") or {}, roles)

    progress("Building interview prep from the research…")
    prep_res = llm.run_json(
        prompts.prep_prompt(company, profile, resume_text, company_data, roles_data),
        prompts.system_prompt(),
        max_tokens=7000,
        max_searches=SEARCHES_PREP,
    )

    all_calls = [company_res, *role_calls, prep_res]
    progress("Done.")
    return {
        "query": company,
        "company": company_data.get("company") or {"name": company},
        "product": company_data.get("product") or {},
        "news": news,
        "news_dropped_old": news_dropped,
        "news_window_days": NEWS_WINDOW_DAYS,
        "roles": roles,
        "roles_reviewed": len(jobs) if jobs else roles_data.get("roles_reviewed"),
        "hiring_notes": roles_data.get("hiring_notes", ""),
        "fit": fit,
        "prep": _as_dict(prep_res.data),
        "data_sources": {"roles": roles_from, "news": news_from},
        "pasted": bool(pasted_role),
        "sources": _merge_sources(*all_calls),
        "searches": sum(r.usage.get("searches", 0) for r in all_calls),
        "usage": add_usage(*(r.usage for r in all_calls)),
    }


def prep_for_role(llm: ResearchLLM, company: str, profile: dict, resume_text: str, role: dict) -> tuple[dict, dict]:
    """Role-specific prep (no web search): requirement map, gap plan, questions, pitch."""
    res = llm.run_json(
        prompts.role_prep_prompt(company, profile, resume_text, role),
        prompts.system_prompt(),
        max_tokens=5000,
        web=False,
    )
    return _as_dict(res.data), res.usage or new_usage()

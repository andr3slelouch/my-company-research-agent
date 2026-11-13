"""Orchestrates the research steps. Pure Python — the Streamlit layer handles caching/UI."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from . import prompts
from .llm import LLMResult, ResearchLLM
from .news import NEWS_WINDOW_DAYS, filter_recent_news
from .scoring import compute_overall

Progress = Callable[[str], None]


def _noop(_: str) -> None:
    pass


def _merge_sources(*results: LLMResult) -> list[dict[str, str]]:
    seen: dict[str, dict[str, str]] = {}
    for r in results:
        for s in r.sources:
            seen.setdefault(s["url"], s)
    return list(seen.values())


def build_profile(llm: ResearchLLM, resume_text: str) -> dict:
    res = llm.run_json(prompts.profile_prompt(resume_text), prompts.system_prompt(), max_tokens=3000, web=False)
    return res.data if isinstance(res.data, dict) else {}


def suggest_companies(
    llm: ResearchLLM, profile: dict, resume_text: str, prefs: dict, n: int = 6, exclude: list[str] | None = None
) -> dict[str, Any]:
    res = llm.run_json(
        prompts.discover_prompt(profile, resume_text, prefs, n, exclude or []),
        prompts.system_prompt(),
        max_tokens=6000,
        max_searches=5,
    )
    data = res.data if isinstance(res.data, dict) else {"companies": res.data}
    companies = data.get("companies") or []
    companies.sort(key=lambda c: c.get("estimated_fit", 0) or 0, reverse=True)
    return {"companies": companies, "sources": res.sources, "searches": res.searches}


def research_company(
    llm: ResearchLLM, company: str, profile: dict, resume_text: str, prefs: dict, progress: Progress = _noop
) -> dict[str, Any]:
    """Run company/news + roles/fit in parallel, then interview prep."""
    progress(f"Researching {company}: products, news, and open roles (in parallel)…")
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_company = pool.submit(
            llm.run_json,
            prompts.company_prompt(company, profile, prefs),
            prompts.system_prompt(),
            max_tokens=7000,
            max_searches=5,
        )
        f_roles = pool.submit(
            llm.run_json,
            prompts.roles_prompt(company, profile, resume_text, prefs),
            prompts.system_prompt(),
            max_tokens=7000,
            max_searches=5,
        )
        company_res: LLMResult = f_company.result()
        roles_res: LLMResult = f_roles.result()

    company_data = company_res.data if isinstance(company_res.data, dict) else {}
    roles_data = roles_res.data if isinstance(roles_res.data, dict) else {}
    roles = sorted(roles_data.get("roles") or [], key=lambda r: r.get("match_score", 0) or 0, reverse=True)
    roles_data["roles"] = roles

    news, news_dropped = filter_recent_news(company_data.get("news") or [])
    company_data["news"] = news  # interview prep only sees in-window news

    fit = roles_data.get("fit") or {}
    fit["overall"] = compute_overall(fit.get("breakdown") or {}, roles)

    progress("Building interview prep from the research…")
    prep_res = llm.run_json(
        prompts.prep_prompt(company, profile, resume_text, company_data, roles_data),
        prompts.system_prompt(),
        max_tokens=7000,
        max_searches=2,
    )
    prep = prep_res.data if isinstance(prep_res.data, dict) else {}

    progress("Done.")
    return {
        "query": company,
        "company": company_data.get("company") or {"name": company},
        "product": company_data.get("product") or {},
        "news": news,
        "news_dropped_old": news_dropped,
        "news_window_days": NEWS_WINDOW_DAYS,
        "roles": roles,
        "roles_reviewed": roles_data.get("roles_reviewed"),
        "hiring_notes": roles_data.get("hiring_notes", ""),
        "fit": fit,
        "prep": prep,
        "sources": _merge_sources(company_res, roles_res, prep_res),
        "searches": company_res.searches + roles_res.searches + prep_res.searches,
    }

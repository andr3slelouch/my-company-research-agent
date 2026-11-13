"""Prompt templates. Every prompt asks for a single JSON object in a ```json fence."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from .news import MAX_NEWS_ITEMS, NEWS_WINDOW_DAYS, news_cutoff


def system_prompt() -> str:
    today = date.today().isoformat()
    return f"""You are a meticulous career research agent helping a job seeker evaluate companies.
Today's date is {today}.

Rules:
- Ground company facts, open roles and news in web search results whenever web search is available. Prefer primary sources: the company's careers page / ATS (Greenhouse, Lever, Ashby, Workday, SmartRecruiters), newsroom, engineering blog, and reputable press.
- NEVER invent job postings, URLs, dates, funding figures or quotes. If you cannot verify something, omit it or say it is unverified. An empty list is better than a fabricated one.
- Only include news published in the last {NEWS_WINDOW_DAYS} days (on or after {news_cutoff().isoformat()}), newest first. Give dates as YYYY-MM-DD; use YYYY-MM only if the day is truly unknown. Never guess a date.
- Be candid about gaps. A useful honest assessment beats flattery.
- Tie every recommendation back to concrete evidence from the candidate's resume.
- Finish your reply with exactly one JSON object inside a ```json code fence that matches the requested schema. No comments inside the JSON."""


def _prefs_block(prefs: dict[str, Any]) -> str:
    lines = []
    labels = {
        "target_roles": "Target roles",
        "locations": "Preferred locations",
        "remote": "Work arrangement",
        "industries": "Industries of interest",
        "company_size": "Company size",
        "work_auth": "Work authorization / visa notes",
        "other": "Other preferences",
    }
    for k, label in labels.items():
        v = prefs.get(k)
        if v:
            lines.append(f"- {label}: {', '.join(v) if isinstance(v, list) else v}")
    return "\n".join(lines) or "- (none stated)"


def profile_prompt(resume_text: str) -> str:
    return f"""Read this resume and extract a structured candidate profile. Do not search the web.

<resume>
{resume_text}
</resume>

Return JSON:
```json
{{
  "name": "string",
  "headline": "one-line professional summary",
  "location": "string",
  "years_experience": 0,
  "seniority": "intern | entry | junior | mid | senior | staff+",
  "core_skills": ["..."],
  "languages_spoken": ["e.g. Spanish (native)"],
  "domains": ["industries / problem spaces"],
  "suggested_target_roles": ["3-6 realistic job titles"],
  "education": ["..."],
  "standout_achievements": ["2-5 concrete, quantified where possible"]
}}
```"""


def discover_prompt(profile: dict, resume_text: str, prefs: dict, n: int, exclude: list[str]) -> str:
    excl = ", ".join(exclude) if exclude else "none"
    return f"""Suggest {n} companies this candidate should target right now.

Candidate profile:
{json.dumps(profile, indent=2)}

<resume>
{resume_text}
</resume>

Preferences:
{_prefs_block(prefs)}

Exclude these companies: {excl}

Method:
1. Use web search to find companies that are actively hiring for roles matching the candidate's skills and seniority and that satisfy the preferences (location / remote / sponsorship if mentioned).
2. Favor a realistic mix: a few well-known names, several mid-size or growth-stage companies, and at least one less obvious "hidden gem". Avoid suggesting only FAANG.
3. For each, cite concrete evidence of current hiring (a specific posting or careers page) when you can find it.

Return JSON:
```json
{{
  "companies": [
    {{
      "name": "string",
      "website": "https://...",
      "industry": "string",
      "size": "e.g. 200-500 employees / Series C",
      "hq": "string",
      "estimated_fit": 0,
      "why_fit": "2-3 sentences tying the company's needs to specific resume evidence",
      "matching_skills": ["..."],
      "example_role": {{"title": "string", "url": "https://... or empty if unverified"}},
      "hiring_signal": "what indicates they are hiring now",
      "watch_out": "one honest caveat (seniority bar, location, sponsorship, etc.)"
    }}
  ]
}}
```
estimated_fit is 0-100, judged on three things only: skill match (50%), years of experience vs. what their roles require (25%), and domain match (25%)."""


def _headlines_block(headlines: list[dict]) -> str:
    return "\n".join(
        f"{i} | {h.get('date') or 'undated'} | {h.get('source') or ''} | {h.get('headline', '')}"
        for i, h in enumerate(headlines)
    )


def company_prompt(company: str, profile: dict, prefs: dict, headlines: list[dict] | None = None) -> str:
    if headlines:
        news_task = f"""NEWS: Below are real headlines about "{company}" from the last {NEWS_WINDOW_DAYS} days (free Google News feed), as index | date | source | headline. Do NOT search for news. Pick up to {MAX_NEWS_ITEMS} that matter to a job seeker: funding/earnings, layoffs or hiring freezes, hiring expansions, acquisitions, leadership changes, product launches, AI initiatives, office/remote policy, legal/regulatory events. Skip stock-price chatter, listicles, duplicates and headlines about a different company with a similar name. Refer to each by its index; headline, URL, source and date are filled in from the feed automatically.
<headlines>
{_headlines_block(headlines)}
</headlines>
Use web search only for the product overview and company facts, and only if you need it."""
        news_schema = """{"index": 0, "summary": "1-2 sentences", "why_it_matters": "1-2 sentences",
      "sentiment": "positive | neutral | negative"}"""
    else:
        news_task = f"""NEWS: Gather ALL notable news about the company published in the last {NEWS_WINDOW_DAYS} days, i.e. on or after {news_cutoff().isoformat()} (today is {date.today().isoformat()}). Search with recency terms (e.g. "{company} news {date.today().strftime('%B %Y')}", "{company} announces", "{company} layoffs OR hiring OR funding OR acquisition", the company newsroom/press page). Cover: funding/earnings, layoffs or hiring freezes, hiring expansions, acquisitions, leadership changes, product launches, AI initiatives, office/remote policy changes, legal/regulatory events. Exclude anything older than the cutoff even if it is important. For each item explain why it matters to THIS job seeker (hiring momentum, team stability, tech direction, interview talking point). If you find nothing in the window, return an empty news list."""
        news_schema = """{"headline": "string", "date": "YYYY-MM-DD", "source": "publication",
      "url": "https://...", "summary": "1-2 sentences", "why_it_matters": "1-2 sentences",
      "sentiment": "positive | neutral | negative"}"""
    return f"""Research the company "{company}" for a job seeker.

Candidate snapshot (to judge relevance of news):
{json.dumps({k: profile.get(k) for k in ("headline", "core_skills", "suggested_target_roles", "location")}, indent=2)}
Preferences:
{_prefs_block(prefs)}

Find: what the company does and sells, its main products, customers, business model, competitors, and engineering stack/tech signals (engineering blog, job posts, GitHub).

{news_task}

If the name is ambiguous, pick the most likely company for a software-engineering job seeker and state the assumption in "disambiguation".

Return JSON:
```json
{{
  "company": {{
    "name": "string", "website": "https://...", "careers_url": "https://...",
    "industry": "string", "hq": "string", "size": "string",
    "founded": "string", "funding_or_ticker": "string", "one_liner": "string",
    "disambiguation": ""
  }},
  "product": {{
    "overview": "3-5 sentence plain-English overview",
    "products": [{{"name": "string", "description": "string", "relevance_to_candidate": "string"}}],
    "customers": "who buys / uses it",
    "business_model": "how they make money",
    "competitors": ["..."],
    "engineering_stack": ["technologies with evidence"],
    "culture_signals": ["values, eng practices, remote policy, etc."]
  }},
  "news": [
    {news_schema}
  ]
}}
```
Return up to {MAX_NEWS_ITEMS} news items, newest first, one item per distinct story (merge duplicate coverage)."""


def _postings_block(postings: list[dict], jd_chars: int) -> str:
    return "\n\n".join(
        f"### [{p['index']}] {p.get('title', '')} ({p.get('location') or 'location not stated'})\n"
        f"{(p.get('description') or 'No description available.')[:jd_chars]}"
        for p in postings
    )


def roles_prompt(
    company: str, profile: dict, resume_text: str, prefs: dict,
    postings: list[dict] | None = None, jd_chars: int = 4000,
) -> str:
    if postings:
        method = f"""These are REAL postings from {company}'s job board or pasted by the candidate. Do NOT search for roles.
<postings>
{_postings_block(postings, jd_chars)}
</postings>

Method:
1. For each posting the candidate meets most core requirements for, or is a credible stretch on (at most one level above their seniority), return a role entry with its index. Omit the rest.
2. Title, URL and location are filled in from the posting automatically; focus on the analysis.
3. Read years_required from the description ("3+", "2-5", or "not stated").
4. Score the three fit dimensions below against these postings."""
        role_fields = '"index": 0, "team": "string", "remote": "remote | hybrid | onsite | unknown",\n      "seniority": "string", "years_required": "e.g. 3+ / not stated",'
        return _roles_with_postings(company, profile, resume_text, prefs, method, role_fields)
    return f"""Find CURRENT open roles at "{company}" that this candidate realistically qualifies for, then score overall fit.

<resume>
{resume_text}
</resume>

Candidate profile:
{json.dumps(profile, indent=2)}

Preferences:
{_prefs_block(prefs)}

Method:
1. Search the company's careers page and ATS boards (e.g. "site:boards.greenhouse.io {company}", "site:jobs.lever.co {company}", "site:jobs.ashbyhq.com {company}", "{company} careers software engineer"). Also note the general tech stack and seniority mix you see in postings.
2. Include roles where the candidate meets most core requirements or is a credible stretch (at most one level above their seniority). Skip roles that are clearly far out of reach or unrelated, but count them in roles_reviewed.
3. Only list postings you actually found. Each needs a real URL. If nothing qualifying is open, return an empty roles list and explain in hiring_notes, including which role families to watch.
4. For each role, record the years of experience the posting requires (years_required, e.g. "3+", "2-5", or "not stated").
5. Score exactly three fit dimensions 0-100, each with one sentence of evidence:
   - skills: how well the candidate's hard skills (languages, frameworks, databases, tools) match what the roles require. 90+ = meets nearly all required skills; 50 = about half.
   - years_experience: candidate's total relevant years vs. the years the roles require. Meets or exceeds the typical requirement = 85-100; 1 year short = ~65; 2 years short = ~45; 3+ years short = below 30. If no posting states years, judge from the seniority level and say so.
   - domain: familiarity with the company's industry and problem space (e.g. payments, HR tech, healthcare, dev tools), based on resume evidence. Transferable-but-not-direct experience = 50-70.
   Do not factor location, remote policy or work authorization into these scores; mention them in hiring_notes instead.

Return JSON:
```json
{{
  "roles": [
    {{
      "title": "string", "team": "string", "location": "string", "remote": "remote | hybrid | onsite | unknown",
      "seniority": "string", "years_required": "e.g. 3+ / not stated", "url": "https://...", "posted": "YYYY-MM-DD or empty",
      "match_score": 0,
      "requirements_met": ["..."],
      "gaps": ["..."],
      "why_you_fit": "2 sentences citing resume evidence"
    }}
  ],
  "roles_reviewed": 0,
  "hiring_notes": "hiring momentum, freezes, location/remote and sponsorship policy if known, roles to watch",
  "fit": {{
    "breakdown": {{
      "skills": {{"score": 0, "evidence": "string"}},
      "years_experience": {{"score": 0, "evidence": "e.g. Roles ask for 3-5 yrs; candidate has ~3"}},
      "domain": {{"score": 0, "evidence": "string"}}
    }},
    "candidate_years": 0,
    "summary": "3-4 sentence honest verdict",
    "strengths": ["specific selling points for this company"],
    "gaps": ["specific gaps"],
    "gap_closers": ["concrete actions to close gaps before applying (projects, certs, resume tweaks)"]
  }}
}}
```"""


def _roles_with_postings(company, profile, resume_text, prefs, method, role_fields) -> str:
    return f"""Score how well this candidate fits open roles at "{company}", then score overall fit.

<resume>
{resume_text}
</resume>

Candidate profile:
{json.dumps(profile, indent=2)}

Preferences:
{_prefs_block(prefs)}

{method}

Fit dimensions (0-100, each with one sentence of evidence):
- skills: how well the candidate's hard skills match what the roles require. 90+ = meets nearly all required skills; 50 = about half.
- years_experience: candidate's relevant years vs. the years the roles require. Meets or exceeds = 85-100; 1 year short = ~65; 2 years short = ~45; 3+ years short = below 30. If no posting states years, judge from seniority and say so.
- domain: familiarity with the company's industry and problem space, based on resume evidence. Transferable-but-not-direct = 50-70.
Do not factor location, remote policy or work authorization into these scores; mention them in hiring_notes.
If no posting is a realistic fit, return an empty roles list and say which role families to watch in hiring_notes.

Return JSON:
```json
{{
  "roles": [
    {{
      {role_fields}
      "match_score": 0,
      "requirements_met": ["..."],
      "gaps": ["..."],
      "why_you_fit": "2 sentences citing resume evidence"
    }}
  ],
  "hiring_notes": "hiring momentum, location/remote and sponsorship notes from the postings, roles to watch",
  "fit": {{
    "breakdown": {{
      "skills": {{"score": 0, "evidence": "string"}},
      "years_experience": {{"score": 0, "evidence": "string"}},
      "domain": {{"score": 0, "evidence": "string"}}
    }},
    "candidate_years": 0,
    "summary": "3-4 sentence honest verdict",
    "strengths": ["specific selling points for this company"],
    "gaps": ["specific gaps"],
    "gap_closers": ["concrete actions to close gaps before applying"]
  }}
}}
```"""


def board_match_prompt(company: str, profile: dict, resume_text: str, jobs: list[dict], max_n: int) -> str:
    titles = "\n".join(f"{i} | {j.get('title', '')} | {j.get('location', '')}" for i, j in enumerate(jobs))
    return f"""Pick the open roles at "{company}" this candidate is a realistic fit for. Do not search the web.

<resume>
{resume_text}
</resume>

Candidate profile:
{json.dumps({k: profile.get(k) for k in ("headline", "years_experience", "seniority", "core_skills", "suggested_target_roles", "location")}, indent=2)}

Open roles (index | title | location):
{titles}

Return the {max_n} roles closest to the candidate's function and seniority, best first, scored 0-100 from the title and location alone (85+ = right function and level; below 50 = poor fit). Low scores are fine; they will be checked against the full description later. Return an empty list only if nothing is even close.

Return JSON:
```json
{{"matches": [{{"index": 0, "score": 0, "reason": "one line"}}]}}
```"""


def role_prep_prompt(company: str, profile: dict, resume_text: str, role: dict, jd_chars: int = 8000) -> str:
    jd = (role.get("description") or "").strip()
    if not jd:
        jd = "No full description available. Requirements noted earlier: " + "; ".join(
            (role.get("requirements_met") or []) + (role.get("gaps") or [])
        )
    return f"""Prepare this candidate to interview for one specific role at "{company}". Do not search the web.

<resume>
{resume_text}
</resume>

Candidate profile:
{json.dumps({k: profile.get(k) for k in ("headline", "years_experience", "seniority", "core_skills")}, indent=2)}

<job_description title="{role.get('title', '')}">
{jd[:jd_chars]}
</job_description>

Return JSON:
```json
{{
  "requirement_map": [{{"requirement": "from the JD", "evidence": "matching resume item", "strength": "strong | partial | gap"}}],
  "gap_plan": ["how to address each gap honestly in the interview"],
  "questions": [{{"question": "string", "how_to_answer": "tie to a specific resume item"}}],
  "pitch": "60-second 'why me for this role' answer in first person"
}}
```
6-8 requirements, 8 questions. Never invent resume facts."""


def prep_prompt(company: str, profile: dict, resume_text: str, company_data: dict, roles_data: dict) -> str:
    roles_brief = [
        {k: r.get(k) for k in ("title", "seniority", "requirements_met", "gaps")}
        for r in (roles_data.get("roles") or [])[:5]
    ]
    company_brief = {
        "company": company_data.get("company", {}),
        "products": [p.get("name") for p in company_data.get("product", {}).get("products", [])],
        "engineering_stack": company_data.get("product", {}).get("engineering_stack", []),
        "culture_signals": company_data.get("product", {}).get("culture_signals", []),
        "recent_news": [n.get("headline") for n in company_data.get("news", [])[:5]],
    }
    return f"""Build an interview preparation pack for this candidate applying to "{company}".

<resume>
{resume_text}
</resume>

Candidate profile:
{json.dumps(profile, indent=2)}

Company research:
{json.dumps(company_brief, indent=2)}

Target roles found:
{json.dumps(roles_brief, indent=2) if roles_brief else "No specific open roles found; prepare for the most likely software engineering role at the company."}

You may search the web for this company's known interview process (e.g. Glassdoor, Blind, levels.fyi, company engineering blog) if it helps; label anything unverified as "reported".

Requirements:
- 12-15 likely interview questions across categories: Behavioral, Technical, System design, Coding, Company/motivation. Make them specific to the company's products, stack and the target roles, not generic.
- For each question give a short answer strategy that points to a specific resume experience (e.g. a STAR outline referencing a real project from the resume).
- Talking points must map a company need to resume evidence.
- Include smart questions for the candidate to ask, informed by recent news.
- A 7-day prep plan.

Return JSON:
```json
{{
  "process_overview": "what the interview loop typically looks like (mark as reported if from third parties)",
  "elevator_pitch": "a 60-second 'tell me about yourself' tailored to this company, first person",
  "talking_points": [{{"company_need": "string", "your_evidence": "string"}}],
  "technical_topics": [{{"topic": "string", "why": "string", "resources": "string"}}],
  "questions": [
    {{"category": "Behavioral | Technical | System design | Coding | Company/motivation",
      "question": "string", "why_they_ask": "string", "how_to_answer": "string"}}
  ],
  "questions_to_ask": ["..."],
  "red_flags_to_address": [{{"concern": "string", "how_to_address": "string"}}],
  "prep_plan": [{{"day": "Day 1", "focus": "string", "tasks": ["..."]}}]
}}
```"""

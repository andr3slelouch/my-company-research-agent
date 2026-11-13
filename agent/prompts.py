"""Prompt templates. Every prompt asks for a single JSON object in a ```json fence."""

from __future__ import annotations

import json
from datetime import date
from typing import Any


def system_prompt() -> str:
    today = date.today().isoformat()
    return f"""You are a meticulous career research agent helping a job seeker evaluate companies.
Today's date is {today}.

Rules:
- Ground company facts, open roles and news in web search results whenever web search is available. Prefer primary sources: the company's careers page / ATS (Greenhouse, Lever, Ashby, Workday, SmartRecruiters), newsroom, engineering blog, and reputable press.
- NEVER invent job postings, URLs, dates, funding figures or quotes. If you cannot verify something, omit it or say it is unverified. An empty list is better than a fabricated one.
- Only include news from roughly the last 12 months, newest first, and give dates as YYYY-MM-DD when known (YYYY-MM if only the month is known).
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
estimated_fit is 0-100."""


def company_prompt(company: str, profile: dict, prefs: dict) -> str:
    return f"""Research the company "{company}" for a job seeker.

Candidate snapshot (to judge relevance of news):
{json.dumps({k: profile.get(k) for k in ("headline", "core_skills", "suggested_target_roles", "location")}, indent=2)}
Preferences:
{_prefs_block(prefs)}

Find: what the company does and sells, its main products, customers, business model, competitors, engineering stack/tech signals (engineering blog, job posts, GitHub), and the most relevant recent news (funding, earnings, layoffs, hiring freezes, acquisitions, leadership changes, product launches, AI initiatives, office/remote policy changes). For each news item explain why it matters to THIS job seeker (e.g. hiring momentum, team stability, tech direction, interview talking point).

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
    {{"headline": "string", "date": "YYYY-MM-DD", "source": "publication",
      "url": "https://...", "summary": "1-2 sentences", "why_it_matters": "1-2 sentences",
      "sentiment": "positive | neutral | negative"}}
  ]
}}
```
Return 4-8 news items when available."""


def roles_prompt(company: str, profile: dict, resume_text: str, prefs: dict) -> str:
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
4. Score each fit dimension 0-100 with one sentence of evidence:
   - skills: hard-skill overlap with what the roles require
   - tech_stack: overlap with the company's languages / frameworks / infra
   - experience_level: candidate's years & seniority vs. what roles ask
   - domain: familiarity with the company's industry / problem space
   - location_logistics: location, remote policy, work authorization, language requirements vs. candidate & preferences

Return JSON:
```json
{{
  "roles": [
    {{
      "title": "string", "team": "string", "location": "string", "remote": "remote | hybrid | onsite | unknown",
      "seniority": "string", "url": "https://...", "posted": "YYYY-MM-DD or empty",
      "match_score": 0,
      "requirements_met": ["..."],
      "gaps": ["..."],
      "why_you_fit": "2 sentences citing resume evidence"
    }}
  ],
  "roles_reviewed": 0,
  "hiring_notes": "hiring momentum, freezes, sponsorship policy if known, roles to watch",
  "fit": {{
    "breakdown": {{
      "skills": {{"score": 0, "evidence": "string"}},
      "tech_stack": {{"score": 0, "evidence": "string"}},
      "experience_level": {{"score": 0, "evidence": "string"}},
      "domain": {{"score": 0, "evidence": "string"}},
      "location_logistics": {{"score": 0, "evidence": "string"}}
    }},
    "summary": "3-4 sentence honest verdict",
    "strengths": ["specific selling points for this company"],
    "gaps": ["specific gaps"],
    "gap_closers": ["concrete actions to close gaps before applying (projects, certs, resume tweaks)"]
  }}
}}
```"""


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
- 12-16 likely interview questions across categories: Behavioral, Technical, System design, Coding, Company/motivation. Make them specific to the company's products, stack and the target roles, not generic.
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

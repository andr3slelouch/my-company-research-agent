"""Render a research result as a Markdown report for download."""

from __future__ import annotations

from datetime import date
from typing import Any

from .scoring import LABELS, label_for


def _li(items: list[Any]) -> str:
    return "\n".join(f"- {i}" for i in items if i) or "- _None found_"


def to_markdown(r: dict[str, Any], candidate: str = "") -> str:
    c = r.get("company", {})
    fit = r.get("fit", {})
    prod = r.get("product", {})
    prep = r.get("prep", {})
    score = fit.get("overall", 0)
    label, _ = label_for(score)
    out: list[str] = []

    out.append(f"# {c.get('name', r.get('query', 'Company'))} — Research Report")
    meta = f"_Generated {date.today().isoformat()}"
    out.append(meta + (f" for {candidate}_" if candidate else "_"))
    if c.get("one_liner"):
        out.append(f"\n> {c['one_liner']}")

    out.append(f"\n## Fit score: {score}/100 ({label})\n")
    out.append(fit.get("summary", ""))
    out.append("\n| Dimension | Score | Evidence |\n|---|---|---|")
    for k, v in (fit.get("breakdown") or {}).items():
        v = v if isinstance(v, dict) else {"score": v}
        evidence = str(v.get("evidence", "")).replace("|", "/")
        out.append(f"| {LABELS.get(k, k)} | {v.get('score', '')} | {evidence} |")
    out.append("\n**Strengths**\n" + _li(fit.get("strengths", [])))
    out.append("\n**Gaps**\n" + _li(fit.get("gaps", [])))
    out.append("\n**How to close the gaps**\n" + _li(fit.get("gap_closers", [])))

    out.append("\n## Open roles you qualify for\n")
    roles = r.get("roles", [])
    if not roles:
        out.append("_No qualifying open roles found._")
    for role in roles:
        out.append(f"### [{role.get('title')}]({role.get('url', '')}) — match {role.get('match_score', '?')}/100")
        meta = [role.get("location"), role.get("remote"), role.get("seniority")]
        if role.get("years_required"):
            meta.append(f"{role['years_required']} yrs required")
        out.append(" · ".join(m for m in meta if m))
        out.append(f"\n{role.get('why_you_fit', '')}")
        if role.get("gaps"):
            out.append("\nGaps: " + "; ".join(role["gaps"]))
    if r.get("hiring_notes"):
        out.append(f"\n**Hiring notes:** {r['hiring_notes']}")

    out.append("\n## Interview preparation\n")
    if prep.get("process_overview"):
        out.append(f"**Process:** {prep['process_overview']}\n")
    if prep.get("elevator_pitch"):
        out.append(f"**Your 60-second pitch:**\n\n> {prep['elevator_pitch']}\n")
    if prep.get("talking_points"):
        out.append("**Talking points**")
        out.extend(f"- **{t.get('company_need')}** → {t.get('your_evidence')}" for t in prep["talking_points"])
    if prep.get("questions"):
        out.append("\n**Likely questions**")
        for i, q in enumerate(prep["questions"], 1):
            out.append(f"{i}. _{q.get('category')}_ — **{q.get('question')}**  \n   Why: {q.get('why_they_ask')}  \n   Approach: {q.get('how_to_answer')}")
    if prep.get("questions_to_ask"):
        out.append("\n**Questions to ask them**\n" + _li(prep["questions_to_ask"]))
    if prep.get("prep_plan"):
        out.append("\n**Prep plan**")
        for d in prep["prep_plan"]:
            out.append(f"- **{d.get('day')}: {d.get('focus')}** — " + "; ".join(d.get("tasks", [])))

    out.append(f"\n## Recent news (past {r.get('news_window_days', 90)} days)\n")
    for n in r.get("news", []):
        when = "date unverified" if n.get("date_unverified") else n.get("date", "")
        out.append(f"- **{when}** [{n.get('headline')}]({n.get('url', '')}) ({n.get('source', '')})  \n  {n.get('summary', '')}  \n  _Why it matters:_ {n.get('why_it_matters', '')}")
    if not r.get("news"):
        out.append("_No recent news found._")

    out.append("\n## Product overview\n")
    out.append(prod.get("overview", ""))
    for p in prod.get("products", []):
        out.append(f"- **{p.get('name')}** — {p.get('description')}")
    if prod.get("business_model"):
        out.append(f"\n**Business model:** {prod['business_model']}")
    if prod.get("customers"):
        out.append(f"\n**Customers:** {prod['customers']}")
    if prod.get("competitors"):
        out.append(f"\n**Competitors:** {', '.join(prod['competitors'])}")
    if prod.get("engineering_stack"):
        out.append(f"\n**Engineering stack:** {', '.join(prod['engineering_stack'])}")

    if r.get("sources"):
        out.append("\n## Sources\n")
        out.extend(f"- [{s.get('title') or s['url']}]({s['url']})" for s in r["sources"][:40])
    return "\n".join(out) + "\n"

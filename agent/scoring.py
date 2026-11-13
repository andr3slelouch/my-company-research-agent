"""Deterministic fit scoring.

The model rates each dimension 0-100 with evidence; the overall score is computed
here so it is consistent and explainable across companies.
"""

from __future__ import annotations

from typing import Any

WEIGHTS: dict[str, float] = {
    "skills": 0.30,             # hard-skill overlap with open roles
    "tech_stack": 0.20,         # languages / frameworks / infra the company uses
    "experience_level": 0.20,   # seniority & years vs. what roles ask for
    "domain": 0.10,             # industry / problem-space familiarity
    "location_logistics": 0.20, # location, remote policy, work authorization, language
}

LABELS = {
    "skills": "Skills match",
    "tech_stack": "Tech stack overlap",
    "experience_level": "Experience level",
    "domain": "Domain familiarity",
    "location_logistics": "Location & logistics",
}


def clamp(v: Any, lo: int = 0, hi: int = 100) -> int:
    try:
        return max(lo, min(hi, int(round(float(v)))))
    except (TypeError, ValueError):
        return 0


def _dim_score(entry: Any) -> int:
    if isinstance(entry, dict):
        return clamp(entry.get("score"))
    return clamp(entry)


def compute_overall(breakdown: dict[str, Any], roles: list[dict] | None = None) -> int:
    """Weighted average of dimensions, nudged by the best role matches.

    If qualifying roles exist, blend 75% dimension score + 25% mean of top-3 role
    match scores. If no roles were found, cap at 70: a company with nothing open
    for you is not a top fit *right now*, however good the culture match.
    """
    total_w = 0.0
    acc = 0.0
    for key, w in WEIGHTS.items():
        if key in breakdown:
            acc += _dim_score(breakdown[key]) * w
            total_w += w
    base = acc / total_w if total_w else 0.0

    role_scores = sorted((clamp(r.get("match_score")) for r in (roles or [])), reverse=True)[:3]
    if role_scores:
        overall = 0.75 * base + 0.25 * (sum(role_scores) / len(role_scores))
    else:
        overall = min(base, 70.0)
    return clamp(overall)


def label_for(score: int) -> tuple[str, str]:
    """Return (label, streamlit color name)."""
    if score >= 80:
        return "Strong fit", "green"
    if score >= 65:
        return "Good fit", "blue"
    if score >= 50:
        return "Possible fit", "orange"
    return "Stretch", "red"

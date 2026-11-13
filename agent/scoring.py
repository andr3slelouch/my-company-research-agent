"""Deterministic fit scoring.

The model rates each dimension 0-100 with evidence; the overall score is computed
here so it is consistent and explainable across companies.
"""

from __future__ import annotations

from typing import Any

WEIGHTS: dict[str, float] = {
    "skills": 0.50,            # hard-skill overlap with what the open roles require
    "years_experience": 0.25,  # candidate's years vs. years the roles ask for
    "domain": 0.25,            # industry / problem-space familiarity
}

LABELS = {
    "skills": "Skill match",
    "years_experience": "Years of experience required",
    "domain": "Domain match",
}

ROLE_BLEND = 0.25      # share of the overall score that comes from top role matches
NO_ROLES_CAP = 70      # max overall score when no qualifying roles are open


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
    """Weighted average of the three dimensions, blended with the best role matches.

    If qualifying roles exist: 75% dimension score + 25% mean of the top-3 role
    match scores. If none were found, cap at 70: a company with nothing open for
    you is not a top fit *right now*.
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
        overall = (1 - ROLE_BLEND) * base + ROLE_BLEND * (sum(role_scores) / len(role_scores))
    else:
        overall = min(base, float(NO_ROLES_CAP))
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

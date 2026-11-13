"""Offline tests for the pure-Python core (no API calls, no Streamlit)."""

import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import prompts  # noqa: E402
from agent.jsonutil import JSONExtractionError, extract_json  # noqa: E402
from agent.report import to_markdown  # noqa: E402
from agent.resume import extract_text, load_sample, resume_hash  # noqa: E402
from agent.scoring import compute_overall, label_for  # noqa: E402


# ---------- JSON extraction ----------
def test_extract_fenced_json_after_prose():
    text = 'I searched a few sites.\n```json\n{"a": 1, "b": [1, 2]}\n```\nDone.'
    assert extract_json(text) == {"a": 1, "b": [1, 2]}


def test_extract_last_fence_wins():
    text = '```json\n{"draft": true}\n```\nrevised:\n```json\n{"draft": false}\n```'
    assert extract_json(text) == {"draft": False}


def test_extract_unfenced_with_braces_in_strings():
    text = 'Here: {"title": "Use {curly} braces", "n": 2} thanks'
    assert extract_json(text) == {"title": "Use {curly} braces", "n": 2}


def test_extract_trailing_commas():
    assert extract_json('```json\n{"a": [1, 2,], }\n```') == {"a": [1, 2]}


def test_extract_failure():
    with pytest.raises(JSONExtractionError):
        extract_json("no json here")


# ---------- Scoring ----------
BREAKDOWN = {k: {"score": 80, "evidence": "x"} for k in ("skills", "tech_stack", "experience_level", "domain", "location_logistics")}


def test_overall_without_roles_is_capped():
    assert compute_overall(BREAKDOWN, []) == 70


def test_overall_blends_top_roles():
    roles = [{"match_score": 100}, {"match_score": 90}, {"match_score": 80}, {"match_score": 10}]
    # 0.75*80 + 0.25*90 = 82.5 -> 82 (banker's rounding) or 83; accept either
    assert compute_overall(BREAKDOWN, roles) in (82, 83)


def test_overall_handles_bad_values():
    bd = {"skills": {"score": "abc"}, "tech_stack": 150, "experience_level": None}
    s = compute_overall(bd, [{"match_score": "90"}])
    assert 0 <= s <= 100


def test_labels():
    assert label_for(85)[0] == "Strong fit"
    assert label_for(40)[0] == "Stretch"


# ---------- Resume ----------
def test_sample_resume_loads():
    text = load_sample()
    assert "Thoughtworks" in text and "PySpark" in text
    assert "@" not in text  # contact email removed from the public sample
    assert len(resume_hash(text)) == 16


def test_extract_txt_and_docx():
    sample = load_sample()
    assert extract_text("cv.txt", sample.encode()) .startswith("# Luis")

    import docx

    d = docx.Document()
    for line in sample.splitlines():
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    assert "Neo4j" in extract_text("cv.docx", buf.getvalue())


def test_rejects_unknown_type_and_tiny_text():
    with pytest.raises(ValueError):
        extract_text("cv.png", b"123")
    with pytest.raises(ValueError):
        extract_text("cv.txt", b"too short")


# ---------- Prompts & report ----------
def test_prompts_render_and_contain_schema():
    profile = {"name": "Test", "core_skills": ["Java"]}
    prefs = {"remote": ["Remote"], "locations": ["Oklahoma City"]}
    for p in (
        prompts.profile_prompt("resume"),
        prompts.discover_prompt(profile, "resume", prefs, 5, ["Acme"]),
        prompts.company_prompt("Acme", profile, prefs),
        prompts.roles_prompt("Acme", profile, "resume", prefs),
        prompts.prep_prompt("Acme", profile, "resume", {}, {}),
    ):
        assert "```json" in p
    assert "Oklahoma City" in prompts.roles_prompt("Acme", profile, "resume", prefs)


def test_markdown_report():
    result = {
        "query": "Acme",
        "company": {"name": "Acme", "one_liner": "Rockets"},
        "fit": {"overall": 77, "breakdown": BREAKDOWN, "summary": "Good", "strengths": ["Java"], "gaps": [], "gap_closers": []},
        "roles": [{"title": "Backend Engineer", "url": "https://example.com/job", "match_score": 81, "gaps": ["Go"]}],
        "prep": {"questions": [{"category": "Technical", "question": "Design a cache", "why_they_ask": "x", "how_to_answer": "y"}]},
        "news": [{"headline": "Acme raises", "url": "https://example.com/n", "date": "2026-10-01"}],
        "product": {"overview": "Makes rockets", "competitors": ["Globex"]},
        "sources": [{"url": "https://example.com", "title": "Example"}],
    }
    md = to_markdown(result, "Luis")
    for needle in ("Fit score: 77/100", "Backend Engineer", "Design a cache", "Acme raises", "Globex", "Sources"):
        assert needle in md
    json.dumps(result)  # result stays JSON-serializable

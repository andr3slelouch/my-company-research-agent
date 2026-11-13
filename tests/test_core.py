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
from agent.scoring import WEIGHTS, compute_overall, label_for  # noqa: E402
from agent.news import filter_recent_news, parse_news_date  # noqa: E402
from agent.llm import DEFAULT_MODEL  # noqa: E402
from datetime import date  # noqa: E402


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
BREAKDOWN = {k: {"score": 80, "evidence": "x"} for k in ("skills", "years_experience", "domain")}


def test_overall_without_roles_is_capped():
    assert compute_overall(BREAKDOWN, []) == 70


def test_overall_blends_top_roles():
    roles = [{"match_score": 100}, {"match_score": 90}, {"match_score": 80}, {"match_score": 10}]
    # 0.75*80 + 0.25*90 = 82.5 -> 82 (banker's rounding) or 83; accept either
    assert compute_overall(BREAKDOWN, roles) in (82, 83)


def test_overall_handles_bad_values():
    bd = {"skills": {"score": "abc"}, "years_experience": 150, "domain": None}
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


# ---------- Spec: Haiku, 3-dimension scoring ----------
def test_model_is_haiku():
    assert DEFAULT_MODEL == "claude-haiku-5-5"


def test_weights_are_skills_domain_years():
    assert WEIGHTS == {"skills": 0.50, "years_experience": 0.25, "domain": 0.25}


def test_skills_weigh_double():
    roles = [{"match_score": 50}]
    strong_skills = {"skills": {"score": 100}, "years_experience": {"score": 0}, "domain": {"score": 50}}
    strong_years = {"skills": {"score": 0}, "years_experience": {"score": 100}, "domain": {"score": 50}}
    assert compute_overall(strong_skills, roles) > compute_overall(strong_years, roles)


def test_unknown_dimensions_ignored():
    bd = {**BREAKDOWN, "location_logistics": {"score": 0}}
    assert compute_overall(bd, []) == compute_overall(BREAKDOWN, [])


# ---------- 90-day news window ----------
TODAY = date(2026, 11, 13)  # cutoff = 2026-08-15


def test_parse_formats():
    assert parse_news_date("2026-10-01") == date(2026, 10, 1)
    assert parse_news_date("Oct 5, 2026") == date(2026, 10, 5)
    assert parse_news_date("2026-10-01T12:00:00Z") == date(2026, 10, 1)
    assert parse_news_date("August 2026") == date(2026, 8, 31)
    assert parse_news_date("last week") is None


def test_news_window_filters_sorts_and_flags():
    items = [
        {"headline": "Old", "date": "2026-08-14"},           # 91 days -> dropped
        {"headline": "Edge", "date": "2026-08-15"},          # exactly 90 -> kept
        {"headline": "New", "date": "November 10, 2026"},
        {"headline": "Month only", "date": "2026-09"},
        {"headline": "No date"},
        {"headline": "Future", "date": "2027-01-01"},
        {"headline": "New", "date": "2026-11-10"},           # duplicate headline
    ]
    kept, dropped = filter_recent_news(items, today=TODAY)
    heads = [i["headline"] for i in kept]
    assert dropped == 1
    assert heads[:3] == ["New", "Month only", "Edge"]
    assert kept[0]["date"] == "2026-11-10"
    assert kept[1]["date"] == "2026-09"            # not shown as a fake exact day
    assert set(heads[3:]) == {"No date", "Future"}
    assert all(i["date_unverified"] for i in kept[3:])
    assert "Old" not in heads


def test_news_cap():
    items = [{"headline": f"n{i}", "date": f"2026-11-{(i % 12) + 1:02d}"} for i in range(30)]
    kept, _ = filter_recent_news(items, today=TODAY)
    assert len(kept) == 15


def test_company_prompt_states_cutoff():
    p = prompts.company_prompt("Acme", {}, {})
    assert "90 days" in p and "up to 15" in p


# ---------- Merged from the fork: free job boards, free news, role prep, cost ----------
from agent.job_boards import slug_candidates  # noqa: E402
from agent.llm import add_usage, cost_of, new_usage  # noqa: E402
from agent.news import parse_rss  # noqa: E402
from agent.research import _attach_headlines, _attach_posting_facts  # noqa: E402

RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>Acme launches AI agent - TechCrunch</title><link>https://news.example/a</link>
<pubDate>Mon, 02 Nov 2026 10:00:00 GMT</pubDate><source url="https://techcrunch.com">TechCrunch</source></item>
<item><title>No date story</title><link>https://news.example/b</link></item>
</channel></rss>"""


def test_slug_candidates():
    assert slug_candidates("Scale AI, Inc.") == ["scaleai", "scale-ai"]
    assert slug_candidates("Stripe") == ["stripe"]
    assert slug_candidates("Anything", override=" Gong ") == ["gong"]


def test_parse_rss_strips_publisher_suffix():
    items = parse_rss(RSS)
    assert items[0] == {"headline": "Acme launches AI agent", "url": "https://news.example/a",
                        "source": "TechCrunch", "date": "2026-11-02"}
    assert items[1]["date"] == ""
    kept, dropped = filter_recent_news(items, today=TODAY)
    assert kept[0]["headline"] == "Acme launches AI agent" and kept[1]["date_unverified"]


def test_posting_facts_come_from_board_not_model():
    postings = [{"title": "Backend Engineer", "location": "Remote", "url": "https://boards/1",
                 "description": "Java", "posted": "2026-11-01", "source": "Greenhouse"}]
    roles = [
        {"index": 0, "title": "HALLUCINATED", "url": "https://fake", "match_score": 80},
        {"index": 7, "title": "Not a real posting", "match_score": 99},  # out of range: dropped
        "garbage",
    ]
    out = _attach_posting_facts(roles, postings)
    assert len(out) == 1
    assert out[0]["title"] == "Backend Engineer" and out[0]["url"] == "https://boards/1"
    assert out[0]["match_score"] == 80


def test_news_picks_map_to_real_headlines():
    headlines = [{"headline": "Real one", "url": "https://n/1", "source": "X", "date": "2026-11-01"}]
    picks = [{"index": 0, "why_it_matters": "hiring", "headline": "made up"}, {"index": 3}]
    out = _attach_headlines(picks, headlines)
    assert out == [{"headline": "Real one", "url": "https://n/1", "source": "X", "date": "2026-11-01",
                    "summary": "", "why_it_matters": "hiring", "sentiment": ""}]


def test_cost_estimate():
    u = add_usage({"input_tokens": 1_000_000, "output_tokens": 0, "searches": 2},
                  {"input_tokens": 0, "output_tokens": 1_000_000, "searches": 0})
    assert u == {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "searches": 2}
    assert round(cost_of(u, "claude-haiku-5-5"), 4) == round(0.10 + 0.50 + 0.02, 4)
    assert cost_of(new_usage()) == 0


def test_new_prompt_modes_render():
    pr = {"headline": "dev", "core_skills": ["Java"]}
    with_news = prompts.company_prompt("Acme", pr, {}, headlines=[{"headline": "Acme ships", "date": "2026-11-01"}])
    assert "Do NOT search for news" in with_news and "0 | 2026-11-01" in with_news
    with_posts = prompts.roles_prompt("Acme", pr, "resume", {}, postings=[
        {"index": 0, "title": "SWE", "location": "", "description": "Go, 3+ years"}])
    assert "Do NOT search for roles" in with_posts and "[0] SWE" in with_posts
    for p in (with_news, with_posts,
              prompts.board_match_prompt("Acme", pr, "resume", [{"title": "SWE", "location": "NYC"}], 5),
              prompts.role_prep_prompt("Acme", pr, "resume", {"title": "SWE", "description": "Go"})):
        assert "```json" in p and "{{" not in p

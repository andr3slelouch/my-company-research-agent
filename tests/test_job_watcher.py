"""Offline tests for job_watcher.py (no network, no API calls, no email)."""

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import job_watcher as jw  # noqa: E402

F = json.loads((Path(jw.ROOT) / "watchlist.json").read_text())["filters"]


def test_title_filter():
    assert jw.title_ok("Software Engineer II, Backend", F)
    assert jw.title_ok("Full Stack Developer", F)
    assert not jw.title_ok("Senior Software Engineer", F)
    assert not jw.title_ok("Sr. Backend Engineer", F)
    assert not jw.title_ok("Staff Engineer, Platform", F)
    assert not jw.title_ok("Engineering Manager", F)
    assert not jw.title_ok("Product Designer", F)


def test_location_filter_whole_word():
    assert jw.location_ok("Remote - US", F)
    assert jw.location_ok("United States", F)
    assert jw.location_ok("Oklahoma City, OK", F)
    assert jw.location_ok("Remote (US or Canada)", F)
    assert jw.location_ok("", F)
    assert jw.location_ok("Remote", F)
    assert jw.location_ok("Remote, Global", F)
    assert not jw.location_ok("Remote - Estonia", F)  # country not on any list
    assert not jw.location_ok("Remote - Canada", F)
    assert not jw.location_ok("London, UK", F)
    assert not jw.location_ok("Remote, EMEA", F)
    assert not jw.location_ok("Business Bay, Dubai", F)  # 'us' inside 'business' must not match
    assert not jw.location_ok("San Francisco, CA", F)    # on-site elsewhere: excluded by default


def test_location_uses_secondary_locations_and_countries():
    # Real ElevenLabs postings (Ashby) that the main-location-only filter dropped.
    research = {"location": "United Kingdom", "locations": ["United Kingdom", "United States", "Poland", "Bulgaria"],
                "countries": ["United Kingdom", "United States", "Poland", "Bulgaria"], "remote": True}
    fde = {"location": "San Francisco", "locations": ["San Francisco", "United States", "New York"],
           "countries": ["United States"], "remote": True}
    europe_remote = {"location": "Remote", "locations": ["Remote", "Europe", "Switzerland", "Poland", "United Kingdom"],
                     "countries": [], "remote": True}
    sf_onsite = {"location": "San Francisco", "locations": ["San Francisco"], "countries": ["United States"], "remote": False}
    sf_city_remote = {"location": "San Francisco, CA", "locations": ["San Francisco, CA"], "remote": True}
    lever_us = {"location": "Canada, Europe", "locations": ["Canada, Europe"], "countries": ["CA"], "remote": True}
    assert jw.location_ok(research, F)
    assert jw.location_ok(fde, F)
    assert jw.location_ok(sf_city_remote, F)
    assert not jw.location_ok(europe_remote, F)   # remote, but only in Europe
    assert not jw.location_ok(sf_onsite, F)       # on-site outside Oklahoma, us_onsite off
    assert jw.location_ok(sf_onsite, {**F, "us_onsite": True})
    assert not jw.location_ok(lever_us, F)
    assert "(also: United States, Poland, Bulgaria) · Remote" in jw._place_line(research)


def test_employment_filter():
    assert jw.employment_ok("FullTime", F) and jw.employment_ok("Full-time", F) and jw.employment_ok("", F)
    assert jw.employment_ok("Contract", F)
    assert not jw.employment_ok("PartTime", F) and not jw.employment_ok("Part-time", F)
    assert not jw.employment_ok("Intern", F)


def test_age_filter():
    today = date(2026, 11, 24)
    assert jw.age_ok("2026-11-20", F, today)
    assert jw.age_ok("2026-10-14", F, today)       # 6 weeks old, still open: keep
    assert not jw.age_ok("2026-08-01", F, today)
    assert jw.age_ok("", F, today)


def test_blockers_and_sponsorship():
    b, ns = jw.flags_for("Candidates must be a U.S. citizen and hold an active Secret clearance.")
    assert "US citizenship required" in b and "Security clearance" in b
    b, ns = jw.flags_for("We are unable to sponsor visas for this role.")
    assert b == [] and ns
    b, ns = jw.flags_for("Python, Postgres, Kafka. We sponsor visas.")
    assert b == [] and not ns


def _job(i, title="Software Engineer", loc="Remote - US", desc="Java and Postgres"):
    return {"id": f"u{i}", "url": f"https://x/{i}", "title": title, "location": loc,
            "posted": date.today().isoformat(), "description": desc, "company": "Acme", "source": "Greenhouse"}


def test_select_new_dedupes_and_skips(tmp_path):
    seen = jw.SeenStore(tmp_path / "seen.json")
    seen.add(["u2"])
    jobs = [_job(1), _job(1), _job(2), _job(3, title="Senior Engineer"),
            _job(4, loc="Berlin, Germany"), _job(5, desc="Must be a US citizen.")]
    new, stats = jw.select_new(jobs, F, seen)
    assert [j["id"] for j in new] == ["u1"]
    assert stats["seen"] == 1 and stats["title"] == 1 and stats["location"] == 1 and stats["blocked"] == 1
    assert "u5" in seen  # blocked jobs are remembered so they're never rechecked


def test_digest_sorted_and_escaped():
    a, b = _job(1, title="Backend <Engineer>"), _job(2)
    a.update(fit=70, why="Java match", gaps=""); b.update(fit=90, why="Node match", gaps="Go", no_sponsorship=True)
    stats = {"fetched": 10, "blocked": 0, "scored": 2}
    subject, text, body = jw.build_digest([a, b], stats, 0.002)
    assert subject.startswith("2 new job matches") and "(90)" in subject
    assert text.index("[90]") < text.index("[70]")
    assert "&lt;Engineer&gt;" in body and "no visa sponsorship" in body


def test_run_end_to_end_dry_and_real(tmp_path, monkeypatch):
    wl = {"filters": F, "companies": [{"name": "Acme", "board": "greenhouse", "slug": "acme"}]}
    (tmp_path / "wl.json").write_text(json.dumps(wl))
    monkeypatch.setenv("WATCHLIST_PATH", str(tmp_path / "wl.json"))
    monkeypatch.setenv("SEEN_PATH", str(tmp_path / "seen.json"))
    monkeypatch.setenv("SPEND_PATH", str(tmp_path / "spend.json"))
    monkeypatch.delenv("BUDGET_USD", raising=False)
    monkeypatch.setitem(jw.BOARDS, "greenhouse", lambda slug: [_job(1), _job(2)])
    monkeypatch.setattr(jw, "load_resume", lambda: "resume")

    def fake_score(jobs, resume, key, note=""):
        for j, fit in zip(jobs, (80, 30)):
            j.update(fit=fit, why="w", gaps="")
        return {"input_tokens": 1000, "output_tokens": 100, "searches": 0}

    sent = []
    monkeypatch.setattr(jw, "score_jobs", fake_score)
    monkeypatch.setattr(jw, "send_email", lambda s, t, h: sent.append(s))

    jw.run(dry_run=True)
    assert sent == [] and not (tmp_path / "seen.json").exists()
    jw.run()
    assert len(sent) == 1 and sent[0].startswith("1 new job match")
    jw.run()  # second real run: everything already seen, no email
    assert len(sent) == 1
    ledger = json.loads((tmp_path / "spend.json").read_text())
    assert ledger["runs"] == 2  # the dry run and the first real run called Claude; the third had nothing new
    assert abs(ledger["total_usd"] - 2 * jw.cost_of({"input_tokens": 1000, "output_tokens": 100})) < 1e-9


def test_no_email_when_nothing_clears_the_bar(tmp_path, monkeypatch):
    wl = {"filters": F, "companies": [{"name": "Acme", "board": "greenhouse", "slug": "acme"}]}
    (tmp_path / "wl.json").write_text(json.dumps(wl))
    for k, v in {"WATCHLIST_PATH": "wl.json", "SEEN_PATH": "seen.json", "SPEND_PATH": "spend.json"}.items():
        monkeypatch.setenv(k, str(tmp_path / v))
    monkeypatch.delenv("BUDGET_USD", raising=False)
    monkeypatch.setitem(jw.BOARDS, "greenhouse", lambda slug: [_job(1)])
    monkeypatch.setattr(jw, "load_resume", lambda: "resume")
    monkeypatch.setattr(jw, "score_jobs", lambda jobs, *a: [j.update(fit=20) for j in jobs] and
                        {"input_tokens": 10, "output_tokens": 1, "searches": 0})
    sent = []
    monkeypatch.setattr(jw, "send_email", lambda s, t, h: sent.append(s))
    jw.run()
    assert sent == []


def test_spend_ledger_budget_and_low_balance_alert(tmp_path, monkeypatch):
    monkeypatch.setenv("BUDGET_USD", "5")
    monkeypatch.setenv("LOW_BALANCE_USD", "1")
    sent = []
    monkeypatch.setattr(jw, "send_email", lambda s, t, h: sent.append(s))
    led = jw.SpendLedger(tmp_path / "spend.json")
    led.add(1.25)
    assert "$3.75 of credit left ($5.00 at start)" in led.summary()
    jw.low_balance_alert(led, dry_run=False)
    assert sent == []
    led.add(3.0)  # $0.75 left
    jw.low_balance_alert(led, dry_run=False)
    jw.low_balance_alert(led, dry_run=False)  # only warns once
    assert len(sent) == 1 and "$0.75" in sent[0]
    led.save()
    assert jw.SpendLedger(tmp_path / "spend.json").data["total_usd"] == 4.25
    monkeypatch.setenv("BUDGET_USD", "15")  # topped up: alert re-arms
    jw.low_balance_alert(led, dry_run=False)
    assert "low_alert_sent" not in led.data

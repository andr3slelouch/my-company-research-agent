# 🧭 Company Research Agent

A password-protected Streamlit app, powered by Claude Haiku, that helps job seekers find and research companies. It uses **free sources first** (real job-board postings and Google News headlines) and Claude's web search only as a fallback, so a full company deep-dive costs about **$0.04**.

- **Discover:** the agent suggests companies that are hiring for your profile right now, with an estimated fit, why it fits, an example role and an honest caveat.
- **Research any company:** type a name and get:
  1. **Fit score (0–100)**, from three dimensions scored against the company's actual open roles and your resume:
     - Skill match: **50%**
     - Years of experience required: **25%**
     - Domain match: **25%**
  2. **Open roles you qualify for**, each with a match score, years required, requirements met, gaps, a link to the real posting, and a **🎯 Prep for this role** button (requirement-by-requirement map, gap plan, role questions, 60-second pitch)
  3. **Interview prep**: a tailored 60-second pitch, talking points, 12–15 likely questions with answer strategies tied to your resume, questions to ask them, and a 7-day plan
  4. **News from the past 90 days**, up to 15 stories, newest first, each with why it matters to a job seeker
  5. **Product overview**: products, customers, business model, competitors, engineering stack
- **Paste a job description:** for Workday or custom career sites the free lookup can't read. You get the same analysis, with the fit score measured against that exact posting.
- **Cost shown everywhere:** each run shows its estimated cost and the sidebar shows the session total. Opening the same company again doesn't charge twice.
- **Any resume:** upload a PDF, DOCX, TXT or MD file. A sample resume is preloaded.
- **Export** any company report as Markdown.

## How it works

```
password gate
     │
resume ─► profile extraction (no web)
                 │
company ─► FREE: Greenhouse/Lever/Ashby postings + Google News RSS headlines
                 │
         ┌───────┴──────────────────────────────────────────────┐
         ▼                                                      ▼
 company + product + rank headlines            match titles (no web) → score real
 (1 web search; 5 if no headlines)             postings (no web); web search only
         │                                     if no supported job board
         └──────────────────────┬───────────────────────────────┘
                                ▼
                interview prep (1 web search) ─► report
```

**Fit score.** Claude rates each dimension 0–100 with one line of evidence. The overall score is then **computed in code** (`agent/scoring.py`):

```
overall = 0.75 × (0.50·skills + 0.25·years + 0.25·domain) + 0.25 × mean(top-3 role match scores)
```

If no qualifying roles are open, the overall score is capped at 70: the company may be a good match on paper, but it isn't a top fit *right now*. Location and work authorization are not scored; they appear in the hiring notes.

**Years of experience.** The rubric compares your years with what each posting asks for:
- meets the requirement: 85–100
- 1 year short: ~65
- 2 years short: ~45
- 3+ years short: under 30

**90-day news.** Headlines come from the free Google News RSS feed; Claude only picks and annotates them, and the headline, link, source and date are copied from the feed in code. If the feed returns nothing (it is unofficial and can block cloud servers), Claude's web search is used instead. `agent/news.py` enforces the window in code either way:
- anything older than 90 days is dropped, and the app tells you how many items were filtered out
- items with no reliable date are kept but tagged "date unverified"
- duplicate stories are removed

**Open roles.** The app first reads the company's public **Greenhouse, Lever or Ashby** board (free, no key; `agent/job_boards.py`). A cheap title-only call picks the closest postings, then Claude scores their full descriptions. Title, URL and location are copied from the posting in code, so roles can't be invented. If the board isn't auto-detected, enter its slug (e.g. `gong` from `boards.greenhouse.io/gong`). Companies on Workday or custom sites fall back to Claude's web search, or use **Paste a job description**.

## Security

- **Password required.** Every visitor must enter `APP_PASSWORD` before anything loads.
- **Fails closed.** If `APP_PASSWORD` or `ANTHROPIC_API_KEY` is missing, the app shows a configuration error and stops.
- **Slower guessing.** The password check uses a constant-time comparison, and each wrong attempt adds a short delay.
- **Your key stays private.** The API key lives only in the deployment's secrets and is never shown to visitors.
- **Log out** from the sidebar.

## Run locally

```bash
git clone https://github.com/<you>/company-research-agent.git
cd company-research-agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # set the key and password
streamlit run app.py
```

You need an Anthropic API key from [console.anthropic.com](https://console.anthropic.com). The web search tool must be allowed for your organization; an admin can enable it in the Claude Console.

## Deploy (free) on Streamlit Community Cloud

1. Push this repo to GitHub. Private is fine.
2. Go to [share.streamlit.io](https://share.streamlit.io) → **Create app** → pick the repo, branch `main`, file `app.py`.
3. **Advanced settings → Secrets:**
   ```toml
   ANTHROPIC_API_KEY = "sk-ant-..."
   APP_PASSWORD = "something-long-and-unique"
   ```
4. Deploy, then share the URL and the password with the people you want to use it.

## Cost

The app runs on **Claude Haiku 5.5**. Measured on real runs (Nov 2026):

| Action | Approx. cost |
|---|---|
| Company deep-dive (job board + news feed found) | ~$0.04 |
| Prep for one role | ~$0.003 |
| Company suggestions (up to 5 web searches) | ~$0.05–0.07 |

Web-search caps per deep-dive: 1 for the product overview (5 if no news feed), 5 for roles only when there's no supported job board, 1 for interview prep. Prices used for the estimate live in `PRICES` in `agent/llm.py`; check them against claude.com/pricing. To get deeper research, add `MODEL = "claude-sonnet-5-5"` to the secrets, or change `DEFAULT_MODEL` in `agent/llm.py`.

## Hourly job alerts by email

`job_watcher.py` checks every company in `watchlist.json` (public Greenhouse / Lever / Ashby boards) once an hour, keeps new postings that pass the title, location and age filters, skips ones that require US citizenship or a security clearance, scores up to 30 of them against your resume in **one** Claude Haiku call (about $0.003 per run), and emails you the ones at or above `MIN_FIT` (default 55). Jobs it has already emailed are remembered, so each one shows up only once.

```bash
python job_watcher.py --check     # which boards resolve
RESUME_PATH=~/resume.pdf python job_watcher.py --dry-run   # print the digest, no email
```

An email goes out **only when there are new jobs at or above `MIN_FIT`**; an hour with nothing new sends nothing.

**How locations are matched:** every location a posting lists is checked, not just the main one. Many US-eligible jobs say "United Kingdom" or "San Francisco" up front and list the US only as a secondary location (Ashby `secondaryLocations`, Lever `allLocations`, Greenhouse offices). A job passes if any location names the US or Oklahoma, if it's remote and lists a US city/country, or if it's plain "Remote" with no other country named. Set `"us_onsite": true` in `watchlist.json` to also keep US on-site jobs elsewhere. Jobs up to `max_age_days` (60) old are considered, because good roles often stay open for weeks. A company's `"note"` (for example an application limit) is shown next to each of its jobs.

**Everyday commands** live in `deploy.sh` (needs the `gh` CLI): `./deploy.sh push "Title" ["Body"]`, `./deploy.sh budget 14.43`, `./deploy.sh minfit 60`, `./deploy.sh run [--dry] [--max 400]`, `./deploy.sh ship "Title" ["Body"] --budget 14.43 --max 400`, `./deploy.sh status`, `./deploy.sh log`, `./deploy.sh pause|resume`. `push` refuses to commit `.env`, resumes or state files.

**Catch-up run:** after widening filters there may be hundreds of unseen jobs. Run the workflow by hand with `max_score` set to e.g. `400` (or `MAX_SCORE=400 ./run_local.sh`) to score them all at once, 30 per Claude call, instead of 30 per hour.

**Run it on your own computer:** `cp .env.example .env`, fill it in (`.env` is never committed), then `./run_local.sh --dry-run`, `./run_local.sh` (real run, emails), `./run_local.sh --check` or `./run_local.sh --spend`. The local copy keeps its own memory of sent jobs, separate from GitHub's, so for scheduled alerts use one or the other, not both, or you'll get duplicates.

**Spending:** Anthropic has no API that returns your prepaid balance, so the watcher keeps its own ledger (`.spend.json`) with the exact token cost of every run. Set `BUDGET_USD` to the credit balance the Anthropic console shows when you start (and add any later top-up to it) and every email footer, plus `--spend`, shows roughly what's left. When that falls under `LOW_BALANCE_USD` (default $1) you get one warning email. The ledger counts only this watcher; check https://console.anthropic.com/settings/billing for the real balance.

**Free hourly schedule with GitHub Actions** (`.github/workflows/job-alerts.yml`). Add these repository secrets (Settings → Secrets and variables → Actions):

| Secret | Value |
|---|---|
| `ANTHROPIC_API_KEY` | your Claude API key |
| `SMTP_USER` | your Gmail address |
| `SMTP_PASSWORD` | a Gmail [app password](https://myaccount.google.com/apppasswords) (needs 2-Step Verification), not your normal password |
| `ALERT_TO` | where to send alerts (can be the same address) |
| `RESUME_B64` | base64 of your resume, so it never lands in the repo. Secrets max out at 48 KB, so a text version is safest: `python -c "from agent.resume import extract_text; print(extract_text('r.pdf', open('r.pdf','rb').read()))" | base64 -w0` (a small PDF also works) |

Optional repository *variables*: `MIN_FIT` (threshold), `BUDGET_USD` (console balance when tracking started, plus top-ups), `LOW_BALANCE_USD`. Run it once by hand from the Actions tab (try "Dry run" first). Edit `watchlist.json` to add companies (`board` is `greenhouse`, `lever` or `ashby`; `slug` is the name in the board URL) or tune filters. Note: GitHub pauses scheduled workflows on repos with no commits for 60 days.

## Project layout

```
app.py                 Streamlit UI: password gate, discover / research / profile views
agent/llm.py           Anthropic client (Haiku), web search, pause_turn handling, JSON repair, cost estimate
agent/prompts.py       All prompts and JSON schemas
agent/research.py      Orchestration: free data first, parallel steps, search budgets, role prep
agent/job_boards.py    Free Greenhouse / Lever / Ashby postings
agent/scoring.py       Fit-score weights (skills 50 / years 25 / domain 25)
agent/news.py          Free Google News RSS, 90-day window, date parsing, dedupe, 15-item cap
agent/resume.py        PDF/DOCX/TXT parsing
agent/report.py        Markdown export
sample_data/           Sample resume (contact details removed)
tests/                 Offline unit tests (run by GitHub Actions on every push)
```

## Customizing

| Change | Where |
|---|---|
| Fit-score weights | `WEIGHTS` in `agent/scoring.py` |
| Role blend / no-roles cap | `ROLE_BLEND`, `NO_ROLES_CAP` in `agent/scoring.py` |
| News window / item cap | `NEWS_WINDOW_DAYS`, `MAX_NEWS_ITEMS` in `agent/news.py` |
| Model | `MODEL` secret, or `DEFAULT_MODEL` in `agent/llm.py` |
| Search budgets | `SEARCHES_*` in `agent/research.py` |
| Postings scored per company | `ROLE_CANDIDATES` in `agent/research.py` |
| Cost estimate prices | `PRICES` in `agent/llm.py` |

## Caveats

Job postings change daily, so confirm details on the posting itself before applying. Haiku is less thorough than larger models and may find fewer roles or news items. Interview-process details from third-party sites are marked as "reported".

## Credits

The free job-board and news sources, per-role prep, the paste-a-job-description flow and the cost display are adapted from [albertqian/company-research-agent](https://github.com/albertqian/company-research-agent) (via the fork [andr3slelouch/company-research-agent](https://github.com/andr3slelouch/company-research-agent)).

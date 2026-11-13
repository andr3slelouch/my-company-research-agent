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

# 🧭 Company Research Agent

A Streamlit app, powered by Claude with live web search, that helps job seekers find and research companies.

- **Discover:** the agent suggests companies that are hiring for your profile right now, with an estimated fit, why it fits, an example role and an honest caveat.
- **Research any company:** type a name and get:
  1. **Fit score (0–100)**, based on the company's actual open roles and your resume, broken down into skills, tech stack, experience level, domain, and location/logistics, plus strengths, gaps and how to close them
  2. **Open roles you qualify for**, each with a match score and a link to the real posting
  3. **Interview prep**: a tailored 60-second pitch, talking points, 12–16 likely questions with answer strategies tied to your resume, questions to ask them, and a 7-day plan
  4. **Recent news that matters to a job seeker** (funding, layoffs, launches, leadership changes), with why each item matters to you
  5. **Product overview**: products, customers, business model, competitors, engineering stack
- **Any resume:** upload a PDF, DOCX, TXT or MD file. A sample resume is preloaded so the app works out of the box.
- **Export** any company report as Markdown.

## How it works

```
resume ─► profile extraction (no web)
                 │
company ─┬─► [company, product & news]  ─┐   (parallel, web search)
         └─► [open roles + fit scoring] ─┤
                                         └─► interview prep ─► report
```

- Claude's server-side **web search tool** finds careers pages, ATS boards (Greenhouse, Lever, Ashby, Workday) and news. Prompts forbid inventing postings: if a role can't be verified with a URL, it isn't listed.
- The **overall fit score is computed in code** (`agent/scoring.py`), not by the model: a weighted average of the five dimensions, blended 75/25 with your top-3 role match scores. A company with no qualifying open roles is capped at 70, because it isn't a top fit *right now*.
- Results are cached per session, so switching between companies you've researched is instant.

## Run locally

```bash
git clone https://github.com/<you>/company-research-agent.git
cd company-research-agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # add your key
streamlit run app.py
```

You need an Anthropic API key from [console.anthropic.com](https://console.anthropic.com). The web search tool has to be allowed for your organization; an admin can enable it in the Claude Console. If it isn't available, turn off "Live web research" in the sidebar (roles and news will then be much less reliable).

## Deploy (free) on Streamlit Community Cloud

1. Push this repo to GitHub (public or private).
2. Go to [share.streamlit.io](https://share.streamlit.io), click **Create app**, pick the repo, branch `main`, main file `app.py`.
3. Under **Advanced settings → Secrets**, paste:
   ```toml
   ANTHROPIC_API_KEY = "sk-ant-..."
   APP_PASSWORD = "something-long"
   ```
   `APP_PASSWORD` keeps strangers from spending your API credits. Visitors can still use the app with their own key.
4. Deploy. You'll get a URL like `https://<you>-company-research-agent.streamlit.app`.

## Cost and speed

A full company deep-dive makes 4 model calls and around 10–17 web searches. With Sonnet it typically takes 1–3 minutes and costs a few tens of cents. Haiku is faster and cheaper; Opus is the most thorough. Pick a model in the sidebar.

## Project layout

```
app.py                 Streamlit UI (discover / research / profile views)
agent/llm.py           Anthropic client + web search, pause_turn handling, JSON repair
agent/prompts.py       All prompts and JSON schemas
agent/research.py      Orchestration (parallel steps)
agent/scoring.py       Deterministic fit-score weights
agent/resume.py        PDF/DOCX/TXT parsing
agent/report.py        Markdown export
sample_data/           Sample resume (contact details removed)
tests/                 Offline unit tests (run in GitHub Actions)
```

## Customizing

- **Scoring weights:** edit `WEIGHTS` in `agent/scoring.py`.
- **What "relevant news" means:** edit `company_prompt` in `agent/prompts.py`.
- **Number and style of interview questions:** edit `prep_prompt`.

## Caveats

Job postings and news change daily, so always confirm on the posting itself before applying. Interview-process details from third-party sites are marked as "reported". Fit scores are a structured opinion, not a verdict.

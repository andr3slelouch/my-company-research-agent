"""Company Research Agent — Streamlit UI.

Run locally:  streamlit run app.py
"""

from __future__ import annotations

import hashlib
import json
import os

import streamlit as st

from agent.llm import DEFAULT_MODEL, MODEL_OPTIONS, LLMError, ResearchLLM
from agent.report import to_markdown
from agent.research import build_profile, research_company, suggest_companies
from agent.resume import extract_text, load_sample, resume_hash
from agent.scoring import LABELS, clamp, label_for

st.set_page_config(page_title="Company Research Agent", page_icon="🧭", layout="wide")

NAV_DISCOVER = "🔎 Discover companies"
NAV_RESEARCH = "🏢 Research a company"
NAV_PROFILE = "📄 My profile"

# --------------------------------------------------------------------------- #
# Session state
# --------------------------------------------------------------------------- #
defaults = {
    "nav": NAV_DISCOVER,
    "profiles": {},        # (resume_hash, model) -> profile
    "research": {},        # cache key -> result
    "history": [],         # ordered list of cache keys
    "active_key": None,
    "pending_company": None,
    "suggestions": None,
    "authed": False,
}
for k, v in defaults.items():
    st.session_state.setdefault(k, v)


def _secret(name: str) -> str:
    try:
        return str(st.secrets.get(name, "") or "")
    except Exception:  # no secrets.toml present
        return os.environ.get(name, "")


# --------------------------------------------------------------------------- #
# Sidebar: access, model, resume, preferences
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.title("🧭 Research Agent")

    owner_key = _secret("ANTHROPIC_API_KEY")
    app_password = _secret("APP_PASSWORD")
    st.subheader("Access")
    user_key = st.text_input(
        "Your Anthropic API key",
        type="password",
        help="Optional if the app owner configured one. Your key is only kept in this browser session.",
    )
    api_key = user_key.strip()
    if not api_key and owner_key:
        if app_password and not st.session_state.authed:
            pw = st.text_input("App password", type="password", help="Ask the app owner, or use your own API key above.")
            if pw and pw == app_password:
                st.session_state.authed = True
                st.rerun()
            elif pw:
                st.error("Wrong password.")
        if st.session_state.authed or not app_password:
            api_key = owner_key
            st.caption("✅ Using the app's configured API key.")

    model_label = st.selectbox("Model", list(MODEL_OPTIONS), index=0)
    model = MODEL_OPTIONS.get(model_label, DEFAULT_MODEL)
    use_web = st.toggle("Live web research", value=True, help="Lets Claude search the web for open roles and news. Turn off for a faster, knowledge-only run (roles/news will be unreliable).")

    st.divider()
    st.subheader("Resume")
    upload = st.file_uploader("Upload a resume (PDF, DOCX, TXT, MD)", type=["pdf", "docx", "txt", "md"])
    resume_text = ""
    if upload is not None:
        try:
            resume_text = extract_text(upload.name, upload.getvalue())
            st.caption(f"✅ Loaded **{upload.name}** ({len(resume_text):,} chars)")
        except ValueError as e:
            st.error(str(e))
    if not resume_text:
        resume_text = load_sample()
        st.caption("Using the **sample resume** (Luis Andrade). Upload your own to replace it.")

    st.divider()
    with st.expander("🎯 Job-search preferences", expanded=False):
        p_roles = st.text_input("Target roles", placeholder="Backend Engineer, Software Engineer II")
        p_locs = st.text_input("Preferred locations", placeholder="Oklahoma City, Dallas, Remote (US)")
        p_remote = st.multiselect("Work arrangement", ["Remote", "Hybrid", "Onsite"], default=["Remote", "Hybrid"])
        p_ind = st.text_input("Industries of interest", placeholder="Fintech, Developer tools, AI")
        p_size = st.multiselect("Company size", ["Startup (<200)", "Mid-size (200-2,000)", "Large (2,000+)"])
        p_auth = st.text_input("Work authorization notes", placeholder="e.g. Authorized to work in the US / needs H-1B sponsorship")
        p_other = st.text_area("Anything else", placeholder="Salary floor, values, mission, etc.", height=70)

    def _split(s: str) -> list[str]:
        return [x.strip() for x in s.split(",") if x.strip()]

    prefs = {
        "target_roles": _split(p_roles),
        "locations": _split(p_locs),
        "remote": p_remote,
        "industries": _split(p_ind),
        "company_size": p_size,
        "work_auth": p_auth.strip(),
        "other": p_other.strip(),
    }

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
r_hash = resume_hash(resume_text)
prefs_json = json.dumps(prefs, sort_keys=True)


def get_llm() -> ResearchLLM | None:
    if not api_key:
        st.warning("Add an Anthropic API key in the sidebar to run the agent. Get one at console.anthropic.com.")
        return None
    return ResearchLLM(api_key=api_key, model=model, use_web=use_web)


def get_profile(llm: ResearchLLM) -> dict:
    key = f"{r_hash}:{model}"
    if key not in st.session_state.profiles:
        with st.spinner("Reading the resume…"):
            st.session_state.profiles[key] = build_profile(llm, resume_text)
    return st.session_state.profiles[key]


def research_key(company: str) -> str:
    raw = f"{company.strip().lower()}|{r_hash}|{model}|{use_web}|{prefs_json}"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def go_research(company: str) -> None:
    """Button callback: jump to the research view and queue a company."""
    st.session_state.pending_company = company
    st.session_state.nav = NAV_RESEARCH


def run_research(company: str) -> None:
    llm = get_llm()
    if not llm:
        return
    key = research_key(company)
    if key in st.session_state.research:
        st.session_state.active_key = key
        st.session_state.history_pick = key  # safe: selectbox renders later in the run
        return
    try:
        profile = get_profile(llm)
        with st.status(f"Researching **{company}**…", expanded=True) as status:
            result = research_company(llm, company, profile, resume_text, prefs, progress=status.write)
            status.update(label=f"Research on **{company}** complete", state="complete", expanded=False)
    except LLMError as e:
        st.error(str(e))
        return
    result["candidate"] = profile.get("name", "")
    st.session_state.research[key] = result
    if key not in st.session_state.history:
        st.session_state.history.append(key)
    st.session_state.active_key = key
    st.session_state.history_pick = key


def chips(items: list[str]) -> str:
    return " ".join(f"`{i}`" for i in items if i)


# --------------------------------------------------------------------------- #
# Renderers
# --------------------------------------------------------------------------- #
def render_fit(r: dict) -> None:
    fit = r.get("fit", {})
    score = clamp(fit.get("overall", 0))
    label, color = label_for(score)
    c1, c2 = st.columns([1, 3])
    with c1:
        st.metric("Overall fit", f"{score}/100")
        st.markdown(f"### :{color}[{label}]")
        st.caption("Weighted from the dimensions on the right, blended with your top role matches.")
    with c2:
        st.write(fit.get("summary", ""))
        for k, v in (fit.get("breakdown") or {}).items():
            v = v if isinstance(v, dict) else {"score": v}
            s = clamp(v.get("score"))
            st.progress(s / 100, text=f"**{LABELS.get(k, k)}** — {s}")
            if v.get("evidence"):
                st.caption(v["evidence"])
    a, b, c = st.columns(3)
    with a:
        st.markdown("**✅ Strengths**")
        for s in fit.get("strengths", []):
            st.markdown(f"- {s}")
    with b:
        st.markdown("**⚠️ Gaps**")
        for s in fit.get("gaps", []):
            st.markdown(f"- {s}")
    with c:
        st.markdown("**🛠️ How to close them**")
        for s in fit.get("gap_closers", []):
            st.markdown(f"- {s}")


def render_roles(r: dict) -> None:
    roles = r.get("roles", [])
    reviewed = r.get("roles_reviewed")
    if reviewed:
        st.caption(f"Reviewed ~{reviewed} postings; showing the ones you realistically qualify for.")
    if not roles:
        st.info("No qualifying open roles were found right now.")
    for role in roles:
        with st.container(border=True):
            c1, c2 = st.columns([4, 1])
            with c1:
                st.markdown(f"#### {role.get('title', 'Role')}")
                meta = " · ".join(x for x in [role.get("team"), role.get("location"), role.get("remote"), role.get("seniority"), (f"posted {role['posted']}" if role.get("posted") else "")] if x)
                st.caption(meta)
            with c2:
                ms = clamp(role.get("match_score"))
                st.metric("Match", f"{ms}")
            st.write(role.get("why_you_fit", ""))
            m1, m2 = st.columns(2)
            with m1:
                if role.get("requirements_met"):
                    st.markdown("**Requirements you meet:** " + "; ".join(role["requirements_met"]))
            with m2:
                if role.get("gaps"):
                    st.markdown("**Gaps:** " + "; ".join(role["gaps"]))
            if role.get("url", "").startswith("http"):
                st.link_button("View posting ↗", role["url"])
    if r.get("hiring_notes"):
        st.markdown(f"**Hiring notes:** {r['hiring_notes']}")


def render_prep(r: dict) -> None:
    prep = r.get("prep", {})
    if not prep:
        st.info("No interview prep generated.")
        return
    if prep.get("process_overview"):
        st.markdown(f"**Typical process:** {prep['process_overview']}")
    if prep.get("elevator_pitch"):
        st.markdown("**🎤 Your 60-second pitch**")
        st.info(prep["elevator_pitch"])

    if prep.get("talking_points"):
        st.markdown("**🔗 Talking points: their need → your evidence**")
        for t in prep["talking_points"]:
            st.markdown(f"- **{t.get('company_need')}** → {t.get('your_evidence')}")

    qs = prep.get("questions", [])
    if qs:
        st.markdown("**❓ Likely interview questions**")
        cats: dict[str, list[dict]] = {}
        for q in qs:
            cats.setdefault(q.get("category", "Other"), []).append(q)
        for cat, items in cats.items():
            st.markdown(f"*{cat}*")
            for q in items:
                with st.expander(q.get("question", "")):
                    st.markdown(f"**Why they ask:** {q.get('why_they_ask', '')}")
                    st.markdown(f"**How to answer:** {q.get('how_to_answer', '')}")

    c1, c2 = st.columns(2)
    with c1:
        if prep.get("technical_topics"):
            st.markdown("**📚 Technical topics to brush up on**")
            for t in prep["technical_topics"]:
                st.markdown(f"- **{t.get('topic')}** — {t.get('why')}" + (f"  \n  _{t['resources']}_" if t.get("resources") else ""))
        if prep.get("red_flags_to_address"):
            st.markdown("**🚩 Concerns to address proactively**")
            for f in prep["red_flags_to_address"]:
                st.markdown(f"- **{f.get('concern')}** — {f.get('how_to_address')}")
    with c2:
        if prep.get("questions_to_ask"):
            st.markdown("**🙋 Questions to ask them**")
            for q in prep["questions_to_ask"]:
                st.markdown(f"- {q}")
    if prep.get("prep_plan"):
        st.markdown("**🗓️ 7-day prep plan**")
        for d in prep["prep_plan"]:
            st.markdown(f"- **{d.get('day')} — {d.get('focus')}:** " + "; ".join(d.get("tasks", [])))


def render_news(r: dict) -> None:
    news = r.get("news", [])
    if not news:
        st.info("No recent news found.")
    icon = {"positive": "🟢", "negative": "🔴", "neutral": "⚪"}
    for n in news:
        with st.container(border=True):
            title = n.get("headline", "")
            url = n.get("url", "")
            st.markdown(f"{icon.get(n.get('sentiment', 'neutral'), '⚪')} **[{title}]({url})**" if url else f"**{title}**")
            st.caption(" · ".join(x for x in [n.get("date"), n.get("source")] if x))
            st.write(n.get("summary", ""))
            if n.get("why_it_matters"):
                st.markdown(f"**Why it matters to you:** {n['why_it_matters']}")


def render_product(r: dict) -> None:
    p = r.get("product", {})
    c = r.get("company", {})
    st.write(p.get("overview", ""))
    facts = [("Industry", c.get("industry")), ("HQ", c.get("hq")), ("Size", c.get("size")), ("Founded", c.get("founded")), ("Funding / ticker", c.get("funding_or_ticker"))]
    cols = st.columns(len(facts))
    for col, (k, v) in zip(cols, facts):
        col.markdown(f"**{k}**  \n{v or '—'}")
    if p.get("products"):
        st.markdown("**Products**")
        for prod in p["products"]:
            st.markdown(f"- **{prod.get('name')}** — {prod.get('description')}" + (f"  \n  _Relevance to you:_ {prod['relevance_to_candidate']}" if prod.get("relevance_to_candidate") else ""))
    a, b = st.columns(2)
    with a:
        if p.get("customers"):
            st.markdown(f"**Customers:** {p['customers']}")
        if p.get("business_model"):
            st.markdown(f"**Business model:** {p['business_model']}")
        if p.get("competitors"):
            st.markdown(f"**Competitors:** {', '.join(p['competitors'])}")
    with b:
        if p.get("engineering_stack"):
            st.markdown("**Engineering stack:** " + chips(p["engineering_stack"]))
        if p.get("culture_signals"):
            st.markdown("**Culture signals**")
            for s in p["culture_signals"]:
                st.markdown(f"- {s}")


def render_result(r: dict) -> None:
    c = r.get("company", {})
    head, actions = st.columns([3, 2])
    with head:
        st.header(c.get("name") or r.get("query"))
        if c.get("one_liner"):
            st.caption(c["one_liner"])
        if c.get("disambiguation"):
            st.caption(f"ℹ️ {c['disambiguation']}")
    with actions:
        b1, b2, b3 = st.columns(3)
        if str(c.get("website", "")).startswith("http"):
            b1.link_button("Website ↗", c["website"], width="stretch")
        if str(c.get("careers_url", "")).startswith("http"):
            b2.link_button("Careers ↗", c["careers_url"], width="stretch")
        b3.download_button(
            "Report ⬇",
            to_markdown(r, r.get("candidate", "")),
            file_name=f"{(c.get('name') or r.get('query', 'company')).replace(' ', '_')}_research.md",
            mime="text/markdown",
            width="stretch",
        )

    t_fit, t_roles, t_prep, t_news, t_prod, t_src = st.tabs(
        ["🎯 Fit score", f"💼 Open roles ({len(r.get('roles', []))})", "🧠 Interview prep", "📰 Recent news", "📦 Product", "🔗 Sources"]
    )
    with t_fit:
        render_fit(r)
    with t_roles:
        render_roles(r)
    with t_prep:
        render_prep(r)
    with t_news:
        render_news(r)
    with t_prod:
        render_product(r)
    with t_src:
        srcs = r.get("sources", [])
        st.caption(f"{r.get('searches', 0)} web searches · {len(srcs)} sources")
        for s in srcs:
            st.markdown(f"- [{s.get('title') or s['url']}]({s['url']})")


# --------------------------------------------------------------------------- #
# Main layout
# --------------------------------------------------------------------------- #
st.title("Company Research Agent")
st.caption("Find companies that fit your resume, then get a fit score, matching open roles, interview prep, relevant news, and a product overview.")
view = st.radio("View", [NAV_DISCOVER, NAV_RESEARCH, NAV_PROFILE], key="nav", horizontal=True, label_visibility="collapsed")

# ----- Discover ------------------------------------------------------------ #
if view == NAV_DISCOVER:
    st.subheader("Companies the agent suggests for you")
    c1, c2, c3 = st.columns([1, 2, 1])
    n = c1.slider("How many", 3, 10, 6)
    exclude = c2.text_input("Exclude companies", placeholder="Companies you've already applied to, comma-separated")
    c3.write("")
    c3.write("")
    if c3.button("✨ Suggest companies", type="primary", width="stretch"):
        llm = get_llm()
        if llm:
            try:
                profile = get_profile(llm)
                with st.spinner("Searching for companies hiring people like you… (about a minute)"):
                    st.session_state.suggestions = suggest_companies(
                        llm, profile, resume_text, prefs, n=n, exclude=[x.strip() for x in exclude.split(",") if x.strip()]
                    )
            except LLMError as e:
                st.error(str(e))

    sugg = st.session_state.suggestions
    if sugg:
        companies = sugg.get("companies", [])
        if not companies:
            st.info("No suggestions came back. Try loosening your preferences.")
        cols = st.columns(2)
        for i, comp in enumerate(companies):
            with cols[i % 2].container(border=True):
                fit = clamp(comp.get("estimated_fit"))
                label, color = label_for(fit)
                h1, h2 = st.columns([3, 1])
                h1.markdown(f"### {comp.get('name', '')}")
                h1.caption(" · ".join(x for x in [comp.get("industry"), comp.get("size"), comp.get("hq")] if x))
                h2.metric("Est. fit", fit)
                h2.markdown(f":{color}[{label}]")
                st.write(comp.get("why_fit", ""))
                if comp.get("matching_skills"):
                    st.markdown(chips(comp["matching_skills"]))
                role = comp.get("example_role") or {}
                if role.get("title"):
                    url = role.get("url", "")
                    st.markdown(f"**Example role:** [{role['title']}]({url})" if url.startswith("http") else f"**Example role:** {role['title']}")
                if comp.get("hiring_signal"):
                    st.markdown(f"**Hiring signal:** {comp['hiring_signal']}")
                if comp.get("watch_out"):
                    st.markdown(f"**Watch out:** {comp['watch_out']}")
                st.button(
                    "Deep-dive research →",
                    key=f"dd_{i}_{comp.get('name', '')}",
                    on_click=go_research,
                    args=(comp.get("name", ""),),
                    width="stretch",
                )
        st.caption("Estimated fit is a quick screen. Run a deep-dive for a fit score based on actual open roles.")
    else:
        st.info("Click **Suggest companies** to get tailored recommendations, or switch to **Research a company** to look one up yourself.")

# ----- Research ------------------------------------------------------------ #
elif view == NAV_RESEARCH:
    with st.form("search", clear_on_submit=False, border=False):
        c1, c2 = st.columns([4, 1])
        company = c1.text_input("Company", placeholder="e.g. Stripe, Paycom, Datadog, Thoughtworks", label_visibility="collapsed")
        submitted = c2.form_submit_button("Research", type="primary", width="stretch")
    if submitted and company.strip():
        run_research(company.strip())
    elif st.session_state.pending_company:
        pending = st.session_state.pending_company
        st.session_state.pending_company = None
        run_research(pending)

    if len(st.session_state.history) > 1:
        names = {k: st.session_state.research[k].get("company", {}).get("name") or st.session_state.research[k]["query"] for k in st.session_state.history}
        if st.session_state.get("history_pick") not in names:
            st.session_state.history_pick = st.session_state.active_key
        st.session_state.active_key = st.selectbox(
            "Previously researched",
            st.session_state.history[::-1],
            key="history_pick",
            format_func=lambda k: names[k],
        )

    if st.session_state.active_key and st.session_state.active_key in st.session_state.research:
        render_result(st.session_state.research[st.session_state.active_key])
    else:
        st.info("Type a company name to get a fit score, open roles you qualify for, interview prep, recent news, and a product overview.")

# ----- Profile ------------------------------------------------------------- #
else:
    st.subheader("What the agent knows about you")
    llm = get_llm()
    if llm:
        try:
            profile = get_profile(llm)
        except LLMError as e:
            st.error(str(e))
            profile = {}
        if profile:
            st.markdown(f"### {profile.get('name', '')}")
            st.write(profile.get("headline", ""))
            c1, c2, c3 = st.columns(3)
            c1.metric("Years of experience", profile.get("years_experience", "—"))
            c2.metric("Seniority", str(profile.get("seniority", "—")).title())
            c3.metric("Location", profile.get("location", "—"))
            st.markdown("**Core skills:** " + chips(profile.get("core_skills", [])))
            st.markdown("**Suggested target roles:** " + chips(profile.get("suggested_target_roles", [])))
            st.markdown("**Domains:** " + chips(profile.get("domains", [])))
            st.markdown("**Languages:** " + ", ".join(profile.get("languages_spoken", [])))
            if profile.get("standout_achievements"):
                st.markdown("**Standout achievements**")
                for a in profile["standout_achievements"]:
                    st.markdown(f"- {a}")
            st.caption("Tip: copy suggested target roles into the sidebar preferences to sharpen suggestions.")
    with st.expander("Raw resume text the agent reads"):
        st.text(resume_text)

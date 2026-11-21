"""FilingCheck web app: ask about a company's latest 10-K in plain English.

Run:  streamlit run app.py
Needs pipeline.py in the same folder and ANTHROPIC_API_KEY set.
"""
import html
import json
import math
import os
import re
import time
from datetime import date

import streamlit as st

import pipeline as P
import study
import glass
import ui_pages

LABEL = {"revenue": "Revenue", "net_income": "Net income",
         "operating_income": "Operating income", "total_assets": "Total assets"}
KEYWORDS = {  # checked in this order; operating first so "operating profit" is not "profit"
    "operating_income": r"operating\s+(?:income|profit)|income\s+from\s+operations",
    "revenue": r"revenue|sales|top\s+line",
    "net_income": r"net\s+income|profit|earnings|bottom\s+line",
    "total_assets": r"assets?",
}
EXAMPLES = ["Microsoft revenue and net income",
            "How big are Apple's total assets?",
            "Walmart operating income"]


def pick_fields(q):
    found = []
    for field, pat in KEYWORDS.items():
        if re.search(pat, q, re.I):
            found.append(field)
            q = re.sub(pat, " ", q, flags=re.I)
    return [f for f in P.FIELDS if f in found] or list(P.FIELDS)


def ask_company(q):
    """Ask the model which company the question is about (cheap, one short call)."""
    import anthropic
    prompt = ('Which company is this question about? Reply with JSON only: '
              '{"company": "<official SEC registrant name or ticker, e.g. Google -> Alphabet>"} '
              'or {"company": null} if none.\nQuestion: ' + q)
    resp = anthropic.Anthropic().messages.create(
        model=P.MODEL, max_tokens=200, messages=[{"role": "user", "content": prompt}])
    raw = "".join(b.text for b in resp.content if b.type == "text")
    try:
        return json.loads(raw[raw.index("{"): raw.rindex("}") + 1]).get("company")
    except ValueError:
        return None


@st.cache_data(ttl=86400, show_spinner=False)
def sec_companies():
    return list(P.sec_get("https://www.sec.gov/files/company_tickers.json").json().values())


def resolve(name):
    """Company name or ticker -> (ticker, official name, 10-digit CIK)."""
    n = name.strip().lower()
    rows = sec_companies()
    exact = [r for r in rows if r["ticker"].lower() == n]
    starts = [r for r in rows if r["title"].lower().startswith(n)]
    contains = [r for r in rows if n in r["title"].lower()]
    for group in (exact, starts, contains):
        if group:
            r = min(group, key=lambda x: len(x["title"]))  # shortest = parent company
            return r["ticker"], r["title"], str(r["cik_str"]).zfill(10)
    return None


def _step(trace, title, body, t0, status="ok", code=None):
    trace.append({"title": title, "html": body, "seconds": time.time() - t0, "status": status, "code": code or []})


@st.cache_data(show_spinner=False)
def analyze(ticker, cik, model, abstain):
    """Run one lookup and record every step so the page can show how the answer was produced."""
    cell = "B+F" if abstain else "B"
    trace = []
    t = time.time()
    info = P.get_latest_10k(cik)
    _step(trace, "Find the latest 10-K on SEC EDGAR",
          f'Asked <span class="tag">data.sec.gov/submissions/CIK{cik}.json</span> for the newest form 10-K. Found accession '
          f'<span class="tag">{info["accession"]}</span>, fiscal year ended {info["period_end"]}, filed {info["filed"]}. '
          'This accession number is what ties the answer key to this exact filing.', t)
    t = time.time()
    path = P.download_filing(ticker, cik, info)
    text = P.filing_text(path)
    _step(trace, "Download the filing and turn it into plain text",
          f'Saved the main document <span class="tag">{esc(info["doc"])}</span> and removed the HTML, leaving {len(text):,} characters of text.', t)
    t = time.time()
    secs = P.find_sections(text)
    section_text = "\n\n".join(v for v in secs.values() if v)
    missing = [k for k, v in secs.items() if not v]
    body = ("The finder looks for the heading of each statement followed by a table of numbers. "
            + " ".join(f'<b>{k.title()} statement:</b> {"found, " + format(len(v), ",") + " characters." if v else "NOT found."}' for k, v in secs.items()))
    _step(trace, "Locate the income statement and balance sheet", body, t,
          "ok" if section_text and not missing else "warn",
          [("The text the model will be shown (exactly what is sent)", section_text, f"{ticker}_statements.txt")] if section_text else None)
    out = {"info": info, "section_text": section_text, "missing": missing, "trace": trace, "model": model, "cell": cell}
    if not section_text:
        return out
    ctx = {"section_text": section_text, "company": ticker, "period_end": info["period_end"], "chunks_text": ""}
    prompt = study.build_prompt(cell, ctx)
    t = time.time()
    raw, tin, tout = study.call_model(prompt, model)
    cost = study.cost_of(model, tin, tout)
    parsed = study.parse_reply(raw)
    _step(trace, f"Ask {model} to extract the four numbers",
          f'Condition <b>{cell}</b>: {esc(study.CELLS[cell]["label"])}. Sent {tin:,} tokens, received {tout:,} tokens, cost about ${cost:.4f}. '
          'The model is told to return JSON with a value, unit, period and a verbatim quote for each field. '
          + ("It is allowed to answer null when unsure." if abstain else "It is not told it may decline, so it may guess.")
          + ("" if parsed is not None else " <b>The reply was not valid JSON.</b>"),
          t, "ok" if parsed is not None else "bad",
          [("The prompt (conditions are listed on the Study page)", prompt, f"{ticker}_prompt.txt"), ("The model's raw reply", raw, f"{ticker}_reply.txt")])
    t = time.time()
    key = P.xbrl_key(cik, info["accession"], info["period_end"])
    lines = []
    for f in P.FIELDS:
        k = key.get(f)
        if k:
            alt = "".join(f' &middot; also <span class="tag">{esc(tg)}</span> = {esc(money(v))}' for tg, v in k["alternatives"])
            lines.append(f'<b>{LABEL[f]}:</b> {esc(money(k["value"]))} from <span class="tag">{esc(k["tag"])}</span>{alt}')
        else:
            lines.append(f'<b>{LABEL[f]}:</b> the SEC has no tagged value, so this cannot be verified')
    _step(trace, "Fetch the SEC's own value for each field (the answer key)",
          'Source: <span class="tag">data.sec.gov/api/xbrl/companyfacts</span>. Values are kept only if they belong to this accession number and this fiscal year '
          '(full-year periods only for flow items such as revenue). The first tag that has a value is the key; other tags are shown when they exist.<br>' + "<br>".join(lines), t)
    out.update({"parsed": parsed, "key": key, "raw": raw})
    return out


def money(v):
    if v is None:
        return "not found"
    a = abs(v)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if a >= div:
            return f"{'-' if v < 0 else ''}${a / div:,.1f}{suf}"
    return f"${v:,.0f}"


def judge(field, res):
    """-> (value, status, message, quote, quote_ok)"""
    item = (res.get("parsed") or {}).get(field)
    item = item if isinstance(item, dict) else {}
    value = P.to_number(item.get("value"))
    quote = item.get("quote") or ""
    key = (res.get("key") or {}).get(field)
    kv = key["value"] if key else None
    cls = P.classify(value, kv, res["section_text"])
    quote_ok = P.norm(quote) in P.norm(res["section_text"]) if quote else None
    if cls == "match":
        return value, "ok", "Matches the SEC's official data", quote, quote_ok
    if cls in ("wrong_scale", "wrong_value", "not_in_text"):
        return value, "bad", f"Does NOT match the SEC's official value ({money(kv)})", quote, quote_ok
    if cls == "missing":
        return None, "bad", f"The model did not find it (SEC value: {money(kv)})", quote, quote_ok
    if value is None:
        return None, "warn", "Not reported for this company", quote, quote_ok
    return value, "warn", "No official SEC value to check against, so treat with care", quote, quote_ok


REPO = "https://github.com/WZH6489/Measuring-LLM-Accuracy-and-Fabrication-on-SEC-10-K-Extraction"


def ask_companies(q):
    """Which companies is the question about? Up to three, so you can compare."""
    import anthropic
    prompt = ('Which companies is this question about? Reply with JSON only: {"companies": ["<official SEC registrant '
              'name or ticker, e.g. Google -> Alphabet>"]}. Use an empty list if none.\nQuestion: ' + q)
    resp = anthropic.Anthropic().messages.create(
        model=P.MODEL, max_tokens=300, messages=[{"role": "user", "content": prompt}])
    raw = "".join(b.text for b in resp.content if b.type == "text")
    try:
        data = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
    except ValueError:
        return []
    names = data.get("companies") or ([data["company"]] if data.get("company") else [])
    return [n for n in names if isinstance(n, str) and n.strip()][:3]


@st.cache_data(show_spinner=False)
def history(cik, tags):
    """Last five fiscal years of official SEC values for each field: {field: [(period_end, value)]}."""
    facts = P.sec_get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json").json()["facts"].get("us-gaap", {})
    out = {}
    for field, tag in tags:
        best = {}
        for r in facts.get(tag, {}).get("units", {}).get("USD", []):
            if r.get("form") != "10-K":
                continue
            if field in P.FLOW_FIELDS:
                if "start" not in r:
                    continue
                if not 340 <= (date.fromisoformat(r["end"]) - date.fromisoformat(r["start"])).days <= 380:
                    continue
            if r["end"] not in best or r.get("filed", "") > best[r["end"]][0]:
                best[r["end"]] = (r.get("filed", ""), r["val"])
        out[field] = [(e, v) for e, (_, v) in sorted(best.items())][-5:]
    return out


CSS = """<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@200;300;400;500;600&family=JetBrains+Mono:wght@400;500&display=swap');
:root {
  color-scheme: dark;
  --fc-bg: #05070d; --fc-fg: #f2f5f9; --fc-muted: rgba(226,232,240,.72); --fc-faint: rgba(226,232,240,.46); --fc-hair: rgba(255,255,255,.08);
  --fc-sans: -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Inter', system-ui, sans-serif;
  --fc-mono: 'JetBrains Mono', ui-monospace, 'SF Mono', monospace;
  /* teal for data and highlights, sky blue for links; status colours for verdicts */
  --fc-accent: #5eead4; --fc-link: #7cc4ff; --fc-ok: #34d399; --fc-bad: #ff7a7a; --fc-warn: #f5c463;
  /* Liquid glass: colorless tint and a polished rim. Colour comes from the sky behind. */
  --glass-tint-top: rgba(255,255,255,.07); --glass-tint-bottom: rgba(179,190,210,.04);
  --glass-tint-top-hover: rgba(255,255,255,.11); --glass-tint-bottom-hover: rgba(179,190,210,.08);
  --glass-rim-top: rgba(255,255,255,.36); --glass-rim-hairline: rgba(255,255,255,.07); --glass-rim-bottom: rgba(255,255,255,.04);
  --glass-border: rgba(255,255,255,.1); --glass-shadow: 0 24px 60px rgba(0,0,0,.45);
  --glass-menu-surface: #0d121c;
  --glass-radius: 14px; --glass-radius-lg: 20px; --glass-ease: cubic-bezier(.16,1,.3,1);
}
html, body, .stApp, .stMarkdown, input, button, p, div { font-family: var(--fc-sans); }
.stApp { background: var(--fc-bg); color: var(--fc-fg); letter-spacing:-.011em; -webkit-font-smoothing:antialiased; }
[data-testid="stMain"], .stApp { overflow-x:hidden; }
/* Backdrop: the rendered night-sky image (tools/make_background.py), fixed behind everything,
   with a gentle darkening low down so long pages stay readable. */
.stApp::before { content:""; position:fixed; inset:0; z-index:0; pointer-events:none;
  background: linear-gradient(180deg, rgba(5,7,13,0) 55%, rgba(5,7,13,.45)), url('/app/static/bg.jpg') center / cover no-repeat, #05070d; }
@keyframes fcUp { from { opacity:0; transform: translateY(16px); } to { opacity:1; transform:none; } }
@keyframes fcGrow { from { transform: scaleY(0); } to { transform: scaleY(1); } }
[data-testid="stAppViewContainer"], .block-container { position:relative; z-index:1; background:transparent; }
header[data-testid="stHeader"], [data-testid="stToolbar"], #MainMenu, footer, [data-testid="stSidebarNav"], [data-testid="stSidebarCollapsedControl"] { display:none !important; }
.block-container { max-width: 1120px; padding-top: 1rem; padding-bottom: 3rem; }
[data-testid="stElementContainer"]:has(iframe) { position:absolute; height:0; overflow:hidden; }
[data-testid="InputInstructions"] { display:none !important; }
[data-testid="stElementContainer"]:has(.fc-hero), [data-testid="stElementContainer"]:has(.fs-hero),
[data-testid="stMarkdownContainer"]:has(.fc-hero), [data-testid="stMarkdownContainer"]:has(.fs-hero) { overflow:visible !important; }

/* ---- Glass tiers ---------------------------------------------------------------- */
.fc-lg, [data-testid="stForm"], [data-testid="stExpander"] details {
  position:relative; border:1px solid var(--glass-border) !important;
  background: linear-gradient(180deg, var(--glass-tint-top), var(--glass-tint-bottom)) !important;
  box-shadow: var(--glass-shadow), inset 0 1px 0 var(--glass-rim-top), inset 0 0 0 1px var(--glass-rim-hairline), inset 0 -1px 0 var(--glass-rim-bottom);
  backdrop-filter: blur(14px) saturate(140%); -webkit-backdrop-filter: blur(14px) saturate(140%); }
.fc-chip, .stButton button, .stDownloadButton button {
  border:1px solid var(--glass-border) !important; border-radius:999px !important;
  background: linear-gradient(180deg, var(--glass-tint-top), var(--glass-tint-bottom)) !important;
  box-shadow: inset 0 1px 0 var(--glass-rim-top);
  backdrop-filter: blur(10px) saturate(140%); -webkit-backdrop-filter: blur(10px) saturate(140%); }

/* ---- Navigation: brand, a centred glass pill, a call to action ------------------ */
.fc-nav { display:grid; grid-template-columns:1fr auto 1fr; align-items:center; gap:16px; padding:6px 0; animation: fcUp .6s var(--glass-ease) both; }
.fc-nav a, .fc-link { text-decoration:none !important; }
.fc-brand { display:flex; align-items:center; gap:10px; text-align:left; color:var(--fc-fg) !important; font-size:.95rem; font-weight:500; letter-spacing:-.01em; }
.fc-brand svg { width:26px; height:26px; color:var(--fc-fg); }
.fc-brand small { display:block; font-size:.72rem; font-weight:400; color:var(--fc-faint); letter-spacing:0; margin-top:1px; }
.fc-links { display:flex; gap:2px; border-radius:999px; padding:5px; }
.fc-links a { color:var(--fc-muted) !important; font-size:.84rem; font-weight:400; padding:8px 16px; border-radius:999px; transition: color .15s, background .15s; }
.fc-links a:hover { color:var(--fc-fg) !important; }
.fc-links a.on { color:var(--fc-fg) !important; background:rgba(255,255,255,.1); }
.fc-cta { justify-self:end; color:var(--fc-fg) !important; font-size:.86rem; font-weight:500; padding:11px 20px; white-space:nowrap; transition: background .2s; }
.fc-cta:hover { background: linear-gradient(180deg, var(--glass-tint-top-hover), var(--glass-tint-bottom-hover)) !important; }
@media (max-width: 860px) { .fc-nav { grid-template-columns:1fr auto; } .fc-links { display:none; } }

/* ---- Hero ----------------------------------------------------------------------- */
.fc-hero { position:relative; isolation:isolate; text-align:center; padding:0; }
.fc-chip { display:inline-flex; align-items:center; gap:9px; padding:6px 14px 6px 8px; font-size:.8rem; font-weight:400; color:var(--fc-muted); animation: fcUp .6s .05s var(--glass-ease) both; }
.fc-chip b { font-weight:600; color:var(--fc-fg); }
.fc-tick-row { display:inline-flex; } .fc-tick-row i { font-style:normal; font-family:var(--fc-mono); font-size:.58rem; font-weight:500; width:26px; height:26px; border-radius:50%;
  display:grid; place-items:center; background:#0d1626; color:var(--fc-accent); box-shadow:0 0 0 1.5px #05070d, inset 0 0 0 1px rgba(94,234,212,.35); margin-left:-6px; }
.fc-tick-row i:first-child { margin-left:0; }
.fc-h1 { font-weight:300; font-size:clamp(2.9rem,7vw,5.6rem); line-height:1.02; letter-spacing:-.05em; margin:24px 0 20px; color:var(--fc-fg);
  text-shadow:0 2px 30px rgba(0,0,0,.4); animation: fcUp .9s .1s var(--glass-ease) both; }
.fc-sub { max-width:520px; margin:0 auto 34px; font-size:clamp(1rem,1.5vw,1.12rem); font-weight:400; line-height:1.55; color:var(--fc-muted); animation: fcUp .9s .2s var(--glass-ease) both; }

/* ---- Search: one glass pill with the button inside ------------------------------ */
[data-testid="stForm"] { max-width:600px; margin:0 auto; padding:6px 6px 6px 10px !important; border-radius:999px !important; animation: fcUp .9s .3s var(--glass-ease) both; }
[data-testid="stForm"] [data-testid="stHorizontalBlock"] { gap:6px !important; align-items:center !important; }
[data-testid="stTextInputRootElement"], .stTextInput [data-baseweb="base-input"], .stTextInput [data-baseweb="input"] { background:transparent !important; border:none !important; box-shadow:none !important; }
.stTextInput input { background:transparent !important; border:none !important; color:var(--fc-fg) !important; -webkit-text-fill-color:var(--fc-fg); caret-color:var(--fc-accent); padding:.7rem .8rem !important; font-size:1rem; }
.stTextInput input::placeholder { color:var(--fc-faint); -webkit-text-fill-color:var(--fc-faint); }
[data-testid="stFormSubmitButton"] button { border-radius:999px !important; min-height:2.9rem; background:rgba(242,245,249,.94) !important; border:1px solid transparent !important;
  box-shadow: 0 6px 22px rgba(160,220,255,.16), inset 0 1px 0 #fff; transition: transform .2s var(--glass-ease), background .2s; }
[data-testid="stFormSubmitButton"] button:hover { transform: translateY(-1px); background:#fff !important; }
[data-testid="stFormSubmitButton"] button p { color:#06080e !important; font-weight:600; font-size:.92rem; }
.stButton button, .stDownloadButton button { color:var(--fc-muted) !important; padding:.38rem 1rem; transition: transform .2s var(--glass-ease), color .15s, background .15s; }
.stButton button p, .stDownloadButton button p { font-size:.82rem; color:inherit !important; }
.stButton button:hover, .stDownloadButton button:hover { transform: translateY(-1px); color:var(--fc-fg) !important;
  background: linear-gradient(180deg, var(--glass-tint-top-hover), var(--glass-tint-bottom-hover)) !important; }
.fc-row-l { text-align:center; font-family:var(--fc-mono); font-size:.66rem; letter-spacing:.16em; text-transform:uppercase; color:var(--fc-faint); margin:14px 0 4px; }

/* ---- "What we found": a row of glass cards on the horizon ------------------------ */
.fc-found { margin-top:110px; }
.fc-found-h { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:16px; }
.fc-found-h span { font-size:.92rem; color:var(--fc-muted); } .fc-found-h span::before { content:"\\201C"; font-size:1.4rem; line-height:0; vertical-align:-.3em; margin-right:8px; color:var(--fc-fg); }
.fc-found-g { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; }
.fc-f { border-radius:var(--glass-radius-lg); padding:18px 20px 20px; text-align:left; animation: fcUp .8s .35s var(--glass-ease) both; transition: transform .22s var(--glass-ease); }
.fc-f:hover { transform: translateY(-3px); }
.fc-f small { display:block; font-family:var(--fc-mono); font-size:.64rem; letter-spacing:.12em; text-transform:uppercase; color:var(--fc-faint); }
.fc-f b { display:block; font-weight:300; font-size:2.6rem; letter-spacing:-.045em; line-height:1; margin:22px 0 10px; font-variant-numeric:tabular-nums; }
.fc-f b.ok { color:var(--fc-accent); } .fc-f b.bad { color:var(--fc-bad); }
.fc-f p { font-size:.86rem; color:var(--fc-muted); line-height:1.5; margin:0; }
.fc-f-foot { margin-top:18px; padding-top:12px; border-top:1px solid var(--fc-hair); font-size:.72rem; color:var(--fc-faint); }
@media (max-width: 860px) { .fc-found-g { grid-template-columns:1fr 1fr; } }

/* ---- Result panel --------------------------------------------------------------- */
.fc-panel { border-radius:26px; padding:30px 32px 26px; margin-top:28px; overflow:hidden; animation: fcUp .6s var(--glass-ease) both; }
.fc-co { display:flex; justify-content:space-between; align-items:flex-end; gap:16px; flex-wrap:wrap; margin-bottom:16px; }
.fc-co-name { font-size:2.2rem; font-weight:300; letter-spacing:-.04em; line-height:1.05; }
.fc-tick { font-family:var(--fc-mono); font-size:.76rem; font-weight:500; color:var(--fc-accent); margin-left:12px; letter-spacing:.06em; vertical-align:middle; }
.fc-co-meta { color:var(--fc-muted); font-size:.86rem; margin-top:8px; }
.fc-link { font-size:.86rem; font-weight:500; color:var(--fc-link) !important; white-space:nowrap; }
.fc-link:hover { text-decoration:underline !important; text-underline-offset:3px; }
.fc-banner { display:flex; align-items:center; gap:9px; font-size:.9rem; color:var(--fc-muted); margin:0 0 26px; }
.fc-banner::before { content:""; flex:0 0 8px; height:8px; border-radius:50%; }
.fc-banner.ok::before { background:var(--fc-ok); box-shadow:0 0 10px var(--fc-ok); }
.fc-banner.bad::before { background:var(--fc-bad); box-shadow:0 0 10px var(--fc-bad); }
.fc-banner.warn::before { background:var(--fc-warn); box-shadow:0 0 10px var(--fc-warn); }
.fc-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(210px,1fr)); gap:28px 30px; }
.fc-card { animation: fcUp .6s var(--glass-ease) both; }
.fc-card:nth-child(2) { animation-delay:.05s; } .fc-card:nth-child(3) { animation-delay:.1s; } .fc-card:nth-child(4) { animation-delay:.15s; }
.fc-label { font-family:var(--fc-mono); font-size:.66rem; letter-spacing:.14em; text-transform:uppercase; color:var(--fc-faint); }
.fc-value { font-size:2.3rem; font-weight:300; letter-spacing:-.045em; margin:8px 0 2px; font-variant-numeric:tabular-nums; }
.fc-exact { font-family:var(--fc-mono); font-size:.72rem; color:var(--fc-faint); margin-bottom:10px; }
.fc-pill { display:inline-flex; align-items:center; gap:6px; font-size:.78rem; font-weight:600; white-space:nowrap; }
.fc-pill.ok { color:var(--fc-ok); } .fc-pill.bad { color:var(--fc-bad); } .fc-pill.warn { color:var(--fc-warn); }
.fc-note { font-size:.8rem; color:var(--fc-muted); margin-top:6px; line-height:1.45; }
.fc-yoy { font-size:.8rem; font-weight:600; margin-top:12px; font-variant-numeric:tabular-nums; } .fc-yoy span { font-weight:400; color:var(--fc-faint); }
.fc-yoy.up { color:var(--fc-ok); } .fc-yoy.down { color:var(--fc-bad); }
.fc-spark { width:100%; max-width:240px; height:36px; margin-top:8px; display:block; } .fc-spark rect { transform-origin: 50% 100%; transform-box: fill-box; animation: fcGrow .6s .2s var(--glass-ease) both; }
.fc-spark-l { max-width:240px; display:flex; justify-content:space-between; font-family:var(--fc-mono); font-size:.64rem; color:var(--fc-faint); margin-top:4px; }
.fc-ratios { display:flex; flex-wrap:wrap; gap:6px 26px; margin-top:28px; align-items:baseline; }
.fc-ratio { font-size:.86rem; color:var(--fc-muted); }
.fc-ratio b { font-weight:600; margin-left:7px; color:var(--fc-fg); font-variant-numeric:tabular-nums; } .fc-ratios small { color:var(--fc-faint); font-size:.76rem; }
[data-testid="stExpander"] { margin-top:12px; } [data-testid="stExpander"] details { border-radius:var(--glass-radius) !important; }
[data-testid="stExpander"] summary, [data-testid="stExpander"] summary p { color:var(--fc-fg) !important; font-weight:400; background:transparent !important; }
.fc-ev { padding:14px 0; } .fc-ev:first-child { padding-top:2px; }
.fc-ev-h { display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap; font-size:.9rem; }
.fc-ev-h span { font-family:var(--fc-mono); font-size:.74rem; color:var(--fc-faint); }
.fc-quote { font-family:var(--fc-mono); font-size:.78rem; border-left:2px solid var(--fc-accent); padding:4px 0 4px 14px; margin:10px 0 6px; white-space:pre-wrap; word-break:break-word; color:var(--fc-fg); }
.fc-qok { font-size:.78rem; color:var(--fc-ok); } .fc-qbad { font-size:.78rem; color:var(--fc-bad); font-weight:600; }

/* ---- How it works + footer ------------------------------------------------------ */
.fc-sec { margin-top:120px; scroll-margin-top:20px; }
.fc-sec-k { font-family:var(--fc-mono); font-size:.72rem; letter-spacing:.2em; text-transform:uppercase; color:var(--fc-accent); margin-bottom:14px; }
.fc-sec-t { font-weight:300; font-size:clamp(2rem,4.4vw,3.2rem); line-height:1.06; letter-spacing:-.045em; margin-bottom:34px; max-width:720px; }
.fc-steps { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:14px; }
.fc-step { position:relative; border-radius:var(--glass-radius-lg); padding:24px 26px 28px; transition: transform .22s var(--glass-ease); } .fc-step:hover { transform: translateY(-3px); }
.fc-step::after { content:"\\00d7"; position:absolute; right:20px; top:16px; color:var(--fc-faint); font-size:1rem; }
.fc-step-n { font-family:var(--fc-mono); font-size:.72rem; letter-spacing:.14em; color:var(--fc-faint); }
.fc-step-t { font-size:1.35rem; font-weight:400; margin:54px 0 8px; letter-spacing:-.03em; } .fc-step-d { font-size:.92rem; color:var(--fc-muted); line-height:1.55; }
.fc-foot { margin-top:90px; padding-top:22px; border-top:1px solid var(--fc-hair); display:flex; justify-content:space-between; gap:12px 24px; flex-wrap:wrap; font-size:.78rem; color:var(--fc-faint); line-height:1.6; }
.fc-foot b { color:var(--fc-muted); font-weight:600; }
.fc-foot a { color:var(--fc-faint) !important; text-decoration:none !important; } .fc-foot a:hover { color:var(--fc-fg) !important; }
[data-testid="stSpinner"] { justify-content:center; } [data-testid="stSpinner"] p { color:var(--fc-muted) !important; }
div[data-baseweb="popover"] > div, div[data-baseweb="popover"] ul { background: color-mix(in srgb, var(--glass-menu-surface) 92%, transparent) !important;
  backdrop-filter: blur(24px) saturate(150%); -webkit-backdrop-filter: blur(24px) saturate(150%); border-radius:var(--glass-radius) !important; }
@media (max-width: 640px) { .fc-panel { padding:24px 22px; border-radius:22px; } .fc-co-name { font-size:1.8rem; }
  .fc-found { margin-top:80px; } .fc-found-g { grid-template-columns:1fr; } [data-testid="stForm"] { border-radius:28px !important; padding:8px !important; } }
@media (prefers-reduced-motion: reduce) { * { animation: none !important; transition: none !important; } }
</style>"""
TYPER = """<script>
const W = window.parent, doc = W.document, phrases = __PHRASES__;
if (W.__fcTimer) clearTimeout(W.__fcTimer);
let p = 0, i = 0, del = false;
function tick() {
  const el = doc.querySelector('.stTextInput input'); let wait = 65;
  if (el) { const s = phrases[p];
    if (!del) { i++; if (i >= s.length) { del = true; wait = 1900; } }
    else { i--; wait = 26; if (i <= 0) { del = false; p = (p + 1) % phrases.length; wait = 450; } }
    el.setAttribute('placeholder', s.slice(0, i) + '|'); }
  W.__fcTimer = setTimeout(tick, wait);
}
tick();
</script>"""
TYPED = ["Microsoft revenue and net income", "Compare Apple and Nvidia operating income", "How big are Walmart's total assets?",
         "What did Coca-Cola earn last year?", "Tesla vs Ford revenue"]
PILL = {"ok": "&#10003; Verified", "bad": "&#10007; Mismatch", "warn": "Unverified"}
esc = html.escape


def nice_date(iso):
    d = date.fromisoformat(iso)
    return f"{d:%B} {d.day}, {d.year}"


def set_question(text):
    st.session_state.q = text
    st.session_state.go = True


def lookup(q):
    """Run the whole lookup and keep the results so they survive page reruns."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        st.error("ANTHROPIC_API_KEY is not set. Stop the app, run  export ANTHROPIC_API_KEY=...  and start it again.")
        return
    try:
        t0 = time.time()
        with st.spinner("Working out which company you mean..."):
            hits = []
            for name in ask_companies(q):
                h = resolve(name)
                if h and h not in hits:
                    hits.append(h)
        if not hits:
            st.warning("I couldn't tell which company you mean. Try the company name or its stock ticker.")
            return
        results = []
        for ticker, title, cik in hits:
            with st.spinner(f"Reading {title}'s latest 10-K (the first time can take a minute)..."):
                res = analyze(ticker, cik, st.session_state.get("model", P.MODEL), bool(st.session_state.get("abstain")))
                tags = tuple((f, k["tag"]) for f, k in (res.get("key") or {}).items() if k)
                try:
                    hist = history(cik, tags)
                except Exception:
                    hist = {}
            rs = {"title": "Work out which company you mean", "seconds": time.time() - t0, "status": "ok", "code": [],
                  "html": f'The question was sent to {esc(P.MODEL)} to name the company, then matched to the SEC\'s list of registrants '
                          f'(<span class="tag">company_tickers.json</span>): <b>{esc(title)}</b>, ticker {esc(ticker)}, CIK {cik}. '
                          'Which numbers you asked for was decided by simple keyword rules.'}
            results.append({"ticker": ticker, "title": title, "cik": cik, "res": res, "hist": hist, "resolve": rs})
    except Exception as e:
        st.error(f"Something went wrong: {type(e).__name__}: {e}")
        return
    st.session_state.results = results
    st.session_state.fields = pick_fields(q)
    st.session_state.recent = ([q] + [x for x in st.session_state.get("recent", []) if x != q])[:4]


def trend(field, r):
    """Year-over-year change and a five-year bar chart, both from official SEC values."""
    hist = r["hist"].get(field) or []
    ends = [e for e, _ in hist]
    end = r["res"]["info"]["period_end"]
    out = ""
    if end in ends and ends.index(end) > 0:
        cur, prev = hist[ends.index(end)][1], hist[ends.index(end) - 1][1]
        if prev:
            pct = 100 * (cur - prev) / abs(prev)
            out += (f'<div class="fc-yoy {"up" if pct >= 0 else "down"}">{"&#9650;" if pct >= 0 else "&#9660;"} '
                    f'{abs(pct):.1f}% <span>vs prior year</span></div>')
    if len(hist) >= 2:
        hi = max(abs(v) for _, v in hist) or 1
        bw = 100 / len(hist)
        bars = ""
        for i, (e, v) in enumerate(hist):
            h = max(2, 30 * abs(v) / hi)
            fill = "#ff7a7a" if v < 0 else ("#5eead4" if i == len(hist) - 1 else "rgba(233,236,242,.28)")
            bars += (f'<rect x="{i * bw + bw * .16:.1f}" y="{32 - h:.1f}" width="{bw * .68:.1f}" height="{h:.1f}" rx="1.5" fill="{fill}">'
                     f'<title>FY{e[:4]}: {esc(money(v))}</title></rect>')
        out += (f'<svg class="fc-spark" viewBox="0 0 100 32" preserveAspectRatio="none">{bars}</svg>'
                f'<div class="fc-spark-l"><span>FY{hist[0][0][:4]}</span><span>FY{hist[-1][0][:4]}</span></div>')
    return out


def ratios(res):
    """Margins worked out from the model's figures, shown only when both inputs were verified."""
    j = {f: judge(f, res) for f in P.FIELDS}
    good = lambda f: j[f][1] == "ok" and j[f][0]
    items = []
    for name, top, bottom in (("Net margin", "net_income", "revenue"), ("Operating margin", "operating_income", "revenue"),
                              ("Return on assets", "net_income", "total_assets")):
        if good(top) and good(bottom):
            items.append(f'<span class="fc-ratio">{name}<b>{100 * j[top][0] / j[bottom][0]:.1f}%</b></span>')
    return f'<div class="fc-ratios">{"".join(items)}<small>calculated from verified figures</small></div>' if items else ""


def show_result(r, fields, n):
    res, info = r["res"], r["res"]["info"]
    acc = info["accession"].replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{int(r['cik'])}/{acc}/{info['doc']}"
    top = (f'<div class="fc-co"><div><div class="fc-co-name">{esc(r["title"].title())}<span class="fc-tick">{esc(r["ticker"])}</span></div>'
           f'<div class="fc-co-meta">Form 10-K &middot; fiscal year ended {nice_date(info["period_end"])} &middot; filed {nice_date(info["filed"])}</div></div>'
           f'<a class="fc-link" href="{esc(url)}" target="_blank">View filing on SEC.gov &#8599;</a></div>')
    if not res["section_text"]:
        st.markdown(f'<div class="fc-panel fc-lg">{top}<div class="fc-banner bad">I couldn\'t locate the financial statements in this filing\'s text.</div></div>',
                    unsafe_allow_html=True)
        return
    judged = [(f,) + judge(f, res) for f in fields]
    n_ok = sum(1 for j in judged if j[2] == "ok")
    n_bad = sum(1 for j in judged if j[2] == "bad")
    kind = "bad" if n_bad else ("ok" if n_ok == len(judged) else "warn")
    text = (f"{n_ok} of {len(judged)} figure{'s' if len(judged) != 1 else ''} verified against the SEC's official XBRL data"
            + (f" &middot; {n_bad} did not match" if n_bad else ""))
    if res["missing"]:
        text += " &middot; couldn't find the " + " and ".join(res["missing"]) + " statement"
    cards = ""
    for field, value, status, msg, quote, quote_ok in judged:
        exact = f"${value:,.0f}" if value is not None else "&nbsp;"
        note = "" if status == "ok" else f'<div class="fc-note">{esc(msg)}</div>'
        cards += (f'<div class="fc-card"><div class="fc-label">{LABEL[field]}</div><div class="fc-value">{esc(money(value))}</div>'
                  f'<div class="fc-exact">{exact}</div><span class="fc-pill {status}">{PILL[status]}</span>{note}{trend(field, r)}</div>')
    st.markdown(f'<div class="fc-panel fc-lg">{top}<div class="fc-banner {kind}">{text}</div><div class="fc-grid">{cards}</div>{ratios(res)}</div>',
                unsafe_allow_html=True)
    with st.expander(f"Evidence for {r['title'].title()}: the exact text the model quoted"):
        rows = ""
        for field, value, status, msg, quote, quote_ok in judged:
            key = (res.get("key") or {}).get(field)
            sec = f"SEC value ${key['value']:,.0f} ({esc(key['tag'])})" if key else "no SEC value for this item"
            q_html = ""
            if quote:
                check = ('<div class="fc-qok">&#10003; This quote appears word for word in the filing.</div>' if quote_ok else
                         '<div class="fc-qbad">&#10007; This quote was NOT found in the filing text.</div>')
                q_html = f'<div class="fc-quote">{esc(quote)}</div>{check}'
            rows += f'<div class="fc-ev"><div class="fc-ev-h"><b>{LABEL[field]}</b><span>{sec}</span></div>{q_html}</div>'
        st.markdown(rows, unsafe_allow_html=True)
    with st.expander(f"How this answer was produced: step by step ({r['title'].title()})"):
        ui_pages.render_trace(r, judged, res.get("cell", "B"))


def to_csv(results):
    import csv, io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["company", "ticker", "fiscal_year_end", "field", "model_value", "sec_value", "status", "quote", "quote_found"])
    for r in results:
        if not r["res"]["section_text"]:
            continue
        for f in P.FIELDS:
            value, status, msg, quote, quote_ok = judge(f, r["res"])
            key = (r["res"].get("key") or {}).get(f)
            w.writerow([r["title"], r["ticker"], r["res"]["info"]["period_end"], f, value, key["value"] if key else "",
                        {"ok": "verified", "bad": "mismatch", "warn": "unverified"}[status], quote, quote_ok])
    return buf.getvalue()


LOGO = ('<svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="16" cy="16" r="13" opacity=".35"/><path d="M5 19c4-6 18-6 22 0" opacity=".6"/><path d="m11 15.5 3.5 3.5 7-8"/></svg>')


def nav(active):
    link = lambda href, label, key: f'<a href="{href}" target="_self" class="{"on" if key == active else ""}">{label}</a>'
    return ('<nav class="fc-nav">'
            f'<a class="fc-brand" href="/" target="_self">{LOGO}<span>FilingCheck<small>10-K figures, checked</small></span></a>'
            '<div class="fc-links fc-lg">' + link("/", "Check a filing", "home") + link("/study", "The study", "study")
            + link("/#how-it-works", "How it works", "") + f'<a href="{REPO}" target="_blank">GitHub</a></div>'
            + ('<a class="fc-cta fc-chip" href="/" target="_self">Check a filing &#8250;</a>' if active == "study" else
               '<a class="fc-cta fc-chip" href="/study" target="_self">Read the study &#8250;</a>') + '</nav>')


def footer():
    st.markdown('<div class="fc-foot"><span><b>Not financial advice.</b> Figures are extracted by an AI model and compared with SEC XBRL data. '
                'FilingCheck is a CMPT 218 project at Simon Fraser University by William Huang, Vincent Zhao and James Park.</span>'
                f'<span><a href="/study" target="_self">The study</a> &nbsp;&middot;&nbsp; <a href="{REPO}" target="_blank">GitHub</a>'
                ' &nbsp;&middot;&nbsp; <a href="https://www.sec.gov/edgar/search/" target="_blank">SEC EDGAR</a></span></div>',
                unsafe_allow_html=True)


def install_scripts(typer=False):
    code = (TYPER.replace("__PHRASES__", json.dumps(TYPED)) if typer else "") + glass.loader()
    if hasattr(st, "iframe"):  # newer Streamlit
        st.iframe(code, height=1)
    else:
        st.components.v1.html(code, height=0)


def page_home():
    s = study.load_json("results/study/summary.json", {})
    n = s.get("n_filings")
    try:
        ticks = list(study.load_json(study.FILINGS_JSON, {}).keys())[:4]
    except Exception:
        ticks = []
    chip = ('<span class="fc-chip"><span class="fc-tick-row">' + "".join(f"<i>{esc(t[:2])}</i>" for t in ticks) + '</span>'
            + (f'<span><b>{n} SEC filings</b> tested in our study</span>' if n else '<span>Verified against SEC XBRL data</span>') + '</span>')
    st.markdown(f'<div class="fc-hero">{nav("home")}<div style="height:96px"></div>{chip}'
                '<div class="fc-h1">Read the 10-K.<br>Check every number.</div>'
                '<div class="fc-sub">Ask about any U.S. public company in plain English. An AI model reads its latest annual report, '
                'and every figure is checked against what the company filed with the SEC.</div></div>',
                unsafe_allow_html=True)

    st.session_state.setdefault("q", "")
    with st.form("search", border=False):
        c1, c2 = st.columns([5, 1.25], vertical_alignment="center")
        c1.text_input("Your question", key="q", label_visibility="collapsed", placeholder=" ")
        submitted = c2.form_submit_button("Check ›", use_container_width=True)
    install_scripts(typer=True)

    st.markdown('<div class="fc-row-l">Try</div>', unsafe_allow_html=True)
    cols = st.columns([1, 3, 3, 3, 1])
    for c, ex in zip(cols[1:4], EXAMPLES):
        c.button(ex, on_click=set_question, args=(ex,), use_container_width=True)
    recent = [x for x in st.session_state.get("recent", []) if x not in EXAMPLES][:3]
    if recent:
        st.markdown('<div class="fc-row-l">Recent</div>', unsafe_allow_html=True)
        for i, (c, ex) in enumerate(zip(st.columns([1, 3, 3, 3, 1])[1:4], recent)):
            c.button(ex if len(ex) < 34 else ex[:31] + "...", key=f"recent{i}", on_click=set_question, args=(ex,), use_container_width=True)

    if (submitted or st.session_state.pop("go", False)) and st.session_state.q.strip():
        st.session_state.pop("results", None)
        lookup(st.session_state.q)

    if st.session_state.get("results"):
        results = st.session_state.results
        for i, r in enumerate(results):
            show_result(r, st.session_state.fields, i)
        mid = st.columns([2, 2, 2])[1]
        mid.download_button("Download results (CSV)", to_csv(results), "filingcheck_results.csv", "text/csv", use_container_width=True)
    else:
        finds = ui_pages.headline_findings(s)
        if finds:
            foot = f"{n} filings &middot; Claude Haiku &middot; 3 runs each" if n else "From our study"
            st.markdown('<div class="fc-found"><div class="fc-found-h"><span>What we found when we tested it</span>'
                        '<a class="fc-link" href="/study" target="_self">See the full study &#8250;</a></div><div class="fc-found-g">'
                        + "".join(f'<div class="fc-f fc-lg"><small>{esc(k)}</small><b class="{cls}">{esc(v)}</b><p>{esc(t)}</p>'
                                  f'<div class="fc-f-foot">{foot}</div></div>' for k, v, cls, t in finds)
                        + '</div></div>', unsafe_allow_html=True)

    with st.expander("Settings: model and study condition"):
        st.selectbox("Model", list(study.PRICES), index=list(study.PRICES).index(P.MODEL) if P.MODEL in study.PRICES else 0, key="model")
        st.toggle("Allow the model to answer 'not found' (condition F, the abstention variant)", key="abstain")
        st.caption("Default is the zero-shot baseline, condition B. The Study page explains every condition.")

    st.markdown('<div class="fc-sec" id="how-it-works"><div class="fc-sec-k">How it works</div>'
                '<div class="fc-sec-t">Three steps between your question and a number you can trust.</div><div class="fc-steps">'
                '<div class="fc-step fc-lg"><div class="fc-step-n">01 &middot; FIND</div><div class="fc-step-t">Find the filing</div><div class="fc-step-d">Looks up the company and downloads its latest 10-K from SEC EDGAR.</div></div>'
                '<div class="fc-step fc-lg"><div class="fc-step-n">02 &middot; READ</div><div class="fc-step-t">Read the statements</div><div class="fc-step-d">An AI model reads the financial statements and quotes the exact line it used.</div></div>'
                '<div class="fc-step fc-lg"><div class="fc-step-n">03 &middot; VERIFY</div><div class="fc-step-t">Verify the number</div><div class="fc-step-d">Each figure is compared with the company\'s official XBRL data, so mistakes are flagged.</div></div>'
                '</div></div>', unsafe_allow_html=True)


def page_study():
    st.markdown(nav("study"), unsafe_allow_html=True)
    install_scripts()
    ui_pages.render_study_page()


st.set_page_config(page_title="FilingCheck", page_icon="🧾", layout="centered")
st.markdown(CSS, unsafe_allow_html=True)
st.markdown(ui_pages.EXTRA_CSS, unsafe_allow_html=True)
pg = st.navigation([st.Page(page_home, title="FilingCheck", default=True),
                    st.Page(page_study, title="The study · FilingCheck", url_path="study")], position="hidden")
pg.run()
footer()

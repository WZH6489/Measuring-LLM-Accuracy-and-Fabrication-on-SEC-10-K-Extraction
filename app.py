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
          [("The prompt (conditions are listed on the Method tab)", prompt, f"{ticker}_prompt.txt"), ("The model's raw reply", raw, f"{ticker}_reply.txt")])
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


@st.cache_data(show_spinner=False)
def finance_bg():
    """Draw a dusk 'financial landscape': layered stock-chart ridges and revenue bars."""
    import base64, random
    rnd = random.Random(4)
    W, H = 1600, 900
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid slice"><defs>'
         '<linearGradient id="s" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#070b1f"/><stop offset=".34" stop-color="#171d4d"/>'
         '<stop offset=".58" stop-color="#4b3f88"/><stop offset=".76" stop-color="#b8768b"/><stop offset="1" stop-color="#f2b28a"/></linearGradient>'
         '<linearGradient id="m" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#c58aa0" stop-opacity="0"/>'
         '<stop offset="1" stop-color="#c58aa0" stop-opacity=".30"/></linearGradient></defs>'
         f'<rect width="{W}" height="{H}" fill="url(#s)"/>']
    for y in range(90, H, 90):
        o.append(f'<line x1="0" y1="{y}" x2="{W}" y2="{y}" stroke="#fff" stroke-opacity=".05"/>')
    for x in range(0, W, 26):  # far layer: a bar chart that grows to the right
        h = 60 + 170 * (x / W) ** 1.4 + rnd.uniform(-22, 22)
        o.append(f'<rect x="{x}" y="{560 - h:.0f}" width="14" height="{h + 400:.0f}" fill="#a99ad6" fill-opacity=".30"/>')
    o.append(f'<rect y="430" width="{W}" height="200" fill="url(#m)"/>')
    fills = ["#6f66ad", "#494594", "#2c2f72", "#181d4e", "#0b0f2c"]
    for i, c in enumerate(fills):  # nearer layers: price lines drawn as filled area charts
        base, v, pts = 600 + i * 62, 0.0, []
        for x in range(0, W + 23, 22):
            v += rnd.gauss(1.5 - i * .25, 9 + i * 2.5)
            pts.append((x, base - v))
        line = " ".join(f"{x},{y:.0f}" for x, y in pts)
        o.append(f'<polygon points="0,{H} {line} {W},{H}" fill="{c}"/>')
        stroke, op = ("#9BE8BE", .75) if i >= 3 else ("#ffd9c2", .45)
        o.append(f'<polyline points="{line}" fill="none" stroke="{stroke}" stroke-opacity="{op}" stroke-width="{1.4 + i * .25:.1f}"/>')
        if i == 3:
            o += [f'<circle cx="{x}" cy="{y:.0f}" r="3.2" fill="#9BE8BE"/>' for x, y in pts[4::9]]
        if i < len(fills) - 1:
            o.append(f'<rect y="{base - 20}" width="{W}" height="150" fill="url(#m)"/>')
    o.append("</svg>")
    return base64.b64encode("".join(o).encode()).decode()


def study_stats():
    """Headline numbers from the latest pipeline run, if results exist."""
    try:
        rows = [r for r in json.load(open("results/results.json")) if r.get("key") is not None]
    except Exception:
        rows = []
    stats = []
    for col, label in (("llm_class", "of figures the AI model got right in our test"),
                       ("regex_class", "for a simple rule-based baseline")):
        used = [r for r in rows if r.get(col) not in (None, "not_run", "no_section")]
        if used:
            stats.append((f"{100 * sum(r[col] == 'match' for r in used) / len(used):.0f}%", label))
    if rows:
        stats.append((str(len({r["ticker"] for r in rows})), "companies in the current test set"))
    return stats or [("10-K", "annual reports, straight from SEC EDGAR"), ("XBRL", "official data used as the answer key"),
                     ("4", "key figures checked per filing")]


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
@import url('https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Manrope:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');
html, body, .stApp, .stMarkdown, input, button, p, div { font-family: 'Manrope', -apple-system, 'Segoe UI', sans-serif; }
.stApp { background: linear-gradient(180deg, rgba(6,9,26,.38) 0%, rgba(6,9,26,.08) 46%, rgba(6,9,26,.50) 100%), url("data:image/svg+xml;base64,__BG__") center / cover fixed no-repeat #0b0f2c; color:#fff; }
.stApp::before { content:""; position:fixed; inset:-20%; z-index:0; pointer-events:none; background: radial-gradient(38% 30% at 22% 30%, rgba(155,232,190,.16), transparent 70%), radial-gradient(34% 28% at 78% 22%, rgba(242,178,138,.20), transparent 70%), radial-gradient(40% 30% at 60% 80%, rgba(120,110,230,.20), transparent 70%); animation: fcDrift 22s ease-in-out infinite alternate; }
@keyframes fcDrift { from { transform: translate3d(-3%,-2%,0) scale(1); } to { transform: translate3d(4%,3%,0) scale(1.08); } }
@keyframes fcUp { from { opacity:0; transform: translateY(16px); } to { opacity:1; transform:none; } }
@keyframes fcPulse { 0%,100% { box-shadow:0 0 0 0 rgba(155,232,190,.7); } 50% { box-shadow:0 0 0 6px rgba(155,232,190,0); } }
@keyframes fcGrow { from { transform: scaleY(0); } to { transform: scaleY(1); } }
[data-testid="stAppViewContainer"], .block-container { position:relative; z-index:1; background:transparent; }
header[data-testid="stHeader"], [data-testid="stToolbar"], #MainMenu, footer { display:none !important; }
.block-container { max-width: 1060px; padding-top: 1.6rem; padding-bottom: 3rem; }
[data-testid="stElementContainer"]:has(iframe) { position:absolute; height:0; overflow:hidden; }
[data-testid="InputInstructions"] { display:none !important; }
.fc-glass, .fc-panel, .fc-card, [data-testid="stTextInputRootElement"], .stButton button, .stDownloadButton button, [data-testid="stExpander"] details {
  background: linear-gradient(135deg, rgba(255,255,255,.24) 0%, rgba(255,255,255,.07) 45%, rgba(255,255,255,.13) 100%) !important;
  border: 1px solid rgba(255,255,255,.30) !important;
  backdrop-filter: blur(26px) saturate(190%); -webkit-backdrop-filter: blur(26px) saturate(190%);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.60), inset 1px 0 0 rgba(255,255,255,.18), inset 0 -1px 0 rgba(255,255,255,.10), 0 12px 40px rgba(5,8,28,.30); }
.fc-nav { display:flex; align-items:center; justify-content:space-between; gap:12px; animation: fcUp .7s ease both; }
.fc-nav a, .fc-link { text-decoration:none !important; }
.fc-brand { display:flex; align-items:center; gap:9px; font-family:'Instrument Serif',serif; font-style:italic; font-size:1.5rem; color:#fff; }
.fc-mark { width:26px; height:26px; border-radius:50%; border:1.5px solid #fff; display:flex; align-items:center; justify-content:center; font-family:'Manrope'; font-style:normal; font-size:.8rem; }
.fc-links { display:flex; gap:4px; border-radius:999px; padding:5px; }
.fc-links a { color:#fff !important; font-size:.84rem; font-weight:500; padding:7px 16px; border-radius:999px; opacity:.92; transition: background .2s; }
.fc-links a:hover { background:rgba(255,255,255,.18); opacity:1; }
.fc-cta { background:#fff; color:#12163a !important; font-size:.84rem; font-weight:600; padding:9px 18px; border-radius:999px; white-space:nowrap; box-shadow:0 8px 24px rgba(5,8,28,.25); }
.fc-hero { text-align:center; padding:70px 0 8px; }
.fc-chip { display:inline-block; border-radius:999px; padding:7px 16px; font-size:.7rem; font-weight:600; letter-spacing:.14em; text-transform:uppercase; animation: fcUp .7s .08s ease both; }
.fc-chip i { display:inline-block; width:6px; height:6px; border-radius:50%; background:#9BE8BE; margin-right:8px; vertical-align:middle; animation: fcPulse 2s infinite; }
.fc-h1 { font-size:clamp(2.4rem,5.6vw,4.1rem); line-height:1.06; font-weight:500; letter-spacing:-0.03em; margin:26px 0 18px; color:#fff; text-shadow:0 2px 30px rgba(0,0,0,.30); animation: fcUp .8s .16s ease both; }
.fc-h1 em { font-family:'Instrument Serif',serif; font-weight:400; letter-spacing:-0.01em; }
.fc-sub { max-width:570px; margin:0 auto 30px; font-size:1.02rem; line-height:1.6; color:rgba(255,255,255,.90); animation: fcUp .8s .24s ease both; }
[data-testid="stForm"] { border:none !important; padding:0 !important; max-width:700px; margin:0 auto; animation: fcUp .8s .32s ease both; }
[data-testid="stTextInputRootElement"] { border-radius:999px !important; overflow:hidden; transition: box-shadow .25s, border-color .25s; }
[data-testid="stTextInputRootElement"]:focus-within { border-color:#fff !important; box-shadow: inset 0 1px 0 rgba(255,255,255,.6), 0 0 0 5px rgba(255,255,255,.16), 0 12px 40px rgba(5,8,28,.30); }
.stTextInput [data-baseweb="base-input"] { background:transparent !important; }
.stTextInput input { background:transparent !important; border:none !important; color:#fff !important; -webkit-text-fill-color:#fff; caret-color:#fff; padding:.9rem 1.4rem !important; font-size:1.02rem; }
.stTextInput input::placeholder { color:rgba(255,255,255,.78); -webkit-text-fill-color:rgba(255,255,255,.78); }
[data-testid="stFormSubmitButton"] button { background:#fff !important; color:#12163a !important; border:none !important; border-radius:999px !important; font-weight:600; padding:.85rem 1rem; box-shadow:0 8px 24px rgba(5,8,28,.28); transition: transform .15s; }
[data-testid="stFormSubmitButton"] button:hover { transform: translateY(-1px); background:#F1EEFF !important; }
[data-testid="stFormSubmitButton"] button p { color:#12163a !important; font-weight:600; }
.stButton button, .stDownloadButton button { color:#fff !important; border-radius:999px !important; padding:.38rem .95rem; transition: transform .15s, border-color .2s; }
.stButton button p, .stDownloadButton button p { font-size:.84rem; color:#fff !important; }
.stButton button:hover, .stDownloadButton button:hover { transform: translateY(-2px); border-color:#fff !important; }
.fc-row-l { text-align:center; font-size:.68rem; letter-spacing:.14em; text-transform:uppercase; color:rgba(255,255,255,.7); margin:8px 0 0; }
.fc-stats { display:flex; justify-content:center; flex-wrap:wrap; margin:60px auto 0; max-width:880px; border-radius:24px; padding:20px 8px; animation: fcUp .8s .45s ease both; }
.fc-stat { flex:1 1 200px; padding:2px 26px; border-left:1px solid rgba(255,255,255,.28); text-align:left; }
.fc-stat:first-child { border-left:none; }
.fc-stat b { display:block; font-size:1.9rem; font-weight:500; letter-spacing:-0.02em; }
.fc-stat span { font-size:.8rem; color:rgba(255,255,255,.85); line-height:1.4; display:block; margin-top:2px; }
.fc-panel { border-radius:28px; padding:26px 26px 22px; margin-top:30px; animation: fcUp .6s ease both; }
.fc-co { display:flex; justify-content:space-between; align-items:flex-end; gap:16px; flex-wrap:wrap; margin-bottom:16px; }
.fc-co-name { font-family:'Instrument Serif',serif; font-size:2rem; line-height:1.1; }
.fc-tick { font-family:'JetBrains Mono',monospace; font-size:.74rem; background:#fff; color:#12163a; border-radius:999px; padding:3px 10px; margin-left:10px; vertical-align:middle; font-weight:500; }
.fc-co-meta { color:rgba(255,255,255,.8); font-size:.86rem; margin-top:6px; }
.fc-link { font-size:.85rem; font-weight:600; color:#fff !important; border-bottom:1px solid rgba(255,255,255,.5); white-space:nowrap; }
.fc-banner { border-radius:14px; padding:10px 14px; font-size:.9rem; font-weight:600; margin-bottom:14px; }
.fc-banner.ok { background:rgba(155,232,190,.18); color:#C6F7DC; border:1px solid rgba(155,232,190,.40); }
.fc-banner.bad { background:rgba(255,138,128,.20); color:#FFD0CB; border:1px solid rgba(255,138,128,.45); }
.fc-banner.warn { background:rgba(255,214,120,.16); color:#FFE9B8; border:1px solid rgba(255,214,120,.40); }
.fc-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(210px,1fr)); gap:12px; }
.fc-card { border-radius:20px; padding:18px; transition: transform .2s ease; animation: fcUp .6s ease both; }
.fc-card:nth-child(2) { animation-delay:.08s; } .fc-card:nth-child(3) { animation-delay:.16s; } .fc-card:nth-child(4) { animation-delay:.24s; }
.fc-card:hover { transform: translateY(-4px); }
.fc-label { font-size:.7rem; text-transform:uppercase; letter-spacing:.12em; color:rgba(255,255,255,.78); font-weight:600; }
.fc-value { font-size:2.1rem; font-weight:500; letter-spacing:-0.02em; margin:8px 0 2px; font-variant-numeric:tabular-nums; }
.fc-exact { font-family:'JetBrains Mono',monospace; font-size:.74rem; color:rgba(255,255,255,.65); margin-bottom:12px; }
.fc-pill { display:inline-block; font-size:.74rem; font-weight:600; border-radius:999px; padding:4px 11px; }
.fc-pill.ok { background:#9BE8BE; color:#0E2A1C; } .fc-pill.bad { background:#FF8A80; color:#3A0B07; } .fc-pill.warn { background:#FFD678; color:#3A2A00; }
.fc-note { font-size:.8rem; color:rgba(255,255,255,.85); margin-top:9px; line-height:1.4; }
.fc-yoy { font-size:.8rem; font-weight:600; margin-top:12px; } .fc-yoy span { font-weight:400; color:rgba(255,255,255,.7); }
.fc-yoy.up { color:#9BE8BE; } .fc-yoy.down { color:#FFB0A8; }
.fc-spark { width:100%; max-width:240px; height:38px; margin-top:8px; display:block; } .fc-spark rect { transform-origin: 50% 100%; transform-box: fill-box; animation: fcGrow .7s .3s ease both; }
.fc-spark-l { max-width:240px; display:flex; justify-content:space-between; font-family:'JetBrains Mono',monospace; font-size:.64rem; color:rgba(255,255,255,.6); margin-top:3px; }
.fc-ratios { display:flex; flex-wrap:wrap; gap:8px; margin-top:14px; align-items:center; }
.fc-ratio { border-radius:999px; padding:6px 14px; font-size:.82rem; background:rgba(255,255,255,.12); border:1px solid rgba(255,255,255,.24); }
.fc-ratio b { font-weight:600; margin-left:6px; } .fc-ratios small { color:rgba(255,255,255,.65); font-size:.74rem; }
[data-testid="stExpander"] { margin-top:12px; } [data-testid="stExpander"] details { border-radius:20px !important; }
[data-testid="stExpander"] summary, [data-testid="stExpander"] summary p { color:#fff !important; font-weight:600; }
.fc-ev { padding:13px 0; border-top:1px solid rgba(255,255,255,.16); } .fc-ev:first-child { border-top:none; padding-top:2px; }
.fc-ev-h { display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap; font-size:.9rem; }
.fc-ev-h span { font-family:'JetBrains Mono',monospace; font-size:.76rem; color:rgba(255,255,255,.75); }
.fc-quote { font-family:'JetBrains Mono',monospace; font-size:.78rem; background:rgba(8,11,34,.35); border-left:2px solid #9BE8BE; border-radius:8px; padding:9px 12px; margin:8px 0 6px; white-space:pre-wrap; word-break:break-word; color:#fff; }
.fc-qok { font-size:.78rem; color:#C6F7DC; } .fc-qbad { font-size:.78rem; color:#FFD0CB; font-weight:600; }
.fc-steps { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; margin-top:64px; }
.fc-step { border-radius:22px; padding:20px 22px; transition: transform .2s; } .fc-step:hover { transform: translateY(-4px); }
.fc-step-n { font-family:'Instrument Serif',serif; font-style:italic; font-size:1.5rem; color:#9BE8BE; }
.fc-step-t { font-weight:600; margin:4px 0 6px; } .fc-step-d { font-size:.86rem; color:rgba(255,255,255,.86); line-height:1.5; }
.fc-foot { margin-top:48px; text-align:center; font-size:.76rem; color:rgba(255,255,255,.7); line-height:1.5; }
[data-testid="stSpinner"] { justify-content:center; } [data-testid="stSpinner"] p { color:#fff !important; }
@media (prefers-reduced-motion: reduce) { *, .stApp::before { animation: none !important; transition: none !important; } }
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
            fill = "#FF8A80" if v < 0 else ("#9BE8BE" if i == len(hist) - 1 else "rgba(255,255,255,.42)")
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
        st.markdown(f'<div class="fc-panel">{top}<div class="fc-banner bad">I couldn\'t locate the financial statements in this filing\'s text.</div></div>',
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
    st.markdown(f'<div class="fc-panel">{top}<div class="fc-banner {kind}">{text}</div><div class="fc-grid">{cards}</div>{ratios(res)}</div>',
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


st.set_page_config(page_title="FilingCheck", page_icon="📄", layout="centered")
st.markdown(CSS.replace("__BG__", finance_bg()), unsafe_allow_html=True)
st.markdown('<div class="fc-nav"><div class="fc-brand"><div class="fc-mark">&#10003;</div>filingcheck</div>'
            '<div class="fc-links fc-glass"><a href="#how-it-works">How it works</a>'
            '<a href="https://www.sec.gov/edgar/search/" target="_blank">SEC EDGAR</a>'
            f'<a href="{REPO}" target="_blank">Method</a></div>'
            f'<a class="fc-cta" href="{REPO}" target="_blank">View on GitHub</a></div>'
            '<div class="fc-hero"><span class="fc-chip fc-glass"><i></i>Verified financial data</span>'
            '<div class="fc-h1">Ask a 10-K anything.<br><em>Trust the number.</em></div>'
            '<div class="fc-sub">An AI model reads a company\'s latest annual report and answers in plain English. '
            'Every figure is checked against the data the company filed with the SEC, so you know when to believe it.</div></div>',
            unsafe_allow_html=True)

st.markdown(ui_pages.EXTRA_CSS, unsafe_allow_html=True)
t_lookup, t_method, t_study = st.tabs(["Lookup", "Method", "Study results"])
with t_lookup:
    st.session_state.setdefault("q", "")
    with st.form("search", border=False):
        c1, c2 = st.columns([5, 1], vertical_alignment="bottom")
        q = c1.text_input("Your question", key="q", label_visibility="collapsed", placeholder=" ")
        submitted = c2.form_submit_button("Check", use_container_width=True)
    typer = TYPER.replace("__PHRASES__", json.dumps(TYPED))
    if hasattr(st, "iframe"):  # newer Streamlit
        st.iframe(typer, height=1)
    else:
        st.components.v1.html(typer, height=0)

    cols = st.columns([1, 3, 3, 3, 1])
    for c, ex in zip(cols[1:4], EXAMPLES):
        c.button(ex, on_click=set_question, args=(ex,), use_container_width=True)
    recent = [x for x in st.session_state.get("recent", []) if x not in EXAMPLES][:3]
    if recent:
        st.markdown('<div class="fc-row-l">Recent</div>', unsafe_allow_html=True)
        for i, (c, ex) in enumerate(zip(st.columns([1, 3, 3, 3, 1])[1:4], recent)):
            c.button(ex if len(ex) < 34 else ex[:31] + "...", key=f"recent{i}", on_click=set_question, args=(ex,), use_container_width=True)

    with st.expander("Settings: model and conditions"):
        st.selectbox("Model", list(study.PRICES), index=list(study.PRICES).index(P.MODEL) if P.MODEL in study.PRICES else 0, key="model")
        st.toggle("Allow the model to answer 'not found' (condition F, the abstention variant)", key="abstain")
        st.caption("Default is the zero-shot baseline, condition B. The Method tab explains every condition.")

    if (submitted or st.session_state.pop("go", False)) and st.session_state.q.strip():
        st.session_state.pop("results", None)
        lookup(st.session_state.q)

    if st.session_state.get("results"):
        results = st.session_state.results
        for n, r in enumerate(results):
            show_result(r, st.session_state.fields, n)
        mid = st.columns([2, 2, 2])[1]
        mid.download_button("Download results (CSV)", to_csv(results), "filingcheck_results.csv", "text/csv", use_container_width=True)
    else:
        st.markdown('<div class="fc-stats fc-glass">' + "".join(f'<div class="fc-stat"><b>{esc(n)}</b><span>{esc(t)}</span></div>'
                                                               for n, t in study_stats()) + '</div>', unsafe_allow_html=True)


with t_method:
    ui_pages.render_method()
with t_study:
    ui_pages.render_study()

st.markdown('<div class="fc-steps" id="how-it-works">'
            '<div class="fc-step fc-glass"><div class="fc-step-n">01</div><div class="fc-step-t">Find the filing</div><div class="fc-step-d">Looks up the company and downloads its latest 10-K from SEC EDGAR.</div></div>'
            '<div class="fc-step fc-glass"><div class="fc-step-n">02</div><div class="fc-step-t">Read the statements</div><div class="fc-step-d">An AI model reads the financial statements and quotes the exact line it used.</div></div>'
            '<div class="fc-step fc-glass"><div class="fc-step-n">03</div><div class="fc-step-t">Verify the number</div><div class="fc-step-d">Each figure is compared with the company\'s official XBRL data, so mistakes are flagged.</div></div>'
            '</div><div class="fc-foot">FilingCheck is a CMPT 218 course project at Simon Fraser University. '
            'Figures are extracted by an AI model and compared with SEC XBRL data. It is not financial advice.</div>',
            unsafe_allow_html=True)

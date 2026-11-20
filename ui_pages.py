"""The transparency pages of the FilingCheck site: step-by-step trace, Method, and Study results.

Everything shown here is read from the real code and the saved study files, so it cannot drift
out of date: the prompts come from study.py, the tags from pipeline.py, the numbers from results/study/.
"""
import csv
import html
import inspect
import json
import os

import streamlit as st

import pipeline as P
import study

esc = html.escape
FIELD_LABEL = {"revenue": "Revenue", "net_income": "Net income",
               "operating_income": "Operating income", "total_assets": "Total assets"}
CLASS_COLOR = {"match": "#9BE8BE", "wrong_scale": "#FFD678", "wrong_value": "#FFA766", "not_in_text": "#FF8A80",
               "abstained": "#B8B8D0", "missing": "#8E8EA8", "bad_json": "#C79BFF"}

EXTRA_CSS = """<style>
[role="tablist"] { gap:6px; background:rgba(255,255,255,.10) !important; border:1px solid rgba(255,255,255,.24) !important; border-radius:999px; padding:5px !important; width:fit-content; margin:30px auto 8px; backdrop-filter:blur(22px) saturate(180%); -webkit-backdrop-filter:blur(22px) saturate(180%); box-shadow: inset 0 1px 0 rgba(255,255,255,.45), 0 10px 30px rgba(5,8,28,.25); }
[data-testid="stTab"] { border-radius:999px !important; padding:8px 22px !important; background:transparent !important; height:auto !important; border:none !important; }
[data-testid="stTab"] p { color:rgba(255,255,255,.85) !important; font-weight:600; font-size:.9rem; }
[data-testid="stTab"][aria-selected="true"] { background:rgba(255,255,255,.26) !important; box-shadow: inset 0 1px 0 rgba(255,255,255,.5); }
[data-testid="stTab"][aria-selected="true"] p { color:#fff !important; }
.react-aria-SelectionIndicator, [data-baseweb="tab-highlight"], [data-baseweb="tab-border"] { display:none !important; }
[data-orientation="horizontal"][data-rac]:has(> [role="tablist"]) { border-bottom:none !important; }
[data-testid="stCode"], [data-testid="stCode"] pre { background:rgba(6,9,26,.55) !important; border-radius:14px !important; border:1px solid rgba(255,255,255,.14); }
[data-testid="stCode"] code { font-family:'JetBrains Mono',monospace !important; font-size:.76rem !important; }
.stRadio label, .stRadio p, .stToggle label, .stToggle p, .stCheckbox label, .stCheckbox p, .stSelectbox label, .stSelectbox p { color:#fff !important; }
.fc-h2 { font-family:'Instrument Serif',serif; font-size:1.9rem; margin:34px 0 4px; }
.fc-lead { color:rgba(255,255,255,.88); font-size:.98rem; line-height:1.6; max-width:760px; margin-bottom:14px; }
.fc-flow { display:flex; align-items:stretch; gap:8px; flex-wrap:wrap; margin:14px 0 6px; }
.fc-flow i { align-self:center; font-style:normal; color:rgba(255,255,255,.6); }
.fc-fl { flex:1 1 150px; border-radius:18px; padding:14px 16px; background:linear-gradient(135deg,rgba(255,255,255,.22),rgba(255,255,255,.07)); border:1px solid rgba(255,255,255,.28); backdrop-filter:blur(22px); -webkit-backdrop-filter:blur(22px); box-shadow:inset 0 1px 0 rgba(255,255,255,.5); }
.fc-fl b { display:block; font-size:.88rem; margin-bottom:3px; } .fc-fl span { font-size:.78rem; color:rgba(255,255,255,.8); line-height:1.4; display:block; }
.fc-tbl { width:100%; border-collapse:separate; border-spacing:0; font-size:.85rem; border-radius:18px; overflow:hidden; background:rgba(12,22,19,.40); border:1px solid rgba(255,255,255,.2); backdrop-filter:blur(18px); -webkit-backdrop-filter:blur(18px); }
.fc-tbl th { text-align:left; font-size:.68rem; letter-spacing:.1em; text-transform:uppercase; color:rgba(255,255,255,.7); padding:10px 12px; background:rgba(255,255,255,.08); font-weight:600; }
.fc-tbl td { padding:10px 12px; border-top:1px solid rgba(255,255,255,.1); vertical-align:top; color:rgba(255,255,255,.94); }
.fc-tbl td.m { font-family:'JetBrains Mono',monospace; font-size:.76rem; } .fc-tbl td.c { white-space:nowrap; }
.fc-tbl .tag { display:inline-block; font-family:'JetBrains Mono',monospace; font-size:.74rem; background:rgba(255,255,255,.12); border-radius:6px; padding:1px 7px; margin:1px 3px 1px 0; }
.fc-bar { position:relative; height:22px; min-width:150px; background:rgba(255,255,255,.10); border-radius:999px; overflow:hidden; }
.fc-bar i { position:absolute; left:0; top:0; bottom:0; background:linear-gradient(90deg,#6FD3A5,#9BE8BE); border-radius:999px; opacity:.85; }
.fc-bar.a i { background:linear-gradient(90deg,#9AA0B5,#C5C9D8); }
.fc-bar u { position:absolute; top:9px; height:4px; background:rgba(255,255,255,.95); border-radius:2px; opacity:.9; }
.fc-bar span { position:absolute; left:10px; top:2px; font-size:.74rem; font-weight:600; color:#0E2A1C; mix-blend-mode:normal; text-shadow:0 0 6px rgba(255,255,255,.35); }
.fc-stack { display:flex; height:22px; min-width:220px; border-radius:999px; overflow:hidden; background:rgba(255,255,255,.08); } .fc-stack i { display:block; height:100%; }
.fc-legend { display:flex; flex-wrap:wrap; gap:6px 14px; font-size:.76rem; color:rgba(255,255,255,.85); margin:8px 0 4px; } .fc-legend b { display:inline-block; width:10px; height:10px; border-radius:3px; margin-right:6px; vertical-align:middle; }
.fc-kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; margin:14px 0; }
.fc-kpi { border-radius:20px; padding:16px 18px; background:linear-gradient(135deg,rgba(255,255,255,.24),rgba(255,255,255,.07)); border:1px solid rgba(255,255,255,.28); backdrop-filter:blur(22px); -webkit-backdrop-filter:blur(22px); box-shadow:inset 0 1px 0 rgba(255,255,255,.5); }
.fc-kpi b { display:block; font-size:1.7rem; font-weight:500; letter-spacing:-.02em; } .fc-kpi span { font-size:.78rem; color:rgba(255,255,255,.82); }
.fc-tl { display:flex; gap:14px; padding:14px 0; border-top:1px solid rgba(255,255,255,.14); } .fc-tl:first-child { border-top:none; }
.fc-tl-n { flex:0 0 28px; height:28px; border-radius:50%; background:#fff; color:#12163a; font-weight:700; font-size:.8rem; display:flex; align-items:center; justify-content:center; }
.fc-tl-b { flex:1; min-width:0; } .fc-tl-h { display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap; align-items:center; font-size:.95rem; }
.fc-tl-d { font-size:.84rem; color:rgba(255,255,255,.88); line-height:1.55; margin-top:6px; }
.fc-tl-d .tag { font-family:'JetBrains Mono',monospace; font-size:.74rem; background:rgba(255,255,255,.12); border-radius:6px; padding:1px 7px; }
.fc-note2 { font-size:.8rem; color:rgba(255,255,255,.75); line-height:1.5; margin-top:8px; }
.fc-empty { border-radius:24px; padding:24px 26px; background:linear-gradient(135deg,rgba(255,255,255,.2),rgba(255,255,255,.06)); border:1px solid rgba(255,255,255,.26); backdrop-filter:blur(22px); -webkit-backdrop-filter:blur(22px); }
</style>"""


def money(v):
    if v is None:
        return "none"
    a = abs(v)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if a >= div:
            return f"{'-' if v < 0 else ''}${a / div:,.2f}{suf}"
    return f"${v:,.0f}"


def pill(kind, text):
    return f'<span class="fc-pill {kind}">{text}</span>'


# ------------------------------------------------------------------ the trace

def render_trace(r, judged, cell):
    """Colab-style cell-by-cell record of how one answer was produced."""
    res = r["res"]
    steps = [r["resolve"]] + list(res.get("trace", []))
    key = res.get("key") or {}
    rows = ""
    for field, value, status, msg, quote, quote_ok in judged:
        k = key.get(field)
        cls = P.classify(value, k["value"] if k else None, res["section_text"]) if res.get("parsed") is not None else "bad_json"
        qtxt = "-" if quote_ok is None else ("found" if quote_ok else "NOT found")
        rows += (f'<tr><td>{FIELD_LABEL[field]}</td><td class="m">{esc(money(value))}</td><td class="m">{esc(money(k["value"] if k else None))}</td>'
                 f'<td class="c">{pill({"ok": "ok", "warn": "warn", "bad": "bad"}[status], esc(cls))}</td><td>{qtxt}</td></tr>')
    steps.append({"title": "Compare each figure with the answer key and score it", "status": "ok", "seconds": 0,
                  "html": ('A figure counts as a <b>match</b> when it is within 1% of the SEC value. Anything else is classified by type of error '
                           '(the full list of types is on the Method tab).'
                           '<table class="fc-tbl" style="margin-top:10px"><tr><th>Field</th><th>Model said</th><th>SEC says</th><th>Result</th>'
                           f'<th>Quote in filing?</th></tr>{rows}</table>')})
    st.markdown(f'<div class="fc-note2">Condition <b>{esc(cell)}</b> &middot; {esc(res.get("model", ""))} &middot; every step below was run live for this answer.</div>',
                unsafe_allow_html=True)
    for n, s in enumerate(steps, 1):
        secs = f" &middot; {s['seconds']:.1f}s" if s.get("seconds") else ""
        st.markdown(f'<div class="fc-tl"><div class="fc-tl-n">{n}</div><div class="fc-tl-b"><div class="fc-tl-h"><b>{esc(s["title"])}</b>'
                    f'{pill(s.get("status", "ok"), "Done" + secs)}</div><div class="fc-tl-d">{s["html"]}</div></div></div>',
                    unsafe_allow_html=True)
        for label, text, name in s.get("code", []):
            st.caption(label)
            shown = text if len(text) <= 2600 else (text[:1700] + f"\n\n   ... [{len(text) - 2300:,} more characters omitted from this view] ...\n\n" + text[-600:])
            st.code(shown, language="text")
            if len(text) > 2600:
                st.download_button(f"Download the full text ({len(text):,} characters)", text, name, key=f"dl_{r['ticker']}_{name}")


# --------------------------------------------------------------------- method

def _demo_prompt(cell):
    ctx = {"section_text": "<< the income statement and the balance sheet, as plain text from the filing, go here >>",
           "chunks_text": "<< the passages found by the keyword search go here >>",
           "company": "<company name>", "period_end": "<fiscal year end date>"}
    ex = {"revenue": {"value": 1000000000, "unit": "USD", "period": "fiscal year ended ...", "quote": "Total revenue 1,000"}}
    shots = [{"text": "<< statements from a different company's filing >>", "answer": ex},
             {"text": "<< statements from a second company's filing >>", "answer": ex}]
    return study.build_prompt(cell, ctx, shots)


def _source(obj):
    try:
        return inspect.getsource(obj)
    except (OSError, TypeError):
        return "(source not available)"


def render_method():
    man = study.load_json(study.MANIFEST_JSON, {})
    st.markdown('<div class="fc-h2">How FilingCheck works, and how we check it</div>'
                '<div class="fc-lead">The study asks: when a language model reads a 10-K, how often does it get the key numbers right, '
                'and when it is wrong, is the number misread or made up? Every number the model gives is compared with the figure the '
                'company itself filed with the SEC in machine-readable form, so no one has to judge answers by hand.</div>'
                '<div class="fc-flow">'
                '<div class="fc-fl"><b>1 &middot; SEC EDGAR</b><span>download the latest 10-K</span></div><i>&rarr;</i>'
                '<div class="fc-fl"><b>2 &middot; Statement finder</b><span>locate the income statement and balance sheet</span></div><i>&rarr;</i>'
                '<div class="fc-fl"><b>3 &middot; The model</b><span>extract four numbers, each with a quote</span></div><i>&rarr;</i>'
                '<div class="fc-fl"><b>4 &middot; Verifier</b><span>look up the SEC XBRL answer key</span></div><i>&rarr;</i>'
                '<div class="fc-fl"><b>5 &middot; Scoring</b><span>match, or what kind of error</span></div></div>',
                unsafe_allow_html=True)

    st.markdown('<div class="fc-h2">What we verify with</div>'
                '<div class="fc-lead">Since 2009 companies must tag the numbers in their filings with XBRL, a standard label for each figure. The SEC publishes those '
                'tagged values through its <b>companyfacts</b> API. We use them as the answer key. Different companies label the same idea differently, '
                'so for each field we try several tags in order and take the first that has a value for this exact filing and fiscal year.</div>',
                unsafe_allow_html=True)
    trs = "".join(f"<tr><td>{FIELD_LABEL[f]}</td><td>{''.join(f'<span class=tag>{esc(t)}</span>' for t in P.TAGS[f])}</td>"
                  f"<td>{'a full year (340-380 days)' if f in P.FLOW_FIELDS else 'a single date (year end)'}</td></tr>" for f in P.FIELDS)
    st.markdown(f'<table class="fc-tbl"><tr><th>Field</th><th>XBRL tags tried, in order</th><th>Covers</th></tr>{trs}</table>', unsafe_allow_html=True)
    st.markdown('<div class="fc-note2"><b>Rules for the answer key</b><br>' + "<br>".join("&bull; " + esc(x) for x in study.KEY_RULES) +
                '<br><b>Known weakness:</b> the answer key can itself be wrong or ambiguous (for example a company reporting both consolidated and '
                'attributable net income). We flag items where tags disagree, and the Study results tab shows how often a hand check found the key wrong.</div>',
                unsafe_allow_html=True)

    st.markdown('<div class="fc-h2">The experimental conditions</div>'
                '<div class="fc-lead">Every condition is scored against the same key. They differ only in what the model is shown and told.</div>',
                unsafe_allow_html=True)
    rq = {"A": "Floor: what simple rules achieve without any model.", "B": "Baseline for the research question on prompting strategies.",
          "C": "Does showing worked examples help?", "D": "Does searching a long filing for passages work as well as handing over the statements?",
          "E": "Separates memory from reading: any correct number came from training data, and a wrong one is unsupported by any document.",
          "F": "Does allowing 'not found' reduce made-up numbers?"}
    sees = {"A": "The statements, read by fixed label-matching rules", "B": "The income statement and balance sheet text only",
            "C": "The same, plus two worked examples from other companies' filings", "D": "Only the passages a BM25 keyword search retrieves",
            "E": "Nothing: only the company name and fiscal year", "F": "Same as the condition it is added to, plus 'answer null if unsure'"}
    trs = "".join(f"<tr><td class='c'><b>{k}</b></td><td>{esc(sees[k])}</td><td>{esc(rq[k])}</td></tr>" for k in "ABCDEF")
    st.markdown(f'<table class="fc-tbl"><tr><th>Cond.</th><th>What the model sees</th><th>Why it is in the study</th></tr>{trs}</table>'
                f'<div class="fc-note2">Retrieval details: {esc(study.RETRIEVAL["method"])}; passages of about {study.RETRIEVAL["chunk_chars"]} characters, '
                f'top {study.RETRIEVAL["top_k_per_field"]} per field. Few-shot examples come from companies <i>outside</i> the test sample: '
                f'{", ".join(study.HELD_OUT)} (the first two that download cleanly).</div>', unsafe_allow_html=True)
    with st.expander("See the exact prompt for each condition"):
        for cell in study.ALL_CELLS:
            st.caption(f"Condition {cell}: {study.CELLS[cell]['label']}")
            st.code(_demo_prompt(cell), language="text")

    st.markdown('<div class="fc-h2">How each answer is scored</div>', unsafe_allow_html=True)
    trs = "".join(f"<tr><td class='c'><span class='tag'>{k}</span></td><td>{esc(v)}</td></tr>" for k, v in study.CLASS_DEFS.items())
    st.markdown(f'<table class="fc-tbl"><tr><th>Result</th><th>Meaning</th></tr>{trs}</table>'
                '<div class="fc-note2">Items with no located statement (<span class="tag">no_section</span>) or no SEC value are left out of accuracy, '
                'so a failure of our statement finder is never blamed on the model. Confidence intervals come from resampling whole filings, '
                'because the four numbers from one filing are not independent.</div>', unsafe_allow_html=True)

    st.markdown('<div class="fc-h2">The code behind each step</div>'
                '<div class="fc-lead">This is the real source, read from the files this site runs, not a copy.</div>', unsafe_allow_html=True)
    for title, obj in (("Find the latest 10-K (pipeline.get_latest_10k)", P.get_latest_10k),
                       ("Locate a financial statement (pipeline.find_section)", P.find_section),
                       ("Build the answer key from XBRL (pipeline.xbrl_key)", P.xbrl_key),
                       ("Rule/regex baseline, condition A (pipeline.regex_extract)", P.regex_extract),
                       ("Build the prompt for every condition (study.build_prompt)", study.build_prompt),
                       ("Retrieval for condition D (study.BM25 and study.retrieve)", None),
                       ("Score an answer (pipeline.classify)", P.classify)):
        with st.expander(title):
            st.code(_source(study.BM25) + "\n\n" + _source(study.retrieve) if obj is None else _source(obj), language="python")

    st.markdown('<div class="fc-h2">Reproducibility</div>', unsafe_allow_html=True)
    runs = man.get("runs", [])
    if runs:
        trs = "".join(f"<tr><td class='m'>{esc(r['started'])}</td><td>{esc(', '.join(r['models']))}</td><td>{r['repeats']}</td>"
                      f"<td class='m'>{esc(', '.join(r['cells']))}</td><td class='m'>{esc((r.get('git_commit') or '-')[:8])}</td>"
                      f"<td class='m'>{esc(' '.join(f'{k.split('.')[0]}:{v}' for k, v in (r.get('code_sha') or {}).items()))}</td></tr>" for r in runs)
        st.markdown(f'<table class="fc-tbl"><tr><th>Started (UTC)</th><th>Models</th><th>Repeats</th><th>Conditions</th><th>Commit</th><th>Code hashes</th></tr>{trs}</table>',
                    unsafe_allow_html=True)
    else:
        st.markdown('<div class="fc-note2">No study run recorded yet. Each run writes its models, repeats, conditions and code fingerprints to '
                    '<span class="tag">results/study/manifest.json</span>.</div>', unsafe_allow_html=True)
    st.markdown('<div class="fc-note2">The models cannot be run at a fixed temperature, so the same call can give different answers. '
                'That is why final runs are repeated and the spread reported. Every raw model reply is saved in <span class="tag">results/study/raw/</span> '
                'and every score is recomputed from those files.</div>', unsafe_allow_html=True)

    st.markdown('<div class="fc-h2">Limits to keep in mind</div>'
                '<div class="fc-note2" style="font-size:.88rem">'
                '&bull; Four fields from the annual report of U.S. filers; quarterly reports and other items are not covered.<br>'
                '&bull; Sixty companies is enough to compare conditions but not to describe every industry; groups of six are shown for context only.<br>'
                '&bull; Retrieval uses keyword ranking (BM25), not neural embeddings, so it is a simple retrieval baseline.<br>'
                '&bull; A wrong number that happens to appear elsewhere in the filing is counted as a wrong value, not a fabrication.<br>'
                '&bull; The newest filings may be newer than a model\'s training data, which matters for the closed-book condition.</div>',
                unsafe_allow_html=True)


# ---------------------------------------------------------------------- study

def _bar(e, key, cls=""):
    pt, lo, hi = e[key]
    if pt is None:
        return '<div class="fc-bar"><span style="color:#fff">n/a</span></div>'
    whisk = f'<u style="left:{100 * lo:.1f}%;width:{max(100 * (hi - lo), 0.6):.1f}%"></u>' if lo is not None else ""
    ci = f" ({100 * lo:.0f}-{100 * hi:.0f})" if lo is not None else ""
    return f'<div class="fc-bar {cls}"><i style="width:{100 * pt:.1f}%"></i>{whisk}<span>{100 * pt:.0f}%{ci}</span></div>'


def _pct(v):
    return "n/a" if v is None else f"{100 * v:.0f}%"


@st.cache_data(show_spinner=False)
def _load_rows(stamp):
    return study.build_rows()


def render_study():
    path = "results/study/summary_noamb.json" if st.session_state.get("clean_key") else "results/study/summary.json"
    if not os.path.exists("results/study/summary.json"):
        st.markdown('<div class="fc-h2">Study results</div><div class="fc-empty"><b>No study results yet.</b><br>'
                    '<span class="fc-note2">Run these in your project folder, then reload this page. Each step is explained on the Method tab.</span></div>',
                    unsafe_allow_html=True)
        st.code("python study.py prepare\npython study.py estimate\npython study.py run --budget 5\npython analyze_study.py", language="bash")
        return
    st.toggle("Leave out items whose answer key is uncertain (XBRL tags disagree)", key="clean_key")
    s = study.load_json(path, None) or study.load_json("results/study/summary.json", {})
    cells = s["cells"]
    models = []
    for e in cells:
        if e["model"] != "regex" and e["model"] not in models:
            models.append(e["model"])
    model = st.radio("Model", models, horizontal=True) if len(models) > 1 else (models[0] if models else None)
    shown = [e for e in cells if e["model"] in (model, "regex")]
    shown.sort(key=lambda e: (["A"] + study.ALL_CELLS).index(e["cell"]))
    best = max((e for e in shown if e["accuracy"][0] is not None and e["cell"] != "A"), key=lambda e: e["accuracy"][0])
    audit = (s.get("audit") or {}).get("random")
    kp = [(f"{s['n_filings']}", "filings analysed"), (f"{max(e['n_items'] for e in shown)}", "items scored per condition"),
          (_pct(best["accuracy"][0]), f"best accuracy (condition {best['cell']})"),
          (f"{audit['key_wrong']} of {audit['checked']}" if audit else "not yet", "SEC answer keys wrong in our hand check")]
    st.markdown('<div class="fc-h2">What the study found</div><div class="fc-kpis">' +
                "".join(f'<div class="fc-kpi"><b>{esc(a)}</b><span>{esc(b)}</span></div>' for a, b in kp) + '</div>', unsafe_allow_html=True)

    trs = ""
    for e in shown:
        trs += (f"<tr><td class='c'><b>{e['cell']}</b></td><td>{esc(e['label'])}</td><td class='c'>{e['n_items']}</td>"
                f"<td>{_bar(e, 'accuracy', 'a' if e['cell'] == 'A' else '')}</td><td class='c'>{_pct(e['answer_rate'][0])}</td>"
                f"<td class='c'>{_pct(e['precision'][0])}</td><td class='c'>{_pct(e['fabrication'][0])}</td></tr>")
    st.markdown('<div class="fc-lead">Bars show accuracy with a 95% confidence interval (white line). <b>Answered</b> is how often the model gave a number; '
                '<b>precision</b> is how often its answers were right; <b>fabricated</b> is how often an answer was a number found nowhere in the filing.</div>'
                f'<table class="fc-tbl"><tr><th>Cond.</th><th>What the model sees</th><th>Items</th><th>Accuracy (95% CI)</th><th>Answered</th>'
                f'<th>Precision</th><th>Fabricated</th></tr>{trs}</table>', unsafe_allow_html=True)

    legend = "".join(f'<span><b style="background:{c}"></b>{k}</span>' for k, c in CLASS_COLOR.items())
    trs = ""
    for e in shown:
        segs = "".join(f'<i style="width:{100 * e["classes"][k]:.2f}%;background:{CLASS_COLOR[k]}" title="{k}: {100 * e["classes"][k]:.1f}%"></i>'
                       for k in CLASS_COLOR if e["classes"].get(k))
        trs += f"<tr><td class='c'><b>{e['cell']}</b></td><td><div class='fc-stack'>{segs}</div></td></tr>"
    st.markdown('<div class="fc-h2">What went wrong, by condition</div>'
                f'<div class="fc-legend">{legend}</div><table class="fc-tbl"><tr><th>Cond.</th><th>Share of items by outcome</th></tr>{trs}</table>',
                unsafe_allow_html=True)

    if s.get("paired"):
        items = [p for p in s["paired"] if p["model"] == model]
        if items:
            trs = "".join(f"<tr><td>{esc(p['what'])}</td><td class='m'>{p['a']} minus {p['b']}</td><td class='c'><b>{100 * p['diff']:+.1f}</b> points</td>"
                          f"<td class='m'>{100 * p['lo']:+.0f} to {100 * p['hi']:+.0f}</td></tr>" for p in items)
            st.markdown('<div class="fc-h2">Head-to-head comparisons</div><div class="fc-lead">Same filings, same fields. If the interval includes 0, the data do '
                        f'not show a real difference.</div><table class="fc-tbl"><tr><th>Comparison</th><th>Conditions</th><th>Difference in accuracy</th><th>95% interval</th></tr>{trs}</table>',
                        unsafe_allow_html=True)

    cond_cols = [e for e in shown if e["cell"] in ("A", "B", "C", "D", "E")]
    groups = sorted({g for e in cond_cols for g in e["by_group"]})
    if groups and cond_cols:
        head = "".join(f"<th>{e['cell']}</th>" for e in cond_cols)
        body = ""
        for g in groups:
            tds = ""
            for e in cond_cols:
                v = e["by_group"].get(g)
                a = 0 if v is None else 0.08 + 0.45 * v
                tds += f"<td class='c' style='background:rgba(155,232,190,{a:.2f})'>{_pct(v)}</td>"
            body += f"<tr><td>{esc(g)}</td>{tds}</tr>"
        st.markdown(f'<div class="fc-h2">By sector</div><table class="fc-tbl"><tr><th>Group</th>{head}</tr>{body}</table>', unsafe_allow_html=True)

    c = s["coverage"]
    f = c["statement_finder"]
    notes = [f"The statement finder located the needed statement for {f['items'] - f['no_section']} of {f['items']} items; the rest were excluded, not blamed on the model.",
             f"{c["ambiguous_keys"]} item(s) have an uncertain answer key (SEC tags disagree)."]
    if c["answered_without_sec_value"]:
        notes.append("Answers given where the SEC has no value to check against: " +
                     ", ".join(f"{k}: {v}" for k, v in c["answered_without_sec_value"].items()) + ".")
    if audit:
        notes.append(f"Hand check of {audit['checked']} random items: the SEC value was wrong for {audit['key_wrong']} "
                     f"({100 * audit['rate']:.0f}%, 95% interval {100 * audit['lo']:.0f}% to {100 * audit['hi']:.0f}%).")
    st.markdown('<div class="fc-h2">Data quality</div><div class="fc-note2" style="font-size:.88rem">' + "<br>".join("&bull; " + esc(n) for n in notes) + '</div>',
                unsafe_allow_html=True)

    st.markdown('<div class="fc-h2">Every scored item</div>', unsafe_allow_html=True)
    if st.button("Load the item-level table"):
        import pandas as pd
        raw_dir = study.RAW_DIR
        stamp = (os.path.getmtime(raw_dir) if os.path.isdir(raw_dir) else 0, os.path.getmtime(study.FILINGS_JSON))
        with st.spinner("Rebuilding every score from the saved model replies..."):
            df = pd.DataFrame(_load_rows(stamp))
        keep = ["ticker", "group", "field", "model", "cell", "repeat", "key", "key_tag", "key_ambiguous", "value", "cls", "quote_found"]
        st.session_state["item_df"] = df[keep].rename(columns={"cls": "result", "value": "model_value", "key": "sec_value"})
    df = st.session_state.get("item_df")
    if df is not None:
        a, b, c3 = st.columns(3)
        co = a.selectbox("Company", ["all"] + sorted(df["ticker"].unique()))
        cd = b.selectbox("Condition", ["all"] + sorted(df["cell"].unique()))
        rs = c3.selectbox("Result", ["all"] + sorted(df["result"].unique()))
        view = df
        if co != "all":
            view = view[view["ticker"] == co]
        if cd != "all":
            view = view[view["cell"] == cd]
        if rs != "all":
            view = view[view["result"] == rs]
        st.dataframe(view, use_container_width=True, height=380)
        st.download_button("Download all items (CSV)", df.to_csv(index=False), "filingcheck_items.csv", "text/csv")
    if os.path.exists("results/study/cell_summary.csv"):
        st.download_button("Download the summary table (CSV)", open("results/study/cell_summary.csv").read(), "cell_summary.csv", "text/csv")

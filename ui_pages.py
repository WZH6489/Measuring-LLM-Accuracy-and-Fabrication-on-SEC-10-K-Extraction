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
CLASS_COLOR = {"match": "#4fd1a1", "wrong_scale": "#f5c463", "wrong_value": "#f59e5b", "not_in_text": "#ff7a7a",
               "abstained": "#9aa3b8", "missing": "#5d6478", "bad_json": "#a78bfa"}

EXTRA_CSS = """<style>
/* Tabs: one glass segmented bar; the selected tab is marked by type and an accent rule, not a second surface. */
[role="tablist"] { gap:2px; border-radius:999px; padding:5px 6px !important; width:fit-content; margin:34px auto 10px;
  border:1px solid var(--glass-border) !important; background: linear-gradient(180deg, var(--glass-tint-top), var(--glass-tint-bottom)) !important;
  box-shadow: var(--glass-shadow), inset 0 1px 0 var(--glass-rim-top), inset 0 0 0 1px var(--glass-rim-hairline), inset 0 -1px 0 var(--glass-rim-bottom);
  backdrop-filter: blur(3px) saturate(125%); -webkit-backdrop-filter: blur(3px) saturate(125%); }
[data-testid="stTab"] { position:relative; border-radius:999px !important; padding:8px 20px !important; background:transparent !important; height:auto !important; border:none !important; }
[data-testid="stTab"] p { color:var(--fc-faint) !important; font-weight:500; font-size:.88rem; transition: color .15s; }
[data-testid="stTab"]:hover p { color:var(--fc-muted) !important; }
[data-testid="stTab"][aria-selected="true"] p { color:var(--fc-fg) !important; font-weight:600; }
[data-testid="stTab"][aria-selected="true"]::after { content:""; position:absolute; left:50%; bottom:3px; width:16px; height:2px; margin-left:-8px; border-radius:2px; background:var(--fc-accent); }
.react-aria-SelectionIndicator, [data-baseweb="tab-highlight"], [data-baseweb="tab-border"] { display:none !important; }
[data-orientation="horizontal"][data-rac]:has(> [role="tablist"]) { border-bottom:none !important; }
[data-testid="stCode"], [data-testid="stCode"] pre { background:rgba(0,0,0,.22) !important; border-radius:10px !important; }
[data-testid="stCode"] code { font-family:var(--fc-mono) !important; font-size:.76rem !important; }
.stRadio label, .stRadio p, .stToggle label, .stToggle p, .stCheckbox label, .stCheckbox p, .stSelectbox label, .stSelectbox p, [data-testid="stCaptionContainer"] p { color:var(--fc-muted) !important; }
.fc-h2 { font-size:1.3rem; font-weight:500; letter-spacing:-.02em; line-height:1.2; margin:40px 0 8px; }
.fc-lead { color:var(--fc-muted); font-size:1.02rem; line-height:1.65; max-width:760px; margin-bottom:16px; }
.fc-flow { display:flex; align-items:stretch; gap:8px; flex-wrap:wrap; margin:16px 0 6px; }
.fc-flow i { align-self:center; font-style:normal; color:var(--fc-faint); }
.fc-fl { flex:1 1 150px; border-radius:var(--glass-radius); padding:14px 16px; }
.fc-fl b { display:block; font-size:.86rem; font-weight:600; margin-bottom:4px; } .fc-fl span { font-size:.78rem; color:var(--fc-muted); line-height:1.45; display:block; }
/* Tables carry dense text, so they take a heavier frost than the panels. */
.fc-tbl { width:100%; border-collapse:separate; border-spacing:0; font-size:.85rem; border-radius:var(--glass-radius); overflow:hidden;
  border:1px solid var(--glass-border); background: color-mix(in srgb, var(--glass-menu-surface) 55%, transparent);
  box-shadow: inset 0 1px 0 var(--glass-rim-top), inset 0 0 0 1px var(--glass-rim-hairline);
  backdrop-filter: blur(14px) saturate(130%); -webkit-backdrop-filter: blur(14px) saturate(130%); }
.fc-tbl th, .fc-tbl td { border-left:none !important; border-right:none !important; border-bottom:none !important; }
.fc-tbl th { text-align:left; font-size:.68rem; letter-spacing:.09em; text-transform:uppercase; color:var(--fc-faint); padding:12px 14px 10px; font-weight:600; }
.fc-tbl td { padding:11px 14px; border-top:1px solid var(--fc-hair); vertical-align:top; color:var(--fc-fg); }
.fc-tbl td.m { font-family:var(--fc-mono); font-size:.76rem; } .fc-tbl td.c { white-space:nowrap; font-variant-numeric:tabular-nums; }
.fc-tbl .tag, .fc-tl-d .tag, .fc-note2 .tag { display:inline-block; font-family:var(--fc-mono); font-size:.74rem; color:var(--fc-accent); margin:1px 6px 1px 0; }
.fc-bar { position:relative; height:20px; min-width:150px; background:rgba(255,255,255,.06); border-radius:6px; overflow:hidden; }
.fc-bar i { position:absolute; left:0; top:0; bottom:0; background:linear-gradient(90deg,rgba(94,234,212,.45),rgba(94,234,212,.85)); border-radius:6px; }
.fc-bar.a i { background:linear-gradient(90deg,rgba(233,236,242,.25),rgba(233,236,242,.4)); }
.fc-bar u { position:absolute; top:9px; height:2px; background:rgba(255,255,255,.9); border-radius:1px; }
.fc-bar span { position:absolute; left:9px; top:2px; font-size:.72rem; font-weight:600; color:#fff; font-variant-numeric:tabular-nums; text-shadow:0 1px 3px rgba(0,0,0,.5); }
.fc-stack { display:flex; gap:2px; height:20px; min-width:220px; border-radius:6px; overflow:hidden; } .fc-stack i { display:block; height:100%; }
.fc-legend { display:flex; flex-wrap:wrap; gap:6px 16px; font-size:.76rem; color:var(--fc-muted); margin:8px 0 10px; } .fc-legend b { display:inline-block; width:9px; height:9px; border-radius:2px; margin-right:6px; vertical-align:middle; }
.fc-kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; margin:16px 0; }
.fc-kpi { border-radius:var(--glass-radius-lg); padding:20px 22px; }
.fc-kpi b { display:block; font-weight:300; font-size:2.4rem; line-height:1.05; font-variant-numeric:tabular-nums; } .fc-kpi span { font-size:.78rem; color:var(--fc-muted); }
.fc-tl { display:flex; gap:16px; padding:14px 0; }
.fc-tl-n { flex:0 0 26px; font-family:var(--fc-mono); font-size:.76rem; line-height:1.6; color:var(--fc-accent); padding-top:2px; }
.fc-tl-b { flex:1; min-width:0; } .fc-tl-h { display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap; align-items:center; font-size:.93rem; }
.fc-tl-d { font-size:.84rem; color:var(--fc-muted); line-height:1.6; margin-top:6px; }
.fc-note2 { font-size:.8rem; color:var(--fc-muted); line-height:1.6; margin-top:10px; }
.fc-empty { border-radius:var(--glass-radius-lg); padding:24px 26px; }
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
                           '(the full list of types is on the Study page).'
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


def render_method_core():
    st.markdown('<div class="fc-lead">The study asks: when a language model reads a 10-K, how often does it get the key numbers right, '
                'and when it is wrong, is the number misread or made up? Every number the model gives is compared with the figure the '
                'company itself filed with the SEC in machine-readable form, so no one has to judge answers by hand.</div>'
                '<div class="fc-flow">'
                '<div class="fc-fl fc-lg"><b>1 &middot; SEC EDGAR</b><span>download the latest 10-K</span></div><i>&rarr;</i>'
                '<div class="fc-fl fc-lg"><b>2 &middot; Statement finder</b><span>locate the income statement and balance sheet</span></div><i>&rarr;</i>'
                '<div class="fc-fl fc-lg"><b>3 &middot; The model</b><span>extract four numbers, each with a quote</span></div><i>&rarr;</i>'
                '<div class="fc-fl fc-lg"><b>4 &middot; Verifier</b><span>look up the SEC XBRL answer key</span></div><i>&rarr;</i>'
                '<div class="fc-fl fc-lg"><b>5 &middot; Scoring</b><span>match, or what kind of error</span></div></div>',
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
                'attributable net income). We flag items where tags disagree, and the results section shows how often a hand check found the key wrong.</div>',
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

    st.markdown('<div class="fc-h2">How each answer is scored</div>', unsafe_allow_html=True)
    trs = "".join(f"<tr><td class='c'><span class='tag'>{k}</span></td><td>{esc(v)}</td></tr>" for k, v in study.CLASS_DEFS.items())
    st.markdown(f'<table class="fc-tbl"><tr><th>Result</th><th>Meaning</th></tr>{trs}</table>'
                '<div class="fc-note2">Items with no located statement (<span class="tag">no_section</span>) or no SEC value are left out of accuracy, '
                'so a failure of our statement finder is never blamed on the model. Confidence intervals come from resampling whole filings, '
                'because the four numbers from one filing are not independent.</div>', unsafe_allow_html=True)



def render_method_appendix():
    man = study.load_json(study.MANIFEST_JSON, {})
    with st.expander("See the exact prompt for each condition"):
        for cell in study.ALL_CELLS:
            st.caption(f"Condition {cell}: {study.CELLS[cell]['label']}")
            st.code(_demo_prompt(cell), language="text")
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



# ---------------------------------------------------------------------- study

def _bar(e, key, cls=""):
    pt, lo, hi = e[key]
    if pt is None:
        return '<div class="fc-bar"><span>n/a</span></div>'
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
        st.markdown('<div class="fc-h2">Study results</div><div class="fc-empty fc-lg"><b>No study results yet.</b><br>'
                    '<span class="fc-note2">Run these in your project folder, then reload this page. Each step is explained on the Study page.</span></div>',
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
                tds += f"<td class='c' style='background:rgba(79,209,161,{a:.2f})'>{_pct(v)}</td>"
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


# ------------------------------------------------------------- study page

STUDY_CSS = """<style>
.fs-hero { position:relative; isolation:isolate; text-align:center; padding:96px 0 0; }
.fs-kick { font-family:var(--fc-mono); font-size:.72rem; font-weight:500; letter-spacing:.2em; text-transform:uppercase; color:var(--fc-accent); }
.fs-title { font-weight:300; font-size:clamp(2.6rem,6.2vw,4.9rem); line-height:1.02; letter-spacing:-.045em; margin:22px auto 20px; max-width:900px; }
.fs-title span { color:var(--fc-faint); }
.fs-meta { font-size:.95rem; color:var(--fc-muted); } .fs-meta b { color:var(--fc-fg); font-weight:500; }
.fs-toc { display:flex; flex-wrap:wrap; justify-content:center; gap:8px; margin:30px auto 0; max-width:760px; }
.fs-toc a { text-decoration:none !important; color:var(--fc-muted) !important; font-size:.82rem; padding:7px 14px; border-radius:999px; }
.fs-toc a:hover { color:var(--fc-fg) !important; }
.fs-kpis { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin-top:72px; text-align:left; }
.fs-kpi { border-radius:20px; padding:20px 22px; }
.fs-kpi small { display:block; font-family:var(--fc-mono); font-size:.66rem; letter-spacing:.14em; text-transform:uppercase; color:var(--fc-faint); }
.fs-kpi b { display:block; font-weight:300; font-size:2.7rem; letter-spacing:-.04em; line-height:1; margin:18px 0 8px; font-variant-numeric:tabular-nums; }
.fs-kpi span { font-size:.84rem; color:var(--fc-muted); line-height:1.45; display:block; }
.fs-sec { padding-top:120px; scroll-margin-top:20px; }
.fs-sec-h { display:grid; grid-template-columns:110px minmax(0,1fr); gap:20px; margin-bottom:26px; align-items:start; }
.fs-num { font-weight:200; font-size:3.4rem; line-height:.9; letter-spacing:-.04em; color:var(--fc-faint); font-variant-numeric:tabular-nums; }
.fs-h { font-weight:300; font-size:clamp(2rem,4vw,3rem); letter-spacing:-.04em; line-height:1.06; margin-top:12px; }
.fs-body { margin-left:0; }
.fs-lead { font-size:1.12rem; line-height:1.65; color:var(--fc-muted); max-width:780px; }
.fs-lead + .fs-lead { margin-top:14px; } .fs-lead b { color:var(--fc-fg); font-weight:500; }
.fs-cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:14px; margin-top:26px; }
.fs-cards.three { grid-template-columns:repeat(3,minmax(0,1fr)); }
.fs-card { border-radius:20px; padding:22px 24px 24px; }
.fs-card small { font-family:var(--fc-mono); font-size:.66rem; letter-spacing:.14em; text-transform:uppercase; color:var(--fc-faint); }
.fs-card h4 { font-size:1.12rem; font-weight:500; letter-spacing:-.02em; margin:12px 0 8px; color:var(--fc-fg); }
.fs-card p { font-size:.9rem; line-height:1.55; color:var(--fc-muted); margin:0; }
.fs-card .fs-big { font-weight:300; font-size:3rem; letter-spacing:-.04em; line-height:1; margin:16px 0 10px; color:var(--fc-fg); }
.fs-card .fs-big.bad { color:var(--fc-bad); } .fs-card .fs-big.ok { color:var(--fc-accent); }
.fs-refs { font-size:.84rem; color:var(--fc-muted); line-height:1.6; } .fs-refs p { margin:0 0 10px; }
.fs-refs a { color:var(--fc-link) !important; }
.fs-body .fc-h2 { font-size:1.25rem; font-weight:500; margin:40px 0 8px; letter-spacing:-.02em; }
@media (max-width: 760px) {
  .fs-cards.three { grid-template-columns:1fr; }
  .fs-kpis { grid-template-columns:1fr 1fr; margin-top:48px; } .fs-sec-h { grid-template-columns:1fr; gap:4px; } .fs-body { margin-left:0; }
  .fs-num { font-size:2.4rem; } .fs-hero { min-height:0; padding-bottom:20px; } }
</style>"""


def _sec(n, sid, kick, title):
    st.markdown(f'<div class="fs-sec" id="{sid}"><div class="fs-sec-h"><div class="fs-num">{n}</div>'
                f'<div><div class="fs-kick">{kick}</div><div class="fs-h">{title}</div></div></div></div>', unsafe_allow_html=True)


def _body(html_text):
    st.markdown(f'<div class="fs-body">{html_text}</div>', unsafe_allow_html=True)


def _cell(s, name):
    rows = [e for e in s.get("cells", []) if e["cell"] == name]
    return next((e for e in rows if e["model"] != "regex"), rows[0] if rows else None)


def headline_findings(s):
    """The four numbers the study is about, read from the summary so they never drift from the tables."""
    a, b, c, e = (_cell(s, k) for k in ("A", "B", "C", "E"))
    if not all((a, b, c, e)):
        return []
    pct = lambda v: f"{100 * v:.0f}%" if v is not None and v >= .1 else f"{100 * v:.1f}%"
    return [
        ("Condition C · few-shot", pct(c["accuracy"][0]), "ok", "Accuracy when the model reads the statements, with worked examples."),
        ("Condition B · fabrication", pct(b["fabrication"][0]), "", "Of answers were numbers that appear nowhere in the filing."),
        ("Condition E · closed-book", pct(e["fabrication"][0]), "bad", "Fabricated when the model answers from memory, with no filing."),
        ("Condition A · no model", pct(a["accuracy"][0]), "", f"For a keyword baseline. Reading with an LLM adds "
                                                             f"{100 * (b['accuracy'][0] - a['accuracy'][0]):.0f} points."),
    ]


def render_study_page():
    st.markdown(STUDY_CSS, unsafe_allow_html=True)
    s = study.load_json("results/study/summary.json", {})
    models = sorted({e["model"] for e in s.get("cells", []) if e["model"] != "regex"})
    def pretty(m):  # claude-haiku-5-5 -> Claude Haiku 5.5
        parts = m.split("-")
        words = [w.title() for w in parts if not w.isdigit()]
        nums = [w for w in parts if w.isdigit()]
        return " ".join(words) + (" " + ".".join(nums) if nums else "")
    model_name = ", ".join(pretty(m) for m in models) or "Claude"
    finds = headline_findings(s)
    kpis = "".join(f'<div class="fs-kpi fc-lg"><small>{esc(k)}</small><b>{esc(v)}</b><span>{esc(t)}</span></div>' for k, v, _, t in finds)
    toc = "".join(f'<a class="fc-chip" href="#{i}" target="_self">{t}</a>' for i, t in (
        ("problem", "Problem"), ("related", "Related work"), ("method", "Method"), ("results", "Results"),
        ("findings", "Findings"), ("conclusion", "Conclusion"), ("appendix", "Appendix")))
    st.markdown('<div class="fs-hero">' +
                '<div class="fs-kick">CMPT 218 &middot; Fall 2026 &middot; Final project</div>'
                '<div class="fs-title">Measuring LLM accuracy <span>and fabrication on SEC 10-K extraction</span></div>'
                '<div class="fs-meta"><b>William Huang, Vincent Zhao, James Park</b> &middot; Simon Fraser University</div>'
                f'<div class="fs-toc">{toc}</div>'
                f'<div class="fs-kpis">{kpis}</div></div>', unsafe_allow_html=True)

    _sec("01", "problem", "The problem", "10-Ks are long. People ask AI instead.")
    _body('<div class="fs-lead">SEC filings are how investors judge a company\'s health and risks, but a 10-K runs past a hundred pages of '
          'legal language. The 1998 Plain English rule only covers a few sections, and XBRL tags make figures machine-readable but '
          'not readable to most people.</div>'
          '<div class="fs-lead">So people increasingly paste filings into an AI model. Models can <b>misread</b> a table or '
          '<b>fabricate</b> a number. We measure how often each happens, using the SEC\'s own XBRL data as the answer key.</div>')

    _sec("02", "related", "Related work", "Where this sits.")
    _body('<div class="fs-cards">'
          '<div class="fs-card fc-lg"><small>Chen et al. · 2021</small><h4>FinQA</h4><p>Expert-written questions with step-by-step '
          'calculations. Tests numerical reasoning; models trailed human experts.</p></div>'
          '<div class="fs-card fc-lg"><small>Islam et al. · 2023</small><h4>FinanceBench</h4><p>10,231 questions over 40 companies. 81% of '
          'answers were wrong or refused. Human-checked answers; ours come from XBRL.</p></div>'
          '<div class="fs-card fc-lg"><small>Zhu et al. · 2021</small><h4>TAT-QA</h4><p>Mixed tables and text. Best model 58% vs 90.8% for '
          'experts, scored right or wrong only. We classify why answers are wrong.</p></div></div>')

    _sec("03", "method", "Method", "One answer key. Nine conditions.")
    render_method_core()

    _sec("04", "results", "Results", f"How {esc(model_name)} did.")
    with st.container():
        render_study()

    _sec("05", "findings", "Key findings", "What the numbers say.")
    p = {x["a"] + "-" + x["b"]: x for x in s.get("paired", [])}
    def pts(k):
        x = p.get(k)
        return f"{100 * x['diff']:+.1f} points (95% interval {100 * x['lo']:+.0f} to {100 * x['hi']:+.0f})" if x else ""
    cards = [
        ("Reading beats rules", pts("B-A"), "The model reading the statements beats keyword matching by a wide margin, with fabrication staying under 1%."),
        ("Examples help a little", pts("C-B"), "Few-shot prompting gives a small, marginal gain over zero-shot."),
        ("Retrieval hurts", pts("D-B"), "Showing only search-retrieved passages is worse than handing over the full statements."),
        ("Memory is not reading", pts("E-B"), "Without the filing, the model is mostly wrong, and most of what it says is fabricated."),
        ("“I don’t know” barely matters", pts("B+F-B"), "Allowing null answers changes accuracy very little. Closed-book, the model simply never answers."),
        ("The key can be wrong", "", "With ambiguous XBRL keys removed, zero-shot rises to the top: much of the remaining error is the answer key, not the model."),
    ]
    _body('<div class="fs-cards three">' + "".join(f'<div class="fs-card fc-lg"><h4>{esc(h)}</h4><p>{esc(t)}</p>'
                                              + (f'<p style="margin-top:12px;font-family:var(--fc-mono);font-size:.74rem;color:var(--fc-accent)">{esc(d)}</p>' if d else "")
                                              + '</div>' for h, d, t in cards) + '</div>')

    _sec("06", "conclusion", "Conclusion", "Trust it to read. Still check.")
    _body('<div class="fs-lead">Given the financial statements, the model extracts the four fields accurately and rarely fabricates. '
          'Without the document it is mostly wrong and mostly made up. An LLM can be trusted to read simple fields from a filing, '
          'but the values should still be double-checked, which is what FilingCheck does for every answer.</div>'
          '<div class="fs-cards">'
          '<div class="fs-card fc-lg"><small>Limits</small><p>Four fields, one model, about sixty companies, annual reports only. '
          'Retrieval is keyword-based (BM25), not neural.</p></div>'
          '<div class="fs-card fc-lg"><small>Future work</small><p>More models, a larger sample, harder questions, and hand-verifying '
          'the ambiguous answer keys.</p></div>'
          '<div class="fs-card fc-lg"><small>Ethics</small><p>Public data only. Easy access can invite over-trust, so every answer is '
          'labelled AI-generated, checked against the SEC, and marked not financial advice.</p></div></div>')

    _sec("07", "appendix", "Appendix", "Prompts, code and reproducibility.")
    render_method_appendix()
    st.markdown('<div class="fs-body"><div class="fc-h2">References</div><div class="fs-refs">'
                '<p>[1] Chen et al. FinQA: A Dataset of Numerical Reasoning over Financial Data. EMNLP 2021.</p>'
                '<p>[2] Islam et al. FinanceBench: A New Benchmark for Financial Question Answering. arXiv:2311.11944, 2023.</p>'
                '<p>[3] Zhu et al. TAT-QA: A Question Answering Benchmark on a Hybrid of Tabular and Textual Content in Finance. ACL 2021.</p>'
                '<p>[4] Huang, Zhao, Park. Measuring LLM Accuracy and Fabrication on SEC 10-K Extraction. '
                '<a href="https://github.com/WZH6489/Measuring-LLM-Accuracy-and-Fabrication-on-SEC-10-K-Extraction" target="_blank">GitHub</a>, 2026.</p>'
                '</div></div>', unsafe_allow_html=True)

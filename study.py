"""FilingCheck study runner: the full experiment across ~60 filings.

Steps (each can be re-run; finished work is never repeated):

    python study.py prepare                 # download filings, build answer keys, run the regex baseline (free)
    python study.py estimate                # how much the model calls will cost (free)
    python study.py run --budget 5          # call the model; stops if estimated spend passes the budget
    python study.py status                  # what is done so far
    python analyze_study.py                 # tables, confidence intervals, audit sample

Conditions (every one uses the SAME answer key, from SEC XBRL data):
    A    rule/regex baseline, no model
    B    zero-shot: the model sees only the financial statements
    C    few-shot: B plus two worked examples from OTHER companies' filings
    D    retrieval: the model sees only the filing passages a keyword search (BM25) retrieves
    E    closed-book: the model sees no document, only the company and fiscal year
    +F   abstention variant (B+F, C+F, D+F, E+F): the model is told to answer null if unsure

Needs:  pip install requests beautifulsoup4 anthropic      export ANTHROPIC_API_KEY="..."
"""
import argparse
import hashlib
import functools
import json
import math
import os
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache

import pipeline as P
from companies import HARD, HELD_OUT, SAMPLE

STUDY_DIR = "results/study"
RAW_DIR = f"{STUDY_DIR}/raw"
PROMPT_DIR = f"{STUDY_DIR}/prompts"
FILINGS_JSON = f"{STUDY_DIR}/filings.json"
FEWSHOT_JSON = f"{STUDY_DIR}/fewshot.json"
MANIFEST_JSON = f"{STUDY_DIR}/manifest.json"

# Estimated price in US dollars per million tokens (input, output). Check the Console for current prices.
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.00, 10.00)}
DEFAULT_PRICE = (2.00, 10.00)  # unknown model: assume the expensive one
MAX_TOKENS = 4000

CELLS = {
    "B":   {"label": "Zero-shot, statements only",       "context": "statements", "abstain": False, "shots": 0},
    "C":   {"label": "Few-shot, statements only",        "context": "statements", "abstain": False, "shots": 2},
    "D":   {"label": "Retrieval (BM25 top passages)",    "context": "retrieved",  "abstain": False, "shots": 0},
    "E":   {"label": "Closed-book (no document)",        "context": "none",       "abstain": False, "shots": 0},
    "B+F": {"label": "Zero-shot + may answer null",      "context": "statements", "abstain": True,  "shots": 0},
    "C+F": {"label": "Few-shot + may answer null",       "context": "statements", "abstain": True,  "shots": 2},
    "D+F": {"label": "Retrieval + may answer null",      "context": "retrieved",  "abstain": True,  "shots": 0},
    "E+F": {"label": "Closed-book + may answer null",    "context": "none",       "abstain": True,  "shots": 0},
}
ALL_CELLS = list(CELLS)

KEY_RULES = [
    "Only values reported in this exact 10-K (matching accession number) and fiscal period end.",
    "Flow items (revenue, net income, operating income) must cover 340-380 days.",
    "The first tag in each list that has a value is the key; other tags that disagree by more than 0.1% mark the item as ambiguous.",
    "Net income is NetIncomeLoss (attributable to the parent) before ProfitLoss.",
]
CLASS_DEFS = {
    "match": "within 1% of the SEC value",
    "wrong_scale": "off by exactly 1,000x or 1,000,000x",
    "wrong_value": "a number that does appear in the filing, but is not the SEC value (wrong line, period or column)",
    "not_in_text": "a number that appears nowhere in the filing: fabricated or recalled wrongly",
    "missing": "no answer given (when the model was not allowed to abstain)",
    "abstained": "the model answered null when it was allowed to",
    "bad_json": "the reply could not be parsed",
    "no_section": "the financial statement could not be located in the filing, so the model was never asked",
    "answered_no_key": "the model gave a number but SEC has no value to check it against",
    "no_key": "no SEC value and no answer: not scored",
}

# ------------------------------------------------------------------ prompts
# These are the exact texts sent to the model. The website's Method page shows them.

HEAD = """You are extracting numbers from the financial statements of a 10-K filing.
Return JSON only, no other text, with these keys: {fields}.
For each key give an object with: value (a number in full US dollars, applying any scale stated in the table header such as "in millions"), unit, period (the fiscal period it covers), and quote (a verbatim quote from the text that contains the number).
Use the most recent fiscal year only.
{extra}
{body}"""
ABSTAIN_TEXT = "If a figure is not reported in the text, set that key to null. Do not guess.\n"

HEAD_CLOSED = """No document is provided. From your own knowledge, state these figures from the 10-K that {company} filed for the fiscal year ended {period_end}.
Return JSON only, no other text, with these keys: {fields}.
For each key give an object with: value (a number in full US dollars), unit, period (the fiscal period it covers), and quote (set this to null, because there is no document to quote).
{extra}"""
CLOSED_ANSWER = "Give your best answer for every key, even if you are unsure."
CLOSED_ABSTAIN = "If you do not know a figure, set that key to null. Do not guess."

RETRIEVED_NOTE = ("The text below is a set of passages retrieved from the filing by a keyword search. "
                  "It is not the whole filing, and the passages may not be complete statements.\n\n")
FEWSHOT_INTRO = ("Here are worked examples from other companies' filings, showing the expected output. "
                 "Your filing follows them.\n\n")

QUERIES = {  # condition D: what the keyword search looks for, per field
    "revenue": "total revenue net sales revenues consolidated statements of operations",
    "net_income": "net income attributable consolidated statements of income",
    "operating_income": "operating income income from operations consolidated statements of operations",
    "total_assets": "total assets consolidated balance sheets",
}
RETRIEVAL = {"method": "BM25 (sparse keyword ranking, not neural embeddings)", "chunk_chars": 1500,
             "overlap_chars": 250, "top_k_per_field": 3, "max_chars": 24000}


def build_prompt(cell, ctx, shots=None):
    """ctx: dict with section_text, chunks_text, company, period_end. shots: list of few-shot examples."""
    c = CELLS[cell]
    fields = ", ".join(P.FIELDS)
    if c["context"] == "none":
        extra = CLOSED_ABSTAIN if c["abstain"] else CLOSED_ANSWER
        return HEAD_CLOSED.format(company=ctx["company"], period_end=ctx["period_end"], fields=fields, extra=extra)
    extra = ABSTAIN_TEXT if c["abstain"] else ""
    if c["context"] == "retrieved":
        body = RETRIEVED_NOTE + ctx["chunks_text"]
    else:
        body = ctx["section_text"]
        if c["shots"] and shots:
            ex = ""
            for i, s in enumerate(shots[: c["shots"]], 1):
                ex += (f"### Example {i}\n{s['text']}\n\nExpected output for example {i}:\n"
                       f"{json.dumps(s['answer'], indent=1)}\n\n")
            body = FEWSHOT_INTRO + ex + "### Your filing\n" + body
    return HEAD.format(fields=fields, extra=extra, body=body)


# ---------------------------------------------------------------- retrieval

def _tokens(s):
    return re.findall(r"[a-z]+", s.lower())


def chunk_text(text, size=RETRIEVAL["chunk_chars"], overlap=RETRIEVAL["overlap_chars"]):
    """Split into passages of about `size` characters on line boundaries, overlapping a little."""
    lines, chunks, cur, n = text.split("\n"), [], [], 0
    for ln in lines:
        cur.append(ln)
        n += len(ln) + 1
        if n >= size:
            chunks.append("\n".join(cur))
            keep, k = [], 0
            for prev in reversed(cur):  # carry the tail over as overlap
                if k + len(prev) > overlap:
                    break
                keep.insert(0, prev)
                k += len(prev) + 1
            cur, n = keep, k
    if cur:
        chunks.append("\n".join(cur))
    return chunks


class BM25:
    def __init__(self, chunks, k1=1.5, b=0.75):
        self.docs = [Counter(_tokens(c)) for c in chunks]
        self.len = [sum(d.values()) for d in self.docs]
        self.avg = (sum(self.len) / len(self.len)) if self.len else 1
        self.df = Counter(t for d in self.docs for t in d)
        self.k1, self.b = k1, b

    def rank(self, query):
        N, out = len(self.docs), []
        for d, ln in zip(self.docs, self.len):
            s = 0.0
            for t in set(_tokens(query)):
                if d.get(t):
                    idf = math.log(1 + (N - self.df[t] + 0.5) / (self.df[t] + 0.5))
                    s += idf * d[t] * (self.k1 + 1) / (d[t] + self.k1 * (1 - self.b + self.b * ln / self.avg))
            out.append(s)
        return sorted(range(N), key=lambda i: -out[i])


def retrieve(text):
    chunks = chunk_text(text)
    if not chunks:
        return ""
    bm = BM25(chunks)
    picked = set()
    for q in QUERIES.values():
        picked.update(bm.rank(q)[: RETRIEVAL["top_k_per_field"]])
    out, total = [], 0
    for i in sorted(picked):  # keep document order
        if total + len(chunks[i]) > RETRIEVAL["max_chars"]:
            break
        out.append(chunks[i])
        total += len(chunks[i])
    return "\n---\n".join(out)


# ------------------------------------------------------------- file helpers

def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, path)


def raw_path(ticker, model, cell, repeat):
    return f"{RAW_DIR}/{ticker}__{model}__{cell.replace('+', '')}__r{repeat}.json"


def cost_of(model, tin, tout):
    pin, pout = PRICES.get(model, DEFAULT_PRICE)
    return (tin * pin + tout * pout) / 1e6


@functools.lru_cache(maxsize=None)
def get_text(ticker):
    """Full filing text, cached on disk so repeated runs do not re-parse the HTML."""
    txt = f"data/{ticker}_text.txt"
    if not os.path.exists(txt):
        with open(txt, "w", encoding="utf-8") as f:
            f.write(P.filing_text(f"data/{ticker}_10k.html"))
    return open(txt, encoding="utf-8").read()


def parse_reply(raw):
    try:
        return json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
    except ValueError:
        return None


def call_model(prompt, model):
    import anthropic
    client = anthropic.Anthropic(max_retries=5)
    resp = client.messages.create(model=model, max_tokens=MAX_TOKENS, messages=[{"role": "user", "content": prompt}])
    raw = "".join(b.text for b in resp.content if b.type == "text")
    return raw, resp.usage.input_tokens, resp.usage.output_tokens


# ----------------------------------------------------------------- prepare

def sec_ticker_map():
    data = P.sec_get("https://www.sec.gov/files/company_tickers.json").json()
    return {v["ticker"].upper(): (v["title"], str(v["cik_str"]).zfill(10)) for v in data.values()}


def is_ambiguous(entry):
    """True when different XBRL tags for the same idea give different values: the answer key is then uncertain."""
    if not entry or not entry["alternatives"]:
        return False
    kv = entry["value"]
    return any(abs(v - kv) > 0.001 * max(abs(kv), 1) for _, v in entry["alternatives"])


def prepare(include_hard=True):
    os.makedirs(STUDY_DIR, exist_ok=True)
    filings = load_json(FILINGS_JSON, {})
    tmap = sec_ticker_map()
    entries = SAMPLE + (HARD if include_hard else [])
    for n, (ticker, group) in enumerate(entries, 1):
        old = filings.get(ticker)
        if old and not old.get("error"):
            continue
        print(f"[{n}/{len(entries)}] {ticker} ({group}) ...", end=" ", flush=True)
        try:
            title, cik = tmap[ticker]
            info = P.get_latest_10k(cik)
            P.download_filing(ticker, cik, info)
            text = get_text(ticker)
            secs = P.find_sections(text)
            key = P.xbrl_key(cik, info["accession"], info["period_end"])
            section_text = "\n\n".join(s for s in secs.values() if s)
            regex = P.regex_extract(secs) if section_text else {f: None for f in P.FIELDS}
            rec = {"ticker": ticker, "group": group, "company": title, "cik": cik, "info": info,
                   "key": key, "ambiguous": {f: is_ambiguous(key[f]) for f in P.FIELDS},
                   "sections_found": {k: bool(v) for k, v in secs.items()},
                   "text_chars": len(text), "regex": regex}
            print("ok" + ("" if section_text else "  (statements not found)"))
        except Exception as e:  # one bad filing must not stop the other 59
            rec = {"ticker": ticker, "group": group, "error": f"{type(e).__name__}: {e}"}
            print("FAILED:", rec["error"])
        filings[ticker] = rec
        save_json(FILINGS_JSON, filings)
    ok = [t for t, r in filings.items() if not r.get("error")]
    print(f"\nPrepared {len(ok)} of {len(entries)} filings. Details: {FILINGS_JSON}")


# ------------------------------------------------------------- few-shot set

def quote_for(value, text):
    """A verbatim quote (label line plus number line) from the text that contains this value."""
    lines = text.split("\n")
    for scale in (1, 1_000, 1_000_000):
        n = abs(value) / scale
        if abs(n - round(n)) > 1e-9:
            continue
        pat = re.compile(r"^\s*\(?\$?\s*" + re.escape(f"{int(round(n)):,}") + r"\)?\s*$")
        for i, ln in enumerate(lines):
            if pat.match(ln):
                j = i - 1
                while j >= 0 and not re.search(r"[A-Za-z]{3,}", lines[j]):
                    j -= 1
                return "\n".join(lines[max(j, 0): i + 1]).strip()
    return None


def build_fewshot():
    """Two worked examples from held-out filings (not in the test sample)."""
    cached = load_json(FEWSHOT_JSON, None)
    if cached:
        return cached
    tmap = sec_ticker_map()
    shots = []
    for ticker in HELD_OUT:
        if len(shots) == 2:
            break
        try:
            title, cik = tmap[ticker]
            info = P.get_latest_10k(cik)
            P.download_filing(ticker, cik, info)
            text = get_text(ticker)
            secs = P.find_sections(text)
            key = P.xbrl_key(cik, info["accession"], info["period_end"])
            if not all(secs.values()) or not all(key.values()):
                print(f"  few-shot: {ticker} skipped (incomplete)")
                continue
            body = "\n\n".join(secs.values())
            answer = {}
            for f in P.FIELDS:
                q = quote_for(key[f]["value"], body)
                if not q:
                    raise ValueError(f"no quote for {f}")
                answer[f] = {"value": key[f]["value"], "unit": "USD",
                             "period": f"fiscal year ended {info['period_end']}", "quote": q}
            shots.append({"ticker": ticker, "text": body, "answer": answer})
            print(f"  few-shot example: {ticker}")
        except Exception as e:
            print(f"  few-shot: {ticker} skipped ({type(e).__name__}: {e})")
    if len(shots) < 2:
        raise RuntimeError("could not build two few-shot examples; edit HELD_OUT in companies.py")
    save_json(FEWSHOT_JSON, shots)
    return shots


# --------------------------------------------------------------- contexts

_ctx_cache = {}


def context_for(rec):
    t = rec["ticker"]
    if t not in _ctx_cache:
        text = get_text(t)
        secs = P.find_sections(text)
        _ctx_cache[t] = {
            "company": rec["company"], "period_end": rec["info"]["period_end"],
            "section_text": "\n\n".join(s for s in secs.values() if s),
            "chunks_text": retrieve(text),
        }
        if len(_ctx_cache) > 6:  # keep memory small
            _ctx_cache.pop(next(iter(_ctx_cache)))
    return _ctx_cache[t]


def usable_filings(tickers=None, include_hard=False):
    filings = load_json(FILINGS_JSON, {})
    want = [t for t, _ in SAMPLE] + ([t for t, _ in HARD] if include_hard else [])
    if tickers:
        want = [t for t in tickers if t in filings]
    return [filings[t] for t in want if t in filings and not filings[t].get("error")]


# ---------------------------------------------------------------- estimate

def estimate(models, repeats, cells, tickers, include_hard):
    recs = usable_filings(tickers, include_hard)
    if not recs:
        sys.exit("Nothing prepared yet. Run:  python study.py prepare")
    shots = build_fewshot() if any(CELLS[c]["shots"] for c in cells) else None
    tin = Counter()
    for rec in recs:
        ctx = context_for(rec)
        for c in cells:
            if CELLS[c]["context"] == "statements" and not ctx["section_text"]:
                continue
            tin[c] += len(build_prompt(c, ctx, shots)) / 3.0  # about 3 characters per token for filings
    print(f"{len(recs)} filings, {len(cells)} model conditions, {repeats} repeat(s)\n")
    grand = 0.0
    for m in models:
        total_in = sum(tin.values()) * repeats
        calls = sum(1 for _ in recs) * len(cells) * repeats
        out_tok = calls * 800  # typical reply; thinking tokens, if any, are billed as output too
        cost = cost_of(m, total_in, out_tok)
        grand += cost
        print(f"{m:22} ~{calls} calls, ~{total_in / 1e6:.1f}M input tokens, ~${cost:.2f}")
    print(f"\nEstimated total: ~${grand:.2f}  (a rough figure; the budget flag on 'run' is a hard stop)")


# --------------------------------------------------------------------- run

def git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip() or None
    except Exception:
        return None


def file_sha(path):
    try:
        return hashlib.sha256(open(path, "rb").read()).hexdigest()[:12]
    except OSError:
        return None


def write_manifest(models, repeats, cells):
    old = load_json(MANIFEST_JSON, {})
    runs = old.get("runs", [])
    runs.append({"started": datetime.now(timezone.utc).isoformat(timespec="seconds"), "models": models,
                 "repeats": repeats, "cells": cells, "git_commit": git_commit(),
                 "code_sha": {f: file_sha(f) for f in ("study.py", "pipeline.py", "companies.py")}})
    manifest = {
        "study_version": 1,
        "conditions": {k: {**v} for k, v in CELLS.items()},
        "prompt_templates": {"main": HEAD, "abstain_text": ABSTAIN_TEXT, "closed_book": HEAD_CLOSED,
                             "closed_answer": CLOSED_ANSWER, "closed_abstain": CLOSED_ABSTAIN,
                             "retrieved_note": RETRIEVED_NOTE, "fewshot_intro": FEWSHOT_INTRO},
        "retrieval": {**RETRIEVAL, "queries": QUERIES},
        "answer_key": {"source": "SEC XBRL 'companyfacts' API (data.sec.gov)", "tags": P.TAGS, "rules": KEY_RULES},
        "scoring": {"match_tolerance": 0.01, "classes": CLASS_DEFS},
        "model_settings": {"max_tokens": MAX_TOKENS, "temperature": "not settable on these models; repeats measure the spread"},
        "prices_per_million_tokens": {k: list(v) for k, v in PRICES.items()},
        "sec_access": {"user_agent": P.UA["User-Agent"], "pause_seconds": 0.2},
        "sample": [{"ticker": t, "group": g} for t, g in SAMPLE], "hard_cases": [{"ticker": t, "group": g} for t, g in HARD],
        "held_out_for_fewshot": HELD_OUT, "runs": runs,
    }
    save_json(MANIFEST_JSON, manifest)


def spent_so_far():
    total = 0.0
    if os.path.isdir(RAW_DIR):
        for f in os.listdir(RAW_DIR):
            r = load_json(os.path.join(RAW_DIR, f), {})
            total += r.get("cost_usd", 0.0)
    return total


def run(models, repeats, cells, tickers, budget, include_hard):
    recs = usable_filings(tickers, include_hard)
    if not recs:
        sys.exit("Nothing prepared yet. Run:  python study.py prepare")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set.")
    shots = build_fewshot() if any(CELLS[c]["shots"] for c in cells) else None
    os.makedirs(RAW_DIR, exist_ok=True)
    os.makedirs(PROMPT_DIR, exist_ok=True)
    write_manifest(models, repeats, cells)
    spent, fails, done, skipped = spent_so_far(), 0, 0, 0
    print(f"Already spent (estimated): ${spent:.2f}. Budget: ${budget:.2f}\n")
    for r in range(1, repeats + 1):
        for model in models:
            for rec in recs:
                ctx = context_for(rec)
                for cell in cells:
                    path = raw_path(rec["ticker"], model, cell, r)
                    if os.path.exists(path):
                        continue
                    if CELLS[cell]["context"] == "statements" and not ctx["section_text"]:
                        skipped += 1
                        continue
                    if spent >= budget:
                        print(f"\nBudget reached (${spent:.2f}). Stopping; run again with a higher --budget to continue.")
                        return
                    prompt = build_prompt(cell, ctx, shots)
                    sample = f"{PROMPT_DIR}/{cell.replace('+', '')}.txt"
                    if not os.path.exists(sample):  # keep one full example prompt per condition
                        open(sample, "w", encoding="utf-8").write(f"# {rec['ticker']} / {cell}\n\n{prompt}")
                    try:
                        raw, tin, tout = call_model(prompt, model)
                    except Exception as e:
                        fails += 1
                        print(f"  {rec['ticker']:6} {cell:4} r{r} {model}: ERROR {type(e).__name__}: {str(e)[:100]}")
                        if fails >= 3:
                            sys.exit("Three failures in a row: stopping. Check your API key, credits and model name.")
                        time.sleep(5)
                        continue
                    fails = 0
                    cost = cost_of(model, tin, tout)
                    spent += cost
                    done += 1
                    save_json(path, {"ticker": rec["ticker"], "model": model, "cell": cell, "repeat": r,
                                     "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                     "prompt_sha": hashlib.sha256(prompt.encode()).hexdigest()[:12],
                                     "prompt_chars": len(prompt), "in_tokens": tin, "out_tokens": tout,
                                     "cost_usd": round(cost, 6), "raw": raw})
                    print(f"  {rec['ticker']:6} {cell:4} r{r} {model}: {tin} in / {tout} out  (spent ~${spent:.2f})")
    print(f"\nDone: {done} new calls, {skipped} skipped (statements not found). Estimated spend ${spent:.2f}.")
    print("Next:  python analyze_study.py")


# ------------------------------------------------------------------ scoring

@lru_cache(maxsize=None)
def _classify(ticker, got, kv):
    return P.classify(got, kv, get_text(ticker))


def score_item(rec, cell, field, raw_rec):
    """Class for one (filing, condition, field). raw_rec is None for the regex baseline."""
    entry = rec["key"][field]
    kv = entry["value"] if entry else None
    needs_sec = "balance" if field == "total_assets" else "income"
    sec_missing = not rec["sections_found"].get(needs_sec)
    quote, quote_found, json_ok, got = None, None, True, None
    if raw_rec is None:  # regex baseline
        got = rec["regex"].get(field)
        if sec_missing and kv is not None:
            return {"cls": "no_section", "value": got, "quote": None, "quote_found": None, "json_ok": True}
    else:
        if CELLS[cell]["context"] == "statements" and sec_missing and kv is not None:
            return {"cls": "no_section", "value": None, "quote": None, "quote_found": None, "json_ok": True}
        parsed = parse_reply(raw_rec["raw"])
        json_ok = parsed is not None
        item = (parsed or {}).get(field)
        if isinstance(item, dict):
            got = P.to_number(item.get("value"))
            quote = item.get("quote")
        elif item is not None:
            got = P.to_number(item)
        if quote:
            quote_found = P.norm(quote) in P.norm(get_text(rec["ticker"]))
    if not json_ok and kv is not None:
        cls = "bad_json"
    elif got is None and CELLS.get(cell, {}).get("abstain") and kv is not None:
        cls = "abstained"
    else:
        cls = _classify(rec["ticker"], got, kv)
    return {"cls": cls, "value": got, "quote": quote, "quote_found": quote_found, "json_ok": json_ok}


def build_rows(include_hard=False):
    """Every scored item: one row per (filing, model, condition, repeat, field). Rebuilt from saved files, free."""
    filings = load_json(FILINGS_JSON, {})
    allowed = {t for t, _ in SAMPLE} | ({t for t, _ in HARD} if include_hard else set())
    recs = {t: r for t, r in filings.items() if t in allowed and not r.get("error")}
    rows = []

    def base(rec, field):
        e = rec["key"][field]
        return {"ticker": rec["ticker"], "group": rec["group"], "field": field,
                "key": e["value"] if e else None, "key_tag": e["tag"] if e else None,
                "key_ambiguous": rec["ambiguous"][field]}

    for rec in recs.values():
        for f in P.FIELDS:
            s = score_item(rec, "A", f, None)
            rows.append({**base(rec, f), "model": "regex", "cell": "A", "repeat": 0, **s})
    if os.path.isdir(RAW_DIR):
        for fn in sorted(os.listdir(RAW_DIR)):
            if not fn.endswith(".json"):
                continue
            rr = load_json(os.path.join(RAW_DIR, fn), None)
            if not rr or rr["ticker"] not in recs:
                continue
            rec = recs[rr["ticker"]]
            for f in P.FIELDS:
                s = score_item(rec, rr["cell"], f, rr)
                rows.append({**base(rec, f), "model": rr["model"], "cell": rr["cell"], "repeat": rr["repeat"], **s})
    return rows


def status():
    filings = load_json(FILINGS_JSON, {})
    ok = [t for t, r in filings.items() if not r.get("error")]
    bad = {t: r["error"] for t, r in filings.items() if r.get("error")}
    print(f"Filings prepared: {len(ok)} ok, {len(bad)} failed")
    for t, e in bad.items():
        print(f"   {t}: {e}")
    nsec = [t for t in ok if not all(filings[t]["sections_found"].values())]
    if nsec:
        print(f"Statements not found in: {', '.join(nsec)}")
    raws = len(os.listdir(RAW_DIR)) if os.path.isdir(RAW_DIR) else 0
    print(f"Model calls saved: {raws}   estimated spend so far: ${spent_so_far():.2f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["prepare", "estimate", "run", "status"])
    ap.add_argument("--models", default=os.environ.get("FC_MODEL", "claude-haiku-5-5"), help="comma-separated model names")
    ap.add_argument("--repeats", type=int, default=1, help="repeat every call this many times")
    ap.add_argument("--cells", default=",".join(ALL_CELLS), help=f"conditions to run, from: {', '.join(ALL_CELLS)}")
    ap.add_argument("--tickers", default="", help="only these tickers, comma-separated")
    ap.add_argument("--budget", type=float, default=5.0, help="stop when estimated spend passes this many dollars")
    ap.add_argument("--hard", action="store_true", help="include the bank hard case (JPM)")
    a = ap.parse_args()
    models = [m.strip() for m in a.models.split(",") if m.strip()]
    cells = [c.strip() for c in a.cells.split(",") if c.strip()]
    bad = [c for c in cells if c not in CELLS]
    if bad:
        sys.exit(f"unknown condition(s): {bad}")
    tickers = [t.strip().upper() for t in a.tickers.split(",") if t.strip()]
    if a.command == "prepare":
        prepare(a.hard)
    elif a.command == "estimate":
        estimate(models, a.repeats, cells, tickers, a.hard)
    elif a.command == "run":
        run(models, a.repeats, cells, tickers, a.budget, a.hard)
    else:
        status()


if __name__ == "__main__":
    main()


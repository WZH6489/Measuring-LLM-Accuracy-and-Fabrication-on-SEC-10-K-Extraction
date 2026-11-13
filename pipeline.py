"""FilingCheck pipeline.

For each company: find the latest 10-K, build an answer key from SEC XBRL data,
then extract the same numbers two ways and score both against the key:
  Condition A: rule/regex baseline (no LLM)
  Condition B: LLM extraction (zero-shot, financial statements only)

Usage:
    python pipeline.py                  # all five companies, regex + LLM
    python pipeline.py AAPL MSFT        # chosen companies
    python pipeline.py --no-llm         # answer keys + regex baseline only (free)
    python pipeline.py --abstain        # LLM may answer null if unsure (condition F)

Needs:  pip install requests beautifulsoup4 anthropic
        export ANTHROPIC_API_KEY="..."   (only for the LLM step)
"""
import argparse
import json
import os
import re
import time
from datetime import date

import requests

UA = {"User-Agent": "William Huang wzh1@sfu.ca"}  # the SEC requires a real contact
MODEL = os.environ.get("FC_MODEL", "claude-haiku-5-5")

COMPANIES = {
    "AAPL": ("Apple", "0000320193"),
    "MSFT": ("Microsoft", "0000789019"),
    "WMT": ("Walmart", "0000104169"),
    "KO": ("Coca-Cola", "0000021344"),
    "JPM": ("JPMorgan Chase", "0000019617"),
}

FIELDS = ["revenue", "net_income", "operating_income", "total_assets"]
FLOW_FIELDS = {"revenue", "net_income", "operating_income"}  # cover a period, not a date

# XBRL tag names to try, in order. Companies tag the same idea differently.
TAGS = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "operating_income": ["OperatingIncomeLoss"],
    "total_assets": ["Assets"],
}


# ---------------------------------------------------------------- SEC access

def sec_get(url):
    time.sleep(0.2)  # stay well under the SEC's 10 requests/second limit
    r = requests.get(url, headers=UA, timeout=60)
    r.raise_for_status()
    return r


def get_latest_10k(cik):
    """Return accession number, main document name, period end and filing date."""
    sub = sec_get(f"https://data.sec.gov/submissions/CIK{cik}.json").json()
    rec = sub["filings"]["recent"]
    for i, form in enumerate(rec["form"]):  # newest first
        if form == "10-K":
            return {
                "accession": rec["accessionNumber"][i],
                "doc": rec["primaryDocument"][i],
                "period_end": rec["reportDate"][i],
                "filed": rec["filingDate"][i],
            }
    raise RuntimeError("no 10-K in the recent filings list")


def download_filing(ticker, cik, info):
    """Download the 10-K HTML once and reuse the saved copy afterwards."""
    os.makedirs("data", exist_ok=True)
    path = f"data/{ticker}_10k.html"
    if not os.path.exists(path):
        url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
               f"{info['accession'].replace('-', '')}/{info['doc']}")
        with open(path, "w", encoding="utf-8") as f:
            f.write(sec_get(url).text)
    return path


def filing_text(path):
    from bs4 import BeautifulSoup
    html = open(path, encoding="utf-8", errors="ignore").read()
    text = BeautifulSoup(html, "html.parser").get_text("\n")
    return re.sub(r"\n\s*\n+", "\n", text)


# ------------------------------------------------------------ finding tables

NUM = re.compile(r"\d{1,3}(?:,\d{3})+")
# Headings must be a line of their own. Without this, prose such as "...recorded in
# our consolidated income statements were as follows:" was mistaken for the statement.
def _loose(phrase):
    """Regex for a phrase that tolerates whitespace/newlines between ANY letters
    (some filings split words like 'INC' / 'OME STATEMENTS' across lines)."""
    return r"\s*".join(re.escape(c) for c in phrase.replace(" ", ""))


_TAIL = r"[^\S\n]*(?:\([^)\n]*\))?[^\S\n]*$"
def _loose(phrase):
    """Regex for a phrase that tolerates whitespace/newlines between ANY letters
    (some filings split words like 'INC' / 'OME STATEMENTS' across lines)."""
    return r"\s*".join(re.escape(c) for c in phrase.replace(" ", ""))


_TAIL = r"[^\S\n]*(?:\([^)\n]*\))?[^\S\n]*$"
INCOME_HEAD = re.compile(
    r"^[^\S\n]*(?:" + _loose("consolidated") + r"\s*)?(?:"
    + _loose("statement") + r"\s*s?\s*" + _loose("of") + r"\s*(?:"
    + "|".join(_loose(w) for w in ("operations", "income", "earnings")) + r")"
    + "|" + _loose("income") + r"\s*" + _loose("statement") + r"\s*s?)" + _TAIL,
    re.I | re.M)
INCOME_MARK = re.compile(r"net\s+(?:income|earnings|loss)", re.I)
BALANCE_HEAD = re.compile(
    r"^[^\S\n]*(?:" + _loose("consolidated") + r"\s*)?" + _loose("balance")
    + r"\s*" + _loose("sheet") + r"\s*s?" + _TAIL, re.I | re.M)
BALANCE_MARK = re.compile(r"total\s+assets", re.I)
UNIT_LINE = re.compile(r"in\s+(?:millions|thousands|billions)", re.I)


def find_section(text, head, mark, length=7000):
    """Find a financial statement by its heading.
    Two passes: first prefer a heading followed within 600 characters by the unit
    line ("In millions..."), which real statements have and table-of-contents
    entries and cross-references do not. If nothing qualifies, take the first
    heading followed by a table-sized block of numbers."""
    candidates = []
    for m in head.finditer(text):
        chunk = text[m.start(): m.start() + length]
        if mark.search(chunk) and len(NUM.findall(chunk)) >= 20:
            candidates.append((m.start(), chunk))
    for start, chunk in candidates:
        if UNIT_LINE.search(text[start: start + 600]):
            return chunk
    return candidates[0][1] if candidates else None


def find_sections(text):
    return {
        "income": find_section(text, INCOME_HEAD, INCOME_MARK),
        "balance": find_section(text, BALANCE_HEAD, BALANCE_MARK),
    }


# --------------------------------------------------------------- answer key

def xbrl_key(cik, accession, period_end):
    """Official values for this exact filing and fiscal period, from SEC XBRL data.
    Returns {field: {"value", "tag", "alternatives"} or None}."""
    facts = sec_get(
        f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    ).json()["facts"].get("us-gaap", {})
    end = date.fromisoformat(period_end)
    key = {}
    for field in FIELDS:
        found = []
        for tag in TAGS[field]:
            for r in facts.get(tag, {}).get("units", {}).get("USD", []):
                if r.get("accn") != accession or r.get("end") != period_end:
                    continue
                if field in FLOW_FIELDS:
                    if "start" not in r:
                        continue
                    days = (end - date.fromisoformat(r["start"])).days
                    if not 340 <= days <= 380:  # keep full-year values only
                        continue
                if all(tag != t for t, _ in found):
                    found.append((tag, r["val"]))
        if found:
            key[field] = {"value": found[0][1], "tag": found[0][0], "alternatives": found[1:]}
        else:
            key[field] = None
    return key


# --------------------------------------------------- condition A: regex rules

LABELS = {
    "revenue": r"(?:total\s+net\s+sales|total\s+net\s+revenues?|total\s+revenues?|net\s+sales|net\s+revenues?|revenues?)",
    "net_income": r"(?:net\s+income|net\s+earnings|net\s+loss)",
    "operating_income": r"(?:operating\s+income|income\s+from\s+operations|operating\s+profit)",
    "total_assets": r"total\s+assets",
}
VALUE = r"[\s$]*\(?\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)"


def detect_scale(chunk):
    m = re.search(r"in\s+(millions|thousands|billions)", chunk, re.I)
    if not m:
        return 1
    return {"millions": 1_000_000, "thousands": 1_000, "billions": 1_000_000_000}[m.group(1).lower()]


def regex_extract(sections):
    """Baseline: first number after a known row label. Deliberately naive."""
    out = {}
    for field in FIELDS:
        chunk = sections["balance"] if field == "total_assets" else sections["income"]
        out[field] = None
        if not chunk:
            continue
        m = re.search(LABELS[field] + VALUE, chunk, re.I)
        if m:
            out[field] = float(m.group(1).replace(",", "")) * detect_scale(chunk)
    return out


# ---------------------------------------------------- condition B: LLM extract

def to_number(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.replace("$", "").replace(",", "").strip()
        neg = s.startswith("(") and s.endswith(")")
        s = s.strip("()")
        try:
            return -float(s) if neg else float(s)
        except ValueError:
            return None
    return None


def llm_extract(sections_text, model=MODEL, abstain=False):
    """Returns (parsed dict or None, raw reply text, (input tokens, output tokens))."""
    import anthropic
    extra = ("If a figure is not reported in the text, set that key to null. "
             "Do not guess.\n") if abstain else ""
    prompt = f"""You are extracting numbers from the financial statements of a 10-K filing.
Return JSON only, no other text, with these keys: {", ".join(FIELDS)}.
For each key give an object with: value (a number in full US dollars, applying any scale stated in the table header such as "in millions"), unit, period (the fiscal period it covers), and quote (a verbatim quote from the text that contains the number).
Use the most recent fiscal year only.
{extra}
{sections_text}"""
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    resp = client.messages.create(
        model=model, max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = "".join(b.text for b in resp.content if b.type == "text")
    usage = (resp.usage.input_tokens, resp.usage.output_tokens)
    try:
        parsed = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
    except ValueError:
        parsed = None
    return parsed, raw, usage


# ------------------------------------------------------------------ scoring

def norm(s):
    return " ".join(str(s).split())


def number_in_text(value, text):
    """Is this number printed in the text, at any common scale?"""
    for scale in (1, 1_000, 1_000_000):
        n = abs(value) / scale
        if abs(n - round(n)) < 1e-9 and f"{int(round(n)):,}" in text:
            return True
    return False


def classify(got, key_value, text):
    """match / wrong_scale / not_in_text (possible fabrication) / wrong_value /
    missing / no_key"""
    if key_value is None:
        return "answered_no_key" if got is not None else "no_key"
    if got is None:
        return "missing"
    if key_value != 0 and abs(got - key_value) / abs(key_value) <= 0.01:
        return "match"
    if key_value != 0 and got != 0:
        ratio = abs(got / key_value)
        for f in (1e3, 1e6, 1e-3, 1e-6):
            if abs(ratio / f - 1) < 0.01:
                return "wrong_scale"
    if not number_in_text(got, text):
        return "not_in_text"
    return "wrong_value"


# --------------------------------------------------------------- main loop

def run_company(ticker, use_llm, abstain):
    name, cik = COMPANIES[ticker]
    info = get_latest_10k(cik)
    path = download_filing(ticker, cik, info)
    text = filing_text(path)
    sections = find_sections(text)
    key = xbrl_key(cik, info["accession"], info["period_end"])
    section_text = "\n\n".join(s for s in sections.values() if s)

    print(f"\n=== {ticker} ({name}) | 10-K for period ending {info['period_end']} | {info['accession']}")
    for k, v in sections.items():
        if not v:
            print(f"  ! could not find the {k} statement in the filing text")

    a = regex_extract(sections) if section_text else {f: None for f in FIELDS}
    b, raw, usage = ({f: None for f in FIELDS}, "", (0, 0))
    quotes = {}
    if use_llm and section_text:
        parsed, raw, usage = llm_extract(section_text, abstain=abstain)
        os.makedirs("results/raw", exist_ok=True)
        tag = "abstain" if abstain else "zeroshot"
        with open(f"results/raw/{ticker}_{MODEL}_{tag}.txt", "w") as f:
            f.write(raw)
        if parsed is None:
            print("  ! model reply was not valid JSON (saved in results/raw/)")
        else:
            for field in FIELDS:
                item = parsed.get(field)
                if isinstance(item, dict):
                    b[field] = to_number(item.get("value"))
                    quotes[field] = item.get("quote", "")
        print(f"  tokens: {usage[0]} in, {usage[1]} out")

    rows = []
    norm_text = norm(section_text)
    for field in FIELDS:
        kv = key[field]["value"] if key[field] else None
        src = "balance" if field == "total_assets" else "income"
        no_sec = sections[src] is None and kv is not None
        row = {
            "ticker": ticker, "field": field, "period_end": info["period_end"],
            "accession": info["accession"], "key": kv,
            "key_tag": key[field]["tag"] if key[field] else None,
            "regex_value": a[field], "regex_class": "no_section" if no_sec else classify(a[field], kv, section_text),
            "llm_value": b[field],
            "llm_class": ("no_section" if no_sec else classify(b[field], kv, section_text)) if use_llm else "not_run",
            "quote_found": (norm(quotes[field]) in norm_text) if quotes.get(field) else None,
            "model": MODEL if use_llm else None,
        }
        rows.append(row)
        flag = ""
        if key[field] and key[field]["alternatives"]:
            alts = {v for _, v in key[field]["alternatives"] if v != kv}
            if alts:
                flag = "  (!) other XBRL tags disagree: check by hand"
        keystr = f"{kv:>18,.0f}" if kv is not None else f"{'(no XBRL value)':>18}"
        print(f"  {field:17} key {keystr} | A: {row['regex_class']:12} | B: {row['llm_class']:12}{flag}")
    return rows


def summarize(rows, use_llm):
    print("\n=== SUMMARY (items that have an XBRL answer key)")
    scored = [r for r in rows if r["key"] is not None]
    if not scored:
        print("  nothing to score (no XBRL keys found)")
        return
    conds = [("A regex", "regex_class")] + ([("B LLM", "llm_class")] if use_llm else [])
    for label, col in conds:
        counts = {}
        usable = [r for r in scored if r[col] != "no_section"]
        for r in usable:
            counts[r[col]] = counts.get(r[col], 0) + 1
        n = len(usable)
        nosec = len(scored) - n
        m = counts.get("match", 0)
        print(f"  {label:8} {m}/{n} match ({100 * m / n:.0f}%)  errors: "
              f"{ {k: v for k, v in counts.items() if k != 'match'} }"
              + (f"  [{nosec} items excluded: statement not found]" if nosec else ""))
    skipped = [(r['ticker'], r['field']) for r in rows if r["key"] is None]
    if skipped:
        print(f"  no XBRL key, not scored: {skipped}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="*", default=list(COMPANIES))
    ap.add_argument("--no-llm", action="store_true", help="skip the paid model calls")
    ap.add_argument("--abstain", action="store_true", help="allow null answers (condition F)")
    args = ap.parse_args()

    rows = []
    for t in args.tickers:
        try:
            rows += run_company(t.upper(), not args.no_llm, args.abstain)
        except Exception as e:  # one bad company should not stop the rest
            print(f"\n=== {t}: FAILED ({type(e).__name__}: {e})")
    if rows:
        os.makedirs("results", exist_ok=True)
        with open("results/results.json", "w") as f:
            json.dump(rows, f, indent=2)
        summarize(rows, not args.no_llm)
        print("\nSaved results/results.json")


if __name__ == "__main__":
    main()

"""Turn the saved study results into tables, confidence intervals and an audit sample. Free: no API calls.

    python analyze_study.py                       # main sample (60 filings)
    python analyze_study.py --exclude-ambiguous   # drop items whose answer key is uncertain
    python analyze_study.py --hard                # also include the bank hard case
    python analyze_study.py --audit               # write a sample for you to check the answer key by hand

Confidence intervals come from resampling FILINGS with replacement (2,000 times), because the
four numbers from one filing are not independent of each other.
"""
import argparse
import csv
import json
import math
import os
import random
from collections import Counter, defaultdict
from datetime import datetime, timezone

import study

OUT = study.STUDY_DIR
ANSWERED = {"match", "wrong_scale", "wrong_value", "not_in_text"}
SHOWN = ["match", "wrong_scale", "wrong_value", "not_in_text", "abstained", "missing", "bad_json"]
LABEL = {"A": "Rule/regex baseline (no model)", **{k: v["label"] for k, v in study.CELLS.items()}}
ORDER = ["A"] + study.ALL_CELLS
BOOT = 2000


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def collect(rows, exclude_ambiguous=False):
    """{(model, cell): {ticker: [item, ...]}}, an item being the list of classes across repeats.
    Only items with an SEC answer key and a located statement are scored."""
    tmp = defaultdict(list)
    for r in rows:
        tmp[(r["model"], r["cell"], r["ticker"], r["field"])].append(r)
    out = defaultdict(lambda: defaultdict(list))
    for (model, cell, ticker, field), rs in tmp.items():
        if rs[0]["key"] is None or rs[0]["cls"] == "no_section":
            continue
        if exclude_ambiguous and rs[0]["key_ambiguous"]:
            continue
        out[(model, cell)][ticker].append({"field": field, "group": rs[0]["group"], "cls": [x["cls"] for x in rs],
                                           "quote_found": [x["quote_found"] for x in rs]})
    return out


def stat(items, kind):
    """One statistic over a list of items. Returns None when undefined."""
    if not items:
        return None
    frac = lambda it, f: sum(1 for c in it["cls"] if f(c)) / len(it["cls"])
    if kind == "accuracy":
        return sum(frac(i, lambda c: c == "match") for i in items) / len(items)
    if kind == "answer_rate":
        return sum(frac(i, lambda c: c in ANSWERED) for i in items) / len(items)
    ans = sum(frac(i, lambda c: c in ANSWERED) for i in items)
    if ans == 0:
        return None
    if kind == "precision":
        return sum(frac(i, lambda c: c == "match") for i in items) / ans
    if kind == "fabrication":
        return sum(frac(i, lambda c: c == "not_in_text") for i in items) / ans
    if kind == "wrong_value":
        return sum(frac(i, lambda c: c == "wrong_value") for i in items) / ans
    raise ValueError(kind)


def boot_ci(by_ticker, kind, seed=0):
    tickers = list(by_ticker)
    pt = stat([i for t in tickers for i in by_ticker[t]], kind)
    if pt is None or len(tickers) < 2:
        return pt, None, None
    rnd, vals = random.Random(seed), []
    for _ in range(BOOT):
        pick = [rnd.choice(tickers) for _ in tickers]
        v = stat([i for t in pick for i in by_ticker[t]], kind)
        if v is not None:
            vals.append(v)
    vals.sort()
    return pt, vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals)) - 1]


def paired_diff(a, b, seed=1):
    """accuracy(a) - accuracy(b) over the filings both were run on, with a bootstrap interval."""
    common = [t for t in a if t in b]
    if len(common) < 2:
        return None
    key = lambda it: it["field"]
    diffs = {}
    for t in common:
        bm = {key(i): i for i in b[t]}
        diffs[t] = [stat([i], "accuracy") - stat([bm[key(i)]], "accuracy") for i in a[t] if key(i) in bm]
    allv = [v for t in common for v in diffs[t]]
    if not allv:
        return None
    pt = sum(allv) / len(allv)
    rnd, vals = random.Random(seed), []
    for _ in range(BOOT):
        pick = [x for t in (rnd.choice(common) for _ in common) for x in diffs[t]]
        if pick:
            vals.append(sum(pick) / len(pick))
    vals.sort()
    return pt, vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals)) - 1], len(common)


def summarize(rows, exclude_ambiguous=False):
    data = collect(rows, exclude_ambiguous)
    cells = []
    for (model, cell) in sorted(data, key=lambda k: (k[0] == "regex", k[0], ORDER.index(k[1]))):
        by_t = data[(model, cell)]
        items = [i for t in by_t for i in by_t[t]]
        classes = Counter(c for i in items for c in i["cls"])
        total = sum(classes.values())
        entry = {"model": model, "cell": cell, "label": LABEL[cell], "n_items": len(items), "n_filings": len(by_t),
                 "repeats": max(len(i["cls"]) for i in items)}
        for kind in ("accuracy", "answer_rate", "precision", "fabrication", "wrong_value"):
            pt, lo, hi = boot_ci(by_t, kind)
            entry[kind] = [pt, lo, hi]
        entry["classes"] = {c: classes.get(c, 0) / total for c in SHOWN}
        for dim, name in (("field", "by_field"), ("group", "by_group")):
            groups = defaultdict(list)
            for i in items:
                groups[i[dim]].append(i)
            entry[name] = {g: stat(v, "accuracy") for g, v in sorted(groups.items())}
        multi = [i for i in items if len(i["cls"]) > 1]
        entry["consistency"] = (sum(1 for i in multi if len(set(i["cls"])) == 1) / len(multi)) if multi else None
        qs = [q for i in items for q in i["quote_found"] if q is not None]
        entry["quote_found_rate"] = (sum(qs) / len(qs)) if qs else None
        cells.append(entry)
    paired = []
    for model in sorted({m for m, _ in data if m != "regex"}):
        for x, y, why in (("B", "A", "model vs regex baseline"), ("C", "B", "few-shot vs zero-shot"),
                          ("D", "B", "retrieval vs full statements"), ("B+F", "B", "abstention vs none (zero-shot)"),
                          ("E", "B", "closed-book vs reading the statements")):
            ax = data.get((model, x)) or (data.get(("regex", "A")) if x == "A" else None)
            by = data.get((model, y)) or data.get(("regex", "A")) if y == "A" else data.get((model, y))
            if ax and by:
                d = paired_diff(ax, by)
                if d:
                    paired.append({"model": model, "a": x, "b": y, "what": why, "diff": d[0], "lo": d[1], "hi": d[2], "filings": d[3]})
    # pipeline coverage and suspicious answers, counted over ALL rows
    cov = defaultdict(Counter)
    for r in rows:
        if r["cell"] in ("A", "B"):
            cov[r["cell"]]["items"] += 1
            if r["cls"] == "no_section":
                cov[r["cell"]]["no_section"] += 1
        if r["key"] is None and r["cls"] == "answered_no_key":
            cov[(r["model"], r["cell"])]["answered_no_key"] += 1
    coverage = {"statement_finder": {"items": cov["B"]["items"], "no_section": cov["B"]["no_section"]},
                "answered_without_sec_value": {f"{m} {c}": v["answered_no_key"] for (m, c), v in
                                               ((k, v) for k, v in cov.items() if isinstance(k, tuple)) if v["answered_no_key"]},
                "ambiguous_keys": sum(1 for r in rows if r["cell"] == "A" and r["key_ambiguous"])}
    return {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "exclude_ambiguous": exclude_ambiguous,
            "n_filings": len({r["ticker"] for r in rows}), "cells": cells, "paired": paired, "coverage": coverage}


def pct(v):
    return "   n/a" if v is None else f"{100 * v:5.1f}%"


def ci(e, k):
    pt, lo, hi = e[k]
    return "      n/a      " if pt is None else (f"{100 * pt:5.1f}% " + (f"[{100 * lo:4.0f},{100 * hi:4.0f}]" if lo is not None else "[   n/a  ]"))


def print_report(s):
    print(f"\n{s['n_filings']} filings"
          + ("  (items with an uncertain answer key excluded)" if s["exclude_ambiguous"] else "") + "\n")
    last = None
    for e in s["cells"]:
        if e["model"] != last:
            last = e["model"]
            print(f"--- {last}" + (f"   ({e['repeats']} repeat(s))" if last != "regex" else ""))
            print(f"{'cond':5}{'what the model sees':36}{'items':>6}  {'accuracy [95% CI]':17} {'answered':>8} {'precision':>9} {'fabricated':>10}")
        print(f"{e['cell']:5}{e['label']:36}{e['n_items']:>6}  {ci(e, 'accuracy'):17} {pct(e['answer_rate'][0]):>8} "
              f"{pct(e['precision'][0]):>9} {pct(e['fabrication'][0]):>10}")
    if s["paired"]:
        print("\nPaired differences in accuracy (same filings, same fields), with 95% intervals:")
        for p in s["paired"]:
            print(f"  {p['model']:20} {p['a']:>4} minus {p['b']:<4} {100 * p['diff']:+6.1f} points "
                  f"[{100 * p['lo']:+.0f}, {100 * p['hi']:+.0f}]   ({p['what']}, {p['filings']} filings)")
    c = s["coverage"]
    f = c["statement_finder"]
    print(f"\nPipeline coverage: the statement finder located the needed statement for "
          f"{f['items'] - f['no_section']} of {f['items']} items; {f['no_section']} were excluded as 'no_section'.")
    print(f"Items with an ambiguous answer key (rerun with --exclude-ambiguous to drop them): {c['ambiguous_keys']}")
    if c["answered_without_sec_value"]:
        print("Answers given where the SEC has no value to check against:", c["answered_without_sec_value"])


def write_outputs(s):
    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/summary.json", "w") as f:
        json.dump(s, f, indent=1)
    with open(f"{OUT}/cell_summary.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["model", "condition", "label", "items", "filings", "repeats", "accuracy", "acc_lo", "acc_hi", "answer_rate",
                    "precision", "fabrication_rate", "wrong_value_rate", "consistency", "quote_found_rate"] + SHOWN)
        for e in s["cells"]:
            w.writerow([e["model"], e["cell"], e["label"], e["n_items"], e["n_filings"], e["repeats"], *e["accuracy"], e["answer_rate"][0],
                        e["precision"][0], e["fabrication"][0], e["wrong_value"][0], e["consistency"], e["quote_found_rate"]]
                       + [round(e["classes"][c], 4) for c in SHOWN])
    print(f"\nWrote {OUT}/summary.json and {OUT}/cell_summary.csv")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("(pip install matplotlib to also get charts)")
        return
    for model in sorted({e["model"] for e in s["cells"] if e["model"] != "regex"}):
        es = [e for e in s["cells"] if e["model"] in (model, "regex")]
        es.sort(key=lambda e: ORDER.index(e["cell"]))
        fig, ax = plt.subplots(figsize=(9, 4.2))
        xs = range(len(es))
        acc = [100 * e["accuracy"][0] for e in es]
        err = [[100 * (e["accuracy"][0] - (e["accuracy"][1] if e["accuracy"][1] is not None else e["accuracy"][0])) for e in es],
               [100 * ((e["accuracy"][2] if e["accuracy"][2] is not None else e["accuracy"][0]) - e["accuracy"][0]) for e in es]]
        ax.bar(xs, acc, yerr=err, capsize=3, color=["#999" if e["cell"] == "A" else "#3B6FB6" for e in es])
        ax.set_xticks(list(xs))
        ax.set_xticklabels([e["cell"] for e in es])
        ax.set_ylabel("Accuracy (%)")
        ax.set_ylim(0, 100)
        ax.set_title(f"Extraction accuracy by condition: {model} (95% CI, resampled by filing)")
        fig.tight_layout()
        fig.savefig(f"{OUT}/fig_accuracy_{model}.png", dpi=160)
        fig2, ax2 = plt.subplots(figsize=(9, 4.2))
        bottom = [0.0] * len(es)
        for cls, col in zip(SHOWN, ["#3B9B6B", "#E0A030", "#D9732B", "#C0392B", "#8E8E93", "#B8B8BD", "#5A2A82"]):
            vals = [100 * e["classes"][cls] for e in es]
            ax2.bar(xs, vals, bottom=bottom, label=cls, color=col)
            bottom = [b + v for b, v in zip(bottom, vals)]
        ax2.set_xticks(list(xs))
        ax2.set_xticklabels([e["cell"] for e in es])
        ax2.set_ylabel("Share of items (%)")
        ax2.legend(fontsize=7, ncol=4, loc="upper center", bbox_to_anchor=(0.5, -0.1))
        ax2.set_title(f"What happened to each item: {model}")
        fig2.tight_layout()
        fig2.savefig(f"{OUT}/fig_errors_{model}.png", dpi=160)
        print(f"Wrote charts for {model} in {OUT}/")


# -------------------------------------------------------------------- audit

def write_audit(rows, n_random=20, n_targeted=10):
    """A sample of items for YOU to check against the filing by hand. Fill in the last three columns."""
    filings = study.load_json(study.FILINGS_JSON, {})
    keyed = {}
    for r in rows:
        if r["cell"] == "A" and r["key"] is not None:
            keyed[(r["ticker"], r["field"])] = r
    b_model = next((r["model"] for r in rows if r["cell"] == "B"), None)
    b_cls = {(r["ticker"], r["field"]): r for r in rows if r["cell"] == "B" and r["model"] == b_model and r["repeat"] == 1}
    rnd = random.Random(2026)
    keys = sorted(keyed)
    rand = rnd.sample(keys, min(n_random, len(keys)))
    disagree = [k for k in keys if k not in rand and k in b_cls and b_cls[k]["cls"] in ("wrong_value", "wrong_scale", "not_in_text")]
    targeted = rnd.sample(disagree, min(n_targeted, len(disagree)))
    path = f"{OUT}/audit_sample.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "why_selected", "ticker", "company", "field", "sec_value", "sec_tag", "key_ambiguous", "model_answer",
                    "filing_url", "YOU_fill:value_printed_in_filing", "YOU_fill:sec_value_correct(Y/N)", "YOU_fill:notes"])
        for i, k in enumerate(rand + targeted, 1):
            r, rec = keyed[k], filings[k[0]]
            info = rec["info"]
            url = (f"https://www.sec.gov/Archives/edgar/data/{int(rec['cik'])}/{info['accession'].replace('-', '')}/{info['doc']}")
            ans = b_cls.get(k, {}).get("value")
            w.writerow([i, "random" if k in rand else "model disagreed", k[0], rec["company"], k[1], r["key"], r["key_tag"],
                        r["key_ambiguous"], ans, url, "", "", ""])
    print(f"Wrote {path}: {len(rand)} random items (an unbiased estimate of how often the SEC key is wrong) "
          f"plus {len(targeted)} items where the model disagreed with the key.")
    print("Open each filing, find the number, and fill in the last three columns. Then run:  python analyze_study.py --audit-score")


def audit_result():
    """Result of the hand check, or None if it has not been filled in."""
    path = f"{OUT}/audit_sample.csv"
    if not os.path.exists(path):
        return None
    col = "YOU_fill:sec_value_correct(Y/N)"
    with open(path) as f:
        rows = list(csv.DictReader(f))
    out = {}
    for why in ("random", "model disagreed"):
        done = [r for r in rows if r["why_selected"] == why and r.get(col, "").strip().upper() in ("Y", "N")]
        if done:
            bad = sum(1 for r in done if r[col].strip().upper() == "N")
            lo, hi = wilson(bad, len(done))
            out[why] = {"checked": len(done), "key_wrong": bad, "rate": bad / len(done), "lo": lo, "hi": hi}
    return out or None


def score_audit():
    if not os.path.exists(f"{OUT}/audit_sample.csv"):
        print("No audit sample yet. Run:  python analyze_study.py --audit")
        return
    res = audit_result() or {}
    for why in ("random", "model disagreed"):
        r = res.get(why)
        if not r:
            print(f"{why}: nothing filled in yet")
            continue
        print(f"{why}: the SEC value was wrong for {r['key_wrong']} of {r['checked']} checked items "
              f"({100 * r['rate']:.0f}%, 95% interval {100 * r['lo']:.0f}% to {100 * r['hi']:.0f}%)")
    print("Report the 'random' figure as the estimated error rate of the answer key.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exclude-ambiguous", action="store_true")
    ap.add_argument("--hard", action="store_true", help="include the bank hard case")
    ap.add_argument("--audit", action="store_true", help="write the hand-check sample")
    ap.add_argument("--audit-score", action="store_true", help="score your hand-check")
    a = ap.parse_args()
    if a.audit_score:
        return score_audit()
    rows = study.build_rows(include_hard=a.hard)
    if not rows:
        raise SystemExit("No results yet. Run:  python study.py prepare   then   python study.py run")
    if a.audit:
        return write_audit(rows)
    s_all, s_clean = summarize(rows, False), summarize(rows, True)
    audit = audit_result()
    s_all["audit"] = s_clean["audit"] = audit
    s = s_clean if a.exclude_ambiguous else s_all
    print_report(s)
    write_outputs(s)
    with open(f"{OUT}/summary.json", "w") as f:
        json.dump(s_all, f, indent=1)
    with open(f"{OUT}/summary_noamb.json", "w") as f:
        json.dump(s_clean, f, indent=1)


if __name__ == "__main__":
    main()



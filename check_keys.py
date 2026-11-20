"""Automated check of the SEC answer key. Free: no API calls, no internet.

For every item (company x field) that has an XBRL answer key it asks three questions of the filing text:
  1. Is the key number printed in the income statement / balance sheet we located?
  2. Is it printed anywhere in the filing?
  3. Does the line it sits on (or the line above) carry the label we expect, e.g. "net income"?
A key that fails these is suspect: the XBRL tag may point at a different line than the field we mean.

This is an AUTOMATED consistency check, not a hand audit. It can catch keys that are missing from the text or sit
on the wrong line. It cannot catch a key that is wrong but happens to match a printed number with the right label.

    python check_keys.py          # writes results/study/key_check.csv and prints a summary
"""
import csv
import re
import sys

import pipeline as P
import study

OUT = f"{study.STUDY_DIR}/key_check.csv"


def scaled_strings(value):
    """The ways this number can be printed: in dollars, thousands or millions, with separators and optional 1 decimal."""
    out = []
    for scale in (1, 1_000, 1_000_000):
        n = abs(value) / scale
        if abs(n - round(n)) < 1e-9:
            out.append(f"{int(round(n)):,}")
        elif scale > 1 and abs(n * 10 - round(n * 10)) < 1e-6:
            out.append(f"{n:,.1f}")
    return out


def printed(value, text):
    return any(s in text for s in scaled_strings(value))


def label_near(text, value, field):
    """Is the number printed shortly after a label that matches the field (e.g. 'Total assets ... 404,548')?"""
    pat = re.compile(P.LABELS[field], re.I)
    for s in scaled_strings(value):
        start = 0
        while True:
            pos = text.find(s, start)
            if pos < 0:
                break
            if pat.search(text[max(0, pos - 120):pos]):
                return True
            start = pos + 1
    return False


def main():
    recs = study.usable_filings()
    if not recs:
        sys.exit("Nothing prepared yet. Run:  python study.py prepare")
    rows = []
    for rec in recs:
        text = study.get_text(rec["ticker"])
        secs = P.find_sections(text)
        statements = "\n\n".join(s for s in secs.values() if s)
        for field in P.FIELDS:
            key = rec["key"].get(field)
            row = {"ticker": rec["ticker"], "group": rec["group"], "field": field,
                   "ambiguous": rec["ambiguous"].get(field, False)}
            if not key:
                row.update(key="", tag="", in_statements="", in_filing="", label_ok="", verdict="no_key")
            elif not statements:
                row.update(key=key["value"], tag=key["tag"], in_statements="", in_filing=printed(key["value"], text),
                           label_ok="", verdict="no_section")
            else:
                v = key["value"]
                ins, inf = printed(v, statements), printed(v, text)
                lab = label_near(statements, v, field) if ins else False
                verdict = "ok" if ins and lab else "label_mismatch" if ins else "not_in_statements" if inf else "not_in_filing"
                row.update(key=v, tag=key["tag"], in_statements=ins, in_filing=inf, label_ok=lab, verdict=verdict)
            rows.append(row)
    cols = ["ticker", "group", "field", "key", "tag", "ambiguous", "in_statements", "in_filing", "label_ok", "verdict"]
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    checked = [r for r in rows if r["verdict"] not in ("no_key", "no_section")]
    n = len(checked) or 1
    print(f"{len(recs)} filings, {len(rows)} items; {len(checked)} have both a key and a located statement.\n")
    for v in ("ok", "label_mismatch", "not_in_statements", "not_in_filing"):
        c = sum(r["verdict"] == v for r in checked)
        print(f"  {v:<18}{c:>5}  ({100 * c / n:.1f}%)")
    for flag, name in ((True, "ambiguous keys"), (False, "unambiguous keys")):
        sub = [r for r in checked if r["ambiguous"] == flag]
        if sub:
            bad = sum(r["verdict"] != "ok" for r in sub)
            print(f"\n  {name}: {len(sub)} items, {bad} suspect ({100 * bad / len(sub):.1f}%)")
    print(f"  no key at all: {sum(r['verdict'] == 'no_key' for r in rows)}   statements not found: {sum(r['verdict'] == 'no_section' for r in rows)}")
    sus = [r for r in checked if r["verdict"] != "ok"]
    if sus:
        print("\nSuspect items (hand-check these first):")
        for r in sus[:40]:
            print(f"  {r['ticker']:<6}{r['field']:<18}{r['verdict']:<19}key={r['key']:,.0f}  tag={r['tag']}")
        if len(sus) > 40:
            print(f"  ... and {len(sus) - 40} more in {OUT}")
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()

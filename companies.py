"""The study sample for FilingCheck.

60 companies in 10 groups of 6, chosen to vary by sector and by filing length
(the last group is deliberately smaller companies, whose 10-Ks are shorter).
Tickers are looked up on SEC EDGAR at run time, so a ticker that has changed or
a company that fails to download is logged and skipped, not silently dropped.

Banks are kept out of the main sample on purpose: they report no "operating
income" or standard "revenue", so the SEC answer key is undefined or ambiguous
for them. JPMorgan is kept as a separately reported hard case.
"""

SAMPLE = [
    # (ticker, group)
    ("AAPL", "Technology"), ("MSFT", "Technology"), ("NVDA", "Technology"),
    ("ORCL", "Technology"), ("CSCO", "Technology"), ("TXN", "Technology"),

    ("JNJ", "Healthcare"), ("PFE", "Healthcare"), ("MRK", "Healthcare"),
    ("ABT", "Healthcare"), ("TMO", "Healthcare"), ("AMGN", "Healthcare"),

    ("WMT", "Consumer staples"), ("KO", "Consumer staples"), ("PEP", "Consumer staples"),
    ("PG", "Consumer staples"), ("COST", "Consumer staples"), ("CL", "Consumer staples"),

    ("AMZN", "Consumer discretionary"), ("HD", "Consumer discretionary"), ("NKE", "Consumer discretionary"),
    ("MCD", "Consumer discretionary"), ("SBUX", "Consumer discretionary"), ("TJX", "Consumer discretionary"),

    ("CAT", "Industrials"), ("HON", "Industrials"), ("UPS", "Industrials"),
    ("GE", "Industrials"), ("DE", "Industrials"), ("LMT", "Industrials"),

    ("XOM", "Energy"), ("CVX", "Energy"), ("COP", "Energy"),
    ("SLB", "Energy"), ("EOG", "Energy"), ("MPC", "Energy"),

    ("NEE", "Utilities"), ("DUK", "Utilities"), ("SO", "Utilities"),
    ("D", "Utilities"), ("AEP", "Utilities"), ("EXC", "Utilities"),

    ("META", "Communication"), ("NFLX", "Communication"), ("DIS", "Communication"),
    ("CMCSA", "Communication"), ("T", "Communication"), ("VZ", "Communication"),

    ("LIN", "Materials"), ("APD", "Materials"), ("SHW", "Materials"),
    ("ECL", "Materials"), ("NUE", "Materials"), ("FCX", "Materials"),

    ("CALM", "Smaller companies"), ("COKE", "Smaller companies"), ("WING", "Smaller companies"),
    ("BOOT", "Smaller companies"), ("HLIT", "Smaller companies"), ("SXI", "Smaller companies"),
]

# Reported separately, never mixed into the main numbers.
HARD = [("JPM", "Banks (hard case)")]

# Few-shot examples (condition C) come from these filings, which are NOT in the
# test sample. The first two that download and have a complete answer key are used.
HELD_OUT = ["KR", "LOW", "ITW", "TGT"]









































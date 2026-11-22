# Measuring LLM Accuracy and Fabrication on SEC 10-K Extraction

**FilingCheck** · CMPT 218 Final Project, Simon Fraser University (Fall 2026)

**Team:** William Huang, Vincent Zhao, James Park

![FilingCheck background](static/bg.jpg)

Large language models are increasingly used to read long financial documents, but they can misread a table or invent a figure that is not in the filing. This project measures how reliably an LLM pulls four headline numbers (revenue, net income, operating income and total assets) out of U.S. SEC 10-K filings, and what kind of mistake it makes when it is wrong.

The SEC publishes structured XBRL data for every filing, so we use it as an automatic answer key: no hand labelling is needed to score the model.

The project has two parts:

- **The study:** a controlled experiment across 59 filings, 8 prompting conditions and a rule-based baseline.
- **FilingCheck, the app:** a Streamlit site where you type a company and watch the model read its latest 10-K and check every number against the SEC. A second page presents the study's method and results.

## Key findings

Model: Claude Haiku 5.5, 3 repeats per item. Accuracy is the share of items within 1% of the SEC value; 95% confidence intervals come from resampling filings.

| Condition | What the model sees | Accuracy | Fabricated |
|---|---|---|---|
| A | Rule/regex baseline (no model) | 65.8% | 0.0% |
| B | Zero-shot, financial statements only | 92.6% | 0.5% |
| C | Few-shot (2 examples from held-out filings) | **96.0%** | 0.3% |
| D | Retrieval (BM25 top passages) | 87.2% | 0.5% |
| E | Closed-book (company and year only) | 17.3% | **64.4%** |
| B+F | Zero-shot, may answer null | 92.4% | 0.2% |
| C+F | Few-shot, may answer null | 96.0% | 0.0% |
| D+F | Retrieval, may answer null | 83.0% | 0.0% |
| E+F | Closed-book, may answer null | 0.0% (abstains on every item) | — |

"Fabricated" means the returned number appears nowhere in the filing text. Rates are among answered items.

Paired differences (same filings, 95% CI):

- **Model vs. regex:** zero-shot beats the baseline by +26.7 points [17.5, 36.5].
- **Few-shot vs. zero-shot:** +3.5 points [0.5, 6.7]. This gain disappears once ambiguous answer keys are removed.
- **Retrieval vs. full statements:** −5.4 points [−10.4, −1.5]. Retrieval sometimes misses the statement the number is on.
- **Closed-book vs. reading:** −75.4 points [−81.7, −69.2]. Without the document, the model mostly invents numbers.
- **Abstention:** allowing "null" removes the few fabrications that remain, at little or no cost to accuracy. In closed-book mode the model abstains on everything.

With the 34 ambiguous answer keys excluded, zero-shot accuracy rises to 97.6% and the regex baseline to 74.4%.

## Method

**Sample.** 60 large U.S. companies in 10 groups of 6, varied by sector and filing length (the last group is smaller companies with shorter 10-Ks). Each company's most recent 10-K is downloaded from SEC EDGAR; 59 filings downloaded and parsed. Banks are excluded from the main sample because they do not report standard revenue or operating income; JPMorgan is kept as a separate hard case. Few-shot examples come from held-out filings (KR, LOW, ITW, TGT) that are not in the test set.

**Answer key.** Values come from the SEC `companyfacts` XBRL API, using only facts from that exact 10-K (matching accession number) and fiscal period. Flow items must cover 340 to 380 days. If alternative XBRL tags disagree by more than 0.1%, the item is marked ambiguous. `check_keys.py` also confirms automatically that each key value is printed in the filing with the expected label.

**Conditions.** A is a regex baseline; B to E are LLM conditions that differ in what the model sees; the "+F" variants allow the model to answer null. Retrieval (D) uses BM25 over 1,500-character chunks, keeping the top 3 per field. Every model reply is JSON with the value, unit, period and a verbatim evidence quote.

**Scoring.** Each answer gets exactly one label:

| Label | Meaning |
|---|---|
| match | within 1% of the SEC value |
| wrong_scale | off by exactly 1,000× or 1,000,000× |
| wrong_value | a number that appears in the filing but is not the SEC value (wrong line, period or column) |
| not_in_text | a number that appears nowhere in the filing: fabricated |
| abstained | answered null when allowed to |
| missing / bad_json | no answer, or an unparseable reply |

Confidence intervals use a filing-level bootstrap (2,000 resamples), because the four numbers from one filing are not independent.

**Limits.** One model; four fields; large U.S. filers only; XBRL keys can themselves be ambiguous. Full discussion is in the report and on the app's Study page.

## Repository layout

```
.
├── app.py               # FilingCheck Streamlit app: home page (live check) and navigation
├── ui_pages.py          # the Study page: method, results, findings, appendix
├── glass.py             # liquid-glass refraction effect for the UI
├── pipeline.py          # find the 10-K, build the XBRL answer key, extract and score
├── study.py             # the full experiment runner (prepare / estimate / run / status)
├── analyze_study.py     # tables, confidence intervals and the audit sample
├── check_keys.py        # automated check of the SEC answer key against the filing text
├── companies.py         # the 60-company sample, hard case and held-out few-shot filings
├── step2_xbrl.py        # early single-company scripts (Apple) that the pipeline grew from
├── step3_filing.py
├── step4_extract.py
├── results/study/       # summaries, per-call raw replies, prompts, figures
├── static/bg.jpg        # site background
├── tools/make_background.py   # regenerates the background (also 1080p/4K versions for slides)
└── .streamlit/config.toml     # theme and static file serving
```

## Running it

Requires Python 3.10+.

```bash
python -m venv venv
venv/bin/pip install streamlit anthropic requests beautifulsoup4 numpy pillow
export ANTHROPIC_API_KEY="..."
```

**The app**

```bash
venv/bin/python -m streamlit run app.py
```

Open http://localhost:8501. The study write-up is at http://localhost:8501/study.

**The study** (finished work is cached, so each step can be re-run safely)

```bash
venv/bin/python study.py prepare          # download filings, build answer keys, run the regex baseline (free)
venv/bin/python study.py estimate         # estimated API cost (free)
venv/bin/python study.py run --budget 5   # call the model; stops if the estimate passes the budget
venv/bin/python analyze_study.py          # tables and confidence intervals
venv/bin/python analyze_study.py --exclude-ambiguous
```

EDGAR requires a descriptive `User-Agent` header with your name and email, and limits requests to about 10 per second. Check the SEC's current access policy before downloading.

## Related work

- FinQA (Chen et al., 2021): numerical reasoning over financial reports.
- TAT-QA (Zhu et al., 2021): question answering over hybrid tables and text in financial reports.
- FinanceBench (Islam et al., 2023): open-book financial question answering on public filings.

## Disclaimer

A course research project. Nothing here is financial advice.

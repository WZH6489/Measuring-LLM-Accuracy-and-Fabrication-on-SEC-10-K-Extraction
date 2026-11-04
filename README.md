# Measuring LLM Accuracy and Fabrication on SEC 10-K Extraction

CMPT 218 Final Project, Simon Fraser University (Fall 2026)

**Team:** William Huang, James Park, Vincent Zhao

## Overview

Large language models are increasingly used to read long financial documents, but they can misread a table or invent a figure that is not in the text. This project measures how reliably LLMs extract key numbers (revenue, net income, total assets, and similar) from U.S. SEC 10-K filings, and what kinds of mistakes they make.

We use the structured XBRL data that the SEC publishes for each filing as an automatic answer key, so no hand labeling is needed to score the models.

## Research Questions

1. How does extraction accuracy change across prompting strategies (section-only, few-shot, retrieval-based) compared with a rule-based baseline?
2. When a model is wrong, what kind of error is it (wrong period, wrong scale, wrong line item, or a fabricated number)?
3. Does allowing the model to answer "not found" reduce fabrication?

## Planned Method (summary)

**Data**
- About 60 10-K filings from the most recent fiscal year, from SEC EDGAR, stratified by sector and filing length.
- Ground truth: XBRL values from the SEC `companyfacts` API (data.sec.gov).
- Target fields (tentative): revenue, net income, operating income, total assets, cash and equivalents, diluted EPS.

**Experimental conditions**

| Condition | Description |
|---|---|
| A | Rule/regex baseline |
| B | Zero-shot, financial statements section only |
| C | Few-shot, with examples from filings outside the test set |
| D | Chunk, embed, retrieve top-k, then extract |
| E | Closed-book (company and year only, no document) |
| F | Abstention variant ("return null if unsure") of B, C, and D |

Models: 2 to 3 (one stronger API model, one cheaper model, optionally one open-source model). *TODO: finalize.*

Each model returns JSON with the value, unit, period, and a verbatim evidence quote, so we can check automatically whether the number appears in the filing.

**Metrics**
- Exact match after unit normalization (and within 1% relative error)
- Fabrication rate (the returned number appears nowhere in the filing text)
- Error taxonomy: wrong period, wrong scale, wrong line item, fabricated, formatting failure, abstained
- Confidence intervals, since items cluster within filings

## Repository Structure (planned)

```
.
├── data/            # downloaded filings and XBRL ground truth (not committed if large)
├── src/
│   ├── download/    # EDGAR download and XBRL tag mapping
│   ├── extract/     # prompts and model calls for each condition
│   └── evaluate/    # scoring, fabrication check, error taxonomy
├── results/         # tables and plots
├── report/          # final report materials
└── README.md
```

*TODO: update to match the actual layout.*

## Setup

*TODO: dependencies, API keys, and run instructions once the pipeline exists.*

EDGAR requires a descriptive `User-Agent` header and limits request rates (about 10 requests per second). Check the SEC's current access policy before running the download scripts.

## Team Roles

- William Huang
- James Park
- Vincent Zhao

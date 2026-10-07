# CreditLens

**Automated Credit Risk Assessment & Scenario Stress-Tester.**

Upload a company's financial statements as they appear in the annual report —
granular line items, one or more year columns, one or more files. CreditLens
categorises them itself, computes the full ratio set, a transparent 0–100 credit
score with an indicative rating, multi-year comparison charts, live stress-test
heatmaps, and a Claude-drafted credit review memo.

![Streamlit app](https://img.shields.io/badge/Streamlit-app-brightgreen)

---

## What it does

| Area | Detail |
| --- | --- |
| **Flexible ingest** | `.xlsx` / `.csv`, **multiple files** merged by fiscal year (newest file wins on overlap), multiple sheets per file, statements stacked in one sheet with section headers, and 1–N period columns. Year headers like `FY2023`, `2023`, `31 March 2023`, `2023-24`, `FY23` are all understood. |
| **Auto-categorisation** | A rule engine maps every row to a taxonomy bucket and rolls buckets up into the aggregates the ratio engine needs. *All current-asset lines → current assets; all liability lines except long-term debt → current liabilities;* current portion of long-term debt and current leases → short-term debt; reported subtotals (`Total current assets`, …) are kept for reconciliation, not double-counted. |
| **Review & correct** | A grid shows every row with its detected **category** and **role** (`value` / `subtotal` / `ignore`) — change any of them and the ratios recompute immediately. **Pre-classify with Claude** pre-fills the mappings for messy labels (falls back to rules with no API key). A reconciliation panel flags where classified aggregates disagree with the file's own totals. |
| **Ratio engine** | ~40 ratios across **Profitability, Leverage, Coverage, Liquidity, Solvency, Cash Flow**, each with a strong / adequate / weak read. |
| **Credit score** | Documented weighted model over eight credit-relevant ratios → composite score, rating band (AAA…CC/C), risk category, investment-grade flag, per-factor driver breakdown, and a coverage-based confidence flag. |
| **Multi-year comparison** | Turn on **Comparison mode** and pick 2–5 years. The Overview, Ratios and Credit-score tabs switch to side-by-side tables, trend charts, ratio heatmaps and the score/rating path. |
| **Data visualisation** | Overview tab carries charts as well as the KPI row — revenue & margin trend, margin bridge, capital-structure stack, leverage-vs-coverage, score path, cash-flow bars (multi-year); profitability waterfall + asset-mix / funding donuts (single year). |
| **Scenario stress-testing** | Sliders for revenue decline, EBITDA decline, EBITDA-margin compression, interest-rate shock (bps + floating-rate share), capex change and incremental debt. Base→stressed deltas plus an **EBITDA-decline × rate-shock heatmap** that recolours instantly, with your slider scenario marked on the grid, plus three preset macro scenarios. |
| **Claude credit memo** | `claude-opus-5` drafts a structured committee-style memo (exec summary & recommendation, business/financial profile, leverage, coverage, liquidity, cash flow, scenario analysis, risks & mitigants, suggested covenants, rating rationale) from the metrics, the multi-year trend and your deal notes. Downloadable as Markdown. |

---

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (source .venv/bin/activate elsewhere)
pip install -r requirements.txt
streamlit run app.py
```

Sidebar → **Load sample** to explore a worked 5-year example (a deteriorating
mid-market manufacturer, A → B over FY2020–24).

### Enabling the Claude features

```bash
setx ANTHROPIC_API_KEY "sk-ant-..."     # Windows (new shell after)
# export ANTHROPIC_API_KEY=sk-ant-...   # macOS / Linux
```

or copy `.env.example` → `.env`, or add it to `.streamlit/secrets.toml`.
`CREDITLENS_MODEL` overrides the model (e.g. `claude-sonnet-5`).

---

## Uploading your own statements

- **First column** = line-item labels. Section headers (`Non-current assets`,
  `Current liabilities`, `Cash flow from operating activities`, …) are detected
  and used as context.
- **Other columns** = reporting periods. Values may contain currency symbols,
  thousands separators, `%`, `-`/`–`, and `(1,234)` for negatives.
- Provide as many files as you like (e.g. one workbook per annual report, each
  with its current + comparative year) — they are merged into one dataset.
- After **Process files**, open the **Statements** tab and skim the classification
  grid + reconciliation panel before trusting the numbers.

You can also skip files entirely: **Manual entry** gives an editable canonical
table, and **Canonical template (.xlsx)** downloads its layout.

---

## How the credit score works

Composite = weighted average of eight piecewise-linear sub-scores (each 0–100):

| Factor | Weight | | Factor | Weight |
| --- | --- | --- | --- | --- |
| Net debt / EBITDA | 20% | | FCF / Total debt | 12% |
| EBITDA / Interest | 18% | | EBITDA margin | 10% |
| FFO / Total debt | 15% | | Current ratio | 8% |
| Debt / (Debt + Equity) | 12% | | Return on capital employed | 5% |

Weights renormalise over whatever inputs exist; below 60% coverage the score is
flagged low-confidence. Bands: ≥90 AAA, ≥82 AA, ≥74 A, ≥64 BBB (IG floor),
≥54 BB, ≥42 B, ≥30 CCC, else CC/C.

**Screening aid, not a rating-agency methodology.** Anchor points are
illustrative mid-market calibrations — retune `creditlens/scoring.py` for your
portfolio. Stress shocks cascade with simple assumptions (constant tax rate, D&A
and cash held flat, rate shock on the floating-rate share of debt); see
`creditlens/stress.py`.

---

## Project layout

```
app.py                  Streamlit UI (6 tabs)
creditlens/
  taxonomy.py           line-item buckets + roll-up to canonical keys
  classify.py           rule engine, section detection, aggregation, reconciliation
  classify_llm.py       optional Claude pre-classification
  parser.py             workbook -> raw rows + fiscal-year detection + multi-file merge
  model.py              derive() — fill blanks, add net debt / FFO / capital employed
  ratios.py             ~40 RatioDef entries by category
  scoring.py            weighted credit score -> rating band
  stress.py             StressParams, apply_stress(), heatmap_frame()
  charts.py             matplotlib figure builders
  memo.py               Claude prompt (incl. multi-year trend) + generate_memo()
  sample.py             raw 5-year sample workbook + canonical template
  schema.py             canonical line items (manual-entry path)
```

## Limitations

- Figures are treated as one consistent unit (set the label in the sidebar); no
  unit conversion.
- PDF annual reports are out of scope — export the statements to `.xlsx` / `.csv`.
- Auto-classification is good but not perfect on unusual labels — always review
  the grid and reconciliation panel.
- The memo calls the Anthropic API and consumes tokens; results are cached per
  input signature within a session.

---
name: trader-analyst
description: >-
  The Earnings Analyst. Reads an NSE company's quarterly results (Yahoo
  Finance fundamentals for .NS stocks, or the NSE/BSE results page or Ind AS
  XBRL file you saved from your browser) and sums up what changed: revenue,
  gross, operating and net margins, net profit and diluted EPS, quarter by
  quarter on the Indian April-March fiscal year (Q2 FY27 = Jul-Sep 2026),
  each against the same quarter a year earlier, with the trailing twelve
  months, in ₹ crore. Says when the next results are due under SEBI LODR
  (45 days after the quarter, 60 for Q4). Reads an earnings call transcript
  when you paste one. Use when the user says "how did Reliance's results
  look", "analyze the earnings", "what changed this quarter", "Q2 results of
  TCS", "is revenue growing", "compare margins", "sum up the call", "when are
  the results due", or before trading a stock around its results.
argument-hint: "[NSE symbols] [--file saved-results]"
---

# trader-analyst

Quarterly results move Indian stocks more than anything else on the
calendar, and the press release puts the best number first. This skill lines
up the reported numbers quarter by quarter and says plainly what changed. For
a day trader it answers two questions before a trade: what did the last
results say, and is the next board meeting inside this trade?

Tool in this folder (standard library Python, no keys, no sign-in):

```bash
python "${CLAUDE_SKILL_DIR}/facts.py" --help
```

Symbols are NSE symbols: `RELIANCE` becomes `RELIANCE.NS`. Money is in ₹
crore (1 crore = 100 lakh = 10 million); EPS in ₹.

## What it reads and writes

- **Yahoo Finance fundamentals timeseries** (a plain public GET, no cookie):
  quarterly and annual revenue, gross profit, operating income, net income
  attributable to the parent's owners and diluted EPS. Yahoo keeps about five
  quarters and four years, and sometimes skips a quarter: the tool then
  derives it as the full year minus the other three and marks it "derived".
  Yahoo reports a few companies (Infosys, Wipro) in USD from their US filings;
  the tool says so and shows USD millions.
- **`--file PATH`** (repeatable) for results saved from the exchange:
  - the NSE results page: on nseindia.com, Corporate filings > Integrated
    Filing - Financials (results before March 2025: Financial Results), open
    the filing's XBRL link (an `nsearchives.nseindia.com/corporate/ixbrl/...`
    page) and save it (Ctrl+S);
  - an Ind AS XBRL `.xml` file of the results (NSE or BSE);
  - the NSE results list saved as JSON: the XBRL links in it on
    `nsearchives.nseindia.com` are fetched with a plain GET, others skipped.
  Concepts read: `RevenueFromOperations`, `ProfitOrLossAttributableToOwnersOfParent`
  (else `ProfitLossForPeriod` / `ProfitLoss`), `DilutedEarningsLossPerShare...`,
  and `ProfitBeforeExceptionalItemsAndTax`, `FinanceCosts`, `OtherIncome` for
  operating profit. Units come from the file ("Level of rounding: Lakhs", the
  XBRL unit and decimals); a file that does not state them is refused.
  Consolidated wins over standalone and the header says which one was used.
  Pass the same quarter a year earlier as a second `--file` to get growth.
- The tool does not call `nseindia.com/api`: NSE blocks scripts there and
  this pack does not fake a browser session. Save the page instead.
- Writes `trader/metrics.json` with `--metrics`: one row per company (TTM
  revenue, net profit and diluted EPS, the latest margins and growth).
  `metrics.example.json` in this folder is a real run on 2026-11-06 for
  RELIANCE, TCS, HDFCBANK and INFY.
- Earnings calls: the tool does not fetch transcripts. If the user pastes one
  (companies post them on their IR pages and as an NSE announcement), read it
  and sum up what management said changed: guidance, margins, demand,
  anything they declined to answer.

## The rules

- **Filed numbers win.** Yahoo is a convenient summary; the exchange filing is
  the record. When they differ (Yahoo's revenue can differ slightly from
  "Revenue from operations"), use the filing and say which number came from
  where.
- **Compare like with like.** Growth is against the same quarter a year
  earlier. Quarter-on-quarter changes are shown only for consecutive quarters,
  and many businesses are seasonal.
- **Say the basis.** Consolidated (the group, what the market reacts to) or
  standalone (the listed company alone). Never mix them in one comparison.
- **Say what is derived.** A derived quarter is the year minus three quarters;
  derived EPS is approximate.
- **Say when the data lags.** Under SEBI LODR results are due within 45 days
  of the quarter end (60 for Q4, the audited annual results). When a quarter
  that is already due is missing, the tool prints a NOTE: read the filing on
  NSE and pass it with `--file` before summarising.
- **Flag one-offs.** When net profit is above operating profit, other income
  or exceptional items are inside it. The tool says so. Banks report
  differently: no gross or operating margin is a blank, not a zero.

## Steps

1. Run it for the companies in question (or the symbols on
   `trader/watchlist.json`):

   ```bash
   python "${CLAUDE_SKILL_DIR}/facts.py" RELIANCE.NS
   python "${CLAUDE_SKILL_DIR}/facts.py" TCS INFY HDFCBANK --metrics trader/metrics.json
   python "${CLAUDE_SKILL_DIR}/facts.py" RELIANCE --file q3fy26.html --file q3fy25.html
   ```

   `--quarters 8` shows more (when the source has them), `--json` everything
   machine-readable.
2. Open the results filing or the investor presentation on NSE when the
   numbers need context (segments, guidance), and read it.
3. Write the summary: one line on the quarter (revenue, net profit, EPS,
   growth), one on margins, one on anything unusual (a one-off, a derived
   quarter, data that lags, USD reporting), and the source. Plain words, real
   numbers, no adjectives the numbers do not support.
4. If the next board meeting falls inside the planned trade (an overnight
   hold, or results announced during market hours), say it before
   `/trader-exit` sets the stop: a stop does not protect against a gap. Indian
   companies often announce results during the session, so even an intraday
   trade can be inside it. The meeting date is on NSE (Corporate filings >
   Board meetings) and in `/trader-news` headlines.

## Example

`facts.py RELIANCE.NS` (run 2026-11-06): Q2 FY27, quarter ending 2026-09-30,
revenue ₹330,601.00 crore, +29.8% against a year earlier, net profit
₹25,548.00 crore (+40.6%), diluted EPS ₹18.88 (+40.7%); operating margin
12.5% (+2.0 points on the quarter before). The year-earlier quarter (Q2 FY26)
is derived: Yahoo skipped it. Last four quarters: ₹1,199,033.00 crore revenue
and ₹60.68 EPS. Next results: Q3 FY27, due by 2027-02-14.

`facts.py HDFCBANK` on the same day: Yahoo's data stopped at Q1 FY26, so the
tool printed a NOTE that later quarters were already due. The summary then
read the bank's results on NSE (saved page, `--file`) instead.

## What it never does

- Never says a stock is cheap, expensive, a buy or a sell. It reports what
  the company reported.
- Never invents a number the sources do not have, guesses units, or
  estimates a quarter it cannot derive.
- Never scrapes nseindia.com behind its bot protection or reuses browser
  cookies: saved files only.

## Safety

Education and tooling, not financial advice. This skill only reads results.
Nothing places an order except `/trader-broker`, after `/trader-risk` passes
and the user types an explicit yes for that order.

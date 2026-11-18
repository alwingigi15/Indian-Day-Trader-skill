---
name: trader-scanner
description: >-
  Chart Scanner. Scans hundreds of NSE charts for setups: opening-range
  breakouts, flags, oversold bounces. Builds the list of NSE equities (series
  EQ only, so every name can be traded intraday) from NSE's Nifty 500 / 200 /
  100 / 50 index lists or the full EQUITY_L list, runs a pre-market scan of
  daily closes (20-day closing-high breakout, bull flag, reclaimed the 20-day
  average, oversold, oversold bounce) and an intraday opening-range breakout
  scan on 5-minute bars from the 9:15 AM IST open, then writes
  trader/watchlist.json (symbols like RELIANCE.NS) for trader-news and
  trader-entry. Free Yahoo data, bar by bar. Use when the user says "scan the
  market", "scan Nifty 500", "scan the Nifty 50", "find me setups", "what is
  breaking out on NSE", "opening range breakouts", "scan for bull flags",
  "oversold stocks", "pre-market scan", "build my watchlist", "what should I
  look at today", or at the start of an Indian trading day before /trader-news.
argument-hint: "[universe | daily | orb] [--list nifty500] [--at HH:MM]"
---

# trader-scanner

The Chart Scanner is the first step of a day trade on NSE. It reads hundreds
of free charts and lists the ones that match a rule. That is all it does. A
scan is a list of charts that match a rule, not a list of stocks to buy, and
a setup on a chart is not a prediction. Most people who day trade lose money;
the scanner does not change that. It only saves you from flipping through
500 charts by hand.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe                        # Nifty 500 -> trader/universe.txt
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe --list nifty50         # or nifty100, nifty200, all
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe --file ind_nifty500list.csv   # a CSV saved in your browser
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily                           # before the open -> trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --symbols RELIANCE,TCS,INFY   # or --watchlist FILE, --limit 200
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb                             # after 9:35 AM IST -> trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --at 09:45                  # cut the clock at 9:45 AM IST
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --watchlist trader/watchlist.json --json
```

Flags: `--min-price 50` (rupees) and `--min-volume` (daily 200,000 shares,
orb 0), `--or-minutes 15`, `--max-confirm 200` (orb stage 2), `--until HH:MM`
(orb, default `no_entries_after` or 14:45), `--now "2026-11-06 10:00"` (IST)
to pin the clock, `--offline PATH` to read saved Yahoo files, `--no-write`,
`--show N`, `--json`. Symbols are NSE symbols with the Yahoo suffix
(`RELIANCE.NS`); a bare `RELIANCE` becomes `RELIANCE.NS`, and `M&M`,
`BAJAJ-AUTO` and `3MINDIA` work. Exit code 0 when the scan found something, 1
when it found nothing or could not run (the reason is one plain line).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/universe.txt` | written by `universe`: a `#` header, then one `SYMBOL.NS\|Company name` a line, read by `daily` and `orb` |
| `trader/watchlist.json` | written by `daily` and `orb`: `mode`, `as_of` (IST), `universe`, `scanned`, `failed`, `hits[]`. trader-news and trader-entry read `hits[].symbol` |
| `trader/config.json` | read by `orb`: `no_entries_after` (default 14:45) |
| `trader/market_india.json` | optional: extra NSE holidays, `{"holidays": {"YYYY-MM-DD": "name"}}` |

## The universe

`universe` downloads one CSV from nsearchives.nseindia.com and keeps series
`EQ` only. Series `BE` and `BZ` are trade-for-trade (every trade is
delivered), so they cannot be day traded and are dropped.

| `--list` | file | rows |
| --- | --- | --- |
| `nifty500` (default) | `content/indices/ind_nifty500list.csv` | 500 |
| `nifty200`, `nifty100`, `nifty50` | `content/indices/ind_nifty200list.csv` and so on | 200, 100, 50 |
| `all` | `content/equities/EQUITY_L.csv` | every NSE equity, about 2,400 EQ of 2,630 rows |

The index lists have `Company Name, Industry, Symbol, Series, ISIN Code`;
EQUITY_L has `SYMBOL, NAME OF COMPANY, SERIES, ...` with spaces before the
header names, which the parser trims. If NSE refuses the download (HTTP 401 or
403), open the address in a browser, save the CSV and pass it with
`--file PATH`. The tool never fakes a browser session.

## The daily scan (before the open)

Yahoo's spark endpoint returns closes for 20 symbols a request, so the Nifty
500 takes 25 requests (well under a minute with the polite 0.3 s pause).
Liquidity comes from the spark meta: last price at least `--min-price` (₹50)
and last session's volume at least `--min-volume` (200,000 shares). Run it
before the 9:15 AM open or after the 3:30 PM close: during the session the
volume is the day's so far, and today's daily bar is left out until 3:30 PM.

The setups are computed from daily closes only (spark has no highs or lows):

| setup | the rule on closes |
| --- | --- |
| `breakout_20d` | the last close is above every close of the 20 sessions before it |
| `bull_flag` | up more than 10 % over the 10 sessions before the last 5, then the last 5 closes inside a 5 % range |
| `reclaim_ma20` | the close before was under its 20-day average, the last close is above it |
| `oversold` | RSI 14 (Wilder) under 30 |
| `oversold_bounce` | RSI 14 was under 30 yesterday and today closed up |

A closing-high breakout fires more often than an intraday-high one. Hits are
listed most traded first (price times volume, in rupees).

## The opening-range breakout scan (during the session)

The NSE session runs 9:15 AM to 3:30 PM IST (375 minutes, 75 five-minute
bars; the 9:00 to 9:15 pre-open auction has no bars). The opening range is
the first `--or-minutes` (15) of the session: the 9:15, 9:20 and 9:25 bars.
A hit is the first completed 5-minute bar to CLOSE above the range high, at
that bar's close time, so the earliest hit is at 9:35 AM.

1. Stage 1, every symbol: spark closes of today's 5-minute bars, 20 a
   request. A chart goes on only if a completed bar after the range closed at
   or above the highest close of the range. That is a necessary condition
   (the range high is at least every range close), so no breakout is lost.
2. Stage 2, each candidate: the v8 chart with real highs and lows, one
   request each. Range high = the highest high of the range bars, range low =
   the lowest low. On a strong morning most charts are candidates, so stage 2
   checks at most `--max-confirm` (200) of them, the most traded first (price
   times volume so far), and prints PARTIAL with the count it skipped.
   `--max-confirm 0` checks them all.

A chart is usable only when every 5-minute bar from 9:15 to the cutoff is
there; a missing bar means a halt, a circuit lock or a thin stock. Bars are
read on the IST clock (India is UTC+5:30, so the grid is 9:15, 9:20, ... and
never the UTC hour). Null closes are dropped, a bar off the 5-minute grid
(the live tick, like 11:07:49) is dropped, and a bar counts only once it is
complete (start + 5 minutes). Before 9:35 AM, on a weekend or NSE holiday,
after the close, or after `no_entries_after` (an expired setup) it prints one
plain line and writes nothing.

## Data and limits

- Yahoo's chart and spark endpoints are free, unofficial and can lag. Bars are
  5-minute bars, bar by bar, never tick by tick. query2 first, query1 on a 429
  or 5xx, one retry after a 5-second wait on a 429.
- Partial results are said as partial: "Fetched 480 of 500 charts (20 failed
  or had no data)". Never round a partial scan up to "the whole market".
- NSE holidays for 2026 are built in (NSE holiday circular, checked
  2026-11-06). NSE publishes 2027 around December: add those dates to
  `trader/market_india.json`. Muhurat trading on Diwali is treated as closed.
  NSE has no scheduled early closes.

## Steps

1. Once a week or so: `scan.py universe` (Nifty 500; `--list all` for every
   EQ stock).
2. Before the open: `scan.py daily`. Tell the user the counts (fetched,
   liquid, scanned, hits) and the top few by setup.
3. After 9:35 AM IST (9:45 or 10:00 gives the range time to matter):
   `scan.py orb`. Tell the user how many usable charts broke their range and
   when, and that a later hit is closer to the price now.
4. The user picks one chart. Hand it on: `/trader-news SYMBOL` (what is moving
   it), then `/trader-entry SYMBOL` (the entry bar by bar). Never pick for them.

## Example: Friday Nov 6 2026

Scan at 10:00 AM IST over 101 Nifty 500 names
(`tests/fixtures/trader-scanner/wall5m-2026-11-06-1107.json.gz`, real Yahoo
charts saved at 11:07 AM IST, one made-up symbol):

```
python3 scan.py orb --offline wall5m-2026-11-06-1107.json.gz --now "2026-11-06 10:00"
Opening-range breakout scan, Fri 2026-11-06 10:00 AM IST: 15-minute range (9:15 AM to 9:30 AM), 5-minute bars, Yahoo.
Fetched 100 of 101 charts (1 failed or had no data); 100 usable; 76 candidates, 76 confirmed with real highs and lows.
47 of 100 charts closed above their opening-range high by 10:00 AM:
   9:35 AM  AAVAS.NS         ₹1,133.30  range 1,117.00 to 1,130.20  +0.27% over
  ...
   9:50 AM  RELIANCE.NS      ₹1,311.90  range 1,295.60 to 1,306.00  +0.45% over
```

RELIANCE.NS's range is the 9:15 to 9:25 bars, high 1,306.00, low 1,295.60;
the 9:45 bar closed at 1,311.90, 0.45 % over the high, at 9:50 AM. The user
picks RELIANCE and Claude hands it to `/trader-news RELIANCE.NS`, then
`/trader-entry RELIANCE.NS`.

The pre-market scan that morning (closes through Thu Nov 5) tags RELIANCE,
HDFCBANK, M&M, BAJFINANCE and EICHERMOT a 20-day closing-high breakout, AXISBANK
and CANBK a reclaim of the 20-day average, BSOFT, ATHERENERG and DRREDDY a bull
flag, and APTUS and AIAENG oversold.

## Rules

- Say what was scanned: how many symbols, how many charts came back, how many
  were usable, and the time of the data (IST).
- Say "matched a rule", never "a buy", "a winner" or "a good trade".
- Never print a P&L, a return or a profit figure.
- A hit is stale once the price has moved on. Re-run before acting on it.

## What it never does

It never places, sizes or checks an order, never says which stock to buy,
never calls data real time, and never promises that a setup works.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.

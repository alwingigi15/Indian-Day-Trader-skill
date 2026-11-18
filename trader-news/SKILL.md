---
name: trader-news
description: >-
  News Monitor for Indian stocks. Tracks breaking news on every NSE stock you
  watch and flags what moves it: Yahoo Finance headlines per symbol
  (RELIANCE.NS), NSE corporate announcements filed under SEBI LODR
  Regulation 30 (financial results, board meetings, deals, orders won,
  management changes, pledges, credit ratings) from NSE's public feed, and how
  far each stock is from yesterday's close in ₹. Shows only what is new since
  the last check, newest first, in IST, tags each item (earnings, deal,
  orders, management, promoter, analyst, halt, regulatory, macro...), hides
  routine filings, and never repeats one. Use when the user says "any news on
  RELIANCE", "why is TCS moving", "what's the news on my watchlist", "morning
  briefing", "pre-market news", "check NSE announcements", "did anything
  break", "any results today", or after /trader-scanner finds setups.
argument-hint: "[symbols]"
---

# trader-news

A chart can look perfect and still be about to gap on results, a block deal
or a SEBI order. Before a setup becomes a trade, this skill reads the two
feeds that carry those first, for every stock on the watchlist, and tells you
only what is new. It also prints how far each stock has moved today, so a
headline and a move sit side by side.

Tool in this folder (standard library Python, no keys):

```bash
python3 "${CLAUDE_SKILL_DIR}/news.py"                                # watchlist + plan + portfolio
python3 "${CLAUDE_SKILL_DIR}/news.py" --tickers RELIANCE.NS TCS --hours 12
python3 "${CLAUDE_SKILL_DIR}/news.py" --no-nse --json                # headlines only
python3 "${CLAUDE_SKILL_DIR}/news.py" --announcements-file CF-AN-equities.csv
```

A bare symbol (`RELIANCE`) means NSE (`RELIANCE.NS`).

## What it reads and writes

| file / feed | what |
| --- | --- |
| `--tickers`, else `trader/watchlist.json` | the scanner's hits (trader-scanner) |
| `trader/plan.json` | the trade you are planning (trader-exit) |
| `trader/portfolio.json` | what you hold (trader-broker records fills there) |
| `trader/universe.txt` | company names (`SYMBOL.NS\|Name`), to match announcements by name |
| Yahoo Finance headline RSS | `https://feeds.finance.yahoo.com/rss/2.0/headline?s=RELIANCE.NS&region=IN&lang=en-IN` |
| Yahoo Finance chart endpoint | the last price against yesterday's close (unofficial, can lag) |
| NSE announcements feed | `https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml`: the latest announcements of every listed company, one request |
| `--announcements-file` | a CSV or JSON you downloaded from NSE's announcements page in your browser, for a longer history |
| `trader/state/news.json` | what has been shown (kept 30 days) |

NSE's feed holds only the most recent announcements, so check it often during
the day. NSE's website serves most other data only to a browser; the tool
never fakes a browser session. If the feed is down, it says so and carries on
with Yahoo headlines, and points to the browser download.

## The rules

- **Headlines are leads, not facts.** Before telling the user something
  happened, open the story or the filing and read it. Quote the source and
  link it. Never summarise a paywalled story you could not read: say so.
- **Filings beat headlines.** If an NSE announcement and a headline disagree,
  the announcement is what the company told the exchange.
- **Say what it touches.** Every item lists the symbols it touches and its
  tags. Announcements marked `!` move stocks: results, a deal, an order win,
  a management change, a promoter pledge, a suspension or surveillance
  action, or a regulatory order. Routine filings (newspaper copies, NAVs,
  trading-window notices) are hidden unless `--routine`.
- **Names can mislead.** "Reliance MF ..." is a mutual fund, not Reliance
  Industries. Announcements are matched by the NSE symbol in the filing, or by
  the full company name, never by a single word.
- **MOVING** marks a stock 3% or more from yesterday's close (`--move-pct`).
  A move with no headline is worth saying out loud too. Near a circuit limit,
  say that the stock can freeze.
- **No predictions.** Report what was published and how far the price is from
  yesterday's close. Never say what the stock will do next.

## Steps

1. Run the check: `python3 "${CLAUDE_SKILL_DIR}/news.py"`. Exit code 1 means
   nothing new. `--all` shows items already seen, `--json` gives everything
   machine readable, `--dry-run` leaves the state alone.
2. Read the items that matter: every `!` announcement, anything tagged
   earnings, halt, regulatory, promoter or risk, and every MOVING stock first.
3. Write a short brief, per stock: what happened, the source and link, the
   move, and one line on why it matters to the setup (for example "results
   are due after the close: a gap tomorrow can jump past any stop, so this
   stays an intraday trade, flat by 15:10").
4. If a story changes the picture, say so before `/trader-entry` and
   `/trader-exit`: results inside the trade, a board meeting on a fund raise,
   a surveillance (ASM/GSM) listing. Never act on a headline alone.

During the session: `/loop 15m /trader-news`, or a scheduled task each
morning around 8:45 IST, before the 9:15 open.

## Example

At 12:00 IST on 6 Nov 2026, with Godrej Agrovet on the watchlist, the check
shows the company's results filings from 11:30 IST:

```
! 2026-11-06 11:30 IST  GODREJAGRO.NS NSE           Godrej Agrovet Limited: Financial Results Updates
      tags: earnings  https://nsearchives.nseindia.com/content/debt/WDM/GODREJAGRO_...pdf
```

The brief: "Godrej Agrovet filed its quarterly results at 11:30 IST. Read the
filing before trading it: the first reaction can reverse." When a stock has
no news, say so rather than padding the brief.

## What it never does

- Never invents or embellishes a story, a quote or a number.
- Never trades on news, and never tells the user to buy or sell because of a
  headline.
- Never hammers a server: one request per feed, a pause between calls.
- Never works around NSE's website protection.

## Safety

Education and tooling, not financial advice. This skill only reads and
reports. Nothing places an order except `/trader-broker`, after
`/trader-risk` passes and the user types an explicit yes for that order.

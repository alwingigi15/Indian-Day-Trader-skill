---
name: trader-insider
description: >-
  The Insider Tracker. Watches who is buying and selling Indian stocks in the
  public record: NSE's daily bulk and block deals (the client's name, buy or
  sell, quantity, price, value in ₹) read straight from NSE's published CSVs,
  and SEBI insider-trading (PIT) disclosures by promoters, directors and key
  managerial personnel from the file the user saves from NSE's insider-trading
  page. Alerts once per new deal or disclosure, flags stocks you hold or
  watch, follows a named investor, and shows how many trading days each
  disclosure took. Use when the user says "what are promoters buying",
  "insider buying in RELIANCE", "bulk deals today", "block deals", "track
  <investor> bulk deals", "did any director sell TCS", "promoter pledges", or
  "run the insider skill".
argument-hint: "[deals|pit|new] [--who name] [--symbol SYM]"
---

# trader-insider

India has no law that makes members of Parliament disclose their stock
trades the way the US STOCK Act does, so there is no "politician tracker"
here. There are two public records of who is buying and selling, and this
skill reads both:

1. **Bulk and block deals.** A bulk deal is one client trading at least 0.5%
   of a company's listed shares in a day; a block deal is one large trade in
   the separate block-deal window. NSE publishes both every trading day, with
   the client's name (a fund, an FII, a well-known investor, a promoter
   company).
2. **SEBI insider trading (PIT) disclosures.** Under the SEBI (Prohibition of
   Insider Trading) Regulations, 2015, promoters, directors, key managerial
   personnel and designated persons report their trades to the company within
   2 trading days, and the company tells the exchange within 2 more.

It is fast. It is not early. A deal is published after the close; a PIT
disclosure arrives days after the trade. There is no inside information
here: only public filings, read quickly. Say that every time.

Tool in this folder (standard library Python):

```bash
python3 "${CLAUDE_SKILL_DIR}/insider.py" --help
```

## What it reads and writes

- Reads `https://nsearchives.nseindia.com/content/equities/bulk.csv` and
  `.../block.csv` (the latest trading day only). `--bulk-file` /
  `--block-file` read a saved copy, or the history CSV from
  `https://www.nseindia.com/report-detail/display-bulk-and-block-deals`.
- Reads a PIT file the user downloaded: NSE's insider-trading page
  (`https://www.nseindia.com/companies-listing/corporate-filings-insider-trading`)
  only works in a browser, so the user clicks its CSV download (or saves the
  JSON) and passes it with `--pit-file`. The tool never fakes a browser
  session or works around NSE's protection.
- Writes `trader/state/insider.json` (the items already shown, so each one
  alerts once).
- Reads `trader/portfolio.json`, `trader/plan.json` and
  `trader/watchlist.json` (if they exist) to flag stocks you hold or watch.
- Reads `trader/market_india.json` for extra NSE holidays (used to count
  trading days to disclosure).

## The rules

- **Quote the record, not a tracker.** Every row comes from NSE's own file,
  with the date it was published. Third-party sites are fine for browsing; the
  exchange file wins.
- **A deal names the client, not the reason.** A bulk buy can be one leg of a
  trade that was sold the same day (brokers and arbitrage desks show up
  often). It says nothing about whether they still hold.
- **Mode matters for PIT.** Only market purchases and sales are bets.
  ESOP allotments, gifts, inter-se transfers between promoters, and pledges
  are not. `--market-only` keeps only market trades; pledges show as
  `PLEDGE`.
- **Say the delay.** Each PIT row shows the trade date, the date NSE
  published it, and flags it LATE when a step took more than 2 trading days.
- **Amounts in ₹,** in lakh and crore.

## Step 1: today's bulk and block deals

```bash
python3 "${CLAUDE_SKILL_DIR}/insider.py" deals
python3 "${CLAUDE_SKILL_DIR}/insider.py" deals --who "kacholia"          # one investor
python3 "${CLAUDE_SKILL_DIR}/insider.py" deals --symbol RELIANCE --side buy
python3 "${CLAUDE_SKILL_DIR}/insider.py" deals --min-value 50000000      # ₹5 crore and up
python3 "${CLAUDE_SKILL_DIR}/insider.py" deals --mine                    # stocks you hold or watch
```

`--who` matches when every word of the name appears in the client's name, and
repeats. Exit code 1 means nothing matched.

## Step 2: insider and promoter trades

```bash
python3 "${CLAUDE_SKILL_DIR}/insider.py" pit --pit-file CF-Insider-Trading.csv --category promoter --side buy
python3 "${CLAUDE_SKILL_DIR}/insider.py" pit --pit-file CF-Insider-Trading.csv --symbol TCS --market-only
```

Categories: promoter, promoter group, director, KMP, designated person,
relative.

## Step 3: watch for new ones

```bash
python3 "${CLAUDE_SKILL_DIR}/insider.py" new                                  # deals only
python3 "${CLAUDE_SKILL_DIR}/insider.py" new --pit-file CF-Insider-Trading.csv  # deals and PIT
```

Shows only items not seen at the last check (the first run shows the last
week) and records them in the state file. NSE publishes deals once a day after
the close, so one check in the evening on trading days is plenty: run it with
a scheduled task or `/loop`.

## Example output

```
BULK  2026-11-05  AGIIL.NS  ARTHKUMBH VENTURES LLP BUY 1,000,000 sh @ ₹273.00 = ₹27.30 crore
PIT   2026-11-05  RELIANCE.NS  SAMPLE PROMOTER HOLDINGS PRIVATE LIMITED (promoter group) BUY 50,000 sh, ₹7.42 crore (about ₹1,485.00/sh), market purchase, traded 2026-11-03, on NSE 2 days later
```

Then: "This is a public disclosure published after the trade. It says what
was bought, not what anyone will buy next."

If the user wants to act on it, it goes the long way: a disclosure is a
reason to look at a chart, not a setup. `/trader-scanner` and `/trader-entry`
decide if there is a trade today; then shares only, sized by
`/trader-budget`, checked by `/trader-risk`, and a ticket from
`/trader-broker` only after their yes.

## What it never does

- Never says or implies an insider or a big investor knows something the
  market does not, or that copying them beats the market. No return claims.
- Never treats an ESOP, gift, pledge or inter-se transfer as a market bet.
- Never places an order.
- Never automates a login or works around NSE's website protection.

## Safety

- Education and tooling, not financial advice. Nothing in this pack is a
  recommendation to buy or sell anything.
- Ticket-only by default (`"broker": "none"`, `"paper": true` in
  `trader/config.json`): nothing is sent to a broker.
- Nothing places an order except `/trader-broker`, and only after
  `/trader-risk` passes AND the user types an explicit yes for that order in
  the chat, AND the broker's order tool is set to "Needs approval" in Claude
  so Claude's own "Allow once / Deny" card appears too. Never "Always allow"
  an order tool.
- No options, margin, shorting or leverage unless the config allows it and
  the user confirms.
- Bulk/block deals and SEBI PIT disclosures are PUBLIC records published after
  the trade. This skill reads them fast; it has no inside information, and it
  says so.

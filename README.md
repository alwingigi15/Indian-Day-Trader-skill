# The Indian Day Trader skill

11 Claude skills for day trading Indian stocks on NSE: scan the Nifty 500, track NSE announcements, time every entry and exit in IST, and replay every outcome, charges included, before you risk a rupee.

Free, MIT, no signup, no API key. The tools are plain Python that read free
public data: Yahoo Finance charts for NSE (`RELIANCE.NS`), NSE's published
stock lists, NSE's announcement feeds and its daily bulk and block deal files.

This is the India edition of
[Jake Schincariol's Day Trader skill](https://github.com/Jakeschincariol/day-trader-skill),
the same method with the market rules changed: NSE hours (9:15 to 15:30 IST),
the NSE holiday calendar, NSE tick sizes, rupees, Indian intraday charges
(brokerage, STT, exchange fees, SEBI fee, stamp duty, GST), MIS square-off,
and Zerodha Kite order tickets. `INDIA_MIGRATION_GUIDE.md` explains every
change and how to keep the numbers current.

And no trade goes through until you say yes. Nothing in this pack can place
an order on its own. Ticket-only is the default: the pack builds the order
ticket and you place it in your broker's app.

## Install

In Claude Code, from the folder that contains this one:

```
/plugin marketplace add ./indian-day-trader-skill
/plugin install indian-day-trader-skill@indian-day-trader-skill
```

Plugin skills are namespaced (`/indian-day-trader-skill:trader-scanner`).
Copy the folders instead if you want plain `/trader-scanner`:

```bash
cp -r indian-day-trader-skill/trader-* ~/.claude/skills/
```

On Windows (PowerShell):

```powershell
Copy-Item -Recurse indian-day-trader-skill\trader-* $HOME\.claude\skills\
```

Project-local instead of global: copy the same folders into your project's
`.claude/skills/`. No Claude Code at all? Paste any single `SKILL.md` at the
top of a chat and it runs as a mode. You lose the Python tools, but the method
works.

The tools need Python 3.8 or newer. Nothing to pip install.

## The eleven

| command | job | what it does |
| --- | --- | --- |
| `/trader-scanner` | Chart Scanner | Builds the stock list from NSE (Nifty 500 by default, EQ series only) and scans it for setups: opening-range breakouts, flags, oversold bounces. |
| `/trader-news` | News Monitor | Yahoo headlines and NSE corporate announcements for every stock you watch, tagged (results, deals, management, pledges...). |
| `/trader-entry` | Entry Timer | Watches a setup bar by bar from 9:15 IST and tells you the moment to get in. |
| `/trader-exit` | Exit Planner | Sets your stop and target on legal NSE ticks before you enter, flat by 15:10. |
| `/trader-timemachine` | Time Machine | Replays the rest of the session 10,000 times plus every past time the setup fired, net of Indian charges. |
| `/trader-risk` | Risk Manager | Checks every trade against your ₹ limits, ticks, the entry window, MIS cut-off and trade-for-trade stocks, and blocks anything over them. |
| `/trader-budget` | Budget Manager | Sizes every position in whole shares so the loss at the stop plus charges fits your max loss. |
| `/trader-broker` | Order Ticket | Builds the order ticket (Zerodha Kite format), ticket-only by default, never without your yes. |
| `/trader-alerts` | Price Alerts | Rings when a price, a stop, the NSE opening bell or your flat-by time hits. |
| `/trader-analyst` | Earnings Analyst | Reads Indian quarterly results (April-March fiscal year, ₹ crore) and sums up what changed. |
| `/trader-insider` | Insider Tracker | NSE bulk and block deals and SEBI insider (PIT) disclosures by promoters and directors. |

## How to use it

Everything lands in a `trader/` folder in your project, and each skill reads
what the last one wrote:

```
scanner -> news -> entry -> exit -> timemachine -> budget -> risk -> broker   (one trade)
alerts: stop, target, flat by 15:10                                           (while it is open)
news / analyst / insider                                                       (on a schedule)
```

A trading day, all times IST:

1. **Once a month:** `/trader-scanner` rebuilds `trader/universe.txt` from
   NSE's Nifty 500 list.
2. **8:30 to 9:00:** `/trader-scanner` runs the daily scan on yesterday's
   closes and writes `trader/watchlist.json`; `/trader-news` shows what broke
   overnight on those stocks.
3. **9:15 to 9:30:** the opening range forms. No trades.
4. **From 9:30:** `/trader-entry` watches the watchlist bar by bar and says
   triggered on the first 5-minute close above the range.
5. **On a trigger:** `/trader-exit` sets the stop and target on NSE ticks;
   `/trader-timemachine` gives the honest odds after charges; `/trader-budget`
   sizes it in rupees; `/trader-risk` says PASS or BLOCK; `/trader-broker`
   shows the ticket and waits for your yes; `/trader-alerts` watches the stop,
   the target and the flat-by time.
6. **14:45:** no new entries after this.
7. **15:10:** be flat, before your broker's MIS auto-square-off (around
   15:15 to 15:25, with a fee).

## The tools

All standard-library Python. The data tools read public endpoints and are
polite about it.

```bash
python3 trader-scanner/scan.py universe --list nifty500                # NSE stock list -> trader/universe.txt
python3 trader-scanner/scan.py daily                                   # pre-market setups, whole universe
python3 trader-scanner/scan.py orb                                     # opening-range breakouts so far
python3 trader-news/news.py --tickers RELIANCE.NS --hours 12           # headlines, NSE announcements, today's move
python3 trader-entry/entry.py check RELIANCE.NS --setup orb            # waiting / triggered / expired
python3 trader-entry/entry.py watch RELIANCE.NS --every 30             # bar by bar until it triggers
python3 trader-exit/exit.py plan --stop or-mid --target 2R --write     # -> trader/plan.json
python3 trader-timemachine/timemachine.py run --write                  # 10,000 replays + the history
python3 trader-budget/size.py --write                                  # -> trader/orders.json
python3 trader-risk/check.py                                           # PASS / BLOCK per order
python3 trader-broker/ticket.py show                                   # the tickets, READY or not
python3 trader-alerts/alerts.py status                                 # is NSE open, in IST
python3 trader-alerts/alerts.py check                                  # rings what hit, once
python3 trader-analyst/facts.py RELIANCE.NS                            # quarters, growth, EPS
python3 trader-insider/insider.py deals                                # today's bulk and block deals
```

```bash
python3 -m unittest discover -s tests -v
```

The tests run offline, on fixtures cut from real NSE data (Yahoo bars for
RELIANCE.NS and others, NSE's stock lists and deal files). Where NSE only
serves data to a browser, the fixture is a labelled synthetic sample in NSE's
exact format.

## Keep these current

Market rules change. They live in one place each:

- **Holidays:** built in for 2026. Add 2027 (NSE publishes it in December)
  to `trader/market_india.json`:
  `{"holidays": {"2027-01-26": "Republic Day"}}`. Source: nseindia.com,
  Resources, Exchange Communication, Holidays.
- **Charges:** the `charges` block in `trader/config.json` (see
  `trader-budget/config.example.json`). Check them against your broker's
  brokerage calculator.
- **Tick sizes:** NSE circular NSE/CMTR/67133; NSE reviews them monthly.
- **MIS square-off time:** your broker's; set `flat_by` a few minutes earlier.

## Fine print

**Not financial advice.** This is software that reads public data and does
arithmetic. It does not know your situation and it cannot see the future.
SEBI's own studies found that most individual intraday traders lose money,
before and after charges. You can lose money. Decide for yourself, and talk
to a SEBI-registered investment adviser if you need advice.

**No trade goes through until you say yes.** Only `/trader-broker` can lead to
an order, and only after `/trader-risk` passes that exact order and you type
yes for it in the chat. If you connect a broker tool to Claude, set it to
"Needs approval", never "Always allow", so Claude's own "Allow once / Deny"
card is a second check. No options, margin, short selling or leverage unless
you turn them on.

**Ticket-only first.** Most Indian brokers have no paper-trading account, so
the default (`"broker": "none"`) builds the ticket and you place it yourself.
Broker API tokens expire daily and belong in environment variables, never in
the chat. SEBI's rules for retail API trading (static IP and others) apply if
you automate; check your broker's current terms.

**The Time Machine is a simulation, not a prediction.** It is built from the
stock's own last 60 days of 5-minute bars. The future can do things those 60
days never did: results, a circuit, a headline. Treat its odds as a reality
check, never as a promise.

**A stop is a plan, not a guarantee.** In a fast market, across a gap, or when
a stock is frozen at its circuit limit, a stop can fill well past its price or
not at all. The max loss shown is the loss at the stop price plus charges.

**The data is free and unofficial.** Yahoo Finance's chart endpoint is not an
official API: it can lag, change or rate-limit you. NSE's website protects
most of its data from scripts; this pack never works around that, and asks you
to save a file from your browser where needed. Check anything that matters
against your broker before you act on it.

**Insider and bulk deals are public, not inside information.** They are
published after the trade. The insider skill reads them quickly; that is all.

**Taxes.** In India, intraday equity profits are usually treated as
speculative business income, taxed at your slab rate. Nothing here is tax
advice.

## Files

```
trader-scanner/     SKILL.md, scan.py
trader-news/        SKILL.md, news.py
trader-entry/       SKILL.md, entry.py
trader-exit/        SKILL.md, exit.py
trader-timemachine/ SKILL.md, timemachine.py
trader-risk/        SKILL.md, check.py
trader-budget/      SKILL.md, size.py, config.example.json, targets.example.json
trader-broker/      SKILL.md, ticket.py, claude_desktop_config.example.json
trader-alerts/      SKILL.md, alerts.py, alerts.example.json
trader-analyst/     SKILL.md, facts.py, metrics.example.json
trader-insider/     SKILL.md, insider.py
tests/              unittest suite + offline fixtures
INDIA_MIGRATION_GUIDE.md   how the US pack was converted, and what to keep current
```

## License

MIT. Original US pack made by [Jake Schincariol](https://opusjake.ai); India adaptation built on it.

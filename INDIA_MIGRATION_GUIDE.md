# Converting the Day Trader Skill from US stocks to Indian stocks (NSE / BSE)

This guide walks through converting all 11 skills in this repo from the US
market (NYSE / Nasdaq, USD, Alpaca, SEC) to the Indian market (NSE / BSE, INR,
an Indian broker, NSE/BSE filings). Follow the phases in order. Run the tests
after each phase, and don't start the next phase until they pass.

> **Read this first: facts that change over time.** Holidays, tick sizes,
> taxes and charges, broker API prices and SEBI rules change. Every value
> marked **(verify)** below was correct as far as we know when this was
> written, but check it against the official source linked next to it before
> you trade real money. Put these values in config files instead of
> hard-coding them, so a rule change means editing one JSON file, not the
> Python.

> **Status: done.** This folder is the converted India edition. The code
> follows this guide, with the corrections below, and all 386 tests pass.
> What was decided and checked while building it:
>
> - NSE only; symbols stored as `RELIANCE.NS`; stock list from the Nifty 500
>   (EQ series only). MIS intraday, long only, no leverage.
> - Broker: `"broker": "none"` (ticket only) by default; orders translate to
>   Zerodha Kite `place_order` fields for when you connect Kite's MCP server.
> - `trader-pelosi` became `trader-insider` (NSE bulk/block deals + SEBI PIT).
> - 2026 NSE holidays are built in (16 weekdays, from NSE's 2026 circular,
>   including the 15 Jan Maharashtra election closure). **2027 is not
>   published yet**: add it to `trader/market_india.json` in December.
> - Tick sizes from NSE circular NSE/CMTR/67133 (effective 15 Apr 2025).
> - Charges from zerodha.com/charges (checked 6 Nov 2026): the NSE
>   transaction charge is **0.00307%** (an earlier draft of this guide said
>   0.00297%).
> - No cookie or browser-faking tricks for nseindia.com: see 7.3.

---

## Contents

0. [What changes, in one table](#0-what-changes-in-one-table)
1. [Preparation](#1-preparation)
2. [Phase 1: shared market rules (time zone, hours, holidays, tick size, ticker format)](#2-phase-1-shared-market-rules)
3. [Phase 2: the stock list and the scanner (`universe.txt`, `watchlist.json`)](#3-phase-2-the-stock-list-and-the-scanner)
4. [Phase 3: entry, exit and the Time Machine](#4-phase-3-entry-exit-and-the-time-machine)
5. [Phase 4: budget and risk (rupees, charges, intraday rules)](#5-phase-4-budget-and-risk)
6. [Phase 5: price alerts](#6-phase-5-price-alerts)
7. [Phase 6: news](#7-phase-6-news)
8. [Phase 7: earnings analyst](#8-phase-7-earnings-analyst)
9. [Phase 8: replace the Pelosi tracker](#9-phase-8-replace-the-pelosi-tracker)
10. [Phase 9: broker](#10-phase-9-broker)
11. [Phase 10: tests and fixtures](#11-phase-10-tests-and-fixtures)
12. [Phase 11: documentation (SKILL.md, README, plugin.json)](#12-phase-11-documentation)
13. [Your daily routine after the conversion](#13-your-daily-routine-after-the-conversion)
14. [Final checklist](#14-final-checklist)
15. [Reference: Indian market facts and data sources](#15-reference)

---

## 0. What changes, in one table

| Area | US (now) | India (target) |
|---|---|---|
| Exchange | NYSE / Nasdaq | NSE (main), BSE (optional) |
| Time zone | New York, with daylight saving (UTC-4 / UTC-5) | IST, **UTC+5:30 all year, no daylight saving** |
| Regular session | 9:30 – 16:00 ET | **9:15 – 15:30 IST** |
| Pre-open | (not used) | 9:00 – 9:15 IST (call auction, no intraday bars) |
| Early closes | 13:00 ET on some days | None. **Muhurat trading**: a special evening session on Diwali |
| Holidays | NYSE list | NSE holiday list (published every December) |
| Currency | `$`, USD | `₹`, INR |
| Tick size | $0.01 | Depends on the price band: ₹0.01 / ₹0.05 / ₹0.10 ... (verify) |
| Ticker on Yahoo | `TSLA`, `BRK-B` | `RELIANCE.NS` (NSE), `500325.BO` or `RELIANCE.BO` (BSE) |
| Stock list | Nasdaq Trader symbol files | NSE `EQUITY_L.csv` / Nifty 500 list |
| Filings | SEC EDGAR (8-K, 10-Q, XBRL us-gaap) | NSE/BSE corporate announcements, financial results (Ind AS XBRL) |
| Politician trades | STOCK Act PTRs (House Clerk) | No equivalent. Use SEBI insider (PIT) disclosures or bulk/block deals |
| Broker | Alpaca (has paper trading, MCP server) | Zerodha Kite Connect / Upstox / Angel One SmartAPI / Dhan (mostly no paper trading) |
| Day-trade rule | Pattern Day Trader (PDT, $25k) | No PDT. Intraday (MIS) positions are **auto-squared-off by the broker around 15:15–15:25** |
| Costs | Near zero | Brokerage + STT + exchange fees + SEBI fee + stamp duty + GST (adds up, must be in the R calculation) |
| Daily price limits | LULD halts | **Circuit limits** (2/5/10/20% bands); F&O stocks have dynamic bands |

### Effort per skill

| Skill | File | Effort | Main changes |
|---|---|---|---|
| trader-exit | `trader-exit/exit.py` | Small | IST, tick-size rounding, ₹ |
| trader-entry | `trader-entry/entry.py` | Small | IST, 9:15 open, `.NS` symbols |
| trader-timemachine | `trader-timemachine/timemachine.py` | Small | IST, session times, `.NS` |
| trader-scanner | `trader-scanner/scan.py` | Medium | NSE stock list, IST, filters |
| trader-alerts | `trader-alerts/alerts.py` | Medium | NSE calendar, IST, drop Stooq |
| trader-budget | `trader-budget/size.py` | Medium | ₹, charges, order shape |
| trader-risk | `trader-risk/check.py` | Medium | ₹, ticker regex, remove PDT, circuits, entry window |
| trader-news | `trader-news/news.py` | Medium–Large | NSE/BSE announcements instead of 8-Ks |
| trader-broker | `trader-broker/ticket.py` | Large | Indian broker order format, daily login |
| trader-analyst | `trader-analyst/facts.py` | Large | NSE/BSE results instead of SEC XBRL |
| trader-pelosi | `trader-pelosi/ptr.py` | Rewrite | Insider trades / bulk deals |

---

## 1. Preparation

### 1.1 Make a branch (keep the US version safe)

```powershell
cd C:\Users\antus\day-trader-skill
git checkout -b india
```

If anything goes wrong you can always go back with `git checkout main`.

### 1.2 Check Python and run the tests before changing anything

The scripts use only the Python standard library. Python 3.9+ is required
because the guide uses `zoneinfo`.

```powershell
python --version
cd tests
python -m unittest -v
cd ..
```

Note how many tests pass. That's your baseline. Your goal is a test suite
that passes again at the end, with Indian fixtures.

### 1.3 Clear out the old US state files

The `trader/` folder holds runtime state, not code. Once you're on the new
branch, delete the US data so no skill reads it by mistake:

```powershell
Remove-Item trader\universe.txt, trader\watchlist.json -ErrorAction SilentlyContinue
```

Also delete any `trader/plan.json`, `trader/entry.json`, `trader/orders.json`,
`trader/prices.json`, `trader/portfolio.json` or `trader/config.json` left
over from US runs (look at each one first).

### 1.4 Decide these up front

| Decision | Options | Recommendation |
|---|---|---|
| Exchange | NSE only / NSE + BSE | **NSE only** (better liquidity; Yahoo `.NS`) |
| Stock list | All NSE `EQ` stocks / Nifty 500 / Nifty 100 | **Nifty 500** for day trading |
| Product | MIS (intraday, leverage) / CNC (delivery) | **MIS** for day trades, **no leverage** at first |
| Broker | Zerodha / Upstox / Angel One / Dhan / none | Start with **none** (tickets only), add a broker in Phase 9 |
| Pelosi skill | Insider trades / bulk & block deals / remove | **Insider trades (SEBI PIT)**. Closest in spirit |

---

## 2. Phase 1: shared market rules

Each script has **its own copy** of the US time and calendar code. These
names show up in 8–10 files:

| Code | Files |
|---|---|
| `eastern_offset()`, `to_eastern()`, `eastern_to_utc()`, `_nth_sunday()` | alerts, broker, budget, entry, exit, news, risk, scanner, timemachine |
| `NYSE_HOLIDAYS`, `NYSE_EARLY_CLOSE` | alerts, entry, exit, risk, scanner, timemachine |
| `OPEN = time(9, 30)`, `CLOSE = time(16, 0)`, `EARLY = time(13, 0)` | alerts, entry, exit, risk, scanner, timemachine |
| `"15:55"` (flat-by), `"09:45"` / `"15:30"` (entry window) | budget, exit, alerts, risk |
| `sym.upper().replace(".", "-")` (Yahoo symbol) | alerts, entry, exit, news, scanner, timemachine |
| `"%+03d:00"` offset formatting (`ny_iso`) | broker and others |
| `CENT = Decimal("0.01")` | exit (and cent rounding in budget, broker) |

Find every one of them with:

```powershell
git grep -n -E "eastern_offset|to_eastern|eastern_to_utc|NYSE_|time\(9, ?30\)|time\(16, ?0\)|15:55|replace\(\"\.\", ?\"-\"\)|New York|%\+03d"
```

### 2.1 Replace the time-zone code

IST is a **fixed** UTC+5:30 with no daylight saving, so the US code gets
much simpler. In each file, replace `_nth_sunday`, `eastern_offset`,
`to_eastern` and `eastern_to_utc` with:

```python
IST_OFFSET = datetime.timedelta(hours=5, minutes=30)   # India: UTC+5:30, no daylight saving


def to_ist(utc):
    """naive UTC -> naive India (IST) wall clock."""
    return utc.replace(tzinfo=None) + IST_OFFSET


def ist_to_utc(local):
    """naive IST wall clock -> naive UTC."""
    return local.replace(tzinfo=None) - IST_OFFSET


def ist_iso(now_utc):
    """naive UTC -> "2026-11-06T10:15:00+05:30"."""
    return to_ist(now_utc).replace(microsecond=0).isoformat() + "+05:30"
```

Then rename every call:

| Old | New |
|---|---|
| `to_eastern(x)` | `to_ist(x)` |
| `eastern_to_utc(x)` | `ist_to_utc(x)` |
| `ny_iso(x)` | `ist_iso(x)` |
| `datetime.timedelta(hours=eastern_offset(x))` | `IST_OFFSET` |
| text "New York" / "ET" in messages and help | "IST" / "India time" |
| JSON keys such as `now_new_york` | `now_ist` (update every reader of that key too) |

> **Trap:** don't just make `eastern_offset()` return `5.5`. The
> `"%+03d:00" % off` formatting would print `+05:00` instead of `+05:30`,
> which silently puts every timestamp 30 minutes off. Use `ist_iso()`.

`parse_now()` in each file says "a time without an offset is taken as New
York time". Change it to IST:

```python
if dt.tzinfo is None:
    return ist_to_utc(dt)
```

### 2.2 Session times

Replace the `OPEN` / `CLOSE` / `EARLY` constants with:

```python
PRE_OPEN = datetime.time(9, 0)     # call auction 9:00-9:08, matching until 9:15 (no intraday bars)
OPEN = datetime.time(9, 15)        # NSE regular session
CLOSE = datetime.time(15, 30)
```

Delete `EARLY` and `NYSE_EARLY_CLOSE`, and remove every branch that uses them
(`"early close at 1:00 p.m."`, `before_close` moving 3 hours earlier in
`alerts.py`, and so on). India has no scheduled early closes.

**Muhurat trading:** on Diwali, NSE runs a special session of about one hour
in the evening. Its time is announced in an NSE circular a few weeks before.
If you want to support it, add a dict:

```python
SPECIAL_SESSIONS = {
    # "YYYY-MM-DD": (open, close)   from the NSE circular for that year (verify each year)
}
```

Make `session_times(d)` return the special times when `d` is in it. Otherwise,
just treat Muhurat day as closed. Liquidity is thin and the session is
ceremonial.

### 2.3 Holiday calendar

Replace `NYSE_HOLIDAYS = {...}` with `NSE_HOLIDAYS = {...}` in the same
format, `"YYYY-MM-DD": "Holiday name"`.

**Where to get it:** NSE website → *Resources → Exchange Communication →
Holidays* (`https://www.nseindia.com/resources/exchange-communication-holidays`).
Choose **"Trading Holidays"** and the **Equities** segment. NSE publishes
next year's list in a circular around December.

```python
NSE_HOLIDAYS = {
    # 2026 - copy every row from the official NSE "Trading Holidays" table
    # "2026-01-26": "Republic Day",
    # ...
    # 2027 - add when NSE publishes it
}
```

> Don't guess holiday dates. Several Indian holidays follow the lunar
> calendar and move every year. Holidays that fall on a Saturday or Sunday
> aren't listed and don't matter, because weekends are already closed.

**Better:** keep the holidays in **one** JSON file, `trader/market_india.json`,
and have every script load it. Then you update a single file every December
instead of six Python files (see 2.6).

### 2.4 Tick size (price rounding)

The US code rounds to one cent (`CENT = Decimal("0.01")` in `exit.py`, plus
`round(x, 2)` for prices in budget and broker). NSE uses a tick size that
depends on the price. A price that isn't a multiple of the tick is
**rejected** by the exchange.

The NSE revision that took effect in 2025 (**verify**: NSE circular on tick
sizes, `nseindia.com` → *Circulars*, search "tick size"):

| Share price (₹) | Tick (₹) |
|---|---|
| below 250 | 0.01 |
| 250 – 1,000 | 0.05 |
| 1,000 – 5,000 | 0.10 |
| 5,000 – 10,000 | 0.50 |
| 10,000 – 20,000 | 1.00 |
| above 20,000 | 5.00 |

Put this in config and use one helper everywhere a price is rounded:

```python
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING

TICK_TABLE = [  # (upper price limit, tick) - from config, verify against NSE
    (Decimal("250"), Decimal("0.01")),
    (Decimal("1000"), Decimal("0.05")),
    (Decimal("5000"), Decimal("0.10")),
    (Decimal("10000"), Decimal("0.50")),
    (Decimal("20000"), Decimal("1.00")),
    (None, Decimal("5.00")),
]


def tick_size(price):
    p = Decimal(str(price))
    for limit, tick in TICK_TABLE:
        if limit is None or p < limit:
            return tick


def round_to_tick(price, direction="down"):
    """Round a price to a legal NSE tick. Stops round DOWN (further from a long entry),
    targets round DOWN too (easier to fill), buy limits round UP."""
    p = Decimal(str(price))
    t = tick_size(p)
    mode = ROUND_FLOOR if direction == "down" else ROUND_CEILING
    return (p / t).to_integral_value(rounding=mode) * t
```

Where to use it:

- `trader-exit/exit.py`: everywhere `CENT` is used to round the stop and target.
- `trader-budget/size.py`: the bracket's `stop_price`, `limit_price` and take-profit.
- `trader-broker/ticket.py`: when building the flatten / sell ticket.
- `trader-risk/check.py`: add a check, *"price is not a multiple of the tick"* → BLOCK.

### 2.5 Ticker format (very important)

**Bug you will hit at once:** six scripts turn the symbol into a Yahoo symbol
with `sym.upper().replace(".", "-")` (a US convention: `BRK.B` → `BRK-B`).
For India this turns `RELIANCE.NS` into `RELIANCE-NS`, which Yahoo doesn't
know, so **every price request fails**. Replace each of them with:

```python
import urllib.parse

DEFAULT_SUFFIX = ".NS"   # ".BO" for BSE


def yahoo_symbol(sym):
    """RELIANCE -> RELIANCE.NS, M&M -> M%26M.NS, ^NSEI stays an index."""
    s = sym.strip().upper()
    if not s.startswith("^") and not s.endswith((".NS", ".BO")):
        s += DEFAULT_SUFFIX
    return urllib.parse.quote(s, safe=".-^")
```

The places to change:

| File | Line (approx.) | What's there now |
|---|---|---|
| `trader-alerts/alerts.py` | ~201 | `yahoo_url()` uses `.replace(".", "-")` |
| `trader-entry/entry.py` | ~231 | `return sym.upper().replace(".", "-")` |
| `trader-exit/exit.py` | ~394 | `ysym = sym.upper().replace(".", "-")` |
| `trader-news/news.py` | ~195 | `yahoo_symbol()` |
| `trader-scanner/scan.py` | ~271 | `return sym.upper().replace(".", "-")` |
| `trader-timemachine/timemachine.py` | ~190, ~689 | URL and the symbol comparison |

Indian symbols contain characters the US regexes reject: `M&M`,
`BAJAJ-AUTO`, `3MINDIA` (digits), `MCDOWELL-N` and names longer than 5
letters. Fix the validation regexes:

| File | Now | New |
|---|---|---|
| `trader-risk/check.py:87` | `^[A-Z]{1,5}([.\-][A-Z]{1,2})?$` | `^[A-Z0-9&\-]{1,20}(\.(NS\|BO))?$` |
| `trader-scanner/scan.py:400` | `^[A-Z]{1,5}$\|^[A-Z]{1,4}\.[A-Z]$` | same as above (or remove; see Phase 2) |

Also change the error text in `check.py` (~483): *"does not look like a US
stock or ETF ticker"* → *"does not look like an NSE/BSE stock symbol"*.

**Pick one rule and keep it everywhere:** store symbols in files **with** the
suffix (`RELIANCE.NS`). It's unambiguous, it's what Yahoo needs, and the
broker phase strips it (`RELIANCE.NS` → `RELIANCE` + exchange `NSE`).

### 2.6 (Recommended) one shared settings file

Instead of six copies of the same constants, create
`trader/market_india.json`:

```json
{
  "timezone": "Asia/Kolkata",
  "utc_offset": "+05:30",
  "open": "09:15",
  "close": "15:30",
  "default_suffix": ".NS",
  "currency": "INR",
  "currency_symbol": "₹",
  "holidays": {
    "2026-01-26": "Republic Day"
  },
  "special_sessions": {},
  "tick_table": [[250, 0.01], [1000, 0.05], [5000, 0.10], [10000, 0.50], [20000, 1.00], [null, 5.00]],
  "_note": "Copy holidays from nseindia.com > Resources > Exchange Communication > Holidays. Verify tick sizes against the latest NSE circular."
}
```

Each script reads it at startup (fall back to built-in defaults if it's
missing, which keeps the tests self-contained). The scripts are meant to
stand alone (each skill folder can be installed by itself), so put a short
`load_market()` function in each script rather than a shared module.

### 2.7 Currency text

Find every `$` in an f-string, a `%` format or a message:

```powershell
git grep -n -E "\"\\$|'\\$|\\$%|\\$\{|\\$\s*%" -- "trader-*/*.py"
```

Replace `$` with `₹` (or `Rs.` if your terminal shows `₹` incorrectly. On
Windows, run `chcp 65001` or set `PYTHONIOENCODING=utf-8`).

For large amounts, Indians read **lakh / crore**, not million / billion:

```python
def inr(v):
    """1234567 -> ₹12.35 lakh, 52300000000 -> ₹5,230 crore."""
    a = abs(v)
    if a >= 1e7:
        return "₹{:,.2f} crore".format(v / 1e7)
    if a >= 1e5:
        return "₹{:,.2f} lakh".format(v / 1e5)
    return "₹{:,.2f}".format(v)
```

### 2.8 Phase 1 test

```powershell
cd tests; python -m unittest -v; cd ..
```

Many tests **will now fail**, because they check US times and `$` text.
That's expected. Phase 10 replaces them. For now, check by hand:

```powershell
python trader-alerts/alerts.py status      # should show IST, open 09:15-15:30 on a trading day
```

---

## 3. Phase 2: the stock list and the scanner

### 3.1 How the files connect

```
trader/universe.txt      ← every stock the scanner may check      (INPUT, you build it)
        │
        ▼   python trader-scanner/scan.py daily      (or: orb)
trader/watchlist.json    ← stocks that matched a setup today       (OUTPUT, never edit by hand)
        │
        ▼
trader-news, trader-entry read it
```

**You never write `watchlist.json` yourself.** Rebuild `universe.txt` with
Indian stocks and the scanner writes an Indian `watchlist.json`.

### 3.2 Where to get the stock list

| List | URL | Columns | Size |
|---|---|---|---|
| All NSE equities | `https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv` | `SYMBOL, NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE` | ~2,000+ |
| **Nifty 500** (recommended) | `https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv` | `Company Name, Industry, Symbol, Series, ISIN Code` | 500 |
| Nifty 200 | `https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv` | same | 200 |
| Nifty 100 | `https://nsearchives.nseindia.com/content/indices/ind_nifty100list.csv` | same | 100 |
| Nifty 50 | `https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv` | same | 50 |
| BSE list | `https://www.bseindia.com/corporates/List_Scrips.html` (download button) | Security Code, Id, Name ... | ~5,000 |

**Only keep series `EQ`.** Series `BE` / `BZ` are *trade-for-trade*: every
trade must be delivered, so **intraday trading isn't allowed**, and
including them would give setups you can't day-trade. The Nifty index lists
are already all `EQ`.

> NSE often blocks scripts (HTTP 401/403) that don't look like a browser. If
> the download fails, open the URL in your browser, save the CSV, and pass
> the saved file to the converter with `--file`.

### 3.3 Replace `scan.py universe`

Right now `scan.py universe` downloads Nasdaq Trader's
`nasdaqlisted.txt` / `otherlisted.txt` (`NASDAQ_URL`, `NASDAQ_FILES`,
`TICKER`, the warrant/unit/note filters, all around lines 84–470) and **will
overwrite your Indian list with US stocks**. Replace that subcommand's
download and filter logic:

```python
NSE_LISTS = {
    "all": "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
    "nifty500": "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
    "nifty200": "https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv",
    "nifty100": "https://nsearchives.nseindia.com/content/indices/ind_nifty100list.csv",
    "nifty50": "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv",
}
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Accept": "text/csv,*/*",
    "Referer": "https://www.nseindia.com/",
}


def parse_nse_csv(text):
    """NSE CSV (equity list or index list) -> [(SYMBOL.NS, name)], EQ series only."""
    import csv, io
    rows = list(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))))
    out, seen = [], set()
    for r in rows:
        r = {(k or "").strip().upper(): (v or "").strip() for k, v in r.items()}
        sym = r.get("SYMBOL")
        series = r.get("SERIES", "EQ")
        name = r.get("NAME OF COMPANY") or r.get("COMPANY NAME") or ""
        if not sym or series != "EQ" or sym in seen:
            continue
        seen.add(sym)
        out.append((sym + ".NS", name))
    return sorted(out)


def write_universe(pairs, path):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# NSE equities (EQ series), Yahoo symbols. Built by scan.py universe.\n")
        for sym, name in pairs:
            fh.write("%s|%s\n" % (sym, name.replace("|", " ")))
```

New command line:

```powershell
python trader-scanner/scan.py universe --list nifty500
python trader-scanner/scan.py universe --file C:\Users\antus\Downloads\ind_nifty500list.csv
```

The result, `trader/universe.txt`:

```
# NSE equities (EQ series), Yahoo symbols. Built by scan.py universe.
ADANIENT.NS|Adani Enterprises Ltd.
HDFCBANK.NS|HDFC Bank Ltd.
INFY.NS|Infosys Ltd.
M&M.NS|Mahindra & Mahindra Ltd.
RELIANCE.NS|Reliance Industries Ltd.
TCS.NS|Tata Consultancy Services Ltd.
```

The universe reader (`scan.py` ~line 795–811) already keeps only the text
before `|` and skips `#` lines, so it doesn't need to change.

### 3.4 Scanner filters (defaults)

| Flag | US default | India suggestion | Why |
|---|---|---|---|
| `--min-price` | 5 | **50** | Below ₹50 is mostly penny stocks with wide spreads and circuit risk |
| `--min-volume` | 1,000,000 | **200,000** (Nifty 500) | Indian volumes in shares are lower for many good names |
| (new) `--min-turnover` | none | **₹10 crore** a day (price × volume) | A better liquidity filter for India than share volume |

Change the `argparse` defaults near the bottom of `scan.py` (~line 1040–1055).

### 3.5 Other scanner changes

- **Opening range:** the ORB scan uses 9:30 ET. Change it to 9:15 IST
  (Phase 1 constants). The default `--at 10:15` becomes about `--at 09:45` or
  `10:00`.
- **5-minute grid:** NSE bars start at 9:15 (03:45 UTC). Because of the
  half-hour offset, any code that checks *"is this bar on the hour"* or works
  out bar numbers from UTC must use IST. Look for `% 5`, `minute`, `hour`
  comparisons in `scan_orb()` (~686) and the bar parser (~590–600).
- **Daily "as of" text:** `"2026-11-04 close"` stays the same. Just make sure
  the trading-day check uses `NSE_HOLIDAYS`.
- **Yahoo spark batching:** the scanner fetches many symbols per request.
  This works for `.NS`, but keep the existing pause between requests.
  Yahoo throttles heavy use. Scanning the Nifty 500 takes about a minute.
- **Pre-open movers (optional, new):** NSE's pre-open data
  (`https://www.nseindia.com/api/market-data-pre-open?key=NIFTY`) gives the
  indicative open price at 9:08. That's useful for gap scans, but it is
  browser only (see 7.3).
- **Help text and `SKILL.md`:** replace "US-listed common stocks, ordinary
  shares and ADRs from Nasdaq Trader" with "NSE equities (EQ series) from
  NSE's equity / Nifty index lists".

### 3.6 Phase 2 test

```powershell
python trader-scanner/scan.py universe --list nifty500
python trader-scanner/scan.py daily --symbols RELIANCE.NS,TCS.NS,INFY.NS
python trader-scanner/scan.py daily
Get-Content trader\watchlist.json -TotalCount 40
```

Every `symbol` in `watchlist.json` should end in `.NS`, with prices in rupees.

---

## 4. Phase 3: entry, exit and the Time Machine

These three skills are mostly market-neutral math. Phase 1 already did most
of the work.

### 4.1 trader-entry (`trader-entry/entry.py`)

- Opening range: the first 15 minutes are now **9:15–9:30 IST** (it follows
  `OPEN` automatically once Phase 1 is done. Check any hard-coded `"09:30"`
  or `30` minute values).
- "Not open yet" / "expired" messages: IST times.
- The volume check: the US default may be tuned to US volumes. Make it
  relative (for example, the breakout bar's volume ≥ 1.5× the average of the
  opening-range bars), not an absolute share count.
- Yahoo 1-minute bars exist for the last ~7 days, 5-minute bars for ~60
  days, the same as for US stocks.
- **Yahoo's NSE data can be delayed.** For live entries, the broker's own
  feed (Phase 9) is better. Say so in the output.

### 4.2 trader-exit (`trader-exit/exit.py`)

- `DEFAULTS = {"flat_by": "15:55", ...}` (line ~57) → `"flat_by": "15:10"`.
  Your broker auto-squares-off MIS positions around 15:15–15:25 **(verify
  your broker's exact time)** and charges a fee (about ₹50 + GST at many
  brokers) for doing it. Leave yourself a margin.
- `CENT` (line ~60) → `round_to_tick()` from 2.4. The stop rounds **down**,
  the target rounds **down** (a long target below the exact value fills more
  easily).
- `MIN_RISK_PCT = Decimal("0.05")` (~59): a stop risk under 0.05% is "inside
  the spread". For India, also require the risk to be **at least 2 ticks**,
  and preferably large enough that charges (Phase 4) are under ~10% of the
  risk.
- **Circuit limits:** a stock can freeze at its upper or lower circuit, and
  then a stop can't fill at all. Add a warning when the target is above the
  upper circuit price or the stop is below the lower circuit price. Get the
  band from the broker's quote API (`upper_circuit_limit` /
  `lower_circuit_limit`) or the NSE quote page.
- Messages: `$` → `₹`.

### 4.3 trader-timemachine (`trader-timemachine/timemachine.py`)

- It rebuilds the rest of the day from 60 days of 5-minute bars. The
  session is now **75 bars** (9:15–15:30 = 375 minutes ÷ 5), not 78. Search
  for `78`, `390` (US minutes per session) and `6.5` (hours) and replace
  them with values computed from `OPEN` / `CLOSE`.
- `NYSE_EARLY_CLOSE` (line ~99): remove it (Phase 1).
- Line ~716: `eb["l"] - 0.01 <= entry <= eb["h"] + 0.01` is a one-cent
  tolerance. Use `tick_size(entry)`.
- Line ~689: `if msym and msym != sym.replace(".", "-")`. Compare against
  `yahoo_symbol(sym)` instead (2.5).
- "Neither by the close" means by `flat_by` (15:10), not 16:00. Make the
  simulation end at the flat-by time, because that's when you actually exit.
- **Subtract charges** (Phase 4.4) from every simulated outcome before
  computing average R. On small Indian intraday trades, costs can turn a
  "positive" setup negative, and the honest verdict has to include them.

### 4.4 Phase 3 test

```powershell
python trader-entry/entry.py check RELIANCE.NS --setup orb
python trader-exit/exit.py plan --symbol RELIANCE.NS --entry 1400 --stop or-mid --target 2R
python trader-timemachine/timemachine.py run --symbol RELIANCE.NS
```

Check that every price ends on a legal tick (₹0.10 for a ₹1,400 stock) and
every time is IST.

---

## 5. Phase 4: budget and risk

### 5.1 Config: dollars → rupees

`trader-budget/config.example.json` (also used by trader-risk and the
broker) has `_usd` keys and US defaults. Write a new example:

```json
{
  "currency": "INR",
  "budget": 100000,
  "cash_buffer_pct": 10,
  "max_position_pct": 75,
  "max_order_inr": 80000,
  "min_order_inr": 500,
  "max_loss_per_trade_inr": 1000,
  "max_daily_loss_inr": 3000,
  "max_trades_per_day": 3,
  "max_orders_per_day": 10,
  "min_reward_risk": 1.5,
  "require_stop": true,
  "no_entries_before": "09:30",
  "no_entries_after": "14:45",
  "flat_by": "15:10",
  "exchange": "NSE",
  "product": "MIS",
  "fractional": false,
  "broker": "none",
  "paper": true,
  "blocked": [],
  "allow_options": false,
  "allow_margin": false,
  "allow_short": false,
  "charges": {
    "brokerage_pct": 0.03,
    "brokerage_max_per_order": 20,
    "stt_sell_pct": 0.025,
    "exchange_txn_pct": 0.00307,
    "sebi_per_crore": 10,
    "stamp_buy_pct": 0.003,
    "gst_pct": 18
  }
}
```

Rename the keys in **all** readers (`size.py`, `check.py`, `ticket.py`, the
tests, the SKILL.md files):

| Old key | New key |
|---|---|
| `max_order_usd` | `max_order_inr` |
| `min_order_usd` | `min_order_inr` |
| `max_loss_per_trade_usd` | `max_loss_per_trade_inr` |
| `max_daily_loss_usd` | `max_daily_loss_inr` |
| `pdt_guard` | **delete** (see 5.5) |
| `sec_user_agent` | **delete** (no SEC in India) |
| `broker: "alpaca"` | `"zerodha"`, `"upstox"`, `"angelone"`, `"dhan"` or `"none"` |

```powershell
git grep -n -E "_usd|pdt_guard|sec_user_agent|alpaca" -- "trader-*" "tests"
```

Also update the `DEFAULTS` dicts in `size.py` (~76) and `check.py` (~74):
`"no_entries_before": "09:30"`, `"no_entries_after": "14:45"`,
`"flat_by": "15:10"`.

**Why these times?** The first 15 minutes after 9:15 are the noisiest (and
they're the opening range). After 14:45 there's too little time before the
MIS square-off for a 2R move.

### 5.2 trader-budget (`trader-budget/size.py`)

- **Whole shares only:** this is already the rule (`fractional: false`).
  India has no fractional shares on the exchange.
- **Lot size:** cash equities trade in lots of 1. (F&O has lot sizes, but
  F&O is off.)
- **Charges in the max loss:** the real loss at the stop is
  `qty × (entry − stop) + charges`. Change the "max loss per trade" limit to
  use that total, otherwise every stopped trade loses more than your limit.
- **Order shape:** the file writes one bracket order to `trader/orders.json`
  "in the exact shape of Alpaca's `place_stock_order`". Indian brokers use
  different fields. Phase 9 defines a neutral order shape. Until then, keep
  writing the same JSON but add:

```json
{ "exchange": "NSE", "tradingsymbol": "RELIANCE", "product": "MIS", "currency": "INR" }
```

- **Bracket orders:** Zerodha **discontinued** bracket orders (BO) for
  retail some years ago. Most Indian brokers now use either a **GTT/OCO**
  (good-till-triggered, one-cancels-other), a **cover order** (entry + stop
  only) or separate stop-loss (SL / SL-M) and limit orders placed after the
  fill. Super-orders / forever-orders exist at some brokers. Design the
  sizing output as *entry + stop + target*, and let Phase 9 turn it into
  whatever the chosen broker supports.
- **Leverage:** with `product: MIS`, brokers give intraday leverage (SEBI's
  peak-margin rules cap it at about 5× for equity, **verify**). Keep
  `allow_margin: false` and size on your own cash. Leverage multiplies
  losses as fast as gains.
- Remove the Berkshire / 13F example (`targets.example.json`,
  `tests/fixtures/trader-budget/berkshire-q2-2026-top5.json`) or swap in an
  Indian example (for example, five Nifty 50 stocks with weights).

### 5.3 Indian trading charges (intraday equity) **(verify all)**

| Charge | Rate (intraday equity, NSE) | Charged on |
|---|---|---|
| Brokerage | Usually ₹20 or 0.03% per executed order, whichever is lower (discount brokers) | Each side |
| STT (Securities Transaction Tax) | 0.025% | **Sell** side only |
| Exchange transaction charge (NSE) | 0.00307% (Zerodha's charges page, checked 6 Nov 2026) | Both sides (turnover) |
| SEBI turnover fee | ₹10 per crore (0.0001%) | Both sides |
| Stamp duty | 0.003% | **Buy** side only |
| GST | 18% | On brokerage + exchange charge + SEBI fee |
| Auto-square-off fee (if the broker closes it) | ~₹50 + GST per order (broker-dependent) | Per order |

Sources: your broker's **brokerage calculator** (Zerodha, Upstox, Angel One
and Dhan all publish one), the NSE circular on transaction charges, and the
Union Budget for STT changes.

> For **delivery (CNC)** trades the rates differ: STT is 0.1% on **both**
> sides, stamp duty 0.015% on buy, often zero brokerage, plus a DP charge on
> sell. This guide is about intraday, so use the MIS rates.

### 5.4 The charges function

Add it to `size.py`, `check.py` and `timemachine.py`:

```python
def intraday_charges(qty, buy_price, sell_price, ch):
    """Total ₹ cost of one round trip (buy then sell, same day, NSE equity MIS)."""
    buy_val = qty * buy_price
    sell_val = qty * sell_price
    turnover = buy_val + sell_val

    def brokerage(v):
        return min(v * ch["brokerage_pct"] / 100.0, ch["brokerage_max_per_order"])

    brok = brokerage(buy_val) + brokerage(sell_val)
    stt = sell_val * ch["stt_sell_pct"] / 100.0
    exch = turnover * ch["exchange_txn_pct"] / 100.0
    sebi = turnover * ch["sebi_per_crore"] / 1e7
    stamp = buy_val * ch["stamp_buy_pct"] / 100.0
    gst = (brok + exch + sebi) * ch["gst_pct"] / 100.0
    return round(brok + stt + exch + sebi + stamp + gst, 2)
```

Example: 50 shares bought at ₹1,400, stopped at ₹1,386.
Gross loss = 50 × 14 = ₹700. Charges ≈ ₹40–55. **Real loss ≈ ₹745**.
Size so that *this* number fits `max_loss_per_trade_inr`.

### 5.5 trader-risk (`trader-risk/check.py`)

| Change | Where | What to do |
|---|---|---|
| Ticker regex | line ~87 `TICKER` | New regex from 2.5 |
| Option symbols | line ~86 `OCC` regex | US OCC format. Replace with an NSE F&O pattern (e.g. `NIFTY25NOV24000CE`) like `^[A-Z0-9&\-]+\d{2}[A-Z]{3}\d+(CE\|PE)$` or `...FUT$`, still blocked unless `allow_options` |
| "US stock or ETF" text | ~483 | "NSE/BSE stock" |
| PDT | lines ~54, ~76, ~399, ~627 | **Delete** the `pdt` check and `day_trades()` usage for it. India has no PDT rule |
| Entry window | `DEFAULTS` ~74 | 09:30 / 14:45 / flat-by 15:10 |
| Max loss | the stop-loss check | Use **loss + charges** (5.4) |
| New: tick check | new | BLOCK if any price isn't a multiple of `tick_size()` |
| New: circuit check | new | BLOCK an entry whose limit price is outside the day's circuit band. WARN when the stock is within ~1% of its upper circuit (it can freeze and you can't exit) |
| New: T2T check | new | BLOCK series `BE` / `BZ` (no intraday). Needs the series from the universe file or the broker instrument list |
| New: ASM / GSM | optional | Stocks under SEBI/NSE surveillance (ASM/GSM lists on nseindia.com) have extra margins and restrictions. Block them for intraday |
| New: MIS cutoff | new | BLOCK new MIS entries after the broker's cutoff (often ~15:00–15:15) |
| Short sales | existing | Keep blocked (long-only). Note: in India, intraday shorting **is** allowed in MIS, but delivery short-selling isn't. If you ever enable shorts, allow them **only** with `product: MIS` |
| Crypto | existing | Keep blocked (not on Indian exchanges anyway) |
| Market closed | existing | Uses `NSE_HOLIDAYS` + 9:15–15:30 after Phase 1 |

### 5.6 Phase 4 test

```powershell
Copy-Item trader-budget\config.example.json trader\config.json
python trader-budget/size.py --write
python trader-risk/check.py
```

Check that the output says ₹, includes charges in the max loss, shows no
PDT line, and blocks an order placed at 15:00 or with a price off the tick.

---

## 6. Phase 5: price alerts

File: `trader-alerts/alerts.py`

| Change | Line (approx.) | What to do |
|---|---|---|
| Docstring | ~35–45 | Mentions NYSE and nyse.com. Change to NSE and nseindia.com |
| Holidays / early close | ~66–96 | `NSE_HOLIDAYS`, delete `NYSE_EARLY_CLOSE` and `EARLY` |
| Hours | ~94–95 | 9:15 / 15:30 |
| Time zone | ~111–145 | IST helpers (2.1) |
| Stooq | ~197, ~205–215 | Stooq's Indian coverage is poor. **Remove Stooq** and use Yahoo only, or replace it with your broker's quote API (Phase 9) |
| Yahoo URL | ~201 | `yahoo_symbol()` from 2.5 |
| Opening bell text | ~444–445 | *"Opening bell: the NYSE is open (… New York), early close at 1:00 p.m."* → *"Opening bell: NSE is open (09:15 IST)."* |
| Early-close shift | ~421 | Delete the "moved 3 hours earlier" logic |
| Flat-by default | ~513 | `"15:55"` → `"15:10"` |
| `$` in messages | ~474, ~488, ~578, ~579, ~614 | `₹` |
| Status JSON key | `now_new_york` | `now_ist` |

Optional, useful additions for India:

- **Pre-open alert** at 9:00 ("pre-open has started") and 9:08 (indicative
  open prices are final).
- **MIS square-off warning** at 15:05: "close MIS positions before the
  broker does it at 15:20 (with a fee)".
- **Circuit alert:** ring when a holding is within 1% of its upper or lower
  circuit.

Test:

```powershell
python trader-alerts/alerts.py status
python trader-alerts/alerts.py quote RELIANCE.NS --write
```

---

## 7. Phase 6: news

File: `trader-news/news.py`. Today it uses Yahoo headlines per ticker, SEC
8-K filings and the move from yesterday's close.

### 7.1 Keep: Yahoo headlines

The Yahoo RSS feed works for NSE symbols:
`https://feeds.finance.yahoo.com/rss/2.0/headline?s=RELIANCE.NS&region=IN&lang=en-IN`.
Change the `region` / `lang` parameters if the code sets them to `US`. Fix
`yahoo_symbol()` (~195) as in 2.5.

### 7.2 Replace: SEC 8-K → NSE / BSE corporate announcements

Indian listed companies must report material events to the exchanges under
**SEBI LODR Regulation 30** (results, board meetings, acquisitions,
management changes, credit ratings, orders won, pledges and so on). These are
the Indian equivalent of 8-Ks.

| Source | How | Notes |
|---|---|---|
| **NSE RSS feeds** (easiest) | `https://www.nseindia.com/rss-feed` lists them, e.g. the corporate announcements feed (`https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml`) | Plain XML, all companies. Filter by your symbols. **(verify URL on the RSS page)** |
| NSE JSON API | `https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=RELIANCE` | Browser only (see 7.3): save the CSV instead |
| BSE API | `https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w?strCat=-1&strPrevDate=YYYYMMDD&strScrip=500325&strSearch=P&strToDate=YYYYMMDD&strType=C` | Uses BSE **scrip codes** (RELIANCE = 500325). Needs a browser User-Agent and `Referer: https://www.bseindia.com/` |
| Board meetings / results calendar | `https://www.nseindia.com/api/event-calendar` (browser only) | Replaces "does it report this week" |

Map the categories to the existing tags:

| NSE/BSE subject contains | Tag |
|---|---|
| "Financial Results", "Outcome of Board Meeting" (with results) | `earnings` |
| "Acquisition", "Amalgamation", "Scheme of Arrangement", "Merger" | `deal` |
| "Change in Management", "Resignation", "Appointment" (of MD/CEO/CFO) | `management` |
| "Credit Rating" | `analyst` |
| "Suspension", "Trading Window", "Surveillance" | `halt` / `regulatory` |
| "SEBI", "Show cause", "Penalty", "Litigation" | `regulatory` |
| "Pledge", "Encumbrance" | `promoter` (new tag, important in India) |
| "Bulk Deal", "Block Deal" | `deal_flow` (new tag) |

Replace `TICKERS_URL` (SEC `company_tickers.json`, ~59) with the symbol →
name map from your `universe.txt`, or the NSE equity list. The CIK lookup
disappears.

### 7.3 Data that only works in a browser

Most `nseindia.com/api/...` endpoints answer only a real browser session.
**Do not** script your way past that (harvesting the home page's cookies,
faking a browser): it works around NSE's bot protection, and the converted
pack never does it. Use what NSE publishes openly instead, and let the user
save the rest from their browser:

| Need | Plain public source the pack uses | Browser fallback |
| --- | --- | --- |
| Announcements | `https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml` | CSV from the announcements page, `news.py --announcements-file` |
| Stock lists | `nsearchives.nseindia.com/content/indices/ind_nifty500list.csv`, `.../equities/EQUITY_L.csv` | the saved CSV, `scan.py universe --file` |
| Bulk / block deals | `nsearchives.nseindia.com/content/equities/bulk.csv`, `block.csv` | the history CSV, `insider.py --bulk-file` |
| Insider (PIT) trades | none | CSV from the insider-trading page, `insider.py pit --pit-file` |
| Quarterly results | Yahoo fundamentals timeseries for `.NS` | NSE results JSON / XBRL, `facts.py --file` |

Two things learned while building it:

- **Identify yourself plainly, without a URL.** NSE's archive servers
  silently drop requests whose User-Agent contains a web address. The pack
  sends `Mozilla/5.0 (compatible; day-trader-skill/3.0)` plus `Accept: */*`,
  and NSE answers in well under a second.
- **Always handle the failure gracefully** ("NSE announcements unavailable,
  showing Yahoo headlines only"), the same way the US code handled a SEC
  failure, and tell the user about the browser download.

### 7.4 Times

Announcements are stamped in IST. Show "newest first, in IST" (the current
text says New York time).

Test:

```powershell
python trader-news/news.py --tickers RELIANCE.NS TCS.NS --hours 48
```

---

## 8. Phase 7: earnings analyst

File: `trader-analyst/facts.py`. Today it reads SEC EDGAR XBRL (`us-gaap`
concepts, `USD` units, 10-Q / 10-K / 8-K item 2.02). **None of this exists
for Indian companies**, so this phase is a rewrite of the data layer. The
calculations and report (quarter vs same quarter last year, margins, TTM)
can stay.

### 8.1 Where Indian results come from

| Source | What | How |
|---|---|---|
| **NSE financial results** | Quarterly results filed by every listed company, with XBRL | `https://www.nseindia.com/companies-listing/corporate-filings-financial-results`. API: `https://www.nseindia.com/api/corporates-financial-results?index=equities&symbol=RELIANCE&period=Quarterly` (browser only, see 7.3). Each row links to an **XBRL file** |
| **BSE results** | The same filings | `https://www.bseindia.com/stock-share-price/.../financials-results/` (and BSE's API with scrip code) |
| Yahoo `.NS` fundamentals | Quarterly revenue, profit, EPS | The fundamentals-timeseries endpoint works with a plain request (what `facts.py` uses) |
| Company investor relations pages | Press release / investor presentation PDF | Replaces the 8-K EX-99.1 press release |
| Screener.in, Tijori, Trendlyne | Clean tables | Check their terms before automating. Many forbid scraping. Fine to read by hand |

### 8.2 What's different in the numbers

| Topic | US (SEC) | India |
|---|---|---|
| Taxonomy | `us-gaap:Revenues`, `NetIncomeLoss`, `EarningsPerShareDiluted` | **Ind AS** taxonomy in NSE/BSE XBRL: `RevenueFromOperations`, `ProfitLossForPeriod`, `DilutedEarningsLossPerShareFromContinuingOperations` (names vary by filing. Map them in a table, as the US code does with its concept list) |
| Units | USD | INR, usually reported in **₹ lakh or ₹ crore**. Read the "unit" / decimals in the XBRL, never assume |
| Standalone vs consolidated | One set | **Both** are filed. Use **consolidated** when available (it's what the market reacts to) and say which one you used |
| Fiscal year | Varies | Almost always **April–March**: Q1 = Apr–Jun, Q2 = Jul–Sep, Q3 = Oct–Dec, Q4 = Jan–Mar. "FY27" = April 2026 – March 2027 |
| Q4 | Derived = 10-K minus three 10-Qs | Indian companies **file Q4 results directly** (with the annual numbers), so you rarely need to derive it |
| Deadline | 40/45 days | SEBI LODR: within **45 days** of quarter end, **60 days** for Q4/annual |
| Earnings call | Transcript pasted by the user | Same. Indian companies post transcripts and recordings on their IR pages and on the exchange as an announcement |

### 8.3 Steps

1. Delete `TICKERS_URL`, `FACTS_URL`, `SUBMISSIONS_URL`, `ARCHIVE_URL`,
   `PLACEHOLDER`, `UA_HELP` and the CIK logic (`ticker_ciks`, ~460).
2. Write `fetch_results(symbol)`: list the last 8+ quarterly filings from the
   NSE API (or BSE), download each XBRL, and pull revenue, expenses,
   operating profit, net profit and diluted EPS (consolidated preferred).
3. Build the same `{end: {"value", ...}}` quarter dict the rest of the code
   expects. The margin, YoY and TTM functions (~196–280) then work unchanged.
4. Change the formatting (~304, ~314, ~418): `$x.xxB` → `inr()` from 2.7
   (crore), "diluted EPS ₹xx.xx".
5. Replace the text "no SEC CIK for %s (is it a US filer?)" (~504) with
   "no NSE results found for %s".
6. Replace the fixtures in `tests/fixtures/trader-analyst/` with saved NSE
   responses for two companies.

Test:

```powershell
python trader-analyst/facts.py RELIANCE.NS
```

---

## 9. Phase 8: replace the Pelosi tracker

India has **no rule requiring members of Parliament to disclose their stock
trades** the way the US STOCK Act does, so `trader-pelosi` can't be
converted. Choose one replacement (or remove the skill).

### Option A (recommended): insider and promoter trades (SEBI PIT)

Under the **SEBI (Prohibition of Insider Trading) Regulations, 2015**,
promoters, directors and key employees must disclose trades above a
threshold within 2 trading days, and the company must tell the exchange.
This is the closest equivalent to "people with inside knowledge buying".

| Source | URL |
|---|---|
| NSE insider trading (PIT) page | `https://www.nseindia.com/companies-listing/corporate-filings-insider-trading` |
| NSE API (browser only, see 7.3) | `https://www.nseindia.com/api/corporates-pit?index=equities&from_date=01-11-2026&to_date=06-11-2026` |
| SAST (substantial acquisition, Reg. 29) | `https://www.nseindia.com/companies-listing/corporate-filings-sast` |
| Promoter pledges | `https://www.nseindia.com/companies-listing/corporate-filings-pledged-data` |

Each row has: acquirer/seller name, category (Promoter, Director, KMP...),
security, number of shares, value, mode (market purchase, off-market, ESOP),
holding before/after, date of trade and date of disclosure. That maps well
onto the current output (who, owner, stock, buy/sell, amount, days to
disclose).

### Option B: bulk and block deals

Large trades (≥0.5% of a company's shares in a day = bulk deal; ≥₹10 crore
in the block window = block deal, **verify thresholds**) are published
daily with the buyer's and seller's names (funds, FIIs, well-known
investors).

| Source | URL |
|---|---|
| Bulk deals (daily CSV) | `https://nsearchives.nseindia.com/content/equities/bulk.csv` |
| Block deals (daily CSV) | `https://nsearchives.nseindia.com/content/equities/block.csv` |
| Historical | `https://www.nseindia.com/report-detail/display-bulk-and-block-deals` |

This option suits a "follow the big investors" skill. Track named investors
(for example well-known individual investors or specific mutual funds) the
way the Pelosi skill tracks one politician.

### Steps (either option)

1. Rename the folder, e.g. `trader-pelosi` → `trader-insider`, and update
   `.claude-plugin/plugin.json`, `marketplace.json`, `README.md` and
   `tests/test_repo.py` (which probably checks the skill list).
2. Replace `INDEX_URL`, `PTR_URL`, `FD_URL` (`ptr.py` ~48–50) and the PDF
   parsing (~200–260) with a JSON/CSV reader. That's much simpler than PDF
   parsing.
3. Keep the "alert once per new filing" state logic and the "flags trades in
   stocks you hold" logic. Both still apply.
4. Rewrite `SKILL.md`: triggers like "what are promoters buying", "insider
   buying in RELIANCE", "bulk deals today".
5. Replace `tests/test_pelosi.py` and its fixtures (the House `2026FD.zip`
   and PTR text files) with saved NSE PIT / bulk-deal responses.

---

## 10. Phase 9: broker

File: `trader-broker/ticket.py`, plus `claude_desktop_config.example.json`
and `SKILL.md`. Today this skill shows a ticket, records your exact "yes",
and hands Claude the JSON for **Alpaca's** `place_stock_order` MCP tool.

### 10.1 Pick a broker

| Broker | API | Cost **(verify)** | Paper trading | Notes |
|---|---|---|---|---|
| **Zerodha** | Kite Connect (`kite.trade`), Python `kiteconnect` | Monthly fee for Connect (a cheaper/free "personal" tier has existed without market data, check current plans) | No sandbox | Most popular. Zerodha also offers an official **Kite MCP server** (`mcp.kite.trade`). Check whether it can place orders and how approval works today |
| **Upstox** | Upstox API v2/v3, `upstox-python-sdk` | Free for individuals (check) | Has offered a **sandbox** for order APIs (check) | Good free option |
| **Angel One** | SmartAPI, `smartapi-python` | Free | No | Free market data with the account |
| **Dhan** | DhanHQ API, `dhanhq` | Trading API free; data API may be paid | No | Has "super orders" (entry + target + stop together) |

### 10.2 Indian API rules that affect the design

- **Daily login:** an API access token **expires every day** (around 6 a.m.
  IST at most brokers) and needs a fresh login, usually with TOTP. Build a
  `ticket.py login` helper that prints the login URL and stores the token in
  an **environment variable or a local file outside the repo**. Never put
  it in chat or git.
- **SEBI retail algo-trading rules:** SEBI's framework for retail API
  trading (circular of February 2025, phased in from 2025, **verify the
  current status**) requires, among other things, orders through APIs to come
  from a **registered static IP**, limits orders per second, and requires
  strategies above a threshold to be registered with the exchange.
  Human-approved one-at-a-time orders like this skill's are the
  lowest-risk case, but read your broker's current API terms.
- **No paper trading at most brokers.** Keep the existing **"ticket only"**
  mode as the default: the skill builds the ticket, logs the "yes", and the
  user places the order in the broker app. Add a local **paper simulator**
  (record "fills" at the next quote into `trader/portfolio.json`) if you want
  practice without money. The fill recorder (`ticket.py fill`) already does
  most of this.
- **Bracket orders:** see 5.2. Map the plan to the broker:
  - Zerodha: a LIMIT/MARKET `MIS` buy, then after the fill a **GTT OCO**
    (stop + target). GTT triggers place a new order, so they're not
    guaranteed in a fast market. Or use an SL-M order for the stop and a
    LIMIT order for the target, and cancel one when the other fills.
  - Dhan: super order. Upstox / Angel One: check their current
    bracket/cover/OCO support.

### 10.3 A neutral order shape

Change `trader-budget` (5.2) to write this, and `ticket.py` to translate it
for the chosen broker:

```json
{
  "symbol": "RELIANCE.NS",
  "exchange": "NSE",
  "tradingsymbol": "RELIANCE",
  "side": "BUY",
  "qty": 50,
  "order_type": "LIMIT",
  "price": 1400.10,
  "product": "MIS",
  "validity": "DAY",
  "stop_loss": 1386.00,
  "target": 1428.20,
  "max_loss_inr": 745.00,
  "charges_estimate_inr": 45.00,
  "flat_by": "15:10"
}
```

Translate it, for example for Zerodha Kite Connect (`place_order`):

```python
def to_kite(o):
    return {
        "variety": "regular",
        "exchange": o["exchange"],
        "tradingsymbol": o["tradingsymbol"],
        "transaction_type": o["side"],
        "quantity": o["qty"],
        "product": o["product"],          # MIS / CNC
        "order_type": o["order_type"],    # MARKET / LIMIT / SL / SL-M
        "price": o.get("price"),
        "validity": o["validity"],
    }
```

### 10.4 Changes in `ticket.py`

| Change | Where (approx.) |
|---|---|
| `STRING_FIELDS` / allowed fields (Alpaca's) | ~45–48 → the neutral shape above |
| Time helpers | ~66–110 → IST (2.1) |
| Ticket text: `BUY 8 TSLA, market ... max loss about $46.88` | ~215–255 → `BUY 50 RELIANCE (NSE, MIS), limit ₹1,400.10 / stop ₹1,386.00 / target ₹1,428.20 / max loss about ₹745 incl. charges` |
| "PAPER account" / "LIVE ACCOUNT" | ~356 → "TICKET ONLY (place it yourself)" / "LIVE - <broker>" |
| `flatten` | Sell ticket with `product` MIS, before `flat_by` 15:10 |
| Keep unchanged | The exact-"yes" approval rules (~272–285), "one order at a time", requiring a trader-risk PASS. These safety rules matter even more without a paper account |
| `claude_desktop_config.example.json` | Replace the Alpaca MCP block with the chosen broker's MCP server (e.g. Zerodha's Kite MCP) or remove it. Keys go in environment variables only |
| Order tools | In Claude, set the broker's order-placing tools to **"Needs approval"**, the same as for Alpaca |

---

## 11. Phase 10: tests and fixtures

The tests in `tests/` use saved US data (TSLA, AAPL, KO, SEC filings, House
PDFs). After the conversion, they must use Indian data.

### 11.1 Replace the fixtures

| Folder | Replace with | How to get it |
|---|---|---|
| `tests/fixtures/trader-alerts/` | `yahoo-RELIANCE.NS.json`, `yahoo-TCS.NS.json`, an unknown symbol (`yahoo-ZZZZ.NS.json`). Delete `stooq-*` if Stooq is removed | `https://query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?range=1d&interval=1d` saved to a file |
| `tests/fixtures/trader-entry/` | One real NSE day of 5-minute bars, e.g. `RELIANCE.NS-2026-11-05-5m.json`, keep the synthetic DEMO file but shift it to 9:15 IST | `...chart/RELIANCE.NS?range=1d&interval=5m` |
| `tests/fixtures/trader-scanner/` | Replace `symdir/nasdaqlisted.txt` / `otherlisted.txt` with a small `EQUITY_L.csv` (include a `BE` row to test the filter) and an index CSV. New spark / 5m files for `.NS` symbols | NSE URLs from 3.2, Yahoo spark |
| `tests/fixtures/trader-timemachine/` | `RELIANCE.NS-60d5m.json.gz` | `...chart/RELIANCE.NS?range=60d&interval=5m`, then gzip |
| `tests/fixtures/trader-budget/`, `trader-risk/` | `plan-reliance.json`, `orders-reliance.json` in rupees, on NSE ticks | Run the converted exit / budget skills and save the output |
| `tests/fixtures/trader-news/` | A Yahoo RSS for `RELIANCE.NS`, an NSE announcements RSS/JSON sample. Delete the `sec-8-K-*.xml` and `company_tickers.json` | 7.1, 7.2 |
| `tests/fixtures/trader-analyst/` | Saved NSE financial-results JSON + XBRL for two companies | 8.1 |
| `tests/fixtures/trader-pelosi/` | Saved NSE PIT JSON or `bulk.csv` | 9 |

Test fixtures are allowed to contain real public data. Keep them small (a few
KB where possible).

### 11.2 Update the test files

For each `tests/test_*.py`:

1. Replace symbols (`TSLA` → `RELIANCE.NS`, and so on) and fixture names.
2. Replace expected times: anything at 9:30/10:15/15:55/16:00 New York →
   9:15/9:45/15:10/15:30 IST. `--now "2026-10-19 10:15"` inputs now mean IST.
3. Replace expected text: `$` → `₹`, "New York" → "IST", "NYSE" → "NSE".
4. Delete tests for removed features: early closes, PDT, Stooq, SEC CIK
   lookup, House PTR PDF parsing.
5. **Add tests for the new rules:**
   - `round_to_tick()`: ₹249.99 → tick 0.01, ₹1,400.07 rounds down to
     ₹1,400.00, ₹5,000 → tick 0.50.
   - `yahoo_symbol()`: `RELIANCE` → `RELIANCE.NS`, `M&M` → `M%26M.NS`,
     `^NSEI` unchanged, `TCS.BO` unchanged.
   - `ist_iso()`: ends in `+05:30`, never `+05:00`.
   - Market status: closed on an NSE holiday, open at 9:15, closed at 15:30.
   - `intraday_charges()` against a hand-checked broker calculator example.
   - Risk: BLOCK off-tick price, BLOCK entry after 14:45, BLOCK `BE` series,
     max loss includes charges, no PDT output.
   - Universe parser: `BE` rows dropped, headers with spaces handled, `.NS`
     added.
6. `tests/test_repo.py`: update the skill names and any README/plugin
   checks.

Run:

```powershell
cd tests; python -m unittest -v; cd ..
```

All tests must pass before you trade anything.

---

## 12. Phase 11: documentation

Every skill's `SKILL.md` is what Claude reads to decide when and how to use
the skill. **If the SKILL.md still says NYSE, $ and Alpaca, Claude will give
US instructions even with India code.**

For each `trader-*/SKILL.md`:

1. Front-matter `description:` → NSE, IST, ₹, the Indian data sources, and
   Indian trigger phrases ("scan Nifty 500", "is NSE open", "alert me when
   RELIANCE hits 1450", "Muhurat timing").
2. Body: every US example → Indian example (`RELIANCE.NS`, ₹ amounts,
   9:15–15:30, flat by 15:10).
3. Remove Alpaca, SEC, House Clerk, Nasdaq Trader, PDT, early closes.
4. Add the India safety notes: MIS auto-square-off, circuits, charges, T2T
   stocks, the daily broker login.

Also update:

- `README.md`: description, data sources ("Yahoo Finance (NSE `.NS`), NSE
  equity lists, NSE/BSE corporate filings"), the command examples (README
  lines ~129–138), the daily routine.
- `.claude-plugin/plugin.json` and `marketplace.json`: description, keywords
  (`nse`, `bse`, `indian-stock-market`, `nifty`), version → `3.0.0-india`.
  Keep the original author credit and the MIT `LICENSE`.

Find leftovers:

```powershell
git grep -n -i -E "nyse|nasdaq|new york|\bET\b|alpaca|edgar|\bsec\b|8-k|10-q|stock act|pelosi|pdt|\\$[0-9]|usd|tsla|aapl"
```

When that search shows nothing (except intentional mentions such as the
original author credit), the conversion is complete.

---

## 13. Your daily routine after the conversion

All times IST.

| Time | Step | Command |
|---|---|---|
| Once a month | Refresh the stock list | `python trader-scanner/scan.py universe --list nifty500` |
| Each December | Add next year's NSE holidays (and tick sizes / charges if changed) | edit `trader/market_india.json` |
| 08:30 | Broker login (daily token) | `python trader-broker/ticket.py login` (Phase 9) |
| 08:30–09:00 | Pre-market scan of yesterday's daily closes | `python trader-scanner/scan.py daily` |
| 08:45 | News on the watchlist | `python trader-news/news.py --hours 18` |
| 09:15–09:30 | Opening range forms. **Don't trade** | - |
| 09:30+ | Watch for the breakout | `python trader-entry/entry.py watch RELIANCE.NS --every 30` |
| On trigger | Stop and target | `python trader-exit/exit.py plan --stop or-mid --target 2R --write` |
| | Odds, including charges | `python trader-timemachine/timemachine.py run --write` |
| | Size in ₹ | `python trader-budget/size.py --write` |
| | Risk check | `python trader-risk/check.py` |
| | Ticket, your "yes", place | `python trader-broker/ticket.py show` |
| | Alerts | `python trader-alerts/alerts.py from-plan` (then `check` every few minutes) |
| 14:45 | No new entries after this | (enforced by trader-risk) |
| **15:10** | **Be flat** (before the broker's MIS square-off) | `python trader-broker/ticket.py flatten --symbol RELIANCE.NS --qty 50` |
| 15:30 | Market closes | - |

---

## 14. Final checklist

**Phase 1: market rules**
- [ ] IST helpers in every script; no `eastern_offset`, `to_eastern`, `_nth_sunday` left
- [ ] Timestamps end in `+05:30`
- [ ] `OPEN` 9:15, `CLOSE` 15:30; `EARLY` / early-close code removed
- [ ] `NSE_HOLIDAYS` copied from the official NSE page (current and next year)
- [ ] `round_to_tick()` used for every price; tick table in config
- [ ] `yahoo_symbol()` replaces every `.replace(".", "-")`
- [ ] Ticker regexes accept `M&M`, `BAJAJ-AUTO`, `3MINDIA`, `.NS`, `.BO`
- [ ] `$` → `₹` in all output; lakh/crore formatting for large values

**Phase 2: scanner**
- [ ] `scan.py universe` builds `universe.txt` from NSE (no Nasdaq Trader code left)
- [ ] Only series `EQ`
- [ ] Filters: min price ₹50, min volume 200,000 (or a turnover filter)
- [ ] ORB uses 9:15 IST

**Phase 3: entry / exit / Time Machine**
- [ ] Opening range 9:15–9:30
- [ ] Flat-by 15:10
- [ ] Session = 75 five-minute bars; no `78` / `390` left
- [ ] Charges subtracted in the simulation

**Phase 4: budget / risk**
- [ ] Config uses `_inr` keys; `pdt_guard`, `sec_user_agent` removed
- [ ] `intraday_charges()` in place; max loss includes charges
- [ ] Entry window 09:30–14:45
- [ ] New checks: tick, circuit, T2T series, MIS cutoff
- [ ] PDT check removed

**Phase 5–8: alerts / news / analyst / insider**
- [ ] Alerts: NSE calendar, Stooq removed or replaced, Indian messages
- [ ] News: NSE/BSE announcements instead of 8-Ks; NSE failure handled gracefully
- [ ] Analyst: NSE/BSE results, Ind AS concepts, consolidated, ₹ crore, April–March fiscal year
- [ ] Pelosi replaced (insider/PIT or bulk deals) or removed; plugin files updated

**Phase 9: broker**
- [ ] Broker chosen; neutral order shape; translation function
- [ ] Daily login handled; tokens never in chat or git
- [ ] "Ticket only" is the default; exact-"yes" rule and risk PASS still required
- [ ] Broker order tools set to "Needs approval" in Claude

**Phase 10–11: tests / docs**
- [ ] All fixtures Indian; all tests pass (`python -m unittest -v` in `tests/`)
- [ ] New tests for tick, symbol, IST, charges, risk rules
- [ ] Every `SKILL.md`, `README.md`, `plugin.json`, `marketplace.json` updated
- [ ] The leftover search in section 12 finds nothing

**Before real money**
- [ ] At least 2–4 weeks in "ticket only" / paper mode, comparing tickets with what really happened
- [ ] Charges checked against your broker's calculator for 3 real trades
- [ ] Holidays, tick sizes, MIS square-off time and charges verified against official sources

---

## 15. Reference

### 15.1 Indian market facts **(verify before relying on them)**

| Item | Value | Official source |
|---|---|---|
| Time zone | IST, UTC+5:30, no daylight saving | - |
| Pre-open session | 9:00–9:15 (orders 9:00–9:08) | NSE: *Market timings* |
| Regular session | 9:15–15:30 | NSE |
| Closing session | 15:40–16:00 (at the closing price) | NSE |
| Settlement | T+1 (optional T+0 for some stocks) | SEBI |
| Price bands (circuits) | 2%, 5%, 10%, 20% per stock; F&O stocks have dynamic bands | NSE: price band list (daily) |
| Market-wide circuit breakers | Nifty / Sensex move of 10%, 15%, 20% halts trading | SEBI/NSE |
| Intraday leverage | Up to ~5× (20% margin) for equity, broker-dependent | SEBI peak margin rules, broker |
| MIS auto-square-off | ~15:15–15:25, broker-dependent | Your broker |
| Holidays | Published yearly | nseindia.com → Resources → Exchange Communication → Holidays |
| Tick sizes | Price-band table (section 2.4) | NSE circulars (search "tick size") |
| Charges | Section 5.3 | Broker calculator, NSE circulars, Union Budget (STT) |
| Indices on Yahoo | `^NSEI` (Nifty 50), `^NSEBANK` (Bank Nifty), `^BSESN` (Sensex), `^CNX100`, `^CRSLDX` (Nifty 500) | - |

### 15.2 Data sources

| Need | Source | Access |
|---|---|---|
| Prices (daily, 1m, 5m) | Yahoo Finance `query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS` | Free, may be delayed, unofficial |
| Live prices for trading | Your broker's API / websocket | With the broker account |
| Stock list | `nsearchives.nseindia.com/content/equities/EQUITY_L.csv`, Nifty index CSVs | Free CSV |
| Holidays | nseindia.com holidays page | Free |
| Corporate announcements | NSE RSS (`nseindia.com/rss-feed`), NSE API, BSE API | Free; NSE pages are browser only (7.3) |
| Financial results | NSE / BSE financial-results pages and XBRL | Free; NSE pages are browser only (7.3) |
| Insider trades | NSE PIT page / API | Free; browser download (7.3) |
| Bulk / block deals | `nsearchives.nseindia.com/content/equities/bulk.csv`, `block.csv` | Free CSV |
| Surveillance lists (ASM/GSM) | nseindia.com → Regulations → Surveillance | Free |
| Broker APIs | Kite Connect, Upstox API, Angel One SmartAPI, DhanHQ | Account required |

### 15.3 Search commands used in this guide

```powershell
# every US time/calendar/symbol leftover
git grep -n -E "eastern_offset|to_eastern|eastern_to_utc|NYSE_|time\(9, ?30\)|time\(16, ?0\)|15:55|replace\(\"\.\", ?\"-\"\)|New York|%\+03d"

# dollar keys and US-only features
git grep -n -E "_usd|pdt_guard|sec_user_agent|alpaca" -- "trader-*" "tests"

# final leftover check
git grep -n -i -E "nyse|nasdaq|new york|\bET\b|alpaca|edgar|\bsec\b|8-k|10-q|stock act|pelosi|pdt|\\$[0-9]|usd|tsla|aapl"
```

---

*This repo is education and tooling, not investment advice. Most intraday
traders lose money once charges are counted. Keep paper / ticket-only mode
on until the numbers say otherwise, and never let any tool place an order
without your explicit yes.*

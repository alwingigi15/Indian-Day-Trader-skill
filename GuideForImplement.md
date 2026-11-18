# Guide for Implement: set up and use the Indian Day Trader skills

A simple, step-by-step guide to installing these skills on your Windows
computer and using them every trading day on NSE.

You need: a Windows PC, Python, and Claude Code. No broker account is needed
to start: you can practise first.

---

## Contents

1. [What you get](#1-what-you-get)
2. [One-time setup](#2-one-time-setup-about-10-minutes)
3. [The config file (your trading rules)](#3-the-config-file-your-trading-rules)
4. [Every trading day](#4-every-trading-day-step-by-step)
5. [Extra tools you can use any time](#5-extra-tools-you-can-use-any-time)
6. [Things to update now and then](#6-things-to-update-now-and-then)
7. [Safety rules](#7-safety-rules)
8. [Problems and fixes](#8-problems-and-fixes)
9. [Quick checklist](#9-quick-checklist)

---

## 1. What you get

11 skills that work together for intraday trading on NSE:

| Skill | What it does for you |
|---|---|
| `/trader-scanner` | Scans the Nifty 500 and finds stocks with setups (breakouts, flags, oversold) |
| `/trader-news` | Shows news and NSE announcements for the stocks you watch |
| `/trader-entry` | Watches a stock bar by bar and tells you when to buy |
| `/trader-exit` | Sets your stop-loss and target before you buy |
| `/trader-timemachine` | Replays the trade 10,000 times to show the honest odds (after charges) |
| `/trader-budget` | Tells you how many shares to buy so your loss stays small |
| `/trader-risk` | Checks the order against your rules and blocks anything unsafe |
| `/trader-broker` | Shows the order ticket and waits for your "yes" |
| `/trader-alerts` | Rings when your stop, target or 15:10 closing time is hit |
| `/trader-analyst` | Explains a company's quarterly results |
| `/trader-insider` | Shows bulk/block deals and promoter buying/selling |

All times are India time (IST). All money is in rupees (₹).

---

## 2. One-time setup (about 10 minutes)

### Step 1: Unzip the project

Extract `indian-day-trader-skill.zip`. In this guide the folder is:

```
C:\Users\antus\indian-day-trader-skill
```

If you put it somewhere else, use your own path in the commands below.

### Step 2: Check Python is installed

Open **PowerShell** (press Start, type `PowerShell`, press Enter) and type:

```powershell
python --version
```

- If you see `Python 3.8` or higher: good, go to Step 3.
- If you see an error: install Python from https://www.python.org/downloads/
  and tick **"Add Python to PATH"** during installation. Then close and
  reopen PowerShell and try again.

Nothing else needs to be installed. The tools use only standard Python.

### Step 3: Check Claude Code is installed

```powershell
claude --version
```

If this fails, install Claude Code first (see https://claude.com/claude-code).

### Step 4: Test that everything works (optional but recommended)

```powershell
cd C:\Users\antus\indian-day-trader-skill
python -m unittest discover -s tests
```

At the end you should see `OK`. That means all 386 checks passed on your
computer.

### Step 5: Install the skills into Claude

```powershell
Copy-Item -Recurse C:\Users\antus\indian-day-trader-skill\trader-* $HOME\.claude\skills\
```

Close and reopen Claude Code. Now commands like `/trader-scanner` are
available.

### Step 6: Make your trading folder

This is where your watchlist, plans and orders are saved (inside a `trader`
sub-folder). Run these one by one:

```powershell
mkdir C:\Users\antus\my-trading
Copy-Item -Recurse C:\Users\antus\indian-day-trader-skill\trader C:\Users\antus\my-trading\
```

This also copies a ready-made list of Nifty 500 stocks (`universe.txt`) and
the holiday file (`market_india.json`).

### Step 7: Create your config file

See section 3 right below. This is the most important setup step.

### Step 8: Start Claude in your trading folder

Every time you trade, start Claude from this folder:

```powershell
cd C:\Users\antus\my-trading
claude
```

Setup done.

---

## 3. The config file (your trading rules)

### What is it?

`trader\config.json` is a small file with **your personal trading rules**:
how much money you trade with, how much you are willing to lose, and at what
times you may trade.

The skills read it to:

- **Budget**: decide how many shares to buy.
- **Risk check**: block any trade that breaks your rules.
- **Broker**: know if it only makes tickets (default) or prepares Zerodha orders.
- **Alerts and exit**: know your closing time (15:10).

### Is it compulsory?

**Yes, before you size or check a real trade.**

| Without config.json | Works? |
|---|---|
| Scanner, news, entry, exit, alerts, analyst, insider | Yes (uses built-in defaults) |
| **Budget** (how many shares) | **No**: it stops with "no budget: pass --budget or set budget in trader/config.json" |
| **Risk check** | **No**: it also needs your budget |

Budget and risk are what protect your money, so create the file once at the
start.

### How to create it

You do not write it from scratch. A ready example comes with the project.

**Option 1: copy the example (recommended)**

```powershell
Copy-Item C:\Users\antus\indian-day-trader-skill\trader-budget\config.example.json C:\Users\antus\my-trading\trader\config.json
notepad C:\Users\antus\my-trading\trader\config.json
```

Notepad opens. Change the values (see the table below), then **File > Save**.

**Option 2: ask Claude**

Start Claude in `C:\Users\antus\my-trading` and say:

> set my rules: budget 50000, max loss 500 per trade, max 1500 a day

Claude copies the example and fills in your numbers.

### What each setting means

The most important ones first:

| Setting | Example value | Meaning |
|---|---|---|
| `budget` | `100000` | Money you trade with, in ₹ (here ₹1 lakh) |
| `max_loss_per_trade_inr` | `1000` | The most you lose on ONE trade if the stop is hit (charges included) |
| `max_daily_loss_inr` | `3000` | The most you lose in ONE day |
| `max_trades_per_day` | `3` | How many trades you may take in a day |
| `cash_buffer_pct` | `10` | Percent of the budget that always stays in cash |
| `max_position_pct` | `75` | The biggest single position, as a percent of the budget |
| `max_order_inr` | `80000` | The biggest single order in ₹ |
| `min_order_inr` | `500` | The smallest order in ₹ |
| `min_reward_risk` | `1.5` | The target must be at least 1.5 times the risk |
| `require_stop` | `true` | Every trade must have a stop-loss (keep this true) |
| `no_entries_before` | `"09:30"` | No buying before this time (the opening range forms 9:15-9:30) |
| `no_entries_after` | `"14:45"` | No buying after this time |
| `mis_entry_cutoff` | `"15:00"` | No new intraday (MIS) trades after this time |
| `flat_by` | `"15:10"` | Close every trade by this time, before the broker squares you off |
| `exchange` | `"NSE"` | The exchange |
| `product` | `"MIS"` | MIS = intraday |
| `broker` | `"none"` | `none` = only make tickets, you place orders yourself (safest) |
| `paper` | `true` | Keep `true` while practising |
| `blocked` | `[]` | Stocks you never want to trade, e.g. `["YESBANK", "IDEA"]` |
| `allow_options` / `allow_margin` / `allow_short` | `false` | Keep all `false` |
| `charges` | (see below) | Brokerage and tax rates used to calculate the real loss |

### The charges part

```json
"charges": {
  "brokerage_pct": 0.03,
  "brokerage_max_per_order": 20,
  "stt_sell_pct": 0.025,
  "exchange_txn_pct": 0.00307,
  "sebi_per_crore": 10,
  "stamp_buy_pct": 0.003,
  "gst_pct": 18
}
```

These are a discount broker's intraday rates (Zerodha's charges page, checked
6 Nov 2026): brokerage 0.03% or ₹20 per order (whichever is lower), STT
0.025% on the sell, NSE fee 0.00307%, SEBI ₹10 per crore, stamp duty 0.003%
on the buy, GST 18%. Open your broker's **brokerage calculator** on their
website and change any number that differs.

### Rules when editing

- Text values keep their quotes: `"09:30"`, `"none"`, `"NSE"`.
- Numbers have no quotes: `100000`, `1000`, `1.5`.
- `true` and `false` have no quotes.
- Every line ends with a comma, except the last line before a `}`.
- If you break the file, just copy the example again (Option 1).

### A good starting setup for beginners

If you are new, start small. Example for ₹50,000:

| Setting | Value |
|---|---|
| `budget` | `50000` |
| `max_loss_per_trade_inr` | `500` (1% of the budget) |
| `max_daily_loss_inr` | `1500` |
| `max_trades_per_day` | `2` |
| `max_order_inr` | `40000` |

You create this file **once**. All skills use it every day after that.

---

## 4. Every trading day, step by step

Start Claude in your trading folder first:

```powershell
cd C:\Users\antus\my-trading
claude
```

You can **talk to Claude** (easiest) or **type the command yourself**. In
the commands, `TOOLS` means `C:\Users\antus\indian-day-trader-skill`, so for
example `python TOOLS\trader-scanner\scan.py daily` becomes
`python C:\Users\antus\indian-day-trader-skill\trader-scanner\scan.py daily`.
Always run commands from `C:\Users\antus\my-trading`.

### Before the market opens (8:30 to 9:15)

**Step 1: Scan for setups (8:30)**

- Say: `scan the market`
- Or: `python TOOLS\trader-scanner\scan.py daily`

You get a short list of stocks with setups, saved in `trader\watchlist.json`.
A scan is a list of charts to look at, not a list of stocks to buy.

**Step 2: Check the news (8:45)**

- Say: `any news on my watchlist?`
- Or: `python TOOLS\trader-news\news.py`

Skip a stock if it has results or big news today: it can jump past any stop.

### The opening (9:15 to 9:30)

**Step 3: Wait.** The first 15 minutes make the "opening range". Do not trade.

### Finding the trade (from 9:30)

**Step 4: Watch for the breakout**

- Say: `watch RELIANCE, tell me when to get in`
- Or: `python TOOLS\trader-entry\entry.py watch RELIANCE.NS --every 30`

It says **waiting**, **triggered** (time to buy) or **expired**. Always
confirm on your broker's live chart: free Yahoo data can be a little late.

**Step 5: Set the stop and target (BEFORE you buy)**

- Say: `set my stop and target`
- Or: `python TOOLS\trader-exit\exit.py plan --write`

**Step 6: Check the odds**

- Say: `run the time machine`
- Or: `python TOOLS\trader-timemachine\timemachine.py run`

If it says the trade is close to a coin flip or worse after charges, think
twice.

**Step 7: How many shares?**

- Say: `how many shares should I buy?`
- Or: `python TOOLS\trader-budget\size.py --write`

It uses your config to keep the loss (charges included) inside your limit.

**Step 8: Risk check**

- Say: `check my order`
- Or: `python TOOLS\trader-risk\check.py`

**PASS** means the order fits your rules. **BLOCK** means it does not: read
the reason. Never change your limits just to make a trade pass.

**Step 9: See the ticket and say yes**

- Say: `show the ticket`
- Or: `python TOOLS\trader-broker\ticket.py show`

Read the ticket. Reply **yes** to approve this one order, or **no** to skip.
Words like "ok" or "sure" are not accepted on purpose.

**Step 10: Place the order yourself**

Open your broker app (Zerodha, Upstox, Angel One, Dhan...) and enter exactly
what the ticket says:

1. BUY, the stock, quantity, product **MIS**, MARKET (or LIMIT).
2. After it fills, add the **stop** as an **SL-M sell** and the **target** as
   a **LIMIT sell** (or one **GTT OCO** with both).
3. When one of them fills, **cancel the other** straight away.

Then tell Claude what happened, for example:
`I placed it, order id 251106000123, filled 57 at 1312`.
Claude records it, so your daily limits stay correct.

**Step 11: Turn on alerts**

- Say: `watch my trade`
- Or: `python TOOLS\trader-alerts\alerts.py add --from-plan`

Then ask Claude to check every few minutes (for example `/loop 5m` with
`check my alerts`), or run `python TOOLS\trader-alerts\alerts.py check`.

### End of the day

**14:45**: no new trades after this (the risk check blocks them).

**Step 12: Close everything by 15:10**

- Say: `flatten RELIANCE`
- Or: `python TOOLS\trader-broker\ticket.py flatten --symbol RELIANCE --qty 57`

Then check it, say yes, cancel your open stop/target orders, and sell in the
app. After 15:15-15:25 the broker squares off intraday positions itself and
charges a fee (about ₹50 + GST per order).

**15:30**: market closes.

---

## 5. Extra tools you can use any time

| What you want | Say to Claude |
|---|---|
| Is the market open today? | `is NSE open?` |
| Price of a stock | `get me the price of TCS` |
| Company results explained | `how did Reliance's results look?` |
| Today's big investor deals | `show today's bulk deals` |
| One investor's deals | `track kacholia bulk deals` |
| Promoter/insider buying | first download the CSV (see below), then `what are promoters buying?` |
| Rebuild the stock list | `rebuild the stock list from Nifty 500` |

**Promoter / insider trades:** NSE shows these only in a web browser. Open
https://www.nseindia.com/companies-listing/corporate-filings-insider-trading,
click the CSV download, and tell Claude where you saved it.

---

## 6. Things to update now and then

| When | What | How |
|---|---|---|
| **Once a month** | Refresh the Nifty 500 list | Say `rebuild the stock list from Nifty 500` |
| **Every December** | Add next year's NSE holidays | Open `trader\market_india.json` and add each weekday holiday from nseindia.com (Resources > Exchange Communication > Holidays), like `"2027-01-26": "Republic Day"` |
| **When your broker changes fees** | Update charges | Edit the `charges` part of `trader\config.json` |
| **If NSE changes tick sizes** | Check NSE circulars | Ask Claude to update the tick table |

Example `trader\market_india.json` with a 2027 holiday added:

```json
{
  "holidays": {
    "2027-01-26": "Republic Day"
  }
}
```

---

## 7. Safety rules

1. **Practise first.** For 2-4 weeks, build tickets but do not place them.
   Compare with what really happened.
2. **Nothing happens without your "yes".** No skill places an order by
   itself. With `"broker": "none"` you always place orders yourself.
3. **Always have a stop-loss.** Never move it further away once you are in.
4. **Close by 15:10.** No overnight intraday positions.
5. **Charges matter.** Small trades can lose money even when the price moves
   your way. The tools include charges for you.
6. **Free data can be late.** Confirm prices on your broker's chart.
7. **Never share passwords, PINs, TOTP codes or API keys** in the chat.
8. This is a tool for learning, **not financial advice**. Most intraday
   traders lose money. Trade only money you can afford to lose.

---

## 8. Problems and fixes

| Problem | Fix |
|---|---|
| `python` is not recognised | Reinstall Python with "Add Python to PATH" ticked, reopen PowerShell |
| `no budget: pass --budget or set "budget" in trader/config.json` | Create the config file (section 3) |
| `no orders file` | Run the budget step first (`size.py --write`) |
| `NOT READY` on the ticket | Run the risk check again (`check.py`) |
| `BLOCKED by trader-risk` | Read the reason: fewer shares, a closer stop, wait for the time window, or skip the trade |
| `NSE announcements unavailable` | NSE's site is busy; it shows Yahoo news instead. Try later |
| `Yahoo has no data` | Check the symbol (use NSE symbols like `RELIANCE`, `TCS`, `M&M`) |
| The ₹ sign looks strange | Run `chcp 65001` in PowerShell first |
| Config file error after editing | Copy the example again (section 3, Option 1) |
| Skills do not show in Claude | Repeat Step 5 and restart Claude Code |

---

## 9. Quick checklist

**One time**

- [ ] Unzipped the project
- [ ] Python 3.8+ works (`python --version`)
- [ ] Tests pass (`python -m unittest discover -s tests` shows OK)
- [ ] Skills copied to `%USERPROFILE%\.claude\skills\`
- [ ] Trading folder `C:\Users\antus\my-trading` made, with the `trader` folder copied in
- [ ] `trader\config.json` created and edited with my budget and limits
- [ ] Charges compared with my broker's calculator

**Every trading day**

- [ ] 8:30 scan, 8:45 news
- [ ] Wait 9:15-9:30
- [ ] Entry signal, then stop/target, odds, size, risk check
- [ ] Ticket, my yes, order placed in my broker app, stop and target added
- [ ] Alerts on
- [ ] No new trades after 14:45
- [ ] Everything closed by 15:10

**Now and then**

- [ ] Monthly: refresh the Nifty 500 list
- [ ] December: add next year's holidays to `trader\market_india.json`

# My Practice Trading Day: Wednesday, 18 November 2026

Mode: **Practice (paper trading)**. Nothing was really bought or sold.
All times are India time (IST). NSE is open 9:15 to 15:30.

---

## Summary

```
Settings → Scan (109 stocks) → News → Entry (AUROPHARMA triggered 11:05)
→ Exit plan (stop 1797.30 / target 1819.80) → Time machine (break-even)
→ Size (10 shares, max loss ₹94) → Risk check (PASS) → Order slip
→ Alarms set → Watching (₹1,807 at 12:04)
```

| Step | Time | What | Result |
|---|---|---|---|
| 0 | Before | Setup | Installed Python 3.15 |
| 1 | 10:27 | Settings file | ₹25,000 budget, ₹1,000 max loss |
| 2 | 10:28 | Scan | 109 stocks on the watchlist |
| 3 | 10:33 | News | Checked for surprises |
| 4 | 10:53–11:16 | Entry check | AUROPHARMA TRIGGERED at ₹1,804.80 |
| 5 | 11:19 | Exit plan | Stop ₹1,797.30, Target ₹1,819.80 |
| 6 | 11:41 | Time machine | 34% win / 56% loss: break-even |
| 7 | 11:50 | Budget | 10 shares, max loss ₹94 |
| 8 | 11:53 | Risk check | PASS |
| 9 | — | Order slip | Shows the Zerodha order |
| 10 | 12:01 | Set alarms | Stop, target, 15:10 |
| 11 | 12:04 | Check alarms | ₹1,807, nothing rang yet |

---

## Step 0: Setup (before trading)

**What I did:** installed Python 3.15. The old Python 3.6 was too old for these tools.

**Check it:**
```
python --version
```
It should say `Python 3.15.2`.

**Simple meaning:** the tools are Python programs, so they need a new enough Python to run.

---

## Step 1: Settings file (10:27)

**Command:**
```
copy trader-budget\config.example.json trader\config.json
```
Then I edited `trader\config.json` in Notepad.

**What happened:** I set my rules:

| Setting | My value | Meaning |
|---|---|---|
| `budget` | 25000 | Money I trade with (₹) |
| `max_loss_per_trade_inr` | 1000 | Most I can lose on one trade |
| `max_daily_loss_inr` | 3000 | Stop trading for the day after this loss |
| `max_trades_per_day` | 3 | At most 3 trades a day |
| `paper` | true | Practice mode, no real money |

**Simple meaning:** these are my rules, and every later step follows them.

---

## Step 2: Scan for stocks (10:28)

**Command:**
```
python trader-scanner/scan.py daily
```

**What happened:** it checked yesterday's charts of the Nifty 500 stocks and found **109 stocks** with interesting patterns, for example ICICIBANK, SBIN, RELIANCE, HDFCBANK and IIFL. It saved them to `trader/watchlist.json`.

**Simple meaning:** a shortlist of stocks worth **watching** today. It is **not** a list of stocks to buy.

---

## Step 3: Check the news (10:33)

**Command:**
```
python trader-news/news.py --tickers RELIANCE.NS --hours 12
```

**What happened:** it looked for fresh headlines and NSE announcements on the stocks.

**Simple meaning:** it makes sure no big surprise (results, a scandal, a big deal) is about to suddenly move the stock. If there is big news, it can be safer to skip that stock.

---

## Step 4: Watch for the buy moment (10:53 → 11:16)

**Command:**
```
python trader-entry/entry.py check AUROPHARMA.NS --setup orb --write
```

**What happened:**
- First, ASIANPAINT and AUROPHARMA both said **WAITING**: their prices were still below their 9:15–9:30 high.
- At **11:05 AM, AUROPHARMA closed above ₹1,802.60** (its morning high), so it was **TRIGGERED** at **₹1,804.80**.
- Saved to `trader/entry.json`.

**Simple meaning:**
- 9:15 to 9:30 makes the **"opening range"**, the morning's high and low.
- When the price **breaks above** that high, it's a sign it may keep rising. That's the **buy signal**.
- **WAITING** means not yet. **TRIGGERED** means now. **EXPIRED** means too late for today.

**What I learned:** `entry.json` holds only **one stock at a time**. Each new check replaces the old one, so work on one stock at a time.

---

## Step 5: Plan the exit (11:19)

**Command:**
```
python trader-exit/exit.py plan --stop or-mid --target 2R --write
```
(Or with only the stock name: `python trader-exit/exit.py plan --symbol AUROPHARMA.NS --stop or-mid --target 2R --write`. The entry price is fetched automatically.)

**What happened:** it set three prices **before** buying:

| | Price | Meaning |
|---|---|---|
| Buy (entry) | ₹1,804.80 | Where I get in |
| Stop | ₹1,797.30 | If it falls here, sell to cut the loss (−₹7.50 per share) |
| Target | ₹1,819.80 | If it rises here, sell for profit (+₹15 per share) |
| Flat by | 15:10 | Sell by this time, win or lose |

Saved to `trader/plan.json`.

**Simple meaning:** decide where to get out **before** getting in. The profit (₹15) is **2 times** the loss (₹7.50). That's what **"2R"** means.

**What I learned:** at first I typed `--entry 1250` for every stock, so every plan came out the same. The tool now fetches the real entry price by itself.

---

## Step 6: Time machine, the odds test (11:41)

**Command:**
```
python trader-timemachine/timemachine.py run --write
```

**What happened:**

1. **10,000 replays of the rest of today**, using AUROPHARMA's last 58 trading days:

   | Outcome | Chance |
   |---|---|
   | Hit target (win) | 34% |
   | Hit stop (loss) | 56% |
   | Neither by 15:10 | 10% |

2. **The real past:** the same setup happened **35 times** in the last 58 days:
   - 7 wins, 15 losses, 13 closed at 15:10
   - After charges, it **lost a tiny bit on average**

3. **Verdict:** *"Close to break-even."*

**Simple meaning:** I win less often than I lose, but each win is 2 times bigger. In the end it's about **zero profit**. This was **not a strong trade**. With real money, it would be fair to **skip** it.

---

## Step 7: How many shares to buy (11:50)

**Command:**
```
python trader-budget/size.py --write
```

**What happened:** it chose **10 shares** (about ₹18,048).
- It didn't buy more because of my rule that one trade can't use too much of my ₹25,000.
- If the stop hits: **−₹94** (₹75 loss + ₹19 charges)
- If the target hits: about **+₹150 minus charges**

Saved to `trader/orders.json`.

**Simple meaning:** it works out the right amount to buy so that a loss stays small and within my rules.

---

## Step 8: Safety check (11:53)

**Command:**
```
python trader-risk/check.py
```

**What happened:** **PASS**
- Max loss ₹94 is under my ₹1,000 limit
- It was my first trade today (0 of 3)
- The timing was allowed (between 9:30 and 14:45)

**Simple meaning:** the trade follows **my rules**.
**PASS does not mean it's a good trade** (Step 6 decides that). It only means it's a safe size.

---

## Step 9: Order slip

**Command:**
```
python trader-broker/ticket.py show
```

**What it does:** shows the exact order to type into Zerodha Kite:
- BUY 10 AUROPHARMA, NSE, MIS (intraday)
- Stop ₹1,797.30, target ₹1,819.80

**Simple meaning:** the slip I would copy into Zerodha. **The tool never places an order by itself.** In practice mode, I only write it down.

---

## Step 10: Set the alarms (12:01)

**Command:**
```
python trader-alerts/alerts.py add --from-plan
```

**What happened:** it set 3 alarms from my plan:

| Alarm | Rings when |
|---|---|
| Stop | Price falls to ₹1,797.30 |
| Target | Price rises to ₹1,819.80 |
| Flat by | Clock reaches 15:10 |

**Simple meaning:** like setting an alarm clock. You must **set** the alarm (`add`) before it can **ring** (`check`).

**What I learned:** running `check` before `add` gives the error *"no rules in trader/alerts.json"*.

---

## Step 11: Check the alarms (12:04)

**Command:**
```
python trader-alerts/alerts.py check
```

**What happened:** AUROPHARMA was at **₹1,807**, between the stop and the target, so **no alarm rang**. The practice trade is still open.

**To check automatically every 2 minutes** (stop with `Ctrl + C`):
```powershell
while ($true) { python trader-alerts/alerts.py check; Start-Sleep -Seconds 120 }
```

---

## Trade result (fill this in at the end of the day)

| | |
|---|---|
| Date | 18 Nov 2026 |
| Stock | AUROPHARMA.NS |
| Shares | 10 |
| Buy price | ₹1,804.80 (11:05 AM) |
| Stop / Target | ₹1,797.30 / ₹1,819.80 |
| Time machine said | Break-even (34% win) |
| **What happened** | WIN / LOSS / closed at 15:10 |
| **Exit price** | ₹______ |
| **Profit / loss** | ₹______ |

---

## Lessons from today

1. **Follow the order of the steps.** Each step creates a file the next step needs:
   - `entry.py` makes `entry.json`, which `exit.py` reads
   - `exit.py` makes `plan.json`, which the time machine, budget and alerts read
   - `size.py` makes `orders.json`, which the risk check and broker read
2. **Add `--write`** when you want a step to save its result for the next step.
3. **One stock at a time.** `entry.json` and `plan.json` hold only one stock.
4. **Trust the time machine.** PASS from the risk check only means "safe size", not "good trade". Only take trades where the time machine shows a clear edge.
5. **Be out by 15:10.** No new trades after 14:45.

---

## Files created today (in the `trader` folder)

| File | Made by | What's inside |
|---|---|---|
| `config.json` | Me | My rules and charges |
| `watchlist.json` | Scanner | 109 stocks to watch |
| `entry.json` | Entry check | AUROPHARMA triggered at ₹1,804.80 |
| `plan.json` | Exit plan | Entry, stop, target, flat-by |
| `timemachine.json` | Time machine | The odds (34% / 56%) |
| `orders.json` | Budget | 10 shares, max loss ₹94 |
| `alerts.json` | Alerts | The 3 alarms |
| `prices.json` | Alerts | Latest price (₹1,807) |

---

*This is practice only and not financial advice. SEBI's studies show most intraday traders lose money. Practise for a few weeks before using real money.*

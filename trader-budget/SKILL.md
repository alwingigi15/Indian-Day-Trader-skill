---
name: trader-budget
description: >-
  Budget Manager for NSE day trades. Sizes every position in rupees to your
  budget and your max loss per trade, Indian intraday charges included
  (brokerage, STT, exchange fees, SEBI fee, stamp duty, GST). Reads the trade
  plan from trader-exit (entry, stop, target), finds the whole shares that fit
  inside four limits (max loss per trade, position cap, max order, cash after
  your buffer), says which limit decided it, and writes ONE order (entry,
  stop, target, product MIS, exchange NSE) to trader/orders.json. A stop too
  far away for your max loss is a plain refusal, not a smaller order. Nothing
  is placed. Use when the user says "how many shares", "size this trade",
  "how much should I buy", "I have ₹1 lakh", "I only want to risk ₹1,000 a
  trade", "set my rules", "what are my charges", "keep 10% in cash", or after
  /trader-exit writes a plan.
argument-hint: "[budget in rupees]"
---

# trader-budget

The Budget Manager turns a plan ("buy RELIANCE at 1311.90, stop 1300.80")
into an exact order ("buy 57 shares"), inside rules the user sets once. The
stop decides the size: the further away the stop, the fewer shares. Charges
count too: every round trip pays brokerage, STT, exchange and SEBI fees,
stamp duty and GST, and they are part of the loss at the stop. It writes a
proposal only. trader-risk checks it and trader-broker asks the user yes or
no. Day trading loses most people money. Sizing to a max loss keeps each
loss small; it does not make a trade win.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py"                          # trader/plan.json + trader/config.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --write                  # write trader/orders.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --limit --write          # a LIMIT order at the plan's entry
python3 "${CLAUDE_SKILL_DIR}/size.py" --limit 1312.00 --json   # a LIMIT order at 1312.00
python3 "${CLAUDE_SKILL_DIR}/size.py" --weights RELIANCE=30,TCS=20 --config split.json   # weights mode, below
```

`--now "2026-11-06 09:51"` pins the clock (IST). A plan for another day is
refused: make a new one with trader-exit.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/config.json` | the rules and the `charges` block (start from `config.example.json` in this folder) |
| `trader/plan.json` | the trade: entry, stop, target, setup, flat-by time (trader-exit writes it) |
| `trader/prices.json` | optional: a fresh price above the plan's entry means a bigger risk (trader-alerts writes it) |
| `trader/portfolio.json` | optional: its cash caps the size (trader-broker keeps it) |
| `trader/orders.json` | the one proposed order it writes with `--write` |

## Step 1: set the rules once

If `trader/config.json` does not exist, copy `config.example.json` into
`trader/config.json` and ask the user for each value in plain words:

- **budget**: how many rupees this is for. Only money they can afford to lose.
- **cash_buffer_pct**: how much always stays in cash (default 10).
- **max_loss_per_trade_inr**: the most one trade may lose if the stop fills
  at the stop, charges included (default ₹1,000).
- **max_position_pct**, **max_order_inr**, **min_order_inr**: the biggest
  position (75% of the budget), the biggest order (₹80,000) and the smallest
  order (₹500).
- The day limits trader-risk enforces: **max_daily_loss_inr** (₹3,000),
  **max_trades_per_day** (3), **min_reward_risk** (1.5), **require_stop**
  (true), **no_entries_before** / **no_entries_after** ("09:30" / "14:45"),
  **mis_entry_cutoff** ("15:00") and **flat_by** ("15:10"), all IST.
- **product**: MIS (intraday). **exchange**: NSE. **blocked**: symbols they
  never want to trade.
- **charges**: the rates in the example are a discount broker's intraday
  equity rates (brokerage 0.03% or ₹20 an order, whichever is lower; STT
  0.025% on the sell; NSE transaction charge 0.00307%; SEBI ₹10 a crore;
  stamp duty 0.003% on the buy; GST 18% on brokerage and fees). Ask the user
  to check them against their broker's brokerage calculator.
- Keep **broker** "none" and **paper** true until they have watched it work
  on tickets they place themselves.

## Step 2: size the trade

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py" --write
```

Whole shares only: NSE has no fractional shares. The size is the smallest of
four limits:

| limit | shares |
| --- | --- |
| max loss | `max_loss_per_trade_inr` / (risk a share + charges), the most shares whose loss at the stop plus round-trip charges fits |
| position cap | budget x `max_position_pct` / entry |
| max order | `max_order_inr` / entry |
| cash | (budget minus the cash buffer) / entry, never more than the cash in `trader/portfolio.json` |

The entry it sizes at: the limit price with `--limit`. For a market order, the
plan's entry, or the price in `trader/prices.json` if it is fresh (under 15
minutes old) and higher. A price that ran up since the plan means a bigger
loss at the same stop, so fewer shares. A lower price never shrinks the risk.
Every price in the order sits on a legal NSE tick (₹0.10 for a stock between
₹1,000 and ₹5,000).

It refuses, and writes no order, when: the size is zero shares, the order is
under the minimum, reward:risk is under `min_reward_risk`, the stop is not
below the entry, the target is not above it (the move already happened), the
plan is for another day, the symbol is blocked, or the plan is a short. Say
the reason to the user in one line.

## Step 3: show the user

Show the four limits, which one decided the size, the shares, the rupees, the
charges and the max loss at the stop. Say plainly that a stop is an order,
not a promise: in a fast market, or a stock locked at its circuit, it can
fill below the stop or not at all. Then hand over to `/trader-risk`.

## Example

RELIANCE.NS on Fri 2026-11-06. Opening-range breakout: entry 1311.90 at 9:50
IST, stop 1300.80 (the opening-range midpoint), target 1334.10 (2R). Risk
11.10 a share. The example config: ₹1,00,000 budget, 10% cash buffer, ₹1,000
max loss.

```
Limits (whole shares; the smallest one wins):
  max loss      ₹1,000 max loss / (11.10 risk a share + charges)                 82 shares
  position cap  75% of ₹100,000 = ₹75,000 / 1311.90                              57 shares  <- binds
  max order     ₹80,000 / 1311.90                                                60 shares
  cash          budget ₹100,000 minus the ₹10,000 cash buffer = ₹90,000 / 1311.90   68 shares

Size: 57 shares of RELIANCE.NS = ₹74,778.30 at 1311.90. Max loss at the stop: 57 x 11.10 = ₹632.70 + ₹73.55 charges = ₹706.25.
```

It writes one order in the neutral shape trader-risk and trader-broker read:

```json
{"symbol": "RELIANCE.NS", "exchange": "NSE", "tradingsymbol": "RELIANCE", "side": "BUY", "qty": 57,
 "order_type": "MARKET", "product": "MIS", "validity": "DAY", "stop_loss": 1300.8, "target": 1334.1,
 "max_loss_inr": 706.25, "charges_estimate_inr": 73.55, "flat_by": "15:10",
 "why": "opening-range breakout on RELIANCE.NS: 57 shares, bound by your position cap (57 x 11.10 = ₹632.70 + ₹73.55 charges = ₹706.25 at the stop)",
 "plan": {"entry": 1311.9, "stop": 1300.8, "target": 1334.1, "risk_per_share": 11.1, "max_loss": 706.25,
          "setup": "orb", "entry_time": "09:50", "flat_by": "15:10"}}
```

`why`, `plan` and the estimates are notes for trader-risk and trader-broker.
They are never sent to a broker. With the stop at 1200.00 instead, one share
risks 111.90, the max loss allows only 8 shares, and reward:risk falls to
0.20: no order.

## Weights mode

`--weights RELIANCE=30,TCS=20` or `--targets FILE` (`targets.example.json`
here: five Nifty 50 stocks with round illustrative weights, not a
recommendation) splits a budget across a list by weight: the cash buffer
comes off the top, weights are renormalised to 100, any stock over
`max_position_pct` is pinned at the cap and its excess shared out, slices
under the minimum order are dropped, and amounts round to whole rupees (or to
the paisa with `--paise`). With `"fractional": false` (the example config) it
buys whole shares and needs prices (`--prices trader/prices.json`). These are
plain buys with no stop: trader-risk blocks them while `require_stop` is true
and counts each one against `max_trades_per_day`. Use a separate config
(`--config`) with `"product": "CNC"` for a long-term (delivery) split.

## Rules

- Proposals only. This skill never calls a broker tool.
- Never size money the user has not said is for this. Ask for the budget and the max loss.
- Every number comes from the config, the plan or the prices. Nothing is guessed.
- No projections: never say what a trade will make. A target is a plan, not a forecast.
- Free Yahoo prices are unofficial and can lag. The broker's price is the one that fills.
- No intraday leverage: size on the user's own cash, even though brokers offer MIS margin.

## What it never does

It never places, approves or schedules an order, never raises a limit on its
own, never shrinks a stop to fit more shares, and never sizes F&O, margin or
short positions.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.

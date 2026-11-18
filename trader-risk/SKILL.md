---
name: trader-risk
description: >-
  Risk Manager for NSE day trades. Checks every order against your rupee
  limits and stops anything over them, before it can reach the broker. For a
  day trade: a stop is required, the loss at the stop plus Indian intraday
  charges (brokerage, STT, exchange fees, SEBI fee, stamp duty, GST) must fit
  your max loss per trade (measured from the higher of the plan's entry and a
  fresh price), reward:risk must meet your minimum, the day's total risk must
  fit your daily loss limit, no more than your trades per day, no new entries
  outside your IST entry window or when NSE is closed, and no new MIS entries
  after the MIS cut-off. NSE rules too: every price on a legal NSE tick, no
  trade-for-trade (BE/BZ) stocks intraday, nothing outside the day's circuit
  band. It also keeps the basics: blocked symbols, order size, position cap,
  cash buffer, margin, short sales, F&O and crypto, duplicates and a daily
  order limit. Getting out is never blocked by a day limit. Prints PASS or
  BLOCK with the reason and records the verdicts so trader-broker only shows
  an order that passed. Use when the user says "check my order", "is this
  safe", "risk check", "does this break my rules", "can I take this trade",
  "how much have I risked today", or before /trader-broker, always.
argument-hint: "[path to orders.json]"
---

# trader-risk

The Risk Manager is the gate between a proposal and the broker. Every order
written by trader-budget or trader-broker's flatten passes through it. A
BLOCK is final for that order: it does not go to the user for a yes, and it
does not go to the broker.

Most people who day trade lose money; SEBI's own studies of Indian intraday
traders say so, before and after charges. These limits keep each loss and
each day's losses small. They do not make a trade win.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/check.py"                        # trader/ files; writes trader/state/risk.json
python3 "${CLAUDE_SKILL_DIR}/check.py" --json
python3 "${CLAUDE_SKILL_DIR}/check.py" --no-write             # look without recording
python3 "${CLAUDE_SKILL_DIR}/check.py" --now "2026-11-06 09:52"   # pin the clock (IST)
```

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders (trader-budget, or trader-broker's flatten) |
| `trader/config.json` | the limits in rupees and the `charges` block (trader-budget ships `config.example.json`) |
| `trader/portfolio.json` | cash and holdings; if missing, a fresh account funded with the budget |
| `trader/prices.json` | a fresh price raises the entry it measures risk from; values holdings; circuit limits if present |
| `trader/state/broker.json` | what was placed today: duplicates, trades per day, daily loss |
| `trader/market_india.json` | optional: more NSE holidays (2026 is built in) |
| `trader/state/risk.json` | the verdicts it writes, with a fingerprint of each exact order |

## Entries and exits

An **entry** opens or adds to a position: a buy, or a sell that would go
short. An **exit** only reduces what you already hold. Getting out must always
be possible, so an exit is checked for format, asset and short sales only.
The entry rules (blocked list, size, duplicates) turn into warnings for an
exit, and the day limits (window, MIS cut-off, loss, trades) do not apply to
it at all.

## The checks

Orders are checked in file order, as if each passed order had filled, so a
batch that is fine order by order but breaks a limit together is caught.

| check | blocks when |
| --- | --- |
| format | no symbol, side not BUY or SELL, qty not a positive whole number (NSE has no fractional shares and no rupee-amount orders), unknown `order_type` (MARKET, LIMIT, SL, SL-M), `product` (MIS, CNC) or `validity` (DAY, IOC), a LIMIT or SL without a `price`, an SL or SL-M without a `trigger_price`, any price not on its NSE tick |
| asset | an NSE F&O contract (`NIFTY25NOV24000CE`, `RELIANCE25NOVFUT`) while `allow_options` is false; crypto or anything that is not an NSE/BSE stock symbol |
| short | a sell of more than you hold (`allow_short` false) |
| blocked | (entries) the symbol is on the `blocked` list |
| series | (entries) series BE or BZ: trade-for-trade, delivery only, no intraday |
| size | (entries) under `min_order_inr` or over `max_order_inr` |
| position | (entries) the stock would be over `max_position_pct` of the account |
| cash | (entries) the buy would leave less than the cash buffer, or need margin (MIS leverage included) |
| stop | (entries) no stop while `require_stop` is true (the order's `stop_loss`, or its plan stop); a stop on the wrong side of the entry |
| loss | (entries) qty x (entry minus stop) plus the round-trip charges over `max_loss_per_trade_inr` |
| reward:risk | (entries) (target minus entry) / (entry minus stop) under `min_reward_risk` |
| daily loss | (entries) the max losses at the stops of today's entries, plus this one, over `max_daily_loss_inr` |
| trades | (entries) `max_trades_per_day` entries already placed today |
| window | (entries) before `no_entries_before` (09:30) or after `no_entries_after` (14:45) IST, or NSE is closed (weekend, NSE holiday, outside 9:15 to 15:30) |
| mis cut-off | (entries) a new MIS entry at or after `mis_entry_cutoff` (15:00): brokers stop taking them and square off MIS soon after |
| circuit | (entries) an entry outside the day's circuit band, when the order, its plan or `trader/prices.json` carries `upper_circuit` / `lower_circuit`; within 1% of the upper circuit is a warning |
| duplicate | (entries) the same symbol and side twice in one batch, or already placed today |
| daily limit | (entries) `max_orders_per_day` orders already placed today |

How the numbers work:

- **The entry it measures from**: the limit price for a LIMIT order, the
  trigger for an SL-M. For a MARKET order, the higher of the plan's entry and
  a fresh price from `trader/prices.json` (under 15 minutes old), rounded up
  to a tick. A plan written at 9:50 cannot understate the risk at 11:40 after
  the price ran up. An older price is not used, and it says so.
- **Charges** are a round trip at the stop: buy at the entry, sell at the
  stop, with the rates in the config's `charges` block (brokerage 0.03% or
  ₹20 an order, STT 0.025% on the sell, NSE transaction charge 0.00307%, SEBI
  ₹10 a crore, stamp duty 0.003% on the buy, GST 18% on brokerage and fees).
  Check them against your broker's brokerage calculator.
- **Daily loss** counts the full max loss of every entry placed today, win or
  lose, as trader-broker logged it. Three full-size trades can use up a
  ₹3,000 day.
- **The window** is IST, worked out in the tool (UTC+5:30, no daylight
  saving), never from the computer's own time zone. Weekends and NSE holidays
  are closed. Set both `no_entries_*` to null to switch the window off.
- **Ticks**: ₹0.01 under ₹250, ₹0.05 to ₹1,000, ₹0.10 to ₹5,000, ₹0.50 to
  ₹10,000, ₹1 to ₹20,000, ₹5 above (NSE circular NSE/CMTR/67133; NSE reviews
  it monthly). The exchange rejects any other price, so the check does too.
- India has no pattern-day-trader rule, so there is no PDT check.

Warnings do not block, but say them to the user: a live account, a stop that
lives only in the plan and not on the order, a price too old to use, a stock
near its upper circuit, a year whose NSE holidays are not loaded.

## Steps

1. If the price may have moved since the plan, refresh it first
   (trader-alerts: `alerts.py quote RELIANCE.NS --write`).
2. Run `check.py`. Exit code 0 means every order passed, 1 means at least
   one was blocked.
3. Tell the user the result in plain words, order by order: the max loss at
   the stop including charges, reward:risk, and how much of today's limits it
   uses. For a block, give the reason and the fix (fewer shares, a closer stop
   from trader-exit, wait for the window, or skip the trade). Never change a
   limit yourself.
4. If the order changes in any way, run the check again. trader-broker
   matches each order to its verdict by fingerprint, so an edited order is not
   ready until it passes again, and a verdict from yesterday does not count
   today.

## Example

The RELIANCE.NS order from trader-budget at 9:52 IST on Fri 2026-11-06, on a
fresh ₹1,00,000 account with the example config:

```
PASS  #1 BUY RELIANCE.NS 57 shares MIS, entry
          entry 1311.90 (the plan's entry), stop 1300.80, target 1334.10: risk 11.10 a share
          max loss ₹706.25 incl. ₹73.55 charges (limit ₹1,000), reward:risk 2.00 (min 1.5)
          today before this: 0 of 3 trades, ₹0 of ₹3,000 daily loss

1 passed, 0 blocked at 2026-11-06 09:52 IST. Cash after the passed orders: ₹25,221.70.
```

Three more shares and the position cap stops it:

```
BLOCK #1 BUY RELIANCE.NS 60 shares MIS, entry
          max loss ₹740.94 incl. ₹74.94 charges (limit ₹1,000), reward:risk 2.00 (min 1.5)
        - position: RELIANCE.NS would be ₹78,714, 78.7% of the ₹100,000 account, over your 75% cap
```

At 9:20 the same 57 shares are blocked by the window (no new entries before
9:30, while the opening range forms). The 15:10 sell that closes the position
passes, even though it is after the 14:45 entry cut-off: it is an exit.

## Rules

- A PASS only means the order is allowed to be shown to the user. It is not a yes.
- Every number comes from the config, the order, the portfolio, the prices or the broker log.
- Free Yahoo prices are unofficial and can lag; the broker's price is the one
  that fills, and a stop can fill worse than its price in a fast market, or
  not at all when a stock is locked at its circuit.

## What it never does

It never places an order, never edits an order to make it pass, never
loosens a limit, never blocks a sell that only gets the user out, and never
treats a PASS as the user's yes.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.

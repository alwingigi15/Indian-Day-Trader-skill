---
name: trader-exit
description: >-
  Exit Planner. Sets your stop and your target before you enter, so you know
  when to get out. NSE stocks (RELIANCE.NS), India time (IST), prices in
  rupees. Reads the entry from trader-entry (or a price you give), puts the
  stop at the opening-range midpoint by default (or the range low, your
  price, a percent, or a multiple of the ATR), rounds it down to a legal NSE
  tick, and sets the target at 2R (or 1R, or your price). Refuses a stop at
  or above the entry, a target at or below it, or a stop too tight to survive
  the spread (under 0.05% of the price or under 2 ticks), and says why.
  Writes trader/plan.json for the Time Machine, the Budget Manager and Price
  Alerts, flat by 15:10 IST before the broker's MIS square-off. Long side
  only. Use when the user says "where's my stop", "set my stop and target",
  "plan the exit", "when do I get out", "stop loss", "what's my R", "risk
  reward", or right after /trader-entry triggers, before /trader-timemachine.
argument-hint: "[--stop or-mid|or-low|price:X|pct:X|atr:K] [--target 2R|1R|price:X]"
---

# trader-exit

The Exit Planner decides how a trade ends before it starts. The stop is where
the setup has failed. The target is where you take what you came for. Both
are set BEFORE the entry, written down and handed on, so nobody has to decide
anything in the middle of a fast move. A stop is a plan, not a guarantee: a
gap, a fast market or a circuit limit can fill past it, or not at all. Day
trading loses most people money, and a neat plan does not change that.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan                          # trader/entry.json: midpoint stop, 2R target
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop or-mid --target 1R --write
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop atr:1.5           # needs the session's bars (Yahoo or --offline)
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --symbol RELIANCE.NS      # fetches the entry itself (Yahoo bars)
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --symbol RELIANCE.NS --entry 1234.50 --or-high 1231.60 --or-low 1214.60
python3 "${CLAUDE_SKILL_DIR}/exit.py" show                          # the current trader/plan.json
```

A bare symbol (`RELIANCE`) is taken as NSE and becomes `RELIANCE.NS`. Other
flags: `--flat-by 15:10`, `--json`, `--now "2026-11-02 09:36"` (IST),
`--entry PRICE` to override the entry, `--setup level`,
`--offline FILE` (a saved Yahoo chart JSON for an ATR stop or a fetched entry).

No `--entry`? It is filled in for you. From a waiting entry.json it plans ahead
at the breakout level. With `--symbol` alone it reads today's bars: the first
close above the opening-range high is the entry (with its time and bar); before
a breakout it plans ahead at the range high, and says which one it did.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/entry.json` | the trigger from trader-entry: symbol, entry price, time, bar, opening range |
| `trader/config.json` | `flat_by` (15:10 IST) and `min_reward_risk` (1.0: a note here, trader-risk enforces it) |
| `trader/market_india.json` | optional: extra NSE holidays, `{"holidays": {"YYYY-MM-DD": "name"}}` (2026 is built in) |
| Yahoo chart API | only for an `atr:K` stop: the session's bars up to the entry bar, free and unofficial |
| `trader/plan.json` | with `--write`: entry, stop and its rule, target and its R, risk a share, reward:risk, flat by, tick size. trader-timemachine, trader-budget, trader-alerts, trader-risk and trader-broker read it |

## Stops and targets

| stop | where it goes |
| --- | --- |
| `or-mid` | the opening-range midpoint (the default for an opening-range breakout) |
| `or-low` | the opening-range low: wider, so fewer shares for the same max loss |
| `price:X` | your price |
| `pct:X` | X percent under the entry |
| `atr:K` | K times the 14-bar ATR under the entry, on the session's bars up to the entry bar. With fewer than 14 bars by then it averages all of them and says so |

A level setup has no opening range to lean on, so it needs a stop you name.

- **Ticks.** NSE rejects a price that is not a multiple of the tick, and the
  tick depends on the price: ₹0.01 under ₹250, ₹0.05 to ₹1,000, ₹0.10 to
  ₹5,000, ₹0.50 to ₹10,000, ₹1 to ₹20,000, ₹5 above (NSE circular
  NSE/CMTR/67133; NSE reviews it monthly). Every price in the plan is on a
  tick.
- **Rounding.** The entry rounds UP to a tick. The stop rounds AWAY from the
  entry, down to a tick (₹1,400.07 becomes ₹1,400.00). Risk = entry minus
  stop. Target = entry + R x risk, rounded DOWN to a tick, so it is a little
  easier to fill. `2R` by default; `1R`, `1.5R` or `price:X` also work.
- **Refused, with the reason.** A stop at or above the entry. A target at or
  below it. A risk under 0.05% of the price, or under 2 ticks: too tight to
  survive the spread, normal wiggle would stop you out. An entry.json that
  expired or has no data (unless you give `--entry`). A short: v2 plans longs
  only.
- **Flat by.** 15:10 IST by default (or `flat_by` in the config). Out by
  then, win or lose, no overnight hold. Brokers square off intraday (MIS)
  positions on their own from about 15:15 to 15:25 and charge about ₹50 +
  GST an order for it, so a flat-by at or after 15:15 gets a note. A flat-by
  outside 9:15 to 15:30 is refused, and so is an entry at or after the
  flat-by time.
- **Circuit limits.** A stock frozen at its lower circuit cannot be sold, so
  the stop may not fill at all. Say so when the stock is near its band.

## Steps

1. Run it right after `/trader-entry` triggers. Before that, `--entry PRICE`
   plans ahead at the level you expect.
2. Use the default stop for an opening-range breakout unless the user names
   another. A level setup needs one: ask.
3. Run `exit.py plan --write`. Read back the entry, the stop and its rule,
   the target and its rule, the risk a share, reward:risk and flat by.
4. If it refuses, say the reason and the fix (a wider stop, a farther
   target). Never loosen anything on your own.
5. Say once that the stop is a plan, not a guarantee.
6. Hand off to `/trader-timemachine` to replay the plan, then `/trader-budget`
   to size it to the max loss per trade. Offer `/trader-alerts` for the stop,
   the target and the flat-by time. Claude can keep `entry.py watch` running
   in the background or on a `/loop` until a setup triggers, then come here.

## Example: RELIANCE.NS

RELIANCE.NS, Monday 2026-11-02 (real Yahoo 5-minute bars). The entry is
1234.50 at 9:35 AM IST (the 9:30 bar, bar 3) and the opening range is
1214.60 to 1231.60. `exit.py plan --stop or-mid --target 1R --write`:

```
Exit plan: RELIANCE.NS long, Mon 2026-11-02, opening-range breakout on 5-minute bars
  Entry         ₹1,234.50 at 9:35 AM (bar 3)
  Stop          ₹1,223.10  opening-range midpoint: (₹1,231.60 + ₹1,214.60) / 2 = ₹1223.1
  Target        ₹1,245.90  1R (one times the risk)
  Risk a share  ₹11.40 (0.92% of the entry)
  Reward:risk   1.0
  Flat by       15:10 IST, win or lose: no overnight hold (MIS)
```

The default 2R target would be 1234.50 + 2 x 11.40 = 1257.30, already on
the ₹0.10 tick. With `--stop pct:1` the stop is 1234.50 x 0.99 = 1222.155,
rounded down to ₹1,222.10. trader-budget then sizes the plan to your max
loss per trade, charges included.

## Rules

- The stop and the target are set before the entry, never after.
- Never move a stop further from the entry once the trade is on.
- Plain numbers only: entry, stop, target, risk a share, reward:risk. It
  never prints a P&L or a return.
- Nothing here promises winning trades or money. A 1R target is not a
  prediction that the price gets there.
- Long side only. v2 plans longs only.

## What it never does

It never places an order, never sizes a position, never widens a stop to
make a plan pass, and never calls a stop a guarantee.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.

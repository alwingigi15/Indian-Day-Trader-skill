---
name: trader-alerts
description: >-
  Price Alerts for NSE stocks. Rings when a price, a stop, the opening bell or
  your flat-by time hits. Turns the trade plan into three alerts in one go
  (the stop, the target and "flat by" at 15:10 IST with the MIS square-off
  reminder), plus above / below / stop / target levels, percent moves from
  the previous close, any time of day, and the NSE opening bell, each ringing
  once so you are not spammed. Knows NSE hours (9:15 to 15:30 IST) and the
  2026 NSE holidays, takes more from trader/market_india.json, works out IST
  itself, fetches free delayed Yahoo quotes in rupees one symbol at a time,
  and writes trader/prices.json for sizing and the risk check. Use when the
  user says "alert me when", "tell me if RELIANCE hits 1450", "watch my stop",
  "watch my trade", "remind me to square off by 3:10", "is NSE open", "is the
  market open today", "when does the market open", "get me a price", or right
  after /trader-exit writes a plan.
argument-hint: "[symbol] [price]"
---

# trader-alerts

Price Alerts watches the numbers so the user does not have to stare at a
chart. An alert is information, never an order: when a stop alert rings,
nothing is sold by this skill. If the user placed a stop-loss order at the
broker, that order does the work; the alerts tell the user what happened and
when it is time to be flat.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/alerts.py" clock                         # NSE open or closed, next open
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --from-plan               # stop, target, flat by from trader/plan.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --from-plan --mis-warning 15:05   # one more, earlier reminder
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --opening-bell
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol RELIANCE --stop 1301.20
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol RELIANCE --target 1317.10
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --at 15:10 --label "flat by" --mis --symbol RELIANCE
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol TCS --pct 3
python3 "${CLAUDE_SKILL_DIR}/alerts.py" check                         # exit 0 = something rang
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote RELIANCE.NS --write     # -> trader/prices.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote --holdings --write      # portfolio + plan symbols
python3 "${CLAUDE_SKILL_DIR}/alerts.py" list
python3 "${CLAUDE_SKILL_DIR}/alerts.py" reset reliance.ns-plan-stop
```

A bare symbol (`RELIANCE`) means NSE (`RELIANCE.NS`). `--now "2026-11-06 15:10"`
pins the clock (IST); `--offline DIR` reads saved quote files instead of the
network.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | the trade from trader-exit, for `add --from-plan` |
| `trader/config.json` | `flat_by` and `mis_warning` when the plan has none |
| `trader/alerts.json` | the rules (`alerts.example.json` in this folder) |
| `trader/state/alerts.json` | which rules rang and when, so each rings once |
| `trader/prices.json` | the latest quotes in rupees, merged in, for trader-budget and trader-risk |
| `trader/portfolio.json` | the symbols held, for `quote --holdings` |
| `trader/market_india.json` | optional: more NSE holidays, `{"holidays": {"2027-01-26": "Republic Day"}}` |

## Rule types

| type | rings when | rings again |
| --- | --- | --- |
| `above` / `target` | price at or above `price` | after `reset`, or on its own if `"rearm": true` and the price fell back |
| `below` / `stop` | price at or below `price` | same |
| `pct_move` | moved `pct` percent from the previous close (or `ref`), optional `direction` up or down | once per day at most |
| `time` | the IST clock passed `at` ("15:10") on a trading day | once per trading day |
| `opening_bell` | NSE is open | once per trading day |

A rule with `"date"` is watched on that IST date only; `--from-plan` rules
carry the plan's date, so yesterday's stop never rings today. A `time` rule
with `"mis": true` adds the reminder that brokers square off open MIS
(intraday) positions themselves around 15:15 to 15:25 and charge for it.

## Quotes

- Yahoo: `query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS`, an
  unofficial free JSON endpoint, one request per symbol, half a second apart.
  On a 429 it waits and tries once more; on a 429 or a 5xx after that it tries
  `query1`. It identifies itself as `day-trader-skill/3.0`.
- Free quotes are unofficial, delayed and can lag. They are fine for alerts
  and for checking a size, not for timing an order. The broker's price is the
  one that fills. Each price in `trader/prices.json` keeps its quote time, and
  trader-risk only uses one under 15 minutes old.
- If Yahoo is down, read the price from the broker app and write it into
  `trader/prices.json` as `{"as_of": "...Z", "prices": {"RELIANCE.NS": 1311.2}}`.

## The NSE clock

Regular session 9:15 to 15:30 IST (pre-open 9:00 to 9:15 has no intraday
bars), closed on weekends and on NSE trading holidays. India has no daylight
saving, so IST is always UTC+5:30, and NSE has no scheduled early closes.

- 2026 holidays are built in, from NSE's holiday circular: Jan 15, Jan 26,
  Mar 3 (Holi), Mar 26, Mar 31, Apr 3, Apr 14, May 1, May 28, Jun 26, Sep 14,
  Oct 2, Oct 20 (Dussehra), Nov 10 (Diwali Balipratipada), Nov 24, Dec 25.
- NSE publishes next year's list in December
  (nseindia.com, Resources, Exchange Communication, Holidays). Add it to
  `trader/market_india.json`; until then `clock` says the year is unknown.
- Muhurat trading (the one-hour Diwali evening session) is not modelled: that
  day counts as closed.

## Steps

1. Right after trader-exit writes the plan, run `add --from-plan`. Confirm the
   three alerts back to the user: the stop, the target and the flat-by time.
2. Run `check` while the trade is open: every few minutes from a Claude
   scheduled task or `/loop`. Each run fetches only the symbols in today's
   rules and rings each alert once. A check reads the latest delayed quote;
   it is not a live feed, and a price can pass a level between two checks.
3. Tell the user what rang in one line each. A stop or target alert means a
   stop-loss or target order they placed has probably filled: ask them to
   check the broker app and record the fill with trader-broker. "Flat by"
   means close anything still open before the broker's square-off:
   trader-broker `flatten`, then trader-risk, then the user's yes. Never act
   for them.

## Example

RELIANCE.NS on Fri 2026-11-06: entry 1306.50, stop 1301.20, target 1317.10,
flat by 15:10. `add --from-plan` writes `reliance.ns-plan-stop`,
`reliance.ns-plan-target` and `reliance.ns-flat-by` for that day. A `check` at
11:15 reads the real 11:13 quote, 1311.20, between the stop and the target:

```
NSE OPEN (2026-11-06 11:15 IST, regular session).
Nothing hit. 3 rule(s) watched today, RELIANCE.NS ₹1,311.20.
```

At 15:10:

```
ALERT Flat by: it is 15:10 IST (set for 15:10), the market closes at 15:30. If RELIANCE.NS is still open, close it: trader-broker flatten --symbol RELIANCE.NS builds the sell ticket, then trader-risk and your yes. MIS positions still open are squared off by the broker around 15:15-15:25, with a fee of about ₹50 + GST per order.
```

It rings once. The next trading day the plan's three alerts are no longer
watched; with `add --opening-bell`, the bell rings at 9:15.

## Rules

- An alert is a fact about a price or the clock. Say it as a fact.
- Never present a quote as live. Free quotes are delayed and unofficial.
- Confirm every new rule back to the user: symbol, type, level, day.

## What it never does

It never places, cancels or changes an order, never claims a quote is live,
and never predicts where a price is going.

## Safety

- Education and tooling, not financial advice. A pass is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk passed that exact order today, the user typed an explicit yes for that one order in the chat, and the user clicked Allow once on Claude's own card because the order tool is set to "Needs approval" (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude desktop config, never in the chat.

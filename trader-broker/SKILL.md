---
name: trader-broker
description: >-
  Order Ticket for NSE day trades, never without your yes. Shows each
  approved order as a plain ticket in rupees (BUY 57 RELIANCE (NSE, MIS),
  market / stop / target / max loss incl. charges), records your yes word for
  word, and by default stays TICKET ONLY: nothing is sent, you place the order
  in your broker's app. With Zerodha connected through its Kite MCP server it
  also prints the exact Kite place_order fields, then Claude's own Allow once
  / Deny card asks again. Kite has no retail bracket orders, so the stop and
  target become an SL-M sell and a LIMIT sell (or one GTT OCO) after the
  fill. Builds the MIS sell ticket that closes a day trade before the broker's
  square-off, records fills in rupees, and logs each trade's max loss so
  trader-risk can hold the daily limit. Use when the user says "place the
  order", "buy it", "execute", "send it to Zerodha", "close my position",
  "square off", "flatten", "get me out", "connect Kite", "set up my broker",
  or after /trader-risk passes.
argument-hint: "[order number]"
---

# trader-broker

The Order Ticket is the only skill in this pack that can lead to a real
order, so it is the strictest. An order reaches the market only after these
gates, every time, for every order:

1. **trader-risk passed this exact order today** (matched by fingerprint).
2. **The user typed an explicit yes for this one order** in the chat.
3. Then either **the user places it themselves** in the broker's app (ticket
   only, the default), or, with Zerodha's Kite MCP server connected, **the
   user clicks Allow once** on Claude's own permission card, which appears
   because the order tool is set to "Needs approval".

No yes, no order.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/ticket.py" show                    # every ticket, READY or not
python3 "${CLAUDE_SKILL_DIR}/ticket.py" approve --index 1 --reply "yes"
python3 "${CLAUDE_SKILL_DIR}/ticket.py" result --index 1 --status placed --broker-id ID --filled-qty 57 --filled-price 1312.00
python3 "${CLAUDE_SKILL_DIR}/ticket.py" fill --symbol RELIANCE --side sell --qty 57 --price 1334.10
python3 "${CLAUDE_SKILL_DIR}/ticket.py" flatten --symbol RELIANCE --qty 57
python3 "${CLAUDE_SKILL_DIR}/ticket.py" log
```

`ticket.py` never talks to a broker. It renders tickets, checks that a reply
is a real yes for that order, builds the flatten ticket, and keeps the log in
`trader/state/broker.json`. `--now "2026-11-06 09:52"` pins the clock (IST).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders (trader-budget writes it; `flatten` replaces it) |
| `trader/config.json` | `broker` (`none` by default), `paper`, `product`, `flat_by` |
| `trader/state/risk.json` | trader-risk's verdicts; only a PASS from today is READY |
| `trader/state/broker.json` | every yes, no, placed, failed and filled, with each entry's max loss |
| `trader/portfolio.json` | cash and shares, updated by `fill` and by `result --filled-qty` |
| `trader/plan.json` | the flat-by time for `flatten` |

## Default: TICKET ONLY

Most Indian brokers have no paper-trading account, so the safe default is
`"broker": "none"`: the ticket says exactly what to enter in the app (symbol,
exchange NSE, product MIS, BUY, MARKET or LIMIT, quantity), and after the buy
fills, the stop as an SL-M sell and the target as a LIMIT sell, or one GTT
OCO with both. The user places it, then tells you the order id and fill, and
you record them with `result` and `fill`. This works with any broker:
Zerodha, Upstox, Angel One, Dhan or another.

## Option: Zerodha through the Kite MCP server

Zerodha runs an official MCP server for Kite at `https://mcp.kite.trade/mcp`
(the example is in `claude_desktop_config.example.json` here). Check what it
offers today before relying on it: tools, limits and whether it can place
orders change.

1. The user needs a Zerodha account. In Claude Desktop: Settings > Developer >
   Edit Config, and add the `kite` block from the example
   (`npx mcp-remote https://mcp.kite.trade/mcp`; it needs Node.js). Restart
   Claude Desktop.
2. **Log in in the browser, never in the chat.** The Kite login opens in the
   browser. A Kite session (access token) **expires every day** (early
   morning), so the user logs in again each trading day before using it.
   Never ask for, read or repeat a password, TOTP, API key or token. If the
   user pastes one, tell them to delete that message and change it.
3. **Set the tool permissions**: Customize > Connectors (or the server's tool
   list), and set every tool that places, modifies or cancels an order or a
   GTT to **"Needs approval"**. Never "Always allow". Read-only tools
   (profile, holdings, positions, quotes) can be "Always allow".
4. Then set `"broker": "zerodha"` and `"paper": false` in `trader/config.json`.
   Only the user does that: it is real money from then on. Until both are
   set, `ticket.py` stays ticket only.

SEBI's framework for retail algorithmic and API trading (from 2025, phased
in) puts rules on orders sent through APIs, such as registered static IPs
and order-rate limits; brokers apply it differently. One human-approved order
at a time is the simplest case, but read your broker's current API terms.
Other brokers (Upstox, Angel One SmartAPI, Dhan) have APIs too; with them,
stay ticket only unless the user sets up their own connector and its tools
are on "Needs approval".

## Steps: the entry

1. `ticket.py show`. Only READY tickets go to the user. A ticket that is NOT
   READY or BLOCKED goes back to `/trader-risk`.
2. Show one ticket and ask: "Place this order? Reply yes or no." One order per
   question. Never offer "yes to all".
3. Run `ticket.py approve --index N --reply "<the user's reply, word for
   word>"`. If it prints "Not recorded", the reply was not an explicit yes for
   this order (for example "ok", "sure", "haan", "yes to all", a yes with a
   doubt in it, or a yes that names a different stock or amount). Ask again.
   Never turn a reply into a yes yourself.
4. Ticket only: the user places it in the app. With Kite: only after
   "Recorded your yes", call `place_order` with exactly the fields the ticket
   printed; Claude's card appears: Allow once or Deny.
5. Record what happened: `ticket.py result --index N --status placed
   --broker-id <id> --filled-qty Q --filled-price P`, or `--status denied`,
   or `--status failed` with `--note`. A failed order is not retried without a
   new check and a new yes. The log keeps the entry's max loss for
   trader-risk's daily limit.
6. Right after the fill, the stop and the target: an SL-M sell at the stop and
   a LIMIT sell at the target (each its own yes, each its own Allow once with
   Kite), or one GTT OCO. When one fills, cancel the other at once, or the
   leftover sell can fill later and leave a short MIS position.

## Steps: while it is open, and out before the square-off

- trader-alerts watches the stop, the target and the flat-by time
  (`alerts.py add --from-plan`).
- When the stop or the target fills, record it:
  `ticket.py fill --symbol RELIANCE --side sell --qty 57 --price 1300.80`.
- By `flat_by` (15:10 IST), anything still open gets closed. Brokers square
  off MIS positions themselves around 15:15 to 15:25 and charge about ₹50 +
  GST an order for it, so do it first:
  1. Check how many shares are held: the Positions screen, or Kite's
     positions tool (read only).
  2. `ticket.py flatten --symbol RELIANCE --qty 57` writes an MIS market sell
     for those shares to `trader/orders.json`.
  3. `/trader-risk`. A sell that only closes what the user holds is never
     blocked by the daily limits, the entry window or the MIS cut-off.
  4. Cancel the open stop and target sells (or delete the GTT) first.
  5. `ticket.py show`, then the user's yes for that one order, as above.

## Example

The RELIANCE.NS order from trader-budget, after trader-risk passed it at 9:52
IST on Fri 2026-11-06:

```
ORDER 1 of 1, TICKET ONLY (place it yourself), no broker connected
  BUY 57 RELIANCE (NSE, MIS), market
  stop ₹1,300.80
  target ₹1,334.10
  max loss about ₹706 incl. charges
  (the stop can fill worse than ₹1,300.80 in a fast market)
  why: opening-range breakout on RELIANCE.NS: 57 shares, bound by your position cap (57 x 11.10 = ₹632.70 + ₹73.55 charges = ₹706.25 at the stop)
  READY: trader-risk PASS, waiting for your yes
  Nothing is sent from here. Place it yourself in your broker's app, exactly as above:
  BUY, product MIS, MARKET.
  After the buy fills, add the stop as an SL-M sell and the target as a LIMIT sell
  (or one GTT OCO with both), and cancel the other when one fills.
  Reply "yes" to approve this one order, or "no" to skip it.
```

The user replies "yes". `approve` records it word for word; the user places
the order in the app and reads back the order id and fill price; `result`
records them, and the portfolio's cash and shares update. The `why` and the
plan stay in the file; they are never sent anywhere.

## What it never does

- Never places an order without the gates above, and never two orders on one yes.
- Never places orders from a scheduled or background task: nobody is there to say yes.
- Never asks for, reads, stores or repeats passwords, TOTPs, API keys or access tokens.
- Never switches `"paper"` to false or `"broker"` to a live broker. The user does that themselves.
- Never places F&O, margin or short orders on its own.
- Never promises a fill price: a market order and an SL-M fill at whatever the market gives, and a stock locked at its circuit may not fill at all.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.

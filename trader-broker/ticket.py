#!/usr/bin/env python3
"""Order tickets and the decision log for trader-broker. Standard library only.

This tool never talks to a broker and never places anything. It does its
work around the one moment that matters, your yes:

  show      renders each proposed order as a plain ticket in rupees (symbol,
            exchange, product, limit or market, stop, target, max loss incl.
            charges). With "broker": "none" (the default) it is TICKET ONLY:
            you place it yourself in your broker's app. With "broker":
            "zerodha" and "paper": false it also prints the exact Kite
            Connect place_order fields Claude sends through the Kite MCP
            server, plus the stop and target as follow-up orders. An order
            is READY only if trader-risk passed THIS exact order today
            (matched by fingerprint) and nobody has decided on it yet.
  approve   records your explicit yes for ONE order, with your reply word for
            word. It refuses "yes to all", "sure", "ok", a yes that names a
            different stock or amount, and any order that is not READY.
  decline   records a no.
  result    records what happened: placed (by you in the app, or by the Kite
            tool, with the broker's order id), denied, or failed.
  fill      records a fill in trader/portfolio.json (cash and shares).
  flatten   builds an MIS sell ticket that closes a symbol's open position
            before flat_by. It still goes through trader-risk and your yes.
  log       prints today's decisions.

    python ticket.py show                    # every order, READY or not
    python ticket.py show --index 1 --json   # one ticket as JSON (with the Kite translation)
    python ticket.py approve --index 1 --reply "yes"
    python ticket.py result --index 1 --status placed --broker-id 2511060001234
    python ticket.py fill --symbol RELIANCE --side buy --qty 57 --price 1312.00
    python ticket.py flatten --symbol RELIANCE --qty 57
    python ticket.py log

Everything is written to trader/state/broker.json (append only). Dates are
India (IST) dates; --now "2026-11-06 09:50" pins the clock.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys

# The neutral order shape; same tuple as trader-risk/check.py (the fingerprint covers these only).
ORDER_FIELDS = ("symbol", "exchange", "tradingsymbol", "side", "qty", "order_type", "price", "trigger_price",
                "product", "validity", "stop_loss", "target", "client_order_id")
BROKER_NAMES = {"none": "no broker connected", "zerodha": "Zerodha (Kite)", "upstox": "Upstox",
                "angelone": "Angel One", "dhan": "Dhan"}
YES_WORDS = ("yes", "y")
BATCH_WORDS = {"all", "every", "everything", "rest", "remaining", "them", "both", "batch", "each"}
DOUBT_WORDS = {"no", "not", "dont", "don't", "wait", "cancel", "stop", "but", "maybe", "later",
               "hold", "unless", "if"}


class TicketError(Exception):
    pass


# ---------------------------------------------------------------- time (IST, no tz files)

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


def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is taken as IST."""
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise TicketError("--now wants a time like \"2026-11-06 09:50\" (IST) or ...Z (UTC)")
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


# ---------------------------------------------------------------- NSE symbols and ticks

DEFAULT_SUFFIX = ".NS"


def nse_symbol(sym):
    """RELIANCE -> RELIANCE.NS; RELIANCE.NS, TCS.BO and ^NSEI unchanged. Stored form."""
    s = str(sym).strip().upper()
    if s and not s.startswith("^") and not s.endswith((".NS", ".BO")):
        s += DEFAULT_SUFFIX
    return s


def broker_symbol(sym):
    """RELIANCE.NS -> ("NSE", "RELIANCE"); TCS.BO -> ("BSE", "TCS")."""
    s = nse_symbol(sym)
    if s.endswith(".BO"):
        return "BSE", s[:-3]
    if s.endswith(".NS"):
        return "NSE", s[:-3]
    return "NSE", s


# ---------------------------------------------------------------- files

def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise TicketError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise TicketError("%s is not valid JSON: %s" % (path, e))


def write_json(path, data):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def rec_day(rec):
    """The trading day a decision belongs to: its "date" (the day it was made under), else the day of "at"."""
    return str(rec.get("date") or str(rec.get("at", ""))[:10])


# ---------------------------------------------------------------- the order

def fingerprint(order):
    """Same function as trader-risk/check.py: identifies the exact order."""
    core = {k: order[k] for k in ORDER_FIELDS if k in order and order[k] is not None}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _str_num(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        f = float(v)
        return str(int(f)) if f == int(f) else repr(f)
    return str(v)


def _f(x):
    if isinstance(x, bool):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _int(x):
    f = _f(x)
    return int(round(f)) if f is not None else None


def _money(v):
    f = float(v)
    s = "{:,.2f}".format(f)
    return "₹" + (s[:-3] if s.endswith(".00") else s)


def _rupees(v):
    """₹1,400.10 with paise always shown (prices)."""
    return "₹" + "{:,.2f}".format(float(v))


def _p(v):
    return "%.2f" % float(v)


def parts_of(order):
    """(exchange, tradingsymbol, product, side) with sensible defaults."""
    ex, ts = broker_symbol(order.get("symbol", "?"))
    return (str(order.get("exchange") or ex).upper(), str(order.get("tradingsymbol") or ts).upper(),
            str(order.get("product") or "MIS").upper(), str(order.get("side", "")).upper())


def has_legs(order):
    return order.get("stop_loss") is not None or order.get("target") is not None


def max_loss(order):
    """(rupees incl. charges, stop) lost if the stop fills exactly at the stop, or (None, stop).

    trader-budget's max_loss_inr already includes the round-trip charges."""
    plan = order.get("plan") or {}
    stop = _f(order.get("stop_loss")) or _f(plan.get("stop"))
    q = _f(order.get("qty"))
    if not stop or not q:
        return None, stop
    if _f(order.get("max_loss_inr")) is not None:
        return round(_f(order["max_loss_inr"]), 2), stop
    if _f(plan.get("max_loss")) is not None:
        return round(_f(plan["max_loss"]), 2), stop
    return None, stop


def plain_parts(order):
    """The ticket in plain words, one part per line.

    BUY 50 RELIANCE (NSE, MIS), limit ₹1,400.10 / stop ₹1,386.00 / target ₹1,428.20 /
    max loss about ₹745 incl. charges"""
    ex, ts, product, side = parts_of(order)
    typ = str(order.get("order_type", "MARKET")).upper()
    if typ == "LIMIT":
        how = "limit %s" % _rupees(order.get("price"))
    elif typ == "SL":
        how = "stop-limit, trigger %s, limit %s" % (_rupees(order.get("trigger_price")), _rupees(order.get("price")))
    elif typ == "SL-M":
        how = "stop-market, trigger %s" % _rupees(order.get("trigger_price"))
    else:
        how = "market"
    head = "%s %s %s (%s, %s), %s" % (side, _str_num(order.get("qty")), ts, ex, product, how)
    if str(order.get("validity", "DAY")).upper() == "IOC":
        head += ", IOC"
    parts = [head]
    if order.get("stop_loss") is not None:
        parts.append("stop %s" % _rupees(order["stop_loss"]))
    if order.get("target") is not None:
        parts.append("target %s" % _rupees(order["target"]))
    loss, _ = max_loss(order)
    if loss is not None:
        parts.append("max loss about ₹{:,.0f} incl. charges".format(loss))
    return parts


def plain(order):
    return " / ".join(plain_parts(order))


def to_kite(order):
    """The neutral order -> Zerodha Kite Connect place_order fields.

    Kite has no retail bracket orders, so an entry with a stop and a target becomes:
      entry       the buy itself (MARKET or LIMIT, product MIS)
      after_fill  two sells placed once the buy fills: an SL-M stop and a LIMIT target.
                  When one fills, cancel the other (or the leftover sell turns into a short).
      gtt_oco     the alternative: one GTT OCO (two triggers, stop and target). A GTT trigger
                  places a fresh order, so it is not guaranteed in a fast market."""
    ex, ts, product, side = parts_of(order)
    typ = str(order.get("order_type", "MARKET")).upper()
    qty = _int(order.get("qty"))
    entry = {"variety": "regular", "exchange": ex, "tradingsymbol": ts, "transaction_type": side,
             "quantity": qty, "product": product, "order_type": typ,
             "validity": str(order.get("validity", "DAY")).upper()}
    if typ in ("LIMIT", "SL") and order.get("price") is not None:
        entry["price"] = _f(order["price"])
    if typ in ("SL", "SL-M") and order.get("trigger_price") is not None:
        entry["trigger_price"] = _f(order["trigger_price"])
    out = {"tool": "place_order", "entry": entry, "after_fill": [], "gtt_oco": None}
    stop, target = _f(order.get("stop_loss")), _f(order.get("target"))
    if side == "BUY" and (stop or target):
        exit_side = "SELL"
        if stop:
            out["after_fill"].append({"variety": "regular", "exchange": ex, "tradingsymbol": ts,
                                      "transaction_type": exit_side, "quantity": qty, "product": product,
                                      "order_type": "SL-M", "trigger_price": stop, "validity": "DAY"})
        if target:
            out["after_fill"].append({"variety": "regular", "exchange": ex, "tradingsymbol": ts,
                                      "transaction_type": exit_side, "quantity": qty, "product": product,
                                      "order_type": "LIMIT", "price": target, "validity": "DAY"})
        if stop and target:
            last = _f(order.get("price")) or _f((order.get("plan") or {}).get("entry"))
            out["gtt_oco"] = {"tool": "place_gtt_order", "trigger_type": "two-leg", "exchange": ex,
                              "tradingsymbol": ts, "last_price": last, "trigger_values": [stop, target],
                              "orders": [{"transaction_type": exit_side, "quantity": qty, "product": product,
                                          "order_type": "LIMIT", "price": stop},
                                         {"transaction_type": exit_side, "quantity": qty, "product": product,
                                          "order_type": "LIMIT", "price": target}]}
    return out


def amount_numbers(order):
    nums = []
    for k in ("qty", "price", "trigger_price", "stop_loss", "target"):
        if order.get(k) is not None:
            v = _f(order[k])
            if v is not None:
                nums.append(v)
    loss, _ = max_loss(order)
    if loss is not None:
        nums.extend([loss, float(round(loss))])
    return nums


def parse_yes(reply, order, batch_symbols=()):
    """Is this reply an explicit yes for THIS one order? Returns (ok, why)."""
    raw = (reply or "").strip()
    if not raw:
        return False, "the reply is empty"
    if "?" in raw:
        return False, "the reply is a question, not a yes"
    words = re.findall(r"[a-z']+", raw.lower())
    if not words or words[0] not in YES_WORDS:
        return False, "only a reply that starts with \"yes\" counts (not ok, sure, haan, go, do it)"
    if BATCH_WORDS & set(words):
        return False, "a yes covers one order at a time, not a batch"
    if DOUBT_WORDS & set(words[1:]):
        return False, "the reply has a condition or a doubt in it; ask again"
    sym = nse_symbol(order.get("symbol", ""))
    tokens = set(re.findall(r"[A-Z0-9&][A-Z0-9&.\-]*", raw.upper()))
    tokens |= {t[:-3] for t in tokens if t.endswith((".NS", ".BO"))}
    others = set()
    for s in batch_symbols:
        s = nse_symbol(s)
        if s != sym:
            others |= {s, broker_symbol(s)[1]}
    named = tokens & others
    if named:
        return False, "the reply names %s, but this order is for %s" % (", ".join(sorted(named)), sym)
    nums = [float(n.replace(",", "")) for n in re.findall(r"(?<![A-Za-z&])\d[\d,]*(?:\.\d+)?(?![A-Za-z&])", raw)]
    if nums:
        want = amount_numbers(order)
        if not any(abs(n - w) < 0.005 for n in nums for w in want):
            return False, "the reply says %s, but this order is %s" % (
                ", ".join(_str_num(n) for n in nums), plain(order))
    return True, "explicit yes for %s" % plain(order)


# ---------------------------------------------------------------- state

def load_state(path):
    return read_json(path, "broker state", required=False) or {"orders": []}


def latest(state, fp, today):
    """The latest decision recorded today for this fingerprint, or None."""
    found = None
    for rec in state.get("orders", []):
        if rec.get("fingerprint") == fp and rec_day(rec) == today:
            found = rec
    return found


def risk_verdict(risk, fp, today):
    if not risk:
        return None, "trader-risk has not checked these orders (run check.py)"
    if risk.get("date") != today:
        return None, "the risk check is from %s, not today; run check.py again" % risk.get("date")
    for r in risk.get("results", []):
        if r.get("fingerprint") == fp:
            return r, None
    return None, "this exact order was not in the last risk check (it changed or is new); run check.py again"


def status_of(order, risk, state, today):
    """(ready, label, risk_result)"""
    fp = fingerprint(order)
    rv, why = risk_verdict(risk, fp, today)
    if rv is None:
        return False, "NOT READY: " + why, None
    if rv.get("verdict") != "PASS":
        return False, "BLOCKED by trader-risk: " + "; ".join(rv.get("reasons", [])), rv
    rec = latest(state, fp, today)
    if rec:
        st = rec.get("status")
        if st == "approved":
            return False, "APPROVED by you at %s, waiting to be placed and recorded" % rec.get("at"), rv
        return False, "DONE: %s at %s" % (st, rec.get("at")), rv
    return True, "READY: trader-risk PASS, waiting for your yes", rv


def kind_of(order, rv):
    """entry or exit, as trader-risk saw it (older checks: a buy is an entry)."""
    if rv and rv.get("kind"):
        return rv["kind"]
    return "entry" if str(order.get("side", "")).lower() == "buy" else "exit"


def broker_mode(cfg):
    """("ticket", name) unless "broker": "zerodha" and "paper": false, then ("kite", name)."""
    b = str(cfg.get("broker") or "none").lower()
    name = BROKER_NAMES.get(b, b)
    if b == "zerodha" and cfg.get("paper", True) is False:
        return "kite", name
    return "ticket", name


# ---------------------------------------------------------------- rendering

def _json_lines(obj, indent="    "):
    return [indent + l for l in json.dumps(obj, indent=2, ensure_ascii=False).splitlines()]


def render(order, idx, total, cfg, risk, state, today):
    ready, label, rv = status_of(order, risk, state, today)
    mode, name = broker_mode(cfg)
    if mode == "kite":
        acct = "LIVE - %s, REAL MONEY" % name
    else:
        acct = "TICKET ONLY (place it yourself), %s" % name
    lines = ["ORDER %d of %d, %s" % (idx, total, acct)]
    for part in plain_parts(order):
        lines.append("  " + part)
    if max_loss(order)[0] is not None:
        lines.append("  (the stop can fill worse than %s in a fast market)" % _rupees(max_loss(order)[1]))
    if order.get("why"):
        lines.append("  why: " + order["why"])
    lines.append("  " + label)
    for w in (rv or {}).get("warnings", []):
        lines.append("  note: " + w)
    k = to_kite(order)
    if mode == "kite":
        lines.append("  Claude will call the Kite MCP tool place_order with exactly:")
        lines.extend(_json_lines(k["entry"]))
        if k["after_fill"]:
            lines.append("  After the buy fills, the stop and the target go in as two sells (Kite has no")
            lines.append("  bracket orders); when one fills, cancel the other:")
            for leg in k["after_fill"]:
                lines.extend(_json_lines(leg))
    else:
        lines.append("  Nothing is sent from here. Place it yourself in your broker's app, exactly as above:")
        lines.append("  %s, product %s, %s." % (parts_of(order)[3], parts_of(order)[2],
                                                str(order.get("order_type", "MARKET")).upper()))
        if k["after_fill"]:
            lines.append("  After the buy fills, add the stop as an SL-M sell and the target as a LIMIT sell")
            lines.append("  (or one GTT OCO with both), and cancel the other when one fills.")
    if ready:
        lines.append("  Reply \"yes\" to approve this one order, or \"no\" to skip it.")
        if mode == "kite":
            lines.append("  Claude's own card then asks again: Allow once places it, Deny stops it.")
    return "\n".join(lines), ready


def ticket_json(order, idx, cfg, risk, state, today):
    ready, label, rv = status_of(order, risk, state, today)
    loss, stop = max_loss(order)
    mode, name = broker_mode(cfg)
    return {"index": idx, "ready": ready, "status": label, "plain": plain(order),
            "fingerprint": fingerprint(order), "paper": bool(cfg.get("paper", True)),
            "broker": str(cfg.get("broker") or "none"), "mode": mode, "kite": to_kite(order),
            "why": order.get("why", ""), "max_loss_inr": loss, "stop": stop}


# ---------------------------------------------------------------- fills -> portfolio

def apply_fill(portfolio_path, cfg, symbol, side, qty, price, date):
    """Record a filled order in trader/portfolio.json: cash and shares, with a
    cost-basis lot per buy (oldest lots are relieved first on a sell)."""
    pf = read_json(portfolio_path, "portfolio", required=False)
    if pf is None:
        budget = cfg.get("budget")
        if not budget:
            raise TicketError("no %s yet and no budget in the config to start it from" % portfolio_path)
        pf = {"cash": float(budget), "positions": []}
    symbol = nse_symbol(symbol)
    qty, price = float(qty), float(price)
    if qty <= 0 or price <= 0:
        raise TicketError("a fill needs a positive qty and price")
    cost = round(qty * price, 2)
    pos = None
    for p in pf.setdefault("positions", []):
        if nse_symbol(p.get("ticker") or p.get("symbol") or "") == symbol:
            pos = p
    if side == "buy":
        if pos is None:
            pos = {"ticker": symbol, "shares": 0.0, "lots": []}
            pf["positions"].append(pos)
        lots = pos.setdefault("lots", [])
        lots.append({"id": "%s-%d" % (symbol, len(lots) + 1), "date": date, "shares": qty, "cost": cost})
        pos["shares"] = round(float(pos.get("shares", 0)) + qty, 6)
        pf["cash"] = round(float(pf.get("cash", 0)) - cost, 2)
    else:
        if pos is None or float(pos.get("shares", 0)) + 1e-9 < qty:
            raise TicketError("cannot record selling %s %s: the portfolio holds %s"
                              % (_str_num(qty), symbol, pos.get("shares", 0) if pos else 0))
        left = qty
        for lot in sorted(pos.get("lots", []), key=lambda l: l.get("date", "")):
            if left <= 1e-9:
                break
            take = min(left, float(lot["shares"]))
            per = float(lot["cost"]) / float(lot["shares"]) if float(lot["shares"]) else 0
            lot["cost"] = round(float(lot["cost"]) - per * take, 2)
            lot["shares"] = round(float(lot["shares"]) - take, 6)
            left -= take
        pos["lots"] = [l for l in pos.get("lots", []) if float(l["shares"]) > 1e-9]
        pos["shares"] = round(float(pos["shares"]) - qty, 6)
        if pos["shares"] <= 1e-9:
            pf["positions"].remove(pos)
        pf["cash"] = round(float(pf.get("cash", 0)) + cost, 2)
    write_json(portfolio_path, pf)
    return pf


def log_fill(state_path, now, today, symbol, side, qty, price):
    """A fill in the log (trader-risk reads the placed entries; fills are kept for the record)."""
    state = load_state(state_path)
    symbol = nse_symbol(symbol)
    state.setdefault("orders", []).append({
        "at": ist_iso(now), "date": today, "status": "filled", "symbol": symbol, "side": side,
        "qty": _str_num(float(qty)), "price": float(price),
        "plain": "%s %s %s at %s" % (side.upper(), _str_num(float(qty)), broker_symbol(symbol)[1], _rupees(price))})
    write_json(state_path, state)


# ---------------------------------------------------------------- commands

def _clock(a):
    now = parse_now(getattr(a, "now", None))
    today = getattr(a, "today", None) or to_ist(now).date().isoformat()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", today):
        raise TicketError("--today wants a date like 2026-11-06")
    return now, today


def _load(a):
    doc = read_json(a.orders, "orders file (run trader-budget, or ticket.py flatten, first)")
    orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
    if not isinstance(orders, list) or not orders:
        raise TicketError("no orders in %s" % a.orders)
    cfg = read_json(a.config, "config", required=False) or {}
    risk = read_json(a.risk, "risk check", required=False)
    state = load_state(a.state)
    now, today = _clock(a)
    return orders, cfg, risk, state, today, now


def _pick(orders, index):
    if index is None:
        raise TicketError("say which order: --index N")
    if not 1 <= index <= len(orders):
        raise TicketError("there is no order %d (there are %d)" % (index, len(orders)))
    return orders[index - 1]


def cmd_show(a):
    orders, cfg, risk, state, today, now = _load(a)
    picks = [(a.index, _pick(orders, a.index))] if a.index else list(enumerate(orders, 1))
    if a.json:
        out = [ticket_json(o, i, cfg, risk, state, today) for i, o in picks]
        print(json.dumps(out[0] if a.index else out, indent=2, ensure_ascii=False))
        return 0 if any(t["ready"] for t in out) else 1
    blocks, any_ready = [], False
    for i, o in picks:
        text, ready = render(o, i, len(orders), cfg, risk, state, today)
        blocks.append(text)
        any_ready = any_ready or ready
    print("\n\n".join(blocks))
    print("\nOne order at a time: a yes covers one ticket, never the batch. Education and tooling, "
          "not financial advice.")
    return 0 if any_ready else 1


def cmd_approve(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    ready, label, rv = status_of(order, risk, state, today)
    if not ready:
        raise TicketError("order %d is not ready: %s" % (a.index, label))
    ok, why = parse_yes(a.reply, order, [o.get("symbol", "") for o in orders])
    if not ok:
        print("Not recorded: %s. Ask the user again with the ticket for order %d." % (why, a.index))
        return 1
    loss, stop = max_loss(order)
    if loss is None and rv and _f(rv.get("max_loss")) is not None:
        loss = round(_f(rv["max_loss"]), 2)     # trader-risk's own figure (incl. charges) for this exact order
    if stop is None and rv and _f(rv.get("stop")) is not None:
        stop = _f(rv["stop"])
    mode, name = broker_mode(cfg)
    rec = {"at": ist_iso(now), "date": today, "index": a.index, "fingerprint": fingerprint(order),
           "symbol": nse_symbol(order.get("symbol", "")), "side": str(order.get("side", "")).lower(),
           "qty": order.get("qty"), "product": parts_of(order)[2], "kind": kind_of(order, rv),
           "max_loss": loss, "stop": stop, "plain": plain(order), "status": "approved", "reply": a.reply,
           "paper": bool(cfg.get("paper", True)), "broker": str(cfg.get("broker") or "none"), "mode": mode}
    state.setdefault("orders", []).append(rec)
    write_json(a.state, state)
    print("Recorded your yes for order %d: %s." % (a.index, plain(order)))
    k = to_kite(order)
    if mode == "kite":
        print("Now call the Kite MCP tool place_order with exactly:")
        print(json.dumps(k["entry"], indent=2, ensure_ascii=False))
        print("Claude's card shows Allow once / Deny. Then: ticket.py result --index %d --status "
              "placed|denied|failed" % a.index)
        if k["after_fill"]:
            print("After the fill, place the stop (SL-M sell) and the target (LIMIT sell) shown on the ticket, "
                  "each through its own Allow once card.")
    else:
        print("TICKET ONLY: nothing was sent. Place it yourself in your broker's app exactly as the ticket says,")
        print("then record it: ticket.py result --index %d --status placed --broker-id <order id from the app>"
              % a.index)
    return 0


def cmd_decline(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    rec = {"at": ist_iso(now), "date": today, "index": a.index, "fingerprint": fingerprint(order),
           "symbol": nse_symbol(order.get("symbol", "")), "side": str(order.get("side", "")).lower(),
           "plain": plain(order), "status": "declined", "reply": a.reply or "no"}
    state.setdefault("orders", []).append(rec)
    write_json(a.state, state)
    print("Recorded: you said no to order %d (%s). Nothing was sent." % (a.index, plain(order)))
    return 0


def cmd_result(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    fp = fingerprint(order)
    rec = latest(state, fp, today)
    if not rec or rec.get("status") != "approved":
        raise TicketError("order %d has no yes recorded today; a result can only follow an approval" % a.index)
    new = {"at": ist_iso(now), "date": today, "index": a.index, "fingerprint": fp, "symbol": rec["symbol"],
           "side": rec["side"], "plain": rec["plain"], "status": a.status}
    for k in ("qty", "product", "kind", "max_loss", "stop"):
        if rec.get(k) is not None:
            new[k] = rec[k]
    if a.broker_id:
        new["broker_order_id"] = a.broker_id
    if a.note:
        new["note"] = a.note
    state["orders"].append(new)
    write_json(a.state, state)
    print("Recorded order %d as %s." % (a.index, a.status))
    if a.status == "placed" and a.filled_qty and a.filled_price:
        apply_fill(a.portfolio, cfg, rec["symbol"], rec["side"], a.filled_qty, a.filled_price, today)
        log_fill(a.state, now, today, rec["symbol"], rec["side"], a.filled_qty, a.filled_price)
        print("Added the fill to %s." % a.portfolio)
    elif a.status == "placed":
        print("When it fills, record it: ticket.py fill --symbol %s --side %s --qty Q --price P"
              % (rec["symbol"], rec["side"]))
    if a.status == "placed" and rec.get("kind") == "entry" and has_legs(order):
        print("Now the stop and the target: an SL-M sell and a LIMIT sell (or one GTT OCO). When either "
              "fills, cancel the other and record that sell with ticket.py fill.")
    return 0


def cmd_fill(a):
    cfg = read_json(a.config, "config", required=False) or {}
    now = parse_now(a.now)
    date = a.date or to_ist(now).date().isoformat()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise TicketError("--date wants a date like 2026-11-06")
    pf = apply_fill(a.portfolio, cfg, a.symbol, a.side, a.qty, a.price, date)
    log_fill(a.state, now, date, a.symbol, a.side, a.qty, a.price)
    print("Recorded %s %s %s at %s on %s in %s. Cash now %s." % (a.side, a.qty, nse_symbol(a.symbol),
                                                                _rupees(a.price), date, a.portfolio,
                                                                _money(pf["cash"])))
    return 0


def cmd_flatten(a):
    cfg = read_json(a.config, "config", required=False) or {}
    sym = nse_symbol(a.symbol)
    now, today = _clock(a)
    source = "the broker (Kite get_positions, or the Positions screen in your app)"
    if a.qty is not None:
        qty = _f(a.qty)
        if qty is None or qty <= 0 or abs(qty - round(qty)) > 1e-9:
            raise TicketError("--qty wants the whole number of shares you hold, like --qty 57")
    else:
        pf = read_json(a.portfolio, "portfolio", required=False) or {}
        qty = sum(_f(p.get("shares", p.get("qty"))) or 0 for p in pf.get("positions", [])
                  if nse_symbol(p.get("ticker") or p.get("symbol") or "") == sym)
        source = a.portfolio
        if qty <= 1e-9:
            raise TicketError("nothing to flatten: no %s shares in %s. Check the broker's Positions screen "
                              "and pass --qty if you do hold some" % (sym, a.portfolio))
    plan = read_json(a.plan, "plan", required=False) or {}
    flat_by = (plan.get("flat_by") if plan.get("symbol") and nse_symbol(plan["symbol"]) == sym else None) \
        or cfg.get("flat_by") or "15:10"
    ex, ts = broker_symbol(sym)
    product = str(cfg.get("product") or "MIS").upper()
    order = {"symbol": sym, "exchange": ex, "tradingsymbol": ts, "side": "SELL", "qty": int(round(qty)),
             "order_type": "MARKET", "product": product, "validity": "DAY",
             "why": "flat by %s IST: closes the %s day trade before the broker's MIS square-off (qty from %s)"
             % (flat_by, ts, source)}
    old = read_json(a.orders, "orders", required=False)
    replaced = len(old.get("orders", [])) if isinstance(old, dict) else 0
    doc = {"created_by": "trader-broker", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "status": "proposed",
           "currency": "INR", "broker": str(cfg.get("broker") or "none"), "paper": bool(cfg.get("paper", True)),
           "note": "Flatten ticket. Nothing is placed until trader-risk passes it and you say yes in the chat.",
           "source": "ticket.py flatten %s on %s" % (sym, today), "orders": [order]}
    write_json(a.orders, doc)
    print("Flatten %s: %s (flat by %s IST)." % (sym, plain(order), flat_by))
    t_now = to_ist(now).strftime("%H:%M")
    if t_now > flat_by:
        print("It is %s, past your flat_by %s: brokers square off MIS positions themselves around 15:15-15:25, "
              "for a fee." % (t_now, flat_by))
    print("Wrote it to %s%s." % (a.orders, (" (it replaced %d earlier proposal%s)"
                                             % (replaced, "" if replaced == 1 else "s")) if replaced else ""))
    if a.qty is None:
        print("The qty comes from %s. Check it against the broker's Positions screen before you say yes." % source)
    print("Next:")
    print("  1. trader-risk: check.py. A sell that only closes what you hold is never blocked by the daily")
    print("     limits, the entry window or the MIS cutoff.")
    print("  2. ticket.py show, then the user's yes for this one order.")
    print("  3. First cancel the open stop (SL-M) and target (LIMIT) sells, or delete the GTT, for %s." % ts)
    print("     Otherwise one of them can fill after this sell and leave you short in MIS.")
    return 0


def cmd_log(a):
    state = load_state(a.state)
    now, today = _clock(a)
    rows = [r for r in state.get("orders", []) if a.all or rec_day(r) == today]
    if not rows:
        print("No decisions recorded%s." % ("" if a.all else " today"))
        return 1
    for r in rows:
        extra = (" id " + r["broker_order_id"]) if r.get("broker_order_id") else ""
        reply = (' reply "%s"' % r["reply"]) if r.get("reply") and r["status"] in ("approved", "declined") else ""
        loss = (" max loss %s" % _money(r["max_loss"])) if r.get("max_loss") is not None and r["status"] == "placed" else ""
        idx = ("#%s" % r["index"]) if r.get("index") is not None else "  "
        print("%s  %s %-9s %s%s%s%s" % (r.get("at"), idx, r.get("status"), r.get("plain"), reply, loss, extra))
    placed = sum(1 for r in rows if r.get("status") == "placed")
    print("%d placed%s." % (placed, "" if a.all else " today"))
    return 0


def main(argv=None):
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Order tickets and your yes (trader-broker). Never places orders.")
    sub = ap.add_subparsers(dest="cmd")

    def clock(p):
        p.add_argument("--now", help="pretend it is this IST time, e.g. \"2026-11-06 09:50\"")
        p.add_argument("--today", help="YYYY-MM-DD (default: today in India, IST)")

    def common(p):
        p.add_argument("--orders", default="trader/orders.json")
        p.add_argument("--config", default="trader/config.json")
        p.add_argument("--risk", default="trader/state/risk.json")
        p.add_argument("--state", default="trader/state/broker.json")
        clock(p)

    p = sub.add_parser("show", help="render the tickets")
    common(p)
    p.add_argument("--index", type=int)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("approve", help="record the user's explicit yes for one order")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply", required=True, help="the user's reply, word for word")
    p = sub.add_parser("decline", help="record a no")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply")
    p = sub.add_parser("result", help="record what happened after the yes")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--status", required=True, choices=["placed", "denied", "failed"])
    p.add_argument("--broker-id")
    p.add_argument("--note")
    p.add_argument("--filled-qty")
    p.add_argument("--filled-price")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p = sub.add_parser("fill", help="record a fill in trader/portfolio.json")
    p.add_argument("--symbol", required=True)
    p.add_argument("--side", required=True, choices=["buy", "sell"])
    p.add_argument("--qty", required=True)
    p.add_argument("--price", required=True)
    p.add_argument("--date", help="YYYY-MM-DD (default: today in India, IST)")
    p.add_argument("--now")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--state", default="trader/state/broker.json")
    p = sub.add_parser("flatten", help="an MIS sell ticket that closes a symbol's position by flat_by")
    p.add_argument("--symbol", required=True)
    p.add_argument("--qty", help="shares you hold, from the broker (default: trader/portfolio.json)")
    p.add_argument("--orders", default="trader/orders.json", help="where the ticket is written")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--plan", default="trader/plan.json")
    p.add_argument("--config", default="trader/config.json")
    clock(p)
    p = sub.add_parser("log", help="today's decisions")
    p.add_argument("--state", default="trader/state/broker.json")
    clock(p)
    p.add_argument("--all", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"show": cmd_show, "approve": cmd_approve, "decline": cmd_decline, "result": cmd_result,
                "fill": cmd_fill, "flatten": cmd_flatten, "log": cmd_log}[a.cmd](a)
    except TicketError as e:
        print("ticket.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

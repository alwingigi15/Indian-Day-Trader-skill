#!/usr/bin/env python3
"""Pre-trade risk check for trader-risk. Standard library only, no network.

Reads the proposed orders (trader/orders.json) and checks every one against
your rules (trader/config.json), your account (trader/portfolio.json),
prices (trader/prices.json) and what was placed today (trader/state/broker.json).
Each order gets PASS or BLOCK with the reasons. Orders are checked in file
order as if each PASS had filled, so buys that are fine one by one but break
a limit together are caught. Everything is NSE stocks, rupees and IST.

    python check.py                                 # the trader/ files, writes trader/state/risk.json
    python check.py --now "2026-11-06 09:50"        # pin the clock (IST)
    python check.py --orders trader/orders.json --json
    python check.py --no-write                      # check without recording the verdicts

Entries and exits. An ENTRY opens or adds to a position (a buy, or a sell
that would go short). An EXIT only reduces what you hold. Getting out must
always be possible: an exit is checked for format, asset and short sales
only; every other rule turns into a warning for it.

What blocks an order (every order):

  format        missing symbol or side, no whole-share qty (NSE has no
                fractional shares and no rupee-amount orders), unknown
                order_type / product / validity, a LIMIT without a price, an
                SL / SL-M without a trigger_price, any price (price,
                trigger_price, stop_loss, target) that is not on a legal NSE tick
  asset         NSE F&O contracts (NIFTY25NOV24000CE, ...FUT) unless
                "allow_options": true; crypto and anything that is not an
                NSE/BSE stock symbol
  short         selling more than you hold, unless "allow_short": true

What blocks an entry (on top):

  blocked       a symbol on your "blocked" list
  series        series BE or BZ (trade-for-trade: no intraday, delivery only)
  size          under min_order_inr, over max_order_inr
  position      the stock would be over max_position_pct of the account
  cash          the buy would dip into your cash buffer, or need margin
  duplicate     the same symbol and side twice in this batch, or already
                placed today (trader/state/broker.json)
  daily limit   max_orders_per_day orders placed today
  stop          no stop while "require_stop": true (the order's stop_loss or
                its plan stop); a stop on the wrong side of the entry
  loss          qty x (entry - stop) + intraday charges over
                max_loss_per_trade_inr. The entry is the limit price for a
                limit order, else the HIGHER of the plan's entry and a fresh
                price (under 15 minutes old)
  reward:risk   (target - entry) / (entry - stop) under min_reward_risk
  daily loss    the max losses at the stops of today's entries plus this one
                over max_daily_loss_inr (win or lose, each counts in full)
  trades        max_trades_per_day entries already placed today
  window        before no_entries_before or after no_entries_after (IST), or
                the market is closed (weekend, NSE holiday, outside 9:15-15:30)
  mis cutoff    a new MIS entry at or after mis_entry_cutoff (default 15:00)
  circuit       an entry priced outside the day's circuit band (when the
                order, its plan or trader/prices.json carries upper_circuit /
                lower_circuit); within 1% of the upper circuit is a warning

There is no pattern-day-trader rule in India, so there is no PDT check.

Exit code 0 when every order passes, 1 when any order is blocked.
The verdicts go to trader/state/risk.json with a fingerprint of each order,
so trader-broker only shows an order as ready if THIS exact order passed today.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING

DEFAULTS = {
    "budget": None, "cash_buffer_pct": 10, "max_position_pct": 75, "max_order_inr": 80000,
    "min_order_inr": 500, "max_loss_per_trade_inr": 1000, "max_daily_loss_inr": 3000, "max_trades_per_day": 3,
    "max_orders_per_day": 10, "min_reward_risk": 1.5, "require_stop": True,
    "no_entries_before": "09:30", "no_entries_after": "14:45", "mis_entry_cutoff": "15:00", "flat_by": "15:10",
    "exchange": "NSE", "product": "MIS", "broker": "none",
    "paper": True, "blocked": [], "allow_options": False, "allow_margin": False, "allow_short": False,
}
# The neutral order shape (trader-budget writes it, trader-broker translates it). The fingerprint
# covers these fields only; "why", "plan" and the estimates are notes.
ORDER_FIELDS = ("symbol", "exchange", "tradingsymbol", "side", "qty", "order_type", "price", "trigger_price",
                "product", "validity", "stop_loss", "target", "client_order_id")
PRICE_FIELDS = ("price", "trigger_price", "stop_loss", "target")
ORDER_TYPES = {"MARKET", "LIMIT", "SL", "SL-M"}
PRODUCTS = {"MIS", "CNC"}
VALIDITIES = {"DAY", "IOC"}
TICKER = re.compile(r"^[A-Z0-9&\-]{1,20}(\.(NS|BO))?$")
# NSE F&O: monthly NIFTY25NOV24000CE / RELIANCE25NOVFUT, weekly NIFTY2511124000CE (YY, M or O/N/D, DD)
FNO = re.compile(r"^[A-Z0-9&\-]+?\d{2}(([A-Z]{3})|([1-9OND]\d{2}))(\d+(\.\d+)?(CE|PE)|FUT)$")
HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
PAISA = 0.005
FRESH_MINUTES = 15
REF_WORDS = {"plan": "the plan's entry", "price": "the latest price", "limit": "the limit price",
             "trigger": "the trigger price", "stale price": "an old price"}

OPEN = datetime.time(9, 15)      # NSE regular session, IST
CLOSE = datetime.time(15, 30)

# NSE equity trading holidays (weekdays), from the NSE 2026 holiday circular (checked 2026-11-06).
# 2027 is not published yet: add it to trader/market_india.json {"holidays": {"YYYY-MM-DD": "name"}}.
NSE_HOLIDAYS = {
    "2026-01-15": "Municipal Corporation Election in Maharashtra",
    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",
    "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram",
    "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-10": "Diwali Balipratipada",
    "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
    "2026-12-25": "Christmas",
}
MARKET_FILE = "trader/market_india.json"


def load_holidays(path=MARKET_FILE):
    """Built-in NSE holidays plus any in trader/market_india.json ("holidays": {date: name})."""
    out = dict(NSE_HOLIDAYS)
    try:
        with open(path, encoding="utf-8") as fh:
            out.update((json.load(fh) or {}).get("holidays") or {})
    except (OSError, ValueError, AttributeError):
        pass
    return out


class RiskError(Exception):
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
        raise RiskError("--now wants a time like \"2026-11-06 09:50\" (IST) or ...Z (UTC)")
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def parse_utc(text):
    if not text:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None)
    return dt


def trading_day(d, holidays=None):
    """(is_trading_day, reason). Weekends and the NSE holiday list."""
    if d.weekday() >= 5:
        return False, "weekend"
    name = (holidays if holidays is not None else load_holidays()).get(d.isoformat())
    if name:
        return False, "NSE holiday: " + name
    return True, ""


def hhmm(value, key):
    """"09:30" -> time(9, 30); None or "" -> None (that bound is off)."""
    if value in (None, "", False):
        return None
    m = HHMM.match(str(value).strip())
    if not m:
        raise RiskError("%s must be an IST time like \"09:30\", got %r" % (key, value))
    return datetime.time(int(m.group(1)), int(m.group(2)))


def window_check(now_ist, c):
    """Reasons an entry cannot be placed now (IST). Empty when the window is off."""
    before = hhmm(c.get("no_entries_before"), "no_entries_before")
    after = hhmm(c.get("no_entries_after"), "no_entries_after")
    if before is None and after is None:
        return [], []
    warns = []
    holidays = load_holidays()
    years = sorted({int(k[:4]) for k in holidays if k[:4].isdigit()})
    d, t = now_ist.date(), now_ist.time().replace(second=0, microsecond=0)
    if d.year not in years:
        warns.append("the NSE holiday list covers %s only: check nseindia.com for %s and add it to %s"
                     % (" and ".join(map(str, years)), d.isoformat(), MARKET_FILE))
    ok, why = trading_day(d, holidays)
    stamp = now_ist.strftime("%a %Y-%m-%d %H:%M")
    if not ok:
        return ["window: the market is closed today (%s, %s IST)" % (why, stamp)], warns
    if t < OPEN or now_ist.time() >= CLOSE:
        return ["window: the market is closed (%s IST; the NSE session is %s to %s)"
                % (stamp, OPEN.strftime("%H:%M"), CLOSE.strftime("%H:%M"))], warns
    if before is not None and t < before:
        return ["window: no new entries before %s IST (it is %s)" % (before.strftime("%H:%M"),
                                                                     t.strftime("%H:%M"))], warns
    if after is not None and t > after:
        return ["window: no new entries after %s IST (it is %s)" % (after.strftime("%H:%M"),
                                                                    t.strftime("%H:%M"))], warns
    return [], warns


# ---------------------------------------------------------------- NSE symbols, ticks, charges

DEFAULT_SUFFIX = ".NS"


def nse_symbol(sym):
    """RELIANCE -> RELIANCE.NS; RELIANCE.NS, TCS.BO and ^NSEI unchanged. Stored form."""
    s = str(sym).strip().upper()
    if s and not s.startswith("^") and not s.endswith((".NS", ".BO")):
        s += DEFAULT_SUFFIX
    return s


# NSE cash-segment tick sizes, circular NSE/CMTR/67133 (effective 2025-04-15; NSE reviews them monthly).
TICK_TABLE = (
    (Decimal("250"), Decimal("0.01")),
    (Decimal("1000"), Decimal("0.05")),
    (Decimal("5000"), Decimal("0.10")),
    (Decimal("10000"), Decimal("0.50")),
    (Decimal("20000"), Decimal("1.00")),
    (None, Decimal("5.00")),
)


def tick_size(price):
    p = Decimal(str(price))
    for limit, tick in TICK_TABLE:
        if limit is None or p < limit:
            return tick


def round_to_tick(price, direction="down"):
    """A legal NSE price. "down" for stops and long targets, "up" for buy limits."""
    p = Decimal(str(price))
    t = tick_size(p)
    mode = ROUND_FLOOR if direction == "down" else ROUND_CEILING
    return (p / t).to_integral_value(rounding=mode) * t


def on_tick(price):
    """Is this price a multiple of its NSE tick?"""
    p = Decimal(str(round(float(price), 6))).normalize()
    return p % tick_size(p) == 0


DEFAULT_CHARGES = {"brokerage_pct": 0.03, "brokerage_max_per_order": 20.0, "stt_sell_pct": 0.025,
                   "exchange_txn_pct": 0.00307, "sebi_per_crore": 10.0, "stamp_buy_pct": 0.003, "gst_pct": 18.0}


def intraday_charges(qty, buy_price, sell_price, ch=None):
    """Total ₹ cost of one round trip (buy then sell the same day, NSE equity MIS)."""
    c = dict(DEFAULT_CHARGES)
    c.update(ch or {})
    buy_val, sell_val = qty * buy_price, qty * sell_price
    turnover = buy_val + sell_val

    def brokerage(v):
        return min(v * c["brokerage_pct"] / 100.0, c["brokerage_max_per_order"])

    brok = brokerage(buy_val) + brokerage(sell_val)
    stt = sell_val * c["stt_sell_pct"] / 100.0
    exch = turnover * c["exchange_txn_pct"] / 100.0
    sebi = turnover * c["sebi_per_crore"] / 1e7
    stamp = buy_val * c["stamp_buy_pct"] / 100.0
    gst = (brok + exch + sebi) * c["gst_pct"] / 100.0
    return round(brok + stt + exch + sebi + stamp + gst, 2)


# ---------------------------------------------------------------- files and helpers

def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise RiskError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise RiskError("%s is not valid JSON: %s" % (path, e))


def fingerprint(order):
    """Same function in trader-broker/ticket.py: the exact order that was checked."""
    core = {k: order[k] for k in ORDER_FIELDS if k in order and order[k] is not None}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _f(x):
    if isinstance(x, bool):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def money(x):
    return "₹" + "{:,.2f}".format(float(x)).replace(".00", "")


def _p(x):
    return "%.2f" % float(x)


def account(cfg, portfolio, prices):
    """Starting account: cash and position values. No portfolio = a fresh
    account funded with the budget."""
    warnings = []
    if portfolio is None:
        budget = _f(cfg.get("budget"))
        if not budget:
            raise RiskError("no trader/portfolio.json and no budget in the config: "
                            "nothing to check the orders against")
        return {"cash": budget, "shares": {}, "values": {}}, warnings
    cash = _f(portfolio.get("cash"))
    if cash is None:
        raise RiskError("portfolio.json needs a \"cash\" number")
    shares, values = {}, {}
    for p in portfolio.get("positions", []):
        raw = p.get("ticker") or p.get("symbol") or ""
        if not str(raw).strip():
            continue
        t = nse_symbol(raw)
        n = _f(p.get("shares", p.get("qty")))
        if n is None:
            n = sum(_f(l.get("shares")) or 0 for l in p.get("lots", []))
        shares[t] = shares.get(t, 0) + n
        if prices.get(t):
            values[t] = shares[t] * prices[t]
        else:
            basis = sum(_f(l.get("cost")) or 0 for l in p.get("lots", []))
            values[t] = basis
            warnings.append("no price for %s: valued at its cost basis %s" % (t, money(basis)))
    return {"cash": cash, "shares": shares, "values": values}, warnings


def rec_day(rec):
    return str(rec.get("date") or str(rec.get("at", ""))[:10])


def rec_is_entry(rec):
    """trader-broker logs the kind trader-risk gave; older records: a buy is an entry."""
    k = rec.get("kind")
    if k:
        return k == "entry"
    return str(rec.get("side", "")).lower() == "buy"


def placed_today(state, today):
    """Orders recorded as placed today by trader-broker."""
    out = []
    for rec in (state or {}).get("orders", []):
        if rec.get("status") == "placed" and rec_day(rec) == today:
            out.append(rec)
    return out


def _circuit(sources, sym):
    """(lower, upper) from the first source that has them."""
    for src in sources:
        if not isinstance(src, dict):
            continue
        lo, hi = _f(src.get("lower_circuit")), _f(src.get("upper_circuit"))
        if lo or hi:
            return lo, hi
    return None, None


# ---------------------------------------------------------------- the check

def check(orders, cfg, portfolio=None, prices=None, state=None, today=None, now=None, price_times=None,
          circuits=None):
    """Pure function. Returns {"results": [...], "pass": n, "block": n, "warnings": [...]}.

    now          naive UTC (default: the real clock); its IST date is "today"
    price_times  {SYM: naive UTC time of that price}; None = every price counts as fresh
    circuits     {SYM: {"lower_circuit": x, "upper_circuit": y}} (e.g. from trader/prices.json)"""
    c = dict(DEFAULTS)
    c.update(cfg or {})
    ch = c.get("charges") or {}
    prices = {nse_symbol(k): float(v) for k, v in (prices or {}).items() if _f(v)}
    if price_times is not None:
        price_times = {nse_symbol(k): v for k, v in price_times.items()}
    circuits = {nse_symbol(k): v for k, v in (circuits or {}).items()}
    now_utc = now or parse_now(None)
    now_ist = to_ist(now_utc)
    today = today or now_ist.date().isoformat()
    acct, top_warn = account(c, portfolio, prices)
    blocked = {nse_symbol(b) for b in c.get("blocked") or []}
    done_today = placed_today(state, today)
    count_today = len(done_today)
    entries_today = [r for r in done_today if rec_is_entry(r)]
    loss_today = 0.0
    for r in entries_today:
        ml = _f(r.get("max_loss"))
        if ml is None:
            top_warn.append("an entry placed today (%s %s) has no max loss in the log; not counted in "
                            "the daily loss" % (r.get("side"), r.get("symbol")))
        else:
            loss_today += ml
    placed_keys = {(nse_symbol(r.get("symbol", "")), str(r.get("side", "")).lower()) for r in done_today}
    win_reasons, win_warns = window_check(now_ist, c)
    cutoff = hhmm(c.get("mis_entry_cutoff"), "mis_entry_cutoff")
    seen = set()
    results = []
    batch_entries, batch_loss = 0, 0.0
    if not c.get("paper", True):
        top_warn.append("\"paper\": false: these orders are for real money")
    top_warn.extend(win_warns)

    def fresh(sym):
        if sym not in prices:
            return None
        if price_times is None:
            return prices[sym]
        t = price_times.get(sym)
        if t is None:
            return None
        age = (now_utc - t).total_seconds()
        return prices[sym] if -60 <= age <= FRESH_MINUTES * 60 else None

    for i, o in enumerate(orders, 1):
        reasons, warns, info = [], [], []
        later = []                       # entry-only reasons, turned into warnings for an exit
        raw = str(o.get("symbol", "")).strip().upper()
        base = raw[:-3] if raw.endswith((".NS", ".BO")) else raw
        sym = nse_symbol(raw) if raw else ""
        side = str(o.get("side", "")).strip().lower()
        typ = str(o.get("order_type", "MARKET")).strip().upper()
        product = str(o.get("product") or c.get("product") or "MIS").strip().upper()
        validity = str(o.get("validity", "DAY")).strip().upper()
        qty = o.get("qty")
        plan = o.get("plan") if isinstance(o.get("plan"), dict) else {}
        value = None

        # format
        if not raw:
            reasons.append("format: no symbol")
        if side not in ("buy", "sell"):
            reasons.append("format: side must be BUY or SELL, got %r" % o.get("side"))
        q = _f(qty)
        if o.get("notional") is not None:
            reasons.append("format: NSE orders are a whole number of shares (qty), not a rupee amount")
        if q is None or q <= 0:
            reasons.append("format: qty must be a positive whole number of shares, got %r" % qty)
            q = None
        elif abs(q - round(q)) > 1e-9:
            reasons.append("format: NSE takes whole shares only, got qty %r" % qty)
        for k in PRICE_FIELDS:
            if o.get(k) is not None:
                v = _f(o.get(k))
                if v is None or v <= 0:
                    reasons.append("format: %s must be a positive number, got %r" % (k, o.get(k)))
                elif not on_tick(v):
                    reasons.append("format: %s %s is not on the NSE tick of %s (try %s or %s); the exchange "
                                   "rejects it" % (k, _p(v), tick_size(v), round_to_tick(v, "down"),
                                                   round_to_tick(v, "up")))
        if typ not in ORDER_TYPES:
            reasons.append("format: unknown order_type %r (MARKET, LIMIT, SL or SL-M)" % typ)
        if product not in PRODUCTS:
            reasons.append("format: unknown product %r (MIS for intraday, CNC for delivery)" % product)
        if validity not in VALIDITIES:
            reasons.append("format: unknown validity %r (DAY or IOC)" % validity)
        if typ in ("LIMIT", "SL") and o.get("price") is None:
            reasons.append("format: a %s order needs a price" % typ)
        if typ in ("SL", "SL-M") and o.get("trigger_price") is None:
            reasons.append("format: a %s order needs a trigger_price" % typ)

        # asset
        if raw and FNO.match(base.replace(" ", "")):
            if not c.get("allow_options"):
                reasons.append("asset: %s is an NSE F&O contract; futures and options are off "
                               "(\"allow_options\": false)" % raw)
            else:
                warns.append("F&O order: options can lose 100%% of what you pay, futures more")
        elif raw and ("/" in raw or not TICKER.match(raw)):
            reasons.append("asset: %s does not look like an NSE/BSE stock symbol; "
                           "this pack trades NSE stocks only" % raw)

        # the reference price: what one share costs to get in
        plan_entry = _f(plan.get("entry"))
        live = fresh(sym)
        ref, ref_from = None, None
        if typ in ("LIMIT", "SL"):
            ref, ref_from = _f(o.get("price")), "limit"
        elif typ == "SL-M":
            ref, ref_from = _f(o.get("trigger_price")), "trigger"
        if ref is None:
            cands = [(p, w) for p, w in ((plan_entry, "plan"), (live, "price")) if p]
            if cands:
                ref, ref_from = max(cands, key=lambda x: x[0])
                if ref_from == "price":
                    ref = float(round_to_tick(round(ref, 6), "up"))
                if live and ref_from == "price" and plan_entry and live > plan_entry + PAISA:
                    info.append("%s last %s is above the plan's entry %s: the risk is measured from %s"
                                % (sym, _p(live), _p(plan_entry), _p(ref)))
            elif sym in prices:
                ref, ref_from = prices[sym], "stale price"
        if plan_entry and live is None and sym in prices and price_times is not None and typ == "MARKET":
            warns.append("the %s price in trader/prices.json is older than %d minutes: not used"
                         % (sym, FRESH_MINUTES))
        if q and ref:
            value = q * ref

        # entry or exit
        held = acct["shares"].get(sym, 0.0)
        held_value = acct["values"].get(sym, 0.0)
        exiting = False
        if side == "sell" and held > 1e-9 and q is not None:
            exiting = q <= held + 1e-9
        elif side == "buy" and held < -1e-9 and q is not None:
            exiting = q <= -held + 1e-9
        kind = "exit" if exiting else "entry"
        if kind == "entry" and value is None and q:
            later.append("price: no price for %s to size a %s-share order; add it to "
                         "trader/prices.json (trader-alerts: alerts.py quote %s --write)" % (sym, qty, sym))

        # blocked list, series and size
        if sym in blocked:
            later.append("blocked: %s is on your blocked list" % sym)
        series = str(o.get("series") or plan.get("series") or "").strip().upper()
        if series in ("BE", "BZ"):
            later.append("series: %s trades in the %s series (trade-for-trade): no intraday, every buy must be "
                         "taken to delivery and cannot be sold the same day" % (sym, series))
        if value is not None:
            if value < float(c.get("min_order_inr") or 0) - PAISA:
                later.append("size: %s is under your %s minimum order"
                             % (money(value), money(c["min_order_inr"])))
            if c.get("max_order_inr") and value > float(c["max_order_inr"]) + PAISA:
                later.append("size: %s is over your %s max order"
                             % (money(value), money(c["max_order_inr"])))

        # account effects, simulated in order
        cash = acct["cash"]
        total = cash + sum(acct["values"].values())
        buffer_inr = total * float(c.get("cash_buffer_pct") or 0) / 100.0
        if value is not None and side == "buy" and kind == "entry":
            after_cash = cash - value
            if after_cash < -PAISA and not c.get("allow_margin"):
                later.append("cash: needs %s but only %s cash; that would be margin (borrowed money, MIS "
                             "leverage included)" % (money(value), money(cash)))
            elif after_cash < buffer_inr - PAISA:
                later.append("cash: leaves %s cash, under your %s%% buffer of %s"
                             % (money(max(after_cash, 0)), _pct(c["cash_buffer_pct"]), money(buffer_inr)))
            pos_after = held_value + value
            limit = total * float(c.get("max_position_pct") or 100) / 100.0
            if total > 0 and pos_after > limit + PAISA:
                later.append("position: %s would be %s, %.1f%% of the %s account, over your %s%% cap"
                             % (sym, money(pos_after), 100.0 * pos_after / total, money(total),
                                _pct(c["max_position_pct"])))
        if side == "sell" and sym and kind == "entry":
            over = (q > held + 1e-9) if q is not None else held <= 0
            if over and not c.get("allow_short"):
                reasons.append("short: selling more %s than you hold (%s shares); short selling is off. "
                               "If you bought it, record the fill first (ticket.py fill)" % (sym, _num(held)))

        # day-trade checks: stop, loss, reward:risk, daily loss, trades, window, MIS cutoff, circuit
        max_loss = risk_ps = rr = stop = target = charges = None
        if kind == "entry" and side in ("buy", "sell"):
            long = side == "buy"
            leg_stop, plan_stop = _f(o.get("stop_loss")), _f(plan.get("stop"))
            stop = leg_stop if leg_stop else plan_stop
            target = _f(o.get("target")) or _f(plan.get("target"))
            if leg_stop and plan_stop and abs(leg_stop - plan_stop) > PAISA:
                warns.append("the order's stop_loss %s differs from the plan's stop %s: the order's is the one "
                             "the ticket uses" % (_p(leg_stop), _p(plan_stop)))
            if stop and not leg_stop:
                warns.append("the stop %s is in the plan only, not on the order: set a stop alert "
                             "(trader-alerts: alerts.py add --from-plan)" % _p(stop))
            if not stop:
                if c.get("require_stop", True):
                    later.append("stop: no stop. Every entry needs one (\"require_stop\": true): the order's "
                                 "stop_loss, from trader-exit and trader-budget")
                else:
                    warns.append("no stop: the max loss is not capped, so it is not counted in the daily loss")
            elif ref:
                if (long and stop >= ref - PAISA) or (not long and stop <= ref + PAISA):
                    later.append("stop: the stop %s must be %s the entry %s for a %s"
                                 % (_p(stop), "below" if long else "above", _p(ref), side))
                else:
                    risk_c = int(round(abs(ref - stop) * 100))
                    risk_ps = risk_c / 100.0
                    shares = q or 0
                    gross = round(shares * risk_ps, 2)
                    charges = intraday_charges(shares, ref if long else stop, stop if long else ref, ch)
                    max_loss = round(gross + charges, 2)
                    cap = _f(c.get("max_loss_per_trade_inr"))
                    if cap and max_loss > cap + PAISA:
                        later.append("loss: %s shares x %s risk a share = %s + %s charges = %s at the stop, over "
                                     "your %s max loss per trade" % (_num(shares), _p(risk_ps), money(gross),
                                                                    money(charges), money(max_loss), money(cap)))
                    min_rr = _f(c.get("min_reward_risk"))
                    if target:
                        if (long and target <= ref + PAISA) or (not long and target >= ref - PAISA):
                            later.append("target: the target %s must be %s the entry %s"
                                         % (_p(target), "above" if long else "below", _p(ref)))
                        else:
                            rr = round(int(round(abs(target - ref) * 100)) / float(risk_c), 2)
                            if min_rr and rr < min_rr - 1e-9:
                                later.append("reward:risk: %.2f (target %s, entry %s, stop %s) is under your "
                                             "minimum %s" % (rr, _p(target), _p(ref), _p(stop), _num(min_rr)))
                    elif min_rr:
                        later.append("reward:risk: no target to measure it against (min_reward_risk %s)"
                                     % _num(min_rr))
            daily = _f(c.get("max_daily_loss_inr"))
            if daily and max_loss is not None:
                used = loss_today + batch_loss
                if used + max_loss > daily + PAISA:
                    later.append("daily loss: %s already at risk today plus %s for this one = %s, over your %s "
                                 "daily loss limit" % (money(used), money(max_loss), money(used + max_loss),
                                                       money(daily)))
            cap_t = int(c.get("max_trades_per_day") or 0)
            if cap_t and len(entries_today) + batch_entries >= cap_t:
                later.append("trades: %d entries placed today plus %d passed earlier in this batch, your limit "
                             "is %d a day" % (len(entries_today), batch_entries, cap_t))
            later.extend(win_reasons)
            t_now = now_ist.time().replace(second=0, microsecond=0)
            if product == "MIS" and cutoff is not None and t_now >= cutoff:
                later.append("mis cutoff: no new MIS (intraday) entries from %s IST (it is %s); brokers stop "
                             "taking them and square off MIS soon after" % (cutoff.strftime("%H:%M"),
                                                                             t_now.strftime("%H:%M")))
            lo, hi = _circuit([o, plan, circuits.get(sym)], sym)
            if ref and (lo or hi):
                if (hi and ref > hi + PAISA) or (lo and ref < lo - PAISA):
                    later.append("circuit: the entry %s is outside today's circuit band %s to %s; the exchange "
                                 "rejects it" % (_p(ref), _p(lo) if lo else "?", _p(hi) if hi else "?"))
                elif hi and long and (hi - ref) <= hi * 0.01 + PAISA:
                    warns.append("circuit: %s is within 1%% of its upper circuit %s; if it locks there you "
                                 "may not be able to get out at your price" % (sym, _p(hi)))
            if stop and ref and risk_ps:
                info.append("entry %s (%s), stop %s, target %s: risk %s a share"
                            % (_p(ref), REF_WORDS.get(ref_from, ref_from), _p(stop),
                               _p(target) if target else "none", _p(risk_ps)))
                info.append("max loss %s incl. %s charges%s, reward:risk %s%s"
                            % (money(max_loss), money(charges), (" (limit %s)" % money(c["max_loss_per_trade_inr"]))
                               if _f(c.get("max_loss_per_trade_inr")) else "",
                               ("%.2f" % rr) if rr is not None else "n/a",
                               (" (min %s)" % _num(c["min_reward_risk"])) if _f(c.get("min_reward_risk")) else ""))
            if daily or cap_t:
                info.append("today before this: %d of %s trades, %s of %s daily loss"
                            % (len(entries_today) + batch_entries, cap_t or "unlimited",
                               money(loss_today + batch_loss), money(daily) if daily else "no limit on"))

        # duplicates and the daily order limit
        key = (sym, side)
        if key in seen:
            later.append("duplicate: a second %s %s in this batch" % (side, sym))
        if key in placed_keys:
            later.append("duplicate: a %s %s was already placed today" % (side, sym))
        passed_so_far = sum(1 for r in results if r["verdict"] == "PASS")
        cap_n = int(c.get("max_orders_per_day") or 0)
        if cap_n and count_today + passed_so_far >= cap_n:
            later.append("daily limit: %d placed today plus %d passed earlier in this batch, "
                         "your limit is %d a day" % (count_today, passed_so_far, cap_n))
        seen.add(key)

        if kind == "entry":
            reasons.extend(later)
        else:
            warns.extend("exit, not blocked: " + x for x in later)

        verdict = "BLOCK" if reasons else "PASS"
        if verdict == "PASS":
            if kind == "entry" and side in ("buy", "sell"):
                batch_entries += 1
                batch_loss += max_loss or 0.0
            if side == "buy":
                if value is not None:
                    acct["cash"] -= value
                    acct["values"][sym] = acct["values"].get(sym, 0.0) + value
                if q is not None:
                    acct["shares"][sym] = held + q
            elif side == "sell":
                if value is not None:
                    acct["cash"] += value
                    acct["values"][sym] = max(acct["values"].get(sym, 0.0) - value, 0.0)
                if q is not None:
                    acct["shares"][sym] = held - q
        results.append({
            "index": i, "symbol": sym, "side": side, "kind": kind, "product": product,
            "amount": "%s shares" % _num(q) if q is not None else "%s shares" % qty,
            "value_inr": round(value, 2) if value is not None else None,
            "entry_ref": round(ref, 2) if ref else None, "entry_ref_from": ref_from,
            "stop": stop, "target": target, "risk_per_share": risk_ps, "charges_inr": charges,
            "max_loss": max_loss, "reward_risk": rr, "fingerprint": fingerprint(o), "verdict": verdict,
            "reasons": reasons, "warnings": warns, "info": info,
        })

    return {
        "date": today,
        "now_ist": now_ist.strftime("%Y-%m-%d %H:%M"),
        "currency": "INR",
        "paper": bool(c.get("paper", True)),
        "results": results,
        "pass": sum(1 for r in results if r["verdict"] == "PASS"),
        "block": sum(1 for r in results if r["verdict"] == "BLOCK"),
        "warnings": top_warn,
        "cash_after": round(acct["cash"], 2),
        "today": {"orders_placed": count_today, "entries_placed": len(entries_today),
                  "loss_at_risk": round(loss_today, 2)},
    }


def _pct(x):
    x = float(x)
    return str(int(x)) if x == int(x) else ("%.2f" % x)


def _num(x):
    x = float(x)
    return str(int(x)) if x == int(x) else ("%.4f" % x).rstrip("0")


def report(res):
    lines = []
    for w in res["warnings"]:
        lines.append("WARNING: " + w)
    if res["warnings"]:
        lines.append("")
    for r in res["results"]:
        lines.append("%-5s #%d %s %s %s %s, %s" % (r["verdict"], r["index"], r["side"].upper(), r["symbol"],
                                                   r["amount"], r["product"], r["kind"]))
        for x in r.get("info", []):
            lines.append("          " + x)
        for x in r["reasons"]:
            lines.append("        - " + x)
        for x in r["warnings"]:
            lines.append("        ~ " + x)
    lines.append("")
    lines.append("%d passed, %d blocked at %s IST. Cash after the passed orders: %s."
                 % (res["pass"], res["block"], res["now_ist"], money(res["cash_after"])))
    if res["block"]:
        lines.append("Blocked orders do not go to the broker. Fix the order or your rules, then check again.")
    else:
        lines.append("Every order passed. trader-broker still asks you yes or no for each one.")
    lines.append("Education and tooling, not financial advice.")
    return "\n".join(lines)


def load_prices(pdoc):
    """prices.json -> ({SYM: price}, {SYM: price time as naive UTC or None}, {SYM: circuit detail}).

    The price time is the quote's own time (detail.time), else when it was fetched
    (detail.fetched), else the file's as_of."""
    if not isinstance(pdoc, dict):
        return {}, {}, {}
    prices = pdoc.get("prices", pdoc)
    if not isinstance(prices, dict):
        return {}, {}, {}
    out, times, circ = {}, {}, {}
    as_of = parse_utc(pdoc.get("as_of"))
    detail = pdoc.get("detail") or {}
    for s, p in prices.items():
        if _f(p) is None:
            continue
        d = detail.get(s) or detail.get(nse_symbol(s)) or {}
        s = nse_symbol(s)
        out[s] = float(p)
        times[s] = parse_utc(d.get("time")) or parse_utc(d.get("fetched")) or as_of
        if d.get("upper_circuit") or d.get("lower_circuit"):
            circ[s] = {"upper_circuit": d.get("upper_circuit"), "lower_circuit": d.get("lower_circuit")}
    return out, times, circ


def main(argv=None):
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Check proposed NSE orders against your limits (trader-risk).")
    ap.add_argument("--orders", default="trader/orders.json")
    ap.add_argument("--config", default="trader/config.json")
    ap.add_argument("--portfolio", default="trader/portfolio.json",
                    help="your account; if missing, a fresh account funded with the budget")
    ap.add_argument("--prices", default="trader/prices.json")
    ap.add_argument("--state", default="trader/state/broker.json", help="what trader-broker placed today")
    ap.add_argument("--now", help="pretend it is this IST time, e.g. \"2026-11-06 09:50\"")
    ap.add_argument("--today", help="YYYY-MM-DD (default: today in India, IST)")
    ap.add_argument("--out", default="trader/state/risk.json", help="where the verdicts are recorded")
    ap.add_argument("--no-write", action="store_true", help="do not record the verdicts")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        now = parse_now(a.now)
        if a.today and not re.match(r"^\d{4}-\d{2}-\d{2}$", a.today):
            raise RiskError("--today wants a date like 2026-11-06")
        doc = read_json(a.orders, "orders file (run trader-budget first)")
        orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
        if not isinstance(orders, list) or not orders:
            raise RiskError("no orders in %s" % a.orders)
        cfg = read_json(a.config, "config", required=False) or {}
        portfolio = read_json(a.portfolio, "portfolio", required=False)
        pdoc = read_json(a.prices, "prices", required=False) or {}
        prices, times, circ = load_prices(pdoc)
        state = read_json(a.state, "broker state", required=False)
        res = check(orders, cfg, portfolio, prices, state, a.today, now, times, circ)
        if pdoc.get("as_of"):
            res["prices_as_of"] = pdoc["as_of"]
        if not a.no_write:
            d = os.path.dirname(a.out)
            if d:
                os.makedirs(d, exist_ok=True)
            rec = dict(res)
            rec["checked_at"] = datetime.datetime.now(datetime.timezone.utc).replace(
                microsecond=0, tzinfo=None).strftime("%Y-%m-%dT%H:%M:%SZ")
            rec["orders_file"] = a.orders
            tmp = a.out + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(rec, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp, a.out)
    except RiskError as e:
        print("check.py: %s" % e, file=sys.stderr)
        return 1
    print(json.dumps(res, indent=2, ensure_ascii=False) if a.json else report(res))
    return 1 if res["block"] else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Time Machine for trader-timemachine (NSE, India). Standard library only.

Replays a planned day trade before you risk a rupee: 10,000 simulated runs of
the rest of the session from your entry, plus the same rule on every past
session in the data, both net of Indian intraday charges.

    python3 timemachine.py run                          # trader/plan.json, 10,000 replays, seed 19
    python3 timemachine.py run --write                  # also save trader/timemachine.json
    python3 timemachine.py run --until 15:10            # stop each replay at this time (default: flat_by)
    python3 timemachine.py run --seed 7 --replays 20000 --json
    python3 timemachine.py run --symbol RELIANCE.NS --date 2026-11-06 --entry 1311.90 \\
        --stop 1300.80 --target 1323.00 --entry-time "9:50 AM"
    python3 timemachine.py chart                        # ASCII fan chart of the replays
    python3 timemachine.py chart --from trader/timemachine.json

How the replays work:
  1. Fetch 60 days of 5-minute bars (Yahoo v8 chart, range=60d interval=5m;
     one request). The NSE session is 9:15 to 3:30 PM IST, 75 five-minute
     bars. Keep only past sessions (before the plan's date) that have every
     bar from 9:15 up to the end of the replay (--until, by default the plan's
     flat_by, 15:10). Sessions with a gap before then, NSE holidays, weekends
     and today are left out.
  2. The pool: for every bar slot 1..last of the day, each past session's bar
     as ratios to the previous bar's close: (open, high, low, close) / prev close.
  3. One replay starts at the entry price and walks the rest of the session.
     For each bar i it picks a slot j = i + randint(-window, window), kept in
     1..last, then one past bar from pool[j] (random.choice), and scales it to
     the current price. The stop is checked before the target in every bar,
     so a bar that touches both counts as the stop (the conservative call).
  4. Outcomes: "target" or "stop" (whichever was touched first), or "neither"
     when the trade is still open at --until (default the plan's flat_by,
     else "flat_by" in trader/config.json, else 15:10: brokers square off MIS
     positions from about 3:15 PM).
     "green" = target first, or above the entry at --until (below for a sell).
     Gross R = the target's R at the target, -1 at the stop, otherwise
     (price at --until - entry) / risk. A gap through the stop still counts
     -1R here; a real fill can be worse.
     Net R = gross R minus the round trip's charges in R:
     intraday_charges(qty, entry, exit) / (qty * risk). The quantity is the
     plan's "qty" (or --qty) if given, else floor(max_loss_per_trade_inr /
     risk) from trader/config.json, else a ₹1,00,000 position at the entry.
     Charge rates are Zerodha's intraday equity rates (zerodha.com/charges,
     checked 2026-11-06); "charges" in trader/config.json overrides them.
  5. The price at --until: median and the 5th to 95th percentile of all
     replays (pct(a, q) = sorted a at index int(q * n)). The fan chart uses a
     second run of 2,000 paths with the same seed. The histogram has 24 bins
     between the 1st and 99th percentile.
  6. History: the plan's own rule (an opening-range breakout with the same
     opening range, stop rule and target R) re-run on every one of those past
     sessions, first close beyond the range = entry, same stop-first rule,
     stops and targets on legal NSE ticks, the same charges.

The verdict line looks at the replays that hit one side (target or stop) and
compares the target's share of them, target / (target + stop), with the share
that breaks even at the plan's target R after charges:
(1 + cs) / (1 + R - ct + cs), where ct and cs are the charges in R at the
target and at the stop (with no charges: 1 / (1 + R), 50 % at 1R, 33 % at 2R).
The gap, in percentage points:
  less than 5 points    "Close to a coin flip" (1R) / "Close to break-even" (other R)
  5 to 15 points        "Leans toward the target" / "Leans toward the stop"
  15 points or more     "Strongly toward the target" / "Strongly toward the stop"
Below it: a note when more than 40 % of the replays hit neither (the price at
--until decides those), and the net average R when its sign goes against the lean.

5-minute bars only: Yahoo keeps 60 days of 5-min bars but only about 7 days of
1-min bars, too few sessions for a fair pool. The run refuses with a plain
message when there are fewer than 20 usable sessions, the plan's day is an NSE
holiday or a weekend, or the entry bar is not in the data. Same plan, same
data and same --seed give the same numbers on any machine. A simulation built
from the stock's own recent bars is not a prediction: results, news and
circuit limits can do what the last 60 days never did.

Data: https://query2.finance.yahoo.com/v8/finance/chart/<SYMBOL>.NS?range=60d&interval=5m
(unofficial and free, can lag; falls back to query1 on 429 or 5xx).
--offline FILE reads a saved chart JSON (.json or .json.gz) instead.
Times are India (IST, UTC+5:30) wall-clock; --now "2026-11-06 11:07" pins the clock.
"""

import argparse
import datetime
import gzip
import json
import math
import os
import random
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING

UA = "Mozilla/5.0 (compatible; day-trader-skill/3.0)"   # NSE drops requests whose User-Agent holds a URL
PAUSE = 0.5          # seconds between requests: be polite to free endpoints
BACKOFF = 5.0        # seconds to wait once after a 429
HOSTS = ("query2", "query1")
INTERVAL = 300       # 5-minute bars, in seconds
MIN_SESSIONS = 20
SAMPLE_PATHS = 160
BAND_PATHS = 2000
BAND_Q = (0.05, 0.25, 0.5, 0.75, 0.95)
HIST_BINS = 24
COIN_FLIP = 0.05     # verdict thresholds, see the docstring
STRONG = 0.15
MANY_NEITHER = 0.40
EPOCH = datetime.datetime(1970, 1, 1)
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
CONFIG_FILE = "trader/config.json"
DEFAULT_FLAT_BY = "15:10"     # brokers auto-square-off MIS from about 15:15 and charge for it
NOTIONAL_INR = 100000.0       # position size for the charges when there is no qty and no max loss


class TMError(Exception):
    pass


# ---------------------------------------------------------------- time (IST, shared conventions)

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


# ---------------------------------------------------------------- session and calendar (NSE)

OPEN = datetime.time(9, 15)      # NSE regular session, IST
CLOSE = datetime.time(15, 30)
OPEN_MIN = OPEN.hour * 60 + OPEN.minute
CLOSE_MIN = CLOSE.hour * 60 + CLOSE.minute
SESSION_BARS = (CLOSE_MIN - OPEN_MIN) * 60 // INTERVAL    # 75 five-minute bars

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


def closed_reason(day_iso, holidays):
    """Why NSE is shut on that date ("Saturday", a holiday's name), or None on a trading day."""
    d = datetime.date.fromisoformat(day_iso)
    if d.weekday() >= 5:
        return WEEKDAYS[d.weekday()]
    return holidays.get(day_iso)


# ---------------------------------------------------------------- tick size and symbols (NSE)

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


def tick_away(x, long):
    """Round a stop to a legal tick, away from the entry: down for a buy, up for a sell.
    Float noise from Yahoo (1305.0499999) is cleared first, at 6 decimals."""
    return float(round_to_tick(round(x, 6), "down" if long else "up"))


DEFAULT_SUFFIX = ".NS"
SYMBOL_RE = re.compile(r"^[A-Z0-9&\-]{1,20}(\.(NS|BO))?$")


def nse_symbol(sym):
    """RELIANCE -> RELIANCE.NS; RELIANCE.NS, TCS.BO and ^NSEI unchanged. Stored form."""
    s = str(sym).strip().upper()
    if s and not s.startswith("^") and not s.endswith((".NS", ".BO")):
        s += DEFAULT_SUFFIX
    return s


def yahoo_symbol(sym):
    """URL-safe Yahoo symbol: M&M -> M%26M.NS."""
    return urllib.parse.quote(nse_symbol(sym), safe=".-^")


# ---------------------------------------------------------------- money and charges

def inr(v):
    """1234567 -> ₹12.35 lakh, 52300000000 -> ₹5,230.00 crore, small amounts to the paisa."""
    a = abs(v)
    if a >= 1e7:
        return "₹{:,.2f} crore".format(v / 1e7)
    if a >= 1e5:
        return "₹{:,.2f} lakh".format(v / 1e5)
    return "₹{:,.2f}".format(v)


# Zerodha intraday equity rates (zerodha.com/charges, checked 2026-11-06); "charges" in config overrides.
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


def load_config(path=CONFIG_FILE):
    """trader/config.json as a dict, {} when missing or broken (the defaults then apply)."""
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


def sizing_rule(plan, cfg):
    """How many shares the charges are worked out on: the plan's qty, else the config's
    max_loss_per_trade_inr / risk, else a ₹1,00,000 position. A dict for trade_qty()."""
    if plan.get("qty"):
        return {"basis": "qty", "qty": int(plan["qty"])}
    ml = cfg.get("max_loss_per_trade_inr")
    if isinstance(ml, (int, float)) and not isinstance(ml, bool) and ml > 0:
        return {"basis": "max_loss_per_trade_inr", "max_loss_inr": float(ml)}
    return {"basis": "notional", "notional_inr": NOTIONAL_INR}


def trade_qty(rule, entry, risk):
    """Whole shares, at least 1."""
    if rule["basis"] == "qty":
        q = rule["qty"]
    elif rule["basis"] == "max_loss_per_trade_inr":
        q = int(math.floor(rule["max_loss_inr"] / risk + 1e-9))
    else:
        q = int(math.floor(rule["notional_inr"] / entry + 1e-9))
    return max(1, q)


def charges_r(qty, entry, exit_price, risk, long, ch=None):
    """The round trip's charges in R (charges / (qty * risk)). A sell-first trade buys back at the exit."""
    cost = intraday_charges(qty, entry, exit_price, ch) if long else intraday_charges(qty, exit_price, entry, ch)
    return cost / (qty * risk)


# ---------------------------------------------------------------- clock

def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is taken as India time (IST)."""
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise TMError("--now wants a time like \"2026-11-06 11:07\" (IST) or ...Z (UTC)")
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def utc_of(ts):
    return EPOCH + datetime.timedelta(seconds=int(ts))


def parse_clock(text, what):
    """'10:15 AM', '10:15am', '15:10' or '3:10' (a bare 1 to 8 o'clock means p.m.) -> minutes after midnight."""
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*(?:([AaPp])\.?\s*[Mm]\.?)?\s*$", str(text or ""))
    if not m:
        raise TMError("%s wants an IST time like 10:15 AM or 15:10, not %r" % (what, text))
    h, mi, ap = int(m.group(1)), int(m.group(2)), (m.group(3) or "").lower()
    if mi > 59 or h > 23 or (ap and not 1 <= h <= 12):
        raise TMError("%s %r is not a clock time" % (what, text))
    if ap == "p" and h != 12:
        h += 12
    elif ap == "a" and h == 12:
        h = 0
    elif not ap and 1 <= h <= 8:
        h += 12
    return h * 60 + mi


def clock12(minutes):
    h, m = divmod(minutes, 60)
    return "%d:%02d %s" % (h % 12 or 12, m, "AM" if h < 12 else "PM")


def clock24(minutes):
    return "%02d:%02d" % divmod(minutes, 60)


# ---------------------------------------------------------------- data

def chart_url(sym, host="query2"):
    return "https://%s.finance.yahoo.com/v8/finance/chart/%s?range=60d&interval=5m" % (host, yahoo_symbol(sym))


def _sleep(seconds):
    time.sleep(seconds)


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def fetch_chart(sym):
    """One request for 60 days of 5-min bars. query2 first, one retry after a 429,
    then query1 on 429 or 5xx."""
    last = None
    sym = nse_symbol(sym)
    for n, host in enumerate(HOSTS):
        if n:
            _sleep(PAUSE)
        for attempt in (0, 1):
            url = chart_url(sym, host)
            try:
                return _get(url)
            except urllib.error.HTTPError as e:
                last = e.code
                try:
                    e.close()
                except Exception:
                    pass
                if e.code == 429 and attempt == 0:
                    _sleep(BACKOFF)
                    continue
                if e.code == 429 or e.code >= 500:
                    break
                if e.code == 404:
                    raise TMError("Yahoo has no chart for %s (HTTP 404): check the NSE symbol" % sym)
                raise TMError("Yahoo answered HTTP %d for %s" % (e.code, sym))
            except urllib.error.URLError as e:
                raise TMError("could not reach Yahoo: %s" % e.reason)
            except OSError as e:
                raise TMError("could not reach Yahoo: %s" % e)
    if last == 429:
        raise TMError("Yahoo is rate limiting (HTTP 429). Wait a minute and run it again.")
    raise TMError("Yahoo answered HTTP %s for %s on both hosts. Try again later." % (last, sym))


def read_offline(path, sym):
    """A saved Yahoo chart file (.json or .json.gz), or a folder holding <SYM>.NS-60d5m.json(.gz)."""
    sym = nse_symbol(sym)
    if os.path.isdir(path):
        for name in ("%s-60d5m.json" % sym, "%s-60d5m.json.gz" % sym):
            if os.path.exists(os.path.join(path, name)):
                path = os.path.join(path, name)
                break
        else:
            raise TMError("no %s-60d5m.json(.gz) in %s" % (sym, path))
    if not os.path.exists(path):
        raise TMError("no offline chart file %s" % path)
    opener = gzip.open if path.endswith(".gz") else open
    try:
        with opener(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    except OSError as e:
        raise TMError("could not read %s: %s" % (path, e))


def chart_result(text, sym):
    try:
        data = json.loads(text)
    except ValueError:
        raise TMError("Yahoo returned something that is not JSON for %s" % nse_symbol(sym))
    chart = data.get("chart") if isinstance(data, dict) else None
    if not isinstance(chart, dict):
        raise TMError("Yahoo returned something unexpected for %s" % nse_symbol(sym))
    res = chart.get("result")
    if not res:
        err = chart.get("error") or {}
        raise TMError("Yahoo has no data for %s: %s" % (nse_symbol(sym), err.get("description") or "unknown symbol"))
    return res[0]


def parse_bars(result, now_utc, holidays=None):
    """Yahoo chart result -> (meta, bars, dropped). Regular-session 5-min bars only,
    oldest first. Dropped: bars with a missing price, bars off the 5-minute grid
    (the live tick, e.g. 11:06:46), bars not complete yet (now < start + 5 min,
    where now is the earlier of --now and the data's own regularMarketTime), bars
    outside 9:15 to 15:30 IST, and bars on a weekend or an NSE holiday (Muhurat
    trading is not modelled)."""
    hol = load_holidays() if holidays is None else holidays
    meta = result.get("meta") or {}
    stamps = result.get("timestamp") or []
    q = ((result.get("indicators") or {}).get("quote") or [{}])[0] or {}
    cols = [q.get(k) or [] for k in ("open", "high", "low", "close", "volume")]
    as_of = now_utc
    rmt = meta.get("regularMarketTime")
    if isinstance(rmt, (int, float)) and utc_of(rmt) < as_of:
        as_of = utc_of(rmt)
    dropped = {"no_price": 0, "off_grid": 0, "not_complete": 0, "outside_hours": 0, "closed_day": 0}
    bars = {}
    for i, ts in enumerate(stamps):
        o, h, l, c, v = [col[i] if i < len(col) else None for col in cols]
        if ts is None or None in (o, h, l, c):
            dropped["no_price"] += 1
            continue
        ts = int(ts)
        if ts % INTERVAL:
            dropped["off_grid"] += 1
            continue
        start = utc_of(ts)
        local = to_ist(start)
        mins = local.hour * 60 + local.minute - OPEN_MIN
        if mins < 0 or mins >= SESSION_BARS * 5:
            dropped["outside_hours"] += 1
            continue
        if closed_reason(local.date().isoformat(), hol):
            dropped["closed_day"] += 1
            continue
        if start + datetime.timedelta(seconds=INTERVAL) > as_of:
            dropped["not_complete"] += 1
            continue
        bars[ts] = {"date": local.date().isoformat(), "slot": mins // 5, "start": local.strftime("%H:%M"),
                    "o": o, "h": h, "l": l, "c": c, "v": v or 0}
    return meta, [bars[t] for t in sorted(bars)], dropped


def split_sessions(bars):
    """{date: {slot: bar}}."""
    days = {}
    for b in bars:
        days.setdefault(b["date"], {})[b["slot"]] = b
    return days


def full_sessions(days, before, last=SESSION_BARS - 1):
    """Sessions strictly before the plan's date that hold every bar 0..last (the replay
    only needs the day up to --until), cut to those bars, oldest first, and the short
    ones that were left out (gaps before --until)."""
    full, short = [], []
    for d in sorted(days):
        if d >= before:
            continue
        s = days[d]
        if all(i in s for i in range(last + 1)):
            full.append((d, [s[i] for i in range(last + 1)]))
        else:
            short.append({"date": d, "bars": sum(1 for i in s if i <= last)})
    return full, short


# ---------------------------------------------------------------- the simulation

def build_pool(sessions):
    """pool[i] = every past session's bar i as (o, h, l, c) / bar i-1's close, in date order."""
    n = len(sessions[0][1]) if sessions else SESSION_BARS
    pool = [[] for _ in range(n)]
    for _, D in sessions:
        for i in range(1, n):
            pc = D[i - 1]["c"]
            pool[i].append((D[i]["o"] / pc, D[i]["h"] / pc, D[i]["l"] / pc, D[i]["c"] / pc))
    return pool


def pct(a, q):
    return a[min(len(a) - 1, int(q * len(a)))]


def simulate(pool, entry, stop, target, k, last, replays=10000, seed=19, window=2, side="buy", cost=None):
    """Monte Carlo replays of bars k+1..last. cost(exit_price) -> the round trip's charges in R
    (None = no charges); net R = gross R - cost."""
    long = side == "buy"
    risk = (entry - stop) if long else (stop - entry)
    r_target = round(((target - entry) if long else (entry - target)) / risk, 4)
    top = min(last, len(pool) - 1)

    def slot(rng, i):
        return i if window == 0 else min(top, max(1, i + rng.randint(-window, window)))

    rng = random.Random(seed)
    choice = rng.choice
    outcomes, closes, paths = [], [], []
    for n in range(replays):
        p = entry
        outcome = None
        keep = n < SAMPLE_PATHS
        path = [entry]
        for i in range(k + 1, last + 1):
            o, h, l, c = choice(pool[slot(rng, i)])
            hi, lo, cl = p * h, p * l, p * c
            if outcome is None:
                if long:
                    if lo <= stop:
                        outcome = "stop"        # both inside one bar: count the loss (conservative)
                    elif hi >= target:
                        outcome = "target"
                else:
                    if hi >= stop:
                        outcome = "stop"
                    elif lo <= target:
                        outcome = "target"
            p = cl
            if keep:
                path.append(round(p, 2))
        outcomes.append(outcome or "close")
        closes.append(p)
        if keep:
            paths.append(path)
    cnt = {x: outcomes.count(x) for x in ("target", "stop", "close")}
    green = sum(1 for o, p in zip(outcomes, closes)
                if o == "target" or (o == "close" and (p > entry if long else p < entry)))
    Rs = [r_target if o == "target" else -1.0 if o == "stop" else ((p - entry) if long else (entry - p)) / risk
          for o, p in zip(outcomes, closes)]
    if cost:
        costs = [cost(target if o == "target" else stop if o == "stop" else p) for o, p in zip(outcomes, closes)]
    else:
        costs = [0.0] * len(Rs)
    net = [r - c for r, c in zip(Rs, costs)]
    srt = sorted(closes)
    # percentile bands per bar for the fan chart: a second run of 2,000 paths, same seed
    rng = random.Random(seed)
    choice = rng.choice
    allp = []
    for n in range(BAND_PATHS):
        p = entry
        path = [entry]
        for i in range(k + 1, last + 1):
            o, h, l, c = choice(pool[slot(rng, i)])
            p = p * c
            path.append(p)
        allp.append(path)
    bands = []
    for j in range(last - k + 1):
        col = sorted(pp[j] for pp in allp)
        bands.append([round(pct(col, q), 2) for q in BAND_Q])
    lo_, hi_ = pct(srt, 0.01), pct(srt, 0.99)
    W = (hi_ - lo_) / HIST_BINS
    bins = [0] * HIST_BINS
    for c in srt:
        b = int((c - lo_) / W) if W > 0 else 0
        if 0 <= b < HIST_BINS:
            bins[b] += 1
    gross, net_avg = round(statistics.mean(Rs), 3), round(statistics.mean(net), 3)
    return {
        "replays": replays, "seed": seed, "window": window, "bars_left": last - k,
        "p_target": round(cnt["target"] / replays, 4), "p_stop": round(cnt["stop"] / replays, 4),
        "p_neither": round(cnt["close"] / replays, 4), "p_green": round(green / replays, 4),
        "median_close": round(pct(srt, 0.5), 2), "p5_close": round(pct(srt, 0.05), 2),
        "p95_close": round(pct(srt, 0.95), 2),
        "avg_R": net_avg, "avg_R_gross": gross, "avg_R_net": net_avg,
        "avg_charges_R": round(statistics.mean(costs), 4),
        "charges_R_target": round(cost(target), 4) if cost else 0.0,
        "charges_R_stop": round(cost(stop), 4) if cost else 0.0,
        "r_at_target": r_target, "band_paths": BAND_PATHS, "bands": bands, "sample_paths": paths,
        "hist": {"lo": round(lo_, 2), "hi": round(hi_, 2), "bins": bins},
    }


# ---------------------------------------------------------------- stop rules and the history replay

def opening_range(D, n):
    """(high, low) of the first n bars of a session given as {slot: bar} or a list."""
    rows = [D[i] for i in range(n)]
    # Yahoo sends float32 prices (1295.5999755859375); NSE prices are whole paise
    return round(max(b["h"] for b in rows), 2), round(min(b["l"] for b in rows), 2)


def rule_stop(rule, oh, ol, entry, long):
    r = rule["stop_rule"]
    if r == "or_mid":
        return tick_away((oh + ol) / 2, long)
    if r in ("or_low", "or_high"):
        return tick_away(ol if long else oh, long)
    frac = rule["stop_pct"] / 100.0
    return tick_away(entry * (1 - frac) if long else entry * (1 + frac), long)


def history_replay(sessions, rule, last, side="buy", sizing=None, ch=None):
    """The plan's rule on every past session: entry = the first bar after the
    opening range to close beyond it (up to the --until bar), then stop first, then
    target, bar by bar, else the close at --until. Each trade's R also comes net of
    the round trip's charges on the shares sizing gives it."""
    long = side == "buy"
    sizing = sizing or {"basis": "notional", "notional_inr": NOTIONAL_INR}
    n = rule["or_minutes"] // 5
    trades = []
    for d, D in sessions:
        oh, ol = opening_range(D, n)
        kk = next((i for i in range(n, last + 1) if (D[i]["c"] > oh if long else D[i]["c"] < ol)), None)
        if kk is None:
            continue
        e = round(D[kk]["c"], 2)
        s = rule_stop(rule, oh, ol, e, long)
        risk = (e - s) if long else (s - e)
        if risk <= 0:
            continue
        t = tick_away(e + rule["target_r"] * risk, True) if long else tick_away(e - rule["target_r"] * risk, False)
        oc = None
        for b in D[kk + 1:last + 1]:
            if (b["l"] <= s) if long else (b["h"] >= s):
                oc = "stop"
                break
            if (b["h"] >= t) if long else (b["l"] <= t):
                oc = "target"
                break
        cl = D[last]["c"]
        R = ((t - e) if long else (e - t)) / risk if oc == "target" else -1.0 if oc == "stop" else \
            ((cl - e) if long else (e - cl)) / risk
        x = t if oc == "target" else s if oc == "stop" else cl
        qty = trade_qty(sizing, e, risk)
        cR = charges_r(qty, e, x, risk, long, ch)
        trades.append({"date": d, "time": clock12(OPEN_MIN + 5 * (kk + 1)), "entry": e, "stop": s, "target": t,
                       "outcome": oc or "close", "R": round(R, 2), "qty": qty, "charges_R": round(cR, 3),
                       "R_net": round(R - cR, 2)})
    gross = round(statistics.mean(x["R"] for x in trades), 3) if trades else None
    net = round(statistics.mean(x["R"] - x["charges_R"] for x in trades), 3) if trades else None
    return {
        "sessions": len(sessions), "triggered": len(trades),
        "target": sum(1 for x in trades if x["outcome"] == "target"),
        "stop": sum(1 for x in trades if x["outcome"] == "stop"),
        "close": sum(1 for x in trades if x["outcome"] == "close"),
        "green": sum(1 for x in trades if x["R"] > 0),
        "green_net": sum(1 for x in trades if x["R_net"] > 0),
        "avg_R": net, "avg_R_gross": gross, "avg_R_net": net,
        "rule": rule, "trades": trades,
    }


# ---------------------------------------------------------------- the plan

def _num(v, what):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise TMError("the plan needs a number for %s, got %r" % (what, v))
    if not x > 0 or math.isinf(x):
        raise TMError("the plan's %s must be a positive price, got %r" % (what, v))
    return x


def load_plan(a, now_utc):
    """The plan from --plan (trader/plan.json) or from the flags, checked and normalized."""
    if a.symbol:
        missing = [f for f in ("entry", "stop", "target", "entry_time") if getattr(a, f) is None]
        if missing:
            raise TMError("with --symbol also give %s" % ", ".join("--" + m.replace("_", "-") for m in missing))
        raw = {"symbol": a.symbol, "side": a.side, "date": a.date or to_ist(now_utc).date().isoformat(),
               "entry": a.entry, "stop": a.stop, "target": a.target, "entry_time": a.entry_time,
               "setup": a.setup, "opening_range": {"minutes": a.or_minutes}, "stop_rule": a.stop_rule,
               "interval": "5m"}
        src = "flags"
    else:
        if not os.path.exists(a.plan):
            raise TMError("no plan at %s: run /trader-exit first, or pass --symbol --date --entry --stop "
                          "--target --entry-time" % a.plan)
        try:
            with open(a.plan, encoding="utf-8") as fh:
                raw = json.load(fh)
        except ValueError as e:
            raise TMError("%s is not valid JSON: %s" % (a.plan, e))
        if not isinstance(raw, dict):
            raise TMError("%s is not a plan" % a.plan)
        src = a.plan
    if getattr(a, "qty", None) is not None:
        raw = dict(raw, qty=a.qty)
    return normalize_plan(raw, src)


def normalize_plan(raw, src="flags"):
    """A plan dict (trader/plan.json shape) -> the checked plan the time machine runs."""
    sym = nse_symbol(raw.get("symbol") or "")
    if not SYMBOL_RE.match(sym):
        raise TMError("the plan needs an NSE symbol like RELIANCE.NS, got %r" % raw.get("symbol"))
    side = str(raw.get("side") or "buy").lower()
    if side not in ("buy", "sell"):
        raise TMError("side must be buy or sell, got %r" % raw.get("side"))
    interval = str(raw.get("interval") or "5m")
    if interval != "5m":
        raise TMError("5-minute bars only (the plan says %s): Yahoo keeps 60 days of 5-min bars but only about "
                      "7 days of 1-min bars, too few sessions for a fair replay pool" % interval)
    try:
        date = datetime.date.fromisoformat(str(raw.get("date") or "")).isoformat()
    except ValueError:
        raise TMError("the plan needs a date like 2026-11-06, got %r" % raw.get("date"))
    entry, stop, target = (_num(raw.get(k), k) for k in ("entry", "stop", "target"))
    long = side == "buy"
    if long and not stop < entry < target:
        raise TMError("a buy needs stop < entry < target (got stop %s, entry %s, target %s)" % (stop, entry, target))
    if not long and not target < entry < stop:
        raise TMError("a sell needs target < entry < stop (got target %s, entry %s, stop %s)" % (target, entry, stop))
    k = None
    if raw.get("entry_time"):
        close_min = parse_clock(raw["entry_time"], "entry_time")
        if (close_min - OPEN_MIN) % 5:
            raise TMError("entry_time %s is not on the 5-minute grid (use the bar's close, e.g. 9:50 AM)"
                          % raw["entry_time"])
        k = (close_min - OPEN_MIN) // 5 - 1
    if raw.get("entry_bar") is not None:
        try:
            kb = int(raw["entry_bar"])
        except (TypeError, ValueError):
            raise TMError("entry_bar must be a bar number, got %r" % raw["entry_bar"])
        if k is not None and kb != k:
            raise TMError("the plan disagrees with itself: entry_bar %d but entry_time %s is bar %d"
                          % (kb, raw["entry_time"], k))
        k = kb
    if k is None:
        raise TMError("the plan needs entry_time (the entry bar's close, e.g. 9:50 AM) or entry_bar")
    if not 0 <= k <= SESSION_BARS - 2:
        raise TMError("the entry must close between %s and %s IST to leave bars to replay (got %s)"
                      % (clock12(OPEN_MIN + 5), clock12(CLOSE_MIN - 5), clock12(OPEN_MIN + 5 * (k + 1))))
    orng = raw.get("opening_range") or {}
    or_min = orng.get("minutes", 15) if isinstance(orng, dict) else 15
    try:
        or_min = int(or_min)
    except (TypeError, ValueError):
        raise TMError("opening_range.minutes must be a number")
    if or_min < 5 or or_min % 5 or or_min >= SESSION_BARS * 5:
        raise TMError("opening_range.minutes must be a multiple of 5 (got %s)" % or_min)
    flat_by = raw.get("flat_by")
    if flat_by:
        fb = parse_clock(flat_by, "flat_by")
        flat_by = clock24(fb)
    qty = raw.get("qty")
    if qty is not None:
        try:
            qty = int(qty)
        except (TypeError, ValueError):
            raise TMError("qty must be a whole number of shares, got %r" % raw.get("qty"))
        if qty < 1:
            raise TMError("qty must be at least 1 share, got %r" % raw.get("qty"))
    risk = (entry - stop) if long else (stop - entry)
    return {
        "symbol": sym, "side": side, "date": date, "setup": str(raw.get("setup") or "orb"),
        "entry": entry, "entry_time": clock12(OPEN_MIN + 5 * (k + 1)), "entry_bar": k,
        "stop": stop, "target": target, "risk_per_share": round(risk, 2),
        "target_r": round(((target - entry) if long else (entry - target)) / risk, 2),
        "opening_range": dict(orng, minutes=or_min) if isinstance(orng, dict) else {"minutes": or_min},
        "stop_rule": str(raw.get("stop_rule") or "auto"), "interval": "5m",
        "flat_by": flat_by, "qty": qty, "from": src,
    }


def resolve_rule(plan, entry_day):
    """The rule the history replay uses: orb, the plan's opening range, its stop rule
    (or_mid, or_low / or_high, or 'pct' = the plan's stop distance as a percent of
    the entry), and the plan's target R. 'auto' works the stop rule out from the
    plan day's own opening range. None when the setup is not orb."""
    if plan["setup"].lower() != "orb":
        return None
    long = plan["side"] == "buy"
    n = plan["opening_range"]["minutes"] // 5
    rule = {"setup": "orb", "or_minutes": plan["opening_range"]["minutes"], "stop_rule": plan["stop_rule"],
            "target_r": plan["target_r"],
            "stop_pct": round(100.0 * plan["risk_per_share"] / plan["entry"], 4)}
    far = "or_low" if long else "or_high"
    half_tick = float(tick_size(plan["stop"])) / 2
    if rule["stop_rule"] == "auto":
        rule["stop_rule"] = "pct"
        if all(i in entry_day for i in range(n)):
            oh, ol = opening_range(entry_day, n)
            if abs(tick_away((oh + ol) / 2, long) - plan["stop"]) < half_tick:
                rule["stop_rule"] = "or_mid"
            elif abs(tick_away(ol if long else oh, long) - plan["stop"]) < half_tick:
                rule["stop_rule"] = far
    elif rule["stop_rule"] in ("or_low", "or_high") and rule["stop_rule"] != far:
        rule["stop_rule"] = "pct"
    elif rule["stop_rule"] not in ("or_mid", far):
        rule["stop_rule"] = "pct"
    if rule["stop_rule"] != "pct":
        del rule["stop_pct"]
    return rule


# ---------------------------------------------------------------- the run

def _until12(mc):
    t = mc.get("until") or DEFAULT_FLAT_BY
    return clock12(int(t[:2]) * 60 + int(t[3:]))


def lean(mc):
    """(gap, share, break_even): the target's share of the replays that hit one side,
    minus the share that breaks even at the target's R after charges. None when
    nothing hit a side."""
    pt, ps = mc["p_target"], mc["p_stop"]
    if pt + ps <= 0:
        return None
    r = mc.get("r_at_target") or 1.0
    ct, cs = mc.get("charges_R_target") or 0.0, mc.get("charges_R_stop") or 0.0
    den = 1.0 + r - ct + cs
    even = min(1.0, (1.0 + cs) / den) if den > 0 else 1.0
    share = pt / (pt + ps)
    return share - even, share, even


def verdict(mc):
    """One line from the numbers, see the docstring for the thresholds."""
    pt, ps = mc["p_target"], mc["p_stop"]
    lv = lean(mc)
    if lv is None:
        return "No replay reached the target or the stop by %s: the price at %s decides every one." % (
            _until12(mc), _until12(mc))
    d, share, even = lv
    r = mc.get("r_at_target") or 1.0
    one_r = abs(r - 1.0) < 0.005
    charged = bool(mc.get("charges_R_target") or mc.get("charges_R_stop"))
    if abs(d) < COIN_FLIP:
        head = "Close to a coin flip" if one_r else "Close to break-even"
    elif abs(d) < STRONG:
        head = "Leans toward the target" if d > 0 else "Leans toward the stop"
    else:
        head = "Strongly toward the target" if d > 0 else "Strongly toward the stop"
    odds = "target first %.1f %% vs stop first %.1f %%" % (100 * pt, 100 * ps)
    if one_r and not charged:
        return "%s: %s." % (head, odds)
    return ("%s: at %gR%s the target has to come first in %.0f %% of the replays that hit one side; "
            "it did in %.0f %% (%s)." % (head, round(r, 2), " after charges" if charged else "",
                                         100 * even, 100 * share, odds))


def verdict_notes(mc):
    """The lines printed right below the verdict, so nothing reads as one-sided."""
    notes = []
    pn = mc.get("p_neither", 0)
    if pn > MANY_NEITHER and lean(mc) is not None:
        notes.append("%.1f %% of the replays hit neither by %s; the price at %s decides those."
                     % (100 * pn, _until12(mc), _until12(mc)))
    lv = lean(mc)
    avg = mc.get("avg_R")
    if lv and abs(lv[0]) >= COIN_FLIP and avg is not None and avg != 0 and (avg > 0) != (lv[0] > 0):
        notes.append("Average R is %+.3f all the same: the replays that hit neither pull it the other way." % avg)
    return notes


def time_machine(text, plan, now_utc, replays=10000, seed=19, window=2, until=None, source="yahoo",
                 cfg=None, config_path=CONFIG_FILE, holidays=None):
    """Chart JSON text + a normalized plan -> the timemachine.json document.
    cfg is trader/config.json as a dict (flat_by, max_loss_per_trade_inr, charges)."""
    if not 1 <= replays <= 1000000:
        raise TMError("--replays must be between 1 and 1,000,000")
    if not 0 <= window <= 10:
        raise TMError("--window must be between 0 and 10 bars")
    cfg = cfg or {}
    hol = load_holidays() if holidays is None else holidays
    sym = plan["symbol"]
    shut = closed_reason(plan["date"], hol)
    if shut:
        raise TMError("NSE is closed on %s (%s): pick a trading day" % (plan["date"], shut))
    result = chart_result(text, sym)
    meta, bars, dropped = parse_bars(result, now_utc, hol)
    msym = nse_symbol(meta.get("symbol") or "") if meta.get("symbol") else ""
    if msym and msym != nse_symbol(sym):
        raise TMError("the data is for %s but the plan is for %s" % (msym, sym))
    gran = meta.get("dataGranularity")
    if gran and gran != "5m":
        raise TMError("the data has %s bars; the time machine needs 5-minute bars" % gran)
    if not bars:
        raise TMError("no complete 5-minute bars for %s in the data" % sym)
    k = plan["entry_bar"]
    if until is not None:
        until_text, until_from = until, "--until"
    elif plan.get("flat_by"):
        until_text, until_from = plan["flat_by"], "plan flat_by"
    elif cfg.get("flat_by"):
        until_text, until_from = cfg["flat_by"], "config flat_by"
    else:
        until_text, until_from = DEFAULT_FLAT_BY, "default flat_by"
    until_min = parse_clock(until_text, "--until" if until is not None else "flat_by")
    if (until_min - OPEN_MIN) % 5 or until_min > CLOSE_MIN:
        raise TMError("%s must be on the 5-minute grid and no later than %s (the NSE close)"
                      % ("--until" if until is not None else "flat_by", clock12(CLOSE_MIN)))
    last = (until_min - OPEN_MIN) // 5 - 1
    if last <= k:
        raise TMError("%s %s leaves nothing to replay after the %s entry"
                      % ("--until" if until is not None else "flat_by", clock12(until_min), plan["entry_time"]))
    days = split_sessions(bars)
    entry_day = days.get(plan["date"], {})
    if k not in entry_day:
        reason = ("no bars for that day" if not entry_day else
                  "the data stops at the %s bar" % entry_day[max(entry_day)]["start"])
        raise TMError("the entry is not in the data: no complete %s bar (closing %s) on %s for %s (%s)"
                      % (clock24(OPEN_MIN + 5 * k), plan["entry_time"], plan["date"], sym, reason))
    eb = entry_day[k]
    warn = []
    tol = float(tick_size(plan["entry"]))
    if not eb["l"] - tol <= plan["entry"] <= eb["h"] + tol:
        warn.append("the plan's entry %.2f is outside that bar's range %.2f to %.2f"
                    % (plan["entry"], eb["l"], eb["h"]))
    sessions, short = full_sessions(days, plan["date"], last)
    if len(sessions) < MIN_SESSIONS:
        hint = (" (%d more had gaps before %s: Yahoo often leaves the last bars before the close empty, "
                "an earlier --until may keep them)" % (len(short), clock12(until_min))) if short else ""
        raise TMError("only %d full session(s) of %s before %s in the data; the time machine needs at least %d%s"
                      % (len(sessions), sym, plan["date"], MIN_SESSIONS, hint))
    pool = build_pool(sessions)
    long = plan["side"] == "buy"
    ch = cfg.get("charges") if isinstance(cfg.get("charges"), dict) else None
    sizing = sizing_rule(plan, cfg)
    risk = abs(plan["entry"] - plan["stop"])
    qty = trade_qty(sizing, plan["entry"], risk)

    def cost(exit_price):
        return charges_r(qty, plan["entry"], exit_price, risk, long, ch)

    mc = simulate(pool, plan["entry"], plan["stop"], plan["target"], k, last, replays, seed, window, plan["side"],
                  cost)
    mc = dict(mc, until=clock24(until_min), until_from=until_from, pool_sessions=len(sessions),
              pool_from=sessions[0][0], pool_to=sessions[-1][0],
              band_times=[clock24(OPEN_MIN + 5 * (j + 1)) for j in range(k, last + 1)])
    at_target = intraday_charges(qty, plan["entry"], plan["target"], ch) if long else \
        intraday_charges(qty, plan["target"], plan["entry"], ch)
    at_stop = intraday_charges(qty, plan["entry"], plan["stop"], ch) if long else \
        intraday_charges(qty, plan["stop"], plan["entry"], ch)
    charges = dict(sizing, qty=qty, at_target_inr=at_target, at_stop_inr=at_stop,
                   rates=dict(DEFAULT_CHARGES, **(ch or {})), rates_from="config" if ch else "zerodha",
                   config=config_path)
    rule = resolve_rule(plan, entry_day)
    plan_out = {kk: v for kk, v in plan.items() if v is not None}
    if rule:
        plan_out["stop_rule"] = rule["stop_rule"]
        history = history_replay(sessions, rule, last, plan["side"], sizing, ch)
    else:
        history = {"sessions": len(sessions), "triggered": 0, "rule": None, "trades": [],
                   "note": "skipped: the history replay knows the opening-range breakout (orb) only, "
                           "this plan's setup is %r" % plan["setup"]}
    last_bar = bars[-1]
    return {
        "created_by": "trader-timemachine",
        "created_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "created_at_ist": ist_iso(now_utc),
        "source": source,
        "data": {"symbol": msym or sym, "interval": "5m", "range": "60d",
                 "last_bar": "%s %s IST" % (last_bar["date"], last_bar["start"]),
                 "dropped": dropped, "short_sessions_left_out": short},
        "plan": plan_out,
        "charges": charges,
        "mc": mc,
        "history": history,
        "verdict": verdict(mc),
        "verdict_notes": verdict_notes(mc),
        "warnings": warn,
        "note": ("A simulation built from %s's own last %d full sessions. It is not a prediction: results, "
                 "news and circuit limits can do things those sessions never did. Same seed, same numbers."
                 % (sym, len(sessions))),
    }


# ---------------------------------------------------------------- output

def pc(x):
    return "%.1f %%" % (100.0 * x)


def charges_line(doc):
    c = doc.get("charges") or {}
    if not c:
        return None
    cfg = c.get("config", CONFIG_FILE)
    if c["basis"] == "qty":
        how = " (the plan's qty)"
    elif c["basis"] == "max_loss_per_trade_inr":
        how = " (max_loss_per_trade_inr %s / the risk, from %s)" % (inr(c["max_loss_inr"]), cfg)
    else:
        how = ", a %s position (no qty in the plan and no max_loss_per_trade_inr in %s)" % (
            inr(c["notional_inr"]), cfg)
    return ("  Charges: %s at the target, %s at the stop, on %d shares%s; %s rates."
            % (inr(c["at_target_inr"]), inr(c["at_stop_inr"]), c["qty"], how,
               "your config's" if c.get("rates_from") == "config" else "Zerodha intraday"))


def report(doc):
    p, m, h = doc["plan"], doc["mc"], doc["history"]
    long = p["side"] == "buy"
    until = clock12(int(m["until"][:2]) * 60 + int(m["until"][3:]))
    wd = WEEKDAYS[datetime.date.fromisoformat(p["date"]).weekday()]
    r = p["target_r"]
    lines = [
        "%s %s, %s %s: entry ₹%.2f at %s IST (bar %d), stop ₹%.2f, target ₹%.2f (%sR, risk ₹%.2f a share)."
        % (p["symbol"], p["side"], wd, p["date"], p["entry"], p["entry_time"], p["entry_bar"], p["stop"],
           p["target"], ("%g" % r), p["risk_per_share"]),
        "%s replays of the rest of the session, %s to %s (%d five-minute bars, ending at your %s), seed %d."
        % ("{:,}".format(m["replays"]), p["entry_time"], until, m["bars_left"],
           "--until" if m.get("until_from") == "--until" else "flat-by time", m["seed"]),
        "Each bar is drawn whole from %s's own 5-min bars at the same time of day (+-%d bars) over its last "
        "%d full sessions (%s to %s)." % (p["symbol"], m["window"], m["pool_sessions"], m["pool_from"], m["pool_to"]),
        "",
        "  target first   %s" % pc(m["p_target"]),
        "  stop first     %s" % pc(m["p_stop"]),
        "  neither        %-8s still open at %s" % (pc(m["p_neither"]), until),
        "  green          %-8s target first, or %s the entry at %s" % (pc(m["p_green"]),
                                                                       "above" if long else "below", until),
        "",
        "  Price at %s: median ₹%.2f, 90 %% of replays between ₹%.2f and ₹%.2f."
        % (until, m["median_close"], m["p5_close"], m["p95_close"]),
        "  Average R: %+.3f gross, %+.3f net of charges (R = the ₹%.2f risk: %sR at the target, -1R at the stop)."
        % (m.get("avg_R_gross", m["avg_R"]), m.get("avg_R_net", m["avg_R"]), p["risk_per_share"],
           ("+%g" % m["r_at_target"])),
    ]
    cl = charges_line(doc)
    if cl:
        lines.append(cl)
    lines.append("")
    if h.get("rule"):
        ru = h["rule"]
        stop_words = {"or_mid": "opening-range midpoint", "or_low": "opening-range low",
                      "or_high": "opening-range high"}.get(ru["stop_rule"],
                                                           "%.2f %% from the entry" % ru.get("stop_pct", 0))
        if h["triggered"]:
            lines.append("History: the same rule (%d-min opening range, stop at the %s, %gR target) on each of "
                         "those %d real sessions fired %d times: %d target, %d stop, %d neither. "
                         "Average R %+.3f gross, %+.3f net of charges."
                         % (ru["or_minutes"], stop_words, ru["target_r"], h["sessions"], h["triggered"],
                            h["target"], h["stop"], h["close"], h.get("avg_R_gross", h["avg_R"]),
                            h.get("avg_R_net", h["avg_R"])))
        else:
            lines.append("History: the same rule never fired in those %d sessions." % h["sessions"])
    else:
        lines.append("History: " + h.get("note", "skipped."))
    lines += ["", doc["verdict"]] + list(doc.get("verdict_notes") or []) + [""]
    for w in doc.get("warnings") or []:
        lines.append("Note: " + w + ".")
    if doc["data"]["short_sessions_left_out"]:
        lines.append("Left out %d session(s) with gaps before %s: %s."
                     % (len(doc["data"]["short_sessions_left_out"]), until,
                        ", ".join(x["date"] for x in doc["data"]["short_sessions_left_out"])))
    lines.append(doc["note"] + " Repeat this run with --seed %d." % m["seed"])
    return lines


def render_chart(doc, height=17):
    """ASCII fan chart of the bands: one column per 5-min bar."""
    p, m = doc["plan"], doc["mc"]
    bands, times = m["bands"], m["band_times"]
    lo = min([b[0] for b in bands] + [p["stop"], p["target"]])
    hi = max([b[4] for b in bands] + [p["stop"], p["target"]])
    pad = (hi - lo) * 0.04 or 0.01
    lo, hi = lo - pad, hi + pad
    step = (hi - lo) / (height - 1)

    def row(price):
        return max(0, min(height - 1, int(round((hi - price) / step))))
    grid = [[" "] * len(bands) for _ in range(height)]
    for j, b in enumerate(bands):
        r5, r25, r50, r75, r95 = [row(x) for x in b]
        for r in range(r95, r5 + 1):
            grid[r][j] = "."
        for r in range(r75, r25 + 1):
            grid[r][j] = ":"
        grid[r50][j] = "*"
    marks = {row(p["target"]): "target", row(p["stop"]): "stop"}
    if row(p["entry"]) not in marks:
        marks[row(p["entry"])] = "entry"
    for r in (row(p["target"]), row(p["stop"])):
        grid[r] = ["-" if ch == " " else ch for ch in grid[r]]
    out = ["%s %s, %s replay paths from the %s IST entry (seed %d)" % (
        p["symbol"], p["date"], "{:,}".format(m.get("band_paths", BAND_PATHS)), p["entry_time"], m["seed"])]
    for r in range(height):
        label = marks.get(r)
        price = {"target": p["target"], "stop": p["stop"], "entry": p["entry"]}.get(label, hi - r * step)
        left = ("%9.2f |" % price) if (label or r in (0, height - 1)) else "          |"
        out.append(left + "".join(grid[r]) + ("  " + label if label else ""))
    axis = [" "] * len(bands)
    for j in sorted({0, len(bands) // 2, len(bands) - 1}):
        t = times[j]
        hh, mm = int(t[:2]), t[3:]
        lab = "%d:%s" % (hh % 12 or 12, mm)
        s = max(0, min(j, len(bands) - len(lab)))
        axis[s:s + len(lab)] = list(lab)
    out.append("          +" + "-" * len(bands))
    out.append("           " + "".join(axis))
    out.append("* median   : middle half of the paths   . 90 % of the paths   - stop and target")
    return out


# ---------------------------------------------------------------- commands

def _write(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False)
        fh.write("\n")


def compute(a):
    now = parse_now(a.now)
    plan = load_plan(a, now)
    cfg = load_config(a.config)
    if a.offline:
        text, source = read_offline(a.offline, plan["symbol"]), "offline"
    else:
        text, source = fetch_chart(plan["symbol"]), "yahoo"
    return time_machine(text, plan, now, a.replays, a.seed, a.window, a.until, source, cfg, a.config)


def cmd_run(a):
    doc = compute(a)
    if a.write:
        _write(a.write, doc)
    if a.json:
        slim = dict(doc, mc={k: v for k, v in doc["mc"].items() if k != "sample_paths"})
        print(json.dumps(slim, indent=2, ensure_ascii=False))
    else:
        print("\n".join(report(doc)))
        if a.write:
            print("Wrote %s." % a.write)
        print("Next: /trader-budget sizes it to your budget and your max loss per trade.")
    return 0


def cmd_chart(a):
    if a.from_file:
        if not os.path.exists(a.from_file):
            raise TMError("no %s: run timemachine.py run --write first" % a.from_file)
        try:
            with open(a.from_file, encoding="utf-8") as fh:
                doc = json.load(fh)
            doc["mc"]["bands"]
        except (ValueError, KeyError, TypeError):
            raise TMError("%s is not a time machine result" % a.from_file)
    else:
        doc = compute(a)
    print("\n".join(render_chart(doc)))
    print("\n".join([doc.get("verdict", "")] + list(doc.get("verdict_notes") or [])))
    return 0


def main(argv=None):
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Time Machine: replay a planned day trade on NSE (trader-timemachine).")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--plan", default="trader/plan.json", help="the plan from trader-exit")
        p.add_argument("--config", default=CONFIG_FILE,
                       help="flat_by, max_loss_per_trade_inr and charges (default trader/config.json)")
        p.add_argument("--symbol", help="or give the plan as flags: NSE symbol (RELIANCE or RELIANCE.NS)")
        p.add_argument("--side", default="buy", choices=["buy", "sell"])
        p.add_argument("--date", help="the plan's day, YYYY-MM-DD (default today, IST)")
        p.add_argument("--entry", type=float)
        p.add_argument("--stop", type=float)
        p.add_argument("--target", type=float)
        p.add_argument("--entry-time", help='the entry bar\'s close, e.g. "9:50 AM"')
        p.add_argument("--setup", default="orb")
        p.add_argument("--or-minutes", type=int, default=15)
        p.add_argument("--stop-rule", default="auto", choices=["auto", "or_mid", "or_low", "or_high", "pct"])
        p.add_argument("--qty", type=int, help="shares, for the charges (default: from max_loss_per_trade_inr)")
        p.add_argument("--replays", type=int, default=10000)
        p.add_argument("--seed", type=int, default=19)
        p.add_argument("--window", type=int, default=2, help="draw from the same time of day +- this many bars")
        p.add_argument("--until", help="end each replay at this IST time (default the plan's flat_by, 15:10)")
        p.add_argument("--offline", metavar="FILE", help="a saved Yahoo chart JSON (.json or .json.gz), no network")
        p.add_argument("--now", help='pretend it is this time ("2026-11-06 11:07" IST, or ISO with Z)')

    p = sub.add_parser("run", help="replay the plan: 10,000 simulated sessions plus the history")
    common(p)
    p.add_argument("--json", action="store_true")
    p.add_argument("--write", nargs="?", const="trader/timemachine.json", metavar="PATH",
                   help="save the result (default trader/timemachine.json)")
    p = sub.add_parser("chart", help="ASCII fan chart of the replays")
    common(p)
    p.add_argument("--from", dest="from_file", nargs="?", const="trader/timemachine.json", metavar="PATH",
                   help="draw a saved result instead of running again")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"run": cmd_run, "chart": cmd_chart}[a.cmd](a)
    except TMError as e:
        print("timemachine.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

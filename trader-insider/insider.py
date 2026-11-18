#!/usr/bin/env python3
"""Insider and big-investor tracker for trader-insider (NSE, India). Standard library only.

Two public sources of "who is buying and selling":
  1. NSE bulk and block deals, published every trading day as plain CSVs:
       https://nsearchives.nseindia.com/content/equities/bulk.csv
       https://nsearchives.nseindia.com/content/equities/block.csv
     A bulk deal is one client trading at least 0.5% of a company's listed
     shares in a day; a block deal is a single large trade in the separate
     block-deal window. Each row names the client (a fund, an FII, a person).
     The files hold the latest trading day only.
  2. SEBI insider trading (PIT Regulations 2015) disclosures: promoters,
     directors, key managerial personnel (KMP) and designated persons report
     trades to the company within 2 trading days, and the company tells the
     exchange within 2 trading days. NSE lists them at
       https://www.nseindia.com/companies-listing/corporate-filings-insider-trading
     That page only works in a browser, so download its CSV (or save the JSON)
     there and pass it with --pit-file. This tool never fakes a browser session.

    python insider.py deals                              # today's bulk and block deals
    python insider.py deals --who "kacholia"             # one investor
    python insider.py deals --symbol RELIANCE --mine     # filters; --mine = stocks you hold/watch
    python insider.py pit --pit-file CF-Insider-Trading.csv --category promoter --side buy
    python insider.py new --pit-file CF-Insider-Trading.csv   # only what is new since last check

State: `new` remembers which items it has shown in trader/state/insider.json,
so each deal or disclosure alerts once. Items in stocks you hold or watch
(trader/portfolio.json, trader/plan.json, trader/watchlist.json, --symbols)
are flagged. Offline: --bulk-file / --block-file read saved CSVs (the same
format, or the history CSV from nseindia.com/report-detail/display-bulk-and-block-deals).

Public disclosures only: reading them quickly is not inside information.
"""

import argparse
import csv
import datetime
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request

BULK_URL = "https://nsearchives.nseindia.com/content/equities/bulk.csv"
BLOCK_URL = "https://nsearchives.nseindia.com/content/equities/block.csv"
PIT_PAGE = "https://www.nseindia.com/companies-listing/corporate-filings-insider-trading"
UA = "Mozilla/5.0 (compatible; day-trader-skill/3.0)"   # NSE drops requests whose User-Agent holds a URL
PIT_DAYS = 2            # SEBI PIT Reg. 7(2): 2 trading days to the company, 2 more to the exchange
FIRST_RUN_DAYS = 7      # the first `new` shows only the last week of PIT disclosures
STATE_KEEP = 20000

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


DEFAULT_SUFFIX = ".NS"


def nse_symbol(sym):
    """RELIANCE -> RELIANCE.NS; RELIANCE.NS, TCS.BO and ^NSEI unchanged. Stored form."""
    s = str(sym).strip().upper()
    if s and not s.startswith("^") and not s.endswith((".NS", ".BO")):
        s += DEFAULT_SUFFIX
    return s


def inr(v):
    """1234567 -> ₹12.35 lakh, 52300000000 -> ₹5,230.00 crore."""
    a = abs(v)
    if a >= 1e7:
        return "₹{:,.2f} crore".format(v / 1e7)
    if a >= 1e5:
        return "₹{:,.2f} lakh".format(v / 1e5)
    return "₹{:,.2f}".format(v)


class InsiderError(Exception):
    pass


# ---------------------------------------------------------------- reading

def download(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/csv,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise InsiderError("HTTP %s for %s" % (exc.code, url))
    except urllib.error.URLError as exc:
        raise InsiderError("could not reach %s: %s" % (url, exc.reason))


def _key(h):
    """'NAME OF THE ACQUIRER/DISPOSER \\n' -> 'nameoftheacquirerdisposer'. NSE's CSV headers carry a
    literal backslash-n, so drop that before keeping letters and digits."""
    return re.sub(r"[^a-z0-9]", "", str(h or "").replace("\\n", " ").lower())


def _text(data):
    if isinstance(data, bytes):
        data = data.decode("utf-8-sig", "replace")
    return "\n".join(l for l in data.splitlines() if not l.lstrip().startswith("#"))


def _rows(text):
    """CSV text -> list of {normalised header: value}."""
    reader = csv.reader(io.StringIO(text))
    head = None
    out = []
    for rec in reader:
        if not rec or not any(c.strip() for c in rec):
            continue
        if head is None:
            head = [_key(h) for h in rec]
            continue
        out.append(dict((head[i], rec[i].strip()) for i in range(min(len(head), len(rec)))))
    return out


def iso_date(text):
    """'05-NOV-2026', '05-Nov-2026 18:30', '05/11/2026', '2026-11-05' -> '2026-11-05'."""
    s = str(text or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return "%s-%s-%s" % m.groups()
    m = re.match(r"^(\d{1,2})[-/ ]([A-Za-z]{3,9}|\d{1,2})[-/ ](\d{4})", s)
    if not m:
        return None
    day, mon, year = m.groups()
    if mon.isalpha():
        try:
            mon = datetime.datetime.strptime(mon[:3].title(), "%b").month
        except ValueError:
            return None
    try:
        return datetime.date(int(year), int(mon), int(day)).isoformat()
    except ValueError:
        return None


def _num(text):
    s = str(text or "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _side(text):
    t = str(text or "").lower()
    if re.search(r"\b(buy|purchase|acquisition|acquire)", t):
        return "buy"
    if re.search(r"\b(sell|sale|disposal|dispose)", t):
        return "sell"
    if "pledge" in t or "invoc" in t or "revoc" in t:
        return "pledge"
    return t or None


def parse_deals(data, kind):
    """bulk.csv / block.csv (or NSE's history download) -> deal rows."""
    out = []
    for r in _rows(_text(data)):
        date = iso_date(r.get("date"))
        sym = r.get("symbol", "")
        if not date or not sym:
            continue  # "NO RECORDS"
        qty = _num(r.get("quantitytraded"))
        price = _num(r.get("tradepricewghtavgprice") or r.get("tradeprice") or r.get("price"))
        side = _side(r.get("buysell"))
        row = {"kind": kind, "symbol": nse_symbol(sym), "company": r.get("securityname") or None,
               "who": " ".join((r.get("clientname") or "").split()), "category": None,
               "side": side, "qty": int(qty) if qty is not None else None, "price": price,
               "value": round(qty * price, 2) if qty is not None and price is not None else None,
               "trade_date": date, "disclosed": date,
               "remarks": None if r.get("remarks") in (None, "", "-") else r.get("remarks")}
        row["id"] = "|".join(str(x) for x in (kind, date, row["symbol"], row["who"].upper(), side,
                                                row["qty"], price))
        out.append(row)
    return out


PIT_FIELDS = {
    "symbol": ["symbol"],
    "company": ["company", "companyname"],
    "who": ["nameoftheacquirerdisposer", "nameofacquirerdisposer", "acqname", "name"],
    "category": ["categoryofperson", "personcategory", "category"],
    "security": ["typeofsecurityacquireddisplosed", "typeofsecurityacquireddisposed", "sectype"],
    "qty": ["noofsecuritiesacquireddisplosed", "noofsecuritiesacquireddisposed", "secacq"],
    "value": ["valueofsecurityacquireddisplosed", "valueofsecurityacquireddisposed", "secval"],
    "txn": ["acquisitiondisposaltransactiontype", "tdptransactiontype", "transactiontype"],
    "before_pct": ["shareholdingprior", "befacqsharesper"],
    "after_pct": ["post", "shareholdingpost", "afteracqsharesper"],
    "from": ["dateofallotmentacquisitionfrom", "acqfromdt"],
    "to": ["dateofallotmentacquisitionto", "acqtodt"],
    "intimated": ["dateofinitmationtocompany", "dateofintimationtocompany", "intimdt"],
    "mode": ["modeofacquisition", "acqmode"],
    "exchange": ["exchange"],
    "remarks": ["remark", "remarks"],
    "disclosed": ["broadcastedateandtime", "broadcastdateandtime", "date"],
}
CATEGORIES = [("promoter group", "promoter group"), ("promoter", "promoter"),
              ("director", "director"), ("key managerial", "KMP"), ("kmp", "KMP"),
              ("designated", "designated person"), ("employee", "designated person"),
              ("immediate relative", "relative"), ("relative", "relative")]


def category(text):
    t = str(text or "").lower()
    for needle, name in CATEGORIES:
        if needle in t:
            return name
    return t or None


def trading_days_between(start, end, holidays=None):
    """NSE trading days after `start` up to and including `end` (ISO dates)."""
    if not start or not end:
        return None
    hol = holidays if holidays is not None else load_holidays()
    a, b = datetime.date.fromisoformat(start), datetime.date.fromisoformat(end)
    if b <= a:
        return 0
    n, d = 0, a
    while d < b:
        d += datetime.timedelta(days=1)
        if d.weekday() < 5 and d.isoformat() not in hol:
            n += 1
    return n


def _pit_records(data):
    text = data.decode("utf-8-sig", "replace") if isinstance(data, bytes) else data
    body = _text(text).lstrip()
    if body.startswith("{") or body.startswith("["):
        try:
            doc = json.loads(body)
        except ValueError:
            raise InsiderError("the PIT file is not valid JSON")
        rows = doc.get("data") if isinstance(doc, dict) else doc
        if not isinstance(rows, list):
            raise InsiderError("the PIT JSON has no list of rows (expected {\"data\": [...]})")
        return [dict((_key(k), "" if v is None else str(v)) for k, v in r.items())
                for r in rows if isinstance(r, dict)]
    return _rows(body)


def parse_pit(data, holidays=None):
    """NSE insider-trading (PIT) CSV or JSON saved from the browser -> disclosure rows."""
    hol = holidays if holidays is not None else load_holidays()
    recs = _pit_records(data)
    if recs and not any(k in recs[0] for k in PIT_FIELDS["who"]):
        raise InsiderError("this does not look like NSE's insider-trading download "
                           "(no acquirer/disposer name column)")
    out = []
    for r in recs:
        f = {}
        for name, keys in PIT_FIELDS.items():
            f[name] = next((r[k] for k in keys if r.get(k) not in (None, "", "-")), None)
        if not f["symbol"] or not f["who"]:
            continue
        qty, value = _num(f["qty"]), _num(f["value"])
        frm, to = iso_date(f["from"]), iso_date(f["to"])
        trade = to or frm
        intimated, disclosed = iso_date(f["intimated"]), iso_date(f["disclosed"])
        row = {"kind": "pit", "symbol": nse_symbol(f["symbol"]), "company": f["company"],
               "who": " ".join(f["who"].split()), "category": category(f["category"]),
               "category_raw": f["category"], "side": _side(f["txn"]),
               "qty": int(qty) if qty is not None else None, "value": value,
               "price": round(value / qty, 2) if value and qty else None,
               "security": f["security"], "mode": f["mode"], "trade_date": trade,
               "trade_from": frm, "intimated": intimated, "disclosed": disclosed,
               "before_pct": _num(f["before_pct"]), "after_pct": _num(f["after_pct"]),
               "exchange": f["exchange"], "remarks": f["remarks"]}
        row["days_to_disclose"] = ((datetime.date.fromisoformat(disclosed)
                                    - datetime.date.fromisoformat(trade)).days
                                   if trade and disclosed else None)
        to_company = trading_days_between(trade, intimated, hol)
        to_exchange = trading_days_between(intimated, disclosed, hol)
        row["trading_days_to_company"] = to_company
        row["trading_days_to_exchange"] = to_exchange
        row["late"] = bool((to_company or 0) > PIT_DAYS or (to_exchange or 0) > PIT_DAYS)
        row["market"] = bool(f["mode"] and "market" in f["mode"].lower()
                             and "off" not in f["mode"].lower())
        row["id"] = "|".join(str(x) for x in ("pit", row["symbol"], row["who"].upper(), frm, to,
                                                row["qty"], row["side"], disclosed))
        out.append(row)
    return out


# ---------------------------------------------------------------- filters

def _words(s):
    return re.findall(r"[a-z0-9]+", str(s or "").lower())


def who_match(row, names):
    """Every word of a name appears in the row's name: 'kacholia', 'Ashish Kacholia'."""
    if not names:
        return True
    have = set(_words(row.get("who")))
    return any(all(w in have for w in _words(n)) for n in names if _words(n))


def watched_symbols(args):
    """{symbol: why} from --symbols, the portfolio, the plan and the watchlist."""
    out = {}
    for s in getattr(args, "symbols", None) or []:
        out.setdefault(nse_symbol(s), "your list")

    def load(path):
        if path and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    return json.load(fh)
            except (OSError, ValueError):
                return None
        return None

    pf = load(getattr(args, "portfolio", None)) or {}
    for p in pf.get("positions", []) if isinstance(pf, dict) else []:
        s = p.get("symbol") or p.get("ticker")
        if s:
            out.setdefault(nse_symbol(s), "you hold it")
    plan = load(getattr(args, "plan", None)) or {}
    if isinstance(plan, dict) and plan.get("symbol"):
        out.setdefault(nse_symbol(plan["symbol"]), "your trade plan")
    wl = load(getattr(args, "watchlist", None)) or {}
    for h in wl.get("hits", []) if isinstance(wl, dict) else []:
        if isinstance(h, dict) and h.get("symbol"):
            out.setdefault(nse_symbol(h["symbol"]), "your watchlist")
    return out


def select(rows, who=None, symbols=None, side=None, categories=None, min_value=None,
           market_only=False, watch=None, mine=False):
    syms = set(nse_symbol(s) for s in symbols or [])
    cats = set(c.lower() for c in categories or [])
    out = []
    for r in rows:
        if not who_match(r, who):
            continue
        if syms and r["symbol"] not in syms:
            continue
        if side and r.get("side") != side:
            continue
        if cats and not any(c in (r.get("category") or "").lower() for c in cats):
            continue
        if min_value and (r.get("value") or 0) < min_value:
            continue
        if market_only and r["kind"] == "pit" and not r.get("market"):
            continue
        if mine and r["symbol"] not in (watch or {}):
            continue
        out.append(r)
    return out


# ---------------------------------------------------------------- state

def load_state(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return {"seen": [], "last_check": None}


def save_state(path, state):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def new_since(rows, state, today, first_days=FIRST_RUN_DAYS):
    """Rows not seen before. On the first run: only those disclosed in the last first_days days."""
    seen = set(state.get("seen", []))
    if state.get("last_check") is None:
        cutoff = (today - datetime.timedelta(days=first_days)).isoformat()
        return [r for r in rows if (r.get("disclosed") or r.get("trade_date") or "") >= cutoff]
    return [r for r in rows if r["id"] not in seen]


# ---------------------------------------------------------------- output

def describe(row, watch=None):
    sym = row["symbol"]
    tag = {"bulk": "BULK", "block": "BLOCK", "pit": "PIT"}[row["kind"]]
    who = row["who"]
    if row.get("category"):
        who += " (%s)" % row["category"]
    side = (row.get("side") or "?").upper()
    bits = ["%-5s %s  %s" % (tag, row.get("disclosed") or row.get("trade_date") or "?", sym),
            "%s %s" % (who, side)]
    qty = "{:,}".format(row["qty"]) + " sh" if row.get("qty") is not None else "? sh"
    if row["kind"] == "pit":
        if row.get("value"):
            qty += ", %s" % inr(row["value"])
            if row.get("price"):
                qty += " (about ₹{:,.2f}/sh)".format(row["price"])
    else:
        if row.get("price") is not None:
            qty += " @ ₹{:,.2f}".format(row["price"])
        if row.get("value"):
            qty += " = %s" % inr(row["value"])
    line = "  ".join(bits) + " " + qty
    extra = []
    if row["kind"] == "pit":
        if row.get("mode"):
            extra.append(row["mode"].lower())
        if row.get("before_pct") is not None and row.get("after_pct") is not None:
            extra.append("holding %.2f%% -> %.2f%%" % (row["before_pct"], row["after_pct"]))
        if row.get("trade_date"):
            when = "traded %s" % row["trade_date"]
            if row.get("days_to_disclose") is not None:
                when += ", on NSE %d days later" % row["days_to_disclose"]
            if row.get("late"):
                when += " (LATE: over %d trading days at a step)" % PIT_DAYS
            extra.append(when)
    elif row.get("remarks"):
        extra.append(row["remarks"])
    if extra:
        line += ", " + ", ".join(extra)
    if watch and sym in watch:
        line += "  <- %s (%s)" % (sym, watch[sym])
    return line


FOOTER = {
    "deals": ("Bulk deal: one client traded at least 0.5% of the company's shares that day. Block "
              "deal: one large trade in the block window. A deal names the client, not the reason, "
              "and does not say whether they still hold. Public data, not inside information."),
    "pit": ("SEBI PIT disclosure: filed by the insider within 2 trading days and passed on by the "
            "company within 2 more, so it arrives days after the trade. ESOP allotments, gifts, "
            "pledges and inter-se transfers are not market bets. Public data, not inside information."),
}


def render(rows, watch=None, kinds=("deals",)):
    out = [describe(r, watch) for r in rows]
    if out:
        out.append("")
        for k in kinds:
            out.append(FOOTER[k])
    return "\n".join(out) + ("\n" if out else "")


# ---------------------------------------------------------------- CLI

def _parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd")

    def common(p, deals=True, pit=False):
        if deals:
            p.add_argument("--kind", choices=("bulk", "block", "both"), default="both")
            p.add_argument("--bulk-file", metavar="CSV", help="a saved bulk-deals CSV instead of NSE")
            p.add_argument("--block-file", metavar="CSV", help="a saved block-deals CSV instead of NSE")
        p.add_argument("--pit-file", metavar="PATH", required=pit,
                       help="insider-trading CSV/JSON saved from " + PIT_PAGE)
        p.add_argument("--who", action="append", help="a name to track (all its words must match); "
                                                      "repeat for more")
        p.add_argument("--symbol", action="append", help="only these symbols; repeat")
        p.add_argument("--side", choices=("buy", "sell"))
        p.add_argument("--category", action="append",
                       help="PIT only: promoter, director, KMP, designated, relative")
        p.add_argument("--min-value", type=float, metavar="INR", help="only deals worth at least this")
        p.add_argument("--market-only", action="store_true",
                       help="PIT only: market purchases and sales (no ESOP, gift, pledge, off-market)")
        p.add_argument("--mine", action="store_true", help="only stocks you hold or watch")
        p.add_argument("--symbols", nargs="*", help="flag items in these symbols too")
        p.add_argument("--portfolio", default="trader/portfolio.json")
        p.add_argument("--plan", default="trader/plan.json")
        p.add_argument("--watchlist", default="trader/watchlist.json")
        p.add_argument("--market-file", default=MARKET_FILE)
        p.add_argument("--json", action="store_true")

    common(sub.add_parser("deals", help="NSE bulk and block deals (latest trading day)"))
    common(sub.add_parser("pit", help="SEBI insider/promoter trades from a saved NSE file"),
           deals=False, pit=True)
    p = sub.add_parser("new", help="deals (and PIT, with --pit-file) not seen at the last check")
    common(p)
    p.add_argument("--no-deals", action="store_true", help="PIT file only")
    p.add_argument("--state", default="trader/state/insider.json")
    p.add_argument("--today", help="YYYY-MM-DD (IST), for reproducible runs")
    p.add_argument("--dry-run", action="store_true", help="do not update the state file")
    return ap


def load_deals(args):
    rows = []
    kinds = ("bulk", "block") if args.kind == "both" else (args.kind,)
    for k in kinds:
        path = getattr(args, "%s_file" % k)
        if path:
            with open(path, "rb") as fh:
                data = fh.read()
        else:
            data = download(BULK_URL if k == "bulk" else BLOCK_URL)
        rows.extend(parse_deals(data, k))
    return rows


def load_pit(path, market_file=MARKET_FILE):
    with open(path, "rb") as fh:
        return parse_pit(fh.read(), load_holidays(market_file))


def main(argv=None):
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = _parser()
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 2
    try:
        return _run(args)
    except (InsiderError, OSError) as exc:
        print("insider: %s" % exc, file=sys.stderr)
        return 2


def _run(args):
    watch = watched_symbols(args)
    rows, kinds = [], []
    if args.cmd in ("deals", "new") and not getattr(args, "no_deals", False):
        rows.extend(load_deals(args))
        kinds.append("deals")
    if args.pit_file:
        rows.extend(load_pit(args.pit_file, args.market_file))
        kinds.append("pit")
    picked = select(rows, args.who, args.symbol, args.side, args.category, args.min_value,
                    args.market_only, watch, args.mine)
    picked.sort(key=lambda r: (r.get("disclosed") or "", r.get("value") or 0), reverse=True)
    for r in picked:
        r["watched"] = watch.get(r["symbol"])

    if args.cmd in ("deals", "pit"):
        if args.json:
            print(json.dumps(picked, indent=2, ensure_ascii=False))
        else:
            sys.stdout.write(render(picked, watch, kinds))
            if not picked:
                print("No matching %s." % ("deals" if args.cmd == "deals" else "disclosures"))
        return 0 if picked else 1

    # new
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    today = (datetime.date.fromisoformat(args.today) if args.today else to_ist(now).date())
    state = load_state(args.state)
    fresh = new_since(picked, state, today)
    if args.json:
        print(json.dumps({"new": fresh, "checked_ist": ist_iso(now)}, indent=2, ensure_ascii=False))
    else:
        if not fresh:
            print("Nothing new since %s." % (state.get("last_check") or "the last check"))
        else:
            print("NEW since %s:" % (state.get("last_check") or "the last week"))
            sys.stdout.write(render(fresh, watch, kinds))
    if not args.dry_run:
        seen = list(state.get("seen", []))
        have = set(seen)
        for r in picked:
            if r["id"] not in have:
                seen.append(r["id"])
                have.add(r["id"])
        state["seen"] = seen[-STATE_KEEP:]
        state["last_check"] = ist_iso(now)
        save_state(args.state, state)
    return 0 if fresh else 1


if __name__ == "__main__":
    sys.exit(main())

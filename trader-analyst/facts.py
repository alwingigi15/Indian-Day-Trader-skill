#!/usr/bin/env python3
"""Earnings reader for trader-analyst (NSE, India). Standard library only.

Lays out what changed in a company's results, quarter by quarter: revenue,
gross, operating and net margins, net profit, diluted EPS, and growth against
the same quarter a year earlier, with the trailing twelve months. Quarters
are labelled on the Indian fiscal year (April to March: Q1 Apr-Jun, Q2
Jul-Sep, Q3 Oct-Dec, Q4 Jan-Mar; "FY27" = April 2026 to March 2027).

    python facts.py RELIANCE.NS
    python facts.py RELIANCE TCS --quarters 8
    python facts.py RELIANCE.NS --json
    python facts.py RELIANCE.NS TCS.NS INFY.NS --metrics trader/metrics.json
    python facts.py RELIANCE.NS --file results.html --file results-last-year.xml

Sources:
  1. Yahoo Finance fundamentals timeseries (a plain GET, no key, no cookie):
     https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/<SYM>
     Quarterly and annual revenue, gross profit, operating income, net income
     (attributable to the parent's owners) and diluted EPS, in the currency
     Yahoo states (INR for NSE stocks). Yahoo sometimes skips a quarter: when
     the year's total and the other three quarters are there, the missing one
     is the year minus those three and is marked "derived".
  2. --file PATH (repeatable): results the user saved from NSE or BSE in
     their browser. Any of:
       - the NSE "XBRL" page of a results filing (Integrated Filing - Financials,
         .html, served from nsearchives.nseindia.com/corporate/ixbrl/...)
       - an Ind AS XBRL instance (.xml) of the financial results
       - the NSE financial-results list as JSON (the XBRL links in it on
         nsearchives.nseindia.com are fetched with a plain GET)
     Ind AS concepts read: RevenueFromOperations, OtherIncome, FinanceCosts,
     ProfitBeforeExceptionalItemsAndTax, ProfitOrLossAttributableToOwnersOfParent
     (else ProfitLossForPeriod / ProfitLoss) and DilutedEarningsLossPerShare...
     Units come from the file (XBRL unitRef and decimals; the page's "Level of
     rounding used in financial results"); a file that does not state them is
     refused. Consolidated is preferred over standalone and the report says
     which one it used. With --file the report is built from the files only.
  Operating profit for Ind AS results = profit before exceptional items and
  tax + finance costs - other income (Yahoo's operating income matches it).
  NSE's own API (nseindia.com/api) is not called: it needs a browser session.

Results deadline (SEBI LODR Reg. 33): within 45 days of the quarter end, 60
days for Q4 (the audited annual results). The report says when the next
results are due, and warns when a due quarter is missing from the data.

--metrics FILE merges one row per symbol into a JSON file (for notes or a
sheet). Offline: --offline DIR reads timeseries-<SYM>.json (and the files an
NSE results JSON links to, by file name) from DIR.

Numbers only: this tool never says whether a stock is a buy.
"""

import argparse
import datetime as _dt
import gzip
import html as _html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

UA = "Mozilla/5.0 (compatible; day-trader-skill/3.0)"   # NSE drops requests whose User-Agent holds a URL
YAHOO_TS_URL = ("https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/"
                "timeseries/%s?type=%s&period1=%d&period2=%d")
YAHOO_PERIOD1 = 1483228800  # 2017-01-01: Yahoo returns what it has (about five quarters, four years)
ALLOWED_HOSTS = ("nsearchives.nseindia.com", "archives.nseindia.com")

# metric -> Yahoo field stem (quarterly<stem>, annual<stem>)
YAHOO_FIELDS = {
    "revenue": "TotalRevenue",
    "cost_of_revenue": "CostOfRevenue",
    "gross_profit": "GrossProfit",
    "operating_income": "OperatingIncome",
    "net_income": "NetIncome",
    "eps_diluted": "DilutedEPS",
}
# Yahoo's annual gross profit and cost of revenue do not add up to its quarters, so only
# these are derived (year minus three quarters) when Yahoo skips a quarter.
DERIVABLE = ("revenue", "operating_income", "net_income", "eps_diluted")
METRICS = ("revenue", "cost_of_revenue", "gross_profit", "operating_income", "net_income",
           "eps_diluted")

# Ind AS (NSE/BSE results taxonomy) concepts, first match wins
INDAS = {
    "revenue": ["RevenueFromOperations"],
    "other_income": ["OtherIncome"],
    "finance_costs": ["FinanceCosts"],
    "profit_before_exceptional": ["ProfitBeforeExceptionalItemsAndTax"],
    "net_income": ["ProfitOrLossAttributableToOwnersOfParent", "ProfitLossForPeriod", "ProfitLoss"],
    "eps_diluted": ["DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
                    "DilutedEarningsLossPerShareFromContinuingOperations",
                    "DilutedEarningsLossPerShare", "DilutedEarningsPerShare"],
}
# the same rows as labelled on NSE's results page (normalised to lower case)
PAGE_LABELS = {
    "revenue": ["revenue from operations"],
    "other_income": ["other income"],
    "finance_costs": ["finance costs"],
    "profit_before_exceptional": ["total profit before exceptional items and tax",
                                  "profit before exceptional items and tax"],
    "net_income": ["profit or loss, attributable to owners of parent",
                   "total profit (loss) for period", "net profit (loss) for the period"],
    "eps_diluted": ["diluted earnings (loss) per share from continuing and discontinued operations",
                    "diluted earnings (loss) per share from continuing operations",
                    "diluted earnings per share"],
}
ROUNDING = {"actual": 1, "rupees": 1, "units": 1, "hundreds": 1e2, "thousands": 1e3,
            "lakhs": 1e5, "lakh": 1e5, "millions": 1e6, "million": 1e6, "crores": 1e7, "crore": 1e7,
            "billions": 1e9}

IST_OFFSET = _dt.timedelta(hours=5, minutes=30)   # India: UTC+5:30, no daylight saving
DEFAULT_SUFFIX = ".NS"
SYMBOL_RX = re.compile(r"^[A-Z0-9&\-]{1,20}(\.(NS|BO))?$")


def to_ist(utc):
    """naive UTC -> naive India (IST) wall clock."""
    return utc.replace(tzinfo=None) + IST_OFFSET


def nse_symbol(sym):
    """RELIANCE -> RELIANCE.NS; RELIANCE.NS, TCS.BO and ^NSEI unchanged. Stored form."""
    s = str(sym).strip().upper()
    if s and not s.startswith("^") and not s.endswith((".NS", ".BO")):
        s += DEFAULT_SUFFIX
    return s


def yahoo_symbol(sym):
    """URL-safe Yahoo symbol: M&M -> M%26M.NS."""
    return urllib.parse.quote(nse_symbol(sym), safe=".-^")


class FactsError(Exception):
    pass


class Fetcher(object):
    def __init__(self, offline=None, pause=0.3):
        self.offline, self.pause, self._last = offline, pause, 0.0

    def get(self, url, name):
        """Bytes from a plain GET (or DIR/name offline)."""
        if self.offline:
            path = os.path.join(self.offline, name)
            if not os.path.isfile(path):
                raise FactsError("offline: no file %s" % path)
            with open(path, "rb") as fh:
                return fh.read()
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise FactsError("HTTP %s for %s: the source wants a sign-in cookie or crumb, which "
                                 "this tool does not work around. Save the company's results from "
                                 "NSE in your browser and pass --file." % (exc.code, url))
            raise FactsError("HTTP %s for %s" % (exc.code, url))
        except urllib.error.URLError as exc:
            raise FactsError("could not reach %s: %s" % (url, exc.reason))
        return data

    def json(self, url, name):
        data = self.get(url, name)
        try:
            return json.loads(data.decode("utf-8"))
        except ValueError:
            raise FactsError("%s did not return JSON" % url)


# ---------------------------------------------------------------- calendar

def _date(s):
    return _dt.date.fromisoformat(s)


def month_end(year, month):
    """Last day of a month (month may run past 12 or below 1)."""
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    nxt = _dt.date(year + (month == 12), month % 12 + 1, 1)
    return nxt - _dt.timedelta(days=1)


def add_quarters(end, n):
    d = _date(end) if isinstance(end, str) else end
    return month_end(d.year, d.month + 3 * n)


def fiscal(end, fy_end_month=3):
    """(quarter, fiscal year) on a year ending in fy_end_month. 2026-09-30 -> (2, 2027)."""
    d = _date(end) if isinstance(end, str) else end
    q = ((d.month - fy_end_month - 1) % 12) // 3 + 1
    fy = d.year if d.month <= fy_end_month else d.year + 1
    return q, fy


def fiscal_label(end, fy_end_month=3):
    """2026-09-30 -> "Q2 FY27" (April-March year)."""
    q, fy = fiscal(end, fy_end_month)
    return "Q%d FY%02d" % (q, fy % 100)


def results_due(end, fy_end_month=3):
    """SEBI LODR: 45 days after the quarter end, 60 for the last quarter of the year."""
    d = _date(end) if isinstance(end, str) else end
    q, _ = fiscal(d, fy_end_month)
    return d + _dt.timedelta(days=60 if q == 4 else 45)


def today_ist():
    return to_ist(_dt.datetime.now(_dt.timezone.utc)).date()


# ---------------------------------------------------------------- money

def inr(v):
    """1234567 -> ₹12.35 lakh, 52300000000 -> ₹5,230.00 crore."""
    a = abs(v)
    if a >= 1e7:
        return "₹{:,.2f} crore".format(v / 1e7)
    if a >= 1e5:
        return "₹{:,.2f} lakh".format(v / 1e5)
    return "₹{:,.2f}".format(v)


def money(v, currency="INR"):
    if v is None:
        return "n/a"
    if currency == "INR":
        return inr(v)
    return "%s %s million" % (currency, "{:,.0f}".format(v / 1e6))


def per_share(v, currency="INR"):
    return ("₹%.2f" if currency == "INR" else currency + " %.2f") % v


# ---------------------------------------------------------------- Yahoo

def yahoo_types():
    return ",".join("%s%s" % (p, YAHOO_FIELDS[k]) for k in METRICS for p in ("quarterly", "annual"))


def yahoo_series(doc):
    """Yahoo timeseries JSON -> ({metric: {end: quarter}}, {metric: {end: annual}}, currency, fy_end)."""
    results = ((doc or {}).get("timeseries") or {}).get("result") or []
    stems = dict((v, k) for k, v in YAHOO_FIELDS.items())
    quarters = dict((k, {}) for k in METRICS)
    years = dict((k, {}) for k in METRICS)
    currencies, months = {}, {}
    for r in results:
        t = ((r.get("meta") or {}).get("type") or [""])[0]
        m = re.match(r"^(quarterly|annual)(\w+)$", t)
        if not m or m.group(2) not in stems:
            continue
        key = stems[m.group(2)]
        for x in r.get(t) or []:
            if not x or "asOfDate" not in x:
                continue
            val = (x.get("reportedValue") or {}).get("raw")
            if val is None:
                continue
            end = x["asOfDate"]
            cur = x.get("currencyCode") or "INR"
            currencies[cur] = currencies.get(cur, 0) + 1
            if m.group(1) == "quarterly" and x.get("periodType", "3M") == "3M":
                start = (add_quarters(end, -1) + _dt.timedelta(days=1)).isoformat()
                quarters[key][end] = {"value": float(val), "start": start, "end": end,
                                      "derived": False, "source": "yahoo"}
            elif m.group(1) == "annual" and x.get("periodType", "12M") == "12M":
                years[key][end] = float(val)
                mo = _date(end).month
                months[mo] = months.get(mo, 0) + 1
    currency = max(currencies, key=currencies.get) if currencies else "INR"
    fy_end = max(months, key=months.get) if months else 3
    return quarters, years, currency, fy_end


def derive_missing(quarters, years):
    """Fill one missing quarter per year: the year minus the three quarters inside it."""
    for end, total in years.items():
        ends = [add_quarters(end, -i).isoformat() for i in range(4)]
        missing = [e for e in ends if e not in quarters]
        if len(missing) != 1:
            continue
        e = missing[0]
        quarters[e] = {"value": total - sum(quarters[x]["value"] for x in ends if x != e),
                       "start": (add_quarters(e, -1) + _dt.timedelta(days=1)).isoformat(),
                       "end": e, "derived": True, "source": "yahoo"}
    return quarters


def from_yahoo(doc):
    quarters, years, currency, fy_end = yahoo_series(doc)
    for k in DERIVABLE:
        derive_missing(quarters[k], years[k])
    return {"series": quarters, "currency": currency, "fy_end_month": fy_end,
            "basis": "as Yahoo reports it (consolidated for most NSE companies)",
            "source": "Yahoo Finance fundamentals timeseries"}


def fetch_yahoo(symbol, fetcher, now=None):
    now = int(now or time.time())
    url = YAHOO_TS_URL % (yahoo_symbol(symbol), yahoo_types(), YAHOO_PERIOD1, now)
    doc = fetcher.json(url, "timeseries-%s.json" % nse_symbol(symbol))
    err = ((doc or {}).get("timeseries") or {}).get("error") or (doc or {}).get("finance", {}).get("error")
    if err:
        raise FactsError("Yahoo said: %s" % (err.get("description") if isinstance(err, dict) else err))
    return from_yahoo(doc)


# ---------------------------------------------------------------- results files

def _num(text):
    """'2,69,49,600.00' -> 26949600.0, '(81,100.00)' -> -81100.0, else None."""
    s = (text or "").strip().replace(",", "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    if not re.fullmatch(r"-?\d+(\.\d+)?", s):
        return None
    v = float(s)
    return -v if neg else v


def _dmy(text):
    m = re.match(r"^\s*(\d{1,2})[-/](\d{1,2}|[A-Za-z]{3})[-/](\d{4})", text or "")
    if not m:
        m2 = re.match(r"^\s*(\d{4})-(\d{2})-(\d{2})", text or "")
        return "%s-%s-%s" % m2.groups() if m2 else None
    day, mon, year = m.groups()
    if mon.isalpha():
        try:
            mon = _dt.datetime.strptime(mon.title(), "%b").month
        except ValueError:
            return None
    try:
        return _dt.date(int(year), int(mon), int(day)).isoformat()
    except ValueError:
        return None


def operating_profit(v):
    """Profit before exceptional items and tax + finance costs - other income."""
    pbe = v.get("profit_before_exceptional")
    if pbe is None:
        return None
    return pbe + (v.get("finance_costs") or 0.0) - (v.get("other_income") or 0.0)


def parse_results_page(text):
    """NSE's results page (the "XBRL" link of a filing, rendered as HTML) -> period records."""
    text = re.sub(r"(?is)<(style|script).*?</\1>", " ", text)
    cells = [" ".join(_html.unescape(c).split())
             for c in re.split(r"(?i)<\s*/?\s*t[dh][^>]*>", text)]
    cells = [re.sub(r"<[^>]+>", " ", c) for c in cells]
    cells = [" ".join(c.split()) for c in cells if c.strip()]
    low = [c.lower() for c in cells]

    def after(label, n=1):
        try:
            i = low.index(label.lower())
        except ValueError:
            return None
        return cells[i + 1:i + 1 + n]

    def one(label):
        v = after(label)
        return v[0] if v else None

    rounding = one("Level of rounding used in financial results")
    currency = one("Description of presentation currency")
    if not rounding or rounding.strip().lower() not in ROUNDING:
        raise FactsError("the results page does not state its rounding (lakh, crore...): "
                         "refusing to guess the units")
    scale = ROUNDING[rounding.strip().lower()]
    starts = after("Date of start of reporting period", 2) or []
    ends = after("Date of end of reporting period", 2) or []
    nature = one("Nature of report standalone or consolidated")
    values = [{}, {}]
    for key, labels in PAGE_LABELS.items():
        for lab in labels:
            got = after(lab, 2)
            if not got:
                continue
            nums = [_num(g) for g in got]
            if nums[0] is None:
                continue
            for col in (0, 1):
                if nums[col] is not None:
                    values[col][key] = nums[col] if key == "eps_diluted" else nums[col] * scale
            break
    out = []
    for col in (0, 1):
        s = _dmy(starts[col]) if col < len(starts) else None
        e = _dmy(ends[col]) if col < len(ends) else None
        if not (s and e and values[col]):
            continue
        out.append({"start": s, "end": e, "nature": (nature or "").lower(), "values": values[col],
                    "currency": (currency or "INR").upper(), "units": "₹ " + rounding.lower(),
                    "symbol": one("NSE Symbol") or one("Symbol"), "name": one("Name of company")})
    if not out:
        raise FactsError("no reporting periods with numbers on the results page")
    return out


def _local(tag):
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag.split(":")[-1]


def parse_xbrl(data):
    """An Ind AS financial-results XBRL instance -> period records."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise FactsError("not valid XBRL/XML: %s" % exc)
    contexts, units = {}, {}
    for el in root:
        name = _local(el.tag)
        if name == "context":
            if any(_local(x.tag) in ("segment", "scenario") for x in el.iter()):
                continue  # a dimension (segment, member...), not the company total
            p = {_local(x.tag): (x.text or "").strip() for x in el.iter()
                 if _local(x.tag) in ("startDate", "endDate", "instant")}
            contexts[el.get("id")] = p
        elif name == "unit":
            units[el.get("id")] = " ".join((x.text or "").strip() for x in el.iter()
                                           if _local(x.tag) == "measure")
    text_facts, periods, decimals = {}, {}, set()
    for el in root:
        name = _local(el.tag)
        ctx = el.get("contextRef")
        if not ctx:
            continue
        if el.get("unitRef") is None:
            text_facts.setdefault(name, (el.text or "").strip())
            continue
        c = contexts.get(ctx)
        if not c or "startDate" not in c:
            continue
        unit = units.get(el.get("unitRef"), "")
        measures = unit.lower()
        v = _num(el.text)
        if v is None:
            continue
        currency = "INR"
        cur = re.search(r"iso4217:([a-z]{3})", measures)
        if cur:
            currency = cur.group(1).upper()
        elif "inr" not in measures:
            continue  # a ratio, share count or percentage
        rec = periods.setdefault((c["startDate"], c["endDate"]), {"values": {}, "currency": currency})
        rec["values"].setdefault(name, v)
        if el.get("decimals") not in (None, "INF") and "shares" not in measures:
            decimals.add(el.get("decimals"))
    nature = ""
    for k, v in text_facts.items():
        if k.lower().startswith("natureofreport"):
            nature = v.lower()
    named = {"-3": "₹1,000", "-5": "₹1 lakh", "-6": "₹10 lakh", "-7": "₹1 crore", "0": "₹1"}
    rounding = "; ".join("decimals %s (to the nearest %s)" % (d, named.get(d, "10^%s" % d.lstrip("-")))
                         for d in sorted(decimals)) or "decimals not stated"
    out = []
    for (start, end), rec in sorted(periods.items()):
        vals = {}
        for key, concepts in INDAS.items():
            for c in concepts:
                if c in rec["values"]:
                    vals[key] = rec["values"][c]
                    break
        if not vals:
            continue
        out.append({"start": start, "end": end, "nature": nature, "values": vals,
                    "currency": rec["currency"], "units": "%s, %s" % (rec["currency"], rounding),
                    "symbol": text_facts.get("Symbol") or text_facts.get("NSESymbol"),
                    "name": text_facts.get("NameOfTheCompany") or text_facts.get("NameOfCompany")})
    if not out:
        raise FactsError("no Ind AS results figures (RevenueFromOperations, ProfitLossForPeriod...) "
                         "in the XBRL file")
    return out


def _rows_of(doc):
    if isinstance(doc, dict):
        for k in ("data", "rows", "results"):
            if isinstance(doc.get(k), list):
                return doc[k]
        return [doc]
    return doc if isinstance(doc, list) else []


def parse_results_json(doc, fetcher):
    """NSE financial-results list (saved JSON): fetch each row's XBRL link (nsearchives only)."""
    out, skipped = [], []
    for row in _rows_of(doc):
        if not isinstance(row, dict):
            continue
        low = dict((str(k).lower(), v) for k, v in row.items())
        link = str(low.get("xbrl") or low.get("xbrlfile") or low.get("attachment") or "").strip()
        if not link or link == "-":
            continue
        host = urllib.parse.urlparse(link).hostname or ""
        if host not in ALLOWED_HOSTS:
            skipped.append(link)
            continue
        name = os.path.basename(urllib.parse.urlparse(link).path)
        try:
            out.extend(parse_any(fetcher.get(link, name), name, fetcher))
        except FactsError as exc:
            skipped.append("%s (%s)" % (link, exc))
    for s in skipped:
        print("facts: skipped %s (only nsearchives.nseindia.com links are fetched)" % s,
              file=sys.stderr)
    if not out:
        raise FactsError("no readable XBRL in the results list%s" % (
            ": " + "; ".join(skipped[:3]) if skipped else ""))
    return out


def parse_any(data, name, fetcher=None):
    head = data[:4096].lstrip().lower()
    if head.startswith(b"{") or head.startswith(b"["):
        try:
            doc = json.loads(data.decode("utf-8-sig"))
        except ValueError:
            raise FactsError("%s is not valid JSON" % name)
        return parse_results_json(doc, fetcher or Fetcher())
    if b"<html" in head or b"<!doctype html" in head:
        return parse_results_page(data.decode("utf-8", "replace"))
    if head.startswith(b"<?xml") or b"xbrl" in head:
        return parse_xbrl(data)
    raise FactsError("%s is not an NSE results page, XBRL file or results JSON" % name)


def from_files(paths, fetcher):
    """Saved results files -> the same shape as from_yahoo(). Consolidated wins over standalone."""
    records = []
    for p in paths:
        with open(p, "rb") as fh:
            records.extend(parse_any(fh.read(), p, fetcher))
    best = {}
    for r in records:
        key = (r["start"], r["end"])
        rank = 1 if "consolidated" in r["nature"] and not r["nature"].startswith("non") else 0
        if key not in best or rank > best[key][0]:
            best[key] = (rank, r)
    series = dict((k, {}) for k in METRICS)
    years = dict((k, {}) for k in METRICS)
    natures, units, name, symbol, currency = set(), set(), None, None, "INR"
    for (start, end), (rank, r) in best.items():
        v = dict(r["values"])
        v["operating_income"] = operating_profit(v)
        days = (_date(end) - _date(start)).days
        natures.add("consolidated" if rank else (r["nature"] or "basis not stated"))
        units.add(r["units"])
        name, symbol, currency = name or r.get("name"), symbol or r.get("symbol"), r["currency"]
        for k in ("revenue", "operating_income", "net_income", "eps_diluted"):
            if v.get(k) is None:
                continue
            if 80 <= days <= 100:
                series[k][end] = {"value": v[k], "start": start, "end": end, "derived": False,
                                  "source": "file"}
            elif 350 <= days <= 380:
                years[k][end] = v[k]
    for k in DERIVABLE:
        derive_missing(series[k], years[k])
    if not any(series[k] for k in METRICS):
        raise FactsError("the files hold no quarter (3-month) figures")
    return {"series": series, "currency": currency, "fy_end_month": 3,
            "basis": " and ".join(sorted(natures)), "units": sorted(units),
            "source": "NSE/BSE results file%s (%s)" % ("s" if len(paths) > 1 else "",
                                                        ", ".join(os.path.basename(p) for p in paths)),
            "name": name, "symbol": symbol}


# ---------------------------------------------------------------- the report

def _yoy_match(series_, end, days=365, slack=10):
    target = _date(end) - _dt.timedelta(days=days)
    best = None
    for e in series_:
        gap = abs((_date(e) - target).days)
        if gap <= slack and (best is None or gap < best[0]):
            best = (gap, e)
    return series_[best[1]] if best else None


def pct(a, b):
    if a is None or b in (None, 0):
        return None
    return round(100.0 * (a - b) / abs(b), 1) + 0.0  # no "-0.0"


def margin(part, whole):
    if part is None or not whole:
        return None
    return round(100.0 * part / whole, 1)


def analyse(data, quarters=6, name=None, symbol=None):
    """Every number in the report, as a dict. data: from_yahoo() or from_files()."""
    s = data["series"]
    fy_end = data.get("fy_end_month", 3)
    rev = s["revenue"]
    if not rev and not s["net_income"]:
        raise FactsError("no quarterly revenue or net profit")
    ends = sorted(set(rev) | set(s["net_income"]), reverse=True)[:quarters]
    rows = []
    for end in ends:
        r = rev.get(end, {}).get("value")
        gp = s["gross_profit"].get(end, {}).get("value")
        if gp is None and r is not None and end in s["cost_of_revenue"]:
            gp = r - s["cost_of_revenue"][end]["value"]
        oi = s["operating_income"].get(end, {}).get("value")
        ni = s["net_income"].get(end, {}).get("value")
        eps = s["eps_diluted"].get(end, {}).get("value")
        prior = dict((k, _yoy_match(s[k], end)) for k in ("revenue", "net_income", "eps_diluted"))
        base = rev.get(end) or s["net_income"].get(end) or {}
        rows.append({
            "end": end, "start": base.get("start"), "fiscal": fiscal_label(end, fy_end),
            "derived": any(s[k].get(end, {}).get("derived") for k in ("revenue", "net_income")),
            "revenue": r, "gross_profit": gp, "operating_income": oi, "net_income": ni,
            "eps_diluted": round(eps, 2) if eps is not None else None,
            "gross_margin": margin(gp, r), "operating_margin": margin(oi, r),
            "net_margin": margin(ni, r),
            "revenue_yoy": pct(r, (prior["revenue"] or {}).get("value")),
            "net_income_yoy": pct(ni, (prior["net_income"] or {}).get("value")),
            "eps_yoy": pct(eps, (prior["eps_diluted"] or {}).get("value")),
            "results_due": results_due(end, fy_end).isoformat(),
        })
    ttm = {}
    for key in ("revenue", "net_income", "eps_diluted"):
        last4 = sorted(s[key])[-4:]
        if len(last4) == 4 and (_date(last4[-1]) - _date(last4[0])).days < 300:
            ttm[key] = sum(s[key][e]["value"] for e in last4)
    if "eps_diluted" in ttm:
        ttm["eps_diluted"] = round(ttm["eps_diluted"], 2)
    return {"entity": name or data.get("name"), "symbol": symbol or data.get("symbol"),
            "currency": data.get("currency", "INR"), "fy_end_month": fy_end,
            "basis": data.get("basis"), "units": data.get("units"), "source": data.get("source"),
            "rows": rows, "ttm": ttm}


def schedule(result, today):
    """The next results due date, and a NOTE when a due quarter is missing from the data."""
    if not result["rows"]:
        return None, None
    fy_end = result.get("fy_end_month", 3)
    latest = result["rows"][0]["end"]
    nxt = add_quarters(latest, 1)
    overdue = None
    while results_due(nxt, fy_end) < today:
        overdue = nxt
        nxt = add_quarters(nxt, 1)
    q4 = fiscal(nxt, fy_end)[0] == 4
    nxt_line = ("Next results: %s (quarter ending %s) due by %s under SEBI LODR (%s). The exact "
                "board-meeting date is announced on NSE beforehand." % (
                    fiscal_label(nxt, fy_end), nxt.isoformat(), results_due(nxt, fy_end).isoformat(),
                    "60 days after the year end for Q4" if q4 else
                    "45 days after the quarter end; 60 for Q4"))
    note = None
    if overdue:
        note = ("the data stops at %s (%s), but results up to the quarter ending %s (%s) were due by "
                "%s. The source may lag: check NSE's financial results for the company and pass "
                "the saved page with --file." % (latest, fiscal_label(latest, fy_end),
                                                  overdue.isoformat(), fiscal_label(overdue, fy_end),
                                                  results_due(overdue, fy_end).isoformat()))
    return nxt_line, note


def changes(result):
    """Plain sentences: what changed in the latest quarter."""
    rows = result["rows"]
    if not rows:
        return []
    cur = rows[0]
    prev = rows[1] if len(rows) > 1 else None
    if prev and prev["end"] != add_quarters(cur["end"], -1).isoformat():
        prev = None  # not the quarter just before (a gap in the data)
    ccy = result.get("currency", "INR")
    out = []
    label = "%s, quarter ending %s%s" % (cur.get("fiscal") or "", cur["end"],
                                         " (derived from the full year)" if cur["derived"] else "")
    line = "%s: revenue %s" % (label, money(cur["revenue"], ccy))
    if cur["revenue_yoy"] is not None:
        line += " (%+.1f%% vs a year earlier)" % cur["revenue_yoy"]
    line += ", net profit %s" % money(cur["net_income"], ccy)
    if cur["net_income_yoy"] is not None:
        line += " (%+.1f%%)" % cur["net_income_yoy"]
    if cur["eps_diluted"] is not None:
        line += ", diluted EPS %s" % per_share(cur["eps_diluted"], ccy)
        if cur["eps_yoy"] is not None:
            line += " (%+.1f%%)" % cur["eps_yoy"]
    out.append(line + ".")
    for key, name in (("gross_margin", "Gross margin"), ("operating_margin", "Operating margin"),
                      ("net_margin", "Net margin")):
        if cur[key] is None:
            continue
        s = "%s %.1f%%" % (name, cur[key])
        if prev and prev[key] is not None:
            s += " (%+.1f points vs the quarter before)" % (cur[key] - prev[key])
        out.append(s + ".")
    if cur["net_income"] is not None and cur["operating_income"] is not None \
            and cur["net_income"] > cur["operating_income"]:
        out.append("Net profit is above operating profit: it includes gains outside the "
                   "business (other income, exceptional items, tax). Check the results filing "
                   "before reading it as growth.")
    if prev and cur["revenue"] is not None and prev["revenue"]:
        out.append("Revenue %+.1f%% vs the quarter before (many businesses are seasonal: "
                   "compare with a year earlier first)." % pct(cur["revenue"], prev["revenue"]))
    return out


# ---------------------------------------------------------------- output

def _cr(v, currency="INR"):
    """₹ crore, or millions for a company Yahoo reports in another currency."""
    return "-" if v is None else "{:,.0f}".format(v / (1e7 if currency == "INR" else 1e6))


def _p(v):
    return "-" if v is None else "%+.1f%%" % v if v < 0 or v > 0 else "0.0%"


def _pm(v):
    return "-" if v is None else "%.1f%%" % v


def render(symbol, result, today=None):
    ccy = result.get("currency", "INR")
    unit = "₹ crore" if ccy == "INR" else "%s million" % ccy
    who = "%s (%s)" % (result["entity"], symbol) if result.get("entity") else symbol
    head = "%s. %s except EPS. Source: %s; %s." % (who, unit, result["source"], result["basis"])
    out = [head]
    if ccy != "INR":
        out.append("NOTE: the source reports this company in %s, not ₹ (its overseas filings). For "
                   "₹ figures, save its NSE results page and pass --file." % ccy)
    if result.get("units"):
        out.append("Units as stated in the file: %s." % "; ".join(result["units"]))
    out.append("")
    out.append("%-11s %-9s %10s %8s %7s %7s %7s %10s %8s %7s" % (
        "qtr end", "fiscal", "revenue", "rev yoy", "gross", "oper", "net",
        "net prof", "EPS dil", "EPS yoy"))
    for r in result["rows"]:
        out.append("%-11s %-9s %10s %8s %7s %7s %7s %10s %8s %7s%s" % (
            r["end"], r["fiscal"], _cr(r["revenue"], ccy), _p(r["revenue_yoy"]),
            _pm(r["gross_margin"]), _pm(r["operating_margin"]), _pm(r["net_margin"]),
            _cr(r["net_income"], ccy), "-" if r["eps_diluted"] is None else "%.2f" % r["eps_diluted"],
            _p(r["eps_yoy"]), "  derived" if r["derived"] else ""))
    t = result["ttm"]
    if t:
        out.append("")
        out.append("Last 4 quarters: revenue %s, net profit %s, diluted EPS %s." % (
            money(t.get("revenue"), ccy) if t.get("revenue") is not None else "-",
            money(t.get("net_income"), ccy) if t.get("net_income") is not None else "-",
            per_share(t["eps_diluted"], ccy) if t.get("eps_diluted") is not None else "-"))
    out.append("")
    out.append("What changed:")
    for line in changes(result):
        out.append("  - " + line)
    nxt, note = schedule(result, today or today_ist())
    if nxt:
        out.append("")
        out.append(nxt)
    if note:
        out.append("")
        out.append("NOTE: " + note)
    if any(r["derived"] for r in result["rows"]):
        out.append("")
        out.append("Derived quarters are the full year minus the other three quarters (the source "
                   "skipped them); derived EPS is approximate.")
    return "\n".join(out) + "\n"


def metrics_row(symbol, result):
    rows = result["rows"]
    cur = rows[0] if rows else {}
    t = result["ttm"]
    return {"symbol": symbol, "name": result["entity"], "currency": result.get("currency", "INR"),
            "period_end": cur.get("end"), "fiscal_quarter": cur.get("fiscal"),
            "revenue_ttm": t.get("revenue"), "net_income_ttm": t.get("net_income"),
            "eps_ttm": t.get("eps_diluted"),
            "revenue_growth_yoy": cur.get("revenue_yoy"), "eps_growth_yoy": cur.get("eps_yoy"),
            "net_income_growth_yoy": cur.get("net_income_yoy"),
            "gross_margin": cur.get("gross_margin"), "operating_margin": cur.get("operating_margin"),
            "net_margin": cur.get("net_margin"), "source": result.get("source")}


def main(argv=None):
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("symbols", nargs="*", help="NSE symbols, e.g. RELIANCE.NS TCS (.NS is added)")
    ap.add_argument("--file", action="append", metavar="PATH",
                    help="saved NSE/BSE results (results page .html, Ind AS XBRL .xml, or the "
                         "results list .json); repeat for more quarters. One symbol only.")
    ap.add_argument("--quarters", type=int, default=6)
    ap.add_argument("--metrics", metavar="FILE", help="merge a row per symbol into this JSON")
    ap.add_argument("--today", help="YYYY-MM-DD (IST), for reproducible due-date notes")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not args.symbols and not args.file:
        ap.print_help()
        return 2
    if args.file and len(args.symbols) > 1:
        print("facts: --file reads one company: give one symbol (or none)", file=sys.stderr)
        return 2
    for s in args.symbols:
        if not SYMBOL_RX.match(nse_symbol(s)):
            print("facts: %r is not an NSE symbol" % s, file=sys.stderr)
            return 2
    today = _dt.date.fromisoformat(args.today) if args.today else today_ist()
    fetcher = Fetcher(offline=args.offline)
    results, rows, failed = [], [], 0
    wanted = [nse_symbol(s) for s in args.symbols] or [None]
    for sym in wanted:
        try:
            if args.file:
                data = from_files(args.file, fetcher)
                sym = sym or (nse_symbol(data["symbol"]) if data.get("symbol") else "file")
            else:
                data = fetch_yahoo(sym, fetcher)
            result = analyse(data, quarters=args.quarters, symbol=sym)
        except (FactsError, OSError) as exc:
            msg = str(exc)
            if "no quarterly revenue" in msg and not args.file:
                msg = ("no NSE results found for %s on Yahoo (check the symbol, or save the "
                       "company's results from NSE and pass --file)" % sym)
            print("facts: %s: %s" % (sym, msg), file=sys.stderr)
            failed += 1
            continue
        nxt, note = schedule(result, today)
        results.append({"symbol": sym, "result": result, "changes": changes(result),
                        "next_results": nxt, "note": note})
        rows.append(metrics_row(sym, result))
        if not args.json:
            sys.stdout.write(render(sym, result, today))
            sys.stdout.write("\n")
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    if args.metrics and rows:
        existing = {}
        if os.path.isfile(args.metrics):
            with open(args.metrics, encoding="utf-8") as fh:
                existing = dict((r.get("symbol") or r.get("ticker"), r)
                                for r in json.load(fh).get("companies", []))
        for r in rows:
            existing[r["symbol"]] = r
        folder = os.path.dirname(args.metrics)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.metrics, "w", encoding="utf-8") as fh:
            json.dump({"as_of": today.isoformat(),
                       "companies": sorted(existing.values(), key=lambda r: r.get("symbol") or "")},
                      fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        if not args.json:
            print("wrote %s (%d companies)" % (args.metrics, len(existing)))
    return 0 if results and not failed else (1 if results else 2)


if __name__ == "__main__":
    sys.exit(main())

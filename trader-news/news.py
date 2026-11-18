#!/usr/bin/env python3
"""News monitor for trader-news (NSE, India). Standard library only.

Watches headlines and NSE corporate announcements for the stocks you watch or
hold and prints only what is new since the last check, newest first, with how
far each stock has moved today and a flag on anything that touches your stocks.

    python3 news.py                                      # watchlist + plan + portfolio symbols
    python3 news.py --tickers RELIANCE.NS TCS.NS INFY
    python3 news.py --tickers RELIANCE --all --hours 72  # everything in the last 3 days, seen or not
    python3 news.py --no-nse                             # headlines only
    python3 news.py --no-moves                           # skip the price line per stock
    python3 news.py --announcements-file CF-AN-equities.csv   # a file saved from nseindia.com

Your symbols, when --tickers is not given: every hit in trader/watchlist.json
(trader-scanner), the symbol in trader/plan.json (trader-exit) and the
positions in trader/portfolio.json. A bare symbol (RELIANCE) means NSE
(RELIANCE.NS).

Sources:
  - Yahoo Finance headline RSS, one request per symbol:
    https://feeds.finance.yahoo.com/rss/2.0/headline?s=<SYMBOL>.NS&region=IN&lang=en-IN
  - NSE corporate announcements (SEBI LODR Regulation 30: results, board
    meetings, deals, management changes, orders, pledges...), NSE's public
    RSS feed of the latest announcements for every company, one request:
    https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml
    It holds the most recent items only, so check often during the day. For a
    longer history, open https://www.nseindia.com/companies-listing/corporate-filings-announcements
    in your browser, download the CSV (or save the JSON) and pass it with
    --announcements-file. This tool never fakes a browser session.
    Announcements are matched to your stocks by the symbol in the attachment
    name, or by the company's name (from trader/universe.txt or Yahoo).
  - The move: Yahoo Finance's chart endpoint (unofficial, can lag), the last
    price against yesterday's close:
    https://query2.finance.yahoo.com/v8/finance/chart/<SYMBOL>.NS?range=1d&interval=5m

Times print in India time (IST, UTC+5:30). State: trader/state/news.json
remembers what you have seen (30 days), so an item shows once. Offline:
--offline DIR reads yahoo-<SYMBOL>.xml, chart-<SYMBOL>.json and
nse-announcements.xml from DIR. The tests use it.

Headlines are leads, not facts: open the story or the filing before acting,
and treat nothing here as advice.
"""

import argparse
import csv
import datetime as _dt
import email.utils
import gzip
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

YAHOO = "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%s&region=IN&lang=en-IN"
CHART = "https://%s.finance.yahoo.com/v8/finance/chart/%s?range=1d&interval=5m"
NSE_FEED = "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml"
NSE_PAGE = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
UA = "Mozilla/5.0 (compatible; day-trader-skill/3.0)"   # NSE drops requests whose User-Agent holds a URL
KEEP_DAYS = 30
MOVE_PCT = 3.0  # a stock this far from yesterday's close is flagged as moving
IST_OFFSET = _dt.timedelta(hours=5, minutes=30)   # India: UTC+5:30, no daylight saving
IST = _dt.timezone(IST_OFFSET)
DEFAULT_SUFFIX = ".NS"

# NSE announcement subjects -> tags. The first match wins its tag; an item can carry several.
NSE_SUBJECTS = [
    ("earnings", r"financial results|limited review|results? updates?|audited|unaudited|earnings"
                 r"|transcripts?|quarterly calls?"),
    ("board", r"board meeting"),
    ("deal", r"acquisition|amalgamation|merger|scheme of arrangement|demerger|takeover|open offer"
             r"|sale or disposal|joint venture|tie up|agreement"),
    ("orders", r"bagging|receiving of orders|orders? (?:won|received)|contracts?"),
    ("management", r"change in management|change in directors|resignation|appointment|cessation"
                   r"|managing director|\bceo\b|\bcfo\b|\bkmp\b"),
    ("analyst", r"credit rating|analysts?|investor meet|con\. call|investor presentation"),
    ("capital", r"dividend|buy ?back|bonus|split|rights issue|allotment|fund raising|record date"
                r"|preferential|qip|esop|esos|alteration of capital"),
    ("promoter", r"pledge|encumbrance|promoter|regulation 31|sast|regulation 29"),
    ("deal_flow", r"bulk deal|block deal"),
    ("halt", r"suspension|trading window|surveillance|delisting|\basm\b|\bgsm\b"),
    ("regulatory", r"\bsebi\b|show cause|penalty|litigation|orders? passed|action\(s\) initiated"
                   r"|insolvency|\bnclt\b|\bibc\b|default|fraud|regulation 30"),
]
# subjects that move a stock: printed with "!"
IMPORTANT_TAGS = {"earnings", "deal", "orders", "management", "promoter", "halt", "regulatory"}
ROUTINE = re.compile(r"newspaper publication|declaration of nav|trading window closure"
                     r"|certificate under regulation 74|loss of share certificate", re.I)

KEYWORDS = [
    ("earnings", r"\bearnings\b|\bresults\b|\bprofit\b|\brevenue\b|\bEPS\b|\bguidance\b|\bQ[1-4]\b"
                 r"|\bquarterly\b"),
    ("analyst", r"\bupgrade[sd]?\b|\bdowngrade[sd]?\b|price target|target price|\bbuy rating\b"
                r"|\boutperform\b|\bunderperform\b|\brating\b"),
    ("legal", r"\blawsuit\b|\bprobe\b|\binvestigation\b|\bcourt\b|\bNCLT\b|\btribunal\b"),
    ("deal", r"\bacqui(?:re|res|red|sition)\b|\bmerger\b|\bdemerger\b|\bstake\b|\btakeover\b"
             r"|\bdeal\b|\bopen offer\b"),
    ("orders", r"\border(?:s)? (?:worth|win|from)\b|\bwins? (?:order|contract)\b|\bcontract\b"),
    ("capital", r"\bdividend\b|\bbuyback\b|\bbonus\b|\bstock split\b|\bQIP\b|\brights issue\b"
                r"|\bOFS\b|\bIPO\b"),
    ("people", r"\bCEO\b|\bCFO\b|\bMD\b|\bresign|\bsteps down\b|\bappoint"),
    ("promoter", r"\bpromoters?\b|\bpledge"),
    ("deal_flow", r"\bbulk deal\b|\bblock deal\b"),
    ("risk", r"\brecall\b|\binsolvency\b|\bdefault\b|\bdelist|\bhack|\bbreach\b|\boutage\b|\bfraud\b"),
    ("halt", r"\bupper circuit\b|\blower circuit\b|\bsuspend|\bASM\b|\bGSM\b"),
    ("regulatory", r"\bSEBI\b|\bRBI\b|\bCCI\b|\bUSFDA\b|\bFDA\b|\bapproval\b|\bpenalty\b"),
    ("macro", r"\bRBI policy\b|\brepo rate\b|\binflation\b|\bCPI\b|\bGDP\b|\bFIIs?\b|\bFPIs?\b"
              r"|\bNifty\b|\bSensex\b|\bbudget\b|\bGST\b|\btariffs?\b|\brupee\b"),
]


class NewsError(Exception):
    pass


# ---------------------------------------------------------------- time and symbols

def to_ist(utc):
    """Aware or naive UTC -> naive India (IST) wall clock."""
    if utc.tzinfo is not None:
        utc = utc.astimezone(_dt.timezone.utc)
    return utc.replace(tzinfo=None) + IST_OFFSET


def ist_to_utc(local):
    """naive IST wall clock -> naive UTC."""
    return local.replace(tzinfo=None) - IST_OFFSET


def ist_iso(now_utc):
    """UTC -> "2026-11-06T10:15:00+05:30"."""
    return to_ist(now_utc).replace(microsecond=0).isoformat() + "+05:30"


def nse_symbol(sym):
    """RELIANCE -> RELIANCE.NS; RELIANCE.NS, TCS.BO and ^NSEI unchanged. Stored form."""
    s = str(sym).strip().upper()
    if s and not s.startswith("^") and not s.endswith((".NS", ".BO")):
        s += DEFAULT_SUFFIX
    return s


def yahoo_symbol(sym):
    """URL-safe Yahoo symbol: M&M -> M%26M.NS."""
    return urllib.parse.quote(nse_symbol(sym), safe=".-^")


def bare(sym):
    """RELIANCE.NS -> RELIANCE (the NSE trading symbol)."""
    s = nse_symbol(sym)
    return s[:-3] if s.endswith((".NS", ".BO")) else s


_NAME_DROP = re.compile(r"\b(the|limited|ltd|ltd\.|co|company|corporation|corp|inc|india)\b")


def norm_name(name):
    """'Mahindra & Mahindra Limited' -> 'mahindra and mahindra'. For matching NSE's company names."""
    s = str(name or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = _NAME_DROP.sub(" ", s)
    return " ".join(s.split())


# ---------------------------------------------------------------- inputs

def load_json(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def watch_from_files(portfolio="trader/portfolio.json", watchlist="trader/watchlist.json",
                     plan="trader/plan.json"):
    """{SYMBOL.NS: name or None}: the scanner's hits, the planned trade and your holdings."""
    out = {}
    wl = load_json(watchlist) or {}
    for h in wl.get("hits", []):
        sym = h.get("symbol") or h.get("ticker")
        if sym:
            out[nse_symbol(sym)] = h.get("name")
    pl = load_json(plan) or {}
    if pl.get("symbol"):
        out.setdefault(nse_symbol(pl["symbol"]), pl.get("name"))
    pf = load_json(portfolio) or {}
    for p in pf.get("positions", []):
        sym = p.get("ticker") or p.get("symbol")
        if sym:
            s = nse_symbol(sym)
            out[s] = p.get("name") or out.get(s)
    return out


def universe_names(path="trader/universe.txt"):
    """{SYMBOL.NS: company name} from trader-scanner's universe file (SYMBOL.NS|Name lines)."""
    out = {}
    if not path or not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "|" not in line:
                continue
            sym, name = line.split("|", 1)
            out[nse_symbol(sym)] = name.strip() or None
    return out


# ---------------------------------------------------------------- fetching

class Fetcher(object):
    def __init__(self, offline=None, pause=0.15):
        self.offline = offline
        self.pause = pause
        self._last = 0.0

    def get(self, url, name):
        if self.offline:
            path = os.path.join(self.offline, name)
            if not os.path.isfile(path):
                raise NewsError("offline: no file %s" % path)
            with open(path, "rb") as fh:
                return fh.read()
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*",
                                                    "Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as exc:
            raise NewsError("HTTP %s for %s" % (exc.code, url))
        except urllib.error.URLError as exc:
            raise NewsError("could not reach %s: %s" % (url, exc.reason))
        except (OSError, ValueError) as exc:     # timeouts, resets, a bad gzip body
            raise NewsError("could not read %s: %s" % (url, exc))


# ---------------------------------------------------------------- parsing

def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _text(el, name):
    for c in el.iter():
        if _local(c.tag) == name:
            return (c.text or "").strip()
    return ""


def parse_time(value):
    """RFC 822 (RSS), ISO 8601, or NSE's '06-Nov-2026 11:40:47' (IST) -> aware UTC datetime, or None."""
    value = (value or "").strip()
    if not value:
        return None
    m = re.match(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$", value)
    if m:
        d, mon, y, hh, mm, ss = m.groups()
        try:
            month = _dt.datetime.strptime(mon.title(), "%b").month
            local = _dt.datetime(int(y), month, int(d), int(hh or 0), int(mm or 0), int(ss or 0))
        except ValueError:
            return None
        return ist_to_utc(local).replace(tzinfo=_dt.timezone.utc)
    d = None
    if not re.match(r"^\d{4}-\d{2}-\d{2}", value):
        try:
            d = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            d = None
    if d is None:
        try:
            d = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if d.tzinfo is None:          # an ISO time without an offset is India time
            d = d.replace(tzinfo=IST)
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.astimezone(_dt.timezone.utc)


def parse_rss(data, ticker):
    """Yahoo headline RSS -> items."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise NewsError("RSS for %s is not valid XML: %s" % (ticker, exc))
    out = []
    for it in root.iter("item"):
        title = _text(it, "title")
        link = _text(it, "link")
        guid = _text(it, "guid") or link
        out.append({"id": "rss:" + guid, "source": "Yahoo Finance", "kind": "headline",
                    "ticker": ticker, "title": title, "summary": _text(it, "description"),
                    "link": link, "time": parse_time(_text(it, "pubDate"))})
    return out


_LINK_SYMBOL = re.compile(r"/corporate/([A-Z0-9&\-]+)_\d{8,14}")


def _subject(desc, company):
    """'X has informed the Exchange about Y |SUBJECT: Z' -> 'Z' (or Y when there is no SUBJECT)."""
    if "|SUBJECT:" in desc:
        return desc.split("|SUBJECT:", 1)[1].strip()
    s = desc
    if company and s.lower().startswith(company.lower()):
        s = s[len(company):]
    s = re.sub(r"^\s*has informed the Exchange (?:about|regarding|that)\s*", "", s, flags=re.I)
    return s.strip()


def announcement(company, symbol, subject, details, link, when, source="NSE"):
    """One NSE announcement as an item (ticker is filled in when it is matched to your stocks)."""
    subject = " ".join((subject or "").split())
    details = " ".join((details or "").split())
    title = "%s: %s" % (company or symbol or "?", subject or details[:120])
    key = "%s|%s|%s|%s" % (symbol or company, subject, link, when.isoformat() if when else "")
    return {"id": "nse:" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:20], "source": source,
            "kind": "announcement", "ticker": None, "company": company, "nse_symbol": symbol,
            "subject": subject, "title": title, "summary": details, "link": link, "time": when}


def parse_nse_rss(data):
    """NSE's Online_announcements.xml -> announcement items (every company)."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise NewsError("NSE announcements feed is not valid XML: %s" % exc)
    out = []
    for it in root.iter("item"):
        company = _text(it, "title")
        link = _text(it, "link")
        desc = _text(it, "description")
        m = _LINK_SYMBOL.search(link)
        out.append(announcement(company, m.group(1) if m else None, _subject(desc, company), desc,
                                link, parse_time(_text(it, "pubDate"))))
    return out


def _key(h):
    return re.sub(r"[^a-z0-9]", "", str(h or "").replace("\\n", " ").lower())


ANN_FIELDS = {
    "symbol": ["symbol"],
    "company": ["companyname", "smname", "company"],
    "subject": ["subject", "desc", "purpose"],
    "details": ["details", "attchmnttext", "description"],
    "time": ["broadcastdatetime", "broadcastdateandtime", "andt", "sortdate", "exchdisstime", "date"],
    "link": ["attachment", "attchmntfile", "link"],
}


def parse_announcements_file(data):
    """An announcements CSV or JSON saved from nseindia.com in a browser -> items."""
    text = data.decode("utf-8-sig", "replace") if isinstance(data, bytes) else data
    body = text.lstrip()
    if body.startswith("{") or body.startswith("["):
        try:
            doc = json.loads(body)
        except ValueError:
            raise NewsError("the announcements file is not valid JSON")
        rows = doc.get("data") if isinstance(doc, dict) else doc
        if not isinstance(rows, list):
            raise NewsError("the announcements JSON has no list of rows")
        recs = [dict((_key(k), "" if v is None else str(v)) for k, v in r.items())
                for r in rows if isinstance(r, dict)]
    else:
        reader = csv.reader(io.StringIO(text))
        head, recs = None, []
        for rec in reader:
            if not rec or not any(c.strip() for c in rec):
                continue
            if head is None:
                head = [_key(h) for h in rec]
                continue
            recs.append(dict((head[i], rec[i].strip()) for i in range(min(len(head), len(rec)))))
    if recs and not any(k in recs[0] for k in ANN_FIELDS["symbol"] + ANN_FIELDS["company"]):
        raise NewsError("this does not look like NSE's announcements download (no symbol column)")
    out = []
    for r in recs:
        f = {}
        for name, keys in ANN_FIELDS.items():
            f[name] = next((r[k] for k in keys if r.get(k) not in (None, "", "-")), None)
        if not (f["symbol"] or f["company"]):
            continue
        out.append(announcement(f["company"], (f["symbol"] or "").upper() or None, f["subject"],
                                f["details"], f["link"] or NSE_PAGE, parse_time(f["time"]),
                                source="NSE (file)"))
    return out


def match_announcements(items, watch, names):
    """Keep the announcements about your stocks and set their ticker. Match on the NSE symbol in
    the item (or its attachment name), else on the company name."""
    by_symbol = {bare(t): t for t in watch}
    by_name = {}
    for t in watch:
        for n in (watch.get(t), names.get(t)):
            k = norm_name(n)
            if k:
                by_name.setdefault(k, t)
    out = []
    for it in items:
        t = by_symbol.get((it.get("nse_symbol") or "").upper())
        if t is None:
            t = by_name.get(norm_name(it.get("company")))
        if t is None:
            continue
        it = dict(it)
        it["ticker"] = t
        out.append(it)
    return out


def parse_chart(data, ticker):
    """Yahoo chart JSON -> {"ticker", "price", "prev_close", "pct", "time", "name"} or raise."""
    try:
        doc = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
        meta = doc["chart"]["result"][0]["meta"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise NewsError("chart for %s is not usable" % ticker)
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if not price or not prev:
        raise NewsError("chart for %s has no price" % ticker)
    when = meta.get("regularMarketTime")
    return {"ticker": ticker, "price": round(float(price), 2), "prev_close": round(float(prev), 2),
            "pct": round((float(price) / float(prev) - 1) * 100, 2),
            "time": _dt.datetime.fromtimestamp(when, _dt.timezone.utc) if when else None,
            "name": meta.get("longName") or meta.get("shortName")}


def collect_moves(fetcher, watch, errors=None):
    errors = [] if errors is None else errors
    out = []
    for t in sorted(watch):
        name = "chart-%s.json" % t
        try:
            try:
                data = fetcher.get(CHART % ("query2", yahoo_symbol(t)), name)
            except NewsError as exc:
                if fetcher.offline or not re.search(r"HTTP (429|5\d\d)", str(exc)):
                    raise
                data = fetcher.get(CHART % ("query1", yahoo_symbol(t)), name)
            out.append(parse_chart(data, t))
        except NewsError as exc:
            if not (fetcher.offline and "no file" in str(exc)):
                errors.append("price for %s: %s" % (t, exc))
    return out


def mention_patterns(watch):
    pats = {}
    for t, name in watch.items():
        b = re.escape(bare(t))
        alts = [r"\(%s\)" % b, r"\b(?:NSE|BSE)\s*:\s*%s\b" % b, r"\b%s\b" % re.escape(t)]
        if name and len(name) >= 4:
            short = re.sub(r"\s+(?:Limited|Ltd\.?)$", "", name.strip(), flags=re.I)
            alts.append(r"\b%s\b" % re.escape(short).replace(r"\ ", r"[\s-]"))
        pats[t] = re.compile("|".join(alts))
    return pats


def tag(items, watch):
    """Which of your stocks each item touches, and why it matters."""
    pats = mention_patterns(watch)
    for it in items:
        hay = "%s %s" % (it["title"], it.get("summary") or "")
        touches = {it["ticker"]} if it["ticker"] in watch else set()
        if it["kind"] == "headline":
            for t, rx in pats.items():
                if rx.search(hay):
                    touches.add(t)
        it["touches"] = sorted(touches)
        if it["kind"] == "announcement":
            subj = "%s %s" % (it.get("subject") or "", it.get("summary") or "")
            it["tags"] = [label for label, rx in NSE_SUBJECTS if re.search(rx, subj, re.I)]
            it["routine"] = bool(ROUTINE.search(it.get("subject") or ""))
            it["important"] = (not it["routine"]) and bool(set(it["tags"]) & IMPORTANT_TAGS)
        else:
            it["tags"] = [label for label, rx in KEYWORDS if re.search(rx, hay, re.I)]
    return items


def dedupe(items):
    """One item per story: same id, or same link, or same title the same day."""
    seen, out = {}, []
    for it in items:
        keys = [it["id"], it["link"],
                "%s|%s" % (re.sub(r"\W+", " ", it["title"].lower()).strip(),
                           it["time"].date() if it["time"] else "")]
        hit = next((seen[k] for k in keys if k and k in seen), None)
        if hit is not None:
            for t in it.get("touches", []):
                if t not in hit["touches"]:
                    hit["touches"].append(t)
            hit["touches"].sort()
            continue
        for k in keys:
            if k:
                seen[k] = it
        out.append(it)
    return out


def fingerprint(it):
    return hashlib.sha1(it["id"].encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- the check

def collect(fetcher, watch, names, nse=True, ann_file=None, errors=None):
    errors = [] if errors is None else errors
    items = []
    for t in sorted(watch):
        try:
            items += parse_rss(fetcher.get(YAHOO % yahoo_symbol(t), "yahoo-%s.xml" % t), t)
        except NewsError as exc:
            errors.append("headlines for %s: %s" % (t, exc))
    anns = []
    if nse:
        try:
            anns += parse_nse_rss(fetcher.get(NSE_FEED, "nse-announcements.xml"))
        except NewsError as exc:
            errors.append("NSE announcements unavailable (%s); showing Yahoo headlines only. For "
                          "announcements, download the CSV from %s and pass --announcements-file"
                          % (exc, NSE_PAGE))
    if ann_file:
        with open(ann_file, "rb") as fh:
            anns += parse_announcements_file(fh.read())
    items += match_announcements(anns, watch, names)
    return items


def select(items, state, now, hours=48, show_all=False, routine=False):
    """Items within the window; only unseen ones unless show_all; routine filings only with routine."""
    seen = state.setdefault("seen", {})
    cutoff = now - _dt.timedelta(hours=hours)
    ahead = now + _dt.timedelta(minutes=10)  # a pinned --now: nothing from after it
    out = []
    for it in items:
        if it["time"] is not None and (it["time"] < cutoff or it["time"] > ahead):
            continue
        if it.get("routine") and not routine:
            continue
        fp = fingerprint(it)
        it["new"] = fp not in seen
        if it["new"] or show_all:
            out.append(it)
    out.sort(key=lambda i: (i["time"] or now), reverse=True)
    return out


def remember(items, state, now):
    seen = state.setdefault("seen", {})
    stamp = now.isoformat()
    for it in items:
        seen.setdefault(fingerprint(it), stamp)
    cutoff = (now - _dt.timedelta(days=KEEP_DAYS)).isoformat()
    state["seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    state["last_check"] = stamp


def render_moves(moves, move_pct=MOVE_PCT):
    out = []
    for m in moves:
        flag = "MOVING" if abs(m["pct"]) >= move_pct else ""
        when = (" at %s IST" % to_ist(m["time"]).strftime("%H:%M")) if m.get("time") else ""
        out.append("%-12s ₹%s %+.2f%% vs yesterday's close ₹%s%s  %s" % (
            m["ticker"], "{:,.2f}".format(m["price"]), m["pct"], "{:,.2f}".format(m["prev_close"]),
            when, flag))
    return out


def render(items, errors, now, moves=None, move_pct=MOVE_PCT):
    out = render_moves(moves or [], move_pct)
    if out:
        out.append("")
    if not items:
        out.append("Nothing new.")
    for it in items:
        when = to_ist(it["time"]).strftime("%Y-%m-%d %H:%M IST") if it["time"] else "time unknown"
        mark = "!" if it.get("important") else " "
        who = ",".join(it["touches"]) or it["ticker"]
        out.append("%s %s  %-12s %-13s %s" % (mark, when, who, it["source"], it["title"]))
        extra = []
        if it["tags"]:
            extra.append("tags: " + ", ".join(it["tags"]))
        extra.append(it["link"])
        out.append("      " + "  ".join(extra))
    for e in errors:
        out.append("note: %s" % e)
    out.append("Checked %s. Headlines are leads, not facts; open the story or the filing before "
               "acting. Not financial advice." % to_ist(now).strftime("%Y-%m-%d %H:%M IST"))
    return "\n".join(out) + "\n"


def _json_ready(items):
    out = []
    for it in items:
        d = dict(it)
        d["time"] = it["time"].isoformat() if it["time"] else None
        d["time_ist"] = ist_iso(it["time"]) if it["time"] else None
        out.append(d)
    return out


def main(argv=None):
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tickers", nargs="*", help="default: watchlist + plan + portfolio")
    ap.add_argument("--portfolio", default="trader/portfolio.json")
    ap.add_argument("--watchlist", default="trader/watchlist.json")
    ap.add_argument("--plan", default="trader/plan.json")
    ap.add_argument("--universe", default="trader/universe.txt",
                    help="SYMBOL.NS|Name lines, to match announcements by company name")
    ap.add_argument("--state", default="trader/state/news.json")
    ap.add_argument("--hours", type=float, default=48, help="ignore items older than this")
    ap.add_argument("--all", action="store_true", help="show seen items too")
    ap.add_argument("--routine", action="store_true",
                    help="also show routine filings (newspaper copies, NAVs, trading-window notices)")
    ap.add_argument("--no-nse", action="store_true", help="headlines only, no NSE announcements")
    ap.add_argument("--announcements-file", metavar="PATH",
                    help="announcements CSV/JSON saved from " + NSE_PAGE)
    ap.add_argument("--no-moves", action="store_true", help="skip the price line per stock")
    ap.add_argument("--move-pct", type=float, default=MOVE_PCT, help="flag a move this big (percent)")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--now", help="ISO time (IST if no offset), for reproducible runs")
    ap.add_argument("--dry-run", action="store_true", help="do not update the state file")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.tickers:
        watch = {nse_symbol(t): None for t in args.tickers}
        known = watch_from_files(args.portfolio, args.watchlist, args.plan)
        watch.update({t: known[t] for t in watch if known.get(t)})
    else:
        watch = watch_from_files(args.portfolio, args.watchlist, args.plan)
    if not watch:
        print("news: no symbols. Pass --tickers, or run /trader-scanner first (it writes "
              "trader/watchlist.json).", file=sys.stderr)
        return 2
    now = parse_time(args.now) if args.now else _dt.datetime.now(_dt.timezone.utc)
    if now is None:
        print("news: --now wants a time like \"2026-11-06 11:45\" (IST)", file=sys.stderr)
        return 2
    state = load_json(args.state) or {}
    errors = []
    fetcher = Fetcher(offline=args.offline)
    moves = [] if args.no_moves else collect_moves(fetcher, watch, errors)
    names = universe_names(args.universe)
    for m in moves:
        names.setdefault(m["ticker"], m.get("name"))
    try:
        items = collect(fetcher, watch, names, nse=not args.no_nse,
                        ann_file=args.announcements_file, errors=errors)
    except (NewsError, OSError) as exc:
        print("news: %s" % exc, file=sys.stderr)
        return 2
    items = dedupe(tag(items, watch))
    shown = select(items, state, now, hours=args.hours, show_all=args.all, routine=args.routine)
    if args.json:
        mv = [dict(m, time=m["time"].isoformat() if m.get("time") else None,
                   moving=abs(m["pct"]) >= args.move_pct) for m in moves]
        print(json.dumps({"checked": now.isoformat(), "checked_ist": ist_iso(now), "moves": mv,
                          "items": _json_ready(shown), "notes": errors}, indent=2, ensure_ascii=False))
    else:
        sys.stdout.write(render(shown, errors, now, moves, args.move_pct))
    if not args.dry_run:
        remember(items, state, now)
        folder = os.path.dirname(args.state)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.state, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
            fh.write("\n")
    return 0 if any(i["new"] for i in shown) else 1


if __name__ == "__main__":
    sys.exit(main())

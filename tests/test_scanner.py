import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX

scan = load("trader-scanner", "scan")

SDIR = os.path.join(FIX, "trader-scanner")
# All real data, saved on Fri Nov 6 2026 (IST):
WALL = os.path.join(SDIR, "wall5m-2026-11-06-1107.json.gz")        # 101 v8 5-min charts, fetched 11:07 IST
DAILY = os.path.join(SDIR, "daily-spark-2026-11-06-1111.json")     # one 20-symbol spark answer, 3mo / 1d
SPARK5 = os.path.join(SDIR, "spark-5m-live-2026-11-06-1108.json")  # one 20-symbol spark answer, 1d / 5m
REL5 = os.path.join(SDIR, "RELIANCE.NS-5m-live-2026-11-06-1108.json")  # RELIANCE.NS v8, fetched with it
EQUITY_L = os.path.join(SDIR, "EQUITY_L-excerpt-2026-11-06.csv")   # 11 real rows of nsearchives EQUITY_L.csv
NIFTY50 = os.path.join(SDIR, "ind_nifty50list-2026-11-06.csv")     # nsearchives ind_nifty50list.csv, whole
DAILY_SYMS = ["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "M&M.NS", "BAJAJ-AUTO.NS", "BAJFINANCE.NS",
              "EICHERMOT.NS", "AXISBANK.NS", "CANBK.NS", "BIOCON.NS", "APTUS.NS", "AIAENG.NS", "ATUL.NS",
              "ANANTRAJ.NS", "CLEAN.NS", "BSOFT.NS", "DRREDDY.NS", "ATHERENERG.NS", "ZZZZ.NS"]  # as asked
UNTIL = datetime.time(14, 45)
DT = datetime.datetime
D = datetime.date


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = scan.main(argv)
    return code, out.getvalue(), err.getvalue()


def ts(ist_text):
    """India wall-clock text -> unix seconds."""
    utc = scan.ist_to_utc(DT.strptime(ist_text, "%Y-%m-%d %H:%M:%S" if ist_text.count(":") == 2
                                      else "%Y-%m-%d %H:%M"))
    return int((utc - DT(1970, 1, 1)).total_seconds())


def bars_series(day, closes, highs=None, lows=None, start="09:15", extra=None):
    """A v8-like series of 5-min bars from `start` (IST) on `day` (YYYY-MM-DD). Synthetic, for edge cases."""
    t0 = DT.strptime(day + " " + start, "%Y-%m-%d %H:%M")
    t = [ts((t0 + datetime.timedelta(minutes=5 * i)).strftime("%Y-%m-%d %H:%M")) for i in range(len(closes))]
    s = {"t": t, "c": list(closes), "h": list(highs or [c + 0.1 if c is not None else None for c in closes]),
         "l": list(lows or [c - 0.1 if c is not None else None for c in closes]), "o": None,
         "v": [1000] * len(closes), "meta": {}}
    for when, c in (extra or []):
        s["t"].append(ts(when))
        for k in ("c", "h", "l"):
            s[k].append(c)
        s["v"].append(10)
    return s


def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class Universe(unittest.TestCase):
    def test_equity_list_keeps_series_eq_only(self):
        text = read_text(EQUITY_L)
        self.assertTrue(text.startswith("SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING"))  # NSE pads headers
        kept, counts = scan.parse_nse_csv(text)
        self.assertEqual([s for s, _ in kept],
                         ["20MICRONS.NS", "3MINDIA.NS", "BAJAJ-AUTO.NS", "HDFCBANK.NS", "INFY.NS", "M&M.NS",
                          "M&MFIN.NS", "RELIANCE.NS", "TCS.NS"])
        self.assertEqual(counts, {"rows": 11, "series BE": 1, "series BZ": 1})
        names = dict(kept)
        self.assertNotIn("21STCENMGM.NS", names)        # series BE: trade-for-trade, no intraday
        self.assertNotIn("AGSTRA.NS", names)            # series BZ
        self.assertEqual(names["M&M.NS"], "Mahindra & Mahindra Limited")
        self.assertEqual(names["RELIANCE.NS"], "Reliance Industries Limited")

    def test_nifty_index_list(self):
        kept, counts = scan.parse_nse_csv(read_text(NIFTY50))
        self.assertEqual(len(kept), 50)
        self.assertEqual(counts, {"rows": 50})
        names = dict(kept)
        self.assertEqual(names["M&M.NS"], "Mahindra & Mahindra Ltd.")
        self.assertEqual(names["BAJAJ-AUTO.NS"], "Bajaj Auto Ltd.")
        self.assertTrue(all(s.endswith(".NS") for s, _ in kept))

    def test_header_spaces_bom_case_and_padding(self):
        text = ("﻿ Symbol ,  Company Name , SERIES \n"
                " RELIANCE , Reliance Industries Ltd. , EQ \n"
                "reliance,dup,EQ\n"
                "ABC,A | B Ltd,eq\n"
                "XYZ,Some Ltd,BE\n"
                "BAD SYM,Space Ltd,EQ\n"
                "NOSER,No Series Ltd,\n")
        kept, counts = scan.parse_nse_csv(text)
        self.assertEqual(kept, [("ABC.NS", "A B Ltd"), ("RELIANCE.NS", "Reliance Industries Ltd.")])
        self.assertEqual(counts, {"rows": 6, "duplicate": 1, "series BE": 1, "not a plain symbol": 1,
                                  "series blank": 1})

    def test_not_an_nse_list(self):
        for junk in ("<html>Access Denied</html>", "", "Symbol,Name\nRELIANCE,Reliance\n"):
            with self.assertRaises(scan.ScanError) as cm:
                scan.parse_nse_csv(junk)
            self.assertIn("--file", str(cm.exception))

    def test_lists_point_at_nsearchives(self):
        self.assertEqual(sorted(scan.NSE_LISTS), ["all", "nifty100", "nifty200", "nifty50", "nifty500"])
        for url in scan.NSE_LISTS.values():
            self.assertTrue(url.startswith("https://nsearchives.nseindia.com/content/"), url)
        self.assertTrue(scan.NSE_LISTS["all"].endswith("/equities/EQUITY_L.csv"))


class Symbols(unittest.TestCase):
    def test_ns_is_added(self):
        self.assertEqual(scan.nse_symbol("reliance"), "RELIANCE.NS")
        self.assertEqual(scan.nse_symbol(" RELIANCE.NS "), "RELIANCE.NS")
        self.assertEqual(scan.nse_symbol("TCS.BO"), "TCS.BO")
        self.assertEqual(scan.nse_symbol("^NSEI"), "^NSEI")
        self.assertEqual(scan.nse_symbol("m&m"), "M&M.NS")

    def test_m_and_m_is_url_encoded(self):
        self.assertEqual(scan.yahoo_symbol("M&M"), "M%26M.NS")
        self.assertEqual(scan.yahoo_symbol("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")
        self.assertEqual(scan.spark_path(["M&M", "bajaj-auto.ns", "RELIANCE"], "3mo", "1d"),
                         "/v7/finance/spark?symbols=M%26M.NS,BAJAJ-AUTO.NS,RELIANCE.NS&range=3mo&interval=1d")
        self.assertEqual(scan.chart_path("M&M.NS", "1d", "5m"), "/v8/finance/chart/M%26M.NS?range=1d&interval=5m")
        self.assertEqual(scan.chart_path("RELIANCE", "1d", "5m"),
                         "/v8/finance/chart/RELIANCE.NS?range=1d&interval=5m")

    def test_symbol_regex(self):
        for ok in ("M&M.NS", "BAJAJ-AUTO.NS", "3MINDIA.NS", "MCDOWELL-N.NS", "RELIANCE", "TCS.BO"):
            self.assertTrue(scan.SYMBOL_RE.match(ok), ok)
        for bad in ("BRK.B.NS", "RELIANCE.XX", "A B.NS", "^NSEI", "X" * 21):
            self.assertFalse(scan.SYMBOL_RE.match(bad), bad)


class Time(unittest.TestCase):
    def test_ist_is_utc_plus_five_thirty_all_year(self):
        self.assertEqual(scan.IST_OFFSET, datetime.timedelta(hours=5, minutes=30))
        self.assertEqual(scan.parse_now("2026-11-06 10:00"), DT(2026, 11, 6, 4, 30))
        self.assertEqual(scan.parse_now("2026-01-15 09:15"), DT(2026, 1, 15, 3, 45))   # no daylight saving
        self.assertEqual(scan.parse_now("2026-07-15 09:15"), DT(2026, 7, 15, 3, 45))
        self.assertEqual(scan.parse_now("2026-11-06T04:30:00Z"), DT(2026, 11, 6, 4, 30))
        self.assertEqual(scan.parse_now("2026-11-06T10:00:00+05:30"), DT(2026, 11, 6, 4, 30))
        self.assertEqual(scan.ist_iso(DT(2026, 11, 6, 4, 45)), "2026-11-06T10:15:00+05:30")
        self.assertEqual(scan.to_ist(DT(2026, 11, 6, 3, 45)), DT(2026, 11, 6, 9, 15))
        self.assertEqual(scan.ts_to_ist(1793936700), DT(2026, 11, 6, 9, 15))    # Yahoo's first NSE bar, 03:45 UTC
        self.assertEqual(scan.clock_label(DT(2026, 11, 6, 9, 15)), "9:15 AM")
        self.assertEqual(scan.clock_label(datetime.time(15, 30)), "3:30 PM")
        with self.assertRaises(scan.ScanError):
            scan.parse_now("tomorrow")
        with self.assertRaises(scan.ScanError) as cm:
            scan.parse_hhmm("25:00")
        self.assertIn("IST", str(cm.exception))

    def test_session_and_holidays(self):
        self.assertEqual((scan.OPEN, scan.CLOSE), (datetime.time(9, 15), datetime.time(15, 30)))
        self.assertEqual(scan.session(D(2026, 11, 6)), (datetime.time(9, 15), datetime.time(15, 30)))
        self.assertEqual(scan.trading_day(D(2026, 11, 10)), (False, "NSE holiday: Diwali Balipratipada"))
        self.assertEqual(scan.trading_day(D(2026, 10, 20)), (False, "NSE holiday: Dussehra"))
        self.assertEqual(scan.trading_day(D(2026, 11, 8)), (False, "weekend"))   # Muhurat Sunday: closed
        self.assertIsNone(scan.session(D(2026, 12, 25)))
        self.assertEqual(scan.session(D(2026, 12, 24)), (datetime.time(9, 15), datetime.time(15, 30)))  # no early close
        self.assertFalse(hasattr(scan, "NYSE_HOLIDAYS"))
        self.assertFalse(hasattr(scan, "EARLY"))

    def test_holidays_from_market_file_and_patch(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "market_india.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"holidays": {"2027-01-26": "Republic Day"}}, fh)
            h = scan.load_holidays(p)
            self.assertEqual(h["2027-01-26"], "Republic Day")
            self.assertEqual(h["2026-11-10"], "Diwali Balipratipada")
            self.assertEqual(scan.load_holidays(os.path.join(d, "missing.json")), scan.NSE_HOLIDAYS)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("not json")
            self.assertEqual(scan.load_holidays(p), scan.NSE_HOLIDAYS)
        finally:
            shutil.rmtree(d)
        saved = dict(scan.NSE_HOLIDAYS)
        scan.NSE_HOLIDAYS["2026-11-06"] = "Test Holiday"
        try:
            with self.assertRaises(scan.ScanError) as cm:
                scan.orb_window(scan.parse_now("2026-11-06 10:00"), None, 15, UNTIL)
            self.assertIn("NSE holiday: Test Holiday", str(cm.exception))
        finally:
            scan.NSE_HOLIDAYS.clear()
            scan.NSE_HOLIDAYS.update(saved)

    def test_real_daily_bars_skip_the_holidays(self):
        data, _ = scan.load_offline(DAILY)
        days = {scan.ts_to_ist(t).date() for s in data.values() for t in s["t"]}
        self.assertNotIn(D(2026, 10, 2), days)    # Gandhi Jayanti
        self.assertNotIn(D(2026, 10, 20), days)   # Dussehra
        self.assertTrue(all(scan.trading_day(d)[0] for d in days))
        self.assertTrue(all(scan.ts_to_ist(t).time() == datetime.time(9, 15) for t in data["TCS.NS"]["t"]))


class Spark(unittest.TestCase):
    def test_a_real_spark_answer(self):
        data = scan.parse_spark(read_text(DAILY))
        self.assertEqual(len(data), 19)                # 20 asked; Yahoo silently drops the made-up ZZZZ.NS
        self.assertEqual(sorted(data), sorted(DAILY_SYMS[:-1]))
        self.assertIn("M&M.NS", data)                  # Yahoo names it unquoted
        r = data["RELIANCE.NS"]
        self.assertEqual(len(r["t"]), 64)
        self.assertIsNone(r["h"])                      # spark is closes only
        self.assertEqual(r["meta"]["currency"], "INR")
        self.assertEqual(r["meta"]["regularMarketVolume"], 8925541)   # the day so far at 11:11 IST
        self.assertEqual(data["TCS.NS"]["meta"]["regularMarketPrice"], 2508.8)
        # read before the open: Friday's bar is today's, so the last complete close is Thursday's
        closes = scan.daily_closes(r, scan.parse_now("2026-11-06 09:00"))
        self.assertEqual(closes[-1], (D(2026, 11, 5), 1313.4))
        closes = scan.daily_closes(r, scan.parse_now("2026-11-06 15:29"))
        self.assertEqual(closes[-1][0], D(2026, 11, 5))
        closes = scan.daily_closes(r, scan.parse_now("2026-11-06 15:30"))    # the 3:30 PM close
        self.assertEqual(closes[-1][0], D(2026, 11, 6))

    def test_spark_errors(self):
        too_many = '{"spark":{"result":null,"error":{"code":"Bad Request",' \
                   '"description":"Number of symbols needs to be less than or equal to 20"}}}'
        with self.assertRaises(scan.ScanError) as cm:
            scan.parse_spark(too_many)
        self.assertIn("less than or equal to 20", str(cm.exception))
        self.assertEqual(scan.parse_spark('{"spark":{"result":[],"error":null}}'), {})
        with self.assertRaises(scan.ScanError):
            scan.parse_spark('{"chart":{}}')


class Network(unittest.TestCase):
    """429s and 5xx without the network."""

    def setUp(self):
        self.calls, self.urls, self.sleeps = [], [], []
        self._get, self._sleep = scan.http_get, scan.SLEEP
        scan.SLEEP = self.sleeps.append
        self.body = read_text(DAILY)

    def tearDown(self):
        scan.http_get, scan.SLEEP = self._get, self._sleep

    def answers(self, codes, body=None):
        codes = list(codes)

        def fake(url, timeout=20):
            self.urls.append(url)
            self.calls.append(url.split("/v")[0])
            code = codes.pop(0) if codes else 200
            if code != 200:
                raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))
            return self.body if body is None else body
        scan.http_get = fake

    def test_429_falls_back_then_waits_once(self):
        self.answers([429, 429, 200])
        text = scan.yahoo_get("/v7/finance/spark?symbols=RELIANCE.NS&range=3mo&interval=1d")
        self.assertEqual(text, self.body)
        self.assertEqual(self.calls, ["https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com",
                                      "https://query2.finance.yahoo.com"])
        self.assertEqual(self.sleeps, [scan.BACKOFF])

    def test_5xx_goes_to_query1_without_waiting(self):
        self.answers([503, 200])
        scan.yahoo_get("/v8/finance/chart/RELIANCE.NS?range=1d&interval=5m")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.sleeps, [])

    def test_429_for_good_is_reported_as_partial(self):
        self.answers([200] + [429] * 10)
        syms = DAILY_SYMS + ["X%02d.NS" % i for i in range(20)]
        data, failed = scan.fetch_spark(syms, "3mo", "1d", 0.3)
        self.assertEqual(len(data), 19)
        self.assertIn("M&M.NS", data)
        self.assertIn("symbols=RELIANCE.NS,TCS.NS,INFY.NS,HDFCBANK.NS,M%26M.NS,BAJAJ-AUTO.NS", self.urls[0])
        self.assertEqual(failed["ZZZZ.NS"], "no data from Yahoo")
        self.assertEqual(len(failed), 21)
        self.assertIn("HTTP 429", failed["X00.NS"])
        self.assertEqual(self.sleeps, [0.3, scan.BACKOFF])  # a pause between batches, one backoff
        hits, counts, _ = scan.scan_daily(data, syms, scan.parse_now("2026-11-06 09:00"), 50, 200000)
        self.assertEqual(counts["requested"], 40)
        self.assertEqual(counts["fetched"], 19)

    def test_a_404_is_final(self):
        self.answers([404])
        with self.assertRaises(scan.ScanError):
            scan.yahoo_get("/v8/finance/chart/NOPE.NS?range=1d&interval=5m")
        self.assertEqual(len(self.calls), 1)

    def test_universe_download(self):
        d = tempfile.mkdtemp()
        try:
            out = os.path.join(d, "universe.txt")
            self.answers([200], body=read_text(NIFTY50))
            code, text, _ = run(["universe", "--list", "nifty50", "--out", out])
            self.assertEqual(code, 0)
            self.assertEqual(self.urls, ["https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv"])
            self.assertIn("Kept 50 NSE equities (series EQ).", text)
            lines = read_text(out).splitlines()
            self.assertTrue(lines[0].startswith("# NSE equities (EQ series)"))
            self.assertEqual(lines[1], "# source: " + scan.NSE_LISTS["nifty50"])
            self.assertEqual(len(lines), 52)
            self.assertIn("M&M.NS|Mahindra & Mahindra Ltd.", lines)
            # NSE refuses: a plain line that says to save it from a browser, and nothing is written
            os.remove(out)
            self.urls[:] = []
            self.answers([403])
            code, _, err = run(["universe", "--out", out])
            self.assertEqual(code, 1)
            self.assertEqual(self.urls, [scan.NSE_LISTS["nifty500"]])   # nifty500 is the default
            self.assertIn("HTTP 403", err)
            self.assertIn("--file PATH", err)
            self.assertFalse(os.path.exists(out))
        finally:
            shutil.rmtree(d)


class Daily(unittest.TestCase):
    # Hand-checked from the closes, through Thu Nov 5 2026 (two charts end Wed Nov 4: Yahoo had no Nov 5 bar).
    EXPECT = {
        "RELIANCE.NS": ["breakout_20d"],     # 1313.40 > 1311.00, the best close of the 20 before
        "HDFCBANK.NS": ["breakout_20d"],     # 760.00 > 736.70
        "M&M.NS": ["breakout_20d"],          # 3127.50 > 2961.90
        "BAJFINANCE.NS": ["breakout_20d"],   # 1070.00 > 994.00
        "EICHERMOT.NS": ["breakout_20d"],    # 7713.00 > 7440.00
        "BAJAJ-AUTO.NS": ["breakout_20d"],   # 10608 > 10540 (through Nov 4)
        "BIOCON.NS": ["breakout_20d", "reclaim_ma20"],   # 362.00 > 360.00; 344.45 under its average, then over 344.86
        "AXISBANK.NS": ["reclaim_ma20"],     # 1232.00 under its average, then 1280.00 over 1239.25
        "CANBK.NS": ["reclaim_ma20"],        # 118.32 under, then 122.90 over 119.17
        "BSOFT.NS": ["bull_flag"],           # up 14.6 % over the 10 sessions, last 5 closes in a 3.3 % range
        "ATHERENERG.NS": ["bull_flag"],      # up 13.4 %, 4.7 % range
        "DRREDDY.NS": ["bull_flag"],         # up 10.6 %, 2.3 % range
        "APTUS.NS": ["oversold"],            # RSI 28.9 (206.28 -> 179.93)
        "AIAENG.NS": ["oversold"],           # RSI 18.5
        "ATUL.NS": ["oversold", "oversold_bounce"],      # RSI 23.4 then 28.5, closed up 5073 -> 5156.50
        "ANANTRAJ.NS": ["oversold_bounce"],  # RSI 29.8 on Wednesday, closed up Thursday (460.40 -> 466.00)
        "CLEAN.NS": ["oversold_bounce"],     # RSI 25.8, then 656.40 -> 678.70
    }

    def setUp(self):
        self.data, self.errors = scan.load_offline(DAILY)
        self.syms = sorted(self.data)

    def test_setups_before_the_open(self):
        self.assertEqual(len(self.syms), 19)
        hits, counts, last = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-11-06 09:00"), 0, 0)
        self.assertEqual({h["symbol"]: h["setups"] for h in hits}, self.EXPECT)
        self.assertEqual(last, D(2026, 11, 5))
        self.assertEqual(counts, {"requested": 19, "fetched": 19, "illiquid": 0, "short": 0, "scanned": 19,
                                  "hits": 17})
        rel = [h for h in hits if h["symbol"] == "RELIANCE.NS"][0]
        self.assertEqual((rel["price"], rel["rsi"], rel["close_date"]), (1313.4, 67.3, "2026-11-05"))
        self.assertEqual(hits[0]["symbol"], "HDFCBANK.NS")   # most rupees traded first

    def test_the_in_progress_bar_is_dropped(self):
        before = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-11-06 09:00"), 0, 0)
        during = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-11-06 11:00"), 0, 0)
        self.assertEqual(before, during)
        bars = scan.daily_closes(self.data["INFY.NS"], scan.parse_now("2026-11-06 15:29"))
        self.assertEqual(bars[-1][0], D(2026, 11, 5))
        bars = scan.daily_closes(self.data["INFY.NS"], scan.parse_now("2026-11-06 15:30"))
        self.assertEqual(bars[-1], (D(2026, 11, 6), 1170.9))

    def test_liquidity_filter(self):
        # meta is Yahoo's as of 11:11 AM IST, so the volume is the day's so far
        hits, counts, _ = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-11-06 11:11"), 50, 200000)
        self.assertEqual(counts["illiquid"], 4)     # AIAENG, ATUL, CLEAN, EICHERMOT under 200,000 shares so far
        self.assertEqual(counts["scanned"], 15)
        self.assertEqual(sorted(h["symbol"] for h in hits),
                         ["ANANTRAJ.NS", "APTUS.NS", "ATHERENERG.NS", "AXISBANK.NS", "BAJAJ-AUTO.NS",
                          "BAJFINANCE.NS", "BIOCON.NS", "BSOFT.NS", "CANBK.NS", "DRREDDY.NS", "HDFCBANK.NS",
                          "M&M.NS", "RELIANCE.NS"])
        hits, counts, _ = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-11-06 09:00"), 150, 0)
        self.assertEqual(counts["illiquid"], 1)     # CANBK at 121.79 under a 150 floor

    def test_short_and_empty_charts(self):
        s = {"t": [ts("2026-10-%02d 09:15" % d) for d in range(5, 15)], "c": [100.0] * 10,
             "meta": {"regularMarketPrice": 100, "regularMarketVolume": 5000000}}
        hits, counts, last = scan.scan_daily({"X.NS": s, "E.NS": {"t": [], "c": [], "meta": {}}},
                                             ["X.NS", "E.NS", "GONE.NS"], scan.parse_now("2026-11-06 09:00"),
                                             50, 200000)
        self.assertEqual(hits, [])
        self.assertEqual((counts["fetched"], counts["short"], counts["illiquid"], counts["scanned"]), (2, 1, 1, 0))
        self.assertIsNone(last)

    def test_rsi_matches_the_reel_math(self):
        self.assertIsNone(scan.rsi([1.0] * 10))
        self.assertEqual(scan.rsi([float(x) for x in range(1, 40)]), 100.0)
        closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61,
                  46.28, 46.28]
        self.assertAlmostEqual(scan.rsi(closes), 70.46, places=1)  # Wilder's textbook example


class Orb(unittest.TestCase):
    RELIANCE_HIT = {"symbol": "RELIANCE.NS", "setups": ["orb"], "price": 1311.9, "at": "9:50 AM",
                    "bar_start": "09:45", "bar_index": 6, "or_high": 1306.0, "or_low": 1295.6, "pct_over": 0.45}
    MM_HIT = {"symbol": "M&M.NS", "setups": ["orb"], "price": 3241.9, "at": "9:35 AM", "bar_start": "09:30",
              "bar_index": 3, "or_high": 3226.5, "or_low": 3133.9, "pct_over": 0.48}

    @classmethod
    def setUpClass(cls):
        cls.data, cls.errors = scan.load_offline(WALL)
        cls.syms = sorted(set(cls.data) | set(cls.errors))

    def scan_at(self, now, min_price=0, syms=None, or_minutes=15):
        day, cutoff = scan.orb_window(scan.parse_now(now), None, or_minutes, UNTIL)
        return scan.scan_orb(self.data, syms or self.syms, day, cutoff, or_minutes, min_price, 0)

    def test_the_scan_at_ten(self):
        hits, counts, failed = self.scan_at("2026-11-06 10:00")
        self.assertEqual(len(self.syms), 101)
        self.assertEqual(self.errors, {"ZZZZ.NS": "no data for ZZZZ.NS: No data found, symbol may be delisted"})
        self.assertEqual((counts["fetched"], counts["unusable"], counts["scanned"]), (100, 0, 100))
        self.assertEqual(counts["candidates"], 76)
        self.assertEqual(len(hits), 47)
        self.assertIn(self.RELIANCE_HIT, hits)
        self.assertIn(self.MM_HIT, hits)
        self.assertEqual(hits[0]["symbol"], "AAVAS.NS")   # sorted by time, then symbol
        self.assertEqual(sum(1 for h in hits if h["at"] == "9:35 AM"), 11)   # the first bar after the range
        self.assertEqual(hits[-1]["at"], "10:00 AM")
        self.assertTrue(all(h["symbol"].endswith(".NS") for h in hits))

    def test_more_breakouts_as_the_morning_goes_on(self):
        self.assertEqual(len(self.scan_at("2026-11-06 09:35")[0]), 11)
        self.assertEqual(len(self.scan_at("2026-11-06 09:45")[0]), 26)
        self.assertEqual(len(self.scan_at("2026-11-06 11:05")[0]), 54)

    def test_price_floor(self):
        hits, counts, _ = self.scan_at("2026-11-06 10:00", min_price=200)
        self.assertEqual(counts["illiquid"], 4)          # AWL, ASHOKLEY, APTUS, TATASTEEL under 200 at 10:00
        self.assertEqual(len(hits), 46)
        self.assertNotIn("ASHOKLEY.NS", [h["symbol"] for h in hits])   # 167.91, a hit without the floor
        hits, counts, _ = self.scan_at("2026-11-06 10:00", min_price=50)   # the default: nothing in this wall is under 50
        self.assertEqual((counts["illiquid"], len(hits)), (0, 47))

    def test_in_progress_bar_and_no_breakout_yet(self):
        hits, _, _ = self.scan_at("2026-11-06 09:49", syms=["RELIANCE.NS"])
        self.assertEqual(hits, [])                         # the 9:45 bar is not done until 9:50
        hits, _, _ = self.scan_at("2026-11-06 09:50", syms=["RELIANCE.NS"])
        self.assertEqual(hits[0]["at"], "9:50 AM")

    def test_the_ist_grid_and_the_live_tick(self):
        s = self.data["RELIANCE.NS"]
        self.assertEqual(scan.ts_to_ist(s["t"][0]), DT(2026, 11, 6, 9, 15))
        self.assertEqual(scan.ts_to_ist(s["t"][-1]), DT(2026, 11, 6, 11, 7, 49))   # off the grid
        bars = scan.session_bars(s, D(2026, 11, 6), DT(2026, 11, 6, 11, 8))
        self.assertEqual(len(bars), 22)                    # 9:15 to 11:00; 11:05 in progress, the tick dropped
        self.assertEqual(bars[0]["start"], DT(2026, 11, 6, 9, 15))
        self.assertTrue(all(b["start"].minute % 5 == 0 for b in bars))
        self.assertTrue(scan.usable(bars, D(2026, 11, 6), DT(2026, 11, 6, 11, 8), 3))
        self.assertEqual(scan.slot(D(2026, 11, 6), 3), DT(2026, 11, 6, 9, 30))
        hits, _, _ = self.scan_at("2026-11-06 11:07", syms=["RELIANCE.NS"])
        self.assertEqual(hits[0]["price"], 1311.9)         # still the first close above the range

    def test_stage_one_is_a_necessary_condition(self):
        day, cut = D(2026, 11, 6), DT(2026, 11, 6, 11, 5)
        for sym, s in self.data.items():
            bars = scan.session_bars(s, day, cut)
            if scan.usable(bars, day, cut, 3) and scan.orb_hit(bars, 3):
                self.assertTrue(scan.orb_candidate(bars, 3), sym)

    def test_confirm_uses_the_v8_highs(self):
        spark_like = {k: dict(v, h=None, l=None) for k, v in self.data.items()}  # closes only, like spark
        asked = []

        def confirm(sym):
            asked.append(sym)
            return self.data[sym]
        hits, counts, failed = scan.scan_orb(spark_like, self.syms, D(2026, 11, 6), DT(2026, 11, 6, 10, 0), 15,
                                             0, 0, confirm)
        self.assertEqual(len(hits), 47)
        self.assertEqual(counts["candidates"], len(asked))
        self.assertLess(len(asked), 100)                   # stage 1 skipped some charts

        def broken(sym):
            raise scan.ScanError("Yahoo answered HTTP 429 for /v8/finance/chart/%s" % sym)
        hits, counts, failed = scan.scan_orb(spark_like, ["RELIANCE.NS", "M&M.NS"], D(2026, 11, 6),
                                             DT(2026, 11, 6, 10, 0), 15, 0, 0, broken)
        self.assertEqual(hits, [])
        self.assertIn("429", failed["RELIANCE.NS"])

    def test_a_missing_bar_makes_a_chart_unusable(self):
        # synthetic: no halted chart in the real wall
        s = bars_series("2026-11-06", [100, 100.2, 100.1, None, 100.5, 100.6, 100.7, 100.8, 100.9])
        bars = scan.session_bars(s, D(2026, 11, 6), DT(2026, 11, 6, 10, 0))
        self.assertFalse(scan.usable(bars, D(2026, 11, 6), DT(2026, 11, 6, 10, 0), 3))
        # and a real chart with one bar cut out
        real = dict(self.data["RELIANCE.NS"])
        real["t"] = real["t"][:4] + real["t"][5:]
        real["c"] = real["c"][:4] + real["c"][5:]
        hits, counts, _ = scan.scan_orb({"RELIANCE.NS": real}, ["RELIANCE.NS"], D(2026, 11, 6),
                                        DT(2026, 11, 6, 10, 0), 15)
        self.assertEqual((counts["unusable"], hits), (1, []))

    def test_a_30_minute_range_and_the_close(self):
        # synthetic: a full 75-bar session (9:15 to 3:25) and the bars after 3:30 dropped
        closes = [100, 101, 100.5, 100.8, 101.2, 100.9, 101.5] + [101.0] * 68
        s = bars_series("2026-11-05", closes, highs=[c + 0.2 for c in closes],
                        extra=[("2026-11-05 15:30", 99.0), ("2026-11-05 15:40", 98.0)])
        hits, counts, _ = scan.scan_orb({"X.NS": s}, ["X.NS"], D(2026, 11, 5), DT(2026, 11, 5, 12, 0), 30, 0, 0)
        self.assertEqual(counts["scanned"], 1)
        self.assertEqual(hits[0]["at"], "9:50 AM")          # range 9:15 to 9:45 (high 101.4), 9:45 bar 101.5
        self.assertEqual(hits[0]["or_high"], 101.4)
        bars = scan.session_bars(s, D(2026, 11, 5), DT(2026, 11, 5, 18, 0))
        self.assertEqual(len(bars), 75)                    # 375 minutes, no closing-session prints
        self.assertTrue(scan.usable(bars, D(2026, 11, 5), DT(2026, 11, 5, 18, 0), 6))
        with self.assertRaises(scan.ScanError) as cm:
            scan.orb_window(scan.parse_now("2026-11-05 15:40"), None, 15, datetime.time(16, 0))
        self.assertIn("closed at 3:30 PM IST", str(cm.exception))

    def test_window_messages(self):
        def msg(now, at=None, until=UNTIL):
            with self.assertRaises(scan.ScanError) as cm:
                scan.orb_window(scan.parse_now(now), at, 15, until)
            return str(cm.exception)
        self.assertIn("weekend", msg("2026-11-07 10:00"))
        self.assertIn("Diwali Balipratipada", msg("2026-11-10 10:00"))
        m = msg("2026-11-06 09:25")
        self.assertIn("runs 9:15 AM to 9:30 AM", m)
        self.assertIn("Run orb again after 9:35 AM", m)
        self.assertIn("Run orb again after 9:35 AM", msg("2026-11-06 09:34"))
        self.assertIn("expired", msg("2026-11-06 14:50"))           # after the default 14:45
        self.assertIn("expired", msg("2026-11-06 11:30", until=datetime.time(11, 0)))
        self.assertIn("closed at 3:30 PM IST", msg("2026-11-06 15:45", until=datetime.time(16, 0)))
        self.assertIn("later than now", msg("2026-11-06 09:40", at=datetime.time(9, 45)))
        self.assertIn("after the 3:30 PM close", msg("2026-11-06 17:00", at=datetime.time(15, 45)))
        self.assertEqual(scan.orb_window(scan.parse_now("2026-11-06 16:30"), datetime.time(9, 45), 15, UNTIL),
                         (D(2026, 11, 6), DT(2026, 11, 6, 9, 45)))
        self.assertEqual(scan.orb_window(scan.parse_now("2026-11-06 09:35"), None, 15, UNTIL)[1],
                         DT(2026, 11, 6, 9, 35))
        with self.assertRaises(scan.ScanError):
            scan.orb_window(scan.parse_now("2026-11-06 10:00"), None, 7, UNTIL)


class LiveFiveMinute(unittest.TestCase):
    """A real spark 5-minute answer and RELIANCE.NS's v8 chart, fetched at 11:08:16 AM IST on Fri Nov 6 2026."""

    def setUp(self):
        self.spark = scan.parse_spark(read_text(SPARK5))
        self.v8 = scan.parse_chart(read_text(REL5), "RELIANCE.NS")
        self.now = scan.parse_now("2026-11-06T05:38:17Z")

    def test_in_progress_bar_and_live_tick_are_dropped(self):
        self.assertEqual(len(self.spark), 19)              # 20 asked, ZZZZ.NS dropped
        self.assertIn("M&M.NS", self.spark)
        self.assertIn("BAJAJ-AUTO.NS", self.spark)
        t = self.spark["RELIANCE.NS"]
        self.assertEqual(len(t["t"]), 24)                  # 9:15 ... 11:05 (in progress) + the 11:08:16 tick
        self.assertEqual(scan.ts_to_ist(t["t"][-1]), DT(2026, 11, 6, 11, 8, 16))
        day, cut = scan.orb_window(self.now, None, 15, UNTIL)
        self.assertEqual(cut, DT(2026, 11, 6, 11, 8))
        bars = scan.session_bars(t, day, cut)
        self.assertEqual(bars[0]["start"].strftime("%H:%M"), "09:15")
        self.assertEqual(bars[-1]["start"].strftime("%H:%M"), "11:00")
        self.assertEqual(len(bars), 22)
        self.assertEqual(len(scan.session_bars(self.v8, day, cut)), 22)

    def test_breakouts_with_v8_confirmation(self):
        day, cut = scan.orb_window(self.now, None, 15, UNTIL)
        wall, _ = scan.load_offline(WALL)
        hits, counts, failed = scan.scan_orb(self.spark, ["RELIANCE.NS", "M&M.NS", "ITC.NS"], day, cut, 15, 50, 0,
                                             lambda s: self.v8 if s == "RELIANCE.NS" else wall[s])
        # RELIANCE range 9:15 to 9:30: high 1306.00, low 1295.60; the 9:45 bar closed 1311.90
        self.assertEqual(hits, [Orb.MM_HIT,
                                {"symbol": "ITC.NS", "setups": ["orb"], "price": 279.35, "at": "9:45 AM",
                                 "bar_start": "09:40", "bar_index": 5, "or_high": 278.6, "or_low": 275.95,
                                 "pct_over": 0.27},
                                Orb.RELIANCE_HIT])
        self.assertEqual(counts["confirmed"], 3)

    def test_stage_two_cap_takes_the_most_traded(self):
        data, _ = scan.load_offline(WALL)
        spark_like = {k: dict(v, h=None, l=None) for k, v in data.items()}
        asked = []

        def confirm(sym):
            asked.append(sym)
            return data[sym]
        day, cut = D(2026, 11, 6), DT(2026, 11, 6, 10, 0)
        hits, counts, _ = scan.scan_orb(spark_like, sorted(data), day, cut, 15, 0, 0, confirm, max_confirm=10)
        self.assertEqual((counts["confirmed"], counts["unconfirmed"]), (10, 66))
        self.assertEqual(asked[0], "HDFCBANK.NS")          # the most rupees traded by 10:00
        self.assertIn("RELIANCE.NS", asked)
        self.assertEqual(len(hits), 7)
        hits_all, _, _ = scan.scan_orb(spark_like, sorted(data), day, cut, 15, 0, 0, confirm, max_confirm=0)
        self.assertEqual(len(hits_all), 47)
        for h in hits:
            self.assertIn(h, hits_all)


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.t = os.path.join(self.d, "trader")
        self.wl = os.path.join(self.t, "watchlist.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def read(self, path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_universe_daily_orb_end_to_end(self):
        uni = os.path.join(self.t, "universe.txt")
        code, text, _ = run(["universe", "--file", EQUITY_L, "--out", uni])
        self.assertEqual(code, 0)
        self.assertIn("NSE list EQUITY_L-excerpt-2026-11-06.csv: 11 rows.", text)
        self.assertIn("Kept 9 NSE equities (series EQ).", text)
        self.assertIn("Dropped: 1 series BE, 1 series BZ.", text)
        lines = read_text(uni).splitlines()
        self.assertEqual(lines[0], "# NSE equities (EQ series), Yahoo symbols. Built by scan.py universe.")
        self.assertEqual(len(lines), 11)
        self.assertIn("M&M.NS|Mahindra & Mahindra Limited", lines)
        self.assertNotIn("21STCENMGM", read_text(uni))
        self.assertEqual(scan.symbols_from_file(uni)[:3], ["20MICRONS.NS", "3MINDIA.NS", "BAJAJ-AUTO.NS"])

        # daily over that universe with the fixture charts: 6 of the 9 are in both, so partial
        code, text, _ = run(["daily", "--universe", uni, "--offline", DAILY, "--now", "2026-11-06 09:00",
                             "--out", self.wl, "--min-volume", "0"])
        self.assertEqual(code, 0)
        self.assertIn("Daily scan, Fri 2026-11-06 09:00 IST, closes through 2026-11-05", text)
        self.assertIn("Fetched 6 of 9 charts (3 failed or had no data)", text)
        self.assertIn("(₹50+ and 0+ shares)", text)
        self.assertIn("RELIANCE.NS      ₹1,313.40  RSI  67.3  20-day closing-high breakout", text)
        doc = self.read(self.wl)
        self.assertEqual((doc["mode"], doc["universe"], doc["scanned"], doc["failed"]), ("daily", 9, 6, 3))
        self.assertEqual([h["symbol"] for h in doc["hits"]], ["HDFCBANK.NS", "M&M.NS", "RELIANCE.NS",
                                                              "BAJAJ-AUTO.NS"])
        self.assertEqual(doc["hits"][2], {"symbol": "RELIANCE.NS", "setups": ["breakout_20d"], "price": 1313.4,
                                          "close_date": "2026-11-05", "rsi": 67.3, "volume": 8925541})
        self.assertEqual(doc["as_of"], "2026-11-05 close")
        self.assertEqual(doc["filters"], {"min_price": 50.0, "min_volume": 0})

        # the 10:00 IST scan
        code, text, _ = run(["orb", "--offline", WALL, "--now", "2026-11-06 10:00", "--out", self.wl])
        self.assertEqual(code, 0)
        self.assertIn("Opening-range breakout scan, Fri 2026-11-06 10:00 AM IST: 15-minute range (9:15 AM to "
                      "9:30 AM)", text)
        self.assertIn("47 of 100 charts closed above their opening-range high by 10:00 AM", text)
        self.assertIn(" 9:50 AM  RELIANCE.NS      ₹1,311.90  range 1,295.60 to 1,306.00  +0.45% over", text)
        self.assertIn("not a list of stocks to buy", text)
        doc = self.read(self.wl)
        self.assertEqual({k: doc[k] for k in ("created_by", "created_at", "mode", "as_of", "universe", "scanned",
                                              "source")},
                         {"created_by": "trader-scanner", "created_at": "2026-11-06T04:30:00Z", "mode": "orb",
                          "as_of": "2026-11-06 10:00 IST", "universe": 101, "scanned": 100, "source": "yahoo"})
        self.assertEqual(doc["filters"]["min_price"], 50.0)
        self.assertEqual(len(doc["hits"]), 47)
        self.assertIn(Orb.RELIANCE_HIT, doc["hits"])

        # the hits read back as a watchlist, with --at instead of --now
        code, text, _ = run(["orb", "--offline", WALL, "--at", "10:00", "--watchlist", self.wl,
                             "--json", "--no-write"])
        self.assertEqual(code, 0)
        out = json.loads(text)
        self.assertEqual((out["universe"], out["scanned"], len(out["hits"])), (47, 47, 47))

    def test_partial_stage_two_is_said(self):
        """The networked path with a fake Yahoo that answers from the Nov 6 bundle."""
        bundle = json.loads(scan._read_maybe_gz(WALL))
        calls = []

        def get(path):
            calls.append(path)
            if path.startswith("/v7/finance/spark"):
                res = []
                for y in path.split("symbols=")[1].split("&")[0].split(","):
                    y = y.replace("%26", "&")
                    r = ((bundle.get(y) or {}).get("chart") or {}).get("result")
                    if r:
                        closes = r[0]["indicators"]["quote"][0].get("close", [])
                        res.append({"symbol": y, "response": [dict(r[0], indicators={"quote": [{"close": closes}]})]})
                return json.dumps({"spark": {"result": res, "error": None}})
            return json.dumps(bundle[path.split("/chart/")[1].split("?")[0].replace("%26", "&")])
        old_get, old_sleep, real_now = scan.yahoo_get, scan.SLEEP, scan.parse_now
        scan.yahoo_get, scan.SLEEP = get, (lambda s: None)
        scan.parse_now = lambda text: real_now(text or "2026-11-06 10:05")  # the machine clock, pinned
        uni = os.path.join(self.d, "u.txt")
        with open(uni, "w", encoding="utf-8") as fh:
            fh.write("\n".join(s.replace(".NS", "") for s in sorted(bundle)) + "\n")   # bare symbols get .NS
        try:
            code, text, _ = run(["orb", "--universe", uni, "--now", "2026-11-06 10:00", "--max-confirm", "10",
                                 "--no-write"])
        finally:
            scan.yahoo_get, scan.SLEEP, scan.parse_now = old_get, old_sleep, real_now
        self.assertEqual(code, 0)
        self.assertIn("Fetched 100 of 101 charts (1 failed or had no data); 100 usable; 76 candidates, "
                      "10 confirmed", text)
        self.assertIn("PARTIAL: 66 more candidates were not checked", text)
        self.assertIn("6 of the 10 checked charts closed above their opening-range high by 10:00 AM", text)
        self.assertEqual(sum(1 for c in calls if c.startswith("/v8/")), 10)
        self.assertEqual(sum(1 for c in calls if c.startswith("/v7/")), 6)   # 101 symbols, 20 a request
        self.assertTrue(any("M%26M.NS" in c for c in calls))
        self.assertTrue(all("range=1d&interval=5m" in c for c in calls))

    def test_plain_messages_and_exit_codes(self):
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-11-07 10:00", "--out", self.wl])
        self.assertEqual(code, 1)
        self.assertIn("No session on Sat 2026-11-07 (weekend)", err)
        self.assertNotIn("Traceback", err)
        self.assertFalse(os.path.exists(self.wl))
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-11-10 10:00", "--out", self.wl])
        self.assertIn("NSE holiday: Diwali Balipratipada", err)
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-11-06 09:30", "--out", self.wl])
        self.assertIn("after 9:35 AM", err)
        cfg = os.path.join(self.t, "config.json")
        os.makedirs(self.t)
        with open(cfg, "w") as fh:
            json.dump({"no_entries_after": "09:45"}, fh)
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-11-06 10:00", "--config", cfg])
        self.assertEqual(code, 1)
        self.assertIn("expired", err)
        code, _, err = run(["orb", "--offline", WALL])
        self.assertIn("--at HH:MM", err)
        code, text, _ = run(["orb", "--offline", WALL, "--now", "2026-11-06 09:45", "--symbols", "RELIANCE",
                             "--out", self.wl])
        self.assertEqual(code, 1)                          # no breakout yet, but the empty list is written
        self.assertIn("No chart closed above its opening-range high by 9:45 AM", text)
        self.assertEqual(self.read(self.wl)["hits"], [])
        code, _, err = run(["daily", "--offline", DAILY, "--symbols", "BRK.B"])
        self.assertEqual(code, 1)
        self.assertIn("BRK.B.NS does not look like an NSE/BSE stock symbol", err)
        code, _, err = run(["daily", "--universe", os.path.join(self.d, "nope.txt")])
        self.assertEqual(code, 1)
        self.assertIn("scan.py universe", err)
        code, _, err = run(["daily", "--offline", os.path.join(self.d, "nothing-here")])
        self.assertIn("no offline file", err)
        empty = os.path.join(self.d, "empty.json")
        with open(empty, "w") as fh:
            fh.write("{}")
        code, _, err = run(["daily", "--offline", empty])
        self.assertEqual(code, 1)
        self.assertIn("no symbols to scan", err)
        code, _, err = run(["universe", "--file", os.path.join(self.d, "nope.csv")])
        self.assertEqual(code, 1)
        self.assertIn("no file at", err)
        bad = os.path.join(self.d, "page.csv")
        with open(bad, "w") as fh:
            fh.write("<html>Access Denied</html>")
        code, _, err = run(["universe", "--file", bad, "--out", os.path.join(self.d, "u.txt")])
        self.assertEqual(code, 1)
        self.assertIn("not an NSE equity or index list", err)
        self.assertFalse(os.path.exists(os.path.join(self.d, "u.txt")))

    def test_offline_folder_of_v8_files(self):
        folder = os.path.join(self.d, "charts")
        os.makedirs(folder)
        bundle = json.loads(scan._read_maybe_gz(WALL))
        for sym in ("RELIANCE.NS", "M&M.NS", "INFY.NS", "ZZZZ.NS"):
            with open(os.path.join(folder, sym.replace(".NS", "") + ".json"), "w") as fh:   # RELIANCE.json etc.
                json.dump(bundle[sym], fh)
        code, text, _ = run(["orb", "--offline", folder, "--now", "2026-11-06 10:00", "--no-write", "--json"])
        out = json.loads(text)
        self.assertEqual((out["universe"], out["scanned"], out["failed"]), (4, 3, 1))
        self.assertEqual([h["symbol"] for h in out["hits"]], ["M&M.NS", "RELIANCE.NS"])
        self.assertEqual(code, 0)

    def test_symbol_lists(self):
        txt = os.path.join(self.d, "list.txt")
        with open(txt, "w", encoding="utf-8") as fh:
            fh.write("# my list\nRELIANCE.NS|Reliance Industries Ltd.\ntcs\n\nRELIANCE\nm&m\n")
        self.assertEqual(scan.symbols_from_file(txt), ["RELIANCE.NS", "TCS.NS", "RELIANCE.NS", "M&M.NS"])
        js = os.path.join(self.d, "wl.json")
        with open(js, "w") as fh:
            json.dump({"hits": [{"symbol": "reliance.ns"}, {"symbol": "TCS"}]}, fh)
        self.assertEqual(scan.symbols_from_file(js), ["RELIANCE.NS", "TCS.NS"])
        code, text, _ = run(["daily", "--offline", DAILY, "--watchlist", txt, "--now", "2026-11-06 09:00",
                             "--json", "--no-write", "--min-volume", "0"])
        out = json.loads(text)
        self.assertEqual((out["universe"], out["scanned"]), (3, 3))
        code, text, _ = run(["daily", "--offline", DAILY, "--symbols", "reliance, TCS.NS,M&M", "--now",
                             "2026-11-06 09:00", "--json", "--no-write", "--min-volume", "0"])
        out = json.loads(text)
        self.assertEqual([h["symbol"] for h in out["hits"]], ["M&M.NS", "RELIANCE.NS"])
        code, text, _ = run(["daily", "--offline", DAILY, "--limit", "3", "--now", "2026-11-06 09:00", "--json",
                             "--no-write", "--min-volume", "0"])
        self.assertEqual(json.loads(text)["universe"], 3)

    def test_daily_json_and_defaults(self):
        code, text, _ = run(["daily", "--offline", DAILY, "--now", "2026-11-06 09:00", "--json", "--no-write",
                             "--min-volume", "0", "--min-price", "0"])
        self.assertEqual(code, 0)
        out = json.loads(text)
        self.assertEqual(len(out["hits"]), 17)
        self.assertEqual(out["note"], "A scan is a list of charts that match a rule, not a list of stocks to buy.")
        code, text, _ = run(["daily", "--offline", DAILY, "--now", "2026-11-06 11:11", "--json", "--no-write"])
        out = json.loads(text)
        self.assertEqual(out["filters"], {"min_price": 50.0, "min_volume": 200000})
        self.assertEqual(out["counts"]["illiquid"], 4)


if __name__ == "__main__":
    unittest.main()

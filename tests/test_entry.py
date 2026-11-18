"""trader-entry tests. Fixtures in tests/fixtures/trader-entry/:

RELIANCE.NS-2026-11-02-5m.json  RELIANCE.NS's Mon 2 Nov 2026 five-minute bars, a complete NSE
                         session (9:15-15:30 IST, 75 bars, the 15:20 bar null as Yahoo sent it).
                         Cut from a real Yahoo chart JSON v8 answer (range=5d&interval=5m,
                         fetched 2026-11-06). Timestamps and quotes untouched; meta trimmed to
                         that day by hand like a range=1d answer: previousClose 1164.90 is the
                         Oct 30 daily close, regularMarketTime is set to the 15:30 close.
                         Yahoo shows the NSE 9:15 bar with volume 0.
DEMO-1m-synthetic.json   SYNTHETIC one-minute bars (not market data): a wick above the
                         range high, a close AT the high, a null bar, a live tick. Session
                         starts 9:15 IST on 2026-11-02.

The live-tick and in-progress-bar cases are derived from the real file in live_cut() and
live_provider() below (a mid-session copy with a synthetic live tick): Yahoo's own mid-session
answers are not saved as fixtures.
"""

import copy
import datetime
import gzip
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX, ROOT

entry = load("trader-entry", "entry")

EDIR = os.path.join(FIX, "trader-entry")
REL = os.path.join(EDIR, "RELIANCE.NS-2026-11-02-5m.json")
DEMO = os.path.join(EDIR, "DEMO-1m-synthetic.json")
OPEN_TS = 1793591100       # 2026-11-02 09:15 IST (03:45 UTC)
DAY = datetime.date(2026, 11, 2)
DT = datetime.datetime


def ist(text):
    return entry.parse_now(text)


def doc(path=REL):
    with open(path) as fh:
        return json.load(fh)


def chart(d=None, sym="RELIANCE.NS"):
    return entry.parse_chart(json.dumps(d if d is not None else doc()), sym)


def at(text, d=None, **kw):
    return entry.evaluate(chart(d), ist(text), **kw)


def shift_to(d, day):
    """The same session moved to another date: 9:15 IST is 03:45 UTC all year."""
    d = copy.deepcopy(d)
    r = d["chart"]["result"][0]
    new_open = int((DT.combine(day, datetime.time(3, 45)) - DT(1970, 1, 1)).total_seconds())
    delta = new_open - OPEN_TS
    r["timestamp"] = [t + delta for t in r["timestamp"]]
    r["meta"]["regularMarketTime"] += delta
    return d


def live_cut(d=None, last_slot=18, tick_offset=285):
    """The file as Yahoo would serve it mid-session: bars up to slot `last_slot` (still in
    progress) plus a live tick `tick_offset` seconds into that bar (10:45 + 285 s = 10:49:45)."""
    d = copy.deepcopy(d if d is not None else doc())
    r = d["chart"]["result"][0]
    keep = [i for i, t in enumerate(r["timestamp"]) if t <= OPEN_TS + last_slot * 300]
    r["timestamp"] = [r["timestamp"][i] for i in keep]
    q = r["indicators"]["quote"][0]
    for k in q:
        q[k] = [q[k][i] for i in keep]
    tick = OPEN_TS + last_slot * 300 + tick_offset
    last = q["close"][-1]
    r["timestamp"].append(tick)
    for k in ("open", "high", "low", "close"):
        q[k].append(last)
    q["volume"].append(0)
    r["meta"]["regularMarketTime"] = tick
    return d


def no_breakout(d=None):
    """Every bar after the range stays under the 1231.60 high."""
    d = copy.deepcopy(d if d is not None else doc())
    q = d["chart"]["result"][0]["indicators"]["quote"][0]
    for i in range(3, len(q["close"])):
        if q["close"][i] is None:
            continue
        q["close"][i] = min(q["close"][i], 1231.0)
        q["high"][i] = min(q["high"][i], 1231.5)
        q["open"][i] = min(q["open"][i], 1231.0)
        q["low"][i] = min(q["low"][i], 1231.0)
    return d


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = entry.main(argv)
    return code, out.getvalue(), err.getvalue()


class TheExample(unittest.TestCase):
    def test_triggers_at_935_on_the_930_bar(self):
        r = at("2026-11-02 09:35")
        self.assertEqual(r["status"], "triggered")
        self.assertEqual(r["opening_range"]["high"], 1231.6)
        self.assertEqual(r["opening_range"]["low"], 1214.6)
        self.assertEqual(r["opening_range"]["bars"], 3)
        t = r["trigger"]
        self.assertEqual(t["time"], "9:35 AM")
        self.assertEqual(t["bar_start"], "09:30")
        self.assertEqual(t["bar_index"], 3)
        self.assertEqual(t["price"], 1234.5)
        self.assertEqual(t["close"], 1234.5)
        self.assertEqual(t["pct_over"], 0.24)
        self.assertEqual(r["level"], 1231.6)
        self.assertEqual(r["prev_close"], 1164.9000244140625)
        self.assertEqual((r["opens"], r["closes"]), ("09:15", "15:30"))
        self.assertIn("NSE data can be delayed", r["delay_note"])

    def test_at_933_the_930_bar_is_still_forming(self):
        r = at("2026-11-02 09:33")
        self.assertEqual(r["status"], "waiting")
        self.assertIsNone(r["trigger"])
        self.assertEqual(r["last"]["bar_start"], "09:25")
        self.assertEqual(r["last"]["close"], 1229.9)
        self.assertEqual(r["forming"]["bar_start"], "09:30")
        self.assertIn("₹1.70 under the range high ₹1231.60", r["reason"])
        self.assertEqual(at("2026-11-02 09:34:59")["status"], "waiting")

    def test_opening_range_is_915_to_930(self):
        r = at("2026-11-02 09:10")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "pre-open"))
        self.assertIn("opens at 9:15 IST", r["reason"])
        self.assertIn("range is set at 9:30", r["reason"])
        r = at("2026-11-02 09:29")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "forming"))
        self.assertEqual(r["opening_range"]["bars"], 2)
        self.assertFalse(r["opening_range"]["complete"])
        r = at("2026-11-02 09:31")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "open"))
        self.assertEqual(r["opening_range"]["bars"], 3)
        self.assertTrue(r["opening_range"]["complete"])
        self.assertIn("Opening range 9:15-9:30: high ₹1231.60, low ₹1214.60", "\n".join(entry.describe(r)))

    def test_the_live_tick_and_the_in_progress_bar_are_dropped(self):
        c = chart(live_cut())
        self.assertEqual(c["bars"][-1]["t"], OPEN_TS + 18 * 300 + 285)    # the 10:49:45 tick is in the file
        r = entry.evaluate(c, entry.utc_of(c["data_time"]))               # read as of the file's own time
        self.assertEqual(r["now"], "2026-11-02 10:49:45")
        self.assertEqual(r["_bars"][-1]["slot"], 17)                       # the 10:40 bar
        self.assertEqual(r["forming"]["bar_start"], "10:45")
        self.assertEqual(r["bars_closed"], 18)
        # 10:50: closed by the clock, but Yahoo's last trade (10:49:45) is before its close
        r = entry.evaluate(c, ist("2026-11-02 10:50"))
        self.assertEqual(r["_bars"][-1]["slot"], 17)
        self.assertIn("Yahoo's last trade is 10:49:45", r["lag_note"])
        # a minute later with no newer trade it counts; the tick never becomes a bar
        r = entry.evaluate(c, ist("2026-11-02 10:51"))
        self.assertEqual(r["_bars"][-1]["slot"], 18)
        self.assertEqual(r["bars_closed"], 19)
        self.assertTrue(all(b["t"] % 300 == 0 for b in r["_bars"]))

    def test_the_null_bar_and_the_whole_session(self):
        c = chart()
        self.assertEqual(len(c["bars"]), 74)                               # 75 slots, the 15:20 bar null
        r = entry.evaluate(c, entry.utc_of(c["data_time"]))
        self.assertEqual(r["now"], "2026-11-02 15:30:00")
        self.assertEqual(r["bars_closed"], 74)
        self.assertNotIn(73, [b["slot"] for b in r["_bars"]])
        self.assertEqual(r["_bars"][-1]["slot"], 74)

    def test_after_the_close_it_stays_triggered_and_says_it_is_late(self):
        r = at("2026-11-02 15:35")
        self.assertEqual((r["status"], r["phase"]), ("triggered", "after-close"))
        self.assertEqual(r["trigger"]["time"], "9:35 AM")
        text = "\n".join(entry.describe(r))
        self.assertIn("minutes ago", text)
        self.assertNotIn("This is the moment", text)


class Rules(unittest.TestCase):
    def test_volume_is_relative_and_skips_the_zero_volume_915_bar(self):
        r = at("2026-11-02 09:35", min_volume_x=1)
        # Yahoo's 9:15 bar shows 0 volume: the average is of the 9:20 and 9:25 bars
        self.assertEqual(r["opening_range"]["avg_volume"], (3308308 + 2760909) // 2)
        self.assertEqual(r["trigger"]["volume_x"], 1.07)
        self.assertEqual(r["trigger"]["bar_index"], 3)
        r = at("2026-11-02 09:36", min_volume_x=1.5)
        self.assertEqual(r["status"], "waiting")
        self.assertIn("1.07x the range's volume, under your 1.50x", r["reason"])
        self.assertEqual(at("2026-11-02 14:46", min_volume_x=1.5)["status"], "expired")
        # the volume check moves a level trigger: 9:35 bar (0.72x) -> the 10:25 bar (0.83x)
        r = at("2026-11-02 11:00", setup="level", level=1240)
        self.assertEqual((r["trigger"]["time"], r["trigger"]["bar_index"]), ("9:40 AM", 4))
        r = at("2026-11-02 11:00", setup="level", level=1240, min_volume_x=0.8)
        self.assertEqual((r["trigger"]["time"], r["trigger"]["bar_index"], r["trigger"]["volume_x"]),
                         ("10:30 AM", 14, 0.83))
        # an opening range whose bars all show 0 volume has no average, and nothing crashes
        d = doc()
        q = d["chart"]["result"][0]["indicators"]["quote"][0]
        q["volume"][1] = q["volume"][2] = 0
        r = at("2026-11-02 09:40", d, min_volume_x=1)
        self.assertIsNone(r["opening_range"]["avg_volume"])
        self.assertEqual(r["status"], "waiting")

    def test_level_setup(self):
        r = at("2026-11-02 11:00", setup="level", level=1245.8)
        self.assertEqual(r["trigger"]["time"], "10:30 AM")
        self.assertEqual(r["trigger"]["bar_index"], 14)
        self.assertEqual(r["trigger"]["price"], 1250.1)
        # a level under the open: the first eligible close is the 9:25 bar's, at 9:30 (no_entries_before)
        r = at("2026-11-02 11:00", setup="level", level=1200)
        self.assertEqual((r["trigger"]["time"], r["trigger"]["bar_index"]), ("9:30 AM", 2))
        with self.assertRaises(entry.EntryError):
            at("2026-11-02 10:30", setup="level")

    def test_no_breakout_waits_then_expires_at_the_cutoff(self):
        d = no_breakout()
        r = at("2026-11-02 10:49", d)
        self.assertEqual(r["status"], "waiting")
        self.assertEqual(at("2026-11-02 14:44", d)["status"], "waiting")
        r = at("2026-11-02 14:45", d)
        self.assertEqual(r["status"], "expired")
        self.assertIn("by 14:45", r["reason"])
        self.assertIn("₹1231.60", r["reason"])
        self.assertEqual(at("2026-11-02 15:35", d)["phase"], "after-close")

    def test_the_bar_closing_at_the_cutoff_still_counts_when_yahoo_lags(self):
        d = no_breakout()
        r0 = d["chart"]["result"][0]
        keep = [i for i, t in enumerate(r0["timestamp"]) if t <= OPEN_TS + 14 * 300]   # live: up to the 10:25 bar
        r0["timestamp"] = [r0["timestamp"][i] for i in keep]
        q = r0["indicators"]["quote"][0]
        for k in q:
            q[k] = [q[k][i] for i in keep]
        r0["meta"]["regularMarketTime"] = OPEN_TS + 74 * 60 + 40                       # last trade 10:29:40
        cfg = {"no_entries_before": "09:30", "no_entries_after": "10:30"}
        r = at("2026-11-02 10:30:10", d, cfg=cfg)                          # the 10:25 bar closed by the clock only
        self.assertEqual(r["status"], "waiting")
        self.assertIn("Yahoo's last trade is 10:29:40", r["lag_note"])
        self.assertEqual(at("2026-11-02 10:31", d, cfg=cfg)["status"], "expired")

    def test_config_cutoff(self):
        cfg = {"no_entries_before": "09:30", "no_entries_after": "09:30"}
        r = at("2026-11-02 09:35", cfg=cfg)
        self.assertEqual(r["status"], "expired")
        cfg = {"no_entries_before": "09:40", "no_entries_after": "14:45"}
        r = at("2026-11-02 10:00", cfg=cfg)
        self.assertEqual(r["trigger"]["time"], "9:40 AM")        # the 9:35 bar, first to close at or after 9:40
        # a cutoff after the close is held to the 15:30 close
        cfg = {"no_entries_before": "09:30", "no_entries_after": "16:00"}
        self.assertEqual(at("2026-11-02 10:00", cfg=cfg)["no_entries_after"], "15:30")

    def test_defaults_are_the_nse_entry_window(self):
        self.assertEqual(entry.DEFAULTS, {"no_entries_before": "09:30", "no_entries_after": "14:45"})
        self.assertEqual((entry.OPEN, entry.CLOSE), (datetime.time(9, 15), datetime.time(15, 30)))

    def test_bad_inputs(self):
        with self.assertRaises(entry.EntryError):
            at("2026-11-02 10:15", or_minutes=7)
        with self.assertRaises(entry.EntryError):
            at("2026-11-02 10:15", interval="15m")
        with self.assertRaises(entry.EntryError):
            entry.parse_now("tomorrow")
        with self.assertRaises(entry.EntryError):
            entry.parse_hhmm("25:00", "x")
        self.assertEqual(entry.parse_now("2026-11-02 09:35"), DT(2026, 11, 2, 4, 5))
        self.assertEqual(entry.parse_now("2026-11-02T04:05:00Z"), DT(2026, 11, 2, 4, 5))
        self.assertEqual(entry.parse_now("2026-11-02 09:35 IST"), DT(2026, 11, 2, 4, 5))
        self.assertEqual(entry.parse_now("2026-11-02T09:35:00+05:30"), DT(2026, 11, 2, 4, 5))

    def test_or_minutes_30(self):
        r = at("2026-11-02 11:00", or_minutes=30)
        self.assertEqual(r["opening_range"]["bars"], 6)
        self.assertEqual(r["opening_range"]["high"], 1243.4)
        self.assertEqual(r["trigger"]["time"], "10:25 AM")
        self.assertEqual(r["trigger"]["bar_index"], 13)


class OneMinute(unittest.TestCase):
    def c(self):
        return entry.parse_chart(json.dumps(doc(DEMO)), "DEMO.NS")

    def test_wick_and_close_at_the_high_do_not_trigger(self):
        c = self.c()
        r = entry.evaluate(c, entry.utc_of(c["data_time"]), interval="1m")
        self.assertEqual(r["now"], "2026-11-02 09:51:30")
        self.assertEqual(r["opening_range"], {"high": 100.5, "low": 99.0, "minutes": 15, "bars": 15, "of": 15,
                                              "complete": True, "avg_volume": 10667})
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_start"], t["bar_index"], t["price"]), ("9:44 AM", "09:43", 28, 100.62))
        r = entry.evaluate(c, ist("2026-11-02 09:43:59"), interval="1m")
        self.assertEqual(r["status"], "waiting")
        self.assertEqual(r["forming"]["bar_start"], "09:43")
        self.assertEqual(r["last"]["close"], 100.5)             # the 9:42 bar closed AT the high

    def test_null_bar_tick_and_volume(self):
        c = self.c()
        r = entry.evaluate(c, entry.utc_of(c["data_time"]), interval="1m", min_volume_x=2)
        self.assertEqual((r["trigger"]["time"], r["trigger"]["bar_index"]), ("9:47 AM", 31))
        slots = [b["slot"] for b in r["_bars"]]
        self.assertNotIn(19, slots)                               # 9:34 came as nulls
        self.assertEqual(slots[-1], 35)                           # 9:51 still forming, 9:51:30 tick dropped
        self.assertEqual(r["forming"]["bar_start"], "09:51")

    def test_the_file_and_the_interval_must_agree(self):
        code, _, err = run(["check", "DEMO", "--offline", DEMO])
        self.assertEqual(code, 1)
        self.assertIn("1m bars, not 5m", err)


class Calendar(unittest.TestCase):
    def test_ist_is_utc_plus_530_all_year(self):
        for day in (datetime.date(2026, 1, 5), datetime.date(2026, 3, 9), datetime.date(2026, 7, 6),
                    datetime.date(2026, 12, 7)):
            d = shift_to(doc(), day)
            r = entry.evaluate(chart(d), ist("%s 09:35" % day))
            self.assertEqual(r["status"], "triggered", day)
            self.assertEqual(r["trigger"]["time"], "9:35 AM", day)
            self.assertEqual(r["trigger"]["bar_index"], 3, day)
            self.assertEqual(entry.evaluate(chart(d), ist("%s 09:33" % day))["status"], "waiting", day)
            self.assertEqual(ist("%s 09:35" % day), DT.combine(day, datetime.time(4, 5)), day)
        self.assertEqual(entry.to_ist(DT(2026, 11, 2, 3, 45)), DT(2026, 11, 2, 9, 15))
        self.assertEqual(entry.ist_to_utc(DT(2026, 11, 2, 15, 30)), DT(2026, 11, 2, 10, 0))

    def test_ist_iso_ends_plus_0530(self):
        s = entry.ist_iso(DT(2026, 11, 6, 4, 45, 0, 123))
        self.assertEqual(s, "2026-11-06T10:15:00+05:30")
        self.assertTrue(s.endswith("+05:30"))

    def test_nse_holiday_is_closed(self):
        self.assertEqual(entry.trading_day(datetime.date(2026, 11, 10)),
                         (False, "NSE holiday: Diwali Balipratipada"))
        self.assertIsNone(entry.session(datetime.date(2026, 10, 20)))         # Dussehra
        d = shift_to(doc(), datetime.date(2026, 11, 10))
        r = entry.evaluate(chart(d), ist("2026-11-10 10:30"))
        self.assertEqual((r["status"], r["phase"]), ("no-data", "closed"))
        self.assertIn("NSE is closed today (NSE holiday: Diwali Balipratipada)", r["reason"])
        self.assertEqual(r["next_open"], "2026-11-11 09:15")
        # a holiday added to the table (or trader/market_india.json) closes the day too
        saved = dict(entry.NSE_HOLIDAYS)
        try:
            entry.NSE_HOLIDAYS["2026-11-02"] = "Test holiday"
            self.assertEqual(at("2026-11-02 09:35")["status"], "no-data")
        finally:
            entry.NSE_HOLIDAYS.clear()
            entry.NSE_HOLIDAYS.update(saved)
        self.assertEqual(at("2026-11-02 09:35")["status"], "triggered")

    def test_weekend_and_empty_data(self):
        r = at("2026-10-31 10:30")
        self.assertEqual((r["status"], r["phase"]), ("no-data", "closed"))
        self.assertIn("weekend", r["reason"])
        self.assertEqual(r["next_open"], "2026-11-02 09:15")
        empty = copy.deepcopy(doc())
        res = empty["chart"]["result"][0]
        del res["timestamp"]
        res["indicators"] = {"quote": [{}]}
        r = at("2026-11-02 10:15", empty)
        self.assertEqual((r["status"], r["phase"]), ("no-data", "open"))
        r = at("2026-11-03 10:15")                                # Yahoo sent yesterday's bars only
        self.assertEqual(r["status"], "no-data")
        self.assertFalse(at("2028-03-01 10:30")["calendar_known"])
        self.assertIn("market_india.json", "\n".join(entry.describe(at("2028-03-01 10:30"))))

    def test_yahoo_errors(self):
        bad = json.dumps({"chart": {"result": None, "error": {"code": "Not Found",
                                                              "description": "No data found, symbol may be delisted"}}})
        with self.assertRaises(entry.EntryError) as cm:
            entry.parse_chart(bad, "ZZZZ.NS")
        self.assertIn("No data found", str(cm.exception))
        with self.assertRaises(entry.EntryError):
            entry.parse_chart("<html>", "RELIANCE.NS")


class Symbols(unittest.TestCase):
    def test_nse_symbol_and_yahoo_symbol(self):
        self.assertEqual(entry.nse_symbol("reliance"), "RELIANCE.NS")
        self.assertEqual(entry.nse_symbol("RELIANCE.NS"), "RELIANCE.NS")
        self.assertEqual(entry.nse_symbol("TCS.BO"), "TCS.BO")
        self.assertEqual(entry.nse_symbol("^NSEI"), "^NSEI")
        self.assertEqual(entry.yahoo_symbol("M&M"), "M%26M.NS")
        self.assertEqual(entry.yahoo_symbol("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")
        self.assertIn("/chart/M%26M.NS?", entry.chart_path("m&m", "1d", "5m"))
        self.assertIn("/chart/RELIANCE.NS?", entry.chart_path("RELIANCE.NS", "1d", "5m"))


class Network(unittest.TestCase):
    """Yahoo is never called: _open_url and _sleep are swapped for fakes."""

    def setUp(self):
        self.calls, self.sleeps = [], []
        self._open, self._sleep = entry._open_url, entry._sleep
        entry._sleep = lambda s: self.sleeps.append(s)
        with open(REL) as fh:
            self.body = fh.read()

    def tearDown(self):
        entry._open_url, entry._sleep = self._open, self._sleep

    def answers(self, *codes):
        seq = list(codes)

        def fake(url, timeout=20):
            self.calls.append(url)
            code = seq.pop(0)
            if code == 200:
                return self.body
            raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b'{"chart":{"result":null,'
                                                                           b'"error":{"description":"No data found"}}}'))
        entry._open_url = fake

    def test_url_and_429_retry_once_after_a_pause(self):
        self.answers(429, 200)
        c = entry.get_chart("RELIANCE", "5m", DAY, today=DAY)
        self.assertEqual(len(c["bars"]), 74)
        self.assertEqual(c["symbol"], "RELIANCE.NS")
        self.assertEqual(self.calls,
                         ["https://query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?range=1d&interval=5m"] * 2)
        self.assertEqual(self.sleeps, [entry.BACKOFF])

    def test_429_twice_falls_back_to_query1(self):
        self.answers(429, 429, 200)
        entry.get_chart("RELIANCE.NS", "5m", DAY, today=DAY)
        self.assertEqual([u.split("/")[2] for u in self.calls],
                         ["query2.finance.yahoo.com", "query2.finance.yahoo.com", "query1.finance.yahoo.com"])

    def test_5xx_falls_back_without_waiting(self):
        self.answers(503, 200)
        entry.get_chart("RELIANCE.NS", "5m", DAY, today=DAY)
        self.assertEqual(self.sleeps, [])
        self.assertIn("query1", self.calls[1])

    def test_still_busy_is_one_plain_error(self):
        self.answers(429, 429, 429, 429)
        code, out, err = run(["check", "RELIANCE.NS", "--now", "2026-11-02 09:35"])
        self.assertEqual(code, 1)
        self.assertIn("HTTP 429", err)
        self.assertNotIn("Traceback", err)

    def test_unknown_ticker_404(self):
        self.answers(404)
        with self.assertRaises(entry.EntryError) as cm:
            entry.get_chart("ZZZZ.NS", "5m", DAY, today=DAY)
        self.assertIn("No data found", str(cm.exception))

    def test_ranges_for_past_sessions(self):
        today = datetime.date(2026, 11, 3)
        self.assertEqual(entry.chart_range(today, today, "5m"), "1d")
        self.assertEqual(entry.chart_range(DAY, today, "5m"), "5d")
        self.assertEqual(entry.chart_range(datetime.date(2026, 10, 15), today, "5m"), "1mo")
        self.assertEqual(entry.chart_range(datetime.date(2026, 9, 14), today, "5m"), "60d")
        with self.assertRaises(entry.EntryError):
            entry.chart_range(datetime.date(2026, 10, 15), today, "1m")


SPIKE = 1246.00     # above the 1245.80 level, then the bar closes under it


def live_provider(d, lag_until=None, calls=None):
    """Bars the way Yahoo serves them at `now`: closed bars final, the in-progress bar
    showing a spike to SPIKE (above the level, then it closes under), plus a live tick.
    lag_until: before this UTC time Yahoo's last trade lags 30 seconds behind."""
    base = chart(d)

    def provider(now):
        if calls is not None:
            calls.append(now)
        nts = entry.ts_of(now)
        lagging = lag_until is not None and now < lag_until
        data_ts = nts - 30 if lagging else nts
        bars = []
        for b in base["bars"]:
            if b["t"] % 300 or b["t"] > data_ts:
                continue
            b = dict(b)
            if b["t"] + 300 > data_ts:
                b["c"], b["h"] = SPIKE, max(b["h"], SPIKE)
            bars.append(b)
        if data_ts % 300:
            bars.append({"t": data_ts, "o": SPIKE, "h": SPIKE, "l": SPIKE, "c": SPIKE, "v": 0})
        return {"symbol": "RELIANCE.NS", "meta": {}, "bars": bars, "prev_close": 1164.9, "data_time": data_ts}
    return provider


class Watch(unittest.TestCase):
    ORB = {"setup": "orb", "or_minutes": 15, "interval": "5m", "level": None, "min_volume_x": None, "cfg": None}
    LEVEL = dict(ORB, setup="level", level=1245.8)

    def loop(self, provider, start, opts=None, **kw):
        clock = entry.SimClock(ist(start))
        lines, fired = [], []
        code, res = entry.watch_loop(provider, clock.now, clock.sleep, lines.append, dict(opts or self.LEVEL),
                                     on_trigger=lambda r, now: fired.append((r, now)), **kw)
        return code, res, lines, fired, clock

    def test_fires_on_the_first_closed_bar_not_the_spike_in_progress(self):
        calls = []
        code, res, lines, fired, clock = self.loop(live_provider(doc(), calls=calls), "2026-11-02 10:22", every=60)
        self.assertEqual(code, 0)
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0][0]["trigger"]["time"], "10:30 AM")
        self.assertEqual(fired[0][0]["trigger"]["price"], 1250.1)
        # it looked at 10:22 (10:20 bar spiking to 1246 in progress) and at 10:25:02 (that bar closed 1244.40)
        self.assertEqual([entry.to_ist(c).strftime("%H:%M:%S") for c in calls], ["10:22:00", "10:25:02", "10:30:02"])
        text = "\n".join(lines)
        self.assertIn("10:20 bar  closed ₹1244.40  ₹1.40 under ₹1245.80", text)
        self.assertIn("10:25 bar  closed ₹1250.10  TRIGGERED", text)
        self.assertEqual(text.count("TRIGGERED"), 1)
        self.assertIn("NSE data can be delayed", text)

    def test_waits_for_yahoo_to_catch_up(self):
        lag = ist("2026-11-02 10:30:30")
        calls = []
        code, res, lines, fired, clock = self.loop(live_provider(doc(), lag_until=lag, calls=calls),
                                                   "2026-11-02 10:27", every=30)
        self.assertEqual(code, 0)
        times = [entry.to_ist(c).strftime("%H:%M:%S") for c in calls]
        self.assertEqual(times, ["10:27:00", "10:30:02", "10:30:32"])   # 10:30:02: Yahoo still at 10:29:32
        self.assertEqual(fired[0][0]["trigger"]["time"], "10:30 AM")

    def test_from_before_the_open_prints_the_range_bar_by_bar(self):
        code, res, lines, fired, clock = self.loop(live_provider(doc()), "2026-11-02 09:00", opts=self.ORB, every=30)
        self.assertEqual(code, 0)
        text = "\n".join(lines)
        self.assertIn("09:00 IST  not open yet: watching from the first bar's close at 9:20", lines[0])
        self.assertIn(" 9:25 bar  closed ₹1229.90  opening range set: high ₹1231.60, low ₹1214.60", text)
        self.assertIn(" 9:30 bar  closed ₹1234.50  TRIGGERED", text)
        self.assertEqual(sum(1 for x in lines if " bar  closed " in x), 4)    # 9:15 ... 9:30, one line each
        self.assertEqual(res["trigger"]["time"], "9:35 AM")

    def test_a_computer_that_dozes_off_catches_up_when_it_wakes(self):
        class Dozing(entry.SimClock):
            naps = 0

            def sleep(self, seconds):
                entry.SimClock.sleep(self, seconds)
                self.naps += 1
                if self.naps == 3:                      # the laptop sleeps for 17 minutes mid-wait
                    self.t += datetime.timedelta(minutes=17)
        clock = Dozing(ist("2026-11-02 09:46"))
        calls, lines = [], []
        code, res = entry.watch_loop(live_provider(doc(), calls=calls), clock.now, clock.sleep, lines.append,
                                     dict(self.LEVEL), every=30)
        self.assertEqual(code, 0)
        times = [entry.to_ist(c).strftime("%H:%M:%S") for c in calls]
        self.assertEqual(times[:2], ["09:46:00", "10:04:30"])   # straight after waking, not a long sleep later
        text = "\n".join(lines)
        for start in (" 9:45", " 9:50", " 9:55", "10:00"):
            self.assertIn(start + " bar  closed", text)          # the bars it slept through, one line each
        self.assertEqual(res["trigger"]["time"], "10:30 AM")

    def test_expires_and_until(self):
        code, res, lines, fired, clock = self.loop(live_provider(no_breakout()), "2026-11-02 14:00", opts=self.ORB,
                                                   every=300)
        self.assertEqual(code, 1)
        self.assertEqual(res["status"], "expired")
        self.assertEqual(fired, [])
        self.assertEqual(entry.to_ist(clock.now()).strftime("%H:%M"), "14:45")
        until = DT(2026, 11, 2, 11, 0)
        code, res, lines, fired, clock = self.loop(live_provider(no_breakout()), "2026-11-02 10:30", opts=self.ORB,
                                                   every=300, until=until)
        self.assertEqual(code, 1)
        self.assertIn("--until 11:00", lines[-1])

    def test_closed_day_and_repeated_errors(self):
        code, _, lines, _, _ = self.loop(live_provider(doc()), "2026-10-31 10:00")
        self.assertEqual(code, 1)
        self.assertIn("weekend", lines[0])
        code, _, lines, _, _ = self.loop(live_provider(doc()), "2026-11-10 10:00")
        self.assertEqual(code, 1)
        self.assertIn("NSE holiday: Diwali Balipratipada", lines[0])

        def broken(now):
            raise entry.EntryError("Yahoo did not answer")
        code, _, lines, _, _ = self.loop(broken, "2026-11-02 10:00", every=30)
        self.assertEqual(code, 1)
        self.assertIn("Stopping after 5 failed reads", lines[-1])


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def test_check_write_is_the_contract_shape(self):
        code, text, _ = run(["check", "RELIANCE", "--offline", REL, "--now", "2026-11-02 09:35", "--write"])
        self.assertEqual(code, 0)
        self.assertIn("RELIANCE.NS  Mon 2026-11-02 09:35 IST", text)
        self.assertIn("TRIGGERED 9:35 AM at ₹1234.50", text)
        self.assertIn("NSE data can be delayed", text)
        self.assertIn("/trader-exit", text)
        self.assertNotIn("$", text)
        with open(os.path.join("trader", "entry.json"), encoding="utf-8") as fh:
            e = json.load(fh)
        want = {"created_by": "trader-entry", "symbol": "RELIANCE.NS", "setup": "orb", "side": "buy",
                "date": "2026-11-02", "status": "triggered", "time": "9:35 AM", "bar_start": "09:30", "bar_index": 3,
                "price": 1234.5, "level": 1231.6, "interval": "5m",
                "opening_range": {"high": 1231.6, "low": 1214.6, "minutes": 15},
                "source": "yahoo", "checked_at": "2026-11-02T04:05:00Z", "checked_at_ist": "2026-11-02T09:35:00+05:30"}
        for k, v in want.items():
            self.assertEqual(e[k], v, k)

    def test_check_waiting_json_and_exit_codes(self):
        code, text, _ = run(["check", "RELIANCE.NS", "--offline", REL, "--now", "2026-11-02 09:33", "--json"])
        self.assertEqual(code, 1)
        r = json.loads(text)
        self.assertEqual(r["status"], "waiting")
        self.assertNotIn("_bars", r)
        code, text, _ = run(["check", "RELIANCE.NS", "--offline", REL, "--now", "2026-11-02 09:33", "--write", "e.json"])
        with open("e.json") as fh:
            self.assertEqual(json.load(fh)["status"], "waiting")
        self.assertIn("still forming", text)

    def test_offline_gz_folder_and_mismatch(self):
        os.mkdir("charts")
        with open(REL, "rb") as src, gzip.open(os.path.join("charts", "RELIANCE.NS.json.gz"), "wb") as dst:
            dst.write(src.read())
        code, text, _ = run(["check", "reliance", "--offline", "charts", "--now", "2026-11-02 09:35"])
        self.assertEqual(code, 0)
        code, _, err = run(["check", "TCS", "--offline", REL])
        self.assertEqual(code, 1)
        self.assertIn("holds RELIANCE.NS, not TCS.NS", err)

    def test_longs_only_and_flag_errors(self):
        code, _, err = run(["check", "RELIANCE.NS", "--offline", REL, "--side", "short"])
        self.assertEqual(code, 1)
        self.assertIn("v2 plans longs only", err)
        code, _, err = run(["check", "RELIANCE.NS", "--offline", REL, "--level", "1200"])
        self.assertIn("--level is for --setup level", err)
        code, _, err = run(["check", "RELIANCE.NS", "--offline", REL, "--setup", "level"])
        self.assertIn("needs --level", err)

    def test_config_is_read(self):
        os.mkdir("trader")
        with open(os.path.join("trader", "config.json"), "w") as fh:
            json.dump({"no_entries_after": "09:30"}, fh)
        code, text, _ = run(["check", "RELIANCE.NS", "--offline", REL, "--now", "2026-11-02 09:35"])
        self.assertEqual(code, 1)
        self.assertIn("EXPIRED", text)
        with open(os.path.join("trader", "config.json"), "w") as fh:
            fh.write("{nope")
        code, _, err = run(["check", "RELIANCE.NS", "--offline", REL])
        self.assertIn("not valid JSON", err)

    def test_market_india_json_adds_a_holiday(self):
        os.mkdir("trader")
        with open(os.path.join("trader", "market_india.json"), "w") as fh:
            json.dump({"holidays": {"2026-11-02": "Test closure"}}, fh)
        code, text, _ = run(["check", "RELIANCE.NS", "--offline", REL, "--now", "2026-11-02 09:35"])
        self.assertEqual(code, 1)
        self.assertIn("NSE holiday: Test closure", text)
        self.assertIn("Next open: 2026-11-03 09:15 IST", text)

    def test_watch_replays_the_file_and_writes_the_entry(self):
        code, text, _ = run(["watch", "RELIANCE.NS", "--offline", REL, "--now", "2026-11-02 09:20", "--every", "30"])
        self.assertEqual(code, 0)
        self.assertIn(" 9:30 bar  closed ₹1234.50  TRIGGERED", text)
        with open(os.path.join("trader", "entry.json")) as fh:
            self.assertEqual(json.load(fh)["bar_index"], 3)
        code, text, _ = run(["watch", "RELIANCE.NS", "--offline", REL, "--now", "2026-11-02 09:31", "--setup", "level",
                             "--level", "1300", "--until", "10:00", "--out", "x.json"])
        self.assertEqual(code, 1)
        self.assertIn("--until 10:00", text)
        self.assertFalse(os.path.exists("x.json"))

    def test_watchlist(self):
        os.mkdir("charts")
        shutil.copy(REL, os.path.join("charts", "RELIANCE.NS.json"))
        shutil.copy(DEMO, os.path.join("charts", "DEMO.json"))          # a bare file name is found too
        os.mkdir("trader")
        with open(os.path.join("trader", "watchlist.json"), "w") as fh:
            json.dump({"created_by": "trader-scanner", "hits": [
                {"symbol": "RELIANCE.NS", "setups": ["orb"], "or_high": 1231.6, "or_low": 1214.6},
                {"symbol": "ZZZZ", "setups": ["orb"]}, {"symbol": "DEMO.NS", "setups": ["orb"]}]}, fh)
        code, text, _ = run(["watchlist", "--offline", "charts", "--now", "2026-11-02 09:35"])
        self.assertEqual(code, 0)
        rows = text.splitlines()
        self.assertTrue(rows[1].startswith("RELIANCE.NS   triggered  9:35 AM   ₹1234.50    ₹1231.60    ₹1234.50    +0.24%"), rows[1])
        self.assertIn("ZZZZ.NS       error: no offline file", rows[2])
        self.assertIn("DEMO.NS       error: DEMO.NS has 1m bars", rows[3])
        self.assertIn("1 of 3 triggered", text)
        code, text, _ = run(["watchlist", "--offline", "charts", "--now", "2026-11-02 09:33", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(text)[0]["result"]["status"], "waiting")
        code, _, err = run(["watchlist", "--watchlist", "missing.json"])
        self.assertIn("run /trader-scanner first", err)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-entry", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join(ROOT, "trader-risk", "SKILL.md"), encoding="utf-8") as fh:
            risk = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-entry\n"))
        self.assertGreater(len(text.splitlines()), 60)
        self.assertEqual(text[text.index("## Safety"):], risk[risk.index("## Safety"):])
        low = " ".join(text.lower().split())
        for must in ("bar by bar", "can lag", "day trading loses most people money", "/trader-exit", "longs only",
                     "in the background", "/loop", "nothing was placed", "9:35 am", "₹1234.50", "bar 3",
                     "reliance.ns", "ist", "9:15 to 9:30", "nse holidays", "can be delayed"):
            self.assertIn(must, low)
        for never in ("\u2014", "tick " + "by tick", "real " + "time", "pro" + "fit", "new york", "nyse"):
            self.assertNotIn(never, low)


if __name__ == "__main__":
    unittest.main()

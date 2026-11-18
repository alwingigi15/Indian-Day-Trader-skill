import datetime
import gzip
import hashlib
import io
import json
import os
import random
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX

tm = load("trader-timemachine", "timemachine")

DIR = os.path.join(FIX, "trader-timemachine")
# real Yahoo v8 chart for RELIANCE.NS, range=60d interval=5m, fetched 2026-11-06 11:06 IST (05:36Z).
# Like the live feed it has the in-progress 11:05 bar, the 11:06:46 tick, empty 15:20 / 15:25 bars on most
# days, a gap at 11:45-11:50 on 2026-10-15, and no bars on the NSE holidays 2026-09-14, 10-02 and 10-20.
REL = os.path.join(DIR, "RELIANCE.NS-60d5m.json.gz")
with gzip.open(REL, "rt", encoding="utf-8") as _fh:
    TEXT = _fh.read()
NOW = "2026-11-06 11:07"
NOW_UTC = tm.parse_now(NOW)

# an opening-range breakout on the fixture's last day, as trader-exit writes it: range 1295.60 to 1306.00,
# the 9:45 bar closes 1311.90 above it, stop at the midpoint 1300.80 (a ₹0.10 tick), 1R target 1323.00
PLAN = {"created_by": "trader-exit", "symbol": "RELIANCE.NS", "side": "buy", "date": "2026-11-06", "setup": "orb",
        "entry": 1311.90, "entry_time": "9:50 AM", "entry_bar": 6, "interval": "5m",
        "opening_range": {"high": 1306.00, "low": 1295.60, "minutes": 15},
        "stop": 1300.80, "stop_rule": "or_mid", "stop_label": "opening-range midpoint",
        "target": 1323.00, "target_r": 1.0, "target_label": "1R (one times the risk)",
        "risk_per_share": 11.10, "reward_risk": 1.0, "flat_by": "15:10"}

# fingerprints of this run (seed 19): any change to the draws or the data shows up here
SIM_BANDS = "25a6343b156cf9b5"
SIM_PATHS = "957a8f2f8434b7bf"
SIM_TRADES = "9c37d695556e666e"
SIM_HIST = {"lo": 1283.93, "hi": 1337.71,
            "bins": [104, 119, 139, 236, 283, 389, 470, 562, 630, 696, 720, 765, 774, 719, 678, 609, 498, 421,
                     330, 223, 193, 127, 78, 75]}


def sha(x):
    return hashlib.sha256(json.dumps(x).encode("utf-8")).hexdigest()[:16]


def plan(**kw):
    raw = dict(PLAN)
    raw.update(kw)
    return tm.normalize_plan(raw)


def machine(p=None, text=TEXT, now=NOW_UTC, **kw):
    return tm.time_machine(text, p or plan(), now, **kw)


def edited(fn):
    """The fixture's chart JSON, changed by fn(result), back as text."""
    doc = json.loads(TEXT)
    fn(doc["chart"]["result"][0])
    return json.dumps(doc)


def keep_bars(result, keep):
    """Keep only the bars where keep(ts) is true."""
    q = result["indicators"]["quote"][0]
    idx = [i for i, ts in enumerate(result["timestamp"]) if keep(ts)]
    result["timestamp"] = [result["timestamp"][i] for i in idx]
    for k in q:
        q[k] = [q[k][i] for i in idx]


def ist(ts):
    return tm.to_ist(tm.utc_of(ts))


def stamp(local):
    """IST wall clock -> Unix seconds."""
    return int((tm.ist_to_utc(local) - tm.EPOCH).total_seconds())


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = tm.main(argv)
    return code, out.getvalue(), err.getvalue()


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = machine()

    def test_the_odds(self):
        m = self.doc["mc"]
        self.assertEqual((m["replays"], m["seed"], m["window"], m["until"], m["until_from"]),
                         (10000, 19, 2, "15:10", "plan flat_by"))
        self.assertEqual(m["pool_sessions"], 58)
        self.assertEqual(m["bars_left"], 64)
        self.assertEqual((m["pool_from"], m["pool_to"]), ("2026-08-12", "2026-11-05"))
        self.assertEqual((m["p_target"], m["p_stop"], m["p_neither"], m["p_green"]), (0.2703, 0.3891, 0.3406, 0.4414))
        self.assertEqual((m["median_close"], m["p5_close"], m["p95_close"]), (1310.34, 1291.41, 1329.52))
        self.assertEqual((m["avg_R_gross"], m["avg_R_net"], m["avg_R"]), (-0.121, -0.219, -0.219))
        self.assertEqual(m["r_at_target"], 1.0)

    def test_bit_for_bit(self):
        m = self.doc["mc"]
        self.assertEqual(sha(m["bands"]), SIM_BANDS)
        self.assertEqual(len(m["sample_paths"]), 160)
        self.assertEqual(sha(m["sample_paths"]), SIM_PATHS)
        self.assertEqual(m["hist"], SIM_HIST)
        self.assertEqual(m["bands"][-1], [1291.53, 1302.29, 1310.36, 1318.29, 1329.5])

    def test_the_history_replay(self):
        h = self.doc["history"]
        self.assertEqual((h["sessions"], h["triggered"]), (58, 28))
        self.assertEqual((h["target"], h["stop"], h["close"], h["green"], h["green_net"]), (13, 10, 5, 15, 14))
        self.assertEqual((h["avg_R_gross"], h["avg_R_net"], h["avg_R"]), (0.057, -0.157, -0.157))
        self.assertEqual(h["rule"], {"setup": "orb", "or_minutes": 15, "stop_rule": "or_mid", "target_r": 1.0})
        slim = [{"date": t["date"], "entry": t["entry"], "outcome": t["outcome"], "R": t["R"]} for t in h["trades"]]
        self.assertEqual(sha(slim), SIM_TRADES)
        first = h["trades"][0]
        self.assertEqual((first["date"], first["time"], first["entry"], first["stop"], first["target"],
                          first["outcome"], first["qty"], first["R"], first["R_net"]),
                         ("2026-08-17", "12:50 PM", 1313.2, 1308.5, 1317.9, "target", 76, 1.0, 0.77))

    def test_history_prices_are_on_legal_ticks(self):
        for t in self.doc["history"]["trades"]:
            for k in ("stop", "target"):
                tick = tm.tick_size(t[k])
                self.assertEqual(tm.Decimal(str(t[k])) % tick, 0, (t, k))

    def test_the_verdict_counts_the_charges(self):
        self.assertEqual(self.doc["verdict"],
                         "Leans toward the stop: at 1R after charges the target has to come first in 55 % of the "
                         "replays that hit one side; it did in 41 % (target first 27.0 % vs stop first 38.9 %).")
        gap, share, even = tm.lean(self.doc["mc"])
        self.assertEqual((round(share, 3), round(even, 3)), (0.41, 0.549))
        self.assertEqual(self.doc["verdict_notes"], [])

    def test_bands_start_at_the_entry(self):
        m = self.doc["mc"]
        self.assertEqual(m["bands"][0], [1311.9] * 5)
        self.assertEqual(len(m["bands"]), 65)
        self.assertEqual((m["band_times"][0], m["band_times"][-1], len(m["band_times"])), ("09:50", "15:10", 65))
        self.assertTrue(all(p[0] == 1311.9 and len(p) == 65 for p in m["sample_paths"]))

    def test_the_plan_is_carried_through(self):
        p = self.doc["plan"]
        self.assertEqual((p["symbol"], p["entry"], p["stop"], p["target"], p["entry_bar"], p["entry_time"]),
                         ("RELIANCE.NS", 1311.9, 1300.8, 1323.0, 6, "9:50 AM"))
        self.assertEqual((p["risk_per_share"], p["target_r"], p["flat_by"]), (11.1, 1.0, "15:10"))
        self.assertEqual(self.doc["warnings"], [])
        self.assertEqual(self.doc["data"]["last_bar"], "2026-11-06 11:00 IST")
        self.assertEqual(self.doc["created_at_ist"], "2026-11-06T11:07:00+05:30")

    def test_the_text_report(self):
        text = "\n".join(tm.report(self.doc))
        for line in ("RELIANCE.NS buy, Friday 2026-11-06: entry ₹1311.90 at 9:50 AM IST (bar 6), stop ₹1300.80, "
                     "target ₹1323.00 (1R, risk ₹11.10 a share).",
                     "10,000 replays", "9:50 AM to 3:10 PM (64 five-minute bars, ending at your flat-by time)",
                     "seed 19", "58 full sessions",
                     "target first   27.0 %", "stop first     38.9 %", "neither        34.1 %   still open at 3:10 PM",
                     "green          44.1 %", "median ₹1310.34, 90 % of replays between ₹1291.41 and ₹1329.52",
                     "Average R: -0.121 gross, -0.219 net of charges",
                     "Charges: ₹82.82 at the target, ₹82.33 at the stop, on 76 shares, a ₹1.00 lakh position",
                     "fired 28 times: 13 target, 10 stop, 5 neither. Average R +0.057 gross, -0.157 net of charges.",
                     "Leans toward the stop: at 1R after charges",
                     "Left out 1 session(s) with gaps before 3:10 PM: 2026-10-15.",
                     "not a prediction", "--seed 19"):
            self.assertIn(line, text)
        for word in ("profit", "P&L", "guarantee", "real time", "tick by tick", "$", "New York", " ET"):
            self.assertNotIn(word, text)


class Seeds(unittest.TestCase):
    def test_same_seed_same_numbers(self):
        a = machine(replays=2000, seed=5)["mc"]
        b = machine(replays=2000, seed=5)["mc"]
        self.assertEqual(a, b)

    def test_another_seed_gives_other_numbers(self):
        m = machine(seed=7)["mc"]
        self.assertEqual((m["p_target"], m["p_stop"], m["median_close"]), (0.2665, 0.3833, 1310.14))
        self.assertAlmostEqual(m["p_target"] + m["p_stop"] + m["p_neither"], 1.0, places=6)
        self.assertNotEqual(sha(m["bands"]), SIM_BANDS)

    def test_the_draws_are_stable_across_pythons(self):
        # random.Random(seed), randint and choice are stable since Python 3.2
        r = random.Random(19)
        self.assertEqual([r.randint(-2, 2) for _ in range(8)], [-2, 2, -2, 2, -1, 1, 0, 2])
        self.assertEqual(r.choice(range(59)), 18)

    def test_window_and_replays(self):
        a = machine(replays=500, window=0)["mc"]
        self.assertEqual((a["replays"], a["window"]), (500, 0))
        self.assertNotEqual(a["bands"], machine(replays=500)["mc"]["bands"])
        with self.assertRaises(tm.TMError):
            machine(replays=0)
        with self.assertRaises(tm.TMError):
            machine(window=11)


class Session(unittest.TestCase):
    def test_the_nse_session_is_75_bars(self):
        self.assertEqual((tm.OPEN, tm.CLOSE), (datetime.time(9, 15), datetime.time(15, 30)))
        self.assertEqual(tm.SESSION_BARS, 75)
        self.assertEqual((tm.OPEN_MIN, tm.CLOSE_MIN), (555, 930))
        self.assertFalse(hasattr(tm, "NYSE_EARLY_CLOSE"))
        self.assertFalse(hasattr(tm, "NYSE_HOLIDAYS"))
        with open(tm.__file__, encoding="utf-8") as fh:
            src = fh.read()
        for old in ("= 78", "390", "6.5", "eastern", "16 * 60"):
            self.assertNotIn(old, src, old)

    def test_ist_times_and_the_session_edges(self):
        day = datetime.date(2026, 11, 5)
        at = lambda h, m: stamp(datetime.datetime.combine(day, datetime.time(h, m)))  # noqa: E731
        stamps = [at(9, 10), at(9, 15), at(12, 0), at(15, 25), at(15, 30)]
        self.assertEqual(stamps[1], 1793850300)       # 2026-11-05 03:45Z = 9:15 IST
        res = {"meta": {"symbol": "X.NS"}, "timestamp": stamps,
               "indicators": {"quote": [{"open": [1] * 5, "high": [1] * 5, "low": [1] * 5, "close": [1] * 5,
                                         "volume": [0] * 5}]}}
        meta, bars, dropped = tm.parse_bars(res, tm.parse_now("2026-11-06T00:00Z"))
        self.assertEqual([(b["date"], b["start"], b["slot"]) for b in bars],
                         [("2026-11-05", "09:15", 0), ("2026-11-05", "12:00", 33), ("2026-11-05", "15:25", 74)])
        self.assertEqual(dropped["outside_hours"], 2)

    def test_ist_helpers(self):
        utc = datetime.datetime(2026, 11, 6, 5, 37)
        self.assertEqual(tm.to_ist(utc), datetime.datetime(2026, 11, 6, 11, 7))
        self.assertEqual(tm.ist_iso(utc), "2026-11-06T11:07:00+05:30")
        self.assertEqual(tm.parse_now("2026-11-06 11:07"), utc)
        self.assertEqual(tm.parse_now("2026-11-06T05:37Z"), utc)
        self.assertEqual(tm.parse_now("2026-11-06T11:07+05:30"), utc)
        # no daylight saving: the same offset in June and December
        self.assertEqual(tm.to_ist(datetime.datetime(2026, 6, 1)) - datetime.datetime(2026, 6, 1),
                         tm.to_ist(datetime.datetime(2026, 12, 1)) - datetime.datetime(2026, 12, 1))

    def test_a_full_75_bar_session_is_needed_to_replay_to_the_close(self):
        # Yahoo leaves 15:20 (and often 15:25) empty, so no fixture day has all 75 bars
        with self.assertRaises(tm.TMError) as cm:
            machine(until="15:30", replays=10)
        self.assertIn("only 0 full session(s)", str(cm.exception))
        self.assertIn("an earlier --until", str(cm.exception))
        # fill the holes with the bar before them (synthetic, only to prove 75-bar sessions count)
        def fill(res):
            q = res["indicators"]["quote"][0]
            for i in range(1, len(res["timestamp"])):
                if q["close"][i] is None and ist(res["timestamp"][i]).hour == 15:
                    for k in ("open", "high", "low", "close"):
                        q[k][i] = q["close"][i - 1]
                    q["volume"][i] = 0
        doc = machine(text=edited(fill), until="15:30", replays=300)
        self.assertEqual((doc["mc"]["until"], doc["mc"]["bars_left"], doc["mc"]["band_times"][-1]), ("15:30", 68, "15:30"))
        self.assertEqual(doc["mc"]["pool_sessions"], 58)
        days = tm.split_sessions(tm.parse_bars(tm.chart_result(edited(fill), "RELIANCE"), NOW_UTC)[1])
        self.assertEqual(len(days["2026-11-05"]), 75)

    def test_the_replay_ends_at_the_plans_flat_by(self):
        doc = machine(plan(flat_by="14:30"), replays=300)
        m = doc["mc"]
        self.assertEqual((m["until"], m["until_from"], m["bars_left"], m["band_times"][-1]),
                         ("14:30", "plan flat_by", 56, "14:30"))
        self.assertIn("still open at 2:30 PM", "\n".join(tm.report(doc)))
        # no flat_by in the plan: the config's, else 15:10
        self.assertEqual(machine(plan(flat_by=None), replays=300, cfg={"flat_by": "14:30"})["mc"]["bars_left"], 56)
        m = machine(plan(flat_by=None), replays=300)["mc"]
        self.assertEqual((m["until"], m["until_from"], m["bars_left"]), ("15:10", "default flat_by", 64))
        # --until wins over the plan
        m = machine(plan(flat_by="14:30"), until="15:00", replays=300)["mc"]
        self.assertEqual((m["until"], m["until_from"]), ("15:00", "--until"))
        # the stop or the target only count when they are hit before the flat-by time
        early = machine(plan(flat_by="10:30"), replays=2000)["mc"]
        late = machine(replays=2000)["mc"]
        self.assertGreater(early["p_neither"], late["p_neither"])
        self.assertEqual(plan(flat_by="3:10 PM")["flat_by"], "15:10")


class Holidays(unittest.TestCase):
    def setUp(self):
        self.saved = dict(tm.NSE_HOLIDAYS)

    def tearDown(self):
        tm.NSE_HOLIDAYS.clear()
        tm.NSE_HOLIDAYS.update(self.saved)

    def test_a_plan_on_a_holiday_or_weekend_is_refused(self):
        for day, words in (("2026-11-10", "Diwali Balipratipada"), ("2026-11-07", "Saturday"),
                           ("2026-10-02", "Mahatma Gandhi Jayanti")):
            with self.assertRaises(tm.TMError) as cm:
                machine(plan(date=day), replays=10)
            self.assertIn("NSE is closed on %s (%s)" % (day, words), str(cm.exception))

    def test_bars_on_a_holiday_never_reach_the_pool(self):
        # the 2026-10-01 session copied onto 2026-10-02 (Gandhi Jayanti, no trading) and onto
        # Sunday 2026-11-01, like a stray Muhurat-style session
        def ghost(res):
            q = res["indicators"]["quote"][0]
            src = [i for i, ts in enumerate(res["timestamp"]) if ist(ts).date().isoformat() == "2026-10-01"]
            for shift in (1, 31):
                for i in src:
                    res["timestamp"].append(res["timestamp"][i] + shift * 86400)
                    for k in q:
                        q[k].append(q[k][i])
        doc = machine(text=edited(ghost), replays=300)
        self.assertEqual(doc["data"]["dropped"]["closed_day"], 146)    # 2 x the 73 priced bars of that day
        self.assertEqual(doc["mc"]["pool_sessions"], 58)
        self.assertNotIn("2026-10-02", [t["date"] for t in doc["history"]["trades"]])

    def test_a_new_holiday_is_skipped(self):
        tm.NSE_HOLIDAYS["2026-11-05"] = "test holiday"
        doc = machine(replays=300)
        self.assertEqual((doc["mc"]["pool_sessions"], doc["mc"]["pool_to"]), (57, "2026-11-04"))
        self.assertEqual(doc["data"]["dropped"]["closed_day"], 74)

    def test_market_india_json_adds_holidays(self):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "market_india.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"holidays": {"2027-01-26": "Republic Day"}}, fh)
            hol = tm.load_holidays(path)
            self.assertEqual(hol["2027-01-26"], "Republic Day")
            self.assertEqual(hol["2026-12-25"], "Christmas")
            self.assertEqual(tm.load_holidays(os.path.join(d, "missing.json")), tm.NSE_HOLIDAYS)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("not json")
            self.assertEqual(tm.load_holidays(path), tm.NSE_HOLIDAYS)
        finally:
            shutil.rmtree(d)


class Bars(unittest.TestCase):
    def setUp(self):
        self.result = tm.chart_result(TEXT, "RELIANCE.NS")

    def test_the_in_progress_tick_is_dropped(self):
        meta, bars, dropped = tm.parse_bars(self.result, NOW_UTC)
        self.assertEqual(dropped, {"no_price": 108, "off_grid": 1, "not_complete": 1, "outside_hours": 0,
                                   "closed_day": 0})
        last = bars[-1]
        self.assertEqual((last["date"], last["start"], last["slot"]), ("2026-11-06", "11:00", 21))
        # with the real clock (any time later) the data's own time still marks 11:05 as in progress
        self.assertEqual(tm.parse_bars(self.result, tm.parse_now(None))[1][-1]["start"], "11:00")
        entry = [b for b in bars if b["date"] == "2026-11-06" and b["slot"] == 6][0]
        self.assertEqual((entry["start"], entry["c"]), ("09:45", 1311.9000244140625))

    def test_now_pins_the_clock(self):
        bars = tm.parse_bars(self.result, tm.parse_now("2026-11-06 09:48"))[1]
        today = [b for b in bars if b["date"] == "2026-11-06"]
        self.assertEqual(len(today), 6)
        with self.assertRaises(tm.TMError) as cm:
            machine(now=tm.parse_now("2026-11-06 09:48"))
        self.assertIn("not in the data", str(cm.exception))
        self.assertIn("09:40 bar", str(cm.exception))
        self.assertEqual(machine(now=tm.parse_now("2026-11-06 09:50"), replays=200)["mc"]["pool_sessions"], 58)

    def test_a_session_with_a_gap_is_left_out(self):
        doc = machine(replays=300)
        self.assertEqual(doc["data"]["short_sessions_left_out"], [{"date": "2026-10-15", "bars": 69}])
        self.assertNotIn("2026-10-15", [t["date"] for t in doc["history"]["trades"]])
        # the 2026-10-15 gap is at 11:45, so a replay that ends at 11:30 keeps that day
        p = plan(entry_time="9:50 AM", flat_by="11:30")
        self.assertEqual(machine(p, replays=300)["mc"]["pool_sessions"], 59)

    def test_a_day_cut_short_is_left_out(self):
        def cut(res):  # 2026-09-04 stops at 1:00 PM
            keep_bars(res, lambda ts: not (ist(ts).date().isoformat() == "2026-09-04" and ist(ts).hour >= 13))
        doc = machine(text=edited(cut), replays=300)
        self.assertEqual(doc["mc"]["pool_sessions"], 57)
        self.assertIn({"date": "2026-09-04", "bars": 45}, doc["data"]["short_sessions_left_out"])
        self.assertEqual(doc["history"]["sessions"], 57)
        self.assertNotIn("2026-09-04", [t["date"] for t in doc["history"]["trades"]])
        self.assertIn("Left out 2 session(s)", "\n".join(tm.report(doc)))

    def test_today_is_not_in_the_pool(self):
        doc = machine(replays=200)
        self.assertEqual(doc["mc"]["pool_to"], "2026-11-05")
        self.assertNotIn("2026-11-06", [t["date"] for t in doc["history"]["trades"]])

    def test_a_missing_price_is_dropped(self):
        def hole(res):
            res["indicators"]["quote"][0]["close"][5] = None
        doc = machine(text=edited(hole), replays=200)
        self.assertEqual(doc["data"]["dropped"]["no_price"], 109)
        self.assertEqual(doc["mc"]["pool_sessions"], 57)   # 2026-08-12 now has a gap at 9:40

    def test_empty_and_broken_data(self):
        empty = json.dumps({"chart": {"result": [{"meta": {"symbol": "RELIANCE.NS"}, "timestamp": [],
                                                  "indicators": {"quote": [{}]}}], "error": None}})
        with self.assertRaises(tm.TMError) as cm:
            machine(text=empty)
        self.assertIn("no complete 5-minute bars", str(cm.exception))
        gone = json.dumps({"chart": {"result": None, "error": {"code": "Not Found",
                                                               "description": "No data found, symbol may be delisted"}}})
        with self.assertRaises(tm.TMError) as cm:
            machine(text=gone)
        self.assertIn("No data found", str(cm.exception))
        with self.assertRaises(tm.TMError):
            machine(text="<html>rate limited</html>")

    def test_fewer_than_20_sessions_is_refused(self):
        def trim(res):
            keep_bars(res, lambda ts: ist(ts).date().isoformat() >= "2026-10-08")
        with self.assertRaises(tm.TMError) as cm:
            machine(text=edited(trim))
        self.assertIn("only 19 full session(s)", str(cm.exception))
        self.assertIn("at least 20", str(cm.exception))

        def twenty(res):
            keep_bars(res, lambda ts: ist(ts).date().isoformat() >= "2026-10-07")
        self.assertEqual(machine(text=edited(twenty), replays=200)["mc"]["pool_sessions"], 20)

    def test_the_data_must_be_the_plans_symbol(self):
        with self.assertRaises(tm.TMError) as cm:
            machine(plan(symbol="TCS"))
        self.assertIn("data is for RELIANCE.NS but the plan is for TCS.NS", str(cm.exception))
        # a bare symbol becomes RELIANCE.NS and matches the data
        self.assertEqual(machine(plan(symbol="reliance"), replays=50)["plan"]["symbol"], "RELIANCE.NS")


class Symbols(unittest.TestCase):
    def test_nse_and_yahoo_symbols(self):
        self.assertEqual(tm.nse_symbol("reliance"), "RELIANCE.NS")
        self.assertEqual(tm.nse_symbol("TCS.BO"), "TCS.BO")
        self.assertEqual(tm.nse_symbol("^NSEI"), "^NSEI")
        self.assertEqual(tm.yahoo_symbol("M&M"), "M%26M.NS")
        self.assertEqual(tm.yahoo_symbol("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")

    def test_plan_symbols(self):
        for raw, want in (("RELIANCE", "RELIANCE.NS"), ("m&m", "M&M.NS"), ("3MINDIA", "3MINDIA.NS"),
                          ("BAJAJ-AUTO.NS", "BAJAJ-AUTO.NS"), ("INFY.BO", "INFY.BO")):
            self.assertEqual(plan(symbol=raw)["symbol"], want)
        for bad in ("^NSEI", "RELIANCE.L", "TOO LONG NAME", "X" * 21):
            with self.assertRaises(tm.TMError):
                plan(symbol=bad)


class Ticks(unittest.TestCase):
    def test_tick_size_table(self):
        for price, tick in ((249.99, "0.01"), (250, "0.05"), (1311.9, "0.10"), (5000, "0.50"), (12000, "1.00"),
                            (25000, "5.00")):
            self.assertEqual(tm.tick_size(price), tm.Decimal(tick), price)

    def test_stops_round_away_from_the_entry_on_the_tick(self):
        self.assertEqual(tm.tick_away(1300.85, True), 1300.8)
        self.assertEqual(tm.tick_away(1300.85, False), 1300.9)
        self.assertEqual(tm.tick_away((1306.0 + 1295.5999755859375) / 2, True), 1300.7)
        self.assertEqual(tm.tick_away((1306.0 + 1295.6) / 2, True), 1300.8)   # opening_range rounds to paise
        self.assertEqual(tm.tick_away(1300.8000000001, True), 1300.8)
        self.assertEqual(tm.tick_away(212.347, True), 212.34)
        self.assertEqual(tm.tick_away(612.33, True), 612.3)

    def test_the_entry_tolerance_is_one_tick(self):
        # the 9:45 bar's high is 1312.90: one ₹0.10 tick above it is fine, two are flagged
        self.assertEqual(machine(plan(entry=1313.00, target=1325.20), replays=50)["warnings"], [])
        doc = machine(plan(entry=1313.10, target=1325.40), replays=50)
        self.assertIn("outside that bar's range", doc["warnings"][0])


class Charges(unittest.TestCase):
    def test_the_charges_function(self):
        # by hand: brokerage 0.30 + 0.30, STT 0.25, exchange 0.0614, SEBI 0.002, stamp 0.03, GST 0.1194
        self.assertEqual(tm.intraday_charges(10, 100.0, 100.0), 1.06)
        # brokerage caps at ₹20 an order
        self.assertEqual(tm.intraday_charges(76, 1311.9, 1323.0), 82.82)
        self.assertEqual(tm.intraday_charges(76, 1311.9, 1323.0, {"brokerage_max_per_order": 0}), 35.62)

    def test_charges_lower_the_net_average_r(self):
        doc = machine(replays=2000)
        m, h = doc["mc"], doc["history"]
        self.assertLess(m["avg_R_net"], m["avg_R_gross"])
        self.assertAlmostEqual(m["avg_R_gross"] - m["avg_R_net"], m["avg_charges_R"], places=2)
        self.assertLess(h["avg_R_net"], h["avg_R_gross"])
        for t in h["trades"]:
            self.assertAlmostEqual(t["R_net"], t["R"] - t["charges_R"], places=1)
            self.assertGreater(t["charges_R"], 0)
        c = doc["charges"]
        self.assertEqual((c["basis"], c["qty"], c["at_target_inr"], c["at_stop_inr"], c["rates_from"]),
                         ("notional", 76, 82.82, 82.33, "zerodha"))
        self.assertAlmostEqual(m["charges_R_target"], 82.82 / (76 * 11.1), places=4)

    def test_no_charges_means_net_equals_gross(self):
        zero = {"charges": {k: 0 for k in tm.DEFAULT_CHARGES}}
        doc = machine(cfg=zero, replays=2000)
        self.assertEqual(doc["mc"]["avg_R_net"], doc["mc"]["avg_R_gross"])
        self.assertEqual(doc["history"]["avg_R_net"], doc["history"]["avg_R_gross"])
        self.assertEqual(doc["verdict"], "Leans toward the stop: target first 26.9 % vs stop first 38.9 %.")
        self.assertEqual(doc["charges"]["rates_from"], "config")

    def test_the_quantity_for_the_charges(self):
        doc = machine(cfg={"max_loss_per_trade_inr": 1000}, replays=200)
        self.assertEqual((doc["charges"]["basis"], doc["charges"]["qty"]), ("max_loss_per_trade_inr", 90))
        self.assertIn("on 90 shares (max_loss_per_trade_inr ₹1,000.00 / the risk, from trader/config.json)",
                      "\n".join(tm.report(doc)))
        doc = machine(plan(qty=10), cfg={"max_loss_per_trade_inr": 1000}, replays=200)
        self.assertEqual((doc["charges"]["basis"], doc["charges"]["qty"]), ("qty", 10))
        # fewer shares: the ₹40 of brokerage weighs more per R
        self.assertGreater(doc["mc"]["avg_charges_R"], machine(replays=200)["mc"]["avg_charges_R"])
        for bad in (0, "x"):
            with self.assertRaises(tm.TMError):
                plan(qty=bad)

    def test_break_even_moves_with_the_charges(self):
        mc = {"p_target": 0.5, "p_stop": 0.5, "r_at_target": 1.0}
        self.assertEqual(tm.lean(mc)[2], 0.5)
        self.assertAlmostEqual(tm.lean(dict(mc, charges_R_target=0.1, charges_R_stop=0.1))[2], 1.1 / 2.0)
        self.assertEqual(tm.lean(dict(mc, charges_R_target=1.5, charges_R_stop=0.1))[2], 1.0)

    def test_inr_text(self):
        self.assertEqual(tm.inr(82.82), "₹82.82")
        self.assertEqual(tm.inr(100000), "₹1.00 lakh")
        self.assertEqual(tm.inr(52300000000), "₹5,230.00 crore")


class Plans(unittest.TestCase):
    def test_an_entry_not_in_the_data_is_refused(self):
        with self.assertRaises(tm.TMError) as cm:
            machine(plan(date="2026-11-09"))
        self.assertIn("not in the data", str(cm.exception))
        self.assertIn("no bars for that day", str(cm.exception))
        with self.assertRaises(tm.TMError) as cm:      # 11:05 has not closed in this data yet
            machine(plan(entry_time="11:10 AM", entry_bar=None))
        self.assertIn("not in the data", str(cm.exception))

    def test_plan_checks(self):
        cases = [
            (dict(entry_time="9:52 AM", entry_bar=None), "5-minute grid"),
            (dict(entry_time="3:30 PM", entry_bar=None), "between 9:20 AM and 3:25 PM IST"),
            (dict(entry_bar=9), "disagrees with itself"),
            (dict(entry_time=None, entry_bar=None), "entry_time"),
            (dict(interval="1m"), "7 days of 1-min bars"),
            (dict(stop=1312.0), "stop < entry < target"),
            (dict(side="short"), "buy or sell"),
            (dict(symbol=""), "NSE symbol"),
            (dict(entry="abc"), "number for entry"),
            (dict(date="6 Nov"), "date like"),
            (dict(opening_range={"minutes": 7}), "multiple of 5"),
            (dict(opening_range={"minutes": 375}), "multiple of 5"),
            (dict(flat_by="soon"), "IST time"),
        ]
        for change, words in cases:
            with self.assertRaises(tm.TMError, msg=change) as cm:
                plan(**change)
            self.assertIn(words, str(cm.exception), change)

    def test_entry_bar_or_entry_time_alone(self):
        self.assertEqual(plan(entry_time=None)["entry_time"], "9:50 AM")
        self.assertEqual(plan(entry_bar=None)["entry_bar"], 6)
        self.assertEqual(plan(entry_time="09:50", entry_bar=None)["entry_bar"], 6)
        self.assertEqual(plan(entry_time="9:50am", entry_bar=None)["entry_bar"], 6)

    def test_until_cuts_the_replay(self):
        doc = machine(until="14:55", replays=500)
        m = doc["mc"]
        self.assertEqual((m["until"], m["bars_left"], m["band_times"][-1]), ("14:55", 61, "14:55"))
        self.assertIn("still open at 2:55 PM", "\n".join(tm.report(doc)))
        self.assertIn("ending at your --until", "\n".join(tm.report(doc)))
        self.assertEqual(machine(until="2:55 PM", replays=500)["mc"], m)
        for bad, words in (("9:50 AM", "nothing to replay"), ("15:35", "no later than 3:30 PM"),
                           ("15:07", "5-minute grid"), ("noon", "IST time")):
            with self.assertRaises(tm.TMError) as cm:
                machine(until=bad, replays=10)
            self.assertIn(words, str(cm.exception), bad)

    def test_the_stop_rule_is_worked_out_when_not_given(self):
        rule = machine(plan(stop_rule=None), replays=100)["history"]["rule"]
        self.assertEqual(rule["stop_rule"], "or_mid")
        rule = machine(plan(stop_rule=None, stop=1295.6, target=1328.2), replays=100)["history"]["rule"]
        self.assertEqual((rule["stop_rule"], rule["target_r"]), ("or_low", 1.0))
        rule = machine(plan(stop_rule="atr", stop=1302.0, target=1331.7), replays=100)["history"]["rule"]
        self.assertEqual((rule["stop_rule"], rule["stop_pct"], rule["target_r"]), ("pct", 0.7546, 2.0))

    def test_a_setup_other_than_orb_skips_the_history(self):
        doc = machine(plan(setup="flag"), replays=200)
        self.assertIsNone(doc["history"]["rule"])
        self.assertIn("orb", doc["history"]["note"])
        self.assertEqual(doc["mc"]["pool_sessions"], 58)
        self.assertIn("History: skipped", "\n".join(tm.report(doc)))

    def test_a_sell_plan_mirrors_the_rules(self):
        doc = machine(plan(side="sell", stop=1323.0, target=1300.8, stop_rule="or_high"), replays=2000)
        m = doc["mc"]
        self.assertAlmostEqual(m["p_target"] + m["p_stop"] + m["p_neither"], 1.0, places=6)
        self.assertEqual(m["r_at_target"], 1.0)
        self.assertLess(m["avg_R_net"], m["avg_R_gross"])
        self.assertEqual(doc["history"]["rule"]["stop_rule"], "or_high")
        for t in doc["history"]["trades"]:
            self.assertTrue(t["target"] < t["entry"] < t["stop"], t)
        self.assertIn("below the entry", "\n".join(tm.report(doc)))

    def test_the_verdict_thresholds_at_1r(self):
        # 1R, no charges: the target's share of the replays that hit one side against 50 %
        self.assertTrue(tm.verdict({"p_target": 0.30, "p_stop": 0.26}).startswith("Close to a coin flip"))
        self.assertTrue(tm.verdict({"p_target": 0.40, "p_stop": 0.30}).startswith("Leans toward the target"))
        self.assertTrue(tm.verdict({"p_target": 0.20, "p_stop": 0.30}).startswith("Leans toward the stop"))
        self.assertTrue(tm.verdict({"p_target": 0.60, "p_stop": 0.30}).startswith("Strongly toward the target"))
        self.assertEqual(tm.verdict({"p_target": 0.10, "p_stop": 0.60, "r_at_target": 1.0}),
                         "Strongly toward the stop: target first 10.0 % vs stop first 60.0 %.")
        self.assertEqual(tm.verdict({"p_target": 0.0, "p_stop": 0.0, "until": "15:10"}),
                         "No replay reached the target or the stop by 3:10 PM: the price at 3:10 PM decides every one.")
        # charges can turn a coin flip into a lean toward the stop
        flip = {"p_target": 0.30, "p_stop": 0.28}
        self.assertTrue(tm.verdict(flip).startswith("Close to a coin flip"))
        self.assertTrue(tm.verdict(dict(flip, charges_R_target=0.3, charges_R_stop=0.3))
                        .startswith("Leans toward the stop: at 1R after charges"))

    def test_a_2r_target_is_judged_against_its_break_even(self):
        mc = {"p_target": 0.241, "p_stop": 0.505, "p_neither": 0.254, "r_at_target": 2.0, "avg_R": 0.095}
        self.assertEqual(tm.verdict(mc), "Close to break-even: at 2R the target has to come first in 33 % of the "
                                         "replays that hit one side; it did in 32 % (target first 24.1 % vs stop "
                                         "first 50.5 %).")
        self.assertEqual(tm.verdict_notes(mc), [])
        lean = dict(mc, p_target=0.15, p_stop=0.55)
        self.assertTrue(tm.verdict(lean).startswith("Leans toward the stop: at 2R the target has to come first in "
                                                    "33 % of the replays that hit one side; it did in 21 %"))
        self.assertEqual(tm.verdict_notes(lean), ["Average R is +0.095 all the same: the replays that hit neither "
                                                  "pull it the other way."])
        self.assertTrue(tm.verdict(dict(mc, p_target=0.30, p_stop=0.40)).startswith("Leans toward the target"))
        self.assertTrue(tm.verdict(dict(mc, p_target=0.40, p_stop=0.40)).startswith("Strongly toward the target"))
        self.assertIn("at 1.5R the target has to come first in 40 %", tm.verdict(dict(mc, r_at_target=1.5)))

    def test_2r_on_the_fixture(self):
        # stop 1306.00, 2R target 1323.70
        doc = machine(plan(stop=1306.0, target=1323.7, stop_rule="pct"))
        m = doc["mc"]
        self.assertEqual((m["r_at_target"], m["p_target"], m["p_stop"], m["avg_R_gross"], m["avg_R_net"]),
                         (2.0, 0.2144, 0.6526, -0.157, -0.341))
        self.assertEqual(doc["verdict"], "Leans toward the stop: at 2R after charges the target has to come first in "
                                         "39 % of the replays that hit one side; it did in 25 % (target first 21.4 % "
                                         "vs stop first 65.3 %).")
        self.assertEqual(doc["verdict_notes"], [])

    def test_2r_on_the_fixture_with_many_neither(self):
        doc = machine(plan(target=1334.1))
        m = doc["mc"]
        self.assertEqual((m["r_at_target"], m["p_target"], m["p_stop"], m["p_neither"]),
                         (2.0, 0.0423, 0.394, 0.5637))
        self.assertEqual(doc["verdict"], "Strongly toward the stop: at 2R after charges the target has to come first "
                                         "in 37 % of the replays that hit one side; it did in 10 % (target first "
                                         "4.2 % vs stop first 39.4 %).")
        self.assertEqual(doc["verdict_notes"],
                         ["56.4 % of the replays hit neither by 3:10 PM; the price at 3:10 PM decides those."])
        text = "\n".join(tm.report(doc))
        self.assertIn(doc["verdict"] + "\n" + doc["verdict_notes"][0], text)


class Network(unittest.TestCase):
    def setUp(self):
        self.calls, self.sleeps = [], []
        self._get, self._sleep = tm._get, tm._sleep
        tm._sleep = self.sleeps.append

    def tearDown(self):
        tm._get, tm._sleep = self._get, self._sleep

    def answer(self, codes):
        codes = list(codes)

        def fake(url, timeout=20):
            self.calls.append(url.split(".finance")[0].split("//")[1])
            self.urls.append(url)
            code = codes.pop(0)
            if code != 200:
                raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))
            return TEXT
        self.urls = []
        tm._get = fake

    def test_url_and_user_agent(self):
        self.assertEqual(tm.chart_url("reliance"),
                         "https://query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?range=60d&interval=5m")
        self.assertIn("/chart/M%26M.NS?", tm.chart_url("m&m", "query1"))
        self.assertIn("/chart/TCS.BO?", tm.chart_url("TCS.BO"))
        self.assertIn("day-trader-skill/3.0", tm.UA)

    def test_429_waits_retries_then_falls_back_to_query1(self):
        self.answer([429, 429, 200])
        self.assertEqual(tm.fetch_chart("RELIANCE"), TEXT)
        self.assertEqual(self.calls, ["query2", "query2", "query1"])
        self.assertEqual(self.sleeps, [tm.BACKOFF, tm.PAUSE])
        self.assertTrue(all("/RELIANCE.NS?" in u for u in self.urls))

    def test_one_429_then_ok(self):
        self.answer([429, 200])
        self.assertEqual(tm.fetch_chart("RELIANCE.NS"), TEXT)
        self.assertEqual(self.calls, ["query2", "query2"])

    def test_5xx_falls_back(self):
        self.answer([503, 200])
        self.assertEqual(tm.fetch_chart("RELIANCE.NS"), TEXT)
        self.assertEqual(self.calls, ["query2", "query1"])

    def test_rate_limited_everywhere_is_one_plain_line(self):
        self.answer([429, 429, 429, 429])
        with self.assertRaises(tm.TMError) as cm:
            tm.fetch_chart("RELIANCE.NS")
        self.assertIn("rate limiting", str(cm.exception))

    def test_404(self):
        self.answer([404])
        with self.assertRaises(tm.TMError) as cm:
            tm.fetch_chart("ZZZZ")
        self.assertIn("ZZZZ.NS", str(cm.exception))
        self.assertIn("check the NSE symbol", str(cm.exception))

    def test_run_over_the_network_path(self):
        self.answer([200])
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "plan.json")
            with open(p, "w") as fh:
                json.dump(PLAN, fh)
            code, text, _ = run(["run", "--plan", p, "--now", NOW, "--replays", "300",
                                 "--config", os.path.join(d, "none.json")])
            self.assertEqual(code, 0)
            self.assertEqual(self.calls, ["query2"])
        finally:
            shutil.rmtree(d)


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.plan = os.path.join(self.d, "trader", "plan.json")
        os.makedirs(os.path.dirname(self.plan))
        with open(self.plan, "w", encoding="utf-8") as fh:
            json.dump(PLAN, fh, indent=2)
        self.out = os.path.join(self.d, "trader", "timemachine.json")
        self.cfg = os.path.join(self.d, "trader", "config.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_run_with_the_plan_end_to_end(self):
        code, text, err = run(["run", "--plan", self.plan, "--offline", REL, "--now", NOW, "--write", self.out,
                               "--config", self.cfg])
        self.assertEqual(code, 0, err)
        self.assertIn("target first   27.0 %", text)
        self.assertIn("Average R: -0.121 gross, -0.219 net of charges", text)
        self.assertIn("Wrote %s." % self.out, text)
        self.assertIn("/trader-budget", text)
        with open(self.out, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(sorted(k for k in ("plan", "mc", "history", "charges") if k in doc),
                         ["charges", "history", "mc", "plan"])
        self.assertEqual(doc["created_by"], "trader-timemachine")
        self.assertEqual(doc["source"], "offline")
        self.assertEqual((doc["mc"]["p_target"], doc["mc"]["avg_R_gross"], doc["mc"]["avg_R_net"]),
                         (0.2703, -0.121, -0.219))
        self.assertEqual(sha(doc["mc"]["sample_paths"]), SIM_PATHS)
        self.assertEqual((doc["history"]["triggered"], doc["history"]["avg_R_net"]), (28, -0.157))
        self.assertEqual(doc["plan"]["from"], self.plan)

    def test_the_config_is_read(self):
        with open(self.cfg, "w", encoding="utf-8") as fh:
            json.dump({"max_loss_per_trade_inr": 1000, "flat_by": "14:30",
                       "charges": {"brokerage_max_per_order": 0}}, fh)
        plan_no_flat = dict(PLAN)
        del plan_no_flat["flat_by"]
        with open(self.plan, "w", encoding="utf-8") as fh:
            json.dump(plan_no_flat, fh)
        code, text, err = run(["run", "--plan", self.plan, "--offline", REL, "--now", NOW, "--json",
                               "--config", self.cfg, "--replays", "300"])
        self.assertEqual(code, 0, err)
        doc = json.loads(text)
        self.assertEqual((doc["mc"]["until"], doc["mc"]["until_from"]), ("14:30", "config flat_by"))
        self.assertEqual((doc["charges"]["qty"], doc["charges"]["rates_from"]), (90, "config"))
        self.assertEqual(doc["charges"]["rates"]["brokerage_max_per_order"], 0)
        code, text, err = run(["run", "--plan", self.plan, "--offline", REL, "--now", NOW, "--json",
                               "--config", self.cfg, "--replays", "300", "--qty", "25"])
        self.assertEqual(json.loads(text)["charges"]["qty"], 25)

    def test_json_output(self):
        code, text, _ = run(["run", "--plan", self.plan, "--offline", REL, "--now", NOW, "--json",
                             "--config", self.cfg])
        self.assertEqual(code, 0)
        doc = json.loads(text)
        self.assertEqual(doc["mc"]["p_stop"], 0.3891)
        self.assertNotIn("sample_paths", doc["mc"])
        self.assertEqual(doc["verdict"][:21], "Leans toward the stop")
        self.assertFalse(os.path.exists(self.out))

    def test_the_plan_as_flags_matches_the_file(self):
        code, text, err = run(["run", "--symbol", "reliance", "--date", "2026-11-06", "--entry", "1311.90",
                               "--stop", "1300.80", "--target", "1323.00", "--entry-time", "9:50 AM",
                               "--offline", REL, "--now", NOW, "--json", "--config", self.cfg])
        self.assertEqual(code, 0, err)
        doc = json.loads(text)
        self.assertEqual((doc["mc"]["p_target"], doc["mc"]["p_green"]), (0.2703, 0.4414))
        self.assertEqual(doc["mc"]["until_from"], "default flat_by")
        self.assertEqual(doc["plan"]["stop_rule"], "or_mid")
        self.assertEqual((doc["history"]["target"], doc["history"]["stop"], doc["history"]["avg_R_gross"]),
                         (13, 10, 0.057))
        code, _, err = run(["run", "--symbol", "RELIANCE", "--entry", "1311.90", "--offline", REL])
        self.assertEqual(code, 1)
        self.assertIn("--stop", err)

    def test_offline_folder_and_default_paths(self):
        shutil.copy(REL, self.d)
        cwd = os.getcwd()
        os.chdir(self.d)
        try:
            code, text, err = run(["run", "--offline", ".", "--now", NOW, "--replays", "500", "--write"])
            self.assertEqual(code, 0, err)
            self.assertTrue(os.path.exists(os.path.join("trader", "timemachine.json")))
            code, text, err = run(["chart", "--from"])
            self.assertEqual(code, 0, err)
            self.assertIn("* median", text)
            with open(os.path.join("trader", "timemachine.json"), encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["mc"]["replays"], 500)
        finally:
            os.chdir(cwd)

    def test_errors_are_one_plain_line(self):
        for argv in (["run", "--plan", os.path.join(self.d, "nope.json")],
                     ["run", "--plan", self.plan, "--offline", os.path.join(self.d, "missing.json.gz")],
                     ["run", "--plan", self.plan, "--offline", REL, "--until", "9:00"],
                     ["run", "--plan", self.plan, "--offline", REL, "--until", "15:30", "--now", NOW],
                     ["chart", "--from", os.path.join(self.d, "nope.json")]):
            code, text, err = run(argv)
            self.assertEqual(code, 1, argv)
            self.assertEqual(len(err.strip().splitlines()), 1, err)
            self.assertTrue(err.startswith("timemachine.py: "), err)
            self.assertNotIn("Traceback", err)
        self.assertEqual(run([])[0], 1)

    def test_chart(self):
        run(["run", "--plan", self.plan, "--offline", REL, "--now", NOW, "--write", self.out, "--config", self.cfg])
        code, text, _ = run(["chart", "--from", self.out])
        self.assertEqual(code, 0)
        lines = text.splitlines()
        self.assertIn("2,000 replay paths from the 9:50 AM IST entry (seed 19)", lines[0])
        self.assertTrue(any(l.startswith("  1323.00 |") and l.endswith("  target") for l in lines))
        self.assertTrue(any(l.startswith("  1300.80 |") and l.endswith("  stop") for l in lines))
        self.assertTrue(any(l.startswith("  1311.90 |*") and l.endswith("  entry") for l in lines))
        self.assertIn("9:50", text)
        self.assertIn("3:10", text)
        self.assertEqual(lines[-1], "Leans toward the stop: at 1R after charges the target has to come first in 55 % "
                                    "of the replays that hit one side; it did in 41 % (target first 27.0 % vs stop "
                                    "first 38.9 %).")
        code, again, _ = run(["chart", "--plan", self.plan, "--offline", REL, "--now", NOW, "--config", self.cfg])
        self.assertEqual(again, text)


if __name__ == "__main__":
    unittest.main()

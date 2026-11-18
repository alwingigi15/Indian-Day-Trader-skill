"""trader-exit tests. Bars for the ATR stop reuse tests/fixtures/trader-entry/
(RELIANCE.NS's real Mon 2 Nov 2026 five-minute bars, see test_entry.py): the
opening range 9:15-9:30 is 1214.60 to 1231.60 and the 9:30 bar closes above it
at 1234.50 (the entry at 9:35 AM IST, bar 3). RELIANCE trades between ₹1,000
and ₹5,000, so its NSE tick is ₹0.10."""

import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal

from _load import load, FIX, ROOT

exitp = load("trader-exit", "exit")
entry = load("trader-entry", "entry")

REL = os.path.join(FIX, "trader-entry", "RELIANCE.NS-2026-11-02-5m.json")

# trader/plan.json for the example, exactly as the contract writes it
PLAN = {"created_by": "trader-exit", "symbol": "RELIANCE.NS", "side": "buy", "date": "2026-11-02", "setup": "orb",
        "entry": 1234.5, "entry_time": "9:35 AM", "entry_bar": 3, "interval": "5m",
        "opening_range": {"high": 1231.6, "low": 1214.6, "minutes": 15},
        "stop": 1223.1, "stop_rule": "or_mid", "stop_label": "opening-range midpoint",
        "target": 1245.9, "target_r": 1.0, "target_label": "1R (one times the risk)",
        "risk_per_share": 11.4, "reward_risk": 1.0, "flat_by": "15:10", "tick_size": 0.1}
ENTRY = {"created_by": "trader-entry", "symbol": "RELIANCE.NS", "setup": "orb", "side": "buy", "date": "2026-11-02",
         "status": "triggered", "time": "9:35 AM", "bar_start": "09:30", "bar_index": 3, "price": 1234.5,
         "level": 1231.6, "interval": "5m", "opening_range": {"high": 1231.6, "low": 1214.6, "minutes": 15},
         "source": "yahoo", "checked_at": "2026-11-02T04:06:00Z"}


def run(mod, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = mod.main(argv)
    return code, out.getvalue(), err.getvalue()


def c(entry_px, stop, target="2R", oh=1231.6, ol=1214.6, atr=None, n=None):
    sr, sp = exitp.parse_stop(stop)
    tr, tp = exitp.parse_target(target)
    return exitp.compute(entry_px, sr, sp, tr, tp, oh, ol, atr, n)


class Ticks(unittest.TestCase):
    def test_tick_table(self):
        for price, tick in ((99, "0.01"), (249.99, "0.01"), (250, "0.05"), (999.95, "0.05"), (1000, "0.10"),
                            (4999.9, "0.10"), (5000, "0.50"), (10000, "1.00"), (20000, "5.00")):
            self.assertEqual(exitp.tick_size(price), Decimal(tick), price)

    def test_round_to_tick(self):
        self.assertEqual(exitp.tick_down(1400.07), Decimal("1400.00"))
        self.assertEqual(exitp.tick_up(1400.01), Decimal("1400.10"))
        self.assertEqual(exitp.tick_down(249.987), Decimal("249.98"))
        self.assertEqual(exitp.tick_down(612.34), Decimal("612.30"))
        self.assertEqual(exitp.tick_up(612.31), Decimal("612.35"))

    def test_session_and_holidays(self):
        self.assertEqual(exitp.session(datetime.date(2026, 11, 9)),
                         (datetime.time(9, 15), datetime.time(15, 30)))
        self.assertIsNone(exitp.session(datetime.date(2026, 11, 10)))     # Diwali Balipratipada
        self.assertIsNone(exitp.session(datetime.date(2026, 11, 8)))      # Sunday (Muhurat not modelled)

    def test_ist(self):
        utc = datetime.datetime(2026, 11, 2, 4, 5)
        self.assertEqual(exitp.to_ist(utc), datetime.datetime(2026, 11, 2, 9, 35))
        self.assertEqual(exitp.ist_iso(utc), "2026-11-02T09:35:00+05:30")
        self.assertEqual(exitp.parse_now("2026-11-02 09:35"), utc)          # no offset = IST

    def test_symbols(self):
        self.assertEqual(exitp.nse_symbol("reliance"), "RELIANCE.NS")
        self.assertEqual(exitp.yahoo_symbol("M&M"), "M%26M.NS")


class Math(unittest.TestCase):
    def test_or_mid_1r(self):
        p = c(1234.5, "or-mid", "1R")
        self.assertEqual(p["stop"], Decimal("1223.10"))          # (1231.60 + 1214.60) / 2, already on the tick
        self.assertEqual(p["risk"], Decimal("11.40"))
        self.assertEqual(p["target"], Decimal("1245.90"))
        self.assertEqual(p["reward_risk"], Decimal("1.00"))
        self.assertEqual(p["tick"], Decimal("0.10"))
        self.assertEqual(p["target_label"], "1R (one times the risk)")

    def test_the_default_2r(self):
        p = c(1234.5, "or-mid")
        self.assertEqual(p["target"], Decimal("1257.30"))
        self.assertEqual(p["target_label"], "2R (two times the risk)")

    def test_raw_yahoo_floats_land_on_ticks(self):
        p = c(1234.4999389648438, "or-mid", "1R", 1231.5999755859375, 1214.5999755859375)
        self.assertEqual((p["entry"], p["stop"], p["target"]), (Decimal("1234.50"), Decimal("1223.10"), Decimal("1245.90")))

    def test_entry_rounds_up_to_a_tick(self):
        self.assertEqual(c(1234.47, "pct:1")["entry"], Decimal("1234.50"))

    def test_other_stops(self):
        p = c(1234.5, "or-low")
        self.assertEqual((p["stop"], p["risk"], p["target"]), (Decimal("1214.60"), Decimal("19.90"), Decimal("1274.30")))
        p = c(1234.5, "price:1223.19")
        self.assertEqual(p["stop"], Decimal("1223.10"))            # down to the tick, away from the entry
        self.assertIn("rounded down", p["stop_detail"])
        p = c(1234.5, "pct:1")                                     # 1222.155 -> 1222.10
        self.assertEqual((p["stop"], p["risk"], p["target"]), (Decimal("1222.10"), Decimal("12.40"), Decimal("1259.30")))
        p = c(1234.5, "atr:1.5", atr=10.75, n=4)                   # 1234.50 - 16.125 = 1218.375 -> 1218.30
        self.assertEqual(p["stop"], Decimal("1218.30"))
        self.assertEqual(p["stop_label"], "1.5 x ATR(4) under the entry")

    def test_targets_round_down_and_price_targets(self):
        p = c(100, "price:99.01", "1.5R", None, None)
        self.assertEqual(p["target"], Decimal("101.48"))            # 100 + 1.485, down to the 0.01 tick
        self.assertEqual(p["target_label"], "1.5R (1.5 times the risk)")
        p = c(1234.5, "or-mid", "price:1250")
        self.assertEqual(p["reward_risk"], Decimal("1.36"))         # 15.50 / 11.40
        self.assertEqual(p["target_label"], "your price (1.36R)")
        p = c(1234.5, "or-mid", "price:1250.07")
        self.assertEqual(p["target"], Decimal("1250.00"))

    def test_refusals_say_why(self):
        cases = [("price:1234.50", "2R", "at or above the entry"),
                 ("price:1240", "2R", "at or above the entry"),
                 ("or-mid", "price:1234", "at or below the entry"),
                 ("price:1234.10", "2R", "too tight to survive the spread"),
                 ("pct:0.04", "2R", "too tight to survive the spread")]
        for stop, target, why in cases:
            with self.assertRaises(exitp.PlanError) as cm:
                c(1234.5, stop, target)
            self.assertIn(why, str(cm.exception), stop)
        # 0.05% of 1234.50 is 0.617: a ₹0.70 risk is allowed, ₹0.60 is not
        self.assertEqual(c(1234.5, "price:1233.80")["risk"], Decimal("0.70"))
        with self.assertRaises(exitp.PlanError):
            c(1234.5, "price:1233.90")
        with self.assertRaises(exitp.PlanError):
            c(1220, "or-mid")                                      # the midpoint is above this entry
        for bad in ("mid-ish", "pct:100", "price:0", "atr:-1"):
            with self.assertRaises(exitp.PlanError):
                exitp.parse_stop(bad)
        for bad in ("0R", "R", "lots", "price:x"):
            with self.assertRaises(exitp.PlanError):
                exitp.parse_target(bad)
        with self.assertRaises(exitp.PlanError):
            c(1234.5, "or-mid", oh=None, ol=None)
        with self.assertRaises(exitp.PlanError):
            c(1234.5, "or-mid", oh=1214.6, ol=1231.6)

    def test_two_tick_minimum(self):
        self.assertEqual(exitp.MIN_RISK_TICKS, 2)
        saved = exitp.MIN_RISK_PCT
        exitp.MIN_RISK_PCT = Decimal("0")                          # isolate the tick rule
        try:
            with self.assertRaises(exitp.PlanError) as cm:
                c(1234.5, "price:1234.40")                         # one ₹0.10 tick of risk
            self.assertIn("less than 2 ticks", str(cm.exception))
            self.assertEqual(c(1234.5, "price:1234.30")["risk"], Decimal("0.20"))
        finally:
            exitp.MIN_RISK_PCT = saved

    def test_atr_on_the_session_bars(self):
        ch = exitp.load_chart("RELIANCE.NS", "5m", None, REL)
        day = datetime.date(2026, 11, 2)
        bars = exitp.session_bars(ch, day, "5m", upto_slot=3)
        self.assertEqual(len(bars), 4)                              # 9:15 ... the 9:30 entry bar
        trs, prev = [], None
        for b in bars:
            r = b["h"] - b["l"]
            if prev is not None:
                r = max(r, abs(b["h"] - prev), abs(b["l"] - prev))
            trs.append(r)
            prev = b["c"]
        atr, n = exitp.atr_of(bars)
        self.assertEqual(n, 4)
        self.assertAlmostEqual(atr, sum(trs) / 4, places=9)
        self.assertAlmostEqual(atr, 10.75, places=2)
        all_bars = exitp.session_bars(ch, day, "5m")
        self.assertGreater(len(all_bars), 14)
        self.assertLessEqual(len(all_bars), 75)                     # 9:15-15:30 is 75 five-minute bars
        self.assertEqual(exitp.atr_of(all_bars)[1], 14)


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.mkdir("trader")

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def write(self, path, doc):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

    def plan(self, *args):
        return run(exitp, ["plan", "--now", "2026-11-02 09:37"] + list(args))

    def read_plan(self):
        with open(os.path.join("trader", "plan.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def test_entry_to_plan_end_to_end(self):
        code, _, _ = run(entry, ["check", "RELIANCE.NS", "--offline", REL, "--now", "2026-11-02 09:36", "--write"])
        self.assertEqual(code, 0)
        code, text, err = self.plan("--stop", "or-mid", "--target", "1R", "--write")
        self.assertEqual(code, 0, err)
        p = self.read_plan()
        for k, v in PLAN.items():
            self.assertEqual(p[k], v, k)
        self.assertTrue(p["created_at_ist"].endswith("+05:30"))
        self.assertIn("Stop          ₹1,223.10  opening-range midpoint", text)
        self.assertIn("Target        ₹1,245.90  1R (one times the risk)", text)
        self.assertIn("Risk a share  ₹11.40 (0.92% of the entry)", text)
        self.assertIn("Reward:risk   1.0", text)
        self.assertIn("Flat by       15:10 IST", text)
        self.assertIn("not a guarantee", text)
        self.assertIn("/trader-timemachine", text)
        self.assertNotIn("$", text)
        self.assertNotIn("pro" + "fit", text.lower())   # never on screen
        code, text, _ = self.plan("--write")                       # the defaults: or-mid, 2R
        self.assertEqual(self.read_plan()["target"], 1257.3)
        self.assertEqual(self.read_plan()["target_r"], 2.0)
        code, text, _ = run(exitp, ["show", "--now", "2026-11-02 09:40"])
        self.assertEqual(code, 0)
        self.assertIn("₹1,257.30", text)
        self.assertNotIn("not today", text)
        code, text, _ = run(exitp, ["show", "--now", "2026-11-03 09:40"])
        self.assertIn("not today", text)

    def test_manual_flags_and_json(self):
        code, text, _ = self.plan("--symbol", "reliance", "--entry", "1234.50", "--or-high", "1231.60", "--or-low",
                                  "1214.60", "--time", "9:35", "--entry-bar", "3", "--date", "2026-11-02",
                                  "--target", "1R", "--json")
        self.assertEqual(code, 0)
        p = json.loads(text)
        for k, v in PLAN.items():
            self.assertEqual(p[k], v, k)
        self.assertEqual(p["warnings"], [])

    def test_symbol_alone_fetches_the_entry(self):
        base = ("--symbol", "RELIANCE", "--date", "2026-11-02", "--offline", REL, "--json")
        code, text, err = self.plan(*base)                         # whole day: the 9:30 bar triggered
        self.assertEqual(code, 0, err)
        p = json.loads(text)
        self.assertEqual((p["entry"], p["entry_time"], p["entry_bar"], p["stop"]), (1234.5, "9:35 AM", 3, 1223.1))
        self.assertEqual(p["opening_range"], {"high": 1231.6, "low": 1214.6, "minutes": 15})
        self.assertIn("TRIGGERED", p["warnings"][0])
        code, text, err = self.plan(*(base + ("--now", "2026-11-02 09:32")))   # range set, no breakout yet
        self.assertEqual(code, 0, err)
        p = json.loads(text)
        self.assertEqual((p["entry"], p["entry_time"], p["stop"]), (1231.6, None, 1223.1))
        self.assertIn("plans ahead at the range high", p["warnings"][0])
        code, _, err = self.plan(*(base + ("--now", "2026-11-02 09:25")))      # range still forming
        self.assertEqual(code, 1)
        self.assertIn("opening range is not set yet", err)

    def test_atr_stop_reads_bars(self):
        self.write(os.path.join("trader", "entry.json"), ENTRY)
        code, text, err = self.plan("--stop", "atr:1.5", "--offline", REL, "--json")
        self.assertEqual(code, 0, err)
        p = json.loads(text)
        self.assertEqual((p["stop"], p["risk_per_share"], p["target"]), (1218.3, 16.2, 1266.9))
        self.assertEqual((p["stop_rule"], p["atr_bars"]), ("atr", 4))
        self.assertAlmostEqual(p["atr"], 10.75, places=2)
        code, _, err = self.plan("--stop", "atr:1.5", "--offline", REL, "--entry-bar", "1")
        self.assertEqual(code, 1)
        self.assertIn("at least 3 closed", err)

    def test_refusals_from_the_cli(self):
        self.write(os.path.join("trader", "entry.json"), dict(ENTRY, status="waiting", time=None, price=None,
                                                              bar_index=None, bar_start=None))
        code, text, err = self.plan("--json")                      # waiting: plans ahead at the level by itself
        self.assertEqual(code, 0, err)
        p = json.loads(text)
        self.assertEqual((p["entry"], p["stop"], p["entry_time"]), (1231.6, 1223.1, None))
        self.assertIn("plans ahead at the breakout level", p["warnings"][0])
        self.write(os.path.join("trader", "entry.json"), dict(ENTRY, status="expired", time=None, price=None))
        code, _, err = self.plan()
        self.assertEqual(code, 1)
        self.assertIn("RELIANCE.NS is expired, not triggered", err)
        code, text, _ = self.plan("--entry", "1231.70", "--json")   # plan ahead at a price you choose
        self.assertEqual(code, 0)
        p = json.loads(text)
        self.assertEqual((p["entry"], p["stop"], p["entry_time"]), (1231.7, 1223.1, None))

        self.write(os.path.join("trader", "entry.json"), dict(ENTRY, setup="level", level=1230.0))
        code, _, err = self.plan()
        self.assertIn("level setup has no opening-range stop", err)
        self.assertEqual(self.plan("--stop", "pct:1")[0], 0)

        code, _, err = self.plan("--side", "sell")
        self.assertIn("v2 plans longs only", err)
        self.write(os.path.join("trader", "entry.json"), dict(ENTRY, side="sell"))
        code, _, err = self.plan()
        self.assertIn("longs only", err)
        os.remove(os.path.join("trader", "entry.json"))
        code, _, err = self.plan()
        self.assertIn("no entry", err)
        code, _, err = self.plan("--symbol", "RELIANCE", "--setup", "level")
        self.assertIn("level setup needs --entry", err)
        code, _, err = self.plan("--symbol", "RELIANCE", "--or-high", "1231.60", "--or-low", "1214.60")
        self.assertIn("need --entry PRICE too", err)
        code, _, err = self.plan("--symbol", "RELIANCE", "--from", "x.json", "--entry", "1")
        self.assertIn("not both", err)
        code, _, err = self.plan("--symbol", "RELIANCE", "--entry", "1234.50", "--or-high", "1231.60")
        self.assertIn("both --or-high and --or-low", err)
        self.assertNotIn("Traceback", err)

    def test_flat_by_config_square_off_and_late_entries(self):
        self.write(os.path.join("trader", "entry.json"), ENTRY)
        self.write(os.path.join("trader", "config.json"), {"flat_by": "15:00", "min_reward_risk": 1.5})
        code, text, _ = self.plan("--target", "1R", "--json")
        p = json.loads(text)
        self.assertEqual(p["flat_by"], "15:00")
        self.assertTrue(any("under your min_reward_risk 1.5" in w for w in p["warnings"]))
        os.remove(os.path.join("trader", "config.json"))

        code, text, _ = self.plan("--flat-by", "15:20")
        self.assertEqual(code, 0)
        self.assertIn("square off", text)                           # past the MIS auto-square-off
        code, _, err = self.plan("--flat-by", "15:45")
        self.assertEqual(code, 1)
        self.assertIn("outside the NSE session", err)
        self.write(os.path.join("trader", "entry.json"), dict(ENTRY, time="3:12 PM"))
        code, _, err = self.plan()
        self.assertEqual(code, 1)
        self.assertIn("no time left to hold it", err)

    def test_show_without_a_plan(self):
        code, text, _ = run(exitp, ["show"])
        self.assertEqual(code, 1)
        self.assertIn("No plan", text)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-exit", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join(ROOT, "trader-risk", "SKILL.md"), encoding="utf-8") as fh:
            risk = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-exit\n"))
        self.assertGreater(len(text.splitlines()), 60)
        self.assertEqual(text[text.index("## Safety"):], risk[risk.index("## Safety"):])
        low = " ".join(text.lower().split())
        for must in ("before the entry", "a stop is a plan, not a guarantee", "day trading loses most people money",
                     "/trader-timemachine", "/trader-budget", "longs only", "₹1,223.10", "₹1,245.90", "₹11.40",
                     "1257.30", "too tight to survive the spread", "15:10", "tick"):
            self.assertIn(must, low)
        for never in ("\u2014", "tick " + "by tick", "real " + "time", "pro" + "fit", "new york", "nyse"):
            self.assertNotIn(never, low)
        self.assertNotRegex(low, r"\$\d")                          # no dollar amounts


if __name__ == "__main__":
    unittest.main()

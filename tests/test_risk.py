"""trader-risk tests. The example order is trader-budget's real output for a
RELIANCE.NS opening-range breakout on Fri 6 Nov 2026: 57 shares, entry
1311.90, stop 1300.80, target 1334.10 (tests/fixtures/trader-risk/)."""

import copy
import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX, ROOT

check = load("trader-risk", "check")
ticket = load("trader-broker", "ticket")

RDIR = os.path.join(FIX, "trader-risk")
ORDERS = os.path.join(RDIR, "orders-reliance.json")
PRICES = os.path.join(RDIR, "prices-reliance.json")
with open(os.path.join(ROOT, "trader-budget", "config.example.json"), encoding="utf-8") as _fh:
    CFG = json.load(_fh)
NOW = check.parse_now("2026-11-06 09:52")           # just after the 9:50 entry, inside 09:30-14:45


def rel():
    with open(ORDERS, encoding="utf-8") as fh:
        return copy.deepcopy(json.load(fh)["orders"][0])


def buy(sym="TCS.NS", qty=10, price=2510.0, stop=2490.0, target=2550.0, **kw):
    o = {"symbol": sym, "exchange": "NSE", "tradingsymbol": sym.split(".")[0], "side": "BUY", "qty": qty,
         "order_type": "MARKET", "product": "MIS", "validity": "DAY", "stop_loss": stop, "target": target,
         "plan": {"entry": price, "stop": stop, "target": target}}
    o.update(kw)
    return o


def sell(sym="RELIANCE.NS", qty=57, **kw):
    o = {"symbol": sym, "exchange": "NSE", "tradingsymbol": sym.split(".")[0], "side": "SELL", "qty": qty,
         "order_type": "MARKET", "product": "MIS", "validity": "DAY"}
    o.update(kw)
    return o


def run_check(orders, cfg=None, now=NOW, **kw):
    return check.check(orders, dict(CFG, **(cfg or {})), now=now, **kw)


def one(orders, **kw):
    return run_check(orders if isinstance(orders, list) else [orders], **kw)["results"][0]


def has(reasons, prefix):
    return any(r.startswith(prefix) for r in reasons)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = check.main(argv)
    return code, out.getvalue(), err.getvalue()


class Basics(unittest.TestCase):
    def test_charges_by_hand(self):
        # 57 x 1311.90 = 74,778.30 bought, 57 x 1300.80 = 74,145.60 sold
        # brokerage 20 + 20 (0.03% is above the ₹20 cap), STT 0.025% of the sell = 18.536,
        # exchange 0.00307% of 148,923.90 = 4.572, SEBI ₹10/crore = 0.149, stamp 0.003% of the buy = 2.243,
        # GST 18% of (40 + 4.572 + 0.149) = 8.050 -> 73.55
        self.assertEqual(check.intraday_charges(57, 1311.9, 1300.8), 73.55)
        # a small order: brokerage is 0.03%, not ₹20
        self.assertEqual(check.intraday_charges(1, 1000, 1000), round(
            0.3 * 2 + 0.25 + 2000 * 0.0000307 + 2000 * 1e-6 + 0.03 + (0.6 + 2000 * 0.0000307 + 0.002) * 0.18, 2))
        cheaper = check.intraday_charges(57, 1311.9, 1300.8, {"brokerage_max_per_order": 0, "brokerage_pct": 0})
        self.assertLess(cheaper, 73.55)

    def test_ticks(self):
        for p in (1311.9, 1300.8, 249.99, 612.35, 5000.5, 20005):
            self.assertTrue(check.on_tick(p), p)
        for p in (1311.93, 612.31, 5000.2, 20003):
            self.assertFalse(check.on_tick(p), p)

    def test_symbols_and_fno(self):
        for s in ("RELIANCE", "RELIANCE.NS", "M&M.NS", "BAJAJ-AUTO.NS", "3MINDIA.NS", "TCS.BO"):
            self.assertTrue(check.TICKER.match(s), s)
        for s in ("NIFTY25NOV24000CE", "BANKNIFTY25DEC52000PE", "RELIANCE25NOVFUT", "NIFTY2511124000CE"):
            self.assertTrue(check.FNO.match(s), s)
        self.assertFalse(check.FNO.match("RELIANCE"))
        self.assertFalse(check.FNO.match("3MINDIA"))

    def test_ist(self):
        self.assertEqual(check.parse_now("2026-11-06 09:52"), datetime.datetime(2026, 11, 6, 4, 22))
        self.assertEqual(check.ist_iso(datetime.datetime(2026, 11, 6, 4, 22)), "2026-11-06T09:52:00+05:30")


class TheExample(unittest.TestCase):
    def test_the_reliance_order_passes_every_check(self):
        r = one(rel())
        self.assertEqual(r["verdict"], "PASS", r["reasons"])
        self.assertEqual((r["symbol"], r["kind"], r["product"]), ("RELIANCE.NS", "entry", "MIS"))
        self.assertEqual((r["entry_ref"], r["stop"], r["target"], r["risk_per_share"]), (1311.9, 1300.8, 1334.1, 11.1))
        self.assertEqual(r["charges_inr"], 73.55)
        self.assertEqual(r["max_loss"], 706.25)                  # 57 x 11.10 = 632.70 + 73.55
        self.assertEqual(r["reward_risk"], 2.0)
        self.assertIn("max loss ₹706.25 incl. ₹73.55 charges (limit ₹1,000), reward:risk 2.00 (min 1.5)", r["info"])

    def test_more_shares_is_blocked_by_the_max_loss(self):
        o = rel()
        o["qty"] = 85                                            # 85 x 11.10 = 943.50 + charges > 1,000
        r = one(o, cfg={"max_order_inr": 200000, "max_position_pct": 100, "budget": 200000})
        self.assertEqual(r["verdict"], "BLOCK")
        self.assertEqual([x.split(":")[0] for x in r["reasons"]], ["loss"])
        self.assertIn("charges", r["reasons"][0])

    def test_fingerprint_is_shared_with_the_broker(self):
        o = rel()
        self.assertEqual(check.ORDER_FIELDS, ticket.ORDER_FIELDS)
        self.assertEqual(check.fingerprint(o), ticket.fingerprint(o))
        self.assertEqual(check.fingerprint(dict(o, why="anything", plan={})), check.fingerprint(o))
        self.assertNotEqual(check.fingerprint(dict(o, qty=58)), check.fingerprint(o))
        self.assertNotEqual(check.fingerprint(dict(o, stop_loss=1300.7)), check.fingerprint(o))


class IndiaRules(unittest.TestCase):
    def test_no_pdt_check(self):
        self.assertNotIn("pdt_guard", check.DEFAULTS)
        state = {"orders": [{"status": "placed", "date": "2026-11-06", "symbol": s, "side": "buy", "kind": "entry",
                             "max_loss": 1} for s in ("A.NS", "B.NS")]}
        r = one(rel(), cfg={"max_trades_per_day": 0, "max_daily_loss_inr": 0}, state=state)
        self.assertEqual(r["verdict"], "PASS", r["reasons"])
        self.assertFalse(any("pdt" in x.lower() or "pattern" in x.lower() for x in r["reasons"] + r["warnings"]))

    def test_off_tick_prices_are_blocked(self):
        o = rel()
        o["stop_loss"] = 1300.83
        r = one(o)
        self.assertEqual(r["verdict"], "BLOCK")
        self.assertTrue(any("not on the NSE tick of 0.10" in x for x in r["reasons"]), r["reasons"])
        o = dict(buy(), order_type="LIMIT", price=2510.03)
        self.assertTrue(has(one(o)["reasons"], "format: price 2510.03 is not on the NSE tick"))

    def test_trade_for_trade_series_is_blocked(self):
        for series in ("BE", "BZ"):
            r = one(dict(rel(), series=series))
            self.assertTrue(has(r["reasons"], "series:"), series)
        self.assertEqual(one(dict(rel(), series="EQ"))["verdict"], "PASS")

    def test_entry_window_in_ist(self):
        self.assertTrue(has(one(rel(), now=check.parse_now("2026-11-06 09:20"))["reasons"],
                            "window: no new entries before 09:30"))
        self.assertEqual(one(rel(), now=check.parse_now("2026-11-06 09:30"))["verdict"], "PASS")
        self.assertEqual(one(rel(), now=check.parse_now("2026-11-06 14:45"))["verdict"], "PASS")
        self.assertTrue(has(one(rel(), now=check.parse_now("2026-11-06 14:46"))["reasons"],
                            "window: no new entries after 14:45"))
        self.assertTrue(has(one(rel(), now=check.parse_now("2026-11-06 09:10"))["reasons"],
                            "window: the market is closed"))
        self.assertTrue(has(one(rel(), now=check.parse_now("2026-11-07 10:00"))["reasons"],
                            "window: the market is closed today (weekend"))
        r = one(rel(), now=check.parse_now("2026-11-10 10:00"))
        self.assertTrue(any("Diwali Balipratipada" in x for x in r["reasons"]))

    def test_the_window_uses_ist_not_the_machine(self):
        # 04:22 UTC is 09:52 in India whatever the computer's own time zone
        self.assertEqual(one(rel(), now=datetime.datetime(2026, 11, 6, 4, 22))["verdict"], "PASS")

    def test_mis_cutoff(self):
        cfg = {"no_entries_after": None}
        self.assertEqual(one(rel(), cfg=cfg, now=check.parse_now("2026-11-06 14:59"))["verdict"], "PASS")
        r = one(rel(), cfg=cfg, now=check.parse_now("2026-11-06 15:00"))
        self.assertTrue(has(r["reasons"], "mis cutoff:"))
        self.assertEqual(one(dict(rel(), product="CNC"), cfg=cfg, now=check.parse_now("2026-11-06 15:00"))["verdict"],
                         "PASS")

    def test_circuit_band(self):
        o = dict(rel(), upper_circuit=1300.0, lower_circuit=1180.0)
        self.assertTrue(has(one(o)["reasons"], "circuit:"))
        near = dict(rel(), upper_circuit=1320.0, lower_circuit=1080.0)
        r = one(near)
        self.assertEqual(r["verdict"], "PASS")
        self.assertTrue(any(w.startswith("circuit:") for w in r["warnings"]))
        r = one(rel(), circuits={"RELIANCE.NS": {"upper_circuit": 1300.0, "lower_circuit": 1180.0}})
        self.assertTrue(has(r["reasons"], "circuit:"))

    def test_fno_off_unless_allowed(self):
        o = buy(sym="NIFTY25NOV24000CE", qty=75, price=120.0, stop=100.0, target=160.0)
        self.assertTrue(has(one(o)["reasons"], "asset: NIFTY25NOV24000CE is an NSE F&O contract"))
        r = one(o, cfg={"allow_options": True})
        self.assertFalse(has(r["reasons"], "asset:"))
        self.assertTrue(any("F&O order" in w for w in r["warnings"]))

    def test_symbols_and_junk(self):
        self.assertFalse(has(one(buy(sym="M&M", price=3500.0, stop=3480.0, target=3540.0, qty=5))["reasons"], "asset:"))
        for junk in ("BTC/INR", "reliance industries", "AAPL!"):
            self.assertTrue(has(one(buy(sym=junk))["reasons"], "asset:"), junk)


class DayTradeChecks(unittest.TestCase):
    def test_a_stop_is_required(self):
        o = rel()
        o.pop("stop_loss")
        o["plan"].pop("stop")
        r = one(o)
        self.assertTrue(has(r["reasons"], "stop: no stop"))
        r = one(o, cfg={"require_stop": False})
        self.assertFalse(has(r["reasons"], "stop:"))
        self.assertTrue(any("no stop" in w for w in r["warnings"]))

    def test_a_plan_stop_counts_but_warns(self):
        o = rel()
        o.pop("stop_loss")
        r = one(o)
        self.assertEqual(r["verdict"], "PASS", r["reasons"])
        self.assertTrue(any("in the plan only" in w for w in r["warnings"]))

    def test_stop_and_target_on_the_right_side(self):
        self.assertTrue(has(one(dict(rel(), stop_loss=1320.0))["reasons"], "stop: the stop 1320.00 must be below"))
        self.assertTrue(has(one(dict(rel(), target=1310.0, plan=dict(rel()["plan"], target=1310.0)))["reasons"],
                            "target:"))

    def test_reward_risk(self):
        o = dict(rel(), target=1320.0)                            # (1320 - 1311.9) / 11.1 = 0.73
        r = one(o)
        self.assertTrue(has(r["reasons"], "reward:risk: 0.73"))

    def test_a_fresh_higher_price_is_the_entry(self):
        prices = {"RELIANCE.NS": 1313.8}
        times = {"RELIANCE.NS": NOW - datetime.timedelta(minutes=2)}
        r = one(rel(), prices=prices, price_times=times)
        self.assertEqual(r["entry_ref"], 1313.8)
        self.assertEqual(r["risk_per_share"], 13.0)
        self.assertTrue(any("above the plan's entry" in x for x in r["info"]))

    def test_a_stale_or_lower_price_does_not_change_the_risk(self):
        old = {"RELIANCE.NS": NOW - datetime.timedelta(minutes=30)}
        r = one(rel(), prices={"RELIANCE.NS": 1313.8}, price_times=old)
        self.assertEqual(r["entry_ref"], 1311.9)
        self.assertTrue(any("older than 15 minutes" in w for w in r["warnings"]))
        r = one(rel(), prices={"RELIANCE.NS": 1305.0}, price_times={"RELIANCE.NS": NOW})
        self.assertEqual(r["entry_ref"], 1311.9)

    def test_a_limit_order_uses_its_limit_price(self):
        r = one(dict(rel(), order_type="LIMIT", price=1312.0))
        self.assertEqual((r["entry_ref"], r["entry_ref_from"]), (1312.0, "limit"))
        self.assertTrue(has(one(dict(rel(), order_type="LIMIT"))["reasons"], "format: a LIMIT order needs a price"))
        self.assertTrue(has(one(dict(rel(), order_type="SL-M"))["reasons"], "format: a SL-M order needs a trigger"))

    def test_daily_loss_and_trades_per_day(self):
        state = {"orders": [{"status": "placed", "date": "2026-11-06", "symbol": "TCS.NS", "side": "buy",
                             "kind": "entry", "max_loss": 2500}]}
        r = one(rel(), state=state)
        self.assertTrue(has(r["reasons"], "daily loss: ₹2,500 already at risk today"))
        state["orders"] = [dict(state["orders"][0], symbol=s, max_loss=10) for s in ("A.NS", "B.NS", "C.NS")]
        self.assertTrue(has(one(rel(), state=state)["reasons"], "trades: 3 entries placed today"))

    def test_the_batch_counts_toward_the_daily_limits(self):
        orders = [buy("TCS.NS", qty=10, price=2510.0, stop=2490.0, target=2550.0),
                  buy("INFY.NS", qty=10, price=1171.0, stop=1160.0, target=1193.0),
                  buy("HDFCBANK.NS", qty=10, price=770.45, stop=765.0, target=781.0),
                  buy("SBIN.NS", qty=10, price=1044.2, stop=1040.0, target=1053.0)]
        res = run_check(orders, cfg={"min_reward_risk": 1.0})
        self.assertEqual([r["verdict"] for r in res["results"]], ["PASS", "PASS", "PASS", "BLOCK"])
        self.assertTrue(has(res["results"][3]["reasons"], "trades: 0 entries placed today plus 3 passed"))

    def test_cash_buffer_margin_and_position_cap(self):
        big = buy("TCS.NS", qty=40, price=2510.0, stop=2505.0, target=2520.0)   # ₹1,00,400: over the account
        r = one(big, cfg={"max_order_inr": 500000, "max_loss_per_trade_inr": 100000, "max_daily_loss_inr": 100000})
        self.assertTrue(has(r["reasons"], "cash: needs ₹100,400 but only ₹100,000 cash"))
        self.assertTrue(has(r["reasons"], "position:"))
        mid = buy("TCS.NS", qty=36, price=2510.0, stop=2505.0, target=2520.0)   # ₹90,360 leaves ₹9,640 < ₹10,000
        r = one(mid, cfg={"max_order_inr": 500000, "max_loss_per_trade_inr": 100000, "max_daily_loss_inr": 100000,
                          "max_position_pct": 100})
        self.assertTrue(has(r["reasons"], "cash: leaves"))

    def test_size_limits(self):
        self.assertTrue(has(one(buy("TCS.NS", qty=40, price=2510.0, stop=2505.0, target=2520.0))["reasons"],
                            "size: ₹100,400 is over your ₹80,000 max order"))
        self.assertTrue(has(one(buy("ITC.NS", qty=1, price=410.0, stop=405.0, target=425.0))["reasons"],
                            "size: ₹410 is under your ₹500 minimum order"))

    def test_blocked_list_and_duplicates(self):
        self.assertTrue(has(one(rel(), cfg={"blocked": ["reliance"]})["reasons"], "blocked:"))
        res = run_check([rel(), rel()])
        self.assertTrue(has(res["results"][1]["reasons"], "duplicate: a second buy RELIANCE.NS"))
        state = {"orders": [{"status": "placed", "date": "2026-11-06", "symbol": "RELIANCE.NS", "side": "buy",
                             "kind": "entry", "max_loss": 1}]}
        self.assertTrue(has(one(rel(), state=state)["reasons"], "duplicate: a buy RELIANCE.NS was already placed"))

    def test_format_rules(self):
        self.assertTrue(has(one(dict(rel(), qty=2.5))["reasons"], "format: NSE takes whole shares only"))
        self.assertTrue(has(one(dict(rel(), qty=0))["reasons"], "format: qty must be a positive"))
        self.assertTrue(has(one(dict(rel(), notional=50000))["reasons"], "format: NSE orders are a whole number"))
        self.assertTrue(has(one(dict(rel(), order_type="BRACKET"))["reasons"], "format: unknown order_type"))
        self.assertTrue(has(one(dict(rel(), product="NRML"))["reasons"], "format: unknown product"))
        self.assertTrue(has(one(dict(rel(), validity="GTC"))["reasons"], "format: unknown validity"))
        self.assertTrue(has(one(dict(rel(), side="hold"))["reasons"], "format: side must be BUY or SELL"))

    def test_live_account_warns(self):
        res = run_check([rel()], cfg={"paper": False})
        self.assertTrue(any("real money" in w for w in res["warnings"]))


class Exits(unittest.TestCase):
    PF = {"cash": 25000.0, "positions": [{"symbol": "RELIANCE.NS", "shares": 57}]}

    def test_getting_out_is_never_blocked_by_the_day_limits(self):
        late = check.parse_now("2026-11-06 15:12")
        state = {"orders": [{"status": "placed", "date": "2026-11-06", "symbol": s, "side": "buy", "kind": "entry",
                             "max_loss": 5000} for s in ("A.NS", "B.NS", "C.NS")]}
        r = one(sell(), now=late, portfolio=self.PF, prices={"RELIANCE.NS": 1305.0}, state=state)
        self.assertEqual((r["verdict"], r["kind"], r["reasons"]), ("PASS", "exit", []))
        r = one(sell(), now=late, portfolio=self.PF, prices={"RELIANCE.NS": 1305.0}, state=state,
                cfg={"blocked": ["RELIANCE"]})
        self.assertEqual(r["verdict"], "PASS")
        self.assertTrue(any(w.startswith("exit, not blocked: blocked") for w in r["warnings"]))

    def test_short_sales_are_blocked(self):
        r = one(sell(qty=60), portfolio=self.PF, prices={"RELIANCE.NS": 1305.0})
        self.assertEqual(r["kind"], "entry")
        self.assertTrue(has(r["reasons"], "short: selling more RELIANCE.NS than you hold (57 shares)"))

    def test_format_and_asset_still_apply_to_a_sell(self):
        r = one(sell(qty=57, order_type="LIMIT", price=1305.03), portfolio=self.PF, prices={"RELIANCE.NS": 1305.0})
        self.assertTrue(has(r["reasons"], "format: price 1305.03 is not on the NSE tick"))

    def test_buy_then_sell_in_one_batch(self):
        res = run_check([rel(), sell(qty=57)])
        self.assertEqual([(r["verdict"], r["kind"]) for r in res["results"]], [("PASS", "entry"), ("PASS", "exit")])


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cfg = os.path.join(self.d, "config.json")
        with open(self.cfg, "w", encoding="utf-8") as fh:
            json.dump(CFG, fh)
        self.out = os.path.join(self.d, "state", "risk.json")
        self.none = os.path.join(self.d, "none.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def base(self, now="2026-11-06 11:43"):
        return ["--orders", ORDERS, "--config", self.cfg, "--portfolio", self.none, "--prices", PRICES,
                "--state", self.none, "--out", self.out, "--now", now]

    def test_cli_with_a_fresh_price(self):
        code, text, _ = run(self.base())            # the 11:42 IST quote, 1313.80, is a minute old
        self.assertEqual(code, 0, text)
        self.assertIn("PASS  #1 BUY RELIANCE.NS 57 shares MIS, entry", text)
        self.assertIn("RELIANCE.NS last 1313.80 is above the plan's entry 1311.90", text)
        self.assertIn("1 passed, 0 blocked at 2026-11-06 11:43 IST", text)
        self.assertNotIn("$", text)
        with open(self.out, encoding="utf-8") as fh:
            rec = json.load(fh)
        self.assertEqual(rec["results"][0]["fingerprint"], check.fingerprint(rel()))
        self.assertEqual(rec["currency"], "INR")

    def test_prices_file_freshness(self):
        code, text, _ = run(self.base("2026-11-06 12:30") + ["--json"])
        r = json.loads(text)["results"][0]
        self.assertEqual(r["entry_ref"], 1311.9)                  # the 11:42 quote is 48 minutes old
        self.assertTrue(any("older than 15 minutes" in w for w in r["warnings"]))

    def test_blocked_exit_code_and_errors(self):
        code, text, _ = run(self.base("2026-11-06 15:05"))
        self.assertEqual(code, 1)
        self.assertIn("BLOCK #1", text)
        self.assertIn("Blocked orders do not go to the broker", text)
        code, _, err = run(["--orders", self.none, "--out", self.out])
        self.assertEqual(code, 1)
        self.assertIn("check.py: no orders file", err)
        self.assertEqual(len(err.strip().splitlines()), 1)
        code, _, err = run(self.base("soon"))
        self.assertIn("--now wants", err)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-risk", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-risk\n"))
        low = " ".join(text.lower().split())
        for must in ("nse", "ist", "charges", "tick", "mis", "circuit", "trade-for-trade", "not financial advice",
                     "yes"):
            self.assertIn(must, low)
        for never in ("nyse", "new york", "alpaca", "pdt_guard", "finra", "\u2014"):
            self.assertNotIn(never, low)
        self.assertNotRegex(low, r"\$\d")


if __name__ == "__main__":
    unittest.main()

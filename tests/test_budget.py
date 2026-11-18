import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

size = load("trader-budget", "size")

CFG = os.path.join(ROOT, "trader-budget", "config.example.json")
# The plan is trader-exit's ORB plan on the real RELIANCE.NS 5-minute bars of Fri 2026-11-06
# (Yahoo chart JSON): opening range 9:15-9:30 high 1306.00 / low 1295.60, the 9:45 bar closed at
# 1311.90 above it; stop at the range midpoint 1300.80, target 2R 1334.10. All on the 0.10 tick.
PLAN = os.path.join(FIX, "trader-budget", "plan-reliance.json")
ORDERS_FIX = os.path.join(FIX, "trader-risk", "orders-reliance.json")
TARGETS = os.path.join(ROOT, "trader-budget", "targets.example.json")
NOW = "2026-11-06 09:50"
TODAY = "2026-11-06"
# synthetic round prices for the weights mode (the split logic does not depend on them)
PRICES = {"RELIANCE.NS": 1311.0, "HDFCBANK.NS": 980.0, "ICICIBANK.NS": 1350.0, "INFY.NS": 1490.0,
          "TCS.NS": 3050.0}
SPLIT = {"RELIANCE.NS": 25000, "HDFCBANK.NS": 23214, "ICICIBANK.NS": 18571, "INFY.NS": 13929, "TCS.NS": 9286}
SHIPPED_CFG = {
    "currency": "INR", "budget": 100000, "cash_buffer_pct": 10, "max_position_pct": 75, "max_order_inr": 80000,
    "min_order_inr": 500, "max_loss_per_trade_inr": 1000, "max_daily_loss_inr": 3000, "max_trades_per_day": 3,
    "max_orders_per_day": 10, "min_reward_risk": 1.5, "require_stop": True,
    "no_entries_before": "09:30", "no_entries_after": "14:45", "mis_entry_cutoff": "15:00", "flat_by": "15:10",
    "exchange": "NSE", "product": "MIS", "fractional": False, "broker": "none", "paper": True, "blocked": [],
    "allow_options": False, "allow_margin": False, "allow_short": False,
    "charges": {"brokerage_pct": 0.03, "brokerage_max_per_order": 20, "stt_sell_pct": 0.025,
                "exchange_txn_pct": 0.00307, "sebi_per_crore": 10, "stamp_buy_pct": 0.003, "gst_pct": 18},
}
# a long-term split config for the weights mode tests
SPLIT_CFG = {"budget": 100000, "cash_buffer_pct": 10, "max_position_pct": 25, "max_order_inr": 50000,
             "min_order_inr": 500, "fractional": True, "paper": True, "blocked": []}


def cfg(**over):
    with open(CFG, encoding="utf-8") as fh:
        c = json.load(fh)
    c.update(over)
    return c


def split(**over):
    c = dict(SPLIT_CFG)
    c.update(over)
    return c


def plan(**over):
    with open(PLAN, encoding="utf-8") as fh:
        p = json.load(fh)
    p.update(over)
    return p


def rows(path):
    with open(path, encoding="utf-8") as fh:
        return size.parse_weights(json.load(fh))


def rupees(result):
    return {p["ticker"]: p["rupees"] for p in result["positions"]}


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = size.main(argv)
    return code, out.getvalue(), err.getvalue()


def prices_doc(price, quote_time="2026-11-06T04:35:00Z"):
    return {"as_of": quote_time, "source": "yahoo", "prices": {"RELIANCE.NS": price},
            "detail": {"RELIANCE.NS": {"prev_close": 1313.40, "time": quote_time, "source": "yahoo"}}}


class IndiaRules(unittest.TestCase):
    def test_charges_by_hand(self):
        # 50 shares bought at 1400, stopped at 1386 (the guide's example):
        # brokerage min(21, 20) + min(20.79, 20) = 40; STT 69300 x 0.025% = 17.325;
        # exchange 139300 x 0.00307% = 4.27651; SEBI 139300 x 10 / 1e7 = 0.1393; stamp 70000 x 0.003% = 2.1;
        # GST 18% of (40 + 4.27651 + 0.1393) = 7.99484. Total 71.83565 -> 71.84
        self.assertEqual(size.intraday_charges(50, 1400, 1386), 71.84)
        # small order: brokerage is 0.03%, not the 20 cap
        self.assertEqual(size.intraday_charges(1, 100, 100), round(0.06 + 0.025 + 0.00614 + 0.0002 + 0.003 +
                                                                  (0.06 + 0.00614 + 0.0002) * 0.18, 2))
        self.assertEqual(size.intraday_charges(50, 1400, 1386, {"brokerage_max_per_order": 0}), 24.64)

    def test_tick_rounding(self):
        self.assertEqual(str(size.round_to_tick("1311.87", "up")), "1311.90")
        self.assertEqual(str(size.round_to_tick("1311.87", "down")), "1311.80")
        self.assertEqual(str(size.round_to_tick("249.997", "down")), "249.99")
        self.assertEqual(str(size.round_to_tick("512.33", "down")), "512.30")
        self.assertEqual(str(size.round_to_tick("7123.4", "up")), "7123.50")
        self.assertEqual(str(size.round_to_tick("25003", "down")), "25000.00")
        self.assertEqual(size.tick(1311.87, "up"), 1311.9)

    def test_symbols_and_ist(self):
        self.assertEqual(size.nse_symbol("reliance"), "RELIANCE.NS")
        self.assertEqual(size.nse_symbol("TCS.BO"), "TCS.BO")
        self.assertEqual(size.broker_symbol("M&M"), ("NSE", "M&M"))
        self.assertEqual(size.yahoo_symbol("M&M"), "M%26M.NS")
        self.assertEqual(size.parse_now("2026-11-06 09:50").isoformat(), "2026-11-06T04:20:00")
        self.assertEqual(size.ist_iso(size.parse_now("2026-11-06 09:50")), "2026-11-06T09:50:00+05:30")

    def test_a_bare_symbol_in_the_plan_is_stored_with_the_suffix(self):
        s = size.size_trade(plan(symbol="reliance"), cfg(), today=TODAY)
        self.assertEqual((s["symbol"], s["exchange"], s["tradingsymbol"]), ("RELIANCE.NS", "NSE", "RELIANCE"))


class TheExample(unittest.TestCase):
    """₹1,00,000, 10% buffer, ₹1,000 max loss, RELIANCE risk 11.10 -> 57 shares (position cap)."""

    def test_config_example_is_the_shipped_one(self):
        with open(CFG, encoding="utf-8") as fh:
            c = json.load(fh)
        self.assertEqual(c, SHIPPED_CFG)
        for gone in ("pdt_guard", "sec_user_agent", "max_loss_per_trade_usd"):
            self.assertNotIn(gone, c)

    def test_reliance_is_57_shares_bound_by_the_position_cap(self):
        s = size.size_trade(plan(), cfg(), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"]), (57, "position cap"))
        self.assertEqual(s["risk_per_share"], 11.1)
        self.assertEqual(s["cost"], 74778.3)
        self.assertEqual(s["gross_loss"], 632.7)
        self.assertEqual(s["charges"], size.intraday_charges(57, 1311.9, 1300.8))
        self.assertEqual(s["charges"], 73.55)
        self.assertEqual(s["max_loss"], 706.25)
        self.assertEqual(s["reward_risk"], 2.0)
        self.assertEqual(s["cash_buffer"], 10000)
        self.assertEqual([(l["name"], l["shares"]) for l in s["limits"]],
                         [("max loss", 82), ("position cap", 57), ("max order", 60), ("cash", 68)])
        self.assertIsNone(s["refusal"])

    def test_the_max_loss_includes_charges(self):
        s = size.size_trade(plan(), cfg(max_loss_per_trade_inr=500), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"]), (40, "max loss"))
        self.assertEqual((s["gross_loss"], s["charges"], s["max_loss"]), (444.0, 55.49, 499.49))
        # 45 shares x 11.10 = 499.50 alone would fit; with charges it does not
        self.assertGreater(45 * 11.1 + size.intraday_charges(45, 1311.9, 1300.8), 500)
        self.assertLessEqual(s["max_loss"], 500)

    def test_the_order_is_the_neutral_shape(self):
        o = size.entry_order(size.size_trade(plan(), cfg(), today=TODAY))
        why = o.pop("why")
        self.assertEqual(o, {
            "symbol": "RELIANCE.NS", "exchange": "NSE", "tradingsymbol": "RELIANCE", "side": "BUY", "qty": 57,
            "order_type": "MARKET", "product": "MIS", "validity": "DAY", "stop_loss": 1300.8, "target": 1334.1,
            "max_loss_inr": 706.25, "charges_estimate_inr": 73.55, "flat_by": "15:10",
            "plan": {"entry": 1311.9, "stop": 1300.8, "target": 1334.1, "risk_per_share": 11.1,
                     "max_loss": 706.25, "setup": "orb", "entry_time": "09:50", "flat_by": "15:10"}})
        self.assertIn("bound by your position cap", why)
        self.assertIn("₹73.55 charges = ₹706.25", why)

    def test_the_fixture_orders_file_is_what_it_writes(self):
        with open(ORDERS_FIX, encoding="utf-8") as fh:
            fixture = json.load(fh)["orders"][0]
        self.assertEqual(size.entry_order(size.size_trade(plan(), cfg(), today=TODAY)), fixture)

    def test_a_limit_order_on_the_tick(self):
        o = size.entry_order(size.size_trade(plan(), cfg(), limit=1311.90, today=TODAY))
        self.assertEqual((o["order_type"], o["price"], o["qty"]), ("LIMIT", 1311.9, 57))
        s = size.size_trade(plan(), cfg(), limit=1311.83, today=TODAY)     # off-tick: rounds UP to 1311.90
        self.assertEqual(s["entry"], 1311.9)
        s = size.size_trade(plan(stop=1300.83, target=1334.17), cfg(), today=TODAY)   # stop and target round DOWN
        self.assertEqual((s["stop"], s["target"]), (1300.8, 1334.1))

    def test_cli_writes_one_order(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "trader", "orders.json")
            code, text, _ = run(["--plan", PLAN, "--config", CFG, "--portfolio", os.path.join(d, "none"),
                                 "--now", NOW, "--write", out])
            self.assertEqual(code, 0)
            self.assertIn("Size: 57 shares of RELIANCE.NS = ₹74,778.30 at 1311.90", text)
            self.assertIn("57 x 11.10 = ₹632.70 + ₹73.55 charges = ₹706.25", text)
            self.assertIn("BUY 57 RELIANCE (NSE, MIS), market, stop 1300.80, target 1334.10", text)
            self.assertIn("Flat by 15:10 IST", text)
            self.assertIn("<- binds", text)
            self.assertIn("not financial advice", text)
            self.assertNotIn("$", text)
            with open(out, encoding="utf-8") as fh:
                doc = json.load(fh)
            self.assertEqual((doc["status"], doc["currency"], doc["broker"]), ("proposed", "INR", "none"))
            self.assertIs(doc["paper"], True)
            self.assertEqual(doc["created_at"], "2026-11-06T04:20:00Z")
            self.assertEqual(len(doc["orders"]), 1)
            self.assertEqual(doc["orders"][0]["max_loss_inr"], 706.25)

    def test_cli_json_and_limit(self):
        code, text, _ = run(["--plan", PLAN, "--config", CFG, "--portfolio", "/nonexistent", "--now", NOW,
                             "--limit", "--json"])
        self.assertEqual(code, 0)
        out = json.loads(text)
        self.assertEqual(out["sizing"]["qty"], 57)
        self.assertEqual(out["orders"][0]["price"], 1311.9)


class PlanRules(unittest.TestCase):
    def test_zero_shares_is_a_plain_refusal(self):
        s = size.size_trade(plan(stop=300.0), cfg(), today=TODAY)
        self.assertEqual(s["qty"], 0)
        self.assertIn("plus charges, more than your ₹1,000 max loss per trade", s["refusal"])
        self.assertIsNone(size.entry_order(s))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "plan.json")
            with open(p, "w") as fh:
                json.dump(plan(stop=300.0), fh)
            out = os.path.join(d, "orders.json")
            code, text, _ = run(["--plan", p, "--config", CFG, "--portfolio", os.path.join(d, "none"),
                                 "--now", NOW, "--write", out])
            self.assertEqual(code, 1)
            self.assertIn("No order:", text)
            self.assertFalse(os.path.exists(out))

    def test_each_limit_can_bind(self):
        self.assertEqual(size.size_trade(plan(), cfg(max_order_inr=50000), today=TODAY)["bound_by"], "max order")
        s = size.size_trade(plan(), cfg(max_position_pct=20), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"]), (15, "position cap"))
        s = size.size_trade(plan(), cfg(), cash=20000, today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"], s["cash_available"]), (7, "cash", 10000))
        s = size.size_trade(plan(), cfg(max_loss_per_trade_inr=300), today=TODAY)
        self.assertEqual(s["bound_by"], "max loss")

    def test_a_fresh_higher_price_means_a_bigger_risk(self):
        s = size.size_trade(plan(), cfg(min_reward_risk=0), price=1318.43, today=TODAY)
        self.assertEqual((s["entry"], s["entry_from"], s["risk_per_share"]), (1318.5, "price", 17.7))
        self.assertEqual(s["reward_risk"], 0.88)
        self.assertIsNone(s["refusal"])
        s = size.size_trade(plan(), cfg(), price=1318.43, today=TODAY)
        self.assertIn("reward:risk 0.88 (target 1334.10, entry 1318.50, stop 1300.80) is under your minimum 1.5",
                      s["refusal"])
        self.assertIsNone(size.entry_order(s))
        lower = size.size_trade(plan(), cfg(), price=1305.00, today=TODAY)
        self.assertEqual((lower["entry"], lower["qty"]), (1311.9, 57))

    def test_prices_file_is_used_only_when_fresh(self):
        with tempfile.TemporaryDirectory() as d:
            pp = os.path.join(d, "prices.json")
            with open(pp, "w") as fh:
                json.dump(prices_doc(1318.43), fh)
            base = ["--plan", PLAN, "--config", CFG, "--portfolio", os.path.join(d, "none"), "--prices", pp, "--json"]
            code, text, _ = run(base + ["--now", "2026-11-06 10:10"])
            out = json.loads(text)
            self.assertEqual((code, out["orders"]), (1, []))
            self.assertIn("above the plan's entry", out["notes"][0])
            self.assertIn("IST", out["notes"][0])
            out = json.loads(run(base + ["--now", "2026-11-06 10:30"])[1])
            self.assertEqual(out["sizing"]["qty"], 57)
            self.assertIn("not used", out["notes"][0])

    def test_the_portfolio_cash_caps_the_size(self):
        with tempfile.TemporaryDirectory() as d:
            pf = os.path.join(d, "portfolio.json")
            with open(pf, "w") as fh:
                json.dump({"cash": 20000, "positions": []}, fh)
            out = json.loads(run(["--plan", PLAN, "--config", CFG, "--portfolio", pf, "--now", NOW, "--json"])[1])
            self.assertEqual((out["sizing"]["qty"], out["sizing"]["bound_by"]), (7, "cash"))

    def test_bad_plans(self):
        for p, word in ((plan(stop=1312.0), "not below the entry"),
                        (plan(target=1310.0), "already happened"),
                        (plan(target=None), "positive target"),
                        (plan(side="sell"), "long (buy) day trades only"),
                        (plan(symbol=""), "no symbol")):
            with self.assertRaises(size.SizeError) as e:
                size.size_trade(p, cfg(), today=TODAY)
            self.assertIn(word, str(e.exception))
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(plan(), cfg(), price=1340.00, today=TODAY)
        self.assertIn("already happened", str(e.exception))
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(plan(), cfg(blocked=["reliance"]), today=TODAY)
        self.assertIn("blocked list", str(e.exception))
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(plan(), cfg(), today="2026-11-09")
        self.assertIn("make a new plan", str(e.exception))

    def test_under_the_minimum_order(self):
        cheap = plan(symbol="ABC", entry=40.0, stop=30.0, target=60.0)
        s = size.size_trade(cheap, cfg(max_loss_per_trade_inr=11), today=TODAY)
        self.assertEqual(s["qty"], 1)
        self.assertIn("under your ₹500 minimum order", s["refusal"])
        self.assertIsNone(size.entry_order(s))

    def test_series_and_circuits_pass_through_to_risk(self):
        o = size.entry_order(size.size_trade(plan(series="eq", upper_circuit=1442.9, lower_circuit=1180.7),
                                             cfg(), today=TODAY))
        self.assertEqual((o["series"], o["upper_circuit"], o["lower_circuit"]), ("EQ", 1442.9, 1180.7))

    def test_missing_plan_and_weights_only_flags(self):
        code, _, err = run(["--plan", "/nonexistent/plan.json", "--config", CFG, "--now", NOW])
        self.assertEqual(code, 1)
        self.assertIn("no plan", err)
        code, _, err = run(["--plan", PLAN, "--config", CFG, "--now", NOW, "--top", "2"])
        self.assertEqual(code, 1)
        self.assertIn("--weights", err)
        code, _, err = run(["--plan", PLAN, "--config", CFG, "--now", "tomorrow"])
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", err)


class WeightsMode(unittest.TestCase):
    """v1's split, kept behind --weights / --targets: ₹1,00,000, 10% cash, 25% cap, five Nifty 50 stocks."""

    def check_split(self, r):
        self.assertEqual(rupees(r), SPLIT)
        self.assertEqual(r["cash_buffer"], 10000)
        self.assertEqual((r["invested"], r["idle"]), (90000, 0))
        self.assertEqual(r["cap"], 25000)
        self.assertEqual([p["ticker"] for p in r["positions"] if p["capped"]], ["RELIANCE.NS"])

    def test_from_the_example_weights_file(self):
        r = size.size(rows(TARGETS), split())
        self.check_split(r)

    def test_from_inline_bare_symbols(self):
        w = size.parse_weights(size.weights_from_arg("RELIANCE=30,HDFCBANK=25,ICICIBANK=20,INFY=15,TCS=10"))
        self.assertEqual([x["ticker"] for x in w][:2], ["RELIANCE.NS", "HDFCBANK.NS"])
        self.check_split(size.size(w, split()))

    def test_renormalised_weights(self):
        r = size.size(rows(TARGETS), split())
        shown = {p["ticker"]: round(p["weight"], 1) for p in r["positions"]}
        self.assertEqual(shown, {"RELIANCE.NS": 30.0, "HDFCBANK.NS": 25.0, "ICICIBANK.NS": 20.0,
                                 "INFY.NS": 15.0, "TCS.NS": 10.0})

    def test_a_rupee_split_alone_gives_no_orders(self):
        r = size.size(rows(TARGETS), split())
        with self.assertRaises(size.SizeError) as e:
            size.orders_from(r)
        self.assertIn("no fractional shares", str(e.exception))

    def test_paise_mode_keeps_the_total(self):
        r = size.size(rows(TARGETS), split(), paise=True)
        d = rupees(r)
        self.assertEqual(d["RELIANCE.NS"], 25000)
        self.assertAlmostEqual(sum(d.values()), 90000, places=2)
        self.assertAlmostEqual(d["HDFCBANK.NS"], 23214.29, places=2)

    def test_cap_redistributes_until_nobody_is_over(self):
        w = size.parse_weights({"A": 60, "B": 30, "C": 10})
        r = size.size(w, {"budget": 1000, "cash_buffer_pct": 0, "max_position_pct": 40, "min_order_inr": 1,
                          "fractional": True})
        self.assertEqual(rupees(r), {"A.NS": 400, "B.NS": 400, "C.NS": 200})
        self.assertEqual(sorted(p["ticker"] for p in r["positions"] if p["capped"]), ["A.NS", "B.NS"])

    def test_everything_capped_leaves_money_idle(self):
        w = size.parse_weights({"RELIANCE": 50, "TCS": 50})
        r = size.size(w, split())
        self.assertEqual(rupees(r), {"RELIANCE.NS": 25000, "TCS.NS": 25000})
        self.assertEqual(r["idle"], 40000)
        self.assertIn("every stock is at its cap", size.report(r, []))

    def test_slices_under_the_minimum_are_dropped_and_shared_out(self):
        w = size.parse_weights({"A": 90, "B": 7, "C": 3})
        r = size.size(w, {"budget": 10000, "cash_buffer_pct": 10, "max_position_pct": 100, "min_order_inr": 500,
                          "fractional": True})
        self.assertNotIn("C.NS", rupees(r))
        self.assertEqual(sum(rupees(r).values()), 9000)
        self.assertTrue(all(v >= 500 for v in rupees(r).values()))
        self.assertEqual(r["dropped"][0]["ticker"], "C.NS")
        self.assertIn("₹500 minimum order", r["dropped"][0]["reason"])

    def test_blocked_symbols_are_dropped_first(self):
        r = size.size(rows(TARGETS), split(blocked=["tcs"]))
        self.assertNotIn("TCS.NS", rupees(r))
        self.assertIn("blocked", r["dropped"][0]["reason"])

    def test_top_n(self):
        r = size.size(rows(TARGETS), split(max_position_pct=100), top=2)
        self.assertEqual(sorted(rupees(r)), ["HDFCBANK.NS", "RELIANCE.NS"])

    def test_whole_shares_need_prices(self):
        with self.assertRaises(size.SizeError):
            size.size(rows(TARGETS), split(fractional=False))
        with self.assertRaises(size.SizeError):
            size.size(rows(TARGETS), cfg())          # the shipped config is whole shares

    def test_whole_shares_with_prices_give_neutral_orders(self):
        r = size.size(rows(TARGETS), split(fractional=False), prices=PRICES)
        self.assertTrue(r["whole_shares"])
        for p in r["positions"]:
            self.assertEqual(p["shares"], int(p["shares"]))
            self.assertAlmostEqual(p["rupees"], p["shares"] * PRICES[p["ticker"]], places=2)
            self.assertLessEqual(p["rupees"], 25000)
        self.assertAlmostEqual(r["invested"] + r["idle"], 90000, places=2)
        orders = size.orders_from(r, "an example")
        self.assertEqual(orders[0]["symbol"], "RELIANCE.NS")
        self.assertEqual({k: orders[0][k] for k in ("exchange", "tradingsymbol", "side", "order_type", "product",
                                                     "validity")},
                         {"exchange": "NSE", "tradingsymbol": "RELIANCE", "side": "BUY", "order_type": "MARKET",
                          "product": "CNC", "validity": "DAY"})
        self.assertTrue(all(isinstance(o["qty"], int) and "notional" not in o for o in orders))
        self.assertIn("capped at 25%", orders[0]["why"])

    def test_no_budget_and_duplicates_are_errors(self):
        with self.assertRaises(size.SizeError):
            size.size(rows(TARGETS), {"cash_buffer_pct": 10})
        with self.assertRaises(size.SizeError):
            size.size([{"ticker": "TCS.NS", "name": "", "weight": 1}, {"ticker": "TCS.NS", "name": "", "weight": 2}],
                      split())

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            c = os.path.join(d, "config.json")
            with open(c, "w") as fh:
                json.dump(split(fractional=False), fh)
            pp = os.path.join(d, "prices.json")
            with open(pp, "w") as fh:
                json.dump({"prices": PRICES}, fh)
            out = os.path.join(d, "trader", "orders.json")
            code, text, _ = run(["--targets", TARGETS, "--config", c, "--prices", pp, "--write", out])
            self.assertEqual(code, 0)
            self.assertIn("left idle", text)
            self.assertIn("require_stop", text)
            self.assertIn("BUY 19 RELIANCE (NSE, CNC), market", text)
            with open(out, encoding="utf-8") as fh:
                doc = json.load(fh)
            self.assertEqual([o["symbol"] for o in doc["orders"]],
                             ["RELIANCE.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS", "TCS.NS"])
            c2 = os.path.join(d, "split.json")
            with open(c2, "w") as fh:
                json.dump(split(), fh)
            code, text, _ = run(["--targets", TARGETS, "--config", c2, "--json"])
            self.assertEqual(json.loads(text)["result"]["idle"], 0)
            code, _, err = run(["--targets", "/nonexistent/targets.json", "--config", c])
            self.assertEqual(code, 1)
            self.assertIn("no weights file", err)


if __name__ == "__main__":
    unittest.main()

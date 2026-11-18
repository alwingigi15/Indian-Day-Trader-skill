"""trader-broker tests. The order is trader-budget's real output for a
RELIANCE.NS opening-range breakout on Fri 6 Nov 2026 (tests/fixtures/trader-risk/
orders-reliance.json): BUY 57 at market, stop 1300.80, target 1334.10, max loss
₹706.25 including ₹73.55 of charges."""

import copy
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

ticket = load("trader-broker", "ticket")
check = load("trader-risk", "check")

CFG_PATH = os.path.join(ROOT, "trader-budget", "config.example.json")
ORDERS_PATH = os.path.join(FIX, "trader-risk", "orders-reliance.json")
PLAN_PATH = os.path.join(FIX, "trader-budget", "plan-reliance.json")
NOW = "2026-11-06 09:52"
TODAY = "2026-11-06"
PLAIN = ("BUY 57 RELIANCE (NSE, MIS), market / stop ₹1,300.80 / target ₹1,334.10 / "
         "max loss about ₹706 incl. charges")


def rel(**over):
    with open(ORDERS_PATH, encoding="utf-8") as fh:
        o = copy.deepcopy(json.load(fh)["orders"][0])
    o.update(over)
    return o


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = ticket.main(argv)
    return code, out.getvalue(), err.getvalue()


class ExplicitYes(unittest.TestCase):
    def ok(self, reply, order=None, batch=()):
        return ticket.parse_yes(reply, order or rel(), batch)[0]

    def test_plain_yes_counts(self):
        for r in ("yes", "Yes", "yes.", "yes, place it", "y", "yes RELIANCE", "yes 57 shares", "yes 706"):
            self.assertTrue(self.ok(r), r)

    def test_anything_else_does_not(self):
        for r in ("", "ok", "sure", "haan", "go", "do it", "yes?", "yes to all", "yes, all of them",
                  "yes but wait", "yes if it dips", "no", "yes maybe later"):
            self.assertFalse(self.ok(r), r)

    def test_amount_and_symbol_must_match_this_order(self):
        self.assertFalse(self.ok("yes 60 shares"))
        self.assertTrue(self.ok("yes at 1300.80"))
        self.assertFalse(self.ok("yes, stop at 1300.80"))      # "stop" reads as a doubt: ask again
        ok, why = ticket.parse_yes("yes TCS", rel(), ["RELIANCE.NS", "TCS.NS"])
        self.assertFalse(ok)
        self.assertIn("names TCS", why)


class TicketShape(unittest.TestCase):
    def test_plain_in_rupees(self):
        self.assertEqual(ticket.plain(rel()), PLAIN)
        limit = rel(order_type="LIMIT", price=1312.0, validity="IOC")
        self.assertTrue(ticket.plain(limit).startswith("BUY 57 RELIANCE (NSE, MIS), limit ₹1,312.00, IOC"))
        slm = rel(order_type="SL-M", trigger_price=1312.5)
        self.assertIn("stop-market, trigger ₹1,312.50", ticket.plain(slm))
        self.assertNotIn("$", ticket.plain(rel()))

    def test_symbols(self):
        self.assertEqual(ticket.broker_symbol("RELIANCE.NS"), ("NSE", "RELIANCE"))
        self.assertEqual(ticket.broker_symbol("tcs.bo"), ("BSE", "TCS"))
        self.assertEqual(ticket.broker_symbol("M&M"), ("NSE", "M&M"))

    def test_kite_translation(self):
        k = ticket.to_kite(rel())
        self.assertEqual(k["tool"], "place_order")
        self.assertEqual(k["entry"], {"variety": "regular", "exchange": "NSE", "tradingsymbol": "RELIANCE",
                                      "transaction_type": "BUY", "quantity": 57, "product": "MIS",
                                      "order_type": "MARKET", "validity": "DAY"})
        stop, target = k["after_fill"]
        self.assertEqual((stop["transaction_type"], stop["order_type"], stop["trigger_price"]), ("SELL", "SL-M", 1300.8))
        self.assertEqual((target["order_type"], target["price"], target["quantity"]), ("LIMIT", 1334.1, 57))
        self.assertEqual(k["gtt_oco"]["trigger_values"], [1300.8, 1334.1])
        self.assertEqual(k["gtt_oco"]["last_price"], 1311.9)
        self.assertNotIn("why", json.dumps(k))
        limit = ticket.to_kite(rel(order_type="LIMIT", price=1312.0))
        self.assertEqual(limit["entry"]["price"], 1312.0)
        sell = ticket.to_kite({"symbol": "RELIANCE.NS", "side": "SELL", "qty": 57, "order_type": "MARKET"})
        self.assertEqual((sell["after_fill"], sell["gtt_oco"]), ([], None))

    def test_max_loss_includes_charges(self):
        self.assertEqual(ticket.max_loss(rel()), (706.25, 1300.8))
        self.assertEqual(ticket.max_loss(rel(max_loss_inr=None, plan={})), (None, 1300.8))

    def test_broker_mode(self):
        self.assertEqual(ticket.broker_mode({})[0], "ticket")
        self.assertEqual(ticket.broker_mode({"broker": "zerodha"})[0], "ticket")      # paper by default
        self.assertEqual(ticket.broker_mode({"broker": "zerodha", "paper": False}), ("kite", "Zerodha (Kite)"))
        self.assertEqual(ticket.broker_mode({"broker": "dhan", "paper": False})[0], "ticket")

    def test_fingerprint_matches_the_risk_check(self):
        self.assertEqual(ticket.ORDER_FIELDS, check.ORDER_FIELDS)
        self.assertEqual(ticket.fingerprint(rel()), check.fingerprint(rel()))


class DesktopConfig(unittest.TestCase):
    def test_example_is_the_kite_mcp_server_without_keys(self):
        with open(os.path.join(ROOT, "trader-broker", "claude_desktop_config.example.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        server = doc["mcpServers"]["kite"]
        self.assertEqual(server["args"][-1], "https://mcp.kite.trade/mcp")
        self.assertNotIn("alpaca", json.dumps(doc).lower())
        self.assertNotIn("env", server)                       # Kite logs in through the browser, no keys here


class Flow(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.orders = os.path.join(self.d, "orders.json")
        shutil.copy(ORDERS_PATH, self.orders)
        self.cfg = os.path.join(self.d, "config.json")
        shutil.copy(CFG_PATH, self.cfg)
        self.risk = os.path.join(self.d, "state", "risk.json")
        self.state = os.path.join(self.d, "state", "broker.json")
        self.pf = os.path.join(self.d, "portfolio.json")
        self.none = os.path.join(self.d, "none")

    def tearDown(self):
        shutil.rmtree(self.d)

    def args(self, *extra, **kw):
        return list(extra) + ["--orders", self.orders, "--config", self.cfg, "--risk", self.risk,
                              "--state", self.state, "--now", kw.get("now", NOW)]

    def run_risk(self, now=NOW):
        with redirect_stdout(io.StringIO()):
            return check.main(["--orders", self.orders, "--config", self.cfg, "--portfolio", self.pf,
                               "--prices", self.none, "--state", self.state, "--now", now, "--out", self.risk])

    def state_doc(self):
        with open(self.state, encoding="utf-8") as fh:
            return json.load(fh)

    def set_cfg(self, **kw):
        with open(self.cfg, encoding="utf-8") as fh:
            cfg = json.load(fh)
        cfg.update(kw)
        with open(self.cfg, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)

    def test_nothing_is_ready_before_the_risk_check(self):
        code, text, _ = run(self.args("show"))
        self.assertEqual(code, 1)
        self.assertIn("NOT READY", text)
        self.assertNotIn("Reply \"yes\"", text)
        code, _, err = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 1)
        self.assertIn("not ready", err)

    def test_the_ticket_only_flow(self):
        self.assertEqual(self.run_risk(), 0)
        code, text, _ = run(self.args("show"))
        self.assertEqual(code, 0)
        self.assertIn("ORDER 1 of 1, TICKET ONLY (place it yourself), no broker connected", text)
        for part in PLAIN.split(" / "):
            self.assertIn(part, text)
        self.assertIn("READY: trader-risk PASS, waiting for your yes", text)
        self.assertIn("Nothing is sent from here", text)
        self.assertIn("SL-M sell", text)
        self.assertNotIn("place_order", text)

        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "ok"))
        self.assertEqual(code, 1)
        self.assertIn("Not recorded", text)
        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "yes, 57 RELIANCE"))
        self.assertEqual(code, 0)
        self.assertIn("TICKET ONLY: nothing was sent", text)
        rec = self.state_doc()["orders"][-1]
        self.assertEqual((rec["status"], rec["reply"], rec["kind"], rec["max_loss"], rec["mode"]),
                         ("approved", "yes, 57 RELIANCE", "entry", 706.25, "ticket"))
        self.assertTrue(rec["at"].endswith("+05:30"))
        code, text, _ = run(self.args("show"))
        self.assertIn("APPROVED by you", text)
        self.assertEqual(run(self.args("approve", "--index", "1", "--reply", "yes"))[0], 1)   # once only

        code, text, _ = run(self.args("result", "--index", "1", "--status", "placed", "--broker-id", "251106000123",
                                      "--filled-qty", "57", "--filled-price", "1312.00", "--portfolio", self.pf))
        self.assertEqual(code, 0)
        self.assertIn("Added the fill", text)
        self.assertIn("SL-M sell and a LIMIT sell", text)
        with open(self.pf, encoding="utf-8") as fh:
            pf = json.load(fh)
        self.assertEqual(pf["cash"], 100000 - 57 * 1312.0)
        self.assertEqual(pf["positions"][0]["shares"], 57)
        code, text, _ = run(["log", "--state", self.state, "--now", NOW])
        self.assertIn("placed", text)
        self.assertIn("max loss ₹706.25", text)
        self.assertIn("id 251106000123", text)

        # the placed entry now counts in trader-risk: the same buy again is a duplicate
        self.assertEqual(self.run_risk(), 1)

    def test_the_kite_flow_prints_the_exact_call(self):
        self.set_cfg(broker="zerodha", paper=False)
        self.run_risk()
        code, text, _ = run(self.args("show"))
        self.assertIn("LIVE - Zerodha (Kite), REAL MONEY", text)
        self.assertIn("Claude will call the Kite MCP tool place_order with exactly:", text)
        self.assertIn('"tradingsymbol": "RELIANCE"', text)
        self.assertIn("Allow once places it", text)
        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 0)
        self.assertIn("Now call the Kite MCP tool place_order", text)
        self.assertEqual(self.state_doc()["orders"][-1]["mode"], "kite")
        code, text, _ = run(self.args("show", "--index", "1", "--json"))
        doc = json.loads(text)
        self.assertEqual((doc["mode"], doc["broker"], doc["max_loss_inr"]), ("kite", "zerodha", 706.25))

    def test_an_edited_order_is_not_ready(self):
        self.run_risk()
        with open(self.orders, encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["orders"][0]["qty"] = 58
        with open(self.orders, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        code, text, _ = run(self.args("show"))
        self.assertEqual(code, 1)
        self.assertIn("this exact order was not in the last risk check", text)

    def test_yesterdays_check_does_not_count(self):
        self.run_risk()
        code, text, _ = run(self.args("show", now="2026-11-09 09:52"))
        self.assertIn("the risk check is from 2026-11-06, not today", text)

    def test_blocked_orders_are_never_ready(self):
        self.assertEqual(self.run_risk(now="2026-11-06 09:20"), 1)   # before the 09:30 window
        code, text, _ = run(self.args("show"))
        self.assertEqual(code, 1)
        self.assertIn("BLOCKED by trader-risk: window", text)

    def test_result_needs_an_approval_and_decline_is_logged(self):
        self.run_risk()
        code, _, err = run(self.args("result", "--index", "1", "--status", "placed"))
        self.assertEqual(code, 1)
        self.assertIn("no yes recorded today", err)
        code, text, _ = run(self.args("decline", "--index", "1", "--reply", "no, skip it"))
        self.assertEqual(code, 0)
        self.assertIn("Nothing was sent", text)
        self.assertEqual(self.state_doc()["orders"][-1]["status"], "declined")
        code, text, _ = run(self.args("show"))
        self.assertIn("DONE: declined", text)

    def test_flatten_before_flat_by(self):
        run(["fill", "--symbol", "RELIANCE", "--side", "buy", "--qty", "57", "--price", "1312", "--portfolio", self.pf,
             "--config", self.cfg, "--state", self.state, "--now", NOW])
        code, text, _ = run(["flatten", "--symbol", "RELIANCE", "--orders", self.orders, "--portfolio", self.pf,
                             "--plan", PLAN_PATH, "--config", self.cfg, "--now", "2026-11-06 15:08"])
        self.assertEqual(code, 0)
        self.assertIn("Flatten RELIANCE.NS: SELL 57 RELIANCE (NSE, MIS), market (flat by 15:10 IST)", text)
        self.assertIn("(it replaced 1 earlier proposal)", text)
        self.assertIn("cancel the open stop (SL-M) and target (LIMIT) sells", text)
        with open(self.orders, encoding="utf-8") as fh:
            order = json.load(fh)["orders"][0]
        self.assertEqual((order["side"], order["qty"], order["product"], order["exchange"]), ("SELL", 57, "MIS", "NSE"))
        # an exit passes trader-risk even after the entry window and the MIS cut-off
        with redirect_stdout(io.StringIO()):
            code = check.main(["--orders", self.orders, "--config", self.cfg, "--portfolio", self.pf, "--prices",
                               self.none, "--state", self.state, "--now", "2026-11-06 15:08", "--out", self.risk])
        self.assertEqual(code, 0)
        code, text, _ = run(["flatten", "--symbol", "RELIANCE", "--orders", self.orders, "--portfolio", self.pf,
                             "--plan", PLAN_PATH, "--config", self.cfg, "--now", "2026-11-06 15:18"])
        self.assertIn("past your flat_by 15:10", text)

    def test_flatten_with_the_broker_qty_and_nothing_to_flatten(self):
        code, text, _ = run(["flatten", "--symbol", "TCS", "--qty", "10", "--orders", self.orders, "--portfolio",
                             self.none, "--plan", self.none, "--config", self.cfg, "--now", NOW])
        self.assertEqual(code, 0)
        self.assertIn("SELL 10 TCS (NSE, MIS)", text)
        code, _, err = run(["flatten", "--symbol", "TCS", "--orders", self.orders, "--portfolio", self.none,
                            "--plan", self.none, "--config", self.cfg, "--now", NOW])
        self.assertEqual(code, 1)
        self.assertIn("nothing to flatten", err)
        self.assertEqual(run(["flatten", "--symbol", "TCS", "--qty", "2.5", "--orders", self.orders,
                              "--config", self.cfg])[0], 1)

    def test_fill_sell_relieves_the_oldest_lot(self):
        base = ["--portfolio", self.pf, "--config", self.cfg, "--state", self.state]
        run(["fill", "--symbol", "RELIANCE", "--side", "buy", "--qty", "10", "--price", "1300", "--date", "2026-11-05"]
            + base)
        run(["fill", "--symbol", "RELIANCE.NS", "--side", "buy", "--qty", "10", "--price", "1320",
             "--date", "2026-11-06"] + base)
        code, text, _ = run(["fill", "--symbol", "RELIANCE", "--side", "sell", "--qty", "15", "--price", "1330",
                             "--date", "2026-11-06"] + base)
        self.assertEqual(code, 0)
        self.assertIn("Cash now ₹", text)
        with open(self.pf, encoding="utf-8") as fh:
            pf = json.load(fh)
        pos = pf["positions"][0]
        self.assertEqual(pos["shares"], 5)
        self.assertEqual([(l["shares"], l["cost"]) for l in pos["lots"]], [(5.0, 6600.0)])
        self.assertEqual(pf["cash"], 100000 - 13000 - 13200 + 19950)
        code, _, err = run(["fill", "--symbol", "RELIANCE", "--side", "sell", "--qty", "6", "--price", "1330"] + base)
        self.assertEqual(code, 1)
        self.assertIn("cannot record selling 6", err)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-broker", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-broker\n"))
        low = " ".join(text.lower().split())
        for must in ("ticket only", "zerodha", "kite", "needs approval", "mis", "not financial advice", "yes",
                     "token"):
            self.assertIn(must, low)
        for never in ("alpaca", "nyse", "new york", "\u2014"):
            self.assertNotIn(never, low)
        self.assertNotRegex(low, r"\$\d")


if __name__ == "__main__":
    unittest.main()

"""trader-alerts tests. The quote fixtures are Yahoo's real chart JSON for
RELIANCE.NS, TCS.NS, INFY.NS and HDFCBANK.NS read at 11:13 IST on Fri 6 Nov
2026, plus Yahoo's real answer for an unknown symbol (ZZZZ.NS)."""

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

from _load import load, ROOT, FIX

alerts = load("trader-alerts", "alerts")

QDIR = os.path.join(FIX, "trader-alerts")
PLAN = os.path.join(QDIR, "plan-reliance.json")
DT = datetime.datetime
UA = "Mozilla/5.0 (compatible; day-trader-skill/3.0)"

# NSE equity trading holidays 2026 (weekdays), from NSE's holiday circular
NSE_2026 = ["2026-01-15", "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31", "2026-04-03", "2026-04-14",
            "2026-05-01", "2026-05-28", "2026-06-26", "2026-09-14", "2026-10-02", "2026-10-20", "2026-11-10",
            "2026-11-24", "2026-12-25"]


def ist(text):
    return alerts.parse_now(text)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = alerts.main(argv)
    return code, out.getvalue(), err.getvalue()


def plan_rules(**kw):
    with open(PLAN, encoding="utf-8") as fh:
        return alerts.rules_from_plan(json.load(fh), **kw)


class Calendar(unittest.TestCase):
    def test_holidays_match_the_published_list(self):
        self.assertEqual(sorted(alerts.NSE_HOLIDAYS), NSE_2026)
        for d in alerts.NSE_HOLIDAYS:
            self.assertLess(datetime.date.fromisoformat(d).weekday(), 5, d)
        self.assertFalse(hasattr(alerts, "NYSE_EARLY_CLOSE"))

    def test_holidays_extend_from_the_market_file(self):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "market_india.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"holidays": {"2027-01-26": "Republic Day"}}, fh)
            hol = alerts.load_holidays(path)
            self.assertEqual(hol["2027-01-26"], "Republic Day")
            self.assertEqual(alerts.trading_day(datetime.date(2027, 1, 26), hol), (False, "NSE holiday: Republic Day"))
        finally:
            shutil.rmtree(d)

    def test_ist_has_no_daylight_saving(self):
        self.assertEqual(alerts.to_ist(DT(2026, 3, 8, 4, 0)), DT(2026, 3, 8, 9, 30))
        self.assertEqual(alerts.to_ist(DT(2026, 11, 1, 4, 0)), DT(2026, 11, 1, 9, 30))
        self.assertEqual(alerts.ist_iso(DT(2026, 11, 6, 3, 45)), "2026-11-06T09:15:00+05:30")

    def test_parse_now(self):
        self.assertEqual(ist("2026-11-06T09:15"), DT(2026, 11, 6, 3, 45))
        self.assertEqual(ist("2026-11-06 15:10"), DT(2026, 11, 6, 9, 40))
        self.assertEqual(ist("2026-11-06T05:43:14Z"), DT(2026, 11, 6, 5, 43, 14))
        self.assertEqual(ist("2026-11-06T11:13:14+05:30"), DT(2026, 11, 6, 5, 43, 14))
        with self.assertRaises(alerts.AlertError):
            ist("tomorrow")

    def test_market_status(self):
        st = alerts.market_status(ist("2026-11-06T10:00"))
        self.assertTrue(st["open"])
        self.assertEqual((st["opens"], st["closes"]), ("09:15", "15:30"))
        early = alerts.market_status(ist("2026-11-06T09:14"))
        self.assertFalse(early["open"])
        self.assertEqual(early["reason"], "before the open")
        self.assertEqual(early["next_open"], "2026-11-06 09:15")
        self.assertTrue(alerts.market_status(ist("2026-11-06T09:15"))["open"])
        self.assertTrue(alerts.market_status(ist("2026-11-06T15:29"))["open"])
        after = alerts.market_status(ist("2026-11-06T15:30"))
        self.assertFalse(after["open"])
        self.assertEqual(after["next_open"], "2026-11-09 09:15")       # Mon, after the weekend
        diwali = alerts.market_status(ist("2026-11-10T11:00"))
        self.assertFalse(diwali["open"])
        self.assertIn("Diwali Balipratipada", diwali["reason"])
        self.assertEqual(diwali["next_open"], "2026-11-11 09:15")
        self.assertEqual(alerts.market_status(ist("2026-11-07T10:00"))["reason"], "weekend")
        self.assertFalse(alerts.market_status(ist("2026-11-08T18:00"))["open"])   # Muhurat not modelled
        self.assertTrue(alerts.market_status(ist("2026-11-06T10:00"))["calendar_known"])
        self.assertFalse(alerts.market_status(ist("2028-03-01T10:00"))["calendar_known"])


class Quotes(unittest.TestCase):
    def test_yahoo_fixtures(self):
        with open(os.path.join(QDIR, "yahoo-RELIANCE.NS.json"), encoding="utf-8") as fh:
            q = alerts.parse_yahoo(fh.read(), "RELIANCE")
        self.assertEqual(q["symbol"], "RELIANCE.NS")
        self.assertEqual((q["price"], q["prev_close"], q["open"]), (1311.2, 1313.4, 1295.0))
        self.assertEqual(q["time"], "2026-11-06T05:43:14Z")
        self.assertEqual(q["currency"], "INR")

    def test_unknown_symbol(self):
        with open(os.path.join(QDIR, "yahoo-ZZZZ.NS.json"), encoding="utf-8") as fh:
            with self.assertRaises(alerts.AlertError) as cm:
                alerts.parse_yahoo(fh.read(), "ZZZZ")
        self.assertIn("No data found", str(cm.exception))

    def test_urls_and_user_agent(self):
        self.assertEqual(alerts.UA, UA)
        self.assertNotIn("http", alerts.UA)                       # NSE drops User-Agents holding a URL
        self.assertEqual(alerts.yahoo_url("reliance"),
                         "https://query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?range=1d&interval=1d")
        self.assertIn("/chart/M%26M.NS?", alerts.yahoo_url("M&M"))
        self.assertIn("/chart/BAJAJ-AUTO.NS?", alerts.yahoo_url("BAJAJ-AUTO"))
        self.assertIn("//query1.finance.yahoo.com/", alerts.yahoo_url("TCS", alerts.YAHOO_HOSTS[1]))
        self.assertFalse(hasattr(alerts, "stooq_url"))

    def test_offline_quotes_and_errors(self):
        q, errors = alerts.get_quotes(["RELIANCE", "TCS.NS", "ZZZZ", "NOPE"], QDIR)
        self.assertEqual(sorted(q), ["RELIANCE.NS", "TCS.NS"])
        self.assertEqual(q["TCS.NS"]["price"], 2510.0)
        self.assertEqual(len(errors), 2)
        self.assertIn("No data found", errors[0])

    def test_offline_gz(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(QDIR, "yahoo-RELIANCE.NS.json"), "rb") as src:
                with gzip.open(os.path.join(d, "yahoo-RELIANCE.NS.json.gz"), "wb") as dst:
                    dst.write(src.read())
            q, errors = alerts.get_quotes(["RELIANCE.NS"], d)
            self.assertEqual((q["RELIANCE.NS"]["price"], errors), (1311.2, []))
        finally:
            shutil.rmtree(d)


class FakeNet(object):
    """Stands in for urllib: answers each request with the next status code."""

    def __init__(self, codes):
        self.codes, self.calls, self.sleeps = list(codes), [], []
        with open(os.path.join(QDIR, "yahoo-RELIANCE.NS.json"), encoding="utf-8") as fh:
            self.body = fh.read()

    def urlopen(self, req, timeout=None):
        self.calls.append((req.full_url.split("/")[2], req.get_header("User-agent")))
        code = self.codes.pop(0)
        if code != 200:
            fp = io.BytesIO(b'{"chart":{"result":null,"error":{"code":"Not Found",'
                            b'"description":"No data found, symbol may be delisted"}}}' if code == 404 else b"")
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, fp)
        return io.BytesIO(self.body.encode("utf-8"))


class Network(unittest.TestCase):
    def setUp(self):
        self.saved = (alerts._urlopen, alerts._sleep)

    def tearDown(self):
        alerts._urlopen, alerts._sleep = self.saved

    def net(self, codes):
        f = FakeNet(codes)
        alerts._urlopen = f.urlopen
        alerts._sleep = f.sleeps.append
        return f

    def hosts(self, f):
        return [h.split(".")[0] for h, _ in f.calls]

    def test_query2_first_with_the_contract_user_agent(self):
        f = self.net([200])
        q, errors = alerts.get_quotes(["RELIANCE"])
        self.assertEqual((q["RELIANCE.NS"]["price"], errors), (1311.2, []))
        self.assertEqual(f.calls, [("query2.finance.yahoo.com", UA)])

    def test_one_retry_with_a_pause_on_429(self):
        f = self.net([429, 200])
        self.assertEqual(alerts.get_quotes(["RELIANCE"])[0]["RELIANCE.NS"]["price"], 1311.2)
        self.assertEqual(self.hosts(f), ["query2", "query2"])
        self.assertEqual(f.sleeps, [alerts.BACKOFF])

    def test_falls_back_to_query1_on_429_and_5xx(self):
        f = self.net([429, 429, 200])
        self.assertIn("RELIANCE.NS", alerts.get_quotes(["RELIANCE"])[0])
        self.assertEqual(self.hosts(f), ["query2", "query2", "query1"])
        f = self.net([503, 200])
        self.assertIn("RELIANCE.NS", alerts.get_quotes(["RELIANCE"])[0])
        self.assertEqual(self.hosts(f), ["query2", "query1"])
        f = self.net([500, 502])
        q, errors = alerts.get_quotes(["RELIANCE"])
        self.assertEqual(q, {})
        self.assertIn("query1.finance.yahoo.com/v8/finance/chart/RELIANCE.NS answered HTTP 502", errors[0])

    def test_a_404_is_yahoos_answer_and_a_403_is_not_retried(self):
        f = self.net([404])
        q, errors = alerts.get_quotes(["NOPE"])
        self.assertIn("No data found", errors[0])
        self.assertEqual(self.hosts(f), ["query2"])
        f = self.net([403])
        q, errors = alerts.get_quotes(["RELIANCE"])
        self.assertIn("HTTP 403", errors[0])
        self.assertEqual(self.hosts(f), ["query2"])

    def test_a_pause_between_symbols(self):
        f = self.net([200, 200])
        alerts.get_quotes(["RELIANCE", "TCS"])
        self.assertEqual(f.sleeps, [alerts.PAUSE])


class Rules(unittest.TestCase):
    def setUp(self):
        self.q, _ = alerts.get_quotes(["RELIANCE", "TCS", "INFY", "HDFCBANK"], QDIR)
        self.now = ist("2026-11-06T11:15")
        self.open = alerts.market_status(self.now)

    def at(self, text, rules, state=None, quotes=None):
        now = ist(text)
        return alerts.evaluate(rules, self.q if quotes is None else quotes, alerts.market_status(now),
                               state or {}, now)

    def test_a_level_rings_once(self):
        rules = [{"id": "hdfc-770", "symbol": "HDFCBANK", "type": "above", "price": 770}]
        rings, st = alerts.evaluate(rules, self.q, self.open, {}, self.now)
        self.assertEqual(len(rings), 1)
        self.assertIn("HDFCBANK.NS is ₹770.45: at or above your alert level ₹770.00", rings[0]["message"])
        rings, st = alerts.evaluate(rules, self.q, self.open, st, self.now)
        self.assertEqual(rings, [])

    def test_stop_and_rearm(self):
        rules = [{"id": "infy-stop", "symbol": "INFY.NS", "type": "stop", "price": 1172, "rearm": True}]
        rings, st = alerts.evaluate(rules, self.q, self.open, {}, self.now)
        self.assertIn("your stop ₹1,172.00", rings[0]["message"])
        self.q["INFY.NS"]["price"] = 1180.0
        rings, st = alerts.evaluate(rules, self.q, self.open, st, self.now)
        self.assertEqual(rings, [])
        self.assertNotIn("infy-stop", st["rung"])
        self.q["INFY.NS"]["price"] = 1169.5
        rings, st = alerts.evaluate(rules, self.q, self.open, st, self.now)
        self.assertEqual(len(rings), 1)

    def test_pct_move_from_previous_close(self):
        big = [{"id": "hdfc-3", "symbol": "HDFCBANK", "type": "pct_move", "pct": 3}]
        self.assertEqual(alerts.evaluate(big, self.q, self.open, {}, self.now)[0], [])
        up = [{"id": "hdfc-1", "symbol": "HDFCBANK", "type": "pct_move", "pct": 1, "direction": "up"}]
        rings, st = alerts.evaluate(up, self.q, self.open, {}, self.now)
        self.assertIn("HDFCBANK.NS moved +1.38% to ₹770.45 (from ₹760.00)", rings[0]["message"])
        self.assertEqual(alerts.evaluate(up, self.q, self.open, st, self.now)[0], [])
        down = [{"id": "hdfc-dn", "symbol": "HDFCBANK", "type": "pct_move", "pct": 1, "direction": "down"}]
        self.assertEqual(alerts.evaluate(down, self.q, self.open, {}, self.now)[0], [])

    def test_opening_bell_once_per_trading_day(self):
        rules = [{"id": "bell", "type": "opening_bell"}]
        closed = alerts.market_status(ist("2026-11-06T09:00"))
        self.assertEqual(alerts.evaluate(rules, {}, closed, {}, ist("2026-11-06T09:00"))[0], [])
        rings, st = alerts.evaluate(rules, {}, self.open, {}, self.now)
        self.assertEqual(rings[0]["message"], "Opening bell: NSE is open (09:15 IST).")
        self.assertEqual(alerts.evaluate(rules, {}, self.open, st, ist("2026-11-06T13:00"))[0], [])
        nxt = alerts.market_status(ist("2026-11-09T09:16"))
        self.assertEqual(len(alerts.evaluate(rules, {}, nxt, st, ist("2026-11-09T09:16"))[0]), 1)

    def test_the_plan_gives_a_stop_a_target_and_flat_by(self):
        rules = plan_rules()
        self.assertEqual([(r["id"], r["type"]) for r in rules],
                         [("reliance.ns-plan-stop", "stop"), ("reliance.ns-plan-target", "target"),
                          ("reliance.ns-flat-by", "time")])
        self.assertEqual((rules[0]["price"], rules[1]["price"]), (1301.2, 1317.1))
        self.assertEqual((rules[2]["at"], rules[2]["label"], rules[2]["mis"]), ("15:10", "flat by", True))
        self.assertTrue(all(r["date"] == "2026-11-06" for r in rules))
        rings, st = self.at("2026-11-06 11:15", rules)            # the real 11:13 quote, 1311.20
        self.assertEqual(rings, [])
        q = json.loads(json.dumps(self.q))
        q["RELIANCE.NS"]["price"] = 1300.9
        rings, st = self.at("2026-11-06 11:30", rules, st, q)
        self.assertEqual([r["id"] for r in rings], ["reliance.ns-plan-stop"])
        self.assertIn("RELIANCE.NS is ₹1,300.90: at or below your stop ₹1,301.20 (plan stop: opening-range "
                      "midpoint)", rings[0]["message"])
        q["RELIANCE.NS"]["price"] = 1317.1
        self.assertEqual([r["id"] for r in self.at("2026-11-06 12:00", rules, st, q)[0]],
                         ["reliance.ns-plan-target"])

    def test_mis_warning_rule(self):
        rules = plan_rules(mis_warning="15:05")
        self.assertEqual([r["id"] for r in rules][2:], ["reliance.ns-mis-warning", "reliance.ns-flat-by"])
        with self.assertRaises(alerts.AlertError):
            plan_rules(mis_warning="3pm")

    def test_flat_by_rings_once_per_trading_day(self):
        rules = plan_rules()[2:]
        self.assertEqual(self.at("2026-11-06 15:09", rules)[0], [])
        rings, st = self.at("2026-11-06 15:10", rules)
        self.assertEqual(len(rings), 1)
        msg = rings[0]["message"]
        self.assertIn("Flat by: it is 15:10 IST (set for 15:10), the market closes at 15:30", msg)
        self.assertIn("trader-broker flatten --symbol RELIANCE.NS", msg)
        self.assertIn("squared off by the broker", msg)
        self.assertEqual(self.at("2026-11-06 15:20", rules, st)[0], [])
        rings, _ = self.at("2026-11-06 15:40", rules)
        self.assertIn("the market has closed", rings[0]["message"])
        self.assertEqual(self.at("2026-11-09 15:11", rules, st)[0], [])     # plan rules: their day only

    def test_time_alerts_skip_closed_days(self):
        rule = [{"id": "flat", "type": "time", "at": "15:10", "label": "flat by"}]
        self.assertEqual(self.at("2026-11-07 15:11", rule)[0], [])         # Saturday
        self.assertEqual(self.at("2026-11-10 15:11", rule)[0], [])         # Diwali Balipratipada
        rings, st = self.at("2026-11-11 15:10", rule)
        self.assertEqual(len(rings), 1)
        self.assertEqual(st["rung"]["flat"], "2026-11-11T09:40:00Z")

    def test_the_example_rules_are_valid(self):
        with open(os.path.join(ROOT, "trader-alerts", "alerts.example.json"), encoding="utf-8") as fh:
            rules = json.load(fh)["rules"]
        for r in rules:
            alerts.validate(r)
        self.assertEqual([r for r in rules if r["id"].startswith("reliance.ns-")],
                         plan_rules())

    def test_bad_rules(self):
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "moon"}], {}, self.open, {}, self.now)
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "above", "symbol": "TCS"}], {}, self.open, {}, self.now)
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "time", "at": "3pm"}], {}, self.open, {}, self.now)
        with self.assertRaises(alerts.AlertError):
            alerts.rules_from_plan({"symbol": "RELIANCE.NS", "stop": 1301.2})
        with self.assertRaises(alerts.AlertError):
            alerts.rules_from_plan({"stop": 1301.2, "target": 1317.1})


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.rules = os.path.join(self.d, "alerts.json")
        self.state = os.path.join(self.d, "state", "alerts.json")
        self.prices = os.path.join(self.d, "prices.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def check(self, now):
        return run(["check", "--rules", self.rules, "--state", self.state, "--prices-out", self.prices,
                    "--offline", QDIR, "--now", now])

    def test_a_trade_from_the_plan(self):
        code, text, _ = run(["add", "--from-plan", "--plan", PLAN, "--rules", self.rules, "--state", self.state,
                             "--config", os.path.join(self.d, "none")])
        self.assertEqual(code, 0)
        self.assertIn("Added reliance.ns-flat-by", text)
        self.assertEqual(run(["add", "--rules", self.rules, "--opening-bell"])[0], 0)

        code, text, _ = self.check("2026-11-06 11:14")
        self.assertEqual(code, 0)                                      # the opening bell rings first
        self.assertIn("NSE OPEN (2026-11-06 11:14 IST", text)
        code, text, _ = self.check("2026-11-06 11:15")
        self.assertEqual(code, 1)
        self.assertIn("Nothing hit. 4 rule(s) watched today, RELIANCE.NS ₹1,311.20.", text)
        with open(self.prices, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc["prices"], {"RELIANCE.NS": 1311.2})
        self.assertEqual(doc["currency"], "INR")
        self.assertEqual(doc["detail"]["RELIANCE.NS"]["time"], "2026-11-06T05:43:14Z")
        self.assertEqual(doc["detail"]["RELIANCE.NS"]["fetched"], "2026-11-06T05:45:00Z")
        self.assertTrue(doc["as_of_ist"].endswith("+05:30"))

        code, text, _ = self.check("2026-11-06 15:10")
        self.assertEqual(code, 0)
        self.assertIn("ALERT Flat by", text)
        self.assertIn("not an order", text)
        self.assertEqual(self.check("2026-11-06 15:12")[0], 1)
        code, text, _ = self.check("2026-11-09 09:20")
        self.assertIn("ALERT Opening bell", text)
        code, text, _ = self.check("2026-11-09 09:25")
        self.assertIn("Nothing hit. 1 rule(s) watched today.", text)

        # a new plan replaces the plan rules and re-arms them
        run(["add", "--from-plan", "--plan", PLAN, "--rules", self.rules, "--state", self.state,
             "--config", os.path.join(self.d, "none")])
        with open(self.rules, encoding="utf-8") as fh:
            ids = [r["id"] for r in json.load(fh)["rules"]]
        self.assertEqual(sorted(ids), ["opening-bell", "reliance.ns-flat-by", "reliance.ns-plan-stop",
                                       "reliance.ns-plan-target"])
        with open(self.state, encoding="utf-8") as fh:
            self.assertNotIn("reliance.ns-flat-by", json.load(fh)["rung"])

    def test_config_mis_warning(self):
        cfg = os.path.join(self.d, "config.json")
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump({"mis_warning": "15:05"}, fh)
        code, text, _ = run(["add", "--from-plan", "--plan", PLAN, "--rules", self.rules, "--state", self.state,
                             "--config", cfg])
        self.assertEqual(code, 0)
        self.assertIn("Added reliance.ns-mis-warning", text)

    def test_levels_evening_and_morning(self):
        for args in (["--opening-bell"], ["--symbol", "TCS", "--above", "2600"], ["--symbol", "INFY", "--stop", "1100"]):
            code, _, _ = run(["add", "--rules", self.rules] + args)
            self.assertEqual(code, 0)
        code, _, err = run(["add", "--rules", self.rules, "--symbol", "TCS.NS", "--above", "2600"])
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)

        code, text, _ = self.check("2026-11-06T20:00")
        self.assertEqual(code, 1)
        self.assertIn("Nothing hit", text)
        with open(self.prices, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc["prices"], {"TCS.NS": 2510.0, "INFY.NS": 1171.0})

        code, text, _ = self.check("2026-11-09T09:16")
        self.assertEqual(code, 0)
        self.assertIn("ALERT Opening bell", text)
        code, text, _ = self.check("2026-11-09T09:45")
        self.assertEqual(code, 1)

        code, text, _ = run(["list", "--rules", self.rules, "--state", self.state])
        self.assertIn("rang", text)
        self.assertEqual(run(["reset", "opening-bell", "--state", self.state])[0], 0)
        self.assertEqual(run(["reset", "opening-bell", "--state", self.state])[0], 1)

    def test_add_time_and_target(self):
        code, text, _ = run(["add", "--rules", self.rules, "--at", "15:10", "--label", "flat by", "--mis",
                             "--symbol", "reliance"])
        self.assertEqual(code, 0)
        code, text, _ = run(["add", "--rules", self.rules, "--symbol", "RELIANCE", "--target", "1317.1",
                             "--date", "2026-11-06"])
        self.assertEqual(code, 0)
        with open(self.rules, encoding="utf-8") as fh:
            rules = json.load(fh)["rules"]
        self.assertEqual(rules[0], {"id": "time-1510", "type": "time", "at": "15:10", "label": "flat by",
                                    "symbol": "RELIANCE.NS", "mis": True})
        self.assertEqual(rules[1], {"id": "reliance.ns-target-1317.10", "symbol": "RELIANCE.NS", "type": "target",
                                    "price": 1317.1, "date": "2026-11-06"})
        self.assertEqual(run(["add", "--rules", self.rules, "--at", "3pm"])[0], 1)
        code, _, err = run(["add", "--rules", self.rules, "--from-plan", "--plan", os.path.join(self.d, "none")])
        self.assertEqual(code, 1)
        self.assertIn("no plan", err)

    def test_quote_write_merges(self):
        code, text, _ = run(["quote", "RELIANCE", "TCS", "--offline", QDIR, "--write", self.prices,
                             "--now", "2026-11-06T05:45:00Z"])
        self.assertEqual(code, 0)
        self.assertIn("RELIANCE.NS  ₹1,311.20 (-0.17% vs prev close ₹1,313.40)  2026-11-06 11:13 IST", text)
        self.assertIn("delayed", text)
        self.assertNotIn("$", text)
        run(["quote", "INFY", "--offline", QDIR, "--write", self.prices])
        with open(self.prices, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(sorted(doc["prices"]), ["INFY.NS", "RELIANCE.NS", "TCS.NS"])
        self.assertEqual(doc["detail"]["TCS.NS"]["prev_close"], 2495.7)
        self.assertEqual(doc["detail"]["TCS.NS"]["fetched"], "2026-11-06T05:45:00Z")

    def test_quote_holdings_from_portfolio_and_plan(self):
        pf = os.path.join(self.d, "portfolio.json")
        with open(pf, "w", encoding="utf-8") as fh:
            json.dump({"cash": 0, "positions": [{"symbol": "INFY", "shares": 1}]}, fh)
        code, text, _ = run(["quote", "--holdings", "--portfolio", pf, "--plan", PLAN, "--offline", QDIR, "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(json.loads(text)["quotes"]), ["INFY.NS", "RELIANCE.NS"])

    def test_clock_exit_codes(self):
        code, text, _ = run(["clock", "--now", "2026-11-06 10:15"])
        self.assertEqual(code, 0)
        self.assertIn("NSE OPEN", text)
        self.assertIn("09:15 to 15:30 IST", text)
        code, text, _ = run(["clock", "--now", "2026-12-25T10:00"])
        self.assertEqual(code, 1)
        self.assertIn("Christmas", text)
        self.assertIn("Next open: 2026-12-28 09:15 IST", text)
        code, text, _ = run(["clock", "--now", "2027-02-01T10:00"])
        self.assertIn("market_india.json", text)                      # 2027 not loaded yet

    def test_check_without_rules(self):
        code, _, err = run(["check", "--rules", self.rules, "--offline", QDIR])
        self.assertEqual(code, 1)
        self.assertIn("no rules", err)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-alerts", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-alerts\n"))
        low = " ".join(text.lower().split())
        for must in ("nse", "ist", "9:15", "15:30", "15:10", "market_india.json", "not financial advice", "yes"):
            self.assertIn(must, low)
        for never in ("nyse", "new york", "stooq", "\u2014"):
            self.assertNotIn(never, low)
        self.assertNotRegex(low, r"\$\d")


if __name__ == "__main__":
    unittest.main()

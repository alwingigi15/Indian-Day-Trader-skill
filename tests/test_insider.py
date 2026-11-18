"""trader-insider tests.

bulk-2026-11-05.csv and block-2026-11-05.csv are NSE's real published files for
5 Nov 2026 (nsearchives.nseindia.com/content/equities/bulk.csv and block.csv;
the block file had no records that day). SAMPLE-pit.csv / SAMPLE-pit.json are
SYNTHETIC: NSE's insider-trading page only works in a browser, so the rows are
made up in the exact column layout of its CSV download (including the literal
"\\n" NSE puts in its header cells) and of its JSON.
"""

import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, load

ins = load("trader-insider", "insider")
DIR = os.path.join(FIX, "trader-insider")
BULK = os.path.join(DIR, "bulk-2026-11-05.csv")
BLOCK = os.path.join(DIR, "block-2026-11-05.csv")
PIT_CSV = os.path.join(DIR, "SAMPLE-pit.csv")
PIT_JSON = os.path.join(DIR, "SAMPLE-pit.json")


def raw(path):
    with open(path, "rb") as fh:
        return fh.read()


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = ins.main(args)
    return code, out.getvalue(), err.getvalue()


class Basics(unittest.TestCase):
    def test_symbols_get_the_nse_suffix(self):
        self.assertEqual(ins.nse_symbol("reliance"), "RELIANCE.NS")
        self.assertEqual(ins.nse_symbol("RELIANCE.NS"), "RELIANCE.NS")
        self.assertEqual(ins.nse_symbol("TCS.BO"), "TCS.BO")
        self.assertEqual(ins.nse_symbol("M&M"), "M&M.NS")

    def test_rupees_in_lakh_and_crore(self):
        self.assertEqual(ins.inr(950), "₹950.00")
        self.assertEqual(ins.inr(1234567), "₹12.35 lakh")
        self.assertEqual(ins.inr(52300000000), "₹5,230.00 crore")

    def test_ist_is_plus_five_thirty(self):
        utc = datetime.datetime(2026, 11, 5, 4, 0)
        self.assertEqual(ins.to_ist(utc), datetime.datetime(2026, 11, 5, 9, 30))
        self.assertEqual(ins.ist_to_utc(datetime.datetime(2026, 11, 5, 9, 30)), utc)
        self.assertTrue(ins.ist_iso(utc).endswith("+05:30"))

    def test_dates_in_every_nse_format(self):
        for s in ("05-NOV-2026", "05-Nov-2026 18:30", "05/11/2026", "2026-11-05", "5 November 2026"):
            self.assertEqual(ins.iso_date(s), "2026-11-05", s)
        self.assertIsNone(ins.iso_date("NO RECORDS"))

    def test_headers_with_nse_literal_backslash_n(self):
        self.assertEqual(ins._key("NAME OF THE ACQUIRER/DISPOSER \\n"), "nameoftheacquirerdisposer")
        self.assertEqual(ins._key("Trade Price / Wght. Avg. Price"), "tradepricewghtavgprice")

    def test_trading_days_skip_weekends_and_nse_holidays(self):
        # Mon 19 Oct 2026 -> Fri 23 Oct: 20 Oct is Dussehra, so 21, 22, 23 = 3
        self.assertEqual(ins.trading_days_between("2026-10-19", "2026-10-23", ins.NSE_HOLIDAYS), 3)
        # Fri 6 Nov -> Mon 9 Nov: the weekend does not count
        self.assertEqual(ins.trading_days_between("2026-11-06", "2026-11-09", ins.NSE_HOLIDAYS), 1)

    def test_holidays_extend_from_market_file(self):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "market_india.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"holidays": {"2027-01-26": "Republic Day"}}, fh)
            hol = ins.load_holidays(path)
            self.assertIn("2027-01-26", hol)
            self.assertIn("2026-11-10", hol)
            self.assertEqual(ins.load_holidays(os.path.join(d, "missing.json")), ins.NSE_HOLIDAYS)
        finally:
            shutil.rmtree(d)


class Deals(unittest.TestCase):
    def setUp(self):
        self.bulk = ins.parse_deals(raw(BULK), "bulk")

    def test_real_bulk_file(self):
        self.assertEqual(len(self.bulk), 93)
        first = self.bulk[0]
        self.assertEqual(first["symbol"], "ADVENTHTL.NS")
        self.assertEqual(first["who"], "MONEKA ANIL PODDAR")
        self.assertEqual(first["side"], "buy")
        self.assertEqual(first["qty"], 295726)
        self.assertEqual(first["price"], 118.40)
        self.assertEqual(first["value"], round(295726 * 118.40, 2))
        self.assertEqual(first["trade_date"], "2026-11-05")
        self.assertEqual(sum(1 for r in self.bulk if r["side"] == "sell"), 44)

    def test_no_records_block_file(self):
        self.assertEqual(ins.parse_deals(raw(BLOCK), "block"), [])

    def test_ids_are_unique(self):
        ids = [r["id"] for r in self.bulk]
        self.assertEqual(len(ids), len(set(ids)))

    def test_who_filter_needs_every_word(self):
        self.assertTrue(ins.who_match({"who": "RAVI AMIT JALAN"}, ["jalan"]))
        self.assertTrue(ins.who_match({"who": "RAVI AMIT JALAN"}, ["Ravi Jalan"]))
        self.assertFalse(ins.who_match({"who": "RAVI AMIT JALAN"}, ["Ravi Kumar"]))
        picked = ins.select(self.bulk, who=["poddar"])
        self.assertTrue(picked)
        self.assertTrue(all("PODDAR" in r["who"] for r in picked))

    def test_symbol_side_and_value_filters(self):
        self.assertEqual([r["symbol"] for r in ins.select(self.bulk, symbols=["adventhtl"])][:1],
                         ["ADVENTHTL.NS"])
        self.assertTrue(all(r["side"] == "sell" for r in ins.select(self.bulk, side="sell")))
        big = ins.select(self.bulk, min_value=5e7)
        self.assertTrue(all(r["value"] >= 5e7 for r in big))

    def test_describe_in_rupees(self):
        line = ins.describe(self.bulk[0], {"ADVENTHTL.NS": "your watchlist"})
        self.assertIn("BULK", line)
        self.assertIn("₹118.40", line)
        self.assertIn("₹3.50 crore", line)
        self.assertIn("<- ADVENTHTL.NS (your watchlist)", line)
        self.assertNotIn("$", line)


class Pit(unittest.TestCase):
    def setUp(self):
        self.rows = ins.parse_pit(raw(PIT_CSV), ins.NSE_HOLIDAYS)
        self.by = {r["symbol"]: r for r in self.rows}

    def test_csv_rows(self):
        self.assertEqual(sorted(self.by), ["HDFCBANK.NS", "INFY.NS", "RELIANCE.NS", "TCS.NS"])
        rel = self.by["RELIANCE.NS"]
        self.assertEqual(rel["category"], "promoter group")
        self.assertEqual(rel["side"], "buy")
        self.assertEqual(rel["qty"], 50000)
        self.assertEqual(rel["price"], 1485.0)
        self.assertEqual(rel["trade_date"], "2026-11-03")
        self.assertEqual(rel["disclosed"], "2026-11-05")
        self.assertEqual(rel["days_to_disclose"], 2)
        self.assertTrue(rel["market"])
        self.assertFalse(rel["late"])

    def test_categories(self):
        self.assertEqual(self.by["TCS.NS"]["category"], "director")
        self.assertEqual(self.by["INFY.NS"]["category"], "KMP")
        self.assertEqual(self.by["HDFCBANK.NS"]["category"], "promoter")

    def test_late_disclosure_counts_nse_trading_days(self):
        tcs = self.by["TCS.NS"]   # traded 27 Oct, told the company 4 Nov: 6 trading days
        self.assertEqual(tcs["trading_days_to_company"], 6)
        self.assertTrue(tcs["late"])
        self.assertIn("LATE", ins.describe(tcs))

    def test_esop_and_pledge_are_not_market_trades(self):
        self.assertFalse(self.by["INFY.NS"]["market"])
        self.assertEqual(self.by["HDFCBANK.NS"]["side"], "pledge")
        market = ins.select(self.rows, market_only=True)
        self.assertEqual(sorted(r["symbol"] for r in market), ["RELIANCE.NS", "TCS.NS"])

    def test_category_filter(self):
        promo = ins.select(self.rows, categories=["promoter"])
        self.assertEqual(sorted(r["symbol"] for r in promo), ["HDFCBANK.NS", "RELIANCE.NS"])

    def test_json_matches_csv(self):
        j = ins.parse_pit(raw(PIT_JSON), ins.NSE_HOLIDAYS)
        self.assertEqual(len(j), 1)
        rel = self.by["RELIANCE.NS"]
        for k in ("symbol", "who", "category", "side", "qty", "price", "trade_date", "disclosed"):
            self.assertEqual(j[0][k], rel[k], k)

    def test_wrong_file_is_refused(self):
        with self.assertRaises(ins.InsiderError):
            ins.parse_pit(raw(BULK), ins.NSE_HOLIDAYS)
        with self.assertRaises(ins.InsiderError):
            ins.parse_pit(b'{"rows": 1}', ins.NSE_HOLIDAYS)


class State(unittest.TestCase):
    def test_first_run_shows_last_week_then_only_new(self):
        rows = ins.parse_pit(raw(PIT_CSV), ins.NSE_HOLIDAYS)
        today = datetime.date(2026, 11, 6)
        first = ins.new_since(rows, {"seen": [], "last_check": None}, today)
        self.assertEqual(sorted(r["symbol"] for r in first), ["INFY.NS", "RELIANCE.NS", "TCS.NS"])
        state = {"seen": [r["id"] for r in rows], "last_check": "2026-11-06T09:00:00+05:30"}
        self.assertEqual(ins.new_since(rows, state, today), [])


class Cli(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.none = os.path.join(self.dir, "none.json")
        self.base = ["--portfolio", self.none, "--plan", self.none, "--market-file", self.none]

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_deals_offline(self):
        code, out, _ = run(["deals", "--bulk-file", BULK, "--block-file", BLOCK, "--who", "jalan",
                            "--watchlist", self.none] + self.base)
        self.assertEqual(code, 0)
        self.assertIn("RAVI AMIT JALAN BUY", out)
        self.assertIn("Bulk deal:", out)

    def test_no_match_returns_one(self):
        code, out, _ = run(["deals", "--bulk-file", BULK, "--block-file", BLOCK, "--who", "nobody here",
                            "--watchlist", self.none] + self.base)
        self.assertEqual(code, 1)
        self.assertIn("No matching deals", out)

    def test_watchlist_flag_and_mine(self):
        wl = os.path.join(self.dir, "watchlist.json")
        with open(wl, "w", encoding="utf-8") as fh:
            json.dump({"hits": [{"symbol": "TCS.NS"}]}, fh)
        code, out, _ = run(["pit", "--pit-file", PIT_CSV, "--mine", "--watchlist", wl] + self.base)
        self.assertEqual(code, 0)
        self.assertIn("TCS.NS (your watchlist)", out)
        self.assertNotIn("RELIANCE.NS", out)

    def test_pit_json_output(self):
        code, out, _ = run(["pit", "--pit-file", PIT_CSV, "--side", "buy", "--json",
                            "--watchlist", self.none] + self.base)
        self.assertEqual(code, 0)
        rows = json.loads(out)
        self.assertEqual(sorted(r["symbol"] for r in rows), ["INFY.NS", "RELIANCE.NS"])

    def test_new_alerts_once(self):
        state = os.path.join(self.dir, "state", "insider.json")
        args = ["new", "--no-deals", "--pit-file", PIT_CSV, "--state", state, "--today", "2026-11-06",
                "--watchlist", self.none] + self.base
        code, out, _ = run(args)
        self.assertEqual(code, 0)
        self.assertIn("NEW since", out)
        with open(state, encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(len(saved["seen"]), 4)
        self.assertTrue(saved["last_check"].endswith("+05:30"))
        code, out, _ = run(args)
        self.assertEqual(code, 1)
        self.assertIn("Nothing new", out)

    def test_dry_run_keeps_state(self):
        state = os.path.join(self.dir, "s.json")
        run(["new", "--no-deals", "--pit-file", PIT_CSV, "--state", state, "--today", "2026-11-06",
             "--dry-run", "--watchlist", self.none] + self.base)
        self.assertFalse(os.path.exists(state))

    def test_missing_file_is_a_clean_error(self):
        code, _, err = run(["pit", "--pit-file", os.path.join(self.dir, "nope.csv"),
                            "--watchlist", self.none] + self.base)
        self.assertEqual(code, 2)
        self.assertIn("insider:", err)


if __name__ == "__main__":
    unittest.main()

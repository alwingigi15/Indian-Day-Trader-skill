"""trader-news tests.

Every fixture is real data saved on 6 Nov 2026: Yahoo's headline RSS for
TCS.NS, INFY.NS and M&M.NS (RELIANCE.NS's feed was empty that morning),
Yahoo chart JSON for RELIANCE.NS and TCS.NS, and NSE's public announcements
feed (nsearchives.nseindia.com/content/RSS/Online_announcements.xml).
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

news = load("trader-news", "news")
DIR = os.path.join(FIX, "trader-news")
NOW = "2026-11-06T12:00:00+05:30"
UTC = datetime.timezone.utc


def raw(name):
    with open(os.path.join(DIR, name), "rb") as fh:
        return fh.read()


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = news.main(args)
    return code, out.getvalue(), err.getvalue()


class Basics(unittest.TestCase):
    def test_symbols(self):
        self.assertEqual(news.nse_symbol("reliance"), "RELIANCE.NS")
        self.assertEqual(news.nse_symbol("TCS.BO"), "TCS.BO")
        self.assertEqual(news.yahoo_symbol("M&M"), "M%26M.NS")
        self.assertEqual(news.yahoo_symbol("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")
        self.assertEqual(news.bare("RELIANCE.NS"), "RELIANCE")

    def test_ist(self):
        utc = datetime.datetime(2026, 11, 6, 6, 10, tzinfo=UTC)
        self.assertEqual(news.to_ist(utc), datetime.datetime(2026, 11, 6, 11, 40))
        self.assertEqual(news.ist_iso(utc), "2026-11-06T11:40:00+05:30")

    def test_times_in_every_format(self):
        want = datetime.datetime(2026, 11, 6, 6, 10, 47, tzinfo=UTC)
        self.assertEqual(news.parse_time("06-Nov-2026 11:40:47"), want)      # NSE, IST
        self.assertEqual(news.parse_time("Fri, 06 Nov 2026 06:10:47 +0000"), want)
        self.assertEqual(news.parse_time("2026-11-06T11:40:47+05:30"), want)
        self.assertEqual(news.parse_time("2026-11-06 11:40:47"), want)       # no offset = IST
        self.assertIsNone(news.parse_time("soon"))

    def test_company_names_normalise(self):
        self.assertEqual(news.norm_name("Mahindra & Mahindra Limited"),
                         news.norm_name("MAHINDRA AND MAHINDRA LTD."))
        self.assertNotEqual(news.norm_name("Reliance Industries Limited"),
                            news.norm_name("Reliance MF - Reliance Interval Fund"))


class Feeds(unittest.TestCase):
    def test_yahoo_rss(self):
        items = news.parse_rss(raw("yahoo-TCS.NS.xml"), "TCS.NS")
        self.assertEqual(len(items), 14)
        self.assertTrue(items[0]["title"].startswith("TCS and Workato"))
        self.assertEqual(items[0]["time"], datetime.datetime(2026, 10, 26, 13, 0, tzinfo=UTC))
        self.assertEqual(news.parse_rss(raw("yahoo-RELIANCE.NS.xml"), "RELIANCE.NS"), [])

    def test_bad_xml(self):
        with self.assertRaises(news.NewsError):
            news.parse_rss(b"<rss><channel>", "X")
        with self.assertRaises(news.NewsError):
            news.parse_nse_rss(b"not xml")

    def test_nse_feed(self):
        items = news.parse_nse_rss(raw("nse-announcements.xml"))
        self.assertEqual(len(items), 63)
        first = items[0]
        self.assertEqual(first["company"], "GUJARAT ENERGY LIMITED")
        self.assertEqual(first["nse_symbol"], "GUJGASLTD")       # from the attachment name
        self.assertTrue(first["subject"].startswith("Arrangements for strategic"))
        self.assertEqual(first["time"], datetime.datetime(2026, 11, 6, 6, 10, 47, tzinfo=UTC))
        esop = items[1]
        self.assertEqual(esop["subject"], "ESOP/ESOS/ESPS")       # the |SUBJECT: part
        no_sym = [i for i in items if i["company"] == "Mahindra & Mahindra Limited"][0]
        self.assertIsNone(no_sym["nse_symbol"])

    def test_chart(self):
        m = news.parse_chart(raw("chart-RELIANCE.NS.json"), "RELIANCE.NS")
        self.assertEqual(m["price"], 1313.8)
        self.assertEqual(m["prev_close"], 1300.0)
        self.assertEqual(m["pct"], 1.06)
        self.assertEqual(m["name"], "Reliance Industries Limited")
        with self.assertRaises(news.NewsError):
            news.parse_chart(b"{}", "X")


class Matching(unittest.TestCase):
    def setUp(self):
        self.items = news.parse_nse_rss(raw("nse-announcements.xml"))

    def test_by_symbol_and_by_name(self):
        watch = {"GODREJAGRO.NS": None, "M&M.NS": None, "RELIANCE.NS": None}
        names = {"M&M.NS": "Mahindra & Mahindra Ltd.", "RELIANCE.NS": "Reliance Industries Limited",
                 "GODREJAGRO.NS": "Godrej Agrovet Limited"}
        got = news.match_announcements(self.items, watch, names)
        by = {}
        for it in got:
            by.setdefault(it["ticker"], []).append(it)
        self.assertEqual(sorted(by), ["GODREJAGRO.NS", "M&M.NS"])
        # Godrej: 2 items carry the symbol in the link, 4 only the company name
        self.assertEqual(len(by["GODREJAGRO.NS"]), 6)
        self.assertEqual(sum(1 for i in by["GODREJAGRO.NS"] if i["nse_symbol"] is None), 4)
        # "Reliance MF ..." mutual-fund NAVs are not Reliance Industries
        self.assertNotIn("RELIANCE.NS", by)

    def test_symbol_match_needs_no_name(self):
        got = news.match_announcements(self.items, {"NACLIND.NS": None}, {})
        self.assertEqual({i["subject"] for i in got}, {"Change in Management", "Outcome of Board Meeting"})

    def test_tags_and_importance(self):
        watch = {"GODREJAGRO.NS": None, "NACLIND.NS": None, "M&M.NS": None, "POWERMECH.NS": None}
        names = {"M&M.NS": "Mahindra & Mahindra Limited", "GODREJAGRO.NS": "Godrej Agrovet Limited"}
        got = news.tag(news.match_announcements(self.items, watch, names), watch)
        subj = {}
        for it in got:
            subj.setdefault(it["subject"], it)
        self.assertIn("earnings", subj["Financial Results Updates"]["tags"])
        self.assertTrue(subj["Financial Results Updates"]["important"])
        self.assertIn("management", subj["Change in Management"]["tags"])
        self.assertIn("orders", subj["Bagging/Receiving of orders/contracts"]["tags"])
        paper = subj["Copy of Newspaper Publication"]
        self.assertTrue(paper["routine"])
        self.assertFalse(paper["important"])

    def test_headline_mentions(self):
        watch = {"TCS.NS": "Tata Consultancy Services Limited", "INFY.NS": None}
        items = news.tag(news.parse_rss(raw("yahoo-TCS.NS.xml"), "TCS.NS"), watch)
        self.assertTrue(all("TCS.NS" in i["touches"] for i in items))
        pats = news.mention_patterns({"M&M.NS": None})
        self.assertTrue(pats["M&M.NS"].search("Shares of NSE: M&M rose"))

    def test_headline_tags(self):
        it = {"kind": "headline", "ticker": "M&M.NS", "title":
              "India's Mahindra plans to double EV capacity as quarterly profit rises", "summary": ""}
        news.tag([it], {"M&M.NS": None})
        self.assertIn("earnings", it["tags"])


class SavedFile(unittest.TestCase):
    def test_csv_from_the_nse_page(self):
        data = ("\"SYMBOL \\n\",\"COMPANY NAME \\n\",\"SUBJECT \\n\",\"DETAILS \\n\",\"BROADCAST DATE/TIME \\n\","
                "\"ATTACHMENT \\n\"\n"
                "\"RELIANCE\",\"Reliance Industries Limited\",\"Outcome of Board Meeting\","
                "\"Board approved the results\",\"17-Oct-2026 19:05:11\",\"https://example.invalid/a.pdf\"\n")
        items = news.parse_announcements_file(data.encode("utf-8"))
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual(it["nse_symbol"], "RELIANCE")
        self.assertEqual(it["subject"], "Outcome of Board Meeting")
        self.assertEqual(it["time"], datetime.datetime(2026, 10, 17, 13, 35, 11, tzinfo=UTC))
        self.assertEqual(it["source"], "NSE (file)")

    def test_json_from_the_nse_page(self):
        doc = [{"symbol": "TCS", "sm_name": "Tata Consultancy Services Limited",
                "desc": "Financial Result Updates", "attchmntText": "Q2 results",
                "an_dt": "09-Oct-2026 16:01:00", "attchmntFile": "https://example.invalid/t.pdf"}]
        items = news.parse_announcements_file(json.dumps(doc))
        self.assertEqual(items[0]["nse_symbol"], "TCS")
        self.assertEqual(items[0]["link"], "https://example.invalid/t.pdf")

    def test_wrong_file(self):
        with self.assertRaises(news.NewsError):
            news.parse_announcements_file(b"a,b\n1,2\n")


class Check(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.state = os.path.join(self.dir, "state", "news.json")
        self.universe = os.path.join(self.dir, "universe.txt")
        with open(self.universe, "w", encoding="utf-8") as fh:
            fh.write("# test\nGODREJAGRO.NS|Godrej Agrovet Limited\nM&M.NS|Mahindra & Mahindra Ltd.\n")
        none = os.path.join(self.dir, "none.json")
        self.base = ["--offline", DIR, "--now", NOW, "--state", self.state, "--universe", self.universe,
                     "--watchlist", none, "--plan", none, "--portfolio", none]

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_check_offline(self):
        code, out, _ = run(["--tickers", "GODREJAGRO", "RELIANCE", "TCS"] + self.base)
        self.assertEqual(code, 0)
        self.assertIn("RELIANCE.NS  ₹1,313.80 +1.06% vs yesterday's close ₹1,300.00 at 11:42 IST", out)
        self.assertIn("Godrej Agrovet Limited: Financial Results Updates", out)
        self.assertIn("! 2026-11-06 11:30 IST", out)
        self.assertIn("Checked 2026-11-06 12:00 IST", out)
        self.assertNotIn("$", out)
        self.assertNotIn("Reliance MF", out)

    def test_routine_hidden_unless_asked(self):
        code, out, _ = run(["--tickers", "M&M", "--no-moves"] + self.base)
        self.assertNotIn("Newspaper", out)
        code, out, _ = run(["--tickers", "M&M", "--no-moves", "--routine", "--all"] + self.base)
        self.assertIn("Mahindra & Mahindra Limited: Copy of Newspaper Publication", out)

    def test_shows_once_then_nothing_new(self):
        args = ["--tickers", "GODREJAGRO", "--no-moves"] + self.base
        self.assertEqual(run(args)[0], 0)
        code, out, _ = run(args)
        self.assertEqual(code, 1)
        self.assertIn("Nothing new.", out)
        code, out, _ = run(args + ["--all"])
        self.assertIn("Financial Results Updates", out)

    def test_feed_down_falls_back_to_headlines(self):
        empty = os.path.join(self.dir, "offline")
        shutil.copytree(DIR, empty)
        os.remove(os.path.join(empty, "nse-announcements.xml"))
        args = list(self.base)
        args[args.index(DIR)] = empty
        code, out, _ = run(["--tickers", "TCS", "--hours", "400"] + args)
        self.assertEqual(code, 0)
        self.assertIn("NSE announcements unavailable", out)
        self.assertIn("--announcements-file", out)
        self.assertIn("TCS and Workato", out)

    def test_json(self):
        code, out, _ = run(["--tickers", "GODREJAGRO", "--json"] + self.base)
        doc = json.loads(out)
        self.assertEqual(doc["checked_ist"], "2026-11-06T12:00:00+05:30")
        self.assertTrue(all(i["time_ist"].endswith("+05:30") for i in doc["items"]))
        self.assertTrue(all(i["ticker"] == "GODREJAGRO.NS" for i in doc["items"]))

    def test_dry_run_and_no_tickers(self):
        run(["--tickers", "GODREJAGRO", "--dry-run"] + self.base)
        self.assertFalse(os.path.exists(self.state))
        code, _, err = run(self.base)
        self.assertEqual(code, 2)
        self.assertIn("no symbols", err)


if __name__ == "__main__":
    unittest.main()

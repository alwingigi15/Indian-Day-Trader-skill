import datetime
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, load

facts = load("trader-analyst", "facts")
DIR = os.path.join(FIX, "trader-analyst")
# Real data: timeseries-*.json are Yahoo fundamentals timeseries responses saved 2026-11-06 (plain GET),
# and the .html is NSE's public results page for Reliance Q3 FY26 (consolidated), saved from
# nsearchives.nseindia.com/corporate/ixbrl/ with a plain GET.
# SYNTHETIC: SAMPLE_INDAS_RELIANCE_Q3FY25.xml and SAMPLE_nse_results_RELIANCE.json. No raw Ind AS
# XBRL instance or results-list JSON is published at a plain URL (they sit behind nseindia.com's
# browser session), so these follow the published shapes with illustrative figures.
PAGE = os.path.join(DIR, "INTEGRATED_FILING_INDAS_135487_16012026193821_iXBRL_WEB.html")
XBRL = os.path.join(DIR, "SAMPLE_INDAS_RELIANCE_Q3FY25.xml")
LIST = os.path.join(DIR, "SAMPLE_nse_results_RELIANCE.json")


def timeseries(sym):
    with open(os.path.join(DIR, "timeseries-%s.json" % sym), encoding="utf-8") as fh:
        return json.load(fh)


def read(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = facts.main(args)
    return code, out.getvalue(), err.getvalue()


class IndianCalendar(unittest.TestCase):
    def test_april_march_fiscal_year(self):
        self.assertEqual(facts.fiscal_label("2026-06-30"), "Q1 FY27")
        self.assertEqual(facts.fiscal_label("2026-09-30"), "Q2 FY27")
        self.assertEqual(facts.fiscal_label("2026-12-31"), "Q3 FY27")
        self.assertEqual(facts.fiscal_label("2027-03-31"), "Q4 FY27")
        self.assertEqual(facts.fiscal_label("2025-12-31", fy_end_month=12), "Q4 FY25")

    def test_sebi_lodr_deadlines(self):
        self.assertEqual(facts.results_due("2026-09-30"), datetime.date(2026, 11, 14))   # 45 days
        self.assertEqual(facts.results_due("2026-12-31"), datetime.date(2027, 2, 14))
        self.assertEqual(facts.results_due("2027-03-31"), datetime.date(2027, 5, 30))    # Q4: 60 days

    def test_lakh_and_crore(self):
        self.assertEqual(facts.inr(1234567), "₹12.35 lakh")
        self.assertEqual(facts.inr(52300000000), "₹5,230.00 crore")
        self.assertEqual(facts.inr(950), "₹950.00")

    def test_symbols(self):
        self.assertEqual(facts.nse_symbol("reliance"), "RELIANCE.NS")
        self.assertEqual(facts.nse_symbol("TCS.BO"), "TCS.BO")
        self.assertEqual(facts.yahoo_symbol("M&M"), "M%26M.NS")

    def test_ist(self):
        self.assertEqual(facts.to_ist(datetime.datetime(2026, 11, 5, 20, 0)),
                         datetime.datetime(2026, 11, 6, 1, 30))


class Reliance(unittest.TestCase):
    def setUp(self):
        self.r = facts.analyse(facts.from_yahoo(timeseries("RELIANCE.NS")), quarters=8)

    def test_latest_quarter(self):
        q = self.r["rows"][0]
        self.assertEqual(q["end"], "2026-09-30")
        self.assertEqual(q["fiscal"], "Q2 FY27")
        self.assertEqual(q["revenue"], 3306010000000)
        self.assertEqual(q["net_income"], 255480000000)
        self.assertEqual(q["eps_diluted"], 18.88)
        self.assertEqual(q["revenue_yoy"], 29.8)    # vs the derived Q2 FY26
        self.assertEqual(q["gross_margin"], 27.1)
        self.assertEqual(q["operating_margin"], 12.5)
        self.assertEqual(q["net_margin"], 7.7)
        self.assertFalse(q["derived"])
        self.assertEqual(self.r["currency"], "INR")

    def test_yahoo_skipped_quarter_is_derived_from_the_year(self):
        # Yahoo has no 2025-09-30 quarter: FY26 total minus Q1, Q3 and Q4
        q = next(r for r in self.r["rows"] if r["end"] == "2025-09-30")
        self.assertTrue(q["derived"])
        self.assertEqual(q["fiscal"], "Q2 FY26")
        self.assertEqual(round(q["revenue"] / 1e7), 254623)
        self.assertEqual(round(q["net_income"] / 1e7), 18165)
        self.assertIsNone(q["gross_margin"])   # Yahoo's annual gross profit does not add up

    def test_trailing_twelve_months(self):
        self.assertEqual(round(self.r["ttm"]["revenue"] / 1e7), 1199033)
        self.assertEqual(round(self.r["ttm"]["net_income"] / 1e7), 82110)
        self.assertEqual(self.r["ttm"]["eps_diluted"], 60.68)

    def test_what_changed_in_words(self):
        lines = facts.changes(self.r)
        self.assertIn("Q2 FY27, quarter ending 2026-09-30", lines[0])
        self.assertIn("revenue ₹330,601.00 crore (+29.8% vs a year earlier)", lines[0])
        self.assertIn("diluted EPS ₹18.88 (+40.7%)", lines[0])
        self.assertTrue(any(l.startswith("Operating margin 12.5% (+2.0 points") for l in lines))

    def test_next_results_and_lagging_data(self):
        nxt, note = facts.schedule(self.r, datetime.date(2026, 11, 6))
        self.assertIn("Q3 FY27 (quarter ending 2026-12-31) due by 2027-02-14", nxt)
        self.assertIsNone(note)
        _, note = facts.schedule(self.r, datetime.date(2027, 2, 20))
        self.assertIn("up to the quarter ending 2026-12-31 (Q3 FY27) were due by 2027-02-14", note)


class Tcs(unittest.TestCase):
    def test_tcs(self):
        r = facts.analyse(facts.from_yahoo(timeseries("TCS.NS")), quarters=4)
        q = r["rows"][0]
        self.assertEqual((q["end"], q["fiscal"]), ("2026-09-30", "Q2 FY27"))
        self.assertEqual(q["eps_diluted"], 38.37)
        self.assertEqual(q["revenue_yoy"], 11.2)
        self.assertEqual(len(r["rows"]), 4)


class ResultsFiles(unittest.TestCase):
    def test_nse_results_page_reads_lakhs(self):
        recs = facts.parse_results_page(read(PAGE))
        q = next(r for r in recs if r["start"] == "2025-10-01")
        self.assertEqual(q["end"], "2025-12-31")
        self.assertEqual(q["nature"], "consolidated")
        self.assertEqual(q["symbol"], "RELIANCE")
        self.assertEqual(q["values"]["revenue"], 2694960000000)       # 2,69,49,600.00 lakh
        self.assertEqual(q["values"]["net_income"], 186450000000)     # owners of the parent
        self.assertEqual(q["values"]["eps_diluted"], 13.78)           # not scaled
        # operating profit = PBT before exceptional + finance costs - other income (Yahoo's figure)
        self.assertEqual(facts.operating_profit(q["values"]), 313960000000)
        ytd = next(r for r in recs if r["start"] == "2025-04-01")
        self.assertEqual(ytd["values"]["revenue"], 7770540000000)

    def test_page_without_rounding_is_refused(self):
        text = read(PAGE).replace(">Lakhs<", "><")
        with self.assertRaises(facts.FactsError) as cm:
            facts.parse_results_page(text)
        self.assertIn("refusing to guess", str(cm.exception))

    def test_xbrl_concepts_units_and_dimensions(self):
        with open(XBRL, "rb") as fh:
            recs = facts.parse_xbrl(fh.read())
        q = next(r for r in recs if r["start"] == "2024-10-01")
        self.assertEqual(q["values"]["revenue"], 2438650000000)       # the total, not the segment
        self.assertEqual(q["values"]["net_income"], 185400000000)     # owners of parent wins
        self.assertEqual(q["values"]["eps_diluted"], 13.70)
        self.assertEqual(q["nature"], "consolidated")
        self.assertIn("nearest ₹1 lakh", q["units"])

    def test_consolidated_is_preferred(self):
        with tempfile.TemporaryDirectory() as tmp:
            sa = os.path.join(tmp, "standalone.xml")
            with open(XBRL, encoding="utf-8") as fh:
                text = fh.read().replace(">Consolidated<", ">Standalone<").replace(
                    "2438650000000", "1250000000000")
            with open(sa, "w", encoding="utf-8") as fh:
                fh.write(text)
            data = facts.from_files([sa, XBRL], facts.Fetcher(offline=tmp))
        self.assertEqual(data["series"]["revenue"]["2024-12-31"]["value"], 2438650000000)
        self.assertEqual(data["basis"], "consolidated")

    def test_results_list_json_fetches_nsearchives_links_only(self):
        code, out, err = run(["RELIANCE", "--file", LIST, "--offline", DIR, "--today", "2026-02-01"])
        self.assertEqual(code, 0, err)
        self.assertIn("skipped https://example.com/not-an-nse-archive.xml", err)
        self.assertIn("RELIANCE INDUSTRIES LIMITED (RELIANCE.NS)", out)
        self.assertIn("consolidated", out)
        self.assertIn("Q3 FY26, quarter ending 2025-12-31: revenue ₹269,496.00 crore "
                      "(+10.5% vs a year earlier)", out)
        self.assertIn("Q4 FY26 (quarter ending 2026-03-31) due by 2026-05-30", out)
        self.assertNotIn("vs the quarter before", out)   # a year apart, not consecutive

    def test_two_files_page_and_xbrl(self):
        code, out, err = run(["--file", PAGE, "--file", XBRL, "--today", "2026-11-06", "--json"])
        self.assertEqual(code, 0, err)
        rows = json.loads(out)[0]["result"]["rows"]
        self.assertEqual([r["fiscal"] for r in rows], ["Q3 FY26", "Q3 FY25"])
        self.assertEqual(rows[0]["eps_yoy"], 0.6)
        self.assertIn("were due by", json.loads(out)[0]["note"])


class Cli(unittest.TestCase):
    def test_report_and_metrics_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "trader", "metrics.json")
            code, out, err = run(["RELIANCE", "TCS.NS", "--offline", DIR, "--metrics", path,
                                  "--today", "2026-11-06"])
            self.assertEqual(code, 0, err)
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        rows = {r["symbol"]: r for r in data["companies"]}
        self.assertEqual(sorted(rows), ["RELIANCE.NS", "TCS.NS"])
        self.assertEqual(rows["RELIANCE.NS"]["eps_ttm"], 60.68)
        self.assertEqual(rows["RELIANCE.NS"]["fiscal_quarter"], "Q2 FY27")
        self.assertEqual(rows["TCS.NS"]["period_end"], "2026-09-30")
        self.assertIn("₹ crore except EPS", out)
        self.assertIn("Yahoo Finance fundamentals timeseries", out)
        self.assertIn("wrote", out)
        for bad in ("$", "SEC", "10-Q", "CIK", chr(0x2014)):
            self.assertNotIn(bad, out)

    def test_unknown_symbol(self):
        code, _, err = run(["ZZZZ", "--offline", DIR])
        self.assertEqual(code, 2)
        self.assertIn("no NSE results found for ZZZZ.NS", err)
        code, _, err = run(["BAD SYMBOL!", "--offline", DIR])
        self.assertEqual(code, 2)

    def test_json_output(self):
        code, out, err = run(["RELIANCE.NS", "--offline", DIR, "--json", "--quarters", "4",
                              "--today", "2026-11-06"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(len(data[0]["result"]["rows"]), 4)
        self.assertIn("due by 2027-02-14", data[0]["next_results"])

    def test_no_sec_left(self):
        src = read(os.path.join(os.path.dirname(DIR), "..", "..", "trader-analyst", "facts.py"))
        for bad in ("data.sec.gov", "sec_user_agent", "CIK", "us-gaap", "8-K"):
            self.assertNotIn(bad, src)


if __name__ == "__main__":
    unittest.main()

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import report_watcher as W

# Coltron's SPP3 template: quarter in bold, link text is the status.
TEMPLATE = """
### Reports
* **Q4 2026** — [Pending/Posted Date](https://discuss.ens.domains/t/#)
* **Q1 2027** — [Pending](https://discuss.ens.domains/t/#)
* **Q2 2027** — [Pending](https://discuss.ens.domains/t/#)

For any questions, please contact us via [forum/email] at [handle/email].
"""

# The shape SPP2 providers actually use: the quarter IS the link text.
SPP2 = """
## Reports
* [Q4 2024](https://discuss.ens.domains/t/efp-service-provider/20102)
* [Q1 2025](https://discuss.ens.domains/t/efp-service-provider/20102/2?u=brantlymillegan)
* [Q3 2025](https://discuss.ens.domains/t/ethid-efp-spp/20102/4)
"""

MIXED = """
### Reports
* **Q3 2026** — [Posted 28 Oct 2026](https://discuss.ens.domains/t/namespace-spp3-reports/22999/3)
* **Q4 2026** — [Pending](https://discuss.ens.domains/t/#)
"""


class TestParsing(unittest.TestCase):
    def test_template_placeholders_are_not_filed(self):
        rows = W.parse_reports(TEMPLATE)
        self.assertEqual([r["quarter"] for r in rows],
                         ["2026Q4", "2027Q1", "2027Q2"])
        self.assertTrue(all(not r["filed"] for r in rows))

    def test_spp2_shape_where_the_quarter_is_the_link(self):
        rows = W.parse_reports(SPP2)
        self.assertEqual([r["quarter"] for r in rows],
                         ["2024Q4", "2025Q1", "2025Q3"])
        self.assertTrue(all(r["filed"] for r in rows))

    def test_a_posted_report_is_detected_among_pending_ones(self):
        rows = W.parse_reports(MIXED)
        by_q = {r["quarter"]: r for r in rows}
        self.assertTrue(by_q["2026Q3"]["filed"])
        self.assertIn("/22999/3", by_q["2026Q3"]["url"])
        self.assertFalse(by_q["2026Q4"]["filed"])

    def test_contact_line_links_are_not_mistaken_for_reports(self):
        # "[forum/email] at [handle/email]" sits right below the list.
        self.assertEqual(len(W.parse_reports(TEMPLATE)), 3)

    def test_no_reports_section_yields_nothing(self):
        self.assertEqual(W.parse_reports("# Hello\n\nNo reports here."), [])

    def test_quarter_normalisation(self):
        self.assertEqual(W.normalise_quarter("Q4 2026"), "2026Q4")
        self.assertEqual(W.normalise_quarter("q1 2027"), "2027Q1")
        self.assertIsNone(W.normalise_quarter("Q1/Q2 2024"))
        self.assertIsNone(W.normalise_quarter("not a quarter"))


class TestFiledDetection(unittest.TestCase):
    def test_placeholder_urls_are_never_filed(self):
        for u in ("https://discuss.ens.domains/t/#", "#", "", None):
            self.assertFalse(W.is_filed(u), u)

    def test_real_topic_and_post_urls_are_filed(self):
        for u in ("https://discuss.ens.domains/t/slug/22999",
                  "https://discuss.ens.domains/t/slug/22999/3",
                  "https://discuss.ens.domains/t/22999/3?u=x"):
            self.assertTrue(W.is_filed(u), u)


# The structure of EthID's real thread, which broke the first parser: a
# "Previous Reports" heading with prose sits BEFORE the actual "Reports" list.
REAL_SHAPE = """
## Reason
Transparency matters.

## Previous Reports
Previously, I made a new post for each report. Going forward I will simply
add new reports as replies in this thread.

## Reports
* [Q1/Q2 2024](https://discuss.ens.domains/t/efp-report-q1-q2-2024/19410)
* [Q3 2024](https://discuss.ens.domains/t/efp-report-q3-2024/19665)
* [Q1 2025](https://discuss.ens.domains/t/efp-reports/20102/2?u=brantlymillegan)

# Q4 2024
## Income
"""


class TestRealThreadShape(unittest.TestCase):
    def test_a_previous_reports_heading_does_not_swallow_the_real_list(self):
        rows = W.parse_reports(REAL_SHAPE)
        self.assertEqual([r["quarter"] for r in rows], ["2024Q3", "2025Q1"])

    def test_combined_quarters_are_rejected_not_guessed(self):
        # "Q1/Q2 2024" covers two quarters and cannot be credited to one.
        self.assertNotIn("2024Q1", [r["quarter"] for r in W.parse_reports(REAL_SHAPE)])

    def test_body_headings_after_the_list_do_not_contribute(self):
        rows = W.parse_reports(REAL_SHAPE)
        self.assertTrue(all(r["quarter"].startswith(("2024", "2025")) for r in rows))

    def test_duplicate_quarters_keep_the_first(self):
        md = ("## Reports\n"
              "* [Q3 2026](https://discuss.ens.domains/t/a/1)\n"
              "* [Q3 2026](https://discuss.ens.domains/t/b/2)\n")
        rows = W.parse_reports(md)
        self.assertEqual(len(rows), 1)
        self.assertIn("/a/1", rows[0]["url"])


class TestDueState(unittest.TestCase):
    PROVIDERS = {"providers": [
        {"slug": "namespace", "name": "Namespace", "cohort": "spp3",
         "award_usd": 500000, "approved_wallet": "0x" + "1" * 40,
         "categories": [], "recusals": []}]}
    COMMITS = {"quarters": [{"quarter": "2026Q3", "ends": "2026-09-30",
                             "report_due": "2026-10-30"}],
               "providers": {"namespace": {"report_thread": "https://x/t/1"}}}

    def _state(self, now_date, markdown):
        import calendar, time as _t
        now = calendar.timegm(_t.strptime(now_date, "%Y-%m-%d"))
        return W.build_state(self.PROVIDERS, self.COMMITS, now,
                             fetcher=lambda ref: markdown)

    def test_upcoming_well_before_the_due_date(self):
        st = self._state("2026-09-01", "## Reports\n* **Q3 2026** — [Pending](https://x/t/#)\n")
        self.assertEqual(st[0]["quarters"][0]["state"], "upcoming")

    def test_due_soon_inside_the_nudge_window(self):
        st = self._state("2026-10-25", "## Reports\n* **Q3 2026** — [Pending](https://x/t/#)\n")
        self.assertEqual(st[0]["quarters"][0]["state"], "due soon")
        self.assertEqual(W.nudges(st)[0]["code"], "report_due_soon")

    def test_overdue_after_the_due_date(self):
        st = self._state("2026-11-05", "## Reports\n* **Q3 2026** — [Pending](https://x/t/#)\n")
        self.assertEqual(st[0]["quarters"][0]["state"], "overdue")
        n = W.nudges(st)[0]
        self.assertEqual(n["code"], "report_overdue")
        self.assertEqual(n["severity"], "critical")

    def test_a_filed_report_is_never_overdue(self):
        st = self._state("2026-11-05",
                         "## Reports\n* **Q3 2026** — [Posted](https://x/t/slug/22/3)\n")
        self.assertEqual(st[0]["quarters"][0]["state"], "filed")
        self.assertEqual(W.nudges(st), [])

    def test_unreachable_thread_is_surfaced_not_silent(self):
        st = W.build_state(self.PROVIDERS, self.COMMITS, 0, fetcher=lambda r: None)
        self.assertEqual(W.nudges(st)[0]["code"], "thread_unreachable")

    def test_provider_without_a_thread_is_skipped_not_flagged(self):
        commits = {"quarters": self.COMMITS["quarters"],
                   "providers": {"namespace": {"report_thread": ""}}}
        st = W.build_state(self.PROVIDERS, commits, 0, fetcher=lambda r: None)
        self.assertIsNone(st[0]["thread_reachable"])
        self.assertNotIn("thread_unreachable", [n["code"] for n in W.nudges(st)])


class TestAlertSuppression(unittest.TestCase):
    N = [{"severity": "critical", "code": "report_overdue",
          "subject": "Namespace", "detail": "2026Q3 report was due"}]

    def test_first_time_speaks(self):
        self.assertTrue(W.alert_decision(self.N, [])["send"])

    def test_unchanged_picture_stays_quiet(self):
        keys = W.alert_decision(self.N, [])["keys"]
        self.assertFalse(W.alert_decision(self.N, keys)["send"])

    def test_resolution_sends_once(self):
        keys = W.alert_decision(self.N, [])["keys"]
        d = W.alert_decision([], keys)
        self.assertTrue(d["send"])
        self.assertEqual(d["kind"], "recovery")


if __name__ == "__main__":
    unittest.main()

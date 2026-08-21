import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "site"))
sys.path.insert(0, str(ROOT / "scripts"))
import render as R
import stream_monitor as M


def ctx(now=None):
    status = json.loads((ROOT / "data" / "streams" / "status.json").read_text())
    providers = json.loads((ROOT / "data" / "providers.json").read_text())
    status["findings"] = M.findings(status)
    board_path = ROOT / "data" / "notion" / "board.json"
    return {
        "status": status,
        "providers": providers,
        "board": json.loads(board_path.read_text()) if board_path.exists() else {},
        "calendar": json.loads((ROOT / "data" / "calendar.json").read_text()),
        "commitments": json.loads((ROOT / "data" / "commitments.json").read_text()),
        "now": now or (providers["spp3_stream_start"] + 86400),
    }


ALL_PAGES = ["/", "/providers", "/streams", "/reports", "/calendar",
             "/provider/namespace", "/provider/goldsky",
             "/provider/unruggable", "/provider/fluidkey"]


class TestRouting(unittest.TestCase):
    def test_every_page_renders(self):
        c = ctx()
        for path in ALL_PAGES:
            html = R.render(c, path)
            self.assertIsNotNone(html, path)
            self.assertTrue(html.startswith("<!doctype html>"), path)
            self.assertIn("</html>", html)

    def test_unknown_paths_return_none_so_server_can_404(self):
        c = ctx()
        for path in ("/nope", "/provider/ethid", "/provider/", "/rfp", "/board.json"):
            self.assertIsNone(R.render(c, path), path)

    def test_nav_marks_current_page(self):
        html = R.render(ctx(), "/streams")
        self.assertIn('class="nav__link is-active" href="/streams"', html)

    def test_provider_page_navigates_under_providers(self):
        html = R.render(ctx(), "/provider/namespace")
        self.assertIn('class="nav__link is-active" href="/providers"', html)


class TestScope(unittest.TestCase):
    """This is a cohort accountability tracker, not a program dashboard."""

    def test_rfp_is_not_a_page(self):
        self.assertIsNone(R.render(ctx(), "/rfp"))

    def test_no_rfp_process_markers_appear_anywhere(self):
        # Guards against the RFP section returning. Asserts on the process
        # vocabulary the RFP surfaces used, deliberately not on applicant
        # names: this repo is public and the applicant list is not.
        c = ctx()
        for path in ALL_PAGES:
            html = R.render(c, path)
            for marker in ("Gate Review", "Proposed Pass", "Proposed Fail",
                           "decision clock", "gate confirmed", "(ungated)"):
                self.assertNotIn(marker, html,
                                 "%s leaked into %s" % (marker, path))

    def test_calendar_excludes_rfp_milestones(self):
        html = R.render(ctx(), "/calendar")
        self.assertNotIn("RFP evaluation complete", html)
        self.assertIn("Quarterly Reports", html)


class TestCohortAuthority(unittest.TestCase):
    def test_declined_provider_never_shown_as_funded(self):
        # EthID declined 2026-07-03; the Pipeline DB still says "Cohort selected".
        html = R.render(ctx(), "/providers")
        rows = re.findall(r'app__name"><a[^>]*>([^<]+)</a>', html)
        self.assertEqual(sorted(rows),
                         ["Fluidkey", "Goldsky", "Namespace", "Unruggable"])

    def test_drift_is_surfaced_when_the_board_disagrees_with_the_chain(self):
        # Written against a synthetic ghost, not against EthID. The original
        # version asserted "Board drift" was present and broke the moment the
        # Pipeline DB was corrected -- it had encoded a transient data fault as
        # a permanent expectation. What must hold forever is the mechanism.
        c = ctx()
        c["board"]["pipeline"] = list(c["board"].get("pipeline", [])) + [
            {"name": "Ghost Labs", "status": "Cohort selected", "awarded_usd": 1}]
        html = R.render(c, "/providers")
        self.assertIn("Board drift", html)
        self.assertIn("Ghost Labs", html)

    def test_no_drift_notice_when_board_and_chain_agree(self):
        html = R.render(ctx(), "/providers")
        self.assertNotIn("Board drift", html)

    def test_no_provider_page_for_a_declined_provider(self):
        self.assertIsNone(R.render(ctx(), "/provider/ethid"))


class TestTickerBasis(unittest.TestCase):
    def test_no_ticker_counts_from_before_the_epoch(self):
        c = ctx()
        epoch = c["providers"]["spp3_stream_start"]
        for path in ALL_PAGES:
            for since in re.findall(r'data-since="(\d+)"', R.render(c, path)):
                self.assertGreaterEqual(int(since), epoch, path)

    def test_true_flow_start_still_shown_on_streams(self):
        html = R.render(ctx(), "/streams")
        self.assertIn("flowing since 12 Sep 2025", html)


class TestProviderPage(unittest.TestCase):
    def test_shows_ratified_scope_and_rationale(self):
        html = R.render(ctx(), "/provider/goldsky")
        self.assertIn("ENS indexing as a dedicated public service", html)
        self.assertIn("standalone mandate", html)

    def test_empty_commitments_are_explained_not_blank(self):
        # Tests the empty branch explicitly rather than relying on the live
        # data being empty; the previous version broke the moment provisional
        # milestones were extracted.
        c = ctx()
        c["commitments"]["providers"]["fluidkey"]["milestones"] = []
        html = R.render(c, "/provider/fluidkey")
        self.assertIn("No commitments recorded yet", html)
        self.assertIn("Award Notice Item 5", html)

    def test_provisional_milestones_are_never_presented_as_confirmed(self):
        # 60 milestones now render from the applications. Every one of them is
        # what a provider PROPOSED, not the negotiated Award Notice Item 5 set.
        # If that distinction ever silently drops, the page starts asserting
        # commitments the DAO never agreed to.
        for slug in ("namespace", "goldsky", "unruggable", "fluidkey"):
            html = R.render(ctx(), "/provider/" + slug)
            self.assertIn("Provisional, not confirmed", html, slug)
            self.assertIn("Award Notice Item 5", html, slug)
            self.assertIn("none counts toward the 80% completion metric", html, slug)

    def test_every_provisional_milestone_carries_a_source_quote(self):
        # The extraction is AI-assisted, so an unquoted milestone is an
        # unverifiable claim about a real company.
        c = ctx()
        for slug, prov in c["commitments"]["providers"].items():
            for m in prov.get("milestones", []):
                self.assertTrue(m.get("quote"), "%s: %s" % (slug, m.get("title")))
                self.assertEqual(m.get("source"), "application")
                self.assertFalse(m.get("confirmed"))

    def test_quarters_fall_inside_the_term(self):
        valid = {"2026Q3", "2026Q4", "2027Q1", "2027Q2", "2027Q3", None}
        c = ctx()
        for prov in c["commitments"]["providers"].values():
            for m in prov.get("milestones", []):
                self.assertIn(m.get("target_quarter"), valid, m.get("title"))

    def test_external_dependency_is_flagged(self):
        # Namespace milestones depend on ENSv2 shipping; a slip is not a
        # delivery failure and the page must say so.
        html = R.render(ctx(), "/provider/namespace")
        self.assertIn("External dependency", html)
        self.assertIn("ENSv2", html)

    def test_recusal_is_disclosed(self):
        html = R.render(ctx(), "/provider/namespace")
        self.assertIn("sovereignsignal.eth", html)
        self.assertIn("another member signs off", html)

    def test_reports_section_says_not_overdue(self):
        html = R.render(ctx(), "/provider/unruggable")
        self.assertIn("No reports filed", html)
        self.assertIn("Not overdue", html)


class TestReportsPage(unittest.TestCase):
    def test_lists_every_quarter_with_due_dates(self):
        html = R.render(ctx(), "/reports")
        for q in ("2026Q3", "2026Q4", "2027Q1", "2027Q2"):
            self.assertIn(q, html)
        self.assertIn("2026-10-30", html)

    def test_states_the_public_forum_post_is_contractual(self):
        html = R.render(ctx(), "/reports")
        self.assertIn("clause 6.3", html)

    def test_nothing_overdue_before_the_first_window(self):
        html = R.render(ctx(), "/reports")
        self.assertNotIn("OVERDUE", html)

    def test_overdue_is_flagged_once_the_window_passes(self):
        html = R.render(ctx(now=1794500000), "/reports")   # 2026-11-12, past the window
        self.assertIn("OVERDUE", html)


class TestPrivacy(unittest.TestCase):
    def test_no_private_block_reaches_any_page(self):
        c = ctx()
        c["providers"]["providers"][0]["private"] = {"x": "COMMITTEE-ONLY"}
        for path in ALL_PAGES:
            self.assertNotIn("COMMITTEE-ONLY", R.render(c, path), path)

    def test_no_contact_details(self):
        c = ctx()
        for path in ALL_PAGES:
            html = R.render(c, path).lower()
            for token in ("@gmail", "@proton", "t.me/", "mailto:"):
                self.assertNotIn(token, html, path)


class TestDegradation(unittest.TestCase):
    def test_pages_render_without_optional_feeds(self):
        c = ctx()
        c["board"] = {}
        c["commitments"] = {}
        c["calendar"] = {}
        for path in ("/", "/providers", "/streams", "/reports", "/calendar"):
            html = R.render(c, path)
            self.assertIsNotNone(html, path)
            self.assertIn("</html>", html)


class TestFlowDiagram(unittest.TestCase):
    def test_streams_page_draws_the_pod_topology(self):
        html = R.render(ctx(), "/streams")
        self.assertIn('class="flow"', html)
        self.assertIn("Stream Pod", html)
        self.assertIn("Timelock", html)

    def test_one_edge_per_funded_provider_plus_the_master(self):
        html = R.render(ctx(), "/streams")
        svg = html.split('class="flow"')[1].split("</svg>")[0]
        self.assertEqual(svg.count('class="fl"') + svg.count('class="fl fl--master"'), 5)

    def test_edge_thickness_tracks_rate(self):
        # Namespace at $500k must be drawn thicker than Fluidkey at $340k.
        html = R.render(ctx(), "/streams")
        svg = html.split('class="flow"')[1].split("</svg>")[0]
        widths = dict(zip(
            re.findall(r'stroke="(#[0-9A-Fa-f]{6})"', svg),
            [float(w) for w in re.findall(r'stroke-width="([\d.]+)"', svg)[1:]]))
        self.assertGreater(widths[R.accent("namespace")], widths[R.accent("fluidkey")])

    def test_a_stalled_stream_loses_its_motion(self):
        c = ctx()
        for s_ in c["status"]["streams"]:
            if s_["slug"] == "fluidkey":
                s_["ok"] = False
        html = R.render(c, "/streams")
        self.assertIn("fl--stalled", html)

    def test_each_provider_has_a_distinct_accent(self):
        slugs = ["namespace", "goldsky", "unruggable", "fluidkey"]
        self.assertEqual(len({R.accent(s) for s in slugs}), 4)

    def test_accents_do_not_collide_with_semantic_colours(self):
        semantic = {"#0E8A5F", "#B87503", "#C2331B"}
        self.assertFalse(semantic & {R.accent(s) for s in R.ACCENT})

    def test_diagram_is_labelled_for_screen_readers(self):
        html = R.render(ctx(), "/streams")
        self.assertIn('role="img"', html)
        self.assertIn("aria-label=", html)


class TestProviderIdentity(unittest.TestCase):
    def test_provider_page_wears_its_own_accent(self):
        for slug in ("namespace", "goldsky", "unruggable", "fluidkey"):
            html = R.render(ctx(), "/provider/" + slug)
            self.assertIn("--accent:%s" % R.accent(slug), html, slug)

    def test_shared_pages_use_the_default_accent(self):
        for path in ("/", "/streams", "/reports", "/calendar", "/providers"):
            self.assertNotIn("<main class=\"wrap\" style=", R.render(ctx(), path), path)

    def test_master_trunk_is_heavier_than_any_branch(self):
        # The master stream carries $3.2M, more than any single provider, and
        # must not read as the thinnest edge in the diagram.
        html = R.render(ctx(), "/streams")
        svg = html.split('class="flow"')[1].split("</svg>")[0]
        widths = [float(w) for w in re.findall(r'stroke-width="([\d.]+)"', svg)]
        self.assertEqual(widths[0], max(widths))


class TestStaleness(unittest.TestCase):
    """A failed deploy leaves the last good page serving. It must not keep
    asserting a live verdict from dead data.

    Found in production 2026-08-17: a Railway build failed, the site kept
    serving the previous day's build, and the only tell was "checked 1444 min
    ago" in small muted text beside the block number.

    Assertions target the rendered <header> class, never a bare "v-stale"
    substring: that string also appears in the stylesheet, so a substring
    check passes whether or not the bar is actually stale.
    """

    HEADER = re.compile(r'<header class="verdict (v-[a-z]+)"')

    def _bar(self, hours):
        c = ctx()
        c["now"] = R._parse_iso(c["status"]["checked_at"]) + hours * 3600
        html = R.render(c, "/")
        m = self.HEADER.search(html)
        self.assertIsNotNone(m, "no verdict header rendered")
        return m.group(1), html

    def test_fresh_data_shows_the_stream_verdict(self):
        cls, html = self._bar(2)
        self.assertEqual(cls, "v-healthy")
        self.assertIn("All streams flowing", html)

    def test_just_inside_the_window_is_still_fresh(self):
        cls, _ = self._bar(R.STALE_HOURS - 1)
        self.assertEqual(cls, "v-healthy")

    def test_stale_data_stops_claiming_a_live_verdict(self):
        cls, html = self._bar(R.STALE_HOURS + 1)
        self.assertEqual(cls, "v-stale")
        self.assertIn("may not reflect the chain", html)
        self.assertIn("last verdict:", html)

    def test_the_incident_age_reads_in_hours_not_minutes(self):
        # 1444 minutes rendered as "1444 min ago", which nobody parses as a day.
        _, html = self._bar(24)
        self.assertIn("24 hours ago", html)
        self.assertNotIn("min ago", html)

    def test_multi_day_staleness_reads_in_days(self):
        _, html = self._bar(72)
        self.assertIn("days ago", html)

    def test_staleness_applies_on_every_page(self):
        c = ctx()
        c["now"] = R._parse_iso(c["status"]["checked_at"]) + (R.STALE_HOURS + 5) * 3600
        for path in ("/", "/providers", "/streams", "/reports", "/calendar",
                     "/provider/namespace"):
            m = self.HEADER.search(R.render(c, path))
            self.assertEqual(m.group(1), "v-stale", path)


class TestOverviewIsStatic(unittest.TestCase):
    """Sov 2026-08-19: the overview is a plain program intro. Live data lives
    on the pages that own it."""

    def test_no_live_tickers_on_the_overview(self):
        html = R.render(ctx(), "/")
        self.assertNotIn("data-rate=", html)

    def test_no_flow_diagram_on_the_overview(self):
        html = R.render(ctx(), "/")
        self.assertNotIn('<svg class="flow"', html)

    def test_overview_introduces_the_program(self):
        html = R.render(ctx(), "/")
        self.assertIn("EP&nbsp;6.42", html)
        self.assertIn("EP&nbsp;6.49", html)
        self.assertIn("Committee", html)
        for name in ("Namespace", "Goldsky", "Unruggable", "Fluidkey"):
            self.assertIn(name, html)

    def test_overview_links_every_section(self):
        html = R.render(ctx(), "/")
        for href in ("/streams", "/providers", "/reports", "/calendar"):
            self.assertIn('href="%s"' % href, html)


class TestBoardExportScope(unittest.TestCase):
    def test_only_cohort_rows_ever_reach_the_public_board(self):
        # board.json is committed and rendered. The pipeline DB holds all 26
        # applications, and the 21 rejected applicants' scores and asks were
        # never published by EP 6.49. Caught 2026-08-21, pre-review.
        import json
        board = json.loads((ROOT / "data" / "notion" / "board.json").read_text())
        rows = board.get("pipeline", [])
        self.assertTrue(rows)
        for r in rows:
            self.assertEqual(r.get("status"), "Cohort selected", r.get("name"))

    def test_process_flags_do_not_leave_notion(self):
        # Award Notice / Program Terms checkboxes and requested amounts are
        # committee process. Serving them (or committing them once the repo
        # is public) published unsigned-notice state that EP 6.49 did not.
        import json
        board = json.loads((ROOT / "data" / "notion" / "board.json").read_text())
        for r in board.get("pipeline", []):
            for key in ("notice_agreed", "terms_agreed", "requested_usd",
                        "final_score"):
                self.assertNotIn(key, r, r.get("name"))


class TestPublicShare(unittest.TestCase):
    """The tracker is a public record. Copy and sources must survive a
    delegate reading it as if they had never seen the committee workspace."""

    def test_hero_does_not_claim_nothing_is_self_reported(self):
        # Commitments are extracted from the providers' own applications.
        html = R.render(ctx(), "/")
        self.assertNotIn("self-reported", html)

    def test_hero_states_what_each_figure_is(self):
        html = R.render(ctx(), "/")
        self.assertIn("Stream rates are read from Ethereum", html)
        self.assertIn("Award Notice Item 5", html)

    def test_labs_seat_is_labelled_non_compensated(self):
        html = R.render(ctx(), "/")
        self.assertIn("non-compensated", html)
        streams = R.render(ctx(), "/streams")
        self.assertIn("no fifth committee stream", streams)
        self.assertNotIn("gregskril.eth</span>", streams)

    def test_categories_use_ep_names_not_bare_numbers(self):
        html = R.render(ctx(), "/provider/namespace")
        self.assertIn("1: Infrastructure", html)
        self.assertIn("2: Outreach &amp; Integrations", html)
        self.assertNotIn("<dd>1, 2</dd>", html)

    def test_unruggable_spp2_carryover_is_explained(self):
        html = R.render(ctx(), "/provider/unruggable")
        self.assertIn("uninterrupted from SPP2", html)

    def test_milestone_sources_are_the_ep_6_49_ipfs_applications(self):
        import ensdao_spp as E
        c = ctx()
        for slug, prov in c["commitments"]["providers"].items():
            url = prov.get("milestones_source_url", "")
            self.assertEqual(url, E.PROPOSAL_URLS[slug], slug)
            self.assertIn("/ipfs/", url)

    def test_calendar_term_end_matches_the_overview(self):
        html = R.render(ctx(), "/calendar")
        self.assertIn("2027-07-31", html)
        self.assertNotIn("2027-08-01", html)
        home = R.render(ctx(), "/")
        self.assertIn("31 Jul 2027", home)

    def test_opengraph_and_favicon_are_present(self):
        html = R.render(ctx(), "/")
        self.assertIn('property="og:title"', html)
        self.assertIn('rel="icon" href="/favicon.svg"', html)

    def test_fluidkey_post_term_milestones_are_labelled(self):
        html = R.render(ctx(), "/provider/fluidkey")
        self.assertIn("after the 12-month term", html)


if __name__ == "__main__":
    unittest.main()

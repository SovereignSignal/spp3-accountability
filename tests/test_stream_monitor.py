import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import acct_config as C
import stream_monitor as M


class FakeReader:
    """Serves canned flowrates keyed by (sender, receiver), lowercased."""

    def __init__(self, rates, account_net=None, balances=None):
        self.rates = {(s.lower(), r.lower()): v for (s, r), v in rates.items()}
        self.account_net = account_net or {}
        self.balances = balances or {}

    def flowrate(self, token, sender, receiver):
        return self.rates.get((sender.lower(), receiver.lower()), 0)

    def flow_info(self, token, sender, receiver):
        return {"last_updated": 1785561311,
                "flowrate": self.flowrate(token, sender, receiver),
                "deposit": 0}

    def account_flowrate(self, token, account):
        return self.account_net.get(account.lower(), 0)

    def balance_of(self, token, account):
        return self.balances.get((token.lower(), account.lower()), 0)


def load_real():
    return json.loads((ROOT / "data" / "providers.json").read_text())


def healthy_rates(doc):
    rates = {}
    for p in doc["providers"]:
        rates[(doc["pod"], p["approved_wallet"])] = C.expected_rate(p["award_usd"])
    for r in doc["retired"]:
        rates[(doc["pod"], r["approved_wallet"])] = 0
    return rates


class TestCheckStreams(unittest.TestCase):
    def test_all_healthy(self):
        doc = load_real()
        results = M.check_streams(doc, FakeReader(healthy_rates(doc)))
        self.assertEqual(len(results), 10)
        self.assertTrue(all(r["ok"] for r in results))
        self.assertTrue(all(r["state"] == "ok" for r in results))

    def test_namespace_expected_rate_matches_chain_reality(self):
        doc = load_real()
        results = M.check_streams(doc, FakeReader(healthy_rates(doc)))
        ns = next(r for r in results if r["slug"] == "namespace")
        self.assertEqual(ns["expected_wei_s"], 15854895991882293)

    def test_rate_mismatch_detected(self):
        doc = load_real()
        rates = healthy_rates(doc)
        rates[(doc["pod"], "0x168CAfEcFBE97dF85968Ea039CC11D10a9A44567")] = 1
        results = M.check_streams(doc, FakeReader(rates))
        ns = next(r for r in results if r["slug"] == "namespace")
        self.assertFalse(ns["ok"])
        self.assertEqual(ns["state"], "rate_mismatch")

    def test_liquidated_stream_reported_as_stopped_not_mismatch(self):
        doc = load_real()
        rates = healthy_rates(doc)
        rates[(doc["pod"], "0xdcC34c0da55cEF7AeD38Bb749AD97DAC12A9936C")] = 0
        results = M.check_streams(doc, FakeReader(rates))
        fk = next(r for r in results if r["slug"] == "fluidkey")
        self.assertEqual(fk["state"], "stopped")
        self.assertFalse(fk["ok"])

    def test_wei_comparison_is_exact_where_dollar_round_trip_is_lossy(self):
        # The regression guard for the whole design. Assertions use exact
        # integer arithmetic on purpose: the per-provider truncation is
        # sub-dollar and disappears into float rounding, which is precisely
        # why dollars are the wrong unit to compare in.
        doc = load_real()
        results = M.check_streams(doc, FakeReader(healthy_rates(doc)))
        ns = next(r for r in results if r["slug"] == "namespace")
        self.assertTrue(ns["ok"], "a healthy stream must never be flagged")
        self.assertEqual(ns["expected_wei_s"], ns["actual_wei_s"])
        # Re-annualising the truncated rate does not recover the nominal award.
        self.assertNotEqual(ns["expected_wei_s"] * C.SECONDS_PER_YEAR,
                            500000 * 10**18)

    def test_master_stream_nominal_dollars_do_not_round_trip(self):
        # The vivid case: the master stream is documented as $3.21M/yr and is
        # on-chain as 101720934415475068 wei/s, which annualises to $3,207,871.
        # A dollar comparison would report a $2,129/yr discrepancy every day
        # on a stream that is exactly correct.
        doc = load_real()
        self.assertEqual(doc["master_stream_wei_s"], 101720934415475068)
        self.assertNotEqual(
            doc["master_stream_wei_s"] * C.SECONDS_PER_YEAR, 3210000 * 10**18)


class TestCheckRetired(unittest.TestCase):
    def test_retired_streams_stopped_is_ok(self):
        doc = load_real()
        results = M.check_retired(doc, FakeReader(healthy_rates(doc)))
        self.assertEqual(len(results), 4)
        self.assertTrue(all(r["ok"] for r in results))

    def test_retired_stream_still_running_is_flagged(self):
        doc = load_real()
        rates = healthy_rates(doc)
        rates[(doc["pod"], "0x4dC96AAd2Daa3f84066F3A00EC41Fd1e88c8865A")] = 12345
        results = M.check_retired(doc, FakeReader(rates))
        nh = next(r for r in results if r["slug"] == "namehash")
        self.assertFalse(nh["ok"])
        self.assertEqual(nh["state"], "unexpected_active")


class TestNetFlowReconciliation(unittest.TestCase):
    def _healthy(self, doc):
        rates = healthy_rates(doc)
        known_out = sum(C.expected_rate(p["award_usd"]) for p in doc["providers"])
        net = doc["master_stream_wei_s"] - known_out
        return FakeReader(rates, account_net={doc["pod"].lower(): net})

    def test_no_unknown_streams(self):
        doc = load_real()
        r = M.reconcile_net_flow(doc, self._healthy(doc))
        self.assertEqual(r["unaccounted_wei_s"], 0)
        self.assertTrue(r["ok"])

    def test_expected_net_matches_measured_reality(self):
        # Measured on-chain 2026-08-04: pod net flow is -67497852409250.
        doc = load_real()
        r = M.reconcile_net_flow(doc, self._healthy(doc))
        self.assertEqual(r["expected_net_wei_s"], -67497852409250)

    def test_unknown_stream_detected(self):
        doc = load_real()
        rates = healthy_rates(doc)
        known_out = sum(C.expected_rate(p["award_usd"]) for p in doc["providers"])
        # A rogue receiver draining 1e12 wei/s that we do not know about.
        net = doc["master_stream_wei_s"] - known_out - 10**12
        reader = FakeReader(rates, account_net={doc["pod"].lower(): net})
        r = M.reconcile_net_flow(doc, reader)
        self.assertEqual(r["unaccounted_wei_s"], -10**12)
        self.assertFalse(r["ok"])


class TestRunway(unittest.TestCase):
    def _reader(self, doc, pod_usdcx, tl_usdcx, tl_usdc):
        return FakeReader(healthy_rates(doc), balances={
            (C.USDCX.lower(), doc["pod"].lower()): pod_usdcx,
            (C.USDCX.lower(), C.TIMELOCK.lower()): tl_usdcx,
            (C.USDC.lower(), C.TIMELOCK.lower()): tl_usdc,
        })

    def test_healthy_runway_matches_measured_reality(self):
        # Measured 2026-08-04: 690313.35 USDCx + 7550477.22 USDC at the
        # timelock, master draw 8788.69 USDCx/day => ~937.7 days.
        doc = load_real()
        reader = self._reader(doc, 209438 * 10**18,
                              690313 * 10**18, 7550477 * 10**6)
        r = M.check_runway(doc, reader)
        self.assertAlmostEqual(r["combined_days"], 937.7, delta=1.0)
        self.assertEqual(r["level"], "ok")
        self.assertTrue(r["ok"])

    def test_warning_threshold(self):
        doc = load_real()
        # 30 days of runway: below the 60-day warning, above 21-day critical.
        daily = doc["master_stream_wei_s"] * 86400
        reader = self._reader(doc, 0, 30 * daily, 0)
        r = M.check_runway(doc, reader)
        self.assertEqual(r["level"], "warning")
        self.assertFalse(r["ok"])

    def test_critical_threshold(self):
        doc = load_real()
        daily = doc["master_stream_wei_s"] * 86400
        reader = self._reader(doc, 0, 10 * daily, 0)
        r = M.check_runway(doc, reader)
        self.assertEqual(r["level"], "critical")
        self.assertFalse(r["ok"])

    def test_usdc_counts_toward_runway_with_decimal_scaling(self):
        # USDC is 6dp, USDCx is 18dp. 1 USDC must count as 1e12 wei of USDCx.
        doc = load_real()
        daily = doc["master_stream_wei_s"] * 86400
        usdc_units = (100 * daily) // 10**12
        reader = self._reader(doc, 0, 0, usdc_units)
        r = M.check_runway(doc, reader)
        self.assertAlmostEqual(r["combined_days"], 100.0, delta=0.5)


class TestBuildStatus(unittest.TestCase):
    def _healthy_reader(self, doc):
        known_out = sum(C.expected_rate(p["award_usd"]) for p in doc["providers"])
        return FakeReader(
            healthy_rates(doc),
            account_net={doc["pod"].lower(): doc["master_stream_wei_s"] - known_out},
            balances={
                (C.USDCX.lower(), doc["pod"].lower()): 209438 * 10**18,
                (C.USDCX.lower(), C.TIMELOCK.lower()): 690313 * 10**18,
                (C.USDC.lower(), C.TIMELOCK.lower()): 7550477 * 10**6,
            })

    def test_healthy_status_document(self):
        doc = load_real()
        s = M.build_status(doc, self._healthy_reader(doc),
                           25685582, "2026-08-05T21:00:00Z")
        self.assertEqual(s["overall"], "healthy")
        self.assertEqual(s["block_number"], 25685582)
        self.assertTrue(s["_generated"])
        self.assertEqual(len(s["streams"]), 10)
        self.assertEqual(len(s["retired"]), 4)
        self.assertEqual(M.findings(s), [])

    def test_rate_mismatch_makes_status_critical(self):
        doc = load_real()
        rates = healthy_rates(doc)
        rates[(doc["pod"], "0x168CAfEcFBE97dF85968Ea039CC11D10a9A44567")] = 1
        known_out = sum(C.expected_rate(p["award_usd"]) for p in doc["providers"])
        reader = FakeReader(
            rates,
            account_net={doc["pod"].lower(): doc["master_stream_wei_s"] - known_out},
            balances={(C.USDCX.lower(), C.TIMELOCK.lower()): 10**30})
        s = M.build_status(doc, reader, 1, "2026-08-05T21:00:00Z")
        self.assertEqual(s["overall"], "critical")
        f = M.findings(s)
        self.assertTrue(any(x["code"] == "rate_mismatch"
                            and x["subject"] == "namespace" for x in f))

    def test_low_runway_makes_status_warning(self):
        doc = load_real()
        known_out = sum(C.expected_rate(p["award_usd"]) for p in doc["providers"])
        daily = doc["master_stream_wei_s"] * 86400
        reader = FakeReader(
            healthy_rates(doc),
            account_net={doc["pod"].lower(): doc["master_stream_wei_s"] - known_out},
            balances={(C.USDCX.lower(), C.TIMELOCK.lower()): 30 * daily})
        s = M.build_status(doc, reader, 1, "2026-08-05T21:00:00Z")
        self.assertEqual(s["overall"], "warning")
        self.assertTrue(any(x["code"] == "low_runway" for x in M.findings(s)))

    def test_unaccounted_flow_is_critical(self):
        doc = load_real()
        known_out = sum(C.expected_rate(p["award_usd"]) for p in doc["providers"])
        reader = FakeReader(
            healthy_rates(doc),
            account_net={doc["pod"].lower():
                         doc["master_stream_wei_s"] - known_out - 10**12},
            balances={(C.USDCX.lower(), C.TIMELOCK.lower()): 10**30})
        s = M.build_status(doc, reader, 1, "2026-08-05T21:00:00Z")
        self.assertEqual(s["overall"], "critical")
        self.assertTrue(any(x["code"] == "unaccounted_flow" for x in M.findings(s)))


class TestWriteStatus(unittest.TestCase):
    def test_write_reports_change_then_no_change(self):
        status = {"_generated": True, "overall": "healthy", "checked_at": "t1"}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "status.json"
            self.assertTrue(M.write_status(status, p))
            self.assertFalse(M.write_status(status, p))

    def test_checked_at_alone_does_not_count_as_change(self):
        # Otherwise every daily run commits noise and the git history stops
        # being a useful record of what actually changed.
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "status.json"
            M.write_status({"_generated": True, "overall": "healthy",
                            "checked_at": "t1"}, p)
            self.assertFalse(
                M.write_status({"_generated": True, "overall": "healthy",
                                "checked_at": "t2"}, p))


class TestSinceTimestamp(unittest.TestCase):
    def test_streams_carry_flow_start(self):
        doc = load_real()
        results = M.check_streams(doc, FakeReader(healthy_rates(doc)))
        self.assertTrue(all(r["since"] == 1785561311 for r in results))

    def test_program_epoch_is_explicit_not_inferred(self):
        # A stream's own lastUpdated is when the flow last CHANGED, not when
        # SPP3 began. Unruggable was unchanged at $400k, so its flow still
        # reports its 2025-09-12 SPP2 start. Inferring the epoch per stream
        # would credit them 327 days of SPP2 money in an SPP3 total.
        doc = load_real()
        self.assertEqual(doc["spp3_stream_start"], 1785561311)

    def test_retired_streams_have_no_start(self):
        doc = load_real()
        results = M.check_retired(doc, FakeReader(healthy_rates(doc)))
        self.assertTrue(all(r["since"] == 0 for r in results))


if __name__ == "__main__":
    unittest.main()

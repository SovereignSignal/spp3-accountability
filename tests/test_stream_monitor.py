import json
import sys
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


if __name__ == "__main__":
    unittest.main()

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import acct_config as C
import ledger as L


def topic(addr):
    return "0x" + addr.lower().replace("0x", "").rjust(64, "0")


def word(value):
    if value < 0:
        value += 1 << 256
    return hex(value)[2:].rjust(64, "0")


NOMENTUM = "0xF8DD51A64942aAC80340a71fC22AF8d41591cE82"
PROVIDER = "0x168CAfEcFBE97dF85968Ea039CC11D10a9A44567"
# Literal on purpose: a fake that matched on L.CFA_V1 is what hid the Polygon
# address for a month. Superfluid CFAv1 proxy on Ethereum mainnet.
MAINNET_CFA = "0x2844c1bbda121e9e43105630b9c8310e5c72744b"


class FakeChain:
    epoch = 1785561311
    latest = epoch + 100

    def block_number(self):
        return L.START_BLOCK + 2

    def block_at_or_after(self, timestamp, latest=None):
        return L.START_BLOCK

    def block_timestamp(self, block):
        return {
            L.START_BLOCK: self.epoch + 40,
            L.START_BLOCK + 1: self.epoch + 60,
            L.START_BLOCK + 2: self.latest,
        }.get(block, self.latest)

    def flowrate(self, token, sender, receiver):
        return C.expected_rate(500000)

    def code(self, address):
        return "0x6080" if address.lower() == MAINNET_CFA else "0x"

    def flow_info(self, token, sender, receiver):
        return {
            "last_updated": FakeChain.epoch,
            "flowrate": C.expected_rate(500000),
            "deposit": 0,
        }

    def event_logs(self, address, topics, start, end):
        if address.lower() == C.USDC.lower():
            out = []
            if start <= L.START_BLOCK <= end and len(topics) > 2 and topics[2] == topic(C.STREAM_POD):
                out.append({
                    "transactionHash": "0x" + "1" * 64,
                    "logIndex": "0x1", "blockNumber": hex(L.START_BLOCK),
                    "topics": [L.TRANSFER_TOPIC, topic(C.TIMELOCK), topic(C.STREAM_POD)],
                    "data": hex(500000 * 10**6),
                })
            if start <= L.START_BLOCK + 1 <= end and len(topics) == 2 and topics[1] == topic(C.STREAM_POD):
                out.append({
                    "transactionHash": "0x" + "2" * 64,
                    "logIndex": "0x2", "blockNumber": hex(L.START_BLOCK + 1),
                    "topics": [L.TRANSFER_TOPIC, topic(C.STREAM_POD), topic(NOMENTUM)],
                    "data": hex(30000 * 10**6),
                })
            return out
        if address.lower() == MAINNET_CFA:
            out = []
            rate = C.expected_rate(500000)
            if start <= L.START_BLOCK <= end:
                out.append({
                    "transactionHash": "0x" + "3" * 64,
                    "logIndex": "0x3", "blockNumber": hex(L.START_BLOCK),
                    "topics": [L.FLOW_UPDATED_TOPIC, topic(C.USDCX),
                               topic(C.STREAM_POD), topic(PROVIDER)],
                    "data": "0x" + word(0) + word(0) + word(0) + word(128),
                })
            if start <= L.START_BLOCK + 1 <= end:
                out.append({
                    "transactionHash": "0x" + "4" * 64,
                    "logIndex": "0x4", "blockNumber": hex(L.START_BLOCK + 1),
                    "topics": [L.FLOW_UPDATED_TOPIC, topic(C.USDCX),
                               topic(C.STREAM_POD), topic(PROVIDER)],
                    "data": "0x" + word(rate) + word(0) + word(0) + word(128),
                })
            return out
        return []


class TestLedger(unittest.TestCase):
    def fixture(self):
        commitments = {"marketplace_award": {
            "slug": "nomentum", "name": "Nomentum Labs",
            "recipient": NOMENTUM, "award_usd": 500000,
            "paid_usdc": 30000, "upfront_total_usd": 90000,
            "conditional_stream_usd": 310000, "performance_reserve_usd": 100000}}
        providers = {"spp3_stream_start": FakeChain.epoch, "providers": [{
            "slug": "namespace", "name": "Namespace", "cohort": "spp3",
            "award_usd": 500000, "approved_wallet": PROVIDER}]}
        return commitments, providers

    def test_build_reconciles_custody_and_stream_history(self):
        commitments, providers = self.fixture()
        previous = {
            "through_block": L.START_BLOCK - 1,
            "events": [],
        }
        doc = L.build(FakeChain(), commitments, providers, previous=previous,
                      latest=L.START_BLOCK + 2)
        self.assertEqual(doc["summary"]["usdc_in"], 500000)
        self.assertEqual(doc["summary"]["usdc_out"], 30000)
        self.assertEqual(doc["summary"]["net_usdc"], 470000)
        stream = doc["stream_history"]["streams"][0]
        rate = C.expected_rate(500000)
        self.assertEqual(stream["delivered_wei"], rate * 100)
        self.assertTrue(stream["reconciled"])
        self.assertTrue(doc["stream_history"]["all_reconciled"])
        self.assertEqual(doc["financials"]["authorized_usd"], 1000000)
        self.assertEqual(doc["financials"]["marketplace_held_usd"], 470000)
        self.assertEqual(doc["financials"]["marketplace_gated_usd"], 410000)
        self.assertEqual(doc["financials"]["marketplace_scheduled_usd"], 60000)

    def test_signed_word_decoding(self):
        self.assertEqual(L._signed_word(word(-1)), -1)
        self.assertEqual(L._signed_word(word(123)), 123)


class TestCfaAddress(unittest.TestCase):
    def test_cfa_is_the_ethereum_mainnet_deployment(self):
        self.assertEqual(L.CFA_V1.lower(), MAINNET_CFA)


RETIRED = "0x" + "e" * 40
STRANGER = "0x" + "d" * 40
T0 = 1_800_000_000          # checkpoint timestamp
CHECKPOINT = 26_000_000
R500 = C.expected_rate(500000)


def flow_log(block, index, receiver, rate):
    return {"transactionHash": "0x" + ("%064x" % (block * 10 + index)),
            "logIndex": hex(index), "blockNumber": hex(block),
            "topics": [L.FLOW_UPDATED_TOPIC, topic(C.USDCX), topic(C.STREAM_POD), topic(receiver)],
            "data": "0x" + word(rate) + word(0) + word(0) + word(128)}


class AdvanceChain:
    """Blocks after CHECKPOINT are 12 s apart. Flow logs only at the mainnet CFA."""
    def __init__(self, logs, live):
        self.logs, self.live = logs, live

    def block_timestamp(self, block):
        return T0 + (block - CHECKPOINT) * 12

    def event_logs(self, address, topics, start, end):
        if address.lower() != MAINNET_CFA:
            return []
        return [x for x in self.logs if start <= int(x["blockNumber"], 16) <= end]

    def flowrate(self, token, sender, receiver):
        return self.live.get(receiver.lower(), 0)


class TestAdvanceHistory(unittest.TestCase):
    """The incremental path every published figure comes from after bootstrap."""

    def setUp(self):
        self.providers = {"spp3_stream_start": T0 - 86400, "providers": [{
            "slug": "namespace", "name": "Namespace", "cohort": "spp3",
            "award_usd": 500000, "approved_wallet": PROVIDER}],
            "retired": [{"slug": "old", "approved_wallet": RETIRED}]}
        self.commitments = TestLedger().fixture()[0]
        self.previous = {"epoch": T0 - 86400, "through_block": CHECKPOINT,
                         "through_timestamp": T0, "unknown_flow_events": [],
                         "retired_flow_events": [], "streams": [{
                             "slug": "namespace", "name": "Namespace", "cohort": "spp3",
                             "receiver": PROVIDER, "epoch_rate_wei_s": R500,
                             "current_rate_wei_s": R500, "delivered_wei": 7,
                             "delivered_usd": 7 / 10**18, "changes": []}]}

    def advance(self, logs, live, latest=CHECKPOINT + 100, previous=None):
        client = AdvanceChain(logs, live)
        return L._advance_history(client, self.providers, previous or self.previous, latest,
                                  client.block_timestamp(latest), {}, self.commitments)

    def test_rate_change_then_stop(self):
        half = R500 // 2
        h = self.advance([flow_log(CHECKPOINT + 10, 1, PROVIDER, half),
                          flow_log(CHECKPOINT + 30, 1, PROVIDER, 0)],
                         {PROVIDER.lower(): 0})
        s = h["streams"][0]
        self.assertEqual(s["delivered_wei"], 7 + R500 * 120 + half * 240)
        self.assertEqual(s["current_rate_wei_s"], 0)
        self.assertEqual(len(s["changes"]), 2)
        self.assertTrue(h["all_reconciled"])

    def test_live_rate_mismatch_does_not_reconcile(self):
        h = self.advance([], {PROVIDER.lower(): R500 - 1})
        self.assertFalse(h["streams"][0]["reconciled"])
        self.assertFalse(h["all_reconciled"])

    def test_retired_and_unknown_receivers_are_separated(self):
        h = self.advance([flow_log(CHECKPOINT + 5, 1, RETIRED, 0),
                          flow_log(CHECKPOINT + 6, 1, STRANGER, 5)],
                         {PROVIDER.lower(): R500})
        self.assertEqual([e["retired_slug"] for e in h["retired_flow_events"]], ["old"])
        self.assertEqual([e["receiver"].lower() for e in h["unknown_flow_events"]], [STRANGER])

    def test_marketplace_stream_opens_as_a_tracked_stream(self):
        rate = 9_830_000_000_000_000
        h = self.advance([flow_log(CHECKPOINT + 50, 1, NOMENTUM, rate)],
                         {PROVIDER.lower(): R500, NOMENTUM.lower(): rate})
        nom = h["streams"][-1]
        self.assertEqual((nom["slug"], nom["cohort"]), ("nomentum", "marketplace"))
        self.assertEqual(nom["delivered_wei"], rate * 50 * 12)
        self.assertEqual(h["unknown_flow_events"], [])
        self.assertTrue(h["all_reconciled"])

    def test_parked_unknown_event_replays_once_receiver_is_configured(self):
        rate = 1_000
        parked = L._decode_flow(flow_log(CHECKPOINT - 50, 1, NOMENTUM, rate),
                                AdvanceChain([], {}), {})
        previous = dict(self.previous, unknown_flow_events=[parked])
        h = self.advance([], {PROVIDER.lower(): R500, NOMENTUM.lower(): rate},
                         previous=previous)
        nom = h["streams"][-1]
        self.assertEqual(nom["slug"], "nomentum")
        self.assertEqual(nom["delivered_wei"], rate * 150 * 12)
        self.assertEqual(h["unknown_flow_events"], [])

    def test_parked_event_for_unconfigured_receiver_stays_unknown(self):
        # Dropping it would let an unexplained stream vanish and the ledger
        # report healthy.
        parked = L._decode_flow(flow_log(CHECKPOINT - 50, 1, STRANGER, 5),
                                AdvanceChain([], {}), {})
        previous = dict(self.previous, unknown_flow_events=[parked])
        h = self.advance([], {PROVIDER.lower(): R500}, previous=previous)
        self.assertEqual([e["receiver"].lower() for e in h["unknown_flow_events"]], [STRANGER])

    def test_open_stream_moves_out_of_gated(self):
        rate = 10**16
        h = self.advance([flow_log(CHECKPOINT + 50, 1, NOMENTUM, rate)],
                         {PROVIDER.lower(): R500, NOMENTUM.lower(): rate})
        events = [{"classification": "marketplace payment", "amount": 30000}]
        fin = L._financials(self.providers, self.commitments, events, h)
        streamed = rate * 50 * 12 / 10**18
        self.assertEqual(fin["marketplace_gated_usd"], 100000)
        self.assertAlmostEqual(fin["marketplace_held_usd"], 470000 - streamed)
        self.assertAlmostEqual(fin["marketplace_held_usd"],
                               fin["marketplace_gated_usd"] + fin["marketplace_scheduled_usd"]
                               + fin["marketplace_streaming_remaining_usd"])

    def test_closed_stream_keeps_310k_gated(self):
        h = self.advance([], {PROVIDER.lower(): R500})
        fin = L._financials(self.providers, self.commitments,
                            [{"classification": "marketplace payment", "amount": 30000}], h)
        self.assertEqual(fin["marketplace_gated_usd"], 410000)
        self.assertEqual(fin["marketplace_held_usd"], 470000)

    def test_provider_without_stream_does_not_crash_financials(self):
        providers = dict(self.providers, providers=self.providers["providers"] + [{
            "slug": "newco", "name": "NewCo", "cohort": "spp3", "award_usd": 100000,
            "approved_wallet": "0x" + "c" * 40}])
        h = self.advance([], {PROVIDER.lower(): R500})
        fin = L._financials(providers, self.commitments, [], h)
        self.assertEqual([a["delivered_usd"] for a in fin["awards"] if a["slug"] == "newco"], [0.0])


class TestLedgerMain(unittest.TestCase):
    """main() is the publish gate. In Actions its exit code is the only signal."""

    def run_main(self, healthy=True, published="pushed", code="0x6080", head=26_000_200,
                 through=26_000_000, dry=False):
        import tempfile
        from unittest import mock
        import stream_monitor
        history = {"all_reconciled": healthy, "streams": [],
                   "unknown_flow_events": [] if healthy else [
                       {"receiver": STRANGER, "block_number": 1, "flow_rate_wei_s": 5}]}
        doc = {"through_block": head, "financials": {}, "stream_history": history}
        client = mock.Mock()
        client.code.return_value = code
        client.block_number.return_value = head
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            path.write_text(json.dumps({"through_block": through}))
            with mock.patch.object(C, "LEDGER_PATH", path), \
                    mock.patch.object(L.chain, "Chain", return_value=client), \
                    mock.patch.object(L, "build", return_value=doc) as build, \
                    mock.patch.object(stream_monitor, "publish", return_value=published) as pub:
                rc = L.main(["--dry-run"] if dry else [])
                return rc, pub.called, json.loads(path.read_text()), build

    def test_healthy_change_is_published(self):
        rc, published, _, build = self.run_main()
        self.assertEqual((rc, published), (0, True))
        self.assertEqual(build.call_args.kwargs["latest"], 26_000_200 - L.FINALITY_DEPTH)

    def test_unreconciled_history_is_never_written_or_published(self):
        rc, published, on_disk, _ = self.run_main(healthy=False)
        self.assertEqual((rc, published), (2, False))
        self.assertEqual(on_disk, {"through_block": 26_000_000})

    def test_dry_run_reports_unhealthy(self):
        self.assertEqual(self.run_main(healthy=False, dry=True)[0], 2)

    def test_push_that_stays_local_fails_the_job(self):
        self.assertEqual(self.run_main(published="local")[0], 3)

    def test_missing_cfa_contract_refuses_to_run(self):
        rc, published, _, build = self.run_main(code="0x")
        self.assertEqual((rc, published, build.called), (2, False, False))

    def test_checkpoint_never_moves_backwards(self):
        rc, published, _, build = self.run_main(head=26_000_050)
        self.assertEqual((rc, published, build.called), (0, False, False))


if __name__ == "__main__":
    unittest.main()

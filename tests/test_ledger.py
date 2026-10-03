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
        if address.lower() == L.CFA_V1.lower():
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
        doc = L.build(FakeChain(), commitments, providers, latest=L.START_BLOCK + 2)
        self.assertEqual(doc["summary"]["usdc_in"], 500000)
        self.assertEqual(doc["summary"]["usdc_out"], 30000)
        self.assertEqual(doc["summary"]["net_usdc"], 470000)
        stream = doc["stream_history"]["streams"][0]
        rate = C.expected_rate(500000)
        self.assertEqual(stream["delivered_wei"], rate * 80)
        self.assertTrue(stream["reconciled"])
        self.assertTrue(doc["stream_history"]["all_reconciled"])
        self.assertEqual(doc["financials"]["authorized_usd"], 1000000)
        self.assertEqual(doc["financials"]["marketplace_held_usd"], 470000)
        self.assertEqual(doc["financials"]["marketplace_gated_usd"], 410000)
        self.assertEqual(doc["financials"]["marketplace_scheduled_usd"], 60000)

    def test_signed_word_decoding(self):
        self.assertEqual(L._signed_word(word(-1)), -1)
        self.assertEqual(L._signed_word(word(123)), 123)


if __name__ == "__main__":
    unittest.main()

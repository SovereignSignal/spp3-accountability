import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import acct_config as C
import ledger as L


def topic(addr):
    return "0x" + addr.lower().replace("0x", "").rjust(64, "0")


class FakeChain:
    def block_number(self):
        return L.START_BLOCK + 1

    def block_timestamp(self, block):
        return 1789053335

    def event_logs(self, address, topics, start, end):
        if len(topics) > 2 and topics[2] == topic(C.STREAM_POD):
            return [{
                "transactionHash": "0x" + "1" * 64,
                "logIndex": "0x1",
                "blockNumber": hex(L.START_BLOCK),
                "topics": [L.TRANSFER_TOPIC, topic(C.TIMELOCK), topic(C.STREAM_POD)],
                "data": hex(500000 * 10**6),
            }]
        if len(topics) == 2 and topics[1] == topic(C.STREAM_POD):
            return [{
                "transactionHash": "0x" + "2" * 64,
                "logIndex": "0x2",
                "blockNumber": hex(L.START_BLOCK + 1),
                "topics": [L.TRANSFER_TOPIC, topic(C.STREAM_POD),
                           topic("0xF8DD51A64942aAC80340a71fC22AF8d41591cE82")],
                "data": hex(30000 * 10**6),
            }]
        return []


class TestLedger(unittest.TestCase):
    def test_build_reconciles_known_marketplace_movements(self):
        commitments = {"marketplace_award": {
            "recipient": "0xF8DD51A64942aAC80340a71fC22AF8d41591cE82"}}
        doc = L.build(FakeChain(), commitments, latest=L.START_BLOCK + 1)
        self.assertEqual(doc["summary"]["usdc_in"], 500000)
        self.assertEqual(doc["summary"]["usdc_out"], 30000)
        self.assertEqual(doc["summary"]["net_usdc"], 470000)
        self.assertEqual(doc["summary"]["unclassified_events"], 0)
        self.assertEqual([e["classification"] for e in doc["events"]],
                         ["treasury funding", "marketplace payment"])


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import chain


class TestKeccak(unittest.TestCase):
    def test_empty_string_vector(self):
        self.assertEqual(
            chain.keccak256(b"").hex(),
            "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470",
        )

    def test_known_selectors(self):
        self.assertEqual(chain.selector("transfer(address,uint256)"), "a9059cbb")
        self.assertEqual(chain.selector("balanceOf(address)"), "70a08231")
        self.assertEqual(
            chain.selector("getFlowrate(address,address,address)"), "1d8b6526")
        self.assertEqual(
            chain.selector("getAccountFlowrate(address,address)"), "22c904d9")


class TestEncoding(unittest.TestCase):
    def test_encode_call_pads_addresses_to_32_bytes(self):
        data = chain.encode_call(
            "balanceOf(address)", "0xB162Bf7A7fD64eF32b787719335d06B2780e31D1")
        self.assertEqual(data[:10], "0x70a08231")
        self.assertEqual(len(data), 2 + 8 + 64)
        self.assertTrue(
            data.endswith("b162bf7a7fd64ef32b787719335d06b2780e31d1"))

    def test_encode_call_multiple_args(self):
        data = chain.encode_call(
            "getFlowrate(address,address,address)",
            "0x1BA8603DA702602A8657980e825A6DAa03Dee93a",
            "0xB162Bf7A7fD64eF32b787719335d06B2780e31D1",
            "0x168CAfEcFBE97dF85968Ea039CC11D10a9A44567")
        self.assertEqual(len(data), 2 + 8 + 64 * 3)


class TestDecoding(unittest.TestCase):
    def test_decode_uint256(self):
        self.assertEqual(chain.decode_uint256("0x" + "00" * 31 + "ff"), 255)

    def test_decode_int96_positive(self):
        # 15854895991882293 = Namespace rate
        raw = "0x" + format(15854895991882293, "064x")
        self.assertEqual(chain.decode_int96(raw), 15854895991882293)

    def test_decode_int96_negative(self):
        # -67497852409250, sign-extended into 32 bytes as the ABI returns it
        val = (1 << 256) - 67497852409250
        raw = "0x" + format(val, "064x")
        self.assertEqual(chain.decode_int96(raw), -67497852409250)

    def test_decode_int96_zero(self):
        self.assertEqual(chain.decode_int96("0x" + "00" * 32), 0)



class FakeOpener:
    """Records calls and replays scripted responses or raises."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    def __call__(self, url, body, headers):
        self.urls.append(url)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        import json as _json
        return _json.dumps(item).encode()


class TestChainClient(unittest.TestCase):
    def test_uses_first_working_endpoint(self):
        op = FakeOpener([{"jsonrpc": "2.0", "id": 1, "result": "0x1f"}])
        c = chain.Chain(rpcs=["https://a.example", "https://b.example"], opener=op)
        self.assertEqual(c.call("0xdead", "0xbeef"), "0x1f")
        self.assertEqual(op.urls, ["https://a.example"])

    def test_fails_over_to_next_endpoint(self):
        op = FakeOpener([OSError("connection reset"),
                         {"jsonrpc": "2.0", "id": 1, "result": "0x2a"}])
        c = chain.Chain(rpcs=["https://a.example", "https://b.example"], opener=op)
        self.assertEqual(c.call("0xdead", "0xbeef"), "0x2a")
        self.assertEqual(op.urls, ["https://a.example", "https://b.example"])

    def test_fails_over_on_jsonrpc_error(self):
        op = FakeOpener([{"jsonrpc": "2.0", "id": 1,
                          "error": {"code": -32603, "message": "Internal error"}},
                         {"jsonrpc": "2.0", "id": 1, "result": "0x7"}])
        c = chain.Chain(rpcs=["https://a.example", "https://b.example"], opener=op)
        self.assertEqual(c.call("0xdead", "0xbeef"), "0x7")

    def test_raises_when_all_endpoints_fail(self):
        op = FakeOpener([OSError("boom"), OSError("boom")])
        c = chain.Chain(rpcs=["https://a.example", "https://b.example"], opener=op)
        with self.assertRaises(chain.RpcUnavailable):
            c.call("0xdead", "0xbeef")

    def test_flowrate_decodes_int96(self):
        raw = "0x" + format(15854895991882293, "064x")
        op = FakeOpener([{"jsonrpc": "2.0", "id": 1, "result": raw}])
        c = chain.Chain(rpcs=["https://a.example"], opener=op)
        self.assertEqual(
            c.flowrate("0x1BA8603DA702602A8657980e825A6DAa03Dee93a",
                       "0xB162Bf7A7fD64eF32b787719335d06B2780e31D1",
                       "0x168CAfEcFBE97dF85968Ea039CC11D10a9A44567"),
            15854895991882293)

    def test_account_flowrate_decodes_negative(self):
        raw = "0x" + format((1 << 256) - 67497852409250, "064x")
        op = FakeOpener([{"jsonrpc": "2.0", "id": 1, "result": raw}])
        c = chain.Chain(rpcs=["https://a.example"], opener=op)
        self.assertEqual(
            c.account_flowrate("0x1BA8603DA702602A8657980e825A6DAa03Dee93a",
                               "0xB162Bf7A7fD64eF32b787719335d06B2780e31D1"),
            -67497852409250)

    def test_sends_browser_user_agent(self):
        captured = {}

        def op(url, body, headers):
            captured.update(headers)
            import json as _json
            return _json.dumps({"jsonrpc": "2.0", "id": 1, "result": "0x1"}).encode()

        c = chain.Chain(rpcs=["https://a.example"], opener=op)
        c.call("0xdead", "0xbeef")
        self.assertIn("Mozilla", captured["User-Agent"])

if __name__ == "__main__":
    unittest.main()

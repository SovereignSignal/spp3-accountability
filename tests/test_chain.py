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


if __name__ == "__main__":
    unittest.main()

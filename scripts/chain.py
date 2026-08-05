"""chain.py — minimal Ethereum read client. Standard library only.

Knows about Ethereum ABI encoding and JSON-RPC. Knows nothing about SPP3.
Pure functions (keccak256, selector, encode_call, decode_*) are separated
from network I/O so callers can unit-test logic without a connection.
"""
_RC = [0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
       0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
       0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
       0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
       0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
       0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008]
_ROTC = [1, 3, 6, 10, 15, 21, 28, 36, 45, 55, 2, 14, 27, 41, 56,
         8, 25, 43, 62, 18, 39, 61, 20, 44]
_PILN = [10, 7, 11, 17, 18, 3, 5, 16, 8, 21, 24, 4, 15, 23, 19,
         13, 12, 2, 20, 14, 22, 9, 6, 1]
_M = (1 << 64) - 1


def _rol(x, n):
    return ((x << n) | (x >> (64 - n))) & _M


def _keccak_f(st):
    for rnd in range(24):
        bc = [st[i] ^ st[i + 5] ^ st[i + 10] ^ st[i + 15] ^ st[i + 20]
              for i in range(5)]
        for i in range(5):
            t = bc[(i + 4) % 5] ^ _rol(bc[(i + 1) % 5], 1)
            for j in range(0, 25, 5):
                st[j + i] ^= t
        t = st[1]
        for i in range(24):
            j = _PILN[i]
            prev = st[j]
            st[j] = _rol(t, _ROTC[i])
            t = prev
        for j in range(0, 25, 5):
            row = [st[j + i] for i in range(5)]
            for i in range(5):
                st[j + i] = row[i] ^ (((~row[(i + 1) % 5]) & _M) & row[(i + 2) % 5])
        st[0] ^= _RC[rnd]
    return st


def keccak256(data):
    """Keccak-256 (the pre-standard padding Ethereum uses, not SHA3-256)."""
    rate = 136
    st = [0] * 25
    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % rate != 0:
        padded.append(0x00)
    padded[-1] |= 0x80
    for off in range(0, len(padded), rate):
        block = padded[off:off + rate]
        for i in range(rate // 8):
            st[i] ^= int.from_bytes(block[i * 8:(i + 1) * 8], "little")
        _keccak_f(st)
    return b"".join(st[i].to_bytes(8, "little") for i in range(4))


def selector(signature):
    """4-byte function selector as 8 hex chars, no 0x prefix."""
    return keccak256(signature.encode()).hex()[:8]


def _pad_address(addr):
    return addr.lower().replace("0x", "").rjust(64, "0")


def encode_call(signature, *args):
    """Calldata for a function taking only address arguments."""
    return "0x" + selector(signature) + "".join(_pad_address(a) for a in args)


def decode_uint256(hexstr):
    return int(hexstr, 16)


def decode_int96(hexstr):
    """Decode an int96 returned right-aligned in a 32-byte word."""
    v = int(hexstr, 16) & ((1 << 96) - 1)
    if v >= 1 << 95:
        v -= 1 << 96
    return v

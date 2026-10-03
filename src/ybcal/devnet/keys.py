"""Fixed regtest pool keys and their P2PKH addresses (standard library only).

Owner: WP-9.

A pool needs its payout address *before* it starts (``-yellowbackpayoutaddress``). ycash6's test
framework uses three fixed WIFs for that (``qa/rpc-tests/test_framework/yellowback_util.py``
``POOL_WIFS``, regtest address bytes ``1c 95``); this module derives the same addresses without
the framework: secp256k1 public key → HASH160 → Base58Check. RIPEMD-160 comes from ``hashlib``
when the OpenSSL build provides it, else from the pure-Python fallback below (OpenSSL 3 dropped it
from the default provider on some macOS builds).
"""

from __future__ import annotations

import hashlib
import struct

OWNER_WP = "WP-9"

#: ycash6 ``POOL_WIFS`` (regtest test keys; never funds of value).
POOL_WIFS: tuple[str, ...] = (
    "cQ9RpTGKp1NqLABAMv7xVV6RK6ZWhsx7ZcJgArRdD8MBa75kTEEE",
    "cQFGkN4XoV9snA1N1sxTKnX1fA5w9DCwu5Uao8tHgHtJTAcjdT4W",
    "cTa9Ej6rWau4VyxUQLEonn1DS27HzAZgoqSHz2soDp998gBHhXe8",
)
#: Ycash regtest P2PKH version bytes (``chainparams.cpp``; renders ``tm…``).
REGTEST_PUBKEY_ADDRESS: bytes = b"\x1c\x95"

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_G = (
    0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
)


def b58decode_check(s: str) -> bytes:
    """Base58Check decode (payload without the 4-byte checksum)."""
    n = 0
    for c in s:
        n = n * 58 + _B58.index(c)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    raw = b"\x00" * (len(s) - len(s.lstrip("1"))) + raw
    payload, check = raw[:-4], raw[-4:]
    if hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4] != check:
        raise ValueError("bad Base58Check checksum")
    return payload


def b58encode_check(payload: bytes) -> str:
    """Base58Check encode."""
    raw = payload + hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    n = int.from_bytes(raw, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return "1" * (len(raw) - len(raw.lstrip(b"\x00"))) + out


def wif_to_secret(wif: str) -> tuple[int, bool]:
    """``(secret, compressed)`` of a WIF key."""
    payload = b58decode_check(wif)
    compressed = len(payload) == 34 and payload[-1] == 1
    return int.from_bytes(payload[1:33], "big"), compressed


def _add(p: tuple[int, int] | None, q: tuple[int, int] | None) -> tuple[int, int] | None:
    if p is None:
        return q
    if q is None:
        return p
    if p[0] == q[0] and (p[1] + q[1]) % _P == 0:
        return None
    if p == q:
        lam = 3 * p[0] * p[0] * pow(2 * p[1], -1, _P) % _P
    else:
        lam = (q[1] - p[1]) * pow(q[0] - p[0], -1, _P) % _P
    x = (lam * lam - p[0] - q[0]) % _P
    return x, (lam * (p[0] - x) - p[1]) % _P


def secret_to_pubkey(secret: int, compressed: bool = True) -> bytes:
    """secp256k1 public key (SEC1, compressed by default)."""
    if not 0 < secret < _N:
        raise ValueError("secret out of range")
    r: tuple[int, int] | None = None
    q: tuple[int, int] | None = _G
    k = secret
    while k:
        if k & 1:
            r = _add(r, q)
        q = _add(q, q)
        k >>= 1
    assert r is not None
    if compressed:
        return bytes([2 + (r[1] & 1)]) + r[0].to_bytes(32, "big")
    return b"\x04" + r[0].to_bytes(32, "big") + r[1].to_bytes(32, "big")


# --- RIPEMD-160 (fallback) -----------------------------------------------------------------------------

_RL = [
    0,
    1,
    2,
    3,
    4,
    5,
    6,
    7,
    8,
    9,
    10,
    11,
    12,
    13,
    14,
    15,
    7,
    4,
    13,
    1,
    10,
    6,
    15,
    3,
    12,
    0,
    9,
    5,
    2,
    14,
    11,
    8,
    3,
    10,
    14,
    4,
    9,
    15,
    8,
    1,
    2,
    7,
    0,
    6,
    13,
    11,
    5,
    12,
    1,
    9,
    11,
    10,
    0,
    8,
    12,
    4,
    13,
    3,
    7,
    15,
    14,
    5,
    6,
    2,
    4,
    0,
    5,
    9,
    7,
    12,
    2,
    10,
    14,
    1,
    3,
    8,
    11,
    6,
    15,
    13,
]
_RR = [
    5,
    14,
    7,
    0,
    9,
    2,
    11,
    4,
    13,
    6,
    15,
    8,
    1,
    10,
    3,
    12,
    6,
    11,
    3,
    7,
    0,
    13,
    5,
    10,
    14,
    15,
    8,
    12,
    4,
    9,
    1,
    2,
    15,
    5,
    1,
    3,
    7,
    14,
    6,
    9,
    11,
    8,
    12,
    2,
    10,
    0,
    4,
    13,
    8,
    6,
    4,
    1,
    3,
    11,
    15,
    0,
    5,
    12,
    2,
    13,
    9,
    7,
    10,
    14,
    12,
    15,
    10,
    4,
    1,
    5,
    8,
    7,
    6,
    2,
    13,
    14,
    0,
    3,
    9,
    11,
]
_SL = [
    11,
    14,
    15,
    12,
    5,
    8,
    7,
    9,
    11,
    13,
    14,
    15,
    6,
    7,
    9,
    8,
    7,
    6,
    8,
    13,
    11,
    9,
    7,
    15,
    7,
    12,
    15,
    9,
    11,
    7,
    13,
    12,
    11,
    13,
    6,
    7,
    14,
    9,
    13,
    15,
    14,
    8,
    13,
    6,
    5,
    12,
    7,
    5,
    11,
    12,
    14,
    15,
    14,
    15,
    9,
    8,
    9,
    14,
    5,
    6,
    8,
    6,
    5,
    12,
    9,
    15,
    5,
    11,
    6,
    8,
    13,
    12,
    5,
    12,
    13,
    14,
    11,
    8,
    5,
    6,
]
_SR = [
    8,
    9,
    9,
    11,
    13,
    15,
    15,
    5,
    7,
    7,
    8,
    11,
    14,
    14,
    12,
    6,
    9,
    13,
    15,
    7,
    12,
    8,
    9,
    11,
    7,
    7,
    12,
    7,
    6,
    15,
    13,
    11,
    9,
    7,
    15,
    11,
    8,
    6,
    6,
    14,
    12,
    13,
    5,
    14,
    13,
    13,
    7,
    5,
    15,
    5,
    8,
    11,
    14,
    14,
    6,
    14,
    6,
    9,
    12,
    9,
    12,
    5,
    15,
    8,
    8,
    5,
    12,
    9,
    12,
    5,
    14,
    6,
    8,
    13,
    6,
    5,
    15,
    13,
    11,
    11,
]
_KL = [0x00000000, 0x5A827999, 0x6ED9EBA1, 0x8F1BBCDC, 0xA953FD4E]
_KR = [0x50A28BE6, 0x5C4DD124, 0x6D703EF3, 0x7A6D76E9, 0x00000000]


def _f(j: int, x: int, y: int, z: int) -> int:
    if j < 16:
        return x ^ y ^ z
    if j < 32:
        return (x & y) | (~x & z)
    if j < 48:
        return (x | ~y) ^ z
    if j < 64:
        return (x & z) | (y & ~z)
    return x ^ (y | ~z)


def _rol(x: int, n: int) -> int:
    x &= 0xFFFFFFFF
    return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF


def ripemd160_py(data: bytes) -> bytes:
    """Pure-Python RIPEMD-160."""
    h = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0]
    msg = data + b"\x80" + b"\x00" * ((55 - len(data)) % 64) + struct.pack("<Q", 8 * len(data))
    for off in range(0, len(msg), 64):
        x = struct.unpack("<16I", msg[off : off + 64])
        al, bl, cl, dl, el = h
        ar, br, cr, dr, er = h
        for j in range(80):
            t = _rol(al + _f(j, bl, cl, dl) + x[_RL[j]] + _KL[j // 16], _SL[j]) + el
            al, el, dl, cl, bl = el, dl, _rol(cl, 10), bl, t & 0xFFFFFFFF
            t = _rol(ar + _f(79 - j, br, cr, dr) + x[_RR[j]] + _KR[j // 16], _SR[j]) + er
            ar, er, dr, cr, br = er, dr, _rol(cr, 10), br, t & 0xFFFFFFFF
        t = (h[1] + cl + dr) & 0xFFFFFFFF
        h[1] = (h[2] + dl + er) & 0xFFFFFFFF
        h[2] = (h[3] + el + ar) & 0xFFFFFFFF
        h[3] = (h[4] + al + br) & 0xFFFFFFFF
        h[4] = (h[0] + bl + cr) & 0xFFFFFFFF
        h[0] = t
    return struct.pack("<5I", *h)


def ripemd160(data: bytes) -> bytes:
    """RIPEMD-160 via hashlib when available, else :func:`ripemd160_py`."""
    try:
        return hashlib.new("ripemd160", data).digest()
    except (ValueError, TypeError):
        return ripemd160_py(data)


def hash160(data: bytes) -> bytes:
    """RIPEMD-160(SHA-256(data))."""
    return ripemd160(hashlib.sha256(data).digest())


def address_of(wif: str, version: bytes = REGTEST_PUBKEY_ADDRESS) -> str:
    """The P2PKH address of a WIF key (regtest by default)."""
    secret, compressed = wif_to_secret(wif)
    return b58encode_check(version + hash160(secret_to_pubkey(secret, compressed)))


def pool_addresses() -> list[str]:
    """Payout addresses of :data:`POOL_WIFS`."""
    return [address_of(w) for w in POOL_WIFS]

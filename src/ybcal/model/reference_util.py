# ==== ybcal vendored file -- do not edit; re-vendor with `ybcal verify --revendor` ====
# source: boyfromcave/ycash6 qa/rpc-tests/test_framework/yellowback_util.py + qa/rpc-tests/test_framework/util.py
# commit: 7702d22606d1ec3edf0fca962573fba64dac3904
# sha256: f980d3e162def16d34a42652b2f6c46f256561ae025509d0a236f8c94c9cc7cd 24636f48435ba60013d2e9d8841be701f6c83c78867964659af255b8c81ffd54
# body-sha256: 04342349e2e908a330bf3a4a790ea04f0adc295a46ee5cee1b6a48ad564057d2
# mode: verbatim top-level definitions (transitive closure of the roots in vendor.SPECS)
# ruff: noqa
# ==== end of ybcal header; the vendored source follows unchanged ====
"""Verbatim top-level definitions extracted from the ycash6 test framework (see header)."""

from . import reference as ym
from binascii import hexlify
from binascii import unhexlify
from decimal import Decimal
import hashlib


# ---- from qa/rpc-tests/test_framework/yellowback_util.py

YCASH_CANOPY_BRANCH_ID = 0x19bd2d2f

SIGNING_BRANCH_ID = YCASH_CANOPY_BRANCH_ID

COIN = 10 ** 8

BPS = 10_000

REF_WINDOW = 40

REF_LAG = 2

FEE_MIN = 50_000_000           # enforcement fee floor, zat (0.5 YEC)

FEE_BPS = 25

GRACE = 24

CLASS_RANGES = {'A': (48, 96, 50_000), 'B': (97, 144, 40_000), 'C': (145, 240, 30_000)}

TOKEN_VALUE = 10_000           # zat carried by every YED output

YELLOWBACK_FEE = 1_000         # the network fee floor, zat (distinct from the enforcement fee); the wallet pays wallet_network_fee()

FEE_VOUT_NONE = ym.FEE_VOUT_NONE

PAYLOAD_VERSION_V3 = 3

ATTEST_ARM_MIN = 3

ATTEST_ARM_DELAY = 8

N_SLOTS = 5

M_SELECT = 2

K_SLACK = 1

BUNDLE_MAX = 6

Q_LOW_BPS = 3_333

Q_HIGH_BPS = 6_667

ATTEST_FEE_BPS = 2_500

BOND_MIN_ZAT = 10 * COIN

BOND_MIN_LOCK = 200

BOND_MATURITY = 8

AGE_CAP = 64

FOUNDING_WINDOW = 16

CARRIER_VALUE = 10_000         # wallet policy

REGTEST_PUBKEY_ADDRESS = b'\x1c\x95'

USER = 0

POOLS = [2, 3, 4]

ATTESTOR_A = 6

ATTESTOR_B = 7

_B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'

_SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141

_SECP256K1_P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F

_SECP256K1_G = (0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
                0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8)

def wif_to_secret(wif):
    """Decode a WIF private key to its 32 secret bytes."""
    n = 0
    for c in wif:
        n = n * 58 + _B58.index(c)
    raw = n.to_bytes((n.bit_length() + 7) // 8, 'big')
    raw = b'\x00' * (len(wif) - len(wif.lstrip('1'))) + raw
    body, check = raw[:-4], raw[-4:]
    assert_equal(hashlib.sha256(hashlib.sha256(body).digest()).digest()[:4], check)
    secret = body[1:]                                   # the version byte
    if len(secret) == 33 and secret[-1] == 1:
        secret = secret[:-1]                            # the compressed-key marker
    assert_equal(len(secret), 32)
    return secret

def _ec_add(p, q):
    if p is None:
        return q
    if q is None:
        return p
    if p[0] == q[0] and (p[1] + q[1]) % _SECP256K1_P == 0:
        return None
    if p == q:
        lam = (3 * p[0] * p[0]) * pow(2 * p[1], _SECP256K1_P - 2, _SECP256K1_P) % _SECP256K1_P
    else:
        lam = (q[1] - p[1]) * pow(q[0] - p[0], _SECP256K1_P - 2, _SECP256K1_P) % _SECP256K1_P
    x = (lam * lam - p[0] - q[0]) % _SECP256K1_P
    return (x, (lam * (p[0] - x) - p[1]) % _SECP256K1_P)

def secret_to_pubkey(secret):
    """The 33-byte compressed public key of a secret (pure Python, so address_of() needs no
    libcrypto at node start)."""
    k = int.from_bytes(secret, 'big')
    assert 0 < k < _SECP256K1_N
    r, a = None, _SECP256K1_G
    while k:
        if k & 1:
            r = _ec_add(r, a)
        a = _ec_add(a, a)
        k >>= 1
    return bytes([2 + (r[1] & 1)]) + r[0].to_bytes(32, 'big')

def pubkey_to_address(pubkey, version=REGTEST_PUBKEY_ADDRESS):
    return ym.base58check_encode(version + ym.hash160(pubkey))

def term_class_of(lock_blocks):
    """'A' | 'B' | 'C' for a lockBlocks inside a class, else None (mint-bad-lock)."""
    for name, (lo, hi, _ratio) in CLASS_RANGES.items():
        if lo <= lock_blocks <= hi:
            return name
    return None

def fee_zat(collateral_zat):
    """FEE-1: the enforcement fee for a collateral (max(FEE_MIN, collateral * FEE_BPS / BPS))."""
    return ym.fee_zat(collateral_zat, FEE_MIN, FEE_BPS)

def usd_to_micro(usd):
    return int((Decimal(str(usd)) * 1_000_000).to_integral_value())


# ---- from qa/rpc-tests/test_framework/util.py

def bytes_to_hex_str(byte_str):
    return hexlify(byte_str).decode('ascii')

def hex_str_to_bytes(hex_str):
    return unhexlify(hex_str.encode('ascii'))

def assert_equal(expected, actual, message=""):
    if expected != actual:
        if message:
            message = "; %s" % message
        raise AssertionError("(left == right)%s\n  left: <%s>\n right: <%s>" % (message, str(expected), str(actual)))

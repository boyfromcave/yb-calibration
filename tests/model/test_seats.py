"""Seat ranking, eviction and the D-Q-30 bond minimum: bit for bit against the node (quantum D-Q-29..31)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ybcal.model import kernels as K
from ybcal.model import seats as S

VECTORS = json.loads((Path(S.__file__).parent / S.VECTORS_FILE).read_text())
COIN = S.COIN
PROPS = settings(max_examples=300, deadline=None)


def _params(net: str) -> S.SeatParams:
    return S.MAINNET if net == "main" else S.REGTEST


def test_vectors_come_from_the_pinned_node_commit():
    assert VECTORS["commit"] == S.SOURCE_COMMIT


@pytest.mark.parametrize("net", ["main", "regtest"])
def test_params_equal_the_node_model(net):
    p = _params(net)
    for k, v in VECTORS["params"][net].items():
        assert getattr(p, k) == v, k


def test_log2_q16_node_model_vectors():
    for x, want in VECTORS["log2_q16"]:
        assert S.log2_q16(x) == want, x


def test_log2_q16_cpp_unit_vectors():
    # src/test/yellowback_math_tests.cpp seat_weight_log_tenure_factor
    assert S.log2_q16(0) == 0 and S.log2_q16(1) == 0 and S.log2_q16(2) == 65536
    assert S.log2_q16(1024) == 10 << 16 and S.log2_q16(3) == 103872
    assert S.log2_q16(1 << 40) == 40 << 16 and S.log2_q16(S.UINT64_MAX) == (63 << 16) | 0xFFFF
    assert all(S.log2_q16(x + 1) >= S.log2_q16(x) for x in range(1, 5000))
    with pytest.raises(ValueError):
        S.log2_q16(1 << 64)


def test_seat_age_factor_node_model_vectors():
    for a, u, cap, want in VECTORS["seat_age_factor_q16"]:
        assert S.seat_age_factor_q16(a, u, cap) == want, (a, u, cap)


def test_seat_age_factor_cpp_unit_vectors():
    u, cap = 1152, 420480
    f = S.seat_age_factor_q16
    assert f(0, u, cap) == f(-10, u, cap) == 65536
    assert f(cap, u, cap) == f(3 * cap, u, cap) == 65536 + 58982 == S.FACTOR_MAX_Q16
    assert (f(1152, u, cap), f(8064, u, cap), f(210240, u, cap)) == (72462, 86314, 117619)
    assert f(100, 0, cap) == f(100, u, 0) == 65536
    assert all(f(a + 7, u, cap) >= f(a, u, cap) for a in range(0, 20000, 7))


def test_seat_weight_vectors_and_cpp_claims():
    for b, a, u, cap, want in VECTORS["seat_weight"]:
        assert S.seat_weight(b, a, u, cap) == want
    u, cap = 1152, 420480
    w = S.seat_weight
    assert w(8000 * COIN, 8064) == 8000 * COIN * 86314 and w(8000 * COIN, 8064) // 65536 == 1053637695312
    assert w(20000 * COIN, 0) * 10000 > w(8000 * COIN, cap) * 10500
    assert w(3000 * COIN, 0) * 10000 > w(2000 * COIN, 8064) * 10500
    assert w(3000 * COIN, 0) < w(2000 * COIN, cap)
    assert w(K.MAX_MONEY, cap) > K.INT64_MAX  # arith_uint256 in the C++: never truncated
    assert w(0, 10) == w(-1, 10) == 0


def test_seat_rank_hysteresis():
    p = S.MAINNET
    assert S.seat_rank(5000 * COIN, 0, False) == 5000 * COIN * 65536 * 10000
    assert S.seat_rank(5000 * COIN, 0, True) == 5000 * COIN * 65536 * 10500
    # equal bonds, equal tenure: the seated one keeps the seat
    assert S.seat_rank(5000 * COIN, p.seat_age_cap, True) > S.seat_rank(5000 * COIN, p.seat_age_cap, False)


def test_eviction_victim_node_model_vectors():
    for e in VECTORS["eviction_victim"]:
        p = _params(e["net"])
        members = [S.SeatCandidate(bytes.fromhex(k), b, j, la) for k, b, j, la in e["members"]]
        got = S.eviction_victim(members, e["height"], e["new_bond_zat"], e["liveness_window"], p)
        assert (None if got is None else got.hex()) == e["victim"], e


def test_eviction_victim_node_model_unit_cases():
    # qa/rpc-tests/test_framework/test_yellowback_model.py SeatTests.test_eviction_victim (regtest params,
    # f07a9aef6: the 5-YEC regtest floor)
    p = S.REGTEST
    H, LW, C = 100000, 1000, COIN

    def k(i: int) -> bytes:
        return bytes([2]) + bytes([i]) * 32

    def m(rows):
        return [S.SeatCandidate(*r) for r in rows]

    base = [(k(0), 20 * C, H - 8064, H), (k(1), 8 * C, H - 8064, H), (k(2), 8 * C, H - 10, H)]
    assert S.eviction_victim(m(base), H, 8 * C, LW, p) is None
    assert S.eviction_victim(m(base), H, 9 * C, LW, p) == k(2)
    assert S.eviction_victim(m(base), H, 8 * C * 105 // 100, LW, p) is None
    assert S.eviction_victim(m([(k(0), 6 * C, H - 8064, H)]), H, 9 * C, LW, p) is not None
    assert S.eviction_victim(m([(k(0), 6 * C, H - 420480, H)]), H, 9 * C, LW, p) is None
    assert S.eviction_victim(m([(k(0), 8000 * C, H - 10 * 420480, H)]), H, 20000 * C, LW, p) is not None
    dead = [*base, (k(3), 1000 * C, H - 420480, H - LW - 1)]
    assert S.eviction_victim(m(dead), H, p.bond_min_floor_zat, LW, p) == k(3)
    assert S.eviction_victim(m(dead), H, p.bond_min_floor_zat - 1, LW, p) is None  # B2: no dust eviction
    live = [*base, (k(3), 1000 * C, H - 420480, H - LW)]
    assert S.eviction_victim(m(live), H, p.bond_min_floor_zat, LW, p) is None
    assert S.eviction_victim(m([(k(6), 8 * C, H - 50, H), (k(5), 8 * C, H - 50, H)]), H, 100 * C, LW, p) == k(
        5
    )
    assert S.eviction_victim([], H, 100 * C, LW, p) is None


def test_bond_min_vectors_and_bounds():
    for net, c, want in VECTORS["bond_min_from_collateral_zat"]:
        assert S.bond_min_from_collateral_zat(c, _params(net)) == want, (net, c)
    p = S.MAINNET
    assert S.bond_min_from_collateral_zat(0) == 10_000 * COIN
    assert S.bond_min_from_collateral_zat(K.MAX_MONEY) == 20_000 * COIN
    lo, hi = S.bond_neutral_collateral_zat(p)
    assert S.bond_min_from_collateral_zat(lo - 1) == p.bond_min_floor_zat < S.bond_min_from_collateral_zat(lo)
    assert S.bond_min_from_collateral_zat(hi - 1) < p.bond_min_cap_zat == S.bond_min_from_collateral_zat(hi)
    assert (
        lo == 1_800_000 * COIN + 1 and hi == 3_600_000 * COIN - 179
    )  # ceil: the cap binds just below 3.6 M YEC
    assert S.bond_return_height(1000) == 1000 + 16_128
    zero_slots = S.SeatParams(n_slots=0)
    assert S.bond_min_from_collateral_zat(1, zero_slots) == zero_slots.bond_min_cap_zat


@PROPS
@given(st.integers(1, S.UINT64_MAX))
def test_log2_q16_integer_part(x):
    assert S.log2_q16(x) >> 16 == x.bit_length() - 1


def test_log2_q16_within_one_of_exact_floor():
    # deterministic, not exact (math.h:249-255): floor(log2(x) * 2^16) or one below it; the exact floor
    # is bit_length(x^65536) - 1
    below = 0
    for x in range(1, 300):
        exact = pow(x, 65536).bit_length() - 1
        got = S.log2_q16(x)
        assert got in (exact, exact - 1), x
        below += got != exact
    assert below < 20


@PROPS
@given(st.integers(-(10**6), 2 * 420480), st.integers(1, 10**6), st.integers(1, 10**7))
def test_factor_range_and_monotone(a, u, cap):
    f = S.seat_age_factor_q16
    assert 65536 <= f(a, u, cap) <= S.FACTOR_MAX_Q16
    assert f(a + 1, u, cap) >= f(a, u, cap)


def test_check_vectors_rows_all_pass():
    rows = S.check_vectors()
    assert len(rows) == 6 and all(ok for _, ok, _ in rows), rows

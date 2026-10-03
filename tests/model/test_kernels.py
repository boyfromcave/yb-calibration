"""Scalar kernels: equal to the reference model where both exist; C++ semantics elsewhere (WP-1)."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from ybcal.model import kernels as K
from ybcal.model import reference as ref
from ybcal.model import reference_attest as ref_attest

PRICE = st.integers(100, 100_000_000)
OPT_PRICE = st.one_of(st.none(), PRICE)
MONEY = st.integers(0, K.MAX_MONEY)
#: per-test settings (no global profile: other packages' hypothesis tests keep their own)
PROPS = settings(max_examples=200, deadline=None)


@PROPS
@given(st.lists(st.integers(-(10**12), 10**12), max_size=60))
def test_lower_median_equals_reference_and_units(values):
    from ybcal.units import lower_median as units_lm

    assert K.lower_median(values) == ref.lower_median(values) == units_lm(values)


@PROPS
@given(st.lists(PRICE, max_size=40), st.integers(0, 45))
def test_window_median_fill(prices, fill):
    got = K.window_median_with_fill(prices, fill)
    if len(prices) < fill or not prices:
        assert got is None
    else:
        assert got == sorted(prices)[(len(prices) - 1) // 2]


@PROPS
@given(
    st.lists(PRICE, min_size=2, max_size=44),
    st.integers(1, 100_000),
    st.integers(0, 10_000),
    st.integers(10_000, 100_000),
)
def test_sigma_equals_reference_on_its_domain(samples, sigma_ref, ppy, max_bps):
    assert K.sigma_mult_bps(samples, sigma_ref, ppy, max_bps) == ref.sigma_mult_bps(
        samples, sigma_ref, ppy, max_bps
    )


@PROPS
@given(st.lists(OPT_PRICE, max_size=44), st.integers(-5, 100_000), st.integers(-5, 30_000))
def test_sigma_cpp_guards(samples, sigma_ref, max_bps):
    got = K.sigma_mult_bps(samples, sigma_ref, 8_760, max_bps)
    if sigma_ref <= 0:
        assert got == 10_000
    elif len(samples) < 2 or None in samples:
        assert got == max(max_bps, 10_000)
    else:
        assert 10_000 <= got <= max(max_bps, 10_000)


@PROPS
@given(
    st.lists(OPT_PRICE, min_size=1, max_size=200),
    st.integers(0, 199),
    st.integers(1, 100),
    st.integers(1, 20),
)
def test_sigma_samples(series, index, window, step):
    index = min(index, len(series) - 1)
    s = K.sigma_samples(series, index, window, step)
    assert len(s) == window // step + 1
    for k, v in enumerate(s):
        j = index - k * step
        assert v == (series[j] if j >= 0 else None)


@PROPS
@given(st.integers(-10, 10**7), st.integers(-10, 200_000), OPT_PRICE)
def test_required_zat(cents, ratio, p):
    got = K.required_zat(cents, ratio, p)
    if cents <= 0 or ratio <= 0 or p is None:
        assert got is None
        return
    assert got == ref.required_zat(cents, ratio, p)
    exact = -((-cents * ratio * K.COIN) // p)
    assert got == (exact if exact <= K.MAX_MONEY else None)
    r = K.required_zat_rounded(cents, ratio, p)
    if got is not None and r is not None:
        assert r % 1000 == 0 and 0 <= r - got < 1000


@PROPS
@given(st.integers(-10, 10**7), st.integers(-10, 20_000), OPT_PRICE)
def test_claimant_max_and_residual(cents, margin, p):
    got = K.claimant_max_zat(cents, margin, p)
    if cents <= 0 or margin <= 0 or p is None:
        assert got is None
    else:
        assert got == ref.claimant_max_zat(cents, margin, p)
    c = cents * 1000
    assert K.residual_zat(c, got) == ref.residual_zat(c, got) == (max(0, c - got) if got is not None else 0)


@PROPS
@given(st.integers(-5, K.MAX_MONEY), OPT_PRICE, st.integers(-5, 10**9))
def test_global_ratio_and_cap(coll, p, supply):
    gr = K.global_ratio_bps(coll, p, supply)
    if supply <= 0 or p is None or coll < 0:
        assert gr is None
    else:
        assert gr == ref.global_ratio_bps(coll, p, supply) == coll * p // (K.COIN * supply)
    cap = K.cap_cents(coll, p)
    assert cap == (None if p is None or coll < 0 else ref.cap_cents(coll, p))
    for bps in (0, 1, 1_500, 10_000):
        sc = K.supply_cap_cents(coll, p, bps)
        assert sc == (None if bps == 0 or cap is None else ref.supply_cap_cents(coll, p, bps))


@PROPS
@given(st.integers(-5, K.MAX_MONEY), OPT_PRICE, st.integers(-5, 10**7), st.integers(-5, 20_000))
def test_is_underwater(coll, p, minted, thr):
    got = K.is_underwater(coll, p, minted, thr)
    if p is None or minted <= 0 or thr <= 0:
        assert got is False
    else:
        assert got == ref.is_underwater(max(coll, 0), p, minted, thr)


@PROPS
@given(MONEY, st.integers(1, 10**7), st.integers(1, 20_000))
def test_underwater_price_is_the_boundary(coll, minted, thr):
    p = K.underwater_price(coll, minted, thr)
    if p is None:
        assert not K.is_underwater(coll, 1, minted, thr)
    elif coll > 0:
        assert K.is_underwater(coll, p, minted, thr) and not K.is_underwater(coll, p + 1, minted, thr)


@PROPS
@given(st.integers(-5, K.MAX_MONEY), st.integers(0, 100_000_000), st.integers(-5, 10_000))
def test_fees(coll, fee_min, bps):
    f = K.fee_zat(coll, fee_min, bps)
    assert f == ref.fee_zat(max(coll, 0), fee_min, max(bps, 0))
    for abps in (-1, 0, 1, 2_500, 10_000):
        assert K.attest_fee_zat(f, abps) == ref.attest_fee_zat(f, abps)


@PROPS
@given(st.integers(-5, K.MAX_MONEY), st.integers(-5, 10**9), st.integers(-5, 300_000))
def test_bond_weight(bond, age, cap):
    w = K.bond_weight(bond, age, cap)
    if bond <= 0 or age <= 0 or cap <= 0:
        assert w == 0
    else:
        assert w == ref_attest.bond_weight(bond, age, cap) == bond * min(age, cap)


ENTRIES = st.lists(st.tuples(st.integers(1, 1000), st.integers(0, 10**19)), max_size=10)


@PROPS
@given(ENTRIES, st.integers(-100, 10_100))
def test_weighted_quantile_reference_and_cpp_edges(entries, q):
    got = K.weighted_quantile(entries, q)
    total = sum(w for _p, w in entries)
    if not entries:
        assert got is None
    elif q > 10_000 and total > 0:
        assert got is None  # C++: threshold > total
    elif total == 0:
        assert got == min(p for p, _w in entries)  # C++: threshold 0 → first price
    else:
        # reference sorts ties by (price, weight, seq); the answer is a price, so ties agree
        assert got == ref_attest.weighted_quantile(entries, max(q, 0))


@PROPS
@given(
    st.lists(st.tuples(st.integers(1, 1000), st.integers(1, 10**18)), min_size=0, max_size=8),
    st.integers(1, 6),
)
def test_bundle_stat(entries, m):
    got = K.bundle_stat(entries, 3_333, 6_667, m)
    want = ref_attest.bundle_stat([((i, p), w) for i, (p, w) in enumerate(entries)], 3_333, 6_667, m)
    assert got == want


@PROPS
@given(OPT_PRICE, OPT_PRICE, OPT_PRICE, OPT_PRICE)
def test_price_combine_and_medians(a, b, c, d):
    pc = K.price_combine(a, b, c, d)
    assert pc.p_mint == (min(a, c) if a is not None and c is not None else None)
    assert pc.p_claim == (max(b, d) if b is not None and d is not None else None)
    assert pc.p_emerg == (min(b, d) if b is not None and d is not None else None)
    assert K.price_mint(a, b, c) == (min(a, b, c) if None not in (a, b, c) else None)
    assert K.price_claim(b, c) == (max(b, c) if None not in (b, c) else None)


def test_halt3_fires_only_on_a_fall():
    assert K.halt3_divergence(79_999, 100_000, 100_000, 2_000)
    assert not K.halt3_divergence(80_000, 100_000, 100_000, 2_000)
    assert K.halt3_divergence(100_000, 79_999, 100_000, 2_000)
    assert not K.halt3_divergence(200_000, 150_000, 100_000, 2_000)  # rally
    assert not K.halt3_divergence(None, 100_000, 100_000, 2_000)


def test_select_attestors_delegates_and_is_deterministic():
    bh = "11" * 32
    sel = K.outpoint_selector("22" * 32, 0)
    pool = [(0, 5), (1, 7), (2, 0), (3, 11), (4, 2)]
    a = K.select_attestors(bh, sel, pool, 4, 2)
    assert a == ref_attest.select_attestors(bh, sel, pool, 4, 2)
    assert len(a) == 5 and len(set(a)) == 5 and a == K.select_attestors(bh, sel, list(reversed(pool)), 4, 2)
    assert K.select_attestors(bh, sel, [(3, 0), (1, 0)], 1, 1) == [1, 3]  # all-zero pool: ascending seq


def test_reg4_judgement():
    peers = [100_000, 101_000, 99_000, 100_500, 99_500]
    assert K.reg4_judgement(100_000, peers, 5, 1_000, 300) == (True, True, False)
    # dev = floor(|q - m| * 1e4 / m): 103,009 -> 300 (band edge, in), 110,000..110,009 -> 1000 (not > 1000)
    assert K.reg4_judgement(103_009, peers, 5, 1_000, 300) == (True, True, False)
    assert K.reg4_judgement(103_010, peers, 5, 1_000, 300) == (True, False, False)
    assert K.reg4_judgement(110_010, peers, 5, 1_000, 300) == (True, False, True)
    assert K.reg4_judgement(110_009, peers, 5, 1_000, 300) == (True, False, False)
    assert K.reg4_judgement(100_000, peers[:4], 5, 1_000, 300) == (False, False, False)
    assert list(K.reg4_peer_heights(50, 10)) == list(range(40, 60))


def test_activation_and_hysteresis_steps():
    s = K.ActivationState(K.SIGNALING, 0, 0)
    s = K.activation_step(s, 62, 64, 1, 64, 48, 64)  # window not yet full (H < start + 63)
    assert s.status == K.SIGNALING
    s = K.activation_step(s, 64, 48, 1, 64, 48, 64)
    assert s == (K.LOCKED_IN, 64, 128)
    assert K.activation_step(s, 127, 0, 1, 64, 48, 64).status == K.LOCKED_IN
    assert K.activation_step(s, 128, 0, 1, 64, 48, 64) == (K.ACTIVE, 64, 128)
    assert K.activation_step(K.ActivationState(K.SIGNALING, 0, 0), 64, 48, 1, 64, 48, 0).status == K.ACTIVE
    # ACT-4: set below the floor, held until the threshold is met again
    assert K.participation_halt_step(False, 1209, True, 1512, 1210)
    assert not K.participation_halt_step(False, 1210, True, 1512, 1210)
    assert K.participation_halt_step(True, 1511, True, 1512, 1210)
    assert not K.participation_halt_step(True, 1512, True, 1512, 1210)
    assert not K.participation_halt_step(False, 0, False, 1512, 1210)  # not ACTIVE: never set
    # ACT-6
    assert K.enforcement_halt_step(False, 1007, True, 1210, 1008)
    assert K.enforcement_halt_step(True, 1209, True, 1210, 1008)
    assert not K.enforcement_halt_step(True, 1210, True, 1210, 1008)
    assert K.signal_count([True, False, True, True], 3, 2) == 2
    assert K.signal_count([True, False, True, True], 1, 64) == 1


def test_pin_kernels():
    assert not K.pin1_triggered([50_000], 2, 500)  # too few rows
    assert not K.pin1_triggered([50_000, 52_500], 2, 500)  # exactly 5 %: not >
    assert K.pin1_triggered([50_000, 52_501], 2, 500)
    assert K.pin1_triggered([None, 0, 50_000, 52_501], 2, 500)  # undefined rows count, never a value
    assert not K.pin1_triggered([None, 0], 2, 500)
    q = [
        ("k1", 50_000),
        ("k1", 50_000),
        ("k1", 50_000),
        ("k2", 50_000),
        ("k2", 50_001),
        ("k2", 50_000),
        ("k3", 49_000),
        ("k3", 49_000),
    ]
    assert K.pin1_pinned_keys(q, 3) == ["k1"]
    assert K.pin1_pinned_keys(q, 2) == ["k1", "k3"]
    assert K.pin2_triggered(52_501, 50_000, 500) and K.pin2_triggered(50_000, 52_501, 500)
    assert not K.pin2_triggered(52_500, 50_000, 500) and not K.pin2_triggered(None, 50_000, 500)
    rows = [
        ([1, 2], [50_000, 60_000], [10, 10]),
        ([1, 2], [50_000, 61_000], [11, 11]),
        ([1, 3], [50_000, 70_000], [12, 12]),
        ([3], [70_000], [12]),
    ]
    assert K.pin2_pinned_seqs(rows, 3) == [1]
    assert K.pin2_pinned_seqs(rows, 2) == [1, 3] or K.pin2_pinned_seqs(rows, 2) == [1]
    # seq 3: one price in two rows but a single cited height (12): a reused attestation cannot pin
    assert 3 not in K.pin2_pinned_seqs(rows, 2)


def test_min_fills_match_reference_params():
    for w in (8, 24, 64, 96, 576, 2016, 1, 7):
        p = ref.Params.regtest(1)
        p.p_fast_window = p.p_mid_window = p.p_slow_window = w
        assert (K.min_fill_fast(w), K.min_fill_slow(w), K.min_fill_slow(w)) == (
            p.min_fill_fast,
            p.min_fill_mid,
            p.min_fill_slow,
        )


def test_param_set_start_admissible_edge():
    assert not K.param_set_start_admissible(10, 0, 0, lambda h: True)

"""Every vectorised kernel equals the scalar kernel, element for element (PLAN §8)."""

from __future__ import annotations

import time

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ybcal.model import kernels as K
from ybcal.model import vkernels as V

#: per-test settings (no global profile: other packages' hypothesis tests keep their own)
PROPS = settings(max_examples=200, deadline=None)

PRICE = st.integers(100, 100_000_000)
OPT_PRICE = st.one_of(st.none(), PRICE)
# the series strategy: gaps (None), boundary prices, runs of equal values
SERIES = st.lists(
    st.one_of(st.none(), PRICE, st.sampled_from([100, 100_000_000, 50_000])), min_size=1, max_size=160
)


def opt(v) -> int | None:
    v = int(v)
    return None if v <= 0 else v


@PROPS
@given(SERIES, st.integers(1, 200), st.integers(0, 120))
def test_rolling_lower_median(series, window, fill):
    got = V.rolling_lower_median(V.to_array(series), window, fill)
    for i in range(len(series)):
        want = K.window_median_with_fill(
            [x for x in series[max(0, i - window + 1) : i + 1] if x is not None], fill
        )
        assert opt(got[i]) == want, (i, window, fill)


@PROPS
@given(st.lists(SERIES, min_size=1, max_size=5), st.integers(1, 60), st.integers(0, 40), st.integers(1, 400))
def test_rolling_median_multi_path_and_batching(paths, window, fill, batch):
    n = max(len(p) for p in paths)
    arr = np.full((len(paths), n), V.UNDEF, dtype=np.int64)
    for r, p in enumerate(paths):
        arr[r, : len(p)] = V.to_array(p)
    rm = V.RollingMedian(arr, batch_elems=batch)
    got = rm.median(window, fill)
    for r in range(len(paths)):
        assert np.array_equal(got[r], V.rolling_lower_median(arr[r], window, fill))
    # an explicit valid mask equals the sentinel encoding
    mask = arr > 0
    assert np.array_equal(V.RollingMedian(np.where(mask, arr, 7), valid=mask).median(window, fill), got)


@PROPS
@given(SERIES)
def test_price_medians_three_windows(series):
    arr = V.to_array(series)
    f, m, s = V.price_medians(arr, (8, 24, 64), (4, 16, 43))
    for i in range(len(series)):
        w = [[x for x in series[max(0, i - W + 1) : i + 1] if x is not None] for W in (8, 24, 64)]
        assert (opt(f[i]), opt(m[i]), opt(s[i])) == (
            K.window_median_with_fill(w[0], 4),
            K.window_median_with_fill(w[1], 16),
            K.window_median_with_fill(w[2], 43),
        )


@PROPS
@given(st.lists(st.tuples(OPT_PRICE, OPT_PRICE, OPT_PRICE), min_size=1, max_size=50), st.integers(0, 10_000))
def test_price_mint_claim_halt3(rows, div):
    f, m, s = (V.to_array(c) for c in zip(*rows, strict=True))
    pm, pc, h3 = V.price_mint(f, m, s), V.price_claim(m, s), V.halt3_divergence(f, m, s, div)
    for (a, b, c), x, y, z in zip(rows, pm, pc, h3, strict=True):
        assert opt(x) == K.price_mint(a, b, c)
        assert opt(y) == K.price_claim(b, c)
        assert bool(z) == K.halt3_divergence(a, b, c, div)


@PROPS
@given(st.lists(st.tuples(OPT_PRICE, OPT_PRICE, OPT_PRICE, OPT_PRICE), min_size=1, max_size=50))
def test_price_combine(rows):
    cols = [V.to_array(c) for c in zip(*rows, strict=True)]
    a, b, c = V.price_combine(*cols)
    for r, x, y, z in zip(rows, a, b, c, strict=True):
        assert (opt(x), opt(y), opt(z)) == tuple(K.price_combine(*r))


SIGMA_SERIES = st.lists(
    st.one_of(st.integers(40_000, 60_000), st.none(), st.sampled_from([100, 100_000_000])),
    min_size=1,
    max_size=150,
)


@PROPS
@given(
    SIGMA_SERIES,
    st.integers(1, 40),
    st.integers(1, 10),
    st.integers(-1, 50_000),
    st.integers(0, 10_000),
    st.integers(0, 40_000),
)
def test_sigma_series(pf, window, step, sigma_ref, ppy, max_bps):
    got = V.sigma_mult_series(V.to_array(pf), window, step, sigma_ref, ppy, max_bps)
    for i in range(len(pf)):
        assert int(got[i]) == K.sigma_mult_bps(
            K.sigma_samples(pf, i, window, step), sigma_ref, ppy, max_bps
        ), i


def test_sigma_series_overflow_fallback():
    # alternating PRICE_MIN / PRICE_MAX: returns of 1e10 bps, past the int64-safe bound
    pf = [100 if i % 2 else 100_000_000 for i in range(200)]
    got = V.sigma_mult_series(V.to_array(pf), 64, 1, 10**9, 8_760, 10**9)
    for i in range(len(pf)):
        assert int(got[i]) == K.sigma_mult_bps(K.sigma_samples(pf, i, 64, 1), 10**9, 8_760, 10**9)
    assert int(got[-1]) > 30_000


CENTS = st.one_of(
    st.integers(-5, 10**7), st.sampled_from([0, 1, 10_000, 1_000_000, 10_000_000, 210_000_000_001])
)
RATIO = st.one_of(st.integers(-5, 200_000), st.sampled_from([0, 10_000, 150_000, 11_000]))
MONEY = st.one_of(st.integers(-5, K.MAX_MONEY), st.sampled_from([0, K.MAX_MONEY, 600_000_000_000]))
BIGP = st.one_of(OPT_PRICE, st.integers(10**11, 10**15))  # beyond PRICE_MAX: exercises the scalar fallback


@PROPS
@given(st.lists(st.tuples(CENTS, RATIO, BIGP), min_size=1, max_size=40))
def test_required_and_claimant(rows):
    c, r, p = (V.to_array(x) for x in zip(*rows, strict=True))
    req, cm = V.required_zat(c, r, p), V.claimant_max_zat(c, r, p)
    for (a, b, q), x, y in zip(rows, req, cm, strict=True):
        assert opt(x) == K.required_zat(a, b, q)
        assert opt(y) == K.claimant_max_zat(a, b, q)
    res = V.residual_zat(np.full(len(rows), 600_000_000_000), cm)
    for y, z in zip(cm, res, strict=True):
        assert int(z) == K.residual_zat(600_000_000_000, opt(y) if int(y) >= 0 else None)


@PROPS
@given(st.lists(st.tuples(MONEY, BIGP, CENTS, RATIO), min_size=1, max_size=40))
def test_is_underwater(rows):
    got = V.is_underwater(*(V.to_array(x) for x in zip(*rows, strict=True)))
    for r, g in zip(rows, got, strict=True):
        assert bool(g) == K.is_underwater(*r), r


@PROPS
@given(
    st.lists(st.tuples(MONEY, BIGP, st.integers(-5, 10**12)), min_size=1, max_size=40),
    st.sampled_from([0, 1, 1_500, 10_000, 25_000]),
)
def test_global_ratio_and_caps(rows, cap_bps):
    a, p, s = (V.to_array(x) for x in zip(*rows, strict=True))
    gr, cc, sc = V.global_ratio_bps(a, p, s), V.cap_cents(a, p), V.supply_cap_cents(a, p, cap_bps)
    h2 = V.halt2_global_ratio(a, p, s, 25_000)
    for (x, y, z), g, c, k, h in zip(rows, gr, cc, sc, h2, strict=True):
        assert (None if g < 0 else int(g)) == K.global_ratio_bps(x, y, z)
        assert (None if c < 0 else int(c)) == K.cap_cents(x, y)
        assert (None if k < 0 else int(k)) == K.supply_cap_cents(x, y, cap_bps)
        assert bool(h) == K.halt2_global_ratio(x, y, z, 25_000)


@PROPS
@given(
    st.lists(MONEY, min_size=1, max_size=40),
    st.integers(0, 100_000_000),
    st.integers(-5, 10_000),
    st.integers(-5, 10_000),
)
def test_fees(coll, fee_min, bps, abps):
    f = V.fee_zat(np.array(coll, dtype=np.int64), fee_min, bps)
    af = V.attest_fee_zat(f, abps)
    for c, x, y in zip(coll, f, af, strict=True):
        assert int(x) == K.fee_zat(c, fee_min, bps)
        assert int(y) == K.attest_fee_zat(int(x), abps)


@PROPS
@given(st.lists(st.integers(-5, 60_000), min_size=1, max_size=40), st.integers(-5, 40_000))
def test_min_ratio(base, sigma):
    got = V.min_ratio_bps(np.array(base, dtype=np.int64), sigma)
    assert [int(x) for x in got] == [K.min_ratio_bps(b, sigma) for b in base]


@PROPS
@given(st.lists(st.booleans(), min_size=1, max_size=200), st.integers(1, 80))
def test_signal_counts(signals, window):
    got = V.signal_counts(signals, window)
    assert [int(x) for x in got] == [K.signal_count(signals, i, window) for i in range(len(signals))]


@PROPS
@given(
    st.lists(st.tuples(st.integers(0, 80), st.booleans()), min_size=1, max_size=200),
    st.booleans(),
    st.integers(0, 80),
    st.integers(0, 80),
)
def test_halt_hysteresis_series(rows, initial, thr, floor):
    counts = np.array([c for c, _a in rows], dtype=np.int64)
    active = np.array([a for _c, a in rows])
    part = V.participation_halt_series(counts, active, thr, floor, initial)
    enf = V.enforcement_halt_series(counts, active, thr, floor, initial)
    sp = se = initial
    for (c, a), x, y in zip(rows, part, enf, strict=True):
        sp = K.participation_halt_step(sp, c, a, thr, floor)
        se = K.enforcement_halt_step(se, c, a, thr, floor)
        assert (bool(x), bool(y)) == (sp, se)


@pytest.mark.slow
def test_rolling_median_speed_one_path_100k_w2016():
    rng = np.random.default_rng(0)
    p = rng.integers(40_000, 60_000, 100_000)
    p[rng.random(p.size) < 0.3] = V.UNDEF
    t = time.perf_counter()
    V.rolling_lower_median(p, 2016, 1344)
    assert time.perf_counter() - t < 5.0   # target 2 s; measured ~0.1-0.2 s (docs/architecture.md)

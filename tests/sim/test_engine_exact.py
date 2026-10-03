"""Block mode == the vendored reference model (``YellowbackModel.feed_block``), height by height.

Streams are regtest scale (windows 8/24/64, signal window 64, volStep 8); every tag is a real coinbase
scriptSig fed through ``find_tag``, so TAG-2 validity is exercised too.
"""

from __future__ import annotations

import numpy as np
import pytest

from ybcal.model import reference as R
from ybcal.params.paramset import regtest
from ybcal.sim import engine as E
from ybcal.sim import supply as SUP

KEYS = [bytes([k + 1]) * 20 for k in range(8)]


def ref_params(ps):
    return R.Params.regtest(
        start_height=int(ps["startHeight"]),
        sigma_ref_bps=int(ps["sigmaRefBps"]),
        supply_cap_bps=int(ps["supplyCapBps"]),
    )


def run_reference(ps, inputs: E.BlockInputs, path: int = 0, bundle_rows: dict | None = None):
    model = R.YellowbackModel(ref_params(ps))
    if bundle_rows:
        for h, a in bundle_rows.items():
            row = R.BundleLogRecord()
            row.a_mint = a
            model.bundle_log[h] = row
    out = []
    for j in range(inputs.n_blocks):
        h = inputs.start_height + j
        cb = R.height_prefix(h)
        # the raw (pre-normalisation) stream lives in meta so invalid tags reach find_tag
        raw = inputs.meta["raw"]
        if raw["present"][path, j]:
            flags = 1 if raw["signal"][path, j] else 0
            cb += R.tag_push(flags, int(raw["price"][path, j]), 0, KEYS[int(raw["pool"][path, j])])
        model.feed_block(h, f"{h:064x}", cb.hex(), R.regtest_subsidy(h), [])
        s = model.snapshot(h)
        out.append(s)
    return out


def make_inputs(ps, present, price, pool, signal, attest=None):
    raw = {
        "present": np.atleast_2d(present),
        "price": np.atleast_2d(price),
        "pool": np.atleast_2d(pool),
        "signal": np.atleast_2d(signal),
    }
    inp = E.BlockInputs(
        true_price=np.maximum(raw["price"], 1),
        tag_present=raw["present"],
        tag_price=raw["price"],
        tag_pool=raw["pool"],
        signal_bit=raw["signal"],
        start_height=int(ps["startHeight"]),
        attest=attest,
        meta={"raw": raw},
    )
    return inp


def assert_equal(ps, inputs, series, path=0, bundle_rows=None):
    ref = run_reference(ps, inputs, path, bundle_rows)
    for j, s in enumerate(ref):
        got = series.snapshot(path, j)
        want = {
            "height": inputs.start_height + j,
            "p_fast": s.p_fast,
            "p_mid": s.p_mid,
            "p_slow": s.p_slow,
            "p_mint": s.p_mint,
            "p_claim": s.p_claim,
            "sigma_mult_bps": s.sigma_mult_bps,
            "halt_mask": s.halt_mask,
            "activation": s.activation.status,
            "signal_count": s.signal_count,
            "issued_zat": s.issued_zat,
            "global_ratio_bps": s.global_ratio_bps,
        }
        assert got == want, f"height {inputs.start_height + j}: {got} != {want}"
        pinned_ref = sorted(KEYS.index(k) for k in s.pinned_keys)
        bits = int(series.pinned_pools[path, j])
        assert [k for k in range(8) if bits >> k & 1] == pinned_ref, (
            f"PIN-1 keys at {inputs.start_height + j}"
        )
    return ref


def random_stream(
    rng,
    n,
    *,
    tag_rate=0.8,
    quote_rate=0.85,
    signal_rate=0.9,
    n_pools=4,
    price0=2_000_000,
    vol=0.02,
    outages=(),
    crash=None,
    invalid_rate=0.0,
):
    p = price0 * np.exp(np.cumsum(rng.normal(0, vol, n)))
    if crash is not None:
        a, b, f = crash
        p[a:] *= np.concatenate([np.linspace(1, f, b - a), np.full(n - b, f)])
    present = rng.random(n) < tag_rate
    quote = rng.random(n) < quote_rate
    price = np.where(quote, np.rint(p * (1 + rng.normal(0, 0.003, n))), 0).astype(np.int64)
    for a, b in outages:
        present[a:b] = False
    if invalid_rate:
        bad = rng.random(n) < invalid_rate
        price = np.where(bad, 50, price)  # below PRICE_MIN: TAG-2 drops the whole tag
    pool = rng.integers(0, n_pools, n)
    signal = rng.random(n) < signal_rate
    return present, price, pool, signal


@pytest.mark.parametrize("seed", range(6))
def test_random_streams_match_reference(seed):
    rng = np.random.default_rng(seed)
    ps = regtest().replace(startHeight=5 + seed, sigmaRefBps=8_000 + 1_000 * seed)
    n = 420
    present, price, pool, signal = random_stream(
        rng,
        n,
        tag_rate=[0.95, 0.7, 0.55, 0.85, 0.9, 0.75][seed],
        signal_rate=[0.95, 0.8, 0.7, 0.9, 0.6, 0.85][seed],
        outages=[(150, 150 + 12 * seed)],
        crash=(250, 262, 0.55) if seed % 2 else None,
        invalid_rate=0.02 if seed == 3 else 0.0,
    )
    inp = make_inputs(ps, present, price, pool, signal)
    series = E.simulate_blocks(ps, inp, activation_mode="internal")
    assert series.activation_source == "internal"
    assert_equal(ps, inp, series)


def test_fill_boundaries_exact():
    """Tag counts sitting exactly at and one below each min-fill (4 / 16 / 43)."""
    ps = regtest().replace(sigmaRefBps=10_000)
    n = 300
    rng = np.random.default_rng(11)
    present = np.zeros(n, bool)
    # a periodic pattern whose density puts the 64-window count right around 43
    for i in range(n):
        present[i] = (i % 3) != 0 or (i // 64) % 2 == 0
    present[200:204] = False
    price = np.rint(1_000_000 * (1 + rng.normal(0, 0.01, n))).astype(np.int64)
    inp = make_inputs(ps, present, price, rng.integers(0, 3, n), np.ones(n, bool))
    series = E.simulate_blocks(ps, inp, activation_mode="internal")
    ref = assert_equal(ps, inp, series)
    # both sides of the slow fill actually occur
    assert any(s.p_slow is None for s in ref[64:]) and any(s.p_slow is not None for s in ref[64:])


def test_outage_no_price_sigma_cap_and_divergence():
    ps = regtest().replace(sigmaRefBps=20_000, startHeight=3)
    n = 400
    rng = np.random.default_rng(5)
    present, price, pool, signal = random_stream(
        rng,
        n,
        tag_rate=1.0,
        quote_rate=1.0,
        signal_rate=1.0,
        outages=[(180, 200)],
        crash=(300, 306, 0.5),
        vol=0.001,
    )
    inp = make_inputs(ps, present, price, pool, signal)
    series = E.simulate_blocks(ps, inp, activation_mode="internal")
    ref = assert_equal(ps, inp, series)
    masks = np.array([s.halt_mask for s in ref])
    assert (masks & E.HALT_NO_PRICE)[190:205].any()
    assert (masks & E.HALT_DIVERGENCE).any()
    sig = np.array([s.sigma_mult_bps for s in ref])
    assert (sig[200:230] == 30_000).all()  # K12: the gap pins the cap
    assert (sig[100:170] < 30_000).any()


def test_activation_participation_enforcement_exact():
    ps = regtest().replace(sigmaRefBps=10_000)
    n = 520
    rng = np.random.default_rng(9)
    sig_rate = np.concatenate([np.full(200, 0.95), np.full(120, 0.45), np.full(200, 0.95)])
    present = np.ones(n, bool)
    signal = rng.random(n) < sig_rate
    price = np.rint(1_500_000 * (1 + rng.normal(0, 0.005, n))).astype(np.int64)
    inp = make_inputs(ps, present, price, rng.integers(0, 4, n), signal)
    series = E.simulate_blocks(ps, inp, activation_mode="internal")
    ref = assert_equal(ps, inp, series)
    masks = np.array([s.halt_mask for s in ref])
    assert (masks & E.HALT_PARTICIPATION).any() and (masks & E.HALT_ENFORCEMENT).any()


def test_pin1_masked_medians_exact():
    """PIN-1 armed through preloaded BundleLog rows; a stale pool (constant quote) gets pinned and its
    quotes leave all three medians at those heights (scalar recompute path)."""
    ps = regtest().replace(sigmaRefBps=10_000)
    n = 240
    rng = np.random.default_rng(3)
    pool = rng.integers(0, 4, n)
    price = np.rint(2_000_000 * (1 + rng.normal(0, 0.01, n))).astype(np.int64)
    price[pool == 2] = 1_234_567  # pool 2 is stuck
    present = np.ones(n, bool)
    signal = np.ones(n, bool)
    start = int(ps["startHeight"])
    # BundleLog rows with an aMint range > pinDelta (5 %) around heights 100..180
    bundle_present = np.zeros(n, bool)
    bundle_a = np.zeros(n, np.int64)
    rows = {}
    for j in range(90, 180, 3):
        bundle_present[j] = True
        a = 2_000_000 if (j // 3) % 2 else 2_200_000
        if j == 120:
            a = 0  # a row whose statistic is undefined
        bundle_a[j] = a
        rows[start + j] = a if a > 0 else None
    inp = make_inputs(
        ps, present, price, pool, signal, attest={"bundle_present": bundle_present, "bundle_a_mint": bundle_a}
    )
    series = E.simulate_blocks(ps, inp, activation_mode="internal", attest_mode="unarmed")
    assert series.pinned.any() and series.pinned_recomputed > 0
    assert_equal(ps, inp, series, bundle_rows=rows)


def test_issued_zat_is_regtest_subsidy_since_start():
    n = 700
    iz = SUP.issued_zat_series(7, n, SUP.REGTEST)
    want = np.cumsum([R.regtest_subsidy(h) for h in range(7, 7 + n)])
    assert (iz == want).all()


def test_halt2_from_vaults_hook_matches_kernel():
    from ybcal.model import kernels as K

    ps = regtest().replace(sigmaRefBps=10_000)
    n = 200
    rng = np.random.default_rng(1)
    price = np.rint(1_000_000 * (1 + rng.normal(0, 0.01, n))).astype(np.int64)
    inp = make_inputs(ps, np.ones(n, bool), price, np.zeros(n, np.int16), np.ones(n, bool))
    supply = np.linspace(0, 50_000, n).astype(np.int64)[None, :]
    coll = np.full((1, n), 3 * 10**8, dtype=np.int64)

    def vaults(stage, params, inputs, series):
        if stage == "vaults":
            series.supply_cents = supply.copy()
            series.collateral_zat = coll.copy()

    s = E.simulate_blocks(ps, inp, hooks=[vaults], activation_mode="always_active")
    for j in range(n):
        xm = int(s.x_mint[0, j])
        xm = xm if xm > 0 else None
        want = K.halt2_global_ratio(int(coll[0, j]), xm, int(supply[0, j]), int(ps["globalRatioHaltBps"]))
        assert bool(s.halt_mask[0, j] & E.HALT_GLOBAL_RATIO) == want
        g = K.global_ratio_bps(int(coll[0, j]), xm, int(supply[0, j]))
        assert (None if s.global_ratio_bps[0, j] == E.UNDEF else int(s.global_ratio_bps[0, j])) == g
    assert (s.halt_mask & E.HALT_GLOBAL_RATIO).any()


def test_hooks_see_every_stage_in_snap_order():
    ps = regtest()
    seen = []

    def hook(stage, params, inputs, series):
        seen.append(stage)

    inp = E.BlockInputs.perfect(np.full(100, 1_000_000), 1)
    E.simulate_blocks(ps, inp, hooks=[hook])
    assert tuple(seen) == E.STAGES


def test_chunked_and_parallel_equal_serial():
    ps = regtest().replace(sigmaRefBps=10_000)
    rng = np.random.default_rng(2)
    P, n = 6, 300
    tp = (1_000_000 * np.exp(np.cumsum(rng.normal(0, 0.01, (P, n)), axis=1))).astype(np.int64)
    from ybcal.sim.oracle import OracleConfig, generate_block_inputs

    inp = generate_block_inputs(tp, OracleConfig.honest(4), rng=rng, start_height=1)
    a = E.simulate_blocks(ps, inp)
    b = E.simulate_blocks(ps, inp, chunk_paths=2)
    c = E.simulate_blocks(ps, inp, workers=3)
    for name in E.BlockSeries.ARRAY_FIELDS:
        assert np.array_equal(getattr(a, name), getattr(b, name)), name
        assert np.array_equal(getattr(a, name), getattr(c, name)), name

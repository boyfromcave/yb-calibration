"""Oracle generation and analytics, SIGMA-1 helpers, the Ycash subsidy schedule, hour mode, devnet."""

from __future__ import annotations

import time

import numpy as np
import pytest

from ybcal.model import kernels as K
from ybcal.params.paramset import mainnet, regtest
from ybcal.sim import engine as E
from ybcal.sim import oracle as O
from ybcal.sim import sigma as S
from ybcal.sim import supply as SUP
from ybcal.studies.base import BUDGETS
from ybcal.types import PricePath


def gbm(rng, P, n, vol=1.2, p0=1_000_000):
    s = vol / np.sqrt(420_480)
    return (p0 * np.exp(np.cumsum(rng.normal(0, s, (P, n)), axis=1))).astype(np.int64)


# ------------------------------------------------------------------ subsidy


def test_mainnet_subsidy_schedule_numbers():
    m = SUP.MAINNET
    assert m.subsidy(1_099_999) == 625_000_000  # pre-Blossom, one halving (since 850,000)
    assert m.subsidy(1_100_000) == 312_500_000  # Blossom halves the per-block subsidy
    assert m.halving(3_075_000) == 2
    assert m.subsidy(3_075_000) == 156_250_000  # 1.5625 YEC at the current startHeight
    assert m.halving_height(3) == 3_960_000
    assert m.subsidy(3_959_999) == 156_250_000 and m.subsidy(3_960_000) == 78_125_000
    assert m.subsidy(5_000) == (1_250_000_000 // 20_000) * 5_000  # slow start
    assert m.subsidy(15_000) == (1_250_000_000 // 20_000) * 15_001
    # one sunset year from the current start: 420,480 × 1.5625 YEC = 657,000 YEC
    assert SUP.issued_between(3_075_000, 3_075_000 + 420_480 - 1, m) == 657_000 * 10**8


@pytest.mark.parametrize("sched", [SUP.MAINNET, SUP.TESTNET, SUP.REGTEST])
def test_subsidy_vectorised_and_closed_form(sched):
    rng = np.random.default_rng(0)
    hs = np.concatenate(
        [rng.integers(0, 6_000_000, 2000), np.arange(0, 400), np.arange(1_099_990, 1_100_010)]
    )
    if sched is SUP.REGTEST:
        hs = rng.integers(0, 20_000, 2000)
    assert (sched.subsidy_array(hs) == [sched.subsidy(int(h)) for h in hs]).all()
    for a, b in (
        [(0, 30_000), (839_000, 1_200_000), (3_000_000, 4_100_000)]
        if sched is not SUP.REGTEST
        else [(1, 9_000)]
    ):
        assert SUP.issued_between(a, b, sched) == int(np.sum(sched.subsidy_array(np.arange(a, b + 1))))


def test_regtest_schedule_is_reference_regtest_subsidy():
    from ybcal.model import reference as R

    for h in range(0, 5_000, 7):
        assert SUP.REGTEST.subsidy(h) == R.regtest_subsidy(h)


def test_schedule_for_network():
    assert SUP.schedule_for(mainnet()) is SUP.MAINNET
    assert SUP.schedule_for(regtest()) is SUP.REGTEST


def test_days_until_cap_admits():
    ps = mainnet()
    # $1 YEC: cap after j blocks = 1.5625·j YEC · $1 · 15 % → a $10,000 mint needs ~42,667 blocks (~37 days)
    d = SUP.days_until_cap_admits(1_000_000, ps, np.full(200_000, 1_000_000))
    c = SUP.days_until_cap_admits_const(1_000_000, 1_000_000, ps)
    assert d.shape == (1,) and abs(d[0] - c) < 1e-9
    assert 36 < c < 38
    # hour resolution through a PricePath-like object gives the hour-end answer
    pp = PricePath(
        __import__("datetime").datetime(2026, 1, 1), "hour", np.full((2, 2000), 1_000_000), "synthetic"
    )
    dh = SUP.days_until_cap_admits(1_000_000, ps, pp)
    assert dh.shape == (2,) and abs(dh[0] - c) <= 1 / 24 + 1e-9
    # class A at σ=1 reaches recapRatioBps (50,000): bypasses the cap (W20)
    assert SUP.days_until_cap_admits(1_000_000, ps, np.full(10, 1_000_000), term_class=0)[0] == 0
    assert np.isnan(SUP.days_until_cap_admits(10**9, ps, np.full(1000, 1_000_000))[0])


def test_supply_cap_series_matches_kernel():
    rng = np.random.default_rng(1)
    issued = rng.integers(0, 10**15, 500)
    xm = np.where(rng.random(500) < 0.1, -1, rng.integers(100, 10**8, 500))
    got = SUP.supply_cap_cents(issued, xm, 1_500)
    for i in range(500):
        want = K.supply_cap_cents(int(issued[i]), int(xm[i]) if xm[i] > 0 else None, 1_500)
        assert (None if got[i] == -1 else int(got[i])) == want


# ------------------------------------------------------------------ oracle


def test_generation_shares_tags_and_signals():
    rng = np.random.default_rng(0)
    tp = gbm(rng, 4, 20_000)
    cfg = O.OracleConfig.honest(5, 0.8, noise_bps=20)
    inp = O.generate_block_inputs(tp, cfg, rng=rng, start_height=10)
    assert abs(inp.tag_present.mean() - 0.8) < 0.02
    assert (inp.tag_pool[inp.tag_present] >= 0).all() and (inp.tag_pool[~inp.tag_present] == -1).all()
    assert np.array_equal(inp.signal_bit, inp.tag_present)
    q = inp.tag_price[inp.tag_price > 0]
    rel = np.abs(q / tp[inp.tag_price > 0] - 1)
    assert np.median(rel) < 0.005
    assert inp.tag_price.min() >= 0 and q.min() >= 100


def test_generation_outages_staleness_attack():
    rng = np.random.default_rng(1)
    tp = np.full((3, 30_000), 2_000_000, dtype=np.int64)
    pools = (
        O.Pool(0.4, outage_rate_per_day=2, outage_mean_hours=3, outage_mode="untagged"),
        O.Pool(0.3, refresh_blocks=50, noise_bps=50),
        O.Pool(0.2, quotes=False),
    )
    cfg = O.OracleConfig(pools, (O.Attack((0,), 1_000, 10_000, 20_000),))
    inp = O.generate_block_inputs(tp, cfg, rng=rng)
    tagged0 = (inp.tag_pool == 0).mean()
    assert tagged0 < 0.4 - 0.01  # outages remove tags
    assert (inp.tag_price[inp.tag_pool == 2] == 0).all()  # signal-only pool
    p1 = inp.tag_price[0][inp.tag_pool[0] == 1]
    assert len(np.unique(p1)) < len(p1) / 3  # stale feed repeats quotes
    att = inp.tag_price[:, 10_000:20_000][inp.tag_pool[:, 10_000:20_000] == 0]
    assert abs(np.median(att) / 2_000_000 - 1.1) < 0.005


def test_price_series_matches_scalar_kernel():
    rng = np.random.default_rng(2)
    ps = regtest()
    tp = rng.integers(100, 10_000, (2, 300))
    tp[rng.random(tp.shape) < 0.4] = 0
    s = O.price_series(ps, tp)
    for r in range(2):
        for i in range(300):
            seg = lambda w, i=i, r=r: [int(x) for x in tp[r, max(0, i - w + 1) : i + 1] if x > 0]  # noqa: E731
            pf = K.window_median_with_fill(seg(8), 4)
            pm = K.window_median_with_fill(seg(24), 16)
            pz = K.window_median_with_fill(seg(64), 43)
            assert [None if v == -1 else int(v) for v in (s.p_fast[r, i], s.p_mid[r, i], s.p_slow[r, i])] == [
                pf,
                pm,
                pz,
            ]
            assert bool(s.halt3[r, i]) == K.halt3_divergence(pf, pm, pz, 2_000)


def test_min_attack_share_analytic():
    # everyone else quotes: up needs a strict majority of the window's blocks → ~0.5
    s = O.min_attack_share(2016, 1344)
    assert 0.49 < s < 0.52
    # down only needs a tie: slightly cheaper
    assert O.min_attack_share(96, 48, direction="down") < O.min_attack_share(96, 48)
    # sparse honest quoting (30 % of hash): majority needs > 0.3, but the fill binds first —
    # A + H ≥ 384 of 576 needs s ≥ 2/3 − 0.3 ≈ 0.367 (below that the median is NO_PRICE instead)
    s_sparse = O.min_attack_share(576, 384, honest_share=0.3)
    assert 0.36 < s_sparse < 0.38
    assert O.min_attack_quotes(10, 43) == 33 and O.min_attack_quotes(30, 43) == 31
    assert O.attack_success_prob(0.0, 96, 48) == 0.0
    p = O.attack_success_prob(0.6, 96, 48)
    assert 0.95 < p <= 1.0


def test_attack_effect_simulated_agrees_with_analytic():
    ps = regtest()
    weak = O.attack_effect(0.2, 2_000, ps, n_blocks=600, paths=4, seed=3)
    strong = O.attack_effect(0.6, 2_000, ps, n_blocks=600, paths=4, seed=3)
    assert strong.moved_fraction["p_slow"] > 0.9 and weak.moved_fraction["p_slow"] < 0.05
    assert strong.max_dev_bps["x_mint"] > 1_500
    # a downward attack by a majority trips HALT-3 (falls only)
    down = O.attack_effect(0.6, -3_000, ps, n_blocks=600, paths=2, seed=3)
    assert down.extra_halt3_blocks > 0


# ------------------------------------------------------------------ sigma


def test_sigma_series_and_helpers():
    ps = regtest().replace(sigmaRefBps=10_000)
    rng = np.random.default_rng(4)
    tp = gbm(rng, 2, 3_000, vol=1.0)
    pf = O.price_series(ps, tp).p_fast
    pf[0, 1500:1510] = -1  # feed gap
    sm = S.sigma_series(ps, pf)
    for i in range(0, 3_000, 37):
        samples = K.sigma_samples([int(v) if v > 0 else None for v in pf[0]], i, 64, 8)
        assert sm[0, i] == K.sigma_mult_bps(samples, 10_000, 8_760, 30_000)
    und = S.undefined_sample_mask(ps, pf)
    assert und[0, :64].all() and und[0, 1500:1574].all() and not und[0, 1580:].any()
    assert (sm[und] == 30_000).all()
    st = S.multiplier_stats(ps, sm, pf)
    assert st.cap_from_undefined_fraction > 0 and st.blocks == 2 * (3_000 - 64)
    assert S.k12_trap_blocks(ps, pf)[0] >= 74
    assert 0 < S.estimator_cv(ps, pf[1:]) < 1


def test_sigma_responsiveness_after_regime_change():
    ps = mainnet()
    rng = np.random.default_rng(6)
    n = 40_000
    vol = np.where(np.arange(n) < 15_000, 0.5, 2.0) / np.sqrt(420_480)
    tp = (1_000_000 * np.exp(np.cumsum(rng.normal(0, 1, (2, n)) * vol, axis=1))).astype(np.int64)
    sm = S.sigma_series(ps, O.price_series(ps, tp).p_fast)
    r = S.responsiveness_blocks(sm, 15_000, frac=0.5)
    assert np.all(r > 0) and np.all(r < 3 * int(ps["volWindow"]))  # σ̂ needs ~one volWindow of new returns


# ------------------------------------------------------------------ engine misc, hour mode


def test_mainnet_smoke_one_path_90_days():
    ps = mainnet()
    rng = np.random.default_rng(7)
    t = time.time()
    tp = gbm(rng, 1, 90 * 1_152)
    inp = O.generate_block_inputs(tp, O.OracleConfig.honest(), rng=rng, start_height=int(ps["startHeight"]))
    s = E.simulate_blocks(ps, inp)
    dt = time.time() - t
    assert dt < 10
    assert s.activation_source in ("internal", "wp5")
    assert int(s.issued_zat[0, -1]) == 90 * 1_152 * 156_250_000
    assert (s.x_mint[0, 2016:] > 0).all()  # fills reached, no NO_PRICE
    assert s.activation_status[0, -1] == E.ACTIVE  # 80 % signalling activates
    assert s.sigma_mult_bps[0, :2016].min() == 30_000  # fact 1.5-3: warm-up at the cap


def test_paths_for_budget():
    assert E.paths_for_budget(BUDGETS["quick"], workers=4) == 64
    assert E.paths_for_budget(BUDGETS["deep"], workers=4, fraction=0.001) < 2000


def test_price_path_adapter():
    pp = PricePath(__import__("datetime").datetime(2026, 1, 1), "block", np.full((2, 10), 5), "synthetic")
    arr, res = E.prices_from(pp)
    assert arr.shape == (2, 10) and res == "block"
    assert E.prices_from(np.arange(3))[0].shape == (1, 3)


def test_hour_mode_kernel_error_within_tolerance():
    ps = mainnet()
    train = [gbm(np.random.default_rng(i), 4, 20 * 1_152) for i in range(2)]
    kern = E.calibrate_kernel(ps, train, seed=1)
    held = gbm(np.random.default_rng(42), 4, 20 * 1_152, vol=1.5)
    inp = O.generate_block_inputs(held, O.OracleConfig.honest(), rng=np.random.default_rng(9))
    bs = E.simulate_blocks(ps, inp, activation_mode="always_active")
    err = E.kernel_error(kern, bs)
    for k in ("p_mint", "p_claim"):
        assert err[k]["p95"] <= E.KERNEL_TOLERANCE_P95_BPS, (k, err[k])
    hs = E.simulate_hours(ps, held[:, 47::48], kern, rng=np.random.default_rng(0))
    assert hs.p_mint.shape == (4, 20 * 24) and (hs.halt_mask[:, :2] & E.HALT_NO_PRICE).all()
    assert (hs.sigma_mult_bps[:, :42] == 30_000).all()


def test_simulate_devnet_records():
    from ybcal.devnet.diff import DEFAULT_FIELDS, compare, resolve_simulator
    from ybcal.devnet.scenarios import make_schedule, schedule_prices
    from ybcal.devnet.scrape import HISTORY_FIELDS

    assert resolve_simulator() is E.simulate_devnet
    ps = regtest()
    sched = make_schedule("crash-70", ps, seed=3)
    recs = E.simulate_devnet(ps, schedule_prices(sched), sched)
    assert len(recs) == sched.total_blocks and recs[0]["height"] == 1
    assert set(HISTORY_FIELDS) <= set(recs[0])
    assert (
        recs[0]["pMint"] is None and recs[0]["tagged"] == 1 and recs[0]["quote"] == 0
    )  # funding: signal-only
    act = [r for r in recs if r["activationStatus"] == "active"]
    assert act and act[0]["activateHeight"] == act[0]["lockInHeight"] + int(ps["activationDelay"])
    assert any(r["haltMask"] & E.HALT_DIVERGENCE for r in recs)
    assert compare(recs, E.simulate_devnet(ps, schedule_prices(sched), sched), DEFAULT_FIELDS).passed
    drop = make_schedule("hashrate-drop", ps)
    r2 = E.simulate_devnet(ps, schedule_prices(drop), drop)
    assert any(r["haltMask"] & E.HALT_PARTICIPATION for r in r2)

"""Synthetic models: simulate statistics, fit recovery, bootstrap, spread and pool models."""

from __future__ import annotations

import math

import numpy as np
import pytest

from ybcal.data import synthetic as syn
from ybcal.data.loaders import PoolShareLog, SpreadsLog
from ybcal.data.pricepath import DT_BLOCK, DT_HOUR, log_returns, resample
from ybcal.types import PricePath
from ybcal.units import PRICE_MAX, PRICE_MIN

HOURS_PER_YEAR = 8760


def rng(seed=1):
    return np.random.default_rng(seed)


def ann_vol(pp: PricePath) -> float:
    r = log_returns(pp)
    per_year = HOURS_PER_YEAR if pp.resolution == "hour" else 420_480
    return float(np.std(r) * math.sqrt(per_year))


@pytest.mark.parametrize("name", ["gbm", "merton", "garch", "regime"])
def test_presets_are_yec_like_and_well_formed(name):
    m = syn.preset(name)
    pp = m.simulate(40, HOURS_PER_YEAR + 1, "hour", rng(), p0=400_000)
    assert pp.prices.shape == (40, HOURS_PER_YEAR + 1) and pp.prices.dtype == np.int64
    assert (pp.prices[:, 0] == 400_000).all() and pp.provenance == "synthetic" and pp.resolution == "hour"
    assert pp.prices.min() >= PRICE_MIN and pp.prices.max() <= PRICE_MAX
    assert pp.meta["model"] == name and pp.meta["calibrated"] is False and "placeholder" in pp.meta["note"]
    assert 0.95 < ann_vol(pp) < 1.45  # ≈ 120 % annualised
    mdd = [1 - (row / np.maximum.accumulate(row)).min() for row in pp.prices]
    assert np.median(mdd) > 0.6  # deep drawdowns within a year


def test_preset_table_and_bootstrap_has_no_preset():
    t = syn.preset_table()
    assert set(t) == {"gbm", "merton", "garch", "regime"}
    assert all(0.9 < v["annual_vol"] < 1.3 for v in t.values())
    with pytest.raises(ValueError, match="real data"):
        syn.preset("bootstrap")
    with pytest.raises(ValueError):
        syn.preset("gbm", nosuch=1)


def test_simulate_is_deterministic_per_seed():
    m = syn.preset("garch")
    a = m.simulate(3, 500, "hour", rng(5))
    b = m.simulate(3, 500, "hour", rng(5))
    c = m.simulate(3, 500, "hour", rng(6))
    assert np.array_equal(a.prices, b.prices) and not np.array_equal(a.prices, c.prices)


def test_dt_validation():
    with pytest.raises(ValueError):
        syn.GBM().simulate(1, 10, 1 / 365, rng())
    assert syn.GBM().simulate(1, 10, DT_BLOCK, rng()).resolution == "block"


def test_gbm_moments_and_fit():
    m = syn.GBM(mu=0.2, sigma=0.9)
    pp = m.simulate(1, 5 * HOURS_PER_YEAR, DT_HOUR, rng(2))
    f = syn.GBM.fit(pp)
    assert f.sigma == pytest.approx(0.9, rel=0.03)
    assert f.meta["fitted"] and f.meta["data_provenance"] == "synthetic"
    many = m.simulate(400, 25, "hour", rng(3))
    r = log_returns(many)
    assert r.mean() == pytest.approx(
        (0.2 - 0.5 * 0.81) * DT_HOUR, abs=4 * 0.9 * math.sqrt(DT_HOUR) / math.sqrt(r.size)
    )


def test_merton_fit_recovers_jumps():
    m = syn.Merton(mu=0.1, sigma=0.8, lam=50.0, jump_mu=-0.05, jump_sigma=0.1)
    pp = m.simulate(1, 5 * HOURS_PER_YEAR, "hour", rng(4))
    f = syn.Merton.fit(pp)
    assert f.sigma == pytest.approx(0.8, rel=0.05)
    assert f.lam == pytest.approx(50, rel=0.3)
    assert f.jump_mu == pytest.approx(-0.05, abs=0.02)
    assert f.jump_sigma == pytest.approx(0.1, rel=0.3)
    assert m.total_vol == pytest.approx(math.sqrt(0.64 + 50 * (0.0025 + 0.01)))


def test_garch_fit_recovers_parameters():
    g = syn.Garch.preset()
    pp = g.simulate(1, 30_000, "hour", rng(7))
    f = syn.Garch.fit(pp)
    assert f.alpha == pytest.approx(0.06, abs=0.02)
    assert f.beta == pytest.approx(0.93, abs=0.03)
    assert f.nu == pytest.approx(4.0, abs=1.2)
    assert f.dt_native == pytest.approx(DT_HOUR)


def test_garch_vol_clustering_and_fat_tails():
    pp = syn.preset("garch").simulate(4, 20_000, "hour", rng(8))
    r = log_returns(pp)[0]
    a = np.abs(r) - np.abs(r).mean()
    assert np.dot(a[:-1], a[1:]) / np.dot(a, a) > 0.05  # |r| autocorrelated
    z = (r - r.mean()) / r.std()
    assert (z**4).mean() - 3 > 1.0  # excess kurtosis


def test_garch_block_bridge_preserves_hourly_returns():
    g = syn.Garch.preset()
    hourly = g.log_returns(2, 101, "hour", rng(9))
    block = g.log_returns(2, 100 * 48 + 1, "block", rng(9))
    assert block.shape == (2, 4800)
    assert np.allclose(block.reshape(2, 100, 48).sum(axis=2), hourly)
    # sub-step variance ≈ hourly variance / 48
    big = g.log_returns(50, 200 * 48 + 1, "block", rng(10))
    assert np.var(big) * 48 == pytest.approx(g.unconditional_var, rel=0.25)


def test_regime_fit_recovers_states():
    m = syn.RegimeSwitch()
    pp = m.simulate(1, 5 * HOURS_PER_YEAR, "hour", rng(11))
    f = syn.RegimeSwitch.fit(pp)
    assert f.sigma[0] == pytest.approx(0.8, rel=0.1) and f.sigma[1] == pytest.approx(2.0, rel=0.1)
    assert 0.5 * m.q01 < f.q01 < 2 * m.q01 and 0.5 * m.q10 < f.q10 < 2 * m.q10
    st = m.states(20, 5000, DT_HOUR, rng(12))
    assert st.mean() == pytest.approx(m.stationary[1], abs=0.08)


def test_bootstrap_preserves_marginal_distribution():
    base = syn.preset("garch").simulate(1, 20_000, "hour", rng(13))
    b = syn.BlockBootstrap.fit(base)
    assert b.mean_block == pytest.approx(168.0)
    out = b.simulate(20, 5_000, "hour", rng(14))
    r_src, r_out = b.returns, log_returns(out).ravel()
    for q in (0.01, 0.1, 0.5, 0.9, 0.99):
        assert np.quantile(r_out, q) == pytest.approx(np.quantile(r_src, q), rel=0.15, abs=2e-4)
    # every resampled return is one of the source returns
    idx = b.indices(3, 1000, rng(15))
    assert idx.min() >= 0 and idx.max() < len(r_src)
    # blocks: consecutive indices continue with probability 1 - 1/L
    cont = (np.diff(idx, axis=1) == 1).mean()
    assert cont == pytest.approx(1 - 1 / 168, abs=0.01)


def test_fit_uses_observed_points_only():
    # hourly observations held onto a block grid fit as hourly returns, not as 47 zeros + 1 move
    coarse = syn.GBM(sigma=1.0).simulate(1, 2001, "hour", rng(16))
    hourly = resample(coarse, "block")
    filled = np.ones(hourly.n_steps, dtype=bool)
    filled[::48] = False
    obs = PricePath(hourly.t0, "block", hourly.prices, "real", {"filled": filled})
    r, dt = syn.fit_returns_of(obs)
    assert dt == pytest.approx(DT_HOUR) and len(r) == 2000
    f = syn.GBM.fit(obs)
    assert f.sigma == pytest.approx(1.0, rel=0.08)


# spread model ---------------------------------------------------------------------------------------


def test_spread_model_generate_shapes_and_staleness():
    true = syn.GBM(sigma=0.6).simulate(2, 20_000, "block", rng(17))
    sm = syn.SpreadModel()
    q = sm.generate(true, rng(18))
    assert q.quotes.shape == (2, 3, 20_000) and q.step_seconds == 75
    assert (q.quotes[q.outage] == 0).all() and (q.quotes[~q.outage] > 0).all()
    # nonkyc refreshes 30/h at 75 s steps → stale share ≈ exp(-30·75/3600)
    assert q.stale[:, 2].mean() == pytest.approx(math.exp(-30 * 75 / 3600), abs=0.03)
    sp = q.pair_spreads_bps()
    assert np.median(sp[("coingecko", "safetrade")]) > 0


def _spreads_log_from(q: syn.SourceQuotes, step: int) -> SpreadsLog:
    n = q.quotes.shape[2]
    ts = 1_700_000_000 + np.arange(n) * step
    return SpreadsLog(ts, q.quotes[0].T.copy(), q.names, [""] * n, "synthetic")


def test_spread_model_fit_round_trip():
    true = syn.GBM(sigma=0.6).simulate(1, 8_000, "block", rng(19))
    sm = syn.SpreadModel(
        bias_bps=(0.0, 60.0, -40.0),
        sigma_bps=(30.0, 100.0, 150.0),
        tau_seconds=900.0,
        refresh_per_hour=(1e6, 1e6, 1e6),
        outage_per_day=(0.5, 1.0, 0.5),
        outage_mean_hours=(0.5, 1.0, 2.0),
    )
    q = sm.generate(true.prices, rng(20), step_seconds=300)
    f = syn.SpreadModel.fit(_spreads_log_from(q, 300))
    # deviations are measured from the cross-source median, so relative biases are what is recovered
    rel = np.array(f.bias_bps) - f.bias_bps[0]
    assert rel[1] == pytest.approx(60, abs=25) and rel[2] == pytest.approx(-40, abs=30)
    assert f.sigma_bps[2] > f.sigma_bps[0]
    assert 300 < f.tau_seconds < 3000
    assert f.outage_mean_hours[2] == pytest.approx(2.0, rel=0.5)
    assert f.meta["fitted"] and f.meta["step_seconds"] == 300


def test_spread_model_fit_needs_rows():
    log = SpreadsLog(np.arange(3), np.ones((3, 3), dtype=np.int64))
    with pytest.raises(ValueError):
        syn.SpreadModel.fit(log)


# pools ----------------------------------------------------------------------------------------------


def test_pool_assign_matches_shares():
    pm = syn.PoolModel()
    miner = pm.assign(4, 50_000, rng(21))
    freq = np.bincount(miner.ravel(), minlength=pm.n_pools + 1) / miner.size
    assert freq[: pm.n_pools] == pytest.approx(pm.shares, abs=0.01)
    assert freq[-1] == pytest.approx(1 - sum(pm.shares), abs=0.01)
    # time-varying shares
    sh = np.array([[0.9, 0, 0, 0, 0, 0], [0.0, 0.9, 0, 0, 0, 0]])
    m2 = pm.assign(1, 2000, rng(22), shares=sh, shares_step_blocks=1000)
    assert (m2[0, :1000] == 0).mean() == pytest.approx(0.9, abs=0.05)
    assert (m2[0, 1000:] == 1).mean() == pytest.approx(0.9, abs=0.05)


def test_pool_generate_tags_twap_bias_frozen():
    p = np.full((1, 5000), 400_000, dtype=np.int64)
    p[0, 2500:] = 200_000
    pm = syn.PoolModel().with_pools(
        noise_bps=[0] * 6,
        bias_bps=[1000, 0, 0, 0, 0, 0],
        frozen=[False, True, False, False, False, False],
        tagging=[True, True, True, True, True, False],
        outage_per_day=[0] * 6,
    )
    out = pm.generate(p, rng(23))
    assert out.quote.shape == p.shape
    assert not out.tagged[out.miner == 5].any() and not out.tagged[out.miner == 6].any()
    a = out.miner == 0
    early = a & (np.arange(5000) < 2000)
    assert np.allclose(out.quote[early], round(400_000 * math.exp(0.1)), atol=1)
    frozen = out.miner == 1
    assert (out.quote[frozen] == 400_000).all()  # stuck at its first quote
    late = (out.miner == 2) & (np.arange(5000) > 2600)
    assert (out.quote[late] == 200_000).all()  # TWAP caught up after 12 blocks + lag
    assert pm.tagging_share == pytest.approx(0.25 + 0.20 + 0.15 + 0.10 + 0.06)


def test_pool_fit_and_drift():
    keys = ("A", "B", "C", "dust")
    r = rng(24)
    pool = r.choice(4, size=20_000, p=[0.5, 0.3, 0.195, 0.005]).astype(np.int32)
    log = PoolShareLog(np.arange(20_000, dtype=np.int64), pool, keys, "x")
    pm = syn.PoolModel.fit(log)
    assert pm.names == ("A", "B", "C") and pm.shares[0] == pytest.approx(0.5, abs=0.02)
    assert pm.meta["other_share"] == pytest.approx(0.005, abs=0.003)
    d = syn.HashrateDrift()
    sh = d.simulate(pm.shares, 500, rng(25), n_paths=3)
    assert sh.shape == (3, 500, 3) and (sh.sum(axis=2) < 1).all()
    assert np.allclose(sh[:, 0], pm.shares, atol=1e-6)
    fit = syn.HashrateDrift.fit(log, window_blocks=1000)
    assert fit.sigma_per_sqrt_day > 0
    miner = pm.assign(1, 1000, rng(26), shares=sh[0], shares_step_blocks=48)
    assert miner.shape == (1, 1000)


def test_renewal_outages_fraction():
    m = syn.renewal_outages(20, 1, 20_000, 75, [2.0], [3.0], rng(27))
    # expected down fraction = mean_down / (mean_up + mean_down) = 3 / (12 + 3)
    assert m.mean() == pytest.approx(3 / 15, abs=0.04)


def test_returns_to_path_clamps():
    pp = syn.returns_to_path(np.array([[50.0, -100.0]]), 400_000, DT_HOUR)
    assert pp.prices.tolist() == [[400_000, PRICE_MAX, PRICE_MIN]]


def test_regime_fast_switching_keeps_stationary_share():
    # hourly switching probabilities like YEC's fit (p01 ≈ 0.17, p10 ≈ 0.41): the simulated chain's
    # turbulent share must equal the model's stationary share, so the zero-drift shift holds
    q01, q10 = syn.embed_two_state(0.17, 0.41, DT_HOUR)
    m = syn.RegimeSwitch(mu=(0.0, 0.0), sigma=(0.5, 8.0), q01=q01, q10=q10)
    assert m.stationary[1] == pytest.approx(0.17 / 0.58)
    st = m.states(50, 20_000, DT_HOUR, rng(21))
    assert st.mean() == pytest.approx(m.stationary[1], abs=0.01)
    syn.neutralise_drift(m)
    assert m.expected_log_drift() == pytest.approx(0.0, abs=1e-9)
    r = m.log_returns(200, 8761, "hour", rng(22))
    assert r.sum(axis=1).mean() == pytest.approx(0.0, abs=0.5)  # one year of log returns


def test_fit_neutralises_drift_by_default():
    pp = syn.GBM(mu=3.0, sigma=1.0).simulate(1, 2 * HOURS_PER_YEAR, "hour", rng(23))
    pp.meta["source_file"] = "x"
    for name in ("gbm", "merton", "garch", "regime", "bootstrap"):
        m = syn.fit(name, pp)
        assert m.expected_log_drift() == pytest.approx(0.0, abs=1e-6), name
    assert syn.fit("gbm", pp, drift="fitted").expected_log_drift() > 1.0
    assert syn.fit("bootstrap", pp, drift="fitted").demean is False


def test_stale_runs_dropped_from_fit():
    r = np.array([0.01, -0.02] + [0.0] * 10 + [0.3, 0.01, 0.0, 0.0, -0.01])
    out = syn.drop_stale_runs(r, 6)
    # the 10 zeros and the 0.3 that closes the stale run go; the short 2-zero run stays
    np.testing.assert_allclose(out, [0.01, -0.02, 0.01, 0.0, 0.0, -0.01])

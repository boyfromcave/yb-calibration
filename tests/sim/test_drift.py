"""Drift conventions for long-horizon solvency ensembles (D-RD-AUD-1)."""

from __future__ import annotations

import numpy as np
import pytest

from ybcal.config import Policy
from ybcal.data import synthetic as SY
from ybcal.sim import drift as DR
from ybcal.studies import g3_collateral as G3
from ybcal.studies.base import Budget, Env

TINY = Budget(
    "quick",
    paths=8,
    block_horizon_days=3,
    hour_horizon_years=1.0,
    grid_points=3,
    lhs_samples=4,
    halving_rounds=1,
    scenario_set="core",
    morris_trajectories=2,
    sobol_samples=8,
    max_minutes=1,
)


@pytest.mark.parametrize("name", ["gbm", "merton", "garch", "regime"])
def test_targets_per_preset(name):
    m = SY.preset(name)
    vol = DR.annual_vol(m)
    assert 0.5 < vol < 2.5
    assert DR.target_log_drift("centred", vol, m.expected_log_drift()) == 0.0
    assert DR.target_log_drift("martingale", vol, m.expected_log_drift()) == pytest.approx(-0.5 * vol * vol)
    assert DR.target_log_drift("model", vol, m.expected_log_drift()) == m.expected_log_drift()
    assert DR.shift_per_year("model", m) == 0.0


def test_presets_disagree_without_a_convention():
    """The defect: GBM/Merton/regime are martingales (≈ −0.7/yr log drift), GARCH-t is centred."""
    d = {n: SY.preset(n).expected_log_drift() for n in ("gbm", "merton", "garch", "regime")}
    assert d["garch"] == 0.0 and all(d[n] < -0.6 for n in ("gbm", "merton", "regime"))


def test_apply_shifts_the_sample_mean():
    rng = np.random.default_rng(3)
    m = SY.preset("gbm")
    dt = SY.as_dt("hour")
    r = m.log_returns(64, 8761, dt, rng)
    for kind, want in (("centred", 0.0), ("martingale", -0.72), ("model", -0.72)):
        got = DR.apply(r, dt, kind, m).mean() / dt
        assert got == pytest.approx(want, abs=0.25)


def test_bootstrap_centred_removes_sample_drift():
    rng = np.random.default_rng(4)
    r = rng.normal(0.0002, 0.01, 5000)  # +1.75/yr of hourly drift, like the 2025-26 YEC year
    bb = SY.BlockBootstrap.fit_returns(r, SY.as_dt("hour"))
    assert DR.describe("centred", bb)["applied_log_drift"] == 0.0
    assert DR.describe("model", bb)["model_log_drift"] == pytest.approx(r.mean() / SY.as_dt("hour"))
    with pytest.raises(ValueError):
        DR.check("up")


def test_g3_ensemble_honours_the_policy():
    G3.clear_caches()
    base = Env(Policy(), TINY, seed=5)
    model = Env(Policy().replace(price_drift="model"), TINY, seed=5)
    from ybcal.params.paramset import mainnet

    ps = mainnet()
    e_c = G3.ensemble(base, ps)
    e_m = G3.ensemble(model, ps)
    assert e_c.meta["price_drift"] == "centred" and e_m.meta["price_drift"] == "model"
    # "model" is exactly the old SY.preset(name).simulate path (same draws)
    rng = model.rng_for("ybcal-hour-ensemble", "gbm")
    old = SY.preset("gbm").simulate(2, e_m.true["gbm"].shape[1], "hour", rng)
    assert np.array_equal(np.asarray(old.prices, dtype=np.int64), e_m.true["gbm"])
    # centring lifts the GBM paths' end log price by σ²/2 · T on average
    lift = np.log(e_c.true["gbm"][:, -1] / e_m.true["gbm"][:, -1]).mean()
    assert lift == pytest.approx(0.72 * 1.0, abs=0.01)
    G3.clear_caches()

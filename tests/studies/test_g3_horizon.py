"""G3 long-horizon layer (wave 2, D-RD-COL-1..3): daily series, members, the sorted-sample frontier
against WP-4's fast path, real-history replay, and the daily members inside G3's ensemble."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from ybcal.config import Policy
from ybcal.data.pricepath import make_path
from ybcal.params.paramset import mainnet
from ybcal.sim import engine as E
from ybcal.sim import metrics as M
from ybcal.studies import g3_collateral as G3
from ybcal.studies import g3_horizon as H
from ybcal.studies.base import Budget, Env
from ybcal.units import BPS

T0 = int(datetime(2020, 1, 1, tzinfo=UTC).timestamp())


def toy_daily(n=900, seed=3, vol=1.2) -> H.Daily:
    rng = np.random.default_rng(seed)
    r = rng.standard_normal(n - 1) * vol / np.sqrt(365)
    p = 0.3 * np.exp(np.r_[0.0, np.cumsum(r)])
    return H.Daily(T0 + 86_400 * np.arange(n, dtype=np.int64), p, "toy")


def test_p_and_es_match_brute_force():
    rng = np.random.default_rng(0)
    x = np.sort(rng.lognormal(0, 0.8, 4000))
    R = np.array([10_000, 15_000, 30_000, 77_500, 200_000])
    p, es = H._p_and_es(x, R)
    for i, r in enumerate(R / BPS):
        assert p[i] == pytest.approx(np.mean(r * x < 1))
        assert es[i] == pytest.approx(np.mean(np.maximum(0, 1 - r * x)))
    p0, _ = H._p_and_es(np.array([]), R)
    assert np.isnan(p0).all()


def test_daily_window_returns_and_loader(tmp_path):
    d = toy_daily(400)
    w = d.window("2020-02-01", "2020-02-29")
    assert len(w) == 29 and H._iso(w.ts[0]) == "2020-02-01"
    assert len(d.window("-30", None)) == 31
    r = d.returns()
    assert r.size == 399 and np.allclose(np.exp(np.cumsum(r))[-1], d.price[-1] / d.price[0])
    f = tmp_path / "p.csv"
    with f.open("w") as fh:
        fh.write("ts,price_usd\n")
        for t, p in zip(d.ts[:10], d.price[:10], strict=True):
            fh.write(f"{t + 3600},{p * 0.9}\n{t + 7200},{p}\n")  # two points a day: the last one wins
    ld = H.load_daily(f)
    assert len(ld) == 10 and np.allclose(ld.price, d.price[:10]) and (ld.ts == d.ts[:10]).all()


def test_daily_to_hourly_hits_every_close():
    d = toy_daily(10)
    h = H.daily_to_hourly(d.price)
    assert h.shape == (1, 9 * 24 + 1)
    assert np.allclose(h[0, ::24] / 1e6, d.price, rtol=1e-5)  # µUSD rounding


def test_daily_of_reads_a_forward_filled_hourly_grid():
    d = toy_daily(30)
    hourly = np.repeat(np.rint(d.price * 1e6).astype(np.int64), 24)
    filled = np.ones(hourly.size, bool)
    filled[::24] = False
    pp = make_path(datetime.fromtimestamp(T0, UTC), "hour", hourly[None, :], "real", {"filled": filled})
    got = H.daily_of(pp)
    assert got is not None and len(got) == 30 and np.allclose(got.price, d.price, rtol=1e-5)
    pp2 = make_path(datetime.fromtimestamp(T0, UTC), "hour", hourly[None, :], "real")
    assert H.daily_of(pp2) is None  # every hour observed: an hourly series, not a daily one


@pytest.mark.parametrize("name", ["bootstrap-30d", "regime", "martingale"])
def test_members_simulate_hourly_paths(name):
    d = toy_daily(900)
    tp = H.simulate_member(name, d, paths=3, years=0.5, rng=np.random.default_rng(1))
    assert tp.shape == (3, round(0.5 * 8760) + 1) and (tp > 0).all()
    assert tp[0, 0] == round(d.price[-1] * 1e6)


def test_frontier_matches_fast_path():
    """The sorted-sample frontier equals p_bad_debt_fast at the same locked ratio (σ fixed at 1×), up
    to the wallet's 1,000-zat rounding."""
    rng = np.random.default_rng(5)
    params = mainnet()
    n = round(1.4 * 8760)
    r = rng.standard_normal((2, n - 1)) * 1.0 / np.sqrt(8760)
    tp = np.rint(360_000 * np.exp(np.c_[np.zeros(2), np.cumsum(r, axis=1)])).astype(np.int64)
    hours = E.simulate_hours(params, tp, E.OracleTransferKernel.ideal(params, substeps=H.HOUR_SUBSTEPS))
    rs = H.ratio_samples(params, tp, "toy", hours=hours, n_terms=4, start_stride=12, classes=(0, 1))
    f = H.frontier(rs, ratios=(15_000, 20_000, 30_000))
    for ratio in (15_000, 20_000, 30_000):
        ps = params.replace({"baseRatioBps[0]": ratio, "baseRatioBps[1]": ratio})
        fb = M.p_bad_debt_fast(ps, tp, hour_series=hours, classes=(0, 1), n_terms=4, sigma=10_000,
                               start_stride=12)
        for c in ("A", "B"):
            assert f.at(c, ratio)["p"] == pytest.approx(fb.p[c], abs=2e-3)
            assert f.at(c, ratio)["p_lock"] == pytest.approx(fb.p_lock[c], abs=2e-3)
            assert f.at(c, ratio)["es"] == pytest.approx(fb.shortfall[c], abs=2e-3)
    assert f.needed("A", 1.0) == 15_000


def test_frontier_class_value_needs_every_term():
    d = toy_daily(500)
    rs = H.ratio_samples(mainnet(), H.daily_to_hourly(d.price), "history", n_terms=4, start_stride=24)
    f = H.frontier(rs, ratios=(50_000,))
    assert np.isfinite(f.p["A"]).all()
    assert np.isnan(f.p["C"]).all()  # 500 days cannot hold a 5-year term
    assert min(rs.n_starts["C"]) == 0


def test_history_table_counts_and_worst_start():
    d = toy_daily(700)
    rows = H.history_table(mainnet(), d, (50_000, 1_000_000), terms_days=(30, 365, 1825))
    by = {r["term_days"]: r for r in rows}
    assert by[1825]["n_starts"] == 0
    a = by[30]
    assert a["n_starts"] > 500 and 0 <= a["p_bad@50000"] <= 1 and a["p_bad@1000000"] <= a["p_bad@50000"]
    assert a["x_min"] <= a["x_p01"] <= a["x_p50"]
    assert a["ratio_to_cover_all"] >= BPS / a["x_min"] - 1


def test_g3_ensemble_adds_daily_members():
    tiny = Budget("quick", paths=8, block_horizon_days=3, hour_horizon_years=1.2, grid_points=3,
                  lhs_samples=4, halving_rounds=1, scenario_set="core", morris_trajectories=2,
                  sobol_samples=8, max_minutes=1)
    d = toy_daily(2400)
    hourly = H.daily_to_hourly(d.price[-400:])
    pp = make_path(datetime.fromtimestamp(int(d.ts[-400]), UTC), "hour", hourly, "real")
    env = Env(Policy(), tiny, seed=3, data={"price": pp, "price_daily": d})
    G3.clear_caches()
    ens = G3.ensemble(env, mainnet())
    assert ens.names == ("bootstrap", "history", *G3.DAILY_MEMBERS)
    assert G3.hourly_members(ens) == ("bootstrap", "history")
    s = G3.member_sigma(ens, "daily-bootstrap", "median", G3.warmup_hours(mainnet()))
    assert isinstance(s, int) and s >= 10_000
    assert G3.member_sigma(ens, "bootstrap", "median", 0) == "median"
    # without a daily path the ensemble is the pre-wave-2 one
    env2 = Env(Policy(), tiny, seed=3, data={"price": pp})
    assert G3.ensemble(env2, mainnet()).names == ("bootstrap", "history")
    G3.clear_caches()

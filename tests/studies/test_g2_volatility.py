"""G2 study (WP-7a): space, decision rules, responsiveness helper, determinism, tiny run."""

from __future__ import annotations

import numpy as np
import pytest

from ybcal.config import Policy
from ybcal.params.paramset import mainnet
from ybcal.params.registry import params_for_group
from ybcal.studies import g1_price_windows as G1
from ybcal.studies import g2_volatility as G
from ybcal.studies.base import Budget, Env, Metrics, ResultTable, load_study, missing_recommendations

TINY = Budget(
    "quick",
    paths=2,
    block_horizon_days=10,
    hour_horizon_years=1.0,
    grid_points=3,
    lhs_samples=4,
    halving_rounds=1,
    scenario_set="core",
    morris_trajectories=2,
    sobol_samples=8,
    max_minutes=1,
)


def env_for(tmp_path=None, seed=5) -> Env:
    return Env(Policy(), TINY, seed=seed, data={"out_dir": str(tmp_path)} if tmp_path is not None else {})


def test_space_includes_base_and_respects_invariants():
    st = G.make_study()
    base = mainnet()
    for b in ("quick", "standard"):
        cands = list(st.space(base, Budget.named(b)))
        assert cands[0] == base and len(set(cands)) == len(cands)
        for c in cands:
            assert not c.check(), c.delta(base)
            assert c.as_int("volPeriodsPerYear") * c.as_int("volStep") == 420_480


def test_shift_response():
    n, k = 4000, 1000
    s = np.full((2, n), 5000.0)
    s[:, :200] = np.nan  # undefined warm-up inside the pre-change window
    s[0, k:] = np.linspace(5000, 15000, n - k).clip(max=15000)
    s[0, k + 900 :] = 15000
    s[1, k + 10 :] = 15000
    r = G.shift_response(s, k, frac=0.9)
    assert r[1] == 10
    assert 790 <= r[0] <= 900


# ---------------------------------------------------------------------------------------------------
# Decision rules on hand-built tables


def _m(cv, p50=7000.0, p99t=20000.0, *, ok=True, prov="real-data", out=None):
    v = {
        "sigma_hat_cv_calm": cv,
        "sigma_hat_p50_realised": p50,
        "sigma_hat_p99_turbulent": p99t,
        "responsiveness_blocks": 1500.0,
    }
    return Metrics(
        v,
        "sigma_hat_cv_calm",
        True,
        {"max_sigma_lag_blocks": ok, "k12_trap_le_max_sigma_lag": True},
        prov,
        {"out_dir": out, "budget": "standard"},
    )  # type: ignore[arg-type]


def _table(tmp_path, rows, prov="real-data"):
    base = mainnet()
    t = ResultTable(base)
    for change, kw in rows:
        t.add(base.replace(**change), _m(prov=prov, out=str(tmp_path), **kw))
    return t


def _rec(recs, p):
    return next(r for r in recs if r.param == p)


def test_windows_rule(tmp_path):
    t = _table(
        tmp_path,
        [
            ({}, dict(cv=0.15)),
            ({"volWindow": 2880}, dict(cv=0.11)),
            ({"volWindow": 4032}, dict(cv=0.05, ok=False)),
        ],
    )
    recs = G.make_study().decide(t, Policy())
    r = _rec(recs, "volWindow")
    assert r.recommended == 2880 and r.verdict == "CHANGE"  # 27 % > 20 %; 4032 infeasible
    assert "max_sigma_lag_blocks" in r.binding
    assert _rec(recs, "volPeriodsPerYear").recommended == 8760
    assert not missing_recommendations(G.make_study(), recs)
    t = _table(tmp_path, [({}, dict(cv=0.15)), ({"volWindow": 2880}, dict(cv=0.13))])
    assert _rec(G.make_study().decide(t, Policy()), "volWindow").verdict == "KEEP"


def test_volstep_change_derives_periods(tmp_path):
    t = _table(tmp_path, [({}, dict(cv=0.15)), ({"volStep": 24}, dict(cv=0.10))])
    recs = G.make_study().decide(t, Policy())
    assert _rec(recs, "volStep").recommended == 24
    assert _rec(recs, "volPeriodsPerYear").recommended == 17_520


def test_sigma_ref_rule(tmp_path):
    # p50 7,240 → rounded *down* to 7,000; current 10,000 gives ratio 0.72 < 1 → CHANGE
    t = _table(tmp_path, [({}, dict(cv=0.15, p50=7240.0))])
    r = _rec(G.make_study().decide(t, Policy()), "sigmaRefBps")
    assert r.recommended == 7000 and r.verdict == "CHANGE"
    assert r.metrics["recommended"]["m14_ratio"] >= 1.0
    # p50 11,000: ratio 1.1 in band and rule value 11,000 is 10 % off → KEEP
    t = _table(tmp_path, [({}, dict(cv=0.15, p50=11000.0))])
    r = _rec(G.make_study().decide(t, Policy()), "sigmaRefBps")
    assert r.recommended == 10000 and r.verdict == "KEEP"
    # p50 14,000: in band (1.4) but the rule value is 40 % away → CHANGE
    t = _table(tmp_path, [({}, dict(cv=0.15, p50=14000.0))])
    assert _rec(G.make_study().decide(t, Policy()), "sigmaRefBps").recommended == 14000


def test_cap_rule(tmp_path):
    # p50 10,000 → ref stays 10,000; turbulent p99 σ̂ 21,000 → 2.1× → smallest multiple of 2,500 = 22,500;
    # current 30,000 covers it and is 25 % above → CHANGE down
    t = _table(tmp_path, [({}, dict(cv=0.15, p50=10000.0, p99t=21000.0))])
    r = _rec(G.make_study().decide(t, Policy()), "sigmaMultMaxBps")
    assert r.recommended == 22500 and r.verdict == "CHANGE"
    # 2.6× → 27,500, only 8 % below the current 30,000 → KEEP
    t = _table(tmp_path, [({}, dict(cv=0.15, p50=10000.0, p99t=26000.0))])
    assert _rec(G.make_study().decide(t, Policy()), "sigmaMultMaxBps").verdict == "KEEP"
    # 3.6× → current does not cover → 37,500
    t = _table(tmp_path, [({}, dict(cv=0.15, p50=10000.0, p99t=36000.0))])
    r = _rec(G.make_study().decide(t, Policy()), "sigmaMultMaxBps")
    assert r.recommended == 37500 and r.verdict == "CHANGE"
    # beyond the 50,000 bound → BLOCKED
    t = _table(tmp_path, [({}, dict(cv=0.15, p50=10000.0, p99t=60000.0))])
    r = _rec(G.make_study().decide(t, Policy()), "sigmaMultMaxBps")
    # D-RD-AUD-7: the least-violating value of a BLOCKED cap is the bound, not the current value
    assert r.verdict == "BLOCKED" and r.recommended == 50000
    assert any("K12 trap" in n for n in r.notes) and any("single jumps" in n for n in r.notes)


def test_synthetic_is_provisional(tmp_path):
    t = _table(tmp_path, [({}, dict(cv=0.15, p50=7240.0))], prov="synthetic")
    assert {r.verdict for r in G.make_study().decide(t, Policy())} == {"PROVISIONAL"}


# ---------------------------------------------------------------------------------------------------
# Tiny end-to-end run


@pytest.fixture(scope="module")
def tiny_run(tmp_path_factory):
    G1.clear_caches()
    out = tmp_path_factory.mktemp("g2")
    env = env_for(out)
    st = load_study("G2")
    base = mainnet()
    t = ResultTable(base)
    for c in (
        base,
        base.replace(volWindow=2880),
        base.replace(sigmaRefBps=8000),
        base.replace(sigmaMultMaxBps=25000),
    ):
        t.add(c, st.evaluate(c, env))
    return st, t, st.decide(t, env.policy), out


def test_tiny_run_recommends_every_param(tiny_run):
    st, t, recs, _out = tiny_run
    assert set(st.params) == set(params_for_group("G2"))
    assert not missing_recommendations(st, recs)
    for r in recs:
        assert r.verdict in ("PROVISIONAL", "BLOCKED") and r.provenance == "synthetic"
        text = st.explain(r, t)
        assert r.param in text and len(text) > 80
    assert all(p.exists() for p in recs[0].evidence)
    assert any(str(p).endswith(".png") for p in recs[0].evidence)


def test_tiny_run_sigma_is_pfast_based(tiny_run):
    """D-WP3-6: σ̂ on pFast is below the true-price volatility of the same paths."""
    _, t, _, _ = tiny_run
    v = t.current().metrics.values
    assert v["sigma_hat_p50_realised"] < v["true_vol_bps_realised"]
    assert v["sigma_hat_p50_calm"] < v["sigma_hat_p50_turbulent"]
    assert v["k12_trap_after_feed_blocks"] >= mainnet().as_int("volWindow")  # fact 1.5-3
    assert 0 < v["responsiveness_blocks"] <= 2 * mainnet().as_int("volWindow")


def test_determinism_per_seed():
    st = G.make_study()
    G1.clear_caches()
    a = st.evaluate(mainnet(), env_for(seed=3)).values
    G1.clear_caches()
    b = st.evaluate(mainnet(), env_for(seed=3)).values
    assert a == b

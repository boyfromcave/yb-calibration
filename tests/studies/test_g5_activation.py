"""G5 study (WP-7c): analytics vs Monte Carlo, decision rules on hand-built tables, simulation
confirmation, determinism, and a tiny end-to-end run that recommends every owned parameter."""

from __future__ import annotations

import math

import numpy as np
import pytest

from ybcal.config import Policy
from ybcal.model import vkernels as V
from ybcal.params.paramset import mainnet, regtest
from ybcal.params.registry import params_for_group
from ybcal.sim import activation as act
from ybcal.studies import g5_activation as G
from ybcal.studies.base import Budget, Env, Metrics, ResultTable, load_study, missing_recommendations

TINY = Budget("quick", paths=8, block_horizon_days=3, hour_horizon_years=1.0, grid_points=3, lhs_samples=4,
              halving_rounds=1, scenario_set="core", morris_trajectories=2, sobol_samples=8, max_minutes=1)


def env_for(tmp_path=None, seed=5, policy=None) -> Env:
    return Env(policy or Policy(), TINY, seed=seed, out_dir=str(tmp_path) if tmp_path is not None else None)


# ---------------------------------------------------------------------------------------------------
# Analytics


def test_false_abandon_bound_dominates_monte_carlo():
    """At regtest-sized windows the false-abandonment bound is ≥ the simulated frequency."""
    W, EF, ER = 32, 16, 19
    ps = regtest().replace(signalWindow=W, enforcementFloor=EF, enforcementResume=ER, participationFloor=19,
                           activationThreshold=24)
    rng = np.random.default_rng(1)
    for p, A in ((0.55, 40), (0.57, 70), (0.6, 33)):
        n, paths = 20_000, 40
        sig = rng.random((paths, n + W)) < p
        c = V.signal_counts(sig, W)[:, W:]
        h = V.hysteresis(c < EF, c < ER, False)
        ab = act.abandoned(h, A)
        starts = ab[:, 1:] & ~ab[:, :-1]
        sim_rate = starts.sum() / (paths * n)                          # abandonments per block
        bound = G.false_abandon_probability(ps, p, A, blocks_per_year=1)   # = expected count per block
        assert sim_rate <= bound * 1.1 + 1e-5, (p, A, sim_rate, bound)
    # mainnet at the policy share: astronomically small, monotone in A, 1 for A ≤ 0
    P = mainnet()
    assert G.false_abandon_probability(P, 0.8) < 1e-12
    assert G.false_abandon_probability(P, 0.55, 2016) >= G.false_abandon_probability(P, 0.55, 34_560)
    assert G.false_abandon_probability(P, 0.8, 0) == 1.0
    # share samples are averaged
    s = [0.52, 0.8]
    one = [G.false_abandon_probability(P, x, 4032, blocks_per_year=1) for x in s]
    assert G.false_abandon_probability(P, s, 4032, blocks_per_year=1) == pytest.approx(0.5 * sum(one))


def test_detection_quantile_bound_vs_monte_carlo():
    for W, F, p, pd in ((2016, 1008, 0.8, 0.45), (288, 144, 0.75, 0.4), (64, 32, 0.8, 0.3)):
        b = G.detection_quantile_blocks(p, pd, W, F, 0.95)
        dd = act.detection_delay(p, pd, W, F, n_paths=4000, rng=np.random.default_rng(2))
        q = np.quantile(dd[dd > 0], 0.95)
        assert q <= 1.01 * b + 1 and q >= 0.8 * b, (W, q, b)
    assert math.isinf(G.detection_quantile_blocks(0.8, 0.55, 2016, 1008))   # never below the floor
    assert G.detection_quantile_blocks(0.4, 0.3, 2016, 1008) == 0.0


def test_halted_fraction_estimate_vs_simulation():
    W, F, R = 64, 32, 39
    for p in (0.55, 0.6, 0.66):
        est = float(G.halted_fraction(F, R, W, [p])[0])
        sim = act.simulate_halts(p, W, F, R, 40_000, 32, rng=np.random.default_rng(3))["halted_fraction"]
        lo, hi = float(act.p_count_below(F, p, W)), float(act.p_count_below(R, p, W))
        assert lo - 1e-3 <= sim <= hi + 1e-3
        assert 0.3 * sim - 1e-3 <= est <= hi + 1e-12, (p, est, sim)
        if W * p >= R:                      # fluid regime: within a factor 3; else conservative (≤ hi)
            assert est <= 3 * sim + 0.02, (p, est, sim)


def test_share_samples_synthetic_and_from_pool_log():
    env = env_for()
    s, prov = G.share_samples(env, 2016)
    assert prov == "judgement" and len(s) == G.N_SHARE_SAMPLES and np.all(np.diff(s) >= 0)
    assert 0.6 < np.median(s) < 0.95
    from ybcal.data.loaders import PoolShareLog

    rng = np.random.default_rng(0)
    n = 2016 * 20
    pool = rng.choice(4, size=n, p=[0.5, 0.3, 0.15, 0.05]).astype(np.int32)
    log = PoolShareLog(np.arange(n, dtype=np.int64), pool, ("a", "b", "c", "d"))
    env2 = Env(Policy(), TINY, seed=5, data={"pool_shares": log})
    s2, prov2 = G.share_samples(env2, 2016)
    assert prov2 == "real-data" and abs(np.median(s2) - 0.8) < 0.03
    m = load_study("G5").evaluate(mainnet(), env2)
    assert m.provenance == "real-data" and m.meta["share_provenance"] == "real-data"


# ---------------------------------------------------------------------------------------------------
# Decision rules on hand-built tables


def _m(values, cons, prov="judgement"):
    v = {"zero": 0.0, **values}
    return Metrics(v, "zero", True, cons, prov, {})


def test_valve_family_keeps_when_not_material_and_moves_when_violated():
    st = G.G5Study()
    fam = next(f for f in st.families() if f.name == "valve")
    base = mainnet()
    t = ResultTable(base)
    for v, ok in ((6, True), (5, True), (4, False), (8, True)):
        t.add(base.replace(valveBlocks=v), _m({"valve.split_blocks": float(v)},
                                              {"valve_minority": ok, "valve_natural": True}))
    row, verdict, _ = st.decide_family(t, fam, Policy())
    assert verdict == "KEEP" and row.params["valveBlocks"] == 6          # 16.7 % < 20 %
    row, verdict, _ = st.decide_family(t, fam, Policy(materiality=0.1))
    assert verdict == "CHANGE" and row.params["valveBlocks"] == 5
    # current infeasible: never kept
    t2 = ResultTable(base)
    for v, ok in ((6, False), (7, True), (9, True)):
        t2.add(base.replace(valveBlocks=v), _m({"valve.split_blocks": float(v)},
                                               {"valve_minority": ok, "valve_natural": True}))
    row, verdict, _ = st.decide_family(t2, fam, Policy())
    assert verdict == "CHANGE" and row.params["valveBlocks"] == 7


def test_threshold_family_ties_keep_current_and_rows_are_sliced():
    st = G.G5Study()
    fam = next(f for f in st.families() if f.name == "participation")
    base = mainnet()
    t = ResultTable(base)
    cons = {"false_halt": True, "flaps": True, "detect_part": True}
    t.add(base, _m({"fh.hours_q": 0.0}, cons))
    t.add(base.replace(participationFloor=1100), _m({"fh.hours_q": 0.0}, cons))
    # a row of another family must not leak into this one
    t.add(base.replace(valveBlocks=3), _m({"fh.hours_q": -5.0}, cons))
    row, verdict, _ = st.decide_family(t, fam, Policy())
    assert verdict == "KEEP" and row.params == base
    assert len(G.family_rows(t, fam)) == 2


def test_window_change_carries_scaled_thresholds():
    st = G.G5Study()
    base = mainnet()
    out = st.adjust_changes({"signalWindow": 4032}, {}, base)
    assert out == {"signalWindow": 4032, "activationThreshold": 3024, "participationFloor": 2420,
                   "enforcementFloor": 2016, "enforcementResume": 2420}
    assert not base.replace(out).check()


# ---------------------------------------------------------------------------------------------------
# End to end


@pytest.fixture(scope="module")
def tiny_run(tmp_path_factory):
    from ybcal.optimize.runner import run_group

    out = tmp_path_factory.mktemp("g5")
    env = Env(Policy(), TINY, seed=11, out_dir=str(out))
    return run_group(load_study("G5"), mainnet(), env, workers=1), out


def test_every_param_recommended_with_full_fields(tiny_run):
    run, out = tiny_run
    recs = run.recommendations
    study = load_study("G5")
    assert study.params == params_for_group("G5")
    assert missing_recommendations(study, recs) == []
    for r in recs:
        assert r.verdict in ("KEEP", "CHANGE", "PROVISIONAL", "BLOCKED")
        assert r.rule and r.binding and r.explanation
        assert r.metrics["current"] is not None and "recommended" in r.metrics
        assert r.sensitivity.get("sentence")
        assert r.provenance in ("judgement", "real-data", "synthetic")
        assert r.evidence and all(p.exists() for p in r.evidence)
        assert str(r.evidence[0]).startswith(str(out / "g5"))
    by = {r.param: r for r in recs}
    # at the default policy every current value passes and nothing is materially better
    assert all(by[p].verdict == "KEEP" for p in by), {p: by[p].verdict for p in by}
    assert by["valveBlocks"].change_path == "patch-release"
    assert any("patch-release" in n for n in by["valveBlocks"].notes)
    assert len([p for p in run.recommendations[0].evidence if p.suffix == ".png"]) == 2


def test_simulation_confirms_analytics_at_the_chosen_point(tiny_run):
    run, _ = tiny_run
    sim = run.recommendations[0].metrics["simulation"]["current"]
    assert sim["activation_blocks_sim_median"] == sim["activation_blocks_analytic"] == 2015 + 2016
    assert sim["false_halt_hours_per_year_sim"] == 0.0
    assert sim["probe_consistent"] and sim["detect_consistent"]
    assert sim["scenario_detected_fraction"] == 1.0
    assert sim["scenario_detect_blocks_p95"] <= 1.05 * sim["detect_enf_p95_bound"]


def test_low_share_policy_moves_or_blocks():
    """At an expected share of 0.64 the policy cannot be met by the current set: nothing is KEEP-ed
    silently; the valve (excluded) lengthens."""
    from ybcal.optimize.runner import run_group

    env = env_for(policy=Policy(expected_enforcing_share=0.64, owner_pinned={}))
    recs = {r.param: r for r in run_group(load_study("G5"), mainnet(), env, workers=1).recommendations}
    assert recs["valveBlocks"].recommended > 6
    assert recs["participationFloor"].verdict in ("BLOCKED", "CHANGE")


def test_evaluate_is_deterministic():
    st = load_study("G5")
    a = st.evaluate(mainnet(), env_for(seed=3))
    G._SHARE_CACHE.clear()
    b = st.evaluate(mainnet(), env_for(seed=3))
    assert dict(a.values) == dict(b.values) and dict(a.constraints) == dict(b.constraints)


# ---------------------------------------------------------------------------------------------------
# Real pool landscape (wave 2, D-RD-ACT-1..5)


def _lumpy_log(days=40, seed=0):
    """A 4-operator log with a regime switch: 'flex' hash moves from key b to key c mid-sample (the
    shape of the real unidentified-key → zpool switch)."""
    from ybcal.data.loaders import PoolShareLog

    rng = np.random.default_rng(seed)
    n = days * 1152
    half = n // 2
    p1, p2 = [0.52, 0.25, 0.0, 0.23], [0.52, 0.0, 0.25, 0.23]
    pool = np.concatenate([rng.choice(4, size=half, p=p1), rng.choice(4, size=n - half, p=p2)])
    pool = pool.astype(np.int32)
    return PoolShareLog(np.arange(n, dtype=np.int64), pool, ("big", "flexA", "flexB", "rest"))


def test_coalition_choice_auto_and_explicit():
    from ybcal.sim.landscape import Landscape

    log = _lumpy_log()
    land = Landscape.from_log(log, top=4, names={k: k for k in log.keys})
    auto = G.enforcing_coalition(land, Policy(expected_enforcing_share=0.70))
    assert auto[0] == "big" and len(auto) >= 2
    pinned = G.enforcing_coalition(land, Policy(enforcing_pools=("big", "rest")))
    assert set(pinned) == {"big", "rest"}
    with pytest.raises(ValueError):
        G.enforcing_coalition(land, Policy(enforcing_pools=("nobody",)))


def test_landscape_metrics_replace_the_binomial_mixture():
    """With a pool-share log the halt metrics come from the per-block replay of the named coalition: a
    coalition that loses a key mid-sample halts (the binomial mixture of the old code could not see
    the order of blocks), the explicit stable coalition does not, and the valve's attack metric is
    present when valve_attack_days is set."""
    log = _lumpy_log()
    base = dict(owner_pinned={}) if "owner_pinned" in Policy.field_names() else {}
    lumpy = Policy(enforcing_pools=("big", "flexA"), **base)
    stable = Policy(enforcing_pools=("big", "rest"), activation_reach_days=30.0, valve_attack_days=5.0,
                    **base)
    st = load_study("G5")
    m1 = st.evaluate(mainnet(), Env(lumpy, TINY, seed=3, data={"pool_shares": log}))
    m2 = st.evaluate(mainnet(), Env(stable, TINY, seed=3, data={"pool_shares": log}))
    assert m1.provenance == "real-data" and m1.meta["landscape"]
    assert m1.values["fh.enf_hours"] > 100 and not m1.constraints["false_halt"]
    assert m2.values["fh.hours"] < m1.values["fh.hours"]
    assert 0.0 <= m2.values["act.p_reach"] <= 1.0 and "valve.attack_trip" in m2.values
    assert "valve.attack_trip" not in m1.values
    assert m2.meta["coalition"] == ["big", "rest"]


def test_scenario_confirmation_covers_the_real_pool_family():
    out = G.scenario_confirmation(mainnet(), np.random.default_rng(0), 4)
    assert {"hashrate-drop-45", "real-pools-ninja-offline-7d", "real-pools-ninja-rogue"} <= set(out)
    rogue = out["real-pools-ninja-rogue"]
    assert rogue["minority_enforced_blocks"] > 0 and rogue["detected_fraction"] == 0.0  # faked signals
    assert out["real-pools-ninja-offline-45d"]["abandoned_fraction"] == 1.0  # > abandonBlocks
    assert out["real-pools-launch-hop"]["locked_fraction"] == 1.0  # spurious lock-in

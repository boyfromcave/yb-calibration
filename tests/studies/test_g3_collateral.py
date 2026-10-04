"""G3 study (WP-7b): shared helpers vs WP-4/kernels, decision rules on hand-built tables (incl. the
BLOCKED least-violating path), determinism, and a tiny end-to-end run recommending every parameter."""

from __future__ import annotations

import itertools
import math

import numpy as np
import pytest

from ybcal.config import Policy
from ybcal.model import kernels as K
from ybcal.params.paramset import mainnet
from ybcal.params.registry import params_for_group
from ybcal.sim import metrics as M
from ybcal.studies import g3_collateral as G3
from ybcal.studies.base import Budget, Env, Metrics, ResultTable, load_study, missing_recommendations

TINY = Budget(
    "quick",
    paths=8,
    block_horizon_days=3,
    hour_horizon_years=5.2,
    grid_points=3,
    lhs_samples=4,
    halving_rounds=1,
    scenario_set="core",
    morris_trajectories=2,
    sobol_samples=8,
    max_minutes=1,
)


def env_for(tmp_path=None, seed=11, policy=None) -> Env:
    return Env(policy or Policy(), TINY, seed=seed, out_dir=str(tmp_path) if tmp_path is not None else None)


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    """One tiny end-to-end G3 run (2 paths per preset), shared by the tests below."""
    out = tmp_path_factory.mktemp("g3")
    env = env_for(out)
    st = load_study("G3")
    base = mainnet()
    tab = ResultTable(base)
    for c in st.space(base, env.budget):
        tab.add(c, st.evaluate(c, env))
    recs = st.decide(tab, env.policy)
    for r in recs:
        r.explanation = st.explain(r, tab)
    return env, st, tab, recs, out


# ---------------------------------------------------------------------------------------------------
# Helpers


def test_first_below_matches_brute_force():
    rng = np.random.default_rng(0)
    x = rng.integers(0, 100, 257)
    fb = G3.FirstBelow(x)
    t0 = rng.integers(0, 260, 500)
    lvl = rng.integers(0, 110, 500)
    got = fb.first(t0, lvl)
    for a, l_, g in zip(t0, lvl, got, strict=True):
        idx = [t for t in range(min(a, 257), 257) if x[t] < l_]
        assert g == (idx[0] if idx else 257)


def test_underwater_level_is_exact():
    rng = np.random.default_rng(1)
    coll = rng.integers(10**6, 10**13, 200)
    for theta in (10_000, 10_500, 11_000, 15_000):
        L = G3._underwater_level(coll, theta)
        for c, lv in zip(coll[:40], L[:40], strict=True):
            assert K.is_underwater(int(c), int(lv) - 1, G3.TEST_CENTS, theta)
            assert not K.is_underwater(int(c), int(lv), G3.TEST_CENTS, theta)


def test_heterogeneity_definition():
    assert G3.heterogeneity(np.array([0.0, 0.0, 0.0, 0.0])) == 0.0
    h = G3.heterogeneity(np.array([0.01, 0.02, 0.03, 0.06]))
    assert h == pytest.approx((0.06 - 0.01) / 0.03)
    assert math.isnan(G3.heterogeneity(np.array([np.nan, np.nan])))


def test_agents_from_policy_maps_agent_keys():
    pol = Policy().replace(
        yed_premium_bps=-500, claimant_slippage_bps=250, defector_share=0.1, lost_key_prob=0.02
    )
    cfg = G3.agents_from_policy(pol)
    assert cfg.market.premium_bps == -500 and cfg.claimant.slippage_bps == 250
    assert cfg.owner.defector_share == 0.1 and cfg.owner.lost_key_prob == 0.02
    assert cfg.claimant.min_profit_bps == pol.claimant_min_profit_bps


def test_bad_debt_is_wp4_fast_path(run):
    env, _, _, _, _ = run
    ps = mainnet()
    ens = G3.ensemble(env, ps)
    name = ens.names[0]
    fb = G3.bad_debt(ens, name, ps, 0, sigma="median", term_distribution="uniform", n_terms=8, stride=24)
    ref = M.p_bad_debt_fast(
        ps,
        ens.true[name],
        hour_series=ens.hours[name],
        classes=(0,),
        n_terms=8,
        sigma="median",
        start_stride=24,
    )
    assert fb.p["A"] == ref.p["A"] and fb.p_lock["A"] == ref.p_lock["A"]


# ---------------------------------------------------------------------------------------------------
# Decision rules on hand-built tables


def _ratio_table(pbads: dict[int, float], cur: int = 50_000, tol: float = 0.005) -> ResultTable:
    base = mainnet()
    t = ResultTable(base)
    for r in sorted(pbads, key=lambda v: v != cur):
        ps = base.replace({"baseRatioBps[0]": r})
        p = pbads[r]
        t.add(
            ps,
            Metrics(
                {"zero": 0.0, "ratio.A": float(r), "pbad.A": p, "viol.bad_debt_A": max(0.0, p / tol - 1)},
                "ratio.A",
                True,
                {"bad_debt_A": p <= tol},
            ),
        )
    return t


def test_ratio_rule_keeps_within_materiality_and_changes_on_violation():
    pol = Policy()
    rule = G3.ratio_rule(0)
    # current feasible; 45,000 also feasible but only 10 % less collateral → KEEP
    d = G3.decide_rule(_ratio_table({45_000: 0.004, 50_000: 0.003, 55_000: 0.002}), rule, pol)
    assert d.verdict == "KEEP" and d.row.params["baseRatioBps[0]"] == 50_000
    # 37,500 feasible: 25 % less collateral > materiality → CHANGE to the smallest feasible
    d = G3.decide_rule(_ratio_table({37_500: 0.0049, 40_000: 0.004, 50_000: 0.003}), rule, pol)
    assert d.verdict == "CHANGE" and d.row.params["baseRatioBps[0]"] == 37_500
    # current violates → CHANGE to the smallest feasible regardless of materiality
    d = G3.decide_rule(_ratio_table({50_000: 0.01, 52_500: 0.006, 55_000: 0.004, 60_000: 0.003}), rule, pol)
    assert d.verdict == "CHANGE" and d.row.params["baseRatioBps[0]"] == 55_000


def test_ratio_rule_blocked_returns_least_violating():
    import dataclasses

    t = _ratio_table({50_000: 0.05, 60_000: 0.03, 70_000: 0.02, 80_000: 0.011})
    d = G3.decide_rule(t, dataclasses.replace(G3.ratio_rule(0), env_limits=()), Policy())
    assert d.verdict == "BLOCKED"
    assert d.row.params["baseRatioBps[0]"] == 80_000 and d.least is d.row


def test_ratio_rule_environment_limit_is_least_harm_not_blocked():
    """D-RD-COL-4: an unmeetable class tolerance is an environment limit — least harm (lowest P(bad
    debt)), CHANGE with the exposure and design note G3-DN1, never a silent BLOCKED."""
    pol = Policy()
    t = _ratio_table({50_000: 0.05, 60_000: 0.03, 70_000: 0.02, 80_000: 0.011})
    d = G3.decide_rule(t, G3.ratio_rule(0, pol), pol)
    assert d.verdict == "CHANGE" and d.row.params["baseRatioBps[0]"] == 80_000
    assert d.env and d.env["constraints"] == ["bad_debt_A"] and "G3-DN1" in str(d.env)


def test_verify_rule_keeps_feasible_current():
    rule = G3.Rule(
        "v", ("classMin[0]",), ("classMin[0]",), "verify", primary="zero", constraints=("ok",), kind="verify"
    )
    base = mainnet()
    t = ResultTable(base)
    t.add(base, Metrics({"zero": 0.0}, "zero", True, {"ok": True}))
    t.add(base.replace({"classMin[0]": 30_000}), Metrics({"zero": 0.0}, "zero", True, {"ok": True}))
    assert G3.decide_rule(t, rule, Policy()).verdict == "KEEP"
    t2 = ResultTable(base)
    t2.add(base, Metrics({"zero": 0.0, "viol.ok": 1.0}, "zero", True, {"ok": False}))
    t2.add(base.replace({"classMin[0]": 33_408}), Metrics({"zero": 0.0}, "zero", True, {"ok": True}))
    d = G3.decide_rule(t2, rule, Policy())
    assert d.verdict == "CHANGE" and d.row.params["classMin[0]"] == 33_408


# ---------------------------------------------------------------------------------------------------
# End to end


def test_every_param_recommended(run):
    _, st, _, recs, _ = run
    assert st.params == params_for_group("G3")
    assert not missing_recommendations(st, recs)
    for r in recs:
        assert r.rule and r.binding and r.explanation and r.provenance in ("synthetic", "real-data")
        assert r.verdict in ("KEEP", "CHANGE", "PROVISIONAL", "BLOCKED")
        assert r.verdict != "KEEP" and r.verdict != "CHANGE"  # synthetic-only → PROVISIONAL or BLOCKED
        assert "design_notes" in r.metrics


def test_blocked_ratio_quantifies_tradeoff(run):
    _, _, _, recs, _ = run
    by = {r.param: r for r in recs}
    for c in range(3):
        r = by[f"baseRatioBps[{c}]"]
        tr = r.metrics["tradeoff"]
        assert len(tr) >= 5
        ps = [p for _, p in tr if isinstance(p, float) and math.isfinite(p)]
        assert ps[0] >= ps[-1]  # more collateral, less bad debt
        if r.verdict == "BLOCKED":
            assert r.recommended == max(x for x, _ in tr) or r.metrics["least_violating"]


def test_claim_margin_rises_with_threshold(run):
    _, _, tab, _, _ = run
    rows = sorted(
        (r for r in tab if set(r.delta) <= {"claimThresholdBps"}), key=lambda r: r.params["claimThresholdBps"]
    )
    m = [r.metrics.values["claim.mean_bps"] for r in rows]
    assert all(b >= a - 1e-9 for a, b in itertools.pairwise(m))


def test_emergency_only_below_threshold_and_benefit(run):
    _, _, tab, recs, _ = run
    by = {r.param: r for r in recs}
    assert 10_000 < by["emergencyRatioBps"].recommended < by["claimThresholdBps"].current
    cur = tab.current()
    assert cur.metrics.values["emerg.shortfall"] <= cur.metrics.values["emerg.shortfall_a"] + 1e-12


def test_boundaries_contiguous_and_design_notes(run):
    env, _, tab, recs, out = run
    by = {r.param: r for r in recs}
    assert by["classMin[1]"].recommended == by["classMax[0]"].recommended + 1
    assert by["classMin[2]"].recommended == by["classMax[1]"].recommended + 1
    notes = G3.design_notes(tab, env.policy)
    ids = {n["id"] for n in notes}
    assert {"G3-DN1", "G3-DN2", "G3-DN3", "G3-DN4", "G3-DN5"} <= ids
    for n in notes:
        assert set(n) == {"id", "title", "finding", "evidence", "consequence", "fix", "params"}
    assert (out / "g3" / "g3_results.csv").exists() and (out / "g3" / "g3_bad_debt_vs_ratio.png").exists()


def test_determinism():
    G3.clear_caches()
    st = G3.make_study()
    a = st.evaluate(mainnet(), env_for(seed=3))
    G3.clear_caches()
    b = st.evaluate(mainnet(), env_for(seed=3))
    assert dict(a.values) == dict(b.values) or all(
        (x == y) or (math.isnan(x) and math.isnan(y))
        for x, y in zip(a.values.values(), b.values.values(), strict=True)
    )


def test_shortfall_is_bounded_by_p_bad(run):
    """D-RD-AUD-3: E[max(0, 1 − value/debt)] at claim opening lies in [0, P(bad)]."""
    _, _, tab, _, _ = run
    v = tab.current().metrics.values
    for c in "ABC":
        for m in ("gbm", "merton", "garch", "regime"):
            es, p = v[f"es.{c}.{m}"], v[f"pbad.{c}.{m}"]
            if math.isfinite(p):
                assert 0.0 <= es <= p + 1e-12
                assert (es == 0.0) == (p == 0.0)


def test_term_frontier_days():
    terms = np.array([1152 * 30, 1152 * 60, 1152 * 90])
    worst = [np.array([0.001, 0.004, 0.02]), np.array([0.002, 0.006, 0.01])]
    assert G3.term_frontier_days(worst, terms, 0.005, "worst") == 30.0
    assert G3.term_frontier_days(worst, terms, 0.05, "worst") == 90.0
    assert G3.term_frontier_days(worst, terms, 0.0005, "worst") == 0.0
    assert math.isnan(G3.term_frontier_days([], terms, 0.01, "worst"))


def test_emergency_closure_needs_a_paying_exit(run):
    """D-RD-AUD-4: RED-4(b) closes only when the exit pays at the policy's YED price, so at par it
    closes no more vaults than under a YED discount (the paying set only grows with the discount)."""
    _, _, tab, _, _ = run
    v = tab.current().metrics.values
    assert 0.0 <= v["emerg.b_share"] <= v["emerg.b_share_stress"] + 1e-12
    for k in ("emerg.shortfall", "emerg.shortfall_stress", "emerg.shortfall_a"):
        assert 0.0 <= v[k] <= 1.0

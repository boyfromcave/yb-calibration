"""G6 study (WP-7d): every owned param recommended at a tiny budget, the REG-4 deviation helper vs the
vectorised rule, decision rules on hand-built tables (incl. the BLOCKED least-violating path), the
[agents] policy mapping, fee recomputation and determinism."""

from __future__ import annotations

import math

import numpy as np
import pytest

from ybcal.config import Policy
from ybcal.params.paramset import mainnet
from ybcal.params.registry import params_for_group
from ybcal.sim import fees as F
from ybcal.studies import g6_miners_fees as G
from ybcal.studies.base import Budget, Env, Metrics, ResultTable, load_study, missing_recommendations

TINY = Budget(
    "quick",
    paths=4,
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


@pytest.fixture(scope="module")
def tiny_run(tmp_path_factory):
    from ybcal.optimize.runner import run_group

    out = tmp_path_factory.mktemp("g6")
    env = Env(Policy(), TINY, seed=11, out_dir=str(out))
    return run_group(load_study("G6"), mainnet(), env, workers=1), out


def test_every_param_recommended_with_full_fields(tiny_run):
    run, out = tiny_run
    study = load_study("G6")
    assert study.params == params_for_group("G6")
    assert "attestFeeBps" in study.params
    assert missing_recommendations(study, run.recommendations) == []
    by = {r.param: r for r in run.recommendations}
    for r in run.recommendations:
        assert r.verdict in ("KEEP", "CHANGE", "PROVISIONAL", "BLOCKED")
        assert r.rule and r.binding and r.explanation and r.sensitivity.get("sentence")
        assert r.evidence and all(p.exists() for p in r.evidence)
        assert str(r.evidence[0]).startswith(str(out / "g6"))
        assert isinstance(r.metrics.get("design_notes"), list)
        assert r.param in r.explanation
    for p in ("nPenalty", "accuracyWindow", "payeeTiltBps", "nReg"):
        assert by[p].change_path == "patch-release"
        assert any("patch-release" in n for n in by[p].notes)
    # synthetic-only judgement → provisional (or blocked); feeMin rests on exact policy arithmetic
    for p in ("deviationBps", "peerLag", "feeBps"):
        assert by[p].provenance == "synthetic" and by[p].verdict in ("PROVISIONAL", "BLOCKED")
    assert by["feeMin"].provenance == "judgement"
    pngs = sorted(p.name for p in (out / "g6").glob("*.png"))
    assert pngs == ["g6_fees.png", "g6_judgement.png"]


def test_fee_min_edge_redeem_finding(tiny_run):
    """At worst_price_usd a minMint class-C vault holds 3 YEC, so feeMin 0.5 YEC is 16.7 % of it — above
    the 9.1 % margin left at the claim threshold: the verify rule moves feeMin to the nearest value that
    passes (0.2 YEC on the 0.1-YEC grid)."""
    run, _ = tiny_run
    by = {r.param: r for r in run.recommendations}
    cur = run.table.current().metrics
    assert cur.values["fee.edge_fee_share_max"] == pytest.approx(0.5 / 3)
    assert not cur.constraints["edge_redeem"]
    assert by["feeMin"].recommended == 20_000_000 and by["feeMin"].verdict == "CHANGE"


def test_fee_share_is_size_free_and_design_note(tiny_run):
    run, _ = tiny_run
    cur = run.table.current().metrics.values
    # 2 · feeBps · ratio (+ AFEE-1 on the mint leg): A 5×, B 4×, C 3× at 25 bps and 2,500 bps
    assert cur["fee.share_minmint_A"] == pytest.approx(0.0025 * 5 * 2.25, rel=1e-3)
    assert cur["fee.share_minmint_C"] == pytest.approx(0.0025 * 3 * 2.25, rel=1e-3)
    notes = G.design_notes(run.table, Policy())
    assert any("FEE-1 base" in n for n in notes)


def test_reg4_deviations_match_judgement_series():
    rng = np.random.default_rng(3)
    for _ in range(5):
        P, n = 2, 300
        price = (400_000 * np.exp(rng.normal(0, 0.01, (P, n)))).astype(np.int64)
        present = rng.random((P, n)) < 0.7
        tp = np.where(present, price, 0)
        lag, pmin = int(rng.integers(2, 8)), int(rng.integers(1, 6))
        dev_bps, band = int(rng.integers(50, 300)), int(rng.integers(20, 100))
        ps = {"peerLag": lag, "peerMin": pmin, "deviationBps": dev_bps, "accuracyBandBps": band}
        js = F.judgement_series(ps, tp, present)
        cnt, _, dev, judged = G.reg4_deviations(tp, lag)
        ev = (dev >= 0) & judged & (cnt >= pmin)
        assert np.array_equal(ev, js.evaluated)
        assert np.array_equal(ev & (dev <= band), js.in_band)
        assert np.array_equal(ev & (dev > dev_bps), js.penalized)


def test_agents_config_maps_policy_keys():
    pol = Policy().replace(
        yed_premium_bps=-200, claimant_slippage_bps=250, defector_share=0.1, lost_key_prob=0.02
    )
    cfg = G.agents_config(pol)
    assert cfg.market.premium_bps == -200
    assert cfg.claimant.slippage_bps == 250
    assert cfg.owner.defector_share == 0.1 and cfg.owner.lost_key_prob == 0.02
    assert cfg.minter.mints_per_day == pol.adoption_scenarios[pol.adoption_case]["mints_per_day"]


def test_fee_revenue_linear_in_fee_bps(tiny_run):
    env = Env(Policy(), TINY, seed=11)
    res = G.revenue_book(env, mainnet())
    a = G.fee_revenue_for(res, 10_000_000, 25, 2500)
    b = G.fee_revenue_for(res, 10_000_000, 50, 2500)
    assert b["pool_usd_day"] == pytest.approx(2 * a["pool_usd_day"], rel=0.02)
    assert b["attest_usd_day"] == pytest.approx(2 * a["attest_usd_day"], rel=0.02)
    assert G.fee_revenue_for(res, 10_000_000, 25, 0)["attest_usd_day"] == 0.0


# ---------------------------------------------------------------------------------------------------
# Decision rules on hand-built tables


def _row(table, ps, values, cons, prov="synthetic"):
    v = {"zero": 0.0, **values}
    table.add(ps, Metrics(v, "zero", True, cons, prov, {}))


def _fam(name):
    return next(f for f in G._fams() if f.name == name)


def test_fees_blocked_returns_least_violating():
    base = mainnet()
    t = ResultTable(base)
    cons_bad = {"fee_share": False, "pool_revenue": True, "attestor_revenue": False}
    _row(
        t,
        base,
        {
            "fee.share_minmint": 0.028,
            "viol.fee_share": 0.4,
            "viol.attestor_revenue": 0.5,
            "fee.pool_usd_month": 300,
            "fee.attestor_usd_month": 25,
        },
        cons_bad,
    )
    _row(
        t,
        base.replace({"feeBps": 15, "attestFeeBps": 5000}),
        {
            "fee.share_minmint": 0.019,
            "viol.fee_share": 0.0,
            "viol.attestor_revenue": 0.2,
            "fee.pool_usd_month": 180,
            "fee.attestor_usd_month": 40,
        },
        {"fee_share": True, "pool_revenue": True, "attestor_revenue": False},
    )
    _row(
        t,
        base.replace({"feeBps": 50, "attestFeeBps": 5000}),
        {
            "fee.share_minmint": 0.06,
            "viol.fee_share": 2.0,
            "viol.attestor_revenue": 0.0,
            "fee.pool_usd_month": 600,
            "fee.attestor_usd_month": 120,
        },
        {"fee_share": False, "pool_revenue": True, "attestor_revenue": True},
    )
    _row(
        t,
        base.replace({"feeBps": 20}),
        {
            "fee.share_minmint": 0.0225,
            "viol.fee_share": 0.125,
            "viol.attestor_revenue": 0.0,
            "fee.pool_usd_month": 250,
            "fee.attestor_usd_month": 50,
        },
        {"fee_share": False, "pool_revenue": True, "attestor_revenue": True},
    )
    st = G.make_study()
    row, verdict, reason = st.decide_family(t, _fam("fees"), Policy())
    assert verdict == "BLOCKED"
    # attestFeeBps is owner-pinned (D-3): the least-violating point keeps it; the free optimum is evidence
    assert (row.params["feeBps"], row.params["attestFeeBps"]) == (20, 2500)
    assert "least-violating" in reason
    assert st._pinned_evidence.startswith("Owner-pinned attestFeeBps = 2500")


def test_fees_feasible_change_and_keep_toward_current():
    base = mainnet()
    st = G.make_study()
    ok = {"fee_share": True, "pool_revenue": True, "attestor_revenue": True}
    t = ResultTable(base)
    _row(t, base, {"fee.share_minmint": 0.018}, ok)
    _row(t, base.replace({"feeBps": 20}), {"fee.share_minmint": 0.016}, ok)  # 11 % better: within 20 %
    row, verdict, _ = st.decide_family(t, _fam("fees"), Policy())
    assert verdict == "KEEP" and row.params["feeBps"] == 25
    t2 = ResultTable(base)
    _row(t2, base, {"fee.share_minmint": 0.028}, {**ok, "fee_share": False})  # violating current
    _row(t2, base.replace({"feeBps": 15}), {"fee.share_minmint": 0.017}, ok)
    _row(t2, base.replace({"feeBps": 10}), {"fee.share_minmint": 0.011}, {**ok, "attestor_revenue": False})
    row, verdict, _ = st.decide_family(t2, _fam("fees"), Policy())
    assert verdict == "CHANGE" and row.params["feeBps"] == 15


def test_deviation_smallest_feasible_and_peer_min_largest():
    base = mainnet()
    st = G.make_study()
    t = ResultTable(base)
    good = {
        "dev_margin": True,
        "false_penalty": True,
        "false_penalty_stale": True,
        "liar_detect": True,
        "not_evaluated": True,
    }
    _row(t, base, {"judge.dev_bps": 1000, "judge.peer_min": 5}, good)
    _row(t, base.replace(deviationBps=500), {"judge.dev_bps": 500, "judge.peer_min": 5}, good)
    _row(
        t,
        base.replace(deviationBps=400),
        {"judge.dev_bps": 400, "judge.peer_min": 5},
        {**good, "dev_margin": False},
    )
    _row(t, base.replace(peerMin=9), {"judge.dev_bps": 1000, "judge.peer_min": 9}, good)
    _row(
        t,
        base.replace(peerMin=12),
        {"judge.dev_bps": 1000, "judge.peer_min": 12},
        {**good, "not_evaluated": False},
    )
    row, verdict, _ = st.decide_family(t, _fam("deviation"), Policy())
    assert verdict == "CHANGE" and row.params["deviationBps"] == 500
    row, verdict, _ = st.decide_family(t, _fam("peer_min"), Policy())
    assert verdict == "CHANGE" and row.params["peerMin"] == 9


def test_accuracy_rule_target_and_materiality():
    base = mainnet()
    st = G.make_study()
    t = ResultTable(base)
    _row(t, base, {"judge.acc_target": 280.0}, {})
    _row(t, base.replace(accuracyBandBps=250), {"judge.acc_target": 280.0}, {})
    row, verdict, _ = st.decide_family(t, _fam("accuracy"), Policy())
    assert verdict == "KEEP" and row.params["accuracyBandBps"] == 300  # 7 % from target
    t2 = ResultTable(base)
    _row(t2, base, {"judge.acc_target": 100.0}, {})
    _row(t2, base.replace(accuracyBandBps=100), {"judge.acc_target": 100.0}, {})
    row, verdict, _ = st.decide_family(t2, _fam("accuracy"), Policy())
    assert verdict == "CHANGE" and row.params["accuracyBandBps"] == 100


def test_peer_lag_change_rechecks_peer_min():
    st = G.make_study()
    st._resolved = {}
    base = mainnet()
    t = ResultTable(base)
    _row(t, base, {}, {})
    row = t.rows[0]
    row.metrics.meta.update({"quote_density": 0.8, "max_not_evaluated_prob": 0.05})  # type: ignore[union-attr]
    chosen = {"peerMin": (None, row, "CHANGE", "")}
    out = st.adjust_changes({"peerLag": 4, "peerMin": 12}, chosen, base)
    # 7 peer slots at 80 % density: P(< m) ≤ 5 % only for m ≤ 4
    assert out["peerMin"] == 4 and "peerMin" in st._resolved
    assert G._p_bin_below(4, 7, 0.8) <= 0.05 < G._p_bin_below(5, 7, 0.8)


def test_determinism():
    st = G.make_study()
    env = Env(Policy(), TINY, seed=21)
    a = st.evaluate(mainnet(), env).values
    G.clear_caches()
    from ybcal.studies import g1_price_windows as G1

    G1.clear_caches()
    b = st.evaluate(mainnet(), env).values
    for k in a:
        if isinstance(a[k], float) and math.isnan(a[k]):
            assert math.isnan(b[k])
        else:
            assert a[k] == b[k], k


def test_peer_min_must_hold_at_the_participation_floor():
    """D-RD-AUD-8: the quote density at the participation floor (60 % of the window, expected 80 %)
    is 3/4 of the calm stream's, and peerMin must leave P(not evaluated) within policy there too."""
    from ybcal.params.paramset import mainnet
    from ybcal.studies import g6_miners_fees as G6

    ps, pol = mainnet(), Policy()
    assert G6.floor_density(0.8, ps, pol) == pytest.approx(0.8 * (1210 / 2016) / 0.8)
    fam = next(f for f in G6.make_study().families() if f.name == "peer_min")
    assert "not_evaluated_floor" in fam.constraints
    # at density 0.8 → floor 0.60, 19 peers: peerMin 12 fails at the floor, 7 passes
    d = G6.floor_density(0.8, ps, pol)
    assert G6._p_bin_below(12, 19, d) > pol.max_not_evaluated_prob
    assert G6._p_bin_below(7, 19, d) <= pol.max_not_evaluated_prob


def test_fees_attestor_floor_environment_limited():
    """D-RD-ATT-8: when only the attestor floor is unmeetable, pay attestors the most the cap allows."""
    base = mainnet()
    t = ResultTable(base)
    bad = {"fee_share": False, "pool_revenue": True, "attestor_revenue": False}
    _row(t, base, {"fee.share_minmint": 0.028, "fee.attestor_usd_month": 45}, bad)
    for fb, share, att in ((10, 0.011, 25), (15, 0.017, 37)):
        _row(t, base.replace({"feeBps": fb}), {"fee.share_minmint": share, "fee.attestor_usd_month": att},
             {"fee_share": True, "pool_revenue": True, "attestor_revenue": False})
    _row(t, base.replace({"feeBps": 20}), {"fee.share_minmint": 0.0225, "fee.attestor_usd_month": 50},
         {"fee_share": False, "pool_revenue": True, "attestor_revenue": True})
    st = G.make_study()
    row, verdict, reason = st.decide_family(t, _fam("fees"), Policy())
    assert verdict == "CHANGE" and row.params["feeBps"] == 15 and row.params["attestFeeBps"] == 2500
    assert st._fee_env["note"] == "G6-ENV-1" and "attestor revenue floor" in reason

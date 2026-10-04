"""G7 study (WP-7d): every owned param recommended at a tiny budget (recapRatioBps = 2 × halt), design
notes, decision rules on hand-built tables (incl. BLOCKED), the system tolerance and drawdown curve,
liquidation-demand accounting and determinism."""

from __future__ import annotations

import math

import pytest

from ybcal.config import Policy
from ybcal.params.paramset import mainnet
from ybcal.params.registry import params_for_group
from ybcal.studies import g7_supply_halts as G
from ybcal.studies.base import Budget, Env, Metrics, ResultTable, load_study, missing_recommendations

TINY = Budget(
    "quick",
    paths=16,
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

    out = tmp_path_factory.mktemp("g7")
    env = Env(Policy(), TINY, seed=11, out_dir=str(out))
    return run_group(load_study("G7"), mainnet(), env, workers=1), out


def test_every_param_recommended_with_full_fields(tiny_run):
    run, out = tiny_run
    study = load_study("G7")
    assert study.params == params_for_group("G7")
    assert missing_recommendations(study, run.recommendations) == []
    by = {r.param: r for r in run.recommendations}
    for r in run.recommendations:
        assert r.verdict in ("KEEP", "CHANGE", "PROVISIONAL", "BLOCKED")
        assert r.rule and r.binding and r.explanation and r.sensitivity.get("sentence")
        assert r.evidence and all(p.exists() for p in r.evidence)
        assert str(r.evidence[0]).startswith(str(out / "g7"))
        assert isinstance(r.metrics["design_notes"], list)
        if r.param == "supplyCapBps":  # owner-pinned (W20, D-RD-INF-2): KEEP whatever the data
            assert r.verdict == "KEEP" and r.metrics["owner_pin"]["ref"].startswith("W20")
            continue
        if r.param == "globalRatioHaltBps":  # owner-pinned in effect (W16 + W20, D-RD-ORA-7)
            assert r.verdict == "KEEP" and r.metrics["owner_pin"]["ref"].startswith("W16")
            continue
        assert r.provenance == "synthetic" and r.verdict in ("PROVISIONAL", "BLOCKED")
    assert by["recapRatioBps"].recommended == 2 * by["globalRatioHaltBps"].recommended
    assert by["globalRatioHaltBps"].recommended < 30_000
    pngs = sorted(p.name for p in (out / "g7").glob("*.png"))
    assert pngs == ["g7_halts.png", "g7_supply_cap.png"]


def test_early_cap_design_note(tiny_run):
    """Fact 1.5-2: with issuedZat from startHeight and class A bypassing the cap, B/C stay closed far
    longer than max_class_closed_days at the shipped values → a design note naming the issuedZat origin."""
    run, _ = tiny_run
    cur = run.table.current().metrics.values
    assert cur["cap.closed_days_bc"] > Policy().max_class_closed_days
    notes = G.design_notes(run.table, Policy())
    assert any("issuedZat" in n and "early supply cap" in n for n in notes)
    # a lenient owner who tolerates a year of closure gets no early-cap note
    lenient = Policy().replace(max_class_closed_days=400.0)
    assert not any("early supply cap" in n for n in G.design_notes(run.table, lenient))


def test_demand_split_and_halt_metrics(tiny_run):
    run, _ = tiny_run
    for r in run.table:
        v = r.metrics.values
        assert v["liq.demand_bound_usd"] <= v["liq.demand_total_usd"] + 1e-6
        assert v["liq.demand_exempt_usd"] <= v["liq.demand_total_usd"] + 1e-6
        assert 0 <= v["div.recall"] <= 1 and 0 <= v["div.precision"] <= 1
    # HALT-3 does not fire during the +200 % rally (fact 1.5-6: it fires on falls only)
    assert all(
        r.metrics.values["div.fp.rally"] == 0.0 for r in run.table if r.params["divergenceBps"] >= 1000
    )


def test_system_tolerance_and_drawdown_curve():
    pol = Policy()
    tol = G.system_tolerance(pol, mainnet())
    # outstanding-debt weights 0.4·69k : 0.4·262k : 0.2·1.26M blocks → between B's and C's tolerance
    assert 0.01 < tol < 0.02
    env = Env(pol, TINY, seed=3)
    ps = [G.sys_bad_prob(env, h, 34_560) for h in (12_500, 20_000, 30_000)]
    assert ps[0] >= ps[1] >= ps[2]
    assert ps[0] > 0.05


def _row(table, ps, values, cons):
    table.add(ps, Metrics({"zero": 0.0, **values}, "zero", True, cons, "synthetic", {}))


def _fam(name):
    return next(f for f in G._fams() if f.name == name)


def test_divergence_rules():
    base = mainnet()
    st = G.make_study()
    t = ResultTable(base)
    _row(t, base, {"div.f1": 0.88}, {"recall": False})
    _row(t, base.replace(divergenceBps=1500), {"div.f1": 0.97}, {"recall": True})
    _row(t, base.replace(divergenceBps=1000), {"div.f1": 0.94}, {"recall": True})
    row, verdict, _ = st.decide_family(t, _fam("divergence"), Policy())
    assert verdict == "CHANGE" and row.params["divergenceBps"] == 1500
    # nothing meets the recall floor → BLOCKED, current kept
    t2 = ResultTable(base)
    _row(t2, base, {"div.f1": 0.88}, {"recall": False})
    _row(t2, base.replace(divergenceBps=1500), {"div.f1": 0.9}, {"recall": False})
    row, verdict, _ = st.decide_family(t2, _fam("divergence"), Policy())
    assert verdict == "BLOCKED" and row.params["divergenceBps"] == 2000
    # a feasible current within materiality is kept (ties toward current)
    t3 = ResultTable(base)
    _row(t3, base, {"div.f1": 0.90}, {"recall": True})
    _row(t3, base.replace(divergenceBps=1500), {"div.f1": 0.95}, {"recall": True})
    row, verdict, _ = st.decide_family(t3, _fam("divergence"), Policy())
    assert verdict == "KEEP" and row.params["divergenceBps"] == 2000


def test_cap_and_halt_rules():
    base = mainnet()
    st = G.make_study()
    t = ResultTable(base)
    _row(t, base, {"cap.bc_refused": 0.9}, {"depth_bound": True})
    _row(t, base.replace(supplyCapBps=2500), {"cap.bc_refused": 0.3}, {"depth_bound": True})
    _row(t, base.replace(supplyCapBps=4000), {"cap.bc_refused": 0.1}, {"depth_bound": False})
    row, verdict, _ = st.decide_family(t, _fam("cap"), Policy())
    assert verdict == "CHANGE" and row.params["supplyCapBps"] == 2500
    # D-RD-AUD-5: a larger cap that admits (almost) no more B/C demand is no improvement
    t1 = ResultTable(base)
    _row(t1, base, {"cap.bc_refused": 0.91}, {"depth_bound": True})
    _row(t1, base.replace(supplyCapBps=2000), {"cap.bc_refused": 0.90}, {"depth_bound": True})
    row, verdict, _ = st.decide_family(t1, _fam("cap"), Policy())
    assert verdict == "KEEP" and row.params["supplyCapBps"] == 1500
    h = ResultTable(base)
    ok = {"halt_sys_bad": True, "halt_below_floor": True, "halt2_timely": True}
    _row(h, base, {"halt.false_calm": 0.1}, {**ok, "halt_sys_bad": False})
    _row(h, base.replace(globalRatioHaltBps=27_500), {"halt.false_calm": 0.2}, ok)
    _row(h, base.replace(globalRatioHaltBps=20_000), {"halt.false_calm": 0.0}, {**ok, "halt_sys_bad": False})
    row, verdict, _ = st.decide_family(h, _fam("halt"), Policy())
    assert verdict == "CHANGE" and row.params["globalRatioHaltBps"] == 27_500
    assert row.params["recapRatioBps"] == 55_000


def test_determinism():
    st = G.make_study()
    env = Env(Policy(), TINY, seed=21)
    a = st.evaluate(mainnet(), env).values
    from ybcal.studies import g1_price_windows as G1
    from ybcal.studies import g6_miners_fees as G6

    G6.clear_caches()
    G1.clear_caches()
    b = st.evaluate(mainnet(), env).values
    for k in a:
        assert (math.isnan(a[k]) and math.isnan(b[k])) or a[k] == b[k], k


def test_cap_keeps_current_when_the_depth_bound_is_uninformative():
    """D-RD-AUD-5: with no cap-bound debt at any swept cap (all classes ≥ recapRatioBps) the "largest
    cap" rule has no evidence: KEEP instead of the top of the grid."""
    base = mainnet()
    st = G.make_study()
    t = ResultTable(base)
    none = {"cap.bound_debt_usd": 0.0, "cap.bound_uncensored_share": math.nan, "cap.informative": 0.0}
    _row(t, base, {"cap.bc_refused": 0.0, **none}, {"depth_bound": True})
    _row(t, base.replace(supplyCapBps=5000), {"cap.bc_refused": 0.0, **none}, {"depth_bound": True})
    row, verdict, reason = st.decide_family(t, _fam("cap"), Policy())
    assert verdict == "KEEP" and row.params["supplyCapBps"] == 1500 and "uninformative" in reason


def test_censored_cap_bound_debt_is_unverified(tiny_run):
    """D-RD-AUD-5: a cap whose cap-bound debt mostly opens its claim path beyond the book cannot pass
    the depth bound, however small the (censored) measured demand."""
    run, _ = tiny_run
    lim = G.JUDGEMENT["cap_min_uncensored_share"]
    for r in run.table:
        v, c = r.metrics.values, r.metrics.constraints
        unc = v["cap.bound_uncensored_share"]
        if v["cap.bound_debt_usd"] > 0 and (not math.isfinite(unc) or unc < lim):
            assert c["depth_bound"] is False and v["viol.depth_bound"] > 0


def test_calm_halt3_hours_share_the_availability_budget(tiny_run):
    """D-RD-AUD-6: HALT-3 hours in calm count against max_no_price_hours."""
    run, _ = tiny_run
    pol = Policy()
    for r in run.table:
        v, c = r.metrics.values, r.metrics.constraints
        assert c["calm_availability"] == (v["div.calm_halt_hours_per_year"] <= pol.max_no_price_hours)
    assert "calm_availability" in _fam("divergence").constraints
    assert run.table.current().metrics.values["cap.informative"] in (0.0, 1.0)


def test_unverifiable_current_cap_is_kept():
    """D-RD-AUD-5: when the current cap's bound debt is mostly censored, the book cannot judge it:
    KEEP rather than ratchet the cap down to one that admits nothing."""
    base = mainnet()
    st = G.make_study()
    t = ResultTable(base)
    cen = {"cap.bound_debt_usd": 9e3, "cap.bound_uncensored_share": 0.1, "cap.informative": 0.0}
    _row(t, base, {"cap.bc_refused": 0.91, **cen}, {"depth_bound": False})
    _row(t, base.replace(supplyCapBps=250), {"cap.bc_refused": 0.92, "cap.bound_debt_usd": 0.0,
                                             "cap.informative": 0.0}, {"depth_bound": True})
    row, verdict, reason = st.decide_family(t, _fam("cap"), Policy())
    assert verdict == "KEEP" and row.params["supplyCapBps"] == 1500 and "unverifiable" in reason


def test_real_sys_bad_prob_on_a_hand_built_history():
    """D-RD-ORA-7: P(min within grace ≤ 1/halt of the start), daily starts, on the series itself."""
    from datetime import UTC, datetime

    import numpy as np

    from ybcal.studies import g7_supply_halts as G7
    from ybcal.types import PricePath
    from ybcal.units import BLOCKS_PER_DAY

    p = np.full(24 * 100, 1_000_000, dtype=np.int64)
    p[24 * 60 : 24 * 61] = 300_000  # one day at −70 %
    pp = PricePath(datetime(2024, 1, 1, tzinfo=UTC), "hour", p[None, :], "real")
    grace = 30 * BLOCKS_PER_DAY
    pr = G7.real_sys_bad_prob(pp, 25_000, grace)  # 1/2.5 = 40 % of the start: the dip qualifies
    starts = len(range(0, 24 * 100 - 24 * 30, 24))
    # starts on days 30..59 see the dip within 30 days (day 60 itself starts inside it)
    assert pr == pytest.approx(30 / starts)
    assert G7.real_sys_bad_prob(pp, 20_000, grace) == pytest.approx(30 / starts)  # ≤ 50 %: same days
    assert G7.real_sys_bad_prob(pp, 40_000, grace) == 0.0  # 1/4 = 25 %: the dip (30 %) is not that deep

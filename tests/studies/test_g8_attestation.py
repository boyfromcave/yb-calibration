"""G8 study (WP-7c): every owned param recommended, decision rules on hand-built tables, the ported
measurements on a real-shaped CSV, analytic vs simulated agreement, determinism."""

from __future__ import annotations

import io
import math

import numpy as np
import pytest

from ybcal.config import Policy
from ybcal.params.paramset import mainnet
from ybcal.params.registry import params_for_group
from ybcal.studies import _g8_ports as P
from ybcal.studies import g8_attestation as G
from ybcal.studies.base import Budget, Env, Metrics, ResultTable, load_study, missing_recommendations

TINY = Budget("quick", paths=8, block_horizon_days=3, hour_horizon_years=1.0, grid_points=3, lhs_samples=4,
              halving_rounds=1, scenario_set="core", morris_trajectories=2, sobol_samples=8, max_minutes=1)


def env_for(tmp_path=None, seed=5, policy=None, data=None) -> Env:
    return Env(policy or Policy(), TINY, seed=seed, data=data or {},
               out_dir=str(tmp_path) if tmp_path is not None else None)


@pytest.fixture(scope="module")
def tiny_run(tmp_path_factory):
    from ybcal.optimize.runner import run_group

    out = tmp_path_factory.mktemp("g8")
    env = Env(Policy(), TINY, seed=11, out_dir=str(out))
    return run_group(load_study("G8"), mainnet(), env, workers=1), out


def test_every_param_recommended_with_full_fields(tiny_run):
    run, out = tiny_run
    study = load_study("G8")
    assert study.params == params_for_group("G8")
    assert missing_recommendations(study, run.recommendations) == []
    by = {r.param: r for r in run.recommendations}
    for r in run.recommendations:
        assert r.verdict in ("KEEP", "CHANGE", "PROVISIONAL", "BLOCKED")
        assert r.rule and r.binding and r.explanation and r.sensitivity.get("sentence")
        assert r.evidence and all(p.exists() for p in r.evidence)
        assert str(r.evidence[0]).startswith(str(out / "g8"))
    # derived values follow their parents; design choices are kept
    assert by["qHighBps"].recommended == 10_000 - by["qLowBps"].recommended
    assert by["attestMaxAge"].recommended == 2 * by["attestInterval"].recommended
    assert by["attestRequired"].recommended is True and by["attestRequired"].verdict == "KEEP"
    assert by["bondMinLock"].recommended == 420_480
    assert by["attestInterval"].change_path == "patch-release" and by["attestMaxAge"].change_path == "locked"
    # synthetic-only evidence is provisional
    for p in ("divergeBpsAttest", "pinDeltaBps", "emergencyPersist"):
        assert by[p].provenance == "synthetic" and by[p].verdict in ("PROVISIONAL", "BLOCKED")
    # the ported spreads rule: 3 × worst p95 rounded up to 100
    cur = run.table.current().metrics.values
    assert cur["div.target"] == max(300, math.ceil(3 * cur["div.worst_p95"] / 100) * 100)
    # bundle-level griefing finding (qLow 3333 vs a 25 % entity among 9 seats)
    assert cur["cap.bundle_share_when_selected"] > 0.3333 and cur["cap.harm_prob"] == 0.0
    assert by["qLowBps"].recommended > 3333 and by["qLowBps"].verdict == "CHANGE"


def test_simulation_confirms_liveness_and_dormancy(tiny_run):
    run, _ = tiny_run
    by = {r.param: r for r in run.recommendations}
    sim = by["kSlack"].metrics["simulation"]["current"]
    assert sim["liveness_consistent"] and sim["dead_consistent"]
    assert sim["dead_ejected_fraction_sim"] == 1.0


def test_dormancy_double_fix_is_reconciled(tiny_run):
    run, _ = tiny_run
    by = {r.param: r for r in run.recommendations}
    changed = [p for p in ("dormancyBlocks", "dormancyMinBundles") if by[p].recommended != by[p].current]
    assert len(changed) <= 1
    if changed:
        other = {"dormancyBlocks", "dormancyMinBundles", "dormancyCheck"} - set(changed)
        for p in other:
            assert by[p].verdict == "KEEP" and any("resolved" in n for n in by[p].notes)


def test_real_spreads_log_reproduces_the_port(tmp_path):
    """On the same CSV, the study's divergeBpsAttest target equals spreads.py analyze's."""
    from ybcal.data.loaders import load_spreads_csv

    rng = np.random.default_rng(4)
    lines = [",".join(P.COLUMNS)]
    for i in range(4032):
        b = 400_000 * (1 + 0.01 * math.sin(i / 50))
        q = [int(b), int(b * (1 + rng.normal(0.002, 0.01))), int(b * (1 + rng.normal(-0.001, 0.02)))]
        lines.append(",".join(["x", str(1_700_000_000 + 300 * i), *map(str, q), ""]))
    text = "\n".join(lines) + "\n"
    want = P.analyze_spreads(P.read_log(io.StringIO(text)), 300)["recommended_bps"]
    env = env_for(data={"spreads": load_spreads_csv(io.StringIO(text))})
    m = load_study("G8").evaluate(mainnet(), env)
    assert m.values["div.target"] == want and m.meta["spreads_provenance"] == "real-data"


def test_real_hourly_price_reproduces_pinrate():
    from ybcal.data.pricepath import from_usd

    rng = np.random.default_rng(9)
    usd = 0.4 * np.exp(np.cumsum(rng.normal(0, 0.015, 24 * 60)))
    pp = from_usd(np.datetime64("2026-01-01T00:00:00").astype(object), "hour", usd[None, :], "real")
    env = env_for(data={"price": pp})
    m = load_study("G8").evaluate(mainnet(), env)
    series = P.series_from_prices(pp.prices[0].astype(float) / 1e6, 3600)
    want = P.analyze_pinrate(series, 288, 75, 500)
    assert m.values["pin.arm_rate"] == pytest.approx(want["rates"][500]["rate"])
    assert m.values["pin.target_delta"] == P.pin_delta_from_rates(want, 500)
    assert m.meta["price_provenance"] == "real-data"


def _m(values, cons):
    return Metrics({"zero": 0.0, **values}, "zero", True, cons, "judgement", {})


def test_verify_family_moves_to_nearest_feasible():
    st = G.G8Study()
    fam = next(f for f in st.families() if f.name == "qlow")
    base = mainnet()
    t = ResultTable(base)
    for q, grief in ((3333, False), (3400, False), (3500, True), (3600, True), (3000, False)):
        ps = base if q == 3333 else base.replace(qLowBps=q)
        t.add(ps, _m({}, {"harm_capture": True, "grief_capture": grief}))
    row, verdict, _ = st.decide_family(t, fam, Policy())
    assert verdict == "CHANGE" and row.params["qLowBps"] == 3500 and row.params["qHighBps"] == 6500
    # all feasible: KEEP
    t2 = ResultTable(base)
    for q in (3333, 3500):
        ok = {"harm_capture": True, "grief_capture": True}
        t2.add(base if q == 3333 else base.replace(qLowBps=q), _m({}, ok))
    assert st.decide_family(t2, fam, Policy())[1] == "KEEP"


def test_rule_family_keeps_within_materiality():
    st = G.G8Study()
    fam = next(f for f in st.families() if f.name == "diverge")
    base = mainnet()
    for target, want in ((1300, ("KEEP", 1500)), (1100, ("CHANGE", 1100)), (2400, ("CHANGE", 2400))):
        t = ResultTable(base)
        for v in (1100, 1300, 1500, 2400):
            ps = base if v == 1500 else base.replace(divergeBpsAttest=v)
            t.add(ps, _m({"div.target": float(target)}, {}))
        row, verdict, _ = st.decide_family(t, fam, Policy())
        assert (verdict, row.params["divergeBpsAttest"]) == want, target


def test_analytic_pieces():
    # one seat down: hypergeometric mixture lies between the all-up and the k−1 slack values
    from ybcal.sim.attest import liveness_probability

    f = 0.99
    u1 = G.unavail_with_down(4, 2, 9, 1, f, 0.0)
    assert 1 - liveness_probability(4, 2, f) < u1 < 1 - liveness_probability(4, 1, f)
    assert G.unavail_with_down(4, 2, 6, 3, 1.0, 0.0) == pytest.approx(1.0)
    # pin detection is bundle-limited at low adoption, tag-limited at high
    assert G.pin_detect_blocks(288, 3, 2, 0.04, 2 / 1152) > 1000
    assert G.pin_detect_blocks(288, 3, 2, 0.04, 1.0) == pytest.approx(75.0)
    # dead-attestor detection: about dormancyBlocks when rows are plentiful
    rng = np.random.default_rng(0)
    t = G.dead_detect_times(rng, 200, 0.02, 16_128, 20, 48)
    assert np.all(t >= 16_128) and np.median(t) <= 16_128 + 48
    t0 = G.dead_detect_times(rng, 200, 0.02, 16_128, 20, 48, t_min=0)
    assert abs(np.median(t0) - 1000) < 300


def test_evaluate_is_deterministic():
    st = load_study("G8")
    a = st.evaluate(mainnet(), env_for(seed=3))
    G._CACHE.clear()
    b = st.evaluate(mainnet(), env_for(seed=3))
    va, vb = dict(a.values), dict(b.values)
    assert va.keys() == vb.keys()
    for k in va:
        assert va[k] == vb[k] or (math.isnan(va[k]) and math.isnan(vb[k])), k

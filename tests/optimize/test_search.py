"""Search-space generators: axes, grid, LHS, neighbourhood, invariant filtering, halving."""

from __future__ import annotations

import pytest

from tests.optimize.toys import Bowl
from ybcal.config import Policy
from ybcal.optimize.evaluate import EvalCache
from ybcal.optimize.search import (
    Rejected,
    SearchSpace,
    Tie,
    axis,
    grid,
    halving_schedule,
    latin_hypercube,
    neighbourhood,
    screen,
    successive_halving,
)
from ybcal.params.paramset import candidate, mainnet, regtest
from ybcal.params.registry import REGISTRY
from ybcal.studies.base import Budget, Env

BASE = candidate()


def _on_axis(name, v, base=BASE):
    s = REGISTRY[name]
    lo, hi = s.bounds
    return lo <= v <= hi and (v - base[name]) % s.step == 0


def test_axis_anchored_on_base_and_inside_bounds():
    a = axis("grace", BASE)
    assert BASE["grace"] in a.values
    assert all(_on_axis("grace", v) for v in a.values)
    assert a.values[0] - a.step < REGISTRY["grace"].bounds[0]
    assert a.values[-1] + a.step > REGISTRY["grace"].bounds[1]
    assert a.snap(BASE["grace"] + 500) == BASE["grace"]
    assert a.snap(BASE["grace"] + 700) == BASE["grace"] + 1152


def test_axis_regtest_base_outside_bounds_anchors_on_lower_bound():
    a = axis("grace", regtest())
    assert a.values[0] == REGISTRY["grace"].bounds[0]
    assert regtest()["grace"] not in a.values


def test_axis_errors_and_overrides():
    with pytest.raises(ValueError):
        axis("pFastMinFill", BASE)          # derived: step 0
    assert axis("bundleCarrier", BASE, values=["SCRIPTSIG", "OP_RETURN"]).values == ("SCRIPTSIG", "OP_RETURN")
    assert axis("grace", BASE, bounds=(30_000, 40_000), step=1152).values == (
        31_104, 32_256, 33_408, 34_560, 35_712, 36_864, 38_016, 39_168)


def test_thin_keeps_base_and_ends():
    a = axis("abandonBlocks", BASE)
    t = a.thin(5)
    assert len(t) <= 5 and BASE["abandonBlocks"] in t.values
    assert t.values[-1] == a.values[-1]


def test_for_params_skips_untunable():
    sp = SearchSpace.for_params(BASE, ["pFastWindow", "pFastMinFill", "network"])
    assert sp.names == ("pFastWindow",)


def test_grid_snapping_bounds_and_base_first():
    sp = SearchSpace.for_params(BASE, ["pFastWindow", "pMidWindow"])
    cs = grid(sp, points=4)
    assert cs.candidates[0] == BASE
    for c in cs:
        assert _on_axis("pFastWindow", c["pFastWindow"]) and _on_axis("pMidWindow", c["pMidWindow"])
        assert c["pFastMinFill"] == -(-c["pFastWindow"] // 2)   # derived recomputed via replace
    assert len({c.digest() for c in cs}) == len(cs)


def test_grid_invariant_filtering_counts():
    # grace × abandonBlocks, both ±2 steps around the (equal) current values: abandon < grace fails W21.
    vals = {p: [BASE[p] + d * 1152 for d in range(-2, 3)] for p in ("grace", "abandonBlocks")}
    sp = SearchSpace.for_params(BASE, ["grace", "abandonBlocks"], values=vals)
    cs = grid(sp)
    # pairs with abandon < grace: for 5×5 offsets, #(da < dg) = 10
    assert cs.n_invalid == 10
    assert cs.invalid_counts() == {"abandon_ge_grace": 10}
    assert cs.proposed == 25 and cs.duplicates == 1 and len(cs) == 15   # 25 grid points; base twice
    assert all(c["abandonBlocks"] >= c["grace"] for c in cs)
    assert "10 rejected (abandon_ge_grace×10)" in cs.summary()


def test_feasibility_predicate_and_coupling():
    def short_grace(ps):
        return "grace too long" if ps["grace"] > 45_000 else None

    sp = SearchSpace.for_params(BASE, ["grace"], values={"grace": [33_408, 34_560, 40_320, 46_080]},
                                couple=[Tie("abandonBlocks", "grace")], feasible=[short_grace])
    cs = grid(sp)
    assert [c["grace"] for c in cs] == [34_560, 33_408, 40_320]
    assert all(c["abandonBlocks"] == c["grace"] for c in cs)
    assert cs.invalid_counts() == {"infeasible": 1}
    assert cs.rejected[0].detail == ("grace too long",)


def test_coupling_out_of_bounds_rejected():
    sp = SearchSpace.for_params(BASE, ["grace"], values={"grace": [BASE["grace"], 103_680]},
                                couple=[Tie("abandonBlocks", "grace", offset=1152)])
    cs = grid(sp)
    assert cs.invalid_counts() == {"bounds": 1}


def test_construction_error_is_reported():
    sp = SearchSpace.for_params(BASE, ["grace"], values={"grace": [BASE["grace"], "x"]})
    cs = grid(sp)
    assert cs.invalid_counts() == {"error": 1} and isinstance(cs.rejected[0], Rejected)


def test_grid_cap_thins_with_warning():
    sp = SearchSpace.for_params(BASE, ["pFastWindow", "pMidWindow", "pSlowWindow"])
    with pytest.warns(UserWarning, match="exceeds cap"):
        cs = grid(sp, cap=64)
    assert cs.proposed <= 64 and cs.warnings and BASE in cs.candidates


def test_grid_cap_falls_back_to_lhs():
    names = ["pFastWindow", "pMidWindow", "pSlowWindow", "volWindow", "volStep", "sigmaRefBps", "grace"]
    sp = SearchSpace.for_params(BASE, names)
    with pytest.warns(UserWarning, match="Latin-hypercube"):
        cs = grid(sp, cap=50)
    assert cs.method == "grid→lhs" and cs.proposed == 50


def test_lhs_snapped_deduped_deterministic_and_stratified():
    sp = SearchSpace.for_params(BASE, ["grace", "abandonBlocks", "sigmaRefBps"])
    a = latin_hypercube(sp, 20, seed=7)
    b = latin_hypercube(sp, 20, seed=7)
    assert [c.digest() for c in a] == [c.digest() for c in b]
    assert a.candidates[0] == BASE
    for c in a:
        for p in sp.names:
            assert _on_axis(p, c[p])
    assert len({c.digest() for c in a}) == len(a)
    assert a.proposed == 20 and len(a) + a.n_invalid + a.duplicates == 21
    # stratification: 20 samples over sigmaRefBps' 56 levels hit 20 distinct strata
    lv = axis("sigmaRefBps", BASE).values
    idx = sorted(lv.index(c["sigmaRefBps"]) * 20 // len(lv) for c in a.candidates[1:])
    rej = [r.changes.get("sigmaRefBps", BASE["sigmaRefBps"]) for r in a.rejected]
    assert len(idx) + len(rej) + a.duplicates == 20


def test_lhs_duplicates_counted_on_small_axes():
    sp = SearchSpace.for_params(BASE, ["peerMin"])          # 11 levels
    cs = latin_hypercube(sp, 40, seed=1)
    assert len(cs) <= 11 and cs.duplicates >= 29


def test_neighbourhood_axis_and_full():
    sp = SearchSpace.for_params(BASE, ["pFastWindow", "pMidWindow"])
    nb = neighbourhood(sp, k=1)
    assert nb.candidates[0] == BASE and len(nb) == 5
    around = BASE.replace(pFastWindow=144)
    nb2 = neighbourhood(sp, around, k=2, params=["pFastWindow"])
    assert [c["pFastWindow"] for c in nb2] == [144, 96, 192, 48, 240]
    full = neighbourhood(sp, k=1, mode="full")
    assert len(full) == 9


def test_neighbourhood_rejects_invariant_breakers():
    sp = SearchSpace.for_params(BASE, ["abandonBlocks"])
    nb = neighbourhood(sp, k=1)      # abandon − 1 step < grace
    assert nb.invalid_counts() == {"abandon_ge_grace": 1} and len(nb) == 2


def test_screen_adds_base_and_filters():
    sets = [BASE.replace(pFastWindow=576), BASE.replace(pFastWindow=144), BASE.replace(pFastWindow=144)]
    cs = screen(sets, BASE)
    assert cs.candidates[0] == BASE and cs.warnings
    assert cs.invalid_counts() == {"window_order": 1} and cs.duplicates == 1 and len(cs) == 2


def test_halving_schedule():
    assert halving_schedule(64, 2) == [22, 64]
    assert halving_schedule(400, 3, eta=3, min_paths=8) == [45, 134, 400]
    assert halving_schedule(10, 4) == [8, 8, 8, 10]
    assert halving_schedule(64, 1) == [64]


def test_halving_keeps_true_best_and_base():
    env = Env(Policy(), Budget.named("standard"), seed=3)
    target = BASE["sigmaRefBps"] + 7 * 500
    fn = Bowl((("sigmaRefBps", target),), w=0.2, noise=1.0)
    sp = SearchSpace.for_params(BASE, ["sigmaRefBps"])
    cs = grid(sp)
    assert len(cs) > 30
    res = successive_halving(fn, cs, env, eta=3, keep=[BASE], crn=False, cache=EvalCache())
    best, _ = res.best
    assert best["sigmaRefBps"] == target
    assert BASE in res.candidates                       # current survives for materiality
    assert all(mm.values["paths"] == 400 for mm in res.metrics)
    assert [r.paths for r in res.rounds] == [45, 134, 400]
    assert res.rounds[0].evaluated == len(cs) and res.rounds[-1].evaluated < len(cs) / 3 + 2
    assert res.low_fidelity and res.low_fidelity[0][0] == 45


def test_mainnet_base_network_override():
    sp = SearchSpace.for_params(mainnet(), ["grace"], network="candidate")
    cs = latin_hypercube(sp, 5, seed=0)
    assert all(c.network == "candidate" for c in cs)

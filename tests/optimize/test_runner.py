"""optimize_group end to end on a toy Study implementing the frozen protocol."""

from __future__ import annotations

import pytest

from tests.optimize.toys import Bowl, ToyStudy
from ybcal.config import Policy
from ybcal.optimize.evaluate import EvalCache
from ybcal.optimize.runner import optimize_group, recommended_set, run_group
from ybcal.params.paramset import candidate
from ybcal.studies.base import Budget, Env, Study

BASE = candidate()
G, A = BASE.as_int("grace"), BASE.as_int("abandonBlocks")     # both 34,560


def _env(budget="quick"):
    return Env(Policy(), Budget.named(budget), seed=5, provenance="real-data")


def test_toy_is_a_study():
    assert isinstance(ToyStudy(Bowl(())), Study)


def test_keep_when_improvement_below_materiality():
    # optimum one step above current on both params; gain 1.1 → 1.0 = 9 % < 20 %
    st = ToyStudy(Bowl((("grace", G + 1152), ("abandonBlocks", A + 1152)), w=0.05))
    table, recs = optimize_group(st, BASE, _env(), workers=1)
    assert {r.param for r in recs} == set(st.params)
    assert all(r.verdict == "KEEP" and not r.changed for r in recs)
    assert table.current(st.params) is not None
    assert all(any(n.startswith("Search: G4 [space]") for n in r.notes) for r in recs)


def test_change_when_improvement_above_materiality():
    st = ToyStudy(Bowl((("grace", G + 2 * 1152), ("abandonBlocks", A + 2 * 1152)), w=1.0))
    _, recs = optimize_group(st, BASE, _env(), workers=1)
    by = {r.param: r for r in recs}
    assert by["grace"].verdict == "CHANGE" and by["grace"].recommended == G + 2 * 1152
    assert by["abandonBlocks"].recommended == A + 2 * 1152


def test_provisional_on_synthetic_provenance():
    st = ToyStudy(Bowl((("grace", G + 2 * 1152), ("abandonBlocks", A + 2 * 1152)), provenance="synthetic"))
    _, recs = optimize_group(st, BASE, _env(), workers=1)
    assert all(r.verdict == "PROVISIONAL" for r in recs)


def test_invalid_counts_timing_and_neighbours():
    st = ToyStudy(Bowl((("grace", G), ("abandonBlocks", A + 1152)), w=1.0))
    run = run_group(st, BASE, _env(), workers=1)
    # 5×5 space with abandon < grace in 10 cells
    assert run.n_invalid == 10 and run.candidates.invalid_counts() == {"abandon_ge_grace": 10}
    assert len(run.table) == 15
    assert set(run.seconds) == {"space", "evaluate", "decide", "neighbours", "total"}
    assert "10 rejected (abandon_ge_grace×10)" in run.summary()
    # 1.0 → 2.0 is a 50 % gain → CHANGE abandonBlocks to +1 step
    assert run.recommended["abandonBlocks"] == A + 1152 and run.recommended["grace"] == G
    nb = run.neighbours
    assert [p.steps for p in nb["grace"]] == [-1, 1] and [p.steps for p in nb["abandonBlocks"]] == [-1, 1]
    up = nb["grace"][1]                       # grace +1 step: abandon still ≥ grace
    assert up.primary == pytest.approx(2.0) and up.improvement == pytest.approx(-1.0)
    rec = {r.param: r for r in run.recommendations}["abandonBlocks"]
    assert len(rec.sensitivity["neighbours"]) == 2 and rec.sensitivity["local_slope"] in ("moderate", "steep")
    assert rec.explanation.startswith("abandonBlocks: CHANGE")


def test_neighbour_rejections_reported():
    st = ToyStudy(Bowl((("grace", G), ("abandonBlocks", A)), w=1.0))
    run = run_group(st, BASE, _env(), workers=1)
    pts = run.neighbours["abandonBlocks"]
    assert pts[0].rejected == ("abandon_ge_grace",) and pts[0].primary is None
    rec = {r.param: r for r in run.recommendations}["abandonBlocks"]
    assert any("rejected by invariants" in n for n in rec.notes)


def test_better_neighbour_note():
    # optimum 3 steps away but the toy space only spans ±2: the neighbour of the best is better
    st = ToyStudy(Bowl((("grace", G), ("abandonBlocks", A + 3 * 1152)), w=1.0))
    run = run_group(st, BASE, _env(), workers=1)
    rec = {r.param: r for r in run.recommendations}["abandonBlocks"]
    assert rec.recommended == A + 2 * 1152
    assert any(n.startswith("Neighbour abandonBlocks=") and "beyond materiality" in n for n in rec.notes)


@pytest.mark.parametrize("method", ["grid", "lhs"])
def test_generated_methods(method):
    st = ToyStudy(Bowl((("grace", G + 20 * 1152), ("abandonBlocks", A + 20 * 1152)), w=0.01))
    run = run_group(st, BASE, _env(), method=method, workers=1)
    assert run.method in ("grid", "lhs") and len(run.table) > 1
    assert run.table.rows[0].params == BASE
    assert all(r.params["abandonBlocks"] >= r.params["grace"] for r in run.table)
    assert any(r.verdict == "CHANGE" for r in run.recommendations)


def test_halving_method():
    st = ToyStudy(Bowl((("grace", G + 1152), ("abandonBlocks", A + 2 * 1152)), w=1.0, noise=0.5), k=2)
    run = run_group(st, BASE, _env("standard"), method="halving", workers=1)
    assert run.method == "halving(space)" and run.halving is not None
    assert [r.paths for r in run.halving.rounds] == [45, 134, 400]
    assert all(r.metrics.values["paths"] == 400 for r in run.table)
    assert run.table.current(st.params) is not None              # base survives
    assert run.recommended["grace"] == G + 1152 and run.recommended["abandonBlocks"] == A + 2 * 1152


def test_workers_do_not_change_results():
    st = ToyStudy(Bowl((("grace", G + 1152), ("abandonBlocks", A + 2 * 1152)), w=0.3, noise=0.2))
    t1, r1 = optimize_group(st, BASE, _env(), workers=1)
    t2, r2 = optimize_group(st, BASE, _env(), workers=2)
    assert [r.metrics.values for r in t1] == [r.metrics.values for r in t2]
    def key(recs):
        return [(r.param, r.recommended, r.verdict) for r in recs]

    assert key(r1) == key(r2)


def test_shared_cache_reuses_evaluations():
    st = ToyStudy(Bowl((("grace", G), ("abandonBlocks", A + 1152)), w=1.0))
    cache = EvalCache()
    run_group(st, BASE, _env(), workers=1, cache=cache)
    again = run_group(st, BASE, _env(), workers=1, cache=cache)
    assert again.stats.evaluated == 0 and again.stats.cached > 0


def test_recommended_set_skips_derived():
    from ybcal.studies.base import Recommendation

    recs = [Recommendation("pFastWindow", 96, 144, "CHANGE", "r", "b"),
            Recommendation("pFastMinFill", 48, 72, "CHANGE", "r", "b")]
    s = recommended_set(BASE, recs)
    assert s["pFastWindow"] == 144 and s["pFastMinFill"] == 72

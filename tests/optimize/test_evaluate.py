"""Evaluation: determinism across worker counts, per-candidate seeding, cache."""

from __future__ import annotations

import json

import pytest

from tests.optimize.toys import Bowl, ToyStudy
from ybcal.config import Policy
from ybcal.optimize.evaluate import (
    EvalCache,
    EvalStats,
    candidate_env,
    env_fingerprint,
    evaluate_many,
    evaluate_set,
    evaluator_id,
    metrics_from_dict,
    metrics_to_dict,
    with_paths,
)
from ybcal.optimize.search import SearchSpace, grid
from ybcal.params.paramset import candidate
from ybcal.studies.base import Budget, Env

BASE = candidate()
FN = Bowl((("grace", 40_320),), w=0.1, noise=1.0)


def _env(seed=11):
    return Env(Policy(), Budget.named("quick"), seed=seed)


def _cands():
    return grid(SearchSpace.for_params(BASE, ["grace"]), points=9).candidates


def _det(c):
    """The noise-free part of FN."""
    return 1 + 0.1 * ((c.as_int("grace") - 40_320) / 1152) ** 2


def test_workers_1_vs_2_identical():
    cands = _cands()
    a = evaluate_many(FN, cands, _env(), workers=1)
    b = evaluate_many(FN, cands, _env(), workers=2)
    assert [m.values for m in a] == [m.values for m in b]


def test_order_independent_and_crn():
    cands = _cands()
    a = evaluate_many(FN, cands, _env(), workers=1)
    b = evaluate_many(FN, cands[::-1], _env(), workers=1)[::-1]
    assert [m.values for m in a] == [m.values for m in b]
    # CRN: the noise term is identical for every candidate
    noise = [m.primary_value - _det(c) for c, m in zip(cands, a, strict=True)]
    assert max(noise) - min(noise) < 1e-12
    ind = evaluate_many(FN, cands, _env(), workers=1, crn=False)
    noise2 = [m.primary_value - _det(c) for c, m in zip(cands, ind, strict=True)]
    assert max(noise2) - min(noise2) > 1e-6


def test_candidate_env_resets_rng_only():
    env = _env()
    env.rng.random(5)
    e = candidate_env(env, BASE)
    assert e is not env and e.policy is env.policy and e.budget == env.budget
    assert e.rng.random() == env.rng_for("evaluate").random()


def test_cache_memory_hits_and_duplicates():
    cache, st = EvalCache(), EvalStats()
    cands = _cands()
    evaluate_many(FN, [*cands, cands[0]], _env(), cache=cache, stats=st)
    assert st.evaluated == len(cands) and st.n == len(cands) + 1
    st2 = EvalStats()
    evaluate_many(FN, cands, _env(), cache=cache, stats=st2)
    assert st2.evaluated == 0 and st2.cached == len(cands)
    # a different budget is a different key
    st3 = EvalStats()
    evaluate_many(FN, cands[:2], with_paths(_env(), 8), cache=cache, stats=st3)
    assert st3.evaluated == 2


def test_cache_on_disk_roundtrip(tmp_path):
    c1 = EvalCache.on_disk(tmp_path / "cache")
    m1 = evaluate_set(FN, BASE, _env(), cache=c1)
    files = list((tmp_path / "cache").rglob("*.json"))
    assert len(files) == 1 and json.loads(files[0].read_text())["primary"] == "loss"
    c2 = EvalCache.on_disk(tmp_path / "cache")
    m2 = evaluate_set(FN, BASE, _env(), cache=c2)
    assert c2.hits == 1 and m2.values == m1.values


def test_env_fingerprint_sensitivity():
    assert env_fingerprint(_env(1)) == env_fingerprint(_env(1))
    assert env_fingerprint(_env(1)) != env_fingerprint(_env(2))
    e = _env(1)
    e.data["x"] = [1, 2]
    assert env_fingerprint(e) != env_fingerprint(_env(1))


def test_metrics_dict_roundtrip():
    m = FN(BASE, _env())
    assert metrics_from_dict(metrics_to_dict(m)) == m


def test_evaluator_id_and_study_evaluate():
    st = ToyStudy(FN)
    assert evaluator_id(st.evaluate).endswith("ToyStudy.evaluate[G4]")
    assert evaluate_set(st, BASE, _env()).values == evaluate_set(FN, BASE, _env()).values


def test_unpicklable_falls_back_to_serial():
    def local(c, e):
        return FN(c, e)

    with pytest.warns(RuntimeWarning, match="not picklable"):
        out = evaluate_many(local, _cands()[:3], _env(), workers=2)
    assert len(out) == 3

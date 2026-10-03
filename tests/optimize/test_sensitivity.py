"""OAT, Morris and Sobol — validated on Ishigami (analytic indices) and a linear model."""

from __future__ import annotations

import math

import numpy as np
import pytest

from tests.optimize.toys import Bowl, linear
from ybcal.config import Policy
from ybcal.optimize.sensitivity import (
    Factor,
    OATResult,
    ParamSetObjective,
    factors_for_params,
    format_value,
    ishigami,
    ishigami_indices,
    morris,
    morris_design,
    oat,
    oat_from_table,
    saltelli_design,
    sensitivity_sentence,
    sobol,
)
from ybcal.params.paramset import candidate
from ybcal.studies.base import Budget, Env, Metrics, ResultTable

PI = math.pi
ISHI = [Factor("x1", -PI, PI), Factor("x2", -PI, PI), Factor("x3", -PI, PI)]


def test_ishigami_analytic_reference():
    ref = ishigami_indices()
    np.testing.assert_allclose(ref["S1"], [0.314, 0.442, 0.0], atol=1e-3)
    np.testing.assert_allclose(ref["ST"], [0.558, 0.442, 0.244], atol=1e-3)


def test_sobol_ishigami_within_tolerance():
    r = sobol(ishigami, ISHI, 2**13, seed=1, n_boot=100)
    ref = ishigami_indices()
    np.testing.assert_allclose(r.S1, ref["S1"], atol=0.02)
    np.testing.assert_allclose(r.ST, ref["ST"], atol=0.02)
    assert r.n_evals == 2**13 * 5
    for i in range(3):         # CIs bracket the analytic values
        assert r.S1_conf[i, 0] - 0.01 <= ref["S1"][i] <= r.S1_conf[i, 1] + 0.01
        assert r.ST_conf[i, 0] - 0.01 <= ref["ST"][i] <= r.ST_conf[i, 1] + 0.01
    assert r.ranking() == ["x1", "x2", "x3"]
    assert r.insensitive(0.01) == []


def test_sobol_linear_and_dummy():
    fs = [Factor("x1", 0, 1), Factor("x2", 0, 2), Factor("x3", 0, 4), Factor("dummy", 0, 1)]
    r = sobol(linear, fs, 2**12, seed=2, n_boot=50)
    var = np.array([9 * 1 / 12, 4 * 4 / 12, 0.25 * 16 / 12, 0])
    want = var / var.sum()
    np.testing.assert_allclose(r.S1, want, atol=0.02)
    np.testing.assert_allclose(r.ST, want, atol=0.02)      # additive: S1 = ST
    assert r.insensitive(0.01) == ["dummy"]


def test_sobol_constant_output_and_nan_rows():
    r = sobol(lambda X: np.zeros(len(X)), ISHI, 64)
    assert (r.S1 == 0).all() and (r.ST == 0).all()

    def holes(X):
        y = ishigami(X)
        y[X[:, 0] > 3.0] = np.nan
        return y

    r2 = sobol(holes, ISHI, 2**10, seed=3, n_boot=20)
    assert r2.n_invalid > 0 and np.isfinite(r2.ST).all()


def test_saltelli_design_structure():
    X, n = saltelli_design(ISHI, 100, seed=0)
    assert n == 128 and X.shape == (128 * 5, 3)
    A, B = X[:n], X[n:2 * n]
    AB2 = X[(2 + 1) * n:(2 + 2) * n]
    np.testing.assert_array_equal(AB2[:, 1], B[:, 1])
    np.testing.assert_array_equal(AB2[:, [0, 2]], A[:, [0, 2]])


def test_morris_ranks_ishigami():
    fs = [*ISHI, Factor("dummy", 0, 1)]
    m = morris(ishigami, fs, r=100, levels=4, seed=0)
    rank = m.ranking()
    assert set(rank[:2]) == {"x1", "x2"} and rank[2] == "x3" and rank[3] == "dummy"
    i3 = m.names.index("x3")
    assert m.mu_star[-1] == 0 and m.sigma[-1] == 0
    assert m.sigma[i3] > m.mu_star[i3]          # x3 acts only through its interaction with x1
    assert m.n_evals == 100 * 5


def test_morris_linear_exact():
    fs = [Factor("x1", 0, 1), Factor("x2", 0, 2), Factor("x3", 0, 4)]
    m = morris(linear, fs, r=10, levels=6, seed=4)
    np.testing.assert_allclose(m.mu_star, [3 * 1, 2 * 2, 0.5 * 4])   # |coef|·range
    np.testing.assert_allclose(m.mu, [3, -4, 2])
    np.testing.assert_allclose(m.sigma, 0, atol=1e-12)
    assert m.ranking() == ["x2", "x1", "x3"]


def test_morris_design_on_stepped_integer_grid():
    base = candidate()
    fs = factors_for_params(base, ["pFastWindow", "grace", "peerMin"], k_steps=2)
    assert fs[0].levels == (48, 96, 144, 192)            # ±2 steps, clipped at the lower bound 48
    X, order, dx = morris_design(fs, 6, seed=1)
    assert X.shape == (6 * 4, 3)
    for j, f in enumerate(fs):
        assert set(X[:, j]) <= set(f.levels)                     # stays on the integer grid
    for t in range(6):                                           # one factor moves per step
        blk = X[t * 4:(t + 1) * 4]
        assert all((np.diff(blk, axis=0) != 0).sum(axis=1) == 1)
        assert sorted(order[t]) == [0, 1, 2]
    assert (dx != 0).all()


def test_factors_for_params_full_range():
    base = candidate()
    f = factors_for_params(base, ["peerMin"], k_steps=None)[0]
    assert f.levels == tuple(range(2, 13))


def test_paramset_objective_morris_and_invalid():
    base = candidate()
    env = Env(Policy(), Budget.named("quick"), seed=1)
    fn = Bowl((("grace", 40_320), ("abandonBlocks", 46_080)), w=1.0)
    obj = ParamSetObjective(fn, env, base, ["grace", "abandonBlocks"])
    fs = factors_for_params(base, ["grace", "abandonBlocks"], k_steps=2)
    m = morris(obj, fs, r=8, seed=0)
    assert obj.invalid > 0 and m.n_invalid == obj.invalid        # abandon < grace points are NaN
    assert np.isfinite(m.mu_star).all()


# ------------------------------------------------------------------------------------------------- OAT


def _oat_bowl():
    # P(miss) falls steeply below 20 days, flat 20–40 days; bad debt rises gently above 40 days.
    days = np.array([5, 10, 15, 20, 25, 30, 35, 40, 50, 60], dtype=float)
    miss = np.array([0.5, 0.2, 0.05, 0.010, 0.010, 0.010, 0.010, 0.010, 0.010, 0.010])
    debt = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.002, 0.004])
    y = miss + debt
    return OATResult("grace", days * 1152, y, "loss", {"P(miss)": miss, "ΔP(bad debt)": debt},
                     base_value=30 * 1152)


def test_oat_classification_and_sentence():
    o = _oat_bowl()
    assert o.classes[:2] == ["steep", "steep"] and set(o.classes[3:7]) == {"flat"}
    assert o.local()["class"] == "flat"
    s = sensitivity_sentence("grace", o, "loss")
    assert s == ("loss is flat between 20 and 40 days; P(miss) dominates below 20 days; "
                 "ΔP(bad debt) dominates above 40 days.")


def test_oat_sentence_without_components():
    x = np.array([1, 2, 3, 4, 5, 6], dtype=float) * 1152
    y = np.array([10.0, 5.0, 2.0, 2.0, 2.0, 2.0])
    s = sensitivity_sentence("grace", OATResult("grace", x, y, "P(miss)"))
    assert s == "P(miss) is flat between 3 and 6 days; P(miss) rises steeply below 3 days."


def test_oat_sentence_flat_everywhere_and_monotone():
    x = np.arange(1, 6, dtype=float) * 100
    assert "insensitive" in sensitivity_sentence("feeBps", OATResult("feeBps", x, np.ones(5), "m"))
    s = sensitivity_sentence("feeBps", OATResult("feeBps", x, x ** 2, "m"))
    assert "steep" in s and "rises as feeBps increases" in s


def test_format_value_units():
    assert format_value("grace", 34_560) == "30 days"
    assert format_value("peerLag", 10) == "10 blocks"
    assert format_value("peerMin", 5) == "5"
    assert format_value("deviationBps", 1000) == "10 %"
    assert format_value("valveBlocks", 96) == "2 hours"
    assert format_value("x", 2.5) == "2.5"


def test_oat_through_evaluator_and_table():
    base = candidate()
    env = Env(Policy(), Budget.named("quick"), seed=1)
    fn = Bowl((("pMidWindow", 576),), w=1.0)
    o = oat(fn, env, base, "pMidWindow", points=7)
    assert base["pMidWindow"] in o.values and len(o.values) == 7
    assert o.values[int(np.argmin(o.metric))] == 576 and o.overall == "steep"
    t = ResultTable(base)
    for v in (480, 528, 576, 624):
        ps = base.replace(pMidWindow=v)
        t.add(ps, fn(ps, env))
    t.add(base.replace(pFastWindow=144), Metrics({"loss": 9.0}, "loss"))   # not on the slice
    o2 = oat_from_table(t, "pMidWindow")
    assert o2.values.tolist() == [480, 528, 576, 624]
    with pytest.raises(KeyError):
        oat_from_table(t, "pMidWindow", metric="nope")

"""Robust aggregation and selection on hand-computed tables."""

from __future__ import annotations

import math

import numpy as np
import pytest

from ybcal.config import Policy
from ybcal.optimize.robust import (
    Constraint,
    ScenarioTable,
    aggregate,
    cvar,
    max_regret,
    quantile,
    regret_matrix,
    robust_select,
    step_distance,
    tie_break,
    weighted_mean,
    worst_case,
)
from ybcal.params.paramset import candidate
from ybcal.studies.base import Metrics, ResultTable

# 3 candidates × 3 scenarios (losses)
V = np.array([[1.0, 5.0, 3.0],
              [2.0, 2.0, 4.0],
              [4.0, 1.0, 2.0]])


def test_cvar_hand_values():
    x = np.arange(1, 11, dtype=float)
    assert cvar(x, 0.8) == pytest.approx(9.5)
    assert cvar(x, 0.75) == pytest.approx((10 + 9 + 0.5 * 8) / 2.5)
    assert cvar(x, 0.8, minimize=False) == pytest.approx(1.5)
    assert cvar(x, 0.0) == pytest.approx(5.5)
    assert cvar(x, 0.95) == pytest.approx(10.0)
    # weighted: mass 0.5 on 10 → CVaR_0.5 = 10
    assert cvar([1.0, 10.0], 0.5, weights=[0.5, 0.5]) == pytest.approx(10.0)
    assert cvar([1.0, 10.0], 0.5, weights=[0.8, 0.2]) == pytest.approx((0.2 * 10 + 0.3 * 1) / 0.5)
    with pytest.raises(ValueError):
        cvar(x, 1.0)


def test_mean_quantile_worst():
    assert weighted_mean([1, 2, 3]) == pytest.approx(2)
    assert weighted_mean([1, 3], [3, 1]) == pytest.approx(1.5)
    assert quantile([1, 2, 3, 4], 0.5) == pytest.approx(2.5)
    assert quantile([1, 2, 3, 4], 0.5, weights=[1, 1, 1, 1]) == 2.0
    assert worst_case([1, 5, 3]) == 5 and worst_case([1, 5, 3], minimize=False) == 1


def test_aggregate_rows():
    np.testing.assert_allclose(aggregate(V, "mean"), [3, 8 / 3, 7 / 3])
    np.testing.assert_allclose(aggregate(V, "worst"), [5, 4, 4])
    np.testing.assert_allclose(aggregate(V, "cvar", alpha=1 / 3), [4, 3, 3])
    np.testing.assert_allclose(aggregate(V, "best"), [1, 2, 1])


def test_regret_hand_values():
    R = regret_matrix(V)
    np.testing.assert_allclose(R, [[0, 4, 1], [1, 1, 2], [3, 0, 0]])
    np.testing.assert_allclose(max_regret(V), [4, 2, 3])
    # maximise: best per scenario is the max
    np.testing.assert_allclose(max_regret(V, minimize=False), [3, 3, 4])


def _st(**kw):
    return ScenarioTable(["A", "B", "C"], ["s1", "s2", "s3"], {"loss": V, "aux": V * 10}, **kw)


def test_robust_select_rules():
    t = _st()
    assert robust_select(t, "loss").label == "B"                       # minimax regret
    assert robust_select(t, "loss", rule="mean").label == "C"
    assert robust_select(t, "loss", rule="cvar", alpha=1 / 3).label == "B"   # B/C tie → index order
    assert robust_select(t, "loss", rule="worst", current="C").label == "C"   # tie → current
    ch = robust_select(t, "loss", rule="worst", current=1)
    assert ch.label == "B" and "current set kept" in ch.reason


def test_policy_constraints_feasible_set_and_blocked():
    t = _st()
    c = Constraint("aux_cap", "aux", "<=", 45.0, agg="worst")   # worst aux: A 50, B 40, C 40
    ch = robust_select(t, "loss", constraints=[c])
    assert ch.label == "B" and not ch.blocked
    # regret is measured against feasible candidates only
    np.testing.assert_allclose(ch.scores[1:], [2, 2])      # B and C tie at 2 → lower index
    tight = Constraint("tight", "aux", "<=", 30.0, agg="worst")
    b = robust_select(t, "loss", constraints=[tight])
    assert b.blocked and b.label in ("B", "C") and "BLOCKED" in b.reason
    assert b.violations["tight"] == pytest.approx(1 / 3)


def test_scenario_feasibility_flags():
    feas = np.array([[True, True, True], [True, False, True], [True, True, True]])
    ch = robust_select(_st(feasible=feas), "loss")
    assert ch.label == "C" and not ch.feasible[1]              # B excluded; C regret 3 < A 4


def test_constraint_from_policy():
    p = Policy()
    c = Constraint.from_policy(p, "max_bad_debt_prob", "pbd", key="B", agg="cvar")
    assert c.bound == 0.01 and c.name == "max_bad_debt_prob[B]"
    g = Constraint.from_policy(p, "attack_share_min", "share", op=">=")
    assert g.violation(0.17) == pytest.approx(0.5) and g.violation(0.5) == 0


def test_tie_break_current_then_smaller_change():
    s = np.array([1.0, 1.0, 1.0, 0.5])
    assert tie_break(s, eligible=np.array([True, True, True, False]), current=2)[0] == 2
    assert tie_break(s, eligible=np.array([True, True, True, False]), distance=np.array([3, 1, 2, 0]))[0] == 1
    assert tie_break(s)[0] == 3


def test_paramset_labels_distance_default():
    base = candidate()
    labels = [base.replace(grace=base["grace"] + 2 * 1152), base, base.replace(grace=base["grace"] + 1152)]
    t = ScenarioTable(labels, ["s"], {"loss": np.array([[1.0], [2.0], [1.0]])})
    # rows 0 and 2 tie; current (row 1) is worse → smaller change wins
    ch = robust_select(t, "loss", rule="mean", current=base)
    assert ch.index == 2
    assert step_distance(labels[0], base) == 2.0


def test_from_tables_alignment():
    base = candidate()
    a, b = base.replace(grace=base["grace"] + 1152), base
    tabs = {}
    for s, (va, vb) in {"calm": (1.0, 2.0), "crash": (5.0, 3.0)}.items():
        rt = ResultTable(base)
        rt.add(a, Metrics({"loss": va}, "loss"))
        rt.add(b, Metrics({"loss": vb}, "loss", constraints={"x": s != "crash"}))
        tabs[s] = rt
    st = ScenarioTable.from_tables(tabs)
    np.testing.assert_allclose(st.column("loss"), [[1, 5], [2, 3]])
    assert st.feasible.tolist() == [[True, True], [True, False]]
    ch = robust_select(st, "loss")
    assert ch.index == 0 and math.isclose(ch.score, 0.0)

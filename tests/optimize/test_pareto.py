"""Pareto fronts, knee point and feasible selection on known sets."""

from __future__ import annotations

import json

import numpy as np

from ybcal.optimize.pareto import (
    front_for_plot,
    knee_point,
    non_dominated_sort,
    objectives_from_table,
    pareto_front,
    ranks,
    select_feasible,
)
from ybcal.params.paramset import candidate
from ybcal.studies.base import Metrics, ResultTable

# min/min: front = 0 (1,5), 1 (2,3), 2 (3,2), 4 (5,1); 3 (4,4) dominated by 1 and 2; 5 (6,6) by all
F = np.array([[1, 5], [2, 3], [3, 2], [4, 4], [5, 1], [6, 6]], dtype=float)


def test_front_min_min():
    assert pareto_front(F, ["min", "min"]) == [0, 1, 2, 4]
    assert non_dominated_sort(F, ["min", "min"]) == [[0, 1, 2, 4], [3], [5]]
    assert ranks(F, ["min", "min"]).tolist() == [0, 0, 0, 1, 0, 2]


def test_front_with_max_direction():
    G = F.copy()
    G[:, 1] = -G[:, 1]                    # second objective now "max" of the negated column
    assert pareto_front(G, ["min", "max"]) == [0, 1, 2, 4]
    # maximise both: (6,6) dominates everything
    assert pareto_front(F, ["max", "max"]) == [5]


def test_duplicates_share_front():
    D = np.array([[1, 1], [1, 1], [2, 2]], dtype=float)
    assert non_dominated_sort(D, ["min", "min"]) == [[0, 1], [2]]


def test_three_objectives():
    X = np.array([[1, 2, 3], [3, 2, 1], [2, 2, 2], [3, 3, 3], [1, 1, 4]], dtype=float)
    assert pareto_front(X, ["min"] * 3) == [0, 1, 2, 4]


def test_knee_point():
    # convex front with a clear knee at (1, 1)
    K = np.array([[0, 10], [1, 1], [10, 0], [5, 5]], dtype=float)
    assert knee_point(K, ["min", "min"]) == 1
    assert knee_point(F, ["min", "min"]) in (1, 2)


def test_select_feasible_primary():
    feas = [False, True, True, True, False, True]
    assert select_feasible(F, ["min", "min"], 0, feas) == 1
    assert select_feasible(F, ["min", "min"], 1, feas) == 2
    assert select_feasible(F, ["min", "min"], 0, [False] * 6) is None
    # ties on primary prefer the non-dominated row, then current
    T = np.array([[1, 3], [1, 2], [1, 2]], dtype=float)
    assert select_feasible(T, ["min", "min"], 0) == 1
    assert select_feasible(T, ["min", "min"], 0, current=2) == 2


def test_front_for_plot_json_ready():
    out = front_for_plot(F, ["min", "min"], ["cost", "risk"], labels=list("abcdef"),
                         feasible=[True] * 6, primary=0, current=3)
    json.dumps(out)
    assert out["front"] == [0, 1, 2, 4] and out["selected"] == 0
    assert out["points"][3]["current"] and not out["points"][3]["pareto"]
    assert sum(p["knee"] for p in out["points"]) == 1


def test_objectives_from_table():
    base = candidate()
    t = ResultTable(base)
    t.add(base, Metrics({"a": 1.0, "b": 2.0}, "a"))
    t.add(base.replace(grace=base["grace"] + 1152), Metrics({"a": 3.0, "b": 0.5}, "a"))
    assert objectives_from_table(t, ["a", "b"]).tolist() == [[1, 2], [3, 0.5]]

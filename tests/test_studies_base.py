"""Study contract helpers: budgets, env RNG, result tables, materiality, recommendations."""

from __future__ import annotations

import contextlib
import csv
import math

import pytest

from ybcal.config import Policy
from ybcal.params.paramset import mainnet
from ybcal.studies.base import (
    BUDGETS,
    STUDY_MODULES,
    Budget,
    Env,
    Metrics,
    Recommendation,
    ResultTable,
    decide_with_materiality,
    final_verdict,
    load_study,
    relative_improvement,
)


def _m(v, ok=True, minimize=True):
    return Metrics({"loss": v, "aux": 2 * v}, primary="loss", minimize=minimize, constraints={"policy": ok})


def _table(rows):
    base = mainnet()
    t = ResultTable(base)
    for grace, v, ok in rows:
        t.add(base.replace(grace=grace), _m(v, ok))
    return t


def test_budgets():
    assert set(BUDGETS) == {"quick", "standard", "deep"}
    assert Budget.named("quick").max_minutes <= 10
    with pytest.raises(ValueError):
        Budget.named("huge")


def test_env_rng_is_order_independent():
    e1 = Env(Policy(), Budget.named("quick"), seed=1)
    e2 = Env(Policy(), Budget.named("quick"), seed=1)
    a = e1.rng_for("G3", "crash").integers(0, 1 << 30, 4)
    e2.rng_for("other").integers(0, 10, 100)
    b = e2.rng_for("G3", "crash").integers(0, 1 << 30, 4)
    assert (a == b).all()
    assert not (e1.rng_for("G3", "calm").integers(0, 1 << 30, 4) == a).all()


def test_keep_when_improvement_not_material():
    t = _table([(34_560, 1.0, True), (40_320, 0.85, True), (46_080, 0.9, True)])
    d = decide_with_materiality(t, Policy())
    assert d.verdict == "KEEP" and d.row.params["grace"] == 34_560
    assert math.isclose(d.improvement, 0.15)


def test_change_when_material():
    t = _table([(34_560, 1.0, True), (40_320, 0.5, True)])
    d = decide_with_materiality(t, 0.2)
    assert d.verdict == "CHANGE" and d.row.params["grace"] == 40_320


def test_infeasible_candidates_ignored_and_ties_toward_current():
    t = _table([(34_560, 1.0, True), (28_800, 0.1, False), (46_080, 0.5, True), (40_320, 0.5, True)])
    d = decide_with_materiality(t, 0.2)
    assert d.verdict == "CHANGE" and d.row.params["grace"] == 40_320  # tie at 0.5 → fewer steps away


def test_current_violation_forces_change_and_none_feasible_blocks():
    t = _table([(34_560, 0.1, False), (40_320, 0.95, True)])
    assert decide_with_materiality(t, 0.2).verdict == "CHANGE"
    t = _table([(34_560, 0.1, False), (40_320, 0.95, False)])
    assert decide_with_materiality(t, 0.2).verdict == "BLOCKED"


def test_current_must_be_evaluated():
    with pytest.raises(ValueError):
        decide_with_materiality(_table([(40_320, 1.0, True)]), 0.2)


def test_relative_improvement_edges():
    assert relative_improvement(0.0, -1.0) == math.inf
    assert relative_improvement(0.0, 0.0) == 0.0
    assert relative_improvement(2.0, 3.0, minimize=False) == 0.5


def test_final_verdict():
    assert final_verdict("KEEP", "synthetic") == "PROVISIONAL"
    assert final_verdict("CHANGE", "real-data") == "CHANGE"
    assert final_verdict("BLOCKED", "synthetic") == "BLOCKED"


def test_result_table_csv_and_queries(tmp_path):
    t = _table([(34_560, 1.0, True), (40_320, 0.5, False)])
    assert t.current() is t.rows[0]
    assert t.argmin().params["grace"] == 40_320
    assert t.feasible().argmin().params["grace"] == 34_560
    assert list(t.column("aux")) == [2.0, 1.0]
    p = t.to_csv(tmp_path / "r.csv")
    rows = list(csv.reader(p.open()))
    assert rows[0] == ["grace", "loss", "aux", "feasible", "violated", "provenance"]
    assert rows[2][:2] == ["40320", "0.5"] and rows[2][3:5] == ["0", "policy"]


def test_recommendation_notes():
    r = Recommendation("grace", 34_560, 34_560, "KEEP", rule="r", binding="b")
    assert r.group == "G4" and r.change_path == "locked" and not r.changed
    assert "start height" in r.klass_note
    x = Recommendation("walletConfirmations", 6, 3, "CHANGE", rule="r", binding="b")
    assert x.change_path == "patch-release" and x.changed
    assert x.to_dict()["change_path"] == "patch-release"
    with pytest.raises(KeyError):
        Recommendation("nosuch", 1, 1, "KEEP", rule="", binding="")


def test_metrics_primary_must_exist():
    with pytest.raises(KeyError):
        Metrics({"a": 1.0}, primary="b")


def test_study_modules_are_stubs_until_implemented():
    assert set(STUDY_MODULES) == {"G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9", "R"}
    for g in STUDY_MODULES:
        with contextlib.suppress(NotImplementedError):
            load_study(g)


def test_forced_move_goes_to_the_nearest_feasible_within_materiality():
    """D-RD-AUD-9: a violating current moves to the closest feasible row whose primary is within
    materiality of the best feasible one — not straight to the best."""
    # current violates; 40,320 (1 step, primary 0.55) is within 20 % of the best 0.5 (2 steps away)
    t = _table([(34_560, 0.1, False), (40_320, 0.55, True), (46_080, 0.5, True)])
    d = decide_with_materiality(t, 0.2)
    assert d.verdict == "CHANGE" and d.row.params["grace"] == 40_320 and "minimal change" in d.reason
    # outside materiality of the best → the best
    t = _table([(34_560, 0.1, False), (40_320, 0.7, True), (46_080, 0.5, True)])
    d = decide_with_materiality(t, 0.2)
    assert d.verdict == "CHANGE" and d.row.params["grace"] == 46_080

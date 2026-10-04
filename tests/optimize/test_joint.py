"""Joint pass (WP-8): coordinate descent on toy studies, failure handling, re-statement, sensitivity."""

from __future__ import annotations

import math
from dataclasses import dataclass

import pytest

from tests.report.toys import TINY, CheapRisk, StubStudy, stub_loader
from ybcal.config import Policy
from ybcal.optimize.joint import (
    TOP_METRICS,
    GroupOutcome,
    TopRiskModel,
    attach_sensitivity,
    build_move,
    collect_design_notes,
    joint_pass,
    joint_sensitivity,
)
from ybcal.params.invariants import Context
from ybcal.params.paramset import mainnet
from ybcal.params.registry import REGISTRY
from ybcal.studies.base import Env, Recommendation, ResultTable


def env(seed: int = 3) -> Env:
    return Env(Policy(owner_pinned={}), TINY, seed=seed)  # pins are tested in test_pins.py


def coupled_spec() -> dict:
    # G3 moves baseRatioBps[0] up one step in round 1; G1's pFastWindow follows baseRatioBps[0]
    # (one step per step), so it can only move in round 2; round 3 confirms the fixed point.
    return {
        "G1": StubStudy("G1", coupled=("pFastWindow", "baseRatioBps[0]", 0)),
        "G3": StubStudy("G3", {"baseRatioBps[0]": 1}),
        "G2": StubStudy("G2"),
    }


def test_coordinate_descent_converges_on_coupled_toys():
    res = joint_pass(mainnet(), env(), loader=stub_loader(coupled_spec()), groups=["G1", "G2", "G3"])
    assert res.converged
    assert len(res.rounds) == 3
    assert set(res.rounds[0].moves) == {"baseRatioBps[0]"}
    assert set(res.rounds[1].moves) >= {"pFastWindow", "pFastMinFill"}
    assert res.rounds[2].moves == {}
    base = mainnet()
    assert res.recommended["baseRatioBps[0]"] == base.as_int("baseRatioBps[0]") + 2500
    assert res.recommended["pFastWindow"] == 96 + 48
    assert res.recommended["pFastMinFill"] == 72  # derived recomputed
    r = res.recommendations["pFastWindow"]
    assert r.current == 96 and r.recommended == 144 and r.verdict == "CHANGE"
    r3 = res.recommendations["baseRatioBps[0]"]
    # decided in round 1, confirmed later: the round-1 decision (made against the shipped value) is kept
    assert r3.current == 50000 and r3.recommended == 52500 and r3.verdict == "CHANGE"
    assert any("confirmed in round 3" in n for n in r3.notes)
    assert r3.metrics["current"]["loss"] > r3.metrics["recommended"]["loss"]
    # moved in round 2: round 3 compared against 144, re-stated against the shipped 96
    assert any("shipped value is 96" in n for n in r.notes)
    assert res.recommendations["pFastMinFill"].recommended == 72


def test_round_limit_reports_non_convergence():
    res = joint_pass(mainnet(), env(), loader=stub_loader(coupled_spec()), groups=["G1", "G3"], max_rounds=1)
    assert not res.converged and len(res.rounds) == 1


def test_missing_and_broken_studies_are_reported_not_raised():
    spec = {"G1": StubStudy("G1", {"pMidWindow": 1}), "G2": "broken"}
    res = joint_pass(mainnet(), env(), loader=stub_loader(spec), groups=["G1", "G2", "G5"])
    assert res.outcomes["G1"].status == "ok"
    assert res.outcomes["G2"].status == "error" and "boom" in res.outcomes["G2"].reason
    assert res.outcomes["G5"].status == "not-run"
    assert set(res.groups_with("ok")) == {"G1"}
    assert res.recommended["pMidWindow"] == 624
    assert not any(REGISTRY[p].group in ("G2", "G5") for p in res.recommendations)


@dataclass
class BadDecide(StubStudy):
    """Recommends a grace above abandonBlocks (W21 violation) — the joint pass must not apply it."""

    def decide(self, results: ResultTable, policy: Policy) -> list[Recommendation]:
        recs = super().decide(results, policy)
        for r in recs:
            if r.param == "grace":
                r.recommended = results.base.as_int("grace") + 1152
                r.verdict = "CHANGE"
        return recs


def test_inadmissible_group_change_is_not_applied():
    res = joint_pass(mainnet(), env(), loader=stub_loader({"G4": BadDecide("G4")}), groups=["G4"])
    assert res.recommended == mainnet()
    assert not res.outcomes["G4"].applied and "abandon_ge_grace" in res.outcomes["G4"].reason
    r = res.recommendations["grace"]
    assert r.recommended == r.current == 34560 and r.verdict == "KEEP"


def test_determinism_per_seed():
    a = joint_pass(mainnet(), env(7), loader=stub_loader(coupled_spec()), groups=["G1", "G3"])
    b = joint_pass(mainnet(), env(7), loader=stub_loader(coupled_spec()), groups=["G1", "G3"])
    assert a.recommended == b.recommended
    strip = lambda r: {k: v for k, v in r.to_dict().items() if k != "notes"}  # noqa: E731
    assert {k: strip(r) for k, r in a.recommendations.items()} == {
        k: strip(r) for k, r in b.recommendations.items()
    }


def test_design_notes_are_aggregated_and_deduplicated():
    spec = {
        "G1": StubStudy("G1", notes=("Classes B/C stay closed early.",)),
        "G3": StubStudy("G3", notes=("classes b/c  stay closed early", "RED-5 residual is 0.")),
    }
    res = joint_pass(mainnet(), env(), loader=stub_loader(spec), groups=["G1", "G3"])
    texts = [d.text for d in res.design_notes]
    assert len(texts) == 2 and texts[0] == "Classes B/C stay closed early."
    assert res.design_notes[0].groups == ("G1", "G3")
    o = GroupOutcome("G9", "ok", design_notes=["from design_notes()"])
    rec = Recommendation(
        "qLowBps", 3333, 3500, "CHANGE", "r", "b", notes=["Design note: qLow vs entity share."]
    )
    res.outcomes["G3"].run.recommendations.append(rec)
    assert "qLow vs entity share." in [d.text for d in collect_design_notes(res.outcomes)]
    assert [d.text for d in collect_design_notes({"G9": o})] == ["from design_notes()"]


def test_build_move_couples_class_bounds_and_grace():
    ctx = Context.from_policy(Policy())
    b = mainnet()
    ps = build_move(b, {"classMax[0]": 103680 + 1152}, ctx)
    assert ps is not None and ps["classMin[1]"] == 103680 + 1152 + 1
    ps = build_move(b, {"grace": 34560 + 1152}, ctx)
    assert ps is not None and ps["abandonBlocks"] == 34560 + 1152
    # mSelect + kSlack would exceed bundleMax: greedy keeps what is admissible
    ps = build_move(b, {"mSelect": 5, "pinWindow": 336}, ctx)
    assert ps is not None and ps["mSelect"] == 4 and ps["pinWindow"] == 336


@pytest.mark.parametrize("method", ["sobol", "morris"])
def test_joint_sensitivity_on_a_cheap_model(method):
    e = env()
    base = mainnet()
    names = ["pFastWindow", "baseRatioBps[1]", "enforcementFloor", "nSlots", "grace"]
    s = joint_sensitivity(
        base, e, method=method, params=names, fn=CheapRisk(), n=64, workers=1, max_evals=10_000
    )
    assert not s.grouped and s.params == names
    key = "ST" if method == "sobol" else "share"
    bd = {f: d[key] for f, d in s.indices["bad_debt_prob"].items()}
    assert bd["baseRatioBps[1]"] > 0.5 and bd["nSlots"] == 0.0
    assert set(s.insensitive()) >= {"nSlots", "grace"}
    assert "pFastWindow" not in s.insensitive() and "baseRatioBps[1]" not in s.insensitive()
    t = s.tornado["bad_debt_prob"]["baseRatioBps[1]"]
    assert t["y_lo"] > s.base_values["bad_debt_prob"] > t["y_hi"]
    assert s.per_param["enforcementFloor"]["dominant"] == "false_halt_h_per_year"
    assert "Insensitive" in s.sentence("nSlots")


def test_joint_sensitivity_groups_when_over_the_cap_and_attaches():
    e = env()
    s = joint_sensitivity(mainnet(), e, fn=CheapRisk(), workers=1, max_evals=50)
    assert s.grouped and "G3 group" in s.factors and len(s.params) > 50
    assert "nSlots" in s.insensitive()
    assert "baseRatioBps[1]" not in s.insensitive()
    assert "baseRatioBps[0]" in s.insensitive()  # sensitive group, but no own tornado effect
    rec = Recommendation("nSlots", 9, 10, "CHANGE", "r", "b")
    attach_sensitivity({"nSlots": rec}, s)
    assert rec.sensitivity["joint"]["insensitive"] and rec.sensitivity["insensitive"]
    assert rec.verdict == "CHANGE" and any("insensitive" in n for n in rec.notes)


def test_top_risk_model_is_cheap_deterministic_and_finite():
    e = env()
    m = TopRiskModel()
    a = m(mainnet(), e)
    b = m(mainnet(), env())
    assert a.values == b.values
    assert set(TOP_METRICS) <= set(a.values)
    assert all(math.isfinite(v) for v in a.values.values())
    assert 0.0 < a["attack_share"] < 1.0
    c = m(mainnet().replace({"baseRatioBps[1]": 60000}), e)
    assert c["bad_debt_prob_B"] <= a["bad_debt_prob_B"]


@dataclass
class FailsLater(StubStudy):
    """Works in round 1 (on the shipped set), raises once another group has moved something."""

    def evaluate(self, cand, env):
        if cand["baseRatioBps[0]"] != 50000:
            raise ValueError("cannot convert float NaN to integer")
        return super().evaluate(cand, env)


def test_later_round_failure_keeps_the_earlier_result():
    spec = {"G1": FailsLater("G1", {"pMidWindow": 1}), "G3": StubStudy("G3", {"baseRatioBps[0]": 1})}
    res = joint_pass(mainnet(), env(), loader=stub_loader(spec), groups=["G1", "G3"])
    o = res.outcomes["G1"]
    assert o.status == "ok" and o.round == 1 and "round 2 re-run failed" in o.reason
    r = res.recommendations["pMidWindow"]
    assert r.recommended == 624 and any("re-run of G1 failed" in n for n in r.notes)
    assert res.rounds[1].statuses["G1"].startswith("error")

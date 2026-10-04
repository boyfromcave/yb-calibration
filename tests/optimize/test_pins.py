"""Owner-pinned parameters (D-RD-INF-2): studied, kept, evidence shown, never CHANGE or patched."""

from __future__ import annotations

import pytest

from tests.optimize.toys import Bowl, ToyStudy
from tests.report.toys import TINY, CheapRisk, StubStudy, stub_loader
from ybcal.config import Policy
from ybcal.optimize.joint import joint_pass
from ybcal.optimize.pins import apply_owner_pins, pin_info, verdict_label
from ybcal.optimize.runner import run_group
from ybcal.params.paramset import mainnet
from ybcal.report.build import RecommendConfig, run_recommend
from ybcal.studies.base import Budget, Env

BASE = mainnet()
G, A = BASE.as_int("grace"), BASE.as_int("abandonBlocks")


def env(pins: dict[str, str]) -> Env:
    return Env(Policy(owner_pinned=pins), Budget.named("quick"), seed=5, provenance="real-data")


def toy() -> ToyStudy:
    # the bowl wants grace +2 steps and abandonBlocks +2 steps
    return ToyStudy(Bowl((("grace", G + 2 * 1152), ("abandonBlocks", A + 2 * 1152))))


def test_unpinned_moves_both():
    run = run_group(toy(), BASE, env({}), workers=1)
    recs = {r.param: r for r in run.recommendations}
    assert recs["grace"].recommended == G + 2304 and recs["abandonBlocks"].recommended == A + 2304


def test_pinned_value_is_kept_with_evidence_and_others_decided_given_the_pin():
    # grace pinned: abandonBlocks may still move up (abandon_ge_grace keeps the other order inadmissible)
    run = run_group(toy(), BASE, env({"grace": "D-R-6"}), workers=1)
    recs = {r.param: r for r in run.recommendations}
    gr, ab = recs["grace"], recs["abandonBlocks"]
    assert gr.recommended == G and gr.verdict == "KEEP"
    pin = pin_info(gr)
    assert pin is not None and pin["ref"] == "D-R-6"
    assert pin["evidence_value"] == G + 2304 and pin["evidence_points_elsewhere"]
    assert pin["evidence_verdict"] == "CHANGE"
    assert "loss" in pin["risk"] and "at the evidence value" in pin["risk"]
    assert gr.rule.startswith("Owner decision D-R-6")
    assert any(n.startswith("Owner pin D-R-6: the evidence points to") for n in gr.notes)
    # the other parameter is still optimised, with the pin held
    assert ab.recommended == A + 2304 and ab.verdict == "CHANGE" and pin_info(ab) is None
    assert run.recommended["grace"] == G
    assert verdict_label(gr, gr.verdict) == "KEEP (owner decision D-R-6)"


def test_pin_where_evidence_agrees():
    st = ToyStudy(Bowl((("grace", G), ("abandonBlocks", A))))
    run = run_group(st, BASE, env({"abandonBlocks": "W21", "grace": "D-R-6"}), workers=1)
    for r in run.recommendations:
        if r.param in ("grace", "abandonBlocks"):
            pin = pin_info(r)
            assert r.verdict == "KEEP" and not pin["evidence_points_elsewhere"]
            assert pin["risk"].startswith("none found")


def test_pin_on_a_blocked_parameter_is_keep_with_the_risk():
    st = StubStudy("G9", {"minMint": 2}, blocked=("minMint",))
    e = Env(Policy(owner_pinned={"minMint": "X-1"}), TINY, seed=1, provenance="real-data")
    run = run_group(st, BASE, e, workers=1)
    r = {x.param: x for x in run.recommendations}["minMint"]
    assert r.verdict == "KEEP" and r.recommended == BASE["minMint"]
    assert pin_info(r)["evidence_blocked"] and pin_info(r)["evidence_verdict"] == "BLOCKED"


def test_redecide_failure_still_pins(monkeypatch):
    st = toy()
    run = run_group(st, BASE, env({}), workers=1)
    calls = []

    def boom(table, policy):
        calls.append(1)
        raise RuntimeError("no")

    monkeypatch.setattr(st, "decide", boom)
    recs, warns = apply_owner_pins(st, run.table, run.recommendations, Policy(owner_pinned={"grace": "D"}),
                                   BASE)
    assert calls and warns and "re-deciding" in warns[0]
    assert {r.param: r for r in recs}["grace"].recommended == G


def test_policy_rejects_unknown_pins():
    with pytest.raises(KeyError, match="owner_pinned"):
        Policy(owner_pinned={"noSuchParam": "X"})
    with pytest.raises(KeyError):
        Policy(owner_pinned={"recapRatioBps": "W16"})  # derived, not tunable


def test_default_policy_pins_the_plan_decisions():
    pins = Policy().owner_pinned
    assert pins["abandonBlocks"].startswith("W21") and pins["supplyCapBps"].startswith("W20")
    assert pins["attestFeeBps"] == "D-3" and pins["grace"].startswith("D-R-6")
    assert all(f"class{m}[{i}]" in pins for m in ("Min", "Max") for i in range(3))
    assert Policy.load("policy/real-data-2026-10.toml").owner_pinned == pins


def test_joint_pass_treats_pins_as_fixed_and_report_renders_them(tmp_path):
    spec = {"G4": StubStudy("G4", {"abandonBlocks": 2}), "G6": StubStudy("G6", {"feeBps": 1, "peerMin": 1})}
    pol = Policy(owner_pinned={"feeBps": "D-T (test)"})
    e = Env(pol, TINY, seed=2, provenance="real-data")
    j = joint_pass(BASE, e, groups=["G4", "G6"], loader=stub_loader(spec), workers=1)
    assert j.recommended["feeBps"] == BASE["feeBps"] and j.recommended["abandonBlocks"] != A
    assert j.recommended["peerMin"] != BASE["peerMin"]
    rec = j.recommendations["feeBps"]
    assert rec.verdict == "KEEP" and pin_info(rec)["evidence_points_elsewhere"]

    cfg = RecommendConfig(budget=TINY, policy=pol, out=tmp_path / "r", workers=1, groups=["G4", "G6"])
    res = run_recommend(cfg, loader=stub_loader(spec), sensitivity_fn=CheapRisk(), on_event=lambda m: None)
    md = (res.out / "report.md").read_text()
    assert "Owner decisions the evidence argues against" in md
    assert "`feeBps` — KEEP (owner decision D-T (test))" in md
    assert "> **Owner decision D-T (test): the value is kept.** The evidence points to" in md
    patch = (res.out / "params.cpp.patch").read_text()
    assert "feeBps" not in patch and "peerMin" in patch
    html = (res.out / "report.html").read_text()
    assert "Owner decisions the evidence argues against" in html
    check = [ln for ln in md.splitlines() if ln.startswith("| Owner-pinned parameters hold")]
    assert check and "| pass |" in check[0] and "evidence points elsewhere for feeBps" in check[0]


THR = ("activationThreshold", "participationFloor", "enforcementFloor", "enforcementResume")


class FracStudy:
    """G5 toy: candidates scale signalWindow with the thresholds at the shipped fractions or not."""

    group = "G5"

    def __init__(self, best: str) -> None:
        from ybcal.params.registry import params_for_group

        self.params = params_for_group("G5")
        self.best = best

    def space(self, base, budget):
        sw = base.as_int("signalWindow")
        scaled = base.replace({"signalWindow": 2592, **{t: round(base.as_int(t) * 2592 / sw) for t in THR}})
        raw = base.replace({"signalWindow": 2592, "activationThreshold": 2000})
        return [base, scaled, raw]

    def evaluate(self, cand, env):
        from ybcal.studies.base import Metrics

        loss = 1.0
        if cand.as_int("signalWindow") == 2592:
            loss = 0.5 if (cand.as_int("activationThreshold") == 2000) == (self.best == "raw") else 0.7
        return Metrics({"loss": loss}, "loss", True, {"ok": True}, "real-data")

    def decide(self, results, policy):
        from ybcal.studies.base import Recommendation, decide_with_materiality

        d = decide_with_materiality(results, 0.0)
        return [Recommendation(p, results.base[p], d.row.params[p], d.verdict, "toy", "loss")
                for p in self.params]

    def explain(self, rec, results):
        return "toy"


@pytest.mark.parametrize("best", ["scaled", "raw"])
def test_fraction_pins_follow_the_signal_window(best):
    pins = {t: {"ref": "L3", "of": "signalWindow"} for t in THR}
    run = run_group(FracStudy(best), BASE, env(pins), workers=1, neighbours=0)
    recs = {r.param: r for r in run.recommendations}
    assert recs["signalWindow"].recommended == 2592
    assert [recs[t].recommended for t in THR] == [1944, 1556, 1296, 1556]
    a = recs["activationThreshold"]
    assert a.verdict == "CHANGE" and pin_info(a)["evidence_points_elsewhere"] == (best == "raw")
    assert verdict_label(a, a.verdict) == "CHANGE (owner decision L3: 75% of signalWindow)"


def test_policy_accepts_fraction_pins_and_rejects_bad_parents():
    Policy(owner_pinned={"activationThreshold": {"ref": "L3", "of": "signalWindow"}})
    with pytest.raises(KeyError):
        Policy(owner_pinned={"activationThreshold": {"ref": "L3", "of": "nope"}})
    assert Policy().owner_pinned["enforcementFloor"] == {"ref": "L3", "of": "signalWindow"}


def test_environment_blocked_rows_are_not_blocked_after_the_joint_pass():
    from ybcal.studies.envlimit import env_info

    class EnvBlocked(StubStudy):
        def decide(self, results, policy):
            recs = super().decide(results, policy)
            for r in recs:
                if r.param == "feeBps":
                    r.verdict = "BLOCKED"
                    r.metrics["environment_blocked"] = {"constraints": ["c"], "note": "G6-ENV-X",
                                                        "why": "w", "exposure": "e"}
            return recs

    e = Env(Policy(owner_pinned={}), TINY, seed=2, provenance="real-data")
    j = joint_pass(BASE, e, groups=["G6"], loader=stub_loader({"G6": EnvBlocked("G6")}), workers=1)
    r = j.recommendations["feeBps"]
    assert r.verdict == "KEEP" and env_info(r)["note"] == "G6-ENV-X"

"""Environment-limited constraints (D-RD-INF-3): least harm, not BLOCKED, exposure stated."""

from __future__ import annotations

from dataclasses import dataclass

from tests.report.toys import TINY, CheapRisk, StubStudy, stub_loader

from ybcal.config import Policy
from ybcal.optimize.pins import verdict_label
from ybcal.params.paramset import mainnet
from ybcal.report.build import RecommendConfig, run_recommend
from ybcal.studies.base import Metrics, Recommendation, ResultTable, final_verdict
from ybcal.studies.envlimit import (
    EnvironmentLimit,
    attach_environment,
    decide_with_environment,
    env_info,
    unmeetable_constraints,
)

BASE = mainnet()
W = BASE.as_int("pFastWindow")

LIMIT = EnvironmentLimit(
    "attack_share_min",
    "attack_share",
    False,
    "G1-ENV-1",
    "the top real pool mines 52 % of blocks > attack_share_min 0.34",
    lambda r: f"a 52 % pool sets the medians; attack share {r.metrics['attack_share']:.0%}",
    fix="pool diversity (L4 launch bar), not a window",
)


def table(shares: dict[int, float], extra_ok: dict[int, bool] | None = None) -> ResultTable:
    t = ResultTable(BASE)
    for d, a in shares.items():
        cons = {"attack_share_min": a >= 0.52, "max_no_price_hours": (extra_ok or {}).get(d, True)}
        t.add(
            BASE.replace(pFastWindow=W + d * 8) if d else BASE,
            Metrics({"objective": 1.0 + abs(d), "attack_share": a}, "objective", True, cons, "real-data"),
        )
    return t


def test_unmeetable_constraints():
    t = table({0: 0.30, 1: 0.31, -1: 0.29})
    assert unmeetable_constraints(t) == ["attack_share_min"]


def test_least_harm_change_with_exposure():
    t = table({0: 0.30, 1: 0.31, 2: 0.40, -1: 0.29})
    d = decide_with_environment(t, 0.2, [LIMIT])
    assert d.verdict == "CHANGE" and d.row.params["pFastWindow"] == W + 16  # 0.40, nothing within 20 %
    # minimal change: a nearer value within materiality of the best wins (0.33 vs 0.40)
    t2 = table({0: 0.30, 1: 0.33, 2: 0.40})
    assert decide_with_environment(t2, 0.2, [LIMIT]).row.params["pFastWindow"] == W + 8
    e = d.environment
    assert e["constraints"] == ["attack_share_min"] and e["note"] == "G1-ENV-1"
    assert e["harm_at_choice"] == 0.40 and e["harm_at_current"] == 0.30
    assert "attack share 40%" in e["exposure"]


def test_least_harm_keep_within_materiality():
    t = table({0: 0.38, 1: 0.40, -1: 0.30})
    d = decide_with_environment(t, 0.2, [LIMIT])
    assert d.verdict == "KEEP" and d.row.params["pFastWindow"] == W
    assert d.environment is not None


def test_other_unmeetable_constraint_stays_blocked():
    t = table({0: 0.30, 1: 0.33}, extra_ok={0: False, 1: False})
    d = decide_with_environment(t, 0.2, [LIMIT])
    assert d.verdict == "BLOCKED" and d.environment is None


def test_meetable_policy_is_ordinary_decision():
    t = table({0: 0.60, 1: 0.70})
    d = decide_with_environment(t, 0.2, [LIMIT])
    assert d.verdict == "KEEP" and d.environment is None


def test_attach_and_label():
    t = table({0: 0.30, 1: 0.40})
    d = decide_with_environment(t, 0.2, [LIMIT])
    r = Recommendation("pFastWindow", W, d.row.params["pFastWindow"], d.verdict, "rule", "attack_share")
    attach_environment(r, d)
    assert env_info(r)["note"] == "G1-ENV-1"
    assert r.metrics["design_notes"][0]["id"] == "G1-ENV-1"
    want = "CHANGE — policy unmeetable in this environment (design note G1-ENV-1)"
    assert verdict_label(r, r.verdict) == want
    assert r.notes[0].startswith("Environment limit (attack_share_min)")


@dataclass
class EnvStub(StubStudy):
    """G1 stub whose decide is environment-limited for pFastWindow."""

    def decide(self, results: ResultTable, policy: Policy) -> list[Recommendation]:
        recs = super().decide(results, policy)
        for r in recs:
            if r.param == "pFastWindow":
                d = decide_with_environment(table({0: 0.30, 1: 0.40}), policy, [LIMIT])
                r.recommended = d.row.params["pFastWindow"]
                r.verdict = final_verdict(d.verdict, "real-data")
                attach_environment(r, d)
        return recs


def test_report_shows_environment_limit_not_blocked(tmp_path):
    spec = {"G1": EnvStub("G1"), "G9": StubStudy("G9", {"minMint": 1})}
    cfg = RecommendConfig(budget=TINY, policy=Policy(), out=tmp_path / "r", workers=1, groups=["G1", "G9"])
    res = run_recommend(cfg, loader=stub_loader(spec), sensitivity_fn=CheapRisk(), on_event=lambda m: None)
    md = (res.out / "report.md").read_text()
    assert "Policy unmeetable in this environment — not a parameter failure" in md
    assert "`pFastWindow` — CHANGE — policy unmeetable in this environment (design note G1-ENV-1)" in md
    assert "a 52 % pool sets the medians" in md
    assert "G1-ENV-1" in md.split("## 5. Design notes", 1)[1]
    lines = {ln.split("|")[1].strip(): ln for ln in md.splitlines() if ln.startswith("| ")}
    assert "| pass |" in lines["No parameter is BLOCKED by the policy"]
    assert "| pass |" in lines["Environment-limited policy constraints are stated with their exposure"]
    assert res.counts["BLOCKED"] == 0
    assert "cannot meet the policy in this environment" in md.split("**Top remaining risks**", 1)[1]
    assert "Policy unmeetable in this environment" in (res.out / "report.html").read_text()

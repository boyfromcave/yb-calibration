"""Picklable stub studies and a cheap risk model for the report / joint-pass tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ybcal.config import Policy
from ybcal.optimize.joint import TOP_METRICS
from ybcal.params.paramset import ParamSet
from ybcal.params.registry import REGISTRY, params_for_group
from ybcal.studies.base import (
    BUDGETS,
    Budget,
    Env,
    Metrics,
    Recommendation,
    ResultTable,
    decide_with_materiality,
    final_verdict,
    load_study,
)

TINY = Budget(
    "quick",
    paths=2,
    block_horizon_days=10,
    hour_horizon_years=1.0,
    grid_points=3,
    lhs_samples=4,
    halving_rounds=1,
    scenario_set="core",
    morris_trajectories=2,
    sobol_samples=8,
    max_minutes=1,
)


def tunable(group: str) -> list[str]:
    return [p for p in params_for_group(group) if REGISTRY[p].tunable]


@dataclass
class StubStudy:
    """One-at-a-time ±1 step around the base for each tunable param of ``group``; loss is a bowl
    around ``targets`` (steps from the shipped value), optionally coupled to another parameter:
    the target of ``coupled[0]`` is the current value of ``coupled[1]`` scaled (a G1→G3-like link)."""

    group: str
    targets: dict[str, int] = field(default_factory=dict)  #: param → offset in steps from shipped
    provenance: str = "real-data"
    coupled: tuple[str, str, int] | None = None  #: (param, source, offset steps)
    notes: tuple[Any, ...] = ()  #: design notes: strings or {id, title, finding, …} dicts
    blocked: tuple[str, ...] = ()  #: params reported BLOCKED (least-violating = the bowl's best)
    params: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        self.params = params_for_group(self.group)

    def _target(self, p: str, cand: ParamSet) -> int:
        spec = REGISTRY[p]
        t = int(spec.mainnet) + self.targets.get(p, 0) * spec.step  # type: ignore[arg-type]
        if self.coupled and self.coupled[0] == p:
            src = self.coupled[1]
            moved = (cand.as_int(src) - int(REGISTRY[src].mainnet)) // REGISTRY[src].step  # type: ignore[arg-type]
            t = int(spec.mainnet) + (moved + self.coupled[2]) * spec.step  # type: ignore[arg-type]
        return t

    def space(self, base: ParamSet, budget: Budget) -> list[ParamSet]:
        out = [base]
        for p in tunable(self.group):
            s = REGISTRY[p].step
            for k in (-1, 1):
                try:
                    c = base.replace({p: base.as_int(p) + k * s})
                except (TypeError, ValueError):
                    continue
                out.append(c)
        return out

    def evaluate(self, cand: ParamSet, env: Env) -> Metrics:
        loss = 1.0
        for p in tunable(self.group):
            loss += ((cand.as_int(p) - self._target(p, cand)) / REGISTRY[p].step) ** 2
        return Metrics(
            {"loss": loss, "seed": float(env.seed % 1000)},
            "loss",
            True,
            {"ok": True},
            self.provenance,
            {"out_dir": env.out_dir},
        )  # type: ignore[arg-type]

    def decide(self, results: ResultTable, policy: Policy) -> list[Recommendation]:
        d = decide_with_materiality(results, 0.0, params=self.params)
        cur = results.current(self.params)
        assert cur is not None
        ev: list[Path] = []
        od = cur.metrics.meta.get("out_dir")
        if od:
            ev.append(results.to_csv(Path(od) / self.group.lower() / "stub.csv"))
        recs = []
        for p in self.params:
            recs.append(
                Recommendation(
                    param=p,
                    current=results.base[p],
                    recommended=d.row.params[p],
                    verdict=final_verdict(d.verdict, d.row.metrics.provenance),
                    rule="stub: minimise the bowl",
                    binding="loss",
                    metrics={
                        "current": {"loss": cur.metrics.primary_value},
                        "recommended": {"loss": d.row.metrics.primary_value},
                        "design_notes": list(self.notes),
                    },
                    confidence="medium",
                    provenance=d.row.metrics.provenance,
                    evidence=ev,
                    group=self.group,
                    notes=[d.reason],
                )
            )
        for r in recs:
            if r.param in self.blocked:
                r.verdict = "BLOCKED"
                r.binding = "max_bad_debt_prob[B]"
                r.metrics["least_violating"] = {
                    "delta": {r.param: r.recommended},
                    "values": {"pbad.B": 0.031},
                }
                r.metrics["decision"] = "no candidate satisfies max_bad_debt_prob[B]"
        return recs

    def explain(self, rec: Recommendation, results: ResultTable) -> str:
        return f"{rec.param}: stub explanation; verdict {rec.verdict}."


@dataclass
class BrokenStudy(StubStudy):
    """Raises inside evaluate."""

    def evaluate(self, cand: ParamSet, env: Env) -> Metrics:
        raise RuntimeError("boom")


def stub_loader(spec: dict[str, Any]):
    """A loader: ``spec[g]`` is a StubStudy, ``"real"`` (load the merged study), ``"missing"`` or
    ``"broken"``; groups not in ``spec`` are missing."""

    def load(g: str):
        v = spec.get(g, "missing")
        if v == "real":
            return load_study(g)
        if v == "missing":
            raise NotImplementedError(f"study {g} not implemented yet (stub)")
        if v == "broken":
            return BrokenStudy(g)
        return v

    return load


@dataclass(frozen=True)
class CheapRisk:
    """A deterministic risk model with the TOP_METRICS names (fast sensitivity tests)."""

    def __call__(self, cand: ParamSet, env: Env) -> Metrics:
        g = cand.as_int("baseRatioBps[1]") / 40000
        w = cand.as_int("pFastWindow") / 96
        vals = {
            "bad_debt_prob": 0.05 / g + 0.001 * w,
            "price_halt_h_per_year": 3.0 * w,
            "false_halt_h_per_year": cand.as_int("enforcementFloor") / 1008,
            "attack_share": 0.4,
            "bad_debt_prob_A": 0.004,
            "bad_debt_prob_B": 0.05 / g,
            "bad_debt_prob_C": 0.2,
        }
        assert set(TOP_METRICS) <= set(vals)
        return Metrics(vals, "bad_debt_prob", True, {}, "synthetic")


__all__ = ["BUDGETS", "TINY", "BrokenStudy", "CheapRisk", "StubStudy", "stub_loader"]

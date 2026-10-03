"""Picklable toy objectives and a toy Study for the optimizer tests (module level on purpose)."""

from __future__ import annotations

from dataclasses import dataclass, field

from ybcal.config import Policy
from ybcal.params.paramset import ParamSet
from ybcal.params.registry import REGISTRY, params_for_group
from ybcal.studies.base import (
    Budget,
    Env,
    Metrics,
    Recommendation,
    ResultTable,
    decide_with_materiality,
    final_verdict,
)


@dataclass(frozen=True)
class Bowl:
    """``loss = 1 + w·Σ((v − target)/step)²`` plus Monte-Carlo noise of sd ``noise/√paths``."""

    targets: tuple[tuple[str, int], ...]
    w: float = 1.0
    noise: float = 0.0
    provenance: str = "real-data"

    def __call__(self, cand: ParamSet, env: Env) -> Metrics:
        loss = 1.0
        for p, t in self.targets:
            loss += self.w * ((cand.as_int(p) - t) / REGISTRY[p].step) ** 2
        if self.noise:
            loss += self.noise * float(env.rng.standard_normal(env.budget.paths).mean())
        return Metrics({"loss": loss, "paths": float(env.budget.paths)}, primary="loss",
                       constraints={"ok": True}, provenance=self.provenance)  # type: ignore[arg-type]


@dataclass
class ToyStudy:
    """G4 toy: ``space`` = ±2 steps of grace × abandonBlocks around base; loss = :class:`Bowl`."""

    bowl: Bowl
    group: str = "G4"
    params: tuple[str, ...] = field(default_factory=lambda: params_for_group("G4"))
    k: int = 2

    def space(self, base: ParamSet, budget: Budget) -> list[ParamSet]:
        out = []
        for dg in range(-self.k, self.k + 1):
            for da in range(-self.k, self.k + 1):
                out.append(base.replace(grace=base.as_int("grace") + dg * 1152,
                                        abandonBlocks=base.as_int("abandonBlocks") + da * 1152))
        return out

    def evaluate(self, cand: ParamSet, env: Env) -> Metrics:
        return self.bowl(cand, env)

    def decide(self, results: ResultTable, policy: Policy) -> list[Recommendation]:
        d = decide_with_materiality(results, policy, params=self.params)
        cur = results.current(self.params)
        assert cur is not None
        recs = []
        for p in self.params:
            recs.append(Recommendation(
                param=p, current=results.base[p], recommended=d.row.params[p],
                verdict=final_verdict(d.verdict, d.row.metrics.provenance), rule="min loss, materiality",
                binding="loss", metrics={"current": cur.metrics.primary_value,
                                         "recommended": d.row.metrics.primary_value},
                provenance=d.row.metrics.provenance, notes=[d.reason]))
        return recs

    def explain(self, rec: Recommendation, results: ResultTable) -> str:
        return f"{rec.param}: {rec.verdict} ({rec.notes[0]})"


def linear(X):
    """``3·x1 − 2·x2 + 0.5·x3``."""
    return 3 * X[:, 0] - 2 * X[:, 1] + 0.5 * X[:, 2]

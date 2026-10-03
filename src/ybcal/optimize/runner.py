"""High-level driver: one study group through search → evaluate → decide → neighbour evidence.

:func:`optimize_group` is the frozen-protocol driver of ``studies/base.py``'s docstring, plus the
optimizer features: alternative candidate sources (the study's own ``space()``, a registry grid,
Latin hypercube, successive halving), invariant screening with counted rejections, a shared cache,
optional process parallelism, timing, and "why not the neighbours" evidence attached to every
recommendation.

WP-8's joint pass (``optimize/joint.py``) can do coordinate descent with it::

    cache = EvalCache.on_disk()
    current = base
    for rnd in range(policy.max_rounds_joint):
        moved = False
        for g in GROUP_ORDER:
            run = run_group(load_study(g), current, env, method="space", cache=cache)
            new = recommended_set(current, run.recommendations)
            moved |= new != current
            current = new
        if not moved:
            break

and score any set directly with :func:`evaluate_set` (same cache keys).
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from ybcal.optimize.evaluate import (
    EvalCache,
    EvalStats,
    evaluate_many,
    evaluate_set,
    evaluator_id,
    with_budget,
)
from ybcal.optimize.search import (
    CandidateSet,
    Coupling,
    Feasibility,
    HalvingResult,
    SearchSpace,
    grid,
    latin_hypercube,
    neighbourhood,
    screen,
    successive_halving,
)
from ybcal.optimize.sensitivity import OATResult
from ybcal.params.invariants import Context
from ybcal.params.paramset import ParamSet
from ybcal.params.registry import REGISTRY
from ybcal.studies.base import (
    Budget,
    Env,
    Metrics,
    Recommendation,
    ResultTable,
    Study,
    missing_recommendations,
    relative_improvement,
)

if TYPE_CHECKING:
    from ybcal.config import Policy

OWNER_WP = "WP-6"

Method = Literal["space", "grid", "lhs", "halving"]

__all__ = [
    "GroupRun",
    "Method",
    "NeighbourPoint",
    "evaluate_set",
    "optimize_group",
    "recommended_set",
    "run_group",
]


@dataclass(frozen=True)
class NeighbourPoint:
    """One neighbour of the recommended value (others held at the recommended set)."""

    param: str
    value: Any
    steps: int                    #: signed offset in registry steps
    primary: float | None         #: None when rejected
    improvement: float | None     #: relative improvement over the recommended set (> 0 = better)
    feasible: bool
    violated: tuple[str, ...] = ()
    rejected: tuple[str, ...] = ()  #: invariant / predicate names when the set was inadmissible

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready."""
        return {"value": self.value, "steps": self.steps, "primary": self.primary,
                "improvement": self.improvement, "feasible": self.feasible,
                "violated": list(self.violated), "rejected": list(self.rejected)}


@dataclass
class GroupRun:
    """Everything one :func:`run_group` call produced."""

    group: str
    method: str
    table: ResultTable
    recommendations: list[Recommendation]
    candidates: CandidateSet
    recommended: ParamSet
    neighbours: dict[str, list[NeighbourPoint]] = field(default_factory=dict)
    neighbour_table: ResultTable | None = None
    halving: HalvingResult | None = None
    stats: EvalStats = field(default_factory=EvalStats)
    seconds: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def n_invalid(self) -> int:
        """Candidates rejected before evaluation."""
        return self.candidates.n_invalid

    def summary(self) -> str:
        """``G3 [grid]: 25 candidates, 2 rejected (window_order×2); 25 evaluated (0 cached) in 1.2 s``."""
        return (f"{self.group} [{self.method}]: {self.candidates.summary()}; {self.stats.evaluated} "
                f"evaluated ({self.stats.cached} cached, {self.stats.workers} worker"
                f"{'s' if self.stats.workers != 1 else ''}) in {self.seconds.get('total', 0.0):.1f} s")


def recommended_set(base: ParamSet, recs: Sequence[Recommendation]) -> ParamSet:
    """``base`` with every *tunable* recommendation applied (derived values recomputed)."""
    ch = {r.param: r.recommended for r in recs
          if r.param in REGISTRY and REGISTRY[r.param].tunable and r.recommended != base[r.param]}
    return base.replace(ch) if ch else base


def _source(study: Study, base: ParamSet, env: Env, budget: Budget, source: str, *, params: Sequence[str],
            context: Context, couple: Sequence[Coupling], feasible: Sequence[Feasibility]) -> CandidateSet:
    if source == "space":
        return screen(study.space(base, budget), base, method="space", context=context, feasible=feasible)
    space = SearchSpace.for_params(base, params, couple=couple, feasible=feasible, context=context)
    if not space.axes:
        return space.materialize([], source)
    if source == "grid":
        return grid(space, points=budget.grid_points)
    if source == "lhs":
        seed = int(env.rng_for("lhs", study.group).integers(0, 2**63 - 1))
        return latin_hypercube(space, budget.lhs_samples, seed=seed)
    raise ValueError(f"unknown candidate source {source!r}")


def _neighbour_points(study: Study, run_table: ResultTable, rec_set: ParamSet, rec_m: Metrics, env: Env,
                      params: Sequence[str], *, k: int, context: Context, couple: Sequence[Coupling],
                      feasible: Sequence[Feasibility], workers: int | None, cache: EvalCache, crn: bool,
                      stats: EvalStats) -> tuple[dict[str, list[NeighbourPoint]], ResultTable]:
    space = SearchSpace.for_params(rec_set, params, couple=couple, feasible=feasible, context=context)
    nb = neighbourhood(space, rec_set, k=k)
    sets = nb.candidates[1:]
    ms = evaluate_many(study.evaluate, sets, env, workers=workers, cache=cache, crn=crn, stats=stats)
    ntable = ResultTable(run_table.base)
    out: dict[str, list[NeighbourPoint]] = {a.name: [] for a in space.axes}

    def changed(ch: Mapping[str, Any]) -> str | None:
        hits = [a.name for a in space.axes if a.name in ch and ch[a.name] != rec_set[a.name]]
        return hits[0] if hits else None

    for s, m in zip(sets, ms, strict=True):
        ntable.add(s, m)
        p = changed(s.delta(rec_set))
        if p is None:
            continue
        step = REGISTRY[p].step or 1
        out[p].append(NeighbourPoint(
            p, s[p], round((s.as_int(p) - rec_set.as_int(p)) / step) if step else 0, m.primary_value,
            relative_improvement(rec_m.primary_value, m.primary_value, minimize=rec_m.minimize),
            m.feasible, m.violated))
    for rj in nb.rejected:
        p = changed(rj.changes)
        if p is None:
            continue
        v = rj.changes[p]
        step = REGISTRY[p].step or 1
        out[p].append(NeighbourPoint(p, v, round((int(v) - rec_set.as_int(p)) / step), None, None, False,
                                     (), rj.detail))
    for p in out:
        out[p].sort(key=lambda q: q.steps)
    return out, ntable


def _attach(recs: Sequence[Recommendation], neigh: dict[str, list[NeighbourPoint]], rec_set: ParamSet,
            rec_m: Metrics, materiality: float, summary: str) -> None:
    for r in recs:
        r.notes.append(f"Search: {summary}.")
        pts = neigh.get(r.param)
        if not pts:
            continue
        r.sensitivity.setdefault("neighbours", [q.to_dict() for q in pts])
        valid = [q for q in pts if q.primary is not None]
        if valid and isinstance(rec_set[r.param], int):
            xs = [float(rec_set.as_int(r.param))] + [float(q.value) for q in valid]
            ys = [rec_m.primary_value] + [float(q.primary) for q in valid]  # type: ignore[arg-type]
            local = OATResult(r.param, xs, ys, rec_m.primary, base_value=xs[0]).local()  # type: ignore[arg-type]
            r.sensitivity.setdefault("local_slope", local["class"])
            r.sensitivity.setdefault("local_elasticity", local["elasticity"])
        better = [q for q in valid if q.feasible and (q.improvement or 0) > 0]
        for q in better:
            tag = "beyond" if (q.improvement or 0) > materiality else "within"
            r.notes.append(f"Neighbour {r.param}={q.value} ({q.steps:+d} step) scores {q.primary:.4g} vs "
                           f"{rec_m.primary_value:.4g}, {q.improvement:.1%} better — {tag} materiality "
                           f"{materiality:.0%}.")
        rej = [q for q in pts if q.rejected]
        if rej:
            r.notes.append("Neighbours rejected by invariants: "
                           + ", ".join(f"{q.value} ({'/'.join(q.rejected)})" for q in rej) + ".")


def run_group(
    study: Study,
    base: ParamSet,
    env: Env,
    budget: Budget | None = None,
    policy: Policy | None = None,
    *,
    method: Method = "space",
    params: Sequence[str] | None = None,
    workers: int | None = None,
    cache: EvalCache | None = None,
    context: Context | None = None,
    couple: Sequence[Coupling] = (),
    feasible: Sequence[Feasibility] = (),
    neighbours: int = 1,
    halving_source: Literal["space", "grid", "lhs"] = "space",
    eta: int = 3,
    crn: bool = True,
    explain: bool = True,
) -> GroupRun:
    """Run one study group and return the full :class:`GroupRun` (see :func:`optimize_group`)."""
    t_all = time.perf_counter()
    budget = budget or env.budget
    policy = policy or env.policy
    envb = with_budget(env, budget)
    if policy is not env.policy:
        import dataclasses

        envb = dataclasses.replace(envb, policy=policy)
    ctx = context if context is not None else Context.from_policy(policy)
    cache = cache if cache is not None else EvalCache()
    names = tuple(params) if params is not None else tuple(study.params)
    stats = EvalStats()
    secs: dict[str, float] = {}
    warns: list[str] = []

    t = time.perf_counter()
    src = halving_source if method == "halving" else method
    cands = _source(study, base, envb, budget, src, params=names, context=ctx, couple=couple,
                    feasible=feasible)
    if method == "halving":
        cands.method = f"halving({src})"
    secs["space"] = time.perf_counter() - t
    warns.extend(cands.warnings)

    t = time.perf_counter()
    table = ResultTable(base)
    halv = None
    if method == "halving":
        halv = successive_halving(study.evaluate, cands, envb, eta=eta, keep=cands.candidates[:1],
                                  workers=workers, cache=cache, crn=crn, stats=stats)
        for c, m in zip(halv.candidates, halv.metrics, strict=True):
            table.add(c, m)
    else:
        ms = evaluate_many(study.evaluate, cands.candidates, envb, workers=workers, cache=cache, crn=crn,
                           evaluator=evaluator_id(study.evaluate), stats=stats)
        for c, m in zip(cands.candidates, ms, strict=True):
            table.add(c, m)
    secs["evaluate"] = time.perf_counter() - t

    t = time.perf_counter()
    recs = list(study.decide(table, policy))
    missing = missing_recommendations(study, recs)
    if missing:
        warns.append(f"decide() returned no Recommendation for {', '.join(missing)}")
    secs["decide"] = time.perf_counter() - t

    try:
        rec_set = recommended_set(base, recs)
    except (KeyError, TypeError, ValueError) as e:
        warns.append(f"recommended values do not form a valid set ({e}); neighbours taken around base")
        rec_set = base
    if rec_set.check(ctx):
        warns.append("recommended set violates invariants; neighbours taken around base")
        rec_set = base

    t = time.perf_counter()
    neigh: dict[str, list[NeighbourPoint]] = {}
    ntable = None
    if neighbours > 0:
        rec_m = evaluate_many(study.evaluate, [rec_set], envb, workers=1, cache=cache, crn=crn,
                              stats=stats)[0]
        neigh, ntable = _neighbour_points(study, table, rec_set, rec_m, envb, names, k=neighbours,
                                          context=ctx, couple=couple, feasible=feasible, workers=workers,
                                          cache=cache, crn=crn, stats=stats)
    secs["neighbours"] = time.perf_counter() - t
    secs["total"] = time.perf_counter() - t_all

    run = GroupRun(study.group, cands.method, table, recs, cands, rec_set, neigh, ntable, halv, stats, secs,
                   warns)
    if neighbours > 0:
        _attach(recs, neigh, rec_set, rec_m, float(policy.materiality), run.summary())
    else:
        for r in recs:
            r.notes.append(f"Search: {run.summary()}.")
    if explain:
        for r in recs:
            if not r.explanation:
                r.explanation = study.explain(r, table)
    return run


def optimize_group(
    study: Study,
    base: ParamSet,
    env: Env,
    budget: Budget | None = None,
    policy: Policy | None = None,
    *,
    method: Method = "space",
    workers: int | None = None,
    **kw: Any,
) -> tuple[ResultTable, list[Recommendation]]:
    """Search, evaluate and decide one study group.

    * ``method="space"`` — the study's own ``space(base, budget)``;
      ``"grid"`` — registry grid over the study's tunable params (``budget.grid_points`` per axis,
      capped); ``"lhs"`` — ``budget.lhs_samples`` Latin-hypercube samples (plus the base);
      ``"halving"`` — successive halving over ``env.budget.paths`` on the candidates from
      ``halving_source`` (default the study's space); the table holds the full-fidelity survivors.
    * Candidates failing ``ParamSet.check(Context.from_policy(policy))`` or a ``feasible`` predicate
      are rejected and counted (``GroupRun.candidates``); the base set is always evaluated.
    * ``workers=None`` → ``os.cpu_count()``; results do not depend on it (``study`` and ``env`` must be
      picklable for ``workers > 1``; otherwise evaluation falls back to serial with a warning).
    * Every Recommendation gets a "Search: …" note (method, counts, timing) and, for tunable params,
      ``sensitivity["neighbours"]`` (±``neighbours`` steps around the recommended set) and
      ``sensitivity["local_slope"]``.

    Other keyword arguments go to :func:`run_group` (``params``, ``cache``, ``context``, ``couple``,
    ``feasible``, ``neighbours``, ``halving_source``, ``eta``, ``crn``, ``explain``).
    """
    run = run_group(study, base, env, budget, policy, method=method, workers=workers, **kw)
    return run.table, run.recommendations

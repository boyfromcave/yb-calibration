"""Scenario-ensemble aggregation, minimax regret, CVaR and policy-constrained robust selection
(PLAN §2.4, §5.10 step 3).

Inputs are candidate × scenario matrices of one metric (a :class:`ScenarioTable` holds several
metrics plus per-scenario feasibility). Every statistic takes a ``minimize`` flag: for a loss
(minimise) the bad tail is the *upper* tail, for a benefit (maximise) the lower tail.

Selection (:func:`robust_select`):

1. Hard constraints (from :class:`~ybcal.config.Policy` via :meth:`Constraint.from_policy`, plus the
   per-scenario ``Metrics.constraints`` flags) define the feasible set.
2. Among feasible candidates the rule (minimax regret, mean, CVaR, worst case, quantile) scores each.
   Regret is measured against the best *feasible* candidate in each scenario.
3. If nothing is feasible the least-violating candidate is returned with ``blocked=True``.
4. Ties (within ``tol``) break toward the current set, then toward the smaller change (step
   distance), then input order — the minimal-change rule of PLAN §2.3.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

from ybcal.params.paramset import ParamSet
from ybcal.params.registry import REGISTRY
from ybcal.studies.base import ResultTable

OWNER_WP = "WP-6"

Rule = Literal["minimax_regret", "mean", "cvar", "worst", "quantile", "median"]
Agg = Literal["mean", "median", "quantile", "cvar", "worst", "best"]


# ---------------------------------------------------------------------------------------------------
# Statistics over one candidate's scenario outcomes


def _w(x: np.ndarray, weights: Sequence[float] | np.ndarray | None) -> np.ndarray:
    if weights is None:
        return np.full(x.shape[-1], 1.0 / x.shape[-1])
    w = np.asarray(weights, dtype=float)
    if w.shape != (x.shape[-1],) or (w < 0).any() or w.sum() <= 0:
        raise ValueError("weights must be non-negative, one per scenario, not all zero")
    return w / w.sum()


def weighted_mean(x: Sequence[float] | np.ndarray, weights: Sequence[float] | None = None) -> float:
    """(Weighted) mean."""
    a = np.asarray(x, dtype=float)
    return float(a @ _w(a, weights))


def quantile(x: Sequence[float] | np.ndarray, q: float, weights: Sequence[float] | None = None) -> float:
    """``q``-quantile: numpy's linear interpolation unweighted; inverted CDF when weighted."""
    a = np.asarray(x, dtype=float)
    if weights is None:
        return float(np.quantile(a, q))
    w = _w(a, weights)
    o = np.argsort(a, kind="stable")
    cdf = np.cumsum(w[o])
    return float(a[o][min(int(np.searchsorted(cdf, q - 1e-12)), len(a) - 1)])


def cvar(x: Sequence[float] | np.ndarray, alpha: float = 0.95, *, minimize: bool = True,
         weights: Sequence[float] | None = None) -> float:
    """Conditional value at risk: the (weighted) mean of the worst ``1 − alpha`` probability mass.

    For a loss (``minimize=True``) the worst outcomes are the largest; for a benefit, the smallest.
    The boundary atom is split fractionally (Rockafellar–Uryasev), so with ``n`` equally likely
    scenarios and ``(1 − alpha)·n`` integral, CVaR is the plain mean of the worst ``(1 − alpha)·n``.
    ``alpha = 0`` gives the mean; ``alpha → 1`` the worst case.
    """
    a = np.asarray(x, dtype=float)
    if not 0.0 <= alpha < 1.0:
        raise ValueError("alpha must be in [0, 1)")
    w = _w(a, weights)
    o = np.argsort(-a if minimize else a, kind="stable")   # worst first
    tail, acc, mass = 1.0 - alpha, 0.0, 0.0
    for i in o:
        take = min(w[i], tail - mass)
        if take <= 0:
            break
        acc += take * a[i]
        mass += take
    return float(acc / mass)


def worst_case(x: Sequence[float] | np.ndarray, *, minimize: bool = True) -> float:
    """Largest loss (``minimize``) or smallest benefit."""
    a = np.asarray(x, dtype=float)
    return float(a.max() if minimize else a.min())


def aggregate(values: np.ndarray, how: Agg = "mean", *, minimize: bool = True, alpha: float = 0.95,
              q: float = 0.9, weights: Sequence[float] | None = None) -> np.ndarray:
    """Aggregate a ``(n_candidates, n_scenarios)`` matrix row-wise → ``(n_candidates,)``.

    ``quantile`` uses ``q`` as given for a loss and ``1 − q`` for a benefit, so ``q=0.9`` is always
    "the bad 10 %" side.
    """
    v = np.atleast_2d(np.asarray(values, dtype=float))
    if how == "mean":
        return np.array([weighted_mean(r, weights) for r in v])
    if how == "median":
        return np.array([quantile(r, 0.5, weights) for r in v])
    if how == "quantile":
        qq = q if minimize else 1.0 - q
        return np.array([quantile(r, qq, weights) for r in v])
    if how == "cvar":
        return np.array([cvar(r, alpha, minimize=minimize, weights=weights) for r in v])
    if how == "worst":
        return np.array([worst_case(r, minimize=minimize) for r in v])
    if how == "best":
        return np.array([worst_case(r, minimize=not minimize) for r in v])
    raise ValueError(f"unknown aggregation {how!r}")


def regret_matrix(values: np.ndarray, *, minimize: bool = True,
                  among: np.ndarray | None = None) -> np.ndarray:
    """``regret[i, s] = |v[i, s] − best_s|`` where ``best_s`` is the best value in scenario ``s``
    among the rows selected by the boolean mask ``among`` (default: all rows). Non-negative for the
    selected rows."""
    v = np.atleast_2d(np.asarray(values, dtype=float))
    m = np.ones(v.shape[0], bool) if among is None else np.asarray(among, bool)
    if not m.any():
        raise ValueError("regret needs at least one reference candidate")
    if minimize:
        return v - v[m].min(axis=0)
    return v[m].max(axis=0) - v


def max_regret(values: np.ndarray, *, minimize: bool = True, among: np.ndarray | None = None) -> np.ndarray:
    """Worst-case regret per candidate."""
    return regret_matrix(values, minimize=minimize, among=among).max(axis=1)


# ---------------------------------------------------------------------------------------------------
# Tie-breaking


def step_distance(a: Mapping[str, Any], b: Mapping[str, Any]) -> float:
    """Distance between two parameter sets in registry search steps (``|Δ|/step`` per numeric
    field, 1 per other changed field; ``network`` ignored)."""
    d = 0.0
    for k, va in a.items():
        if k == "network" or k not in b or b[k] == va:
            continue
        vb = b[k]
        step = REGISTRY[k].step if k in REGISTRY else 0
        if (isinstance(va, int) and isinstance(vb, int) and not isinstance(va, bool)
                and not isinstance(vb, bool) and step > 0):
            d += abs(va - vb) / step
        else:
            d += 1.0
    return d


def _resolve_current(labels: Sequence[Any], current: int | Any | None) -> int | None:
    if current is None:
        return None
    if isinstance(current, (int, np.integer)) and not isinstance(current, bool):
        return int(current)
    for i, lab in enumerate(labels):
        if lab == current:
            return i
    if isinstance(current, ParamSet):
        for i, lab in enumerate(labels):
            if isinstance(lab, ParamSet) and step_distance(lab, current) == 0:
                return i
    return None


def _distances(labels: Sequence[Any], cur: int | None,
               distance: Sequence[float] | Callable[[Any], float] | None) -> np.ndarray:
    n = len(labels)
    if distance is None:
        if cur is not None and all(isinstance(x, ParamSet) for x in labels):
            return np.array([step_distance(x, labels[cur]) for x in labels])
        return np.zeros(n)
    if callable(distance):
        return np.array([float(distance(x)) for x in labels])
    d = np.asarray(distance, dtype=float)
    if d.shape != (n,):
        raise ValueError("distance must have one entry per candidate")
    return d


def tie_break(scores: np.ndarray, *, eligible: np.ndarray | None = None, current: int | None = None,
              distance: np.ndarray | None = None, tol: float = 1e-12) -> list[int]:
    """Indices ordered best-first by ``scores`` (lower is better). Scores within
    ``tol·max(1, |best|)`` of the best eligible score tie; ties go to ``current``, then the smaller
    ``distance``, then the lower index. Ineligible rows come last."""
    s = np.asarray(scores, dtype=float)
    n = len(s)
    el = np.ones(n, bool) if eligible is None else np.asarray(eligible, bool)
    dist = np.zeros(n) if distance is None else np.asarray(distance, float)

    def key(i: int) -> tuple[Any, ...]:
        return (not el[i], math.inf if math.isnan(s[i]) else s[i], i != current, dist[i], i)

    order = sorted(range(n), key=key)
    if not el.any():
        return order
    best = s[order[0]]
    thr = best + tol * max(1.0, abs(best))
    ties = [i for i in order if el[i] and s[i] <= thr]
    ties.sort(key=lambda i: (i != current, dist[i], i))
    rest = [i for i in order if i not in set(ties)]
    return [*ties, *rest]


# ---------------------------------------------------------------------------------------------------
# Scenario tables and constraints


@dataclass
class ScenarioTable:
    """Metric matrices over candidates × scenarios.

    ``values[metric]`` has shape ``(n_candidates, n_scenarios)``; ``feasible`` (optional, same
    shape) holds the per-scenario ``Metrics.feasible`` flags; ``weights`` the scenario weights.
    """

    labels: list[Any]
    scenarios: list[str]
    values: dict[str, np.ndarray]
    feasible: np.ndarray | None = None
    weights: np.ndarray | None = None
    minimize: dict[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        shape = (len(self.labels), len(self.scenarios))
        for k, v in list(self.values.items()):
            a = np.asarray(v, dtype=float)
            if a.shape != shape:
                raise ValueError(f"values[{k!r}] has shape {a.shape}, want {shape}")
            self.values[k] = a
        if self.feasible is not None:
            self.feasible = np.asarray(self.feasible, bool)
            if self.feasible.shape != shape:
                raise ValueError("feasible must have shape (n_candidates, n_scenarios)")

    @classmethod
    def from_tables(cls, tables: Mapping[str, ResultTable], metrics: Sequence[str] | None = None,
                    weights: Mapping[str, float] | None = None) -> ScenarioTable:
        """Align per-scenario :class:`ResultTable` rows by parameter-set digest. Only candidates
        evaluated in every scenario are kept (first table's order)."""
        names = list(tables)
        if not names:
            raise ValueError("no scenarios")
        index = [{r.params.digest(): r for r in tables[s]} for s in names]
        first = tables[names[0]]
        keep = [r for r in first if all(r.params.digest() in ix for ix in index)]
        if not keep:
            raise ValueError("no candidate was evaluated in every scenario")
        mets = list(metrics) if metrics is not None else list(keep[0].metrics.values)
        vals = {m: np.array([[float(ix[r.params.digest()].metrics.values.get(m, math.nan)) for ix in index]
                             for r in keep]) for m in mets}
        feas = np.array([[ix[r.params.digest()].metrics.feasible for ix in index] for r in keep])
        mini = {m: keep[0].metrics.minimize if m == keep[0].metrics.primary else True for m in mets}
        w = None if weights is None else np.array([float(weights[s]) for s in names])
        return cls([r.params for r in keep], names, vals, feas, w, mini)

    def column(self, metric: str) -> np.ndarray:
        """The ``(n, s)`` matrix of ``metric``."""
        return self.values[metric]

    def aggregate(self, metric: str, how: Agg = "mean", **kw: Any) -> np.ndarray:
        """Row-wise aggregate of one metric (``minimize`` defaults to the table's direction)."""
        kw.setdefault("minimize", self.minimize.get(metric, True))
        kw.setdefault("weights", self.weights)
        return aggregate(self.values[metric], how, **kw)


@dataclass(frozen=True)
class Constraint:
    """A hard constraint: ``agg(metric over scenarios) op bound``.

    Build policy constraints with :meth:`from_policy`, e.g.
    ``Constraint.from_policy(policy, "max_bad_debt_prob", "p_bad_debt_A", key="A", agg="cvar")``.
    """

    name: str
    metric: str
    op: Literal["<=", ">="]
    bound: float
    agg: Agg = "worst"
    alpha: float = 0.95
    q: float = 0.9

    @classmethod
    def from_policy(cls, policy: Any, policy_field: str, metric: str, *, op: Literal["<=", ">="] = "<=",
                    agg: Agg = "worst", key: str | None = None, name: str | None = None,
                    alpha: float = 0.95, q: float = 0.9) -> Constraint:
        """Bound read from ``policy.<policy_field>`` (``[key]`` for dict-valued fields)."""
        v = getattr(policy, policy_field)
        if key is not None:
            v = v[key]
        label = name or (f"{policy_field}[{key}]" if key else policy_field)
        return cls(label, metric, op, float(v), agg, alpha, q)

    def values(self, table: ScenarioTable) -> np.ndarray:
        """The aggregated metric per candidate (worst side for ``<=``: high is bad)."""
        return aggregate(table.values[self.metric], self.agg, minimize=self.op == "<=",
                         alpha=self.alpha, q=self.q, weights=table.weights)

    def violation(self, value: np.ndarray | float) -> np.ndarray:
        """Relative violation ``≥ 0`` (``|bound|`` normalised; absolute when the bound is 0)."""
        v = np.asarray(value, dtype=float)
        gap = v - self.bound if self.op == "<=" else self.bound - v
        return np.maximum(gap, 0.0) / (abs(self.bound) if self.bound else 1.0)


@dataclass(frozen=True)
class RobustChoice:
    """Outcome of :func:`robust_select`."""

    index: int
    label: Any
    rule: str
    score: float
    scores: np.ndarray
    feasible: np.ndarray
    blocked: bool
    violations: dict[str, float]
    ranking: list[int]
    reason: str


def robust_select(
    table: ScenarioTable,
    metric: str,
    *,
    minimize: bool | None = None,
    rule: Rule = "minimax_regret",
    alpha: float = 0.95,
    q: float = 0.9,
    constraints: Sequence[Constraint] = (),
    scenario_constraints: bool = True,
    current: int | Any | None = None,
    distance: Sequence[float] | Callable[[Any], float] | None = None,
    tol: float = 1e-12,
) -> RobustChoice:
    """Policy-constrained robust selection (see module docstring).

    ``scenario_constraints`` also requires ``table.feasible`` to hold in every scenario. ``current``
    is an index or a label (e.g. the current :class:`ParamSet`). ``distance`` defaults to the step
    distance from the current set when labels are ParamSets.
    """
    mini = table.minimize.get(metric, True) if minimize is None else minimize
    v = table.values[metric]
    n = v.shape[0]
    viol = np.zeros(n)
    per: dict[str, np.ndarray] = {}
    for c in constraints:
        per[c.name] = c.violation(c.values(table))
        viol += per[c.name]
    if scenario_constraints and table.feasible is not None:
        w = _w(v, table.weights)
        per["scenario_constraints"] = (~table.feasible).astype(float) @ w
        viol += per["scenario_constraints"]
    feas = viol <= 0
    cur = _resolve_current(table.labels, current)
    dist = _distances(table.labels, cur, distance)
    if not feas.any():
        order = tie_break(viol, current=cur, distance=dist, tol=tol)
        i = order[0]
        bad = {k: float(a[i]) for k, a in per.items() if a[i] > 0}
        return RobustChoice(i, table.labels[i], rule, math.nan, np.full(n, math.nan), feas, True, bad, order,
                            f"BLOCKED: no candidate satisfies every constraint; least violating "
                            f"(total relative violation {viol[i]:.3g}: {', '.join(bad)})")
    if rule == "minimax_regret":
        scores = max_regret(v, minimize=mini, among=feas)
    elif rule in ("mean", "cvar", "worst", "quantile", "median"):
        agg = aggregate(v, rule, minimize=mini, alpha=alpha, q=q, weights=table.weights)
        scores = agg if mini else -agg
    else:
        raise ValueError(f"unknown rule {rule!r}")
    order = tie_break(scores, eligible=feas, current=cur, distance=dist, tol=tol)
    i = order[0]
    shown = scores[i] if rule == "minimax_regret" or mini else -scores[i]
    why = f"{rule} = {shown:.6g} on {metric} over {len(table.scenarios)} scenarios"
    if cur is not None and i == cur:
        why += " (current set kept)"
    return RobustChoice(i, table.labels[i], rule, float(shown), scores, feas, False, {}, order, why)

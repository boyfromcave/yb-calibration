"""The Study contract and the shared result types (owner: WP-0; frozen — PLAN §3.1).

A study owns one parameter group (``registry.params_for_group(group)``). The driver calls::

    study = load_study("G3")
    table = ResultTable(base)
    for cand in study.space(base, env.budget):
        if cand.check(ctx):            # invariants first (PLAN §1.4)
            continue
        table.add(cand, study.evaluate(cand, env))
    recs = study.decide(table, env.policy)
    for r in recs:
        r.explanation = study.explain(r, table)

Each ``ybcal.studies.gN_*`` module exposes ``make_study() -> Study``.
"""

from __future__ import annotations

import csv
import importlib
import math
import zlib
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

import numpy as np

from ybcal.params.paramset import ParamSet
from ybcal.params.registry import REGISTRY
from ybcal.types import PricePath, Provenance

if TYPE_CHECKING:
    from ybcal.config import Policy

Verdict = Literal["KEEP", "CHANGE", "PROVISIONAL", "BLOCKED"]
Confidence = Literal["high", "medium", "low"]
BudgetName = Literal["quick", "standard", "deep"]

#: Study group → module (each exposes ``make_study()``).
STUDY_MODULES: dict[str, str] = {
    "G1": "ybcal.studies.g1_price_windows",
    "G2": "ybcal.studies.g2_volatility",
    "G3": "ybcal.studies.g3_collateral",
    "G4": "ybcal.studies.g4_grace_abandon",
    "G5": "ybcal.studies.g5_activation",
    "G6": "ybcal.studies.g6_miners_fees",
    "G7": "ybcal.studies.g7_supply_halts",
    "G8": "ybcal.studies.g8_attestation",
    "G9": "ybcal.studies.g9_amounts",
    "R": "ybcal.studies.release",
}
#: Joint-pass dependency order (PLAN §5.10).
GROUP_ORDER: tuple[str, ...] = ("G1", "G2", "G5", "G3", "G4", "G7", "G6", "G8", "G9", "R")


# ---------------------------------------------------------------------------------------------------
# Budget and environment


@dataclass(frozen=True)
class Budget:
    """Compute knobs for one run. ``quick`` must finish ``recommend`` in ≤ 10 min on 4 cores."""

    name: BudgetName
    paths: int                  #: Monte-Carlo paths per scenario
    block_horizon_days: int     #: block-mode horizon (≤ 120 days)
    hour_horizon_years: float   #: hour-mode horizon (≤ 6 years; class C term + grace needs ~5.1)
    grid_points: int            #: points per searched dimension in a grid
    lhs_samples: int            #: Latin-hypercube samples for joint searches
    halving_rounds: int         #: successive-halving rounds
    scenario_set: Literal["core", "all"]   #: which of scenarios/*.toml to run
    morris_trajectories: int    #: Morris screening trajectories
    sobol_samples: int          #: Saltelli base samples
    max_minutes: float          #: soft wall-clock target for `recommend`

    @classmethod
    def named(cls, name: str) -> Budget:
        """``quick`` / ``standard`` / ``deep``."""
        try:
            return BUDGETS[name]
        except KeyError:
            raise ValueError(f"unknown budget {name!r}; choose from {sorted(BUDGETS)}") from None


BUDGETS: dict[str, Budget] = {
    "quick": Budget("quick", paths=64, block_horizon_days=30, hour_horizon_years=5.2, grid_points=5,
                    lhs_samples=16, halving_rounds=2, scenario_set="core", morris_trajectories=4,
                    sobol_samples=64, max_minutes=10),
    "standard": Budget("standard", paths=400, block_horizon_days=90, hour_horizon_years=6.0, grid_points=9,
                       lhs_samples=64, halving_rounds=3, scenario_set="all", morris_trajectories=10,
                       sobol_samples=512, max_minutes=60),
    "deep": Budget("deep", paths=2000, block_horizon_days=120, hour_horizon_years=6.0, grid_points=17,
                   lhs_samples=256, halving_rounds=4, scenario_set="all", morris_trajectories=20,
                   sobol_samples=4096, max_minutes=720),
}


def stable_key(*parts: object) -> int:
    """A process-independent 32-bit key for seeding (``hash()`` is salted per process)."""
    return zlib.crc32("\x1f".join(map(str, parts)).encode())


@dataclass
class Env:
    """What ``Study.evaluate`` sees: data + scenarios + policy + budget + seeded RNG.

    ``data`` maps a name (``"price"``, ``"spreads"``, …) to a loaded object, usually a
    :class:`PricePath`; ``scenarios`` maps scenario name → the WP-2 scenario object. Use
    :meth:`rng_for` for per-candidate / per-scenario streams so results do not depend on order.
    """

    policy: Policy
    budget: Budget
    seed: int
    data: dict[str, Any] = field(default_factory=dict)
    scenarios: dict[str, Any] = field(default_factory=dict)
    provenance: Provenance = "synthetic"
    out_dir: str | None = None  #: evidence root; studies write to ``<out_dir>/<group>/``
    rng: np.random.Generator = field(init=False)

    def __post_init__(self) -> None:
        self.rng = np.random.default_rng(self.seed)

    def rng_for(self, *keys: object) -> np.random.Generator:
        """An independent generator determined by ``(seed, keys)`` only."""
        return np.random.default_rng(np.random.SeedSequence([self.seed, *(stable_key(k) for k in keys)]))

    def price(self, name: str = "price") -> PricePath:
        """A loaded price path, type-checked."""
        p = self.data[name]
        if not isinstance(p, PricePath):
            raise TypeError(f"env.data[{name!r}] is not a PricePath")
        return p


# ---------------------------------------------------------------------------------------------------
# Metrics and results


@dataclass(frozen=True)
class Metrics:
    """Scores of one candidate. ``constraints`` maps a policy constraint name → satisfied."""

    values: Mapping[str, float]
    primary: str
    minimize: bool = True
    constraints: Mapping[str, bool] = field(default_factory=dict)
    provenance: Provenance = "synthetic"
    meta: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.primary not in self.values:
            raise KeyError(f"primary metric {self.primary!r} not in values")

    def __getitem__(self, key: str) -> float:
        return self.values[key]

    @property
    def primary_value(self) -> float:
        """Value of the primary metric."""
        return float(self.values[self.primary])

    @property
    def feasible(self) -> bool:
        """All policy constraints satisfied."""
        return all(self.constraints.values())

    @property
    def violated(self) -> tuple[str, ...]:
        """Names of violated constraints."""
        return tuple(k for k, ok in self.constraints.items() if not ok)


@dataclass(frozen=True)
class ResultRow:
    """One evaluated candidate: its delta from the table's base, the full set, and its metrics."""

    delta: Mapping[str, Any]
    params: ParamSet
    metrics: Metrics


def _step_distance(delta_a: Mapping[str, Any], base: ParamSet, params: ParamSet) -> float:
    """Distance from ``base`` in search steps (count of changes for non-numeric values)."""
    d = 0.0
    for k in delta_a:
        a, b = params[k], base[k]
        step = REGISTRY[k].step
        if isinstance(a, int) and isinstance(b, int) and not isinstance(a, bool) and step > 0:
            d += abs(a - b) / step
        else:
            d += 1.0
    return d


class ResultTable:
    """Rows of ``(ParamSet delta, Metrics)`` relative to a ``base`` set (usually the current one)."""

    def __init__(self, base: ParamSet, rows: Iterable[ResultRow] = ()) -> None:
        self.base = base
        self.rows: list[ResultRow] = list(rows)

    def add(self, params: ParamSet, metrics: Metrics) -> ResultRow:
        """Append a row (the delta is computed against ``base``; ``network`` is ignored)."""
        delta = {k: v for k, v in params.delta(self.base).items() if k != "network"}
        row = ResultRow(delta, params, metrics)
        self.rows.append(row)
        return row

    def __len__(self) -> int:
        return len(self.rows)

    def __iter__(self) -> Iterator[ResultRow]:
        return iter(self.rows)

    def filter(self, pred: Callable[[ResultRow], bool]) -> ResultTable:
        """Rows for which ``pred`` holds."""
        return ResultTable(self.base, (r for r in self.rows if pred(r)))

    def feasible(self) -> ResultTable:
        """Rows that satisfy every policy constraint."""
        return self.filter(lambda r: r.metrics.feasible)

    def current(self, params: Iterable[str] | None = None) -> ResultRow | None:
        """The row equal to ``base`` (on ``params`` only, if given); ``None`` if not evaluated."""
        keys = None if params is None else set(params)
        for r in self.rows:
            if keys is None and not r.delta:
                return r
            if keys is not None and not (keys & set(r.delta)):
                return r
        return None

    def distance(self, row: ResultRow) -> float:
        """Distance of a row from ``base`` in search steps (tie-breaking toward current)."""
        return _step_distance(row.delta, self.base, row.params)

    def _key(self, metric: str | None, sign: float) -> Callable[[ResultRow], tuple[float, float]]:
        def key(r: ResultRow) -> tuple[float, float]:
            v = r.metrics.primary_value if metric is None else float(r.metrics.values[metric])
            return (sign * v, self.distance(r))
        return key

    def argmin(self, metric: str | None = None) -> ResultRow:
        """Row minimising ``metric`` (default: each row's primary); ties → closest to base."""
        if not self.rows:
            raise ValueError("empty ResultTable")
        return min(self.rows, key=self._key(metric, 1.0))

    def argmax(self, metric: str | None = None) -> ResultRow:
        """Row maximising ``metric``; ties → closest to base."""
        if not self.rows:
            raise ValueError("empty ResultTable")
        return min(self.rows, key=self._key(metric, -1.0))

    def best(self) -> ResultRow:
        """Best row by the primary metric and its direction (rows must share primary/direction)."""
        if not self.rows:
            raise ValueError("empty ResultTable")
        return self.argmin() if self.rows[0].metrics.minimize else self.argmax()

    def column(self, metric: str) -> np.ndarray:
        """One metric across rows (NaN where absent)."""
        return np.array([float(r.metrics.values.get(metric, math.nan)) for r in self.rows])

    def param_values(self, name: str) -> list[Any]:
        """One parameter across rows."""
        return [r.params[name] for r in self.rows]

    def to_csv(self, path: str | Path) -> Path:
        """Write the table: changed params (registry order), metrics, feasible, violated, provenance."""
        pcols = [k for k in REGISTRY if any(k in r.delta for r in self.rows)]
        mcols: list[str] = []
        for r in self.rows:
            mcols.extend(m for m in r.metrics.values if m not in mcols)
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow([*pcols, *mcols, "feasible", "violated", "provenance"])
            for r in self.rows:
                w.writerow([
                    *(r.params[k] for k in pcols),
                    *(r.metrics.values.get(m, "") for m in mcols),
                    int(r.metrics.feasible), ";".join(r.metrics.violated), r.metrics.provenance,
                ])
        return p


# ---------------------------------------------------------------------------------------------------
# Recommendation


@dataclass
class Recommendation:
    """The per-parameter output of a study (PLAN §3.1)."""

    param: str
    current: Any
    recommended: Any
    verdict: Verdict
    rule: str                      #: the decision rule, human-readable
    binding: str                   #: which constraint/metric decided it
    metrics: dict[str, Any] = field(default_factory=dict)       #: primary + secondary at current/recommended
    sensitivity: dict[str, Any] = field(default_factory=dict)   #: d(metric)/d(param), Morris µ*/σ, flat/steep
    confidence: Confidence = "low"
    provenance: str = "synthetic"
    evidence: list[Path] = field(default_factory=list)          #: figures / tables
    group: str = ""
    notes: list[str] = field(default_factory=list)
    explanation: str = ""          #: filled from Study.explain

    def __post_init__(self) -> None:
        if self.param not in REGISTRY:
            raise KeyError(f"Recommendation for unknown parameter {self.param!r}")
        if not self.group:
            self.group = REGISTRY[self.param].group

    @property
    def changed(self) -> bool:
        """Whether the recommended value differs from the current one."""
        return self.recommended != self.current

    @property
    def change_path(self) -> str:
        """``locked`` / ``patch-release`` / ``per-release`` / ``protocol-constant`` / ``meta``."""
        return REGISTRY[self.param].change_path

    @property
    def klass_note(self) -> str:
        """The report's locked-vs-patch-release sentence."""
        return {
            "locked": "Locked: changes only through a new parameter set keyed by start height "
                      "(K10, L8, W19).",
            "patch-release": "Excluded: never hashed or read by a consensus rule; may change in a "
                             "patch release.",
            "per-release": "Per-release: derived from the release tip and date (M14, L8), not optimised.",
            "protocol-constant": "Protocol constant: verified against source, never tuned.",
            "meta": "Identity field: not calibrated.",
        }[self.change_path]

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict (paths as strings)."""
        d = asdict(self)
        d["evidence"] = [str(p) for p in self.evidence]
        d["change_path"] = self.change_path
        return d


# ---------------------------------------------------------------------------------------------------
# Decision helpers


@dataclass(frozen=True)
class MaterialityDecision:
    """Outcome of :func:`decide_with_materiality`."""

    row: ResultRow                 #: the chosen row (``current`` unless the verdict is CHANGE)
    verdict: Literal["KEEP", "CHANGE", "BLOCKED"]
    improvement: float             #: relative improvement of the best feasible row over current
    reason: str


def relative_improvement(current: float, candidate: float, *, minimize: bool = True) -> float:
    """``(cur - cand) / |cur|`` (minimise) or ``(cand - cur) / |cur|`` (maximise); ±inf when cur = 0."""
    gain = (current - candidate) if minimize else (candidate - current)
    if current == 0:
        return math.inf if gain > 0 else (0.0 if gain == 0 else -math.inf)
    return gain / abs(current)


def decide_with_materiality(
    table: ResultTable,
    materiality: float | Policy,
    *,
    params: Iterable[str] | None = None,
    metric: str | None = None,
    minimize: bool | None = None,
) -> MaterialityDecision:
    """Keep the current value unless the primary metric improves by more than ``materiality``
    with no policy constraint violated; ties break toward current (PLAN §2.3, §5.10).

    * If no evaluated row is feasible → ``BLOCKED`` (the current row is returned).
    * If the current row violates a constraint and a feasible row exists → ``CHANGE`` to the best
      feasible row regardless of materiality (a violation is never "kept").
    * ``params`` restricts what "current" means to those fields (a joint table may vary others).
    """
    thr = materiality if isinstance(materiality, (int, float)) else float(materiality.materiality)
    cur = table.current(params)
    if cur is None:
        raise ValueError("the current value was not evaluated; every study must evaluate it")
    mini = cur.metrics.minimize if minimize is None else minimize
    feas = table.feasible()
    if not len(feas):
        return MaterialityDecision(cur, "BLOCKED", 0.0,
                                   "no candidate satisfies the policy "
                                   f"(current violates {cur.metrics.violated})")
    best = feas.argmin(metric) if mini else feas.argmax(metric)

    def val(r: ResultRow) -> float:
        return r.metrics.primary_value if metric is None else float(r.metrics.values[metric])

    if not cur.metrics.feasible:
        return MaterialityDecision(best, "CHANGE", relative_improvement(val(cur), val(best), minimize=mini),
                                   f"current violates {', '.join(cur.metrics.violated)}")
    imp = relative_improvement(val(cur), val(best), minimize=mini)
    if best is cur or imp <= thr:
        return MaterialityDecision(cur, "KEEP", max(imp, 0.0),
                                   f"best improvement {imp:.1%} <= materiality {thr:.0%}" if imp > 0
                                   else "current is (jointly) best")
    return MaterialityDecision(best, "CHANGE", imp, f"improves primary metric by {imp:.1%} > {thr:.0%}")


def final_verdict(verdict: Literal["KEEP", "CHANGE", "BLOCKED"], provenance: str) -> Verdict:
    """Apply PLAN §2.5: a recommendation resting only on synthetic data is PROVISIONAL."""
    if verdict == "BLOCKED":
        return "BLOCKED"
    return "PROVISIONAL" if provenance == "synthetic" else verdict


# ---------------------------------------------------------------------------------------------------
# The protocol


@runtime_checkable
class Study(Protocol):
    """One parameter group's study (PLAN §3.1)."""

    group: str
    params: tuple[str, ...]   #: the fields it owns (= registry.params_for_group(group))

    def space(self, base: ParamSet, budget: Budget) -> Iterable[ParamSet]:
        """Candidate sets to evaluate; must include ``base`` itself."""
        ...

    def evaluate(self, cand: ParamSet, env: Env) -> Metrics:
        """Score one candidate."""
        ...

    def decide(self, results: ResultTable, policy: Policy) -> list[Recommendation]:
        """One Recommendation per owned param."""
        ...

    def explain(self, rec: Recommendation, results: ResultTable) -> str:
        """Plain-English explanation for the report."""
        ...


def load_study(group: str) -> Study:
    """Import ``STUDY_MODULES[group]`` and call its ``make_study()``.

    Raises ``NotImplementedError`` while the owning WP has not landed it.
    """
    mod = importlib.import_module(STUDY_MODULES[group])
    make = getattr(mod, "make_study", None)
    if make is None:
        raise NotImplementedError(f"study {group} not implemented yet ({getattr(mod, 'OWNER_WP', '?')})")
    study = make()
    if not isinstance(study, Study):
        raise TypeError(f"{STUDY_MODULES[group]}.make_study() did not return a Study")
    return study


def missing_recommendations(study: Study, recs: Iterable[Recommendation]) -> list[str]:
    """Owned params without a Recommendation (each study must cover all of them)."""
    have = {r.param for r in recs}
    return [p for p in study.params if p not in have]


#: Alias named in the WP-0 brief ("a ``materiality`` helper").
materiality = decide_with_materiality

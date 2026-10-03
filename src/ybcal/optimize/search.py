"""Candidate generators over a subset of parameters: grid, Latin hypercube, neighbourhood,
successive halving (PLAN §5, §5.10).

A :class:`SearchSpace` is a base :class:`ParamSet` plus one :class:`Axis` per searched parameter.
Axis values come from the registry's ``bounds`` / ``step`` (``ParamSpec``) and are **anchored on the
base value** (``base + k·step`` inside the bounds), so the current value is always a grid point and
"±1 step" means the same thing in a grid, a neighbourhood and a sensitivity sweep.

Every generator returns a :class:`CandidateSet`: the admissible candidates (base first, deduplicated
by ``ParamSet.digest``) **and** the rejected proposals with their reasons — invariant failures
(``ParamSet.check``), user feasibility predicates, coupled values pushed out of bounds, or
construction errors. Rejections are counted and reported, never dropped silently.

Coupled parameters. Derived parameters are recomputed by ``ParamSet.replace``. Other couplings
(e.g. ``classMax[0] = classMin[1] − 1``) are expressed as :data:`Coupling` callables that map the
proposed changes to extra changes; :class:`Tie` is a picklable helper for the common
"target = f(source)" case.
"""

from __future__ import annotations

import itertools
import math
import warnings
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from ybcal.optimize.evaluate import EvalCache, EvalFn, EvalStats, evaluate_many, rank_key, with_paths
from ybcal.params.invariants import Context
from ybcal.params.paramset import ParamSet
from ybcal.params.registry import REGISTRY
from ybcal.studies.base import Env, Metrics, stable_key
from ybcal.types import ParamValue

OWNER_WP = "WP-6"

#: ``coupling(changes, base) -> extra changes`` (applied in order; later couplings see earlier output).
Coupling = Callable[[Mapping[str, ParamValue], ParamSet], Mapping[str, ParamValue]]
#: ``predicate(cand) -> True/None/"" (feasible) | False | "reason" (infeasible)``.
Feasibility = Callable[[ParamSet], bool | str | None]

#: Largest full grid generated before falling back (with a warning).
DEFAULT_GRID_CAP: int = 4096

RejectReason = Literal["invariant", "infeasible", "bounds", "error"]


# ---------------------------------------------------------------------------------------------------
# Axes and spaces


@dataclass(frozen=True)
class Axis:
    """The values one parameter may take in a search (ascending for numeric axes)."""

    name: str
    values: tuple[ParamValue, ...]
    base: ParamValue
    step: int = 0
    bounds: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        if not self.values:
            raise ValueError(f"axis {self.name!r} has no values")

    def __len__(self) -> int:
        return len(self.values)

    @property
    def numeric(self) -> bool:
        """Integer-valued axis with a step (neighbours are ``v ± j·step``)."""
        return self.step > 0 and all(isinstance(v, int) and not isinstance(v, bool) for v in self.values)

    def index_of(self, value: ParamValue) -> int:
        """Index of ``value`` (nearest value for an off-grid numeric value)."""
        try:
            return self.values.index(value)
        except ValueError:
            if not self.numeric or not isinstance(value, int):
                raise
            return int(np.argmin([abs(int(v) - value) for v in self.values]))

    def snap(self, value: int) -> int:
        """Nearest axis value (ties → the lower)."""
        return int(self.values[self.index_of(value)])

    def thin(self, points: int | None) -> Axis:
        """At most ``points`` evenly spaced values, always keeping the base value when present."""
        n = len(self.values)
        if points is None or points >= n:
            return self
        points = max(1, int(points))
        idx = sorted({round(x) for x in np.linspace(0, n - 1, points)})
        if self.base in self.values:
            b = self.values.index(self.base)
            if b not in idx:
                j = min(range(len(idx)), key=lambda t: (abs(idx[t] - b), idx[t]))
                idx[j] = b
                idx = sorted(set(idx))
        return Axis(self.name, tuple(self.values[i] for i in idx), self.base, self.step, self.bounds)

    def neighbours(self, center: ParamValue, k: int = 1) -> list[ParamValue]:
        """Values ``±1..k`` steps from ``center`` (inside bounds), nearest first, below before above."""
        out: list[ParamValue] = []
        if self.numeric and isinstance(center, int) and not isinstance(center, bool):
            lo, hi = self.bounds if self.bounds else (min(self.values), max(self.values))
            for j in range(1, k + 1):
                for v in (center - j * self.step, center + j * self.step):
                    if lo <= v <= hi:
                        out.append(v)
            return out
        i = self.index_of(center)
        for j in range(1, k + 1):
            for t in (i - j, i + j):
                if 0 <= t < len(self.values):
                    out.append(self.values[t])
        return out


def axis(name: str, base: ParamSet | ParamValue, *, bounds: tuple[int, int] | None = None,
         step: int | None = None, values: Sequence[ParamValue] | None = None) -> Axis:
    """An :class:`Axis` from the registry's ``bounds``/``step`` (overridable), anchored on the base.

    Numeric values are ``b + k·step`` inside ``bounds`` where ``b`` is the base value; if the base
    lies outside the bounds (a regtest-scale set) the grid is anchored on the lower bound instead.
    ``values`` overrides everything (enums, booleans, hand-picked levels).
    """
    spec = REGISTRY.get(name)
    b = base[name] if isinstance(base, ParamSet) else base
    if values is not None:
        vals = tuple(dict.fromkeys(values))
        return Axis(name, vals, b, step or 0, bounds)
    if spec is None and (bounds is None or step is None):
        raise KeyError(f"{name!r} is not a registry parameter; give bounds and step")
    lo, hi = bounds if bounds is not None else spec.bounds  # type: ignore[union-attr]
    st = int(step if step is not None else spec.step)  # type: ignore[union-attr]
    if st <= 0:
        raise ValueError(f"{name} has step 0 (derived/constant/not searched); give step= or values=")
    if lo > hi:
        raise ValueError(f"{name}: empty bounds {lo}..{hi}")
    anchor = b if isinstance(b, int) and not isinstance(b, bool) and lo <= b <= hi else lo
    first = anchor - ((anchor - lo) // st) * st
    vals = tuple(range(first, hi + 1, st))
    return Axis(name, vals, b, st, (lo, hi))


@dataclass(frozen=True)
class Tie:
    """Picklable coupling ``target = scale·source // div + offset`` (integer arithmetic)."""

    target: str
    source: str
    scale: int = 1
    div: int = 1
    offset: int = 0

    def __call__(self, changes: Mapping[str, ParamValue], base: ParamSet) -> dict[str, ParamValue]:
        src = changes.get(self.source, base[self.source])
        if isinstance(src, bool) or not isinstance(src, int):
            raise TypeError(f"Tie source {self.source} is not an integer")
        return {self.target: self.scale * src // self.div + self.offset}


@dataclass(frozen=True)
class Rejected:
    """A proposal that did not become a candidate."""

    changes: Mapping[str, ParamValue]
    reason: RejectReason
    detail: tuple[str, ...]


@dataclass
class CandidateSet:
    """Admissible candidates (base first, unique) plus the counted rejections."""

    method: str
    candidates: list[ParamSet] = field(default_factory=list)
    rejected: list[Rejected] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    proposed: int = 0
    duplicates: int = 0

    def __iter__(self) -> Iterator[ParamSet]:
        return iter(self.candidates)

    def __len__(self) -> int:
        return len(self.candidates)

    @property
    def n_invalid(self) -> int:
        """Number of rejected proposals."""
        return len(self.rejected)

    def invalid_counts(self) -> dict[str, int]:
        """Rejections by cause: the invariant name for invariant failures, else the reason."""
        c: Counter[str] = Counter()
        for r in self.rejected:
            if r.reason == "invariant":
                c.update(r.detail)
            else:
                c[r.reason] += 1
        return dict(sorted(c.items()))

    def summary(self) -> str:
        """One line: ``grid: 23 candidates, 2 rejected (window_order×2), 1 duplicate``."""
        bits = [f"{self.method}: {len(self.candidates)} candidates"]
        if self.rejected:
            why = ", ".join(f"{k}×{v}" for k, v in self.invalid_counts().items())
            bits.append(f"{self.n_invalid} rejected ({why})")
        if self.duplicates:
            bits.append(f"{self.duplicates} duplicate{'s' if self.duplicates != 1 else ''}")
        return ", ".join(bits)

    def merge(self, other: CandidateSet, method: str | None = None) -> CandidateSet:
        """Union (order kept, duplicates removed and counted)."""
        out = CandidateSet(method or f"{self.method}+{other.method}", list(self.candidates),
                           [*self.rejected, *other.rejected], [*self.warnings, *other.warnings],
                           self.proposed + other.proposed, self.duplicates + other.duplicates)
        seen = {c.digest() for c in out.candidates}
        for c in other.candidates:
            d = c.digest()
            if d in seen:
                out.duplicates += 1
            else:
                seen.add(d)
                out.candidates.append(c)
        return out


@dataclass
class SearchSpace:
    """What to search: a base set, the axes, couplings, feasibility predicates and the invariant
    context. Build one with :meth:`for_params`."""

    base: ParamSet
    axes: tuple[Axis, ...]
    couple: tuple[Coupling, ...] = ()
    feasible: tuple[Feasibility, ...] = ()
    context: Context | None = None
    network: str | None = None       #: force this ``network`` on candidates (e.g. ``"candidate"``)
    include_base: bool = True        #: always put ``base`` first (the materiality rule needs it)

    @classmethod
    def for_params(
        cls,
        base: ParamSet,
        params: Iterable[str],
        *,
        bounds: Mapping[str, tuple[int, int]] | None = None,
        steps: Mapping[str, int] | None = None,
        values: Mapping[str, Sequence[ParamValue]] | None = None,
        couple: Sequence[Coupling] = (),
        feasible: Sequence[Feasibility] = (),
        context: Context | None = None,
        network: str | None = None,
        tunable_only: bool = True,
    ) -> SearchSpace:
        """Axes for ``params`` from the registry. With ``tunable_only`` (default), parameters that
        are not tunable (derived, constants, per-release, meta, step 0) are skipped unless an
        explicit ``values``/``steps`` override is given."""
        bounds, steps, values = dict(bounds or {}), dict(steps or {}), dict(values or {})
        axes = []
        for p in params:
            spec = REGISTRY.get(p)
            explicit = p in values or p in steps
            if tunable_only and spec is not None and not spec.tunable and not explicit:
                continue
            axes.append(axis(p, base, bounds=bounds.get(p), step=steps.get(p), values=values.get(p)))
        return cls(base, tuple(axes), tuple(couple), tuple(feasible), context, network)

    @property
    def names(self) -> tuple[str, ...]:
        """Searched parameter names."""
        return tuple(a.name for a in self.axes)

    def axis(self, name: str) -> Axis:
        """The axis for ``name``."""
        for a in self.axes:
            if a.name == name:
                return a
        raise KeyError(name)

    @property
    def size(self) -> int:
        """Full-grid size."""
        return math.prod(len(a) for a in self.axes)

    # -- building candidates ----------------------------------------------------------------------
    def build(self, changes: Mapping[str, ParamValue], anchor: ParamSet | None = None) -> ParamSet | Rejected:
        """Apply couplings, ``replace``, bounds, invariants and predicates to one proposal."""
        src = self.base if anchor is None else anchor
        ch: dict[str, ParamValue] = dict(changes)
        try:
            for c in self.couple:
                ch.update(c(ch, src))
            if self.network is not None:
                ch.setdefault("network", self.network)
            cand = src.replace(ch)
        except Exception as e:  # construction errors are reported, not raised
            return Rejected(dict(changes), "error", (f"{type(e).__name__}: {e}",))
        if not cand.is_regtest_scale:
            oob = []
            for k, v in ch.items():
                spec = REGISTRY.get(k)
                if (spec is None or spec.step <= 0 or isinstance(v, bool) or not isinstance(v, int)
                        or v == self.base.get(k)):
                    continue
                lo, hi = spec.bounds
                if not lo <= v <= hi:
                    oob.append(f"{k}={v} outside {lo}..{hi}")
            if oob:
                return Rejected(dict(ch), "bounds", tuple(oob))
        viol = cand.check(self.context)
        if viol:
            return Rejected(dict(ch), "invariant", tuple(v.invariant for v in viol))
        for pred in self.feasible:
            r = pred(cand)
            if r is False or (isinstance(r, str) and r):
                why = r if isinstance(r, str) else getattr(pred, "__name__", type(pred).__name__)
                return Rejected(dict(ch), "infeasible", (why,))
        return cand

    def materialize(self, proposals: Iterable[Mapping[str, ParamValue]], method: str,
                    *, anchor: ParamSet | None = None, include_base: bool | None = None) -> CandidateSet:
        """Build every proposal into a :class:`CandidateSet` (base first when ``include_base``)."""
        out = CandidateSet(method)
        seen: set[str] = set()
        inc = self.include_base if include_base is None else include_base
        if inc:
            b = self.base if self.network is None else self.base.replace({"network": self.network})
            viol = b.check(self.context)
            if viol:
                out.warnings.append("base set violates invariants: "
                                    + ", ".join(sorted({v.invariant for v in viol}))
                                    + " (kept: the current value must be evaluated)")
            out.candidates.append(b)
            seen.add(b.digest())
        for ch in proposals:
            out.proposed += 1
            r = self.build(ch, anchor)
            if isinstance(r, Rejected):
                out.rejected.append(r)
                continue
            d = r.digest()
            if d in seen:
                out.duplicates += 1
                continue
            seen.add(d)
            out.candidates.append(r)
        return out


def screen(sets: Iterable[ParamSet], base: ParamSet, *, method: str = "space",
           context: Context | None = None, feasible: Sequence[Feasibility] = (),
           include_base: bool = True) -> CandidateSet:
    """Screen ready-made sets (e.g. from ``Study.space``) exactly as given: invariants and
    predicates, deduplication, base first. If ``include_base`` and no set equals ``base`` (ignoring
    ``network``), ``base`` is prepended with a warning (the materiality rule needs it)."""
    out = CandidateSet(method)
    seen: set[str] = set()
    items = list(sets)
    has_base = any(not {k for k in s.delta(base) if k != "network"} for s in items)
    if include_base and not has_base:
        out.warnings.append("space() did not include the base set; added it")
        items.insert(0, base)
    elif include_base:
        i = next(i for i, s in enumerate(items) if not {k for k in s.delta(base) if k != "network"})
        items.insert(0, items.pop(i))
    for k, s in enumerate(items):
        is_base = include_base and k == 0
        if not is_base:
            out.proposed += 1
        d = s.digest()
        if d in seen:
            out.duplicates += 1
            continue
        viol = s.check(context)
        if viol and not is_base:
            out.rejected.append(Rejected(s.delta(base), "invariant", tuple(v.invariant for v in viol)))
            continue
        if viol:
            out.warnings.append("base set violates invariants: "
                                + ", ".join(sorted({v.invariant for v in viol})) + " (kept)")
        bad = None
        for pred in feasible if not is_base else ():
            r = pred(s)
            if r is False or (isinstance(r, str) and r):
                bad = r if isinstance(r, str) else getattr(pred, "__name__", type(pred).__name__)
                break
        if bad is not None:
            out.rejected.append(Rejected(s.delta(base), "infeasible", (bad,)))
            continue
        seen.add(d)
        out.candidates.append(s)
    return out


# ---------------------------------------------------------------------------------------------------
# Generators


def grid(space: SearchSpace, *, points: int | Mapping[str, int] | None = None,
         cap: int = DEFAULT_GRID_CAP, seed: int | None = None) -> CandidateSet:
    """The full factorial grid over ``space.axes`` (each thinned to ``points`` values, keeping the
    base). If the grid exceeds ``cap``, the per-axis point count is lowered until it fits; if even 2
    points per axis do not fit, ``cap`` Latin-hypercube samples are drawn instead. Both fallbacks
    add a warning to the result (and emit a :class:`UserWarning`)."""
    def pts(a: Axis) -> int | None:
        return points.get(a.name) if isinstance(points, Mapping) else points

    axes = [a.thin(pts(a)) for a in space.axes]
    msg = None
    size = math.prod(len(a) for a in axes)
    if size > cap:
        p = max(len(a) for a in axes)
        while p > 2 and math.prod(min(len(a), p) for a in axes) > cap:
            p -= 1
        if math.prod(min(len(a), p) for a in axes) > cap:
            msg = f"grid of {size} points exceeds cap {cap}; using {cap} Latin-hypercube samples"
            warnings.warn(msg, UserWarning, stacklevel=2)
            out = latin_hypercube(space, cap, seed=seed)
            out.method = "grid→lhs"
            out.warnings.append(msg)
            return out
        axes = [a.thin(p) for a in axes]
        msg = f"grid of {size} points exceeds cap {cap}; thinned to ≤ {p} points per axis"
        warnings.warn(msg, UserWarning, stacklevel=2)
    names = [a.name for a in axes]
    proposals = ({n: v for n, v in zip(names, combo, strict=True) if v != space.base[n]}
                 for combo in itertools.product(*(a.values for a in axes)))
    out = space.materialize(proposals, "grid")
    if msg:
        out.warnings.append(msg)
    return out


def _rng(seed: int | np.random.Generator | None, space: SearchSpace, tag: str) -> np.random.Generator:
    if isinstance(seed, np.random.Generator):
        return seed
    s = stable_key(tag, *space.names) if seed is None else int(seed)
    return np.random.default_rng(s)


def lhs_indices(sizes: Sequence[int], n: int, rng: np.random.Generator) -> np.ndarray:
    """``(n, d)`` Latin-hypercube indices: column ``j`` stratifies ``[0, 1)`` into ``n`` strata and maps
    each point to one of ``sizes[j]`` levels (``floor(u · size)``)."""
    d = len(sizes)
    u = (np.stack([rng.permutation(n) for _ in range(d)], axis=1) + rng.random((n, d))) / n
    return np.minimum((u * np.asarray(sizes)).astype(int), np.asarray(sizes) - 1)


def latin_hypercube(space: SearchSpace, n: int, *,
                    seed: int | np.random.Generator | None = None) -> CandidateSet:
    """``n`` Latin-hypercube samples over the axes, snapped to axis values (``base + k·step``) and
    deduplicated (duplicates counted). The base is included first. Deterministic for a given seed;
    the default seed is a stable hash of the axis names."""
    if n <= 0 or not space.axes:
        return space.materialize([], "lhs")
    idx = lhs_indices([len(a) for a in space.axes], int(n), _rng(seed, space, "lhs"))
    proposals = ({a.name: a.values[i] for a, i in zip(space.axes, row, strict=True)
                  if a.values[i] != space.base[a.name]} for row in idx)
    return space.materialize(proposals, "lhs")


def neighbourhood(space: SearchSpace, around: ParamSet | None = None, *, k: int = 1,
                  params: Iterable[str] | None = None, mode: Literal["axis", "full"] = "axis",
                  cap: int = DEFAULT_GRID_CAP) -> CandidateSet:
    """Candidates ``±1..k`` steps around ``around`` (default: the base) — the "why not the
    neighbours" evidence. ``mode="axis"`` moves one parameter at a time (``2k`` per axis);
    ``mode="full"`` takes the product of the per-axis offsets (capped like :func:`grid`). ``around``
    is included first; neighbours inherit every other value from it."""
    center = space.base if around is None else around
    names = set(space.names if params is None else params)
    axes = [a for a in space.axes if a.name in names]
    if mode == "axis":
        proposals: Iterable[dict[str, ParamValue]] = (
            {a.name: v} for a in axes for v in a.neighbours(center[a.name], k))
    else:
        levels = [[center[a.name], *a.neighbours(center[a.name], k)] for a in axes]
        if math.prod(len(lv) for lv in levels) > cap:
            raise ValueError(f"full neighbourhood exceeds cap {cap}; use mode='axis' or a smaller k")
        proposals = ({a.name: v for a, v in zip(axes, combo, strict=True) if v != center[a.name]}
                     for combo in itertools.product(*levels))
    sub = SearchSpace(center, space.axes, space.couple, space.feasible, space.context, space.network,
                      include_base=True)
    out = sub.materialize(proposals, f"neighbourhood(k={k},{mode})", anchor=center)
    return out


# ---------------------------------------------------------------------------------------------------
# Successive halving


@dataclass(frozen=True)
class HalvingRound:
    """One rung of successive halving."""

    paths: int
    evaluated: int
    kept: int
    best_digest: str
    best_value: float


@dataclass
class HalvingResult:
    """Survivors scored at full fidelity (best first) plus the history."""

    candidates: list[ParamSet]
    metrics: list[Metrics]
    rounds: list[HalvingRound]
    low_fidelity: list[tuple[int, ParamSet, Metrics]] = field(default_factory=list)

    @property
    def best(self) -> tuple[ParamSet, Metrics]:
        """The best survivor."""
        return self.candidates[0], self.metrics[0]


def halving_schedule(full_paths: int, rounds: int, eta: int = 3, min_paths: int = 8) -> list[int]:
    """Paths per rung: ``max(min_paths, ceil(full / eta^(R-1-r)))``, the last rung = ``full``."""
    rounds = max(1, int(rounds))
    sched = [max(min(min_paths, full_paths), math.ceil(full_paths / eta ** (rounds - 1 - r)))
             for r in range(rounds)]
    sched[-1] = full_paths
    return sched


def successive_halving(
    fn: EvalFn,
    cands: Sequence[ParamSet] | CandidateSet,
    env: Env,
    *,
    rounds: int | None = None,
    eta: int = 3,
    min_paths: int = 8,
    keep: Sequence[ParamSet] = (),
    metric: str | None = None,
    minimize: bool | None = None,
    workers: int | None = 1,
    cache: EvalCache | None = None,
    crn: bool = True,
    stats: EvalStats | None = None,
) -> HalvingResult:
    """Successive halving over Monte-Carlo fidelity (Hyperband-lite, D-WP6-3).

    Rung ``r`` scores the surviving candidates with ``env.budget.paths`` reduced to
    :func:`halving_schedule` paths and keeps the best ``ceil(n / eta)`` (feasible first, then the
    metric in its direction; ties → input order). The last rung runs at the full budget. Sets in
    ``keep`` (typically the current set) always survive, so the materiality rule can compare at
    full fidelity. ``rounds`` defaults to ``env.budget.halving_rounds``.
    """
    pool = list(cands.candidates if isinstance(cands, CandidateSet) else cands)
    if not pool:
        return HalvingResult([], [], [])
    keep_d = {k.digest() for k in keep}
    sched = halving_schedule(env.budget.paths, rounds or env.budget.halving_rounds, eta, min_paths)
    hist: list[HalvingRound] = []
    low: list[tuple[int, ParamSet, Metrics]] = []
    ms: list[Metrics] = []
    for r, paths in enumerate(sched):
        ms = evaluate_many(fn, pool, with_paths(env, paths), workers=workers, cache=cache, crn=crn,
                           stats=stats)
        order = sorted(range(len(pool)), key=lambda i: (rank_key(ms[i], metric, minimize), i))
        last = r == len(sched) - 1
        if not last:
            low.extend((paths, pool[i], ms[i]) for i in order)
            n_keep = max(1, math.ceil(len(pool) / eta))
            chosen = order[:n_keep]
            chosen += [i for i in order[n_keep:] if pool[i].digest() in keep_d]
            hist.append(HalvingRound(paths, len(pool), len(chosen), pool[order[0]].digest(),
                                     _val(ms[order[0]], metric)))
            pool = [pool[i] for i in sorted(chosen, key=order.index)]
        else:
            hist.append(HalvingRound(paths, len(pool), len(pool), pool[order[0]].digest(),
                                     _val(ms[order[0]], metric)))
            pool, ms = [pool[i] for i in order], [ms[i] for i in order]
    return HalvingResult(pool, ms, hist, low)


def _val(m: Metrics, metric: str | None) -> float:
    return m.primary_value if metric is None else float(m.values[metric])

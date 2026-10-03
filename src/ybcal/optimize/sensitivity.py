"""Sensitivity: one-at-a-time sweeps, Morris elementary effects, Sobol indices (PLAN §5.10).

* **OAT** (:func:`oat`, :func:`oat_from_table`) sweeps one parameter with the others held, and
  classifies each segment by its arc elasticity as *flat* / *moderate* / *steep*;
  :func:`sensitivity_sentence` turns that into the report's plain-English sentence.
* **Morris** (:func:`morris`) — ``r`` random one-factor-at-a-time trajectories on (stepped,
  integer) level grids; reports ``μ``, ``μ*`` (mean |EE|) and ``σ`` per factor, with elementary
  effects in units of the factor's range so factors are comparable.
* **Sobol** (:func:`sobol`) — Saltelli's ``A``, ``B``, ``A_B^(i)`` design on a scrambled Sobol'
  sequence, Jansen estimators for first-order ``S1`` and total-order ``ST`` indices, percentile
  bootstrap confidence intervals. Own implementation (no SALib); validated on the Ishigami function.

Model functions take an ``(n, k)`` array of factor values and return ``(n,)`` outputs.
:class:`ParamSetObjective` adapts a ``Study.evaluate``-style callable (through the optimizer's
cache and process pool) so the same routines run on parameter sets; sets that fail an invariant
score ``NaN`` and are excluded from the estimators (counted in the result).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
from scipy.stats import qmc

from ybcal.optimize.evaluate import EvalCache, EvalFn, evaluate_many
from ybcal.optimize.search import axis as make_axis
from ybcal.params.invariants import Context
from ybcal.params.paramset import ParamSet
from ybcal.params.registry import REGISTRY, derived_names
from ybcal.studies.base import Env, Metrics, ResultTable
from ybcal.units import BLOCKS_PER_DAY, BLOCKS_PER_HOUR

OWNER_WP = "WP-6"

Slope = Literal["flat", "moderate", "steep"]
ModelFn = Callable[[np.ndarray], np.ndarray]

#: |arc elasticity| below this → flat; at or above ``STEEP`` → steep.
FLAT_ELASTICITY: float = 0.1
STEEP_ELASTICITY: float = 1.0


# ---------------------------------------------------------------------------------------------------
# Factors


@dataclass(frozen=True)
class Factor:
    """An input of a sensitivity analysis: continuous on ``[lo, hi]`` or discrete on ``levels``."""

    name: str
    lo: float
    hi: float
    levels: tuple[float, ...] | None = None

    def __post_init__(self) -> None:
        if self.levels is not None and len(self.levels) < 1:
            raise ValueError(f"factor {self.name} has no levels")
        if self.hi < self.lo:
            raise ValueError(f"factor {self.name}: hi < lo")

    @classmethod
    def discrete(cls, name: str, levels: Sequence[float]) -> Factor:
        """A factor on explicit (sorted, unique) levels."""
        lv = tuple(sorted(dict.fromkeys(levels)))
        return cls(name, float(lv[0]), float(lv[-1]), lv)

    @property
    def span(self) -> float:
        """``hi − lo`` (1 for a single-level factor, to avoid division by zero)."""
        return (self.hi - self.lo) or 1.0

    def grid(self, p: int = 4) -> tuple[float, ...]:
        """Morris levels: the discrete levels, or ``p`` equally spaced points on ``[lo, hi]``."""
        if self.levels is not None:
            return self.levels
        return tuple(np.linspace(self.lo, self.hi, p))

    def from_unit(self, u: np.ndarray) -> np.ndarray:
        """Map ``u ∈ [0, 1)`` to values (uniform on ``[lo, hi]`` or on the level indices)."""
        u = np.asarray(u, dtype=float)
        if self.levels is None:
            return self.lo + u * (self.hi - self.lo)
        lv = np.asarray(self.levels)
        return lv[np.minimum((u * len(lv)).astype(int), len(lv) - 1)]


def factors_for_params(base: ParamSet, names: Iterable[str], *, k_steps: int | None = 1,
                       bounds: dict[str, tuple[int, int]] | None = None) -> list[Factor]:
    """Discrete factors on the registry grid (``base + j·step``): ``±k_steps`` around the base value
    (the joint pass uses ±1 step, PLAN §5.10), or the full bounds when ``k_steps`` is ``None``."""
    out = []
    for n in names:
        ax = make_axis(n, base, bounds=(bounds or {}).get(n))
        lv = list(ax.values) if k_steps is None else sorted([base[n], *ax.neighbours(base[n], k_steps)])
        out.append(Factor.discrete(n, lv))  # type: ignore[arg-type]
    return out


class ParamSetObjective:
    """``f(X) -> y``: row ``x`` of ``X`` becomes ``base.replace({name_j: int(x_j)})``, scored with
    ``fn`` through :func:`evaluate_many` (cache, process pool, per-candidate seeding). Rows that
    fail an invariant (or a user predicate) score ``NaN``; ``self.invalid`` counts them.

    Picklable when ``fn`` and ``env`` are (the instance itself is never sent to workers).
    """

    def __init__(self, fn: EvalFn, env: Env, base: ParamSet, names: Sequence[str], *,
                 metric: str | None = None, workers: int | None = 1, cache: EvalCache | None = None,
                 context: Context | None = None,
                 feasible: Sequence[Callable[[ParamSet], bool]] = ()) -> None:
        self.fn, self.env, self.base, self.names = fn, env, base, tuple(names)
        self.metric, self.workers, self.context = metric, workers, context
        self.feasible = tuple(feasible)
        self.cache = cache if cache is not None else EvalCache()
        self.invalid = 0
        self.evaluated = 0

    def paramset(self, x: Sequence[float]) -> ParamSet:
        """The set for one row."""
        return self.base.replace({n: round(v) for n, v in zip(self.names, x, strict=True)})

    def __call__(self, X: np.ndarray) -> np.ndarray:
        X = np.atleast_2d(X)
        y = np.full(X.shape[0], math.nan)
        ok_idx, sets = [], []
        for i, row in enumerate(X):
            try:
                ps = self.paramset(row)
            except (TypeError, ValueError):
                self.invalid += 1
                continue
            if ps.check(self.context) or not all(p(ps) for p in self.feasible):
                self.invalid += 1
                continue
            ok_idx.append(i)
            sets.append(ps)
        ms = evaluate_many(self.fn, sets, self.env, workers=self.workers, cache=self.cache)
        self.evaluated += len(sets)
        for i, m in zip(ok_idx, ms, strict=True):
            y[i] = m.primary_value if self.metric is None else float(m.values[self.metric])
        return y


# ---------------------------------------------------------------------------------------------------
# One at a time


def _classify(e: float, flat: float, steep: float) -> Slope:
    a = abs(e)
    if not math.isfinite(a):
        return "steep"
    return "flat" if a < flat else "steep" if a >= steep else "moderate"


@dataclass
class OATResult:
    """A one-parameter sweep and its slope classification.

    ``elasticity[j]`` is the arc elasticity of segment ``j`` (between ``values[j]`` and
    ``values[j+1]``): ``(Δy / y_scale) / (Δx / x_mid)`` with ``y_scale = max(|y_mid|, floor)`` and
    ``floor = 5 %`` of ``max |y|`` over the sweep (so near-zero probabilities do not explode).
    """

    param: str
    values: np.ndarray
    metric: np.ndarray
    metric_name: str = "metric"
    components: dict[str, np.ndarray] = field(default_factory=dict)
    base_value: float | None = None
    flat: float = FLAT_ELASTICITY
    steep: float = STEEP_ELASTICITY
    invalid: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        x = np.asarray(self.values, dtype=float)
        y = np.asarray(self.metric, dtype=float)
        o = np.argsort(x, kind="stable")
        keep = o[np.isfinite(y[o])]
        self.values, self.metric = x[keep], y[keep]
        self.components = {k: np.asarray(v, dtype=float)[keep] for k, v in self.components.items()}

    @property
    def slopes(self) -> np.ndarray:
        """``Δy / Δx`` per segment."""
        return np.diff(self.metric) / np.diff(self.values) if len(self.values) > 1 else np.zeros(0)

    @property
    def elasticity(self) -> np.ndarray:
        """Arc elasticity per segment (see class docstring)."""
        x, y = self.values, self.metric
        if len(x) < 2:
            return np.zeros(0)
        floor = max(0.05 * float(np.max(np.abs(y))), 1e-300)
        ymid = np.maximum(np.abs((y[1:] + y[:-1]) / 2), floor)
        xmid = np.abs((x[1:] + x[:-1]) / 2)
        xmid = np.where(xmid > 0, xmid, np.abs(np.diff(x)))
        return (np.diff(y) / ymid) / (np.diff(x) / xmid)

    @property
    def classes(self) -> list[Slope]:
        """Per-segment classification."""
        return [_classify(e, self.flat, self.steep) for e in self.elasticity]

    @property
    def overall(self) -> Slope:
        """Classification of the whole sweep (endpoint-to-endpoint arc elasticity, or the steepest
        segment if steeper)."""
        if len(self.values) < 2:
            return "flat"
        order = {"flat": 0, "moderate": 1, "steep": 2}
        worst = max(self.classes, key=order.__getitem__)
        x, y = self.values, self.metric
        floor = max(0.05 * float(np.max(np.abs(y))), 1e-300)
        e = ((y[-1] - y[0]) / max(abs((y[-1] + y[0]) / 2), floor)) / (
            (x[-1] - x[0]) / max(abs((x[-1] + x[0]) / 2), 1e-300))
        whole = _classify(e, self.flat, self.steep)
        return worst if order[worst] > order[whole] and worst == "steep" else whole

    def local(self, at: float | None = None) -> dict[str, Any]:
        """Slope/elasticity/class of the segment(s) adjacent to ``at`` (default: the base value)."""
        x0 = self.base_value if at is None else at
        if x0 is None or len(self.values) < 2:
            return {"slope": 0.0, "elasticity": 0.0, "class": "flat"}
        j = int(np.clip(np.searchsorted(self.values, x0), 1, len(self.values) - 1))
        segs = [s for s in (j - 1, j) if 0 <= s < len(self.values) - 1]
        if self.values[j - 1] == x0 and j - 2 >= 0:
            segs = [j - 2, j - 1]
        e = self.elasticity[segs]
        k = int(np.argmax(np.abs(e)))
        return {"slope": float(self.slopes[segs][k]), "elasticity": float(e[k]),
                "class": _classify(float(e[k]), self.flat, self.steep)}

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready summary (for ``Recommendation.sensitivity``)."""
        return {"param": self.param, "metric": self.metric_name,
                "values": self.values.tolist(), "y": self.metric.tolist(),
                "classes": self.classes, "overall": self.overall, "local": self.local()}


def oat(fn: EvalFn, env: Env, base: ParamSet, param: str, values: Sequence[int] | None = None, *,
        metric: str | None = None, components: Sequence[str] = (), points: int | None = None,
        workers: int | None = 1, cache: EvalCache | None = None,
        context: Context | None = None) -> OATResult:
    """Sweep ``param`` over ``values`` (default: its registry axis thinned to ``points`` values,
    default ``env.budget.grid_points`` — always including the base value), others held at ``base``.
    Sets that fail an invariant are skipped and listed in ``invalid``."""
    if values is None:
        ax = make_axis(param, base).thin(points or env.budget.grid_points)
        values = [int(v) for v in ax.values]  # type: ignore[arg-type]
    sets, xs, bad = [], [], []
    for v in values:
        ps = base.replace({param: v})
        if ps.check(context):
            bad.append(float(v))
            continue
        sets.append(ps)
        xs.append(float(v))
    ms = evaluate_many(fn, sets, env, workers=workers, cache=cache)
    return _oat_from_metrics(param, xs, ms, metric, components, base, bad)


def _oat_from_metrics(param: str, xs: Sequence[float], ms: Sequence[Metrics], metric: str | None,
                      components: Sequence[str], base: ParamSet, bad: list[float]) -> OATResult:
    name = metric or (ms[0].primary if ms else "metric")
    y = [m.primary_value if metric is None else float(m.values[metric]) for m in ms]
    comps = {c: np.array([float(m.values.get(c, math.nan)) for m in ms]) for c in components}
    bv = base[param]
    return OATResult(param, np.asarray(xs, float), np.asarray(y, float), name, comps,
                     float(bv) if isinstance(bv, int) and not isinstance(bv, bool) else None, invalid=bad)


def oat_from_table(table: ResultTable, param: str, metric: str | None = None,
                   components: Sequence[str] = ()) -> OATResult:
    """The OAT slice of an evaluated table: rows that differ from ``table.base`` only in ``param``
    (and values derived from it)."""
    derived = set(derived_names())
    rows = [r for r in table if set(r.delta) - derived <= {param}]
    xs = [float(r.params[param]) for r in rows]
    return _oat_from_metrics(param, xs, [r.metrics for r in rows], metric, components, table.base, [])


def format_value(param: str, v: float, *, unit: bool = True) -> str:
    """Human value for ``param``: blocks as days/hours when round, bps as %, else the number."""
    spec = REGISTRY.get(param)
    u = spec.unit if spec is not None else ""
    if u in ("blocks", "height") and abs(v) >= BLOCKS_PER_HOUR:
        if abs(v) >= BLOCKS_PER_DAY:
            d = v / BLOCKS_PER_DAY
            s = f"{d:.0f}" if abs(d - round(d)) < 0.05 else f"{d:.1f}"
            return f"{s} days" if unit else s
        h = v / BLOCKS_PER_HOUR
        s = f"{h:.0f}" if abs(h - round(h)) < 0.05 else f"{h:.1f}"
        return f"{s} hours" if unit else s
    if u == "bps":
        return f"{v / 100:g} %" if unit else f"{v / 100:g}"
    s = f"{int(v)}" if float(v).is_integer() else f"{v:g}"
    return f"{s} {u}" if unit and u in ("blocks", "zat", "cents", "micro-usd") else s


def _runs(classes: Sequence[str], target: str) -> list[tuple[int, int]]:
    """Maximal runs ``[i, j)`` of segments with class ``target``."""
    out, i = [], 0
    while i < len(classes):
        if classes[i] == target:
            j = i
            while j < len(classes) and classes[j] == target:
                j += 1
            out.append((i, j))
            i = j
        else:
            i += 1
    return out


def sensitivity_sentence(param: str, oat_result: OATResult, metric_name: str | None = None) -> str:
    """One plain-English sentence describing an OAT sweep, e.g.
    ``"P(owner miss) is flat between 20 and 40 days; P(miss) dominates below 20 days."``

    The longest flat run is named first; the regions below and above it are described by their
    steepest class and direction, or — when the sweep carries ``components`` — by the component
    that changes most there ("X dominates below …").
    """
    o = oat_result
    name = metric_name or o.metric_name
    x, y, cls = o.values, o.metric, o.classes
    if len(x) < 2:
        return f"{name}: not enough valid points to judge sensitivity to {param}."
    rank = {"flat": 0, "moderate": 1, "steep": 2}

    def region(lo: int, hi: int, where: str, edge: float) -> str | None:
        """Describe segments [lo, hi) lying ``where`` ('below'/'above') ``edge``."""
        if hi <= lo:
            return None
        seg = cls[lo:hi]
        worst = max(seg, key=rank.__getitem__)
        if worst == "flat":
            return None
        if o.components:
            deltas = {c: abs(float(v[hi] - v[lo])) for c, v in o.components.items()
                      if np.isfinite(v[lo]) and np.isfinite(v[hi])}
            if deltas and max(deltas.values()) > 0:
                dom = max(deltas, key=deltas.__getitem__)
                return f"{dom} dominates {where} {format_value(param, edge)}"
        dy = y[hi] - y[lo]
        # direction as the parameter moves *away* from the flat region
        rises = dy < 0 if where == "below" else dy > 0
        adv = "steeply" if worst == "steep" else "moderately"
        return f"{name} {'rises' if rises else 'falls'} {adv} {where} {format_value(param, edge)}"

    flats = _runs(cls, "flat")
    if not flats:
        dy = y[-1] - y[0]
        trend = "rises" if dy > 0 else "falls" if dy < 0 else "changes"
        return (f"{name} is {o.overall} in {param} across {format_value(param, x[0], unit=False)}–"
                f"{format_value(param, x[-1])}: it {trend} as {param} increases.")
    i, j = max(flats, key=lambda r: (r[1] - r[0], -r[0]))
    if i == 0 and j == len(cls):
        return (f"{name} is flat in {param} across {format_value(param, x[0], unit=False)}–"
                f"{format_value(param, x[-1])} (insensitive over the swept range).")
    parts = [f"{name} is flat between {format_value(param, x[i], unit=False)} and "
             f"{format_value(param, x[j])}"]
    for txt in (region(0, i, "below", x[i]), region(j, len(cls), "above", x[j])):
        if txt:
            parts.append(txt)
    return "; ".join(parts) + "."


# ---------------------------------------------------------------------------------------------------
# Morris


@dataclass
class MorrisResult:
    """Morris screening: ``mu``, ``mu_star`` = mean |EE|, ``sigma`` = std of EE (ddof 1), per factor.
    Elementary effects are per unit of the factor's range (``Δx / (hi − lo)``)."""

    names: list[str]
    mu: np.ndarray
    mu_star: np.ndarray
    sigma: np.ndarray
    effects: np.ndarray            #: ``(r, k)`` elementary effects (NaN where a point was invalid)
    n_evals: int
    n_invalid: int = 0

    def ranking(self) -> list[str]:
        """Factor names by decreasing ``μ*``."""
        return [self.names[i] for i in np.argsort(-self.mu_star, kind="stable")]

    def to_dict(self) -> dict[str, dict[str, float]]:
        """``{name: {"mu", "mu_star", "sigma"}}``."""
        return {n: {"mu": float(self.mu[i]), "mu_star": float(self.mu_star[i]),
                    "sigma": float(self.sigma[i])} for i, n in enumerate(self.names)}


def morris_design(factors: Sequence[Factor], r: int, *, levels: int = 4,
                  seed: int | np.random.Generator = 0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``r`` trajectories of ``k + 1`` points each on the factors' level grids.

    Each factor with ``n`` levels jumps ``max(1, n // 2)`` levels (Morris's ``Δ = p / (2(p − 1))``
    for even ``p``), up or down at random but always inside the grid, in a random factor order.
    Returns ``X`` of shape ``(r·(k+1), k)``, ``order`` ``(r, k)`` (which factor moves at step ``s``),
    and ``dx`` ``(r, k)`` (the normalised move of that factor).
    """
    rng = seed if isinstance(seed, np.random.Generator) else np.random.default_rng(seed)
    k = len(factors)
    grids = [np.asarray(f.grid(levels), dtype=float) for f in factors]
    sizes = np.array([len(g) for g in grids])
    jumps = np.maximum(1, sizes // 2)
    X = np.empty((r * (k + 1), k))
    order = np.empty((r, k), dtype=int)
    dx = np.zeros((r, k))
    for t in range(r):
        idx = np.array([rng.integers(0, s) for s in sizes])
        X[t * (k + 1)] = [g[i] for g, i in zip(grids, idx, strict=True)]
        perm = rng.permutation(k)
        order[t] = perm
        for s, f in enumerate(perm):
            if sizes[f] < 2:
                pass
            elif idx[f] + jumps[f] <= sizes[f] - 1 and (idx[f] - jumps[f] < 0 or rng.random() < 0.5):
                idx[f] += jumps[f]
            else:
                idx[f] -= jumps[f]
            row = [g[i] for g, i in zip(grids, idx, strict=True)]
            X[t * (k + 1) + s + 1] = row
            dx[t, s] = (X[t * (k + 1) + s + 1, f] - X[t * (k + 1) + s, f]) / factors[f].span
    return X, order, dx


def morris(f: ModelFn, factors: Sequence[Factor], r: int = 10, *, levels: int = 4,
           seed: int | np.random.Generator = 0) -> MorrisResult:
    """Morris elementary-effects screening (``r·(k+1)`` model runs). Factors with a single level
    get ``μ* = σ = 0``. NaN outputs (invalid sets) drop the affected effects."""
    k = len(factors)
    X, order, dx = morris_design(factors, r, levels=levels, seed=seed)
    y = np.asarray(f(X), dtype=float).reshape(-1)
    ee = np.full((r, k), math.nan)
    for t in range(r):
        base = t * (k + 1)
        for s, fi in enumerate(order[t]):
            if dx[t, s] != 0:
                ee[t, fi] = (y[base + s + 1] - y[base + s]) / dx[t, s]
            elif len(factors[fi].grid(levels)) < 2:
                ee[t, fi] = 0.0
    with np.errstate(invalid="ignore"):
        cnt = np.sum(np.isfinite(ee), axis=0)
        mu = np.where(cnt > 0, np.nansum(ee, axis=0) / np.maximum(cnt, 1), math.nan)
        mu_star = np.where(cnt > 0, np.nansum(np.abs(ee), axis=0) / np.maximum(cnt, 1), math.nan)
        dev = np.where(np.isfinite(ee), ee - mu, 0.0)
        sigma = np.where(cnt > 1, np.sqrt((dev ** 2).sum(axis=0) / np.maximum(cnt - 1, 1)), 0.0)
    return MorrisResult([fa.name for fa in factors], mu, mu_star, sigma, ee, len(y),
                        int(np.sum(~np.isfinite(y))))


# ---------------------------------------------------------------------------------------------------
# Sobol (Saltelli design, Jansen estimators)


@dataclass
class SobolResult:
    """First- and total-order Sobol indices with bootstrap confidence intervals."""

    names: list[str]
    S1: np.ndarray
    ST: np.ndarray
    S1_conf: np.ndarray            #: ``(k, 2)`` percentile CI
    ST_conf: np.ndarray            #: ``(k, 2)``
    variance: float
    n_base: int
    n_evals: int
    n_invalid: int = 0
    conf_level: float = 0.95

    def insensitive(self, threshold: float) -> list[str]:
        """Factors with total-order index below ``threshold`` (``policy.insensitive_total_order``)."""
        return [n for n, st in zip(self.names, self.ST, strict=True) if st < threshold]

    def ranking(self) -> list[str]:
        """Factor names by decreasing ``ST``."""
        return [self.names[i] for i in np.argsort(-self.ST, kind="stable")]

    def to_dict(self) -> dict[str, dict[str, Any]]:
        """``{name: {"S1", "ST", "S1_conf", "ST_conf"}}``."""
        return {n: {"S1": float(self.S1[i]), "ST": float(self.ST[i]),
                    "S1_conf": self.S1_conf[i].tolist(), "ST_conf": self.ST_conf[i].tolist()}
                for i, n in enumerate(self.names)}


def saltelli_design(factors: Sequence[Factor], n: int, *, seed: int = 0,
                    scramble: bool = True) -> tuple[np.ndarray, int]:
    """Saltelli's design: ``[A; B; A_B^(1); …; A_B^(k)]`` of shape ``(n·(k+2), k)`` in factor
    values, where ``A_B^(i)`` is ``A`` with column ``i`` taken from ``B``. ``A`` and ``B`` are the two
    halves of a scrambled ``2k``-dimensional Sobol' sequence; ``n`` is rounded up to a power of two
    (the sequence's balance needs it). Returns the design and the actual ``n``."""
    k = len(factors)
    m = max(1, math.ceil(math.log2(max(n, 2))))
    U = qmc.Sobol(d=2 * k, scramble=scramble, seed=seed).random_base2(m)
    n2 = U.shape[0]
    A_u, B_u = U[:, :k], U[:, k:]
    blocks = [A_u, B_u]
    for i in range(k):
        ab = A_u.copy()
        ab[:, i] = B_u[:, i]
        blocks.append(ab)
    Uall = np.vstack(blocks)
    X = np.column_stack([fa.from_unit(Uall[:, j]) for j, fa in enumerate(factors)])
    return X, n2


def _jansen(fA: np.ndarray, fB: np.ndarray, fAB: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Jansen (1999) estimators along axis -1 (supports a leading bootstrap axis).
    ``fAB`` has shape ``(..., k, n)``."""
    V = np.var(np.concatenate([fA, fB], axis=-1), axis=-1, ddof=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        ST = 0.5 * np.mean((fA[..., None, :] - fAB) ** 2, axis=-1) / V[..., None]
        S1 = 1.0 - 0.5 * np.mean((fB[..., None, :] - fAB) ** 2, axis=-1) / V[..., None]
    return S1, ST, V


def sobol(f: ModelFn, factors: Sequence[Factor], n: int = 1024, *, seed: int = 0, n_boot: int = 200,
          conf: float = 0.95, scramble: bool = True) -> SobolResult:
    """Sobol first-order and total-order indices (``n·(k+2)`` model runs, ``n`` rounded up to a
    power of two). Rows with a NaN output in ``A``, ``B`` or any ``A_B^(i)`` are dropped (counted in
    ``n_invalid``). A zero-variance output returns all-zero indices."""
    k = len(factors)
    X, n2 = saltelli_design(factors, n, seed=seed, scramble=scramble)
    y = np.asarray(f(X), dtype=float).reshape(-1)
    Y = y.reshape(k + 2, n2)
    ok = np.all(np.isfinite(Y), axis=0)
    fA, fB, fAB = Y[0, ok], Y[1, ok], Y[2:, ok]
    names = [fa.name for fa in factors]
    nn = int(ok.sum())
    if nn < 2 or np.var(np.concatenate([fA, fB])) == 0:
        z = np.zeros(k)
        return SobolResult(names, z, z.copy(), np.zeros((k, 2)), np.zeros((k, 2)), 0.0, n2, len(y),
                           int(np.sum(~np.isfinite(y))), conf)
    S1, ST, V = _jansen(fA, fB, fAB)
    rng = np.random.default_rng([seed, 0x5EB0])
    idx = rng.integers(0, nn, size=(n_boot, nn))
    bS1, bST, _ = _jansen(fA[idx], fB[idx], np.moveaxis(fAB[:, idx], 0, 1))
    lo, hi = (1 - conf) / 2 * 100, (1 + conf) / 2 * 100
    S1c = np.column_stack([np.nanpercentile(bS1, lo, axis=0), np.nanpercentile(bS1, hi, axis=0)])
    STc = np.column_stack([np.nanpercentile(bST, lo, axis=0), np.nanpercentile(bST, hi, axis=0)])
    return SobolResult(names, S1, ST, S1c, STc, float(V), n2, len(y), int(np.sum(~np.isfinite(y))), conf)


# ---------------------------------------------------------------------------------------------------
# Analytic test functions


def ishigami(X: np.ndarray, a: float = 7.0, b: float = 0.1) -> np.ndarray:
    """Ishigami function on ``[−π, π]^3``: ``sin x1 + a sin² x2 + b x3⁴ sin x1``."""
    X = np.atleast_2d(X)
    return np.sin(X[:, 0]) + a * np.sin(X[:, 1]) ** 2 + b * X[:, 2] ** 4 * np.sin(X[:, 0])


def ishigami_indices(a: float = 7.0, b: float = 0.1) -> dict[str, np.ndarray]:
    """Analytic ``S1`` and ``ST`` of :func:`ishigami` (uniform inputs on ``[−π, π]``)."""
    pi = math.pi
    V1 = 0.5 * (1 + b * pi ** 4 / 5) ** 2
    V2 = a ** 2 / 8
    V13 = b ** 2 * pi ** 8 * (1 / 18 - 1 / 50)
    V = V1 + V2 + V13
    return {"S1": np.array([V1, V2, 0.0]) / V, "ST": np.array([V1 + V13, V2, V13]) / V}

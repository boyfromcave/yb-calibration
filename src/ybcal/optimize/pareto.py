"""Multi-objective fronts, knee points and policy-constrained selection (PLAN §5, §5.10).

Objectives are columns of an ``(n, k)`` matrix with a direction per column (``"min"`` / ``"max"``).
Internally every column is turned into a minimisation (``max`` columns negated).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any, Literal

import numpy as np

from ybcal.studies.base import ResultTable

OWNER_WP = "WP-6"

Direction = Literal["min", "max"]


def _as_min(F: np.ndarray | Sequence[Sequence[float]], directions: Sequence[Direction]) -> np.ndarray:
    a = np.atleast_2d(np.asarray(F, dtype=float))
    if a.shape[1] != len(directions):
        raise ValueError(f"{a.shape[1]} objectives but {len(directions)} directions")
    sign = np.array([1.0 if d == "min" else -1.0 if d == "max" else math.nan for d in directions])
    if np.isnan(sign).any():
        raise ValueError("directions must be 'min' or 'max'")
    return a * sign


def dominates(a: np.ndarray, b: np.ndarray) -> bool:
    """``a`` dominates ``b`` (both already in minimisation form)."""
    return bool(np.all(a <= b) and np.any(a < b))


def non_dominated_sort(F: np.ndarray | Sequence[Sequence[float]],
                       directions: Sequence[Direction]) -> list[list[int]]:
    """Fast non-dominated sort (Deb et al. 2002): a list of fronts, each a sorted list of row
    indices; front 0 is the Pareto set. Duplicate rows share a front."""
    G = _as_min(F, directions)
    n = G.shape[0]
    if n == 0:
        return []
    le = np.all(G[:, None, :] <= G[None, :, :], axis=2)
    lt = np.any(G[:, None, :] < G[None, :, :], axis=2)
    dom = le & lt                                  # dom[i, j]: i dominates j
    count = dom.sum(axis=0)                        # how many dominate j
    fronts: list[list[int]] = []
    current = [j for j in range(n) if count[j] == 0]
    while current:
        fronts.append(sorted(current))
        nxt = []
        for i in current:
            for j in np.flatnonzero(dom[i]):
                count[j] -= 1
                if count[j] == 0:
                    nxt.append(int(j))
        current = nxt
    return fronts


def pareto_front(F: np.ndarray | Sequence[Sequence[float]], directions: Sequence[Direction]) -> list[int]:
    """Indices of the non-dominated rows."""
    fronts = non_dominated_sort(F, directions)
    return fronts[0] if fronts else []


def ranks(F: np.ndarray | Sequence[Sequence[float]], directions: Sequence[Direction]) -> np.ndarray:
    """Front number per row (0 = Pareto-optimal)."""
    fronts = non_dominated_sort(F, directions)
    r = np.zeros(sum(len(f) for f in fronts), dtype=int)
    for k, f in enumerate(fronts):
        r[f] = k
    return r


def normalize(F: np.ndarray | Sequence[Sequence[float]], directions: Sequence[Direction],
              rows: Sequence[int] | None = None) -> np.ndarray:
    """Minimisation form scaled to ``[0, 1]`` per objective over ``rows`` (ideal → 0, nadir → 1;
    a constant column maps to 0)."""
    G = _as_min(F, directions)
    ref = G if rows is None else G[list(rows)]
    lo, hi = ref.min(axis=0), ref.max(axis=0)
    span = np.where(hi > lo, hi - lo, 1.0)
    return (G - lo) / span


def knee_point(F: np.ndarray | Sequence[Sequence[float]], directions: Sequence[Direction],
               front: Sequence[int] | None = None) -> int:
    """The knee of the Pareto front: after normalising the front to ``[0, 1]`` per objective, the
    point farthest below the hyperplane ``Σ f = 1`` through the normalised extremes, i.e. the
    smallest sum of normalised objectives (for ``k = 2`` this is the point with the largest
    perpendicular distance from the chord joining the two extremes). Ties → lowest index."""
    fr = list(front) if front is not None else pareto_front(F, directions)
    if not fr:
        raise ValueError("empty front")
    N = normalize(F, directions, fr)[fr]
    s = N.sum(axis=1)
    return int(fr[int(np.argmin(s))])


def select_feasible(F: np.ndarray | Sequence[Sequence[float]], directions: Sequence[Direction],
                    primary: int, feasible: Sequence[bool] | np.ndarray | None = None, *,
                    current: int | None = None, tol: float = 1e-12) -> int | None:
    """The policy-feasible row that is best on objective ``primary``. Ties (within
    ``tol·max(1, |best|)``) prefer a non-dominated row (among feasible ones), then ``current``, then
    the lowest index. ``None`` if no row is feasible."""
    G = _as_min(F, directions)
    n = G.shape[0]
    feas = np.ones(n, bool) if feasible is None else np.asarray(feasible, bool)
    idx = np.flatnonzero(feas)
    if not len(idx):
        return None
    col = G[idx, primary]
    best = col.min()
    ties = [int(i) for i in idx[col <= best + tol * max(1.0, abs(best))]]
    pf = set(int(idx[j]) for j in pareto_front(G[idx], ["min"] * G.shape[1]))
    ties.sort(key=lambda i: (i not in pf, i != current, i))
    return ties[0]


def objectives_from_table(table: ResultTable, metrics: Sequence[str]) -> np.ndarray:
    """``(n_rows, k)`` matrix of ``metrics`` from a :class:`ResultTable`."""
    return np.column_stack([table.column(m) for m in metrics]) if len(table) else np.zeros((0, len(metrics)))


def front_for_plot(F: np.ndarray | Sequence[Sequence[float]], directions: Sequence[Direction],
                   names: Sequence[str], labels: Sequence[Any] | None = None, *,
                   feasible: Sequence[bool] | None = None, primary: int | None = None,
                   current: int | None = None) -> dict[str, Any]:
    """JSON-ready description for WP-8's plots: every point with its objectives (original sign),
    front rank, Pareto/knee/selected flags, plus the front ordered along the first objective."""
    A = np.atleast_2d(np.asarray(F, dtype=float))
    n = A.shape[0]
    rk = ranks(A, directions) if n else np.zeros(0, int)
    fr = [i for i in range(n) if rk[i] == 0]
    knee = knee_point(A, directions, fr) if fr else None
    sel = select_feasible(A, directions, primary, feasible, current=current) if primary is not None else None
    feas = [True] * n if feasible is None else [bool(x) for x in feasible]
    pts = [{
        "index": i,
        "label": str(labels[i]) if labels is not None else str(i),
        "objectives": {nm: float(A[i, j]) for j, nm in enumerate(names)},
        "rank": int(rk[i]), "pareto": bool(rk[i] == 0), "knee": i == knee,
        "feasible": feas[i], "selected": i == sel, "current": i == current,
    } for i in range(n)]
    return {
        "names": list(names), "directions": list(directions), "points": pts,
        "front": sorted(fr, key=lambda i: (A[i, 0], i)), "knee": knee, "selected": sel,
    }

"""Descriptive statistics of a price path (``ybcal data describe``; PLAN §4, §5.2, §5.3).

* realised volatility, annualised at the path's resolution (and rolling);
* maximum-drawdown distribution over horizons 30 days … 5 years, taken over **all start dates**
  (strided to at most ~one start per day to bound cost) — the G3 drawdown budget;
* the Hill tail index of each tail of the return distribution;
* gap statistics (zero prices, forward-filled points, longest gap);
* autocorrelation of returns and of absolute returns (volatility clustering).

Statistics are floats by design (they are metrics, never consensus quantities).

Owner: WP-2.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from typing import Any

import numpy as np

from ybcal.data.pricepath import STEP_SECONDS, log_returns, steps_per_year
from ybcal.types import PricePath

OWNER_WP = "WP-2"

#: Default drawdown horizons, in days.
DRAWDOWN_HORIZONS_DAYS: tuple[int, ...] = (30, 90, 180, 365, 730, 1095, 1825)


def _observed_mask(pp: PricePath) -> np.ndarray | None:
    """The loader's per-point "observed" mask (``~meta['filled']``), or None for synthetic paths."""
    filled = pp.meta.get("filled")
    if isinstance(filled, np.ndarray) and filled.shape[-1] == pp.n_steps:
        return ~np.broadcast_to(filled.astype(bool), pp.prices.shape)
    return None


def native_returns(pp: PricePath, path: int = 0) -> tuple[np.ndarray, int]:
    """Log returns between consecutive **observed** points of one path at their modal spacing,
    and that spacing in grid steps.

    A real file resampled onto the hour grid carries ``meta['filled']``. Daily data on the hour
    grid is 23/24 forward-filled: its hour-to-hour returns are mostly exact zeros, which leave the
    variance sum intact but wreck kurtosis, the Hill index and every autocorrelation. Returns are
    therefore taken observed point to observed point (as ``synthetic.fit_returns_of`` fits), so
    daily data is described as daily returns. Without a mask (synthetic paths) every step counts."""
    obs = _observed_mask(pp)
    p = pp.prices[path].astype(np.float64)
    ok = p > 0
    if obs is not None:
        ok &= obs[path]
    idx = np.nonzero(ok)[0]
    if len(idx) < 2:
        return np.zeros(0), 1
    lp = np.log(p[idx])
    spacing = np.diff(idx)
    vals, counts = np.unique(spacing, return_counts=True)
    m = int(vals[np.argmax(counts)])
    r = np.diff(lp)[spacing == m]
    return r[np.isfinite(r)], m


def _returns(pp: PricePath, path: int | None = None, observed_only: bool = True) -> np.ndarray:
    """Finite log returns of one path (or all, flattened). For a real file (one path with a filled
    mask) these are :func:`native_returns`; for synthetic ensembles, every step of every path."""
    if observed_only and _observed_mask(pp) is not None and (path is not None or pp.n_paths == 1):
        return native_returns(pp, path or 0)[0]
    r = log_returns(pp)
    if path is not None:
        r = r[path]
    return r[np.isfinite(r)]


def _native_steps(pp: PricePath) -> int:
    """Grid steps per return in :func:`_returns` (1 except for coarser real data on a finer grid)."""
    if _observed_mask(pp) is not None and pp.n_paths == 1:
        return native_returns(pp, 0)[1]
    return 1


def realised_vol(pp: PricePath, path: int | None = None) -> float:
    """Annualised sd of log returns (all paths pooled when ``path`` is None), annualised at the
    returns' own spacing (daily data → daily returns × √365)."""
    r = _returns(pp, path)
    if len(r) < 2:
        return math.nan
    per_year = steps_per_year(pp.resolution) / _native_steps(pp)
    return float(np.std(r, ddof=1) * math.sqrt(per_year))


def rolling_vol(pp: PricePath, window_steps: int, path: int = 0) -> np.ndarray:
    """Annualised rolling sd of log returns over ``window_steps`` returns (NaN until full)."""
    r = log_returns(pp)[path]
    out = np.full(len(r), np.nan)
    if len(r) < window_steps:
        return out
    win = np.lib.stride_tricks.sliding_window_view(r, window_steps)
    out[window_steps - 1 :] = np.nanstd(win, axis=1, ddof=1) * math.sqrt(steps_per_year(pp.resolution))
    return out


def max_drawdown(prices: np.ndarray) -> float:
    """Largest peak-to-trough fall of one price series, as a fraction (0 … 1). Gaps are ignored."""
    p = np.asarray(prices, dtype=np.float64)
    p = p[p > 0]
    if len(p) < 2:
        return 0.0
    peak = np.maximum.accumulate(p)
    return float(np.max(1.0 - p / peak))


def drawdown_distribution(
    pp: PricePath,
    horizons_days: Sequence[int] = DRAWDOWN_HORIZONS_DAYS,
    *,
    max_starts: int = 2000,
    max_paths: int = 50,
) -> dict[int, dict[str, float]]:
    """Max drawdown within ``[s, s + h]`` for every start ``s`` (strided; pooled over paths).

    Returns ``{horizon_days: {n, mean, p50, p90, p95, p99, max}}``; ``n = 0`` when the path is
    shorter than the horizon. Starts are spaced ``max(1 day, n/max_starts)`` apart; at most
    ``max_paths`` paths (the first ones) are used, to bound cost on large ensembles.
    """
    step = STEP_SECONDS[pp.resolution]
    per_day = 86400 // step
    out: dict[int, dict[str, float]] = {}
    p = pp.prices[:max_paths].astype(np.float64)
    p = np.where(p > 0, p, np.nan)
    # forward-fill gaps so a gap never reads as a crash
    for i in range(p.shape[0]):
        row = p[i]
        idx = np.where(np.isfinite(row), np.arange(len(row)), 0)
        np.maximum.accumulate(idx, out=idx)
        p[i] = row[idx]
    for h in horizons_days:
        w = h * per_day
        n_start = pp.n_steps - w
        if n_start <= 0:
            out[int(h)] = {"n": 0}
            continue
        stride = max(per_day, n_start // max_starts)
        mdds = []
        for i in range(p.shape[0]):
            for s in range(0, n_start, stride):
                seg = p[i, s : s + w + 1]
                seg = seg[np.isfinite(seg)]
                if len(seg) > 1:
                    mdds.append(float(np.max(1.0 - seg / np.maximum.accumulate(seg))))
        a = np.array(mdds)
        out[int(h)] = (
            {
                "n": len(a),
                "mean": float(a.mean()),
                "p50": float(np.quantile(a, 0.5)),
                "p90": float(np.quantile(a, 0.9)),
                "p95": float(np.quantile(a, 0.95)),
                "p99": float(np.quantile(a, 0.99)),
                "max": float(a.max()),
            }
            if len(a)
            else {"n": 0}
        )
    return out


def hill_tail_index(returns: np.ndarray, tail: str = "lower", k: int | None = None) -> float:
    """Hill estimator α̂ of the tail index of ``returns`` (smaller = fatter; Gaussian → large).

    ``tail`` = ``lower`` (losses), ``upper`` or ``both`` (absolute returns). ``k`` (order statistics
    used) defaults to 5 % of the sample, at least 10.
    """
    r = np.asarray(returns, dtype=np.float64)
    r = r[np.isfinite(r)]
    x = -r if tail == "lower" else r if tail == "upper" else np.abs(r)
    x = np.sort(x[x > 0])[::-1]
    if k is None:
        k = max(10, int(0.05 * len(x)))
    if len(x) <= k + 1:
        return math.nan
    logs = np.log(x[:k]) - math.log(x[k])
    m = float(np.mean(logs))
    return math.inf if m <= 0 else 1.0 / m


def autocorrelation(x: np.ndarray, lags: Sequence[int] = (1, 2, 3, 6, 12, 24)) -> dict[int, float]:
    """Sample autocorrelation at each lag."""
    x = np.asarray(x, dtype=np.float64)
    x = x[np.isfinite(x)] - np.nanmean(x)
    var = float(np.dot(x, x))
    out = {}
    for lag in lags:
        out[int(lag)] = float(np.dot(x[:-lag], x[lag:]) / var) if 0 < lag < len(x) and var > 0 else math.nan
    return out


def gap_stats(pp: PricePath) -> dict[str, Any]:
    """Zero-price steps, longest gap run, forward-filled share (from ``meta['filled']``)."""
    zeros = pp.prices <= 0
    longest = 0
    for row in zeros:
        cur = 0
        for v in row.tolist():
            cur = cur + 1 if v else 0
            longest = max(longest, cur)
    filled = pp.meta.get("filled")
    ff = float(np.asarray(filled).mean()) if isinstance(filled, np.ndarray) else 0.0
    step = STEP_SECONDS[pp.resolution]
    return {
        "zero_steps": int(zeros.sum()),
        "longest_gap_steps": longest,
        "longest_gap_hours": longest * step / 3600.0,
        "filled_fraction": ff,
        "source_gaps": pp.meta.get("gaps"),
        "source_longest_gap_hours": pp.meta.get("longest_gap_hours"),
    }


def realised_vol_by_horizon(
    pp: PricePath, horizons_hours: Sequence[int] = (1, 4, 24, 168), path: int = 0
) -> dict[str, float]:
    """Annualised vol from non-overlapping ``h``-hour log returns of one path (gaps forward-filled).

    Equal values across horizons mean returns are serially uncorrelated; vol falling with the
    horizon means negative autocorrelation at short lags (bid-ask bounce, aggregator noise, stale
    venues), so the 1-hour figure overstates the volatility that matters over days."""
    step_h = STEP_SECONDS[pp.resolution] / 3600.0
    p = pp.prices[path].astype(np.float64)
    p = np.where(p > 0, p, np.nan)
    idx = np.where(np.isfinite(p), np.arange(len(p)), 0)
    np.maximum.accumulate(idx, out=idx)
    lp = np.log(p[idx])
    native_h = _native_steps(pp) * step_h
    out: dict[str, float] = {}
    for h in horizons_hours:
        if h < native_h - 1e-9:
            continue
        k = max(1, round(h / step_h))
        r = np.diff(lp[::k])
        r = r[np.isfinite(r)]
        out[str(int(h))] = float(np.std(r, ddof=1) * math.sqrt(8760.0 / h)) if len(r) > 10 else math.nan
    return out


def flat_stats_of(pp: PricePath, path: int = 0) -> dict[str, float]:
    """Stale/flat stretches among the observed points of one path: share of exactly-zero returns
    and the length distribution of runs of an unchanged price (a thin market printing the same
    trade for hours; medians and σ estimates react to it)."""
    from ybcal.data.venues import flat_stats  # local: venues imports the loaders

    obs = _observed_mask(pp)
    p = pp.prices[path]
    ok = p > 0
    if obs is not None:
        ok &= obs[path]
    idx = np.nonzero(ok)[0]
    step = STEP_SECONDS[pp.resolution] * (native_returns(pp, path)[1] if obs is not None else 1)
    return flat_stats(p[idx].astype(np.float64), step)


def describe(pp: PricePath, horizons_days: Sequence[int] = DRAWDOWN_HORIZONS_DAYS) -> dict[str, Any]:
    """Everything above, as one JSON-safe dict."""
    r = _returns(pp)
    step = STEP_SECONDS[pp.resolution]
    span_days = (pp.n_steps - 1) * step / 86400.0
    p = pp.prices[pp.prices > 0]
    native = _native_steps(pp)
    lags = (1, 2, 3, 6, 12, 24) if pp.resolution == "hour" else (1, 2, 4, 12, 48, 576)
    if native > 1:
        lags = (1, 2, 3, 5, 10, 20)  # in native steps (e.g. days)
    return {
        "provenance": pp.provenance,
        "resolution": pp.resolution,
        "t0": pp.t0.isoformat(),
        "n_paths": pp.n_paths,
        "n_steps": pp.n_steps,
        "span_days": span_days,
        "price_usd": {
            "first": float(pp.prices[0, 0]) / 1e6,
            "last": float(pp.prices[0, -1]) / 1e6,
            "min": float(p.min()) / 1e6 if len(p) else math.nan,
            "max": float(p.max()) / 1e6 if len(p) else math.nan,
        },
        "realised_vol_annual": realised_vol(pp),
        "return_sd": float(np.std(r, ddof=1)) if len(r) > 1 else math.nan,
        "skew": float(((r - r.mean()) ** 3).mean() / r.std() ** 3)
        if len(r) > 2 and r.std() > 0
        else math.nan,
        "excess_kurtosis": float(((r - r.mean()) ** 4).mean() / r.std() ** 4 - 3)
        if len(r) > 3 and r.std() > 0
        else math.nan,
        "max_drawdown": max(max_drawdown(row) for row in pp.prices),
        "drawdowns": {str(k): v for k, v in drawdown_distribution(pp, horizons_days).items()},
        "hill_lower": hill_tail_index(r, "lower"),
        "hill_upper": hill_tail_index(r, "upper"),
        "acf_returns": {str(k): v for k, v in autocorrelation(r, lags).items()},
        "acf_abs_returns": {str(k): v for k, v in autocorrelation(np.abs(r), lags).items()},
        "native_step_hours": native * step / 3600.0,
        "realised_vol_by_horizon_hours": realised_vol_by_horizon(pp) if pp.n_paths == 1 else {},
        "n_returns": len(r),
        "flat": flat_stats_of(pp),
        "gaps": gap_stats(pp),
        "meta": {k: v for k, v in pp.meta.items() if isinstance(v, str | int | float | bool)},
    }


def _f(v: Any, pct: bool = False) -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "-"
    return f"{100 * v:.1f}%" if pct else f"{v:.4g}" if isinstance(v, float) else str(v)


def format_description(d: dict[str, Any]) -> str:
    """Human-readable rendering of :func:`describe`."""
    lines = [
        f"provenance: {d['provenance']}   resolution: {d['resolution']}   paths: {d['n_paths']}   "
        f"steps: {d['n_steps']}   span: {d['span_days']:.1f} days   t0: {d['t0']}",
        f"price USD: first {_f(d['price_usd']['first'])}  last {_f(d['price_usd']['last'])}  "
        f"min {_f(d['price_usd']['min'])}  max {_f(d['price_usd']['max'])}",
        f"realised vol (annualised): {_f(d['realised_vol_annual'], True)}   skew {_f(d['skew'])}   "
        f"excess kurtosis {_f(d['excess_kurtosis'])}",
        f"tail index (Hill): lower {_f(d['hill_lower'])}  upper {_f(d['hill_upper'])}   "
        f"max drawdown: {_f(d['max_drawdown'], True)}",
        "max drawdown by horizon (all start dates):",
        f"  {'days':>6} {'n':>6} {'p50':>7} {'p90':>7} {'p95':>7} {'p99':>7} {'max':>7}",
    ]
    for h, s in d["drawdowns"].items():
        if not s.get("n"):
            lines.append(f"  {h:>6} {0:>6}   (path shorter than horizon)")
            continue
        lines.append(
            f"  {h:>6} {s['n']:>6} {_f(s['p50'], True):>7} {_f(s['p90'], True):>7} "
            f"{_f(s['p95'], True):>7} {_f(s['p99'], True):>7} {_f(s['max'], True):>7}"
        )
    lines.append("autocorrelation  lag: " + "  ".join(f"{k}:{_f(v)}" for k, v in d["acf_returns"].items()))
    lines.append(
        "|returns| acf    lag: " + "  ".join(f"{k}:{_f(v)}" for k, v in d["acf_abs_returns"].items())
    )
    lines.append(
        f"returns: {d.get('n_returns', '-')} at a native step of {d.get('native_step_hours', 1):g} h"
        + (" (autocorrelation lags in native steps)" if d.get("native_step_hours", 1) > 1 else "")
    )
    vh = d.get("realised_vol_by_horizon_hours") or {}
    if vh:
        lines.append(
            "realised vol by return horizon: " + "  ".join(f"{h} h {_f(v, True)}" for h, v in vh.items())
        )
    fl = d.get("flat")
    if fl:
        lines.append(
            f"flat stretches: zero returns {_f(fl['zero_return_share'], True)}  runs {fl['runs']}  "
            f"(>= 6 h: {fl['runs_ge_6h']})  run p50 {fl['run_p50_hours']:g} h  "
            f"p90 {fl['run_p90_hours']:g} h  max {fl['run_max_hours']:g} h"
        )
    cl = d.get("meta", {}).get("clamped_points")
    if cl:
        lines.append(
            f"WARNING: {cl} observations lie outside the node's price bounds ($0.0001 .. $100) and were "
            "clamped: statistics are wrong; rescale the file (--scale) — returns are scale-free"
        )
    g = d["gaps"]
    lines.append(
        f"gaps: zero steps {g['zero_steps']}  longest {g['longest_gap_hours']:.1f} h  "
        f"forward-filled {_f(g['filled_fraction'], True)}  source gaps {_f(g['source_gaps'])}"
    )
    return "\n".join(lines)


def to_json(d: dict[str, Any]) -> str:
    """JSON with non-finite floats as null."""

    def clean(o: Any) -> Any:
        if isinstance(o, float) and not math.isfinite(o):
            return None
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items()}
        if isinstance(o, list | tuple):
            return [clean(v) for v in o]
        return o

    return json.dumps(clean(d), indent=2, sort_keys=True)

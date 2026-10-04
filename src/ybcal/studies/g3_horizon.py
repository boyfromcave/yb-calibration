"""G3 long-horizon evidence: daily-history price members, real-history replays and the ratio frontier.

Owner: collateral group (wave 2, D-RD-COL-*). Used by :mod:`ybcal.studies.g3_collateral` (long-horizon
members when a daily price path is supplied) and by ``python -m ybcal.studies.g3_horizon`` (the
frontier evidence of the 2026-10 real-data report).

Why a separate horizon layer
----------------------------
Fact 1.5-1 (``ycash6 src/yellowback/script.cpp:76-92``: ``IF <lock> CLTV <owner> CHECKSIG ELSE
<lock + grace> CLTV TRUE ENDIF``) means a vault's collateral must cover its debt from the mint to
``lockHeight + grace`` with no liquidation in between, so P(bad debt) is a statement about the price
ratio ``p(claim opening) / pMint`` over 60 days (class A's shortest term plus grace) to 5 years and a
month (class C's longest). Two facts of the real YEC data make the hourly file the wrong source for
that ratio:

* the hourly file starts 2020-03-26 (CoinMarketCap's hourly history), so a 5-year term has only
  about 1.5 years of real start dates — and the 2019-07 → 2020-03 fall of 98 % is missing;
* realised volatility depends strongly on the horizon (docs/real-data-2026-10.md; ``yec-daily.csv``):
  annualised 440 % at one hour, 235 % at one day, ≈ 160–180 % over a week to a quarter and ≈ 115 %
  over a year — a model's long-horizon dispersion is set by how much of that mean reversion its
  dependence structure keeps, not by its one-step σ.

So the members here are fitted on the **daily** series (2019-07-20 → 2026-10, CoinMarketCap spliced
over CoinCodex, D-RD-D2) and simulated at hour resolution (a Brownian bridge inside each day) so the
hour-mode oracle kernel prices the mint exactly as G3 does:

* ``bootstrap-<b>d`` — stationary block bootstrap of the daily log returns, demeaned (D-RD-D1,
  D-RD-AUD-1 centred), mean block ``b`` days: 30 d keeps the monthly structure, 365 d also keeps the
  annual mean reversion the data show (with only ~7 independent blocks: an optimistic bound);
* ``regime`` — the two-state regime switch fitted on the daily returns, centred;
* ``martingale`` — the 30-day bootstrap shifted to zero arithmetic drift (expected log drift −σ²/2):
  the stress convention of D-RD-AUD-1;
* ``history`` — the real daily path itself (log-linear between daily closes), every real start date:
  no model at all. For a term ``T`` only starts with ``start + T + grace`` inside the data count, so
  ``n_eff = span / (T + grace)`` non-overlapping windows exist; for 5-year terms that is below 1.5.

The frontier
------------
A vault minted at hour ``s`` with locked ratio ``R`` (``base × σ multiplier``) holds ``R · debt /
pMint[s]`` YEC (MINT-5, ``state.cpp:322-327``), and is bad at the claim opening iff ``R · x < 1`` with
``x = true[s + ⌈(T + grace + 1)/48⌉] / pMint[s]`` — the test of :func:`ybcal.sim.metrics.p_bad_debt_fast`
up to the wallet's 1,000-zat rounding. So the ratio samples ``x`` are drawn once per member, class
and grid term, and every ratio's P(bad debt), severity ``E[max(0, 1 − R x)]`` and capital efficiency
(YED per USD of YEC locked = ``1/R``) follow from the sorted samples: the frontier costs nothing per
ratio. The class value averages the term grid with equal weights, as G3 does.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from ybcal.data import synthetic as SY
from ybcal.data.pricepath import DT_HOUR, PricePath
from ybcal.sim import agents as AG
from ybcal.sim import drift as DR
from ybcal.sim import engine as E
from ybcal.units import BLOCKS_PER_DAY, BLOCKS_PER_HOUR, BPS

OWNER_WP = "WP-7b"

DT_DAY: float = 24 * DT_HOUR
CLASS_NAMES = ("A", "B", "C")
#: Members built from the daily series, in report order.
DAILY_MODELS = ("bootstrap-30d", "bootstrap-365d", "regime", "martingale", "history")
#: Named data windows (inclusive ISO dates; ``None`` = the series' own end).
WINDOWS: dict[str, tuple[str | None, str | None]] = {
    "full": (None, None),
    "last365": ("-365", None),
    "2021-22": ("2021-01-01", "2022-12-31"),
    "2025-26": ("2025-01-01", None),
    "ex-launch": ("2020-01-01", None),
}
#: Locked-ratio grid of the frontier (bps): fine near the shipped values, coarse above.
FRONTIER_RATIOS: tuple[int, ...] = tuple(
    list(range(20_000, 100_001, 2_500)) + list(range(110_000, 200_001, 10_000))
    + [250_000, 300_000, 400_000, 500_000, 1_000_000]
)
HOUR_SUBSTEPS = 4


# ===================================================================================================
# The daily series


@dataclass(frozen=True)
class Daily:
    """A daily close series (``ts`` unix seconds, ``price`` USD/YEC), sorted, positive."""

    ts: np.ndarray
    price: np.ndarray
    source: str = ""

    def __len__(self) -> int:
        return int(self.ts.size)

    @property
    def span(self) -> str:
        return f"{_iso(self.ts[0])} → {_iso(self.ts[-1])}" if len(self) else "empty"

    def window(self, start: str | None, end: str | None) -> Daily:
        """The closes in ``[start, end]`` (ISO dates; ``"-N"`` = the last N days)."""
        lo, hi = -math.inf, math.inf
        if start is not None and start.startswith("-"):
            lo = float(self.ts[-1]) - int(start[1:]) * 86_400
        elif start is not None:
            lo = _parse_day(start)
        if end is not None:
            hi = _parse_day(end) + 86_399
        m = (self.ts >= lo) & (self.ts <= hi)
        return Daily(self.ts[m], self.price[m], self.source)

    def returns(self) -> np.ndarray:
        """Daily log returns between consecutive closes one day apart, stale runs dropped (D-RD-D4)."""
        lp = np.log(self.price)
        r = np.diff(lp)[np.diff(self.ts) == 86_400]
        return SY.drop_stale_runs(r)


def _iso(t: float) -> str:
    return datetime.fromtimestamp(int(t), UTC).strftime("%Y-%m-%d")


def _parse_day(s: str) -> float:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=UTC).timestamp()


def load_daily(path: str | Path) -> Daily:
    """A price CSV (``ts,price_usd`` …) as daily closes: the last price of each UTC day."""
    from ybcal.data import loaders as L

    ser = L.load_price_csv(path)
    day = (np.asarray(ser.ts, dtype=np.int64) // 86_400) * 86_400
    last = np.r_[day[1:] != day[:-1], True]
    return Daily(day[last], np.asarray(ser.price_usd, dtype=float)[last], str(path))


def daily_of(pp: PricePath) -> Daily | None:
    """A real price path on an hourly grid whose observations are daily (the loader's forward fill)
    → its daily closes; ``None`` when the path is not daily."""
    p = np.asarray(pp.prices, dtype=np.int64)[0]
    filled = pp.meta.get("filled")
    obs = p > 0
    if isinstance(filled, np.ndarray) and filled.shape[-1] == p.shape[0]:
        f = filled if filled.ndim == 1 else filled[0]
        obs = obs & ~f.astype(bool)
    idx = np.nonzero(obs)[0]
    if idx.size < 3:
        return None
    step = 1 if pp.resolution == "hour" else 0
    if not step:
        return None
    vals, counts = np.unique(np.diff(idx), return_counts=True)
    if int(vals[np.argmax(counts)]) != 24:
        return None
    t0 = int(pp.t0.timestamp())
    return Daily(t0 + idx.astype(np.int64) * 3600, p[idx] / 1e6, "price path")


# ===================================================================================================
# Members


def daily_to_hourly(price_usd: np.ndarray) -> np.ndarray:
    """Log-linear interpolation of daily closes to hourly µUSD (one row): the real history between
    its observations, with no invented intraday noise."""
    lp = np.log(np.asarray(price_usd, dtype=float) * 1e6)
    n = (lp.size - 1) * 24 + 1
    x = np.interp(np.arange(n) / 24.0, np.arange(lp.size), lp)
    return np.rint(np.exp(x)).astype(np.int64)[None, :]


def fit_member(name: str, d: Daily) -> SY.PriceModel:
    """The daily-fitted model behind member ``name`` (bootstrap-<b>d, regime, martingale)."""
    r = d.returns()
    if name.startswith("bootstrap-") or name == "martingale":
        b = 30.0 if name == "martingale" else float(name.split("-")[1].rstrip("d"))
        return SY.BlockBootstrap(returns=r, mean_block=min(b, len(r) / 4), dt_native=DT_DAY, demean=True)
    if name == "regime":
        return SY.RegimeSwitch.fit_returns(r, DT_DAY)
    raise ValueError(f"unknown daily member {name!r}")


def simulate_member(
    name: str, d: Daily, *, paths: int, years: float, rng: np.random.Generator, p0_usd: float | None = None
) -> np.ndarray:
    """Hourly true prices ``(paths, years·8760 + 1)`` µUSD of a fitted daily member (centred, or the
    martingale convention for ``martingale``)."""
    model = fit_member(name, d)
    n = round(years * 8760) + 1
    r = model.log_returns(paths, n, "hour", rng)[:, : n - 1]
    r = DR.apply(r, DT_HOUR, "martingale" if name == "martingale" else "centred", model)
    p0 = round((p0_usd if p0_usd is not None else float(d.price[-1])) * 1e6)
    return np.asarray(SY.returns_to_path(r, max(1, p0), DT_HOUR).prices, dtype=np.int64)


def realised_vol_by_horizon(
    hourly: np.ndarray, horizons_days: Sequence[int] = (1, 7, 30, 90, 365)
) -> dict[int, float]:
    """Annualised σ of non-overlapping-start log returns at each horizon, pooled over paths."""
    lp = np.log(np.asarray(hourly, dtype=float))
    out = {}
    for k in horizons_days:
        h = 24 * k
        if lp.shape[1] <= h:
            out[k] = math.nan
            continue
        r = (lp[:, h:] - lp[:, :-h])[:, :: max(1, h // 4)]
        out[k] = float(np.std(r) * math.sqrt(365.0 / k))
    return out


def daily_vol_by_horizon(d: Daily, horizons_days: Sequence[int] = (1, 7, 30, 90, 365)) -> dict[int, float]:
    lp = np.log(d.price)
    return {
        k: (float(np.std(lp[k:] - lp[:-k]) * math.sqrt(365.0 / k)) if lp.size > k + 2 else math.nan)
        for k in horizons_days
    }


# ===================================================================================================
# Ratio samples and the frontier


@dataclass
class RatioSamples:
    """Per class and grid term, the sorted samples of ``x = true(claim opening) / pMint(mint)`` (and
    at the owner-path opening, ``lock``)."""

    member: str
    terms: dict[str, np.ndarray]
    x: dict[str, list[np.ndarray]]
    x_lock: dict[str, list[np.ndarray]]
    n_starts: dict[str, list[int]]
    meta: dict[str, Any] = field(default_factory=dict)


def ratio_samples(
    params: Mapping,
    hourly_true: np.ndarray,
    member: str,
    *,
    hours: E.HourSeries | None = None,
    n_terms: int = 16,
    term_distribution: str = "uniform",
    start_stride: int = 24,
    skip_hours: int | None = None,
    classes: Sequence[int] = (0, 1, 2),
) -> RatioSamples:
    """Draw ``x`` for every start (stride ``start_stride`` hours after the oracle warm-up), path and
    grid term of each class. Starts whose claim opening lies beyond the path are censored, and starts
    the mint could not use (pMint undefined, NO_PRICE / DIVERGENCE halt) are skipped — as in
    :func:`ybcal.sim.metrics.p_bad_debt_fast`."""
    tp = np.asarray(hourly_true, dtype=np.int64)
    if hours is None:
        hours = E.simulate_hours(params, tp, E.OracleTransferKernel.ideal(params, substeps=HOUR_SUBSTEPS))
    P, n = tp.shape
    if skip_hours is None:
        skip_hours = math.ceil((int(params["volWindow"]) + int(params["pSlowWindow"])) / BLOCKS_PER_HOUR)
    starts = np.arange(skip_hours, n, max(1, int(start_stride)))
    pm = hours.p_mint[:, starts]
    ok = (pm > 0) & ((hours.halt_mask[:, starts] & (E.HALT_NO_PRICE | E.HALT_DIVERGENCE)) == 0)
    g = int(params["grace"])
    out = RatioSamples(member, {}, {}, {}, {}, meta={"paths": P, "hours": n, "stride": start_stride})
    for c in classes:
        name = CLASS_NAMES[c]
        terms = AG.term_grid(params, c, n_terms, term_distribution)[0]  # type: ignore[arg-type]
        out.terms[name] = terms
        xs, xl, ns = [], [], []
        for T in terms.tolist():
            row = []
            for off in (T + g + 1, T + 1):
                e = starts + -(-off // BLOCKS_PER_HOUR)
                inside = e < n
                cols = np.nonzero(inside)[0]
                good = ok[:, cols]
                v = tp[:, e[cols]].astype(float) / np.where(good, pm[:, cols], 1).astype(float)
                row.append(np.sort(v[good]))
            xs.append(row[0])
            xl.append(row[1])
            ns.append(int(row[0].size))
        out.x[name], out.x_lock[name], out.n_starts[name] = xs, xl, ns
    return out


def _p_and_es(x_sorted: np.ndarray, ratios_bps: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """P(R·x < 1) and E[max(0, 1 − R·x)] for every ratio, from sorted samples."""
    if x_sorted.size == 0:
        return np.full(ratios_bps.shape, np.nan), np.full(ratios_bps.shape, np.nan)
    R = ratios_bps.astype(float) / BPS
    k = np.searchsorted(x_sorted, 1.0 / R, side="left")
    cs = np.r_[0.0, np.cumsum(x_sorted)]
    n = x_sorted.size
    p = k / n
    es = (k - R * cs[k]) / n
    return p, np.maximum(es, 0.0)


@dataclass
class Frontier:
    """P(bad debt) / severity / capital efficiency against the locked ratio, per class (term-grid mean)
    and per grid term, for one member."""

    member: str
    ratios: np.ndarray
    p: dict[str, np.ndarray]
    p_lock: dict[str, np.ndarray]
    es: dict[str, np.ndarray]
    p_by_term: dict[str, np.ndarray]  # (terms, ratios)
    terms: dict[str, np.ndarray]
    n_starts: dict[str, list[int]]
    meta: dict[str, Any] = field(default_factory=dict)

    def at(self, cls: str, ratio_bps: int) -> dict[str, float]:
        i = int(np.searchsorted(self.ratios, ratio_bps))
        if i >= self.ratios.size or self.ratios[i] != ratio_bps:
            raise KeyError(ratio_bps)
        return {
            "p": float(self.p[cls][i]),
            "es": float(self.es[cls][i]),
            "p_lock": float(self.p_lock[cls][i]),
        }

    def needed(self, cls: str, tol: float) -> float:
        """The smallest grid ratio (bps) whose class P(bad debt) ≤ ``tol`` (NaN when none)."""
        ok = np.nonzero(np.nan_to_num(self.p[cls], nan=1.0) <= tol)[0]
        return float(self.ratios[ok[0]]) if ok.size else math.nan

    def needed_by_term(self, cls: str, tol: float) -> np.ndarray:
        out = []
        for row in self.p_by_term[cls]:
            ok = np.nonzero(np.nan_to_num(row, nan=1.0) <= tol)[0]
            out.append(float(self.ratios[ok[0]]) if ok.size else math.nan)
        return np.asarray(out)


def frontier(rs: RatioSamples, ratios: Sequence[int] = FRONTIER_RATIOS) -> Frontier:
    R = np.asarray(sorted(set(int(r) for r in ratios)), dtype=np.int64)
    p, pl, es, pbt = {}, {}, {}, {}
    for name, xs in rs.x.items():
        rows = [_p_and_es(x, R) for x in xs]
        prow = np.array([r[0] for r in rows])
        erow = np.array([r[1] for r in rows])
        lrow = np.array([_p_and_es(x, R)[0] for x in rs.x_lock[name]])
        # the class value is the term-grid mean, defined only when every grid term has samples: a
        # censored window (a short history, long terms) would otherwise average its short terms only
        full = bool(np.isfinite(prow).all())
        nan = np.full(R.shape, np.nan)
        p[name] = prow.mean(axis=0) if full else nan
        es[name] = erow.mean(axis=0) if full else nan
        pl[name] = lrow.mean(axis=0) if full and np.isfinite(lrow).all() else nan
        pbt[name] = prow
    return Frontier(rs.member, R, p, pl, es, pbt, rs.terms, rs.n_starts, dict(rs.meta))


def merge(frontiers: Sequence[Frontier], label: str) -> Frontier:
    """Mean of several frontiers of the same member (seeds) — equal weights."""
    f0 = frontiers[0]

    def avg(get):
        return {k: np.nanmean(np.stack([get(f)[k] for f in frontiers]), axis=0) for k in get(f0)}

    return Frontier(
        label, f0.ratios, avg(lambda f: f.p), avg(lambda f: f.p_lock), avg(lambda f: f.es),
        avg(lambda f: f.p_by_term), f0.terms, f0.n_starts, {"merged": len(frontiers)},
    )


# ===================================================================================================
# Real-history replay


def history_table(
    params: Mapping, d: Daily, ratios_bps: Sequence[int], *, terms_days: Sequence[int]
) -> list[dict[str, Any]]:
    """Empirical replay on the real daily path: for every term (days) and every real start date with
    the claim opening inside the data, the distribution of ``x`` (true at claim opening / pMint), the
    share of starts bad at each ratio, the worst start and the effective number of independent
    windows (span / (term + grace))."""
    tp = daily_to_hourly(d.price)
    hrs = E.simulate_hours(params, tp, E.OracleTransferKernel.ideal(params, substeps=HOUR_SUBSTEPS))
    skip = math.ceil((int(params["volWindow"]) + int(params["pSlowWindow"])) / BLOCKS_PER_HOUR)
    starts = np.arange(skip, tp.shape[1], 24)
    pm = hrs.p_mint[0, starts]
    ok = pm > 0
    g = int(params["grace"])
    rows = []
    for T in terms_days:
        off = T * BLOCKS_PER_DAY + g + 1
        e = starts + -(-off // BLOCKS_PER_HOUR)
        inside = (e < tp.shape[1]) & ok
        if not inside.any():
            rows.append({"term_days": T, "n_starts": 0})
            continue
        x = tp[0, e[inside]] / pm[inside]
        s_idx = starts[inside]
        span_days = (tp.shape[1] - 1) / 24.0
        row: dict[str, Any] = {
            "term_days": T,
            "n_starts": int(x.size),
            "first_start": _iso(d.ts[0] + int(s_idx[0]) * 3600),
            "last_start": _iso(d.ts[0] + int(s_idx[-1]) * 3600),
            "n_eff": span_days / (T + g / BLOCKS_PER_DAY),
            "x_min": float(x.min()),
            "x_p01": float(np.percentile(x, 1)),
            "x_p05": float(np.percentile(x, 5)),
            "x_p50": float(np.percentile(x, 50)),
            "worst_start": _iso(d.ts[0] + int(s_idx[int(np.argmin(x))]) * 3600),
            "ratio_to_cover_all": float(math.ceil(BPS / x.min())),
        }
        for r in ratios_bps:
            row[f"p_bad@{r}"] = float(np.mean(x * r / BPS < 1.0))
        rows.append(row)
    return rows


# ===================================================================================================
# Claimant economics at real liquidity, and the emergency path on real crashes


#: Vault debts (cents) the claim-liquidity table scores: $100, $1,000 (G3's test vault), $10,000.
CLAIM_SIZES = (10_000, 100_000, 1_000_000)


def claim_liquidity_rows(
    env: Any, params: Mapping, sizes: Sequence[int] = CLAIM_SIZES
) -> list[dict[str, Any]]:
    """Claimant margin at the first RED-4(a) trigger against ``claimThresholdBps``, per vault size and
    claimant type, on G3's hourly members: ``sell`` = sells the YEC into the bid side of the book
    (G3's claim study, at that debt), ``hold`` = a YEC holder who keeps it. The worst member's mean,
    and the show-up rate, per threshold."""
    from ybcal.studies import g3_collateral as G3

    ens = G3.ensemble(env, params)
    pol = env.policy
    depth = G3.depth_p10_usd(env, float(pol.claimant_slippage_pctl))
    rows = []
    for cents in sizes:
        for sell in (True, False):
            st = G3.claim_stats(ens, params, pol, depth, cents=int(cents), sell=sell)
            for theta, d in sorted(st.items()):
                ms = G3.hourly_members(ens)
                rows.append({
                    "debt_usd": cents / 100, "claimant": "sell" if sell else "hold", "theta_bps": theta,
                    "mean_bps": G3.agg([d[f"{m}.mean_bps"] for m in ms], "worst", minimize=False),
                    "p10_bps": G3.agg([d[f"{m}.p10_bps"] for m in ms], "worst", minimize=False),
                    "show_up": G3.agg([d[f"{m}.show_up"] for m in ms], "worst", minimize=False),
                    "bad_at_trigger": G3.agg([d[f"{m}.bad"] for m in ms], "worst"),
                    "forfeit_bps": G3.agg([d[f"{m}.forfeit_bps"] for m in ms], "worst"),
                    "bid_depth_usd": depth if sell else math.nan,
                    **{f"mean_bps.{m}": d[f"{m}.mean_bps"] for m in ms},
                })
    return rows


def worst_windows(hourly: np.ndarray, days: int, k: int = 3) -> list[tuple[int, float]]:
    """The ``k`` worst non-overlapping ``days``-day log returns of one hourly path: (start hour, return)."""
    lp = np.log(np.asarray(hourly, dtype=float))
    h = 24 * days
    r = lp[h:] - lp[:-h]
    out: list[tuple[int, float]] = []
    taken = np.zeros(r.size, bool)
    for i in np.argsort(r):
        if len(out) >= k:
            break
        if taken[max(0, i - h) : i + h].any():
            continue
        out.append((int(i), float(r[i])))
        taken[i] = True
    return out


def real_crash_runs(params: Mapping, hourly: np.ndarray, *, durations=(1, 7, 30), k: int = 3,
                    pre_hours: int = 72, post_days: int = 60) -> Any:
    """A :class:`ybcal.studies.g3_collateral.CrashRuns` of the worst real falls: for each duration,
    the ``k`` worst non-overlapping windows of the hourly history, each cut from ``pre_hours`` before
    the fall to ``post_days`` after its start (one path per window, stacked per duration)."""
    from ybcal.studies import g3_collateral as G3

    x = np.asarray(hourly, dtype=np.int64).reshape(-1)
    kernel = E.OracleTransferKernel.ideal(params, substeps=HOUR_SUBSTEPS)
    warm = math.ceil((int(params["pSlowWindow"]) + int(params["volWindow"])) / BLOCKS_PER_HOUR)
    true, hours, names, info = {}, {}, [], {}
    for dd in durations:
        rows = []
        for i, r in worst_windows(x, dd, k):
            a = i - pre_hours - warm
            b = i + post_days * 24
            if a < 0 or b > x.size:
                continue
            rows.append(x[a:b])
            info.setdefault(f"real-worst-{dd}d", []).append({"start_hour": i, "log_return": r})
        if not rows:
            continue
        n = min(len(r) for r in rows)
        tp = np.stack([r[:n] for r in rows])
        name = f"real-worst-{dd}d"
        names.append(name)
        true[name] = tp
        hours[name] = E.simulate_hours(params, tp, kernel)
    cr = G3.CrashRuns(("real", tuple(durations), k), tuple(names), true, hours)
    cr.info = info  # type: ignore[attr-defined]
    return cr


# ===================================================================================================
# Driver: the frontier evidence


def run_frontier(
    params: Mapping,
    d: Daily,
    *,
    seeds: Sequence[int],
    paths: int,
    years: float,
    windows: Mapping[str, tuple[str | None, str | None]] = WINDOWS,
    models: Sequence[str] = DAILY_MODELS,
    n_terms: int = 16,
    term_distribution: str = "uniform",
    stride_hours: int = 24,
    hourly_history: np.ndarray | None = None,
    log=print,
) -> dict[tuple[str, str], Frontier]:
    """Frontiers keyed by (window, member). Fitted members are averaged over ``seeds`` (and each seed's
    frontier is kept as ``(window, "<member>#<seed>")`` for the stability check); ``history`` is the
    real path of each window (a class value only where the window holds every grid term);
    ``hourly-history`` reproduces the pre-wave-2 history member when ``hourly_history`` is given."""
    out: dict[tuple[str, str], Frontier] = {}
    for wname, (a, b) in windows.items():
        dw = d.window(a, b)
        for m in models:
            if m == "history":
                rs = ratio_samples(params, daily_to_hourly(dw.price), "history", n_terms=n_terms,
                                   term_distribution=term_distribution, start_stride=24)
                out[(wname, m)] = frontier(rs)
                out[(wname, m)].meta.update({"window": wname, "span": dw.span})
                log(f"  {wname:8s} {m:15s} n={[min(v) for v in rs.n_starts.values()]} (min per term)")
                continue
            per_seed = []
            for s in seeds:
                rng = np.random.default_rng(np.random.SeedSequence([int(s), 0x67336872, len(m), len(wname)]))
                tp = simulate_member(m, dw, paths=paths, years=years, rng=rng)
                rs = ratio_samples(params, tp, m, n_terms=n_terms, term_distribution=term_distribution,
                                   start_stride=stride_hours)
                f = frontier(rs)
                f.meta.update({"seed": int(s), "window": wname, "span": dw.span,
                               "vol": realised_vol_by_horizon(tp)})
                out[(wname, f"{m}#{s}")] = f
                per_seed.append(f)
            mf = merge(per_seed, m)
            mf.meta.update({"window": wname, "span": dw.span, "seeds": list(seeds),
                            "vol": per_seed[0].meta["vol"]})
            out[(wname, m)] = mf
            log(f"  {wname:8s} {m:15s} A@775 {mf.at('A', 77_500)['p']:.4f}"
                f"  B@700 {mf.at('B', 70_000)['p']:.4f}  C@600 {mf.at('C', 60_000)['p']:.4f}")
    if hourly_history is not None:
        rs = ratio_samples(params, hourly_history, "hourly-history", n_terms=n_terms,
                           term_distribution=term_distribution, start_stride=24)
        out[("full", "hourly-history")] = frontier(rs)
    return out


def frontier_rows(fr: Mapping[tuple[str, str], Frontier], tol: Mapping[str, float]) -> list[dict[str, Any]]:
    rows = []
    for (w, m), f in fr.items():
        for c in CLASS_NAMES:
            if c not in f.p:
                continue
            for i, r in enumerate(f.ratios.tolist()):
                rows.append({
                    "window": w, "member": m, "class": c, "ratio_bps": r,
                    "p_bad": float(f.p[c][i]), "p_bad_lock": float(f.p_lock[c][i]),
                    "es": float(f.es[c][i]), "yed_per_usd": BPS / r,
                    "meets": bool(f.p[c][i] <= tol[c]),
                })
    return rows


def needed_rows(fr: Mapping[tuple[str, str], Frontier], tol: Mapping[str, float]) -> list[dict[str, Any]]:
    rows = []
    for (w, m), f in fr.items():
        for c in CLASS_NAMES:
            if c not in f.p:
                continue
            nb = f.needed_by_term(c, tol[c])
            for T, need, n in zip(f.terms[c].tolist(), nb.tolist(), f.n_starts[c], strict=False):
                rows.append({"window": w, "member": m, "class": c, "term_days": T / BLOCKS_PER_DAY,
                             "ratio_needed_bps": need, "n_starts": n})
    return rows


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        return
    keys: list[str] = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: (f"{v:.6g}" if isinstance(v, float) else v) for k, v in r.items()})


def run_studies(
    *,
    hourly: str,
    daily: str | None,
    extra: Sequence[str],
    policy_path: str | None,
    budget: str,
    seed: int,
    workers: int,
    out: Path,
    groups: Sequence[str] = ("G3", "G4"),
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, list[Any]]:
    """G3 (and G4) on the real hourly file plus the daily history as ``price_daily`` — the study API
    path until ``recommend`` takes two price files (infra). Writes ``<out>/<group>.json`` with every
    recommendation's verdict, values and metrics."""
    import dataclasses

    from ybcal.config import Policy
    from ybcal.optimize.runner import run_group
    from ybcal.params.paramset import mainnet
    from ybcal.report.build import load_data
    from ybcal.studies.base import Budget, Env
    from ybcal.studies.g3_collateral import G3Study
    from ybcal.studies.g4_grace_abandon import G4Study

    pol = Policy.load(policy_path)
    if overrides:
        pol = dataclasses.replace(pol, **dict(overrides))
    data, prov, _ = load_data([Path(hourly), *[Path(x) for x in extra]])
    if daily:
        data["price_daily"] = load_daily(daily)
    env = Env(pol, Budget.named(budget), int(seed), data=data, provenance=prov,  # type: ignore[arg-type]
              out_dir=str(out / "evidence"))
    studies = {"G3": G3Study(), "G4": G4Study()}
    res: dict[str, list[Any]] = {}
    for g in groups:
        run = run_group(studies[g], mainnet(), env, workers=workers, explain=False)
        rows = []
        for r in run.recommendations:
            rows.append({"param": r.param, "current": r.current, "recommended": r.recommended,
                         "verdict": r.verdict, "binding": r.binding,
                         "metrics": {k: v for k, v in r.metrics.items() if isinstance(v, int | float | str)}})
        (out / f"{g}.json").write_text(json.dumps(rows, indent=1, default=str))
        cur = run.table.current()
        if cur is not None:
            (out / f"{g}-current-values.json").write_text(
                json.dumps({k: v for k, v in cur.metrics.values.items()}, indent=1, default=str))
        res[g] = rows
    return res


def claims_main(argv: Sequence[str]) -> int:
    """``claims``: claimant economics at real liquidity and the emergency path on the worst real falls."""
    from ybcal.config import Policy
    from ybcal.params.paramset import mainnet
    from ybcal.report.build import load_data
    from ybcal.studies import g3_collateral as G3
    from ybcal.studies.base import Budget, Env

    sp = argparse.ArgumentParser(prog="python -m ybcal.studies.g3_horizon claims")
    sp.add_argument("--hourly", required=True)
    sp.add_argument("--daily")
    sp.add_argument("--data", action="append", default=[])
    sp.add_argument("--policy")
    sp.add_argument("--budget", default="standard")
    sp.add_argument("--seed", type=int, default=1)
    sp.add_argument("--overlay")
    sp.add_argument("--out", required=True)
    b = sp.parse_args(list(argv))
    out = Path(b.out)
    out.mkdir(parents=True, exist_ok=True)
    pol = Policy.load(b.policy)
    data, prov, _ = load_data([Path(b.hourly), *[Path(x) for x in b.data]])
    if b.daily:
        data["price_daily"] = load_daily(b.daily)
    env = Env(pol, Budget.named(b.budget), b.seed, data=data, provenance=prov)  # type: ignore[arg-type]
    params = mainnet()
    if b.overlay:
        from ybcal.devnet.overlay import load_overlay

        params = load_overlay(b.overlay, params)
    write_csv(out / "claim_liquidity.csv", claim_liquidity_rows(env, params))
    hourly = G3._real_hourly(env)
    assert hourly is not None, "claims needs a real hourly price file"
    cr = real_crash_runs(params, hourly)
    rows = []
    for theta in (11_000, 12_000, 13_250, 15_000):
        ps = params.replace(claimThresholdBps=theta)
        if int(ps["emergencyRatioBps"]) >= theta:
            continue
        st = G3.emergency_stats(cr, ps, pol)
        for e, d in sorted(st.items()):
            for name in cr.names:
                rows.append({"theta_bps": theta, "emergency_bps": e, "scenario": name,
                             **{k.split(".", 1)[1]: v for k, v in d.items() if k.startswith(name + ".")}})
    write_csv(out / "emergency_real.csv", rows)
    (out / "crashes.json").write_text(json.dumps(getattr(cr, "info", {}), indent=1))
    print(f"wrote {out}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "claims":
        return claims_main(argv[1:])
    if argv and argv[0] == "study":
        sp = argparse.ArgumentParser(prog="python -m ybcal.studies.g3_horizon study",
                                     description="G3/G4 on hourly + daily real data (study API)")
        sp.add_argument("--hourly", required=True)
        sp.add_argument("--daily")
        sp.add_argument("--data", action="append", default=[], help="depth / pool-share / spreads CSVs")
        sp.add_argument("--policy")
        sp.add_argument("--budget", default="standard")
        sp.add_argument("--seed", type=int, default=1)
        sp.add_argument("--workers", type=int, default=2)
        sp.add_argument("--groups", default="G3,G4")
        sp.add_argument("--set", action="append", default=[], help="policy override KEY=JSON")
        sp.add_argument("--out", required=True)
        b = sp.parse_args(argv[1:])
        o = Path(b.out)
        o.mkdir(parents=True, exist_ok=True)
        ov = {k: json.loads(v) for k, v in (x.split("=", 1) for x in b.set)}
        res = run_studies(hourly=b.hourly, daily=b.daily, extra=b.data, policy_path=b.policy,
                          budget=b.budget, seed=b.seed, workers=b.workers, out=o,
                          groups=[g for g in b.groups.split(",") if g], overrides=ov)
        for g, rows in res.items():
            for r in rows:
                print(f"{g} {r['param']:22s} {r['verdict']:12s} {r['current']} -> {r['recommended']}")
        return 0
    return frontier_main(argv)


def frontier_main(argv: Sequence[str] | None = None) -> int:
    from ybcal.config import Policy
    from ybcal.params.paramset import mainnet

    ap = argparse.ArgumentParser(prog="python -m ybcal.studies.g3_horizon [frontier]",
                                 description="G3 long-horizon frontier on the daily YEC history")
    ap.add_argument("--daily", required=True, help="daily price CSV (yec-daily.csv)")
    ap.add_argument("--hourly", help="hourly price CSV (adds the pre-wave-2 hourly history member)")
    ap.add_argument("--policy", default=None)
    ap.add_argument("--seeds", default="1,2,3")
    ap.add_argument("--paths", type=int, default=100, help="paths per member and seed (standard: 100)")
    ap.add_argument("--years", type=float, default=6.0)
    ap.add_argument("--stride-hours", type=int, default=24)
    ap.add_argument("--windows", default=",".join(WINDOWS))
    ap.add_argument("--models", default=",".join(DAILY_MODELS))
    ap.add_argument("--overlay", help="recommended.json / params JSON to evaluate instead of the shipped set")
    ap.add_argument("--out", required=True)
    argv = list(argv or [])
    a = ap.parse_args(argv[1:] if argv and argv[0] == "frontier" else argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pol = Policy.load(a.policy)
    params = mainnet()
    if a.overlay:
        from ybcal.devnet.overlay import load_overlay

        params = load_overlay(a.overlay, params)
    tol = {c: float(pol.max_bad_debt(c)) for c in CLASS_NAMES}
    d = load_daily(a.daily)
    seeds = [int(s) for s in a.seeds.split(",") if s]
    hh = None
    if a.hourly:
        from ybcal.data import loaders as L

        ser = L.load_price_csv(a.hourly)
        rs_ = L.resample_to_grid(ser, "hour")
        x = np.asarray(rs_.path.prices, dtype=np.int64)[0].copy()
        good = x > 0
        idx = np.maximum.accumulate(np.where(good, np.arange(x.size), 0))
        hh = x[idx][None, :]
    print(f"daily {d.span} ({len(d)} closes); seeds {seeds}; paths {a.paths}; {a.years} y")
    windows = {w: WINDOWS[w] for w in a.windows.split(",") if w}
    fr = run_frontier(params, d, seeds=seeds, paths=a.paths, years=a.years, windows=windows,
                      models=[m for m in a.models.split(",") if m], stride_hours=a.stride_hours,
                      hourly_history=hh, term_distribution=str(pol.term_distribution))
    write_csv(out / "frontier.csv", frontier_rows(fr, tol))
    write_csv(out / "needed_by_term.csv", needed_rows(fr, tol))
    hist_terms = (30, 60, 90, 120, 180, 270, 365, 548, 730, 1095, 1460, 1825)
    write_csv(out / "history_replay.csv",
              history_table(params, d, (50_000, 77_500, 100_000, 200_000, 400_000, 700_000, 1_000_000),
                            terms_days=hist_terms))
    vol = {f"{w}/{m}": f.meta.get("vol") for (w, m), f in fr.items() if "#" not in m and f.meta.get("vol")}
    vol["data/daily"] = daily_vol_by_horizon(d)
    meta = {"daily": a.daily, "span": d.span, "seeds": seeds, "paths": a.paths, "years": a.years,
            "stride_hours": a.stride_hours, "windows": windows, "tolerance": tol,
            "term_distribution": str(pol.term_distribution), "vol_by_horizon": vol,
            "sigma_multiplier": "locked ratio = base × multiplier; frontier in locked ratio"}
    (out / "meta.json").write_text(json.dumps(meta, indent=2, default=str))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    # run the package module's main, not ``__main__``'s: a Daily pickled to the evaluation workers must
    # be ``ybcal.studies.g3_horizon.Daily`` (a ``__main__.Daily`` fails G3's isinstance check there)
    from ybcal.studies.g3_horizon import main as _main

    sys.exit(_main())

# Copyright (c) 2026 The Ycash developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or https://www.opensource.org/licenses/mit-license.php .
"""Zcash (ZEC) as the level-shift template for YEC (owner direction 2026-10-07).

YEC has never sustained a price above $1, so its own history cannot test whether percentage
volatility falls once price and market cap rise after a level shift. ZEC shares YEC's lineage and
mining base and has lived through a low-price regime (sub-$30 in 2023-24) and a run-up to four
figures (2025-26). Three parts, reported by :func:`main` into ``docs/reports/2026-10-zec/``:

1. **ZEC σ vs price level and vs market cap**, the method of :mod:`ybcal.studies.vol_price_level`
   (rolling 30/60/90-day annualised σ of log returns, elasticity ``d log σ / d log p`` and
   ``d log σ / d log mcap`` with moving-block-bootstrap CIs, deciles, date regimes), and the quantity
   bad debt depends on directly: the distribution of ``pMint / pClaim`` over 30/60/90-day terms
   (plus the 30-day grace) in each regime.

2. **Level-shift simulation for YEC**: ZEC's percentage path from the start of its trough is the
   template; YEC's last frozen price is mapped onto it, an ensemble is drawn by a within-regime
   moving-block bootstrap of ZEC's hourly returns, and class A P(bad debt) at 500/725/1,000 % is
   scored per regime under today's σ multiplier, a flat multiplier and price-level rules, with the
   G3 bad-debt definition (:func:`ybcal.studies.vol_price_level.backtest`).

3. **Forward-looking, horizon-aware volatility estimators** scored on ZEC and YEC by how well they
   forecast the next-term ``pMint / pClaim`` (coverage at 1/2/5 %) and by the collateral they lock:
   realised windows (42 h, 7/30/60/90 d), EWMA, GARCH(1,1) (scipy QML), a trailing p90 of realised σ.

Data: ``data/local/zec/`` (the one authorised pull, ``SHA256SUMS`` verified before reading) and the
frozen YEC snapshot ``data/local/frozen-20261004`` (nothing fetched by this module).

Run::

    PYTHONPATH=src .venv/bin/python -m ybcal.studies.zec_level_shift \
        --zec-dir data/local/zec --yec-dir data/local/frozen-20261004 --out docs/reports/2026-10-zec
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ybcal.studies import vol_price_level as VPL
from ybcal.studies.vol_price_level import (
    GRACE,
    SIGMA_CAP_H3,
    SIGMA_REF_H3,
    Series,
    backtest,
    clamp_mult,
    decile_table,
    fit_level,
    load,
    md_table,
    ols,
    rolling_mean,
    rolling_sigma,
    sigma_hat_bps_hourly,
    write_csv,
)
from ybcal.units import BLOCKS_PER_HOUR, BPS

C1, C2, C3, INK, MUTED = VPL.C1, VPL.C2, VPL.C3, VPL.INK, VPL.MUTED
C4, C5, C6 = "#8a4fd6", "#c9a400", "#d6366e"

#: ZEC date regimes (by the window's end / the mint hour). The launch weeks (a $2,000+ listing spike
#: collapsing to $50 inside a month) are a regime of their own so they never contaminate the fits.
REGIMES: tuple[tuple[str, str, str], ...] = (
    ("launch", "2016-10-28", "2017-03-31"),
    ("2017-18 boom/bust", "2017-04-01", "2018-12-31"),
    ("2019-20 low", "2019-01-01", "2020-12-31"),
    ("2021-22 pump/decline", "2021-01-01", "2022-12-31"),
    ("2023-24 trough", "2023-01-01", "2024-09-30"),
    ("2024-25 base", "2024-10-01", "2025-09-30"),
    ("run-up", "2025-10-01", "2026-01-31"),
    ("high", "2026-02-01", "2099-01-01"),
)
#: The three coarse regimes the owner asked for (low → run-up → high), by date.
COARSE = {
    "low": ("2023-01-01", "2025-09-30"),
    "run-up": ("2025-10-01", "2026-01-31"),
    "high": ("2026-02-01", "2099-01-01"),
}
PRICE_BANDS = ((0.0, 50.0, "< $50"), (50.0, 300.0, "$50-300"), (300.0, 1e9, "> $300"))
TERMS_D = (30, 60, 90)
GRACE_D = GRACE // (BLOCKS_PER_HOUR * 24)
BASES = (20_000, 25_000, 30_000, 35_000, 50_000, 72_500, 100_000)
TOLS = (0.01, 0.02, 0.05)
TEMPLATE_T0 = "2023-01-01"
ENSEMBLE_SEEDS = 24
BLOCK_H = 24 * 7


def _ts(s: str) -> int:
    return int(dt.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=dt.UTC).timestamp())


# ---------------------------------------------------------------------------------------------------
# data


def check_sums(data_dir: Path, names: tuple[str, ...]) -> dict[str, bool]:
    want = {}
    for line in (data_dir / "SHA256SUMS").read_text().splitlines():
        h, _, name = line.partition("  ")
        want[name.strip()] = h.strip()
    return {n: hashlib.sha256((data_dir / n).read_bytes()).hexdigest() == want.get(n) for n in names}


def load_mcap(path: Path, grid: np.ndarray) -> np.ndarray:
    """``ts_iso,ts,market_cap_usd`` forward-filled onto ``grid`` (nan before the first point)."""
    import csv

    ts, mc = [], []
    with path.open() as f:
        for row in csv.DictReader(f):
            ts.append(int(row["ts"]))
            mc.append(float(row["market_cap_usd"]))
    ts_a, mc_a = np.array(ts, dtype=np.int64), np.array(mc)
    o = np.argsort(ts_a, kind="stable")
    ts_a, mc_a = ts_a[o], mc_a[o]
    idx = np.searchsorted(ts_a, grid, side="right") - 1
    out = np.where(idx >= 0, mc_a[np.maximum(idx, 0)], np.nan)
    return out


def regime_of(ts: np.ndarray, regimes=REGIMES) -> np.ndarray:
    lab = np.array(["" for _ in ts], dtype=object)
    for name, a, b in regimes:
        lab[(ts >= _ts(a)) & (ts < _ts(b) + 86_400)] = name
    return lab


def coarse_of(ts: np.ndarray) -> np.ndarray:
    lab = np.array(["" for _ in ts], dtype=object)
    for name, (a, b) in COARSE.items():
        lab[(ts >= _ts(a)) & (ts < _ts(b) + 86_400)] = name
    return lab


# ---------------------------------------------------------------------------------------------------
# part 1


def fit_mask(s: Series, w_days: int, mask: np.ndarray, label: str, source: str, reps: int, x_override=None):
    """:func:`fit_level` restricted to an arbitrary boolean mask (regimes), optionally regressing
    on another level series (market cap)."""
    per_day = 86_400 // s.step
    w = w_days * per_day
    ppy = 365 * per_day
    logp = np.log(s.price)
    sig = rolling_sigma(logp, w, ppy)
    lvl = np.exp(rolling_mean(logp, w)) if x_override is None else np.exp(rolling_mean(np.log(x_override), w))
    m = ~np.isnan(sig) & (sig > 0) & ~np.isnan(lvl) & mask
    if m.sum() < 2 * w:
        return None
    y, x = np.log(sig[m]), np.log(lvl[m])
    t = (s.ts[m] - s.ts[m][0]) / (365.25 * 86_400)
    a, b, r2 = ols(y, x)
    _, b_t, _ = ols(y, x, t)
    lo, hi = VPL.block_bootstrap_slope(y, x, w, reps)
    return VPL.LevelFit(
        label,
        source,
        w_days,
        int(m.sum()),
        VPL.spearman(x, y),
        VPL.pearson(x, y),
        b,
        lo,
        hi,
        r2,
        a,
        b_t,
        float(np.median(sig[m])),
        float(np.median(lvl[m])),
    )


def need_series(price: np.ndarray, h: int) -> np.ndarray:
    """``p_s / p_{s+h}`` per start (nan where censored)."""
    out = np.full(price.size, np.nan)
    if h < price.size:
        out[:-h] = price[:-h] / price[h:]
    return out


def worst_in_term(price: np.ndarray, h: int) -> np.ndarray:
    """``p_s / min(p_{s+1..s+h})``: the worst mark-to-market inside the term."""
    from scipy.ndimage import minimum_filter1d

    out = np.full(price.size, np.nan)
    if h < price.size:
        mn = minimum_filter1d(price, size=h, mode="nearest", origin=-(h // 2))
        # minimum over [i, i+h-1]; shift by one so the window is (s, s+h]
        out[:-h] = price[:-h] / mn[1 : price.size - h + 1]
    return out


def drawdown_rows(s: Series, labels: np.ndarray, who: str) -> list[dict]:
    rows = []
    per_day = 86_400 // s.step
    for name in [*dict.fromkeys(labels[labels != ""]), "all"]:
        m = np.ones(s.ts.size, bool) if name == "all" else labels == name
        for h_d in TERMS_D:
            for kind, h in (
                ("claim-open (T+grace)", (h_d + GRACE_D) * per_day),
                ("term end (T)", h_d * per_day),
            ):
                nd = need_series(s.price, h)
                sel = m & ~np.isnan(nd)
                if sel.sum() < 10:
                    continue
                v = nd[sel]
                rows.append(
                    {
                        "series": who,
                        "regime": name,
                        "term_days": h_d,
                        "horizon": kind,
                        "n": int(sel.sum()),
                        "p50": float(np.median(v)),
                        "p90": float(np.percentile(v, 90)),
                        "p99": float(np.percentile(v, 99)),
                        "max": float(v.max()),
                        "frac_gt_5x": float((v > 5).mean()),
                        "frac_gt_7.25x": float((v > 7.25).mean()),
                        "frac_gt_10x": float((v > 10).mean()),
                        "price_median": float(np.median(s.price[sel])),
                    }
                )
    return rows


# ---------------------------------------------------------------------------------------------------
# part 2: level-shift simulation


def block_bootstrap_path(logp: np.ndarray, seg_bounds: list[tuple[int, int]], block: int, rng) -> np.ndarray:
    """Resample hourly log returns within each segment ``[a, b)`` by moving blocks, keeping the
    segment lengths; the path starts at ``logp[0]``."""
    r = np.diff(logp)
    out = [logp[0]]
    pieces = []
    for a, b in seg_bounds:
        seg = r[max(a - 1, 0) : b - 1] if a > 0 else r[: b - 1]
        n = b - a if a > 0 else b - 1
        if seg.size <= block:
            pieces.append(seg[:n])
            continue
        starts = rng.integers(0, seg.size - block + 1, size=-(-n // block))
        idx = (starts[:, None] + np.arange(block)[None, :]).ravel()[:n]
        pieces.append(seg[idx])
    rr = np.concatenate(pieces)
    return np.concatenate([[out[0]], out[0] + np.cumsum(rr)])


@dataclass
class Rules:
    names: list[str]
    mult: dict[str, np.ndarray]


def level_rules(
    p_usd: np.ndarray, sig_hat: np.ndarray, p0: float, sig0_bps: float, b_power: float, b_fit: float
) -> Rules:
    """The multiplier series of every rule on one path (bps, per hour). Level rules are anchored
    at ``(p0, sig0)``: at the anchor price they demand the anchor σ̂, then follow the level."""
    cur = clamp_mult(sig_hat, SIGMA_REF_H3, SIGMA_CAP_H3)
    flat = np.full(p_usd.size, BPS, dtype=np.int64)
    pw = np.rint(sig0_bps * np.power(p_usd / p0, -b_power)).astype(np.int64)
    pf = np.rint(sig0_bps * np.power(p_usd / p0, -b_fit)).astype(np.int64)
    lg = np.rint(sig0_bps * math.log1p(p0) / np.log1p(p_usd)).astype(np.int64)
    mult = {
        "current (σ̂ 42 h / σref)": cur,
        "flat 1×": flat,
        f"level power b={b_power:g} (hypothesis)": clamp_mult(pw, SIGMA_REF_H3, SIGMA_CAP_H3),
        f"level power b={b_fit:+.2f} (ZEC fit)": clamp_mult(pf, SIGMA_REF_H3, SIGMA_CAP_H3),
        "level log(p+1)": clamp_mult(lg, SIGMA_REF_H3, SIGMA_CAP_H3),
        f"max(current, power b={b_power:g})": clamp_mult(
            np.where(sig_hat < 0, pw, np.maximum(sig_hat, pw)), SIGMA_REF_H3, SIGMA_CAP_H3
        ),
        "max(current, log)": clamp_mult(
            np.where(sig_hat < 0, lg, np.maximum(sig_hat, lg)), SIGMA_REF_H3, SIGMA_CAP_H3
        ),
    }
    return Rules(list(mult), mult)


def score_path(
    p_usd: np.ndarray,
    labels: np.ndarray,
    regimes: tuple[str, ...],
    p0: float,
    sig0_bps: float,
    b_power: float,
    b_fit: float,
    label: str,
) -> tuple[list[dict], list[dict]]:
    """Class A P(bad) and mean effective ratio per (regime, rule, base) on one hourly path, the
    fixed-term 30/60/90-day P(bad), and the smallest lattice base meeting each tolerance per
    (regime, rule) with the mean lock it implies."""
    p_micro = np.rint(p_usd * 1_000_000).astype(np.int64)
    sig_hat = sigma_hat_bps_hourly(p_micro)
    rules = level_rules(p_usd, sig_hat, p0, sig0_bps, b_power, b_fit)
    bt = backtest(p_micro, "A")
    starts = bt.starts
    lab_s = labels[starts]
    # fixed terms (claim-open need) for the per-term view
    need_T = {h: need_series(p_usd, (h + GRACE_D) * 24)[starts] * BPS for h in TERMS_D}
    rows, req_rows = [], []
    for reg in (*regimes, "all"):
        m = np.ones(starts.size, bool) if reg == "all" else lab_s == reg
        if m.sum() < 100:
            continue
        ok_any = m & ~np.isnan(bt.need).all(axis=1)
        nd = bt.need[ok_any]
        nd = nd[~np.isnan(nd)]
        for name in rules.names:
            ms = rules.mult[name][starts]
            for tol in TOLS:
                base, pb = VPL.required_base(bt, ms, tol, m)
                eff = (base * ms // BPS).astype(np.float64)[ok_any] if base > 0 else np.array([np.nan])
                req_rows.append(
                    {
                        "path": label,
                        "regime": reg,
                        "rule": name,
                        "tolerance": tol,
                        "required_base_bps": base,
                        "p_bad_at_required": pb,
                        "eff_mean_bps": float(eff.mean()),
                        "eff_p90_bps": float(np.percentile(eff, 90)),
                        "mult_mean": float(ms[ok_any].mean() / BPS) if ok_any.any() else math.nan,
                        "need_p99_bps": float(np.percentile(nd, 99)) if nd.size else math.nan,
                        "need_max_bps": float(nd.max()) if nd.size else math.nan,
                    }
                )
            for base in BASES:
                eff = (base * ms // BPS).astype(np.int64)
                row = {
                    "path": label,
                    "regime": reg,
                    "rule": name,
                    "base_bps": base,
                    "p_bad_classA": bt.p_bad(eff, m),
                    "eff_mean_bps": float(eff[ok_any].mean()) if ok_any.any() else math.nan,
                    "mult_mean": float(ms[ok_any].mean() / BPS) if ok_any.any() else math.nan,
                    "need_p99_bps": float(np.percentile(nd, 99)) if nd.size else math.nan,
                    "need_max_bps": float(nd.max()) if nd.size else math.nan,
                    "n_starts": int(m.sum()),
                }
                for h in TERMS_D:
                    v = need_T[h]
                    okh = m & ~np.isnan(v)
                    row[f"p_bad_{h}d"] = float((eff[okh] < v[okh]).mean()) if okh.any() else math.nan
                rows.append(row)
    return rows, req_rows


# ---------------------------------------------------------------------------------------------------
# part 3: horizon-aware estimators


def ewma_sigma(r: np.ndarray, half_life_h: float, ppy: float) -> np.ndarray:
    """EWMA σ (annualised) of hourly log returns, value at index i uses returns up to i."""
    lam = 0.5 ** (1.0 / half_life_h)
    v = np.empty(r.size)
    acc = float(np.mean(r[: int(max(24, half_life_h))] ** 2))
    for i in range(r.size):
        acc = lam * acc + (1 - lam) * r[i] * r[i]
        v[i] = acc
    out = np.full(r.size + 1, np.nan)
    out[1:] = np.sqrt(v * ppy)
    return out


def garch11_fit(r: np.ndarray) -> tuple[float, float, float, dict]:
    """GARCH(1,1) on daily returns by Gaussian QML with **variance targeting** (``omega = var ·
    (1 − α − β)``) and α ≤ 0.8, β ≥ 0.1 (scipy ``minimize`` from a coarse grid). Without the
    constraints the unconstrained QML on both ZEC and YEC runs to the IGARCH boundary (α → 1, β → 0,
    ω → 0: tomorrow's variance = today's squared return), which is a better Gaussian fit to
    heavy-tailed daily returns but useless as a term forecast; that value is reported too."""
    from scipy.optimize import minimize

    r = r - r.mean()
    var0 = float(np.var(r))

    def nll(alpha, beta, omega=None):
        if alpha + beta >= 0.999 or alpha <= 0 or beta <= 0:
            return 1e12
        om = var0 * (1 - alpha - beta) if omega is None else omega
        h = var0
        s = 0.0
        for x in r:
            h = om + alpha * x * x + beta * h
            s += math.log(h) + x * x / h
        return 0.5 * s

    best = None
    for al in np.linspace(0.02, 0.78, 20):
        for be in np.linspace(0.12, 0.96, 22):
            v = nll(al, be)
            if best is None or v < best[0]:
                best = (v, al, be)
    lo = np.array([0.01, 0.1])
    hi = np.array([0.8, 0.98])

    def obj(th):
        a_, b_ = lo + (hi - lo) / (1 + np.exp(-th))
        return nll(float(a_), float(b_))

    th0 = np.log((np.array(best[1:]) - lo) / (hi - np.array(best[1:])))
    res = minimize(obj, th0, method="Nelder-Mead", options={"maxiter": 600, "xatol": 1e-5, "fatol": 1e-7})
    alpha, beta = (lo + (hi - lo) / (1 + np.exp(-res.x))).tolist()
    omega = var0 * (1 - alpha - beta)
    return (
        omega,
        alpha,
        beta,
        {
            "converged": bool(res.success),
            "nll": float(res.fun),
            "n": int(r.size),
            "nll_unconstrained_igarch_boundary": float(nll(0.998, 1e-6, 1e-15)),
            "daily_var": var0,
        },
    )


def garch11_term_sigma(
    r_daily: np.ndarray, omega: float, alpha: float, beta: float, horizons_d: tuple[int, ...]
) -> dict[int, np.ndarray]:
    """Conditional variance recursion over the whole daily series (parameters fixed), then the
    annualised term σ = sqrt(mean of the k-step-ahead variances over the horizon)."""
    r = r_daily - r_daily.mean()
    n = r.size
    h = np.empty(n + 1)
    h[0] = omega / max(1 - alpha - beta, 1e-6)
    for i in range(n):
        h[i + 1] = omega + alpha * r[i] * r[i] + beta * h[i]
    lr = omega / max(1 - alpha - beta, 1e-6)
    phi = alpha + beta
    out = {}
    for H in horizons_d:
        k = np.arange(1, H + 1)
        # h_{t+k} = lr + phi^(k-1) (h_{t+1} - lr); mean over k
        w = np.mean(phi ** (k - 1))
        term_var = lr + w * (h[1:] - lr)
        out[H] = np.sqrt(np.maximum(term_var, 0) * 365)
    return out


def _causal_rank_state(x: np.ndarray, window: int, step: int = 24) -> np.ndarray:
    """Tercile (0/1/2) of ``x[i]`` within its own trailing ``window`` values (causal), evaluated
    every ``step`` hours and held; -1 where undefined."""
    out = np.full(x.size, -1, dtype=np.int8)
    for i in range(window, x.size, step):
        seg = x[i - window : i]
        seg = seg[~np.isnan(seg)]
        if seg.size < window // 4 or np.isnan(x[i]):
            continue
        r = (seg < x[i]).mean()
        out[i : i + step] = 0 if r < 1 / 3 else (1 if r < 2 / 3 else 2)
    return out


def _band_state(x: np.ndarray, edges: tuple[float, float]) -> np.ndarray:
    out = np.full(x.size, -1, dtype=np.int8)
    ok = ~np.isnan(x)
    out[ok] = np.where(x[ok] < edges[0], 0, np.where(x[ok] < edges[1], 1, 2))
    return out


def trailing_quantiles(
    need: np.ndarray,
    H: int,
    q: float,
    window: int | None,
    state: np.ndarray | None,
    skip: int,
    step: int = 24,
    min_n: int = 200,
) -> tuple[np.ndarray, np.ndarray]:
    """Causal (VaR, CVaR) of the realised ``need[s] = p_s / p_{s+H}``: at hour ``t`` only needs
    with ``s + H <= t`` are known; ``window`` limits ``s`` to the last ``window`` hours (None =
    expanding); ``state`` restricts to past starts in ``t``'s state (falls back to all when fewer than
    ``min_n``). Evaluated every ``step`` hours and held."""
    n = need.size
    var = np.full(n, np.nan)
    cvar = np.full(n, np.nan)
    for t in range(skip, n, step):
        hi = t - H
        lo = max(0, hi - window) if window is not None else 0
        if hi - lo < min_n:
            continue
        seg = need[lo:hi]
        if state is not None and state[t] >= 0:
            sub = seg[state[lo:hi] == state[t]]
            sub = sub[~np.isnan(sub)]
            if sub.size >= min_n:
                seg = sub
        seg = seg[~np.isnan(seg)]
        if seg.size < min_n:
            continue
        v = np.percentile(seg, q * 100)
        tail = seg[seg >= v]
        var[t : t + step] = v
        cvar[t : t + step] = tail.mean() if tail.size else v
    return var, cvar


def estimators(s: Series, garch_fit_frac: float = 0.4, skip: int = 24 * 400) -> tuple[dict, dict]:
    """Candidate sizing signals on the hourly series: ``name -> {"kind": "sigma" | "lock",
    "h": {horizon_d -> series}}``. ``sigma`` candidates are annualised σ̂ (sized through the SIGMA-1
    form or proportionally); ``lock`` candidates are already a ratio (a quantile of the realised
    ``pMint/pClaim``) and are sized by a scalar."""
    logp = np.log(s.price)
    r = np.diff(logp)
    ppy = 8_760
    base: dict[str, np.ndarray] = {}
    base["realised 42 h (today)"] = rolling_sigma(logp, 42, ppy)
    base["realised 7 d"] = rolling_sigma(logp, 24 * 7, ppy)
    base["realised 30 d"] = rolling_sigma(logp, 24 * 30, ppy)
    base["realised 60 d"] = rolling_sigma(logp, 24 * 60, ppy)
    base["realised 90 d"] = rolling_sigma(logp, 24 * 90, ppy)
    base["EWMA half-life 1 d"] = ewma_sigma(r, 24, ppy)
    base["EWMA half-life 7 d"] = ewma_sigma(r, 24 * 7, ppy)
    base["EWMA half-life 30 d"] = ewma_sigma(r, 24 * 30, ppy)
    rv30 = base["realised 30 d"]
    p90 = np.full(rv30.size, np.nan)
    w = 24 * 365
    for i in range(w, rv30.size, 24):
        seg = rv30[i - w : i]
        seg = seg[~np.isnan(seg)]
        if seg.size:
            p90[i : i + 24] = np.percentile(seg, 90)
    base["trailing-year p90 of realised 30 d"] = p90
    base["flat (constant)"] = np.ones(rv30.size)
    out: dict = {k: {"kind": "sigma", "h": {h: v for h in TERMS_D}} for k, v in base.items()}
    # GARCH(1,1) on daily closes, fitted on the first ``garch_fit_frac`` of days
    day_idx = np.arange(0, s.ts.size, 24)
    rd = np.diff(logp[day_idx])
    nfit = int(rd.size * garch_fit_frac)
    om, al, be, info = garch11_fit(rd[:nfit])
    term = garch11_term_sigma(rd, om, al, be, tuple(h + GRACE_D for h in TERMS_D))
    g = {}
    for h in TERMS_D:
        arr = np.full(s.ts.size, np.nan)
        vals = term[h + GRACE_D]
        for d in range(vals.size):
            a = day_idx[d + 1] if d + 1 < day_idx.size else s.ts.size
            b = day_idx[d + 2] if d + 2 < day_idx.size else s.ts.size
            arr[a:b] = vals[d]
        g[h] = arr
    out["GARCH(1,1) term forecast"] = {"kind": "sigma", "h": g}
    info.update({"omega": om, "alpha": al, "beta": be, "persistence": al + be, "fit_days": nfit})

    # ---- drawdown-based and state-conditioned candidates (direct lock quantiles) ----------------
    states = {
        "price level (trailing 2 y tercile)": _causal_rank_state(s.price, 24 * 730),
        "90-day momentum (<0.8, 0.8-1.25, >1.25)": _band_state(
            np.concatenate([np.full(24 * 90, np.nan), s.price[24 * 90 :] / s.price[: -24 * 90]]), (0.8, 1.25)
        ),
        "realised σ30 (trailing 2 y tercile)": _causal_rank_state(rv30, 24 * 730),
    }
    vol_ok = ~np.isnan(s.volume) & (s.volume > 0)
    if vol_ok.mean() > 0.5:
        turnover = rolling_mean(np.where(vol_ok, s.volume, np.nan), 24 * 30) / rolling_mean(s.price, 24 * 30)
        states["turnover: 30 d volume / price (trailing 2 y tercile)"] = _causal_rank_state(
            turnover, 24 * 730
        )
    for h in TERMS_D:
        H = (h + GRACE_D) * 24
        need = need_series(s.price, H)
        for q, qlab in ((0.99, "p99"), (0.95, "p95")):
            for window, wlab in ((None, "expanding"), (24 * 365 * 2, "2 y"), (24 * 365, "1 y")):
                var, cvar = trailing_quantiles(need, H, q, window, None, skip)
                out.setdefault(f"historical VaR {qlab} of pMint/pClaim ({wlab})", {"kind": "lock", "h": {}})[
                    "h"
                ][h] = var
                out.setdefault(f"historical CVaR {qlab} of pMint/pClaim ({wlab})", {"kind": "lock", "h": {}})[
                    "h"
                ][h] = cvar
        for sname, st in states.items():
            var, cvar = trailing_quantiles(need, H, 0.99, None, st, skip)
            out.setdefault(f"conditional VaR p99 | {sname}", {"kind": "lock", "h": {}})["h"][h] = var
            out.setdefault(f"conditional CVaR p99 | {sname}", {"kind": "lock", "h": {}})["h"][h] = cvar
    return out, info


def score_estimators(s: Series, est: dict, who: str, skip_h: int = 24 * 400) -> list[dict]:
    """Size each candidate to each tolerance (scalar ``k`` on a geometric lattice) over the full
    sample and over the first half; report the mean lock and the second-half coverage."""
    rows = []
    n = s.ts.size
    starts = np.arange(skip_h, n)
    half = starts[starts.size // 2]
    ks = np.exp(np.linspace(math.log(0.2), math.log(60), 500))
    for h in TERMS_D:
        need = need_series(s.price, (h + GRACE_D) * 24)[starts]
        ok = ~np.isnan(need)
        first, second = ok & (starts < half), ok & (starts >= half)
        for name, spec in est.items():
            if h not in spec["h"]:
                continue
            sig = spec["h"][h][starts]
            okn = ok & ~np.isnan(sig)
            if okn.sum() < 1000:
                continue
            forms = ("sigma1", "prop") if spec["kind"] == "sigma" else ("direct",)
            for form in forms:
                if form == "sigma1":
                    sig_bps = np.where(np.isnan(sig), -1, np.rint(sig * BPS)).astype(np.int64)
                    mult = clamp_mult(sig_bps, SIGMA_REF_H3, SIGMA_CAP_H3).astype(np.float64) / BPS
                    if name == "flat (constant)":
                        mult = np.ones(sig.size)
                elif form == "prop":
                    tau = (h + GRACE_D) / 365
                    mult = np.where(np.isnan(sig), np.nan, sig * math.sqrt(tau))
                    if name == "flat (constant)":
                        mult = np.ones(sig.size)
                else:
                    mult = sig
                for tol in TOLS:
                    for fit_mask_, test_mask, tag in (
                        (okn, okn, "in-sample"),
                        (okn & first, okn & second, "first→second half"),
                    ):
                        if fit_mask_.sum() < 500 or test_mask.sum() < 500:
                            continue
                        lock_fit = mult[fit_mask_][None, :] * ks[:, None]
                        exc = (lock_fit < need[fit_mask_][None, :]).mean(axis=1)
                        if not (exc <= tol).any():
                            continue
                        j = int(np.argmax(exc <= tol))
                        k = ks[j]
                        lock_test = mult[test_mask] * k
                        rows.append(
                            {
                                "series": who,
                                "term_days": h,
                                "estimator": name,
                                "kind": spec["kind"],
                                "form": form,
                                "tolerance": tol,
                                "eval": tag,
                                "k": float(k),
                                "lock_mean": float(lock_test.mean()),
                                "lock_median": float(np.median(lock_test)),
                                "lock_p90": float(np.percentile(lock_test, 90)),
                                "exceed_fit": float(exc[j]),
                                "exceed_test": float((lock_test < need[test_mask]).mean()),
                                "n_test": int(test_mask.sum()),
                            }
                        )
    return rows


THETAS = (1.0, 1.10, 1.25, 1.50)
DELAYS_H = (24, 72, 168)
R_LATTICE = np.arange(1.0, 8.01, 0.25)


def lever_study(s: Series, who: str, skip: int = 24 * 400, stride: int = 6) -> list[dict]:
    """Management levers as risk controls: a vault becomes claimable when its collateral falls
    below ``θ × debt`` (claim threshold) and the claim completes ``d`` hours later (the cancel /
    arming delay); it is bad iff still under 100 % at completion (or, with no crossing, at claim
    opening ``T + grace`` as today). ``θ = 1.0`` is today's rule (no in-term claims). Reports the
    smallest flat ratio meeting each tolerance per (term, θ, d)."""
    rows = []
    n = s.ts.size
    for h in TERMS_D:
        H = (h + GRACE_D) * 24
        starts = np.arange(skip, n - H, stride)
        idx = starts[:, None] + np.arange(1, H + 1)[None, :]
        X = (s.price[starts][:, None] / s.price[idx]).astype(np.float32)  # need path
        X_end = X[:, -1]
        for theta in THETAS:
            for d in DELAYS_H if theta > 1.0 else (0,):
                res = {}
                for R in R_LATTICE:
                    if theta == 1.0:
                        bad = X_end > R
                    else:
                        cross = (R / theta) < X
                        has = cross.any(axis=1)
                        u = np.argmax(cross, axis=1)  # first crossing (0-based → hour u+1)
                        at = np.minimum(u + d, H - 1)
                        x_liq = X[np.arange(X.shape[0]), at]
                        bad = np.where(has, x_liq > R, X_end > R)
                    res[float(R)] = float(bad.mean())
                for tol in TOLS:
                    Rs = [R for R, pb in res.items() if pb <= tol]
                    rows.append(
                        {
                            "series": who,
                            "term_days": h,
                            "theta": theta,
                            "delay_h": d,
                            "tolerance": tol,
                            "required_flat_ratio": min(Rs) if Rs else math.nan,
                            "p_bad_at_725": res[7.25],
                            "p_bad_at_500": res[5.0],
                            "p_bad_at_300": res[3.0],
                            "p_bad_at_200": res[2.0],
                            "n_starts": int(starts.size),
                        }
                    )
    return rows


# ---------------------------------------------------------------------------------------------------
# main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--zec-dir", type=Path, required=True)
    ap.add_argument("--yec-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--reps", type=int, default=300)
    ap.add_argument("--seeds", type=int, default=ENSEMBLE_SEEDS)
    ap.add_argument("--no-figures", action="store_true")
    args = ap.parse_args(argv)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    zec_files = ("zec-daily-cmc.csv", "zec-hourly-cmc.csv", "zec-daily-cmc-mcap.csv", "zec-hourly-cg-90d.csv")
    sums = check_sums(args.zec_dir, zec_files)
    ysums = VPL.check_sums(args.yec_dir)
    if not all(sums.values()) or not all(ysums.values()):
        raise SystemExit(f"SHA256 mismatch: zec {sums} yec {ysums}")
    zd = load(args.zec_dir / "zec-daily-cmc.csv", 86_400)
    zh = load(args.zec_dir / "zec-hourly-cmc.csv", 3_600)
    cg90 = load(args.zec_dir / "zec-hourly-cg-90d.csv", 3_600)
    yd = load(args.yec_dir / "yec-daily.csv", 86_400)
    yh = load(args.yec_dir / "yec-hourly.csv", 3_600)
    mcap_d = load_mcap(args.zec_dir / "zec-daily-cmc-mcap.csv", zd.ts)
    summary: dict = {
        "checksums": {"zec": sums, "yec": ysums},
        "zec_daily": {"rows": int(zd.ts.size), "first": zd.iso(0), "last": zd.iso(-1)},
        "zec_hourly": {"rows": int(zh.ts.size), "first": zh.iso(0), "last": zh.iso(-1)},
        "zec_hourly_cg90": {"rows": int(cg90.ts.size), "first": cg90.iso(0), "last": cg90.iso(-1)},
        "yec_hourly": {"rows": int(yh.ts.size), "first": yh.iso(0), "last": yh.iso(-1)},
    }
    # CMC vs CoinGecko overlap on the last 90 days (a sanity check of the long series)
    common = np.intersect1d(zh.ts, cg90.ts)
    a = zh.price[np.searchsorted(zh.ts, common)]
    b = cg90.price[np.searchsorted(cg90.ts, common)]
    summary["cmc_vs_cg_90d"] = {
        "n": int(common.size),
        "median_abs_rel_diff": float(np.median(np.abs(a / b - 1))),
        "return_corr": float(np.corrcoef(np.diff(np.log(a)), np.diff(np.log(b)))[0, 1]),
    }
    # flat (stale) runs in the hourly CMC series
    fl = VPL.rolling_mean((np.diff(zh.price) == 0).astype(float), 24)
    summary["zec_hourly_flat_frac"] = float(np.nanmean(fl))

    # regime table: price range, median mcap
    reg_d = regime_of(zd.ts)
    reg_rows = []
    for name, *_ in REGIMES:
        m = reg_d == name
        if not m.any():
            continue
        mc = mcap_d[m]
        mc = mc[~np.isnan(mc)]
        reg_rows.append(
            {
                "regime": name,
                "from": zd.iso(int(np.argmax(m))),
                "to": zd.iso(int(zd.ts.size - 1 - np.argmax(m[::-1]))),
                "days": int(m.sum()),
                "price_min": float(zd.price[m].min()),
                "price_median": float(np.median(zd.price[m])),
                "price_max": float(zd.price[m].max()),
                "mcap_median_musd": float(np.median(mc) / 1e6) if mc.size else math.nan,
            }
        )
    write_csv(out / "regimes.csv", reg_rows)

    # ---- part 1: fits -------------------------------------------------------------------------
    fits: list[VPL.LevelFit] = []
    reg_h = regime_of(zh.ts)
    for src, s, labels in (("zec-daily", zd, reg_d), ("zec-hourly", zh, reg_h)):
        for w in VPL.WINDOWS_D:
            for lab, mask in [("all", np.ones(s.ts.size, bool)), ("ex-launch", labels != "launch")] + [
                (name, labels == name) for name, *_ in REGIMES
            ]:
                reps = args.reps if lab in ("all", "ex-launch") else max(60, args.reps // 3)
                f = fit_mask(s, w, mask, lab, src, reps)
                if f:
                    fits.append(f)
        # price bands
        for lo, hi, lab in PRICE_BANDS:
            per_day = 86_400 // s.step
            lvl = np.exp(rolling_mean(np.log(s.price), 30 * per_day))
            m = (lvl >= lo) & (lvl < hi) & (labels != "launch")
            f = fit_mask(s, 30, m, f"band {lab}", src, max(60, args.reps // 3))
            if f:
                fits.append(f)
    # market cap (daily)
    mc_ok = ~np.isnan(mcap_d) & (reg_d != "launch")
    for w in VPL.WINDOWS_D:
        f = fit_mask(
            zd,
            w,
            mc_ok,
            "ex-launch",
            "zec-daily-mcap",
            args.reps,
            x_override=np.where(np.isnan(mcap_d), zd.price, mcap_d),
        )
        if f:
            fits.append(f)
    for name, *_ in REGIMES[1:]:
        f = fit_mask(
            zd,
            30,
            mc_ok & (reg_d == name),
            name,
            "zec-daily-mcap",
            max(60, args.reps // 3),
            x_override=np.where(np.isnan(mcap_d), zd.price, mcap_d),
        )
        if f:
            fits.append(f)
    # YEC for side-by-side
    for w in VPL.WINDOWS_D:
        f, _, _ = fit_level(yd, w, "all", "yec-daily", None, args.reps)
        if f:
            fits.append(f)
    fit_rows = [f.__dict__ for f in fits]
    write_csv(out / "level_fits.csv", fit_rows)

    # deciles (ZEC daily ex-launch, by price and by mcap)
    sig30 = rolling_sigma(np.log(zd.price), 30, 365)
    lvl30 = np.exp(rolling_mean(np.log(zd.price), 30))
    exl = reg_d != "launch"
    dec_price = decile_table(np.where(exl, sig30, np.nan), lvl30)
    mc30 = np.exp(rolling_mean(np.log(np.where(np.isnan(mcap_d), np.nan, mcap_d)), 30))
    dec_mcap = decile_table(np.where(exl, sig30, np.nan), mc30)
    write_csv(out / "deciles_price_daily_30d.csv", dec_price)
    write_csv(out / "deciles_mcap_daily_30d.csv", dec_mcap)

    # regime σ summary (daily 30 d and hourly 30 d median, p90)
    sig30h = rolling_sigma(np.log(zh.price), 24 * 30, 8_760)
    sig_rows = []
    for name, *_ in REGIMES:
        md, mh = (reg_d == name) & ~np.isnan(sig30), (reg_h == name) & ~np.isnan(sig30h)
        sig_rows.append(
            {
                "regime": name,
                "sigma30d_daily_median": float(np.median(sig30[md])) if md.any() else math.nan,
                "sigma30d_daily_p90": float(np.percentile(sig30[md], 90)) if md.any() else math.nan,
                "sigma30d_hourly_median": float(np.median(sig30h[mh])) if mh.any() else math.nan,
                "sigma30d_hourly_p90": float(np.percentile(sig30h[mh], 90)) if mh.any() else math.nan,
                "price_median": float(np.median(zd.price[reg_d == name])),
            }
        )
    ysig30 = rolling_sigma(np.log(yd.price), 30, 365)
    sig_rows.append(
        {
            "regime": "YEC all (2019-26)",
            "sigma30d_daily_median": float(np.nanmedian(ysig30)),
            "sigma30d_daily_p90": float(np.nanpercentile(ysig30, 90)),
            "sigma30d_hourly_median": float(np.nanmedian(rolling_sigma(np.log(yh.price), 24 * 30, 8_760))),
            "sigma30d_hourly_p90": float(
                np.nanpercentile(rolling_sigma(np.log(yh.price), 24 * 30, 8_760), 90)
            ),
            "price_median": float(np.median(yd.price)),
        }
    )
    write_csv(out / "regime_sigma.csv", sig_rows)

    # drawdowns (daily series; ZEC per regime, YEC all)
    dd_rows = drawdown_rows(zd, reg_d, "ZEC") + drawdown_rows(
        yd, np.array([""] * yd.ts.size, dtype=object), "YEC"
    )
    write_csv(out / "drawdowns.csv", dd_rows)

    # ---- part 2: level shift ---------------------------------------------------------------------
    t0 = int(np.searchsorted(zh.ts, _ts(TEMPLATE_T0)))
    tpl_ts = zh.ts[t0:]
    tpl_log = np.log(zh.price[t0:])
    y_now = float(yh.price[-1])
    scale = math.log(y_now) - tpl_log[0]
    implied = np.exp(tpl_log + scale)
    # b for the power rule: the ZEC ex-launch daily 30 d elasticity, used in the hypothesised
    # direction (σ ∝ p^(−b), b > 0) when the fit is negative; else the owner's b = 0.5 and the fitted
    # sign is reported
    f_ex = next(f for f in fits if f.source == "zec-daily" and f.window_days == 30 and f.label == "ex-launch")
    b_fit = -f_ex.elasticity
    b_power = b_fit if b_fit > 0 else 0.5
    summary["template"] = {
        "t0": TEMPLATE_T0,
        "zec_p0": float(np.exp(tpl_log[0])),
        "zec_p_end": float(np.exp(tpl_log[-1])),
        "growth_x": float(np.exp(tpl_log[-1] - tpl_log[0])),
        "yec_now": y_now,
        "yec_implied_end": float(implied[-1]),
        "yec_implied_max": float(implied.max()),
        "b_fit_zec_daily30_exlaunch": b_fit,
        "b_power_used": b_power,
        "hours": int(tpl_ts.size),
    }
    # anchor of the level rules: YEC's own current state — its last frozen price and the median
    # SIGMA-1 σ̂ (42 h kernel) over its last 90 days — so at today's price a level rule demands what
    # the chain demands today and then follows the level
    y_sig = sigma_hat_bps_hourly(np.rint(yh.price * 1_000_000).astype(np.int64))[-24 * 90 :]
    sig0 = float(np.median(y_sig[y_sig >= 0]))
    summary["template"]["sigma0_anchor_bps_yec_last90d"] = sig0
    coarse_tpl = coarse_of(tpl_ts)
    rows_real, req_real = score_path(
        implied, coarse_tpl, tuple(COARSE), y_now, sig0, b_power, b_fit, "ZEC template (real)"
    )
    # ensemble: within-coarse-regime block bootstrap
    coarse_t = coarse_of(tpl_ts)
    bounds = []
    for name in ("low", "run-up", "high"):
        idx = np.nonzero(coarse_t == name)[0]
        if idx.size:
            bounds.append((int(idx[0]), int(idx[-1]) + 1))
    ens_rows, ens_req = [], []
    rng = np.random.default_rng(2026)
    ends = []
    for seed in range(args.seeds):
        lp = block_bootstrap_path(np.log(implied), bounds, BLOCK_H, rng)
        ends.append(float(np.exp(lp[-1])))
        r, rq = score_path(
            np.exp(lp), coarse_tpl[: lp.size], tuple(COARSE), y_now, sig0, b_power, b_fit, f"seed {seed}"
        )
        ens_rows.extend(r)
        ens_req.extend(rq)
    summary["template"]["ensemble_end_price"] = {
        "p10": float(np.percentile(ends, 10)),
        "median": float(np.median(ends)),
        "p90": float(np.percentile(ends, 90)),
    }
    # aggregate ensemble: mean and worst over seeds per (regime, rule, base)
    agg = {}
    for r in ens_rows:
        key = (r["regime"], r["rule"], r["base_bps"])
        agg.setdefault(key, []).append(r)
    ens_agg = []
    for (reg, rule, base), rs in agg.items():
        pb = np.array([x["p_bad_classA"] for x in rs], dtype=float)
        ens_agg.append(
            {
                "regime": reg,
                "rule": rule,
                "base_bps": base,
                "p_bad_mean": float(np.nanmean(pb)),
                "p_bad_worst": float(np.nanmax(pb)),
                "p_bad_p90": float(np.nanpercentile(pb, 90)),
                "eff_mean_bps": float(np.nanmean([x["eff_mean_bps"] for x in rs])),
                "need_p99_mean_bps": float(np.nanmean([x["need_p99_bps"] for x in rs])),
                "need_max_worst_bps": float(np.nanmax([x["need_max_bps"] for x in rs])),
                "seeds": len(rs),
            }
        )
    # ensemble required base: worst (max) over seeds per (regime, rule, tol), mean lock at it
    agg_r = {}
    for r in ens_req:
        agg_r.setdefault((r["regime"], r["rule"], r["tolerance"]), []).append(r)
    ens_req_agg = []
    for (reg, rule, tol), rs in agg_r.items():
        bases_ = [x["required_base_bps"] for x in rs]
        ens_req_agg.append(
            {
                "regime": reg,
                "rule": rule,
                "tolerance": tol,
                "required_base_worst_bps": max(bases_),
                "required_base_median_bps": float(np.median(bases_)),
                "eff_mean_at_worst_bps": float(
                    np.nanmean(
                        [
                            x["eff_mean_bps"] / max(x["required_base_bps"], 1) * max(bases_)
                            for x in rs
                            if x["required_base_bps"] > 0
                        ]
                    )
                )
                if any(x["required_base_bps"] > 0 for x in rs)
                else math.nan,
                "need_max_worst_bps": float(np.nanmax([x["need_max_bps"] for x in rs])),
            }
        )
    write_csv(out / "levelshift_real.csv", rows_real)
    write_csv(out / "levelshift_real_required.csv", req_real)
    write_csv(out / "levelshift_ensemble.csv", ens_agg)
    write_csv(out / "levelshift_ensemble_required.csv", ens_req_agg)
    # the ZEC history ex-launch through the same rules at ZEC's own prices, anchored at its 2023-24
    # trough (median price, median σ̂), for the realised need across the 2017-18 and 2021 cycles too
    t1 = int(np.searchsorted(zh.ts, _ts("2017-04-01")))
    zh_sig = sigma_hat_bps_hourly(np.rint(zh.price * 1_000_000).astype(np.int64))
    tr = reg_h == "2023-24 trough"
    z_sig0 = float(np.median(zh_sig[tr & (zh_sig >= 0)]))
    z_p0 = float(np.median(zh.price[tr]))
    summary["zec_own"] = {"anchor_price": z_p0, "anchor_sigma_bps": z_sig0}
    rows_zec_all, req_zec_all = score_path(
        zh.price[t1:],
        reg_h[t1:],
        tuple(r[0] for r in REGIMES[1:]),
        z_p0,
        z_sig0,
        b_power,
        b_fit,
        "ZEC 2017-04→ (own prices)",
    )
    write_csv(out / "levelshift_zec_full.csv", rows_zec_all)
    write_csv(out / "levelshift_zec_full_required.csv", req_zec_all)

    # ---- part 3: estimators ----------------------------------------------------------------------
    est_rows, lever_rows = [], []
    garch_info = {}
    for who, s, skip in (
        ("ZEC", Series(zh.ts[t1:], zh.price[t1:], zh.volume[t1:], 3_600), 24 * 400),
        ("YEC", yh, 24 * 400),
    ):
        est, info = estimators(s, skip=skip)
        garch_info[who] = info
        est_rows.extend(score_estimators(s, est, who, skip))
        lever_rows.extend(lever_study(s, who, skip))
    write_csv(out / "estimators.csv", est_rows)
    write_csv(out / "levers.csv", lever_rows)
    summary["garch"] = garch_info
    # ranking: out-of-sample, per (series, term, tol): candidates meeting the tolerance on the second
    # half (exceedance ≤ 1.25 × tol) ranked by mean lock; the others listed as failing coverage
    rank_rows = []
    for who in ("ZEC", "YEC"):
        for h in TERMS_D:
            for tol in TOLS:
                cand = [
                    r
                    for r in est_rows
                    if r["series"] == who
                    and r["term_days"] == h
                    and r["tolerance"] == tol
                    and r["eval"] == "first→second half"
                ]
                # one entry per estimator: its best form by lock among the covering forms, else by exceedance
                by_est: dict[str, dict] = {}
                for r in cand:
                    cur = by_est.get(r["estimator"])
                    covers = r["exceed_test"] <= 1.25 * tol
                    if cur is None:
                        by_est[r["estimator"]] = r
                        continue
                    cur_cov = cur["exceed_test"] <= 1.25 * tol
                    if (covers and not cur_cov) or (
                        covers == cur_cov
                        and (
                            r["lock_mean"] < cur["lock_mean"]
                            if covers
                            else r["exceed_test"] < cur["exceed_test"]
                        )
                    ):
                        by_est[r["estimator"]] = r
                ranked = sorted(
                    by_est.values(),
                    key=lambda r: (
                        not (r["exceed_test"] <= 1.25 * tol),
                        r["lock_mean"] if r["exceed_test"] <= 1.25 * tol else r["exceed_test"],
                    ),
                )
                for i, r in enumerate(ranked):
                    rank_rows.append(
                        {
                            "series": who,
                            "term_days": h,
                            "tolerance": tol,
                            "rank": i + 1,
                            "estimator": r["estimator"],
                            "form": r["form"],
                            "lock_mean": r["lock_mean"],
                            "lock_p90": r["lock_p90"],
                            "exceed_test": r["exceed_test"],
                            "covers": r["exceed_test"] <= 1.25 * tol,
                        }
                    )
    write_csv(out / "ranking.csv", rank_rows)

    # headline numbers
    def pick(src, w, lab):
        return next((f for f in fits if f.source == src and f.window_days == w and f.label == lab), None)

    summary["headline_fits"] = {
        k: {"b": f.elasticity, "ci": [f.ci_lo, f.ci_hi], "spearman": f.spearman, "r2": f.r2, "n": f.n}
        for k, f in {
            "zec-daily-30-all": pick("zec-daily", 30, "all"),
            "zec-daily-30-exlaunch": pick("zec-daily", 30, "ex-launch"),
            "zec-hourly-30-exlaunch": pick("zec-hourly", 30, "ex-launch"),
            "zec-mcap-30-exlaunch": pick("zec-daily-mcap", 30, "ex-launch"),
            "zec-daily-30-high": pick("zec-daily", 30, "high"),
            "zec-daily-30-trough": pick("zec-daily", 30, "2023-24 trough"),
            "yec-daily-30-all": pick("yec-daily", 30, "all"),
        }.items()
        if f
    }
    summary["regime_sigma"] = sig_rows
    summary["drawdowns_claim_open_60d"] = [
        r for r in dd_rows if r["term_days"] == 60 and r["horizon"].startswith("claim")
    ]
    summary["levelshift_real_required"] = req_real
    summary["levelshift_ens_required"] = ens_req_agg
    summary["levelshift_zec_full_required"] = req_zec_all
    summary["ranking_top3_oos"] = {
        f"{who}-{h}d-tol{tol}": [
            {
                "estimator": r["estimator"],
                "form": r["form"],
                "lock_mean": r["lock_mean"],
                "exceed_test": r["exceed_test"],
            }
            for r in rank_rows
            if r["series"] == who and r["term_days"] == h and r["tolerance"] == tol and r["rank"] <= 3
        ]
        for who in ("ZEC", "YEC")
        for h in TERMS_D
        for tol in TOLS
    }
    summary["levers_60d"] = [r for r in lever_rows if r["term_days"] == 60]
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))

    # tables.md
    frag = []
    frag.append(
        "### ZEC regimes\n\n"
        + md_table(
            reg_rows,
            [
                ("regime", "regime", None),
                ("from", "from", None),
                ("to", "to", None),
                ("days", "days", None),
                ("price_min", "min $", 1),
                ("price_median", "median $", 1),
                ("price_max", "max $", 1),
                ("mcap_median_musd", "median mcap (M$)", 0),
            ],
        )
    )
    frag.append(
        "### Level fits\n\n"
        + md_table(
            sorted(fit_rows, key=lambda r: (r["source"], r["window_days"], r["label"])),
            [
                ("source", "series", None),
                ("window_days", "w (d)", None),
                ("label", "period", None),
                ("n", "n", None),
                ("spearman", "Spearman", 3),
                ("elasticity", "b", 3),
                ("ci_lo", "CI lo", 3),
                ("ci_hi", "CI hi", 3),
                ("elasticity_time_ctrl", "b | time", 3),
                ("r2", "R²", 3),
                ("sigma_med", "median σ", 2),
                ("price_med", "median level", 2),
            ],
        )
    )
    frag.append(
        "### Regime σ\n\n"
        + md_table(
            sig_rows,
            [
                ("regime", "regime", None),
                ("price_median", "median $", 2),
                ("sigma30d_daily_median", "σ30 daily med", 2),
                ("sigma30d_daily_p90", "p90", 2),
                ("sigma30d_hourly_median", "σ30 hourly med", 2),
                ("sigma30d_hourly_p90", "p90", 2),
            ],
        )
    )
    frag.append(
        "### Price deciles (ZEC daily 30 d, ex-launch)\n\n"
        + md_table(
            dec_price,
            [
                ("decile", "decile", None),
                ("price_lo", "from $", 1),
                ("price_hi", "to $", 1),
                ("n", "n", None),
                ("sigma_p10", "σ p10", 2),
                ("sigma_median", "σ med", 2),
                ("sigma_p90", "σ p90", 2),
            ],
        )
    )
    frag.append(
        "### Market-cap deciles (ZEC daily 30 d, ex-launch)\n\n"
        + md_table(
            [{**r, "price_lo": r["price_lo"] / 1e6, "price_hi": r["price_hi"] / 1e6} for r in dec_mcap],
            [
                ("decile", "decile", None),
                ("price_lo", "from M$", 0),
                ("price_hi", "to M$", 0),
                ("n", "n", None),
                ("sigma_p10", "σ p10", 2),
                ("sigma_median", "σ med", 2),
                ("sigma_p90", "σ p90", 2),
            ],
        )
    )
    frag.append(
        "### Drawdowns pMint/pClaim\n\n"
        + md_table(
            dd_rows,
            [
                ("series", "series", None),
                ("regime", "regime", None),
                ("term_days", "T (d)", None),
                ("horizon", "horizon", None),
                ("n", "n", None),
                ("p50", "p50", 2),
                ("p90", "p90", 2),
                ("p99", "p99", 2),
                ("max", "max", 2),
                ("frac_gt_5x", "> 5×", 4),
                ("frac_gt_7.25x", "> 7.25×", 4),
                ("frac_gt_10x", "> 10×", 4),
            ],
        )
    )
    cols_ls = [
        ("path", "path", None),
        ("regime", "regime", None),
        ("rule", "rule", None),
        ("base_bps", "base", None),
        ("p_bad_classA", "P(bad) A", 4),
        ("p_bad_30d", "30 d", 4),
        ("p_bad_60d", "60 d", 4),
        ("p_bad_90d", "90 d", 4),
        ("eff_mean_bps", "eff mean", 0),
        ("mult_mean", "mult", 2),
        ("need_p99_bps", "need p99", 0),
        ("need_max_bps", "need max", 0),
    ]
    frag.append("### Level shift, real template\n\n" + md_table(rows_real, cols_ls))
    frag.append("### Level shift, ZEC 2017-04→ own prices\n\n" + md_table(rows_zec_all, cols_ls))
    cols_rq = [
        ("path", "path", None),
        ("regime", "regime", None),
        ("rule", "rule", None),
        ("tolerance", "tol", 2),
        ("required_base_bps", "base needed", None),
        ("p_bad_at_required", "P(bad)", 4),
        ("eff_mean_bps", "eff mean", 0),
        ("eff_p90_bps", "eff p90", 0),
        ("mult_mean", "mult", 2),
        ("need_p99_bps", "need p99", 0),
        ("need_max_bps", "need max", 0),
    ]
    frag.append("### Required base per tolerance, real template\n\n" + md_table(req_real, cols_rq))
    frag.append(
        "### Required base per tolerance, ZEC 2017-04→ own prices\n\n" + md_table(req_zec_all, cols_rq)
    )
    frag.append(
        "### Required base per tolerance, ensemble (worst seed)\n\n"
        + md_table(
            ens_req_agg,
            [
                ("regime", "regime", None),
                ("rule", "rule", None),
                ("tolerance", "tol", 2),
                ("required_base_worst_bps", "base worst", None),
                ("required_base_median_bps", "base median", 0),
                ("eff_mean_at_worst_bps", "eff mean at worst", 0),
                ("need_max_worst_bps", "need max", 0),
            ],
        )
    )
    frag.append(
        "### Level shift, ensemble\n\n"
        + md_table(
            ens_agg,
            [
                ("regime", "regime", None),
                ("rule", "rule", None),
                ("base_bps", "base", None),
                ("p_bad_mean", "P(bad) mean", 4),
                ("p_bad_p90", "p90", 4),
                ("p_bad_worst", "worst", 4),
                ("eff_mean_bps", "eff mean", 0),
                ("need_p99_mean_bps", "need p99", 0),
                ("need_max_worst_bps", "need max", 0),
            ],
        )
    )
    frag.append(
        "### Ranking (out of sample: sized on the first half, scored on the second)\n\n"
        + md_table(
            rank_rows,
            [
                ("series", "series", None),
                ("term_days", "T", None),
                ("tolerance", "tol", 2),
                ("rank", "rank", None),
                ("estimator", "candidate", None),
                ("form", "form", None),
                ("lock_mean", "lock mean", 2),
                ("lock_p90", "lock p90", 2),
                ("exceed_test", "exceedance", 4),
                ("covers", "covers", None),
            ],
        )
    )
    frag.append(
        "### Levers: claim threshold θ and claim delay d, required flat ratio\n\n"
        + md_table(
            lever_rows,
            [
                ("series", "series", None),
                ("term_days", "T", None),
                ("theta", "θ", 2),
                ("delay_h", "d (h)", None),
                ("tolerance", "tol", 2),
                ("required_flat_ratio", "flat ratio needed", 2),
                ("p_bad_at_200", "P(bad) @200 %", 4),
                ("p_bad_at_300", "@300 %", 4),
                ("p_bad_at_500", "@500 %", 4),
                ("p_bad_at_725", "@725 %", 4),
            ],
        )
    )
    frag.append(
        "### Estimators\n\n"
        + md_table(
            est_rows,
            [
                ("series", "series", None),
                ("term_days", "T", None),
                ("estimator", "estimator", None),
                ("kind", "kind", None),
                ("form", "form", None),
                ("tolerance", "tol", 2),
                ("eval", "eval", None),
                ("k", "k", 3),
                ("lock_mean", "lock mean", 2),
                ("lock_median", "lock med", 2),
                ("lock_p90", "lock p90", 2),
                ("exceed_fit", "exc fit", 4),
                ("exceed_test", "exc test", 4),
                ("n_test", "n", None),
            ],
        )
    )
    (out / "tables.md").write_text("\n\n".join(frag))

    if not args.no_figures:
        figures(
            out,
            zd,
            zh,
            yd,
            reg_d,
            sig30,
            lvl30,
            mcap_d,
            fits,
            dd_rows,
            tpl_ts,
            implied,
            req_real,
            ens_req_agg,
            est_rows,
            rank_rows,
            rng,
            bounds,
        )
    print(
        json.dumps(
            {
                k: summary[k]
                for k in (
                    "zec_daily",
                    "zec_hourly",
                    "cmc_vs_cg_90d",
                    "headline_fits",
                    "template",
                    "garch",
                    "ranking_top3_oos",
                )
            },
            indent=1,
            default=str,
        )
    )
    return 0


def figures(
    out,
    zd,
    zh,
    yd,
    reg_d,
    sig30,
    lvl30,
    mcap_d,
    fits,
    dd_rows,
    tpl_ts,
    implied,
    req_real,
    ens_req_agg,
    est_rows,
    rank_rows,
    rng,
    bounds,
):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "font.size": 9,
            "axes.edgecolor": MUTED,
            "axes.labelcolor": INK,
            "xtick.color": INK,
            "ytick.color": INK,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.color": "#e6e5e0",
            "grid.linewidth": 0.6,
        }
    )
    pal = {n: c for (n, *_), c in zip(REGIMES, (MUTED, C1, C2, C3, C4, C5, C2, C6), strict=True)}
    td = [dt.datetime.fromtimestamp(int(t), dt.UTC) for t in zd.ts]

    # F1: ZEC price (log) with regimes, σ30 below
    fig, axes = plt.subplots(2, 1, figsize=(9, 6), sharex=True)
    axes[0].plot(td, zd.price, color=INK, lw=1.1)
    axes[0].set_yscale("log")
    for name, a, b in REGIMES:
        axes[0].axvspan(
            dt.datetime.fromtimestamp(_ts(a), dt.UTC),
            min(dt.datetime.fromtimestamp(_ts(b), dt.UTC), td[-1]),
            color=pal[name],
            alpha=0.12,
            lw=0,
        )
        mid = (_ts(a) + min(_ts(b), int(zd.ts[-1]))) / 2
        axes[0].annotate(
            name,
            (dt.datetime.fromtimestamp(mid, dt.UTC), float(zd.price.max()) * 1.4),
            ha="center",
            fontsize=7,
            color=INK,
            rotation=0,
        )
    axes[0].set_ylim(10, float(zd.price.max()) * 3)
    axes[0].set_ylabel("ZEC/USD (log)")
    axes[0].set_title("ZEC 2016-2026: price regimes and 30-day realised σ", loc="left")
    axes[1].plot(td, sig30, color=C1, lw=1.2, label="ZEC σ30 (daily returns)")
    tyd = [dt.datetime.fromtimestamp(int(t), dt.UTC) for t in yd.ts]
    axes[1].plot(tyd, rolling_sigma(np.log(yd.price), 30, 365), color=C2, lw=0.9, alpha=0.8, label="YEC σ30")
    axes[1].set_ylim(0, 4)
    axes[1].set_ylabel("annualised σ")
    axes[1].legend(frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(out / "fig1_zec_regimes.png", dpi=140)
    plt.close(fig)

    # F2: σ vs price and σ vs mcap scatter by regime (ex-launch)
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.4), sharey=True)
    for ax, x, xl in (
        (axes[0], lvl30, "30-day mean price ($, log)"),
        (axes[1], np.exp(rolling_mean(np.log(mcap_d), 30)) / 1e6, "30-day mean market cap (M$, log)"),
    ):
        for name, *_ in REGIMES[1:]:
            m = (reg_d == name) & ~np.isnan(sig30) & ~np.isnan(x)
            ax.scatter(x[m], sig30[m], s=6, alpha=0.5, color=pal[name], label=name, linewidths=0)
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xlabel(xl)
    f_ex = next(f for f in fits if f.source == "zec-daily" and f.window_days == 30 and f.label == "ex-launch")
    A, b = f_ex.model()
    xs = np.logspace(1, 3.3, 50)
    axes[0].plot(xs, A * xs ** (-b), color=INK, lw=2, label=f"fit: σ = {A:.2f}·p^({-b:+.2f})")
    f_mc = next(
        (f for f in fits if f.source == "zec-daily-mcap" and f.window_days == 30 and f.label == "ex-launch"),
        None,
    )
    if f_mc:
        Am, bm = f_mc.model()
        xm = np.logspace(8, 10.5, 50)
        axes[1].plot(xm / 1e6, Am * xm ** (-bm), color=INK, lw=2, label=f"fit: σ ∝ mcap^({-bm:+.2f})")
    axes[0].set_ylabel("30-day realised σ (log)")
    axes[0].legend(frameon=False, fontsize=7)
    axes[1].legend(frameon=False, fontsize=7)
    axes[0].set_title("ZEC: σ vs price level (ex-launch)", loc="left")
    axes[1].set_title("ZEC: σ vs market cap", loc="left")
    fig.tight_layout()
    fig.savefig(out / "fig2_sigma_vs_level.png", dpi=140)
    plt.close(fig)

    # F3: elasticity per regime (daily & hourly, 30 d) + YEC
    fig, ax = plt.subplots(figsize=(8, 3.8))
    labs = ["all", "ex-launch", *[r[0] for r in REGIMES]]
    for k, (src, color) in enumerate((("zec-daily", C1), ("zec-hourly", C2), ("zec-daily-mcap", C3))):
        pts = [
            next((f for f in fits if f.source == src and f.window_days == 30 and f.label == lab), None)
            for lab in labs
        ]
        x = np.arange(len(labs)) + (k - 1) * 0.2
        ys = [p.elasticity if p else np.nan for p in pts]
        lo = [max(p.elasticity - p.ci_lo, 0) if p and not math.isnan(p.ci_lo) else 0 for p in pts]
        hi = [max(p.ci_hi - p.elasticity, 0) if p and not math.isnan(p.ci_hi) else 0 for p in pts]
        ax.errorbar(
            x,
            ys,
            yerr=[lo, hi],
            fmt="o",
            color=color,
            ms=4,
            lw=1,
            capsize=2,
            label={
                "zec-daily": "daily vs price",
                "zec-hourly": "hourly vs price",
                "zec-daily-mcap": "daily vs mcap",
            }[src],
        )
    ax.axhline(0, color=MUTED, lw=1)
    ax.set_xticks(np.arange(len(labs)))
    ax.set_xticklabels(labs, rotation=30, ha="right", fontsize=7)
    ax.set_ylabel("elasticity d log σ / d log level (90 % CI)")
    ax.set_title("ZEC: did σ fall as the level rose? Elasticity per regime, 30-day windows", loc="left")
    ax.legend(frameon=False, fontsize=7)
    fig.tight_layout()
    fig.savefig(out / "fig3_elasticity.png", dpi=140)
    plt.close(fig)

    # F4: drawdown quantiles per regime (claim-open 60 d)
    fig, ax = plt.subplots(figsize=(8, 3.8))
    rows = [r for r in dd_rows if r["term_days"] == 60 and r["horizon"].startswith("claim")]
    x = np.arange(len(rows))
    ax.bar(x, [r["p99"] for r in rows], color=C1, width=0.6, label="p99")
    ax.plot(x, [r["max"] for r in rows], "v", color=INK, label="max")
    ax.plot(x, [r["p90"] for r in rows], "_", color=C2, ms=14, mew=2, label="p90")
    for y_, c_, lab in ((5, C3, "500 %"), (7.25, C4, "725 %"), (10, C6, "1,000 %")):
        ax.axhline(y_, color=c_, ls="--", lw=0.8)
        ax.annotate(lab, (len(rows) - 0.5, y_ * 1.03), fontsize=7, color=c_, ha="right")
    ax.set_xticks(x)
    ax.set_xticklabels([f"{r['series']} {r['regime']}" for r in rows], rotation=30, ha="right", fontsize=7)
    ax.set_yscale("log")
    ax.set_ylabel("pMint / pClaim, 60-day term + 30-day grace")
    ax.set_title("The quantity bad debt depends on: pMint/pClaim at claim opening, per regime", loc="left")
    ax.legend(frameon=False, fontsize=7)
    fig.tight_layout()
    fig.savefig(out / "fig4_drawdowns.png", dpi=140)
    plt.close(fig)

    # F5: implied YEC path + ensemble fan
    fig, ax = plt.subplots(figsize=(9, 4))
    tt = [dt.datetime.fromtimestamp(int(t), dt.UTC) for t in tpl_ts]
    rng2 = np.random.default_rng(99)
    for _ in range(12):
        lp = block_bootstrap_path(np.log(implied), bounds, BLOCK_H, rng2)
        ax.plot(tt[: lp.size], np.exp(lp), color=C1, lw=0.5, alpha=0.35)
    ax.plot(tt, implied, color=INK, lw=1.4, label="YEC implied by the ZEC template (real path)")
    ax.set_yscale("log")
    for name, c_ in (("low", C3), ("run-up", C2), ("high", C6)):
        a, b = COARSE[name]
        ax.axvspan(
            dt.datetime.fromtimestamp(_ts(a), dt.UTC),
            min(dt.datetime.fromtimestamp(_ts(b), dt.UTC), tt[-1]),
            color=c_,
            alpha=0.08,
            lw=0,
        )
    ax.set_ylabel("implied YEC/USD (log)")
    ax.set_title(
        f"Level-shift template: YEC ${implied[0]:.2f} today mapped onto ZEC from {TEMPLATE_T0} "
        "(blue: 12 block-bootstrap members)",
        loc="left",
        fontsize=9,
    )
    ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(out / "fig5_template.png", dpi=140)
    plt.close(fig)

    # F6: per-regime base needed at 2 % by rule, real template vs ensemble worst seed
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), sharey=True)
    rules = list(dict.fromkeys(r["rule"] for r in req_real))
    regs = ("low", "run-up", "high", "all")
    wdt = 0.8 / len(rules)
    for ax, data, title, key in (
        (axes[0], req_real, "real template: base needed at 2 %", "required_base_bps"),
        (axes[1], ens_req_agg, "ensemble, worst seed: base needed at 2 %", "required_base_worst_bps"),
    ):
        for i, rule in enumerate(rules):
            ys = [
                next(
                    (
                        r[key] / 100
                        for r in data
                        if r["rule"] == rule and r["regime"] == reg and r["tolerance"] == 0.02
                    ),
                    np.nan,
                )
                for reg in regs
            ]
            ax.bar(
                np.arange(len(regs)) + i * wdt - 0.4 + wdt / 2,
                ys,
                width=wdt,
                color=[C1, MUTED, C2, C3, C4, C6, C5][i % 7],
                label=rule,
            )
        ax.set_xticks(np.arange(len(regs)))
        ax.set_xticklabels(regs)
        ax.set_title(title, loc="left", fontsize=9)
    axes[0].set_ylabel("base ratio (%) meeting 2 % P(bad), class A")
    axes[0].legend(frameon=False, fontsize=6)
    fig.tight_layout()
    fig.savefig(out / "fig6_levelshift_required.png", dpi=140)
    plt.close(fig)

    # F7: estimator frontier — lock mean vs realised OOS exceedance, 60 d, tol 2 %, sigma1 form
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2))
    for ax, who in zip(axes, ("ZEC", "YEC"), strict=True):
        rows = [
            r
            for r in est_rows
            if r["series"] == who
            and r["term_days"] == 60
            and r["tolerance"] == 0.02
            and r["eval"] == "first→second half"
            and r["form"] in ("sigma1", "direct")
        ]
        for r in rows:
            kind_c = {"sigma": C1, "lock": C2}[r["kind"]]
            mk = "o" if "conditional" not in r["estimator"] else "s"
            ax.scatter(r["lock_mean"], r["exceed_test"], s=28, color=kind_c, marker=mk, alpha=0.8)
        top = {
            r["estimator"]
            for r in rank_rows
            if r["series"] == who and r["term_days"] == 60 and r["tolerance"] == 0.02 and r["rank"] <= 3
        }
        for r in rows:
            if r["estimator"] in top or r["estimator"] in ("realised 42 h (today)", "flat (constant)"):
                ax.annotate(
                    r["estimator"][:40],
                    (r["lock_mean"], r["exceed_test"]),
                    fontsize=6,
                    xytext=(4, 3),
                    textcoords="offset points",
                )
        ax.axhline(0.02, color=MUTED, ls="--", lw=0.8)
        ax.set_xlabel("mean locked ratio (×), sized to 2 % on the first half")
        ax.set_title(f"{who}: second-half exceedance vs lock, 60-day term", loc="left", fontsize=9)
    axes[0].set_ylabel("realised exceedance (second half)")
    axes[0].scatter([], [], color=C1, label="σ-based (SIGMA-1 form)")
    axes[0].scatter([], [], color=C2, label="drawdown quantile (direct)")
    axes[0].scatter([], [], color=C2, marker="s", label="state-conditional quantile")
    axes[0].legend(frameon=False, fontsize=7, loc="upper right")
    fig.tight_layout()
    fig.savefig(out / "fig7_estimators.png", dpi=140)
    plt.close(fig)


if __name__ == "__main__":
    raise SystemExit(main())

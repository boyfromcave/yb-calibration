# Copyright (c) 2026 The Ycash developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or https://www.opensource.org/licenses/mit-license.php .
"""Volatility vs price level (owner hypothesis 2026-10-07) and price-level-aware ratio rules.

Pure numpy over the frozen YEC series (``yec-daily.csv``, ``yec-hourly.csv``); no ensembles, no
oracle simulation. Three parts, reported by :func:`main` into ``docs/reports/2026-10-volatility/``:

1. **Realised σ vs price level.** Rolling 30/60/90-day annualised σ of log returns (daily and hourly)
   against the window's mean price, with price-decile tables, Spearman/Pearson correlations, the
   elasticity ``d log σ / d log p`` (OLS with a moving-block bootstrap CI and a time-trend control)
   per sub-period (2019-20, 2021-22, 2023-24, 2025-26). The collateral maths is scale-free (ratios of
   prices), so ``log(p+1)`` or any monotone transform of ``p`` changes nothing by itself: the testable
   claim is that σ *depends on* the level, and Spearman is identical for every monotone transform.

2. **Ratio-rule backtest** on the real hourly path with the G3 bad-debt definition
   (``ybcal.sim.metrics.p_bad_debt_fast``): a vault minted at hour ``s`` with
   ``collateral = cents · ratio / pMint`` is bad iff ``collateral · price(e) < debt`` at the claim-open
   hour ``e = s + ceil((T + grace + 1) / 48)``, averaged over an equal-probability grid of lock terms
   of the class. Here ``pMint`` is the market price (ideal oracle) and σ̂ is SIGMA-1's own kernel
   (``vkernels.sigma_mult_series``) run on the hourly price as pFast (volWindow 2016 / volStep 48 at
   mainnet = 42 hourly returns). Rules: the current multiplier, a price-level model σ̂(p) = a·p^(−b)
   (in-sample and fitted on data to 2022 only), the conservative blend ``max`` and the geometric blend,
   and a fixed multiplier; each sized to the same tolerance, reporting the required base ratio and the
   time-average effective ratio minters would have faced.

3. The consensus feasibility note is prose in the README (no code).

Run::

    PYTHONPATH=src .venv/bin/python -m ybcal.studies.vol_price_level \
        --data-dir data/local/frozen-20261004 --out docs/reports/2026-10-volatility
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ybcal.model import vkernels as V
from ybcal.units import BLOCKS_PER_HOUR, BPS

# the reference categorical palette (slots 1-3 validate all-pairs) + neutral ink
C1, C2, C3, INK, MUTED = "#2a78d6", "#eb6834", "#1baf7a", "#0b0b0b", "#9a9890"

# --- mainnet parameters this study reads (registry @ 7702d22 + hardening H-3 decisions) -----------
VOL_WINDOW, VOL_STEP, PPY = 2016, 48, 8760
SIGMA_REF_SHIPPED, SIGMA_REF_H3 = 10_000, 18_000
SIGMA_CAP_SHIPPED, SIGMA_CAP_H3 = 30_000, 47_500
GRACE = 34_560
CLASS_TERMS = {"A": (34_560, 103_680), "B": (103_681, 420_480), "C": (420_481, 2_102_400)}
TOLERANCE = {"A": 0.005, "B": 0.01, "C": 0.02}
N_TERMS = 16
LATTICE = np.arange(10_000, 1_000_001, 2_500)
SUBPERIODS = (
    ("2019-20", 2019, 2020),
    ("2021-22", 2021, 2022),
    ("2023-24", 2023, 2024),
    ("2025-26", 2025, 2026),
)
WINDOWS_D = (30, 60, 90)


# ---------------------------------------------------------------------------------------------------
# data


@dataclass
class Series:
    ts: np.ndarray  # int64 unix seconds, regular grid
    price: np.ndarray  # float USD, forward-filled
    volume: np.ndarray  # float USD/24h, nan where absent
    step: int  # seconds between rows

    @property
    def years(self) -> np.ndarray:
        return np.array([dt.datetime.fromtimestamp(int(t), dt.UTC).year for t in self.ts])

    def iso(self, i: int) -> str:
        return dt.datetime.fromtimestamp(int(self.ts[i]), dt.UTC).strftime("%Y-%m-%d")


def load(path: Path, step: int) -> Series:
    """Read a ``ts_iso,ts,price_usd,volume_24h_usd`` CSV onto a regular grid of ``step`` seconds
    (gaps forward-filled; duplicates keep the last)."""
    ts, px, vol = [], [], []
    with path.open() as f:
        for row in csv.DictReader(f):
            ts.append(int(row["ts"]))
            px.append(float(row["price_usd"]))
            v = (row.get("volume_24h_usd") or "").strip()
            vol.append(float(v) if v else math.nan)
    ts_a, px_a, vol_a = np.array(ts, dtype=np.int64), np.array(px), np.array(vol)
    order = np.argsort(ts_a, kind="stable")
    ts_a, px_a, vol_a = ts_a[order], px_a[order], vol_a[order]
    grid = np.arange(ts_a[0], ts_a[-1] + 1, step, dtype=np.int64)
    idx = np.searchsorted(ts_a, grid, side="right") - 1
    return Series(grid, px_a[idx], vol_a[idx], step)


def check_sums(data_dir: Path) -> dict[str, bool]:
    """Verify ``SHA256SUMS`` for the two price files (never fetch)."""
    import hashlib

    want = {}
    for line in (data_dir / "SHA256SUMS").read_text().splitlines():
        h, _, name = line.partition("  ")
        want[name.strip()] = h.strip()
    out = {}
    for name in ("yec-hourly.csv", "yec-daily.csv"):
        got = hashlib.sha256((data_dir / name).read_bytes()).hexdigest()
        out[name] = got == want.get(name)
    return out


# ---------------------------------------------------------------------------------------------------
# part 1: realised σ vs level


def rolling_sigma(logp: np.ndarray, w: int, ppy: float) -> np.ndarray:
    """Annualised σ of the ``w`` log returns ending at each index (nan before ``w``)."""
    r = np.diff(logp)
    n = r.size
    out = np.full(logp.size, np.nan)
    if n < w:
        return out
    c1 = np.concatenate([[0.0], np.cumsum(r)])
    c2 = np.concatenate([[0.0], np.cumsum(r * r)])
    s1 = c1[w:] - c1[:-w]
    s2 = c2[w:] - c2[:-w]
    var = (s2 - s1 * s1 / w) / (w - 1)
    out[w:] = np.sqrt(np.maximum(var, 0.0) * ppy)
    return out


def rolling_mean(x: np.ndarray, w: int) -> np.ndarray:
    out = np.full(x.size, np.nan)
    c = np.concatenate([[0.0], np.cumsum(np.nan_to_num(x))])
    k = np.concatenate([[0.0], np.cumsum(~np.isnan(x))])
    num = c[w:] - c[:-w]
    den = k[w:] - k[:-w]
    with np.errstate(invalid="ignore", divide="ignore"):
        out[w - 1 :] = np.where(den > 0, num / den, np.nan)
    return out


def ols(y: np.ndarray, x: np.ndarray, t: np.ndarray | None = None) -> tuple[float, float, float]:
    """``y = a + b·x (+ c·t)``: returns ``(a, b, r2)``."""
    cols = [np.ones_like(x), x] + ([t] if t is not None else [])
    X = np.column_stack(cols)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    yhat = X @ beta
    ss = ((y - y.mean()) ** 2).sum()
    r2 = 1 - ((y - yhat) ** 2).sum() / ss if ss > 0 else math.nan
    return float(beta[0]), float(beta[1]), float(r2)


def block_bootstrap_slope(
    y: np.ndarray, x: np.ndarray, block: int, reps: int, seed: int = 7
) -> tuple[float, float]:
    """Moving-block bootstrap 90 % CI of the OLS slope (rolling windows overlap, so plain OLS
    standard errors are far too small)."""
    rng = np.random.default_rng(seed)
    n = y.size
    if n <= block + 2:
        return math.nan, math.nan
    nb = -(-n // block)
    starts = np.arange(0, n - block + 1)
    slopes = []
    for _ in range(reps):
        pick = rng.choice(starts, nb)
        idx = (pick[:, None] + np.arange(block)[None, :]).ravel()[:n]
        slopes.append(ols(y[idx], x[idx])[1])
    lo, hi = np.percentile(slopes, [5, 95])
    return float(lo), float(hi)


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    from scipy.stats import spearmanr

    return float(spearmanr(a, b).statistic)


def pearson(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.corrcoef(a, b)[0, 1])


@dataclass
class LevelFit:
    label: str
    source: str
    window_days: int
    n: int
    spearman: float
    pearson_log: float
    elasticity: float  # b in log σ = a + b log p
    ci_lo: float
    ci_hi: float
    r2: float
    a: float  # intercept (log σ at p = $1)
    elasticity_time_ctrl: float  # b with a linear time control
    sigma_med: float
    price_med: float

    def model(self) -> tuple[float, float]:
        """σ̂(p) = A · p^(-b) with A = e^a."""
        return math.exp(self.a), -self.elasticity


def fit_level(
    s: Series, w_days: int, label: str, source: str, years: tuple[int, ...] | None, reps: int
) -> tuple[LevelFit | None, np.ndarray, np.ndarray]:
    per_day = 86_400 // s.step
    w = w_days * per_day
    ppy = 365 * per_day
    logp = np.log(s.price)
    sig = rolling_sigma(logp, w, ppy)
    lvl = np.exp(rolling_mean(logp, w))
    yrs = s.years
    m = ~np.isnan(sig) & (sig > 0) & ~np.isnan(lvl)
    if years is not None:
        m &= np.isin(yrs, years)
    if m.sum() < 3 * w:
        return None, sig, lvl
    y, x = np.log(sig[m]), np.log(lvl[m])
    t = (s.ts[m] - s.ts[m][0]) / (365.25 * 86_400)
    a, b, r2 = ols(y, x)
    _, b_t, _ = ols(y, x, t)
    lo, hi = block_bootstrap_slope(y, x, w, reps)
    return (
        LevelFit(
            label,
            source,
            w_days,
            int(m.sum()),
            spearman(x, y),
            pearson(x, y),
            b,
            lo,
            hi,
            r2,
            a,
            b_t,
            float(np.median(sig[m])),
            float(np.median(lvl[m])),
        ),
        sig,
        lvl,
    )


def decile_table(sig: np.ndarray, lvl: np.ndarray, vol: np.ndarray | None = None) -> list[dict]:
    m = ~np.isnan(sig) & ~np.isnan(lvl)
    sig, lvl = sig[m], lvl[m]
    vol = vol[m] if vol is not None else None
    edges = np.percentile(lvl, np.linspace(0, 100, 11))
    rows = []
    for i in range(10):
        lo, hi = edges[i], edges[i + 1]
        sel = (lvl >= lo) & (lvl <= hi) if i == 9 else (lvl >= lo) & (lvl < hi)
        if not sel.any():
            continue
        row = {
            "decile": i + 1,
            "price_lo": float(lo),
            "price_hi": float(hi),
            "n": int(sel.sum()),
            "sigma_p10": float(np.percentile(sig[sel], 10)),
            "sigma_median": float(np.median(sig[sel])),
            "sigma_p90": float(np.percentile(sig[sel], 90)),
        }
        if vol is not None:
            vv = vol[sel]
            vv = vv[~np.isnan(vv)]
            row["volume_median"] = float(np.median(vv)) if vv.size else math.nan
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------------------------------
# part 2: ratio-rule backtest on the real path


def term_grid(lo: int, hi: int, n: int = N_TERMS) -> np.ndarray:
    """Equal-probability grid of a uniform term distribution on ``[lo, hi]`` (midpoints)."""
    q = (np.arange(n) + 0.5) / n
    return np.rint(lo + q * (hi - lo)).astype(np.int64)


def sigma_hat_bps_hourly(p_micro: np.ndarray) -> np.ndarray:
    """SIGMA-1's unclamped annualised σ̂ (bps) on an hourly price used as pFast: volWindow/volStep =
    42 returns, one per hour, annualised with 8,760 (exactly the mainnet kernel at 48 blocks/hour).
    ``-1`` before warm-up."""
    nret = VOL_WINDOW // VOL_STEP
    raw = V.sigma_mult_series(p_micro[None, :], nret, 1, 1, PPY, 2**52)[0]
    und = np.arange(p_micro.size) < nret
    return np.where(und, -1, np.where(raw <= BPS, 0, raw // BPS))


def clamp_mult(sigma_bps: np.ndarray, ref: int, cap: int) -> np.ndarray:
    """``σ̂·10^4 / ref`` clamped to ``[10^4, cap]`` (SIGMA-1), cap where σ̂ is undefined."""
    s = np.asarray(sigma_bps, dtype=np.int64)
    m = np.clip(s * BPS // ref, BPS, cap)
    return np.where(s < 0, cap, m)


@dataclass
class Backtest:
    """``need[s, j]`` = smallest effective ratio (bps) at which the mint at ``s`` with grid term ``j``
    is *not* bad at claim opening; ``nan`` when censored (claim opens after the data ends)."""

    starts: np.ndarray
    need: np.ndarray

    def p_bad(self, eff_bps: np.ndarray, mask: np.ndarray | None = None) -> float:
        """P(bad) for an effective ratio per start (bps, int64), over the uncensored (start, term)."""
        ok = ~np.isnan(self.need)
        if mask is not None:
            ok &= mask[:, None]
        if not ok.any():
            return math.nan
        bad = (eff_bps[:, None] < self.need) & ok
        return float(bad.sum() / ok.sum())


def backtest(p_micro: np.ndarray, cls: str, grace: int = GRACE, skip: int | None = None) -> Backtest:
    lo, hi = CLASS_TERMS[cls]
    terms = term_grid(lo, hi)
    n = p_micro.size
    skip = (VOL_WINDOW // VOL_STEP) if skip is None else skip
    starts = np.arange(skip, n)
    need = np.full((starts.size, terms.size), np.nan)
    ps = p_micro[starts].astype(np.float64)
    for j, T in enumerate(terms.tolist()):
        e = starts + (-(-(T + grace + 1) // BLOCKS_PER_HOUR))
        inside = e < n
        # bad iff coll · p_e < cents · COIN · BPS / ... ⇔ ratio_eff < BPS · p_s / p_e (exact up to rounding)
        need[inside, j] = np.ceil(BPS * ps[inside] / p_micro[e[inside]].astype(np.float64))
    return Backtest(starts, need)


def required_base(
    bt: Backtest, mult: np.ndarray, tol: float, mask: np.ndarray | None = None
) -> tuple[int, float]:
    """Smallest lattice base ratio whose P(bad) ≤ tol under the per-start multiplier ``mult``."""
    for b in LATTICE.tolist():
        eff = np.asarray(b, dtype=np.int64) * mult // BPS
        p = bt.p_bad(eff, mask)
        if p <= tol:
            return b, p
    eff = LATTICE[-1] * mult // BPS
    return -1, bt.p_bad(eff, mask)


def exact_spot_check(
    p_micro: np.ndarray, bt: Backtest, mult: np.ndarray, base: int, cls: str, k: int = 2000
) -> int:
    """Re-run ``k`` random (start, term) cells with the exact integer kernels (wallet collateral at
    $1,000, rounding, ``is_underwater``) and count disagreements with the float ``need`` test."""
    from ybcal.sim import vaults as VB

    rp = VB.RuleParams.of(_params_for(cls, base))
    lo, hi = CLASS_TERMS[cls]
    terms = term_grid(lo, hi)
    rng = np.random.default_rng(1)
    ok = np.argwhere(~np.isnan(bt.need))
    pick = ok[rng.choice(ok.shape[0], min(k, ok.shape[0]), replace=False)]
    si, tj = pick[:, 0], pick[:, 1]
    s = bt.starts[si]
    cents = 100_000
    coll = VB.wallet_collateral(rp, np.full(s.size, cents), np.full(s.size, 0), mult[s], p_micro[s])
    e = s + (-(-(terms[tj] + GRACE + 1) // BLOCKS_PER_HOUR))
    bad_exact = V.is_underwater(coll, p_micro[e], cents, BPS)
    eff = np.asarray(base, dtype=np.int64) * mult[s] // BPS
    bad_float = eff < bt.need[si, tj]
    return int((bad_exact != bad_float).sum())


def _params_for(cls: str, base: int) -> dict:
    """The minimal RuleParams mapping (class A slot carries ``base``)."""
    from ybcal.params.paramset import candidate

    return candidate(**{"baseRatioBps[0]": base})


# ---------------------------------------------------------------------------------------------------
# report


def _fmt(x, d=2):
    if isinstance(x, float):
        if math.isnan(x):
            return "n/a"
        return f"{x:.{d}f}"
    return str(x)


def md_table(rows: list[dict], cols: list[tuple[str, str, int | None]]) -> str:
    head = "| " + " | ".join(c[1] for c in cols) + " |\n|" + "---|" * len(cols) + "\n"
    body = ""
    for r in rows:
        cells = []
        for key, _, d in cols:
            v = r.get(key, "")
            if isinstance(v, float) and d is not None:
                cells.append(_fmt(v, d))
            elif isinstance(v, float):
                cells.append(_fmt(v))
            else:
                cells.append(str(v))
        body += "| " + " | ".join(cells) + " |\n"
    return head + body


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def monthly_index(ts: np.ndarray) -> np.ndarray:
    d = [dt.datetime.fromtimestamp(int(t), dt.UTC) for t in ts]
    return np.array([x.year * 12 + x.month - 1 for x in d])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--reps", type=int, default=300, help="bootstrap replications for the elasticity CI")
    ap.add_argument("--no-figures", action="store_true")
    args = ap.parse_args(argv)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    sums = check_sums(args.data_dir)
    if not all(sums.values()):
        raise SystemExit(f"SHA256 mismatch: {sums}")
    daily = load(args.data_dir / "yec-daily.csv", 86_400)
    hourly = load(args.data_dir / "yec-hourly.csv", 3_600)
    summary: dict = {
        "checksums": sums,
        "daily": {"rows": int(daily.ts.size), "first": daily.iso(0), "last": daily.iso(-1)},
        "hourly": {"rows": int(hourly.ts.size), "first": hourly.iso(0), "last": hourly.iso(-1)},
    }

    # ---- part 1 ---------------------------------------------------------------------------------
    fits: list[LevelFit] = []
    series_cache: dict[tuple[str, int], tuple[np.ndarray, np.ndarray]] = {}
    for src, s in (("daily", daily), ("hourly", hourly)):
        for w in WINDOWS_D:
            f, sig, lvl = fit_level(s, w, "all", src, None, args.reps)
            series_cache[(src, w)] = (sig, lvl)
            if f:
                fits.append(f)
            for label, y0, y1 in SUBPERIODS:
                f, _, _ = fit_level(s, w, label, src, (y0, y1), max(60, args.reps // 3))
                if f:
                    fits.append(f)
    fit_rows = [f.__dict__ for f in fits]
    write_csv(out / "level_fits.csv", fit_rows)

    sig30d, lvl30d = series_cache[("daily", 30)]
    dec_daily = decile_table(sig30d, lvl30d, rolling_mean(daily.volume, 30))
    write_csv(out / "deciles_daily_30d.csv", dec_daily)
    sig30h, lvl30h = series_cache[("hourly", 30)]
    dec_hourly = decile_table(sig30h, lvl30h)
    write_csv(out / "deciles_hourly_30d.csv", dec_hourly)

    # volume: σ vs volume (daily, where present) and the partial correlation given price
    vm = ~np.isnan(sig30d) & ~np.isnan(daily.volume) & (daily.volume > 0)
    vol30 = rolling_mean(np.where(daily.volume > 0, daily.volume, np.nan), 30)
    vm = ~np.isnan(sig30d) & ~np.isnan(vol30) & ~np.isnan(lvl30d)
    if vm.sum() > 100:
        y, xv, xp = np.log(sig30d[vm]), np.log(vol30[vm]), np.log(lvl30d[vm])
        X = np.column_stack([np.ones_like(y), xp, xv])
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        summary["volume"] = {
            "n": int(vm.sum()),
            "spearman_sigma_volume": spearman(xv, y),
            "spearman_volume_price": spearman(xv, xp),
            "elasticity_price_given_volume": float(beta[1]),
            "elasticity_volume_given_price": float(beta[2]),
            "volume_first": daily.iso(int(np.argmax(vm))),
        }

    hv = np.where(hourly.volume > 0, hourly.volume, np.nan)
    hv30 = rolling_mean(hv, 24 * 30)
    hm = ~np.isnan(sig30h) & ~np.isnan(hv30) & ~np.isnan(lvl30h) & (sig30h > 0)
    if hm.sum() > 1000:
        y, xv, xp = np.log(sig30h[hm]), np.log(hv30[hm]), np.log(lvl30h[hm])
        X = np.column_stack([np.ones_like(y), xp, xv])
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        summary["volume_hourly_last_year"] = {
            "n": int(hm.sum()),
            "first": hourly.iso(int(np.argmax(hm))),
            "spearman_sigma_volume": spearman(xv, y),
            "spearman_volume_price": spearman(xv, xp),
            "elasticity_price_given_volume": float(beta[1]),
            "elasticity_volume_given_price": float(beta[2]),
        }

    # ---- part 2 ---------------------------------------------------------------------------------
    p_micro = np.rint(hourly.price * 1_000_000).astype(np.int64)
    sig_hat = sigma_hat_bps_hourly(p_micro)  # bps, -1 before warm-up
    full_fit = next(f for f in fits if f.source == "hourly" and f.window_days == 30 and f.label == "all")
    # out-of-sample model: fitted on hourly 30-day windows to end-2022 only
    f_oos, _, _ = fit_level(hourly, 30, "to-2022", "hourly", (2020, 2021, 2022), args.reps // 3)
    summary["models"] = {}
    rules: dict[str, np.ndarray] = {}
    price_usd = hourly.price

    def level_sigma_bps(f: LevelFit) -> np.ndarray:
        A, b = f.model()
        return np.rint(A * np.power(price_usd, -b) * BPS).astype(np.int64)

    for ref, cap, tag in (
        (SIGMA_REF_H3, SIGMA_CAP_H3, "h3"),
        (SIGMA_REF_SHIPPED, SIGMA_CAP_SHIPPED, "shipped"),
    ):
        rules[f"current[{tag}]"] = clamp_mult(sig_hat, ref, cap)
        lv = level_sigma_bps(full_fit)
        rules[f"level-insample[{tag}]"] = clamp_mult(lv, ref, cap)
        if f_oos:
            lo_ = level_sigma_bps(f_oos)
            rules[f"level-to2022[{tag}]"] = clamp_mult(lo_, ref, cap)
        rules[f"blend-max[{tag}]"] = clamp_mult(np.where(sig_hat < 0, lv, np.maximum(sig_hat, lv)), ref, cap)
        geo = np.where(sig_hat < 0, lv, np.rint(np.sqrt(np.maximum(sig_hat, 1) * lv)).astype(np.int64))
        rules[f"blend-geo[{tag}]"] = clamp_mult(geo, ref, cap)
    # the owner's hypothesised direction: σ falls as the price rises, σ̂(p) = σ_med·(p/p_med)^(−b), b > 0,
    # anchored so that σ̂ at the median price equals the kernel's median σ̂ (no fit: the elasticity is assumed)
    defined = sig_hat >= 0
    sig_med, p_med = float(np.median(sig_hat[defined])), float(np.median(price_usd[defined]))
    summary["anchor"] = {"sigma_hat_median_bps": sig_med, "price_median_usd": p_med}
    for b_h in (0.5, 1.0):
        hyp = np.rint(sig_med * np.power(price_usd / p_med, -b_h)).astype(np.int64)
        rules[f"hypothesis-b{b_h:g}[h3]"] = clamp_mult(hyp, SIGMA_REF_H3, SIGMA_CAP_H3)
        rules[f"hypothesis-b{b_h:g}-blend-max[h3]"] = clamp_mult(
            np.where(sig_hat < 0, hyp, np.maximum(sig_hat, hyp)), SIGMA_REF_H3, SIGMA_CAP_H3
        )
    rules["fixed-1x"] = np.full(p_micro.size, BPS, dtype=np.int64)
    A_, b_ = full_fit.model()
    summary["models"]["insample"] = {"A_at_1usd": A_, "b": b_, "fit": full_fit.__dict__}
    if f_oos:
        A2, b2 = f_oos.model()
        summary["models"]["to2022"] = {"A_at_1usd": A2, "b": b2, "fit": f_oos.__dict__}

    bt = {c: backtest(p_micro, c) for c in ("A", "B", "C")}
    yrs_start = hourly.years[bt["A"].starts]
    last_ts = hourly.ts[-1]
    masks = {
        "full": np.ones(bt["A"].starts.size, dtype=bool),
        "last365": hourly.ts[bt["A"].starts] >= last_ts - 365 * 86_400,
        "2021-22": np.isin(yrs_start, (2021, 2022)),
        "2025-26": np.isin(yrs_start, (2025, 2026)),
    }
    rule_rows = []
    for name, mult in rules.items():
        m_s = mult[bt["A"].starts]
        for win, mask in masks.items():
            for tol in (0.005, 0.01, 0.02):
                base, p = required_base(bt["A"], m_s, tol, mask)
                ok = mask & ~np.isnan(bt["A"].need).all(axis=1)
                eff = (
                    (np.asarray(base, dtype=np.int64) * m_s // BPS).astype(np.float64)[ok]
                    if base > 0
                    else np.array([np.nan])
                )
                rule_rows.append(
                    {
                        "rule": name,
                        "window": win,
                        "tolerance": tol,
                        "class": "A",
                        "required_base_bps": base,
                        "p_bad_at_required": p,
                        "eff_mean_bps": float(eff.mean()),
                        "eff_median_bps": float(np.median(eff)),
                        "eff_p10_bps": float(np.percentile(eff, 10)),
                        "eff_p90_bps": float(np.percentile(eff, 90)),
                        "yed_per_usd_locked_mean": float((BPS / eff).mean()) if base > 0 else math.nan,
                        "mult_mean": float(m_s[ok].mean() / BPS),
                        "mult_at_floor_frac": float((m_s[ok] <= BPS).mean()),
                        "mult_at_cap_frac": float(
                            (m_s[ok] >= max(SIGMA_CAP_H3 if "h3" in name else SIGMA_CAP_SHIPPED, BPS)).mean()
                        ),
                    }
                )
    write_csv(out / "rules_backtest.csv", rule_rows)

    # classes B and C under the two main rules, full window, their own tolerance (mostly censored)
    bc_rows = []
    for c in ("B", "C"):
        for name in ("current[h3]", "level-insample[h3]", "fixed-1x"):
            m_s = rules[name][bt[c].starts]
            base, p = required_base(bt[c], m_s, TOLERANCE[c])
            unc = int((~np.isnan(bt[c].need)).sum())
            bc_rows.append(
                {
                    "class": c,
                    "rule": name,
                    "tolerance": TOLERANCE[c],
                    "required_base_bps": base,
                    "p_bad_at_required": p,
                    "uncensored_cells": unc,
                }
            )
    write_csv(out / "classes_bc.csv", bc_rows)

    # exact-kernel spot check of the float bad-debt test at the current rule's full-window need
    cur = next(
        r
        for r in rule_rows
        if r["rule"] == "current[h3]" and r["window"] == "full" and r["tolerance"] == 0.005
    )
    if cur["required_base_bps"] > 0:
        summary["exact_spot_check_disagreements"] = exact_spot_check(
            p_micro, bt["A"], rules["current[h3]"], cur["required_base_bps"], "A"
        )

    # monthly tracking: effective ratio per rule (at its full/0.5 % base) vs realised forward need
    months = monthly_index(hourly.ts[bt["A"].starts])
    um = np.unique(months)
    track = []
    with np.errstate(all="ignore"):
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            need_p = np.nanpercentile(bt["A"].need, 99, axis=1)
    need_max = np.nanmax(np.where(np.isnan(bt["A"].need), -np.inf, bt["A"].need), axis=1)
    need_max[np.isinf(need_max)] = np.nan
    bases = {
        r["rule"]: r["required_base_bps"]
        for r in rule_rows
        if r["window"] == "full" and r["tolerance"] == 0.005
    }
    for mo in um.tolist():
        sel = months == mo
        row = {
            "month": f"{mo // 12:04d}-{mo % 12 + 1:02d}",
            "price_mean": float(price_usd[bt["A"].starts][sel].mean()),
            "sigma_hat_median": float(
                np.median(sig_hat[bt["A"].starts][sel][sig_hat[bt["A"].starts][sel] >= 0]) / BPS
            )
            if (sig_hat[bt["A"].starts][sel] >= 0).any()
            else math.nan,
            "need_p99_bps": float(np.nanmedian(need_p[sel]))
            if not np.all(np.isnan(need_p[sel]))
            else math.nan,
            "need_max_bps": float(np.nanmax(need_max[sel]))
            if not np.all(np.isnan(need_max[sel]))
            else math.nan,
        }
        for name in (
            "current[h3]",
            "level-insample[h3]",
            "level-to2022[h3]",
            "blend-max[h3]",
            "hypothesis-b0.5[h3]",
            "fixed-1x",
        ):
            if name in bases and bases[name] > 0:
                row[f"eff_{name}"] = float((bases[name] * rules[name][bt["A"].starts][sel] // BPS).mean())
        for name in ("current[h3]", "level-insample[h3]", "hypothesis-b0.5[h3]", "fixed-1x"):
            if name in bases and bases[name] > 0:
                eff = bases[name] * rules[name][bt["A"].starts] // BPS
                row[f"pbad_{name}"] = bt["A"].p_bad(eff, sel)
        track.append(row)
    write_csv(out / "monthly_tracking.csv", track)

    # episodes from the daily series
    def episode(y0: int, y1: int) -> dict:
        m = np.isin(daily.years, (y0, y1))
        idx = np.nonzero(m)[0]
        pk = idx[np.argmax(daily.price[idx])]
        after = idx[idx >= pk]
        tr = after[np.argmin(daily.price[after])]
        return {
            "years": f"{y0}-{y1}",
            "peak_date": daily.iso(pk),
            "peak_usd": float(daily.price[pk]),
            "trough_date": daily.iso(tr),
            "trough_usd": float(daily.price[tr]),
            "drawdown": float(1 - daily.price[tr] / daily.price[pk]),
            "sigma30_at_peak": float(sig30d[pk]) if not np.isnan(sig30d[pk]) else math.nan,
            "sigma30_max_after": float(np.nanmax(sig30d[pk : tr + 1])) if tr > pk else math.nan,
        }

    summary["episodes"] = [episode(2021, 2021), episode(2025, 2026)]
    summary["rules_full_tol0.005"] = {
        r["rule"]: {
            k: r[k]
            for k in (
                "required_base_bps",
                "p_bad_at_required",
                "eff_mean_bps",
                "eff_median_bps",
                "yed_per_usd_locked_mean",
            )
        }
        for r in rule_rows
        if r["window"] == "full" and r["tolerance"] == 0.005
    }
    summary["rules_last365_tol0.005"] = {
        r["rule"]: {k: r[k] for k in ("required_base_bps", "p_bad_at_required", "eff_mean_bps")}
        for r in rule_rows
        if r["window"] == "last365" and r["tolerance"] == 0.005
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))

    if not args.no_figures:
        figures(
            out, daily, hourly, series_cache, fits, full_fit, f_oos, sig_hat, track, rules, bt["A"], bases
        )

    # markdown fragments for the README
    frag = []
    frag.append(
        "### Level fits\n\n"
        + md_table(
            sorted(fit_rows, key=lambda r: (r["source"], r["window_days"], r["label"] != "all", r["label"])),
            [
                ("source", "series", None),
                ("window_days", "window (d)", None),
                ("label", "period", None),
                ("n", "n", None),
                ("spearman", "Spearman", 3),
                ("pearson_log", "Pearson (logs)", 3),
                ("elasticity", "elasticity b", 3),
                ("ci_lo", "90 % CI lo", 3),
                ("ci_hi", "90 % CI hi", 3),
                ("elasticity_time_ctrl", "b | time", 3),
                ("r2", "R²", 3),
                ("sigma_med", "median σ", 2),
                ("price_med", "median p ($)", 3),
            ],
        )
    )
    frag.append(
        "### Price deciles, daily 30-day σ\n\n"
        + md_table(
            dec_daily,
            [
                ("decile", "decile", None),
                ("price_lo", "p from ($)", 3),
                ("price_hi", "p to ($)", 3),
                ("n", "n", None),
                ("sigma_p10", "σ p10", 2),
                ("sigma_median", "σ median", 2),
                ("sigma_p90", "σ p90", 2),
                ("volume_median", "volume 24h median ($)", 0),
            ],
        )
    )
    frag.append(
        "### Price deciles, hourly 30-day σ\n\n"
        + md_table(
            dec_hourly,
            [
                ("decile", "decile", None),
                ("price_lo", "p from ($)", 3),
                ("price_hi", "p to ($)", 3),
                ("n", "n", None),
                ("sigma_p10", "σ p10", 2),
                ("sigma_median", "σ median", 2),
                ("sigma_p90", "σ p90", 2),
            ],
        )
    )
    frag.append(
        "### Rules backtest (class A)\n\n"
        + md_table(
            rule_rows,
            [
                ("rule", "rule", None),
                ("window", "window", None),
                ("tolerance", "tol", 3),
                ("required_base_bps", "base bps", None),
                ("p_bad_at_required", "P(bad)", 4),
                ("eff_mean_bps", "eff mean", 0),
                ("eff_median_bps", "eff median", 0),
                ("eff_p10_bps", "eff p10", 0),
                ("eff_p90_bps", "eff p90", 0),
                ("yed_per_usd_locked_mean", "YED/$ locked", 4),
                ("mult_mean", "mult mean", 3),
                ("mult_at_floor_frac", "at 1×", 3),
                ("mult_at_cap_frac", "at cap", 3),
            ],
        )
    )
    frag.append(
        "### Classes B and C (full window, own tolerance)\n\n"
        + md_table(
            bc_rows,
            [
                ("class", "class", None),
                ("rule", "rule", None),
                ("tolerance", "tol", 3),
                ("required_base_bps", "base bps", None),
                ("p_bad_at_required", "P(bad)", 4),
                ("uncensored_cells", "uncensored cells", None),
            ],
        )
    )
    (out / "tables.md").write_text("\n\n".join(frag))
    print(
        json.dumps(
            {
                k: summary[k]
                for k in (
                    "daily",
                    "hourly",
                    "episodes",
                    "rules_full_tol0.005",
                    "rules_last365_tol0.005",
                    "volume",
                    "volume_hourly_last_year",
                    "anchor",
                    "exact_spot_check_disagreements",
                )
                if k in summary
            },
            indent=1,
            default=str,
        )
    )
    return 0


def figures(out, daily, hourly, cache, fits, full_fit, f_oos, sig_hat, track, rules, btA, bases):
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

    # F1: scatter log σ vs log p, daily 30 d, coloured by sub-period (3 slots + muted for the 4th)
    sig, lvl = cache[("daily", 30)]
    yrs = daily.years
    fig, ax = plt.subplots(figsize=(7, 4.5))
    cols = {"2019-20": MUTED, "2021-22": C1, "2023-24": C2, "2025-26": C3}
    for label, y0, y1 in SUBPERIODS:
        m = np.isin(yrs, (y0, y1)) & ~np.isnan(sig) & ~np.isnan(lvl)
        ax.scatter(lvl[m], sig[m], s=6, alpha=0.45, color=cols[label], label=label, linewidths=0)
    f_d = next(f for f in fits if f.source == "daily" and f.window_days == 30 and f.label == "all")
    Ad, bd = f_d.model()
    xs = np.logspace(np.log10(np.nanmin(lvl)), np.log10(np.nanmax(lvl)), 100)
    ax.plot(xs, Ad * xs ** (-bd), color=INK, lw=2, label=f"fit daily: σ = {Ad:.2f}·p^({-bd:+.2f})")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("30-day mean price ($, log)")
    ax.set_ylabel("30-day realised σ, annualised (log)")
    ax.set_title("YEC: realised volatility vs price level, daily data 2019-2026", loc="left")
    ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(out / "fig1_sigma_vs_price_daily.png", dpi=140)
    plt.close(fig)

    # F2: decile medians, daily and hourly
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.6), sharey=True)
    for ax, (src, color) in zip(axes, (("daily", C1), ("hourly", C2)), strict=True):
        s_, l_ = cache[(src, 30)]
        rows = decile_table(s_, l_)
        x = np.arange(len(rows))
        med = [r["sigma_median"] for r in rows]
        ax.bar(x, med, color=color, width=0.7)
        ax.vlines(x, [r["sigma_p10"] for r in rows], [r["sigma_p90"] for r in rows], color=INK, lw=1)
        ax.set_xticks(x)
        ax.set_xticklabels([f"${r['price_lo']:.2f}" for r in rows], rotation=45, ha="right", fontsize=7)
        ax.set_title(f"{src}: median 30-day σ by price decile (bars p10-p90)", loc="left", fontsize=9)
        ax.set_xlabel("decile lower edge")
    axes[0].set_ylabel("annualised σ")
    fig.tight_layout()
    fig.savefig(out / "fig2_deciles.png", dpi=140)
    plt.close(fig)

    # F3: time series — price, realised σ (30 d daily), σ̂(p) model, SIGMA-1 σ̂ (42 h) monthly median
    fig, axes = plt.subplots(2, 1, figsize=(9, 6), sharex=True)
    td = [dt.datetime.fromtimestamp(int(t), dt.UTC) for t in daily.ts]
    axes[0].plot(td, daily.price, color=INK, lw=1.2)
    axes[0].set_yscale("log")
    axes[0].set_ylabel("price ($, log)")
    axes[0].set_title("YEC price and volatility, 2019-2026", loc="left")
    axes[1].plot(td, sig, color=C1, lw=1.4, label="realised σ, 30 d daily")
    axes[1].plot(td, Ad * daily.price ** (-bd), color=C2, lw=1.4, label="σ̂(p) = A·p^(−b), daily fit")
    th = [dt.datetime.fromtimestamp(int(t), dt.UTC) for t in hourly.ts]
    sh = np.where(sig_hat < 0, np.nan, sig_hat / BPS)
    sh_m = rolling_mean(sh, 24 * 30)
    axes[1].plot(th, sh_m, color=C3, lw=1.0, label="SIGMA-1 σ̂ (42 h kernel), 30-day mean")
    axes[1].axhline(SIGMA_REF_H3 / BPS, color=MUTED, ls="--", lw=1)
    axes[1].annotate("σref 180 %", (td[5], SIGMA_REF_H3 / BPS * 1.05), color=MUTED, fontsize=8)
    axes[1].set_ylabel("annualised σ")
    axes[1].set_ylim(0, min(6, float(np.nanmax(sig)) * 1.1))
    axes[1].legend(frameon=False, fontsize=8, loc="upper right")
    fig.tight_layout()
    fig.savefig(out / "fig3_timeseries.png", dpi=140)
    plt.close(fig)

    # F4: monthly effective ratio per rule vs realised forward need
    fig, ax = plt.subplots(figsize=(9, 4.2))
    tm = [dt.datetime.strptime(r["month"], "%Y-%m").replace(tzinfo=dt.UTC) for r in track]
    series = (
        ("eff_current[h3]", C1, "current rule (σ̂/σref, 42 h)"),
        ("eff_level-insample[h3]", C2, "fitted level rule σ̂(p), b = −0.26"),
        ("eff_hypothesis-b0.5[h3]", C3, "hypothesis σ̂ ∝ p^(−0.5)"),
        ("eff_fixed-1x", MUTED, "fixed 1×"),
    )
    for key, color, label in series:
        if key in track[0]:
            ax.plot(tm, [r.get(key, np.nan) / 100 for r in track], color=color, lw=1.6, label=label)
    ax.plot(
        tm,
        [r["need_max_bps"] / 100 for r in track],
        color=INK,
        lw=1,
        ls=":",
        label="worst realised need (max p_mint/p_claim)",
    )
    ax.plot(tm, [r["need_p99_bps"] / 100 for r in track], color=INK, lw=1, label="realised need p99")
    ax.set_yscale("log")
    ax.set_ylabel("effective ratio (%)")
    ax.set_title(
        "Class A: effective ratio each rule would have required (base sized to 0.5 % on the full window)",
        loc="left",
    )
    ax.legend(frameon=False, fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(out / "fig4_tracking.png", dpi=140)
    plt.close(fig)

    # F5: elasticity by sub-period and window
    fig, ax = plt.subplots(figsize=(7, 3.8))
    labs = ["all", *[s[0] for s in SUBPERIODS]]
    for k, (src, color) in enumerate((("daily", C1), ("hourly", C2))):
        for w, mk in zip(WINDOWS_D, ("o", "s", "^"), strict=True):
            pts = [
                next((f for f in fits if f.source == src and f.window_days == w and f.label == lab), None)
                for lab in labs
            ]
            x = np.arange(len(labs)) + (k * 3 + WINDOWS_D.index(w)) * 0.09 - 0.22
            ys = [p.elasticity if p else np.nan for p in pts]
            lo = [p.elasticity - p.ci_lo if p else 0 for p in pts]
            hi = [p.ci_hi - p.elasticity if p else 0 for p in pts]
            ax.errorbar(
                x, ys, yerr=[lo, hi], fmt=mk, color=color, ms=5, lw=1, capsize=2, label=f"{src} {w} d"
            )
    ax.axhline(0, color=MUTED, lw=1)
    ax.set_xticks(np.arange(len(labs)))
    ax.set_xticklabels(labs)
    ax.set_ylabel("elasticity d log σ / d log p (90 % CI)")
    ax.set_title("Is σ lower when the price is higher? Elasticity per period", loc="left")
    ax.legend(frameon=False, fontsize=7, ncol=3)
    fig.tight_layout()
    fig.savefig(out / "fig5_elasticity.png", dpi=140)
    plt.close(fig)


if __name__ == "__main__":
    raise SystemExit(main())

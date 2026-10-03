"""SIGMA-1 series and multiplier statistics (owner: WP-3; PLAN §5.2, fact 1.5-3).

The series is ``Snapshots[H].sigmaMultBps`` (state.cpp:1209-1220 @ 7702d22): samples ``s_0`` = this
SNAP's pFast and ``s_k = Snapshots[H − k·volStep].pFast`` for ``k = 1 … volWindow/volStep``; a
virtual (below ``startHeight``) or undefined sample makes the multiplier ``sigmaMultMaxBps`` (K12);
``sigmaRefBps ≤ 0`` fixes it at 10^4 (regtest default). The arithmetic is
``ybcal.model.vkernels.sigma_mult_series`` (property-tested equal to ``kernels.sigma_mult_bps``);
nothing here reimplements it.

The helpers turn a series into the G2 metrics: the multiplier distribution, time at the cap (split
into "cap because a sample was undefined" and "cap because volatility is high"), estimator noise,
and the responsiveness after a volatility regime change.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from ybcal.model import vkernels as V
from ybcal.units import BLOCKS_PER_HOUR, BLOCKS_PER_YEAR, BPS

OWNER_WP = "WP-3"


def sigma_series(params: Mapping, p_fast) -> np.ndarray:
    """SIGMA-1 multiplier (bps, int64) at every block of a pFast series ``(..., n)`` whose element 0
    is ``startHeight`` (undefined pFast ``<= 0``)."""
    return V.sigma_mult_series(
        p_fast,
        int(params["volWindow"]),
        int(params["volStep"]),
        int(params["sigmaRefBps"]),
        int(params["volPeriodsPerYear"]),
        int(params["sigmaMultMaxBps"]),
    )


def undefined_sample_mask(params: Mapping, p_fast) -> np.ndarray:
    """True where SIGMA-1 is pinned at the cap because a sample is undefined or virtual (K12),
    regardless of the volatility (fact 1.5-3)."""
    pf = np.asarray(p_fast, dtype=np.int64)
    step, nret = int(params["volStep"]), int(params["volWindow"]) // max(int(params["volStep"]), 1)
    n = pf.shape[-1]
    if int(params["sigmaRefBps"]) <= 0:
        return np.zeros(pf.shape, dtype=bool)
    if nret < 1:
        return np.ones(pf.shape, dtype=bool)
    bad = (pf <= 0).astype(np.int64)
    # count undefined among s_0..s_nret, i.e. indices i, i-step, …, i-nret·step
    cs = np.zeros(pf.shape, dtype=np.int64)
    for r in range(step):
        cs[..., r::step] = np.cumsum(bad[..., r::step], axis=-1)
    span = (nret + 1) * step
    cnt = cs.copy()
    if span < n:
        cnt[..., span:] -= cs[..., : n - span]
    idx = np.arange(n)
    virtual = idx - nret * step < 0
    return (cnt > 0) | virtual


@dataclass(frozen=True)
class MultiplierStats:
    """Distribution of the multiplier over the defined (post-warm-up) blocks, in multiples of 1×."""

    p05: float
    p50: float
    p90: float
    p95: float
    p99: float
    mean: float
    at_cap_fraction: float  # all reasons
    cap_from_undefined_fraction: float  # K12: an undefined/virtual sample
    cap_from_vol_fraction: float  # every sample defined and σ·10^4/ref ≥ cap
    at_floor_fraction: float  # clamped at 1×
    blocks: int

    @property
    def cap_hours_per_year(self) -> float:
        return self.at_cap_fraction * BLOCKS_PER_YEAR / BLOCKS_PER_HOUR

    @property
    def cap_from_undefined_hours_per_year(self) -> float:
        return self.cap_from_undefined_fraction * BLOCKS_PER_YEAR / BLOCKS_PER_HOUR

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["cap_hours_per_year"] = self.cap_hours_per_year
        d["cap_from_undefined_hours_per_year"] = self.cap_from_undefined_hours_per_year
        return d


def multiplier_stats(
    params: Mapping, sigma_mult, p_fast=None, *, skip_warmup: bool = True
) -> MultiplierStats:
    """Distribution and time-at-cap of a multiplier series ``(..., n)``. With ``skip_warmup`` the
    first ``volWindow`` blocks (always at the cap, fact 1.5-3) are excluded."""
    s = np.asarray(sigma_mult, dtype=np.int64)
    n = s.shape[-1]
    lo = min(int(params["volWindow"]), n - 1) if skip_warmup else 0
    body = s[..., lo:]
    cap = max(int(params["sigmaMultMaxBps"]), BPS)
    at_cap = body >= cap
    if p_fast is not None:
        und = undefined_sample_mask(params, p_fast)[..., lo:]
    else:
        und = np.zeros(body.shape, dtype=bool)
    m = body.astype(np.float64) / BPS
    q = np.percentile(m, [5, 50, 90, 95, 99]) if m.size else [np.nan] * 5
    tot = max(body.size, 1)
    return MultiplierStats(
        p05=float(q[0]),
        p50=float(q[1]),
        p90=float(q[2]),
        p95=float(q[3]),
        p99=float(q[4]),
        mean=float(m.mean()) if m.size else float("nan"),
        at_cap_fraction=float(at_cap.sum() / tot),
        cap_from_undefined_fraction=float((at_cap & und).sum() / tot),
        cap_from_vol_fraction=float((at_cap & ~und).sum() / tot),
        at_floor_fraction=float((body <= BPS).sum() / tot),
        blocks=int(body.size),
    )


def time_at_cap(params: Mapping, sigma_mult) -> np.ndarray:
    """Fraction of blocks at the cap, per path (``(paths,)``)."""
    s = np.asarray(sigma_mult, dtype=np.int64)
    s2 = s.reshape(-1, s.shape[-1])
    return (s2 >= max(int(params["sigmaMultMaxBps"]), BPS)).mean(axis=1)


def sigma_hat_bps(params: Mapping, p_fast) -> np.ndarray:
    """The *unclamped* annualised σ̂ (bps) SIGMA-1 computes before dividing by ``sigmaRefBps`` —
    obtained exactly from the kernel by running it with ``sigmaRefBps = 1`` (so the result is
    ``σ̂·10^4``, above the 1× floor for any σ̂ ≥ 1 bps) and a huge cap, then dividing by 10^4.
    ``-1`` where a sample is undefined. Used for the estimator-noise metric (CV of σ̂)."""
    pf = np.asarray(p_fast, dtype=np.int64)
    raw = V.sigma_mult_series(
        pf, int(params["volWindow"]), int(params["volStep"]), 1, int(params["volPeriodsPerYear"]), 2**52
    )
    und = undefined_sample_mask(params, pf)
    return np.where(und, -1, np.where(raw <= BPS, 0, raw // BPS))


def estimator_cv(params: Mapping, p_fast, *, skip_warmup: bool = True) -> float:
    """Coefficient of variation of σ̂ over the defined blocks (G2 estimator noise; meaningful on a
    stationary regime). Values clamped at the 1× floor are included as computed (unclamped)."""
    sh = sigma_hat_bps(params, p_fast)
    lo = int(params["volWindow"]) if skip_warmup else 0
    v = sh[..., lo:]
    v = v[v >= 0].astype(np.float64)
    if v.size < 2 or v.mean() == 0:
        return float("nan")
    return float(v.std() / v.mean())


def responsiveness_blocks(
    sigma_mult, change_index: int, *, frac: float = 0.9, steady_from: int | None = None
) -> np.ndarray:
    """Blocks after ``change_index`` (a volatility regime change) until the multiplier first covers
    ``frac`` of the way from its pre-change level to its new steady state, per path (``nan`` if
    never). Pre-change level = median over ``[change − 1·(steady span), change)``; new steady state =
    median over ``[steady_from, n)`` (default: the last quarter of the series)."""
    s = np.asarray(sigma_mult, dtype=np.float64)
    s2 = s.reshape(-1, s.shape[-1])
    n = s2.shape[1]
    sf = steady_from if steady_from is not None else max(change_index + 1, n - n // 4)
    span = max(1, n - sf)
    before = np.median(s2[:, max(0, change_index - span) : change_index], axis=1)
    after = np.median(s2[:, sf:], axis=1)
    target = before + frac * (after - before)
    out = np.full(s2.shape[0], np.nan)
    for r in range(s2.shape[0]):
        seg = s2[r, change_index:]
        hit = seg >= target[r] if after[r] >= before[r] else seg <= target[r]
        if hit.any():
            out[r] = float(hit.argmax())
    return out


def k12_trap_blocks(params: Mapping, p_fast) -> np.ndarray:
    """Per path: blocks pinned at the cap only because of an undefined sample (after warm-up).
    A feed gap of g blocks costs up to ``g + volWindow`` such blocks (fact 1.5-3)."""
    und = undefined_sample_mask(params, p_fast)
    lo = int(params["volWindow"])
    u = und[..., lo:]
    return u.reshape(-1, u.shape[-1]).sum(axis=1)

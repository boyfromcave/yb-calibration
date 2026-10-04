"""Synthetic price, exchange-spread and pool models (PLAN §4.2).

Price models — each a dataclass with

* ``simulate(n_paths, n_steps, dt, rng, p0=DEFAULT_P0) -> PricePath`` (``provenance="synthetic"``),
  where ``dt`` is the step in years (:data:`~ybcal.data.pricepath.DT_BLOCK` or
  :data:`~ybcal.data.pricepath.DT_HOUR`, or the strings ``"block"``/``"hour"``), ``p0`` is the
  starting price in integer µUSD and column 0 of every path equals ``p0``;
* ``log_returns(n_paths, n_steps, dt, rng)`` — the underlying ``(n_paths, n_steps)`` log returns;
* ``fit(PricePath)`` / ``fit_returns(returns, dt)`` classmethods — calibration to real data;
* ``preset()`` — the "YEC-like" default. **The presets are placeholders** (≈ 120 % annualised
  volatility, fat tails, deep multi-month drawdowns), not estimates; ``--calibrate FILE`` replaces
  them with fitted values, and anything simulated from a preset is tagged ``synthetic``.

=====================  ================================================  ==============================
model                  dynamics                                          fit
=====================  ================================================  ==============================
:class:`GBM`           constant μ, σ                                     moments of log returns
:class:`Merton`        GBM + compound-Poisson normal jumps in log price  threshold moments (4·MAD jumps)
:class:`Garch`         GARCH(1,1), standardised Student-t innovations    MLE (scipy L-BFGS-B)
:class:`RegimeSwitch`  2-state Markov (calm/turbulent) GBM               Baum–Welch EM on a Gaussian HMM
:class:`BlockBootstrap` Politis–Romano stationary bootstrap of returns   stores the returns
=====================  ================================================  ==============================

GBM, Merton and the regime switch are continuous-time and simulate at any ``dt``. GARCH and the
bootstrap are discrete-time at the resolution they were fitted on (``dt_native``); simulating at
a finer grid fills each native step with a Brownian bridge whose variance matches that step's
conditional variance (so native-step returns are reproduced exactly), and at a coarser grid sums
native steps. Only integer ratios are supported.

Behaviour models (consumed by the simulator, PLAN §5.5/§5.6/§5.8):

* :class:`SpreadModel` — per-source correlated, persistent quote noise around the true price, with
  staleness (quotes refresh at a Poisson rate) and outages (an alternating renewal process);
  ``fit(SpreadsLog)`` from a ``spreads.py`` log; ``generate`` → :class:`SourceQuotes` (G6/G8).
* :class:`PoolModel` — pools with hashrate shares; who mines each block is a categorical draw; a
  tagging pool publishes its 15-minute TWAP (``lag_blocks`` stale) with per-pool noise, optional
  bias (attackers), frozen quotes (stale pools) and outages; ``fit(PoolShareLog)`` (G1/G5/G6).
* :class:`HashrateDrift` — an Ornstein–Uhlenbeck process on share logits (G5).

Owner: WP-2.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar

import numpy as np
from scipy import optimize, signal, special

from ybcal.data.loaders import PoolShareLog, SpreadsLog
from ybcal.data.pricepath import (
    DT_BLOCK,
    DT_HOUR,
    STEP_SECONDS,
    log_returns,
    resolution_for_dt,
    steps_per_year,
)
from ybcal.types import PricePath, Resolution
from ybcal.units import BLOCK_SECONDS, PRICE_MAX, PRICE_MIN

OWNER_WP = "WP-2"

#: Default start price: $0.40 (YEC traded ≈ $0.43 when yellowback_price.py's presets were verified,
#: 2026-09-05). A placeholder; scenarios and studies pass their own.
DEFAULT_P0: int = 400_000
#: Fixed epoch for synthetic paths (deterministic output; the date carries no meaning).
SYNTH_T0: datetime = datetime(2027, 1, 1, tzinfo=UTC)
#: Marker carried in ``meta`` of every preset-based path.
PLACEHOLDER_NOTE = "YEC-like placeholder preset (not fitted to data)"


# ---------------------------------------------------------------------------------------------------
# Shared helpers


def as_dt(dt: float | str) -> float:
    """``"block"``/``"hour"`` or a year fraction → a year fraction (validated)."""
    if isinstance(dt, str):
        return {"block": DT_BLOCK, "hour": DT_HOUR}[dt]
    resolution_for_dt(float(dt))
    return float(dt)


def returns_to_path(
    r: np.ndarray,
    p0: int,
    dt: float,
    *,
    provenance: str = "synthetic",
    meta: dict[str, Any] | None = None,
    t0: datetime = SYNTH_T0,
) -> PricePath:
    """Cumulate log returns ``(paths, n-1)`` from ``p0`` µUSD into an int64 path ``(paths, n)``.

    Log prices are clipped to ``[log PRICE_MIN, log PRICE_MAX]`` (``params.h``) before rounding.
    """
    r = np.atleast_2d(np.asarray(r, dtype=np.float64))
    lp = np.empty((r.shape[0], r.shape[1] + 1))
    lp[:, 0] = math.log(p0)
    np.cumsum(r, axis=1, out=lp[:, 1:])
    lp[:, 1:] += math.log(p0)
    np.clip(lp, math.log(PRICE_MIN), math.log(PRICE_MAX), out=lp)
    prices = np.rint(np.exp(lp)).astype(np.int64)
    prices = np.clip(prices, PRICE_MIN, PRICE_MAX)
    return PricePath(t0, resolution_for_dt(dt), prices, provenance, dict(meta or {}))  # type: ignore[arg-type]


def fit_returns_of(pp: PricePath, path: int = 0) -> tuple[np.ndarray, float]:
    """Log returns to fit on, and their step in years.

    Grid points flagged in ``meta["filled"]`` (forward-filled by the loader) and gaps are not
    observations: returns are taken between consecutive *observed* points, and only those at the
    modal spacing are kept (so daily data on an hourly grid fits as daily returns, ``dt`` = 1 day).
    """
    p = pp.prices[path]
    step_dt = DT_BLOCK if pp.resolution == "block" else DT_HOUR
    observed = p > 0
    filled = pp.meta.get("filled")
    if isinstance(filled, np.ndarray) and filled.shape[-1] == p.shape[0]:
        f = filled if filled.ndim == 1 else filled[path]
        observed = observed & ~f.astype(bool)
    idx = np.nonzero(observed)[0]
    if len(idx) < 3:
        raise ValueError("fewer than 3 observed prices to fit on")
    lp = np.log(p[idx].astype(np.float64))
    spacing = np.diff(idx)
    vals, counts = np.unique(spacing, return_counts=True)
    m = int(vals[np.argmax(counts)])
    r = np.diff(lp)[spacing == m]
    return drop_stale_runs(r), m * step_dt


#: A run of at least this many consecutive exactly-zero returns is a stale feed, not a market.
STALE_RUN_RETURNS = 6


def drop_stale_runs(r: np.ndarray, min_run: int = STALE_RUN_RETURNS) -> np.ndarray:
    """Drop runs of ≥ ``min_run`` exactly-zero returns and the return that ends each run.

    An aggregator that stops updating (CoinMarketCap's YEC feed held one price for 359 hours in
    2025) prints exact zeros: a GARCH likelihood then drives the variance to ~0 and "fits" a
    model with no volatility, and every σ estimate is biased low. The return that closes the run
    carries the whole stale period's move at one step, so it goes too. Short runs (a quiet hour)
    are kept: they are market behaviour."""
    r = np.asarray(r, dtype=np.float64)
    if min_run <= 0 or len(r) < min_run:
        return r
    zero = r == 0.0
    keep = np.ones(len(r), dtype=bool)
    i = 0
    n = len(r)
    while i < n:
        if zero[i]:
            j = i
            while j < n and zero[j]:
                j += 1
            if j - i >= min_run:
                keep[i : min(j + 1, n)] = False
            i = j
        else:
            i += 1
    return r[keep]


def _native_to_dt(n_steps: int, dt: float, dt_native: float) -> tuple[int, int, int]:
    """(native steps needed, refine factor k ≥ 1, aggregate factor a ≥ 1) for ``n_steps-1`` returns."""
    need = max(n_steps - 1, 0)
    ratio = dt_native / dt
    if abs(ratio - round(ratio)) < 1e-6 and round(ratio) >= 1:
        k = round(ratio)
        return -(-need // k), k, 1
    inv = dt / dt_native
    if abs(inv - round(inv)) < 1e-6 and round(inv) >= 1:
        a = round(inv)
        return need * a, 1, a
    raise ValueError(f"dt {dt} is not an integer multiple or fraction of the native step {dt_native}")


def brownian_bridge(
    native: np.ndarray, var: np.ndarray | float, k: int, rng: np.random.Generator
) -> np.ndarray:
    """Split each native return into ``k`` sub-returns that sum to it exactly.

    Sub-return ``j`` = ``R/k + sqrt(var/k)·(z_j − z̄)``; with ``Var(R) = var`` each sub-return has
    variance ``var/k`` (a Brownian bridge on the native grid).
    """
    if k == 1:
        return native
    paths, m = native.shape
    z = rng.standard_normal((paths, m, k))
    z -= z.mean(axis=2, keepdims=True)
    sd = np.sqrt(np.broadcast_to(np.asarray(var, dtype=np.float64), (paths, m)) / k)
    sub = native[:, :, None] / k + sd[:, :, None] * z
    return sub.reshape(paths, m * k)


def _resize(
    native: np.ndarray, var: np.ndarray | float, n_steps: int, k: int, a: int, rng: np.random.Generator
) -> np.ndarray:
    need = max(n_steps - 1, 0)
    if k > 1:
        return brownian_bridge(native, var, k, rng)[:, :need]
    if a > 1:
        return native.reshape(native.shape[0], need, a).sum(axis=2)
    return native[:, :need]


class PriceModel:
    """Common ``simulate`` on top of each model's ``log_returns``."""

    name: ClassVar[str] = ""

    def log_returns(
        self, n_paths: int, n_steps: int, dt: float | str, rng: np.random.Generator
    ) -> np.ndarray:  # pragma: no cover - abstract
        raise NotImplementedError

    def expected_log_drift(self) -> float:
        """E[log return] per year (used to centre scenario bases)."""
        raise NotImplementedError  # pragma: no cover - overridden

    def params(self) -> dict[str, Any]:
        """The model's parameters (JSON-safe)."""
        d = asdict(self)  # type: ignore[call-overload]
        return {
            k: (v.tolist() if isinstance(v, np.ndarray) else v)
            for k, v in d.items()
            if k not in ("returns", "meta")
        }

    def simulate(
        self,
        n_paths: int,
        n_steps: int,
        dt: float | str,
        rng: np.random.Generator,
        p0: int = DEFAULT_P0,
        *,
        t0: datetime = SYNTH_T0,
    ) -> PricePath:
        """``n_paths`` paths of ``n_steps`` prices (µUSD int64) starting at ``p0``."""
        if n_steps < 1 or n_paths < 1:
            raise ValueError("n_paths and n_steps must be >= 1")
        dtf = as_dt(dt)
        r = self.log_returns(n_paths, n_steps, dtf, rng)[:, : n_steps - 1]
        meta = {"model": self.name, "params": self.params(), "p0": int(p0), "dt_years": dtf}
        src = getattr(self, "meta", {}) or {}
        meta["calibrated"] = bool(src.get("fitted"))
        if not meta["calibrated"]:
            meta["note"] = PLACEHOLDER_NOTE
        meta.update({k: v for k, v in src.items() if k in ("fitted_from", "fitted", "data_provenance")})
        return returns_to_path(r, p0, dtf, meta=meta, t0=t0)

    @classmethod
    def fit(cls, pp: PricePath, path: int = 0) -> PriceModel:
        """Fit to the observed returns of ``pp`` (see :func:`fit_returns_of`)."""
        r, dt = fit_returns_of(pp, path)
        model = cls.fit_returns(r, dt)
        model.meta = {
            "fitted": True,
            "fitted_from": str(pp.meta.get("source_file", pp.provenance)),
            "data_provenance": pp.provenance,
            "n_returns": len(r),
            "dt_fit_years": dt,
        }  # type: ignore[attr-defined]
        return model

    @classmethod
    def fit_returns(cls, r: np.ndarray, dt: float) -> PriceModel:  # pragma: no cover - abstract
        raise NotImplementedError


# ---------------------------------------------------------------------------------------------------
# GBM


@dataclass
class GBM(PriceModel):
    """Geometric Brownian motion: ``dlog p = (μ − σ²/2) dt + σ dW`` (annualised μ, σ)."""

    mu: float = 0.0
    sigma: float = 1.20
    meta: dict[str, Any] = field(default_factory=dict, compare=False)
    name: ClassVar[str] = "gbm"

    @classmethod
    def preset(cls) -> GBM:
        """Placeholder: σ = 120 %/yr, μ = 0 (a martingale price; the median falls ~51 %/yr)."""
        return cls(mu=0.0, sigma=1.20)

    def expected_log_drift(self) -> float:
        return self.mu - 0.5 * self.sigma**2

    def log_returns(
        self, n_paths: int, n_steps: int, dt: float | str, rng: np.random.Generator
    ) -> np.ndarray:
        d = as_dt(dt)
        z = rng.standard_normal((n_paths, max(n_steps - 1, 0)))
        return (self.mu - 0.5 * self.sigma**2) * d + self.sigma * math.sqrt(d) * z

    @classmethod
    def fit_returns(cls, r: np.ndarray, dt: float) -> GBM:
        """Moment matching: σ = sd(r)/√dt, μ = mean(r)/dt + σ²/2."""
        r = r[np.isfinite(r)]
        sigma = float(np.std(r, ddof=1) / math.sqrt(dt))
        return cls(mu=float(np.mean(r) / dt + 0.5 * sigma**2), sigma=sigma)


# ---------------------------------------------------------------------------------------------------
# Merton


@dataclass
class Merton(PriceModel):
    """Merton jump-diffusion: GBM plus Poisson(λ) jumps, each ``N(jump_mu, jump_sigma²)`` in log.

    ``mu`` is the arithmetic drift (jump-compensated: E[p_t] = p_0 e^{μt})."""

    mu: float = 0.0
    sigma: float = 0.95
    lam: float = 12.0  #: jumps per year
    jump_mu: float = -0.02  #: mean log jump
    jump_sigma: float = 0.20  #: sd of the log jump
    meta: dict[str, Any] = field(default_factory=dict, compare=False)
    name: ClassVar[str] = "merton"
    #: Returns further than this many robust sds from the median are classed as jumps when fitting.
    JUMP_THRESHOLD: ClassVar[float] = 4.0

    @classmethod
    def preset(cls) -> Merton:
        """Placeholder: diffusion 95 %, 12 jumps/yr of N(−2 %, 20 %) → total vol ≈ 118 %/yr."""
        return cls()

    @property
    def total_vol(self) -> float:
        """Annualised sd of log returns: √(σ² + λ(m² + s²))."""
        return math.sqrt(self.sigma**2 + self.lam * (self.jump_mu**2 + self.jump_sigma**2))

    def expected_log_drift(self) -> float:
        k = math.exp(self.jump_mu + 0.5 * self.jump_sigma**2) - 1.0
        return self.mu - 0.5 * self.sigma**2 - self.lam * k + self.lam * self.jump_mu

    def log_returns(
        self, n_paths: int, n_steps: int, dt: float | str, rng: np.random.Generator
    ) -> np.ndarray:
        d = as_dt(dt)
        n = max(n_steps - 1, 0)
        k = math.exp(self.jump_mu + 0.5 * self.jump_sigma**2) - 1.0
        drift = (self.mu - 0.5 * self.sigma**2 - self.lam * k) * d
        r = drift + self.sigma * math.sqrt(d) * rng.standard_normal((n_paths, n))
        nj = rng.poisson(self.lam * d, size=(n_paths, n))
        if nj.any():
            r += nj * self.jump_mu + np.sqrt(nj) * self.jump_sigma * rng.standard_normal((n_paths, n))
        return r

    @classmethod
    def fit_returns(cls, r: np.ndarray, dt: float, refine: bool = True) -> Merton:
        """Threshold moments, then (``refine``) a mixture maximum-likelihood polish.

        Step 1: returns beyond 4 robust sds (1.4826·MAD, re-estimated once on the non-jumps) are
        jumps; σ from the rest, λ = jumps/(N·dt), jump moments from the jumps. This misses jumps
        smaller than the threshold, so step 2 maximises the likelihood of the "at most one jump per
        step" mixture ``(1−λdt)·N(m, s²) + λdt·N(m + jump_mu, s² + jump_sigma²)`` from that start
        (valid while λ·dt ≪ 1, as for hourly or daily YEC data).
        """
        base_fit = cls._threshold_fit(r, dt)
        if not refine:
            return base_fit
        r = r[np.isfinite(r)]
        sd0 = base_fit.sigma * math.sqrt(dt)
        p0 = min(max(base_fit.lam * dt, 1e-5), 0.3)
        k0 = math.exp(base_fit.jump_mu + 0.5 * base_fit.jump_sigma**2) - 1.0
        m0 = (base_fit.mu - 0.5 * base_fit.sigma**2 - base_fit.lam * k0) * dt
        js0 = max(base_fit.jump_sigma, 2 * sd0)

        def nll(th: np.ndarray) -> float:
            m, ls, lp, jm, ljs = th
            s2, p = math.exp(2 * ls), 1.0 / (1.0 + math.exp(-lp))
            v2 = s2 + math.exp(2 * ljs)
            a = (1 - p) * np.exp(-0.5 * (r - m) ** 2 / s2) / math.sqrt(2 * math.pi * s2)
            b = p * np.exp(-0.5 * (r - m - jm) ** 2 / v2) / math.sqrt(2 * math.pi * v2)
            return -float(np.sum(np.log(a + b + 1e-300)))

        th0 = np.array([m0, math.log(sd0), math.log(p0 / (1 - p0)), base_fit.jump_mu, math.log(js0)])
        res = optimize.minimize(
            nll, th0, method="Nelder-Mead", options={"maxiter": 4000, "xatol": 1e-7, "fatol": 1e-4}
        )
        if not (res.success or res.status == 2) or res.fun > nll(th0):
            return base_fit
        m, ls, lp, jm, ljs = (float(v) for v in res.x)
        sigma = math.exp(ls) / math.sqrt(dt)
        lam = 1.0 / (1.0 + math.exp(-lp)) / dt
        js = math.exp(ljs)
        k = math.exp(jm + 0.5 * js**2) - 1.0
        mu = m / dt + 0.5 * sigma**2 + lam * k
        return cls(mu=mu, sigma=sigma, lam=lam, jump_mu=jm, jump_sigma=js)

    @classmethod
    def _threshold_fit(cls, r: np.ndarray, dt: float) -> Merton:
        """Step 1 of :meth:`fit_returns` (threshold moments)."""
        r = r[np.isfinite(r)]
        med = float(np.median(r))
        s = 1.4826 * float(np.median(np.abs(r - med))) or float(np.std(r))
        jump = np.abs(r - med) > cls.JUMP_THRESHOLD * s
        s = float(np.std(r[~jump], ddof=1))
        jump = np.abs(r - med) > cls.JUMP_THRESHOLD * s
        base = r[~jump]
        sigma = float(np.std(base, ddof=1) / math.sqrt(dt))
        nj = int(jump.sum())
        lam = nj / (len(r) * dt)
        jm = float(np.mean(r[jump]) - np.mean(base)) if nj else 0.0
        js = float(np.std(r[jump], ddof=1)) if nj > 1 else 0.0
        k = math.exp(jm + 0.5 * js**2) - 1.0
        mu = float(np.mean(r)) / dt + 0.5 * sigma**2 + lam * k - lam * jm
        return cls(mu=mu, sigma=sigma, lam=lam, jump_mu=jm, jump_sigma=js)


# ---------------------------------------------------------------------------------------------------
# GARCH(1,1) with Student-t innovations


def _std_t(rng: np.random.Generator, nu: float, size: tuple[int, ...]) -> np.ndarray:
    """Student-t draws scaled to unit variance (ν > 2)."""
    return rng.standard_t(nu, size=size) * math.sqrt((nu - 2.0) / nu)


def garch_variance(e: np.ndarray, omega: float, alpha: float, beta: float, h0: float) -> np.ndarray:
    """Conditional variances ``h_t = ω + α e²_{t−1} + β h_{t−1}`` with ``h_0 = h0`` (scipy lfilter)."""
    x = omega + alpha * e[:-1] ** 2
    h = np.empty_like(e)
    h[0] = h0
    if len(e) > 1:
        h[1:], _ = signal.lfilter([1.0], [1.0, -beta], x, zi=[beta * h0])
    return h


def garch_t_nll(theta: np.ndarray, r: np.ndarray) -> float:
    """Negative log-likelihood of GARCH(1,1)-t; ``theta = (mu, omega, alpha, beta, nu)``."""
    mu, omega, alpha, beta, nu = theta
    if alpha + beta >= 0.9999 or omega <= 0 or nu <= 2.0:
        return 1e12
    e = r - mu
    h = garch_variance(e, omega, alpha, beta, float(np.var(r)))
    if np.any(h <= 0) or not np.all(np.isfinite(h)):
        return 1e12
    c = special.gammaln((nu + 1) / 2) - special.gammaln(nu / 2) - 0.5 * math.log(math.pi * (nu - 2))
    ll = c - 0.5 * np.log(h) - (nu + 1) / 2 * np.log1p(e**2 / (h * (nu - 2)))
    v = -float(np.sum(ll))
    return v if math.isfinite(v) else 1e12


@dataclass
class Garch(PriceModel):
    """GARCH(1,1) on log returns at native step ``dt_native`` with standardised Student-t(ν) shocks.

    ``r_t = mu + √h_t z_t``, ``h_t = omega + alpha (r_{t−1} − mu)² + beta h_{t−1}`` (per native step).
    """

    mu: float = 0.0
    omega: float = 1.644e-4 * (1 - 0.06 - 0.93)
    alpha: float = 0.06
    beta: float = 0.93
    nu: float = 4.0
    dt_native: float = DT_HOUR
    meta: dict[str, Any] = field(default_factory=dict, compare=False)
    name: ClassVar[str] = "garch"

    @classmethod
    def preset(cls) -> Garch:
        """Placeholder (hourly): unconditional vol 120 %/yr, α = 0.06, β = 0.93 (half-life ≈ 69 h), ν = 4."""
        alpha, beta = 0.06, 0.93
        return cls(mu=0.0, omega=(1.20**2 * DT_HOUR) * (1 - alpha - beta), alpha=alpha, beta=beta, nu=4.0)

    @property
    def persistence(self) -> float:
        """α + β."""
        return self.alpha + self.beta

    def at_bounds(self) -> list[str]:
        """Parameters sitting on the fit's bounds (α = 0.5, ν → 2, α + β → 1): the likelihood
        wanted to leave the GARCH(1,1)-t family, so the fit is not a usable generator — its
        unconditional variance is dominated by an explosive tail. YEC's hourly and daily data
        both land here (docs/real-data-2026-10.md); use the block bootstrap instead."""
        out = []
        if self.alpha >= 0.499:
            out.append("alpha")
        if self.nu <= 2.1:
            out.append("nu")
        if self.persistence >= 0.999:
            out.append("persistence")
        return out

    @property
    def unconditional_var(self) -> float:
        """ω / (1 − α − β), per native step."""
        return self.omega / (1.0 - self.persistence)

    @property
    def annual_vol(self) -> float:
        """√(unconditional variance / dt_native)."""
        return math.sqrt(self.unconditional_var / self.dt_native)

    def expected_log_drift(self) -> float:
        return self.mu / self.dt_native

    def log_returns(
        self, n_paths: int, n_steps: int, dt: float | str, rng: np.random.Generator
    ) -> np.ndarray:
        d = as_dt(dt)
        m, k, a = _native_to_dt(n_steps, d, self.dt_native)
        z = _std_t(rng, self.nu, (n_paths, m))
        r = np.empty((n_paths, m))
        hs = np.empty((n_paths, m))
        h = np.full(n_paths, self.unconditional_var)
        e_prev = np.zeros(n_paths)
        for t in range(m):
            if t:
                h = self.omega + self.alpha * e_prev**2 + self.beta * h
            e_prev = np.sqrt(h) * z[:, t]
            r[:, t] = self.mu + e_prev
            hs[:, t] = h
        return _resize(r, hs, n_steps, k, a, rng)

    @classmethod
    def fit_returns(cls, r: np.ndarray, dt: float) -> Garch:
        """Maximum likelihood (L-BFGS-B on returns scaled to unit variance, several starts)."""
        r = r[np.isfinite(r)]
        s = float(np.std(r)) or 1.0
        x = r / s
        best: optimize.OptimizeResult | None = None
        bounds = [(-1.0, 1.0), (1e-8, 5.0), (1e-6, 0.5), (0.0, 0.9998), (2.05, 200.0)]
        for a0, b0, nu0 in ((0.05, 0.90, 6.0), (0.10, 0.85, 4.0), (0.03, 0.96, 8.0)):
            theta0 = np.array([float(np.mean(x)), 1.0 - a0 - b0, a0, b0, nu0])
            res = optimize.minimize(garch_t_nll, theta0, args=(x,), method="L-BFGS-B", bounds=bounds)
            if best is None or res.fun < best.fun:
                best = res
        assert best is not None
        mu, om, al, be, nu = (float(v) for v in best.x)
        return cls(mu=mu * s, omega=om * s * s, alpha=al, beta=be, nu=nu, dt_native=dt)


# ---------------------------------------------------------------------------------------------------
# Two-state regime switch


@dataclass
class RegimeSwitch(PriceModel):
    """Two-state continuous-time Markov regime switch between GBMs (state 0 calm, 1 turbulent).

    ``q01``/``q10`` are switching rates per year (mean sojourn = 1/q). Paths start in the stationary
    distribution."""

    mu: tuple[float, float] = (0.30, -1.50)
    sigma: tuple[float, float] = (0.80, 2.00)
    q01: float = 365.0 / 120.0  #: calm → turbulent (mean calm spell 120 days)
    q10: float = 365.0 / 30.0  #: turbulent → calm (mean turbulent spell 30 days)
    meta: dict[str, Any] = field(default_factory=dict, compare=False)
    name: ClassVar[str] = "regime"

    @classmethod
    def preset(cls) -> RegimeSwitch:
        """Placeholder: calm σ 80 % (120-day spells), turbulent σ 200 % with −150 %/yr drift
        (30-day spells); stationary vol ≈ 115 %/yr, deep drawdowns in turbulent spells."""
        return cls()

    @property
    def stationary(self) -> tuple[float, float]:
        """Long-run probabilities of the two states."""
        p1 = self.q01 / (self.q01 + self.q10)
        return 1.0 - p1, p1

    @property
    def annual_vol(self) -> float:
        """√(Σ π_s σ_s²) (ignores the drift-mixing term)."""
        p0, p1 = self.stationary
        return math.sqrt(p0 * self.sigma[0] ** 2 + p1 * self.sigma[1] ** 2)

    def expected_log_drift(self) -> float:
        p = self.stationary
        return sum(p[i] * (self.mu[i] - 0.5 * self.sigma[i] ** 2) for i in range(2))

    def states(self, n_paths: int, n_steps: int, dt: float, rng: np.random.Generator) -> np.ndarray:
        """Regime per step, ``(n_paths, n_steps)`` int8."""
        d = as_dt(dt)
        # exact CTMC transition probabilities over d (exp(Q d)), so the simulated chain keeps the
        # stationary distribution ``self.stationary`` at any step size (see embed_two_state)
        q = self.q01 + self.q10
        jump = 1.0 - math.exp(-q * d) if q > 0 else 0.0
        p01 = self.stationary[1] * jump
        p10 = self.stationary[0] * jump
        s = np.empty((n_paths, n_steps), dtype=np.int8)
        s[:, 0] = rng.random(n_paths) < self.stationary[1]
        u = rng.random((n_paths, n_steps))
        for t in range(1, n_steps):
            prev = s[:, t - 1]
            s[:, t] = np.where(prev == 0, u[:, t] < p01, u[:, t] >= p10)
        return s

    def log_returns(
        self, n_paths: int, n_steps: int, dt: float | str, rng: np.random.Generator
    ) -> np.ndarray:
        d = as_dt(dt)
        n = max(n_steps - 1, 0)
        st = self.states(n_paths, max(n, 1), d, rng)[:, :n]
        mu = np.asarray(self.mu)[st]
        sg = np.asarray(self.sigma)[st]
        return (mu - 0.5 * sg**2) * d + sg * math.sqrt(d) * rng.standard_normal((n_paths, n))

    @classmethod
    def fit_returns(cls, r: np.ndarray, dt: float, max_iter: int = 200, tol: float = 1e-7) -> RegimeSwitch:
        """Baum–Welch EM for a 2-state Gaussian HMM on per-step returns, mapped to annual parameters.

        Initialised by splitting at the 75th percentile of ``|r|``; the state with the smaller sd is
        "calm". Stops when the log-likelihood gains less than ``tol`` per observation.
        """
        r = r[np.isfinite(r)]
        n = len(r)
        big = np.abs(r - np.median(r)) > np.quantile(np.abs(r - np.median(r)), 0.75)
        m = np.array([r[~big].mean(), r[big].mean()])
        sd = np.array([r[~big].std() or 1e-6, r[big].std() or 1e-6])
        P = np.array([[0.99, 0.01], [0.03, 0.97]])
        pi = np.array([0.75, 0.25])
        ll_old = -np.inf
        for _ in range(max_iter):
            em = np.exp(-0.5 * ((r[:, None] - m) / sd) ** 2) / (sd * math.sqrt(2 * math.pi)) + 1e-300
            alpha, beta, c = _forward_backward(em, P, pi)
            ll = float(np.sum(np.log(c)))
            gamma = alpha * beta
            gamma /= gamma.sum(axis=1, keepdims=True)
            xi = (alpha[:-1, :, None] * P[None, :, :] * (em[1:] * beta[1:])[:, None, :]) / c[1:, None, None]
            xs = xi.sum(axis=0)
            P = xs / xs.sum(axis=1, keepdims=True)
            pi = gamma[0]
            w = gamma.sum(axis=0)
            m = (gamma * r[:, None]).sum(axis=0) / w
            sd = np.sqrt((gamma * (r[:, None] - m) ** 2).sum(axis=0) / w)
            sd = np.maximum(sd, 1e-9)
            if ll - ll_old < tol * n:
                break
            ll_old = ll
        order = np.argsort(sd)
        m, sd, P = m[order], sd[order], P[np.ix_(order, order)]
        sig = sd / math.sqrt(dt)
        mu = m / dt + 0.5 * sig**2
        p01 = min(max(P[0, 1], 1e-12), 1 - 1e-12)
        p10 = min(max(P[1, 0], 1e-12), 1 - 1e-12)
        q01, q10 = embed_two_state(p01, p10, dt)
        return cls(
            mu=(float(mu[0]), float(mu[1])),
            sigma=(float(sig[0]), float(sig[1])),
            q01=q01,
            q10=q10,
        )


def embed_two_state(p01: float, p10: float, dt: float) -> tuple[float, float]:
    """Rates ``(q01, q10)`` of the two-state CTMC whose transition matrix over ``dt`` is the fitted
    discrete chain: ``exp(Q dt)`` has off-diagonals ``π_j (1 − e^{−q dt})`` with ``q = q01 + q10``,
    so ``q = −ln(1 − p01 − p10)/dt`` and ``q01 = q·p01/(p01 + p10)``.

    The earlier per-state map ``q = −ln(1 − p)/dt`` is exact only when switching is rare per step;
    on YEC's hourly fit (p01 ≈ 0.17, p10 ≈ 0.41) it moved the stationary turbulent share from
    0.30 to 0.26, and with an 8.4/yr turbulent σ that left a +1.4/yr log drift the zero-drift
    shift could not see. A chain with ``p01 + p10 ≥ 1`` (anti-persistent) has no embedding; it is
    clamped to ``1 − 1e-9``."""
    tot = min(p01 + p10, 1.0 - 1e-9)
    q = -math.log(1.0 - tot) / dt
    return q * p01 / (p01 + p10), q * p10 / (p01 + p10)


def _forward_backward(
    em: np.ndarray, P: np.ndarray, pi: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Scaled forward–backward for a 2-state HMM (plain-float loops: fast for two states)."""
    n = em.shape[0]
    e0 = em[:, 0].tolist()
    e1 = em[:, 1].tolist()
    p00, p01, p10, p11 = float(P[0, 0]), float(P[0, 1]), float(P[1, 0]), float(P[1, 1])
    a0 = [0.0] * n
    a1 = [0.0] * n
    c = [0.0] * n
    x0, x1 = pi[0] * e0[0], pi[1] * e1[0]
    s = x0 + x1
    a0[0], a1[0], c[0] = x0 / s, x1 / s, s
    for t in range(1, n):
        x0 = (a0[t - 1] * p00 + a1[t - 1] * p10) * e0[t]
        x1 = (a0[t - 1] * p01 + a1[t - 1] * p11) * e1[t]
        s = x0 + x1
        a0[t], a1[t], c[t] = x0 / s, x1 / s, s
    b0 = [1.0] * n
    b1 = [1.0] * n
    for t in range(n - 2, -1, -1):
        y0 = p00 * e0[t + 1] * b0[t + 1] + p01 * e1[t + 1] * b1[t + 1]
        y1 = p10 * e0[t + 1] * b0[t + 1] + p11 * e1[t + 1] * b1[t + 1]
        b0[t], b1[t] = y0 / c[t + 1], y1 / c[t + 1]
    return np.array([a0, a1]).T, np.array([b0, b1]).T, np.array(c)


# ---------------------------------------------------------------------------------------------------
# Stationary block bootstrap


@dataclass
class BlockBootstrap(PriceModel):
    """Politis–Romano stationary bootstrap of a real return series (circular, geometric blocks).

    Each path starts at a uniform index; at every step it continues to the next return with
    probability ``1 − 1/mean_block`` and jumps to a fresh uniform index otherwise. The marginal
    distribution of resampled returns equals the empirical one; dependence within blocks is kept.
    """

    returns: np.ndarray = field(default_factory=lambda: np.zeros(0))
    mean_block: float = 168.0
    dt_native: float = DT_HOUR
    demean: bool = False
    meta: dict[str, Any] = field(default_factory=dict, compare=False)
    name: ClassVar[str] = "bootstrap"

    @classmethod
    def preset(cls) -> BlockBootstrap:
        """No preset: a bootstrap needs real returns (``fit``)."""
        raise ValueError("the block bootstrap needs real data: pass --calibrate FILE (no synthetic preset)")

    def params(self) -> dict[str, Any]:
        return {
            "n_returns": len(self.returns),
            "mean_block": self.mean_block,
            "dt_native": self.dt_native,
            "demean": self.demean,
        }

    def expected_log_drift(self) -> float:
        return 0.0 if self.demean or not len(self.returns) else float(np.mean(self.returns)) / self.dt_native

    def indices(self, n_paths: int, m: int, rng: np.random.Generator) -> np.ndarray:
        """Resampled indices ``(n_paths, m)`` into ``returns`` (vectorised)."""
        n = len(self.returns)
        if n == 0:
            raise ValueError("empty return series")
        restart = rng.random((n_paths, m)) < 1.0 / max(self.mean_block, 1.0)
        restart[:, 0] = True
        t = np.arange(m)
        block_start_t = np.maximum.accumulate(np.where(restart, t, 0), axis=1)
        block_id = np.cumsum(restart, axis=1) - 1
        starts = rng.integers(0, n, size=(n_paths, int(block_id.max()) + 1))
        start_idx = np.take_along_axis(starts, block_id, axis=1)
        return (start_idx + (t - block_start_t)) % n

    def log_returns(
        self, n_paths: int, n_steps: int, dt: float | str, rng: np.random.Generator
    ) -> np.ndarray:
        d = as_dt(dt)
        m, k, a = _native_to_dt(n_steps, d, self.dt_native)
        r = self.returns - self.returns.mean() if self.demean else self.returns
        native = r[self.indices(n_paths, max(m, 1), rng)][:, :m]
        return _resize(native, float(np.var(r)), n_steps, k, a, rng)

    @classmethod
    def fit_returns(cls, r: np.ndarray, dt: float, mean_block: float | None = None) -> BlockBootstrap:
        """Store the returns; ``mean_block`` defaults to one week of native steps, capped at n/4."""
        r = np.asarray(r, dtype=np.float64)
        r = r[np.isfinite(r)]
        if mean_block is None:
            week = (7 * 86400) / (dt * 365 * 86400)
            mean_block = max(1.0, min(week, len(r) / 4))
        # Demeaned by default (zero log drift, a flat median path): a sample's mean return is noise
        # at YEC's volatility — the 2025-10..2026-10 CoinGecko year carries +184 %/yr of log drift,
        # which a 5-year bootstrap compounds into the PRICE_MAX clamp (D-RD-1). Set
        # ``demean = False`` explicitly to replay the sample's own drift.
        return cls(returns=r, mean_block=float(mean_block), dt_native=dt, demean=True)


# ---------------------------------------------------------------------------------------------------
# Registry


MODELS: dict[str, type[PriceModel]] = {
    "gbm": GBM,
    "merton": Merton,
    "garch": Garch,
    "regime": RegimeSwitch,
    "bootstrap": BlockBootstrap,
}


def preset(name: str, **overrides: Any) -> PriceModel:
    """The placeholder preset of model ``name`` with optional parameter overrides."""
    cls = MODELS[name]
    m = cls.preset()  # type: ignore[attr-defined]
    for k, v in overrides.items():
        if not hasattr(m, k) or k == "meta":
            raise ValueError(f"{name} has no parameter {k!r}")
        setattr(m, k, tuple(v) if isinstance(v, list) else v)
    return m


DRIFTS: tuple[str, ...] = ("zero", "fitted")


def neutralise_drift(model: PriceModel) -> PriceModel:
    """Shift ``model``'s drift so its expected log return is zero (a flat median path), keeping
    every other parameter (the regime switch keeps the *difference* between its states' drifts).

    Drift is not identifiable from a few years of YEC data (its standard error is σ/√T ≈ 230 %/yr
    over one year), so a fitted drift is noise that would dominate a multi-year simulation (D-RD-1).
    """
    eld = model.expected_log_drift()
    if isinstance(model, GBM | Merton):
        model.mu -= eld
    elif isinstance(model, Garch):
        model.mu = 0.0
    elif isinstance(model, RegimeSwitch):
        model.mu = (model.mu[0] - eld, model.mu[1] - eld)
    elif isinstance(model, BlockBootstrap):
        model.demean = True
    meta = getattr(model, "meta", None)
    if isinstance(meta, dict):
        meta["drift"] = "zero"
        meta["fitted_log_drift"] = eld
    return model


def fit(name: str, pp: PricePath, path: int = 0, drift: str = "zero") -> PriceModel:
    """Fit model ``name`` to ``pp``; ``drift="zero"`` (default) neutralises the fitted drift
    (:func:`neutralise_drift`), ``"fitted"`` keeps the sample's."""
    if drift not in DRIFTS:
        raise ValueError(f"drift must be one of {DRIFTS}")
    model = MODELS[name].fit(pp, path)
    if drift == "zero":
        neutralise_drift(model)
    elif isinstance(model, BlockBootstrap):
        model.demean = False
        model.meta["drift"] = "fitted"  # type: ignore[attr-defined]
    return model


# ---------------------------------------------------------------------------------------------------
# Renewal on/off process (outages)


def renewal_outages(
    n_paths: int,
    n_units: int,
    n_steps: int,
    step_seconds: float,
    rate_per_day: Sequence[float],
    mean_hours: Sequence[float],
    rng: np.random.Generator,
) -> np.ndarray:
    """Outage mask ``(n_paths, n_units, n_steps)``: alternating Exp up-times (rate per day) and Exp
    down-times (mean hours) per unit, starting up."""
    out = np.zeros((n_paths, n_units, n_steps), dtype=bool)
    horizon = n_steps * step_seconds
    for u in range(n_units):
        rate = float(rate_per_day[u]) / 86400.0
        mean_down = float(mean_hours[u]) * 3600.0
        if rate <= 0 or mean_down <= 0:
            continue
        for p in range(n_paths):
            t = rng.exponential(1.0 / rate)
            while t < horizon:
                d = rng.exponential(mean_down)
                a, b = int(t // step_seconds), math.ceil((t + d) / step_seconds)
                out[p, u, a : min(b, n_steps)] = True
                t += d + rng.exponential(1.0 / rate)
    return out


def _runs(mask: np.ndarray) -> list[int]:
    """Lengths of runs of True."""
    runs, cur = [], 0
    for v in mask.tolist():
        if v:
            cur += 1
        elif cur:
            runs.append(cur)
            cur = 0
    if cur:
        runs.append(cur)
    return runs


# ---------------------------------------------------------------------------------------------------
# Exchange spread model


@dataclass
class SourceQuotes:
    """Per-source quotes generated around true paths."""

    names: tuple[str, ...]
    quotes: np.ndarray  #: int64 (paths, sources, n) µUSD; 0 during an outage
    stale: np.ndarray  #: bool (paths, sources, n): the quote was not refreshed this step
    outage: np.ndarray  #: bool (paths, sources, n)
    step_seconds: int

    def pair_spreads_bps(self, path: int = 0) -> dict[tuple[str, str], np.ndarray]:
        """``|a − b|·10⁴/min(a, b)`` per pair where both sources quote (MINT-10's form)."""
        out = {}
        q = self.quotes[path].astype(np.float64)
        for i in range(len(self.names)):
            for j in range(i + 1, len(self.names)):
                ok = (q[i] > 0) & (q[j] > 0)
                out[(self.names[i], self.names[j])] = (
                    np.abs(q[i, ok] - q[j, ok]) * 1e4 / np.minimum(q[i, ok], q[j, ok])
                )
        return out


def _default_corr(n: int, rho: float = 0.3) -> tuple[tuple[float, ...], ...]:
    return tuple(tuple(1.0 if i == j else rho for j in range(n)) for i in range(n))


@dataclass
class SpreadModel:
    """Per-source quote = true price × exp(bias + e_t), held between refreshes, absent in outages.

    ``e`` is a stationary Gaussian AR(1) per source (sd ``sigma_bps``, persistence time ``tau_seconds``)
    with cross-source correlation ``corr``. Refreshes arrive at ``refresh_per_hour`` (Poisson);
    outages start at ``outage_per_day`` and last Exp(``outage_mean_hours``). Defaults are
    placeholders for the three ``spreads.py`` sources; ``fit`` replaces them.
    """

    names: tuple[str, ...] = ("coingecko", "safetrade", "nonkyc")
    bias_bps: tuple[float, ...] = (0.0, 40.0, -30.0)
    sigma_bps: tuple[float, ...] = (40.0, 120.0, 150.0)
    corr: tuple[tuple[float, ...], ...] = field(default_factory=lambda: _default_corr(3))
    tau_seconds: float = 1800.0
    refresh_per_hour: tuple[float, ...] = (120.0, 12.0, 30.0)
    outage_per_day: tuple[float, ...] = (0.05, 0.20, 0.10)
    outage_mean_hours: tuple[float, ...] = (0.5, 3.0, 1.0)
    meta: dict[str, Any] = field(default_factory=dict, compare=False)

    def generate(
        self, true: PricePath | np.ndarray, rng: np.random.Generator, step_seconds: int | None = None
    ) -> SourceQuotes:
        """Quotes for every source around ``true`` (µUSD, ``(paths, n)``)."""
        if isinstance(true, PricePath):
            step = STEP_SECONDS[true.resolution]
            p = true.prices
        else:
            p = np.atleast_2d(np.asarray(true, dtype=np.int64))
            step = int(step_seconds or BLOCK_SECONDS)
        paths, n = p.shape
        ns = len(self.names)
        L = np.linalg.cholesky(np.asarray(self.corr, dtype=np.float64) + 1e-12 * np.eye(ns))
        phi = math.exp(-step / max(self.tau_seconds, 1e-9))
        z = rng.standard_normal((paths, n, ns)) @ L.T  # correlated N(0,1)
        z = np.moveaxis(z, 2, 1)  # (paths, ns, n)
        e = np.empty_like(z)
        e[:, :, 0] = z[:, :, 0]  # stationary start
        if n > 1:
            e[:, :, 1:], _ = signal.lfilter(
                [math.sqrt(1 - phi**2)], [1.0, -phi], z[:, :, 1:], axis=-1, zi=phi * z[:, :, :1]
            )
        sd = np.asarray(self.sigma_bps)[None, :, None] / 1e4
        bias = np.asarray(self.bias_bps)[None, :, None] / 1e4
        fresh = p[:, None, :].astype(np.float64) * np.exp(bias + sd * e)
        # staleness: the quote shown is the one from the last refresh
        prob = 1.0 - np.exp(-np.asarray(self.refresh_per_hour)[None, :, None] * step / 3600.0)
        refresh = rng.random((paths, ns, n)) < prob
        refresh[:, :, 0] = True
        t = np.arange(n)
        last = np.maximum.accumulate(np.where(refresh, t, 0), axis=2)
        held = np.take_along_axis(fresh, last, axis=2)
        outage = renewal_outages(paths, ns, n, step, self.outage_per_day, self.outage_mean_hours, rng)
        q = np.clip(np.rint(held), PRICE_MIN, PRICE_MAX).astype(np.int64)
        q[outage] = 0
        return SourceQuotes(self.names, q, last != t, outage, step)

    @classmethod
    def fit(cls, log: SpreadsLog, interval: int | None = None) -> SpreadModel:
        """Fit from a ``spreads.py`` log.

        Deviations ``d_i = log(p_i / median of present quotes)`` over rows where every source is
        present give bias, sd, correlation and (from lag-1 autocorrelation over consecutive rows)
        the persistence time. The share of consecutive rows with an unchanged quote gives the
        refresh rate; runs of missing cells give the outage rate and mean duration.
        """
        from ybcal.data.loaders import infer_step

        step = int(interval or infer_step(log.ts))
        p = log.prices.astype(np.float64)
        present = p > 0
        full = present.all(axis=1)
        if full.sum() < 10:
            raise ValueError("need at least 10 rows with every source present to fit the spread model")
        lp = np.log(np.where(present, p, 1.0))
        med = np.median(lp[full], axis=1)
        d = lp[full] - med[:, None]
        bias = d.mean(axis=0) * 1e4
        sd = d.std(axis=0, ddof=1) * 1e4
        with np.errstate(invalid="ignore", divide="ignore"):
            corr = np.corrcoef((d - d.mean(axis=0)).T) if d.shape[1] > 1 else np.ones((1, 1))
        corr = np.nan_to_num(corr, nan=0.0)
        np.fill_diagonal(corr, 1.0)
        ts_full = log.ts[full]
        consecutive = np.diff(ts_full) <= 1.5 * step
        phis = []
        for i in range(d.shape[1]):
            x, y = d[:-1, i][consecutive], d[1:, i][consecutive]
            if len(x) > 2 and x.std() > 0 and y.std() > 0:
                phis.append(float(np.corrcoef(x, y)[0, 1]))
        phi = float(np.clip(np.mean(phis) if phis else 0.5, 0.01, 0.999))
        tau = -step / math.log(phi)
        refresh, out_rate, out_mean = [], [], []
        cons_all = np.diff(log.ts) <= 1.5 * step
        for i in range(p.shape[1]):
            both = present[:-1, i] & present[1:, i] & cons_all
            same = float(np.mean(p[:-1, i][both] == p[1:, i][both])) if both.any() else 0.0
            same = min(max(same, 1e-6), 0.999)
            refresh.append(-math.log(same) * 3600.0 / step)
            runs = _runs(~present[:, i])
            up_days = present[:, i].sum() * step / 86400.0
            out_rate.append(len(runs) / up_days if up_days > 0 else 0.0)
            out_mean.append(float(np.mean(runs)) * step / 3600.0 if runs else 0.0)
        return cls(
            names=tuple(log.names),
            bias_bps=tuple(float(x) for x in bias),
            sigma_bps=tuple(float(x) for x in sd),
            corr=tuple(tuple(float(v) for v in row) for row in corr),
            tau_seconds=float(tau),
            refresh_per_hour=tuple(refresh),
            outage_per_day=tuple(out_rate),
            outage_mean_hours=tuple(out_mean),
            meta={"fitted": True, "fitted_from": log.source, "rows": len(log), "step_seconds": step},
        )


# ---------------------------------------------------------------------------------------------------
# Pools: who mines each block, what they tag


@dataclass
class PoolBlocks:
    """Per-block mining and tagging outcome."""

    miner: np.ndarray  #: int16 (paths, n): pool index, ``n_pools`` = "other" (untagged miners)
    tagged: np.ndarray  #: bool (paths, n): the block carries a price tag
    quote: np.ndarray  #: int64 (paths, n): µUSD tag, 0 where untagged
    names: tuple[str, ...]


@dataclass
class PoolModel:
    """Pools with hashrate shares that mine blocks at random and tag their quotes.

    Shares need not sum to 1; the remainder is "other" (miners that never tag). A tagging pool's
    quote for a block is its ``twap_blocks`` TWAP of the true price, ``lag_blocks`` old, times
    ``exp(N(bias, noise))``; a ``frozen`` pool repeats its first quote (a stuck feed, PIN-1); a pool
    in an outage mines but does not tag. Defaults are placeholders (6 pools, 80 % tagging share).
    """

    names: tuple[str, ...] = ("pool_a", "pool_b", "pool_c", "pool_d", "pool_e", "pool_f")
    shares: tuple[float, ...] = (0.25, 0.20, 0.15, 0.10, 0.06, 0.04)
    tagging: tuple[bool, ...] = (True, True, True, True, True, True)
    noise_bps: tuple[float, ...] = (30.0, 30.0, 30.0, 30.0, 30.0, 30.0)
    lag_blocks: tuple[int, ...] = (2, 2, 2, 2, 2, 2)
    bias_bps: tuple[float, ...] = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    frozen: tuple[bool, ...] = (False, False, False, False, False, False)
    outage_per_day: tuple[float, ...] = (0.02, 0.02, 0.02, 0.02, 0.02, 0.02)
    outage_mean_hours: tuple[float, ...] = (2.0, 2.0, 2.0, 2.0, 2.0, 2.0)
    twap_blocks: int = 12  #: 900 s TWAP (yellowback_price.py TWAP_SECONDS) / 75 s
    meta: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def n_pools(self) -> int:
        """Number of named pools (excluding "other")."""
        return len(self.names)

    @property
    def tagging_share(self) -> float:
        """Sum of the shares of tagging pools."""
        return float(sum(s for s, t in zip(self.shares, self.tagging, strict=True) if t))

    def with_pools(self, **changes: Sequence[Any]) -> PoolModel:
        """A copy with per-pool tuples replaced (lists accepted)."""
        d = {k: (tuple(v) if isinstance(v, list | np.ndarray) else v) for k, v in changes.items()}
        return PoolModel(**{**asdict(self), **d})

    def assign(
        self,
        n_paths: int,
        n_blocks: int,
        rng: np.random.Generator,
        shares: np.ndarray | None = None,
        shares_step_blocks: int = 1,
    ) -> np.ndarray:
        """Miner of each block, ``(n_paths, n_blocks)`` int16 (``n_pools`` = other).

        ``shares`` may vary over time: shape ``(T, pools)`` or ``(n_paths, T, pools)``, each row
        applying to ``shares_step_blocks`` blocks.
        """
        u = rng.random((n_paths, n_blocks))
        if shares is None:
            cum = np.cumsum(np.asarray(self.shares, dtype=np.float64))
            return np.searchsorted(cum, u, side="right").astype(np.int16)
        sh = np.asarray(shares, dtype=np.float64)
        if sh.ndim == 2:
            sh = sh[None]
        rows = np.minimum(np.arange(n_blocks) // shares_step_blocks, sh.shape[1] - 1)
        cum = np.cumsum(sh, axis=-1)[:, rows, :]  # (P|1, n, pools)
        return (u[:, :, None] >= cum).sum(axis=-1).astype(np.int16)

    def generate(
        self,
        true_blocks: PricePath | np.ndarray,
        rng: np.random.Generator,
        *,
        shares: np.ndarray | None = None,
        shares_step_blocks: int = 1,
    ) -> PoolBlocks:
        """Mine and tag every block of block-resolution true prices ``(paths, n)``."""
        if isinstance(true_blocks, PricePath):
            if true_blocks.resolution != "block":
                raise ValueError("PoolModel.generate needs block-resolution prices")
            p = true_blocks.prices
        else:
            p = np.atleast_2d(np.asarray(true_blocks, dtype=np.int64))
        paths, n = p.shape
        k = self.n_pools
        miner = self.assign(paths, n, rng, shares, shares_step_blocks)
        w = max(1, self.twap_blocks)
        c = np.cumsum(np.concatenate([np.zeros((paths, 1)), p.astype(np.float64)], axis=1), axis=1)
        t = np.arange(n)
        lo = np.maximum(0, t - w + 1)
        twap = (c[:, t + 1] - c[:, lo]) / (t + 1 - lo)
        m = np.minimum(miner, k - 1).astype(np.intp)
        is_pool = miner < k
        lag = np.asarray(self.lag_blocks, dtype=np.intp)[m]
        src_t = np.maximum(0, t[None, :] - lag)
        frozen = np.asarray(self.frozen, dtype=bool)[m]
        src_t = np.where(frozen, 0, src_t)
        base = np.take_along_axis(twap, src_t, axis=1)
        noise = np.asarray(self.noise_bps)[m] / 1e4 * rng.standard_normal((paths, n))
        noise = np.where(frozen, 0.0, noise)
        bias = np.asarray(self.bias_bps)[m] / 1e4
        quote = np.clip(np.rint(base * np.exp(bias + noise)), PRICE_MIN, PRICE_MAX).astype(np.int64)
        outage = renewal_outages(paths, k, n, BLOCK_SECONDS, self.outage_per_day, self.outage_mean_hours, rng)
        in_out = np.take_along_axis(outage, m[:, None, :], axis=1)[:, 0, :]
        tagged = is_pool & np.asarray(self.tagging, dtype=bool)[m] & ~in_out
        quote = np.where(tagged, quote, 0)
        return PoolBlocks(miner, tagged, quote, self.names)

    @classmethod
    def fit(cls, log: PoolShareLog, top: int | None = 8, min_share: float = 0.01) -> PoolModel:
        """Pools and shares from a ``height,payout_key`` log. Keys beyond ``top`` or below
        ``min_share`` are folded into "other" (assumed not to tag). Noise/lag/outages keep defaults."""
        sh = log.shares()
        keep = [(k, v) for k, v in sh.items() if v >= min_share][: top or None]
        d = cls()
        n = len(keep)

        def rep(x: tuple[Any, ...]) -> tuple[Any, ...]:
            return tuple(x[0] for _ in range(n))

        return cls(
            names=tuple(k for k, _ in keep),
            shares=tuple(float(v) for _, v in keep),
            tagging=tuple(True for _ in keep),
            noise_bps=rep(d.noise_bps),
            lag_blocks=rep(d.lag_blocks),
            bias_bps=rep(d.bias_bps),
            frozen=rep(d.frozen),
            outage_per_day=rep(d.outage_per_day),
            outage_mean_hours=rep(d.outage_mean_hours),
            meta={
                "fitted": True,
                "fitted_from": log.source,
                "blocks": len(log),
                "other_share": float(1 - sum(v for _, v in keep)),
            },
        )


@dataclass
class HashrateDrift:
    """Ornstein–Uhlenbeck drift of pool share logits (the last logit is "other").

    ``x_{t+1} = x_t + κ·Δ·(x_0 − x_t) + σ·√Δ·z`` with Δ in days; shares = softmax(x). Evaluated every
    ``step_blocks`` blocks (default one hour) — feed the result to :meth:`PoolModel.assign` with
    ``shares_step_blocks=step_blocks``.
    """

    sigma_per_sqrt_day: float = 0.05
    reversion_per_day: float = 0.02
    meta: dict[str, Any] = field(default_factory=dict, compare=False)

    def simulate(
        self,
        shares0: Sequence[float],
        n_steps: int,
        rng: np.random.Generator,
        *,
        n_paths: int = 1,
        step_blocks: int = 48,
    ) -> np.ndarray:
        """Shares of the named pools ``(n_paths, n_steps, pools)`` (``other`` = 1 − row sum)."""
        s = np.asarray(shares0, dtype=np.float64)
        full = np.append(s, max(1e-9, 1.0 - s.sum()))
        x0 = np.log(np.maximum(full, 1e-9))
        dt = step_blocks * BLOCK_SECONDS / 86400.0
        x = np.tile(x0, (n_paths, 1))
        out = np.empty((n_paths, n_steps, len(s)))
        for t in range(n_steps):
            if t:
                x = (
                    x
                    + self.reversion_per_day * dt * (x0 - x)
                    + self.sigma_per_sqrt_day * math.sqrt(dt) * rng.standard_normal(x.shape)
                )
            e = np.exp(x - x.max(axis=1, keepdims=True))
            out[:, t, :] = (e / e.sum(axis=1, keepdims=True))[:, :-1]
        return out

    @classmethod
    def fit(cls, log: PoolShareLog, window_blocks: int = 1152, top: int | None = 8) -> HashrateDrift:
        """σ from the sd of daily logit changes of the ``top`` pools' window shares (κ kept default)."""
        rs = log.rolling_shares(window_blocks)
        if rs.shape[0] < 3:
            raise ValueError("need at least three windows of blocks to fit hashrate drift")
        order = np.argsort(-rs.mean(axis=0))[: top or None]
        x = np.log(np.clip(rs[:, order], 1e-3, 1.0))
        dt = window_blocks * BLOCK_SECONDS / 86400.0
        sig = float(np.nanmean(np.std(np.diff(x, axis=0), axis=0, ddof=1)) / math.sqrt(dt))
        return cls(sigma_per_sqrt_day=sig, meta={"fitted": True, "fitted_from": log.source})


# ---------------------------------------------------------------------------------------------------
# Convenience


def describe_model(m: PriceModel | SpreadModel | PoolModel | HashrateDrift) -> dict[str, Any]:
    """A JSON-safe summary of a model's parameters."""
    if isinstance(m, PriceModel):
        d: dict[str, Any] = {"model": m.name, **m.params()}
        if isinstance(m, Garch):
            d["annual_vol"] = m.annual_vol
            hit = m.at_bounds()
            if hit:
                d["at_bounds"] = hit
        elif isinstance(m, Merton):
            d["annual_vol"] = m.total_vol
        elif isinstance(m, RegimeSwitch):
            d["annual_vol"] = m.annual_vol
        elif isinstance(m, GBM):
            d["annual_vol"] = m.sigma
        return d
    return {k: v for k, v in asdict(m).items() if k != "meta"}


def simulate_years(
    model: PriceModel,
    n_paths: int,
    years: float,
    resolution: Resolution,
    rng: np.random.Generator,
    p0: int = DEFAULT_P0,
) -> PricePath:
    """Simulate ``years`` of ``resolution`` steps (convenience for the CLI)."""
    n = round(years * steps_per_year(resolution)) + 1
    return model.simulate(n_paths, n, resolution, rng, p0)


def realised_annual_vol(pp: PricePath) -> float:
    """Annualised sd of all finite log returns (a quick check; see :mod:`ybcal.data.describe`)."""
    r = log_returns(pp)
    return float(np.nanstd(r) * math.sqrt(steps_per_year(pp.resolution)))


def preset_table() -> Mapping[str, dict[str, Any]]:
    """Every model's placeholder preset (bootstrap excluded: it has none)."""
    return {name: describe_model(MODELS[name].preset()) for name in MODELS if name != "bootstrap"}  # type: ignore[attr-defined]

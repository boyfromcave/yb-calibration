"""Price-drift convention for long-horizon solvency ensembles (audit D-RD-AUD-1).

P(bad debt) over 30-day to 5-year horizons is dominated by the drift a price model carries, not by
its dispersion: a 120 %-vol martingale (``E[p_T] = p_0``) loses ``σ²/2 = 72 %`` of log price a
year, so its median price falls ~90 % in three years, while a centred model keeps the median flat.
The synthetic presets mixed both conventions (GBM, Merton and the regime switch are martingales,
GARCH-t is centred) and a real-data block bootstrap carried the sample's own drift (+176 %/yr for
the year to 2026-10), so the "worst" member — and with it a locked base ratio — was decided by the
drift convention. This module applies one convention to every member:

* ``"centred"`` (default, the project convention of D-WP2-5): expected log drift 0 — the median
  price is flat; dispersion, fat tails, clustering and regime structure are kept;
* ``"martingale"``: expected log drift ``−σ²/2`` (``E[p_T] = p_0``), a stress convention: it adds a
  deterministic bleed that grows with σ² and the horizon;
* ``"model"``: no adjustment (each preset's own drift; a real bootstrap's sample drift).

The adjustment is a deterministic shift of the per-step log returns, so the draws (and common
random numbers) are unchanged. Only the long-horizon solvency ensembles read it (the G3/G4 hour
ensemble and the joint top-risk model); every other study keeps its centred base (D-WP2-5; G7's
``halt.p_sys_bad`` looks ``grace`` = 30 days ahead, where the convention moves the answer little).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

DRIFTS: tuple[str, ...] = ("centred", "martingale", "model")
DEFAULT_DRIFT = "centred"


def check(kind: str) -> str:
    """Validate a convention name (``ValueError`` on anything else)."""
    k = str(kind)
    if k not in DRIFTS:
        raise ValueError(f"price_drift must be one of {', '.join(DRIFTS)} (got {kind!r})")
    return k


def annual_vol(model: Any, returns: np.ndarray | None = None, dt: float | None = None) -> float:
    """Annualised volatility of a ``PriceModel`` (``annual_vol`` / ``total_vol`` / ``sigma``), or of
    ``returns`` sampled every ``dt`` years when the model exposes none (a block bootstrap)."""
    for attr in ("annual_vol", "total_vol"):
        v = getattr(model, attr, None)
        if v is not None:
            v = v() if callable(v) else v
            if isinstance(v, int | float) and math.isfinite(v):
                return float(v)
    s = getattr(model, "sigma", None)
    if isinstance(s, int | float):
        return float(s)
    r = getattr(model, "returns", None) if returns is None else returns
    d = getattr(model, "dt_native", None) if dt is None else dt
    if r is not None and d and len(r) > 1:
        return float(np.std(np.asarray(r, dtype=float), ddof=1) / math.sqrt(d))
    raise ValueError("cannot determine the annual volatility of this model")


def target_log_drift(kind: str, vol: float, model_drift: float) -> float:
    """Expected log drift per year under the convention ``kind``."""
    k = check(kind)
    if k == "centred":
        return 0.0
    if k == "martingale":
        return -0.5 * vol * vol
    return float(model_drift)


def shift_per_year(kind: str, model: Any) -> float:
    """What to add to the model's expected log drift (per year) to reach the convention."""
    md = float(model.expected_log_drift())
    return target_log_drift(kind, annual_vol(model), md) - md


def apply(r: np.ndarray, dt_years: float, kind: str, model: Any) -> np.ndarray:
    """Per-step log returns ``r`` of ``model`` sampled every ``dt_years``, shifted to ``kind``."""
    s = shift_per_year(kind, model)
    return r if s == 0.0 else r + s * float(dt_years)


def describe(kind: str, model: Any) -> dict[str, float | str]:
    """The model's own and the applied expected log drift (per year) — for evidence and tests."""
    md = float(model.expected_log_drift())
    vol = annual_vol(model)
    return {
        "convention": check(kind),
        "model_log_drift": md,
        "applied_log_drift": target_log_drift(kind, vol, md),
        "annual_vol": vol,
    }

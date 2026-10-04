"""Price feeds as the shipped agents build them (owner: attestation wave 2, D-RD-ATT-1).

Both the pool quote agent (``ycash6 contrib/yellowback/yellowback_price.py``, ``PriceFeed.aggregate``)
and the attestor agent (``contrib/yellowback/attest/src/price.rs``, ``PriceFeed::aggregate``) publish
the **median** of several venues, not one venue:

1. per source, the average of its samples in the last ``twap_seconds`` (900 s = 12 blocks);
   a source silent for ``silence_seconds`` (120 s), stale beyond its ``max_age`` or wider than
   ``max_spread_bps`` is dropped;
2. ``med`` = median of the live sources' averages; sources more than ``outlier_bps`` (1,000) from
   ``med`` are dropped;
3. fewer than ``min_sources`` kept (or fewer than ``min_venues`` distinct venues) → no price: the pool
   agent writes ``yed_setquote 0`` (a signal-only tag, no quote tag), the attestor signs nothing;
4. else the price is the median of the kept averages (``statistics.median``: mean of the two middle
   values for an even count).

The calibration tool modelled a pool as reading *one* venue (pool ``i`` → source ``i mod 3``), which
turns the venues' disagreement (worst real pair p95 1,906 bps) into honest REG-4 deviation. This
module provides

* :class:`VenueReplay` — the real venue disagreement replayed around simulated true prices: each
  source's log deviation from the aggregate (column 0, CoinGecko's aggregate, which *is* the true
  price series the price models are fitted to) is read row by row from a real ``spreads.py`` log,
  starting at a random row per path; a missing cell (an outage, or a ``max_age`` drop in the
  ``-maxage1h`` reconstruction) is an absent source;
* :func:`agent_quotes` — steps 1–4 vectorised over ``(paths, sources, blocks)``.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ybcal.units import BPS

OWNER_WP = "attest-wave2"

#: Shipped agent defaults (yellowback_price.py:47-50, attest/src/price.rs:23-26).
AGENT_TWAP_BLOCKS = 12          # 900 s / 75 s
AGENT_OUTLIER_BPS = 1000
#: both sample configs ship 3 (pool/yellowback-quote.toml.sample:31, attest/attest.toml.sample:35)
AGENT_MIN_SOURCES = 3
AGENT_MIN_VENUES = 2


@dataclass
class VenueReplay:
    """Real per-source deviations from the aggregate, replayed in time order around true prices."""

    names: tuple[str, ...]
    dev: np.ndarray  #: float64 (rows, sources): log(p_i / p_0); NaN = source absent in that row
    step_seconds: int
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_log(cls, log, ref: int = 0) -> VenueReplay:
        """From a :class:`ybcal.data.loaders.SpreadsLog`; rows without the reference are dropped."""
        from ybcal.data.loaders import infer_step

        p = np.asarray(log.prices, dtype=np.float64)
        ok = p[:, ref] > 0
        p = p[ok]
        if len(p) < 10:
            raise ValueError("need at least 10 rows with the reference source present")
        with np.errstate(divide="ignore", invalid="ignore"):
            d = np.where(p > 0, np.log(np.where(p > 0, p, 1.0) / p[:, [ref]]), np.nan)
        step = int(infer_step(np.asarray(log.ts)[ok]))
        return cls(tuple(log.names), d, step, {"rows": len(p), "source": getattr(log, "source", "")})

    def generate(self, true: np.ndarray, rng: np.random.Generator, step_seconds: int = 75) -> np.ndarray:
        """Source quotes ``(paths, sources, n)`` µUSD (0 = absent) around ``true`` ``(paths, n)``."""
        tp = np.atleast_2d(np.asarray(true, dtype=np.int64))
        P, n = tp.shape
        rows = self.dev.shape[0]
        start = rng.integers(0, rows, size=P)
        r = (start[:, None] + (np.arange(n)[None, :] * step_seconds) // self.step_seconds) % rows  # (P, n)
        d = self.dev[r]  # (P, n, ns)
        q = tp[:, :, None].astype(np.float64) * np.exp(np.nan_to_num(d, nan=0.0))
        q = np.where(np.isnan(d), 0.0, np.rint(q))
        return np.moveaxis(q.astype(np.int64), 2, 1)

    def pair_p95(self) -> dict[tuple[str, str], float]:
        """Nearest-rank-free p95 of ``|a − b|·10⁴/min(a, b)`` per pair (MINT-10's form)."""
        out = {}
        e = np.exp(self.dev)
        for i in range(len(self.names)):
            for j in range(i + 1, len(self.names)):
                a, b = e[:, i], e[:, j]
                ok = np.isfinite(a) & np.isfinite(b)
                if ok.any():
                    out[(self.names[i], self.names[j])] = float(
                        np.percentile(np.abs(a[ok] - b[ok]) * BPS / np.minimum(a[ok], b[ok]), 95))
        return out


def _window_avg(src: np.ndarray, w: int) -> np.ndarray:
    """Mean of the positive values in the last ``w`` columns (0 where none), along the last axis."""
    s = np.where(src > 0, src, 0).astype(np.float64)
    v = (src > 0).astype(np.int64)
    pad = [(0, 0)] * (s.ndim - 1) + [(1, 0)]
    cs = np.cumsum(np.pad(s, pad), axis=-1)
    cv = np.cumsum(np.pad(v, pad), axis=-1)
    n = s.shape[-1]
    t = np.arange(n)
    lo = np.maximum(0, t - w + 1)
    tot = cs[..., t + 1] - cs[..., lo]
    cnt = cv[..., t + 1] - cv[..., lo]
    return np.where(cnt > 0, tot / np.maximum(cnt, 1), 0.0)


def agent_quotes(quotes: np.ndarray, *, twap_blocks: int = AGENT_TWAP_BLOCKS,
                 outlier_bps: int = AGENT_OUTLIER_BPS, min_sources: int = AGENT_MIN_SOURCES,
                 min_venues: int = AGENT_MIN_VENUES, venues: tuple[str, ...] | None = None) -> np.ndarray:
    """The agent's published price per block ``(paths, n)`` µUSD (0 = fails closed) from per-source
    quotes ``(paths, sources, n)`` (0 = source absent this block: silence drop).

    ``venues`` names each source's venue (distinct by default)."""
    q = np.asarray(quotes)
    if q.ndim == 2:
        q = q[None]
    P, _, n = q.shape
    avg = _window_avg(q, max(1, int(twap_blocks)))
    live = (q > 0) & (avg > 0)  # a source silent this block is dropped (silence_seconds 120 s < 2 blocks)
    a = np.where(live, avg, np.nan)
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN columns: no live source (fails closed)
        med = np.nanmedian(a, axis=1)  # (P, n)
        keep = live & (np.abs(a - med[:, None, :]) * BPS <= outlier_bps * med[:, None, :])
        k = np.where(keep, a, np.nan)
        out = np.nanmedian(k, axis=1)
    nk = keep.sum(axis=1)
    if venues is None:
        nv = nk
    else:
        vid = {v: i for i, v in enumerate(dict.fromkeys(venues))}
        ids = np.asarray([vid[v] for v in venues])
        nv = np.zeros((P, n), dtype=np.int64)
        for v in range(len(vid)):
            nv += keep[:, ids == v, :].any(axis=1)
    ok = (live.sum(axis=1) >= min_sources) & (nk >= min_sources) & (nv >= min_venues) & np.isfinite(out)
    return np.where(ok, np.rint(np.nan_to_num(out)), 0).astype(np.int64)


def unchanged_share(series: np.ndarray) -> float:
    """Share of consecutive positive pairs with an unchanged value (a feed's staleness at the series'
    cadence)."""
    s = np.atleast_2d(np.asarray(series))
    a, b = s[:, :-1], s[:, 1:]
    ok = (a > 0) & (b > 0)
    return float(np.mean(a[ok] == b[ok])) if ok.any() else math.nan

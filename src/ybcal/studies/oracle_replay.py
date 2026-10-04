"""Real-history replay of the oracle (D-RD-ORA-3): the real YEC price, block by block, through the
honest pool oracle and PRICE-1 / SIGMA-1 / HALT-3 — no bootstrap, no program.

The studies' ensembles bootstrap the real returns, which keeps their distribution but scrambles the
order beyond a week and drops the real regimes. This module answers the question the owner asks
first — *what would these parameters have done on the real price?* — for the whole history and for
named eras (the 2021–22 bust, the 2025–26 cycle, the last year):

* the hourly real price is interpolated log-linearly onto 75-second blocks (``resample`` with
  ``loglinear``; no intra-hour noise is invented), then quoted by the same oracle the studies use
  (``g1_price_windows.oracle_config``: the real pool landscape when a pool-share log is loaded, its
  real miner sequence replayed cyclically; otherwise the policy's equal pools; per-pool feed outages
  as the policy says; 15-minute TWAP quotes with 30 bps noise);
* PRICE-1 medians at the candidate's windows (``vkernels.RollingMedian``, memoised per window),
  SIGMA-1 (``sim.sigma``: the node's arithmetic, state.cpp:1212-1221, math.h ``SigmaMultBps``) and
  HALT-3 (state.cpp:1238-1241);
* events read from the real series: one-hour round-trip wicks (aggregator prints) and real falls
  (≥ 30 % within a day, ≥ 50 % within a week), for HALT-3 detection.

Outputs are plain metric dicts (``replay_*``) that G2 and G7 attach to every candidate, so each
recommendation carries "on the real history" evidence next to its ensemble numbers.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np

from ybcal.data.pricepath import resample
from ybcal.model import vkernels as V
from ybcal.sim import oracle as O
from ybcal.sim import sigma as S
from ybcal.types import PricePath
from ybcal.units import BLOCKS_PER_DAY, BLOCKS_PER_HOUR, BLOCKS_PER_YEAR, BPS

OWNER_WP = "oracle (wave 2)"

#: Named eras of the real history (UTC, [start, end)); ``None`` = the series' own end. ``last365``
#: is relative to the series' end. Chosen from docs/real-data-2026-10.md §5: the 2021–22 bust, the
#: 2025–26 cycle ($0.05 → $1.11 → $0.36) and a calm stretch (2023, thin and flat-ish).
ERAS: dict[str, tuple[str | None, str | None]] = {
    "full": (None, None),
    "last365": ("-365", None),
    "2021-22": ("2021-01-01", "2023-01-01"),
    "2023": ("2023-01-01", "2024-01-01"),
    "2025-26": ("2025-07-01", None),
}
#: Blocks skipped at the start of every era (medians and σ warm up).
WARMUP_BLOCKS = 2 * 4032
_PER_YEAR_H = BLOCKS_PER_YEAR / BLOCKS_PER_HOUR

_CACHE: OrderedDict[tuple, Replay] = OrderedDict()
_MED: OrderedDict[tuple, np.ndarray] = OrderedDict()
_CAP, _CAP_MED = 6, 24


@dataclass
class Replay:
    """One era of real history through the oracle (one path, block resolution)."""

    era: str
    true: np.ndarray  #: (1, n) int64 µUSD
    tag_price: np.ndarray  #: (1, n) int64, 0 = no quote
    valid: np.ndarray  #: (1, n) bool
    t0: datetime
    key: tuple = ()
    info: dict = field(default_factory=dict)
    _rm: V.RollingMedian | None = None

    @property
    def n(self) -> int:
        return int(self.true.shape[1])

    def median(self, window: int, fill: int) -> np.ndarray:
        k = (self.key, int(window), int(fill))
        hit = _MED.get(k)
        if hit is not None:
            _MED.move_to_end(k)
            return hit
        if self._rm is None:
            self._rm = V.RollingMedian(self.tag_price, self.valid)
        m = self._rm.median(int(window), int(fill))
        _MED[k] = m
        while len(_MED) > _CAP_MED:
            _MED.popitem(last=False)
        return m


def clear_caches() -> None:
    _CACHE.clear()
    _MED.clear()
    _EVENTS.clear()


def _parse(d: str) -> datetime:
    return datetime.fromisoformat(d).replace(tzinfo=UTC)


def era_slice(price: PricePath, era: str) -> tuple[int, int]:
    """Hour indices ``[a, b)`` of ``era`` in an hourly ``price`` (clipped to the series)."""
    if price.resolution != "hour":
        raise ValueError("the replay needs an hourly price path")
    n = price.n_steps
    lo, hi = ERAS[era]
    t0 = price.t0 if price.t0.tzinfo else price.t0.replace(tzinfo=UTC)

    def idx(d: str | None, default: int) -> int:
        if d is None:
            return default
        if d.startswith("-"):
            return n - int(d[1:]) * 24
        return int((_parse(d) - t0).total_seconds() // 3600)

    a, b = max(0, idx(lo, 0)), min(n, idx(hi, n))
    return a, max(a, b)


def despiked(price: PricePath, threshold: float = 0.20) -> PricePath:
    """``price`` with every one-hour round-trip wick larger than ``threshold`` (:func:`wick_events`)
    replaced by the geometric mean of its neighbours — the aggregator prints removed, the market
    kept (evidence for how much of σ̂'s tail the prints make)."""
    p = price.prices.copy()
    for h in wick_events(price, threshold):
        if 0 < h < p.shape[1] - 1 and p[0, h - 1] > 0 and p[0, h + 1] > 0:
            p[0, h] = round(math.sqrt(float(p[0, h - 1]) * float(p[0, h + 1])))
    return PricePath(price.t0, price.resolution, p, price.provenance, dict(price.meta))


def replay(
    env,
    era: str = "full",
    *,
    price: PricePath | None = None,
    despike: bool = False,
    intra_hour: str = "loglinear",
) -> Replay | None:
    """Realise ``era`` of the real price through the study oracle (memoised). ``None`` without an
    hourly real price or when the era is shorter than its warm-up. ``despike`` removes the one-hour
    round-trip prints first (:func:`despiked`). ``intra_hour``: ``loglinear`` (no intra-hour moves:
    a lower bound on the feed noise pools see) or ``bridge`` (a Brownian bridge with the series' own
    hourly variance, as the studies' bootstrap does at block resolution: an upper bound)."""
    from ybcal.studies import g1_price_windows as G1

    pp = price if price is not None else G1.real_price(env)
    if pp is None or pp.resolution != "hour":
        return None
    if despike:
        pp = despiked(pp)
    a, b = era_slice(pp, era)
    if (b - a) * BLOCKS_PER_HOUR < WARMUP_BLOCKS + BLOCKS_PER_DAY:
        return None
    cfg = G1.oracle_config(env)
    land = G1.pool_landscape(env)
    key = (
        int(env.seed),
        era,
        a,
        b,
        G1.data_fingerprint(env) if price is None else hash(pp.prices[:, a:b].tobytes()),
        tuple((round(p.share, 9), p.tags) for p in cfg.pools),
        land.fingerprint() if land is not None else None,
        bool(despike),
        intra_hour,
    )
    hit = _CACHE.get(key)
    if hit is not None:
        _CACHE.move_to_end(key)
        return hit
    seg = PricePath(pp.t0, "hour", pp.prices[:1, a:b].copy(), "real", {})
    # a gap (0) in the hourly series is held at the last price (a feed gap is the oracle's business)
    p = seg.prices[0].astype(np.int64)
    if (p <= 0).any():
        good = np.where(p > 0, np.arange(p.size), 0)
        np.maximum.accumulate(good, out=good)
        p = p[good]
        seg = PricePath(pp.t0, "hour", p[None, :], "real", {})
    if intra_hour == "bridge":
        from ybcal.data.synthetic import brownian_bridge

        lp = np.log(seg.prices[0].astype(np.float64))
        r = np.diff(lp)[None, :]
        sub = brownian_bridge(r, float(np.var(r)), BLOCKS_PER_HOUR, env.rng_for("replay", "bridge", era))
        path = lp[0] + np.concatenate(([0.0], np.cumsum(sub[0])))
        blk = np.clip(np.rint(np.exp(path)), 1, None).astype(np.int64)[None, :]
    else:
        blk = resample(seg, "block", method="loglinear").prices
    miner = None
    if land is not None and len(land.sequence):
        L = len(land.sequence)
        off = int(env.rng_for("replay", "miners", era).integers(0, L))
        miner = land.sequence[(off + np.arange(blk.shape[1])) % L][None, :]
    inp = O.generate_block_inputs(blk, cfg, rng=env.rng_for("replay", "oracle", era, a, b), miner=miner)
    valid = inp.tag_present & (inp.tag_price > 0)
    t0 = pp.t0 if pp.t0.tzinfo else pp.t0.replace(tzinfo=UTC)
    from datetime import timedelta

    rep = Replay(
        era,
        blk,
        np.where(valid, inp.tag_price, 0),
        valid,
        t0 + timedelta(hours=a),
        key,
        {
            "hours": b - a,
            "tagging_share": cfg.tagging_share,
            "miners": "real sequence" if miner is not None else "drawn from shares",
        },
    )
    _CACHE[key] = rep
    while len(_CACHE) > _CAP:
        _CACHE.popitem(last=False)
    return rep


# ---------------------------------------------------------------------------------------------------
# Events read from the real series


def wick_events(price: PricePath, threshold: float = 0.20) -> list[int]:
    """Hour indices of one-hour round trips larger than ``threshold`` (up then down, or down then up,
    each leg > threshold): aggregator prints, docs/real-data-2026-10.md §6."""
    p = price.prices[0].astype(float)
    p = np.where(p > 0, p, np.nan)
    r = np.diff(np.log(p))
    lt = math.log1p(threshold)
    out = []
    for i in range(len(r) - 1):
        a, b = r[i], r[i + 1]
        if np.isfinite(a) and np.isfinite(b) and abs(a) > lt and abs(b) > lt and a * b < 0:
            out.append(i + 1)  # the spike hour
    return out


def _smoothed(price: PricePath, k: int = 6) -> np.ndarray:
    p = price.prices[0].astype(float)
    p = np.where(p > 0, p, np.nan)
    from scipy.ndimage import median_filter

    filled = np.where(np.isfinite(p), p, np.nanmedian(p))
    return median_filter(filled, size=k, origin=(k - 1) // 2, mode="nearest")


def fall_events(
    price: PricePath, fall: float, within_hours: int, *, min_gap_hours: int = 72
) -> list[tuple[int, int]]:
    """Real falls of at least ``fall`` (fraction) within ``within_hours``, on the hourly series'
    trailing 6-hour medians (so a one-hour print is not a crash): ``(peak, trough)`` hour pairs — the
    last hour from which the fall to the trough still exceeds ``fall``, and the lowest point within
    the horizon. Falls whose peaks lie closer
    than ``min_gap_hours`` merge."""
    sm = _smoothed(price)
    n = sm.size
    out: list[tuple[int, int]] = []
    i = 0
    while i < n - 1:
        j = min(n, i + within_hours + 1)
        t = i + int(np.argmin(sm[i:j]))
        if sm[t] <= sm[i] * (1 - fall):
            # the peak: the last hour before the trough from which the fall still exceeds ``fall``
            ok = np.nonzero(sm[i : t + 1] * (1 - fall) >= sm[t])[0]
            pk = i + int(ok[-1])
            if not out or pk - out[-1][0] >= min_gap_hours:
                out.append((pk, t))
            i = t + 1
        else:
            i += 1
    return out


def merge_falls(*lists: list[tuple[int, int]], min_gap_hours: int = 72) -> list[tuple[int, int]]:
    """Union of fall lists, keeping the first of any falls whose peaks are within ``min_gap_hours``."""
    out: list[tuple[int, int]] = []
    for pk, tr in sorted(x for lst in lists for x in lst):
        if out and pk - out[-1][0] < min_gap_hours:
            out[-1] = (out[-1][0], max(out[-1][1], tr))
        else:
            out.append((pk, tr))
    return out


# ---------------------------------------------------------------------------------------------------
# Metrics


def era_blocks(rep: Replay, era: str, price: PricePath | None = None) -> tuple[int, int]:
    """Block indices ``[lo, hi)`` of ``era`` inside a replay of the full history (the replay's own
    warm-up excluded). A replay of one era answers only that era."""
    if rep.era == era or price is None:
        return min(WARMUP_BLOCKS, rep.n - 1), rep.n
    a0, _ = era_slice(price, rep.era)
    a, b = era_slice(price, era)
    lo = max((a - a0) * BLOCKS_PER_HOUR, min(WARMUP_BLOCKS, rep.n - 1))
    hi = min(rep.n, (b - a0) * BLOCKS_PER_HOUR)
    return lo, max(lo, hi)


_EVENTS: dict[tuple, dict[str, list]] = {}


def events(price: PricePath) -> dict[str, list]:
    """Real events of an hourly series (memoised): ``falls`` (≥ 30 % in a day or ≥ 50 % in a week —
    what HALT-3 should catch) and ``near`` (≥ 20 % in a week — a halt near one is not false) as
    ``(peak, trough)`` hour pairs (``falls1d``: the one-day ones alone), and ``wicks`` (one-hour
    round trips > 20 %) as hour indices."""
    k = (price.n_steps, hash(price.prices[0, :: max(1, price.n_steps // 4096)].tobytes()), price.t0)
    hit = _EVENTS.get(k)
    if hit is None:
        hit = {
            "falls": merge_falls(fall_events(price, 0.30, 24), fall_events(price, 0.50, 7 * 24)),
            "falls1d": fall_events(price, 0.30, 24),
            "near": fall_events(price, 0.20, 7 * 24),
            "wicks": wick_events(price),
        }
        _EVENTS[k] = hit
    return hit


def prices_for(rep: Replay, params: Mapping) -> O.PriceSeries:
    (wf, wm, ws), (ff, fm, fs) = O.windows_and_fills(params)
    pf, pm, ps = (rep.median(wf, ff), rep.median(wm, fm), rep.median(ws, fs))
    xm = V.price_mint(pf, pm, ps)
    xc = V.price_claim(pm, ps)
    h3 = V.halt3_divergence(pf, pm, ps, int(params["divergenceBps"]))
    return O.PriceSeries(pf, pm, ps, xm, xc, h3, xm == O.UNDEF)


def sigma_metrics(
    rep: Replay,
    params: Mapping,
    prefix: str = "replay",
    era: str | None = None,
    price: PricePath | None = None,
) -> dict[str, float]:
    """σ̂ on the replayed pFast and the multiplier at ``params``' reference and cap, over ``era``
    (default: the replay's own) — ``price`` locates an era inside a full-history replay."""
    (wf, _, _), (ff, _, _) = O.windows_and_fills(params)
    pf = rep.median(wf, ff).astype(np.int64)
    e = era or rep.era
    lo, hi = era_blocks(rep, e, price)
    sh = S.sigma_hat_bps(params, pf)[:, lo:hi]
    x = sh[sh >= 0].astype(float)
    out: dict[str, float] = {}
    for q in (25, 50, 75, 95, 99):
        out[f"{prefix}_sigma_hat_p{q}_{e}"] = float(np.percentile(x, q)) if x.size else math.nan
    m = S.sigma_series(params, pf)[:, lo:hi]
    und = S.undefined_sample_mask(params, pf)[:, lo:hi]
    cap = max(int(params["sigmaMultMaxBps"]), BPS)
    body = m.astype(float) / BPS
    q = np.percentile(body, [50, 90, 99]) if body.size else [math.nan] * 3
    for name, x in zip(("p50", "p90", "p99"), q, strict=True):
        out[f"{prefix}_mult_{name}_{e}"] = float(x)
    at_cap = m >= cap
    tot = max(m.size, 1)
    out[f"{prefix}_cap_vol_share_{e}"] = float((at_cap & ~und).sum() / tot)
    out[f"{prefix}_cap_undefined_share_{e}"] = float((at_cap & und).sum() / tot)
    out[f"{prefix}_floor_share_{e}"] = float((m <= BPS).sum() / tot)
    # realised vol of the true price at the sampling step and at one day (context, like-for-like)
    tp = rep.true[0, lo:hi].astype(float)
    step = int(params["volStep"])
    r1 = np.diff(np.log(tp[::step]))
    rd = np.diff(np.log(tp[::BLOCKS_PER_DAY]))
    out[f"{prefix}_true_vol_step_bps_{e}"] = float(r1.std() * math.sqrt(BLOCKS_PER_YEAR / step) * BPS)
    out[f"{prefix}_true_vol_day_bps_{e}"] = (
        float(rd.std() * math.sqrt(365.0) * BPS) if rd.size > 2 else math.nan
    )
    return out


def halt_metrics(
    rep: Replay,
    params: Mapping,
    price: PricePath | None = None,
    prefix: str = "replay",
    era: str | None = None,
) -> dict[str, float]:
    """Over ``era`` (default: the replay's own): HALT-3 and NO_PRICE hours per year; with ``price``
    also HALT-3 detection of real falls (fires within a day of the start of a ≥ 30 % one-day or
    ≥ 50 % one-week fall: caught when HALT-3 fires between its peak and a day after its trough;
    falls whose window is mostly NO_PRICE cannot test HALT-3 and are left out),
    *false* HALT-3 hours (outside every ≥ 20 % weekly fall, from a day before its peak to two days
    after its trough) and HALT-3 hours in the day after a real one-hour wick > 20 %."""
    ps = prices_for(rep, params)
    e = era or rep.era
    lo, hi = era_blocks(rep, e, price)
    out = {
        f"{prefix}_halt3_h_per_year_{e}": float(ps.halt3[:, lo:hi].mean()) * _PER_YEAR_H,
        f"{prefix}_no_price_h_per_year_{e}": float(ps.no_price[:, lo:hi].mean()) * _PER_YEAR_H,
    }
    if price is None:
        return out
    a0, _ = era_slice(price, rep.era)
    ev = events(price)
    B, Dy = BLOCKS_PER_HOUR, BLOCKS_PER_DAY

    def local(hours: list[int]) -> list[int]:
        return [(h - a0) * B for h in hours if lo <= (h - a0) * B < hi]

    # a fall is caught when HALT-3 fires between its peak and a day after its trough
    falls = [(pk, tr) for pk, tr in ev["falls"] if lo <= (pk - a0) * B < hi]
    def testable(pk: int, tr: int) -> bool:  # HALT-3 needs the medians; mostly NO_PRICE = untestable
        return bool(ps.no_price[0, (pk - a0) * B : (tr - a0) * B + Dy].mean() < 0.5)

    falls = [f for f in falls if testable(*f)]
    hit = [bool(ps.halt3[0, (pk - a0) * B : (tr - a0) * B + Dy].any()) for pk, tr in falls]
    near = np.zeros(rep.n, dtype=bool)
    for pk, tr in ev["near"]:
        b0, b1 = (pk - a0 - 24) * B, (tr - a0) * B + 2 * Dy
        if b1 > 0 and b0 < rep.n:
            near[max(0, b0) : min(rep.n, b1)] = True
    nb = max(hi - lo, 1)
    out[f"{prefix}_halt3_false_h_per_year_{e}"] = (
        float((ps.halt3[0, lo:hi] & ~near[lo:hi]).sum()) / nb * _PER_YEAR_H
    )
    f1 = [(pk, tr) for pk, tr in ev["falls1d"] if lo <= (pk - a0) * B < hi and testable(pk, tr)]
    hit1 = [bool(ps.halt3[0, (pk - a0) * B : (tr - a0) * B + Dy].any()) for pk, tr in f1]
    out[f"{prefix}_falls1d_{e}"] = float(len(f1))
    out[f"{prefix}_fall1d_recall_{e}"] = float(np.mean(hit1)) if hit1 else math.nan
    out[f"{prefix}_falls_{e}"] = float(len(falls))
    out[f"{prefix}_fall_recall_{e}"] = float(np.mean(hit)) if hit else math.nan
    wicks = local(ev["wicks"])
    wh = [float(ps.halt3[0, b : b + Dy].sum()) / B for b in wicks]
    out[f"{prefix}_wicks_{e}"] = float(len(wicks))
    out[f"{prefix}_wick_halt3_h_{e}"] = float(np.mean(wh)) if wh else math.nan
    return out


# ---------------------------------------------------------------------------------------------------
# Evidence bundles for the studies

#: Eras whose metrics the studies attach to every candidate (a slice of one full-history replay).
STUDY_ERAS = ("full", "last365", "2021-22", "2023", "2025-26")


def sigma_evidence(env, params: Mapping) -> dict[str, float]:
    """``replay_*`` σ̂ / multiplier metrics per era, plus ``replay_despiked_*`` on the full history
    with the one-hour prints removed. Empty without an hourly real price."""
    from ybcal.studies import g1_price_windows as G1

    price = G1.real_price(env)
    rep = replay(env, "full")
    if rep is None or price is None:
        return {}
    out: dict[str, float] = {}
    for e in STUDY_ERAS:
        if era_blocks(rep, e, price)[1] - era_blocks(rep, e, price)[0] > BLOCKS_PER_DAY * 30:
            out.update(sigma_metrics(rep, params, era=e, price=price))
    dsp = replay(env, "full", despike=True)
    if dsp is not None:
        out.update(sigma_metrics(dsp, params, prefix="replay_despiked", era="full", price=price))
    return out


def halt_evidence(env, params: Mapping) -> dict[str, float]:
    """``replay_*`` HALT-3 / NO_PRICE metrics per era (see :func:`halt_metrics`)."""
    from ybcal.studies import g1_price_windows as G1

    price = G1.real_price(env)
    rep = replay(env, "full")
    if rep is None or price is None:
        return {}
    out: dict[str, float] = {}
    for e in STUDY_ERAS:
        lo, hi = era_blocks(rep, e, price)
        if hi - lo > BLOCKS_PER_DAY * 30:
            out.update(halt_metrics(rep, params, price, era=e))
    return out

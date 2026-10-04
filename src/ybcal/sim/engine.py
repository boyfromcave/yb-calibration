"""Block clock, two resolutions, path batching, seeds (owner: WP-3; PLAN §3.3, §6.4).

Block mode
----------
:func:`simulate_blocks` composes pure, vectorised stage functions over arrays shaped
``(paths, n_blocks)`` in the node's SNAP order (``ComputeSnapshot``, state.cpp:1074-1281 @ 7702d22).
Column ``j`` is height ``inputs.start_height + j`` and column 0 is ``startHeight`` (nothing before
it exists: windows are truncated there and earlier snapshots are virtual, exactly as on the node).

=============  ==================================================  ====================================
stage          built-in step (this module / WP-3)                  hooked by
=============  ==================================================  ====================================
``judge``      — (REG-4 runs first in SNAP; hook only)             WP-4/WP-5 (G6 judgements)
``activation`` ACT-1..3 + ACT-4/6 series (``activation.simulate``  WP-5
               when present; else the exact internal kernels)
``attest``     ``attest.simulate`` when present; else unarmed      WP-5
``pin``        PIN-1 keys from the BundleLog aMint range + quotes  WP-5 (PIN-2 seqs ride on attest)
``price``      PRICE-1/2 medians (fast path; exact recompute at    —
               heights with pinned keys), xMint, xClaim
``sigma``      SIGMA-1 (``vkernels.sigma_mult_series``)            —
``supply``     issuedZat (subsidy since start), MINT-6 cap         —
``halts``      haltMask: NOT_ACTIVE, NO_PRICE, HALT-2, HALT-3,     —
               PARTICIPATION, ENFORCEMENT
``vaults``     after the hooks: globalRatio + HALT-2 recomputed    WP-4 (fills supply/collateral)
               from ``supply_cents`` / ``collateral_zat``
``dormancy``   — (hook only, last in SNAP)                         WP-5
=============  ==================================================  ====================================

A hook is ``hook(stage, params, inputs, series) -> None``; every hook sees every stage, runs after
that stage's built-in step, and may mutate ``series`` in place (later stages read what it wrote,
e.g. a ``pin`` hook can set ``series.pinned_pools``). Hooks must be module-level callables (or
picklable objects) when ``workers > 1``.

Hour mode
---------
:class:`OracleTransferKernel` maps an hourly true-price history to hourly ``(pFast, pMid, pSlow,
pMint, pClaim, haltMask, σ)``: each median is a rolling lower median of the (log-linearly
interpolated) true path over the window, at a fitted lag, times a fitted bias and noise.
:func:`calibrate_kernel` fits it from block-mode runs, :func:`simulate_hours` applies it, and
:func:`kernel_error` measures it against a block-mode run (PLAN §3.3, §8: kernel error ≤ tolerance).
"""

from __future__ import annotations

import math
import multiprocessing as mp
import os
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field, fields
from typing import Any, Literal

import numpy as np

from ybcal.model import vkernels as V
from ybcal.sim import oracle as O
from ybcal.sim import sigma as S
from ybcal.sim import supply as SUP
from ybcal.units import BLOCKS_PER_DAY, BLOCKS_PER_HOUR, BPS, PRICE_MAX, PRICE_MIN

OWNER_WP = "WP-3"

UNDEF = V.UNDEF

#: haltMask bits (src/yellowback/view.h:565-571 @ 7702d22, declaration order).
HALT_NOT_ACTIVE = 1 << 0
HALT_NO_PRICE = 1 << 1
HALT_PARTICIPATION = 1 << 2
HALT_GLOBAL_RATIO = 1 << 3
HALT_DIVERGENCE = 1 << 4
HALT_ENFORCEMENT = 1 << 5
#: The bits decided by prices alone (what hour mode reproduces).
PRICE_HALT_BITS = HALT_NO_PRICE | HALT_DIVERGENCE

#: ActivationStatus (view.h:197).
SIGNALING, LOCKED_IN, ACTIVE = 0, 1, 2

#: Hook stages in SNAP order. The contract agreed with WP-5 names the middle eight; ``judge`` and
#: ``dormancy`` (first and last in SNAP) are hook-only extensions.
STAGES: tuple[str, ...] = (
    "judge",
    "activation",
    "attest",
    "pin",
    "price",
    "sigma",
    "supply",
    "halts",
    "vaults",
    "dormancy",
)

Hook = Callable[[str, Mapping, "BlockInputs", "BlockSeries"], None]
ActivationMode = Literal["auto", "internal", "always_active"]
AttestMode = Literal["auto", "unarmed"]

#: Measured single-core cost of block mode (seconds per path per simulated day, mainnet windows,
#: oracle generation included); see docs/architecture.md "Simulator core (WP-3)".
SECONDS_PER_PATH_DAY: float = 0.0045


# ---------------------------------------------------------------------------------------------------
# Inputs


def _2d(a, dtype) -> np.ndarray:
    x = np.asarray(a, dtype=dtype)
    return x[None, :] if x.ndim == 1 else x


@dataclass
class BlockInputs:
    """Everything a block-mode run reads, ``(paths, n)`` arrays; column ``j`` is height
    ``start_height + j``.

    * ``true_price`` int64 µUSD/YEC — the market (agents and metrics read it; rules never do).
    * ``tag_present`` bool — the block carries a valid coinbase tag (TAG-1..5).
    * ``tag_price`` int64 — its quote, 0 = signal-only (TAG-3).
    * ``tag_pool`` int16 — the tag's payout key as a pool id (0..63), −1 = no tag.
    * ``signal_bit`` bool — the tag's signal flag.
    * ``attest`` — optional dict passed through to WP-5's ``attest.simulate``; the engine itself
      reads ``bundle_present`` (bool) and ``bundle_a_mint`` (int64, ≤ 0 undefined) from it for PIN-1
      when no ``AttestSeries`` provides them.
    * ``subsidy_zat`` — optional ``(n,)`` override of ``GetBlockSubsidy`` per column.

    Normalisation mirrors TAG-2 (reference ``tag_is_valid``): a quote outside
    [PRICE_MIN, PRICE_MAX] makes the whole tag invalid, i.e. absent (no signal either).
    """

    true_price: np.ndarray
    tag_present: np.ndarray
    tag_price: np.ndarray
    tag_pool: np.ndarray
    signal_bit: np.ndarray
    start_height: int
    attest: dict | None = None
    subsidy_zat: np.ndarray | None = None
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.true_price = _2d(self.true_price, np.int64)
        shape = self.true_price.shape
        self.tag_price = np.broadcast_to(_2d(self.tag_price, np.int64), shape).copy()
        present = np.broadcast_to(_2d(self.tag_present, bool), shape).copy()
        bad = (self.tag_price != 0) & ((self.tag_price < PRICE_MIN) | (self.tag_price > PRICE_MAX))
        present &= ~bad
        self.tag_present = present
        self.tag_price = np.where(present, self.tag_price, 0)
        self.tag_pool = np.where(present, np.broadcast_to(_2d(self.tag_pool, np.int16), shape), -1).astype(
            np.int16
        )
        self.signal_bit = np.broadcast_to(_2d(self.signal_bit, bool), shape) & present
        self.start_height = int(self.start_height)
        if self.subsidy_zat is not None:
            self.subsidy_zat = np.asarray(self.subsidy_zat, dtype=np.int64).reshape(-1)
            if self.subsidy_zat.size != shape[1]:
                raise ValueError("subsidy_zat must have one entry per block")

    @property
    def n_paths(self) -> int:
        return int(self.true_price.shape[0])

    @property
    def n_blocks(self) -> int:
        return int(self.true_price.shape[1])

    @property
    def heights(self) -> np.ndarray:
        return self.start_height + np.arange(self.n_blocks, dtype=np.int64)

    def slice_paths(self, sl: slice) -> BlockInputs:
        P = self.n_paths
        att = None
        if self.attest is not None:
            att = {
                k: (v[sl] if isinstance(v, np.ndarray) and v.ndim == 2 and v.shape[0] == P else v)
                for k, v in self.attest.items()
            }
        return BlockInputs(
            self.true_price[sl],
            self.tag_present[sl],
            self.tag_price[sl],
            self.tag_pool[sl],
            self.signal_bit[sl],
            self.start_height,
            att,
            self.subsidy_zat,
            dict(self.meta),
        )

    @classmethod
    def perfect(cls, true_price, start_height: int, *, signal: bool = True) -> BlockInputs:
        """Every block tagged by pool 0 quoting the true price (an ideal oracle)."""
        tp = _2d(true_price, np.int64)
        return cls(
            tp,
            np.ones(tp.shape, bool),
            tp,
            np.zeros(tp.shape, np.int16),
            np.full(tp.shape, signal),
            start_height,
        )


def prices_from(price_path) -> tuple[np.ndarray, str]:
    """Adapter for WP-2's ``PricePath`` (not merged yet): any object with ``.prices`` (int64
    ``(paths, n)`` µUSD) and ``.resolution`` (``"block"`` | ``"hour"``), or a plain array (taken as
    block resolution). When ``ybcal.data.pricepath.PricePath`` lands this stays a one-liner."""
    if hasattr(price_path, "prices"):
        return _2d(price_path.prices, np.int64), str(getattr(price_path, "resolution", "block"))
    return _2d(price_path, np.int64), "block"


# ---------------------------------------------------------------------------------------------------
# Outputs


@dataclass
class BlockSeries:
    """Per-height snapshot fields, ``(paths, n)``; undefined prices/ratios are ``UNDEF`` (−1)."""

    params: Any
    start_height: int
    true_price: np.ndarray  # reference to the input (not a copy)
    p_fast: np.ndarray
    p_mid: np.ndarray
    p_slow: np.ndarray
    x_mint: np.ndarray  # snapshot pMint (cross-section; the plan's xMint)
    x_claim: np.ndarray
    sigma_mult_bps: np.ndarray
    halt_mask: np.ndarray  # uint16, view.h bit values
    activation_status: np.ndarray  # int8 SIGNALING/LOCKED_IN/ACTIVE
    signal_count: np.ndarray
    participation_halt: np.ndarray  # ACT-4 (bool)
    enforcement_halt: np.ndarray  # ACT-6 (bool)
    issued_zat: np.ndarray  # broadcast view of the (n,) issuance series
    supply_cap_cents: np.ndarray  # MINT-6 cap at the snapshot's xMint (UNDEF: none)
    supply_cents: np.ndarray  # placeholders, filled by the WP-4 "vaults" hook
    collateral_zat: np.ndarray
    global_ratio_bps: np.ndarray
    pin1_triggered: np.ndarray  # bool
    pinned_pools: np.ndarray  # uint64 bitmask of PIN-1 keys pinned at H
    a_mint: np.ndarray  # from WP-5 (UNDEF when unarmed / absent)
    a_claim: np.ndarray
    armed: np.ndarray  # bool
    pinned_seqs: Any = None  # PIN-2, from WP-5's AttestSeries
    attest: Any = None  # WP-5's AttestSeries as returned
    activation_source: str = "internal"  # "wp5" | "internal" | "always_active"
    activation_series: Any = None  # WP-5 ActivationSeries when activation_source == "wp5"
    attest_source: str = "unarmed"  # "wp5" | "unarmed"
    pinned_recomputed: int = 0  # heights recomputed exactly (PIN-1)
    extras: dict = field(default_factory=dict)  # hook outputs (WP-4 vault book, judgements, …)
    rng: Any = None

    ARRAY_FIELDS = (
        "p_fast",
        "p_mid",
        "p_slow",
        "x_mint",
        "x_claim",
        "sigma_mult_bps",
        "halt_mask",
        "activation_status",
        "signal_count",
        "participation_halt",
        "enforcement_halt",
        "supply_cap_cents",
        "supply_cents",
        "collateral_zat",
        "global_ratio_bps",
        "pin1_triggered",
        "pinned_pools",
        "a_mint",
        "a_claim",
        "armed",
    )

    @property
    def n_paths(self) -> int:
        return int(self.p_fast.shape[0])

    @property
    def n_blocks(self) -> int:
        return int(self.p_fast.shape[1])

    @property
    def heights(self) -> np.ndarray:
        return self.start_height + np.arange(self.n_blocks, dtype=np.int64)

    @property
    def height0(self) -> int:
        """Height of column 0 (= ``start_height``). WP-5's ``attest.simulate`` and
        :func:`_attest_frame` read ``series.height0``; without it they fell back to 0 and walked the
        attestation layer one block behind the engine's columns (found by the devnet, D-RD-DEV-5)."""
        return int(self.start_height)

    @property
    def pinned(self) -> np.ndarray:
        """True where at least one PIN-1 key is pinned at H."""
        return self.pinned_pools != 0

    def combined_prices(self):
        """PRICE-2 per-transaction prices at each snapshot (math.h:286): ``(pMint, pClaim, pEmerg)``
        combining x with a where ARMED, x alone otherwise (pEmerg = UNDEF unarmed)."""
        pm, pc, pe = V.price_combine(self.x_mint, self.x_claim, self.a_mint, self.a_claim)
        return (
            np.where(self.armed, pm, self.x_mint),
            np.where(self.armed, pc, self.x_claim),
            np.where(self.armed, pe, UNDEF),
        )

    def snapshot(self, path: int, j: int) -> dict:
        """One snapshot as Python values (``None`` = undefined), for comparisons with the model."""

        def opt(a):
            v = int(a[path, j])
            return v if v > 0 else None

        return {
            "height": self.start_height + j,
            "p_fast": opt(self.p_fast),
            "p_mid": opt(self.p_mid),
            "p_slow": opt(self.p_slow),
            "p_mint": opt(self.x_mint),
            "p_claim": opt(self.x_claim),
            "sigma_mult_bps": int(self.sigma_mult_bps[path, j]),
            "halt_mask": int(self.halt_mask[path, j]),
            "activation": int(self.activation_status[path, j]),
            "signal_count": int(self.signal_count[path, j]),
            "issued_zat": int(self.issued_zat[path, j]),
            "global_ratio_bps": opt(self.global_ratio_bps),
        }

    @classmethod
    def concat(cls, parts: Sequence[BlockSeries]) -> BlockSeries:
        if len(parts) == 1:
            return parts[0]
        first = parts[0]
        kw = {f.name: getattr(first, f.name) for f in fields(cls)}
        for name in cls.ARRAY_FIELDS:
            kw[name] = np.concatenate([getattr(p, name) for p in parts], axis=0)
        kw["true_price"] = np.concatenate([p.true_price for p in parts], axis=0)
        P = kw["p_fast"].shape[0]
        kw["issued_zat"] = np.broadcast_to(first.issued_zat[0], (P, first.n_blocks))
        kw["pinned_recomputed"] = sum(p.pinned_recomputed for p in parts)
        kw["attest"] = [p.attest for p in parts] if any(p.attest is not None for p in parts) else None
        kw["pinned_seqs"] = (
            [p.pinned_seqs for p in parts] if any(p.pinned_seqs is not None for p in parts) else None
        )
        ex: dict = {}
        for p in parts:
            for k, v in p.extras.items():
                ex.setdefault(k, []).append(v)
        kw["extras"] = ex
        return cls(**kw)


# ---------------------------------------------------------------------------------------------------
# Stage functions (pure; vectorised over paths × blocks)


def internal_activation(params: Mapping, signal_bit) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """ACT-1..3 and ACT-4/6 exactly as state.cpp:1080-1090, 1242-1249 (the WP-1 kernels
    ``vkernels.signal_counts`` / ``participation_halt_series`` / ``enforcement_halt_series``).
    Returns ``(status int8, signal_count int64, participation_halt, enforcement_halt)``."""
    sig = np.asarray(signal_bit, dtype=bool)
    W = int(params["signalWindow"])
    count = V.signal_counts(sig, W)
    n = sig.shape[-1]
    idx = np.arange(n)
    thr = max(0, int(params["activationThreshold"]))
    lock_ok = (idx >= W - 1) & (count >= thr)
    lock = np.where(lock_ok.any(axis=-1), lock_ok.argmax(axis=-1), n + 2**40)[..., None]
    act_at = lock + int(params["activationDelay"])
    status = np.where(idx < lock, SIGNALING, np.where(idx >= act_at, ACTIVE, LOCKED_IN)).astype(np.int8)
    active = status == ACTIVE
    part = V.participation_halt_series(
        count, active, int(params["activationThreshold"]), int(params["participationFloor"])
    )
    enf = V.enforcement_halt_series(
        count, active, int(params["enforcementResume"]), int(params["enforcementFloor"])
    )
    return status, count, part, enf


def _trailing_extreme(a: np.ndarray, w: int, fn: str) -> np.ndarray:
    """``out[..., i] = min|max(a[..., i−w+1 : i+1])`` (window truncated at 0) via scipy's O(n)
    filters (origin set so the window trails)."""
    from scipy.ndimage import maximum_filter1d, minimum_filter1d

    f = minimum_filter1d if fn == "min" else maximum_filter1d
    big = np.iinfo(np.int64).max if fn == "min" else np.iinfo(np.int64).min
    if w <= 1:
        return a.copy()
    return f(a, size=w, axis=-1, mode="constant", cval=big, origin=(w - 1) // 2)


def _prior_window(a: np.ndarray, w: int, fn: str) -> np.ndarray:
    """min|max over the PIN window ``[i − w, i − 1]`` (``(H − 1 − W, H − 1]``)."""
    big = np.iinfo(np.int64).max if fn == "min" else np.iinfo(np.int64).min
    t = _trailing_extreme(a, w, fn)
    out = np.full(a.shape, big, dtype=np.int64)
    out[..., 1:] = t[..., :-1]
    return out


def _prior_count(mask: np.ndarray, w: int) -> np.ndarray:
    c = np.zeros((*mask.shape[:-1], mask.shape[-1] + 1), dtype=np.int64)
    np.cumsum(mask, axis=-1, out=c[..., 1:])
    n = mask.shape[-1]
    i = np.arange(n)
    return c[..., i] - c[..., np.maximum(i - w, 0)]


def pin1_trigger_series(params: Mapping, bundle_present, bundle_a_mint) -> np.ndarray:
    """PIN-1's arming test at every height (state.cpp:1124-1134): over the BundleLog rows of
    ``[max(H − pinWindow, start), H − 1]`` — at least ``max(1, pinMinBundles)`` rows, and
    ``(aHi − aLo)·10^4 > max(0, pinDeltaBps)·aLo`` over the defined (``> 0``) aMint values."""
    pres = np.asarray(bundle_present, dtype=bool)
    am = np.asarray(bundle_a_mint, dtype=np.int64)
    w = int(params["pinWindow"])
    if w <= 0:
        return np.zeros(pres.shape, dtype=bool)
    rows = _prior_count(pres, w)
    defined = pres & (am > 0)
    big = np.iinfo(np.int64).max
    lo = _prior_window(np.where(defined, am, big), w, "min")
    hi = _prior_window(np.where(defined, am, np.iinfo(np.int64).min), w, "max")
    has = lo != big
    lo_s = np.where(has, lo, 1)
    hi_s = np.where(has, hi, 1)
    return (
        (rows >= max(1, int(params["pinMinBundles"])))
        & has
        & ((hi_s - lo_s) * BPS > max(0, int(params["pinDeltaBps"])) * lo_s)
    )


def pin1_keys(params: Mapping, trigger, tag_price, tag_pool, tag_present) -> np.ndarray:
    """PIN-1 keys at each triggered height (state.cpp:1135-1147): a pool whose quote tags in
    ``[max(H − pinWindow, start), H − 1]`` number ≥ ``max(1, pinMinTags)`` and all carry one price.
    Returns the uint64 pool bitmask."""
    trig = np.asarray(trigger, dtype=bool)
    out = np.zeros(trig.shape, dtype=np.uint64)
    if not trig.any():
        return out
    w = int(params["pinWindow"])
    tp = np.asarray(tag_price, dtype=np.int64)
    pool = np.asarray(tag_pool)
    quote = np.asarray(tag_present, dtype=bool) & (tp > 0)
    min_tags = max(1, int(params["pinMinTags"]))
    big, small = np.iinfo(np.int64).max, np.iinfo(np.int64).min
    for k in np.unique(pool[quote & (pool >= 0)]).tolist():
        qk = quote & (pool == k)
        cnt = _prior_count(qk, w)
        cand = trig & (cnt >= min_tags)
        if not cand.any():
            continue
        lo = _prior_window(np.where(qk, tp, big), w, "min")
        hi = _prior_window(np.where(qk, tp, small), w, "max")
        pinned = cand & (lo == hi)
        out |= pinned.astype(np.uint64) << np.uint64(k)
    return out


def halt_mask_series(
    params: Mapping,
    status,
    x_mint,
    p_fast,
    p_mid,
    p_slow,
    participation,
    enforcement,
    supply_cents=None,
    collateral_zat=None,
) -> np.ndarray:
    """haltMask (state.cpp:1232-1250) as uint16."""
    mask = np.zeros(np.shape(x_mint), dtype=np.uint16)
    mask |= np.where(np.asarray(status) != ACTIVE, HALT_NOT_ACTIVE, 0).astype(np.uint16)
    mask |= np.where(np.asarray(x_mint) == UNDEF, HALT_NO_PRICE, 0).astype(np.uint16)
    if supply_cents is not None and collateral_zat is not None and np.any(supply_cents):
        h2 = SUP.halt2_mask(collateral_zat, x_mint, supply_cents, int(params["globalRatioHaltBps"]))
        mask |= np.where(h2, HALT_GLOBAL_RATIO, 0).astype(np.uint16)
    h3 = V.halt3_divergence(p_fast, p_mid, p_slow, int(params["divergenceBps"]))
    mask |= np.where(h3, HALT_DIVERGENCE, 0).astype(np.uint16)
    mask |= np.where(participation, HALT_PARTICIPATION, 0).astype(np.uint16)
    mask |= np.where(enforcement, HALT_ENFORCEMENT, 0).astype(np.uint16)
    return mask


def refresh_halt2(params: Mapping, series: BlockSeries) -> None:
    """Recompute ``global_ratio_bps`` and the HALT-2 bit from ``supply_cents`` / ``collateral_zat``
    (run after the ``vaults`` hooks; idempotent)."""
    series.global_ratio_bps = SUP.global_ratio_bps(series.collateral_zat, series.x_mint, series.supply_cents)
    h2 = SUP.halt2_mask(
        series.collateral_zat, series.x_mint, series.supply_cents, int(params["globalRatioHaltBps"])
    )
    m = series.halt_mask & np.uint16(~HALT_GLOBAL_RATIO & 0xFFFF)
    series.halt_mask = (m | np.where(h2, HALT_GLOBAL_RATIO, 0).astype(np.uint16)).astype(np.uint16)


# ---------------------------------------------------------------------------------------------------
# WP-5 plug-ins


def _wp5(name: str, attr: str = "simulate"):
    try:
        mod = __import__(f"ybcal.sim.{name}", fromlist=[attr])
    except ImportError:
        return None
    return getattr(mod, attr, None)


def _issued(params: Mapping, inputs: BlockInputs) -> np.ndarray:
    if inputs.subsidy_zat is not None:
        return np.cumsum(inputs.subsidy_zat)
    return SUP.issued_zat_series(inputs.start_height, inputs.n_blocks, SUP.schedule_for(params))


#: Maximum oracle → attest → PIN-1 → medians passes when attestation runs (D-WP5-3 item 2). The
#: coupled system is causal (PIN-2 at H reads pMint < H, PIN-1 at H reads BundleLog rows < H), so its
#: solution is unique and every pass extends the correct prefix; in practice one or two passes.
MAX_PIN_PASSES = 8


class _PMintView:
    """The series as ``attest.simulate`` sees it, with ``p_mint`` = a given xMint array."""

    def __init__(self, series: Any, p_mint: np.ndarray) -> None:
        self._series = series
        self.p_mint = p_mint

    def __getattr__(self, name: str) -> Any:
        return getattr(self._series, name)


def _apply_attest(s: BlockSeries, att: Any, shape: tuple[int, int]) -> None:
    s.attest, s.attest_source = att, "wp5"
    for name in ("a_mint", "a_claim"):
        v = getattr(att, name, None)
        if v is not None:
            setattr(s, name, np.asarray(v, dtype=np.int64).reshape(shape))
    if getattr(att, "armed", None) is not None:
        s.armed = np.asarray(att.armed, dtype=bool).reshape(shape)
    s.pinned_seqs = getattr(att, "pinned_seqs", None)


def _apply_pin1(params: Mapping, inputs: BlockInputs, s: BlockSeries, shape: tuple[int, int]) -> None:
    trig = getattr(s.attest, "pin1_triggered", None) if s.attest is not None else None
    if trig is None:
        src = (
            s.attest
            if (s.attest is not None and getattr(s.attest, "bundle_present", None) is not None)
            else None
        )
        bp = (
            getattr(src, "bundle_present", None)
            if src is not None
            else (inputs.attest or {}).get("bundle_present")
        )
        ba = (
            getattr(src, "bundle_a_mint", None)
            if src is not None
            else (inputs.attest or {}).get("bundle_a_mint")
        )
        if bp is not None and ba is not None:
            trig = pin1_trigger_series(params, np.asarray(bp).reshape(shape), np.asarray(ba).reshape(shape))
    if trig is not None:
        s.pin1_triggered = np.asarray(trig, dtype=bool).reshape(shape)
        s.pinned_pools = pin1_keys(
            params, s.pin1_triggered, inputs.tag_price, inputs.tag_pool, inputs.tag_present
        )


def _apply_prices(params: Mapping, inputs: BlockInputs, s: BlockSeries) -> None:
    ps = O.price_series(
        params, inputs.tag_price, inputs.tag_present, tag_pool=inputs.tag_pool, pinned_pools=s.pinned_pools
    )
    s.p_fast, s.p_mid, s.p_slow, s.x_mint, s.x_claim = ps.p_fast, ps.p_mid, ps.p_slow, ps.x_mint, ps.x_claim
    s.pinned_recomputed = ps.recomputed


def pin2_trigger_mask(params: Mapping, p_mint: np.ndarray, height0: int, start: int) -> np.ndarray:
    """PIN-2's trigger per column from xMint, as ``attest.simulate`` computes it (state.cpp:1151):
    pMint at H − 1 and H − 1 − pinWindow both defined and differing by more than pinDeltaBps; the
    only way the attestation layer reads the engine's prices."""
    pm = np.asarray(p_mint, dtype=np.int64)
    n = pm.shape[-1]
    pw = int(params["pinWindow"])
    delta = max(0, int(params["pinDeltaBps"]))
    j1 = np.arange(n) - 1
    j0 = j1 - pw
    ok = (j0 >= 0) & (j1 >= 0)
    x1 = np.where(ok, pm[..., np.clip(j1, 0, n - 1)], -1)
    x0 = np.where(ok, pm[..., np.clip(j0, 0, n - 1)], -1)
    real = (height0 + np.arange(n) - 1 - pw) >= start
    lo = np.minimum(x1, x0)
    hi = np.maximum(x1, x0)
    return (x1 > 0) & (x0 > 0) & real & ((hi - lo) * 10_000 > delta * lo)


def _attest_frame(inputs: BlockInputs, s: BlockSeries) -> tuple[int, int]:
    """``(height0, start_height)`` exactly as ``attest.simulate`` resolves them for this run."""
    a = inputs.attest if isinstance(inputs.attest, Mapping) else {}

    def get(obj: Any, name: str, default: Any = None) -> Any:
        if obj is None:
            return default
        if isinstance(obj, Mapping):
            return obj.get(name, default)
        return getattr(obj, name, default)

    h0 = int(a.get("height0", get(s, "height0", get(inputs, "height0", 0))))
    st_default = get(inputs, "start_height", s.params["startHeight"])
    st = int(a.get("start_height", get(s, "start_height", st_default)))
    return h0, st


def _pin_fixed_point(params: Mapping, inputs: BlockInputs, s: BlockSeries, shape: tuple[int, int],
                     att_fn: Callable) -> None:
    """Re-run attestation with the engine's xMint until the PIN-2 trigger mask it reads no longer
    changes (then attest, PIN-1 and the medians are mutually consistent). The first pass saw no
    pMint (no PIN-2 trigger). Records ``s.extras["pin_passes"]`` and ``["pin_fixed_point"]``."""
    h0, st = _attest_frame(inputs, s)
    fed: np.ndarray | None = None
    passes = 1
    converged = False
    while True:
        mask = pin2_trigger_mask(params, s.x_mint, h0, st)
        if (fed is None and not mask.any()) or (fed is not None and np.array_equal(mask, fed)):
            converged = True
            break
        if passes >= MAX_PIN_PASSES:
            break
        fed = mask
        _apply_attest(s, att_fn(params, inputs, _PMintView(s, s.x_mint.copy())), shape)
        _apply_pin1(params, inputs, s, shape)
        _apply_prices(params, inputs, s)
        passes += 1
    s.extras["pin_passes"] = passes
    s.extras["pin_fixed_point"] = converged


def _simulate_chunk(
    params: Mapping,
    inputs: BlockInputs,
    hooks: Sequence[Hook],
    rng,
    activation_mode: ActivationMode,
    attest_mode: AttestMode,
) -> BlockSeries:
    P, n = inputs.n_paths, inputs.n_blocks
    shape = (P, n)
    und = np.full(shape, UNDEF, dtype=np.int64)
    s = BlockSeries(
        params=params,
        start_height=inputs.start_height,
        true_price=inputs.true_price,
        p_fast=und.copy(),
        p_mid=und.copy(),
        p_slow=und.copy(),
        x_mint=und.copy(),
        x_claim=und.copy(),
        sigma_mult_bps=np.full(shape, BPS, dtype=np.int64),
        halt_mask=np.zeros(shape, np.uint16),
        activation_status=np.zeros(shape, np.int8),
        signal_count=np.zeros(shape, np.int64),
        participation_halt=np.zeros(shape, bool),
        enforcement_halt=np.zeros(shape, bool),
        issued_zat=np.zeros(shape, np.int64),
        supply_cap_cents=und.copy(),
        supply_cents=np.zeros(shape, np.int64),
        collateral_zat=np.zeros(shape, np.int64),
        global_ratio_bps=und.copy(),
        pin1_triggered=np.zeros(shape, bool),
        pinned_pools=np.zeros(shape, np.uint64),
        a_mint=und.copy(),
        a_claim=und.copy(),
        armed=np.zeros(shape, bool),
        rng=rng,
    )

    def run_hooks(stage: str) -> None:
        for h in hooks:
            h(stage, params, inputs, s)

    run_hooks("judge")

    # ACT-1..3, ACT-4/6
    act = _wp5("activation") if activation_mode == "auto" else None
    if act is not None:
        a = act(params, inputs.signal_bit, inputs.start_height, inputs.start_height)
        status, count, part, enf = a.status, a.signal_count, a.participation_halt, a.enforcement_halt
        s.activation_series = a
        s.activation_source = "wp5"
    elif activation_mode == "always_active":
        status = np.full(shape, ACTIVE, dtype=np.int8)
        count = V.signal_counts(inputs.signal_bit, int(params["signalWindow"]))
        part = enf = np.zeros(shape, dtype=bool)
        s.activation_source = "always_active"
    else:
        status, count, part, enf = internal_activation(params, inputs.signal_bit)
        s.activation_source = "internal"
    s.activation_status = np.asarray(status, dtype=np.int8).reshape(shape)
    s.signal_count = np.asarray(count, dtype=np.int64).reshape(shape)
    s.participation_halt = np.asarray(part, dtype=bool).reshape(shape)
    s.enforcement_halt = np.asarray(enf, dtype=bool).reshape(shape)
    run_hooks("activation")

    # maturity, ARM-1/2, bundles (WP-5)
    att_fn = _wp5("attest") if attest_mode == "auto" and inputs.attest else None
    if att_fn is not None:
        _apply_attest(s, att_fn(params, inputs, s), shape)
    run_hooks("attest")

    # PIN-1
    _apply_pin1(params, inputs, s, shape)
    run_hooks("pin")

    # PRICE-1/2
    _apply_prices(params, inputs, s)
    # PIN-1/PIN-2 coupling (D-WP5-3 item 2): iterate oracle → attest → PIN-1 → medians to the fixed point
    if att_fn is not None:
        _pin_fixed_point(params, inputs, s, shape, att_fn)
    run_hooks("price")

    # SIGMA-1
    s.sigma_mult_bps = S.sigma_series(params, s.p_fast)
    run_hooks("sigma")

    # issuance, MINT-6 cap
    issued = _issued(params, inputs)
    s.issued_zat = np.broadcast_to(issued, shape)
    s.supply_cap_cents = SUP.supply_cap_cents(issued[None, :], s.x_mint, int(params["supplyCapBps"]))
    run_hooks("supply")

    # HALT-1..4, ACT-4/6
    s.global_ratio_bps = SUP.global_ratio_bps(s.collateral_zat, s.x_mint, s.supply_cents)
    s.halt_mask = halt_mask_series(
        params,
        s.activation_status,
        s.x_mint,
        s.p_fast,
        s.p_mid,
        s.p_slow,
        s.participation_halt,
        s.enforcement_halt,
        s.supply_cents,
        s.collateral_zat,
    )
    run_hooks("halts")

    run_hooks("vaults")
    if np.any(s.supply_cents) or np.any(s.collateral_zat):
        refresh_halt2(params, s)
    run_hooks("dormancy")
    return s


def _chunk_job(args):
    params, inputs, hooks, seed, am, tm = args
    rng = None if seed is None else np.random.default_rng(seed)
    return _simulate_chunk(params, inputs, hooks, rng, am, tm)


def _pool(workers: int) -> ProcessPoolExecutor:
    ctx = mp.get_context("fork") if "fork" in mp.get_all_start_methods() else None
    return ProcessPoolExecutor(max_workers=workers, mp_context=ctx)


def simulate_blocks(
    params: Mapping,
    inputs: BlockInputs,
    *,
    hooks: Sequence[Hook] = (),
    rng=None,
    workers: int = 1,
    chunk_paths: int | None = None,
    activation_mode: ActivationMode = "auto",
    attest_mode: AttestMode = "auto",
) -> BlockSeries:
    """Run block mode over every path of ``inputs`` (see the module docstring for the stages).

    ``activation_mode``: ``"auto"`` uses WP-5's ``activation.simulate`` when it exists, else the exact
    internal kernels (``"internal"``); ``"always_active"`` assumes ACTIVE from ``startHeight`` with
    no ACT-4/6 halts (price-only studies). ``attest_mode``: ``"auto"`` uses ``attest.simulate`` when it
    exists, else (or ``"unarmed"``) aMint/aClaim are undefined and nothing is armed.
    ``series.activation_source`` / ``attest_source`` record which ran.

    ``workers > 1`` splits the paths into chunks of ``chunk_paths`` (default ``ceil(P/workers)``)
    run in worker processes. ``rng`` reaches the hooks as ``series.rng``; with chunking each chunk
    gets ``default_rng(seed_i)`` from seeds drawn once from ``rng``, so results depend on
    ``chunk_paths`` but not on ``workers``.
    """
    P = inputs.n_paths
    if chunk_paths is None:
        chunk_paths = P if workers <= 1 else max(1, math.ceil(P / workers))
    starts = list(range(0, P, chunk_paths))
    if len(starts) == 1:
        return _simulate_chunk(params, inputs, hooks, rng, activation_mode, attest_mode)
    seeds = [None] * len(starts) if rng is None else rng.integers(0, 2**63 - 1, size=len(starts)).tolist()
    jobs = [
        (
            params,
            inputs.slice_paths(slice(a, a + chunk_paths)),
            tuple(hooks),
            sd,
            activation_mode,
            attest_mode,
        )
        for a, sd in zip(starts, seeds, strict=True)
    ]
    if workers <= 1:
        parts = [_chunk_job(j) for j in jobs]
    else:
        with _pool(workers) as ex:
            parts = list(ex.map(_chunk_job, jobs))
    return BlockSeries.concat(parts)


# ---------------------------------------------------------------------------------------------------
# Streaming runs (for path counts that do not fit in memory)


def _run_job(args):
    params, make_inputs, reducer, hooks, seed, idx, n, am, tm = args
    rng = np.random.default_rng([seed, idx])
    inputs = make_inputs(rng, n, idx)
    series = _simulate_chunk(params, inputs, hooks, rng, am, tm)
    return reducer(series, inputs)


def run_paths(
    params: Mapping,
    make_inputs: Callable[[np.random.Generator, int, int], BlockInputs],
    n_paths: int,
    *,
    reducer: Callable[[BlockSeries, BlockInputs], Any],
    hooks: Sequence[Hook] = (),
    seed: int = 0,
    chunk_paths: int = 16,
    workers: int = 1,
    activation_mode: ActivationMode = "auto",
    attest_mode: AttestMode = "auto",
) -> list:
    """Generate, simulate and reduce ``n_paths`` paths chunk by chunk, so 1,000 × 90-day runs never
    hold more than ``chunk_paths`` paths in memory per worker.

    ``make_inputs(rng, n, chunk_index) -> BlockInputs`` builds a chunk (true prices + oracle);
    ``reducer(series, inputs)`` returns something small and picklable (metrics). Chunk ``i`` uses
    ``default_rng([seed, i])`` for both, so results are identical for any ``workers``. With
    ``workers > 1`` both callables must be module-level (picklable). Returns the reducer outputs in
    chunk order.
    """
    sizes = [min(chunk_paths, n_paths - a) for a in range(0, n_paths, chunk_paths)]
    jobs = [
        (params, make_inputs, reducer, tuple(hooks), int(seed), i, m, activation_mode, attest_mode)
        for i, m in enumerate(sizes)
    ]
    if workers <= 1 or len(jobs) == 1:
        return [_run_job(j) for j in jobs]
    with _pool(workers) as ex:
        return list(ex.map(_run_job, jobs))


def paths_for_budget(
    budget,
    *,
    days: float | None = None,
    workers: int | None = None,
    fraction: float = 1.0,
    seconds_per_path_day: float = SECONDS_PER_PATH_DAY,
) -> int:
    """How many block-mode paths of ``days`` (default ``budget.block_horizon_days``) fit in
    ``fraction`` of ``budget.max_minutes`` on ``workers`` cores (default ``os.cpu_count()``), capped
    at ``budget.paths``. Uses the measured :data:`SECONDS_PER_PATH_DAY`."""
    d = float(days if days is not None else budget.block_horizon_days)
    w = max(1, workers or os.cpu_count() or 1)
    seconds = budget.max_minutes * 60.0 * fraction
    fit = int(seconds * w / max(seconds_per_path_day * d, 1e-9))
    return max(1, min(int(budget.paths), fit))


# ---------------------------------------------------------------------------------------------------
# Hour mode


@dataclass(frozen=True)
class WindowFit:
    """Hour-mode model of one PRICE-1 median: rolling lower median of the interpolated true path
    over ``span`` sub-steps, lagged by ``lag`` sub-steps, times ``exp(bias + noise_sd·z)``."""

    window_blocks: int
    span: int
    lag: int
    bias: float = 0.0
    noise_sd: float = 0.0
    no_price_prob: float = 0.0


@dataclass(frozen=True)
class OracleTransferKernel:
    """Maps an hourly true-price history to hourly oracle outputs (PLAN §3.3)."""

    fast: WindowFit
    mid: WindowFit
    slow: WindowFit
    substeps: int = 12
    meta: dict = field(default_factory=dict, compare=False, hash=False)

    @classmethod
    def ideal(cls, params: Mapping, substeps: int = 12) -> OracleTransferKernel:
        """No lag, no bias, no noise: the rolling median of the true path itself."""
        w = O.windows_and_fills(params)[0]
        fits = [WindowFit(x, max(1, round(x * substeps / BLOCKS_PER_HOUR)), 0) for x in w]
        return cls(*fits, substeps=substeps)

    def _fine(self, hourly) -> np.ndarray:
        """Log-linear interpolation to ``substeps`` points per hour; fine index ``h·S + S − 1`` is
        hour ``h``'s sample."""
        h = np.log(np.maximum(np.asarray(hourly, dtype=np.float64), 1.0))
        prev = np.concatenate([h[:, :1], h[:, :-1]], axis=1)
        frac = (np.arange(self.substeps) + 1) / self.substeps
        fine = prev[:, :, None] + (h - prev)[:, :, None] * frac[None, None, :]
        return fine.reshape(h.shape[0], -1)

    def medians(self, hourly_true, *, rng: np.random.Generator | None = None, noise: bool = True):
        """``(pFast, pMid, pSlow)`` hourly int64 arrays (UNDEF where the model says NO_PRICE)."""
        ht = _2d(hourly_true, np.int64)
        fine = self._fine(ht)
        S_ = self.substeps
        # integer log-price grid (1e-6 resolution) so the exact wavelet rolling median serves
        q = np.rint((fine - fine.min()) * 1e6).astype(np.int64) + 1
        rm = V.RollingMedian(q)
        n_h = ht.shape[1]
        ends = np.arange(n_h) * S_ + S_ - 1
        out = []
        for wf in (self.fast, self.mid, self.slow):
            med = rm.median(wf.span, 1)
            idx = np.maximum(ends - wf.lag, 0)
            lg = (med[:, idx] - 1) / 1e6 + fine.min()
            lg = lg + wf.bias
            if noise and rng is not None and wf.noise_sd > 0:
                lg = lg + wf.noise_sd * rng.standard_normal(lg.shape)
            p = np.clip(np.rint(np.exp(lg)), PRICE_MIN, PRICE_MAX).astype(np.int64)
            # warm-up: the window cannot fill before ~fill blocks have elapsed
            warm = (np.arange(n_h) + 1) * BLOCKS_PER_HOUR < math.ceil(wf.window_blocks / 2)
            p[:, warm] = UNDEF
            if noise and rng is not None and wf.no_price_prob > 0:
                p[rng.random(p.shape) < wf.no_price_prob] = UNDEF
            out.append(p)
        return tuple(out)


@dataclass
class HourSeries:
    """Hour-mode outputs, ``(paths, hours)``; hour ``h`` ends at block ``48h + 47``."""

    p_fast: np.ndarray
    p_mid: np.ndarray
    p_slow: np.ndarray
    p_mint: np.ndarray
    p_claim: np.ndarray
    sigma_mult_bps: np.ndarray
    halt_mask: np.ndarray  # NO_PRICE | DIVERGENCE only (the price-decided bits)

    @property
    def n_hours(self) -> int:
        return int(self.p_fast.shape[1])


def _hour_sigma(params: Mapping, p_fast_hourly: np.ndarray) -> np.ndarray:
    """SIGMA-1 on hourly pFast: exact when ``volStep`` is a whole number of hours (mainnet 48), since
    the samples ``H − k·volStep`` are then hour ends; otherwise the step is rounded to hours."""
    step_h = max(1, round(int(params["volStep"]) / BLOCKS_PER_HOUR))
    win_h = max(step_h, round(int(params["volWindow"]) / BLOCKS_PER_HOUR))
    return V.sigma_mult_series(
        p_fast_hourly,
        win_h,
        step_h,
        int(params["sigmaRefBps"]),
        int(params["volPeriodsPerYear"]),
        int(params["sigmaMultMaxBps"]),
    )


def simulate_hours(
    params: Mapping,
    hourly_true_price,
    kernel: OracleTransferKernel,
    *,
    rng: np.random.Generator | None = None,
    noise: bool = True,
) -> HourSeries:
    """Hour mode: the oracle transfer kernel applied to an hourly true-price history (array
    ``(paths, hours)`` or a PricePath-like object with ``resolution == "hour"``)."""
    ht, res = prices_from(hourly_true_price)
    if res == "block":
        ht = ht[:, BLOCKS_PER_HOUR - 1 :: BLOCKS_PER_HOUR] if hasattr(hourly_true_price, "prices") else ht
    pf, pm, ps = kernel.medians(ht, rng=rng, noise=noise)
    xm = V.price_mint(pf, pm, ps)
    xc = V.price_claim(pm, ps)
    mask = np.where(xm == UNDEF, HALT_NO_PRICE, 0).astype(np.uint16)
    mask |= np.where(V.halt3_divergence(pf, pm, ps, int(params["divergenceBps"])), HALT_DIVERGENCE, 0).astype(
        np.uint16
    )
    return HourSeries(pf, pm, ps, xm, xc, _hour_sigma(params, pf), mask)


def _hour_marks(a: np.ndarray) -> np.ndarray:
    return a[:, BLOCKS_PER_HOUR - 1 :: BLOCKS_PER_HOUR]


def calibrate_kernel(
    params: Mapping,
    scenarios_block_paths,
    *,
    oracle: O.OracleConfig | None = None,
    seed: int = 0,
    substeps: int = 12,
    max_lag_hours: float = 2.0,
) -> OracleTransferKernel:
    """Fit an :class:`OracleTransferKernel` from block-mode runs.

    ``scenarios_block_paths``: an array ``(paths, n_blocks)`` of block-resolution true prices, a
    sequence of them, or a mapping name → array (or :class:`BlockSeries` already simulated, whose
    ``true_price`` is used). For each window the lag (in sub-steps, 0 … ``max_lag_hours``) minimising
    the mean |log error| at hour marks is chosen; bias and noise are the mean and s.d. of the log
    residual, ``no_price_prob`` the post-warm-up undefined fraction."""
    if isinstance(scenarios_block_paths, Mapping):
        items = list(scenarios_block_paths.values())
    elif isinstance(scenarios_block_paths, np.ndarray | BlockSeries):
        items = [scenarios_block_paths]
    else:
        items = list(scenarios_block_paths)
    windows = O.windows_and_fills(params)[0]
    cfg = oracle or O.OracleConfig.honest()
    rng = np.random.default_rng(seed)
    hourly, blocks = [], [[], [], []]
    for it in items:
        if isinstance(it, BlockSeries):
            tp, meds = it.true_price, (it.p_fast, it.p_mid, it.p_slow)
        else:
            tp = _2d(it, np.int64)
            inp = O.generate_block_inputs(tp, cfg, rng=rng)
            ps = O.price_series(params, inp.tag_price, inp.tag_present)
            meds = (ps.p_fast, ps.p_mid, ps.p_slow)
        hourly.append(_hour_marks(tp))
        for i in range(3):
            blocks[i].append(_hour_marks(meds[i]))
    ht = np.concatenate(hourly, axis=0)
    base = OracleTransferKernel.ideal(params, substeps)
    max_lag = max(0, round(max_lag_hours * substeps))
    warm_h = math.ceil(max(windows) / BLOCKS_PER_HOUR) + 1
    fits = []
    for i, wf0 in enumerate((base.fast, base.mid, base.slow)):
        target = np.concatenate(blocks[i], axis=0)
        best = None
        for lag in range(max_lag + 1):
            wf = WindowFit(wf0.window_blocks, wf0.span, lag)
            k = OracleTransferKernel(wf, wf, wf, substeps=substeps)
            m = k.medians(ht, noise=False)[0]
            ok = (m > 0) & (target > 0)
            ok[:, :warm_h] = False
            if not ok.any():
                continue
            r = np.log(target[ok] / m[ok])
            score = float(np.mean(np.abs(r - r.mean())))
            if best is None or score < best[0]:
                best = (score, lag, float(r.mean()), float(r.std()))
        tgt = target[:, warm_h:]
        npp = float((tgt <= 0).mean()) if tgt.size else 0.0
        if best is None:
            fits.append(wf0)
        else:
            fits.append(WindowFit(wf0.window_blocks, wf0.span, best[1], best[2], best[3], npp))
    return OracleTransferKernel(
        *fits, substeps=substeps, meta={"scenarios": len(items), "paths": int(ht.shape[0]), "seed": seed}
    )


def kernel_error(
    kernel: OracleTransferKernel,
    block_series: BlockSeries,
    *,
    params: Mapping | None = None,
    true_price=None,
    tolerance_bps: float | None = None,
) -> dict:
    """Hour-mode (deterministic: bias, no noise) versus block mode at every hour mark after the slow
    warm-up. Returns, per series, the p50 / p95 / max absolute relative error in bps, plus the
    NO_PRICE disagreement rate and the σ multiplier's absolute error (bps of 1×). ``within_tolerance``
    says whether the pMint and pClaim p95 errors are at most ``tolerance_bps`` (callers pass
    ``Policy.hour_kernel_tolerance_bps``; default :data:`KERNEL_TOLERANCE_P95_BPS`)."""
    prm = params if params is not None else block_series.params
    tp = _2d(true_price if true_price is not None else block_series.true_price, np.int64)
    ht = _hour_marks(tp)
    hs = simulate_hours(prm, ht, kernel, noise=False)
    warm_h = math.ceil(int(prm["pSlowWindow"]) / BLOCKS_PER_HOUR) + 1
    out: dict = {}
    pairs = {
        "p_fast": (hs.p_fast, block_series.p_fast),
        "p_mid": (hs.p_mid, block_series.p_mid),
        "p_slow": (hs.p_slow, block_series.p_slow),
        "p_mint": (hs.p_mint, block_series.x_mint),
        "p_claim": (hs.p_claim, block_series.x_claim),
    }
    for name, (h, b) in pairs.items():
        bb = _hour_marks(b)[:, warm_h:]
        hh = h[:, warm_h:]
        ok = (bb > 0) & (hh > 0)
        e = np.abs(hh[ok] - bb[ok]) / bb[ok] * BPS if ok.any() else np.array([np.nan])
        out[name] = {
            "p50": float(np.median(e)),
            "p95": float(np.percentile(e, 95)),
            "max": float(e.max()),
            "n": int(ok.sum()),
        }
    bm = _hour_marks(block_series.x_mint)[:, warm_h:] == UNDEF
    out["no_price_disagreement"] = float((bm != (hs.p_mint[:, warm_h:] == UNDEF)).mean())
    sig_warm = warm_h + math.ceil(int(prm["volWindow"]) / BLOCKS_PER_HOUR)
    sb = _hour_marks(block_series.sigma_mult_bps)[:, sig_warm:]
    sh = hs.sigma_mult_bps[:, sig_warm:]
    d = np.abs(sb - sh).astype(np.float64)
    out["sigma_mult_bps"] = (
        {"p50": float(np.median(d)), "p95": float(np.percentile(d, 95)), "max": float(d.max())}
        if d.size
        else {"p50": np.nan, "p95": np.nan, "max": np.nan}
    )
    tol = KERNEL_TOLERANCE_P95_BPS if tolerance_bps is None else float(tolerance_bps)
    out["tolerance_bps"] = tol
    out["within_tolerance"] = bool(out["p_mint"]["p95"] <= tol and out["p_claim"]["p95"] <= tol)
    return out


# ---------------------------------------------------------------------------------------------------
# Devnet differential contract (WP-9, docs/decisions.md D-WP9-5)

_ACT_NAMES = {SIGNALING: "signaling", LOCKED_IN: "locked_in", ACTIVE: "active"}
_HALT_NAMES = ("NOT_ACTIVE", "NO_PRICE", "PARTICIPATION", "GLOBAL_RATIO", "DIVERGENCE", "ENFORCEMENT")


def devnet_inputs(
    params: Mapping,
    schedule,
    *,
    n_pools: int = 3,
    jitter_bps: int = 10,
    seed: int | None = None,
    first_height: int = 1,
) -> BlockInputs:
    """The tag stream WP-9's :func:`ybcal.devnet.runner.replay` produces for ``schedule``, block
    for block: the same miner order (``MinerPlan``, carried across steps), the same quotes
    (``step_quote`` with ``random.Random(seed)``, default ``schedule.seed``), signal-only tags from
    the pools while a step's price is 0 (``yed_setquote 0 0``; every pool runs
    ``-yellowbacksignal=1``), no tag from the dark miner. Block ``i`` of the schedule is height
    ``first_height + i`` (a fresh regtest chain); blocks below ``startHeight`` are dropped (the
    node ignores them)."""
    import random

    from ybcal.devnet.runner import MinerPlan, step_quote

    rng = random.Random(schedule.seed if seed is None else seed)
    has_dark = "dark_miner" in getattr(schedule, "needs", frozenset())
    last: list[int | None] = [None] * n_pools
    pools, prices, refs = [], [], []
    plan = MinerPlan(n_pools, has_dark)
    for step in schedule.steps:
        if step.price == 0:
            last = [None] * n_pools
        for m in plan.miners(step):
            q = 0
            if m >= 0 and step.price:
                q = step_quote(step, m, jitter_bps, rng, last[m])
                last[m] = q
            pools.append(m)
            prices.append(q)
            refs.append(step.price)
    start = int(params["startHeight"])
    skip = max(0, start - first_height)
    if first_height > start:
        raise ValueError(
            "the schedule starts above startHeight: the simulator needs every block from startHeight"
        )
    pool = np.array(pools[skip:], dtype=np.int16)
    present = pool >= 0
    return BlockInputs(
        true_price=np.array(refs[skip:], dtype=np.int64),
        tag_present=present,
        tag_price=np.array(prices[skip:], dtype=np.int64),
        tag_pool=pool,
        signal_bit=present,
        start_height=start,
        meta={"schedule": getattr(schedule, "name", "")},
    )


def series_records(series: BlockSeries, path: int = 0) -> list[dict]:
    """Per-block records in WP-9's scrape shape (``ybcal.devnet.scrape.HISTORY_FIELDS``): integers,
    ``None`` for undefined prices and ratio; ``blockHash`` is empty (the simulator has no chain)."""
    p = series.params
    st = series.activation_status[path]
    started = np.nonzero(st != SIGNALING)[0]
    lock_i = int(started[0]) if started.size else -1
    delay = int(p["activationDelay"])
    recs = []
    for j in range(series.n_blocks):
        h = series.start_height + j
        locked = lock_i >= 0 and j >= lock_i
        mask = int(series.halt_mask[path, j])

        def opt(a, j=j):
            v = int(a[path, j])
            return v if v > 0 else None

        tp = series.extras.get("tag_present")
        tq = series.extras.get("tag_price")
        recs.append(
            {
                "height": h,
                "blockHash": "",
                "tagged": int(bool(tp[path, j])) if tp is not None else 0,
                "quote": int(bool(tq[path, j] > 0)) if tq is not None else 0,
                "signalCount": int(series.signal_count[path, j]),
                "activationStatus": _ACT_NAMES[int(st[j])],
                "activationCode": int(st[j]),
                "lockInHeight": series.start_height + lock_i if locked else 0,
                "activateHeight": series.start_height + lock_i + delay if locked else 0,
                "pFast": opt(series.p_fast),
                "pMid": opt(series.p_mid),
                "pSlow": opt(series.p_slow),
                "pMint": opt(series.x_mint),
                "pClaim": opt(series.x_claim),
                "sigmaMultBps": int(series.sigma_mult_bps[path, j]),
                "issuedZat": int(series.issued_zat[path, j]),
                "supplyCents": int(series.supply_cents[path, j]),
                "collateralZat": int(series.collateral_zat[path, j]),
                "globalRatioBps": None
                if int(series.global_ratio_bps[path, j]) == UNDEF
                else int(series.global_ratio_bps[path, j]),
                "haltMask": mask,
                "haltNames": "|".join(n for b, n in enumerate(_HALT_NAMES) if mask >> b & 1),
            }
        )
    return recs


def simulate_devnet(
    params: Mapping,
    path,
    schedule,
    *,
    hooks: Sequence[Hook] = (),
    n_pools: int = 3,
    jitter_bps: int = 10,
    seed: int | None = None,
    first_height: int = 1,
    attest: dict | None = None,
) -> list[dict]:
    """WP-9's simulator contract (``ybcal.devnet.diff.resolve_simulator``): one record per block from
    ``params["startHeight"]`` for the replay of ``schedule`` (``path`` — the schedule's reference
    prices, ``schedule_prices(schedule)`` — is accepted for the signature; the quotes are rebuilt
    exactly from the schedule as :func:`devnet_inputs` documents). ``n_pools`` / ``jitter_bps`` /
    ``seed`` must match the devnet run (runner defaults: 3, 10, the run seed)."""
    inputs = devnet_inputs(
        params, schedule, n_pools=n_pools, jitter_bps=jitter_bps, seed=seed, first_height=first_height
    )
    if attest is not None:  # an attestation replay (WP-5 schema), e.g. ybcal.devnet.attestreplay
        inputs.attest = attest

    def _tags(stage, prm, inp, s):
        if stage == "judge":
            s.extras["tag_present"] = inp.tag_present
            s.extras["tag_price"] = inp.tag_price

    series = simulate_blocks(params, inputs, hooks=(_tags, *hooks))
    return series_records(series)


#: Default acceptance for :func:`kernel_error` (p95 relative error of pMint / pClaim, bps); mirrors
#: ``Policy.hour_kernel_tolerance_bps`` (D-WP3-5), which studies should prefer.
KERNEL_TOLERANCE_P95_BPS: float = 300.0

__all__ = [
    "ACTIVE",
    "BLOCKS_PER_DAY",
    "HALT_DIVERGENCE",
    "HALT_ENFORCEMENT",
    "HALT_GLOBAL_RATIO",
    "HALT_NOT_ACTIVE",
    "HALT_NO_PRICE",
    "HALT_PARTICIPATION",
    "KERNEL_TOLERANCE_P95_BPS",
    "LOCKED_IN",
    "PRICE_HALT_BITS",
    "SIGNALING",
    "STAGES",
    "BlockInputs",
    "BlockSeries",
    "HourSeries",
    "OracleTransferKernel",
    "WindowFit",
    "calibrate_kernel",
    "devnet_inputs",
    "halt_mask_series",
    "internal_activation",
    "kernel_error",
    "paths_for_budget",
    "pin1_keys",
    "pin1_trigger_series",
    "prices_from",
    "refresh_halt2",
    "run_paths",
    "series_records",
    "simulate_blocks",
    "simulate_devnet",
    "simulate_hours",
]

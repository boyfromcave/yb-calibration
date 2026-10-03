"""issuedZat, the MINT-6 supply cap and the HALT-2 global ratio (owner: WP-3; PLAN §1.5-2, §5.7).

``issuedZat`` (state.cpp:1224-1225 @ 7702d22) is **the block subsidy summed from ``startHeight``**,
not the YEC supply: ``Snapshots[H].issuedZat = Snapshots[H-1].issuedZat + GetBlockSubsidy(H)``
with the virtual snapshot below ``startHeight`` carrying 0 (view.h ``Snapshot()``; SERIALISATION.md
§2). The subsidy is ``Consensus::Params::GetBlockSubsidy`` (index.cpp:217, 351 pass it to
``EvaluateBlock``), so this module transcribes the Ycash subsidy schedule:

``src/consensus/params.cpp:125-158`` ``GetBlockSubsidy`` and ``:53-73`` ``Halving`` (ZIP 208)::

    nSubsidy = 12.5 * COIN
    h <  SlowStartShift            : nSubsidy / SlowStartInterval * h
    h <  SlowStartInterval         : nSubsidy / SlowStartInterval * (h + 1)
    halvings >= 64                 : 0
    Blossom active (h >= Blossom)  : (nSubsidy / BLOSSOM_RATIO) >> Halving(h)
    otherwise                      : nSubsidy >> Halving(h)
    Halving(h) = ((Blossom - SlowStartShift) * BLOSSOM_RATIO + (h - Blossom)) / PostBlossomInterval
                                                                       (Blossom active)
               = (h - SlowStartShift) / PreBlossomInterval             (otherwise)

with, at ycash6 ``7702d22``:

* ``src/consensus/params.h:167-178``: pre/post-Blossom spacing 150 s / 75 s, ``BLOSSOM_RATIO = 2``,
  ``PRE_BLOSSOM_HALVING_INTERVAL = 840,000``, ``PRE_BLOSSOM_REGTEST_HALVING_INTERVAL = 144``,
  ``POST_BLOSSOM_HALVING_INTERVAL(x) = 2x``; ``SubsidySlowStartShift() = interval / 2`` (params.h:228).
* ``src/chainparams.cpp:94-96, 134`` (mainnet ``CMainParams``): ``nSubsidySlowStartInterval = 20,000``,
  pre-Blossom halving 840,000, post-Blossom 1,680,000, **Blossom at 1,100,000**. The Ycash fork
  (``UPGRADE_YCASH`` at 570,000, chainparams.cpp:130) does not change the subsidy; the post-fork
  Ycash Development Fund is paid *out of* the subsidy (founders-reward outputs, main.cpp:5919-5936),
  so it does not change ``GetBlockSubsidy`` either. There are no ZIP 207 funding streams
  (chainparams.cpp:204).
* testnet (``CTestNetParams``, chainparams.cpp:389-391, 429): same intervals, Blossom at 661,610.
* regtest (``CRegTestParams``, chainparams.cpp:619-621): slow start 0, halving 144 / 288, Blossom
  ``NO_ACTIVATION_HEIGHT`` by default; the Yellowback functional tests activate every upgrade at
  height 1, which is what ``reference.regtest_subsidy`` (the vendored model) assumes — so the
  regtest schedule here uses Blossom = 1 and is tested equal to it.

Mainnet consequence: at the current ``startHeight`` 3,075,000 the halving index is
``(1,090,000·2 + 1,975,000) // 1,680,000 = 2`` and the subsidy is ``625,000,000 >> 2 =
156,250,000`` zat (1.5625 YEC) per block until the third post-Blossom halving at 3,960,000; over the
first sunset year (420,480 blocks) ``issuedZat`` grows linearly to 657,000 YEC.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from ybcal.model import vkernels as V
from ybcal.units import BLOCKS_PER_DAY, BPS, COIN

OWNER_WP = "WP-3"

UNDEF = V.UNDEF


# ---------------------------------------------------------------------------------------------------
# Subsidy schedule


@dataclass(frozen=True)
class SubsidySchedule:
    """The parameters of ``GetBlockSubsidy`` for one network (consensus/params.cpp:125)."""

    name: str
    slow_start_interval: int
    pre_blossom_halving: int
    blossom_height: int | None  # None = NO_ACTIVATION_HEIGHT
    spacing_ratio: int = 2  # BLOSSOM_POW_TARGET_SPACING_RATIO (params.h:172)
    max_subsidy: int = 125 * COIN // 10  # ``12.5 * COIN``

    @property
    def slow_start_shift(self) -> int:
        return self.slow_start_interval // 2

    @property
    def post_blossom_halving(self) -> int:
        return self.pre_blossom_halving * self.spacing_ratio

    def blossom_active(self, height: int) -> bool:
        return self.blossom_height is not None and height >= self.blossom_height

    def halving(self, height: int) -> int:
        """``Params::Halving`` (params.cpp:53-73); C++ integer division truncates toward zero."""
        if self.blossom_active(height):
            assert self.blossom_height is not None
            scaled = (self.blossom_height - self.slow_start_shift) * self.spacing_ratio + (
                height - self.blossom_height
            )
            return (
                int(scaled / self.post_blossom_halving) if scaled < 0 else scaled // self.post_blossom_halving
            )
        x = height - self.slow_start_shift
        return int(x / self.pre_blossom_halving) if x < 0 else x // self.pre_blossom_halving

    def halving_height(self, index: int) -> int:
        """``Params::HalvingHeight`` for a height past Blossom (params.cpp:83-121): the first height
        whose halving index is ``index``."""
        if self.blossom_height is None:
            return self.pre_blossom_halving * index + self.slow_start_shift
        return (
            self.post_blossom_halving * index
            - self.spacing_ratio * (self.blossom_height - self.slow_start_shift)
            + self.blossom_height
        )

    def subsidy(self, height: int) -> int:
        """``GetBlockSubsidy(height)`` in zat (params.cpp:125-158)."""
        n = self.max_subsidy
        if height < self.slow_start_shift:
            return (n // self.slow_start_interval) * height
        if height < self.slow_start_interval:
            return (n // self.slow_start_interval) * (height + 1)
        h = self.halving(height)
        if h >= 64:
            return 0
        if self.blossom_active(height):
            return (n // self.spacing_ratio) >> h
        return n >> h

    def subsidy_array(self, heights) -> np.ndarray:
        """``subsidy`` elementwise over an int array of heights (vectorised, exact)."""
        h = np.asarray(heights, dtype=np.int64)
        out = np.zeros(h.shape, dtype=np.int64)
        n = self.max_subsidy
        ss, si = self.slow_start_shift, self.slow_start_interval
        slow1 = h < ss
        slow2 = (~slow1) & (h < si)
        rest = ~(slow1 | slow2)
        if si > 0:
            out[slow1] = (n // si) * h[slow1]
            out[slow2] = (n // si) * (h[slow2] + 1)
        if rest.any():
            hr = h[rest]
            if self.blossom_height is not None:
                bl = hr >= self.blossom_height
            else:
                bl = np.zeros(hr.shape, dtype=bool)
            scaled = ((self.blossom_height or 0) - ss) * self.spacing_ratio + (
                hr - (self.blossom_height or 0)
            )
            halv = np.where(bl, scaled // self.post_blossom_halving, (hr - ss) // self.pre_blossom_halving)
            base = np.where(bl, n // self.spacing_ratio, n)
            halv = np.clip(halv, 0, 64)
            val = np.where(halv >= 64, 0, base >> np.minimum(halv, 63))
            out[rest] = val
        return out


#: Ycash mainnet (chainparams.cpp:94-96, 134 @ 7702d22).
MAINNET = SubsidySchedule(
    "main", slow_start_interval=20_000, pre_blossom_halving=840_000, blossom_height=1_100_000
)
#: Ycash testnet (chainparams.cpp:389-391, 429).
TESTNET = SubsidySchedule(
    "test", slow_start_interval=20_000, pre_blossom_halving=840_000, blossom_height=661_610
)
#: Regtest as the Yellowback functional tests run it (every upgrade at 1; reference.regtest_subsidy).
REGTEST = SubsidySchedule("regtest", slow_start_interval=0, pre_blossom_halving=144, blossom_height=1)


def schedule_for(params: Mapping) -> SubsidySchedule:
    """The schedule of a ParamSet's network (``candidate`` sets are mainnet scale → MAINNET)."""
    net = str(params.get("network", "main"))
    if net == "regtest":
        return REGTEST
    if net == "test":
        return TESTNET
    return MAINNET


def issued_zat_series(
    start_height: int, n: int, schedule: SubsidySchedule = MAINNET, issued_before_start: int = 0
) -> np.ndarray:
    """``Snapshots[start + j].issuedZat`` for ``j = 0 … n-1`` (state.cpp:1224): the cumulative
    subsidy over ``[startHeight, startHeight + j]`` (+ the virtual snapshot's carry, 0 on the node)."""
    heights = start_height + np.arange(n, dtype=np.int64)
    return np.cumsum(schedule.subsidy_array(heights)) + int(issued_before_start)


def issued_between(start_height: int, end_height: int, schedule: SubsidySchedule = MAINNET) -> int:
    """Σ subsidy over ``[start_height, end_height]`` in closed form (exact; for long horizons)."""
    if end_height < start_height:
        return 0
    total = 0
    h = start_height
    while h <= end_height:
        # constant-subsidy run: up to the next halving / slow-start / Blossom boundary
        if h < schedule.slow_start_interval:
            nxt = min(end_height, schedule.slow_start_interval - 1)
            total += sum(schedule.subsidy(x) for x in range(h, nxt + 1))
            h = nxt + 1
            continue
        bounds = [end_height + 1]
        if schedule.blossom_height is not None and h < schedule.blossom_height:
            bounds.append(schedule.blossom_height)
        idx = schedule.halving(h)
        if schedule.blossom_active(h) or schedule.blossom_height is None:
            bounds.append(schedule.halving_height(idx + 1))
        else:
            bounds.append(schedule.pre_blossom_halving * (idx + 1) + schedule.slow_start_shift)
        nxt = min(b for b in bounds if b > h)
        total += schedule.subsidy(h) * (nxt - h)
        h = nxt
    return total


# ---------------------------------------------------------------------------------------------------
# MINT-6 supply cap, HALT-2


def supply_cap_cents(issued_zat, x_mint, supply_cap_bps: int) -> np.ndarray:
    """MINT-6 cap series ``⌊⌊issued·xMint/COIN⌋/10^4⌋·capBps/10^4`` (math.h:139-148 via
    ``vkernels.supply_cap_cents``); ``UNDEF`` where the price is undefined or ``capBps <= 0``
    (regtest default: no cap)."""
    return V.supply_cap_cents(issued_zat, x_mint, int(supply_cap_bps))


def cap_admits(supply_cents, cents, cap_cents, *, min_ratio_bps=None, recap_ratio_bps: int | None = None):
    """MINT-6 (W20 soft cap) as a predicate: a mint of ``cents`` is admitted unless the cap is
    defined, ``supply + cents > cap``, and the mint's class minimum (``min_ratio_bps``) is below
    ``recapRatioBps`` (reference ``_mint_verdict``)."""
    s, c, cap = np.broadcast_arrays(
        np.asarray(supply_cents, dtype=np.int64),
        np.asarray(cents, dtype=np.int64),
        np.asarray(cap_cents, dtype=np.int64),
    )
    over = (cap != UNDEF) & (s + c > cap)
    if min_ratio_bps is not None and recap_ratio_bps is not None:
        over &= np.asarray(min_ratio_bps) < int(recap_ratio_bps)
    return ~over


def global_ratio_bps(collateral_zat, x_mint, supply_cents) -> np.ndarray:
    """``Snapshots[H].globalRatioBps`` (state.cpp:1229, math.h:162); ``UNDEF`` when undefined."""
    return V.global_ratio_bps(collateral_zat, x_mint, supply_cents)


def halt2_mask(collateral_zat, x_mint, supply_cents, global_ratio_halt_bps: int) -> np.ndarray:
    """HALT-2 (state.cpp:1237): ``pMint`` defined, supply > 0 and ratio < ``globalRatioHaltBps``."""
    return V.halt2_global_ratio(collateral_zat, x_mint, supply_cents, int(global_ratio_halt_bps))


# ---------------------------------------------------------------------------------------------------
# G7 helpers


def _price_blocks(price_path) -> tuple[np.ndarray, int]:
    """(2-D int64 prices, blocks per step) from an array (block resolution) or a PricePath-like
    object with ``.prices`` and ``.resolution`` (``"block"`` | ``"hour"``). With WP-2's
    ``ybcal.data.pricepath.PricePath`` this is already the adapter (duck-typed)."""
    if hasattr(price_path, "prices"):
        arr = np.asarray(price_path.prices, dtype=np.int64)
        step = 48 if getattr(price_path, "resolution", "block") == "hour" else 1
    else:
        arr, step = np.asarray(price_path, dtype=np.int64), 1
    if arr.ndim == 1:
        arr = arr[None, :]
    return arr, step


def days_until_cap_admits(
    cents: int,
    params: Mapping,
    price_path,
    *,
    start_height: int | None = None,
    existing_supply_cents: int = 0,
    schedule: SubsidySchedule | None = None,
    term_class: int | None = None,
    sigma_mult_bps: int = BPS,
) -> np.ndarray:
    """Days after ``startHeight`` until MINT-6 first admits a mint of ``cents`` (fact 1.5-2, G7).

    ``price_path`` is the pMint series starting at ``startHeight`` (block resolution, ``(n,)`` or
    ``(paths, n)``) or a PricePath-like object (hour resolution: each step is 48 blocks, the price
    is applied at the hour's last block). Returns float days per path, ``nan`` when the cap never
    admits within the horizon. With ``term_class`` given, a class whose ``min_ratio_bps`` at
    ``sigma_mult_bps`` reaches ``recapRatioBps`` bypasses the cap (W20) → 0 days.
    """
    prices, step = _price_blocks(price_path)
    sched = schedule or schedule_for(params)
    start = int(params["startHeight"] if start_height is None else start_height)
    if term_class is not None:
        mr = int(params[f"baseRatioBps[{term_class}]"]) * int(sigma_mult_bps) // BPS
        if mr >= int(params["recapRatioBps"]):
            return np.zeros(prices.shape[0])
    n = prices.shape[1]
    block_idx = np.arange(n, dtype=np.int64) * step + (step - 1)
    issued_all = issued_zat_series(start, int(block_idx[-1]) + 1, sched)
    issued = issued_all[block_idx]
    cap = supply_cap_cents(issued[None, :], prices, int(params["supplyCapBps"]))
    if int(params["supplyCapBps"]) <= 0:
        return np.zeros(prices.shape[0])
    ok = (cap != UNDEF) & (cap >= int(existing_supply_cents) + int(cents))
    first = np.where(ok.any(axis=1), ok.argmax(axis=1), -1)
    days = np.where(first >= 0, (block_idx[np.maximum(first, 0)] + 1) / BLOCKS_PER_DAY, np.nan)
    return days


def days_until_cap_admits_const(
    cents: int,
    price_microusd: int,
    params: Mapping,
    *,
    existing_supply_cents: int = 0,
    schedule: SubsidySchedule | None = None,
    max_days: int = 3650,
) -> float:
    """Closed-form companion of :func:`days_until_cap_admits` at a constant price: the first block
    ``j`` with ``supplyCap(issued(start..j)) ≥ supply + cents`` (bisection over exact issuance)."""
    sched = schedule or schedule_for(params)
    start, cap_bps = int(params["startHeight"]), int(params["supplyCapBps"])
    if cap_bps <= 0:
        return 0.0
    need = int(existing_supply_cents) + int(cents)

    def admits(j: int) -> bool:
        issued = issued_between(start, start + j, sched)
        cap = V.supply_cap_cents(np.array([issued]), np.array([price_microusd]), cap_bps)[0]
        return cap != UNDEF and cap >= need

    hi = max_days * BLOCKS_PER_DAY
    if not admits(hi):
        return float("nan")
    lo = 0
    if admits(0):
        return 1 / BLOCKS_PER_DAY
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if admits(mid):
            hi = mid
        else:
            lo = mid
    return (hi + 1) / BLOCKS_PER_DAY

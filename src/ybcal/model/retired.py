"""RETIRED v3 kernels — not rules of the current node model (owner: WP-1).

The vault upgrade (upgrade plan §15.10, U-21; ``SCHEMA_VERSION`` 6 onwards, 13 at the current pin)
removed two rule families the v2/v3 Yellowback carried, and the quantum plan (D-Q-23) replaced a third:

* **ACT-1..6** — the miner-signalled activation state machine (SIGNALING → LOCKED_IN → ACTIVE over
  ``signalWindow`` / ``activationThreshold`` / ``activationDelay``), the PARTICIPATION (ACT-4) and
  ENFORCEMENT (ACT-6) hysteresis halts and the W19 "freeze, then fix" start predicate (ACT-5). The
  module is now active from the ``UPGRADE_VAULT`` height and every node checks every rule: there is
  no signal to count and no enforcement share to lose. The haltMask bits stay reserved and are never set.
* **FEE-1** — the collateral fee ``max(feeMin, collateral · feeBps / 10^4)`` and the AFEE-1 share of it.
  Replaced by FEE-1′ (the debt's banded dollar fee at the vault's mint price,
  :func:`ybcal.model.kernels.protocol_fee_zat`) and IT-9′.

These functions are the exact mirrors as they stood at ycash6 ``7702d22`` (golden ``SCHEMA_VERSION``
4), copied unchanged from ``kernels`` / ``vkernels`` before the port to the upgrade model. **No
vendored reference checks them any more** — the current reference model has no such rules. They are
kept only because the parameter registry (``ybcal.params``, snapshot 7702d22) and the simulator and
studies built on it (``ybcal.sim.activation``, ``sim.engine``, ``sim.landscape``, ``sim.vaults``,
``sim.fees``, studies G3/G5/G6) still model the v3 parameter set. Their outputs describe the v3
design, not the upgrade line; see ``docs/decisions.md`` (D-MP-1) for which calibration outputs are
retired and which must be re-run once the registry is re-pinned.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import NamedTuple

import numpy as np

from ybcal.model import kernels as K
from ybcal.model import vkernels as V

OWNER_WP = "WP-1"

#: The commit these mirrors were last checked against (the v3 golden chain, schema 4).
RETIRED_AT = "7702d22606d1ec3edf0fca962573fba64dac3904"

#: Activation.status of v3 (view.h declaration order at 7702d22).
SIGNALING, LOCKED_IN, ACTIVE = 0, 1, 2
#: The haltMask bits ACT-4 / ACT-6 set (reserved since U-21).
HALT_PARTICIPATION, HALT_ENFORCEMENT = 1 << 2, 1 << 5

_I64 = np.int64


# ---------------------------------------------------------------------------------------------------
# FEE-1 (retired by D-Q-23)


def fee_zat(collateral_zat: int, fee_min: int, fee_bps: int) -> int:
    """v3 math.h:185-192 FeeZat (FEE-1) = max(feeMin, collateral · feeBps // 10^4); negative inputs
    count as 0; a product past int64 saturates at MAX_MONEY."""
    c, b = max(collateral_zat, 0), max(fee_bps, 0)
    f = c * b // K.BPS
    if not (0 <= f <= K.INT64_MAX):
        return max(fee_min, K.MAX_MONEY)
    return max(fee_min, f)


def fee_zat_v(collateral_zat, fee_min: int, fee_bps: int) -> np.ndarray:
    """FEE-1 (:func:`fee_zat`) elementwise (exact; ``fee_bps`` scalar)."""
    c = np.maximum(np.asarray(collateral_zat, dtype=_I64), 0)
    b = max(int(fee_bps), 0)
    q, r = np.divmod(c, K.BPS)
    if b and int(q.max(initial=0)) > (2**62) // b:
        return np.array([fee_zat(int(x), fee_min, fee_bps) for x in c.ravel()], dtype=_I64).reshape(c.shape)
    return np.maximum(int(fee_min), q * b + r * b // K.BPS)


# ---------------------------------------------------------------------------------------------------
# ACT-1..6 (retired by U-21)


def signal_count(signals: Sequence[bool], index: int, signal_window: int) -> int:
    """v3 state.cpp:959-967 SignalCount over a per-block signal series whose element 0 is
    ``startHeight``: the signal bits in ``(H − signalWindow, H]`` at or after the start."""
    return sum(1 for s in signals[max(index - signal_window + 1, 0):index + 1] if s)


class ActivationState(NamedTuple):
    status: int            #: SIGNALING / LOCKED_IN / ACTIVE
    lock_in_height: int
    activate_height: int


def activation_step(state: ActivationState, height: int, count: int, start_height: int,
                    signal_window: int, activation_threshold: int, activation_delay: int) -> ActivationState:
    """v3 state.cpp:1080-1089 ACT-1..3 at SNAP of ``height``: SIGNALING → LOCKED_IN once
    ``H ≥ start + signalWindow − 1`` and ``count ≥ threshold`` (activate = H + delay); LOCKED_IN →
    ACTIVE at ``H ≥ activateHeight`` (possibly in the same step when the delay is 0)."""
    status, lock_in, activate = state
    window_full = height >= start_height + signal_window - 1
    if status == SIGNALING and window_full and count >= max(0, activation_threshold):
        status, lock_in, activate = LOCKED_IN, height, height + activation_delay
    if status == LOCKED_IN and height >= activate:
        status = ACTIVE
    return ActivationState(status, lock_in, activate)


def participation_halt_step(prev_halted: bool, count: int, active: bool, activation_threshold: int,
                            participation_floor: int) -> bool:
    """v3 state.cpp:1242-1245 ACT-4 (→ MINT-4): held while ``count < activationThreshold``; set when
    ACTIVE and ``count < participationFloor``."""
    part = prev_halted and count < max(0, activation_threshold)
    return part or (active and count < max(0, participation_floor))


def enforcement_halt_step(prev_halted: bool, count: int, active: bool, enforcement_resume: int,
                          enforcement_floor: int) -> bool:
    """v3 state.cpp:1246-1249 ACT-6 (→ ACT-5, BLK-1): held while ``count < enforcementResume``; set
    when ACTIVE and ``count < enforcementFloor``."""
    enf = prev_halted and count < max(0, enforcement_resume)
    return enf or (active and count < max(0, enforcement_floor))


def param_set_start_admissible(start_height: int, previous_enforce_until: int, signal_window: int,
                               enforcement_halted_at: Callable[[int], bool]) -> bool:
    """v3 params.cpp:259-268 (ACT-5, L8 / W19 "freeze, then fix"): a new set may start at X iff
    X ≥ the previous sunset (> 0), or every height in [X − signalWindow, X − 1] (all ≥ 0) shows
    the ENFORCEMENT halt."""
    if previous_enforce_until > 0 and start_height >= previous_enforce_until:
        return True
    if signal_window <= 0:
        return False
    return all(h >= 0 and enforcement_halted_at(h) for h in range(start_height - signal_window, start_height))


def signal_counts(signals, signal_window: int) -> np.ndarray:
    """:func:`signal_count` at every block of a bool signal series ``(..., n)``."""
    s = np.asarray(signals, dtype=_I64)
    c = np.cumsum(s, axis=-1)
    out = c.copy()
    if signal_window < s.shape[-1]:
        out[..., signal_window:] -= c[..., :-signal_window]
    return out


def participation_halt_series(counts, active, activation_threshold: int, participation_floor: int,
                              initial: bool = False) -> np.ndarray:
    """ACT-4 over a series (:func:`participation_halt_step` iterated)."""
    c = np.asarray(counts, dtype=_I64)
    return V.hysteresis(np.asarray(active, dtype=bool) & (c < max(0, participation_floor)),
                        c < max(0, activation_threshold), initial)


def enforcement_halt_series(counts, active, enforcement_resume: int, enforcement_floor: int,
                            initial: bool = False) -> np.ndarray:
    """ACT-6 over a series (:func:`enforcement_halt_step` iterated)."""
    c = np.asarray(counts, dtype=_I64)
    return V.hysteresis(np.asarray(active, dtype=bool) & (c < max(0, enforcement_floor)),
                        c < max(0, enforcement_resume), initial)

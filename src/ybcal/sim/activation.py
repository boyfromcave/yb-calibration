"""ACT-1..7 over a per-block signal process, plus the G5 analytic helpers (owner: WP-5).

Block-mode component of the simulator (PLAN §3.3, §5.5). Everything is a pure function over numpy
arrays shaped ``(paths, n_blocks)``; column ``j`` is height ``height0 + j``.

Exact rules (ycash6 @ 7702d22):

* ACT-1 signal count (state.cpp:959 ``SignalCount``): the signal bits of ``(H − signalWindow, H]``
  at or after ``startHeight`` (the window is clipped at the start, never padded).
* ACT-2/3 (state.cpp:1080-1089): SIGNALING → LOCKED_IN at the first ``H ≥ start + signalWindow − 1``
  with ``count ≥ activationThreshold`` (``activateHeight = H + activationDelay``); LOCKED_IN → ACTIVE
  at ``H ≥ activateHeight`` (the same SNAP when the delay is 0). Status values are the node enum
  (``SIGNALING, LOCKED_IN, ACTIVE = 0, 1, 2``, view.h).
* ACT-4 PARTICIPATION halt (state.cpp:1242): held while ``count < activationThreshold``, set when ACTIVE
  and ``count < participationFloor`` (hysteresis).
* ACT-6 ENFORCEMENT halt (state.cpp:1246): held while ``count < enforcementResume``, set when ACTIVE and
  ``count < enforcementFloor``.
* ACT-5 ``EnforcementOn(H)`` (state.cpp:950): read from ``Snapshots[H − 1]`` — ACTIVE, ENFORCEMENT clear
  — and the sunset ``H ≤ enforceUntilHeight`` (when > 0). A virtual snapshot (H − 1 < start) is off.
* Sunset signalling (index.cpp:713-727 ``IsEnforcing``): a miner signals in block H only while its tip
  ``H − 1 < enforceUntilHeight``, i.e. ``H ≤ enforceUntilHeight`` (:func:`sunset_signal_mask`).
* Abandonment (index.cpp:732 ``IsAbandonedLocked``, L10/L12): ENFORCEMENT set at every snapshot of
  ``[tip − abandonBlocks + 1, tip]`` with the first one at or above the start (:func:`abandoned`).
* ACT-5 / W19 "freeze, then fix" (params.cpp:259): :func:`start_admissible` delegates to
  ``kernels.param_set_start_admissible``; :func:`earliest_fix_start` is its vectorised search.
* ACT-7 work valve (index.cpp:553-560): the analytic :func:`valve_trip_probability`.

The per-block state machine uses the ``vkernels.signal_counts`` window sum (in int32) and
``vkernels.participation_halt_series`` / ``enforcement_halt_series`` (WP-1, property tested equal to the
scalar kernels); lock-in is the first qualifying column (an ``argmax``), so the whole series costs a
few numpy passes per chunk of paths — no per-block Python loop.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from ybcal.model import kernels as K
from ybcal.model import reference as _ref
from ybcal.model import vkernels as V
from ybcal.units import BLOCKS_PER_HOUR, BLOCKS_PER_YEAR

OWNER_WP = "WP-5"

#: Activation.status (view.h order; equal to ``reference.SIGNALING/LOCKED_IN/ACTIVE``).
SIGNALING, LOCKED_IN, ACTIVE = K.SIGNALING, K.LOCKED_IN, K.ACTIVE
#: haltMask bits this component owns (view.h; ``reference.HALT_*``).
HALT_NOT_ACTIVE: int = _ref.HALT_NOT_ACTIVE
HALT_NO_PRICE: int = _ref.HALT_NO_PRICE
HALT_PARTICIPATION: int = _ref.HALT_PARTICIPATION
HALT_ENFORCEMENT: int = _ref.HALT_ENFORCEMENT


def _p(params: Mapping, key: str) -> int:
    return int(params[key])


def _as_paths(a, dtype) -> np.ndarray:
    arr = np.asarray(a, dtype=dtype)
    if arr.ndim == 1:
        arr = arr[np.newaxis, :]
    if arr.ndim != 2:
        raise ValueError("expected an array shaped (paths, n_blocks)")
    return arr


def effective_enforce_until(params: Mapping, start_height: int) -> int:
    """``enforceUntilHeight`` re-based onto ``start_height``: ``0`` (no sunset) stays 0; otherwise the
    sunset keeps its distance from the start (``start + enforceUntil − P.startHeight``), so a set whose
    heights are absolute and an engine running relative heights read the same rule."""
    until = _p(params, "enforceUntilHeight")
    if until <= 0:
        return 0
    return start_height + (until - _p(params, "startHeight"))


def sunset_signal_mask(n_blocks: int, height0: int, enforce_until: int) -> np.ndarray:
    """``True`` where a miner still sets the signal bit (index.cpp:727: tip ``H − 1 < enforceUntil``,
    i.e. ``H ≤ enforceUntil``); all ``True`` without a sunset (``enforce_until ≤ 0``). The engine ANDs
    its signal draws with this (fact 1.5-4: minting halts about one window after the sunset)."""
    h = height0 + np.arange(n_blocks)
    if enforce_until <= 0:
        return np.ones(n_blocks, dtype=bool)
    return h <= enforce_until


@dataclass(frozen=True)
class ActivationInit:
    """State carried into column 0 when ``height0 > start_height`` (the series starts mid-chain).

    ``prior_signals`` are the signal bits of the ``signalWindow − 1`` heights before ``height0``
    (shape ``(paths, w)`` or ``(w,)``, oldest first; heights below the start are ignored); the other
    fields are ``Snapshots[height0 − 1]``.
    """

    status: int = SIGNALING
    lock_in_height: int = 0
    activate_height: int = 0
    participation_halt: bool = False
    enforcement_halt: bool = False
    prior_signals: np.ndarray | None = None


@dataclass
class ActivationSeries:
    """ACT-1..6 per path and block. Column ``j`` is height ``height0 + j``; columns below
    ``start_height`` are the virtual snapshot (SIGNALING, count 0, no ACT halts, enforcement off)."""

    height0: int
    start_height: int
    enforce_until: int          #: effective sunset height (0 = none)
    status: np.ndarray          #: int8 (paths, n): SIGNALING / LOCKED_IN / ACTIVE
    signal_count: np.ndarray    #: int32 (paths, n)
    participation_halt: np.ndarray  #: bool (paths, n) — ACT-4 (MINT-4)
    enforcement_halt: np.ndarray    #: bool (paths, n) — ACT-6
    enforcement_on: np.ndarray      #: bool (paths, n) — ACT-5 at H (reads H − 1 and the sunset)
    lock_in_height: np.ndarray      #: int64 (paths,), −1 = never
    activate_height: np.ndarray     #: int64 (paths,), −1 = never

    @property
    def n_paths(self) -> int:
        return int(self.status.shape[0])

    @property
    def n_blocks(self) -> int:
        return int(self.status.shape[1])

    @property
    def heights(self) -> np.ndarray:
        return self.height0 + np.arange(self.n_blocks)

    @property
    def active(self) -> np.ndarray:
        return self.status == ACTIVE

    @property
    def halt_bits(self) -> np.ndarray:
        """The haltMask bits ACT owns (uint8): HALT-4 NOT_ACTIVE, ACT-4 PARTICIPATION, ACT-6
        ENFORCEMENT. The engine ORs in HALT-1/2/3 (virtual columns also carry NO_PRICE there)."""
        m = np.where(self.status != ACTIVE, HALT_NOT_ACTIVE, 0).astype(np.uint8)
        m |= np.where(self.participation_halt, HALT_PARTICIPATION, 0).astype(np.uint8)
        m |= np.where(self.enforcement_halt, HALT_ENFORCEMENT, 0).astype(np.uint8)
        return m

    def abandoned(self, abandon_blocks: int) -> np.ndarray:
        """:func:`abandoned` over this series' ENFORCEMENT halt."""
        return abandoned(self.enforcement_halt, abandon_blocks)


def simulate(params: Mapping, signal_bit, start_height: int, height0: int = 0, *,
             enforce_until: int | None = None, initial: ActivationInit | None = None,
             chunk_paths: int = 64) -> ActivationSeries:
    """ACT-1..6 for every path and block.

    ``signal_bit``: bool ``(paths, n)`` (or ``(n,)``) — the signal flag of the block at each height
    (a block without a tag has no signal). ``start_height``: the parameter set's ``startHeight`` in
    the series' height frame. ``enforce_until``: absolute sunset height (default
    :func:`effective_enforce_until`). ``initial``: needed iff ``height0 > start_height``.
    Paths are processed ``chunk_paths`` at a time (bounded memory).
    """
    sig = _as_paths(signal_bit, bool)
    paths, n = sig.shape
    W = _p(params, "signalWindow")
    thr = max(0, _p(params, "activationThreshold"))
    delay = _p(params, "activationDelay")
    pfloor = max(0, _p(params, "participationFloor"))
    efloor = max(0, _p(params, "enforcementFloor"))
    eresume = max(0, _p(params, "enforcementResume"))
    until = effective_enforce_until(params, start_height) if enforce_until is None else int(enforce_until)
    heights = height0 + np.arange(n, dtype=np.int64)
    real = heights >= start_height                         # non-virtual snapshots

    if height0 > start_height:
        if initial is None:
            raise ValueError("height0 > start_height needs initial= (the snapshot at height0 - 1)")
    else:
        initial = ActivationInit()

    status = np.empty((paths, n), dtype=np.int8)
    counts = np.empty((paths, n), dtype=np.int32)
    part = np.empty((paths, n), dtype=bool)
    enf = np.empty((paths, n), dtype=bool)
    on = np.empty((paths, n), dtype=bool)
    lock = np.empty(paths, dtype=np.int64)
    act_h = np.empty(paths, dtype=np.int64)
    prior_all = None
    if initial.prior_signals is not None:
        prior_all = _as_paths(initial.prior_signals, bool)
        if prior_all.shape[0] == 1 and paths > 1:
            prior_all = np.broadcast_to(prior_all, (paths, prior_all.shape[1]))
    for a in range(0, paths, chunk_paths):
        b = min(paths, a + chunk_paths)
        out = _simulate_chunk(sig[a:b], None if prior_all is None else prior_all[a:b], heights, real,
                              start_height, height0, W, thr, delay, pfloor, efloor, eresume, until, initial)
        status[a:b], counts[a:b], part[a:b], enf[a:b], on[a:b], lock[a:b], act_h[a:b] = out
    return ActivationSeries(height0=height0, start_height=start_height, enforce_until=until, status=status,
                            signal_count=counts, participation_halt=part, enforcement_halt=enf,
                            enforcement_on=on, lock_in_height=lock, activate_height=act_h)


def _simulate_chunk(sig, prior_signals, heights, real, start_height, height0, W, thr, delay, pfloor, efloor,
                    eresume, until, initial):
    paths, n = sig.shape
    # ACT-1: counts over the clipped window. Prepend the carried prior bits (heights < height0).
    sig = sig & real[np.newaxis, :]
    pre = W - 1 if W > 1 else 0
    prior = np.zeros((paths, pre), dtype=bool)
    if prior_signals is not None and pre > 0:
        ps = prior_signals[:, -pre:]
        prior[:, pre - ps.shape[1]:] = ps
        prior &= (height0 - pre + np.arange(pre) >= start_height)[np.newaxis, :]
    full = np.concatenate([prior, sig], axis=1)
    if W > 0:                                   # == vkernels.signal_counts, in int32 (count ≤ W)
        c = np.cumsum(full, axis=1, dtype=np.int32)
        if c.shape[1] > W:
            c[:, W:] -= c[:, :-W].copy()
        counts = c[:, pre:]
    else:
        counts = np.zeros((paths, n), np.int32)

    # ACT-2/3
    lock = np.full(paths, -1, dtype=np.int64)
    act_h = np.full(paths, -1, dtype=np.int64)
    if initial.status != SIGNALING:
        lock[:] = initial.lock_in_height
        act_h[:] = initial.activate_height
    else:
        cand = real & (heights >= start_height + W - 1)
        q = (counts >= thr) & cand[np.newaxis, :]
        anyq = q.any(axis=1)
        first = np.argmax(q, axis=1)
        lock = np.where(anyq, heights[first], -1)
        act_h = np.where(anyq, lock + delay, -1)
    locked = (lock[:, None] >= 0) & (heights[None, :] >= lock[:, None])
    active = locked & (heights[None, :] >= act_h[:, None])
    status = np.where(active, ACTIVE, np.where(locked, LOCKED_IN, SIGNALING)).astype(np.int8)

    # ACT-4 / ACT-6 (vkernels hysteresis = the scalar step iterated)
    part = V.participation_halt_series(counts, active, thr, pfloor, initial=initial.participation_halt)
    enf = V.enforcement_halt_series(counts, active, eresume, efloor, initial=initial.enforcement_halt)
    part &= real[np.newaxis, :]
    enf &= real[np.newaxis, :]

    # ACT-5 at H from the snapshot H − 1
    prev_on = active & ~enf & real[np.newaxis, :]
    on = np.zeros((paths, n), dtype=bool)
    on[:, 1:] = prev_on[:, :-1]
    if height0 - 1 >= start_height:
        on[:, 0] = initial.status == ACTIVE and not initial.enforcement_halt
    if until > 0:
        on &= (heights <= until)[np.newaxis, :]
    return status, counts, part, enf, on, lock, act_h


# ---------------------------------------------------------------------------------------------------
# Abandonment and W19


def _run_lengths(mask) -> np.ndarray:
    """Length of the run of ``True`` ending at each column (0 where False), along the last axis."""
    m = np.asarray(mask, dtype=bool)
    n = m.shape[-1]
    idx = np.arange(n)
    last_false = np.maximum.accumulate(np.where(~m, idx, -1), axis=-1)
    return np.where(m, idx - last_false, 0)


def abandoned(enforcement_halt, abandon_blocks: int) -> np.ndarray:
    """L10/L12 abandonment at each tip column: ENFORCEMENT set at every snapshot of
    ``[tip − abandonBlocks + 1, tip]`` (index.cpp:732). Column 0 is taken as the first snapshot at or
    after the start (a virtual snapshot never has ENFORCEMENT, so an earlier start changes nothing).
    ``abandon_blocks ≤ 0`` follows the C++ loop: an empty range is ``True`` at every tip at/after start."""
    e = np.asarray(enforcement_halt, dtype=bool)
    if abandon_blocks <= 0:
        return np.ones_like(e)
    return _run_lengths(e) >= abandon_blocks


def start_admissible(enforcement_halt, start: int, signal_window: int, *, height0: int = 0,
                     previous_enforce_until: int = 0) -> np.ndarray:
    """ACT-5 / W19 per path: may a new parameter set start at height ``start``? Delegates to
    ``kernels.param_set_start_admissible`` (params.cpp:259) with the series as the halt oracle;
    heights outside the series count as not halted. Returns bool ``(paths,)``."""
    e = _as_paths(enforcement_halt, bool)
    n = e.shape[1]
    out = np.zeros(e.shape[0], dtype=bool)
    for i in range(e.shape[0]):
        row = e[i]

        def halted(h: int, row=row) -> bool:
            j = h - height0
            return 0 <= j < n and bool(row[j])

        out[i] = K.param_set_start_admissible(start, previous_enforce_until, signal_window, halted)
    return out


def earliest_fix_start(enforcement_halt, signal_window: int, *, height0: int = 0) -> np.ndarray:
    """The smallest ``X`` per path such that every height of ``[X − signalWindow, X − 1]`` (all ≥ 0)
    shows ENFORCEMENT within the series — the earliest W19 "freeze, then fix" start without the
    sunset clause; ``−1`` if none. Vectorised (run lengths); equal to scanning
    :func:`start_admissible` (tested)."""
    e = _as_paths(enforcement_halt, bool)
    if signal_window <= 0:
        return np.full(e.shape[0], -1, dtype=np.int64)
    heights = height0 + np.arange(e.shape[1])
    ok = (_run_lengths(e) >= signal_window) & ((heights - signal_window + 1) >= 0)[None, :]
    anyok = ok.any(axis=1)
    return np.where(anyok, heights[np.argmax(ok, axis=1)] + 1, -1).astype(np.int64)


# ---------------------------------------------------------------------------------------------------
# G5 analytic helpers (PLAN §5.5): exact binomial / Poisson-binomial tails, crossing rates, bounds


def p_count_below(floor: int, p, window: int):
    """``P(count < floor)`` for a window of ``window`` blocks each signalling independently with
    probability ``p`` (the enforcing hash share): the exact binomial CDF at ``floor − 1``."""
    from scipy.stats import binom

    return binom.cdf(floor - 1, window, np.asarray(p, dtype=float))


def poisson_binomial_pmf(probs: Sequence[float]) -> np.ndarray:
    """Exact pmf of a sum of independent Bernoulli(``probs[i]``) (DP convolution, O(n²)); index k =
    P(sum = k). Used when the share drifts inside the window."""
    pmf = np.zeros(len(probs) + 1)
    pmf[0] = 1.0
    for i, q in enumerate(probs):
        q = float(q)
        pmf[1:i + 2] = pmf[1:i + 2] * (1.0 - q) + pmf[0:i + 1] * q
        pmf[0] *= 1.0 - q
    return pmf


def p_count_below_poisson_binomial(floor: int, probs: Sequence[float]) -> float:
    """``P(count < floor)`` with per-block signal probabilities ``probs`` (one per block of the
    window): exact Poisson-binomial tail."""
    pmf = poisson_binomial_pmf(probs)
    return float(pmf[:max(0, floor)].sum())


def downcrossing_rate(floor: int, p: float, window: int) -> float:
    """Exact expected number of blocks per block at which the count falls below ``floor``
    (``count_{H−1} ≥ floor > count_H``) for iid Bernoulli(p) signals: with ``Y`` the count of the
    ``window − 1`` shared blocks, the event is ``Y = floor − 1``, the leaving block signalled and the
    entering one did not, so the rate is ``P(Y = floor − 1)·p·(1 − p)``."""
    from scipy.stats import binom

    if window <= 0 or floor <= 0:
        return 0.0
    return float(binom.pmf(floor - 1, window - 1, p) * p * (1.0 - p))


@dataclass(frozen=True)
class FalseHaltEstimate:
    """False-halt statistics at a constant enforcing share (per year of ``blocks_per_year``)."""

    episodes_per_year_upper: float   #: expected downcrossings of the floor (≥ expected halt episodes)
    p_any_per_year: float            #: 1 − exp(−episodes) (Poisson-clumping approximation, upper)
    halted_fraction_lower: float     #: P(count < floor) — halted ⇒ ... ≥ this
    halted_fraction_upper: float     #: P(count < resume) — halted ⇒ count < resume
    hours_per_year_lower: float
    hours_per_year_upper: float
    simulated: dict | None = None    #: from :func:`simulate_halts` when requested


def false_halt_rate(p: float, window: int, floor: int, resume: int, *,
                    blocks_per_year: int = BLOCKS_PER_YEAR, simulate_blocks: int = 0, n_paths: int = 0,
                    rng: np.random.Generator | None = None) -> FalseHaltEstimate:
    """False halts of a hysteresis band (floor, resume) at true share ``p`` with overlapping windows.

    Analytic: every halt episode starts at a downcrossing of ``floor`` while not halted, so the exact
    downcrossing rate (:func:`downcrossing_rate`) bounds the episode rate from above; it is tight
    when ``resume`` is close to ``floor`` or halts are rare (no second crossing inside an episode).
    The halted fraction is bracketed by ``P(count < floor)`` and ``P(count < resume)``.
    ``simulate_blocks``/``n_paths`` > 0 adds a Monte-Carlo estimate (:func:`simulate_halts`).
    """
    rate = downcrossing_rate(floor, p, window) * blocks_per_year
    lo = float(p_count_below(floor, p, window))
    hi = float(p_count_below(resume, p, window))
    hours = blocks_per_year / BLOCKS_PER_HOUR
    sim = None
    if simulate_blocks > 0 and n_paths > 0:
        sim = simulate_halts(p, window, floor, resume, simulate_blocks, n_paths, rng=rng,
                             blocks_per_year=blocks_per_year)
    return FalseHaltEstimate(rate, 1.0 - math.exp(-rate), lo, hi, lo * hours, hi * hours, sim)


def simulate_halts(p, window: int, floor: int, resume: int, n_blocks: int, n_paths: int, *,
                   rng: np.random.Generator | None = None, blocks_per_year: int = BLOCKS_PER_YEAR,
                   chunk_paths: int = 64) -> dict:
    """Monte Carlo of the hysteresis halt (ACT-4/ACT-6 shape) on iid Bernoulli(p) signals with the
    window pre-filled (stationary start, ACTIVE, not halted). ``p`` may be a scalar or a per-block
    array ``(n_blocks,)`` (share drift). Returns episodes/year, halted fraction, hours/year and the
    clears (flaps) per year."""
    rng = rng if rng is not None else np.random.default_rng(0)
    pb = np.broadcast_to(np.asarray(p, dtype=float), (n_blocks,))
    episodes = clears = halted = 0
    done = 0
    while done < n_paths:
        m = min(chunk_paths, n_paths - done)
        warm = rng.random((m, window)) < pb[0]
        draws = rng.random((m, n_blocks)) < pb[None, :]
        s = np.concatenate([warm, draws], axis=1)
        c = V.signal_counts(s, window)[:, window:]
        h = V.hysteresis(c < floor, c < resume, False)
        starts = h[:, 1:] & ~h[:, :-1]
        ends = ~h[:, 1:] & h[:, :-1]
        episodes += int(starts.sum() + h[:, 0].sum())
        clears += int(ends.sum())
        halted += int(h.sum())
        done += m
    years = n_paths * n_blocks / blocks_per_year
    frac = halted / (n_paths * n_blocks)
    return {"episodes_per_year": episodes / years, "clears_per_year": clears / years,
            "halted_fraction": frac, "hours_per_year": frac * blocks_per_year / BLOCKS_PER_HOUR,
            "path_years": years}


def flapping_rate(p: float, window: int, floor: int, resume: int, **kw) -> dict:
    """Halt/clear cycles per year at share ``p``: the analytic upper bound (downcrossings) and,
    with ``simulate_blocks``/``n_paths``, the simulated clears per year."""
    est = false_halt_rate(p, window, floor, resume, **kw)
    out = {"cycles_per_year_upper": est.episodes_per_year_upper}
    if est.simulated is not None:
        out["cycles_per_year_simulated"] = est.simulated["clears_per_year"]
    return out


def detection_delay_approx(p: float, p_drop: float, window: int, floor: int) -> int | None:
    """Fluid approximation of the blocks from a drop ``p → p_drop`` (window full at ``p``) until the
    expected count ``W·p − t·(p − p_drop)`` falls below ``floor``; ``None`` if it never does."""
    if window * p_drop >= floor:
        return None
    if window * p < floor:
        return 0
    t = (window * p - floor) / (p - p_drop)
    return max(0, math.floor(t) + 1)


def detection_delay(p: float, p_drop: float, window: int, floor: int, *, n_paths: int = 2000,
                    max_blocks: int | None = None, rng: np.random.Generator | None = None) -> np.ndarray:
    """Monte-Carlo distribution of the detection delay: blocks after a drop ``p → p_drop`` (the window
    full of draws at ``p``, ACTIVE) until ``count < floor`` first holds (1 = the first block after the
    drop). ``−1`` where it is not detected within ``max_blocks`` (default 4·window)."""
    rng = rng if rng is not None else np.random.default_rng(0)
    max_blocks = max_blocks or 4 * window
    warm = rng.random((n_paths, window)) < p
    after = rng.random((n_paths, max_blocks)) < p_drop
    c = V.signal_counts(np.concatenate([warm, after], axis=1), window)[:, window:]
    hit = c < floor
    anyh = hit.any(axis=1)
    return np.where(anyh, np.argmax(hit, axis=1) + 1, -1)


def p_lock_in_first_window(p: float, window: int, threshold: int) -> float:
    """P(lock-in at the first eligible height ``start + W − 1``) = P(Bin(W, p) ≥ threshold)."""
    return float(1.0 - p_count_below(threshold, p, window))


def time_to_activation(params: Mapping, p, n_blocks: int, n_paths: int, *,
                       rng: np.random.Generator | None = None) -> np.ndarray:
    """Monte Carlo of ``activateHeight − startHeight`` (blocks) under iid signals at share ``p``
    (scalar or per-block array) through :func:`simulate`; ``−1`` where not activated in ``n_blocks``."""
    rng = rng if rng is not None else np.random.default_rng(0)
    pb = np.broadcast_to(np.asarray(p, dtype=float), (n_blocks,))
    sig = rng.random((n_paths, n_blocks)) < pb[None, :]
    s = simulate(params, sig, start_height=0, height0=0, enforce_until=0)
    return np.where(s.activate_height >= 0, s.activate_height, -1)


def valve_trip_probability(q_nonenforcing: float, valve_blocks: int) -> float:
    """ACT-7: probability that miners not enforcing (hash share ``q``) who build on a block this node
    rejected ever carry that branch ``valveBlocks`` of work above the enforcing tip (index.cpp:560),
    starting one block ahead: the gambler's-ruin ``(q / (1 − q))^(valveBlocks − 1)`` for ``q < ½``
    (1 otherwise). Assumes constant difficulty and that they never give up (an upper bound)."""
    if q_nonenforcing >= 0.5:
        return 1.0
    if q_nonenforcing <= 0:
        return 0.0
    return float((q_nonenforcing / (1.0 - q_nonenforcing)) ** max(0, valve_blocks - 1))


def natural_fork_trip_rate(orphan_rate: float, valve_blocks: int, *,
                           blocks_per_year: int = BLOCKS_PER_YEAR) -> float:
    """Expected per-year count of natural forks whose losing branch reaches ``valveBlocks`` blocks
    (each extra block of a competing branch taken as another orphan event, ``orphan_rate`` per
    block) — the false-trip exposure if such a branch carried a block this node rejects. A coarse
    upper-tail estimate: ``N · orphan_rate^valveBlocks``."""
    return float(blocks_per_year * orphan_rate ** max(1, valve_blocks))

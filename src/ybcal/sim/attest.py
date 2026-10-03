"""Attestor set, uptime, bias, seating, selection, bundles, PIN-1/2 inputs, dormancy (owner: WP-5).

Block-mode component of the simulator (PLAN §3.3, §5.8). :func:`simulate` walks each path through the
v3 SNAP rules of ycash6 @ 7702d22 exactly; the analytic G8 helpers live at the bottom.

Exact rules (``state.cpp`` unless noted)
----------------------------------------
* REG-A1 (666-713): a registration with ``bond ≥ bondMin`` at ``H ≥ startHeight`` creates seq
  ``AttestorSeq.next`` (registration order) as PENDING.
* Maturity (1105-1113): PENDING → ELIGIBLE at SNAP ``H ≥ registerHeight + bondMaturity``.
* ARM-1/2 (1114-1123): UNARMED → TRIGGERED at the first SNAP with ``attestArmMin > 0`` and
  ``eligibleCount ≥ attestArmMin`` (``armHeight = H + attestArmDelay``); TRIGGERED → ARMED at
  ``H ≥ armHeight``. Never backwards. "ARMED" as rules read it also needs ``attestRequired`` (W15).
* Bond weight (1373-1384, math.h:202): ``bond · clamp(H − ageOrigin, 0, ageCap)``; ``ageOrigin =
  triggerHeight`` for a registrant with ``registerHeight ≤ triggerHeight + foundingWindow`` once the
  layer is not UNARMED, else ``registerHeight``.
* Seating (1176-1194): the ``nSlots`` ELIGIBLE seqs of greatest weight at H, ties by seq;
  ``seatedSince`` follows membership (set to H when entering with 0, cleared when leaving).
* PIN-2 (1150-1175): at H, if pMint(H − 1) and pMint(H − 1 − pinWindow) are defined and differ by more
  than ``pinDeltaBps``, the seqs that signed one price at ≥ pinMinTags distinct cited heights across
  ≥ pinMinTags BundleLog rows of ``(H − 1 − pinWindow, H − 1]`` are pinned (``kernels.pin2_pinned_seqs``).
* Selection (1440-1455, W9): ``Snapshots[R].seated − pinnedSeqs`` weighted at R, ``mSelect + kSlack``
  draws seeded by ``SHA256(blockHash(R) ‖ selector ‖ "S" ‖ i)`` (``kernels.select_attestors``).
* BuildBundle (index.cpp:1174-1214): every selected seq with a fresh attestation (newest cited height in
  ``(R − attestMaxAge, R]``, ≥ start), in seq order; BUNDLE-1 (bundle.cpp:133) needs ``mSelect ≤ |C| ≤
  bundleMax``. Statistic: ``kernels.bundle_stat`` (qLow → aMint, qHigh → aClaim) with weights at R.
* BundleLog[H] (R12, 185-200): lower medians of the block's verified aMint/aClaim, union of the
  selected sets, union of (seq, price, citedHeight).
* Dormancy (1252-1275): at ``H % dormancyCheck == 0``, an ELIGIBLE seq seated since ``≤ H −
  dormancyBlocks`` that was selected in ≥ max(1, dormancyMinBundles) rows of ``(H − dormancyBlocks, H]``
  (incl. H) and signed none of them becomes DORMANT (after seating at H).
* REV-1 (757-779): a DORMANT seq with a signed price cited in ``(H − attestMaxAge, H − 1]`` → ELIGIBLE.
* EQV-1 (731-755): ejects any seq not WITHDRAWN/EJECTED. IN-2: a bond spend → WITHDRAWN unless EJECTED.
* Pool admission (index.cpp:1132): an EJECTED/WITHDRAWN seq's attestations are refused, so an
  attestation cited at ``c`` is usable iff ``c`` is before the seq's ejection/withdrawal height.

Order inside one height (EvaluateBlock): transactions (registration, EQV-1, bond spend, REV-1, the
demands' bundles, read from snapshots < H), BundleLog[H], then SNAP (maturity, ARM, PIN-2, seating,
dormancy). Every such rule is checked against the reference model (``tests/sim/test_attest*.py``).

The walk is event-driven: seating is computed with numpy for a whole segment of constant statuses
(and in O(1) when every ELIGIBLE seq is seated), and a per-path Python loop visits only demand
heights, PIN-2 trigger heights and dormancy-check heights. A dormancy that fires ends the segment.

Attest input schema (``inputs["attest"]`` / ``inputs.attest``)
--------------------------------------------------------------
``roster`` (required)
    list of attestor dicts shared by all paths, or ``callable(path_index, rng) -> list``. Keys:

    ``bond_zat`` int (required); ``register_height`` int (required, absolute height);
    ``uptime`` float (default ``attest["uptime"]``, 1.0); ``mean_outage_blocks`` float (default
    ``attest["mean_outage_blocks"]``; None = iid per block); ``outages`` list of ``(start, end)``
    half-open absolute height intervals forced offline; ``common`` bool — member of the common outage
    process (default True); ``bias_bps`` float (0); ``noise_bps`` float (``attest["noise_bps"]``, 0);
    ``phase`` int in ``[0, k)`` (random); ``frozen_from`` height — from then on it re-signs the
    price it reported first at or after that height (a stuck feed, for PIN-2); ``equivocate_at``
    int or list of heights (EQV-1 against it); ``withdraw_height`` int (bond spend; clamped up to
    ``registerHeight + bondMinLock + 1``, the earliest a CLTV bond can be spent); ``revive`` bool
    (default ``attest["revive"]``, True: send REV-1 after a dormancy, see ``revive_delay``);
    ``revive_delay`` int
    (``attest["revive_delay"]``, 1): REV-1 at the first ``H ≥ dormantHeight + revive_delay``
    with an own attestation cited in ``(H − attestMaxAge, H − 1]``; ``revive_at`` list of heights
    (explicit REV-1 transactions, replay); ``sign_heights`` / ``sign_prices`` — explicit cited heights
    (and prices) replacing the generated cadence (replay).

``true_price``
    int64 µUSD ``(paths, n)`` or ``(n,)`` aligned with the series columns (≤ 0 = no source: no
    attestation). Falls back to ``inputs["true_price"]``.
``attest_interval`` k (default ``params["attestInterval"]``): an online attestor signs heights
    ``c ≡ phase (mod k)``. ``uptime``, ``mean_outage_blocks``, ``noise_bps`` defaults.
``common_noise_bps`` float: a price noise shared by all attestors at a cited height.
``common_outage`` dict ``{"uptime": u, "mean_outage_blocks": L}``: a shared outage process; members
    (``common`` True) are offline while it is down.
Demands (blocks whose transactions need a bundle), first present wins:
    ``demands`` list of ``{"height", "ref_height"?, "kind"?, "selector"?}`` (shared, or one list per
    path); ``demand_counts`` int ``(paths, n)``/``(n,)`` bundles per block; ``demand_heights``
    heights (shared or per path); else Poisson(``demand_rate``, default 1/48) per block.
    ``kind`` is ``"mint"`` (selector ``b""``, MINT-9), ``"notice"`` or ``"claim"`` (a 36-byte vault
    outpoint selector, random unless given); ``claim_fraction`` (0) of generated demands are claims.
    ``ref_height`` defaults to ``H − ref_lag`` (``attest["ref_lag"]``, default ``DEFAULT_REF_LAG``).
``block_hashes`` ``callable(path, height) -> hex`` or a mapping ``height -> hex`` (shared); default a
    synthetic hash ``SHA256(path key ‖ height)`` with the path key drawn from the path's RNG.
``seed`` int or ``rng`` (``np.random.Generator``; one integer is drawn from it): path ``i`` uses
    ``default_rng([seed, i])``, so a path does not depend on how many paths run.
``height0`` / ``start_height`` (default from ``series`` or ``inputs``, else 0 and
    ``params["startHeight"]``); ``record_status`` bool (store per-block attestor statuses).
Mid-chain series (``height0 > start_height``): events before ``height0`` are applied at ``height0``
    (registrations, maturity, ejections …); ``initial_trigger_height`` sets the carried ARM-1 trigger
    (else the trigger is evaluated from ``height0`` on), ``initial_seated_since`` the ``seatedSince`` of
    every attestor ELIGIBLE at ``height0`` (else ``height0``: no dormancy in the first
    ``dormancyBlocks``). BundleLog rows before ``height0`` are not known (empty PIN / dormancy
    windows at first).

``series`` may be an :class:`~ybcal.sim.activation.ActivationSeries`, a mapping, or any object; read
attributes/keys: ``p_mint`` (int64 ``(paths, n)``, ≤ 0 undefined — the cross-section xMint the engine
computed; without it PIN-2 never triggers), ``height0``, ``start_height``.
"""

from __future__ import annotations

import bisect
import hashlib
import heapq
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, NamedTuple

import numpy as np

from ybcal.model import kernels as K
from ybcal.model import reference as _ref
from ybcal.units import BLOCKS_PER_YEAR, COIN, MICRO_USD_PER_USD, PRICE_MAX, PRICE_MIN

OWNER_WP = "WP-5"

#: Attest.status (view.h).
UNARMED, TRIGGERED, ARMED = _ref.UNARMED, _ref.TRIGGERED, _ref.ARMED
#: AttestorRecord.status (view.h); ``NOT_REGISTERED`` (−1) is the simulator's "no record yet".
PENDING, ELIGIBLE, DORMANT, EJECTED, WITHDRAWN = (_ref.A_PENDING, _ref.A_ELIGIBLE, _ref.A_DORMANT,
                                                  _ref.A_EJECTED, _ref.A_WITHDRAWN)
NOT_REGISTERED = -1

#: Transition causes (``AttestSeries.transitions["cause"]``).
CAUSE_REGISTER, CAUSE_MATURE, CAUSE_DORMANT, CAUSE_REVIVE, CAUSE_EJECT, CAUSE_WITHDRAW = range(6)
CAUSE_NAMES = ("REGISTER", "MATURE", "DORMANT", "REVIVE", "EJECT", "WITHDRAW")
#: Demand kinds.
KIND_MINT, KIND_NOTICE, KIND_CLAIM = range(3)
KIND_NAMES = ("mint", "notice", "claim")
#: Demand outcomes (``AttestSeries.demands["reason"]``).
R_OK, R_UNARMED, R_NO_SNAPSHOT, R_INSUFFICIENT, R_COUNT = range(5)
REASON_NAMES = ("ok", "unarmed", "no-snapshot", "insufficient", "count")

UNDEF = -1
MAX_ATTESTORS = 64       # seated / selected / pinned sets are uint64 bitmasks by seq

_EV_REGISTER, _EV_EJECT, _EV_WITHDRAW, _EV_REVIVE, _EV_MATURE = range(5)   # in-block order

DEMAND_DTYPE = np.dtype([
    ("path", np.int32), ("height", np.int64), ("ref_height", np.int64), ("kind", np.int8),
    ("armed", np.bool_), ("reason", np.int8), ("success", np.bool_), ("n_selected", np.int8),
    ("n_fresh", np.int8), ("selected", np.uint64), ("signed", np.uint64),
    ("a_mint", np.int64), ("a_claim", np.int64)])
TRANSITION_DTYPE = np.dtype([("path", np.int32), ("seq", np.int16), ("height", np.int64),
                             ("old", np.int8), ("new", np.int8), ("cause", np.int8)])


class BundleLogRow(NamedTuple):
    """BundleLog[H] (view.h BundleLogRecord): ``a_mint``/``a_claim`` ``None`` when undefined."""

    a_mint: int | None
    a_claim: int | None
    selected_seqs: tuple[int, ...]
    seqs: tuple[int, ...]
    prices: tuple[int, ...]
    cited_heights: tuple[int, ...]


@dataclass
class AttestorFinal:
    """An attestor record at the end of a path."""

    seq: int
    roster_index: int
    bond_zat: int
    register_height: int
    status: int
    status_height: int
    seated_since: int
    bond_spent_height: int


@dataclass
class AttestSeries:
    """Per-path, per-block output of :func:`simulate`. Column ``j`` is height ``height0 + j``.
    Bitmask arrays have bit ``s`` set for seq ``s``."""

    height0: int
    start_height: int
    status: np.ndarray            #: int8 (paths, n): UNARMED / TRIGGERED / ARMED (snapshot attest)
    trigger_height: np.ndarray    #: int64 (paths,), −1 = never
    arm_height: np.ndarray        #: int64 (paths,), −1 = never
    eligible_count: np.ndarray    #: int16 (paths, n), after maturity at H
    seated: np.ndarray            #: uint64 (paths, n) bitmask
    pinned_seqs: np.ndarray       #: uint64 (paths, n) bitmask (PIN-2)
    pin2_triggered: np.ndarray    #: bool (paths, n)
    bundle_row: np.ndarray        #: bool (paths, n): BundleLog[H] exists
    row_a_mint: np.ndarray        #: int64 (paths, n), −1 = no row / undefined
    row_a_claim: np.ndarray       #: int64 (paths, n)
    pin1_triggered: np.ndarray    #: bool (paths, n): the PIN-1 trigger from the rows (state.cpp:1130)
    demands: np.ndarray           #: DEMAND_DTYPE records
    transitions: np.ndarray       #: TRANSITION_DTYPE records
    bundle_log: list[dict[int, BundleLogRow]]
    attestors: list[list[AttestorFinal]]
    seq_of_roster: list[list[int]]          #: per path: roster index → seq (−1 = not registered)
    attestor_status: np.ndarray | None = None   #: int8 (paths, A, n) when ``record_status``
    meta: dict[str, Any] = field(default_factory=dict)

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
    def armed(self) -> np.ndarray:
        return self.status == ARMED

    def column(self, height: int) -> int:
        return height - self.height0

    def seated_at(self, path: int, height: int) -> list[int]:
        return seqs_of(int(self.seated[path, self.column(height)]))

    def pinned_at(self, path: int, height: int) -> list[int]:
        return seqs_of(int(self.pinned_seqs[path, self.column(height)]))

    def bundle_success_rate(self) -> float:
        """Share of demands that needed a bundle (ARMED at R) and got one."""
        d = self.demands[self.demands["armed"]]
        return float(d["success"].mean()) if len(d) else float("nan")


def seqs_of(mask: int) -> list[int]:
    """The ascending seqs of a bitmask."""
    out, s = [], 0
    while mask:
        if mask & 1:
            out.append(s)
        mask >>= 1
        s += 1
    return out


def mask_of(seqs) -> int:
    m = 0
    for s in seqs:
        m |= 1 << int(s)
    return m


# ---------------------------------------------------------------------------------------------------
# Inputs


def _get(obj, name: str, default=None):
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _paths_array(a, paths: int | None, n: int | None, dtype) -> np.ndarray:
    arr = np.asarray(a, dtype=dtype)
    if arr.ndim == 1:
        arr = arr[np.newaxis, :]
    if paths is not None and arr.shape[0] == 1 and paths > 1:
        arr = np.broadcast_to(arr, (paths, arr.shape[1]))
    if n is not None and arr.shape[1] != n:
        raise ValueError(f"array has {arr.shape[1]} columns, expected {n}")
    return arr


def markov_online(n: int, uptime: float, mean_outage_blocks: float | None,
                  rng: np.random.Generator) -> np.ndarray:
    """A per-block online mask with stationary probability ``uptime``: iid when
    ``mean_outage_blocks`` is None, else a two-state Markov chain whose outages last
    ``mean_outage_blocks`` on average (down→up ``1/L``, up→down ``(1 − u)/(u·L)``), stationary start."""
    if uptime >= 1.0:
        return np.ones(n, dtype=bool)
    if uptime <= 0.0:
        return np.zeros(n, dtype=bool)
    if mean_outage_blocks is None or mean_outage_blocks <= 0:
        return rng.random(n) < uptime
    beta = min(1.0, 1.0 / mean_outage_blocks)
    alpha = min(1.0, beta * (1.0 - uptime) / uptime)
    out = np.empty(n, dtype=bool)
    state = bool(rng.random() < uptime)
    pos = 0
    while pos < n:
        m = max(16, int((n - pos) * min(alpha, beta)) + 16)
        up_len = rng.geometric(alpha, m)
        down_len = rng.geometric(beta, m)
        for i in range(m):
            for is_up, length in ((True, up_len[i]), (False, down_len[i])) if state else \
                    ((False, down_len[i]), (True, up_len[i])):
                end = min(n, pos + int(length))
                out[pos:end] = is_up
                pos = end
                if pos >= n:
                    return out
    return out


@dataclass
class _Att:
    roster_index: int
    bond: int
    reg: int
    eject: int            # effective EQV-1 height, or a huge value
    withdraw: int         # effective bond-spend height, or a huge value
    revive: bool
    revive_delay: int
    revive_at: tuple[int, ...]
    sign_h: np.ndarray    # sorted usable cited heights
    sign_p: np.ndarray    # their prices

    @property
    def terminal(self) -> int:
        return min(self.eject, self.withdraw)


_FAR = 1 << 62


@dataclass
class _Ctx:
    params: Mapping
    n: int
    height0: int
    start: int
    k: int
    max_age: int
    attest: Mapping
    p_mint: np.ndarray | None
    true_price: np.ndarray | None
    seed: int
    record_status: bool


def _seed_from(attest: Mapping) -> int:
    rng = attest.get("rng")
    if rng is not None:
        return int(rng.integers(0, 2**63 - 1))
    seed = attest.get("seed", 0)
    if isinstance(seed, np.random.SeedSequence):
        return int(seed.generate_state(1, dtype=np.uint64)[0] >> 1)
    return int(seed)


def _block_hash_fn(ctx: _Ctx, path: int, rng: np.random.Generator) -> Callable[[int], str]:
    bh = ctx.attest.get("block_hashes")
    if callable(bh):
        return lambda h: bh(path, h)
    if isinstance(bh, Mapping):
        return lambda h: bh[h]
    key = rng.bytes(16)
    return lambda h: hashlib.sha256(key + int(h).to_bytes(8, "little", signed=True)).hexdigest()


def _prepare_attestors(ctx: _Ctx, path: int, rng: np.random.Generator) -> tuple[list[_Att], list[int]]:
    P, a = ctx.params, ctx.attest
    roster = a["roster"]
    if callable(roster):
        roster = roster(path, rng)
    bond_min = int(P["bondMin"])
    lock = int(P["bondMinLock"])
    n, h0, start, k = ctx.n, ctx.height0, ctx.start, ctx.k
    heights = h0 + np.arange(n, dtype=np.int64)
    # common outage process and common noise (one draw per path, before the attestors)
    common = a.get("common_outage")
    common_up = (markov_online(n, float(common.get("uptime", 1.0)), common.get("mean_outage_blocks"), rng)
                 if common else np.ones(n, dtype=bool))
    common_noise = float(a.get("common_noise_bps", 0.0))
    z_common = rng.standard_normal(n) if common_noise else np.zeros(n)
    tp = ctx.true_price[path] if ctx.true_price is not None else None

    order = sorted(range(len(roster)), key=lambda i: (int(roster[i]["register_height"]), i))
    seq_of = [-1] * len(roster)
    atts: list[_Att] = []
    for i in order:
        e = roster[i]
        bond, reg = int(e["bond_zat"]), int(e["register_height"])
        # per-attestor draws happen for every roster entry (registered or not) so seeds stay aligned
        u = float(e.get("uptime", a.get("uptime", 1.0)))
        L = e.get("mean_outage_blocks", a.get("mean_outage_blocks"))
        online = markov_online(n, u, L, rng)
        phase = int(e["phase"]) if "phase" in e else int(rng.integers(0, max(1, k)))
        z = rng.standard_normal(n)
        if bond < bond_min or reg < start:          # REG-A1 bond floor; below start nothing registers
            continue
        seq = len(atts)
        if seq >= MAX_ATTESTORS:
            raise ValueError(f"more than {MAX_ATTESTORS} registered attestors (bitmask limit)")
        seq_of[i] = seq
        eq = e.get("equivocate_at")
        eqs = sorted(int(x) for x in ([eq] if isinstance(eq, int) else (eq or ())) if int(x) >= reg)
        wd = e.get("withdraw_height")
        withdraw = max(int(wd), reg + lock + 1) if wd is not None else _FAR
        # an EQV-1 at the bond-spend height is ordered first (in-block order: EQV before IN-2)
        eject = next((x for x in eqs if x <= withdraw), _FAR)
        terminal = min(eject, withdraw)
        if "sign_heights" in e:
            sh = np.asarray(e["sign_heights"], dtype=np.int64)
            if "sign_prices" in e:
                sp = np.asarray(e["sign_prices"], dtype=np.int64)
            else:
                if tp is None:
                    raise ValueError("sign_heights without sign_prices needs true_price")
                sp = np.array([int(tp[c - h0]) if 0 <= c - h0 < n else 0 for c in sh], dtype=np.int64)
            o = np.argsort(sh, kind="stable")
            sh, sp = sh[o], sp[o]
        else:
            if tp is None:
                raise ValueError("attest needs true_price (or explicit sign_heights/sign_prices)")
            for s0, s1 in e.get("outages", ()):
                online[max(0, int(s0) - h0):max(0, int(s1) - h0)] = False
            if e.get("common", True):
                online &= common_up
            cad = ((heights - phase) % max(1, k)) == 0
            use = online & cad & (heights >= reg) & (tp > 0)
            idx = np.nonzero(use)[0]
            bias = float(e.get("bias_bps", 0.0))
            noise = float(e.get("noise_bps", a.get("noise_bps", 0.0)))
            f = 1.0 + bias / 1e4 + noise / 1e4 * z[idx] + common_noise / 1e4 * z_common[idx]
            sp = np.rint(tp[idx].astype(float) * f).astype(np.int64)
            sh = heights[idx]
            ff = e.get("frozen_from")
            if ff is not None:
                after = sh >= int(ff)
                if after.any():
                    sp[after] = sp[np.argmax(after)]
        keep = (sh >= start) & (sh < terminal) & (sh >= reg) & (sp >= PRICE_MIN) & (sp <= PRICE_MAX)
        atts.append(_Att(i, bond, reg, eject, withdraw, bool(e.get("revive", a.get("revive", True))),
                         int(e.get("revive_delay", a.get("revive_delay", 1))),
                         tuple(int(x) for x in e.get("revive_at", ())), sh[keep], sp[keep]))
    return atts, seq_of


def _demands_for_path(ctx: _Ctx, path: int, rng: np.random.Generator) -> list[tuple[int, int, int, bytes]]:
    a, n, h0 = ctx.attest, ctx.n, ctx.height0
    lag = int(a.get("ref_lag", ctx.params["DEFAULT_REF_LAG"]))
    claim_frac = float(a.get("claim_fraction", 0.0))
    out: list[tuple[int, int, int, bytes]] = []

    def kind_sel(kind_name: str | None, sel):
        kind = KIND_NAMES.index(kind_name) if kind_name else KIND_MINT
        if sel is None:
            sel = b"" if kind == KIND_MINT else rng.bytes(36)
        return kind, bytes(sel)

    if a.get("demands") is not None:
        d = a["demands"]
        rows = d[path] if (len(d) and isinstance(d[0], (list, tuple))) else d
        for r in rows:
            h = int(r["height"])
            kind, sel = kind_sel(r.get("kind"), r.get("selector"))
            out.append((h, int(r.get("ref_height", h - lag)), kind, sel))
        out.sort(key=lambda x: x[0])
        return out
    if a.get("demand_counts") is not None:
        counts = _paths_array(a["demand_counts"], None, n, np.int64)
        counts = counts[path if counts.shape[0] > 1 else 0]
    elif a.get("demand_heights") is not None:
        dh = a["demand_heights"]
        hs = dh[path] if (len(dh) and isinstance(dh[0], (list, tuple, np.ndarray))) else dh
        counts = np.bincount(np.asarray(hs, dtype=np.int64) - h0, minlength=n)[:n]
    else:
        counts = rng.poisson(float(a.get("demand_rate", 1.0 / 48.0)), n)
    for j in np.nonzero(counts)[0]:
        h = h0 + int(j)
        for _ in range(int(counts[j])):
            kind = KIND_CLAIM if claim_frac > 0 and rng.random() < claim_frac else KIND_MINT
            out.append((h, h - lag, kind, b"" if kind == KIND_MINT else rng.bytes(36)))
    return out


def _pin2_trigger_mask(ctx: _Ctx, path: int) -> np.ndarray:
    """PIN-2 trigger per column from the engine's pMint (state.cpp:1151): pMint at H − 1 and
    H − 1 − pinWindow (virtual / outside the series = undefined) differ by > pinDeltaBps."""
    n = ctx.n
    out = np.zeros(n, dtype=bool)
    if ctx.p_mint is None:
        return out
    pm = ctx.p_mint[path]
    pw = int(ctx.params["pinWindow"])
    delta = max(0, int(ctx.params["pinDeltaBps"]))
    heights = ctx.height0 + np.arange(n)
    j1 = np.arange(n) - 1
    j0 = j1 - pw
    ok = (j0 >= 0) & (j1 >= 0)
    x1 = np.where(ok, pm[np.clip(j1, 0, n - 1)], -1)
    x0 = np.where(ok, pm[np.clip(j0, 0, n - 1)], -1)
    real = (heights - 1 - pw) >= ctx.start
    defined = (x1 > 0) & (x0 > 0) & real
    lo = np.minimum(x1, x0)
    hi = np.maximum(x1, x0)
    out = defined & ((hi - lo) * 10_000 > delta * lo)
    return out


# ---------------------------------------------------------------------------------------------------
# The walk


def _seated_since_at(S: np.ndarray, last_unseated: np.ndarray, carried: np.ndarray, h: int, a: int,
                     c: int) -> int:
    """``seatedSince`` of attestor ``a`` after the seating at height ``h + c`` of a segment starting at
    ``h`` (state.cpp:1189-1193): the start of its current seated run, the carried value if the run
    began before the segment; 0 when not seated. The C++ uses 0 for "not seated", so a run that
    starts at height 0 reads 0 there and is re-set to 1 at the next SNAP."""
    if not S[a, c]:
        return 0
    lu = int(last_unseated[a, c])
    v = h + lu + 1 if lu >= 0 else (int(carried[a]) or h)
    if v == 0 and h + c >= 1:
        v = 1
    return v


def _weights(atts: list[_Att], idx: Sequence[int], height, trig: int | None, fw: int, cap: int):
    """bond · clamp(H − ageOrigin, 0, ageCap) for the attestors ``idx`` (Python ints)."""
    out = []
    for a in idx:
        at = atts[a]
        origin = trig if (trig is not None and trig <= height and at.reg <= trig + fw) else at.reg
        out.append(at.bond * min(max(height - origin, 0), cap))
    return out


def _simulate_path(ctx: _Ctx, path: int) -> dict:
    P = ctx.params
    rng = np.random.default_rng([ctx.seed, path])
    atts, seq_of = _prepare_attestors(ctx, path, rng)
    demands = _demands_for_path(ctx, path, rng)
    block_hash = _block_hash_fn(ctx, path, rng)
    n, h0, start = ctx.n, ctx.height0, ctx.start
    hlast = h0 + n - 1
    hfirst = max(h0, start)
    A = len(atts)
    n_slots = max(0, int(P["nSlots"]))
    m_sel, k_slack = max(0, int(P["mSelect"])), max(0, int(P["kSlack"]))
    bundle_max = max(0, int(P["bundleMax"]))
    q_low, q_high = int(P["qLowBps"]), int(P["qHighBps"])
    arm_min, arm_delay = int(P["attestArmMin"]), int(P["attestArmDelay"])
    required = bool(P["attestRequired"])
    maturity = int(P["bondMaturity"])
    fw, cap = int(P["foundingWindow"]), int(P["ageCap"])
    pw, pin_min_tags = int(P["pinWindow"]), int(P["pinMinTags"])
    dc, db, dmin = int(P["dormancyCheck"]), int(P["dormancyBlocks"]), max(1, int(P["dormancyMinBundles"]))
    max_age = ctx.max_age

    big = A > 0 and max(a.bond for a in atts) * max(cap, 0) >= (1 << 62)   # int64 weights would overflow
    bond_arr = np.array([a.bond for a in atts], dtype=object if big else np.int64)
    regs = np.array([a.reg for a in atts], dtype=np.int64)

    status = np.full(A, NOT_REGISTERED, dtype=np.int64)
    status_h = np.zeros(A, dtype=np.int64)
    seated_since = np.zeros(A, dtype=np.int64)
    spent_h = np.zeros(A, dtype=np.int64)
    trig: int | None = None            # Attest.triggerHeight once not UNARMED
    armh = -1

    o_status = np.zeros(n, dtype=np.int8)
    o_elig = np.zeros(n, dtype=np.int16)
    o_seated = np.zeros(n, dtype=np.uint64)
    o_pinned = np.zeros(n, dtype=np.uint64)
    o_rec = np.full((A, n), NOT_REGISTERED, dtype=np.int8) if ctx.record_status else None
    pin2 = _pin2_trigger_mask(ctx, path)
    transitions: list[tuple] = []

    def trans(seq: int, h: int, new: int, cause: int) -> None:
        transitions.append((path, seq, h, int(status[seq]), new, cause))
        status[seq] = new
        status_h[seq] = h

    # events: (height, order, seq)
    ev: list[tuple[int, int, int]] = []
    for s, a in enumerate(atts):
        ev.append((a.reg, _EV_REGISTER, s))
        ev.append((a.reg + maturity, _EV_MATURE, s))
        if a.eject < _FAR:
            ev.append((a.eject, _EV_EJECT, s))
        if a.withdraw < _FAR:
            ev.append((a.withdraw, _EV_WITHDRAW, s))
        for r in a.revive_at:
            ev.append((r, _EV_REVIVE, s))
    heapq.heapify(ev)

    # demands grouped by height
    d_by_h: dict[int, list] = {}
    for d in demands:
        d_by_h.setdefault(d[0], []).append(d)
    max_rows = len({d[0] for d in demands})
    row_h = np.zeros(max_rows, dtype=np.int64)
    row_sel = np.zeros(max_rows, dtype=np.uint64)
    row_sig = np.zeros(max_rows, dtype=np.uint64)
    row_rec: list[tuple[int, BundleLogRow]] = []
    n_rows = 0
    d_out: list[tuple] = []
    pinned_cache: dict[int, int] = {}
    sel_cache: dict[tuple, list[int]] = {}

    sign_h = [a.sign_h.tolist() for a in atts]
    sign_p = [a.sign_p.tolist() for a in atts]

    def fresh(s: int, R: int):
        """AttestationPool::Freshest: the newest own attestation cited in (R − maxAge, R], ≥ start."""
        sh = sign_h[s]
        i = bisect.bisect_right(sh, R) - 1
        if i >= 0:
            c = sh[i]
            if c > R - max_age and c >= start:
                return c, sign_p[s][i]
        return None

    def apply_event(h: int, kind: int, s: int) -> None:
        st = status[s]
        if kind == _EV_REGISTER:
            trans(s, h, PENDING, CAUSE_REGISTER)
        elif kind == _EV_MATURE:
            if st == PENDING:
                trans(s, h, ELIGIBLE, CAUSE_MATURE)
        elif kind == _EV_EJECT:
            if st not in (WITHDRAWN, EJECTED, NOT_REGISTERED):
                trans(s, h, EJECTED, CAUSE_EJECT)
        elif kind == _EV_WITHDRAW:
            if st != NOT_REGISTERED and spent_h[s] == 0:
                spent_h[s] = h
                if st != EJECTED:
                    trans(s, h, WITHDRAWN, CAUSE_WITHDRAW)
        elif kind == _EV_REVIVE and st == DORMANT:
            trans(s, h, ELIGIBLE, CAUSE_REVIVE)

    def schedule_revive(s: int, h: int) -> None:
        a = atts[s]
        if not a.revive:
            return
        T = h + max(1, a.revive_delay)
        sh = a.sign_h
        i = int(np.searchsorted(sh, T - max_age + 1, side="left"))   # first c with c + maxAge − 1 ≥ T
        while i < len(sh):
            c = int(sh[i])
            if c >= start:
                hr = max(T, c + 1)
                if hr <= hlast:
                    heapq.heappush(ev, (hr, _EV_REVIVE, s))
                return
            i += 1

    def pinned_at(R: int) -> int:
        j = R - h0
        if j < 0 or not pin2[j]:
            return 0
        if R in pinned_cache:
            return pinned_cache[R]
        lo = max(R - pw, start)
        i0 = int(np.searchsorted(row_h[:n_rows], lo, side="left"))
        i1 = int(np.searchsorted(row_h[:n_rows], R, side="left"))
        rows = [(r.seqs, r.prices, r.cited_heights) for _h, r in row_rec[i0:i1]]
        m = mask_of(K.pin2_pinned_seqs(rows, pin_min_tags))
        pinned_cache[R] = m
        o_pinned[j] = m
        return m

    def process_demands(t: int) -> None:
        nonlocal n_rows
        a_mints, a_claims, sel_u, pairs = [], [], 0, set()
        any_ok = False
        for (_h, R, kind, sel) in d_by_h[t]:
            jR = R - h0
            rec = [path, t, R, kind, False, R_UNARMED, False, 0, 0, 0, 0, UNDEF, UNDEF]
            if start > R or hfirst > R or jR >= n or t <= R:
                rec[5] = R_UNARMED if start > R else R_NO_SNAPSHOT
                d_out.append(tuple(rec))
                continue
            armed = required and o_status[jR] == ARMED
            rec[4] = armed
            if not armed:
                d_out.append(tuple(rec))
                continue
            pool_mask = int(o_seated[jR]) & ~pinned_at(R)
            trig_R = trig if (trig is not None and trig <= R) else None
            key = (R, sel, pool_mask)                 # MINT-9 bundles share the empty selector
            selected = sel_cache.get(key)
            if selected is None:
                pool_seqs = seqs_of(pool_mask)
                w = _weights(atts, pool_seqs, R, trig_R, fw, cap)
                selected = K.select_attestors(block_hash(R), sel, list(zip(pool_seqs, w, strict=True)),
                                              m_sel, k_slack)
                if len(sel_cache) > 4096:
                    sel_cache.clear()
                sel_cache[key] = selected
            chosen = []
            for s in sorted(selected):
                f = fresh(s, R)
                if f is not None:
                    chosen.append((s, f[0], f[1]))
            rec[7], rec[8] = len(selected), len(chosen)
            rec[9] = mask_of(selected)
            ok = m_sel <= len(chosen) <= bundle_max and len(chosen) > 0
            if not ok:
                rec[5] = R_COUNT if len(chosen) > bundle_max else R_INSUFFICIENT
                d_out.append(tuple(rec))
                continue
            wc = _weights(atts, [c[0] for c in chosen], R, trig_R, fw, cap)
            stat = K.bundle_stat([(c[2], wi) for c, wi in zip(chosen, wc, strict=True)], q_low, q_high, m_sel)
            rec[5], rec[6], rec[10] = R_OK, True, mask_of(c[0] for c in chosen)
            if stat is not None:
                rec[11], rec[12] = stat
                a_mints.append(stat[0])
                a_claims.append(stat[1])
            any_ok = True
            sel_u |= rec[9]
            pairs.update((c[0], c[2], c[1]) for c in chosen)
            d_out.append(tuple(rec))
        if any_ok:
            ps = sorted(pairs)
            row = BundleLogRow(K.lower_median(a_mints), K.lower_median(a_claims), tuple(seqs_of(sel_u)),
                               tuple(p[0] for p in ps), tuple(p[1] for p in ps), tuple(p[2] for p in ps))
            row_h[n_rows] = t
            row_sel[n_rows] = sel_u
            row_sig[n_rows] = mask_of(row.seqs)
            row_rec.append((t, row))
            n_rows += 1

    # pre-series events (heights below the first real snapshot) collapse onto hfirst; a series that
    # starts mid-chain may carry the trigger height and a seatedSince for the attestors seated then
    init_trig = ctx.attest.get("initial_trigger_height")
    if init_trig is not None and hfirst > start:
        trig, armh = int(init_trig), int(init_trig) + arm_delay
    init_ss = ctx.attest.get("initial_seated_since")
    h = hfirst
    seg_count = 0
    while h <= hlast:
        while ev and ev[0][0] <= h:
            eh, kind, s = heapq.heappop(ev)
            apply_event(max(eh, h) if eh < hfirst else eh, kind, s)
        if h == hfirst and init_ss is not None and hfirst > start:
            seated_since[:] = np.where(status == ELIGIBLE, int(init_ss), 0)
        eligible = status == ELIGIBLE
        ecount = int(eligible.sum())
        if trig is None and arm_min > 0 and ecount >= arm_min:
            trig, armh = h, h + arm_delay
        nxt = ev[0][0] if ev else hlast + 1
        seg_end = min(nxt - 1, hlast)
        L = seg_end - h + 1
        j0 = h - h0
        hs = np.arange(h, seg_end + 1, dtype=np.int64)
        # seating for [h, seg_end]
        elig_idx = np.nonzero(eligible)[0]
        if ecount <= n_slots:
            S = np.broadcast_to(eligible[:, None], (A, L))
            const_mask = mask_of(elig_idx)
            o_seated[j0:j0 + L] = const_mask
        else:
            origin = np.where(regs <= trig + fw, trig, regs) if trig is not None else regs
            age = np.clip(hs[None, :] - origin[:, None], 0, cap)
            Wt = bond_arr[:, None] * age
            Wt = np.where(eligible[:, None], Wt, -1)
            order = np.argsort(-Wt, axis=0, kind="stable") if not big else \
                np.array([sorted(range(A), key=lambda a, c=c: (-Wt[a, c], a)) for c in range(L)]).T
            rank = np.empty_like(order)
            np.put_along_axis(rank, order, np.arange(A)[:, None].repeat(L, axis=1), axis=0)
            S = (rank < n_slots) & eligible[:, None]
            masks = np.zeros(L, dtype=np.uint64)
            for a in elig_idx:
                masks |= S[a].astype(np.uint64) << np.uint64(a)
            o_seated[j0:j0 + L] = masks
        o_elig[j0:j0 + L] = ecount
        if trig is None:
            o_status[j0:j0 + L] = UNARMED
        else:
            o_status[j0:j0 + L] = np.where(hs >= armh, ARMED, TRIGGERED)
        if o_rec is not None:
            o_rec[:, j0:j0 + L] = status[:, None]
        # seatedSince inside the segment
        carried = seated_since.copy()
        idx = np.arange(L)
        last_unseated = np.maximum.accumulate(np.where(~S, idx[None, :], -1), axis=1) if A else None

        def ss_at(a: int, c: int, S=S, lu=last_unseated, carried=carried, h=h) -> int:
            return _seated_since_at(S, lu, carried, h, a, c)

        # walk the points of the segment
        pts = set(d for d in d_by_h if h <= d <= seg_end)
        pts.update((np.nonzero(pin2[j0:j0 + L])[0] + h).tolist())
        if dc > 0:
            first = h + ((-h) % dc)
            pts.update(range(first, seg_end + 1, dc))
        cut = seg_end
        for t in sorted(pts):
            if t in d_by_h:
                process_demands(t)
            if pin2[t - h0]:
                pinned_at(t)
            if dc > 0 and t % dc == 0 and A:
                c = t - h
                cands = [a for a in elig_idx if S[a, c] and 0 < ss_at(a, c) <= t - db]
                if not cands:
                    continue
                lo = max(t - db + 1, start)
                i0 = int(np.searchsorted(row_h[:n_rows], lo, side="left"))
                i1 = int(np.searchsorted(row_h[:n_rows], t, side="right"))
                sel_w, sig_w = row_sel[i0:i1], row_sig[i0:i1]
                flipped = False
                for a in cands:
                    bit = np.uint64(1) << np.uint64(a)
                    in_sel = (sel_w & bit) != 0
                    if int(in_sel.sum()) >= dmin and not ((sig_w & bit) != 0)[in_sel].any():
                        seated_since[a] = ss_at(a, c)
                        trans(a, t, DORMANT, CAUSE_DORMANT)
                        schedule_revive(a, t)
                        flipped = True
                if flipped:
                    cut = t
                    break
        # carry seatedSince to the end of the segment (cut)
        c = cut - h
        for a in range(A):
            seated_since[a] = ss_at(a, c) if S[a, c] else 0
        seg_count += 1
        h = cut + 1
    finals = [AttestorFinal(s, a.roster_index, a.bond, a.reg, int(status[s]), int(status_h[s]),
                            int(seated_since[s]), int(spent_h[s])) for s, a in enumerate(atts)]
    # demands outside the walked heights (before the first real snapshot / after the series)
    for (dh, R, kind, _sel) in demands:
        if dh < hfirst or dh > hlast:
            d_out.append((path, dh, R, kind, False, R_NO_SNAPSHOT, False, 0, 0, 0, 0, UNDEF, UNDEF))
    return {"status": o_status, "trigger": -1 if trig is None else trig, "arm": armh, "elig": o_elig,
            "seated": o_seated,
            "pinned": o_pinned, "pin2": pin2, "rows": row_rec, "demands": d_out, "transitions": transitions,
            "finals": finals, "seq_of": seq_of, "rec": o_rec, "segments": seg_count}


def pin1_trigger_series(row_heights, row_a_mint, heights, *, pin_window: int, start_height: int,
                        pin_min_bundles: int, pin_delta_bps: int) -> np.ndarray:
    """PIN-1 trigger (state.cpp:1130-1137) at each of ``heights`` from sorted BundleLog rows: the rows
    of ``[max(H − pinWindow, start), H − 1]`` number ≥ max(1, pinMinBundles) and their defined
    (``> 0``) aMint values span more than ``pinDeltaBps``. Vectorised with a sparse table (range
    min/max); equal to ``kernels.pin1_triggered`` per height (tested)."""
    rh = np.asarray(row_heights, dtype=np.int64)
    am = np.asarray(row_a_mint, dtype=np.int64)
    H = np.asarray(heights, dtype=np.int64)
    out = np.zeros(H.shape, dtype=bool)
    if rh.size == 0:
        return out
    lo_h = np.maximum(H - pin_window, start_height)
    i0 = np.searchsorted(rh, lo_h, side="left")
    i1 = np.searchsorted(rh, H, side="left")            # rows < H
    cnt = i1 - i0
    big = np.iinfo(np.int64).max
    mins = [np.where(am > 0, am, big)]
    maxs = [np.where(am > 0, am, -1)]
    span = 1
    while 2 * span <= rh.size:
        mins.append(np.minimum(mins[-1][:-span], mins[-1][span:]))
        maxs.append(np.maximum(maxs[-1][:-span], maxs[-1][span:]))
        span *= 2
    ok = cnt >= max(1, pin_min_bundles)
    safe = np.where(ok & (cnt > 0), cnt, 1)
    lvl = np.floor(np.log2(safe)).astype(np.int64)
    lo = np.full(H.shape, big, dtype=np.int64)
    hi = np.full(H.shape, -1, dtype=np.int64)
    for k in np.unique(lvl[ok]):
        sel = ok & (lvl == k)
        a = i0[sel]
        b = i1[sel] - (1 << int(k))
        lo[sel] = np.minimum(mins[k][a], mins[k][b])
        hi[sel] = np.maximum(maxs[k][a], maxs[k][b])
    defined = ok & (lo < big)
    out = defined & ((hi - lo) * 10_000 > max(0, pin_delta_bps) * lo)
    return out


def simulate(params: Mapping, inputs, series=None) -> AttestSeries:
    """The attestation layer for every path (see the module docstring for the input schema)."""
    t0 = time.perf_counter()
    a = _get(inputs, "attest")
    if a is None:
        raise ValueError("inputs carries no 'attest' dict")
    tp = a.get("true_price", _get(inputs, "true_price"))
    p_mint = _get(series, "p_mint")
    n = None
    for arr in (tp, p_mint):
        if arr is not None:
            n = np.asarray(arr).shape[-1]
            break
    if n is None:
        n = int(a["n_blocks"])
    paths = a.get("paths")
    for arr in (tp, p_mint):
        if arr is not None and np.asarray(arr).ndim == 2:
            paths = paths or np.asarray(arr).shape[0]
    paths = int(paths or 1)
    tp_arr = _paths_array(tp, paths, n, np.int64) if tp is not None else None
    pm_arr = _paths_array(p_mint, paths, n, np.int64) if p_mint is not None else None
    height0 = int(a.get("height0", _get(series, "height0", _get(inputs, "height0", 0))))
    start = int(a.get("start_height", _get(series, "start_height",
                                            _get(inputs, "start_height", params["startHeight"]))))
    ctx = _Ctx(params, n, height0, start, int(a.get("attest_interval", params["attestInterval"])),
               int(params["attestMaxAge"]), a, pm_arr, tp_arr, _seed_from(a), bool(a.get("record_status")))
    res = [_simulate_path(ctx, i) for i in range(paths)]

    def stack(key, dtype):
        return np.stack([r[key] for r in res]).astype(dtype, copy=False)

    row_present = np.zeros((paths, n), dtype=bool)
    row_am = np.full((paths, n), UNDEF, dtype=np.int64)
    row_ac = np.full((paths, n), UNDEF, dtype=np.int64)
    pin1 = np.zeros((paths, n), dtype=bool)
    heights = height0 + np.arange(n)
    for i, r in enumerate(res):
        rh = np.array([h for h, _ in r["rows"]], dtype=np.int64)
        ram = np.array([row.a_mint or 0 for _, row in r["rows"]], dtype=np.int64)
        if rh.size:
            j = rh - height0
            row_present[i, j] = True
            row_am[i, j] = np.where(ram > 0, ram, UNDEF)
            row_ac[i, j] = [row.a_claim if row.a_claim else UNDEF for _, row in r["rows"]]
        pin1[i] = pin1_trigger_series(rh, ram, heights, pin_window=int(params["pinWindow"]),
                                      start_height=start, pin_min_bundles=int(params["pinMinBundles"]),
                                      pin_delta_bps=int(params["pinDeltaBps"]))
        pin1[i] &= heights >= max(start, height0)
    demands = np.array([d for r in res for d in r["demands"]], dtype=DEMAND_DTYPE)
    if len(demands):
        demands = demands[np.lexsort((demands["height"], demands["path"]))]
    transitions = np.array([t for r in res for t in r["transitions"]], dtype=TRANSITION_DTYPE)
    rec = None
    if ctx.record_status:
        A = max((r["rec"].shape[0] for r in res), default=0)
        rec = np.full((paths, A, n), NOT_REGISTERED, dtype=np.int8)
        for i, r in enumerate(res):
            rec[i, :r["rec"].shape[0]] = r["rec"]
    return AttestSeries(
        height0=height0, start_height=start, status=stack("status", np.int8),
        trigger_height=np.array([r["trigger"] for r in res], dtype=np.int64),
        arm_height=np.array([r["arm"] for r in res], dtype=np.int64),
        eligible_count=stack("elig", np.int16), seated=stack("seated", np.uint64),
        pinned_seqs=stack("pinned", np.uint64), pin2_triggered=stack("pin2", bool),
        bundle_row=row_present, row_a_mint=row_am, row_a_claim=row_ac, pin1_triggered=pin1,
        demands=demands, transitions=transitions,
        bundle_log=[dict(r["rows"]) for r in res], attestors=[r["finals"] for r in res],
        seq_of_roster=[r["seq_of"] for r in res], attestor_status=rec,
        meta={"seconds": time.perf_counter() - t0, "segments": sum(r["segments"] for r in res),
              "seed": ctx.seed, "attest_interval": ctx.k})


# ---------------------------------------------------------------------------------------------------
# G8 analytic helpers (PLAN §5.8)


def liveness_probability(m: int, k: int, uptime: float, rho_correlation: float = 0.0) -> float:
    """P(at least ``m`` of the ``m + k`` selected attestors have a fresh attestation), each available
    with probability ``uptime``: binomial when ``rho_correlation`` is 0, else beta-binomial with
    intra-class correlation ρ (``α = u(1 − ρ)/ρ``, ``β = (1 − u)(1 − ρ)/ρ``) — the correlated-outage
    extension (common outages raise the chance that many are down together)."""
    from scipy.stats import betabinom, binom

    n = m + k
    if m <= 0:
        return 1.0
    if uptime >= 1.0:
        return 1.0
    if uptime <= 0.0:
        return 0.0
    if rho_correlation <= 0:
        return float(binom.sf(m - 1, n, uptime))
    if rho_correlation >= 1:
        return float(uptime)
    a = uptime * (1 - rho_correlation) / rho_correlation
    b = (1 - uptime) * (1 - rho_correlation) / rho_correlation
    return float(betabinom.sf(m - 1, n, a, b))


def fresh_probability(uptime: float, attest_interval: int, attest_max_age: int,
                      mean_outage_blocks: float | None = None) -> float:
    """P(an attestor has a fresh attestation at a reference height): it signs every ``k`` blocks
    while online and a bundle accepts cited heights in ``(R − maxAge, R]``, so it needs to have been
    online at one of the ``⌈maxAge / k⌉`` (2 for maxAge = 2k) signing heights in that range. iid
    online: ``1 − (1 − u)^s``; Markov outages with mean length L: ``1 − (1 − u)·(1 − 1/L)^((s − 1)·k)``
    (down at the first slot, still down ``k`` blocks later, …)."""
    s = max(1, -(-attest_max_age // max(1, attest_interval)))
    if mean_outage_blocks is None or mean_outage_blocks <= 1:
        return 1.0 - (1.0 - uptime) ** s
    stay = 1.0 - 1.0 / mean_outage_blocks
    return 1.0 - (1.0 - uptime) * stay ** ((s - 1) * attest_interval)


def capture_threshold_shares(q_low_bps: int, q_high_bps: int | None = None) -> dict[str, float]:
    """The weight share of a bundle an adversary needs to dictate each bundle statistic (exact for the
    weighted quantile up to the ceiling of one unit of weight): ``aMint`` down needs ≥ qLow, up needs
    > 1 − qLow; ``aClaim`` down ≥ qHigh, up > 1 − qHigh."""
    qh = 10_000 - q_low_bps if q_high_bps is None else q_high_bps
    return {"a_mint_down": q_low_bps / 1e4, "a_mint_up": 1 - q_low_bps / 1e4,
            "a_claim_down": qh / 1e4, "a_claim_up": 1 - qh / 1e4}


def capture_probability(weights, adversarial, *, m_select: int, k_slack: int, q_low_bps: int,
                        q_high_bps: int, direction: str = "a_mint_down", move_bps: int = 1_000,
                        honest_price: int = 1_000_000, n_draws: int = 2_000, seed: int = 0) -> float:
    """Monte Carlo with the exact kernels: over ``n_draws`` synthetic block hashes, select from the
    seated pool ``weights`` (one weight per seat, ``adversarial`` flags the adversary's seats), every
    selected seat signs (honest at ``honest_price``, adversary at ``honest_price·(1 ± move)``) and
    the bundle statistic is computed; returns the fraction of bundles whose statistic moved by
    ≥ ``move_bps``. ``direction`` is one of :func:`capture_threshold_shares`' keys."""
    w = [int(x) for x in weights]
    adv = [bool(x) for x in adversarial]
    sign = -1 if direction.endswith("down") else 1
    adv_price = honest_price + sign * honest_price * move_bps // 10_000
    use_low = direction.startswith("a_mint")
    pool = list(enumerate(w))
    key = int(seed).to_bytes(8, "little", signed=False)
    hits = 0
    for d in range(n_draws):
        bh = hashlib.sha256(key + d.to_bytes(8, "little")).hexdigest()
        sel = K.select_attestors(bh, b"", pool, m_select, k_slack)
        entries = [(adv_price if adv[s] else honest_price, w[s]) for s in sorted(sel)]
        st = K.bundle_stat(entries, q_low_bps, q_high_bps, m_select)
        if st is None:
            continue
        v = st[0] if use_low else st[1]
        hits += int(abs(v - honest_price) * 10_000 >= move_bps * honest_price)
    return hits / n_draws


def capture_share_needed(q_low_bps: int, weights_distribution=None, *, q_high_bps: int | None = None,
                         m_select: int = 4, k_slack: int = 2, n_adversary_seats: int = 1,
                         direction: str = "a_mint_down", target: float = 0.5, move_bps: int = 1_000,
                         n_draws: int = 500, seed: int = 0, grid: int = 40) -> float:
    """The smallest weight share of the seated set an adversary needs to move the bundle statistic
    (``direction``) by ``move_bps`` in at least ``target`` of bundles.

    Without ``weights_distribution`` this is the bundle-level threshold of
    :func:`capture_threshold_shares`. With it (the honest seats' weights, a sequence), the adversary
    holds ``n_adversary_seats`` equal seats; the share is scanned on a grid of ``grid`` points with
    :func:`capture_probability` (exact selection + weighted-quantile kernels) and the first share
    reaching ``target`` is returned (``nan`` if none below 1)."""
    qh = 10_000 - q_low_bps if q_high_bps is None else q_high_bps
    if weights_distribution is None:
        return capture_threshold_shares(q_low_bps, qh)[direction]
    honest = [int(x) for x in weights_distribution]
    total_h = sum(honest)
    for g in range(1, grid):
        s = g / grid
        adv_total = s / (1 - s) * total_h
        each = max(1, int(adv_total / max(1, n_adversary_seats)))
        w = honest + [each] * n_adversary_seats
        flags = [False] * len(honest) + [True] * n_adversary_seats
        p = capture_probability(w, flags, m_select=m_select, k_slack=k_slack, q_low_bps=q_low_bps,
                                q_high_bps=qh, direction=direction, move_bps=move_bps, n_draws=n_draws,
                                seed=seed)
        if p >= target:
            return s
    return float("nan")


def false_dormancy_probability(uptime: float, dormancy_blocks: int, min_bundles: int, demand_rate: float,
                               selected_prob: float, *, mean_outage_blocks: float | None = None) -> float:
    """P(an honest attestor seated for the whole window is made DORMANT at one dormancy check).

    It is ejected iff it was selected in ≥ max(1, min_bundles) rows of the ``dormancy_blocks`` window
    and signed none. Rows arrive per block with probability ``1 − exp(−demand_rate)`` (BundleLog has
    one row per block), the attestor is in a row's selected set with ``selected_prob``, and it signs
    a row iff it is available (``uptime``). iid availability: with ``r = (1 − e^{−λ})·selected_prob``
    the selected rows are Binomial(dormancy_blocks, r) and the answer is
    ``Σ_{j ≥ min} C(N, j) (r(1 − u))^j (1 − r)^{N − j}``. With ``mean_outage_blocks`` the
    availability is the stationary two-state Markov chain of :func:`markov_online`, solved exactly by
    a forward recursion over the window (state × selected-rows-so-far, absorbing on a signed row)."""

    N = int(dormancy_blocks)
    mb = max(1, int(min_bundles))
    r = (1.0 - math.exp(-demand_rate)) * selected_prob
    if mean_outage_blocks is None:
        # P(no selected-and-up row, ≥ mb selected rows): each block is "selected while down" with
        # r(1−u), "selected while up" (absorbing: not dormant) r·u, else nothing.
        q = r * (1.0 - uptime)
        return _thinned_tail(N, q, r * uptime, mb)
    beta = min(1.0, 1.0 / mean_outage_blocks)
    alpha = min(1.0, beta * (1.0 - uptime) / uptime) if uptime > 0 else 1.0
    # probs[s, c]: alive (no signed selected row) with state s (0 = up, 1 = down), c selected rows (capped)
    probs = np.zeros((2, mb + 1))
    probs[0, 0], probs[1, 0] = uptime, 1.0 - uptime
    T = np.array([[1 - alpha, alpha], [beta, 1 - beta]])
    for _ in range(N):
        probs = T.T @ probs
        up, down = probs[0].copy(), probs[1].copy()
        probs[0] = up * (1 - r)                       # selected while up → signed → absorbed (dropped)
        sel = down * r
        probs[1] = down * (1 - r)
        probs[1, 1:] += sel[:-1]
        probs[1, mb] += sel[mb]
    return float(probs[:, mb].sum())


def _thinned_tail(N: int, q_down: float, q_up: float, mb: int) -> float:
    """iid blocks: each is 'selected & down' (q_down), 'selected & up' (q_up, kills), or nothing.
    P(no kill and ≥ mb downs) = Σ_{j≥mb} C(N,j) q_down^j (1 − q_down − q_up)^{N−j}."""
    from scipy.stats import binom

    rest = 1.0 - q_up
    if rest <= 0:
        return 0.0
    # (1 − q_up)^N · P(Bin(N, q_down/(1 − q_up)) ≥ mb)
    return float(rest ** N * binom.sf(mb - 1, N, q_down / rest))


def false_dormancy_per_year(p_check: float, dormancy_check: int, *,
                            blocks_per_year: int = BLOCKS_PER_YEAR) -> float:
    """Union bound on P(a false dormancy within a year) from the per-check probability (checks every
    ``dormancy_check`` blocks; consecutive checks are strongly correlated, so this is an upper bound)."""
    return float(min(1.0, p_check * blocks_per_year / max(1, dormancy_check)))


def griefing_cost_usd(bond_min: int, price_paths, *, seats: int = 1, lock_blocks: int | None = None,
                      opportunity_apr: float = 0.0, quantiles=(0.05, 0.5, 0.95)) -> dict:
    """USD cost of ``seats`` minimum bonds under YEC price paths (µUSD per YEC, ``(paths, n)`` or
    ``(n,)``): the capital at the first price, the worst capital along each path (the bond is in YEC,
    so a crash cheapens griefing), and the opportunity cost of locking it ``lock_blocks`` (default one
    year) at ``opportunity_apr``. Returns per-path arrays and their quantiles."""
    p = np.asarray(price_paths, dtype=float)
    if p.ndim == 1:
        p = p[np.newaxis, :]
    yec = seats * bond_min / COIN
    usd = np.where(p > 0, p, np.nan) * yec / MICRO_USD_PER_USD
    first = usd[:, 0]
    worst = np.nanmin(usd, axis=1)
    years = (lock_blocks if lock_blocks is not None else BLOCKS_PER_YEAR) / BLOCKS_PER_YEAR
    opp = first * opportunity_apr * years
    q = {f"q{int(x * 100)}": float(np.nanquantile(worst, x)) for x in quantiles}
    return {"capital_usd": first, "min_capital_usd": worst, "opportunity_cost_usd": opp,
            "min_capital_quantiles": q}

"""Golden-vector replay and kernel-vs-model checks on the golden chain (owner: WP-1; PLAN §6.4, §8).

``yellowback_golden.json`` (vendored) is the node's state-hash golden vector: 440 regtest blocks
covering the v2 lifecycle and the v3 attestation tail. :func:`check_golden` replays it through the
vendored reference model and checks the pinned state hash, tip and totals.
:func:`check_chain_kernels` then recomputes, at every snapshot of the replayed chain, the
quantities the model computes *inline* (medians, PRICE-2, HALT-1/2/3, ACT-1..6, REG-4, PIN-1/2,
signal counts) with :mod:`ybcal.model.kernels` and the vectorised :mod:`ybcal.model.vkernels`,
and requires equality with the model's stored snapshot — the second, node-free check of §6.4.
"""

from __future__ import annotations

import functools
import json
from importlib import resources
from typing import Any

import numpy as np

from ybcal.model import kernels as K
from ybcal.model import reference as ref
from ybcal.model import vkernels as V

OWNER_WP = "WP-1"

Check = tuple[str, bool, str]


def load_golden_doc() -> dict[str, Any]:
    """The vendored golden document (package data)."""
    return json.loads((resources.files("ybcal.model") / "yellowback_golden.json").read_text())


@functools.lru_cache(maxsize=1)
def replayed_model() -> ref.YellowbackModel:
    """The reference model after replaying the golden vector (cached; treat as read-only)."""
    return ref.replay_golden(load_golden_doc())


def check_golden(model: ref.YellowbackModel | None = None, doc: dict | None = None) -> list[Check]:
    """State hash, tip and totals of the replay against the values pinned in the document."""
    doc = doc or load_golden_doc()
    model = model or replayed_model()
    h = model.state_hash()
    return [
        ("golden state hash", h == doc["stateHash"], h),
        ("golden tip", (model.tip_height, model.tip_hash) == (doc["tip"]["height"], doc["tip"]["hash"]),
         f"{model.tip_height} {model.tip_hash}"),
        ("golden totals", model.totals.as_dict() == doc["totals"], json.dumps(model.totals.as_dict())),
    ]


def _quotes(model, lo: int, hi: int, excluded=()) -> list[int]:
    p = model.params
    return [t.price_micro_usd for h in range(max(lo + 1, p.start_height), hi + 1)
            if (t := model.tags.get(h)) is not None and t.is_quote and t.payout_key not in excluded]


def check_chain_kernels(model: ref.YellowbackModel | None = None) -> list[Check]:
    """Kernels (scalar and vectorised) recompute every stored snapshot of the replayed chain."""
    model = model or replayed_model()
    p = model.params
    heights = sorted(h for h, s in model.snapshots.items() if not s.virtual and h >= p.start_height)
    fails: dict[str, list[int]] = {}
    counted: dict[str, int] = {}

    def check(name: str, ok: bool, h: int) -> None:
        counted[name] = counted.get(name, 0) + 1
        if not ok:
            fails.setdefault(name, []).append(h)

    fills = (p.min_fill_fast, p.min_fill_mid, p.min_fill_slow)
    windows = (p.p_fast_window, p.p_mid_window, p.p_slow_window)
    act = K.ActivationState(K.SIGNALING, 0, 0)
    prev_mask = 0
    for h in heights:
        s = model.snapshots[h]
        meds = [K.window_median_with_fill(_quotes(model, h - w, h, s.pinned_keys), f)
                for w, f in zip(windows, fills, strict=True)]
        check("PRICE-1 medians", meds == [s.p_fast, s.p_mid, s.p_slow], h)
        prices2 = (K.price_mint(*meds), K.price_claim(meds[1], meds[2]))
        check("PRICE-2 pMint/pClaim", prices2 == (s.p_mint, s.p_claim), h)
        signals = [model.tags[x].signal if x in model.tags else False
                   for x in range(p.start_height, h + 1)]
        count = K.signal_count(signals, h - p.start_height, p.signal_window)
        check("ACT signal count", count == s.signal_count, h)
        act = K.activation_step(act, h, count, p.start_height, p.signal_window, p.activation_threshold,
                                p.activation_delay)
        check("ACT-1..3 activation", tuple(act) == (s.activation.status, s.activation.lock_in_height,
                                                     s.activation.activate_height), h)
        active = act.status == K.ACTIVE
        mask = 0
        if not active:
            mask |= ref.HALT_NOT_ACTIVE
        if s.p_mint is None:
            mask |= ref.HALT_NO_PRICE
        if K.halt2_global_ratio(s.collateral_zat, s.p_mint, s.supply_cents, p.global_ratio_halt_bps):
            mask |= ref.HALT_GLOBAL_RATIO
        if K.halt3_divergence(*meds, p.divergence_bps):
            mask |= ref.HALT_DIVERGENCE
        if K.participation_halt_step(bool(prev_mask & ref.HALT_PARTICIPATION), count, active,
                                     p.activation_threshold, p.participation_floor):
            mask |= ref.HALT_PARTICIPATION
        if K.enforcement_halt_step(bool(prev_mask & ref.HALT_ENFORCEMENT), count, active,
                                   p.enforcement_resume, p.enforcement_floor):
            mask |= ref.HALT_ENFORCEMENT
        check("HALT-1..4 / ACT-4 / ACT-6 mask", mask == s.halt_mask, h)
        ratio = K.global_ratio_bps(s.collateral_zat, s.p_mint, s.supply_cents)
        check("HALT-2 global ratio", ratio == s.global_ratio_bps, h)
        prev_mask = s.halt_mask
        # PIN-1 / PIN-2 (window (H-1-pinWindow, H-1])
        lo = max(h - p.pin_window, p.start_height)
        rows = [model.bundle_log[x] for x in range(lo, h) if x in model.bundle_log]
        keys: list = []
        if K.pin1_triggered([r.a_mint for r in rows], p.pin_min_bundles, p.pin_delta_bps):
            quotes = [(t.payout_key, t.price_micro_usd) for x in range(lo, h)
                      if (t := model.tags.get(x)) is not None and t.is_quote]
            keys = K.pin1_pinned_keys(quotes, p.pin_min_tags)
        check("PIN-1 pinned keys", keys == list(s.pinned_keys), h)
        s1, s0 = model.snapshot(h - 1), model.snapshot(h - 1 - p.pin_window)
        x1 = None if s1 is None or s1.virtual else s1.p_mint
        x0 = None if s0 is None or s0.virtual else s0.p_mint
        seqs: list[int] = []
        if K.pin2_triggered(x1, x0, p.pin_delta_bps):
            seqs = K.pin2_pinned_seqs([(r.seqs, r.prices, r.cited_heights) for r in rows], p.pin_min_tags)
        check("PIN-2 pinned seqs", seqs == list(s.pinned_seqs), h)
        # REG-4 judgement of the tag at t = h - peerLag
        t = h - p.peer_lag
        tag = model.tags.get(t)
        if t >= p.start_height and tag is not None and tag.is_quote:
            peers = [q.price_micro_usd for x in K.reg4_peer_heights(t, p.peer_lag) if x != t
                     and (q := model.tags.get(x)) is not None and q.is_quote]
            j = K.reg4_judgement(tag.price_micro_usd, peers, p.peer_min, p.deviation_bps, p.accuracy_band_bps)
            mj = model.judgements[t]
            check("REG-4 judgement", tuple(j) == (mj.evaluated, mj.in_band, mj.penalized), h)

    # vectorised: the three medians over the tag series (heights with pinned keys excluded from
    # the comparison: PIN-1 removes keys per height, which the static-mask vkernel does not model)
    n = heights[-1] - p.start_height + 1
    series = np.full(n, V.UNDEF, dtype=np.int64)
    for hh, tg in model.tags.items():
        if hh >= p.start_height and tg.is_quote:
            series[hh - p.start_height] = tg.price_micro_usd
    vm = V.price_medians(series, windows, fills)
    pmint = V.price_mint(*vm)
    halt3 = V.halt3_divergence(*vm, p.divergence_bps)
    for h in heights:
        s = model.snapshots[h]
        if s.pinned_keys:
            continue
        i = h - p.start_height
        got = [None if v[i] <= 0 else int(v[i]) for v in vm]
        check("vkernel medians", got == [s.p_fast, s.p_mid, s.p_slow], h)
        check("vkernel pMint", (None if pmint[i] <= 0 else int(pmint[i])) == s.p_mint, h)
        check("vkernel HALT-3", bool(halt3[i]) == bool(s.halt_mask & ref.HALT_DIVERGENCE), h)
    # σ at a mainnet-like reference (the golden chain runs with sigmaRefBps 0): vkernel == kernel
    pf = [model.snapshots[h].p_fast for h in heights]
    vs = V.sigma_mult_series(V.to_array(pf), p.vol_window, p.vol_step, 10_000, p.vol_periods_per_year,
                             p.sigma_mult_max_bps)
    for i, h in enumerate(heights):
        want = K.sigma_mult_bps(K.sigma_samples(pf, i, p.vol_window, p.vol_step), 10_000,
                                p.vol_periods_per_year, p.sigma_mult_max_bps)
        check("vkernel sigma series", int(vs[i]) == want, h)
        check("SIGMA-1 stored (ref 0)", model.snapshots[h].sigma_mult_bps
              == K.sigma_mult_bps(K.sigma_samples(pf, i, p.vol_window, p.vol_step), p.sigma_ref_bps,
                                  p.vol_periods_per_year, p.sigma_mult_max_bps), h)
    return [(f"chain: {name}", name not in fails,
             f"{counted[name]} heights" + (f"; first mismatches {fails[name][:5]}" if name in fails else ""))
            for name in counted]

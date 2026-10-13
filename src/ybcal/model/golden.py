"""Golden-vector replay and kernel-vs-model checks on the golden chain (owner: WP-1; PLAN §6.4, §8).

``yellowback_golden.json`` (vendored) is the node's state-hash golden vector (``SCHEMA_VERSION`` 13): 440
regtest heights, 457 block entries — the vault upgrade's lifecycle (mint into the YED V, owner redeem,
in-term claim into intents, release and CAN-1 cancel), the attestor set's acts and the v3 attestation
tail; seventeen entries are ``"invalid"`` (one per consensus verdict, audit 03/T-1) and are rejected,
not applied. :func:`check_golden` replays it through the vendored reference model and checks the
pinned state hash, tip and totals. :func:`check_chain_kernels` then recomputes, at every snapshot of
the replayed chain, the quantities the model computes *inline* (medians, PRICE-2, HALT-1..3, the
D-Q-25 issuance, REG-4, PIN-1/2) with :mod:`ybcal.model.kernels` and the vectorised
:mod:`ybcal.model.vkernels`, and, for every Yellowback transaction, the money rules (MINT-4/5/6/8′,
AFEE-1′, RED-3′/IT-9′, RED-4/5/5′, CAN-1, IT-6) — the second, node-free check of §6.4. Since the
vault upgrade there is no ACT-1..6 state machine to check (U-21; :mod:`ybcal.model.retired`).
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


def load_golden_doc_v3() -> dict[str, Any]:
    """RETIRED: the v3 golden vector (ycash6 ``7702d22``, SCHEMA_VERSION 4) the simulator's v3 books are
    checked against (:mod:`ybcal.model.reference_v3`; see ``vendor.V3_SPECS``)."""
    return json.loads((resources.files("ybcal.model") / "yellowback_golden_v3.json").read_text())


@functools.lru_cache(maxsize=1)
def replayed_model_v3():
    """RETIRED: the v3 reference model after replaying the v3 golden vector (cached; read-only)."""
    from ybcal.model import reference_v3

    return reference_v3.replay_golden(load_golden_doc_v3())


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


def _golden_txs(doc: dict[str, Any]) -> dict[str, tuple[int, Any]]:
    """txid → (height, Tx) over the blocks the replay applied (an ``"invalid"`` entry is rejected and
    mined again at the same height without the offending transaction, audit 03/T-1)."""
    out: dict[str, tuple[int, Any]] = {}
    for b in doc["blocks"]:
        if b.get("invalid"):
            continue
        for h in b["txs"]:
            tx = ref.tx_from_hex(h)
            out[tx.txid] = (int(b["height"]), tx)
    return out


def check_chain_kernels(model: ref.YellowbackModel | None = None, doc: dict | None = None) -> list[Check]:
    """Kernels (scalar and vectorised) recompute every stored snapshot of the replayed chain, and the
    vault-level rules (MINT-4/5/8′, AFEE-1′, RED-3′/IT-9′, RED-4/5/5′, CAN-1, IT-6) recompute what the
    model recorded for every Yellowback transaction of the golden vector."""
    doc = doc or load_golden_doc()
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
    subsidy = {int(b["height"]): int(b["subsidyZat"]) for b in doc["blocks"] if not b.get("invalid")}
    issued = 0
    for h in heights:
        s = model.snapshots[h]
        meds = [K.window_median_with_fill(_quotes(model, h - w, h, s.pinned_keys), f)
                for w, f in zip(windows, fills, strict=True)]
        check("PRICE-1 medians", meds == [s.p_fast, s.p_mid, s.p_slow], h)
        prices2 = (K.price_mint(*meds), K.price_claim(meds[1], meds[2]))
        check("PRICE-2 pMint/pClaim", prices2 == (s.p_mint, s.p_claim), h)
        # U-21: no activation state machine, no signal count, no PARTICIPATION / ENFORCEMENT bit
        mask = K.halt_mask(*meds, s.collateral_zat, s.supply_cents, p.global_ratio_halt_bps, p.divergence_bps)
        check("HALT-1..3 mask (U-21: no ACT bits)", mask == s.halt_mask, h)
        ratio = K.global_ratio_bps(s.collateral_zat, s.p_mint, s.supply_cents)
        check("HALT-2 global ratio", ratio == s.global_ratio_bps, h)
        issued += subsidy[h]                                   # D-Q-25: the golden's issuedBeforeStart is 0
        check("MINT-6 issuedZat (D-Q-25)", s.issued_zat == issued, h)
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

    _check_vault_rules(model, doc, check)

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
    # σ at the v2 reference and cap (math_tests SIGMA_REF_V2 / SIGMA_MAX_V2; the live set pins σ at 1,
    # D-IT-5, and the golden chain runs with sigmaRefBps 0): vkernel == kernel
    pf = [model.snapshots[h].p_fast for h in heights]
    vs = V.sigma_mult_series(V.to_array(pf), p.vol_window, p.vol_step, 10_000, p.vol_periods_per_year, 30_000)
    for i, h in enumerate(heights):
        want = K.sigma_mult_bps(K.sigma_samples(pf, i, p.vol_window, p.vol_step), 10_000,
                                p.vol_periods_per_year, 30_000)
        check("vkernel sigma series", int(vs[i]) == want, h)
        check("SIGMA-1 stored (ref 0)", model.snapshots[h].sigma_mult_bps
              == K.sigma_mult_bps(K.sigma_samples(pf, i, p.vol_window, p.vol_step), p.sigma_ref_bps,
                                  p.vol_periods_per_year, p.sigma_mult_max_bps), h)
    return [(f"chain: {name}", name not in fails,
             f"{counted[name]} heights" + (f"; first mismatches {fails[name][:5]}" if name in fails else ""))
            for name in counted]


def _check_vault_rules(model: ref.YellowbackModel, doc: dict[str, Any], check) -> None:
    """The per-transaction money rules of the golden chain, recomputed with the kernels from the
    transactions themselves (payload, outputs) and the model's snapshots, against what the model
    recorded (TxLog, vault records, totals)."""
    p = model.params
    txs = _golden_txs(doc)
    bands, floor = p.fee_bands, p.fee_floor_cents
    # where each vault outpoint went (a cancel re-creates the vault under the cancel's outpoint, U-24)
    moved = {old: new for r in model.txlog.values() for old, new in zip(r.closed_vaults, r.reopened_vaults,
                                                                         strict=False)}

    def current(op):
        while op in moved:
            op = moved[op]
        return model.vaults.get(op)

    def payload(tx):
        return ref.tx_payload([o.script for o in tx.vout])[0]

    claims: dict[tuple[str, int], tuple[str, Any]] = {}       # vault outpoint -> (claim txid, TxLog record)
    for txid, rec in sorted(model.txlog.items(), key=lambda kv: kv[1].height):
        h, tx = txs[txid]
        if rec.verdict != ref.VERDICT_OK:
            continue
        if rec.type == "MINT":
            pl = payload(tx)
            v = current((txid, 0))
            s = model.snapshot(pl.ref_height)
            pm = s.p_mint
            if rec.a_mint is not None:
                pm = K.price_combine(s.p_mint, s.p_claim, rec.a_mint, rec.a_claim).p_mint
            check("PRICE-2 the vault's pMint (FEE-1')", v is not None and v.p_mint == pm, h)
            check("IT-1 appHeight = ownerHeight = refHeight + 1",
                  v is not None and v.app_height == v.owner_height == pl.ref_height + 1, h)
            base = p.base_ratio_bps[pl.term_class]
            mint4 = K.mint4_halt_verdict(s.halt_mask, base, p.recap_ratio_bps)
            check("MINT-4 halt verdict", mint4 is None, h)
            req = K.required_zat(pl.cents, K.min_ratio_bps(base, s.sigma_mult_bps), pm)
            check("MINT-5' collateral", req is not None and tx.vout[0].value >= req, h)
            cap = K.supply_cap_cents(s.issued_zat, s.p_mint, p.supply_cap_bps)
            mv = K.max_vault_cents(cap, p.max_vault_share_bps, p.vault_share_floor_cents)
            refused = K.mint6_cap_refuses(s.supply_cents, pl.cents, cap, base, p.recap_ratio_bps)
            check("MINT-6 / MINT-6b", not refused and (mv is None or pl.cents <= mv), h)
            if rec.payee is not None:
                fee = K.protocol_fee_zat(pl.cents, pm, bands, floor)
                check("MINT-8' protocol fee", fee is not None and rec.fee_zat >= fee, h)
                if rec.attest_payee is not None:
                    afee = K.attest_fee_zat(fee, p.attest_fee_bps)
                    check("AFEE-1' attestor fee", rec.attest_fee_zat >= afee, h)
        elif rec.type == "REDEEM":
            pl = payload(tx)
            op = rec.closed_vaults[0]
            v = current(op)
            _, prev = txs[op[0]]
            collateral = prev.vout[op[1]].value
            owner = rec.path == "owner"
            if rec.payee is not None:
                fee = K.redeem_fee_zat(v.minted_cents, v.p_mint, owner_path=owner, height=h,
                                       lock_height=v.lock_height,
                                       early_redeem_fee_bps=p.early_redeem_fee_bps[v.term_class],
                                       fee_bands=bands, fee_floor_cents=floor)
                check("RED-3' / IT-9' fee at the vault's pMint", fee is not None and rec.fee_zat >= fee, h)
            if owner:
                continue
            s = model.snapshot(pl.ref_height)
            pc = K.CombinedPrices(None, s.p_claim, None)
            if rec.a_claim is not None:
                pc = K.price_combine(s.p_mint, s.p_claim, rec.a_mint, rec.a_claim)
            path = K.claim_path(collateral, pc.p_claim, pc.p_emerg, v.minted_cents, p.claim_threshold_bps,
                                p.emergency_ratio_bps, notice_persisted=rec.claim_path == "b")
            check("RED-4 claim path (IT-2: no height clause)", path == rec.claim_path, h)
            threshold = K.residual_min_zat(v.p_mint, p.residual_min_zat, p.residual_min_cents)
            split = K.claim_split(collateral, v.minted_cents, pc.p_claim, rec.claim_path,
                                  p.claim_threshold_bps, threshold)
            n_intents = sum(1 for o in tx.vout if ref.parse_intent_template(o.script) is not None)
            check("RED-5 / RED-5' residual", split.residual_zat == rec.residual_zat
                  and n_intents == (2 if split.residual_due else 1), h)
            check("IT-2 claim admitted from appHeight", h >= v.app_height, h)
            check("IN-3 claim burns the debt", rec.burned >= v.minted_cents
                  and K.claim_accounting("claim", burned_cents=rec.burned).supply_cents == -rec.burned, h)
            claims[op] = (txid, rec)
        elif rec.type == "CLAIM_CANCEL" and rec.reopened_vaults:
            op = rec.closed_vaults[0]
            _claim_txid, claim = claims[op]
            tok_txid, tok_n = claim.spent_tokens[0]                      # CAN-1: the lowest-index YED input
            refund_script = txs[tok_txid][1].vout[tok_n].script
            prev_coll = txs[op[0]][1].vout[op[1]].value
            d = K.claim_accounting("cancel", burned_cents=claim.burned, claiming_collateral_zat=prev_coll,
                                   relock_zat=tx.vout[0].value)
            check("CAN-1 cancel refunds the whole burn", rec.yed_out == d.supply_cents == claim.burned
                  and len(rec.assigned) == 1 and tx.vout[rec.assigned[0][0]].script == refund_script, h)
            v = current(rec.reopened_vaults[0])
            check("CAN-1 / U-24 the vault re-opens ACTIVE", v is not None and v.status == ref.V_ACTIVE
                  and v.collateral_zat == tx.vout[0].value and v.burned_cents == 0, h)
    # IT-6: Totals.collateralZat counts ACTIVE and CLAIMING vaults; supply is the live tokens
    t = model.totals
    live = sum(v.collateral_zat for v in model.vaults.values() if v.status in (ref.V_ACTIVE, ref.V_CLAIMING))
    check("IT-6 collateral accounting", t.collateral_zat == live, model.tip_height)
    live_tokens = sum(k.cents for k in model.tokens.values())
    check("supply = live tokens", t.supply_cents == live_tokens, model.tip_height)
    for ir in model.intents.values():
        check("CAN-1 refundScript on the claimant intent only",
              bool(ir.refund_script) == (ir.role == ref.I_CLAIMANT), model.tip_height)

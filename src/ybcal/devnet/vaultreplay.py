"""Vault differential: replay the node's vault transactions through the simulator's rule layer (M6).

Owner: WP-9 (devnet validation), on WP-4's rule layer (:mod:`ybcal.sim.vaults`).

The price/halt differential (:mod:`ybcal.devnet.diff`) compares what the oracle pipeline computes.
This module extends it to the vault book. A devnet run with wallet actions
(:mod:`ybcal.devnet.actions`) records each vault the node created (``yed_listvaults``) and the
transactions that closed them (the driver's events). Those *transactions* — mint height, refHeight,
amount, lock, the collateral the wallet chose, the spend's path and refHeight — are the inputs; the
simulator re-derives everything the rules decide from them:

* each MINT's verdict (ACTIVE or VOID + reason) via :func:`~ybcal.sim.vaults.mint_verdict` at
  ``Snapshots[R]`` taken from the simulator's own series (HALT-2 from the replayed totals at R);
* each spend's verdict via :func:`~ybcal.sim.vaults.red_verdict`, hence status, closeHeight, burn;
* per-height ``supplyCents`` / ``collateralZat`` (written into the series, so the engine's
  ``globalRatioBps`` and HALT-2 bit are then compared by the per-height diff as well);
* per-height, per-vault ``claimable`` exactly as ``yed_listvaults`` reports it unarmed
  (``EstimateClaim``: tip ≥ claimHeight and RED-4 (a) underwater at the tip's pClaim);
* the wallet's collateral (:func:`~ybcal.sim.vaults.wallet_collateral_int` at R) against the
  collateral the node's wallet put in ``vout[0]``;
* each mint the node's wallet refused: the simulator's verdict at the same R must not be ``ok``.

Armed runs take each transaction's bundle from the simulator's own attestation replay
(:mod:`ybcal.devnet.attestreplay`); the per-height claimable test stays the unarmed clause (a) one,
so it is compared only where the snapshot is unarmed.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ybcal.model import kernels as K
from ybcal.sim import vaults as VB

OWNER_WP = "WP-9"

#: Vault fields compared (key ``vault``).
VAULT_FIELDS: tuple[str, ...] = (
    "status",
    "termClass",
    "lockHeight",
    "claimHeight",
    "collateralZat",
    "mintedCents",
    "mintHeight",
    "refHeight",
    "feePaidZat",
    "closeHeight",
    "burnedCents",
    "unbacked",
    "voidReason",
)
_CLASS = {"A": 0, "B": 1, "C": 2}
_LETTER = {v: k for k, v in _CLASS.items()}


@dataclass
class NodeVaultRun:
    """What a devnet run recorded about vaults (``scrape/vaults.json`` + ``scrape/actions.json``)."""

    vaults: list[dict[str, Any]]
    events: list[dict[str, Any]]
    vault_blocks: list[dict[str, Any]]

    @classmethod
    def from_docs(cls, vaults: Sequence[Mapping[str, Any]], actions: Mapping[str, Any]) -> NodeVaultRun:
        return cls(
            [dict(v) for v in vaults],
            [dict(e) for e in actions.get("events", [])],
            [dict(b) for b in actions.get("vault_blocks", [])],
        )

    def spends(self) -> dict[str, dict[str, Any]]:
        """vault txid → {path, ref, txid} of the spend that closed it, from the driver's events.

        A redemption is one transaction whose R is the tip when it was built (the driver event's
        height); a claim is two-step and its R is the preflight's ``refHeight``.
        """
        out: dict[str, dict[str, Any]] = {}
        labels = {e.get("label"): e.get("txid") for e in self.events if e.get("op") == "mint-complete"}
        claim_ref: dict[str, int] = {}
        for e in self.events:
            res = e.get("result") or {}
            if e.get("op") == "redeem" and "error" not in e:
                txid = labels.get(e.get("vault"), e.get("vault"))
                out[str(txid)] = {"path": "owner", "ref": int(e["height"]), "txid": res.get("txid")}
            op = e.get("op")
            started = [res] if op == "claim" else e.get("results", []) if op == "claim_all" else []
            for r in started:
                if isinstance(r, Mapping) and r.get("carrierTxid"):
                    claim_ref[str(r["carrierTxid"])] = int(r["refHeight"])
        for e in self.events:
            if e.get("op") == "claim-complete" and e.get("vault") and e.get("txid"):
                ref = claim_ref.get(str(e.get("carrier")))
                out[str(e["vault"])] = {"path": "claim", "ref": ref, "txid": e.get("txid")}
        return out


@dataclass
class VaultReplayResult:
    """The simulator's view of the node's vaults."""

    vaults: list[dict[str, Any]]  #: predicted rows, keyed ``vault``
    claimable: list[dict[str, Any]]  #: {height, vault, status, claimable}
    collateral: list[dict[str, Any]]  #: {vault, node, sim} wallet collateral
    refusals: list[dict[str, Any]]  #: {height, node_error, sim_verdict}
    armed_heights: set[int] = field(default_factory=set)  #: heights whose claimable test is not replayed
    supply_cents: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))
    collateral_zat: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))


class VaultReplay:
    """WP-3 ``vaults`` hook replaying a :class:`NodeVaultRun` (block mode, one path)."""

    def __init__(self, run: NodeVaultRun) -> None:
        self.run = run
        self.result: VaultReplayResult | None = None

    def __call__(self, stage: str, params: Mapping, inputs: Any, series: Any) -> None:
        if stage != "vaults":
            return
        res = replay_vaults(params, inputs, series, self.run)
        series.supply_cents[0] = res.supply_cents
        series.collateral_zat[0] = res.collateral_zat
        series.extras["vault_replay"] = res
        self.result = res


def _snap(tl: VB.Timeline, j: int, supply: int, coll: int, rp: VB.RuleParams) -> VB.Snap:
    halt = int(tl.base_halt[j]) & ~VB.HALT_GLOBAL_RATIO
    xm = int(tl.x_mint[j])
    if xm > 0 and supply > 0:
        gr = K.global_ratio_bps(coll, xm, supply)
        if gr is not None and gr < rp.global_ratio_halt_bps:
            halt |= VB.HALT_GLOBAL_RATIO
    opt = lambda v: int(v) if int(v) > 0 else None  # noqa: E731
    return VB.Snap(
        height=int(tl.heights[j]),
        active=bool(tl.active[j]),
        halt_mask=halt,
        x_mint=opt(xm),
        x_claim=opt(tl.x_claim[j]),
        p_fast=opt(tl.p_fast[j]),
        sigma_mult_bps=int(tl.sigma_mult_bps[j]),
        issued_zat=int(tl.issued_zat[j]),
        armed=bool(tl.armed[j]),
        eligible=bool(tl.eligible[j]),
    )


def replay_vaults(params: Mapping, inputs: Any, series: Any, run: NodeVaultRun) -> VaultReplayResult:
    """Replay ``run``'s transactions in height order (see the module docstring)."""
    tl = VB.timeline_from_blocks(params, inputs, series, 0)
    rp = VB.RuleParams.of(params)
    start, n = int(series.start_height), int(series.n_blocks)
    book = VB.VaultBook(rp)
    supply = np.zeros(n, np.int64)
    coll = np.zeros(n, np.int64)

    def j_of(h: int) -> int:
        j = int(h) - start
        if not 0 <= j < n:
            raise ValueError(f"height {h} outside the simulated range {start}..{start + n - 1}")
        return j

    def snap_at(h: int) -> VB.Snap:
        j = j_of(h)
        return _snap(tl, j, int(supply[j]), int(coll[j]), rp)

    att = getattr(series, "attest", None)

    def bundle(h: int, ref: int, kind: int) -> VB.Bundle | None:
        if att is None:
            return VB.NO_BUNDLE
        from ybcal.devnet.attestreplay import armed_bundle

        return armed_bundle(att, h, ref, kind)

    spends = run.spends()
    mints_at: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for v in run.vaults:
        mints_at[int(v["mintHeight"])].append(v)
    spends_at: dict[int, list[str]] = defaultdict(list)
    for v in run.vaults:
        if v.get("closeHeight") is not None and v["txid"] in spends:
            spends_at[int(v["closeHeight"])].append(v["vault"])
    vid_of: dict[str, int] = {}
    meta: dict[str, dict[str, Any]] = {}
    collateral_rows: list[dict[str, Any]] = []
    claim_rows: list[dict[str, Any]] = []
    armed_heights: set[int] = set()
    for j in range(n):
        H = start + j
        for v in sorted(mints_at.get(H, []), key=lambda r: r["vault"]):
            cls = _CLASS[str(v["termClass"])]
            R = int(v["refHeight"])
            s = snap_at(R)
            tx = VB.MintTx(
                term_class=cls,
                cents=int(v["mintedCents"]),
                lock_height=int(v["lockHeight"]),
                ref_height=R,
                collateral_zat=int(v["collateralZat"]),
                fee_zat=rp.fee(int(v["collateralZat"])) if s.eligible else None,
                bundle=bundle(H, R, 0) if s.armed else None,
                attest_fee_zat=rp.attest_fee(int(v["collateralZat"])) if s.armed else None,
            )
            vid, _verdict = book.mint(H, tx, s)
            vid_of[v["vault"]] = vid
            meta[v["vault"]] = {"vault": v["vault"], "termClass": _LETTER[cls]}
            p_mint = s.x_mint or 0
            if s.armed and tx.bundle is not None and tx.bundle.ok and tx.bundle.a_mint:
                p_mint = min(p_mint, int(tx.bundle.a_mint))  # MINT-5 at min(xMint, aMint) when ARMED
            want = VB.wallet_collateral_int(rp, tx.cents, cls, s.sigma_mult_bps, p_mint)
            collateral_rows.append({"vault": v["vault"], "node": tx.collateral_zat, "sim": want})
        for key in spends_at.get(H, []):
            v = next(x for x in run.vaults if x["vault"] == key)
            sp = spends[v["txid"]]
            if sp.get("ref") is None:
                continue
            rec = book.vaults[vid_of[key]]
            ref = int(sp["ref"])
            armed = sp["path"] == "claim" and snap_at(ref).armed
            tx = VB.SpendTx(
                path=sp["path"],
                ref_height=ref,
                yed_in=int(v["burnedCents"]),
                fee_zat=rp.fee(rec.collateral_zat),
                owner_paid_zat=rec.collateral_zat,  # the wallet pays the RED-5 residual in full
                bundle=bundle(H, ref, 2) if armed else None,
                attest_fee_zat=rp.attest_fee(rec.collateral_zat) if armed else None,
            )
            book.spend(H, vid_of[key], tx, snap_at(int(sp["ref"])), enforcing=bool(tl.enforcing[j]))
        supply[j] = book.totals.supply_cents
        coll[j] = book.totals.collateral_zat
        xc = int(tl.x_claim[j])
        if tl.armed[j]:  # EstimateClaim under ARMED builds a bundle per vault: not replayed here
            armed_heights.add(H)
            continue
        for key, vid in vid_of.items():
            rec = book.vaults[vid]
            status = VB.STATUS_NAMES[rec.status]
            claimable = (
                rec.status == VB.ACTIVE
                and rec.claim_height <= H
                and K.is_underwater(
                    rec.collateral_zat, xc if xc > 0 else None, rec.minted_cents, rp.claim_threshold_bps
                )
            )
            claim_rows.append({"height": H, "vault": key, "status": status, "claimable": bool(claimable)})
    rows = []
    for key, vid in vid_of.items():
        r = book.vaults[vid]
        rows.append(
            {
                **meta[key],
                "status": VB.STATUS_NAMES[r.status],
                "lockHeight": r.lock_height,
                "claimHeight": r.claim_height,
                "collateralZat": r.collateral_zat,
                "mintedCents": r.minted_cents,
                "mintHeight": r.mint_height,
                "refHeight": r.ref_height,
                "feePaidZat": r.fee_paid_zat,
                "closeHeight": r.close_height or None,
                "burnedCents": r.burned_cents,
                "unbacked": int(bool(r.unbacked)),
                "voidReason": r.void_reason,
            }
        )
    refusals = []
    for e in run.events:
        if e.get("op") != "mint" or "error" not in e:
            continue
        tip = int(e["height"])
        R = tip - int(rp.default_ref_lag)
        try:
            s = snap_at(R)
        except ValueError as err:
            refusals.append({"height": tip, "node_error": e["error"], "sim_verdict": f"n/a: {err}"})
            continue
        cls = K.class_for_lock_blocks(int(e["lock"]), rp.class_min, rp.class_max)
        c = -1
        if cls >= 0:
            c = VB.wallet_collateral_int(rp, int(e["cents"]), cls, s.sigma_mult_bps, s.x_mint or 0)
        tx = VB.MintTx(max(cls, 0), int(e["cents"]), R + int(e["lock"]), R, max(c, 0), rp.fee(max(c, 0)))
        verdict = VB.mint_verdict(rp, tip + 1, tx, s, int(supply[j_of(tip)]))
        refusals.append({"height": tip, "node_error": e["error"], "sim_verdict": verdict})
    return VaultReplayResult(rows, claim_rows, collateral_rows, refusals, armed_heights, supply, coll)

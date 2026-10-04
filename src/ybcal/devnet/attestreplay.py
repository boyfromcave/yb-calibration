"""Attestation differential: replay a devnet run's seats, signatures and bundle demands (M6).

Owner: WP-9 (devnet validation), on WP-5's replay inputs (:mod:`ybcal.sim.attest`).

A run with attestor seats (:class:`ybcal.devnet.actions.Seat`) records the registrations
(``yed_listattestors``: seq, registerHeight, bond), every signature the emulated agents made
(seq, cited height, price), each block hash, and every transaction that carried a bundle (a MINT at
``mintHeight`` with its ``refHeight``; a claim with its vault outpoint as selector). Those are WP-5's
explicit replay inputs (``sign_heights`` / ``sign_prices``, ``demands``, ``block_hashes``), so the
simulator re-derives on its own: maturity, ARM-1/2, seating, selection, which bundles are sufficient
and their aMint/aClaim, BundleLog, PIN-1/PIN-2, dormancy. :func:`compare_attest` then checks, per
height, the layer status (UNARMED/TRIGGERED/ARMED), each seq's status and ``pinned`` flag, and per
MINT the bundle statistic and seqs the node recorded (``yed_gettxinfo``).

Limits (D-RD-DEV-4): the emulated agent never sends REV-1 (``revive`` is off in the roster), never
equivocates and never withdraws; a mint the wallet refused for want of a bundle makes no demand (no
transaction exists), so refusals are compared only through the simulator's verdict.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from ybcal.model import kernels as K
from ybcal.sim import attest as AT

OWNER_WP = "WP-9"

STATUS_NAMES = {AT.PENDING: "PENDING", AT.ELIGIBLE: "ELIGIBLE", AT.DORMANT: "DORMANT",
                AT.EJECTED: "EJECTED", AT.WITHDRAWN: "WITHDRAWN", AT.NOT_REGISTERED: ""}
LAYER_NAMES = {AT.UNARMED: "UNARMED", AT.TRIGGERED: "TRIGGERED", AT.ARMED: "ARMED"}


def attest_inputs(
    actions: Mapping[str, Any],
    attestors: Sequence[Mapping[str, Any]],
    vaults: Sequence[Mapping[str, Any]],
    history: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """WP-5's ``inputs.attest`` for a run with seats (``None`` when the run had none)."""
    seats = actions.get("seats") or []
    if not seats:
        return None
    by_seq = {int(a["seq"]): a for a in attestors}
    sigs: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for s in actions.get("signatures", []):
        sigs[int(s["seq"])].append((int(s["cited"]), int(s["price"])))
    roster = []
    for seq in sorted(by_seq):  # seq order = registration order (ties keep the roster index order)
        a = by_seq[seq]
        sh = sorted(sigs.get(seq, []))
        roster.append(
            {
                "bond_zat": int(a["bondZat"]),
                "register_height": int(a["registerHeight"]),
                "sign_heights": [c for c, _ in sh],
                "sign_prices": [p for _, p in sh],
                "revive": False,
                "common": False,
                "phase": 0,
            }
        )
    demands = [
        {"height": int(v["mintHeight"]), "ref_height": int(v["refHeight"]), "kind": "mint"} for v in vaults
    ]
    from ybcal.devnet.vaultreplay import NodeVaultRun

    spends = NodeVaultRun.from_docs(vaults, actions).spends()
    for v in vaults:
        sp = spends.get(str(v["txid"]))
        if sp and sp["path"] == "claim" and sp.get("ref") is not None and v.get("closeHeight") is not None:
            demands.append(
                {
                    "height": int(v["closeHeight"]),
                    "ref_height": int(sp["ref"]),
                    "kind": "claim",
                    "selector": K.outpoint_selector(str(v["txid"]), int(v.get("vout", 0))),
                }
            )
    return {
        "roster": roster,
        "demands": demands,
        "block_hashes": {int(r["height"]): str(r["blockHash"]) for r in history if r.get("blockHash")},
        "record_status": True,
        "seed": 0,
    }


def compare_attest(
    actions: Mapping[str, Any], attest_series: Any, mint_info: Mapping[str, Mapping[str, Any]],
    vaults: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Mismatches (empty lists = pass) of the layer status, seq status / pinned, and MINT bundles."""
    out: dict[str, list[dict[str, Any]]] = {"layer": [], "seats": [], "bundles": [], "compared": []}
    s = attest_series
    h0 = int(s.height0)
    n = int(s.n_blocks)
    for b in actions.get("attest_blocks", []):
        j = int(b["height"]) - h0
        if not 0 <= j < n:
            continue
        sim = LAYER_NAMES[int(s.status[0, j])]
        if sim != b["status"]:
            out["layer"].append({"height": b["height"], "node": b["status"], "sim": sim})
    st = s.attestor_status
    for b in actions.get("attestor_blocks", []):
        j = int(b["height"]) - h0
        seq = int(b["seq"])
        if not 0 <= j < n or st is None or seq >= st.shape[1]:
            continue
        sim_status = STATUS_NAMES[int(st[0, seq, j])]
        sim_pinned = bool(int(s.pinned_seqs[0, j]) >> seq & 1)
        if sim_status != b["status"] or sim_pinned != bool(b["pinned"]):
            out["seats"].append(
                {"height": b["height"], "seq": seq, "node": (b["status"], bool(b["pinned"])),
                 "sim": (sim_status, sim_pinned)}
            )
    d = s.demands
    for v in vaults:
        info = mint_info.get(v["vault"]) or {}
        h, r = int(v["mintHeight"]), int(v["refHeight"])
        m = d[(d["height"] == h) & (d["ref_height"] == r) & (d["kind"] == AT.KIND_MINT)]
        if not len(m):
            out["bundles"].append({"vault": v["vault"], "node": info.get("aMint"), "sim": "no demand"})
            continue
        row = m[0]
        sim_a = int(row["a_mint"]) if row["success"] and int(row["a_mint"]) > 0 else None
        node_a = info.get("aMint")
        node_seqs = sorted(int(x) for x in info.get("bundleSeqs") or [])
        sim_seqs = [i for i in range(64) if int(row["signed"]) >> i & 1] if row["success"] else []
        out["compared"].append({"vault": v["vault"], "aMint": node_a, "seqs": node_seqs})
        if node_a != sim_a or node_seqs != sim_seqs:
            out["bundles"].append(
                {"vault": v["vault"], "node": (node_a, node_seqs), "sim": (sim_a, sim_seqs),
                 "reason": AT.REASON_NAMES[int(row["reason"])]}
            )
    return out


def armed_bundle(attest_series: Any, height: int, ref: int, kind: int = AT.KIND_MINT) -> Any:
    """The :class:`~ybcal.sim.vaults.Bundle` the simulator built for the transaction at ``height``."""
    from ybcal.sim import vaults as VB

    d = attest_series.demands
    m = d[(d["height"] == height) & (d["ref_height"] == ref) & (d["kind"] == kind)]
    if not len(m):
        return VB.NO_BUNDLE
    row = m[0]
    if not row["success"]:
        return VB.Bundle(None, None, ok=False, reason=AT.REASON_NAMES[int(row["reason"])])
    opt = lambda x: int(x) if int(x) > 0 else None  # noqa: E731
    return VB.Bundle(opt(row["a_mint"]), opt(row["a_claim"]))


__all__ = ["armed_bundle", "attest_inputs", "compare_attest"]

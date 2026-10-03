"""Build reference-model transactions and states from the vault book's abstractions (WP-4 tests).

The vendored ``YellowbackModel`` reads real transaction shapes (vault script, payload, fee outputs);
these helpers turn a :class:`~ybcal.sim.vaults.MintTx` / :class:`~ybcal.sim.vaults.SpendTx` into
that shape so the two can be compared verdict for verdict.
"""

from __future__ import annotations

import hashlib

from ybcal.model import reference as R
from ybcal.model import reference_attest as RA
from ybcal.model import reference_util as RU
from ybcal.sim import vaults as VB

PAYEE = bytes([0xA1]) * 20  #: the E(R) pool key
OTHER = bytes([0xB2]) * 20  #: a key outside E(R)
CLAIMANT = bytes([0xC3]) * 20
ATTESTOR_KEY = bytes([0xD4]) * 20


def owner_pubkey(i: int) -> bytes:
    return RU.secret_to_pubkey((i + 1).to_bytes(32, "big"))


def txid(tag: str, i: int) -> str:
    return hashlib.sha256(f"{tag}:{i}".encode()).hexdigest()


def opret(data: bytes) -> bytes:
    return bytes([R.OP_RETURN]) + R.push(data)


def mint_tx(tx: VB.MintTx, grace: int, owner: bytes, tid: str) -> tuple[R.Tx, R.Payload, int]:
    """vout[0] vault, vout[1] token, vout[2] payload, vout[3] fee (if any), vout[4] attest fee."""
    vs = R.vault_script(tx.lock_height, owner, tx.lock_height + grace)
    outs = [R.TxOut(tx.collateral_zat, R.p2sh_script(vs)), R.TxOut(10_000, R.p2pkh_script(R.hash160(owner)))]
    fee_vout = 3 if tx.fee_zat is not None else R.FEE_VOUT_NONE
    afv = R.FEE_VOUT_NONE
    if tx.attest_fee_zat is not None:
        afv = 4 if tx.fee_zat is not None else 3
    payload = R.encode_mint(tx.term_class, tx.cents, tx.lock_height, tx.ref_height, owner, fee_vout, afv)
    outs.append(R.TxOut(0, opret(payload)))
    if tx.fee_zat is not None:
        outs.append(R.TxOut(tx.fee_zat, R.p2pkh_script(PAYEE)))
    if tx.attest_fee_zat is not None:
        outs.append(R.TxOut(tx.attest_fee_zat, R.p2pkh_script(ATTESTOR_KEY)))
    t = R.Tx(tid, [R.TxIn(txid("fund", hash(tid) & 0xFFFF), 0, b"")], outs)
    pl, idx = R.tx_payload([o.script for o in outs])
    return t, pl, idx


def spend_tx(
    tx: VB.SpendTx,
    vault_op: tuple[str, int],
    token_op: tuple[str, int] | None,
    vault: VB.VaultRecord,
    owner: bytes,
    grace: int,
    tid: str,
    *,
    payee: bytes = PAYEE,
) -> tuple[R.Tx, R.Payload | None, int | None]:
    vs = R.vault_script(vault.lock_height, owner, vault.lock_height + grace)
    if tx.path == "owner":
        sig = R.push(b"\x30" * 71) + bytes([R.OP_1]) + R.push(vs)
    elif tx.path == "claim":
        sig = bytes([R.OP_0]) + R.push(vs)
    else:
        sig = bytes([0xFF])  # not push-only: RED-1 fails
    vin = [R.TxIn(vault_op[0], vault_op[1], sig)]
    if token_op is not None and tx.yed_in:
        vin.append(R.TxIn(token_op[0], token_op[1], b""))
    outs = [
        R.TxOut(
            max(vault.collateral_zat // 2, 1),
            R.p2pkh_script(CLAIMANT if tx.path == "claim" else R.hash160(owner)),
        )
    ]
    pl = None
    idx = None
    if tx.redeem_payload:
        fee_vout = R.FEE_VOUT_NONE
        afv = R.FEE_VOUT_NONE
        nxt = 1
        assigned = []
        for c in tx.assigned:
            outs.append(R.TxOut(10_000, R.p2pkh_script(OTHER)))
            assigned.append((nxt, c))
            nxt += 1
        if tx.fee_zat is not None:
            outs.append(R.TxOut(tx.fee_zat, R.p2pkh_script(payee if tx.fee_payee_ok else OTHER)))
            fee_vout = nxt
            nxt += 1
        if tx.attest_fee_zat is not None:
            outs.append(R.TxOut(tx.attest_fee_zat, R.p2pkh_script(ATTESTOR_KEY)))
            afv = nxt
            nxt += 1
        if tx.owner_paid_zat:
            outs.append(R.TxOut(tx.owner_paid_zat, R.p2pkh_script(R.hash160(owner))))
            nxt += 1
        outs.append(R.TxOut(0, opret(R.encode_redeem(tx.ref_height, fee_vout, assigned, afv))))
        pl, idx = R.tx_payload([o.script for o in outs])
    t = R.Tx(tid, vin, outs)
    return t, pl, idx


def ref_vault(v: VB.VaultRecord, owner: bytes) -> R.Vault:
    r = R.Vault()
    r.owner_pubkey = owner
    r.term_class = v.term_class
    r.lock_height = v.lock_height
    r.claim_height = v.claim_height
    r.collateral_zat = v.collateral_zat
    r.minted_cents = v.minted_cents
    r.mint_height = v.mint_height
    r.ref_height = v.ref_height
    r.status = R.V_ACTIVE
    return r


def ref_snapshot(s: VB.Snap, attest_armed: bool | None = None) -> R.Snapshot:
    r = R.Snapshot()
    r.activation.status = R.ACTIVE if s.active else R.SIGNALING
    r.halt_mask = s.halt_mask
    r.p_mint = s.x_mint
    r.p_claim = s.x_claim
    r.p_fast = s.p_fast
    r.sigma_mult_bps = s.sigma_mult_bps
    r.issued_zat = s.issued_zat
    armed = s.armed if attest_armed is None else attest_armed
    if armed:
        r.attest = R.AttestState(R.ARMED, 0, 0)
    return r


def patch_bundle(model: R.YellowbackModel, bundle: VB.Bundle | None, seqs=(7, 8)) -> None:
    """Make BUNDLE-1 return ``bundle`` (``None`` = no carrier) and AFEE-1 accept any output of
    sufficient value (the key match is the real function's job, not this test's)."""

    def fake_bundle(tx, ref_height, selector, skip_vin0):
        b = bundle or VB.NO_BUNDLE
        return {
            "ok": b.ok,
            "reason": b.reason,
            "carrier_present": b.carrier_present,
            "atts": [(s, b.a_mint or 0, ref_height) for s in seqs] if b.ok else [],
            "selected": list(seqs),
            "a_mint": b.a_mint,
            "a_claim": b.a_claim,
        }

    def fake_afee(tx, attest_fee_vout, A, excluded, attest_fee_zat):
        if (
            attest_fee_vout == R.FEE_VOUT_NONE
            or attest_fee_vout >= len(tx.vout)
            or attest_fee_vout in excluded
        ):
            return None
        return A[0] if A and tx.vout[attest_fee_vout].value >= attest_fee_zat else None

    model._bundle = fake_bundle  # type: ignore[method-assign]
    model._attest_fee_ok = fake_afee  # type: ignore[method-assign]


__all__ = ["RA"]

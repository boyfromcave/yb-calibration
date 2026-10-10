"""Post-quantum chain growth and the ``-pqfeerate`` multiplier (quantum plan Q7, §4.5, §6, §9 item 3).

An analytic study, like ``oracle_replay``: no simulation, no price data, runs in well under a second.
It answers four questions for the post-quantum line (``upgrade/quantum-vaults``, byte layouts frozen
in ``docs/plans/yellowback-quantum-spec.md``):

1. **Size per path.** Exact serialized sizes of every Yellowback / vault transaction shape on the
   v4 (Sapling) transaction format, built from the spec's byte counts — SLH-DSA owner scriptSig
   7,939 B, Falcon 1,578 B, ``TX_PQPKH`` 35 B, holder spends 7,938 / 1,577 B — beside today's
   ECDSA shapes, a P2PKH payment and a Sapling z→z spend.
2. **Chain growth** per year under adoption scenarios (the policy's ``adoption_scenarios`` plus
   transfer, claim and bridge volumes declared here), separately for SLH-DSA-only holders (D-Q-2
   option b) and Falcon holders (option a).
3. **Fees** per path on both node lines: ycash-dd ``max(DEFAULT_FEE, rate × size)`` and ycash6
   ``max(-yellowbackfee, ZIP-317 conventional fee, rate × size)``, with ``rate = m × 100 zat/KB``;
   the break-even multipliers against ordinary transparent and Sapling bytes; a recommended
   release multiplier ``m`` with the rule that chose it.
4. **Validation cost.** Structural bounds (PQ verifications per full block, the verify-time budget
   that keeps a PQ-filled block no slower than an ECDSA-stuffed one); measured times are
   parameters (``VERIFY_US``) and stay ``None`` until the Q1 benchmarks exist.

Node constants (cited at ``wt/q-dd`` = ycash-dd ``upgrade/quantum-vaults`` @ ``ec9abe852`` and
``wt/q6`` = ycash6 @ ``0f150defb``) are module constants; tests pin them.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ybcal.config import Policy
from ybcal.units import BLOCKS_PER_DAY, COIN

OWNER_WP = "Q7 (quantum plan)"

# ---------------------------------------------------------------------------------------------------
# Node constants

#: ``DEFAULT_FEE`` (ycash-dd ``src/policy/fees.h:15``; ycash6 ``LEGACY_DEFAULT_FEE`` ``amount.h:38``
#: and ``DEFAULT_YELLOWBACK_FEE`` ``yellowback/params.h:83``), zat.
DEFAULT_FEE = 1_000
#: Relay floor ``DEFAULT_MIN_RELAY_TX_FEE``, zat per 1000 bytes (dd ``main.h:72``, 6 ``main.h:75``).
#: ``CFeeRate::GetFeeForRelay`` caps it at ``DEFAULT_FEE`` (dd ``amount.cpp:22-25``, 6 ``:25-28``).
RELAY_FLOOR_ZAT_PER_KB = 100
#: ``MAX_BLOCK_SIZE`` (``consensus/consensus.h``: dd ``:24``, 6 ``:29``).
MAX_BLOCK_SIZE = 2_000_000
#: ``MAX_BLOCK_SIGOPS`` and the policy cost of one ``OP_CHECKPQSIG`` (spec §0 / F-5: policy-only count).
MAX_BLOCK_SIGOPS = 20_000
PQ_SIGOP_COST = 20
#: ZIP-317 (ycash6 ``src/zip317.h``): marginal fee, grace actions, the per-action byte sizes.
ZIP317_MARGINAL_FEE = 500
ZIP317_GRACE_ACTIONS = 2
ZIP317_INPUT_BYTES = 150
ZIP317_OUTPUT_BYTES = 34
#: Sapling description sizes on the v4 format (spend 384, output 948, bindingSig 64).
SAPLING_SPEND = 384
SAPLING_OUTPUT = 948
SAPLING_BINDING_SIG = 64
#: ``VAULT_RPC_FEE`` (ycash-dd ``rpc/vault.cpp:51``): the flat fee of the vault_* RPCs.
VAULT_RPC_FEE = 10_000

# ---------------------------------------------------------------------------------------------------
# Script sizes (bytes). PQ numbers are the spec's (§0, §1.5, §2.1); the chunk arithmetic is re-derived
# by ``pq_scriptsig`` and checked against them in the tests.

PQ_SIG = {"slh": 7_856, "falcon": 666}  #: ``pq::SigSize`` without the hashtype byte
PQ_PK = {"slh": 32, "falcon": 897}  #: ``pq::PubKeySize``
MAX_CHUNK = 520
EC_SIG = 72  #: ``MAX_SIG_SIZE``: a 71-byte low-S DER signature + hashtype (ycash6 txbuilder.h)
EC_PUBKEY = 33
P2PKH_SCRIPT = 25
P2SH_SCRIPT = 23
PQPKH_SCRIPT = 35  #: ``20 <hash32> 51|52 c2`` (spec §2.1, F-1)
V_SCRIPT = 217  #: a YED V (spec §1.1; V is 209..220, the owner slot is 35 bytes both before and after)
I_SCRIPT = 198  #: an intent I (196..199)
ATTESTATION_SIZE = 74  #: ``yellowback/attest.h:31``
BUNDLE_HEADER_SIZE = 4  #: ``yellowback/bundle.h:38``
CARRIER_SCRIPT_SIZE = 71  #: ``yellowback/script.h:127``
PAYLOAD_MINT = 52  #: spec §3.1 (v4 and v3 alike)


def push_size(n: int) -> int:
    """Bytes of a minimal push of ``n`` data bytes (``CheckMinimalPush``)."""
    if n <= 75:
        return 1 + n
    if n <= 255:
        return 2 + n
    if n <= 520:
        return 3 + n
    raise ValueError(f"push of {n} bytes exceeds MAX_SCRIPT_ELEMENT_SIZE")


def chunks(n: int) -> list[int]:
    """Split ``n`` bytes into 520-byte chunks, the last one shorter (briefing: chunked stack)."""
    out = [MAX_CHUNK] * (n // MAX_CHUNK)
    if n % MAX_CHUNK:
        out.append(n % MAX_CHUNK)
    return out


def pq_scriptsig(scheme: str, selector: bool) -> int:
    """``<sig_1>…<sig_s> <s> <pk_1>…<pk_p> <p>`` (+ the V/I selector ``OP_n`` when ``selector``)."""
    sig = chunks(PQ_SIG[scheme] + 1)  # the hashtype byte rides on the last signature chunk
    pk = chunks(PQ_PK[scheme])
    n = sum(push_size(c) for c in sig) + 1 + sum(push_size(c) for c in pk) + 1
    return n + (1 if selector else 0)


SCRIPTSIG = {
    "p2pkh": push_size(EC_SIG) + push_size(EC_PUBKEY),  # 107
    "owner_ec": push_size(EC_SIG) + 1,  # <ownerSig> OP_2 = 74
    "owner_slh": pq_scriptsig("slh", True),  # 7,939
    "owner_falcon": pq_scriptsig("falcon", True),  # 1,578
    "holder_slh": pq_scriptsig("slh", False),  # 7,938
    "holder_falcon": pq_scriptsig("falcon", False),  # 1,577
    "selector": 1,  # OP_4 (claim), OP_1 (intent release)
}


def carrier_scriptsig(k_attest: int) -> int:
    """The P2SH carrier input: ``<bundle> <sig> <redeem>`` (txbuilder.cpp SignCarrierInput)."""
    return (
        push_size(BUNDLE_HEADER_SIZE + ATTESTATION_SIZE * k_attest)
        + push_size(EC_SIG)
        + push_size(CARRIER_SCRIPT_SIZE)
    )


def payload_script(n: int) -> int:
    """``OP_RETURN <payload>``."""
    return 1 + push_size(n)


def compact_size(n: int) -> int:
    return 1 if n < 253 else 3 if n <= 0xFFFF else 5


# ---------------------------------------------------------------------------------------------------
# Transactions


@dataclass(frozen=True)
class TxShape:
    """One transaction: the scriptSig length of each transparent input, the scriptPubKey length of each
    output, Sapling spends/outputs, and which inputs are PQ (for the fee rule and verify counts)."""

    name: str
    scriptsigs: tuple[int, ...]
    scripts: tuple[int, ...]
    sapling_spends: int = 0
    sapling_outputs: int = 0
    pq: tuple[str, ...] = ()  #: one entry per PQ verification: "slh" | "falcon"

    @property
    def inputs_bytes(self) -> int:
        return sum(32 + 4 + compact_size(n) + n + 4 for n in self.scriptsigs)

    @property
    def outputs_bytes(self) -> int:
        return sum(8 + compact_size(n) + n for n in self.scripts)

    @property
    def size(self) -> int:
        """Serialized v4 size: header 8, vin/vout, nLockTime 4, nExpiryHeight 4, valueBalance 8,
        the Sapling vectors, nJoinSplit 0, bindingSig when there is any Sapling description."""
        shielded = self.sapling_spends + self.sapling_outputs
        return (
            8
            + compact_size(len(self.scriptsigs))
            + self.inputs_bytes
            + compact_size(len(self.scripts))
            + self.outputs_bytes
            + 4
            + 4
            + 8
            + compact_size(self.sapling_spends)
            + SAPLING_SPEND * self.sapling_spends
            + compact_size(self.sapling_outputs)
            + SAPLING_OUTPUT * self.sapling_outputs
            + 1
            + (SAPLING_BINDING_SIG if shielded else 0)
        )

    @property
    def pq_bytes(self) -> int:
        """Bytes of PQ scriptSig (every PQ scriptSig is > 1,500 bytes, every other < 600)."""
        return sum(n for n in self.scriptsigs if n > 1_500)

    @property
    def is_pq(self) -> bool:
        return bool(self.pq)


#: Variants: (owner scheme, holder scheme). ``today`` is the upgrade/vault line (EC owner, P2PKH holders);
#: ``pq_p2pkh`` the quantum line while Falcon is inactive with P2PKH holders (spec §3.6, the wallet default);
#: ``slh`` D-Q-2 option (b), every holder on SLH-DSA; ``falcon`` option (a), Falcon holders.
VARIANTS: dict[str, tuple[str, str]] = {
    "today": ("ec", "p2pkh"),
    "pq_p2pkh": ("slh", "p2pkh"),
    "slh": ("slh", "slh"),
    "falcon": ("slh", "falcon"),
}


def _holder(holder: str) -> tuple[int, int, tuple[str, ...]]:
    """(scriptSig of a holder spend, scriptPubKey of a holder output, PQ verify tag)."""
    if holder == "p2pkh":
        return SCRIPTSIG["p2pkh"], P2PKH_SCRIPT, ()
    return SCRIPTSIG[f"holder_{holder}"], PQPKH_SCRIPT, (holder,)


def paths(variant: str, *, n_yed: int = 1, k_attest: int = 3) -> dict[str, TxShape]:
    """Every transaction shape of the variant. ``n_yed`` YED token inputs per transfer/redeem/claim,
    ``k_attest`` attestations in a carried bundle. Shapes follow ycash-dd ``yellowback/txbuilder.cpp``:
    MintOutputs ``:37-56``, PlanVaultSpend ``:58-…``, BuildTransfer ``:1375-1446``."""
    owner, holder = VARIANTS[variant]
    h_sig, h_spk, h_pq = _holder(holder)
    o_sig = SCRIPTSIG["owner_ec"] if owner == "ec" else SCRIPTSIG[f"owner_{owner}"]
    o_pq = () if owner == "ec" else (owner,)
    # The owner's residual / released collateral: P2PKH(owner) today, PQPKH(owner) on the PQ line (F-3).
    res_spk = P2PKH_SCRIPT if owner == "ec" else PQPKH_SCRIPT
    res_sig = SCRIPTSIG["p2pkh"] if owner == "ec" else SCRIPTSIG[f"holder_{owner}"]
    carrier = carrier_scriptsig(k_attest)
    P, S = SCRIPTSIG["p2pkh"], SCRIPTSIG["selector"]
    return {
        # vin funding P2PKH; vout P2SH carrier + change. Precedes every MINT and CLAIM.
        "carrier": TxShape("carrier", (P,), (P2SH_SCRIPT, P2PKH_SCRIPT)),
        # vin funding + carrier; vout V, token, payload, pool fee, attestor fee, change.
        "mint": TxShape(
            "mint",
            (P, carrier),
            (V_SCRIPT, h_spk, payload_script(PAYLOAD_MINT), P2PKH_SCRIPT, P2PKH_SCRIPT, P2PKH_SCRIPT),
        ),
        # vin n_yed tokens + YEC funding; vout recipient token, token change, payload (2 assignments),
        # YEC change.
        "transfer": TxShape(
            "transfer",
            (h_sig,) * n_yed + (P,),
            (h_spk, h_spk, payload_script(5 + 5 * 2), P2PKH_SCRIPT),
            pq=h_pq * n_yed,
        ),
        # owner-path REDEEM: vin V (owner selector 2) + n_yed tokens; vout collateral, pool fee, payload.
        "redeem": TxShape(
            "redeem",
            (o_sig,) + (h_sig,) * n_yed,
            (P2PKH_SCRIPT, P2PKH_SCRIPT, payload_script(11)),
            pq=o_pq + h_pq * n_yed,
        ),
        # in-term / post-term CLAIM: vin V (APP, OP_4) + claimant tokens + funding + carrier;
        # vout claimant intent, pool fee, attestor fee, residual intent, payload, funding change.
        "claim": TxShape(
            "claim",
            (S,) + (h_sig,) * n_yed + (P, carrier),
            (I_SCRIPT, P2PKH_SCRIPT, P2PKH_SCRIPT, I_SCRIPT, payload_script(11), P2PKH_SCRIPT),
            pq=h_pq * n_yed,
        ),
        # I selector 1 (CSV release): the claimant's to P2PKH, the owner's residual to PQPKH(owner).
        "release_claimant": TxShape("release_claimant", (S,), (P2PKH_SCRIPT,)),
        "release_residual": TxShape("release_residual", (S,), (res_spk,)),
        # the owner sweeps the released residual once (an SLH spend on the PQ line).
        "residual_sweep": TxShape("residual_sweep", (res_sig,), (P2PKH_SCRIPT,), pq=o_pq),
        # bridge: vault_lock (no owner signature), vault_ownerspend / recover (owner selector 2 or 3).
        "bridge_lock": TxShape("bridge_lock", (P,), (V_SCRIPT, P2PKH_SCRIPT)),
        "bridge_recover": TxShape("bridge_recover", (o_sig,), (P2PKH_SCRIPT,), pq=o_pq),
    }


def references() -> dict[str, TxShape]:
    """Ordinary transactions the PQ bytes are compared with."""
    P = SCRIPTSIG["p2pkh"]
    return {
        "p2pkh_1in2out": TxShape("p2pkh_1in2out", (P,), (P2PKH_SCRIPT, P2PKH_SCRIPT)),
        "p2pkh_2in2out": TxShape("p2pkh_2in2out", (P, P), (P2PKH_SCRIPT, P2PKH_SCRIPT)),
        "sapling_z2z": TxShape("sapling_z2z", (), (), sapling_spends=1, sapling_outputs=2),
    }


# ---------------------------------------------------------------------------------------------------
# Fees


def size_fee(size: int, mult: float) -> int:
    """``CFeeRate(m × 100).GetFee(size)``: integer zat, floor."""
    return int(mult * RELAY_FLOOR_ZAT_PER_KB * size // 1000)


def zip317_actions(tx: TxShape) -> int:
    """ycash6 ``CalculateLogicalActionCount`` (zip317.cpp:24-39), no JoinSplits/Orchard."""
    return max(
        math.ceil(tx.inputs_bytes / ZIP317_INPUT_BYTES), math.ceil(tx.outputs_bytes / ZIP317_OUTPUT_BYTES)
    ) + max(tx.sapling_spends, tx.sapling_outputs)


def zip317_fee(tx: TxShape) -> int:
    """``CalculateConventionalFee``: 500 × max(2, actions)."""
    return ZIP317_MARGINAL_FEE * max(ZIP317_GRACE_ACTIONS, zip317_actions(tx))


def fee_dd(tx: TxShape, mult: float) -> int:
    """ycash-dd wallet: flat ``DEFAULT_FEE``; a PQ transaction pays ``max(DEFAULT_FEE, rate × size)``
    (quantum plan §4.5)."""
    return max(DEFAULT_FEE, size_fee(tx.size, mult)) if tx.is_pq else DEFAULT_FEE


def fee_6(tx: TxShape, mult: float, *, additive: bool = False) -> int:
    """ycash6 wallet: ``max(-yellowbackfee, ZIP-317)`` (P-2, txbuilder.h NetworkFee); a PQ tx adds the
    size term — as a third ``max`` argument (default, the smallest reading of §4.5) or ``additive``."""
    base = max(DEFAULT_FEE, zip317_fee(tx))
    if not tx.is_pq:
        return base
    return base + size_fee(tx.size, mult) if additive else max(base, size_fee(tx.size, mult))


def relay_min_fee(tx: TxShape) -> int:
    """The least fee any node relays (``GetFeeForRelay`` = min(floor × size, DEFAULT_FEE)), both lines;
    ycash6's ZIP-317 unpaid-action limits are SIZE_MAX (zip317.h), so they do not raise it."""
    return min(size_fee(tx.size, 1), DEFAULT_FEE)


def zat_per_byte(fee: int, size: int) -> float:
    return fee / size


def break_even_mult(target_zat_per_byte: float) -> float:
    """Multiplier at which the size term reaches ``target`` zat per byte (rate × size dominates)."""
    return target_zat_per_byte * 1000 / RELAY_FLOOR_ZAT_PER_KB


def min_mult_for_rate(tx: TxShape, target_zat_per_byte: float, line: str) -> float:
    """Smallest multiplier (0.01 grid) at which ``tx`` pays at least ``target`` zat/byte on ``line``."""
    fee = fee_dd if line == "dd" else fee_6
    if fee(tx, 0) / tx.size >= target_zat_per_byte:
        return 0.0
    lo = math.floor(break_even_mult(target_zat_per_byte) * 100) - 1
    m = max(lo, 0)
    while fee(tx, m / 100) / tx.size < target_zat_per_byte:
        m += 1
    return m / 100


def min_mult_cross_line(tx: TxShape) -> float:
    """Smallest multiplier (0.01 grid) from which the size term binds on ycash6 too, so both lines charge
    the same fee for ``tx`` (ZIP-317 no longer the max)."""
    m = 0
    while size_fee(tx.size, m / 100) < max(DEFAULT_FEE, zip317_fee(tx)):
        m += 1
    return m / 100


# ---------------------------------------------------------------------------------------------------
# Adoption and growth

#: Volumes beyond the policy's mints/redeems per day (ASSUMPTIONS, stated in the report). Transfers are
#: YED payments; claims are the in-term + post-term CLAIMs (each adds two releases and one residual sweep);
#: bridge_recovers are bridge owner spends (rare: dormancy recovery); bridge_locks carry no PQ signature.
EXTRA_VOLUMES: dict[str, dict[str, float]] = {
    "low": {
        "transfers_per_day": 10.0,
        "claims_per_day": 0.2,
        "bridge_locks_per_day": 1.0,
        "bridge_recovers_per_day": 0.0,
    },
    "mid": {
        "transfers_per_day": 100.0,
        "claims_per_day": 1.0,
        "bridge_locks_per_day": 5.0,
        "bridge_recovers_per_day": 0.1,
    },
    "high": {
        "transfers_per_day": 1_000.0,
        "claims_per_day": 5.0,
        "bridge_locks_per_day": 20.0,
        "bridge_recovers_per_day": 1.0,
    },
    # A payments-heavy stress case (plan §6: "ten thousand a day"), redeems as in high.
    "stress": {
        "transfers_per_day": 10_000.0,
        "claims_per_day": 20.0,
        "bridge_locks_per_day": 50.0,
        "bridge_recovers_per_day": 5.0,
    },
}


def scenarios(policy: Policy) -> dict[str, dict[str, float]]:
    """Policy ``adoption_scenarios`` (mints/redeems per day) merged with ``EXTRA_VOLUMES``."""
    base = dict(policy.adoption_scenarios)
    base.setdefault("stress", {"mints_per_day": 100.0, "redeems_per_day": 60.0})
    out = {}
    for name, extra in EXTRA_VOLUMES.items():
        if name not in base:
            continue
        b = base[name]
        out[name] = {
            "mints_per_day": float(b["mints_per_day"]),
            "redeems_per_day": float(b["redeems_per_day"]),
            **extra,
        }
    return out


def daily_mix(vol: Mapping[str, float]) -> dict[str, float]:
    """Transactions per day per path."""
    claims = vol["claims_per_day"]
    return {
        "carrier": vol["mints_per_day"] + claims,
        "mint": vol["mints_per_day"],
        "transfer": vol["transfers_per_day"],
        "redeem": vol["redeems_per_day"],
        "claim": claims,
        "release_claimant": claims,
        "release_residual": claims,
        "residual_sweep": claims,
        "bridge_lock": vol["bridge_locks_per_day"],
        "bridge_recover": vol["bridge_recovers_per_day"],
    }


@dataclass
class Growth:
    scenario: str
    variant: str
    bytes_per_day: float
    pq_bytes_per_day: float
    gb_per_year: float
    block_share: float  #: share of 2 MB × 1,152 blocks a day
    pq_verifies_per_day: float


def growth(variant: str, scenario: str, vol: Mapping[str, float], **kw: int) -> Growth:
    ps = paths(variant, **kw)
    mix = daily_mix(vol)
    b = sum(n * ps[k].size for k, n in mix.items())
    pqb = sum(n * ps[k].pq_bytes for k, n in mix.items())
    ver = sum(n * len(ps[k].pq) for k, n in mix.items())
    return Growth(scenario, variant, b, pqb, b * 365 / 1e9, b / (MAX_BLOCK_SIZE * BLOCKS_PER_DAY), ver)


# ---------------------------------------------------------------------------------------------------
# Validation cost (structural; measured times are placeholders)

#: Verify time per signature in microseconds on the CI runner / reference machine. PLACEHOLDERS: fill from
#: the Q1 benchmarks (ycash-dd ``q/pqlib``: no bench output committed as of d4038d544).
VERIFY_US: dict[str, float | None] = {"ecdsa": None, "slh": None, "falcon": None}


def validation(verify_us: Mapping[str, float | None] = VERIFY_US) -> dict[str, object]:
    """PQ verifications per full block, the ECDSA-stuffed reference, and the break-even verify ratio."""
    one_in = {
        s: TxShape(f"{s}_1in1out", (SCRIPTSIG[f"holder_{s}"],), (P2PKH_SCRIPT,), pq=(s,))
        for s in ("slh", "falcon")
    }
    p2pkh_in = 32 + 4 + 1 + SCRIPTSIG["p2pkh"] + 4
    ecdsa_per_block = MAX_BLOCK_SIZE // p2pkh_in  # a block of P2PKH inputs: one ECDSA verify each
    out: dict[str, object] = {
        "ecdsa_inputs_per_block": ecdsa_per_block,
        "policy_pq_checks_per_block": MAX_BLOCK_SIGOPS // PQ_SIGOP_COST,
    }
    for s, tx in one_in.items():
        n = MAX_BLOCK_SIZE // tx.size
        out[f"{s}_per_block"] = n
        out[f"{s}_per_block_policy"] = min(n, MAX_BLOCK_SIGOPS // PQ_SIGOP_COST)
        #: a PQ-filled block verifies no slower than an ECDSA-stuffed one while t_pq / t_ecdsa <= this
        out[f"{s}_break_even_ratio"] = ecdsa_per_block / n
        t, te = verify_us.get(s), verify_us.get("ecdsa")
        out[f"{s}_block_verify_s"] = None if t is None else n * t / 1e6
        out["ecdsa_block_verify_s"] = None if te is None else ecdsa_per_block * te / 1e6
    return out


# ---------------------------------------------------------------------------------------------------
# The recommendation

#: Round multipliers the release may ship (§9 item 3).
CANDIDATES: tuple[float, ...] = (1, 2, 5, 10, 20, 25, 30, 40, 50, 60, 75, 100)


@dataclass
class Recommendation:
    mult: float
    m_parity_p2pkh: float  #: no PQ path pays less per byte than an ordinary P2PKH payment (dd)
    m_parity_sapling: float  #: … than a Sapling z→z
    m_cross_line: float  #: the size term binds on ycash6 for every PQ path (both lines charge the same)
    max_fee_usd_worst: float  #: the dearest PQ path's fee at worst_price_usd
    reasons: list[str] = field(default_factory=list)


def recommend(policy: Policy, *, fee_cap_usd: float = 1.0) -> Recommendation:
    """Smallest candidate ≥ max(P2PKH parity, cross-line identity) whose dearest PQ fee at the policy's
    ``worst_price_usd`` stays ≤ ``fee_cap_usd``. Sapling parity is implied (it is ~10× lower)."""
    refs = references()
    r_p2pkh = DEFAULT_FEE / refs["p2pkh_1in2out"].size
    r_sap = DEFAULT_FEE / refs["sapling_z2z"].size
    pq_paths = [tx for v in ("slh", "falcon") for tx in paths(v).values() if tx.is_pq]
    m_p = max(min_mult_for_rate(tx, r_p2pkh, "dd") for tx in pq_paths)
    m_s = max(min_mult_for_rate(tx, r_sap, "dd") for tx in pq_paths)
    m_x = max(min_mult_cross_line(tx) for tx in pq_paths)
    need = max(m_p, m_x)
    mult = next((c for c in CANDIDATES if c >= need), CANDIDATES[-1])
    worst = max(fee_dd(tx, mult) for tx in pq_paths) / COIN * policy.worst_price_usd
    rec = Recommendation(mult, m_p, m_s, m_x, worst)
    rec.reasons = [
        f"P2PKH parity needs m >= {m_p:g}: at 1x a PQ byte pays 0.1 zat against {r_p2pkh:.2f} zat for an "
        f"ordinary payment's byte, so block templates (fee-rate ordered) would place PQ spends last.",
        f"Cross-line identity needs m >= {m_x:g}: below it ycash6's ZIP-317 conventional fee "
        f"(500 zat per 150 input bytes = 3,333 zat/KB) is the max and the two lines charge different fees.",
        f"Sapling parity needs only m >= {m_s:g} (a Sapling z->z pays {r_sap:.3f} zat/byte).",
        f"At m = {mult:g} the dearest PQ path costs ${worst:.4f} at the policy's worst price "
        f"${policy.worst_price_usd:g}/YEC (cap ${fee_cap_usd:g}).",
    ]
    if worst > fee_cap_usd:
        rec.reasons.append("The fee cap is exceeded: the owner must trade parity for cost.")
    return rec


# ---------------------------------------------------------------------------------------------------
# Run and report


def run(
    policy: Policy, multipliers: Sequence[float] = (1, 10, 34, 50, 100), *, n_yed: int = 1, k_attest: int = 3
) -> dict[str, object]:
    refs = references()
    out: dict[str, object] = {
        "constants": {
            "DEFAULT_FEE": DEFAULT_FEE,
            "RELAY_FLOOR_ZAT_PER_KB": RELAY_FLOOR_ZAT_PER_KB,
            "MAX_BLOCK_SIZE": MAX_BLOCK_SIZE,
            "BLOCKS_PER_DAY": BLOCKS_PER_DAY,
            "scriptsig": SCRIPTSIG,
            "carrier_scriptsig": carrier_scriptsig(k_attest),
            "n_yed": n_yed,
            "k_attest": k_attest,
        }
    }
    sizes: dict[str, dict[str, int]] = {}
    fees: dict[str, dict[str, dict[str, int]]] = {}
    for v in VARIANTS:
        for name, tx in paths(v, n_yed=n_yed, k_attest=k_attest).items():
            sizes.setdefault(name, {})[v] = tx.size
            if tx.is_pq or v == "today":
                for m in multipliers:
                    fees.setdefault(f"{name}/{v}", {})[f"{m:g}"] = {
                        "dd": fee_dd(tx, m),
                        "6": fee_6(tx, m),
                        "6_additive": fee_6(tx, m, additive=True),
                        "relay_min": relay_min_fee(tx),
                    }
    for name, tx in refs.items():
        sizes[name] = {"ref": tx.size}
        fees[f"{name}/ref"] = {
            "any": {"dd": DEFAULT_FEE, "6": max(DEFAULT_FEE, zip317_fee(tx)), "relay_min": relay_min_fee(tx)}
        }
    out["sizes"] = sizes
    out["fees"] = fees
    # Filling one 2 MB block: the fee it earns, per path, at each multiplier, against the references.
    fill: dict[str, dict[str, float]] = {}
    for name, tx in refs.items():
        n = MAX_BLOCK_SIZE // tx.size
        fill[f"{name}"] = {
            "dd": n * DEFAULT_FEE,
            "6": n * max(DEFAULT_FEE, zip317_fee(tx)),
            "relay_min": n * relay_min_fee(tx),
        }
    for v in ("slh", "falcon"):
        for name in ("redeem", "transfer", "bridge_recover"):
            tx = paths(v, n_yed=n_yed, k_attest=k_attest)[name]
            if not tx.is_pq:
                continue
            n = MAX_BLOCK_SIZE // tx.size
            row: dict[str, float] = {"relay_min": n * relay_min_fee(tx)}
            for m in multipliers:
                row[f"dd@{m:g}"] = n * fee_dd(tx, m)
                row[f"6@{m:g}"] = n * fee_6(tx, m)
            fill[f"{name}/{v}"] = row
    out["block_fill_zat"] = fill
    sc = scenarios(policy)
    out["scenarios"] = sc
    out["growth"] = [
        asdict(growth(v, s, vol, n_yed=n_yed, k_attest=k_attest)) for s, vol in sc.items() for v in VARIANTS
    ]
    out["validation"] = validation()
    out["recommendation"] = asdict(recommend(policy))
    out["dust"] = dust(policy)
    return out


def dust(policy: Policy, mult: float | None = None) -> dict[str, float]:
    """G9's ``minOutput`` lower bound (3 × the cost of spending a token) under PQ holders."""
    m = mult if mult is not None else recommend(policy).mult
    out = {"mult": m}
    for v in ("today", "slh", "falcon"):
        f = fee_dd(paths(v)["transfer"], m)
        out[f"{v}_transfer_fee_zat"] = f
        out[f"{v}_minOutput_bound_usd_at_worst"] = 3 * f / COIN * policy.worst_price_usd
    return out


def _fmt_val(v: object) -> str:
    if v is None:
        return "PLACEHOLDER (no benchmark yet)"
    return f"{v:,.2f}" if isinstance(v, float) else str(v)


def _fmt_int(x: float) -> str:
    return f"{x:,.0f}"


def render_markdown(res: Mapping[str, object], multipliers: Sequence[float]) -> str:
    sizes: Mapping[str, Mapping[str, int]] = res["sizes"]  # type: ignore[assignment]
    fees: Mapping[str, Mapping[str, Mapping[str, int]]] = res["fees"]  # type: ignore[assignment]
    rec: Mapping[str, object] = res["recommendation"]  # type: ignore[assignment]
    L: list[str] = []
    L.append("## Sizes per path (bytes, serialized v4)\n")
    L.append(
        "| Path | today (EC owner, P2PKH holder) | PQ, P2PKH holders | (b) SLH holders | (a) Falcon holders |"
    )
    L.append("|---|---:|---:|---:|---:|")
    for name, row in sizes.items():
        if "ref" in row:
            continue
        L.append(f"| {name} | " + " | ".join(_fmt_int(row[v]) for v in VARIANTS) + " |")
    L.append("\n| Reference | bytes |\n|---|---:|")
    for name, row in sizes.items():
        if "ref" in row:
            L.append(f"| {name} | {_fmt_int(row['ref'])} |")
    L.append("\n## Fees per PQ path (zat): ycash-dd / ycash6 (max form) / ycash6 additive\n")
    L.append("| Path / variant | " + " | ".join(f"m = {m:g}" for m in multipliers) + " | relay minimum |")
    L.append("|---|" + "---:|" * (len(multipliers) + 1))
    for key, row in fees.items():
        if key.endswith("/ref") or key.endswith("/today"):
            continue
        cells = [
            f"{row[f'{m:g}']['dd']:,} / {row[f'{m:g}']['6']:,} / {row[f'{m:g}']['6_additive']:,}"
            for m in multipliers
        ]
        L.append(f"| {key} | " + " | ".join(cells) + f" | {row[f'{multipliers[0]:g}']['relay_min']:,} |")
    L.append("\n## Chain growth\n")
    L.append(
        "| Scenario | Variant | bytes/day | PQ bytes/day | GB/year | share of block space | PQ verifies/day |"
    )
    L.append("|---|---|---:|---:|---:|---:|---:|")
    for g in res["growth"]:  # type: ignore[union-attr]
        L.append(
            f"| {g['scenario']} | {g['variant']} | {_fmt_int(g['bytes_per_day'])} | "
            f"{_fmt_int(g['pq_bytes_per_day'])} | "
            f"{g['gb_per_year']:.3f} | {g['block_share']:.5%} | {g['pq_verifies_per_day']:,.1f} |"
        )
    L.append("\n## Filling one 2 MB block (total fees, zat)\n")
    L.append("| Block of | " + " | ".join(f"dd / 6 at m = {m:g}" for m in multipliers) + " | relay minimum |")
    L.append("|---|" + "---:|" * (len(multipliers) + 1))
    for key, row in res["block_fill_zat"].items():  # type: ignore[union-attr]
        if "dd" in row:
            cells = [f"{row['dd']:,.0f} / {row['6']:,.0f}"] * len(multipliers)
        else:
            cells = [f"{row[f'dd@{m:g}']:,.0f} / {row[f'6@{m:g}']:,.0f}" for m in multipliers]
        L.append(f"| {key} | " + " | ".join(cells) + f" | {row['relay_min']:,.0f} |")
    L.append("\n## Dust (G9 minOutput lower bound, 3 x a token spend at worst_price_usd)\n")
    for k, v in res["dust"].items():  # type: ignore[union-attr]
        L.append(f"- `{k}`: {v:,.4f}" if isinstance(v, float) else f"- `{k}`: {v:,}")
    L.append("\n## Recommendation\n")
    L.append(
        f"**-pqfeerate multiplier m = {rec['mult']:g}** "
        f"(rate {rec['mult'] * RELAY_FLOOR_ZAT_PER_KB:,.0f} zat/KB).\n"
    )
    for r in rec["reasons"]:  # type: ignore[union-attr]
        L.append(f"- {r}")
    L.append("\n## Validation (structural)\n")
    for k, v in res["validation"].items():  # type: ignore[union-attr]
        L.append(
            f"- `{k}`: {_fmt_val(v)}"
        )
    return "\n".join(L) + "\n"


def configure_pq(p: argparse.ArgumentParser) -> None:
    p.add_argument("--multipliers", default="1,10,34,50,100", help="comma-separated -pqfeerate multipliers")
    p.add_argument("--policy", default=None, help="policy TOML (default policy/default.toml)")
    p.add_argument(
        "--n-yed", dest="n_yed", type=int, default=1, help="YED token inputs per transfer/redeem/claim"
    )
    p.add_argument("--k-attest", dest="k_attest", type=int, default=3, help="attestations per carried bundle")
    p.add_argument("--out", default=None, help="write pq.json and pq.md here")


def _mults(s: str) -> list[float]:
    return [float(x) for x in s.split(",") if x.strip()]


def cli_pq(args: argparse.Namespace) -> int:
    policy = Policy.load(args.policy)
    mults = _mults(args.multipliers)
    res = run(policy, mults, n_yed=args.n_yed, k_attest=args.k_attest)
    md = render_markdown(res, mults)
    if args.out:
        d = Path(args.out)
        d.mkdir(parents=True, exist_ok=True)
        (d / "pq.json").write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
        (d / "pq.md").write_text(md)
    sys.stdout.write(md)
    return 0

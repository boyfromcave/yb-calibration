"""The vault book: mint, mature, redeem, claim, notice, void, sweep, unbacked (owner: WP-4).

Three layers, from exact to fast:

1. **Rule functions** (pure Python integers, ``None`` = undefined) transcribed from
   ``src/yellowback/state.cpp`` @ 7702d22 and tested verdict-for-verdict against the vendored
   reference model (``YellowbackModel._mint_verdict`` / ``_red_verdict`` / ``_apply_notice``):

   * :func:`mint_verdict` — MintVerdict (state.cpp:293-381): MINT-2 amount / class / lock / ref
     window, MINT-3 (structure, assumed wallet-built unless stated), MINT-4 halts with the W16
     GLOBAL_RATIO recapitalisation exemption, MINT-5 (pMint, ``4·feeMin`` floor), MINT-6 soft cap
     (W20; reads the *live* supply, same-block mints included), MINT-7, MINT-8 (FEE-0 when ``E(R)``
     is empty), and when ARMED MINT-9 → AFEE-1 → MINT-5 at ``min(xMint, aMint)`` → MINT-10.
   * :func:`red_verdict` — RedVerdict (state.cpp:491-575): RED-1 (single vault, path, payload,
     ref window, assignments; the claim path's bundle when ARMED), RED-2 (burn ≥ debt), RED-3,
     AFEE-1, RED-4 (a) underwater at pClaim with ``claimThresholdBps`` or (b) a notice persisted
     ``emergencyPersist … emergencyNoticeTtl`` and underwater at pEmerg with ``emergencyRatioBps``,
     RED-5 residual to the owner.
   * :func:`notice_verdict` — NOT-1 (reference ``_apply_notice``).
   * Outcomes (state.cpp:384-438, 578-643, 869-892): :class:`VaultBook` applies them — ACTIVE or
     VOID on a mint; CLOSED (owner) / CLAIMED (claim) on a passing spend; a *failing* spend that a
     block nevertheless carries (only possible when ACT-5 enforcement is off) closes the vault,
     burns the YED inputs and adds ``mintedCents − burned`` to ``unbackedCents``; a VOID vault is
     released by an ordinary spend (CLOSED, nothing burned).
   * The vault script (script.cpp:79-90): the owner path from ``lockHeight``, the claim path from
     ``claimHeight = lockHeight + grace`` only (fact 1.5-1); a spend with ``nLockTime = L`` confirms
     at the earliest in block ``L + 1`` (IsFinalTx: ``nLockTime < nBlockHeight``).

2. **The book runner** :func:`run_book` — one path's timeline (block or hour resolution, a
   :class:`Timeline`), mint attempts from :mod:`ybcal.sim.agents`, and the personas' decisions.
   Every lifecycle that does not depend on other vaults (owner redeem, sweeps, claims and notices) is
   planned *vectorised* with first-passage searches over sparse tables; the order-dependent part
   (refHeight choice, MINT-4 HALT-2 at R, MINT-6 against the live supply, VOID) runs sequentially in
   height order through the exact rule functions above, so every state change the book makes is a
   verdict the node would give.

3. **Engine integration** — :class:`VaultHook` is WP-3's ``vaults`` hook (block mode: fills
   ``BlockSeries.supply_cents`` / ``collateral_zat``; the engine then recomputes HALT-2, which equals
   the book's own HALT-2 at every height) and :func:`simulate_vault_book_hours` runs multi-year
   horizons through WP-3's :class:`~ybcal.sim.engine.OracleTransferKernel` /
   :func:`~ybcal.sim.engine.simulate_hours`.

Hour mode conventions (documented in docs/architecture.md): step ``t`` is the snapshot at the last
block of hour ``t`` (height ``start + offset + 48t + 47``); a transaction "at step t" uses
``R = heights[t − 1]`` and confirms at ``R + DEFAULT_REF_LAG + 1``; the 40-block refHeight choice is
below the hour resolution, so it collapses to that one snapshot.
"""

from __future__ import annotations

import bisect
import heapq
import multiprocessing as mp
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

from ybcal.model import kernels as K
from ybcal.model import reference as REF
from ybcal.model import vkernels as V
from ybcal.sim import agents as AG
from ybcal.sim import engine as E
from ybcal.sim import fees as FEES
from ybcal.sim import supply as SUP
from ybcal.units import BLOCKS_PER_DAY, BLOCKS_PER_HOUR, BPS, COIN, LOCKTIME_THRESHOLD

OWNER_WP = "WP-4"

UNDEF = V.UNDEF
MAX_MONEY = K.MAX_MONEY
_BIG = np.iinfo(np.int64).max

#: Vaults.status (view.h; reference V_*).
ACTIVE, VOID, CLOSED, CLAIMED = REF.V_ACTIVE, REF.V_VOID, REF.V_CLOSED, REF.V_CLAIMED
STATUS_NAMES = dict(REF.VAULT_STATUS_NAMES)

HALT_NOT_ACTIVE = E.HALT_NOT_ACTIVE
HALT_NO_PRICE = E.HALT_NO_PRICE
HALT_PARTICIPATION = E.HALT_PARTICIPATION
HALT_GLOBAL_RATIO = E.HALT_GLOBAL_RATIO
HALT_DIVERGENCE = E.HALT_DIVERGENCE
HALT_ENFORCEMENT = E.HALT_ENFORCEMENT

# Verdicts (state.cpp:22-59 @ 7702d22).
OK = "ok"
BAD_MINT_AMOUNT = "bad-mint-amount"
BAD_MINT_CLASS = "bad-mint-class"
BAD_MINT_LOCK_HEIGHT = "bad-mint-lock-height"
BAD_MINT_REF_HEIGHT = "bad-mint-ref-height"
BAD_MINT_OUTPUTS = "bad-mint-outputs"
MINT_NOT_ACTIVE = "mint-not-active"
MINT_HALTED_NO_PRICE = "mint-halted-no-price"
MINT_HALTED_PARTICIPATION = "mint-halted-participation"
MINT_HALTED_GLOBAL_RATIO = "mint-halted-global-ratio"
MINT_HALTED_DIVERGENCE = "mint-halted-divergence"
BAD_MINT_COLLATERAL = "bad-mint-collateral"
MINT_UNSATISFIABLE = "mint-unsatisfiable"
MINT_SUPPLY_CAP = "mint-supply-cap"
BAD_MINT_TOKEN_OUTPUT = "bad-mint-token-output"
BAD_MINT_FEE = "bad-mint-fee"
MINT9_NO_BUNDLE = "mint9-no-bundle"
MINT9_BUNDLE_PREFIX = "mint9-bundle-"
MINT10_DIVERGED = "mint10-diverged"
AFEE1_FEE = "afee1-fee"
BUNDLE_STAT = "stat"
VAULT_SPEND_MALFORMED = "vault-spend-malformed"
VAULT_SPEND_MISSING_BURN = "vault-spend-missing-burn"
VAULT_SPEND_SHORT_BURN = "vault-spend-short-burn"
VAULT_SPEND_BAD_FEE = "vault-spend-bad-fee"
VAULT_SPEND_BAD_PAYEE = "vault-spend-bad-payee"
VAULT_CLAIM_NOT_UNDERWATER = "vault-claim-not-underwater"
RED1_BUNDLE_PREFIX = "red1-bundle-"
RED5_RESIDUAL = "red5-residual"
#: Wallet preflight refusal when no snapshot in the window admits the mint (no transaction sent).
REFUSED = "refused"

#: How a vault left ACTIVE / VOID (book close kinds).
K_OPEN, K_OWNER, K_SWEEP, K_CLAIM, K_THIEF, K_VOID_RELEASE, K_NOTICE = -1, 0, 1, 2, 3, 4, 5
CLOSE_KIND_NAMES = {
    K_OPEN: "open",
    K_OWNER: "owner",
    K_SWEEP: "sweep",
    K_CLAIM: "claim",
    K_THIEF: "thief",
    K_VOID_RELEASE: "void-release",
}
#: Attempt outcomes.
O_REFUSED, O_VOID, O_ACTIVE = 0, 1, 2


# ---------------------------------------------------------------------------------------------------
# 1. Rule layer


@dataclass(frozen=True)
class RuleParams:
    """The parameters the vault rules read, as ints (from a ParamSet / any registry-keyed mapping)."""

    start_height: int
    ref_window: int
    grace: int
    class_min: tuple[int, int, int]
    class_max: tuple[int, int, int]
    base_ratio: tuple[int, int, int]
    min_mint: int
    max_mint: int
    min_output: int
    max_output: int
    fee_min: int
    fee_bps: int
    attest_fee_bps: int
    claim_threshold_bps: int
    emergency_ratio_bps: int
    emergency_persist: int
    emergency_notice_ttl: int
    residual_min_zat: int
    recap_ratio_bps: int
    global_ratio_halt_bps: int
    supply_cap_bps: int
    diverge_bps_attest: int
    enforce_until: int
    abandon_blocks: int
    default_ref_lag: int

    @classmethod
    def of(cls, p: Mapping) -> RuleParams:
        if isinstance(p, RuleParams):
            return p
        g = lambda k: int(p[k])  # noqa: E731
        return cls(
            start_height=g("startHeight"),
            ref_window=g("refWindow"),
            grace=g("grace"),
            class_min=tuple(g(f"classMin[{i}]") for i in range(3)),  # type: ignore[arg-type]
            class_max=tuple(g(f"classMax[{i}]") for i in range(3)),  # type: ignore[arg-type]
            base_ratio=tuple(g(f"baseRatioBps[{i}]") for i in range(3)),  # type: ignore[arg-type]
            min_mint=g("minMint"),
            max_mint=g("maxMint"),
            min_output=g("minOutput"),
            max_output=g("maxOutput"),
            fee_min=g("feeMin"),
            fee_bps=g("feeBps"),
            attest_fee_bps=g("attestFeeBps"),
            claim_threshold_bps=g("claimThresholdBps"),
            emergency_ratio_bps=g("emergencyRatioBps"),
            emergency_persist=g("emergencyPersist"),
            emergency_notice_ttl=g("emergencyNoticeTtl"),
            residual_min_zat=g("residualMinZat"),
            recap_ratio_bps=g("recapRatioBps"),
            global_ratio_halt_bps=g("globalRatioHaltBps"),
            supply_cap_bps=g("supplyCapBps"),
            diverge_bps_attest=g("divergeBpsAttest"),
            enforce_until=g("enforceUntilHeight"),
            abandon_blocks=g("abandonBlocks"),
            default_ref_lag=g("DEFAULT_REF_LAG"),
        )

    def fee(self, collateral_zat: int) -> int:
        """FEE-1."""
        return K.fee_zat(collateral_zat, self.fee_min, self.fee_bps)

    def attest_fee(self, collateral_zat: int) -> int:
        """AFEE-1 (on FEE-1 of the same collateral)."""
        return K.attest_fee_zat(self.fee(collateral_zat), self.attest_fee_bps)


@dataclass(frozen=True)
class Snap:
    """What a rule reads from ``Snapshots[R]`` (``haltMask`` already includes HALT-2)."""

    height: int
    active: bool = True
    halt_mask: int = 0
    x_mint: int | None = None
    x_claim: int | None = None
    p_fast: int | None = None
    sigma_mult_bps: int = BPS
    issued_zat: int = 0
    armed: bool = False
    eligible: bool = True  #: E(R) non-empty (FEE-2; empty ⇒ FEE-0)


@dataclass(frozen=True)
class Bundle:
    """A BUNDLE-1 outcome: verified (``ok``) with its statistic, or the failure reason."""

    a_mint: int | None
    a_claim: int | None
    ok: bool = True
    reason: str = ""
    carrier_present: bool = True


#: The outcome of a transaction without a carrier (reference ``_find_carrier`` reason ``shape``).
NO_BUNDLE = Bundle(None, None, ok=False, reason="shape", carrier_present=False)


@dataclass(frozen=True)
class MintTx:
    """A MINT as the rules see it. ``fee_zat`` is the value of a fee output that pays an ``E(R)``
    member at a valid ``feeVout`` (``None``: no such output); ``attest_fee_zat`` likewise for AFEE-1.
    ``structure`` is MINT-3's verdict (``ok`` for every wallet-built mint)."""

    term_class: int
    cents: int
    lock_height: int
    ref_height: int
    collateral_zat: int
    fee_zat: int | None = None
    bundle: Bundle | None = None
    attest_fee_zat: int | None = None
    structure: str = OK
    token_output_ok: bool = True


def _opt(v) -> int | None:
    return None if v is None or int(v) <= 0 else int(v)


def mint_verdict(rp: RuleParams, height: int, tx: MintTx, snap: Snap | None, supply_cents: int) -> str:
    """MintVerdict (state.cpp:293-381) for a MINT confirming at ``height`` with ``Snapshots[R] =
    snap`` and the live ``totals.supplyCents`` (MINT-6 reads the running total, so a mint earlier in
    the same block counts)."""
    H = int(height)
    c = int(tx.term_class)
    # MINT-2 (signed arithmetic, M1)
    if not 0 <= c <= 2:
        return BAD_MINT_CLASS
    if tx.cents < rp.min_mint or tx.cents > rp.max_mint:
        return BAD_MINT_AMOUNT
    lock, ref = int(tx.lock_height), int(tx.ref_height)
    if not lock + rp.grace < LOCKTIME_THRESHOLD:
        return BAD_MINT_LOCK_HEIGHT
    if not (H - rp.ref_window <= ref <= H - 1) or ref < rp.start_height:
        return BAD_MINT_REF_HEIGHT
    if not lock > ref or lock - ref < rp.class_min[c] or lock - ref > rp.class_max[c]:
        return BAD_MINT_LOCK_HEIGHT
    # MINT-3
    if tx.structure != OK:
        return tx.structure
    # MINT-4
    if snap is None or not snap.active:
        return MINT_NOT_ACTIVE
    m = int(snap.halt_mask)
    if m & HALT_NOT_ACTIVE:
        return MINT_NOT_ACTIVE
    if m & HALT_NO_PRICE:
        return MINT_HALTED_NO_PRICE
    if m & (HALT_PARTICIPATION | HALT_ENFORCEMENT):
        return MINT_HALTED_PARTICIPATION
    mr = K.min_ratio_bps(rp.base_ratio[c], int(snap.sigma_mult_bps))
    if (m & HALT_GLOBAL_RATIO) and mr < rp.recap_ratio_bps:  # W16 recapitalisation exemption
        return MINT_HALTED_GLOBAL_RATIO
    if m & HALT_DIVERGENCE:
        return MINT_HALTED_DIVERGENCE
    if m & ~HALT_GLOBAL_RATIO:
        return MINT_NOT_ACTIVE
    x_mint = _opt(snap.x_mint)
    coll = int(tx.collateral_zat)

    def mint5(p_mint: int | None) -> str | None:
        if p_mint is None:
            return MINT_HALTED_NO_PRICE
        req = K.required_zat(int(tx.cents), mr, p_mint)
        if req is None:
            return MINT_UNSATISFIABLE
        if coll < req or coll < 4 * rp.fee_min:
            return BAD_MINT_COLLATERAL
        return None

    if not snap.armed:
        v = mint5(x_mint)
        if v is not None:
            return v
    # MINT-6 (W20 soft cap: refused only below the recapitalisation floor)
    cap = K.supply_cap_cents(int(snap.issued_zat), x_mint, rp.supply_cap_bps)
    if cap is not None and int(supply_cents) + int(tx.cents) > cap and mr < rp.recap_ratio_bps:
        return MINT_SUPPLY_CAP
    # MINT-7
    if not tx.token_output_ok:
        return BAD_MINT_TOKEN_OUTPUT
    # MINT-8 (FEE-0 when E(R) is empty, K11)
    if snap.eligible and (tx.fee_zat is None or tx.fee_zat < rp.fee(coll)):
        return BAD_MINT_FEE
    if not snap.armed:
        return OK
    # MINT-9
    b = tx.bundle if tx.bundle is not None else NO_BUNDLE
    if not b.ok:
        return MINT9_NO_BUNDLE if not b.carrier_present else MINT9_BUNDLE_PREFIX + b.reason
    a_mint = _opt(b.a_mint)
    if a_mint is None:
        return MINT9_BUNDLE_PREFIX + BUNDLE_STAT
    # AFEE-1
    if tx.attest_fee_zat is None or tx.attest_fee_zat < rp.attest_fee(coll):
        return AFEE1_FEE
    # MINT-5 at the combined pMint
    v = mint5(min(x_mint, a_mint) if x_mint is not None else None)
    if v is not None:
        return v
    # MINT-10 (W17): pFast(R) against aMint
    x = _opt(snap.p_fast) or x_mint
    if abs(x - a_mint) * BPS > max(0, rp.diverge_bps_attest) * min(x, a_mint):  # type: ignore[operator]
        return MINT10_DIVERGED
    return OK


@dataclass
class VaultRecord:
    """``Vaults[outpoint]`` (view.h; reference ``Vault``)."""

    term_class: int
    lock_height: int
    claim_height: int
    collateral_zat: int
    minted_cents: int
    mint_height: int
    ref_height: int
    status: int = ACTIVE
    void_reason: str = ""
    close_height: int = 0
    burned_cents: int = 0
    fee_paid_zat: int = 0
    unbacked: bool = False


@dataclass(frozen=True)
class SpendTx:
    """A transaction spending one vault at ``vin[0]``. ``path`` is the scriptSig selector (``None``
    = not push-only / too few pushes); ``redeem_payload`` False = no REDEEM payload (a sweep, a
    thief, a VOID release). ``fee_zat`` / ``fee_payee_ok`` model RED-3's fee output,
    ``owner_paid_zat`` the largest P2PKH(owner) output outside the excluded vouts (RED-5)."""

    path: Literal["owner", "claim"] | None
    ref_height: int
    yed_in: int = 0
    assigned: tuple[int, ...] = ()
    redeem_payload: bool = True
    single_vault: bool = True
    fee_zat: int | None = None
    fee_payee_ok: bool = True
    bundle: Bundle | None = None
    attest_fee_zat: int | None = None
    owner_paid_zat: int = 0


@dataclass(frozen=True)
class RedOutcome:
    verdict: str
    claim_path: str = ""  #: "a" | "b" | ""
    residual_zat: int = 0
    p_claim: int | None = None
    p_emerg: int | None = None


def red_verdict(
    rp: RuleParams,
    height: int,
    vault: VaultRecord,
    tx: SpendTx,
    snap: Snap | None,
    notice_ref_height: int | None = None,
) -> RedOutcome:
    """RedVerdict (state.cpp:491-575) for a spend of an ACTIVE ``vault`` confirming at ``height``,
    with ``Snapshots[R] = snap`` and the standing notice's refHeight (``None``: no notice)."""
    H = int(height)
    if not tx.single_vault or tx.path is None or not tx.redeem_payload:
        return RedOutcome(VAULT_SPEND_MALFORMED)
    ref = int(tx.ref_height)
    if not (H - rp.ref_window <= ref <= H - 1) or ref < rp.start_height:
        return RedOutcome(VAULT_SPEND_MALFORMED)
    for a in tx.assigned:
        if a < rp.min_output or a > rp.max_output:
            return RedOutcome(VAULT_SPEND_MALFORMED)
    claim = tx.path == "claim"
    armed = claim and snap is not None and bool(snap.armed)
    b = tx.bundle if tx.bundle is not None else NO_BUNDLE
    if armed:
        if not b.ok:
            return RedOutcome(RED1_BUNDLE_PREFIX + b.reason)
        if _opt(b.a_claim) is None:
            return RedOutcome(RED1_BUNDLE_PREFIX + BUNDLE_STAT)
    # RED-2
    burn = int(tx.yed_in) - sum(tx.assigned)
    if burn < vault.minted_cents:
        return RedOutcome(VAULT_SPEND_MISSING_BURN if burn <= 0 else VAULT_SPEND_SHORT_BURN)
    # RED-3
    if snap is not None and snap.eligible:
        if tx.fee_zat is None:
            return RedOutcome(VAULT_SPEND_BAD_FEE)
        if not tx.fee_payee_ok:
            return RedOutcome(VAULT_SPEND_BAD_PAYEE)
        if tx.fee_zat < rp.fee(vault.collateral_zat):
            return RedOutcome(VAULT_SPEND_BAD_FEE)
    # AFEE-1
    if armed and (tx.attest_fee_zat is None or tx.attest_fee_zat < rp.attest_fee(vault.collateral_zat)):
        return RedOutcome(AFEE1_FEE)
    if not claim:
        return RedOutcome(OK)
    # RED-4
    x_claim = _opt(snap.x_claim) if snap is not None else None
    p_claim: int | None = x_claim
    p_emerg: int | None = None
    if armed:
        cp = K.price_combine(_opt(snap.x_mint), x_claim, _opt(b.a_mint), _opt(b.a_claim))  # type: ignore[union-attr]
        p_claim, p_emerg = cp.p_claim, cp.p_emerg
    coll, minted = vault.collateral_zat, vault.minted_cents
    if K.is_underwater(coll, p_claim, minted, rp.claim_threshold_bps):
        path = "a"
    else:
        persisted = (
            armed
            and notice_ref_height is not None
            and rp.emergency_persist <= ref - int(notice_ref_height) <= rp.emergency_notice_ttl
        )
        if not persisted or not K.is_underwater(coll, p_emerg, minted, rp.emergency_ratio_bps):
            return RedOutcome(VAULT_CLAIM_NOT_UNDERWATER, p_claim=p_claim, p_emerg=p_emerg)
        path = "b"
    # RED-5
    if p_claim is None:
        return RedOutcome(RED5_RESIDUAL, path)
    margin = rp.claim_threshold_bps if path == "a" else BPS
    residual = K.residual_zat(coll, K.claimant_max_zat(minted, margin, p_claim))
    if residual >= rp.residual_min_zat and tx.owner_paid_zat < residual:
        return RedOutcome(RED5_RESIDUAL, path, residual, p_claim, p_emerg)
    return RedOutcome(OK, path, residual, p_claim, p_emerg)


def notice_verdict(
    rp: RuleParams,
    height: int,
    vault: VaultRecord | None,
    ref: int,
    snap: Snap | None,
    bundle: Bundle | None,
    standing_height: int | None = None,
) -> bool:
    """NOT-1 (reference ``_apply_notice``): the vault ACTIVE, R in the window, ARMED at R, a bundle
    with the vault-outpoint selector, ``pEmerg = min(xClaim, aClaim)`` underwater at
    ``emergencyRatioBps``, and no standing notice younger than ``emergencyNoticeTtl``."""
    if vault is None or vault.status != ACTIVE:
        return False
    if not (height - rp.ref_window <= ref <= height - 1) or ref < rp.start_height:
        return False
    if snap is None or not snap.armed:
        return False
    if bundle is None or not bundle.ok:
        return False
    xc, ac = _opt(snap.x_claim), _opt(bundle.a_claim)
    if xc is None or ac is None:
        return False
    if not K.is_underwater(vault.collateral_zat, min(xc, ac), vault.minted_cents, rp.emergency_ratio_bps):
        return False
    return not (standing_height is not None and height - standing_height <= rp.emergency_notice_ttl)


def path_open(vault: VaultRecord, height: int, path: Literal["owner", "claim"]) -> bool:
    """The vault script (script.cpp:79-90) with IsFinalTx: a spend with ``nLockTime = lockHeight``
    (owner) / ``claimHeight`` (claim) confirms only in a block whose height exceeds it. There is no
    liquidation before ``claimHeight`` (fact 1.5-1)."""
    return height > (vault.lock_height if path == "owner" else vault.claim_height)


def wallet_collateral(rp: RuleParams, cents, term_class, sigma_mult_bps, p_mint, buffer_bps=0) -> np.ndarray:
    """``vout[0]`` of a wallet-built MINT (txbuilder.cpp:1095-1103 MintCollateral):
    ``max(RequiredCollateralRounded(...), 4·feeMin)`` rounded up to 1,000 zat, plus a persona
    buffer of ``buffer_bps`` (rounded up to 1,000 again). Vectorised; ``UNDEF`` when unsatisfiable
    (K14) or above MAX_MONEY."""
    c = np.asarray(cents, dtype=np.int64)
    tc = np.asarray(term_class, dtype=np.int64)
    base = np.asarray(rp.base_ratio, dtype=np.int64)[tc]
    mr = V.min_ratio_bps(base, np.asarray(sigma_mult_bps, dtype=np.int64))
    req = V.required_zat(c, mr, np.asarray(p_mint, dtype=np.int64))
    bad = req == UNDEF
    r = _round_up(np.where(bad, 0, req), 1000)
    bad |= r > MAX_MONEY
    coll = _round_up(np.maximum(r, 4 * rp.fee_min), 1000)
    buf = np.asarray(buffer_bps, dtype=np.int64)
    extra = (coll // BPS) * buf + ((coll % BPS) * buf + BPS - 1) // BPS
    coll = _round_up(coll + np.where(buf > 0, extra, 0), 1000)
    bad |= coll > MAX_MONEY
    return np.where(bad, UNDEF, coll)


def wallet_collateral_int(
    rp: RuleParams, cents: int, term_class: int, sigma_mult_bps: int, p_mint: int, buffer_bps: int = 0
) -> int:
    """Scalar :func:`wallet_collateral` in Python integers (``-1`` when unsatisfiable)."""
    if p_mint <= 0:
        return -1
    mr = K.min_ratio_bps(rp.base_ratio[term_class], sigma_mult_bps)
    req = K.required_zat_rounded(cents, mr, p_mint)
    if req is None:
        return -1
    coll = max(req, 4 * rp.fee_min)
    coll += -coll % 1000
    if buffer_bps > 0:
        coll += -((-coll * buffer_bps) // BPS)
        coll += -coll % 1000
    return coll if coll <= MAX_MONEY else -1


def _round_up(x: np.ndarray, g: int) -> np.ndarray:
    x = np.asarray(x, dtype=np.int64)
    return x + (-x) % g


@dataclass
class Totals:
    """The ``Totals`` record (view.h; reference ``Totals``)."""

    supply_cents: int = 0
    collateral_zat: int = 0
    active_vaults: int = 0
    void_vaults: int = 0
    closed_vaults: int = 0
    claimed_vaults: int = 0
    unbacked_cents: int = 0

    def as_dict(self) -> dict:
        return {
            "supplyCents": self.supply_cents,
            "collateralZat": self.collateral_zat,
            "activeVaults": self.active_vaults,
            "voidVaults": self.void_vaults,
            "closedVaults": self.closed_vaults,
            "claimedVaults": self.claimed_vaults,
            "unbackedCents": self.unbacked_cents,
        }


class VaultBook:
    """The exact sequential state machine of the vault table and Totals (state.cpp:384-438,
    578-643, 869-892). The caller supplies each transaction's snapshot view."""

    def __init__(self, params: Mapping | RuleParams):
        self.rp = RuleParams.of(params)
        self.vaults: list[VaultRecord] = []
        self.totals = Totals()
        self.notices: dict[int, tuple[int, int]] = {}  #: vault id → (notice height, notice refHeight)

    def mint(self, height: int, tx: MintTx, snap: Snap | None) -> tuple[int, str]:
        """Apply a MINT (wallet-built: ``vout[0]`` is P2SH, so a failing mint becomes VOID).
        Returns ``(vault id, verdict)``."""
        v = mint_verdict(self.rp, height, tx, snap, self.totals.supply_cents)
        rec = VaultRecord(
            tx.term_class,
            tx.lock_height,
            tx.lock_height + self.rp.grace,
            tx.collateral_zat,
            tx.cents,
            height,
            tx.ref_height,
        )
        if v == OK:
            if snap is not None and snap.eligible:
                rec.fee_paid_zat = int(tx.fee_zat or 0)
            self.totals.supply_cents += tx.cents
            self.totals.collateral_zat += tx.collateral_zat
            self.totals.active_vaults += 1
        else:
            rec.status = VOID
            rec.void_reason = v
            self.totals.void_vaults += 1
        self.vaults.append(rec)
        return len(self.vaults) - 1, v

    def spend(
        self, height: int, vid: int, tx: SpendTx, snap: Snap | None, *, enforcing: bool = True
    ) -> tuple[RedOutcome, bool]:
        """Apply a spend of ACTIVE vault ``vid``. A failing spend is *mined* only when enforcement is
        off (BLK-1 otherwise rejects the block); then the vault closes unbacked. Returns
        ``(outcome, mined)``."""
        rec = self.vaults[vid]
        if rec.status != ACTIVE:
            raise ValueError(f"vault {vid} is not ACTIVE")
        notice = self.notices.get(vid)
        out = red_verdict(self.rp, height, rec, tx, snap, None if notice is None else notice[1])
        burned = int(tx.yed_in) - (sum(tx.assigned) if out.verdict == OK else 0)
        if out.verdict == OK:
            rec.status = CLOSED if tx.path == "owner" else CLAIMED
            rec.fee_paid_zat = int(tx.fee_zat or 0) if (snap is not None and snap.eligible) else 0
            if tx.path == "owner":
                self.totals.closed_vaults += 1
            else:
                self.totals.claimed_vaults += 1
        elif enforcing:
            return out, False
        else:
            rec.status = CLOSED
            rec.unbacked = burned < rec.minted_cents
            self.totals.unbacked_cents += max(0, rec.minted_cents - burned)
            self.totals.closed_vaults += 1
        rec.close_height = height
        rec.burned_cents = burned
        self.totals.supply_cents -= burned
        self.totals.collateral_zat -= rec.collateral_zat
        self.totals.active_vaults -= 1
        self.notices.pop(vid, None)
        return out, True

    def void_release(self, height: int, vid: int) -> None:
        """IN-2 for a VOID vault (K3): an ordinary spend closes it, nothing burned."""
        rec = self.vaults[vid]
        if rec.status != VOID:
            raise ValueError(f"vault {vid} is not VOID")
        rec.status = CLOSED
        rec.close_height = height
        rec.unbacked = False
        self.totals.void_vaults -= 1
        self.totals.closed_vaults += 1

    def notice(self, height: int, vid: int, ref: int, snap: Snap | None, bundle: Bundle | None) -> bool:
        """NOT-1; stores ``(height, ref)`` when it holds."""
        st = self.notices.get(vid)
        ok = notice_verdict(
            self.rp, height, self.vaults[vid], ref, snap, bundle, None if st is None else st[0]
        )
        if ok:
            self.notices[vid] = (int(height), int(ref))
        return ok


# ---------------------------------------------------------------------------------------------------
# 2. Timelines and first-passage searches


class _Lift:
    """Sparse table over a 1-D series for vectorised first-passage queries: ``kind="min"`` finds the
    first ``t ≥ t0`` with ``x[t] < level``; ``kind="max"`` the first with ``x[t] ≥ level``
    (``n`` when none). Binary lifting: O(log n) numpy steps for any number of queries."""

    def __init__(self, x: np.ndarray, kind: Literal["min", "max"]):
        x = np.asarray(x)
        self.n = int(x.shape[0])
        self.kind = kind
        op = np.minimum if kind == "min" else np.maximum
        self.lv = [x]
        k = 1
        while (1 << k) <= self.n:
            prev = self.lv[-1]
            h = 1 << (k - 1)
            self.lv.append(op(prev[: self.n - (1 << k) + 1], prev[h : h + self.n - (1 << k) + 1]))
            k += 1

    def first(self, t0, level) -> np.ndarray:
        pos = np.clip(np.asarray(t0, dtype=np.int64), 0, self.n).copy()
        lvl = np.asarray(level)
        for k in range(len(self.lv) - 1, -1, -1):
            size = 1 << k
            fits = pos + size <= self.n
            idx = np.minimum(pos, self.n - size)
            v = self.lv[k][idx]
            skip = fits & ((v >= lvl) if self.kind == "min" else (v < lvl))
            pos = pos + np.where(skip, size, 0)
        return pos


def _trailing_min(a: np.ndarray, w: int) -> np.ndarray:
    """``out[i] = min(a[max(i − w + 1, 0) : i + 1])`` (scipy's O(n) filter, window trailing)."""
    from scipy.ndimage import minimum_filter1d

    if w <= 1:
        return a.copy()
    return minimum_filter1d(a, size=w, mode="constant", cval=_BIG, origin=(w - 1) // 2)


def _next_true(mask: np.ndarray) -> np.ndarray:
    """``out[t]`` = the first ``t' ≥ t`` with ``mask[t']`` (``n`` if none); length ``n + 1``."""
    n = mask.shape[0]
    idx = np.where(mask, np.arange(n), n)
    out = np.empty(n + 1, dtype=np.int64)
    out[n] = n
    out[:n] = np.minimum.accumulate(idx[::-1])[::-1]
    return out


def _run_length(mask: np.ndarray) -> np.ndarray:
    """Length of the run of True ending at each index."""
    m = np.asarray(mask, dtype=bool)
    idx = np.arange(m.shape[0])
    last_false = np.maximum.accumulate(np.where(~m, idx, -1))
    return np.where(m, idx - last_false, 0)


@dataclass
class Timeline:
    """One path's per-step snapshot fields as the vault book reads them (all ``(n,)``).

    ``heights[t]`` is the block height of snapshot ``t``; ``conf[t]`` the confirmation height of a
    transaction at step ``t`` whose refHeight candidates are steps ``[t − ref_steps, t − 1]``.
    ``base_halt`` is the haltMask *without* HALT-2 (the book adds HALT-2 from its own totals).
    ``enforcing[t]`` is ACT-5 for a transaction at step ``t``; ``abandoned[t]`` the abandonment
    predicate at the tip before it."""

    resolution: str
    heights: np.ndarray
    conf: np.ndarray
    true_price: np.ndarray
    x_mint: np.ndarray
    x_claim: np.ndarray
    p_fast: np.ndarray
    sigma_mult_bps: np.ndarray
    base_halt: np.ndarray
    active: np.ndarray
    issued_zat: np.ndarray
    eligible: np.ndarray
    armed: np.ndarray
    a_mint: np.ndarray
    a_claim: np.ndarray
    enforcing: np.ndarray
    abandoned: np.ndarray
    ref_steps: int
    step_blocks: int
    wallet_ref_lag: int = 2  #: steps between the tip and the wallet's R (block mode: REF_LAG)

    @property
    def n(self) -> int:
        return int(self.heights.shape[0])


def _enforcing_series(params: Mapping, heights, conf, active, enf_halt) -> np.ndarray:
    n = heights.shape[0]
    out = np.zeros(n, dtype=bool)
    eu = int(params["enforceUntilHeight"])
    if n > 1:
        out[1:] = active[:-1] & ~enf_halt[:-1]
        if eu > 0:
            out &= conf <= eu
    return out


def _abandoned_series(params: Mapping, enf_halt, step_blocks: int) -> np.ndarray:
    n = enf_halt.shape[0]
    ab = int(params["abandonBlocks"])
    run = _run_length(enf_halt) * step_blocks
    out = np.zeros(n, dtype=bool)
    if n > 1:
        out[1:] = run[:-1] >= max(ab, 1)
    return out


def timeline_from_blocks(params: Mapping, inputs, series, path: int = 0) -> Timeline:
    """Block-mode timeline of one path of an engine run (column ``j`` = height ``start + j``)."""
    p = path
    heights = series.heights.astype(np.int64)
    active = series.activation_status[p] == E.ACTIVE
    enf = np.asarray(series.enforcement_halt[p], dtype=bool)
    elig = FEES.eligible_nonempty_series(
        params, inputs.tag_price[p], inputs.tag_present[p], inputs.tag_pool[p], series.pinned_pools[p]
    )[0]
    return Timeline(
        resolution="block",
        heights=heights,
        conf=heights.copy(),
        true_price=np.asarray(series.true_price[p], dtype=np.int64),
        x_mint=series.x_mint[p],
        x_claim=series.x_claim[p],
        p_fast=series.p_fast[p],
        sigma_mult_bps=series.sigma_mult_bps[p],
        base_halt=(series.halt_mask[p].astype(np.int64) & ~HALT_GLOBAL_RATIO),
        active=active,
        issued_zat=np.asarray(series.issued_zat[p], dtype=np.int64),
        eligible=elig,
        armed=np.asarray(series.armed[p], dtype=bool),
        a_mint=series.a_mint[p],
        a_claim=series.a_claim[p],
        enforcing=_enforcing_series(params, heights, heights, active, enf),
        abandoned=_abandoned_series(params, enf, 1),
        ref_steps=int(params["refWindow"]),
        step_blocks=1,
        wallet_ref_lag=int(params["DEFAULT_REF_LAG"]),
    )


# ---------------------------------------------------------------------------------------------------
# 3. The book runner


@dataclass(frozen=True)
class BookOptions:
    """Switches of :func:`run_book`."""

    emergency: bool = True  #: model NOT-1 notices and RED-4(b) claims where ARMED
    supply_cap: bool = True  #: apply MINT-6 (False isolates solvency studies from the cap)
    record_events: bool = False  #: keep the per-transaction event log (tests, devnet replay)
    traj_every: int = 1  #: trajectory sampling stride, steps
    max_ref_tries: int = 40  #: snapshots a minter tries when the preferred one is refused


@dataclass
class PathBook:
    """One path's vault book: a row per mint attempt plus the totals trajectory."""

    vaults: dict[str, np.ndarray]
    traj_steps: np.ndarray
    supply_cents: np.ndarray
    collateral_zat: np.ndarray
    unbacked_cents: np.ndarray
    counters: dict
    events: list[dict] | None = None
    totals: Totals | None = None

    def series_full(self, n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Totals after every step ``0 … n−1`` (requires ``traj_every = 1``)."""
        return self.supply_cents[:n], self.collateral_zat[:n], self.unbacked_cents[:n]


_VAULT_INT_COLS = (
    "step",
    "cents",
    "term_class",
    "lock_blocks",
    "buffer_bps",
    "owner_kind",
    "owner_delay",
    "outcome",
    "ref_step",
    "ref_height",
    "mint_height",
    "collateral_zat",
    "lock_height",
    "claim_height",
    "p_mint",
    "sigma_mult_bps",
    "min_ratio_bps",
    "tp_mint",
    "lock_open_step",
    "claim_open_step",
    "tp_lock_open",
    "tp_claim_open",
    "close_step",
    "close_kind",
    "close_height",
    "status",
    "residual_zat",
    "pool_fee_mint",
    "attest_fee_mint",
    "pool_fee_close",
    "attest_fee_close",
    "claimant_receive_zat",
    "tp_close",
    "unbacked_cents",
    "notice_step",
    "planned_step",
)
_VAULT_BOOL_COLS = ("bad_at_lock", "bad_at_claim_open", "owner_missed", "claim_b", "plan_mismatch")
_VAULT_FLOAT_COLS = ("claimant_profit_usd", "shortfall_usd", "value_at_mint_usd")
_VAULT_STR_COLS = ("reason",)


def _empty_table(m: int) -> dict[str, np.ndarray]:
    t: dict[str, np.ndarray] = {c: np.full(m, -1, dtype=np.int64) for c in _VAULT_INT_COLS}
    t.update({c: np.zeros(m, dtype=bool) for c in _VAULT_BOOL_COLS})
    t.update({c: np.full(m, np.nan) for c in _VAULT_FLOAT_COLS})
    t["reason"] = np.full(m, "", dtype=object)
    for c in (
        "residual_zat",
        "pool_fee_mint",
        "attest_fee_mint",
        "pool_fee_close",
        "attest_fee_close",
        "claimant_receive_zat",
        "unbacked_cents",
    ):
        t[c][:] = 0
    return t


class _Ctx:
    """Per-path derived arrays shared by the planners."""

    def __init__(self, params: Mapping, tl: Timeline, agents: AG.AgentsConfig, options: BookOptions):
        self.params = params
        self.rp = RuleParams.of(params)
        self.tl = tl
        self.agents = agents
        self.opt = options
        n = tl.n
        self.n = n
        xm, xc = tl.x_mint.astype(np.int64), tl.x_claim.astype(np.int64)
        am, ac = tl.a_mint.astype(np.int64), tl.a_claim.astype(np.int64)
        arm = tl.armed.astype(bool)
        self.pm_eff = np.where(arm, np.where((xm > 0) & (am > 0), np.minimum(xm, am), UNDEF), xm)
        self.pc_eff = np.where(arm, np.where((xc > 0) & (ac > 0), np.maximum(xc, ac), UNDEF), xc)
        self.pe_eff = np.where(arm & (xc > 0) & (ac > 0), np.minimum(xc, ac), UNDEF)
        pf = tl.p_fast.astype(np.int64)
        fast = np.where(pf > 0, pf, xm)
        m10 = np.abs(fast - am) * BPS <= max(0, self.rp.diverge_bps_attest) * np.minimum(fast, am)
        self.base_ok = (
            tl.active & ((tl.base_halt & ~HALT_GLOBAL_RATIO) == 0) & (xm > 0) & (~arm | ((am > 0) & m10))
        )
        self.tp = tl.true_price.astype(np.int64)
        self.W = max(1, int(tl.ref_steps))
        pc_big = np.where(self.pc_eff > 0, self.pc_eff, _BIG)
        self.pc_big = pc_big
        # adversarial claimant: the lowest pClaim among the window's snapshots (UNDEF never counts)
        if self.W == 1:
            win = np.full(n, _BIG, dtype=np.int64)
            win[1:] = pc_big[:-1]
        else:
            win = np.full(n, _BIG, dtype=np.int64)
            if n > 1:
                tr = _trailing_min(pc_big, self.W)
                win[1:] = tr[:-1]
        self.pc_win = win
        self._lift_pc = None
        self._lift_tp = None
        self.not_enf_next = _next_true(~tl.enforcing.astype(bool))
        self.aband_next = _next_true(tl.abandoned.astype(bool))
        self.armed_any = bool(arm.any())
        self.eligible = tl.eligible.astype(bool)
        cap_bps = self.rp.supply_cap_bps if options.supply_cap else 0
        self.cap = V.supply_cap_cents(tl.issued_zat.astype(np.int64), xm, cap_bps)
        sg = tl.sigma_mult_bps.astype(np.int64)
        self.min_ratio = [V.min_ratio_bps(np.int64(b), sg) for b in self.rp.base_ratio]

    @property
    def lift_pc(self) -> _Lift:
        if self._lift_pc is None:
            self._lift_pc = _Lift(self.pc_win, "min")
        return self._lift_pc

    @property
    def lift_tp(self) -> _Lift:
        if self._lift_tp is None:
            self._lift_tp = _Lift(self.tp, "max")
        return self._lift_tp

    def step_at_or_after_conf(self, height) -> np.ndarray:
        """First step whose confirmation height is ≥ ``height`` (``n`` if none)."""
        return np.searchsorted(self.tl.conf, np.asarray(height, dtype=np.int64), side="left")


def _ceil_div_arr(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Exact ``ceil(a / b)`` for int64 ``a ≥ 0``, ``b > 0``."""
    return -((-a) // b)


def _underwater_level(minted, threshold_bps: int, coll) -> np.ndarray:
    """``U`` with ``is_underwater(coll, p, minted, thr) ⇔ 0 < p < U`` (``U = ceil(minted·thr·COIN /
    coll)``; exact, with a Python-int fallback past int64)."""
    m = np.asarray(minted, dtype=np.int64)
    c = np.maximum(np.asarray(coll, dtype=np.int64), 1)
    if int(m.max(initial=0)) <= (2**62) // max(threshold_bps * COIN, 1):
        return _ceil_div_arr(m * threshold_bps * COIN, c)
    return np.array(
        [min(-((-int(x) * threshold_bps * COIN) // int(y)), _BIG) for x, y in zip(m, c, strict=True)],
        dtype=np.int64,
    )


def _plan_closures(ctx: _Ctx, coll, cents, lock, claim_h, kind, delay, mint_step) -> dict[str, np.ndarray]:
    """Vectorised lifecycle plan of accepted vaults (independent of other vaults): the first step at
    which the owner redeems, a defector / honest owner sweeps, a claimant claims (RED-4(a); RED-4(b)
    with a notice where ARMED) or a thief takes the claim path while enforcement is off — the
    earliest wins (ties: owner, sweep, claim, thief)."""
    rp, ag = ctx.rp, ctx.agents
    n = ctx.n
    m = coll.shape[0]
    coll = coll.astype(np.int64)
    cents = cents.astype(np.int64)
    never = np.full(m, n, dtype=np.int64)
    lock_open = ctx.step_at_or_after_conf(lock + 1)
    claim_open = ctx.step_at_or_after_conf(claim_h + 1)
    lost = kind == AG.LOST
    owner_h = np.where(lost, _BIG // 2, lock + 1 + np.minimum(delay, _BIG // 4))
    owner_ready = np.maximum(ctx.step_at_or_after_conf(owner_h), mint_step + 1)
    # owner redeem: first step ≥ ready with the redemption worth it at the true price
    fee = V.fee_zat(coll, rp.fee_min, rp.fee_bps)
    net = coll - fee - ag.tx_fee_zat
    lvl = AG.redeem_price_floor(net, cents, ag.owner, ag.market)
    lvl_i = np.where(np.isfinite(lvl), np.ceil(np.minimum(lvl, 9e18)), _BIG).astype(np.int64)
    redeem = np.where(lost | (owner_ready >= n), n, ctx.lift_tp.first(np.minimum(owner_ready, n), lvl_i))
    # sweeps: defectors whenever ACT-5 is off; honest owners under the abandonment predicate
    sweep = never.copy()
    rdy = np.minimum(owner_ready, n)
    dfc = kind == AG.DEFECTOR
    sweep = np.where(dfc, ctx.not_enf_next[rdy], sweep)
    if ag.owner.sweep_on_abandon:
        hon = kind == AG.HONEST
        sweep = np.where(hon, np.minimum(sweep, ctx.aband_next[rdy]), sweep)
    sweep = np.where(lost, n, sweep)
    # claims
    claim_a = never.copy()
    claim_b = never.copy()
    b_ref = np.full(m, -1, dtype=np.int64)
    b_notice_step = np.full(m, -1, dtype=np.int64)
    b_notice_ref = np.full(m, -1, dtype=np.int64)
    thief = never.copy()
    cl = ag.claimant
    copen = np.maximum(np.minimum(claim_open, n), mint_step + 1)
    if cl.enabled:
        U = _underwater_level(cents, rp.claim_threshold_bps, coll)
        afee = V.attest_fee_zat(fee, rp.attest_fee_bps) if ctx.armed_any else np.zeros(m, np.int64)
        cost = fee + afee + ag.tx_fee_zat
        Q = AG.claim_price_floor(coll, cost, cents, cl, ag.market)
        Q_i = np.where(np.isfinite(Q), np.ceil(np.minimum(Q, 9e18)), _BIG).astype(np.int64)
        claim_a = _joint_first(ctx, copen, U, Q_i)
        if cl.depth_usd:
            claim_a = _depth_recheck(ctx, claim_a, U, Q_i, coll, cost, cents)
        if ctx.opt.emergency and cl.use_emergency and ctx.armed_any:
            for i in range(m):
                res = _plan_claim_b(
                    ctx,
                    int(coll[i]),
                    int(cents[i]),
                    int(copen[i]),
                    int(mint_step[i]),
                    int(min(claim_a[i], n)),
                    int(cost[i]),
                )
                if res is not None:
                    claim_b[i], b_ref[i], b_notice_step[i], b_notice_ref[i] = res
    if cl.thief_when_unenforced:
        thief = ctx.not_enf_next[copen]
    # a defector prefers the sweep (no burn) whenever it is available no later than its redeem
    redeem = np.where(dfc & (sweep <= redeem), n, redeem)
    cand = np.stack([redeem, sweep, np.minimum(claim_a, claim_b), thief])
    kind_close = np.argmin(cand, axis=0)  # first index on ties: owner, sweep, claim, thief
    close = cand[kind_close, np.arange(m)]
    is_b = (kind_close == 2) & (claim_b < claim_a)
    kind_out = np.select(
        [kind_close == 0, kind_close == 1, kind_close == 2, kind_close == 3],
        [K_OWNER, K_SWEEP, K_CLAIM, K_THIEF],
    )
    kind_out = np.where(close >= n, K_OPEN, kind_out)
    return {
        "close_step": np.where(close >= n, -1, close),
        "close_kind": kind_out,
        "claim_b": is_b & (close < n),
        "b_ref": b_ref,
        "notice_step": np.where(is_b & (close < n), b_notice_step, -1),
        "notice_ref": b_notice_ref,
        "lock_open_step": np.where(lock_open >= n, -1, lock_open),
        "claim_open_step": np.where(claim_open >= n, -1, claim_open),
    }


def _joint_first(ctx: _Ctx, t0, U, Q, max_iter: int = 64) -> np.ndarray:
    """First step ``t ≥ t0`` with ``pc_win[t] < U`` (underwater at some R of the window) and
    ``tp[t] ≥ Q`` (the claim pays), by alternating the two lifted first-passage searches."""
    n = ctx.n
    t = np.minimum(np.asarray(t0, dtype=np.int64), n).copy()
    out = np.full(t.shape, n, dtype=np.int64)
    act = t < n
    idx = np.nonzero(act)[0]
    for _ in range(max_iter):
        if idx.size == 0:
            break
        a = ctx.lift_pc.first(t[idx], U[idx])
        b = ctx.lift_tp.first(a, Q[idx])
        found = (a < n) & (b == a)
        gone = (a >= n) | (b >= n)
        out[idx[found]] = a[found]
        t[idx] = np.where(found | gone, n, b)
        idx = idx[~(found | gone)]
    for i in idx.tolist():  # rare: very long alternations
        s = int(t[i])
        hit = np.nonzero((ctx.pc_win[s:] < U[i]) & (ctx.tp[s:] >= Q[i]))[0]
        out[i] = s + int(hit[0]) if hit.size else n
    return out


def _depth_recheck(ctx: _Ctx, claim_a, U, Q, coll, cost, cents) -> np.ndarray:
    """With a depth model the price floor above used base slippage only: re-check the found step's
    profit with the size impact and search on when it fails."""
    cl, mk = ctx.agents.claimant, ctx.agents.market
    n = ctx.n
    out = claim_a.copy()
    for _ in range(32):
        idx = np.nonzero(out < n)[0]
        if idx.size == 0:
            break
        prof = AG.claim_profit_usd(coll[idx], cost[idx], ctx.tp[out[idx]], cents[idx], cl, mk)
        bad = prof < cents[idx] / 100 * cl.min_profit_bps / BPS
        if not bad.any():
            break
        j = idx[bad]
        out[j] = _joint_first(ctx, out[j] + 1, U[j], Q[j])
    return out


def _plan_claim_b(
    ctx: _Ctx, coll: int, cents: int, claim_open: int, mint_step: int, a_step: int, cost: int
) -> tuple[int, int, int, int] | None:
    """RED-4(b) for one vault: the claimant posts NOT-1 as soon as pEmerg is underwater at
    ``emergencyRatioBps`` (only when it can still be persisted at a claimable R), then claims at the
    first step whose window holds an R with ``persist ≤ R − noticeR ≤ ttl``, pEmerg underwater and
    a profitable payout (claimantMax at margin 10^4 and pClaim; residual to the owner). Returns
    ``(claim step, claim ref step, notice step, notice ref step)`` or ``None``."""
    rp, tl = ctx.rp, ctx.tl
    n, W = ctx.n, ctx.W
    if a_step <= claim_open or claim_open >= n:
        return None
    Ue = int(_underwater_level(np.array([cents]), rp.emergency_ratio_bps, np.array([coll]))[0])
    uw = (ctx.pe_eff > 0) & (ctx.pe_eff < Ue)
    if not uw[:a_step].any():
        return None
    nt = _next_true(uw)
    heights = tl.heights
    lo_h = int(tl.conf[claim_open]) - 1 - rp.ref_window - rp.emergency_notice_ttl - tl.step_blocks
    t_n = max(mint_step + 1, int(np.searchsorted(heights, lo_h, side="left")))
    cl, mk = ctx.agents.claimant, ctx.agents.market
    while t_n < min(n, a_step):
        r_n = int(nt[max(t_n - W, 0)])
        if r_n >= n - 1:
            return None
        t_post = max(r_n + 1, t_n)
        if t_post >= min(n, a_step):
            return None
        hn = int(heights[r_n])
        lo = int(np.searchsorted(heights, hn + rp.emergency_persist, side="left"))
        hi = int(np.searchsorted(heights, hn + rp.emergency_notice_ttl, side="right"))
        R = np.arange(max(lo, claim_open - W), min(hi, n - 1))
        if R.size:
            tR = np.maximum(np.maximum(R + 1, claim_open), t_post + 1)
            ok = uw[R] & tl.armed[R] & (tR - R <= W) & (tR < n) & (tR < a_step)
            if ok.any():
                R, tR = R[ok], tR[ok]
                pc = ctx.pc_eff[R]
                cmax = V.claimant_max_zat(np.full(R.shape, cents), BPS, pc)
                resid = V.residual_zat(np.full(R.shape, coll), cmax)
                receive = np.where(resid >= rp.residual_min_zat, coll - resid, coll)
                prof = AG.claim_profit_usd(receive, cost, ctx.tp[tR], cents, cl, mk)
                good = prof >= cents / 100 * cl.min_profit_bps / BPS
                if good.any():
                    gi = np.nonzero(good)[0]
                    best_t = int(tR[gi].min())
                    cand = gi[tR[gi] == best_t]
                    k = cand[np.argmin(pc[cand])]  # the lowest pClaim pays the claimant most
                    return best_t, int(R[k]), t_post, r_n
        # the notice expires: a new one once height − notice.height > ttl
        t_n = int(ctx.step_at_or_after_conf(int(tl.conf[t_post]) + rp.emergency_notice_ttl + 1))
    return None


def _snap(ctx: _Ctx, r: int, supply: int, coll: int) -> Snap:
    tl, rp = ctx.tl, ctx.rp
    halt = int(tl.base_halt[r]) & ~HALT_GLOBAL_RATIO
    xm = int(tl.x_mint[r])
    if xm > 0 and supply > 0:
        gr = K.global_ratio_bps(coll, xm, supply)
        if gr is not None and gr < rp.global_ratio_halt_bps:
            halt |= HALT_GLOBAL_RATIO
    return Snap(
        height=int(tl.heights[r]),
        active=bool(tl.active[r]),
        halt_mask=halt,
        x_mint=_opt(xm),
        x_claim=_opt(tl.x_claim[r]),
        p_fast=_opt(tl.p_fast[r]),
        sigma_mult_bps=int(tl.sigma_mult_bps[r]),
        issued_zat=int(tl.issued_zat[r]) if ctx.opt.supply_cap else 0,
        armed=bool(tl.armed[r]),
        eligible=bool(tl.eligible[r]),
    )


def _bundle_at(tl: Timeline, r: int) -> Bundle | None:
    if not tl.armed[r]:
        return None
    return Bundle(_opt(tl.a_mint[r]), _opt(tl.a_claim[r]))


def run_book(
    params: Mapping,
    tl: Timeline,
    attempts: AG.MintAttempts,
    agents: AG.AgentsConfig | None = None,
    *,
    options: BookOptions | None = None,
) -> PathBook:
    """Run one path's vault book (see the module docstring). Every state change goes through
    :class:`VaultBook` with the exact rule functions; ``counters["plan_mismatch"]`` counts planned
    honest spends the rules refused (0 by construction; tests assert it)."""
    agents = agents or AG.AgentsConfig()
    opt = options or BookOptions()
    ctx = _Ctx(params, tl, agents, opt)
    rp, n, W = ctx.rp, ctx.n, ctx.W
    rp_eff = rp if opt.supply_cap else _no_cap(rp)
    book = VaultBook(rp_eff)
    m = len(attempts)
    tab = _empty_table(m)
    for c, src in (
        ("step", "step"),
        ("cents", "cents"),
        ("term_class", "term_class"),
        ("lock_blocks", "lock_blocks"),
        ("buffer_bps", "buffer_bps"),
        ("owner_kind", "owner_kind"),
        ("owner_delay", "owner_delay_blocks"),
    ):
        tab[c][:] = getattr(attempts, src)
    events: list[dict] | None = [] if opt.record_events else None
    counters = {
        "attempts": m,
        "refused": 0,
        "void": 0,
        "active": 0,
        "plan_mismatch": 0,
        "notices": 0,
        "reasons": {},
    }
    steps = attempts.step.astype(np.int64)
    # ---- planning: refHeight candidates (newest first) and preference
    adversarial = agents.minter.ref_choice == "adversarial"
    if adversarial:
        cand = steps[:, None] - 1 - np.arange(W)[None, :]
    else:
        lag = tl.wallet_ref_lag if tl.resolution == "block" else 0
        if agents.minter.ref_lag is not None and tl.resolution == "block":
            lag = int(agents.minter.ref_lag)
        cand = (steps - 1 - lag)[:, None]
    valid = (cand >= 0) & (cand < n)
    cc = np.clip(cand, 0, max(n - 1, 0))
    okc = valid & ctx.base_ok[cc] if n else valid
    pref = AG.ref_preference(np.where(okc, ctx.pm_eff[cc], -1), okc, adversarial=adversarial)
    cand_sorted = np.take_along_axis(cand, pref, axis=1)
    ok_sorted = np.take_along_axis(okc, pref, axis=1)
    r0 = np.clip(cand_sorted[:, 0], 0, max(n - 1, 0)) if m else np.zeros(0, np.int64)
    coll0 = (
        wallet_collateral(
            rp,
            attempts.cents,
            attempts.term_class,
            ctx.tl.sigma_mult_bps[r0],
            ctx.pm_eff[r0],
            attempts.buffer_bps,
        )
        if m
        else np.zeros(0, np.int64)
    )
    lock0 = tl.heights[r0] + attempts.lock_blocks if m else np.zeros(0, np.int64)
    plan_ok = ok_sorted[:, 0] & (coll0 != UNDEF) if m else np.zeros(0, bool)
    plan = {
        k: np.full(m, -1, dtype=np.int64)
        for k in (
            "close_step",
            "close_kind",
            "b_ref",
            "notice_step",
            "notice_ref",
            "lock_open_step",
            "claim_open_step",
        )
    }
    plan["claim_b"] = np.zeros(m, dtype=bool)
    pi = np.nonzero(plan_ok)[0]
    if pi.size:
        pl = _plan_closures(
            ctx,
            coll0[pi],
            attempts.cents[pi],
            lock0[pi],
            lock0[pi] + rp.grace,
            attempts.owner_kind[pi],
            attempts.owner_delay_blocks[pi],
            steps[pi],
        )
        for k, v in pl.items():
            plan[k][pi] = v
    # ---- sequential pass
    hist_steps: list[int] = [-1]
    hist: list[tuple[int, int, int]] = [(0, 0, 0)]
    heap: list[tuple[int, int, int, int]] = []  # (step, kind order, seq, vault row)
    vid_of_row: dict[int, int] = {}
    row_of_vid: dict[int, int] = {}
    seq = 0

    def record(s: int) -> None:
        t = book.totals
        val = (t.supply_cents, t.collateral_zat, t.unbacked_cents)
        if hist_steps[-1] == s:
            hist[-1] = val
        else:
            hist_steps.append(s)
            hist.append(val)

    def totals_at(s: int) -> tuple[int, int, int]:
        return hist[bisect.bisect_right(hist_steps, s) - 1]

    def snap_at(r: int) -> Snap:
        sup, col, _ = totals_at(r)
        return _snap(ctx, r, sup, col)

    def push(s: int, kind: int, row: int) -> None:
        nonlocal seq
        if 0 <= s < n:
            heapq.heappush(heap, (s, 0 if kind == K_NOTICE else 1, seq, row))
            seq += 1

    def apply(s: int, row: int, is_notice: bool) -> None:
        vid = vid_of_row[row]
        rec = book.vaults[vid]
        H = int(tl.conf[s])
        if is_notice:
            r = int(plan_row["notice_ref"][row])
            if rec.status == ACTIVE and book.notice(
                H, vid, int(tl.heights[r]), snap_at(r), _bundle_at(tl, r)
            ):
                counters["notices"] += 1
                if events is not None:
                    events.append(
                        {
                            "step": s,
                            "height": H,
                            "kind": "notice",
                            "row": row,
                            "vid": vid,
                            "ref_height": int(tl.heights[r]),
                        }
                    )
            return
        kind = int(tab["close_kind"][row])
        if rec.status == VOID:
            book.void_release(H, vid)
            tab["status"][row] = rec.status
            if events is not None:
                events.append({"step": s, "height": H, "kind": "void-release", "row": row, "vid": vid})
            return
        if rec.status != ACTIVE:
            return
        enforcing = bool(tl.enforcing[s])
        if kind == K_OWNER:
            r = max(s - 1 - (tl.wallet_ref_lag if tl.resolution == "block" else 0), 0)
            sn = snap_at(r)
            tx = SpendTx(
                "owner",
                int(tl.heights[r]),
                yed_in=rec.minted_cents,
                fee_zat=rp.fee(rec.collateral_zat) if sn.eligible else None,
            )
        elif kind == K_CLAIM:
            r = int(plan_row["claim_ref"][row])
            sn = snap_at(r)
            b = _bundle_at(tl, r)
            tx = SpendTx(
                "claim",
                int(tl.heights[r]),
                yed_in=rec.minted_cents,
                fee_zat=rp.fee(rec.collateral_zat) if sn.eligible else None,
                bundle=b,
                attest_fee_zat=rp.attest_fee(rec.collateral_zat) if sn.armed else None,
                owner_paid_zat=rec.collateral_zat,
            )
        else:  # K_SWEEP / K_THIEF: no payload, no burn
            r = max(s - 1, 0)
            sn = snap_at(r)
            tx = SpendTx("owner" if kind == K_SWEEP else "claim", int(tl.heights[r]), redeem_payload=False)
        out, mined = book.spend(H, vid, tx, sn, enforcing=enforcing)
        if not mined:
            counters["plan_mismatch"] += 1
            tab["plan_mismatch"][row] = True
            tab["close_step"][row] = -1
            tab["close_kind"][row] = K_OPEN
            if events is not None:
                events.append(
                    {
                        "step": s,
                        "height": H,
                        "kind": "refused-spend",
                        "row": row,
                        "vid": vid,
                        "verdict": out.verdict,
                    }
                )
            return
        record(s)
        tp = int(ctx.tp[s])
        tab["close_height"][row] = H
        tab["status"][row] = rec.status
        tab["tp_close"][row] = tp
        tab["pool_fee_close"][row] = rec.fee_paid_zat
        if kind == K_CLAIM:
            tab["attest_fee_close"][row] = int(tx.attest_fee_zat or 0)
            tab["residual_zat"][row] = out.residual_zat if out.residual_zat >= rp.residual_min_zat else 0
            recv = rec.collateral_zat - int(tab["residual_zat"][row])
            tab["claimant_receive_zat"][row] = recv
            cost = int(tab["pool_fee_close"][row]) + int(tab["attest_fee_close"][row]) + agents.tx_fee_zat
            tab["claimant_profit_usd"][row] = float(
                AG.claim_profit_usd(recv, cost, tp, rec.minted_cents, agents.claimant, agents.market)
            )
            tab["claim_b"][row] = out.claim_path == "b"
        if kind in (K_SWEEP, K_THIEF):
            tab["unbacked_cents"][row] = rec.minted_cents - rec.burned_cents
        if events is not None:
            events.append(
                {
                    "step": s,
                    "height": H,
                    "kind": CLOSE_KIND_NAMES[kind],
                    "row": row,
                    "vid": vid,
                    "ref_height": tx.ref_height,
                    "verdict": out.verdict,
                    "claim_path": out.claim_path,
                    "residual_zat": out.residual_zat,
                    "fee_zat": tx.fee_zat,
                    "path": tx.path,
                    "yed_in": tx.yed_in,
                    "payload": tx.redeem_payload,
                    "attest_fee_zat": tx.attest_fee_zat,
                }
            )

    plan_row = {"notice_ref": plan["notice_ref"], "claim_ref": np.full(m, -1, dtype=np.int64)}
    order = np.argsort(steps, kind="stable")
    for i in order.tolist():
        t = int(steps[i])
        while heap and heap[0][0] <= t:
            s, ko, _sq, row = heapq.heappop(heap)
            apply(s, row, ko == 0)
        if t >= n or t < 1:
            tab["outcome"][i] = O_REFUSED
            tab["reason"][i] = REFUSED
            counters["refused"] += 1
            continue
        H = int(tl.conf[t])
        tip_supply = totals_at(t - 1)[0]
        chosen = None
        first_v = None
        c_i, cents_i = int(attempts.term_class[i]), int(attempts.cents[i])
        # MINT-6 pre-screen of the whole candidate row against the tip supply (exact: a candidate it
        # rejects would fail MINT-6 in mint_verdict too); k = 0 always gets the full verdict
        row = np.clip(cand_sorted[i], 0, n - 1)
        cap_ok = (
            (ctx.cap[row] == UNDEF)
            | (tip_supply + cents_i <= ctx.cap[row])
            | (ctx.min_ratio[c_i][row] >= rp_eff.recap_ratio_bps)
        )
        for k in range(min(cand_sorted.shape[1], opt.max_ref_tries)):
            r = int(cand_sorted[i, k])
            if r < 0 or r >= n:
                continue
            if adversarial and not ok_sorted[i, k] and k > 0:
                break
            if k > 0 and not cap_ok[k]:
                continue
            sn = snap_at(r)
            if k == 0 and plan_ok[i]:
                coll = int(coll0[i])
            else:
                coll = wallet_collateral_int(
                    rp,
                    cents_i,
                    c_i,
                    int(tl.sigma_mult_bps[r]),
                    int(ctx.pm_eff[r]),
                    int(attempts.buffer_bps[i]),
                )
            lock = int(tl.heights[r]) + int(attempts.lock_blocks[i])
            b = _bundle_at(tl, r)
            tx = MintTx(
                int(attempts.term_class[i]),
                int(attempts.cents[i]),
                lock,
                int(tl.heights[r]),
                max(coll, 0),
                fee_zat=rp.fee(max(coll, 0)) if sn.eligible else None,
                bundle=b,
                attest_fee_zat=rp.attest_fee(max(coll, 0)) if sn.armed else None,
            )
            v = mint_verdict(rp_eff, H, tx, sn, tip_supply)
            if first_v is None:
                first_v = v
            if v == OK:
                chosen = (k, r, sn, tx)
                break
        if chosen is None:
            tab["outcome"][i] = O_REFUSED
            tab["reason"][i] = first_v or REFUSED
            counters["refused"] += 1
            counters["reasons"][first_v or REFUSED] = counters["reasons"].get(first_v or REFUSED, 0) + 1
            continue
        k, r, sn, tx = chosen
        vid, v = book.mint(H, tx, sn)
        vid_of_row[i] = vid
        row_of_vid[vid] = i
        record(t)
        tab["ref_step"][i] = r
        tab["ref_height"][i] = int(tl.heights[r])
        tab["mint_height"][i] = H
        tab["collateral_zat"][i] = tx.collateral_zat
        tab["lock_height"][i] = tx.lock_height
        tab["claim_height"][i] = tx.lock_height + rp.grace
        tab["p_mint"][i] = int(ctx.pm_eff[r])
        tab["sigma_mult_bps"][i] = int(tl.sigma_mult_bps[r])
        tab["min_ratio_bps"][i] = K.min_ratio_bps(rp.base_ratio[tx.term_class], int(tl.sigma_mult_bps[r]))
        tab["tp_mint"][i] = int(ctx.tp[t])
        tab["status"][i] = book.vaults[vid].status
        if events is not None:
            events.append(
                {
                    "step": t,
                    "height": H,
                    "kind": "mint",
                    "row": i,
                    "vid": vid,
                    "verdict": v,
                    "ref_height": tx.ref_height,
                    "cents": tx.cents,
                    "term_class": tx.term_class,
                    "lock_height": tx.lock_height,
                    "collateral_zat": tx.collateral_zat,
                    "fee_zat": tx.fee_zat,
                    "attest_fee_zat": tx.attest_fee_zat,
                }
            )
        if v != OK:
            tab["outcome"][i] = O_VOID
            tab["reason"][i] = v
            counters["void"] += 1
            counters["reasons"][v] = counters["reasons"].get(v, 0) + 1
            ready = int(ctx.step_at_or_after_conf(tx.lock_height + 1 + int(attempts.owner_delay_blocks[i])))
            if attempts.owner_kind[i] != AG.LOST:
                tab["close_kind"][i] = K_VOID_RELEASE
                push(max(ready, t + 1), K_VOID_RELEASE, i)
            continue
        tab["outcome"][i] = O_ACTIVE
        counters["active"] += 1
        tab["pool_fee_mint"][i] = book.vaults[vid].fee_paid_zat
        tab["attest_fee_mint"][i] = int(tx.attest_fee_zat or 0) if sn.armed else 0
        if not (k == 0 and plan_ok[i]):  # the plan assumed another snapshot: re-plan this vault
            one = _plan_closures(
                ctx,
                np.array([tx.collateral_zat]),
                attempts.cents[i : i + 1],
                np.array([tx.lock_height]),
                np.array([tx.lock_height + rp.grace]),
                attempts.owner_kind[i : i + 1],
                attempts.owner_delay_blocks[i : i + 1],
                np.array([t]),
            )
            for kk, vv in one.items():
                plan[kk][i] = vv[0]
        for kk in ("close_step", "close_kind", "lock_open_step", "claim_open_step", "notice_step"):
            tab[kk][i] = plan[kk][i]
        tab["planned_step"][i] = plan["close_step"][i]
        cs, ck = int(plan["close_step"][i]), int(plan["close_kind"][i])
        if ck == K_CLAIM:
            plan_row["claim_ref"][i] = (
                int(plan["b_ref"][i])
                if plan["claim_b"][i]
                else _claim_ref_a(ctx, cs, tx.collateral_zat, tx.cents)
            )
            if plan["claim_b"][i]:
                plan_row["notice_ref"][i] = int(plan["notice_ref"][i])
                push(int(plan["notice_step"][i]), K_NOTICE, i)
        if ck != K_OPEN:
            push(cs, ck, i)
    while heap:
        s, ko, _sq, row = heapq.heappop(heap)
        apply(s, row, ko == 0)
    # ---- per-vault valuations (the G3 bad-debt tests)
    acc = np.nonzero(tab["outcome"] == O_ACTIVE)[0]
    if acc.size:
        lo_s = tab["lock_open_step"][acc]
        co_s = tab["claim_open_step"][acc]
        coll = tab["collateral_zat"][acc]
        cents = tab["cents"][acc]
        for col_s, col_tp, col_bad in (
            (lo_s, "tp_lock_open", "bad_at_lock"),
            (co_s, "tp_claim_open", "bad_at_claim_open"),
        ):
            has = col_s >= 0
            tpv = np.where(has, ctx.tp[np.maximum(col_s, 0)], -1)
            tab[col_tp][acc] = tpv
            tab[col_bad][acc] = has & V.is_underwater(coll, tpv, cents, BPS)
        tab["value_at_mint_usd"][acc] = AG.zat_value_usd(coll, tab["tp_mint"][acc])
        tab["owner_missed"][acc] = tab["owner_delay"][acc] > rp.grace
        closed = tab["close_step"][acc] >= 0
        val = AG.zat_value_usd(coll, np.where(closed, tab["tp_close"][acc], ctx.tp[-1] if n else 0))
        tab["shortfall_usd"][acc] = np.maximum(cents / 100 - val, 0.0)
    # ---- trajectory
    every = max(1, int(opt.traj_every))
    ts = np.arange(0, n, every, dtype=np.int64)
    hs = np.asarray(hist_steps, dtype=np.int64)
    hv = np.asarray(hist, dtype=np.int64).reshape(-1, 3)
    pos = np.searchsorted(hs, ts, side="right") - 1
    traj = hv[pos]
    return PathBook(
        tab, ts, traj[:, 0].copy(), traj[:, 1].copy(), traj[:, 2].copy(), counters, events, book.totals
    )


def _claim_ref_a(ctx: _Ctx, s: int, coll: int, cents: int) -> int:
    """The claimant's refHeight for RED-4(a) at step ``s``: the window's lowest pClaim (fact 1.5-5)."""
    W = ctx.W
    cand = np.arange(max(s - W, 0), s)[::-1]
    return int(cand[np.argmin(ctx.pc_big[cand])])


def _no_cap(rp: RuleParams) -> RuleParams:
    from dataclasses import replace

    return replace(rp, supply_cap_bps=0)


# ---------------------------------------------------------------------------------------------------
# 4. Multi-path results


@dataclass
class VaultBookResult:
    """Vault tables of many paths (a ``path`` column added) plus totals trajectories
    ``(paths, samples)`` at ``traj_heights``."""

    vaults: dict[str, np.ndarray]
    supply_cents: np.ndarray
    collateral_zat: np.ndarray
    unbacked_cents: np.ndarray
    traj_steps: np.ndarray
    traj_heights: np.ndarray
    true_price: np.ndarray  #: true price at the trajectory samples, (paths, samples)
    resolution: str
    step_blocks: int
    n_steps: int
    params: Any = None
    counters: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    @property
    def n_paths(self) -> int:
        return int(self.supply_cents.shape[0])

    @property
    def days(self) -> float:
        return self.n_steps * self.step_blocks / BLOCKS_PER_DAY

    def accepted(self) -> np.ndarray:
        return self.vaults["outcome"] == O_ACTIVE

    def column(self, name: str, mask=None) -> np.ndarray:
        v = self.vaults[name]
        return v if mask is None else v[mask]

    @classmethod
    def from_books(
        cls,
        books: Sequence[PathBook],
        heights: np.ndarray,
        true_price: np.ndarray,
        *,
        resolution: str,
        step_blocks: int,
        n_steps: int,
        params=None,
        meta: dict | None = None,
        path_offset: int = 0,
    ) -> VaultBookResult:
        cols = list(books[0].vaults) if books else list(_empty_table(0))
        vaults = {c: np.concatenate([b.vaults[c] for b in books]) if books else np.zeros(0) for c in cols}
        vaults["path"] = (
            np.concatenate(
                [np.full(len(b.vaults["step"]), path_offset + i, dtype=np.int64) for i, b in enumerate(books)]
            )
            if books
            else np.zeros(0, np.int64)
        )
        ts = books[0].traj_steps if books else np.zeros(0, np.int64)
        tp = np.asarray(true_price, dtype=np.int64)
        counters: dict = {}
        for b in books:
            for k, v in b.counters.items():
                if isinstance(v, dict):
                    d = counters.setdefault(k, {})
                    for kk, vv in v.items():
                        d[kk] = d.get(kk, 0) + vv
                else:
                    counters[k] = counters.get(k, 0) + v
        return cls(
            vaults=vaults,
            supply_cents=np.stack([b.supply_cents for b in books]) if books else np.zeros((0, 0), np.int64),
            collateral_zat=np.stack([b.collateral_zat for b in books])
            if books
            else np.zeros((0, 0), np.int64),
            unbacked_cents=np.stack([b.unbacked_cents for b in books])
            if books
            else np.zeros((0, 0), np.int64),
            traj_steps=ts,
            traj_heights=np.asarray(heights, dtype=np.int64)[ts] if len(ts) else np.zeros(0, np.int64),
            true_price=tp[:, ts] if tp.ndim == 2 and len(ts) else tp,
            resolution=resolution,
            step_blocks=step_blocks,
            n_steps=n_steps,
            params=params,
            counters=counters,
            meta=dict(meta or {}),
        )

    @classmethod
    def concat(cls, parts: Sequence[VaultBookResult]) -> VaultBookResult:
        parts = [p for p in parts if p is not None]
        first = parts[0]
        if len(parts) == 1:
            return first
        vaults = {c: np.concatenate([p.vaults[c] for p in parts]) for c in first.vaults}
        counters: dict = {}
        for p in parts:
            for k, v in p.counters.items():
                if isinstance(v, dict):
                    d = counters.setdefault(k, {})
                    for kk, vv in v.items():
                        d[kk] = d.get(kk, 0) + vv
                else:
                    counters[k] = counters.get(k, 0) + v
        meta = dict(first.meta)
        meta["seconds"] = sum(p.meta.get("seconds", 0.0) for p in parts)
        return cls(
            vaults=vaults,
            supply_cents=np.concatenate([p.supply_cents for p in parts]),
            collateral_zat=np.concatenate([p.collateral_zat for p in parts]),
            unbacked_cents=np.concatenate([p.unbacked_cents for p in parts]),
            traj_steps=first.traj_steps,
            traj_heights=first.traj_heights,
            true_price=np.concatenate([p.true_price for p in parts]),
            resolution=first.resolution,
            step_blocks=first.step_blocks,
            n_steps=first.n_steps,
            params=first.params,
            counters=counters,
            meta=meta,
        )


# ---------------------------------------------------------------------------------------------------
# 5. Block mode: the engine hook


@dataclass
class VaultHook:
    """WP-3 ``vaults`` hook (``simulate_blocks(..., hooks=(VaultHook(...),))``): runs the vault book
    on every path, fills ``series.supply_cents`` / ``series.collateral_zat`` (the engine then
    recomputes ``global_ratio_bps`` and HALT-2 from them — equal to the book's own HALT-2 by
    construction) and stores the :class:`VaultBookResult` in ``series.extras["vaults"]``.

    ``attempts`` overrides the sampled mint attempts (one :class:`~ybcal.sim.agents.MintAttempts`
    per path; tests and replays). Randomness: ``series.rng`` (the engine's per-chunk generator), or
    ``default_rng(seed)``."""

    agents: AG.AgentsConfig = field(default_factory=AG.AgentsConfig)
    options: BookOptions = field(default_factory=BookOptions)
    seed: int | None = None
    attempts: Sequence[AG.MintAttempts] | None = None

    def __call__(self, stage: str, params: Mapping, inputs, series) -> None:
        if stage != "vaults":
            return
        rng = series.rng if series.rng is not None else np.random.default_rng(self.seed)
        n = series.n_blocks
        books = []
        opt = BookOptions(**{**self.options.__dict__, "traj_every": 1})
        for p in range(series.n_paths):
            tl = timeline_from_blocks(params, inputs, series, p)
            if self.attempts is not None:
                att = self.attempts[p]
            else:
                att = AG.sample_mint_attempts(rng, params, self.agents, n, 1)
            pb = run_book(params, tl, att, self.agents, options=opt)
            series.supply_cents[p] = pb.supply_cents
            series.collateral_zat[p] = pb.collateral_zat
            books.append(pb)
        res = VaultBookResult.from_books(
            books,
            series.heights,
            series.true_price,
            resolution="block",
            step_blocks=1,
            n_steps=n,
            params=params,
        )
        if self.options.record_events:
            res.meta["events"] = [b.events for b in books]
        series.extras["vaults"] = res


# ---------------------------------------------------------------------------------------------------
# 6. Hour mode


@dataclass(frozen=True)
class HourOptions:
    """Hour-mode environment of :func:`simulate_vault_book_hours`.

    * ``start_offset_blocks`` — the simulated window starts this many blocks after ``startHeight``
      (``issuedZat`` and the supply cap count from ``startHeight``, fact 1.5-2).
    * ``activation_blocks`` — ACTIVE from ``startHeight + activation_blocks`` (default: the fastest
      activation, ``signalWindow − 1 + activationDelay``); ACT-4/6 halts from ``enforcement_halt``.
    * ``assume_renewal`` — treat the parameter set as renewed at its sunset (W18), i.e. ACT-5 never
      lapses inside the horizon; False applies ``enforceUntilHeight`` (sweeps and thieves after it).
    * ``armed`` / ``attest_lag_hours`` / ``attest_noise_bps`` — a bundle proxy for ARMED studies:
      ``aMint = aClaim`` = the true price ``lag`` hours earlier × (1 + noise); RED-4(b) needs it.
    * ``enforcement_halt`` — optional ``(paths, hours)`` bool: ACT-6 ENFORCEMENT (and
      PARTICIPATION) halt hours (abandonment, sweeps).
    """

    start_offset_blocks: int = 0
    activation_blocks: int | None = None
    assume_renewal: bool = True
    armed: bool = False
    attest_lag_hours: int = 0
    attest_noise_bps: float = 0.0
    enforcement_halt: np.ndarray | None = None
    book: BookOptions = field(default_factory=lambda: BookOptions(traj_every=24))


def timeline_from_hours(
    params: Mapping,
    hs,
    path: int,
    hourly_true: np.ndarray,
    *,
    issued_zat: np.ndarray,
    options: HourOptions,
    rng: np.random.Generator | None = None,
    attacker: AG.AttackerConfig | None = None,
    quoting_share: float = 0.8,
) -> Timeline:
    """Hour-mode timeline of one path of a :class:`~ybcal.engine.HourSeries`."""
    n = hs.n_hours
    start = int(params["startHeight"]) + int(options.start_offset_blocks)
    heights = start + BLOCKS_PER_HOUR * np.arange(n, dtype=np.int64) + (BLOCKS_PER_HOUR - 1)
    lag = int(params["DEFAULT_REF_LAG"])
    conf = np.empty(n, dtype=np.int64)
    conf[1:] = heights[:-1] + lag + 1
    conf[:1] = heights[:1] - BLOCKS_PER_HOUR + lag + 1
    pf, pm, ps = hs.p_fast[path], hs.p_mid[path], hs.p_slow[path]
    halt = hs.halt_mask[path].astype(np.int64)
    xm, xc = hs.p_mint[path], hs.p_claim[path]
    if attacker is not None:
        pf = attacker.apply_to_hours(pf, quoting_share)
        pm = attacker.apply_to_hours(pm, quoting_share)
        ps = attacker.apply_to_hours(ps, quoting_share)
        xm, xc = V.price_mint(pf, pm, ps), V.price_claim(pm, ps)
        halt = np.where(xm == UNDEF, HALT_NO_PRICE, 0) | np.where(
            V.halt3_divergence(pf, pm, ps, int(params["divergenceBps"])), HALT_DIVERGENCE, 0
        )
    act_blocks = (
        int(params["signalWindow"]) - 1 + int(params["activationDelay"])
        if options.activation_blocks is None
        else int(options.activation_blocks)
    )
    active = heights >= int(params["startHeight"]) + act_blocks
    enf = (
        np.asarray(options.enforcement_halt[path], dtype=bool)
        if options.enforcement_halt is not None
        else np.zeros(n, dtype=bool)
    )
    halt = (
        halt | np.where(~active, HALT_NOT_ACTIVE, 0) | np.where(enf, HALT_ENFORCEMENT | HALT_PARTICIPATION, 0)
    )
    pr = dict(params)
    if options.assume_renewal:
        pr["enforceUntilHeight"] = 0
    if options.armed:
        armed = active.copy()
        tp = np.asarray(hourly_true, dtype=np.int64)
        lagged = (
            np.concatenate([np.full(options.attest_lag_hours, tp[0]), tp])[:n]
            if options.attest_lag_hours
            else tp
        )
        if options.attest_noise_bps and rng is not None:
            lagged = np.rint(lagged * (1 + options.attest_noise_bps / BPS * rng.standard_normal(n))).astype(
                np.int64
            )
        a = np.clip(lagged, 100, 100_000_000).astype(np.int64)
    else:
        armed = np.zeros(n, dtype=bool)
        a = np.full(n, UNDEF, dtype=np.int64)
    return Timeline(
        resolution="hour",
        heights=heights,
        conf=conf,
        true_price=np.asarray(hourly_true, dtype=np.int64),
        x_mint=np.asarray(xm, dtype=np.int64),
        x_claim=np.asarray(xc, dtype=np.int64),
        p_fast=np.asarray(pf, dtype=np.int64),
        sigma_mult_bps=hs.sigma_mult_bps[path].astype(np.int64),
        base_halt=halt.astype(np.int64),
        active=active,
        issued_zat=issued_zat,
        eligible=np.ones(n, dtype=bool),
        armed=armed,
        a_mint=a,
        a_claim=a.copy(),
        enforcing=_enforcing_series(pr, heights, conf, active, enf),
        abandoned=_abandoned_series(pr, enf, BLOCKS_PER_HOUR),
        ref_steps=1,
        step_blocks=BLOCKS_PER_HOUR,
        wallet_ref_lag=0,
    )


def hourly_issued_zat(params: Mapping, n_hours: int, start_offset_blocks: int = 0) -> np.ndarray:
    """``issuedZat`` (subsidy since ``startHeight``) at each hour-mode snapshot height."""
    off = int(start_offset_blocks)
    last = off + BLOCKS_PER_HOUR * n_hours
    issued = SUP.issued_zat_series(int(params["startHeight"]), last, SUP.schedule_for(params))
    return issued[off + BLOCKS_PER_HOUR * np.arange(n_hours) + (BLOCKS_PER_HOUR - 1)]


#: Sub-steps per hour of the default hour-mode kernel. WP-3's default (12) costs ≈ 1.3 s per
#: path-5-years in the rolling medians; 4 costs ≈ 0.45 s and moved P(bad debt) by < 0.1 pp on the
#: 200-path GARCH benchmark (docs/architecture.md "Vaults, agents and fees (WP-4)").
DEFAULT_HOUR_SUBSTEPS = 4


def _hour_chunk(args):
    """One chunk of paths (``options.enforcement_halt``, if any, already sliced to the chunk)."""
    params, prices, agents, kernel, seed, options, idx, path0 = args
    t0 = time.perf_counter()
    rng = np.random.default_rng([seed, idx])
    hs = E.simulate_hours(params, prices, kernel, rng=rng)
    n = hs.n_hours
    issued = hourly_issued_zat(params, n, options.start_offset_blocks)
    books = []
    for p in range(prices.shape[0]):
        tl = timeline_from_hours(
            params, hs, p, prices[p], issued_zat=issued, options=options, rng=rng, attacker=agents.attacker
        )
        att = AG.sample_mint_attempts(rng, params, agents, n, BLOCKS_PER_HOUR)
        books.append(run_book(params, tl, att, agents, options=options.book))
    res = VaultBookResult.from_books(
        books,
        _hour_heights(params, n, options),
        prices,
        resolution="hour",
        step_blocks=BLOCKS_PER_HOUR,
        n_steps=n,
        params=None,
        path_offset=path0,
    )
    res.meta["seconds"] = time.perf_counter() - t0
    return res


def _hour_heights(params: Mapping, n: int, options: HourOptions) -> np.ndarray:
    start = int(params["startHeight"]) + int(options.start_offset_blocks)
    return start + BLOCKS_PER_HOUR * np.arange(n, dtype=np.int64) + (BLOCKS_PER_HOUR - 1)


def simulate_vault_book_hours(
    params: Mapping,
    price_paths,
    agents_cfg: AG.AgentsConfig | None = None,
    kernel: E.OracleTransferKernel | None = None,
    rng: np.random.Generator | None = None,
    *,
    options: HourOptions | None = None,
    workers: int = 1,
    chunk_paths: int = 8,
) -> VaultBookResult:
    """Hour-mode vault book over 1–6-year horizons (PLAN §3.3): hourly true prices (``(paths,
    hours)`` int64 µUSD or an hour-resolution PricePath) → WP-3's oracle transfer kernel
    (:func:`~ybcal.sim.engine.simulate_hours`; default ``OracleTransferKernel.ideal(params,
    substeps=4)``, see :data:`DEFAULT_HOUR_SUBSTEPS`) → the
    vault book of every path. Chunks of ``chunk_paths`` paths use ``default_rng([seed, chunk])`` with
    ``seed`` drawn once from ``rng``, so results are identical for any ``workers``."""
    from dataclasses import replace as _replace

    ht, res = E.prices_from(price_paths)
    if hasattr(price_paths, "resolution") and res != "hour":
        raise ValueError("simulate_vault_book_hours takes hourly prices (resolution 'hour')")
    agents_cfg = agents_cfg or AG.AgentsConfig()
    kernel = kernel or E.OracleTransferKernel.ideal(params, substeps=DEFAULT_HOUR_SUBSTEPS)
    options = options or HourOptions()
    rng = rng or np.random.default_rng(0)
    seed = int(rng.integers(0, 2**62))
    P = ht.shape[0]
    jobs = []
    for idx, a in enumerate(range(0, P, chunk_paths)):
        sl = slice(a, min(P, a + chunk_paths))
        opt = options
        if options.enforcement_halt is not None:
            opt = _replace(options, enforcement_halt=np.atleast_2d(np.asarray(options.enforcement_halt))[sl])
        jobs.append((params, ht[sl], agents_cfg, kernel, seed, opt, idx, a))
    t0 = time.perf_counter()
    if workers <= 1 or len(jobs) == 1:
        parts = [_hour_chunk(j) for j in jobs]
    else:
        ctx = mp.get_context("fork") if "fork" in mp.get_all_start_methods() else None
        with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as ex:
            parts = list(ex.map(_hour_chunk, jobs))
    out = VaultBookResult.concat(parts)
    out.params = params
    out.meta["wall_seconds"] = time.perf_counter() - t0
    out.meta["workers"] = workers
    out.meta["kernel"] = getattr(kernel, "meta", {})
    return out


__all__ = [
    "ACTIVE",
    "CLAIMED",
    "CLOSED",
    "NO_BUNDLE",
    "OK",
    "VOID",
    "BookOptions",
    "Bundle",
    "HourOptions",
    "MintTx",
    "PathBook",
    "RedOutcome",
    "RuleParams",
    "Snap",
    "SpendTx",
    "Timeline",
    "Totals",
    "VaultBook",
    "VaultBookResult",
    "VaultHook",
    "VaultRecord",
    "hourly_issued_zat",
    "mint_verdict",
    "notice_verdict",
    "path_open",
    "red_verdict",
    "run_book",
    "simulate_vault_book_hours",
    "timeline_from_blocks",
    "timeline_from_hours",
    "wallet_collateral",
    "wallet_collateral_int",
]

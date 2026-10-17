"""Accounting for one claim and its cancel (MEDIUM-W4-K-3).

The vault book (:mod:`ybcal.sim.vaults`) is still the v3 book: it does not run
this sequence. This module accounts one claim intent, ``claimDelay``, a
one-seat cancel, the CAN-1 refund and a residual that is not re-locked. It
composes the vendored kernels (:func:`ybcal.model.kernels.claim_path`,
:func:`~ybcal.model.kernels.claim_split`, :func:`~ybcal.model.kernels.claim_accounting`,
:func:`~ybcal.model.kernels.claimant_max_zat`, :func:`~ybcal.model.kernels.residual_zat`)
and the primitive's ``RateCap``. :func:`one_spread_bucket` reports extraction
for one spread bucket with the RED-5b sizing on and the pre-RED-5b residual
off. It does not recommend a value of θ, ``emergencyRatioBps``, the bond floor,
``claimDelay``, or ``rateLimitBps``, and nothing here is a re-run of G3, the
seats study, or D-MP-1's T5 plan.

The vendored reference at ``vendor.VENDORED_COMMIT`` (ycash6 ``4129f22cd``) has
no ``attestor_rate_limit_bps``. The node pins the YED set's ``rateLimitBps`` to
0 (ycash6 ``90b1ac763``, ycash-dd #97, W3-Y-3 / R-1d). That constant is
:data:`YED_ATTESTOR_RATE_LIMIT_BPS`; :func:`ybcal.model.kernels.set_create_adoptable`
does not grow a parameter the vendored model does not have.

What this does not model, on purpose:

* CAN-2's later-cancel escalation. The first cancel is admitted when
  ``cancel_signers >= cancel_threshold`` (the pinned threshold is 1). A second
  cancel of the same vault is not made more expensive here.
* A clock. ``claim_delay`` is recorded and ``within_delay`` says whether the
  cancel falls inside it (``h - intentHeight < delay``). No height is advanced.
"""

from __future__ import annotations

from typing import NamedTuple

from ybcal.model.kernels import (
    BPS,
    COIN,
    TotalsDelta,
    claim_accounting,
    claim_path,
    claim_split,
    claimant_max_zat,
    residual_zat,
)

__all__ = [
    "YED_ATTESTOR_RATE_LIMIT_BPS",
    "ClaimCancelAccount",
    "SpreadBucket",
    "account_claim_cancel",
    "format_spread_bucket",
    "one_spread_bucket",
    "path_a_closed_collateral",
    "rate_cap_zat",
]

#: YED adopted set: ``rateLimitBps`` == 0.
#: The vendored model (ycash6 4129f22cd) has no such field. The node pin is
#: ycash6 90b1ac763 / ycash-dd #97 (W3-Y-3, R-1d). Not a re-scored constant.
YED_ATTESTOR_RATE_LIMIT_BPS: int = 0


def rate_cap_zat(epoch_basis_zat: int, rate_limit_bps: int) -> int:
    """``RateCap`` (``vault/state.cpp``): ``basis / 10^4 * bps + (basis % 10^4) * bps / 10^4``.

    A non-positive basis or bps yields 0, matching the C++. The node does not
    call this when ``rateLimitBps == 0``: that value skips the epoch check
    rather than forbidding every spend. Callers that want the skip test
    ``rate_limit_bps != 0`` themselves, as :func:`account_claim_cancel` does.
    """
    if epoch_basis_zat <= 0 or rate_limit_bps <= 0:
        return 0
    basis = epoch_basis_zat
    bps = rate_limit_bps
    return (basis // BPS) * bps + ((basis % BPS) * bps) // BPS


def path_a_closed_collateral(minted_cents: int, p_claim: int, claim_threshold_bps: int) -> int | None:
    """Smallest collateral that is not RED-4(a)-underwater at ``p_claim``.

    ``is_underwater`` is strict (``C · p < minted · θ · COIN``), so the boundary
    is ``ceil(minted · θ · COIN / pClaim)``. ``None`` when no positive price or
    threshold defines it.
    """
    if minted_cents <= 0 or p_claim <= 0 or claim_threshold_bps <= 0:
        return None
    num = minted_cents * claim_threshold_bps * COIN
    return (num + p_claim - 1) // p_claim


class ClaimCancelAccount(NamedTuple):
    """One claim, and the cancel of its claimant intent when that cancel lands.

    ``owner_extracted_zat`` is the residual intent a cancel does not re-lock:
    the burn is refunded (CAN-1) and that residual has left the vault. It is 0
    when the claim does not happen, the cancel does not land, or no residual
    intent was due. This is an accounting identity, not a calibrated loss bound.
    """

    path: str | None
    claimant_intent_zat: int
    residual_intent_zat: int
    claim: TotalsDelta | None
    cancel: TotalsDelta | None
    owner_extracted_zat: int
    relock_zat: int
    rate_refused: bool
    rate_limit_bps: int
    claim_delay: int


def _none(
    path: str | None,
    *,
    claimant: int = 0,
    residual: int = 0,
    rate_refused: bool = False,
    rate_limit_bps: int,
    claim_delay: int,
) -> ClaimCancelAccount:
    return ClaimCancelAccount(
        path, claimant, residual, None, None, 0, 0, rate_refused, rate_limit_bps, claim_delay
    )


def account_claim_cancel(
    *,
    collateral_zat: int,
    minted_cents: int,
    p_claim: int | None,
    p_emerg: int | None,
    claim_threshold_bps: int,
    emergency_ratio_bps: int,
    notice_persisted: bool,
    residual_threshold_zat: int,
    cancelled: bool = True,
    within_delay: bool = True,
    cancel_signers: int = 1,
    cancel_threshold: int = 1,
    rate_limit_bps: int = 0,
    epoch_basis_zat: int = 0,
    epoch_used_zat: int = 0,
    claim_delay: int = 576,
    module_fix: bool = True,
) -> ClaimCancelAccount:
    """Account a claim and, when it lands, a cancel of the claimant intent.

    The claim's intents are the vendored split (RED-5b) when ``module_fix`` is
    true: path ``"a"`` caps at ``p_claim`` with margin θ, path ``"b"`` at
    ``p_emerg`` with margin 10^4. ``module_fix`` false is the pre-RED-5b path
    (b): the claimant cap is ``claimant_max_zat`` at ``p_claim`` with margin
    10^4, so at the path-(a) boundary the residual is ``(1 − 1/θ)·C``. A
    residual at or above ``residual_threshold_zat`` is its own owner intent;
    otherwise the claimant intent carries the whole collateral.

    The claim spends ``claimant + residual`` against the set epoch
    (``vault/state.cpp`` sums intent outputs on an APP spend). ``rate_limit_bps
    == 0`` does not refuse, because the node skips ``RateCap`` in that case.
    A positive limit refuses when ``epoch_used + intent sum > RateCap(basis, bps)``.

    A cancel inside ``claim_delay`` with ``cancel_signers >= cancel_threshold``
    re-locks the claimant intent and refunds the whole burn. The residual intent
    is not re-locked: ``owner_extracted_zat`` is that residual, and the cancel's
    collateral delta is its negation. One member suffices exactly when
    ``cancel_threshold`` is 1.
    """
    path = claim_path(
        collateral_zat,
        p_claim,
        p_emerg,
        minted_cents,
        claim_threshold_bps,
        emergency_ratio_bps,
        notice_persisted=notice_persisted,
    )
    if path is None:
        return _none(None, rate_limit_bps=rate_limit_bps, claim_delay=claim_delay)

    if module_fix or path != "b":
        split = claim_split(
            collateral_zat,
            minted_cents,
            p_claim,
            path,
            claim_threshold_bps,
            residual_threshold_zat,
            p_emerg=p_emerg,
        )
        raw_residual = split.residual_zat
        residual_due = split.residual_due
    else:
        # Pre-RED-5b path (b): cap at pClaim with margin 10^4. claim_split is the
        # post-fix rule and is not used for this residual.
        cap = claimant_max_zat(minted_cents, BPS, p_claim) if p_claim is not None else None
        raw_residual = residual_zat(collateral_zat, cap)
        residual_due = raw_residual >= residual_threshold_zat
    residual = raw_residual if residual_due else 0
    claimant = collateral_zat - residual
    if rate_limit_bps != 0:
        cap = rate_cap_zat(epoch_basis_zat, rate_limit_bps)
        if epoch_used_zat + claimant + residual > cap:
            return _none(
                path,
                claimant=claimant,
                residual=residual,
                rate_refused=True,
                rate_limit_bps=rate_limit_bps,
                claim_delay=claim_delay,
            )

    claim = claim_accounting("claim", burned_cents=minted_cents)
    signers_met = cancel_threshold > 0 and cancel_signers >= cancel_threshold
    if not (cancelled and within_delay and signers_met):
        return ClaimCancelAccount(
            path, claimant, residual, claim, None, 0, 0, False, rate_limit_bps, claim_delay
        )
    cancel = claim_accounting(
        "cancel",
        burned_cents=minted_cents,
        claiming_collateral_zat=collateral_zat,
        relock_zat=claimant,
    )
    return ClaimCancelAccount(
        path, claimant, residual, claim, cancel, residual, claimant, False, rate_limit_bps, claim_delay
    )


class SpreadBucket(NamedTuple):
    """One path-(b) bucket: path (a) closed, ``pEmerg`` one µUSD inside path (b).

    ``off`` is the pre-RED-5b residual. ``on`` is RED-5b (path (b) sized at
    ``pEmerg``). ``constants_rescored`` is always false: this report does not
    choose a new θ, ``emergencyRatioBps``, bond floor or ``claimDelay``.
    """

    theta: int
    collateral_zat: int
    p_claim: int
    p_emerg: int
    claim_delay: int
    off: ClaimCancelAccount
    on: ClaimCancelAccount
    constants_rescored: bool


def one_spread_bucket(
    theta: int = 12_500,
    *,
    minted_cents: int = 10_000_000,
    p_claim: int = 420_000,
    emergency_ratio_bps: int = 10_500,
    residual_threshold_zat: int = 100_000,
    claim_delay: int = 576,
) -> SpreadBucket:
    """Extraction for one spread bucket, module sizing off and on.

    Debt defaults to $100,000 at ``pClaim`` $0.42, the audit's path-(a) boundary.
    The YED rate limit passed to both accounts is :data:`YED_ATTESTOR_RATE_LIMIT_BPS`.
    """
    collateral = path_a_closed_collateral(minted_cents, p_claim, theta)
    if collateral is None:
        raise ValueError("no path-(a) boundary for these inputs")
    p_emerg = (minted_cents * emergency_ratio_bps * COIN - 1) // collateral
    common = dict(
        collateral_zat=collateral,
        minted_cents=minted_cents,
        p_claim=p_claim,
        p_emerg=p_emerg,
        claim_threshold_bps=theta,
        emergency_ratio_bps=emergency_ratio_bps,
        notice_persisted=True,
        residual_threshold_zat=residual_threshold_zat,
        rate_limit_bps=YED_ATTESTOR_RATE_LIMIT_BPS,
        claim_delay=claim_delay,
        cancel_signers=1,
        cancel_threshold=1,
    )
    return SpreadBucket(
        theta,
        collateral,
        p_claim,
        p_emerg,
        claim_delay,
        account_claim_cancel(**common, module_fix=False),
        account_claim_cancel(**common, module_fix=True),
        False,
    )


def format_spread_bucket(row: SpreadBucket) -> str:
    """One line a study or test can print. Shares are of the vault collateral."""

    def share(account: ClaimCancelAccount) -> float:
        return 100.0 * account.owner_extracted_zat / row.collateral_zat

    return (
        f"spread path-b-just-open theta={row.theta} claim_delay={row.claim_delay} "
        f"off_extracted_zat={row.off.owner_extracted_zat} ({share(row.off):.2f}%) "
        f"on_extracted_zat={row.on.owner_extracted_zat} ({share(row.on):.2f}%) "
        f"constants_rescored={row.constants_rescored}"
    )

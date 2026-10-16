"""Accounting for one claim and its cancel (MEDIUM-W4-K-3).

The vault book (:mod:`ybcal.sim.vaults`) is still the v3 book: it has no claim
intents, ``claimDelay``, cancel, or residual release. This module is the
smallest addition that makes those events visible to the calibration. It
composes the vendored kernels (:func:`ybcal.model.kernels.claim_path`,
:func:`~ybcal.model.kernels.claim_split`, :func:`~ybcal.model.kernels.claim_accounting`)
and the primitive's ``RateCap``. It does not recommend a value of θ,
``emergencyRatioBps``, the bond floor, ``claimDelay``, or ``rateLimitBps``, and
nothing here is a re-run of G3, the seats study, or D-MP-1's T5 plan.

What this does not model, on purpose:

* CAN-2's later-cancel escalation. The first cancel is admitted when
  ``cancel_signers >= cancel_threshold`` (the pinned threshold is 1). A second
  cancel of the same vault is not made more expensive here.
* A clock. ``claim_delay`` is recorded and ``within_delay`` says whether the
  cancel falls inside it (``h - intentHeight < delay``). No height is advanced.
"""

from __future__ import annotations

from typing import NamedTuple

from ybcal.model.kernels import BPS, COIN, TotalsDelta, claim_accounting, claim_path, claim_split

__all__ = [
    "ClaimCancelAccount",
    "account_claim_cancel",
    "path_a_closed_collateral",
    "rate_cap_zat",
]


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
) -> ClaimCancelAccount:
    """Account a claim and, when it lands, a cancel of the claimant intent.

    The claim's intents are the vendored split (RED-5b): path ``"a"`` caps at
    ``p_claim`` with margin θ, path ``"b"`` at ``p_emerg`` with margin 10^4. A
    residual at or above ``residual_threshold_zat`` is its own owner intent;
    otherwise the claimant intent carries the whole collateral. The pre-RED-5b
    residual ``(1 − 1/θ)·C`` is not what this returns.

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

    split = claim_split(
        collateral_zat,
        minted_cents,
        p_claim,
        path,
        claim_threshold_bps,
        residual_threshold_zat,
        p_emerg=p_emerg,
    )
    residual = split.residual_zat if split.residual_due else 0
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

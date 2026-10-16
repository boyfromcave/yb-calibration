"""Claim/cancel accounting only (MEDIUM-W4-K-3). Not a study re-run."""

from __future__ import annotations

from ybcal.model.kernels import BPS, COIN, is_underwater
from ybcal.sim.extract import account_claim_cancel, path_a_closed_collateral, rate_cap_zat

#: $100,000 of debt at $0.42, the audit's path-(a) boundary example.
DEBT_CENTS = 10_000_000
P_CLAIM = 420_000
EMERGENCY_BPS = 10_500
RESIDUAL_FLOOR = 100_000


def _boundary(theta: int) -> int:
    collateral = path_a_closed_collateral(DEBT_CENTS, P_CLAIM, theta)
    assert collateral is not None
    assert not is_underwater(collateral, P_CLAIM, DEBT_CENTS, theta)
    assert is_underwater(collateral - 1, P_CLAIM, DEBT_CENTS, theta)
    return collateral


def _account(theta: int, **overrides: object):
    collateral = _boundary(theta)
    # Just inside path (b): pEmerg one µUSD below the emergency boundary at this collateral.
    p_emerg = (DEBT_CENTS * EMERGENCY_BPS * COIN - 1) // collateral
    kwargs = dict(
        collateral_zat=collateral,
        minted_cents=DEBT_CENTS,
        p_claim=P_CLAIM,
        p_emerg=p_emerg,
        claim_threshold_bps=theta,
        emergency_ratio_bps=EMERGENCY_BPS,
        notice_persisted=True,
        residual_threshold_zat=RESIDUAL_FLOOR,
        rate_limit_bps=0,
        claim_delay=576,
    )
    kwargs.update(overrides)
    return account_claim_cancel(**kwargs)  # type: ignore[arg-type]


def test_rate_cap_matches_the_node_formula():
    assert rate_cap_zat(0, 2_500) == 0
    assert rate_cap_zat(20_000, 0) == 0
    assert rate_cap_zat(10_000, 2_500) == 2_500
    assert rate_cap_zat(10_001, 2_500) == 2_500
    assert rate_cap_zat(19_999, 2_500) == 4_999
    assert rate_cap_zat(20_000, 2_500) == 5_000


def test_path_b_cancel_at_the_path_a_boundary_extracts_the_residual():
    """RED-5b, as the vendored ``claim_split`` implements it: path (b) caps at pEmerg.

    At the path-(a) boundary with pEmerg one µUSD inside the emergency test, the
    residual is ``C − ceil(debt · 10^4 · COIN / pEmerg)``. That is not the
    pre-RED-5b figure ``(1 − 1/θ)·C`` (23,810 / 59,524 / 119,048 YEC at
    θ 11,000 / 12,500 / 15,000). Pinning the zat residual does not recommend a
    constant; G3 was not re-run against it.
    """
    pre_red5b_yec = {11_000: 23_810, 12_500: 59_524, 15_000: 119_048}
    pinned_zat = {11_000: 1_247_159_876_799, 12_500: 1_417_153_217_824, 15_000: 1_700_564_579_180}
    for theta, yec in pre_red5b_yec.items():
        got = _account(theta)
        collateral = _boundary(theta)
        p_emerg = (DEBT_CENTS * EMERGENCY_BPS * COIN - 1) // collateral
        cap = (DEBT_CENTS * BPS * COIN + p_emerg - 1) // p_emerg
        assert got.path == "b"
        assert got.rate_refused is False
        assert got.claim_delay == 576
        assert got.owner_extracted_zat == pinned_zat[theta] == collateral - cap
        assert got.residual_intent_zat == got.owner_extracted_zat
        assert got.claimant_intent_zat + got.residual_intent_zat == collateral
        assert round(got.owner_extracted_zat / COIN) != yec
        assert got.claim is not None and got.cancel is not None
        assert got.claim.supply_cents == -DEBT_CENTS
        assert got.cancel.supply_cents == DEBT_CENTS
        assert got.claim.supply_cents + got.cancel.supply_cents == 0
        assert got.claim.collateral_zat == 0
        assert got.cancel.collateral_zat == -got.owner_extracted_zat
        assert got.relock_zat == got.claimant_intent_zat
        assert got.cancel.active_vaults == 1
        assert got.claim.active_vaults == -1


def test_one_extra_microusd_of_p_emerg_closes_path_b():
    got = _account(12_500)
    closed = _account(12_500, p_emerg=(DEBT_CENTS * EMERGENCY_BPS * COIN - 1) // _boundary(12_500) + 1)
    assert got.path == "b" and got.owner_extracted_zat > 0
    assert closed.path is None and closed.owner_extracted_zat == 0 and closed.claim is None


def test_cancel_needs_the_threshold_and_the_delay():
    assert _account(12_500, cancel_threshold=2, cancel_signers=1).cancel is None
    assert _account(12_500, cancel_threshold=2, cancel_signers=1).owner_extracted_zat == 0
    assert _account(12_500, within_delay=False).cancel is None
    assert _account(12_500, cancelled=False).cancel is None
    held = _account(12_500, cancelled=False)
    assert held.claim is not None and held.residual_intent_zat > 0
    assert held.owner_extracted_zat == 0


def test_rate_limit_zero_does_not_refuse_and_a_positive_limit_can():
    collateral = _boundary(12_500)
    open_set = _account(12_500, rate_limit_bps=0, epoch_basis_zat=collateral, epoch_used_zat=collateral)
    assert open_set.rate_refused is False and open_set.cancel is not None
    refused = _account(12_500, rate_limit_bps=9_999, epoch_basis_zat=collateral, epoch_used_zat=0)
    assert refused.rate_refused is True
    assert refused.claim is None and refused.cancel is None and refused.owner_extracted_zat == 0
    # The intents are still reported, so the refused spend is visible.
    assert refused.claimant_intent_zat + refused.residual_intent_zat == collateral
    admitted = _account(12_500, rate_limit_bps=10_000, epoch_basis_zat=collateral, epoch_used_zat=0)
    assert admitted.rate_refused is False and admitted.cancel is not None

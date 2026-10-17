"""Claim/cancel accounting only (MEDIUM-W4-K-3). Not a study re-run."""

from __future__ import annotations

import pytest

from ybcal.model.kernels import BPS, COIN, is_underwater
from ybcal.sim.extract import (
    YED_ATTESTOR_RATE_LIMIT_BPS,
    account_claim_cancel,
    format_spread_bucket,
    one_spread_bucket,
    path_a_closed_collateral,
    rate_cap_zat,
)

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


def test_one_spread_bucket_reports_extraction_with_the_module_fix_on_and_off(capsys):
    """One spread bucket, not a G3 or seats re-run. Constants are not re-scored.

    Off (pre-RED-5b) extracts (1 − 1/θ)·C, which rises with θ (9.09 % at 11,000,
    20.00 % at 12,500, 59,524 YEC at $100,000 and pClaim $0.42). On (RED-5b, cap
    at pEmerg) extracts less of the same collateral. Both sides are one claim
    intent, claimDelay 576, a one-seat cancel, a CAN-1 refund, and a residual
    that is not re-locked.
    """
    yec = {11_000: 23_810, 12_500: 59_524, 15_000: 119_048}
    shares = []
    for theta, expected_yec in yec.items():
        row = one_spread_bucket(theta)
        print(format_spread_bucket(row))
        assert row.constants_rescored is False
        assert row.claim_delay == 576
        assert row.off.rate_limit_bps == YED_ATTESTOR_RATE_LIMIT_BPS == 0
        for account in (row.off, row.on):
            assert account.path == "b"
            assert account.rate_refused is False
            assert account.claim is not None and account.cancel is not None
            assert account.claim.supply_cents == -DEBT_CENTS
            assert account.cancel.supply_cents == DEBT_CENTS
            assert account.owner_extracted_zat == account.residual_intent_zat
            assert account.relock_zat == account.claimant_intent_zat
            assert account.claimant_intent_zat + account.residual_intent_zat == row.collateral_zat
            assert account.cancel.collateral_zat == -account.owner_extracted_zat
        formula = row.collateral_zat * (theta - BPS) // theta
        assert abs(row.off.owner_extracted_zat - formula) <= 1
        assert round(row.off.owner_extracted_zat / COIN) == expected_yec
        assert row.on.owner_extracted_zat < row.off.owner_extracted_zat
        shares.append(row.off.owner_extracted_zat / row.collateral_zat)
    assert shares[0] == pytest.approx(0.0909, abs=0.0001)
    assert shares[1] == pytest.approx(0.20, abs=1e-9)
    assert shares[0] < shares[1] < shares[2]
    printed = capsys.readouterr().out
    assert "off_extracted_zat=" in printed and "on_extracted_zat=" in printed
    assert "constants_rescored=False" in printed


def test_yed_rate_limit_bps_is_zero_and_absent_from_the_vendored_model():
    """ycash-dd #97 / ycash6 90b1ac763 pin the YED set; the vendored model does not."""
    import inspect

    from ybcal.model import reference as ref
    from ybcal.model.kernels import set_create_adoptable

    assert YED_ATTESTOR_RATE_LIMIT_BPS == 0
    assert "rate_limit_bps" not in inspect.signature(set_create_adoptable).parameters
    assert not hasattr(ref.Params.mainnet(1), "attestor_rate_limit_bps")

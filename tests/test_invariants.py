"""PLAN §1.4 invariants: shipped sets pass, each check catches its violation."""

from __future__ import annotations

import pytest

from ybcal.config import Policy
from ybcal.params.extract import load_snapshot
from ybcal.params.invariants import INVARIANTS, Context, check_all, runbook_length
from ybcal.params.paramset import ParamSet, candidate, mainnet, regtest


def test_shipped_sets_pass():
    ctx = Context.from_policy(Policy.load(), release_tip=3_052_055)
    assert mainnet().check(ctx) == []
    assert regtest().check(ctx) == []


def test_snapshot_columns_pass():
    snap = load_snapshot()
    for net in ("main", "regtest"):
        assert ParamSet.from_extracted(snap, net).check() == []


def _names(ps, ctx=None):
    return {v.invariant for v in check_all(ps, ctx)}


# (changes applied to a mainnet candidate, invariant expected to fail)
CASES = [
    ({"pFastMinFill": 47}, "min_fill_fast"),
    ({"pMidMinFill": 383}, "min_fill_mid_slow"),
    ({"pFastWindow": 600}, "window_order"),
    ({"participationFloor": 1000}, "activation_order"),
    ({"enforcementResume": 1600}, "activation_order"),
    ({"abandonBlocks": 30_000, "grace": 34_560}, "abandon_ge_grace"),
    ({"abandonBlocks": 20_000, "grace": 11_520}, "abandon_ge_runbook"),
    ({"startHeight": 0}, "start_configured"),
    ({"enforceUntilHeight": 3_495_479}, "sunset"),
    ({"enforceUntilHeight": 0}, "sunset"),
    ({"sigmaRefBps": 0}, "regtest_zero_meanings"),
    ({"classMin[1]": 103_682}, "class_contiguous"),
    ({"classMin[0]": 0}, "class_contiguous"),
    ({"classMax[2]": 499_000_000, "classMin[2]": 420_481}, "class_locktime"),
    ({"volWindow": 2000}, "vol_step_divides"),
    ({"volPeriodsPerYear": 8000}, "vol_periods"),
    ({"volStep": 50, "volWindow": 2000}, "vol_periods"),
    ({"baseRatioBps[2]": 25_000}, "base_gt_halt"),
    ({"recapRatioBps": 40_000}, "recap_double"),
    ({"emergencyRatioBps": 11_000}, "claim_emergency_order"),
    ({"emergencyRatioBps": 10_000}, "claim_emergency_order"),
    ({"kSlack": 3}, "bundle_size"),
    ({"nSlots": 5}, "bundle_size"),
    ({"bundleMax": 7}, "bundle_size"),
    ({"qHighBps": 6666}, "quantile_sum"),
    ({"attestMaxAge": 21}, "attest_max_age"),
    ({"emergencyPersist": 1152}, "emergency_persist_lt_ttl"),
    ({"bondMinLock": 200_000}, "bond_lock_year"),
    ({"ageCap": 2**32}, "age_cap_u32"),
    ({"minMint": 50}, "amount_order"),
    ({"feeMin": 100_000_000}, "fee_floor_mintable"),
    ({"tokenValue": 9999}, "protocol_constants"),
]


@pytest.mark.parametrize(("changes", "inv"), CASES)
def test_each_invariant_catches_its_violation(changes, inv):
    # explicit values override derivation, so derived fields can be broken on purpose
    ps = candidate().replace(changes)
    assert inv in _names(ps), f"{inv} did not fire for {changes}"


def test_every_invariant_has_a_failing_case():
    covered = {inv for _, inv in CASES} | {"release_lead", "qlow_vs_entity"}
    assert covered == set(INVARIANTS)


def test_release_lead_needs_a_tip():
    ps = candidate()
    assert "release_lead" not in _names(ps)
    assert "release_lead" in _names(ps, Context(release_tip=3_060_000))
    assert "release_lead" not in _names(ps, Context(release_tip=3_052_055))


def test_qlow_vs_entity_policy():
    ps = candidate()
    assert "qlow_vs_entity" in _names(ps, Context(max_entity_share_bps=3333))
    assert "qlow_vs_entity" not in _names(ps, Context(max_entity_share_bps=2500))


def test_next_upgrade_caps_sunset():
    assert "sunset" in _names(candidate(), Context(next_upgrade_height=3_400_000))


def test_regtest_scale_relaxations():
    r = regtest()
    assert r["enforceUntilHeight"] == 0 and r["sigmaRefBps"] == 0
    assert check_all(r) == []
    # the same zeros at mainnet scale fail
    assert {"sunset", "regtest_zero_meanings", "bond_lock_year"} <= _names(r.replace(network="candidate"))


def test_runbook_margin_at_current_values():
    ctx = Context.from_policy(Policy.load())
    need = runbook_length(mainnet(), ctx)
    assert need == 2016 + 2016 + 16_128 + 4608
    assert mainnet()["abandonBlocks"] - need == 9792

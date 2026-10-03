"""Registry completeness and agreement with source (PLAN §8, Registry row)."""

from __future__ import annotations

import pytest

from ybcal.params.extract import check_drift, load_snapshot
from ybcal.params.paramset import mainnet, regtest
from ybcal.params.registry import GROUPS, KLASSES, REGISTRY, field_names, params_for_group


def test_registry_covers_every_params_field_in_order(extracted):
    assert list(field_names()) == list(extracted.fields)


def test_no_drift_against_source(extracted):
    assert check_drift(REGISTRY, extracted) == []


def test_live_source_equals_snapshot(live_extracted):
    snap = load_snapshot()
    assert live_extracted.to_dict() | {"ref": snap.ref} == snap.to_dict()


def test_values_match_extraction(extracted):
    main, reg = extracted.values("main"), extracted.values("regtest")
    for name, spec in REGISTRY.items():
        assert main[name] == spec.mainnet, name
        assert reg[name] == spec.regtest, name


def test_hashed_fields_are_the_params_record_six():
    assert {s.name for s in REGISTRY.values() if s.hashed} == {
        "startHeight", "sigmaRefBps", "supplyCapBps", "enforceUntilHeight", "attestArmMin", "bundleCarrier",
    }


def test_regtest_flags_are_the_flag_fields():
    flagged = {s.name for s in REGISTRY.values() if s.regtest_flag}
    hashed = {s.name for s in REGISTRY.values() if s.hashed}
    assert flagged == hashed


@pytest.mark.parametrize("ps_factory", [mainnet, regtest])
def test_derived_values_follow_their_formula(ps_factory):
    assert ps_factory().derived_mismatches() == {}


def test_spec_shapes():
    for s in REGISTRY.values():
        assert s.klass in KLASSES
        assert s.group in GROUPS
        assert (s.derive is not None) == (s.klass == "derived"), s.name
        assert bool(s.parents) == (s.derive is not None), s.name
        lo, hi = s.bounds
        assert lo <= hi, s.name
        if s.step > 0:
            assert lo <= s.mainnet <= hi, f"{s.name} mainnet value outside its search bounds"
        if s.klass in ("excluded", "meta"):
            assert not s.consensus, s.name


def test_classification_matches_plan_table():
    excluded = {s.name for s in REGISTRY.values() if s.klass == "excluded"}
    assert excluded == {"valveBlocks", "nReg", "nPenalty", "accuracyWindow", "payeeTiltBps", "carrierValue",
                        "attestInterval", "walletConfirmations", "DEFAULT_REF_LAG"}
    derived = {s.name for s in REGISTRY.values() if s.klass == "derived"}
    assert derived == {"pFastMinFill", "pMidMinFill", "pSlowMinFill", "recapRatioBps", "volPeriodsPerYear",
                       "qHighBps", "attestMaxAge"}
    assert REGISTRY["abandonBlocks"].klass == "locked"
    assert REGISTRY["attestMaxAge"].consensus and REGISTRY["attestMaxAge"].change_path == "locked"
    assert REGISTRY["attestInterval"].change_path == "patch-release"


def test_every_studied_group_has_params():
    for g in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9", "R"):
        assert params_for_group(g), g

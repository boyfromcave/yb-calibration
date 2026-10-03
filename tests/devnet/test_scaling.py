"""Mainnet → regtest scaling (params/scaling.py, PLAN §6.3)."""

from __future__ import annotations

from fractions import Fraction

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ybcal.params.paramset import ParamSet, candidate, mainnet, regtest
from ybcal.params.registry import REGISTRY
from ybcal.params.scaling import (
    REGTEST_BOND_MIN,
    RULES,
    SHIPPED_REGTEST_NOTES,
    ScalingError,
    compare_to_shipped,
    default_factor,
    scale_to_regtest,
)


@pytest.fixture(scope="module")
def scaled():
    return scale_to_regtest(mainnet())


def test_default_factor_maps_slow_window_to_64(scaled):
    assert default_factor(mainnet()) == Fraction(63, 2)
    assert scaled.factor == Fraction(63, 2)
    assert scaled.params["pSlowWindow"] == 64
    assert scaled.params["signalWindow"] == 64
    assert scaled.params.network == "regtest" and scaled.params.is_regtest_scale


def test_scaled_set_passes_every_regtest_invariant(scaled):
    assert scaled.params.check() == []
    assert scaled.params.derived_mismatches() == {}


def test_preserved_relations(scaled):
    p, m = scaled.params, mainnet()
    # sample count of the sigma estimator, K13 annualisation
    assert p["volWindow"] % p["volStep"] == 0
    assert p["volWindow"] // p["volStep"] == m["volWindow"] // m["volStep"] == 42
    assert p["volPeriodsPerYear"] == 8760
    # strict ordering of windows and thresholds (spec:481)
    assert p["pFastWindow"] < p["pMidWindow"] < p["pSlowWindow"]
    assert (
        p["enforcementFloor"]
        < p["participationFloor"]
        <= p["enforcementResume"]
        < p["activationThreshold"]
        <= p["signalWindow"]
    )
    # thresholds are the ceiling of the mainnet fraction of the window
    for k in ("activationThreshold", "participationFloor", "enforcementFloor", "enforcementResume"):
        assert p[k] == -(-m[k] * p["signalWindow"] // m["signalWindow"])
    # contiguous classes starting at grace, W21
    assert p["classMin[0]"] == p["grace"]
    for i in (1, 2):
        assert p[f"classMin[{i}]"] == p[f"classMax[{i - 1}]"] + 1
    assert p["abandonBlocks"] >= p["grace"]
    assert p["emergencyPersist"] < p["emergencyNoticeTtl"]
    assert p["mSelect"] + p["kSlack"] <= p["bundleMax"] and p["nSlots"] >= p["mSelect"] + p["kSlack"]
    assert p["attestMaxAge"] == 2 * p["attestInterval"]


def test_bps_amounts_and_counts_not_scaled(scaled):
    p, m = scaled.params, mainnet()
    for name, spec in REGISTRY.items():
        if spec.unit in ("bps", "cents") or (spec.unit == "zat" and name != "bondMin"):
            assert p[name] == m[name], name
        if spec.origin == "header":
            assert p[name] == m[name], name
    for k in ("nSlots", "mSelect", "kSlack", "bundleMax", "peerMin", "valveBlocks", "attestArmMin"):
        assert p[k] == m[k], k
    assert p["bondMin"] == REGTEST_BOND_MIN
    assert scale_to_regtest(mainnet(), bond_min="mainnet").params["bondMin"] == m["bondMin"]


def test_runtime_flags(scaled):
    p = scaled.params
    assert p["startHeight"] == 1 and p["enforceUntilHeight"] == 0
    assert p["sigmaRefBps"] == mainnet()["sigmaRefBps"]
    s = scale_to_regtest(mainnet(), keep_sunset=True, start_height=5)
    assert s.params["enforceUntilHeight"] == 5 + round((3_495_480 - 3_075_000) / 31.5)
    assert any("D-WP9-3" in n for n in s.notes)


def test_every_inexact_scale_is_a_ratio_loss(scaled):
    f = scaled.factor
    expect = set()
    for k, r in RULES.items():
        if r.kind in ("window", "term", "class", "vol"):
            m, v = mainnet().as_int(k), scaled.params.as_int(k)
            if Fraction(m, v) != f:
                expect.add(f"{k} scale")
    got = {x.name for x in scaled.losses if x.kind == "scale"}
    assert got == expect
    assert all(x.rel_error > 0 for x in scaled.losses)


def test_named_ratio_losses(scaled):
    by = {x.name: x for x in scaled.losses}
    fill = by["pFastMinFill/pFastWindow"]
    assert fill.mainnet == 0.5 and fill.regtest == pytest.approx(2 / 3) and fill.cause == "rounding"
    assert by["pinMinTags/pinWindow"].cause == "floor"  # the PLAN's "pinMinTags = 2 vs 3" example
    assert by["2*peerLag/peerMin"].cause == "unscaled"
    assert by["attestMaxAge/pFastWindow"].cause == "floor"  # attestInterval floored at 4
    assert "volWindow/volStep" not in by  # sample count held exactly
    assert "activationThreshold/signalWindow" not in by  # 75 % exactly
    assert all(x.rel_error > 0.05 for x in scaled.significant_losses())


def test_every_difference_from_shipped_column_is_explained(scaled):
    diffs = compare_to_shipped(scaled)
    names = {d.param for d in diffs}
    assert names <= set(SHIPPED_REGTEST_NOTES)
    # what a uniform 31.5x scaling reproduces exactly
    for k in (
        "pSlowWindow",
        "pSlowMinFill",
        "signalWindow",
        "activationThreshold",
        "participationFloor",
        "activationDelay",
        "enforcementFloor",
        "enforcementResume",
        "pinMinTags",
        "pinMinBundles",
        "dormancyMinBundles",
        "attestInterval",
        "attestMaxAge",
        "bondMin",
        "volPeriodsPerYear",
    ):
        assert k not in names, k
        assert scaled.params[k] == regtest()[k]


def test_shipped_column_from_live_source_explained(extracted, scaled):
    shipped = ParamSet.from_extracted(extracted, "regtest")
    assert shipped == regtest()
    assert compare_to_shipped(scaled, shipped)


def test_unexplained_difference_raises(scaled):
    odd = regtest().replace({"deviationBps": 1234})
    with pytest.raises(KeyError, match="deviationBps"):
        compare_to_shipped(scaled, odd)


def test_candidate_slow_window_maps_to_64():
    c = candidate(pSlowWindow=2400)
    s = scale_to_regtest(c)
    assert s.factor == Fraction(2400, 64) and s.params["pSlowWindow"] == 64


def test_term_factor_compresses_terms_only():
    s = scale_to_regtest(mainnet(), 31.5, term_factor=1440)
    assert s.params["grace"] == 24 and s.params["classMin[0]"] == 24
    assert s.params["pSlowWindow"] == 64
    assert s.params.check() == []


def test_refuses_regtest_source():
    with pytest.raises(ScalingError):
        scale_to_regtest(regtest())
    with pytest.raises(ScalingError):
        scale_to_regtest(mainnet(), 0)


def test_to_dict_round_trips_values(scaled):
    d = scaled.to_dict()
    assert ParamSet.from_dict(d["values"]) == scaled.params
    assert d["factor"] == "63/2" and len(d["losses"]) == len(scaled.losses)


@settings(max_examples=40, deadline=None)
@given(
    factor=st.integers(min_value=8, max_value=126).map(lambda n: n / 2),
    term=st.sampled_from([None, 100.0, 1440.0]),
)
def test_property_any_factor_yields_admissible_set(factor, term):
    s = scale_to_regtest(mainnet(), factor, term_factor=term)
    assert s.params.check() == []
    p = s.params
    assert p["volWindow"] // p["volStep"] == 42
    assert p["pFastWindow"] < p["pMidWindow"] < p["pSlowWindow"]

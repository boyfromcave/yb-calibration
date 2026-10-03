"""ParamSet: immutability, derivation on replace, diff, serialisation."""

from __future__ import annotations

import pytest

from ybcal.params.paramset import ParamSet, candidate, mainnet, regtest


def test_mainnet_and_regtest_columns():
    m, r = mainnet(), regtest()
    assert m.network == "main" and not m.is_regtest_scale
    assert r.network == "regtest" and r.is_regtest_scale
    assert m.array("baseRatioBps") == (50_000, 40_000, 30_000)
    assert m["bondMin"] == 20_000 * 100_000_000


def test_replace_recomputes_derived():
    m = mainnet()
    c = m.replace(pFastWindow=97, pMidWindow=600, globalRatioHaltBps=20_000, qLowBps=3000, volStep=24,
                  attestInterval=12)
    assert c["pFastMinFill"] == 49
    assert c["pMidMinFill"] == 400
    assert c["recapRatioBps"] == 40_000
    assert c["qHighBps"] == 7000
    assert c["volPeriodsPerYear"] == 17_520
    assert c["attestMaxAge"] == 24
    assert m["pFastMinFill"] == 48  # original untouched


def test_explicit_override_wins_over_derivation():
    c = mainnet().replace(pFastWindow=100, pFastMinFill=10)
    assert c["pFastMinFill"] == 10
    assert c.derived_mismatches() == {"pFastMinFill": (10, 50)}


def test_regtest_keeps_k13_annualisation():
    assert regtest().replace(volStep=16)["volPeriodsPerYear"] == 8760


def test_array_fields_through_mapping():
    c = mainnet().replace({"classMax[0]": 100_000, "classMin[1]": 100_001})
    assert c.array("classMax")[0] == 100_000
    assert c.check() == []


def test_immutable_and_hashable():
    m = mainnet()
    with pytest.raises(TypeError):
        m["grace"] = 1  # type: ignore[index]
    assert hash(m) == hash(mainnet()) and m == mainnet()
    assert len({m, mainnet(), regtest()}) == 2


def test_type_and_key_checks():
    with pytest.raises(TypeError):
        mainnet().replace(grace=1.5)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        mainnet().replace(attestRequired=1)
    with pytest.raises(KeyError):
        mainnet().replace(nosuch=1)
    with pytest.raises(KeyError):
        ParamSet({"grace": 1})


def test_diff_and_delta():
    m = mainnet()
    c = candidate(grace=40_320)
    d = m.diff(c)
    assert d == {"network": ("main", "candidate"), "grace": (34_560, 40_320)}
    assert c.delta(m) == {"network": "candidate", "grace": 40_320}


def test_json_roundtrip_and_digest():
    c = candidate(feeBps=30)
    again = ParamSet.from_json(c.to_json())
    assert again == c
    assert again.digest() == c.digest()
    assert ParamSet.from_dict(c.to_dict()) == c
    assert mainnet().digest() != c.digest()

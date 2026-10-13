"""The C++ worked examples of src/test/yellowback_math_tests.cpp @ ycash6 1310a41a1 (PLAN §8), and the
spec-rational cases of src/test/data/yellowback_math_cases.json (audit 03/T-3)."""

from __future__ import annotations

import pytest

from ybcal.model import examples


@pytest.mark.parametrize("ex", examples.EXAMPLES, ids=lambda e: f"{e.rule}:{e.cpp_line}:{e.name}")
def test_worked_example(ex):
    ok, got = ex.run()
    assert ok, (ex.name, got, ex.expected)


def test_named_worked_examples_present():
    names = {e.name for e in examples.EXAMPLES}
    for must in (
        "sigma one 10% move",
        "sigma same move other side",
        "required $100 @300% $0.05",
        "underwater threshold price",
        "claimant max worked example at 125 %",
        "attest fee half of the $1 floor at $0.05",
        "wq exact thresholds (three)",
        "combine conservative side",
        "bond weight clamp",
        "cap_cents 1e6 YEC @ $0.05",
        "global ratio 250% / one cent more",
        "protocol fee zat at the mint price",
        "early redeem fee of the debt",
        "residual threshold in dollars",
        "max vault cents",
    ):
        assert must in names
    assert len(examples.EXAMPLES) >= 110
    # the retired v3 examples are gone with their rules (U-21, D-Q-23)
    assert not any(e.rule in ("FEE-1", "ACT-5") for e in examples.EXAMPLES)


def test_math_spec_rational_cases():
    rows = examples.run_math_cases()
    assert sum(int(d.split()[0]) for _n, _ok, d in rows) == len(examples.load_math_cases()) >= 50
    for name, ok, detail in rows:
        assert ok, (name, detail)


#: The reference model's mainnet column against the registry, which is still the 7702d22 (v3) set: exactly
#: the values the vault upgrade, in-term claims (D-IT-*), the quantum fee plan (D-Q-*) and the hardening
#: plan (H-*) changed. A registry re-pin empties this (the test then fails on purpose: update it).
KNOWN_V3_REGISTRY_DRIFT = {
    "PRICE_MAX": (4_294_967_295, 100_000_000),          # D-Q-22
    "attestArmMin": (7, 5),                             # H-2
    "attestFeeBps": (5_000, 2_500),                     # H-4
    "baseRatioBps[0]": (30_000, 50_000),                # D-IT-4
    "baseRatioBps[2]": (50_000, 30_000),
    "bondMinLock": (0, 420_480),                        # D-Q-31
    "claimThresholdBps": (12_500, 11_000),              # D-IT-2
    "classMax[1]": (207_360, 420_480),                  # D-IT-9/10
    "classMax[2]": (420_480, 2_102_400),
    "classMin[2]": (207_361, 420_481),
    "globalRatioHaltBps": (20_000, 25_000),             # D-IT-11
    "maxMint": (10_000_000, 1_000_000),                 # D-Q-24
    "mintRequiresArmed": (True, False),                 # H-1
    "sigmaMultMaxBps": (10_000, 30_000),                # D-IT-5
    "sigmaRefBps": (0, 10_000),                         # D-IT-5
}


def test_reference_mainnet_column_drift_is_the_known_v3_registry_set():
    assert examples.reference_param_drift() == KNOWN_V3_REGISTRY_DRIFT

"""The C++ worked examples of src/test/yellowback_math_tests.cpp @ 7702d22 (PLAN §8)."""

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
        "claimant max worked example",
        "attest fee quarter of 15 YEC",
        "wq exact thresholds (three)",
        "combine conservative side",
        "bond weight clamp",
        "cap_cents 1e6 YEC @ $0.05",
        "global ratio 250% / one cent more",
        "fee 201 YEC",
    ):
        assert must in names
    assert len(examples.EXAMPLES) >= 90


def test_reference_mainnet_column_drift_is_only_the_known_w21_one():
    # the model's Params.mainnet still carries abandon_blocks = 4,032 (two windows); spec rev 4
    # (W21) made ABANDON_BLOCKS = GRACE = 34,560. Kernels take parameters from the registry.
    assert examples.reference_param_drift() == {"abandonBlocks": (4_032, 34_560)}

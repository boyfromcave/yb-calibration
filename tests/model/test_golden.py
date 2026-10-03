"""Golden vector replay through the vendored reference model, and kernels on the golden chain."""

from __future__ import annotations

import pytest

from ybcal.model import golden


def test_golden_replay_matches_pinned_hash_tip_totals():
    for name, ok, detail in golden.check_golden():
        assert ok, (name, detail)


@pytest.mark.parametrize("row", golden.check_chain_kernels(), ids=lambda r: r[0])
def test_kernels_recompute_every_golden_snapshot(row):
    name, ok, detail = row
    assert ok, (name, detail)


def test_golden_chain_exercises_the_rules():
    m = golden.replayed_model()
    masks = [s.halt_mask for s in m.snapshots.values() if not s.virtual]
    from ybcal.model import reference as ref

    for bit in (ref.HALT_NOT_ACTIVE, ref.HALT_NO_PRICE, ref.HALT_DIVERGENCE):
        assert any(x & bit for x in masks), bit
    assert sum(1 for j in m.judgements.values() if j.evaluated) > 100

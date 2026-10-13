"""RETIRED v3 kernels (ACT-1..6, FEE-1): still exact against the retained v3 reference (7702d22).

They are not rules of the current node model (U-21, D-Q-23); :mod:`ybcal.model.retired` keeps them for the
simulator's v3 books, which still run the 7702d22 parameter registry.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from ybcal.model import golden
from ybcal.model import reference as ref
from ybcal.model import reference_v3 as ref_v3
from ybcal.model import retired as RK

PROPS = settings(max_examples=200, deadline=None)


def test_current_reference_has_no_activation_or_fee1():
    for name in ("SIGNALING", "LOCKED_IN", "ACTIVE", "fee_zat", "Activation"):
        assert not hasattr(ref, name), name
    assert (RK.SIGNALING, RK.LOCKED_IN, RK.ACTIVE) == (ref_v3.SIGNALING, ref_v3.LOCKED_IN, ref_v3.ACTIVE)
    assert RK.HALT_PARTICIPATION == ref_v3.HALT_PARTICIPATION
    assert RK.HALT_ENFORCEMENT == ref_v3.HALT_ENFORCEMENT


@PROPS
@given(st.integers(0, 2_100_000_000_000_000), st.integers(0, 100_000_000), st.integers(0, 10_000))
def test_retired_fee1_equals_the_v3_reference(coll, fee_min, bps):
    assert RK.fee_zat(coll, fee_min, bps) == ref_v3.fee_zat(coll, fee_min, bps)


def test_retired_act_kernels_recompute_the_v3_golden_chain():
    m = golden.replayed_model_v3()
    p = m.params
    heights = sorted(h for h, s in m.snapshots.items() if not s.virtual and h >= p.start_height)
    act = RK.ActivationState(RK.SIGNALING, 0, 0)
    prev_mask = 0
    seen = set()
    for h in heights:
        s = m.snapshots[h]
        signals = [m.tags[x].signal if x in m.tags else False for x in range(p.start_height, h + 1)]
        count = RK.signal_count(signals, h - p.start_height, p.signal_window)
        assert count == s.signal_count, h
        act = RK.activation_step(act, h, count, p.start_height, p.signal_window, p.activation_threshold,
                                 p.activation_delay)
        want = (s.activation.status, s.activation.lock_in_height, s.activation.activate_height)
        assert tuple(act) == want, h
        active = act.status == RK.ACTIVE
        seen.add(act.status)
        part = RK.participation_halt_step(bool(prev_mask & RK.HALT_PARTICIPATION), count, active,
                                          p.activation_threshold, p.participation_floor)
        enf = RK.enforcement_halt_step(bool(prev_mask & RK.HALT_ENFORCEMENT), count, active,
                                       p.enforcement_resume, p.enforcement_floor)
        assert part == bool(s.halt_mask & RK.HALT_PARTICIPATION), h
        assert enf == bool(s.halt_mask & RK.HALT_ENFORCEMENT), h
        prev_mask = s.halt_mask
    assert seen == {RK.SIGNALING, RK.LOCKED_IN, RK.ACTIVE}

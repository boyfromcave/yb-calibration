"""ACT-1..7 simulator: exactness vs the reference model and the kernels, abandonment, W19."""

from __future__ import annotations

import numpy as np
import pytest

from ybcal.model import golden
from ybcal.model import kernels as K
from ybcal.model import reference as ref
from ybcal.params.paramset import mainnet, regtest
from ybcal.sim import activation as act

KEY = bytes([7]) * 20


def _feed(signals, enforce_until=0, start=1, quote=True):
    """Feed a tag stream (signal bit per height start..) through the reference model."""
    m = ref.YellowbackModel(ref.Params.regtest(start, enforce_until=enforce_until))
    for h in range(1, start):
        m.feed_block(h, f"{h:064x}", ref.height_prefix(h).hex(), 0, [])
    for i, s in enumerate(signals):
        h = start + i
        cb = ref.height_prefix(h)
        if s is not None:
            cb += ref.tag_push(1 if s else 0, 50_000 if quote else 0, 0, KEY)
        m.feed_block(h, f"{h:064x}", cb.hex(), 0, [])
        m._abandoned_at = getattr(m, "_abandoned_at", {})
        m._abandoned_at[h] = m.is_abandoned()
    return m


def _check(series, m, start, n, height0=None):
    height0 = start if height0 is None else height0
    for h in range(start, start + n):
        j = h - height0
        s = m.snapshots[h]
        assert int(series.status[0, j]) == s.activation.status, h
        assert int(series.signal_count[0, j]) == s.signal_count, h
        assert bool(series.participation_halt[0, j]) == bool(s.halt_mask & ref.HALT_PARTICIPATION), h
        assert bool(series.enforcement_halt[0, j]) == bool(s.halt_mask & ref.HALT_ENFORCEMENT), h
        assert bool(series.enforcement_on[0, j]) == m.blocks[h].enforcement_on, h
        assert int(series.halt_bits[0, j]) & ~ref.HALT_NO_PRICE == s.halt_mask & (
            ref.HALT_NOT_ACTIVE | ref.HALT_PARTICIPATION | ref.HALT_ENFORCEMENT), h


def _share_stream(rng, plan):
    """Signal bits from a list of (blocks, share); ``None`` = untagged block (10 %)."""
    out = []
    for blocks, share in plan:
        for _ in range(blocks):
            out.append(None if rng.random() < 0.1 else bool(rng.random() < share))
    return out


def test_golden_chain_activation_history():
    m = golden.replayed_model()
    n = m.tip_height
    sig = np.array([m.tags[h].signal if h in m.tags else False for h in range(1, n + 1)])
    s = act.simulate(regtest(), sig, start_height=1, height0=1)
    _check(s, m, 1, n)
    assert (s.lock_in_height[0], s.activate_height[0]) == (m.activation.lock_in_height,
                                                            m.activation.activate_height)


@pytest.mark.parametrize("seed", range(6))
def test_hysteresis_streams_match_reference(seed):
    """Share drops through the floors and recoveries into the hysteresis bands, untagged blocks."""
    rng = np.random.default_rng(seed)
    plan = [(150, 0.9), (60, 0.35), (40, 0.55), (60, 0.95), (50, 0.52), (30, 0.4), (80, 0.62), (60, 0.9)]
    stream = _share_stream(rng, plan)
    m = _feed(stream)
    sig = np.array([bool(x) for x in stream])
    s = act.simulate(regtest(), sig, start_height=1, height0=1)
    _check(s, m, 1, len(stream))
    ab = s.abandoned(int(regtest()["abandonBlocks"]))
    for h in range(1, len(stream) + 1):
        assert bool(ab[0, h - 1]) == m._abandoned_at[h], h


def test_streams_exercise_both_halts_and_abandonment():
    rng = np.random.default_rng(1)
    plan = [(150, 0.95), (300, 0.2), (100, 0.58), (100, 0.95)]
    stream = _share_stream(rng, plan)
    m = _feed(stream)
    s = act.simulate(regtest(), np.array([bool(x) for x in stream]), start_height=1, height0=1)
    _check(s, m, 1, len(stream))
    assert s.participation_halt.any() and s.enforcement_halt.any()
    ab = s.abandoned(int(regtest()["abandonBlocks"]))
    assert ab.any() and any(m._abandoned_at.values())
    # hysteresis: some block with floor <= count < resume is still halted
    c = s.signal_count[0]
    band = (c >= 32) & (c < 39) & s.enforcement_halt[0]
    assert band.any()


@pytest.mark.parametrize("start", [1, 5])
def test_sunset_and_start_clipping(start):
    """Sunset: miners stop signalling after enforceUntil (index.cpp:727); ACT-5 turns off at
    H > enforceUntil; the window is clipped at a later start (heights < start ignored)."""
    rng = np.random.default_rng(start)
    n = 420
    until = start + 300
    bits = rng.random(n) < 0.92
    bits &= act.sunset_signal_mask(n, start, until)
    m = _feed([bool(b) for b in bits], enforce_until=until, start=start)
    P = regtest().replace(enforceUntilHeight=until, startHeight=start)
    assert act.effective_enforce_until(P, start) == until
    s = act.simulate(P, bits, start_height=start, height0=start)
    _check(s, m, start, n)
    j = until + 1 - start
    assert not s.enforcement_on[0, j:].any() and s.enforcement_on[0, j - 5:j].all()
    assert s.participation_halt[0, -1]          # minting halts about one window after the sunset


def test_series_before_start_is_virtual():
    """height0 < start: the columns before the start are virtual and their bits are ignored."""
    rng = np.random.default_rng(3)
    start, height0, n = 40, 1, 300
    bits = rng.random(n) < 0.9
    bits[:start - height0] = True                       # ignored: below the start
    m = _feed([bool(b) for b in bits[start - height0:]], start=start)
    s = act.simulate(regtest().replace(startHeight=start), bits, start_height=start, height0=height0)
    assert (s.status[0, :start - height0] == act.SIGNALING).all()
    assert (s.signal_count[0, :start - height0] == 0).all() and not s.enforcement_on[0, :start].any()
    _check(s, m, start, n - (start - height0), height0=height0)


def test_mid_chain_start_with_initial_state():
    """A series starting mid-chain with the carried state equals the tail of the full series."""
    rng = np.random.default_rng(4)
    P = regtest()
    n, cut = 500, 230
    bits = rng.random((3, n)) < np.linspace(0.3, 0.95, 3)[:, None]
    full = act.simulate(P, bits, start_height=1, height0=1)
    W = int(P["signalWindow"])
    j = cut - 1
    inits = []
    tails = []
    for i in range(3):
        init = act.ActivationInit(int(full.status[i, j - 1]), int(full.lock_in_height[i]),
                                  int(full.activate_height[i]), bool(full.participation_halt[i, j - 1]),
                                  bool(full.enforcement_halt[i, j - 1]), bits[i, j - (W - 1):j])
        inits.append(init)
        tails.append(act.simulate(P, bits[i, j:], start_height=1, height0=cut, initial=init))
    for i in range(3):
        t = tails[i]
        for name in ("status", "signal_count", "participation_halt", "enforcement_halt", "enforcement_on"):
            assert (getattr(t, name)[0] == getattr(full, name)[i, j:]).all(), (i, name)
    with pytest.raises(ValueError):
        act.simulate(P, bits[:, j:], start_height=1, height0=cut)


def test_vectorised_paths_equal_scalar_kernels():
    """Many paths at once == the scalar ACT kernels iterated per path (mainnet column)."""
    rng = np.random.default_rng(5)
    P = mainnet()
    n = 9000
    share = np.clip(0.75 + 0.2 * np.sin(np.arange(n) / 700.0), 0, 1)
    bits = rng.random((4, n)) < share[None, :] * np.array([1.0, 0.9, 0.8, 0.7])[:, None]
    s = act.simulate(P, bits, start_height=0, height0=0)
    W, thr = int(P["signalWindow"]), int(P["activationThreshold"])
    for i in range(4):
        st = K.ActivationState(K.SIGNALING, 0, 0)
        part = enf = False
        cnt = np.cumsum(bits[i])
        for h in range(n):
            c = int(cnt[h] - (cnt[h - W] if h >= W else 0))
            if h % 997 == 0:
                assert c == K.signal_count(bits[i].tolist(), h, W)
            st = K.activation_step(st, h, c, 0, W, thr, int(P["activationDelay"]))
            active = st.status == K.ACTIVE
            part = K.participation_halt_step(part, c, active, thr, int(P["participationFloor"]))
            enf = K.enforcement_halt_step(enf, c, active, int(P["enforcementResume"]),
                                          int(P["enforcementFloor"]))
            assert (int(s.status[i, h]), int(s.signal_count[i, h]), bool(s.participation_halt[i, h]),
                    bool(s.enforcement_halt[i, h])) == (st.status, c, part, enf), (i, h)


def test_abandoned_and_freeze_then_fix():
    rng = np.random.default_rng(6)
    e = rng.random((5, 400)) < 0.97
    e[1, 100:300] = True
    for ab in (1, 5, 60, 128):
        got = act.abandoned(e, ab)
        for i in range(5):
            for j in range(400):
                want = j - ab + 1 >= 0 and all(e[i, j - ab + 1:j + 1])
                assert bool(got[i, j]) == want
    W = 64
    height0 = 1000
    x = act.earliest_fix_start(e, W, height0=height0)
    for i in range(5):
        scan = [X for X in range(height0, height0 + 401)
                if act.start_admissible(e[i:i + 1], X, W, height0=height0)[0]]
        assert x[i] == (scan[0] if scan else -1)
    # the sunset clause: at or after the previous sunset any start is admissible
    assert act.start_admissible(np.zeros((1, 10), bool), 500, W, previous_enforce_until=400)[0]
    assert not act.start_admissible(np.zeros((1, 10), bool), 300, W, previous_enforce_until=400)[0]


def test_time_to_activation_and_first_window_lockin():
    P = regtest()
    t = act.time_to_activation(P, 0.77, 800, 2000, rng=np.random.default_rng(7))
    W, d = int(P["signalWindow"]), int(P["activationDelay"])
    p_first = act.p_lock_in_first_window(0.77, W, int(P["activationThreshold"]))
    assert 0.3 < p_first < 0.9
    assert abs((t == W - 1 + d).mean() - p_first) < 0.04
    assert (t[t >= 0] >= W - 1 + d).all() and (t >= 0).mean() > 0.95

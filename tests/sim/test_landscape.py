"""The real pool landscape replay (ybcal.sim.landscape, G5 wave 2): bootstrap, ACT-4/6 halt statistics
against the scalar node kernels, lock-in against the ACT-1..6 simulator, and the ACT-7 race model."""

from __future__ import annotations

import math

import numpy as np
import pytest

from ybcal.data.loaders import PoolShareLog
from ybcal.model import kernels as K
from ybcal.params.paramset import regtest
from ybcal.sim import activation as act
from ybcal.sim import landscape as L


def _log(seq, keys=("a", "b", "c", "d")):
    seq = np.asarray(seq, dtype=np.int32)
    return PoolShareLog(np.arange(seq.size, dtype=np.int64), seq, tuple(keys))


def test_landscape_names_merge_and_coalitions():
    rng = np.random.default_rng(0)
    seq = rng.choice(4, size=10_000, p=[0.5, 0.25, 0.15, 0.10])
    land = L.Landscape.from_log(_log(seq), top=3, names={"a": "big", "b": "flex"})
    assert land.names[:2] == ("big", "flex") and land.names[2].startswith("k2:")
    sh = land.shares()
    assert abs(sh["big"] - 0.5) < 0.02 and abs(sh["(others)"] - 0.10) < 0.02    # "d" is beyond top 3
    m = L.Landscape.from_log(_log(seq), top=4, names={"a": "big", "b": "x", "c": "y"},
                             merge={"flex": ["x", "y"]})
    assert m.names == ("big", "flex", "k3:d")
    assert m.keys["flex"] == ("b", "c")
    assert np.array_equal(m.indicator(["flex"]), np.isin(seq, [1, 2]))
    cs = L.coalitions(land, min_share=0.6)
    assert ("big", "flex") in cs and all(sum(sh[x] for x in c) >= 0.6 for c in cs)
    assert ("big",) not in cs
    with pytest.raises(KeyError):
        land.indicator(["nobody"])
    d = land.daily_shares(1000)
    assert d.shape == (10, 4) and np.allclose(d.sum(axis=1), 1.0)


def test_block_bootstrap_uses_contiguous_real_stretches():
    x = np.arange(1000)
    out = L.block_bootstrap(x, 2500, 7, np.random.default_rng(1), block_len=100)
    assert out.shape == (7, 2500)
    for row in out:
        for k in range(25):
            seg = row[k * 100:(k + 1) * 100]
            assert np.all(np.diff(seg) % 1000 == 1)          # consecutive, wrapping at the end
    with pytest.raises(ValueError):
        L.block_bootstrap(np.zeros(0), 10, 1, np.random.default_rng(0))


def _scalar_halts(ps, sig):
    """ACT-4/ACT-6 by the scalar node kernels, ACTIVE from the first full window."""
    W = int(ps["signalWindow"])
    c = np.convolve(sig.astype(int), np.ones(W, int), "valid")
    part = enf = False
    P, E = [], []
    for x in c:
        part = K.participation_halt_step(part, int(x), True, int(ps["activationThreshold"]),
                                         int(ps["participationFloor"]))
        enf = K.enforcement_halt_step(enf, int(x), True, int(ps["enforcementResume"]),
                                      int(ps["enforcementFloor"]))
        P.append(part)
        E.append(enf)
    return np.array(P), np.array(E)


def test_halt_stats_equal_the_scalar_kernels():
    ps = regtest()            # W 64, T 48, PF 39, EF 32, ER 39
    rng = np.random.default_rng(3)
    share = np.repeat(rng.choice([0.85, 0.62, 0.45, 0.30], size=60), 200)   # regime changes
    sig = rng.random(share.size) < share
    P, E = _scalar_halts(ps, sig)
    h = L.halt_stats(ps, sig[None, :].repeat(2, axis=0), chunk_paths=1)
    n = P.size
    years = 2 * n / 420_480
    assert h.part_hours_per_year == pytest.approx(P.sum() / n * 420_480 / 48)
    assert h.enf_hours_per_year == pytest.approx(E.sum() / n * 420_480 / 48)
    rises = lambda m: int(m[0]) + int((m[1:] & ~m[:-1]).sum())   # noqa: E731
    assert h.part_episodes_per_year == pytest.approx(2 * rises(P) / years)
    assert h.enf_episodes_per_year == pytest.approx(2 * rises(E) / years)
    assert P.any() and E.any()


def test_activation_stats_match_the_simulator():
    ps = regtest()
    rng = np.random.default_rng(4)
    sig = rng.random((40, 3000)) < 0.74
    a = L.activation_stats(ps, sig, horizons_days=(1, 2))
    s = act.simulate(ps, sig, start_height=0, height0=0, enforce_until=0)
    lock = s.lock_in_height
    assert a.p_first_window == pytest.approx(np.mean(lock == int(ps["signalWindow"]) - 1))
    assert a.p_within[2] == pytest.approx(np.mean((lock >= 0) & (lock <= 2 * 1152)))
    never = np.zeros((3, 500), dtype=bool)
    b = L.activation_stats(ps, never, horizons_days=(1,))
    assert b.p_first_window == 0 and math.isinf(b.median_days)


def test_race_probability_closed_form_and_monte_carlo():
    assert L.race_trip_probability(0.5, 6) == pytest.approx(2 / 7)
    assert L.race_trip_probability(0.0, 6) == 0.0 and L.race_trip_probability(1.0, 6) == 1.0
    # monotone: more blocks, less trips; more stock hash, more trips
    rtp = L.race_trip_probability
    assert rtp(0.3, 12) < rtp(0.3, 6) < rtp(0.4, 6)
    # vs the sequential race model on iid blocks: per-race probability from the trip rate
    q, V = 0.4, 4
    rng = np.random.default_rng(5)
    x = rng.random((400, 4000)) < q
    tr = L.valve_race_trips(x, V)
    # simulate single races directly: start at lead 1, absorb at -1 or V
    wins = 0
    n = 40_000
    for _ in range(n):
        lead = 1
        while -1 < lead < V:
            lead += 1 if rng.random() < q else -1
        wins += lead >= V
    assert wins / n == pytest.approx(L.race_trip_probability(q, V), abs=0.01)
    assert (tr >= 0).mean() > 0.99                          # back-to-back races trip quickly at q = 0.4
    none = L.valve_race_trips(np.zeros((3, 100), dtype=bool), 6)
    assert np.all(none == -1)


def test_drop_detection_blocks():
    ps = regtest()
    before = np.ones((2, 64), dtype=bool)
    after = np.zeros((2, 100), dtype=bool)
    d = L.drop_detection_blocks(ps, before, after)
    assert np.all(d == 64 - 32 + 1)          # count falls below EF 32 after 33 non-signalling blocks


def test_cli_landscape_writes_the_coalition_and_valve_tables(tmp_path, capsys):
    from ybcal.cli import main

    rng = np.random.default_rng(6)
    seq = rng.choice(3, size=6_000, p=[0.6, 0.3, 0.1])
    f = tmp_path / "pools.csv"
    f.write_text("height,payout_key\n" + "".join(f"{i},{'abc'[k]}\n" for i, k in enumerate(seq)))
    out = tmp_path / "land.csv"
    rc = main(["data", "landscape", str(f), "--window", "64,96", "--paths", "4", "--years", "0.02",
               "--block-days", "0.5", "--valve", "4,8", "--attack-days", "1", "--out", str(out)])
    assert rc == 0
    text = out.read_text().splitlines()
    assert text[0].startswith("activate_extra_days") or "coalition" in text[0]
    assert len(text) > 2 and (tmp_path / "land-valve.csv").exists()
    assert "valve" in capsys.readouterr().out

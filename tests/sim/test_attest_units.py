"""Attestation simulator: weight / founding / ageCap edges, dormancy and revival, PIN-2, generation,
determinism, PIN-1 series, input schema."""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from ybcal.model import kernels as K
from ybcal.params.paramset import mainnet, regtest
from ybcal.sim import attest as att

from ._harness import Scenario, run_reference
from .test_attest_reference import _compare, _p_mint

KEY = bytes([9]) * 20


def _flat_scenario(n, roster, demands=(), price=50_000):
    tags = [(True, price, KEY) for _ in range(n)]
    hashes = {h: f"{h * 7919:064x}" for h in range(1, n + 1)}
    return Scenario(n=n, tags=tags, hashes=hashes, roster=roster, demands=list(demands))


def _run_both(sc, P=None):
    m = run_reference(sc)
    res = att.simulate(P or regtest(), {"attest": {"roster": sc.roster, "demands": sc.demands,
                                                   "block_hashes": sc.hashes, "height0": 1,
                                                   "start_height": 1, "record_status": True}},
                       {"p_mint": _p_mint(m, sc.n)})
    _compare(res, m, sc.n)
    return res, m


def _signer(reg, n, bond, k=4, phase=0, **kw):
    sh = [c for c in range(reg, n + 1) if (c - phase) % k == 0]
    return {"bond_zat": bond, "register_height": reg, "sign_heights": sh, "sign_prices": [50_000] * len(sh),
            **kw}


def test_founding_window_boundary_and_age_cap_ranking():
    """regtest: maturity 8, armMin 3 → trigger at 20; foundingWindow 16 → a registrant at 36 ages from
    20, one at 37 from 37; ageCap 64 caps the founders at 84, after which the larger late bonds
    overtake them (a seating change with no status event)."""
    n = 220
    G = 10**9
    roster = [_signer(10, n, G), _signer(11, n, G), _signer(12, n, G),
              _signer(36, n, 2 * G), _signer(37, n, 2 * G), _signer(40, n, 3 * G), _signer(41, n, 3 * G)]
    res, _m = _run_both(_flat_scenario(n, roster))
    assert res.trigger_height[0] == 20
    w = lambda bond, H, origin: K.bond_weight(bond, H - origin, 64)  # noqa: E731
    # at H = 60 (all eligible): founders 1G·40, seq3 2G·40 (founding), seq4 2G·23, seq5/6 3G·(20/19)
    assert w(2 * G, 60, 20) > w(2 * G, 60, 37)
    seats = [tuple(res.seated_at(0, h)) for h in range(50, n + 1)]
    changes = [h for h, (a, b) in enumerate(itertools.pairwise(seats), start=51) if a != b]
    events = set(int(t["height"]) for t in res.transitions)
    assert any(h not in events for h in changes), (changes, events)


def test_ties_are_broken_by_seq():
    n = 120
    roster = [_signer(10 + (i % 2), n, 10**9) for i in range(8)]        # equal weights in pairs
    res, m = _run_both(_flat_scenario(n, roster))
    for h in range(40, n + 1):
        assert res.seated_at(0, h) == list(m.snapshots[h].seated)
    # registered at 10: seqs 0..3 (roster 0,2,4,6), at 11: 4..7 → the four oldest plus seq 4
    assert res.seated_at(0, 100) == [0, 1, 2, 3, 4]


def test_dormancy_needs_seat_age_selection_and_check_height():
    """A seated attestor that stops signing goes DORMANT only at H % dormancyCheck == 0, once seated
    for dormancyBlocks and selected in ≥ dormancyMinBundles rows of (H − dormancyBlocks, H] it did
    not sign; then REV-1 restores it at the first height with a fresh own attestation."""
    n = 200
    roster = [_signer(5, n, 10**9) for _ in range(3)]
    silent = _signer(5, n, 10**9)
    silent["sign_heights"] = [c for c in silent["sign_heights"] if not 90 <= c < 140]
    silent["sign_prices"] = [50_000] * len(silent["sign_heights"])
    silent["revive_delay"] = 3
    roster.append(silent)
    demands = [{"height": h, "ref_height": h - 2, "kind": "mint", "selector": b""}
               for h in range(30, n + 1, 3)]
    res, _m = _run_both(_flat_scenario(n, roster, demands))
    t = res.transitions
    dorm = t[(t["cause"] == att.CAUSE_DORMANT) & (t["seq"] == 3)]
    assert len(dorm) >= 1
    h = int(dorm["height"][0])
    assert h % 4 == 0 and 90 < h < 160
    rev = t[(t["cause"] == att.CAUSE_REVIVE) & (t["seq"] == 3)]
    assert len(rev) and int(rev["height"][0]) >= max(h + 3, 141)
    # the others never go dormant
    assert set(int(s) for s in t[t["cause"] == att.CAUSE_DORMANT]["seq"]) == {3}


def test_pin2_excludes_the_pinned_seq_from_selection():
    """A stuck attestor (one price) is pinned on a ≥ pinDelta pMint move and is never selected while
    pinned."""
    n = 260
    roster = [_signer(5, n, 10**9) for _ in range(4)]
    roster[2]["sign_prices"] = [77_777] * len(roster[2]["sign_heights"])       # frozen feed
    tags = []
    for h in range(1, n + 1):
        p = 50_000 if h < 150 else 60_000
        tags.append((True, p, bytes([h % 3 + 1]) * 20))
    hashes = {h: f"{h * 104729:064x}" for h in range(1, n + 1)}
    for e in roster:
        if e is not roster[2]:
            e["sign_prices"] = [50_000 if c < 150 else 60_000 for c in e["sign_heights"]]
    demands = [{"height": h, "ref_height": h - 1, "kind": "claim", "selector": bytes([h % 256]) * 36}
               for h in range(30, n + 1)]
    sc = Scenario(n=n, tags=tags, hashes=hashes, roster=roster, demands=demands)
    res, _m = _run_both(sc)
    pinned = res.pinned_seqs[0]
    assert (pinned & (1 << 2)).any()
    d = res.demands
    for rec in d:
        j = rec["ref_height"] - 1
        if int(pinned[j]) & (1 << 2):
            assert not int(rec["selected"]) & (1 << 2)


def test_pin1_series_equals_kernel():
    rng = np.random.default_rng(0)
    rh = np.sort(rng.choice(np.arange(1, 2000), 300, replace=False))
    am = rng.integers(900, 1100, 300)
    am[rng.random(300) < 0.1] = 0
    heights = np.arange(1, 2100)
    for pw, mb, delta in ((16, 2, 500), (288, 3, 100), (50, 1, 0)):
        got = att.pin1_trigger_series(rh, am, heights, pin_window=pw, start_height=1, pin_min_bundles=mb,
                                      pin_delta_bps=delta)
        for i, H in enumerate(heights):
            sel = (rh >= max(H - pw, 1)) & (rh < H)
            want = K.pin1_triggered([int(x) if x > 0 else None for x in am[sel]], mb, delta)
            assert bool(got[i]) == want, (pw, H)


def _gen_inputs(paths=3, n=3000, seed=11, **over):
    rng = np.random.default_rng(1)
    tp = (50_000 * np.exp(np.cumsum(rng.normal(0, 0.002, (paths, n)), axis=1))).astype(np.int64)
    roster = [{"bond_zat": 10**9 * (1 + i % 3), "register_height": 5 + 3 * i} for i in range(7)]
    a = {"roster": roster, "true_price": tp, "uptime": 0.9, "mean_outage_blocks": 20, "noise_bps": 30,
         "demand_rate": 0.3, "seed": seed, "height0": 1, "start_height": 1}
    a.update(over)
    return {"attest": a}


def test_generated_roster_is_deterministic_and_path_independent():
    P = regtest()
    a = att.simulate(P, _gen_inputs())
    b = att.simulate(P, _gen_inputs())
    for name in ("status", "seated", "pinned_seqs", "row_a_mint", "eligible_count"):
        assert (getattr(a, name) == getattr(b, name)).all()
    assert (a.demands == b.demands).all()
    one = att.simulate(P, {"attest": {**_gen_inputs()["attest"], "true_price":
                                      _gen_inputs()["attest"]["true_price"][:1]}})
    assert (one.seated[0] == a.seated[0]).all() and (one.row_a_mint[0] == a.row_a_mint[0]).all()
    c = att.simulate(P, _gen_inputs(seed=12))
    assert not (c.demands["height"].size == a.demands["height"].size
                and (c.demands["height"] == a.demands["height"]).all())


def test_dead_attestor_is_ejected_within_the_detection_bound():
    P = regtest()
    inp = _gen_inputs(paths=4, uptime=1.0, mean_outage_blocks=None, demand_rate=0.5)
    inp["attest"]["roster"][1]["outages"] = [(600, 10**9)]
    res = att.simulate(P, inp)
    db, dc = int(P["dormancyBlocks"]), int(P["dormancyCheck"])
    for i in range(4):
        seq = res.seq_of_roster[i][1]
        t = res.transitions[(res.transitions["path"] == i) & (res.transitions["seq"] == seq)
                            & (res.transitions["cause"] == att.CAUSE_DORMANT)]
        assert len(t) == 1 and 600 < int(t["height"][0]) <= 600 + db + dc + 60
        assert not len(res.transitions[(res.transitions["path"] == i) & (res.transitions["seq"] == seq)
                                       & (res.transitions["cause"] == att.CAUSE_REVIVE)])
    # honest, always-online attestors are never made dormant
    others = res.transitions[(res.transitions["cause"] == att.CAUSE_DORMANT)]
    assert set(int(s) for s in others["seq"]) == {res.seq_of_roster[0][1]}


def test_bundle_prices_follow_bias_and_noise():
    P = regtest()
    inp = _gen_inputs(paths=2, uptime=1.0, mean_outage_blocks=None, noise_bps=0)
    for e in inp["attest"]["roster"]:
        e["bias_bps"] = 100
    res = att.simulate(P, inp)
    d = res.demands[res.demands["success"]]
    tp = inp["attest"]["true_price"]
    # every signer reports true·1.01 at its cited height (≤ 2k blocks before R): aMint within the
    # range of true prices over (R − 8, R], scaled
    for rec in d[:200]:
        lo = tp[rec["path"], rec["ref_height"] - 8:rec["ref_height"]].min() * 1.01
        hi = tp[rec["path"], rec["ref_height"] - 8:rec["ref_height"]].max() * 1.01
        assert lo - 2 <= rec["a_mint"] <= hi + 2


def test_markov_online_stationary_uptime_and_outage_length():
    rng = np.random.default_rng(3)
    x = att.markov_online(2_000_000, 0.95, 48, rng)
    assert abs(x.mean() - 0.95) < 0.005
    d = np.diff(np.concatenate([[1], x.astype(int), [1]]))
    starts, ends = np.nonzero(d == -1)[0], np.nonzero(d == 1)[0]
    assert abs((ends - starts).mean() - 48) < 3
    assert att.markov_online(10, 1.0, 5, rng).all() and not att.markov_online(10, 0.0, 5, rng).any()


def test_unarmed_and_snapshotless_demands():
    P = regtest().replace(attestArmMin=0)        # never arms: no bundle is ever needed
    res = att.simulate(P, _gen_inputs(paths=1, n=500))
    assert (res.status == att.UNARMED).all() and not res.bundle_row.any()
    assert (res.demands["reason"] == att.R_UNARMED).all()
    res = att.simulate(regtest(), _gen_inputs(paths=1, n=500, demands=[{"height": 1, "ref_height": 0},
                                                                         {"height": 300, "ref_height": 300}]))
    assert list(res.demands["reason"]) == [att.R_UNARMED, att.R_NO_SNAPSHOT]


def test_bitmask_limit_and_missing_inputs():
    P = regtest()
    with pytest.raises(ValueError):
        att.simulate(P, {})
    roster = [{"bond_zat": 10**9, "register_height": 5, "sign_heights": [], "sign_prices": []}] * 65
    with pytest.raises(ValueError):
        att.simulate(P, {"attest": {"roster": roster, "n_blocks": 50, "height0": 1, "start_height": 1}})


def test_mainnet_runtime_smoke():
    """10 paths × 7 days at mainnet scale (documented runtime is for 100 × 30 days)."""
    P = mainnet()
    n = 7 * 1152
    rng = np.random.default_rng(2)
    tp = (500_000 * np.exp(np.cumsum(rng.normal(0, 0.001, (10, n)), axis=1))).astype(np.int64)
    roster = [{"bond_zat": 20_000 * 10**8 * (1 + i % 4), "register_height": -40_000 + 500 * i}
              for i in range(14)]
    res = att.simulate(P, {"attest": {"roster": roster, "true_price": tp, "uptime": 0.95,
                                      "mean_outage_blocks": 48, "demand_rate": 1 / 48, "seed": 1,
                                      "height0": 0, "start_height": -50_000,
                                      "initial_trigger_height": -30_000, "initial_seated_since": -20_000}})
    assert res.meta["seconds"] < 20
    assert (res.status == att.ARMED).all() and res.bundle_success_rate() > 0.9

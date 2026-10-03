"""Attestation simulator vs the reference model: the golden chain and randomised scenarios."""

from __future__ import annotations

import numpy as np
import pytest

from ybcal.model import golden
from ybcal.model import kernels as K
from ybcal.model import reference as ref
from ybcal.params.paramset import regtest
from ybcal.sim import attest as att

from ._harness import random_scenario, run_reference


def _p_mint(model, n):
    return np.array([model.snapshots[h].p_mint or -1 for h in range(1, n + 1)], dtype=np.int64)


def _compare(res, model, n, path=0):
    bad = []
    for h in range(1, n + 1):
        s = model.snapshots[h]
        got = (int(res.status[path, h - 1]), res.seated_at(path, h), res.pinned_at(path, h))
        want = (s.attest.status, list(s.seated), list(s.pinned_seqs))
        if got != want:
            bad.append((h, got, want))
    assert not bad, bad[:5]
    rows = res.bundle_log[path]
    assert sorted(rows) == sorted(model.bundle_log)
    for h, r in rows.items():
        g = model.bundle_log[h]
        assert (r.a_mint, r.a_claim, list(r.selected_seqs), list(r.seqs), list(r.prices),
                list(r.cited_heights)) == (g.a_mint, g.a_claim, g.selected_seqs, g.seqs, g.prices,
                                           g.cited_heights), h
    finals = {f.seq: f for f in res.attestors[path]}
    assert sorted(finals) == sorted(model.attestors)
    for seq, r in model.attestors.items():
        f = finals[seq]
        assert (f.status, f.status_height, f.seated_since, f.bond_spent_height, f.register_height,
                f.bond_zat) == (r.status, r.status_height, r.seated_since, r.bond_spent_height,
                                r.register_height, r.bond_zat), seq
    a = model.attest
    if a.status == ref.UNARMED:
        assert res.trigger_height[path] == -1
    else:
        assert (res.trigger_height[path], res.arm_height[path]) == (a.trigger_height, a.arm_height)
    # PIN-1 trigger from the rows equals the kernel over the model's BundleLog
    p = model.params
    for h in range(1, n + 1):
        lo = max(h - p.pin_window, p.start_height)
        a_mints = [model.bundle_log[x].a_mint for x in range(lo, h) if x in model.bundle_log]
        assert bool(res.pin1_triggered[path, h - 1]) == K.pin1_triggered(a_mints, p.pin_min_bundles,
                                                                          p.pin_delta_bps), h
        assert bool(res.bundle_row[path, h - 1]) == (h in model.bundle_log)


# ---------------------------------------------------------------------------------------------------
# golden chain (440 snapshots)


@pytest.fixture(scope="module")
def golden_replay():
    """The golden replay with the bundle-bearing transactions recorded (height, refHeight, selector)."""
    calls = []

    class Rec(ref.YellowbackModel):
        def _apply_tx(self, tx, height):
            self._h = height
            return super()._apply_tx(tx, height)

        def _bundle(self, tx, r, sel, skip):
            b = super()._bundle(tx, r, sel, skip)
            calls.append((self._h, r, bytes(sel), b["ok"], b["atts"]))
            return b

    doc = golden.load_golden_doc()
    p = doc["params"]
    m = Rec(ref.Params.regtest(int(p["startHeight"]), int(p["sigmaRefBps"]), int(p["supplyCapBps"]),
                               int(p["enforceUntil"]), int(p["attestArmMin"]), int(p["bundleCarrier"])))
    for b in doc["blocks"]:
        txs = [ref.tx_from_hex(x) for x in b["txs"]]
        m.feed_block(int(b["height"]), b["hash"], txs[0].vin[0].script_sig.hex(), int(b["subsidyZat"]),
                     txs[1:])
    assert m.state_hash() == doc["stateHash"]
    return m, doc, calls


def _golden_inputs(m, doc, calls):
    # every attestation that appears in a verified bundle, per seq (the chain's signing record)
    sign: dict[int, dict[int, int]] = {}
    for _h, _r, _s, ok, atts in calls:
        if ok:
            for seq, price, cited in atts:
                sign.setdefault(seq, {})[cited] = price
    roster = []
    revive_tx = {tl.attestor_seq: tl.height for tl in m.txlog.values() if tl.type == "ATTESTOR_REVIVE"}
    eqv_tx = {tl.attestor_seq: tl.height for tl in m.txlog.values() if tl.type == "EQUIVOCATION"}
    for seq, r in sorted(m.attestors.items()):
        sh = sorted(sign.get(seq, {}))
        e = {"bond_zat": r.bond_zat, "register_height": r.register_height, "sign_heights": sh,
             "sign_prices": [sign[seq][c] for c in sh], "revive": False}
        if seq in eqv_tx:
            e["equivocate_at"] = eqv_tx[seq]
        if seq in revive_tx:
            e["revive_at"] = [revive_tx[seq]]
        if r.bond_spent_height:
            e["withdraw_height"] = r.bond_spent_height
        roster.append(e)
    demands = [{"height": h, "ref_height": r, "kind": "mint" if s == b"" else "claim", "selector": s}
               for h, r, s, ok, _a in calls if ok]
    hashes = {int(b["height"]): b["hash"] for b in doc["blocks"]}
    return {"attest": {"roster": roster, "demands": demands, "block_hashes": hashes, "height0": 1,
                       "start_height": 1, "record_status": True}}


def test_golden_chain_attestation_history(golden_replay):
    m, doc, calls = golden_replay
    n = m.tip_height
    res = att.simulate(regtest(), _golden_inputs(m, doc, calls), {"p_mint": _p_mint(m, n)})
    _compare(res, m, n)
    # the chain's v3 events, as transitions
    causes = [(int(t["seq"]), int(t["height"]), att.CAUSE_NAMES[t["cause"]]) for t in res.transitions]
    assert (3, 304, "DORMANT") in causes and (0, 306, "EJECT") in causes
    assert (3, 308, "REVIVE") in causes and (2, 440, "WITHDRAW") in causes
    assert (0, 440, "WITHDRAW") not in causes          # EJECTED stays EJECTED; the spend is recorded
    assert res.demands["success"].all() and len(res.demands) == 3


def test_golden_eligible_count_and_status_record(golden_replay):
    m, doc, calls = golden_replay
    n = m.tip_height
    res = att.simulate(regtest(), _golden_inputs(m, doc, calls), {"p_mint": _p_mint(m, n)})
    st = res.attestor_status[0]
    assert res.eligible_count[0, 235 - 1] == 3 and res.trigger_height[0] == 235
    assert int(st[3, 304 - 1]) == att.ELIGIBLE and int(st[3, 305 - 1]) == att.DORMANT
    assert (res.eligible_count[0] == (st == att.ELIGIBLE).sum(axis=0)).all()


# ---------------------------------------------------------------------------------------------------
# randomised scenarios: seating with ranking (more eligible than nSlots), founding window / ageCap,
# PIN-2 pins, dormancy, dynamic and explicit REV-1, EQV-1, bond spends, a sub-minimum bond


@pytest.mark.parametrize("seed", range(8))
def test_random_scenarios_match_reference(seed):
    sc = random_scenario(seed)
    m = run_reference(sc)
    res = att.simulate(regtest(), {"attest": {"roster": sc.roster, "demands": sc.demands,
                                              "block_hashes": sc.hashes, "height0": 1, "start_height": 1}},
                       {"p_mint": _p_mint(m, sc.n)})
    _compare(res, m, sc.n)


def test_random_scenarios_exercise_the_rules():
    """The randomised scenarios do reach every branch the comparison is meant to cover."""
    seen = {"pinned": 0, "dormant": 0, "revive": 0, "eject": 0, "withdraw": 0, "ranked": 0, "fail": 0}
    for seed in range(8):
        sc = random_scenario(seed)
        m = run_reference(sc)
        res = att.simulate(regtest(), {"attest": {"roster": sc.roster, "demands": sc.demands,
                                                  "block_hashes": sc.hashes, "height0": 1,
                                                  "start_height": 1}},
                           {"p_mint": _p_mint(m, sc.n)})
        seen["pinned"] += int((res.pinned_seqs[0] != 0).sum())
        names = [att.CAUSE_NAMES[c] for c in res.transitions["cause"]]
        seen["dormant"] += names.count("DORMANT")
        seen["revive"] += names.count("REVIVE")
        seen["eject"] += names.count("EJECT")
        seen["withdraw"] += names.count("WITHDRAW")
        seen["ranked"] += int((res.eligible_count[0] > 5).sum())
        seen["fail"] += int((res.demands["armed"] & ~res.demands["success"]).sum())
    assert all(v > 0 for v in seen.values()), seen


def test_withdraw_is_clamped_to_the_bond_lock():
    P = regtest()
    roster = [{"bond_zat": 10**9, "register_height": 10, "sign_heights": [], "sign_prices": [],
               "withdraw_height": 20}]
    res = att.simulate(P, {"attest": {"roster": roster, "demands": [], "n_blocks": 400, "height0": 1,
                                      "start_height": 1}})
    t = res.transitions[res.transitions["cause"] == att.CAUSE_WITHDRAW]
    assert int(t["height"][0]) == 10 + int(P["bondMinLock"]) + 1

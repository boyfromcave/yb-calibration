"""Test-only reference harness: drives the vendored YellowbackModel with synthetic events.

The model's own v3 code does every SNAP step (maturity, ARM, PIN-2, seating, dormancy) and the
selection (``YellowbackModel.selected``) and weights (``YellowbackModel.weight``); the harness only
plays the transactions: REG-A1 / EQV-1 / bond spend / REV-1 write the records the way the model's
``_apply_*`` do, and a demand assembles the BuildBundle bundle (every selected seq with a fresh
attestation, seq order) and records it into the model's BundleLog accumulator exactly like
``YellowbackModel._bundle``. Coinbase tags carry signal bits and quotes, so the model computes pMint
(and PIN-1) itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ybcal.model import reference as ref
from ybcal.model import reference_attest as ya


@dataclass
class Scenario:
    n: int                                  # blocks 1..n (startHeight 1)
    tags: list                              # per height: (signal, price, key) or None
    hashes: dict                            # height -> hex
    roster: list                            # sim roster entries (explicit sign heights/prices)
    demands: list                           # [{"height", "ref_height", "kind", "selector"}]
    regtest_kwargs: dict = field(default_factory=dict)


class _Ev:
    is_coinbase = False

    def __init__(self, fn):
        self.fn = fn
        self.txid = "00" * 32


class Harness(ref.YellowbackModel):
    def _apply_tx(self, tx, height):
        tx.fn(self, height)
        return None


def run_reference(sc: Scenario):
    """Feed the scenario through the model; returns the model (snapshots, attestors, bundle_log)."""
    params = ref.Params.regtest(1, **sc.regtest_kwargs)
    m = Harness(params)
    p = params
    bond_min = p.bond_min
    # roster in registration order (sim assigns seqs the same way)
    order = sorted(range(len(sc.roster)), key=lambda i: (sc.roster[i]["register_height"], i))
    seq_of: dict[int, int] = {}
    terminal: dict[int, int] = {}
    by_h: dict[int, list] = {}

    def at(h, fn):
        by_h.setdefault(h, []).append(fn)

    for i in order:
        e = sc.roster[i]
        if e["bond_zat"] < bond_min or e["register_height"] < p.start_height:
            continue

        def reg(model, h, i=i, e=e):
            r = ref.AttestorRecord()
            r.attestor_pubkey = bytes([2]) + i.to_bytes(32, "big")
            r.bond_zat = e["bond_zat"]
            r.register_height = h
            r.status = ref.A_PENDING
            r.status_height = h
            seq_of[i] = model.attestor_seq
            model.attestors[model.attestor_seq] = r
            model.attestor_seq += 1
        at(e["register_height"], reg)
    # in-block order: registrations, EQV-1, bond spends, REV-1, then bundles
    for i in order:
        e = sc.roster[i]
        eq = e.get("equivocate_at")
        for x in ([eq] if isinstance(eq, int) else (eq or ())):
            def eject(model, h, i=i):
                s = seq_of.get(i)
                r = model.attestors.get(s) if s is not None else None
                if r is not None and r.status not in (ref.A_WITHDRAWN, ref.A_EJECTED):
                    r.status, r.status_height = ref.A_EJECTED, h
                    terminal[s] = h
            at(x, eject)
    for i in order:
        e = sc.roster[i]
        if e.get("withdraw_height") is not None:
            def spend(model, h, i=i):
                s = seq_of.get(i)
                r = model.attestors.get(s) if s is not None else None
                if r is None or r.bond_spent_height:
                    return
                if r.status != ref.A_EJECTED:
                    r.status, r.status_height = ref.A_WITHDRAWN, h
                    terminal.setdefault(s, h)
                r.bond_spent_height = h
            at(max(e["withdraw_height"], e["register_height"] + p.bond_min_lock + 1), spend)
    for i in order:
        for x in sc.roster[i].get("revive_at", ()):
            def rev(model, h, i=i):
                s = seq_of.get(i)
                r = model.attestors.get(s) if s is not None else None
                if r is not None and r.status == ref.A_DORMANT:
                    r.status, r.status_height = ref.A_ELIGIBLE, h
            at(x, rev)

    def sign_list(seq):
        i = next(k for k, v in seq_of.items() if v == seq)
        e = sc.roster[i]
        return list(zip(e["sign_heights"], e["sign_prices"], strict=True)), e

    def fresh(seq, R):
        best = None
        sl, _e = sign_list(seq)
        for c, price in sorted(sl):
            if R - p.attest_max_age < c <= R and c >= p.start_height and c < terminal.get(seq, 1 << 62):
                best = (c, price)
        return best

    def demand(model, h, d):
        R = d["ref_height"]
        if not model.armed_at(R):
            return
        sel = d["selector"]
        selected = model.selected(R, sel)
        chosen = [(s, *f) for s in sorted(selected) if (f := fresh(s, R)) is not None]
        if not (p.m_select <= len(chosen) <= p.bundle_max) or not chosen:
            return
        snap = model.snapshots[R]
        rows = [((s, price), model.weight(model.attestors[s], snap.attest, R)) for s, _c, price in chosen]
        stat = ya.bundle_stat(rows, p.q_low_bps, p.q_high_bps, p.m_select)
        acc = model._bundle_acc
        if stat is not None:
            acc["a_mints"].append(stat[0])
            acc["a_claims"].append(stat[1])
        acc["selected"].update(selected)
        acc["pairs"].update((s, price, c) for s, c, price in chosen)
        acc["any"] = True

    for d in sc.demands:
        at(d["height"], lambda model, h, d=d: demand(model, h, d))

    def dyn_revive(model, h):
        s = revive_due_rev.get(h)
        for seq in s or ():
            r = model.attestors[seq]
            if r.status == ref.A_DORMANT:
                r.status, r.status_height = ref.A_ELIGIBLE, h

    revive_due_rev: dict[int, list] = {}
    for h in range(1, sc.n + 1):
        evs = list(by_h.get(h, []))
        tag = sc.tags[h - 1]
        cb = ref.height_prefix(h)
        if tag is not None:
            sig, price, key = tag
            cb += ref.tag_push(1 if sig else 0, price, 0, key)
        # dynamic REV-1 goes with the other REV-1 transactions (before the bundles)
        n_pre = sum(1 for _ in evs) - sum(1 for d in sc.demands if d["height"] == h)
        txs = [_Ev(f) for f in evs[:n_pre]] + [_Ev(dyn_revive)] + [_Ev(f) for f in evs[n_pre:]]
        m.feed_block(h, sc.hashes[h], cb.hex(), 0, txs)
        # schedule dynamic revivals for attestors that went DORMANT at this SNAP
        for seq, r in m.attestors.items():
            if r.status == ref.A_DORMANT and r.status_height == h:
                i = next(k for k, v in seq_of.items() if v == seq)
                e = sc.roster[i]
                if not e.get("revive", True):
                    continue
                T = h + max(1, e.get("revive_delay", 1))
                for H in range(T, sc.n + 1):
                    if any(H - p.attest_max_age < c <= H - 1 and c >= p.start_height
                           and c < terminal.get(seq, 1 << 62) for c in e["sign_heights"]):
                        revive_due_rev.setdefault(H, []).append(seq)
                        break
    return m


def random_scenario(seed: int, n: int = 360, n_att: int = 8) -> Scenario:
    rng = np.random.default_rng(seed)
    keys = [bytes([k + 1]) * 20 for k in range(5)]
    price = 50_000
    true = []
    tags = []
    for _h in range(1, n + 1):
        if rng.random() < 0.06:
            price = int(price * (1 + rng.choice([-0.12, 0.12])))
        price = max(1_000, int(price * (1 + rng.normal(0, 0.01))))
        true.append(price)
        r = rng.random()
        if r < 0.1:
            tags.append(None)
        else:
            k = int(rng.integers(0, len(keys)))
            q = 47_000 if k == 4 else int(price * (1 + rng.normal(0, 0.003)))   # key 4 is a stuck feed
            tags.append((bool(rng.random() < 0.97), q, keys[k]))
    hashes = {h: rng.bytes(32).hex() for h in range(1, n + 1)}
    roster = []
    kint = 4
    for i in range(n_att):
        reg = int(rng.integers(5, 140))
        bond = int(rng.choice([1, 2, 3, 5, 8])) * 10**9
        if i == n_att - 1:
            bond = 10**9 - 1                     # below bondMin: never registers
        phase = int(rng.integers(0, kint))
        online = np.ones(n + 1, dtype=bool)
        for _ in range(int(rng.integers(0, 4))):
            a = int(rng.integers(reg, n))
            online[a:a + int(rng.integers(5, 60))] = False
        if i == 1:
            online[200:] = False                 # goes silent: dormancy
        sh = [c for c in range(reg, n + 1) if (c - phase) % kint == 0 and online[c]]
        frozen = i == 2
        sp = [true[c - 1] if not frozen else 33_333 for c in sh]
        sp = [int(x * (1 + rng.normal(0, 0.002))) if not frozen else x for x in sp]
        e = {"bond_zat": bond, "register_height": reg, "sign_heights": sh, "sign_prices": sp,
             "revive": i != 3, "revive_delay": int(rng.integers(1, 6))}
        if i == 4:
            e["equivocate_at"] = int(rng.integers(150, 300))
        if i == 5:
            e["withdraw_height"] = int(rng.integers(reg + 150, n + 40))
        if i == 6:
            e["revive_at"] = [int(rng.integers(200, n))]
        roster.append(e)
    demands = []
    for h in range(2, n + 1):
        for _ in range(int(rng.poisson(0.7))):
            lag = int(rng.choice([1, 2, 2, 3, 5]))
            if rng.random() < 0.5:
                demands.append({"height": h, "ref_height": h - lag, "kind": "mint", "selector": b""})
            else:
                demands.append({"height": h, "ref_height": h - lag, "kind": "claim",
                                "selector": rng.bytes(36)})
    return Scenario(n=n, tags=tags, hashes=hashes, roster=roster, demands=demands)

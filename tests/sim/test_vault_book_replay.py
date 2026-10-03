"""End-to-end: the block-mode vault book (``VaultHook`` inside ``simulate_blocks``) == the reference model.

Every event the book records (mints, VOIDs, owner redeems, claims, sweeps, thefts, VOID releases) is
turned into a real transaction and fed, block by block with the same coinbase tags, to the vendored
``YellowbackModel``. Per height the reference's Totals (supply, collateral, unbacked, counts) and
haltMask must equal the engine's arrays (the HALT-2 the engine recomputes from the book's totals),
every transaction's verdict must equal the book's, and the final vault statuses must agree.
"""

from __future__ import annotations

import numpy as np
import pytest

from ybcal.model import reference as R
from ybcal.params.paramset import regtest
from ybcal.sim import agents as AG
from ybcal.sim import engine as E
from ybcal.sim import vaults as VB

from .refbuild import PAYEE, mint_tx, owner_pubkey, spend_tx, txid

START = 5


def price_path(n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    p = 2_000_000 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    # crash, hold low (claims open as the slow median catches up), partial recovery (claims pay)
    a, b, c = 260, 290, 420
    f = np.ones(n)
    f[a:b] = np.linspace(1.0, 0.15, b - a)
    f[b:c] = 0.15
    f[c:] = np.linspace(0.15, 0.45, n - c)
    return np.maximum(np.rint(p * f), 200).astype(np.int64)


def inputs_for(ps, n, seed, *, dropout=None):
    tp = price_path(n, seed)
    sig = np.ones(n, dtype=bool)
    if dropout is not None:
        sig[dropout[0] : dropout[1]] = False
    inp = E.BlockInputs(
        true_price=tp,
        tag_present=np.ones(n, bool),
        tag_price=tp,
        tag_pool=np.zeros(n, np.int16),
        signal_bit=sig,
        start_height=START,
    )
    return inp


def replay(ps, inp, series, res, events):
    """Feed the same chain to the reference model; return it with per-height totals."""
    p = R.Params.regtest(
        start_height=START, sigma_ref_bps=int(ps["sigmaRefBps"]), supply_cap_bps=int(ps["supplyCapBps"])
    )
    m = R.YellowbackModel(p)
    grace = int(ps["grace"])
    by_h: dict[int, list[dict]] = {}
    for e in events:
        by_h.setdefault(e["height"], []).append(e)
    mints: dict[int, tuple[str, bytes, VB.VaultRecord]] = {}
    out_totals = []
    n = inp.n_blocks
    verdicts = []
    for j in range(n):
        h = START + j
        cb = R.height_prefix(h) + R.tag_push(
            1 if inp.signal_bit[0, j] else 0, int(inp.tag_price[0, j]), 0, PAYEE
        )
        txs = []
        for e in by_h.get(h, []):
            vid = e["vid"]
            if e["kind"] == "mint":
                tx = VB.MintTx(
                    e["term_class"],
                    e["cents"],
                    e["lock_height"],
                    e["ref_height"],
                    e["collateral_zat"],
                    fee_zat=e["fee_zat"],
                    attest_fee_zat=e["attest_fee_zat"],
                )
                tid = txid("mint", vid)
                t, _pl, _ = mint_tx(tx, grace, owner_pubkey(vid), tid)
                rec = VB.VaultRecord(
                    tx.term_class,
                    tx.lock_height,
                    tx.lock_height + grace,
                    tx.collateral_zat,
                    tx.cents,
                    h,
                    tx.ref_height,
                )
                mints[vid] = (tid, owner_pubkey(vid), rec)
                txs.append(t)
                verdicts.append((tid, e["verdict"]))
            else:
                tid0, owner, rec = mints[vid]
                if e["kind"] == "void-release":
                    sp = VB.SpendTx("owner", h - 1, redeem_payload=False)
                else:
                    sp = VB.SpendTx(
                        e["path"],
                        e["ref_height"],
                        yed_in=e["yed_in"],
                        redeem_payload=e["payload"],
                        fee_zat=e["fee_zat"],
                        attest_fee_zat=e["attest_fee_zat"],
                        owner_paid_zat=rec.collateral_zat if e["path"] == "claim" and e["payload"] else 0,
                    )
                tid = txid(e["kind"], vid)
                t, _pl, _ = spend_tx(sp, (tid0, 0), (tid0, 1), rec, owner, grace, tid)
                txs.append(t)
                if e["kind"] != "void-release":
                    verdicts.append((tid, e["verdict"]))
        bv = m.feed_block(h, f"{h:064x}", cb.hex(), R.regtest_subsidy(h), txs)
        assert not (bv.block_invalid and bv.enforcement_on), f"BLK-1 at {h}: {bv.reason}"
        t = m.totals
        s = m.snapshot(h)
        out_totals.append((t.supply_cents, t.collateral_zat, t.unbacked_cents, s.halt_mask))
    return m, out_totals, verdicts


SCENARIOS = {
    "caps-claims": dict(sigma=0, seed=1, dropout=None, rate=1152 / 4, defector=0.0, ref="adversarial"),
    "enforcement-off": dict(
        sigma=10_000, seed=2, dropout=(330, 470), rate=1152 / 5, defector=0.4, ref="adversarial"
    ),
    "wallet-ref": dict(sigma=20_000, seed=3, dropout=None, rate=1152 / 3, defector=0.0, ref="wallet"),
}


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_block_book_replays_exactly(name):
    sc = SCENARIOS[name]
    n = 640
    ps = regtest().replace(startHeight=START, supplyCapBps=1500, sigmaRefBps=sc["sigma"])
    inp = inputs_for(ps, n, sc["seed"], dropout=sc["dropout"])
    ag = AG.AgentsConfig(
        minter=AG.MinterConfig(
            mints_per_day=sc["rate"], class_weights=(0.4, 0.4, 0.2), ref_choice=sc["ref"], buffer_bps_hi=2_000
        ),
        owner=AG.OwnerConfig(
            absence_rate_per_year=40.0,
            absence_median_days=0.05,
            defector_share=sc["defector"],
            lost_key_prob=0.1,
        ),
        claimant=AG.ClaimantConfig(min_profit_bps=100, slippage_bps=50),
    )
    hook = VB.VaultHook(agents=ag, seed=sc["seed"], options=VB.BookOptions(record_events=True))
    series = E.simulate_blocks(ps, inp, hooks=(hook,), rng=np.random.default_rng(sc["seed"]))
    res = series.extras["vaults"]
    events = res.meta["events"][0]
    kinds = {e["kind"] for e in events}
    m, tot, verdicts = replay(ps, inp, series, res, events)
    for j, (sup, col, unb, mask) in enumerate(tot):
        assert int(series.supply_cents[0, j]) == sup, f"supply at {START + j}"
        assert int(series.collateral_zat[0, j]) == col, f"collateral at {START + j}"
        assert int(res.unbacked_cents[0, j]) == unb, f"unbacked at {START + j}"
        assert int(series.halt_mask[0, j]) == mask, (
            f"haltMask at {START + j}: {series.halt_mask[0, j]} != {mask}"
        )
    for tid, v in verdicts:
        assert m.txlog[tid].verdict == v, (tid, m.txlog[tid].verdict, v)
    # final statuses
    for row in np.nonzero(res.vaults["outcome"] != VB.O_REFUSED)[0]:
        e = next(x for x in events if x["kind"] == "mint" and x["row"] == row)
        ref = m.vaults[(txid("mint", e["vid"]), 0)]
        assert ref.status == res.vaults["status"][row], (row, ref.status, res.vaults["status"][row])
    assert res.counters["plan_mismatch"] == 0
    # the scenarios exercise what they are for
    assert "mint" in kinds and "owner" in kinds
    if name == "caps-claims":
        assert "claim" in kinds and res.counters["reasons"].get(VB.MINT_SUPPLY_CAP, 0) > 0
    if name == "enforcement-off":
        assert {"sweep", "thief"} & kinds and int(res.unbacked_cents[0, -1]) > 0
    # claims never confirm at or before claimHeight; owner spends never at or before lockHeight
    for e in events:
        if e["kind"] in ("claim", "thief"):
            r = res.vaults
            assert e["height"] > r["claim_height"][e["row"]]
        if e["kind"] in ("owner", "sweep", "void-release"):
            assert e["height"] > res.vaults["lock_height"][e["row"]]


def test_void_from_same_block_cap_race():
    """Two class-B mints preflighted against the same tip both fit the cap alone; the second one in
    the block meets the live supply and is VOID (MINT-6 reads the running Totals)."""
    n = 300
    ps = regtest().replace(startHeight=START, supplyCapBps=1500, sigmaRefBps=0)
    tp = np.full(n, 2_000_000, dtype=np.int64)
    inp = E.BlockInputs.perfect(tp, START)
    # cap at step 250: issued ≈ 12.5 YEC × 246 blocks... find the cap and size two mints to straddle it
    probe = E.simulate_blocks(ps, inp)
    cap = int(probe.supply_cap_cents[0, 249])
    cents = cap // 2 + 1
    att = AG.MintAttempts.from_rows([(250, cents, 1, 100, 0, 0, 0), (250, cents, 1, 100, 0, 0, 0)])
    hook = VB.VaultHook(
        attempts=[att],
        options=VB.BookOptions(record_events=True),
        agents=AG.AgentsConfig(minter=AG.MinterConfig(ref_choice="wallet")),
    )
    series = E.simulate_blocks(ps, inp, hooks=(hook,))
    res = series.extras["vaults"]
    assert list(res.vaults["outcome"]) == [VB.O_ACTIVE, VB.O_VOID]
    assert res.vaults["reason"][1] == VB.MINT_SUPPLY_CAP
    m, _tot, verdicts = replay(ps, inp, series, res, res.meta["events"][0])
    assert [v for _t, v in verdicts][:2] == [VB.OK, VB.MINT_SUPPLY_CAP]
    assert all(m.txlog[t].verdict == v for t, v in verdicts)

"""Wallet actions (devnet/actions.py) and the vault / attestation replays (M6, D-RD-DEV-3/4)."""

from __future__ import annotations

from typing import Any

import pytest

from ybcal.devnet.actions import WalletDriver
from ybcal.devnet.attestreplay import attest_inputs
from ybcal.devnet.rpc import RpcError
from ybcal.devnet.scenarios import Schedule, make_schedule, schedule_prices
from ybcal.devnet.vaultreplay import NodeVaultRun, VaultReplay
from ybcal.params.paramset import regtest
from ybcal.sim import vaults as VB
from ybcal.sim.engine import simulate_devnet


class Client:
    """A scripted RPC client: ``handlers[method](*params)``; every call is logged."""

    def __init__(self, **handlers: Any) -> None:
        self.handlers = handlers
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def call(self, method: str, *params: Any) -> Any:
        self.calls.append((method, params))
        h = self.handlers.get(method)
        if h is None:
            raise RpcError(-32601, f"no {method}")
        return h(*params)


def test_driver_mint_completion_matches_the_carrier_outpoint():
    mempool: list[str] = []
    txs = {
        # spends carrier C1's change (vout 1) first: must NOT be taken for C1's completion
        "M2": {"vin": [{"txid": "C1", "vout": 1}, {"txid": "C2", "vout": 0}]},
        "M1": {"vin": [{"txid": "X", "vout": 0}, {"txid": "C1", "vout": 0}]},
    }
    confirmed: set[str] = set()
    node0 = Client(
        yed_getbalance=lambda: {"height": 10},
        yed_mint=lambda cents, lock, *_: {"pending": True, "carrierTxid": "C1" if lock == 50 else "C2"},
        gettxout=lambda txid, n, mem: {"confirmations": 1} if txid in confirmed else None,
        getrawmempool=lambda: list(mempool),
        getrawtransaction=lambda txid, verbose=0: txs[txid],
        yed_listvaults=lambda *a: [],
    )
    d = WalletDriver([node0], timeout=0.2)
    d.run({"op": "mint", "node": 0, "cents": 20000, "lock": 50}, 10)
    d.run({"op": "mint", "node": 0, "cents": 20000, "lock": 100}, 10)
    assert [p.carrier for p in d.pending] == ["C1", "C2"] and d.tracking
    d.after_block(11)  # carriers not confirmed yet
    assert len(d.pending) == 2
    confirmed |= {"C1", "C2"}
    mempool += ["M2", "M1"]
    d.after_block(12)
    assert d.vaults == {"v0": "M1", "v1": "M2"} and not d.pending


def test_driver_refusal_is_an_outcome_and_claim_all_reads_vault_outpoints():
    def refuse(*a: Any) -> Any:
        raise RpcError(-26, "mintpol-divergence")

    claimed: list[str] = []
    node1 = Client(
        yed_getbalance=lambda: {"height": 5},
        yed_mint=refuse,
        yed_listclaimable=lambda *a: [{"vault": "ab" * 32 + ":0", "claimHeight": 3}],
        yed_claim=lambda txid, *a: claimed.append(txid)
        or {"pending": True, "carrierTxid": "K", "refHeight": 5},
    )
    d = WalletDriver([node1, node1], timeout=0.1)
    d.run({"op": "mint", "node": 1, "cents": 1, "lock": 1}, 5)
    assert "mintpol-divergence" in d.events[-1]["error"]
    d.run({"op": "claim_all", "node": 1}, 5)
    assert claimed == ["ab" * 32] and d.pending[-1].vault == "ab" * 32
    with pytest.raises(ValueError, match="unknown action"):
        d.run({"op": "nope"}, 5)


def test_driver_pushes_missing_mempool_transactions_to_the_miner():
    sent: list[str] = []
    miner = Client(getrawmempool=lambda: ["A"], sendrawtransaction=lambda hx: sent.append(hx))
    other = Client(getrawmempool=lambda: ["A", "B"], getrawtransaction=lambda txid: f"hex-{txid}")
    WalletDriver([miner, other]).sync_mempool(miner)
    assert sent == ["hex-B"]


# --- the vault replay ---------------------------------------------------------------------------------


def _calm_series_inputs():
    ps = regtest()
    sched = make_schedule("calm", ps, seed=4)
    return ps, sched


def test_vault_replay_mint_and_redeem_against_the_simulator():
    ps, sched = _calm_series_inputs()
    probe: dict[str, Any] = {}

    def grab(stage, prm, inp, s):
        probe["s"] = s

    simulate_devnet(ps, schedule_prices(sched), sched, hooks=(grab,))
    s = probe["s"]
    R, H = 300, 303
    j = R - s.start_height
    rp = VB.RuleParams.of(ps)
    coll = VB.wallet_collateral_int(rp, 20_000, 0, int(s.sigma_mult_bps[0, j]), int(s.x_mint[0, j]))
    assert coll > 0
    vault = {
        "vault": "aa" * 32 + ":0", "txid": "aa" * 32, "vout": 0, "termClass": "A", "mintedCents": 20_000,
        "mintHeight": H, "refHeight": R, "lockHeight": R + 50, "claimHeight": R + 50 + int(ps["grace"]),
        "collateralZat": coll, "closeHeight": 360, "burnedCents": 20_000,
    }
    events = [
        {"height": R, "op": "mint", "label": "v0", "result": {"pending": True, "carrierTxid": "C"}},
        {"height": 302, "op": "mint-complete", "label": "v0", "txid": "aa" * 32, "carrier": "C"},
        {"height": 359, "op": "redeem", "vault": "v0", "result": {"txid": "dd" * 32}},
    ]
    hook = VaultReplay(NodeVaultRun([vault], events, []))
    recs = simulate_devnet(ps, schedule_prices(sched), sched, hooks=(hook,))
    res = hook.result
    assert res is not None and res.collateral == [{"vault": vault["vault"], "node": coll, "sim": coll}]
    (row,) = res.vaults
    assert row["status"] == "CLOSED" and row["closeHeight"] == 360 and row["feePaidZat"] == rp.fee(coll)
    by_h = {r["height"]: r for r in recs}
    assert by_h[H - 1]["supplyCents"] == 0 and by_h[H]["supplyCents"] == 20_000
    assert by_h[H]["collateralZat"] == coll and by_h[360]["supplyCents"] == 0
    assert by_h[H]["globalRatioBps"] is not None
    claim = [r for r in res.claimable if r["vault"] == vault["vault"]]
    assert claim[0]["height"] == H and not any(r["claimable"] for r in claim)


def test_attest_inputs_from_a_run():
    actions = {
        "seats": [{"index": 0, "node": 0, "pubkey": "p", "register_txid": "r", "seq": 0}],
        "signatures": [{"seq": 0, "cited": 12, "price": 7}, {"seq": 0, "cited": 8, "price": 5}],
        "events": [],
    }
    attestors = [{"seq": 0, "bondZat": 10**9, "registerHeight": 4}]
    vaults = [{"vault": "aa" * 32 + ":0", "txid": "aa" * 32, "mintHeight": 20, "refHeight": 17}]
    history = [{"height": 1, "blockHash": "00" * 32}]
    a = attest_inputs(actions, attestors, vaults, history)
    assert a is not None
    assert a["roster"][0]["sign_heights"] == [8, 12] and a["roster"][0]["sign_prices"] == [5, 7]
    assert a["roster"][0]["revive"] is False
    assert a["demands"] == [{"height": 20, "ref_height": 17, "kind": "mint"}]
    assert a["block_hashes"] == {1: "00" * 32}
    assert attest_inputs({"seats": []}, [], [], []) is None


def test_schedule_actions_round_trip():
    sched = make_schedule("vault-cycle", regtest(), seed=2)
    acts = [a for st in sched.steps for a in st.actions]
    assert [a["op"] for a in acts] == ["mint", "mint", "mint", "send", "redeem", "mint", "claim_all"]
    assert Schedule.from_dict(sched.to_dict()) == sched
    out = make_schedule("attestor-outage-1", regtest(), seed=2)
    regs = [a for st in out.steps for a in st.actions if a["op"] == "register"]
    assert len(regs) == 3 and any(st.attestors_down for st in out.steps)


def test_engine_attest_frame_starts_at_the_first_column():
    """D-RD-DEV-5: without ``height0`` in the attest dict the layer walked one block behind the
    engine's columns, so a bundle in the last block found no snapshot."""
    from ybcal.sim import engine as E

    ps, sched = _calm_series_inputs()
    probe: dict[str, Any] = {}
    simulate_devnet(ps, schedule_prices(sched), sched, hooks=(lambda st, p, i, s: probe.update(s=s, i=i),))
    s = probe["s"]
    assert s.height0 == s.start_height == 1
    assert E._attest_frame(probe["i"], s) == (1, 1)

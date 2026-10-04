"""Wallet actions during a devnet replay: mints, YED transfers, redemptions, claims (M6, D-RD-DEV-3).

Owner: WP-9 (devnet validation).

A :class:`~ybcal.devnet.scenarios.ReplayStep` may carry ``actions`` — small dicts run on a node's
wallet right before the step's first block:

``{"op": "mint", "node": 0, "cents": 20000, "lock": 50}``
    ``yed_mint cents lock "" "" false``: the carrier now, the MINT after the carrier confirms
    (the wallet completes it on the next block). Mints are numbered in schedule order (``v0``,
    ``v1``, …) so later actions can name them.
``{"op": "send", "node": 0, "to": 1, "cents": 5000}``
    ``yed_send`` to a fresh YED address of node ``to``.
``{"op": "redeem", "node": 0, "vault": "v0"}``
    ``yed_redeem`` (owner path; one transaction).
``{"op": "claim", "node": 1, "vault": "v1"}`` / ``{"op": "claim_all", "node": 1}``
    ``yed_claim … false`` for one vault / for every vault ``yed_listclaimable`` reports.

The driver makes transaction timing deterministic, which is what lets a differential compare
heights exactly: before every block it pushes each transaction any node holds into the mining
node's mempool (``sendrawtransaction``; the p2p trickle is seconds and once dropped a carrier from
the next block), and after every block it waits for each wallet's pending two-step completion
(the transaction spending the confirmed carrier) to reach its mempool. An action the wallet refuses
(a halt, ``insufficient-yed``, …) is recorded with its error; it is a scenario outcome, not a
harness failure. After every block the driver records each vault's ``status`` and ``claimable``
(``yed_listvaults`` on node 0) for the claim-timing comparison.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ybcal.devnet.rpc import RpcError

OWNER_WP = "WP-9"

#: Action ops the driver knows.
OPS = ("mint", "send", "redeem", "claim", "claim_all", "register", "freeze")


@dataclass
class Seat:
    """An attestor seat: a node's wallet registered with ``yed_registerattestor`` and an emulated
    agent (``yed_signattestation`` every ``attestInterval`` cited heights, ``yed_addattestation`` on
    every node) — the job ``yellowback-attest attest`` does in the launcher devnet."""

    index: int  #: seat number (registration order in the schedule; ``attestors_down`` uses it)
    node: int
    pubkey: str
    register_txid: str
    seq: int | None = None
    down: bool = False
    frozen_price: int | None = None  #: a stuck feed: re-sign this price (PIN-2's target)


@dataclass
class Pending:
    """A two-step wallet call waiting for its carrier to confirm."""

    node: int
    op: str
    carrier: str
    label: str = ""  #: ``v<n>`` for a mint
    vault: str = ""  #: the vault txid for a claim


@dataclass
class WalletDriver:
    """Runs a schedule's wallet actions on a devnet and keeps the mempools in step."""

    clients: Sequence[Any]  #: every node's RPC client, node 0 first
    timeout: float = 30.0
    events: list[dict[str, Any]] = field(default_factory=list)
    vault_blocks: list[dict[str, Any]] = field(default_factory=list)
    vaults: dict[str, str] = field(default_factory=dict)  #: v<n> → vault txid
    pending: list[Pending] = field(default_factory=list)
    n_mints: int = 0
    tracking: bool = False
    seats: list[Seat] = field(default_factory=list)
    signatures: list[dict[str, Any]] = field(default_factory=list)  #: {seat, seq, cited, price, height}
    attest_interval: int = 4
    ref_lag: int = 2
    price: int = 0  #: the step's reference price (what every live agent attests)
    attestor_blocks: list[dict[str, Any]] = field(default_factory=list)
    attest_blocks: list[dict[str, Any]] = field(default_factory=list)  #: {height, status} (layer status)

    # -- actions ---------------------------------------------------------------------------------
    def run(self, action: Mapping[str, Any], height: int) -> None:
        """Execute one action at the current tip ``height``; the outcome is appended to ``events``."""
        op = str(action.get("op"))
        node = int(action.get("node", 0))
        c = self.clients[node]
        ev: dict[str, Any] = {"height": height, **dict(action)}
        self.wallet_synced(c, height)
        if "to" in action:
            self.wallet_synced(self.clients[int(action["to"])], height)
        try:
            if op == "mint":
                label = f"v{self.n_mints}"
                self.n_mints += 1
                ev["label"] = label
                res = c.call("yed_mint", int(action["cents"]), int(action["lock"]), "", "", False)
                ev["result"] = res
                self._pend(node, op, res, label=label)
                self.tracking = True
            elif op == "send":
                to = self.clients[int(action["to"])].call("yed_getnewaddress")
                ev["result"] = c.call("yed_send", to, int(action["cents"]))
            elif op == "redeem":
                ev["result"] = c.call("yed_redeem", self._vault(action["vault"]))
            elif op == "claim":
                txid = self._vault(action["vault"])
                res = c.call("yed_claim", txid, "", "", False)
                ev["result"] = res
                self._pend(node, op, res, vault=txid)
            elif op == "claim_all":
                rows = c.call("yed_listclaimable", 100, 0)
                # rows name the vault as "<txid>:<vout>" (the vault is always vout 0 of its MINT)
                ev["claimable"] = [str(r.get("vault", "")) for r in rows]
                ev["results"] = []
                for vault in ev["claimable"]:
                    txid = vault.split(":")[0]
                    try:
                        res = c.call("yed_claim", txid, "", "", False)
                        ev["results"].append(res)
                        self._pend(node, "claim", res, vault=txid)
                    except RpcError as e:
                        ev["results"].append({"error": str(e)})
            elif op == "register":
                bond, lock = int(action.get("bond", 10)), int(action.get("lock", 200))
                res = c.call("yed_registerattestor", bond, lock, 0)
                ev["result"] = res
                self.seats.append(Seat(len(self.seats), node, str(res["attestorPubKey"]), str(res["txid"])))
            elif op == "freeze":
                seat = self.seats[int(action["seat"])]
                seat.frozen_price = int(action["price"]) if action.get("price") else (self.price or None)
                ev["price"] = seat.frozen_price
            else:
                raise ValueError(f"unknown action op {op!r} (known: {', '.join(OPS)})")
        except RpcError as e:
            ev["error"] = str(e)
        self.events.append(ev)

    def _vault(self, ref: str) -> str:
        if ref in self.vaults:
            return self.vaults[ref]
        if len(ref) == 64:
            return ref
        raise ValueError(f"vault {ref!r} has not been minted (known: {sorted(self.vaults)})")

    def _pend(self, node: int, op: str, res: Mapping[str, Any], *, label: str = "", vault: str = "") -> None:
        if res.get("pending") and res.get("carrierTxid"):
            self.pending.append(Pending(node, op, str(res["carrierTxid"]), label, vault))
        elif op == "mint" and res.get("txid"):
            self.vaults[label] = str(res["txid"])

    # -- mempools --------------------------------------------------------------------------------
    def sync_mempool(self, miner: Any) -> None:
        """Give ``miner`` every transaction another node holds (best effort per transaction)."""
        have = set(miner.call("getrawmempool"))
        for c in self.clients:
            if c is miner:
                continue
            for txid in c.call("getrawmempool"):
                if txid in have:
                    continue
                with contextlib.suppress(RpcError):
                    miner.call("sendrawtransaction", c.call("getrawtransaction", txid))
                have.add(txid)

    def after_block(self, height: int) -> None:
        """Wait for every pending completion whose carrier is now confirmed; record vault rows."""
        still: list[Pending] = []
        for p in self.pending:
            c = self.clients[p.node]
            try:  # the chain's view (the wallet's own lags): the carrier output in the UTXO set
                out = c.call("gettxout", p.carrier, 0, False)
                conf = int((out or {}).get("confirmations", 0) or 0)
            except RpcError:
                conf = 0
            if conf <= 0:
                still.append(p)
                continue
            txid = self._wait_spender(c, p.carrier)
            done = {"height": height, "op": f"{p.op}-complete", "carrier": p.carrier, "txid": txid}
            self.events.append(done | {"label": p.label, "vault": p.vault})
            if p.op == "mint" and txid:
                self.vaults[p.label] = txid
        self.pending = still
        if self.seats:
            self._agents(height)
        if self.tracking:
            for row in self.clients[0].call("yed_listvaults", "", 1000, 0):
                self.vault_blocks.append(
                    {
                        "height": height,
                        "vault": f"{row['txid']}:{row.get('vout', 0)}",
                        "status": row.get("status"),
                        "claimable": bool(row.get("claimable")),
                    }
                )

    def wallet_synced(self, c: Any, height: int) -> None:
        """Wait until node ``c``'s Yellowback index (what its wallet's YED balance reads; reported as
        ``yed_getbalance.height``) has processed ``height``: only node 0's index is awaited after
        each block, and an action on another node must not see a stale balance."""
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                if int(c.call("yed_getbalance").get("height", -1)) >= height:
                    return
            except RpcError:
                return  # a node without the Yellowback wallet: nothing to wait for
            if time.monotonic() > deadline:
                return
            time.sleep(0.05)

    def set_down(self, index: int, down: bool) -> None:
        """``attestors_down``: stop / restart seat ``index``'s agent."""
        if not 0 <= index < len(self.seats):
            raise ValueError(f"no attestor seat {index}: this devnet has {len(self.seats)} attestor seats")
        self.seats[index].down = down

    def _agents(self, height: int) -> None:
        """Resolve seats' seqs; each live agent signs cited height ``height - ref_lag`` when it is on
        its cadence (``cited ≡ seq (mod attestInterval)``) and hands the signature to every node."""
        rows = None
        for seat in self.seats:
            if seat.seq is None:
                if rows is None:
                    rows = self.clients[0].call("yed_listattestors")
                for r in rows:
                    if str(r.get("attestorPubKey", "")) == seat.pubkey:
                        seat.seq = int(r["seq"])
        if rows is None:
            rows = self.clients[0].call("yed_listattestors")
        att = self.clients[0].call("yed_getinfo").get("attest") or {}
        self.attest_blocks.append({"height": height, "status": att.get("status")})
        for r in rows:
            row = {"height": height, "seq": int(r["seq"]), "status": r.get("status")}
            self.attestor_blocks.append(row | {"pinned": bool(r.get("pinned"))})
        cited = height - self.ref_lag
        for seat in self.seats:
            k = self.attest_interval
            if seat.seq is None or seat.down or cited % k != seat.seq % k:
                continue
            price = seat.frozen_price or self.price
            if not price:
                continue
            try:
                sig = self.clients[seat.node].call("yed_signattestation", seat.seq, price, cited)
            except RpcError as e:
                self.events.append({"height": height, "op": "sign", "seat": seat.index, "error": str(e)})
                continue
            self.signatures.append(
                {"seat": seat.index, "seq": seat.seq, "cited": cited, "price": price, "height": height}
            )
            for c in self.clients:
                with contextlib.suppress(RpcError):
                    c.call("yed_addattestation", sig["hex"])

    def _wait_spender(self, c: Any, carrier: str) -> str:
        """The mempool transaction (of node ``c``) spending ``carrier``, waiting up to ``timeout``."""
        deadline = time.monotonic() + self.timeout
        while True:
            for txid in c.call("getrawmempool"):
                with contextlib.suppress(RpcError):
                    vin = c.call("getrawtransaction", txid, 1).get("vin", [])
                    # the completion spends the carrier's output 0 as its *last* input (W7); another
                    # completion may spend the carrier's change, so match the outpoint exactly
                    if vin and vin[-1].get("txid") == carrier and int(vin[-1].get("vout", -1)) == 0:
                        return str(txid)
            if time.monotonic() > deadline:
                return ""
            time.sleep(0.1)

    def to_dict(self) -> dict[str, Any]:
        """``actions.json``."""
        return {
            "events": self.events,
            "vaults": self.vaults,
            "vault_blocks": self.vault_blocks,
            "seats": [seat.__dict__ for seat in self.seats],
            "signatures": self.signatures,
            "attestor_blocks": self.attestor_blocks,
            "attest_blocks": self.attest_blocks,
            "attest_interval": self.attest_interval,
        }

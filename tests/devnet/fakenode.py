"""A fake ``ycashd`` JSON-RPC server for the devnet tests (http.server in a thread).

One server plays every node of a devnet: the node index is the URL path (``/node0/``,
``/node1/`` …). The chain is shared (one height), each node remembers its quote, and every call is
logged as ``(node, method, params)``. ``yed_getinfo.params`` is rendered from a ParamSet with the
documented field names, so the version / parameter checks run against it.
"""

from __future__ import annotations

import base64
import json
import threading
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from ybcal.devnet.build import GETACTIVATION_PARAMS, GETINFO_PARAMS
from ybcal.devnet.overlay import CARRIER_FLAG_VALUES
from ybcal.devnet.rpc import RpcClient
from ybcal.params.paramset import ParamSet

USER, PASSWORD = "u", "p"


def getinfo_params(ps: Mapping[str, Any]) -> dict[str, Any]:
    """``yed_getinfo().params`` as a node at ``ps`` reports it (doc/yellowback-rpc.md)."""
    out: dict[str, Any] = {}
    for path, name in GETINFO_PARAMS.items():
        v = ps[name]
        if name == "bundleCarrier":
            v = CARRIER_FLAG_VALUES[str(v)]
        cur = out
        parts = path.split(".")
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = v
    out["refLag"] = ps["DEFAULT_REF_LAG"]
    out["feeZat"] = 1000
    out["classes"] = [
        {
            "class": "ABC"[i],
            "minBlocks": ps[f"classMin[{i}]"],
            "maxBlocks": ps[f"classMax[{i}]"],
            "baseRatioBps": ps[f"baseRatioBps[{i}]"],
        }
        for i in range(3)
    ]
    return out


def history_row(h: int) -> dict[str, Any]:
    """A plausible ``yed_gethistory`` row for height ``h``."""
    active = h >= 130
    price = 50_000_000 + (h % 7) * 1000 if h > 8 else None
    return {
        "height": h,
        "blockHash": f"{h:064x}",
        "tagged": True,
        "quote": h > 101,
        "signalCount": min(h, 64),
        "activation": {
            "status": "active" if active else "signaling",
            "lockInHeight": 64 if active else 0,
            "activateHeight": 129 if active else 0,
        },
        "pFast": price,
        "pMid": price if h > 24 else None,
        "pSlow": price if h > 64 else None,
        "pMint": price if h > 64 else None,
        "pClaim": price if h > 64 else None,
        "sigmaMultBps": 10000,
        "issuedZat": h * 625_000_000,
        "supplyCents": 0,
        "collateralZat": 0,
        "globalRatioBps": None,
        "haltMask": [] if active and h > 64 else ["NOT_ACTIVE", "NO_PRICE"],
    }


class FakeChain:
    """Shared state of the fake devnet."""

    def __init__(
        self, params: ParamSet, n_vaults: int = 0, height: int = 0, warmup: int = 0, attestors: bool = False
    ) -> None:
        self.params = params
        self.height = height
        self.calls: list[tuple[int, str, list[Any]]] = []
        self.quotes: dict[int, int] = {}
        self.miners: list[int] = []
        self.n_vaults = n_vaults
        self.warmup = warmup  # answer -28 this many times first
        self.attestors = attestors
        self.lock = threading.Lock()

    def handle(self, node: int, method: str, params: list[Any]) -> Any:
        with self.lock:
            self.calls.append((node, method, list(params)))
            if self.warmup > 0:
                self.warmup -= 1
                raise _RpcFail(-28, "Loading block index...")
            return self._dispatch(node, method, params)

    def _dispatch(self, node: int, method: str, params: list[Any]) -> Any:
        if method == "getblockcount":
            return self.height
        if method == "generate":
            hashes = []
            for _ in range(int(params[0])):
                self.height += 1
                self.miners.append(node)
                hashes.append(f"{self.height:064x}")
            return hashes
        if method == "yed_setquote":
            price = int(params[0])
            if price:
                self.quotes[node] = price
            else:
                self.quotes.pop(node, None)
            return {
                "priceMicroUsd": price,
                "sourceMask": params[1],
                "receivedAt": 0,
                "nextTag": {"kind": "quote" if price else "signal", "signal": True, "payoutAddress": "sm"},
            }
        if method == "yed_getinfo":
            return {
                "rpcversion": 3,
                "network": "regtest",
                "height": self.height,
                "startHeight": 1,
                "healthy": True,
                "params": getinfo_params(self.params),
            }
        if method == "yed_getactivation":
            act = {k: self.params[v] for k, v in GETACTIVATION_PARAMS.items()}
            return {
                "status": "active",
                "lockInHeight": 64,
                "activateHeight": 129,
                "signalCount": 64,
                "enforceUntilHeight": self.params["enforceUntilHeight"],
                "history": [],
                **act,
            }
        if method == "yed_gethistory":
            lo, hi = int(params[0]), int(params[1])
            if hi - lo + 1 > 2016 or lo < 1 or hi > self.height:
                raise _RpcFail(-8, "yed_gethistory range")
            return [history_row(h) for h in range(lo, hi + 1)]
        if method == "yed_getstats":
            return {
                "height": self.height,
                "supplyCents": 0,
                "collateralZat": 0,
                "activeVaults": self.n_vaults,
                "haltMask": [],
                "mintingAllowed": True,
                "mintableClasses": ["A", "B", "C"],
            }
        if method == "yed_listvaults":
            _status, count, skip = params[0], int(params[1]), int(params[2])
            rows = [vault_row(i) for i in range(self.n_vaults)]
            return rows[skip : skip + count]
        if method == "yed_listattestors":
            if not self.attestors:
                raise _RpcFail(-32601, "Method not found")
            return [
                {
                    "seq": 0,
                    "status": "ELIGIBLE",
                    "statusHeight": 8,
                    "registerHeight": 1,
                    "bondZat": 10**9,
                    "bondLocktime": 200,
                    "weight": "31000000000",
                    "seated": True,
                    "pinned": False,
                    "founding": True,
                    "seatedSince": 9,
                    "lastBundleHeight": None,
                    "bondSpentHeight": None,
                    "poolFresh": True,
                    "flags": {"tier": 0, "pool": False},
                    "attestorPubKey": "02" + "ab" * 32,
                }
            ]
        if method in ("importprivkey", "stop", "addnode"):
            return None
        raise _RpcFail(-32601, f"Method not found: {method}")


def vault_row(i: int) -> dict[str, Any]:
    """A ``yed_listvaults`` row."""
    return {
        "txid": f"{i:064x}",
        "vout": 0,
        "status": "ACTIVE",
        "ownerPubKey": "02",
        "ownerKeyId": "00",
        "ownerAddress": "yr",
        "termClass": "A",
        "lockHeight": 380 + i,
        "claimHeight": 404 + i,
        "collateralZat": 25_125_628_141,
        "collateral": 251.25628141,
        "mintedCents": 100_000,
        "mintHeight": 332,
        "refHeight": 329,
        "feePaidZat": 62_814_071,
        "closeHeight": None,
        "closingTxid": "",
        "burnedCents": 0,
        "unbacked": False,
        "claimable": False,
        "underwaterAt": 437_800,
        "voidReason": "",
        "sweepBefore": 404,
        "noticed": False,
        "noticeHeight": None,
        "emergencyOpenAt": None,
    }


class _RpcFail(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


class FakeServer:
    """``with FakeServer(chain) as srv: srv.client(0).getblockcount()``."""

    def __init__(self, chain: FakeChain) -> None:
        self.chain = chain
        handler = self._handler()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        chain = self.chain
        expected = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:
                pass

            def do_POST(self) -> None:
                if self.headers.get("Authorization") != expected:
                    self.send_response(401)
                    self.end_headers()
                    return
                node = int(self.path.strip("/").removeprefix("node") or 0)
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                try:
                    result, error, status = (
                        chain.handle(node, req["method"], req.get("params", [])),
                        None,
                        200,
                    )
                except _RpcFail as e:
                    result, error, status = None, {"code": e.code, "message": e.message}, 500
                body = json.dumps({"result": result, "error": error, "id": req.get("id")}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return H

    @property
    def port(self) -> int:
        return int(self.httpd.server_address[1])

    def url(self, node: int = 0) -> str:
        return f"http://127.0.0.1:{self.port}/node{node}/"

    def client(self, node: int = 0) -> RpcClient:
        return RpcClient(self.url(node), USER, PASSWORD, timeout=10)

    def __enter__(self) -> FakeServer:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class FakeDevnet:
    """A :class:`ybcal.devnet.runner.Devnet` over a :class:`FakeServer`."""

    def __init__(self, srv: FakeServer, n_pools: int = 3, dark: bool = False) -> None:
        self.pools = [srv.client(i) for i in range(n_pools)]
        self.dark = srv.client(n_pools) if dark else None
        self.primary = self.pools[0]
        self.synced: list[int] = []

    def wait_synced(self, height: int, timeout: float = 120.0) -> None:
        assert int(self.primary.call("getblockcount")) >= height
        self.synced.append(height)

"""Minimal JSON-RPC 1.0 client for ``ycashd`` (standard library only).

Owner: WP-9.

Auth is user/password (``rpcuser``/``rpcpassword``) or the datadir cookie
(``<datadir>/regtest/.cookie``). A node answers ``-28`` (warming up) for ~12 s after launch on
6.20.0 (Orchard parameters); :meth:`RpcClient.wait_ready` retries through it.
"""

from __future__ import annotations

import base64
import itertools
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

OWNER_WP = "WP-9"

#: ``RPC_IN_WARMUP``.
RPC_IN_WARMUP = -28


class RpcError(RuntimeError):
    """A JSON-RPC error answer (``code``, ``message``) or a transport failure (``code`` None)."""

    def __init__(self, code: int | None, message: str, method: str = "") -> None:
        super().__init__(f"{method}: {message} (code {code})" if method else f"{message} (code {code})")
        self.code = code
        self.message = message
        self.method = method


def read_cookie(datadir: str | Path, network: str = "regtest") -> tuple[str, str]:
    """``(user, password)`` from the node's ``.cookie`` file."""
    base = Path(datadir)
    for cand in (base / network / ".cookie", base / ".cookie"):
        if cand.exists():
            user, _, password = cand.read_text().strip().partition(":")
            return user, password
    raise FileNotFoundError(f"no .cookie under {datadir}")


class RpcClient:
    """``client.call("yed_getinfo")`` or ``client.yed_getinfo()``."""

    def __init__(
        self,
        url: str,
        user: str = "",
        password: str = "",
        *,
        timeout: float = 60.0,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.url = url
        self.timeout = timeout
        self._auth = ("Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()) if user else None
        self._ids = itertools.count(1)
        self._opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({})).open

    @classmethod
    def from_datadir(
        cls, datadir: str | Path, port: int, *, host: str = "127.0.0.1", timeout: float = 60.0
    ) -> RpcClient:
        """A client using the datadir cookie."""
        user, password = read_cookie(datadir)
        return cls(f"http://{host}:{port}/", user, password, timeout=timeout)

    def call(self, method: str, *params: Any) -> Any:
        """One JSON-RPC call; raises :class:`RpcError` on an error answer or a transport failure."""
        body = json.dumps(
            {"jsonrpc": "1.0", "id": next(self._ids), "method": method, "params": list(params)}
        ).encode()
        req = urllib.request.Request(
            self.url, data=body, method="POST", headers={"Content-Type": "application/json"}
        )
        if self._auth:
            req.add_header("Authorization", self._auth)
        try:
            with self._opener(req, timeout=self.timeout) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as e:
            # ycashd answers RPC errors with HTTP 500 and a JSON body
            payload = e.read()
            if not payload:
                raise RpcError(None, f"HTTP {e.code} {e.reason}", method) from e
        except (TimeoutError, urllib.error.URLError, ConnectionError, OSError) as e:
            raise RpcError(None, f"transport: {e}", method) from e
        try:
            doc = json.loads(payload)
        except ValueError as e:
            raise RpcError(None, f"non-JSON answer: {payload[:200]!r}", method) from e
        err = doc.get("error")
        if err:
            raise RpcError(err.get("code"), err.get("message", ""), method)
        return doc.get("result")

    def __getattr__(self, name: str) -> Callable[..., Any]:
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *params: self.call(name, *params)

    def wait_ready(self, timeout: float = 120.0, poll: float = 0.5) -> int:
        """Poll ``getblockcount`` through warm-up (``-28``) and refused connections; the height."""
        deadline = time.monotonic() + timeout
        last: RpcError | None = None
        while time.monotonic() < deadline:
            try:
                return int(self.call("getblockcount"))
            except RpcError as e:
                if e.code not in (None, RPC_IN_WARMUP):
                    raise
                last = e
            time.sleep(poll)
        raise RpcError(None, f"node not ready after {timeout:.0f} s: {last}", "getblockcount")

"""Run a regtest devnet, replay a schedule on it, scrape it, take it down (PLAN §6.2 item 4).

Owner: WP-9.

Two backends:

* :class:`MinimalDevnet` (default) — ycash6's ``contrib/yellowback/devnet/yellowback-devnet up``
  hard-codes ``-yellowbackstartheight=1 -yellowbacksigmaref=0`` and passes no other runtime flag
  (``yellowback_node_args`` in ``qa/rpc-tests/test_framework/yellowback_util.py``), so the six
  runtime parameters could not be varied through it. This backend starts ``n_pools`` pool nodes
  (plus an optional non-signalling *dark* miner) from the single-node configuration of
  ``doc/yellowback-devnet.md`` §2 — ``regtest=1``, ``experimentalfeatures=1``, ``yellowback=1``,
  the six ``nuparams`` at height 1, the six yellowback flags — with the launcher's fixed pool keys
  (``-yellowbackpayoutaddress``, ``-yellowbacksignal=1``) and its port-seed scheme.
* :class:`LauncherDevnet` — drives ``yellowback-devnet up --dir … --portseed … --no-viz`` from the
  worktree when the overlay's runtime flags equal the launcher's (it then provides the attestor
  seats and their real ``yellowback-attest`` agents, which ``attestor-outage-1`` needs).

:func:`replay` executes a :class:`~ybcal.devnet.scenarios.Schedule`: before every block it sets
the mining pool's quote with ``yed_setquote`` (jittered by ±``jitter_bps`` and never equal to that
pool's previous quote, so PIN-1 does not pin a constant feed), then ``generate 1`` on that pool and
waits until every node *and* node 0's Yellowback index reach the new height (~2 s a block on
6.20.0). :func:`run_devnet` wires binary resolution, the version checks, start, replay, scrape and
teardown together; every environmental failure returns :class:`~ybcal.devnet.status.Skipped`.
"""

from __future__ import annotations

import contextlib
import json
import os
import random
import secrets
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from ybcal.devnet.actions import WalletDriver
from ybcal.devnet.build import (
    BinaryInfo,
    NodeCheck,
    SkewReport,
    binary_version,
    check_node_params,
    check_skew,
    resolve_binary,
)
from ybcal.devnet.keys import POOL_WIFS, pool_addresses
from ybcal.devnet.overlay import OverlaySplit, build_key
from ybcal.devnet.rpc import RpcClient, RpcError
from ybcal.devnet.scenarios import ReplayStep, Schedule
from ybcal.devnet.scrape import ScrapeResult, scrape
from ybcal.devnet.status import Skipped
from ybcal.devnet.worktree import work_dir
from ybcal.units import PRICE_MAX, PRICE_MIN

OWNER_WP = "WP-9"

#: Inherited framework port scheme (test_framework/util.py) with the devnet's MAX_NODES = 12.
PORT_MIN, PORT_RANGE, MAX_NODES = 11000, 5000, 12
#: Default port seed: away from the launcher's default 7 and the functional-test bands.
DEFAULT_PORTSEED = 101

#: Network upgrades active from height 1 (doc/yellowback-devnet.md §2: regtest activates none itself).
NUPARAMS: tuple[str, ...] = (
    "5ba81b19:1",
    "76b809bb:1",
    "374d694f:1",
    "8e471bd6:1",
    "66314da3:1",
    "19bd2d2f:1",
)

#: The launcher's hard-coded runtime flags (``yellowback_node_args``: start 1, sigmaref 0; the rest default).
LAUNCHER_RUNTIME: dict[str, Any] = {
    "startHeight": 1,
    "sigmaRefBps": 0,
    "supplyCapBps": 0,
    "enforceUntilHeight": 0,
    "attestArmMin": 3,
    "bundleCarrier": "SCRIPTSIG",
}


#: Width of one port slot under an explicit port base: 12 p2p ports, then 12 RPC ports.
PORT_SLOT = 2 * MAX_NODES
#: Ports one explicit base may use (``base … base + PORT_BAND - 1``).
PORT_BAND = 1000
#: Environment variable that sets the port base (e.g. ``41000`` keeps every port in 41000–41999).
PORT_BASE_ENV = "YBCAL_DEVNET_PORT_BASE"


def default_port_base() -> int | None:
    """``$YBCAL_DEVNET_PORT_BASE`` as an integer, else ``None`` (the framework scheme)."""
    v = os.environ.get(PORT_BASE_ENV, "").strip()
    return int(v) if v else None


def _slot(seed: int, base: int) -> int:
    off = PORT_SLOT * seed
    if seed < 0 or off + PORT_SLOT > PORT_BAND:
        raise ValueError(
            f"port seed {seed} does not fit the band {base}–{base + PORT_BAND - 1} "
            f"(seeds 0..{PORT_BAND // PORT_SLOT - 1} under a port base)"
        )
    return base + off


def p2p_port(n: int, seed: int, base: int | None = None) -> int:
    """P2P port of node ``n`` under port seed ``seed``.

    Without ``base``: the framework scheme (devnet seed ``s`` → 11000 + 12·s + n). With ``base``
    (``--port-base`` / ``$YBCAL_DEVNET_PORT_BASE``): ``base + 24·seed + n``, so a whole devnet stays
    inside ``base … base + 999`` (seeds 0–40) — the band a shared machine reserves for it.
    """
    if base is not None:
        return _slot(seed, base) + n
    return PORT_MIN + n + (MAX_NODES * seed) % (PORT_RANGE - 1 - MAX_NODES)


def rpc_port(n: int, seed: int, base: int | None = None) -> int:
    """RPC port of node ``n`` (5000 above its p2p port; 12 above it under a port base)."""
    if base is not None:
        return _slot(seed, base) + MAX_NODES + n
    return PORT_MIN + PORT_RANGE + n + (MAX_NODES * seed) % (PORT_RANGE - 1 - MAX_NODES)


def port_free(port: int, host: str = "127.0.0.1") -> bool:
    """True when nothing listens on ``host:port`` (a bind test; the socket is closed at once)."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        # ycashd binds with SO_REUSEADDR too: a TIME_WAIT left by a finished devnet is not a clash
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def index_reached(info: dict[str, Any], height: int) -> bool:
    """Whether node 0's Yellowback index (``yed_getinfo``) has processed ``height``.

    Below ``startHeight`` the index holds nothing and reports ``height: -1`` (a fresh chain at
    genesis, the funding blocks), so any height under ``startHeight`` counts as reached.
    """
    if int(info.get("height", -1)) >= height:
        return True
    return height < int(info.get("startHeight", 1))


class Devnet(Protocol):
    """What :func:`replay` needs from a running devnet."""

    pools: list[Any]  #: one RPC client per signalling pool, in pool order
    dark: Any | None  #: the non-signalling miner, if any
    primary: Any  #: node 0 (scraped)

    def wait_synced(self, height: int, timeout: float = 120.0) -> None: ...


# ---------------------------------------------------------------------------------------------------
# Minimal launcher


@dataclass
class DevnetConfig:
    """A :class:`MinimalDevnet`'s configuration."""

    ycashd: Path
    run_dir: Path
    runtime_args: list[str]
    portseed: int = DEFAULT_PORTSEED
    port_base: int | None = None  #: explicit port band (see :func:`p2p_port`); None = framework scheme
    n_pools: int = 3
    dark_miner: bool = False
    extra_args: list[str] = field(default_factory=list)
    rpc_user: str = "ybcal"
    rpc_password: str = field(default_factory=lambda: secrets.token_hex(12))
    start_timeout: float = 180.0

    def __post_init__(self) -> None:
        if not 1 <= self.n_pools <= len(POOL_WIFS):
            raise ValueError(f"n_pools must be 1..{len(POOL_WIFS)} (fixed pool keys)")

    @property
    def n_nodes(self) -> int:
        """Pools plus the dark miner."""
        return self.n_pools + int(self.dark_miner)


def node_conf(cfg: DevnetConfig, index: int) -> str:
    """``ycash.conf`` of node ``index`` (pools first, then the dark miner)."""
    lines = [
        "# written by ybcal devnet (doc/yellowback-devnet.md §2 configuration)",
        "regtest=1",
        "server=1",
        "listen=1",
        "printtoconsole=0",
        "showmetrics=0",
        f"rpcuser={cfg.rpc_user}",
        f"rpcpassword={cfg.rpc_password}",
        "rpcallowip=127.0.0.1",
        "experimentalfeatures=1",
        "yellowback=1",
        *(f"nuparams={n}" for n in NUPARAMS),
        *(a.lstrip("-") for a in cfg.runtime_args),
        # top level: the Zcash 4.x/6.x config parser has no [network] sections
        f"port={p2p_port(index, cfg.portseed, cfg.port_base)}",
        f"rpcport={rpc_port(index, cfg.portseed, cfg.port_base)}",
        "bind=127.0.0.1",
        "discover=0",
        "listenonion=0",
    ]
    if index < cfg.n_pools:
        lines += [f"yellowbackpayoutaddress={pool_addresses()[index]}", "yellowbacksignal=1"]
    else:
        lines += ["yellowbacksignal=0"]
    lines += [a.lstrip("-") for a in cfg.extra_args]
    for other in range(cfg.n_nodes):
        if other != index:
            lines.append(f"addnode=127.0.0.1:{p2p_port(other, cfg.portseed, cfg.port_base)}")
    return "\n".join(lines) + "\n"


@dataclass
class _Node:
    index: int
    datadir: Path
    client: RpcClient
    proc: subprocess.Popen[bytes] | None = None


class MinimalDevnet:
    """``n_pools`` pool nodes (+ dark miner) started directly from ``ycash.conf``."""

    def __init__(
        self,
        cfg: DevnetConfig,
        *,
        popen: Callable[..., Any] = subprocess.Popen,
        client_factory: Callable[[str, str, str], Any] | None = None,
    ) -> None:
        self.cfg = cfg
        self._popen = popen
        self._client = client_factory or (lambda url, u, p: RpcClient(url, u, p, timeout=120))
        self.nodes: list[_Node] = []
        for i in range(cfg.n_nodes):
            url = f"http://127.0.0.1:{rpc_port(i, cfg.portseed, cfg.port_base)}/"
            self.nodes.append(
                _Node(i, cfg.run_dir / f"node{i}", self._client(url, cfg.rpc_user, cfg.rpc_password))
            )

    def ports(self) -> list[int]:
        """Every p2p and RPC port this devnet binds."""
        c = self.cfg
        return [f(i, c.portseed, c.port_base) for i in range(c.n_nodes) for f in (p2p_port, rpc_port)]

    @property
    def pools(self) -> list[Any]:
        """Pool clients."""
        return [n.client for n in self.nodes[: self.cfg.n_pools]]

    @property
    def dark(self) -> Any | None:
        """The dark miner's client."""
        return self.nodes[-1].client if self.cfg.dark_miner else None

    @property
    def primary(self) -> Any:
        """Node 0."""
        return self.nodes[0].client

    def write_confs(self) -> None:
        """Create the datadirs and their ``ycash.conf``."""
        for n in self.nodes:
            n.datadir.mkdir(parents=True, exist_ok=True)
            (n.datadir / "ycash.conf").write_text(node_conf(self.cfg, n.index))

    def start(self) -> None:
        """Start every node, wait through RPC warm-up (~12 s on 6.20.0), import the pool keys."""
        if any(n.datadir.exists() and any(n.datadir.iterdir()) for n in self.nodes):
            raise RuntimeError(f"{self.cfg.run_dir} already holds node data; use a fresh run dir")
        if self._popen is subprocess.Popen:
            busy = [p for p in self.ports() if not port_free(p)]
            if busy:
                raise RuntimeError(
                    f"ports {busy} are in use (another devnet or node?): "
                    "pick another --portseed / --port-base"
                )
        self.write_confs()
        for n in self.nodes:
            log = (n.datadir / "ycashd.out").open("ab")
            n.proc = self._popen(
                [str(self.cfg.ycashd), f"-datadir={n.datadir}"],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            log.close()
        for n in self.nodes:
            n.client.wait_ready(self.cfg.start_timeout)
        for i, n in enumerate(self.nodes[: self.cfg.n_pools]):
            with contextlib.suppress(RpcError):  # the payout key only matters for spending pool fees
                n.client.call("importprivkey", POOL_WIFS[i], "yellowback-payout", False)
        self.wait_synced(int(self.primary.call("getblockcount")))

    def wait_synced(self, height: int, timeout: float = 120.0) -> None:
        """Until every node is at ``height`` and node 0's index reports it."""
        deadline = time.monotonic() + timeout
        while True:
            heights = [int(n.client.call("getblockcount")) for n in self.nodes]
            info = self.primary.call("yed_getinfo")
            idx = info.get("height", -1)
            if min(heights) >= height and index_reached(info, height):
                return
            if time.monotonic() > deadline:
                raise RuntimeError(
                    f"devnet not synced to {height} after {timeout:.0f} s: nodes {heights}, index {idx}"
                )
            time.sleep(0.1)

    def stop(self, *, wipe: bool = False, grace: float = 60.0) -> None:
        """RPC ``stop`` every node, wait, then SIGTERM/SIGKILL stragglers; ``wipe`` deletes the datadirs."""
        for n in self.nodes:
            with contextlib.suppress(RpcError):
                n.client.call("stop")
        deadline = time.monotonic() + grace
        for n in self.nodes:
            if n.proc is None:
                continue
            try:
                n.proc.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    try:
                        n.proc.send_signal(sig)
                        n.proc.wait(timeout=10)
                        break
                    except (subprocess.TimeoutExpired, OSError):
                        continue
        if wipe:
            for n in self.nodes:
                shutil.rmtree(n.datadir, ignore_errors=True)

    def __enter__(self) -> MinimalDevnet:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()


# ---------------------------------------------------------------------------------------------------
# yellowback-devnet launcher


class LauncherDevnet:
    """``contrib/yellowback/devnet/yellowback-devnet`` from a worktree (role ``none``).

    ``up`` mines the funding and activation blocks itself (and arms the attestors unless
    ``attest`` is false), so a schedule replayed here must skip its bootstrap steps.
    """

    def __init__(
        self,
        worktree: Path,
        ycashd: Path,
        run_dir: Path,
        *,
        portseed: int = DEFAULT_PORTSEED,
        python: str | None = None,
        attest: bool = True,
        price_usd: str = "50",
        run: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
    ) -> None:
        self.script = worktree / "contrib" / "yellowback" / "devnet" / "yellowback-devnet"
        self.ycashd, self.run_dir, self.portseed = ycashd, run_dir, portseed
        self.python = python or os.environ.get("YBCAL_DEVNET_PYTHON") or sys.executable
        self.attest, self.price_usd, self._run = attest, price_usd, run
        self.state: dict[str, Any] = {}
        self.pools: list[Any] = []
        self.dark: Any | None = None
        self.primary: Any = None
        self.attestor_nodes: list[int] = []

    def _cmd(self, *args: str) -> list[str]:
        return [self.python, str(self.script), *args]

    def up_command(self) -> list[str]:
        """The ``up`` invocation."""
        cmd = self._cmd(
            "up",
            "--dir",
            str(self.run_dir),
            "--portseed",
            str(self.portseed),
            "--bitcoind",
            str(self.ycashd),
            "--no-viz",
            "--price",
            self.price_usd,
        )
        return [*cmd, "--no-attest"] if not self.attest else cmd

    def start(self) -> None:
        """``up``, then read ``devnet.json`` for the RPC endpoints."""
        if not self.script.exists():
            raise FileNotFoundError(f"launcher not found: {self.script}")
        proc = self._run(
            self.up_command(), capture_output=True, text=True, env={**os.environ, "ZCASHD": str(self.ycashd)}
        )
        if proc.returncode != 0:
            raise RuntimeError(f"yellowback-devnet up failed: {(proc.stderr or proc.stdout or '')[-2000:]}")
        self.state = json.loads((self.run_dir / "devnet.json").read_text())
        rpc = self.state["rpc"]

        def client(n: int) -> RpcClient:
            r = rpc[str(n)]
            return RpcClient(
                r["url"] if r["url"].startswith("http") else f"http://{r['url']}",
                r["user"],
                r["password"],
                timeout=120,
            )

        self.primary = client(0)
        self.pools = [client(n) for n in self.state.get("auto_pools") or self.state["pools"]]
        self.attestor_nodes = list(self.state.get("auto_attestors") or [])
        self._all = [client(n) for n in range(int(self.state["num_nodes"])) if n != 1]  # node 1 is stock

    def wait_synced(self, height: int, timeout: float = 120.0) -> None:
        """Until every Yellowback node is at ``height`` and node 0's index reports it."""
        deadline = time.monotonic() + timeout
        while True:
            hs = [int(c.call("getblockcount")) for c in self._all]
            if min(hs) >= height and index_reached(self.primary.call("yed_getinfo"), height):
                return
            if time.monotonic() > deadline:
                raise RuntimeError(f"launcher devnet not synced to {height}: {hs}")
            time.sleep(0.1)

    def write_attest_price(self, micro_usd: int) -> None:
        """Every automated attestor's mock price file (dollars), as ``yellowback-devnet price`` does."""
        usd = f"{micro_usd / 1_000_000:.6f}"
        for n in self.attestor_nodes:
            p = self.run_dir / f"attest-price-{n}"
            tmp = p.with_suffix(".tmp")
            tmp.write_text(usd + "\n")
            tmp.replace(p)

    def set_attestor_down(self, index: int, down: bool) -> None:
        """``yellowback-devnet attestor N stop|start`` for the ``index``-th automated attestor."""
        node = self.attestor_nodes[index]
        self._run(
            self._cmd("attestor", str(node), "stop" if down else "start", "--dir", str(self.run_dir)),
            capture_output=True,
            text=True,
        )

    def stop(self, *, wipe: bool = False) -> None:
        """``down [--wipe]``."""
        args = ["down", "--dir", str(self.run_dir)] + (["--wipe"] if wipe else [])
        self._run(self._cmd(*args), capture_output=True, text=True)


# ---------------------------------------------------------------------------------------------------
# Replay


@dataclass(frozen=True)
class BlockEvent:
    """One mined block of a replay."""

    height: int
    miner: str  #: "pool<j>" or "dark"
    quote: int | None  #: the quote set on the miner right before (None: none / dark)
    step: int


@dataclass
class ReplayLog:
    """What a replay did, block by block."""

    start_height: int
    events: list[BlockEvent] = field(default_factory=list)
    driver: WalletDriver | None = None  #: the wallet-action driver, when the schedule had actions

    @property
    def end_height(self) -> int:
        """Last height mined (``start_height`` when nothing was mined)."""
        return self.events[-1].height if self.events else self.start_height

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {"start_height": self.start_height, "events": [e.__dict__ for e in self.events]}


def interleave(counts: Sequence[int]) -> list[int]:
    """Indices ``i`` repeated ``counts[i]`` times, interleaved by largest deficit (every prefix is as
    close to the target ratio as integers allow; ties to the lower index) — the framework's
    ``round_robin_schedule`` ordering."""
    n = sum(counts)
    mined = [0] * len(counts)
    out: list[int] = []
    for step in range(1, n + 1):
        open_ = [i for i in range(len(counts)) if mined[i] < counts[i]]
        i = max(open_, key=lambda i: (counts[i] * step - mined[i] * n, -i))
        mined[i] += 1
        out.append(i)
    return out


def apportion(n: int, weights: Sequence[int]) -> list[int]:
    """Split ``n`` by ``weights`` with largest remainders (sums to ``n`` exactly)."""
    total = sum(weights)
    if total <= 0:
        raise ValueError("weights must sum to a positive number")
    base = [n * w // total for w in weights]
    rema = sorted(range(len(weights)), key=lambda i: (-(n * weights[i] % total), i))
    for i in rema[: n - sum(base)]:
        base[i] += 1
    return base


class MinerPlan:
    """Who mines each block, carried across the whole schedule (smooth weighted round robin).

    Every block credits each pool ``share · w_i / Σw`` and the dark miner ``1 − share``; the
    largest credit mines (ties: pools before the dark miner, lower index first) and pays 1. Exact
    fractions, so the node replay and the simulator draw the identical sequence. Earlier versions
    interleaved within each step only: the many one-block steps of a price walk were then all
    mined by pool 0, so ``pool_weights`` and ``signal_share_bps`` had no effect (D-RD-DEV-2).
    """

    def __init__(self, n_pools: int, has_dark: bool) -> None:
        from fractions import Fraction

        self._F = Fraction
        self.n_pools, self.has_dark = n_pools, has_dark
        self.credit = [Fraction(0)] * n_pools
        self.dark_credit = Fraction(0)

    def miners(self, step: ReplayStep) -> list[int]:
        """The miner of each block of ``step``: a pool index, or ``-1`` for the dark miner."""
        F = self._F
        share = 10_000 if step.signal_share_bps is None else step.signal_share_bps
        if share < 10_000 and not self.has_dark:
            raise ValueError(f"step {step.label!r} needs a dark miner (signal_share_bps={share})")
        weights = list(step.pool_weights) if step.pool_weights else [1] * self.n_pools
        if len(weights) != self.n_pools:
            raise ValueError(f"pool_weights has {len(weights)} entries for {self.n_pools} pools")
        tot = sum(weights)
        out = []
        for _ in range(step.blocks):
            for i, w in enumerate(weights):
                self.credit[i] += F(share * w, 10_000 * tot)
            self.dark_credit += F(10_000 - share, 10_000)
            best = max(range(self.n_pools), key=lambda i: (self.credit[i], -i))
            if self.has_dark and self.dark_credit > self.credit[best]:
                self.dark_credit -= 1
                out.append(-1)
            else:
                self.credit[best] -= 1
                out.append(best)
        return out


def block_miners(step: ReplayStep, n_pools: int, has_dark: bool) -> list[int]:
    """Miner of each block of ``step`` alone (a fresh :class:`MinerPlan`)."""
    return MinerPlan(n_pools, has_dark).miners(step)


def jittered_quote(
    price: int, bias_bps: int, jitter_bps: int, rng: random.Random, previous: int | None
) -> int:
    """``price · (1 + bias)`` plus integer jitter of ±``jitter_bps`` (at least ±1 µUSD), clamped to
    the valid range and never equal to ``previous``."""
    base = price * (10_000 + bias_bps) // 10_000
    span = max(1, base * jitter_bps // 10_000)
    q = base + rng.randint(-span, span)
    q = max(PRICE_MIN, min(PRICE_MAX, q))
    if q == previous:
        q = q + 1 if q < PRICE_MAX else q - 1
    return q


def step_quote(step: ReplayStep, pool: int, jitter_bps: int, rng: random.Random, previous: int | None) -> int:
    """The quote ``pool`` sets for one block of ``step``: its previous quote again while the pool is
    in ``frozen_pools`` (no RNG draw), else :func:`jittered_quote`."""
    if pool in step.frozen_pools and previous is not None:
        return previous
    return jittered_quote(step.price, int(step.pool_bias_bps.get(pool, 0)), jitter_bps, rng, previous)


def replay(
    devnet: Devnet,
    steps: Sequence[ReplayStep],
    *,
    seed: int = 0,
    jitter_bps: int = 10,
    on_step: Callable[[int, ReplayStep, ReplayLog], None] | None = None,
    attestor_price: Callable[[int], None] | None = None,
    attestor_down: Callable[[int, bool], None] | None = None,
    driver: WalletDriver | None = None,
) -> ReplayLog:
    """Execute ``steps`` on ``devnet``: per block, quote on the miner (jittered) and ``generate 1``.

    ``attestor_price`` / ``attestor_down`` are the launcher's hooks (mock price files, agent
    stop/start); a step with ``attestors_down`` and no hook raises. Steps with ``actions`` run them
    through ``driver`` (created over ``devnet.nodes`` when omitted), which also keeps the mempools
    in step before every block; the driver ends up in ``ReplayLog.driver``.
    """
    rng = random.Random(seed)
    pools = devnet.pools
    last: list[int | None] = [None] * len(pools)
    height = int(devnet.primary.call("getblockcount"))
    log = ReplayLog(start_height=height)
    wants_seats = attestor_down is None and any(st.attestors_down for st in steps)
    if driver is None and (wants_seats or any(st.actions for st in steps)):
        nodes = getattr(devnet, "nodes", None)
        clients = [n.client for n in nodes] if nodes else [*pools, *([devnet.dark] if devnet.dark else [])]
        driver = WalletDriver(clients)
    log.driver = driver
    if attestor_down is None and driver is not None:
        attestor_down = driver.set_down
    down: set[int] = set()
    plan = MinerPlan(len(pools), devnet.dark is not None)
    for si, step in enumerate(steps):
        if driver is not None:
            driver.price = step.price
        for action in step.actions:
            if driver is None:
                raise ValueError("step actions need a WalletDriver")
            driver.run(action, height)
        want_down = set(step.attestors_down)
        if want_down != down:
            if attestor_down is None:
                raise ValueError(f"step {si} stops attestors but this devnet has no attestor seats")
            for a in sorted(want_down - down):
                attestor_down(a, True)
            for a in sorted(down - want_down):
                attestor_down(a, False)
            down = want_down
        if attestor_price is not None and step.price:
            attestor_price(step.price)
        if step.price == 0:
            for j, c in enumerate(pools):
                if last[j] is not None:
                    c.call("yed_setquote", 0, 0)
                    last[j] = None
        for m in plan.miners(step):
            quote: int | None = None
            if m < 0:
                miner, client = "dark", devnet.dark
            else:
                miner, client = f"pool{m}", pools[m]
                if step.price:
                    quote = step_quote(step, m, jitter_bps, rng, last[m])
                    client.call("yed_setquote", quote, 1)
                    last[m] = quote
            if driver is not None:
                driver.sync_mempool(client)
            client.call("generate", 1)
            height += 1
            devnet.wait_synced(height)
            log.events.append(BlockEvent(height, miner, quote, si))
            if driver is not None:
                driver.after_block(height)
        if on_step is not None:
            on_step(si, step, log)
    return log


# ---------------------------------------------------------------------------------------------------
# Orchestration


@dataclass
class RunResult:
    """A finished devnet run (or the reason it stopped)."""

    status: Literal["ok", "skipped", "refused", "failed"]
    run_dir: Path | None
    schedule: Schedule
    binary: BinaryInfo | None = None
    skew: SkewReport | None = None
    node_check: NodeCheck | None = None
    replay: ReplayLog | None = None
    scrape: ScrapeResult | None = None
    message: str = ""
    params: dict[str, Any] | None = None  #: the overlay's full regtest value set (for ``devnet diff``)
    replay_args: dict[str, Any] = field(default_factory=dict)  #: seed, jitter_bps, n_pools

    def to_dict(self) -> dict[str, Any]:
        """The run manifest (``run.json``)."""
        return {
            "format": "ybcal-devnet-run/1",
            "status": self.status,
            "message": self.message,
            "run_dir": str(self.run_dir) if self.run_dir else None,
            "schedule": self.schedule.to_dict(),
            "binary": None
            if self.binary is None
            else {
                "ycashd": str(self.binary.ycashd),
                "origin": self.binary.origin,
                "commit": self.binary.commit,
                "key": self.binary.key,
            },
            "skew": self.skew.to_dict() if self.skew else None,
            "node_check": None
            if self.node_check is None
            else {k: list(v) for k, v in self.node_check.differences.items()},
            "replay": {"start_height": self.replay.start_height, "end_height": self.replay.end_height}
            if self.replay
            else None,
            "scrape": {k: str(v) for k, v in self.scrape.files.items()} if self.scrape else None,
            "params": self.params,
            "replay_args": self.replay_args,
        }


def _sigterm_raises() -> Callable[[], None]:
    """In the main thread, make SIGTERM raise (``KeyboardInterrupt``) so ``run_devnet``'s ``finally``
    still stops the nodes when the run is killed (``timeout``, a supervisor); returns the restorer.
    A background shell job ignores SIGINT, so SIGTERM is the signal that reaches it."""
    import threading

    if threading.current_thread() is not threading.main_thread():
        return lambda: None

    def handler(signum: int, frame: Any) -> None:
        raise KeyboardInterrupt(f"signal {signum}")

    old = signal.signal(signal.SIGTERM, handler)
    return lambda: signal.signal(signal.SIGTERM, old)


def closing_txinfo(client: Any, vaults: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """``yed_gettxinfo`` of every vault's closing transaction (its refHeight, path, burn), by vault."""
    out: dict[str, Any] = {}
    for v in vaults:
        txid = v.get("closingTxid")
        if txid:
            try:
                out[v["vault"]] = client.call("yed_gettxinfo", txid)
            except RpcError as e:
                out[v["vault"]] = {"error": str(e)}
    return out


def mint_txinfo(client: Any, vaults: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """``yed_gettxinfo`` of every vault's MINT (refHeight, xMint/aMint, bundleSeqs), by vault."""
    out: dict[str, Any] = {}
    for v in vaults:
        try:
            out[v["vault"]] = client.call("yed_gettxinfo", v["txid"])
        except RpcError as e:
            out[v["vault"]] = {"error": str(e)}
    return out


def default_run_dir(name: str) -> Path:
    """``.work/devnet/<name>-<timestamp>``."""
    return work_dir() / "devnet" / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}"


def run_devnet(
    schedule: Schedule,
    sp: OverlaySplit,
    *,
    ycashd: str | Path | None = None,
    repo: str | Path | None = None,
    commit: str | None = None,
    run_dir: Path | None = None,
    portseed: int = DEFAULT_PORTSEED,
    port_base: int | None = None,
    n_pools: int = 3,
    seed: int = 0,
    jitter_bps: int = 10,
    allow_version_skew: bool = False,
    keep: bool = False,
    launcher_worktree: Path | None = None,
    devnet_factory: Callable[[DevnetConfig], Any] | None = None,
) -> RunResult | Skipped:
    """Resolve a binary, check its version, start a devnet, replay ``schedule``, scrape, stop.

    Returns :class:`Skipped` for environmental causes (no binary, attestor seats unavailable);
    a ``refused`` result when the binary's commit or the node's parameters do not match the pin
    / overlay and ``allow_version_skew`` is false.
    """
    from ybcal.params.registry import PINNED_COMMIT

    commit = commit or PINNED_COMMIT
    binfo = resolve_binary(ycashd, key=build_key(sp, commit))
    if isinstance(binfo, Skipped):
        return binfo
    if launcher_worktree is not None and any(sp.runtime[k] != v for k, v in LAUNCHER_RUNTIME.items()):
        return Skipped(
            "the launcher hard-codes -yellowbackstartheight=1 -yellowbacksigmaref=0 and passes no other "
            f"runtime flag; this overlay sets {sp.runtime} — use the minimal backend",
            "run",
        )
    run_dir = run_dir or default_run_dir(schedule.name)
    version = binary_version(binfo.ycashd)
    skew = check_skew(repo, binfo.commit or version.commit, commit, allow=allow_version_skew)
    result = RunResult("ok", run_dir, schedule, binfo, skew)
    result.params = sp.params.to_dict()
    result.replay_args = {"seed": seed, "jitter_bps": jitter_bps, "n_pools": n_pools}
    if not skew.ok:
        result.status, result.message = (
            "refused",
            (skew.message or f"binary relation to the pin: {skew.relation}")
            + " (pass --allow-version-skew to run anyway)",
        )
        return result
    run_dir.mkdir(parents=True, exist_ok=True)
    steps: list[ReplayStep] = list(schedule.steps)
    attestor_price = attestor_down = None
    if launcher_worktree is not None:
        net: Any = LauncherDevnet(
            launcher_worktree, binfo.ycashd, run_dir, portseed=portseed, attest="attestors" in schedule.needs
        )
        steps = [s for s in steps if s.label not in ("funding", "activation")]  # `up` did the bootstrap
        attestor_price, attestor_down = net.write_attest_price, net.set_attestor_down
    else:
        cfg = DevnetConfig(
            binfo.ycashd,
            run_dir,
            sp.node_args(),
            portseed=portseed,
            port_base=port_base if port_base is not None else default_port_base(),
            n_pools=n_pools,
            dark_miner="dark_miner" in schedule.needs,
        )
        net = (devnet_factory or MinimalDevnet)(cfg)
    restore = _sigterm_raises()
    try:
        net.start()
        result.node_check = check_node_params(net.primary, sp.params, allow=allow_version_skew)
        if not result.node_check.ok:
            result.status = "refused"
            result.message = f"node parameters differ from the overlay: {result.node_check.fatal}"
            return result
        driver = None
        if launcher_worktree is None and any(st.actions or st.attestors_down for st in steps):
            driver = WalletDriver(
                [n.client for n in net.nodes],
                attest_interval=int(sp.params["attestInterval"]),
                ref_lag=int(sp.params["DEFAULT_REF_LAG"]),
            )
        result.replay = replay(
            net,
            steps,
            seed=seed,
            jitter_bps=jitter_bps,
            attestor_price=attestor_price,
            attestor_down=attestor_down,
            driver=driver,
        )
        result.scrape = scrape(net.primary, run_dir / "scrape")
        if result.replay.driver is not None:
            doc = result.replay.driver.to_dict()
            doc["closing"] = closing_txinfo(net.primary, result.scrape.vaults)
            doc["mints"] = mint_txinfo(net.primary, result.scrape.vaults)
            path = run_dir / "scrape" / "actions.json"
            path.write_text(json.dumps(doc, indent=2, default=str) + "\n")
            result.scrape.files["actions"] = path
    except Exception as e:
        result.status, result.message = "failed", f"{type(e).__name__}: {e}"
        with contextlib.suppress(Exception):  # best effort: keep what the chain shows for the post-mortem
            scrape(net.primary, run_dir / "scrape-partial")
        raise
    finally:
        try:
            net.stop(wipe=not keep)
            restore()
        finally:
            (run_dir / "run.json").write_text(json.dumps(result.to_dict(), indent=2, default=str) + "\n")
    return result

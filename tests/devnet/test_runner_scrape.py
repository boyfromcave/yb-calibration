"""Price replay, the minimal launcher, the RPC client and scrape normalisation, over a fake node."""

from __future__ import annotations

import itertools
import json
import random
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from tests.devnet.fakenode import USER, FakeChain, FakeDevnet, FakeServer, history_row, vault_row
from ybcal.devnet import runner as r
from ybcal.devnet import scrape as sc
from ybcal.devnet.keys import pool_addresses
from ybcal.devnet.overlay import split
from ybcal.devnet.rpc import RpcClient, RpcError
from ybcal.devnet.scenarios import ReplayStep, Schedule, make_schedule
from ybcal.devnet.status import Skipped
from ybcal.params.paramset import regtest


@pytest.fixture
def srv():
    with FakeServer(FakeChain(regtest())) as s:
        yield s


# --- RPC client ------------------------------------------------------------------------------------------


def test_rpc_client_calls_errors_and_auth(srv: FakeServer):
    c = srv.client(0)
    assert c.getblockcount() == 0
    assert c.call("generate", 2) == [f"{1:064x}", f"{2:064x}"]
    with pytest.raises(RpcError) as e:
        c.call("nope")
    assert e.value.code == -32601
    bad = RpcClient(srv.url(0), USER, "wrong", timeout=5)
    with pytest.raises(RpcError, match="401"):
        bad.getblockcount()
    with pytest.raises(RpcError) as e:
        RpcClient("http://127.0.0.1:1/", timeout=2).getblockcount()
    assert e.value.code is None


def test_wait_ready_through_warmup():
    chain = FakeChain(regtest(), height=7, warmup=3)
    with FakeServer(chain) as s:
        assert s.client().wait_ready(timeout=10, poll=0.01) == 7


def test_read_cookie(tmp_path: Path):
    from ybcal.devnet.rpc import read_cookie

    (tmp_path / "regtest").mkdir()
    (tmp_path / "regtest" / ".cookie").write_text("__cookie__:abc\n")
    assert read_cookie(tmp_path) == ("__cookie__", "abc")


# --- schedules -------------------------------------------------------------------------------------------


def test_interleave_apportion_block_miners():
    assert r.apportion(10, [34, 33, 33]) == [4, 3, 3]
    assert sum(r.apportion(7, [1, 1, 1])) == 7
    assert r.interleave([2, 1]) == [0, 1, 0]
    assert Counter(r.interleave([5, 3, 2])) == {0: 5, 1: 3, 2: 2}
    m = r.block_miners(ReplayStep(1_000_000, 20, signal_share_bps=4500), 3, True)
    assert m.count(-1) == 11 and len(m) == 20
    assert r.block_miners(ReplayStep(1_000_000, 6), 3, False) == [0, 1, 2, 0, 1, 2]
    with pytest.raises(ValueError, match="dark miner"):
        r.block_miners(ReplayStep(1_000_000, 4, signal_share_bps=5000), 3, False)


def test_jittered_quote_bounds_and_never_repeats():
    rng = random.Random(1)
    prev = None
    for _ in range(500):
        q = r.jittered_quote(50_000_000, 0, 10, rng, prev)
        assert abs(q - 50_000_000) <= 50_001 and q != prev
        prev = q
    assert r.jittered_quote(100, 0, 10, rng, 100) != 100  # clamped at PRICE_MIN, still moves
    assert r.jittered_quote(1_000_000, 2_500, 0, rng, None) in (1_249_999, 1_250_000, 1_250_001)


def test_schedules_for_the_suite():
    ps = regtest()
    for name in ("calm", "crash-70", "hashrate-drop", "attestor-outage-1", "oracle-attack-34"):
        s = make_schedule(name, ps, seed=3)
        assert s.steps[0].label == "funding" and s.steps[0].price == 0 and s.steps[0].blocks == 101
        assert s.steps[1].blocks == 64 + 64 + 2
        assert s == make_schedule(name, ps, seed=3)  # deterministic in the seed
    assert "dark_miner" in make_schedule("hashrate-drop", ps).needs
    assert "attestors" in make_schedule("attestor-outage-1", ps).needs
    crash = make_schedule("crash-70", ps, seed=1)
    prices = [st.price for st in crash.steps if st.label == "crash"]
    assert prices[-1] * 10 // prices[0] in (2, 3)
    with pytest.raises(KeyError):
        make_schedule("nope", ps)


def test_price_file_schedule(tmp_path: Path):
    f = tmp_path / "p.csv"
    f.write_text("block,price\n0,1000000\n1,1000000\n2,900000\n")
    s = make_schedule(str(f), regtest(), file_blocks_per_step=2)
    assert [(st.price, st.blocks) for st in s.steps[2:]] == [(1_000_000, 4), (900_000, 2)]
    j = tmp_path / "p.json"
    j.write_text(json.dumps([5_000_000, 6_000_000]))
    assert [st.price for st in make_schedule(str(j), regtest(), bootstrap=False).steps] == [
        5_000_000,
        6_000_000,
    ]


# --- replay -----------------------------------------------------------------------------------------------


def test_replay_quotes_then_mines_each_block(srv: FakeServer):
    net = FakeDevnet(srv)
    steps = [
        ReplayStep(0, 4, label="funding"),
        ReplayStep(2_000_000, 6),
        ReplayStep(1_000_000, 3),
        ReplayStep(0, 2, label="outage"),
        ReplayStep(1_500_000, 3, pool_bias_bps={0: 2500}),
    ]
    log = r.replay(net, steps, seed=7, jitter_bps=10)
    chain = srv.chain
    assert chain.height == 18 and log.start_height == 0 and log.end_height == 18
    assert net.synced == list(range(1, 19))
    # every quoted block: the miner's setquote immediately precedes its generate
    calls = [(n, m, p) for n, m, p in chain.calls if m in ("yed_setquote", "generate")]
    gen_idx = [i for i, c in enumerate(calls) if c[1] == "generate"]
    quoted = [e for e in log.events if e.quote is not None]
    assert len(quoted) == 12
    for e, gi in zip(log.events, gen_idx, strict=True):
        node = int(e.miner.removeprefix("pool"))
        assert calls[gi][0] == node
        if e.quote is not None:
            assert calls[gi - 1] == (node, "yed_setquote", [e.quote, 1])
    # jitter: within 10 bps of the step price, never the same quote twice in a row per pool
    for e in log.events:
        st = steps[e.step]
        if e.quote is not None:
            base = st.price * (10_000 + st.pool_bias_bps.get(int(e.miner[-1]), 0)) // 10_000
            assert abs(e.quote - base) <= base // 1000 + 1
    per_pool: dict[str, list[int]] = {}
    for e in quoted:
        per_pool.setdefault(e.miner, []).append(e.quote)
    for qs in per_pool.values():
        assert all(a != b for a, b in itertools.pairwise(qs))
    # the outage cleared every pool's quote once
    clears = [c for c in chain.calls if c[1] == "yed_setquote" and c[2] == [0, 0]]
    assert sorted(c[0] for c in clears) == [0, 1, 2]
    assert all(e.quote is None for e in log.events if steps[e.step].label in ("funding", "outage"))


def test_replay_weights_dark_miner_and_attestor_hooks(srv: FakeServer):
    net = FakeDevnet(srv, dark=True)
    downs: list[tuple[int, bool]] = []
    prices: list[int] = []
    steps = [
        ReplayStep(1_000_000, 100, pool_weights=(34, 33, 33), signal_share_bps=8000),
        ReplayStep(1_000_000, 2, attestors_down=(0,)),
        ReplayStep(1_000_000, 1),
    ]
    log = r.replay(net, steps, attestor_down=lambda i, d: downs.append((i, d)), attestor_price=prices.append)
    by = Counter(e.miner for e in log.events if e.step == 0)
    assert by["dark"] == 20 and (by["pool0"], by["pool1"], by["pool2"]) == (27, 27, 26)
    assert all(e.quote is None for e in log.events if e.miner == "dark")
    assert downs == [(0, True), (0, False)] and prices == [1_000_000] * 3
    with pytest.raises(ValueError, match="attestor seats"):
        r.replay(FakeDevnet(srv), [ReplayStep(1_000_000, 1, attestors_down=(1,))])


# --- minimal launcher -------------------------------------------------------------------------------------


def test_ports_follow_the_seed():
    assert r.p2p_port(0, 7) == 11084 and r.rpc_port(0, 7) == 16084
    assert r.rpc_port(2, 101) - r.p2p_port(2, 101) == 5000


def test_node_conf(tmp_path: Path):
    sp = split(regtest().replace(sigmaRefBps=10_000))
    cfg = r.DevnetConfig(Path("/x/ycashd"), tmp_path, sp.node_args(), portseed=5, n_pools=2, dark_miner=True)
    c0, c2 = r.node_conf(cfg, 0), r.node_conf(cfg, 2)
    for line in (
        "regtest=1",
        "experimentalfeatures=1",
        "yellowback=1",
        "nuparams=19bd2d2f:1",
        "yellowbacksigmaref=10000",
        "yellowbackstartheight=1",
        "yellowbacksignal=1",
        f"yellowbackpayoutaddress={pool_addresses()[0]}",
        f"rpcport={r.rpc_port(0, 5)}",
    ):
        assert line in c0.splitlines(), line
    assert "[regtest]" not in c0
    assert "yellowbacksignal=0" in c2 and "yellowbackpayoutaddress" not in c2
    assert c0.count("addnode=") == 2
    with pytest.raises(ValueError):
        r.DevnetConfig(Path("y"), tmp_path, [], n_pools=4)


class FakeProc:
    def __init__(self, argv: list[str], **kw: Any) -> None:
        self.argv, self.waited = argv, False

    def wait(self, timeout: float | None = None) -> int:
        self.waited = True
        return 0

    def send_signal(self, sig: int) -> None:  # pragma: no cover - not reached
        pass


def test_minimal_devnet_start_stop(srv: FakeServer, tmp_path: Path):
    procs: list[FakeProc] = []

    def popen(argv: list[str], **kw: Any) -> FakeProc:
        procs.append(FakeProc(argv, **kw))
        return procs[-1]

    cfg = r.DevnetConfig(Path("/x/ycashd"), tmp_path / "run", split(regtest()).node_args())
    net = r.MinimalDevnet(cfg, popen=popen, client_factory=lambda url, u, p: srv.client(len(_seen(url))))
    net.start()
    assert [p.argv for p in procs] == [
        ["/x/ycashd", f"-datadir={tmp_path / 'run' / f'node{i}'}"] for i in range(3)
    ]
    assert all((tmp_path / "run" / f"node{i}" / "ycash.conf").exists() for i in range(3))
    imports = [c for c in srv.chain.calls if c[1] == "importprivkey"]
    assert len(imports) == 3
    log = r.replay(net, [ReplayStep(3_000_000, 3)])
    assert log.end_height == 3
    net.stop(wipe=True)
    assert all(p.waited for p in procs) and not (tmp_path / "run" / "node0").exists()
    with pytest.raises(RuntimeError, match="already holds"):
        (tmp_path / "run2" / "node0").mkdir(parents=True)
        (tmp_path / "run2" / "node0" / "x").write_text("")
        r.MinimalDevnet(
            r.DevnetConfig(Path("y"), tmp_path / "run2", []),
            popen=popen,
            client_factory=lambda *a: srv.client(0),
        ).start()


_URLS: list[str] = []


def _seen(url: str) -> list[str]:
    if url not in _URLS:
        _URLS.append(url)
    return _URLS[: _URLS.index(url)]


# --- orchestration -----------------------------------------------------------------------------------------


def _fake_binary(tmp_path: Path, commit: str = "7702d22") -> Path:
    exe = tmp_path / "bin" / "ycashd"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text(f"#!/bin/sh\necho 'Ycash Daemon version v6.21.0-rc1-{commit}'\n")
    exe.chmod(0o755)
    return exe


def test_run_devnet_end_to_end_with_fakes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("YBCAL_YCASHD", raising=False)
    sp = split(regtest())
    sched = Schedule("tiny", (ReplayStep(0, 3, label="funding"), ReplayStep(2_000_000, 9)))
    with FakeServer(FakeChain(regtest())) as s:

        def factory(cfg: r.DevnetConfig) -> FakeDevnetLife:
            return FakeDevnetLife(s, cfg.n_pools)

        res = r.run_devnet(
            sched, sp, ycashd=_fake_binary(tmp_path), run_dir=tmp_path / "run", devnet_factory=factory
        )
    assert isinstance(res, r.RunResult) and res.status == "ok", res.message
    assert res.skew.relation == "equal" and res.node_check.ok
    assert res.scrape.heights == (1, 12)
    rows = sc.load_history_csv(tmp_path / "run" / "scrape" / "history.csv")
    assert len(rows) == 12 and rows == res.scrape.history
    doc = json.loads((tmp_path / "run" / "run.json").read_text())
    assert doc["status"] == "ok" and doc["replay"] == {"start_height": 0, "end_height": 12}


def test_run_devnet_refuses_skewed_binary_and_params(tmp_path: Path):
    sp = split(regtest())
    sched = Schedule("tiny", (ReplayStep(1_000_000, 2),))
    res = r.run_devnet(sched, sp, ycashd=_fake_binary(tmp_path, "94bafa4"), run_dir=tmp_path / "a")
    assert res.status == "refused" and "allow-version-skew" in res.message
    with FakeServer(FakeChain(regtest().replace(abandonBlocks=4032))) as s:
        res = r.run_devnet(
            sched,
            sp,
            ycashd=_fake_binary(tmp_path),
            run_dir=tmp_path / "b",
            devnet_factory=lambda cfg: FakeDevnetLife(s, 3),
        )
    assert res.status == "refused" and "abandonBlocks" in res.message


def test_run_devnet_skips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("YBCAL_YCASHD", raising=False)
    monkeypatch.setenv("YBCAL_WORK", str(tmp_path))
    sp = split(regtest())
    sk = r.run_devnet(make_schedule("calm", regtest()), sp)
    assert isinstance(sk, Skipped) and "no ycashd binary" in sk.reason
    exe = _fake_binary(tmp_path)
    sk = r.run_devnet(make_schedule("attestor-outage-1", regtest()), sp, ycashd=exe)
    assert isinstance(sk, Skipped) and "attestor seats" in sk.reason
    sp2 = split(regtest().replace(sigmaRefBps=10_000))
    sk = r.run_devnet(make_schedule("calm", regtest()), sp2, ycashd=exe, launcher_worktree=tmp_path)
    assert isinstance(sk, Skipped) and "hard-codes" in sk.reason


class FakeDevnetLife(FakeDevnet):
    """FakeDevnet with the start/stop life cycle run_devnet drives."""

    def __init__(self, srv: FakeServer, n_pools: int) -> None:
        super().__init__(srv, n_pools)
        self.stopped = False

    def start(self) -> None:
        pass

    def stop(self, *, wipe: bool = False) -> None:
        self.stopped = True


def test_launcher_up_command(tmp_path: Path):
    ran: list[list[str]] = []
    ld = r.LauncherDevnet(
        tmp_path,
        Path("/b/ycashd"),
        tmp_path / "d",
        portseed=9,
        python="py",
        attest=False,
        run=lambda argv, **kw: ran.append(argv) or subprocess.CompletedProcess(argv, 0, "", ""),
    )
    cmd = ld.up_command()
    assert cmd[:3] == ["py", str(tmp_path / "contrib/yellowback/devnet/yellowback-devnet"), "up"]
    for a in ("--dir", "--portseed", "9", "--bitcoind", "/b/ycashd", "--no-viz", "--no-attest"):
        assert a in cmd
    ld.attestor_nodes = [5, 6]
    ld.run_dir.mkdir()
    ld.write_attest_price(1_234_567)
    assert (ld.run_dir / "attest-price-6").read_text() == "1.234567\n"
    ld.set_attestor_down(1, True)
    assert ran[-1][-4:] == ["6", "stop", "--dir", str(ld.run_dir)]


# --- scrape -------------------------------------------------------------------------------------------------


def test_normalize_history_row_types():
    rec = sc.normalize_history_row(history_row(200))
    assert tuple(rec) == sc.HISTORY_FIELDS
    assert rec["activationCode"] == 2 and rec["haltMask"] == 0 and rec["tagged"] == 1
    for k in ("pFast", "pMid", "pSlow", "sigmaMultBps", "issuedZat"):
        assert type(rec[k]) is int
    assert rec["globalRatioBps"] is None
    early = sc.normalize_history_row(history_row(5))
    assert (
        early["pFast"] is None and early["haltMask"] == 0b11 and early["haltNames"] == "NOT_ACTIVE|NO_PRICE"
    )
    bad = history_row(5) | {"sigmaMultBps": 1.5}
    with pytest.raises(sc.ScrapeError):
        sc.normalize_history_row(bad)
    with pytest.raises(sc.ScrapeError):
        sc.normalize_history_row(history_row(5) | {"haltMask": ["BOGUS"]})
    assert sc.halt_names(sc.halt_mask(["ENFORCEMENT", "NO_PRICE"])) == ["NO_PRICE", "ENFORCEMENT"]


def test_fetch_history_chunks():
    class C:
        def __init__(self) -> None:
            self.calls: list[tuple[int, int]] = []

        def call(self, m: str, a: int, b: int) -> list[dict[str, Any]]:
            self.calls.append((a, b))
            return [{"height": h} for h in range(a, b + 1)]

    c = C()
    rows = sc.fetch_history(c, 1, 5000)
    assert len(rows) == 5000 and c.calls == [(1, 2016), (2017, 4032), (4033, 5000)]
    with pytest.raises(ValueError):
        sc.fetch_history(c, 1, 2, chunk=2017)


def test_scrape_writes_typed_files(tmp_path: Path):
    chain = FakeChain(regtest(), n_vaults=230, height=2100, attestors=True)
    with FakeServer(chain) as s:
        res = sc.scrape(s.client(0), tmp_path / "out")
    assert res.heights == (1, 2100) and res.errors == {}
    hist_calls = [c for c in chain.calls if c[1] == "yed_gethistory"]
    assert [c[2] for c in hist_calls] == [[1, 2016], [2017, 2100]]
    assert len(res.vaults) == 230 and res.vaults[0]["vault"] == f"{0:064x}:0"
    assert res.vaults[0]["closeHeight"] is None and type(res.vaults[0]["collateralZat"]) is int
    assert "collateral" not in res.vaults[0]
    assert res.attestors[0]["weight"] == 31_000_000_000
    for name in (
        "history.csv",
        "history.json",
        "vaults.csv",
        "info.json",
        "stats.json",
        "activation.json",
        "attestors.json",
        "scrape.json",
    ):
        assert (tmp_path / "out" / name).exists(), name
    assert sc.load_history_csv(tmp_path / "out" / "history.csv") == res.history
    assert sc.normalize_vault(vault_row(1))["vault"].endswith(":0")


def test_scrape_v2_node_records_missing_attestors(tmp_path: Path):
    with FakeServer(FakeChain(regtest(), height=10)) as s:
        res = sc.scrape(s.client(0), None, from_height=3)
    assert res.attestors is None and "yed_listattestors" in res.errors
    assert res.heights == (3, 10) and res.files == {}

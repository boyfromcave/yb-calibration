"""Binary resolution, preflight, cached builds, CI artifacts and version-skew checks (devnet/build.py)."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import ycash6_path
from tests.devnet.fakenode import FakeChain, FakeServer, getinfo_params
from ybcal.devnet import build as b
from ybcal.devnet import worktree as wtmod
from ybcal.devnet.overlay import build_key, split
from ybcal.devnet.status import Skipped, is_skipped
from ybcal.params.paramset import regtest
from ybcal.params.registry import PARAMS_CPP, PINNED_COMMIT

REPO = ycash6_path()
needs_repo = pytest.mark.skipif(REPO is None, reason="no ycash6 clone (set YBCAL_YCASH6)")


@pytest.fixture
def work(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> Path:
    d = wtmod.PROJECT_ROOT / ".work" / f"pytest-{os.getpid()}-{request.node.name}"
    monkeypatch.setenv("YBCAL_WORK", str(d))
    monkeypatch.delenv("YBCAL_YCASHD", raising=False)
    yield d
    shutil.rmtree(d, ignore_errors=True)
    if REPO is not None:
        subprocess.run(["git", "-C", str(REPO), "worktree", "prune"], capture_output=True)


def fake_ycashd(path: Path, banner: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\necho '{banner}'\n")
    path.chmod(0o755)
    return path


# --- preflight -------------------------------------------------------------------------------------------


def test_preflight_here_is_skipped_or_ready():
    """In the cloud sandbox the depends hosts are blocked: preflight must say skipped, never fake ready."""
    res = b.preflight()
    assert isinstance(res, b.Ready | Skipped)
    if is_skipped(res):
        assert res.reason.startswith("cannot build ycashd here:")
        assert str(res).startswith("skipped (build): ")


def test_preflight_names_missing_tools_and_blocked_hosts():
    res = b.preflight(
        which=lambda t: None if t in ("autoconf", "libtoolize", "glibtoolize") else f"/bin/{t}",
        probe=lambda h: "z.cash" not in h,
    )
    assert isinstance(res, Skipped)
    assert "missing tool autoconf" in res.reason and "libtoolize or glibtoolize" in res.reason
    assert "https://download.z.cash/" in res.reason and "github.com" not in res.reason


def test_preflight_ready_and_depends_cache_skips_probe(tmp_path: Path):
    probed: list[str] = []
    res = b.preflight(which=lambda t: f"/bin/{t}", probe=lambda h: probed.append(h) or True, min_free_gb=0)
    assert isinstance(res, b.Ready) and len(probed) == len(b.DEPENDS_HOSTS)
    (tmp_path / "depends" / "x86_64-pc-linux-gnu" / "share").mkdir(parents=True)
    (tmp_path / "depends" / "x86_64-pc-linux-gnu" / "share" / "config.site").write_text("")
    probed.clear()
    res = b.preflight(tmp_path, which=lambda t: f"/bin/{t}", probe=lambda h: False, min_free_gb=0)
    assert isinstance(res, b.Ready) and res.depends_built and probed == []


def test_plan_build_steps(tmp_path: Path):
    cold = b.plan_build(tmp_path, 4, configured=False)
    assert cold[0].argv == ("./zcutil/build.sh", "-j4")
    assert cold[-1].argv == ("make", "-C", "src", "-j4", "ycashd", "ycash-cli")
    warm = b.plan_build(tmp_path, 4, configured=True)
    assert [s.argv[0] for s in warm] == ["sh", "make"]


def test_plan_build_borrows_a_built_tree(tmp_path: Path):
    src = tmp_path / "ycash6"
    (src / "depends" / "aarch64-apple-darwin25.0.0" / "share").mkdir(parents=True)
    (src / "depends" / "aarch64-apple-darwin25.0.0" / "share" / "config.site").write_text("")
    assert b.find_reuse(src) is None  # no cargo target yet
    (src / "target").mkdir()
    reuse = b.find_reuse(src)
    assert reuse is not None and reuse.triple == "aarch64-apple-darwin25.0.0"
    wt = tmp_path / "wt"
    steps = b.plan_build(wt, 4, configured=False, reuse=reuse)
    assert steps[0].argv[0] in ("/bin/cp", "cp")
    assert steps[0].argv[-2:] == (str(src / "target"), str(wt / "target"))
    assert "-cpR" in steps[0].argv or "-a" in steps[0].argv  # mtimes kept (cargo fingerprints)
    assert [s.argv[0] for s in steps[1:]] == ["./autogen.sh", "sh", "sh", "make"]
    assert "CONFIG_SITE=" in steps[2].argv[2] and "aarch64-apple-darwin25.0.0" in steps[2].argv[2]
    env = b.build_env(wt, {"PATH": "/usr/bin", "CARGO_TARGET_DIR": "/shared"})
    assert env["CARGO_TARGET_DIR"] == str(wt / "target") and env["PATH"].endswith("/usr/bin")


# --- binaries --------------------------------------------------------------------------------------------


def test_resolve_binary_order(work: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    assert isinstance(b.resolve_binary(), Skipped)
    exe = fake_ycashd(tmp_path / "a" / "ycashd", "Ycash Daemon version v6.21.0-rc1-7702d22")
    got = b.resolve_binary(exe)
    assert isinstance(got, b.BinaryInfo) and got.origin == "path" and got.ycashd == exe.resolve()
    monkeypatch.setenv("YBCAL_YCASHD", str(exe))
    assert b.resolve_binary().origin == "env"
    assert isinstance(b.resolve_binary(tmp_path / "missing"), Skipped)
    monkeypatch.delenv("YBCAL_YCASHD")
    cached = fake_ycashd(b.bin_dir("stock-x", work) / "ycashd", "v")
    (cached.parent / "manifest.json").write_text(json.dumps({"commit": "abc"}))
    hit = b.resolve_binary(key="stock-x")
    assert hit.origin == "cache" and hit.commit == "abc"


@pytest.mark.parametrize(
    "banner, version, commit, dirty",
    [
        ("Ycash Daemon version v6.21.0-rc1-94bafa4", "6.21.0-rc1", "94bafa4", False),
        ("Ycash Daemon version v6.21.0-rc1-94bafa4-dirty", "6.21.0-rc1", "94bafa4", True),
        ("Ycash Daemon version v6.21.0-rc1", "6.21.0-rc1", None, False),
        ("Zcash Daemon version v6.20.0-g7702d22606d1", "6.20.0", "7702d22606d1", False),
        ("garbage", "", None, False),
    ],
)
def test_parse_version_banner(banner: str, version: str, commit: str | None, dirty: bool):
    v = b.parse_version_banner(banner + "\nCopyright …")
    assert (v.version, v.commit, v.dirty) == (version, commit, dirty)


def test_binary_version_runs_the_binary(tmp_path: Path):
    exe = fake_ycashd(tmp_path / "ycashd", "Ycash Daemon version v6.21.0-rc1-94bafa4")
    assert b.binary_version(exe).commit == "94bafa4"


# --- skew ------------------------------------------------------------------------------------------------


@needs_repo
def test_skew_ci_commit_predates_pin():
    rep = b.check_skew(REPO, b.KNOWN_CI_COMMIT)
    assert rep.relation == "predates" and not rep.ok
    assert len(rep.commits_between) == 8
    assert rep.param_diffs["regtest"] == {}  # regtest column identical …
    assert rep.param_diffs["main"] == {"abandonBlocks": (4032, 34560)}  # … W21 changed mainnet only
    assert any("W20" in c for c in rep.commits_between)  # the soft-cap rule change is listed
    assert b.check_skew(REPO, b.KNOWN_CI_COMMIT, allow=True).ok
    eq = b.check_skew(REPO, PINNED_COMMIT)
    assert eq.relation == "equal" and eq.ok
    assert b.check_skew(REPO, "0" * 12).relation == "unknown"


def test_skew_without_repo_or_commit():
    assert b.check_skew(None, None).relation == "unknown"
    assert not b.check_skew(None, None).ok
    assert b.check_skew(None, "7702d22606d1").relation == "equal"
    assert b.check_skew(None, "94bafa4").relation == "unknown"


def test_compare_node_params_matches_and_detects():
    ps = regtest()
    params = getinfo_params(ps)
    act = {
        "window": 64,
        "threshold": 48,
        "participationFloor": 39,
        "enforcementFloor": 32,
        "enforcementResume": 39,
    }
    assert b.compare_node_params(params, ps, act) == {}
    params["abandonBlocks"] = 4032
    params["attest"]["carrierMode"] = "either"
    params["classes"][2]["maxBlocks"] = 241
    del params["windows"]["fast"]
    act["threshold"] = 47
    diff = b.compare_node_params(params, ps, act)
    assert diff == {
        "abandonBlocks": (4032, 128),
        "bundleCarrier": ("EITHER", "SCRIPTSIG"),
        "classMax[2]": (241, 240),
        "pFastWindow": (None, 8),
        "activationThreshold": (47, 48),
    }


def test_version_skew_detected_through_fake_node():
    """A node built at another commit reports other params: check_node_params flags them over RPC."""
    node_side = regtest().replace(abandonBlocks=4032 // 16, nPenalty=13)  # an "older" column
    with FakeServer(FakeChain(node_side)) as srv:
        chk = b.check_node_params(srv.client(0), regtest())
        assert set(chk.differences) == {"abandonBlocks", "nPenalty"}
        assert set(chk.fatal) == {"abandonBlocks"} and not chk.ok  # nPenalty is node-overridable policy
        assert b.check_node_params(srv.client(0), regtest(), allow=True).ok
    with FakeServer(FakeChain(regtest())) as srv:
        assert b.check_node_params(srv.client(0), regtest()).ok


# --- build (fake commands, real worktree) ----------------------------------------------------------------


@needs_repo
def test_build_pipeline_with_fake_make(work: Path):
    sp = split(regtest().replace(deviationBps=1500))
    calls: list[list[str]] = []

    def run(argv: list[str], cwd: Path, **kw: Any) -> subprocess.CompletedProcess[Any]:
        calls.append(argv)
        if argv[0] == "make":
            fake_ycashd(Path(cwd) / "src" / "ycashd", "Ycash Daemon version v6.21.0-rc1-7702d22-dirty")
            fake_ycashd(Path(cwd) / "src" / "ycash-cli", "cli")
        return subprocess.CompletedProcess(argv, 0)

    ready = lambda wt: b.Ready({}, {}, False)  # noqa: E731
    res = b.build(REPO, sp, run=run, preflight_fn=ready, jobs=2, reuse_from=None)
    try:
        assert isinstance(res, b.BuildResult) and not res.cached
        assert res.binary.key == build_key(sp, wtmod.resolve_commit(REPO, PINNED_COMMIT))
        assert [c[0] for c in calls] == ["./zcutil/build.sh", "sh", "make"]
        assert "r.deviationBps = 1500;" in (res.worktree / PARAMS_CPP).read_text()
        man = json.loads((res.binary.ycashd.parent / "manifest.json").read_text())
        assert man["compiled"] == {"deviationBps": 1500} and man["patch"].startswith("--- a/")
        again = b.build(REPO, sp, run=run, preflight_fn=ready, reuse_from=None)
        assert again.cached and len(calls) == 3
        # a stock overlay restores the pristine file in the same worktree
        b.build(REPO, split(regtest()), run=run, preflight_fn=ready, reuse_from=None)
        assert "deviationBps = 1500" not in (res.worktree / PARAMS_CPP).read_text()
    finally:
        wtmod.remove_worktree(res.worktree, REPO)


@needs_repo
def test_build_skipped_creates_no_worktree(work: Path):
    res = b.build(REPO, split(regtest()), preflight_fn=lambda wt: Skipped("no toolchain", "build"))
    assert isinstance(res, Skipped)
    assert not any(p.name.startswith("ycash6-") for p in work.glob("*"))


# --- CI artifacts ----------------------------------------------------------------------------------------


def _artifact_tarball(dirpath: Path) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name in ("ycashd", "ycash-cli", "ycash-tx"):
            data = b"#!/bin/sh\necho 'Ycash Daemon version v6.21.0-rc1-94bafa4'\n"
            info = tarfile.TarInfo(f"ycashd_v6.21.0-rc1_linux_x86_64/{name}")
            info.size, info.mode = len(data), 0o644
            tf.addfile(info, io.BytesIO(data))
    (dirpath / "ycashd_v6.21.0-rc1_linux_x86_64.tar.gz").write_bytes(buf.getvalue())


class FakeGh:
    def __init__(self, download_ok: bool = True, api_ok: bool = False) -> None:
        self.download_ok, self.api_ok, self.calls = download_ok, api_ok, []

    def __call__(self, argv: list[str], **kw: Any) -> subprocess.CompletedProcess[Any]:
        self.calls.append(argv)
        if argv[1:3] == ["run", "view"]:
            return subprocess.CompletedProcess(
                argv, 0, json.dumps({"headSha": "94bafa4fde5b" + "0" * 28}), ""
            )
        if argv[1:3] == ["run", "download"]:
            if not self.download_ok:
                return subprocess.CompletedProcess(argv, 1, "", "error downloading: blob storage blocked")
            _artifact_tarball(Path(argv[argv.index("-D") + 1]))
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[1] == "api" and argv[2].endswith("/artifacts"):
            return subprocess.CompletedProcess(
                argv, 0, json.dumps({"artifacts": [{"name": "release-linux_x86_64", "id": 42}]}), ""
            )
        if argv[1] == "api" and argv[2].endswith("/zip"):
            if not self.api_ok:
                return subprocess.CompletedProcess(argv, 1, b"", b"blocked")
            tmp = io.BytesIO()
            inner = Path(kw.get("cwd") or ".")
            del inner
            with zipfile.ZipFile(tmp, "w") as zf:
                buf = io.BytesIO()
                with tarfile.open(fileobj=buf, mode="w:gz") as tf:
                    data = b"#!/bin/sh\necho v\n"
                    info = tarfile.TarInfo("pkg/ycashd")
                    info.size = len(data)
                    tf.addfile(info, io.BytesIO(data))
                zf.writestr("pkg.tar.gz", buf.getvalue())
            return subprocess.CompletedProcess(argv, 0, tmp.getvalue(), b"")
        raise AssertionError(argv)


def test_fetch_ci_binary_installs_and_caches(work: Path):
    gh = FakeGh()
    got = b.fetch_ci_binary(b.KNOWN_CI_RUN, "release-linux_x86_64", run=gh, which=lambda x: "/usr/bin/gh")
    assert isinstance(got, b.BinaryInfo) and got.origin == "ci"
    assert got.ycashd.parent.name == "ci-94bafa4fde5b" and os.access(got.ycashd, os.X_OK)
    assert got.ycash_cli is not None and got.commit.startswith("94bafa4")
    assert b.binary_version(got.ycashd).commit == "94bafa4"
    n = len(gh.calls)
    assert b.fetch_ci_binary(b.KNOWN_CI_RUN, run=gh, which=lambda x: "gh").origin == "cache"
    assert len(gh.calls) == n + 1  # only `run view`


def test_fetch_ci_binary_api_fallback(work: Path):
    got = b.fetch_ci_binary(
        "1", "release-linux_x86_64", run=FakeGh(download_ok=False, api_ok=True), which=lambda x: "gh"
    )
    assert isinstance(got, b.BinaryInfo) and got.ycash_cli is None


def test_fetch_ci_binary_skips_cleanly(work: Path):
    assert "GitHub CLI" in b.fetch_ci_binary("1", which=lambda x: None).reason
    sk = b.fetch_ci_binary("1", "release-linux_x86_64", run=FakeGh(download_ok=False), which=lambda x: "gh")
    assert isinstance(sk, Skipped) and "blob storage blocked" in sk.reason and "artifacts/42/zip" in sk.reason


def test_default_artifact():
    assert b.default_artifact("Linux", "x86_64") == "release-linux_x86_64"
    assert b.default_artifact("Darwin", "arm64") == "release-macos_aarch64"
    assert b.default_artifact("Darwin", "x86_64") == "release-macos_x86_64"
    assert b.default_artifact("Linux", "aarch64") == "release-linux_aarch64"

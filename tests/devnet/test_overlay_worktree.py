"""Overlay split, RegtestParams() patch generation, and the throwaway worktree."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import ycash6_path
from ybcal.devnet import worktree as wtmod
from ybcal.devnet.overlay import (
    RUNTIME_FLAGS,
    OverlayError,
    apply_patch,
    build_key,
    load_overlay,
    make_patch,
    overlay_hash,
    patch_source,
    read_params_cpp,
    runtime_args,
    split,
)
from ybcal.params.extract import extract_sources, git_show
from ybcal.params.paramset import ParamSet, mainnet, regtest
from ybcal.params.registry import PARAMS_CPP, PARAMS_H, PINNED_COMMIT
from ybcal.params.scaling import scale_to_regtest

REPO = ycash6_path()
needs_repo = pytest.mark.skipif(REPO is None, reason="no ycash6 clone (set YBCAL_YCASH6)")


@pytest.fixture
def work(monkeypatch: pytest.MonkeyPatch) -> Path:
    """A work dir under the project's .work (worktrees may only live under yb-calibration)."""
    d = wtmod.PROJECT_ROOT / ".work" / f"pytest-{os.getpid()}"
    monkeypatch.setenv("YBCAL_WORK", str(d))
    yield d
    shutil.rmtree(d, ignore_errors=True)
    if REPO is not None:
        subprocess.run(["git", "-C", str(REPO), "worktree", "prune"], capture_output=True)


# --- split ----------------------------------------------------------------------------------------


def test_split_shipped_column_is_stock():
    sp = split(regtest())
    assert sp.compiled == {} and not sp.needs_build
    assert sp.node_args() == [
        "-yellowbackstartheight=1",
        "-yellowbacksigmaref=0",
        "-yellowbacksupplycapbps=0",
        "-yellowbackenforceuntil=0",
        "-yellowbackattestarmmin=3",
        "-yellowbackbundlecarrier=scriptsig",
    ]
    assert sp.conf_lines()[0] == "yellowbackstartheight=1"
    assert build_key(sp, "7702d22") == "stock-7702d22"


def test_runtime_only_change_needs_no_build():
    ov = regtest().replace(
        sigmaRefBps=12_000,
        supplyCapBps=1500,
        attestArmMin=0,
        bundleCarrier="EITHER",
        startHeight=10,
        enforceUntilHeight=500,
    )
    sp = split(ov)
    assert sp.compiled == {}
    assert set(sp.runtime) == set(RUNTIME_FLAGS)
    assert "-yellowbackbundlecarrier=either" in sp.node_args()
    assert "-yellowbackattestarmmin=0" in sp.node_args()
    # flags change the run key but not the binary key
    assert build_key(sp) == build_key(split(regtest()))
    assert overlay_hash(ov) != overlay_hash(regtest())


def test_compiled_change_and_derived_follow():
    sp = split(regtest().replace(pFastWindow=10, deviationBps=1500))
    assert sp.compiled == {"pFastWindow": 10, "pFastMinFill": 5, "deviationBps": 1500}
    assert sp.needs_build and build_key(sp).startswith("ov-")


def test_split_refusals():
    with pytest.raises(OverlayError, match="not regtest"):
        split(mainnet())
    with pytest.raises(OverlayError, match="cannot be patched"):
        split(regtest().replace(REF_WINDOW=41))
    with pytest.raises(OverlayError, match="violates"):
        split(regtest().replace(abandonBlocks=10))  # W21: < grace
    with pytest.raises(OverlayError, match="outside"):
        runtime_args({**{k: regtest()[k] for k in RUNTIME_FLAGS}, "supplyCapBps": 10_001})


def test_load_overlay_forms(tmp_path: Path):
    assert load_overlay({"grace": 30})["grace"] == 30
    assert load_overlay({"format": "ybcal-overlay/1", "values": {"pFastWindow": 10}})["pFastMinFill"] == 5
    full = regtest().replace(grace=30)
    f = tmp_path / "o.json"
    f.write_text(full.to_json())
    assert load_overlay(f) == full
    s = scale_to_regtest(mainnet())
    f.write_text(json.dumps(s.to_dict()))
    assert load_overlay(f) == s.params
    with pytest.raises(OverlayError, match="unknown parameter"):
        load_overlay({"nope": 1})
    with pytest.raises(OverlayError, match="format"):
        load_overlay({"format": "x/1", "values": {}})


# --- patch ----------------------------------------------------------------------------------------

SYNTH = """namespace yellowback {
Params RegtestParams(int startHeight, int sigmaRefBps, int supplyCapBps, int enforceUntil,
                     int attestArmMin, BundleCarrier bundleCarrier)
{
    Params r;
    r.pFastWindow = 8;  r.pFastMinFill = 4;
    r.bondMin             = 10 * COIN;
    // the six flags
    r.startHeight        = startHeight;
    return r;
}
}
"""


def test_patch_source_replaces_and_inserts():
    out = patch_source(
        SYNTH,
        {
            "pFastWindow": 10,
            "pFastMinFill": 5,
            "bondMin": 20 * 10**8,
            "deviationBps": 1500,
            "attestRequired": False,
        },
        tag="k",
    )
    assert "r.pFastWindow = 10;  r.pFastMinFill = 5;" in out
    assert "r.bondMin             = 20 * COIN;" in out
    assert "    r.deviationBps = 1500;   // ybcal overlay k\n    r.attestRequired = false;" in out
    assert out.index("deviationBps") < out.index("// the six flags")
    assert make_patch(SYNTH, {}) == ""
    patch = make_patch(SYNTH, {"pFastWindow": 10})
    assert patch.startswith(f"--- a/{PARAMS_CPP}\n+++ b/{PARAMS_CPP}\n@@")


@needs_repo
def test_patch_at_pin_reextracts_to_the_overlay():
    """The patched params.cpp, parsed by WP-0's extractor, yields exactly the overlay's regtest column."""
    s = scale_to_regtest(mainnet())
    sp = split(s.params)
    src = read_params_cpp(REPO, PINNED_COMMIT)
    header = git_show(REPO, PINNED_COMMIT, PARAMS_H)
    patched = patch_source(src, sp.compiled)
    ex = extract_sources(header, patched, regtest_flags=sp.runtime)
    assert ParamSet.from_extracted(ex, "regtest") == s.params
    # only RegtestParams() changed
    lo = src.index("Params RegtestParams(")
    assert patched[:lo] == src[:lo]
    assert extract_sources(header, patched).networks["main"] == extract_sources(header, src).networks["main"]


@needs_repo
def test_git_apply_check_in_temp_worktree(work: Path):
    s = scale_to_regtest(mainnet())
    sp = split(s.params.replace(deviationBps=1500))
    patch = make_patch(read_params_cpp(REPO, PINNED_COMMIT), sp.compiled, tag=build_key(sp))
    assert patch
    with wtmod.temp_worktree(REPO, PINNED_COMMIT) as wt:
        assert wt.path.is_dir() and wtmod._inside(wt.path, work)
        apply_patch(wt.path, patch, check_only=True)
        apply_patch(wt.path, patch)
        assert "r.deviationBps = 1500;" in (wt.path / PARAMS_CPP).read_text()
        # the clone's own checkout is untouched
        assert "r.deviationBps = 1500;" not in read_params_cpp(REPO, PINNED_COMMIT)
        path = wt.path
    assert not path.exists()
    assert path.resolve() not in wtmod.list_worktrees(REPO)


# --- worktree -------------------------------------------------------------------------------------


@needs_repo
def test_worktree_refuses_missing_commit(work: Path):
    with pytest.raises(wtmod.WorktreeError, match="not present"):
        wtmod.create_worktree(REPO, "0" * 40)
    with pytest.raises(wtmod.WorktreeError, match="not present"):
        wtmod.resolve_commit(REPO, "no-such-ref-xyz")


@needs_repo
def test_worktree_refuses_path_outside_work_dir(work: Path, tmp_path: Path):
    with pytest.raises(wtmod.WorktreeError, match="outside the work dir"):
        wtmod.create_worktree(REPO, PINNED_COMMIT, path=tmp_path / "x")


@needs_repo
def test_worktree_create_reuse_remove(work: Path):
    branches_before = subprocess.run(
        ["git", "-C", str(REPO), "branch", "--list"], capture_output=True, text=True
    ).stdout
    head_before = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout
    wt = wtmod.create_worktree(REPO, PINNED_COMMIT)
    try:
        assert wt.path == wtmod.worktree_path(wt.commit, work)
        assert wt.path.name == f"ycash6-{wt.commit[:12]}"
        again = wtmod.create_worktree(REPO, PINNED_COMMIT)
        assert again == wt
        detached = subprocess.run(
            ["git", "-C", str(wt.path), "symbolic-ref", "-q", "HEAD"], capture_output=True
        )
        assert detached.returncode != 0  # detached HEAD, no branch
    finally:
        wtmod.remove_worktree(wt)
    assert not wt.path.exists()
    assert (
        subprocess.run(["git", "-C", str(REPO), "branch", "--list"], capture_output=True, text=True).stdout
        == branches_before
    )
    assert (
        subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
        == head_before
    )


def test_ycash6_repo_resolution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("YBCAL_YCASH6", raising=False)
    assert wtmod.ycash6_repo(tmp_path) is None
    (tmp_path / ".git").mkdir()
    assert wtmod.ycash6_repo(tmp_path) == tmp_path.resolve()
    monkeypatch.setenv("YBCAL_YCASH6", str(tmp_path))
    assert wtmod.ycash6_repo(None) == tmp_path.resolve()

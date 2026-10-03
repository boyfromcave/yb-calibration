"""Live devnet tests: need a real ycashd (``YBCAL_YCASHD``). Skipped everywhere else.

Run on the owner's machine with ``YBCAL_YCASHD=/path/to/ycashd pytest -m devnet``. Each test takes
a few minutes (~12 s RPC warm-up per node, ~2 s per block).
"""

from __future__ import annotations

import os

import pytest

from ybcal.devnet.overlay import split
from ybcal.devnet.runner import RunResult, run_devnet
from ybcal.devnet.scenarios import ReplayStep, Schedule, bootstrap_steps
from ybcal.devnet.worktree import ycash6_repo
from ybcal.params.paramset import regtest

pytestmark = [
    pytest.mark.devnet,
    pytest.mark.skipif(not os.environ.get("YBCAL_YCASHD"), reason="needs a ycashd binary (set YBCAL_YCASHD)"),
]


def test_live_short_replay_and_scrape(tmp_path):
    ps = regtest()
    sched = Schedule(
        "live-short", (*bootstrap_steps(ps), ReplayStep(48_000_000, 20), ReplayStep(30_000_000, 20))
    )
    res = run_devnet(
        sched,
        split(ps),
        repo=ycash6_repo(),
        run_dir=tmp_path / "run",
        portseed=131,
        allow_version_skew=bool(os.environ.get("YBCAL_ALLOW_SKEW")),
    )
    assert isinstance(res, RunResult), res
    assert res.status == "ok", res.message
    assert res.scrape is not None and res.scrape.heights[1] == sched.total_blocks
    last = res.scrape.history[-1]
    assert last["activationStatus"] == "active" and last["pFast"] is not None

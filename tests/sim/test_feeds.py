"""The shipped agents' aggregation (ybcal.sim.feeds, D-RD-ATT-1) by hand and against the upstream agent."""

from __future__ import annotations

import importlib.util
import subprocess
import sys

import numpy as np
import pytest

from tests.conftest import ycash6_path
from ybcal.data.loaders import SpreadsLog
from ybcal.sim import feeds as F

UPSTREAM_COMMIT = "7702d22"


def _q(*cols):
    """(1, sources, n) from per-source lists."""
    return np.asarray([list(cols)], dtype=np.int64)


def test_median_of_three_and_even_count():
    q = _q([100_000], [101_000], [130_000])  # 130k is > 10 % from the median 101k: dropped
    assert F.agent_quotes(q, twap_blocks=1, min_sources=2)[0, 0] == 100_500  # mean of the middle two
    assert F.agent_quotes(q, twap_blocks=1, min_sources=3)[0, 0] == 0  # 2 kept < 3: fails closed
    q = _q([100_000], [101_000], [109_000])
    assert F.agent_quotes(q, twap_blocks=1, min_sources=3)[0, 0] == 101_000


def test_silent_source_dropped_and_twap():
    # source 2 absent this block (silence), sources 0/1 average over the window
    q = _q([100_000, 103_000, 102_000], [100_000, 100_000, 100_000], [100_000, 100_000, 0])
    out = F.agent_quotes(q, twap_blocks=3, min_sources=2, min_venues=2)
    assert out[0, 2] == 100_833  # median(avg = 101,666.7, 100,000)
    assert F.agent_quotes(q, twap_blocks=3, min_sources=3)[0, 2] == 0


def test_min_venues():
    q = _q([100], [101], [102])
    assert F.agent_quotes(q, twap_blocks=1, min_sources=2, min_venues=2, venues=("a", "a", "a"))[0, 0] == 0
    assert F.agent_quotes(q, twap_blocks=1, min_sources=2, min_venues=2, venues=("a", "a", "b"))[0, 0] == 101


def test_venue_replay_reproduces_log_deviations():
    ts = np.arange(20) * 300
    p = np.array([[100_000, 102_000, 0]] * 20, dtype=np.int64)
    log = SpreadsLog(ts, p)
    rp = F.VenueReplay.from_log(log)
    true = np.full((2, 40), 200_000, dtype=np.int64)
    q = rp.generate(true, np.random.default_rng(0), step_seconds=75)
    assert q.shape == (2, 3, 40)
    assert (q[:, 0] == 200_000).all() and (q[:, 1] == 204_000).all() and (q[:, 2] == 0).all()
    assert rp.pair_p95()[("coingecko", "safetrade")] == pytest.approx(200.0)


def test_unchanged_share():
    assert F.unchanged_share(np.array([1, 1, 2, 2, 0, 3])) == pytest.approx(2 / 3)


@pytest.fixture(scope="module")
def upstream_price():
    repo = ycash6_path()
    if repo is None:
        pytest.skip("no ycash6 clone (set YBCAL_YCASH6)")
    src = "contrib/yellowback/yellowback_price.py"
    try:
        body = subprocess.run(["git", "-C", str(repo), "show", f"{UPSTREAM_COMMIT}:{src}"],
                              check=True, capture_output=True).stdout
    except subprocess.CalledProcessError:
        pytest.skip(f"ycash6 clone lacks {UPSTREAM_COMMIT}:{src}")
    import tempfile
    from pathlib import Path

    d = Path(tempfile.mkdtemp())
    (d / "yellowback_price.py").write_bytes(body)
    spec = importlib.util.spec_from_file_location("_upstream_yellowback_price", d / "yellowback_price.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_parity_with_shipped_pool_agent(upstream_price):
    """PriceFeed.aggregate (yellowback_price.py) on the same per-source averages, many random cases."""
    rng = np.random.default_rng(7)
    venues = ("coingecko", "safe_trade", "nonkyc_io")
    for _ in range(400):
        avgs = (100_000 * np.exp(rng.normal(0, 0.08, 3))).round()
        live = rng.random(3) > 0.2
        ms = int(rng.integers(1, 4))
        feed = upstream_price.PriceFeed.__new__(upstream_price.PriceFeed)
        feed.mock_file = None
        feed.settings = {"min_sources": ms, "min_venues": min(2, ms), "outlier_bps": 1000}
        feed.sources = [{"name": n, "venue": v} for n, v in zip(("cg", "st", "nk"), venues, strict=True)]
        names = ("cg", "st", "nk")
        triples = [(n, v, float(a)) for n, v, a, ok in zip(names, venues, avgs, live, strict=True) if ok]
        feed._live_twaps = lambda now, t=triples: t
        want = feed.aggregate()
        q = np.where(live, avgs, 0).astype(np.int64).reshape(1, 3, 1)
        got = F.agent_quotes(q, twap_blocks=1, min_sources=ms, min_venues=min(2, ms), venues=venues)[0, 0]
        assert got == (0 if want is None else want[0])

"""Real-history replay (D-RD-ORA-3): eras, events, σ̂ / HALT-3 metrics on a hand-built hourly path."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import numpy as np
import pytest

from ybcal.config import Policy
from ybcal.params.paramset import mainnet
from ybcal.studies import oracle_replay as R
from ybcal.studies.base import Budget, Env
from ybcal.types import PricePath
from ybcal.units import BLOCKS_PER_DAY, BLOCKS_PER_HOUR


def _hourly(prices, t0=datetime(2022, 12, 1, tzinfo=UTC)) -> PricePath:
    return PricePath(t0, "hour", np.asarray(prices, dtype=np.int64)[None, :], "real", {"source_file": "t"})


def _env(pp: PricePath, seed: int = 3) -> Env:
    return Env(Policy(), Budget.named("quick"), seed=seed, data={"price": pp})


def _calm(n_hours: int, sigma_h: float = 0.01, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return np.rint(400_000 * np.exp(np.cumsum(rng.normal(0, sigma_h, n_hours)))).astype(np.int64)


def test_era_slice_and_blocks():
    pp = _hourly(_calm(24 * 120))  # 2022-12-01 … 2023-03-31
    a, b = R.era_slice(pp, "2023")
    assert a == 31 * 24 and b == pp.n_steps
    assert R.era_slice(pp, "2021-22") == (0, 31 * 24)
    a, b = R.era_slice(pp, "last365")
    assert (a, b) == (0, pp.n_steps)


def test_wick_and_fall_events():
    p = np.full(24 * 30, 400_000, dtype=np.int64)
    p[100] = 200_000  # one-hour −50 % print, back next hour
    p[400:] = 200_000  # a real −50 % fall that stays
    pp = _hourly(p)
    assert R.wick_events(pp) == [100]
    falls = R.fall_events(pp, 0.30, 24)
    assert len(falls) == 1 and 395 <= falls[0][0] <= 403 and falls[0][1] >= 400  # the print is ignored
    assert R.merge_falls(falls, R.fall_events(pp, 0.50, 7 * 24)) == falls[:1] or len(
        R.merge_falls(falls, R.fall_events(pp, 0.45, 7 * 24))
    ) == 1
    d = R.despiked(pp)
    assert d.prices[0, 100] == 400_000 and d.prices[0, 500] == 200_000


def test_replay_calm_sigma_matches_the_kernel_scale():
    """On a calm 1 %/h path the replayed σ̂ is close to the path's own hourly vol (≈ 94 %/yr) — the
    node's simple-return, no-demeaning estimator over pFast sampled every volStep (state.cpp:1212)."""
    pp = _hourly(_calm(24 * 60, 0.01))
    env = _env(pp)
    R.clear_caches()
    rep = R.replay(env, "full")
    assert rep is not None and rep.n == pp.n_steps * BLOCKS_PER_HOUR
    m = R.sigma_metrics(rep, mainnet())
    true_h = m["replay_true_vol_step_bps_full"]
    assert 0.5 * true_h < m["replay_sigma_hat_p50_full"] < 1.2 * true_h
    assert m["replay_cap_undefined_share_full"] == 0.0  # policy pools, no outages beyond the policy's
    h = R.halt_metrics(rep, mainnet(), pp)
    assert h["replay_halt3_h_per_year_full"] == pytest.approx(0.0, abs=1.0)
    assert h["replay_falls_full"] == 0.0 and math.isnan(h["replay_fall_recall_full"])


def test_replay_detects_a_fast_real_fall_and_eras_slice_one_replay():
    p = _calm(24 * 60, 0.002)
    p[24 * 45 :] = (p[24 * 45 :] * 0.4).astype(np.int64)  # −60 % in one hour at day 45 (2023-01-15)
    pp = _hourly(p)
    env = _env(pp)
    R.clear_caches()
    rep = R.replay(env, "full")
    base = mainnet()
    h = R.halt_metrics(rep, base, pp)
    assert h["replay_falls_full"] == 1.0 and h["replay_fall_recall_full"] == 1.0
    assert h["replay_halt3_false_h_per_year_full"] == pytest.approx(0.0, abs=1e-9)
    h23 = R.halt_metrics(rep, base, pp, era="2023")
    assert h23["replay_falls_2023"] == 1.0
    lo, hi = R.era_blocks(rep, "2023", pp)
    assert lo == 31 * 24 * BLOCKS_PER_HOUR and hi == rep.n
    # σ̂ spikes after the fall: the multiplier reaches the cap at a 100 % reference
    s = R.sigma_metrics(rep, base, era="2023", price=pp)
    assert s["replay_mult_p99_2023"] == pytest.approx(3.0)
    assert R.halt_evidence(env, base)  # bundles are non-empty with a real price
    assert R.sigma_evidence(env, base)


def test_replay_needs_hourly_real_price():
    env = Env(Policy(), Budget.named("quick"), seed=1, data={})
    assert R.replay(env) is None and R.sigma_evidence(env, mainnet()) == {}
    short = _hourly(_calm(24 * 3))
    assert R.replay(_env(short)) is None  # shorter than the warm-up
    assert BLOCKS_PER_DAY > 0

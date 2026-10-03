"""G5 analytic helpers vs Monte Carlo."""

from __future__ import annotations

import numpy as np
from scipy.stats import binom

from ybcal.model import vkernels as V
from ybcal.sim import activation as act

W, FLOOR, RESUME = 64, 32, 39


def test_count_tail_binomial_and_poisson_binomial():
    rng = np.random.default_rng(0)
    p = 0.55
    sims = (rng.random((200_000, W)) < p).sum(axis=1)
    assert abs((sims < FLOOR).mean() - float(act.p_count_below(FLOOR, p, W))) < 0.004
    # Poisson-binomial == binomial for equal probabilities, and == MC for drifting ones
    exact = float(act.p_count_below(FLOOR, p, W))
    assert abs(act.p_count_below_poisson_binomial(FLOOR, [p] * W) - exact) < 1e-12
    probs = np.linspace(0.8, 0.3, W)
    sims = (rng.random((200_000, W)) < probs[None, :]).sum(axis=1)
    assert abs((sims < FLOOR).mean() - act.p_count_below_poisson_binomial(FLOOR, probs)) < 0.004
    pmf = act.poisson_binomial_pmf(probs)
    assert abs(pmf.sum() - 1) < 1e-12 and abs((np.arange(W + 1) * pmf).sum() - probs.sum()) < 1e-9


def test_downcrossing_rate_is_the_exact_expectation():
    rng = np.random.default_rng(1)
    p, n, paths = 0.6, 20_000, 64
    s = rng.random((paths, n + W)) < p
    c = V.signal_counts(s, W)[:, W - 1:]
    down = ((c[:, :-1] >= FLOOR) & (c[:, 1:] < FLOOR)).sum()
    expected = act.downcrossing_rate(FLOOR, p, W) * paths * n
    assert abs(down - expected) < 4 * np.sqrt(expected) + 5


def test_false_halt_bounds_bracket_the_simulation():
    rng = np.random.default_rng(2)
    p = 0.62
    est = act.false_halt_rate(p, W, FLOOR, RESUME, blocks_per_year=420_480, simulate_blocks=50_000,
                              n_paths=64, rng=rng)
    sim = est.simulated
    assert est.halted_fraction_lower <= sim["halted_fraction"] * 1.05 + 1e-4
    assert sim["halted_fraction"] <= est.halted_fraction_upper * 1.05 + 1e-4
    assert sim["episodes_per_year"] <= est.episodes_per_year_upper * 1.1
    assert sim["episodes_per_year"] > 0.3 * est.episodes_per_year_upper
    fl = act.flapping_rate(p, W, FLOOR, RESUME, simulate_blocks=20_000, n_paths=32, rng=rng)
    assert fl["cycles_per_year_simulated"] <= fl["cycles_per_year_upper"] * 1.15
    # mainnet thresholds at the policy's expected share: practically never
    m = act.false_halt_rate(0.80, 2016, 1008, 1210)
    assert m.episodes_per_year_upper < 1e-30 and m.hours_per_year_upper < 1e-20


def test_detection_delay_distribution_vs_fluid_approximation():
    rng = np.random.default_rng(3)
    d = act.detection_delay(0.8, 0.3, W, FLOOR, n_paths=4000, rng=rng)
    approx = act.detection_delay_approx(0.8, 0.3, W, FLOOR)
    assert (d > 0).all()
    assert abs(np.median(d) - approx) <= 0.15 * approx + 2
    d2 = act.detection_delay(0.8, 0.6, W, FLOOR, n_paths=500, rng=rng)       # never below 32 on average
    assert act.detection_delay_approx(0.8, 0.6, W, FLOOR) is None and (d2 == -1).mean() > 0.5
    assert act.detection_delay_approx(0.4, 0.3, W, FLOOR) == 0


def test_valve_trip_probability_vs_random_walk():
    rng = np.random.default_rng(4)
    q, v = 0.3, 4
    paths, steps = 20_000, 400
    d = 1 + np.cumsum(np.where(rng.random((paths, steps)) < q, 1, -1), axis=1)
    reached = (d >= v).any(axis=1)
    assert abs(reached.mean() - act.valve_trip_probability(q, v)) < 0.01
    assert act.valve_trip_probability(0.6, 6) == 1.0 and act.valve_trip_probability(0.0, 6) == 0.0
    assert act.natural_fork_trip_rate(0.005, 6) < 1e-6


def test_simulate_halts_with_share_drift_matches_poisson_binomial_mean():
    rng = np.random.default_rng(5)
    n = 4000
    share = np.full(n, 0.8)
    share[2000:] = 0.3
    r = act.simulate_halts(share, W, FLOOR, RESUME, n, 32, rng=rng)
    assert r["halted_fraction"] > 0.4
    assert binom.sf(FLOOR - 1, W, 0.3) < 0.01

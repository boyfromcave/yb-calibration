"""G8 analytic helpers vs Monte Carlo (and vs the simulator itself)."""

from __future__ import annotations

import numpy as np

from ybcal.model import kernels as K
from ybcal.params.paramset import regtest
from ybcal.sim import attest as att


def test_liveness_binomial_and_beta_binomial_vs_mc():
    rng = np.random.default_rng(0)
    m, k, u = 4, 2, 0.9
    up = rng.random((200_000, m + k)) < u
    assert abs((up.sum(axis=1) >= m).mean() - att.liveness_probability(m, k, u)) < 0.003
    # correlated outages: per-trial availability ~ Beta with mean u and ICC rho
    rho = 0.2
    a, b = u * (1 - rho) / rho, (1 - u) * (1 - rho) / rho
    pt = rng.beta(a, b, 200_000)
    up = rng.random((200_000, m + k)) < pt[:, None]
    assert abs((up.sum(axis=1) >= m).mean() - att.liveness_probability(m, k, u, rho)) < 0.003
    assert att.liveness_probability(m, k, u, rho) < att.liveness_probability(m, k, u)
    assert att.liveness_probability(m, 0, 1.0) == 1.0 and att.liveness_probability(2, 1, 0.0) == 0.0


def test_liveness_matches_the_simulator_bundle_success_rate():
    """iid per-block availability u with k-block signing and maxAge = 2k: each selected attestor is
    fresh with 1 − (1 − u)², and the simulator's bundle success rate equals the binomial tail."""
    P = regtest().replace(dormancyMinBundles=100)   # nSlots 5, m 2, k 1, interval 4, maxAge 8; no dormancy
    u = 0.6
    paths, n = 6, 3000
    roster = [{"bond_zat": 10**9, "register_height": 2 + i} for i in range(6)]
    res = att.simulate(P, {"attest": {"roster": roster, "true_price": np.full((paths, n), 50_000),
                                      "uptime": u, "demand_rate": 0.5, "seed": 3, "height0": 1,
                                      "start_height": 1}})
    d = res.demands[res.demands["armed"] & (res.demands["height"] > 200)]
    f = att.fresh_probability(u, int(P["attestInterval"]), int(P["attestMaxAge"]))
    want = att.liveness_probability(int(P["mSelect"]), int(P["kSlack"]), f)
    got = d["success"].mean()
    assert abs(got - want) < 4 * np.sqrt(want * (1 - want) / len(d)) + 0.005, (got, want, len(d))


def test_fresh_probability_markov_vs_mc():
    rng = np.random.default_rng(1)
    u, L, k, age = 0.9, 30, 10, 20
    x = att.markov_online(3_000_000, u, L, rng)
    # signing heights c ≡ 0 mod k; fresh at R iff online at one of the two signing heights ≤ R in (R − 20, R]
    R = np.arange(100, 3_000_000, 37)
    last = (R // k) * k
    fresh = x[last] | x[last - k]
    assert abs(fresh.mean() - att.fresh_probability(u, k, age, L)) < 0.005
    assert abs(att.fresh_probability(u, k, age) - (1 - 0.1 ** 2)) < 1e-12


def test_false_dormancy_iid_and_markov_vs_mc():
    rng = np.random.default_rng(2)
    N, mb, lam, sel = 16, 2, 0.5, 0.6
    r = (1 - np.exp(-lam)) * sel
    for u, L in ((0.5, None), (0.7, None), (0.8, 8.0), (0.9, 30.0)):
        trials = 200_000
        selected = rng.random((trials, N)) < r
        if L is None:
            up = rng.random((trials, N)) < u
        else:
            up = np.stack([att.markov_online(N, u, L, rng) for _ in range(trials // 20)])
            selected = selected[:len(up)]
        dormant = (selected.sum(axis=1) >= mb) & ~(selected & up).any(axis=1)
        want = att.false_dormancy_probability(u, N, mb, lam, sel, mean_outage_blocks=L)
        tol = 4 * np.sqrt(want * (1 - want) / len(up)) + 1e-4
        assert abs(dormant.mean() - want) < tol, (u, L, dormant.mean(), want)
    # mainnet-scale numbers are tiny for honest attestors at the policy uptime
    p = att.false_dormancy_probability(0.95, 16_128, 20, 1 / 48, 6 / 9, mean_outage_blocks=48)
    assert p < 1e-30
    assert att.false_dormancy_per_year(1e-6, 48) <= 1


def test_capture_thresholds_are_exact_at_the_weighted_quantile():
    q = 3_333
    sh = att.capture_threshold_shares(q)
    assert sh["a_mint_down"] == 0.3333 and abs(sh["a_claim_up"] - 0.3333) < 1e-12
    # adversary weight exactly at ceil(q·T/1e4) controls aMint (low price); one less does not
    T = 30_000
    thr = -((-q * T) // 10_000)
    for adv, moved in ((thr, True), (thr - 1, False)):
        entries = [(900, adv), (1_000, T - adv)]
        assert (K.weighted_quantile(entries, q) == 900) is moved


def test_capture_probability_and_share_needed():
    honest = [10**9 * 64] * 7
    p0 = att.capture_probability([*honest, 1], [False] * 7 + [True], m_select=4, k_slack=2, q_low_bps=3_333,
                                 q_high_bps=6_667, n_draws=300)
    p1 = att.capture_probability([*honest, 10**9 * 640], [False] * 7 + [True], m_select=4, k_slack=2,
                                 q_low_bps=3_333, q_high_bps=6_667, n_draws=300)
    assert p0 == 0 and p1 > 0.9
    s = att.capture_share_needed(3_333, honest, m_select=4, k_slack=2, n_draws=200, grid=20)
    assert 0.05 < s < 0.6
    assert att.capture_share_needed(3_333) == 0.3333


def test_griefing_cost():
    prices = np.array([[500_000, 250_000, 400_000], [1_000_000, 1_000_000, 2_000_000]])
    g = att.griefing_cost_usd(20_000 * 10**8, prices, seats=2, opportunity_apr=0.05)
    assert list(g["capital_usd"]) == [20_000.0, 40_000.0]
    assert list(g["min_capital_usd"]) == [10_000.0, 40_000.0]
    assert list(g["opportunity_cost_usd"]) == [1_000.0, 2_000.0]

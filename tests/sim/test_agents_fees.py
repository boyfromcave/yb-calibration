"""Personas (determinism, adversarial refHeight, absence model) and fees (FEE-1/2, AFEE-1, REG-4, FEE-W)."""

from __future__ import annotations

import numpy as np
import pytest

from ybcal.model import kernels as K
from ybcal.params.paramset import mainnet, regtest
from ybcal.sim import agents as AG
from ybcal.sim import engine as E
from ybcal.sim import fees as F
from ybcal.sim import vaults as VB

PS = mainnet()


# ---------------------------------------------------------------------------------------------------
# agents


def test_attempts_deterministic_and_in_bounds():
    ag = AG.AgentsConfig(
        minter=AG.MinterConfig(mints_per_day=20.0, buffer_bps_hi=500),
        owner=AG.OwnerConfig(lost_key_prob=0.1, defector_share=0.2),
    )
    a = AG.sample_mint_attempts(np.random.default_rng(5), PS, ag, 20_000, 48)
    b = AG.sample_mint_attempts(np.random.default_rng(5), PS, ag, 20_000, 48)
    c = AG.sample_mint_attempts(np.random.default_rng(6), PS, ag, 20_000, 48)
    for f in a.__dataclass_fields__:
        assert np.array_equal(getattr(a, f), getattr(b, f))
    assert len(a) != len(c) or not np.array_equal(a.cents, c.cents)
    assert np.all(np.diff(a.step) >= 0) and a.step.min() >= 1
    assert a.cents.min() >= PS["minMint"] and a.cents.max() <= PS["maxMint"]
    lo = np.array([PS[f"classMin[{i}]"] for i in range(3)])[a.term_class]
    hi = np.array([PS[f"classMax[{i}]"] for i in range(3)])[a.term_class]
    assert np.all((a.lock_blocks >= lo) & (a.lock_blocks <= hi))
    assert np.all((a.buffer_bps >= 0) & (a.buffer_bps <= 500))
    # arrival rate: 20/day over 20,000 hours ≈ 16,667 attempts
    assert abs(len(a) - 20 * 20_000 / 24) < 5 * np.sqrt(20 * 20_000 / 24)
    assert np.all(a.owner_delay_blocks[a.owner_kind == AG.LOST] == AG.NEVER)
    assert abs((a.owner_kind == AG.DEFECTOR).mean() - 0.2) < 0.02


@pytest.mark.parametrize("dist", ["uniform", "short-heavy", "long-heavy"])
def test_term_grid_and_sampling_agree(dist):
    rng = np.random.default_rng(1)
    s = AG.sample_lock_blocks(rng, PS, np.full(40_000, 1), dist)  # type: ignore[arg-type]
    g, w = AG.term_grid(PS, 1, 200, dist)  # type: ignore[arg-type]
    assert abs(np.median(s) - np.median(g)) / np.median(g) < 0.02
    assert w.sum() == pytest.approx(1.0)


def test_adversarial_ref_scalar():
    assert AG.adversarial_ref(np.array([5, 9, 9, 3]), np.array([True, True, True, True])) == 1
    assert AG.adversarial_ref(np.array([5, 9, 9, 3]), np.array([True, False, True, True])) == 2
    assert AG.adversarial_ref(np.array([5, 9]), np.array([False, False])) == -1
    order = AG.ref_preference(np.array([[5, 9, 9, 3]]), np.array([[True, True, False, True]]))
    assert order.tolist() == [[1, 0, 3, 2]]


def test_book_picks_the_max_pmint_snapshot_in_the_window():
    """Fact 1.5-5: with pMint varying inside the 40-block window, the adversarial minter's R is the
    window's highest admissible pMint; the wallet persona uses tip − REF_LAG."""
    start = 5
    n = 400
    ps = regtest().replace(startHeight=start, sigmaRefBps=0)
    rng = np.random.default_rng(3)
    tp = (2_000_000 * np.exp(np.cumsum(rng.normal(0, 0.03, n)))).astype(np.int64)
    inp = E.BlockInputs.perfect(tp, start)
    att = AG.MintAttempts.from_rows([(t, 20_000, 0, 60, 0, 0, 0) for t in range(200, 380, 7)])
    for choice in ("adversarial", "wallet"):
        hook = VB.VaultHook(attempts=[att], agents=AG.AgentsConfig(minter=AG.MinterConfig(ref_choice=choice)))
        s = E.simulate_blocks(ps, inp, hooks=(hook,))
        v = s.extras["vaults"].vaults
        for i in np.nonzero(v["outcome"] == VB.O_ACTIVE)[0]:
            t = int(v["step"][i])
            win = np.arange(t - 40, t)
            ok = (s.halt_mask[0, win] == 0) & (s.x_mint[0, win] > 0)
            if choice == "adversarial":
                assert v["p_mint"][i] == s.x_mint[0, win][ok].max()
                assert ok[v["ref_step"][i] - (t - 40)]
            else:
                assert v["ref_step"][i] == t - 1 - int(ps["DEFAULT_REF_LAG"])


def test_owner_absence_simulated_matches_analytic():
    cfg = AG.OwnerConfig(absence_rate_per_year=6.0, absence_median_days=7.0, absence_sigma=1.0)
    d = AG.owner_return_delay_blocks(np.random.default_rng(2), 400_000, cfg)
    for g_days in (1, 7, 30, 60):
        g = g_days * 1152
        sim = (d > g).mean()
        ana = AG.p_owner_miss(g, cfg)
        assert sim == pytest.approx(ana, rel=0.12, abs=4e-4), (g_days, sim, ana)
    assert AG.p_owner_miss(10**9, cfg) < 1e-9
    assert AG.p_owner_miss(0, AG.OwnerConfig(lost_key_prob=0.05)) > 0.05


def test_claim_price_floor_is_the_breakeven():
    cl = AG.ClaimantConfig(min_profit_bps=200, slippage_bps=100)
    mk = AG.YedMarket(premium_bps=50)
    q = AG.claim_price_floor(np.array([10**10]), np.array([10**8]), np.array([50_000]), cl, mk)[0]
    prof = AG.claim_profit_usd(10**10, 10**8, q, 50_000, cl, mk)
    assert prof == pytest.approx(500 * 0.02, rel=1e-9)
    assert AG.redeem_price_floor(np.array([0]), np.array([1]), AG.OwnerConfig(), mk)[0] == np.inf


def test_attacker_hours_bias_only_with_majority():
    p = np.full((1, 10), 1_000_000, dtype=np.int64)
    a = AG.AttackerConfig(share=0.5, bias_bps=-1000, start_block=48 * 2, end_block=48 * 5)
    out = a.apply_to_hours(p, quoting_share=0.8)
    assert out[0, 2:5].tolist() == [900_000] * 3 and out[0, 5] == 1_000_000 and out[0, 1] == 1_000_000
    assert np.array_equal(AG.AttackerConfig(share=0.3, bias_bps=-1000).apply_to_hours(p, 0.8), p)


def test_agents_from_policy():
    from ybcal.config import Policy

    pol = Policy()
    ag = AG.AgentsConfig.from_policy(pol, adoption="mid")
    assert ag.minter.mints_per_day == pol.adoption_scenarios["mid"]["mints_per_day"]
    assert ag.claimant.min_profit_bps == pol.claimant_min_profit_bps
    assert ag.owner.absence_median_days == pol.owner_absence_median_days


# ---------------------------------------------------------------------------------------------------
# fees


def test_fees_match_kernels():
    rng = np.random.default_rng(0)
    coll = rng.integers(0, 10**15, 500)
    pool, att = F.fees_zat(PS, coll, armed=True)
    for c, p_, a_ in zip(coll.tolist(), pool.tolist(), att.tolist(), strict=True):
        f = K.fee_zat(c, PS["feeMin"], PS["feeBps"])
        assert p_ == f and a_ == K.attest_fee_zat(f, PS["attestFeeBps"])
        fb = F.tx_fees(PS, c, armed=True)
        assert (fb.pool_zat, fb.attest_zat) == (f, a_)
    assert F.tx_fees(PS, 10**12, armed=True, owner_path=True).attest_zat == 0
    assert F.tx_fees(PS, 10**12, eligible=False).pool_zat == 0
    assert F.min_collateral_floor(PS) == 4 * PS["feeMin"]


def _stream(rng, n, n_pools=5, liar=None):
    present = rng.random(n) < 0.8
    quote = rng.random(n) < 0.9
    pool = rng.integers(0, n_pools, n).astype(np.int16)
    base = 1_000_000 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    price = np.rint(base * (1 + rng.normal(0, 0.004, n))).astype(np.int64)
    if liar is not None:
        price = np.where(pool == liar, np.rint(price * 1.2).astype(np.int64), price)
    price = np.where(present & quote, price, 0)
    return present, price, np.where(present, pool, -1)


def test_judgement_series_matches_reg4_kernel():
    rng = np.random.default_rng(4)
    n = 600
    present, price, _pool = _stream(rng, n, liar=2)
    js = F.judgement_series(PS, price, present)
    lag = int(PS["peerLag"])
    for t in range(n):
        if price[t] <= 0 or t + lag >= n:
            assert not js.evaluated[0, t]
            continue
        peers = [int(price[h]) for h in range(max(t - lag, 0), t + lag) if h != t and h < n and price[h] > 0]
        j = K.reg4_judgement(
            int(price[t]), peers, int(PS["peerMin"]), int(PS["deviationBps"]), int(PS["accuracyBandBps"])
        )
        assert (bool(js.evaluated[0, t]), bool(js.in_band[0, t]), bool(js.penalized[0, t])) == tuple(j), t


def test_eligible_series_matches_scalar():
    rng = np.random.default_rng(5)
    n = 400
    present, price, pool = _stream(rng, n)
    price[100:260] = 0  # a long quote gap: FEE-0 heights appear
    pins = np.zeros(n, dtype=np.uint64)
    pins[300:320] = np.uint64(0b101)
    ser = F.eligible_nonempty_series(PS, price, present, pool, pins)[0]
    for r in range(n):
        assert ser[r] == bool(F.eligible_payees(PS, pool, price, r, int(pins[r]))), r
    assert not ser[200] and ser[50]


def test_fee_w_excludes_penalised_and_tilts_by_accuracy():
    rng = np.random.default_rng(6)
    n = 900
    present, price, pool = _stream(rng, n, n_pools=4, liar=3)
    js = F.judgement_series(PS, price, present)
    r = 850
    w = F.fee_w_weights(PS, pool, price, js, r)
    E_r = F.eligible_payees(PS, pool, price, r)
    assert set(w) <= set(E_r) and 3 in E_r and 3 not in w  # the liar is penalised (REG-2)
    assert sum(w.values()) == pytest.approx(1.0)
    # no tilt: weights ∝ tag counts in the window
    w0 = F.fee_w_weights(PS, pool, price, js, r, payee_tilt_bps=0)
    win = range(r - int(PS["payeeWindow"]) + 1, r + 1)
    counts = {k: sum(1 for i in win if pool[i] == k and price[i] > 0) for k in w0}
    tot = sum(counts.values())
    for k in w0:
        assert w0[k] == pytest.approx(counts[k] / tot)
    # all penalised → equal weights over E(R)
    js_all = F.JudgementSeries(js.evaluated, js.in_band, np.ones_like(js.penalized))
    wa = F.fee_w_weights(PS, pool, price, js_all, r)
    assert set(wa) == set(E_r) and all(v == pytest.approx(1 / len(E_r)) for v in wa.values())
    # FEE-0
    empty = np.zeros(n, dtype=np.int64)
    assert F.fee_w_weights(PS, pool, empty, js, r) == {}


def test_redemption_affordability_and_fee_table():
    a = F.redemption_affordability(PS, 10_000, 2, 100_000_000, 100_000_000)  # $100 class C at $100/YEC
    assert a.collateral_zat == 3 * 10**8 and not a.floor_binds  # D-7: 3 YEC ≥ 4·feeMin = 2 YEC
    crash = F.redemption_affordability(PS, 10_000, 0, 4_000_000, 400_000)  # A at $4 (125 YEC), crash to $0.40
    assert crash.fee_zat == PS["feeMin"] and not crash.redeem_worth_it
    rows = F.fee_table(PS, [10_000, 100_000, 1_000_000], 40_000_000)
    assert rows[0]["fee_share_of_debt"] > rows[-1]["fee_share_of_debt"]  # feeMin dominates small vaults
    assert all(r["collateral_zat"] % 1000 == 0 for r in rows)


def test_pool_revenue_and_attestor_revenue():
    out = F.pool_revenue(
        np.array([[10**8, 0, 10**8]]), 1152, 3, {0: 0.75, 1: 0.25}, np.array([[1_000_000] * 3])
    )
    assert out["yec_per_day"] == pytest.approx(2 / 3)
    assert out["per_pool_yec_per_day"][0] == pytest.approx(0.5)
    assert out["usd_per_day"] == pytest.approx(2 / 3)
    assert F.attestor_revenue_month(90.0, 30.0, 9) == pytest.approx(10.0)


def test_fee_share_of_value():
    c = np.array([4 * 10**8, 10**12])
    s = F.fee_share_of_value(PS, c, armed=True)
    f0 = K.fee_zat(int(c[0]), PS["feeMin"], PS["feeBps"])
    want0 = (f0 + K.attest_fee_zat(f0, PS["attestFeeBps"]) + f0) / c[0]
    assert s[0] == pytest.approx(want0) and s[1] == pytest.approx((25 + 6.25 + 25) / 10_000, rel=1e-9)

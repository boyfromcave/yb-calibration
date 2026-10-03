"""Hour mode, metrics on hand-computable toy paths, determinism, and the ARMED (notice / RED-4(b)) book."""

from __future__ import annotations

import math

import numpy as np
import pytest

from ybcal.params.paramset import mainnet, regtest
from ybcal.sim import agents as AG
from ybcal.sim import engine as E
from ybcal.sim import metrics as M
from ybcal.sim import vaults as VB
from ybcal.units import BLOCKS_PER_HOUR

PS = mainnet()
KERNEL = E.OracleTransferKernel.ideal(PS, substeps=4)


def hourly(n, level=1_000_000, crash_at=None, crash_to=50_000, paths=1):
    p = np.full((paths, n), level, dtype=np.int64)
    if crash_at is not None:
        p[:, crash_at:] = crash_to
    return p


def test_fast_path_constant_price_has_no_bad_debt():
    hs_n = 24 * 365 + 24 * 150  # A and B terms fit
    ht = hourly(hs_n)
    r = M.p_bad_debt_fast(PS, ht, kernel=KERNEL, classes=(0, 1), n_terms=4, start_stride=24)
    assert r.p["A"] == 0.0 and r.p["B"] == 0.0 and r.p_lock["A"] == 0.0
    assert r.n["A"] > 0 and r.n["B"] > 0


def _expected_crash_rate(ps, n, crash_at, terms, grace, skip, stride, lock_only=False):
    """Every vault minted before the crash and tested after it is bad (5× ratio × 5 % < 1); after
    the crash the medians reach the new price within the fast window (minting is halted by HALT-3
    meanwhile), so later vaults are safe."""
    hs = E.simulate_hours(ps, hourly(n, crash_at=crash_at), KERNEL, noise=False)
    starts = np.arange(skip, n, stride)
    rates = []
    for T in terms:
        off = T + 1 + (0 if lock_only else grace)
        e = starts + -(-off // BLOCKS_PER_HOUR)
        ok = (
            (e < n)
            & (hs.p_mint[0, starts] > 0)
            & ((hs.halt_mask[0, starts] & (E.HALT_NO_PRICE | E.HALT_DIVERGENCE)) == 0)
        )
        bad = ok & (hs.p_mint[0, starts] > 60_000) & (e >= crash_at)
        rates.append(bad.sum() / ok.sum())
    return float(np.mean(rates))


def test_fast_path_crash_before_claim_opens_is_bad_debt():
    n, crash = 24 * 200, 24 * 120
    r = M.p_bad_debt_fast(
        PS, hourly(n, crash_at=crash), kernel=KERNEL, classes=(0,), n_terms=4, start_stride=6, graces=[0]
    )
    terms = r.terms["A"]
    skip = r.meta["skip_hours"]
    want = _expected_crash_rate(PS, n, crash, terms, int(PS["grace"]), skip, 6)
    want_lock = _expected_crash_rate(PS, n, crash, terms, int(PS["grace"]), skip, 6, lock_only=True)
    assert r.p["A"] == pytest.approx(want, abs=1e-12)
    assert r.p_lock["A"] == pytest.approx(want_lock, abs=1e-12)
    assert r.by_grace["A"][0] == pytest.approx(want_lock, abs=1e-12)
    # the grace window adds exactly the starts whose lock is before the crash and claim after it
    assert r.increment["A"] == pytest.approx(want - want_lock, abs=1e-12) and r.increment["A"] > 0


def run_hours(prices, agents=None, options=None, workers=1, chunk_paths=8, seed=3):
    return VB.simulate_vault_book_hours(
        PS,
        prices,
        agents or AG.AgentsConfig(minter=AG.MinterConfig(mints_per_day=6)),
        KERNEL,
        np.random.default_rng(seed),
        options=options or VB.HourOptions(start_offset_blocks=420_480),
        workers=workers,
        chunk_paths=chunk_paths,
    )


def test_book_constant_price_all_redeemed_no_shortfall():
    res = run_hours(hourly(24 * 300, paths=2))
    bd = M.bad_debt_prob(res)
    assert bd["A"]["n"] > 0 and bd["all"]["bad"] == 0
    v = res.vaults
    acc = v["outcome"] == VB.O_ACTIVE
    closed = acc & (v["close_step"] >= 0)
    assert np.all(v["close_kind"][closed] == VB.K_OWNER)  # nobody is ever underwater
    assert res.counters["plan_mismatch"] == 0
    assert M.system_shortfall(res)["p_any"] == 0.0
    fr = M.fee_revenue(res, n_pools=4, n_seated=9)
    v = res.vaults
    tot_yec = (v["pool_fee_mint"].sum() + v["pool_fee_close"][v["close_step"] >= 0].sum()) / 1e8
    assert fr["pool_yec_per_day"] == pytest.approx(tot_yec / (res.days * 2))
    assert fr["pool_usd_per_day"] == pytest.approx(fr["pool_yec_per_day"])  # $1/YEC
    assert sum(fr["per_pool_usd_month"].values()) == pytest.approx(fr["pool_usd_per_day"] * 30)
    # FEE-1 is on the collateral: a class-A round trip costs 2 × 0.25 % × 500 % = 2.5 % of the debt
    assert fr["fee_share_of_debt_p50"]["A"] == pytest.approx(0.025, rel=0.02)
    ce = M.capital_efficiency(res)
    assert ce["A"]["yed_per_usd_p50"] == pytest.approx(0.2, rel=0.01)  # 1 / 500 % at σ = 1
    assert ce["B"]["yed_per_usd_p50"] == pytest.approx(0.25, rel=0.01)


def test_book_crash_marks_exactly_the_vaults_minted_before_it():
    n, crash = 24 * 260, 24 * 150
    res = run_hours(hourly(n, crash_at=crash, crash_to=40_000, paths=2))
    v = res.vaults
    acc = (v["outcome"] == VB.O_ACTIVE) & (v["claim_open_step"] >= 0)
    want = (v["step"] < crash) & (v["claim_open_step"] >= crash)
    assert np.array_equal(v["bad_at_claim_open"][acc], want[acc])
    assert M.bad_debt_prob(res)["A"]["bad"] == int((want & acc & (v["term_class"] == 0)).sum()) > 0
    sh = M.system_shortfall(res)
    assert sh["p_any"] == 1.0 and np.all(
        sh["final_usd"] > 0
    )  # nobody claims at a loss: debt stays under-backed
    assert res.counters["plan_mismatch"] == 0


def test_hour_mode_deterministic_across_workers():
    rng = np.random.default_rng(11)
    p = (1_000_000 * np.exp(np.cumsum(rng.normal(0, 0.01, (4, 24 * 120)), axis=1))).astype(np.int64)
    a = run_hours(p, workers=1, chunk_paths=2)
    b = run_hours(p, workers=2, chunk_paths=2)
    for k in a.vaults:
        if a.vaults[k].dtype == object:
            assert list(a.vaults[k]) == list(b.vaults[k])
        else:
            assert np.array_equal(a.vaults[k], b.vaults[k], equal_nan=a.vaults[k].dtype.kind == "f"), k
    assert np.array_equal(a.supply_cents, b.supply_cents)


def test_hour_enforcement_halt_defectors_sweep_unbacked():
    n = 24 * 200
    halt = np.zeros((1, n), dtype=bool)
    halt[0, 24 * 120 : 24 * 160] = True  # 40 days ENFORCEMENT: also > abandonBlocks (30 days)
    ag = AG.AgentsConfig(
        minter=AG.MinterConfig(mints_per_day=6, class_weights=(1, 0, 0)),
        owner=AG.OwnerConfig(defector_share=0.5),
    )
    res = run_hours(
        hourly(n), agents=ag, options=VB.HourOptions(start_offset_blocks=420_480, enforcement_halt=halt)
    )
    v = res.vaults
    sw = v["close_kind"] == VB.K_SWEEP
    unb = sw | (v["close_kind"] == VB.K_THIEF)  # thieves take claimable vaults without a burn too
    assert sw.any() and int(res.unbacked_cents[0, -1]) == int(v["unbacked_cents"][unb].sum()) > 0
    hs = v["close_step"][sw]
    assert np.all((hs >= 24 * 120) & (hs <= 24 * 160 + 1))
    honest = sw & (v["owner_kind"] == AG.HONEST)
    # honest owners sweep only after abandonBlocks of ENFORCEMENT
    if honest.any():
        assert np.all(v["close_step"][honest] >= 24 * 120 + int(PS["abandonBlocks"]) // BLOCKS_PER_HOUR)
    sh = M.system_shortfall(res)
    assert sh["unbacked_usd"][0, -1] == pytest.approx(res.unbacked_cents[0, -1] / 100)


def test_owner_miss_metric():
    ag = AG.AgentsConfig(
        minter=AG.MinterConfig(mints_per_day=40),
        owner=AG.OwnerConfig(absence_rate_per_year=20.0, absence_median_days=10.0),
    )
    res = run_hours(hourly(24 * 150, paths=2), agents=ag)
    om = M.owner_miss(res, PS, ag.owner)
    assert om["n"] > 1000
    assert om["simulated"] == pytest.approx(om["analytic"], abs=0.03)


def test_claims_profit_and_liquidation_volume():
    # a crash, a hold (the slow median catches up: vaults go underwater) and a partial recovery (claims pay)
    n = 24 * 260
    p = np.full(n, 1_000_000, dtype=np.int64)
    p[24 * 150 :] = 150_000
    p[24 * 170 :] = np.linspace(150_000, 260_000, n - 24 * 170).astype(np.int64)
    ag = AG.AgentsConfig(
        minter=AG.MinterConfig(mints_per_day=8, class_weights=(1, 0, 0)),
        owner=AG.OwnerConfig(lost_key_prob=0.5),
        claimant=AG.ClaimantConfig(min_profit_bps=100, slippage_bps=50),
    )
    res = run_hours(p[None, :], agents=ag)
    cp = M.claimant_profit(res)
    assert cp["n"] > 0 and cp["bps_p10"] >= 100 - 1e-6  # every executed claim clears the policy margin
    lv = M.liquidation_volume(res, depth_usd=50_000.0)
    v = res.vaults
    claimed = v["close_kind"] == VB.K_CLAIM
    total = AG.zat_value_usd(v["claimant_receive_zat"][claimed], v["tp_close"][claimed]).sum()
    assert lv["daily_usd"].sum() == pytest.approx(total)
    assert lv["max_depth_fraction"] == pytest.approx(lv["max_usd"] / 50_000.0)
    assert M.emergency_recovery(res)["n"] == 0  # unarmed


def test_time_until_cap_admits_class_gate():
    pm = np.full((1, 24 * 400), 1_000_000, dtype=np.int64)

    class PP:
        prices = pm
        resolution = "hour"

    d = M.time_until_cap_admits(PS, PP, 100_000)  # $1,000 at $1/YEC
    assert d["A"][0] == 0.0  # class A at σ = 1 reaches recapRatioBps: W20 exemption
    assert 0 < d["B"][0] == d["C"][0] < 30


class Arm:
    """An ``attest``-stage hook: ARMED from activation, attestors 10 % under the true price."""

    def __call__(self, stage, params, inputs, s):
        if stage == "attest":
            act = s.activation_status == E.ACTIVE
            a = np.rint(inputs.true_price * 0.9).astype(np.int64)
            s.armed = act.copy()
            s.a_mint = np.where(act, a, -1)
            s.a_claim = np.where(act, a, -1)


@pytest.mark.parametrize("premium,expect_b", [(-2000, True), (0, False)])
def test_armed_book_notices_and_red4b(premium, expect_b):
    """RED-4(b) pays the claimant the debt's worth at pClaim (margin 10^4) and returns the residual;
    with YED at par that never beats buying YED, so rational claimants use (b) only when YED trades
    at a discount. Every planned notice and claim passes the exact NOT-1 / RED verdicts."""
    start, n = 5, 900
    t = np.arange(n)
    tp = (2_000_000 * np.exp(-np.maximum(t - 200, 0) / 350)).astype(np.int64)
    ps = regtest().replace(startHeight=start, supplyCapBps=0, sigmaRefBps=0)
    ag = AG.AgentsConfig(
        minter=AG.MinterConfig(mints_per_day=1152 / 3, class_weights=(1, 0, 0)),
        owner=AG.OwnerConfig(lost_key_prob=1.0),
        market=AG.YedMarket(premium_bps=premium),
        claimant=AG.ClaimantConfig(min_profit_bps=100, slippage_bps=50),
    )
    hook = VB.VaultHook(agents=ag, seed=1, options=VB.BookOptions(record_events=True))
    s = E.simulate_blocks(
        ps, E.BlockInputs.perfect(tp, start), hooks=(Arm(), hook), rng=np.random.default_rng(1)
    )
    res = s.extras["vaults"]
    assert res.counters["plan_mismatch"] == 0
    v = res.vaults
    nb = int(v["claim_b"].sum())
    if expect_b:
        assert nb > 0 and res.counters["notices"] >= nb
        ev = res.meta["events"][0]
        notices = {e["row"]: e for e in ev if e["kind"] == "notice"}
        for e in ev:
            if e["kind"] == "claim" and e["claim_path"] == "b":
                nref = notices[e["row"]]["ref_height"]
                assert int(ps["emergencyPersist"]) <= e["ref_height"] - nref <= int(ps["emergencyNoticeTtl"])
                assert e["height"] > v["claim_height"][e["row"]]
        er = M.emergency_recovery(res)
        assert er["n"] == nb and er["recovered_usd"] > 0
    else:
        assert nb == 0


def test_supply_trajectory_and_refusals():
    res = run_hours(hourly(24 * 60, paths=2), options=VB.HourOptions())  # from startHeight: the cap is tiny
    st = M.supply_trajectory(res)
    assert st["supply_usd"].shape == res.supply_cents.shape and np.all(np.diff(st["days"]) > 0)
    rb = M.refusal_breakdown(res)
    assert rb["B"]["reasons"].get(VB.MINT_SUPPLY_CAP, 0) > 0  # fact 1.5-2: B/C closed early
    assert rb["A"]["reasons"].get(VB.MINT_SUPPLY_CAP, 0) == 0  # class A bypasses (W20)
    assert math.isclose(res.days, 60.0)

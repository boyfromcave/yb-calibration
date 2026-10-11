"""Option D fees / maxMint / supply-cap study (quantum plan D-Q-23, D-Q-24): the emission schedule, the
fee bands, the network-fee clamp, the depth model and the CLI."""

from __future__ import annotations

import json
from datetime import date

import pytest

from ybcal import cli
from ybcal.config import Policy
from ybcal.studies import fees_option_d as F
from ybcal.units import COIN


def test_emission_schedule_matches_ycash_v450():
    # chainparams.cpp: slow start 20,000; pre-Blossom halving 840,000; Blossom at 1,100,000 (Ycash, not Zcash)
    assert F.block_subsidy(0) == 0
    assert F.block_subsidy(10_000) == 12_5 * COIN // 10 // 20_000 * 10_001
    assert F.block_subsidy(20_000) == 12_5 * COIN // 10
    assert F.block_subsidy(849_999) == 12_5 * COIN // 10
    assert F.block_subsidy(850_000) == 6_25 * COIN // 100
    assert F.block_subsidy(1_099_999) == 6_25 * COIN // 100
    assert F.block_subsidy(1_100_000) == 3_125 * COIN // 1000
    assert F.block_subsidy(2_279_999) == 3_125 * COIN // 1000
    assert F.block_subsidy(2_280_000) == 1_5625 * COIN // 10_000
    assert F.halving_heights(4_000_000) == [850_000, 1_100_000, 2_280_000, 3_960_000]
    # the slow start skips the middle payout: exactly 125,000 YEC over 20,000 blocks
    assert F.cumulative_supply_zat(20_000) == 125_000 * COIN
    # a direct sum agrees with the segment sum
    direct = sum(F.block_subsidy(h) for h in range(0, 1_100_100))
    assert F.cumulative_supply_zat(1_100_100) == direct
    # ~16.97 M YEC at the 2026-10-11 height
    sup = F.cumulative_supply_zat(3_062_423) / COIN
    assert 16_900_000 < sup < 17_000_000
    assert F.issued_since(3_062_423, 3_062_423 + 1_152) == 1_152 * F.block_subsidy(3_062_423)


def test_height_today_from_policy_release_tip():
    pol = Policy.load(None)
    assert F.height_today(pol, date(2026, 10, 2)) == 3_052_055
    assert F.height_today(pol, date(2026, 10, 11)) == 3_052_055 + 9 * 1_152


def test_option_d_bands_floor_and_no_split_incentive():
    d = F.DEFAULT
    assert F.protocol_fee_usd(100, d) == 1.0  # the floor equals the 1 % band at minMint
    assert F.protocol_fee_usd(50, d) == 1.0  # floor binds below $100
    assert F.protocol_fee_usd(1_000, d) == 10.0
    assert F.protocol_fee_usd(10_000, d) == pytest.approx(55.0)
    assert F.protocol_fee_usd(100_000, d) == pytest.approx(145.0)
    assert F.effective_rate(100_000, d) == pytest.approx(0.00145)
    # marginal bands: splitting never pays
    assert 10 * F.protocol_fee_usd(10_000, d) > F.protocol_fee_usd(100_000, d)
    assert F.protocol_fee_usd(0, d) == 0.0
    # the conversion at pMint: $10 at $0.40 is 25 YEC
    assert F.usd_to_zat(10.0, 0.40) == 25 * COIN
    # early fee on the debt vs on the collateral (class A, 300 %): 5 % vs 15 % of the debt
    assert F.early_fee_usd(1_000, 0, d) == 50.0
    assert F.early_fee_today_usd(1_000, 0) == pytest.approx(150.0)
    assert F.claim_penalty_max_usd(1_000) == 250.0
    # every class: early fee on debt < today's < the claim penalty
    for c in range(3):
        assert F.early_fee_usd(1_000, c, d) < F.early_fee_today_usd(1_000, c) < F.claim_penalty_max_usd(1_000)


def test_todays_rule_scales_with_price_option_d_does_not():
    # FEE-1 today: 0.5 YEC floor binds at low prices for small vaults, 0.15 % of collateral above
    assert F.fee_today_zat(10 * COIN) == F.FEE_MIN_ZAT
    assert F.fee_today_usd(100, F.PRICE_MAX_USD) == pytest.approx(0.5 * F.PRICE_MAX_USD)
    assert F.fee_today_usd(100_000, 1.0) == pytest.approx(450.0)  # 0.15 % of $300k
    assert F.protocol_fee_usd(100_000) == F.protocol_fee_usd(100_000)  # price-free by construction


def test_sensitivity_scaling():
    d = F.DEFAULT.scaled(b2=0.5, yb=1.5)
    assert d.bands[2][1] == pytest.approx(0.0005)
    assert d.bands[0][1] == 0.01
    assert d.yb_usd_per_kb == pytest.approx(0.075)
    assert d.pq_usd_per_kb == 0.02


def test_network_fee_clamp_and_cap_bind_price():
    d = F.DEFAULT
    redeem = 8_249  # SLH owner redeem, P2PKH holders (pq_growth)
    # the cap binds below $4.12 for the $0.05/KB target on 8,249 bytes
    assert F.cap_bind_price_usd(redeem, 0.05, d) == pytest.approx(4.1245)
    assert F.network_fee_zat(redeem, 0.05, 0.40, d) == (F.MAX_TX_FEE_ZAT, "cap")
    assert F.network_fee_zat(redeem, 0.05, 5.0, d) == (8_249_000, "target")
    # at the ceiling the 1× floor does not yet bind for the redeem (9,603 > 824) but the parity fails
    zat, bound = F.network_fee_zat(redeem, 0.05, F.PRICE_MAX_USD, d)
    assert bound == "target" and zat / redeem < F.P2PKH_ZAT_PER_BYTE
    assert F.parity_price_usd(0.05) == pytest.approx(1_225, rel=0.01)
    # the recommended set: floor at m = 50 keeps parity at the ceiling, the $2 cap never binds on the redeem
    r = F.RECOMMENDED
    zat, bound = F.network_fee_zat(redeem, 0.05, F.PRICE_MAX_USD, r)
    assert bound == "floor" and zat / redeem >= F.P2PKH_ZAT_PER_BYTE
    assert F.cap_bind_price_usd(redeem, 0.05, r) == 0.0
    assert r.cap_zat(0.40) == 5 * COIN and r.cap_zat(F.PRICE_MAX_USD) == F.MAX_TX_FEE_ZAT


def test_depth_model_and_concentration():
    # closed form agrees with the executable test at its boundary
    for depth in (37.0, 4_982.0):
        m = F.max_executable_debt_usd(depth)
        assert F.claim_executable(m * 0.999, depth) and not F.claim_executable(m * 1.001, depth)
    assert F.max_executable_debt_usd(4_982.0) < 100_000  # a $100k whale is unexecutable on the frozen books
    rows = F.concentration(1_000_000.0)
    single, spread = rows[0], rows[1]
    assert single.n_vaults == 1 and spread.n_vaults == 100
    assert single.unexecutable_share == 1.0 and spread.unexecutable_share == 0.0
    assert spread.lost_key_exposure == pytest.approx(0.01)


def test_supply_rows_and_rule_cap():
    rows = F.supply_rows(3_062_423, (0.40, 10.0))
    r = rows[0]
    assert r.vaults_100k == 10
    # MINT-6's base (issued since start) is far below the circulating cap: 15 % of 1.5625 × 1,152 × 30 × $0.40
    assert r.rule_cap_30d_usd == pytest.approx(0.15 * 1.5625 * 1_152 * 30 * 0.40)
    assert r.rule_days_to_one_100k > 365
    assert rows[1].rule_cap_365d_vaults_100k == pytest.approx(9.855, rel=0.01)


def test_revenue_models_and_attestor_floor():
    pol = Policy.load(None)
    rows = F.revenue(pol, F.DEFAULT, (0.40,))
    low = F.revenue_at(rows, "low", "supply")
    assert low.mean_debt_usd == pytest.approx(50_000 / (2 * 180))
    assert not low.seat_meets_floor  # the bands do not pay the attestor floor at low adoption
    assert F.revenue_at(rows, "mid", "supply").seat_meets_floor  # from the mid case on they do
    whale = F.revenue_at(rows, "whale", "whale")
    assert whale.attestor_usd_year == pytest.approx(10 * 145 * 0.5)
    assert whale.pool_usd_year == pytest.approx(20 * 145)
    today = F.revenue_at(rows, "whale", "whale", "today")
    assert today.pool_usd_year == pytest.approx(20 * 450)


def test_shock_rows_fix_the_fee_in_yec():
    rows = F.shock_rows(F.DEFAULT)
    assert all(r["fee_zat"] == 25 * COIN for r in rows)
    assert [round(r["fee_usd_now"], 2) for r in rows] == [3.0, 10.0, 17.0]
    assert rows[0]["claimable"] and not rows[1]["claimable"]


def test_run_and_cli(tmp_path, capsys):
    rc = cli.main(["fees", "--today", "2026-10-11", "--out", str(tmp_path)])
    assert rc == 0
    res = json.loads((tmp_path / "fees.json").read_text())
    assert res["constants"]["height"] == 3_062_423
    assert res["recommendation"]["network_floor_zat_per_kb"] == 5_000
    assert res["recommendation"]["max_vault_share_bps"] == 1_000
    md = (tmp_path / "fees.md").read_text()
    assert "## Recommendation" in md and "maxVaultShareBps" in md
    assert "Recommendation" in capsys.readouterr().out


def test_attestor_tiers_bonds_and_break_even():
    pol = Policy.load(None)
    tiers = F.adoption_tiers(pol)
    assert list(tiers) == ["low", "mid", "high", "whale"]
    assert tiers["low"]["transfers_per_day"] == 10.0 and tiers["low"]["claims_per_day"] == 0.2
    assert tiers["high"]["transfers_per_day"] == 1_000.0 and tiers["high"]["mints_per_day"] == 50.0
    assert tiers["whale"]["mints_per_day"] == 10.0 and tiers["whale"]["whale_mints_per_year"] == 10.0
    # bond rules: 20,000 YEC scales with price; the pinned bonds keep the 2,000-YEC floor (D-Q-23a)
    assert F.bond_usd("yec20k", 0.40) == 8_000.0 and F.bond_usd("yec20k", 10.0) == 200_000.0
    assert F.bond_yec("usd8k", 0.40) == 20_000.0 and F.bond_yec("usd8k", 1.0) == 8_000.0
    assert F.bond_yec("usd8k", 5.0) == 2_000.0 and F.bond_usd("usd8k", 10.0) == 20_000.0
    assert F.bond_usd("usd2k", 0.40) == 2_000.0 and F.bond_usd("usd2k", 5.0) == 10_000.0
    # revenue has no bond term and no price term: low tier, supply-consistent sizes, 50 %
    low = tiers["low"]
    assert F.seat_revenue_usd_year(low, "supply", 0.5) == pytest.approx(730 * (50_000 / 360) * 0.01 * 0.5 / 6)
    assert F.seat_revenue_usd_year(low, "supply", 1.0) == pytest.approx(
        2 * F.seat_revenue_usd_year(low, "supply", 0.5)
    )
    # the whale tier adds ten $145 fees a year on top of mid
    mid, whale = tiers["mid"], tiers["whale"]
    assert F.set_revenue_usd_year(whale, "supply", 0.5) - F.set_revenue_usd_year(mid, "supply", 0.5) == (
        pytest.approx(10 * 145 * 0.5)
    )
    # break-even closed form inverts the return
    fee = F.tier_mean_fee_usd(low, "supply")
    be = F.break_even_mints_per_day(fee, 0.5, 8_000.0, 0.05)
    assert be == pytest.approx(0.05 * 8_000 * 6 / (365 * fee * 0.5))
    assert 9 < be < 10  # the $8,000 bond's hurdle is the mid tier
    assert F.break_even_mints_per_day(fee, 0.5, 2_000.0, 0.05) == pytest.approx(be / 4)
    assert F.break_even_mints_per_day(fee, 1.0, 8_000.0, 0.05) == pytest.approx(be / 2)


def test_attestor_return_and_capture_rows():
    pol = Policy.load(None)
    rows = F.bond_return_rows(pol)
    r = next(
        x
        for x in rows
        if (x.tier, x.size_model, x.price_usd, x.bond_rule) == ("low", "supply", 0.40, "usd8k")
    )
    assert r.return_apr == pytest.approx(84.49 / 8_000, rel=1e-3) and not r.clears_cost_of_capital
    m = next(
        x
        for x in rows
        if (x.tier, x.size_model, x.price_usd, x.bond_rule) == ("mid", "supply", 0.40, "usd8k")
    )
    assert m.clears_cost_of_capital
    h = next(
        x
        for x in rows
        if (x.tier, x.size_model, x.price_usd, x.bond_rule) == ("high", "supply", 10.0, "yec20k")
    )
    assert not h.clears_cost_of_capital  # 20,000 YEC at $10 is a $200k bond
    cap = F.capture_rows(3_062_423)
    c8 = next(c for c in cap if c["price_usd"] == 0.40 and c["bond_rule"] == "usd8k")
    assert c8["capture_capital_usd"] == 7 * 8_000
    assert c8["cap_d_q_25_usd"] == pytest.approx(0.15 * 16_972_535.9375 * 0.40)
    assert c8["capture_to_old_cap_year"] == pytest.approx(
        56_000 / 39_420, rel=1e-3
    )  # D-RD-ATT-4 met on the old base
    assert c8["capture_to_cap"] < 0.06  # and not under D-Q-25
    # the 20,000-YEC bond keeps the same capture/cap ratio at every price; the pinned bond's falls
    ratios = {c["price_usd"]: c["capture_to_cap"] for c in cap if c["bond_rule"] == "yec20k"}
    assert len({round(v, 6) for v in ratios.values()}) == 1


def test_attestors_cli(tmp_path, capsys):
    rc = cli.main(["fees", "--attestors", "--today", "2026-10-11", "--out", str(tmp_path)])
    assert rc == 0
    res = json.loads((tmp_path / "attestors.json").read_text())
    assert res["constants"]["height"] == 3_062_423 and res["constants"]["seats"] == 6
    assert len(res["seat_revenue"]) == 8 and len(res["break_even"]) == 18
    md = (tmp_path / "attestors.md").read_text()
    assert "## 6. Recommendation for launch" in md and "revenue is set by adoption, not by bond size" in md
    assert "Share 50 %" in md and "$8,000 pinned" in md
    assert not (tmp_path / "fees.md").exists()
    assert "Attestor economics" in capsys.readouterr().out

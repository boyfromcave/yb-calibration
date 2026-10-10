"""PQ growth + fee-multiplier study (quantum plan Q7): spec byte counts, node fees, the recommendation."""

from __future__ import annotations

import json

import pytest

from ybcal import cli
from ybcal.config import Policy
from ybcal.studies import pq_growth as Q


def test_spec_byte_counts():
    # yellowback-quantum-spec.md §0, §1.5, §2.1
    assert Q.SCRIPTSIG["owner_slh"] == 7_939
    assert Q.SCRIPTSIG["owner_falcon"] == 1_578
    assert Q.SCRIPTSIG["holder_slh"] == 7_938
    assert Q.SCRIPTSIG["holder_falcon"] == 1_577
    assert Q.PQPKH_SCRIPT == 35
    assert Q.chunks(7_857) == [520] * 15 + [57]
    assert Q.chunks(667) == [520, 147] and Q.chunks(897) == [520, 377]
    # Falcon holder spends fit under today's 1,650-byte scriptSig limit; SLH needs the 9,000 policy limit.
    assert Q.SCRIPTSIG["holder_falcon"] < 1_650 < Q.SCRIPTSIG["owner_slh"] <= 9_000


def test_reference_sizes_and_zip317():
    refs = Q.references()
    assert refs["p2pkh_1in2out"].size == 245
    assert refs["sapling_z2z"].size == 2_373
    assert Q.zip317_fee(refs["p2pkh_1in2out"]) == 1_000
    assert Q.SCRIPTSIG["p2pkh"] == 107 and Q.carrier_scriptsig(3) == 373


def test_variants_change_only_what_the_spec_changes():
    today, pq = Q.paths("today"), Q.paths("pq_p2pkh")
    # V and the MINT payload keep their lengths (spec §1.1, §3.1): a MINT with a P2PKH holder is unchanged.
    assert today["mint"].size == pq["mint"].size
    assert Q.paths("falcon")["mint"].size == today["mint"].size + 10  # PQPKH token output: 35 vs 25
    assert pq["redeem"].size - today["redeem"].size == Q.SCRIPTSIG["owner_slh"] - Q.SCRIPTSIG["owner_ec"] + 2
    assert not any(tx.is_pq for tx in today.values())
    assert pq["redeem"].pq == ("slh",) and Q.paths("falcon")["redeem"].pq == ("slh", "falcon")


def test_fee_rules_both_lines():
    redeem = Q.paths("pq_p2pkh")["redeem"]
    # clients survey §9: at 1x the DEFAULT_FEE floor binds on ycash-dd (≈ 810 < 1,000)
    assert Q.size_fee(redeem.size, 1) < 1_000 and Q.fee_dd(redeem, 1) == 1_000
    # ycash6: ZIP-317 already prices transparent input bytes (500 zat / 150 B)
    assert Q.fee_6(redeem, 1) == 500 * Q.zip317_actions(redeem) > 25_000
    assert Q.fee_6(redeem, 1, additive=True) == Q.fee_6(redeem, 1) + Q.size_fee(redeem.size, 1)
    # the relay minimum is capped at DEFAULT_FEE whatever the size
    assert Q.relay_min_fee(Q.paths("slh")["redeem"]) == 1_000
    # non-PQ transactions keep today's fees
    mint = Q.paths("pq_p2pkh")["mint"]
    assert Q.fee_dd(mint, 50) == 1_000


def test_recommendation_rule():
    pol = Policy()
    rec = Q.recommend(pol)
    assert rec.mult in Q.CANDIDATES and rec.mult >= max(rec.m_parity_p2pkh, rec.m_cross_line)
    assert 33 < rec.m_cross_line < 35 and 40 < rec.m_parity_p2pkh < 45 and rec.m_parity_sapling < 5
    assert rec.mult == 50
    p2pkh_rate = 1_000 / Q.references()["p2pkh_1in2out"].size
    for v in ("slh", "falcon"):
        for tx in Q.paths(v).values():
            if tx.is_pq:
                assert Q.fee_dd(tx, rec.mult) == Q.fee_6(tx, rec.mult)  # cross-line identity
                assert Q.fee_dd(tx, rec.mult) / tx.size >= p2pkh_rate  # P2PKH parity
    assert rec.max_fee_usd_worst < 1.0


def test_growth_matches_plan_first_order_numbers():
    pol = Policy()
    sc = Q.scenarios(pol)
    assert set(sc) == {"low", "mid", "high", "stress"}
    # plan §6: ten thousand SLH spends a day ≈ 30 GB a year
    g = Q.growth("slh", "stress", sc["stress"])
    assert 28 < g.gb_per_year < 33
    # clients survey §9: 30 owner redeems a day ≈ 243 KB a day of owner spends
    assert 240_000 < 30 * Q.paths("pq_p2pkh")["redeem"].size < 250_000
    lo = {v: Q.growth(v, "high", sc["high"]).gb_per_year for v in Q.VARIANTS}
    assert lo["today"] < lo["pq_p2pkh"] < lo["falcon"] < lo["slh"]


def test_validation_structure():
    v = Q.validation()
    assert v["policy_pq_checks_per_block"] == 1_000
    assert v["slh_per_block"] == 248 and v["slh_per_block_policy"] == 248
    assert v["falcon_per_block_policy"] == 1_000 < v["falcon_per_block"]
    # Q1 benchmark table (q/pqlib 7838d66ce): SLH 235.0 µs, Falcon 18.9 µs, ECDSA 18.9 µs
    assert v["slh_block_verify_s"] == pytest.approx(248 * 235.0e-6)
    assert v["falcon_block_verify_s"] == pytest.approx(1_188 * 18.9e-6)
    assert v["ecdsa_block_verify_s"] == pytest.approx(13_513 * 18.9e-6)
    assert v["slh_measured_ratio"] < v["slh_break_even_ratio"]
    assert v["falcon_measured_ratio"] < v["falcon_break_even_ratio"]
    assert v["sapling_block_verify_s"] is None and v["sapling_z2z_per_block"] == 842
    w = Q.validation({"ecdsa": 50.0, "slh": 1_000.0, "falcon": 100.0})
    assert w["slh_block_verify_s"] == pytest.approx(0.248)


def test_cli_writes_report(tmp_path, capsys):
    rc = cli.main(["pq", "--multipliers", "1,50", "--out", str(tmp_path)])
    assert rc == 0
    res = json.loads((tmp_path / "pq.json").read_text())
    assert res["recommendation"]["mult"] == 50
    assert "m = 50" in (tmp_path / "pq.md").read_text()
    assert "Recommendation" in capsys.readouterr().out

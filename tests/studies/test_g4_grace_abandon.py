"""G4 study (WP-7b): runbook and dev-absence arithmetic, the grace and abandonment rules on hand-built
tables, and a tiny end-to-end run."""

from __future__ import annotations

import pytest

from ybcal.config import Policy
from ybcal.params.paramset import mainnet
from ybcal.sim import agents as AG
from ybcal.studies import g4_grace_abandon as G4
from ybcal.studies.base import Budget, Env, Metrics, ResultTable, load_study, missing_recommendations
from ybcal.units import BLOCKS_PER_DAY

TINY = Budget(
    "quick",
    paths=8,
    block_horizon_days=3,
    hour_horizon_years=5.2,
    grid_points=3,
    lhs_samples=4,
    halving_rounds=1,
    scenario_set="core",
    morris_trajectories=2,
    sobol_samples=8,
    max_minutes=1,
)


def test_runbook_and_dev_absence():
    pol = Policy()
    ps = mainnet()
    assert G4.runbook_blocks(ps, pol) == 2016 + 2016 + 16_128 + 4_608 == 24_768
    d = G4.dev_absence(34_560, ps, pol)
    assert d["dev.max_absence_days"] == pytest.approx((34_560 - 16_128 - 4_608) / BLOCKS_PER_DAY)  # 12 days
    assert d["dev.premature_14d"] == 1.0 and d["dev.unsweepable_days_180d"] == 30.0
    assert G4.dev_absence(80_000, ps, pol)["dev.premature_30d"] == 0.0
    assert G4.ceil_days(24_768) == 25_344


def _grace_table(
    rows: dict[int, tuple[float, float]], cur: int = 34_560, A: int = 34_560, shares=(0.8,) * 10
) -> ResultTable:
    """rows: grace → (P(miss), weighted ΔP); w_owner = w_debt = 1."""
    pol = Policy()
    base = mainnet()
    t = ResultTable(base)
    for g in sorted(rows, key=lambda v: v != cur):
        pm, dp = rows[g]
        ps = base if g == cur else base.replace(grace=g, abandonBlocks=max(A, g))
        t.add(
            ps,
            Metrics(
                {
                    "zero": 0.0,
                    "J": pm + dp,
                    "owner.p_miss": pm,
                    "owner.p_miss_with_lost_keys": pm,
                    "dP.weighted": dp,
                    "dP.A": dp,
                    "dP.B": dp,
                    "dP.C": dp,
                    "pbad.A": dp,
                    "pbad.B": dp,
                    "pbad.C": dp,
                    "viol.owner_miss": max(0.0, pm / pol.max_owner_miss_prob - 1),
                },
                "J",
                True,
                {"owner_miss": pm <= pol.max_owner_miss_prob},
                meta={"shares": list(shares), "provenance": "synthetic", "share_provenance": "judgement"},
            ),
        )
    return t


def test_grace_rule_keep_change_blocked():
    st = G4.make_study()
    pol = Policy()
    # current best within materiality → KEEP, abandonment untouched
    recs = {
        r.param: r for r in st.decide(_grace_table({34_560: (0.004, 0.019), 17_280: (0.009, 0.010)}), pol)
    }
    assert recs["grace"].recommended == 34_560 and recs["abandonBlocks"].recommended == 34_560
    # 15 days improves J by > 20 % and meets the owner tolerance → change; abandonment follows the runbook
    recs = {
        r.param: r for r in st.decide(_grace_table({34_560: (0.004, 0.030), 17_280: (0.009, 0.012)}), pol)
    }
    assert recs["grace"].recommended == 17_280 and recs["grace"].verdict == "PROVISIONAL"
    assert recs["abandonBlocks"].recommended == 25_344  # ceil_days(runbook 24,768)
    assert recs["abandonBlocks"].recommended >= recs["grace"].recommended
    # nobody meets the owner tolerance → BLOCKED with the least violating grace
    recs = {r.param: r for r in st.decide(_grace_table({34_560: (0.02, 0.01), 41_472: (0.015, 0.02)}), pol)}
    assert recs["grace"].verdict == "BLOCKED" and recs["grace"].recommended == 41_472


def test_abandon_unmeetable_bound_is_blocked_not_a_change():
    """D-RD-COL-8: share samples below enforcementResume make P(false abandonment) ≈ 1 at every
    abandonBlocks; the rule must not 'recommend' the registry ceiling (which still violates)."""
    st = G4.make_study()
    pol = Policy()
    rows = {34_560: (0.004, 0.019), 17_280: (0.009, 0.010)}
    shares = (0.50, 0.52) + (0.75,) * 18
    assert G4.lost_share_fraction(mainnet(), shares) == pytest.approx(0.1)
    recs = {r.param: r for r in st.decide(_grace_table(rows, shares=shares), pol)}
    a = recs["abandonBlocks"]
    assert a.verdict == "BLOCKED" and a.recommended == a.current == 34_560
    assert "enforcementResume" in a.metrics["decision"]
    # an enforcing majority well above the resume threshold: the grace bound decides (KEEP)
    recs = {r.param: r for r in st.decide(_grace_table(rows, shares=(0.72,) * 20), pol)}
    a = recs["abandonBlocks"]
    assert a.verdict in ("KEEP", "PROVISIONAL") and a.recommended == 34_560


def test_owner_miss_analytic_matches_wp4():
    pol = Policy()
    assert AG.p_owner_miss(34_560, G4.owner_cfg(pol)) == pytest.approx(0.0043, abs=3e-4)


def test_end_to_end(tmp_path):
    env = Env(Policy(), TINY, seed=7, out_dir=str(tmp_path))
    st = load_study("G4")
    base = mainnet()
    tab = ResultTable(base)
    for c in st.space(base, env.budget):
        tab.add(c, st.evaluate(c, env))
    recs = st.decide(tab, env.policy)
    assert not missing_recommendations(st, recs)
    by = {r.param: r for r in recs}
    assert by["abandonBlocks"].recommended >= max(
        by["grace"].recommended, G4.runbook_blocks(base, env.policy)
    )
    assert by["abandonBlocks"].recommended % BLOCKS_PER_DAY == 0
    for r in recs:
        assert st.explain(r, tab)
    cur = tab.current().metrics.values
    assert cur["dP.A"] >= -1e-12 and 0 < cur["owner.p_miss"] < 0.01
    assert (tmp_path / "g4" / "g4_grace_tradeoff.png").exists()
    assert {n["id"] for n in G4.design_notes(tab, env.policy)} >= {"G4-DN1"}

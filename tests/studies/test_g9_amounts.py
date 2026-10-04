"""G9 study (WP-7b): closed forms (Nakamoto, VOID, fee bounds), the verify rule including CHANGE and
BLOCKED, and the end-to-end recommendation of every parameter."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pytest

from ybcal.config import Policy
from ybcal.params.paramset import mainnet
from ybcal.studies import g9_amounts as G9
from ybcal.studies.base import Budget, Env, ResultTable, load_study, missing_recommendations

TINY = Budget(
    "quick",
    paths=8,
    block_horizon_days=3,
    hour_horizon_years=1.0,
    grid_points=3,
    lhs_samples=4,
    halving_rounds=1,
    scenario_set="core",
    morris_trajectories=2,
    sobol_samples=8,
    max_minutes=1,
)


@dataclass
class FakeDepth:
    volume_24h_usd: np.ndarray


def run(policy=None, data=None, tmp_path=None):
    env = Env(policy or Policy(), TINY, seed=1, data=data or {}, out_dir=str(tmp_path) if tmp_path else None)
    st = load_study("G9")
    base = mainnet()
    tab = ResultTable(base)
    for c in st.space(base, env.budget):
        tab.add(c, st.evaluate(c, env))
    recs = st.decide(tab, env.policy)
    return st, tab, {r.param: r for r in recs}


def test_nakamoto_table():
    # Nakamoto (2008) §11: q = 0.1, z = 5 → 0.0009137; q = 0.3, P < 0.001 first at z = 24
    assert G9.nakamoto_catch_up(0.1, 5) == pytest.approx(0.0009137, rel=1e-3)
    assert G9.min_confirmations(0.1, 0.001) == 5
    assert G9.min_confirmations(0.3, 0.001) == 24
    assert G9.nakamoto_catch_up(0.5, 100) == 1.0


def test_void_and_ranges_at_mainnet():
    assert G9.p_void_natural(2, 0.005) == pytest.approx(0.005**3)
    env = Env(Policy(), TINY, seed=1)
    ctx = G9.G9Context.build(env)
    r = G9.ranges(mainnet(), ctx)
    assert r["minMint"][0] == pytest.approx(4 * 50_000_000 * 100_000_000 / (30_000 * 100_000_000))  # $66.67
    assert r["walletConfirmations"][0] == 24
    assert r["DEFAULT_REF_LAG"] == (0.0, 30.0, r["DEFAULT_REF_LAG"][2], r["DEFAULT_REF_LAG"][3])


def test_end_to_end_defaults(tmp_path):
    st, tab, by = run(tmp_path=tmp_path)
    assert not missing_recommendations(st, list(by.values()))
    assert by["walletConfirmations"].recommended == 24 and by["walletConfirmations"].verdict == "CHANGE"
    assert by["DEFAULT_REF_LAG"].verdict == "KEEP" and by["DEFAULT_REF_LAG"].recommended == 2
    assert by["minMint"].recommended == 10_000 and by["minMint"].verdict == "PROVISIONAL"
    assert by["maxMint"].verdict == "PROVISIONAL"  # no volume data: kept, needs data
    assert by["minMint"].metrics["current"]["prop_share.A@ref"] == pytest.approx(0.025)
    assert {n["id"] for n in G9.design_notes(tab, Policy())} >= {"G9-DN1", "G9-DN2"}
    for r in by.values():
        assert st.explain(r, tab)
    assert (tmp_path / "g9" / "g9_fees_and_reorgs.png").exists()


def test_max_mint_change_with_volume_data():
    _, _, by = run(data={"depth": FakeDepth(np.full(60, 20_000.0))})
    # 10 % of $20,000 / 1.1 = $1,818 → $1,000 lattice step
    assert by["maxMint"].recommended == 100_000 and by["maxMint"].verdict == "CHANGE"
    assert by["maxMint"].provenance == "real-data"
    assert by["minMint"].recommended <= by["maxMint"].recommended <= by["maxOutput"].recommended


def test_blocked_when_range_empty():
    _, _, by = run(policy=Policy().replace(reorg_attacker_share=0.45))
    assert by["walletConfirmations"].verdict == "BLOCKED"


# --- wave 2 (D-RD-INF-4): real data -------------------------------------------------------------


@dataclass
class FakeDepth2:
    volume_24h_usd: np.ndarray
    depth_2pct_usd: np.ndarray


@dataclass
class FakePools:
    s: dict

    def shares(self):
        return self.s


REAL_POOLS = FakePools({"a": 0.5203, "b": 0.2130, "c": 0.1373, "d": 0.0612, "e": 0.0683})


def test_rd2_crash_thin_market_does_not_raise(tmp_path):
    """rd2: p10 volume $700 gave a maxMint upper bound of $63, rounded to 0, then minMint 0 and
    fee_table raised "mint unsatisfiable (K14)". Now: environment-limited, current kept, no zero."""
    pol = Policy().replace(yec_daily_volume_p10_usd=700.0)
    _, _, by = run(policy=pol, data={"depth": FakeDepth2(np.full(5, np.nan), np.full(5, 169.0))},
                      tmp_path=tmp_path)
    mm = by["maxMint"]
    assert mm.recommended == 1_000_000 and mm.verdict in ("KEEP", "PROVISIONAL")
    e = mm.metrics["environment_blocked"]
    assert e["note"] == "G9-ENV-1" and "15.7 days of p10 volume" in e["exposure"]
    assert "65× the ±2 % book depth ($169)" in e["exposure"]
    assert by["minMint"].recommended == 10_000 and by["minMint"].recommended > 0
    assert all(int(r.recommended) > 0 for r in by.values())
    assert (tmp_path / "g9" / "g9_maxmint_frontier.csv").exists()
    assert any(n["id"] == "G9-ENV-1" for n in mm.metrics["design_notes"])


def test_majority_pool_sizes_confirmations_against_the_largest_minority():
    _, _, by = run(data={"pool_shares": REAL_POOLS})
    wc = by["walletConfirmations"]
    # policy 0.30 > second pool 0.213: z = 24 (Nakamoto, q = 0.3, P ≤ 0.001)
    assert wc.recommended == 24 and wc.verdict == "CHANGE"
    e = wc.metrics["environment_blocked"]
    assert e["note"] == "G9-ENV-2" and "52.0%" in e["why"]
    assert any("VOID any mint" in n for n in by["DEFAULT_REF_LAG"].notes)


def test_minority_top_pool_raises_the_adversary():
    _, _, by = run(data={"pool_shares": FakePools({"a": 0.31, "b": 0.30, "c": 0.39 - 1e-9})})
    # top 0.39 > policy 0.30 → q = 0.39 → z far above the registry's 30: BLOCKED (a parameter bound)
    assert by["walletConfirmations"].verdict == "BLOCKED"
    assert "environment_blocked" not in by["walletConfirmations"].metrics
    _, _, by = run(data={"pool_shares": FakePools({"a": 0.31, "b": 0.30, "c": 0.29, "d": 0.10})})
    assert by["walletConfirmations"].recommended == G9.min_confirmations(0.31, 0.001)
    _, _, by = run(data={"pool_shares": FakePools({"a": 0.6, "b": 0.25, "c": 0.15})})
    # majority: sized against max(policy 0.30, second 0.25) = 0.30 → 24, environment-limited
    assert by["walletConfirmations"].recommended == 24
    assert by["walletConfirmations"].metrics["environment_blocked"]["note"] == "G9-ENV-2"


def test_blocked_fallback_stays_inside_the_registry():
    """A BLOCKED range keeps the current value clamped to the registry bounds (never 0)."""
    _, _, by = run(policy=Policy().replace(reorg_attacker_share=0.45))
    wc = by["walletConfirmations"]
    lo, hi = G9.REGISTRY["walletConfirmations"].bounds
    assert wc.verdict == "BLOCKED" and lo <= wc.recommended <= hi


def test_fee_table_errors_are_accurate():
    from ybcal.sim import fees as F

    with pytest.raises(ValueError, match="undefined: non-positive"):
        F.fee_table(mainnet(), [0], 400_000)
    with pytest.raises(ValueError, match="K14"):
        F.fee_table(mainnet(), [10_000_000_000], 100)

"""Release study (WP-7b): M14 / L8 / W18 / W19 arithmetic, tip and upgrade inputs, chainparams parse."""

from __future__ import annotations

import pytest

from ybcal.config import Policy
from ybcal.params.paramset import mainnet
from ybcal.studies import release as R
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


def run(data=None, tmp_path=None):
    env = Env(Policy(), TINY, seed=1, data=data or {}, out_dir=str(tmp_path) if tmp_path else None)
    st = load_study("R")
    tab = ResultTable(mainnet())
    for c in st.space(mainnet(), env.budget):
        tab.add(c, st.evaluate(c, env))
    recs = st.decide(tab, env.policy)
    return st, tab, {r.param: r for r in recs}


def test_default_tip_keeps_shipped_heights(tmp_path):
    st, tab, by = run(tmp_path=tmp_path)
    assert not missing_recommendations(st, list(by.values()))
    s, u = by["startHeight"], by["enforceUntilHeight"]
    assert (s.recommended, u.recommended) == (3_075_000, 3_495_480)
    assert s.verdict == u.verdict == "KEEP"  # judgement provenance: not PROVISIONAL
    m = s.metrics["recommended"]
    assert m["lead_margin"] == 3_075_000 - 3_052_055 - 16_128 == 6_817
    assert m["renewal_deadline"] == 3_495_480 - 210_240
    assert m["runbook"] == 24_768 and m["runbook_slack"] == 210_240 - 24_768
    assert m["abandon_margin"] == 34_560 - 24_768
    assert s.metrics["dates_recommended"]["start"] == "2026-10-21"
    assert st.explain(s, tab) and (tmp_path / "r" / "release.csv").exists()


def test_later_tip_moves_start_and_sunset():
    _, _, by = run(data={"release_tip": 3_060_000})
    assert by["startHeight"].recommended == 3_077_000  # ceil((3,060,000 + 16,128) / 1,000) · 1,000
    assert by["enforceUntilHeight"].recommended == 3_077_000 + 420_480
    assert by["startHeight"].verdict == "CHANGE"


def test_upgrade_caps_sunset():
    _, _, by = run(data={"next_upgrade_height": 3_400_000})
    assert by["enforceUntilHeight"].recommended == 3_400_000
    assert by["startHeight"].recommended == 3_075_000


def test_chainparams_parse_matches_recorded():
    ups, src = R.mainnet_upgrades()
    assert ups == R.PINNED_UPGRADES
    assert R.next_upgrade(3_052_055, ups) is None
    assert R.next_upgrade(0, ups) == ("OVERWINTER", 347_500)
    if "recorded" in src:
        pytest.skip("no ycash6 clone: the recorded table was used")

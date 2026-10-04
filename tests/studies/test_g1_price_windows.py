"""G1 study (WP-7a): engine equivalence, space, decision rule, determinism, tiny end-to-end run."""

from __future__ import annotations

import numpy as np
import pytest

from ybcal.config import Policy
from ybcal.params.paramset import mainnet
from ybcal.params.registry import params_for_group
from ybcal.sim import engine as E
from ybcal.studies import g1_price_windows as G
from ybcal.studies.base import Budget, Env, Metrics, ResultTable, load_study, missing_recommendations

TINY = Budget(
    "quick",
    paths=2,
    block_horizon_days=10,
    hour_horizon_years=1.0,
    grid_points=3,
    lhs_samples=4,
    halving_rounds=1,
    scenario_set="core",
    morris_trajectories=2,
    sobol_samples=8,
    max_minutes=1,
)


def env_for(tmp_path=None, seed=5, policy=None) -> Env:
    data = {"out_dir": str(tmp_path)} if tmp_path is not None else {}
    return Env(policy or Policy(), TINY, seed=seed, data=data)


# ---------------------------------------------------------------------------------------------------
# Building blocks


def test_prices_equal_engine():
    """The memoised median path equals sim.engine.simulate_blocks on the same tag stream."""
    G.clear_caches()
    env = env_for()
    r = G.realise(env, "feed-outage-6h", n_paths=2, horizon=12.0)
    ps = mainnet().replace(pFastWindow=48, pMidWindow=432, pSlowWindow=1584)
    p = G.prices(r, ps)
    inp = E.BlockInputs(r.true, r.valid, r.tag_price, r.tag_pool, r.valid, 3_075_000)
    s = E.simulate_blocks(ps, inp, activation_mode="always_active", attest_mode="unarmed")
    for name in ("p_fast", "p_mid", "p_slow", "x_mint", "x_claim"):
        assert np.array_equal(getattr(p, name), getattr(s, name)), name
    assert np.array_equal(p.halt3, (s.halt_mask & E.HALT_DIVERGENCE) != 0)
    assert np.array_equal(p.no_price, (s.halt_mask & E.HALT_NO_PRICE) != 0)
    assert p.no_price.any()  # the outage shows up


def test_tracking_lag_of_a_delayed_copy():
    n = 4000
    t = np.full((2, n), 1_000_000, dtype=np.int64)
    t[:, 1000:] = 300_000
    d = 150
    p = np.full_like(t, 1_000_000)
    p[:, 1000 + d :] = 300_000
    lags = G.tracking_lags(t, p, 1000, 1001)
    for fr in (0.5, 0.9):
        lag, cens = lags[fr]
        assert cens == 0 and np.all(lag == d)


def test_min_attack_share_v16():
    """V16: with everyone else quoting the threshold tends to 1/2 of the tagging share."""
    s = G.min_attack_share(2016, 1344, 0.8, "up")
    assert 0.38 < s < 0.41
    assert G.min_attack_share(96, 48, 0.8, "down") <= G.min_attack_share(96, 48, 0.8, "up") + 1e-3


def test_shipped_windows_meet_attack_share_min():
    """Sanity: the mainnet windows need ≥ attack_share_min (34 %) of hash to control any median."""
    pol = Policy()
    ps = mainnet()
    for w, f in G.FILLS.items():
        s = min(
            G.min_attack_share(ps.as_int(w), ps.as_int(f), pol.expected_enforcing_share, d)
            for d in ("up", "down")
        )
        assert s >= pol.attack_share_min, (w, s)


def test_space_includes_base_and_respects_invariants():
    st = G.make_study()
    base = mainnet()
    for b in ("quick", "standard"):
        cands = list(st.space(base, Budget.named(b)))
        assert cands[0] == base
        assert len(set(cands)) == len(cands)
        for c in cands:
            assert not c.check(), c.delta(base)
            assert c.as_int("pFastWindow") < c.as_int("pMidWindow") < c.as_int("pSlowWindow")


# ---------------------------------------------------------------------------------------------------
# Decision rule on hand-built tables


def _m(lag, over, *, ok=True, nph=1.0, prov="real-data", out=None):
    v = {
        "crash_lag_cvar_h": lag,
        "pump_overpricing_bps": over,
        "attack_share_min": 0.4,
        "no_price_h_per_year": nph,
    }
    c = {"attack_share_min": True, "max_no_price_hours": ok, "halt_recall_floor": True}
    return Metrics(v, "crash_lag_cvar_h", True, c, prov, {"out_dir": out, "budget": "standard"})  # type: ignore[arg-type]


def _table(rows, tmp_path, prov="real-data"):
    base = mainnet()
    t = ResultTable(base)
    for (f, m, s), (lag, over, ok) in rows:
        t.add(
            base.replace(pFastWindow=f, pMidWindow=m, pSlowWindow=s),
            _m(lag, over, ok=ok, prov=prov, out=str(tmp_path)),
        )
    return t


def _rec(recs, p):
    return next(r for r in recs if r.param == p)


def test_decide_change_when_material(tmp_path):
    t = _table([((96, 576, 2016), (40.0, 100.0, True)), ((96, 576, 1152), (20.0, 100.0, True))], tmp_path)
    recs = G.make_study().decide(t, Policy())
    assert _rec(recs, "pSlowWindow").recommended == 1152
    assert _rec(recs, "pSlowWindow").verdict == "CHANGE"
    assert _rec(recs, "pSlowMinFill").recommended == 768  # derived ⌈2W/3⌉
    assert _rec(recs, "pFastWindow").recommended == 96
    assert not missing_recommendations(G.make_study(), recs)


def test_decide_keep_within_materiality(tmp_path):
    # J(current) = 1 + 0.5; J(cand) = 0.9 + 0.5 → 6.7 % better < 20 %
    t = _table([((96, 576, 2016), (40.0, 100.0, True)), ((96, 576, 1152), (36.0, 100.0, True))], tmp_path)
    recs = G.make_study().decide(t, Policy())
    r = _rec(recs, "pSlowWindow")
    assert r.recommended == 2016 and r.verdict == "KEEP"
    assert "materiality" in r.binding


def test_decide_infeasible_better_row_names_binding(tmp_path):
    t = _table([((96, 576, 2016), (40.0, 100.0, True)), ((96, 288, 1152), (10.0, 50.0, False))], tmp_path)
    r = _rec(G.make_study().decide(t, Policy()), "pMidWindow")
    assert r.recommended == 576 and r.verdict == "KEEP"
    assert "max_no_price_hours" in r.binding


def test_decide_current_infeasible_moves(tmp_path):
    t = _table([((96, 576, 2016), (40.0, 100.0, False)), ((96, 1152, 2016), (40.0, 100.0, True))], tmp_path)
    r = _rec(G.make_study().decide(t, Policy()), "pMidWindow")
    assert r.recommended == 1152 and r.verdict == "CHANGE"


def test_decide_blocked_and_provisional(tmp_path):
    t = _table([((96, 576, 2016), (40.0, 100.0, False)), ((96, 1152, 2016), (40.0, 100.0, False))], tmp_path)
    assert {r.verdict for r in G.make_study().decide(t, Policy())} == {"BLOCKED"}
    t = _table(
        [((96, 576, 2016), (40.0, 100.0, True)), ((96, 576, 1152), (20.0, 100.0, True))],
        tmp_path,
        prov="synthetic",
    )
    recs = G.make_study().decide(t, Policy())
    assert {r.verdict for r in recs} == {"PROVISIONAL"}
    assert _rec(recs, "pSlowWindow").recommended == 1152


def test_objective_weights_overpricing(tmp_path):
    """λ trades pump overpricing against crash lag (both normalised by the current row)."""
    t = _table([((96, 576, 2016), (40.0, 100.0, True)), ((48, 576, 2016), (40.0, 20.0, True))], tmp_path)
    assert _rec(G.make_study().decide(t, Policy()), "pFastWindow").recommended == 48  # J 1.5 → 1.1
    r = _rec(G.make_study().decide(t, Policy(pump_overpricing_lambda=0.1)), "pFastWindow")
    assert r.recommended == 96  # J 1.1 → 1.02


# ---------------------------------------------------------------------------------------------------
# Tiny end-to-end run


@pytest.fixture(scope="module")
def tiny_run(tmp_path_factory):
    G.clear_caches()
    out = tmp_path_factory.mktemp("g1")
    env = env_for(out)
    st = load_study("G1")
    base = mainnet()
    t = ResultTable(base)
    for c in (base, base.replace(pFastWindow=48), base.replace(pMidWindow=1152)):
        t.add(c, st.evaluate(c, env))
    recs = st.decide(t, env.policy)
    return st, t, recs, out


def test_tiny_run_recommends_every_param(tiny_run):
    st, t, recs, out = tiny_run
    assert set(st.params) == set(params_for_group("G1"))
    assert not missing_recommendations(st, recs)
    for r in recs:
        assert r.verdict in ("PROVISIONAL", "BLOCKED")  # synthetic data only
        assert r.provenance == "synthetic" and r.rule and r.binding
        text = st.explain(r, t)
        assert r.param in text and len(text) > 100
    ev = recs[0].evidence
    assert any(str(p).endswith(".csv") for p in ev) and any(str(p).endswith(".png") for p in ev)
    assert all(p.exists() for p in ev) and str(ev[0]).startswith(str(out))


def test_tiny_run_metrics_are_sane(tiny_run):
    _, t, _, _ = tiny_run
    cur = t.current()
    v = cur.metrics.values
    assert v["attack_share_min"] >= Policy().attack_share_min
    assert v["lag_mint90_crash70_h"] < v["lag_claim90_crash70_h"]  # pMint = min tracks a fall first
    assert v["halt3_recall_crash70"] == 1.0
    assert v["no_price_h_per_feed_outage"] > 6.0  # a 6-hour outage at least
    assert set(cur.metrics.constraints) == {"attack_share_min", "max_no_price_hours", "halt_recall_floor"}


def test_determinism_per_seed():
    st = G.make_study()
    base = mainnet()
    G.clear_caches()
    a = st.evaluate(base, env_for(seed=11)).values
    G.clear_caches()
    b = st.evaluate(base, env_for(seed=11)).values
    G.clear_caches()
    c = st.evaluate(base, env_for(seed=12)).values
    assert a == b
    assert a != c


def _log(pattern: str, reps: int):
    """A PoolShareLog whose miner sequence is ``pattern`` (one letter per block) repeated."""
    from ybcal.data.loaders import PoolShareLog

    seq = pattern * reps
    keys = tuple(sorted(set(seq)))
    return PoolShareLog(
        np.arange(len(seq), dtype=np.int64), np.array([keys.index(c) for c in seq], dtype=np.int32), keys, "t"
    )


def test_pool_landscape_tagging_selection():
    """D-RD-ORA-1: the policy names who tags; else the largest keys until expected_enforcing_share."""
    from types import SimpleNamespace

    log = _log("AAAAABBBCCD" * 1, 100)  # A 45 %, B 27 %, C 18 %, D 9 %
    env = SimpleNamespace(policy=Policy(), data={"pool_shares": log})
    land = G.pool_landscape(env)
    assert land is not None and land.keys == ("A", "B", "C", "D")
    assert land.tagging == (True, True, True, False)  # 45 + 27 < 80 → C tags too
    env2 = SimpleNamespace(policy=Policy(enforcing_pools=("B", "D")), data={"pool_shares": log})
    land2 = G.pool_landscape(env2)
    assert land2.tagging == (False, True, False, True)
    assert abs(land2.tagging_share - 4 / 11) < 1e-9
    cfg = G.oracle_config(env2)
    assert [p.tags for p in cfg.pools] == [False, True, False, True]
    assert abs(cfg.tagging_share - 4 / 11) < 1e-9
    env3 = SimpleNamespace(policy=Policy(enforcing_pools=("Z",)), data={"pool_shares": log})
    assert "matched no key" in G.pool_landscape(env3).selection
    assert G.pool_landscape(SimpleNamespace(policy=Policy(), data={})) is None


def test_no_price_from_mask_is_exact():
    """Fill counts quotes only: a 2-of-3 tag pattern fails a ⌈2W/3⌉+1 fill everywhere, meets ⌈2W/3⌉."""
    m = np.tile([True, True, False], 400)
    tot, parts = G.no_price_from_mask(m, (6, 12, 24), (3, 8, 16))
    assert tot == 0.0 and parts == (0.0, 0.0, 0.0)
    tot, parts = G.no_price_from_mask(m, (6, 12, 24), (3, 8, 17))
    assert parts[2] == pytest.approx(G._PER_YEAR_H) and tot == pytest.approx(G._PER_YEAR_H)
    assert np.isnan(G.no_price_from_mask(m[:10], (6, 12, 24), (3, 8, 16))[0])


def test_landscape_metrics_single_pool_majority():
    """A key holding most quotes controls every median whatever the windows; withholding it
    leaves too few quotes for the fills."""
    from types import SimpleNamespace

    log = _log("AAAAABBBCCD", 600)
    env = SimpleNamespace(policy=Policy(enforcing_pools=("A", "B")), data={"pool_shares": log})
    lm = G.landscape_metrics(G.pool_landscape(env), mainnet())
    assert lm["top_pool_quote_share"] == pytest.approx(5 / 8)
    assert lm["top_pool_control_min"] > 0.95 and lm["attack_env_blocked"] == 1.0
    assert lm["no_price_top_withholds_h_per_year"] == pytest.approx(G._PER_YEAR_H)
    assert lm["no_price_all_tag_h_per_year"] == 0.0
    assert lm["no_price_seq_h_per_year"] == 0.0  # 8 of every 11 blocks tagged ≥ the ⌈2W/3⌉ fill


def test_oracle_miner_override_replays_a_sequence():
    """``generate_block_inputs(miner=…)``: blocks of a non-tagging pool or a stock miner carry no tag."""
    from ybcal.sim import oracle as O

    cfg = O.OracleConfig((O.Pool(share=0.5), O.Pool(share=0.3, tags=False, quotes=False)))
    true = np.full((2, 50), 400_000, dtype=np.int64)
    seq = np.array([0, 1, -1, 0, 7] * 10)
    inp = O.generate_block_inputs(true, cfg, rng=np.random.default_rng(1), miner=seq)
    assert (inp.tag_present[0] == (seq == 0)).all() and (inp.tag_present[1] == (seq == 0)).all()
    assert (inp.tag_pool[0][seq == 0] == 0).all()


def test_environment_adjust_picks_least_harm(tmp_path):
    """D-RD-ORA-2: every set violates both environment constraints → BLOCKED, but the value is the
    least-harm set (lowest NO_PRICE band, then J), not the current one."""
    base = mainnet()
    t = ResultTable(base)

    def m(lag, nph):
        v = {
            "crash_lag_cvar_h": lag,
            "pump_overpricing_bps": 100.0,
            "attack_share_min": 0.36,
            "no_price_h_per_year": nph,
            "attack_env_blocked": 1.0,
            "top_pool_quote_share": 0.72,
            "top_pool_share": 0.52,
            "top_pool_control_min": 1.0,
        }
        c = {"attack_share_min": False, "max_no_price_hours": False, "halt_recall_floor": True}
        meta = {"out_dir": str(tmp_path), "budget": "standard"}
        return Metrics(v, "crash_lag_cvar_h", True, c, "real-data", meta)  # type: ignore[arg-type]

    t.add(base, m(40.0, 1500.0))
    t.add(base.replace(pMidWindow=1152), m(41.0, 1100.0))
    t.add(base.replace(pMidWindow=864), m(39.0, 1400.0))
    recs = G.make_study().decide(t, Policy())
    r = _rec(recs, "pMidWindow")
    assert r.verdict == "BLOCKED" and r.recommended == 1152
    assert "environment-blocked" in r.binding
    assert any("D-RD-ORA-2" in n for n in r.notes)
    # nothing environmental → the ordinary rule (BLOCKED keeps current)
    t2 = _table([((96, 576, 2016), (40.0, 100.0, False)), ((96, 1152, 2016), (41.0, 100.0, False))], tmp_path)
    for row in t2:
        row.metrics.values["no_price_h_per_year"] = 1.0  # type: ignore[index]
    assert _rec(G.make_study().decide(t2, Policy()), "pMidWindow").recommended == 576




def test_rogue_majority_pool_captures_every_median():
    """D-RD-ORA-4: a pool with most of the quotes moves every median by its bias (the windows only
    delay it) and, withholding, leaves the medians undefined."""
    log = _log("AAAAAAABBC", 2000)  # A 70 % of blocks
    env = Env(Policy(enforcing_pools=("A", "B")), TINY, seed=7, data={"pool_shares": log})
    G.clear_caches()
    m = G.rogue_metrics(env, mainnet())
    assert m["rogue_attack_days"] == pytest.approx(20.0, abs=0.01)
    assert m["rogue_up_mint_share"] > 0.8 and m["rogue_down_claim_share"] > 0.8
    assert m["rogue_up_capture_h_fast"] < m["rogue_up_capture_h_mid"] < m["rogue_up_capture_h_slow"] < 48
    assert m["rogue_withhold_noprice_share"] > 0.95 and m["rogue_withhold_onset_h"] < 2
    assert G.rogue_metrics(env_for(), mainnet()) == {}

"""Scenario library: every file loads and generates deterministically; program semantics."""

from __future__ import annotations

import math

import numpy as np
import pytest

from ybcal.data import scenarios as S
from ybcal.data.pricepath import make_path
from ybcal.data.synthetic import SYNTH_T0
from ybcal.studies.base import BUDGETS

PLAN_TABLE = [  # PLAN §4.3 — every row must be shipped (families by prefix)
    "calm-90d",
    "crash-70-1d",
    "crash-90-30d",
    "slow-bleed-95-2y",
    "pump-dump-3x",
    "flash-wick-50-1h",
    "feed-outage-6h",
    "stale-pools",
    "oracle-attack-",
    "attestor-outage-",
    "attestor-capture-",
    "hashrate-drop-",
    "dev-absence-",
    "owner-absence",
    "sunset-no-renewal",
]

LIB = S.load_library()


def test_every_plan_scenario_is_shipped():
    names = set(LIB)
    for want in PLAN_TABLE:
        if want.endswith("-"):
            assert sum(n.startswith(want) for n in names) >= 3, want
        else:
            assert want in names, want


def test_families_and_core_set():
    fam = S.families(LIB)
    assert {"oracle-attack", "attestor-outage", "attestor-capture", "hashrate-drop", "dev-absence"} <= set(
        fam
    )
    core = S.scenario_set("core")
    assert {"calm-90d", "crash-70-1d", "oracle-attack-34", "attestor-outage-1", "hashrate-drop-45"} <= set(
        core
    )
    assert set(core) < set(S.scenario_set("all"))
    assert all(BUDGETS[b].scenario_set in ("core", "all") for b in BUDGETS)


@pytest.mark.parametrize("name", sorted(LIB))
def test_each_scenario_generates_deterministically(name):
    sc = LIB[name]
    assert sc.description and sc.stresses
    a = sc.generate(np.random.default_rng(42), n_paths=2)
    b = sc.generate(np.random.default_rng(42), n_paths=2)
    c = sc.generate(np.random.default_rng(43), n_paths=2)
    n = sc.n_steps()
    assert a.paths.prices.shape == (2, n) and a.paths.provenance == "scenario"
    assert a.paths.resolution == sc.resolution and a.paths.meta["scenario"] == name
    assert (a.paths.prices[:, 0] == sc.p0).all()
    assert np.array_equal(a.paths.prices, b.paths.prices)
    assert sc.base.model == "flat" or not np.array_equal(a.paths.prices, c.paths.prices)
    for k, v in a.schedules.items():
        assert k in S.SCHEDULES and v.shape[-1] == n and np.array_equal(v, b.schedules[k])
    # one file serves both resolutions
    other = "hour" if sc.resolution == "block" else "block"
    short = min(sc.horizon_days, 20)
    r2 = sc.generate(np.random.default_rng(1), resolution=other, horizon_days=short)
    assert r2.paths.resolution == other and r2.paths.n_steps == sc.n_steps(other, short)


def _flat(doc_extra: dict, horizon=10, res="hour") -> S.Scenario:
    doc = {
        "name": "t",
        "description": "t",
        "horizon_days": horizon,
        "resolution": res,
        "base": {"model": "flat"},
        **doc_extra,
    }
    return S.parse_scenario(doc)


def _prices(sc, **kw):
    return sc.generate(np.random.default_rng(0), **kw).paths.prices[0].astype(float) / 1e6


def test_shock_ramp_wick_hold_semantics():
    p = _prices(_flat({"segments": [{"kind": "shock", "at_days": 2, "pct": -50}]}))
    assert p[47] == pytest.approx(0.4) and p[48] == pytest.approx(0.2) and p[-1] == pytest.approx(0.2)
    p = _prices(_flat({"segments": [{"kind": "ramp", "start_days": 1, "duration_days": 2, "pct": -75}]}))
    assert (
        p[24] == pytest.approx(0.4)
        and p[48] == pytest.approx(0.2, rel=1e-4)
        and p[72] == pytest.approx(0.1, rel=1e-4)
    )
    p = _prices(_flat({"segments": [{"kind": "wick", "at_hours": 5, "pct": -50, "duration_hours": 4}]}))
    assert p[4] == pytest.approx(0.4) and p[5] == pytest.approx(0.2) and p[9] == pytest.approx(0.4, rel=1e-5)
    p = _prices(
        _flat(
            {
                "segments": [
                    {"kind": "ramp", "start_days": 0, "pct": 100, "duration_days": 4},
                    {"kind": "hold", "start_days": 1, "duration_days": 1},
                ]
            }
        )
    )
    assert p[24] == pytest.approx(p[48]) and p[48] < p[72]
    p = _prices(_flat({"segments": [{"kind": "drift", "rate": -8.76, "start_days": 0}]}))
    assert p[1] == pytest.approx(0.4 * math.exp(-0.001), rel=1e-5)


def test_vol_scales_base_only():
    doc = {
        "name": "v",
        "description": "v",
        "horizon_days": 5,
        "resolution": "hour",
        "base": {"model": "gbm", "params": {"sigma": 0.5}},
        "segments": [{"kind": "vol", "start_days": 0, "mult": 3.0}],
    }
    sc = S.parse_scenario(doc)
    base = S.parse_scenario({**doc, "segments": []})
    r = np.diff(np.log(sc.generate(np.random.default_rng(3), 50).paths.prices.astype(float)))
    r0 = np.diff(np.log(base.generate(np.random.default_rng(3), 50).paths.prices.astype(float)))
    assert r.std() / r0.std() == pytest.approx(3.0, rel=0.02)


def test_centering_removes_base_log_drift():
    doc = {
        "name": "c",
        "description": "c",
        "horizon_days": 100,
        "resolution": "hour",
        "base": {"model": "gbm", "params": {"sigma": 1.2}},
    }
    lp = np.log(S.parse_scenario(doc).generate(np.random.default_rng(4), 400).paths.prices[:, -1] / 400_000)
    assert abs(lp.mean()) < 0.1  # uncentred would be −0.72·100/365 ≈ −0.2
    doc["base"]["center"] = False
    lp2 = np.log(S.parse_scenario(doc).generate(np.random.default_rng(4), 400).paths.prices[:, -1] / 400_000)
    assert lp2.mean() == pytest.approx(-0.72 * 100 / 365, abs=0.1)


def test_schedules_changes_ramps_outages():
    sc = _flat(
        {
            "schedules": {
                "enforcing_share": {
                    "default": 0.8,
                    "changes": [{"at_days": 2, "value": 0.45}],
                    "ramps": [{"start_days": 5, "duration_days": 1, "to": 0.8}],
                },
                "feed_up": {"default": 1, "outages": {"rate_per_day": 2.0, "mean_hours": 3.0, "value": 0}},
            }
        }
    )
    run = sc.generate(np.random.default_rng(5), n_paths=3)
    e = run.schedules["enforcing_share"]
    assert e.shape == (241,) and e[47] == 0.8 and e[48] == 0.45 and e[119] == 0.45
    assert e[120] == pytest.approx(0.45 + 0.35 / 24) and e[144] == pytest.approx(0.8) and e[-1] == 0.8
    f = run.schedules["feed_up"]
    assert f.shape == (3, 241) and set(np.unique(f)) <= {0.0, 1.0} and f.min() == 0.0


def test_crash_and_pump_programs_move_prices():
    crash = LIB["crash-70-1d"].generate(np.random.default_rng(6), n_paths=20, resolution="hour")
    p = crash.paths.prices.astype(float)
    ratio = np.median(p[:, 21 * 24] / p[:, 20 * 24])
    assert ratio == pytest.approx(0.3, rel=0.15)
    pump = LIB["pump-dump-3x"].generate(np.random.default_rng(7), n_paths=20, resolution="hour")
    q = pump.paths.prices.astype(float)
    assert np.median(q[:, 25 * 24] / q[:, 15 * 24]) == pytest.approx(3.0, rel=0.2)


def test_variant_overrides_and_feed_outage():
    sc = LIB["oracle-attack-34"]
    run = sc.generate(np.random.default_rng(8))
    a = run.schedules["attacker_share"]
    step = 86400 // 75
    assert a[5 * step - 1] == 0 and a[5 * step] == pytest.approx(0.34) and a[25 * step] == 0
    assert run.constants["attacker_share"] == 0.34 and sc.family == "oracle-attack"
    f = LIB["feed-outage-6h"].generate(np.random.default_rng(9)).schedules["feed_up"]
    assert (f == 0).sum() == 6 * 48
    d = LIB["dev-absence-90"]
    dev = d.generate(np.random.default_rng(10)).schedules["dev_present"]
    assert (dev == 0).sum() == 90 * 24 and d.horizon_days == 180


def test_base_path_replay_and_bootstrap_fallback():
    sc = LIB["crash-70-1d"]
    base = make_path(SYNTH_T0, "hour", np.full((1, 61 * 24), 400_000), "real")
    run = sc.generate(np.random.default_rng(11), n_paths=2, base_path=base, resolution="hour")
    p = run.paths.prices
    assert np.array_equal(p[0], p[1]) and run.paths.meta["base_model"] == "base_path"
    assert p[0, 21 * 24] == pytest.approx(120_000, rel=1e-4)  # pure program on a flat history
    boot = S.parse_scenario(
        {
            "name": "b",
            "description": "b",
            "horizon_days": 5,
            "resolution": "hour",
            "base": {"model": "bootstrap", "fallback": "gbm"},
        }
    )
    r = boot.generate(np.random.default_rng(12))
    assert r.paths.meta["base_fallback"] is True and r.paths.meta["base_model"] == "gbm"
    real = make_path(SYNTH_T0, "hour", np.random.default_rng(1).integers(390_000, 410_000, (1, 2000)), "real")
    r2 = boot.generate(np.random.default_rng(12), data=real)
    assert r2.paths.meta["base_provenance"] == "real"


@pytest.mark.parametrize(
    "bad",
    [
        {"nosuch": 1},
        {"resolution": "day"},
        {"horizon_days": 200},
        {"base": {"model": "nosuch"}},
        {"base": {"model": "gbm", "params": {"nosuch": 1}}},
        {"segments": [{"kind": "teleport"}]},
        {"segments": [{"kind": "shock"}]},
        {"schedules": {"nosuch_share": 1}},
        {"schedules": {"feed_up": {"default": 1, "typo": 2}}},
    ],
)
def test_validation_errors(bad):
    doc = {"name": "x", "description": "x", "horizon_days": 10, "resolution": "block", **bad}
    with pytest.raises(ValueError):
        S.parse_scenario(doc)


def test_duplicate_names_rejected(tmp_path):
    body = 'name = "a"\ndescription = "a"\nhorizon_days = 1\n'
    (tmp_path / "a.toml").write_text(body)
    (tmp_path / "b.toml").write_text(body)
    with pytest.raises(ValueError, match="duplicate"):
        S.load_library(tmp_path)
    with pytest.raises(KeyError):
        S.get("nope")


def test_replay_base_takes_a_real_window_in_order():
    """D-RD-ORA-4: ``model = "replay"`` replays real hourly returns from ``start`` or from the
    ``select``-ed window, identical on every path; without data it falls back to the preset."""
    from datetime import UTC, datetime

    from ybcal.types import PricePath

    hours = 24 * 200
    p = np.full(hours, 400_000, dtype=np.int64)
    p[24 * 100 : 24 * 110] = np.linspace(400_000, 100_000, 240).astype(np.int64)  # the worst fall
    p[24 * 110 :] = 100_000
    t0 = datetime(2025, 1, 1, tzinfo=UTC)
    data = PricePath(t0, "hour", p[None, :], "real", {})
    doc = {
        "name": "r",
        "description": "r",
        "horizon_days": 20,
        "resolution": "hour",
        "base": {"model": "replay", "params": {"select": "worst_drawdown", "window_days": 20}},
    }
    sc = S.parse_scenario(doc)
    run = sc.generate(np.random.default_rng(0), n_paths=3, data=data)
    x = run.paths.prices
    assert (x[0] == x[1]).all() and (x[0] == x[2]).all()
    assert x[0, -1] < 0.3 * x[0, 0]  # the 75 % fall is inside the window
    a = run.paths.meta["replay_start_hour"]
    assert 24 * 89 <= a <= 24 * 100
    doc["base"] = {"model": "replay", "params": {"start": "2025-04-11T00:00:00+00:00"}}  # day 100
    run2 = S.parse_scenario(doc).generate(np.random.default_rng(0), n_paths=1, data=data)
    assert run2.paths.meta["replay_start_hour"] == 24 * 100
    blk = S.parse_scenario({**doc, "resolution": "block"}).generate(np.random.default_rng(0), data=data)
    assert blk.paths.prices[0, -1] < 0.3 * blk.paths.prices[0, 0]
    fb = S.parse_scenario(doc).generate(np.random.default_rng(0), n_paths=1)
    assert fb.paths.meta.get("base_fallback") is True
    for bad in ({"start": "2025-01-01", "select": "worst_drawdown"}, {}, {"select": "nope"}, {"x": 1}):
        with pytest.raises(ValueError):
            S.parse_scenario({**doc, "base": {"model": "replay", "params": bad}})

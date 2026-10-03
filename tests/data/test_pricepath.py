"""PricePath helpers: units, clamp, resampling, slicing, persistence."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from ybcal.data import pricepath as pp_mod
from ybcal.data.pricepath import (
    DT_BLOCK,
    DT_HOUR,
    clamp_prices,
    from_usd,
    log_returns,
    make_path,
    resample,
    resolution_for_dt,
    select_paths,
    slice_steps,
    timestamps,
    usd_to_micro,
)
from ybcal.types import PricePath
from ybcal.units import PRICE_MAX, PRICE_MIN

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_reexports_frozen_type():
    assert pp_mod.PricePath is PricePath


def test_usd_to_micro_rounds_clamps_and_gaps():
    out = usd_to_micro([0.4321234, 1e-9, 1e9, 0.0, -1.0, float("nan")])
    assert out.dtype == np.int64
    assert out.tolist() == [432123, PRICE_MIN, PRICE_MAX, 0, 0, 0]


def test_clamp_keeps_gaps_and_rejects_floats():
    assert clamp_prices(np.array([0, 5, 200_000_000])).tolist() == [0, PRICE_MIN, PRICE_MAX]
    assert clamp_prices(np.array([0, 5]), keep_gaps=False).tolist() == [PRICE_MIN, PRICE_MIN]
    with pytest.raises(TypeError):
        clamp_prices(np.array([0.5]))


def test_resolution_for_dt():
    assert resolution_for_dt(DT_BLOCK) == "block"
    assert resolution_for_dt(DT_HOUR) == "hour"
    assert pytest.approx(1 / 8760) == DT_HOUR
    with pytest.raises(ValueError):
        resolution_for_dt(1 / 365)


def test_timestamps_and_slicing_move_t0():
    p = make_path(T0, "hour", np.arange(1, 11) * 1000, "real", {"filled": np.zeros(10, dtype=bool)})
    ts = timestamps(p)
    assert ts[1] - ts[0] == 3600 and ts[0] == int(T0.timestamp())
    s = slice_steps(p, 3, 7)
    assert s.n_steps == 4 and s.prices[0, 0] == 4000
    assert int(s.t0.timestamp()) == ts[3]
    assert s.meta["filled"].shape == (4,)


def test_select_paths_keeps_2d():
    p = make_path(T0, "block", np.array([[100, 200], [300, 400], [500, 600]]), "synthetic")
    s = select_paths(p, 1)
    assert s.prices.shape == (1, 2) and s.prices[0, 0] == 300


def test_resample_hold_round_trip_is_exact():
    rng = np.random.default_rng(0)
    p = make_path(
        T0,
        "hour",
        rng.integers(1_000, 1_000_000, size=(3, 50)),
        "synthetic",
        {"filled": rng.random(50) < 0.3},
    )
    b = resample(p, "block")
    assert b.resolution == "block" and b.n_steps == 50 * 48
    assert np.array_equal(b.prices[:, :48], np.repeat(p.prices[:, :1], 48, axis=1))
    assert b.meta["filled"].shape == (2400,)
    back = resample(b, "hour")
    assert np.array_equal(back.prices, p.prices)
    assert np.array_equal(back.meta["filled"], p.meta["filled"])


def test_resample_loglinear_interpolates_and_keeps_gaps():
    p = make_path(T0, "hour", np.array([[100_000, 400_000, 0, 400_000]]), "real")
    b = resample(p, "block", method="loglinear")
    assert b.prices[0, 0] == 100_000
    assert b.prices[0, 24] == pytest.approx(200_000, rel=1e-6)  # geometric midpoint
    assert (b.prices[0, 96:144] == 0).all()
    assert b.prices[0, -1] == 400_000


def test_log_returns_nan_on_gaps():
    r = log_returns(np.array([100, 200, 0, 400]))
    assert r[0, 0] == pytest.approx(np.log(2))
    assert np.isnan(r[0, 1]) and np.isnan(r[0, 2])


def test_csv_round_trip_single_and_multi(tmp_path):
    single = from_usd(T0, "hour", np.array([0.4, 0.41, np.nan, 0.39]), "real", {"source_file": "x"})
    f = pp_mod.to_csv(single, tmp_path / "s.csv")
    assert f.read_text().splitlines()[0] == "ts_iso,ts,price_usd"
    back = pp_mod.from_csv(f)
    assert np.array_equal(back.prices, single.prices) and back.provenance == "real"
    assert back.t0 == single.t0 and back.resolution == "hour"
    multi = make_path(
        T0, "block", np.array([[100, 200, 300], [400, 500, 600]]), "synthetic", {"model": "gbm"}
    )
    f2 = pp_mod.to_csv(multi, tmp_path / "m.csv")
    back2 = pp_mod.from_csv(f2)
    assert np.array_equal(back2.prices, multi.prices) and back2.meta["model"] == "gbm"


def test_csv_without_sidecar_infers_grid(tmp_path):
    single = from_usd(T0, "hour", np.array([0.4, 0.41, 0.42]), "real")
    f = pp_mod.to_csv(single, tmp_path / "s.csv", sidecar=False)
    back = pp_mod.from_csv(f)
    assert back.resolution == "hour" and np.array_equal(back.prices, single.prices)


def test_npz_round_trip(tmp_path):
    p = make_path(
        T0,
        "hour",
        np.arange(1, 7).reshape(2, 3) * 1000,
        "scenario",
        {"filled": np.array([True, False, False]), "scenario": "x"},
    )
    f = pp_mod.save(p, tmp_path / "p.npz")
    back = pp_mod.load(f)
    assert np.array_equal(back.prices, p.prices) and back.provenance == "scenario"
    assert back.meta["scenario"] == "x" and back.meta["filled"].tolist() == [True, False, False]


def test_load_falls_back_to_price_loader(tmp_path):
    f = tmp_path / "raw.csv"
    f.write_text("ts,price_usd\n1700000000,0.4\n1700003600,0.5\n1700010800,0.6\n")
    p = pp_mod.load(f)
    assert p.resolution == "hour" and p.provenance == "real"

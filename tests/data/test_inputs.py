"""Price inputs by role and data windows (D-RD-INF-1, D-RD-INF-5)."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from ybcal.data import inputs as IN
from ybcal.data import loaders as L
from ybcal.data import pricepath as PP
from ybcal.data import synthetic as SY
from ybcal.report.build import load_data

T0 = 1_600_000_000 - 1_600_000_000 % 86400  # a UTC midnight (2020-09-13)


def write_series(path: Path, step: int, n: int, seed: int, t0: int = T0, sigma: float = 0.03) -> Path:
    rng = np.random.default_rng(seed)
    p = 0.5 * np.exp(np.cumsum(rng.normal(0, sigma, n)))
    with path.open("w") as fh:
        fh.write("ts,price_usd\n")
        for i in range(n):
            fh.write(f"{t0 + i * step},{p[i]:.8f}\n")
    return path


@pytest.fixture
def files(tmp_path):
    hourly = write_series(tmp_path / "h.csv", 3600, 24 * 400, 1, t0=T0 + 300 * 86400, sigma=0.01)
    daily = write_series(tmp_path / "d.csv", 86400, 1000, 2)
    return hourly, daily


def test_roles_by_native_granularity_not_file_order(files):
    hourly, daily = files
    for order in ([hourly, daily], [daily, hourly]):
        data, prov, infos = load_data(order)
        assert prov == "real-data"
        assert set(data) == {"price", "price_daily"}
        assert data["price"].meta["native_step_seconds"] == 3600
        assert data["price_daily"].meta["native_step_seconds"] == 86400
        assert data["price"].meta["source_file"].endswith("h.csv")
        assert data["price_daily"].meta["source_file"].endswith("d.csv")
        kinds = {i.key: i.kind for i in infos}
        assert kinds == {
            "price": "price [price, native 1 h]",
            "price_daily": "price [price_daily, native 1 d]",
        }


def test_single_daily_file_is_the_price(files):
    _, daily = files
    data, _, _ = load_data([daily])
    assert set(data) == {"price"}
    assert data["price"].meta["native_step_seconds"] == 86400


def test_two_series_for_one_role_is_an_error(tmp_path, files):
    hourly, _ = files
    other = write_series(tmp_path / "h2.csv", 3600, 100, 3)
    with pytest.raises(ValueError, match="two price series for role 'price'"):
        load_data([hourly, other])


def test_daily_on_hourly_grid_fits_daily_returns_and_survives_the_stale_filter(files):
    """The wave-1 bug: the daily series on the hourly grid lost its mask and the stale-run filter
    (≥ 6 exact-zero returns) emptied it. Observed-to-observed returns are daily, none dropped."""
    _, daily = files
    data, _, _ = load_data([daily])
    pp = data["price"]
    r, dt = SY.fit_returns_of(pp)
    assert math.isclose(dt * 365, 1.0)
    assert len(r) == 999
    # without the mask the hourly-grid returns are 23 zeros per day and the filter empties them
    flat = PP.make_path(pp.t0, "hour", pp.prices, "real")
    r2, _ = SY.fit_returns_of(flat)
    assert len(r2) < 50
    bb = SY.BlockBootstrap.fit(pp)
    assert bb.params()["n_returns"] == 999


def test_block_resample_keeps_observed_to_observed(files):
    """Hourly data resampled to the block grid: 47 held copies per hour are not observations."""
    hourly, _ = files
    data, _, _ = load_data([hourly])
    b = PP.resample(data["price"], "block")
    r, dt = SY.fit_returns_of(b)
    assert math.isclose(dt * 365 * 24, 1.0)
    assert len(r) == data["price"].n_steps - 1
    back = PP.resample(b, "hour")
    assert np.array_equal(back.meta["filled"], data["price"].meta["filled"])


def test_g3_ensemble_on_a_daily_series(files):
    """G3's real-data ensemble used to raise "empty return series" on a daily price (rd0.log)."""
    from ybcal.config import Policy
    from ybcal.params.paramset import mainnet
    from ybcal.studies import g3_collateral as G3
    from ybcal.studies.base import Budget, Env

    _, daily = files
    data, prov, _ = load_data([daily])
    tiny = Budget("quick", 8, 5, 0.2, 3, 4, 1, "core", 2, 8, 1)
    env = Env(Policy(), tiny, 7, data=data, provenance=prov)
    G3.clear_caches()
    ens = G3.ensemble(env, mainnet())
    assert ens.provenance == "real-data"
    assert np.isfinite(ens.true["bootstrap"]).all()


def test_long_price_prefers_the_daily_series(files):
    from ybcal.config import Policy
    from ybcal.studies import g1_price_windows as G1
    from ybcal.studies.base import BUDGETS, Env

    hourly, daily = files
    data, prov, _ = load_data([hourly, daily])
    env = Env(Policy(), BUDGETS["quick"], 1, data=data, provenance=prov)
    assert G1.long_price(env) is data["price_daily"]
    assert G1.real_price(env) is data["price"]
    fp_both = G1.data_fingerprint(env)
    data1, _, _ = load_data([hourly])
    env1 = Env(Policy(), BUDGETS["quick"], 1, data=data1, provenance=prov)
    assert G1.long_price(env1) is data1["price"]
    assert G1.data_fingerprint(env1) != fp_both


@pytest.mark.parametrize(
    "text, name",
    [("full", None), (None, None), ("last365", "last365"), ("2021-22", "2021-22"), ("last30", "last30")],
)
def test_parse_window(text, name):
    w = IN.parse_window(text)
    assert (w.name if w else None) == name


def test_parse_window_dates_and_errors():
    w = IN.parse_window("2021-01-01:2021-07-01")
    assert w.start == IN._ts("2021-01-01") and w.end == IN._ts("2021-07-01")
    assert IN.parse_window(":2021-07-01").start is None
    with pytest.raises(ValueError):
        IN.parse_window("someday")
    with pytest.raises(ValueError):
        IN.parse_window("2021-13-01:")


def test_window_applies_to_every_price_role(files):
    hourly, daily = files
    w = IN.parse_window("last365")
    data, _, infos = load_data([hourly, daily], w)
    latest = int(L.load_price_csv(hourly).ts[-1])
    for role in ("price", "price_daily"):
        ts = PP.timestamps(data[role])
        assert ts[0] >= latest - 365 * 86400 - 3600
        assert "last365" in data[role].meta["window"]
    assert all("window last365" in i.gaps for i in infos)


def test_window_with_no_data_is_an_error(files):
    hourly, _ = files
    with pytest.raises(ValueError, match="fewer than 3 observations"):
        load_data([hourly], IN.parse_window("2010-01-01:2011-01-01"))


def test_assign_roles_unit():
    def ser(step, n, name):
        return L.PriceSeries(np.arange(n, dtype=np.int64) * step + T0, np.ones(n), name)

    h, d, d2 = ser(3600, 50, "h"), ser(86400, 50, "d"), ser(86400, 60, "d2")
    assert IN.assign_roles([d, h]) == {"price": h, "price_daily": d}
    assert IN.assign_roles([d]) == {"price": d}
    with pytest.raises(ValueError, match="price_daily"):
        IN.assign_roles([h, d, d2])
    assert IN.step_label(3600) == "1 h" and IN.step_label(86400) == "1 d" and IN.step_label(300) == "5 min"

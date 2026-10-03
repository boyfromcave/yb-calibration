"""describe: realised vol, drawdowns, Hill tail index, autocorrelation, gaps."""

from __future__ import annotations

import math

import numpy as np
import pytest

from ybcal.data import describe as D
from ybcal.data.pricepath import make_path
from ybcal.data.synthetic import GBM, SYNTH_T0


def test_describe_on_known_gbm():
    pp = GBM(mu=0.0, sigma=0.8).simulate(1, 3 * 8760 + 1, "hour", np.random.default_rng(1))
    d = D.describe(pp)
    assert d["realised_vol_annual"] == pytest.approx(0.8, rel=0.03)
    assert abs(d["excess_kurtosis"]) < 0.2 and abs(d["skew"]) < 0.1
    assert d["hill_lower"] > 3.5  # thin Gaussian tails
    assert abs(d["acf_returns"]["1"]) < 0.03
    dd = d["drawdowns"]
    assert dd["30"]["n"] > 1000 and dd["1825"]["n"] == 0
    assert dd["30"]["p50"] < dd["365"]["p50"] < 1
    # GBM 30-day median drawdown ≈ σ√T scale: between 0.5σ√T and 2σ√T
    s = 0.8 * math.sqrt(30 / 365)
    assert 0.5 * s < dd["30"]["p50"] < 2 * s
    text = D.format_description(d)
    assert "realised vol" in text and "max drawdown by horizon" in text
    assert '"realised_vol_annual"' in D.to_json(d)


def test_max_drawdown_and_gaps():
    assert D.max_drawdown(np.array([100, 200, 50, 300, 150])) == pytest.approx(0.75)
    assert D.max_drawdown(np.array([100, 0, 50])) == pytest.approx(0.5)
    pp = make_path(
        SYNTH_T0,
        "hour",
        np.array([[100, 0, 0, 120, 0]]),
        "real",
        {"filled": np.array([False, True, True, False, True])},
    )
    g = D.gap_stats(pp)
    assert g["zero_steps"] == 3 and g["longest_gap_steps"] == 2 and g["filled_fraction"] == pytest.approx(0.6)


def test_hill_on_pareto_tail():
    rng = np.random.default_rng(2)
    x = rng.pareto(3.0, 200_000) + 1.0
    assert D.hill_tail_index(-x, "lower", k=2000) == pytest.approx(3.0, rel=0.1)
    assert D.hill_tail_index(x, "upper", k=2000) == pytest.approx(3.0, rel=0.1)
    assert math.isnan(D.hill_tail_index(np.array([1.0, 2.0]), "both"))


def test_autocorrelation_ar1():
    rng = np.random.default_rng(3)
    e = rng.standard_normal(50_000)
    x = np.empty_like(e)
    x[0] = e[0]
    for t in range(1, len(e)):
        x[t] = 0.6 * x[t - 1] + e[t]
    ac = D.autocorrelation(x, (1, 2))
    assert ac[1] == pytest.approx(0.6, abs=0.02) and ac[2] == pytest.approx(0.36, abs=0.02)


def test_rolling_vol_and_drawdown_distribution_shapes():
    pp = GBM(sigma=0.5).simulate(3, 24 * 100 + 1, "hour", np.random.default_rng(4))
    rv = D.rolling_vol(pp, 24 * 7)
    assert np.isnan(rv[0]) and rv[-1] == pytest.approx(0.5, rel=0.5)
    dist = D.drawdown_distribution(pp, (30, 365))
    assert dist[30]["n"] == 3 * len(range(0, 2401 - 720, 24)) and dist[365] == {"n": 0}

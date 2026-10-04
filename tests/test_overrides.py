"""--set policy overrides and real_price_model (D-RD-INF-5)."""

from __future__ import annotations

import numpy as np
import pytest

from ybcal.config import Policy
from ybcal.data import pricepath as PP
from ybcal.data import synthetic as SY
from ybcal.studies import g1_price_windows as G1
from ybcal.studies.base import BUDGETS, Env


def test_with_overrides_parses_toml_values():
    p, o = Policy().with_overrides(["price_drift='martingale'", "materiality=0.1", "owner_pinned={}",
                                    "real_price_model=regime"])
    got = (p.price_drift, p.materiality, p.owner_pinned, p.real_price_model)
    assert got == ("martingale", 0.1, {}, "regime")
    assert o["materiality"] == 0.1
    with pytest.raises(KeyError):
        Policy().with_overrides(["nope=1"])
    with pytest.raises(ValueError):
        Policy().with_overrides(["materiality"])


def _env(kind: str) -> Env:
    rng = np.random.default_rng(3)
    p = (0.5 * np.exp(np.cumsum(rng.normal(0, 0.05, 900)))) * 1e6
    pp = PP.make_path(SY.SYNTH_T0, "hour", p.astype(np.int64)[None, :], "real")
    return Env(Policy(real_price_model=kind), BUDGETS["quick"], 1, data={"price": pp}, provenance="real-data")


def test_real_model_kinds_and_fingerprint():
    b, r = _env("bootstrap"), _env("regime")
    assert isinstance(G1.real_model(b), SY.BlockBootstrap)
    m = G1.real_model(r)
    assert isinstance(m, SY.Centred) and m.expected_log_drift() == 0.0
    x = m.log_returns(200, 2000, "hour", np.random.default_rng(1))
    assert abs(x.mean()) < 5 * x.std() / np.sqrt(x.size)
    assert G1.data_fingerprint(b) != G1.data_fingerprint(r)
    with pytest.raises(ValueError):
        G1.real_model(_env("nope"))

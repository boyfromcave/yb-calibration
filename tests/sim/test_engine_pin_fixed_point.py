"""PIN-1/PIN-2 coupling (D-WP5-3 item 2, WP-8): the engine iterates oracle → attest → PIN-1 → medians
until the PIN-2 trigger mask attestation reads stops changing, so its output is the fixed point."""

from __future__ import annotations

import numpy as np
import pytest

from ybcal.params.paramset import regtest
from ybcal.sim import attest as att
from ybcal.sim import engine as E
from ybcal.sim import oracle as O


def scenario(seed: int = 3, n: int = 700, crash: float = 0.6):
    ps = regtest()
    t = np.arange(n)
    tp = np.where(t < 300, 50_000, 50_000 * np.maximum(crash, 1 - (t - 300) * (1 - crash) / 30)).astype(
        np.int64
    )
    tp = np.vstack([tp, tp])
    roster = [{"bond_zat": 10**9 * (1 + i % 2), "register_height": 3 + i} for i in range(6)]
    a = {
        "roster": roster,
        "true_price": tp,
        "demand_rate": 0.5,
        "seed": seed,
        "height0": 1,
        "start_height": 1,
        "uptime": 0.97,
        "noise_bps": 20,
    }
    inp = O.generate_block_inputs(
        tp, O.OracleConfig.honest(4, 0.9), rng=np.random.default_rng(seed), start_height=1, attest=a
    )
    return ps, inp


def test_engine_output_is_the_pin_fixed_point():
    ps, inp = scenario()
    s = E.simulate_blocks(ps, inp, activation_mode="always_active")
    assert s.attest_source == "wp5" and s.armed.any()
    mask = E.pin2_trigger_mask(ps, s.x_mint, 1, 1)
    assert mask.any(), "scenario must make PIN-2 fire"
    assert s.extras["pin_fixed_point"] and s.extras["pin_passes"] >= 2
    # fixed point: attestation re-run on the final xMint reproduces the engine's attestation layer …
    again = att.simulate(ps, inp, {"p_mint": s.x_mint, "start_height": 1})
    assert np.array_equal(np.asarray(again.pin2_triggered), np.asarray(s.attest.pin2_triggered))
    assert np.array_equal(np.asarray(again.row_a_mint), np.asarray(s.attest.row_a_mint))
    assert np.array_equal(np.asarray(again.pin1_triggered), s.pin1_triggered)
    # … and PIN-1 + the medians on those rows reproduce the engine's prices
    trig = np.asarray(again.pin1_triggered, dtype=bool)
    pools = E.pin1_keys(ps, trig, inp.tag_price, inp.tag_pool, inp.tag_present)
    pr = O.price_series(ps, inp.tag_price, inp.tag_present, tag_pool=inp.tag_pool, pinned_pools=pools)
    assert np.array_equal(pr.x_mint, s.x_mint) and np.array_equal(pr.x_claim, s.x_claim)


def test_single_pass_when_pin2_never_fires():
    ps, inp = scenario(crash=0.99)
    s = E.simulate_blocks(ps, inp, activation_mode="always_active")
    assert not E.pin2_trigger_mask(ps, s.x_mint, 1, 1).any()
    assert s.extras["pin_passes"] == 1 and s.extras["pin_fixed_point"]


def test_pass_limit_is_reported(monkeypatch):
    ps, inp = scenario()
    full = E.simulate_blocks(ps, inp, activation_mode="always_active")
    monkeypatch.setattr(E, "MAX_PIN_PASSES", 1)
    s = E.simulate_blocks(ps, inp, activation_mode="always_active")
    assert s.extras["pin_passes"] == 1 and not s.extras["pin_fixed_point"]
    # the single pass never saw pMint, so PIN-2 never fired there; the fixed point has PIN-2 pins
    assert not np.asarray(s.attest.pin2_triggered).any()
    assert np.asarray(full.attest.pin2_triggered).any()


@pytest.mark.parametrize("seed", [5, 9])
def test_deterministic(seed):
    ps, inp = scenario(seed)
    a = E.simulate_blocks(ps, inp, activation_mode="always_active")
    b = E.simulate_blocks(ps, inp, activation_mode="always_active")
    assert np.array_equal(a.x_mint, b.x_mint) and a.extras["pin_passes"] == b.extras["pin_passes"]

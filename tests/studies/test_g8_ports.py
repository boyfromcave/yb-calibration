"""The spreads.py / pinrate.py ports (WP-7c) against the upstream scripts and hand-computed values."""

from __future__ import annotations

import importlib.util
import io
import subprocess
import sys

import numpy as np
import pytest
from tests.conftest import ycash6_path

from ybcal.studies import _g8_ports as P

HOUR = 3600


# ---------------------------------------------------------------------------------------------------
# Upstream copies (read-only: `git show` at the pin into a temp dir; ycash6 is never touched)


@pytest.fixture(scope="module")
def upstream(tmp_path_factory):
    repo = ycash6_path()
    if repo is None:
        pytest.skip("no ycash6 clone (set YBCAL_YCASH6)")
    root = tmp_path_factory.mktemp("ycash6_calibrate")
    files = {"contrib/yellowback/yellowback_price.py": root / "contrib/yellowback/yellowback_price.py"}
    for f in P.UPSTREAM_FILES:
        files[f] = root / f
    for src, dst in files.items():
        try:
            body = subprocess.run(["git", "-C", str(repo), "show", f"{P.UPSTREAM_COMMIT}:{src}"],
                                  check=True, capture_output=True).stdout
        except subprocess.CalledProcessError:
            pytest.skip(f"ycash6 clone lacks {P.UPSTREAM_COMMIT}:{src}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(body)
    mods = {}
    for name in ("spreads", "pinrate"):
        path = root / "contrib/yellowback/attest/calibrate" / f"{name}.py"
        spec = importlib.util.spec_from_file_location(f"_upstream_{name}", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        mods[name] = mod
    return mods


def _spreads_csv(rows) -> str:
    """rows: [(ts, cg, st, nk)] µUSD ints (None = missing)."""
    out = io.StringIO()
    out.write(",".join(P.COLUMNS) + "\n")
    for ts, *vals in rows:
        out.write(",".join(["x", str(ts)] + ["" if v is None else str(v) for v in vals] + [""]) + "\n")
    return out.getvalue()


def _random_spreads_rows(seed=0, n=3000):
    rng = np.random.default_rng(seed)
    base = 400_000 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    rows = []
    t = 1_700_000_000
    for i in range(n):
        t += 300 if rng.random() > 0.01 else 3600          # a few gaps
        noise = ((0, 0.003), (0.004, 0.012), (-0.003, 0.015))
        vals = [int(base[i] * (1 + rng.normal(b, s))) for b, s in noise]
        vals = [None if rng.random() < 0.02 else v for v in vals]
        rows.append((t, *vals))
    rows.append(rows[5])                                    # an out-of-order duplicate row
    return rows


def _random_hourly_csv(seed=1, n=24 * 60):
    rng = np.random.default_rng(seed)
    p = 0.4 * np.exp(np.cumsum(rng.normal(0, 0.012, n)))
    out = io.StringIO()
    out.write("ts,price_usd,ignored\n")
    for i in rng.permutation(n):
        out.write(f"{1_700_000_000 + int(i) * HOUR},{float(p[i])!r},x\n")
    out.write("garbage,row\n")
    return out.getvalue()


def test_spreads_port_equals_upstream(upstream):
    up = upstream["spreads"]
    csv_text = _spreads_csv(_random_spreads_rows())
    ours, theirs = P.read_log(io.StringIO(csv_text)), up.read_log(io.StringIO(csv_text))
    assert ours == theirs and len(ours) == 3001
    for interval, factor in ((300, 3.0), (300, 10.0), (60, 3.0)):
        assert P.analyze_spreads(ours, interval, factor) == up.analyze(theirs, interval, factor)
    for p95 in ({"a": 120.0, "b": 410.0, "c": None}, {"a": 100.0}, {"a": None}, {"x": 357.5}):
        assert P.recommend_bps(p95) == up.recommend_bps(p95)
    xs = list(np.random.default_rng(3).random(97))
    for pct in (50, 90, 95, 99, 100):
        assert P.percentile(xs, pct) == up.percentile(xs, pct)


def test_pinrate_port_equals_upstream(upstream):
    up = upstream["pinrate"]
    text = _random_hourly_csv()
    s1, s2 = P.read_csv(io.StringIO(text)), up.read_csv(io.StringIO(text))
    assert s1 == s2 and len(s1) == 24 * 60
    for wb, bs, d in ((288, 75, 500), (96, 75, 300), (576, 75, 200), (288, 60, 750)):
        assert P.analyze_pinrate(s1, wb, bs, d) == up.analyze(s2, wb, bs, d)
    # the decision rule reproduces the script's printed decision line
    for d in (100, 500, 1500):
        r = P.analyze_pinrate(s1, 288, 75, d)
        rate = r["rates"][d]["rate"]
        assert r["decision"] == ("confirm" if rate > up.CONFIRM_RATE else "drop" if rate < up.DROP_RATE
                                 else "inconclusive")


# ---------------------------------------------------------------------------------------------------
# Hand-computed values (always run; the upstream test_calibrate.py cases)


def _const_rows(n, st_bps=100, nk_bps=300, interval=300):
    return [(1_700_000_000 + i * interval, 400_000, round(400_000 * (1 + st_bps / 1e4)),
             round(400_000 * (1 + nk_bps / 1e4))) for i in range(n)]


def test_spreads_hand_computed():
    rows = P.read_log(io.StringIO(_spreads_csv(_const_rows(50))))
    res = P.analyze_spreads(rows, 300)
    assert res["rows"] == 50
    assert res["stats"][("coingecko", "safetrade")]["p95"] == pytest.approx(100.0)
    assert res["stats"][("coingecko", "nonkyc")]["p95"] == pytest.approx(300.0)
    assert res["stats"][("safetrade", "nonkyc")]["p95"] == pytest.approx(200 / 1.01)
    assert res["gaps"] == [] and res["recommended_bps"] == 900
    assert P.recommend_bps({"a": 120.0, "b": 410.0, "c": None}) == 1300
    assert P.recommend_bps({"a": 100.0}) == 300 and P.recommend_bps({"a": None}) is None
    assert P.spread_bps(100, 110) == pytest.approx(1000.0) == P.spread_bps(110, 100)
    assert P.percentile(list(range(1, 101)), 95) == 95 and P.percentile([7], 95) == 7
    # missing sources are excluded, not zero; gaps flagged
    r = _const_rows(20)
    r[3] = (r[3][0], r[3][1], None, r[3][3])
    r[4] = (r[4][0], None, None, None)
    r = r[:10] + [(t + 7200, a, b, c) for t, a, b, c in r[10:]]
    res = P.analyze_spreads(P.read_log(io.StringIO(_spreads_csv(r))), 300)
    assert res["missing"] == {"coingecko": 1, "safetrade": 2, "nonkyc": 1}
    assert res["stats"][("coingecko", "safetrade")]["n"] == 18
    assert len(res["gaps"]) == 1 and res["gaps"][0][2] == 7500
    # nearest-rank p95: 4 % wild ticks do not move it
    r = _const_rows(100)
    for i in range(4):
        r[i] = (r[i][0], 400_000, 600_000, 412_000)
    res = P.analyze_spreads(P.read_log(io.StringIO(_spreads_csv(r))), 300)
    assert res["stats"][("coingecko", "safetrade")]["p95"] == pytest.approx(100.0)
    assert res["stats"][("coingecko", "safetrade")]["max"] == pytest.approx(5000.0)


def _series(prices, step=HOUR, t0=1_700_000_000):
    return [(t0 + i * step, p) for i, p in enumerate(prices)]


def test_pinrate_hand_computed():
    res = P.analyze_pinrate(_series([0.40] * 100))
    assert res["windows"] == 94 and res["rates"][500]["rate"] == 0.0 and res["decision"] == "drop"
    assert res["rates"][500]["longest_quiet_windows"] == 94 and set(res["rates"]) == {500, 200, 300}
    res = P.analyze_pinrate(_series([0.40, 0.432] * 50))
    assert res["rates"][500]["rate"] == 1.0 and res["decision"] == "confirm"
    assert res["rates"][500]["rate_endpoints"] == 0.0
    res = P.analyze_pinrate(_series([0.40, 0.432] * 50), window_blocks=240)
    assert res["rates"][500]["rate_endpoints"] == 1.0
    step = [0.40] * 50 + [0.44] * 50
    res = P.analyze_pinrate(_series(step))
    assert res["rates"][500]["armed"] == 5 and res["rates"][500]["longest_quiet_windows"] == 45
    assert res["rates"][500]["rate_endpoints"] == pytest.approx(6 / 94) and res["decision"] == "inconclusive"
    assert P.analyze_pinrate(_series(step + [0.44] * 100))["decision"] == "drop"
    res = P.analyze_pinrate(_series([0.40] * 50 + [0.412] * 50))
    assert (res["rates"][200]["armed"], res["rates"][300]["armed"], res["rates"][500]["armed"]) == (5, 0, 0)
    assert P.analyze_pinrate(_series(step), window_blocks=96, block_seconds=75)["rates"][500]["armed"] == 1
    assert P.analyze_pinrate(_series([0.4, 0.5, 0.6]))["decision"] is None


def test_pin_delta_rule():
    # confirm / inconclusive keep the current value
    assert P.pin_delta_from_rates(P.analyze_pinrate(_series([0.40, 0.432] * 50)), 500) == 500
    assert P.pin_delta_from_rates(P.analyze_pinrate(_series([0.40] * 50 + [0.44] * 50)), 500) == 500
    # drop: the smallest alternative whose arming rate clears 20 %, else 200
    fake = {"rates": {500: {"rate": 0.01}, 200: {"rate": 0.5}, 300: {"rate": 0.3}}}
    assert P.pin_delta_from_rates(fake, 500) == 200
    fake = {"rates": {500: {"rate": 0.01}, 200: {"rate": 0.1}, 300: {"rate": 0.25}}}
    assert P.pin_delta_from_rates(fake, 500) == 300
    fake = {"rates": {500: {"rate": 0.01}, 200: {"rate": 0.1}, 300: {"rate": 0.05}}}
    assert P.pin_delta_from_rates(fake, 500) == 200


def test_pooled_pinrate_matches_single_history():
    s = _series(list(0.4 * np.exp(np.cumsum(np.random.default_rng(2).normal(0, 0.01, 500)))))
    one = P.analyze_pinrate(s, 288, 75, 500)
    pooled = P.pooled_pinrate([s], 288, 500)
    assert pooled["rates"][500]["armed"] == one["rates"][500]["armed"]
    assert pooled["rates"][500]["rate"] == pytest.approx(one["rates"][500]["rate"])
    assert pooled["decision"] == one["decision"]
    two = P.pooled_pinrate([s, s], 288, 500)
    assert two["rates"][500]["rate"] == pytest.approx(one["rates"][500]["rate"])
    assert two["windows"] == 2 * one["windows"]


def test_spreads_log_adapter_matches_read_log():
    from ybcal.data.loaders import load_spreads_csv

    rows = _random_spreads_rows(n=300)[:-1]                  # no duplicate: the loader keeps the last one
    text = _spreads_csv(rows)
    log = load_spreads_csv(io.StringIO(text))
    assert P.rows_from_spreads_log(log) == P.read_log(io.StringIO(text))

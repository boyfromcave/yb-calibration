"""Ports of the ycash6 calibration scripts (owner: WP-7c; PLAN §5.8, proposal §16).

Source: ``contrib/yellowback/attest/calibrate/spreads.py`` and ``pinrate.py`` at ycash6 ``7702d22``
(MIT, The Ycash developers). Only the pure analysis functions are ported — the network ``log`` /
``fetch_history`` halves stay in ycash6 (``ybcal data fetch`` / ``ybcal data import`` replace them).
Every function keeps the upstream name, signature and arithmetic so that, on the same CSV, the
numbers are identical (``tests/studies/test_g8_ports.py`` imports a temporary copy of the upstream
scripts when a ycash6 clone is present and compares outputs; hand-computed cases always run).

Adapters at the bottom feed the WP-2 loaders' objects (``SpreadsLog``, ``PricePath``) into them.
"""

from __future__ import annotations

import csv
import datetime
import itertools
import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import numpy as np

OWNER_WP = "WP-7c"

UPSTREAM_COMMIT = "7702d22"
UPSTREAM_FILES = ("contrib/yellowback/attest/calibrate/spreads.py",
                  "contrib/yellowback/attest/calibrate/pinrate.py")

# ===================================================================================================
# spreads.py (analyze half)

#: The three sources of proposal §16, in column order (spreads.py SOURCES names).
NAMES = ["coingecko", "safetrade", "nonkyc"]
COLUMNS = ["ts_iso", "ts"] + [f"{n}_micro_usd" for n in NAMES] + ["errors"]
DEFAULT_INTERVAL = 300
DEFAULT_DURATION_DAYS = 14.0


def spread_bps(a, b):
    """|a - b| * 10^4 / min(a, b), MINT-10's form. a, b > 0."""
    return abs(a - b) * 10_000 / min(a, b)


def percentile(values, pct):
    """Nearest-rank percentile of a non-empty list (pct in (0, 100])."""
    xs = sorted(values)
    rank = max(1, math.ceil(pct / 100.0 * len(xs)))
    return xs[rank - 1]


def read_log(fp, names: Sequence[str] = NAMES):
    """Rows of a spreads log: [(ts, {name: micro_usd or None})], ascending ts. Malformed rows are skipped."""
    rows = []
    for rec in csv.DictReader(fp):
        try:
            ts = int(float(rec["ts"]))
        except (KeyError, TypeError, ValueError):
            continue
        prices = {}
        for n in names:
            v = (rec.get(f"{n}_micro_usd") or "").strip()
            try:
                prices[n] = int(v) if v else None
            except ValueError:
                prices[n] = None
            if prices[n] is not None and prices[n] <= 0:
                prices[n] = None
        rows.append((ts, prices))
    rows.sort(key=lambda r: r[0])
    return rows


def pair_spreads(rows, names: Sequence[str] = NAMES):
    """{(a, b): [spread_bps, ...]} over the rows where both sources are present."""
    out = {pair: [] for pair in itertools.combinations(names, 2)}
    for _, prices in rows:
        for a, b in out:
            if prices.get(a) and prices.get(b):
                out[(a, b)].append(spread_bps(prices[a], prices[b]))
    return out


def find_gaps(rows, expected_interval, factor=3.0):
    """[(ts_before, ts_after, seconds)] where consecutive rows are more than factor x interval apart."""
    gaps = []
    for (t0, _), (t1, _) in itertools.pairwise(rows):
        if t1 - t0 > factor * expected_interval:
            gaps.append((t0, t1, t1 - t0))
    return gaps


def recommend_bps(p95_by_pair, multiple=3.0, round_to=100):
    """3 x the worst pair's p95, rounded up to `round_to` bps; None when no pair has data."""
    p95s = [v for v in p95_by_pair.values() if v is not None]
    if not p95s:
        return None
    raw = multiple * max(p95s)
    return int(math.ceil(raw / round_to) * round_to)


def analyze_spreads(rows, expected_interval, gap_factor=3.0, multiple=3.0, names: Sequence[str] = NAMES):
    """``spreads.analyze`` (renamed: two ``analyze`` functions live in this module)."""
    spreads = pair_spreads(rows, names)
    stats = {}
    for pair, xs in spreads.items():
        if xs:
            stats[pair] = {"n": len(xs), "median": percentile(xs, 50), "p90": percentile(xs, 90),
                           "p95": percentile(xs, 95), "p99": percentile(xs, 99), "max": max(xs)}
        else:
            stats[pair] = {"n": 0, "median": None, "p90": None, "p95": None, "p99": None, "max": None}
    missing = {n: sum(1 for _, p in rows if p.get(n) is None) for n in names}
    coverage_days = (rows[-1][0] - rows[0][0]) / 86400.0 if len(rows) > 1 else 0.0
    return {
        "rows": len(rows),
        "coverage_days": coverage_days,
        "missing": missing,
        "stats": stats,
        "gaps": find_gaps(rows, expected_interval, gap_factor),
        "gap_factor": gap_factor,
        "recommended_bps": recommend_bps({p: s["p95"] for p, s in stats.items()}, multiple),
    }


# ===================================================================================================
# pinrate.py (analysis half)

DEFAULT_WINDOW_BLOCKS = 288     # PIN_WINDOW (proposal §10.1)
DEFAULT_BLOCK_SECONDS = 75      # Ycash block target
DEFAULT_DELTA_BPS = 500         # PIN_DELTA_BPS
CONFIRM_RATE = 0.20
DROP_RATE = 0.05
ALT_DELTAS = (200, 300)


def read_csv(fp):
    """[(ts, price_usd)] from a CSV with `ts` (or `ts_iso`) and `price_usd` columns. Bad rows are skipped."""
    out = []
    for rec in csv.DictReader(fp):
        try:
            if rec.get("ts"):
                ts = int(float(rec["ts"]))
            else:
                ts = int(datetime.datetime.fromisoformat(rec["ts_iso"].replace("Z", "+00:00")).timestamp())
            price = float(rec["price_usd"])
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        if price > 0:
            out.append((ts, price))
    return normalize_series(out)


def normalize_series(series):
    """Ascending, one sample per timestamp."""
    return sorted({ts: p for ts, p in series}.items())


def rolling_windows(series, window_seconds):
    """For every sample as the window's end: (end_ts, [prices in (end - window, end]], price at the
    window's start) -- the start price is the last sample at or before end - window, PIN-2's other
    endpoint. Windows that do not yet reach back a full window are skipped."""
    out = []
    start = 0
    for i, (t_end, _) in enumerate(series):
        while series[start][0] <= t_end - window_seconds:
            start += 1
        if start == 0:
            continue
        out.append((t_end, [p for _, p in series[start:i + 1]], series[start - 1][1]))
    return out


def armed_range(prices, delta_bps):
    """PIN-1: (hi - lo) * 10^4 > delta * lo."""
    lo, hi = min(prices), max(prices)
    return (hi - lo) * 10_000 > delta_bps * lo


def armed_endpoints(p_start, p_end, delta_bps):
    """PIN-2: the prices one window apart differ by more than delta (relative to the lesser)."""
    return abs(p_start - p_end) * 10_000 > delta_bps * min(p_start, p_end)


def longest_run(flags):
    """Longest run of consecutive False values."""
    best = cur = 0
    for f in flags:
        cur = 0 if f else cur + 1
        best = max(best, cur)
    return best


def analyze_pinrate(series, window_blocks=DEFAULT_WINDOW_BLOCKS, block_seconds=DEFAULT_BLOCK_SECONDS,
                    delta_bps=DEFAULT_DELTA_BPS, alt_deltas=ALT_DELTAS):
    """``pinrate.analyze`` (renamed)."""
    window_seconds = window_blocks * block_seconds
    windows = rolling_windows(series, window_seconds)
    res = {"samples": len(series), "windows": len(windows), "window_seconds": window_seconds,
           "delta_bps": delta_bps, "coverage_days": 0.0, "step_seconds": None, "rates": {}, "decision": None}
    if len(series) > 1:
        res["coverage_days"] = (series[-1][0] - series[0][0]) / 86400.0
        res["step_seconds"] = (series[-1][0] - series[0][0]) / (len(series) - 1)
    if not windows:
        return res
    for d in (delta_bps, *tuple(x for x in alt_deltas if x != delta_bps)):
        flags = [armed_range(ps, d) for _, ps, _ in windows]
        ends = [armed_endpoints(p0, ps[-1], d) for _, ps, p0 in windows]
        run = longest_run(flags)
        res["rates"][d] = {
            "armed": sum(flags), "rate": sum(flags) / len(flags),
            "rate_endpoints": sum(ends) / len(ends),
            "longest_quiet_windows": run,
            "longest_quiet_seconds": (run * res["step_seconds"] if res["step_seconds"] else None),
        }
    rate = res["rates"][delta_bps]["rate"]
    if rate > CONFIRM_RATE:
        res["decision"] = "confirm"
    elif rate < DROP_RATE:
        res["decision"] = "drop"
    else:
        res["decision"] = "inconclusive"
    return res


# ===================================================================================================
# The decision rule of pinrate.py's README, as a function (the script prints it; it does not return it)


def pin_delta_from_rates(res: Mapping[str, Any], current: int, *, confirm_rate: float = CONFIRM_RATE,
                         drop_rate: float = DROP_RATE, alt_deltas: Sequence[int] = ALT_DELTAS) -> int:
    """README §2: above ``confirm_rate`` confirms ``current``; between the two keeps it ("the proposal
    biases larger"); below ``drop_rate`` drops to 200–300 — "pick the smaller of the two whose rate
    clears 20 %" (read literally: the smallest alternative whose arming rate exceeds ``confirm_rate``;
    200 when neither does)."""
    rates = res.get("rates") or {}
    if current not in rates:
        return current
    rate = rates[current]["rate"]
    if rate >= drop_rate:
        return current
    clear = sorted(d for d in alt_deltas if d in rates and rates[d]["rate"] > confirm_rate)
    return clear[0] if clear else min(alt_deltas)


# ===================================================================================================
# Adapters


def rows_from_spreads_log(log) -> list[tuple[int, dict[str, int | None]]]:
    """``ybcal.data.loaders.SpreadsLog`` → ``read_log`` rows (0 = missing → None)."""
    names = list(log.names)
    out = []
    for i, ts in enumerate(np.asarray(log.ts).tolist()):
        out.append((int(ts), {n: (int(v) if v > 0 else None) for n, v in zip(names, log.prices[i].tolist(),
                                                                              strict=True)}))
    return out


def rows_from_quotes(ts: Sequence[int], quotes: np.ndarray, names: Sequence[str]) -> list:
    """Per-source µUSD quotes ``(sources, n)`` (0 = missing) at timestamps ``ts`` → rows."""
    q = np.asarray(quotes)
    return [(int(t), {n: (int(q[j, i]) if q[j, i] > 0 else None) for j, n in enumerate(names)})
            for i, t in enumerate(ts)]


def series_from_prices(prices_usd: Iterable[float], step_seconds: int, t0: int = 1_700_000_000) -> list:
    """An evenly spaced USD price list → ``[(ts, price_usd)]`` (pinrate's series shape)."""
    return [(t0 + i * step_seconds, float(p)) for i, p in enumerate(prices_usd) if p > 0]


def pooled_pinrate(series_list: Sequence[list], window_blocks: int, delta_bps: int,
                   alt_deltas: Sequence[int] = ALT_DELTAS,
                   block_seconds: int = DEFAULT_BLOCK_SECONDS) -> dict:
    """:func:`analyze_pinrate` over several independent histories: windows are pooled (armed counts
    summed), the longest quiet run is the maximum; the decision applies the same thresholds."""
    agg: dict[int, dict[str, float]] = {}
    windows = 0
    step = None
    for s in series_list:
        r = analyze_pinrate(s, window_blocks, block_seconds, delta_bps, tuple(alt_deltas))
        if not r["rates"]:
            continue
        windows += r["windows"]
        step = r["step_seconds"]
        for d, v in r["rates"].items():
            a = agg.setdefault(d, {"armed": 0, "ends": 0.0, "quiet": 0, "n": 0})
            a["armed"] += v["armed"]
            a["ends"] += v["rate_endpoints"] * r["windows"]
            a["quiet"] = max(a["quiet"], v["longest_quiet_windows"])
            a["n"] += r["windows"]
    res: dict[str, Any] = {"windows": windows, "delta_bps": delta_bps, "rates": {}, "decision": None,
                           "histories": len(series_list)}
    for d, a in agg.items():
        res["rates"][d] = {"armed": a["armed"], "rate": a["armed"] / a["n"],
                           "rate_endpoints": a["ends"] / a["n"],
                           "longest_quiet_windows": a["quiet"],
                           "longest_quiet_seconds": a["quiet"] * step if step else None}
    if delta_bps in res["rates"]:
        rate = res["rates"][delta_bps]["rate"]
        res["decision"] = "confirm" if rate > CONFIRM_RATE else "drop" if rate < DROP_RATE else "inconclusive"
    return res

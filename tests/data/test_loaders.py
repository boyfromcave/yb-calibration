"""Loaders: timestamps, price / spreads / pool-share / depth CSVs, gaps, grid resampling."""

from __future__ import annotations

import io

import numpy as np
import pytest

from ybcal.data import loaders
from ybcal.data.loaders import (
    SPREADS_COLUMNS,
    DataFormatError,
    gap_report,
    import_file,
    load_depth_csv,
    load_pool_shares_csv,
    load_price_csv,
    load_spreads_csv,
    parse_ts,
    resample_to_grid,
    write_price_csv,
    write_spreads_csv,
)

T = 1_700_000_000  # 2023-11-14T22:13:20Z


def test_parse_ts_forms():
    assert parse_ts("1700000000") == T
    assert parse_ts(1_700_000_000_123) == T  # milliseconds
    assert parse_ts("2023-11-14T22:13:20Z") == T
    assert parse_ts("2023-11-14T22:13:20.987654321Z") == T  # sub-second dropped (nanoseconds too)
    assert parse_ts("2023-11-14 22:13:20") == T  # naive = UTC
    assert parse_ts("2023-11-15T00:13:20+02:00") == T
    with pytest.raises(ValueError):
        parse_ts("")


def test_price_csv_duplicates_bad_rows_and_iso():
    text = (
        "ts_iso,ts,price_usd\n"
        f"x,{T + 7200},0.42\n"
        f"x,{T},0.40\n"
        f"x,{T + 3600},0.41\n"
        f"x,{T + 3600},0.415\n"  # duplicate ts, conflicting: last wins
        f"x,{T + 3600},0.415\n"  # duplicate, agreeing
        "x,notatime,0.5\n"
        f"x,{T + 10800},-1\n"
        f"x,{T + 14400},nan\n"
    )
    s = load_price_csv(io.StringIO(text))
    assert s.ts.tolist() == [T, T + 3600, T + 7200]
    assert s.price_usd.tolist() == [0.40, 0.415, 0.42]
    assert s.duplicates == 2 and s.conflicting_duplicates == 1 and s.rows_skipped == 3


def test_price_csv_iso_only_and_aliases():
    s = load_price_csv(io.StringIO("ts_iso,price\n2023-11-14T22:13:20Z,0.4\n2023-11-14T23:13:20Z,0.5\n"))
    assert s.ts.tolist() == [T, T + 3600]
    with pytest.raises(DataFormatError):
        load_price_csv(io.StringIO("when,value\n1,2\n"))
    with pytest.raises(DataFormatError):
        load_price_csv(io.StringIO("ts,price_usd\nbad,bad\n"))


def test_price_round_trip_with_volume(tmp_path):
    s = load_price_csv(io.StringIO(f"ts,price_usd,volume_24h_usd\n{T},0.4,100\n{T + 3600},0.5,\n"))
    f = write_price_csv(s, tmp_path / "p.csv")
    s2 = load_price_csv(f)
    assert s2.ts.tolist() == s.ts.tolist() and s2.price_usd.tolist() == s.price_usd.tolist()
    assert s2.volume_usd[0] == 100 and np.isnan(s2.volume_usd[1])


def test_gap_report():
    ts = [T, T + 3600, T + 7200, T + 6 * 3600, T + 7 * 3600]
    g = gap_report(ts)
    assert g.expected_step == 3600 and g.n_gaps == 1
    assert g.gaps[0] == (T + 7200, T + 6 * 3600, 4 * 3600)
    assert g.missing_steps == 3 and g.longest == 4 * 3600
    assert g.coverage == pytest.approx(5 / 8)
    assert "gaps" in g.summary()
    assert gap_report([]).summary() == "no samples"


def test_resample_to_grid_forward_fill_mask():
    # hourly data with a 3-hour hole and one off-grid sample
    ts = np.array([T, T + 3600, T + 7200, T + 5 * 3600 + 10, T + 6 * 3600])
    s = loaders.PriceSeries(ts, np.array([1.0, 2.0, 3.0, 4.0, 5.0]))
    rs = resample_to_grid(s, "hour", t0=T)
    assert rs.path.prices[0].tolist() == [
        1_000_000,
        2_000_000,
        3_000_000,
        3_000_000,
        3_000_000,
        3_000_000,
        5_000_000,
    ]
    assert rs.filled.tolist() == [False, False, False, True, True, True, False]
    assert rs.path.meta["filled"] is rs.filled and rs.path.provenance == "real"
    # the off-grid sample (T+5h+10s) lands in cell (T+5h, T+6h]: observed at T+6h, superseded by 5.0
    rs2 = resample_to_grid(s, "hour", t0=T, max_ffill_seconds=2 * 3600)
    assert rs2.path.prices[0].tolist()[3:6] == [3_000_000, 3_000_000, 0]


def test_resample_default_t0_rounds_up_and_block_grid():
    s = loaders.PriceSeries(np.array([T + 10, T + 3610]), np.array([0.4, 0.5]))
    rs = resample_to_grid(s, "block")
    assert int(rs.path.t0.timestamp()) % 75 == 0 and int(rs.path.t0.timestamp()) >= T + 10
    assert rs.path.resolution == "block" and rs.filled.mean() > 0.9


# spreads.py log format ------------------------------------------------------------------------------


def spreads_text(rows):
    """Like ycash6 test_calibrate.spreads_csv: rows of (ts, cg, st, nk) USD floats, None = missing."""
    out = [",".join(SPREADS_COLUMNS)]
    for ts, cg, st, nk in rows:
        cells = ["" if v is None else str(round(v * 1_000_000)) for v in (cg, st, nk)]
        out.append(",".join(["2023-11-14T22:13:20Z", str(ts), *cells, ""]))
    return "\n".join(out) + "\n"


def test_spreads_columns_match_spreads_py():
    assert SPREADS_COLUMNS == (
        "ts_iso",
        "ts",
        "coingecko_micro_usd",
        "safetrade_micro_usd",
        "nonkyc_micro_usd",
        "errors",
    )


def test_spreads_load_rules_and_pair_spreads():
    rows = [(T + i * 300, 0.40, 0.404, 0.412) for i in range(5)] + [(T + 5 * 300, 0.40, None, 0.412)]
    text = spreads_text(rows) + "x,bad,1,2,3,\n" + f"x,{T},0,-5,abc,boom\n"
    log = load_spreads_csv(io.StringIO(text))
    assert len(log) == 6 and log.rows_skipped == 1 and log.duplicates == 1
    assert log.prices[0].tolist() == [0, 0, 0] and log.errors[0] == "boom"  # duplicate ts: last row wins
    assert log.missing() == {"coingecko": 1, "safetrade": 2, "nonkyc": 1}
    sp = log.pair_spreads_bps()
    assert sp[("coingecko", "safetrade")] == pytest.approx([100.0] * 4)
    assert sp[("safetrade", "nonkyc")] == pytest.approx([(412 - 404) * 1e4 / 404] * 4)
    assert log.gaps().n_gaps == 0


def test_spreads_round_trip(tmp_path):
    rows = [(T + i * 300, 0.40, 0.41 if i % 3 else None, 0.39) for i in range(20)]
    log = load_spreads_csv(io.StringIO(spreads_text(rows)))
    f = write_spreads_csv(log, tmp_path / "s.csv")
    assert f.read_text().splitlines()[0] == ",".join(SPREADS_COLUMNS)
    log2 = load_spreads_csv(f)
    assert np.array_equal(log2.prices, log.prices) and np.array_equal(log2.ts, log.ts)


def test_spreads_rejects_other_files():
    with pytest.raises(DataFormatError):
        load_spreads_csv(io.StringIO("ts,price_usd\n1,2\n"))


# pool shares ----------------------------------------------------------------------------------------


def test_pool_shares():
    text = (
        "height,payout_key\n"
        + "\n".join(f"{h},{'A' if h % 2 else ('B' if h % 4 else 'C')}" for h in range(100, 140))
        + "\n105,Z\n200,A\nbad,A\n141,\n"
    )
    log = load_pool_shares_csv(io.StringIO(text))
    assert log.duplicates == 1 and log.rows_skipped == 2
    assert log.missing_heights() == 200 - 100 + 1 - len(log)
    sh = log.shares()
    assert next(iter(sh)) == "A" and sum(sh.values()) == pytest.approx(1.0)
    assert log.rolling_shares(10).shape == (len(log) // 10, len(log.keys))
    assert log.gaps().n_gaps == 1


# depth ----------------------------------------------------------------------------------------------


def test_depth_summary_form():
    d = load_depth_csv(io.StringIO(f"ts,depth_2pct_usd,volume_24h_usd\n{T},5000,20000\n{T + 3600},4000,\n"))
    assert d.form == "summary" and d.depth_2pct_usd.tolist() == [5000, 4000]
    assert np.isnan(d.volume_24h_usd[1]) and np.isnan(d.bid_depth_2pct_usd[0])


def test_depth_book_form_summarises_within_band():
    text = (
        "ts,side,price_usd,size_yec\n"
        f"{T},bid,0.99,100\n{T},bid,0.97,1000\n{T},ask,1.01,50\n{T},ask,1.03,1000\n"
        f"{T + 60},bid,1.0,10\n"
    )  # one-sided snapshot: NaN
    d = load_depth_csv(io.StringIO(text))
    assert d.form == "book" and len(d) == 2
    # mid = 1.00: bids >= 0.98 → 99; asks <= 1.02 → 50.5
    assert d.depth_2pct_usd[0] == pytest.approx(99 + 50.5)
    assert d.bid_depth_2pct_usd[0] == pytest.approx(99)
    assert np.isnan(d.depth_2pct_usd[1])


def test_import_file_dispatch(tmp_path):
    f = tmp_path / "p.csv"
    f.write_text(f"ts,price_usd\n{T},0.4\n")
    assert isinstance(import_file(f, "price"), loaders.PriceSeries)
    with pytest.raises(ValueError):
        import_file(f, "nope")

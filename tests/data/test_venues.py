"""The M7 real-data sources (venues.py) against recorded replies, plus splice/spreads/volume/depth."""

from __future__ import annotations

import io
import json
import math

import numpy as np
import pytest

from ybcal import cli
from ybcal.data import fetch, loaders, venues
from ybcal.data.loaders import PriceSeries

from .helpers import FakeOpener, fixture_bytes, fixture_json, http_error


def client():
    return fetch.HttpClient(sleep=lambda s: None, clock=lambda: 0.0, min_interval=0.0, retries=0)


@pytest.fixture
def opener(monkeypatch):
    def install(script):
        fake = FakeOpener(script)
        monkeypatch.setattr(fetch, "urlopen", fake)
        return fake

    return install


def test_cmc_hourly_chunks_and_close_stamp(opener):
    body = fixture_bytes("cmc_historical_hourly.json")
    fake = opener([body, body])
    res = venues.fetch_cmc_history("hourly", start=1790000000, end=1790000000 + 31 * 86400, client=client())
    assert len(fake.urls) == 2  # 30-day chunks
    assert "id=4160" in fake.urls[0] and "interval=hourly" in fake.urls[0]
    c = res.candles
    q0 = fixture_json("cmc_historical_hourly.json")["data"]["quotes"][0]
    # stamped at close = open + 1 h; price = close
    assert c.ts[0] == loaders.parse_ts(q0["timeOpen"]) + 3600
    assert c.close[0] == pytest.approx(q0["quote"]["close"])
    assert c.volume_kind == "usd_24h" and res.series.volume_usd is not None
    assert np.all(np.diff(c.ts) > 0)  # chunks merged without duplicates


def test_cmc_bad_reply(opener):
    opener([b'{"status": {"error_code": "400"}}'])
    with pytest.raises(fetch.FetchError, match=r"data\.quotes"):
        venues.fetch_cmc_history("daily", start=0, end=86400 * 10, client=client())


def test_coincodex_skips_null_and_reduces_daily(opener):
    obj = {"YEC": [[86400 * 10 + 3600, 1.0, 5.0, None], [86400 * 10 + 7200, None, 5.0, None],
                   [86400 * 10 + 80000, 2.0, 6.0, None], [86400 * 11 + 10, 3.0, 7.0, None]]}
    opener([json.dumps(obj).encode()])
    res = venues.fetch_coincodex_daily(start=86400 * 10, end=86400 * 12, client=client())
    s = res.series
    assert list(s.ts) == [86400 * 11, 86400 * 12]  # stamped at next midnight
    assert list(s.price_usd) == [2.0, 3.0]  # last point of each day; the null is skipped


def test_coincodex_fixture_parses():
    s = venues.parse_coincodex(fixture_json("coincodex_yec.json"), "YEC")
    assert len(s) > 5 and np.all(s.price_usd > 0)


def test_nonkyc_candles(opener):
    opener([fixture_bytes("nonkyc_candles.json")])
    res = venues.fetch_nonkyc_candles(start=1790000000, end=1790036000, client=client())
    bars = fixture_json("nonkyc_candles.json")["bars"]
    c = res.candles
    assert c.ts[0] == bars[0]["time"] // 1000 + 3600
    assert c.close[0] == pytest.approx(bars[0]["close"])
    assert c.volume_kind == "base_per_candle"


def test_safetrade_pages_backwards(opener):
    page2 = [[1000 * 3600, "1", "1", "1", "1.5", "2"], [1001 * 3600, "1", "1", "1", "1.6", "0"]]
    page1 = [[1001 * 3600, "1", "1", "1", "1.6", "0"], [1002 * 3600, "1", "1", "1", "1.7", "3"]]
    fake = opener([json.dumps(page1).encode(), json.dumps(page2).encode(), b"[]"])
    res = venues.fetch_safetrade_klines(start=900 * 3600, end=1003 * 3600, client=client())
    assert len(fake.urls) == 3
    assert f"time_to={1001 * 3600 - 1}" in fake.urls[1]
    assert list(res.candles.close) == [1.5, 1.6, 1.7]


def test_safetrade_fixture_parses():
    c = venues.parse_peatio_klines(fixture_json("safetrade_kline.json"))
    assert len(c) > 3 and c.interval == 3600


def test_orderbooks_rows_and_btc_conversion(opener):
    btc = json.dumps({"lastPriceNumber": 100000.0}).encode()
    yb = json.dumps(
        {"bids": [{"price": "0.000004", "quantity": "10"}], "asks": [{"price": "0.0000041", "quantity": "5"}]}
    )
    fake = opener(
        [fixture_bytes("nonkyc_orderbook.json"), yb.encode(), btc, fixture_bytes("safetrade_depth.json")]
    )
    res = venues.fetch_orderbooks(client=client(), now=1_791_000_000)
    assert len(fake.urls) == 4
    venues_seen = {r["venue"] for r in res.rows}
    assert venues_seen == set(venues.BOOK_VENUES)
    btc_rows = [r for r in res.rows if r["venue"] == "nonkyc_io:YEC_BTC"]
    assert btc_rows[0]["price_usd"] == pytest.approx(0.4)
    assert all(r["ts"] == 1_791_000_000 for r in res.rows)


def test_orderbooks_skip_failing_venue(opener):
    opener([http_error("x", 503), fixture_bytes("safetrade_depth.json")])
    res = venues.fetch_orderbooks(["nonkyc_io:YEC_USDT", "safe_trade:yecusdt"], client=client(), now=1)
    assert {r["venue"] for r in res.rows} == {"safe_trade:yecusdt"}
    assert res.notes and "nonkyc_io:YEC_USDT" in res.notes[0]


def test_depth_loader_per_venue_mid():
    # venue A trades at 1.00, venue B at 1.10: pooled, the "mid" would be skewed and B's bid would
    # sit above A's ask (crossed). Per venue, each book's ±2 % depth is counted around its own mid.
    csv = (
        "ts,venue,side,price_usd,size_yec\n"
        "100,A,bid,0.995,10\n100,A,ask,1.005,10\n100,A,bid,0.9,100\n"
        "100,B,bid,1.095,10\n100,B,ask,1.105,10\n"
    )
    d = loaders.load_depth_csv(io.StringIO(csv))
    want = 0.995 * 10 + 1.005 * 10 + 1.095 * 10 + 1.105 * 10
    assert d.depth_2pct_usd[0] == pytest.approx(want)
    assert d.bid_depth_2pct_usd[0] == pytest.approx(0.995 * 10 + 1.095 * 10)


def test_depth_loader_without_venue_unchanged():
    csv = "ts,side,price_usd,size_yec\n100,bid,0.99,10\n100,ask,1.01,10\n100,ask,1.5,10\n"
    d = loaders.load_depth_csv(io.StringIO(csv))
    assert d.depth_2pct_usd[0] == pytest.approx(0.99 * 10 + 1.01 * 10)


def test_inzyght_pages(opener):
    def page(start, n):
        return json.dumps(
            {"draw": 1, "data": [{"height": 1000 - start - i, "miner": f"s1{(start + i) % 3}", "time": 1}
                                 for i in range(n)]}
        ).encode()

    fake = opener([page(0, 200), page(200, 50)])
    res = venues.fetch_inzyght_blocks(250, client=client())
    assert len(res.rows) == 250
    assert "start=200&length=50" in fake.urls[1]
    assert [r["height"] for r in res.rows] == sorted(r["height"] for r in res.rows)


def test_inzyght_fixture_parses():
    rows = venues.parse_inzyght_blocks(fixture_json("inzyght_blocks.json"))
    assert len(rows) == 5 and all(m.startswith("s1") for _, m, _ in rows)


def _ps(ts, px, vol=None):
    ts = np.asarray(ts, dtype=np.int64)
    return PriceSeries(ts, np.asarray(px, dtype=float), "t", len(ts), 0, 0, 0,
                       None if vol is None else np.asarray(vol, dtype=float))


def test_compare_and_splice():
    a = _ps([3600 * k for k in range(10, 20)], [1.0 + 0.01 * k for k in range(10)])
    b = _ps([3600 * k for k in range(0, 15)], [1.02 * (0.9 + 0.01 * k) for k in range(15)])
    ov = venues.compare(a, b)
    assert ov.n == 5 and ov.return_corr > 0.99
    joined, info = venues.splice(a, b)
    assert len(joined) == 20 and info["secondary_points"] == 10
    assert joined.ts[10] == a.ts[0] and joined.price_usd[10] == a.price_usd[0]
    _, info2 = venues.splice(a, b, rescale=True)
    # raw: the 2 % level step turns a +1 % hour into about -1 %; rescaled: the true +1 % again
    assert info["splice_return_bps"] == pytest.approx(-97.05, abs=0.1)
    assert info2["splice_return_bps"] == pytest.approx(1e4 * (1.0 / 0.99 - 1), abs=0.1)


def test_last_trade_asof_skips_zero_volume():
    c = venues.candles_from_rows(
        [(0, 1, 1, 1, 1.0, 5), (3600, 1, 1, 1, 9.0, 0), (7200, 1, 1, 1, 2.0, 1)], 3600, "x"
    )
    grid = np.array([3600, 7200, 10800, 100000])
    np.testing.assert_allclose(venues.last_trade_asof(c, grid), [1.0, 1.0, 2.0, 2.0])
    np.testing.assert_allclose(venues.last_trade_asof(c, grid, max_age=7200), [1.0, 1.0, 2.0, 0.0])


def test_reconstruct_spreads_roundtrips_through_loader(tmp_path):
    agg = _ps([3600 * k for k in range(1, 6)], [1.0, 1.0, 1.1, 1.1, 1.2])
    st = venues.candles_from_rows([(0, 1, 1, 1, 1.05, 1)], 3600, "st")
    nk = venues.candles_from_rows([(3600 * 2, 1, 1, 1, 0.9, 1)], 3600, "nk")
    rows = venues.reconstruct_spreads(agg, {"safetrade": st, "nonkyc": nk})
    out = venues.write_spreads_rows(rows, tmp_path / "s.csv")
    log = loaders.load_spreads_csv(out)
    assert len(log) == 5
    assert log.missing() == {"coingecko": 0, "safetrade": 0, "nonkyc": 2}
    assert log.prices[0, 1] == 1_050_000


def test_flat_runs():
    runs = venues.flat_runs(np.array([1, 1, 1, 2, 3, 3, 4, 4, 4, 4], dtype=float))
    assert list(runs) == [3, 2, 4]
    st = venues.flat_stats(np.array([1, 1, 1, 2, 3, 3, 4, 4, 4, 4], dtype=float), 3600)
    assert st["zero_return_share"] == pytest.approx(6 / 9)
    assert st["run_max_hours"] == 4.0


def test_cli_volume_and_splice(tmp_path, capsys):
    f = tmp_path / "p.csv"
    lines = ["ts,price_usd,volume_24h_usd"]
    for d in range(30):
        for h in (0, 12):
            lines.append(f"{86400 * d + 3600 * h},1.0,{1000 + d * 10 + h}")
    f.write_text("\n".join(lines) + "\n")
    assert cli.main(["data", "volume", str(f), "--days", "30"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["days"] == 30 and out["min_usd"] == 1012.0  # the last reading of each day
    g = tmp_path / "q.csv"
    g.write_text("ts,price_usd\n" + "\n".join(f"{86400 * d},1.0" for d in range(-10, 5)) + "\n")
    assert cli.main(["data", "splice", str(f), str(g), "--out", str(tmp_path / "j.csv")]) == 0
    j = loaders.load_price_csv(tmp_path / "j.csv")
    assert j.ts[0] == -864000 and (tmp_path / "j.csv.splice.json").exists()


def test_coingecko_free_plan_hint(opener):
    body = json.dumps(
        {"error": {"status": {"error_code": 10012, "error_message": "Public API users are limited to "
                              "querying historical data within the past 365 days."}}}
    ).encode()

    def err(url):
        e = http_error(url, 401, "Unauthorized")
        e.fp = io.BytesIO(body)
        return e

    opener([err])
    with pytest.raises(fetch.FetchError) as ei:
        fetch.fetch_coingecko_market_chart(3650, granularity="daily", client=client())
    msg = str(ei.value)
    assert "365 days" in msg and "--source coinmarketcap" in msg


def test_extra_source_writes_provenance(opener, tmp_path):
    opener([fixture_bytes("nonkyc_candles.json")])
    out = tmp_path / "nk.csv"
    venues.fetch_extra_to_csv("nonkyc-candles", out, start=1790000000, end=1790036000, client=client())
    prov = json.loads((tmp_path / "nk.csv.provenance.json").read_text())
    assert prov["source"] == "nonkyc:candles" and prov["rows"] > 0
    s = loaders.load_price_csv(out)  # candle CSV is a price CSV
    assert len(s) == prov["rows"] and not math.isnan(s.price_usd[0])
    c = venues.read_candles_csv(out)
    assert c.interval == 3600 and len(c) == len(s)


def test_candle_csv_volume_only_when_usd_24h(tmp_path):
    usd = venues.candles_from_rows([(0, 1, 1, 1, 1.0, 500.0)], 86400, "cmc", "usd_24h")
    base = venues.candles_from_rows([(0, 1, 1, 1, 1.0, 500.0)], 3600, "nk", "base_per_candle")
    a = loaders.load_price_csv(venues.write_candles_csv(usd, tmp_path / "a.csv"))
    b = loaders.load_price_csv(venues.write_candles_csv(base, tmp_path / "b.csv"))
    assert a.volume_usd is not None and a.volume_usd[0] == 500.0
    assert b.volume_usd is None or math.isnan(b.volume_usd[0])


def test_cli_import_spreads_infers_interval(tmp_path, capsys):
    rows = [[loaders.iso(3600 * k), str(3600 * k), "1000000", "1010000", "990000", ""] for k in range(1, 50)]
    f = venues.write_spreads_rows(rows, tmp_path / "s.csv")
    assert cli.main(["data", "import", str(f), "--kind", "spreads"]) == 0
    out = capsys.readouterr().out
    assert "expected step: 3600 s" in out and "gaps (> 3x step): 0" in out

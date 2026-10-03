"""Fetchers against recorded replies (no network: ``fetch.urlopen`` is replaced)."""

from __future__ import annotations

import json
import math
import urllib.error
from datetime import UTC, datetime

import pytest

from ybcal import cli
from ybcal.config import sha256_file
from ybcal.data import fetch
from ybcal.data.loaders import load_price_csv

from .helpers import FakeOpener, fixture_bytes, fixture_json, http_error


def client(**kw):
    """An HttpClient that never really sleeps; records the requested delays."""
    sleeps: list[float] = []
    c = fetch.HttpClient(sleep=sleeps.append, clock=lambda: 0.0, min_interval=0.0, **kw)
    c.sleeps = sleeps  # type: ignore[attr-defined]
    return c


@pytest.fixture
def opener(monkeypatch):
    def install(script):
        fake = FakeOpener(script)
        monkeypatch.setattr(fetch, "urlopen", fake)
        return fake

    return install


def test_market_chart_auto(opener):
    fake = opener([fixture_bytes("coingecko_market_chart_hourly.json")])
    res = fetch.fetch_coingecko_market_chart(3, client=client(), api_key="demo-key")
    s = res.series
    assert fake.urls == ["https://api.coingecko.com/api/v3/coins/ycash/market_chart?vs_currency=usd&days=3"]
    assert fake.headers[0]["x-cg-demo-api-key"] == "demo-key"
    raw = fixture_json("coingecko_market_chart_hourly.json")
    assert len(s) == len(raw["prices"])
    assert s.ts[0] == raw["prices"][0][0] // 1000 and s.price_usd[0] == raw["prices"][0][1]
    assert s.volume_usd is not None and not math.isnan(s.volume_usd[0])
    assert s.gaps().expected_step in range(3590, 3640)


def test_market_chart_hourly_chunks_merge(opener):
    fake = opener(
        [
            fixture_bytes("coingecko_range_1.json"),
            fixture_bytes("coingecko_range_2.json"),
            fixture_bytes("coingecko_range_2.json"),
        ]
    )
    now = 1_790_000_000
    res = fetch.fetch_coingecko_market_chart(150, granularity="hourly", client=client(), now=now)
    assert len(fake.urls) == 2 and all("/market_chart/range?" in u for u in fake.urls)
    assert f"from={now - 150 * 86400}" in fake.urls[0] and f"to={now}" in fake.urls[-1]
    assert len(res.series) == 96 and (res.series.ts[1:] > res.series.ts[:-1]).all()


def test_market_chart_daily(opener):
    fake = opener([fixture_bytes("coingecko_market_chart_daily.json")])
    res = fetch.fetch_coingecko_market_chart(120, granularity="daily", client=client())
    assert fake.urls[0].endswith("days=120&interval=daily")
    assert res.series.gaps().expected_step == 86400


def test_market_chart_rejects_bad_shapes(opener):
    opener([b'{"error": "coin not found"}'])
    with pytest.raises(fetch.FetchError):
        fetch.fetch_coingecko_market_chart(3, client=client(retries=0))
    opener([b'{"prices": [[1, NaN]]}'])
    with pytest.raises(fetch.FetchError, match="not valid JSON"):
        fetch.fetch_coingecko_market_chart(3, client=client(retries=0))


def test_tickers_filter_and_fields(opener):
    fake = opener([fixture_bytes("coingecko_tickers.json")])
    res = fetch.fetch_coingecko_tickers(exchanges=["safe_trade"], client=client(), now=1_788_673_000)
    assert fake.urls[0].endswith("/coins/ycash/tickers?exchange_ids=safe_trade")
    assert [r["target"] for r in res.rows] == ["USDT", "BTC"]
    usdt = res.rows[0]
    assert usdt["venue"] == "safe_trade" and usdt["price_usd"] == 0.43002
    assert usdt["spread_bps"] == pytest.approx(120.4) and usdt["is_stale"] is False
    assert usdt["last_traded_at"] == int(datetime(2026, 9, 5, 5, 51, 12, tzinfo=UTC).timestamp())
    assert res.rows[1]["is_stale"] is True


def test_nonkyc(opener):
    fake = opener([fixture_bytes("nonkyc_yec_usdt.json")])
    res = fetch.fetch_nonkyc_market(client=client(), now=1_788_673_100)
    assert fake.urls[0] == "https://api.nonkyc.io/api/v2/market/getbysymbol/YEC_USDT"
    row = res.rows[0]
    assert row["price_usd"] == 0.428 and row["last_trade_at"] == 1_788_673_000
    assert row["spread_bps"] == pytest.approx((0.4291 - 0.4262) / 0.42765 * 1e4)


def test_retry_after_429_then_success(opener):
    url = "https://api.coingecko.com/x"
    fake = opener(
        [
            http_error(url, 429, "Too Many Requests", {"Retry-After": "7"}),
            http_error(url, 503, "Service Unavailable"),
            fixture_bytes("nonkyc_yec_usdt.json"),
        ]
    )
    c = client(retries=3, backoff=2.0)
    assert c.get_json(url)["symbol"] == "YEC_USDT"
    assert c.sleeps == [7.0, 4.0] and len(fake.urls) == 3


def test_http_404_is_not_retried(opener):
    url = "https://api.coingecko.com/x"
    fake = opener([http_error(url, 404, "Not Found")])
    with pytest.raises(fetch.FetchError, match="404") as ei:
        client(retries=3).get_json(url)
    assert not isinstance(ei.value, fetch.NetworkBlockedError) and len(fake.urls) == 1


def test_network_blocked_message_points_to_docs(opener):
    err = urllib.error.URLError("Tunnel connection failed: 403 Forbidden")
    opener([err, err])
    with pytest.raises(fetch.NetworkBlockedError) as ei:
        client(retries=1).get_json("https://api.coingecko.com/api/v3/ping")
    msg = str(ei.value)
    assert "api.coingecko.com" in msg and "docs/data.md" in msg and "data/local/" in msg


def test_proxy_403_and_407_are_blocked(opener):
    url = "https://api.nonkyc.io/x"
    opener([http_error(url, 407, "Proxy Authentication Required")])
    with pytest.raises(fetch.NetworkBlockedError):
        client().get_json(url)


def test_body_cap(opener):
    opener([b"[" + b"1," * 100 + b"1]"])
    with pytest.raises(fetch.FetchError, match="cap"):
        client(retries=0, max_body=50).get_json("https://x.example/a")


def test_rate_limit_spacing():
    t = [0.0]
    sleeps: list[float] = []

    def sleep(d):
        sleeps.append(d)
        t[0] += d

    c = fetch.HttpClient(min_interval=2.5, sleep=sleep, clock=lambda: t[0])
    c._throttle()
    t[0] += 1.0
    c._throttle()
    assert sleeps == [pytest.approx(1.5)]


def test_fetch_to_csv_writes_csv_and_provenance(opener, tmp_path):
    opener([fixture_bytes("coingecko_market_chart_hourly.json")])
    out = tmp_path / "yec.csv"
    fetch.fetch_to_csv("coingecko", out, days=3, client=client())
    s = load_price_csv(out)
    assert len(s) == len(fixture_json("coingecko_market_chart_hourly.json")["prices"])
    prov = json.loads((tmp_path / "yec.csv.provenance.json").read_text())
    assert prov["sha256"] == sha256_file(out) and prov["source"] == "coingecko"
    assert prov["urls"][0].startswith("https://api.coingecko.com/") and prov["provenance"] == "real"
    assert prov["fetched_at"].endswith("Z") and prov["days"] == 3


def test_snapshot_sources_append(opener, tmp_path):
    out = tmp_path / "tickers.csv"
    opener([fixture_bytes("coingecko_tickers.json"), fixture_bytes("coingecko_tickers.json")])
    fetch.fetch_to_csv("tickers", out, client=client(), now=1)
    fetch.fetch_to_csv("tickers", out, client=client(), now=2)
    lines = out.read_text().splitlines()
    assert lines[0].split(",") == list(fetch.TICKER_COLUMNS) and len(lines) == 1 + 2 * 2
    prov = json.loads((tmp_path / "tickers.csv.provenance.json").read_text())
    assert prov["sha256"] == sha256_file(out) and len(prov["history"]) == 1
    out2 = tmp_path / "nonkyc.csv"
    opener([fixture_bytes("nonkyc_yec_usdt.json")])
    fetch.fetch_to_csv("nonkyc", out2, client=client(), now=1)
    assert out2.read_text().splitlines()[1].split(",")[2] == "nonkyc_io"


def test_cli_fetch_ok_and_blocked(opener, tmp_path, capsys):
    opener([fixture_bytes("coingecko_market_chart_hourly.json")])
    out = tmp_path / "sub" / "yec.csv"
    assert cli.main(["data", "fetch", "--days", "3", "--out", str(out)]) == 0
    assert out.exists() and "wrote" in capsys.readouterr().out
    err = urllib.error.URLError("[Errno -3] Temporary failure in name resolution")
    opener([err])
    rc = cli.main(["data", "fetch", "--out", str(tmp_path / "b.csv"), "--retries", "0"])
    assert rc == 3 and "docs/data.md" in capsys.readouterr().err

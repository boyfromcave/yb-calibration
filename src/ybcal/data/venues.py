"""More real-data sources: long price history, venue candles, order books, block miners (M7).

``fetch.py`` covers what the attestor agents read live (CoinGecko aggregate, CoinGecko tickers,
nonkyc's market summary). Calibration needs more than that, and the free CoinGecko plan stops at
365 days, so this module adds (verified reachable 2026-10-03):

* **CoinMarketCap** ``data-api/v3/cryptocurrency/historical`` — the public endpoint behind the
  coinmarketcap.com charts (no key, unofficial: it may change without notice). Hourly and daily
  OHLCV in USD; YEC (id 4160) from 2020-03-25, ZEC (id 1437) from 2016. A call returns at most
  ~750 candles, so hourly is fetched in 30-day chunks and daily in 300-day chunks.
* **CoinCodex** ``get_coin_history`` — the only free source found that reaches the fork
  (2019-07-19); used for the daily series before CoinMarketCap's listing.
* **nonkyc.io** ``/api/v2/market/candles`` (hourly from 2023-09) and ``/api/v2/market/orderbook``.
* **SafeTrade** (Peatio/OpenDAX) ``/api/v2/trade/public/markets/{m}/k-line`` (hourly from
  2023-03, at most 10,000 per call, the latest ones in the range) and ``.../depth``. Cloudflare
  refuses curl and browser-like user agents but answers ``ybcal/x`` and ``yellowback-quote/2``
  (the agents' own client, ``yellowback_price.py``).
* **Inzyght** (explorer.ycash.xyz) ``/api/v1/blocks`` — DataTables paging (``draw``/``start``/
  ``length``, at most 200 per page), each row carrying the coinbase payout address as ``miner``.

Candles are stamped at their **close** (open + interval): the close is the last trade at that
time, which is what a last-trade quote (``converted_last``, ``lastPriceNumber``) would have read.
A candle with zero volume carries no trade; :func:`last_trade_asof` skips it, so a venue that did
not trade keeps quoting its previous trade — exactly the staleness the live agents see.

:func:`reconstruct_spreads` turns hourly candles into a ``spreads.py log``-shaped CSV and
:func:`splice` joins two price series at a cut-over, with :func:`compare` for the overlap check.

Owner: WP-2 (M7 real data).
"""

from __future__ import annotations

import csv
import math
import time
import urllib.parse
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from ybcal.data.fetch import (
    HISTORY_MAX_BODY_BYTES,
    FetchError,
    FetchResult,
    HttpClient,
    _finite,
)
from ybcal.data.loaders import SPREADS_COLUMNS, PriceSeries, iso, parse_ts

OWNER_WP = "WP-2"

CMC_HISTORICAL = "https://api.coinmarketcap.com/data-api/v3/cryptocurrency/historical"
CMC_IDS = {"ycash": 4160, "zcash": 1437}
CMC_USD = 2781
CMC_CHUNK_DAYS = {"hourly": 30, "daily": 300}
COINCODEX_HISTORY = "https://coincodex.com/api/coincodex/get_coin_history"
NONKYC_BASE = "https://api.nonkyc.io"
SAFETRADE_BASE = "https://safe.trade"
COINCODEX_CHUNK_DAYS = 120
INZYGHT_BASE = "https://explorer.ycash.xyz"
INZYGHT_PAGE = 200
#: Ycash forked from Zcash at block 570,000 on 2019-07-18.
YCASH_FORK_TS = 1_563_408_000
INTERVAL_SECONDS = {"hourly": 3600, "daily": 86400}

CANDLE_COLUMNS = ("ts_iso", "ts", "price_usd", "open", "high", "low", "volume", "volume_kind")


# ---------------------------------------------------------------------------------------------------
# Candles


@dataclass
class Candles:
    """OHLCV candles stamped at their close (``ts`` = open + interval), ascending, unique."""

    ts: np.ndarray  #: int64 close time, unix seconds
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray  #: NaN when unknown
    interval: int  #: seconds
    source: str = ""
    volume_kind: str = ""  #: "base_per_candle" | "usd_24h" | ""
    meta: dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.ts)

    def to_series(self) -> PriceSeries:
        """Close prices as a :class:`PriceSeries` (volume carried when it is a USD 24 h figure)."""
        vol = self.volume.copy() if self.volume_kind == "usd_24h" else None
        return PriceSeries(self.ts.copy(), self.close.copy(), self.source, len(self), 0, 0, 0, vol)


def candles_from_rows(
    rows: Iterable[tuple[int, float, float, float, float, float]],
    interval: int,
    source: str,
    volume_kind: str = "",
) -> Candles:
    """Build :class:`Candles` from ``(open_time, o, h, l, c, v)`` rows: dedupe (last wins), sort,
    drop non-positive closes."""
    by_t: dict[int, tuple[float, float, float, float, float]] = {}
    for t, o, h, lo, c, v in rows:
        if not (math.isfinite(c) and c > 0):
            continue
        by_t[int(t) + interval] = (o, h, lo, c, v)
    ts = sorted(by_t)
    arr = np.array([by_t[t] for t in ts], dtype=np.float64).reshape(len(ts), 5)
    return Candles(
        np.array(ts, dtype=np.int64),
        arr[:, 0],
        arr[:, 1],
        arr[:, 2],
        arr[:, 3],
        arr[:, 4],
        interval,
        source,
        volume_kind,
    )


def merge_candles(parts: Sequence[Candles]) -> Candles:
    """Concatenate chunks (later chunks win on a repeated close time)."""
    rows = []
    for c in parts:
        for i in range(len(c)):
            rows.append((int(c.ts[i]) - c.interval, c.open[i], c.high[i], c.low[i], c.close[i], c.volume[i]))
    first = parts[0]
    return candles_from_rows(rows, first.interval, first.source, first.volume_kind)


def write_candles_csv(c: Candles, file: str | Path) -> Path:
    """``ts_iso,ts,price_usd,open,high,low,volume,volume_kind`` (``price_usd`` = close) — a price
    CSV that :func:`ybcal.data.loaders.load_price_csv` reads as is."""
    file = Path(file)
    with file.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(CANDLE_COLUMNS)
        for i in range(len(c)):
            v = float(c.volume[i])
            w.writerow(
                [
                    iso(int(c.ts[i])),
                    int(c.ts[i]),
                    repr(float(c.close[i])),
                    repr(float(c.open[i])),
                    repr(float(c.high[i])),
                    repr(float(c.low[i])),
                    "" if math.isnan(v) else repr(v),
                    c.volume_kind,
                ]
            )
    return file


def read_candles_csv(file: str | Path, interval: int | None = None) -> Candles:
    """Read :func:`write_candles_csv` output back."""
    rows = []
    kind = ""
    with Path(file).open() as fh:
        for r in csv.DictReader(fh):
            t = parse_ts(r["ts"])
            v = float(r["volume"]) if r.get("volume") else math.nan
            kind = r.get("volume_kind") or kind
            rows.append((t, float(r["open"]), float(r["high"]), float(r["low"]), float(r["price_usd"]), v))
    ts = np.array([r[0] for r in rows], dtype=np.int64)
    step = interval or (int(np.median(np.diff(ts))) if len(ts) > 1 else 3600)
    return candles_from_rows([(t - step, *rest) for t, *rest in rows], step, str(file), kind)


# ---------------------------------------------------------------------------------------------------
# CoinMarketCap (unofficial public data-api)


def parse_cmc_historical(obj: Any, interval: int) -> Candles:
    """``{"data": {"quotes": [{"timeOpen", "quote": {open, high, low, close, volume}}]}}``.

    ``volume`` is CoinMarketCap's 24 h USD volume at the candle's close."""
    try:
        quotes = obj["data"]["quotes"]
    except (KeyError, TypeError) as e:
        raise FetchError(f"CoinMarketCap reply has no data.quotes ({str(obj)[:200]})") from e
    rows = []
    for q in quotes:
        try:
            t = parse_ts(q["timeOpen"])
            qq = q["quote"]
            rows.append(
                (
                    t,
                    _finite(qq["open"], "open"),
                    _finite(qq["high"], "high"),
                    _finite(qq["low"], "low"),
                    _finite(qq["close"], "close"),
                    _finite(qq["volume"], "volume") if qq.get("volume") is not None else math.nan,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return candles_from_rows(rows, interval, "coinmarketcap:historical", "usd_24h")


def fetch_cmc_history(
    interval: str = "hourly",
    *,
    coin: str = "ycash",
    start: int | None = None,
    end: int | None = None,
    client: HttpClient | None = None,
) -> FetchResult:
    """Hourly or daily USD candles from CoinMarketCap, chunked; ``start`` defaults to the fork."""
    if interval not in CMC_CHUNK_DAYS:
        raise ValueError(f"interval must be one of {tuple(CMC_CHUNK_DAYS)}")
    cid = CMC_IDS.get(coin)
    if cid is None:
        try:
            cid = int(coin)
        except ValueError as e:
            raise ValueError(
                f"unknown CoinMarketCap coin {coin!r}; known: {sorted(CMC_IDS)} or a numeric id"
            ) from e
    client = client or HttpClient(max_body=HISTORY_MAX_BODY_BYTES, min_interval=2.0)
    end = int(end if end is not None else time.time())
    start = int(start if start is not None else YCASH_FORK_TS)
    step = INTERVAL_SECONDS[interval]
    chunk = CMC_CHUNK_DAYS[interval] * 86400
    urls: list[str] = []
    parts: list[Candles] = []
    t = start
    while t < end:
        t1 = min(end, t + chunk)
        url = f"{CMC_HISTORICAL}?id={cid}&convertId={CMC_USD}&timeStart={t}&timeEnd={t1}&interval={interval}"
        urls.append(url)
        part = parse_cmc_historical(client.get_json(url, max_body=HISTORY_MAX_BODY_BYTES), step)
        if len(part):
            parts.append(part)
        t = t1
    if not parts:
        raise FetchError("CoinMarketCap returned no candles for the range")
    c = merge_candles(parts)
    c.source = f"coinmarketcap:historical:{coin}:{interval}"
    res = FetchResult("coinmarketcap", urls, c.to_series())
    res.notes.append(
        f"CoinMarketCap data-api (unofficial, no key), {interval} in {len(urls)} chunks; "
        "price = candle close stamped at close time; volume = 24 h USD at close"
    )
    res.candles = c
    return res


# ---------------------------------------------------------------------------------------------------
# CoinCodex


def parse_coincodex(obj: Any, symbol: str) -> PriceSeries:
    """``{SYMBOL: [[ts, price, volume_24h, market_cap], …]}``; null prices are skipped."""
    try:
        pts = obj[symbol]
    except (KeyError, TypeError) as e:
        raise FetchError(f"CoinCodex reply has no {symbol!r} list") from e
    by_t: dict[int, tuple[float, float]] = {}
    skipped = 0
    for p in pts:
        try:
            price = _finite(p[1], "price")
        except (FetchError, IndexError, TypeError):
            skipped += 1
            continue
        if price <= 0:
            skipped += 1
            continue
        vol = p[2] if len(p) > 2 and p[2] is not None else math.nan
        by_t[int(p[0])] = (price, float(vol))
    ts = sorted(by_t)
    return PriceSeries(
        np.array(ts, dtype=np.int64),
        np.array([by_t[t][0] for t in ts]),
        "coincodex:history",
        len(pts),
        skipped,
        0,
        0,
        np.array([by_t[t][1] for t in ts]),
    )


def daily_last(series: PriceSeries) -> PriceSeries:
    """One point per UTC day: the last observation of the day, stamped at the next midnight
    (the day's close, the convention of :class:`Candles`)."""
    day = series.ts // 86400
    keep = np.r_[day[1:] != day[:-1], True]
    vol = series.volume_usd[keep] if series.volume_usd is not None else None
    return PriceSeries(
        (day[keep] + 1) * 86400, series.price_usd[keep], series.source, int(keep.sum()), 0, 0, 0, vol
    )


def fetch_coincodex_daily(
    *,
    symbol: str = "YEC",
    start: int | None = None,
    end: int | None = None,
    client: HttpClient | None = None,
    chunk_days: int = COINCODEX_CHUNK_DAYS,
) -> FetchResult:
    """Daily closes from CoinCodex, reduced with :func:`daily_last`.

    CoinCodex thins a reply to roughly 600 points whatever ``samples`` asks for (a 7-year call
    comes back 3-daily), so the range is fetched in ``chunk_days`` chunks at 4 samples a day."""
    client = client or HttpClient(max_body=HISTORY_MAX_BODY_BYTES)
    start = int(start if start is not None else YCASH_FORK_TS)
    end = int(end if end is not None else time.time())
    urls: list[str] = []
    parts: list[PriceSeries] = []
    skipped = 0
    t = start
    while t < end:
        t1 = min(end, t + chunk_days * 86400)
        d0 = datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%d")
        d1 = datetime.fromtimestamp(t1, UTC).strftime("%Y-%m-%d")
        samples = max(2, 4 * ((t1 - t) // 86400 + 1))
        url = f"{COINCODEX_HISTORY}/{urllib.parse.quote(symbol)}/{d0}/{d1}/{samples}"
        urls.append(url)
        part = parse_coincodex(client.get_json(url, max_body=HISTORY_MAX_BODY_BYTES), symbol)
        skipped += part.rows_skipped
        parts.append(part)
        t = t1 + 86400  # the date-granular API includes d1: start the next chunk the day after
    ts = np.concatenate([p.ts for p in parts])
    px = np.concatenate([p.price_usd for p in parts])
    vol = np.concatenate(
        [p.volume_usd if p.volume_usd is not None else np.full(len(p), np.nan) for p in parts]
    )
    order = np.argsort(ts, kind="stable")
    ts, px, vol = ts[order], px[order], vol[order]
    keep = np.r_[ts[1:] != ts[:-1], True]
    raw = PriceSeries(
        ts[keep], px[keep], "coincodex:history", len(ts), skipped, int((~keep).sum()), 0, vol[keep]
    )
    series = daily_last(raw)
    series.source = "coincodex:history:daily"
    res = FetchResult("coincodex", urls, series)
    res.notes.append(
        f"CoinCodex get_coin_history in {len(urls)} chunks of <= {chunk_days} days ({skipped} null points "
        "skipped), reduced to the last point of each UTC day stamped at the next midnight"
    )
    return res


# ---------------------------------------------------------------------------------------------------
# Venue candles


def parse_nonkyc_candles(obj: Any, interval: int = 3600) -> Candles:
    """``{"bars": [{"time" (ms, open), open, high, low, close, volume (base)}]}``."""
    if not isinstance(obj, dict) or not isinstance(obj.get("bars"), list):
        raise FetchError("nonkyc candles reply has no 'bars' list")
    rows = []
    for b in obj["bars"]:
        try:
            rows.append(
                (
                    parse_ts(b["time"]),
                    _finite(b["open"], "open"),
                    _finite(b["high"], "high"),
                    _finite(b["low"], "low"),
                    _finite(b["close"], "close"),
                    _finite(b.get("volume", math.nan), "volume") if b.get("volume") is not None else math.nan,
                )
            )
        except (KeyError, TypeError, ValueError, FetchError):
            continue
    return candles_from_rows(rows, interval, "nonkyc:candles", "base_per_candle")


def fetch_nonkyc_candles(
    *,
    symbol: str = "YEC_USDT",
    start: int | None = None,
    end: int | None = None,
    client: HttpClient | None = None,
    chunk_days: int = 365,
) -> FetchResult:
    """Hourly candles from nonkyc.io (USDT at par)."""
    client = client or HttpClient(max_body=HISTORY_MAX_BODY_BYTES)
    end = int(end if end is not None else time.time())
    start = int(start if start is not None else end - 4 * 365 * 86400)
    urls: list[str] = []
    parts: list[Candles] = []
    t = start
    while t < end:
        t1 = min(end, t + chunk_days * 86400)
        url = (
            f"{NONKYC_BASE}/api/v2/market/candles?symbol={urllib.parse.quote(symbol)}"
            f"&resolution=60&from={t}&to={t1}"
        )
        urls.append(url)
        part = parse_nonkyc_candles(client.get_json(url, max_body=HISTORY_MAX_BODY_BYTES))
        if len(part):
            parts.append(part)
        t = t1
    if not parts:
        raise FetchError("nonkyc returned no candles for the range")
    c = merge_candles(parts)
    c.source = f"nonkyc:candles:{symbol}"
    res = FetchResult("nonkyc:candles", urls, c.to_series())
    res.notes.append("nonkyc hourly candles; close stamped at close time; volume = base units per candle")
    res.candles = c
    return res


def parse_peatio_klines(obj: Any, interval: int = 3600) -> Candles:
    """``[[open_time, o, h, l, c, v], …]`` (Peatio/OpenDAX ``k-line``)."""
    if not isinstance(obj, list):
        raise FetchError(f"k-line reply is not a list ({str(obj)[:200]})")
    rows = []
    for k in obj:
        try:
            rows.append(
                (
                    parse_ts(k[0]),
                    _finite(k[1], "open"),
                    _finite(k[2], "high"),
                    _finite(k[3], "low"),
                    _finite(k[4], "close"),
                    _finite(k[5], "volume"),
                )
            )
        except (IndexError, TypeError, ValueError, FetchError):
            continue
    return candles_from_rows(rows, interval, "safetrade:k-line", "base_per_candle")


def fetch_safetrade_klines(
    *,
    market: str = "yecusdt",
    start: int | None = None,
    end: int | None = None,
    client: HttpClient | None = None,
    base_url: str = SAFETRADE_BASE,
) -> FetchResult:
    """Hourly candles from SafeTrade, paged backwards (the API returns the latest ≤ 10,000 in a
    range), until a page returns nothing new or ``start`` is reached."""
    client = client or HttpClient(max_body=HISTORY_MAX_BODY_BYTES)
    end = int(end if end is not None else time.time())
    start = int(start if start is not None else end - 4 * 365 * 86400)
    urls: list[str] = []
    parts: list[Candles] = []
    hi = end
    while hi > start:
        url = (
            f"{base_url}/api/v2/trade/public/markets/{urllib.parse.quote(market)}/k-line"
            f"?period=60&time_from={start}&time_to={hi}&limit=10000"
        )
        urls.append(url)
        part = parse_peatio_klines(client.get_json(url, max_body=HISTORY_MAX_BODY_BYTES))
        if not len(part):
            break
        parts.append(part)
        first_open = int(part.ts[0]) - part.interval
        if first_open >= hi or first_open <= start:
            break
        hi = first_open - 1
    if not parts:
        raise FetchError("SafeTrade returned no candles for the range")
    c = merge_candles(parts)
    c.source = f"safetrade:k-line:{market}"
    res = FetchResult("safetrade:k-line", urls, c.to_series())
    res.notes.append("SafeTrade hourly k-line; close stamped at close time; volume = base units per candle")
    res.candles = c
    return res


# ---------------------------------------------------------------------------------------------------
# Order books


BOOK_COLUMNS = ("ts_iso", "ts", "venue", "side", "price_usd", "size_yec")


def parse_nonkyc_orderbook(obj: Any) -> list[tuple[str, float, float]]:
    """``{"bids": [{"price", "quantity"}], "asks": [...]}`` → ``(side, price, size)``."""
    if not isinstance(obj, dict):
        raise FetchError("nonkyc orderbook reply is not an object")
    out = []
    for side, key in (("bid", "bids"), ("ask", "asks")):
        for lvl in obj.get(key) or []:
            try:
                out.append((side, _finite(lvl["price"], "price"), _finite(lvl["quantity"], "quantity")))
            except (KeyError, TypeError, FetchError):
                continue
    return out


def parse_peatio_depth(obj: Any) -> list[tuple[str, float, float]]:
    """``{"bids": [[price, size]], "asks": [...]}`` → ``(side, price, size)``."""
    if not isinstance(obj, dict):
        raise FetchError("depth reply is not an object")
    out = []
    for side, key in (("bid", "bids"), ("ask", "asks")):
        for lvl in obj.get(key) or []:
            try:
                out.append((side, _finite(lvl[0], "price"), _finite(lvl[1], "size")))
            except (IndexError, TypeError, FetchError):
                continue
    return out


#: Venues :func:`fetch_orderbooks` reads: name → (url template, parser, quote currency).
BOOK_VENUES: dict[str, tuple[str, Any, str]] = {
    "nonkyc_io:YEC_USDT": (
        NONKYC_BASE + "/api/v2/market/orderbook?symbol=YEC_USDT&depth=500",
        parse_nonkyc_orderbook,
        "USDT",
    ),
    "nonkyc_io:YEC_BTC": (
        NONKYC_BASE + "/api/v2/market/orderbook?symbol=YEC_BTC&depth=500",
        parse_nonkyc_orderbook,
        "BTC",
    ),
    "safe_trade:yecusdt": (
        SAFETRADE_BASE + "/api/v2/trade/public/markets/yecusdt/depth?limit=500",
        parse_peatio_depth,
        "USDT",
    ),
}


def fetch_orderbooks(
    venues: Iterable[str] | None = None,
    *,
    client: HttpClient | None = None,
    now: int | None = None,
) -> FetchResult:
    """One snapshot of every venue's book, as rows ``ts_iso,ts,venue,side,price_usd,size_yec``
    sharing one ``ts``. USDT is taken at par; a BTC-quoted book is converted at nonkyc's BTC_USDT
    last price. A venue that fails is skipped and named in ``notes``."""
    client = client or HttpClient(min_interval=0.5)
    t_now = int(now if now is not None else time.time())
    names = list(venues) if venues is not None else list(BOOK_VENUES)
    rows: list[dict[str, Any]] = []
    urls: list[str] = []
    notes: list[str] = []
    btc_usd: float | None = None
    for name in names:
        url, parser, quote = BOOK_VENUES[name]
        urls.append(url)
        try:
            levels = parser(client.get_json(url))
            mult = 1.0
            if quote == "BTC":
                if btc_usd is None:
                    burl = NONKYC_BASE + "/api/v2/market/getbysymbol/BTC_USDT"
                    urls.append(burl)
                    btc_usd = _finite(client.get_json(burl)["lastPriceNumber"], "BTC_USDT")
                mult = btc_usd
        except (FetchError, KeyError, TypeError) as e:
            notes.append(f"{name}: {e}")
            continue
        for side, p, s in levels:
            rows.append(
                {
                    "ts_iso": iso(t_now),
                    "ts": t_now,
                    "venue": name,
                    "side": side,
                    "price_usd": p * mult,
                    "size_yec": s,
                }
            )
    res = FetchResult("orderbooks", urls, rows=rows, notes=notes)
    return res


# ---------------------------------------------------------------------------------------------------
# Block miners (Inzyght explorer)


POOL_COLUMNS = ("height", "payout_key", "time")


def parse_inzyght_blocks(obj: Any) -> list[tuple[int, str, int]]:
    """``{"data": [{"height", "miner", "time"}]}`` → ``(height, miner, time)``; rows without a
    miner are kept with key ``"?"`` (counted by the caller)."""
    if not isinstance(obj, dict) or not isinstance(obj.get("data"), list):
        raise FetchError("explorer reply has no 'data' list")
    out = []
    for b in obj["data"]:
        try:
            out.append((int(b["height"]), str(b.get("miner") or "?"), int(b.get("time") or 0)))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def fetch_inzyght_blocks(
    n_blocks: int,
    *,
    client: HttpClient | None = None,
    base_url: str = INZYGHT_BASE,
    progress: Any = None,
) -> FetchResult:
    """The ``n_blocks`` most recent blocks' coinbase payout addresses (``miner``), newest first in
    the API, returned ascending by height. Paged ``INZYGHT_PAGE`` at a time, politely spaced."""
    client = client or HttpClient(min_interval=1.0)
    rows: dict[int, tuple[str, int]] = {}
    urls: list[str] = []
    start = 0
    while len(rows) < n_blocks:
        length = min(INZYGHT_PAGE, n_blocks - len(rows))
        url = f"{base_url}/api/v1/blocks?draw=1&start={start}&length={length}"
        if not urls:
            urls.append(url)
        page = parse_inzyght_blocks(client.get_json(url))
        if not page:
            break
        for h, m, t in page:
            rows[h] = (m, t)
        start += len(page)
        if progress:
            progress(len(rows))
    urls.append(f"{base_url}/api/v1/blocks?draw=1&start=<0..{start}>&length={INZYGHT_PAGE}")
    res = FetchResult("inzyght:blocks", urls)
    res.rows = [{"height": h, "payout_key": rows[h][0], "time": rows[h][1]} for h in sorted(rows)]
    res.notes.append("payout_key = the explorer's 'miner' field (first transparent coinbase output address)")
    return res


# ---------------------------------------------------------------------------------------------------
# Splice and compare


@dataclass
class Overlap:
    """Agreement of two price series on their common timestamps."""

    n: int
    first: int | None
    last: int | None
    median_abs_diff_bps: float
    p95_abs_diff_bps: float
    return_corr: float  #: correlation of log returns on consecutive common points
    level_ratio_median: float  #: median of a/b

    def summary(self) -> str:
        if not self.n:
            return "no overlap"
        return (
            f"overlap {self.n} points ({iso(self.first or 0)} .. {iso(self.last or 0)}): "
            f"median |diff| {self.median_abs_diff_bps:.0f} bps, p95 {self.p95_abs_diff_bps:.0f} bps, "
            f"return correlation {self.return_corr:.3f}, median ratio {self.level_ratio_median:.4f}"
        )


def compare(a: PriceSeries, b: PriceSeries) -> Overlap:
    """Overlap statistics on exactly common timestamps (|a−b|·10⁴/min(a,b), as MINT-10)."""
    common, ia, ib = np.intersect1d(a.ts, b.ts, return_indices=True)
    if len(common) < 3:
        return Overlap(len(common), None, None, math.nan, math.nan, math.nan, math.nan)
    pa, pb = a.price_usd[ia], b.price_usd[ib]
    d = np.abs(pa - pb) * 1e4 / np.minimum(pa, pb)
    step = int(np.median(np.diff(common)))
    consec = np.diff(common) == step
    ra, rb = np.diff(np.log(pa))[consec], np.diff(np.log(pb))[consec]
    corr = float(np.corrcoef(ra, rb)[0, 1]) if len(ra) > 2 and ra.std() > 0 and rb.std() > 0 else math.nan
    return Overlap(
        len(common),
        int(common[0]),
        int(common[-1]),
        float(np.median(d)),
        float(np.percentile(d, 95)),
        corr,
        float(np.median(pa / pb)),
    )


def splice(
    primary: PriceSeries, secondary: PriceSeries, *, rescale: bool = False
) -> tuple[PriceSeries, dict[str, Any]]:
    """``secondary`` strictly before ``primary``'s first timestamp, then ``primary``.

    ``rescale=True`` multiplies the secondary part by the median primary/secondary ratio on the
    overlap (so the splice return carries no level step); the default keeps raw prices and reports
    the step. Volume is carried from each part."""
    cut = int(primary.ts[0])
    keep = secondary.ts < cut
    ov = compare(primary, secondary)
    factor = ov.level_ratio_median if rescale and math.isfinite(ov.level_ratio_median) else 1.0
    ts = np.r_[secondary.ts[keep], primary.ts]
    px = np.r_[secondary.price_usd[keep] * factor, primary.price_usd]
    va = secondary.volume_usd[keep] if secondary.volume_usd is not None else np.full(int(keep.sum()), np.nan)
    vb = primary.volume_usd if primary.volume_usd is not None else np.full(len(primary), np.nan)
    out = PriceSeries(
        ts,
        px,
        f"splice({secondary.source} < {iso(cut)} <= {primary.source})",
        len(ts),
        0,
        0,
        0,
        np.r_[va, vb],
    )
    step_bps = math.nan
    if keep.any():
        step_bps = float((primary.price_usd[0] / (secondary.price_usd[keep][-1] * factor) - 1) * 1e4)
    info = {
        "cut_ts": cut,
        "cut_iso": iso(cut),
        "secondary_points": int(keep.sum()),
        "primary_points": len(primary),
        "rescale_factor": factor,
        "splice_return_bps": step_bps,
        "overlap": ov.__dict__,
    }
    return out, info


# ---------------------------------------------------------------------------------------------------
# Spread reconstruction


def last_trade_asof(c: Candles, grid: np.ndarray, max_age: int | None = None) -> np.ndarray:
    """At each grid time, the close of the last candle **with volume > 0** that closed at or
    before it (a last-trade quote); 0 where none (or older than ``max_age`` seconds)."""
    traded = ~(c.volume <= 0)  # NaN volume (unknown) counts as traded
    ts, px = c.ts[traded], c.close[traded]
    idx = np.searchsorted(ts, grid, side="right") - 1
    out = np.where(idx >= 0, px[np.maximum(idx, 0)], 0.0)
    if max_age is not None:
        age = grid - np.where(idx >= 0, ts[np.maximum(idx, 0)], grid - max_age - 1)
        out = np.where(age > max_age, 0.0, out)
    return out


def reconstruct_spreads(
    aggregate: PriceSeries,
    venues: dict[str, Candles],
    *,
    start: int | None = None,
    end: int | None = None,
    max_age: int | None = None,
) -> list[list[str]]:
    """Rows in ``spreads.py log``'s layout on the aggregate's own (hourly) timestamps.

    ``coingecko`` = the aggregate's point at ``t`` (only timestamps the aggregate observed);
    ``safetrade``/``nonkyc`` = their last trade as of ``t`` (:func:`last_trade_asof`). A venue
    with no trade yet (or older than ``max_age``) is an empty cell with an ``errors`` note."""
    names = ("coingecko", "safetrade", "nonkyc")
    ts = aggregate.ts
    sel = np.ones(len(ts), dtype=bool)
    if start is not None:
        sel &= ts >= start
    if end is not None:
        sel &= ts <= end
    grid = ts[sel]
    cols = {"coingecko": aggregate.price_usd[sel]}
    for n in ("safetrade", "nonkyc"):
        cols[n] = last_trade_asof(venues[n], grid, max_age) if n in venues else np.zeros(len(grid))
    rows = []
    for k, t in enumerate(grid):
        cells, errs = [], []
        for n in names:
            v = cols[n][k]
            if v > 0:
                cells.append(str(round(v * 1e6)))
            else:
                cells.append("")
                errs.append(f"{n}: no trade in reconstruction")
        rows.append([iso(int(t)), str(int(t)), *cells, "; ".join(errs)])
    return rows


def write_spreads_rows(rows: list[list[str]], file: str | Path) -> Path:
    """Write ``spreads.py log`` rows with its header."""
    file = Path(file)
    with file.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(SPREADS_COLUMNS)
        w.writerows(rows)
    return file


# ---------------------------------------------------------------------------------------------------
# Quality: flat stretches


def flat_runs(prices: np.ndarray, rel_tol: float = 0.0) -> np.ndarray:
    """Lengths (in samples) of maximal runs of consecutive equal prices (|Δ|/p ≤ ``rel_tol``),
    counting runs of length ≥ 2 only; a run of length L contributes L − 1 zero returns."""
    p = np.asarray(prices, dtype=np.float64)
    if len(p) < 2:
        return np.zeros(0, dtype=np.int64)
    same = np.abs(np.diff(p)) <= rel_tol * np.abs(p[:-1])
    runs = []
    cur = 0
    for s in same.tolist():
        if s:
            cur += 1
        elif cur:
            runs.append(cur + 1)
            cur = 0
    if cur:
        runs.append(cur + 1)
    return np.array(runs, dtype=np.int64)


def flat_stats(prices: np.ndarray, step_seconds: int, rel_tol: float = 0.0) -> dict[str, float]:
    """Share of zero returns, number of flat runs, and their p50/p90/max length in hours."""
    runs = flat_runs(prices, rel_tol)
    n_ret = max(1, len(prices) - 1)
    h = step_seconds / 3600.0
    return {
        "zero_return_share": float((runs - 1).sum() / n_ret) if len(runs) else 0.0,
        "runs": len(runs),
        "runs_ge_6h": int((runs * h >= 6).sum()) if len(runs) else 0,
        "run_p50_hours": float(np.median(runs) * h) if len(runs) else 0.0,
        "run_p90_hours": float(np.percentile(runs, 90) * h) if len(runs) else 0.0,
        "run_max_hours": float(runs.max() * h) if len(runs) else 0.0,
    }


# ---------------------------------------------------------------------------------------------------
# Writing (``ybcal data fetch --source …`` for the sources of this module)


EXTRA_SOURCES: tuple[str, ...] = (
    "coinmarketcap",
    "coincodex",
    "nonkyc-candles",
    "safetrade-candles",
    "orderbooks",
    "inzyght",
)


def fetch_extra_to_csv(
    source: str,
    out: str | Path,
    *,
    coin: str = "ycash",
    granularity: str = "hourly",
    start: int | None = None,
    end: int | None = None,
    symbol: str | None = None,
    blocks: int = 40_000,
    client: HttpClient | None = None,
    now: int | None = None,
) -> FetchResult:
    """Fetch one of :data:`EXTRA_SOURCES` and write ``out`` plus ``<out>.provenance.json``.

    History and candle sources write a fresh candle/price CSV; ``orderbooks`` appends one snapshot
    (``ts_iso,ts,venue,side,price_usd,size_yec``); ``inzyght`` writes ``height,payout_key,time``."""
    from ybcal.data.fetch import append_rows, write_provenance
    from ybcal.data.loaders import write_price_csv

    out = Path(out)
    extra: dict[str, Any] = {}
    if source == "coinmarketcap":
        gran = "daily" if granularity == "daily" else "hourly"
        res = fetch_cmc_history(gran, coin=coin, start=start, end=end, client=client)
        write_candles_csv(res.candles, out)
        extra = {"coin": coin, "interval": gran, "cmc_id": CMC_IDS.get(coin, coin)}
    elif source == "coincodex":
        sym = symbol if symbol and "_" not in symbol else ("ZEC" if coin == "zcash" else "YEC")
        res = fetch_coincodex_daily(symbol=sym, start=start, end=end, client=client)
        assert res.series is not None
        write_price_csv(res.series, out)
        extra = {"symbol": sym}
    elif source == "nonkyc-candles":
        sym = symbol or "YEC_USDT"
        res = fetch_nonkyc_candles(symbol=sym, start=start, end=end, client=client)
        write_candles_csv(res.candles, out)
        extra = {"symbol": sym}
    elif source == "safetrade-candles":
        mkt = (symbol or "yecusdt").replace("_", "").lower()
        res = fetch_safetrade_klines(market=mkt, start=start, end=end, client=client)
        write_candles_csv(res.candles, out)
        extra = {"market": mkt}
    elif source == "orderbooks":
        res = fetch_orderbooks(client=client, now=now)
        append_rows(out, BOOK_COLUMNS, res.rows)
        extra = {"venues": list(BOOK_VENUES)}
    elif source == "inzyght":
        res = fetch_inzyght_blocks(blocks, client=client)
        append_rows_fresh(out, POOL_COLUMNS, res.rows)
        extra = {"blocks": len(res.rows)}
    else:
        raise ValueError(f"unknown source {source!r}; choose from {EXTRA_SOURCES}")
    rows = (
        len(res.candles)
        if res.candles is not None
        else len(res.series)
        if res.series is not None
        else len(res.rows)
    )
    write_provenance(out, res, rows=rows, start=start, end=end, **extra)
    return res


def append_rows_fresh(file: str | Path, columns: Iterable[str], rows: Iterable[dict[str, Any]]) -> Path:
    """Write rows to a new file (replacing any old one)."""
    from ybcal.data.fetch import append_rows

    file = Path(file)
    if file.exists():
        file.unlink()
    return append_rows(file, columns, rows)

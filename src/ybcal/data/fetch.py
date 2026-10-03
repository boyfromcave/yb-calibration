"""Download price data: CoinGecko ``market_chart``, CoinGecko tickers, nonkyc ``YEC_USDT`` (PLAN §4.1).

Ported in behaviour (not imported) from ycash6 ``contrib/yellowback/yellowback_price.py`` at
``7702d22`` — the presets ``coingecko_ticker`` and ``nonkyc_market``, the no-redirect opener, the
1 MiB body cap, the ``x-cg-demo-api-key`` header and the non-finite-JSON refusal — and from
``attest/calibrate/pinrate.py`` (``market_chart`` history). Standard library only (``urllib``).

Every fetch writes a CSV plus a provenance sidecar ``<csv>.provenance.json`` recording the source,
the URLs, ``fetched_at`` and the CSV's sha256, so a later run can prove which bytes it used.

The cloud sandbox this tool is developed in cannot reach these hosts; the owner runs ``ybcal data
fetch`` on a networked machine (``docs/data.md``). When the network is blocked the error says so.

Owner: WP-2.
"""

from __future__ import annotations

import csv
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from ybcal import __version__
from ybcal.config import sha256_file
from ybcal.data.loaders import PriceSeries, iso, parse_ts, write_price_csv

OWNER_WP = "WP-2"

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
NONKYC_BASE = "https://api.nonkyc.io"
MAX_BODY_BYTES = 1 << 20  # yellowback_price.py MAX_BODY_BYTES
HISTORY_MAX_BODY_BYTES = 16 << 20  # market_chart over years is larger than a ticker reply
USER_AGENT = f"ybcal/{__version__}"
#: CoinGecko returns hourly points for a ``market_chart/range`` span of 2–90 days; chunk at 89.
HOURLY_CHUNK_DAYS = 89
DATA_DOC = "docs/data.md"


class FetchError(RuntimeError):
    """A fetch failed after retries (HTTP error, bad shape, non-finite numbers)."""


class NetworkBlockedError(FetchError):
    """The host could not be reached at all (DNS, proxy, firewall) — not a venue error."""

    def __init__(self, url: str, reason: str) -> None:
        host = urllib.parse.urlsplit(url).hostname or url
        super().__init__(
            f"cannot reach {host} ({reason}). This machine's network blocks the price APIs "
            f"(the ybcal development sandbox does). Run `ybcal data fetch` on a networked machine and "
            f"copy the CSV (and its .provenance.json) into data/local/ — see {DATA_DOC} and data/README.md."
        )
        self.url = url
        self.reason = reason


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """3xx is an error, never followed (a redirect would carry the API-key header elsewhere)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def urlopen(req: urllib.request.Request, timeout: float) -> Any:
    """The single network entry point (tests replace it with a fake)."""
    return _OPENER.open(req, timeout=timeout)


def _read_capped(resp: Any, cap: int) -> bytes:
    length = resp.headers.get("Content-Length") if getattr(resp, "headers", None) is not None else None
    if length is not None and str(length).isdigit() and int(length) > cap:
        raise FetchError(f"reply body {length} bytes exceeds the {cap} byte cap")
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = resp.read(min(65536, cap + 1 - total))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > cap:
            raise FetchError(f"reply body exceeds the {cap} byte cap")


def _reject_constant(name: str) -> float:
    raise ValueError(f"non-finite JSON literal {name}")


_BLOCKED_HINTS = (
    "proxy",
    "tunnel",
    "name or service not known",
    "nodename nor servname",
    "temporary failure in name resolution",
    "network is unreachable",
    "connection refused",
    "no route to host",
    "getaddrinfo",
)


# ---------------------------------------------------------------------------------------------------
# HTTP client with retry, backoff and rate limiting


@dataclass
class HttpClient:
    """GET JSON with retries (exponential backoff, ``Retry-After`` honoured) and a minimum spacing
    between requests (CoinGecko's public API allows roughly 30 calls/minute; we default to one call
    every 2.5 s). ``sleep``/``clock`` are injectable for tests."""

    timeout: float = 30.0
    retries: int = 4
    backoff: float = 2.0
    min_interval: float = 2.5
    max_body: int = MAX_BODY_BYTES
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    log: list[str] = field(default_factory=list)
    _last: float | None = field(default=None, init=False, repr=False)

    def _throttle(self) -> None:
        now = self.clock()
        if self._last is not None and now - self._last < self.min_interval:
            self.sleep(self.min_interval - (now - self._last))
        self._last = self.clock()

    def get_json(
        self, url: str, headers: dict[str, str] | None = None, *, max_body: int | None = None
    ) -> Any:
        """Fetch and parse JSON. Raises :class:`NetworkBlockedError` or :class:`FetchError`."""
        hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})}
        cap = max_body or self.max_body
        last_err = ""
        blocked = False
        for attempt in range(self.retries + 1):
            self._throttle()
            req = urllib.request.Request(url, headers=hdrs)
            try:
                resp = urlopen(req, self.timeout)
                try:
                    raw = _read_capped(resp, cap)
                finally:
                    close = getattr(resp, "close", None)
                    if close:
                        close()
                try:
                    return json.loads(raw.decode(), parse_constant=_reject_constant)
                except ValueError as e:
                    raise FetchError(f"{url}: reply is not valid JSON ({e})") from e
            except urllib.error.HTTPError as e:
                retry_after = e.headers.get("Retry-After") if e.headers is not None else None
                last_err = f"HTTP {e.code} {e.reason}"
                # 403 from the egress proxy and 407 mean "blocked here", not a venue error
                if e.code == 407 or (e.code == 403 and "proxy" in str(e.reason).lower()):
                    raise NetworkBlockedError(url, last_err) from e
                if e.code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    delay = (
                        float(retry_after)
                        if retry_after and str(retry_after).isdigit()
                        else self.backoff * (2**attempt)
                    )
                    self.log.append(f"{url}: {last_err}; retry in {delay:g}s")
                    self.sleep(delay)
                    continue
                raise FetchError(f"{url}: {last_err}") from e
            except urllib.error.URLError as e:
                last_err = str(e.reason)
                blocked = any(h in last_err.lower() for h in _BLOCKED_HINTS)
            except (TimeoutError, ConnectionError, OSError) as e:
                last_err = f"{type(e).__name__}: {e}"
                blocked = any(h in last_err.lower() for h in _BLOCKED_HINTS)
            if attempt < self.retries:
                delay = self.backoff * (2**attempt)
                self.log.append(f"{url}: {last_err}; retry in {delay:g}s")
                self.sleep(delay)
        if blocked or last_err:
            raise NetworkBlockedError(url, last_err or "unreachable")
        raise FetchError(f"{url}: failed")  # pragma: no cover


def _cg_headers(api_key: str | None) -> dict[str, str]:
    return {"x-cg-demo-api-key": api_key} if api_key else {}


def _finite(v: Any, what: str) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError) as e:
        raise FetchError(f"{what} is not a number: {v!r}") from e
    if not math.isfinite(f):
        raise FetchError(f"{what} is not finite: {v!r}")
    return f


# ---------------------------------------------------------------------------------------------------
# CoinGecko market_chart


@dataclass
class FetchResult:
    """What a fetch produced: the rows, the URLs it read and the source label."""

    source: str
    urls: list[str]
    series: PriceSeries | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)
    fetched_at: str = field(default_factory=lambda: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
    notes: list[str] = field(default_factory=list)


def parse_market_chart(obj: Any) -> PriceSeries:
    """``{"prices": [[ms, price], …], "total_volumes": [[ms, vol], …], …}`` → :class:`PriceSeries`."""
    if not isinstance(obj, dict) or not isinstance(obj.get("prices"), list):
        raise FetchError("market_chart reply has no 'prices' list")
    vols = {}
    for pt in obj.get("total_volumes") or []:
        try:
            vols[int(pt[0]) // 1000] = _finite(pt[1], "volume")
        except (TypeError, IndexError, ValueError, FetchError):
            continue
    pts: dict[int, float] = {}
    for pt in obj["prices"]:
        if not isinstance(pt, list | tuple) or len(pt) < 2 or pt[1] is None:
            continue
        p = _finite(pt[1], "price")
        if p > 0:
            pts[int(pt[0]) // 1000] = p
    ts = sorted(pts)
    return PriceSeries(
        np.array(ts, dtype=np.int64),
        np.array([pts[t] for t in ts]),
        "coingecko:market_chart",
        len(obj["prices"]),
        len(obj["prices"]) - len(ts),
        0,
        0,
        np.array([vols.get(t, math.nan) for t in ts]),
    )


def _merge(series: Iterable[PriceSeries]) -> PriceSeries:
    pts: dict[int, tuple[float, float]] = {}
    for s in series:
        vol = s.volume_usd if s.volume_usd is not None else np.full(len(s), np.nan)
        for t, p, v in zip(s.ts, s.price_usd, vol, strict=True):
            pts[int(t)] = (float(p), float(v))
    ts = sorted(pts)
    return PriceSeries(
        np.array(ts, dtype=np.int64),
        np.array([pts[t][0] for t in ts]),
        "coingecko:market_chart",
        len(ts),
        0,
        0,
        0,
        np.array([pts[t][1] for t in ts]),
    )


def fetch_coingecko_market_chart(
    days: int = 365,
    *,
    coin: str = "ycash",
    vs: str = "usd",
    granularity: str = "auto",
    api_key: str | None = None,
    client: HttpClient | None = None,
    now: int | None = None,
) -> FetchResult:
    """YEC/USD history from CoinGecko.

    ``granularity``:

    * ``auto`` — one ``/coins/{coin}/market_chart?days=N`` call: CoinGecko returns hourly points for
      2–90 days and daily points beyond (5-minutely for 1 day).
    * ``hourly`` — for ``days > 90``, consecutive ``/market_chart/range`` calls of ≤ 89 days each
      (hourly granularity), merged. The public/demo plan limits history to the past 365 days.
    * ``daily`` — ``market_chart?days=N&interval=daily``.
    """
    client = client or HttpClient(max_body=HISTORY_MAX_BODY_BYTES)
    hdr = _cg_headers(api_key)
    base = f"{COINGECKO_BASE}/coins/{urllib.parse.quote(coin)}"
    urls: list[str] = []
    notes: list[str] = []
    if granularity == "hourly" and days > 90:
        end = int(now if now is not None else time.time())
        start = end - days * 86400
        parts = []
        t = start
        while t < end:
            t1 = min(end, t + HOURLY_CHUNK_DAYS * 86400)
            url = f"{base}/market_chart/range?vs_currency={vs}&from={t}&to={t1}"
            urls.append(url)
            parts.append(parse_market_chart(client.get_json(url, hdr, max_body=HISTORY_MAX_BODY_BYTES)))
            t = t1
        series = _merge(parts)
        notes.append(f"hourly via {len(urls)} market_chart/range calls of <= {HOURLY_CHUNK_DAYS} days")
    else:
        url = f"{base}/market_chart?vs_currency={vs}&days={int(days)}"
        if granularity == "daily":
            url += "&interval=daily"
        urls.append(url)
        series = parse_market_chart(client.get_json(url, hdr, max_body=HISTORY_MAX_BODY_BYTES))
        notes.append(
            "CoinGecko auto granularity: hourly for 2-90 days, daily beyond"
            if granularity == "auto"
            else "daily"
        )
    series.source = "coingecko:market_chart"
    return FetchResult("coingecko", urls, series, notes=notes)


# ---------------------------------------------------------------------------------------------------
# CoinGecko tickers (SafeTrade etc.)


TICKER_COLUMNS = (
    "ts_iso",
    "ts",
    "venue",
    "target",
    "price_usd",
    "last",
    "spread_bps",
    "volume_base",
    "converted_volume_usd",
    "last_traded_at",
    "is_stale",
    "is_anomaly",
)


def parse_tickers(
    obj: Any, *, markets: Iterable[str] | None = None, now: int | None = None
) -> list[dict[str, Any]]:
    """Rows from ``/coins/{id}/tickers``: one per ticker on the selected markets.

    Fields follow yellowback_price.py's ``coingecko_ticker`` preset: ``market.identifier``,
    ``target``, ``converted_last.usd``, ``bid_ask_spread_percentage`` (×100 → bps),
    ``last_traded_at`` (ISO), ``is_stale``, ``is_anomaly``, ``volume`` (24 h, base units).
    """
    if not isinstance(obj, dict) or not isinstance(obj.get("tickers"), list):
        raise FetchError("tickers reply has no 'tickers' list")
    want = set(markets) if markets else None
    t_now = int(now if now is not None else time.time())
    rows = []
    for tk in obj["tickers"]:
        try:
            venue = tk["market"]["identifier"]
        except (KeyError, TypeError):
            continue
        if want is not None and venue not in want:
            continue
        conv = tk.get("converted_last") or {}
        cvol = tk.get("converted_volume") or {}
        spread = tk.get("bid_ask_spread_percentage")
        traded = tk.get("last_traded_at") or tk.get("timestamp")
        rows.append(
            {
                "ts_iso": iso(t_now),
                "ts": t_now,
                "venue": venue,
                "target": tk.get("target", ""),
                "price_usd": _finite(conv.get("usd"), f"{venue} converted_last.usd"),
                "last": _finite(tk.get("last"), f"{venue} last") if tk.get("last") is not None else math.nan,
                "spread_bps": _finite(spread, "spread") * 100 if spread is not None else math.nan,
                "volume_base": _finite(tk.get("volume"), "volume")
                if tk.get("volume") is not None
                else math.nan,
                "converted_volume_usd": _finite(cvol.get("usd"), "volume")
                if cvol.get("usd") is not None
                else math.nan,
                "last_traded_at": parse_ts(traded) if traded else "",
                "is_stale": bool(tk.get("is_stale", False)),
                "is_anomaly": bool(tk.get("is_anomaly", False)),
            }
        )
    return rows


def fetch_coingecko_tickers(
    *,
    coin: str = "ycash",
    exchanges: Iterable[str] = ("safe_trade",),
    api_key: str | None = None,
    client: HttpClient | None = None,
    now: int | None = None,
) -> FetchResult:
    """A snapshot of CoinGecko's tickers for ``coin`` on the given exchange identifiers."""
    client = client or HttpClient()
    ex = list(exchanges)
    url = f"{COINGECKO_BASE}/coins/{urllib.parse.quote(coin)}/tickers"
    if ex:
        url += "?exchange_ids=" + urllib.parse.quote(",".join(ex))
    rows = parse_tickers(client.get_json(url, _cg_headers(api_key)), markets=ex or None, now=now)
    return FetchResult("coingecko:tickers", [url], rows=rows)


# ---------------------------------------------------------------------------------------------------
# nonkyc


NONKYC_COLUMNS = (
    "ts_iso",
    "ts",
    "venue",
    "symbol",
    "price_usd",
    "bid",
    "ask",
    "spread_bps",
    "volume_base",
    "last_trade_at",
)


def parse_nonkyc_market(obj: Any, *, symbol: str = "YEC_USDT", now: int | None = None) -> dict[str, Any]:
    """Row from ``/api/v2/market/getbysymbol/{symbol}`` (yellowback_price.py ``nonkyc_market``):
    ``lastPriceNumber``, ``bestBidNumber``, ``bestAskNumber``, ``lastTradeAt`` (ms), ``volumeNumber``.
    USDT is taken at par with USD (as the node's agents do)."""
    if not isinstance(obj, dict):
        raise FetchError("nonkyc reply is not an object")
    try:
        last = _finite(obj["lastPriceNumber"], "lastPriceNumber")
    except KeyError as e:
        raise FetchError("nonkyc reply lacks lastPriceNumber") from e
    bid = _finite(obj["bestBidNumber"], "bestBidNumber") if obj.get("bestBidNumber") is not None else math.nan
    ask = _finite(obj["bestAskNumber"], "bestAskNumber") if obj.get("bestAskNumber") is not None else math.nan
    mid = (bid + ask) / 2
    spread = (ask - bid) / mid * 10_000 if mid > 0 else math.nan
    t_now = int(now if now is not None else time.time())
    lt = obj.get("lastTradeAt")
    return {
        "ts_iso": iso(t_now),
        "ts": t_now,
        "venue": "nonkyc_io",
        "symbol": symbol,
        "price_usd": last,
        "bid": bid,
        "ask": ask,
        "spread_bps": spread,
        "volume_base": _finite(obj["volumeNumber"], "volumeNumber")
        if obj.get("volumeNumber") is not None
        else math.nan,
        "last_trade_at": parse_ts(lt) if lt is not None else "",
    }


def fetch_nonkyc_market(
    *,
    symbol: str = "YEC_USDT",
    client: HttpClient | None = None,
    now: int | None = None,
    base_url: str = NONKYC_BASE,
) -> FetchResult:
    """A snapshot of nonkyc's ``symbol`` market."""
    client = client or HttpClient()
    url = f"{base_url}/api/v2/market/getbysymbol/{urllib.parse.quote(symbol)}"
    row = parse_nonkyc_market(client.get_json(url), symbol=symbol, now=now)
    return FetchResult("nonkyc", [url], rows=[row])


# ---------------------------------------------------------------------------------------------------
# Writing


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return "" if math.isnan(v) else repr(v)
    if isinstance(v, bool):
        return "1" if v else "0"
    return str(v)


def append_rows(file: str | Path, columns: Iterable[str], rows: Iterable[dict[str, Any]]) -> Path:
    """Append snapshot rows (header written when the file is new/empty), like ``spreads.py log``."""
    file = Path(file)
    cols = list(columns)
    new = not file.exists() or file.stat().st_size == 0
    with file.open("a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(cols)
        for r in rows:
            w.writerow([_fmt(r.get(c, "")) for c in cols])
    return file


def write_provenance(file: str | Path, result: FetchResult, **extra: Any) -> Path:
    """Write ``<file>.provenance.json``: source, urls, fetched_at, sha256 of ``file``, ybcal version."""
    file = Path(file)
    side = Path(str(file) + ".provenance.json")
    history: list[dict[str, Any]] = []
    if side.exists():
        try:
            prev = json.loads(side.read_text())
            history = list(prev.get("history", []))
            history.append({k: prev.get(k) for k in ("fetched_at", "urls", "sha256")})
        except ValueError:
            history = []
    doc = {
        "file": file.name,
        "source": result.source,
        "urls": result.urls,
        "fetched_at": result.fetched_at,
        "sha256": sha256_file(file),
        "provenance": "real",
        "tool": f"ybcal {__version__}",
        "notes": result.notes,
        **extra,
    }
    if history:
        doc["history"] = history[-50:]
    side.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return side


def fetch_to_csv(
    source: str,
    out: str | Path,
    *,
    days: int = 365,
    coin: str = "ycash",
    granularity: str = "auto",
    api_key: str | None = None,
    exchanges: Iterable[str] = ("safe_trade",),
    symbol: str = "YEC_USDT",
    client: HttpClient | None = None,
    now: int | None = None,
) -> FetchResult:
    """Fetch ``source`` (``coingecko`` | ``tickers`` | ``nonkyc``) and write ``out`` + provenance.

    ``coingecko`` writes a fresh history CSV (``ts_iso,ts,price_usd,volume_24h_usd``); the snapshot
    sources append one row per ticker (run them from cron to build a spread/depth log).
    """
    if source == "coingecko":
        res = fetch_coingecko_market_chart(
            days, coin=coin, granularity=granularity, api_key=api_key, client=client, now=now
        )
        assert res.series is not None
        write_price_csv(res.series, out)
        write_provenance(out, res, days=days, granularity=granularity, coin=coin, rows=len(res.series))
    elif source == "tickers":
        res = fetch_coingecko_tickers(coin=coin, exchanges=exchanges, api_key=api_key, client=client, now=now)
        append_rows(out, TICKER_COLUMNS, res.rows)
        write_provenance(out, res, coin=coin, exchanges=list(exchanges))
    elif source == "nonkyc":
        res = fetch_nonkyc_market(symbol=symbol, client=client, now=now)
        append_rows(out, NONKYC_COLUMNS, res.rows)
        write_provenance(out, res, symbol=symbol)
    else:
        raise ValueError(f"unknown source {source!r}")
    return res

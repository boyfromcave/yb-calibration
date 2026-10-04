"""Import real data: price, spreads, pool-share and order-book depth CSVs (PLAN §4.1).

Every timestamp is UTC. A loader never guesses silently: duplicates, malformed rows and gaps are
counted and reported, and resampling onto a block/hour grid keeps a ``filled`` mask of the grid
points that were forward-filled rather than observed (PLAN §2.5 provenance).

Formats (documented in ``data/README.md``):

* **price** — ``ts,price_usd`` (``ts`` = unix seconds, unix ms, or ISO 8601; ``ts_iso`` is accepted
  when ``ts`` is absent; extra columns are ignored). This is also what ``pinrate.py --save-csv``
  and ``ybcal data fetch`` write.
* **spreads** — exactly the ``spreads.py log`` columns at ycash6 ``7702d22``:
  ``ts_iso,ts,coingecko_micro_usd,safetrade_micro_usd,nonkyc_micro_usd,errors``.
* **pool shares** (``--kind hashrate``) — ``height,payout_key`` (one row per block; the key is any
  stable pool identifier: payout address, coinbase tag, ``yed_listminers`` key).
* **depth** — either a summary ``ts,depth_2pct_usd,volume_24h_usd[,bid_depth_2pct_usd]`` or an
  order-book snapshot list ``ts,side,price_usd,size_yec[,venue]`` (``side`` = ``bid``/``ask``),
  summarised per snapshot to the USD depth within ±2 % of the mid (of each venue's own book when a
  ``venue`` column is present, summed over venues).

Owner: WP-2.
"""

from __future__ import annotations

import csv
import io
import itertools
import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

import numpy as np

from ybcal.data.pricepath import STEP_SECONDS, usd_to_micro
from ybcal.types import PricePath, Resolution

OWNER_WP = "WP-2"

#: ``spreads.py`` source names and columns, in its column order (ycash6 7702d22, spreads.py:46-50).
SPREADS_NAMES: tuple[str, ...] = ("coingecko", "safetrade", "nonkyc")
SPREADS_COLUMNS: tuple[str, ...] = ("ts_iso", "ts", *(f"{n}_micro_usd" for n in SPREADS_NAMES), "errors")

PRICE_TS_COLUMNS: tuple[str, ...] = ("ts", "timestamp", "time", "ts_iso", "date")
PRICE_VALUE_COLUMNS: tuple[str, ...] = ("price_usd", "price", "close")


class DataFormatError(ValueError):
    """A file does not have the expected columns, or has no usable rows."""


# ---------------------------------------------------------------------------------------------------
# Timestamps


def parse_ts(value: str | float | int) -> int:
    """Unix seconds (UTC) from unix seconds, unix milliseconds (> 1e11) or an ISO 8601 string.

    A naive ISO time is read as UTC; ``Z`` and offsets are honoured; sub-second digits are dropped.
    """
    if isinstance(value, int | float) and not isinstance(value, bool):
        f = float(value)
    else:
        s = str(value).strip()
        if not s:
            raise ValueError("empty timestamp")
        try:
            f = float(s)
        except ValueError:
            if s.endswith(("Z", "z")):
                s = s[:-1] + "+00:00"
            if " " in s and "T" not in s and len(s) > 10:
                s = s.replace(" ", "T", 1)
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return int(dt.timestamp())
    if not math.isfinite(f):
        raise ValueError("non-finite timestamp")
    if abs(f) > 1e11:  # milliseconds
        f /= 1000.0
    return int(f)


def iso(ts: int) -> str:
    """``YYYY-MM-DDTHH:MM:SSZ`` for unix seconds."""
    return datetime.fromtimestamp(int(ts), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _open(source: str | Path | TextIO) -> tuple[TextIO, str]:
    if hasattr(source, "read"):
        return source, getattr(source, "name", "<stream>")  # type: ignore[return-value]
    path = Path(source)  # type: ignore[arg-type]
    return io.StringIO(path.read_text()), str(path)


def _lower_fields(reader: csv.DictReader) -> list[str]:
    return [f.strip().lower() for f in (reader.fieldnames or [])]


# ---------------------------------------------------------------------------------------------------
# Gap report


@dataclass(frozen=True)
class GapReport:
    """Gaps in a timestamp series: consecutive samples more than ``factor × expected_step`` apart."""

    expected_step: int  #: seconds
    factor: float
    n_samples: int
    first: int | None
    last: int | None
    gaps: tuple[tuple[int, int, int], ...]  #: (ts_before, ts_after, seconds)
    missing_steps: int  #: expected samples absent inside the gaps

    @property
    def n_gaps(self) -> int:
        """Number of gaps."""
        return len(self.gaps)

    @property
    def longest(self) -> int:
        """Longest gap in seconds (0 when none)."""
        return max((g[2] for g in self.gaps), default=0)

    @property
    def span_seconds(self) -> int:
        """Last minus first timestamp."""
        return 0 if self.first is None or self.last is None else self.last - self.first

    @property
    def coverage(self) -> float:
        """Observed samples / expected samples over the span (1.0 = no gaps)."""
        expected = self.span_seconds // self.expected_step + 1 if self.n_samples else 0
        return 1.0 if expected <= 0 else min(1.0, self.n_samples / expected)

    def summary(self, limit: int = 10) -> str:
        """Human-readable lines."""
        if not self.n_samples:
            return "no samples"
        lines = [
            f"samples: {self.n_samples}  span: {self.span_seconds / 86400:.1f} days "
            f"({iso(self.first or 0)} .. {iso(self.last or 0)})  expected step: {self.expected_step} s",
            f"gaps (> {self.factor:g}x step): {self.n_gaps}  longest: {self.longest / 3600:.1f} h  "
            f"missing steps: {self.missing_steps}  coverage: {self.coverage:.1%}",
        ]
        for a, b, s in self.gaps[:limit]:
            lines.append(f"  {iso(a)} -> {iso(b)}  ({s / 3600:.1f} h)")
        if self.n_gaps > limit:
            lines.append(f"  ... {self.n_gaps - limit} more")
        return "\n".join(lines)


def infer_step(ts: np.ndarray) -> int:
    """The modal spacing of a sorted timestamp array (seconds); 3600 when fewer than two samples."""
    if len(ts) < 2:
        return STEP_SECONDS["hour"]
    d = np.diff(ts)
    d = d[d > 0]
    if not len(d):
        return STEP_SECONDS["hour"]
    vals, counts = np.unique(d, return_counts=True)
    return int(vals[np.argmax(counts)])


def gap_report(
    ts: Iterable[int] | np.ndarray, expected_step: int | None = None, factor: float = 1.5
) -> GapReport:
    """Report gaps in sorted timestamps (``expected_step`` defaults to the modal spacing)."""
    arr = np.asarray(list(ts) if not isinstance(ts, np.ndarray) else ts, dtype=np.int64)
    step = int(expected_step or infer_step(arr))
    gaps: list[tuple[int, int, int]] = []
    missing = 0
    if len(arr) > 1:
        d = np.diff(arr)
        for i in np.nonzero(d > factor * step)[0]:
            gaps.append((int(arr[i]), int(arr[i + 1]), int(d[i])))
            missing += round(d[i] / step) - 1
    return GapReport(
        step,
        factor,
        len(arr),
        int(arr[0]) if len(arr) else None,
        int(arr[-1]) if len(arr) else None,
        tuple(gaps),
        missing,
    )


# ---------------------------------------------------------------------------------------------------
# Price CSV


@dataclass
class PriceSeries:
    """An irregular, sorted, de-duplicated price series as read from a file."""

    ts: np.ndarray  #: int64 unix seconds, strictly ascending
    price_usd: np.ndarray  #: float64 USD per YEC, > 0
    source: str = ""
    rows_read: int = 0
    rows_skipped: int = 0  #: malformed, non-positive or non-finite rows
    duplicates: int = 0  #: rows dropped because their timestamp repeated (the last one wins)
    conflicting_duplicates: int = 0  #: of those, how many disagreed on the price
    volume_usd: np.ndarray | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.ts)

    def gaps(self, expected_step: int | None = None, factor: float = 1.5) -> GapReport:
        """Gap report over the raw timestamps."""
        return gap_report(self.ts, expected_step, factor)


def _dedupe(ts: list[int], vals: list[Any]) -> tuple[list[int], list[Any], int, int]:
    """Sort by ts, keep the last value per timestamp (pinrate.py's rule); count dropped and conflicts."""
    order = sorted(range(len(ts)), key=lambda i: (ts[i], i))
    out_t: list[int] = []
    out_v: list[Any] = []
    dups = conflicts = 0
    for i in order:
        if out_t and out_t[-1] == ts[i]:
            dups += 1
            if out_v[-1] != vals[i]:
                conflicts += 1
            out_v[-1] = vals[i]
        else:
            out_t.append(ts[i])
            out_v.append(vals[i])
    return out_t, out_v, dups, conflicts


def load_price_csv(source: str | Path | TextIO) -> PriceSeries:
    """Read a price CSV (``ts,price_usd``; see the module docstring). Raises :class:`DataFormatError`."""
    fh, name = _open(source)
    reader = csv.DictReader(fh)
    fields = _lower_fields(reader)
    tcol = next((c for c in PRICE_TS_COLUMNS if c in fields), None)
    pcol = next((c for c in PRICE_VALUE_COLUMNS if c in fields), None)
    if tcol is None or pcol is None:
        raise DataFormatError(
            f"{name}: need a timestamp column {PRICE_TS_COLUMNS} and a price column "
            f"{PRICE_VALUE_COLUMNS}; got {fields}"
        )
    vcol = next((c for c in ("volume_24h_usd", "volume_usd", "total_volume") if c in fields), None)
    # candle CSVs (ybcal.data.venues) carry ``volume`` + ``volume_kind``; only a 24 h USD figure
    # (CoinMarketCap's) is a USD volume — a venue candle's volume is base units per candle
    kind_col = vcol is None and "volume" in fields and "volume_kind" in fields
    if kind_col:
        vcol = "volume"
    ts: list[int] = []
    vals: list[tuple[float, float]] = []
    read = skipped = 0
    for raw in reader:
        rec = {(k or "").strip().lower(): (v or "") for k, v in raw.items()}
        read += 1
        try:
            tval = rec.get(tcol, "").strip() or rec.get("ts_iso", "").strip()
            t = parse_ts(tval)
            p = float(rec[pcol])
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        if not (math.isfinite(p) and p > 0):
            skipped += 1
            continue
        v = math.nan
        if vcol:
            try:
                v = float(rec.get(vcol) or "nan")
            except ValueError:
                v = math.nan
            if kind_col and rec.get("volume_kind", "") != "usd_24h":
                v = math.nan
        ts.append(t)
        vals.append((p, v))
    if not ts:
        raise DataFormatError(f"{name}: no usable price rows ({read} read, {skipped} skipped)")
    t2, v2, dups, conflicts = _dedupe(ts, vals)
    vol = np.array([v for _, v in v2], dtype=np.float64) if vcol else None
    return PriceSeries(
        np.array(t2, dtype=np.int64),
        np.array([p for p, _ in v2], dtype=np.float64),
        name,
        read,
        skipped,
        dups,
        conflicts,
        vol,
    )


def write_price_csv(series: PriceSeries, file: str | Path) -> Path:
    """Write ``ts_iso,ts,price_usd[,volume_24h_usd]`` (readable by :func:`load_price_csv`)."""
    file = Path(file)
    with file.open("w", newline="") as fh:
        w = csv.writer(fh)
        has_vol = series.volume_usd is not None
        w.writerow(["ts_iso", "ts", "price_usd", *(["volume_24h_usd"] if has_vol else [])])
        for i, (t, p) in enumerate(zip(series.ts, series.price_usd, strict=True)):
            row = [iso(int(t)), int(t), repr(float(p))]
            if has_vol:
                v = float(series.volume_usd[i])  # type: ignore[index]
                row.append("" if math.isnan(v) else repr(v))
            w.writerow(row)
    return file


# ---------------------------------------------------------------------------------------------------
# Resampling onto a grid


@dataclass
class Resampled:
    """A series on a regular grid: the path, the forward-fill mask and the gap report of the input."""

    path: PricePath
    filled: np.ndarray  #: bool (n,): True where no observation fell in the grid cell
    gaps: GapReport

    @property
    def filled_fraction(self) -> float:
        """Share of grid points that were forward-filled (or left as gaps)."""
        return float(self.filled.mean()) if self.filled.size else 0.0


def resample_to_grid(
    series: PriceSeries,
    resolution: Resolution = "hour",
    *,
    t0: int | None = None,
    n_steps: int | None = None,
    max_ffill_seconds: int | None = None,
) -> Resampled:
    """As-of resampling onto a block (75 s) or hour grid.

    Grid point ``g_k = t0 + k·step`` takes the last observation at or before ``g_k`` (forward fill);
    ``filled[k]`` is True when no observation lies in ``(g_k − step, g_k]``. ``t0`` defaults to the
    first timestamp rounded *up* to the step, so every grid point has a prior observation. If
    ``max_ffill_seconds`` is set, a point whose last observation is older than that becomes a gap
    (price 0). The path's ``meta`` carries ``filled`` (the mask), the source and the gap summary.
    """
    step = STEP_SECONDS[resolution]
    ts = series.ts
    if t0 is None:
        t0 = int(-(-int(ts[0]) // step) * step)
    if n_steps is None:
        n_steps = max(1, (int(ts[-1]) - t0) // step + 1)
    grid = t0 + np.arange(n_steps, dtype=np.int64) * step
    idx = np.searchsorted(ts, grid, side="right") - 1  # last obs ≤ g
    valid = idx >= 0
    safe = np.where(valid, idx, 0)
    prices = np.where(valid, usd_to_micro(series.price_usd)[safe], 0)
    prev_idx = np.searchsorted(ts, grid - step, side="right") - 1  # last obs ≤ g − step
    filled = ~(valid & (idx > prev_idx))
    if max_ffill_seconds is not None:
        age = grid - np.where(valid, ts[safe], grid[0] - 10 * max_ffill_seconds - 1)
        prices = np.where(age > max_ffill_seconds, 0, prices)
    gaps = series.gaps()
    meta = {
        "source_file": series.source,
        "filled": filled,
        "filled_fraction": float(filled.mean()),
        "native_step_seconds": gaps.expected_step,
        "gaps": gaps.n_gaps,
        "longest_gap_hours": gaps.longest / 3600,
        "duplicates": series.duplicates,
        "rows_skipped": series.rows_skipped,
    }
    pp = PricePath(datetime.fromtimestamp(t0, UTC), resolution, prices[np.newaxis, :], "real", meta)
    return Resampled(pp, filled, gaps)


# ---------------------------------------------------------------------------------------------------
# spreads.csv


@dataclass
class SpreadsLog:
    """A ``spreads.py log`` CSV: per-tick µUSD per source (``0`` = missing) and the error column."""

    ts: np.ndarray  #: int64 unix seconds, ascending
    prices: np.ndarray  #: int64 (n, sources) µUSD, 0 = missing
    names: tuple[str, ...] = SPREADS_NAMES
    errors: list[str] = field(default_factory=list)
    source: str = ""
    rows_skipped: int = 0
    duplicates: int = 0

    def __len__(self) -> int:
        return len(self.ts)

    def gaps(self, expected_step: int = 300, factor: float = 3.0) -> GapReport:
        """Gaps as ``spreads.py analyze`` defines them (default 3 × the 300 s interval)."""
        return gap_report(self.ts, expected_step, factor)

    def missing(self) -> dict[str, int]:
        """Rows missing per source."""
        return {n: int((self.prices[:, i] <= 0).sum()) for i, n in enumerate(self.names)}

    def pair_spreads_bps(self) -> dict[tuple[str, str], np.ndarray]:
        """``|a − b| · 10⁴ / min(a, b)`` per pair over rows where both are present (MINT-10's form)."""
        out: dict[tuple[str, str], np.ndarray] = {}
        for (i, a), (j, b) in itertools.combinations(enumerate(self.names), 2):
            pa, pb = self.prices[:, i].astype(np.float64), self.prices[:, j].astype(np.float64)
            ok = (pa > 0) & (pb > 0)
            out[(a, b)] = np.abs(pa[ok] - pb[ok]) * 10_000 / np.minimum(pa[ok], pb[ok])
        return out


def load_spreads_csv(source: str | Path | TextIO) -> SpreadsLog:
    """Read a ``spreads.py log`` CSV with ``spreads.py read_log``'s rules.

    A row whose ``ts`` does not parse is skipped; a source cell that is empty, not an integer or
    ``≤ 0`` is missing; rows are sorted by ``ts``. Unlike ``read_log``, a repeated ``ts`` keeps the
    last row (counted in ``duplicates``) so the log is a function of time.
    """
    fh, name = _open(source)
    reader = csv.DictReader(fh)
    fields = _lower_fields(reader)
    need = [c for c in SPREADS_COLUMNS if c not in ("ts_iso", "errors")]
    if any(c not in fields for c in need):
        raise DataFormatError(
            f"{name}: not a spreads.py log; need columns {list(SPREADS_COLUMNS)}, got {fields}"
        )
    ts: list[int] = []
    rows: list[tuple[list[int], str]] = []
    skipped = 0
    for rec in reader:
        try:
            t = int(float(rec["ts"]))
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        ps = []
        for n in SPREADS_NAMES:
            v = (rec.get(f"{n}_micro_usd") or "").strip()
            try:
                p = int(v) if v else 0
            except ValueError:
                p = 0
            ps.append(p if p > 0 else 0)
        ts.append(t)
        rows.append((ps, rec.get("errors") or ""))
    t2, r2, dups, _ = _dedupe(ts, rows)
    prices = np.array([p for p, _ in r2], dtype=np.int64).reshape(len(t2), len(SPREADS_NAMES))
    return SpreadsLog(
        np.array(t2, dtype=np.int64), prices, SPREADS_NAMES, [e for _, e in r2], name, skipped, dups
    )


def write_spreads_csv(log: SpreadsLog, file: str | Path) -> Path:
    """Write the exact ``spreads.py`` column layout (round-trips through :func:`load_spreads_csv`)."""
    file = Path(file)
    with file.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(SPREADS_COLUMNS)
        for k, t in enumerate(log.ts):
            cells = ["" if p <= 0 else str(int(p)) for p in log.prices[k]]
            err = log.errors[k] if k < len(log.errors) else ""
            w.writerow([iso(int(t)), str(int(t)), *cells, err])
    return file


# ---------------------------------------------------------------------------------------------------
# Pool shares


@dataclass
class PoolShareLog:
    """Who mined each block: ``height`` → pool key (encoded as an index into ``keys``)."""

    heights: np.ndarray  #: int64 ascending
    pool: np.ndarray  #: int32 index into ``keys``
    keys: tuple[str, ...]
    source: str = ""
    duplicates: int = 0
    rows_skipped: int = 0

    def __len__(self) -> int:
        return len(self.heights)

    def missing_heights(self) -> int:
        """Heights absent between the first and last block."""
        return 0 if not len(self.heights) else int(self.heights[-1] - self.heights[0] + 1 - len(self.heights))

    def gaps(self) -> GapReport:
        """Gaps in the height sequence (``expected_step`` = 1 block)."""
        return gap_report(self.heights, 1, 1.5)

    def shares(self) -> dict[str, float]:
        """Block share per pool over the whole log, largest first."""
        counts = np.bincount(self.pool, minlength=len(self.keys))
        tot = counts.sum()
        pairs = sorted(
            ((self.keys[i], counts[i] / tot) for i in range(len(self.keys))), key=lambda kv: -kv[1]
        )
        return {k: float(v) for k, v in pairs}

    def rolling_shares(self, window: int) -> np.ndarray:
        """Shares per non-overlapping ``window`` of blocks, shape ``(n_windows, n_pools)``."""
        n = len(self.pool) // window
        if n == 0:
            return np.zeros((0, len(self.keys)))
        blocks = self.pool[: n * window].reshape(n, window)
        return np.stack([(blocks == i).mean(axis=1) for i in range(len(self.keys))], axis=1)


def load_pool_shares_csv(source: str | Path | TextIO) -> PoolShareLog:
    """Read ``height,payout_key`` (``pool``/``miner``/``key`` accepted for the key column)."""
    fh, name = _open(source)
    reader = csv.DictReader(fh)
    fields = _lower_fields(reader)
    kcol = next((c for c in ("payout_key", "pool", "miner", "key", "address") if c in fields), None)
    if "height" not in fields or kcol is None:
        raise DataFormatError(f"{name}: need columns height,payout_key; got {fields}")
    hs: list[int] = []
    ks: list[str] = []
    skipped = 0
    for raw in reader:
        rec = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        try:
            h = int(rec["height"])
        except (KeyError, ValueError):
            skipped += 1
            continue
        if not rec.get(kcol):
            skipped += 1
            continue
        hs.append(h)
        ks.append(rec[kcol])
    if not hs:
        raise DataFormatError(f"{name}: no usable rows")
    h2, k2, dups, _ = _dedupe(hs, ks)
    keys = tuple(sorted(set(k2)))
    index = {k: i for i, k in enumerate(keys)}
    return PoolShareLog(
        np.array(h2, dtype=np.int64),
        np.array([index[k] for k in k2], dtype=np.int32),
        keys,
        name,
        dups,
        skipped,
    )


# ---------------------------------------------------------------------------------------------------
# Depth


@dataclass
class DepthSeries:
    """Liquidity per snapshot: USD depth within ±2 % of mid and 24 h volume (NaN where unknown)."""

    ts: np.ndarray
    depth_2pct_usd: np.ndarray
    volume_24h_usd: np.ndarray
    bid_depth_2pct_usd: np.ndarray
    source: str = ""
    form: str = "summary"  #: "summary" | "book"
    rows_skipped: int = 0
    duplicates: int = 0

    def __len__(self) -> int:
        return len(self.ts)

    def gaps(self, factor: float = 1.5) -> GapReport:
        """Gap report over the snapshot times."""
        return gap_report(self.ts, None, factor)


def summarise_book(levels: Iterable[tuple[str, float, float]], band: float = 0.02) -> tuple[float, float]:
    """``(depth_usd, bid_depth_usd)`` within ``±band`` of the mid for one snapshot.

    ``levels`` are ``(side, price_usd, size_yec)``; mid = (best bid + best ask)/2. Depth sums
    ``price · size`` of bids priced ≥ mid·(1−band) and asks ≤ mid·(1+band). NaN without both sides.
    """
    bids = [(p, s) for side, p, s in levels if side == "bid"]
    asks = [(p, s) for side, p, s in levels if side == "ask"]
    if not bids or not asks:
        return math.nan, math.nan
    mid = (max(p for p, _ in bids) + min(p for p, _ in asks)) / 2
    bid_depth = sum(p * s for p, s in bids if p >= mid * (1 - band))
    ask_depth = sum(p * s for p, s in asks if p <= mid * (1 + band))
    return bid_depth + ask_depth, bid_depth


def load_depth_csv(source: str | Path | TextIO) -> DepthSeries:
    """Read a depth CSV in either form (auto-detected from the header)."""
    fh, name = _open(source)
    reader = csv.DictReader(fh)
    fields = _lower_fields(reader)
    recs = [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()} for r in reader]
    skipped = 0

    def fnum(s: str | None) -> float:
        try:
            return float(s) if s else math.nan
        except ValueError:
            return math.nan

    if "ts" in fields and "depth_2pct_usd" in fields:
        ts: list[int] = []
        vals: list[tuple[float, float, float]] = []
        for r in recs:
            try:
                t = parse_ts(r["ts"])
            except ValueError:
                skipped += 1
                continue
            ts.append(t)
            vals.append(
                (
                    fnum(r.get("depth_2pct_usd")),
                    fnum(r.get("volume_24h_usd")),
                    fnum(r.get("bid_depth_2pct_usd")),
                )
            )
        form = "summary"
    elif {"ts", "side", "price_usd", "size_yec"} <= set(fields):
        # With a ``venue`` column, each venue's book is summarised around its own mid and the
        # depths are summed: pooling books across venues would take the mid from the best bid of
        # one venue and the best ask of another (a crossed or skewed "mid" when venues disagree).
        books: dict[int, dict[str, list[tuple[str, float, float]]]] = {}
        for r in recs:
            try:
                t = parse_ts(r["ts"])
                side = r["side"].lower()
                p, s = float(r["price_usd"]), float(r["size_yec"])
            except (KeyError, ValueError):
                skipped += 1
                continue
            if side not in ("bid", "ask") or not (p > 0 and s >= 0):
                skipped += 1
                continue
            books.setdefault(t, {}).setdefault(r.get("venue", ""), []).append((side, p, s))
        ts = sorted(books)
        vals = []
        for t in ts:
            parts = [summarise_book(lv) for lv in books[t].values()]
            parts = [pb for pb in parts if not math.isnan(pb[0])]
            if parts:
                vals.append((sum(d for d, _ in parts), math.nan, sum(b for _, b in parts)))
            else:
                vals.append((math.nan, math.nan, math.nan))
        form = "book"
    else:
        raise DataFormatError(
            f"{name}: need ts,depth_2pct_usd,volume_24h_usd or ts,side,price_usd,size_yec; got {fields}"
        )
    if not ts:
        raise DataFormatError(f"{name}: no usable rows")
    t2, v2, dups, _ = _dedupe(ts, vals)
    arr = np.array(v2, dtype=np.float64).reshape(len(t2), 3)
    return DepthSeries(
        np.array(t2, dtype=np.int64), arr[:, 0], arr[:, 1], arr[:, 2], name, form, skipped, dups
    )


# ---------------------------------------------------------------------------------------------------
# Dispatch


KINDS: tuple[str, ...] = ("price", "spreads", "hashrate", "depth")


def import_file(
    path: str | Path, kind: str = "price"
) -> PriceSeries | SpreadsLog | PoolShareLog | DepthSeries:
    """Load ``path`` as ``kind`` (``hashrate`` = the pool-share CSV)."""
    loaders = {
        "price": load_price_csv,
        "spreads": load_spreads_csv,
        "hashrate": load_pool_shares_csv,
        "depth": load_depth_csv,
    }
    if kind not in loaders:
        raise ValueError(f"unknown kind {kind!r}; choose from {KINDS}")
    return loaders[kind](path)  # type: ignore[operator]

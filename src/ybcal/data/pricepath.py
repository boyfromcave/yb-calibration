"""Helpers around the frozen :class:`ybcal.types.PricePath` (PLAN §3.1).

``PricePath`` itself is a WP-0 contract and lives in :mod:`ybcal.types`; it is re-exported here so
data-layer callers can write ``from ybcal.data.pricepath import PricePath``. This module adds:

* unit conversion between float USD and integer µUSD, with the ``PRICE_MIN``/``PRICE_MAX`` clamp of
  ``params.h`` (a ``0`` stays ``0``: it means "no price", a feed gap);
* timestamps, slicing and path selection;
* resampling between block (75 s) and hour (48-block) grids;
* log returns (gaps become ``NaN``);
* persistence: a CSV form (single path ``ts_iso,ts,price_usd``, which :mod:`ybcal.data.loaders`
  reads back; multi-path ``ts_iso,ts,path_0,…`` in µUSD) with a JSON sidecar for the metadata, and a
  compact ``.npz`` form for large synthetic ensembles.

Owner: WP-2.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import numpy as np

from ybcal.types import PathProvenance, PricePath, Resolution
from ybcal.units import (
    BLOCK_SECONDS,
    BLOCKS_PER_HOUR,
    BLOCKS_PER_YEAR,
    MICRO_USD_PER_USD,
    PRICE_MAX,
    PRICE_MIN,
)

OWNER_WP = "WP-2"

__all__ = [
    "DT_BLOCK",
    "DT_HOUR",
    "STEP_SECONDS",
    "PricePath",
    "clamp_prices",
    "from_csv",
    "from_npz",
    "from_usd",
    "load",
    "log_returns",
    "make_path",
    "micro_to_usd",
    "resample",
    "resolution_for_dt",
    "save",
    "select_paths",
    "slice_steps",
    "steps_per_year",
    "timestamps",
    "to_csv",
    "to_npz",
    "usd_to_micro",
    "with_meta",
]

#: Seconds per step of each resolution.
STEP_SECONDS: dict[str, int] = {"block": BLOCK_SECONDS, "hour": BLOCK_SECONDS * BLOCKS_PER_HOUR}
#: Year fraction of one step (the node's year is ``BLOCKS_PER_YEAR`` = 420,480 blocks = 365 days).
DT_BLOCK: float = 1.0 / BLOCKS_PER_YEAR
DT_HOUR: float = BLOCKS_PER_HOUR / BLOCKS_PER_YEAR


def steps_per_year(resolution: Resolution) -> int:
    """Steps in one node year: 420,480 blocks or 8,760 hours."""
    return BLOCKS_PER_YEAR if resolution == "block" else BLOCKS_PER_YEAR // BLOCKS_PER_HOUR


def resolution_for_dt(dt: float) -> Resolution:
    """Map a step length in years to a resolution; raises ``ValueError`` for any other ``dt``."""
    for res, ref in (("block", DT_BLOCK), ("hour", DT_HOUR)):
        if abs(dt - ref) <= 1e-9 * ref:
            return res  # type: ignore[return-value]
    raise ValueError(f"dt={dt!r} years is neither one block ({DT_BLOCK!r}) nor one hour ({DT_HOUR!r})")


def _utc(t0: datetime) -> datetime:
    return t0.replace(tzinfo=UTC) if t0.tzinfo is None else t0.astimezone(UTC)


# ---------------------------------------------------------------------------------------------------
# Units


def clamp_prices(prices: np.ndarray, *, keep_gaps: bool = True) -> np.ndarray:
    """Clamp µUSD prices to ``[PRICE_MIN, PRICE_MAX]`` as int64; ``0`` (gap) is kept when ``keep_gaps``."""
    arr = np.asarray(prices)
    if not np.issubdtype(arr.dtype, np.integer):
        raise TypeError("clamp_prices takes integer µUSD; use usd_to_micro for floats")
    out = np.clip(arr.astype(np.int64), PRICE_MIN, PRICE_MAX)
    if keep_gaps:
        out = np.where(arr == 0, 0, out)
    return out


def usd_to_micro(usd: np.ndarray | Sequence[float] | float, *, clamp: bool = True) -> np.ndarray:
    """Float USD → int64 µUSD (round half to even). Non-finite or non-positive values become 0 (gap)."""
    arr = np.asarray(usd, dtype=np.float64)
    ok = np.isfinite(arr) & (arr > 0)
    micro = np.where(ok, np.rint(np.where(ok, arr, 0.0) * MICRO_USD_PER_USD), 0.0)
    micro = np.minimum(micro, float(np.iinfo(np.int64).max // 2)).astype(np.int64)
    if clamp:
        micro = np.where(ok, np.clip(micro, PRICE_MIN, PRICE_MAX), 0)
    return micro


def micro_to_usd(micro: np.ndarray) -> np.ndarray:
    """int64 µUSD → float USD (``0`` → ``NaN``); for statistics and display only."""
    arr = np.asarray(micro, dtype=np.float64)
    return np.where(arr > 0, arr / MICRO_USD_PER_USD, np.nan)


def make_path(
    t0: datetime,
    resolution: Resolution,
    prices: np.ndarray,
    provenance: PathProvenance,
    meta: dict[str, Any] | None = None,
    *,
    clamp: bool = True,
) -> PricePath:
    """Build a :class:`PricePath` from integer µUSD, clamping to the ``params.h`` bounds."""
    arr = np.asarray(prices)
    if arr.ndim == 1:
        arr = arr[np.newaxis, :]
    if clamp:
        arr = clamp_prices(arr)
    return PricePath(_utc(t0), resolution, arr, provenance, dict(meta or {}))


def from_usd(
    t0: datetime,
    resolution: Resolution,
    prices_usd: np.ndarray,
    provenance: PathProvenance,
    meta: dict[str, Any] | None = None,
) -> PricePath:
    """Build a :class:`PricePath` from float USD (rounded and clamped to µUSD)."""
    return make_path(t0, resolution, usd_to_micro(prices_usd), provenance, meta, clamp=False)


# ---------------------------------------------------------------------------------------------------
# Time, slicing


def timestamps(pp: PricePath) -> np.ndarray:
    """Unix seconds (int64) of every step: ``t0 + k · step``."""
    t0 = int(_utc(pp.t0).timestamp())
    return t0 + np.arange(pp.n_steps, dtype=np.int64) * STEP_SECONDS[pp.resolution]


def _slice_meta(meta: dict[str, Any], n: int, sl: slice | None, rows: np.ndarray | None) -> dict[str, Any]:
    """Slice per-step arrays held in ``meta`` (e.g. the ``filled`` mask) consistently."""
    out: dict[str, Any] = {}
    for k, v in meta.items():
        if isinstance(v, np.ndarray) and v.ndim >= 1 and v.shape[-1] == n:
            w = v
            if rows is not None and w.ndim == 2:
                w = w[rows]
            if sl is not None:
                w = w[..., sl]
            out[k] = w
        else:
            out[k] = v
    return out


def slice_steps(pp: PricePath, start: int, stop: int | None = None) -> PricePath:
    """Steps ``[start, stop)`` of every path; ``t0`` moves with ``start``."""
    n = pp.n_steps
    sl = slice(start, stop)
    s0, _, _ = sl.indices(n)
    t0 = _utc(pp.t0) + timedelta(seconds=s0 * STEP_SECONDS[pp.resolution])
    return PricePath(
        t0, pp.resolution, pp.prices[:, sl].copy(), pp.provenance, _slice_meta(pp.meta, n, sl, None)
    )


def select_paths(pp: PricePath, idx: int | Sequence[int] | np.ndarray) -> PricePath:
    """A subset of the paths (keeps 2-D shape)."""
    rows = np.atleast_1d(np.asarray(idx))
    return PricePath(
        pp.t0,
        pp.resolution,
        pp.prices[rows].copy(),
        pp.provenance,
        _slice_meta(pp.meta, pp.n_steps, None, rows),
    )


# ---------------------------------------------------------------------------------------------------
# Resampling


def resample(pp: PricePath, to: Resolution, *, method: Literal["hold", "loglinear"] = "hold") -> PricePath:
    """Resample between block and hour grids.

    * hour → block: hour ``i`` covers blocks ``[48 i, 48 i + 48)``. ``hold`` repeats the hourly price
      (a step function, the way a quote is held until the next one); ``loglinear`` interpolates
      ``log p`` between consecutive hours (the last hour is held). A gap (0) stays a gap.
    * block → hour: point sample at blocks ``0, 48, 96, …`` (the price at the start of each hour), so
      ``resample(resample(p, "block"), "hour") == p`` exactly.

    Per-step boolean masks in ``meta`` (e.g. ``filled``) are resampled the same way.
    """
    if to == pp.resolution:
        return pp
    k = BLOCKS_PER_HOUR
    meta = dict(pp.meta)
    if to == "block":
        if method == "hold":
            prices = np.repeat(pp.prices, k, axis=1)
        else:
            p = pp.prices.astype(np.float64)
            gap = p <= 0
            lp = np.log(np.where(gap, 1.0, p))
            nxt = np.concatenate([lp[:, 1:], lp[:, -1:]], axis=1)
            nxt_gap = np.concatenate([gap[:, 1:], gap[:, -1:]], axis=1)
            nxt = np.where(nxt_gap, lp, nxt)
            frac = np.arange(k, dtype=np.float64) / k
            out = lp[:, :, None] + (nxt - lp)[:, :, None] * frac[None, None, :]
            prices = np.rint(np.exp(out)).astype(np.int64).reshape(pp.n_paths, -1)
            prices = np.where(np.repeat(gap, k, axis=1), 0, clamp_prices(prices))
        for key, v in pp.meta.items():
            if isinstance(v, np.ndarray) and v.ndim >= 1 and v.shape[-1] == pp.n_steps:
                meta[key] = np.repeat(v, k, axis=-1)
        f = meta.get("filled")
        if isinstance(f, np.ndarray) and f.dtype == bool and f.shape[-1] == prices.shape[-1]:
            # only the first block of an hour carries that hour's observation; the other 47 are
            # held copies. Marking them filled keeps fits observed-to-observed (D-RD-INF-1): else
            # 47 exact-zero returns per hour read as a stale feed and drop_stale_runs empties it.
            f = f.copy()
            f[..., np.arange(f.shape[-1]) % k != 0] = True
            meta["filled"] = f
    else:
        prices = pp.prices[:, ::k].copy()
        for key, v in pp.meta.items():
            if isinstance(v, np.ndarray) and v.ndim >= 1 and v.shape[-1] == pp.n_steps:
                meta[key] = v[..., ::k].copy()
    meta["resampled_from"] = pp.resolution
    return PricePath(pp.t0, to, prices, pp.provenance, meta)


# ---------------------------------------------------------------------------------------------------
# Returns


def log_returns(pp: PricePath | np.ndarray) -> np.ndarray:
    """Log returns ``log(p[t+1] / p[t])``, shape ``(paths, n-1)``; any step touching a gap is ``NaN``."""
    p = (pp.prices if isinstance(pp, PricePath) else np.atleast_2d(np.asarray(pp))).astype(np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        lp = np.where(p > 0, np.log(np.where(p > 0, p, 1.0)), np.nan)
    return np.diff(lp, axis=1)


# ---------------------------------------------------------------------------------------------------
# Persistence


def _meta_json(pp: PricePath) -> dict[str, Any]:
    """JSON-safe header: arrays are dropped (persisted separately where needed)."""
    meta = {k: v for k, v in pp.meta.items() if not isinstance(v, np.ndarray)}
    return {
        "t0": _utc(pp.t0).isoformat(),
        "resolution": pp.resolution,
        "provenance": pp.provenance,
        "n_paths": pp.n_paths,
        "n_steps": pp.n_steps,
        "meta": json.loads(json.dumps(meta, default=str)),
    }


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_csv(pp: PricePath, file: str | Path, *, sidecar: bool = True) -> Path:
    """Write a CSV (plus ``<file>.meta.json`` when ``sidecar``).

    One path → ``ts_iso,ts,price_usd`` (the shape ``pinrate.py`` and the price loader read; a gap is
    an empty cell). Several paths → ``ts_iso,ts,path_0,path_1,…`` in integer µUSD (gap = 0).
    """
    file = Path(file)
    ts = timestamps(pp)
    with file.open("w", newline="") as fh:
        w = csv.writer(fh)
        if pp.n_paths == 1:
            w.writerow(["ts_iso", "ts", "price_usd"])
            for t, p in zip(ts, pp.prices[0], strict=True):
                w.writerow([_iso(t), int(t), "" if p <= 0 else f"{int(p) / MICRO_USD_PER_USD:.6f}"])
        else:
            w.writerow(["ts_iso", "ts", *(f"path_{i}" for i in range(pp.n_paths))])
            for j, t in enumerate(ts):
                w.writerow([_iso(t), int(t), *(int(x) for x in pp.prices[:, j])])
    if sidecar:
        Path(str(file) + ".meta.json").write_text(json.dumps(_meta_json(pp), indent=2, sort_keys=True) + "\n")
    return file


def from_csv(file: str | Path) -> PricePath:
    """Read a CSV written by :func:`to_csv` (the sidecar, when present, restores the metadata).

    A single-path CSV without a sidecar is a regular grid only if its timestamps are evenly spaced
    at 75 s or 3600 s; use :func:`ybcal.data.loaders.load_price_csv` for arbitrary price CSVs.
    """
    file = Path(file)
    side = Path(str(file) + ".meta.json")
    header: dict[str, Any] = json.loads(side.read_text()) if side.exists() else {}
    with file.open(newline="") as fh:
        rows = list(csv.reader(fh))
    cols, body = rows[0], rows[1:]
    ti = cols.index("ts")
    ts = np.array([int(float(r[ti])) for r in body], dtype=np.int64)
    if "price_usd" in cols:
        i = cols.index("price_usd")
        prices = usd_to_micro([float(r[i]) if r[i].strip() else np.nan for r in body])[np.newaxis, :]
    else:
        pcols = [j for j, c in enumerate(cols) if c.startswith("path_")]
        if not pcols:
            raise ValueError(f"{file}: no price_usd or path_<i> columns")
        prices = np.array([[int(r[j]) for j in pcols] for r in body], dtype=np.int64).T
    if header:
        res = header["resolution"]
        prov = header["provenance"]
        meta = dict(header.get("meta", {}))
        t0 = datetime.fromisoformat(header["t0"])
    else:
        step = int(np.median(np.diff(ts))) if len(ts) > 1 else STEP_SECONDS["hour"]
        res = next((r for r, s in STEP_SECONDS.items() if s == step), None)
        if res is None:
            raise ValueError(f"{file}: step {step}s is not a block or hour grid; use loaders.load_price_csv")
        prov, meta = "real", {"source_file": str(file)}
        t0 = datetime.fromtimestamp(int(ts[0]), UTC)
    return PricePath(t0, res, prices, prov, meta)


def to_npz(pp: PricePath, file: str | Path) -> Path:
    """Compressed ``.npz``: ``prices`` plus a JSON header and any per-step mask arrays in ``meta``."""
    file = Path(file)
    arrays = {f"meta__{k}": v for k, v in pp.meta.items() if isinstance(v, np.ndarray)}
    np.savez_compressed(file, prices=pp.prices, header=np.array(json.dumps(_meta_json(pp))), **arrays)
    return file


def from_npz(file: str | Path) -> PricePath:
    """Inverse of :func:`to_npz`."""
    with np.load(file, allow_pickle=False) as z:
        header = json.loads(str(z["header"]))
        meta = dict(header.get("meta", {}))
        for k in z.files:
            if k.startswith("meta__"):
                meta[k.removeprefix("meta__")] = z[k]
        return PricePath(
            datetime.fromisoformat(header["t0"]),
            header["resolution"],
            z["prices"],
            header["provenance"],
            meta,
        )


def save(pp: PricePath, file: str | Path) -> Path:
    """Save by extension: ``.npz`` → :func:`to_npz`, anything else → :func:`to_csv`."""
    return to_npz(pp, file) if str(file).endswith(".npz") else to_csv(pp, file)


def load(file: str | Path) -> PricePath:
    """Load by extension: ``.npz`` → :func:`from_npz`; ``.csv`` → :func:`from_csv` (written by us) or,
    failing that, the general price loader resampled to an hour grid."""
    if str(file).endswith(".npz"):
        return from_npz(file)
    try:
        return from_csv(file)
    except (ValueError, IndexError, KeyError):
        from ybcal.data.loaders import load_price_csv, resample_to_grid

        return resample_to_grid(load_price_csv(file), "hour").path


def with_meta(pp: PricePath, **meta: Any) -> PricePath:
    """A shallow copy with extra metadata."""
    return replace(pp, meta={**pp.meta, **meta})

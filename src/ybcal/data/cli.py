"""``ybcal data fetch | import | synth | describe`` (dispatch convention: docs/architecture.md).

``cli.py`` (WP-0) declares the PLAN §3.2 arguments; the ``configure_<name>`` hooks below add the
WP-2 extras. Exit codes: 0 ok, 1 bad input, 3 network blocked (fetch).

Owner: WP-2.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np

from ybcal import __version__
from ybcal.data import describe as describe_mod
from ybcal.data import fetch as fetch_mod
from ybcal.data import loaders, pricepath, synthetic, venues

OWNER_WP = "WP-2"

EXIT_BAD_INPUT = 1
EXIT_NETWORK_BLOCKED = 3


# ---------------------------------------------------------------------------------------------------
# fetch


def configure_fetch(p: argparse.ArgumentParser) -> None:
    """Extra ``data fetch`` arguments."""
    p.add_argument("--coin", default="ycash", help="CoinGecko coin id (default ycash)")
    p.add_argument(
        "--granularity",
        choices=("auto", "hourly", "daily"),
        default="auto",
        help="coingecko: auto (hourly <= 90 days, else daily), hourly (chunked range calls), daily",
    )
    p.add_argument(
        "--api-key", default=None, help="CoinGecko demo API key (default $YBCAL_COINGECKO_API_KEY; optional)"
    )
    p.add_argument(
        "--exchange",
        action="append",
        default=[],
        help="tickers: CoinGecko exchange id (repeatable; default safe_trade)",
    )
    p.add_argument("--symbol", default="YEC_USDT", help="nonkyc market symbol (default YEC_USDT)")
    p.add_argument("--retries", type=int, default=4, help="HTTP retries with exponential backoff")
    p.add_argument(
        "--start",
        default=None,
        help="coinmarketcap/coincodex/*-candles: first time (ISO or unix; default: fork)",
    )
    p.add_argument("--end", default=None, help="last time (ISO or unix; default now)")
    p.add_argument("--blocks", type=int, default=40_000, help="inzyght: most recent blocks to read")


def cli_fetch(args: argparse.Namespace) -> int:
    """Download price data and write ``--out`` plus ``<out>.provenance.json``."""
    api_key = getattr(args, "api_key", None) or os.environ.get("YBCAL_COINGECKO_API_KEY")
    client = fetch_mod.HttpClient(
        retries=getattr(args, "retries", 4), max_body=fetch_mod.HISTORY_MAX_BODY_BYTES
    )
    out = Path(args.out)
    if out.parent and not out.parent.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
    if args.source in venues.EXTRA_SOURCES:
        return _fetch_extra(args, client, out)
    try:
        res = fetch_mod.fetch_to_csv(
            args.source,
            out,
            days=args.days,
            coin=getattr(args, "coin", "ycash"),
            granularity=getattr(args, "granularity", "auto"),
            api_key=api_key,
            exchanges=getattr(args, "exchange", None) or ["safe_trade"],
            symbol=getattr(args, "symbol", "YEC_USDT"),
            client=client,
        )
    except fetch_mod.NetworkBlockedError as e:
        print(f"ybcal data fetch: {e}", file=sys.stderr)
        return EXIT_NETWORK_BLOCKED
    except fetch_mod.FetchError as e:
        print(f"ybcal data fetch: {e}", file=sys.stderr)
        return EXIT_BAD_INPUT
    if res.series is not None:
        g = res.series.gaps()
        print(f"wrote {out} ({len(res.series)} rows) and {out}.provenance.json")
        print(g.summary())
    else:
        print(f"appended {len(res.rows)} row(s) to {out}; provenance in {out}.provenance.json")
    for n in res.notes:
        print(f"note: {n}")
    return 0


def _fetch_extra(args: argparse.Namespace, client: fetch_mod.HttpClient, out: Path) -> int:
    start = loaders.parse_ts(args.start) if getattr(args, "start", None) else None
    end = loaders.parse_ts(args.end) if getattr(args, "end", None) else None
    gran = getattr(args, "granularity", "auto")
    sym = getattr(args, "symbol", None)
    if args.source in ("coinmarketcap", "coincodex") and sym == "YEC_USDT":
        sym = None  # the nonkyc default, not meant for these sources
    if args.source == "inzyght":
        client.min_interval = max(client.min_interval, 1.0)
    try:
        res = venues.fetch_extra_to_csv(
            args.source,
            out,
            coin=getattr(args, "coin", "ycash"),
            granularity="daily" if gran == "daily" else "hourly",
            start=start,
            end=end,
            symbol=sym,
            blocks=getattr(args, "blocks", 40_000),
            client=client,
        )
    except fetch_mod.NetworkBlockedError as e:
        print(f"ybcal data fetch: {e}", file=sys.stderr)
        return EXIT_NETWORK_BLOCKED
    except fetch_mod.FetchError as e:
        print(f"ybcal data fetch: {e}", file=sys.stderr)
        return EXIT_BAD_INPUT
    if res.candles is not None:
        c = res.candles
        traded = int((~(c.volume <= 0)).sum())
        print(f"wrote {out} ({len(c)} candles, {traded} with trades) and {out}.provenance.json")
        print(loaders.gap_report(c.ts, c.interval).summary())
    elif res.series is not None:
        print(f"wrote {out} ({len(res.series)} rows) and {out}.provenance.json")
        print(res.series.gaps().summary())
    else:
        print(f"wrote {len(res.rows)} row(s) to {out}; provenance in {out}.provenance.json")
    for n in res.notes:
        print(f"note: {n}")
    return 0


# ---------------------------------------------------------------------------------------------------
# splice / spreads / volume


def cli_splice(args: argparse.Namespace) -> int:
    """Join ``secondary`` (before) and ``primary`` (from its first timestamp); print the overlap."""
    try:
        a = loaders.load_price_csv(args.primary)
        b = loaders.load_price_csv(args.secondary)
    except (loaders.DataFormatError, OSError) as e:
        print(f"ybcal data splice: {e}", file=sys.stderr)
        return EXIT_BAD_INPUT
    joined, info = venues.splice(a, b)
    print(venues.compare(a, b).summary())
    print(
        f"splice at {info['cut_iso']}: {info['secondary_points']} secondary + {info['primary_points']} "
        f"primary points; splice-step return {info['splice_return_bps']:.0f} bps"
    )
    if args.out:
        loaders.write_price_csv(joined, args.out)
        side = Path(str(args.out) + ".splice.json")
        doc = {"primary": args.primary, "secondary": args.secondary, **info}
        side.write_text(json.dumps(doc, indent=2, default=float) + "\n")
        prov = _splice_provenance(args.primary, args.secondary, args.out, info, len(joined))
        print(f"wrote {args.out} ({len(joined)} rows), {side} and {prov}")
    return 0


def _read_provenance(path: str) -> dict:
    side = Path(str(path) + ".provenance.json")
    try:
        return json.loads(side.read_text()) if side.exists() else {}
    except ValueError:
        return {}


def _derived_provenance(out: str, source: str, inputs: dict, notes: list[str], **extra: object) -> Path:
    """``<out>.provenance.json`` for a file derived from other data files (their hashes and URLs)."""
    from ybcal.config import sha256_file

    parts, urls, fetched = [], [], []
    for role, f in inputs.items():
        if not f:
            continue
        pv = _read_provenance(f)
        urls += list(pv.get("urls") or [])
        if pv.get("fetched_at"):
            fetched.append(pv["fetched_at"])
        parts.append(
            {"role": role, "file": Path(f).name, "sha256": sha256_file(f), "source": pv.get("source")}
        )
    doc = {
        "file": Path(out).name,
        "source": source,
        "urls": urls,
        "fetched_at": max(fetched) if fetched else None,
        "sha256": sha256_file(out),
        "provenance": "real",
        "tool": f"ybcal {__version__}",
        "inputs": parts,
        "notes": notes,
        **extra,
    }
    side = Path(str(out) + ".provenance.json")
    side.write_text(json.dumps(doc, indent=2, sort_keys=True, default=float) + "\n")
    return side


def _splice_provenance(primary: str, secondary: str, out: str, info: dict, rows: int) -> Path:
    """``<out>.provenance.json`` for a spliced file: both inputs' sources, URLs and hashes."""
    from ybcal.config import sha256_file

    parts = []
    urls: list[str] = []
    for role, f in (("primary", primary), ("secondary", secondary)):
        pv = _read_provenance(f)
        urls += list(pv.get("urls") or [])
        parts.append(
            {
                "role": role,
                "file": Path(f).name,
                "sha256": sha256_file(f),
                "source": pv.get("source"),
                "fetched_at": pv.get("fetched_at"),
            }
        )
    fetched = [p["fetched_at"] for p in parts if p["fetched_at"]]
    doc = {
        "file": Path(out).name,
        "source": "splice(" + " < ".join(str(p["source"]) for p in reversed(parts)) + ")",
        "urls": urls,
        "fetched_at": max(fetched) if fetched else None,
        "sha256": sha256_file(out),
        "provenance": "real",
        "tool": f"ybcal {__version__}",
        "rows": rows,
        "inputs": parts,
        "splice": {k: v for k, v in info.items() if k != "overlap"},
        "overlap": info.get("overlap"),
        "notes": [
            f"secondary strictly before {info['cut_iso']}, primary from then on; raw prices (no rescale)"
        ],
    }
    side = Path(str(out) + ".provenance.json")
    side.write_text(json.dumps(doc, indent=2, sort_keys=True, default=float) + "\n")
    return side


def configure_spreads(p: argparse.ArgumentParser) -> None:
    """Extra ``data spreads`` arguments."""
    p.add_argument(
        "--safetrade", default=None, help="SafeTrade candle CSV (data fetch --source safetrade-candles)"
    )
    p.add_argument("--nonkyc", default=None, help="nonkyc candle CSV (data fetch --source nonkyc-candles)")
    p.add_argument("--start", default=None, help="first time (ISO or unix)")
    p.add_argument(
        "--max-age-hours",
        type=float,
        default=None,
        help="a venue's last trade older than this is a missing cell",
    )


def cli_spreads(args: argparse.Namespace) -> int:
    """Reconstruct a ``spreads.py log`` CSV from hourly candles (see venues.reconstruct_spreads)."""
    try:
        agg = loaders.load_price_csv(args.aggregate)
        vs = {}
        for name in ("safetrade", "nonkyc"):
            f = getattr(args, name, None)
            if f:
                vs[name] = venues.read_candles_csv(f)
    except (loaders.DataFormatError, OSError, KeyError, ValueError) as e:
        print(f"ybcal data spreads: {e}", file=sys.stderr)
        return EXIT_BAD_INPUT
    start = loaders.parse_ts(args.start) if getattr(args, "start", None) else None
    mah = getattr(args, "max_age_hours", None)
    rows = venues.reconstruct_spreads(agg, vs, start=start, max_age=int(mah * 3600) if mah else None)
    venues.write_spreads_rows(rows, args.out)
    _derived_provenance(
        args.out,
        "reconstructed:spreads",
        {"aggregate": args.aggregate, **{n: getattr(args, n, None) for n in ("safetrade", "nonkyc")}},
        [
            "RECONSTRUCTED from hourly data, not logged live: coingecko = the aggregate's hourly point; "
            "safetrade/nonkyc = close of the last hourly candle with volume > 0 at or before the "
            "point (the venue's last trade as of then, as converted_last / lastPriceNumber read it)",
            f"max_age_hours = {mah}" if mah else "no max_age (spreads.py log semantics)",
        ],
        rows=len(rows),
    )
    log = loaders.load_spreads_csv(args.out)
    print(f"wrote {args.out}: {len(log)} rows; missing per source {log.missing()}")
    for (a, b), xs in log.pair_spreads_bps().items():
        if len(xs):
            print(
                f"  {a}/{b}: n={len(xs)} p50={np.percentile(xs, 50):.0f} p95={np.percentile(xs, 95):.0f} "
                f"p99={np.percentile(xs, 99):.0f} max={xs.max():.0f} bps"
            )
    return 0


def configure_volume(p: argparse.ArgumentParser) -> None:
    """Extra ``data volume`` arguments."""
    p.add_argument("--days", type=int, default=365, help="window: the last N days of the file")


def cli_volume(args: argparse.Namespace) -> int:
    """Percentiles of daily 24 h USD volume (one value per UTC day: the day's last reading) —
    the input ``yec_daily_volume_p10_usd`` wants."""
    try:
        s = loaders.load_price_csv(args.file)
    except (loaders.DataFormatError, OSError) as e:
        print(f"ybcal data volume: {e}", file=sys.stderr)
        return EXIT_BAD_INPUT
    if s.volume_usd is None or not np.isfinite(s.volume_usd).any():
        print(f"ybcal data volume: {args.file} has no volume_24h_usd column", file=sys.stderr)
        return EXIT_BAD_INPUT
    d = volume_stats(s, getattr(args, "days", 365))
    print(json.dumps(d, indent=2))
    return 0


def volume_stats(s: loaders.PriceSeries, days: int = 365) -> dict[str, float]:
    """p10/p25/p50/mean of daily 24 h USD volume over the last ``days`` days (the last reading of
    each UTC day; 24 h volumes from hourly points overlap, so one per day avoids overweighting)."""
    assert s.volume_usd is not None
    ok = np.isfinite(s.volume_usd) & (s.volume_usd > 0)
    ts, vol = s.ts[ok], s.volume_usd[ok]
    ts_end = int(ts[-1])
    sel = ts > ts_end - days * 86400
    ts, vol = ts[sel], vol[sel]
    day = ts // 86400
    keep = np.r_[day[1:] != day[:-1], True]
    v = vol[keep]
    return {
        "days": len(v),
        "first": loaders.iso(int(ts[0])),
        "last": loaders.iso(int(ts[-1])),
        "p10_usd": float(np.percentile(v, 10)),
        "p25_usd": float(np.percentile(v, 25)),
        "p50_usd": float(np.percentile(v, 50)),
        "mean_usd": float(v.mean()),
        "min_usd": float(v.min()),
    }


# ---------------------------------------------------------------------------------------------------
# import


def configure_import(p: argparse.ArgumentParser) -> None:
    """Extra ``data import`` arguments."""
    p.add_argument(
        "--resolution",
        choices=("hour", "block"),
        default="hour",
        help="price: grid to resample onto when writing --out (default hour)",
    )
    p.add_argument(
        "--max-ffill-hours",
        type=float,
        default=None,
        help="price: a grid point older than this since its last observation becomes a gap (0)",
    )
    p.add_argument("--interval", type=int, default=300, help="spreads: the logging interval in seconds")


def cli_import(args: argparse.Namespace) -> int:
    """Validate a data file, print its summary and gap report, optionally write a normalised copy."""
    try:
        obj = loaders.import_file(args.file, args.kind)
    except (loaders.DataFormatError, OSError) as e:
        print(f"ybcal data import: {e}", file=sys.stderr)
        return EXIT_BAD_INPUT
    out = Path(args.out) if args.out else None
    if isinstance(obj, loaders.PriceSeries):
        print(
            f"price: {len(obj)} rows ({obj.rows_skipped} skipped, {obj.duplicates} duplicate timestamps, "
            f"{obj.conflicting_duplicates} conflicting)"
        )
        print(obj.gaps().summary())
        mf = getattr(args, "max_ffill_hours", None)
        rs = loaders.resample_to_grid(
            obj,
            getattr(args, "resolution", "hour"),
            max_ffill_seconds=int(mf * 3600) if mf is not None else None,
        )
        print(f"{rs.path.resolution} grid: {rs.path.n_steps} steps, forward-filled {rs.filled_fraction:.1%}")
        if out:
            pricepath.save(rs.path, out)
            print(f"wrote {out}")
    elif isinstance(obj, loaders.SpreadsLog):
        print(
            f"spreads: {len(obj)} rows ({obj.rows_skipped} skipped, {obj.duplicates} duplicates); "
            f"missing per source: {obj.missing()}"
        )
        print(obj.gaps(getattr(args, "interval", 300)).summary())
        for (a, b), xs in obj.pair_spreads_bps().items():
            if len(xs):
                print(
                    f"  {a}/{b}: n={len(xs)} p50={np.percentile(xs, 50):.0f} p95={np.percentile(xs, 95):.0f} "
                    f"max={xs.max():.0f} bps"
                )
        try:
            m = synthetic.SpreadModel.fit(obj, getattr(args, "interval", None))
            print("fitted spread model: " + json.dumps(synthetic.describe_model(m), default=float))
        except ValueError as e:
            print(f"spread model not fitted: {e}")
        if out:
            loaders.write_spreads_csv(obj, out)
            print(f"wrote {out}")
    elif isinstance(obj, loaders.PoolShareLog):
        print(
            f"pool shares: {len(obj)} blocks, {len(obj.keys)} keys, {obj.missing_heights()} missing heights, "
            f"{obj.duplicates} duplicate heights"
        )
        for k, v in list(obj.shares().items())[:15]:
            print(f"  {k}: {v:.1%}")
        if out:
            out.write_text(json.dumps({"shares": obj.shares(), "blocks": len(obj)}, indent=2) + "\n")
            print(f"wrote {out}")
    else:
        assert isinstance(obj, loaders.DepthSeries)
        print(
            f"depth ({obj.form}): {len(obj)} snapshots; median depth ±2 % "
            f"${np.nanmedian(obj.depth_2pct_usd):,.0f}; median 24 h volume "
            f"{'-' if np.all(np.isnan(obj.volume_24h_usd)) else f'${np.nanmedian(obj.volume_24h_usd):,.0f}'}"
        )
        print(obj.gaps().summary())
        if out:
            with out.open("w") as fh:
                fh.write("ts,depth_2pct_usd,volume_24h_usd,bid_depth_2pct_usd\n")
                for i, t in enumerate(obj.ts):
                    vals = [obj.depth_2pct_usd[i], obj.volume_24h_usd[i], obj.bid_depth_2pct_usd[i]]
                    fh.write(
                        f"{int(t)}," + ",".join("" if np.isnan(v) else repr(float(v)) for v in vals) + "\n"
                    )
            print(f"wrote {out}")
    return 0


# ---------------------------------------------------------------------------------------------------
# synth


def configure_synth(p: argparse.ArgumentParser) -> None:
    """Extra ``data synth`` arguments."""
    p.add_argument(
        "--resolution", choices=("hour", "block"), default="hour", help="output grid (default hour)"
    )
    p.add_argument("--p0-usd", type=float, default=synthetic.DEFAULT_P0 / 1e6, help="start price in USD")
    p.add_argument(
        "--drift",
        choices=synthetic.DRIFTS,
        default="zero",
        help="with --calibrate: zero (default; flat median path, D-RD-1) or the sample's fitted drift",
    )
    p.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="override a preset parameter (e.g. sigma=0.9); repeatable",
    )


def _load_price(path: str) -> pricepath.PricePath:
    """A PricePath from ``.npz`` / our CSV / any price CSV (hour grid, with the filled mask)."""
    if path.endswith(".npz"):
        return pricepath.from_npz(path)
    if Path(path + ".meta.json").exists():  # written by pricepath.to_csv
        return pricepath.from_csv(path)
    try:
        series = loaders.load_price_csv(path)
    except loaders.DataFormatError:
        return pricepath.from_csv(path)
    return loaders.resample_to_grid(series, "hour").path


def cli_synth(args: argparse.Namespace) -> int:
    """Simulate synthetic paths from a preset or a model fitted with ``--calibrate``."""
    from ybcal.config import Policy

    seed = args.seed if args.seed is not None else Policy().seed
    rng = np.random.default_rng(seed)
    try:
        if args.calibrate:
            drift = getattr(args, "drift", "zero")
            model = synthetic.fit(args.model, _load_price(args.calibrate), drift=drift)
        else:
            overrides = {}
            for kv in getattr(args, "set", []) or []:
                k, _, v = kv.partition("=")
                overrides[k] = json.loads(v)
            model = synthetic.preset(args.model, **overrides)
    except (ValueError, OSError, loaders.DataFormatError) as e:
        print(f"ybcal data synth: {e}", file=sys.stderr)
        return EXIT_BAD_INPUT
    p0 = round(getattr(args, "p0_usd", 0.40) * 1e6)
    pp = synthetic.simulate_years(model, args.paths, args.years, getattr(args, "resolution", "hour"), rng, p0)
    pp.meta["seed"] = seed
    info = synthetic.describe_model(model)
    print(f"model: {json.dumps(info, default=float)}")
    placeholder = "PLACEHOLDER preset (synthetic, not fitted)"
    origin = f"calibrated on {args.calibrate}" if args.calibrate else placeholder
    print(origin)
    if info.get("at_bounds"):
        print(
            f"WARNING: fit sits on its bounds ({', '.join(info['at_bounds'])}): not a usable generator "
            "for this data; prefer --model bootstrap (docs/real-data-2026-10.md)"
        )
    print(
        f"paths: {pp.n_paths} x {pp.n_steps} {pp.resolution} steps; realised vol "
        f"{describe_mod.realised_vol(pp):.1%}; median final price "
        f"${np.median(pp.prices[:, -1]) / 1e6:.4f}; median max drawdown "
        f"{np.median([describe_mod.max_drawdown(r) for r in pp.prices[:200]]):.1%}"
    )
    if args.out:
        pricepath.save(pp, args.out)
        print(f"wrote {args.out}")
    return 0


# ---------------------------------------------------------------------------------------------------
# describe


def configure_describe(p: argparse.ArgumentParser) -> None:
    """Extra ``data describe`` arguments."""
    p.add_argument("--json", action="store_true", help="JSON output")
    p.add_argument("--horizons", default=None, help="comma-separated drawdown horizons in days")


def cli_describe(args: argparse.Namespace) -> int:
    """Realised vol, drawdowns over all start dates, tail index, gaps, autocorrelation."""
    try:
        pp = _load_price(args.file)
    except (OSError, ValueError, loaders.DataFormatError) as e:
        print(f"ybcal data describe: {e}", file=sys.stderr)
        return EXIT_BAD_INPUT
    horizons = describe_mod.DRAWDOWN_HORIZONS_DAYS
    if getattr(args, "horizons", None):
        horizons = tuple(int(x) for x in args.horizons.split(","))
    d = describe_mod.describe(pp, horizons)
    print(describe_mod.to_json(d) if getattr(args, "json", False) else describe_mod.format_description(d))
    return 0

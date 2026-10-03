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

from ybcal.data import describe as describe_mod
from ybcal.data import fetch as fetch_mod
from ybcal.data import loaders, pricepath, synthetic

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


def cli_fetch(args: argparse.Namespace) -> int:
    """Download price data and write ``--out`` plus ``<out>.provenance.json``."""
    api_key = getattr(args, "api_key", None) or os.environ.get("YBCAL_COINGECKO_API_KEY")
    client = fetch_mod.HttpClient(
        retries=getattr(args, "retries", 4), max_body=fetch_mod.HISTORY_MAX_BODY_BYTES
    )
    out = Path(args.out)
    if out.parent and not out.parent.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
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
            model = synthetic.fit(args.model, _load_price(args.calibrate))
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

"""``ybcal sensitivity`` (wired by WP-8, D-WP6-7): joint sensitivity around a parameter set."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ybcal.config import Policy
from ybcal.optimize.joint import TOP_METRICS, joint_sensitivity
from ybcal.params.paramset import ParamSet, mainnet
from ybcal.params.registry import REGISTRY
from ybcal.studies.base import Budget, Env

OWNER_WP = "WP-8"


def configure_sensitivity(p: argparse.ArgumentParser) -> None:
    """Extra ``sensitivity`` arguments."""
    p.add_argument(
        "--set",
        dest="paramset",
        default=None,
        help="the set to analyse: a recommended.json, a report directory or a ybcal-paramset JSON "
        "(default: the shipped mainnet set)",
    )
    p.add_argument("--params", default=None, help="comma-separated parameters (default: every tunable one)")
    p.add_argument("--grouping", choices=("auto", "param", "group"), default="auto")
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--synthetic", action="store_true", help="ignore --data")


def load_set(path: str | None) -> ParamSet:
    """A ParamSet from a report dir / recommended.json (``ybcal-extract/1``) / paramset JSON."""
    if not path:
        return mainnet()
    p = Path(path)
    if p.is_dir():
        p = p / "recommended.json"
    d = json.loads(p.read_text())
    if d.get("format") == "ybcal-extract/1":
        from ybcal.params.extract import Extracted

        return ParamSet.from_extracted(Extracted.from_dict(d), "main")
    return ParamSet.from_dict(d)


def cli_sensitivity(args: argparse.Namespace) -> int:
    """Morris or Sobol at ±1 step plus the tornado; prints the table, writes JSON/CSV/PNGs with --out."""
    from ybcal.report.build import expand_data_args, load_data

    try:
        base = load_set(args.paramset)
        files = [] if args.synthetic else expand_data_args(args.data)
        from ybcal.data.inputs import parse_window

        data, prov, _ = load_data(files, parse_window(getattr(args, "window", None)))
    except (OSError, ValueError, KeyError) as e:
        print(f"ybcal sensitivity: {e}", file=sys.stderr)
        return 2
    params = [x.strip() for x in args.params.split(",")] if args.params else None
    unknown = [x for x in params or () if x not in REGISTRY or not REGISTRY[x].tunable]
    if unknown:
        print(f"ybcal sensitivity: not tunable registry parameters: {unknown}", file=sys.stderr)
        return 2
    pol = Policy.load(args.policy)
    env = Env(
        pol,
        Budget.named(args.budget),
        int(args.seed if args.seed is not None else pol.seed),
        data=data,
        provenance=prov,
    )  # type: ignore[arg-type]
    res = joint_sensitivity(
        base, env, method=args.method, params=params, workers=args.workers, grouping=args.grouping
    )
    key = "ST" if res.method == "sobol" else "share"
    print(
        f"{res.method}{' (grouped by study group)' if res.grouped else ''}: {len(res.params)} parameters, "
        f"{res.n_evals} evaluations, {res.seconds:.1f} s; index = {key}"
    )
    hdr = ["factor", *[TOP_METRICS[m][0] for m in res.metrics]]
    print("  ".join(hdr))
    for f in res.factors:
        print("  ".join([f, *[f"{float(res.indices[m][f].get(key, 0) or 0):.3f}" for m in res.metrics]]))
    print(
        "value at the set: " + ", ".join(f"{TOP_METRICS[m][0]} {res.base_values[m]:.4g}" for m in res.metrics)
    )
    print(f"insensitive ({len(res.insensitive())}): {', '.join(res.insensitive()) or 'none'}")
    if args.out:
        from ybcal.report import plots

        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "sensitivity.json").write_text(json.dumps(res.to_dict(), indent=1, default=str) + "\n")
        plots.sobol_bars(
            res.indices,
            {m: TOP_METRICS[m][0] for m in res.metrics},
            out / "indices.png",
            key=key,
            threshold=res.threshold,
        )
        for m in res.metrics:
            plots.tornado(res.tornado[m], res.base_values[m], TOP_METRICS[m][0], out / f"tornado_{m}.png")
        print(f"wrote {out}")
    return 0

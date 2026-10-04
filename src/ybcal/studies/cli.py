"""``ybcal study GROUP`` (owner: WP-8; D-8): one study (or all) and a mini report."""

from __future__ import annotations

import argparse
import sys

from ybcal.report.build import run_recommend
from ybcal.report.cli import add_run_options, config_from_args, print_summary
from ybcal.studies.base import GROUP_ORDER

OWNER_WP = "WP-8"


def configure_study(p: argparse.ArgumentParser) -> None:
    """Extra ``study`` arguments."""
    p.add_argument("--synthetic", action="store_true", help="synthetic data only")
    p.add_argument(
        "--sensitivity", action="store_true", help="also run the joint sensitivity (off by default)"
    )
    add_run_options(p, sensitivity=False)
    p.set_defaults(no_sensitivity=True, sensitivity_method="sobol")


def cli_study(args: argparse.Namespace) -> int:
    """Run one group's study (one round, no coupling) and write a mini report; ``all`` runs every group."""
    groups = list(GROUP_ORDER) if args.group == "all" else [args.group]
    args.no_sensitivity = not args.sensitivity
    try:
        cfg = config_from_args(
            args, groups=groups, mini=args.group != "all", title=f"Yellowback study {args.group}"
        )
    except (FileNotFoundError, ValueError, KeyError) as e:
        print(f"ybcal study: {e}", file=sys.stderr)
        return 2
    if cfg.max_rounds is None:
        cfg.max_rounds = 1
    res = run_recommend(cfg)
    print_summary(res)
    return 0

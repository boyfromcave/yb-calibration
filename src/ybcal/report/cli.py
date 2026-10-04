"""``ybcal recommend`` and ``ybcal report open`` (owner: WP-8; routes in ``ybcal.cli.ROUTES``)."""

from __future__ import annotations

import argparse
import contextlib
import functools
import http.server
import sys
from pathlib import Path

from ybcal.config import Policy
from ybcal.report.build import (
    RecommendConfig,
    expand_data_args,
    reproduce_config,
    run_recommend,
)
from ybcal.studies.base import GROUP_ORDER, Budget

OWNER_WP = "WP-8"

DEFAULT_LOCAL_DATA = Path("data") / "local"


def _groups(text: str | None) -> list[str] | None:
    if not text:
        return None
    gs = [g.strip().upper() for g in text.split(",") if g.strip()]
    bad = [g for g in gs if g not in GROUP_ORDER]
    if bad:
        raise SystemExit(f"unknown group(s) {bad}; choose from {', '.join(GROUP_ORDER)}")
    return gs


def add_run_options(p: argparse.ArgumentParser, *, sensitivity: bool = True) -> None:
    """Options shared by ``recommend`` and ``study``."""
    p.add_argument("--workers", type=int, default=None, help="worker processes (default: all cores)")
    p.add_argument("--ycash6", default=None, help="ycash6 clone for the patch check (default $YBCAL_YCASH6)")
    p.add_argument(
        "--max-rounds", type=int, default=None, help="joint-pass rounds (default policy.max_rounds_joint)"
    )
    p.add_argument(
        "--cache", default=None, metavar="DIR", help="persist evaluations under DIR (e.g. .work/cache)"
    )
    if sensitivity:
        p.add_argument("--no-sensitivity", action="store_true", help="skip the joint sensitivity pass")
        p.add_argument("--sensitivity-method", choices=("sobol", "morris"), default="sobol")


def configure_recommend(p: argparse.ArgumentParser) -> None:
    """Extra ``recommend`` arguments."""
    p.add_argument("--groups", default=None, help="comma-separated subset of groups (default all)")
    add_run_options(p)


def data_files(args: argparse.Namespace) -> list[Path]:
    """``--data`` files/dirs; without them ``data/local/`` when it holds files (unless --synthetic)."""
    if getattr(args, "synthetic", False):
        if args.data:
            print("ybcal: --synthetic given; ignoring --data", file=sys.stderr)
        return []
    if args.data:
        return expand_data_args(args.data)
    if DEFAULT_LOCAL_DATA.is_dir():
        return expand_data_args([str(DEFAULT_LOCAL_DATA)])
    return []


def config_from_args(
    args: argparse.Namespace, *, groups: list[str] | None, mini: bool = False, title: str | None = None
) -> RecommendConfig:
    """Map CLI flags onto :class:`RecommendConfig` (``--manifest`` restores a recorded run)."""
    budget, seed, policy_path = args.budget, args.seed, args.policy
    files = data_files(args)
    workers = args.workers
    window = getattr(args, "window", None)
    man = getattr(args, "manifest", None)
    if man:
        rc = reproduce_config(man)
        for pr in rc["problems"]:
            print(f"ybcal: warning: {pr}", file=sys.stderr)
        budget, seed, policy_path = rc["budget"], rc["seed"], rc["policy_path"] or None
        files = [Path(f) for f in rc["data_files"]]
        groups = rc["groups"] or groups
        workers = workers or rc["workers"]
        window = window or rc.get("window")
    policy = Policy.load(policy_path)
    sets = list(getattr(args, "policy_set", None) or [])
    if man and not sets:
        sets = list(rc.get("policy_set") or [])
    policy, _ = policy.with_overrides(sets)
    from ybcal.data.inputs import parse_window

    parse_window(window)  # fail early on a bad --window
    cfg = RecommendConfig(
        budget=Budget.named(budget),
        policy=policy,
        policy_path=policy_path or "",
        seed=seed,
        data_files=files,
        out=Path(args.out) if args.out else None,
        workers=workers,
        groups=groups,
        sensitivity=not getattr(args, "no_sensitivity", True),
        sensitivity_method=getattr(args, "sensitivity_method", "sobol"),
        ycash6=args.ycash6,
        command=["ybcal", *sys.argv[1:]]
        if sys.argv and "ybcal" in sys.argv[0]
        else ["ybcal", *args.command_path],
        max_rounds=args.max_rounds,
        cache_dir=args.cache,
        mini=mini,
        window=window,
        policy_set=sets,
    )
    if title:
        cfg.title = title
    return cfg


def print_summary(res) -> None:
    """Final lines of a run."""
    c = res.counts
    print(f"report: {res.out / 'report.html'}")
    print("verdicts: " + ", ".join(f"{k} {v}" for k, v in c.items()))
    bad = [g for g, o in res.joint.outcomes.items() if o.status != "ok"]
    if bad:
        print(
            "not run: "
            + "; ".join(
                f"{g} ({res.joint.outcomes[g].status}: "
                f"{res.joint.outcomes[g].reason.splitlines()[0] if res.joint.outcomes[g].reason else ''})"
                for g in bad
            )
        )
    print(f"lock-ready: {'yes' if res.ready else 'no'}; {res.seconds:.0f} s")


def cli_recommend(args: argparse.Namespace) -> int:
    """All studies, the joint pass, sensitivity and the report (PLAN §7)."""
    try:
        cfg = config_from_args(args, groups=_groups(args.groups))
    except (FileNotFoundError, ValueError, KeyError) as e:
        print(f"ybcal recommend: {e}", file=sys.stderr)
        return 2
    res = run_recommend(cfg)
    print_summary(res)
    return 0


def configure_open(p: argparse.ArgumentParser) -> None:
    """Extra ``report open`` arguments."""
    p.add_argument("--port", type=int, default=8000, help="port for --serve (default 8000)")
    p.add_argument("--host", default="127.0.0.1")


def cli_open(args: argparse.Namespace) -> int:
    """Print the report path; with ``--serve`` serve the run directory over HTTP until interrupted."""
    d = Path(args.run_dir)
    html = d / "report.html" if d.is_dir() else d
    if not html.exists():
        print(f"ybcal report open: no report.html in {d}", file=sys.stderr)
        return 1
    print(html.resolve())
    if not args.serve:
        return 0
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(html.parent))
    with http.server.ThreadingHTTPServer((args.host, args.port), handler) as srv:
        print(f"serving http://{args.host}:{srv.server_address[1]}/{html.name} (Ctrl-C to stop)", flush=True)
        with contextlib.suppress(KeyboardInterrupt):
            srv.serve_forever()
    return 0

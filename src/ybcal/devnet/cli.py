"""``ybcal devnet build | run | validate`` (owner: WP-9; dispatch convention in docs/architecture.md).

Exit codes: ``0`` done — or *skipped* because the environment cannot build/run nodes (the reason
is printed as ``skipped: …``; ``--strict`` turns a skip into ``3``); ``1`` an error or a failed
differential suite; ``4`` refused (binary/commit or node-parameter skew without
``--allow-version-skew``, or an overlay a CI binary cannot carry).

``ROUTES`` has no ``fetch-binary`` command, so ``build`` takes ``--from-ci-run RUN [--artifact
NAME]`` (download a CI release artifact) and ``--ycashd PATH`` (register an existing binary).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from ybcal.devnet import build as b
from ybcal.devnet.diff import resolve_simulator, validate_suite
from ybcal.devnet.overlay import (
    OverlayError,
    OverlaySplit,
    build_key,
    load_overlay,
    make_patch,
    read_params_cpp,
    split,
)
from ybcal.devnet.runner import DEFAULT_PORTSEED, DevnetConfig, RunResult, node_conf, run_devnet
from ybcal.devnet.scenarios import SCENARIOS, SUITE, make_schedule
from ybcal.devnet.status import Skipped
from ybcal.devnet.worktree import WorktreeError, create_worktree, work_dir, ycash6_repo
from ybcal.params.paramset import ParamSet
from ybcal.params.paramset import regtest as shipped_regtest
from ybcal.params.registry import PINNED_COMMIT
from ybcal.params.scaling import ScaledSet, scale_to_regtest

OWNER_WP = "WP-9"

EXIT_OK, EXIT_ERROR, EXIT_SKIPPED_STRICT, EXIT_REFUSED = 0, 1, 3, 4


# ---------------------------------------------------------------------------------------------------
# argument hooks


def _common(p: argparse.ArgumentParser, *, overlay: bool, ycash6: bool, seed: bool) -> None:
    if overlay:
        p.add_argument(
            "--overlay",
            default=None,
            help="parameter overlay JSON (regtest set, delta, or a mainnet-scale set to scale)",
        )
    if ycash6:
        p.add_argument("--ycash6", default=None, help="ycash6 clone (default $YBCAL_YCASH6)")
        p.add_argument(
            "--ref", default=PINNED_COMMIT, help=f"commit to build/run against (default {PINNED_COMMIT})"
        )
    if seed:
        p.add_argument("--seed", type=int, default=None, help="schedule / jitter seed (default 0)")
    p.add_argument(
        "--scale-factor",
        type=float,
        default=None,
        help="factor for scaling a mainnet-scale overlay (default pSlowWindow/64)",
    )
    p.add_argument(
        "--term-factor",
        type=float,
        default=None,
        help="factor for terms/grace/abandonment when scaling (default = --scale-factor)",
    )
    p.add_argument(
        "--ycashd", default=None, help="use this ycashd (default $YBCAL_YCASHD, then the build cache)"
    )
    p.add_argument(
        "--allow-version-skew",
        action="store_true",
        help="run a binary whose commit or yed_getinfo.params differ from the pin/overlay (warns)",
    )
    p.add_argument("--strict", action="store_true", help="exit 3 instead of 0 when skipped")
    p.add_argument("--json", action="store_true", help="machine-readable output")


def configure_build(p: argparse.ArgumentParser) -> None:
    """Extra ``devnet build`` arguments (``--overlay``, ``--ycash6``, ``--ref`` come from cli.py)."""
    _common(p, overlay=False, ycash6=False, seed=False)
    p.add_argument(
        "--from-ci-run",
        default=None,
        metavar="RUN",
        help=f"download a CI release artifact instead of building (e.g. {b.KNOWN_CI_RUN})",
    )
    p.add_argument("--artifact", default=None, help="artifact name (default release-<this platform>)")
    p.add_argument("--ci-repo", default=b.CI_REPO, help=f"GitHub repo of the CI run (default {b.CI_REPO})")
    p.add_argument("--jobs", "-j", type=int, default=None, help="make -j (default: CPU count)")
    p.add_argument(
        "--dry-run", action="store_true", help="print the plan, flags, patch and preflight; build nothing"
    )
    p.add_argument("--force", action="store_true", help="rebuild even when the cache has this overlay")


def configure_run(p: argparse.ArgumentParser) -> None:
    """Extra ``devnet run`` arguments (``--scenario``, ``--overlay``, ``--seed`` come from cli.py)."""
    _common(p, overlay=False, ycash6=True, seed=False)
    p.add_argument(
        "--portseed", type=int, default=DEFAULT_PORTSEED, help=f"port seed (default {DEFAULT_PORTSEED})"
    )
    p.add_argument("--pools", type=int, default=3, help="pool nodes (1-3, default 3)")
    p.add_argument("--dir", default=None, help="run directory (default .work/devnet/<scenario>-<time>)")
    p.add_argument(
        "--keep", action="store_true", help="keep the node datadirs (default: wiped after the scrape)"
    )
    p.add_argument(
        "--launcher",
        action="store_true",
        help="drive contrib/yellowback/devnet/yellowback-devnet (attestor seats) instead of the "
        "minimal launcher",
    )
    p.add_argument(
        "--jitter-bps", type=int, default=10, help="per-block quote jitter (default 10 bps; avoids PIN-1)"
    )
    p.add_argument(
        "--blocks-per-step", type=int, default=1, help="blocks per price of a price file (default 1)"
    )
    p.add_argument(
        "--dry-run", action="store_true", help="print the schedule, flags and node config; start nothing"
    )


def configure_validate(p: argparse.ArgumentParser) -> None:
    """Extra ``devnet validate`` arguments (``--scenario`` comes from cli.py)."""
    _common(p, overlay=True, ycash6=True, seed=True)
    p.add_argument("--portseed", type=int, default=DEFAULT_PORTSEED)
    p.add_argument("--out", default=None, help="write the suite report JSON here")


# ---------------------------------------------------------------------------------------------------
# helpers


def _emit(args: argparse.Namespace, doc: dict[str, Any], text: str) -> None:
    print(json.dumps(doc, indent=2, default=str) if getattr(args, "json", False) else text)


def _skipped(args: argparse.Namespace, sk: Skipped, extra: dict[str, Any] | None = None) -> int:
    _emit(args, {**sk.to_dict(), **(extra or {})}, str(sk))
    return EXIT_SKIPPED_STRICT if getattr(args, "strict", False) else EXIT_OK


def resolve_overlay(args: argparse.Namespace) -> tuple[ParamSet, ScaledSet | None]:
    """The regtest overlay of ``--overlay`` (scaled when mainnet-scale), else the shipped column."""
    if not getattr(args, "overlay", None):
        return shipped_regtest(), None
    ps = load_overlay(args.overlay)
    if ps.is_regtest_scale:
        return ps, None
    scaled = scale_to_regtest(ps, args.scale_factor, term_factor=args.term_factor)
    return scaled.params, scaled


def _scaled_text(scaled: ScaledSet | None) -> str:
    if scaled is None:
        return ""
    sig = scaled.significant_losses()
    lines = [
        f"scaled a {scaled.source.network} set by {float(scaled.factor):g} "
        f"(terms {float(scaled.term_factor):g}); "
        f"{len(scaled.losses)} ratio losses, {len(sig)} over 5 %:"
    ]
    lines += [f"  {x}" for x in sig]
    lines += [f"  note: {n}" for n in scaled.notes]
    return "\n".join(lines) + "\n"


def _repo(args: argparse.Namespace) -> Path | None:
    return ycash6_repo(getattr(args, "ycash6", None))


def _version_gate(
    args: argparse.Namespace, binfo: b.BinaryInfo, repo: Path | None, ref: str
) -> tuple[int, dict[str, Any], str]:
    ver = b.binary_version(binfo.ycashd)
    skew = b.check_skew(repo, binfo.commit or ver.commit, ref, allow=args.allow_version_skew)
    doc = {"ycashd": str(binfo.ycashd), "origin": binfo.origin, "banner": ver.banner, "skew": skew.to_dict()}
    text = f"ycashd {binfo.ycashd} ({binfo.origin}): {ver.banner or '(no banner)'}\n"
    if skew.relation != "equal":
        text += f"version: {skew.message or skew.relation}\n"
        for c in skew.commits_between:
            text += f"  {c}\n"
        for net, diffs in skew.param_diffs.items():
            for k, (bv, pv) in diffs.items():
                text += f"  {net} {k}: binary {bv} vs pin {pv}\n"
    if not skew.ok:
        return EXIT_REFUSED, doc, text + "refused: pass --allow-version-skew to use this binary anyway"
    if skew.relation != "equal":
        text += "warning: version skew allowed (--allow-version-skew)\n"
    return EXIT_OK, doc, text


# ---------------------------------------------------------------------------------------------------
# build


def cli_build(args: argparse.Namespace) -> int:
    """Worktree + overlay patch + build (cached), or fetch a CI binary / register ``--ycashd``."""
    try:
        overlay, scaled = resolve_overlay(args)
        sp = split(overlay)
    except (OverlayError, ValueError, KeyError) as e:
        print(f"ybcal devnet build: {e}", file=sys.stderr)
        return EXIT_ERROR
    repo, ref = _repo(args), args.ref
    head = _scaled_text(scaled) + (
        f"runtime flags: {' '.join(sp.node_args())}\n"
        f"compiled values changed: {len(sp.compiled)} -> key {build_key(sp, ref)}\n"
    )
    if args.from_ci_run or args.ycashd:
        if sp.compiled:
            print(
                head + f"refused: a prebuilt binary carries the stock RegtestParams(); this overlay changes "
                f"{sorted(sp.compiled)} — build it instead",
                file=sys.stderr,
            )
            return EXIT_REFUSED
        if args.from_ci_run:
            got = b.fetch_ci_binary(args.from_ci_run, args.artifact, repo=args.ci_repo)
        else:
            got = b.resolve_binary(args.ycashd)
        if isinstance(got, Skipped):
            return _skipped(args, got)
        code, doc, text = _version_gate(args, got, repo, ref)
        _emit(args, {"status": "refused" if code else "ok", **doc}, head + text)
        return code
    if repo is None:
        return _skipped(args, Skipped("no ycash6 clone: pass --ycash6 PATH or set YBCAL_YCASH6", "build"))
    if args.dry_run:
        try:
            patch = make_patch(read_params_cpp(repo, ref), sp.compiled, tag=build_key(sp, ref))
        except OverlayError as e:
            print(f"ybcal devnet build: {e}", file=sys.stderr)
            return EXIT_ERROR
        pre = b.preflight(None)
        jobs = args.jobs or 2
        steps = b.plan_build(work_dir() / f"ycash6-{ref}", jobs, configured=False)
        doc = {
            "runtime": sp.node_args(),
            "compiled": sp.compiled,
            "key": build_key(sp, ref),
            "patch": patch,
            "preflight": pre.to_dict() if isinstance(pre, Skipped) else "ready",
            "steps": [" ".join(s.argv) for s in steps],
        }
        text = head + "\n".join(f"step: {' '.join(s.argv)}  # {s.what}" for s in steps) + "\n"
        text += (
            patch or "(no patch: stock RegtestParams)\n"
        ) + f"preflight: {pre if isinstance(pre, Skipped) else 'ready'}"
        _emit(args, doc, text)
        return EXIT_OK
    try:
        res = b.build(repo, sp, commit=ref, jobs=args.jobs, force=args.force)
    except (WorktreeError, OverlayError) as e:
        print(f"ybcal devnet build: {e}", file=sys.stderr)
        return EXIT_ERROR
    if isinstance(res, Skipped):
        return _skipped(args, res, {"key": build_key(sp, ref)})
    _emit(
        args,
        {"status": "ok", "ycashd": str(res.binary.ycashd), "cached": res.cached, "key": res.binary.key},
        head + f"{'cached' if res.cached else 'built'}: {res.binary.ycashd}",
    )
    return EXIT_OK


# ---------------------------------------------------------------------------------------------------
# run


def cli_run(args: argparse.Namespace) -> int:
    """Up, replay a scenario (or a price file), scrape, down."""
    try:
        overlay, scaled = resolve_overlay(args)
        sp: OverlaySplit = split(overlay)
        sched = make_schedule(
            args.scenario, sp.params, seed=args.seed or 0, file_blocks_per_step=args.blocks_per_step
        )
    except (OverlayError, ValueError, KeyError) as e:
        print(f"ybcal devnet run: {e}", file=sys.stderr)
        return EXIT_ERROR
    head = _scaled_text(scaled) + (
        f"scenario {sched.name}: {sched.description}; {sched.total_blocks} blocks "
        f"(~{2 * sched.total_blocks // 60} min at ~2 s/block)\n"
        f"runtime flags: {' '.join(sp.node_args())}\n"
    )
    if args.dry_run:
        cfg = DevnetConfig(
            Path(args.ycashd or "ycashd"),
            Path(args.dir or "RUN_DIR"),
            sp.node_args(),
            portseed=args.portseed,
            n_pools=args.pools,
            dark_miner="dark_miner" in sched.needs,
        )
        _emit(
            args,
            {"schedule": sched.to_dict(), "runtime": sp.node_args(), "node0_conf": node_conf(cfg, 0)},
            head + "node0 ycash.conf:\n" + node_conf(cfg, 0),
        )
        return EXIT_OK
    repo = _repo(args)
    launcher_wt = None
    if args.launcher:
        if repo is None:
            return _skipped(args, Skipped("--launcher needs a ycash6 clone (--ycash6 / YBCAL_YCASH6)", "run"))
        launcher_wt = create_worktree(repo, args.ref).path
    res = run_devnet(
        sched,
        sp,
        ycashd=args.ycashd,
        repo=repo,
        commit=args.ref,
        run_dir=Path(args.dir) if args.dir else None,
        portseed=args.portseed,
        n_pools=args.pools,
        seed=args.seed or 0,
        jitter_bps=args.jitter_bps,
        allow_version_skew=args.allow_version_skew,
        keep=args.keep,
        launcher_worktree=launcher_wt,
    )
    if isinstance(res, Skipped):
        return _skipped(args, res)
    return _report_run(args, res, head)


def _report_run(args: argparse.Namespace, res: RunResult, head: str) -> int:
    text = head + f"status: {res.status} {res.message}\nrun dir: {res.run_dir}\n"
    if res.scrape is not None and res.scrape.heights:
        text += (
            f"scraped heights {res.scrape.heights[0]}..{res.scrape.heights[1]} into {res.run_dir}/scrape\n"
        )
    _emit(args, res.to_dict(), text)
    return {"ok": EXIT_OK, "refused": EXIT_REFUSED}.get(res.status, EXIT_ERROR)


# ---------------------------------------------------------------------------------------------------
# validate


def cli_validate(args: argparse.Namespace) -> int:
    """The PLAN §6.4 differential suite (pending until the simulator exists; skipped without nodes)."""
    names = args.scenario or list(SUITE)
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        print(
            f"ybcal devnet validate: unknown scenario(s) {unknown}; suite: {', '.join(SUITE)}",
            file=sys.stderr,
        )
        return EXIT_ERROR
    try:
        overlay, scaled = resolve_overlay(args)
        sp = split(overlay)
    except (OverlayError, ValueError, KeyError) as e:
        print(f"ybcal devnet validate: {e}", file=sys.stderr)
        return EXIT_ERROR
    simulator = resolve_simulator()
    repo = _repo(args)
    binfo = b.resolve_binary(args.ycashd, key=build_key(sp, args.ref))
    node_runner = None
    skip_reason = binfo.reason if isinstance(binfo, Skipped) else ""
    if not isinstance(binfo, Skipped):

        def node_runner(sched: Any) -> Any:
            r = run_devnet(
                sched,
                sp,
                ycashd=binfo.ycashd,
                repo=repo,
                commit=args.ref,
                portseed=args.portseed,
                seed=args.seed or 0,
                allow_version_skew=args.allow_version_skew,
            )
            if isinstance(r, Skipped):
                return r
            if r.status != "ok" or r.scrape is None:
                return Skipped(f"devnet run {r.status}: {r.message}", "run")
            return r.scrape.history

    suite = validate_suite(
        names,
        simulator,
        params=sp.params,
        node_runner=node_runner,
        seed=args.seed or 0,
        node_skip_reason=skip_reason or "no devnet runner",
    )
    out = (
        Path(args.out)
        if args.out
        else work_dir() / "devnet" / f"validate-{time.strftime('%Y%m%d-%H%M%S')}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = suite.to_dict() | {"overlay_digest": sp.params.digest(), "scaled": scaled is not None}
    out.write_text(json.dumps(doc, indent=2, default=str) + "\n")
    _emit(args, doc, _scaled_text(scaled) + suite.summary() + f"\nreport: {out}")
    if suite.failed:
        return EXIT_ERROR
    if not suite.validated and args.strict:
        return EXIT_SKIPPED_STRICT
    return EXIT_OK

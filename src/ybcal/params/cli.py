"""``ybcal params show | extract | check | doc`` (owner: WP-0)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from ybcal.params.doc import fmt_value, render_markdown
from ybcal.params.extract import Extracted, ExtractError, check_drift, extract, load_snapshot
from ybcal.params.invariants import Context
from ybcal.params.paramset import ParamSet
from ybcal.params.registry import PINNED_COMMIT, REGISTRY, ParamSpec, specs

#: Environment variable naming a local ycash6 clone (fallback for --ycash6).
YCASH6_ENV = "YBCAL_YCASH6"
DOCS_PARAMETERS = Path("docs") / "parameters.md"


def _spec_dict(s: ParamSpec) -> dict[str, Any]:
    return {
        "name": s.name, "mainnet": s.mainnet, "regtest": s.regtest, "klass": s.klass, "group": s.group,
        "unit": s.unit, "hashed": s.hashed, "rules": list(s.rules), "bounds": list(s.bounds),
        "step": s.step, "doc": s.doc, "consensus": s.consensus, "origin": s.origin,
        "change_path": s.change_path, "parents": list(s.parents), "regtest_flag": s.regtest_flag,
        "cpp_expr": s.cpp_expr, "note": s.note,
    }


def _table(rows: list[list[str]]) -> str:
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    out = []
    for n, r in enumerate(rows):
        out.append("  ".join(c.ljust(widths[i]) for i, c in enumerate(r)).rstrip())
        if n == 0:
            out.append("  ".join("-" * w for w in widths))
    return "\n".join(out)


def _source(args: argparse.Namespace) -> tuple[Extracted, str]:
    """Extraction from --ycash6 / $YBCAL_YCASH6, else the committed snapshot."""
    repo = getattr(args, "ycash6", None) or os.environ.get(YCASH6_ENV)
    ref = getattr(args, "ref", None) or PINNED_COMMIT
    if repo:
        return extract(repo, ref), f"{repo} @ {ref}"
    if ref != PINNED_COMMIT:
        raise ExtractError(f"--ref {ref} needs --ycash6 (the snapshot is at {PINNED_COMMIT})")
    return load_snapshot(), f"snapshot {PINNED_COMMIT} (no --ycash6 / ${YCASH6_ENV})"


def cli_show(args: argparse.Namespace) -> int:
    """Print the §1.3 table (optionally against live source)."""
    sel = list(specs(group=args.group, klass=args.klass))
    live: dict[str, dict[str, Any]] = {}
    if args.ycash6:
        ex, _ = _source(args)
        live = {"main": ex.values("main"), "regtest": ex.values("regtest")}
    if args.json:
        out = [_spec_dict(s) for s in sel]
        if live:
            for d in out:
                d["source_mainnet"] = live["main"].get(d["name"])
                d["source_regtest"] = live["regtest"].get(d["name"])
        print(json.dumps(out, indent=2))
        return 0
    header = ["field", "mainnet", "regtest", "class", "group", "unit", "hashed", "rules"]
    if live:
        header.append("source")
    rows = [header]
    for s in sel:
        r = [s.name, fmt_value(s.mainnet), fmt_value(s.regtest), s.klass, s.group, s.unit,
             "yes" if s.hashed else "", ",".join(s.rules)]
        if live:
            same = live["main"].get(s.name) == s.mainnet and live["regtest"].get(s.name) == s.regtest
            r.append("ok" if same else "DRIFT")
        rows.append(r)
    print(_table(rows))
    print(f"\n{len(sel)} of {len(REGISTRY)} parameters (ycash6 {PINNED_COMMIT})")
    return 0


def cli_extract(args: argparse.Namespace) -> int:
    """Extract main/test/regtest sets from source and print or write JSON."""
    ex, where = _source(args)
    text = ex.to_json()
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text)
        print(f"wrote {args.out} from {where}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


def cli_check(args: argparse.Namespace) -> int:
    """Drift vs registry + invariants on main and regtest; non-zero on any failure."""
    from ybcal.config import Policy

    ex, where = _source(args)
    print(f"source: {where} (commit {ex.commit[:12]})")
    failures = 0
    drift = check_drift(REGISTRY, ex)
    if drift:
        print(f"DRIFT ({len(drift)}):")
        for d in drift:
            print(f"  {d}")
        failures += len(drift)
    else:
        print(f"drift: none ({len(ex.fields)} fields, {len(REGISTRY)} registry entries)")
    policy = Policy.load(args.policy)
    ctx = Context.from_policy(policy, release_tip=args.release_tip)
    for net in ("main", "regtest"):
        try:
            ps = ParamSet.from_extracted(ex, net)
        except (KeyError, TypeError) as e:
            print(f"{net}: cannot build a ParamSet from source: {e}")
            failures += 1
            continue
        viol = ps.check(ctx)
        if viol:
            print(f"{net}: {len(viol)} invariant violation(s):")
            for v in viol:
                print(f"  {v}")
            failures += len(viol)
        else:
            print(f"{net}: all invariants pass")
    failures += _check_policy_base(policy, ctx)
    print("OK" if not failures else f"FAILED ({failures})")
    return 0 if not failures else 1


def _check_policy_base(policy: Any, ctx: Context) -> int:
    """When the policy sets a base (``base_set`` / ``base_values``), run the invariants on that set
    too and list what it changes against the shipped mainnet column; returns the failure count."""
    from ybcal.params.classes import CLASS_NAMES, disabled_classes
    from ybcal.params.paramset import mainnet, policy_base

    if not (policy.base_set or policy.base_values):
        return 0
    try:
        base = policy_base(policy)
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"policy base: cannot build it: {type(e).__name__}: {e}")
        return 1
    where = policy.base_set or "the shipped mainnet column"
    delta = mainnet().delta(base)
    off = disabled_classes(base)
    print(f"policy base: {where} + {len(policy.base_values)} base_values -> {len(delta)} value(s) differ "
          f"from the shipped set" + (f"; disabled classes (empty term range, H-5): "
                                      f"{', '.join(CLASS_NAMES[i] for i in off)}" if off else ""))
    planned = [k for k in REGISTRY if REGISTRY[k].proposed and base[k] != REGISTRY[k].mainnet]
    if planned:
        print(f"  proposed field(s) set by the policy, not yet in source at the pin: {', '.join(planned)}")
    viol = base.check(ctx)
    if viol:
        print(f"policy base: {len(viol)} invariant violation(s):")
        for v in viol:
            print(f"  {v}")
        return len(viol)
    print("policy base: all invariants pass")
    return 0


def cli_doc(args: argparse.Namespace) -> int:
    """Write (or --check) docs/parameters.md from the registry."""
    text = render_markdown()
    out = Path(args.out)
    if args.check:
        if not out.exists() or out.read_text() != text:
            print(f"{out} is out of date; run `ybcal params doc`", file=sys.stderr)
            return 1
        print(f"{out} is up to date")
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(f"wrote {out}")
    return 0

"""``ybcal verify`` (owner: WP-1; PLAN §3.2, §10 M2).

Runs, and prints as a pass/fail table:

1. vendored-file integrity (pin headers, hashes; and, given a ycash6 clone, equality with a fresh
   render at the pin);
2. the golden vector replayed through the vendored reference model (state hash, tip, totals);
3. the kernels and vkernels recomputing every snapshot of the golden chain;
4. the C++ worked examples of ``src/test/yellowback_math_tests.cpp``;
5. a quick seeded parity sample (vkernels == kernels == reference);
6. (informational) the reference model's own mainnet column against the registry.

``ybcal verify --revendor --ycash6 PATH [--ref REF]`` re-vendors the reference files from a ycash6
clone (read-only towards it) and exits; run ``ybcal verify`` again afterwards.
Exit code: 0 all pass, 1 any failure.
"""

from __future__ import annotations

import argparse
import os
import sys

from ybcal.model import vendor

OWNER_WP = "WP-1"


def configure_verify(parser: argparse.ArgumentParser) -> None:
    """Extra ``ybcal verify`` arguments (the dispatch convention's ``configure_<name>`` hook)."""
    parser.add_argument(
        "--revendor",
        action="store_true",
        help="re-copy the reference model files from a ycash6 clone, then exit",
    )
    parser.add_argument(
        "--ycash6",
        default=None,
        help="ycash6 clone (default $YBCAL_YCASH6); with it, also diff the vendored files",
    )
    parser.add_argument(
        "--ref",
        default=None,
        help=f"git ref to vendor from / compare with (default {vendor.VENDORED_COMMIT[:7]})",
    )
    parser.add_argument("--samples", type=int, default=300, help="parity sample size (default 300)")
    parser.add_argument("--seed", type=int, default=0, help="parity sample seed (default 0)")
    parser.add_argument("--quiet", action="store_true", help="print failures and the summary only")


def _ycash6(args: argparse.Namespace) -> str | None:
    """The explicit clone (--ycash6, else $YBCAL_YCASH6 unless $YBCAL_NO_YCASH6); never guessed."""
    if getattr(args, "ycash6", None):
        return args.ycash6
    if os.environ.get("YBCAL_NO_YCASH6"):
        return None
    return os.environ.get("YBCAL_YCASH6") or None


def _table(rows: list[tuple[str, str, bool, str]], quiet: bool) -> None:
    width = max((len(r[1]) for r in rows), default=10)
    for section, name, ok, detail in rows:
        if quiet and ok:
            continue
        print(f"{'PASS' if ok else 'FAIL'}  {section:<9} {name:<{width}}  {detail}")


def cli_verify(args: argparse.Namespace) -> int:
    ycash6 = _ycash6(args)
    ref = getattr(args, "ref", None)
    if getattr(args, "revendor", False):
        if not ycash6:
            print("ybcal verify --revendor: needs --ycash6 PATH (or $YBCAL_YCASH6)", file=sys.stderr)
            return 2
        written = vendor.revendor(ycash6, ref or vendor.VENDORED_COMMIT)
        for name, digest in written.items():
            print(f"vendored {name:<24} sha256 {digest}")
        print("re-vendored; update vendor.VENDORED_COMMIT if the pin moved, then run `ybcal verify`")
        return 0

    rows: list[tuple[str, str, bool, str]] = []
    problems = vendor.check_vendored()
    rows.append(
        ("vendor", "vendored files intact", not problems, "; ".join(problems) or vendor.VENDORED_COMMIT[:12])
    )
    if ycash6:
        try:
            diff = vendor.check_against_source(ycash6, ref)
            rows.append(
                (
                    "vendor",
                    f"equal to ycash6 {ref or vendor.VENDORED_COMMIT[:7]}",
                    not diff,
                    "; ".join(diff) or ycash6,
                )
            )
        except Exception as e:  # pragma: no cover - environment dependent
            rows.append(("vendor", "equal to ycash6", False, f"{type(e).__name__}: {e}"))

    from ybcal.model import examples, golden, parity

    rows += [("golden", n, ok, d) for n, ok, d in golden.check_golden()]
    rows += [("golden", n, ok, d) for n, ok, d in golden.check_chain_kernels()]
    for ex, ok, got in examples.run_examples():
        detail = f"math_tests:{ex.cpp_line}" + ("" if ok else f" got {got!r} want {ex.expected!r}")
        rows.append((ex.rule, ex.name, ok, detail))
    rows += [("parity", n, ok, d) for n, ok, d in parity.quick_parity(args.samples, args.seed)]
    _table(rows, getattr(args, "quiet", False))

    drift = examples.reference_param_drift()
    if drift:
        print(
            "note: the reference model's own mainnet column differs from the registry (informational; "
            "the kernels take parameters from the registry): "
            + ", ".join(f"{k} model={a} registry={b}" for k, (a, b) in sorted(drift.items()))
        )
    failed = sum(1 for r in rows if not r[2])
    print(
        f"ybcal verify: {len(rows) - failed}/{len(rows)} checks passed"
        + (f", {failed} FAILED" if failed else "")
    )
    return 1 if failed else 0

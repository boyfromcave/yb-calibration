#!/usr/bin/env python
"""P(bad debt) of class A as a function of ``baseRatioBps[0]`` — the curve behind H-3, computed
DIRECTLY over a ratio grid (no optimiser): one G3 hour ensemble per (window, model, seed), then
:func:`ybcal.studies.g3_collateral.bad_debt` per ratio and member, exactly as ``G3Study.evaluate``
scores the ``bad_debt_A`` constraint (worst member, σ multiplier at the policy's ``sigma_mult_at``).

    python docs/reports/2026-10-tolerance/bad_debt_curve.py curve --window 2025-26 --seeds 20261003,20261004,20261005 \
        --budget standard --models bootstrap --out docs/reports/2026-10-tolerance/data
    python docs/reports/2026-10-tolerance/bad_debt_curve.py ingest .work/robust/harden-std --budget standard --out ...
    python docs/reports/2026-10-tolerance/bad_debt_curve.py tables docs/reports/2026-10-tolerance/data

``curve`` writes ``curve.csv`` (window, model, seed, budget, ratio, member, p, p_lock, shortfall, n)
and, with ``--sensitivity``, ``terms.csv`` (P(bad debt) per grid term at each ratio) and ``sigma.csv``
(σ multiplier median / p90 / at the clamp ``sigmaMultMaxBps``). ``ingest`` reads a finished robust
sweep's ``evidence/G3/g3_tradeoff.csv`` files into the same ``curve.csv`` layout (rows tagged
``source=robust``). ``tables`` inverts the curves: the smallest grid ratio whose worst-member
P(bad debt) is within each tolerance in every seed of a window, and the worse-window / full-only rules.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
import time
from pathlib import Path

import numpy as np

from ybcal.config import Policy
from ybcal.data.inputs import parse_window
from ybcal.params.paramset import policy_base
from ybcal.report.build import load_data
from ybcal.report.robust import MODEL_SETS
from ybcal.studies import g3_collateral as G3
from ybcal.studies.base import Budget, Env

GRID = [50_000, 60_000, 72_500, 75_000, 100_000, 125_000, 150_000, 192_500, 250_000]
TOLS = [0.005, 0.01, 0.02, 0.03, 0.05]
FROZEN = Path("data/local/frozen-20261004")
FILES = ["yec-hourly.csv", "yec-daily.csv", "spreads-reconstructed.csv", "pool-shares.csv", "depth.csv"]
CURVE_HEADER = ["source", "window", "model", "seed", "budget", "ratio", "member", "p", "p_lock", "shortfall", "n"]


def policy_for(path: str, model: str) -> Policy:
    pol = Policy.load(path)
    pol, _ = pol.with_overrides(MODEL_SETS.get(model, []))
    return pol


def make_env(pol: Policy, window: str, seed: int, budget: str, data_dir: Path) -> Env:
    data, prov, _ = load_data([data_dir / f for f in FILES], parse_window(window))
    return Env(pol, Budget.named(budget), seed, data=data, provenance=prov)  # type: ignore[arg-type]


def append(path: Path, header: list[str], rows: list[list]) -> None:
    new = not path.exists()
    with path.open("a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(header)
        w.writerows(rows)


def cmd_curve(a: argparse.Namespace) -> int:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    base = policy_base(Policy.load(a.policy))
    grid = [int(x) for x in a.grid.split(",")]
    seeds = [int(s) for s in a.seeds.split(",")]
    for model in a.models.split(","):
        pol = policy_for(a.policy, model)
        for window in a.window:
            for si, seed in enumerate(seeds):
                t0 = time.perf_counter()
                env = make_env(pol, window, seed, a.budget, Path(a.data))
                ens = G3.ensemble(env, base)
                b = env.budget
                stride, nt = G3.start_stride_hours(b), G3.terms_per_class(b)
                skip = G3.warmup_hours(base)
                sigma = pol.sigma_mult_at
                rows, trows, srows = [], [], []
                for r in grid:
                    cand = base.replace({"baseRatioBps[0]": r})
                    for m in ens.names:
                        fb = G3.bad_debt(ens, m, cand, 0, sigma=G3.member_sigma(ens, m, sigma, skip),
                                         term_distribution=pol.term_distribution, n_terms=nt, stride=stride)
                        rows.append(["direct", window, model, seed, a.budget, r, m, fb.p["A"], fb.p_lock["A"],
                                     fb.shortfall.get("A", math.nan), fb.n["A"]])
                        if a.sensitivity and si == 0:
                            for T, p in zip(fb.terms["A"].tolist(), fb.by_term["A"].tolist(), strict=True):
                                trows.append([window, model, seed, a.budget, r, m, int(T), p])
                    if a.sensitivity and si == 0:
                        clamp = int(base["sigmaMultMaxBps"])
                        for label, sg in (("median", "median"), ("p90", "p90"), (f"clamp{clamp}", clamp)):
                            for m in ens.names:
                                fb = G3.bad_debt(ens, m, cand, 0, sigma=G3.member_sigma(ens, m, sg, skip),
                                                 term_distribution=pol.term_distribution, n_terms=nt, stride=stride)
                                srows.append([window, model, seed, a.budget, r, label, m, fb.p["A"]])
                append(out / "curve.csv", CURVE_HEADER, rows)
                if trows:
                    append(out / "terms.csv", ["window", "model", "seed", "budget", "ratio", "member", "term_blocks", "p"], trows)
                if srows:
                    append(out / "sigma.csv", ["window", "model", "seed", "budget", "ratio", "sigma", "member", "p"], srows)
                meds = {m: ens.sigma_median(m, skip) for m in G3.hourly_members(ens)}
                print(f"{window} {model} s{seed} {a.budget}: {len(rows)} rows in {time.perf_counter() - t0:.0f} s; "
                      f"members {list(ens.names)}; σ-mult median {meds}", file=sys.stderr, flush=True)
                G3.clear_caches()
    return 0


def cmd_ingest(a: argparse.Namespace) -> int:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for run in sorted(Path(a.robust_dir, "runs").iterdir()):
        if ".partial" in run.name or not (run / "evidence/G3/g3_tradeoff.csv").exists():
            continue
        window, model, seed = run.name.split("__")
        with (run / "evidence/G3/g3_tradeoff.csv").open() as fh:
            rd = csv.DictReader(fh)
            members = [c for c in rd.fieldnames or [] if c not in ("class", "baseRatioBps", "aggregate", "tolerance")]
            for r in rd:
                if r["class"] != "A":
                    continue
                for m in members:
                    rows.append(["robust", window, model, int(seed[1:]), a.budget, int(r["baseRatioBps"]), m,
                                 float(r[m]), "", "", ""])
    append(out / "curve.csv", CURVE_HEADER, rows)
    print(f"ingested {len(rows)} rows from {a.robust_dir}", file=sys.stderr)
    return 0


def _load_curve(d: Path) -> list[dict]:
    with (d / "curve.csv").open() as fh:
        return [r for r in csv.DictReader(fh)]


def pct(x: float) -> str:
    return "—" if x is None or not np.isfinite(x) else f"{x:.2%}"


def ratio_txt(r: int | None) -> str:
    return "none ≤ grid max" if r is None else f"{r:,} ({r / 100:,.0f} %)"


def cmd_tables(a: argparse.Namespace) -> int:
    d = Path(a.data_dir)
    rows = _load_curve(d)
    grid = sorted({int(r["ratio"]) for r in rows})
    want = [int(x) for x in a.grid.split(",")] if a.grid else grid
    # worst-member aggregate per (window, model, budget, seed, ratio)
    agg: dict[tuple, dict[int, float]] = {}
    for r in rows:
        k = (r["window"], r["model"], r["budget"], int(r["seed"]))
        agg.setdefault(k, {})
        ratio = int(r["ratio"])
        agg[k][ratio] = max(agg[k].get(ratio, -1.0), float(r["p"]))
    lines: list[str] = []
    combos = sorted({(k[0], k[1], k[2]) for k in agg}, key=lambda t: (t[1], t[2], t[0]))
    lines.append("## Curve: worst-member P(bad debt at claim opening), class A, per seed\n")
    for window, model, budget in combos:
        seeds = sorted(k[3] for k in agg if k[:3] == (window, model, budget))
        lines.append(f"**{window} · {model} · {budget}** (seeds {', '.join(map(str, seeds))})\n")
        lines.append("| ratio | " + " | ".join(f"s{s}" for s in seeds) + " | max over seeds |")
        lines.append("|---|" + "---|" * (len(seeds) + 1))
        for ratio in want:
            vals = [agg[(window, model, budget, s)].get(ratio) for s in seeds]
            vals = [v for v in vals if v is not None]
            if not vals:
                continue
            lines.append(f"| {ratio:,} ({ratio / 100:,.0f} %) | " + " | ".join(pct(v) for v in vals) + f" | {pct(max(vals))} |")
        lines.append("")
    # inversion
    lines.append("## Need per window: smallest grid ratio within the tolerance in EVERY seed\n")
    need: dict[tuple, dict[float, int | None]] = {}
    for window, model, budget in combos:
        seeds = sorted(k[3] for k in agg if k[:3] == (window, model, budget))
        need[(window, model, budget)] = {}
        for tol in TOLS:
            found = None
            for ratio in grid:
                vals = [agg[(window, model, budget, s)].get(ratio) for s in seeds]
                if all(v is not None and v <= tol for v in vals):
                    found = ratio
                    break
            need[(window, model, budget)][tol] = found
    lines.append("| window · model · budget | " + " | ".join(f"tol {t:.1%}" for t in TOLS) + " |")
    lines.append("|---|" + "---|" * len(TOLS))
    for key in combos:
        lines.append(f"| {' · '.join(key)} | " + " | ".join(ratio_txt(need[key][t]) for t in TOLS) + " |")
    lines.append("")
    lines.append("## The two lock rules (bootstrap model, standard budget)\n")
    lines.append("| tolerance | full history only | worse-window (max of full, last365) | all four windows |")
    lines.append("|---|---|---|---|")
    for tol in TOLS:
        def nd(w: str) -> int | None:
            return need.get((w, "bootstrap", "standard"), {}).get(tol)
        full = nd("full")
        l365 = nd("last365")
        ww = None if (full is None or l365 is None) else max(full, l365)
        others = [nd(w) for w in ("2021-22", "2025-26")]
        allw = None if (ww is None or any(o is None for o in others)) else max(ww, *others)  # type: ignore[type-var]
        lines.append(f"| {tol:.1%} | {ratio_txt(full)} | {ratio_txt(ww)} | {ratio_txt(allw)} |")
    lines.append("")
    # sensitivities
    if (d / "terms.csv").exists():
        with (d / "terms.csv").open() as fh:
            trows = [r for r in csv.DictReader(fh)]
        lines.append("## Term length: worst-member P(bad debt) by grid term (first seed)\n")
        for window, model, budget in sorted({(r["window"], r["model"], r["budget"]) for r in trows}):
            sub = [r for r in trows if (r["window"], r["model"], r["budget"]) == (window, model, budget)]
            terms = sorted({int(r["term_blocks"]) for r in sub})
            pick = [terms[0], terms[len(terms) // 2], terms[-1]]
            lines.append(f"**{window} · {model} · {budget}** — terms {', '.join(f'{t / 1152:.0f} d' for t in pick)} (shortest, median, longest grid term)\n")
            lines.append("| ratio | " + " | ".join(f"{t / 1152:.0f} d" for t in pick) + " |")
            lines.append("|---|" + "---|" * len(pick))
            for ratio in want:
                cells = []
                for t in pick:
                    vs = [float(r["p"]) for r in sub if int(r["ratio"]) == ratio and int(r["term_blocks"]) == t]
                    cells.append(pct(max(vs)) if vs else "—")
                if any(c != "—" for c in cells):
                    lines.append(f"| {ratio:,} | " + " | ".join(cells) + " |")
            lines.append("")
    if (d / "sigma.csv").exists():
        with (d / "sigma.csv").open() as fh:
            srows = [r for r in csv.DictReader(fh)]
        lines.append("## σ multiplier: worst-member P(bad debt) at median / p90 / the clamp (first seed)\n")
        for window, model, budget in sorted({(r["window"], r["model"], r["budget"]) for r in srows}):
            sub = [r for r in srows if (r["window"], r["model"], r["budget"]) == (window, model, budget)]
            labels = sorted({r["sigma"] for r in sub}, key=lambda s: ("median", "p90").index(s) if s in ("median", "p90") else 2)
            lines.append(f"**{window} · {model} · {budget}**\n")
            lines.append("| ratio | " + " | ".join(labels) + " |")
            lines.append("|---|" + "---|" * len(labels))
            for ratio in want:
                cells = []
                for lab in labels:
                    vs = [float(r["p"]) for r in sub if int(r["ratio"]) == ratio and r["sigma"] == lab]
                    cells.append(pct(max(vs)) if vs else "—")
                if any(c != "—" for c in cells):
                    lines.append(f"| {ratio:,} | " + " | ".join(cells) + " |")
            lines.append("")
    text = "\n".join(lines)
    (d / "tables.md").write_text(text)
    print(text)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    c = sp.add_parser("curve")
    c.add_argument("--window", action="append", required=True)
    c.add_argument("--seeds", default="20261003,20261004,20261005")
    c.add_argument("--budget", default="standard")
    c.add_argument("--models", default="bootstrap", help="comma list of bootstrap|regime|martingale")
    c.add_argument("--grid", default=",".join(map(str, GRID)))
    c.add_argument("--policy", default="policy/harden-2026-10.toml")
    c.add_argument("--data", default=str(FROZEN))
    c.add_argument("--out", required=True)
    c.add_argument("--sensitivity", action="store_true", help="also terms.csv / sigma.csv for the first seed")
    c.set_defaults(fn=cmd_curve)
    i = sp.add_parser("ingest")
    i.add_argument("robust_dir")
    i.add_argument("--budget", required=True)
    i.add_argument("--out", required=True)
    i.set_defaults(fn=cmd_ingest)
    t = sp.add_parser("tables")
    t.add_argument("data_dir")
    t.add_argument("--grid", default=",".join(map(str, GRID)))
    t.set_defaults(fn=cmd_tables)
    a = p.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())

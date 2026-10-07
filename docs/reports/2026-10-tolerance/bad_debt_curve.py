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
from ybcal.model import vkernels as V
from ybcal.sim import engine as E
from ybcal.sim import metrics as M
from ybcal.sim import vaults as VB
from ybcal.units import BLOCKS_PER_DAY, BLOCKS_PER_HOUR, BPS, COIN
from ybcal.data.inputs import parse_window
from ybcal.params.paramset import policy_base
from ybcal.report.build import load_data
from ybcal.report.robust import MODEL_SETS
from ybcal.studies import g3_collateral as G3
from ybcal.studies.base import Budget, Env

GRID = [50_000, 60_000, 72_500, 75_000, 100_000, 125_000, 150_000, 192_500, 250_000]
#: the low-ratio extension (LOW-RATIOS.md, owner question of 2026-10-07: "what about 500 %?")
LOW_GRID = [30_000, 35_000, 40_000, 45_000, 50_000, 55_000, 60_000, 65_000, 72_500]
DISCLOSE = [50_000, 60_000, 72_500]
LOSS_HEADER = ["window", "model", "seed", "budget", "ratio", "classmax_days", "member", "term_blocks", "n", "bad",
               "p", "gap_mean", "gap_p50", "gap_p90", "gap_max", "cov_min"]
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


def loss_given_bad_debt(ens, name: str, params, sigma, *, n_terms: int, stride: int, term_distribution: str) -> list[dict]:
    """Per grid term of class A: the counts of :func:`ybcal.sim.metrics.p_bad_debt_fast` (same loop,
    same test, same grace) AND the distribution of the uncovered share ``gap = 1 − value/debt`` over
    the bad samples, which ``p_bad_debt_fast`` only sums into ``shortfall``. ``cov_min`` is the
    worst collateral/debt seen. Validated against :func:`G3.bad_debt`: ``bad/n`` == ``by_term``."""
    from ybcal.sim import agents as AG
    ht, _ = E.prices_from(ens.true[name])
    P, n = ht.shape
    g0 = int(params["grace"])
    rp = VB.RuleParams.of(params)
    skip = G3.warmup_hours(params)
    terms = AG.term_grid(params, 0, n_terms, term_distribution)[0]
    starts = np.arange(skip, n, max(1, int(stride)))
    hs = ens.hours[name]
    gaps: dict[int, list[np.ndarray]] = {ti: [] for ti in range(len(terms))}
    cnt = {ti: [0, 0] for ti in range(len(terms))}
    for a0 in range(0, P, 32):
        sl = slice(a0, min(P, a0 + 32))
        pm, sg, hm = hs.p_mint[sl], hs.sigma_mult_bps[sl], hs.halt_mask[sl]
        sgc = M._sigma_choice(np.asarray(sg, dtype=np.int64), sigma, skip)
        tp = ht[sl]
        pms = pm[:, starts]
        okm = (pms > 0) & ((hm[:, starts] & (E.HALT_NO_PRICE | E.HALT_DIVERGENCE)) == 0)
        coll = VB.wallet_collateral(rp, np.full(pms.shape, G3.TEST_CENTS), np.zeros(pms.shape, dtype=np.int64),
                                    sgc[:, starts], np.where(okm, pms, 1))
        okc = okm & (coll != VB.UNDEF)
        for ti, T in enumerate(terms.tolist()):
            e = starts + -(-(T + g0 + 1) // BLOCKS_PER_HOUR)
            inside = e < n
            if not inside.any():
                continue
            cols = np.nonzero(inside)[0]
            tpe = tp[:, e[cols]]
            good = okc[:, cols]
            bad = V.is_underwater(np.where(good, coll[:, cols], 0), tpe, G3.TEST_CENTS, BPS) & good
            cnt[ti][0] += int(bad.sum())
            cnt[ti][1] += int(good.sum())
            if bad.any():
                val = coll[:, cols][bad].astype(np.float64) * tpe[bad].astype(np.float64) / COIN
                gaps[ti].append(np.maximum(0.0, 1.0 - val / (G3.TEST_CENTS * 1e4)))
    out = []
    for ti, T in enumerate(terms.tolist()):
        b, k = cnt[ti]
        g = np.concatenate(gaps[ti]) if gaps[ti] else np.zeros(0)
        out.append({"term_blocks": int(T), "n": k, "bad": b, "p": b / k if k else math.nan,
                    "gap_mean": float(g.mean()) if g.size else math.nan,
                    "gap_p50": float(np.percentile(g, 50)) if g.size else math.nan,
                    "gap_p90": float(np.percentile(g, 90)) if g.size else math.nan,
                    "gap_max": float(g.max()) if g.size else math.nan,
                    "cov_min": float(1 - g.max()) if g.size else math.nan, "gaps": g})
    return out


def cmd_loss(a: argparse.Namespace) -> int:
    """At the disclosure ratios: loss given bad debt per term and, with ``--classmax-days``, class A's
    P(bad debt) when its longest term is capped (the term-limit question), for every seed."""
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    base = policy_base(Policy.load(a.policy))
    grid = [int(x) for x in a.grid.split(",")]
    seeds = [int(s) for s in a.seeds.split(",")]
    caps = [int(x) for x in a.classmax_days.split(",")] if a.classmax_days else []
    for model in a.models.split(","):
        pol = policy_for(a.policy, model)
        for window in a.window:
            for seed in seeds:
                t0 = time.perf_counter()
                env = make_env(pol, window, seed, a.budget, Path(a.data))
                ens = G3.ensemble(env, base)
                b = env.budget
                stride, nt = G3.start_stride_hours(b), G3.terms_per_class(b)
                skip = G3.warmup_hours(base)
                rows, hrows = [], []
                for r in grid:
                    for cap in [None, *caps]:
                        ch = {"baseRatioBps[0]": r}
                        if cap is not None:
                            ch["classMax[0]"] = cap * BLOCKS_PER_DAY
                        cand = base.replace(ch)
                        capd = cap if cap is not None else int(base["classMax[0]"]) // BLOCKS_PER_DAY
                        for m in ens.names:
                            sg = G3.member_sigma(ens, m, pol.sigma_mult_at, skip)
                            fb = G3.bad_debt(ens, m, cand, 0, sigma=sg, term_distribution=pol.term_distribution,
                                             n_terms=nt, stride=stride)
                            if cap is None:
                                per = loss_given_bad_debt(ens, m, cand, sg, n_terms=nt, stride=stride,
                                                          term_distribution=pol.term_distribution)
                                d = max(abs(x["p"] - y) for x, y in zip(per, fb.by_term["A"].tolist(), strict=True))
                                if not (d == 0 or d < 1e-12):
                                    raise SystemExit(f"loss replication differs from G3.bad_debt: {d}")
                                for x in per:
                                    rows.append([window, model, seed, a.budget, r, capd, m] + [x[k] for k in LOSS_HEADER[7:]])
                                allg = np.concatenate([x["gaps"] for x in per]) if any(x["gaps"].size for x in per) else np.zeros(0)
                                if allg.size:
                                    hist, edges = np.histogram(allg, bins=[0, .05, .1, .15, .2, .25, .3, .4, .5, .6, .8, 1.0])
                                    for lo, hi, c in zip(edges[:-1], edges[1:], hist, strict=True):
                                        hrows.append([window, model, seed, a.budget, r, m, lo, hi, int(c), allg.size])
                            # the class value under the cap (uniform over classMin..cap, nt terms) — same scorer as the lock
                            rows.append([window, model, seed, a.budget, r, capd, m, 0, fb.n["A"], "", fb.p["A"],
                                         fb.shortfall.get("A", math.nan), "", "", "", ""])
                append(out / "loss.csv", LOSS_HEADER, rows)
                if hrows:
                    append(out / "loss_hist.csv", ["window", "model", "seed", "budget", "ratio", "member", "gap_lo", "gap_hi", "count", "total"], hrows)
                print(f"{window} {model} s{seed}: {len(rows)} loss rows in {time.perf_counter() - t0:.0f} s", file=sys.stderr, flush=True)
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


def _worst(rows: list[dict], key, val="p") -> dict[tuple, float]:
    agg: dict[tuple, float] = {}
    for r in rows:
        k = key(r)
        agg[k] = max(agg.get(k, -1.0), float(r[val]))
    return agg


def cmd_disclosure(a: argparse.Namespace) -> int:
    """LOW-RATIOS.md's tables from ``curve.csv`` (low grid), ``loss.csv`` and ``loss_hist.csv``."""
    d = Path(a.data_dir)
    windows = ["full", "last365", "2021-22", "2025-26"]
    curve = [r for r in _load_curve(d) if r["budget"] == "standard"]
    grid = sorted({int(r["ratio"]) for r in curve})
    L: list[str] = []
    for model in ("bootstrap", "regime"):
        sub = [r for r in curve if r["model"] == model]
        if not sub:
            continue
        ws = _worst(sub, lambda r: (r["window"], int(r["seed"]), int(r["ratio"])))
        seeds = {w: sorted({k[1] for k in ws if k[0] == w}) for w in windows}
        L.append(f"### {model}: worst-member P(bad debt at claim opening), class A — max over seeds (seed min–max)\n")
        L.append("| `baseRatioBps[0]` | " + " | ".join(f"{w} ({len(seeds[w])} seeds)" for w in windows if seeds[w]) + " |")
        L.append("|---|" + "---|" * len([w for w in windows if seeds[w]]))
        for r in grid:
            cells = []
            for w in windows:
                if not seeds[w]:
                    continue
                v = [ws[(w, s, r)] for s in seeds[w] if (w, s, r) in ws]
                cells.append(f"**{max(v):.2%}** ({min(v):.2%}–{max(v):.2%})" if v else "—")
            L.append(f"| {r:,} ({r / 100:,.0f} %) | " + " | ".join(cells) + " |")
        L.append("")
        # need per window at each tolerance, and the tolerance each ratio passes
        L.append(f"### {model}: smallest grid ratio within the tolerance in every seed\n")
        tols = [0.01, 0.02, 0.03, 0.05, 0.075, 0.10]
        L.append("| window | " + " | ".join(f"{t:.1%}" for t in tols) + " |")
        L.append("|---|" + "---|" * len(tols))
        for w in windows:
            if not seeds[w]:
                continue
            cells = []
            for t in tols:
                found = next((r for r in grid if all(ws.get((w, s, r), 9) <= t for s in seeds[w])), None)
                cells.append("none ≤ 725 %" if found is None else f"{found / 100:,.0f} %")
            L.append(f"| {w} | " + " | ".join(cells) + " |")
        L.append("")
        L.append(f"### {model}: the tolerance each disclosure ratio passes (max over seeds, rounded up to the next ½ %)\n")
        L.append("| ratio | " + " | ".join(w for w in windows if seeds[w]) + " |")
        L.append("|---|" + "---|" * len([w for w in windows if seeds[w]]))
        for r in DISCLOSE:
            cells = []
            for w in windows:
                if not seeds[w]:
                    continue
                v = max(ws.get((w, s, r), math.nan) for s in seeds[w])
                cells.append(f"{v:.2%} → passes {math.ceil(v * 200) / 200:.1%}")
            L.append(f"| {r / 100:,.0f} % | " + " | ".join(cells) + " |")
        L.append("")
    # disclosure: per window and term, from loss.csv (all seeds)
    if (d / "loss.csv").exists():
        with (d / "loss.csv").open() as fh:
            loss = [r for r in csv.DictReader(fh) if r["model"] == "bootstrap"]
        base_cap = max(int(r["classmax_days"]) for r in loss)
        per_term = [r for r in loss if int(r["term_blocks"]) > 0]
        cls = [r for r in loss if int(r["term_blocks"]) == 0]
        L.append("## Disclosure table (bootstrap, standard): P(bad debt) per term with seed spread, loss given bad debt, YED per USD locked\n")
        L.append("Term rows are the shortest, median and longest of the 16 equal-probability grid terms of class A "
                 "(≈ 32, 62 and 88 days); 'class' is the term-averaged value the lock rule scores. P = worst ensemble member, "
                 "max over 3 seeds (min–max in parentheses). Loss given bad debt = uncovered share of the debt "
                 "1 − collateral/debt at claim opening over the bad samples of that member, pooled over seeds: mean, median, "
                 "p90, worst. YED per USD locked = 100 / ratio at σ multiplier ×1.0 (the median of the hourly history, which "
                 "is what the policy sizes at); round-trip fee share scales linearly from H4's 2.7 % at 725 %.\n")
        for r0 in DISCLOSE:
            L.append(f"### {r0 / 100:,.0f} % — {10_000 / r0:.3f} YED per USD locked ({r0 / 100:,.0f} USD of YEC per 100 YED); "
                     f"round-trip fee share ≈ {2.7 * r0 / 72_500:.1f} %\n")
            L.append("| window | term | P(bad debt) max (min–max over seeds) | loss given bad: mean | median | p90 | worst | n bad / n |")
            L.append("|---|---|---|---|---|---|---|---|")
            for w in windows:
                # worst member = the member with the highest class p (max over seeds)
                cw = [r for r in cls if r["window"] == w and int(r["ratio"]) == r0 and int(r["classmax_days"]) == base_cap]
                if not cw:
                    continue
                members = sorted({r["member"] for r in cw})
                mp = {m: max(float(r["p"]) for r in cw if r["member"] == m) for m in members}
                wm = max(members, key=lambda m: mp[m])
                pt = [r for r in per_term if r["window"] == w and int(r["ratio"]) == r0 and r["member"] == wm]
                terms = sorted({int(r["term_blocks"]) for r in pt})
                picks = [("≈ 30 d", terms[0]), ("≈ 60 d", terms[len(terms) // 2]), ("≈ 90 d", terms[-1]), ("class 30–90 d", 0)]
                for label, T in picks:
                    if T:
                        rows = [r for r in pt if int(r["term_blocks"]) == T]
                        ps = [float(r["p"]) for r in rows]
                        nb = sum(int(r["bad"]) for r in rows)
                        nn = sum(int(r["n"]) for r in rows)
                        wts = np.array([int(r["bad"]) for r in rows], dtype=float)
                        def wmean(k):
                            v = np.array([float(r[k]) if r[k] not in ("", "nan") else 0.0 for r in rows])
                            return float((v * wts).sum() / wts.sum()) if wts.sum() else math.nan
                        gmax = max((float(r["gap_max"]) for r in rows if r["gap_max"] not in ("", "nan")), default=math.nan)
                        L.append(f"| {w} ({wm}) | {label} ({T / BLOCKS_PER_DAY:.0f} d) | **{max(ps):.2%}** ({min(ps):.2%}–{max(ps):.2%}) | "
                                 f"{pct(wmean('gap_mean'))} | {pct(wmean('gap_p50'))} | {pct(wmean('gap_p90'))} | {pct(gmax)} | {nb:,} / {nn:,} |")
                    else:
                        rows = [r for r in cw if r["member"] == wm]
                        ps = [float(r["p"]) for r in rows]
                        sh = [float(r["gap_mean"]) for r in rows]  # class shortfall (unconditional mean gap) sits in gap_mean
                        egl = max(sh) / max(ps) if max(ps) > 0 else math.nan
                        L.append(f"| {w} ({wm}) | {label} | **{max(ps):.2%}** ({min(ps):.2%}–{max(ps):.2%}) | ≈ {pct(egl)} (E[shortfall]/P) | | | | |")
            L.append("")
        if (d / "loss_hist.csv").exists():
            with (d / "loss_hist.csv").open() as fh:
                H = [r for r in csv.DictReader(fh)]
            L.append("### How far under water: distribution of 1 − collateral/debt over bad samples (all terms, all seeds, worst member)\n")
            bins = sorted({(float(r["gap_lo"]), float(r["gap_hi"])) for r in H})
            L.append("| ratio · window | " + " | ".join(f"{lo:.0%}–{hi:.0%}" for lo, hi in bins) + " | n bad |")
            L.append("|---|" + "---|" * (len(bins) + 1))
            for r0 in DISCLOSE:
                for w in windows:
                    cw = [r for r in cls if r["window"] == w and int(r["ratio"]) == r0 and int(r["classmax_days"]) == base_cap]
                    if not cw:
                        continue
                    members = sorted({r["member"] for r in cw})
                    wm = max(members, key=lambda m: max(float(r["p"]) for r in cw if r["member"] == m))
                    hr = [r for r in H if r["window"] == w and int(r["ratio"]) == r0 and r["member"] == wm]
                    tot = sum(int(r["count"]) for r in hr)
                    cells = []
                    for lo, hi in bins:
                        c = sum(int(r["count"]) for r in hr if float(r["gap_lo"]) == lo)
                        cells.append(f"{c / tot:.0%}" if tot else "—")
                    L.append(f"| {r0 / 100:,.0f} % · {w} ({wm}) | " + " | ".join(cells) + f" | {tot:,} |")
            L.append("")
        # term caps
        caps = sorted({int(r["classmax_days"]) for r in cls})
        L.append("## Term limit: class A P(bad debt) when classMax[0] is capped (worst member, max over 3 seeds; bootstrap, standard)\n")
        L.append("| ratio · window | " + " | ".join(f"30–{c} d" for c in caps) + " |")
        L.append("|---|" + "---|" * len(caps))
        for r0 in DISCLOSE:
            for w in windows:
                cells = []
                for c in caps:
                    v = [float(r["p"]) for r in cls if r["window"] == w and int(r["ratio"]) == r0 and int(r["classmax_days"]) == c]
                    cells.append(pct(max(v)) if v else "—")
                if any(x != "—" for x in cells):
                    L.append(f"| {r0 / 100:,.0f} % · {w} | " + " | ".join(cells) + " |")
        L.append("")
    text = "\n".join(L)
    (d / "low-tables.md").write_text(text)
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
    lo = sp.add_parser("loss")
    lo.add_argument("--window", action="append", required=True)
    lo.add_argument("--seeds", default="20261003,20261004,20261005")
    lo.add_argument("--budget", default="standard")
    lo.add_argument("--models", default="bootstrap")
    lo.add_argument("--grid", default=",".join(map(str, DISCLOSE)))
    lo.add_argument("--classmax-days", default="30,45,60,75", help="class A term caps to score (days); '' for none")
    lo.add_argument("--policy", default="policy/harden-2026-10.toml")
    lo.add_argument("--data", default=str(FROZEN))
    lo.add_argument("--out", required=True)
    lo.set_defaults(fn=cmd_loss)
    i = sp.add_parser("ingest")
    i.add_argument("robust_dir")
    i.add_argument("--budget", required=True)
    i.add_argument("--out", required=True)
    i.set_defaults(fn=cmd_ingest)
    dz = sp.add_parser("disclosure")
    dz.add_argument("data_dir")
    dz.set_defaults(fn=cmd_disclosure)
    t = sp.add_parser("tables")
    t.add_argument("data_dir")
    t.add_argument("--grid", default=",".join(map(str, GRID)))
    t.set_defaults(fn=cmd_tables)
    a = p.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())

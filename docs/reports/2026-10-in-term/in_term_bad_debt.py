#!/usr/bin/env python
"""P(bad debt) under the in-term claim path (in-term claims plan, T5) — computed DIRECTLY over the
plan's classes and a ratio grid with :func:`ybcal.studies.g3_collateral.bad_debt`, exactly as
``G3Study.evaluate`` scores ``bad_debt_<class>`` (worst member, σ multiplier at the policy's
``sigma_mult_at``; at ``sigmaRefBps = 0`` the multiplier is 1), in both claim-path modes:

* ``post-term`` — today's rule and every earlier number: the vault is tested once, when the claim path
  opens at ``lockHeight + grace``;
* ``in-term`` — the plan's rule (D-IT-1): anyone may claim at the first hour where
  ``collateral < θ × debt`` at pClaim (θ = ``claimThresholdBps``), the claim completes
  ``claim_delay_blocks`` later (capped at the post-term opening), bad iff collateral < debt at the
  true price then.

    PY=../../yb-calibration/.venv/bin/python; export PYTHONPATH=src; T=docs/reports/2026-10-in-term
    $PY $T/in_term_bad_debt.py curve --window full --window last365 --seeds 20261003,20261004,20261005 \
        --budget standard --models bootstrap --sensitivity --data $S --out $T/data/a
    $PY $T/in_term_bad_debt.py sys  --window full ... --out $T/data/sys
    $PY $T/in_term_bad_debt.py fee  --data $S --out $T/data/fee
    $PY $T/in_term_bad_debt.py zec  --zec-dir ../calib-study/data/local/zec --data $S --out $T/data/zec
    $PY $T/in_term_bad_debt.py tables $T/data      # after merging the curve.csv files into $T/data/

``curve`` writes ``curve.csv`` (window, model, seed, budget, class, ratio, mode, theta, delay, member,
p, p_lock, shortfall, in_term, n, terms, terms_censored, gap_*). ``sys`` scores the HALT-2 / recap
questions of the plan (``halt_sys_bad`` at 200 %, the θ-level fall, the vault model at the halt ratio).
``fee`` gives the FEE-1 round-trip share of a ``minMint`` vault per class. ``zec`` validates the in-term
mode against the ZEC study's lever harness (``zec_level_shift.lever_study``) on the same inputs.
``tables`` renders everything in the out directory to ``tables.md``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import math
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

from ybcal.config import Policy
from ybcal.data.inputs import parse_window
from ybcal.params.paramset import policy_base
from ybcal.report.build import load_data
from ybcal.report.robust import MODEL_SETS
from ybcal.sim import engine as E
from ybcal.sim import fees as F
from ybcal.sim import metrics as M
from ybcal.studies import g1_price_windows as G1
from ybcal.studies import g3_collateral as G3
from ybcal.studies import g6_miners_fees as G6
from ybcal.studies import g7_supply_halts as G7
from ybcal.studies import vol_price_level as VPL
from ybcal.studies.base import Budget, Env
from ybcal.units import BLOCKS_PER_DAY, BLOCKS_PER_HOUR, BPS

CLASSES = ("A", "B", "C")
TOLS = [0.01, 0.02, 0.05]
DELAYS = [288, 576, 1152, 2304]  # 6 h, 12 h, 24 h, 48 h
THETAS = [11_500, 12_500, 15_000]
GRID = [15_000, 20_000, 25_000, 30_000, 35_000, 40_000, 45_000, 50_000, 60_000, 72_500]
FILES = ["yec-hourly.csv", "yec-daily.csv", "spreads-reconstructed.csv", "pool-shares.csv", "depth.csv"]
CURVE_HEADER = ["window", "model", "seed", "budget", "class", "ratio", "mode", "theta", "delay", "member",
                "p", "p_lock", "shortfall", "in_term", "n", "terms", "terms_censored",
                "gap_mean", "gap_p50", "gap_p90", "gap_max"]
SYS_HEADER = ["window", "model", "seed", "budget", "halt_bps", "theta", "grace", "p_sys_bad", "p_sys_bad_real",
              "tolerance", "halt_sys_bad_pass", "p_fall_to_theta", "p_fall_to_theta_real", "recap_bps",
              "recap_classes", "halt_below_floor", "class", "member", "p_vault_at_halt_in_term",
              "p_vault_at_halt_post", "in_term_share"]
ZEC_HEADER = ["series", "term_days", "ratio", "mode", "theta", "delay", "harness", "ideal", "kernel",
              "n_harness", "n_ideal", "n_kernel"]


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


def _gap_stats(g: np.ndarray) -> list[float]:
    if g.size == 0:
        return [math.nan] * 4
    return [float(g.mean()), float(np.percentile(g, 50)), float(np.percentile(g, 90)), float(g.max())]


def _row(window, model, seed, budget, name, r, mode, theta, delay, m, fb) -> list:
    bt = np.asarray(fb.by_term[name], dtype=float)
    return [window, model, seed, budget, name, r, mode, theta, delay, m, fb.p[name], fb.p_lock[name],
            fb.shortfall.get(name, math.nan), fb.in_term.get(name, 0.0), fb.n[name], int(bt.size),
            int(np.isnan(bt).sum()), *_gap_stats(fb.gaps.get(name, np.zeros(0)))]


def cmd_curve(a: argparse.Namespace) -> int:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pol0 = Policy.load(a.policy)
    base = policy_base(pol0)
    theta0, delay0 = int(base["claimThresholdBps"]), int(pol0.claim_delay_blocks)
    grid = [int(x) for x in a.grid.split(",")] if a.grid else []
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
                rows: list[list] = []

                def score(cand, c, mode, delay, theta, r):
                    for m in ens.names:
                        fb = G3.bad_debt(ens, m, cand, c, sigma=G3.member_sigma(ens, m, sigma, skip),
                                         term_distribution=pol.term_distribution, n_terms=nt, stride=stride,
                                         claim_path=mode, claim_delay_blocks=delay, keep_gaps=True)
                        rows.append(_row(window, model, seed, a.budget, CLASSES[c], r, mode, theta, delay, m, fb))

                for c in range(3):
                    plan = int(base[f"baseRatioBps[{c}]"])
                    for r in sorted({plan, *grid}):
                        cand = base.replace({f"baseRatioBps[{c}]": r})
                        score(cand, c, "post-term", 0, theta0, r)
                        score(cand, c, "in-term", delay0, theta0, r)
                    if a.sensitivity:
                        for d in DELAYS:
                            if d != delay0:
                                score(base, c, "in-term", d, theta0, plan)
                        for th in THETAS:
                            if th != theta0:
                                score(base.replace({"claimThresholdBps": th}), c, "in-term", delay0, th, plan)
                append(out / "curve.csv", CURVE_HEADER, rows)
                print(f"{window} {model} s{seed} {a.budget}: {len(rows)} rows in {time.perf_counter() - t0:.0f} s; "
                      f"members {list(ens.names)}; hours {ens.meta.get('years')} y", file=sys.stderr, flush=True)
                G3.clear_caches()
    return 0


def cmd_sys(a: argparse.Namespace) -> int:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pol0 = Policy.load(a.policy)
    base = policy_base(pol0)
    h, theta, grace, recap = (int(base["globalRatioHaltBps"]), int(base["claimThresholdBps"]),
                              int(base["grace"]), int(base["recapRatioBps"]))
    seeds = [int(s) for s in a.seeds.split(",")]
    for model in a.models.split(","):
        pol = policy_for(a.policy, model)
        for window in a.window:
            for seed in seeds:
                t0 = time.perf_counter()
                env = make_env(pol, window, seed, a.budget, Path(a.data))
                p_sys = G7.sys_bad_prob(env, h, grace)
                tol = G7.system_tolerance(pol, base)
                real = G1.real_price(env)
                p_real = G7.real_sys_bad_prob(real, h, grace) if real is not None else math.nan
                # the price fall that turns a book sitting at the halt ratio into claimable vaults (θ level)
                h_theta = round(h * BPS / theta)
                p_fall = G7.sys_bad_prob(env, h_theta, grace)
                p_fall_real = G7.real_sys_bad_prob(real, h_theta, grace) if real is not None else math.nan
                from ybcal.params.classes import enabled_classes
                on = enabled_classes(base)
                recap_classes = sum(int(base[f"baseRatioBps[{i}]"]) >= recap for i in on)
                below = h < min(int(base[f"baseRatioBps[{i}]"]) for i in on)
                # the vault model: a vault minted at the halt ratio (the book's state when HALT-2 fires)
                ens = G3.ensemble(env, base)
                b = env.budget
                stride, nt = G3.start_stride_hours(b), G3.terms_per_class(b)
                skip = G3.warmup_hours(base)
                rows = []
                for c in range(3):
                    cand = base.replace({f"baseRatioBps[{c}]": h})
                    for m in ens.names:
                        sg = G3.member_sigma(ens, m, pol.sigma_mult_at, skip)
                        it = G3.bad_debt(ens, m, cand, c, sigma=sg, term_distribution=pol.term_distribution,
                                         n_terms=nt, stride=stride, claim_path="in-term",
                                         claim_delay_blocks=int(pol0.claim_delay_blocks))
                        po = G3.bad_debt(ens, m, cand, c, sigma=sg, term_distribution=pol.term_distribution,
                                         n_terms=nt, stride=stride)
                        rows.append([window, model, seed, a.budget, h, theta, grace, p_sys, p_real, tol,
                                     p_sys <= tol, p_fall, p_fall_real, recap, recap_classes, below, CLASSES[c], m,
                                     it.p[CLASSES[c]], po.p[CLASSES[c]], it.in_term.get(CLASSES[c], math.nan)])
                append(out / "sys.csv", SYS_HEADER, rows)
                print(f"{window} {model} s{seed}: p_sys_bad {p_sys:.4f} (real {p_real:.4f}) tol {tol:.4f} "
                      f"fall-to-θ {p_fall:.4f} in {time.perf_counter() - t0:.0f} s", file=sys.stderr, flush=True)
                G3.clear_caches()
    return 0


def cmd_fee(a: argparse.Namespace) -> int:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pol = Policy.load(a.policy)
    base = policy_base(pol)
    env = make_env(pol, "full", pol.seed, "quick", Path(a.data))
    p_ref = G6.reference_price(env)
    rows = []
    for c in range(3):
        for r in (30_000, 40_000, 50_000, 60_000, 72_500):
            cand = base.replace({f"baseRatioBps[{c}]": r})
            for cents in (int(base["minMint"]), 100_000):
                t = F.fee_table(cand, [cents], p_ref, term_class=c, armed=True)[0]
                rows.append([CLASSES[c], r, cents, p_ref, t["collateral_zat"], t["mint_fee_zat"], t["redeem_fee_zat"],
                             t["round_trip_fee_usd"], t["fee_share_of_debt"]])
    append(out / "fee.csv", ["class", "ratio", "cents", "p_ref_microusd", "collateral_zat", "mint_fee_zat",
                             "redeem_fee_zat", "round_trip_fee_usd", "fee_share_of_debt"], rows)
    for r in rows:
        print(r)
    return 0


# ---------------------------------------------------------------------------------------------------
# ZEC validation against the lever harness


def _check_sums(d: Path, names: list[str]) -> None:
    want = {}
    for line in (d / "SHA256SUMS").read_text().splitlines():
        h, _, name = line.partition("  ")
        want[name.strip()] = h.strip()
    bad = [n for n in names if hashlib.sha256((d / n).read_bytes()).hexdigest() != want.get(n)]
    if bad:
        raise SystemExit(f"SHA256 mismatch in {d}: {bad}")


def _harness(price: np.ndarray, T_d: int, grace_d: int, R: float, theta: float, d_h: int, skip: int,
             stride: int) -> tuple[float, int]:
    """``zec_level_shift.lever_study``'s inner loop for one (term, ratio, θ, delay), chunked over starts."""
    H = (T_d + grace_d) * 24
    n = price.size
    starts = np.arange(skip, n - H, stride)
    bad_n = 0
    for a0 in range(0, starts.size, 1500):
        st = starts[a0:a0 + 1500]
        idx = st[:, None] + np.arange(1, H + 1)[None, :]
        X = (price[st][:, None] / price[idx]).astype(np.float32)
        X_end = X[:, -1]
        if theta <= 1.0:
            bad = X_end > R
        else:
            cross = (R / theta) < X
            has = cross.any(axis=1)
            u = np.argmax(cross, axis=1)
            at = np.minimum(u + d_h, H - 1)
            x_liq = X[np.arange(X.shape[0]), at]
            bad = np.where(has, x_liq > R, X_end > R)
        bad_n += int(bad.sum())
    return bad_n / max(starts.size, 1), int(starts.size)


def _fake_hours(price_mu: np.ndarray) -> E.HourSeries:
    p = price_mu[None, :]
    return E.HourSeries(p, p, p, p, p, np.full(p.shape, BPS, dtype=np.int64), np.zeros(p.shape, dtype=np.int64))


def cmd_zec(a: argparse.Namespace) -> int:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pol = Policy.load(a.policy)
    base = policy_base(pol)
    zec_dir, yec_dir = Path(a.zec_dir), Path(a.data)
    _check_sums(zec_dir, ["zec-hourly-cmc.csv"])
    _check_sums(yec_dir, ["yec-hourly.csv"])
    series = {"ZEC": VPL.load(zec_dir / "zec-hourly-cmc.csv", 3_600), "YEC": VPL.load(yec_dir / "yec-hourly.csv", 3_600)}
    grace = int(base["grace"])
    grace_d = grace // BLOCKS_PER_DAY
    theta_bps, delay = int(base["claimThresholdBps"]), int(pol.claim_delay_blocks)
    d_h = -(-delay // BLOCKS_PER_HOUR)
    skip, stride = 24 * 400, 6
    rows = []
    for who, s in series.items():
        price = np.asarray(s.price, dtype=np.float64)
        mu = np.round(price * 1e6).astype(np.int64)
        for T_d in (30, 60, 90):
            T = T_d * BLOCKS_PER_DAY
            ps = base.replace({"classMin[0]": T, "classMax[0]": T})
            kern = E.OracleTransferKernel.ideal(ps, substeps=G3.HOUR_SUBSTEPS)
            hs = E.simulate_hours(ps, mu[None, :], kern)
            fake = _fake_hours(mu)
            for R in (30_000, 40_000, 50_000):
                cand = ps.replace({"baseRatioBps[0]": R})
                for mode, th, d in (("post-term", 10_000, 0), ("in-term", theta_bps, delay)):
                    hp, nh = _harness(price, T_d, grace_d, R / BPS, th / BPS if mode == "in-term" else 1.0, d_h,
                                      skip, stride)
                    ideal = M.p_bad_debt_fast(cand, mu[None, :], hour_series=fake, classes=(0,), n_terms=1,
                                              sigma=BPS, start_stride=stride, skip_hours=skip, claim_path=mode,
                                              claim_delay_blocks=d)
                    kernel = M.p_bad_debt_fast(cand, mu[None, :], hour_series=hs, classes=(0,), n_terms=1,
                                               sigma="median", start_stride=stride, skip_hours=skip,
                                               claim_path=mode, claim_delay_blocks=d)
                    rows.append([who, T_d, R, mode, th, d, hp, ideal.p["A"], kernel.p["A"], nh, ideal.n["A"],
                                 kernel.n["A"]])
                    print(rows[-1], file=sys.stderr, flush=True)
    append(out / "zec_validation.csv", ZEC_HEADER, rows)
    return 0


# ---------------------------------------------------------------------------------------------------
# tables


def _load(path: Path) -> list[dict]:
    with path.open() as fh:
        return list(csv.DictReader(fh))


def pct(x: float) -> str:
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{100 * x:.2f} %"


def _f(r: dict, k: str) -> float:
    v = r.get(k, "")
    try:
        return float(v)
    except (TypeError, ValueError):
        return math.nan


def worst_by_seed(rows: list[dict], key_fn) -> dict[tuple, dict[int, float]]:
    """``key → {seed: worst-member p}`` (nan members ignored; all-nan → nan)."""
    acc: dict[tuple, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        acc[key_fn(r)][int(r["seed"])].append(_f(r, "p"))
    out: dict[tuple, dict[int, float]] = {}
    for k, per in acc.items():
        out[k] = {s: (float(np.nanmax(v)) if np.isfinite(v).any() else math.nan) for s, v in per.items()}
    return out


def fmt_seeds(d: dict[int, float]) -> str:
    v = [x for x in d.values() if np.isfinite(x)]
    if not v:
        return "—"
    return f"{100 * max(v):.2f} % ({100 * min(v):.2f}–{100 * max(v):.2f})" if len(v) > 1 else f"{100 * max(v):.2f} %"


def cmd_tables(a: argparse.Namespace) -> int:
    d = Path(a.dir)
    curve = _load(d / "curve.csv")
    windows = list(dict.fromkeys(r["window"] for r in curve))
    pol = Policy.load(a.policy)
    base = policy_base(pol)
    plan = {CLASSES[c]: int(base[f"baseRatioBps[{c}]"]) for c in range(3)}
    theta0, delay0 = str(int(base["claimThresholdBps"])), str(int(pol.claim_delay_blocks))
    lines = [f"# In-term claims: tables generated from `{d}` (worst ensemble member, max over seeds; seed min–max)\n"]
    boot = [r for r in curve if r["model"] == "bootstrap"]
    regime = [r for r in curve if r["model"] == "regime"]

    def plan_rows(rows, mode, theta=theta0, delay=delay0):
        return [r for r in rows if r["mode"] == mode and int(r["ratio"]) == plan[r["class"]]
                and r["theta"] == theta and r["delay"] == (delay if mode == "in-term" else "0")]

    # 1. the plan's classes, post-term vs in-term
    lines.append("## 1. The plan's classes at their ratios: P(bad debt), post-term rule vs in-term claims (bootstrap)\n")
    lines.append("| class · ratio | window | post-term (today) | in-term (θ 125 %, 24 h) | claimed in term | 1 % | 2 % | 5 % |")
    lines.append("|---|---|---|---|---|---|---|---|")
    wp = worst_by_seed(plan_rows(boot, "post-term"), lambda r: (r["class"], r["window"]))
    wi = worst_by_seed(plan_rows(boot, "in-term"), lambda r: (r["class"], r["window"]))
    its: dict[tuple, list[float]] = defaultdict(list)
    for r in plan_rows(boot, "in-term"):
        its[(r["class"], r["window"])].append(_f(r, "in_term"))
    for cls in CLASSES:
        for w in windows:
            k = (cls, w)
            if k not in wi:
                continue
            v = [x for x in wi[k].values() if np.isfinite(x)]
            worst = max(v) if v else math.nan
            marks = ["✓" if np.isfinite(worst) and worst <= t else ("✗" if np.isfinite(worst) else "—") for t in TOLS]
            share = float(np.nanmean(its[k])) if its[k] else math.nan
            lines.append(f"| {cls} · {plan[cls] // 100} % | {w} | {fmt_seeds(wp.get(k, {}))} | **{fmt_seeds(wi[k])}** | "
                         f"{pct(share)} | {' | '.join(marks)} |")
    # 2. loss given bad debt (worst seed, worst member rows at the plan ratio, in-term)
    lines.append("\n## 2. Loss given bad debt at the plan's ratios (in-term; the worst member's bad samples, seed with the highest P)\n")
    lines.append("| class | window | P(bad) | mean gap | median | p90 | worst | post-term mean gap |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for cls in CLASSES:
        for w in windows:
            cand = [r for r in plan_rows(boot, "in-term") if r["class"] == cls and r["window"] == w and np.isfinite(_f(r, "p"))]
            if not cand:
                continue
            r = max(cand, key=lambda r: _f(r, "p"))
            po = [x for x in plan_rows(boot, "post-term") if x["class"] == cls and x["window"] == w and x["seed"] == r["seed"] and x["member"] == r["member"]]
            pg = _f(po[0], "gap_mean") if po else math.nan
            lines.append(f"| {cls} | {w} ({r['member']}) | {pct(_f(r, 'p'))} | {pct(_f(r, 'gap_mean'))} | {pct(_f(r, 'gap_p50'))} | "
                         f"{pct(_f(r, 'gap_p90'))} | {pct(_f(r, 'gap_max'))} | {pct(pg)} |")
    # 3. delay sensitivity
    lines.append("\n## 3. Sensitivity to the cancel delay (in-term, θ 125 %, plan ratios; bootstrap)\n")
    lines.append("| class | window | " + " | ".join(f"{dl // 48} h" for dl in DELAYS) + " | post-term |")
    lines.append("|---|---|" + "---|" * (len(DELAYS) + 1))
    for cls in CLASSES:
        for w in windows:
            cells = []
            for dl in DELAYS:
                wd = worst_by_seed(plan_rows(boot, "in-term", theta0, str(dl)), lambda r: (r["class"], r["window"]))
                cells.append(fmt_seeds(wd.get((cls, w), {})))
            if all(c == "—" for c in cells):
                continue
            lines.append(f"| {cls} | {w} | " + " | ".join(cells) + f" | {fmt_seeds(wp.get((cls, w), {}))} |")
    # 3b. the same on the hourly members only (the daily members hold the price constant within a day and
    # cannot resolve a sub-day claim window: a 6 h completion lands in the same post-jump day)
    hourly = [r for r in boot if r["member"] in ("bootstrap", "history")]
    lines.append("\n### 3b. The same on the hourly members only (`bootstrap`, `history`); the daily members cannot resolve a sub-day window\n")
    lines.append("| class | window | " + " | ".join(f"{dl // 48} h" for dl in DELAYS) + " | post-term |")
    lines.append("|---|---|" + "---|" * (len(DELAYS) + 1))
    wph = worst_by_seed(plan_rows(hourly, "post-term"), lambda r: (r["class"], r["window"]))
    for cls in CLASSES:
        for w in windows:
            cells = []
            for dl in DELAYS:
                wd = worst_by_seed(plan_rows(hourly, "in-term", theta0, str(dl)), lambda r: (r["class"], r["window"]))
                cells.append(fmt_seeds(wd.get((cls, w), {})))
            if all(c == "—" for c in cells):
                continue
            lines.append(f"| {cls} | {w} | " + " | ".join(cells) + f" | {fmt_seeds(wph.get((cls, w), {}))} |")
    # 4. theta sensitivity
    lines.append("\n## 4. Sensitivity to the claim threshold θ (in-term, 24 h delay, plan ratios; bootstrap)\n")
    lines.append("| class | window | " + " | ".join(f"θ {t // 100} %" for t in THETAS) + " |")
    lines.append("|---|---|" + "---|" * len(THETAS))
    for cls in CLASSES:
        for w in windows:
            cells = []
            for t in THETAS:
                wt = worst_by_seed(plan_rows(boot, "in-term", str(t), delay0), lambda r: (r["class"], r["window"]))
                cells.append(fmt_seeds(wt.get((cls, w), {})))
            if all(c == "—" for c in cells):
                continue
            lines.append(f"| {cls} | {w} | " + " | ".join(cells) + " |")
    # 5. the ratio curve and the need per tolerance, both modes
    grid = sorted({int(r["ratio"]) for r in boot})
    for mode in ("in-term", "post-term"):
        lines.append(f"\n## 5{'a' if mode == 'in-term' else 'b'}. P(bad debt) over the ratio grid — {mode} (bootstrap, worst member, max over seeds)\n")
        lines.append("| class | window | " + " | ".join(f"{g // 100} %" for g in grid) + " |")
        lines.append("|---|---|" + "---|" * len(grid))
        rows_m = [r for r in boot if r["mode"] == mode and r["theta"] == theta0 and r["delay"] == (delay0 if mode == "in-term" else "0")]
        wm = worst_by_seed(rows_m, lambda r: (r["class"], r["window"], int(r["ratio"])))
        need_lines = []
        for cls in CLASSES:
            for w in windows:
                cells = []
                for g in grid:
                    v = wm.get((cls, w, g), {})
                    vals = [x for x in v.values() if np.isfinite(x)]
                    cells.append(f"{100 * max(vals):.2f}" if vals else "—")
                if all(c == "—" for c in cells):
                    continue
                lines.append(f"| {cls} | {w} | " + " | ".join(cells) + " |")
                needs = []
                for t in TOLS:
                    ok = [g for g in grid if (vals := [x for x in wm.get((cls, w, g), {}).values() if np.isfinite(x)])
                          and max(vals) <= t]
                    needs.append(f"{min(ok) // 100} %" if ok else f"none ≤ {max(grid) // 100} %")
                need_lines.append(f"| {cls} | {w} | " + " | ".join(needs) + " |")
        lines.append(f"\nSmallest grid ratio within the tolerance in every seed ({mode}):\n")
        lines.append("| class | window | " + " | ".join(f"{100 * t:g} %" for t in TOLS) + " |")
        lines.append("|---|---|" + "---|" * len(TOLS))
        lines.extend(need_lines)
    # 6. regime
    if regime:
        lines.append("\n## 6. Regime price model (stress), plan ratios\n")
        lines.append("| class | window | post-term | in-term |")
        lines.append("|---|---|---|---|")
        rp = worst_by_seed(plan_rows(regime, "post-term"), lambda r: (r["class"], r["window"]))
        ri = worst_by_seed(plan_rows(regime, "in-term"), lambda r: (r["class"], r["window"]))
        for cls in CLASSES:
            for w in windows:
                if (cls, w) in ri:
                    lines.append(f"| {cls} | {w} | {fmt_seeds(rp.get((cls, w), {}))} | {fmt_seeds(ri[(cls, w)])} |")
    # 7. censoring
    lines.append("\n## 7. Censoring: grid terms with no uncensored sample / samples per term, per member (plan ratio, in-term, first seed)\n")
    lines.append("| class | window | " + " | ".join(sorted({r["member"] for r in boot})) + " |")
    members = sorted({r["member"] for r in boot})
    lines.append("|---|---|" + "---|" * len(members))
    first_seed = min(int(r["seed"]) for r in boot)
    for cls in CLASSES:
        for w in windows:
            cells = []
            for m in members:
                rr = [r for r in plan_rows(boot, "in-term") if r["class"] == cls and r["window"] == w and r["member"] == m and int(r["seed"]) == first_seed]
                if not rr:
                    cells.append("—")
                    continue
                r = rr[0]
                cells.append(f"{r['terms_censored']}/{r['terms']} censored; n {int(float(r['n'])):,}" + (" **(no sample)**" if not np.isfinite(_f(r, "p")) else ""))
            lines.append(f"| {cls} | {w} | " + " | ".join(cells) + " |")
    # 8. sys
    if (d / "sys.csv").exists():
        srows = _load(d / "sys.csv")
        lines.append("\n## 8. HALT-2 at 200 % and the recap at 500 % (G7's `halt_sys_bad` and the in-term reading)\n")
        lines.append("| window | seed | P(sys bad: fall ≤ 1/2 within grace) | real history | tolerance | `halt_sys_bad` | P(fall to θ level ≤ 0.625) | real | recap classes (of 3) | halt < every base |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        seen = set()
        for r in srows:
            k = (r["window"], r["seed"], r["model"])
            if k in seen:
                continue
            seen.add(k)
            lines.append(f"| {r['window']} ({r['model']}) | {r['seed']} | {pct(_f(r, 'p_sys_bad'))} | {pct(_f(r, 'p_sys_bad_real'))} | {pct(_f(r, 'tolerance'))} | "
                         f"{'pass' if r['halt_sys_bad_pass'] == 'True' else 'FAIL'} | {pct(_f(r, 'p_fall_to_theta'))} | {pct(_f(r, 'p_fall_to_theta_real'))} | {r['recap_classes']} | {r['halt_below_floor']} |")
        lines.append("\nA vault minted at the halt ratio (200 %): P(bad debt) in-term vs post-term, worst member, max over seeds:\n")
        lines.append("| class | window | in-term | post-term | claimed in term |")
        lines.append("|---|---|---|---|---|")
        acc: dict[tuple, list[tuple[float, float, float]]] = defaultdict(list)
        for r in srows:
            acc[(r["class"], r["window"])].append((_f(r, "p_vault_at_halt_in_term"), _f(r, "p_vault_at_halt_post"), _f(r, "in_term_share")))
        for (cls, w), v in acc.items():
            arr = np.array(v, dtype=float)
            lines.append(f"| {cls} | {w} | {pct(float(np.nanmax(arr[:, 0])))} | {pct(float(np.nanmax(arr[:, 1])))} | {pct(float(np.nanmean(arr[:, 2])))} |")
    # 9. fee
    if (d / "fee.csv").exists():
        frows = _load(d / "fee.csv")
        lines.append("\n## 9. FEE-1 round-trip fee share of the debt (mint armed + redeem), at the reference price\n")
        lines.append("| class | ratio | debt | collateral (YEC) | round trip (USD) | share of debt |")
        lines.append("|---|---|---|---|---|---|")
        for r in frows:
            lines.append(f"| {r['class']} | {int(r['ratio']) // 100} % | ${int(r['cents']) / 100:,.0f} | {float(r['collateral_zat']) / 1e8:,.1f} | "
                         f"${float(r['round_trip_fee_usd']):.2f} | {pct(_f(r, 'fee_share_of_debt'))} |")
        lines.append(f"\n(reference price {int(float(frows[0]['p_ref_microusd'])) / 1e6:.4f} USD/YEC: the last frozen hourly price.)")
    # 10. zec validation
    if (d / "zec_validation.csv").exists():
        z = _load(d / "zec_validation.csv")
        lines.append("\n## 10. Validation against the ZEC study's lever harness (same series, skip 400 d, stride 6 h, single term)\n")
        lines.append("| series | term | ratio | mode | harness | ybcal, ideal oracle | ybcal, hour-mode kernel | n (harness / ybcal) |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for r in z:
            lines.append(f"| {r['series']} | {r['term_days']} d | {int(r['ratio']) // 100} % | {r['mode']} | {pct(_f(r, 'harness'))} | "
                         f"{pct(_f(r, 'ideal'))} | {pct(_f(r, 'kernel'))} | {r['n_harness']} / {r['n_kernel']} |")
    (d / "tables.md").write_text("\n".join(lines) + "\n")
    print(f"wrote {d / 'tables.md'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("curve", "sys"):
        p = sub.add_parser(name)
        p.add_argument("--window", action="append", required=True)
        p.add_argument("--seeds", default="20261003")
        p.add_argument("--budget", default="standard")
        p.add_argument("--models", default="bootstrap")
        p.add_argument("--policy", default="policy/in-term-2026-10.toml")
        p.add_argument("--data", required=True)
        p.add_argument("--out", required=True)
        if name == "curve":
            p.add_argument("--grid", default=",".join(str(g) for g in GRID), help="ratio grid (bps); '' = plan ratios only")
            p.add_argument("--sensitivity", action="store_true", help="delay and θ sweeps at the plan ratios")
    p = sub.add_parser("fee")
    p.add_argument("--policy", default="policy/in-term-2026-10.toml")
    p.add_argument("--data", required=True)
    p.add_argument("--out", required=True)
    p = sub.add_parser("zec")
    p.add_argument("--policy", default="policy/in-term-2026-10.toml")
    p.add_argument("--zec-dir", required=True)
    p.add_argument("--data", required=True)
    p.add_argument("--out", required=True)
    p = sub.add_parser("tables")
    p.add_argument("dir")
    p.add_argument("--policy", default="policy/in-term-2026-10.toml")
    a = ap.parse_args()
    return {"curve": cmd_curve, "sys": cmd_sys, "fee": cmd_fee, "zec": cmd_zec, "tables": cmd_tables}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())

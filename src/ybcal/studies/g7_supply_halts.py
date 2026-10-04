"""G7 — supply cap and halts (PLAN §5.7): ``supplyCapBps``, ``globalRatioHaltBps``,
``recapRatioBps`` (derived = 2 × halt, W16), ``divergenceBps``.

Owner: WP-7d. Method, metric definitions and decision rules: ``docs/studies/g7.md``.

Three families (``ybcal.studies.g5_activation.Family``) on one table of one-at-a-time sweeps:

* **cap** (``supplyCapBps``) — an hour-mode vault book (``simulate_vault_book_hours``, personas from the
  policy) runs a year from ``startHeight`` (``issuedZat`` = subsidy since start, fact 1.5-2) and then the
  ``crash-90-30d`` / ``crash-70-1d`` programmes. *Liquidation demand* on a day = the collateral value of
  the vaults whose claim path is open and which fall under ``claimThresholdBps`` at the true price that
  day (what claimants would have to sell for the system to stay whole — the book's actual claims are
  reported too, but claims are rarely profitable, WP-4 finding 3). Rule: the largest cap whose
  **cap-bound** (B/C below ``recapRatioBps``) worst-day demand (p95 over paths, worst crash) is ≤
  ``max_depth_fraction`` × the p10 daily YEC volume (``env.data["depth"]`` or a placeholder). The
  cap-exempt class-A demand (W16/W20) is reported against the same budget and raised as a design note.
* **halt** (``globalRatioHaltBps`` → ``recapRatioBps``) — minimise P(HALT-2 fires in ``calm-90d``) for a
  mature book (its global ratio at the end of the year) subject to: P(the true price falls to
  1/halt of its level within ``grace``) ≤ the outstanding-debt-weighted ``max_bad_debt_prob`` (system bad debt
  from the halt level before any claim can act, fact 1.5-1); halt below every class floor (§1.4); and
  HALT-2 firing no later than the true system ratio reaches 150 % on ≥ ``halt_recall_floor`` of crash
  paths.
* **divergence** (``divergenceBps``) — the best F1 of HALT-3 over crash falls (positives:
  ``crash-70-1d``, ``crash-90-30d`` and the dump of ``pump-dump-3x``) versus calm windows (negatives:
  ``calm-90d``, the rally of ``pump-dump-3x`` — HALT-3 fires on falls only, fact 1.5-6 — and the
  recovering ``flash-wick-50-1h``), subject to recall ≥ ``halt_recall_floor``.

Days until the cap admits a typical B/C mint and the days B/C stay closed come from the same book;
when they exceed ``max_class_closed_days`` the study emits a **design note** (rule-level: the
``issuedZat`` origin) — ``design_notes(results)`` and ``Recommendation.metrics["design_notes"]``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ybcal.model import vkernels as V
from ybcal.params.paramset import ParamSet
from ybcal.params.registry import REGISTRY, params_for_group
from ybcal.studies import g1_price_windows as G1
from ybcal.studies.base import Budget, Env, Metrics, Recommendation, ResultRow, ResultTable
from ybcal.studies.g5_activation import Family, FamilyStudy, env_out_dir, family_rows, family_table
from ybcal.studies.g6_miners_fees import (
    _cached,
    _ekey,
    _fmt,
    agents_config,
    figure_style,
    hour_prefix,
    neighbour_sentence,
    reference_price,
)
from ybcal.units import BLOCKS_PER_DAY, BLOCKS_PER_HOUR, BPS, COIN, MICRO_USD_PER_USD

OWNER_WP = "WP-7d"

GROUP = "G7"

#: Judgement constants (no policy key yet; requested in docs/decisions.md D-WP7d-6).
JUDGEMENT: dict[str, float] = {
    "p10_daily_volume_usd": 25_000.0,  # placeholder p10 daily YEC volume when no depth file is loaded
    "system_ratio_alarm_bps": 15_000,  # HALT-2 must fire before the true system ratio reaches this
    "demand_quantile": 95.0,  # quantile over paths of the worst-day liquidation demand
    # share of the cap-bound debt whose claim path must open inside the book for the depth bound to
    # count as evidence (D-RD-AUD-5)
    "cap_min_uncensored_share": 0.5,
}
POLICY_KEYS: dict[str, str] = {
    "p10_daily_volume_usd": "p10_daily_volume_usd",
    "system_ratio_alarm_bps": "system_ratio_alarm_bps",
}

CRASHES: tuple[str, ...] = ("crash-90-30d", "crash-70-1d")
PREFIX_DAYS = 365  #: book days from startHeight before the crash programme
TAIL_DAYS = 60  #: days of each crash scenario appended (covers both falls)
DD_YEARS = 5  #: hourly years per path of the drawdown ensemble behind halt_sys_bad
#: HALT-3 classification: positive falls and negative (calm) windows per scenario.
POSITIVE: tuple[str, ...] = ("crash-70-1d", "crash-90-30d", "pump-dump-3x")
NEGATIVE: tuple[str, ...] = ("calm-90d", "pump-dump-3x", "flash-wick-50-1h")
DETECT_TAIL_BLOCKS = BLOCKS_PER_DAY  #: a fall counts as caught if HALT-3 fires before its end + 1 day


def judgement(policy, key: str) -> float:
    pk = POLICY_KEYS.get(key)
    return float(getattr(policy, pk, JUDGEMENT[key]) if pk else JUDGEMENT[key])


def g7_paths(budget: Budget) -> int:
    """Book paths per crash scenario: ``budget.paths // 8`` (quick 8, standard 50, deep 250), ≥ 2."""
    return max(2, int(budget.paths) // 8)


# ===================================================================================================
# Inputs


def depth_budget(env: Env) -> tuple[float, float, str]:
    """(p10 daily YEC volume USD, liquidation budget USD = max_depth_fraction × it, provenance)."""
    d = env.data.get("depth") if isinstance(env.data, Mapping) else None
    vol = None
    if d is not None and hasattr(d, "volume_24h_usd"):
        arr = np.asarray(d.volume_24h_usd, dtype=float)
        arr = arr[np.isfinite(arr) & (arr > 0)]
        if len(arr):
            vol = float(np.percentile(arr, 10))
    prov = "real-data" if vol is not None else "judgement"
    if vol is None and getattr(env.policy, "yec_daily_volume_p10_usd", None) is not None:
        vol = float(env.policy.yec_daily_volume_p10_usd)  # owner-supplied figure, shared with G9
    if vol is None:
        vol = judgement(env.policy, "p10_daily_volume_usd")
    return vol, float(env.policy.max_depth_fraction) * vol, prov


def crash_book_prices(env: Env) -> tuple[np.ndarray, int, list[str]]:
    """Hourly paths: a ``PREFIX_DAYS`` prefix (``g6.hour_prefix``) followed by each crash scenario's
    first ``TAIL_DAYS`` days, rescaled to start at the prefix's last price. Returns ``(prices
    (len(CRASHES)·P, n), crash start hour, label per path)``."""
    P = g7_paths(env.budget)

    def build():
        pre = hour_prefix(env, P, PREFIX_DAYS, "G7")
        out, labels = [], []
        for name in CRASHES:
            scen = G1.scenario(env, name)
            run = scen.generate(
                env.rng_for("G7", "crash", name, P),
                P,
                resolution="hour",
                horizon_days=min(TAIL_DAYS, scen.horizon_days),
            )
            tail = np.asarray(run.paths.prices, dtype=np.float64)
            tail = tail / tail[:, :1] * pre[:, -1:]
            out.append(np.concatenate([pre, np.rint(tail[:, 1:]).astype(np.int64)], axis=1))
            labels += [name] * P
        n = min(a.shape[1] for a in out)
        return np.concatenate([a[:, :n] for a in out], axis=0), PREFIX_DAYS * 24, labels

    return _cached(_ekey(env, "g7_prices", P), build)


def _book_key_params(cand: ParamSet) -> ParamSet:
    """The book depends on the cap and the halt (HALT-2, W16 recap); HALT-3 in the hour kernel is held
    at the registry divergenceBps so the divergence sweep reuses one book (D-WP7d-4)."""
    return cand.replace({"divergenceBps": REGISTRY["divergenceBps"].mainnet})


def crash_book(env: Env, cand: ParamSet):
    from ybcal.sim import vaults as VB

    ps = _book_key_params(cand)

    def build():
        prices, _, _ = crash_book_prices(env)
        return VB.simulate_vault_book_hours(
            ps,
            prices,
            agents_config(env.policy),
            rng=env.rng_for("G7", "book"),
            options=VB.HourOptions(book=VB.BookOptions(traj_every=24)),
            workers=1,
        )

    return _cached(_ekey(env, "g7_book", ps.digest()), build)


def typical_mint_cents(cand: Mapping) -> int:
    """The median of the minter's log-uniform size distribution: √(minMint · maxMint)."""
    return math.isqrt(int(cand["minMint"]) * int(cand["maxMint"]))


def book_metrics(env: Env, cand: ParamSet) -> dict[str, Any]:
    """Liquidation demand, the mature global ratio, cap headroom and B/C closure from the crash book."""
    ps = _book_key_params(cand)

    def build() -> dict[str, Any]:
        from ybcal.sim import supply as SUP
        from ybcal.sim import vaults as VB

        res = crash_book(env, cand)
        _, crash_h, labels = crash_book_prices(env)
        labels_a = np.asarray(labels)
        v = res.vaults
        ts = res.traj_steps
        tp = res.true_price.astype(np.float64)
        Pn, S = res.supply_cents.shape
        j0 = int(np.searchsorted(ts, crash_h))
        thr = int(ps["claimThresholdBps"])
        recap = int(ps["recapRatioBps"])
        acc = (v["outcome"] == VB.O_ACTIVE) & (v["claim_open_step"] >= 0)
        exempt_v = v["min_ratio_bps"] >= recap
        dem = {k: np.zeros((Pn, S)) for k in ("total", "bound", "exempt")}
        p0 = int(v["path"].min(initial=0)) if v["path"].size else 0
        for p in range(Pn):
            m = acc & (v["path"] == p + p0)
            if not m.any():
                continue
            co = v["claim_open_step"][m]
            end = np.where(v["close_step"][m] >= 0, v["close_step"][m], np.iinfo(np.int64).max)
            coll = v["collateral_zat"][m].astype(np.float64)
            cents = v["cents"][m].astype(np.float64)
            alive = (co[:, None] <= ts[None, :]) & (ts[None, :] < end[:, None])
            under = alive & (coll[:, None] * tp[p][None, :] < cents[:, None] * 1e12 * thr / BPS)
            first = np.where(under.any(axis=1), under.argmax(axis=1), -1)
            k = first >= 0
            usd = coll[k] * tp[p, first[k]] / COIN / MICRO_USD_PER_USD
            ex = exempt_v[m][k]
            np.add.at(dem["total"][p], first[k], usd)
            np.add.at(dem["exempt"][p], first[k][ex], usd[ex])
            np.add.at(dem["bound"][p], first[k][~ex], usd[~ex])
        q = judgement(env.policy, "demand_quantile")
        out: dict[str, Any] = {}
        for kind, arr in dem.items():
            worst = 0.0
            for name in CRASHES:
                rows = arr[labels_a == name][:, j0:]
                if rows.size:
                    worst = max(worst, float(np.percentile(rows.max(axis=1), q)))
            out[f"liq.demand_{kind}_usd"] = worst
        pre = dem["total"][:, :j0]
        out["liq.demand_prefix_usd"] = float(np.percentile(pre.max(axis=1), q)) if pre.size else 0.0
        from ybcal.sim import metrics as M

        lv = M.liquidation_volume(res)["daily_usd"]
        d0 = int(crash_h * BLOCKS_PER_HOUR // BLOCKS_PER_DAY)
        worst = 0.0
        for name in CRASHES:
            rows = lv[labels_a == name][:, d0:]
            if rows.size:
                worst = max(worst, float(np.percentile(rows.max(axis=1), q)))
        out["liq.claims_max_day_usd"] = worst
        # mature book: global ratio at the true price at the end of the prefix
        je = max(0, j0 - 1)
        sup = res.supply_cents[:, je].astype(np.float64) / 100
        col = res.collateral_zat[:, je].astype(np.float64) / COIN * tp[:, je] / MICRO_USD_PER_USD
        ok = sup > 0
        out["book.global_ratio_true"] = float(np.median(col[ok] / sup[ok])) if ok.any() else math.nan
        out["book.supply_usd_year1"] = float(np.median(sup))
        exempt_usd = []
        for p in range(Pn):
            m = (
                (v["outcome"] == VB.O_ACTIVE)
                & (v["path"] == p + p0)
                & exempt_v
                & (v["step"] <= ts[je])
                & ((v["close_step"] < 0) | (v["close_step"] > ts[je]))
            )
            exempt_usd.append(float(v["cents"][m].sum()) / 100)
        out["book.exempt_share_year1"] = (
            float(np.median(np.asarray(exempt_usd)[ok] / sup[ok])) if ok.any() else math.nan
        )
        # cap headroom over the prefix (true price as pMint proxy)
        start = int(ps["startHeight"])
        h = res.traj_heights[:j0].astype(np.int64)
        iss = SUP.issued_zat_series(start, int(h.max()) - start + 1)[h - start] if len(h) else np.zeros(0)
        cap = SUP.supply_cap_cents(iss[None, :], res.true_price[:, :j0], int(ps["supplyCapBps"]))
        cap = np.where(cap < 0, np.iinfo(np.int64).max // 4, cap)
        head = cap - res.supply_cents[:, :j0]
        typ = typical_mint_cents(ps)
        closed = {}
        for cls, name in ((1, "B"), (2, "C")):
            if int(ps[f"baseRatioBps[{cls}]"]) >= recap:
                closed[name] = 0.0
            else:
                closed[name] = float(np.median((head < typ).sum(axis=1)))
        out["cap.closed_days_B"] = closed["B"]
        out["cap.closed_days_C"] = closed["C"]
        out["cap.closed_days_bc"] = max(closed.values())
        admits = head >= typ
        first = np.where(admits.any(axis=1), admits.argmax(axis=1), j0)
        out["cap.days_until_admits_bc"] = float(np.median(first))
        bc = (v["term_class"] >= 1) & (v["step"] < crash_h)
        n_bc = int(bc.sum())
        out["cap.bc_refused_share"] = (
            float((bc & (v["reason"] == "mint-supply-cap")).sum() / n_bc) if n_bc else math.nan
        )
        # how much evidence the depth bound carries (D-RD-AUD-5): the cap-bound debt minted in the prefix
        # and the share of it whose claim path opens inside the book's horizon (the rest — every class-C
        # vault, the long class-B ones — can never show up in liq.demand_bound_usd)
        bound = (v["outcome"] == VB.O_ACTIVE) & ~exempt_v & (v["step"] < crash_h)
        bd = float(v["cents"][bound].sum()) / 100
        out["cap.bound_debt_usd"] = bd / max(1, Pn)
        out["cap.bound_uncensored_share"] = (
            float(v["cents"][bound & (v["claim_open_step"] >= 0)].sum()) / 100 / bd if bd > 0 else math.nan
        )
        a = (v["term_class"] == 0) & (v["step"] < crash_h)
        out["cap.a_accepted_share"] = float((a & (v["outcome"] == VB.O_ACTIVE)).sum() / max(1, int(a.sum())))
        # figure series (JSON-safe, ~weekly)
        step = max(1, j0 // 52)
        out["series.days"] = [float(x) for x in (ts[:j0:step] * res.step_blocks / BLOCKS_PER_DAY)]
        out["series.supply_usd_p50"] = [
            float(x) for x in np.median(res.supply_cents[:, :j0:step], axis=0) / 100
        ]
        capm = np.median(np.where(cap > np.iinfo(np.int64).max // 8, np.nan, cap)[:, ::step], axis=0) / 100
        out["series.cap_usd_p50"] = [float(x) for x in capm]
        return out

    return _cached(_ekey(env, "g7_book_metrics", ps.digest()), build)


# ---------------------------------------------------------------------------------------------------
# HALT-2 and HALT-3


def sys_bad_prob(env: Env, ratio_bps: int, horizon_blocks: int) -> float:
    """P(the true price falls to ≤ 10⁴/ratio of its level within ``horizon_blocks``), over daily starts
    of a ``DD_YEARS``-year hourly ensemble (``g6.hour_prefix``: real bootstrap or GARCH placeholder)."""
    from scipy.ndimage import minimum_filter1d

    n_paths = max(8, int(env.budget.paths) // 2)

    def ratios() -> np.ndarray:
        p = hour_prefix(env, n_paths, DD_YEARS * 365, "G7dd").astype(np.float64)
        w = max(1, round(horizon_blocks / BLOCKS_PER_HOUR))
        fmin = np.stack([minimum_filter1d(row, size=w + 1, origin=-(w // 2), mode="nearest") for row in p])
        return (fmin / p)[:, : p.shape[1] - w : 24].ravel()

    r = _cached(_ekey(env, "g7_dd", n_paths, int(horizon_blocks)), ratios)
    return float((r <= BPS / int(ratio_bps)).mean())


def real_sys_bad_prob(price, ratio_bps: int, horizon_blocks: int) -> float:
    """``sys_bad_prob`` on the real hourly history itself, no bootstrap: over daily starts, the share
    where the price's minimum within ``horizon_blocks`` is ≤ 10⁴/ratio of the start (D-RD-ORA-7)."""
    from scipy.ndimage import minimum_filter1d

    p = price.prices[0].astype(np.float64)
    p = np.where(p > 0, p, np.nan)
    good = np.isfinite(p)
    if good.sum() < 48:
        return math.nan
    p = np.where(good, p, np.nanmedian(p))
    w = max(1, round(horizon_blocks / BLOCKS_PER_HOUR))
    if p.size <= w + 24:
        return math.nan
    fmin = minimum_filter1d(p, size=w + 1, origin=-(w // 2), mode="nearest")
    r = (fmin / p)[: p.size - w : 24]
    return float((r <= BPS / int(ratio_bps)).mean())


def system_tolerance(policy, params: Mapping) -> float:
    """System bad-debt tolerance: the class tolerances (``max_bad_debt_prob``) weighted by each class's
    share of the outstanding debt of a mature book — arrival weight (the minter's ``class_weights``) ×
    mean term (mid-point of ``[classMin, classMax]``, uniform terms) (D-WP7d-3)."""
    w = agents_config(policy).minter.class_weights
    terms = [(int(params[f"classMin[{i}]"]) + int(params[f"classMax[{i}]"])) / 2 for i in range(3)]
    debt = [wi * t for wi, t in zip(w, terms, strict=True)]
    return float(sum(d * policy.max_bad_debt(i) for i, d in enumerate(debt)) / max(sum(debt), 1e-12))


def _realised(env: Env, name: str, cand: Mapping):
    r = G1.realise(env, name)
    return r, G1.prices(r, cand)


def halt2_metrics(env: Env, cand: ParamSet, r0: float) -> dict[str, float]:
    """HALT-2 at ``globalRatioHaltBps`` for a book at true global ratio ``r0`` entering each crash
    (collateral and supply frozen — HALT-2 itself stops new mints): the global ratio at xMint is
    ``r0 · xMint(t)/true(fall)``, the true one ``r0 · true(t)/true(fall)``."""
    h = int(cand["globalRatioHaltBps"]) / BPS
    alarm = judgement(env.policy, "system_ratio_alarm_bps") / BPS
    out: dict[str, float] = {}
    hits, events, leads, hits1, ev1 = 0, 0, [], 0, 0
    for name in ("crash-70-1d", "crash-90-30d"):
        r, pr = _realised(env, name, cand)
        s = r.marks.get("fall")
        if s is None:
            continue
        ref = r.true[:, s].astype(np.float64)[:, None]
        T = r0 * r.true[:, s:] / ref
        xm = pr.x_mint[:, s:]
        G = np.where(xm > 0, r0 * xm / ref, np.inf)
        fire = h > G
        t_h = np.where(fire.any(axis=1), fire.argmax(axis=1), np.iinfo(np.int64).max)
        for lvl in (alarm, 1.0):
            bad = lvl > T
            has = bad.any(axis=1)
            t_b = np.where(has, bad.argmax(axis=1), -1)
            if lvl == alarm:
                events += int(has.sum())
                hits += int((has & (t_h <= t_b)).sum())
                leads += [
                    float(b - a) / BLOCKS_PER_HOUR
                    for a, b in zip(t_h[has], t_b[has], strict=True)
                    if a < np.iinfo(np.int64).max
                ]
            else:
                ev1 += int(has.sum())
                hits1 += int((has & (t_h <= t_b)).sum())
    out["halt.recall_alarm"] = hits / events if events else 1.0
    out["halt.recall_100"] = hits1 / ev1 if ev1 else 1.0
    out["halt.alarm_events"] = float(events)
    out["halt.lead_hours_p50"] = float(np.median(leads)) if leads else math.nan
    r, pr = _realised(env, "calm-90d", cand)
    w0 = min(G1.WARMUP_BLOCKS, r.n - 1)
    ref = r.true[:, w0].astype(np.float64)[:, None]
    xm = pr.x_mint[:, w0:]
    G = np.where(xm > 0, r0 * xm / ref, np.inf)
    out["halt.false_calm"] = float((h > G).any(axis=1).mean())
    out["halt.false_calm_hours_p50"] = float(np.median((h > G).sum(axis=1)) / BLOCKS_PER_HOUR)
    return out


def divergence_metrics(env: Env, cand: ParamSet) -> dict[str, float]:
    """HALT-3 confusion counts per path (see the module docstring) and precision / recall / F1."""
    d = int(cand["divergenceBps"])
    tp = fn = fp = tn = 0
    out: dict[str, float] = {}
    for name in sorted(set(POSITIVE) | set(NEGATIVE)):
        r, pr = _realised(env, name, cand)
        h3 = np.asarray(V.halt3_divergence(pr.p_fast, pr.p_mid, pr.p_slow, d), dtype=bool)
        w0 = min(G1.WARMUP_BLOCKS, r.n - 1)
        mk = r.marks
        if name in POSITIVE and "fall" in mk:
            a, b = mk["fall"], min(r.n, mk["fall_end"] + DETECT_TAIL_BLOCKS)
            hit = h3[:, a:b].any(axis=1)
            tp += int(hit.sum())
            fn += int((~hit).sum())
            out[f"div.recall.{name}"] = float(hit.mean())
        if name in NEGATIVE:
            end = mk["fall"] if name == "pump-dump-3x" and "fall" in mk else r.n
            neg = h3[:, w0:end].any(axis=1)
            fp += int(neg.sum())
            tn += int((~neg).sum())
            out[f"div.fp.{name}"] = float(neg.mean())
            if name == "pump-dump-3x" and "rise" in mk:
                rally = h3[:, mk["rise"] : mk["rise_end"] + 1].any(axis=1)
                out["div.fp.rally"] = float(rally.mean())
            if name == "calm-90d":
                out["div.calm_halt_hours_per_year"] = float(h3[:, w0:].mean() * 8760)
    rec = tp / (tp + fn) if tp + fn else 0.0
    prec = tp / (tp + fp) if tp + fp else 0.0
    out["div.recall"] = rec
    out["div.precision"] = prec
    out["div.f1"] = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    out["div.tp"], out["div.fn"], out["div.fp"], out["div.tn"] = float(tp), float(fn), float(fp), float(tn)
    return out


# ===================================================================================================
# The study


def _fams() -> tuple[Family, ...]:
    cap = (
        "liq.demand_bound_usd",
        "liq.demand_exempt_usd",
        "liq.demand_total_usd",
        "liq.budget_usd",
        "liq.claims_max_day_usd",
        "cap.closed_days_bc",
        "cap.days_until_admits_bc",
        "cap.bc_refused_share",
        "cap.days_first_admit_empty",
        "book.supply_usd_year1",
        "book.exempt_share_year1",
        "cap.bound_debt_usd",
        "cap.bound_uncensored_share",
        "cap.informative",
        "cap.value",
    )
    halt = (
        "halt.false_calm",
        "halt.p_sys_bad",
        "halt.p_sys_bad_real",
        "halt.sys_bad_tolerance",
        "halt.required_bps",
        "halt.recall_alarm",
        "halt.recall_100",
        "halt.lead_hours_p50",
        "halt.bypass_classes",
        "book.global_ratio_true",
    )
    div = (
        "div.f1",
        "div.recall",
        "div.precision",
        "div.recall.crash-70-1d",
        "div.recall.crash-90-30d",
        "div.recall.pump-dump-3x",
        "div.fp.calm-90d",
        "div.fp.pump-dump-3x",
        "div.fp.flash-wick-50-1h",
        "div.calm_halt_hours_per_year",
    )
    return (
        Family(
            "cap",
            ("supplyCapBps",),
            ("supplyCapBps",),
            "supplyCapBps: the cap (step 250) that admits the most class-B/C mint attempts (primary: "
            "the share refused by the cap in the first year, ties toward the current value — a larger "
            "cap is a benefit only when it admits demand, D-RD-AUD-5) among caps whose cap-bound "
            "(classes below recapRatioBps) "
            "liquidation demand on the worst day of the worst crash (p95 over paths) stays ≤ "
            "max_depth_fraction × the p10 daily YEC volume, counted only where at least half of the "
            "cap-bound debt opens its claim path inside the book (else the bound is unverified, "
            "D-RD-AUD-5); KEEP when no swept cap binds any class; the cap-exempt class-A demand is "
            "reported against the same budget (design note when it alone exceeds it); KEEP unless > "
            "materiality.",
            primary="cap.bc_refused",
            minimize=True,
            constraints=("depth_bound",),
            report=cap,
            sens_metric="liq.demand_bound_usd",
            provenance_key="cap_provenance",
        ),
        Family(
            "halt",
            ("globalRatioHaltBps", "recapRatioBps"),
            ("globalRatioHaltBps",),
            "globalRatioHaltBps (recapRatioBps = 2× follows, W16): minimise P(HALT-2 fires in calm-90d) for "
            "the mature book subject to P(the true price falls to 1/halt within grace) ≤ the debt-weighted "
            "max_bad_debt_prob, halt < every baseRatioBps (§1.4), and HALT-2 firing no later than the "
            "true system ratio reaches 150 % on ≥ halt_recall_floor of crash paths; KEEP unless > "
            "materiality.",
            primary="halt.false_calm",
            constraints=("halt_sys_bad", "halt_below_floor", "halt2_timely"),
            report=halt,
            sens_metric="halt.p_sys_bad",
            provenance_key="price_provenance",
        ),
        Family(
            "divergence",
            ("divergenceBps",),
            ("divergenceBps",),
            "divergenceBps: the best F1 of HALT-3 over crash falls (crash-70-1d, crash-90-30d, the dump of "
            "pump-dump-3x) versus calm windows (calm-90d, the rally of pump-dump-3x, flash-wick-50-1h) "
            "subject to recall ≥ halt_recall_floor and HALT-3 hours per year in calm-90d ≤ "
            "max_no_price_hours (minting availability, D-RD-AUD-6); KEEP unless > materiality.",
            primary="div.f1",
            minimize=False,
            constraints=("recall", "calm_availability"),
            report=div,
            sens_metric="div.recall",
            provenance_key="price_provenance",
        ),
    )


def _bps(x: float) -> str:
    return f"{int(x):,}" if isinstance(x, (int, float)) and math.isfinite(x) else "none on the grid"


def _axis(base: ParamSet, name: str, values: Iterable[int]) -> list[ParamSet]:
    lo, hi = REGISTRY[name].bounds
    return [base.replace({name: int(v)}) for v in values if lo <= int(v) <= hi and int(v) != base[name]]


@dataclass
class G7Study(FamilyStudy):
    """Supply cap and halts (PLAN §5.7)."""

    group: str = GROUP
    params: tuple[str, ...] = field(default_factory=lambda: params_for_group(GROUP))

    def families(self) -> tuple[Family, ...]:
        return _fams()

    def space(self, base: ParamSet, budget: Budget) -> Iterable[ParamSet]:
        fine = budget.name != "quick"
        out = [base]
        out += _axis(
            base,
            "supplyCapBps",
            range(250, 5001, 250) if fine else (500, 1000, 1500, 2000, 2500, 3000, 4000, 5000),
        )
        out += _axis(base, "globalRatioHaltBps", range(12_500, 30_001, 1250 if fine else 2500))
        out += _axis(base, "divergenceBps", range(500, 5001, 250 if fine else 500))
        return out

    def evaluate(self, cand: ParamSet, env: Env) -> Metrics:
        pol = env.policy
        v: dict[str, float] = {"zero": 0.0}
        c: dict[str, bool] = {}
        price_prov = G1.provenance_of(env)
        vol, budget_usd, dprov = depth_budget(env)
        meta: dict[str, Any] = {
            "seed": env.seed,
            "budget": env.budget.name,
            "budget_paths": env.budget.paths,
            "budget_days": env.budget.block_horizon_days,
            "out_dir": env_out_dir(env),
            "price_provenance": price_prov,
            "depth_provenance": dprov,
            "cap_provenance": "real-data" if price_prov == dprov == "real-data" else "synthetic",
            "max_class_closed_days": float(pol.max_class_closed_days),
        }
        # ---- supply cap -----------------------------------------------------------------------------
        bm = book_metrics(env, cand)
        for k, x in bm.items():
            if k.startswith("series."):
                meta[k] = x
            else:
                v[k] = float(x)
        v["cap.value"] = float(cand["supplyCapBps"])
        r_bc = v.get("cap.bc_refused_share", math.nan)
        v["cap.bc_refused"] = float(r_bc) if math.isfinite(r_bc) else 0.0  # no B/C attempt: nothing refused
        v["liq.p10_volume_usd"] = vol
        v["liq.budget_usd"] = budget_usd
        from ybcal.sim import supply as SUP

        typ = typical_mint_cents(cand)
        v["cap.typical_mint_usd"] = typ / 100
        v["cap.days_first_admit_empty"] = SUP.days_until_cap_admits_const(typ, reference_price(env), cand)
        # D-RD-AUD-5: the depth bound is evidence only when the cap-bound debt's claims open inside the
        # book; a cap that admits mostly long B/C debt (claims beyond the horizon) is unverified, not safe
        unc = v.get("cap.bound_uncensored_share", math.nan)
        bound_debt = v.get("cap.bound_debt_usd", 0.0)
        min_unc = judgement(pol, "cap_min_uncensored_share")
        v["cap.informative"] = float(bound_debt > 0 and math.isfinite(unc) and unc >= min_unc)
        verified = bound_debt <= 0 or bool(v["cap.informative"])
        c["depth_bound"] = verified and v["liq.demand_bound_usd"] <= budget_usd
        v["viol.depth_bound"] = max(0.0, v["liq.demand_bound_usd"] / max(budget_usd, 1e-9) - 1.0) + (
            0.0 if verified else (min_unc - (unc if math.isfinite(unc) else 0.0)) / min_unc
        )
        # ---- HALT-2 ---------------------------------------------------------------------------------
        h = int(cand["globalRatioHaltBps"])
        r0 = v["book.global_ratio_true"]
        if not math.isfinite(r0):
            r0 = float(np.mean([int(cand[f"baseRatioBps[{i}]"]) for i in range(3)])) / BPS
        v.update(halt2_metrics(env, cand, r0))
        grace = int(cand["grace"])
        v["halt.p_sys_bad"] = sys_bad_prob(env, h, grace)
        real = G1.real_price(env)
        if real is not None:  # the same probability read on the real history itself (D-RD-ORA-7)
            v["halt.p_sys_bad_real"] = real_sys_bad_prob(real, h, grace)
        tol = system_tolerance(pol, cand)
        v["halt.sys_bad_tolerance"] = tol
        grid = list(range(12_500, 30_000, 1250))
        req = next((g for g in grid if sys_bad_prob(env, g, grace) <= tol), math.nan)
        v["halt.required_bps"] = float(req)
        v["halt.bypass_classes"] = float(
            sum(int(cand[f"baseRatioBps[{i}]"]) >= int(cand["recapRatioBps"]) for i in range(3))
        )
        c["halt_sys_bad"] = v["halt.p_sys_bad"] <= tol
        c["halt_below_floor"] = h < min(int(cand[f"baseRatioBps[{i}]"]) for i in range(3))
        c["halt2_timely"] = v["halt.recall_alarm"] >= float(pol.halt_recall_floor)
        meta["global_ratio_true"] = r0
        # ---- HALT-3 ---------------------------------------------------------------------------------
        v.update(divergence_metrics(env, cand))
        # the real history replayed (D-RD-ORA-3): HALT-3 on YEC's own price, per era
        from ybcal.studies import oracle_replay as R

        ev = R.halt_evidence(env, cand)
        v.update({f"div.{k}": x for k, x in ev.items()})
        false_eras = [x for k, x in ev.items() if k.startswith("replay_halt3_false_h_per_year_")]
        v["div.real_false_h_max"] = max(false_eras) if false_eras else math.nan
        c["recall"] = v["div.recall"] >= float(pol.halt_recall_floor)
        # HALT-3 in a calm market stops minting like NO_PRICE does: it shares the availability budget
        # (D-RD-AUD-6). Synthetic calm is GBM 60 %; with real data the calm base is a bootstrap.
        # With a real price the budget is read on the real history (D-RD-ORA-6): HALT-3 hours outside
        # every real ≥ 20 % weekly fall, in the worst era. The bootstrap calm-90d is not calm — it
        # resamples the real crash days too — so it counted correct halts as false ones.
        avail = v["div.real_false_h_max"]
        if not math.isfinite(avail):
            avail = v["div.calm_halt_hours_per_year"]
        v["div.availability_h_per_year"] = float(avail)
        c["calm_availability"] = avail <= float(pol.max_no_price_hours)
        prov = "real-data" if price_prov == "real-data" else "synthetic"
        return Metrics(v, "zero", True, c, prov, meta)

    def decide_family(self, table: ResultTable, fam: Family, policy) -> tuple[ResultRow, str, str]:
        """The cap family keeps the current value when its depth bound carries no evidence at the
        current set (D-RD-AUD-5): "the largest cap whose cap-bound liquidation demand fits the depth
        budget" with no cap-bound demand to measure — every class at or above recapRatioBps (W20 soft
        cap), or the cap-bound vaults' claims opening beyond the book — would otherwise always pick
        the top of the grid."""
        if fam.name == "cap":
            rows = family_table(table, fam)
            cur = rows.current()
            if cur is not None:
                cv = cur.metrics.values
                if all(not r.metrics.values.get("cap.bound_debt_usd", 1.0) for r in rows):
                    return cur, "KEEP", (
                        "depth bound uninformative at this set: no cap-bound debt at any swept cap (every "
                        "class minimum ≥ recapRatioBps bypasses the soft cap, W20); current kept"
                    )
                if cv.get("cap.bound_debt_usd", 0.0) > 0 and not cv.get("cap.informative", 1.0):
                    unc = cv.get("cap.bound_uncensored_share", math.nan)
                    return cur, "KEEP", (
                        f"depth bound unverifiable at the current cap: only {unc:.0%} of the cap-bound "
                        "debt opens its claim path inside the book, so the book can neither confirm nor "
                        "refute it; current kept (no evidence to move either way)"
                    )
        return super().decide_family(table, fam, policy)

    def decide(self, results: ResultTable, policy) -> list[Recommendation]:
        recs = super().decide(results, policy)
        notes = design_notes(results, policy)
        for r in recs:
            r.metrics["design_notes"] = list(notes)
        return recs

    def extra_notes(self, fam: Family, param: str, cur: ResultRow, best: ResultRow) -> list[str]:
        mv = cur.metrics.values
        out = []
        if fam.name == "cap":
            out.append(
                "Worst-day liquidation demand (p95, worst crash): cap-bound "
                f"${mv['liq.demand_bound_usd']:,.0f}, "
                f"cap-exempt class A ${mv['liq.demand_exempt_usd']:,.0f}, total "
                f"${mv['liq.demand_total_usd']:,.0f} "
                f"against a budget of ${mv['liq.budget_usd']:,.0f} "
                f"({cur.metrics.meta.get('depth_provenance')} p10 "
                f"volume ${mv['liq.p10_volume_usd']:,.0f}); the book's rational claimants (who claim only "
                f"when it pays) sold ${mv['liq.claims_max_day_usd']:,.0f} on their worst crash day (p95)."
            )
            out.append(
                f"B/C: {mv['cap.bc_refused_share']:.0%} of B/C attempts refused by MINT-6 in the first "
                "year; a "
                f"${mv['cap.typical_mint_usd']:,.0f} B/C mint waits {mv['cap.days_first_admit_empty']:.0f} "
                "days in an "
                f"empty system and {mv['cap.days_until_admits_bc']:.0f} days with the class-A supply the "
                "book "
                f"accumulates ({mv['book.exempt_share_year1']:.0%} of supply after a year is cap-exempt)."
            )
        if fam.name == "halt":
            if best is not cur:
                rb = int(best.params["recapRatioBps"])
                lost = [
                    n
                    for i, n in enumerate("ABC")
                    if int(cur.params[f"baseRatioBps[{i}]"]) >= int(cur.params["recapRatioBps"])
                    and int(best.params[f"baseRatioBps[{i}]"]) < rb
                ]
                gained = [
                    n
                    for i, n in enumerate("ABC")
                    if int(cur.params[f"baseRatioBps[{i}]"]) < int(cur.params["recapRatioBps"])
                    and int(best.params[f"baseRatioBps[{i}]"]) >= rb
                ]
                if lost:
                    out.append(
                        f"Coupling (W16/W20): recapRatioBps moves to {rb}, so class {', '.join(lost)} at σ "
                        "= 1 "
                        "no longer bypasses HALT-2 and the soft cap — the early-cap closure (G7 design note) "
                        "then applies to it too."
                    )
                if gained:
                    out.append(
                        f"Coupling (W16/W20): recapRatioBps moves to {rb}, so class {', '.join(gained)} at "
                        "σ = 1 "
                        "now bypasses HALT-2 and the soft cap."
                    )
            out.append(
                f"P(the price falls to 1/halt within grace) at halt "
                f"{cur.params['globalRatioHaltBps']}: {mv['halt.p_sys_bad']:.2%} vs tolerance "
                f"{mv['halt.sys_bad_tolerance']:.2%}; the smallest grid value meeting it is "
                f"{_bps(mv['halt.required_bps'])}. The mature book's true global ratio is "
                f"{mv['book.global_ratio_true']:.2f}×."
            )
        if fam.name == "divergence":
            out.append(
                "Per scenario at the current value: recall "
                + ", ".join(f"{s} {mv.get(f'div.recall.{s}', math.nan):.0%}" for s in POSITIVE)
                + "; false halts "
                + ", ".join(f"{s} {mv.get(f'div.fp.{s}', math.nan):.0%}" for s in NEGATIVE)
                + ". HALT-3 compares a faster median below a slower one, so it never fires on the rally "
                "(fact 1.5-6)."
            )
        return out

    def figures(
        self, table: ResultTable, chosen: ParamSet, out: Path, confirm: Mapping[str, Any]
    ) -> list[Path]:
        return g7_figures(table, chosen, out, {f.name: f for f in self.families()})

    def explain(self, rec: Recommendation, results: ResultTable) -> str:
        return explain_g7(rec, results, {f.name: f for f in self.families()})


# ===================================================================================================
# Design notes, explanation, figures


def design_notes(results: ResultTable, policy=None) -> list[str]:
    """Rule-level findings G7 raises for the owner (report §5), from the current row (and the
    recommended cap when the table holds it)."""
    cur = results.current()
    if cur is None:
        return []
    mv = cur.metrics.values
    limit = float(
        getattr(policy, "max_class_closed_days", cur.metrics.meta.get("max_class_closed_days", 90.0))
    )
    notes = []
    closed = mv.get("cap.closed_days_bc", math.nan)
    if math.isfinite(closed) and closed > limit:
        best_cap = max(
            (r for r in results if set(r.delta) <= {"supplyCapBps"}),
            key=lambda r: int(r.params["supplyCapBps"]),
        )
        bc_best = best_cap.metrics.values.get("cap.closed_days_bc", math.nan)
        notes.append(
            "G7 design note (early supply cap, fact 1.5-2): MINT-6 measures the cap against issuedZat = the "
            f"subsidy since startHeight, so it starts at zero and grows by {_subsidy_yec(cur.params):g} "
            "YEC a block; "
            "the cap also "
            "counts class-A supply, which bypasses it at σ = 1 (W16/W20). In the first year a typical "
            f"${mv.get('cap.typical_mint_usd', 0):,.0f} class-B/C mint finds no headroom on {closed:.0f} "
            "days "
            f"(median path; {mv.get('cap.bc_refused_share', math.nan):.0%} of B/C attempts refused), above "
            f"max_class_closed_days = {limit:.0f}; even supplyCapBps = {best_cap.params['supplyCapBps']} "
            "leaves "
            f"{bc_best:.0f} closed days. No cap value fixes it: the fix is rule-level — an earlier issuedZat "
            "origin (e.g. the YEC supply at activation, or the subsidy since a fixed earlier height) or a "
            "cap "
            "sum that excludes cap-exempt supply. Reported for the owner; not tuned."
        )
    p_bad, tol = mv.get("halt.p_sys_bad", math.nan), mv.get("halt.sys_bad_tolerance", math.nan)
    if math.isfinite(p_bad) and math.isfinite(tol) and p_bad > tol:
        real = mv.get("halt.p_sys_bad_real", math.nan)
        notes.append(
            "G7 design note (HALT-2 level, D-RD-ORA-7): P(the price falls to 1/halt within grace) at "
            f"globalRatioHaltBps {int(cur.params['globalRatioHaltBps']):,} is {p_bad:.1%} on the bootstrap"
            + (f" and {real:.1%} on the real history (daily starts)" if math.isfinite(real) else "")
            + f" against a tolerance of {tol:.1%}. Meeting it needs a halt near 300 %, which §1.4 forbids "
            "while class C's base ratio is 300 % (halt < every base ratio) and which W16 (recapRatioBps = "
            "2 × halt, owner decision D-R-3) would turn into a 600 % recap gate — moving the 500 % soft-cap "
            "gate W20 pinned (D-R-11). The halt level is therefore owner-pinned in effect; the exposure is "
            "reported, not tuned."
        )
    ex, budget = mv.get("liq.demand_exempt_usd", 0.0), mv.get("liq.budget_usd", math.inf)
    if ex > budget:
        notes.append(
            "G7 design note (cap-exempt liquidation): the worst-day liquidation demand of class-A vaults "
            "alone "
            f"(${ex:,.0f}, p95 of the worst crash) exceeds max_depth_fraction of the p10 daily YEC volume "
            f"(${budget:,.0f}). Class A at σ = 1 sits exactly at recapRatioBps and bypasses MINT-6 (W20), so "
            "supplyCapBps cannot bound it; only a rule change (cap-exempt supply counted against its own "
            "limit) or a higher class-A ratio would. The depth figure is a placeholder unless a depth "
            "file is loaded."
        )
    return notes


def _subsidy_yec(params: Mapping) -> float:
    from ybcal.sim import supply as SUP

    return SUP.schedule_for(params).subsidy(int(params["startHeight"])) / COIN


WHAT = {
    "supplyCapBps": "the soft supply cap: YED supply ≤ capBps × issuedZat at pMint for mints below "
    "recapRatioBps (MINT-6, W20)",
    "globalRatioHaltBps": "the global collateral ratio below which minting halts (HALT-2)",
    "recapRatioBps": "the ratio at which a mint bypasses HALT-2 and the soft cap (= 2 × halt, W16)",
    "divergenceBps": "how far the fast median may sit below a slower one before minting halts (HALT-3)",
}


def explain_g7(rec: Recommendation, results: ResultTable, fams: Mapping[str, Family]) -> str:
    fam = next(f for f in fams.values() if rec.param in f.owns)
    mc = rec.metrics.get("current", {})
    mr = rec.metrics.get("recommended", {})
    verb = {"KEEP": "Keep", "CHANGE": "Change", "PROVISIONAL": "Provisionally", "BLOCKED": "Blocked —"}[
        rec.verdict
    ]
    move = f"{rec.current} → {rec.recommended}" if rec.changed else f"{rec.current}"
    if rec.verdict == "PROVISIONAL":
        move = f"change {rec.current} → {rec.recommended}" if rec.changed else f"keep {rec.current}"
    out = [
        f"**{rec.param}** — {WHAT.get(rec.param, REGISTRY[rec.param].doc)} "
        f"(rules {', '.join(REGISTRY[rec.param].rules)}). {verb} {move}."
    ]
    if rec.param == "recapRatioBps":
        out.append("Derived: recapRatioBps = 2 × globalRatioHaltBps (W16); the parent's rule decides it.")
    out.append(f"Decision rule: {rec.rule}")
    out.append(f"Binding: {rec.binding}.")
    if fam.name == "cap":
        out.append(
            "Worst-day liquidation demand of cap-bound vaults "
            f"{_fmt(mc.get('liq.demand_bound_usd'), 'usd')} at the "
            f"current cap ({_fmt(mr.get('liq.demand_bound_usd'), 'usd')} at the recommended one) against a "
            "budget "
            f"of {_fmt(mc.get('liq.budget_usd'), 'usd')}; class-A (cap-exempt) demand "
            f"{_fmt(mc.get('liq.demand_exempt_usd'), 'usd')}. B/C stay closed "
            f"{_fmt(mc.get('cap.closed_days_bc'))} days of the first year at the current cap, "
            f"{_fmt(mr.get('cap.closed_days_bc'))} at the recommended one."
        )
    elif fam.name == "halt":
        out.append(
            f"P(system bad debt within grace from the halt level) {_fmt(mc.get('halt.p_sys_bad'), 'pct')} "
            "at the "
            f"current value vs tolerance {_fmt(mc.get('halt.sys_bad_tolerance'), 'pct')} "
            f"({_fmt(mr.get('halt.p_sys_bad'), 'pct')} at the recommended one); HALT-2 fires before the true "
            f"system ratio reaches 150 % on {_fmt(mc.get('halt.recall_alarm'), 'pct')} of crash paths "
            f"(median lead {_fmt(mc.get('halt.lead_hours_p50'))} h); false HALT-2 in calm "
            f"{_fmt(mc.get('halt.false_calm'), 'pct')} of paths."
        )
    elif fam.name == "divergence":
        out.append(
            f"HALT-3 F1 {_fmt(mc.get('div.f1'))} (recall {_fmt(mc.get('div.recall'), 'pct')}, precision "
            f"{_fmt(mc.get('div.precision'), 'pct')}) at the current value; {_fmt(mr.get('div.f1'))} (recall "
            f"{_fmt(mr.get('div.recall'), 'pct')}) at the recommended one; HALT-3 in calm "
            f"{_fmt(mc.get('div.calm_halt_hours_per_year'))} h/yr at the current value."
        )
    if rec.param != "recapRatioBps":
        ns = neighbour_sentence(rec, results, fam)
        if ns:
            out.append(ns)
    s = rec.sensitivity.get("sentence")
    if s:
        out.append(s)
    dn = rec.metrics.get("design_notes") or []
    if dn and fam.name == "cap":
        out.append(f"See the {len(dn)} G7 design note(s).")
    out.append(f"Provenance: {rec.provenance}; confidence {rec.confidence}.")
    out.append(rec.klass_note)
    return " ".join(out)


def g7_figures(table: ResultTable, chosen: ParamSet, out: Path, fams: Mapping[str, Family]) -> list[Path]:
    plt, C = figure_style()
    paths: list[Path] = []
    cur = table.current()
    assert cur is not None
    md = cur.metrics.meta
    # 1. cap vs supply in the first year, and liquidation demand vs the cap
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 3.6), facecolor=C["surface"])
    for a in (a1, a2):
        a.set_facecolor(C["surface"])
        a.grid(alpha=0.2)
    days = md.get("series.days", [])
    if days:
        a1.plot(days, md.get("series.supply_usd_p50", []), color=C["blue"], lw=2, label="YED supply (median)")
        a1.plot(
            days,
            md.get("series.cap_usd_p50", []),
            color=C["orange"],
            lw=2,
            label=f"MINT-6 cap at {table.base['supplyCapBps']} bps",
        )
    a1.set_xlabel("days after startHeight", color=C["ink"])
    a1.set_ylabel("USD", color=C["ink"])
    a1.set_title("Early supply cap vs supply (fact 1.5-2)", color=C["ink"], fontsize=10)
    a1.legend(frameon=False, fontsize=7)
    rows = sorted(family_rows(table, fams["cap"]), key=lambda r: int(r.params["supplyCapBps"]))
    x = [int(r.params["supplyCapBps"]) for r in rows]
    a2.plot(
        x,
        [r.metrics.values["liq.demand_bound_usd"] for r in rows],
        color=C["blue"],
        lw=2,
        marker="o",
        ms=3,
        label="cap-bound (B/C)",
    )
    a2.plot(
        x,
        [r.metrics.values["liq.demand_exempt_usd"] for r in rows],
        color=C["green"],
        lw=2,
        marker="o",
        ms=3,
        label="cap-exempt (A)",
    )
    a2.axhline(cur.metrics.values["liq.budget_usd"], color=C["ink"], lw=1, ls="--")
    a2.axvline(int(table.base["supplyCapBps"]), color=C["muted"], lw=1, ls=":")
    a2.set_xlabel("supplyCapBps", color=C["ink"])
    a2.set_ylabel("worst-day demand, p95 (USD)", color=C["ink"])
    a2.set_title("Liquidation demand vs depth budget (dashed)", color=C["ink"], fontsize=10)
    a2.legend(frameon=False, fontsize=7)
    fig.tight_layout()
    p = out / "g7_supply_cap.png"
    fig.savefig(p, dpi=110)
    plt.close(fig)
    paths.append(p)
    # 2. halts: HALT-3 recall / F1 vs divergenceBps; HALT-2 P(sys bad) and recall vs halt
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 3.6), facecolor=C["surface"])
    for a in (a1, a2):
        a.set_facecolor(C["surface"])
        a.grid(alpha=0.2)
    rows = sorted(family_rows(table, fams["divergence"]), key=lambda r: int(r.params["divergenceBps"]))
    x = [int(r.params["divergenceBps"]) for r in rows]
    a1.plot(
        x, [r.metrics.values["div.f1"] for r in rows], color=C["blue"], lw=2, marker="o", ms=3, label="F1"
    )
    a1.plot(x, [r.metrics.values["div.recall"] for r in rows], color=C["orange"], lw=2, label="recall")
    a1.plot(x, [r.metrics.values["div.precision"] for r in rows], color=C["green"], lw=2, label="precision")
    a1.axvline(int(table.base["divergenceBps"]), color=C["muted"], lw=1, ls=":")
    a1.set_xlabel("divergenceBps", color=C["ink"])
    a1.set_title("HALT-3: crashes vs calm", color=C["ink"], fontsize=10)
    a1.legend(frameon=False, fontsize=7)
    rows = sorted(family_rows(table, fams["halt"]), key=lambda r: int(r.params["globalRatioHaltBps"]))
    x = [int(r.params["globalRatioHaltBps"]) for r in rows]
    a2.plot(
        x,
        [100 * r.metrics.values["halt.p_sys_bad"] for r in rows],
        color=C["blue"],
        lw=2,
        marker="o",
        ms=3,
        label="P(fall to 1/halt within grace) %",
    )
    a2.plot(
        x,
        [100 * r.metrics.values["halt.recall_alarm"] for r in rows],
        color=C["orange"],
        lw=2,
        label="HALT-2 before true 150 % (%)",
    )
    a2.axhline(100 * cur.metrics.values["halt.sys_bad_tolerance"], color=C["ink"], lw=1, ls="--")
    a2.axvline(int(table.base["globalRatioHaltBps"]), color=C["muted"], lw=1, ls=":")
    a2.set_yscale("symlog", linthresh=1)
    a2.set_xlabel("globalRatioHaltBps", color=C["ink"])
    a2.set_title("HALT-2 level", color=C["ink"], fontsize=10)
    a2.legend(frameon=False, fontsize=7)
    fig.tight_layout()
    p = out / "g7_halts.png"
    fig.savefig(p, dpi=110)
    plt.close(fig)
    paths.append(p)
    return paths


def make_study() -> G7Study:
    """The G7 study (``ybcal.studies.base.load_study("G7")``)."""
    return G7Study()


__all__ = ["G7Study", "design_notes", "make_study"]

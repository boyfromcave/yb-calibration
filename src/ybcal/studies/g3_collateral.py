"""G3 — collateral and classes: baseRatioBps, classMin/Max, claimThresholdBps, emergencyRatioBps (PLAN §5.3).

Owner: WP-7b. Method, metric definitions and decision rules: ``docs/studies/g3.md``.

How it works
------------
Fact 1.5-1 drives everything here: the vault script allows **no** spend but the owner's before
``lockHeight + grace`` (``script.cpp:79-90``), so a vault's collateral must cover its debt across the
whole term plus grace. The core metric is therefore WP-4's fast-path P(bad debt)
(:func:`ybcal.sim.metrics.p_bad_debt_fast`): for every start hour, path and grid term of a class, a
vault minted at the hour-mode pMint with the wallet collateral at the σ multiplier
(``Policy.sigma_mult_at``) is *bad* when its collateral is worth less than its debt at the true price
when the claim path opens.

The **hour ensemble** (:func:`ensemble`) is built once per process and parameter-independent for this
group: the four synthetic presets (``gbm``, ``merton``, ``garch``, ``regime``) — or, when
``env.data["price"]`` is a real path, a stationary block bootstrap of its hourly returns plus the
history itself — passed through WP-3's hour-mode oracle kernel (``OracleTransferKernel.ideal``,
4 sub-steps, the WP-4 default). Every candidate reuses it (common random numbers), and P(bad debt) is
memoised per (scenario, class, ratio, class bounds, grace), so a ratio sweep costs one fast-path
call per new value. Scenario values are aggregated with ``optimize.robust.aggregate`` under the
policy's ``ensemble_agg`` (default ``"worst"``, i.e. minimax over the presets).

**Long-horizon members (wave 2, D-RD-COL-1).** When ``env.data`` also holds a daily price path
(``price_daily`` / ``price_long``: the 2019-07 → daily history), two members fitted on it join the
real-data ensemble — ``daily-bootstrap`` (30-day mean blocks, demeaned) and ``daily-history`` (the
real daily path, every start date; the regime switch fitted on daily data fails the volatility-by-
horizon check and is a frontier stress only, D-RD-COL-3) — fitted on the window from
``long_window_start`` (2020-01-01: the fork-airdrop launch fall is excluded, D-RD-COL-2). The hourly
file alone has 1.5 years of start dates for a 5-year term. Daily members are priced at the σ
multiplier the *hourly* history implies (their bridged intraday noise is not market data), see
:func:`member_sigma`. The claimant study keeps the hourly members (hour-scale pClaim lag).

Two secondary studies run on populations that do not depend on the class ratios (so they too are
memoised once per threshold value):

* **Claimant incentive** (``claimThresholdBps``): matured vaults whose owner is away, opened at a
  grid of coverages above the threshold; at the first hour pClaim shows them underwater (RED-4(a)),
  the claimant's net margin — collateral less FEE-1 and the network fee, sold at the true price less
  the policy's slippage, minus the YED it burns — in bps of the debt.
* **Emergency path** (``emergencyRatioBps``): the ``crash-70-1d`` / ``crash-90-30d`` scenarios with
  the module ARMED (``aClaim`` = the true price), RED-4(b) notices persisted for
  ``emergencyPersist``, versus RED-4(a) alone: the debt left uncovered at the closure.

Shared helpers used by G4 and G9 live here: :func:`ensemble`, :func:`bad_debt`, :func:`agents_from_policy`,
:class:`Rule` / :func:`decide_rule`, :func:`compose_explanation` and the design-note format.

Design notes
------------
:func:`design_notes` returns the rule-level findings (things tuning cannot fix) as a list of dicts
``{id, title, finding, evidence, consequence, fix, params}``; each G3 Recommendation also carries the
notes that concern its parameter in ``metrics["design_notes"]``.
"""

from __future__ import annotations

import hashlib
import math
from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np

from ybcal.data import scenarios as SC
from ybcal.data import synthetic as SY
from ybcal.model import vkernels as V
from ybcal.optimize.robust import aggregate
from ybcal.optimize.sensitivity import oat_from_table, sensitivity_sentence
from ybcal.params.invariants import Context
from ybcal.params.paramset import ParamSet
from ybcal.params.registry import REGISTRY, derived_names, params_for_group
from ybcal.sim import agents as AG
from ybcal.sim import drift as DR
from ybcal.sim import engine as E
from ybcal.sim import metrics as M
from ybcal.sim import vaults as VB
from ybcal.studies import g3_horizon as H
from ybcal.studies.base import (
    Budget,
    Env,
    Metrics,
    Recommendation,
    ResultRow,
    ResultTable,
    decide_with_materiality,
    final_verdict,
)
from ybcal.studies.g1_price_windows import (
    C_CUR,
    C_GRID,
    C_REC,
    C_TEXT,
    C_TRUE,
    data_fingerprint,
    evidence_dir,
    out_dir_of,
    plot_style,
    real_model,
    real_price,
)
from ybcal.units import BLOCKS_PER_DAY, BLOCKS_PER_HOUR, BPS, COIN

OWNER_WP = "WP-7b"

GROUP = "G3"
CLASS_NAMES = ("A", "B", "C")
SYNTHETIC_PRESETS = ("gbm", "merton", "garch", "regime")
REAL_MEMBERS = ("bootstrap", "history")
#: Members fitted on the daily history when one is supplied (D-RD-COL-1).
#: The daily regime switch is not one: it misses the data's 7-day..1-year volatility by +36..+110 %
#: (no multi-day mean reversion) and stays a stress row of the frontier (D-RD-COL-3).
DAILY_MEMBERS = ("daily-bootstrap", "daily-history")
CRASH_SCENARIOS = ("crash-70-1d", "crash-90-30d")
#: Hour-mode kernel sub-steps (WP-4 default, D-WP4-4: within 0.1 pp of 12 sub-steps).
HOUR_SUBSTEPS = VB.DEFAULT_HOUR_SUBSTEPS
#: The oracle/σ fields the hour series depends on (the ensemble memo key).
ORACLE_KEYS = (
    "pFastWindow",
    "pMidWindow",
    "pSlowWindow",
    "pFastMinFill",
    "pMidMinFill",
    "pSlowMinFill",
    "volWindow",
    "volStep",
    "volPeriodsPerYear",
    "sigmaRefBps",
    "sigmaMultMaxBps",
    "divergenceBps",
)
#: Debt of every test vault (the fast path's default; FEE-1 is proportional above the floor).
TEST_CENTS = 100_000
_BIG = np.iinfo(np.int64).max // 4

#: Judgement constants without a policy key (read through :func:`pget`, so a policy key of the same
#: name overrides them; listed in docs/studies/g3.md and docs/decisions.md D-WP7b-*).
JUDGEMENT: dict[str, Any] = {
    "ensemble_agg": "worst",  # aggregate of a loss over ensemble members (optimize.robust)
    "claim_coverages": (1.05, 1.15, 1.3, 1.6),  # claim study: coverage at claim open, × threshold
    "claim_stride_hours": 168,  # claim study: one claim-open hour per week of each path
    "claim_horizon_days": 365,  # claim study: first crossing must happen within a year
    # claim study (D-RD-COL-6): who claims and how sure. At YEC's liquidity (bid depth ≈ $120 within
    # 2 %, volume p10 ≈ $700/day) selling a $1,000 vault's collateral costs 20 %+ and a $10,000 one
    # cannot be sold at all, so the claimant that exists is a YEC holder who keeps the collateral
    # ("hold"; "sell" = the pre-wave-2 seller into the book), and the threshold must make ~90 % of
    # triggered claims pay ("p10"; "mean_bps" = the pre-wave-2 rule)
    "claimant_model": "hold",
    "claim_margin_stat": "p10_bps",
    "emergency_coverages": (1.02, 1.1, 1.25, 1.5),  # emergency study: coverage at crash start, × threshold
    "emergency_open_hours": 48,  # emergency study: vaults are claimable from this hour (medians full)
    "emergency_stress_premium_bps": -2000,  # YED price, emergency study's depeg stress row (D-RD-AUD-4)
    "long_window_start": "2020-01-01",  # daily members' fit window start (D-RD-COL-2)
    "long_block_days": 30,  # daily bootstrap mean block (D-RD-COL-1)
    "boundary_days_quick": {0: (60, 120), 1: (270, 548)},
    "boundary_days_full": {0: (45, 60, 75, 120, 150, 180), 1: (180, 270, 450, 548, 730)},
}


def pget(policy: Any, key: str, default: Any = None) -> Any:
    """``policy.<key>`` when the Policy has it, else :data:`JUDGEMENT` (else ``default``)."""
    v = getattr(policy, key, None)
    if v is not None:
        return v
    return JUDGEMENT.get(key, default)


# ===================================================================================================
# Budget knobs


def paths_per_scenario(budget: Budget) -> int:
    """Hour-mode paths per ensemble member: ``budget.paths // 4`` (quick 16, standard 100, deep 500),
    at least 2."""
    return max(2, int(budget.paths) // 4)


def start_stride_hours(budget: Budget) -> int:
    """Start-hour stride of the fast path: quick 24 h, standard 6 h, deep 2 h."""
    return {"quick": 24, "standard": 6, "deep": 2}.get(budget.name, 24)


def terms_per_class(budget: Budget) -> int:
    """Equal-probability term grid per class: quick 8, standard / deep 16."""
    return 8 if budget.name == "quick" else 16


def horizon_years(budget: Budget) -> float:
    return float(min(6.0, max(1.0, budget.hour_horizon_years)))


# ===================================================================================================
# The hour ensemble


@dataclass
class Ensemble:
    """Hourly true prices and hour-mode oracle outputs per ensemble member."""

    key: tuple
    names: tuple[str, ...]
    true: dict[str, np.ndarray]
    hours: dict[str, E.HourSeries]
    provenance: str
    meta: dict = field(default_factory=dict)

    def sigma_median(self, name: str, skip: int) -> float:
        s = self.hours[name].sigma_mult_bps[:, skip:]
        return float(np.median(s)) if s.size else float(BPS)


_ENS: OrderedDict[tuple, Ensemble] = OrderedDict()
_BD: dict[tuple, M.FastBadDebt] = {}
_CLAIM: dict[tuple, dict[int, dict[str, float]]] = {}
_EMERG: dict[tuple, dict[int, dict[str, float]]] = {}


def clear_caches() -> None:
    """Drop every per-process memo (tests)."""
    _ENS.clear()
    _BD.clear()
    _CLAIM.clear()
    _EMERG.clear()


def _real_hourly(env: Env) -> np.ndarray | None:
    """The real price path as one hourly int64 row (gaps forward-filled), or ``None``."""
    rp = real_price(env)
    if rp is None:
        return None
    from ybcal.data import pricepath as PP

    pp = rp if rp.resolution == "hour" else PP.resample(rp, "hour")
    x = np.asarray(pp.prices, dtype=np.int64)[0].copy()
    good = x > 0
    if not good.any():
        return None
    idx = np.where(good, np.arange(x.size), 0)
    np.maximum.accumulate(idx, out=idx)
    x = x[idx]
    x[: int(np.argmax(good))] = x[int(np.argmax(good))]
    return x


def long_daily(env: Env) -> H.Daily | None:
    """The daily long-horizon history, if one is loaded: ``env.data["price_daily"]`` (or
    ``"price_long"``) as a :class:`ybcal.studies.g3_horizon.Daily` or a real :class:`PricePath` whose
    observations are daily (the loader's forward-filled hourly grid). ``None`` otherwise."""
    from ybcal.data.pricepath import PricePath

    if not isinstance(env.data, Mapping):
        return None
    for k in ("price_daily", "price_long"):
        v = env.data.get(k)
        if isinstance(v, H.Daily) and len(v) > 30:
            return v
        if isinstance(v, PricePath) and v.provenance == "real":
            d = H.daily_of(v)
            if d is not None and len(d) > 30:
                return d
    return None


def hourly_members(ens: Ensemble) -> tuple[str, ...]:
    """The members on hourly data (synthetic presets, the hourly bootstrap and history): the studies
    that read hour-scale dynamics (claimant timing, the σ multiplier) use these only."""
    return tuple(m for m in ens.names if not m.startswith("daily-"))


def member_sigma(ens: Ensemble, name: str, sigma: Any, skip: int) -> Any:
    """The σ choice for one member: as given, except that a daily member under ``"median"`` / ``"p90"``
    takes that quantile of the *hourly* history's multiplier (an int): a Brownian bridge spreads each
    daily return over the day as white noise, which the hour-mode σ̂ reads as 200 %+ volatility and
    a multiplier the real market does not show (DN5: the real median is 10,000 bps)."""
    if not name.startswith("daily-") or "history" not in ens.hours or isinstance(sigma, int):
        return sigma
    q = {"median": 50, "p90": 90}.get(str(sigma))
    if q is None:
        return sigma
    s = ens.hours["history"].sigma_mult_bps[:, skip:]
    return int(np.percentile(s, q)) if s.size else sigma


def _hourly_filled(rp) -> np.ndarray | None:
    """The loader's ``filled`` mask of ``rp`` on the hourly grid (``None`` if it carries none)."""
    if rp is None:
        return None
    from ybcal.data import pricepath as PP

    pp = rp if rp.resolution == "hour" else PP.resample(rp, "hour")
    f = pp.meta.get("filled")
    if not isinstance(f, np.ndarray):
        return None
    f = np.asarray(f, dtype=bool)
    return f if f.ndim == 1 else f[0]


def ensemble(env: Env, params: Mapping) -> Ensemble:
    """The hour ensemble for ``env`` (memoised per process on seed, budget, data and oracle params).

    Synthetic: the four presets, ``paths_per_scenario`` paths each over ``horizon_years``.
    Real data: ``bootstrap`` (stationary block bootstrap of the real hourly returns, one-week mean
    block, started at the last real price) and ``history`` (the real path itself)."""
    P = paths_per_scenario(env.budget)
    years = horizon_years(env.budget)
    okey = tuple(int(params[k]) for k in ORACLE_KEYS)
    drift = DR.check(pget(env.policy, "price_drift", DR.DEFAULT_DRIFT))
    daily = long_daily(env)
    lkey = (
        None
        if daily is None
        else (
            hashlib.sha1(np.ascontiguousarray(daily.price).tobytes()).hexdigest()[:16],
            str(pget(env.policy, "long_window_start")),
            float(pget(env.policy, "long_block_days")),
        )
    )
    key = (env.seed, P, years, data_fingerprint(env), okey, drift, lkey)
    hit = _ENS.get(key)
    if hit is not None:
        _ENS.move_to_end(key)
        return hit
    n = round(years * 8760) + 1
    real = _real_hourly(env)
    true: dict[str, np.ndarray] = {}
    drifts: dict[str, dict] = {}
    if real is not None:
        from ybcal.data.pricepath import make_path

        rp = real_price(env)
        meta = {}
        filled = _hourly_filled(rp)
        if filled is not None and filled.shape[-1] == real.shape[0]:
            # keep the loader's forward-fill mask: returns are fitted observed to observed at the
            # native step, so a daily series on the hourly grid is not emptied by the stale-run
            # filter (D-RD-INF-1)
            meta["filled"] = filled
        pp = make_path(rp.t0, "hour", real[None, :], "real", meta)  # type: ignore[union-attr]
        model = real_model(env, pp)
        true["bootstrap"] = _drifted_prices(
            model, P, n, env.rng_for("ybcal-hour-ensemble", "bootstrap"), drift, int(real[-1])
        )
        drifts["bootstrap"] = DR.describe(drift, model)
        # the realised history keeps its own drift: it is what happened, not a model
        true["history"] = real[None, :].astype(np.int64)
        if daily is not None:
            dw = daily.window(str(pget(env.policy, "long_window_start")), None)
            for name in DAILY_MEMBERS:
                if name == "daily-history":
                    true[name] = H.daily_to_hourly(dw.price)
                    continue
                blk = int(pget(env.policy, "long_block_days"))
                model = H.fit_member(f"bootstrap-{blk}d", dw)
                true[name] = _drifted_prices(
                    model, P, n, env.rng_for("ybcal-hour-ensemble", name), drift, int(real[-1])
                )
                drifts[name] = DR.describe(drift, model)
        prov = "real-data"
    else:
        for name in SYNTHETIC_PRESETS:
            model = SY.preset(name)
            true[name] = _drifted_prices(model, P, n, env.rng_for("ybcal-hour-ensemble", name), drift)
            drifts[name] = DR.describe(drift, model)
        prov = "synthetic"
    kernel = E.OracleTransferKernel.ideal(params, substeps=HOUR_SUBSTEPS)
    hours = {name: E.simulate_hours(params, tp, kernel) for name, tp in true.items()}
    ens = Ensemble(
        key,
        tuple(true),
        true,
        hours,
        prov,
        meta={
            "paths": P,
            "years": years,
            "kernel": f"ideal/{HOUR_SUBSTEPS}",
            "price_drift": drift,
            "drifts": drifts,
        },
    )
    _ENS[key] = ens
    while len(_ENS) > 2:
        _ENS.popitem(last=False)
    return ens


def _drifted_prices(
    model: SY.PriceModel, P: int, n: int, rng: np.random.Generator, drift: str, p0: int = SY.DEFAULT_P0
) -> np.ndarray:
    """``model.simulate(P, n, "hour", rng, p0)`` with the log returns shifted to the ``drift``
    convention (:mod:`ybcal.sim.drift`; same draws, so ``"model"`` reproduces ``simulate`` exactly)."""
    dt = SY.as_dt("hour")
    r = model.log_returns(P, n, dt, rng)[:, : n - 1]
    r = DR.apply(r, dt, drift, model)
    return np.asarray(SY.returns_to_path(r, p0, dt).prices, dtype=np.int64)


def warmup_hours(params: Mapping) -> int:
    """σ and slow-median warm-up the fast path skips (``(volWindow + pSlowWindow) / 48``)."""
    return math.ceil((int(params["volWindow"]) + int(params["pSlowWindow"])) / BLOCKS_PER_HOUR)


def bad_debt(
    ens: Ensemble,
    name: str,
    params: Mapping,
    c: int,
    *,
    sigma: Any,
    term_distribution: str,
    n_terms: int,
    stride: int,
    graces: Sequence[int] = (),
) -> M.FastBadDebt:
    """:func:`ybcal.sim.metrics.p_bad_debt_fast` for one member and one class, memoised on what it reads
    (the class's ratio and bounds, grace, feeMin, the σ choice, the term grid and extra graces)."""
    gl = tuple(sorted({int(g) for g in graces}))
    key = (
        ens.key,
        name,
        c,
        int(params[f"baseRatioBps[{c}]"]),
        int(params[f"classMin[{c}]"]),
        int(params[f"classMax[{c}]"]),
        int(params["grace"]),
        int(params["feeMin"]),
        str(sigma),
        term_distribution,
        n_terms,
        stride,
        gl,
    )
    hit = _BD.get(key)
    if hit is None:
        hit = M.p_bad_debt_fast(
            params,
            ens.true[name],
            hour_series=ens.hours[name],
            classes=(c,),
            n_terms=n_terms,
            term_distribution=term_distribution,
            sigma=sigma,
            cents=TEST_CENTS,
            graces=gl or None,
            start_stride=stride,
        )
        _BD[key] = hit
    return hit


def term_frontier_days(by_term: Sequence[np.ndarray], terms: Any, tol: float, how: str) -> float:
    """The longest grid term (days) whose P(bad debt), aggregated over members like ``pbad``, is within
    ``tol`` while every shorter grid term is too: how far the class could reach at this ratio. 0 when
    even the shortest term fails; NaN without data (D-RD-AUD-3)."""
    if terms is None or not len(by_term):
        return math.nan
    t = np.asarray(terms, dtype=float)
    out = 0.0
    seen = False
    for i in range(t.size):
        v = agg([float(b[i]) for b in by_term if i < b.size], how)
        if not math.isfinite(v):
            continue
        seen = True
        if v > tol:
            break
        out = float(t[i]) / BLOCKS_PER_DAY
    return out if seen else math.nan


def agg(values: Sequence[float], how: str, *, minimize: bool = True) -> float:
    """Robust aggregate of one metric over ensemble members (NaN members dropped; NaN if none)."""
    v = np.asarray([x for x in values if np.isfinite(x)], dtype=float)
    if not v.size:
        return math.nan
    return float(aggregate(v[None, :], how, minimize=minimize)[0])  # type: ignore[arg-type]


def heterogeneity(by_term: np.ndarray) -> float:
    """Within-class spread of P(bad debt) across the term grid: (mean of the longest quarter of terms −
    mean of the shortest quarter) / class mean; 0 when the class mean is 0, NaN without data."""
    x = np.asarray(by_term, dtype=float)
    if not np.isfinite(x).any():
        return math.nan
    q = max(1, x.size // 4)
    lo, hi, m = np.nanmean(x[:q]), np.nanmean(x[-q:]), np.nanmean(x)
    if not (np.isfinite(lo) and np.isfinite(hi)):
        return math.nan
    return 0.0 if m <= 0 else float((hi - lo) / m)


def agents_from_policy(policy: Any) -> AG.AgentsConfig:
    """``AgentsConfig.from_policy`` plus the ``[agents]`` keys (D-WP4-3 resolution): YED premium,
    claimant slippage, defector share, lost-key probability."""
    from dataclasses import replace

    cfg = AG.AgentsConfig.from_policy(policy)
    owner = replace(
        cfg.owner,
        lost_key_prob=float(getattr(policy, "lost_key_prob", 0.0)),
        defector_share=float(getattr(policy, "defector_share", 0.0)),
    )
    claimant = replace(cfg.claimant, slippage_bps=int(getattr(policy, "claimant_slippage_bps", 100)))
    market = AG.YedMarket(premium_bps=int(getattr(policy, "yed_premium_bps", 0)))
    return cfg.replace(owner=owner, claimant=claimant, market=market)


def depth_p10_usd(env: Env | None, pctl: float = 90.0) -> float | None:
    """The bad-side percentile (``100 − claimant_slippage_pctl``) of the order-book depth a claimant
    selling YEC meets — the **bid** side within 2 % of mid (D-RD-COL-5: the two-sided figure counts
    asks a seller never touches, 1.4× the bids on the 2026-10 books) — if a depth CSV is loaded in
    ``env.data["depth"]``; half the two-sided depth when the file has no bid column."""
    if env is None:
        return None
    d = env.data.get("depth") if isinstance(env.data, Mapping) else None
    bid = getattr(d, "bid_depth_2pct_usd", None)
    if bid is not None and np.isfinite(np.asarray(bid, dtype=float)).any():
        arr = bid
    else:
        two = getattr(d, "depth_2pct_usd", None)
        arr = None if two is None else np.asarray(two, dtype=float) / 2
    if arr is None:
        return None
    a = np.asarray(arr, dtype=float)
    a = a[np.isfinite(a) & (a > 0)]
    return float(np.percentile(a, 100.0 - pctl)) if a.size else None


# ===================================================================================================
# Claimant incentive (claimThresholdBps)


class FirstBelow:
    """Sparse table over one series: :meth:`first` gives the first ``t ≥ t0`` with ``x[t] < level``
    (``n`` when none), for any number of queries in O(log n) numpy steps."""

    def __init__(self, x: np.ndarray):
        x = np.asarray(x)
        self.n = int(x.shape[0])
        self.lv = [x]
        k = 1
        while (1 << k) <= self.n:
            prev, h = self.lv[-1], 1 << (k - 1)
            self.lv.append(np.minimum(prev[: self.n - (1 << k) + 1], prev[h : h + self.n - (1 << k) + 1]))
            k += 1

    def first(self, t0: np.ndarray, level: np.ndarray) -> np.ndarray:
        pos = np.clip(np.asarray(t0, dtype=np.int64), 0, self.n).copy()
        lvl = np.asarray(level)
        for k in range(len(self.lv) - 1, -1, -1):
            size = 1 << k
            fits = pos + size <= self.n
            v = self.lv[k][np.minimum(pos, self.n - size)]
            pos = pos + np.where(fits & (v >= lvl), size, 0)
        return pos


def _coll_for_coverage(cov: np.ndarray, price: np.ndarray, cents: int = TEST_CENTS) -> np.ndarray:
    """Collateral (zat, ≥ 1) worth ``cov`` × the debt at ``price`` µUSD/YEC."""
    return np.maximum(1, np.ceil(cov * (cents / 100) / (price / 1e6) * COIN)).astype(np.int64)


def _underwater_level(coll: np.ndarray, threshold_bps: int, cents: int = TEST_CENTS) -> np.ndarray:
    """``L`` with ``coll · p < cents · threshold · 10^8  ⇔  p < L`` for integer p (exact, int64)."""
    x = np.int64(cents) * np.int64(threshold_bps) * np.int64(10**8)
    return -(-x // np.asarray(coll, dtype=np.int64))


def _margin_bps(
    params: Mapping,
    coll: np.ndarray,
    price: np.ndarray,
    *,
    slip_base: float,
    depth: float | None,
    impact_bps: float,
    premium_bps: float,
    tx_fee: int,
    cents: int = TEST_CENTS,
) -> np.ndarray:
    """Claimant net margin (bps of the debt): (collateral − FEE-1 − network fee) sold at ``price`` less
    slippage, minus the debt bought in YED at ``1 + premium``."""
    fee = V.fee_zat(coll, int(params["feeMin"]), int(params["feeBps"]))
    net = np.maximum(coll - fee - tx_fee, 0).astype(float)
    gross = net / COIN * np.asarray(price, dtype=float) / 1e6
    slip = np.full(gross.shape, float(slip_base))
    if depth:
        slip = slip + impact_bps * gross / depth
    slip = np.minimum(slip, 9_999.0)
    debt = cents / 100
    return (gross * (1 - slip / BPS) / debt - (1 + premium_bps / BPS)) * BPS


def claim_grid(params: Mapping) -> list[int]:
    """Every claimThresholdBps the claim study scores (registry lattice anchored on the current value)."""
    spec = REGISTRY["claimThresholdBps"]
    lo, hi = spec.bounds
    cur = int(params["claimThresholdBps"])
    vals = {cur}
    v = cur
    while v - spec.step >= lo:
        v -= spec.step
        vals.add(v)
    v = cur
    while v + spec.step <= hi:
        v += spec.step
        vals.add(v)
    return sorted(vals)


def claim_stats(
    ens: Ensemble,
    params: Mapping,
    policy: Any,
    env_depth: float | None = None,
    *,
    cents: int = TEST_CENTS,
    sell: bool = True,
) -> dict[int, dict]:
    """Claimant economics at every threshold of :func:`claim_grid`, per ensemble member (memoised).

    Population: claim-open hours ``o`` every ``claim_stride_hours`` after warm-up; at each, vaults with
    coverage ``c0 × threshold`` at the true price (``claim_coverages``) whose owner is away. The claim
    trigger is the first hour ``h ≥ o`` (within ``claim_horizon_days``) with the vault underwater at
    pClaim (RED-4(a), exact integer level); the claimant's margin is :func:`_margin_bps` at the true
    price of ``h``. ``cents`` is the vault's debt (liquidity impact scales with it); ``sell=False``
    scores a claimant who keeps the YEC (a YEC holder: no slippage, value at the true price).
    Returns ``{theta: {"<member>.<stat>": value}}`` with stats ``mean_bps``,
    ``p10_bps``, ``p50_bps``, ``show_up`` (share with margin ≥ ``claimant_min_profit_bps``), ``bad``
    (share already bad debt at the trigger), ``forfeit_bps`` (mean collateral value above the debt the
    absent owner loses — RED-5 pays no residual under (a)) and ``n``."""
    cfg = agents_from_policy(policy)
    covs = tuple(float(c) for c in pget(policy, "claim_coverages"))
    stride = int(pget(policy, "claim_stride_hours"))
    horizon = int(float(pget(policy, "claim_horizon_days")) * 24)
    thetas = claim_grid(params)
    key = (
        ens.key,
        int(params["feeMin"]),
        int(params["feeBps"]),
        cfg.claimant.slippage_bps,
        cfg.market.premium_bps,
        int(policy.claimant_min_profit_bps),
        covs,
        stride,
        horizon,
        env_depth,
        tuple(thetas),
        int(cents),
        bool(sell),
    )
    hit = _CLAIM.get(key)
    if hit is not None:
        return hit
    skip = warmup_hours(params)
    out: dict[int, dict] = {t: {} for t in thetas}
    min_profit = float(policy.claimant_min_profit_bps)
    for name in hourly_members(ens):
        tp = ens.true[name]
        pc = ens.hours[name].p_claim
        Pn, n = tp.shape
        opens = np.arange(skip, n - 1, max(1, stride))
        acc: dict[int, list[np.ndarray]] = {t: [] for t in thetas}
        for p in range(Pn):
            if not opens.size:
                break
            fb = FirstBelow(np.where(pc[p] > 0, pc[p], _BIG))
            po = tp[p, opens]
            for t in thetas:
                for c0 in covs:
                    coll = _coll_for_coverage(np.full(opens.shape, c0 * t / BPS), po, cents)
                    h = fb.first(opens, _underwater_level(coll, t, cents))
                    ok = (h < n) & (h - opens <= horizon)
                    if not ok.any():
                        continue
                    price = tp[p, h[ok]]
                    m = _margin_bps(
                        params,
                        coll[ok],
                        price,
                        slip_base=cfg.claimant.slippage_bps if sell else 0.0,
                        depth=env_depth if sell else None,
                        impact_bps=cfg.claimant.impact_bps_at_depth,
                        premium_bps=cfg.market.premium_bps,
                        tx_fee=cfg.tx_fee_zat,
                        cents=cents,
                    )
                    value = coll[ok] / COIN * price / 1e6 / (cents / 100)
                    acc[t].append(np.stack([m, value]))
        for t in thetas:
            if acc[t]:
                a = np.concatenate(acc[t], axis=1)
                m, value = a[0], a[1]
                out[t].update(
                    {
                        f"{name}.mean_bps": float(m.mean()),
                        f"{name}.p10_bps": float(np.percentile(m, 10)),
                        f"{name}.p50_bps": float(np.percentile(m, 50)),
                        f"{name}.show_up": float((m >= min_profit).mean()),
                        f"{name}.bad": float((value < 1).mean()),
                        f"{name}.forfeit_bps": float(np.maximum(value - 1, 0).mean() * BPS),
                        f"{name}.n": float(m.size),
                    }
                )
            else:
                out[t].update(
                    {
                        f"{name}.{s}": math.nan
                        for s in ("mean_bps", "p10_bps", "p50_bps", "show_up", "bad", "forfeit_bps")
                    }
                )
                out[t][f"{name}.n"] = 0.0
    _CLAIM[key] = out
    return out


# ===================================================================================================
# Emergency path (emergencyRatioBps)


def emergency_grid(params: Mapping) -> list[int]:
    """Every emergencyRatioBps on the registry lattice strictly between 10,000 and claimThresholdBps."""
    spec = REGISTRY["emergencyRatioBps"]
    lo, hi = spec.bounds
    cur, theta = int(params["emergencyRatioBps"]), int(params["claimThresholdBps"])
    vals = [
        v for v in range(cur - spec.step * ((cur - lo) // spec.step), hi + 1, spec.step) if 10_000 < v < theta
    ]
    return sorted(set(vals) | ({cur} if cur < theta else set()))


@dataclass
class CrashRuns:
    key: tuple
    names: tuple[str, ...]
    true: dict[str, np.ndarray]
    hours: dict[str, E.HourSeries]


_CRASH: dict[tuple, CrashRuns] = {}


def crash_runs(env: Env, params: Mapping) -> CrashRuns:
    """``crash-70-1d`` and ``crash-90-30d`` at hour resolution through the same hour-mode kernel."""
    P = paths_per_scenario(env.budget)
    key = (env.seed, P, tuple(int(params[k]) for k in ORACLE_KEYS))
    hit = _CRASH.get(key)
    if hit is not None:
        return hit
    kernel = E.OracleTransferKernel.ideal(params, substeps=HOUR_SUBSTEPS)
    true, hours = {}, {}
    for name in CRASH_SCENARIOS:
        scen = env.scenarios.get(name) if isinstance(env.scenarios, Mapping) else None
        scen = scen if isinstance(scen, SC.Scenario) else SC.get(name)
        run = scen.generate(env.rng_for("ybcal-hour-crash", name), n_paths=P, resolution="hour")
        true[name] = np.asarray(run.paths.prices, dtype=np.int64)
        hours[name] = E.simulate_hours(params, true[name], kernel)
    cr = CrashRuns(key, CRASH_SCENARIOS, true, hours)
    _CRASH.clear()
    _CRASH[key] = cr
    return cr


def _first_true(mask: np.ndarray, start: int) -> np.ndarray:
    """First index ≥ ``start`` along the last axis where ``mask`` holds (``n`` when none)."""
    n = mask.shape[-1]
    m = mask.copy()
    m[..., :start] = False
    any_ = m.any(axis=-1)
    return np.where(any_, np.argmax(m, axis=-1), n)


def _closure_cov(coll: np.ndarray, tp: np.ndarray, h: np.ndarray) -> np.ndarray:
    """Coverage (collateral value / debt) at the true price of hour ``h`` (``(P, C)``, clipped to the
    horizon)."""
    hh = np.minimum(h, tp.shape[1] - 1)
    return coll / COIN * np.take_along_axis(tp, hh, axis=1) / 1e6 / (TEST_CENTS / 100)


def emergency_stats(cr: CrashRuns, params: Mapping, policy: Any) -> dict[int, dict]:
    """Uncovered debt at closure with and without RED-4(b), at every emergency ratio of
    :func:`emergency_grid` (memoised). Vaults: matured, owner away, coverage ``c0 × threshold`` at the
    true price at hour ``emergency_open_hours`` (``emergency_coverages``). RED-4(a) closes at the first
    hour pClaim shows the vault underwater at the threshold **and** the claimant's margin clears
    ``claimant_min_profit_bps``; RED-4(b) (ARMED, aClaim = the true price, so pEmerg = min(pClaim,
    true)) at the first hour pEmerg has shown it underwater at the emergency ratio for
    ``ceil(emergencyPersist / 48)`` consecutive hours **and** the exit pays: a RED-4(b) claimant burns
    the debt in YED bought at ``1 + premium`` and receives the debt's worth at pClaim, sold at the true
    price less the claimant slippage, so it acts only when ``(1 − true/pClaim)·10⁴ + slippage ≤
    −premium_bps`` (D-RD-AUD-4: at the policy's working peg, premium 0, a (b) closure is a loss and
    nobody executes it; the stress row ``*_stress`` repeats the test at
    ``emergency_stress_premium_bps``, a YED discount). ``shortfall`` = mean max(0, 1 − collateral
    value / debt) at the closure (at the horizon end if never closed). Returns ``{e: {"<scen>.<stat>":
    …}}`` with ``shortfall``, ``shortfall_a``, ``b_share``, ``b_par_loss_bps`` (the exit's loss,
    1 − true/pClaim, over the (b) triggers whether or not executed) and ``shortfall_stress`` /
    ``b_share_stress``."""
    cfg = agents_from_policy(policy)
    covs = tuple(float(c) for c in pget(policy, "emergency_coverages"))
    o = int(pget(policy, "emergency_open_hours"))
    theta = int(params["claimThresholdBps"])
    persist_h = max(1, math.ceil(int(params["emergencyPersist"]) / BLOCKS_PER_HOUR))
    grid = emergency_grid(params)
    key = (
        cr.key,
        theta,
        persist_h,
        int(params["feeMin"]),
        int(params["feeBps"]),
        covs,
        o,
        cfg.claimant.slippage_bps,
        cfg.market.premium_bps,
        int(policy.claimant_min_profit_bps),
        tuple(grid),
        int(pget(policy, "emergency_stress_premium_bps")),
    )
    stress = int(pget(policy, "emergency_stress_premium_bps"))
    hit = _EMERG.get(key)
    if hit is not None:
        return hit
    out: dict[int, dict] = {e: {} for e in grid}
    for name in cr.names:
        tp = cr.true[name]
        pc = cr.hours[name].p_claim
        Pn, n = tp.shape
        oo = min(o, n - 1)
        pcx = np.where(pc > 0, pc, _BIG)
        cov = np.asarray(covs)[None, :] * theta / BPS  # (1, C)
        coll = _coll_for_coverage(np.broadcast_to(cov, (Pn, len(covs))), tp[:, oo][:, None])  # (P, C)
        c3 = coll[:, :, None]
        tp3 = tp[:, None, :]
        under_a = pcx[:, None, :] < _underwater_level(coll, theta)[:, :, None]
        marg = _margin_bps(
            params,
            np.broadcast_to(c3, (Pn, len(covs), n)),
            np.broadcast_to(tp3, (Pn, len(covs), n)),
            slip_base=cfg.claimant.slippage_bps,
            depth=None,
            impact_bps=0.0,
            premium_bps=cfg.market.premium_bps,
            tx_fee=cfg.tx_fee_zat,
        )
        ha = _first_true(under_a & (marg >= float(policy.claimant_min_profit_bps)), oo)
        pem = np.minimum(pcx, tp)[:, None, :]
        # the (b) claimant's loss per hour (bps of the debt) before slippage: 1 − true/pClaim
        exit_loss = ((1 - tp / pcx) * BPS)[:, None, :]

        def pays(premium: int, loss: np.ndarray = exit_loss) -> np.ndarray:
            return loss + cfg.claimant.slippage_bps <= -premium

        short_a = np.maximum(0.0, 1 - _closure_cov(coll, tp, ha)).mean()
        for e in grid:
            m = pem < _underwater_level(coll, e)[:, :, None]
            run = np.ones_like(m)
            for k in range(persist_h + 1):
                run[..., k:] &= m[..., : n - k] if k else m
                run[..., :k] = False
            trig = _first_true(run, oo + persist_h)
            trig_b = (trig < ha) & (trig < n)
            if trig_b.any():
                pi = np.nonzero(trig_b)[0]
                hh = trig[trig_b]
                par = float(((1 - tp[pi, hh] / pcx[pi, hh]) * BPS).mean())
            else:
                par = math.nan
            row = {f"{name}.shortfall_a": float(short_a), f"{name}.b_par_loss_bps": par}
            for tag, prem in (("", cfg.market.premium_bps), ("_stress", stress)):
                hb = _first_true(run & pays(prem), oo + persist_h)
                h = np.minimum(ha, hb)
                isb = (hb < ha) & (hb < n)
                row[f"{name}.shortfall{tag}"] = float(np.maximum(0.0, 1 - _closure_cov(coll, tp, h)).mean())
                row[f"{name}.b_share{tag}"] = float(isb.mean())
            out[e].update(row)
    _EMERG[key] = out
    return out


# ===================================================================================================
# Rules (shared with G4 / G9)


_DERIVED = frozenset(derived_names())


@dataclass(frozen=True)
class Rule:
    """One decision rule over one slice of the candidate table.

    Rows whose delta ⊆ ``varies`` (+ derived names) belong to it — the base row always does. Metrics
    are re-projected onto ``primary`` and the rule's ``constraints``. ``kind``: ``optimize`` (primary +
    materiality via ``decide_with_materiality``) or ``verify`` (KEEP while every constraint holds,
    else the nearest feasible row). When nothing is feasible the verdict is BLOCKED and the row is the
    *least violating* one (sum of ``viol.<constraint>`` values, ties toward current) — or the current
    row when ``blocked_keeps_current``."""

    name: str
    owns: tuple[str, ...]
    varies: tuple[str, ...]
    text: str
    primary: str
    minimize: bool = True
    constraints: tuple[str, ...] = ()
    kind: Literal["optimize", "verify"] = "optimize"
    blocked_keeps_current: bool = False
    report: tuple[str, ...] = ()
    sens_metric: str | None = None
    provenance_key: str = "provenance"


@dataclass(frozen=True)
class RuleDecision:
    row: ResultRow
    verdict: Literal["KEEP", "CHANGE", "BLOCKED"]
    reason: str
    least: ResultRow | None = None
    improvement: float = 0.0


def rule_table(table: ResultTable, rule: Rule) -> ResultTable:
    allowed = set(rule.varies) | _DERIVED
    out = ResultTable(table.base)
    for r in table:
        if not set(r.delta) <= allowed:
            continue
        m = r.metrics
        cons = {c: bool(m.constraints.get(c, True)) for c in rule.constraints}
        vals = dict(m.values)
        out.rows.append(
            ResultRow(
                r.delta, r.params, Metrics(vals, rule.primary, rule.minimize, cons, m.provenance, m.meta)
            )
        )
    return out


def violation_score(row: ResultRow, constraints: Iterable[str]) -> float:
    return float(sum(max(0.0, float(row.metrics.values.get(f"viol.{c}", 0.0) or 0.0)) for c in constraints))


def decide_rule(table: ResultTable, rule: Rule, policy: Any) -> RuleDecision:
    """Apply one :class:`Rule` (see its docstring)."""
    sub = rule_table(table, rule)
    cur = sub.current()
    if cur is None:
        raise ValueError(f"rule {rule.name}: the current set was not evaluated")
    feas = sub.feasible()
    if not len(feas):
        least = min(sub.rows, key=lambda r: (violation_score(r, rule.constraints), sub.distance(r)))
        row = cur if rule.blocked_keeps_current else least
        return RuleDecision(
            row,
            "BLOCKED",
            f"no candidate satisfies {', '.join(rule.constraints)}; least violating: {_delta_str(least)}",
            least,
        )
    if rule.kind == "verify":
        if cur.metrics.feasible:
            return RuleDecision(cur, "KEEP", "every constraint holds at the current value")
        best = min(feas.rows, key=lambda r: (sub.distance(r), r.metrics.primary_value))
        return RuleDecision(
            best, "CHANGE", f"current violates {', '.join(cur.metrics.violated)}; nearest feasible value"
        )
    d = decide_with_materiality(sub, policy)
    return RuleDecision(d.row, d.verdict, d.reason, None, d.improvement)  # type: ignore[arg-type]


def _delta_str(row: ResultRow) -> str:
    return ", ".join(f"{k}={v}" for k, v in row.delta.items() if k not in _DERIVED) or "current"


def fmt(v: Any) -> Any:
    """Report-friendly number: integral floats become ints (heights, blocks), others 6 significant digits."""
    if isinstance(v, float | np.floating):
        v = float(v)
        if math.isinf(v) or math.isnan(v):
            return v
        if v.is_integer() and abs(v) < 1e15:
            return int(v)
        return float(f"{v:.6g}")
    return v


def oat_sensitivity(sub: ResultTable, param: str, metric: str, name: str) -> dict:
    """The OAT slice of a rule table along ``param`` and its sentence (``{}`` with < 2 points)."""
    try:
        rows = [
            r
            for r in sub
            if set(r.delta) - _DERIVED <= {param}
            and np.isfinite(float(r.metrics.values.get(metric, math.nan)))
        ]
        if len({r.params[param] for r in rows}) < 2:
            return {}
        t = ResultTable(sub.base, rows)
        o = oat_from_table(t, param, metric=metric)
        return {
            "oat_values": o.values.tolist(),
            "oat_metric": o.metric.tolist(),
            "metric": metric,
            "classes": list(o.classes),
            "sentence": sensitivity_sentence(param, o, name),
        }
    except Exception as e:  # pragma: no cover - degenerate tables
        return {"sentence": f"sensitivity unavailable ({type(e).__name__}: {e})"}


def compose_explanation(
    rec: Recommendation,
    *,
    what: str,
    key_metrics: Sequence[tuple[str, str, str]] = (),
    extra: Sequence[str] = (),
) -> str:
    """The report paragraph: what the parameter does, the verdict, the rule, the binding constraint,
    metrics at current vs recommended, sensitivity, notes, design notes, provenance and change path."""
    spec = REGISTRY[rec.param]
    verdict = {"KEEP": "Keep", "CHANGE": "Change", "PROVISIONAL": "Provisionally", "BLOCKED": "BLOCKED —"}[
        rec.verdict
    ]
    if rec.verdict == "PROVISIONAL":
        move = f"change {rec.current} → {rec.recommended}" if rec.changed else f"keep {rec.current}"
    elif rec.verdict == "BLOCKED":
        move = (
            f"no value within the searched bounds meets the policy; least violating {rec.recommended} "
            f"(current {rec.current})"
        )
    else:
        move = f"{rec.current} → {rec.recommended}" if rec.changed else f"{rec.current}"
    parts = [f"**{rec.param}** — {what} (rules {', '.join(spec.rules) or '—'}). {verdict} {move}."]
    parts.append(f"Decision rule: {rec.rule}")
    parts.append(f"Binding: {rec.binding}.")
    cur = rec.metrics.get("current", {}) or {}
    new = rec.metrics.get("recommended", {}) or {}
    bits = []
    for label, key, f in key_metrics:
        a, b = cur.get(key), new.get(key)
        if a is None and b is None:
            continue

        def show(x: Any, f: str = f) -> str:
            try:
                return (
                    f.format(x) if x is not None and not (isinstance(x, float) and math.isnan(x)) else "n/a"
                )
            except (TypeError, ValueError):
                return str(x)

        bits.append(
            f"{label} {show(a)}" + (f" → {show(b)}" if rec.changed or rec.verdict == "BLOCKED" else "")
        )
    if bits:
        parts.append(
            "At current"
            + (" → recommended" if rec.changed or rec.verdict == "BLOCKED" else "")
            + ": "
            + "; ".join(bits)
            + "."
        )
    s = rec.sensitivity.get("sentence")
    if s:
        parts.append(s)
    parts.extend(extra)
    for dn in rec.metrics.get("design_notes", []) or []:
        parts.append(f"Design note {dn['id']}: {dn['title']} — {dn['finding']}")
    prov = {
        "synthetic": "synthetic price ensemble only, so the verdict is PROVISIONAL until real YEC data "
        "is supplied",
        "real-data": "real YEC price data",
        "judgement": "exact math under stated policy assumptions",
    }
    parts.append(f"Evidence: {prov.get(rec.provenance, rec.provenance)}; confidence {rec.confidence}.")
    parts.append(rec.klass_note)
    return " ".join(parts)


def confidence_for(prov: str, verdict: str, budget: str) -> Literal["high", "medium", "low"]:
    if verdict == "BLOCKED" or prov == "synthetic":
        return "low"
    if prov == "real-data":
        return "high" if budget in ("standard", "deep") else "medium"
    return "medium"


def lattice(
    base: int, spec_name: str, step: int | None = None, lo: int | None = None, hi: int | None = None
) -> list[int]:
    """Values ``base + k·step`` inside the registry bounds (or ``[lo, hi]``), base included."""
    spec = REGISTRY[spec_name]
    s = int(step or spec.step)
    blo, bhi = spec.bounds
    lo = blo if lo is None else max(blo, lo)
    hi = bhi if hi is None else min(bhi, hi)
    out = {int(base)}
    v = int(base)
    while v - s >= lo:
        v -= s
        out.add(v)
    v = int(base)
    while v + s <= hi:
        v += s
        out.add(v)
    return sorted(out)


def valid(ps: ParamSet) -> bool:
    """Passes every PLAN §1.4 invariant under the default context."""
    return not ps.check(Context())


# ===================================================================================================
# The study


def ratio_rule(c: int, policy: Any | None = None) -> Rule:
    name = CLASS_NAMES[c]
    return Rule(
        f"ratio_{name}",
        (f"baseRatioBps[{c}]",),
        (f"baseRatioBps[{c}]",),
        f"baseRatioBps[{c}] (class {name}) = the smallest ratio on the 2,500-bps lattice whose P(bad debt "
        f"when the claim path opens) — aggregated over the ensemble with ensemble_agg — is ≤ "
        f"max_bad_debt_prob[{name}], with the σ multiplier at sigma_mult_at; KEEP unless the smallest "
        "feasible ratio frees more than materiality of the collateral; a violating current value moves to "
        "the smallest feasible one; BLOCKED (least-violating = lowest P(bad debt)) when no ratio within "
        "the registry bounds meets the policy.",
        primary=f"ratio.{name}",
        constraints=(f"bad_debt_{name}",),
        report=(
            f"pbad.{name}",
            f"pbad_lock.{name}",
            f"es.{name}",
            f"tmax_ok_days.{name}",
            f"yed_per_usd.{name}",
            f"het.{name}",
        ),
        sens_metric=f"pbad.{name}",
    )


RULES_FIXED = (
    Rule(
        "claim",
        ("claimThresholdBps",),
        ("claimThresholdBps",),
        "claimThresholdBps = the smallest threshold (250-bps lattice) at which the claimant margin at the "
        "first RED-4(a) trigger — collateral less FEE-1 and the network fee, valued at the true price "
        "(claimant_model hold: a YEC holder keeps it; sell: sold into the bid-side depth less the policy "
        "slippage), minus the YED burned — is ≥ claimant_min_profit_bps at claim_margin_stat (p10: nine "
        "triggered claims in ten pay; worst ensemble member, D-RD-COL-6); KEEP unless more than "
        "materiality lower; BLOCKED (highest margin) when no threshold within bounds clears it.",
        primary="claim.theta",
        constraints=("claim_incentive",),
        report=(
            "claim.p10_bps",
            "claim.mean_bps",
            "claim.show_up",
            "claim.bad",
            "claim.forfeit_bps",
            "claim_sell.mean_bps",
            "claim_sell.show_up",
        ),
        sens_metric="claim.mean_bps",
    ),
    Rule(
        "emergency",
        ("emergencyRatioBps",),
        ("emergencyRatioBps",),
        "emergencyRatioBps ∈ (10,000, claimThresholdBps): minimise the debt left uncovered at closure "
        "(ARMED, RED-4(b) available) over crash-70-1d / crash-90-30d (worst scenario), counting a RED-4(b) "
        "closure only when the exit pays at the policy's YED price (yed_premium_bps; at par it never "
        "does, D-RD-AUD-4); KEEP unless the improvement exceeds materiality. The same metric under a "
        "YED discount (emergency_stress_premium_bps) is reported as emerg.shortfall_stress.",
        primary="emerg.shortfall",
        report=(
            "emerg.shortfall",
            "emerg.shortfall_a",
            "emerg.benefit",
            "emerg.b_share",
            "emerg.b_par_loss_bps",
            "emerg.shortfall_stress",
            "emerg.b_share_stress",
        ),
        sens_metric="emerg.shortfall",
    ),
    Rule(
        "boundaries",
        ("classMax[0]", "classMin[1]", "classMax[1]", "classMin[2]"),
        ("classMax[0]", "classMin[1]", "classMax[1]", "classMin[2]"),
        "Internal class boundaries (A|B at classMax[0] = classMin[1] − 1, B|C at classMax[1] = classMin[2] − "
        "1) "
        "are checked, not optimised (PLAN §5.3): each class's within-range heterogeneity of P(bad debt) — "
        "(longest-quarter − shortest-quarter term mean) / class mean, worst member — is compared with "
        "class_heterogeneity_max. Above it the study suggests a split or merge; with NUM_CLASSES = 3 fixed "
        "by the rules that is a design note, not a boundary move (shifted partitions are scored as "
        "evidence). KEEP while MINT-2 contiguity and the CLTV bound hold (D-WP7b-3).",
        primary="het.max",
        constraints=("locktime",),
        kind="verify",
        report=("het.A", "het.B", "het.C", "het.max", "pbad.A", "pbad.B", "pbad.C"),
        sens_metric="het.max",
    ),
    Rule(
        "ends",
        ("classMin[0]", "classMax[2]"),
        ("classMin[0]", "classMax[2]"),
        "Outer class ends (shortest and longest lock) are product choices: verified, not tuned — KEEP while "
        "MINT-2 contiguity and the CLTV height bound hold.",
        primary="zero",
        constraints=("locktime",),
        kind="verify",
        report=("het.A", "het.C"),
    ),
)


def rules(policy: Any | None = None) -> tuple[Rule, ...]:
    return (*(ratio_rule(c, policy) for c in range(3)), *RULES_FIXED)


@dataclass
class G3Study:
    """Collateral and classes (PLAN §5.3)."""

    group: str = GROUP
    params: tuple[str, ...] = field(default_factory=lambda: params_for_group(GROUP))

    # -- space -----------------------------------------------------------------------------------
    def space(self, base: ParamSet, budget: Budget) -> Iterable[ParamSet]:
        out = [base]
        for c in range(3):
            k = f"baseRatioBps[{c}]"
            for v in lattice(int(base[k]), k):
                if v != int(base[k]):
                    out.append(base.replace({k: v}))
        for v in claim_grid(base):
            if v != int(base["claimThresholdBps"]):
                out.append(base.replace(claimThresholdBps=v))
        for v in emergency_grid(base):
            if v != int(base["emergencyRatioBps"]):
                out.append(base.replace(emergencyRatioBps=v))
        days = JUDGEMENT["boundary_days_quick" if budget.name == "quick" else "boundary_days_full"]
        for i, ds in days.items():
            for d in ds:
                b = d * BLOCKS_PER_DAY
                if b != int(base[f"classMax[{i}]"]):
                    out.append(base.replace({f"classMax[{i}]": b, f"classMin[{i + 1}]": b + 1}))
        return [ps for i, ps in enumerate(out) if i == 0 or valid(ps)]

    # -- evaluate --------------------------------------------------------------------------------
    def evaluate(self, cand: ParamSet, env: Env) -> Metrics:
        pol = env.policy
        how = str(pget(pol, "ensemble_agg"))
        ens = ensemble(env, cand)
        b = env.budget
        stride, nt = start_stride_hours(b), terms_per_class(b)
        sigma = pol.sigma_mult_at
        skip = warmup_hours(cand)
        values: dict[str, float] = {"zero": 0.0}
        cons: dict[str, bool] = {}
        hets: list[float] = []
        for c, name in enumerate(CLASS_NAMES):
            ps_, pl_, het_, es_, bt_ = [], [], [], [], []
            terms_c = None
            for m in ens.names:
                fb = bad_debt(
                    ens,
                    m,
                    cand,
                    c,
                    sigma=member_sigma(ens, m, sigma, skip),
                    term_distribution=pol.term_distribution,
                    n_terms=nt,
                    stride=stride,
                )
                values[f"pbad.{name}.{m}"] = fb.p[name]
                values[f"pbad_lock.{name}.{m}"] = fb.p_lock[name]
                h = heterogeneity(fb.by_term[name])
                values[f"het.{name}.{m}"] = h
                ps_.append(fb.p[name])
                pl_.append(fb.p_lock[name])
                het_.append(h)
                es_.append(fb.shortfall.get(name, math.nan))
                values[f"es.{name}.{m}"] = es_[-1]
                bt_.append(np.asarray(fb.by_term[name], dtype=float))
                terms_c = fb.terms[name]
            p = agg(ps_, how)
            tol = float(pol.max_bad_debt(name))
            # severity and the term frontier (evidence, D-RD-AUD-3)
            values[f"es.{name}"] = agg(es_, how)
            values[f"tmax_ok_days.{name}"] = term_frontier_days(bt_, terms_c, tol, how)
            values[f"pbad.{name}"] = p
            values[f"pbad_lock.{name}"] = agg(pl_, how)
            values[f"ratio.{name}"] = float(cand[f"baseRatioBps[{c}]"])
            values[f"het.{name}"] = agg(het_, how)
            hets.append(values[f"het.{name}"])
            sig = float(np.median([ens.sigma_median(m, skip) for m in hourly_members(ens)]))
            values[f"sigma_med.{name}"] = sig
            values[f"yed_per_usd.{name}"] = BPS * BPS / (float(cand[f"baseRatioBps[{c}]"]) * sig)
            ok = bool(np.isfinite(p) and p <= tol)
            cons[f"bad_debt_{name}"] = ok
            values[f"viol.bad_debt_{name}"] = 0.0 if ok else (p / tol - 1 if np.isfinite(p) else 1e9)
            hmax = float(pol.class_heterogeneity_max)
            hv = values[f"het.{name}"]
            # heterogeneity triggers design note G3-DN6; it is not a constraint of any parameter's rule
            # (D-RD-AUD-10), so it stays out of `cons` and cannot mark a candidate infeasible
            het_ok = bool(not np.isfinite(hv) or hv <= hmax)
            values[f"viol.het_{name}"] = 0.0 if het_ok else hv / hmax - 1
        values["het.max"] = float(np.nanmax(hets)) if np.isfinite(hets).any() else math.nan
        # claimant incentive
        depth = depth_p10_usd(env, float(pol.claimant_slippage_pctl))
        model = str(pget(pol, "claimant_model"))
        th = int(cand["claimThresholdBps"])
        cs = claim_stats(ens, cand, pol, depth, sell=model == "sell")[th]
        # the other claimant, as evidence: a seller of the test vault into the bid side of the book
        cs_sell = cs if model == "sell" else claim_stats(ens, cand, pol, depth, sell=True)[th]
        for s in ("mean_bps", "p10_bps", "show_up"):
            values[f"claim_sell.{s}"] = agg(
                [cs_sell[f"{m}.{s}"] for m in hourly_members(ens)], how, minimize=False
            )
        values["claim.theta"] = float(cand["claimThresholdBps"])
        for s in ("mean_bps", "p10_bps", "p50_bps", "show_up"):
            values[f"claim.{s}"] = agg([cs[f"{m}.{s}"] for m in hourly_members(ens)], how, minimize=False)
        for s in ("bad", "forfeit_bps"):
            values[f"claim.{s}"] = agg([cs[f"{m}.{s}"] for m in hourly_members(ens)], how, minimize=True)
        values["claim.n"] = float(sum(cs[f"{m}.n"] for m in hourly_members(ens)))
        for m in hourly_members(ens):
            values[f"claim.mean_bps.{m}"] = cs[f"{m}.mean_bps"]
        need = float(pol.claimant_min_profit_bps)
        cm = values[f"claim.{pget(pol, 'claim_margin_stat')}"]
        cons["claim_incentive"] = bool(np.isfinite(cm) and cm >= need)
        values["viol.claim_incentive"] = (
            0.0
            if cons["claim_incentive"]
            else ((need - cm) / max(abs(need), 1.0) if np.isfinite(cm) else 1e9)
        )
        # emergency path
        cr = crash_runs(env, cand)
        es = emergency_stats(cr, cand, pol)
        e = int(cand["emergencyRatioBps"])
        if e in es:
            row = es[e]
            values["emerg.shortfall"] = agg([row[f"{s}.shortfall"] for s in cr.names], how)
            values["emerg.shortfall_a"] = agg([row[f"{s}.shortfall_a"] for s in cr.names], how)
            values["emerg.benefit"] = values["emerg.shortfall_a"] - values["emerg.shortfall"]
            values["emerg.b_share"] = float(np.nanmean([row[f"{s}.b_share"] for s in cr.names]))
            values["emerg.shortfall_stress"] = agg([row[f"{s}.shortfall_stress"] for s in cr.names], how)
            values["emerg.b_share_stress"] = float(np.nanmean([row[f"{s}.b_share_stress"] for s in cr.names]))
            pl = [row[f"{s}.b_par_loss_bps"] for s in cr.names]
            values["emerg.b_par_loss_bps"] = float(np.nanmean(pl)) if np.isfinite(pl).any() else math.nan
            for s in cr.names:
                values[f"emerg.shortfall.{s}"] = row[f"{s}.shortfall"]
        else:
            values["emerg.shortfall"] = math.nan
        # outer ends: the CLTV bound (MINT-2 contiguity is an invariant the driver checks)
        cons["locktime"] = not any(
            v.invariant in ("class_locktime", "class_contiguous") for v in cand.check(Context())
        )
        meta = {
            "seed": env.seed,
            "budget": b.name,
            "out_dir": out_dir_of(env),
            "members": list(ens.names),
            "provenance": ens.provenance,
            "agg": how,
            "paths_per_member": ens.meta["paths"],
            "years": ens.meta["years"],
            "stride_hours": stride,
            "terms_per_class": nt,
            "sigma": sigma,
            "crash_scenarios": list(cr.names),
            "depth_p10_usd": depth,
        }
        return Metrics(values, "pbad.B", True, cons, ens.provenance, meta)  # type: ignore[arg-type]

    # -- decide ----------------------------------------------------------------------------------
    def decide(self, results: ResultTable, policy: Any) -> list[Recommendation]:
        cur_row = results.current()
        if cur_row is None:
            raise ValueError("G3: the current set was not evaluated")
        meta = dict(cur_row.metrics.meta)
        prov = str(meta.get("provenance", cur_row.metrics.provenance))
        decisions = {r.name: (r, decide_rule(results, r, policy)) for r in rules(policy)}
        notes = design_notes(results, policy)
        out = evidence_dir(meta.get("out_dir"), GROUP)
        evidence: list[Path] = []
        try:
            evidence = write_evidence(results, decisions, out, policy)
        except Exception as e:  # pragma: no cover - evidence is best effort
            meta["evidence_error"] = f"{type(e).__name__}: {e}"
        by_param = {p: (r, d) for r, d in decisions.values() for p in r.owns}
        recs: list[Recommendation] = []
        for p in self.params:
            rule, d = by_param[p]
            sub = rule_table(results, rule)
            cur = sub.current()
            assert cur is not None
            new_val = d.row.params[p]
            verdict = (
                d.verdict if d.verdict == "BLOCKED" else ("CHANGE" if new_val != results.base[p] else "KEEP")
            )
            rprov = "synthetic" if rule.name in ("emergency",) else prov
            keys = (rule.primary, *rule.report) if rule.primary != "zero" else rule.report
            mets: dict[str, Any] = {
                "primary": rule.primary,
                "current": {k: fmt(cur.metrics.values.get(k)) for k in keys},
                "recommended": {k: fmt(d.row.metrics.values.get(k)) for k in keys},
                "constraints_current": dict(cur.metrics.constraints),
                "constraints_recommended": dict(d.row.metrics.constraints),
                "decision": d.reason,
                "improvement": fmt(d.improvement),
                "members": meta.get("members"),
                "aggregate": meta.get("agg"),
                "design_notes": [n for n in notes if p in n["params"]],
            }
            if rule.name.startswith("ratio_"):
                cname = rule.name[-1]
                mets["per_member_current"] = {
                    m: fmt(cur.metrics.values.get(f"pbad.{cname}.{m}")) for m in meta.get("members", [])
                }
                mets["tolerance"] = float(policy.max_bad_debt(cname))
                mets["tradeoff"] = [
                    (int(r.params[p]), fmt(r.metrics.values.get(f"pbad.{cname}")))
                    for r in sorted(sub.rows, key=lambda r: int(r.params[p]))
                ]
            if d.least is not None:
                mets["least_violating"] = {
                    "delta": dict(d.least.delta),
                    "values": {k: fmt(d.least.metrics.values.get(k)) for k in keys},
                }
            sens = oat_sensitivity(sub, p, rule.sens_metric, rule.sens_metric) if rule.sens_metric else {}
            if not sens:
                sens = {"sentence": f"{p} is verified, not swept for a metric."}
            rec_notes = [f"Rule '{rule.name}': {d.reason}."]
            if p in ("classMin[1]", "classMin[2]"):
                rec_notes.append(f"Follows classMax[{int(p[-2]) - 1}] + 1 (MINT-2 contiguity).")
            if rule.name == "emergency":
                rec_notes.append(
                    "Crash scenarios are synthetic stress programs; RED-4(b) is assumed executed by a "
                    "YED holder exiting at par (it never pays a claimant buying YED at par, D-WP4-6)."
                )
            recs.append(
                Recommendation(
                    param=p,
                    current=results.base[p],
                    recommended=new_val,
                    verdict=final_verdict(verdict, rprov),  # type: ignore[arg-type]
                    rule=rule.text,
                    binding=binding_text(rule, cur, d, policy),
                    metrics=mets,
                    sensitivity=sens,
                    confidence=confidence_for(rprov, verdict, str(meta.get("budget", "quick"))),
                    provenance=rprov,
                    evidence=list(evidence),
                    group=GROUP,
                    notes=rec_notes,
                )
            )
        return recs

    def explain(self, rec: Recommendation, results: ResultTable) -> str:
        return explain_g3(rec)


def binding_text(rule: Rule, cur: ResultRow, d: RuleDecision, policy: Any) -> str:
    viol = [c for c in rule.constraints if not cur.metrics.constraints.get(c, True)]
    if d.verdict == "BLOCKED":
        return f"policy constraint {', '.join(rule.constraints)} cannot be met within the registry bounds"
    if viol:
        return f"constraint {', '.join(viol)} violated at the current value"
    if rule.kind == "verify":
        return "verification: every constraint holds" + (
            f" ({', '.join(rule.constraints)})" if rule.constraints else ""
        )
    if d.verdict == "KEEP" and d.improvement > 0:
        return f"materiality ({d.reason})"
    return f"{rule.primary} subject to {', '.join(rule.constraints) or 'the invariants'}"


def make_study() -> G3Study:
    """The G3 study (``load_study("G3")``)."""
    return G3Study()


# ===================================================================================================
# Design notes


def design_note(
    id_: str,
    title: str,
    finding: str,
    *,
    evidence: Mapping[str, Any] | None = None,
    consequence: str = "",
    fix: str = "",
    params: Sequence[str] = (),
) -> dict[str, Any]:
    """One design note (the report's §5 format, shared by G3/G4/G9)."""
    return {
        "id": id_,
        "title": title,
        "finding": finding,
        "evidence": dict(evidence or {}),
        "consequence": consequence,
        "fix": fix,
        "params": list(params),
    }


def _pct(x: Any, d: int = 2) -> str:
    return f"{x:.{d}%}" if isinstance(x, float | int) and math.isfinite(float(x)) else "n/a"


def design_notes(results: ResultTable, policy: Any | None = None) -> list[dict[str, Any]]:
    """Rule-level findings of G3 (things no parameter value fixes), computed from the current row."""
    cur = results.current()
    if cur is None:
        return []
    v = cur.metrics.values
    tol = {n: (float(policy.max_bad_debt(n)) if policy is not None else math.nan) for n in CLASS_NAMES}
    pb = {n: fmt(v.get(f"pbad.{n}")) for n in CLASS_NAMES}
    lo_p = {
        n: fmt(
            min(
                (
                    r.metrics.values.get(f"pbad.{n}", math.nan)
                    for r in results
                    if set(r.delta) <= {f"baseRatioBps[{CLASS_NAMES.index(n)}]"}
                ),
                default=math.nan,
            )
        )
        for n in CLASS_NAMES
    }
    top: dict[str, Mapping[str, float]] = {}
    for i, n in enumerate(CLASS_NAMES):
        k = f"baseRatioBps[{i}]"
        rows = [r for r in results if set(r.delta) <= {k}]
        if rows:
            top[n] = max(rows, key=lambda r, k=k: int(r.params[k])).metrics.values
    reach = {n: fmt(top[n].get(f"tmax_ok_days.{n}")) if n in top else math.nan for n in CLASS_NAMES}
    short = {n: fmt(top[n].get(f"es.{n}")) if n in top else math.nan for n in CLASS_NAMES}
    cmax = {n: int(results.base[f"classMax[{i}]"]) // BLOCKS_PER_DAY for i, n in enumerate(CLASS_NAMES)}
    sev = {
        n: (short[n] / lo_p[n] if isinstance(lo_p[n], int | float) and lo_p[n] > 0 else math.nan)
        for n in CLASS_NAMES
    }

    def _days(x: Any) -> str:
        return f"{x:.0f} d" if isinstance(x, int | float) and math.isfinite(x) else "n/a"

    notes = [
        design_note(
            "G3-DN1",
            "No liquidation before lockHeight + grace (fact 1.5-1)",
            "The vault script admits only the owner path until claimHeight, so the base ratio must cover the "
            f"whole term's drawdown. P(bad debt) at the shipped ratios: A {_pct(pb['A'])}, "
            f"B {_pct(pb['B'])}, C {_pct(pb['C'])} "
            f"(tolerances {_pct(tol['A'])}, {_pct(tol['B'])}, {_pct(tol['C'])}); the lowest reachable within "
            f"the registry bounds: A {_pct(lo_p['A'])}, B {_pct(lo_p['B'])}, C {_pct(lo_p['C'])}. "
            f"At the upper bound each class meets its tolerance only for terms up to "
            f"A {_days(reach['A'])}, B {_days(reach['B'])}, C {_days(reach['C'])} "
            f"(class maxima {cmax['A']}, {cmax['B']}, {cmax['C']} d), and a vault that is bad at the "
            f"claim opening is short by {_pct(sev['A'], 0)}, "
            f"{_pct(sev['B'], 0)}, {_pct(sev['C'], 0)} of its debt on average. A bad vault "
            "is not yet a realised loss: nobody may claim it below the debt, it stays claimable if the price "
            "recovers, and YED holders bear the gap only through the peg (D-RD-AUD-3).",
            evidence={
                "pbad_current": pb,
                "pbad_at_upper_bound": lo_p,
                "tolerance": tol,
                "term_reach_days_at_upper_bound": reach,
                "shortfall_at_upper_bound": short,
            },
            consequence="Classes whose tolerance cannot be met at any ratio are BLOCKED; capital efficiency "
            "of long terms collapses before the risk does.",
            fix="Rule change (out of scope for tuning): a claim/top-up path during the term (e.g. RED-4 from "
            "lockHeight at a higher threshold), term-scaled ratios, or shorter maximum terms.",
            params=("baseRatioBps[0]", "baseRatioBps[1]", "baseRatioBps[2]", "classMax[2]"),
        ),
        design_note(
            "G3-DN2",
            "RED-5 residual is always 0 under RED-4(a)",
            "The claimant cap uses the same threshold that made the vault underwater, so a RED-4(a) claimant "
            "takes the whole collateral; an absent owner forfeits everything above the debt "
            f"(mean forfeit at the first trigger: {v.get('claim.forfeit_bps', math.nan):.0f} bps of the "
            "debt).",
            evidence={"claim.forfeit_bps": fmt(v.get("claim.forfeit_bps"))},
            consequence="residualMinZat only matters under RED-4(b); raising claimThresholdBps transfers "
                "more "
            "value from absent owners to claimants.",
            fix="Rule change: cap the RED-4(a) claimant at debt × (1 + a bounty) and return the rest.",
            params=("claimThresholdBps", "residualMinZat"),
        ),
        design_note(
            "G3-DN3",
            "pClaim lag eats the claim margin",
            "pClaim = max(pMid, pSlow) lags a falling price, so when a vault first shows underwater the "
            f"claimant's expected margin is {v.get('claim.mean_bps', math.nan):.0f} bps of the debt at the "
            f"current threshold (show-up rate {_pct(v.get('claim.show_up'), 0)}; already bad debt at the "
            f"trigger: {_pct(v.get('claim.bad'), 0)}).",
            evidence={k: fmt(v.get(k)) for k in ("claim.mean_bps", "claim.show_up", "claim.bad")},
            consequence="Claimants do not show up in a steady decline; abandoned vaults drift into bad debt.",
            fix="Parameter (claimThresholdBps, see its recommendation) or a rule change: a fresher claim "
                "price "
            "(e.g. pFast or the attested aClaim for RED-4(a)).",
            params=("claimThresholdBps",),
        ),
        design_note(
            "G3-DN4",
            "RED-4(b) never pays a claimant buying YED at par",
            "Under RED-4(b) the claimant receives the debt's worth at pClaim, the higher of the two prices, "
            "so "
            f"at par it is a loss ({v.get('emerg.b_par_loss_bps', math.nan):.0f} bps of the debt on average "
            "in the crash runs); it only works as a par exit for YED holders when YED trades at a discount.",
            evidence={k: fmt(v.get(k)) for k in ("emerg.b_par_loss_bps", "emerg.benefit", "emerg.b_share")},
            consequence="The emergency path's benefit depends on a YED discount appearing in a crash.",
            fix="Rule change: pay the RED-4(b) claimant at pEmerg (or with a bounty).",
            params=("emergencyRatioBps",),
        ),
        design_note(
            "G3-DN5",
            "The σ multiplier adds no protection at its median",
            f"The median hour-mode σ multiplier over the ensemble is {fmt(v.get('sigma_med.A'))} bps (σ̂ on "
            "the smoothed pFast stays below sigmaRefBps, D-WP3-6), so the base ratio alone carries the "
            "drawdown "
            "budget.",
            evidence={"sigma_median_bps": fmt(v.get("sigma_med.A"))},
            consequence="Ratios are sized at multiplier 1×; G2 (sigmaRefBps) decides whether that changes.",
            fix="Parameter coupling with G2, not a rule change.",
            params=("baseRatioBps[0]", "baseRatioBps[1]", "baseRatioBps[2]"),
        ),
    ]
    hmax = float(policy.class_heterogeneity_max) if policy is not None else math.nan
    het = {n: fmt(v.get(f"het.{n}")) for n in CLASS_NAMES}
    if any(isinstance(h, float) and math.isfinite(h) and h > hmax for h in het.values()):
        notes.append(
            design_note(
                "G3-DN6",
                "Term heterogeneity inside the classes",
                f"P(bad debt) rises with the term inside each class (heterogeneity A {het['A']:.2f}, B "
                f"{het['B']:.2f}, C {het['C']:.2f} against {hmax}); with three fixed classes (NUM_CLASSES) "
                "and one ratio per "
                "class, "
                "long terms are under-collateralised relative to short ones.",
                evidence={"heterogeneity": het, "max": hmax},
                consequence="Boundary moves only shift the problem between classes.",
                fix="Rule change: more classes, or a ratio that grows with the lock length.",
                params=tuple(f"class{x}[{i}]" for x in ("Min", "Max") for i in range(3)),
            )
        )
    return notes


# ===================================================================================================
# Evidence


def write_evidence(
    results: ResultTable, decisions: Mapping[str, tuple[Rule, RuleDecision]], out: Path, policy: Any
) -> list[Path]:
    """``g3_results.csv`` (every row), ``g3_tradeoff.csv`` (P(bad debt) vs ratio per class and member) and
    two figures: P(bad debt) vs base ratio per class, and the claimant margin vs claimThresholdBps."""
    paths = [results.to_csv(out / "g3_results.csv")]
    cur = results.current()
    assert cur is not None
    members = list(cur.metrics.meta.get("members", []))
    rows = []
    for c, name in enumerate(CLASS_NAMES):
        k = f"baseRatioBps[{c}]"
        for r in sorted(rule_table(results, ratio_rule(c)).rows, key=lambda r: int(r.params[k])):
            rows.append(
                [
                    name,
                    int(r.params[k]),
                    *(r.metrics.values.get(f"pbad.{name}.{m}", math.nan) for m in members),
                    r.metrics.values.get(f"pbad.{name}", math.nan),
                    float(policy.max_bad_debt(name)),
                ]
            )
    p = out / "g3_tradeoff.csv"
    with p.open("w") as fh:
        fh.write(",".join(["class", "baseRatioBps", *members, "aggregate", "tolerance"]) + "\n")
        for r in rows:
            fh.write(",".join(str(x) for x in r) + "\n")
    paths.append(p)
    plt = plot_style()
    if plt is None:  # pragma: no cover
        return paths
    palette = [C_CUR, C_REC, "#1baf7a", "#a855c7", "#c9a227"]
    fig, axs = plt.subplots(1, 3, figsize=(10.5, 3.4), sharey=False)
    for c, (ax, name) in enumerate(zip(axs, CLASS_NAMES, strict=True)):
        sub = [r for r in rows if r[0] == name]
        xs = [r[1] / 100 for r in sub]
        for j, m in enumerate(members):
            ax.plot(xs, [max(r[2 + j], 1e-4) for r in sub], color=palette[j % len(palette)], lw=1.2, label=m)
        ax.plot(xs, [max(r[-2], 1e-4) for r in sub], color=C_TEXT, lw=2.2, label="aggregate")
        ax.axhline(float(policy.max_bad_debt(name)), color=C_TRUE, ls="--", lw=1)
        ax.axvline(int(results.base[f"baseRatioBps[{c}]"]) / 100, color=C_TRUE, ls=":", lw=1)
        d = decisions[f"ratio_{name}"][1]
        ax.axvline(int(d.row.params[f"baseRatioBps[{c}]"]) / 100, color=C_REC, ls=":", lw=1.5)
        ax.set_yscale("log")
        ax.set_xlabel(f"baseRatioBps[{c}] (%)")
        ax.set_title(f"class {name}: P(bad debt) at claim open", loc="left", fontsize=9)
    axs[0].set_ylabel("probability (log; floored at 1e-4)")
    axs[0].legend(fontsize=7)
    fig.suptitle(
        "Dashed = policy tolerance, dotted grey = current, dotted orange = recommended / least violating",
        fontsize=8,
        x=0.01,
        ha="left",
        color=C_TRUE,
    )
    fig.tight_layout()
    f1 = out / "g3_bad_debt_vs_ratio.png"
    fig.savefig(f1, dpi=120)
    plt.close(fig)
    paths.append(f1)
    sub = sorted(rule_table(results, RULES_FIXED[0]).rows, key=lambda r: int(r.params["claimThresholdBps"]))
    if len(sub) >= 2:
        fig, ax = plt.subplots(figsize=(6.4, 3.4))
        xs = [int(r.params["claimThresholdBps"]) / 100 for r in sub]
        for j, m in enumerate(members):
            ax.plot(
                xs,
                [r.metrics.values.get(f"claim.mean_bps.{m}", math.nan) for r in sub],
                color=palette[j % len(palette)],
                lw=1.2,
                label=m,
            )
        ax.plot(
            xs,
            [r.metrics.values.get("claim.mean_bps", math.nan) for r in sub],
            color=C_TEXT,
            lw=2.2,
            label="aggregate",
        )
        ax.axhline(float(policy.claimant_min_profit_bps), color=C_TRUE, ls="--", lw=1)
        ax.axhline(0, color=C_GRID, lw=1)
        ax.axvline(int(results.base["claimThresholdBps"]) / 100, color=C_TRUE, ls=":", lw=1)
        ax.set_xlabel("claimThresholdBps (%)")
        ax.set_ylabel("claimant margin at first trigger (bps of debt)")
        ax.set_title(
            "RED-4(a) claimant margin vs threshold (dashed = claimant_min_profit_bps)", loc="left", fontsize=9
        )
        ax.legend(fontsize=7)
        fig.tight_layout()
        f2 = out / "g3_claim_margin.png"
        fig.savefig(f2, dpi=120)
        plt.close(fig)
        paths.append(f2)
    return paths


# ===================================================================================================
# Explanations

WHAT = {
    "baseRatioBps": "the collateral a class must lock per unit of debt before the σ multiplier (MINT-5); "
    "with no liquidation before lockHeight + grace it is the whole term's drawdown budget",
    "classMin": "the shortest lock (blocks) of a term class (MINT-2)",
    "classMax": "the longest lock (blocks) of a term class (MINT-2)",
    "claimThresholdBps": "the collateral ratio at pClaim below which a matured vault is claimable (RED-4(a)) "
    "and the claimant's payout margin (RED-5)",
    "emergencyRatioBps": "the ratio at pEmerg = min(xClaim, aClaim) below which an ARMED module accepts an "
    "emergency notice and RED-4(b) claim",
}


def explain_g3(rec: Recommendation) -> str:
    base = rec.param.split("[")[0]
    what = WHAT.get(base, REGISTRY[rec.param].doc)
    km: list[tuple[str, str, str]] = []
    extra: list[str] = []
    if base == "baseRatioBps":
        n = CLASS_NAMES[int(rec.param[-2])]
        km = [
            (f"P(bad debt, class {n})", f"pbad.{n}", "{:.2%}"),
            ("P(bad at lock)", f"pbad_lock.{n}", "{:.2%}"),
            ("YED per USD locked", f"yed_per_usd.{n}", "{:.3f}"),
        ]
        tol = rec.metrics.get("tolerance")
        per = rec.metrics.get("per_member_current", {})
        if per:
            extra.append(
                "Per ensemble member at the current ratio: "
                + ", ".join(
                    f"{m} {v:.2%}" if isinstance(v, float) and math.isfinite(v) else f"{m} n/a"
                    for m, v in per.items()
                )
                + (f" (tolerance {tol:.2%})." if tol else ".")
            )
        tr = rec.metrics.get("tradeoff") or []
        if tr:
            pts = [
                f"{r // 100}% → {p:.1%}"
                for r, p in tr[:: max(1, len(tr) // 6)]
                if isinstance(p, float) and math.isfinite(p)
            ]
            extra.append("Trade-off (ratio → aggregate P(bad debt)): " + ", ".join(pts) + ".")
    elif base in ("classMin", "classMax"):
        km = [("heterogeneity A", "het.A", "{:.2f}"), ("B", "het.B", "{:.2f}"), ("C", "het.C", "{:.2f}")]
    elif base == "claimThresholdBps":
        km = [
            ("mean claimant margin (bps)", "claim.mean_bps", "{:.0f}"),
            ("show-up rate", "claim.show_up", "{:.1%}"),
            ("already bad at trigger", "claim.bad", "{:.1%}"),
            ("owner forfeit (bps)", "claim.forfeit_bps", "{:.0f}"),
        ]
    elif base == "emergencyRatioBps":
        km = [
            ("uncovered debt at closure, ARMED", "emerg.shortfall", "{:.2%}"),
            ("RED-4(a) only", "emerg.shortfall_a", "{:.2%}"),
            ("closed by RED-4(b)", "emerg.b_share", "{:.1%}"),
            ("par-exit loss (bps)", "emerg.b_par_loss_bps", "{:.0f}"),
        ]
    lv = rec.metrics.get("least_violating")
    if lv and rec.verdict == "BLOCKED":
        extra.append(f"Least-violating candidate: {lv['delta'] or 'current'}.")
    return compose_explanation(rec, what=what, key_metrics=km, extra=extra)


__all__ = [
    "CLASS_NAMES",
    "JUDGEMENT",
    "Ensemble",
    "FirstBelow",
    "G3Study",
    "Rule",
    "RuleDecision",
    "agents_from_policy",
    "agg",
    "bad_debt",
    "claim_stats",
    "clear_caches",
    "compose_explanation",
    "crash_runs",
    "decide_rule",
    "design_note",
    "design_notes",
    "emergency_stats",
    "ensemble",
    "heterogeneity",
    "make_study",
    "pget",
    "rule_table",
    "rules",
]

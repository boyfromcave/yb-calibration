"""G2 — volatility: volWindow, volStep, volPeriodsPerYear, sigmaRefBps, sigmaMultMaxBps (PLAN §5.2).

Owner: WP-7a. Method, metric definitions and decision rules: ``docs/studies/g2.md``.

SIGMA-1 measures volatility on **pFast** (state.cpp:1213), a median of TWAP quotes, so the σ̂ it
sees is smoother than the market's (D-WP3-6: ≈ 8,200 bps at 120 % true volatility). Every σ̂ here
is therefore computed on simulated pFast: price paths → WP-3 oracle tag stream → PRICE-1 median at
the candidate's ``pFastWindow`` (the G1 helpers; realised once per run and shared by candidates) →
``sim.sigma``. ``sigmaRefBps`` is calibrated on that σ̂.

Ensembles (``ensemble_specs``):

* ``realised`` — the YEC-like ensemble: with a real price path, a block bootstrap of its returns;
  otherwise the four synthetic presets (GBM, Merton, GARCH-t, regime switch), equal paths each.
* ``calm`` / ``turbulent`` — stationary regimes. Synthetic: the regime-switch preset's calm σ
  (GBM) and its turbulent σ as a Merton jump-diffusion with the preset jump law. Real data: the
  same two models at the calm / turbulent volatilities of a regime-switch fit to the real returns.
* ``shift`` — calm for ``SHIFT_AT_DAYS`` days, then turbulent (responsiveness).
* ``feed-outage-6h`` — the scenario (K12 cap trap).
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from ybcal.data import synthetic as SY
from ybcal.optimize.sensitivity import oat_from_table, sensitivity_sentence
from ybcal.params.paramset import ParamSet
from ybcal.params.registry import REGISTRY, params_for_group
from ybcal.sim import oracle as O
from ybcal.sim import sigma as S
from ybcal.studies import g1_price_windows as G1
from ybcal.studies.base import (
    Budget,
    Env,
    Metrics,
    Recommendation,
    ResultTable,
    decide_with_materiality,
    final_verdict,
)
from ybcal.units import BLOCKS_PER_DAY, BLOCKS_PER_HOUR, BLOCKS_PER_YEAR, BPS

if TYPE_CHECKING:  # pragma: no cover
    from ybcal.config import Policy

OWNER_WP = "WP-7a"

GROUP = "G2"
WS = ("volWindow", "volStep")
P0 = 400_000  # $0.40, the scenario library's start price

#: Regime change (calm → turbulent) for the responsiveness metric, in days from the series start.
SHIFT_AT_DAYS = 10.0
#: Blocks skipped before any σ statistic: the longest searchable volWindow plus one pFast window.
WARMUP_BLOCKS = REGISTRY["volWindow"].bounds[1] + REGISTRY["pFastWindow"].bounds[1]

#: Hand-picked quick lattice (every pair divides; every step divides BLOCKS_PER_YEAR).
QUICK_WINDOWS = (1152, 2016, 2880, 4032, 5760)
QUICK_STEPS = (24, 48, 96, 144)
QUICK_REFS = (6000, 8000, 10000, 12000, 15000)
QUICK_CAPS = (20000, 25000, 30000, 40000)


# ---------------------------------------------------------------------------------------------------
# Price ensembles


def _merton_turbulent(total_vol: float) -> SY.Merton:
    m = SY.Merton()
    jump_var = m.lam * (m.jump_mu**2 + m.jump_sigma**2)
    m.sigma = math.sqrt(max(total_vol**2 - jump_var, 0.05**2))
    m.mu = 0.0
    return m


def daily_view(pp):
    """The hourly real path observed once a day (the other hours flagged ``filled``), so a fit reads
    daily returns (``synthetic.fit_returns_of``): fitted on hourly YEC returns the regime model reads
    the aggregator's hour-to-hour noise as a 770 % "turbulent" state and a 34 % "calm" one; on daily
    returns it finds calm ≈ 80 %, turbulent ≈ 350 % (docs/real-data-2026-10.md §9, D-RD-ORA-5)."""
    from ybcal.types import PricePath

    if pp.resolution != "hour":
        return pp
    filled = np.ones(pp.prices.shape[-1], dtype=bool)
    filled[::24] = False
    old = pp.meta.get("filled")
    if isinstance(old, np.ndarray) and old.shape[-1] == filled.shape[0]:
        filled = filled | old.astype(bool).reshape(-1, filled.shape[0])[0]
    return PricePath(pp.t0, "hour", pp.prices, pp.provenance, {**pp.meta, "filled": filled})


def regime_vols(env: Env) -> tuple[float, float, str]:
    """(calm σ, turbulent σ, source): the regime-switch preset, or a fit to the real prices."""
    real = G1.real_price(env)
    if real is not None:
        try:
            rs = SY.RegimeSwitch.fit(daily_view(real))
            lo, hi = sorted(float(x) for x in rs.sigma)
            return lo, hi, "regime-switch fit to the real daily returns"
        except Exception:  # pragma: no cover - degenerate data
            pass
    rs = SY.RegimeSwitch.preset()
    return float(rs.sigma[0]), float(rs.sigma[1]), "regime-switch preset (synthetic)"


def _centred(model: SY.PriceModel, P: int, n: int, rng: np.random.Generator) -> np.ndarray:
    r = model.log_returns(P, n, "block", rng)[:, : n - 1]
    return r - model.expected_log_drift() / BLOCKS_PER_YEAR


def ensemble_prices(env: Env, kind: str, P: int, n: int) -> tuple[np.ndarray, str]:
    """True price paths ``(P, n)`` for an ensemble (see the module docstring) and their label."""
    rng = env.rng_for("G2", "ensemble", kind, P, n)
    calm, turb, src = regime_vols(env)
    if kind == "realised":
        real = G1.real_price(env)
        if real is not None:
            bb = G1.real_model(env, real)
            bb.demean = True
            r = bb.log_returns(P, n, "block", rng)[:, : n - 1]
            label = "block bootstrap of the real price"
        else:
            names = ("gbm", "merton", "garch", "regime")
            parts = []
            for i, nm in enumerate(names):
                k = P // len(names) + (1 if i < P % len(names) else 0)
                if k:
                    parts.append(_centred(SY.preset(nm), k, n, env.rng_for("G2", "preset", nm, k, n)))
            r = np.concatenate(parts, axis=0)
            label = "YEC-like synthetic presets (gbm, merton, garch, regime)"
    elif kind == "calm":
        r = _centred(SY.GBM(mu=0.0, sigma=calm), P, n, rng)
        label = f"calm GBM σ={calm:.2f} ({src})"
    elif kind == "turbulent":
        r = _centred(_merton_turbulent(turb), P, n, rng)
        label = f"turbulent Merton σ={turb:.2f} ({src})"
    elif kind == "shift":
        k = int(SHIFT_AT_DAYS * BLOCKS_PER_DAY)
        a = _centred(SY.GBM(mu=0.0, sigma=calm), P, n, rng)
        b = _centred(_merton_turbulent(turb), P, n, env.rng_for("G2", "ensemble", "shift-b", P, n))
        r = np.concatenate([a[:, :k], b[:, k:]], axis=1)
        label = f"calm σ={calm:.2f} → turbulent σ={turb:.2f} at day {SHIFT_AT_DAYS:g}"
    else:  # pragma: no cover
        raise ValueError(kind)
    return SY.returns_to_path(r, P0, 1 / BLOCKS_PER_YEAR).prices, label


def realise_ensemble(env: Env, kind: str, P: int, days: float) -> G1.ScenarioRealisation:
    """An ensemble's true prices through the honest oracle (memoised with the G1 realisations)."""
    n = round(days * BLOCKS_PER_DAY) + 1
    pol = env.policy
    # the same oracle as G1: the real pool landscape and its miner sequence when a pool-share log is
    # loaded, else the policy's equal pools (D-RD-ORA-1)
    cfg = G1.oracle_config(env)
    land = G1.pool_landscape(env)
    key = (
        int(env.seed),
        f"G2:{kind}",
        P,
        n,
        G1.data_fingerprint(env),
        float(pol.expected_enforcing_share),
        int(pol.expected_pool_count),
        tuple((round(p.share, 9), p.tags) for p in cfg.pools),
        land.fingerprint() if land is not None else None,
    )
    hit = G1._REAL.get(key)
    if hit is not None:
        return hit
    true, label = ensemble_prices(env, kind, P, n)
    miner = G1.real_miners(env, land, P, n, f"G2:{kind}")
    inp = O.generate_block_inputs(true, cfg, rng=env.rng_for("G2", "oracle", kind, P, n), miner=miner)
    valid = inp.tag_present & (inp.tag_price > 0)
    r = G1.ScenarioRealisation(
        key,
        kind,
        true,
        np.where(valid, inp.tag_price, 0),
        valid,
        inp.tag_pool,
        {"shift": int(SHIFT_AT_DAYS * BLOCKS_PER_DAY)} if kind == "shift" else {},
        {"label": label, "provenance": G1.provenance_of(env)},
    )
    return G1._lru_put(G1._REAL, key, r, G1._CACHE_REAL)


def p_fast(r: G1.ScenarioRealisation, params: Mapping) -> np.ndarray:
    """pFast (int64, −1 undefined) for ``params``' pFastWindow/MinFill."""
    return G1.median(r, int(params["pFastWindow"]), int(params["pFastMinFill"])).astype(np.int64)


def shift_response(
    sig: np.ndarray, k: int, *, frac: float = 0.9, before_blocks: int = 2 * BLOCKS_PER_DAY
) -> np.ndarray:
    """Per path: blocks after ``k`` until ``sig`` (NaN = undefined) first covers ``frac`` of the way
    from its median over the ``before_blocks`` before ``k`` to its median over the last quarter of the
    series (the new steady state); NaN if never. Like ``sim.sigma.responsiveness_blocks`` but robust
    to undefined warm-up values inside the pre-change window."""
    n = sig.shape[1]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return _shift_response(sig, k, frac, before_blocks, n)


def _shift_response(sig: np.ndarray, k: int, frac: float, before_blocks: int, n: int) -> np.ndarray:
    before = np.nanmedian(sig[:, max(0, k - before_blocks) : k], axis=1)
    after = np.nanmedian(sig[:, n - n // 4 :], axis=1)
    target = before + frac * (after - before)
    seg = sig[:, k:]
    up = (after >= before)[:, None]
    hit = np.where(up, seg >= target[:, None], seg <= target[:, None]) & np.isfinite(seg)
    out = np.where(hit.any(axis=1), hit.argmax(axis=1), np.nan).astype(float)
    return out


def days_for(budget: Budget) -> float:
    """Simulated days per ensemble: the budget's horizon, at least 30 (warm-up + regime shift)."""
    return float(max(30, int(budget.block_horizon_days)))


# ---------------------------------------------------------------------------------------------------
# The study


def _lattice(name: str, lo: int | None = None, hi: int | None = None) -> list[int]:
    spec = REGISTRY[name]
    a, b = spec.bounds
    return list(range(lo or a, (hi or b) + 1, spec.step))


def _thin(vals: list[int], points: int, keep: int) -> list[int]:
    if points >= len(vals):
        return sorted(set(vals) | {keep})
    idx = np.unique(np.round(np.linspace(0, len(vals) - 1, max(points, 2))).astype(int))
    return sorted({vals[i] for i in idx} | {keep})


@dataclass
class G2Study:
    """PLAN §5.2. ``space``: a volWindow × volStep grid at the current reference and cap, plus
    one-at-a-time sweeps of ``sigmaRefBps`` and ``sigmaMultMaxBps`` (evidence and sensitivity; their
    values are set by rules from the σ̂ distribution, not by search)."""

    group: str = GROUP
    params: tuple[str, ...] = field(default_factory=lambda: params_for_group(GROUP))

    def space(self, base: ParamSet, budget: Budget) -> Iterable[ParamSet]:
        if budget.name == "quick":
            wins, steps = list(QUICK_WINDOWS), list(QUICK_STEPS)
            refs, caps = list(QUICK_REFS), list(QUICK_CAPS)
        else:
            pts = budget.grid_points
            wins = _thin([w for w in _lattice("volWindow") if w % 144 == 0], pts, base.as_int("volWindow"))
            steps = [12, 24, 48, 96, 144, 288]
            refs = _thin(_lattice("sigmaRefBps"), pts, base.as_int("sigmaRefBps"))
            caps = _thin(_lattice("sigmaMultMaxBps"), pts, base.as_int("sigmaMultMaxBps"))
        out = [base]
        bw, bs = base.as_int("volWindow"), base.as_int("volStep")
        for w in sorted(set(wins) | {bw}):
            for s in sorted(set(steps) | {bs}):
                if (w, s) == (bw, bs) or w % s or BLOCKS_PER_YEAR % s:
                    continue
                out.append(base.replace(volWindow=w, volStep=s))
        for v in refs:
            if v != base.as_int("sigmaRefBps"):
                out.append(base.replace(sigmaRefBps=v))
        for v in caps:
            if v != base.as_int("sigmaMultMaxBps"):
                out.append(base.replace(sigmaMultMaxBps=v))
        return out

    def evaluate(self, cand: ParamSet, env: Env) -> Metrics:
        pol = env.policy
        P = G1.paths_for(env.budget)
        days = days_for(env.budget)
        v: dict[str, float] = {}
        series: dict[str, Any] = {}
        lo = WARMUP_BLOCKS
        labels = {}
        for kind in ("realised", "calm", "turbulent"):
            r = realise_ensemble(env, kind, P, days)
            labels[kind] = r.info["label"]
            pf = p_fast(r, cand)
            sh = S.sigma_hat_bps(cand, pf)[:, lo:]
            ok = sh >= 0
            x = sh[ok].astype(float)
            for q in (5, 50, 95, 99):
                v[f"sigma_hat_p{q}_{kind}"] = float(np.percentile(x, q)) if x.size else math.nan
            v[f"sigma_hat_cv_{kind}"] = float(x.std() / x.mean()) if x.size > 1 and x.mean() > 0 else math.nan
            m = S.sigma_series(cand, pf)
            # the undefined-sample mask needs the full series (a slice would look "virtual" at its start)
            und = S.undefined_sample_mask(cand, pf)[:, lo:]
            body = m[:, lo:]
            q = np.percentile(body.astype(float) / BPS, [50, 95, 99])
            v[f"mult_p50_{kind}"], v[f"mult_p95_{kind}"], v[f"mult_p99_{kind}"] = (float(x) for x in q)
            at_cap = body >= max(cand.as_int("sigmaMultMaxBps"), BPS)
            per_year = BLOCKS_PER_YEAR / BLOCKS_PER_HOUR / max(body.size, 1)
            v[f"cap_h_per_year_{kind}"] = float(at_cap.sum()) * per_year
            v[f"cap_undefined_h_per_year_{kind}"] = float((at_cap & und).sum()) * per_year
            v[f"cap_vol_h_per_year_{kind}"] = float((at_cap & ~und).sum()) * per_year
            # true-price realised vol (context for D-WP3-6)
            tr = np.diff(np.log(r.true[:, lo:].astype(float)), axis=1)
            v[f"true_vol_bps_{kind}"] = float(tr.std() * math.sqrt(BLOCKS_PER_YEAR) * BPS)
        # the real history replayed through the same oracle (D-RD-ORA-3): evidence per era
        from ybcal.studies import oracle_replay as R

        v.update(R.sigma_evidence(env, cand))
        ref = cand.as_int("sigmaRefBps")
        v["m14_ratio"] = v["sigma_hat_p50_realised"] / ref if ref > 0 else math.nan
        v["turb_p99_uncapped"] = max(v["sigma_hat_p99_turbulent"] / ref, 1.0) if ref > 0 else math.nan

        # responsiveness: blocks until σ̂ covers 90 % of the calm → turbulent shift
        r = realise_ensemble(env, "shift", P, days)
        pf = p_fast(r, cand)
        sh = S.sigma_hat_bps(cand, pf).astype(float)
        sh[sh < 0] = np.nan
        k = r.marks["shift"]
        resp = shift_response(sh, k, frac=0.9)
        v["responsiveness_blocks"] = float(np.nanmedian(resp)) if np.isfinite(resp).any() else math.inf
        v["responsiveness_p90_blocks"] = (
            float(np.nanpercentile(resp, 90)) if np.isfinite(resp).any() else math.inf
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            med = np.nanmedian(sh[:, ::BLOCKS_PER_HOUR], axis=0)
        series["shift"] = {
            "start": k,
            "step_blocks": BLOCKS_PER_HOUR,
            "sigma_hat": [float(x) if np.isfinite(x) else -1.0 for x in med],
        }

        # K12 cap trap after a 6-hour feed outage
        o = G1.realise(
            env,
            "feed-outage-6h",
            n_paths=P,
            horizon=min(30.0, max(G1._event_end_days(G1.scenario(env, "feed-outage-6h")) + 9, 20)),
        )
        pf = p_fast(o, cand)
        und = S.undefined_sample_mask(cand, pf)
        os_, oe = o.marks.get("outage", lo), o.marks.get("outage_end", lo)
        v["k12_trap_h_per_outage"] = float(und[:, os_:].sum(axis=1).mean()) / BLOCKS_PER_HOUR
        v["k12_trap_after_feed_blocks"] = float(und[:, oe:].sum(axis=1).mean())

        lag = float(pol.max_sigma_lag_blocks)
        constraints = {
            "max_sigma_lag_blocks": bool(v["responsiveness_blocks"] <= lag),
            "k12_trap_le_max_sigma_lag": bool(v["k12_trap_after_feed_blocks"] <= lag),
            # D-RD-AUD-11: pFast is a rolling median over pFastWindow blocks; sampled more finely than
            # half that window its increments overlap and are smoothed, so the K13 annualisation (iid
            # increments) no longer measures volatility and the calm CV falls for the wrong reason
            "sampling": sampling_ok(cand),
        }
        meta = {
            "budget": env.budget.name,
            "out_dir": G1.out_dir_of(env),
            "paths": P,
            "days": days,
            "labels": labels,
            "series": series,
            "data": G1.data_fingerprint(env),
            "sigma_source": "pFast (D-WP3-6)",
        }
        return Metrics(
            v,
            primary="sigma_hat_cv_calm",
            minimize=True,
            constraints=constraints,
            provenance=G1.provenance_of(env),
            meta=meta,
        )  # type: ignore[arg-type]

    # -- decide -----------------------------------------------------------------------------------
    def decide(self, results: ResultTable, policy: Policy) -> list[Recommendation]:
        return decide_g2(results, policy)

    def explain(self, rec: Recommendation, results: ResultTable) -> str:
        return explain_g2(rec)


def make_study() -> G2Study:
    """The G2 study (PLAN §5.2)."""
    return G2Study()


# ---------------------------------------------------------------------------------------------------
# Decision rules


RULE_WS = (
    "volWindow/volStep minimise the coefficient of variation of σ̂ (pFast-based) in a "
    "stationary calm regime, subject to: σ̂ reaching 90 % of a calm → turbulent shift within "
    "max_sigma_lag_blocks ({lag:,} blocks, median path), the K12 cap trap after a 6-hour feed "
    "outage ending within the same bound, and volStep ≥ pFastWindow / 2 (samples of the pFast median "
    "no finer than half its window, D-RD-AUD-11). Keep the current pair unless the CV improves by more "
    "than materiality ({mat:.0%})."
)
RULE_REF = (
    "sigmaRefBps = the median σ̂ of the realised-volatility ensemble, measured on simulated "
    "pFast (D-WP3-6) at the recommended volWindow/volStep, rounded down to a multiple of "
    "{rnd} bps (so the unclamped median multiplier is ≥ 1×). Keep the current value while its "
    "median ratio σ̂/sigmaRef lies in the M14 band [{lo:g}×, {hi:g}×] and the rule value is "
    "within materiality ({mat:.0%}) of it."
)
RULE_CAP = (
    "sigmaMultMaxBps = the smallest multiple of {step:,} bps ≥ the p{pct} multiplier of the "
    "turbulent ensemble at the recommended sigmaRefBps and windows (unclamped). Keep the current "
    "cap while it covers that percentile and the rule value is within materiality ({mat:.0%}) "
    "of it (a cap above need costs collateral only in K12 traps)."
)


def _ws_table(results: ResultTable) -> ResultTable:
    b = results.base
    return results.filter(
        lambda r: (
            r.params["sigmaRefBps"] == b["sigmaRefBps"]
            and r.params["sigmaMultMaxBps"] == b["sigmaMultMaxBps"]
        )
    )


def _row_for(results: ResultTable, **kv) -> Any:
    for r in results:
        if all(r.params[k] == v for k, v in kv.items()):
            return r
    return None


def sampling_ok(params) -> bool:
    """D-RD-AUD-11: volStep ≥ pFastWindow / 2 (σ̂ samples the pFast median no finer than half its window)."""
    return int(params["volStep"]) * 2 >= int(params["pFastWindow"])


def ref_replay_notes(cv: Mapping, ref: int) -> list[str]:
    """Cross-check of the reference on the real history (D-RD-ORA-3/5): the median σ̂ of the replayed
    pFast per era, the median multiplier there at ``ref``, and the like-for-like comparison with the
    raw-return volatilities (σ̂ is a 42-sample estimator on a 2-hour lower median sampled hourly: it
    sits far below the 1-hour return volatility, which carries the aggregator's noise)."""
    eras = [k[len("replay_sigma_hat_p50_") :] for k in cv if str(k).startswith("replay_sigma_hat_p50_")]
    if not eras or ref <= 0:
        return []
    bits = []
    for e in eras:
        m = float(cv.get(f"replay_sigma_hat_p50_{e}", math.nan))
        bits.append(f"{e} {m:,.0f} bps ({max(m / ref, 1.0):.2f}×)")
    full = float(cv.get("replay_sigma_hat_p50_full", math.nan))
    out = [
        "Real-history replay (D-RD-ORA-3), median σ̂ of pFast and the median multiplier at "
        f"{ref:,}: " + "; ".join(bits) + ".",
        "Like for like: over the full history σ̂ (median "
        f"{full:,.0f} bps) compares with the true price's realised volatility at the 1-hour sampling step "
        f"{float(cv.get('replay_true_vol_step_bps_full', math.nan)):,.0f} bps and at one day "
        f"{float(cv.get('replay_true_vol_day_bps_full', math.nan)):,.0f} bps: the 2-hour lower median "
        "strips the aggregator's hour-to-hour noise (lag-1 autocorrelation −0.2), so the reference must "
        "be compared with σ̂ on pFast, never with the 440 % hourly or 235 % daily return volatility.",
    ]
    return out


def decide_g2(results: ResultTable, policy: Policy) -> list[Recommendation]:
    """The three §5.2 rules in order: windows → reference → cap; volPeriodsPerYear derived."""
    base = results.base
    mat = float(policy.materiality)
    ws = _ws_table(results)
    cur = ws.current(WS)
    if cur is None:
        raise ValueError("G2: the current set was not evaluated")
    prov = cur.metrics.provenance
    meta = cur.metrics.meta
    conf = G1._confidence(prov, str(meta.get("budget", "quick")))
    out = G1.evidence_dir(meta.get("out_dir"), GROUP)

    # 1 — volWindow / volStep
    dec = decide_with_materiality(ws, policy, params=WS, metric="sigma_hat_cv_calm")
    chosen = dec.row
    v_ws = final_verdict(dec.verdict, prov)
    better_bad = [
        r
        for r in ws
        if float(r.metrics.values["sigma_hat_cv_calm"]) < float(chosen.metrics.values["sigma_hat_cv_calm"])
        and not r.metrics.feasible
    ]
    viol: dict[str, int] = {}
    for r in better_bad:
        for c in r.metrics.violated:
            viol[c] = viol.get(c, 0) + 1
    if dec.verdict == "BLOCKED":
        bind_ws = dec.reason
    else:
        bits = []
        if viol:
            bits.append("lower-CV pairs violate " + ", ".join(f"{k} ({n})" for k, n in viol.items()))
        if dec.verdict == "KEEP":
            bits.append(f"materiality: {dec.reason}")
        bind_ws = "; ".join(bits) or "CV minimum within the searched grid"
    cv = chosen.metrics.values

    # 2 — sigmaRefBps
    rnd = int(policy.sigma_ref_round_bps)
    lo_b, hi_b = (float(x) for x in policy.sigma_accept_band)
    spec = REGISTRY["sigmaRefBps"]
    p50 = float(cv["sigma_hat_p50_realised"])
    ref_rule = int(min(max((int(p50) // rnd) * rnd, spec.bounds[0]), spec.bounds[1]))
    ref_cur = base.as_int("sigmaRefBps")
    ratio_cur = p50 / ref_cur
    in_band = lo_b <= ratio_cur <= hi_b
    rel = abs(ref_rule - ref_cur) / ref_cur
    if in_band and rel <= mat:
        ref_v, ref_rec, ref_why = (
            "KEEP",
            ref_cur,
            (
                f"current ratio {ratio_cur:.2f}× is in the M14 band and the rule "
                f"value {ref_rule:,} is within {rel:.0%} ≤ {mat:.0%}"
            ),
        )
    else:
        ref_v, ref_rec = "CHANGE", ref_rule
        ref_why = (
            f"current ratio {ratio_cur:.2f}× lies outside the M14 band [{lo_b:g}, {hi_b:g}]"
            if not in_band
            else f"rule value {ref_rule:,} differs by {rel:.0%} > {mat:.0%}"
        )

    # 3 — sigmaMultMaxBps
    cspec = REGISTRY["sigmaMultMaxBps"]
    pct = int(policy.sigma_mult_cap_pctl)
    turb_q = float(cv.get(f"sigma_hat_p{pct}_turbulent", cv["sigma_hat_p99_turbulent"]))
    # with a real price the need is read on the real history (D-RD-ORA-5): σ̂ of the replayed pFast
    # over the whole history with the one-hour aggregator prints removed — the market's own tail,
    # through the node's arithmetic — instead of the parametric turbulent ensemble
    real_q = float(cv.get(f"replay_despiked_sigma_hat_p{pct}_full", math.nan))
    ens_q = turb_q
    if math.isfinite(real_q) and real_q > 0:
        turb_q = real_q
    need = max(turb_q * BPS / ref_rec, BPS)
    step = cspec.step
    cap_rule = int(math.ceil(need / step) * step)
    cap_cur = base.as_int("sigmaMultMaxBps")
    cap_blocked = cap_rule > cspec.bounds[1]
    cap_rule = int(min(max(cap_rule, cspec.bounds[0]), cspec.bounds[1]))
    covers = cap_cur >= need
    crel = (cap_cur - cap_rule) / cap_cur
    if cap_blocked:
        # least violating = the bound (the cap closest to covering the percentile), as for every other
        # BLOCKED rule (D-RD-AUD-7); the report shows it as the owner's fallback, not a recommendation
        cap_v, cap_rec, cap_why = (
            "BLOCKED",
            int(cspec.bounds[1]),
            (
                f"p{pct} turbulent multiplier {need / BPS:.2f}× exceeds the search bound "
                f"{cspec.bounds[1]:,}; least violating: the bound"
            ),
        )
    elif covers and crel <= mat:
        cap_v, cap_rec, cap_why = (
            "KEEP",
            cap_cur,
            (
                f"current cap covers p{pct} ({need / BPS:.2f}×); rule value "
                f"{cap_rule:,} is {crel:.0%} lower ≤ {mat:.0%}"
            ),
        )
    else:
        cap_v, cap_rec = "CHANGE", cap_rule
        cap_why = (
            f"current cap {cap_cur:,} does not cover p{pct} = {need / BPS:.2f}×"
            if not covers
            else f"rule value {cap_rule:,} is {crel:.0%} below the current cap > {mat:.0%}"
        )

    # what moves the cap's need (D-RD-AUD-7): the windows (σ̂ noise grows as pFastWindow, volWindow
    # and volStep shrink) and single jumps — one 40 % jump in a 2-day window alone reads as σ̂ ≈ 480 %
    cur_q = float(cur.metrics.values.get(f"sigma_hat_p{pct}_turbulent", math.nan))
    cap_notes = [
        f"p{pct} turbulent σ̂: {cur_q:,.0f} bps at the current windows (volWindow "
        f"{cur.params.as_int('volWindow')}, volStep {cur.params.as_int('volStep')}, pFastWindow "
        f"{cur.params.as_int('pFastWindow')}) vs {turb_q:,.0f} bps at the chosen ones; the need is "
        f"σ̂/sigmaRefBps = {need / BPS:.2f}× at the recommended reference {ref_rec:,}",
        "The turbulent ensemble is a placeholder Merton process (12 jumps/yr, N(−2 %, 20 %)); its p99 σ̂ "
        "is set by single jumps inside the volWindow, so the cap need is a statement about jump size, "
        "not about sustained volatility (docs/studies/g2.md, Assumptions).",
    ]
    if math.isfinite(real_q):
        rq = {k: float(cv.get(k, math.nan)) for k in cv if str(k).startswith("replay_")}
        cap_notes.insert(
            0,
            f"Need read on the real history (D-RD-ORA-5): p{pct} σ̂ of the replayed pFast, one-hour prints "
            f"removed, {real_q:,.0f} bps (with the prints "
            f"{rq.get(f'replay_sigma_hat_p{pct}_full', math.nan):,.0f}; 2021-22 "
            f"{rq.get(f'replay_sigma_hat_p{pct}_2021-22', math.nan):,.0f}, 2025-26 "
            f"{rq.get(f'replay_sigma_hat_p{pct}_2025-26', math.nan):,.0f}); the turbulent ensemble's "
            f"p{pct} is {ens_q:,.0f} bps.",
        )
    if cap_blocked:
        cap_notes.append(
            "A cap at the bound turns every K12 trap (an undefined sample) into a mint requirement of "
            f"{cspec.bounds[1] / BPS:.0f}× the base ratio; at the current cap it is "
            f"{cap_cur / BPS:.0f}× (cap_undefined_h_per_year_realised = "
            f"{float(cur.metrics.values.get('cap_undefined_h_per_year_realised', math.nan)):.1f} h/yr)."
        )
    evidence = write_evidence(results, ws, cur, chosen, out)
    keys_ws = (
        "sigma_hat_cv_calm",
        "sigma_hat_cv_realised",
        "responsiveness_blocks",
        "responsiveness_p90_blocks",
        "k12_trap_after_feed_blocks",
        "k12_trap_h_per_outage",
        "cap_undefined_h_per_year_realised",
        "cap_vol_h_per_year_realised",
        "mult_p50_calm",
        "mult_p95_calm",
        "mult_p99_calm",
        "mult_p50_turbulent",
        "mult_p95_turbulent",
        "mult_p99_turbulent",
    )
    mc = {k: cur.metrics.values.get(k) for k in keys_ws}
    mr = {k: chosen.metrics.values.get(k) for k in keys_ws}
    ctx = {
        "sigma_hat_p50_realised": p50,
        "true_vol_bps_realised": cv.get("true_vol_bps_realised"),
        "sigma_hat_p99_turbulent": cv["sigma_hat_p99_turbulent"],
        "sigma_hat_p50_calm": cv.get("sigma_hat_p50_calm"),
        "sigma_hat_p50_turbulent": cv.get("sigma_hat_p50_turbulent"),
    }

    def sens(param: str, metric: str, name: str) -> dict:
        try:
            o = oat_from_table(results, param, metric=metric)
            return {
                "oat_values": o.values.tolist(),
                "oat_metric": o.metric.tolist(),
                "metric": metric,
                "sentence": sensitivity_sentence(param, o, name),
            }
        except Exception as e:  # pragma: no cover
            return {"sentence": f"sensitivity unavailable ({e})"}

    recs = []
    fmt = dict(lag=int(policy.max_sigma_lag_blocks), mat=mat, rnd=rnd, lo=lo_b, hi=hi_b, step=step, pct=pct)
    for p in WS:
        recs.append(
            Recommendation(
                p,
                base[p],
                chosen.params[p],
                v_ws,
                RULE_WS.format(**fmt),
                bind_ws,
                {
                    "current": mc,
                    "recommended": mr,
                    "decision": dec.reason,
                    "improvement": dec.improvement,
                    **ctx,
                },
                sens(p, "sigma_hat_cv_calm", "the calm σ̂ CV"),
                conf,
                prov,
                list(evidence),
                GROUP,
                [
                    f"rules: {', '.join(REGISTRY[p].rules)}",
                    f"materiality: {dec.reason}",
                    f"underlying verdict before the provenance rule: {dec.verdict}",
                ],
            )
        )
    recs.append(
        Recommendation(
            "volPeriodsPerYear",
            base["volPeriodsPerYear"],
            chosen.params["volPeriodsPerYear"],
            v_ws,
            "derived (K13): BLOCKS_PER_YEAR / volStep",
            "follows volStep",
            {"current": mc, "recommended": mr},
            {"sentence": "follows volStep"},
            conf,
            prov,
            list(evidence),
            GROUP,
            ["Derived from volStep; recommend the parent, the child follows."],
        )
    )
    recs.append(
        Recommendation(
            "sigmaRefBps",
            ref_cur,
            ref_rec,
            final_verdict(ref_v, prov),  # type: ignore[arg-type]
            RULE_REF.format(**fmt),
            f"M14 band / rule: {ref_why}",
            {
                "current": {
                    "sigmaRefBps": ref_cur,
                    "m14_ratio": ratio_cur,
                    "mult_p50_realised_clamped": max(ratio_cur, 1.0),
                },
                "recommended": {
                    "sigmaRefBps": ref_rec,
                    "m14_ratio": p50 / ref_rec,
                    "mult_p50_realised_clamped": max(p50 / ref_rec, 1.0),
                },
                "rule_value": ref_rule,
                **ctx,
            },
            sens("sigmaRefBps", "m14_ratio", "the median σ̂/sigmaRef ratio"),
            conf,
            prov,
            list(evidence),
            GROUP,
            [
                f"underlying verdict before the provenance rule: {ref_v}",
                f"σ̂ measured on pFast (D-WP3-6); true-price vol of the same ensemble "
                f"{(cv.get('true_vol_bps_realised') or float('nan')):,.0f} bps",
                *ref_replay_notes(cv, ref_rec),
            ],
        )
    )
    recs.append(
        Recommendation(
            "sigmaMultMaxBps",
            cap_cur,
            cap_rec,
            final_verdict(cap_v, prov) if cap_v != "BLOCKED" else "BLOCKED",
            RULE_CAP.format(**fmt),
            f"turbulent p{pct}: {cap_why}",
            {
                "current": {"sigmaMultMaxBps": cap_cur, "covers": covers},
                "recommended": {"sigmaMultMaxBps": cap_rec},
                "rule_value": cap_rule,
                f"turb_mult_p{pct}_at_rec_ref": need / BPS,
                **ctx,
            },
            sens("sigmaMultMaxBps", "mult_p99_turbulent", "the turbulent p99 multiplier"),
            conf,
            prov,
            list(evidence),
            GROUP,
            [f"underlying verdict before the provenance rule: {cap_v}", *cap_notes],
        )
    )
    return recs


# ---------------------------------------------------------------------------------------------------
# Evidence


def write_evidence(results: ResultTable, ws: ResultTable, cur, chosen, out) -> list:
    paths = [results.to_csv(out / "g2_results.csv")]
    plt = G1.plot_style()
    if plt is None:  # pragma: no cover
        return paths
    # 1 — σ̂ CV vs volWindow per volStep, infeasible marked
    fig, ax = plt.subplots(figsize=(7.0, 3.4))
    steps = sorted({r.params.as_int("volStep") for r in ws})
    colors = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]  # fixed slot order
    for i, s in enumerate(steps):
        rows = sorted(
            (r for r in ws if r.params.as_int("volStep") == s), key=lambda r: r.params.as_int("volWindow")
        )
        xs = [r.params.as_int("volWindow") / BLOCKS_PER_DAY for r in rows]
        ys = [float(r.metrics.values["sigma_hat_cv_calm"]) for r in rows]
        c = colors[i % len(colors)]
        ax.plot(xs, ys, color=c, marker="o", ms=4, label=f"volStep {s}")
        for x, y, r in zip(xs, ys, rows, strict=True):
            if not r.metrics.feasible:
                ax.plot([x], [y], marker="x", color="#0b0b0b", ms=8, ls="none")
    ax.plot(
        [cur.params.as_int("volWindow") / BLOCKS_PER_DAY],
        [cur.metrics.values["sigma_hat_cv_calm"]],
        marker="o",
        ms=11,
        mfc="none",
        mec="#0b0b0b",
        ls="none",
        label="current",
    )
    ax.plot(
        [chosen.params.as_int("volWindow") / BLOCKS_PER_DAY],
        [chosen.metrics.values["sigma_hat_cv_calm"]],
        marker="s",
        ms=11,
        mfc="none",
        mec="#eb6834",
        ls="none",
        label="recommended",
    )
    ax.set_xlabel("volWindow (days)")
    ax.set_ylabel("CV of σ̂ (calm)")
    ax.set_title("σ̂ estimator noise vs window (x = violates lag / K12 bound)", loc="left", fontsize=10)
    ax.legend(fontsize=7, ncol=2)
    fig.tight_layout()
    p = out / "g2_cv_vs_window.png"
    fig.savefig(p, dpi=120)
    plt.close(fig)
    paths.append(p)
    # 2 — σ̂ after the calm → turbulent shift, current vs recommended
    sc = cur.metrics.meta.get("series", {}).get("shift")
    sr = chosen.metrics.meta.get("series", {}).get("shift")
    if sc:
        fig, ax = plt.subplots(figsize=(7.0, 3.2))

        def line(s, color, label):
            y = np.asarray(s["sigma_hat"], float)
            y = np.where(y >= 0, y / 100, np.nan)
            x = (np.arange(y.size) * s["step_blocks"] - s["start"]) / BLOCKS_PER_DAY
            ax.plot(x, y, color=color, label=label)

        line(sc, G1.C_CUR, f"current {cur.params.as_int('volWindow')}/{cur.params.as_int('volStep')}")
        if sr and chosen is not cur:
            line(
                sr,
                G1.C_REC,
                f"recommended {chosen.params.as_int('volWindow')}/{chosen.params.as_int('volStep')}",
            )
        ax.axvline(0, color=G1.C_TRUE, lw=1, ls=":")
        ax.set_xlabel("days from the calm → turbulent shift")
        ax.set_ylabel("median σ̂ on pFast (%/yr)")
        ax.set_title("SIGMA-1 responsiveness", loc="left", fontsize=10)
        ax.legend(fontsize=8)
        fig.tight_layout()
        p = out / "g2_responsiveness.png"
        fig.savefig(p, dpi=120)
        plt.close(fig)
        paths.append(p)
    return paths


# ---------------------------------------------------------------------------------------------------
# Explanations


WHAT = {
    "volWindow": "how many blocks of pFast history SIGMA-1 measures volatility over",
    "volStep": "the spacing (blocks) between the pFast samples whose log returns SIGMA-1 squares",
    "volPeriodsPerYear": "the annualisation factor of SIGMA-1, fixed at BLOCKS_PER_YEAR / volStep (K13)",
    "sigmaRefBps": (
        "the reference volatility: the multiplier is σ̂ / sigmaRef, floored at 1×, so this is "
        "the volatility at which minting collateral starts to rise above the class base ratio"
    ),
    "sigmaMultMaxBps": (
        "the multiplier's cap (K12): the most collateral volatility can demand, and the "
        "value the multiplier is pinned at whenever a pFast sample is undefined"
    ),
}


def explain_g2(rec: Recommendation) -> str:
    """Plain-English paragraphs for one G2 recommendation."""
    p = rec.param
    m = rec.metrics
    c, r = m.get("current", {}), m.get("recommended", {})

    def g(d, k, f="{:.3f}"):
        x = d.get(k) if isinstance(d, Mapping) else None
        return "n/a" if x is None or (isinstance(x, float) and not math.isfinite(x)) else f.format(x)

    head = (
        f"{p} sets {WHAT[p]} (rules: {', '.join(REGISTRY[p].rules)}). Current {rec.current:,}; "
        f"recommended {rec.recommended:,} — verdict {rec.verdict} (provenance {rec.provenance}, "
        f"confidence {rec.confidence})."
    )
    if p == "volPeriodsPerYear":
        return "\n\n".join(
            (head, "It is derived, not tuned: it follows volStep so that σ̂ stays annualised.", rec.klass_note)
        )
    rule = f"Decision rule: {rec.rule}"
    if p in WS:
        nums = (
            f"At the current pair the calm σ̂ CV is {g(c, 'sigma_hat_cv_calm')}, σ̂ reaches 90 % of a regime "
            f"shift in {g(c, 'responsiveness_blocks', '{:,.0f}')} blocks (median), and a 6-hour feed outage "
            f"pins the multiplier at its cap for {g(c, 'k12_trap_h_per_outage', '{:.1f}')} h "
            f"({g(c, 'k12_trap_after_feed_blocks', '{:,.0f}')} blocks after the feed returns). Recommended: "
            f"CV {g(r, 'sigma_hat_cv_calm')}, responsiveness {g(r, 'responsiveness_blocks', '{:,.0f}')} "
            f"blocks, trap {g(r, 'k12_trap_h_per_outage', '{:.1f}')} h. Multiplier median/p95/p99: calm "
            f"{g(r, 'mult_p50_calm', '{:.2f}')}/{g(r, 'mult_p95_calm', '{:.2f}')}/"
            f"{g(r, 'mult_p99_calm', '{:.2f}')}×, "
            f"turbulent {g(r, 'mult_p50_turbulent', '{:.2f}')}/{g(r, 'mult_p95_turbulent', '{:.2f}')}/"
            f"{g(r, 'mult_p99_turbulent', '{:.2f}')}× (at the current reference and cap)."
        )
    elif p == "sigmaRefBps":
        nums = (
            f"The median σ̂ measured on pFast is {g(m, 'sigma_hat_p50_realised', '{:,.0f}')} bps, against a "
            f"true-price volatility of {g(m, 'true_vol_bps_realised', '{:,.0f}')} bps in the same ensemble: "
            f"the median filter smooths returns (D-WP3-6), so calibrating on raw price volatility would "
            f"set the reference too high. The median ratio σ̂/sigmaRef is {g(c, 'm14_ratio', '{:.2f}')}× at "
            f"the current value and {g(r, 'm14_ratio', '{:.2f}')}× at the recommended one; the rule value "
            f"is {m.get('rule_value', 'n/a'):,}."
        )
    else:
        key = [k for k in m if k.startswith("turb_mult_p")]
        pct = key[0].split("_")[2][1:] if key else "99"
        nums = (
            f"The turbulent ensemble's p{pct} multiplier at the "
            f"recommended reference is {g(m, key[0], '{:.2f}') if key else 'n/a'}× (uncapped); the rule "
            f"value is {m.get('rule_value', 'n/a'):,} bps. A cap above that is reached only through K12 "
            f"traps, where it sets minting collateral for every class."
        )
    bind = f"Binding: {rec.binding}."
    sens = rec.sensitivity.get("sentence", "")
    pts = rec.sensitivity.get("neighbours")
    neigh = ""
    if pts:
        bits = []
        for q in pts:
            if q.get("primary") is None:
                bits.append(f"{q['value']} is inadmissible ({'/'.join(q.get('rejected', []))})")
            elif not q.get("feasible"):
                bits.append(f"{q['value']} violates {', '.join(q.get('violated', []))}")
            else:
                bits.append(f"{q['value']} changes the CV by {-(q.get('improvement') or 0.0):+.1%}")
        neigh = "Neighbours: " + "; ".join(bits) + "."
    tail = rec.klass_note
    if rec.verdict == "PROVISIONAL":
        tail += " PROVISIONAL: synthetic prices only; recalibrate on real YEC data before locking."
    return "\n\n".join(x for x in (head, rule, nums, bind, sens, neigh, tail) if x)

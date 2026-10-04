"""G8 — price attestation: every v3 attest field of the registry's group G8 (PLAN §5.8).

Owner: WP-7c. Method, metrics and rules: ``docs/studies/g8.md``.

The study is a set of *families* (``ybcal.studies.g5_activation.Family``), one decision rule each:

==================  =============================================  ================================
family              params                                         rule kind / primary metric
==================  =============================================  ================================
diverge             divergeBpsAttest                               ported spreads.py: 3 × worst-pair p95, ↑100
pin_delta           pinDeltaBps                                    ported pinrate.py arming-rate rule
pin_window          pinWindow                                      verify: arming rate, false pins
pin_tags/bundles    pinMinTags / pinMinBundles                     min frozen-feed detection s.t. false pins
slack               kSlack                                         min bundle bytes s.t. P(no bundle)
select / slots      mSelect / nSlots                               verify: liveness, harmful capture
bundle_max          bundleMax                                      verify (520-byte push, m + k)
qlow                qLowBps (+ qHighBps derived)                   verify: harmful + griefing capture
interval            attestInterval (+ attestMaxAge = 2k derived)   verify: relay, staleness, liveness
arm_min / arm_delay attestArmMin / attestArmDelay                  verify: founding capture, liveness
required            attestRequired                                 design choice (W15), KEEP true
dorm_blocks/min     dormancyBlocks / dormancyMinBundles            min dead detection s.t. false ejection
dorm_check          dormancyCheck                                  verify
bond                bondMin                                        max griefing capital s.t. affordability
bond_lock/maturity  bondMinLock / bondMaturity                     verify
age_cap / founding  ageCap / foundingWindow                        verify: capture time, newcomer seating
persist / ttl       emergencyPersist / emergencyNoticeTtl          min crash shortfall s.t. premature / verify
==================  =============================================  ================================

Analytic helpers (``ybcal.sim.attest``: liveness, freshness, exact-kernel capture Monte Carlo,
false dormancy, griefing cost) drive the sweep; the attestation simulator (``attest.simulate``)
confirms liveness and dead-attestor ejection at the current and recommended sets in ``decide``.
"""

from __future__ import annotations

import contextlib
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ybcal.params.paramset import ParamSet
from ybcal.params.registry import REGISTRY, params_for_group
from ybcal.studies import _g8_ports as ports
from ybcal.studies.base import Budget, Env, Metrics, ResultTable
from ybcal.studies.g5_activation import (
    Family,
    FamilyStudy,
    budget_from_meta,
    env_out_dir,
    family_rows,
    stable_seed,
)
from ybcal.units import BLOCKS_PER_DAY, BLOCKS_PER_HOUR, BLOCKS_PER_YEAR, COIN, MICRO_USD_PER_USD

OWNER_WP = "WP-7c"

GROUP = "G8"

#: Judgement constants (no policy key yet — requested in docs/decisions.md D-WP7c-2).
JUDGEMENT: dict[str, float] = {
    "mean_outage_blocks": 48.0,          # attestor outage persistence for the Markov liveness sensitivity
    "capture_prob_max": 0.01,            # P(an entity at the policy weight share moves aMint UP by ≥ 10 %)
    "grief_prob_max": 0.05,              # P(... spans qLow of a bundle: moves aMint DOWN by ≥ 10 %)
    "capture_move_bps": 1000,            # the move that counts as captured
    "stale_share_of_diverge": 1 / 3,     # p99 price move over attestMaxAge ≤ this × divergeBpsAttest
    "premature_claim_max": 0.01,         # P(emergency claim lands on a vault a 1-h wick dipped)
    "vault_buffer": 0.05,                # vault coverage above emergencyRatio before the event
    "claim_reaction_blocks": 576,        # emergencyNoticeTtl − emergencyPersist ≥ this (12 h to claim)
    "registration_notice_blocks": 8064,  # bondMaturity ≥ this: a week of public notice before a bond counts
    "min_capture_days": 90.0,            # harmful capture by a capital-matched newcomer takes ≥ this
    "max_newcomer_seat_days": 365.0,     # an honest newcomer with an equal bond is seated within this
    "yec_vol_annual": 1.20,              # YEC-like annualised volatility when no real price data is given
}

#: Policy field that overrides each judgement constant once the integrator adds it (D-WP7c-2).
POLICY_KEYS: dict[str, str] = {
    "mean_outage_blocks": "attestor_mean_outage_blocks", "capture_prob_max": "max_harmful_capture_prob",
    "grief_prob_max": "max_grief_capture_prob", "premature_claim_max": "max_premature_claim_prob",
    "claim_reaction_blocks": "claim_reaction_blocks",
    "registration_notice_blocks": "registration_notice_blocks",
    "min_capture_days": "min_capture_days", "max_newcomer_seat_days": "max_newcomer_seat_days",
}


def judgement(policy, key: str) -> float:
    """``policy.<POLICY_KEYS[key]>`` when the Policy has it, else ``JUDGEMENT[key]``."""
    pk = POLICY_KEYS.get(key)
    return float(getattr(policy, pk, JUDGEMENT[key]) if pk else JUDGEMENT[key])


SEQ_SPACE = 65_535   # AttestorSeq.next is a u16 (doc/yellowback.md:80-85)


# ---------------------------------------------------------------------------------------------------
# Inputs shared across candidates (cached per process; keys include the seed and budget)

_CACHE: dict[tuple, Any] = {}


def _cached(key: tuple, fn):
    if key not in _CACHE:
        _CACHE[key] = fn()
    return _CACHE[key]


def _ekey(env: Env, *parts) -> tuple:
    keys = ("spreads", "price", "pool_shares", "hashrate")
    data_ids = tuple(sorted((k, id(v)) for k, v in env.data.items() if k in keys))
    return (env.seed, env.budget.name, env.budget.paths, data_ids, *parts)


def _real_price(env: Env):
    p = env.data.get("price") if isinstance(env.data, Mapping) else None
    if p is not None and getattr(p, "provenance", "") == "real":
        return p
    return None


def _adoption(env: Env) -> dict[str, float]:
    pol = env.policy
    return dict(pol.adoption_scenarios.get(pol.adoption_case, {}))


def bundles_per_block(env: Env) -> float:
    """Bundle demand at the policy adoption case (mints carry bundles; claims are rare)."""
    a = _adoption(env)
    return max(1e-9, float(a.get("mints_per_day", 2.0)) / BLOCKS_PER_DAY)


def spreads_inputs(env: Env) -> dict[str, Any]:
    """The ported spreads.py analysis on real ``env.data["spreads"]`` or a synthetic fortnight, plus the
    per-row worst-pair spread in calm and around the crash-70-1d ramp (MINT-10 refusal proxies)."""

    def build() -> dict[str, Any]:
        from ybcal.data.synthetic import GBM, SpreadModel

        log = env.data.get("spreads") if isinstance(env.data, Mapping) else None
        rng = env.rng_for(GROUP, "spreads")
        model = SpreadModel()
        if log is not None and hasattr(log, "prices") and len(log):
            rows = ports.rows_from_spreads_log(log)
            names = list(log.names)
            prov = "real-data"
            with contextlib.suppress(Exception):
                model = SpreadModel.fit(log)
        else:
            names = list(model.names)
            n = int(ports.DEFAULT_DURATION_DAYS * 86400 // ports.DEFAULT_INTERVAL)
            npaths = 4
            from ybcal.data.pricepath import DT_BLOCK

            step = ports.DEFAULT_INTERVAL // 75                     # 300 s = 4 blocks
            true = GBM(sigma=0.6).simulate(npaths, n * step, DT_BLOCK, rng).prices[:, ::step]
            q = model.generate(true, rng, step_seconds=ports.DEFAULT_INTERVAL).quotes
            rows = []
            for i in range(npaths):
                # the synthetic fortnights are laid end to end (no gap between them)
                ts = 1_700_000_000 + (i * q.shape[2] + np.arange(q.shape[2])) * ports.DEFAULT_INTERVAL
                rows += ports.rows_from_quotes(ts, q[i], names)
            prov = "synthetic"
        res = ports.analyze_spreads(
            rows,
            ports.DEFAULT_INTERVAL,
            multiple=float(getattr(env.policy, "diverge_spread_multiplier", 3.0)),
            names=names,
        )
        calm = _row_max_spreads(rows, names)
        crash = _crash_spreads(env, model, rng)
        return {"res": res, "target": res["recommended_bps"], "provenance": prov, "calm": calm,
                "crash": crash, "names": names,
                "refresh_per_block": min(model.refresh_per_hour) / BLOCKS_PER_HOUR}

    return _cached(_ekey(env, "spreads"), build)


def _row_max_spreads(rows, names) -> np.ndarray:
    out = []
    for _, pr in rows:
        v = [pr[n] for n in names if pr.get(n)]
        if len(v) >= 2:
            out.append(ports.spread_bps(max(v), min(v)))
    return np.asarray(out, dtype=float)


def _crash_spreads(env: Env, model, rng) -> np.ndarray:
    try:
        from ybcal.data import scenarios as sc

        run = sc.load_library()["crash-70-1d"].generate(rng, n_paths=2, resolution="block", horizon_days=24)
        p = run.paths.prices[:, 18 * BLOCKS_PER_DAY: 23 * BLOCKS_PER_DAY: 4]   # 300 s around the ramp
        q = model.generate(p, rng, step_seconds=ports.DEFAULT_INTERVAL).quotes
        rows = []
        for i in range(q.shape[0]):
            rows += ports.rows_from_quotes(np.arange(q.shape[2]) * 300, q[i], list(model.names))
        return _row_max_spreads(rows, list(model.names))
    except Exception:  # pragma: no cover - scenario library optional
        return np.zeros(0)


def pin_histories(env: Env) -> tuple[list, str]:
    """Hourly USD histories for the pinrate port: the real price (resampled to hours) or synthetic
    YEC-like GARCH paths (90 days each, CoinGecko's hourly range)."""

    def build():
        real = _real_price(env)
        if real is not None:
            from ybcal.data.pricepath import resample

            pp = real if real.resolution == "hour" else resample(real, "hour")
            out = []
            for i in range(pp.prices.shape[0]):
                usd = pp.prices[i].astype(float) / MICRO_USD_PER_USD
                out.append(ports.series_from_prices(usd, 3600))
            return out, "real-data"
        from ybcal.data.pricepath import DT_HOUR
        from ybcal.data.synthetic import preset

        n = max(4, min(16, env.budget.paths // 4))
        pp = preset("garch").simulate(n, 90 * 24, DT_HOUR, env.rng_for(GROUP, "pin"))
        return [ports.series_from_prices(pp.prices[i].astype(float) / MICRO_USD_PER_USD, 3600)
                for i in range(n)], "synthetic"

    return _cached(_ekey(env, "pinhist"), build)


def pinrate_at(env: Env, window: int, delta: int) -> dict:
    hist, _ = pin_histories(env)
    return _cached(_ekey(env, "pinrate", window, delta),
                   lambda: ports.pooled_pinrate(hist, window, delta))


def _small_pool_share(env: Env) -> float:
    from ybcal.data.synthetic import PoolModel

    return float(min(PoolModel().shares))


def false_pin_per_day(env: Env, window: int, min_tags: int, p_armed: float, share: float,
                      refresh_per_block: float) -> float:
    """Monte Carlo: P(an honest pool of hash share ``share`` whose feed refreshes ``refresh_per_block``
    times per block is PIN-1-pinned at least once in a day), the test being armed in a given hour
    with probability ``p_armed``. Pinned at H iff it has ≥ pinMinTags tags in ``[H − pinWindow, H − 1]``
    all with one quote (no feed refresh between its first and last tag there)."""

    def run() -> float:
        if p_armed <= 0:
            return 0.0
        rng = np.random.default_rng([stable_seed(GROUP, "falsepin", window, min_tags), env.seed])
        P, days = 8, 30
        n = days * BLOCKS_PER_DAY
        mined = rng.random((P, n)) < share
        refresh = rng.random((P, n)) < -math.expm1(-refresh_per_block)
        qid = np.cumsum(refresh, axis=1)
        idx = np.arange(n)
        prv = np.maximum.accumulate(np.where(mined, idx, -1), axis=1)            # last tag ≤ i
        nxt = np.flip(np.minimum.accumulate(np.flip(np.where(mined, idx, n), axis=1), axis=1), axis=1)
        c = np.concatenate([np.zeros((P, 1), np.int64), np.cumsum(mined, axis=1)], axis=1)
        H = np.arange(window + 1, n)
        cnt = c[:, H] - c[:, H - window]                                          # tags in [H-W, H-1]
        first = nxt[:, H - window]
        last = prv[:, H - 1]
        ok = (cnt >= max(1, min_tags)) & (first <= last)
        fq = np.take_along_axis(qid, np.clip(first, 0, n - 1), axis=1)
        lq = np.take_along_axis(qid, np.clip(last, 0, n - 1), axis=1)
        pinned = ok & (fq == lq)
        armed_h = rng.random((P, n // BLOCKS_PER_HOUR + 1)) < p_armed
        pinned &= armed_h[:, H // BLOCKS_PER_HOUR]
        day = H // BLOCKS_PER_DAY
        hit = np.zeros((P, days), dtype=bool)
        for p in range(P):
            hit[p, np.unique(day[pinned[p]])] = True
        return float(hit.mean())

    return _cached(_ekey(env, "falsepin", window, min_tags, round(p_armed, 6), share, refresh_per_block), run)


def p_rows_at_least(k: int, mean: float) -> float:
    from scipy.stats import poisson

    return float(poisson.sf(k - 1, mean)) if k > 0 else 1.0


def pin_detect_blocks(window: int, min_tags: int, min_bundles: int, share: float, bpb: float) -> float:
    """Expected blocks for PIN-1 to pin a frozen pool (share ``share``) once the market moved: it
    needs ≥ pinMinTags of its tags and ≥ pinMinBundles BundleLog rows inside one window. Each need is
    met after ``need/rate`` blocks when that fits in the window, else after ``W / P(≥ need in W)``
    (waiting for a window that happens to hold enough)."""
    from scipy.stats import binom

    def t(need: int, rate: float, p_in_window: float) -> float:
        if need <= 0:
            return 0.0
        if need / max(rate, 1e-12) <= window:
            return need / rate
        return window / p_in_window if p_in_window > 0 else math.inf

    t_tags = t(min_tags, share, float(binom.sf(min_tags - 1, window, share)))
    t_rows = t(min_bundles, bpb, p_rows_at_least(min_bundles, bpb * window))
    return max(t_tags, t_rows)


def dead_detect_times(rng: np.random.Generator, n_paths: int, r: float, dormancy_blocks: int,
                      min_bundles: int, check: int, *, t_min: int | None = None,
                      horizon_windows: int = 8) -> np.ndarray:
    """Monte Carlo blocks from an attestor's death (t = 0) to DORMANT: it is selected in a row with
    probability ``r`` per block and signs none; at every check height ``t ≥ t_min`` (default
    ``dormancyBlocks``: its pre-death window held rows it signed) it is ejected once
    ``(t − dormancyBlocks, t] ∩ [0, t]`` holds ≥ max(1, min_bundles) selected rows. ``inf`` = not
    within ``horizon_windows`` windows."""
    t_min = dormancy_blocks if t_min is None else t_min
    n_int = max(1, (horizon_windows * dormancy_blocks) // check)
    per = rng.binomial(check, min(1.0, r), size=(n_paths, n_int))
    c = np.concatenate([np.zeros((n_paths, 1), np.int64), np.cumsum(per, axis=1)], axis=1)
    w = max(1, dormancy_blocks // check)
    j = np.arange(1, n_int + 1)
    cnt = c[:, j] - c[:, np.maximum(0, j - w)]
    hit = (cnt >= max(1, min_bundles)) & (j * check >= t_min)[None, :]
    anyh = hit.any(axis=1)
    return np.where(anyh, (np.argmax(hit, axis=1) + 1) * check, np.inf).astype(float)


def dead_detect_p95(env: Env, dormancy_blocks: int, min_bundles: int, check: int, r: float) -> float:
    """p95 of :func:`dead_detect_times` (512 paths); ``inf`` when more than 5 % are not ejected
    within 8 windows."""

    def run() -> float:
        rng = np.random.default_rng([stable_seed(GROUP, "dead", dormancy_blocks, min_bundles, check),
                                     env.seed])
        t = dead_detect_times(rng, 512, r, dormancy_blocks, min_bundles, check)
        return float(np.quantile(t, 0.95)) if np.isfinite(t).mean() >= 0.95 else math.inf

    return _cached(_ekey(env, "dead", dormancy_blocks, min_bundles, check, round(r, 10)), run)


def capture_probs(env: Env, n_slots: int, m: int, k: int, q_low: int, share: float) -> dict[str, float]:
    """Exact-kernel Monte Carlo (``attest.capture_probability``): one entity holds ``share`` of the
    seated weight in one seat, ``n_slots − 1`` honest seats share the rest equally; every selected
    seat signs. Returns the probability that it moves aMint down (griefing, needs ≥ qLow of the
    bundle weight), aMint up (theft, > 1 − qLow) and aClaim down (premature claims, ≥ qHigh) by
    ``capture_move_bps``, and its mean share of the bundle weight."""
    from ybcal.sim.attest import capture_probability

    def run() -> dict[str, float]:
        H = 1_000_000
        E = max(1, round(share / (1 - share) * (n_slots - 1) * H))
        w = [E] + [H] * (n_slots - 1)
        adv = [True] + [False] * (n_slots - 1)
        nd = 400 if env.budget.name == "quick" else 2000
        seed = stable_seed(GROUP, "capture", env.seed)
        kw = dict(m_select=m, k_slack=k, q_low_bps=q_low, q_high_bps=10_000 - q_low,
                  move_bps=int(JUDGEMENT["capture_move_bps"]), n_draws=nd, seed=seed)
        sel = min(n_slots, m + k)
        others = (sel - 1) * H
        return {"grief": capture_probability(w, adv, direction="a_mint_down", **kw),
                "harm": capture_probability(w, adv, direction="a_mint_up", **kw),
                "claim_grief": capture_probability(w, adv, direction="a_claim_down", **kw),
                "bundle_share_when_selected": E / (E + others)}

    return _cached(_ekey(env, "capture", n_slots, m, k, q_low, share), run)


def emergency_paths(env: Env) -> dict[str, np.ndarray]:
    """Block-resolution true prices around a 1-h wick (flash-wick-50-1h) and a 1-day −70 % ramp
    (crash-70-1d), with the event start column."""

    def build():
        from ybcal.data import scenarios as sc

        lib = sc.load_library()
        n = max(16, min(64, env.budget.paths))
        wick = lib["flash-wick-50-1h"].generate(env.rng_for(GROUP, "wick"), n_paths=n, resolution="block",
                                                horizon_days=12).paths.prices
        crash = lib["crash-70-1d"].generate(env.rng_for(GROUP, "crash"), n_paths=n, resolution="block",
                                            horizon_days=24).paths.prices
        return {"wick": wick, "wick_at": 10 * BLOCKS_PER_DAY, "crash": crash, "crash_at": 20 * BLOCKS_PER_DAY}

    return _cached(_ekey(env, "emergency"), build)


def emergency_metrics(env: Env, persist: int, ratio_bps: int, max_age: int) -> dict[str, float]:
    """A vault at ``emergencyRatio + vault_buffer`` coverage before the event; pEmerg ≈ the attestors'
    price, i.e. the true price ``maxAge/2`` blocks old (min(xClaim, aClaim) ≤ aClaim; the pool medians
    lag far more). Notice at the first block below the ratio; the earliest claim at notice + persist
    succeeds iff still below. Wick: P(claim succeeds) = premature-claim probability. Crash: mean / p95
    bad-debt fraction ``max(0, 1 − coverage)`` at the claim."""

    def run() -> dict[str, float]:
        d = emergency_paths(env)
        r = ratio_bps / 10_000
        c0 = r + JUDGEMENT["vault_buffer"]
        lag = max(0, max_age // 2)
        out = {}
        for name in ("wick", "crash"):
            p = d[name].astype(float)
            at = d[f"{name}_at"]
            ref = p[:, at - 1]
            pe = np.roll(p, lag, axis=1)
            cov = c0 * pe / ref[:, None]
            below = cov < r
            below[:, :at] = False
            t1 = np.where(below.any(axis=1), np.argmax(below, axis=1), -1)
            claim_t = np.where(t1 >= 0, np.minimum(t1 + persist, p.shape[1] - 1), -1)
            ok = (t1 >= 0) & np.take_along_axis(below, np.maximum(claim_t, 0)[:, None], axis=1)[:, 0]
            true_cov = c0 * np.take_along_axis(p, np.maximum(claim_t, 0)[:, None], axis=1)[:, 0] / ref
            short = np.where(ok, np.maximum(0.0, 1.0 - true_cov), 0.0)
            if name == "wick":
                out["premature"] = float(ok.mean())
            else:
                out["shortfall_mean"] = float(short.mean())
                out["shortfall_p95"] = float(np.quantile(short, 0.95))
                out["claimed"] = float(ok.mean())
        return out

    return _cached(_ekey(env, "em", persist, ratio_bps, max_age), run)


#: Scenarios of the aggregated-feed MINT-10 model (G6's judgement streams: the same pools, venues, agents).
MINT10_SCENARIOS: tuple[str, ...] = ("calm-90d", "crash-70-1d", "pump-dump-3x")
#: Bundle size of the MINT-10 model (mSelect + kSlack at the current set; every selected attestor live).
MINT10_STRIDE = 4  # sample every 4th height (300 s, the spreads.py cadence)
MINT10_KEEP = 20_001


def mint10_gaps(env: Env, cand, scenario: str, venue: int | None = None) -> np.ndarray:
    """|pFast(R) − aMint|·10⁴/min (MINT-10's form, state.cpp:371-377) at every 4th height of a G6
    judgement stream: pFast = PRICE-1's lower median of the pools' quote tags over pFastWindow
    (``oracle.price_series``); aMint = the qLow weighted quantile (equal weights, math.h:228-245) of
    ``m + k`` attestations, each the shipped agent's price (``ybcal.sim.feeds.agent_quotes`` on the same
    venues; or venue ``venue``'s 15-minute TWAP alone: a mis-configured attestor set) cited
    ``U{1..attestInterval}`` blocks before R, times the pool agents' per-quote noise. Heights without
    pFast or without ``mSelect`` attestations are skipped (no mint is built there)."""
    from ybcal.studies import g6_miners_fees as G6

    keys = ("pFastWindow", "pFastMinFill", "pMidWindow", "pMidMinFill", "pSlowWindow", "pSlowMinFill",
            "divergenceBps", "attestInterval", "mSelect", "kSlack", "qLowBps")
    sig = tuple(int(cand[k]) for k in keys)

    def run() -> np.ndarray:
        st = G6.judge_stream(env, scenario)
        if venue is None:
            feed = st.agent
        else:
            if st.venue_twap is None:
                return np.zeros(0)
            feed = st.venue_twap[:, venue, :].astype(np.int64)
        if feed is None:
            return np.zeros(0)
        params = {k: int(cand[k]) for k in keys}
        pf = _p_fast(env, scenario, params["pFastWindow"], params["pFastMinFill"])
        P, n = feed.shape
        vk = venue if venue is not None else -1
        rng = np.random.default_rng([stable_seed(GROUP, "mint10", scenario, vk, *sig), env.seed])
        cols = np.arange(max(64, params["attestInterval"] + 1), n, MINT10_STRIDE)
        nb = max(params["mSelect"], params["mSelect"] + params["kSlack"])
        ages = rng.integers(1, max(1, params["attestInterval"]) + 1, size=(P, len(cols), nb))
        at = np.clip(cols[None, :, None] - ages, 0, n - 1).reshape(P, -1)
        src = np.take_along_axis(feed, at, axis=1).reshape(P, len(cols), nb).astype(np.float64)
        noise = float(np.mean(G6.pool_model(env)[0].noise_bps)) / 1e4
        att = np.where(src > 0, src * np.exp(noise * rng.standard_normal(src.shape)), np.nan)
        cnt = np.sum(np.isfinite(att), axis=2)
        srt = np.sort(att, axis=2)  # NaN last
        idx = np.maximum(0, -((-cnt * params["qLowBps"]) // 10_000) - 1)
        a_mint = np.take_along_axis(srt, idx[:, :, None], axis=2)[:, :, 0]
        x = pf[:, cols].astype(np.float64)
        ok = (x > 0) & (cnt >= params["mSelect"]) & np.isfinite(a_mint)
        with np.errstate(invalid="ignore"):
            gap = np.sort((np.abs(x - a_mint) * 10_000 / np.minimum(x, a_mint))[ok])
        if len(gap) > MINT10_KEEP:  # keep 20,001 order statistics (refusal rates to 5·10⁻⁵; memory)
            gap = np.quantile(gap, np.linspace(0.0, 1.0, MINT10_KEEP))
        return gap.astype(np.float32)

    return _cached(_ekey(env, "mint10", scenario, venue, sig, G6._data_ids(env)), run)


def _p_fast(env: Env, scenario: str, window: int, fill: int) -> np.ndarray:
    """PRICE-1's pFast of a G6 judgement stream (lower median over ``window``, undefined below
    ``fill``), memoised: it depends on neither the attestation parameters nor the candidate."""
    from ybcal.model.vkernels import RollingMedian
    from ybcal.studies import g6_miners_fees as G6

    def run() -> np.ndarray:
        st = G6.judge_stream(env, scenario)
        return RollingMedian(st.tag_price).median(window, fill)

    return _cached(_ekey(env, "pfast", scenario, window, fill, G6._data_ids(env)), run)


def refusal_rate(sorted_gaps: np.ndarray, div_bps: int) -> float:
    """Share of the gaps MINT-10 refuses (``gap > divergeBpsAttest``; the rule is ``≤``)."""
    if not len(sorted_gaps):
        return math.nan
    return float(1.0 - np.searchsorted(sorted_gaps, float(div_bps), side="right") / len(sorted_gaps))


def agent_refresh_per_block(env: Env) -> float:
    """Refresh rate of an honest pool's published quote: ``−ln P(unchanged between consecutive
    blocks)`` of the shipped agent's price in the calm stream (D-RD-ATT-1; the WP-7c model used the
    slowest single venue)."""
    from ybcal.sim.feeds import unchanged_share
    from ybcal.studies import g6_miners_fees as G6

    def run() -> float:
        st = G6.judge_stream(env, "calm-90d")
        if st.agent is None:
            return math.nan
        same = unchanged_share(st.agent)
        same = min(max(same if math.isfinite(same) else 0.0, 1e-9), 0.999)
        return -math.log(same)

    return _cached(_ekey(env, "agent_refresh"), run)


def bond_year_paths(env: Env) -> tuple[np.ndarray, str]:
    """One-year hourly YEC/USD paths from the reference price (G6's ``hour_prefix``: a demeaned block
    bootstrap of the real hourly returns, or the GARCH placeholder): the bond's USD value over its
    minimum lock (bondMinLock = one year)."""
    from ybcal.studies import g6_miners_fees as G6

    def build():
        n = max(16, min(256, env.budget.paths))
        return G6.hour_prefix(env, n, 365, "G8bond"), ("real-data" if _real_price(env) is not None
                                                          else "synthetic")

    return _cached(_ekey(env, "bondyear"), build)


def capture_at_capital(env: Env, cand, p_ref_usd: float) -> dict[str, float]:
    """``attestor-capture-capital`` (D-RD-ATT-9): for each ``capital_usd`` level, the adversary's best
    split of its YEC into ``j`` aged bonds of at least bondMin (``j`` ≤ max_split_seats) against
    ``nSlots − j`` honest seats at bondMin (equal age): the probability a bundle's aMint moves up 10 %
    (harm) and down 10 % (grief), exact kernels (``attest.capture_probability``)."""
    from ybcal.data import scenarios as SC
    from ybcal.sim.attest import capture_probability

    try:
        sc = SC.get("attestor-capture-capital")
    except KeyError:  # pragma: no cover
        return {}
    levels = [float(x) for x in sc.constants.get("capital_usd", [])]
    max_j = int(sc.constants.get("max_split_seats", 6))
    N, m, k, ql = (int(cand[x]) for x in ("nSlots", "mSelect", "kSlack", "qLowBps"))
    B = int(cand["bondMin"]) / COIN
    sig = (N, m, k, ql, int(cand["bondMin"]), round(p_ref_usd, 6), tuple(levels), max_j)

    def run() -> dict[str, float]:
        out: dict[str, float] = {}
        nd = 300 if env.budget.name == "quick" else 1500
        seed = stable_seed(GROUP, "capcap", env.seed)
        kw = dict(m_select=m, k_slack=k, q_low_bps=ql, q_high_bps=10_000 - ql, move_bps=1000, n_draws=nd,
                  seed=seed)
        for c in levels:
            yec = c / max(p_ref_usd, 1e-9)
            best = {"harm": 0.0, "grief": 0.0}
            for j in range(1, min(max_j, N) + 1):
                if yec / j < B:
                    break
                w = [round(yec / j * 1000)] * j + [round(B * 1000)] * (N - j)
                adv = [True] * j + [False] * (N - j)
                best["harm"] = max(best["harm"], capture_probability(w, adv, direction="a_mint_up", **kw))
                best["grief"] = max(best["grief"], capture_probability(w, adv, direction="a_mint_down", **kw))
            out[f"capcap.{int(c)}.harm"] = best["harm"]
            out[f"capcap.{int(c)}.grief"] = best["grief"]
            out[f"capcap.{int(c)}.yec"] = yec
        return out

    return _cached(_ekey(env, "capcap", sig), run)


#: P(a bundle's aMint moves) at which a seat set counts as captured (bond security test, D-RD-ATT-4).
CAPTURE_SEATS_PROB = 0.5


def seats_to_capture(env: Env, n_slots: int, m: int, k: int, q_low: int) -> dict[str, float]:
    """The fewest seats ``j`` an adversary must hold — each a bond one zat heavier than an honest
    minimum bond, aged alike — for P(aMint moves up 10 %) (harm) and P(down 10 %) (grief) to reach
    ``CAPTURE_SEATS_PROB`` (exact kernels, W9 selection). Splitting is the cheap attack: one huge seat is
    selected in only (m + k)/nSlots of bundles. ``inf`` when even every seat but one is not enough."""
    from ybcal.sim.attest import capture_probability

    def run() -> dict[str, float]:
        nd = 400 if env.budget.name == "quick" else 2000
        seed = stable_seed(GROUP, "seats", env.seed)
        kw = dict(m_select=m, k_slack=k, q_low_bps=q_low, q_high_bps=10_000 - q_low, move_bps=1000,
                  n_draws=nd, seed=seed)
        out = {"harm": math.inf, "grief": math.inf, "harm_p": 0.0, "grief_p": 0.0}
        for j in range(1, n_slots):
            w = [1_000_001] * j + [1_000_000] * (n_slots - j)
            adv = [True] * j + [False] * (n_slots - j)
            for d, key in (("a_mint_up", "harm"), ("a_mint_down", "grief")):
                if math.isfinite(out[key]):
                    continue
                pr = capture_probability(w, adv, direction=d, **kw)
                if pr >= CAPTURE_SEATS_PROB:
                    out[key], out[f"{key}_p"] = float(j), pr
        return out

    return _cached(_ekey(env, "seats", n_slots, m, k, q_low), run)


def cap_headroom_yec(cand) -> float:
    """YEC of collateral-value the MINT-6 cap admits after one year from startHeight: supplyCapBps ×
    the subsidy issued since startHeight (issuedZat, state.cpp:1224) — the YED a captured price could
    mint under the cap, in YEC at whatever price (a price-invariant yardstick for the bond)."""
    from ybcal.sim.supply import issued_between

    start = int(cand["startHeight"])
    issued = issued_between(start, start + BLOCKS_PER_YEAR - 1)
    return issued * int(cand["supplyCapBps"]) / 10_000 / COIN


def bond_prices(env: Env) -> tuple[np.ndarray, str]:
    """µUSD YEC price paths over a year (real: the given history; else YEC-like GBM from $0.40)."""

    def build():
        real = _real_price(env)
        if real is not None:
            return np.asarray(real.prices), "real-data"
        from ybcal.data.pricepath import DT_HOUR
        from ybcal.data.synthetic import GBM

        pp = GBM(sigma=JUDGEMENT["yec_vol_annual"]).simulate(64, 365 * 24, DT_HOUR,
                                                             env.rng_for(GROUP, "bond"))
        return np.asarray(pp.prices), "synthetic"

    return _cached(_ekey(env, "bondprice"), build)


def annual_vol(env: Env) -> tuple[float, str]:
    real = _real_price(env)
    if real is not None:
        try:
            from ybcal.data.describe import realised_vol

            v = realised_vol(real)
            v = float(np.nanmedian(np.atleast_1d(v)))
            if math.isfinite(v) and v > 0:
                return v, "real-data"
        except Exception:  # pragma: no cover
            pass
    return JUDGEMENT["yec_vol_annual"], "synthetic"


# ---------------------------------------------------------------------------------------------------
# The study


def _liveness(m: int, k: int, fresh: float, rho: float) -> float:
    from ybcal.sim.attest import liveness_probability

    if k < 0:
        return 0.0
    return liveness_probability(m, k, fresh, rho)


def unavail_with_down(m: int, k: int, n_slots: int, down: int, fresh: float, rho: float) -> float:
    """P(no bundle) when ``down`` of ``n_slots`` equally weighted seats are offline: the selection of
    ``min(n_slots, m + k)`` seats holds ``x`` down ones with the hypergeometric law; the rest must yield
    ≥ m fresh."""
    from scipy.stats import hypergeom

    sel = min(n_slots, m + k)
    tot = 0.0
    for x in range(0, min(down, sel) + 1):
        px = float(hypergeom.pmf(x, n_slots, down, sel))
        tot += px * (1.0 - _liveness(m, sel - x - m, fresh, rho))
    return tot


def _fams() -> tuple[Family, ...]:
    live = ("live.unavail", "live.unavail_outage1", "live.unavail_markov", "live.fresh")
    cap = ("cap.harm_prob", "cap.grief_prob", "cap.claim_grief_prob", "cap.bundle_share_when_selected")
    pin = ("pin.arm_rate", "pin.rate_200", "pin.rate_300", "pin.longest_quiet_h", "pin.bundle_window_prob",
           "pin.false_pin_per_day", "pin.detect_blocks")
    dorm = ("dorm.dead_detect_p95", "dorm.false_eject_year", "dorm.false_eject_year_markov",
            "dorm.expected_rows_in_window")
    return (
        Family("diverge", ("divergeBpsAttest",), ("divergeBpsAttest",),
               "divergeBpsAttest (D-RD-ATT-3): the smallest value, rounded up to 100 bps, at which MINT-10 "
               "refuses at most max_mint10_refusal_prob of honest mints in calm — the gap between the pools' "
               "pFast and the attestors' aMint, both built by the shipped agents (median of the venues) on "
               "the real venue disagreement; KEEP when the current value is within materiality of it. The "
               "proposal's spreads.py rule (diverge_spread_multiplier × the worst venue pair's p95) is "
               "reported as div.target_spreads.", kind="rule", target="div.target",
               report=("div.target", "div.target_spreads", "div.worst_p95", "div.refusal_calm",
                       "div.refusal_crash", "div.refusal_pump", "div.gap_p99.calm-90d",
                       "div.refusal_calm_single_venue_max", "div.refusal_calm_venues"),
               sens_metric="div.refusal_calm", provenance_key="spreads_provenance"),
        Family("pin_delta", ("pinDeltaBps",), ("pinDeltaBps",),
               "pinrate.py (proposal §16 measurement 2): the PIN-1 arming rate over rolling pinWindow "
               "windows of hourly history; > 20 % confirms, < pin_low_move_fraction (5 %) drops "
               "pinDeltaBps to 200–300 (the smaller whose rate clears 20 %), in between keeps it.",
               kind="rule", target="pin.target_delta", report=pin, sens_metric="pin.arm_rate",
               provenance_key="price_provenance"),
        Family("pin_window", ("pinWindow",), ("pinWindow",),
               "pinWindow: verify — the arming rate at the current pinDeltaBps ≥ pin_low_move_fraction "
               "and honest false pins ≤ max_false_pin_prob per day; else the nearest value that passes.",
               kind="verify", constraints=("pin_arming", "false_pin"), report=pin, sens_metric="pin.arm_rate",
               provenance_key="price_provenance"),
        Family("pin_tags", ("pinMinTags",), ("pinMinTags",),
               "pinMinTags: minimise the blocks to pin a frozen feed subject to honest false pins ≤ "
               "max_false_pin_prob per day; KEEP unless > materiality.",
               primary="pin.detect_blocks", constraints=("false_pin",), report=pin,
               sens_metric="pin.false_pin_per_day", provenance_key="feed_provenance"),
        Family("pin_bundles", ("pinMinBundles",), ("pinMinBundles",),
               "pinMinBundles: minimise the blocks to pin a frozen feed subject to honest false pins and "
               "pinMinBundles ≥ 2 (one row cannot show a move); KEEP unless > materiality.",
               primary="pin.detect_blocks", constraints=("false_pin", "pin_bundles_meaningful"), report=pin,
               sens_metric="pin.detect_blocks", provenance_key="feed_provenance"),
        Family("slack", ("kSlack",), ("kSlack",),
               "kSlack: the smallest bundle (4 + 74·(m + k) bytes) with P(minting refuses for want of a "
               "bundle) ≤ max_attest_unavailability at attestor_uptime and attestor_outage_correlation, "
               "also with one seated attestor down, and kSlack ≥ 1 (with no slack a dead selected "
               "attestor fails every bundle that picks it, so dormancy never sees it); KEEP unless > "
               "materiality.",
               primary="live.bundle_bytes", constraints=("unavail", "unavail_outage1", "dead_detectable"),
               report=(*live, "live.bundle_bytes"), sens_metric="live.unavail"),
        Family("select", ("mSelect",), ("mSelect",),
               "mSelect: verify — liveness (as kSlack) and P(an entity at max_single_entity_weight_share "
               "moves aMint up by 10 %) ≤ capture_prob_max; else the nearest value that passes.",
               kind="verify", constraints=("unavail", "unavail_outage1", "harm_capture"),
               report=(*live, *cap),
               sens_metric="cap.grief_prob"),
        Family("slots", ("nSlots",), ("nSlots",),
               "nSlots: verify — liveness with one seat down, harmful capture ≤ capture_prob_max and "
               "nSlots ≥ attestArmMin; else the nearest value that passes.",
               kind="verify", constraints=("unavail_outage1", "harm_capture", "slots_ge_arm"),
               report=(*live, *cap), sens_metric="cap.grief_prob"),
        Family("bundle_max", ("bundleMax",), ("bundleMax",),
               "bundleMax: verify — mSelect + kSlack ≤ bundleMax ≤ 6 (520-byte push); selection never "
               "yields more than m + k entries, so the value is otherwise inert.",
               kind="verify", constraints=("unavail",), report=("live.bundle_bytes",)),
        Family("qlow", ("qLowBps", "qHighBps"), ("qLowBps",),
               "qLowBps (qHighBps = 10⁴ − qLow), D-RD-ATT-11: verify — theft stays expensive: an entity "
               "holding max_single_entity_weight_share of the seated weight cannot move aMint up (≤ "
               "capture_prob_max), and an adversary splitting its YEC into seats of just over bondMin needs "
               "at least min_harm_capture_seats_share of the seats to move aMint up with probability ½; "
               "else the nearest qLow that passes. Griefing (the proposal §7.2 rule: the entity spans qLow "
               "of a bundle and moves aMint down, ≤ grief_prob_max) is reported: no qLow meets both, and "
               "griefing only over-collateralises new mints.",
               kind="verify", constraints=("harm_capture", "harm_seats"),
               report=(*cap, "cap.harm_seats", "cap.grief_seats"), sens_metric="cap.grief_prob"),
        Family("interval", ("attestInterval", "attestMaxAge"), ("attestInterval",),
               "attestInterval k (attestMaxAge = 2k): verify — relay load 1/k ≤ attest_relay_budget_per_"
               "block, honest calm MINT-10 refusals with attestations up to k blocks old ≤ "
               "max_mint10_refusal_prob (D-RD-ATT-5) and liveness; else the nearest k that passes. A k "
               "change moves the locked attestMaxAge (D-3).",
               kind="verify", constraints=("relay", "staleness", "unavail"),
               report=("k.refusal_calm", "k.stale_p99_bps", "k.relay", *live), sens_metric="k.refusal_calm",
               provenance_key="vol_provenance"),
        Family("arm_min", ("attestArmMin",), ("attestArmMin",),
               "attestArmMin: verify — an equal-weight founding set of that size gives no single "
               "founder qLow of the weight, and it yields bundles with P(none) ≤ max_attest_"
               "unavailability; else the nearest value that passes.",
               kind="verify", constraints=("arm_capture", "arm_liveness"),
               report=("arm.founder_share", "arm.unavail", "arm.blocks_to_arm"), sens_metric="arm.unavail"),
        Family("arm_delay", ("attestArmDelay",), ("attestArmDelay",),
               "attestArmDelay: verify — founders registering during the delay keep the founding age "
               "origin (attestArmDelay ≤ foundingWindow).", kind="verify",
               constraints=("arm_delay_in_founding",), report=("arm.blocks_to_arm",)),
        Family("required", ("attestRequired",), (),
               "attestRequired: design choice (W15) — verified, never tuned: false would let an ARMED "
               "chain accept a MINT without a bundle, removing the second population.", kind="verify",
               constraints=("required_true",)),
        Family("dorm_blocks", ("dormancyBlocks",), ("dormancyBlocks",),
               "dormancyBlocks: minimise the p95 blocks to eject a dead seated attestor subject to the "
               "honest false-ejection probability ≤ max_false_ejection_prob per year and detection ≤ "
               "max_dead_detection_blocks, at the policy adoption case's bundle rate; KEEP unless > "
               "materiality.", primary="dorm.dead_detect_p95", constraints=("false_eject", "dead_detect"),
               report=dorm),
        Family("dorm_min", ("dormancyMinBundles",), ("dormancyMinBundles",),
               "dormancyMinBundles: as dormancyBlocks.", primary="dorm.dead_detect_p95",
               constraints=("false_eject", "dead_detect"), report=dorm, sens_metric="dorm.false_eject_year"),
        Family("dorm_check", ("dormancyCheck",), ("dormancyCheck",),
               "dormancyCheck: verify — the false-ejection and dead-detection budgets hold.", kind="verify",
               constraints=("false_eject", "dead_detect"), report=dorm, sens_metric="dorm.dead_detect_p95"),
        Family("bond", ("bondMin",), ("bondMin",),
               "bondMin (D-RD-ATT-4): verify — (security) the capital for harmful capture by one adversary "
               "(seats of just over bondMin, as many as it takes for P(aMint up 10 %) ≥ ½) covers "
               "min_bond_cap_years of the MINT-6 cap's growth (supplyCapBps × subsidy since startHeight; "
               "both in YEC, so the test does not move with the YEC price), and (affordability) an honest "
               "bond's monthly opportunity cost at the reference price ≤ attestor_min_monthly_revenue_usd; "
               "else the nearest value that passes. The frontier (bond vs capture capital vs cost) is "
               "reported.",
               kind="verify", constraints=("bond_secure", "bond_affordable"),
               report=("bond.opportunity_usd_month", "bond.usd_at_start", "bond.usd_1y_p10",
                       "bond.usd_1y_p90",
                       "bond.harm_seats", "bond.harm_capital_usd", "bond.grief_capital_usd",
                       "bond.harm_to_cap_ratio", "bond.seated_total_yec", "bond.grief_capital_usd_p05",
                       "bond.seq_exhaustion_usd"),
               sens_metric="bond.harm_to_cap_ratio", provenance_key="price_provenance"),
        Family("bond_lock", ("bondMinLock",), (),
               "bondMinLock: fixed at BLOCKS_PER_YEAR (§1.4 invariant) — verified, not tuned.",
               kind="verify"),
        Family("maturity", ("bondMaturity",), ("bondMaturity",),
               "bondMaturity: verify — at least registration_notice_blocks of public notice before a "
               "bond counts toward arming and seating.", kind="verify", constraints=("maturity_notice",),
               report=("arm.blocks_to_arm", "age.newcomer_seat_days")),
        Family("age_cap", ("ageCap",), ("ageCap",),
               "ageCap: verify — a capital-matched newcomer needs ≥ min_capture_days to reach the "
               "harmful share and an honest newcomer with an equal bond is seated within "
               "max_newcomer_seat_days.", kind="verify", constraints=("capture_time", "newcomer_seat"),
               report=("age.harm_capture_days", "age.grief_capture_days", "age.newcomer_seat_days"),
               sens_metric="age.harm_capture_days"),
        Family("founding", ("foundingWindow",), ("foundingWindow",),
               "foundingWindow: verify — it covers the arming delay (attestArmDelay ≤ foundingWindow).",
               kind="verify", constraints=("arm_delay_in_founding",), report=("arm.blocks_to_arm",)),
        Family("persist", ("emergencyPersist",), ("emergencyPersist",),
               "emergencyPersist: minimise the crash-70-1d bad-debt fraction of a vault noticed at "
               "emergencyRatio subject to P(a 1-h wick yields an emergency claim) ≤ premature_claim_max "
               "and TTL − persist ≥ claim_reaction_blocks; KEEP unless > materiality.",
               primary="em.shortfall_mean", constraints=("premature_claim", "ttl_margin"),
               report=("em.shortfall_mean", "em.shortfall_p95", "em.premature_prob"),
               sens_metric="em.premature_prob", provenance="synthetic"),
        Family("ttl", ("emergencyNoticeTtl",), ("emergencyNoticeTtl",),
               "emergencyNoticeTtl: verify — TTL − persist ≥ claim_reaction_blocks; else the nearest "
               "value that passes.", kind="verify", constraints=("ttl_margin",),
               report=("em.ttl_margin_blocks",)),
    )


def _axis(base: ParamSet, name: str, values: Iterable[int]) -> list[ParamSet]:
    lo, hi = REGISTRY[name].bounds
    out = []
    for v in values:
        v = int(v)
        if lo <= v <= hi and v != base[name]:
            out.append(base.replace({name: v}))
    return out


@dataclass
class G8Study(FamilyStudy):
    """Price attestation (PLAN §5.8)."""

    group: str = GROUP
    params: tuple[str, ...] = field(default_factory=lambda: params_for_group(GROUP))

    def families(self) -> tuple[Family, ...]:
        return _fams()

    # -- space -----------------------------------------------------------------------------------
    def space(self, base: ParamSet, budget: Budget) -> Iterable[ParamSet]:
        fine = budget.name != "quick"
        out = [base]
        g = out.extend
        g(_axis(base, "divergeBpsAttest", range(300, 5001, 100)))
        g(_axis(base, "pinDeltaBps", range(100, 1501, 50 if fine else 100)))
        g(_axis(base, "pinDeltaBps", (200, 300)))
        g(_axis(base, "pinWindow", range(96, 1153, 48 if fine else 96)))
        g(_axis(base, "pinMinTags", range(1, 11)))
        g(_axis(base, "pinMinBundles", range(1, 11)))
        g(_axis(base, "kSlack", range(0, 5)))
        g(_axis(base, "mSelect", range(2, 7)))
        g(_axis(base, "nSlots", range(5, 22)))
        g(_axis(base, "bundleMax", range(2, 7)))
        g(_axis(base, "qLowBps", range(2600, 5001, 50 if fine else 100)))
        g(_axis(base, "attestInterval", (2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 30, 40, 60)))
        g(_axis(base, "attestArmMin", range(3, 16)))
        g(_axis(base, "attestArmDelay", range(288, 8065, 288 if fine else 576)))
        g(_axis(base, "dormancyBlocks", range(4032, 40321, 1152 if fine else 2304)))
        g(_axis(base, "dormancyMinBundles", (1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 40, 50, 75, 100)))
        g(_axis(base, "dormancyCheck", range(12, 289, 12 if fine else 36)))
        g(_axis(base, "bondMin", [y * COIN for y in range(5_000, 60_001, 1_000 if fine else 2_500)]))
        g(_axis(base, "bondMaturity", range(1152, 40321, 1152 if fine else 2304)))
        g(_axis(base, "ageCap", range(34560, 420481, 11520 if fine else 23040)))
        g(_axis(base, "foundingWindow", range(1152, 40321, 1152 if fine else 2304)))
        g(_axis(base, "emergencyPersist", range(12, 577, 12)))
        g(_axis(base, "emergencyNoticeTtl", range(288, 4609, 288)))
        return out

    # -- evaluate --------------------------------------------------------------------------------
    def evaluate(self, cand: ParamSet, env: Env) -> Metrics:
        from ybcal.sim.attest import (
            false_dormancy_per_year,
            false_dormancy_probability,
            fresh_probability,
            griefing_cost_usd,
        )

        pol = env.policy
        P = {k: cand[k] for k in self.params}
        m, k, N = (int(P[x]) for x in ("mSelect", "kSlack", "nSlots"))
        q_low = int(P["qLowBps"])
        k_int, max_age = int(P["attestInterval"]), int(P["attestMaxAge"])
        u, rho = float(pol.attestor_uptime), float(pol.attestor_outage_correlation)
        w = float(pol.max_single_entity_weight_share)
        L = judgement(pol, "mean_outage_blocks")
        bpb = bundles_per_block(env)
        v: dict[str, float] = {"zero": 0.0}
        c: dict[str, bool] = {}
        meta: dict[str, Any] = {"seed": env.seed, "budget": env.budget.name, "budget_paths": env.budget.paths,
                "budget_days": env.budget.block_horizon_days, "out_dir": env_out_dir(env)}

        # liveness
        fresh = fresh_probability(u, k_int, max_age)
        fresh_mk = fresh_probability(u, k_int, max_age, L)
        v["live.fresh"] = fresh
        v["live.unavail"] = 1 - _liveness(m, k, fresh, rho)
        v["live.unavail_outage1"] = unavail_with_down(m, k, N, 1, fresh, rho)
        v["live.unavail_markov"] = 1 - _liveness(m, k, fresh_mk, rho)
        v["live.bundle_bytes"] = float(4 + 74 * (m + k))
        mx = float(pol.max_attest_unavailability)
        c["unavail"] = v["live.unavail"] <= mx
        c["unavail_outage1"] = v["live.unavail_outage1"] <= mx
        # with kSlack = 0 a selected dead attestor makes every bundle that picks it fail, so no
        # BundleLog row ever lists it: dormancy can never eject it (confirmed by attest.simulate)
        c["dead_detectable"] = k >= 1

        # capture (exact kernels)
        cp = capture_probs(env, N, m, k, q_low, w)
        v["cap.grief_prob"], v["cap.harm_prob"] = cp["grief"], cp["harm"]
        v["cap.claim_grief_prob"] = cp["claim_grief"]
        v["cap.bundle_share_when_selected"] = cp["bundle_share_when_selected"]
        c["harm_capture"] = cp["harm"] <= judgement(pol, "capture_prob_max")
        c["grief_capture"] = cp["grief"] <= judgement(pol, "grief_prob_max")
        sts = seats_to_capture(env, N, m, k, q_low)
        v["cap.harm_seats"], v["cap.grief_seats"] = sts["harm"], sts["grief"]
        c["harm_seats"] = sts["harm"] >= float(getattr(pol, "min_harm_capture_seats_share", 0.75)) * N
        c["slots_ge_arm"] = int(P["attestArmMin"]) <= N

        # interval k / attestMaxAge
        vol, vprov = annual_vol(env)
        v["k.relay"] = 1.0 / k_int
        v["k.stale_p99_bps"] = 2.326 * vol * math.sqrt(max_age / BLOCKS_PER_YEAR) * 1e4
        c["relay"] = v["k.relay"] <= float(pol.attest_relay_budget_per_block)
        # D-RD-ATT-5: staleness is judged where it bites — MINT-10 refusals of honest calm mints with
        # attestations up to k blocks old (the aggregated-feed model below); the closed form
        # 2.326·σ·√(maxAge) at YEC's hourly σ (440 %/yr, inflated by the hourly points' noise) is reported
        v["k.refusal_calm"] = refusal_rate(mint10_gaps(env, cand, "calm-90d"), int(P["divergeBpsAttest"]))
        tol = float(getattr(pol, "max_mint10_refusal_prob", 0.01))
        c["staleness"] = math.isfinite(v["k.refusal_calm"]) and v["k.refusal_calm"] <= tol
        meta["vol_provenance"] = "real-data" if vprov == "real-data" else "judgement"

        # arming
        arm = int(P["attestArmMin"])
        v["arm.founder_share"] = 1.0 / arm
        sel = min(arm, m + k)
        v["arm.unavail"] = 1.0 if sel < m else 1 - _liveness(m, sel - m, fresh, rho)
        v["arm.blocks_to_arm"] = float(int(P["bondMaturity"]) + int(P["attestArmDelay"]))
        c["arm_capture"] = arm * q_low > 10_000
        c["arm_liveness"] = v["arm.unavail"] <= mx
        c["arm_delay_in_founding"] = int(P["attestArmDelay"]) <= int(P["foundingWindow"])
        c["required_true"] = bool(P["attestRequired"])

        # divergence (ported spreads.py)
        sp = spreads_inputs(env)
        div = int(P["divergeBpsAttest"])
        tgt = sp["target"]
        tgt = None if tgt is None else int(min(max(tgt, REGISTRY["divergeBpsAttest"].bounds[0]),
                                               REGISTRY["divergeBpsAttest"].bounds[1]))
        v["div.target"] = float(tgt) if tgt is not None else math.nan
        p95s = [s["p95"] for s in sp["res"]["stats"].values() if s["p95"] is not None]
        v["div.worst_p95"] = float(max(p95s)) if p95s else math.nan
        v["div.target_spreads"] = v["div.target"]
        v["div.refusal_calm_venues"] = float((sp["calm"] > div).mean()) if len(sp["calm"]) else math.nan
        v["div.refusal_crash_venues"] = float((sp["crash"] > div).mean()) if len(sp["crash"]) else math.nan
        # D-RD-ATT-3: MINT-10 compares two *aggregates* (the pools' pFast and the attestors' aMint, each a
        # median of agents that each take the median of the venues), not two venues
        gaps = {sc: mint10_gaps(env, cand, sc) for sc in MINT10_SCENARIOS}
        for sc, g in gaps.items():
            v[f"div.refusal.{sc}"] = refusal_rate(g, div)
            v[f"div.gap_p99.{sc}"] = float(np.percentile(g, 99)) if len(g) else math.nan
        v["div.refusal_calm"] = v["div.refusal.calm-90d"]
        v["div.refusal_crash"] = v["div.refusal.crash-70-1d"]
        v["div.refusal_pump"] = v["div.refusal.pump-dump-3x"]
        mixed = [refusal_rate(mint10_gaps(env, cand, "calm-90d", venue=j), div) for j in range(3)]
        fin = [x for x in mixed if math.isfinite(x)]
        v["div.refusal_calm_single_venue_max"] = float(max(fin)) if fin else math.nan
        tol = float(getattr(pol, "max_mint10_refusal_prob", 0.01))
        g = gaps["calm-90d"]
        lo_b, hi_b = REGISTRY["divergeBpsAttest"].bounds
        if len(g):
            need = float(np.quantile(g, 1.0 - tol))
            v["div.target"] = float(min(hi_b, max(lo_b, math.ceil(need / 100) * 100)))
        else:
            v["div.target"] = math.nan
        meta["spreads_provenance"] = sp["provenance"]

        # PIN (ported pinrate.py + false-pin / detection models)
        pw, pd = int(P["pinWindow"]), int(P["pinDeltaBps"])
        pr = pinrate_at(env, pw, pd)
        rates = pr.get("rates", {})
        _, hprov = pin_histories(env)
        meta["price_provenance"] = hprov
        meta["feed_provenance"] = "real-data" if sp["provenance"] == "real-data" else "synthetic"
        arm_rate = rates.get(pd, {}).get("rate", math.nan)
        v["pin.arm_rate"] = float(arm_rate)
        v["pin.rate_200"] = float(rates.get(200, {}).get("rate", math.nan))
        v["pin.rate_300"] = float(rates.get(300, {}).get("rate", math.nan))
        q = rates.get(pd, {}).get("longest_quiet_seconds")
        v["pin.longest_quiet_h"] = float(q / 3600) if q else math.nan
        v["pin.target_delta"] = float(ports.pin_delta_from_rates(pr, pd)) if rates else math.nan
        codes = {"confirm": 1.0, "inconclusive": 0.0, "drop": -1.0}
        v["pin.decision"] = codes.get(pr.get("decision"), math.nan)
        mt, mb = int(P["pinMinTags"]), int(P["pinMinBundles"])
        pbw = p_rows_at_least(max(2, mb), bpb * pw)
        v["pin.bundle_window_prob"] = pbw
        p_arm = (arm_rate if math.isfinite(arm_rate) else 0.0) * pbw
        share = _small_pool_share(env)
        rpb = agent_refresh_per_block(env)
        if not math.isfinite(rpb):
            rpb = sp["refresh_per_block"]
        v["pin.feed_refresh_per_block"] = rpb
        v["pin.false_pin_per_day"] = false_pin_per_day(env, pw, mt, p_arm, share, rpb)
        v["pin.false_pin_per_day_single_venue"] = false_pin_per_day(env, pw, mt, p_arm, share,
                                                                    sp["refresh_per_block"])
        v["pin.detect_blocks"] = pin_detect_blocks(pw, mt, max(2, mb), share, bpb)
        c["false_pin"] = v["pin.false_pin_per_day"] <= float(pol.max_false_pin_prob)
        c["pin_bundles_meaningful"] = mb >= 2
        c["pin_arming"] = math.isfinite(arm_rate) and arm_rate >= float(pol.pin_low_move_fraction)

        # dormancy
        D, mdb, chk = int(P["dormancyBlocks"]), int(P["dormancyMinBundles"]), int(P["dormancyCheck"])
        sel_p = min(1.0, (m + k) / N)
        # rows that select a dead attestor land only when the others still make the bundle
        r = -math.expm1(-bpb) * sel_p * _liveness(m, k - 1, fresh, rho)
        v["dorm.expected_rows_in_window"] = r * D
        v["dorm.dead_detect_p95"] = dead_detect_p95(env, D, mdb, chk, r)
        pchk = false_dormancy_probability(u, D, mdb, bpb, sel_p)
        v["dorm.false_eject_year"] = false_dormancy_per_year(pchk, chk)
        pmk = _cached(("fdmk", u, D, mdb, round(bpb, 12), sel_p, L),
                      lambda: false_dormancy_probability(u, D, mdb, bpb, sel_p, mean_outage_blocks=L))
        v["dorm.false_eject_year_markov"] = false_dormancy_per_year(pmk, chk)
        c["false_eject"] = v["dorm.false_eject_year"] <= float(pol.max_false_ejection_prob)
        c["dead_detect"] = v["dorm.dead_detect_p95"] <= int(pol.max_dead_detection_blocks)

        # bonds
        bmin = int(P["bondMin"])
        # D-RD-ATT-4: the bond is priced at the reference price (the last real price, as G6's fee test)
        # over one-year paths *from* it; the WP-7c code priced it at the first row of the loaded history
        # (2020-03, $0.075) and took the worst price of the whole 6.5-year history
        prices, bprov = bond_year_paths(env)
        sel_n = min(N, m + k)
        need = int(q_low / max(1, 10_000 - q_low) * (sel_n - 1) * bmin)
        g = griefing_cost_usd(need, prices)
        v["bond.grief_capital_usd_p05"] = float(g["min_capital_quantiles"]["q5"])
        p_ref = float(np.median(prices[:, 0])) / MICRO_USD_PER_USD
        v["bond.ref_price_usd"] = p_ref
        v["bond.usd_at_start"] = bmin / COIN * p_ref
        p_end = prices[:, -1].astype(float) / MICRO_USD_PER_USD
        v["bond.usd_1y_p10"] = bmin / COIN * float(np.quantile(p_end, 0.10))
        v["bond.usd_1y_p90"] = bmin / COIN * float(np.quantile(p_end, 0.90))
        apr = float(pol.bond_opportunity_cost_apr)
        v["bond.opportunity_usd_month"] = v["bond.usd_at_start"] * apr / 12
        v["bond.seq_exhaustion_usd"] = SEQ_SPACE * v["bond.usd_at_start"]
        # harmful capture (aMint up) needs > 1 − qLow of a bundle's weight. The cheap way is many seats
        # of just over bondMin (W9 selects m + k of nSlots), not one heavy seat: capital = seats × bondMin
        st = seats_to_capture(env, N, m, k, q_low)
        v["bond.harm_seats"] = st["harm"]
        v["bond.grief_seats"] = st["grief"]
        v["bond.harm_one_seat_yec"] = (10_000 - q_low) / max(1, q_low) * (sel_n - 1) * bmin / COIN
        harm_yec = st["harm"] * bmin / COIN
        v["bond.harm_capital_yec"] = harm_yec
        v["bond.harm_capital_usd"] = harm_yec * p_ref
        v["bond.grief_capital_yec"] = st["grief"] * bmin / COIN
        v["bond.grief_capital_usd"] = v["bond.grief_capital_yec"] * p_ref
        v["bond.seated_total_yec"] = N * bmin / COIN
        cap_yec = cap_headroom_yec(cand)
        v["bond.cap_first_year_yec"] = cap_yec
        v["bond.harm_to_cap_ratio"] = harm_yec / cap_yec if cap_yec > 0 else math.inf
        c["bond_secure"] = v["bond.harm_to_cap_ratio"] >= float(getattr(pol, "min_bond_cap_years", 2.0))
        v.update(capture_at_capital(env, cand, p_ref))
        c["bond_affordable"] = v["bond.opportunity_usd_month"] <= float(pol.attestor_min_monthly_revenue_usd)
        meta["price_provenance"] = hprov if hprov == "real-data" else bprov

        # age, maturity, founding
        cap = int(P["ageCap"])
        v["age.harm_capture_days"] = (10_000 - q_low) / q_low * cap / BLOCKS_PER_DAY
        v["age.grief_capture_days"] = q_low / (10_000 - q_low) * cap / BLOCKS_PER_DAY
        v["age.newcomer_seat_days"] = (cap + int(P["bondMaturity"])) / BLOCKS_PER_DAY
        c["capture_time"] = v["age.harm_capture_days"] >= judgement(pol, "min_capture_days")
        c["newcomer_seat"] = v["age.newcomer_seat_days"] <= judgement(pol, "max_newcomer_seat_days")
        c["maturity_notice"] = int(P["bondMaturity"]) >= judgement(pol, "registration_notice_blocks")

        # emergency
        persist, ttl = int(P["emergencyPersist"]), int(P["emergencyNoticeTtl"])
        em = emergency_metrics(env, persist, int(cand["emergencyRatioBps"]), max_age)
        v["em.premature_prob"] = em["premature"]
        v["em.shortfall_mean"] = em["shortfall_mean"]
        v["em.shortfall_p95"] = em["shortfall_p95"]
        v["em.ttl_margin_blocks"] = float(ttl - persist)
        c["premature_claim"] = em["premature"] <= judgement(pol, "premature_claim_max")
        c["ttl_margin"] = ttl - persist >= judgement(pol, "claim_reaction_blocks")

        meta.update({"uptime": u, "rho": rho, "entity_share": w, "bundles_per_block": bpb,
                     "spreads_target": tgt, "pin_decision": pr.get("decision"),
                     "spreads_rows": sp["res"]["rows"], "spreads_coverage_days": sp["res"]["coverage_days"]})
        prov = "synthetic" if "synthetic" in (sp["provenance"], hprov) else "judgement"
        return Metrics(v, "zero", True, c, prov, meta)

    # -- confirmation by simulation ----------------------------------------------------------------
    def confirm(self, table: ResultTable, chosen: ParamSet, meta: Mapping[str, Any]) -> dict[str, Any]:
        budget = budget_from_meta(meta)
        seed = int(meta.get("seed", 0))
        u = float(meta.get("uptime", 0.95))
        sets = {"current": table.base}
        keys = ("nSlots", "mSelect", "kSlack", "attestInterval", "dormancyBlocks", "dormancyMinBundles",
                "dormancyCheck")
        if any(chosen[k] != table.base[k] for k in keys):
            sets["recommended"] = chosen
        res = {name: confirm_attestation(ps, u, seed, budget) for name, ps in sets.items()}
        return {"slack": res, "select": res, "slots": res, "interval": res, "dorm_blocks": res,
                "dorm_min": res, "dorm_check": res}

    def figures(self, table: ResultTable, chosen: ParamSet, out: Path,
                confirm: Mapping[str, Any]) -> list[Path]:
        return g8_figures(table, chosen, out, self.families())

    def adjust_changes(self, changes: dict[str, Any], chosen_rows: Mapping[str, Any],
                       base: ParamSet) -> dict[str, Any]:
        """The dormancy families each fix a dead-detection violation on their own; apply only the one
        with the faster detection, and mark the others (incl. a BLOCKED dormancyCheck) resolved."""
        dorm = [p for p in ("dormancyBlocks", "dormancyMinBundles") if p in changes]
        if not dorm:
            return changes
        best = min(dorm, key=lambda p: chosen_rows[p][1].metrics.values["dorm.dead_detect_p95"])
        out = {k: v for k, v in changes.items() if k not in dorm or k == best}
        for p in ("dormancyBlocks", "dormancyMinBundles", "dormancyCheck"):
            if p != best and (chosen_rows[p][1].params[p] == base[p] or p in dorm):
                self._resolved[p] = (f"The dead-detection violation is resolved by the {best} change "
                                     f"({base[best]} → {changes[best]}); this parameter is kept.")
        return out

    def extra_notes(self, fam: Family, param: str, cur, best) -> list[str]:
        mv = cur.metrics.values
        md = cur.metrics.meta
        notes = []
        if fam.name == "qlow":
            notes.append(
                f"Design note: an entity with {md.get('entity_share', 0):.0%} of the seated weight holds "
                f"{mv['cap.bundle_share_when_selected']:.1%} of a bundle's weight when selected, so it spans "
                f"qLow in {mv['cap.grief_prob']:.0%} of bundles at the current value (proposal §7.2 states "
                "the "
                "rule for the *selected* attestors). Griefing only (aMint down → over-collateralisation); "
                f"theft (aMint up) needs > {1 - int(cur.params['qLowBps']) / 1e4:.0%}: "
                f"P = {mv['cap.harm_prob']:.3f}. A seat-splitting adversary (seats of just over bondMin) "
                f"moves aMint up with P ≥ ½ from {mv.get('cap.harm_seats', math.nan):.0f} of "
                f"{int(cur.params['nSlots'])} seats and down from {mv.get('cap.grief_seats', math.nan):.0f}; "
                "any qLow above one third lets 6 of 9 seats steal (D-RD-ATT-11), so griefing resistance "
                "is not bought with cheaper theft.")
        if fam.name == "diverge":
            notes.append(f"Ported spreads.py on {md.get('spreads_rows')} rows "
                         f"({md.get('spreads_coverage_days', 0):.1f} days, {md.get('spreads_provenance')}); "
                         "the proposal asks for two weeks of real logging (proposal §16) and biases larger.")
        if fam.name == "pin_delta":
            notes.append(f"pinrate.py decision at the current values: {md.get('pin_decision')} "
                         f"(arming rate {mv['pin.arm_rate']:.1%}; 200 bps {mv['pin.rate_200']:.1%}, "
                         f"300 bps {mv['pin.rate_300']:.1%}).")
        if fam.name in ("pin_tags", "pin_bundles", "pin_window"):
            per_day = md.get("bundles_per_block", 0) * BLOCKS_PER_DAY
            notes.append(f"At the policy adoption case only {per_day:.1f} bundles/day land: "
                         f"P(≥ 2 rows in one pinWindow) = {mv['pin.bundle_window_prob']:.1%}, "
                         "so PIN-1 rarely arms and frozen-feed detection is bundle-limited.")
        if fam.name in ("dorm_blocks", "dorm_min", "dorm_check"):
            rows = mv["dorm.expected_rows_in_window"]
            notes.append(f"A dead seated attestor is selected in {rows:.1f} rows "
                         "per "
                         "dormancy window on average at the policy adoption case "
                         f"(dormancyMinBundles = {cur.params['dormancyMinBundles']}).")
        if fam.name in ("slack", "select", "slots", "interval"):
            notes.append(f"Sensitivity to the outage model: with {JUDGEMENT['mean_outage_blocks']:.0f}-block "
                         f"(Markov) outages P(no bundle) = {mv['live.unavail_markov']:.4f} vs "
                         f"{mv['live.unavail']:.4f} for independent signing slots.")
        if fam.name == "bond":
            notes.append(f"Seq exhaustion (u16 AttestorSeq) would lock {SEQ_SPACE:,} bonds ≈ "
                         f"${mv['bond.seq_exhaustion_usd']:,.0f} — not a practical attack.")
        return notes


def confirm_attestation(ps: ParamSet, uptime: float, seed: int, budget: Budget) -> dict[str, Any]:
    """``attest.simulate`` at one set: bundle success rate (all attestors alive) vs the analytic
    liveness, and the dormancy time of a dead seated attestor vs the analytic Monte Carlo, both at a
    demand of one bundle per hour (so that the 30-day horizon sees enough rows)."""
    from ybcal.sim import attest as att

    n_slots, m, k = int(ps["nSlots"]), int(ps["mSelect"]), int(ps["kSlack"])
    paths = max(4, min(12, budget.paths // 6))
    n = int(budget.block_horizon_days) * BLOCKS_PER_DAY
    bond = int(ps["bondMin"])
    # positive heights (the simulator reads seatedSince ≤ 0 as "not seated", the node's 0 sentinel);
    # height0 = 50,400 is a multiple of every dormancyCheck on the search grid
    roster = [{"bond_zat": bond, "register_height": 1 + i} for i in range(n_slots)]
    rate = 1.0 / BLOCKS_PER_HOUR
    base = {"true_price": np.full((paths, n), 400_000, dtype=np.int64), "uptime": uptime,
            "demand_rate": rate, "height0": 50_400, "start_height": 1, "initial_trigger_height": 20_000,
            "initial_seated_since": 30_000}
    out: dict[str, Any] = {}
    s = att.simulate(ps, {"attest": {**base, "roster": roster, "seed": seed}})
    f = att.fresh_probability(uptime, int(ps["attestInterval"]), int(ps["attestMaxAge"]))
    # skip the first blocks: no attestation is cited before height0 (warm-up of the signing cadence)
    d = s.demands[s.demands["armed"] & (s.demands["height"] > 50_400 + 2 * int(ps["attestMaxAge"]) + 10)]
    out["bundle_success_sim"] = float(d["success"].mean()) if len(d) else math.nan
    out["bundle_success_analytic"] = att.liveness_probability(m, k, f)
    nd = len(d)
    pa = out["bundle_success_analytic"]
    se = math.sqrt(max(1e-12, pa * (1 - pa)) / max(1, nd))
    out["liveness_consistent"] = bool(abs(out["bundle_success_sim"] - pa) <= 4 * se + 0.003)
    dead = [dict(r) for r in roster]
    dead[0]["outages"] = [(-10**9, 10**9)]
    dead[0]["revive"] = False
    s2 = att.simulate(ps, {"attest": {**base, "roster": dead, "seed": seed + 1}})
    t = []
    for i in range(paths):
        seq = s2.seq_of_roster[i][0]
        tr = s2.transitions[(s2.transitions["path"] == i) & (s2.transitions["seq"] == seq)
                            & (s2.transitions["cause"] == att.CAUSE_DORMANT)]
        t.append(int(tr["height"][0]) - 50_400 if len(tr) else math.inf)
    t_arr = np.asarray(t, dtype=float)
    out["dead_ejected_fraction_sim"] = float(np.isfinite(t_arr).mean())
    out["dead_detect_median_sim"] = float(np.median(t_arr))
    sel_p = min(1.0, (m + k) / n_slots)
    # a row selecting the dead attestor lands only if the other m + k − 1 still yield ≥ m fresh
    r = -math.expm1(-rate) * sel_p * _liveness(m, k - 1, f, 0.0)
    D, mdb, chk = int(ps["dormancyBlocks"]), int(ps["dormancyMinBundles"]), int(ps["dormancyCheck"])
    # BundleLog rows before height0 are unknown to the simulator, and the attestor has been seated
    # since 30,000, so its first eligible check is the first one with enough rows since 0
    mc = dead_detect_times(np.random.default_rng([seed, 7]), 4000, r, D, mdb, chk, t_min=0,
                           horizon_windows=max(1, n // max(1, D)))
    fin = t_arr[np.isfinite(t_arr)]
    out["dead_detect_median_analytic"] = float(np.median(mc))
    out["dead_ejected_fraction_analytic"] = float(np.isfinite(mc).mean())
    q_lo, q_hi = np.quantile(mc[np.isfinite(mc)], [0.001, 0.999]) if np.isfinite(mc).any() else (0, 0)
    frac_ok = abs(out["dead_ejected_fraction_sim"] - out["dead_ejected_fraction_analytic"]) <= 0.35
    out["dead_consistent"] = bool(frac_ok and (len(fin) == 0 or ((fin >= q_lo - 2 * chk)
                                                                 & (fin <= q_hi + 2 * chk)).all()))
    return out


def g8_figures(table: ResultTable, chosen: ParamSet, out: Path, fams) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    blue, orange, ink, muted, surface = "#2a78d6", "#eb6834", "#0b0b0b", "#52514e", "#fcfcfb"
    paths: list[Path] = []
    fam = {f.name: f for f in fams}
    cur = table.current()
    # 1. ported measurements: pairwise p95 spreads and the divergeBpsAttest target; arming rate vs delta
    rows = sorted((r for r in family_rows(table, fam["pin_delta"]) if r.delta),
                  key=lambda r: int(r.params["pinDeltaBps"]))
    rows = sorted([cur, *rows], key=lambda r: int(r.params["pinDeltaBps"]))
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 3.6), facecolor=surface)
    for a in (a1, a2):
        a.set_facecolor(surface)
        a.grid(alpha=0.2)
    x = [int(r.params["pinDeltaBps"]) for r in rows]
    y = [100 * r.metrics.values["pin.arm_rate"] for r in rows]
    a1.plot(x, y, color=blue, lw=2, marker="o", ms=4)
    a1.axhline(20, color=muted, lw=1, ls="--")
    a1.axhline(5, color=muted, lw=1, ls=":")
    a1.axvline(int(table.base["pinDeltaBps"]), color=orange, lw=1, ls="--", label="current")
    a1.set_xlabel("pinDeltaBps", color=ink)
    a1.set_ylabel("windows armed (%)", color=ink)
    a1.set_title("PIN-1 arming rate (pinrate.py port)", color=ink, fontsize=10)
    a1.legend(frameon=False, fontsize=8)
    drows = sorted(family_rows(table, fam["diverge"]), key=lambda r: int(r.params["divergeBpsAttest"]))
    xd = [int(r.params["divergeBpsAttest"]) for r in drows]
    a2.plot(xd, [100 * r.metrics.values["div.refusal_calm"] for r in drows], color=blue, lw=2, label="calm")
    a2.plot(xd, [100 * r.metrics.values["div.refusal_crash"] for r in drows], color=orange, lw=2,
            label="crash-70-1d")
    a2.plot(xd, [100 * r.metrics.values["div.refusal_calm_venues"] for r in drows], color=muted, lw=1,
            ls="--", label="venue pairs (spreads.py)")
    tgt = cur.metrics.values.get("div.target")
    if tgt is not None and math.isfinite(tgt):
        a2.axvline(tgt, color=ink, lw=1, ls="--")
        a2.text(tgt, 1, " target", color=ink, fontsize=8)
    a2.axvline(int(table.base["divergeBpsAttest"]), color=muted, lw=1, ls=":")
    a2.set_xlabel("divergeBpsAttest", color=ink)
    a2.set_ylabel("honest mints refused (%)", color=ink)
    a2.set_title("MINT-10: pFast vs aMint (aggregated feeds)", color=ink, fontsize=10)
    a2.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    p = out / "g8_ported_measurements.png"
    fig.savefig(p, dpi=110)
    plt.close(fig)
    paths.append(p)
    # 2. liveness vs kSlack
    krows = sorted(family_rows(table, fam["slack"]), key=lambda r: int(r.params["kSlack"]))
    fig, ax = plt.subplots(figsize=(6.4, 3.6), facecolor=surface)
    ax.set_facecolor(surface)
    xk = [int(r.params["kSlack"]) for r in krows]
    ax.semilogy(xk, [max(1e-9, r.metrics.values["live.unavail"]) for r in krows], color=blue, lw=2,
                marker="o", label="independent slots")
    ax.semilogy(xk, [max(1e-9, r.metrics.values["live.unavail_markov"]) for r in krows], color=orange, lw=2,
                marker="o", label=f"{JUDGEMENT['mean_outage_blocks']:.0f}-block outages")
    ax.axhline(0.01, color=muted, lw=1, ls="--")
    ax.axvline(int(table.base["kSlack"]), color=muted, lw=1, ls=":")
    ax.set_xlabel("kSlack", color=ink)
    ax.set_ylabel("P(no bundle)", color=ink)
    ax.set_title("Bundle liveness (mSelect fixed)", color=ink, fontsize=10)
    ax.legend(frameon=False, fontsize=8)
    ax.grid(alpha=0.2)
    fig.tight_layout()
    p = out / "g8_liveness.png"
    fig.savefig(p, dpi=110)
    plt.close(fig)
    paths.append(p)
    return paths


def make_study() -> G8Study:
    """The G8 study (``ybcal.studies.base.load_study("G8")``)."""
    return G8Study()

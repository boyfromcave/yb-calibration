"""G6 — miner judgement and fees (PLAN §5.6).

Owner: WP-7d. Method, metric definitions and decision rules: ``docs/studies/g6.md``.

The study is a set of *families* (``ybcal.studies.g5_activation.Family``), one decision rule each, on
one candidate table of one-at-a-time sweeps around the current set (plus a 2-D grid for the two
fee-rate parameters that trade against each other):

=============  ===================================  =====================================================
family         params                               rule kind / primary metric
=============  ===================================  =====================================================
peer_lag       peerLag                              min honest p99 REG-4 deviation s.t. P(not evaluated),
                                                    false penalties
peer_min       peerMin                              **max** peerMin s.t. P(not evaluated) ≤ policy
deviation      deviationBps                         min deviationBps s.t. ≥ k_dev × honest p99, false
                                                    penalties, liar detection
accuracy       accuracyBandBps                      rule: honest p75 deviation (calm), rounded to 50 bps
payee          payeeWindow                          verify: P(FEE-0 in calm) ≤ max_fee0_prob
fee_min        feeMin                               verify: 4·feeMin floor fits a minMint vault at the
                                                    worst price; a minMint owner at the claim edge
                                                    still redeems
fees           feeBps, attestFeeBps (2-D grid)      min round-trip fee share of a minMint vault s.t.
                                                    pool / attestor revenue (adoption case) and fee share;
                                                    BLOCKED → least-violating point
n_penalty      nPenalty (excluded, L6)              min honest exclusion s.t. a liar is excluded
acc_window     accuracyWindow (excluded, L6)        verify: accuracy estimate s.d.
tilt           payeeTiltBps (excluded, L6)          verify: honest payee spread, accuracy premium
n_reg          nReg (excluded, informational)       verify: a small honest pool stays registered
=============  ===================================  =====================================================

**Judgement model.** Every block is mined by a pool of :class:`ybcal.data.synthetic.PoolModel`
(shares fitted to ``env.data["pool_shares"]``/``["hashrate"]`` when present); a tagging pool's quote is
its agent's 15-minute (12-block) TWAP of *its own exchange source*, ``lag_blocks`` old, times its agent
noise; the sources come from :class:`ybcal.data.synthetic.SpreadModel` (fitted to
``env.data["spreads"]`` when present) — correlated per-source deviations, staleness between refreshes
and outages. Pool ``i`` reads source ``i mod 3``. ``stale-pools`` switches pools holding ≈ 30 % of the
hash to a 1-hour-stale TWAP and one ≈ 10 % pool to a frozen quote from day 5. REG-4 deviations are the
vectorised rule of ``ybcal.sim.fees.judgement_series`` (``reg4_deviations`` returns the deviation
itself; a test pins it to the kernel).

**Fee model.** One hour-mode vault book (``ybcal.sim.vaults.simulate_vault_book_hours``, personas from
the policy incl. the ``[agents]`` keys) over a year from ``startHeight`` at the policy adoption case
gives every accepted vault's collateral and its mint/close prices. FEE-1 and AFEE-1 are then
recomputed exactly (``vkernels.fee_zat`` / ``attest_fee_zat``) for every fee candidate on the same
vaults (common random numbers; a fee does not change who mints). AFEE-1 is counted on every mint and
claim (an ARMED chain). The fee share of a ``minMint`` vault is ``ybcal.sim.fees.fee_table`` at the
reference price, ARMED, worst class.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from dataclasses import replace as dc_replace
from pathlib import Path
from typing import Any

import numpy as np

from ybcal.model import vkernels as V
from ybcal.params.paramset import ParamSet
from ybcal.params.registry import REGISTRY, params_for_group
from ybcal.studies import g1_price_windows as G1
from ybcal.studies.base import (
    Budget,
    Env,
    Metrics,
    Recommendation,
    ResultRow,
    ResultTable,
    decide_with_materiality,
)
from ybcal.studies.g5_activation import Family, FamilyStudy, env_out_dir, family_rows, family_table
from ybcal.units import BLOCKS_PER_HOUR, BPS, COIN, MICRO_USD_PER_USD

OWNER_WP = "WP-7d"

GROUP = "G6"

#: Judgement constants the policy does not (yet) carry; each has a requested policy key
#: (``POLICY_KEYS``, docs/decisions.md D-WP7d-6) and is read with ``getattr(policy, key, default)``.
JUDGEMENT: dict[str, float] = {
    "liar_bias_bps": 2000,  # the lie REG-4 must catch (a 20 % misquote, the HALT-3 scale)
    "min_liar_detection": 0.90,  # P(a tag of such a liar is penalised)
    "max_fee0_prob": 0.001,  # P(E(R) empty → FEE-0, an unpaid mint) in calm
    "min_registered_prob": 0.999,  # P(the smallest honest pool is registered at any R) (REG-1)
    "min_liar_exclusion": 0.99,  # share of the time a persistent liar is excluded from FEE-W
    "max_honest_exclusion": 0.01,  # share of the time an honest pool is excluded by false penalties
    "max_accuracy_sd_bps": 500,  # s.d. of an honest pool's REG-3 accuracy estimate
    "max_honest_payee_spread": 1.5,  # max/min FEE-W weight among honest pools (calm)
    "min_accuracy_premium": 0.25,  # weight of an honest p-median-accuracy pool over an accuracy-0 one, − 1
    "typical_sizes_from": 0,  # (documentation only) sizes follow the minter's distribution
}

POLICY_KEYS: dict[str, str] = {
    "liar_bias_bps": "liar_bias_bps",
    "min_liar_detection": "min_liar_detection",
    "max_fee0_prob": "max_fee0_prob",
    "min_registered_prob": "min_registered_prob",
    "min_liar_exclusion": "min_liar_exclusion",
    "max_honest_exclusion": "max_honest_exclusion",
    "max_accuracy_sd_bps": "max_accuracy_sd_bps",
    "max_honest_payee_spread": "max_honest_payee_spread",
    "min_accuracy_premium": "min_accuracy_premium",
}

#: Scenarios of the judgement metrics (``stale-pools`` adds honest-but-slow and frozen pools).
SCENARIOS: tuple[str, ...] = ("calm-90d", "crash-70-1d", "pump-dump-3x", "stale-pools")
#: Days of the fee-revenue book from startHeight.
REVENUE_DAYS = 365
#: G8's provisional bondMin recommendation (D-WP7c), reported as a sensitivity of the attestor test.
G8_BOND_MIN_YEC = 30_000
#: The crash used for the "fee at crash prices" report line.
CRASH_FRACTION = 0.70


def judgement(policy, key: str) -> float:
    """``policy.<POLICY_KEYS[key]>`` when the Policy has it, else ``JUDGEMENT[key]``."""
    pk = POLICY_KEYS.get(key)
    return float(getattr(policy, pk, JUDGEMENT[key]) if pk else JUDGEMENT[key])


# ===================================================================================================
# Shared helpers (also used by G7)

_CACHE: dict[tuple, Any] = {}
_CACHE_MAX = 64


def _cached(key: tuple, fn):
    if key not in _CACHE:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = fn()
    return _CACHE[key]


def clear_caches() -> None:
    """Drop the per-process memo (tests)."""
    _CACHE.clear()


def _data_ids(env: Env) -> tuple:
    keys = ("spreads", "price", "pool_shares", "hashrate", "depth")
    d = env.data if isinstance(env.data, Mapping) else {}
    return tuple(sorted((k, id(v)) for k, v in d.items() if k in keys))


def _ekey(env: Env, *parts) -> tuple:
    return (
        env.seed,
        env.budget.name,
        env.budget.paths,
        env.budget.block_horizon_days,
        _data_ids(env),
        env.policy.digest(),
        *parts,
    )


def reference_price(env: Env) -> int:
    """µUSD/YEC the fee shares and bond costs are quoted at: the last real price when one is loaded,
    else the scenario library's start price (``synthetic.DEFAULT_P0``, $0.40)."""
    from ybcal.data.synthetic import DEFAULT_P0

    real = G1.real_price(env)
    if real is not None:
        p = np.asarray(real.prices)[0]
        p = p[p > 0]
        if len(p):
            return int(p[-1])
    return int(DEFAULT_P0)


def book_paths(budget: Budget) -> int:
    """Hour-mode book paths: ``budget.paths // 4`` (quick 16, standard 100, deep 500), at least 4."""
    return max(4, int(budget.paths) // 4)


def agents_config(policy):
    """``AgentsConfig.from_policy`` plus the ``[agents]`` policy keys (D-WP4-3 resolution):
    ``yed_premium_bps`` → market premium, ``claimant_slippage_bps`` → claimant base slippage,
    ``defector_share`` / ``lost_key_prob`` → owner personas."""
    from ybcal.sim import agents as AG

    cfg = AG.AgentsConfig.from_policy(policy)
    owner = dc_replace(
        cfg.owner,
        lost_key_prob=float(getattr(policy, "lost_key_prob", 0.0)),
        defector_share=float(getattr(policy, "defector_share", 0.0)),
    )
    claimant = dc_replace(cfg.claimant, slippage_bps=int(getattr(policy, "claimant_slippage_bps", 100)))
    market = AG.YedMarket(premium_bps=int(getattr(policy, "yed_premium_bps", 0)))
    return dc_replace(cfg, owner=owner, claimant=claimant, market=market)


def hour_prefix(env: Env, n_paths: int, days: float, tag: str) -> np.ndarray:
    """Hourly true prices ``(n_paths, days·24 + 1)`` µUSD from the reference price: a demeaned block
    bootstrap of real hourly returns when ``env.data["price"]`` is real, else the YEC-like GARCH(1,1)-t
    placeholder (WP-2 preset) with its log drift removed (scenario convention, D-WP2-5)."""
    from ybcal.data import pricepath as PP
    from ybcal.data import synthetic as SY

    n = round(days * 24) + 1
    p0 = reference_price(env) if G1.real_price(env) is not None else SY.DEFAULT_P0
    rng = env.rng_for(tag, "prefix", n_paths, days)
    real = G1.real_price(env)
    if real is not None:
        try:
            hourly = real if real.resolution == "hour" else PP.resample(real, "hour")
            model = G1.real_model(env, hourly)
            model.demean = True
            return np.asarray(model.simulate(n_paths, n, "hour", rng, p0=p0).prices, dtype=np.int64)
        except Exception:  # pragma: no cover - a real file too short to bootstrap → placeholder
            pass
    model = SY.preset("garch")
    r = model.log_returns(n_paths, n, "hour", rng)[:, : n - 1] - model.expected_log_drift() * PP.DT_HOUR
    logp = math.log(p0) + np.concatenate([np.zeros((n_paths, 1)), np.cumsum(r, axis=1)], axis=1)
    return np.clip(np.rint(np.exp(logp)), 1, 100 * MICRO_USD_PER_USD).astype(np.int64)


def figure_style():
    """Matplotlib (Agg) and the shared palette (light surface, two series colours, muted ink)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt, {
        "blue": "#2a78d6",
        "orange": "#eb6834",
        "green": "#2f9e6e",
        "ink": "#0b0b0b",
        "muted": "#52514e",
        "surface": "#fcfcfb",
    }


def least_violating(rows: list[ResultRow], cons: tuple[str, ...], dist) -> ResultRow:
    """The row with the smallest sum of relative violations ``viol.<constraint>`` (ties → closest to
    the current set): the BLOCKED fallback the plan asks for."""

    def score(r: ResultRow) -> tuple[float, float]:
        v = r.metrics.values
        return (
            sum(float(v.get(f"viol.{c}", 0.0 if r.metrics.constraints.get(c, True) else 1.0)) for c in cons),
            dist(r),
        )

    return min(rows, key=score)


# ===================================================================================================
# Judgement streams


@dataclass
class JudgeStream:
    """One scenario's tag stream as REG-4 sees it (``(P, n)`` arrays)."""

    name: str
    tag_price: np.ndarray  #: int64 µUSD, 0 = no quote tag
    kind: np.ndarray  #: int8: -1 no quote, 0 normal honest pool, 1 stale (honest, slow), 2 frozen
    pool: np.ndarray  #: int16 miner pool id (n_pools = untagged "other")
    true: np.ndarray  #: int64 true price
    n_pools: int
    shares: tuple[float, ...]
    info: dict[str, Any] = field(default_factory=dict)


def spread_model(env: Env):
    """(SpreadModel, provenance): fitted to ``env.data["spreads"]`` (a SpreadsLog) when present."""
    from ybcal.data.synthetic import SpreadModel

    log = env.data.get("spreads") if isinstance(env.data, Mapping) else None
    if log is not None and hasattr(log, "prices"):
        try:
            return SpreadModel.fit(log), "real-data"
        except Exception:  # pragma: no cover - too few rows
            pass
    return SpreadModel(), "synthetic"


def pool_model(env: Env):
    """(PoolModel, provenance): fitted to a pool-share log (``pool_shares`` / ``hashrate``) if any."""
    from ybcal.data.synthetic import PoolModel

    for k in ("pool_shares", "hashrate"):
        log = env.data.get(k) if isinstance(env.data, Mapping) else None
        if log is not None and hasattr(log, "shares"):
            try:
                return PoolModel.fit(log), "real-data"
            except Exception:  # pragma: no cover
                pass
    return PoolModel(), "synthetic"


def _subset_closest(shares: Mapping[int, float], target: float) -> tuple[int, ...]:
    """Pools whose shares sum closest to ``target`` (ties → fewer pools)."""
    best: tuple[float, int, tuple[int, ...]] = (math.inf, 0, ())
    ids = sorted(shares)
    for r in range(1, len(ids) + 1):
        for c in itertools.combinations(ids, r):
            d = abs(sum(shares[i] for i in c) - target)
            if (d, r) < best[:2]:
                best = (d, r, c)
    return best[2]


def _twap_valid(src: np.ndarray, w: int) -> np.ndarray:
    """Rolling mean over the last ``w`` positive values (0 where none) along the last axis."""
    s = np.where(src > 0, src, 0).astype(np.float64)
    v = (src > 0).astype(np.int64)
    cs = np.concatenate([np.zeros((*s.shape[:-1], 1)), np.cumsum(s, axis=-1)], axis=-1)
    cv = np.concatenate([np.zeros((*v.shape[:-1], 1), dtype=np.int64), np.cumsum(v, axis=-1)], axis=-1)
    n = s.shape[-1]
    t = np.arange(n)
    lo = np.maximum(0, t - w + 1)
    tot = cs[..., t + 1] - cs[..., lo]
    cnt = cv[..., t + 1] - cv[..., lo]
    return np.where(cnt > 0, tot / np.maximum(cnt, 1), 0.0)


def judge_stream(env: Env, name: str) -> JudgeStream:
    """The scenario's REG-4 tag stream (memoised per process; see the module docstring)."""

    def build() -> JudgeStream:
        from ybcal.data.synthetic import renewal_outages

        scen = G1.scenario(env, name)
        h = G1.horizon_days(scen, env.budget)
        P = G1.paths_for(env.budget)
        rng = env.rng_for("G6", "true", name, P, h)
        real = G1.real_price(env)
        if real is not None:
            model = G1.real_model(env, real)
            model.demean = True
            base = model.simulate(
                P, scen.n_steps("block", h), "block", env.rng_for("G6", "boot", name, P, h), p0=scen.p0
            )
            run = scen.generate(rng, P, base_path=base, resolution="block", horizon_days=h)
        else:
            run = scen.generate(rng, P, resolution="block", horizon_days=h)
        true = np.asarray(run.paths.prices, dtype=np.int64)
        n = true.shape[1]
        sm, _ = spread_model(env)
        pm, _ = pool_model(env)
        k = pm.n_pools
        srng = env.rng_for("G6", "sources", name, P, h)
        sq = sm.generate(true, srng, step_seconds=75).quotes  # (P, ns, n)
        ns = sq.shape[1]
        prng = env.rng_for("G6", "pools", name, P, h)
        miner = pm.assign(P, n, prng)  # (P, n), k = other
        outage = renewal_outages(P, k, n, 75, pm.outage_per_day, pm.outage_mean_hours, prng)
        sched = {
            key: np.broadcast_to(np.asarray(v, dtype=float), (P, n)) if np.ndim(v) else None
            for key, v in run.schedules.items()
        }
        shares = {i: float(pm.shares[i]) for i in range(k)}
        stale_share = (
            float(np.max(sched["stale_pool_share"])) if sched.get("stale_pool_share") is not None else 0.0
        )
        frozen_share = (
            float(np.max(sched["frozen_pool_share"])) if sched.get("frozen_pool_share") is not None else 0.0
        )
        frozen_ids: tuple[int, ...] = ()
        if frozen_share > 0:
            frozen_ids = (min(shares, key=lambda i: abs(shares[i] - frozen_share)),)
        stale_ids: tuple[int, ...] = ()
        if stale_share > 0:
            stale_ids = _subset_closest({i: s for i, s in shares.items() if i not in frozen_ids}, stale_share)
        stale_on = (sched["stale_pool_share"] > 0) if stale_ids else np.zeros((P, n), bool)
        frozen_on = (sched["frozen_pool_share"] > 0) if frozen_ids else np.zeros((P, n), bool)
        stale_lag = (
            sched["stale_lag_blocks"].astype(np.int64)
            if sched.get("stale_lag_blocks") is not None
            else np.zeros((P, n), np.int64)
        )
        quote = np.zeros((P, n), dtype=np.int64)
        kind = np.full((P, n), -1, dtype=np.int8)
        t = np.arange(n)
        nrng = env.rng_for("G6", "noise", name, P, h)
        z = nrng.standard_normal((P, n))
        for i in range(k):
            mine = miner == i
            if not mine.any():
                continue
            tw = _twap_valid(sq[:, i % ns, :], int(pm.twap_blocks))  # (P, n)
            lag = np.full((P, n), int(pm.lag_blocks[i]), dtype=np.int64)
            st = stale_on & (i in stale_ids)
            lag = np.where(st, np.maximum(lag, stale_lag), lag)
            src_t = np.maximum(0, t[None, :] - lag)
            base = np.take_along_axis(tw, src_t, axis=1)
            q = base * np.exp(float(pm.bias_bps[i]) / 1e4 + float(pm.noise_bps[i]) / 1e4 * z)
            q = np.where(base > 0, q, 0.0)
            if i in frozen_ids and frozen_on.any():
                for p in range(P):
                    on = np.flatnonzero(frozen_on[p])
                    if len(on):
                        j0 = int(on[0])
                        prev = q[p, : j0 + 1][q[p, : j0 + 1] > 0]
                        q[p, on] = prev[-1] if len(prev) else 0.0
            qi = np.clip(np.rint(q), 0, 100 * MICRO_USD_PER_USD).astype(np.int64)
            tagged = mine & bool(pm.tagging[i]) & ~outage[:, i, :] & (qi > 0)
            quote = np.where(tagged, qi, quote)
            kd = np.where(st, 1, 0).astype(np.int8)
            if i in frozen_ids:
                kd = np.where(frozen_on, 2, kd).astype(np.int8)
            kind = np.where(tagged, kd, kind)
        info = {
            "scenario": name,
            "paths": P,
            "horizon_days": h,
            "stale_pools": list(stale_ids),
            "frozen_pools": list(frozen_ids),
            "quote_density": float((quote > 0).mean()),
        }
        return JudgeStream(name, quote, kind, miner, true, k, tuple(float(s) for s in pm.shares), info)

    return _cached(_ekey(env, "judge_stream", name), build)


def reg4_deviations(tag_price: np.ndarray, lag: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """REG-4 (state.cpp:910-936) for every quote tag, returning the **deviation** itself.

    Peers of the tag at column ``t`` = the quote tags at ``[t − lag, t + lag − 1]`` other than ``t``
    (not before column 0); ``m`` = their lower median; ``dev = |q − m|·10⁴ // m``. Returns
    ``(count, median, dev, judged)`` ``(P, n)``: peer count, median (0 when none), deviation (−1 where
    there is no quote tag or no peer), and whether the judgement height ``t + lag`` lies inside the
    series. ``ybcal.sim.fees.judgement_series`` is the same rule as booleans (tested equal)."""
    tp = np.atleast_2d(np.asarray(tag_price, dtype=np.int64))
    P, n = tp.shape
    big = np.iinfo(np.int64).max
    offs = [o for o in range(-lag, lag) if o != 0]
    cnt = np.zeros((P, n), dtype=np.int16)
    med = np.zeros((P, n), dtype=np.int64)
    dev = np.full((P, n), -1, dtype=np.int64)
    judged = (np.arange(n) + lag < n)[None, :].repeat(P, axis=0)
    if not offs:
        return cnt, med, dev, judged
    for p in range(P):
        q = tp[p] > 0
        vals = np.where(q, tp[p], big)
        ext = np.concatenate([np.full(lag, big), vals, np.full(lag, big)])
        peers = np.stack([ext[lag + o : lag + o + n] for o in offs], axis=-1)
        c = (peers != big).sum(axis=-1)
        srt = np.sort(peers, axis=-1)
        kk = np.maximum(c - 1, 0) // 2
        m = np.take_along_axis(srt, kk[:, None], axis=-1)[:, 0]
        ok = q & (c > 0)
        m_s = np.where(ok, m, 1)
        cnt[p] = c
        med[p] = np.where(ok, m, 0)
        dev[p] = np.where(ok, np.abs(tp[p] - m_s) * BPS // m_s, -1)
    return cnt, med, dev, judged


@dataclass
class LagArrays:
    """REG-4 inputs of one scenario at one ``peerLag`` (kept compact)."""

    cnt: np.ndarray
    med: np.ndarray
    dev: np.ndarray
    judged: np.ndarray


def lag_arrays(env: Env, name: str, lag: int) -> LagArrays:
    def build() -> LagArrays:
        s = judge_stream(env, name)
        c, m, d, j = reg4_deviations(s.tag_price, int(lag))
        return LagArrays(c, m, d.astype(np.int32), j)

    return _cached(_ekey(env, "lag", name, int(lag)), build)


@dataclass
class JudgeSummary:
    """Sorted deviation samples per pool kind and the not-evaluated share, at (lag, peerMin)."""

    honest: np.ndarray  #: sorted deviations of evaluated tags of normal honest pools
    stale: np.ndarray  #: … of stale (honest, slow) pools
    frozen: np.ndarray  #: … of a frozen pool
    not_eval: float  #: share of judged quote tags with fewer than peerMin peers
    n_tags: int


def floor_density(dens: float, params: Mapping, policy) -> float:
    """Quote-tag density at the participation floor: the calm stream's density (tagging at the
    expected enforcing share) scaled to ``participationFloor / signalWindow`` of the blocks."""
    share = float(policy.expected_enforcing_share)
    floor = int(params["participationFloor"]) / max(1, int(params["signalWindow"]))
    return float(dens) * min(1.0, floor / share) if share > 0 else float(dens)


def judge_summary(env: Env, name: str, lag: int, peer_min: int) -> JudgeSummary:
    def build() -> JudgeSummary:
        s = judge_stream(env, name)
        a = lag_arrays(env, name, lag)
        quote = (a.dev >= 0) & a.judged
        ev = quote & (a.cnt >= int(peer_min))
        nq = int(quote.sum())
        not_eval = float((quote & ~ev).sum() / nq) if nq else 1.0
        return JudgeSummary(
            np.sort(a.dev[ev & (s.kind == 0)]),
            np.sort(a.dev[ev & (s.kind == 1)]),
            np.sort(a.dev[ev & (s.kind == 2)]),
            not_eval,
            nq,
        )

    return _cached(_ekey(env, "summary", name, int(lag), int(peer_min)), build)


def _rate_above(sorted_dev: np.ndarray, thr: int) -> float:
    """Share of samples with ``dev > thr`` (REG-4 penalises strictly above deviationBps)."""
    if not len(sorted_dev):
        return math.nan
    return float(1.0 - np.searchsorted(sorted_dev, int(thr), side="right") / len(sorted_dev))


def _q(sorted_dev: np.ndarray, q: float) -> float:
    return float(np.percentile(sorted_dev, q)) if len(sorted_dev) else math.nan


def liar_detection(env: Env, lag: int, peer_min: int, dev_bps: int, bias_bps: int) -> float:
    """P(a liar's tag is penalised) in calm: an honest normal tag's quote moved by ±``bias_bps`` (half
    up, half down) against the same peers (the liar's own other tags among the peers are ignored —
    a small pool is a small minority of them)."""
    s = judge_stream(env, "calm-90d")
    a = lag_arrays(env, "calm-90d", lag)
    ev = (a.dev >= 0) & a.judged & (a.cnt >= int(peer_min)) & (s.kind == 0)
    q = s.tag_price[ev].astype(np.float64)
    m = a.med[ev].astype(np.float64)
    if not len(q):
        return math.nan
    up = np.floor(np.abs(q * (1 + bias_bps / BPS) - m) * BPS / m) > dev_bps
    dn = np.floor(np.abs(q * (1 - bias_bps / BPS) - m) * BPS / m) > dev_bps
    return float(0.5 * (up.mean() + dn.mean()))


def pool_accuracies(env: Env, lag: int, peer_min: int, band: int) -> dict[int, float]:
    """REG-3 accuracy (share of evaluated tags within accuracyBandBps) of each honest pool in calm."""
    s = judge_stream(env, "calm-90d")
    a = lag_arrays(env, "calm-90d", lag)
    ev = (a.dev >= 0) & a.judged & (a.cnt >= int(peer_min)) & (s.kind == 0)
    out = {}
    for i in range(s.n_pools):
        m = ev & (s.pool == i)
        if m.sum() >= 20:
            out[i] = float((a.dev[m] <= band).mean())
    return out


def fee0_rate(env: Env, window: int) -> float:
    """P(E(R) = ∅) over the calm stream (no quote tag in ``(R − payeeWindow, R]``; FEE-2 → FEE-0)."""
    s = judge_stream(env, "calm-90d")
    q = s.tag_price > 0
    c = np.cumsum(q, axis=1)
    w = int(window)
    if w >= q.shape[1]:
        return 0.0
    cnt = c[:, w:] - c[:, :-w]
    return float((cnt == 0).mean())


def quote_rate_smallest(env: Env) -> tuple[float, float]:
    """(share, quote-tag rate per block) of the smallest pool in the calm stream."""
    s = judge_stream(env, "calm-90d")
    i = int(np.argmin(s.shares))
    return float(s.shares[i]), float(((s.pool == i) & (s.tag_price > 0)).mean())


# ===================================================================================================
# Fee book


def revenue_book(env: Env, cand: ParamSet):
    """One hour-mode vault book over ``REVENUE_DAYS`` from startHeight at the policy adoption case
    (memoised; every G6 parameter is normalised to its registry value — the hour-mode book reads no
    REG-4/FEE-2 parameter (E(R) is always non-empty there) and fees are recomputed per candidate on the
    book's vaults)."""
    from ybcal.sim import vaults as VB

    norm = cand.replace({k: REGISTRY[k].mainnet for k in params_for_group(GROUP) if REGISTRY[k].tunable})
    P = book_paths(env.budget)

    def build():
        prices = hour_prefix(env, P, REVENUE_DAYS, "G6")
        return VB.simulate_vault_book_hours(
            norm,
            prices,
            agents_config(env.policy),
            rng=env.rng_for("G6", "book"),
            options=VB.HourOptions(book=VB.BookOptions(traj_every=24)),
            workers=1,
        )

    return _cached(_ekey(env, "revenue_book", norm.digest(), P), build)


def fee_revenue_for(res, fee_min: int, fee_bps: int, attest_fee_bps: int) -> dict[str, float]:
    """USD per path-day of FEE-1 (pool) and AFEE-1 (attestor) recomputed on the book's vaults."""
    from ybcal.sim import vaults as VB

    v = res.vaults
    acc = (v["outcome"] == VB.O_ACTIVE) & (v["pool_fee_mint"] > 0)
    close = (v["outcome"] == VB.O_ACTIVE) & (v["close_step"] >= 0) & (v["pool_fee_close"] > 0)
    coll = v["collateral_zat"].astype(np.int64)
    fm = V.fee_zat(coll[acc], int(fee_min), int(fee_bps))
    fc = V.fee_zat(coll[close], int(fee_min), int(fee_bps))
    usd = 1.0 / COIN / MICRO_USD_PER_USD
    tpm = v["tp_mint"][acc].astype(np.float64)
    tpc = v["tp_close"][close].astype(np.float64)
    pool = float((fm * tpm).sum() * usd + (fc * tpc).sum() * usd)
    claim = v["close_kind"][close] == VB.K_CLAIM
    am = V.attest_fee_zat(fm, int(attest_fee_bps)).astype(np.float64)
    ac = V.attest_fee_zat(fc, int(attest_fee_bps)).astype(np.float64) * claim
    att = float((am * tpm).sum() * usd + (ac * tpc).sum() * usd)
    pd = max(res.days * max(res.n_paths, 1), 1e-9)
    return {
        "pool_usd_day": pool / pd,
        "attest_usd_day": att / pd,
        "mints_per_day": float(acc.sum()) / pd,
        "closes_per_day": float(close.sum()) / pd,
    }


# ===================================================================================================
# Families


def _fams() -> tuple[Family, ...]:
    judge = (
        "judge.honest_p99",
        "judge.honest_p75_calm",
        "judge.false_penalty",
        "judge.false_penalty_stale",
        "judge.not_evaluated",
        "judge.not_evaluated_analytic",
        "judge.liar_detect_500",
        "judge.liar_detect_1000",
        "judge.liar_detect_2000",
        "judge.frozen_penalised",
    )
    fees = (
        "fee.share_minmint",
        "fee.share_minmint_unarmed",
        "fee.pool_usd_month",
        "fee.pool_required_usd",
        "fee.attestor_usd_month",
        "fee.attestor_required_usd",
        "fee.bond_cost_usd_month",
        "fee.bond_cost_usd_month_g8",
        "fee.pool_usd_month_mid",
        "fee.pool_usd_month_high",
    )
    return (
        Family(
            "peer_lag",
            ("peerLag",),
            ("peerLag",),
            "peerLag: minimise the honest p99 REG-4 deviation (worst scenario of calm-90d, crash-70-1d, "
            "pump-dump-3x, stale-pools) subject to P(not evaluated) ≤ max_not_evaluated_prob at the current "
            "peerMin (at the expected share and at the participation floor, D-RD-AUD-8) and honest false "
            "penalties ≤ max_false_penalty_rate; KEEP unless > materiality.",
            primary="judge.honest_p99",
            constraints=("not_evaluated", "not_evaluated_floor", "false_penalty"),
            report=judge,
            provenance_key="judge_provenance",
        ),
        Family(
            "peer_min",
            ("peerMin",),
            ("peerMin",),
            "peerMin: the largest value with P(fewer than peerMin peers → not evaluated) ≤ "
            "max_not_evaluated_prob at the expected pool count (PLAN §5.6) and also at the quote density "
            "of the participation floor, the lowest share at which minting still runs (analytic binomial, "
            "D-RD-AUD-8); KEEP unless the gain is > materiality.",
            primary="judge.peer_min",
            minimize=False,
            constraints=("not_evaluated", "not_evaluated_floor"),
            report=("judge.not_evaluated", "judge.not_evaluated_analytic", "judge.not_evaluated_floor"),
            sens_metric="judge.not_evaluated",
            provenance_key="judge_provenance",
        ),
        Family(
            "deviation",
            ("deviationBps",),
            ("deviationBps",),
            "deviationBps: as small as allowed by deviationBps ≥ k_dev × the honest p99 deviation, honest "
            "false penalties ≤ max_false_penalty_rate (stale pools included), and a ±liar_bias_bps liar "
            "penalised with probability ≥ min_liar_detection; KEEP unless > materiality.",
            primary="judge.dev_bps",
            constraints=("dev_margin", "false_penalty", "false_penalty_stale", "liar_detect"),
            report=judge,
            sens_metric="judge.false_penalty",
            provenance_key="judge_provenance",
        ),
        Family(
            "accuracy",
            ("accuracyBandBps",),
            ("accuracyBandBps",),
            "accuracyBandBps ≈ the honest p75 REG-4 deviation in calm-90d (PLAN §5.6), rounded to 50 bps; "
            "KEEP when the current value is within materiality of it.",
            kind="rule",
            target="judge.acc_target",
            report=("judge.acc_target", "judge.honest_p75_calm", "judge.in_band_calm"),
            sens_metric="judge.in_band_calm",
            provenance_key="judge_provenance",
        ),
        Family(
            "payee",
            ("payeeWindow",),
            ("payeeWindow",),
            "payeeWindow: verify — P(E(R) = ∅ → FEE-0, an unpaid mint) ≤ max_fee0_prob in calm with pool "
            "and source outages; else the nearest value that passes.",
            kind="verify",
            constraints=("fee0",),
            report=("payee.fee0", "payee.small_pool_in_payees", "payee.window_hours"),
            sens_metric="payee.fee0",
            provenance_key="pool_provenance",
        ),
        Family(
            "fee_min",
            ("feeMin",),
            ("feeMin",),
            "feeMin: verify — the MINT-5 floor 4·feeMin fits a minMint class-C vault at worst_price_usd "
            "(§1.4), and the owner of a minMint vault (any class, minted at the reference or the worst "
            "price) whose collateral has fallen to the claim threshold still gains by redeeming (FEE-1 ≤ "
            "1 − 10⁴/claimThresholdBps of the collateral); else the nearest value that passes.",
            kind="verify",
            constraints=("floor_mintable", "edge_redeem"),
            report=(
                "fee.edge_fee_share_max",
                "fee.edge_fee_share_limit",
                "fee.floor_binds_worst",
                "fee.minmint_fee_usd_worst",
                "fee.redeem_fee_usd_crash",
            ),
            sens_metric="fee.edge_fee_share_max",
            provenance="judgement",
        ),
        Family(
            "fees",
            ("feeBps", "attestFeeBps"),
            ("feeBps", "attestFeeBps"),
            "feeBps, attestFeeBps: the lowest fees — minimum round-trip fee share of a minMint vault "
            "(ARMED, worst class, reference price) — at which pool revenue ≥ "
            "max(pool_min_monthly_revenue_usd, "
            "pool_operating_cost_usd_month) per pool and attestor revenue ≥ max(attestor_min_monthly_"
            "revenue_usd, bondMin opportunity cost) per seated attestor under the policy adoption case, "
            "with the fee share ≤ max_fee_share_small; KEEP unless > materiality; if no grid point passes, "
            "BLOCKED at the least-violating point.",
            primary="fee.share_minmint",
            constraints=("fee_share", "pool_revenue", "attestor_revenue"),
            report=fees,
            sens_metric="fee.share_minmint",
            provenance_key="price_provenance",
        ),
        Family(
            "n_penalty",
            ("nPenalty",),
            ("nPenalty",),
            "nPenalty (wallet default, L6): minimise the share of time an honest pool is excluded from "
            "FEE-W by false penalties subject to a persistent liar (smallest pool share) being excluded "
            "≥ min_liar_exclusion of the time; KEEP unless > materiality.",
            primary="pen.honest_excluded",
            constraints=("liar_excluded", "honest_excluded"),
            report=("pen.honest_excluded", "pen.liar_excluded"),
            sens_metric="pen.liar_excluded",
            provenance_key="judge_provenance",
        ),
        Family(
            "acc_window",
            ("accuracyWindow",),
            ("accuracyWindow",),
            "accuracyWindow (wallet default, L6): verify — the s.d. of an equal-share honest pool's REG-3 "
            "accuracy estimate ≤ max_accuracy_sd_bps; else the nearest value that passes.",
            kind="verify",
            constraints=("accuracy_sd",),
            report=("accw.sd_bps", "accw.tags_in_window"),
            sens_metric="accw.sd_bps",
            provenance_key="judge_provenance",
        ),
        Family(
            "tilt",
            ("payeeTiltBps",),
            ("payeeTiltBps",),
            "payeeTiltBps (wallet default, L6, K8): verify — the FEE-W weight spread among honest pools "
            "≤ max_honest_payee_spread and an honest pool of median accuracy out-weighs an accuracy-0 pool "
            "by ≥ min_accuracy_premium; else the nearest value that passes.",
            kind="verify",
            constraints=("payee_spread", "accuracy_premium"),
            report=("tilt.honest_spread", "tilt.accuracy_premium"),
            sens_metric="tilt.honest_spread",
            provenance_key="judge_provenance",
        ),
        Family(
            "n_reg",
            ("nReg",),
            ("nReg",),
            "nReg (informational, REG-1 / yed_listminers): verify — the smallest honest pool is registered "
            "with probability ≥ min_registered_prob at any R; else the nearest value that passes.",
            kind="verify",
            constraints=("registered",),
            report=("reg.registered", "reg.window_hours"),
            sens_metric="reg.registered",
            provenance_key="pool_provenance",
        ),
    )


def _axis(base: ParamSet, name: str, values: Iterable[int]) -> list[ParamSet]:
    lo, hi = REGISTRY[name].bounds
    out = []
    for v in values:
        v = int(v)
        if lo <= v <= hi and v != base[name]:
            out.append(base.replace({name: v}))
    return out


def _p_bin_below(k: int, n: int, p: float) -> float:
    from scipy.stats import binom

    return float(binom.cdf(k - 1, n, p)) if k >= 1 else 0.0


@dataclass
class G6Study(FamilyStudy):
    """Miner judgement and fees (PLAN §5.6)."""

    group: str = GROUP
    params: tuple[str, ...] = field(default_factory=lambda: params_for_group(GROUP))

    def families(self) -> tuple[Family, ...]:
        return _fams()

    # -- space -----------------------------------------------------------------------------------
    def space(self, base: ParamSet, budget: Budget) -> Iterable[ParamSet]:
        fine = budget.name != "quick"
        out = [base]
        g = out.extend
        lags = (2, 3, 4, 5, 6, 8, 10, 12, 14, 16, 20, 24, 32, 40, 48) if fine else (4, 6, 8, 12, 16, 20, 24)
        g(_axis(base, "peerLag", lags))
        g(_axis(base, "peerMin", range(2, 13)))
        g(_axis(base, "deviationBps", range(300, 3001, 100)))
        g(_axis(base, "accuracyBandBps", range(100, 1001, 50)))
        g(_axis(base, "payeeWindow", (20, 30, 40, 50, 60, 80, 100, 120, 150, 200, 300, 400, 576)))
        g(_axis(base, "feeMin", [x * COIN // 10 for x in range(1, 21)]))
        for fb in range(5, 61, 5):
            for ab in range(0, 5001, 250 if fine else 500):
                if (fb, ab) != (base["feeBps"], base["attestFeeBps"]):
                    out.append(base.replace({"feeBps": fb, "attestFeeBps": ab}))
        g(_axis(base, "nPenalty", range(48, 1153, 48)))
        g(_axis(base, "accuracyWindow", range(96, 2017, 48 if fine else 96)))
        g(_axis(base, "payeeTiltBps", range(0, 20001, 1000 if fine else 2000)))
        g(_axis(base, "nReg", range(96, 2017, 48 if fine else 96)))
        return out

    # -- evaluate --------------------------------------------------------------------------------
    def evaluate(self, cand: ParamSet, env: Env) -> Metrics:
        pol = env.policy
        lag, pmin = int(cand["peerLag"]), int(cand["peerMin"])
        dev_bps, band = int(cand["deviationBps"]), int(cand["accuracyBandBps"])
        v: dict[str, float] = {"zero": 0.0}
        c: dict[str, bool] = {}
        _, sprov = spread_model(env)
        _, pprov = pool_model(env)
        price_prov = G1.provenance_of(env)
        judge_prov = "real-data" if sprov == "real-data" and price_prov == "real-data" else "synthetic"
        meta: dict[str, Any] = {
            "seed": env.seed,
            "budget": env.budget.name,
            "budget_paths": env.budget.paths,
            "budget_days": env.budget.block_horizon_days,
            "out_dir": env_out_dir(env),
            "judge_provenance": judge_prov,
            "price_provenance": price_prov,
            "pool_provenance": pprov if pprov == "real-data" else "synthetic",
            "spreads_provenance": sprov,
        }

        # ---- REG-4 judgement --------------------------------------------------------------------
        sums = {s: judge_summary(env, s, lag, pmin) for s in SCENARIOS}
        normal = [s for s in SCENARIOS]
        p99s = {s: _q(sums[s].honest, 99) for s in normal}
        v.update({f"judge.p99.{s}": p99s[s] for s in normal})
        v["judge.honest_p99"] = float(np.nanmax(list(p99s.values())))
        v["judge.honest_p75_calm"] = _q(sums["calm-90d"].honest, 75)
        v["judge.in_band_calm"] = 1.0 - _rate_above(sums["calm-90d"].honest, band)
        fps = {s: _rate_above(sums[s].honest, dev_bps) for s in normal}
        v.update({f"judge.fp.{s}": fps[s] for s in normal})
        v["judge.false_penalty"] = float(np.nanmax(list(fps.values())))
        v["judge.false_penalty_stale"] = _rate_above(sums["stale-pools"].stale, dev_bps)
        v["judge.frozen_penalised"] = _rate_above(sums["stale-pools"].frozen, dev_bps)
        v["judge.not_evaluated"] = sums["calm-90d"].not_eval
        dens = judge_stream(env, "calm-90d").info["quote_density"]
        v["judge.quote_density"] = dens
        v["judge.not_evaluated_analytic"] = _p_bin_below(pmin, 2 * lag - 1, dens)
        # D-RD-AUD-8: judgement must keep working at the lowest share at which minting still runs
        # (participationFloor of the signal window), not only at the expected share
        dens_floor = floor_density(dens, cand, pol)
        v["judge.quote_density_floor"] = dens_floor
        v["judge.not_evaluated_floor"] = _p_bin_below(pmin, 2 * lag - 1, dens_floor)
        bias = int(judgement(pol, "liar_bias_bps"))
        for b in (500, 1000, 2000):
            v[f"judge.liar_detect_{b}"] = liar_detection(env, lag, pmin, dev_bps, b)
        v["judge.liar_detect"] = (
            v[f"judge.liar_detect_{bias}"]
            if bias in (500, 1000, 2000)
            else liar_detection(env, lag, pmin, dev_bps, bias)
        )
        v["judge.peer_min"] = float(pmin)
        v["judge.dev_bps"] = float(dev_bps)
        kdev = float(pol.k_dev)
        dev_need = kdev * v["judge.honest_p99"]
        v["judge.dev_required"] = dev_need
        lo, hi = REGISTRY["deviationBps"].bounds
        # No evaluated honest quote at all (e.g. peerMin above the peers a window holds) leaves the p99
        # undefined; the target is then undefined too, as for acc_target (the not_evaluated
        # constraint already fails such a candidate).
        v["judge.dev_target"] = (
            float(min(hi, max(lo, math.ceil(dev_need / 100) * 100))) if math.isfinite(dev_need) else math.nan
        )
        alo, ahi = REGISTRY["accuracyBandBps"].bounds
        p75 = v["judge.honest_p75_calm"]
        v["judge.acc_target"] = (
            float(min(ahi, max(alo, round(p75 / 50) * 50))) if math.isfinite(p75) else math.nan
        )
        mx_fp = float(pol.max_false_penalty_rate)
        c["not_evaluated"] = v["judge.not_evaluated"] <= float(pol.max_not_evaluated_prob)
        c["not_evaluated_floor"] = v["judge.not_evaluated_floor"] <= float(pol.max_not_evaluated_prob)
        c["false_penalty"] = v["judge.false_penalty"] <= mx_fp
        stale_fp = v["judge.false_penalty_stale"]
        c["false_penalty_stale"] = (not math.isfinite(stale_fp)) or stale_fp <= mx_fp
        c["dev_margin"] = dev_bps >= dev_need
        c["liar_detect"] = v["judge.liar_detect"] >= judgement(pol, "min_liar_detection")
        meta["quote_density"] = dens
        meta["max_not_evaluated_prob"] = float(pol.max_not_evaluated_prob)
        meta["quote_density_floor"] = dens_floor
        meta["stale_pools"] = judge_stream(env, "stale-pools").info["stale_pools"]
        meta["frozen_pools"] = judge_stream(env, "stale-pools").info["frozen_pools"]

        # ---- payee window, registration ---------------------------------------------------------
        W = int(cand["payeeWindow"])
        v["payee.fee0"] = fee0_rate(env, W)
        s_min, rate_min = quote_rate_smallest(env)
        v["payee.small_pool_in_payees"] = 1.0 - (1.0 - rate_min) ** W
        v["payee.window_hours"] = W / BLOCKS_PER_HOUR
        c["fee0"] = v["payee.fee0"] <= judgement(pol, "max_fee0_prob")
        nreg = int(cand["nReg"])
        v["reg.registered"] = 1.0 - (1.0 - rate_min) ** nreg
        v["reg.window_hours"] = nreg / BLOCKS_PER_HOUR
        c["registered"] = v["reg.registered"] >= judgement(pol, "min_registered_prob")
        meta["smallest_pool_share"] = s_min
        meta["smallest_pool_quote_rate"] = rate_min

        # ---- L6: nPenalty, accuracyWindow, payeeTiltBps -----------------------------------------
        npen = int(cand["nPenalty"])
        fp_rate = v["judge.false_penalty"] if math.isfinite(v["judge.false_penalty"]) else 0.0
        s_eq = float(pol.expected_enforcing_share) / max(1, int(pol.expected_pool_count))
        p_pen_tag = s_eq * dens / max(1e-9, float(np.sum(judge_stream(env, "calm-90d").shares))) * fp_rate
        v["pen.honest_excluded"] = round(1.0 - (1.0 - p_pen_tag) ** npen, 6)
        v["pen.liar_excluded"] = 1.0 - (1.0 - rate_min) ** npen
        c["liar_excluded"] = v["pen.liar_excluded"] >= judgement(pol, "min_liar_exclusion")
        c["honest_excluded"] = v["pen.honest_excluded"] <= judgement(pol, "max_honest_exclusion")
        accw = int(cand["accuracyWindow"])
        rate_eq = s_eq * dens / max(1e-9, float(np.sum(judge_stream(env, "calm-90d").shares)))
        n_tags = rate_eq * accw * (1.0 - v["judge.not_evaluated"])
        pin = (
            min(max(v["judge.in_band_calm"], 1e-6), 1 - 1e-6)
            if math.isfinite(v["judge.in_band_calm"])
            else 0.5
        )
        v["accw.tags_in_window"] = n_tags
        v["accw.sd_bps"] = BPS * math.sqrt(pin * (1 - pin) / max(n_tags, 1e-9))
        c["accuracy_sd"] = v["accw.sd_bps"] <= judgement(pol, "max_accuracy_sd_bps")
        tilt = int(cand["payeeTiltBps"])
        acc = pool_accuracies(env, lag, pmin, band)
        if acc:
            w = [BPS + tilt * int(BPS * a) // BPS for a in acc.values()]
            v["tilt.honest_spread"] = max(w) / min(w)
            med = float(np.median(list(acc.values())))
            v["tilt.accuracy_premium"] = (BPS + tilt * int(BPS * med) // BPS) / BPS - 1.0
        else:
            v["tilt.honest_spread"], v["tilt.accuracy_premium"] = 1.0, 0.0
        c["payee_spread"] = v["tilt.honest_spread"] <= judgement(pol, "max_honest_payee_spread")
        c["accuracy_premium"] = v["tilt.accuracy_premium"] >= judgement(pol, "min_accuracy_premium")
        meta["pool_accuracy_calm"] = {str(k): round(a, 4) for k, a in acc.items()}

        # ---- fees ---------------------------------------------------------------------------------
        self._fee_metrics(cand, env, v, c, meta)
        prov = "real-data" if judge_prov == "real-data" and price_prov == "real-data" else "synthetic"
        return Metrics(v, "zero", True, c, prov, meta)

    def _fee_metrics(self, cand: ParamSet, env: Env, v: dict, c: dict, meta: dict) -> None:
        from ybcal.model import kernels as K
        from ybcal.sim import fees as F

        pol = env.policy
        fee_min, fee_bps, afee = int(cand["feeMin"]), int(cand["feeBps"]), int(cand["attestFeeBps"])
        p_ref = reference_price(env)
        min_mint = int(cand["minMint"])
        shares, shares_un = [], []
        for cls in range(3):
            shares.append(
                F.fee_table(cand, [min_mint], p_ref, term_class=cls, armed=True)[0]["fee_share_of_debt"]
            )
            shares_un.append(F.fee_table(cand, [min_mint], p_ref, term_class=cls)[0]["fee_share_of_debt"])
        v["fee.share_minmint"] = float(max(shares))
        v["fee.share_minmint_unarmed"] = float(max(shares_un))
        for cls, s in zip("ABC", shares, strict=True):
            v[f"fee.share_minmint_{cls}"] = float(s)
        mx = float(pol.max_fee_share_small)
        c["fee_share"] = v["fee.share_minmint"] <= mx
        v["viol.fee_share"] = max(0.0, v["fee.share_minmint"] / mx - 1.0)

        # revenue on the book (CRN across fee candidates)
        res = revenue_book(env, cand)
        r = fee_revenue_for(res, fee_min, fee_bps, afee)
        n_pools = max(1, int(pol.expected_pool_count))
        n_seated = max(1, int(cand["nSlots"]))
        v["fee.pool_usd_month"] = r["pool_usd_day"] * 30 / n_pools
        v["fee.attestor_usd_month"] = r["attest_usd_day"] * 30 / n_seated
        v["fee.book_mints_per_day"] = r["mints_per_day"]
        v["fee.book_closes_per_day"] = r["closes_per_day"]
        scen = pol.adoption_scenarios
        base_mpd = float(scen.get(pol.adoption_case, {}).get("mints_per_day", 2.0)) or 1.0
        for case in ("low", "mid", "high"):
            f = float(scen.get(case, {}).get("mints_per_day", base_mpd)) / base_mpd
            v[f"fee.pool_usd_month_{case}"] = v["fee.pool_usd_month"] * f
        bond_usd = int(cand["bondMin"]) / COIN * p_ref / MICRO_USD_PER_USD
        apr = float(pol.bond_opportunity_cost_apr)
        v["fee.bond_cost_usd_month"] = bond_usd * apr / 12
        v["fee.bond_cost_usd_month_g8"] = G8_BOND_MIN_YEC * p_ref / MICRO_USD_PER_USD * apr / 12
        v["fee.pool_required_usd"] = max(
            float(pol.pool_min_monthly_revenue_usd), float(pol.pool_operating_cost_usd_month)
        )
        v["fee.attestor_required_usd"] = max(
            float(pol.attestor_min_monthly_revenue_usd), v["fee.bond_cost_usd_month"]
        )
        c["pool_revenue"] = v["fee.pool_usd_month"] >= v["fee.pool_required_usd"]
        c["attestor_revenue"] = v["fee.attestor_usd_month"] >= v["fee.attestor_required_usd"]
        v["viol.pool_revenue"] = max(0.0, 1.0 - v["fee.pool_usd_month"] / v["fee.pool_required_usd"])
        v["viol.attestor_revenue"] = max(
            0.0, 1.0 - v["fee.attestor_usd_month"] / v["fee.attestor_required_usd"]
        )

        # feeMin: the MINT-5 floor and redemption affordability at the claim edge
        worst = round(float(pol.worst_price_usd) * MICRO_USD_PER_USD)
        thr = int(cand["claimThresholdBps"])
        limit = 1.0 - BPS / thr
        edge, floor_binds_worst, usd_worst = 0.0, 0.0, 0.0
        for p in (p_ref, worst):
            for cls in range(3):
                a = F.redemption_affordability(cand, min_mint, cls, p, p)
                edge = max(edge, a.fee_zat / a.collateral_zat)
                if p == worst:
                    floor_binds_worst = max(floor_binds_worst, float(a.floor_binds))
                    usd_worst = max(usd_worst, a.fee_usd_at_crash)
        v["fee.edge_fee_share_max"] = edge
        v["fee.edge_fee_share_limit"] = limit
        v["fee.floor_binds_worst"] = floor_binds_worst
        v["fee.minmint_fee_usd_worst"] = usd_worst
        crash = int(p_ref * (1 - CRASH_FRACTION))
        v["fee.redeem_fee_usd_crash"] = F.redemption_affordability(
            cand, min_mint, 0, p_ref, crash
        ).fee_usd_at_crash
        mr_c = K.min_ratio_bps(int(cand["baseRatioBps[2]"]), BPS)
        req_c = K.required_zat_rounded(min_mint, mr_c, worst) or 0
        c["floor_mintable"] = 4 * fee_min <= req_c
        c["edge_redeem"] = edge <= limit
        meta["reference_price_microusd"] = p_ref
        meta["max_fee_share_small"] = mx

    # -- decide ----------------------------------------------------------------------------------
    def decide_family(self, table: ResultTable, fam: Family, policy) -> tuple[ResultRow, str, str]:
        if fam.name != "fees":
            return super().decide_family(table, fam, policy)
        sub = family_table(table, fam)
        d = decide_with_materiality(sub, policy)
        if d.verdict != "BLOCKED":
            return d.row, d.verdict, d.reason
        row = least_violating(list(sub), fam.constraints, sub.distance)
        vv = row.metrics.values
        return (
            row,
            "BLOCKED",
            (
                "no (feeBps, attestFeeBps) on the grid clears the fee-share cap and both revenue floors; the "
                f"least-violating point (fee share {vv['fee.share_minmint']:.2%}, pool "
                f"${vv['fee.pool_usd_month']:.0f}/mo, "
                f"attestor ${vv['fee.attestor_usd_month']:.0f}/mo) is shown"
            ),
        )

    def adjust_changes(
        self, changes: dict[str, Any], chosen_rows: Mapping[str, Any], base: ParamSet
    ) -> dict[str, Any]:
        """A new peerLag changes how many peers a tag has: re-check the chosen peerMin at the new lag
        with the analytic binomial (quote density from the calm stream) and lower it if needed."""
        if "peerLag" not in changes:
            return changes
        row = chosen_rows["peerMin"][1]
        dens = float(row.metrics.meta.get("quote_density_floor", row.metrics.meta.get("quote_density", 0.0)))
        cap = float(row.metrics.meta.get("max_not_evaluated_prob", 0.05))
        lag = int(changes["peerLag"])
        pmin = int(changes.get("peerMin", base["peerMin"]))
        new = pmin
        while new > REGISTRY["peerMin"].bounds[0] and _p_bin_below(new, 2 * lag - 1, dens) > cap:
            new -= 1
        if new != pmin:
            changes = {**changes, "peerMin": new}
            self._resolved["peerMin"] = (
                f"peerMin lowered to {new} so that a tag still has enough peers at the "
                f"recommended peerLag {lag} (analytic binomial at quote density "
                f"{dens:.2f})."
            )
        return changes

    def decide(self, results: ResultTable, policy) -> list[Recommendation]:
        recs = super().decide(results, policy)
        notes = design_notes(results, policy)
        for r in recs:
            r.metrics["design_notes"] = list(notes)
        return recs

    def extra_notes(self, fam: Family, param: str, cur: ResultRow, best: ResultRow) -> list[str]:
        mv, md = cur.metrics.values, cur.metrics.meta
        out = []
        if fam.name in ("peer_lag", "deviation", "accuracy"):
            out.append(
                "Honest p99 deviation by scenario (bps): "
                + ", ".join(f"{s} {mv.get(f'judge.p99.{s}', math.nan):.0f}" for s in SCENARIOS)
                + "; stale pools "
                f"{md.get('stale_pools')} quote 1 h late and pool {md.get('frozen_pools')} freezes in "
                "stale-pools "
                f"(the frozen pool's tags are penalised at {mv.get('judge.frozen_penalised', math.nan):.1%})."
            )
        if fam.name == "fees":
            out.append(
                "FEE-1 is levied on the collateral, so the round-trip fee share of the debt is "
                "2·feeBps·baseRatio regardless of size (A "
                f"{mv['fee.share_minmint_A']:.2%}, B {mv['fee.share_minmint_B']:.2%}, C "
                f"{mv['fee.share_minmint_C']:.2%} "
                "ARMED at the current values); class A carries the binding share."
            )
            out.append(
                f"Attestor test: bondMin opportunity cost ${mv['fee.bond_cost_usd_month']:.0f}/month at the "
                f"reference price (G8's provisional {G8_BOND_MIN_YEC:,} YEC: "
                f"${mv['fee.bond_cost_usd_month_g8']:.0f}); "
                f"pool revenue scales with adoption: mid ${mv['fee.pool_usd_month_mid']:.0f}, high "
                f"${mv['fee.pool_usd_month_high']:.0f} per pool per month."
            )
        if fam.name == "fee_min":
            out.append(
                f"At worst_price_usd a minMint vault pays ${mv['fee.minmint_fee_usd_worst']:.2f} per leg "
                "(the feeMin floor binds); at the reference price the floor never binds."
            )
        if fam.name == "payee":
            out.append(
                f"The smallest pool (share {md.get('smallest_pool_share', 0):.0%}) is in E(R) "
                f"{mv['payee.small_pool_in_payees']:.1%} of the time; FEE-W picks a payee in proportion to "
                "its tags in the window, so its expected revenue share does not depend on the window."
            )
        return out

    # -- evidence ----------------------------------------------------------------------------------
    def figures(
        self, table: ResultTable, chosen: ParamSet, out: Path, confirm: Mapping[str, Any]
    ) -> list[Path]:
        return g6_figures(table, chosen, out, {f.name: f for f in self.families()})

    def explain(self, rec: Recommendation, results: ResultTable) -> str:
        return explain_g6(rec, results, {f.name: f for f in self.families()})


# ===================================================================================================
# Design notes, explanations, figures


def design_notes(results: ResultTable, policy=None) -> list[str]:
    """Rule-level findings G6 raises for the owner (report §5), from the current row."""
    cur = results.current()
    if cur is None:
        return []
    mv = cur.metrics.values
    notes = []
    if "fee.share_minmint_A" in mv:
        notes.append(
            "G6 design note (FEE-1 base): FEE-1 is a share of the vault's collateral, so a vault's "
            "round-trip "
            "fee as a share of its debt is 2·feeBps·baseRatio at every size — "
            f"{mv['fee.share_minmint_A']:.2%} for class A (ARMED) at the current values, above "
            f"max_fee_share_small for any feeBps > {_max_fee_bps_for_share(cur):.0f} bps. Levying FEE-1 on "
            "the "
            "debt value (mintedCents at pMint) would make the fee class-neutral; that is a rule change, "
            "out of "
            "scope for tuning."
        )
    fees = family_table(results, next(f for f in _fams() if f.name == "fees"))
    if len(fees) and not len(fees.feasible()):
        notes.append(
            "G6 design note (fee incentives): no feeBps/attestFeeBps on the grid gives seated attestors the "
            "policy's minimum monthly revenue under the adoption case while keeping the class-A fee share "
            "under max_fee_share_small. AFEE-1 is a fraction of FEE-1, so attestor pay is tied to the "
            "collateral fee; at low adoption it cannot cover a bond's opportunity cost without making "
            "class A "
            "expensive. Options for the owner: a lower bond (G8), a different AFEE-1 base, or accepting a "
            "higher fee share."
        )
    return notes


def _max_fee_bps_for_share(row: ResultRow) -> float:
    """The feeBps at which class A's minMint share reaches max_fee_share_small (share ∝ feeBps)."""
    mv = row.metrics.values
    cur_bps = int(row.params["feeBps"])
    share = mv.get("fee.share_minmint_A", math.nan)
    mx = float(row.metrics.meta.get("max_fee_share_small", 0.02))
    if not cur_bps or not math.isfinite(share) or share <= 0:
        return math.nan
    return mx / (share / cur_bps)


WHAT = {
    "peerLag": "how far either side of a quote tag REG-4 looks for peer quotes (± blocks)",
    "peerMin": "the fewest peer quotes REG-4 needs before it judges a tag",
    "deviationBps": "the deviation from the peers' median above which REG-4 penalises a tag",
    "accuracyBandBps": "the deviation within which a tag counts as accurate (REG-3 accuracy)",
    "payeeWindow": "the window of quote tags whose payout keys may receive a fee (FEE-2, E(R))",
    "feeMin": "the minimum FEE-1 per transaction, and through 4·feeMin the minimum collateral (MINT-5)",
    "feeBps": "FEE-1 as a share of a vault's collateral (mint, redeem and claim)",
    "attestFeeBps": "AFEE-1 to a bundle attestor, as a share of FEE-1 (ARMED mints and claims)",
    "nPenalty": "how long a penalised pool is skipped by the wallet's payee choice (REG-2, FEE-W)",
    "accuracyWindow": "the window of judgements behind a pool's accuracy score (REG-3, FEE-W)",
    "payeeTiltBps": "how strongly FEE-W favours accurate pools (weight 10⁴ + tilt·accuracy/10⁴)",
    "nReg": "the window in which a payout key counts as registered (REG-1, yed_listminers)",
}


def _fmt(x: Any, kind: str = "") -> str:
    if x is None:
        return "n/a"
    if isinstance(x, float) and not math.isfinite(x):
        return "n/a"
    if kind == "pct":
        return f"{float(x):.2%}"
    if kind == "usd":
        return f"${float(x):,.0f}"
    if isinstance(x, float):
        return f"{x:.4g}"
    return str(x)


def _family_of(param: str, fams: Mapping[str, Family]) -> Family:
    return next(f for f in fams.values() if param in f.owns)


def neighbour_sentence(rec: Recommendation, results: ResultTable, fam: Family) -> str:
    """'Why not the neighbouring values': the family rows one grid point either side of the
    recommended value (others at their current values), with their primary and violated constraints."""
    p = rec.param
    sub = family_table(results, fam)
    rows = [r for r in sub if set(r.delta) <= {p} | set(REGISTRY[p].parents)]
    vals = sorted({r.params[p] for r in rows if isinstance(r.params[p], int)})
    if rec.recommended not in vals:
        return ""
    i = vals.index(rec.recommended)
    parts = []
    for j in (i - 1, i + 1):
        if 0 <= j < len(vals):
            r = next(r for r in rows if r.params[p] == vals[j])
            viol = [c for c in fam.constraints if not r.metrics.constraints.get(c, True)]
            prim = r.metrics.values.get(fam.primary) if fam.primary != "zero" else None
            s = f"{vals[j]}"
            if prim is not None:
                s += f" ({fam.primary} {_fmt(prim)})"
            s += f" violates {', '.join(viol)}" if viol else " passes every constraint"
            parts.append(s)
    return ("Neighbouring values: " + "; ".join(parts) + ".") if parts else ""


def explain_g6(rec: Recommendation, results: ResultTable, fams: Mapping[str, Family]) -> str:
    fam = _family_of(rec.param, fams)
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
    out.append(f"Decision rule: {rec.rule}")
    out.append(f"Binding: {rec.binding}.")
    n = fam.name
    if n in ("peer_lag", "deviation", "accuracy", "peer_min"):
        out.append(
            f"Honest pools: p99 deviation {_fmt(mc.get('judge.honest_p99'))} bps (worst scenario), p75 in "
            "calm "
            f"{_fmt(mc.get('judge.honest_p75_calm'))} bps; false-penalty rate "
            f"{_fmt(mc.get('judge.false_penalty'), 'pct')} "
            f"(stale pools {_fmt(mc.get('judge.false_penalty_stale'), 'pct')}); "
            f"P(not evaluated) {_fmt(mc.get('judge.not_evaluated'), 'pct')}; "
            f"a ±20 % liar is caught on {_fmt(mc.get('judge.liar_detect_2000'), 'pct')} of its tags, ±10 % "
            "on "
            f"{_fmt(mc.get('judge.liar_detect_1000'), 'pct')} (current values)."
        )
        if rec.changed:
            out.append(
                f"At the recommended value: false penalties {_fmt(mr.get('judge.false_penalty'), 'pct')}, "
                f"P(not evaluated) {_fmt(mr.get('judge.not_evaluated'), 'pct')}, ±20 % liar detection "
                f"{_fmt(mr.get('judge.liar_detect_2000'), 'pct')}."
            )
    elif n == "fees":
        out.append(
            f"Current: minMint round-trip fee share {_fmt(mc.get('fee.share_minmint'), 'pct')} (ARMED, "
            "class A; "
            f"{_fmt(mc.get('fee.share_minmint_unarmed'), 'pct')} unarmed), pool revenue "
            f"{_fmt(mc.get('fee.pool_usd_month'), 'usd')}/month per pool (needs "
            f"{_fmt(mc.get('fee.pool_required_usd'), 'usd')}), attestor revenue "
            f"{_fmt(mc.get('fee.attestor_usd_month'), 'usd')}/month per seat (needs "
            f"{_fmt(mc.get('fee.attestor_required_usd'), 'usd')})."
        )
        if rec.changed:
            out.append(
                f"Recommended point: fee share {_fmt(mr.get('fee.share_minmint'), 'pct')}, pool "
                f"{_fmt(mr.get('fee.pool_usd_month'), 'usd')}, attestor "
                f"{_fmt(mr.get('fee.attestor_usd_month'), 'usd')}."
            )
    elif n == "fee_min":
        out.append(
            f"The redeem fee of a minMint vault is at most {_fmt(mc.get('fee.edge_fee_share_max'), 'pct')} "
            "of its "
            f"collateral (limit {_fmt(mc.get('fee.edge_fee_share_limit'), 'pct')}: the margin left at the "
            "claim "
            f"threshold); after a {CRASH_FRACTION:.0%} crash from the reference price its redeem fee is "
            f"{_fmt(mc.get('fee.redeem_fee_usd_crash'))} USD."
        )
        if rec.changed:
            out.append(
                "At the recommended value the worst fee share is "
                f"{_fmt(mr.get('fee.edge_fee_share_max'), 'pct')}."
            )
    elif n == "payee":
        out.append(
            f"P(FEE-0) in calm {_fmt(mc.get('payee.fee0'), 'pct')}; window "
            f"{_fmt(mc.get('payee.window_hours'))} h."
        )
    elif n == "n_penalty":
        out.append(
            f"A persistent liar is excluded {_fmt(mc.get('pen.liar_excluded'), 'pct')} of the time; an "
            "honest pool "
            f"{_fmt(mc.get('pen.honest_excluded'), 'pct')}."
        )
    elif n == "acc_window":
        out.append(
            f"An equal-share honest pool has ≈ {_fmt(mc.get('accw.tags_in_window'))} judged tags in the "
            "window; "
            f"its accuracy estimate has s.d. {_fmt(mc.get('accw.sd_bps'))} bps."
        )
    elif n == "tilt":
        out.append(
            f"FEE-W weight spread among honest pools {_fmt(mc.get('tilt.honest_spread'))}×; an honest pool "
            "of "
            "median accuracy out-weighs an accuracy-0 pool by "
            f"{_fmt(mc.get('tilt.accuracy_premium'), 'pct')}."
        )
    elif n == "n_reg":
        out.append(
            f"The smallest honest pool is registered {_fmt(mc.get('reg.registered'), 'pct')} of the time."
        )
    ns = neighbour_sentence(rec, results, fam)
    if ns:
        out.append(ns)
    s = rec.sensitivity.get("sentence")
    if s:
        out.append(s)
    out.append(f"Provenance: {rec.provenance}; confidence {rec.confidence}.")
    out.append(rec.klass_note)
    return " ".join(out)


def g6_figures(table: ResultTable, chosen: ParamSet, out: Path, fams: Mapping[str, Family]) -> list[Path]:
    plt, C = figure_style()
    paths: list[Path] = []
    cur = table.current()
    assert cur is not None
    # 1. judgement: honest p99 vs peerLag; false penalties and liar detection vs deviationBps
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 3.6), facecolor=C["surface"])
    for a in (a1, a2):
        a.set_facecolor(C["surface"])
        a.grid(alpha=0.2)
    rows = sorted(family_rows(table, fams["peer_lag"]), key=lambda r: int(r.params["peerLag"]))
    x = [int(r.params["peerLag"]) for r in rows]
    for s, col in zip(SCENARIOS, (C["blue"], C["orange"], C["green"], C["muted"]), strict=True):
        a1.plot(
            x,
            [r.metrics.values.get(f"judge.p99.{s}", math.nan) for r in rows],
            color=col,
            lw=2,
            marker="o",
            ms=3,
            label=s,
        )
    a1.axvline(int(table.base["peerLag"]), color=C["ink"], lw=1, ls="--")
    a1.set_xlabel("peerLag (blocks)", color=C["ink"])
    a1.set_ylabel("honest p99 deviation (bps)", color=C["ink"])
    a1.set_title("REG-4 honest deviation", color=C["ink"], fontsize=10)
    a1.legend(frameon=False, fontsize=7)
    rows = sorted(family_rows(table, fams["deviation"]), key=lambda r: int(r.params["deviationBps"]))
    x = [int(r.params["deviationBps"]) for r in rows]
    a2.plot(
        x,
        [100 * r.metrics.values["judge.false_penalty"] for r in rows],
        color=C["blue"],
        lw=2,
        label="honest false penalties",
    )
    a2.plot(
        x,
        [100 * r.metrics.values["judge.liar_detect_2000"] for r in rows],
        color=C["orange"],
        lw=2,
        label="±20 % liar caught",
    )
    a2.plot(
        x,
        [100 * r.metrics.values["judge.liar_detect_1000"] for r in rows],
        color=C["green"],
        lw=2,
        label="±10 % liar caught",
    )
    a2.axvline(cur.metrics.values["judge.dev_required"], color=C["muted"], lw=1, ls=":")
    a2.axvline(int(table.base["deviationBps"]), color=C["ink"], lw=1, ls="--")
    a2.set_xlabel("deviationBps", color=C["ink"])
    a2.set_ylabel("% of tags", color=C["ink"])
    a2.set_title("Penalties vs threshold (dotted: k_dev × p99)", color=C["ink"], fontsize=10)
    a2.legend(frameon=False, fontsize=7)
    fig.tight_layout()
    p = out / "g6_judgement.png"
    fig.savefig(p, dpi=110)
    plt.close(fig)
    paths.append(p)
    # 2. fees: fee share vs attestor revenue over the grid
    rows = family_rows(table, fams["fees"])
    fig, ax = plt.subplots(figsize=(6.4, 3.8), facecolor=C["surface"])
    ax.set_facecolor(C["surface"])
    ax.grid(alpha=0.2)
    xs = np.array([100 * r.metrics.values["fee.share_minmint"] for r in rows])
    ys = np.array([r.metrics.values["fee.attestor_usd_month"] for r in rows])
    ok = np.array([r.metrics.feasible for r in rows])
    ax.scatter(xs[~ok], ys[~ok], s=12, color=C["muted"], alpha=0.5, label="violates a constraint")
    if ok.any():
        ax.scatter(xs[ok], ys[ok], s=16, color=C["blue"], label="feasible")
    mv = cur.metrics.values
    ax.scatter(
        [100 * mv["fee.share_minmint"]],
        [mv["fee.attestor_usd_month"]],
        s=60,
        color=C["orange"],
        marker="*",
        label="current",
        zorder=3,
    )
    ch = [
        r
        for r in rows
        if r.params["feeBps"] == chosen["feeBps"] and r.params["attestFeeBps"] == chosen["attestFeeBps"]
    ]
    if ch and ch[0] is not cur:
        ax.scatter(
            [100 * ch[0].metrics.values["fee.share_minmint"]],
            [ch[0].metrics.values["fee.attestor_usd_month"]],
            s=60,
            color=C["green"],
            marker="D",
            label="recommended",
            zorder=3,
        )
    ax.axvline(100 * float(cur.metrics.meta.get("max_fee_share_small", 0.02)), color=C["ink"], lw=1, ls="--")
    ax.axhline(mv["fee.attestor_required_usd"], color=C["ink"], lw=1, ls=":")
    ax.set_xlabel("round-trip fee share of a minMint vault, class A ARMED (%)", color=C["ink"])
    ax.set_ylabel("attestor revenue (USD / seat / month)", color=C["ink"])
    ax.set_title("Fee grid (feeBps × attestFeeBps)", color=C["ink"], fontsize=10)
    ax.legend(frameon=False, fontsize=7)
    fig.tight_layout()
    p = out / "g6_fees.png"
    fig.savefig(p, dpi=110)
    plt.close(fig)
    paths.append(p)
    return paths


def make_study() -> G6Study:
    """The G6 study (``ybcal.studies.base.load_study("G6")``)."""
    return G6Study()

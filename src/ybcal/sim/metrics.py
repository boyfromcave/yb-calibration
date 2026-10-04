"""Vault-book metrics for the studies (owner: WP-4; PLAN §5.3, §5.4, §5.6, §5.7, §5.9).

Every function documents its exact definition. "Debt" is a vault's ``mintedCents``; "collateral
value" is ``collateralZat`` at the **true** market price (``PricePath`` / ``BlockInputs.true_price``,
never an oracle median). The bad-debt test is the exact integer comparison
``collateralZat · price < mintedCents · 10^12`` (µUSD/YEC × zat vs cents), computed with
``vkernels.is_underwater(coll, price, cents, 10^4)``.

Two paths:

* **Book metrics** read a :class:`~ybcal.sim.vaults.VaultBookResult` (block or hour mode): what
  agents actually did — claims, sweeps, VOIDs, refusals, fees, trajectories.
* **Fast path** :func:`p_bad_debt_fast` — no agents, no book: for every start hour of every path,
  a vault minted at the hour-mode pMint with the hour-mode σ multiplier (wallet collateral, exact
  MINT-5 rounding) is tested at ``lockHeight + grace`` (the claim path opening, fact 1.5-1) and at
  ``lockHeight``; vectorised over start dates, paths and a term grid per class. This is the G3/G4
  core metric over 1–6-year horizons.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from ybcal.model import vkernels as V
from ybcal.sim import agents as AG
from ybcal.sim import engine as E
from ybcal.sim import supply as SUP
from ybcal.sim import vaults as VB
from ybcal.units import BLOCKS_PER_DAY, BLOCKS_PER_HOUR, BPS, COIN

OWNER_WP = "WP-4"

CLASS_NAMES = ("A", "B", "C")


# ---------------------------------------------------------------------------------------------------
# Book metrics


def _acc(res: VB.VaultBookResult, term_class: int | None = None) -> np.ndarray:
    m = res.vaults["outcome"] == VB.O_ACTIVE
    if term_class is not None:
        m &= res.vaults["term_class"] == term_class
    return m


def bad_debt_prob(res: VB.VaultBookResult, *, at: str = "claim_open") -> dict[str, dict]:
    """P(bad debt) per class (``"A"``, ``"B"``, ``"C"``) and ``"all"``.

    Definition: among vaults accepted (ACTIVE) whose claim path opened inside the horizon
    (``at="claim_open"``: the first block above ``claimHeight``; ``at="lock"``: the first block
    above ``lockHeight``), the fraction whose collateral value at the true price *at that block* is
    below the debt. Censored vaults (the event lies beyond the horizon) are excluded. Returns
    ``{class: {"p": float, "n": int, "bad": int}}``."""
    col_s = "claim_open_step" if at == "claim_open" else "lock_open_step"
    col_b = "bad_at_claim_open" if at == "claim_open" else "bad_at_lock"
    out = {}
    for c, name in [(None, "all"), (0, "A"), (1, "B"), (2, "C")]:
        m = _acc(res, c) & (res.vaults[col_s] >= 0)
        n = int(m.sum())
        bad = int(res.vaults[col_b][m].sum())
        out[name] = {"p": bad / n if n else float("nan"), "n": n, "bad": bad}
    return out


def incremental_bad_debt_from_grace(res: VB.VaultBookResult) -> dict[str, float]:
    """``P(bad at claimHeight+1) − P(bad at lockHeight+1)`` per class over the vaults where both
    events lie inside the horizon (same population): the bad debt the grace period adds."""
    out = {}
    for c, name in [(None, "all"), (0, "A"), (1, "B"), (2, "C")]:
        m = _acc(res, c) & (res.vaults["claim_open_step"] >= 0) & (res.vaults["lock_open_step"] >= 0)
        n = int(m.sum())
        if not n:
            out[name] = float("nan")
            continue
        out[name] = float(res.vaults["bad_at_claim_open"][m].mean() - res.vaults["bad_at_lock"][m].mean())
    return out


def owner_miss(res: VB.VaultBookResult, params: Mapping, owner: AG.OwnerConfig | None = None) -> dict:
    """P(owner misses the window) vs grace.

    * ``analytic`` — :func:`ybcal.sim.agents.p_owner_miss` at ``grace`` (absent through
      ``[lockHeight, lockHeight + grace]``);
    * ``simulated`` — among accepted non-lost vaults, the share whose drawn return delay exceeds
      ``grace`` blocks;
    * ``loss`` — among accepted vaults, the share *claimed* (or stolen) while their owner was
      still away (``close_height ≤ lockHeight + 1 + delay``): the realised cost of absence."""
    g = int(params["grace"])
    v = res.vaults
    acc = _acc(res)
    honest = acc & (v["owner_kind"] != AG.LOST)
    n = int(honest.sum())
    sim = float((v["owner_delay"][honest] > g).mean()) if n else float("nan")
    away = (v["close_kind"] == VB.K_CLAIM) | (v["close_kind"] == VB.K_THIEF)
    away &= v["close_height"] <= v["lock_height"] + 1 + np.minimum(v["owner_delay"], 2**60)
    loss = float((away & honest).sum() / n) if n else float("nan")
    out = {"simulated": sim, "loss": loss, "n": n}
    if owner is not None:
        out["analytic"] = AG.p_owner_miss(g, owner)
    return out


def claimant_profit(res: VB.VaultBookResult) -> dict:
    """Distribution of claimant profit over executed claims: USD and bps of the debt (p10, p50,
    p90, mean), count, and the share of claims under RED-4(b)."""
    v = res.vaults
    m = (v["close_kind"] == VB.K_CLAIM) & (v["close_step"] >= 0)
    usd = v["claimant_profit_usd"][m]
    bps = usd / np.maximum(v["cents"][m] / 100, 1e-12) * BPS
    if not m.any():
        return {"n": 0}
    q = lambda x, p: float(np.percentile(x, p))  # noqa: E731
    return {
        "n": int(m.sum()),
        "usd_p10": q(usd, 10),
        "usd_p50": q(usd, 50),
        "usd_p90": q(usd, 90),
        "usd_mean": float(usd.mean()),
        "bps_p10": q(bps, 10),
        "bps_p50": q(bps, 50),
        "bps_p90": q(bps, 90),
        "share_b": float(v["claim_b"][m].mean()),
    }


def capital_efficiency(res: VB.VaultBookResult) -> dict[str, dict]:
    """YED minted per USD of collateral locked (debt / collateral value at the true price at the
    mint), and YED per YEC locked, per class: mean and median over accepted vaults."""
    out = {}
    v = res.vaults
    for c, name in [(None, "all"), (0, "A"), (1, "B"), (2, "C")]:
        m = _acc(res, c)
        if not m.any():
            out[name] = {"n": 0}
            continue
        usd = v["cents"][m] / 100
        eff = usd / np.maximum(v["value_at_mint_usd"][m], 1e-12)
        per_yec = usd / (v["collateral_zat"][m] / COIN)
        out[name] = {
            "n": int(m.sum()),
            "yed_per_usd_mean": float(eff.mean()),
            "yed_per_usd_p50": float(np.median(eff)),
            "yed_per_yec_p50": float(np.median(per_yec)),
        }
    return out


def liquidation_volume(res: VB.VaultBookResult, depth_usd: float | None = None) -> dict:
    """Collateral sold by claimants: per path and day, ``Σ claimantReceiveZat × price at the claim``
    (USD). Returns the daily array ``(paths, days)``, its max and p99 over path-days, and — with a
    ±2 % order-book ``depth_usd`` — ``max_depth_fraction`` (worst day / depth)."""
    v = res.vaults
    days = max(1, math.ceil(res.days))
    out = np.zeros((res.n_paths, days))
    m = (v["close_kind"] == VB.K_CLAIM) & (v["close_step"] >= 0)
    if m.any():
        d = (v["close_step"][m] * res.step_blocks // BLOCKS_PER_DAY).astype(np.int64)
        usd = AG.zat_value_usd(v["claimant_receive_zat"][m], v["tp_close"][m])
        path = v["path"][m] - int(v["path"].min(initial=0)) if v["path"].size else v["path"][m]
        np.add.at(out, (path, np.minimum(d, days - 1)), usd)
    r = {"daily_usd": out, "max_usd": float(out.max(initial=0)), "p99_usd": float(np.percentile(out, 99))}
    if depth_usd:
        r["max_depth_fraction"] = r["max_usd"] / float(depth_usd)
    return r


def emergency_recovery(res: VB.VaultBookResult) -> dict:
    """Collateral recovered through RED-4(b) claims: count, USD value at the claim of the collateral
    that left those vaults (claimant + residual), and of the residual returned to owners."""
    v = res.vaults
    m = v["claim_b"] & (v["close_step"] >= 0)
    rec = AG.zat_value_usd(v["collateral_zat"][m], v["tp_close"][m])
    resid = AG.zat_value_usd(v["residual_zat"][m], v["tp_close"][m])
    return {
        "n": int(m.sum()),
        "recovered_usd": float(rec.sum()),
        "residual_usd": float(resid.sum()),
        "debt_usd": float(v["cents"][m].sum() / 100),
    }


def emergency_benefit(res_on: VB.VaultBookResult, res_off: VB.VaultBookResult) -> dict:
    """Bad debt the emergency path removes (common random numbers): total final shortfall
    (:func:`system_shortfall`) with RED-4(b) off minus on, in USD per path."""
    a = system_shortfall(res_off)["final_usd"]
    b = system_shortfall(res_on)["final_usd"]
    return {"benefit_usd_mean": float((a - b).mean()), "benefit_usd_p95": float(np.percentile(a - b, 95))}


def system_shortfall(res: VB.VaultBookResult) -> dict:
    """System-level unbacked YED at every trajectory sample, per path (USD):
    ``unbacked`` (``Totals.unbackedCents`` — vaults closed without the burn: sweeps, thefts) plus
    ``under-collateralised`` debt ``Σ max(0, debt − collateral value)`` over vaults still ACTIVE at
    the sample. Returns the ``(paths, samples)`` arrays, ``final_usd`` per path, ``max_usd`` per path
    and ``p_any`` = share of paths whose shortfall is ever positive."""
    v = res.vaults
    P, S = res.supply_cents.shape
    under = np.zeros((P, S))
    ts = res.traj_steps
    acc = v["outcome"] == VB.O_ACTIVE
    p0 = int(v["path"].min(initial=0)) if v["path"].size else 0
    for p in range(P):
        m = acc & (v["path"] == p + p0)
        if not m.any():
            continue
        start = v["step"][m]
        end = np.where(v["close_step"][m] >= 0, v["close_step"][m], np.iinfo(np.int64).max)
        alive = (start[:, None] <= ts[None, :]) & (ts[None, :] < end[:, None])
        val = AG.zat_value_usd(v["collateral_zat"][m][:, None], res.true_price[p][None, :])
        gap = np.maximum(v["cents"][m][:, None] / 100 - val, 0.0)
        under[p] = (gap * alive).sum(axis=0)
    unb = res.unbacked_cents / 100
    tot = under + unb
    return {
        "under_usd": under,
        "unbacked_usd": unb,
        "total_usd": tot,
        "final_usd": tot[:, -1] if S else tot,
        "max_usd": tot.max(axis=1) if S else tot,
        "p_any": float((tot.max(axis=1) > 0).mean()) if S else 0.0,
    }


def supply_trajectory(res: VB.VaultBookResult, quantiles: Sequence[float] = (10, 50, 90)) -> dict:
    """YED supply (USD) at each trajectory sample: the per-path array and quantiles over paths;
    ``days`` gives the sample times."""
    usd = res.supply_cents / 100
    return {
        "days": res.traj_steps * res.step_blocks / BLOCKS_PER_DAY,
        "supply_usd": usd,
        "quantiles": {q: np.percentile(usd, q, axis=0) for q in quantiles},
    }


def refusal_breakdown(res: VB.VaultBookResult) -> dict:
    """Mint attempts by outcome and refusal / VOID reason (MINT-4 halts, MINT-6 cap, …), per class."""
    v = res.vaults
    out: dict = {}
    for c, name in [(0, "A"), (1, "B"), (2, "C")]:
        m = v["term_class"] == c
        reasons: dict[str, int] = {}
        for r in v["reason"][m & (v["outcome"] != VB.O_ACTIVE)]:
            reasons[str(r)] = reasons.get(str(r), 0) + 1
        out[name] = {
            "attempts": int(m.sum()),
            "accepted": int((m & (v["outcome"] == VB.O_ACTIVE)).sum()),
            "void": int((m & (v["outcome"] == VB.O_VOID)).sum()),
            "reasons": reasons,
        }
    return out


def fee_revenue(
    res: VB.VaultBookResult,
    *,
    payee_share: Mapping[int, float] | None = None,
    n_pools: int | None = None,
    n_seated: int | None = None,
) -> dict:
    """Fee revenue of the book (PLAN §5.6): FEE-1 paid by mints (valued at the true price at the
    mint) and by owner redeems / claims (at the close), per path per day, in YEC and USD; split per
    pool by ``payee_share`` (e.g. :func:`ybcal.sim.fees.fee_w_weights`) or equally over ``n_pools``;
    AFEE-1 per seated attestor per 30 days when ``n_seated`` is given; and the total fee as a share
    of the debt per class (median)."""
    v = res.vaults
    acc = v["outcome"] == VB.O_ACTIVE
    closed = acc & (v["close_step"] >= 0)
    usd_pool = AG.zat_value_usd(v["pool_fee_mint"][acc], v["tp_mint"][acc]).sum()
    usd_pool += AG.zat_value_usd(v["pool_fee_close"][closed], v["tp_close"][closed]).sum()
    yec_pool = (v["pool_fee_mint"][acc].sum() + v["pool_fee_close"][closed].sum()) / COIN
    usd_att = AG.zat_value_usd(v["attest_fee_mint"][acc], v["tp_mint"][acc]).sum()
    usd_att += AG.zat_value_usd(v["attest_fee_close"][closed], v["tp_close"][closed]).sum()
    path_days = max(res.days * max(res.n_paths, 1), 1e-12)
    out: dict = {
        "pool_usd_per_day": float(usd_pool / path_days),
        "pool_yec_per_day": float(yec_pool / path_days),
        "attest_usd_per_day": float(usd_att / path_days),
    }
    share = (
        dict(payee_share)
        if payee_share
        else ({i: 1.0 / n_pools for i in range(n_pools)} if n_pools else None)
    )
    if share:
        out["per_pool_usd_month"] = {k: out["pool_usd_per_day"] * 30 * w for k, w in share.items()}
    if n_seated:
        out["per_attestor_usd_month"] = out["attest_usd_per_day"] * 30 / n_seated
    fee_tot = v["pool_fee_mint"] + v["attest_fee_mint"] + v["pool_fee_close"] + v["attest_fee_close"]
    share_c = {}
    for c, name in enumerate(CLASS_NAMES):
        m = closed & (v["term_class"] == c)
        if m.any():
            usd = AG.zat_value_usd(fee_tot[m], v["tp_mint"][m])
            share_c[name] = float(np.median(usd / (v["cents"][m] / 100)))
    out["fee_share_of_debt_p50"] = share_c
    return out


def time_until_cap_admits(
    params: Mapping, price_path, cents: int, *, sigma_mult_bps: int = BPS, existing_supply_cents: int = 0
) -> dict[str, np.ndarray]:
    """Days after ``startHeight`` until MINT-6 first admits a mint of ``cents`` in each class (W20:
    a class whose ``baseRatio·σ`` reaches ``recapRatioBps`` bypasses the cap → 0), per path —
    :func:`ybcal.sim.supply.days_until_cap_admits` with the class gate. ``price_path`` is the pMint
    series from ``startHeight`` (block array or hour PricePath)."""
    return {
        name: SUP.days_until_cap_admits(
            cents,
            params,
            price_path,
            term_class=c,
            sigma_mult_bps=sigma_mult_bps,
            existing_supply_cents=existing_supply_cents,
        )
        for c, name in enumerate(CLASS_NAMES)
    }


# ---------------------------------------------------------------------------------------------------
# Fast path: P(bad debt) over long horizons


@dataclass
class FastBadDebt:
    """Result of :func:`p_bad_debt_fast`. ``p[c]`` is the term-weighted P(bad debt) of class ``c``
    at the claim-path opening; ``p_lock[c]`` the same at the owner-path opening; ``by_grace[c][g]``
    at ``lock + g`` for each requested grace ``g``; ``by_term[c]`` per grid term. ``n[c]`` counts
    (start, path) samples per term (equal-weighted)."""

    p: dict[str, float]
    p_lock: dict[str, float]
    increment: dict[str, float]
    by_term: dict[str, np.ndarray]
    terms: dict[str, np.ndarray]
    by_grace: dict[str, dict[int, float]]
    n: dict[str, int]
    meta: dict = field(default_factory=dict)
    #: mean uncovered share of the debt at the claim-path opening, ``E[max(0, 1 − value/debt)]``,
    #: term-grid mean — the severity behind ``p`` (D-RD-AUD-3; 0 when no sample is bad)
    shortfall: dict[str, float] = field(default_factory=dict)


def _sigma_choice(sig: np.ndarray, sigma, skip: int) -> np.ndarray:
    if isinstance(sigma, int | np.integer) and not isinstance(sigma, bool):
        return np.full(sig.shape, int(sigma), dtype=np.int64)
    if sigma == "series":
        return sig
    q = {"median": 50, "p90": 90}.get(str(sigma))
    if q is None:
        raise ValueError(f"sigma must be 'series', 'median', 'p90' or an int (got {sigma!r})")
    s = sig[:, skip:] if sig.shape[1] > skip else sig
    per_path = np.percentile(s, q, axis=1).astype(np.int64)
    return np.broadcast_to(per_path[:, None], sig.shape)


def p_bad_debt_fast(
    params: Mapping,
    hourly_true,
    *,
    hour_series: E.HourSeries | None = None,
    kernel: E.OracleTransferKernel | None = None,
    rng: np.random.Generator | None = None,
    classes: Sequence[int] = (0, 1, 2),
    n_terms: int = 16,
    term_distribution: str = "uniform",
    sigma="series",
    cents: int = 100_000,
    graces: Sequence[int] | None = None,
    start_stride: int = 1,
    skip_hours: int | None = None,
    chunk_paths: int = 32,
) -> FastBadDebt:
    """P(bad debt) by the ratio test at ``lockHeight + grace``, vectorised over start dates × paths.

    For each path ``p``, start hour ``s`` (every ``start_stride`` hours after ``skip_hours``, default
    the σ and slow-median warm-up ``(volWindow + pSlowWindow)/48``), class ``c`` and grid term ``T``
    (:func:`ybcal.sim.agents.term_grid`, ``term_distribution``):

    * the mint reads the hour-mode snapshot ``s`` (``R = heights[s]``): skipped when it is not
      mintable for price reasons (pMint undefined, NO_PRICE / DIVERGENCE);
    * ``collateral`` = :func:`ybcal.sim.vaults.wallet_collateral` of ``cents`` at pMint[s] and the
      σ multiplier (``sigma="series"``: σ[s]; ``"median"`` / ``"p90"``: that quantile of the
      path's σ after warm-up; an int: fixed);
    * the claim path opens at block ``R + T + grace + 1`` → hour ``s + ceil((T + grace + 1)/48)``;
      **bad** iff ``collateral · truePrice(hour) < cents · 10^12``. Starts whose end lies beyond
      the horizon are censored (excluded).

    ``p[c]`` averages the terms with equal weights (the grid is equal-probability quantiles of the
    term distribution). ``graces`` adds the same test at other grace values (G4 curve). No agents,
    no supply cap, no HALT-2: a pure solvency statistic of the class ratios (PLAN §5.3)."""
    ht, _res = E.prices_from(hourly_true)
    P, n = ht.shape
    g0 = int(params["grace"])
    gl = sorted({g0, *(int(g) for g in (graces or ()))})
    rp = VB.RuleParams.of(params)
    if skip_hours is None:
        skip_hours = math.ceil((int(params["volWindow"]) + int(params["pSlowWindow"])) / BLOCKS_PER_HOUR)
    kernel = kernel or E.OracleTransferKernel.ideal(params)
    acc: dict[tuple[int, int, str], list[int]] = {}
    sf: dict[tuple[int, int], float] = {}
    terms_of = {c: AG.term_grid(params, c, n_terms, term_distribution)[0] for c in classes}  # type: ignore[arg-type]
    starts = np.arange(skip_hours, n, max(1, int(start_stride)))
    for a in range(0, P, chunk_paths):
        sl = slice(a, min(P, a + chunk_paths))
        if hour_series is not None:
            pm = hour_series.p_mint[sl]
            sg = hour_series.sigma_mult_bps[sl]
            hm = hour_series.halt_mask[sl]
        else:
            hs = E.simulate_hours(params, ht[sl], kernel, rng=rng, noise=rng is not None)
            pm, sg, hm = hs.p_mint, hs.sigma_mult_bps, hs.halt_mask
        sgc = _sigma_choice(np.asarray(sg, dtype=np.int64), sigma, skip_hours)
        tp = ht[sl]
        pms = pm[:, starts]
        okm = (pms > 0) & ((hm[:, starts] & (E.HALT_NO_PRICE | E.HALT_DIVERGENCE)) == 0)
        for c in classes:
            coll = VB.wallet_collateral(
                rp, np.full(pms.shape, cents), np.full(pms.shape, c), sgc[:, starts], np.where(okm, pms, 1)
            )
            okc = okm & (coll != VB.UNDEF)
            for ti, T in enumerate(terms_of[c].tolist()):
                for key, off in [("lock", T + 1)] + [(f"g{g}", T + g + 1) for g in gl]:
                    e = starts + -(-off // BLOCKS_PER_HOUR)
                    inside = e < n
                    if not inside.any():
                        acc.setdefault((c, ti, key), [0, 0])
                        continue
                    cols = np.nonzero(inside)[0]
                    tpe = tp[:, e[cols]]
                    good = okc[:, cols]
                    bad = V.is_underwater(np.where(good, coll[:, cols], 0), tpe, cents, BPS) & good
                    r = acc.setdefault((c, ti, key), [0, 0])
                    r[0] += int(bad.sum())
                    r[1] += int(good.sum())
                    if key == f"g{g0}" and bad.any():
                        val = coll[:, cols][bad].astype(np.float64) * tpe[bad].astype(np.float64) / COIN
                        gap = np.maximum(0.0, 1.0 - val / (cents * 1e4))
                        sf[(c, ti)] = sf.get((c, ti), 0.0) + float(gap.sum())
    p, p_lock, inc, by_term, by_grace, nn, short = {}, {}, {}, {}, {}, {}, {}
    for c in classes:
        name = CLASS_NAMES[c]
        nt = len(terms_of[c])

        def rate(key: str, c=c, nt=nt) -> tuple[np.ndarray, int]:
            vals = []
            cnt = 0
            for ti in range(nt):
                b, k = acc.get((c, ti, key), [0, 0])
                vals.append(b / k if k else np.nan)
                cnt += k
            return np.array(vals), cnt

        bt, cnt = rate(f"g{g0}")
        bl, _ = rate("lock")
        by_term[name] = bt
        p[name] = float(np.nanmean(bt)) if np.isfinite(bt).any() else float("nan")
        p_lock[name] = float(np.nanmean(bl)) if np.isfinite(bl).any() else float("nan")
        inc[name] = p[name] - p_lock[name]
        by_grace[name] = {g: float(np.nanmean(rate(f"g{g}")[0])) for g in gl}
        nn[name] = cnt // max(nt, 1)
        per = [
            sf.get((c, ti), 0.0) / acc[(c, ti, f"g{g0}")][1]
            for ti in range(nt)
            if acc.get((c, ti, f"g{g0}"), [0, 0])[1]
        ]
        short[name] = float(np.mean(per)) if per else float("nan")
    return FastBadDebt(
        p,
        p_lock,
        inc,
        by_term,
        {CLASS_NAMES[c]: terms_of[c] for c in classes},
        by_grace,
        nn,
        meta={
            "cents": cents,
            "sigma": sigma,
            "skip_hours": skip_hours,
            "start_stride": start_stride,
            "paths": P,
            "hours": n,
        },
        shortfall=short,
    )


__all__ = [
    "FastBadDebt",
    "bad_debt_prob",
    "capital_efficiency",
    "claimant_profit",
    "emergency_benefit",
    "emergency_recovery",
    "fee_revenue",
    "incremental_bad_debt_from_grace",
    "liquidation_volume",
    "owner_miss",
    "p_bad_debt_fast",
    "refusal_breakdown",
    "supply_trajectory",
    "system_shortfall",
    "time_until_cap_admits",
]

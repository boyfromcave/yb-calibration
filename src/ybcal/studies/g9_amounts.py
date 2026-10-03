"""G9 — amounts and wallet policy: minMint, maxMint, minOutput, maxOutput, residualMinZat and the
excluded carrierValue, walletConfirmations, DEFAULT_REF_LAG (PLAN §5.9).

Owner: WP-7b. Method, metric definitions and decision rules: ``docs/studies/g9.md``.

These parameters are **constraint-driven**: each constraint is a closed-form bound, so the study
computes every parameter's *admissible range* ``[lo, hi]`` (rounded onto the registry lattice:
lower bounds up, upper bounds down) and applies a *verify* rule (D-WP7b-5): KEEP the current value
while it lies in its range; otherwise move it to the nearest admissible value; BLOCKED when the range
is empty (``lo > hi``). Minimal change, PLAN §2.3: a value is not moved just because a smaller or
larger one would also be admissible. The amount order ``minOutput ≤ minMint ≤ maxMint ≤ maxOutput``
(MINT-2, XFER-1, RED-1) is enforced by resolving ``maxMint`` → ``maxOutput`` → ``minMint`` →
``minOutput`` in that order, each against the already recommended neighbours.

Bounds (all documented in docs/studies/g9.md):

* ``minMint`` ≥ the 4·feeMin floor at the policy's worst price (MINT-5; the ``fee_floor_mintable``
  invariant) and ≥ the size below which the ``feeMin`` floor pushes a minMint vault's round-trip fee
  share above ``max(max_fee_share_small, the class's proportional share)`` at the reference price.
  FEE-1 is proportional to the collateral above the floor, so the round-trip share of the debt is
  ``2 · feeBps · ratio`` for every size — 2.5 % for class A at 500 % — which no ``minMint`` can fix
  (design note G9-DN1).
* ``maxMint`` ≤ the size whose liquidation at the claim threshold stays within
  ``max_depth_fraction`` of the p10 daily YEC volume — evaluated only when a depth/volume series is
  loaded (``env.data["depth"]``) or the policy sets ``yec_daily_volume_p10_usd``; otherwise the
  study reports the volume the current value needs and keeps it (PROVISIONAL).
* ``minOutput`` ≥ ``dust_spend_multiple`` × the network fee at the worst price (a YED output worth less
  than spending it is dust); ≤ ``minMint``. ``maxOutput`` ≥ ``maxMint``.
* ``residualMinZat`` ≥ max(dust, ``dust_spend_multiple`` × network fee) and ≤
  ``residual_max_share`` of the minMint debt at the worst price.
* ``carrierValue`` (wallet policy) ≥ max(dust, ``dust_spend_multiple`` × network fee) and ≤
  ``carrier_max_share`` of the minMint debt at the worst price.
* ``walletConfirmations`` (wallet policy) ≥ the smallest z with Nakamoto's catch-up probability of an
  attacker holding ``reorg_attacker_share`` ≤ ``max_reorg_prob`` (Bitcoin paper §11).
* ``DEFAULT_REF_LAG`` (wallet default) ≥ the smallest lag with P(VOID by a natural reorg deeper than
  the lag) = ``orphan_rate^(lag+1)`` ≤ ``max_void_prob``, and ≤ ``refWindow −
  mint_inclusion_slack_blocks`` (MINT-2 window must still hold at confirmation).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ybcal.data.synthetic import DEFAULT_P0
from ybcal.params.paramset import ParamSet
from ybcal.params.registry import REGISTRY, params_for_group
from ybcal.studies import g3_collateral as G3
from ybcal.studies.base import Budget, Env, Metrics, Recommendation, ResultTable, final_verdict
from ybcal.studies.g1_price_windows import (
    C_CUR,
    C_REC,
    C_TEXT,
    C_TRUE,
    evidence_dir,
    out_dir_of,
    plot_style,
    real_price,
)
from ybcal.units import BPS, COIN

OWNER_WP = "WP-7b"

GROUP = "G9"
#: Network fee a wallet adds (YELLOWBACK_FEE; AgentsConfig.tx_fee_zat).
TX_FEE_ZAT = 1_000

JUDGEMENT: dict[str, Any] = {
    "dust_spend_multiple": 3.0,  # an output is dust when worth less than this × the fee to spend it
    "residual_max_share": 0.01,  # residualMinZat ≤ this share of a minMint debt at the worst price
    "carrier_max_share": 0.001,  # carrierValue ≤ this share of a minMint debt at the worst price
    "mint_inclusion_slack_blocks": 10,  # blocks a mint may wait for inclusion (MINT-2 window must hold)
    "yec_daily_volume_p10_usd": None,  # p10 daily YEC volume when no depth CSV is loaded (None = unknown)
    "reference_price_usd": None,  # YEC price for fee shares (None = real median, else the synthetic p0)
}


def pget(policy: Any, key: str) -> Any:
    v = getattr(policy, key, None)
    return v if v is not None else JUDGEMENT.get(key)


# ---------------------------------------------------------------------------------------------------
# Closed forms


def nakamoto_catch_up(q: float, z: int) -> float:
    """Probability an attacker with hash share ``q`` ever catches up from ``z`` blocks behind
    (Nakamoto 2008 §11, Poisson approximation of the attacker's progress)."""
    p = 1.0 - q
    if q >= p:
        return 1.0
    if z <= 0:
        return 1.0
    lam = z * q / p
    s = 1.0
    pois = math.exp(-lam)
    for k in range(z + 1):
        if k:
            pois *= lam / k
        s -= pois * (1 - (q / p) ** (z - k))
    return max(0.0, s)


def min_confirmations(q: float, max_prob: float, cap: int = 10_000) -> int:
    """Smallest ``z ≥ 1`` with :func:`nakamoto_catch_up` ≤ ``max_prob`` (``cap + 1`` if none)."""
    for z in range(1, cap + 1):
        if nakamoto_catch_up(q, z) <= max_prob:
            return z
    return cap + 1


def p_void_natural(lag: int, orphan_rate: float) -> float:
    """P(a mint built at R = tip − lag is VOIDed by a natural reorg replacing block R): reorg depth ≥
    lag + 1, with independent per-block orphan events, ≈ ``orphan_rate^(lag + 1)``."""
    return float(orphan_rate) ** (int(lag) + 1)


def _round_up(x: float, step: int) -> int:
    return int(math.ceil(x / step - 1e-12) * step)


def _round_down(x: float, step: int) -> int:
    return int(math.floor(x / step + 1e-12) * step)


@dataclass(frozen=True)
class G9Context:
    """Environment facts the closed forms need (recorded in Metrics.meta so ``decide`` can recompute)."""

    p_ref_microusd: int
    p_ref_source: str
    worst_microusd: int
    volume_p10_usd: float | None
    volume_source: str
    max_fee_share_small: float
    max_depth_fraction: float
    dust_zat: int
    dust_mult: float
    residual_max_share: float
    carrier_max_share: float
    inclusion_slack: int
    reorg_q: float
    max_reorg_prob: float
    orphan_rate: float
    max_void_prob: float

    @classmethod
    def build(cls, env: Env) -> G9Context:
        pol = env.policy
        rp = real_price(env)
        ref = pget(pol, "reference_price_usd")
        if ref is not None:
            p_ref, src = round(float(ref) * 1e6), "policy"
        elif rp is not None:
            x = np.asarray(rp.prices, dtype=float)
            x = x[x > 0]
            p_ref, src = (int(np.median(x[-24 * 30 :])) if x.size else DEFAULT_P0), "real-data"
        else:
            p_ref, src = DEFAULT_P0, "synthetic"
        vol, vsrc = None, "none"
        d = env.data.get("depth") if isinstance(env.data, Mapping) else None
        arr = getattr(d, "volume_24h_usd", None)
        if arr is not None:
            a = np.asarray(arr, dtype=float)
            a = a[np.isfinite(a) & (a > 0)]
            if a.size:
                vol, vsrc = float(np.percentile(a, 10)), "real-data"
        if vol is None and pget(pol, "yec_daily_volume_p10_usd") is not None:
            vol, vsrc = float(pget(pol, "yec_daily_volume_p10_usd")), "policy"
        return cls(
            p_ref,
            src,
            round(float(pol.worst_price_usd) * 1e6),
            vol,
            vsrc,
            float(pol.max_fee_share_small),
            float(pol.max_depth_fraction),
            int(pol.dust_zat),
            float(pget(pol, "dust_spend_multiple")),
            float(pget(pol, "residual_max_share")),
            float(pget(pol, "carrier_max_share")),
            int(pget(pol, "mint_inclusion_slack_blocks")),
            float(pol.reorg_attacker_share),
            float(pol.max_reorg_prob),
            float(pol.orphan_rate),
            float(pol.max_void_prob),
        )

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> G9Context:
        return cls(**{k: d[k] for k in cls.__dataclass_fields__})


def fee_shares(ps: Mapping, cents: int, price: int) -> dict[str, float]:
    """Round-trip FEE-1 share of the debt for a ``cents`` vault per class at ``price`` (σ = 1,
    :func:`ybcal.sim.fees.fee_table`) and each class's proportional share ``2·feeBps·ratio``."""
    from ybcal.sim import fees as F

    out = {}
    for c, n in enumerate(G3.CLASS_NAMES):
        row = F.fee_table(ps, [int(cents)], int(price), term_class=c)[0]
        out[f"fee_share.{n}"] = float(row["fee_share_of_debt"])
        out[f"floor_binds.{n}"] = float(row["floor_binds"] or row["mint_fee_zat"] == int(ps["feeMin"]))
        out[f"prop_share.{n}"] = 2 * int(ps["feeBps"]) * int(ps[f"baseRatioBps[{c}]"]) / BPS / BPS
    return out


def ranges(ps: Mapping, ctx: G9Context) -> dict[str, tuple[float, float, str, str]]:
    """Admissible ``(lo, hi, binding_lo, binding_hi)`` per parameter (unrounded; ±inf when open)."""
    fee_min = int(ps["feeMin"])
    rmin = min(int(ps[f"baseRatioBps[{c}]"]) for c in range(3))
    theta = int(ps["claimThresholdBps"])
    worst = ctx.worst_microusd
    out: dict[str, tuple[float, float, str, str]] = {}
    # minMint: 4·feeMin at the worst price; fee floor at the reference price
    mintable = 4 * fee_min * worst / (rmin * COIN)  # cents (cents × bps = µUSD)
    floor_lo = 0.0
    for c in range(3):
        prop = 2 * int(ps["feeBps"]) * int(ps[f"baseRatioBps[{c}]"]) / BPS / BPS
        allow = max(ctx.max_fee_share_small, prop)
        floor_lo = max(floor_lo, 2 * fee_min / COIN * ctx.p_ref_microusd / 1e6 / allow * 100)
    lo = max(mintable, floor_lo)
    out["minMint"] = (
        lo,
        float(ps["maxMint"]),
        "4·feeMin at the worst price"
        if mintable >= floor_lo
        else "feeMin floor share at the reference price",
        "maxMint",
    )
    # maxMint: liquidation at the claim threshold within max_depth_fraction of p10 daily volume
    if ctx.volume_p10_usd is not None:
        hi = ctx.max_depth_fraction * ctx.volume_p10_usd * BPS / theta * 100
        out["maxMint"] = (
            float(ps["minMint"]),
            min(hi, float(ps["maxOutput"])),
            "minMint",
            "liquidation ≤ max_depth_fraction × p10 daily volume",
        )
    else:
        out["maxMint"] = (
            float(ps["minMint"]),
            float(ps["maxOutput"]),
            "minMint",
            "maxOutput (depth unknown)",
        )
    dust_usd_cents = ctx.dust_mult * TX_FEE_ZAT / COIN * worst / 1e6 * 100
    out["minOutput"] = (max(1.0, dust_usd_cents), float(ps["minMint"]), "dust at the worst price", "minMint")
    out["maxOutput"] = (
        float(ps["maxMint"]),
        float(REGISTRY["maxOutput"].bounds[1]),
        "maxMint",
        "registry bound",
    )
    spend = max(float(ctx.dust_zat), ctx.dust_mult * TX_FEE_ZAT)
    min_mint_zat_worst = int(ps["minMint"]) / 100 / (worst / 1e6) * COIN
    out["residualMinZat"] = (
        spend,
        ctx.residual_max_share * min_mint_zat_worst,
        "dust / spend cost",
        "share of a minMint debt at the worst price",
    )
    out["carrierValue"] = (
        spend,
        ctx.carrier_max_share * min_mint_zat_worst,
        "dust / spend cost",
        "share of a minMint debt at the worst price",
    )
    out["walletConfirmations"] = (
        float(min_confirmations(ctx.reorg_q, ctx.max_reorg_prob)),
        float(REGISTRY["walletConfirmations"].bounds[1]),
        "Nakamoto catch-up ≤ max_reorg_prob",
        "registry bound",
    )
    lag_lo = 0
    while p_void_natural(lag_lo, ctx.orphan_rate) > ctx.max_void_prob and lag_lo < 64:
        lag_lo += 1
    out["DEFAULT_REF_LAG"] = (
        float(lag_lo),
        float(int(ps["refWindow"]) - ctx.inclusion_slack),
        "natural VOID ≤ max_void_prob",
        "refWindow − inclusion slack",
    )
    return out


def g9_values(ps: Mapping, ctx: G9Context) -> tuple[dict[str, float], dict[str, bool]]:
    """Every G9 metric and per-parameter ``<param>_ok`` constraint at ``ps``."""
    v: dict[str, float] = {"zero": 0.0}
    v.update({f"{k}@ref": x for k, x in fee_shares(ps, int(ps["minMint"]), ctx.p_ref_microusd).items()})
    v.update({f"{k}@worst": x for k, x in fee_shares(ps, int(ps["minMint"]), ctx.worst_microusd).items()})
    v["fee_share.A.any_size"] = 2 * int(ps["feeBps"]) * int(ps["baseRatioBps[0]"]) / BPS / BPS
    rmin = min(int(ps[f"baseRatioBps[{c}]"]) for c in range(3))
    # price above which the feeMin floor sets FEE-1 of a minMint vault (lowest-ratio class)
    v["fee.floor_binds_above_usd"] = (
        int(ps["minMint"]) / 100 * rmin / BPS / (int(ps["feeMin"]) * BPS / int(ps["feeBps"]) / COIN)
    )
    theta = int(ps["claimThresholdBps"])
    liq = int(ps["maxMint"]) / 100 * theta / BPS
    v["maxMint.liquidation_usd"] = liq
    v["maxMint.volume_needed_usd"] = liq / max(ctx.max_depth_fraction, 1e-12)
    v["maxMint.liquidation_share"] = (liq / ctx.volume_p10_usd) if ctx.volume_p10_usd else math.nan
    z = int(ps["walletConfirmations"])
    v["reorg.p_catch_up"] = nakamoto_catch_up(ctx.reorg_q, z)
    lag = int(ps["DEFAULT_REF_LAG"])
    v["void.p_natural"] = p_void_natural(lag, ctx.orphan_rate)
    v["void.p_attacker"] = nakamoto_catch_up(ctx.reorg_q, lag + 1)
    v["residual.usd_at_worst"] = int(ps["residualMinZat"]) / COIN * ctx.worst_microusd / 1e6
    v["carrier.usd_at_worst"] = int(ps["carrierValue"]) / COIN * ctx.worst_microusd / 1e6
    cons: dict[str, bool] = {}
    for p, (lo, hi, _, _) in ranges(ps, ctx).items():
        x = float(ps[p])
        v[f"{p}.lo"] = lo
        v[f"{p}.hi"] = hi
        cons[f"{p}_ok"] = lo <= x <= hi
    return v, cons


def provenance_for(param: str, ctx: G9Context) -> str:
    if param == "minMint":
        return {"real-data": "real-data", "policy": "judgement"}.get(ctx.p_ref_source, "synthetic")
    if param == "maxMint":
        return {"real-data": "real-data", "policy": "judgement"}.get(ctx.volume_source, "synthetic")
    return "judgement"


ORDER = (
    "maxMint",
    "maxOutput",
    "minMint",
    "minOutput",
    "residualMinZat",
    "carrierValue",
    "walletConfirmations",
    "DEFAULT_REF_LAG",
)

RULES = {
    "minMint": "minMint ∈ [max(4·feeMin collateral at worst_price_usd, size where the feeMin floor pushes "
    "the round-trip fee share above max(max_fee_share_small, the class's proportional share) at the "
    "reference price), maxMint]; KEEP inside the range, else the nearest admissible $10 step.",
    "maxMint": "maxMint ∈ [minMint, max_depth_fraction × p10 daily YEC volume ÷ claim threshold] "
    "(one vault's liquidation at the claim threshold within the depth budget), ≤ maxOutput; KEEP inside, "
    "else the nearest admissible $1,000 step; not evaluable without a volume series.",
    "minOutput": "minOutput ∈ [dust_spend_multiple × the network fee at worst_price_usd, minMint]; "
    "KEEP inside.",
    "maxOutput": "maxOutput ∈ [maxMint, registry bound]; KEEP inside.",
    "residualMinZat": "residualMinZat ∈ [max(dust_zat, dust_spend_multiple × network fee), "
    "residual_max_share × a minMint debt at worst_price_usd]; KEEP inside.",
    "carrierValue": "carrierValue ∈ [max(dust_zat, dust_spend_multiple × network fee), carrier_max_share × a "
    "minMint debt at worst_price_usd]; KEEP inside.",
    "walletConfirmations": "walletConfirmations ≥ the smallest z with Nakamoto's catch-up probability at "
    "reorg_attacker_share ≤ max_reorg_prob; KEEP if already ≥, else raise to it.",
    "DEFAULT_REF_LAG": "DEFAULT_REF_LAG ∈ [smallest lag with orphan_rate^(lag+1) ≤ max_void_prob, "
    "refWindow − mint_inclusion_slack_blocks]; KEEP inside.",
}


@dataclass
class G9Study:
    """Amounts and wallet policy (PLAN §5.9)."""

    group: str = GROUP
    params: tuple[str, ...] = field(default_factory=lambda: params_for_group(GROUP))

    def space(self, base: ParamSet, budget: Budget) -> Iterable[ParamSet]:
        out = [base]
        sweeps = {
            "minMint": G3.lattice(int(base["minMint"]), "minMint", hi=min(30_000, int(base["maxMint"]))),
            "walletConfirmations": list(range(1, REGISTRY["walletConfirmations"].bounds[1] + 1)),
            "DEFAULT_REF_LAG": list(range(0, REGISTRY["DEFAULT_REF_LAG"].bounds[1] + 1)),
            "maxMint": G3.lattice(int(base["maxMint"]), "maxMint", step=200_000),
        }
        for p, vals in sweeps.items():
            for v in vals:
                if v != int(base[p]):
                    out.append(base.replace({p: v}))
        return [ps for i, ps in enumerate(out) if i == 0 or G3.valid(ps)]

    def evaluate(self, cand: ParamSet, env: Env) -> Metrics:
        ctx = G9Context.build(env)
        v, cons = g9_values(cand, ctx)
        prov = "real-data" if ctx.p_ref_source == "real-data" else "synthetic"
        meta = {"budget": env.budget.name, "seed": env.seed, "out_dir": out_dir_of(env), "ctx": ctx.to_dict()}
        return Metrics(v, "zero", True, cons, prov, meta)  # type: ignore[arg-type]

    def decide(self, results: ResultTable, policy: Any) -> list[Recommendation]:
        cur = results.current()
        if cur is None:
            raise ValueError("G9: the current set was not evaluated")
        ctx = G9Context.from_dict(cur.metrics.meta["ctx"])
        base = results.base
        chosen: dict[str, int] = {}
        info: dict[str, dict[str, Any]] = {}
        ps = base
        for p in ORDER:
            lo, hi, blo, bhi = ranges(ps, ctx)[p]
            step = max(1, REGISTRY[p].step)
            lo_r, hi_r = (
                _round_up(lo, step),
                _round_down(hi, step) if math.isfinite(hi) else REGISTRY[p].bounds[1],
            )
            blo_, bhi_ = REGISTRY[p].bounds
            lo_r, hi_r = max(lo_r, blo_), min(hi_r, bhi_)
            x = int(base[p])
            if lo_r > hi_r:
                verdict, val, why = (
                    "BLOCKED",
                    (lo_r if x < lo_r else hi_r if x > hi_r else x),
                    f"admissible range empty ({lo_r} > {hi_r})",
                )
            elif lo <= x <= hi:
                verdict, val, why = "KEEP", x, f"current {x} inside [{lo_r}, {hi_r}]"
            else:
                val = lo_r if x < lo else hi_r
                verdict, why = (
                    "CHANGE",
                    (
                        f"current {x} below the bound {lo_r} ({blo})"
                        if x < lo
                        else f"current {x} above the bound {hi_r} ({bhi})"
                    ),
                )
            chosen[p] = val
            info[p] = {
                "lo": G3.fmt(lo),
                "hi": G3.fmt(hi),
                "lo_step": lo_r,
                "hi_step": hi_r,
                "binding_lo": blo,
                "binding_hi": bhi,
                "verdict": verdict,
                "reason": why,
            }
            ps = ps.replace({p: val})
        rec_set = ps
        v_cur, c_cur = g9_values(base, ctx)
        v_new, c_new = g9_values(rec_set, ctx)
        notes_all = design_notes(results, policy)
        meta = cur.metrics.meta
        out = evidence_dir(meta.get("out_dir"), GROUP)
        evidence: list[Path] = []
        try:
            evidence = write_evidence(results, base, ctx, out)
        except Exception as e:  # pragma: no cover
            info["_evidence_error"] = {"error": f"{type(e).__name__}: {e}"}
        keys = KEY_METRICS
        recs = []
        for p in self.params:
            i = info[p]
            prov = provenance_for(p, ctx)
            klass = REGISTRY[p].change_path
            notes = [f"{i['reason']}."]
            if klass == "patch-release":
                notes.append("Excluded parameter: a change is a patch release, not a new parameter set.")
            if p == "maxMint" and ctx.volume_p10_usd is None:
                notes.append(
                    f"No volume data: the current maxMint needs a p10 daily YEC volume of at least "
                    f"${v_cur['maxMint.volume_needed_usd']:,.0f} under max_depth_fraction "
                    f"{ctx.max_depth_fraction:.0%}; load a depth CSV or set yec_daily_volume_p10_usd."
                )
            ks = keys.get(p, ())
            binding = (
                f"lower bound {i['lo_step']} ({i['binding_lo']})"
                if chosen[p] == i["lo_step"]
                else f"upper bound {i['hi_step']} ({i['binding_hi']})"
                if chosen[p] == i["hi_step"]
                else f"inside [{i['lo_step']}, {i['hi_step']}]: verification ({i['binding_lo']} / "
                     f"{i['binding_hi']})"
            )
            recs.append(
                Recommendation(
                    param=p,
                    current=base[p],
                    recommended=chosen[p],
                    verdict=final_verdict(i["verdict"], prov),  # type: ignore[arg-type]
                    rule=RULES[p],
                    binding=binding,
                    metrics={
                        "primary": f"{p}_ok",
                        "range": {
                            k: i[k] for k in ("lo", "hi", "lo_step", "hi_step", "binding_lo", "binding_hi")
                        },
                        "current": {k: G3.fmt(v_cur.get(k)) for k in ks},
                        "recommended": {k: G3.fmt(v_new.get(k)) for k in ks},
                        "constraints_current": {f"{p}_ok": c_cur[f"{p}_ok"]},
                        "constraints_recommended": {f"{p}_ok": c_new[f"{p}_ok"]},
                        "context": {
                            k: ctx.to_dict()[k]
                            for k in (
                                "p_ref_microusd",
                                "p_ref_source",
                                "worst_microusd",
                                "volume_p10_usd",
                                "volume_source",
                            )
                        },
                        "design_notes": [n for n in notes_all if p in n["params"]],
                    },
                    sensitivity=sensitivity_for(results, p),
                    confidence=G3.confidence_for(prov, i["verdict"], str(meta.get("budget", "quick"))),
                    provenance=prov,
                    evidence=list(evidence),
                    group=GROUP,
                    notes=notes,
                )
            )
        return recs

    def explain(self, rec: Recommendation, results: ResultTable) -> str:
        r = rec.metrics.get("range", {})
        extra = [
            f"Admissible range: [{r.get('lo_step')}, {r.get('hi_step')}] (lower: {r.get('binding_lo')}; "
            f"upper: {r.get('binding_hi')})."
        ]
        if rec.param == "minMint":
            c = rec.metrics.get("current", {})
            extra.append(
                "FEE-1 is a fixed share of the collateral above the feeMin floor, so the round-trip fee is "
                f"{c.get('prop_share.A@ref', 0):.2%} / {c.get('prop_share.B@ref', 0):.2%} / "
                f"{c.get('prop_share.C@ref', 0):.2%} of the debt for A/B/C at every size: minMint cannot "
                "bring class A under max_fee_share_small (design note G9-DN1)."
            )
        return G3.compose_explanation(
            rec,
            what=WHAT[rec.param],
            key_metrics=EXPLAIN_METRICS.get(rec.param, ()),
            extra=extra + [n for n in rec.notes[1:] if n.startswith("No volume")],
        )


KEY_METRICS: dict[str, tuple[str, ...]] = {
    "minMint": (
        "fee_share.A@ref",
        "fee_share.B@ref",
        "fee_share.C@ref",
        "prop_share.A@ref",
        "prop_share.B@ref",
        "prop_share.C@ref",
        "fee_share.C@worst",
        "fee.floor_binds_above_usd",
        "minMint.lo",
        "minMint.hi",
    ),
    "maxMint": (
        "maxMint.liquidation_usd",
        "maxMint.volume_needed_usd",
        "maxMint.liquidation_share",
        "maxMint.lo",
        "maxMint.hi",
    ),
    "minOutput": ("minOutput.lo", "minOutput.hi"),
    "maxOutput": ("maxOutput.lo", "maxOutput.hi"),
    "residualMinZat": ("residual.usd_at_worst", "residualMinZat.lo", "residualMinZat.hi"),
    "carrierValue": ("carrier.usd_at_worst", "carrierValue.lo", "carrierValue.hi"),
    "walletConfirmations": ("reorg.p_catch_up", "walletConfirmations.lo"),
    "DEFAULT_REF_LAG": ("void.p_natural", "void.p_attacker", "DEFAULT_REF_LAG.lo", "DEFAULT_REF_LAG.hi"),
}
EXPLAIN_METRICS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "minMint": (
        ("fee share A", "fee_share.A@ref", "{:.2%}"),
        ("B", "fee_share.B@ref", "{:.2%}"),
        ("C", "fee_share.C@ref", "{:.2%}"),
        ("floor binds above $/YEC", "fee.floor_binds_above_usd", "{:.2f}"),
    ),
    "maxMint": (
        ("liquidation at threshold ($)", "maxMint.liquidation_usd", "{:,.0f}"),
        ("p10 volume needed ($/day)", "maxMint.volume_needed_usd", "{:,.0f}"),
    ),
    "residualMinZat": (("value at worst price ($)", "residual.usd_at_worst", "{:.3f}"),),
    "carrierValue": (("value at worst price ($)", "carrier.usd_at_worst", "{:.3f}"),),
    "walletConfirmations": (("attacker catch-up probability", "reorg.p_catch_up", "{:.2e}"),),
    "DEFAULT_REF_LAG": (
        ("P(VOID) natural", "void.p_natural", "{:.1e}"),
        ("P(VOID) by the policy attacker", "void.p_attacker", "{:.2f}"),
    ),
}
WHAT = {
    "minMint": "the smallest YED amount one MINT may create (MINT-2)",
    "maxMint": "the largest YED amount one MINT may create (MINT-2)",
    "minOutput": "the smallest YED output a transfer or redemption may carry (XFER-1, RED-1)",
    "maxOutput": "the largest YED output (XFER-1, RED-1)",
    "residualMinZat": "the smallest collateral residual a claim must return to the owner (RED-5)",
    "carrierValue": "the value of the P2SH bundle-carrier output a wallet creates (wallet policy)",
    "walletConfirmations": "confirmations the wallet waits before treating a Yellowback transaction as final "
    "(wallet policy)",
    "DEFAULT_REF_LAG": "how many blocks behind the tip the wallet picks a mint's refHeight "
    "(-yellowbackmintlag); a mint survives reorgs shallower than the lag",
}


def sensitivity_for(results: ResultTable, p: str) -> dict:
    metric = {
        "minMint": "fee_share.A@worst",
        "walletConfirmations": "reorg.p_catch_up",
        "DEFAULT_REF_LAG": "void.p_natural",
        "maxMint": "maxMint.volume_needed_usd",
    }.get(p)
    if metric is None:
        return {"sentence": f"{p} is verified against closed-form bounds; no metric sweep."}
    sub = results.filter(lambda r: set(r.delta) <= {p})
    return G3.oat_sensitivity(sub, p, metric, metric) or {"sentence": f"{p}: no sweep."}


def design_notes(results: ResultTable, policy: Any | None = None) -> list[dict[str, Any]]:
    cur = results.current()
    if cur is None:
        return []
    v = cur.metrics.values
    tol = float(policy.max_fee_share_small) if policy is not None else 0.02
    notes = []
    a = v.get("fee_share.A.any_size", math.nan)
    if a > tol:
        notes.append(
            G3.design_note(
                "G9-DN1",
                "FEE-1 on the collateral makes the fee share a function of the class ratio",
                f"Round-trip FEE-1 is {a:.2%} of the debt for class A at any size (2 · feeBps · ratio), "
                "above "
                f"max_fee_share_small {tol:.0%}; B {v.get('prop_share.B@ref', math.nan):.2%}, C "
                f"{v.get('prop_share.C@ref', math.nan):.2%}.",
                evidence={
                    k: G3.fmt(v.get(k))
                    for k in ("fee_share.A.any_size", "prop_share.B@ref", "prop_share.C@ref")
                },
                consequence="No minMint fixes it; raising class A's ratio (G3) makes it worse.",
                fix="G6 (feeBps) or a rule change: charge FEE-1 on the debt instead of the collateral.",
                params=("minMint", "baseRatioBps[0]"),
            )
        )
    notes.append(
        G3.design_note(
            "G9-DN2",
            "feeMin is denominated in YEC",
            f"Above ${v.get('fee.floor_binds_above_usd', math.nan):.2f}/YEC the 0.5-YEC floor, not feeBps, "
            "sets a "
            f"minMint vault's fee; at the policy's worst price the round-trip share of a minMint debt is "
            f"{v.get('fee_share.C@worst', math.nan):.0%} (class C).",
            evidence={k: G3.fmt(v.get(k)) for k in ("fee.floor_binds_above_usd", "fee_share.C@worst")},
            consequence="minMint (cents) and feeMin (zat) drift apart as the YEC price moves; a USD-stable "
                "floor needs "
            "a new parameter set after large price moves.",
            fix="Rule change (feeMin in cents at pMint) or periodic re-sets.",
            params=("minMint",),
        )
    )
    return notes


def write_evidence(results: ResultTable, base: ParamSet, ctx: G9Context, out: Path) -> list[Path]:
    paths = [results.to_csv(out / "g9_results.csv")]
    from ybcal.sim import fees as F

    sizes = [1_000, 2_500, 5_000, 10_000, 25_000, 100_000, 1_000_000]
    p = out / "g9_fee_table.csv"
    with p.open("w") as fh:
        fh.write(
            "class,cents,price_microusd,collateral_zat,floor_binds,round_trip_fee_usd,fee_share_of_debt\n"
        )
        for c, n in enumerate(G3.CLASS_NAMES):
            for price in (ctx.p_ref_microusd, ctx.worst_microusd):
                for r in F.fee_table(base, sizes, price, term_class=c):
                    fh.write(
                        f"{n},{r['cents']},{price},{r['collateral_zat']},{int(r['floor_binds'])},"
                        f"{r['round_trip_fee_usd']},{r['fee_share_of_debt']}\n"
                    )
    paths.append(p)
    plt = plot_style()
    if plt is None:  # pragma: no cover
        return paths
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 3.4))
    prices = np.geomspace(0.05, max(100.0, ctx.worst_microusd / 1e6), 60)
    for c, (n, col) in enumerate(zip(G3.CLASS_NAMES, (C_CUR, C_REC, C_TEXT), strict=True)):
        sh = [
            F.fee_table(base, [int(base["minMint"])], round(pp * 1e6), term_class=c)[0]["fee_share_of_debt"]
            for pp in prices
        ]
        a1.plot(prices, sh, color=col, label=f"class {n}")
    a1.axhline(ctx.max_fee_share_small, color=C_TRUE, ls="--", lw=1)
    a1.axvline(ctx.p_ref_microusd / 1e6, color=C_TRUE, ls=":", lw=1)
    a1.set_xscale("log")
    a1.set_yscale("log")
    a1.set_xlabel("YEC price ($)")
    a1.set_ylabel("round-trip fee / debt")
    a1.set_title(
        f"minMint ${int(base['minMint']) / 100:.0f} vault: fee share vs price", loc="left", fontsize=9
    )
    a1.legend(fontsize=7)
    zs = np.arange(1, 41)
    for q, col in ((0.1, C_CUR), (0.2, "#5b9be0"), (ctx.reorg_q, C_REC), (0.4, C_TEXT)):
        a2.plot(zs, [max(nakamoto_catch_up(q, int(z)), 1e-12) for z in zs], color=col, label=f"q = {q:g}")
    a2.axhline(ctx.max_reorg_prob, color=C_TRUE, ls="--", lw=1)
    a2.axvline(int(base["walletConfirmations"]), color=C_TRUE, ls=":", lw=1)
    a2.set_yscale("log")
    a2.set_xlabel("confirmations z")
    a2.set_ylabel("attacker catch-up probability")
    a2.set_title("Reorg risk vs walletConfirmations (dotted = current)", loc="left", fontsize=9)
    a2.legend(fontsize=7)
    fig.tight_layout()
    f = out / "g9_fees_and_reorgs.png"
    fig.savefig(f, dpi=120)
    plt.close(fig)
    paths.append(f)
    return paths


def make_study() -> G9Study:
    """The G9 study (``load_study("G9")``)."""
    return G9Study()


__all__ = [
    "G9Context",
    "G9Study",
    "design_notes",
    "fee_shares",
    "g9_values",
    "make_study",
    "min_confirmations",
    "nakamoto_catch_up",
    "p_void_natural",
    "ranges",
]

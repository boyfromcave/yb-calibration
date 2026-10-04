"""G4 — grace and abandonment: grace, abandonBlocks (PLAN §5.4).

Owner: WP-7b. Method, metric definitions and decision rules: ``docs/studies/g4.md``.

Grace
-----
Two forces pull in opposite directions (fact 1.5-1: the claim path opens only at ``lock + grace``):

* **Owner continuity** — P(an honest owner is away for the whole window ``[lockHeight, lockHeight +
  grace]``), WP-4's analytic :func:`ybcal.sim.agents.p_owner_miss` under the policy's log-normal
  absence model (lost keys are reported separately: no grace helps them).
* **Extra drawdown exposure** — the increment of P(bad debt) from testing the vault at
  ``lock + grace`` instead of ``lock``, per class, from WP-4's fast path on G3's hour ensemble (one
  call per member with every grace of the grid), aggregated over members with ``ensemble_agg``.

Rule: minimise ``J = w_owner · P(miss) + w_debt · ΔP̄`` (ΔP̄ = the class increments weighted by the
default demand mix 0.4 / 0.4 / 0.2) over a one-day (quick: three-day) lattice, subject to
P(miss) ≤ ``max_owner_miss_prob``; materiality, ties to current. The debt side has no tolerance of its
own — the policy's bad-debt tolerance is on the *total* P(bad debt), which G3 meets through the ratios
— so ΔP enters through J, and "ΔP_c ≤ max_bad_debt_prob[c]" (the grace window alone using up a
class's whole budget) is reported as a flag and a design note, not a constraint (D-WP7b-4). BLOCKED
(least-violating = smallest P(miss)) only if no grace in bounds meets the owner tolerance.

Abandonment
-----------
Lower bound = max(grace, runbook) with runbook = detect (≤ signalWindow) + signalWindow (W19) + the
M14 lead (16,128) + ``runbook_operator_buffer_blocks`` (+ ``dev_absence_tolerance_days`` when the
policy sets it). Rule: the smallest ``abandonBlocks`` ≥ that bound (with the **recommended** grace)
whose false-abandonment probability (G5's :func:`false_abandon_probability` over G5's share samples)
is ≤ ``max_false_abandon_prob``, rounded up to whole days; the current value is kept unless it
violates the bound or the rule value is more than ``materiality`` smaller. The dev-absence variants
(14 … 180 days) are scored analytically: whether abandonment fires before returning developers can
finish the runbook, and how long vaults stay unsweepable when they never return.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ybcal.params.paramset import ParamSet
from ybcal.params.registry import REGISTRY, params_for_group
from ybcal.sim import agents as AG
from ybcal.studies import g3_collateral as G3
from ybcal.studies import g5_activation as G5
from ybcal.studies.base import Budget, Env, Metrics, Recommendation, ResultTable, final_verdict
from ybcal.studies.g1_price_windows import C_CUR, C_REC, C_TEXT, C_TRUE, evidence_dir, out_dir_of, plot_style
from ybcal.units import BLOCKS_PER_DAY

OWNER_WP = "WP-7b"

GROUP = "G4"
#: Demand mix used to weight the class increments (``MinterConfig.class_weights`` default).
CLASS_WEIGHTS = AG.MinterConfig().class_weights
#: dev-absence variants of ``scenarios/dev-absence.toml`` (days the developers are away).
DEV_ABSENCE_DAYS = (14, 30, 60, 90, 180)


def grace_step(budget: Budget) -> int:
    """Grace lattice step: three days in quick, one day otherwise."""
    return 3 * BLOCKS_PER_DAY if budget.name == "quick" else BLOCKS_PER_DAY


def grace_grid(base: int, budget: Budget) -> list[int]:
    return G3.lattice(int(base), "grace", step=grace_step(budget))


def owner_cfg(policy: Any, *, lost: bool = False) -> AG.OwnerConfig:
    return AG.OwnerConfig(
        absence_rate_per_year=float(policy.owner_absence_rate_per_year),
        absence_median_days=float(policy.owner_absence_median_days),
        absence_sigma=float(policy.owner_absence_sigma),
        lost_key_prob=float(getattr(policy, "lost_key_prob", 0.0)) if lost else 0.0,
    )


def runbook_blocks(params: Mapping, policy: Any) -> int:
    """detect (≤ signalWindow) + signalWindow (W19) + M14 lead + operator buffer (+ tolerated dev absence)."""
    W = int(params["signalWindow"])
    tol = float(getattr(policy, "dev_absence_tolerance_days", 0.0) or 0.0)
    return (
        W
        + W
        + int(policy.release_lead_blocks)
        + int(policy.runbook_operator_buffer_blocks)
        + math.ceil(tol * BLOCKS_PER_DAY)
    )


def lost_share_fraction(params: Mapping, shares: Any) -> float:
    """Share of the enforcing-share samples at or below ``enforcementResume / signalWindow`` (+1 σ of
    the window count): there an ENFORCEMENT episode, once started, does not end on its own, so no
    ``abandonBlocks`` prevents abandonment — the module has really lost its enforcing majority."""
    W = int(params["signalWindow"])
    er = int(params["enforcementResume"]) / W
    s = np.atleast_1d(np.asarray(shares, dtype=float))
    sd = np.sqrt(np.maximum(s * (1 - s), 1e-12) / W)
    return float(np.mean(s - sd <= er)) if s.size else 0.0


def ceil_days(blocks: int) -> int:
    return -(-int(blocks) // BLOCKS_PER_DAY) * BLOCKS_PER_DAY


def dev_absence(A: int, params: Mapping, policy: Any) -> dict[str, float]:
    """Analytic dev-absence costs at ``abandonBlocks = A`` (ENFORCEMENT halted at the incident; the
    halt is what ``abandoned()`` counts). Returning developers need ``max(0, W − absence)`` more halted
    blocks (W19) plus the M14 lead and the operator buffer before a fixed set can start; abandonment
    fires ``A`` blocks after the halt. Returns, per variant, whether it fires prematurely, the largest
    tolerated absence and the unsweepable days when the developers never return."""
    W = int(params["signalWindow"])
    tail = int(policy.release_lead_blocks) + int(policy.runbook_operator_buffer_blocks)
    out: dict[str, float] = {}
    for d in DEV_ABSENCE_DAYS[:-1]:
        db = d * BLOCKS_PER_DAY
        fix = db + max(0, W - db) + tail
        out[f"dev.premature_{d}d"] = float(fix > A)
    out["dev.max_absence_days"] = (A - tail) / BLOCKS_PER_DAY if W + tail <= A else -1.0
    out[f"dev.unsweepable_days_{DEV_ABSENCE_DAYS[-1]}d"] = A / BLOCKS_PER_DAY
    return out


@dataclass
class G4Study:
    """Grace and abandonment (PLAN §5.4)."""

    group: str = GROUP
    params: tuple[str, ...] = field(default_factory=lambda: params_for_group(GROUP))

    # -- space -----------------------------------------------------------------------------------
    def space(self, base: ParamSet, budget: Budget) -> Iterable[ParamSet]:
        out = [base]
        A0 = int(base["abandonBlocks"])
        for g in grace_grid(int(base["grace"]), budget):
            if g != int(base["grace"]):
                out.append(base.replace(grace=g, abandonBlocks=max(A0, g)))
        step = 2 * BLOCKS_PER_DAY if budget.name == "quick" else BLOCKS_PER_DAY
        for a in G3.lattice(A0, "abandonBlocks", step=step, hi=3 * A0):
            if a != A0 and a >= int(base["grace"]):
                out.append(base.replace(abandonBlocks=a))
        return [ps for i, ps in enumerate(out) if i == 0 or G3.valid(ps)]

    # -- evaluate --------------------------------------------------------------------------------
    def evaluate(self, cand: ParamSet, env: Env) -> Metrics:
        pol = env.policy
        how = str(G3.pget(pol, "ensemble_agg"))
        b = env.budget
        g = int(cand["grace"])
        A = int(cand["abandonBlocks"])
        ens = G3.ensemble(env, cand)
        grid = sorted(set(grace_grid(g, b)) | {g})
        ref = cand.replace(grace=grid[0], abandonBlocks=max(A, grid[0]))
        values: dict[str, float] = {"zero": 0.0, "grace.days": g / BLOCKS_PER_DAY}
        cons: dict[str, bool] = {}
        dbar = 0.0
        for c, name in enumerate(G3.CLASS_NAMES):
            inc, tot = [], []
            for m in ens.names:
                fb = G3.bad_debt(
                    ens,
                    m,
                    ref,
                    c,
                    sigma=G3.member_sigma(ens, m, pol.sigma_mult_at, G3.warmup_hours(ref)),
                    term_distribution=pol.term_distribution,
                    n_terms=G3.terms_per_class(b),
                    stride=G3.start_stride_hours(b),
                    graces=grid,
                )
                pg = fb.by_grace[name].get(g, math.nan)
                inc.append(pg - fb.p_lock[name])
                tot.append(pg)
                values[f"dP.{name}.{m}"] = inc[-1]
            dp = G3.agg(inc, how)
            values[f"dP.{name}"] = dp
            values[f"pbad.{name}"] = G3.agg(tot, how)
            dbar += CLASS_WEIGHTS[c] * (dp if np.isfinite(dp) else 0.0)
            tol = float(pol.max_bad_debt(name))
            ok = bool(not np.isfinite(dp) or dp <= tol)
            cons[f"grace_debt_{name}"] = ok
            values[f"viol.grace_debt_{name}"] = 0.0 if ok else dp / tol - 1
        values["dP.weighted"] = dbar
        pm = AG.p_owner_miss(g, owner_cfg(pol))
        values["owner.p_miss"] = pm
        values["owner.p_miss_with_lost_keys"] = AG.p_owner_miss(g, owner_cfg(pol, lost=True))
        values["J"] = float(pol.w_owner) * pm + float(pol.w_debt) * dbar
        mx = float(pol.max_owner_miss_prob)
        cons["owner_miss"] = pm <= mx
        values["viol.owner_miss"] = 0.0 if pm <= mx else pm / mx - 1
        # abandonment
        W = int(cand["signalWindow"])
        S, sprov = G5.share_samples(env, W)
        rb = runbook_blocks(cand, pol)
        lb = max(g, rb)
        fa = G5.false_abandon_probability(cand, S, A)
        values.update(
            {
                "abandon.days": A / BLOCKS_PER_DAY,
                "abandon.runbook": float(rb),
                "abandon.lb": float(lb),
                "abandon.margin": float(A - lb),
                "abandon.false_prob": fa,
                "abandon.false_prob_at_60pct": G5.false_abandon_probability(cand, 0.60, A),
            }
        )
        values.update(dev_absence(A, cand, pol))
        cons["abandon_lb"] = lb <= A
        cons["false_abandon"] = fa <= float(pol.max_false_abandon_prob)
        values["viol.abandon_lb"] = 0.0 if lb <= A else (lb - A) / lb
        values["viol.false_abandon"] = (
            0.0 if cons["false_abandon"] else fa / float(pol.max_false_abandon_prob) - 1
        )
        meta = {
            "seed": env.seed,
            "budget": b.name,
            "out_dir": out_dir_of(env),
            "members": list(ens.names),
            "provenance": ens.provenance,
            "share_provenance": sprov,
            "shares": [float(x) for x in S],
            "agg": how,
            "grace_grid": grid,
        }
        return Metrics(values, "J", True, cons, ens.provenance, meta)  # type: ignore[arg-type]

    # -- decide ----------------------------------------------------------------------------------
    def decide(self, results: ResultTable, policy: Any) -> list[Recommendation]:
        cur = results.current()
        if cur is None:
            raise ValueError("G4: the current set was not evaluated")
        meta = dict(cur.metrics.meta)
        base = results.base
        grule = G3.Rule(
            "grace",
            ("grace",),
            ("grace", "abandonBlocks"),
            GRACE_RULE,
            primary="J",
            constraints=("owner_miss",),
            report=(
                "J",
                "owner.p_miss",
                "owner.p_miss_with_lost_keys",
                "dP.weighted",
                "dP.A",
                "dP.B",
                "dP.C",
                "pbad.A",
                "pbad.B",
                "pbad.C",
            ),
            sens_metric="J",
        )
        gsub = G3.rule_table(
            results.filter(
                lambda r: set(r.delta) <= {"grace", "abandonBlocks"} and ("grace" in r.delta or not r.delta)
            ),
            grule,
        )
        gd = G3.decide_rule(gsub, grule, policy)
        g_rec = int(gd.row.params["grace"])
        # abandonment: closed-form rule at the recommended grace
        shares = np.asarray(meta.get("shares", [policy.expected_enforcing_share]), dtype=float)
        rb = runbook_blocks(base, policy)
        lb = max(g_rec, rb)
        a_rule = ceil_days(lb)
        hi = REGISTRY["abandonBlocks"].bounds[1]
        while (
            G5.false_abandon_probability(base, shares, a_rule) > float(policy.max_false_abandon_prob)
            and a_rule + BLOCKS_PER_DAY <= hi
        ):
            a_rule += BLOCKS_PER_DAY
        a_cur = int(base["abandonBlocks"])
        fa_cur = G5.false_abandon_probability(base, shares, a_cur)
        cur_ok = a_cur >= lb and fa_cur <= float(policy.max_false_abandon_prob)
        rel = (a_cur - a_rule) / a_cur if a_cur else 0.0
        fa_rule = G5.false_abandon_probability(base, shares, a_rule)
        lost = lost_share_fraction(base, shares)
        if a_cur >= lb and fa_rule > float(policy.max_false_abandon_prob):
            # D-RD-COL-8: no abandonBlocks within the bounds meets the tolerance; on real pool data
            # the excess comes from share samples at or below the resume
            # threshold, where an ENFORCEMENT episode never ends — enforcement genuinely lost, which
            # abandonment is meant to detect, not variance of an enforcing majority. Moving to the
            # registry ceiling would be a CHANGE that still violates; keep the current value.
            a_rec, a_verdict, a_reason = (
                a_cur,
                "BLOCKED",
                (
                    f"no value up to {a_rule} meets false-abandon (P {fa_rule:.2g}/yr at {a_rule}, "
                    f"{fa_cur:.2g}/yr at {a_cur}): {lost:.0%} of the enforcing-share samples sit at or "
                    "below enforcementResume, where an episode never ends whatever abandonBlocks is"
                ),
            )
        elif not cur_ok:
            a_rec, a_verdict, a_reason = (
                a_rule,
                "CHANGE",
                (
                    f"current {a_cur} violates the lower bound {lb}"
                    if a_cur < lb
                    else "current violates false-abandon"
                ),
            )
        elif rel > float(policy.materiality):
            a_rec, a_verdict, a_reason = (
                a_rule,
                "CHANGE",
                (f"rule value {a_rule} is {rel:.0%} below current (> materiality {policy.materiality:.0%})"),
            )
        else:
            a_rec, a_verdict, a_reason = (
                a_cur,
                "KEEP",
                (
                    "current equals the rule value"
                    if a_rule == a_cur
                    else f"rule value {a_rule} within materiality of current ({rel:.0%} ≤ "
                         f"{policy.materiality:.0%})"
                ),
            )
        a_vals = {
            "abandon.lb": float(lb),
            "abandon.runbook": float(rb),
            "abandon.rule_value": float(a_rule),
            "abandon.margin": float(a_rec - lb),
            "abandon.false_prob": G5.false_abandon_probability(base, shares, a_rec),
            "abandon.days": a_rec / BLOCKS_PER_DAY,
            **dev_absence(a_rec, base, policy),
        }
        a_cur_vals = {
            "abandon.lb": float(max(int(base["grace"]), rb)),
            "abandon.runbook": float(rb),
            "abandon.rule_value": float(a_rule),
            "abandon.margin": float(a_cur - max(int(base["grace"]), rb)),
            "abandon.false_prob": fa_cur,
            "abandon.days": a_cur / BLOCKS_PER_DAY,
            **dev_absence(a_cur, base, policy),
        }
        notes_all = design_notes(results, policy)
        out = evidence_dir(meta.get("out_dir"), GROUP)
        evidence: list[Path] = []
        try:
            evidence = write_evidence(results, gsub, g_rec, a_rec, a_vals, out, policy)
        except Exception as e:  # pragma: no cover
            meta["evidence_error"] = f"{type(e).__name__}: {e}"
        prov = str(meta.get("provenance", "synthetic"))
        keys = ("J", *grule.report[1:])
        gcur = gsub.current()
        assert gcur is not None
        g_verdict = (
            gd.verdict if gd.verdict == "BLOCKED" else ("CHANGE" if g_rec != int(base["grace"]) else "KEEP")
        )
        recs = [
            Recommendation(
                param="grace",
                current=base["grace"],
                recommended=g_rec,
                verdict=final_verdict(g_verdict, prov),  # type: ignore[arg-type]
                rule=GRACE_RULE,
                binding=G3.binding_text(grule, gcur, gd, policy),
                metrics={
                    "primary": "J",
                    "current": {k: G3.fmt(gcur.metrics.values.get(k)) for k in keys},
                    "recommended": {k: G3.fmt(gd.row.metrics.values.get(k)) for k in keys},
                    "constraints_current": dict(gcur.metrics.constraints),
                    "constraints_recommended": dict(gd.row.metrics.constraints),
                    "decision": gd.reason,
                    "flags": {
                        f"grace_debt_{c}": bool(
                            gd.row.metrics.values.get(f"dP.{c}", 0.0) <= float(policy.max_bad_debt(c))
                        )
                        for c in G3.CLASS_NAMES
                    },
                    "improvement": G3.fmt(gd.improvement),
                    "least_violating": (
                        {
                            "delta": dict(gd.least.delta),
                            "values": {k: G3.fmt(gd.least.metrics.values.get(k)) for k in keys},
                        }
                        if gd.least is not None
                        else None
                    ),
                    "curve": [
                        (
                            int(r.params["grace"]) // BLOCKS_PER_DAY,
                            G3.fmt(r.metrics.values["owner.p_miss"]),
                            G3.fmt(r.metrics.values["dP.weighted"]),
                            G3.fmt(r.metrics.values["J"]),
                        )
                        for r in sorted(gsub.rows, key=lambda r: int(r.params["grace"]))
                    ],
                    "design_notes": [n for n in notes_all if "grace" in n["params"]],
                },
                sensitivity=G3.oat_sensitivity(gsub, "grace", "J", "the grace objective J")
                or {"sentence": "grace: too few points for a sensitivity sentence."},
                confidence=G3.confidence_for(prov, g_verdict, str(meta.get("budget", "quick"))),
                provenance=prov,
                evidence=list(evidence),
                group=GROUP,
                notes=[f"Rule 'grace': {gd.reason}."],
            ),
        ]
        sprov = str(meta.get("share_provenance", "judgement"))
        aprov = "real-data" if sprov == "real-data" else "judgement"
        if g_rec != int(base["grace"]) and prov != "real-data":
            aprov = prov  # the bound moved with a grace recommendation that rests on `prov` data
        a_notes = [
            f"Rule 'abandon': {a_reason}.",
            f"Runbook = signalWindow (detect) + signalWindow (W19) + {policy.release_lead_blocks} (M14) + "
            f"{policy.runbook_operator_buffer_blocks} (buffer) = {rb} blocks; lower bound max(grace "
            f"{g_rec}, runbook) = {lb}.",
        ]
        if g_rec != int(base["grace"]):
            a_notes.append(f"Evaluated at the recommended grace {g_rec} (W21: abandonBlocks ≥ grace).")
        recs.append(
            Recommendation(
                param="abandonBlocks",
                current=a_cur,
                recommended=a_rec,
                verdict=final_verdict(a_verdict, aprov),  # type: ignore[arg-type]
                rule=ABANDON_RULE,
                binding=(
                    "lower bound max(grace, runbook) = "
                    + str(lb)
                    + (" (grace binds)" if g_rec >= rb else " (runbook binds)")
                ),
                metrics={
                    "primary": "abandon.days",
                    "current": {k: G3.fmt(v) for k, v in a_cur_vals.items()},
                    "recommended": {k: G3.fmt(v) for k, v in a_vals.items()},
                    "constraints_current": {
                        "abandon_lb": a_cur >= lb,
                        "false_abandon": fa_cur <= float(policy.max_false_abandon_prob),
                    },
                    "decision": a_reason,
                    "design_notes": [n for n in notes_all if "abandonBlocks" in n["params"]],
                },
                sensitivity={
                    "sentence": (
                        f"P(false abandonment) is {a_vals['abandon.false_prob']:.1e}/yr at the recommended "
                        "value and "
                        "falls geometrically with each extra signal window, so the binding constraint is the "
                        "runbook / "
                        "grace lower bound, not false abandonment; every extra day of abandonBlocks is one "
                        "more day "
                        "vaults stay unsweepable if the developers are truly gone."
                    )
                },
                confidence=G3.confidence_for(aprov, a_verdict, str(meta.get("budget", "quick"))),
                provenance=aprov,
                evidence=list(evidence),
                group=GROUP,
                notes=a_notes,
            )
        )
        return recs

    def explain(self, rec: Recommendation, results: ResultTable) -> str:
        if rec.param == "grace":
            km = [
                ("J", "J", "{:.4f}"),
                ("P(owner miss)", "owner.p_miss", "{:.2%}"),
                ("ΔP(bad debt) A", "dP.A", "{:.2%}"),
                ("B", "dP.B", "{:.2%}"),
                ("C", "dP.C", "{:.2%}"),
            ]
            extra = []
            curve = rec.metrics.get("curve") or []
            if curve:
                pts = curve[:: max(1, len(curve) // 6)]
                extra.append(
                    "Trade-off (days → P(miss), weighted ΔP): "
                    + ", ".join(
                        f"{d} d → {pm:.2%}, {dp:.2%}"
                        for d, pm, dp, _ in pts
                        if isinstance(pm, float) and isinstance(dp, float)
                    )
                    + "."
                )
            lv = rec.metrics.get("least_violating")
            if lv and rec.verdict == "BLOCKED":
                extra.append(f"Least-violating grace: {lv['delta'].get('grace', rec.current)} blocks.")
            flags = rec.metrics.get("flags", {})
            over = [c for c in G3.CLASS_NAMES if flags.get(f"grace_debt_{c}") is False]
            if over:
                extra.append(
                    "Flag: at the recommended grace the grace window alone adds more than the whole "
                    f"bad-debt tolerance of class {', '.join(over)} (ΔP_c > max_bad_debt_prob[c])."
                )
            what = (
                "blocks after lockHeight before anyone may take the claim path (MINT-2/3): the owner's "
                "window to redeem, and extra drawdown exposure the base ratio must cover"
            )
            return G3.compose_explanation(rec, what=what, key_metrics=km, extra=extra)
        km = [
            ("abandonBlocks (days)", "abandon.days", "{:.0f}"),
            ("lower bound", "abandon.lb", "{:.0f}"),
            ("margin (blocks)", "abandon.margin", "{:.0f}"),
            ("P(false abandon)/yr", "abandon.false_prob", "{:.1e}"),
            ("max tolerated dev absence (days)", "dev.max_absence_days", "{:.1f}"),
        ]
        extra = []
        new = rec.metrics.get("recommended", {})
        prem = [d for d in DEV_ABSENCE_DAYS[:-1] if new.get(f"dev.premature_{d}d")]
        extra.append(
            "Dev-absence variants: abandonment fires before returning developers can finish the "
            "freeze-then-fix runbook for "
            + (", ".join(f"{d} d" for d in prem) if prem else "none")
            + f"; if they never return, vaults stay unsweepable for {new.get('abandon.days')} days."
        )
        what = (
            "blocks of continuous ENFORCEMENT halt after which the module counts as abandoned and owners may "
            "sweep (L10/L12, TPL-1/2, MP-1, yed_sweep; W21 ≥ grace)"
        )
        return G3.compose_explanation(rec, what=what, key_metrics=km, extra=extra)


GRACE_RULE = (
    "grace = argmin of J = w_owner·P(owner miss) + w_debt·ΔP̄(bad debt from grace) on a one-day lattice "
    "(quick: "
    "three days; ΔP̄ = class increments of P(bad debt) from testing at lock + grace instead of lock, worst "
    "ensemble member, weighted 0.4/0.4/0.2), subject to P(owner miss) ≤ max_owner_miss_prob; KEEP unless J "
    "improves by more than materiality; BLOCKED (least-violating grace) when no grace meets the owner "
    "tolerance. ΔP_c > max_bad_debt_prob[c] is flagged, not constrained (D-WP7b-4)."
)
ABANDON_RULE = (
    "abandonBlocks = the smallest value ≥ max(grace, runbook) — runbook = signalWindow (detect) + "
    "signalWindow "
    "(W19) + 16,128 (M14) + runbook_operator_buffer_blocks — with P(false abandonment per year) ≤ "
    "max_false_abandon_prob at the expected enforcing share (G5 drift mixture), rounded up to whole days; "
    "KEEP "
    "unless the current value violates the bound or the rule value is more than materiality smaller."
)


def design_notes(results: ResultTable, policy: Any | None = None) -> list[dict[str, Any]]:
    """G4 rule-level findings."""
    cur = results.current()
    if cur is None or policy is None:
        return []
    v = cur.metrics.values
    A = int(results.base["abandonBlocks"])
    tail = int(policy.release_lead_blocks) + int(policy.runbook_operator_buffer_blocks)
    notes = [
        G3.design_note(
            "G4-DN1",
            "Abandonment tolerates only a short developer absence",
            f"With abandonBlocks {A} ({A // BLOCKS_PER_DAY} d), developers who return after more than "
            f"{(A - tail) / BLOCKS_PER_DAY:.0f} days cannot ship a freeze-then-fix set (M14 lead "
            f"{policy.release_lead_blocks} "
            f"+ buffer {policy.runbook_operator_buffer_blocks}) before owners may sweep: abandonment and a "
            "slow fix race.",
            evidence={"abandonBlocks": A, "max_absence_days": G3.fmt(v.get("dev.max_absence_days"))},
            consequence="A longer abandonBlocks protects slow developers but keeps vaults locked longer when "
                "they are "
            "truly gone; the parameter cannot serve both.",
            fix="Policy (set dev_absence_tolerance_days) or a rule change (an explicit developer liveness "
                "signal).",
            params=("abandonBlocks",),
        )
    ]
    dA = v.get("dP.A")
    if isinstance(dA, float) and math.isfinite(dA):
        notes.append(
            G3.design_note(
                "G4-DN2",
                "Grace is drawdown exposure for short terms",
                f"The grace window adds {dA:.2%} (A), {v.get('dP.B', math.nan):.2%} (B), "
                f"{v.get('dP.C', math.nan):.2%} (C) "
                "to P(bad debt) at the shipped values because nobody can claim during it (fact 1.5-1); for "
                "class A a "
                "30-day grace is 33–100 % of the term.",
                evidence={k: G3.fmt(v.get(k)) for k in ("dP.A", "dP.B", "dP.C", "owner.p_miss")},
                consequence="Owner continuity is bought with collateral (G3 must size ratios for lock + "
                    "grace).",
                fix="Rule change: allow RED-4 claims from lockHeight at a lower threshold while the owner "
                    "path stays open.",
                params=("grace",),
            )
        )
    return notes


def write_evidence(
    results: ResultTable,
    gsub: ResultTable,
    g_rec: int,
    a_rec: int,
    a_vals: Mapping[str, float],
    out: Path,
    policy: Any,
) -> list[Path]:
    paths = [results.to_csv(out / "g4_results.csv")]
    rows = sorted(gsub.rows, key=lambda r: int(r.params["grace"]))
    p = out / "g4_grace_curve.csv"
    with p.open("w") as fh:
        fh.write("grace_blocks,grace_days,p_owner_miss,dP_A,dP_B,dP_C,dP_weighted,J,feasible\n")
        for r in rows:
            v = r.metrics.values
            fh.write(
                f"{int(r.params['grace'])},{int(r.params['grace']) / BLOCKS_PER_DAY:g},{v['owner.p_miss']},"
                f"{v['dP.A']},{v['dP.B']},{v['dP.C']},{v['dP.weighted']},{v['J']},{int(r.metrics.feasible)}\n"
            )
    paths.append(p)
    plt = plot_style()
    if plt is None:  # pragma: no cover
        return paths
    days = [int(r.params["grace"]) / BLOCKS_PER_DAY for r in rows]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 3.5))
    a1.plot(days, [r.metrics.values["owner.p_miss"] for r in rows], color=C_CUR, label="P(owner miss)")
    for c, ls in zip(G3.CLASS_NAMES, ("-", "--", ":"), strict=True):
        a1.plot(
            days,
            [r.metrics.values[f"dP.{c}"] for r in rows],
            color=C_REC,
            ls=ls,
            lw=1.5,
            label=f"ΔP(bad debt) {c}",
        )
    a1.axhline(float(policy.max_owner_miss_prob), color=C_TRUE, ls="--", lw=1)
    a1.set_xlabel("grace (days)")
    a1.set_ylabel("probability")
    a1.set_title("Owner continuity vs drawdown exposure", loc="left", fontsize=9)
    a1.legend(fontsize=7)
    a2.plot(days, [r.metrics.values["J"] for r in rows], color=C_TEXT)
    a2.axvline(int(results.base["grace"]) / BLOCKS_PER_DAY, color=C_TRUE, ls=":", lw=1)
    a2.axvline(g_rec / BLOCKS_PER_DAY, color=C_REC, ls=":", lw=1.5)
    a2.set_xlabel("grace (days)")
    a2.set_title(
        "Objective J (grey = current, orange = chosen)", loc="left", fontsize=9
    )
    fig.tight_layout()
    f = out / "g4_grace_tradeoff.png"
    fig.savefig(f, dpi=120)
    plt.close(fig)
    paths.append(f)
    # abandonment budget
    fig, ax = plt.subplots(figsize=(7.2, 2.6))
    W = int(results.base["signalWindow"])
    parts = [
        ("detect", W),
        ("W19 window", W),
        ("M14 lead", int(policy.release_lead_blocks)),
        ("buffer", int(policy.runbook_operator_buffer_blocks)),
    ]
    x = 0.0
    for (label, blk), col in zip(parts, (C_CUR, "#5b9be0", C_TRUE, "#9a9893"), strict=True):
        ax.barh([1], [blk / BLOCKS_PER_DAY], left=[x], color=col)
        ax.text(x + blk / BLOCKS_PER_DAY / 2, 1, label, ha="center", va="center", fontsize=7, color="white")
        x += blk / BLOCKS_PER_DAY
    ax.barh([0], [a_rec / BLOCKS_PER_DAY], color=C_REC)
    ax.text(
        a_rec / BLOCKS_PER_DAY / 2,
        0,
        f"abandonBlocks {a_rec / BLOCKS_PER_DAY:.0f} d",
        ha="center",
        va="center",
        fontsize=7,
        color="white",
    )
    ax.axvline(g_rec / BLOCKS_PER_DAY, color=C_TEXT, ls="--", lw=1)
    ax.set_yticks([0, 1], ["chosen", "runbook"])
    ax.set_xlabel("days (dashed = grace)")
    ax.set_title(
        f"Freeze-then-fix runbook vs abandonment (max tolerated dev absence "
        f"{a_vals.get('dev.max_absence_days', math.nan):.0f} d)",
        loc="left",
        fontsize=9,
    )
    fig.tight_layout()
    f2 = out / "g4_abandonment.png"
    fig.savefig(f2, dpi=120)
    plt.close(fig)
    paths.append(f2)
    return paths


def make_study() -> G4Study:
    """The G4 study (``load_study("G4")``)."""
    return G4Study()


__all__ = [
    "ABANDON_RULE",
    "GRACE_RULE",
    "G4Study",
    "ceil_days",
    "design_notes",
    "dev_absence",
    "grace_grid",
    "make_study",
    "owner_cfg",
    "runbook_blocks",
]

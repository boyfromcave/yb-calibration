"""G5 — activation and enforcement: signalWindow, thresholds, floors, activationDelay, valveBlocks
(PLAN §5.5). Owner: WP-7c (moved from WP-7a).

Method
------
The signal count of a window is a sum of per-block draws: each block is mined by an enforcing miner
with probability equal to the enforcing hash share ``p``, independently (PoW), so at a constant share
the count is exactly ``Binomial(signalWindow, p)``. Every sweep metric is therefore **exact math
given the share** (``ybcal.sim.activation``'s binomial tails, the exact downcrossing rate, and an
exact upper bound on the detection-delay quantile). Share drift is folded in as a mixture: the
window-mean enforcing share is sampled from a ``HashrateDrift`` simulation (or from a pool-share
log when one is in ``env.data``) and the binomial metrics are averaged over those samples.

Simulation (``activation.simulate``, the exact ACT-1..6 state machine) confirms the analytic numbers
at the current and the recommended set inside :meth:`G5Study.decide`: steady state at the expected
share, a probe share near the enforcement floor, the detection delay of a drop and the
``hashrate-drop-45`` scenario.

Decision rules (one *family* of candidates per rule; see ``docs/studies/g5.md``)
-------------------------------------------------------------------------------
* ``signalWindow`` (thresholds scaled to keep their fractions), ``participationFloor``,
  ``enforcementFloor`` / ``enforcementResume``, ``activationThreshold``: minimise false-halt hours per
  year (resolution 0.01 h) subject to the false-halt, flapping and detection budgets of the policy,
  the §1.4 ordering, an enforcement majority (``enforcementFloor ≥ W/2``, L3) and reliable
  activation at the expected share. Detection speed is a constraint, not the objective: the ACT-7
  valve bounds the consequence of a minority-enforcement split (spec: "the node rejoins within six
  blocks"), whereas a false halt stops minting for every user.
* ``activationDelay``: the shortest delay ≥ the policy's operator upgrade window.
* ``valveBlocks`` (excluded, node-local → patch release): the shortest split (blocks to rejoin) with
  natural false trips ≤ the policy budget and a non-enforcing minority's trip probability per
  incident ≤ ``JUDGEMENT["valve_minority_trip_max"]``.
* Every family goes through ``decide_with_materiality`` (KEEP unless > ``materiality``; a violated
  constraint is never kept) and ``final_verdict``.

Reusable for G4: :func:`false_abandon_probability`.

The small *family* framework at the top (``Family``, ``FamilyStudy``) is shared with G8.
"""

from __future__ import annotations

import math
import tempfile
import zlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np

from ybcal.optimize.sensitivity import OATResult, sensitivity_sentence
from ybcal.params.paramset import ParamSet
from ybcal.params.registry import REGISTRY, derived_names, params_for_group
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
from ybcal.units import BLOCKS_PER_DAY, BLOCKS_PER_HOUR, BLOCKS_PER_YEAR

OWNER_WP = "WP-7c"

GROUP = "G5"

#: Judgement constants (no policy key yet; each is documented in docs/studies/g5.md and listed as a
#: requested policy field in docs/decisions.md D-WP7c-2).
JUDGEMENT: dict[str, float] = {
    "activation_reliability": 0.99,     # P(lock-in at the first eligible height) at the expected share
    # P(a non-enforcing minority carries a rejected branch valveBlocks ahead), per incident
    "valve_minority_trip_max": 0.01,
    # false-halt hours/yr below this are reported as 0 (no spurious relative "improvement")
    "false_halt_resolution_hours": 0.01,
    "detection_quantile": 0.95,         # quantile of the detection delay the policy bound applies to
    # P(a genuine-minority enforcer ends stuck at VALVE_NOTE_CAP instead of rejoining) (D-RD-ACT-5)
    "valve_capstuck_max": 0.05,
}

#: Number of equally weighted share samples (quantiles of the window-mean share distribution).
N_SHARE_SAMPLES = 100


# ===================================================================================================
# Shared family framework (also used by G8)


@dataclass(frozen=True)
class Family:
    """One decision rule over one slice of the candidate table.

    ``varies``: the params a row of this family may differ in (rows whose delta ⊆ ``varies`` plus
    derived names belong to it — the base row always does). ``owns``: the params it recommends.
    ``kind``: ``optimize`` (primary metric + materiality), ``verify`` (KEEP unless a constraint
    fails, then the nearest feasible value), ``rule`` (a ported closed-form target in metric
    ``target``; KEEP when the current value is within ``materiality`` of it).
    """

    name: str
    owns: tuple[str, ...]
    varies: tuple[str, ...]
    rule: str
    primary: str = "zero"
    minimize: bool = True
    constraints: tuple[str, ...] = ()
    kind: Literal["optimize", "verify", "rule"] = "optimize"
    report: tuple[str, ...] = ()           #: metrics echoed in the recommendation
    sens_metric: str | None = None          #: metric for the OAT sensitivity sentence
    target: str | None = None               #: rule families: metric holding the target value
    provenance: str = "judgement"           #: metric-independent provenance tag (overridden per row)
    provenance_key: str | None = None       #: Metrics.meta key with this family's provenance
    note: str = ""
    #: when no row satisfies the policy: (metric to minimise, constraints still required) — the
    #: least-harm value reported with verdict BLOCKED (wave 2, D-RD-ACT-5); None = keep current
    least_harm: tuple[str, tuple[str, ...]] | None = None
    #: a row other than the base belongs to the family only if this param moved in it (the window
    #: family varies the thresholds *with* the window, never alone; wave 2, D-RD-ACT-2)
    requires: str | None = None

    def report_metrics(self) -> tuple[str, ...]:
        out = [self.primary] if self.primary != "zero" else []
        out += [m for m in self.report if m not in out]
        return tuple(out)


_DERIVED = frozenset(derived_names())


def family_rows(table: ResultTable, fam: Family) -> list[ResultRow]:
    allowed = set(fam.varies) | _DERIVED
    return [r for r in table if set(r.delta) <= allowed
            and (fam.requires is None or not r.delta or fam.requires in r.delta)]


def family_table(table: ResultTable, fam: Family) -> ResultTable:
    """The family's rows with metrics re-projected onto its primary and constraints."""
    out = ResultTable(table.base)
    for r in family_rows(table, fam):
        m = r.metrics
        cons = {c: bool(m.constraints.get(c, True)) for c in fam.constraints}
        vals = dict(m.values)
        vals.setdefault("zero", 0.0)
        out.rows.append(ResultRow(r.delta, r.params, Metrics(vals, fam.primary, fam.minimize, cons,
                                                             m.provenance, m.meta)))
    return out


def out_dir_for(meta: Mapping[str, Any], group: str) -> Path:
    """Evidence directory ``<out_dir>/<group>/`` (``Env.out_dir`` or ``env.data["out_dir"]``, recorded
    in Metrics.meta by ``evaluate``), else a fresh temp dir — the same convention as G1/G2."""
    d = meta.get("out_dir")
    p = Path(d) / group.lower() if d else Path(tempfile.mkdtemp(prefix=f"ybcal-{group.lower()}-"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def env_out_dir(env: Env) -> str:
    for name in ("out_dir", "work_dir", "evidence_dir"):
        v = getattr(env, name, None)
        if v:
            return str(v)
    v = env.data.get("out_dir") if isinstance(env.data, Mapping) else None
    return str(v) if v else ""


def _fmt_metric(v: Any) -> Any:
    if isinstance(v, float):
        if math.isinf(v) or math.isnan(v):
            return v
        return float(f"{v:.6g}")
    return v


def oat_for(sub: ResultTable, param: str, metric: str) -> OATResult | None:
    """The one-at-a-time slice of a family table along ``param`` (rows where only ``param`` — and
    values derived from or scaled with it — moved)."""
    xs, ys = [], []
    seen = set()
    for r in sub:
        keys = set(r.delta) - _DERIVED
        if keys and param not in keys:
            continue
        x = r.params[param]
        if isinstance(x, bool) or not isinstance(x, int) or x in seen:
            continue
        y = float(r.metrics.values.get(metric, math.nan))
        if not math.isfinite(y):
            continue
        seen.add(x)
        xs.append(float(x))
        ys.append(y)
    if len(xs) < 2:
        return None
    base = sub.base[param]
    return OATResult(param, np.asarray(xs), np.asarray(ys), metric,
                     base_value=float(base) if isinstance(base, int) and not isinstance(base, bool) else None)


class FamilyStudy:
    """A Study whose ``decide`` applies one rule per :class:`Family`. Subclasses provide
    ``group``, ``params``, ``families()``, ``space()``, ``evaluate()`` and may override
    :meth:`confirm` (simulation at the current and recommended sets) and :meth:`figures`."""

    group: str = ""
    params: tuple[str, ...] = ()

    def families(self) -> tuple[Family, ...]:  # pragma: no cover - abstract
        raise NotImplementedError

    # -- hooks -----------------------------------------------------------------------------------
    def confirm(self, table: ResultTable, chosen: ParamSet, meta: Mapping[str, Any]) -> dict[str, Any]:
        return {}

    def figures(self, table: ResultTable, chosen: ParamSet, out: Path,
                confirm: Mapping[str, Any]) -> list[Path]:
        return []

    def extra_notes(self, fam: Family, param: str, cur: ResultRow, best: ResultRow) -> list[str]:
        return []

    def adjust_changes(self, changes: dict[str, Any], chosen_rows: Mapping[str, Any],
                       base: ParamSet) -> dict[str, Any]:
        """Reconcile the per-family changes into one set (default: as is). May fill
        ``self._resolved[param] = note`` for params whose family change was dropped because another
        family's change already resolves the same violation (their verdict becomes KEEP)."""
        return changes

    # -- decide ----------------------------------------------------------------------------------
    def decide_family(self, table: ResultTable, fam: Family, policy) -> tuple[ResultRow, str, str]:
        """(chosen row, verdict KEEP/CHANGE/BLOCKED, reason) for one family."""
        sub = family_table(table, fam)
        cur = sub.current()
        if cur is None:
            raise ValueError(f"{self.group}: the current set was not evaluated")
        if fam.kind == "rule":
            tgt = cur.metrics.values.get(fam.target or "", math.nan)
            p = fam.owns[0]
            if not math.isfinite(tgt):
                return cur, "KEEP", "no target could be computed (no data); current kept"
            tgt = int(tgt)
            cands = [r for r in sub if set(r.delta) - _DERIVED <= {p}]
            best = min(cands, key=lambda r: (abs(int(r.params[p]) - tgt), sub.distance(r)))
            cv = int(cur.params[p])
            rel = abs(cv - tgt) / max(1, tgt)
            if best is cur or rel <= float(policy.materiality):
                return cur, "KEEP", (f"current {cv} is within materiality of the rule's target {tgt} "
                                     f"({rel:.0%} ≤ {policy.materiality:.0%})" if cv != tgt
                                     else "current equals the rule's target")
            return best, "CHANGE", f"rule target {tgt} differs from current {cv} by {rel:.0%}"
        d = decide_with_materiality(sub, policy)
        if d.verdict == "BLOCKED" and fam.least_harm is not None:
            metric, hard = fam.least_harm
            ok = [r for r in sub if all(r.metrics.constraints.get(c, True) for c in hard)
                  and math.isfinite(float(r.metrics.values.get(metric, math.nan)))]
            if ok:
                best = min(ok, key=lambda r: (float(r.metrics.values[metric]), sub.distance(r)))
                return best, "BLOCKED", (f"{d.reason}; least-harm value: the smallest {metric} "
                                         f"({float(best.metrics.values[metric]):.4g}) among rows that keep "
                                         f"{', '.join(hard)}")
        return d.row, d.verdict, d.reason

    def decide(self, results: ResultTable, policy) -> list[Recommendation]:
        fams = self.families()
        chosen_rows: dict[str, tuple[Family, ResultRow, str, str]] = {}
        changes: dict[str, Any] = {}
        for fam in fams:
            row, verdict, reason = self.decide_family(results, fam, policy)
            for p in fam.owns:
                chosen_rows[p] = (fam, row, verdict, reason)
                spec = REGISTRY[p]
                if spec.tunable and row.params[p] != results.base[p]:
                    changes[p] = row.params[p]
        self._resolved: dict[str, str] = {}
        changes = self.adjust_changes(changes, chosen_rows, results.base)
        chosen = results.base.replace(changes) if changes else results.base
        cur_row = results.current()
        meta = dict(cur_row.metrics.meta) if cur_row is not None else {}
        out = out_dir_for(meta, self.group)
        try:
            conf = self.confirm(results, chosen, meta)
        except Exception as e:  # never lose the recommendations to a confirmation failure
            conf = {"error": f"{type(e).__name__}: {e}"}
        evidence: list[Path] = []
        try:
            evidence.append(results.to_csv(out / f"{self.group.lower()}_table.csv"))
            evidence += self.figures(results, chosen, out, conf)
        except Exception as e:  # pragma: no cover - evidence is best effort
            conf.setdefault("evidence_error", f"{type(e).__name__}: {e}")

        recs = []
        dnotes = self.design_notes(results, chosen_rows)
        for p in self.params:
            fam, row, verdict, reason = chosen_rows[p]
            cur = family_table(results, fam).current()
            assert cur is not None
            rec_value = chosen[p]
            v = verdict if verdict == "BLOCKED" else ("CHANGE" if rec_value != results.base[p] else "KEEP")
            if p in self._resolved:
                v = "KEEP" if rec_value == results.base[p] else "CHANGE"
            prov = self.row_provenance(fam, cur)
            sens: dict[str, Any] = {}
            sub = family_table(results, fam)
            sm = fam.sens_metric or (fam.primary if fam.primary != "zero" else None)
            if sm is not None and REGISTRY[p].tunable:
                o = oat_for(sub, p, sm)
                if o is not None:
                    sens = {"oat": o.to_dict(), "sentence": sensitivity_sentence(p, o, sm)}
            if not sens:
                how = ("the derivation from " + ", ".join(REGISTRY[p].parents) if REGISTRY[p].parents
                       else "a design choice / fixed bound")
                sens = {"sentence": f"{p} is set by {how}; no sweep."}
            if p in self._resolved and "signalWindow" in chosen_rows:
                row = chosen_rows["signalWindow"][1]      # the set that resolves it: the new window
            mets = {
                "primary": fam.primary,
                "current": {k: _fmt_metric(cur.metrics.values.get(k)) for k in fam.report_metrics()},
                "recommended": {k: _fmt_metric(row.metrics.values.get(k)) for k in fam.report_metrics()},
                "constraints_current": dict(cur.metrics.constraints),
                "constraints_recommended": dict(row.metrics.constraints),
            }
            if conf:
                mets["simulation"] = conf.get(fam.name, conf.get("all", {}))
            mine = [n for n in dnotes if p in n["params"]]
            if mine:
                mets["design_notes"] = mine
            if verdict == "BLOCKED" and fam.least_harm is not None and mine:
                # D-RD-INF-3 shape: the policy is unmeetable in this environment; the value is least harm
                mets["environment_blocked"] = {"note": mine[0]["id"], "least_harm": rec_value}
            binding = self.binding(fam, cur, row)
            notes = [f"Family '{fam.name}': {reason}."]
            if fam.note:
                notes.append(fam.note)
            notes += self.extra_notes(fam, p, cur, row)
            if p in self._resolved:
                notes.append(self._resolved[p])
            if "error" in conf:
                notes.append(f"Simulation confirmation failed: {conf['error']}")
            klass = REGISTRY[p].change_path
            if klass == "patch-release":
                notes.append("Excluded parameter: the verdict is a patch-release change, not a locked one.")
            recs.append(Recommendation(
                param=p, current=results.base[p], recommended=rec_value,
                verdict=final_verdict(v, prov) if v != "BLOCKED" else "BLOCKED",
                rule=fam.rule, binding=binding, metrics=mets, sensitivity=sens,
                confidence=self.confidence(fam, prov, v, cur), provenance=prov,
                evidence=list(evidence), group=self.group, notes=notes))
        return recs

    def row_provenance(self, fam: Family, row: ResultRow) -> str:
        if fam.provenance_key:
            v = row.metrics.meta.get(fam.provenance_key)
            if v:
                return str(v)
        return fam.provenance

    def binding(self, fam: Family, cur: ResultRow, best: ResultRow) -> str:
        viol = [c for c in fam.constraints if not cur.metrics.constraints.get(c, True)]
        if viol:
            return f"constraint {', '.join(viol)} violated at the current value"
        if fam.kind == "rule":
            return f"ported rule target ({fam.target})"
        if fam.kind == "verify":
            return ("verification: every constraint holds at the current value ("
                    + ", ".join(fam.constraints) + ")")
        return f"primary metric {fam.primary} subject to {', '.join(fam.constraints) or 'no constraint'}"

    def confidence(self, fam: Family, prov: str, verdict: str,
                   cur: ResultRow) -> Literal["high", "medium", "low"]:
        if prov == "synthetic":
            return "low"
        if verdict == "BLOCKED":
            return "low"
        if prov == "real-data":
            return "high"
        return "medium" if fam.kind == "verify" or verdict == "CHANGE" else "high"

    def explain(self, rec: Recommendation, results: ResultTable) -> str:
        spec = REGISTRY[rec.param]
        cur, new = rec.current, rec.recommended
        what = spec.doc or rec.param
        verdict = {"KEEP": "Keep", "CHANGE": "Change", "PROVISIONAL": "Provisionally",
                   "BLOCKED": "Blocked:"}[rec.verdict]
        move = (f"{cur} → {new}" if cur != new else f"{cur}")
        pm = rec.metrics.get("primary", "")
        mc = rec.metrics.get("current", {})
        mr = rec.metrics.get("recommended", {})
        parts = [f"{rec.param} ({what}; rules {', '.join(spec.rules) or '—'}). {verdict} {move}.",
                 f"Decision rule: {rec.rule}", f"Binding: {rec.binding}."]
        if pm and pm != "zero":
            parts.append(f"{pm}: {mc.get(pm)} at the current value, {mr.get(pm)} at the recommended one.")
        s = rec.sensitivity.get("sentence")
        if s:
            parts.append(s)
        parts.append(f"Provenance: {rec.provenance}; confidence {rec.confidence}.")
        parts.append(rec.klass_note)
        return " ".join(parts)


def budget_from_meta(meta: Mapping[str, Any]) -> Budget:
    """The run's budget as recorded in Metrics.meta by ``evaluate`` (paths and horizon included, so
    a custom budget survives the trip through ``decide``, which has no Env)."""
    import dataclasses

    b = Budget.named(str(meta.get("budget", "quick")))
    return dataclasses.replace(b, paths=int(meta.get("budget_paths", b.paths)),
                               block_horizon_days=int(meta.get("budget_days", b.block_horizon_days)))


def stable_seed(*parts: object) -> int:
    return zlib.crc32("\x1f".join(map(str, parts)).encode())


# ===================================================================================================
# G5 analytics


def _binom():
    from scipy.stats import binom

    return binom


def p_below(floor: int, window: int, shares) -> np.ndarray:
    """P(Bin(window, p) < floor) for each share p."""
    return _binom().cdf(floor - 1, window, np.asarray(shares, dtype=float))


def downcrossings_per_block(floor: int, window: int, shares) -> np.ndarray:
    """Exact expected downcrossings of ``floor`` per block at iid share p (``activation.downcrossing_rate``,
    vectorised over shares)."""
    s = np.asarray(shares, dtype=float)
    if window <= 0 or floor <= 0:
        return np.zeros_like(s)
    return _binom().pmf(floor - 1, window - 1, s) * s * (1.0 - s)


def halted_fraction(floor: int, resume: int, window: int, shares) -> np.ndarray:
    """Expected fraction of blocks a hysteresis halt (set below ``floor``, held below ``resume``)
    is set at iid share p, by Little's law: episode rate × mean episode length, capped by the
    exact bracket ``P(count < resume)``. The episode rate is the exact downcrossing rate of the
    floor (an upper bound on episode starts); the length is the fluid time for the window to refill
    from ``floor`` to ``resume`` at share p, ``W·(resume − floor + 1)/(W·p − floor + 1)``, or a whole
    year when ``W·p < resume`` (the halt then holds until the share itself recovers)."""
    s = np.asarray(shares, dtype=float)
    dcr = downcrossings_per_block(floor, window, s)
    gap = window * s - floor + 1.0
    dur = np.where(window * s >= resume, window * (resume - floor + 1.0) / np.maximum(gap, 1e-9),
                   float(BLOCKS_PER_YEAR))
    return np.minimum(p_below(resume, window, s), dcr * np.maximum(dur, 1.0))


def detection_quantile_blocks(p: float, p_drop: float, window: int, floor: int, q: float = 0.95) -> float:
    """Exact upper bound on the ``q``-quantile of the detection delay after a drop ``p → p_drop``
    (window full at ``p``): the smallest ``t`` with ``P(Bin(W − t, p) + Bin(t, p_drop) < floor) ≥ q``.
    The first passage happens no later than any time at which the count is below the floor, so
    ``P(delay ≤ t) ≥ P(count_t < floor)``. ``inf`` when even a window full of post-drop blocks does
    not reach ``q`` (the drop is not reliably detectable)."""
    b = _binom()
    W = int(window)

    def prob(t: int) -> float:
        y = np.arange(t + 1)
        return float(np.sum(b.pmf(y, t, p_drop) * b.cdf(floor - 1 - y, W - t, p)))

    if prob(W) < q:
        return math.inf
    if prob(0) >= q:
        return 0.0
    lo, hi = 0, W
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if prob(mid) >= q:
            hi = mid
        else:
            lo = mid
    return float(hi)


def false_abandon_probability(params: Mapping[str, Any], share, abandon_blocks: int | None = None, *,
                              blocks_per_year: int = BLOCKS_PER_YEAR) -> float:
    """Upper bound on P(ENFORCEMENT is set for ``abandonBlocks`` consecutive blocks within a year)
    when the enforcing share is ``share`` and nobody really abandoned the module (PLAN §5.4, G4).

    ``share`` is a float or an array of equally likely window-mean shares (the share is taken as
    constant within an episode). An ENFORCEMENT episode starts only at a downcrossing of
    ``enforcementFloor`` (exact rate ``P(Bin(W − 1, p) = EF − 1)·p·(1 − p)`` per block) and, once
    set, is held only while ``count < enforcementResume``. If it lasts ``A = abandon_blocks`` blocks,
    the count is below ``enforcementResume`` at ``k = ⌊(A − 1)/W⌋`` heights ``W`` apart after the
    start, whose windows are disjoint and independent of the start; so

        E[# false abandonments per year] ≤ N · Σ_p P(p) · downcrossing(p) · P(Bin(W, p) < ER)^k,

    and the probability of at least one is bounded by that expectation (returned, capped at 1).
    ``abandon_blocks`` defaults to ``params["abandonBlocks"]``.
    """
    W = int(params["signalWindow"])
    ef = int(params["enforcementFloor"])
    er = int(params["enforcementResume"])
    A = int(params["abandonBlocks"] if abandon_blocks is None else abandon_blocks)
    if A <= 0:
        return 1.0
    s = np.atleast_1d(np.asarray(share, dtype=float))
    k = max(0, (A - 1) // max(1, W))
    rate = downcrossings_per_block(ef, W, s) * p_below(er, W, s) ** k
    return float(min(1.0, float(np.mean(rate)) * blocks_per_year))


# ---------------------------------------------------------------------------------------------------
# Share samples (drift mixture)

_SHARE_CACHE: dict[tuple, tuple[np.ndarray, str]] = {}


def _pool_log(env: Env):
    for key in ("pool_shares", "hashrate", "pool_share"):
        v = env.data.get(key) if isinstance(env.data, Mapping) else None
        if v is not None and hasattr(v, "rolling_shares") and len(v):
            return v
    return None


def expected_share(env: Env) -> float:
    return float(env.policy.expected_enforcing_share)


def _hourly_enforcing_share(env: Env) -> np.ndarray:
    """Synthetic hourly enforcing share ``(paths, hours)``: the default PoolModel's pools scaled to the
    expected share (all of them enforcing, "other" not), drifting with the default HashrateDrift."""
    from ybcal.data.synthetic import HashrateDrift, PoolModel

    p0 = expected_share(env)
    sh = np.asarray(PoolModel().shares, dtype=float)
    sh = sh / sh.sum() * p0
    hours = max(48, int(env.budget.block_horizon_days) * 24)
    n_paths = max(8, min(64, int(env.budget.paths)))
    s = HashrateDrift().simulate(sh, hours, env.rng_for(GROUP, "drift"), n_paths=n_paths,
                                 step_blocks=BLOCKS_PER_HOUR)
    return s.sum(axis=-1)


def share_samples(env: Env, window: int) -> tuple[np.ndarray, str]:
    """``N_SHARE_SAMPLES`` equally weighted window-mean enforcing shares and their provenance."""
    log = _pool_log(env)
    p0 = expected_share(env)
    if log is not None:
        key = ("log", id(log), len(log), window, p0, tuple(getattr(env.policy, "enforcing_pools", ()) or ()))
    else:
        key = ("syn", env.seed, env.budget.name, env.budget.paths, env.budget.block_horizon_days, window, p0)
    hit = _SHARE_CACHE.get(key)
    if hit is not None:
        return hit
    qs = (np.arange(N_SHARE_SAMPLES) + 0.5) / N_SHARE_SAMPLES
    res: tuple[np.ndarray, str] | None = None
    if log is not None:
        rs = log.rolling_shares(int(window))
        if rs.shape[0] >= 3:
            # the policy's coalition (enforcing_pools, else largest first up to p0; D-RD-ACT-1) —
            # formerly "the largest pools whose cumulative share is closest to p0", which on the real
            # log picked a key that left the chain mid-sample (1,001 false-halt h/yr)
            from ybcal.sim.landscape import Landscape

            land = Landscape.from_log(log, top=len(log.keys), names={k: k for k in log.keys})
            coal = set(enforcing_coalition(land, env.policy))
            cols = [i for i, k in enumerate(log.keys) if k in coal]
            enf = rs[:, cols].sum(axis=1)
            res = (np.clip(np.quantile(enf, qs), 1e-6, 1 - 1e-6), "real-data")
    if res is None:
        hourly = _SHARE_CACHE.get(("hourly", env.seed, env.budget.name, env.budget.paths,
                                   env.budget.block_horizon_days, p0))
        if hourly is None:
            hourly = (_hourly_enforcing_share(env), "")
            _SHARE_CACHE[("hourly", env.seed, env.budget.name, env.budget.paths,
                          env.budget.block_horizon_days, p0)] = hourly
        h = hourly[0]
        w = max(1, round(window / BLOCKS_PER_HOUR))
        w = min(w, h.shape[1])
        c = np.cumsum(np.concatenate([np.zeros((h.shape[0], 1)), h], axis=1), axis=1)
        means = (c[:, w:] - c[:, :-w]) / w
        res = (np.clip(np.quantile(means.ravel(), qs), 1e-6, 1 - 1e-6), "judgement")
    _SHARE_CACHE[key] = res
    return res


# ---------------------------------------------------------------------------------------------------
# The real pool landscape (wave 2, D-RD-ACT-1..4): per-block replay instead of the binomial mixture

#: Bootstrap horizon per budget, years of blocks per path.
LAND_YEARS: dict[str, float] = {"quick": 1.0, "standard": 2.0, "deep": 4.0}
#: Operators considered for the spurious lock-in hazard (the largest payout keys).
SPURIOUS_TOP = 6
#: A coalition is "sub-resume" (it would live in or near the ENFORCEMENT halt once ACTIVE) when its
#: mean block share is below this fraction of the window; the hazard is its P(lock-in).
SPURIOUS_SHARE = 0.60
_LAND_CACHE: dict[tuple, Any] = {}


def enforcing_coalition(land, policy) -> tuple[str, ...]:
    """The operators (payout keys) expected to enforce: ``policy.enforcing_pools`` (key prefixes) when
    set, else the largest operators in order until their cumulative share first reaches
    ``expected_enforcing_share``."""
    prefixes = tuple(getattr(policy, "enforcing_pools", ()) or ())
    if prefixes:
        out = tuple(n for n in land.names if any(n.startswith(px) for px in prefixes))
        if not out:
            raise ValueError(f"enforcing_pools {prefixes} match no payout key of the pool-share log")
        return out
    sh = land.shares()
    p0 = float(policy.expected_enforcing_share)
    out, cum = [], 0.0
    for n in land.names:                     # names are ordered largest first
        out.append(n)
        cum += sh[n]
        if cum >= p0:
            break
    return tuple(out)


def landscape_bundle(env: Env) -> dict[str, Any] | None:
    """Bootstrapped real per-block signal paths for the policy's coalition (common random numbers for
    every candidate), the sub-resume coalitions of the largest operators, and the non-enforcing
    sequence for the valve. ``None`` without a pool-share log."""
    from ybcal.sim import landscape as Lsc

    log = _pool_log(env)
    if log is None:
        return None
    pol = env.policy
    key = ("land", id(log), len(log), env.seed, env.budget.name, env.budget.paths,
           tuple(getattr(pol, "enforcing_pools", ()) or ()), float(pol.expected_enforcing_share),
           float(getattr(pol, "activation_reach_days", 0.0)), float(getattr(pol, "valve_attack_days", 0.0)))
    hit = _LAND_CACHE.get(key)
    if hit is not None:
        return hit
    land = Lsc.Landscape.from_log(log, top=len(log.keys), names={k: k for k in log.keys})
    coal = enforcing_coalition(land, pol)
    rng = env.rng_for(GROUP, "landscape")
    paths = max(16, min(64, int(env.budget.paths)))
    n = int(LAND_YEARS.get(env.budget.name, 1.0) * BLOCKS_PER_YEAR)
    x = land.indicator(coal)
    sig = Lsc.block_bootstrap(x, n, paths, rng)
    reach_days = float(getattr(pol, "activation_reach_days", 0.0)) or 30.0
    n_act = int(reach_days * BLOCKS_PER_DAY) + 8064 + 1
    act_sig = Lsc.block_bootstrap(x, n_act, max(paths, 64), rng)
    sh = land.shares()
    top = list(land.names[:SPURIOUS_TOP])
    spurious = []
    for c in Lsc.coalitions(Lsc.Landscape(tuple(top), np.where(np.isin(land.op, np.arange(len(top))),
                                                                land.op, -1).astype(np.int16)),
                            min_share=0.0):
        m = sum(sh[k] for k in c)
        if m < SPURIOUS_SHARE and m >= 0.40:
            spurious.append((c, m, Lsc.block_bootstrap(land.indicator(c), n_act, max(paths, 64), rng)))
    attack_days = float(getattr(pol, "valve_attack_days", 0.0))
    nonenf = (Lsc.block_bootstrap(~x, int(attack_days * BLOCKS_PER_DAY), max(paths, 128), rng)
              if attack_days > 0 else None)
    res = {"land": land, "coalition": coal, "share": float(x.mean()), "sig": sig, "act_sig": act_sig,
           "spurious": spurious, "nonenf": nonenf, "reach_days": reach_days,
           "attack_days": attack_days, "valve_cache": {}}
    _LAND_CACHE[key] = res
    return res


def landscape_values(cand: ParamSet, bundle: Mapping[str, Any]) -> dict[str, float]:
    """Real-landscape metrics of one candidate (``ybcal.sim.landscape``)."""
    from ybcal.sim import landscape as Lsc

    h = Lsc.halt_stats(cand, bundle["sig"])
    reach = float(bundle["reach_days"])
    a = Lsc.activation_stats(cand, bundle["act_sig"], horizons_days=(int(reach),))
    out = {
        "land.share": float(bundle["share"]),
        "land.part_hours": h.part_hours_per_year, "land.enf_hours": h.enf_hours_per_year,
        "land.part_episodes": h.part_episodes_per_year, "land.enf_episodes": h.enf_episodes_per_year,
        "land.p_enf_full_window": h.p_enf_full_window_per_year,
        "land.part_longest_p95": h.part_longest_blocks_p95,
        "act.p_first_window_real": a.p_first_window,
        "act.p_lock_reach": a.p_within.get(int(reach), math.nan),
        "act.lock_median_days": a.median_days,
    }
    worst, worst_c = 0.0, ""
    for c, _m, s in bundle["spurious"]:
        p = Lsc.activation_stats(cand, s, horizons_days=(int(reach),)).p_within.get(int(reach), 0.0)
        if p > worst:
            worst, worst_c = p, "+".join(k[:8] for k in c)
    out["act.spurious_lock"] = worst
    out["act.spurious_coalition_n"] = float(len(bundle["spurious"]))
    bundle.setdefault("spurious_worst", {})[cand.digest()] = worst_c
    V = int(cand["valveBlocks"])
    if bundle["nonenf"] is not None:
        vc = bundle["valve_cache"]
        if V not in vc:
            tr = Lsc.valve_race_trips(bundle["nonenf"], V)
            vc[V] = float(np.mean(tr >= 0))
        out["valve.attack_trip"] = vc[V]
    return out


# ===================================================================================================
# The study


def _scaled_thresholds(base: ParamSet, W: int) -> dict[str, int]:
    """Thresholds at window ``W`` keeping the base set's fractions of ``signalWindow``."""
    W0 = int(base["signalWindow"])
    out = {"signalWindow": W}
    for k in ("activationThreshold", "participationFloor", "enforcementFloor", "enforcementResume"):
        out[k] = round(int(base[k]) * W / W0)
    # keep the enforcement majority exact (EF ≥ W/2) when the base sits on it
    if 2 * int(base["enforcementFloor"]) >= W0:
        out["enforcementFloor"] = max(out["enforcementFloor"], -(-W // 2))
    return out


@dataclass
class G5Study(FamilyStudy):
    """Activation and enforcement (PLAN §5.5)."""

    group: str = GROUP
    params: tuple[str, ...] = field(default_factory=lambda: params_for_group(GROUP))

    # -- families --------------------------------------------------------------------------------
    def families(self) -> tuple[Family, ...]:
        halt_cons = ("false_halt", "flaps")
        rep = ("fh.hours", "fh.part_hours", "fh.enf_hours", "flaps", "detect.enf_p95", "detect.part_p95",
               "act.p_lockin", "act.p_reach", "act.spurious_lock", "act.lock_median_days", "land.share",
               "abandon.false_prob")
        return (
            Family("window", ("signalWindow",),
                   ("signalWindow", "activationThreshold", "participationFloor", "enforcementFloor",
                    "enforcementResume"),
                   "signalWindow with every threshold kept at its fraction of the window: minimise "
                   "false-halt hours/yr (0.01 h resolution) at the expected enforcing share (drift "
                   "mixture) subject to false-halt ≤ max_false_halt_hours_per_year, flaps ≤ "
                   "max_flaps_per_year, detection p95 of a drop to detection_drop_share ≤ "
                   "max_detection_blocks, EF ≥ W/2 and reliable activation; KEEP unless > materiality.",
                   primary="fh.hours_q", constraints=(*halt_cons, "detect_enf", "detect_part", "majority",
                                                      "activation_reach", "spurious_lock"),
                   report=(*rep, "act.blocks", "runbook_blocks"), sens_metric="detect.enf_p95",
                   provenance_key="share_provenance", requires="signalWindow"),
            Family("participation", ("participationFloor",), ("participationFloor",),
                   "participationFloor: minimise false-halt hours/yr subject to the false-halt, flap and "
                   "detection budgets (drop to detection_drop_share detected by ACT-4) and the §1.4 "
                   "ordering; KEEP unless > materiality.",
                   primary="fh.hours_q", constraints=(*halt_cons, "detect_part"), report=rep,
                   sens_metric="detect.part_p95", provenance_key="share_provenance"),
            Family("enforcement", ("enforcementFloor", "enforcementResume"),
                   ("enforcementFloor", "enforcementResume"),
                   "enforcementFloor / enforcementResume: minimise false-halt hours/yr subject to the "
                   "false-halt, flap and detection budgets, the enforcement majority EF ≥ W/2 (L3) and "
                   "the §1.4 ordering; KEEP unless > materiality.",
                   primary="fh.hours_q", constraints=(*halt_cons, "detect_enf", "majority"), report=rep,
                   sens_metric="detect.enf_p95", provenance_key="share_provenance"),
            Family("activation", ("activationThreshold",), ("activationThreshold",),
                   "activationThreshold (lock-in level and ACT-4 resume) is the bar a coalition must "
                   "clear to switch enforcement on: minimise the probability that a coalition which "
                   "cannot hold the floors (mean share < 60 %) locks in (act.spurious_lock, real pool "
                   "landscape; without one, false-halt hours) subject to P(lock-in of the expected "
                   "coalition within activation_reach_days, 0 = the first eligible window) ≥ "
                   "activation_reliability, the false-halt and flap budgets and the §1.4 ordering; "
                   "KEEP unless > materiality.",
                   primary="act.guard", constraints=(*halt_cons, "activation_reach", "spurious_lock"),
                   report=(*rep, "act.blocks"), sens_metric="act.p_lockin",
                   provenance_key="share_provenance"),
            Family("delay", ("activationDelay",), ("activationDelay",),
                   "activationDelay: the shortest delay (time to activation W − 1 + delay) with "
                   "delay ≥ operator_upgrade_window_blocks; KEEP unless > materiality.",
                   primary="act.blocks", constraints=("upgrade_window",), report=("act.blocks",),
                   provenance="judgement"),
            Family("valve", ("valveBlocks",), ("valveBlocks",),
                   "valveBlocks (node-local): the shortest split (blocks to rejoin) with natural false "
                   "trips ≤ max_valve_false_trips_per_year, a non-enforcing minority's trip "
                   "probability ≤ valve_minority_trip_max (per race, or within valve_attack_days of a "
                   "sustained race attack on the real block sequence) and, when enforcers are a "
                   "genuine minority (stock share 1 − detection_drop_share), a chance ≤ "
                   f"{JUDGEMENT['valve_capstuck_max']} of ending stuck at the 64-note cap instead of "
                   "rejoining; KEEP unless > materiality. When nothing qualifies: the smallest attack "
                   "trip probability that keeps the note-cap bound (BLOCKED, least harm).",
                   primary="valve.split_blocks",
                   constraints=("valve_natural", "valve_minority", "valve_capstuck"),
                   report=("valve.split_blocks", "valve.minority_trip", "valve.attack_trip",
                           "valve.capstuck", "valve.natural_trips_per_year"),
                   least_harm=("valve.attack_trip", ("valve_natural", "valve_capstuck")),
                   sens_metric="valve.minority_trip", provenance="judgement",
                   note="Patch-release parameter (node-local, ACT-7); verdicts never require a new "
                        "parameter set."),
        )

    def adjust_changes(self, changes: dict[str, Any], chosen_rows: Mapping[str, Any],
                       base: ParamSet) -> dict[str, Any]:
        """A new signalWindow carries its thresholds at their fractions unless a threshold family
        moved that threshold itself (then the threshold family's value is rescaled to the new W)."""
        if "signalWindow" not in changes:
            return changes
        W = int(changes["signalWindow"])
        scaled = _scaled_thresholds(base, W)
        out = dict(changes)
        wrow = chosen_rows.get("signalWindow", (None, None, "", ""))[1]
        if wrow is not None and wrow.metrics.feasible:
            # minimal change (D-RD-AUD-9, D-RD-ACT-2): the window move alone satisfies every budget the
            # threshold families were fixing, so their changes are dropped and the thresholds keep
            # their fractions of the new window
            for k in ("activationThreshold", "participationFloor", "enforcementFloor", "enforcementResume"):
                own = f"its own family's change to {out.pop(k)} is dropped; " if k in out else ""
                frac = int(base[k]) / int(base["signalWindow"])
                self._resolved[k] = (f"Resolved by signalWindow {W}: {own}with every threshold at its "
                                     "current fraction of the window the set satisfies the false-halt, "
                                     "flap and detection budgets (minimal change, D-RD-ACT-2); the value "
                                     f"is the current fraction ({frac:.2%}) "
                                     "of the new window.")
        for k in ("activationThreshold", "participationFloor", "enforcementFloor", "enforcementResume"):
            v = int(out.get(k, base[k]))
            out[k] = round(v * W / int(base["signalWindow"])) if k in out else scaled[k]
        return out

    # -- space -----------------------------------------------------------------------------------
    def space(self, base: ParamSet, budget: Budget) -> Iterable[ParamSet]:
        fine = budget.name != "quick"
        out = [base]
        W0 = int(base["signalWindow"])
        lo, hi = REGISTRY["signalWindow"].bounds
        stepW = 288 if fine else 576
        # centred on the current window so its neighbours (W0 ± one step) are always evaluated
        for W in range(W0 - ((W0 - lo) // stepW) * stepW, hi + 1, stepW):
            if W != W0:
                out.append(base.replace(_scaled_thresholds(base, W)))
        frac_step = 0.0125 if fine else 0.025
        step = max(1, round(W0 * frac_step))
        for p in ("participationFloor", "enforcementFloor", "enforcementResume", "activationThreshold"):
            v0 = int(base[p])
            blo, bhi = REGISTRY[p].bounds
            for j in range(-16, 17):
                v = v0 + j * step
                if j and blo <= v <= min(bhi, W0):
                    out.append(base.replace({p: v}))
        d0 = int(base["activationDelay"])
        blo, bhi = REGISTRY["activationDelay"].bounds
        for d in range(blo, min(bhi, 4 * max(d0, 2016)) + 1, 288):
            if d != d0:
                out.append(base.replace(activationDelay=d))
        blo, bhi = REGISTRY["valveBlocks"].bounds
        for v in range(blo, bhi + 1):
            if v != int(base["valveBlocks"]):
                out.append(base.replace(valveBlocks=v))
        return out

    # -- evaluate --------------------------------------------------------------------------------
    def evaluate(self, cand: ParamSet, env: Env) -> Metrics:
        pol = env.policy
        W = int(cand["signalWindow"])
        T = int(cand["activationThreshold"])
        PF = int(cand["participationFloor"])
        EF = int(cand["enforcementFloor"])
        ER = int(cand["enforcementResume"])
        D = int(cand["activationDelay"])
        V = int(cand["valveBlocks"])
        bundle = landscape_bundle(env)
        lv = landscape_values(cand, bundle) if bundle is not None else {}
        # the real coalition's mean share replaces the policy figure where one is measured
        p0 = float(bundle["share"]) if bundle is not None else expected_share(env)
        pd = float(pol.detection_drop_share)
        S, sprov = share_samples(env, W)
        hours = BLOCKS_PER_YEAR / BLOCKS_PER_HOUR
        # expected halted fraction per share sample (rate × duration, capped by P(count < resume))
        if lv:      # real per-block replay (D-RD-ACT-2): the exact ACT-4/ACT-6 state machine
            part_h, enf_h = lv["land.part_hours"], lv["land.enf_hours"]
            flaps = lv["land.part_episodes"] + lv["land.enf_episodes"]
        else:
            part_h = float(np.mean(halted_fraction(PF, T, W, S))) * hours
            enf_h = float(np.mean(halted_fraction(EF, ER, W, S))) * hours
            dc = downcrossings_per_block(PF, W, S) + downcrossings_per_block(EF, W, S)
            flaps = float(np.mean(dc)) * BLOCKS_PER_YEAR
        fh = part_h + enf_h
        res = JUDGEMENT["false_halt_resolution_hours"]
        fh_q = 0.0 if fh < res / 2 else round(fh / res) * res
        q = JUDGEMENT["detection_quantile"]
        det_enf = detection_quantile_blocks(p0, pd, W, EF, q)
        det_part = detection_quantile_blocks(p0, pd, W, PF, q)
        from ybcal.sim import activation as act

        fluid = act.detection_delay_approx(p0, pd, W, EF)
        p_lock = float(1.0 - p_below(T, W, [p0])[0])
        reach_days = float(getattr(pol, "activation_reach_days", 0.0))
        if lv:
            p_reach = lv["act.p_lock_reach"] if reach_days > 0 else lv["act.p_first_window_real"]
        else:
            # binomial: disjoint windows inside the reach are independent chances (a lower bound)
            k = max(1, int(reach_days * BLOCKS_PER_DAY) // max(1, W)) if reach_days > 0 else 1
            p_reach = float(1.0 - (1.0 - p_lock) ** k)
        act_blocks = W - 1 + D
        v_min = act.valve_trip_probability(1.0 - p0, V)
        v_nat = act.natural_fork_trip_rate(float(pol.orphan_rate), V)
        from ybcal.sim.landscape import valve_stuck_share

        v_stuck = valve_stuck_share(1.0 - pd, V)
        fa = false_abandon_probability(cand, S, int(cand["abandonBlocks"]))
        runbook = (det_enf if math.isfinite(det_enf) else W) + W + int(pol.release_lead_blocks) + \
            int(pol.runbook_operator_buffer_blocks)
        values = {
            "zero": 0.0,
            "share_expected": p0, "share_p05": float(np.quantile(S, 0.05)), "share_p50": float(np.median(S)),
            "fh.hours": fh, "fh.hours_q": fh_q, "fh.part_hours": part_h, "fh.enf_hours": enf_h,
            "fh.part_lower_hours": float(np.mean(p_below(PF, W, S))) * hours,
            "fh.enf_lower_hours": float(np.mean(p_below(EF, W, S))) * hours,
            "flaps": flaps,
            "detect.enf_p95": det_enf, "detect.part_p95": det_part,
            "detect.enf_fluid": float(fluid) if fluid is not None else math.inf,
            "act.p_lockin": p_lock, "act.blocks": float(act_blocks),
            "valve.split_blocks": float(V), "valve.minority_trip": v_min,
            "valve.natural_trips_per_year": v_nat, "valve.capstuck": v_stuck,
            "abandon.false_prob": fa, "runbook_blocks": float(runbook),
            "act.p_reach": p_reach,
            **lv,
        }
        # the activation family's objective: the spurious lock-in hazard on a real landscape; the
        # original false-halt objective otherwise (its synthetic coalition has no hopping operator)
        values["act.guard"] = float(values["act.spurious_lock"]) if "act.spurious_lock" in values else fh_q
        cons = {
            "false_halt": fh <= float(pol.max_false_halt_hours_per_year),
            "flaps": flaps <= float(pol.max_flaps_per_year),
            "detect_enf": det_enf <= int(pol.max_detection_blocks),
            "detect_part": det_part <= int(pol.max_detection_blocks),
            "majority": 2 * EF >= W,
            "activation_reach": p_reach >= float(getattr(pol, "activation_reliability",
                                                          JUDGEMENT["activation_reliability"])),
            "spurious_lock": lv.get("act.spurious_lock", 0.0)
            <= float(getattr(pol, "max_spurious_lock_prob", 1.0)),
            "upgrade_window": int(pol.operator_upgrade_window_blocks) <= D,
            "valve_natural": v_nat <= float(pol.max_valve_false_trips_per_year),
            "valve_capstuck": v_stuck <= JUDGEMENT["valve_capstuck_max"],
            "valve_minority": lv.get("valve.attack_trip", v_min)
            <= float(getattr(pol, "valve_minority_trip_max", JUDGEMENT["valve_minority_trip_max"])),
        }
        meta = {"seed": env.seed, "budget": env.budget.name, "budget_paths": env.budget.paths,
                "budget_days": env.budget.block_horizon_days, "out_dir": env_out_dir(env),
                "share_provenance": sprov, "expected_share": p0, "drop_share": pd,
                "orphan_rate": float(pol.orphan_rate),
                "coalition": list(bundle["coalition"]) if bundle is not None else [],
                "reach_days": float(bundle["reach_days"]) if bundle is not None else 0.0,
                "landscape": bundle is not None}
        prov = "real-data" if sprov == "real-data" else "judgement"
        return Metrics(values, "fh.hours_q", True, cons, prov, meta)

    # -- simulation confirmation -----------------------------------------------------------------
    def confirm(self, table: ResultTable, chosen: ParamSet, meta: Mapping[str, Any]) -> dict[str, Any]:
        budget = budget_from_meta(meta)
        seed = int(meta.get("seed", 0))
        p0 = float(meta.get("expected_share", 0.8))
        pd = float(meta.get("drop_share", 0.45))
        sets = {"current": table.base}
        if chosen != table.base:
            sets["recommended"] = chosen
        out: dict[str, Any] = {}
        for name, ps in sets.items():
            out[name] = confirm_activation(ps, p0, pd, seed, budget)
        out["all"] = {k: v for k, v in out.items()}
        return out

    def figures(self, table: ResultTable, chosen: ParamSet, out: Path,
                confirm: Mapping[str, Any]) -> list[Path]:
        return g5_figures(table, chosen, out, self.families()[0])

    def design_notes(self, results: ResultTable, chosen_rows: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Rule- and environment-level findings of the real landscape (D-RD-ACT-1, -4, -5)."""
        from ybcal.studies.g3_collateral import design_note

        cur = results.current()
        if cur is None or not cur.metrics.meta.get("landscape"):
            return []
        v = cur.metrics.values
        share = float(v.get("land.share", math.nan))
        notes = [design_note(
            "G5-ENV-1", "One operator is pivotal for enforcement",
            f"The enforcing coalition ({share:.1%} of blocks) contains an operator with about half the "
            "hash; without it the rest of the chain is below one half, so no coalition without it can "
            "reach the L3 majority and activation without it is impossible at any threshold >= 50 %.",
            evidence={"coalition_share": round(share, 4), "coalition": cur.metrics.meta.get("coalition")},
            consequence="That operator can switch enforcement off by leaving (ENFORCEMENT after about "
            "0.8 W blocks) or fake it by signalling without enforcing (never detected by ACT-6; the "
            "valve is the only defence). No parameter changes this.",
            fix="Coverage: recruit the remaining large hash (the 25 % flex block) before startHeight.",
            params=("signalWindow", "activationThreshold", "enforcementFloor"))]
        sp = v.get("act.spurious_lock")
        if isinstance(sp, float) and sp > 0.05:
            notes.append(design_note(
                "G5-DN-HOP", "A hop can lock in a coalition that cannot hold the floors",
                f"ACT-2 locks in on one window at the threshold, so a few days of an auto-switching "
                f"pool's hash lock in a coalition whose mean share is below 60 % with probability "
                f"{sp:.2f} within the activation reach (real landscape, current set).",
                evidence={"act.spurious_lock": round(sp, 4),
                          "reach_days": cur.metrics.meta.get("reach_days", "policy")},
                consequence="Once ACTIVE that coalition flaps in and out of ENFORCEMENT (W19 windows, "
                "abandonment, defection windows).",
                fix="Procedure: publish startHeight only after the enforcing operators commit and watch "
                "yed_getactivation; or a rule change (lock in after two consecutive windows above the "
                "threshold, state.cpp:1084).",
                params=("activationThreshold", "signalWindow")))
        at = v.get("valve.attack_trip")
        if isinstance(at, float):
            vrow = chosen_rows.get("valveBlocks", (None, None))[1]
            vv = vrow.metrics.values if vrow is not None else v
            notes.append(design_note(
                "G5-DN-VALVE", "No valve length defends a 28 % stock minority against a sustained race",
                f"A matured-vault owner keeps a non-burning sweep in every stock mempool; races run back "
                f"to back on the real block sequence. P(trip within the attack horizon) is {at:.2f} at "
                f"valveBlocks {int(results.base['valveBlocks'])} and "
                f"{float(vv.get('valve.attack_trip', math.nan)):.2f} at "
                f"{int(vrow.params['valveBlocks']) if vrow is not None else '?'}; longer valves leave a "
                "genuine-minority enforcer stuck at the 64-note cap (index.h:65) instead of rejoining.",
                evidence={"attack_trip_current": round(at, 4),
                          "attack_trip_least_harm": round(float(vv.get("valve.attack_trip", math.nan)), 4),
                          "capstuck_least_harm": round(float(vv.get("valve.capstuck", math.nan)), 4)},
                consequence="Enforcement turns off network-wide and the sweep confirms: YED loses the "
                "collateral of every vault that is swept the same way.",
                fix="Coverage (with the flex 25 % enforcing, 16 blocks suffice), or a node-local patch: "
                "raise VALVE_NOTE_CAP and/or require the heavier branch's lead to persist.",
                params=("valveBlocks",)))
        return notes


def confirm_activation(ps: ParamSet, p0: float, pd: float, seed: int, budget: Budget) -> dict[str, Any]:
    """Simulation (exact ACT-1..6) against the analytic metrics at one parameter set."""
    from ybcal.sim import activation as act

    rng = np.random.default_rng([seed, stable_seed(GROUP, "confirm", ps.digest())])
    W, EF, ER = (int(ps[k]) for k in ("signalWindow", "enforcementFloor", "enforcementResume"))
    D = int(ps["activationDelay"])
    paths = max(16, min(64, int(budget.paths)))
    n = int(budget.block_horizon_days) * BLOCKS_PER_DAY
    res: dict[str, Any] = {}
    # 1. steady state at the expected share: activation time and false halts after activation
    sig = rng.random((paths, n)) < p0
    s = act.simulate(ps, sig, start_height=0, height0=0, enforce_until=0)
    ah = s.activate_height
    res["activation_blocks_sim_median"] = float(np.median(ah[ah >= 0])) if (ah >= 0).any() else math.inf
    res["activation_blocks_analytic"] = float(W - 1 + D)
    res["activated_fraction"] = float((ah >= 0).mean())
    after = np.arange(n)[None, :] >= np.where(ah >= 0, ah, n)[:, None]
    blocks_after = int(after.sum())
    halted = int(((s.participation_halt | s.enforcement_halt) & after).sum())
    res["false_halt_hours_per_year_sim"] = (halted / max(1, blocks_after)) * BLOCKS_PER_YEAR / BLOCKS_PER_HOUR
    res["sim_path_years"] = blocks_after / BLOCKS_PER_YEAR
    # 2. probe share one standard deviation above the enforcement floor: a measurable halt rate
    p_probe = min(0.99, (EF + math.sqrt(W * 0.25)) / W)
    est = act.false_halt_rate(p_probe, W, EF, ER, simulate_blocks=min(n, 20 * W), n_paths=paths, rng=rng)
    res["probe_share"] = p_probe
    res["probe_halted_fraction_bracket"] = [est.halted_fraction_lower, est.halted_fraction_upper]
    res["probe_halted_fraction_estimate"] = float(halted_fraction(EF, ER, W, [p_probe])[0])
    res["probe_halted_fraction_sim"] = est.simulated["halted_fraction"]
    res["probe_episodes_upper"] = est.episodes_per_year_upper
    res["probe_episodes_sim"] = est.simulated["episodes_per_year"]
    res["probe_consistent"] = bool(
        est.halted_fraction_lower - 0.05 <= est.simulated["halted_fraction"]
        <= est.halted_fraction_upper + 0.05
        and est.simulated["episodes_per_year"] <= 1.25 * est.episodes_per_year_upper + 50)
    # 3. detection delay of a drop p0 → pd (Monte Carlo) vs the analytic p95 bound
    dd = act.detection_delay(p0, pd, W, EF, n_paths=paths * 16, rng=rng)
    ok = dd[dd > 0]
    res["detect_enf_p95_sim"] = float(np.quantile(ok, 0.95)) if len(ok) else math.inf
    res["detect_enf_p95_bound"] = detection_quantile_blocks(p0, pd, W, EF, JUDGEMENT["detection_quantile"])
    res["detect_enf_detected_fraction"] = float((dd > 0).mean())
    # the bound is on the quantile; allow 1 % for the Monte-Carlo quantile's own noise
    res["detect_consistent"] = bool(res["detect_enf_p95_sim"] <= 1.01 * res["detect_enf_p95_bound"] + 1)
    # 4. the activation scenarios (hashrate-drop-45 plus every scenario tagged "activation", among them
    #    the real-landscape family scenarios/real-pools.toml): detection, recovery, W19, abandonment
    #    and the ACT-7 exposure (enforcement on while enforcers are a minority)
    try:
        res["scenarios"] = scenario_confirmation(ps, rng, max(8, paths // 4))
        h45 = res["scenarios"].get("hashrate-drop-45", {})
        for k in ("detect_blocks_p95", "detected_fraction", "enforcement_restored_fraction",
                  "max_halt_run_blocks"):
            if k in h45:
                res[f"scenario_{k}"] = h45[k]
    except Exception as e:  # pragma: no cover - scenario library optional
        res["scenario_error"] = f"{type(e).__name__}: {e}"
    return res


def scenario_confirmation(ps: ParamSet, rng: np.random.Generator, m: int = 8) -> dict[str, dict[str, float]]:
    """Every scenario tagged ``activation`` through the exact ACT-1..6 state machine at ``ps``.

    Signals are drawn from ``signal_share`` (falling back to ``enforcing_share``); the chain starts
    ACTIVE with a full window at the day-0 share unless the scenario's constant ``start_active`` is 0.
    Per scenario (m paths): detection after the first schedule change (blocks to the first
    ENFORCEMENT, p95), fraction detected, enforcement restored at the end, the longest ENFORCEMENT run,
    whether it reaches a signal window (W19 opens) or ``abandonBlocks`` (abandonment), PARTICIPATION /
    ENFORCEMENT hours, lock-in (when starting un-activated), the blocks with enforcement on while the
    true enforcing share is below one half (ACT-7 exposure) and the probability that a sustained race
    attack trips the work valve in the scenario (``landscape.valve_race_trips`` on iid draws of the
    non-enforcing share, counted only while enforcement is on)."""
    from ybcal.data import scenarios as sc
    from ybcal.sim import activation as act
    from ybcal.sim import landscape as Lsc

    lib = sc.load_library()
    names = [n for n, s_ in lib.items() if "activation" in getattr(s_, "tags", ()) or n == "hashrate-drop-45"]
    W = int(ps["signalWindow"])
    A = int(ps["abandonBlocks"])
    V = int(ps["valveBlocks"])
    out: dict[str, dict[str, float]] = {}
    for name in sorted(set(names)):
        scen = lib[name]
        run = scen.generate(rng, n_paths=1, resolution="block")
        enf_s = np.asarray(run.schedules["enforcing_share"], dtype=float)
        enf_s = enf_s[0] if enf_s.ndim == 2 else enf_s
        sig_s = np.asarray(run.schedules.get("signal_share", enf_s), dtype=float)
        sig_s = sig_s[0] if sig_s.ndim == 2 else sig_s
        n = enf_s.size
        start_active = int(float(run.constants.get("start_active", 1))) == 1
        sig = rng.random((m, n)) < sig_s[None, :]
        if start_active:
            prior = rng.random((m, W)) < sig_s[0]
            init = act.ActivationInit(status=act.ACTIVE, lock_in_height=0, activate_height=1,
                                      prior_signals=prior)
            ss = act.simulate(ps, sig, start_height=0, height0=W + 1, initial=init, enforce_until=0)
        else:
            ss = act.simulate(ps, sig, start_height=0, height0=0, enforce_until=0)
        enf = ss.enforcement_halt
        change = np.flatnonzero(np.abs(np.diff(enf_s)) > 1e-9)
        c0 = int(change[0]) + 1 if change.size else 0
        first = np.where(enf[:, c0:].any(axis=1), np.argmax(enf[:, c0:], axis=1), -1)
        hit = first[first >= 0]
        runs = act._run_lengths(enf).max(axis=1)
        on = ss.enforcement_on
        minority = on & (enf_s[None, :] < 0.5)
        nonenf = (rng.random((m, n)) >= enf_s[None, :]) & on
        trips = Lsc.valve_race_trips(nonenf, V)
        r = {
            "detect_blocks_p95": float(np.quantile(hit, 0.95)) if len(hit) else math.inf,
            "detected_fraction": float((first >= 0).mean()),
            "enforcement_restored_fraction": float((~enf[:, -1]).mean()),
            "max_halt_run_blocks": float(runs.max()),
            "w19_open_fraction": float((runs >= W).mean()),
            "abandoned_fraction": float((runs >= A).mean()),
            "part_hours": float(ss.participation_halt.sum(axis=1).mean() / BLOCKS_PER_HOUR),
            "enf_hours": float(enf.sum(axis=1).mean() / BLOCKS_PER_HOUR),
            "minority_enforced_blocks": float(minority.sum(axis=1).mean()),
            "valve_trip_under_attack": float((trips >= 0).mean()),
        }
        if not start_active:
            lk = ss.lock_in_height
            r["locked_fraction"] = float((lk >= 0).mean())
            r["lock_in_days_median"] = (float(np.median(lk[lk >= 0]) / BLOCKS_PER_DAY) if (lk >= 0).any()
                                        else math.inf)
        out[name] = r
    return out


def g5_figures(table: ResultTable, chosen: ParamSet, out: Path, window_family: Family) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    blue, orange, ink, muted, surface = "#2a78d6", "#eb6834", "#0b0b0b", "#52514e", "#fcfcfb"
    paths: list[Path] = []
    rows = sorted((r for r in family_rows(table, window_family) if not r.delta or "signalWindow" in r.delta),
                  key=lambda r: int(r.params["signalWindow"]))
    if len(rows) >= 2:
        W = [int(r.params["signalWindow"]) for r in rows]
        det = [r.metrics.values["detect.enf_p95"] for r in rows]
        fh = [max(r.metrics.values["fh.hours"], 1e-12) for r in rows]
        fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 3.6), facecolor=surface)
        for a in (a1, a2):
            a.set_facecolor(surface)
            a.axvline(int(table.base["signalWindow"]), color=muted, lw=1, ls="--", label="current")
            if chosen["signalWindow"] != table.base["signalWindow"]:
                a.axvline(int(chosen["signalWindow"]), color=orange, lw=1, ls=":", label="recommended")
            a.set_xlabel("signalWindow (blocks)", color=ink)
            a.grid(alpha=0.2)
        a1.plot(W, fh, color=blue, lw=2)
        a1.set_yscale("log")
        a1.set_title("Expected false-halt hours / year (floored at 1e-12)", color=ink, fontsize=10)
        a2.plot(W, det, color=blue, lw=2)
        a2.set_title("Detection delay p95 bound, drop to the policy share (blocks)", color=ink, fontsize=10)
        a1.legend(frameon=False, fontsize=8)
        fig.tight_layout()
        p = out / "g5_window_tradeoff.png"
        fig.savefig(p, dpi=110)
        plt.close(fig)
        paths.append(p)
    # operating characteristic of the chosen set
    Wc = int(chosen["signalWindow"])
    sh = np.linspace(0.3, 0.95, 131)
    fig, ax = plt.subplots(figsize=(6.4, 3.6), facecolor=surface)
    ax.set_facecolor(surface)
    ax.plot(sh, p_below(int(chosen["enforcementFloor"]), Wc, sh), color=blue, lw=2,
            label="P(count < enforcementFloor)")
    ax.plot(sh, p_below(int(chosen["participationFloor"]), Wc, sh), color=orange, lw=2,
            label="P(count < participationFloor)")
    for x, lab in ((0.8, "expected"), (0.45, "drop")):
        ax.axvline(x, color=muted, lw=1, ls="--")
        ax.text(x, 1.02, lab, color=muted, fontsize=8, ha="center")
    ax.set_xlabel("enforcing hash share", color=ink)
    ax.set_ylabel("probability per window", color=ink)
    ax.set_title(f"Halt operating characteristic (signalWindow {Wc})", color=ink, fontsize=10)
    ax.legend(frameon=False, fontsize=8)
    ax.grid(alpha=0.2)
    fig.tight_layout()
    p = out / "g5_operating_characteristic.png"
    fig.savefig(p, dpi=110)
    plt.close(fig)
    paths.append(p)
    return paths


def make_study() -> G5Study:
    """The G5 study (``ybcal.studies.base.load_study("G5")``)."""
    return G5Study()


__all__ = [
    "JUDGEMENT", "Family", "FamilyStudy", "G5Study", "confirm_activation", "detection_quantile_blocks",
    "downcrossings_per_block", "false_abandon_probability", "family_table", "make_study", "oat_for",
    "p_below", "share_samples",
]

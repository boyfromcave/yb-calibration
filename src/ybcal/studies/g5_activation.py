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

    def report_metrics(self) -> tuple[str, ...]:
        out = [self.primary] if self.primary != "zero" else []
        out += [m for m in self.report if m not in out]
        return tuple(out)


_DERIVED = frozenset(derived_names())


def family_rows(table: ResultTable, fam: Family) -> list[ResultRow]:
    allowed = set(fam.varies) | _DERIVED
    return [r for r in table if set(r.delta) <= allowed]


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
        for p in self.params:
            fam, row, verdict, reason = chosen_rows[p]
            cur = family_table(results, fam).current()
            assert cur is not None
            rec_value = chosen[p]
            v = verdict if verdict == "BLOCKED" else ("CHANGE" if rec_value != results.base[p] else "KEEP")
            if p in self._resolved and rec_value == results.base[p]:
                v = "KEEP"
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
            mets = {
                "primary": fam.primary,
                "current": {k: _fmt_metric(cur.metrics.values.get(k)) for k in fam.report_metrics()},
                "recommended": {k: _fmt_metric(row.metrics.values.get(k)) for k in fam.report_metrics()},
                "constraints_current": dict(cur.metrics.constraints),
                "constraints_recommended": dict(row.metrics.constraints),
            }
            if conf:
                mets["simulation"] = conf.get(fam.name, conf.get("all", {}))
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
        key = ("log", id(log), len(log), window, p0)
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
            order = np.argsort(-rs.mean(axis=0))
            cum = np.cumsum(rs.mean(axis=0)[order])
            n_enf = int(np.argmin(np.abs(cum - p0))) + 1      # the largest pools whose share is closest to p0
            enf = rs[:, order[:n_enf]].sum(axis=1)
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
               "act.p_lockin", "abandon.false_prob")
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
                                                      "activation_reach"),
                   report=(*rep, "act.blocks", "runbook_blocks"), sens_metric="detect.enf_p95",
                   provenance_key="share_provenance"),
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
                   "activationThreshold (lock-in level and ACT-4 resume): minimise false-halt hours/yr "
                   "subject to P(lock-in in the first eligible window) ≥ "
                   f"{JUDGEMENT['activation_reliability']} at the expected share, the false-halt and "
                   "flap budgets and the §1.4 ordering; KEEP unless > materiality.",
                   primary="fh.hours_q", constraints=(*halt_cons, "activation_reach"),
                   report=(*rep, "act.blocks"), sens_metric="act.p_lockin",
                   provenance_key="share_provenance"),
            Family("delay", ("activationDelay",), ("activationDelay",),
                   "activationDelay: the shortest delay (time to activation W − 1 + delay) with "
                   "delay ≥ operator_upgrade_window_blocks; KEEP unless > materiality.",
                   primary="act.blocks", constraints=("upgrade_window",), report=("act.blocks",),
                   provenance="judgement"),
            Family("valve", ("valveBlocks",), ("valveBlocks",),
                   "valveBlocks (node-local): the shortest split (blocks to rejoin) with natural false "
                   "trips ≤ max_valve_false_trips_per_year and a non-enforcing minority's trip "
                   f"probability per incident ≤ {JUDGEMENT['valve_minority_trip_max']}; KEEP unless "
                   "> materiality.",
                   primary="valve.split_blocks",
                   constraints=("valve_natural", "valve_minority"),
                   report=("valve.split_blocks", "valve.minority_trip", "valve.natural_trips_per_year"),
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
        for k in ("activationThreshold", "participationFloor", "enforcementFloor", "enforcementResume"):
            v = int(out.get(k, base[k]))
            out[k] = round(v * W / int(base["signalWindow"])) if k in changes else scaled[k]
        return out

    # -- space -----------------------------------------------------------------------------------
    def space(self, base: ParamSet, budget: Budget) -> Iterable[ParamSet]:
        fine = budget.name != "quick"
        out = [base]
        W0 = int(base["signalWindow"])
        lo, hi = REGISTRY["signalWindow"].bounds
        for W in range(lo, hi + 1, 288 if fine else 576):
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
        p0 = expected_share(env)
        pd = float(pol.detection_drop_share)
        S, sprov = share_samples(env, W)
        hours = BLOCKS_PER_YEAR / BLOCKS_PER_HOUR
        # expected halted fraction per share sample (rate × duration, capped by P(count < resume))
        part_h = float(np.mean(halted_fraction(PF, T, W, S))) * hours
        enf_h = float(np.mean(halted_fraction(EF, ER, W, S))) * hours
        fh = part_h + enf_h
        res = JUDGEMENT["false_halt_resolution_hours"]
        fh_q = 0.0 if fh < res / 2 else round(fh / res) * res
        dc = downcrossings_per_block(PF, W, S) + downcrossings_per_block(EF, W, S)
        flaps = float(np.mean(dc)) * BLOCKS_PER_YEAR
        q = JUDGEMENT["detection_quantile"]
        det_enf = detection_quantile_blocks(p0, pd, W, EF, q)
        det_part = detection_quantile_blocks(p0, pd, W, PF, q)
        from ybcal.sim import activation as act

        fluid = act.detection_delay_approx(p0, pd, W, EF)
        p_lock = float(1.0 - p_below(T, W, [p0])[0])
        act_blocks = W - 1 + D
        v_min = act.valve_trip_probability(1.0 - p0, V)
        v_nat = act.natural_fork_trip_rate(float(pol.orphan_rate), V)
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
            "valve.natural_trips_per_year": v_nat,
            "abandon.false_prob": fa, "runbook_blocks": float(runbook),
        }
        cons = {
            "false_halt": fh <= float(pol.max_false_halt_hours_per_year),
            "flaps": flaps <= float(pol.max_flaps_per_year),
            "detect_enf": det_enf <= int(pol.max_detection_blocks),
            "detect_part": det_part <= int(pol.max_detection_blocks),
            "majority": 2 * EF >= W,
            "activation_reach": p_lock >= float(getattr(pol, "activation_reliability",
                                                         JUDGEMENT["activation_reliability"])),
            "upgrade_window": int(pol.operator_upgrade_window_blocks) <= D,
            "valve_natural": v_nat <= float(pol.max_valve_false_trips_per_year),
            "valve_minority": v_min <= float(getattr(pol, "valve_minority_trip_max",
                                                     JUDGEMENT["valve_minority_trip_max"])),
        }
        meta = {"seed": env.seed, "budget": env.budget.name, "budget_paths": env.budget.paths,
                "budget_days": env.budget.block_horizon_days, "out_dir": env_out_dir(env),
                "share_provenance": sprov, "expected_share": p0, "drop_share": pd,
                "orphan_rate": float(pol.orphan_rate)}
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
    # 4. the hashrate-drop-45 scenario (enforcing share 0.80 → 0.45 at day 30, back over days 75–80)
    try:
        from ybcal.data import scenarios as sc

        scen = sc.load_library()["hashrate-drop-45"]
        run = scen.generate(rng, n_paths=1, resolution="block")
        share = np.asarray(run.schedules["enforcing_share"], dtype=float)
        share = share[0] if share.ndim == 2 else share
        m = max(8, paths // 4)
        sg = rng.random((m, share.size)) < share[None, :]
        ss = act.simulate(ps, sg, start_height=0, height0=0, enforce_until=0)
        drop_at = 30 * BLOCKS_PER_DAY
        enf = ss.enforcement_halt
        first = np.where(enf[:, drop_at:].any(axis=1), np.argmax(enf[:, drop_at:], axis=1), -1)
        hit = first[first >= 0]
        res["scenario_detect_blocks_p95"] = float(np.quantile(hit, 0.95)) if len(hit) else math.inf
        res["scenario_detected_fraction"] = float((first >= 0).mean())
        end = enf[:, -1]
        res["scenario_enforcement_restored_fraction"] = float((~end).mean())
        res["scenario_max_halt_run_blocks"] = float(act._run_lengths(enf).max())
    except Exception as e:  # pragma: no cover - scenario library optional
        res["scenario_error"] = f"{type(e).__name__}: {e}"
    return res


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

"""Joint pass: coordinate descent over the groups, then joint sensitivity (PLAN §5.10).

Owner: WP-8.

**Coordinate descent** (:func:`joint_pass`). Groups run in ``GROUP_ORDER`` (G1 → G2 → G5 → G3 → G4 →
G7 → G6 → G8 → G9, then the release check R). Each group's study starts from the *current joint
set* (the shipped set with every earlier group's recommendation applied, derived values recomputed),
so later groups see earlier choices (G1 windows feed G3's oracle, G2 scales G3 ratios, …). After a
full round, if no value moved the pass has converged; otherwise another round runs, up to
``policy.max_rounds_joint`` (3). One :class:`EvalCache` is shared, so a group whose inputs did not
change in a later round is answered from the cache. A study that cannot be imported or has no
``make_study`` is reported as *not run*; one that raises is reported as *error*; the pass never
crashes on a study. A group whose recommendations would make the joint set violate an invariant
is not applied (the set stays as it was, and the outcome says why).

The final Recommendations are re-stated against the **shipped** set: ``current`` becomes the
shipped value, ``recommended`` the joint set's value, and a KEEP/CHANGE label that a later-round
comparison made relative to an intermediate set is corrected (PROVISIONAL and BLOCKED are kept).

**Joint sensitivity** (:func:`joint_sensitivity`). Four cheap top-level risk metrics
(:class:`TopRiskModel`) are computed for sets around the joint recommended set:

* ``bad_debt_prob`` — system bad-debt probability: the mean over classes A/B/C of P(collateral
  below debt when the claim path opens), WP-4's hour-mode fast path (``metrics.p_bad_debt_fast``)
  on an ideal oracle kernel over GARCH-t paths (or a block bootstrap of real hourly data);
* ``price_halt_h_per_year`` — minting availability: hours per year with NO_PRICE or HALT-3 set in
  hour mode;
* ``false_halt_h_per_year`` — expected false PARTICIPATION + ENFORCEMENT halt hours per year at the
  policy's enforcing share (the G5 analytic estimator);
* ``attack_share`` — the smallest colluding hash share that controls a price median (V16, exact
  binomial, the minimum over the three windows).

Factors are ±1 registry step around the recommended value of each tunable parameter. Sobol
(Saltelli/Jansen) or Morris runs over those factors; when ``n·(k + 2)`` would exceed the budget's
evaluation cap, parameters are grouped by study group (one factor moves the whole group by the same
number of steps). A one-at-a-time ±1-step sweep (the tornado) always runs per parameter. A
parameter is *insensitive* when, for every metric, its factor's total-order index is below
``policy.insensitive_total_order`` — or, for a grouped factor that is sensitive, its own tornado share
is below the same threshold.
"""

from __future__ import annotations

import copy
import importlib
import inspect
import math
import re
import sys
import time
import traceback
import warnings
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

from ybcal.optimize.evaluate import EvalCache, evaluate_many
from ybcal.optimize.runner import GroupRun, recommended_set, run_group
from ybcal.optimize.sensitivity import Factor, morris, sobol
from ybcal.params.invariants import Context
from ybcal.params.paramset import ParamSet
from ybcal.params.registry import REGISTRY, params_for_group
from ybcal.studies.base import GROUP_ORDER, STUDY_MODULES, Env, Metrics, Recommendation, Study, load_study
from ybcal.units import BLOCKS_PER_HOUR, BLOCKS_PER_YEAR

OWNER_WP = "WP-8"

Status = Literal["ok", "not-run", "error"]
Loader = Callable[[str], Study]

__all__ = [
    "TOP_METRICS",
    "DesignNote",
    "GroupOutcome",
    "JointResult",
    "RoundRecord",
    "SensitivityResult",
    "TopRiskModel",
    "collect_design_notes",
    "joint_pass",
    "joint_sensitivity",
]


# ===================================================================================================
# Coordinate descent


@dataclass
class GroupOutcome:
    """What happened to one group in the latest round it ran."""

    group: str
    status: Status
    reason: str = ""
    run: GroupRun | None = None
    seconds: float = 0.0
    round: int = 0
    applied: bool = True  #: its recommendations were applied to the joint set
    design_notes: list[Any] = field(default_factory=list)
    rec_metrics: Metrics | None = None  #: the study's Metrics at the recommended set (cache hit)

    @property
    def recommendations(self) -> list[Recommendation]:
        """The group's Recommendations (empty unless ``ok``)."""
        return list(self.run.recommendations) if self.run is not None else []

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready summary."""
        return {
            "group": self.group,
            "status": self.status,
            "reason": self.reason,
            "round": self.round,
            "seconds": round(self.seconds, 3),
            "applied": self.applied,
            "summary": self.run.summary() if self.run is not None else "",
            "warnings": list(self.run.warnings) if self.run is not None else [],
            "design_notes": list(self.design_notes),
        }


@dataclass
class RoundRecord:
    """One coordinate-descent round."""

    index: int
    moves: dict[str, tuple[Any, Any]]  #: param → (value before the round, after)
    group_moves: dict[str, dict[str, tuple[Any, Any]]]  #: group → its moves in this round
    statuses: dict[str, str]
    seconds: float

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready."""
        return {
            "index": self.index,
            "moves": {k: list(v) for k, v in self.moves.items()},
            "group_moves": {g: {k: list(v) for k, v in m.items()} for g, m in self.group_moves.items()},
            "statuses": dict(self.statuses),
            "seconds": round(self.seconds, 3),
        }


@dataclass(frozen=True)
class DesignNote:
    """A finding that tuning cannot fix, raised for the owner (PLAN §7 item 5).

    Studies give either a plain string or a dict ``{id, title, finding, evidence, consequence, fix,
    params}`` (G3/G4/G6/G7/G9/release); dicts are de-duplicated by ``id``."""

    text: str  #: the finding (or the whole note for plain strings)
    groups: tuple[str, ...]
    params: tuple[str, ...] = ()
    id: str = ""
    title: str = ""
    evidence: tuple[tuple[str, str], ...] = ()
    consequence: str = ""
    fix: str = ""

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready."""
        return {
            "id": self.id,
            "title": self.title,
            "text": self.text,
            "groups": list(self.groups),
            "params": list(self.params),
            "evidence": dict(self.evidence),
            "consequence": self.consequence,
            "fix": self.fix,
        }


@dataclass
class JointResult:
    """The joint pass: final set, final-round Recommendations, history."""

    base: ParamSet
    recommended: ParamSet
    recommendations: dict[str, Recommendation]
    outcomes: dict[str, GroupOutcome]
    rounds: list[RoundRecord]
    converged: bool
    design_notes: list[DesignNote] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    cache: EvalCache | None = None
    seconds: float = 0.0

    def groups_with(self, status: Status) -> list[str]:
        """Groups whose outcome has ``status``."""
        return [g for g, o in self.outcomes.items() if o.status == status]

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready summary (without the result tables)."""
        return {
            "converged": self.converged,
            "rounds": [r.to_dict() for r in self.rounds],
            "outcomes": {g: o.to_dict() for g, o in self.outcomes.items()},
            "changes": {
                k: [a, b] for k, (a, b) in self.base.diff(self.recommended).items() if k != "network"
            },
            "design_notes": [d.to_dict() for d in self.design_notes],
            "warnings": list(self.warnings),
            "seconds": round(self.seconds, 3),
        }


_DESIGN_NOTE = re.compile(r"^\s*design note\s*[:—-]\s*(.+)$", re.I | re.S)


def _norm_note(t: str) -> str:
    return re.sub(r"\s+", " ", t.strip().lower().rstrip("."))


def _note_key(n: Any) -> str:
    if isinstance(n, Mapping):
        if n.get("id"):
            return "id:" + str(n["id"])
        return _norm_note(f"{n.get('title', '')} {n.get('finding', '')}")
    return _norm_note(str(n))


def collect_design_notes(outcomes: Mapping[str, GroupOutcome]) -> list[DesignNote]:
    """Aggregated, de-duplicated design notes: ``Recommendation.metrics["design_notes"]`` of every
    recommendation, notes starting with "Design note:", and each study module's
    ``design_notes(results[, policy])``. Each note is a string or a dict (``id``, ``title``,
    ``finding``, ``evidence``, ``consequence``, ``fix``, ``params``; de-duplicated by ``id``); order =
    group order, then first appearance."""
    seen: dict[str, tuple[Any, list[str], list[str]]] = {}
    for g, o in outcomes.items():
        notes: list[tuple[Any, str | None]] = [(n, None) for n in o.design_notes]
        for r in o.recommendations:
            dn = r.metrics.get("design_notes") if isinstance(r.metrics, Mapping) else None
            if isinstance(dn, (str, Mapping)):
                dn = [dn]
            for n in dn or ():
                notes.append((n, r.param))
            for n in r.notes:  # studies that predate the metrics["design_notes"] convention
                m = _DESIGN_NOTE.match(str(n))
                if m:
                    notes.append((m.group(1), r.param))
        for note, param in notes:
            if isinstance(note, Mapping):
                if not (note.get("finding") or note.get("title")):
                    continue
                extra = [str(x) for x in note.get("params") or ()]
            else:
                note = str(note).strip()
                if not note:
                    continue
                extra = []
            k = _note_key(note)
            if k not in seen:
                seen[k] = (note, [g], [])
            if g not in seen[k][1]:
                seen[k][1].append(g)
            for p in ([param] if param else []) + extra:
                if p not in seen[k][2]:
                    seen[k][2].append(p)
    out = []
    for note, gs, ps in seen.values():
        if isinstance(note, Mapping):
            ev = note.get("evidence") or {}
            evt = (
                tuple((str(k), str(v)) for k, v in ev.items())
                if isinstance(ev, Mapping)
                else (("", str(ev)),)
            )
            out.append(
                DesignNote(
                    str(note.get("finding") or note.get("title")),
                    tuple(gs),
                    tuple(ps),
                    str(note.get("id") or ""),
                    str(note.get("title") or ""),
                    evt,
                    str(note.get("consequence") or ""),
                    str(note.get("fix") or ""),
                )
            )
        else:
            out.append(DesignNote(note, tuple(gs), tuple(ps)))
    return out


def _module_design_notes(study: Study, run: GroupRun, policy: Any = None) -> list[Any]:
    mod = sys.modules.get(type(study).__module__)
    fn = getattr(mod, "design_notes", None) if mod is not None else None
    if not callable(fn):
        return []
    try:
        try:
            nargs = len(inspect.signature(fn).parameters)
        except (TypeError, ValueError):
            nargs = 1
        out = fn(run.table, policy) if nargs >= 2 else fn(run.table)
    except Exception as e:  # a broken helper must not sink the report
        return [f"(design_notes() of {getattr(mod, '__name__', '?')} failed: {e})"]
    if isinstance(out, (str, Mapping)):
        return [out]
    return list(out or [])


def _load(group: str, loader: Loader) -> tuple[Study | None, Status, str]:
    try:
        return loader(group), "ok", ""
    except NotImplementedError as e:
        return None, "not-run", str(e) or "not implemented"
    except ModuleNotFoundError as e:
        return None, "not-run", f"module missing: {e}"
    except Exception as e:
        return None, "error", f"{type(e).__name__}: {e}"


def _restate(
    rec: Recommendation, base: ParamSet, final: ParamSet, rnd: int, first: Recommendation | None = None
) -> Recommendation:
    """Re-state one Recommendation against the shipped set and the final joint set. When a later
    round compared against an intermediate value, the metrics "at the current value" are taken
    from the group's first-round recommendation (where current = shipped)."""
    p = rec.param
    shipped = base[p]
    if rec.current != shipped:
        rec.notes.append(
            f"Joint pass: the study compared candidates against {rec.current} (the round-{rnd} "
            f"joint set); the shipped value is {shipped}."
        )
        rec.current = shipped
        if (
            first is not None
            and isinstance(rec.metrics, dict)
            and isinstance(first.metrics, Mapping)
            and "current" in first.metrics
        ):
            rec.metrics = {**rec.metrics, "current": first.metrics["current"]}
            rec.notes.append(
                "Joint pass: metrics at the current value are from round 1 (other groups then "
                "at their round-1 values)."
            )
    want = final[p]
    if rec.recommended != want:
        rec.notes.append(
            f"Joint pass: the group proposed {rec.recommended}; the joint set holds {want} "
            "(derived from its parent, or the group's change was not applied)."
        )
        rec.recommended = want
    if rec.verdict == "KEEP" and rec.recommended != shipped:
        rec.verdict = "CHANGE"
    elif rec.verdict == "CHANGE" and rec.recommended == shipped:
        rec.verdict = "KEEP"
    return rec


def joint_pass(
    base: ParamSet,
    env: Env,
    *,
    groups: Sequence[str] | None = None,
    max_rounds: int | None = None,
    cache: EvalCache | None = None,
    workers: int | None = None,
    loader: Loader | None = None,
    method: str = "space",
    context: Context | None = None,
    on_event: Callable[[str], None] | None = None,
) -> JointResult:
    """Coordinate descent over ``groups`` (default every group, in ``GROUP_ORDER``); see the module
    docstring. ``loader`` defaults to :func:`load_study` (tests pass toy studies)."""
    t_all = time.perf_counter()
    loader = loader or load_study
    order = [g for g in GROUP_ORDER if groups is None or g in groups]
    order += [g for g in (groups or ()) if g not in order]
    rounds_max = int(max_rounds if max_rounds is not None else env.policy.max_rounds_joint)
    cache = cache if cache is not None else EvalCache()
    ctx = context if context is not None else Context.from_policy(env.policy)
    say = on_event or (lambda _m: None)
    studies: dict[str, Study | None] = {}
    outcomes: dict[str, GroupOutcome] = {}
    for g in order:
        st, status, reason = _load(g, loader)
        studies[g] = st
        if st is None:
            outcomes[g] = GroupOutcome(g, status, reason)
            say(f"{g}: {status} ({reason})")
    current = base
    first_recs: dict[str, Recommendation] = {}
    rounds: list[RoundRecord] = []
    warnings: list[str] = []
    converged = False
    for rnd in range(1, max(1, rounds_max) + 1):
        t_r = time.perf_counter()
        start = current
        group_moves: dict[str, dict[str, tuple[Any, Any]]] = {}
        statuses: dict[str, str] = {}
        for g in order:
            st = studies[g]
            if st is None:
                statuses[g] = outcomes[g].status
                continue
            t = time.perf_counter()
            try:
                run = run_group(
                    st,
                    current,
                    env,
                    workers=workers,
                    cache=cache,
                    method=method,  # type: ignore[arg-type]
                    context=ctx,
                )
            except Exception as e:
                tb = traceback.format_exc(limit=4)
                err = f"{type(e).__name__}: {e}"
                warnings.append(f"round {rnd}: {g} raised {err}\n{tb}")
                prev = outcomes.get(g)
                if prev is not None and prev.status == "ok":
                    # keep the last successful round's result; the joint set keeps its values
                    prev.reason = (prev.reason + "; " if prev.reason else "") + (
                        f"round {rnd} re-run failed ({err}); round {prev.round} result kept"
                    )
                    for r in prev.recommendations:
                        r.notes.append(
                            f"Joint pass: the round-{rnd} re-run of {g} failed ({err}); the round-"
                            f"{prev.round} recommendation is reported."
                        )
                    statuses[g] = "error (kept earlier round)"
                else:
                    outcomes[g] = GroupOutcome(g, "error", err, None, time.perf_counter() - t, rnd)
                    statuses[g] = "error"
                say(f"round {rnd} {g}: error {err}")
                continue
            applied, reason = True, ""
            try:
                new = recommended_set(current, run.recommendations)
                viol = new.check(ctx)
                if viol:
                    applied, reason = (
                        False,
                        "not applied: joint set would violate "
                        + ", ".join(sorted({v.invariant for v in viol})),
                    )
                    new = current
            except (KeyError, TypeError, ValueError) as e:
                applied, reason, new = False, f"not applied: {e}", current
            if not applied:
                warnings.append(f"round {rnd}: {g} {reason}")
            mv = {k: (a, b) for k, (a, b) in current.diff(new).items() if k != "network"}
            if mv:
                group_moves[g] = mv
            current = new
            try:
                rm = evaluate_many(st.evaluate, [run.recommended], env, workers=1, cache=cache)[0]
            except Exception:
                rm = None
            outcomes[g] = GroupOutcome(
                g,
                "ok",
                reason,
                run,
                time.perf_counter() - t,
                rnd,
                applied,
                _module_design_notes(st, run, env.policy),
                rm,
            )
            for r in run.recommendations:
                if r.param not in first_recs and r.current == base.get(r.param):
                    first_recs[r.param] = Recommendation(
                        **{**r.__dict__, "metrics": copy.deepcopy(r.metrics)}
                    )
            statuses[g] = "ok"
            say(f"round {rnd} {run.summary()}" + (f"; moved {', '.join(mv)}" if mv else "; no move"))
        moves = {k: (a, b) for k, (a, b) in start.diff(current).items() if k != "network"}
        rounds.append(RoundRecord(rnd, moves, group_moves, statuses, time.perf_counter() - t_r))
        if not moves:
            converged = True
            break
    recs: dict[str, Recommendation] = {}
    for g in order:
        o = outcomes[g]
        for r in o.recommendations:
            if r.param not in REGISTRY:
                continue
            fr = first_recs.get(r.param)
            if (
                o.round > 1
                and fr is not None
                and fr.recommended == current[r.param]
                and fr.current == base[r.param]
                and fr.verdict != "BLOCKED"
            ):
                # decided in round 1, confirmed later: report the round-1 decision (made against the
                # shipped value, so its metrics and explanation compare current with recommended)
                fr.notes.append(
                    f"Joint pass: confirmed in round {o.round} (the study kept {fr.recommended} "
                    f"against the joint set, verdict {r.verdict})."
                )
                if "joint" in r.sensitivity:
                    fr.sensitivity["joint"] = r.sensitivity["joint"]
                recs[r.param] = _restate(fr, base, current, 1)
                continue
            before = (r.current, r.recommended, r.verdict)
            recs[r.param] = _restate(r, base, current, o.round, first_recs.get(r.param))
            if (r.current, r.recommended, r.verdict) != before and o.run is not None:
                st = studies.get(g)
                try:
                    r.explanation = st.explain(r, o.run.table) if st is not None else r.explanation
                except Exception as e:  # keep the study's own text
                    r.notes.append(f"explain() after re-statement failed: {e}")
    return JointResult(
        base,
        current,
        recs,
        outcomes,
        rounds,
        converged,
        collect_design_notes(outcomes),
        warnings,
        cache,
        time.perf_counter() - t_all,
    )


# ===================================================================================================
# Top-level risk metrics (fast)

#: metric → (label, minimise?)
TOP_METRICS: dict[str, tuple[str, bool]] = {
    "bad_debt_prob": ("system P(bad debt)", True),
    "price_halt_h_per_year": ("minting halted by price (h/yr)", True),
    "false_halt_h_per_year": ("false activation halts (h/yr)", True),
    "attack_share": ("oracle attack share", False),
}

_HOURLY: dict[tuple, Any] = {}
_MEDIANS: dict[tuple, np.ndarray] = {}
_RM: dict[tuple, Any] = {}
_SUBSTEPS = 2


def _risk_paths(env: Env) -> int:
    return int(max(4, min(32, env.budget.paths // 4)))


def _hourly(env: Env) -> tuple[tuple, np.ndarray, str]:
    """Hourly true paths ``(paths, hours)`` for the fast metrics (memoised per process)."""
    from ybcal.data import synthetic as SY
    from ybcal.studies.g1_price_windows import data_fingerprint, real_price

    n = _risk_paths(env)
    years = float(env.budget.hour_horizon_years)
    fp = data_fingerprint(env)
    key = ("hourly", env.seed, n, years, fp)
    hit = _HOURLY.get(key)
    if hit is None:
        rng = env.rng_for("joint-sensitivity", "paths")
        real = real_price(env)
        prov = "synthetic"
        model = SY.preset("garch")
        if real is not None:
            try:
                hp = real if real.resolution == "hour" else None
                if hp is not None:
                    model = SY.BlockBootstrap.fit(hp)
                    prov = "real-data"
            except Exception:
                model = SY.preset("garch")
        pp = SY.simulate_years(model, n, years, "hour", rng)
        hit = (np.asarray(pp.prices, dtype=np.int64), prov)
        _HOURLY.clear()
        _MEDIANS.clear()
        _RM.clear()
        _HOURLY[key] = hit
    return key, hit[0], hit[1]


def _median_hourly(key: tuple, ht: np.ndarray, window: int) -> np.ndarray:
    """Ideal-kernel hourly rolling lower median of ``window`` blocks (no lag, bias or noise), as
    ``engine.OracleTransferKernel.ideal(params, substeps=2).medians`` computes it, memoised per window."""
    from ybcal.model import vkernels as V
    from ybcal.units import PRICE_MAX, PRICE_MIN

    mk = (key, window)
    if mk in _MEDIANS:
        return _MEDIANS[mk]
    S = _SUBSTEPS
    if key not in _RM:
        h = np.log(np.maximum(ht.astype(np.float64), 1.0))
        prev = np.concatenate([h[:, :1], h[:, :-1]], axis=1)
        frac = (np.arange(S) + 1) / S
        fine = (prev[:, :, None] + (h - prev)[:, :, None] * frac[None, None, :]).reshape(h.shape[0], -1)
        q = np.rint((fine - fine.min()) * 1e6).astype(np.int64) + 1
        _RM[key] = (V.RollingMedian(q), float(fine.min()))
    rm, fmin = _RM[key]
    n_h = ht.shape[1]
    span = max(1, round(window * S / BLOCKS_PER_HOUR))
    med = rm.median(span, 1)
    ends = np.arange(n_h) * S + S - 1
    lg = (med[:, ends] - 1) / 1e6 + fmin
    p = np.clip(np.rint(np.exp(lg)), PRICE_MIN, PRICE_MAX).astype(np.int64)
    warm = (np.arange(n_h) + 1) * BLOCKS_PER_HOUR < math.ceil(window / 2)
    p[:, warm] = V.UNDEF
    _MEDIANS[mk] = p
    return p


@dataclass(frozen=True)
class TopRiskModel:
    """``fn(cand, env) -> Metrics`` of the four top-level risk metrics (see module docstring).

    Picklable (no state); memoises paths and medians per process, so ±1-step designs are cheap.
    ``n_terms`` / ``start_stride_h`` thin the bad-debt grid for speed."""

    n_terms: int = 4
    start_stride_h: int = 24

    def __call__(self, cand: ParamSet, env: Env) -> Metrics:
        from ybcal.model import vkernels as V
        from ybcal.sim import engine as E
        from ybcal.sim import metrics as M
        from ybcal.sim import oracle as O
        from ybcal.studies import g1_price_windows as G1
        from ybcal.studies import g5_activation as G5

        key, ht, prov = _hourly(env)
        pf = _median_hourly(key, ht, int(cand["pFastWindow"]))
        pm = _median_hourly(key, ht, int(cand["pMidWindow"]))
        ps = _median_hourly(key, ht, int(cand["pSlowWindow"]))
        xm = V.price_mint(pf, pm, ps)
        xc = V.price_claim(pm, ps)
        mask = np.where(xm == V.UNDEF, E.HALT_NO_PRICE, 0).astype(np.uint16)
        mask |= np.where(
            V.halt3_divergence(pf, pm, ps, int(cand["divergenceBps"])), E.HALT_DIVERGENCE, 0
        ).astype(np.uint16)
        hs = E.HourSeries(pf, pm, ps, xm, xc, E._hour_sigma(cand, pf), mask)
        skip = math.ceil((int(cand["volWindow"]) + int(cand["pSlowWindow"])) / BLOCKS_PER_HOUR)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # empty classes (horizon shorter than the term)
            fb = M.p_bad_debt_fast(
                cand,
                ht,
                hour_series=hs,
                n_terms=self.n_terms,
                start_stride=self.start_stride_h,
                skip_hours=skip,
                term_distribution=str(env.policy.term_distribution),
            )
        ps_ = [fb.p[c] for c in ("A", "B", "C") if fb.n.get(c, 0) > 0 and math.isfinite(fb.p[c])]
        bad = float(np.mean(ps_)) if ps_ else 0.0
        warm = math.ceil(int(cand["pSlowWindow"]) / BLOCKS_PER_HOUR)
        hm = mask[:, warm:]
        price_halt = float(np.mean(hm != 0)) * (BLOCKS_PER_YEAR / BLOCKS_PER_HOUR) if hm.size else 0.0
        g5 = G5.make_study().evaluate(cand, env)
        fh = float(g5.values["fh.hours"])
        tag = O.OracleConfig.from_policy(env.policy).tagging_share
        shares = []
        for w, f in (
            ("pFastWindow", "pFastMinFill"),
            ("pMidWindow", "pMidMinFill"),
            ("pSlowWindow", "pSlowMinFill"),
        ):
            s = G1.min_attack_share(int(cand[w]), int(cand[f]), float(tag))
            shares.append(s if math.isfinite(s) else float(tag))
        values = {
            "bad_debt_prob": bad,
            "price_halt_h_per_year": price_halt,
            "false_halt_h_per_year": fh,
            "attack_share": float(min(shares)),
            **{
                f"bad_debt_prob_{c}": float(fb.p[c])
                for c in fb.p
                if fb.n.get(c, 0) > 0 and math.isfinite(fb.p[c])
            },
        }
        return Metrics(
            values,
            "bad_debt_prob",
            True,
            {},
            "real-data" if prov == "real-data" else "synthetic",
            {"paths": int(ht.shape[0]), "hours": int(ht.shape[1])},
        )


# ===================================================================================================
# Joint sensitivity


def _neighbour_values(base: ParamSet, p: str) -> tuple[Any | None, Any | None]:
    spec = REGISTRY[p]
    v = base.as_int(p)
    lo, hi = spec.bounds
    a = v - spec.step if v - spec.step >= lo else None
    b = v + spec.step if v + spec.step <= hi else None
    return a, b


def default_sensitivity_params(base: ParamSet) -> list[str]:
    """Every tunable registry parameter (locked or excluded), registry order."""
    return [
        k
        for k, s in REGISTRY.items()
        if s.tunable and isinstance(base[k], int) and not isinstance(base[k], bool)
    ]


def _couple(base: ParamSet, ch: dict[str, Any]) -> dict[str, Any]:
    """Keep the moves admissible where a rule ties two values: class boundaries stay contiguous
    (``classMin[i+1] = classMax[i] + 1``) and ``abandonBlocks ≥ grace`` (W21)."""
    ch = dict(ch)
    for i in range(2):
        mx, mn = f"classMax[{i}]", f"classMin[{i + 1}]"
        if mx in ch and mn not in ch:
            ch[mn] = int(ch[mx]) + 1
        elif mn in ch and mx not in ch:
            ch[mx] = int(ch[mn]) - 1
    g = int(ch.get("grace", base["grace"]))
    if "abandonBlocks" not in ch and g > base.as_int("abandonBlocks"):
        ch["abandonBlocks"] = g
    return ch


def build_move(base: ParamSet, ch: Mapping[str, Any], context: Context | None) -> ParamSet | None:
    """``base`` with the moves ``ch`` (coupled); if that set fails an invariant, the moves are added
    one at a time (in the given order) and each kept only while the set stays admissible. ``None``
    when nothing admissible remains."""
    full = _couple(base, dict(ch))
    try:
        ps = base.replace(full) if full else base
        if not ps.check(context):
            return ps
    except (TypeError, ValueError, KeyError):
        pass
    keep: dict[str, Any] = {}
    for k, v in ch.items():
        trial = _couple(base, {**keep, k: v})
        try:
            ps = base.replace(trial)
        except (TypeError, ValueError, KeyError):
            continue
        if not ps.check(context):
            keep[k] = v
    if not keep:
        return None
    return base.replace(_couple(base, keep))


class _OffsetObjective:
    """``f(X) -> y`` where column ``j`` is an offset in steps (−1/0/+1) applied to every param of
    factor ``j`` (through :func:`build_move`); ``metric`` may be switched between calls."""

    def __init__(
        self,
        fn,
        env: Env,
        base: ParamSet,
        factors: Mapping[str, Sequence[str]],
        metric: str,
        cache: EvalCache,
        workers: int | None,
        context: Context | None,
    ) -> None:
        self.fn, self.env, self.base, self.metric = fn, env, base, metric
        self.factors = list(factors.items())
        self.cache, self.workers, self.context = cache, workers, context
        self.invalid = 0
        self._sets: dict[tuple[int, ...], ParamSet | None] = {}

    def paramset(self, row: Sequence[float]) -> ParamSet | None:
        key = tuple(round(x) for x in row)
        if key in self._sets:
            return self._sets[key]
        ch: dict[str, int] = {}
        for (_, params), k in zip(self.factors, key, strict=True):
            if k == 0:
                continue
            for p in params:
                a, b = _neighbour_values(self.base, p)
                v = a if k < 0 else b
                if v is not None:
                    ch[p] = v
        ps = build_move(self.base, ch, self.context) if ch else self.base
        self._sets[key] = ps
        return ps

    def __call__(self, X: np.ndarray) -> np.ndarray:
        X = np.atleast_2d(X)
        y = np.full(X.shape[0], math.nan)
        idx, sets = [], []
        for i, row in enumerate(X):
            ps = self.paramset(row)
            if ps is None:
                self.invalid += 1
                continue
            idx.append(i)
            sets.append(ps)
        ms = evaluate_many(self.fn, sets, self.env, workers=self.workers, cache=self.cache)
        for i, m in zip(idx, ms, strict=True):
            y[i] = float(m.values[self.metric])
        return y


@dataclass
class SensitivityResult:
    """Joint sensitivity around a set (see module docstring)."""

    method: str
    metrics: list[str]
    params: list[str]
    factors: dict[str, list[str]]  #: factor → params it moves
    grouped: bool
    indices: dict[str, dict[str, dict[str, Any]]]  #: metric → factor → {"ST","S1",…} or {"mu_star",…}
    base_values: dict[str, float]
    tornado: dict[str, dict[str, dict[str, Any]]]  #: metric → param → {"lo","hi","y_lo","y_hi"}
    per_param: dict[str, dict[str, Any]]
    threshold: float
    n_evals: int = 0
    n_invalid: int = 0
    seconds: float = 0.0
    notes: list[str] = field(default_factory=list)
    base_metrics: dict[str, float] = field(default_factory=dict)  #: every value of fn at the set

    def insensitive(self) -> list[str]:
        """Parameters labelled insensitive."""
        return [p for p, d in self.per_param.items() if d.get("insensitive")]

    def sentence(self, param: str) -> str:
        """The report's joint-sensitivity sentence for one parameter."""
        d = self.per_param.get(param)
        if d is None:
            return "not part of the joint sensitivity analysis."
        idx = d.get("index", {})
        key = "ST" if self.method == "sobol" else "share"
        parts = [f"{TOP_METRICS[m][0]} {idx.get(m, 0.0):.2f}" for m in self.metrics]
        lead = "Insensitive at ±1 step: " if d.get("insensitive") else "Joint sensitivity at ±1 step: "
        how = (
            f"{key} of factor '{d['factor']}' (grouped)" if self.grouped and d.get("factor") != param else key
        )
        dom = d.get("dominant")
        tail = f"; it moves {TOP_METRICS[dom][0]} most" if dom and not d.get("insensitive") else ""
        return f"{lead}{how} — {', '.join(parts)}{tail}."

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready."""
        return {
            "method": self.method,
            "metrics": self.metrics,
            "params": self.params,
            "factors": self.factors,
            "grouped": self.grouped,
            "indices": self.indices,
            "base_values": self.base_values,
            "tornado": self.tornado,
            "per_param": self.per_param,
            "threshold": self.threshold,
            "n_evals": self.n_evals,
            "n_invalid": self.n_invalid,
            "seconds": round(self.seconds, 3),
            "notes": self.notes,
            "base_metrics": self.base_metrics,
        }


def _jsonable(x: Any) -> Any:
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, (np.floating, np.integer)):
        return x.item()
    return x


def joint_sensitivity(
    base: ParamSet,
    env: Env,
    *,
    method: Literal["sobol", "morris"] = "sobol",
    params: Sequence[str] | None = None,
    metrics: Sequence[str] | None = None,
    fn: Callable[[ParamSet, Env], Metrics] | None = None,
    grouping: Literal["auto", "param", "group"] = "auto",
    max_evals: int | None = None,
    n: int | None = None,
    workers: int | None = None,
    cache: EvalCache | None = None,
    context: Context | None = None,
    tornado: bool = True,
) -> SensitivityResult:
    """Sobol or Morris at ±1 step around ``base`` plus a per-parameter tornado (module docstring).

    ``max_evals`` (default ``20 · budget.sobol_samples``) caps the Sobol design: above it, factors are
    study groups (``grouping="auto"``). ``fn`` defaults to :class:`TopRiskModel`; any ``fn`` whose
    Metrics carry every name in ``metrics`` works (tests use toys)."""
    t0 = time.perf_counter()
    fn = fn or TopRiskModel()
    mets = list(metrics or TOP_METRICS)
    names = list(params) if params is not None else default_sensitivity_params(base)
    names = [p for p in names if p in REGISTRY and REGISTRY[p].step > 0]
    cache = cache if cache is not None else EvalCache()
    ctx = context if context is not None else Context.from_policy(env.policy)
    thr = float(env.policy.insensitive_total_order)
    nn = int(n or env.budget.sobol_samples)
    cap = int(max_evals if max_evals is not None else 20 * env.budget.sobol_samples)
    n2 = 2 ** max(1, math.ceil(math.log2(max(nn, 2))))
    grouped = grouping == "group" or (
        grouping == "auto" and method == "sobol" and n2 * (len(names) + 2) > cap
    )
    if grouped:
        factors: dict[str, list[str]] = {}
        for p in names:
            factors.setdefault(f"{REGISTRY[p].group} group", []).append(p)
    else:
        factors = {p: [p] for p in names}
    flist = []
    for f, ps in factors.items():
        lv = {0}
        for p in ps:
            a, b = _neighbour_values(base, p)
            if a is not None:
                lv.add(-1)
            if b is not None:
                lv.add(1)
        flist.append(Factor.discrete(f, sorted(lv)))
    seed = int(env.rng_for("joint-sensitivity", method).integers(0, 2**31 - 1))
    indices: dict[str, dict[str, dict[str, Any]]] = {}
    invalid = 0
    obj = _OffsetObjective(fn, env, base, factors, mets[0], cache, workers, ctx)
    for m in mets:
        obj.metric = m
        obj.invalid = 0
        if method == "sobol":
            res = sobol(obj, flist, nn, seed=seed, n_boot=100)
            indices[m] = {f: {k: _jsonable(v) for k, v in d.items()} for f, d in res.to_dict().items()}
        else:
            r = int(env.budget.morris_trajectories)
            mres = morris(obj, flist, r, levels=3, seed=seed)
            tot = float(np.nansum(mres.mu_star)) or 1.0
            indices[m] = {
                f: {
                    **{k: _jsonable(v) for k, v in d.items()},
                    "share": float(d["mu_star"]) / tot if math.isfinite(d["mu_star"]) else 0.0,
                }
                for f, d in mres.to_dict().items()
            }
        invalid = max(invalid, obj.invalid)
    base_m = evaluate_many(fn, [base], env, workers=1, cache=cache)[0]
    base_values = {m: float(base_m.values[m]) for m in mets}
    torn: dict[str, dict[str, dict[str, Any]]] = {m: {} for m in mets}
    if tornado:
        sets, keys = [], []
        for p in names:
            a, b = _neighbour_values(base, p)
            for side, v in (("lo", a), ("hi", b)):
                if v is None:
                    continue
                ps = build_move(base, {p: v}, ctx)
                if ps is None or ps[p] != v:
                    continue
                sets.append(ps)
                keys.append((p, side, v))
        ms = evaluate_many(fn, sets, env, workers=workers, cache=cache)
        for (p, side, v), mm in zip(keys, ms, strict=True):
            for m in mets:
                d = torn[m].setdefault(p, {"lo": None, "hi": None, "y_lo": None, "y_hi": None})
                d[side] = v
                d[f"y_{side}"] = float(mm.values[m])
    key = "ST" if method == "sobol" else "share"
    per: dict[str, dict[str, Any]] = {}
    for f, ps in factors.items():
        for p in ps:
            idx = {m: max(0.0, float(indices[m][f].get(key, 0.0) or 0.0)) for m in mets}
            share: dict[str, float] = {}
            for m in mets:
                tot = (
                    sum(
                        abs(
                            (d.get("y_hi") if d.get("y_hi") is not None else base_values[m])
                            - (d.get("y_lo") if d.get("y_lo") is not None else base_values[m])
                        )
                        for d in torn[m].values()
                    )
                    or 0.0
                )
                d = torn[m].get(p, {})
                eff = abs(
                    (d.get("y_hi") if d.get("y_hi") is not None else base_values[m])
                    - (d.get("y_lo") if d.get("y_lo") is not None else base_values[m])
                )
                share[m] = eff / tot if tot > 0 else 0.0
            if grouped:
                ins = all(idx[m] < thr or (tornado and share[m] < thr) for m in mets)
            else:
                ins = all(idx[m] < thr for m in mets)
            score = {m: (share[m] if grouped else idx[m]) for m in mets}
            dom = max(score, key=score.__getitem__) if any(v > 0 for v in score.values()) else None
            per[p] = {
                "factor": f,
                "index": idx,
                "oat_share": share,
                "insensitive": bool(ins),
                "dominant": dom,
            }
    notes = []
    if grouped:
        notes.append(
            f"{len(names)} parameters grouped into {len(factors)} study-group factors "
            f"(Saltelli design {n2}·(k+2) above the cap of {cap} evaluations)."
        )
    return SensitivityResult(
        method,
        mets,
        names,
        factors,
        grouped,
        indices,
        base_values,
        torn,
        per,
        thr,
        len(cache),
        invalid,
        time.perf_counter() - t0,
        notes,
        {k: float(v) for k, v in base_m.values.items()},
    )


def attach_sensitivity(recs: Mapping[str, Recommendation], sens: SensitivityResult) -> None:
    """Write the joint result into each Recommendation's ``sensitivity`` (``"joint"`` key) and add a
    note for insensitive parameters (the verdict is not overridden: D-WP8-3)."""
    for p, r in recs.items():
        d = sens.per_param.get(p)
        if d is None:
            continue
        r.sensitivity["joint"] = {
            **d,
            "method": sens.method,
            "grouped": sens.grouped,
            "sentence": sens.sentence(p),
        }
        if d.get("insensitive"):
            r.sensitivity["insensitive"] = True
            if r.recommended != r.current:
                r.notes.append(
                    "Joint sensitivity: insensitive on the four top-level risk metrics at ±1 step; "
                    "the change rests on the group study's own metric only."
                )


def group_params(group: str) -> tuple[str, ...]:
    """Registry parameters of a group (re-exported for the report)."""
    return params_for_group(group)


def study_module(group: str) -> Any | None:
    """The imported study module of ``group`` (or ``None``)."""
    try:
        return importlib.import_module(STUDY_MODULES[group])
    except Exception:
        return None

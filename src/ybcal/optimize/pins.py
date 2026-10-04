"""Owner-pinned parameters (D-RD-INF-2).

The policy key ``owner_pinned`` maps a parameter to the owner decision that fixed its value
(``abandonBlocks = "W21 (D-R-12)"``). A pinned parameter is still studied — the study searches it
like any other and its evidence is kept — but the value is **kept**:

1. the study decides on its full result table: that is the *evidence* (what the data alone says);
2. if the evidence moves a pinned value, the study decides again on the rows where every pinned
   parameter holds its current value, so the group's other parameters are chosen *given* the pin
   (the joint pass therefore treats pinned values as fixed);
3. each pinned Recommendation gets verdict KEEP, ``recommended = current``, and
   ``metrics["owner_pin"]``: the decision reference, the value and verdict the evidence points to,
   why (the evidence's binding constraint and decision), and the risk of keeping (constraints the
   kept value fails, its primary metric against the evidence value's).

The report renders the verdict as ``KEEP (owner decision W21)`` and, where the evidence points
elsewhere, says so prominently in the executive summary and the parameter section. A pinned value
never appears as CHANGE and never in ``params.cpp.patch``.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ybcal.params.paramset import ParamSet
from ybcal.params.registry import REGISTRY
from ybcal.studies.base import Recommendation, ResultRow, ResultTable, Study, relative_improvement

if TYPE_CHECKING:
    from ybcal.config import Policy

__all__ = ["Pin", "apply_owner_pins", "parse_pin", "pin_info", "pinned_for", "verdict_label"]


@dataclass(frozen=True)
class Pin:
    """One owner pin: the decision reference and, for a relative pin, the parent whose shipped
    fraction is kept (``activationThreshold`` = 75 % of ``signalWindow``)."""

    param: str
    ref: str
    of: str | None = None

    @property
    def fraction(self) -> float:
        if self.of is None:
            return 1.0
        return float(REGISTRY[self.param].mainnet) / float(REGISTRY[self.of].mainnet)  # type: ignore[arg-type]

    def target(self, ps: Mapping[str, Any], base: Mapping[str, Any]) -> Any:
        """The pinned value given the set ``ps`` (its parent) — ``base[param]`` for an absolute pin."""
        if self.of is None:
            return base[self.param]
        return round(self.fraction * int(ps[self.of]))

    def holds(self, ps: Mapping[str, Any], base: Mapping[str, Any]) -> bool:
        if self.of is None:
            return ps[self.param] == base[self.param]
        return abs(int(ps[self.param]) - self.fraction * int(ps[self.of])) <= 1.0

    @property
    def label(self) -> str:
        return self.ref if self.of is None else f"{self.ref}: {self.fraction:.0%} of {self.of}"


def parse_pin(param: str, v: Any) -> Pin:
    """A policy ``owner_pinned`` entry: ``"W21"`` or ``{ref = "L3", of = "signalWindow"}``."""
    if isinstance(v, Mapping):
        return Pin(param, str(v.get("ref", "?")), str(v["of"]) if v.get("of") else None)
    return Pin(param, str(v))


def pinned_for(policy: Policy | None, params: Sequence[str]) -> dict[str, Pin]:
    """``param → Pin`` for the pinned parameters among ``params``."""
    pins = getattr(policy, "owner_pinned", None) or {}
    return {p: parse_pin(p, pins[p]) for p in params if p in pins}


def pin_info(rec: Recommendation) -> Mapping[str, Any] | None:
    """``metrics["owner_pin"]`` of a Recommendation, if pinned."""
    m = rec.metrics if isinstance(rec.metrics, Mapping) else {}
    v = m.get("owner_pin")
    return v if isinstance(v, Mapping) else None


def verdict_label(rec: Recommendation | None, verdict: str) -> str:
    """The verdict as the report prints it: ``KEEP (owner decision W21)`` for a pinned parameter,
    ``KEEP — policy unmeetable in this environment (design note G1-ENV-1)`` for an
    environment-limited one (D-RD-INF-3), else the verdict itself."""
    if rec is None:
        return verdict
    pin = pin_info(rec)
    if pin is not None:
        return f"{verdict} (owner decision {pin.get('ref', '?')})"
    m = rec.metrics if isinstance(rec.metrics, Mapping) else {}
    envb = m.get("environment_blocked")
    if isinstance(envb, Mapping):
        note = envb.get("note") or "see §5"
        return f"{verdict} — policy unmeetable in this environment (design note {note})"
    return verdict


def _row_for(table: ResultTable, values: Mapping[str, Any], keys: Sequence[str]) -> ResultRow | None:
    for r in table:
        if all(r.params[k] == values[k] for k in keys):
            return r
    return None


def _fmt(x: Any) -> str:
    if isinstance(x, float):
        return f"{x:.4g}"
    return str(x)


def _risk(
    table: ResultTable,
    evidence: Mapping[str, Any],
    keys: Sequence[str],
    ev_rec: Recommendation | None = None,
) -> tuple[str, list[str], dict[str, Any]]:
    """(text, constraints failing at the kept value, metrics) — the risk of keeping the pinned
    values against the evidence's set. Constraints are those of the parameter's own rule
    (``constraints_current`` of the evidence Recommendation) when the study reports them; the
    study's own current-vs-recommended metrics are quoted where they differ."""
    cur = table.current(keys)
    ev = _row_for(table, evidence, keys)
    parts: list[str] = []
    failing: list[str] = []
    mets: dict[str, Any] = {}
    m = ev_rec.metrics if ev_rec is not None and isinstance(ev_rec.metrics, Mapping) else {}
    cc = m.get("constraints_current")
    own = set(cc) if isinstance(cc, Mapping) else set()
    if cur is not None:
        failing = [c for c in cur.metrics.violated if not own or c in own]
        mets["primary"] = cur.metrics.primary
        mets["primary_at_kept"] = cur.metrics.primary_value
        if failing:
            parts.append(f"at the kept value the study's constraint(s) {', '.join(failing)} fail")
    if cur is not None and ev is not None and ev is not cur:
        mets["primary_at_evidence"] = ev.metrics.primary_value
        imp = relative_improvement(
            cur.metrics.primary_value, ev.metrics.primary_value, minimize=cur.metrics.minimize
        )
        mets["evidence_improvement"] = imp
        if cur.metrics.primary_value != ev.metrics.primary_value and cur.metrics.primary != "zero":
            parts.append(
                f"{cur.metrics.primary} is {_fmt(cur.metrics.primary_value)} at the kept value vs "
                f"{_fmt(ev.metrics.primary_value)} at the evidence value"
                + (f" ({imp:+.1%})" if imp == imp and abs(imp) != float("inf") else "")
            )
        newly = [c for c in failing if ev.metrics.constraints.get(c, False)]
        if newly:
            mets["met_at_evidence"] = newly
            parts.append(f"the evidence value meets {', '.join(newly)}")
    a, b = m.get("current"), m.get("recommended")
    if isinstance(a, Mapping) and isinstance(b, Mapping):
        diff = [f"{k} {_fmt(a[k])} → {_fmt(b[k])}" for k in a if k in b and a[k] != b[k]
                and not isinstance(a[k], Mapping)][:4]
        if diff:
            parts.append("the study's metrics, kept → evidence: " + ", ".join(diff))
    return ("; ".join(parts) or "no measurable difference in this study"), failing, mets


def apply_owner_pins(
    study: Study,
    table: ResultTable,
    recs: Sequence[Recommendation],
    policy: Policy | None,
    base: ParamSet,
) -> tuple[list[Recommendation], list[str]]:
    """Apply the policy's owner pins to one group's decision (see the module docstring). Returns the
    final Recommendations and warnings."""
    names = tuple(getattr(study, "params", ()) or ())
    pins = pinned_for(policy, names)
    recs = list(recs)
    if not pins:
        return recs, []
    warns: list[str] = []
    by = {r.param: r for r in recs}
    evidence = {p: copy.deepcopy(by[p]) for p in pins if p in by}
    ev_values = {k: base[k] for k in names}
    ev_values.update({r.param: r.recommended for r in recs if r.param in ev_values})
    ev_full = {**base.to_dict(), **ev_values}
    moved = [p for p in evidence if not pins[p].holds(ev_full, base)]
    final = recs
    if moved:
        sub = table.filter(lambda row: all(pins[p].holds(row.params, base) for p in pins))
        try:
            if not len(sub) or sub.current(list(pins)) is None:
                raise ValueError("no evaluated row holds every pinned value")
            final = list(study.decide(sub, policy))  # type: ignore[arg-type]
        except Exception as e:  # keep the evidence decision for the others, pin the pinned
            warns.append(
                f"{study.group}: re-deciding with {', '.join(moved)} pinned failed ({type(e).__name__}: "
                f"{e}); the other parameters keep the unpinned decision"
            )
            final = recs
    fin = {**base.to_dict(), **{r.param: r.recommended for r in final}}
    out: list[Recommendation] = []
    for r in final:
        if r.param not in pins:
            if moved:
                r.notes.append(
                    "Owner pins: decided with "
                    + ", ".join(pins[p].label if pins[p].of else f"{p} = {base[p]}" for p in pins)
                    + " held "
                    "(policy owner_pinned)."
                )
            out.append(r)
            continue
        ev = evidence.get(r.param, r)
        risk_txt, failing, risk_m = _risk(table, ev_values, list(names), ev)
        pin = pins[r.param]
        ref = pin.label
        kept = pin.target(fin, base)
        elsewhere = r.param in moved
        dec = ev.metrics.get("decision") if isinstance(ev.metrics, Mapping) else None
        because = "; ".join(str(x) for x in (ev.binding, dec) if x and x != "—") or ev.rule
        info: dict[str, Any] = {
            "ref": ref,
            "kept": kept,
            "of": pin.of,
            "fraction": pin.fraction if pin.of else None,
            "evidence_value": ev.recommended,
            "evidence_verdict": ev.verdict,
            "evidence_points_elsewhere": bool(elsewhere),
            "evidence_blocked": ev.verdict == "BLOCKED",
            "because": because,
            "risk": risk_txt if (elsewhere or failing) else "none found: the evidence agrees",
            "failing_at_kept": failing,
            **{f"risk_{k}": v for k, v in risk_m.items()},
        }
        r.metrics = {**(r.metrics if isinstance(r.metrics, Mapping) else {}), "owner_pin": info}
        r.recommended = kept
        r.verdict = "KEEP" if kept == base[r.param] else "CHANGE"
        what = "the value is kept" if pin.of is None else f"the fraction of {pin.of} is kept"
        r.rule = f"Owner decision {ref}: {what}; the study still ran. {r.rule}".strip()
        if elsewhere:
            r.notes.insert(
                0,
                f"Owner pin {ref}: the evidence points to {ev.recommended} ({ev.verdict}) because "
                f"{because}; risk of keeping {kept}: {info['risk']}.",
            )
        elif ev.verdict == "BLOCKED":
            r.notes.insert(
                0,
                f"Owner pin {ref}: no evaluated value meets the policy ({because}); risk of keeping: "
                f"{info['risk']}.",
            )
        out.append(r)
    return out, warns

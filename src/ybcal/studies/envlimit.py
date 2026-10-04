"""Environment-limited constraints: a policy no parameter value can meet *here* (D-RD-INF-3).

``decide_with_materiality`` returns BLOCKED when no evaluated row satisfies every constraint. Two very
different situations end there:

* a **parameter failure** — some value would meet the constraint, but not one inside the searched
  bounds, or not together with the others: the owner must relax a tolerance or the rule must change;
* an **environment limit** — the real environment makes the constraint unmeetable by *any* value:
  ``attack_share_min`` 0.34 when one real pool mines 52 % of blocks (no window defeats it), or
  ``activation_reach`` when the enforcing share the policy expects (0.70) is below the threshold of
  every candidate. Reporting BLOCKED there hides a usable answer.

:func:`decide_with_environment` is a drop-in for ``decide_with_materiality``. A study names the
constraints the environment may make unmeetable (:class:`EnvironmentLimit`). When the ordinary
decision is BLOCKED and **every** constraint no evaluated row meets is one of those, it decides on the
rows that meet all the *other* constraints, by the limit's own least-harm objective (e.g. maximise
the attack share, maximise P(activation)) with the policy's materiality and minimal change. The
verdict is KEEP or CHANGE, and :attr:`EnvDecision.environment` carries what the report needs: the
constraints, why the environment defeats them, the design note id and the quantified exposure at
the chosen value. :func:`attach_environment` writes it into a Recommendation
(``metrics["environment_blocked"]`` plus a design note). The report prints
``KEEP — policy unmeetable in this environment (design note G1-ENV-1)``, lists it in the executive
summary with its exposure, and the lock-readiness checklist counts it apart from BLOCKED.

A constraint that is unmeetable but *not* declared environment-limited keeps the BLOCKED verdict.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from ybcal.studies.base import (
    MaterialityDecision,
    Recommendation,
    ResultRow,
    ResultTable,
    decide_with_materiality,
    relative_improvement,
)

if TYPE_CHECKING:
    from ybcal.config import Policy

__all__ = [
    "EnvDecision",
    "EnvironmentLimit",
    "attach_environment",
    "decide_with_environment",
    "env_info",
    "unmeetable_constraints",
]


@dataclass(frozen=True)
class EnvironmentLimit:
    """A constraint the real environment may make unmeetable, and how to choose instead."""

    constraint: str  #: the policy constraint name in ``Metrics.constraints``
    harm_metric: str  #: the least-harm objective among the rows that meet everything else
    minimize: bool  #: direction of ``harm_metric``
    note: str  #: design note id (e.g. ``"G1-ENV-1"``)
    why: str  #: why the environment defeats it ("the top pool mines 52 % > attack_share_min 0.34")
    #: quantified exposure at the chosen row: a string, or ``row -> str``
    exposure: str | Callable[[ResultRow], str] = ""
    title: str = ""  #: design-note title (default from the constraint)
    fix: str = ""  #: what would remove the limit (a rule change, an operational step)


@dataclass(frozen=True)
class EnvDecision:
    """A :class:`MaterialityDecision` plus the environment record (``None`` unless limited)."""

    row: ResultRow
    verdict: Literal["KEEP", "CHANGE", "BLOCKED"]
    improvement: float
    reason: str
    environment: dict[str, Any] | None = field(default=None)

    @classmethod
    def of(cls, d: MaterialityDecision) -> EnvDecision:
        return cls(d.row, d.verdict, d.improvement, d.reason, None)


def unmeetable_constraints(table: ResultTable) -> list[str]:
    """Constraints that no evaluated row satisfies (in first-seen order)."""
    names: list[str] = []
    for r in table:
        names.extend(c for c in r.metrics.constraints if c not in names)
    return [c for c in names if not any(r.metrics.constraints.get(c, True) for r in table)]


def _val(r: ResultRow, metric: str) -> float:
    return float(r.metrics.values.get(metric, float("nan")))


def decide_with_environment(
    table: ResultTable,
    materiality: float | Policy,
    limits: Sequence[EnvironmentLimit],
    *,
    params: Sequence[str] | None = None,
    metric: str | None = None,
    minimize: bool | None = None,
) -> EnvDecision:
    """``decide_with_materiality``, except that a BLOCKED caused only by declared environment limits
    becomes a least-harm KEEP/CHANGE with an environment record (see the module docstring)."""
    d = decide_with_materiality(table, materiality, params=params, metric=metric, minimize=minimize)
    if d.verdict != "BLOCKED" or not limits:
        return EnvDecision.of(d)
    unmet = unmeetable_constraints(table)
    by = {lim.constraint: lim for lim in limits}
    if not unmet or any(c not in by for c in unmet):
        return EnvDecision.of(d)  # a parameter failure (or a mix): stays BLOCKED
    env = [by[c] for c in unmet]
    relaxed = table.filter(lambda r: all(ok for c, ok in r.metrics.constraints.items() if c not in unmet))
    if not len(relaxed):
        return EnvDecision.of(d)  # the other constraints cannot be met either
    thr = materiality if isinstance(materiality, int | float) else float(materiality.materiality)
    lead = env[0]
    sign = 1.0 if lead.minimize else -1.0
    best = min(relaxed, key=lambda r: (sign * _val(r, lead.harm_metric), table.distance(r)))
    cur = table.current(params)
    assert cur is not None  # decide_with_materiality raised otherwise
    hm = lead.harm_metric

    def gain(a: ResultRow, b: ResultRow) -> float:
        return relative_improvement(_val(a, hm), _val(b, hm), minimize=lead.minimize)

    near = [r for r in relaxed if gain(best, r) >= -thr]
    cur_ok = any(r is cur for r in relaxed)
    if cur_ok and any(r is cur for r in near):
        pick, verdict = cur, "KEEP"
        why = (
            f"least harm on {lead.harm_metric}: current {_val(cur, lead.harm_metric):.4g} is within "
            f"materiality {thr:.0%} of the best {_val(best, lead.harm_metric):.4g}"
        )
    else:
        pick = min(near or [best], key=lambda r: (table.distance(r), sign * _val(r, lead.harm_metric)))
        verdict = "CHANGE" if pick is not cur else "KEEP"
        why = (
            f"least harm on {lead.harm_metric}: {_val(pick, lead.harm_metric):.4g} vs current "
            f"{_val(cur, lead.harm_metric):.4g}"
        )
        if not cur_ok:
            why += f" (current also violates {', '.join(c for c in cur.metrics.violated if c not in unmet)})"
    imp = gain(cur, pick)
    exposure = []
    for lim in env:
        e = lim.exposure(pick) if callable(lim.exposure) else lim.exposure
        if e:
            exposure.append(str(e))
    info = {
        "constraints": list(unmet),
        "note": lead.note,
        "notes": [lim.note for lim in env],
        "why": "; ".join(lim.why for lim in env),
        "exposure": "; ".join(exposure),
        "harm_metric": lead.harm_metric,
        "harm_minimize": lead.minimize,
        "harm_at_choice": _val(pick, lead.harm_metric),
        "harm_at_current": _val(cur, lead.harm_metric),
        "harm_best": _val(best, lead.harm_metric),
        "titles": [lim.title or f"Policy {lim.constraint} unmeetable in this environment" for lim in env],
        "fixes": [lim.fix for lim in env],
        "decision": f"environment-limited ({', '.join(unmet)}): {why}",
    }
    reason = f"no value can meet {', '.join(unmet)} in this environment ({info['why']}); {why}"
    return EnvDecision(pick, verdict, imp, reason, info)  # type: ignore[arg-type]


def attach_environment(rec: Recommendation, dec: EnvDecision | Mapping[str, Any] | None) -> Recommendation:
    """Record an environment limit on ``rec``: ``metrics["environment_blocked"]``, a design note per
    limit (de-duplicated by id across parameters) and a note. No-op when ``dec`` carries none."""
    info = dec.environment if isinstance(dec, EnvDecision) else dec
    if not info:
        return rec
    m = dict(rec.metrics) if isinstance(rec.metrics, Mapping) else {}
    m["environment_blocked"] = dict(info)
    dn = m.get("design_notes")
    dn = [dn] if isinstance(dn, str | Mapping) else list(dn or [])
    cons = list(info.get("constraints") or ["?"])
    titles, fixes = list(info.get("titles") or []), list(info.get("fixes") or [])
    harm = (
        f"{info.get('harm_at_choice', float('nan')):.4g} / {info.get('harm_at_current', float('nan')):.4g}"
        f" / {info.get('harm_best', float('nan')):.4g}"
    )
    for i, nid in enumerate(info.get("notes") or [info.get("note")]):
        dn.append(
            {
                "id": nid,
                "title": titles[i] if i < len(titles) else "",
                "finding": f"No value of the searched parameters can meet {cons[min(i, len(cons) - 1)]} in "
                f"the real environment: {info.get('why', '')}.",
                "evidence": {
                    "exposure at the chosen value": info.get("exposure") or "—",
                    f"{info.get('harm_metric')} chosen / current / best": harm,
                },
                "consequence": "The least-harm value is recommended; the residual exposure above is "
                "accepted, not removed.",
                "fix": fixes[i] if i < len(fixes) else "",
                "params": [rec.param],
            }
        )
    m["design_notes"] = dn
    rec.metrics = m
    rec.notes.insert(
        0,
        f"Environment limit ({', '.join(info['constraints'])}): {info.get('why', '')}. Exposure: "
        f"{info.get('exposure') or 'not quantified'}. Design note {info.get('note')}.",
    )
    return rec


def env_info(rec: Recommendation) -> Mapping[str, Any] | None:
    """``metrics["environment_blocked"]`` of a Recommendation, if any."""
    m = rec.metrics if isinstance(rec.metrics, Mapping) else {}
    v = m.get("environment_blocked")
    return v if isinstance(v, Mapping) else None

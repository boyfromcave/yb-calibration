"""Per-parameter explanation generator (PLAN §7 item 3), templated from the decision rules.

Owner: WP-8.

Everything here turns a :class:`~ybcal.studies.base.Recommendation` (plus the registry row and the
joint sensitivity) into plain text the templates print: what the parameter controls, the decision
rule, the binding constraint, metrics at the current and recommended values, a sensitivity sentence,
why not the neighbouring values, and the locked / patch-release note. Nothing is invented: a field
the study did not fill is reported as missing.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ybcal.params.registry import REGISTRY, ParamSpec
from ybcal.studies.base import Recommendation
from ybcal.units import BLOCKS_PER_DAY, BLOCKS_PER_HOUR, COIN

OWNER_WP = "WP-8"

#: Verdict meanings (also printed in the report and docs/report-guide.md).
VERDICTS: dict[str, str] = {
    "KEEP": "the current value stays; no candidate improved the primary metric by more than the policy's "
    "materiality without violating a constraint",
    "CHANGE": "move to the recommended value; it satisfies every policy constraint and improves the "
    "primary metric materially (or the current value violates the policy)",
    "PROVISIONAL": "the study's answer rests on synthetic data only; treat it as a direction, not a value "
    "to lock",
    "BLOCKED": "no evaluated value satisfies the policy; the owner must relax the policy or the rule",
    "NOT RUN": "the group's study is not available in this build or raised an error; no recommendation",
}

_KEYS_SKIP = {"design_notes", "owner_pin", "environment_blocked"}


def _thousands(v: int) -> str:
    return f"{v:,}"


def fmt_value(param: str, v: Any) -> str:
    """Human rendering of a parameter value with its unit (``34,560 blocks (30 d)``, ``1,500 bps
    (15 %)``, ``0.5 YEC``, ``$100.00``)."""
    if v is None:
        return "—"
    spec = REGISTRY.get(param)
    if isinstance(v, bool) or spec is None or not isinstance(v, int):
        return str(v).lower() if isinstance(v, bool) else str(v)
    u = spec.unit
    if u in ("blocks",):
        if abs(v) >= BLOCKS_PER_DAY:
            d = v / BLOCKS_PER_DAY
            ds = f"{d:.0f}" if abs(d - round(d)) < 0.005 else f"{d:.2f}"
            return f"{_thousands(v)} blocks ({ds} d)"
        if abs(v) >= BLOCKS_PER_HOUR:
            h = v / BLOCKS_PER_HOUR
            hs = f"{h:.0f}" if abs(h - round(h)) < 0.005 else f"{h:.1f}"
            return f"{_thousands(v)} blocks ({hs} h)"
        return f"{_thousands(v)} blocks"
    if u == "height":
        return _thousands(v)
    if u == "bps":
        return f"{_thousands(v)} bps ({v / 100:g} %)"
    if u == "zat":
        return f"{_thousands(v)} zat ({v / COIN:g} YEC)"
    if u == "cents":
        return f"${v / 100:,.2f}"
    if u == "micro-usd":
        return f"${v / 1e6:g}"
    return _thousands(v)


def fmt_number(x: Any) -> str:
    """Compact rendering of a metric value."""
    if x is None:
        return "—"
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, int):
        return _thousands(x)
    if isinstance(x, float):
        if math.isnan(x):
            return "n/a"
        if math.isinf(x):
            return "∞" if x > 0 else "−∞"
        a = abs(x)
        if a == 0:
            return "0"
        if a >= 1e5:
            return f"{x:,.0f}"
        if a >= 100:
            return f"{x:,.1f}"
        if a >= 1:
            return f"{x:.3g}"
        if a >= 1e-3:
            return f"{x:.4f}".rstrip("0").rstrip(".")
        return f"{x:.2e}"
    if isinstance(x, (list, tuple)):
        return ", ".join(fmt_number(v) for v in x[:8]) + (" …" if len(x) > 8 else "")
    if isinstance(x, Mapping):
        return ", ".join(f"{k}={fmt_number(v)}" for k, v in list(x.items())[:6])
    return str(x)


def controls(spec: ParamSpec) -> str:
    """What the parameter controls and which rules read it."""
    rules = ", ".join(spec.rules) if spec.rules else "no consensus rule"
    note = f" {spec.note}" if spec.note else ""
    return f"{spec.doc.rstrip('.')}. Read by: {rules}.{note}"


@dataclass
class MetricRow:
    """One line of the current-vs-recommended metrics table."""

    name: str
    current: str
    recommended: str
    changed: bool = False


def _metric_rows(metrics: Mapping[str, Any]) -> tuple[list[MetricRow], list[tuple[str, str]]]:
    cur = metrics.get("current")
    rec = metrics.get("recommended")
    rows: list[MetricRow] = []
    extra: list[tuple[str, str]] = []
    if isinstance(cur, Mapping) and isinstance(rec, Mapping):
        keys = list(dict.fromkeys([*cur.keys(), *rec.keys()]))
        for k in keys:
            a, b = cur.get(k), rec.get(k)
            if isinstance(a, Mapping) or isinstance(b, Mapping):
                continue
            rows.append(MetricRow(str(k), fmt_number(a), fmt_number(b), a != b))
    elif cur is not None or rec is not None:
        rows.append(MetricRow("primary", fmt_number(cur), fmt_number(rec), cur != rec))
    for k, v in metrics.items():
        if k in ("current", "recommended") or k in _KEYS_SKIP:
            continue
        extra.append((str(k), fmt_number(v)))
    return rows, extra


def metric_rows(rec: Recommendation) -> tuple[list[MetricRow], list[tuple[str, str]]]:
    """``(rows, extra)``: metrics at current/recommended, and other reported key/values."""
    m = rec.metrics if isinstance(rec.metrics, Mapping) else {}
    return _metric_rows(m)


def neighbours_text(rec: Recommendation) -> str:
    """ "Why not the neighbouring values", from the runner's ±1-step evaluation."""
    pts: Sequence[Mapping[str, Any]] = rec.sensitivity.get("neighbours") or []
    if not pts:
        spec = REGISTRY[rec.param]
        if not spec.tunable:
            return f"Not searched directly ({spec.klass}); it follows its parent."
        return (
            "No ±1-step neighbour was evaluated (the study fixes this value by a rule, or no admissible "
            "neighbour exists)."
        )
    m = rec.metrics if isinstance(rec.metrics, Mapping) else {}
    own_c = m.get("constraints_current")
    own = set(own_c) if isinstance(own_c, Mapping) else None
    parts = []
    for q in pts:
        v = fmt_value(rec.param, q.get("value"))
        step = int(q.get("steps", 0))
        tag = f"{step:+d} step ({v})"
        violated = list(q.get("violated") or [])
        # D-RD-AUD-10: name only the constraints of this parameter's own rule; a neighbour that fails
        # only other rules' constraints (e.g. another class's bad-debt bound) is not why it was not chosen
        if own is not None and violated and not any(c in own for c in violated):
            q = {**q, "feasible": True}
        elif own is not None:
            violated = [c for c in violated if c in own] or violated
        if q.get("rejected"):
            parts.append(f"{tag}: inadmissible ({', '.join(q['rejected'])})")
        elif q.get("primary") is None:
            parts.append(f"{tag}: not evaluated")
        elif not q.get("feasible", True):
            parts.append(f"{tag}: violates {', '.join(violated or ['a constraint'])}")
        else:
            imp = q.get("improvement")
            if imp is None or not math.isfinite(imp):
                parts.append(f"{tag}: primary {fmt_number(q.get('primary'))}")
            elif imp > 0:
                parts.append(
                    f"{tag}: {imp:.1%} better on the primary metric, below materiality or traded off "
                    "elsewhere"
                )
            elif imp < 0:
                parts.append(f"{tag}: {-imp:.1%} worse on the primary metric")
            else:
                parts.append(f"{tag}: equal on the primary metric (ties go to the current value)")
    return "; ".join(parts) + "."


def sensitivity_text(rec: Recommendation) -> str:
    """The study's sensitivity sentence plus the joint one (when present)."""
    s = rec.sensitivity or {}
    out = []
    sent = s.get("sentence")
    if sent:
        out.append(str(sent).rstrip(".") + ".")
    elif s.get("local_slope"):
        out.append(f"Locally {s['local_slope']} around the recommended value (±1 step, primary metric).")
    j = s.get("joint")
    if isinstance(j, Mapping) and j.get("sentence"):
        out.append(str(j["sentence"]))
    return " ".join(out) if out else "No sensitivity information was produced for this parameter."


@dataclass
class ParamSection:
    """Everything the templates print for one parameter."""

    param: str
    number: str
    anchor: str
    group: str
    status: str  #: ok / not-run / error
    verdict: str
    current: str
    recommended: str
    changed: bool
    change_path: str
    klass: str
    confidence: str
    provenance: str
    controls: str
    rule: str
    binding: str
    metrics: list[MetricRow] = field(default_factory=list)
    extra: list[tuple[str, str]] = field(default_factory=list)
    sensitivity: str = ""
    neighbours: str = ""
    locked_note: str = ""
    explanation: str = ""
    notes: list[str] = field(default_factory=list)
    figures: list[str] = field(default_factory=list)  #: evidence-relative PNG paths
    tables: list[str] = field(default_factory=list)  #: evidence-relative CSV paths
    insensitive: bool = False
    verdict_label: str = ""  #: the verdict as printed (owner pin / environment-limited suffix)
    pin: dict[str, Any] | None = None  #: ``metrics["owner_pin"]`` (D-RD-INF-2)
    env_blocked: dict[str, Any] | None = None  #: ``metrics["environment_blocked"]`` (D-RD-INF-3)

    def __post_init__(self) -> None:
        if not self.verdict_label:
            self.verdict_label = self.verdict


def section_for(
    param: str,
    number: str,
    rec: Recommendation | None,
    *,
    base_value: Any,
    final_value: Any,
    group_status: str,
    group_reason: str = "",
) -> ParamSection:
    """Build the :class:`ParamSection` of one parameter (``rec`` is ``None`` when its study did not run)."""
    spec = REGISTRY[param]
    anchor = "p-" + param.replace("[", "-").replace("]", "")
    if rec is None:
        why = group_reason or "study not available"
        return ParamSection(
            param,
            number,
            anchor,
            spec.group,
            group_status,
            "NOT RUN",
            fmt_value(param, base_value),
            fmt_value(param, final_value),
            False,
            spec.change_path,
            spec.klass,
            "—",
            "—",
            controls(spec),
            f"Study {spec.group} did not run ({why}).",
            "—",
            sensitivity="—",
            neighbours="—",
            locked_note=Recommendation.klass_note.fget(_Shim(param)),  # type: ignore[attr-defined]
            explanation=f"No recommendation: the {spec.group} study was not run ({why}). The current value "
            "stands until it is.",
        )
    rows, extra = metric_rows(rec)
    from ybcal.optimize.pins import pin_info, verdict_label

    pin = pin_info(rec)
    m = rec.metrics if isinstance(rec.metrics, Mapping) else {}
    envb = m.get("environment_blocked") if isinstance(m.get("environment_blocked"), Mapping) else None
    return ParamSection(
        param,
        number,
        anchor,
        rec.group or spec.group,
        group_status,
        rec.verdict,
        fmt_value(param, rec.current),
        fmt_value(param, rec.recommended),
        rec.recommended != rec.current,
        rec.change_path,
        spec.klass,
        rec.confidence,
        rec.provenance,
        controls(spec),
        rec.rule or "—",
        rec.binding or "—",
        rows,
        extra,
        sensitivity_text(rec),
        neighbours_text(rec),
        rec.klass_note,
        rec.explanation or "",
        list(rec.notes),
        insensitive=bool(rec.sensitivity.get("insensitive")),
        verdict_label=verdict_label(rec, rec.verdict),
        pin=dict(pin) if pin is not None else None,
        env_blocked=dict(envb) if envb is not None else None,
    )


class _Shim:
    """Lets :attr:`Recommendation.klass_note` run for a parameter without a Recommendation."""

    def __init__(self, param: str) -> None:
        self.param = param

    @property
    def change_path(self) -> str:
        return REGISTRY[self.param].change_path

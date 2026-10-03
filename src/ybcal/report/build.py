"""``ybcal recommend``: run every study, the joint pass and sensitivity, then write the report (PLAN §7).

Owner: WP-8.

Output directory (default ``reports/<date>-<shorthash>/``)::

    report.md            the report (figures linked from evidence/)
    report.html          the same, self-contained (figures inlined as base64, no external URL)
    recommended.json     the recommended set, same shape as `ybcal params extract`
    params.cpp.patch     locked + per-release changes to src/yellowback/params.cpp (SetCommon/MainParams)
    params-patch-release.patch   excluded (patch-release) field changes, if any
    manifest.json        RunManifest: everything needed to reproduce the run
    evidence/            per-group CSVs and PNGs, sensitivity tables and charts

:func:`run_recommend` is the library entry point; :func:`write_report` renders an assembled
:class:`ReportContext` (tests build one from toy studies).
"""

from __future__ import annotations

import base64
import contextlib
import csv
import hashlib
import json
import math
import os
import shutil
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ybcal import __version__
from ybcal.config import DEFAULT_POLICY_PATH, REPO_ROOT, Policy, RunManifest, sha256_file
from ybcal.optimize.evaluate import EvalCache
from ybcal.optimize.joint import (
    TOP_METRICS,
    JointResult,
    SensitivityResult,
    attach_sensitivity,
    joint_pass,
    joint_sensitivity,
)
from ybcal.params import emit
from ybcal.params.invariants import Context
from ybcal.params.paramset import ParamSet, mainnet
from ybcal.params.registry import PINNED_COMMIT, REGISTRY
from ybcal.report import explain as X
from ybcal.report import plots
from ybcal.studies.base import GROUP_ORDER, Budget, Env, Recommendation, Study

OWNER_WP = "WP-8"

TEMPLATES = Path(__file__).with_name("templates")
VERDICT_ORDER = ("KEEP", "CHANGE", "PROVISIONAL", "BLOCKED", "NOT RUN")
GROUP_TITLES: dict[str, str] = {
    "G1": "Price medians",
    "G2": "Volatility",
    "G3": "Collateral and classes",
    "G4": "Grace and abandonment",
    "G5": "Activation and enforcement",
    "G6": "Miner judgement and fees",
    "G7": "Supply cap and halts",
    "G8": "Price attestation",
    "G9": "Amounts and wallet policy",
    "R": "Release",
    "-": "Not studied",
}


# ===================================================================================================
# Data


@dataclass
class DataInfo:
    """Provenance of one input file (or of the synthetic defaults)."""

    path: str
    kind: str
    sha256: str = ""
    rows: int = 0
    span: str = ""
    gaps: str = ""
    key: str = ""

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready."""
        return dict(self.__dict__)


def expand_data_args(items: Sequence[str]) -> list[Path]:
    """``--data`` values: files, or directories (every regular file inside, sorted, except README)."""
    out: list[Path] = []
    for it in items:
        p = Path(it)
        if p.is_dir():
            out += sorted(
                f
                for f in p.iterdir()
                if f.is_file()
                and not f.name.startswith(".")
                and f.suffix.lower() in (".csv", ".npz", ".json")
                and not f.name.endswith(".meta.json")
                and not f.name.endswith(".provenance.json")
            )
        elif p.exists():
            out.append(p)
        else:
            raise FileNotFoundError(f"--data {it}: no such file or directory")
    return out


def _iso(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(int(ts), UTC).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OverflowError, OSError):
        return "?"


def load_data(files: Sequence[Path]) -> tuple[dict[str, Any], str, list[DataInfo]]:
    """Load each file by sniffing its format: spreads log → ``spreads``, pool-share CSV →
    ``pool_shares``, depth CSV → ``depth``, else a price series → ``price`` (an hourly real
    :class:`PricePath`). Returns ``(env.data, provenance, infos)``."""
    from ybcal.data import loaders as L
    from ybcal.data import pricepath as PP

    data: dict[str, Any] = {}
    infos: list[DataInfo] = []
    for f in files:
        sha = sha256_file(f)
        obj, kind = None, ""
        for k, fn, key in (
            ("spreads", L.load_spreads_csv, "spreads"),
            ("hashrate", L.load_pool_shares_csv, "pool_shares"),
            ("depth", L.load_depth_csv, "depth"),
        ):
            if f.suffix.lower() != ".csv":
                break
            try:
                obj, kind = fn(f), k
                data[key] = obj
                g = obj.gaps()
                ts = getattr(obj, "ts", None)
                hs = getattr(obj, "heights", None)
                span = (
                    f"{_iso(ts[0])} → {_iso(ts[-1])}"
                    if ts is not None and len(ts)
                    else f"heights {int(hs[0])}–{int(hs[-1])}"
                    if hs is not None and len(hs)
                    else ""
                )
                infos.append(DataInfo(str(f), kind, sha, len(obj), span, g.summary().splitlines()[0], key))
                break
            except (L.DataFormatError, ValueError, KeyError, IndexError):
                obj = None
        if obj is not None:
            continue
        if f.suffix.lower() == ".npz":
            pp = PP.load(f)
            data["price"] = pp
            infos.append(DataInfo(str(f), "price", sha, int(pp.prices.shape[-1]), "", "", "price"))
            continue
        try:
            ser = L.load_price_csv(f)
        except (L.DataFormatError, ValueError, KeyError, IndexError) as e:
            raise ValueError(f"{f}: not a recognised price / spreads / pool-share / depth file ({e})") from e
        rs = L.resample_to_grid(ser, "hour")
        data["price"] = rs.path
        infos.append(
            DataInfo(
                str(f),
                "price",
                sha,
                len(ser),
                f"{_iso(ser.ts[0])} → {_iso(ser.ts[-1])}",
                ser.gaps().summary().splitlines()[0] + f"; hourly grid forward-filled "
                f"{rs.filled_fraction:.1%}",
                "price",
            )
        )
    prov = "real-data" if any(i.kind for i in infos) else "synthetic"
    return data, prov, infos


def scenario_library_info() -> list[DataInfo]:
    """The shipped scenario files (synthetic programs) with their hashes."""
    d = REPO_ROOT / "scenarios"
    if not d.exists():
        return []
    return [
        DataInfo(str(f.relative_to(REPO_ROOT)), "scenario", sha256_file(f)) for f in sorted(d.glob("*.toml"))
    ]


# ===================================================================================================
# Devnet, readiness, risks


def devnet_status(recommended: ParamSet | None = None) -> dict[str, Any]:
    """Status of the PLAN §6.4 differential validation for this run (never runs nodes here)."""
    try:
        from ybcal.devnet import build as B
        from ybcal.devnet.diff import resolve_simulator
        from ybcal.devnet.status import Skipped
    except Exception as e:  # pragma: no cover - devnet layer missing
        return {"status": "skipped", "reason": f"devnet layer unavailable: {e}"}
    sim = resolve_simulator()
    b = B.resolve_binary(None)
    if isinstance(b, Skipped):
        return {"status": "skipped", "reason": b.reason, "simulator": sim is not None}
    return {
        "status": "not validated",
        "simulator": sim is not None,
        "binary": str(b.ycashd),
        "reason": "a ycashd binary is available, but `recommend` does not run nodes; run "
        "`ybcal devnet validate --overlay <report>/recommended.json`",
    }


@dataclass
class CheckItem:
    """One line of the lock-readiness checklist."""

    name: str
    ok: bool | None  #: None = advisory / not applicable
    detail: str
    required: bool = True


def lock_readiness(
    joint: JointResult, policy: Policy, *, patch_check: emit.PatchCheck | None, devnet: Mapping[str, Any]
) -> list[CheckItem]:
    """PLAN §7 item 7, plus advisory lines (joint convergence, devnet, patch)."""
    recs = joint.recommendations
    locked = [k for k, s in REGISTRY.items() if s.tunable and s.change_path == "locked"]
    missing = [k for k in locked if k not in recs]
    prov = [
        k
        for k in locked
        if k in recs and (recs[k].verdict == "PROVISIONAL" or recs[k].provenance != "real-data")
    ]
    blocked = [k for k, r in recs.items() if r.verdict == "BLOCKED"]
    viol = joint.recommended.check(Context.from_policy(policy))
    r_out = joint.outcomes.get("R")
    r_ok = (
        r_out is not None
        and r_out.status == "ok"
        and not any(r.verdict == "BLOCKED" for r in r_out.recommendations)
    )

    def lst(xs: Sequence[str], n: int = 8) -> str:
        return ", ".join(xs[:n]) + (f" … (+{len(xs) - n})" if len(xs) > n else "")

    items = [
        CheckItem(
            "Every locked parameter has a recommendation",
            not missing,
            "all studied" if not missing else f"{len(missing)} without a study: {lst(missing)}",
        ),
        CheckItem(
            "Every locked recommendation is non-provisional and backed by real data",
            not prov,
            "all real-data" if not prov else f"{len(prov)} rest on synthetic/judgement evidence: {lst(prov)}",
            required=bool(policy.require_real_data_for_lock),
        ),
        CheckItem(
            "No parameter is BLOCKED by the policy", not blocked, "none" if not blocked else lst(blocked)
        ),
        CheckItem(
            "The recommended set passes every invariant (PLAN §1.4)",
            not viol,
            "all pass" if not viol else "; ".join(str(v) for v in viol[:4]),
        ),
        CheckItem(
            "The release study passes",
            r_ok,
            "release study ran, nothing BLOCKED"
            if r_ok
            else ("release study not run: " + (r_out.reason if r_out else "not requested")),
        ),
        CheckItem(
            "The joint pass converged", joint.converged, f"{len(joint.rounds)} round(s)", required=False
        ),
        CheckItem(
            "params.cpp patch applies at the pin",
            None if patch_check is None or patch_check.status in ("skipped",) else patch_check.ok,
            patch_check.detail if patch_check else "not checked",
            required=False,
        ),
        CheckItem(
            "Devnet differential validation (M6)",
            True if devnet.get("status") == "passed" else None,
            f"{devnet.get('status')}: {devnet.get('reason', '')}",
            required=False,
        ),
    ]
    return items


def top_risks(
    joint: JointResult,
    sens: SensitivityResult | None,
    policy: Policy,
    devnet: Mapping[str, Any],
    provenance: str,
) -> list[str]:
    """The three most important remaining risks, most severe first."""
    risks: list[tuple[int, str]] = []
    recs = joint.recommendations
    blocked = [k for k, r in recs.items() if r.verdict == "BLOCKED"]
    if blocked:
        risks.append(
            (
                0,
                f"{len(blocked)} parameter(s) BLOCKED — no evaluated value meets the policy: "
                f"{', '.join(blocked[:6])}{' …' if len(blocked) > 6 else ''}.",
            )
        )
    if sens is not None:
        over = []
        for c in "ABC":
            p = sens.base_metrics.get(f"bad_debt_prob_{c}")
            if p is not None and math.isfinite(p) and p > policy.max_bad_debt(c):
                over.append(f"{c} {p:.2%} vs {policy.max_bad_debt(c):.1%}")
        if over:
            risks.append(
                (
                    1,
                    "At the recommended set the fast solvency model puts P(bad debt at claim opening) "
                    f"above the policy for class {', '.join(over)} — no liquidation before claimHeight "
                    "(fact 1.5-1).",
                )
            )
    bad = [g for g, o in joint.outcomes.items() if o.status != "ok"]
    if bad:
        n = sum(1 for k, s in REGISTRY.items() if s.tunable and s.group in bad)
        risks.append(
            (
                2,
                f"Studies not run: {', '.join(bad)} — {n} tunable parameter(s) keep their current "
                "value without evidence.",
            )
        )
    if provenance != "real-data":
        risks.append(
            (
                3,
                "Every recommendation rests on synthetic data (PROVISIONAL); lock-readiness needs a "
                "year of real hourly YEC/USD, a spreads.py log and ideally pool-share data.",
            )
        )
    if joint.design_notes:
        risks.append(
            (
                4,
                f"{len(joint.design_notes)} design note(s) describe problems parameter tuning cannot "
                "fix (see §5).",
            )
        )
    if devnet.get("status") != "passed":
        risks.append((5, f"Simulator not validated against real nodes (devnet {devnet.get('status')})."))
    if not joint.converged:
        risks.append((6, f"The joint pass did not converge in {len(joint.rounds)} rounds."))
    risks.sort(key=lambda t: t[0])
    return [t for _, t in risks[:3]]


def blocked_rows(joint: JointResult, sections: Mapping[str, X.ParamSection]) -> list[dict[str, Any]]:
    """BLOCKED parameters for the executive summary: current, least-violating value, reason."""
    out = []
    for p, r in joint.recommendations.items():
        if r.verdict != "BLOCKED" or p not in sections:
            continue
        m = r.metrics if isinstance(r.metrics, Mapping) else {}
        lv = m.get("least_violating")
        lv_txt = ""
        if isinstance(lv, Mapping):
            vals = lv.get("values") or {}
            lv_txt = (
                ", ".join(f"{k} {X.fmt_number(v)}" for k, v in list(vals.items())[:6])
                if isinstance(vals, Mapping)
                else str(vals)
            )
        reason = "; ".join(x for x in (r.binding, str(m.get("decision") or "")) if x and x != "—")
        out.append(
            {
                "param": p,
                "anchor": sections[p].anchor,
                "number": sections[p].number,
                "current": X.fmt_value(p, r.current),
                "least": X.fmt_value(p, r.recommended),
                "applied": r.recommended != r.current,
                "reason": reason or "no candidate meets the policy",
                "least_metrics": lv_txt,
            }
        )
    return out


# ===================================================================================================
# Context


@dataclass
class ReportContext:
    """Everything :func:`write_report` needs."""

    base: ParamSet
    joint: JointResult
    sensitivity: SensitivityResult | None
    policy: Policy
    policy_text: str
    policy_path: str
    budget: Budget
    seed: int
    provenance: str
    data_info: list[DataInfo]
    devnet: dict[str, Any]
    manifest: RunManifest
    timings: dict[str, float] = field(default_factory=dict)
    ycash6: str | None = None
    title: str = "Ycash Yellowback (YED) parameter recommendation"
    sensitivity_error: str = ""
    only_groups: list[str] | None = None  #: restrict §1/§3 to these groups (``ybcal study``)

    def in_scope(self) -> list[str]:
        """The tunable parameters the report covers."""
        return [
            p for p in tunable_params() if self.only_groups is None or REGISTRY[p].group in self.only_groups
        ]


def tunable_params() -> list[str]:
    """Every tunable registry parameter, in group order then registry order (the §3 order)."""
    order = {g: i for i, g in enumerate(GROUP_ORDER)}
    names = [k for k, s in REGISTRY.items() if s.tunable]
    return sorted(names, key=lambda k: (order.get(REGISTRY[k].group, 99), list(REGISTRY).index(k)))


def section_numbers() -> dict[str, str]:
    """``param → "§3.n"`` for tunable params; derived params map to their first parent's section."""
    nums = {p: f"§3.{i + 1}" for i, p in enumerate(tunable_params())}
    for k, s in REGISTRY.items():
        if k not in nums and s.parents:
            for par in s.parents:
                if par in nums:
                    nums[k] = nums[par]
                    break
    return nums


def _rel(p: Path, root: Path) -> str:
    try:
        return p.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def _collect_evidence(rec: Recommendation, out: Path, evidence: Path) -> tuple[list[str], list[str]]:
    """PNG and CSV evidence of ``rec`` as paths relative to ``out`` (copied into evidence/ if outside)."""
    figs, tabs = [], []
    for p in rec.evidence:
        p = Path(p)
        if not p.exists():
            continue
        try:
            p.resolve().relative_to(out.resolve())
            q = p
        except ValueError:
            q = evidence / (rec.group or "misc").lower() / p.name
            q.parent.mkdir(parents=True, exist_ok=True)
            if not q.exists():
                shutil.copy2(p, q)
        rel = _rel(q, out)
        if q.suffix.lower() == ".png" and rel not in figs:
            figs.append(rel)
        elif q.suffix.lower() == ".csv" and rel not in tabs:
            tabs.append(rel)
    return figs, tabs


def _summary_rows(ctx: ReportContext, sections: Mapping[str, X.ParamSection]) -> list[dict[str, Any]]:
    rows = []
    for p in ctx.in_scope():
        s = sections[p]
        rows.append(
            {
                "param": p,
                "anchor": s.anchor,
                "number": s.number,
                "group": REGISTRY[p].group,
                "current": s.current,
                "recommended": s.recommended,
                "changed": s.changed,
                "verdict": s.verdict,
                "change_path": s.change_path,
                "klass": s.klass,
                "confidence": s.confidence,
                "provenance": s.provenance,
                "insensitive": s.insensitive,
            }
        )
    return rows


def _appendix_rows(ctx: ReportContext) -> list[dict[str, Any]]:
    tun = set(tunable_params())
    groups = ctx.only_groups
    nums = section_numbers()
    rows = []
    for k, s in REGISTRY.items():
        if k in tun or (groups is not None and s.group not in groups):
            continue
        if s.klass == "derived":
            how = f"derived from {', '.join(s.parents) or 'its parent'} ({nums.get(k, '—')})"
        elif s.klass == "per-release":
            how = "per-release: set from the release tip and date (release study)"
        elif s.klass == "constant":
            how = "protocol constant: verified against source, never tuned"
        elif s.klass == "meta":
            how = "identity field: not calibrated"
        else:
            how = "fixed by design (verified, not searched)"
        rec = ctx.joint.recommendations.get(k)
        rows.append(
            {
                "param": k,
                "klass": s.klass,
                "group": s.group,
                "current": X.fmt_value(k, ctx.base[k]),
                "recommended": X.fmt_value(k, ctx.joint.recommended[k]),
                "changed": ctx.base[k] != ctx.joint.recommended[k],
                "how": how,
                "verdict": rec.verdict if rec is not None else "—",
            }
        )
    return rows


def _data_uri(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _sens_tables(sens: SensitivityResult) -> dict[str, Any]:
    key = "ST" if sens.method == "sobol" else "share"
    factors = list(sens.factors)
    rows = []
    for f in factors:
        rows.append(
            {
                "factor": f,
                "params": len(sens.factors[f]),
                "vals": [X.fmt_number(float(sens.indices[m][f].get(key, 0.0) or 0.0)) for m in sens.metrics],
                "insensitive": all(sens.per_param[p]["insensitive"] for p in sens.factors[f]),
            }
        )
    rows.sort(key=lambda r: r["insensitive"])
    return {
        "key": key,
        "metric_labels": [TOP_METRICS[m][0] for m in sens.metrics],
        "rows": rows,
        "base": [X.fmt_number(sens.base_values[m]) for m in sens.metrics],
    }


def write_csv(path: Path, header: Sequence[str], rows: Sequence[Sequence[Any]]) -> Path:
    """Small CSV helper."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    return path


def write_report(ctx: ReportContext, out: Path) -> dict[str, Path]:
    """Render ``ctx`` into ``out`` (see the module docstring); returns the written paths."""
    import jinja2

    t0 = time.perf_counter()
    out.mkdir(parents=True, exist_ok=True)
    evidence = out / "evidence"
    evidence.mkdir(exist_ok=True)
    joint = ctx.joint
    nums = section_numbers()
    paths: dict[str, Path] = {}

    # -- per-parameter sections ------------------------------------------------------------------
    sections: dict[str, X.ParamSection] = {}
    figure_no: dict[str, int] = {}
    figure_owner: dict[str, str] = {}
    for p in ctx.in_scope():
        g = REGISTRY[p].group
        o = joint.outcomes.get(g)
        rec = joint.recommendations.get(p)
        status = o.status if o is not None else "not-run"
        reason = o.reason if o is not None else "group not requested"
        sec = X.section_for(
            p,
            nums[p],
            rec,
            base_value=ctx.base[p],
            final_value=joint.recommended[p],
            group_status=status,
            group_reason=reason,
        )
        if rec is not None:
            figs, tabs = _collect_evidence(rec, out, evidence)
            if not figs and rec.sensitivity.get("neighbours") and o is not None and o.rec_metrics is not None:
                f = plots.neighbour_plot(
                    p,
                    float(ctx.base.as_int(p)) if isinstance(ctx.base[p], int) else 0.0,
                    float(joint.recommended.as_int(p)),
                    rec.sensitivity["neighbours"],
                    o.rec_metrics.primary_value,
                    o.rec_metrics.primary,
                    evidence / g.lower() / f"neighbours_{sec.anchor}.png",
                )
                if f is not None:
                    figs = [_rel(f, out)]
            sec.figures, sec.tables = figs, tabs
            for fg in figs:
                if fg not in figure_no:
                    figure_no[fg] = len(figure_no) + 1
                    figure_owner[fg] = p
        sections[p] = sec

    # -- per-group tables ------------------------------------------------------------------------
    for g, o in joint.outcomes.items():
        if o.run is not None:
            with contextlib.suppress(Exception):
                o.run.table.to_csv(evidence / g.lower() / "results.csv")

    # -- sensitivity -----------------------------------------------------------------------------
    sens_ctx: dict[str, Any] | None = None
    if ctx.sensitivity is not None:
        sens = ctx.sensitivity
        sdir = evidence / "sensitivity"
        key = "ST" if sens.method == "sobol" else "share"
        write_csv(
            sdir / "indices.csv",
            ["metric", "factor", "params", "S1", "ST", "mu_star", "share"],
            [
                [
                    m,
                    f,
                    ";".join(sens.factors[f]),
                    d.get("S1", ""),
                    d.get("ST", ""),
                    d.get("mu_star", ""),
                    d.get("share", ""),
                ]
                for m in sens.metrics
                for f, d in sens.indices[m].items()
            ],
        )
        write_csv(
            sdir / "tornado.csv",
            ["metric", "param", "lo", "y_lo", "hi", "y_hi", "base"],
            [
                [m, p, d.get("lo"), d.get("y_lo"), d.get("hi"), d.get("y_hi"), sens.base_values[m]]
                for m in sens.metrics
                for p, d in sens.tornado[m].items()
            ],
        )
        write_csv(
            sdir / "per_param.csv",
            ["param", "factor", "insensitive", "dominant", *[f"index_{m}" for m in sens.metrics]],
            [
                [
                    p,
                    d["factor"],
                    int(d["insensitive"]),
                    d.get("dominant") or "",
                    *[d["index"][m] for m in sens.metrics],
                ]
                for p, d in sens.per_param.items()
            ],
        )
        figs = []
        f = plots.sobol_bars(
            sens.indices,
            {m: TOP_METRICS[m][0] for m in sens.metrics},
            sdir / "indices.png",
            key=key,
            threshold=sens.threshold,
        )
        if f:
            figs.append(_rel(f, out))
        for m in sens.metrics:
            f = plots.tornado(
                sens.tornado[m], sens.base_values[m], TOP_METRICS[m][0], sdir / f"tornado_{m}.png"
            )
            if f:
                figs.append(_rel(f, out))
        sens_ctx = {
            "result": sens,
            **_sens_tables(sens),
            "figures": figs,
            "insensitive": sens.insensitive(),
            "metrics": sens.metrics,
        }
        for fg in figs:
            figure_no.setdefault(fg, len(figure_no) + 1)

    # -- summary ---------------------------------------------------------------------------------
    rows = _summary_rows(ctx, sections)
    counts = {v: sum(1 for r in rows if r["verdict"] == v) for v in VERDICT_ORDER}
    moved = sum(1 for r in rows if r["changed"])
    write_csv(
        evidence / "summary.csv",
        ["param", "group", "current", "recommended", "verdict", "change_path", "confidence", "provenance"],
        [
            [
                r["param"],
                r["group"],
                r["current"],
                r["recommended"],
                r["verdict"],
                r["change_path"],
                r["confidence"],
                r["provenance"],
            ]
            for r in rows
        ],
    )

    # -- patch and recommended.json --------------------------------------------------------------
    src = emit.params_cpp_source(ctx.ycash6)
    psec = dict(nums)
    for k, r in joint.recommendations.items():
        if r.verdict == "BLOCKED" and r.recommended != r.current and k in psec:
            psec[k] = f"{psec[k]}, BLOCKED: least-violating value"
    patch = emit.make_patch(joint.recommended, ctx.base, sections=psec, source=src)
    pfile = out / "params.cpp.patch"
    pfile.write_text(
        patch.locked
        or "# no locked or per-release changes: the recommended set equals the "
        "shipped SetCommon()/MainParams() values\n"
    )
    paths["patch"] = pfile
    check = (
        emit.check_patch(patch.locked, ctx.ycash6) if patch.locked else emit.PatchCheck("empty", "no changes")
    )
    if patch.patch_release:
        (out / "params-patch-release.patch").write_text(patch.patch_release)
        paths["patch_release"] = out / "params-patch-release.patch"
        check_rel = emit.check_patch(patch.patch_release, ctx.ycash6)
    else:
        check_rel = emit.PatchCheck("empty", "no changes")
    paths["recommended"] = emit.write_recommended(
        out / "recommended.json",
        joint.recommended.replace({"network": "main"}),
        base=ctx.base,
        extra={
            "verdicts": {k: r.verdict for k, r in joint.recommendations.items()},
            "policy_hash": ctx.policy.digest(),
            "seed": ctx.seed,
            "budget": ctx.budget.name,
            "provenance": ctx.provenance,
        },
    )

    checklist = lock_readiness(joint, ctx.policy, patch_check=check, devnet=ctx.devnet)
    ready = all(c.ok for c in checklist if c.required)
    risks = top_risks(joint, ctx.sensitivity, ctx.policy, ctx.devnet, ctx.provenance)
    blocked = blocked_rows(joint, sections)

    groups = []
    for g in GROUP_ORDER:
        o = joint.outcomes.get(g)
        params = [p for p in ctx.in_scope() if REGISTRY[p].group == g]
        groups.append(
            {
                "group": g,
                "title": GROUP_TITLES.get(g, g),
                "status": o.status if o else "not requested",
                "reason": o.reason if o else "",
                "summary": o.run.summary() if o is not None and o.run is not None else "",
                "seconds": o.seconds if o else 0.0,
                "warnings": list(o.run.warnings) if o and o.run else [],
                "params": [sections[p] for p in params],
            }
        )

    # -- manifest --------------------------------------------------------------------------------
    m = ctx.manifest
    m.extra.update(
        {
            "run_id": out.name,
            "counts": counts,
            "moved": moved,
            "ready": ready,
            "joint": joint.to_dict(),
            "devnet": ctx.devnet,
            "timings": ctx.timings,
            "patch": {
                "summary": patch.summary(),
                "check": check.__dict__,
                "check_patch_release": check_rel.__dict__,
                "source": {"origin": src.origin, "commit": src.commit, "sha256": src.sha256},
                "changes": [c.to_dict() for c in patch.changes],
                "patch_release_changes": [c.to_dict() for c in patch.release_changes],
                "header_changes": patch.header_changes,
            },
            "sensitivity": None
            if ctx.sensitivity is None
            else {
                "method": ctx.sensitivity.method,
                "grouped": ctx.sensitivity.grouped,
                "insensitive": ctx.sensitivity.insensitive(),
                "n_evals": ctx.sensitivity.n_evals,
                "seconds": round(ctx.sensitivity.seconds, 2),
            },
            "data": [d.to_dict() for d in ctx.data_info],
            "recommended_digest": joint.recommended.digest(),
        }
    )
    if ctx.sensitivity is not None:
        (evidence / "sensitivity" / "sensitivity.json").write_text(
            json.dumps(ctx.sensitivity.to_dict(), indent=1, default=str) + "\n"
        )
    (evidence / "joint.json").write_text(json.dumps(joint.to_dict(), indent=1, default=str) + "\n")
    (evidence / "recommendations.json").write_text(
        json.dumps({k: r.to_dict() for k, r in joint.recommendations.items()}, indent=1, default=str) + "\n"
    )

    # -- render ----------------------------------------------------------------------------------
    images: dict[str, str] = {}
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES)),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=jinja2.StrictUndefined,
    )
    env.filters["num"] = X.fmt_number
    env.filters["pct"] = lambda x: "—" if x is None else f"{x:.2%}"
    env.filters["mdcell"] = lambda s: str(s).replace("|", "\\|").replace("\n", " ")

    def esc(s: Any) -> str:
        from markupsafe import escape

        return str(escape(str(s)))

    env.filters["e"] = esc

    def img(rel: str) -> str:
        if rel not in images:
            images[rel] = _data_uri(out / rel)
        return images[rel]

    tv = {
        "ctx": ctx,
        "title": ctx.title,
        "now": m.created_utc,
        "run_id": out.name,
        "version": __version__,
        "pin": PINNED_COMMIT,
        "rows": rows,
        "counts": counts,
        "moved": moved,
        "verdicts": X.VERDICTS,
        "verdict_order": VERDICT_ORDER,
        "risks": risks,
        "blocked": blocked,
        "groups": groups,
        "sections": sections,
        "joint": joint,
        "sens": sens_ctx,
        "design_notes": joint.design_notes,
        "devnet": ctx.devnet,
        "checklist": checklist,
        "ready": ready,
        "appendix": _appendix_rows(ctx),
        "patch": patch,
        "patch_check": check,
        "patch_check_release": check_rel,
        "figure_no": figure_no,
        "figure_owner": figure_owner,
        "sections_by_param": sections,
        "data_info": ctx.data_info,
        "provenance": ctx.provenance,
        "timings": ctx.timings,
        "manifest": m,
        "budget": ctx.budget,
        "seed": ctx.seed,
        "policy": ctx.policy,
        "policy_text": ctx.policy_text,
        "policy_path": ctx.policy_path,
        "img": img,
        "top_metrics": TOP_METRICS,
        "group_titles": GROUP_TITLES,
        "patch_text": patch.locked,
        "sensitivity_error": ctx.sensitivity_error,
    }
    md = env.get_template("report.md.j2").render(**tv)
    (out / "report.md").write_text(md)
    html = env.get_template("report.html.j2").render(**tv)
    (out / "report.html").write_text(html)
    paths["md"], paths["html"] = out / "report.md", out / "report.html"
    ctx.timings["render"] = time.perf_counter() - t0
    m.extra["timings"] = ctx.timings
    paths["manifest"] = m.save(out / "manifest.json")
    paths["evidence"] = evidence
    return paths


# ===================================================================================================
# The run


@dataclass
class RecommendConfig:
    """Inputs of :func:`run_recommend` (the CLI maps its flags onto this)."""

    budget: Budget
    policy: Policy
    policy_path: str = ""
    seed: int | None = None
    data_files: list[Path] = field(default_factory=list)
    out: Path | None = None
    workers: int | None = None
    groups: list[str] | None = None
    sensitivity: bool = True
    sensitivity_method: str = "sobol"
    sensitivity_params: list[str] | None = None
    ycash6: str | None = None
    command: list[str] = field(default_factory=list)
    max_rounds: int | None = None
    cache_dir: str | None = None
    title: str = "Ycash Yellowback (YED) parameter recommendation"
    mini: bool = False  #: restrict the report to ``groups`` (``ybcal study``)


@dataclass
class RecommendResult:
    """What a run produced."""

    out: Path
    paths: dict[str, Path]
    counts: dict[str, int]
    joint: JointResult
    sensitivity: SensitivityResult | None
    seconds: float
    ready: bool


def default_out_dir(cfg: RecommendConfig, seed: int, data_hashes: Mapping[str, str]) -> Path:
    """``reports/<date>-<shorthash>/`` (hash over version, pin, budget, seed, policy and data)."""
    h = hashlib.sha256(
        json.dumps(
            {
                "v": __version__,
                "pin": PINNED_COMMIT,
                "b": cfg.budget.name,
                "s": seed,
                "p": cfg.policy.digest(),
                "d": dict(data_hashes),
                "g": cfg.groups,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()[:8]
    tag = f"study-{'-'.join(cfg.groups or [])}-" if cfg.mini else ""
    return Path("reports") / f"{datetime.now(UTC).strftime('%Y-%m-%d')}-{tag}{h}"


def policy_text(path: str) -> tuple[str, str]:
    """The policy file verbatim and its display path (built-in defaults when there is none)."""
    p = Path(path) if path else DEFAULT_POLICY_PATH
    if p.exists():
        return p.read_text(), str(p)
    return "# built-in defaults (no policy file found)\n" + json.dumps(
        Policy().to_dict(), indent=1
    ), "(built-in)"


def make_env(cfg: RecommendConfig, data: dict[str, Any], provenance: str, out: Path) -> Env:
    """The run Env: evidence goes to ``<out>/evidence/<group>/``."""
    seed = int(cfg.seed if cfg.seed is not None else cfg.policy.seed)
    return Env(
        cfg.policy,
        cfg.budget,
        seed,
        data=data,
        provenance=provenance,  # type: ignore[arg-type]
        out_dir=str(out / "evidence"),
    )


def run_recommend(
    cfg: RecommendConfig,
    *,
    loader: Callable[[str], Study] | None = None,
    on_event: Callable[[str], None] | None = None,
    sensitivity_fn: Any = None,
) -> RecommendResult:
    """Load data, run the joint pass and the sensitivity, write the report. Never raises because of
    a study: a failing study is reported as not run."""
    t0 = time.perf_counter()
    say = on_event or (lambda msg: print(msg, file=sys.stderr, flush=True))
    data, prov, infos = load_data(cfg.data_files)
    seed = int(cfg.seed if cfg.seed is not None else cfg.policy.seed)
    hashes = {i.path: i.sha256 for i in infos}
    out = Path(cfg.out) if cfg.out else default_out_dir(cfg, seed, hashes)
    out.mkdir(parents=True, exist_ok=True)
    env = make_env(cfg, data, prov, out)
    base = mainnet()
    timings: dict[str, float] = {"load": time.perf_counter() - t0}
    say(
        f"ybcal recommend: budget {cfg.budget.name}, seed {seed}, data {prov} "
        f"({len(infos)} file(s)), out {out}"
    )
    cache = EvalCache.on_disk(cfg.cache_dir) if cfg.cache_dir else EvalCache()
    t = time.perf_counter()
    joint = joint_pass(
        base,
        env,
        groups=cfg.groups,
        max_rounds=cfg.max_rounds,
        cache=cache,
        workers=cfg.workers,
        loader=loader,
        on_event=say,
    )
    timings["joint"] = time.perf_counter() - t
    say(
        f"joint pass: {len(joint.rounds)} round(s), converged={joint.converged}, "
        f"{len(base.diff(joint.recommended))} value(s) moved, {timings['joint']:.1f} s"
    )
    sens = None
    sens_err = ""
    if cfg.sensitivity:
        t = time.perf_counter()
        try:
            sens = joint_sensitivity(
                joint.recommended,
                env,
                method=cfg.sensitivity_method,  # type: ignore[arg-type]
                params=cfg.sensitivity_params,
                workers=cfg.workers,
                fn=sensitivity_fn,
            )
            attach_sensitivity(joint.recommendations, sens)
            say(
                f"sensitivity ({sens.method}{', grouped' if sens.grouped else ''}): "
                f"{len(sens.insensitive())} insensitive of {len(sens.params)}, "
                f"{time.perf_counter() - t:.1f} s"
            )
        except Exception as e:
            sens_err = f"{type(e).__name__}: {e}"
            say(f"sensitivity failed: {sens_err}")
        timings["sensitivity"] = time.perf_counter() - t
    dev = devnet_status(joint.recommended)
    ptext, ppath = policy_text(cfg.policy_path)
    man = RunManifest.create(
        budget=cfg.budget.name,
        seed=seed,
        policy=cfg.policy,
        data_files=[Path(i.path) for i in infos],
        policy_path=ppath,
        command=cfg.command or ["ybcal", "recommend"],
    )
    man.extra["workers"] = cfg.workers
    man.extra["groups"] = cfg.groups
    man.extra["python"] = sys.version.split()[0]
    man.extra["pid_cpu_count"] = os.cpu_count()
    ctx = ReportContext(
        base,
        joint,
        sens,
        cfg.policy,
        ptext,
        ppath,
        cfg.budget,
        seed,
        prov,
        infos + scenario_library_info(),
        dev,
        man,
        timings,
        cfg.ycash6,
        title=cfg.title,
        sensitivity_error=sens_err,
        only_groups=list(cfg.groups) if cfg.mini and cfg.groups else None,
    )
    paths = write_report(ctx, out)
    total = time.perf_counter() - t0
    timings["total"] = total
    man.extra["timings"] = timings
    man.save(out / "manifest.json")
    counts = dict(man.extra.get("counts", {}))
    return RecommendResult(out, paths, counts, joint, sens, total, bool(man.extra.get("ready")))


def reproduce_config(manifest_path: str | Path) -> dict[str, Any]:
    """Settings recorded in a manifest (budget, seed, policy path, data files) with hash checks."""
    m = RunManifest.load(manifest_path)
    probs = [p for p, ok in m.verify_data().items() if not ok]
    pol = Policy.load(m.policy_path) if m.policy_path and Path(m.policy_path).exists() else Policy()
    if pol.digest() != m.policy_hash:
        probs.append(f"policy {m.policy_path or '(built-in)'} changed since the run")
    return {
        "budget": m.budget,
        "seed": m.seed,
        "policy_path": m.policy_path,
        "policy": pol,
        "data_files": list(m.data_hashes),
        "groups": m.extra.get("groups"),
        "workers": m.extra.get("workers"),
        "problems": probs,
    }

"""``ybcal robust``: run ``recommend`` across seeds × data windows × price models and tabulate how
stable each parameter's recommendation is (D-RD-INF-5).

Every combination is one ordinary ``ybcal recommend`` run in its own directory
``<out>/runs/<window>__<model>__s<seed>/`` (a subprocess, so runs are isolated and can be killed),
with ``--window``, ``--seed`` and the model's ``--policy-set`` overrides:

=============  ==========================================================================
model          policy overrides
=============  ==========================================================================
bootstrap      none (demeaned stationary block bootstrap of the hourly series, centred)
regime         ``real_price_model = "regime"`` (regime switch fitted on the daily series)
garch          ``real_price_model = "garch"``
martingale     ``price_drift = "martingale"`` (the −σ²/2 bleed stress, D-RD-AUD-1)
a+b            both (e.g. ``regime+martingale``)
=============  ==========================================================================

CPU is bounded by ``--jobs`` concurrent runs × ``--workers`` processes each (at ``nice`` 10 by
default). A run that finished (``robust-run.json`` with exit 0 and the same command) is skipped, so
the harness is **resumable**: re-run the same command after an interruption. ``--cache DIR`` shares
an on-disk evaluation cache between runs (keys include seed, policy and data, so it is safe).
``--table-only`` re-tabulates what exists.

Outputs in ``<out>/``: ``robust.md`` (the table, unstable parameters first), ``robust-summary.csv``
(one row per parameter), ``robust.csv`` (one row per run × parameter) and ``robust.json``.
Per parameter: the value and verdict of every run, the modal value and its agreement rate, the
verdict agreement, the modal value per window / model / seed, and flags: ``unstable`` (agreement
below ``--agree``), ``window-sensitive``, ``model-sensitive``, ``seed-noise`` (seeds disagree within
one window and model).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ybcal.config import Policy
from ybcal.params.registry import REGISTRY
from ybcal.report import explain as X

OWNER_WP = "infra"

#: price model name → policy overrides (``--policy-set`` items)
MODEL_SETS: dict[str, list[str]] = {
    "bootstrap": [],
    "regime": ["real_price_model='regime'"],
    "garch": ["real_price_model='garch'"],
    "martingale": ["price_drift='martingale'"],
    "centred": ["price_drift='centred'"],
}
DEFAULT_WINDOWS = "full,last365,2021-22,2025-26"
DEFAULT_MODELS = "bootstrap,regime,martingale"


def model_sets(name: str) -> list[str]:
    """``--policy-set`` items of a model name (``a+b`` combines)."""
    out: list[str] = []
    for part in name.split("+"):
        if part not in MODEL_SETS:
            raise ValueError(f"unknown price model {part!r}; choose from {', '.join(MODEL_SETS)} (or a+b)")
        out += MODEL_SETS[part]
    return out


@dataclass(frozen=True)
class RunSpec:
    """One combination."""

    window: str
    model: str
    seed: int

    @property
    def id(self) -> str:
        return f"{self.window.replace(':', '_')}__{self.model}__s{self.seed}"


@dataclass
class RobustConfig:
    """What to run (the CLI maps its flags onto this)."""

    out: Path
    seeds: list[int]
    windows: list[str]
    models: list[str]
    budget: str = "quick"
    policy: str | None = None
    data: list[str] = field(default_factory=list)
    groups: str | None = None
    workers: int = 2
    jobs: int = 1
    sets: list[str] = field(default_factory=list)  #: extra overrides for every run
    sensitivity: bool = False
    max_rounds: int | None = None
    cache: str | None = None
    nice: int = 10
    agree: float = 0.8
    run_dirs: list[Path] = field(default_factory=list)  #: tabulate these finished runs instead of specs

    def specs(self) -> list[RunSpec]:
        return [RunSpec(w, m, s) for w in self.windows for m in self.models for s in self.seeds]


def recommend_cmd(cfg: RobustConfig, spec: RunSpec, run_dir: Path) -> list[str]:
    """The ``ybcal recommend`` command line of one run."""
    cmd = [sys.executable, "-m", "ybcal.cli", "recommend", "--budget", cfg.budget]
    if cfg.policy:
        cmd += ["--policy", cfg.policy]
    for d in cfg.data:
        cmd += ["--data", d]
    if spec.window != "full":
        cmd += ["--window", spec.window]
    cmd += ["--seed", str(spec.seed), "--workers", str(cfg.workers), "--out", str(run_dir)]
    for s in [*cfg.sets, *model_sets(spec.model)]:
        cmd += ["--policy-set", s]
    if cfg.groups:
        cmd += ["--groups", cfg.groups]
    if not cfg.sensitivity:
        cmd += ["--no-sensitivity"]
    if cfg.max_rounds is not None:
        cmd += ["--max-rounds", str(cfg.max_rounds)]
    if cfg.cache:
        cmd += ["--cache", cfg.cache]
    return cmd


def _done(run_dir: Path, cmd: Sequence[str]) -> bool:
    f = run_dir / "robust-run.json"
    if not f.exists():
        return False
    try:
        d = json.loads(f.read_text())
    except (OSError, ValueError):
        return False
    return d.get("rc") == 0 and d.get("cmd") == list(cmd) and (run_dir / "manifest.json").exists()


def subprocess_runner(cmd: Sequence[str], run_dir: Path, nice: int) -> int:
    """Run one ``recommend`` (log to ``run.log``); returns its exit code."""
    import ybcal

    env = dict(os.environ)
    src = str(Path(ybcal.__file__).resolve().parents[1])
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

    def lower() -> None:  # pragma: no cover - runs in the child
        if nice:
            os.nice(nice)

    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / "run.log").open("w") as log:
        p = subprocess.run(list(cmd), stdout=log, stderr=subprocess.STDOUT, env=env, preexec_fn=lower)
    return p.returncode


Runner = Callable[[Sequence[str], Path, int], int]


def run_all(
    cfg: RobustConfig, *, runner: Runner = subprocess_runner, say: Callable[[str], None] = print
) -> list[dict[str, Any]]:
    """Run every combination not already done; returns one status dict per spec."""
    cfg.out.mkdir(parents=True, exist_ok=True)
    specs = cfg.specs()
    cpu = os.cpu_count() or 1
    if cfg.jobs * cfg.workers > cpu:
        say(f"warning: --jobs {cfg.jobs} × --workers {cfg.workers} exceeds {cpu} cores")
    todo, statuses = [], []
    for sp in specs:
        rd = cfg.out / "runs" / sp.id
        cmd = recommend_cmd(cfg, sp, rd)
        if _done(rd, cmd):
            statuses.append({"id": sp.id, "status": "cached"})
        else:
            todo.append((sp, rd, cmd))
    say(f"ybcal robust: {len(specs)} run(s), {len(specs) - len(todo)} done, {len(todo)} to run "
        f"({cfg.jobs} job(s) × {cfg.workers} worker(s))")

    def one(item: tuple[RunSpec, Path, list[str]]) -> dict[str, Any]:
        sp, rd, cmd = item
        t = time.perf_counter()
        say(f"  start {sp.id}")
        rc = runner(cmd, rd, cfg.nice)
        secs = time.perf_counter() - t
        rec = {"id": sp.id, **asdict(sp), "cmd": list(cmd), "rc": rc, "seconds": round(secs, 1)}
        rd.mkdir(parents=True, exist_ok=True)
        (rd / "robust-run.json").write_text(json.dumps(rec, indent=1) + "\n")
        say(f"  {'done' if rc == 0 else f'FAILED (exit {rc})'} {sp.id} in {secs:.0f} s")
        return {"id": sp.id, "status": "ok" if rc == 0 else f"failed ({rc})"}

    if cfg.jobs <= 1:
        statuses += [one(x) for x in todo]
    else:
        with ThreadPoolExecutor(max_workers=cfg.jobs) as ex:
            statuses += list(ex.map(one, todo))
    return statuses


# ===================================================================================================
# Tabulation


def _label(rec: Mapping[str, Any]) -> str:
    m = rec.get("metrics") or {}
    if isinstance(m.get("owner_pin"), Mapping):
        return f"{rec.get('verdict')} (pin)"
    if isinstance(m.get("environment_blocked"), Mapping):
        return f"{rec.get('verdict')} (env)"
    return str(rec.get("verdict"))


def spec_of_dir(run_dir: Path) -> RunSpec:
    """A RunSpec read back from a finished run's manifest (``--runs`` tabulation)."""
    m = json.loads((run_dir / "manifest.json").read_text())
    ex = m.get("extra") or {}
    sets = list(ex.get("policy_set") or [])
    model = next((k for k, v in MODEL_SETS.items() if v and sorted(v) == sorted(sets)), None)
    model = model or ("bootstrap" if not sets else "+".join(sets))
    return RunSpec(str(ex.get("window") or "full"), model, int(m.get("seed", 0)))


def collect_runs(cfg: RobustConfig) -> list[tuple[RunSpec, Path, str]]:
    """``(spec, run_dir, id)`` for every run to tabulate."""
    if cfg.run_dirs:
        return [(spec_of_dir(d), d, d.name) for d in cfg.run_dirs if (d / "manifest.json").exists()]
    return [(sp, cfg.out / "runs" / sp.id, sp.id) for sp in cfg.specs()]


def run_pins(run_dir: Path) -> list[Any]:
    """The owner pins of a finished run's policy (file + ``--policy-set`` overrides)."""
    from ybcal.optimize.pins import parse_pin

    try:
        m = json.loads((run_dir / "manifest.json").read_text())
        pp = m.get("policy_path") or None
        pol = Policy.load(pp) if pp and Path(pp).exists() else Policy()
        pol, _ = pol.with_overrides(list((m.get("extra") or {}).get("policy_set") or []))
    except (OSError, ValueError, KeyError):
        pol = Policy()  # a policy this build cannot read (another branch's keys): the default pins
    return [parse_pin(p, v) for p, v in (pol.owner_pinned or {}).items()]


def feasible_values(run_dir: Path, recs: Mapping[str, Any]) -> dict[str, dict[str, list[str]]]:
    """Per parameter: candidate value (JSON) → constraints of the parameter's own rule violated at the
    best row holding that value, from the run's per-group result tables (``evidence/<g>/results.csv``,
    the final round). A parameter's own constraints are ``metrics.constraints_current`` when the study
    reports them (D-RD-AUD-10), else every constraint. A value absent from a run was not evaluated."""
    from ybcal.params.paramset import mainnet

    shipped = mainnet()
    pins = run_pins(run_dir)
    out: dict[str, dict[str, list[str]]] = {}
    groups = {REGISTRY[p].group for p in recs if p in REGISTRY}
    for g in groups:
        f = run_dir / "evidence" / g.lower() / "results.csv"
        if not f.exists():
            continue
        with f.open() as fh:
            rows = list(csv.DictReader(fh))
        names = [p for p in recs if p in REGISTRY and REGISTRY[p].group == g and REGISTRY[p].tunable]
        recv = {p: recs[p].get("recommended") for p in names}
        full_rows = []
        for row in rows:
            vals = {p: (recv[p] if row.get(p) in (None, "") else _num(row[p])) for p in names}
            ps = {**shipped.to_dict(), **vals}
            # rows a pin excludes are not candidates the run could have chosen (fraction pins, D-RD-INF-2)
            rel = [pin for pin in pins if pin.param in vals and (pin.of is None or pin.of in vals)]
            if all(pin.holds(ps, shipped) for pin in rel):
                full_rows.append((row, vals))
        for p, r in recs.items():
            if p not in REGISTRY or REGISTRY[p].group != g or not REGISTRY[p].tunable:
                continue
            m = r.get("metrics") or {}
            own = set((m.get("constraints_current") or {}).keys()) if isinstance(m, Mapping) else set()
            # per value: the rows closest to the run's recommended set on the group's other parameters
            # (moved together with p by a fraction pin excepted), then the fewest violations
            tied = {pin.param for pin in pins if pin.of == p} | {pin.of for pin in pins if pin.param == p}
            best: dict[str, tuple[int, list[str]]] = {}
            for row, vals in full_rows:
                v = vals[p]
                dist = sum(1 for q in names if q != p and q not in tied and vals[q] != recv[q])
                viol = [c for c in (row.get("violated") or "").split(";") if c]
                if own:
                    viol = [c for c in viol if c in own]
                k = json.dumps(v)
                if k not in best or (dist, len(viol)) < (best[k][0], len(best[k][1])):
                    best[k] = (dist, viol)
            out[p] = {k: v for k, (_, v) in best.items()}
    return out


def _num(s: str) -> Any:
    try:
        return int(s)
    except ValueError:
        try:
            return float(s)
        except ValueError:
            return {"True": True, "False": False}.get(s, s)


def consolidate(
    p: str, current: Any, per_run: Sequence[tuple[str, Mapping[str, list[str]] | None]]
) -> dict[str, Any]:
    """The value feasible (own rule's constraints met) in the most runs; ties → closest to current.
    ``per_run`` = (run id, value → violated constraints) or ``None`` when the run has no table."""
    have = [(rid, d) for rid, d in per_run if d]
    cands: set[str] = set()
    for _, d in have:
        cands |= set(d)
    if not cands:
        return {"value": None, "k": 0, "n": len(per_run), "evaluated": 0, "violations": {}}

    def dist(k: str) -> float:
        v = json.loads(k)
        try:
            return abs(float(v) - float(current))
        except (TypeError, ValueError):
            return 0.0 if v == current else 1.0

    def score(k: str) -> tuple[int, int, float]:
        feas = sum(1 for _, d in have if k in d and not d[k])
        ev = sum(1 for _, d in have if k in d)
        return (-feas, -ev, dist(k))

    best = min(sorted(cands), key=score)
    return {
        "value": json.loads(best),
        "k": -score(best)[0],
        "n": len(per_run),
        "evaluated": -score(best)[1],
        "violations": {rid: d.get(best, ["not evaluated"]) for rid, d in have if d.get(best) != []},
    }


def load_run(run_dir: Path) -> dict[str, Any] | None:
    """``{param: {value, verdict, label}}`` of a finished run (``None`` if it did not finish)."""
    f = run_dir / "evidence" / "recommendations.json"
    m = run_dir / "manifest.json"
    if not f.exists() or not m.exists():
        return None
    recs = json.loads(f.read_text())
    out = {
        p: {"value": r.get("recommended"), "current": r.get("current"), "verdict": r.get("verdict"),
            "label": _label(r)}
        for p, r in recs.items()
    }
    try:
        feas = feasible_values(run_dir, recs)
    except (OSError, ValueError, csv.Error):
        feas = {}
    for p, d in feas.items():
        if p in out:
            out[p]["feasible"] = d
    return out


def _mode(xs: Sequence[Any]) -> tuple[Any, float]:
    if not xs:
        return None, 0.0
    c = Counter(json.dumps(x, sort_keys=True) for x in xs)
    k, n = c.most_common(1)[0]
    return json.loads(k), n / len(xs)


def tabulate(cfg: RobustConfig) -> dict[str, Any]:
    """Read every finished run and write the robustness tables; returns the summary."""
    from ybcal.params.paramset import mainnet

    base = mainnet()
    runs: list[tuple[RunSpec, dict[str, Any]]] = []
    ids: list[str] = []
    missing: list[str] = []
    for sp, rd, rid in collect_runs(cfg):
        r = load_run(rd)
        if r is None:
            missing.append(rid)
        else:
            runs.append((sp, r))
            ids.append(rid)
    params = [k for k, s in REGISTRY.items() if s.tunable]
    rows_long, summary = [], []
    for p in params:
        vals, verds, labels = [], [], []
        by: dict[str, dict[str, list[Any]]] = {"window": {}, "model": {}, "seed": {}}
        cells: dict[tuple[str, str], list[Any]] = {}
        per_run_feas = []
        for (sp, r), rid in zip(runs, ids, strict=True):
            d = r.get(p)
            per_run_feas.append((rid, d.get("feasible") if d else None))
            v = d["value"] if d else base[p]
            verdict = d["verdict"] if d else "NOT RUN"
            label = d["label"] if d else "NOT RUN"
            vals.append(v)
            verds.append(verdict)
            labels.append(label)
            by["window"].setdefault(sp.window, []).append(v)
            by["model"].setdefault(sp.model, []).append(v)
            by["seed"].setdefault(str(sp.seed), []).append(v)
            cells.setdefault((sp.window, sp.model), []).append(v)
            rows_long.append(
                {"run": rid, "window": sp.window, "model": sp.model, "seed": sp.seed, "param": p,
                 "group": REGISTRY[p].group, "current": base[p], "recommended": v, "verdict": verdict,
                 "label": label}
            )
        if not runs or all(lab == "NOT RUN" for lab in labels):
            continue  # a group outside --groups (or never run): nothing to tabulate
        cons = consolidate(p, base[p], per_run_feas)
        mv, agree = _mode(vals)
        none_feasible = cons["value"] is not None and cons["k"] == 0
        mverd, vagree = _mode(verds)
        modal_by = {ax: {k: _mode(v)[0] for k, v in d.items()} for ax, d in by.items()}
        flags = []
        if agree < cfg.agree or vagree < cfg.agree:
            flags.append("unstable")
        if len({json.dumps(v) for v in modal_by["window"].values()}) > 1:
            flags.append("window-sensitive")
        if len({json.dumps(v) for v in modal_by["model"].values()}) > 1:
            flags.append("model-sensitive")
        if any(len({json.dumps(v) for v in c}) > 1 for c in cells.values()):
            flags.append("seed-noise")
        if none_feasible:
            flags.append("none-feasible")
        summary.append(
            {
                "param": p,
                "group": REGISTRY[p].group,
                "current": base[p],
                "modal": mv,
                "agreement": round(agree, 4),
                "modal_verdict": mverd,
                "verdict_agreement": round(vagree, 4),
                "verdicts": dict(Counter(labels)),
                "values": {json.dumps(k): n for k, n in Counter(json.dumps(v) for v in vals).items()},
                "by_window": modal_by["window"],
                "by_model": modal_by["model"],
                "by_seed": modal_by["seed"],
                "flags": flags,
                "n": len(vals),
                "consolidated": cons["value"],
                "feasible_k": cons["k"],
                "feasible_evaluated": cons["evaluated"],
                "violations_at_consolidated": cons["violations"],
            }
        )
    summary.sort(key=lambda s: (0 if "unstable" in s["flags"] else 1 if s["flags"] else 2, s["group"]))
    result = {
        "config": {k: (str(v) if isinstance(v, Path) else v) for k, v in asdict(cfg).items()},
        "runs": ids,
        "missing": missing,
        "summary": summary,
    }
    write_tables(cfg, result, rows_long)
    return result


def _fv(p: str, v: Any) -> str:
    return X.fmt_value(p, v)


def write_tables(
    cfg: RobustConfig, result: Mapping[str, Any], rows_long: Sequence[Mapping[str, Any]]
) -> None:
    out = cfg.out
    with (out / "robust.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["run", "window", "model", "seed", "param", "group", "current",
                                           "recommended", "verdict", "label"])
        w.writeheader()
        w.writerows(rows_long)
    with (out / "robust-summary.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["param", "group", "current", "modal", "agreement", "modal_verdict", "verdict_agreement",
                    "verdicts", "values", "by_window", "by_model", "by_seed", "flags", "n", "consolidated",
                    "feasible_k", "feasible_evaluated", "violations_at_consolidated"])
        for s in result["summary"]:
            w.writerow([s["param"], s["group"], s["current"], s["modal"], s["agreement"], s["modal_verdict"],
                        s["verdict_agreement"], json.dumps(s["verdicts"]), json.dumps(s["values"]),
                        json.dumps(s["by_window"]), json.dumps(s["by_model"]), json.dumps(s["by_seed"]),
                        ";".join(s["flags"]), s["n"], s["consolidated"], s["feasible_k"],
                        s["feasible_evaluated"], json.dumps(s["violations_at_consolidated"])])
    (out / "robust.json").write_text(json.dumps(result, indent=1, default=str) + "\n")
    lines = [
        "# Robustness of the recommendation",
        "",
        (f"{len(result['runs'])} finished run(s) given with --runs: {', '.join(result['runs'])}."
         if cfg.run_dirs else
        f"{len(result['runs'])} run(s) of `ybcal recommend --budget {cfg.budget}`"
        f"{' --groups ' + cfg.groups if cfg.groups else ''}: windows {', '.join(cfg.windows)} × models "
        f"{', '.join(cfg.models)} × seeds {', '.join(map(str, cfg.seeds))}. "
        f"Agreement threshold {cfg.agree:.0%}.")
        + (f" Missing (not finished): {', '.join(result['missing'])}." if result["missing"] else ""),
        "",
        "Flags: **unstable** = the modal value or verdict is shared by fewer runs than the threshold; "
        "*window-sensitive* / *model-sensitive* = the modal value differs between windows / price models; "
        "*none-feasible* = no candidate meets its rule in any run (the consolidated value is then the "
        "closest to current; see BLOCKED / environment limits in the runs' reports); "
        "*seed-noise* = seeds disagree within one window and model. `(pin)` = owner-pinned, `(env)` = "
        "policy unmeetable in this environment.",
        "",
        "**Consolidated** = the value whose own rule's constraints are met in the most runs (every "
        "evaluated candidate of every run counts, not only each run's winner), ties → closest to current; "
        "*feasible k/N* counts those runs, and the violations column names, per run, what fails there.",
        "",
        "| Parameter | Group | Current | Consolidated (feasible k/N) | Modal value | Agreement | Verdicts "
        "| By window | By model | Flags | Violations at the consolidated value |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in result["summary"]:
        p = s["param"]
        verd = ", ".join(f"{k} {v}" for k, v in sorted(s["verdicts"].items(), key=lambda kv: -kv[1]))
        bw = "; ".join(f"{k}: {_fv(p, v)}" for k, v in s["by_window"].items())
        bm = "; ".join(f"{k}: {_fv(p, v)}" for k, v in s["by_model"].items())
        flags = ", ".join(f"**{f}**" if f == "unstable" else f for f in s["flags"]) or "stable"
        cv = "—" if s["consolidated"] is None else f"{_fv(p, s['consolidated'])} ({s['feasible_k']}/{s['n']})"
        viol = "; ".join(f"{k}: {', '.join(v) or '—'}" for k, v in s["violations_at_consolidated"].items())
        lines.append(
            f"| `{p}` | {s['group']} | {_fv(p, s['current'])} | {cv} | {_fv(p, s['modal'])} | "
            f"{s['agreement']:.0%} | {verd} | {bw} | {bm} | {flags} | {viol or 'none'} |".replace("\n", " ")
        )
    lines += [
        "",
        "Per-run values: `robust.csv`; per-parameter detail (values, by seed): `robust-summary.csv`, "
        "`robust.json`. Each run's full report: `runs/<window>__<model>__s<seed>/report.md`.",
        "",
    ]
    (out / "robust.md").write_text("\n".join(lines))


# ===================================================================================================
# CLI


def configure_robust(p: argparse.ArgumentParser) -> None:
    """Extra ``robust`` arguments."""
    p.add_argument("--out", required=True, help="harness directory (runs/ and the tables go here)")
    p.add_argument("--seeds", default="3", help="N seeds from the base seed (--seed / policy.seed), or a "
                   "comma list (default 3)")
    p.add_argument("--windows", default=DEFAULT_WINDOWS, help=f"comma list (default {DEFAULT_WINDOWS})")
    p.add_argument("--models", default=DEFAULT_MODELS,
                   help=f"comma list of {', '.join(MODEL_SETS)} or a+b (default {DEFAULT_MODELS})")
    p.add_argument("--groups", default=None, help="comma-separated subset of groups (default all)")
    p.add_argument("--workers", type=int, default=2, help="worker processes per run (default 2)")
    p.add_argument("--jobs", type=int, default=1, help="concurrent runs (default 1); CPU = jobs × workers")
    p.add_argument("--max-rounds", type=int, default=None)
    p.add_argument("--sensitivity", action="store_true", help="also run the joint sensitivity in every run")
    p.add_argument("--cache", default=None, metavar="DIR", help="shared on-disk evaluation cache")
    p.add_argument("--nice", type=int, default=10, help="niceness of each run (default 10)")
    p.add_argument("--agree", type=float, default=0.8, help="agreement threshold for 'unstable' (0.8)")
    p.add_argument("--table-only", action="store_true", help="only re-tabulate finished runs")
    p.add_argument("--runs", nargs="+", default=[], metavar="DIR",
                   help="tabulate these finished recommend directories (implies --table-only)")
    p.add_argument("--dry-run", action="store_true", help="print the commands and exit")


def config_from_args(args: argparse.Namespace) -> RobustConfig:
    pol = Policy.load(args.policy)
    if args.runs:
        Path(args.out).mkdir(parents=True, exist_ok=True)
    if "," in args.seeds or not args.seeds.isdigit():
        seeds = [int(x) for x in args.seeds.split(",") if x.strip()]
    else:
        s0 = int(args.seed if args.seed is not None else pol.seed)
        seeds = [s0 + i for i in range(int(args.seeds))]
    from ybcal.data.inputs import parse_window

    windows = [w.strip() for w in args.windows.split(",") if w.strip()]
    for w in windows:
        parse_window(w)
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    for m in models:
        model_sets(m)
    data = [str(Path(d).resolve()) for d in args.data]
    return RobustConfig(
        out=Path(args.out),
        seeds=seeds,
        windows=windows,
        models=models,
        budget=args.budget,
        policy=str(Path(args.policy).resolve()) if args.policy else None,
        data=data,
        groups=args.groups,
        workers=args.workers,
        jobs=args.jobs,
        sets=list(getattr(args, "policy_set", None) or []),
        sensitivity=args.sensitivity,
        max_rounds=args.max_rounds,
        cache=args.cache,
        nice=args.nice,
        agree=args.agree,
        run_dirs=[Path(d) for d in args.runs],
    )


def cli_robust(args: argparse.Namespace) -> int:
    """Seeds × windows × models of ``recommend``, then the stability tables."""
    try:
        cfg = config_from_args(args)
    except (ValueError, KeyError, FileNotFoundError) as e:
        print(f"ybcal robust: {e}", file=sys.stderr)
        return 2
    if args.window:
        print("ybcal robust: --window is ignored; use --windows", file=sys.stderr)
    if args.dry_run:
        for sp in cfg.specs():
            print(" ".join(recommend_cmd(cfg, sp, cfg.out / "runs" / sp.id)))
        return 0
    if not args.table_only and not cfg.run_dirs:
        st = run_all(cfg, say=lambda m: print(m, flush=True))
        bad = [s["id"] for s in st if str(s["status"]).startswith("failed")]
        if bad:
            print(f"ybcal robust: {len(bad)} run(s) failed: {', '.join(bad)} (see runs/<id>/run.log)")
    res = tabulate(cfg)
    unstable = [s["param"] for s in res["summary"] if "unstable" in s["flags"]]
    print(f"robust: {len(res['runs'])} run(s) tabulated, {len(res['missing'])} missing; "
          f"{len(unstable)} unstable parameter(s){': ' + ', '.join(unstable[:12]) if unstable else ''}")
    print(f"tables: {cfg.out / 'robust.md'}")
    return 0

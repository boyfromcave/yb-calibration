"""Stress-scenario library: ``scenarios/*.toml`` → price paths + behaviour schedules (PLAN §4.3).

A scenario is a **price program** applied on top of a **base** return process, plus **behaviour
schedules** (hashrate shares, attestor availability, attackers, developer/owner absence, feed
outages) and free-form **constants**. ``docs/scenarios.md`` documents every shipped file.

Schema (one file = one scenario, or a family through ``[[variants]]``)::

    name = "crash-70-1d"                 # required, unique
    description = "…"                    # required
    stresses = "…"                       # what it stresses (PLAN §4.3 table)
    tags = ["core", "price"]             # "core" = part of the quick budget's scenario set
    horizon_days = 60                    # required
    resolution = "block"                 # "block" (≤ 120 days) | "hour"
    p0_usd = 0.40                        # start price (default 0.40)

    [base]                               # the return process the program is applied to
    model = "gbm"                        # gbm | merton | garch | regime | bootstrap | flat
    params = { sigma = 0.6 }             # overrides on the model's YEC-like preset
    fallback = "garch"                   # bootstrap only: model used when no real data is given
    center = true                        # remove the base's expected log drift (default): the
                                         # program alone sets the trend, the base adds noise

    [[segments]]                         # the price program, applied in this order:
    kind = "ramp"                        #   vol (scales base returns) first, then additive kinds,
    start_days = 20                      #   then hold windows (zero returns)
    duration_days = 1
    pct = -70

    [schedules.enforcing_share]          # piecewise-constant behaviour series
    default = 0.80
    changes = [{ at_days = 30, value = 0.45 }]
    ramps = [{ start_days = 75, duration_days = 5, to = 0.80 }]
    outages = { rate_per_day = 0.1, mean_hours = 2, value = 0.0 }   # stochastic → per path

    [constants]                          # scalars the studies read (documented per scenario)
    attacker_bias_bps = 1000

    [[variants]]                         # optional: expand into a family
    name = "oracle-attack-34"
    tags = ["core"]
    set = { "schedules.attacker_share.changes.0.value" = 0.34 }   # dotted path → value

Times accept ``_days``, ``_hours`` or ``_blocks`` suffixes (``at_*``, ``start_*``,
``duration_*``) and are converted to steps of the requested resolution, so one file serves both
block and hour mode.

Segment kinds (``pct`` is a percent move of the price, applied in log space as ``log(1 + pct/100)``):

========  =====================================================================================
hold      ``start``, ``duration``: price frozen (returns zero), resumes from the held level
drift     ``start``, ``duration`` (default: to the end), ``rate``: added annual log drift
vol       ``start``, ``duration``, ``mult``: base returns scaled
jump      ``start``, ``duration``, ``rate_per_day``, ``mean_pct``, ``sd_pct``: random log jumps
shock     ``at``, ``pct``: instantaneous move
ramp      ``start``, ``duration``, ``pct``: total move spread evenly (log-linear) over the window
wick      ``at``, ``pct``, ``duration``, ``recover`` (1.0): instant move, then linear recovery
========  =====================================================================================

Owner: WP-2.
"""

from __future__ import annotations

import copy
import math
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from ybcal.config import REPO_ROOT
from ybcal.data import synthetic
from ybcal.data.pricepath import STEP_SECONDS, log_returns, resample, steps_per_year, usd_to_micro
from ybcal.types import PricePath, Resolution

OWNER_WP = "WP-2"

#: Shipped scenario directory (repo root ``scenarios/``).
SCENARIO_DIR: Path = REPO_ROOT / "scenarios"

SEGMENT_KINDS: tuple[str, ...] = ("hold", "drift", "vol", "jump", "shock", "ramp", "wick")
BASE_MODELS: tuple[str, ...] = ("gbm", "merton", "garch", "regime", "bootstrap", "flat")

#: Known behaviour schedules: name → (meaning, unit). Unknown names are rejected (typo guard).
SCHEDULES: dict[str, tuple[str, str]] = {
    "enforcing_share": ("hash share running enforcing Yellowback nodes (ACT-4/ACT-6 counts)", "fraction"),
    "signal_share": (
        "share of blocks setting the signal bit (dropped after the sunset, index.cpp:713)",
        "fraction",
    ),
    "tagging_share": ("hash share whose blocks carry a price tag (PRICE-1 fill)", "fraction"),
    "attacker_share": ("hash share of a colluding pool coalition", "fraction"),
    "attacker_bias_bps": ("bias of the coalition's quotes vs the true price (signed)", "bps"),
    "stale_pool_share": ("hash share of pools whose quotes are stale", "fraction"),
    "stale_lag_blocks": ("staleness of those pools' quotes", "blocks"),
    "frozen_pool_share": ("hash share of pools repeating one constant quote (PIN-1 target)", "fraction"),
    "feed_up": ("1 while exchange feeds are reachable, 0 during a feed outage (no quotes)", "flag"),
    "attestor_uptime": ("per-attestor availability probability", "probability"),
    "attestors_down": ("number of attestors offline (deterministic, on top of uptime)", "count"),
    "attestor_capture_weight": ("bond-weight share controlled by an adversary", "fraction"),
    "attestor_bias_bps": ("bias of captured attestors' prices (signed)", "bps"),
    "dev_present": ("1 while developers can ship a release, 0 while absent", "flag"),
    "owner_present_fraction": ("fraction of vault owners reachable", "fraction"),
    "renewal": ("1 once a renewal parameter set has been released (W18)", "flag"),
    "mint_demand": ("minting demand multiplier relative to the policy adoption case", "multiplier"),
}

_TOP_KEYS = {
    "name",
    "description",
    "stresses",
    "tags",
    "horizon_days",
    "resolution",
    "p0_usd",
    "base",
    "segments",
    "schedules",
    "constants",
    "variants",
    "core",
}


# ---------------------------------------------------------------------------------------------------
# Time helpers


def to_steps(spec: Mapping[str, Any], key: str, step_seconds: int, default: int | None = None) -> int | None:
    """``spec[key_days|key_hours|key_blocks]`` → whole steps (rounded); ``default`` when absent."""
    for suffix, seconds in (("days", 86400), ("hours", 3600), ("blocks", 75)):
        k = f"{key}_{suffix}"
        if k in spec:
            return round(float(spec[k]) * seconds / step_seconds)
    return default


# ---------------------------------------------------------------------------------------------------
# Schema objects


@dataclass(frozen=True)
class Segment:
    """One step of the price program (see the module docstring for fields per kind)."""

    kind: str
    spec: Mapping[str, Any]

    def window(self, n: int, step_s: int) -> tuple[int, int]:
        """``[start, stop)`` in steps, clipped to ``[0, n]``."""
        a = to_steps(self.spec, "start", step_s, None)
        if a is None:
            a = to_steps(self.spec, "at", step_s, 0)
        assert a is not None
        d = to_steps(self.spec, "duration", step_s, None)
        b = n if d is None else a + max(d, 1)
        return max(0, min(a, n)), max(0, min(b, n))

    def at(self, step_s: int) -> int:
        """The ``at`` (or ``start``) step."""
        v = to_steps(self.spec, "at", step_s, None)
        if v is None:
            v = to_steps(self.spec, "start", step_s, 0)
        assert v is not None
        return v


@dataclass(frozen=True)
class ScheduleSpec:
    """A piecewise-constant (plus ramps, plus optional stochastic outages) behaviour series."""

    name: str
    default: float
    changes: tuple[Mapping[str, Any], ...] = ()
    ramps: tuple[Mapping[str, Any], ...] = ()
    outages: Mapping[str, Any] | None = None

    def array(
        self, n: int, step_s: int, rng: np.random.Generator | None = None, n_paths: int = 1
    ) -> np.ndarray:
        """Values per step: shape ``(n,)``, or ``(n_paths, n)`` when ``outages`` is set."""
        v = np.full(n, float(self.default))
        events: list[tuple[int, str, Mapping[str, Any]]] = []
        events += [(to_steps(c, "at", step_s, 0) or 0, "change", c) for c in self.changes]
        events += [(to_steps(r, "start", step_s, 0) or 0, "ramp", r) for r in self.ramps]
        for t, kind, spec in sorted(events, key=lambda e: e[0]):
            if t >= n:
                continue
            t = max(t, 0)
            if kind == "change":
                v[t:] = float(spec["value"])
            else:
                d = max(1, to_steps(spec, "duration", step_s, 1) or 1)
                start_val = v[t]
                end = min(n, t + d)
                frac = (np.arange(t, end) - t + 1) / d
                v[t:end] = start_val + (float(spec["to"]) - start_val) * frac
                v[end:] = float(spec["to"])
        if not self.outages:
            return v
        if rng is None:
            raise ValueError(f"schedule {self.name} has stochastic outages: pass an rng")
        o = self.outages
        mask = synthetic.renewal_outages(
            n_paths, 1, n, step_s, [float(o["rate_per_day"])], [float(o["mean_hours"])], rng
        )[:, 0, :]
        return np.where(mask, float(o.get("value", 0.0)), v[None, :])


@dataclass(frozen=True)
class BaseSpec:
    """The base return process."""

    model: str = "gbm"
    params: Mapping[str, Any] = field(default_factory=dict)
    fallback: str = "garch"
    center: bool = True  #: remove the model's expected log drift: the program alone sets the trend

    def make_model(self) -> synthetic.PriceModel | None:
        """The preset with overrides (``None`` for ``flat`` and ``bootstrap``)."""
        if self.model in ("flat", "bootstrap"):
            return None
        return synthetic.preset(self.model, **dict(self.params))


@dataclass
class ScenarioRun:
    """One realisation of a scenario."""

    scenario: str
    paths: PricePath  #: provenance "scenario"
    schedules: dict[str, np.ndarray]  #: (n,) deterministic or (n_paths, n) stochastic
    constants: dict[str, Any]

    @property
    def n_steps(self) -> int:
        """Steps per path."""
        return self.paths.n_steps


@dataclass(frozen=True)
class Scenario:
    """A parsed scenario. Use :meth:`generate` to realise it."""

    name: str
    description: str
    horizon_days: float
    resolution: Resolution
    p0: int
    base: BaseSpec
    segments: tuple[Segment, ...] = ()
    schedules: Mapping[str, ScheduleSpec] = field(default_factory=dict)
    constants: Mapping[str, Any] = field(default_factory=dict)
    stresses: str = ""
    tags: tuple[str, ...] = ()
    source: str = ""
    family: str | None = None

    @property
    def core(self) -> bool:
        """Part of the quick budget's ``core`` scenario set."""
        return "core" in self.tags

    def n_steps(self, resolution: Resolution | None = None, horizon_days: float | None = None) -> int:
        """Prices per path at ``resolution`` over ``horizon_days`` (inclusive of step 0)."""
        res = resolution or self.resolution
        h = self.horizon_days if horizon_days is None else horizon_days
        return round(h * 86400 / STEP_SECONDS[res]) + 1

    # -- generation ----------------------------------------------------------------------------------
    def _base_returns(
        self,
        n_paths: int,
        n: int,
        res: Resolution,
        rng: np.random.Generator,
        data: PricePath | None,
        base_path: PricePath | None,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        info: dict[str, Any] = {"base_model": self.base.model}
        if base_path is not None:
            bp = base_path if base_path.resolution == res else resample(base_path, res)
            r = np.nan_to_num(log_returns(bp), nan=0.0)
            if r.shape[1] < n - 1:
                raise ValueError(f"base path has {r.shape[1] + 1} steps; scenario needs {n}")
            idx = np.arange(n_paths) % r.shape[0]
            info.update(base_model="base_path", base_provenance=bp.provenance)
            return r[idx, : n - 1].copy(), info
        if self.base.model == "flat":
            return np.zeros((n_paths, n - 1)), info
        if self.base.model == "bootstrap":
            if data is not None:
                model: synthetic.PriceModel = synthetic.BlockBootstrap.fit(data)
                info.update(base_provenance="real", base_data=str(data.meta.get("source_file", "")))
            else:
                model = synthetic.preset(self.base.fallback)
                info.update(base_model=self.base.fallback, base_fallback=True, base_provenance="synthetic")
        else:
            model = self.base.make_model()  # type: ignore[assignment]
            info["base_provenance"] = "synthetic"
        info["base_params"] = model.params()
        r = model.log_returns(n_paths, n, res, rng)[:, : n - 1]
        if self.base.center:
            r = r - model.expected_log_drift() / steps_per_year(res)
            info["base_centered"] = True
        return r, info

    def apply_program(self, base_r: np.ndarray, res: Resolution, rng: np.random.Generator) -> np.ndarray:
        """Apply the price program to base returns ``(paths, n-1)`` (returns a new array)."""
        r = base_r.copy()
        paths, nr = r.shape
        nr + 1
        step_s = STEP_SECONDS[res]
        dt = 1.0 / steps_per_year(res)
        holds: list[tuple[int, int]] = []
        for seg in self.segments:  # vol first: it scales the base only
            if seg.kind == "vol":
                a, b = seg.window(nr, step_s)
                r[:, a:b] *= float(seg.spec["mult"])
        for seg in self.segments:
            s = seg.spec
            if seg.kind in ("vol",):
                continue
            if seg.kind == "hold":
                holds.append(seg.window(nr, step_s))
            elif seg.kind == "drift":
                a, b = seg.window(nr, step_s)
                r[:, a:b] += float(s["rate"]) * dt
            elif seg.kind == "ramp":
                a, b = seg.window(nr, step_s)
                full = max(1, to_steps(s, "duration", step_s, 1) or 1)
                r[:, a:b] += math.log1p(float(s["pct"]) / 100.0) / full
            elif seg.kind == "shock":
                t = seg.at(step_s)
                if 1 <= t <= nr:
                    r[:, t - 1] += math.log1p(float(s["pct"]) / 100.0)
            elif seg.kind == "wick":
                t = seg.at(step_s)
                jump = math.log1p(float(s["pct"]) / 100.0)
                d = max(1, to_steps(s, "duration", step_s, 1) or 1)
                if 1 <= t <= nr:
                    r[:, t - 1] += jump
                    r[:, t : min(nr, t + d)] += -float(s.get("recover", 1.0)) * jump / d
            elif seg.kind == "jump":
                a, b = seg.window(nr, step_s)
                lam = float(s["rate_per_day"]) * step_s / 86400.0
                k = rng.poisson(lam, size=(paths, max(b - a, 0)))
                mean = math.log1p(float(s.get("mean_pct", 0.0)) / 100.0)
                sd = float(s.get("sd_pct", 0.0)) / 100.0
                r[:, a:b] += k * mean + np.sqrt(k) * sd * rng.standard_normal(k.shape)
        for a, b in holds:
            r[:, a:b] = 0.0
        return r

    def schedule_arrays(
        self, n: int, res: Resolution, rng: np.random.Generator | None = None, n_paths: int = 1
    ) -> dict[str, np.ndarray]:
        """Every schedule evaluated on ``n`` steps of ``res``."""
        return {k: v.array(n, STEP_SECONDS[res], rng, n_paths) for k, v in self.schedules.items()}

    def generate(
        self,
        rng: np.random.Generator,
        n_paths: int = 1,
        *,
        data: PricePath | None = None,
        base_path: PricePath | None = None,
        resolution: Resolution | None = None,
        horizon_days: float | None = None,
        p0: int | None = None,
        t0: datetime = synthetic.SYNTH_T0,
    ) -> ScenarioRun:
        """Realise the scenario: ``n_paths`` price paths (``provenance="scenario"``) + schedules.

        * ``data`` — real prices for a ``bootstrap`` base (otherwise the fallback model is used and
          ``meta["base_fallback"]`` is set);
        * ``base_path`` — replay these paths' returns as the base instead of any model (e.g. a real
          history window), cycling if there are fewer paths than ``n_paths``;
        * ``resolution``/``horizon_days`` override the file (segments beyond the horizon are dropped);
        * deterministic for a given ``rng`` state. Large block-mode ensembles: call in batches with
          independent generators (``Env.rng_for(name, batch)``).
        """
        res = resolution or self.resolution
        n = self.n_steps(res, horizon_days)
        start = int(p0 if p0 is not None else self.p0)
        base_r, info = self._base_returns(n_paths, n, res, rng, data, base_path)
        r = self.apply_program(base_r, res, rng)
        meta = {
            "scenario": self.name,
            "horizon_days": self.horizon_days if horizon_days is None else horizon_days,
            "p0": start,
            **info,
        }
        pp = synthetic.returns_to_path(
            r, start, 1.0 / steps_per_year(res), provenance="scenario", meta=meta, t0=t0
        )
        return ScenarioRun(self.name, pp, self.schedule_arrays(n, res, rng, n_paths), dict(self.constants))


# ---------------------------------------------------------------------------------------------------
# Parsing


def _set_dotted(doc: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur: Any = doc
    for i, p in enumerate(parts[:-1]):
        nxt_is_index = parts[i + 1].isdigit()
        if isinstance(cur, list):
            cur = cur[int(p)]
            continue
        if p not in cur:
            cur[p] = [] if nxt_is_index else {}
        cur = cur[p]
    last = parts[-1]
    if isinstance(cur, list):
        cur[int(last)] = value
    else:
        cur[last] = value


def _parse_schedule(name: str, spec: Any) -> ScheduleSpec:
    if name not in SCHEDULES:
        raise ValueError(f"unknown schedule {name!r}; known: {sorted(SCHEDULES)}")
    if not isinstance(spec, dict):
        return ScheduleSpec(name, float(spec))
    unknown = set(spec) - {"default", "changes", "ramps", "outages"}
    if unknown:
        raise ValueError(f"schedule {name}: unknown keys {sorted(unknown)}")
    return ScheduleSpec(
        name,
        float(spec.get("default", 0.0)),
        tuple(spec.get("changes", ())),
        tuple(spec.get("ramps", ())),
        spec.get("outages"),
    )


def parse_scenario(doc: Mapping[str, Any], source: str = "", family: str | None = None) -> Scenario:
    """Build one :class:`Scenario` from a TOML mapping (without ``variants``)."""
    unknown = set(doc) - _TOP_KEYS
    if unknown:
        raise ValueError(f"{source}: unknown keys {sorted(unknown)}")
    for req in ("name", "description", "horizon_days"):
        if req not in doc:
            raise ValueError(f"{source}: missing {req!r}")
    res = doc.get("resolution", "block")
    if res not in ("block", "hour"):
        raise ValueError(f"{source}: resolution must be block or hour")
    if res == "block" and float(doc["horizon_days"]) > 120:
        raise ValueError(f"{source}: block-mode horizon must be <= 120 days (PLAN §3.3)")
    b = dict(doc.get("base", {}))
    model = b.get("model", "gbm")
    if model not in BASE_MODELS:
        raise ValueError(f"{source}: unknown base model {model!r}")
    unknown_b = set(b) - {"model", "params", "fallback", "center"}
    if unknown_b:
        raise ValueError(f"{source}: unknown [base] keys {sorted(unknown_b)}")
    base = BaseSpec(model, dict(b.get("params", {})), b.get("fallback", "garch"), bool(b.get("center", True)))
    base.make_model()  # validates parameter names
    segs = []
    for s in doc.get("segments", []):
        if s.get("kind") not in SEGMENT_KINDS:
            raise ValueError(f"{source}: unknown segment kind {s.get('kind')!r}")
        need = {
            "drift": ("rate",),
            "vol": ("mult",),
            "jump": ("rate_per_day",),
            "shock": ("pct",),
            "ramp": ("pct",),
            "wick": ("pct",),
            "hold": (),
        }[s["kind"]]
        for k in need:
            if k not in s:
                raise ValueError(f"{source}: segment {s['kind']} needs {k!r}")
        segs.append(Segment(s["kind"], {k: v for k, v in s.items() if k != "kind"}))
    tags = tuple(doc.get("tags", ())) + (("core",) if doc.get("core") else ())
    return Scenario(
        name=str(doc["name"]),
        description=str(doc["description"]),
        horizon_days=float(doc["horizon_days"]),
        resolution=res,
        p0=int(usd_to_micro(float(doc.get("p0_usd", 0.40)))),
        base=base,
        segments=tuple(segs),
        schedules={k: _parse_schedule(k, v) for k, v in doc.get("schedules", {}).items()},
        constants=dict(doc.get("constants", {})),
        stresses=str(doc.get("stresses", "")),
        tags=tags,
        source=source,
        family=family,
    )


def parse_document(doc: Mapping[str, Any], source: str = "") -> list[Scenario]:
    """One scenario, or one per ``[[variants]]`` entry (each overriding the shared body)."""
    variants = doc.get("variants")
    if not variants:
        return [parse_scenario(doc, source)]
    body = {k: v for k, v in doc.items() if k != "variants"}
    out = []
    for v in variants:
        d = copy.deepcopy(body)
        for key in ("name", "description", "tags", "horizon_days", "stresses"):
            if key in v:
                d[key] = v[key]
        for path, value in (v.get("set") or {}).items():
            _set_dotted(d, path, value)
        if "name" not in v:
            raise ValueError(f"{source}: every variant needs a name")
        out.append(parse_scenario(d, source, family=str(doc["name"])))
    return out


def load_file(path: str | Path) -> list[Scenario]:
    """Parse a scenario TOML file (a family yields several scenarios)."""
    p = Path(path)
    with p.open("rb") as fh:
        return parse_document(tomllib.load(fh), str(p))


def load_library(directory: str | Path | None = None) -> dict[str, Scenario]:
    """Every scenario under ``directory`` (default the shipped ``scenarios/``), keyed by name."""
    d = Path(directory) if directory is not None else SCENARIO_DIR
    out: dict[str, Scenario] = {}
    for f in sorted(d.glob("*.toml")):
        for sc in load_file(f):
            if sc.name in out:
                raise ValueError(f"duplicate scenario name {sc.name!r} ({out[sc.name].source}, {f})")
            out[sc.name] = sc
    return out


def scenario_set(which: str = "core", directory: str | Path | None = None) -> dict[str, Scenario]:
    """``core`` (the quick budget) or ``all`` — matches ``Budget.scenario_set``."""
    lib = load_library(directory)
    if which == "all":
        return lib
    if which == "core":
        return {k: v for k, v in lib.items() if v.core}
    raise ValueError("scenario set must be 'core' or 'all'")


def get(name: str, directory: str | Path | None = None) -> Scenario:
    """One scenario by name."""
    lib = load_library(directory)
    try:
        return lib[name]
    except KeyError:
        raise KeyError(f"unknown scenario {name!r}; known: {sorted(lib)}") from None


def names(directory: str | Path | None = None) -> list[str]:
    """All scenario names."""
    return sorted(load_library(directory))


def families(lib: Mapping[str, Scenario]) -> dict[str, list[str]]:
    """Family name → member scenario names."""
    out: dict[str, list[str]] = {}
    for sc in lib.values():
        if sc.family:
            out.setdefault(sc.family, []).append(sc.name)
    return out


def describe_library(lib: Mapping[str, Scenario]) -> Iterable[str]:
    """One line per scenario (for CLIs and docs)."""
    for sc in sorted(lib.values(), key=lambda s: s.name):
        yield (
            f"{sc.name:28s} {sc.resolution:5s} {sc.horizon_days:7.1f} d  "
            f"{'core ' if sc.core else '     '}{sc.description}"
        )

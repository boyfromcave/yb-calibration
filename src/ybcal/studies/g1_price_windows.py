"""G1 — price medians: pFast/Mid/SlowWindow and min-fills (PLAN §5.1).

Owner: WP-7a. Method, metric definitions and decision rule: ``docs/studies/g1.md``.

How it works
------------
Every scenario the study needs (calm, two crashes, pump-and-dump, flash wick, feed outage, oracle
attack) is realised **once per run** at block resolution — true price paths (synthetic, or a block
bootstrap of the real price when ``env.data["price"]`` is real), then the coinbase quote-tag stream
of WP-3's oracle (``sim.oracle.generate_block_inputs``: honest pools at the policy's tagging share,
independent pool outages, the scenario's tagging/feed schedules, a coalition for the attack). None
of that depends on G1's parameters, so candidates share it (common random numbers), and PRICE-1 is
answered for any ``(window, fill)`` from one ``vkernels.RollingMedian`` per realisation, memoised
per process. A candidate's evaluation is then the three medians it names plus vectorised metrics.
``tests/studies/test_g1_price_windows.py`` checks that these medians, pMint, pClaim, HALT-3 and
NO_PRICE equal ``sim.engine.simulate_blocks`` on the same inputs.

Shared helpers here (``ScenarioRealisation``, ``realise``, ``real_price``, ``evidence_dir``,
``plot_style``) are also used by the G2 study.
"""

from __future__ import annotations

import functools
import hashlib
import math
import tempfile
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from ybcal.data import scenarios as SC
from ybcal.data import synthetic as SY
from ybcal.model import vkernels as V
from ybcal.optimize.robust import cvar
from ybcal.optimize.sensitivity import oat_from_table, sensitivity_sentence
from ybcal.params.paramset import ParamSet
from ybcal.params.registry import REGISTRY, params_for_group
from ybcal.sim import oracle as O
from ybcal.studies.base import (
    Budget,
    Env,
    Metrics,
    Recommendation,
    ResultTable,
    decide_with_materiality,
    final_verdict,
)
from ybcal.types import PricePath
from ybcal.units import BLOCKS_PER_DAY, BLOCKS_PER_HOUR, BLOCKS_PER_YEAR, BPS

if TYPE_CHECKING:  # pragma: no cover
    from ybcal.config import Policy

OWNER_WP = "WP-7a"

GROUP = "G1"
WINDOWS = ("pFastWindow", "pMidWindow", "pSlowWindow")
FILLS = {"pFastWindow": "pFastMinFill", "pMidWindow": "pMidMinFill", "pSlowWindow": "pSlowMinFill"}

# ---------------------------------------------------------------------------------------------------
# Study assumptions (documented in docs/studies/g1.md; D-WP7a-3, D-WP7a-2)

#: Independent outages of each honest pool's price feed (signal-only tags while out).
POOL_OUTAGE_RATE_PER_DAY = 1.0 / 30.0
POOL_OUTAGE_MEAN_HOURS = 4.0
#: Harmful-direction attack success tolerated at ``attack_share_min`` (fraction of attack blocks in
#: which the coalition moves pMint up / pClaim down by at least half its bias).
ATTACK_MOVED_TOL = 0.05
#: Quote bias of the simulated coalition when the scenario does not set one.
DEFAULT_ATTACK_BIAS_BPS = 1000
#: Blocks skipped at the start of every series (the longest searchable slow window): medians are
#: undefined before their windows fill, which is a start-up artefact, not an availability failure.
WARMUP_BLOCKS = REGISTRY["pSlowWindow"].bounds[1]
#: Days kept after a scenario's last programmed event (crash end, outage end, …).
TAIL_DAYS = 6.0

#: Rare-event metrics need more paths than the rest: background NO_PRICE comes from long single-pool
#: outages (calm: 3× paths over ≥ 60 days), the crash-lag CVaR from the crash tails (2× paths).
CALM_PATH_FACTOR, CALM_MIN_DAYS, CRASH_PATH_FACTOR = 3, 60.0, 2

#: Scenario roles → default library names (``env.scenarios`` entries of the same name win).
ROLES: dict[str, tuple[str, ...]] = {
    "calm": ("calm-90d",),
    "crash": ("crash-70-1d", "crash-90-30d"),
    "pump": ("pump-dump-3x",),
    "wick": ("flash-wick-50-1h",),
    "outage": ("feed-outage-6h",),
    "attack": ("oracle-attack-34",),
}
#: The sudden crash HALT-3 recall is measured on (coupling constraint, D-WP7a-4).
RECALL_SCENARIO = "crash-70-1d"
RECALL_WITHIN_BLOCKS = BLOCKS_PER_DAY  # HALT-3 must fire within one day of the crash start
#: Crash exposure window: from the crash start to two days after its end.
EXPOSURE_TAIL_BLOCKS = 2 * BLOCKS_PER_DAY


# ---------------------------------------------------------------------------------------------------
# Shared helpers (also used by G2)


def real_price(env: Env) -> PricePath | None:
    """``env.data["price"]`` when it is a real price path, else ``None``."""
    p = env.data.get("price") if isinstance(env.data, Mapping) else None
    if isinstance(p, PricePath) and p.provenance == "real":
        return p
    return None


def long_price(env: Env) -> PricePath | None:
    """The longest real history for long-horizon consumers (G3 class C terms, G4, drawdowns):
    ``env.data["price_daily"]`` when a daily series was given next to the hourly one, else
    :func:`real_price` (D-RD-INF-1). The daily series sits on the hourly grid with its ``filled``
    mask, so fitting it (``synthetic.fit_returns_of``) uses daily observed-to-observed returns."""
    p = env.data.get("price_daily") if isinstance(env.data, Mapping) else None
    if isinstance(p, PricePath) and p.provenance == "real":
        return p
    return real_price(env)


_MODELS: dict[tuple, Any] = {}


def real_model(env: Env, pp: PricePath | None = None) -> Any:
    """The price model of real data (policy ``real_price_model``, D-RD-INF-5): ``bootstrap`` = the
    demeaned block bootstrap of ``pp`` (default :func:`real_price`); ``regime`` / ``garch`` = that model
    fitted on :func:`long_price` (the daily series when given) and centred. Memoised per data and kind."""

    kind = str(getattr(env.policy, "real_price_model", "bootstrap") or "bootstrap")
    src = pp if pp is not None else real_price(env)
    if src is None:
        raise ValueError("real_model needs a real price path")
    if kind == "bootstrap":
        return SY.BlockBootstrap.fit(src)
    if kind not in ("regime", "garch"):
        raise ValueError(f"real_price_model must be bootstrap, regime or garch (got {kind!r})")
    lp = long_price(env) or src
    key = (kind, hashlib.sha1(np.ascontiguousarray(lp.prices).tobytes()).hexdigest()[:16])
    hit = _MODELS.get(key)
    if hit is None:
        inner = SY.MODELS[kind].fit(lp)
        hit = SY.Centred(inner, meta=dict(getattr(inner, "meta", {}) or {}, centred=True))
        _MODELS[key] = hit
    return hit


def provenance_of(env: Env) -> str:
    """``real-data`` when a real price path drives the run, else ``synthetic``."""
    return "real-data" if real_price(env) is not None else "synthetic"


def data_fingerprint(env: Env) -> str:
    """Short content hash of the real price path (``"synthetic"`` when none)."""
    p = real_price(env)
    if p is None:
        return "synthetic"
    h = hashlib.sha1(np.ascontiguousarray(p.prices).tobytes())
    d = env.data.get("price_daily") if isinstance(env.data, Mapping) else None
    if isinstance(d, PricePath):  # D-RD-INF-1: a long series changes long-horizon results
        h.update(b"daily")
        h.update(np.ascontiguousarray(d.prices).tobytes())
    kind = str(getattr(env.policy, "real_price_model", "bootstrap") or "bootstrap")
    if kind != "bootstrap":  # D-RD-INF-5: the model of the data changes every memoised ensemble
        h.update(kind.encode())
    return h.hexdigest()[:16]


def paths_for(budget: Budget) -> int:
    """Paths per scenario: quick 8, standard 50, deep 250 (``budget.paths // 8``, at least 8); a
    test budget with fewer than 8 paths uses them as given."""
    p = int(budget.paths)
    return max(1, p) if p < 8 else max(8, p // 8)


def evidence_dir(meta_out: str | None, group: str) -> Path:
    """Where figures/CSVs go: ``env.data["out_dir"]/<group>`` if given, else a fresh temp dir."""
    if meta_out:
        d = Path(meta_out) / group.lower()
    else:
        d = Path(tempfile.mkdtemp(prefix=f"ybcal-{group.lower()}-"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def out_dir_of(env: Env) -> str | None:
    """``env.out_dir``, else ``env.data["out_dir"]`` (or ``"workdir"``), as a string, if set."""
    if getattr(env, "out_dir", None):
        return str(env.out_dir)
    for k in ("out_dir", "workdir"):
        v = env.data.get(k) if isinstance(env.data, Mapping) else None
        if v:
            return str(v)
    return None


def scenario(env: Env, name: str) -> SC.Scenario:
    """``env.scenarios[name]`` when present, else the shipped library scenario."""
    s = env.scenarios.get(name) if isinstance(env.scenarios, Mapping) else None
    return s if isinstance(s, SC.Scenario) else SC.get(name)


def _event_end_days(scen: SC.Scenario) -> float:
    """Last programmed event (segment end or schedule change), in days."""
    n = scen.n_steps("block")
    end = 0
    for seg in scen.segments:
        a, b = seg.window(n - 1, 75)
        end = max(
            end,
            b
            if "duration_days" in seg.spec or "duration_hours" in seg.spec or "duration_blocks" in seg.spec
            else a,
        )
    for spec in scen.schedules.values():
        for c in spec.changes:
            end = max(end, SC.to_steps(c, "at", 75, 0) or 0)
        for r in spec.ramps:
            end = max(end, (SC.to_steps(r, "start", 75, 0) or 0) + (SC.to_steps(r, "duration", 75, 0) or 0))
    return end / BLOCKS_PER_DAY


def horizon_days(scen: SC.Scenario, budget: Budget, *, min_tail: float = TAIL_DAYS) -> float:
    """Simulated days: the budget's block horizon, extended to cover the scenario's events plus
    ``min_tail`` days, never beyond the scenario's own horizon."""
    need = _event_end_days(scen) + min_tail
    return float(min(scen.horizon_days, max(float(budget.block_horizon_days), need)))


@dataclass
class ScenarioRealisation:
    """One scenario realised through the oracle (independent of every G1/G2 parameter)."""

    key: tuple
    name: str
    true: np.ndarray  #: (P, n) int64 µUSD
    tag_price: np.ndarray  #: (P, n) int64, 0 = no quote
    valid: np.ndarray  #: (P, n) bool, quote tag present
    tag_pool: np.ndarray  #: (P, n) int16
    marks: dict[str, int] = field(default_factory=dict)  #: event block indices
    info: dict[str, Any] = field(default_factory=dict)

    @property
    def n(self) -> int:
        return int(self.true.shape[1])


_REAL: OrderedDict[tuple, ScenarioRealisation] = OrderedDict()
_RM: OrderedDict[tuple, V.RollingMedian] = OrderedDict()
_MED: OrderedDict[tuple, np.ndarray] = OrderedDict()
_CACHE_REAL, _CACHE_RM = 16, 12
#: Byte budget of the memoised medians per process.
MEDIAN_CACHE_BYTES = 768 * 2**20


def _lru_put(d: OrderedDict, key, value, cap: int):
    d[key] = value
    d.move_to_end(key)
    while len(d) > cap:
        d.popitem(last=False)
    return value


def _lru_put_bytes(d: OrderedDict, key, value: np.ndarray, cap_bytes: int) -> np.ndarray:
    d[key] = value
    d.move_to_end(key)
    while len(d) > 1 and sum(a.nbytes for a in d.values()) > cap_bytes:
        d.popitem(last=False)
    return value


def clear_caches() -> None:
    """Drop the per-process memo of realisations and medians (tests use it)."""
    _REAL.clear()
    _RM.clear()
    _MED.clear()


def _first_nonzero_run(a: np.ndarray, pred) -> tuple[int, int] | None:
    m = pred(np.asarray(a).reshape(-1, np.shape(a)[-1])[0])
    if not m.any():
        return None
    s = int(np.argmax(m))
    rest = ~m[s:]
    e = s + (int(np.argmax(rest)) if rest.any() else int(rest.size))
    return s, e


def _marks(scen: SC.Scenario, n: int, schedules: Mapping[str, np.ndarray]) -> dict[str, int]:
    """Event indices: first fall (``fall``/``fall_end``), first rise (``rise``/``rise_end``), wick,
    outage window, attack window."""
    out: dict[str, int] = {}
    for seg in scen.segments:
        pct = float(seg.spec.get("pct", 0.0))
        if seg.kind in ("ramp", "shock"):
            a, b = seg.window(n - 1, 75) if seg.kind == "ramp" else (seg.at(75), seg.at(75) + 1)
            tag = "fall" if pct < 0 else "rise"
            if tag not in out and a < n - 1:
                out[tag], out[f"{tag}_end"] = a, min(b, n - 1)
        elif seg.kind == "wick" and "wick" not in out:
            out["wick"] = seg.at(75)
    if "tagging_share" in schedules:
        run = _first_nonzero_run(schedules["tagging_share"], lambda x: x <= 0)
        if run:
            out["outage"], out["outage_end"] = run
    if "attacker_share" in schedules:
        run = _first_nonzero_run(schedules["attacker_share"], lambda x: x > 0)
        if run:
            out["attack"], out["attack_end"] = run
    return out


# ---------------------------------------------------------------------------------------------------
# The real pool landscape (D-RD-ORA-1)

#: Payout keys kept as named pools: the largest ``LANDSCAPE_TOP`` with at least ``LANDSCAPE_MIN_SHARE``
#: of the log's blocks (as ``PoolModel.fit``); the rest mine as stock, untagged miners.
LANDSCAPE_TOP, LANDSCAPE_MIN_SHARE = 8, 0.01


@dataclass(frozen=True)
class PoolLandscape:
    """The real pool landscape of a pool-share log: named keys, their block shares, which of them tag
    quotes (policy ``enforcing_pools``, else the largest until ``expected_enforcing_share``), and the
    real block-by-block miner sequence (index into ``keys``; −1 = a key below the cut)."""

    keys: tuple[str, ...]
    shares: tuple[float, ...]
    tagging: tuple[bool, ...]
    sequence: np.ndarray = field(compare=False, repr=False)
    source: str = ""
    selection: str = ""

    @property
    def tagging_share(self) -> float:
        return float(sum(sh for sh, t in zip(self.shares, self.tagging, strict=True) if t))

    @property
    def top_tagging(self) -> int | None:
        """Index of the largest tagging key (``None`` if none tags)."""
        idx = [i for i, t in enumerate(self.tagging) if t]
        return max(idx, key=lambda i: self.shares[i]) if idx else None

    def tag_mask(self, tagging: tuple[bool, ...] | None = None) -> np.ndarray:
        """Per block of the real sequence: mined by a tagging key."""
        flags = np.asarray(self.tagging if tagging is None else tagging, dtype=bool)
        seq = self.sequence
        return np.where(seq >= 0, flags[np.maximum(seq, 0)], False)

    def fingerprint(self) -> tuple:
        return (tuple(round(x, 9) for x in self.shares), self.tagging, len(self.sequence))


def _tagging_flags(keys: tuple[str, ...], shares: tuple[float, ...], policy) -> tuple[tuple[bool, ...], str]:
    pref = tuple(getattr(policy, "enforcing_pools", ()) or ())
    if pref:
        flags = tuple(any(k.startswith(p) for p in pref) for k in keys)
        if any(flags):
            return flags, "policy enforcing_pools"
    target = float(policy.expected_enforcing_share)
    out, acc = [], 0.0
    for sh in shares:
        on = acc < target - 1e-12
        out.append(on)
        acc += sh if on else 0.0
    why = "largest keys until expected_enforcing_share"
    return tuple(out), (why + " (enforcing_pools matched no key)" if pref else why)


def pool_landscape(env: Env) -> PoolLandscape | None:
    """The real pool landscape when a pool-share log is loaded (``pool_shares`` / ``hashrate``)."""
    log = None
    for k in ("pool_shares", "hashrate"):
        cand = env.data.get(k) if isinstance(env.data, Mapping) else None
        if cand is not None and hasattr(cand, "shares") and hasattr(cand, "pool"):
            log = cand
            break
    if log is None or not len(log):
        return None
    sh = log.shares()
    keep = [(k, v) for k, v in sh.items() if v >= LANDSCAPE_MIN_SHARE][:LANDSCAPE_TOP]
    if not keep:
        return None
    keys = tuple(k for k, _ in keep)
    shares = tuple(float(v) for _, v in keep)
    flags, why = _tagging_flags(keys, shares, env.policy)
    pos = {k: i for i, k in enumerate(keys)}
    remap = np.array([pos.get(k, -1) for k in log.keys], dtype=np.int16)
    seq = remap[np.asarray(log.pool, dtype=np.int64)]
    return PoolLandscape(keys, shares, flags, seq, str(getattr(log, "source", "")), why)


def no_price_from_mask(tag_mask: np.ndarray, windows, fills) -> tuple[float, tuple[float, ...]]:
    """NO_PRICE hours per year when exactly the blocks in ``tag_mask`` carry a quote (no feed
    outages): PRICE-1's fill only counts quotes, so this is exact for one real miner sequence.
    Returns (total, per-window share of time undefined × hours per year)."""
    m = np.asarray(tag_mask, dtype=np.int64)
    n = m.size
    wmax = max(int(w) for w in windows)
    if n <= wmax:
        return math.nan, tuple(math.nan for _ in windows)
    cs = np.concatenate(([0], np.cumsum(m)))
    L = n - wmax + 1
    bad = np.zeros(L, dtype=bool)
    parts = []
    for w, f in zip(windows, fills, strict=True):
        w = int(w)
        cnt = (cs[w:] - cs[:-w])[-L:]
        b = cnt < max(int(f), 1)
        parts.append(float(b.mean()) * _PER_YEAR_H)
        bad |= b
    return float(bad.mean()) * _PER_YEAR_H, tuple(parts)


def real_miners(env: Env, land: PoolLandscape | None, P: int, n: int, *keys: object) -> np.ndarray | None:
    """``(P, n)`` miners replaying the real block sequence from a random offset per path (cyclic over
    the log), or ``None`` without a landscape (miners are then drawn from the shares)."""
    if land is None or not len(land.sequence):
        return None
    L = len(land.sequence)
    offs = env.rng_for("G1G2", "miners", *keys, P, n).integers(0, L, size=P)
    return land.sequence[(offs[:, None] + np.arange(n)[None, :]) % L]


def oracle_config(env: Env) -> O.OracleConfig:
    """Pools for the G1/G2 oracle. With a real pool-share log: its keys (≥ 1 %, largest 8) at their
    real shares, tagging as :func:`pool_landscape` says (policy ``enforcing_pools``, else the largest
    until ``expected_enforcing_share``), and :func:`realise` replays the real miner sequence.
    Without one: ``expected_pool_count`` equal pools sharing ``expected_enforcing_share``. Background
    NO_PRICE is set by who tags against the ⌈2W/3⌉ fill, so the landscape matters more than the
    windows (D-RD-AUD-9, D-RD-ORA-1). Outages follow the policy's per-pool rate and mean length."""
    pol = env.policy
    kw = dict(
        outage_rate_per_day=float(getattr(pol, "pool_outage_rate_per_day", POOL_OUTAGE_RATE_PER_DAY)),
        outage_mean_hours=float(getattr(pol, "pool_outage_mean_hours", POOL_OUTAGE_MEAN_HOURS)),
        outage_mode="signal",
    )
    land = pool_landscape(env)
    if land is not None and land.tagging_share > 0:
        pools = tuple(
            O.Pool(share=sh, name=f"pool{i}", tags=t, quotes=t, **kw)
            for i, (sh, t) in enumerate(zip(land.shares, land.tagging, strict=True))
        )
        return O.OracleConfig(pools, meta={"source": "pool-share log", "selection": land.selection})
    return O.OracleConfig.from_policy(pol, **kw)


def realise(
    env: Env,
    name: str,
    *,
    n_paths: int | None = None,
    horizon: float | None = None,
    attack: tuple[float, float] | None = None,
    label: str = "",
    rogue: tuple[float, str] | None = None,
) -> ScenarioRealisation:
    """Realise scenario ``name`` at block resolution through the oracle (memoised per process).

    ``attack = (share, bias_bps)`` adds a coalition (carved out of the honest pools) active over the
    scenario's ``attacker_share`` window; ``(share, 0)`` is the clean counterpart with the same pools
    and draws (common random numbers, as ``oracle.attack_effect``).

    ``rogue = (bias_bps, mode)`` turns the *largest real tagging pool* rogue over the same window
    (mode ``bias`` or ``withhold``: it keeps mining but stops quoting), on the real miner sequence;
    the realisation without ``rogue`` is its clean counterpart (same miners and draws). Needs a
    pool-share log (D-RD-ORA-4).
    """
    scen = scenario(env, name)
    P = int(n_paths or paths_for(env.budget))
    h = float(horizon if horizon is not None else horizon_days(scen, env.budget))
    pol = env.policy
    cfg0 = oracle_config(env)
    land = pool_landscape(env)
    key = (
        int(env.seed),
        name,
        P,
        round(h, 4),
        data_fingerprint(env),
        float(pol.expected_enforcing_share),
        int(pol.expected_pool_count),
        tuple(round(p.share, 9) for p in cfg0.pools),
        tuple(p.tags for p in cfg0.pools),
        land.fingerprint() if land is not None else None,
        attack,
        label,
        rogue,
    )
    hit = _REAL.get(key)
    if hit is not None:
        _REAL.move_to_end(key)
        return hit
    rng = env.rng_for("G1G2", "true", name, P, h)
    real = real_price(env)
    if scen.base.model == "replay":
        # a window of the real history in order (D-RD-ORA-4); without real data the fallback model
        run = scen.generate(rng, P, data=real, resolution="block", horizon_days=h)
    elif real is not None:
        model = real_model(env, real)
        model.demean = True
        n = scen.n_steps("block", h)
        base = model.simulate(P, n, "block", env.rng_for("G1G2", "bootstrap", name, P, h), p0=scen.p0)
        run = scen.generate(rng, P, base_path=base, resolution="block", horizon_days=h)
    else:
        run = scen.generate(rng, P, resolution="block", horizon_days=h)
    true = run.paths.prices
    n = true.shape[1]
    sched = {k: np.asarray(v, dtype=float) for k, v in run.schedules.items()}
    marks = _marks(scen, n, sched)
    cfg = cfg0
    tagging = cfg.tagging_share
    if attack is not None:
        share, bias = attack
        st, en = marks.get("attack", WARMUP_BLOCKS), marks.get("attack_end", n)
        cfg = cfg.with_coalition(float(share), float(bias), start=st, end=en)
        if bias == 0:
            cfg = cfg.with_attacks(())
    orng = env.rng_for("G1G2", "oracle", name, P, h)
    if rogue is not None:
        ti = land.top_tagging if land is not None else None
        if ti is None:
            raise ValueError("rogue needs a pool-share log with a tagging pool")
        b, mode = rogue
        st, en = marks.get("attack", WARMUP_BLOCKS), marks.get("attack_end", n)
        att = O.Attack(pools=(ti,), bias_bps=float(b), start=st, end=en, mode=mode)  # type: ignore[arg-type]
        cfg = cfg.with_attacks((att,))
    miner = real_miners(env, land, P, n, name, h) if attack is None else None
    inp = O.generate_block_inputs(true, cfg, rng=orng, miner=miner)
    valid = inp.tag_present & (inp.tag_price > 0)
    keep = np.ones((1, n))
    if "tagging_share" in sched:
        keep = keep * np.clip(np.atleast_2d(sched["tagging_share"]) / max(tagging, 1e-12), 0.0, 1.0)
    if "feed_up" in sched:
        keep = keep * (np.atleast_2d(sched["feed_up"]) > 0)
    if (keep < 1).any():
        u = env.rng_for("G1G2", "thin", name, P, h).random(true.shape)
        valid &= u < keep
    tag_price = np.where(valid, inp.tag_price, 0)
    info = {
        "scenario": name,
        "paths": P,
        "horizon_days": h,
        "base": run.paths.meta.get("base_model"),
        "provenance": "real-data" if real is not None else "synthetic",
        "tagging_share": tagging,
        "miners": "real sequence" if miner is not None else "drawn from shares",
        "constants": dict(run.constants),
    }
    r = ScenarioRealisation(key, name, true, tag_price, valid, inp.tag_pool, marks, info)
    return _lru_put(_REAL, key, r, _CACHE_REAL)


def median(r: ScenarioRealisation, window: int, fill: int) -> np.ndarray:
    """PRICE-1 lower median with min-fill over ``r`` (memoised; int32 when exact)."""
    k = (r.key, int(window), int(fill))
    hit = _MED.get(k)
    if hit is not None:
        _MED.move_to_end(k)
        return hit
    rm = _RM.get(r.key)
    if rm is None:
        rm = _lru_put(_RM, r.key, V.RollingMedian(r.tag_price, r.valid), _CACHE_RM)
    m = rm.median(int(window), int(fill))
    if m.size and int(m.max()) < 2**31 - 1:
        m = m.astype(np.int32)
    return _lru_put_bytes(_MED, k, m, MEDIAN_CACHE_BYTES)


@dataclass
class Prices:
    """The PRICE-1 cross-section of one realisation under one window set."""

    p_fast: np.ndarray
    p_mid: np.ndarray
    p_slow: np.ndarray
    x_mint: np.ndarray
    x_claim: np.ndarray
    halt3: np.ndarray
    no_price: np.ndarray


def prices(r: ScenarioRealisation, params: Mapping) -> Prices:
    """pFast/pMid/pSlow, pMint/pClaim (state.cpp:1205-1206), HALT-3 and NO_PRICE for ``params``."""
    (wf, wm, ws), (ff, fm, fs) = O.windows_and_fills(params)
    pf = median(r, wf, ff).astype(np.int64)
    pm = median(r, wm, fm).astype(np.int64)
    ps = median(r, ws, fs).astype(np.int64)
    xm = V.price_mint(pf, pm, ps)
    xc = V.price_claim(pm, ps)
    h3 = V.halt3_divergence(pf, pm, ps, int(params["divergenceBps"]))
    return Prices(pf, pm, ps, xm, xc, h3, xm == V.UNDEF)


# ---------------------------------------------------------------------------------------------------
# G1 analytics


@functools.lru_cache(maxsize=4096)
def min_attack_share(
    window: int,
    fill: int,
    tagging_share: float,
    direction: str = "up",
    confidence: float = 0.5,
    tol: float = 1e-4,
) -> float:
    """Smallest coalition share whose quotes are the median of a ``window``-block window (with
    ``fill``) with probability ≥ ``confidence``, when the coalition is carved out of a tagging share
    ``tagging_share`` (honest quoting share = ``tagging_share − s``, the rest stock miners). Exact
    binomial (``oracle.attack_success_prob``, V16). ``nan`` if even the whole tagging share fails."""
    T = float(tagging_share)

    def f(s: float) -> float:
        return O.attack_success_prob(
            s, int(window), int(fill), honest_share=max(T - s, 0.0), direction=direction
        )  # type: ignore[arg-type]

    hi = T - 1e-9
    if f(hi) < confidence:
        return float("nan")
    lo = 0.0
    while hi - lo > tol:
        mid = (lo + hi) / 2
        if f(mid) >= confidence:
            hi = mid
        else:
            lo = mid
    return float(hi)


def _target_crossing(series: np.ndarray, target: np.ndarray, start: int, falling: bool) -> np.ndarray:
    """First index ≥ ``start`` where ``series`` (defined) is beyond ``target`` per path; nan if never."""
    seg = series[:, start:]
    okv = seg > 0
    hit = okv & ((seg <= target[:, None]) if falling else (seg >= target[:, None]))
    any_ = hit.any(axis=1)
    idx = np.where(any_, hit.argmax(axis=1) + start, -1).astype(float)
    idx[~any_] = np.nan
    return idx


def tracking_lags(true: np.ndarray, p: np.ndarray, start: int, end: int, fracs=(0.5, 0.9)) -> dict:
    """Per path, blocks by which ``p`` lags the true price in covering ``frac`` of a move.

    The move runs from the median true price over the hour before ``start`` to the median true
    price over the day after ``end``. Lag = (first block ``p`` covers the fraction) − (first block
    the true price does), floored at 0. A path where ``p`` never gets there is censored at the
    horizon (lag = horizon − true crossing) and counted in ``censored``."""
    t = true.astype(np.float64)
    n = t.shape[1]
    before = np.median(t[:, max(0, start - BLOCKS_PER_HOUR) : max(start, 1)], axis=1)
    after = np.median(t[:, min(end, n - 1) : min(n, end + BLOCKS_PER_DAY)], axis=1)
    falling = bool(np.median(after) < np.median(before))
    out: dict = {}
    for fr in fracs:
        target = before + fr * (after - before)
        tt = _target_crossing(t, target, start, falling)
        tp = _target_crossing(p.astype(np.float64), target, start, falling)
        cens = np.isnan(tp) & ~np.isnan(tt)
        lag = np.where(np.isnan(tp), (n - 1) - tt, tp - tt)
        lag = np.maximum(lag, 0.0)
        out[fr] = (lag[~np.isnan(tt)], int(cens.sum()))
    return out


# ---------------------------------------------------------------------------------------------------
# The study


_H = float(BLOCKS_PER_HOUR)
_PER_YEAR_H = BLOCKS_PER_YEAR / _H


def _axis_values(name: str, base: int, points: int) -> list[int]:
    """``points`` values on the registry lattice of ``name`` (bounds, step), always with ``base``."""
    spec = REGISTRY[name]
    lo, hi = spec.bounds
    step = spec.step
    lattice = list(range(lo, hi + 1, step))
    if points >= len(lattice):
        vals = lattice
    else:
        idx = np.unique(np.round(np.linspace(0, len(lattice) - 1, max(points, 2))).astype(int))
        vals = [lattice[i] for i in idx]
    return sorted(set(vals) | {int(base)})


#: Points per window axis outside ``quick`` (a full 3-D grid; see docs/studies/g1.md for runtimes).
AXIS_POINTS = {"standard": 7, "deep": 9}

#: Hand-picked quick lattice (all multiples of 48; includes the shipped 96 / 576 / 2,016).
QUICK_VALUES = {
    "pFastWindow": (48, 96, 144, 192),
    "pMidWindow": (288, 576, 864, 1152),
    "pSlowWindow": (1152, 2016, 3024, 4032),
}


def landscape_metrics(land: PoolLandscape | None, cand) -> dict[str, float]:
    """What the real pool landscape does to one window set (D-RD-ORA-1), from the real miner sequence:

    * ``top_pool_share`` / ``top_pool_quote_share`` — the largest tagging key's share of blocks and
      of quotes;
    * ``top_pool_control_{fast,mid,slow}``, ``top_pool_control_min`` — P(that key's quotes are the
      lower median) per window, worse direction (V16 exact binomial at its real share); ≥ 0.5 on
      every window means it sets every median alone, whatever the windows (``attack_env_blocked``);
    * ``no_price_seq_h_per_year`` — NO_PRICE from the real sequence with the policy's tagging set,
      no feed outages; ``no_price_all_tag_h_per_year`` the same if every named key tagged;
      ``no_price_top_withholds_h_per_year`` if the largest tagging key mines but stops tagging;
      ``no_price_top{2,3,4}_h_per_year`` if exactly the 2, 3, 4 largest keys tag (adoption ladder).
    Empty without a pool-share log."""
    if land is None:
        return {}
    (wf, wm, ws), (ff, fm, fs) = O.windows_and_fills(cand)
    out: dict[str, float] = {}
    ti = land.top_tagging
    T = land.tagging_share
    if ti is not None:
        top = float(land.shares[ti])
        out["top_pool_share"] = top
        out["top_pool_quote_share"] = top / T if T > 0 else math.nan
        ctl = []
        for w, W, F in (("fast", wf, ff), ("mid", wm, fm), ("slow", ws, fs)):
            c = min(
                O.attack_success_prob(top, W, F, honest_share=max(T - top, 0.0), direction="up"),
                O.attack_success_prob(top, W, F, honest_share=max(T - top, 0.0), direction="down"),
            )
            out[f"top_pool_control_{w}"] = float(c)
            ctl.append(c)
        out["top_pool_control_min"] = float(min(ctl))
        out["attack_env_blocked"] = float(min(ctl) >= 0.5)
    win, fil = (wf, wm, ws), (ff, fm, fs)
    out["no_price_seq_h_per_year"] = no_price_from_mask(land.tag_mask(), win, fil)[0]
    out["no_price_all_tag_h_per_year"] = no_price_from_mask(
        land.tag_mask(tuple(True for _ in land.keys)), win, fil
    )[0]
    for k in (2, 3, 4):  # adoption ladder: the k largest keys tag
        if k <= len(land.keys):
            flags = tuple(i < k for i in range(len(land.keys)))
            out[f"no_price_top{k}_h_per_year"] = no_price_from_mask(land.tag_mask(flags), win, fil)[0]
    if ti is not None:
        wh = tuple(t and i != ti for i, t in enumerate(land.tagging))
        out["no_price_top_withholds_h_per_year"] = no_price_from_mask(land.tag_mask(wh), win, fil)[0]
    return out


#: The rogue-pool scenario (the largest real pool turns rogue; D-RD-ORA-4) and its quote biases.
ROGUE_SCENARIO = "rogue-pool-52-up"
ROGUE_BIASES_BPS = (1000, -1000, -3000)


def _capture_hours(att: np.ndarray, cln: np.ndarray, st: int, en: int, sign: int, thr: float) -> float:
    """Median over paths of hours from ``st`` until ``att`` sits ``thr`` beyond ``cln`` (sign +1 up,
    −1 down) for the first time; never within ``[st, en)`` → the window length."""
    a, c = att[:, st:en].astype(float), cln[:, st:en].astype(float)
    ok = (a > 0) & (c > 0)
    hit = ok & ((a - c) / np.where(ok, c, 1.0) * sign >= thr)
    first = np.where(hit.any(axis=1), hit.argmax(axis=1), en - st).astype(float)
    return float(np.median(first)) / _H


def rogue_metrics(env: Env, cand) -> dict[str, float]:
    """What the largest real pool can do by turning rogue (D-RD-ORA-4), on the real miner sequence
    and its clean counterpart (common random numbers). Per quote bias b (+10 %, −10 %, −30 %):

    * ``rogue_{up,down,grief}_capture_h_{fast,mid,slow}`` — hours until each median sits b/2 off
      the clean one (how long the windows delay a majority pool);
    * ``rogue_up_mint_share`` / ``rogue_down_claim_share`` — share of attack blocks with pMint ≥ 5 %
      above / pClaim ≥ 5 % below the clean run (before the attestation layer arms, the harm itself;
      once ARMED, PRICE-2 bounds it: pMint = min(x, a), pClaim = max(x, a));
    * ``rogue_grief_halt3_share`` / ``rogue_up_halt3_share`` — share of attack blocks with HALT-3;
    * ``rogue_withhold_noprice_share``, ``rogue_withhold_onset_h``, ``rogue_withhold_recovery_h`` —
      the pool mines but stops quoting: NO_PRICE share of the outage, hours to NO_PRICE, hours after
      it resumes until a price is defined again.
    Empty without a pool-share log."""
    land = pool_landscape(env)
    if land is None or land.top_tagging is None:
        return {}
    name = ROGUE_SCENARIO
    clean = realise(env, name)
    pc = prices(clean, cand)
    st, en = clean.marks.get("attack", WARMUP_BLOCKS), clean.marks.get("attack_end", clean.n)
    out: dict[str, float] = {}
    for b, tag in zip(ROGUE_BIASES_BPS, ("up", "down", "grief"), strict=True):
        r = realise(env, name, rogue=(float(b), "bias"))
        pr = prices(r, cand)
        sign = 1 if b > 0 else -1
        thr = abs(b) / 2 / BPS
        trio = (("fast", pr.p_fast, pc.p_fast), ("mid", pr.p_mid, pc.p_mid), ("slow", pr.p_slow, pc.p_slow))
        for w, a, c in trio:
            out[f"rogue_{tag}_capture_h_{w}"] = _capture_hours(a, c, st, en, sign, thr)
        sl = slice(st, en)
        xa, xc = pr.x_mint[:, sl].astype(float), pc.x_mint[:, sl].astype(float)
        ok = (xa > 0) & (xc > 0)
        if tag == "up":
            up = (xa - xc) / np.where(ok, xc, 1) >= 0.05
            out["rogue_up_mint_share"] = float(up[ok].mean()) if ok.any() else 0.0
        ca, cc = pr.x_claim[:, sl].astype(float), pc.x_claim[:, sl].astype(float)
        okc = (ca > 0) & (cc > 0)
        if tag == "down":
            out["rogue_down_claim_share"] = (
                float(((cc - ca) / np.where(okc, cc, 1) >= 0.05)[okc].mean()) if okc.any() else 0.0
            )
        out[f"rogue_{tag}_halt3_share"] = float(pr.halt3[:, sl].mean())
    w = realise(env, name, rogue=(0.0, "withhold"))
    pw = prices(w, cand)
    npw = pw.no_price[:, st:en]
    out["rogue_withhold_noprice_share"] = float(npw.mean())
    on = np.where(npw.any(axis=1), npw.argmax(axis=1), en - st).astype(float)
    out["rogue_withhold_onset_h"] = float(np.median(on)) / _H
    after = pw.no_price[:, en:]
    rec = np.where((~after).any(axis=1), (~after).argmax(axis=1), after.shape[1]).astype(float)
    out["rogue_withhold_recovery_h"] = float(np.median(rec)) / _H
    out["rogue_attack_days"] = (en - st) / BLOCKS_PER_DAY
    return out


@dataclass
class G1Study:
    """PLAN §5.1. ``space`` is a grid over the three windows (min-fills derived); ``evaluate``
    scores one window set on the scenario ensemble; ``decide`` applies the §5.1 rule."""

    group: str = GROUP
    params: tuple[str, ...] = field(default_factory=lambda: params_for_group(GROUP))

    # -- space ------------------------------------------------------------------------------------
    def space(self, base: ParamSet, budget: Budget) -> Iterable[ParamSet]:
        if budget.name == "quick":
            axes = {w: sorted(set(QUICK_VALUES[w]) | {base.as_int(w)}) for w in WINDOWS}
        else:
            pts = min(budget.grid_points, AXIS_POINTS.get(budget.name, budget.grid_points))
            axes = {w: _axis_values(w, base.as_int(w), pts) for w in WINDOWS}
        out = [base]
        for f in axes["pFastWindow"]:
            for m in axes["pMidWindow"]:
                for s in axes["pSlowWindow"]:
                    if not f < m < s:
                        continue
                    if (f, m, s) == tuple(base.as_int(w) for w in WINDOWS):
                        continue
                    out.append(base.replace(pFastWindow=f, pMidWindow=m, pSlowWindow=s))
        return out

    # -- evaluate ---------------------------------------------------------------------------------
    def evaluate(self, cand: ParamSet, env: Env) -> Metrics:
        pol = env.policy
        v: dict[str, float] = {}
        series_meta: dict[str, Any] = {}
        # the tagging share the oracle actually simulates (real landscape or the policy's), so the
        # analytic and simulated attack metrics read the same quote population (D-RD-ORA-1)
        tag = float(oracle_config(env).tagging_share)
        land = pool_landscape(env)

        # manipulation resistance — analytic (V16) per window, both directions
        shares = []
        for w in WINDOWS:
            W, F = cand.as_int(w), cand.as_int(FILLS[w])
            s = min(min_attack_share(W, F, tag, "up"), min_attack_share(W, F, tag, "down"))
            v[f"attack_share_{w[1:-6].lower()}"] = s
            shares.append(s)
        v["attack_share_min"] = float(np.nanmin(shares)) if not all(math.isnan(x) for x in shares) else 0.0
        v["tagging_share"] = tag
        v.update(landscape_metrics(land, cand))
        v.update(rogue_metrics(env, cand))

        # manipulation resistance — simulated at the policy's attack_share_min, ±bias
        a_name = ROLES["attack"][0]
        a_scen = scenario(env, a_name)
        bias = float(a_scen.constants.get("attacker_bias_bps", DEFAULT_ATTACK_BIAS_BPS))
        share = float(pol.attack_share_min)
        clean = realise(env, a_name, attack=(share, 0.0))
        up = realise(env, a_name, attack=(share, bias))
        down = realise(env, a_name, attack=(share, -bias))
        pc, pu, pd = prices(clean, cand), prices(up, cand), prices(down, cand)
        st, en = clean.marks.get("attack", WARMUP_BLOCKS), clean.marks.get("attack_end", clean.n)
        st = max(st, WARMUP_BLOCKS) if clean.n > WARMUP_BLOCKS + 1 else st
        sl = slice(st, en)
        thr = abs(bias) / 2 / BPS

        def moved(att: np.ndarray, cln: np.ndarray, sign: int) -> float:
            a, c = att[:, sl].astype(float), cln[:, sl].astype(float)
            ok = (a > 0) & (c > 0)
            if not ok.any():
                return 0.0
            rel = np.where(ok, (a - c) / np.where(ok, c, 1.0), 0.0) * sign
            return float((rel >= thr)[ok].mean())

        v["attack_moved_mint_up"] = moved(pu.x_mint, pc.x_mint, +1)
        v["attack_moved_claim_down"] = moved(pd.x_claim, pc.x_claim, -1)
        v["attack_moved_fast_down"] = moved(pd.p_fast, pc.p_fast, -1)
        v["attack_moved_fast_up"] = moved(pu.p_fast, pc.p_fast, +1)
        v["attack_extra_halt3_h"] = float((pd.halt3[:, sl] & ~pc.halt3[:, sl]).sum(axis=1).mean() / _H)
        harmful = max(v["attack_moved_mint_up"], v["attack_moved_claim_down"])

        # crash tracking lag, exposure, HALT-3 recall
        lag_c90: list[np.ndarray] = []
        cens = 0
        P = paths_for(env.budget)
        for name in ROLES["crash"]:
            r = realise(env, name, n_paths=CRASH_PATH_FACTOR * P)
            if "fall" not in r.marks:
                continue
            p = prices(r, cand)
            a, b = r.marks["fall"], r.marks["fall_end"]
            key = name.split("-")[0] + name.split("-")[1]  # crash70 / crash90
            lm = tracking_lags(r.true, p.x_mint, a, b)
            lc = tracking_lags(r.true, p.x_claim, a, b)
            lag_c90.append(lc[0.9][0])
            cens += lc[0.9][1]
            v[f"lag_mint50_{key}_h"] = float(np.mean(lm[0.5][0])) / _H if lm[0.5][0].size else math.nan
            v[f"lag_mint90_{key}_h"] = float(np.mean(lm[0.9][0])) / _H if lm[0.9][0].size else math.nan
            v[f"lag_claim50_{key}_h"] = float(np.mean(lc[0.5][0])) / _H if lc[0.5][0].size else math.nan
            v[f"lag_claim90_{key}_h"] = float(np.mean(lc[0.9][0])) / _H if lc[0.9][0].size else math.nan
            e = min(r.n, b + EXPOSURE_TAIL_BLOCKS)
            over = (p.x_claim[:, a:e] > r.true[:, a:e]) & (p.x_claim[:, a:e] > 0)
            v[f"exposure_{key}_h"] = float(over.sum(axis=1).mean()) / _H
            fired = p.halt3[:, a : a + RECALL_WITHIN_BLOCKS]
            # conditional on paths where HALT-3 can be tested (medians defined for most of the day):
            # under the real landscape HALT-1 (NO_PRICE) often already stops minting (D-RD-ORA-6)
            testable = p.no_price[:, a : a + RECALL_WITHIN_BLOCKS].mean(axis=1) < 0.5
            hit = fired.any(axis=1)
            v[f"halt3_recall_{key}"] = float(hit[testable].mean()) if testable.any() else 1.0
            v[f"halt3_untestable_{key}"] = float((~testable).mean())
            first = np.where(fired.any(axis=1), fired.argmax(axis=1), np.nan)
            v[f"halt3_delay_{key}_h"] = (
                float(np.nanmean(first)) / _H if np.isfinite(first).any() else math.nan
            )
            if name == RECALL_SCENARIO:
                lo, hi = max(0, a - BLOCKS_PER_DAY), min(r.n, b + 5 * BLOCKS_PER_DAY)
                step = BLOCKS_PER_HOUR
                series_meta["crash"] = {
                    "scenario": name,
                    "start": a - lo,
                    "step_blocks": step,
                    "true": _ds(r.true[:, lo:hi], step),
                    "x_mint": _ds(p.x_mint[:, lo:hi], step),
                    "x_claim": _ds(p.x_claim[:, lo:hi], step),
                }
        allc90 = np.concatenate(lag_c90) if lag_c90 else np.array([math.nan])
        v["crash_lag_cvar_h"] = cvar(allc90, float(pol.crash_lag_cvar_alpha)) / _H
        v["crash_lag_censored"] = float(cens)

        # pump-and-dump: mint-at-the-top exposure
        pr = realise(env, ROLES["pump"][0])
        pp = prices(pr, cand)
        a = pr.marks.get("fall", pr.marks.get("rise", WARMUP_BLOCKS))
        b = min(pr.n, pr.marks.get("fall_end", pr.n) + BLOCKS_PER_DAY)
        xm, tt = pp.x_mint[:, a:b].astype(float), pr.true[:, a:b].astype(float)
        over = np.where(xm > 0, np.maximum(xm - tt, 0.0) / tt * BPS, 0.0)
        v["pump_overpricing_bps"] = float(over.mean())
        v["pump_overpricing_p95_bps"] = float(np.percentile(over, 95))
        ra, rb = pr.marks.get("rise", 0), pr.marks.get("rise_end", 0)
        if rb > ra:
            xr, tr = pp.x_mint[:, ra:rb].astype(float), pr.true[:, ra:rb].astype(float)
            v["pump_rise_overpricing_bps"] = float(
                np.where(xr > 0, np.maximum(xr - tr, 0) / tr * BPS, 0).mean()
            )
        v["halt3_pump_dump_h"] = float(pp.halt3[:, WARMUP_BLOCKS:].sum(axis=1).mean()) / _H

        # availability and false HALT-3 in calm (annualised), per feed outage, per wick
        cs = scenario(env, ROLES["calm"][0])
        cr = realise(
            env,
            cs.name,
            n_paths=CALM_PATH_FACTOR * P,
            horizon=min(cs.horizon_days, max(CALM_MIN_DAYS, float(env.budget.block_horizon_days))),
        )
        cp = prices(cr, cand)
        body = slice(WARMUP_BLOCKS, cr.n)
        v["no_price_h_per_year"] = O.no_price_hours_per_year(cp.no_price[:, body])
        v["halt3_false_h_per_year"] = float(cp.halt3[:, body].mean()) * _PER_YEAR_H
        orr = realise(env, ROLES["outage"][0])
        op = prices(orr, cand)
        os_ = orr.marks.get("outage", WARMUP_BLOCKS)
        v["no_price_h_per_feed_outage"] = float(op.no_price[:, os_:].sum(axis=1).mean()) / _H
        wr = realise(env, ROLES["wick"][0])
        wp = prices(wr, cand)
        wa = wr.marks.get("wick", WARMUP_BLOCKS)
        pre = np.median(wp.x_mint[:, max(0, wa - BLOCKS_PER_HOUR) : wa].astype(float), axis=1)
        post = wp.x_mint[:, wa : wa + 6 * BLOCKS_PER_HOUR].astype(float)
        post = np.where(post > 0, post, pre[:, None])
        v["wick_mint_drop_bps"] = float(np.mean((pre - post.min(axis=1)) / pre * BPS))
        v["halt3_wick_h"] = float(wp.halt3[:, wa : wa + BLOCKS_PER_DAY].sum(axis=1).mean()) / _H

        constraints = {
            "attack_share_min": bool(v["attack_share_min"] >= float(pol.attack_share_min))
            and harmful <= float(getattr(pol, "attack_moved_tol", ATTACK_MOVED_TOL)),
            "max_no_price_hours": bool(v["no_price_h_per_year"] <= float(pol.max_no_price_hours)),
            "halt_recall_floor": bool(v.get("halt3_recall_crash70", 1.0) >= float(pol.halt_recall_floor)),
        }
        meta = {
            "budget": env.budget.name,
            "out_dir": out_dir_of(env),
            "paths": paths_for(env.budget),
            "series": series_meta,
            "attack_bias_bps": bias,
            "data": data_fingerprint(env),
        }
        return Metrics(
            v,
            primary="crash_lag_cvar_h",
            minimize=True,
            constraints=constraints,
            provenance=provenance_of(env),
            meta=meta,
        )  # type: ignore[arg-type]

    # -- decide -----------------------------------------------------------------------------------
    def decide(self, results: ResultTable, policy: Policy) -> list[Recommendation]:
        jt0 = objective_table(results, policy)
        jt, env_notes, env_cons = environment_adjust(jt0, policy)
        dec = decide_with_materiality(jt, policy, params=WINDOWS, metric="objective")
        cur = jt.current(WINDOWS)
        assert cur is not None
        chosen = dec.row
        prov = cur.metrics.provenance
        verdict = final_verdict(dec.verdict, prov)
        binding = binding_constraint(jt, chosen, dec)
        env = None
        if env_notes:
            # the policy is unmeetable in the real environment: least harm — environment-limited
            # (D-RD-ORA-2, envlimit / D-RD-INF-3); the value is the least-harm set of the relaxed rule
            binding = "environment-limited: " + "; ".join(env_notes) + f" — least-harm rule: {dec.reason}"
            env = environment_info(env_cons, env_notes, cur, chosen, dec.reason)
        meta = cur.metrics.meta
        conf = _confidence(prov, str(meta.get("budget", "quick")))
        out = evidence_dir(meta.get("out_dir"), GROUP)
        evidence = write_evidence(jt, cur, chosen, out)
        keys = (
            "objective",
            "crash_lag_cvar_h",
            "pump_overpricing_bps",
            "attack_share_min",
            "attack_moved_mint_up",
            "attack_moved_claim_down",
            "no_price_h_per_year",
            "no_price_h_per_feed_outage",
            "halt3_recall_crash70",
            "halt3_false_h_per_year",
            "exposure_crash70_h",
            "exposure_crash90_h",
            "lag_claim90_crash70_h",
            "lag_mint90_crash70_h",
            "halt3_wick_h",
            "wick_mint_drop_bps",
            "tagging_share",
            "top_pool_quote_share",
            "top_pool_control_min",
            "no_price_seq_h_per_year",
            "no_price_all_tag_h_per_year",
            "no_price_top_withholds_h_per_year",
        )
        mcur = {k: cur.metrics.values.get(k) for k in keys}
        mrec = {k: chosen.metrics.values.get(k) for k in keys}
        recs: list[Recommendation] = []
        for w in WINDOWS:
            spec = REGISTRY[w]
            sens: dict[str, Any] = {}
            try:
                o = oat_from_table(jt, w, metric="objective")
                sens = {
                    "oat_values": o.values.tolist(),
                    "oat_objective": o.metric.tolist(),
                    "classes": list(o.classes),
                    "sentence": sensitivity_sentence(w, o, "the G1 objective"),
                }
            except Exception as e:  # pragma: no cover - degenerate tables
                sens = {"sentence": f"sensitivity unavailable ({e})"}
            recs.append(
                Recommendation(
                    param=w,
                    current=cur.params[w],
                    recommended=chosen.params[w],
                    verdict=verdict,
                    rule=RULE_TEXT.format(
                        lam=policy.pump_overpricing_lambda,
                        share=policy.attack_share_min,
                        nph=policy.max_no_price_hours,
                        recall=policy.halt_recall_floor,
                        mat=policy.materiality,
                        alpha=policy.crash_lag_cvar_alpha,
                    ),
                    binding=binding,
                    metrics={
                        "current": mcur,
                        "recommended": mrec,
                        "decision": dec.reason,
                        "improvement": dec.improvement,
                    },
                    sensitivity=sens,
                    confidence=conf,
                    provenance=prov,
                    evidence=list(evidence),
                    group=GROUP,
                    notes=[
                        f"rules: {', '.join(spec.rules)}",
                        f"materiality: {dec.reason}",
                        f"underlying verdict before the provenance rule: {dec.verdict}",
                        *[f"environment-limited (D-RD-ORA-2): {x}" for x in env_notes],
                    ],
                )
            )
        for w, f in FILLS.items():
            recs.append(
                Recommendation(
                    param=f,
                    current=cur.params[f],
                    recommended=chosen.params[f],
                    verdict=verdict,
                    rule=(
                        "derived (L9): ⌈W/2⌉ of pFastWindow"
                        if f == "pFastMinFill"
                        else f"derived (L9): ⌈2W/3⌉ of {w}"
                    ),
                    binding=f"follows {w}",
                    metrics={"current": mcur, "recommended": mrec},
                    sensitivity={"sentence": f"follows {w}"},
                    confidence=conf,
                    provenance=prov,
                    evidence=list(evidence),
                    group=GROUP,
                    notes=[f"Derived from {w}; recommend the parent, the child follows."],
                )
            )
        if env is not None:
            from ybcal.studies.envlimit import attach_environment

            for r in recs:
                attach_environment(r, env)
        return recs

    # -- explain ----------------------------------------------------------------------------------
    def explain(self, rec: Recommendation, results: ResultTable) -> str:
        return explain_g1(rec)


def make_study() -> G1Study:
    """The G1 study (PLAN §5.1)."""
    return G1Study()


# ---------------------------------------------------------------------------------------------------
# Decision helpers


RULE_TEXT = (
    "Among window sets whose every median needs a colluding hash share ≥ attack_share_min "
    "({share:.0%}) to control it (V16, exact binomial) and in which a {share:.0%} coalition moves "
    "pMint up or pClaim down by half its bias in ≤ 5 % of attack blocks (simulated), with background "
    "NO_PRICE ≤ max_no_price_hours ({nph:g} h/yr) and HALT-3 still firing within a day on ≥ "
    "halt_recall_floor ({recall:.0%}) of 70 % one-day crashes: minimise "
    "J = CVaR{alpha:.0%}(pClaim 90 % crash lag)/current + λ·E[pMint − true | pump-dump]/current "
    "(λ = {lam:g}). Keep the current windows unless J improves by more than materiality ({mat:.0%}); "
    "ties go to the current values. Min-fills follow (L9)."
)


def _objective(lag: float, over: float, lag0: float, over0: float, lam: float) -> float:
    def norm(x: float, x0: float) -> float:
        return x / x0 if x0 > 1e-9 else (0.0 if x <= 1e-9 else x / 1e-9)

    return norm(lag, lag0) + lam * norm(over, over0)


#: Least-harm band on background NO_PRICE when no window set meets ``max_no_price_hours`` (D-RD-ORA-2).
NO_PRICE_BAND = 0.05


def environment_adjust(jt: ResultTable, policy: Policy) -> tuple[ResultTable, list[str], list[str]]:
    """Relax the G1 constraints the real environment makes unmeetable for *every* window set, so the
    rule still picks the least-harm windows instead of stopping at BLOCKED (D-RD-ORA-2):

    * ``attack_share_min`` — when one real pool sets every median alone at the current windows
      (``attack_env_blocked``: its quotes are the lower median with P ≥ ½ in every window), no window
      choice changes who controls the price; the constraint is dropped from the window decision and
      the exposure is reported (attestation and HALT-3 bound it, not the windows);
    * ``max_no_price_hours`` — when no candidate meets it, it becomes "within ``NO_PRICE_BAND`` (5 %)
      of the lowest NO_PRICE any candidate reaches": availability is the harm the environment makes
      binding, so it is minimised first (a tight band, not materiality — 20 % of 1,200 h/yr is 240 h);
      J then chooses inside the band as usual (minimal change within materiality of the best).

    Returns the (possibly) adjusted table, one note per relaxation and the relaxed constraint names;
    no notes = unchanged."""
    cur = jt.current(WINDOWS)
    notes: list[str] = []
    names: list[str] = []
    if cur is None:
        return jt, notes, names
    v = cur.metrics.values
    # whenever one pool sets every median alone the coalition test is moot (it passes or fails on a
    # 34 % coalition nobody needs); the note is always raised, the constraint dropped if it binds
    drop_attack = bool(v.get("attack_env_blocked", 0.0) >= 1.0)
    if drop_attack:
        names.append("attack_share_min")
        notes.append(
            f"the largest pool holds {float(v.get('top_pool_quote_share', math.nan)):.0%} of the quotes "
            f"({float(v.get('top_pool_share', math.nan)):.0%} of blocks) and sets every median alone "
            f"(P ≥ {float(v.get('top_pool_control_min', math.nan)):.2f} per block): attack_share_min "
            f"({policy.attack_share_min:.0%}) cannot be met by any window set"
        )
    nph = [float(r.metrics.values.get("no_price_h_per_year", math.nan)) for r in jt]
    finite = [x for x in nph if math.isfinite(x)]
    relax_np = bool(finite) and min(finite) > float(policy.max_no_price_hours)
    bound = (1.0 + NO_PRICE_BAND) * min(finite) if relax_np else math.nan
    if relax_np:
        np_cur = float(v.get("no_price_h_per_year", math.nan))
        names.append("max_no_price_hours")
        notes.append(
            f"no window set meets max_no_price_hours ({policy.max_no_price_hours:g} h/yr): the lowest "
            f"background NO_PRICE is {min(finite):.0f} h/yr (current {np_cur:.0f}); "
            f"the rule keeps the sets within {NO_PRICE_BAND:.0%} of it (≤ {bound:.0f} h/yr), then applies J"
        )
    if not notes:
        return jt, notes, names
    out = ResultTable(jt.base)
    for r in jt:
        m = r.metrics
        c = dict(m.constraints)
        if drop_attack:
            c["attack_share_min"] = True
        if relax_np:
            x = float(m.values.get("no_price_h_per_year", math.nan))
            c["max_no_price_hours"] = bool(math.isfinite(x) and x <= bound)
        out.add(r.params, Metrics(dict(m.values), m.primary, m.minimize, c, m.provenance, dict(m.meta)))
    return out, notes, names


def _exposure(row) -> str:
    v = row.metrics.values

    def g(k: str, fmt: str = "{:.0f}") -> str:
        x = v.get(k)
        return "n/a" if x is None or not math.isfinite(float(x)) else fmt.format(float(x))

    return (
        f"background NO_PRICE {g('no_price_h_per_year')} h/yr with the policy's tagging set "
        f"(real sequence alone {g('no_price_seq_h_per_year')}; 4 largest keys tag "
        f"{g('no_price_top4_h_per_year')}; every key {g('no_price_all_tag_h_per_year')}); the largest pool "
        f"holds {g('top_pool_quote_share', '{:.0%}')} of the quotes and captures pFast / pMid / pSlow in "
        f"{g('rogue_up_capture_h_fast', '{:.1f}')} / {g('rogue_up_capture_h_mid', '{:.1f}')} / "
        f"{g('rogue_up_capture_h_slow', '{:.1f}')} h; if it withholds quotes NO_PRICE follows in "
        f"{g('rogue_withhold_onset_h', '{:.1f}')} h and lasts until {g('rogue_withhold_recovery_h')} h after "
        "it resumes"
    )


def environment_info(cons: list[str], notes: list[str], cur, chosen, reason: str) -> dict:
    """The ``envlimit`` record (D-RD-INF-3) for the G1 environment limits (D-RD-ORA-2)."""
    ids = {"attack_share_min": "G1-ENV-1", "max_no_price_hours": "G1-ENV-2"}
    titles = {
        "attack_share_min": "One pool sets every price median (attack_share_min unmeetable)",
        "max_no_price_hours": "Background NO_PRICE far above max_no_price_hours at every window set",
    }
    fixes = {
        "attack_share_min": "arm the attestation layer early (PRICE-2 revised bounds over-minting and "
        "path-(a) claims); operationally, spread hash; no window can",
        "max_no_price_hours": "more tagging pools (the unidentified 21 % key: 4 largest tagging → tens of "
        "h/yr), or a lower mid/slow min-fill than ⌈2W/3⌉ (L9; v3's PRICE-2 already bounds the minority "
        "attack L9 guarded against once ARMED)",
    }
    h = float(chosen.metrics.values.get("no_price_h_per_year", math.nan))
    h0 = float(cur.metrics.values.get("no_price_h_per_year", math.nan))
    return {
        "constraints": list(cons),
        "note": ids[cons[0]],
        "notes": [ids[c] for c in cons],
        "why": "; ".join(notes),
        "exposure": _exposure(chosen),
        "harm_metric": "no_price_h_per_year",
        "harm_minimize": True,
        "harm_at_choice": h,
        "harm_at_current": h0,
        "harm_best": h,
        "titles": [titles[c] for c in cons],
        "fixes": [fixes[c] for c in cons],
        "decision": f"environment-limited ({', '.join(cons)}): {reason}",
    }


def objective_table(results: ResultTable, policy: Policy) -> ResultTable:
    """A copy of ``results`` whose rows carry ``objective`` = J (normalised by the current row)."""
    cur = results.current(WINDOWS)
    if cur is None:
        raise ValueError("G1: the current windows were not evaluated")
    lag0 = float(cur.metrics.values["crash_lag_cvar_h"])
    over0 = float(cur.metrics.values["pump_overpricing_bps"])
    lam = float(policy.pump_overpricing_lambda)
    jt = ResultTable(results.base)
    for r in results:
        m = r.metrics
        j = _objective(
            float(m.values["crash_lag_cvar_h"]), float(m.values["pump_overpricing_bps"]), lag0, over0, lam
        )
        vals = dict(m.values)
        vals["objective"] = j
        jt.add(r.params, Metrics(vals, "objective", True, dict(m.constraints), m.provenance, dict(m.meta)))
    return jt


def binding_constraint(jt: ResultTable, chosen, dec) -> str:
    """What stopped the rule going further: constraints violated by rows that score better than the
    chosen one, else materiality (for KEEP), else the search bound / the objective's minimum."""
    jc = float(chosen.metrics.values["objective"])
    better = [r for r in jt if float(r.metrics.values["objective"]) < jc - 1e-12]
    if dec.verdict == "BLOCKED":
        return "no candidate satisfies the policy: " + dec.reason
    blockers: dict[str, int] = {}
    for r in better:
        for c in r.metrics.violated:
            blockers[c] = blockers.get(c, 0) + 1
    feas_better = [r for r in better if r.metrics.feasible]
    parts = []
    if blockers:
        parts.append(
            "better-scoring sets violate "
            + ", ".join(
                f"{k} ({n} set{'s' if n != 1 else ''})"
                for k, n in sorted(blockers.items(), key=lambda x: -x[1])
            )
        )
    if feas_better and dec.verdict == "KEEP":
        parts.append(f"materiality: {dec.reason}")
    at_bound = [w for w in WINDOWS if chosen.params.as_int(w) in REGISTRY[w].bounds]
    if not parts:
        parts.append("objective minimum within the searched grid")
    if at_bound and dec.verdict == "CHANGE":
        parts.append("at the search bound of " + ", ".join(at_bound))
    return "; ".join(parts)


def _confidence(prov: str, budget: str) -> str:
    if prov != "real-data":
        return "low"
    return "high" if budget in ("standard", "deep") else "medium"


def _ds(a: np.ndarray, step: int) -> list[float]:
    """Mean over paths, then one point per ``step`` blocks (undefined → NaN → null-safe float)."""
    x = np.where(a > 0, a.astype(float), np.nan)
    m = np.nanmean(x, axis=0) if np.isfinite(x).any() else np.full(a.shape[1], np.nan)
    pts = m[::step]
    return [float(v) if np.isfinite(v) else -1.0 for v in pts]


# ---------------------------------------------------------------------------------------------------
# Evidence (CSV + figures)

#: Reference categorical palette (dataviz skill, light mode): slot 1 current, slot 2 recommended.
C_CUR, C_REC, C_TRUE, C_GRID, C_TEXT, C_SURF = (
    "#2a78d6",
    "#eb6834",
    "#52514e",
    "#e4e3df",
    "#0b0b0b",
    "#fcfcfb",
)


def plot_style():
    """matplotlib with the Agg backend and a recessive, light style; ``None`` if unavailable."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:  # pragma: no cover
        return None
    plt.rcParams.update(
        {
            "figure.facecolor": C_SURF,
            "axes.facecolor": C_SURF,
            "axes.edgecolor": C_GRID,
            "axes.labelcolor": C_TEXT,
            "xtick.color": C_TRUE,
            "ytick.color": C_TRUE,
            "axes.grid": True,
            "grid.color": C_GRID,
            "grid.linewidth": 0.6,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "font.size": 9,
            "legend.frameon": False,
            "lines.linewidth": 2,
        }
    )
    return plt


def _wtuple(r) -> str:
    return "/".join(str(r.params.as_int(w)) for w in WINDOWS)


def write_evidence(jt: ResultTable, cur, chosen, out: Path) -> list[Path]:
    """``g1_results.csv`` plus two figures: crash tracking (current vs recommended) and the OAT
    objective per window."""
    paths = [jt.to_csv(out / "g1_results.csv")]
    plt = plot_style()
    if plt is None:  # pragma: no cover
        return paths
    # 1 — crash tracking
    sc, sr = (
        cur.metrics.meta.get("series", {}).get("crash"),
        chosen.metrics.meta.get("series", {}).get("crash"),
    )
    if sc:
        fig, ax = plt.subplots(figsize=(7.5, 3.6))
        h = np.arange(len(sc["true"])) - sc["start"] / sc["step_blocks"]

        def arr(x):
            a = np.asarray(x, float)
            return np.where(a > 0, a / 1e6, np.nan)

        ax.plot(h, arr(sc["true"]), color=C_TRUE, lw=1.2, label="true price (mean)")
        ax.plot(h, arr(sc["x_claim"]), color=C_CUR, label=f"pClaim, current {_wtuple(cur)}")
        ax.plot(h, arr(sc["x_mint"]), color=C_CUR, ls="--", lw=1.5, label="pMint, current")
        if sr and chosen is not cur:
            ax.plot(h, arr(sr["x_claim"]), color=C_REC, label=f"pClaim, recommended {_wtuple(chosen)}")
            ax.plot(h, arr(sr["x_mint"]), color=C_REC, ls="--", lw=1.5, label="pMint, recommended")
        ax.set_xlabel("hours from crash start")
        ax.set_ylabel("USD per YEC")
        ax.set_title(f"{sc['scenario']}: oracle prices track the crash", loc="left", fontsize=10)
        ax.legend(fontsize=8, loc="upper right")
        fig.tight_layout()
        p = out / "g1_crash_tracking.png"
        fig.savefig(p, dpi=120)
        plt.close(fig)
        paths.append(p)
    # 2 — OAT objective per window (others at current)
    fig, axs = plt.subplots(1, 3, figsize=(9.5, 3.0), sharey=True)
    for ax, w in zip(axs, WINDOWS, strict=True):
        rows = [r for r in jt if set(r.delta) - set(FILLS.values()) <= {w}]
        rows.sort(key=lambda r: r.params.as_int(w))
        xs = [r.params.as_int(w) / BLOCKS_PER_HOUR for r in rows]
        ys = [float(r.metrics.values["objective"]) for r in rows]
        ax.plot(xs, ys, color=C_CUR, marker="o", ms=4)
        for x, y, r in zip(xs, ys, rows, strict=True):
            if not r.metrics.feasible:
                ax.plot([x], [y], marker="x", color=C_TEXT, ms=8, ls="none")
        ax.axvline(cur.params.as_int(w) / BLOCKS_PER_HOUR, color=C_TRUE, lw=1, ls=":")
        ax.set_xlabel(f"{w} (hours)")
        ax.set_title(w, loc="left", fontsize=9)
    axs[0].set_ylabel("objective J (current = 1 + λ)")
    fig.suptitle(
        "G1 objective, one window at a time (x = violates policy; dotted = current)",
        fontsize=10,
        x=0.01,
        ha="left",
    )
    fig.tight_layout()
    p = out / "g1_oat_objective.png"
    fig.savefig(p, dpi=120)
    plt.close(fig)
    paths.append(p)
    return paths


# ---------------------------------------------------------------------------------------------------
# Explanations


WHAT = {
    "pFastWindow": (
        "the fast price median (pFast): the lower median of the last pFastWindow blocks' "
        "quote tags. pMint is the minimum of the three medians, so pFast is what lets the "
        "minting price fall quickly in a crash; HALT-3 compares it with pMid; SIGMA-1 "
        "measures volatility on it; MINT-10 compares attested prices with it"
    ),
    "pMidWindow": (
        "the mid price median (pMid). pClaim is the maximum of pMid and pSlow, and HALT-3 "
        "fires when pFast falls more than divergenceBps below pMid (or pMid below pSlow)"
    ),
    "pSlowWindow": (
        "the slow price median (pSlow), the anchor of pClaim = max(pMid, pSlow) and of "
        "pMint's upper side; the longest window an attacker must capture to move pClaim "
        "down or pMint up"
    ),
}


def _fmt_w(b: int) -> str:
    h = b / BLOCKS_PER_HOUR
    return f"{b:,} blocks ({h:g} h)" if h < 48 else f"{b:,} blocks ({h / 24:g} days)"


def explain_g1(rec: Recommendation) -> str:
    """Plain-English paragraphs for one G1 recommendation."""
    if rec.param in FILLS.values():
        parent = next(w for w, f in FILLS.items() if f == rec.param)
        frac = "half" if rec.param == "pFastMinFill" else "two thirds"
        return (
            f"{rec.param} is derived (L9): a median is defined only when at least {frac} of its "
            f"{parent}-block window carries a quote tag. It is not tuned on its own; it follows "
            f"{parent} ({rec.current} → {rec.recommended}). Verdict {rec.verdict}. {rec.klass_note}"
        )
    c, r = rec.metrics.get("current", {}), rec.metrics.get("recommended", {})

    def g(d: Mapping, k: str, fmt: str = "{:.2f}") -> str:
        x = d.get(k)
        return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else fmt.format(x)

    moved = rec.recommended != rec.current
    head = (
        f"{rec.param} sets {WHAT[rec.param]} (rules: {', '.join(REGISTRY[rec.param].rules)}). "
        f"Current {_fmt_w(int(rec.current))}; recommended {_fmt_w(int(rec.recommended))} — "
        f"verdict {rec.verdict} (provenance {rec.provenance}, confidence {rec.confidence})."
    )
    rule = f"Decision rule: {rec.rule}"
    nums = (
        f"At the current windows the crash-lag CVaR is {g(c, 'crash_lag_cvar_h', '{:.1f}')} h, pump "
        f"overpricing {g(c, 'pump_overpricing_bps', '{:.0f}')} bps, the smallest controlling coalition "
        f"{g(c, 'attack_share_min', '{:.1%}')} of hash, background NO_PRICE "
        f"{g(c, 'no_price_h_per_year', '{:.1f}')} h/yr ({g(c, 'no_price_h_per_feed_outage', '{:.1f}')} h "
        f"per 6-hour feed outage) and HALT-3 recall on a one-day 70 % crash "
        f"{g(c, 'halt3_recall_crash70', '{:.0%}')}."
    )
    if moved:
        nums += (
            f" At the recommended set: crash-lag CVaR {g(r, 'crash_lag_cvar_h', '{:.1f}')} h, "
            f"overpricing {g(r, 'pump_overpricing_bps', '{:.0f}')} bps, coalition "
            f"{g(r, 'attack_share_min', '{:.1%}')}, NO_PRICE {g(r, 'no_price_h_per_year', '{:.1f}')} h/yr "
            f"({g(r, 'no_price_h_per_feed_outage', '{:.1f}')} h per outage), HALT-3 recall "
            f"{g(r, 'halt3_recall_crash70', '{:.0%}')}; objective {g(r, 'objective', '{:.3f}')} vs "
            f"{g(c, 'objective', '{:.3f}')}."
        )
    bind = f"Binding: {rec.binding}."
    sens = rec.sensitivity.get("sentence", "")
    neigh = ""
    pts = rec.sensitivity.get("neighbours")
    if pts:
        bits = []
        for q in pts:
            if q.get("primary") is None:
                bits.append(f"{q['value']} is inadmissible ({'/'.join(q.get('rejected', []))})")
            elif not q.get("feasible"):
                bits.append(f"{q['value']} violates {', '.join(q.get('violated', []))}")
            else:
                imp = q.get("improvement") or 0.0
                bits.append(f"{q['value']} scores {imp:+.1%} on the crash lag")
        neigh = "Neighbours: " + "; ".join(bits) + "."
    else:
        neigh = (
            "Why not the neighbours: every evaluated alternative either violates a constraint or "
            "does not improve the objective by more than materiality (see the OAT figure)."
        )
    tail = rec.klass_note
    if rec.verdict == "PROVISIONAL":
        tail += (
            " PROVISIONAL: only synthetic prices back this; rerun with real YEC data "
            "(`ybcal data fetch`) before locking."
        )
    return "\n\n".join(x for x in (head, rule, nums, bind, sens, neigh, tail) if x)

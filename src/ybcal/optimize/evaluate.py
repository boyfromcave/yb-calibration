"""Candidate evaluation: per-candidate environments, a result cache, optional process parallelism.

Everything that *scores* a :class:`~ybcal.params.paramset.ParamSet` in the optimizer goes through
:func:`evaluate_many`, so search, successive halving, neighbour evidence, sensitivity and WP-8's
joint pass share one cache and one determinism rule.

Determinism (D-WP6-1). Each candidate is scored with a *fresh* copy of the run :class:`Env` whose
``rng`` is reset to ``env.rng_for("evaluate")`` (common random numbers: every candidate sees the same
stream) or, with ``crn=False``, to ``env.rng_for("evaluate", cand.digest())``. Either way the result
depends only on ``(seed, candidate)``, never on evaluation order, worker count or scheduling. Studies
should still draw scenario paths from ``env.rng_for(scenario, ...)``.

Parallelism. ``workers > 1`` uses a :class:`concurrent.futures.ProcessPoolExecutor`. The evaluation
callable (usually a bound ``study.evaluate``) and the ``Env`` are shipped once per worker through the
pool initializer, so **both must be picklable**: define studies and objective functions at module
level (no lambdas or closures) and keep ``Env.data`` to picklable objects (numpy arrays, dataclasses).
If pickling fails the evaluation falls back to serial with a :class:`RuntimeWarning`.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import pickle
import time
import warnings
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ybcal.params.paramset import ParamSet
from ybcal.studies.base import Budget, Env, Metrics, Study

OWNER_WP = "WP-6"

#: ``fn(cand, env) -> Metrics`` — the shape of ``Study.evaluate``.
EvalFn = Callable[[ParamSet, Env], Metrics]

#: Default on-disk cache directory (relative to the working directory).
DEFAULT_CACHE_DIR = Path(".work") / "cache"


# ---------------------------------------------------------------------------------------------------
# Environments


def default_workers() -> int:
    """``os.cpu_count()`` (1 when unknown)."""
    return os.cpu_count() or 1


def with_budget(env: Env, budget: Budget) -> Env:
    """A copy of ``env`` running at ``budget`` (same seed, data, scenarios, policy)."""
    if budget == env.budget:
        return env
    return dataclasses.replace(env, budget=budget)


def with_paths(env: Env, paths: int) -> Env:
    """A copy of ``env`` whose budget has ``paths`` Monte-Carlo paths (a fidelity level)."""
    return with_budget(env, dataclasses.replace(env.budget, paths=int(paths)))


def candidate_env(env: Env, cand: ParamSet, *, crn: bool = True) -> Env:
    """A fresh ``Env`` for scoring ``cand``: same fields, ``rng`` reset deterministically.

    ``crn=True`` → ``env.rng_for("evaluate")`` (common random numbers across candidates);
    ``crn=False`` → ``env.rng_for("evaluate", cand.digest())`` (independent per candidate).
    """
    e = dataclasses.replace(env)
    e.rng = env.rng_for("evaluate") if crn else env.rng_for("evaluate", cand.digest())
    return e


# ---------------------------------------------------------------------------------------------------
# Fingerprints and cache keys


def _feed(h: Any, obj: Any) -> None:
    """Feed a stable description of ``obj`` into hash ``h`` (arrays by content)."""
    if isinstance(obj, np.ndarray):
        h.update(f"nd{obj.dtype.str}{obj.shape}".encode())
        h.update(np.ascontiguousarray(obj).tobytes())
    elif isinstance(obj, Mapping):
        h.update(b"{")
        for k in sorted(obj, key=str):
            h.update(str(k).encode() + b":")
            _feed(h, obj[k])
        h.update(b"}")
    elif isinstance(obj, (list, tuple)):
        h.update(b"[")
        for v in obj:
            _feed(h, v)
        h.update(b"]")
    elif isinstance(obj, ParamSet):
        h.update(obj.digest().encode())
    elif dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        h.update(type(obj).__qualname__.encode())
        _feed(h, {f.name: getattr(obj, f.name) for f in dataclasses.fields(obj)})
    elif isinstance(obj, np.random.Generator):
        h.update(b"<rng>")   # the per-candidate rng is reset anyway
    else:
        h.update(repr(obj).encode())


def fingerprint(obj: Any) -> str:
    """sha256 of a stable description of ``obj`` (arrays hashed by content, dicts by sorted key)."""
    h = hashlib.sha256()
    _feed(h, obj)
    return h.hexdigest()


def env_fingerprint(env: Env, *, crn: bool = True) -> str:
    """Fingerprint of everything in ``env`` that can change a score: seed, budget, policy, data,
    scenarios, provenance and the CRN mode."""
    pol = env.policy.digest() if hasattr(env.policy, "digest") else fingerprint(env.policy)
    return fingerprint({
        "seed": env.seed, "budget": dataclasses.asdict(env.budget), "policy": pol,
        "data": env.data, "scenarios": env.scenarios, "provenance": env.provenance, "crn": crn,
    })


def evaluator_id(fn: Any) -> str:
    """A stable name for an evaluation callable (``module.Class.method[group]``)."""
    owner = getattr(fn, "__self__", None)
    if owner is not None:
        cls = type(owner)
        group = getattr(owner, "group", "")
        return f"{cls.__module__}.{cls.__qualname__}.{getattr(fn, '__name__', 'evaluate')}[{group}]"
    mod = getattr(fn, "__module__", type(fn).__module__)
    name = getattr(fn, "__qualname__", type(fn).__qualname__)
    return f"{mod}.{name}"


def metrics_to_dict(m: Metrics) -> dict[str, Any]:
    """JSON-ready form of :class:`Metrics` (``meta`` must itself be JSON-serialisable)."""
    return {"values": {k: float(v) for k, v in m.values.items()}, "primary": m.primary,
            "minimize": m.minimize, "constraints": {k: bool(v) for k, v in m.constraints.items()},
            "provenance": m.provenance, "meta": dict(m.meta)}


def metrics_from_dict(d: Mapping[str, Any]) -> Metrics:
    """Inverse of :func:`metrics_to_dict`."""
    return Metrics(values=dict(d["values"]), primary=d["primary"], minimize=bool(d["minimize"]),
                   constraints=dict(d.get("constraints", {})), provenance=d.get("provenance", "synthetic"),
                   meta=dict(d.get("meta", {})))


@dataclass
class EvalCache:
    """Scores keyed by ``(evaluator, ParamSet.digest(), env fingerprint)``.

    Always in memory; with ``directory`` set, entries are also written as JSON files
    (``<directory>/<key[:2]>/<key>.json``) and read back by later runs. Entries whose ``meta`` is
    not JSON-serialisable stay memory-only.
    """

    directory: Path | None = None
    hits: int = 0
    misses: int = 0
    _mem: dict[str, Metrics] = field(default_factory=dict, repr=False)
    _envfp: dict[int, tuple[Env, str]] = field(default_factory=dict, repr=False)

    @classmethod
    def on_disk(cls, directory: str | Path = DEFAULT_CACHE_DIR) -> EvalCache:
        """A cache persisted under ``directory`` (default ``.work/cache``)."""
        return cls(Path(directory))

    def env_key(self, env: Env, *, crn: bool = True) -> str:
        """Memoised :func:`env_fingerprint` (an ``Env`` is hashed once per object)."""
        hit = self._envfp.get(id(env))
        if hit is not None and hit[0] is env:
            return hit[1] + ("" if crn else "-indep")
        fp = env_fingerprint(env, crn=True)
        self._envfp[id(env)] = (env, fp)
        return fp + ("" if crn else "-indep")

    @staticmethod
    def key(evaluator: str, cand: ParamSet, env_fp: str) -> str:
        """The cache key."""
        return hashlib.sha256(f"{evaluator}|{cand.digest()}|{env_fp}".encode()).hexdigest()

    def _path(self, key: str) -> Path | None:
        return None if self.directory is None else self.directory / key[:2] / f"{key}.json"

    def get(self, key: str) -> Metrics | None:
        """A cached score, or ``None``."""
        m = self._mem.get(key)
        if m is None and (p := self._path(key)) is not None and p.exists():
            try:
                m = metrics_from_dict(json.loads(p.read_text()))
            except (OSError, ValueError, KeyError):
                m = None
            if m is not None:
                self._mem[key] = m
        if m is None:
            self.misses += 1
        else:
            self.hits += 1
        return m

    def put(self, key: str, m: Metrics) -> None:
        """Store a score (and write it to disk when persistent and serialisable)."""
        self._mem[key] = m
        p = self._path(key)
        if p is None:
            return
        try:
            text = json.dumps(metrics_to_dict(m), sort_keys=True)
        except (TypeError, ValueError):
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(text)
        tmp.replace(p)

    def __len__(self) -> int:
        return len(self._mem)


# ---------------------------------------------------------------------------------------------------
# Evaluation

_WORKER: dict[str, Any] = {}


def _init_worker(fn: EvalFn, env: Env, crn: bool) -> None:
    _WORKER["fn"], _WORKER["env"], _WORKER["crn"] = fn, env, crn


def _work(cand: ParamSet) -> Metrics:
    return _WORKER["fn"](cand, candidate_env(_WORKER["env"], cand, crn=_WORKER["crn"]))


def _picklable(*objs: Any) -> bool:
    try:
        for o in objs:
            pickle.dumps(o)
    except Exception:
        return False
    return True


@dataclass
class EvalStats:
    """Counters of one :func:`evaluate_many` call."""

    n: int = 0
    evaluated: int = 0
    cached: int = 0
    seconds: float = 0.0
    workers: int = 1


def evaluate_many(
    fn: EvalFn,
    cands: Sequence[ParamSet],
    env: Env,
    *,
    workers: int | None = 1,
    cache: EvalCache | None = None,
    crn: bool = True,
    evaluator: str | None = None,
    stats: EvalStats | None = None,
) -> list[Metrics]:
    """Score ``cands`` with ``fn(cand, candidate_env(env, cand))``, in input order.

    ``workers=None`` → :func:`default_workers`; ``workers<=1`` (or ≤ 1 uncached candidate) runs
    serially in-process. Results are identical for any worker count (see module docstring).
    Duplicate candidates are scored once.
    """
    t0 = time.perf_counter()
    nw = default_workers() if workers is None else max(1, int(workers))
    eid = evaluator or evaluator_id(fn)
    efp = cache.env_key(env, crn=crn) if cache is not None else ""
    out: list[Metrics | None] = [None] * len(cands)
    todo: dict[str, list[int]] = {}
    keys: dict[str, str] = {}
    first: dict[str, ParamSet] = {}
    for i, c in enumerate(cands):
        d = c.digest()
        if cache is not None:
            k = keys.setdefault(d, EvalCache.key(eid, c, efp))
            hit = cache.get(k) if d not in todo else None
            if hit is not None:
                out[i] = hit
                continue
        todo.setdefault(d, []).append(i)
        first.setdefault(d, c)
    uniq = list(todo)
    if nw > 1 and len(uniq) > 1 and not _picklable(fn, env):
        warnings.warn("evaluation callable or Env is not picklable; evaluating serially "
                      "(define studies at module level)", RuntimeWarning, stacklevel=2)
        nw = 1
    if nw > 1 and len(uniq) > 1:
        nw = min(nw, len(uniq))
        with ProcessPoolExecutor(max_workers=nw, initializer=_init_worker, initargs=(fn, env, crn)) as ex:
            chunk = max(1, len(uniq) // (4 * nw))
            results = list(ex.map(_work, [first[d] for d in uniq], chunksize=chunk))
    else:
        nw = 1
        results = [fn(first[d], candidate_env(env, first[d], crn=crn)) for d in uniq]
    for d, m in zip(uniq, results, strict=True):
        if not isinstance(m, Metrics):
            raise TypeError(f"{eid} returned {type(m).__name__}, not Metrics")
        for i in todo[d]:
            out[i] = m
        if cache is not None:
            cache.put(keys[d], m)
    if stats is not None:
        stats.n += len(cands)
        stats.evaluated += len(uniq)
        stats.cached += len(cands) - sum(len(v) for v in todo.values())
        stats.seconds += time.perf_counter() - t0
        stats.workers = max(stats.workers, nw)
    return [m for m in out if m is not None]


def evaluate_set(study: Study | EvalFn, paramset: ParamSet, env: Env, *,
                 cache: EvalCache | None = None, crn: bool = True) -> Metrics:
    """Score one set with ``study.evaluate`` (or a bare ``fn(cand, env)``), through ``cache``."""
    fn = study.evaluate if isinstance(study, Study) else study
    return evaluate_many(fn, [paramset], env, workers=1, cache=cache, crn=crn)[0]


def rank_key(m: Metrics, metric: str | None = None, minimize: bool | None = None) -> tuple[int, int, float]:
    """Sort key "best first": feasible before infeasible (fewer violations first), then the metric
    in its direction (NaN last)."""
    v = m.primary_value if metric is None else float(m.values[metric])
    mini = m.minimize if minimize is None else minimize
    if math.isnan(v):
        return (int(not m.feasible), len(m.violated), math.inf)
    return (int(not m.feasible), len(m.violated), v if mini else -v)


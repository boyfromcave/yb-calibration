"""The real pool landscape as a per-block signal process (owner: G5 activation, wave 2).

``ybcal.studies.g5_activation`` was built on a binomial model: every block signals independently with
the enforcing share ``p``, and share drift enters as a mixture over window means. The real Ycash
landscape (``data/local/pool-shares.csv``, 70,000 blocks, docs/real-data-2026-10.md §8) is not like
that: about four operators, one with 52 % of the blocks, and a ~25 % block of hash that moved from an
unidentified payout key to zpool on one day. The signal count of a window is then driven by which
*operators* enforce and by their day-to-day share swings, not by binomial noise (σ of a 2,016-block
window: ~1 point binomial, 4–9 points real).

This module replays that process exactly:

* :class:`Landscape` — who mined each real block, as operator ids (payout keys named and optionally
  merged, e.g. the unidentified key and zpool into one "flex" operator).
* :func:`block_bootstrap` — circular moving-block bootstrap of the real per-block sequence: paths of
  any length made of real stretches of ``block_len`` blocks (default 3 days, longer than a signal
  window, so the within-window structure is real and only the order of multi-day stretches is
  resampled).
* :func:`halt_stats` / :func:`activation_stats` — ACT-1..6 (``ybcal.sim.activation``, the exact node
  state machine, ycash6 ``src/yellowback/state.cpp:959,1080-1089,1242-1249``) over those paths:
  false PARTICIPATION / ENFORCEMENT hours and episodes per year while every coalition member is
  honest and online, the longest ENFORCEMENT run (W19 freeze window, abandonment), and the
  lock-in time distribution.
* :func:`coalitions` — every subset of the named operators whose mean share is a majority.

Everything is a pure function of the log, the parameters and an ``np.random.Generator``.
"""

from __future__ import annotations

import functools
import itertools
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from ybcal.model import vkernels as V
from ybcal.units import BLOCKS_PER_DAY, BLOCKS_PER_HOUR, BLOCKS_PER_YEAR

OWNER_WP = "WP-5"

#: Default bootstrap stretch: three days (> signalWindow 2,016 blocks = 1.75 d).
DEFAULT_BLOCK_LEN = 3 * BLOCKS_PER_DAY


@dataclass(frozen=True)
class Landscape:
    """Operator of every real block: ``op[j]`` indexes ``names`` (``-1`` = an unnamed small key)."""

    names: tuple[str, ...]
    op: np.ndarray                       #: int16 (n_blocks,)
    source: str = ""
    keys: Mapping[str, tuple[str, ...]] = field(default_factory=dict)   #: name → payout keys

    def __len__(self) -> int:
        return int(self.op.size)

    @classmethod
    def from_log(cls, log, *, top: int = 6, names: Mapping[str, str] | None = None,
                 merge: Mapping[str, Sequence[str]] | None = None) -> Landscape:
        """From a ``PoolShareLog``: the ``top`` payout keys by share become operators, named by
        ``names`` (key or key prefix → name; default ``k<rank>:<key[:8]>``); the rest are ``-1``.
        ``merge`` maps a new operator name to the names it absorbs (one operator behind several keys)."""
        counts = np.bincount(log.pool, minlength=len(log.keys))
        order = [int(i) for i in np.argsort(-counts, kind="stable")[:top] if counts[i] > 0]
        names = dict(names or {})

        def nm(key: str, rank: int) -> str:
            for k, v in names.items():
                if key == k or key.startswith(k):
                    return v
            return f"k{rank}:{key[:8]}"

        base = [nm(log.keys[i], r) for r, i in enumerate(order)]
        keys = {n: (log.keys[i],) for n, i in zip(base, order, strict=True)}
        # merges: operator name → new name
        target = {n: n for n in base}
        for new, olds in (merge or {}).items():
            for o in olds:
                if o not in target:
                    raise KeyError(f"merge: unknown operator {o!r} (have {base})")
                target[o] = new
        final: list[str] = []
        for n in base:
            if target[n] not in final:
                final.append(target[n])
        lut = np.full(len(log.keys), -1, dtype=np.int16)
        for n, i in zip(base, order, strict=True):
            lut[i] = final.index(target[n])
        merged_keys: dict[str, tuple[str, ...]] = {}
        for n in base:
            merged_keys[target[n]] = merged_keys.get(target[n], ()) + keys[n]
        return cls(tuple(final), lut[np.asarray(log.pool)], getattr(log, "source", ""), merged_keys)

    def index(self, name: str) -> int:
        try:
            return self.names.index(name)
        except ValueError:
            raise KeyError(f"unknown operator {name!r} (have {self.names})") from None

    def indicator(self, members: Iterable[str]) -> np.ndarray:
        """bool per real block: mined by a member of the coalition."""
        idx = [self.index(m) for m in members]
        return np.isin(self.op, np.asarray(idx, dtype=np.int16)) if idx else np.zeros(len(self), bool)

    def shares(self) -> dict[str, float]:
        n = max(1, len(self))
        out = {nm: float(np.mean(self.op == i)) for i, nm in enumerate(self.names)}
        out["(others)"] = float(np.mean(self.op < 0)) if n else 0.0
        return out

    def daily_shares(self, blocks_per_day: int = BLOCKS_PER_DAY) -> np.ndarray:
        """``(days, operators + 1)`` block shares per whole day (last column: others)."""
        d = len(self) // blocks_per_day
        seg = self.op[: d * blocks_per_day].reshape(d, blocks_per_day)
        cols = [(seg == i).mean(axis=1) for i in range(len(self.names))] + [(seg < 0).mean(axis=1)]
        return np.stack(cols, axis=1) if d else np.zeros((0, len(self.names) + 1))


def coalitions(land: Landscape, *, min_share: float = 0.5,
               exclude: Iterable[str] = ()) -> list[tuple[str, ...]]:
    """Every subset of the named operators (minus ``exclude``) whose mean block share ≥ ``min_share``,
    largest share first."""
    sh = land.shares()
    pool = [n for n in land.names if n not in set(exclude)]
    out = []
    for r in range(1, len(pool) + 1):
        for c in itertools.combinations(pool, r):
            if sum(sh[m] for m in c) >= min_share:
                out.append(c)
    return sorted(out, key=lambda c: (-sum(sh[m] for m in c), len(c)))


def block_bootstrap(x: np.ndarray, n_blocks: int, n_paths: int, rng: np.random.Generator, *,
                    block_len: int = DEFAULT_BLOCK_LEN) -> np.ndarray:
    """Circular moving-block bootstrap of a 1-d series: ``(n_paths, n_blocks)`` made of stretches of
    ``block_len`` consecutive real values starting at uniform random offsets (wrapping at the end)."""
    x = np.asarray(x)
    n = x.size
    if n == 0:
        raise ValueError("empty series")
    L = max(1, min(int(block_len), n))
    k = -(-n_blocks // L)
    starts = rng.integers(0, n, size=(n_paths, k))
    idx = (starts[:, :, None] + np.arange(L)[None, None, :]) % n
    return x[idx.reshape(n_paths, k * L)[:, :n_blocks]]


def _run_lengths_max(m: np.ndarray) -> np.ndarray:
    """Longest run of True per row."""
    out = np.zeros(m.shape[0], dtype=np.int64)
    for i, row in enumerate(m):
        if not row.any():
            continue
        d = np.diff(np.concatenate([[0], row.astype(np.int8), [0]]))
        s, e = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
        out[i] = int((e - s).max())
    return out


def _episodes(h: np.ndarray) -> np.ndarray:
    """Halt episodes (rising edges, a halt already set at column 0 counts) per row."""
    return h[:, 0].astype(np.int64) + (h[:, 1:] & ~h[:, :-1]).sum(axis=1)


@dataclass(frozen=True)
class HaltStats:
    """ACT-4/ACT-6 while ACTIVE throughout (paths × blocks of a bootstrapped coalition series)."""

    path_years: float
    part_hours_per_year: float
    enf_hours_per_year: float
    part_episodes_per_year: float
    enf_episodes_per_year: float
    part_longest_blocks_p95: float        #: p95 over paths of the longest PARTICIPATION run
    enf_longest_blocks_max: float
    p_enf_full_window_per_year: float      #: P(an ENFORCEMENT run ≥ signalWindow) per year (W19 opens)
    p_part_any_per_year: float             #: P(any PARTICIPATION halt) per path-year
    p_enf_any_per_year: float
    mean_share: float
    window_share_p01: float

    @property
    def hours_per_year(self) -> float:
        """Minting-halted hours (ENFORCEMENT implies a mint halt; the union is bounded by the sum)."""
        return self.part_hours_per_year + self.enf_hours_per_year

    @property
    def flaps_per_year(self) -> float:
        return self.part_episodes_per_year + self.enf_episodes_per_year

    def to_dict(self) -> dict[str, float]:
        d = {k: float(getattr(self, k)) for k in self.__dataclass_fields__}
        d["hours_per_year"] = self.hours_per_year
        d["flaps_per_year"] = self.flaps_per_year
        return d


def window_counts(sig: np.ndarray, window: int) -> np.ndarray:
    """Signal counts of the trailing ``window`` at every column after the first full window
    (``sig`` (paths, n) → (paths, n − window + 1)), in int32."""
    c = np.cumsum(sig, axis=1, dtype=np.int32)
    out = c[:, window - 1:].copy()
    out[:, 1:] -= c[:, :-window]
    return out


def halt_stats(params: Mapping, sig: np.ndarray, *, chunk_paths: int = 16) -> HaltStats:
    """ACT-4 / ACT-6 over ``sig`` (paths, n) with the chain ACTIVE from the first full window on and
    no halt set (ycash6 ``state.cpp:1242-1249``: PARTICIPATION set below ``participationFloor``, held
    below ``activationThreshold``; ENFORCEMENT set below ``enforcementFloor``, held below
    ``enforcementResume``). Every halt here is *false*: the coalition members mine honestly; only
    their real share swings move the count."""
    W = int(params["signalWindow"])
    T, PF = int(params["activationThreshold"]), int(params["participationFloor"])
    EF, ER = int(params["enforcementFloor"]), int(params["enforcementResume"])
    sig = np.asarray(sig, dtype=bool)
    if sig.ndim == 1:
        sig = sig[None, :]
    paths, n = sig.shape
    if n <= W:
        raise ValueError("series shorter than the signal window")
    tot_blocks = 0
    part_b = enf_b = part_e = enf_e = 0
    part_long, enf_long, full_win, part_any, enf_any = [], [], [], [], []
    p01 = []
    for a in range(0, paths, chunk_paths):
        c = window_counts(sig[a:a + chunk_paths], W)
        act = np.ones_like(c, dtype=bool)
        ph = V.participation_halt_series(c, act, T, PF)
        eh = V.enforcement_halt_series(c, act, ER, EF)
        tot_blocks += c.size
        part_b += int(ph.sum())
        enf_b += int(eh.sum())
        part_e += int(_episodes(ph).sum())
        enf_e += int(_episodes(eh).sum())
        pl, el = _run_lengths_max(ph), _run_lengths_max(eh)
        part_long += list(pl)
        enf_long += list(el)
        full_win += list(el >= W)
        part_any += list(pl > 0)
        enf_any += list(el > 0)
        p01.append(np.quantile(c, 0.01) / W)
    years = tot_blocks / BLOCKS_PER_YEAR
    per_path_years = (n - W + 1) / BLOCKS_PER_YEAR
    hours = BLOCKS_PER_YEAR / BLOCKS_PER_HOUR

    def per_year(p_any_path: float) -> float:
        # P(at least one) per path → per year, assuming independence across years of a path
        return 1.0 - (1.0 - p_any_path) ** (1.0 / per_path_years) if p_any_path < 1 else 1.0

    return HaltStats(
        path_years=years,
        part_hours_per_year=part_b / tot_blocks * hours,
        enf_hours_per_year=enf_b / tot_blocks * hours,
        part_episodes_per_year=part_e / years,
        enf_episodes_per_year=enf_e / years,
        part_longest_blocks_p95=float(np.quantile(part_long, 0.95)),
        enf_longest_blocks_max=float(max(enf_long)),
        p_enf_full_window_per_year=per_year(float(np.mean(full_win))),
        p_part_any_per_year=per_year(float(np.mean(part_any))),
        p_enf_any_per_year=per_year(float(np.mean(enf_any))),
        mean_share=float(sig.mean()),
        window_share_p01=float(np.median(p01)),
    )


@dataclass(frozen=True)
class ActivationStats:
    """Lock-in from a start at a random point of the real process (ACT-1..3)."""

    p_first_window: float           #: P(lock-in at the first eligible height start + W − 1)
    p_within: dict[int, float]      #: days → P(lock-in within that many days of the start)
    median_days: float              #: median lock-in time, days (inf if < half lock in)
    p95_days: float
    activate_extra_days: float      #: activationDelay in days (ACTIVE = lock-in + delay)

    def to_dict(self) -> dict:
        return {"p_first_window": self.p_first_window,
                "p_within": {str(k): v for k, v in self.p_within.items()},
                "median_days": self.median_days, "p95_days": self.p95_days,
                "activate_extra_days": self.activate_extra_days}


def activation_stats(params: Mapping, sig: np.ndarray, *, horizons_days: Sequence[int] = (7, 30, 90, 365)
                     ) -> ActivationStats:
    """ACT-2 lock-in (ycash6 ``state.cpp:1084``: the first ``H ≥ start + W − 1`` with
    ``count ≥ activationThreshold``) on each path, the start being column 0."""
    W, T = int(params["signalWindow"]), int(params["activationThreshold"])
    sig = np.asarray(sig, dtype=bool)
    if sig.ndim == 1:
        sig = sig[None, :]
    c = window_counts(sig, W)                     # column j ↔ height start + W − 1 + j
    q = c >= T
    anyq = q.any(axis=1)
    first = np.where(anyq, np.argmax(q, axis=1), np.iinfo(np.int64).max)
    lock_blocks = np.where(anyq, first + W - 1, np.inf)     # blocks after start
    days = lock_blocks / BLOCKS_PER_DAY
    horizon_days = sig.shape[1] / BLOCKS_PER_DAY
    within = {int(h): float(np.mean(days <= h)) for h in horizons_days if h <= horizon_days}
    fin = float(np.mean(np.isfinite(days)))
    med = float(np.quantile(days, 0.5, method="higher")) if fin >= 0.5 else math.inf
    p95 = float(np.quantile(days, 0.95, method="higher")) if fin >= 0.95 else math.inf
    return ActivationStats(float(q[:, 0].mean()), within, med, p95,
                           int(params["activationDelay"]) / BLOCKS_PER_DAY)


def drop_detection_blocks(params: Mapping, sig_before: np.ndarray, sig_after: np.ndarray) -> np.ndarray:
    """Blocks from a regime change (``sig_before`` fills the window, then ``sig_after``) until the
    ENFORCEMENT halt sets (count < enforcementFloor); ``-1`` if never within ``sig_after``."""
    W, EF = int(params["signalWindow"]), int(params["enforcementFloor"])
    full = np.concatenate([np.asarray(sig_before, bool)[:, -W:], np.asarray(sig_after, bool)], axis=1)
    c = window_counts(full, W)[:, 1:]
    hit = c < EF
    return np.where(hit.any(axis=1), np.argmax(hit, axis=1) + 1, -1)


def race_trip_probability(q: float, valve_blocks: int) -> float:
    """One ACT-7 race: a non-enforcing miner (hash share ``q``) mines a block enforcers reject on the
    common tip (lead 1); stock miners keep their own branch at equal work (first seen) and leave it
    only when the enforcers' chain is one block heavier (lead −1); the valve trips at lead
    ``valveBlocks`` (ycash6 ``src/yellowback/index.cpp:560`` ``nChainWork ≥ tip + proof·valveBlocks``).
    Gambler's ruin from 2 with absorbing barriers 0 and ``valveBlocks + 1``."""
    if q <= 0:
        return 0.0
    if q >= 1:
        return 1.0
    rho = (1.0 - q) / q
    n = int(valve_blocks) + 1
    if abs(rho - 1.0) < 1e-12:
        return 2.0 / n
    # (1 − ρ^2) / (1 − ρ^n), written to avoid overflow for large ρ^n
    if rho > 1:
        return float((rho ** -n - rho ** (2 - n)) / (rho ** -n - 1.0))
    return float((1.0 - rho ** 2) / (1.0 - rho ** n))


def valve_race_trips(nonenf: np.ndarray, valve_blocks: int) -> np.ndarray:
    """Sustained ACT-7 attack over a per-block miner sequence: ``nonenf`` (paths, n) is True where a
    non-enforcing miner mines the next block. A rule-breaking transaction (e.g. a matured vault swept
    without burning YED) sits in every stock mempool, so each stock block mined on the enforcers' tip
    starts a race, and a lost race returns the transaction to the mempool for the next one (races run
    back to back). Returns the block index of the first valve trip per path (``-1`` = none)."""
    x = np.asarray(nonenf, dtype=bool)
    if x.ndim == 1:
        x = x[None, :]
    paths, n = x.shape
    V = int(valve_blocks)
    lead = np.zeros(paths, dtype=np.int64)          # 0 = no race (stock miners on the enforcers' tip)
    racing = np.zeros(paths, dtype=bool)
    trip = np.full(paths, -1, dtype=np.int64)
    alive = np.ones(paths, dtype=bool)
    for j in range(n):
        s = x[:, j] & alive
        e = ~x[:, j] & alive
        # stock block: starts a race (lead 1) or extends the stock branch
        lead = np.where(s, np.where(racing, lead + 1, 1), lead)
        racing = racing | s
        # enforcer block during a race: the enforcers' chain gains one
        lead = np.where(e & racing, lead - 1, lead)
        lost = racing & (lead < 0)
        racing &= ~lost
        lead = np.where(lost, 0, lead)
        hit = racing & (lead >= V)
        trip = np.where(hit & (trip < 0), j, trip)
        alive &= ~hit
        if not alive.any():
            break
    return trip


def devnet_steps(signal: np.ndarray, factor: float, *, price_micro_usd: int = 50_000_000, start: int = 0,
                 n_blocks: int | None = None, bootstrap: str = "active", walk_bps: int = 30,
                 seed: int = 0, description: str = "") -> dict:
    """A ``ybcal-steps/1`` devnet schedule replaying a real per-block signal sequence at regtest scale:
    regtest block ``k`` carries the signalling share of mainnet blocks ``[start + k·f, start + (k+1)·f)``
    (``f`` = the scaler's block factor, 31.5 for the shipped set), realised exactly by the devnet's
    smooth weighted round robin (a non-signalling "dark" miner mines the rest). The window-scale share
    process that drives ACT-1..6 is preserved; sub-window bursts are averaged. Prices follow a calm
    ±``walk_bps`` random walk (the scenario is about signalling, not prices)."""
    x = np.asarray(signal, dtype=float)
    f = float(factor)
    total = int((x.size - start) / f) if n_blocks is None else int(n_blocks)
    rng = np.random.default_rng(seed)
    steps = []
    p = int(price_micro_usd)
    for k in range(total):
        a, b = start + round(k * f), start + round((k + 1) * f)
        if b > x.size:
            break
        share = round(10_000 * float(x[a:b].mean())) if b > a else 10_000
        p = max(1, int(p * (1 + rng.integers(-walk_bps, walk_bps + 1) / 10_000)))
        steps.append({"price": p, "blocks": 1, "signal_share_bps": share, "label": f"real[{a}:{b}]"})
    return {"format": "ybcal-steps/1", "bootstrap": bootstrap, "needs": ["dark_miner"],
            "description": description or f"real signalling share replayed at 1/{f:g} scale", "steps": steps}


#: ycash6 ``src/yellowback/index.h:65``: headers noted per rejected root before the valve stops noting.
VALVE_NOTE_CAP = 64


@functools.lru_cache(maxsize=1024)
def race_outcomes(q: float, valve_blocks: int, note_cap: int = VALVE_NOTE_CAP) -> dict[str, float]:
    """Exact outcome probabilities of one ACT-7 race including the note cap: the stock branch starts
    one block ahead (its root, not a note); each further stock header is a note, and once a root holds
    ``note_cap`` notes the next header is refused (``index.cpp:607``) so the valve can no longer trip
    on that root ("stuck": the node stays on its own chain until an operator restarts it). Returns
    ``trip``, ``stuck`` and ``lost`` (the enforcers' chain gets one block heavier: stock miners
    rejoin it). Dynamic programming over (lead, notes)."""
    V = int(valve_blocks)
    if V <= 1:
        return {"trip": 1.0, "stuck": 0.0, "lost": 0.0}
    # mass[lead, notes] for lead in 0..V-1 (lead 1 at the start, 0 notes)
    mass = np.zeros((V, note_cap + 1))
    mass[1, 0] = 1.0
    trip = stuck = lost = 0.0
    for _ in range(100_000):
        if mass.sum() < 1e-15:
            break
        new = np.zeros_like(mass)
        # stock block: lead + 1, notes + 1
        up = mass * q
        stuck += float(up[:, note_cap].sum())
        moved = up[:, :note_cap]
        trip += float(moved[V - 1, :].sum())
        new[1:V, 1:] += moved[0:V - 1, :]
        # enforcer block: lead − 1
        dn = mass * (1.0 - q)
        lost += float(dn[0, :].sum())
        new[0:V - 1, :] += dn[1:V, :]
        mass = new
    return {"trip": trip, "stuck": stuck, "lost": lost}


def valve_stuck_share(q: float, valve_blocks: int, note_cap: int = VALVE_NOTE_CAP) -> float:
    """When enforcers are a genuine minority (stock share ``q`` > ½) races repeat until one ends in a
    trip or the note cap: the probability that the node ends **stuck** rather than rejoining,
    ``stuck / (stuck + trip)`` of :func:`race_outcomes`."""
    o = race_outcomes(q, valve_blocks, note_cap)
    tot = o["stuck"] + o["trip"]
    return float(o["stuck"] / tot) if tot > 0 else 1.0


# ---------------------------------------------------------------------------------------------------
# CLI: ``ybcal data landscape`` — the coalition table behind G5's real-landscape decisions


def configure_landscape(p) -> None:
    """Extra ``data landscape`` arguments."""
    p.add_argument("--names", default=None, help="pool-names.csv (payout_key,pool) to name operators")
    p.add_argument("--merge", action="append", default=[],
                   help="NEW=a,b: one operator behind several names (repeatable), e.g. flex=unid,zpool.ca")
    p.add_argument("--coalition", action="append", default=[],
                   help="operators joined by '+' (repeatable); default: every subset of the top 6 "
                        "with a mean share >= 40 %%")
    p.add_argument("--window", default="2016", help="signalWindow values, comma separated (thresholds "
                   "at the shipped fractions 75/60/50/60 %%)")
    p.add_argument("--valve", default="", help="valveBlocks values for the race-attack table (comma sep.)")
    p.add_argument("--attack-days", type=float, default=30.0)
    p.add_argument("--paths", type=int, default=64)
    p.add_argument("--years", type=float, default=2.0)
    p.add_argument("--block-days", type=float, default=3.0, help="bootstrap stretch length, days")
    p.add_argument("--span", default="", help="A:B — use real blocks [A, B) of the log only")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--out", default=None, help="CSV of every row")


def landscape_table(land: Landscape, coals: Sequence[Sequence[str]], windows: Sequence[int], *,
                    paths: int = 64, years: float = 2.0, block_len: int = DEFAULT_BLOCK_LEN,
                    seed: int = 1, base=None) -> list[dict]:
    """One row per (coalition, window): real-landscape halts and lock-in at the shipped fractions."""
    from ybcal.params.paramset import mainnet

    base = base if base is not None else mainnet()
    rng = np.random.default_rng(seed)
    sh = land.shares()
    rows = []
    for c in coals:
        x = land.indicator(c)
        sig = block_bootstrap(x, int(years * BLOCKS_PER_YEAR), paths, rng, block_len=block_len)
        for W in windows:
            W0 = int(base["signalWindow"])
            def sc(k: str, W: int = W, W0: int = W0) -> int:
                return round(int(base[k]) * W / W0)

            ps = base.replace({"signalWindow": W, "activationThreshold": sc("activationThreshold"),
                               "participationFloor": sc("participationFloor"),
                               "enforcementFloor": max(-(-W // 2), sc("enforcementFloor")),
                               "enforcementResume": sc("enforcementResume")})
            h = halt_stats(ps, sig)
            a = activation_stats(ps, sig[:, : 120 * BLOCKS_PER_DAY], horizons_days=(7, 30, 60, 90))
            rows.append({"coalition": "+".join(c), "share": round(sum(sh[m] for m in c), 4),
                         "signalWindow": W,
                         **{k: round(v, 4) for k, v in h.to_dict().items()},
                         "lock_first": a.p_first_window, **{f"lock_{d}d": v for d, v in a.p_within.items()},
                         "lock_median_days": a.median_days, "lock_p95_days": a.p95_days})
    return rows


def cli_landscape(args) -> int:
    """Coalition × window table of the real pool landscape (and the valve's race-attack table)."""
    import csv
    import json
    import sys

    from ybcal.data.loaders import DataFormatError, load_pool_shares_csv

    try:
        log = load_pool_shares_csv(args.file)
    except (DataFormatError, OSError) as e:
        print(f"ybcal data landscape: {e}", file=sys.stderr)
        return 2
    if getattr(args, "span", ""):
        a, b = (int(v) for v in args.span.split(":"))
        from ybcal.data.loaders import PoolShareLog

        log = PoolShareLog(log.heights[a:b], log.pool[a:b], log.keys, log.source)
    names: dict[str, str] = {}
    if args.names:
        with open(args.names) as fh:
            for r in csv.DictReader(fh):
                names[r["payout_key"]] = r["pool"]
    merge = {}
    for m in args.merge:
        new, olds = m.split("=", 1)
        merge[new] = olds.split(",")
    land = Landscape.from_log(log, names=names, merge=merge or None)
    sh = land.shares()
    print("operators: " + ", ".join(f"{k} {v:.1%}" for k, v in sh.items()))
    coals = ([tuple(c.split("+")) for c in args.coalition] if args.coalition
             else [c for c in coalitions(land, min_share=0.4)])
    windows = [int(w) for w in str(args.window).split(",") if w]
    rows = landscape_table(land, coals, windows, paths=args.paths, years=args.years,
                           block_len=int(args.block_days * BLOCKS_PER_DAY), seed=args.seed)
    for r in rows:
        print(f"{r['coalition']:<48} {r['share']:.3f} W {r['signalWindow']:>5}  "
              f"PART {r['part_hours_per_year']:8.1f} h/yr "
              f"ENF {r['enf_hours_per_year']:8.1f} h/yr  flaps {r['flaps_per_year']:5.2f}  "
              f"lock 30d {r.get('lock_30d', float('nan')):.2f} 60d {r.get('lock_60d', float('nan')):.2f}  "
              f"median {r['lock_median_days']:.1f} d")
    vrows = []
    if args.valve:
        rng = np.random.default_rng(args.seed)
        for c in coals:
            non = ~land.indicator(c)
            seq = block_bootstrap(non, int(args.attack_days * BLOCKS_PER_DAY), max(64, args.paths), rng,
                                  block_len=int(args.block_days * BLOCKS_PER_DAY))
            for V in (int(v) for v in args.valve.split(",") if v):
                tr = valve_race_trips(seq, V)
                vrows.append({"coalition": "+".join(c), "stock_share": round(float(non.mean()), 4),
                              "valveBlocks": V,
                              "p_trip": float(np.mean(tr >= 0)),
                              "median_days": float(np.median(tr[tr >= 0]) / BLOCKS_PER_DAY) if (tr >= 0).any()
                              else math.inf,
                              "capstuck_q055": valve_stuck_share(0.55, V)})
                v = vrows[-1]
                print(f"valve {v['coalition']:<42} stock {v['stock_share']:.3f} V {V:>3}  "
                      f"P(trip in {args.attack_days:g} d) {v['p_trip']:.2f}  "
                      f"stuck@q0.55 {v['capstuck_q055']:.3f}")
    if args.out:
        with open(args.out, "w", newline="") as fh:
            keys = sorted({k for r in rows for k in r})
            w = csv.DictWriter(fh, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)
        if vrows:
            with open(str(args.out).replace(".csv", "") + "-valve.csv", "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(vrows[0]))
                w.writeheader()
                w.writerows(vrows)
        print(json.dumps({"rows": len(rows), "valve_rows": len(vrows), "out": args.out}))
    return 0


__all__ = [
    "DEFAULT_BLOCK_LEN",
    "VALVE_NOTE_CAP",
    "ActivationStats",
    "HaltStats",
    "Landscape",
    "activation_stats",
    "block_bootstrap",
    "coalitions",
    "devnet_steps",
    "drop_detection_blocks",
    "halt_stats",
    "race_outcomes",
    "race_trip_probability",
    "valve_race_trips",
    "valve_stuck_share",
    "window_counts",
]

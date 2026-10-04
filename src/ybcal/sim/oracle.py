"""Pool quote generation, tag cadence, PRICE-1 medians, HALT-3 (owner: WP-3; PLAN §3.3, §5.1, §5.7).

Two halves:

1. **Quote generation** (:class:`Pool`, :class:`Attack`, :class:`OracleConfig`,
   :func:`generate_block_inputs`): turns true-price paths ``(paths, n)`` (int64 µUSD, element 0 at
   ``startHeight``) into the per-block coinbase-tag stream the node sees — which pool mined each
   block (hash shares; the remainder are stock, untagged miners), whether it tagged, its quote (a
   TWAP of the true price over ``twap_blocks`` ≈ 15 min = 12 blocks, times a persistent bias and
   per-quote noise in bps, refreshed every ``refresh_blocks``), outages (signal-only, untagged or
   stale), the signal bit, and coalition attacks (bias ±X %, withholding) on a schedule. Floats are
   used here only to *generate* behaviour; every quote is an integer in [PRICE_MIN, PRICE_MAX].
   Randomness comes only from the ``rng`` argument.

2. **PRICE-1/2 and HALT-3** (:func:`price_series`): rolling lower medians with min-fill over the
   quote tags (``vkernels.RollingMedian``, the wavelet-matrix fast path), with an exact scalar
   recomputation at every height whose PIN-1 key set is non-empty (state.cpp:1199 skips the quote
   tags of the keys pinned at H in all three windows), then ``xMint = min``, ``xClaim = max``,
   HALT-3 (falls only, fact 1.5-6) and NO_PRICE.

Plus the G1 analytics: :func:`attack_success_prob` / :func:`min_attack_share` (exact binomial over
the window, V16) and :func:`attack_effect` (simulated, common random numbers).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Literal

import numpy as np

from ybcal.model import vkernels as V
from ybcal.units import BLOCKS_PER_DAY, BLOCKS_PER_HOUR, BPS, PRICE_MAX, PRICE_MIN

if TYPE_CHECKING:  # pragma: no cover
    from ybcal.sim.engine import BlockInputs

OWNER_WP = "WP-3"

UNDEF = V.UNDEF
OutageMode = Literal["signal", "untagged", "stale"]
AttackMode = Literal["bias", "withhold", "untagged"]

#: Pool ids are bit positions of the PIN-1 key bitmask (uint64).
MAX_POOLS = 64


# ---------------------------------------------------------------------------------------------------
# Configuration


@dataclass(frozen=True)
class Pool:
    """One mining pool's tagging behaviour."""

    share: float  # hash share (fraction of blocks it mines)
    tags: bool = True  # writes a Yellowback coinbase tag at all
    quotes: bool = True  # the tag carries a price (else signal-only, TAG-3)
    signals: bool = True  # sets the signal bit (ACT-1)
    twap_blocks: int = 12  # quote = TWAP of the true price over this many blocks (15 min)
    noise_bps: float = 30.0  # per-quote Gaussian noise, s.d. in bps
    bias_bps: float = 0.0  # persistent bias of this pool's source, bps
    refresh_blocks: int = 1  # quote refreshed every k blocks (k > 1 = stale feed)
    outage_rate_per_day: float = 0.0  # expected outage starts per day
    outage_mean_hours: float = 0.0  # mean outage length (exponential)
    outage_mode: OutageMode = "signal"
    name: str = ""


@dataclass(frozen=True)
class Attack:
    """A coalition of pools acting together during ``[start, end)`` (block offsets from column 0)."""

    pools: tuple[int, ...]
    bias_bps: float = 0.0  # mode "bias": quote × (1 + bias/10^4) (negative = push down)
    start: int = 0
    end: int = 2**62
    mode: AttackMode = "bias"


@dataclass(frozen=True)
class OracleConfig:
    """Pools (index = pool id = PIN-1 key) and attacks. ``1 − Σ share`` is mined by stock, untagged
    miners."""

    pools: tuple[Pool, ...]
    attacks: tuple[Attack, ...] = ()
    meta: dict = field(default_factory=dict, compare=False, hash=False)

    def __post_init__(self) -> None:
        if len(self.pools) > MAX_POOLS:
            raise ValueError(f"at most {MAX_POOLS} pools (PIN-1 keys are bits of a uint64)")
        tot = sum(p.share for p in self.pools)
        if tot > 1 + 1e-9 or any(p.share < 0 for p in self.pools):
            raise ValueError(f"pool shares must be ≥ 0 and sum to ≤ 1 (got {tot})")

    @property
    def tagging_share(self) -> float:
        return float(sum(p.share for p in self.pools if p.tags))

    @property
    def quoting_share(self) -> float:
        return float(sum(p.share for p in self.pools if p.tags and p.quotes))

    @property
    def signalling_share(self) -> float:
        return float(sum(p.share for p in self.pools if p.tags and p.signals))

    @classmethod
    def honest(cls, n_pools: int = 6, tagging_share: float = 0.80, **pool_kw) -> OracleConfig:
        """``n_pools`` equal honest pools sharing ``tagging_share`` of the hash (the rest stock)."""
        s = tagging_share / n_pools
        return cls(tuple(Pool(share=s, name=f"pool{i}", **pool_kw) for i in range(n_pools)))

    @classmethod
    def from_policy(cls, policy, **pool_kw) -> OracleConfig:
        """Pool count and enforcing share from the policy (``expected_pool_count``,
        ``expected_enforcing_share``)."""
        return cls.honest(int(policy.expected_pool_count), float(policy.expected_enforcing_share), **pool_kw)

    def with_coalition(
        self,
        share: float,
        bias_bps: float = 0.0,
        *,
        start: int = 0,
        end: int = 2**62,
        mode: AttackMode = "bias",
        **pool_kw,
    ) -> OracleConfig:
        """Add an attacker pool of hash ``share`` carved proportionally out of the existing pools
        (the stock share is unchanged) and an :class:`Attack` by it."""
        tot = sum(p.share for p in self.pools)
        if not 0 < share < tot:
            raise ValueError("coalition share must be in (0, tagging share)")
        k = (tot - share) / tot
        pools = [replace(p, share=p.share * k) for p in self.pools]
        base = self.pools[0] if self.pools else Pool(share=0.0)
        kw = dict(twap_blocks=base.twap_blocks, noise_bps=base.noise_bps)
        kw.update(pool_kw)
        pools.append(Pool(share=share, name="attacker", **kw))
        att = Attack(pools=(len(pools) - 1,), bias_bps=bias_bps, start=start, end=end, mode=mode)
        return OracleConfig(tuple(pools), (*self.attacks, att), dict(self.meta))

    def with_attacks(self, attacks: Sequence[Attack]) -> OracleConfig:
        return OracleConfig(self.pools, tuple(attacks), dict(self.meta))


# ---------------------------------------------------------------------------------------------------
# Generation


def _twap(cs: np.ndarray, t: np.ndarray, lag: int) -> np.ndarray:
    """Integer TWAP of the true price over ``[t − lag + 1, t]`` (truncated at column 0) from the
    cumulative sum ``cs`` (``(P, n+1)``); ``t`` is ``(P, n)`` column indices."""
    lag = max(int(lag), 1)
    lo = np.maximum(t - lag + 1, 0)
    rows = np.arange(cs.shape[0])[:, None]
    return (cs[rows, t + 1] - cs[rows, lo]) // (t + 1 - lo)


def _outage_mask(rng: np.random.Generator, P: int, n: int, rate_per_day: float, mean_hours: float):
    """(in_outage, outage_start_index) per block for one pool: Poisson starts, exponential lengths."""
    if rate_per_day <= 0 or mean_hours <= 0:
        return None, None
    lam = rate_per_day * n / BLOCKS_PER_DAY
    counts = rng.poisson(lam, size=P)
    tot = int(counts.sum())
    if tot == 0:
        return None, None
    rows = np.repeat(np.arange(P), counts)
    starts = rng.integers(0, n, size=tot)
    lens = np.ceil(rng.exponential(mean_hours * BLOCKS_PER_HOUR, size=tot)).astype(np.int64)
    ends = np.minimum(starts + np.maximum(lens, 1), n)
    diff = np.zeros((P, n + 1), dtype=np.int32)
    np.add.at(diff, (rows, starts), 1)
    np.add.at(diff, (rows, ends), -1)
    inside = np.cumsum(diff[:, :n], axis=1) > 0
    edge = inside.copy()
    edge[:, 1:] &= ~inside[:, :-1]
    idx = np.where(edge, np.arange(n)[None, :], -1)
    begin = np.maximum.accumulate(idx, axis=1)
    return inside, begin


def generate_block_inputs(
    true_price,
    config: OracleConfig,
    *,
    rng: np.random.Generator,
    start_height: int = 0,
    attest: dict | None = None,
    meta: dict | None = None,
    enforce_until: int = 0,
    miner: np.ndarray | None = None,
) -> BlockInputs:
    """Tag stream for every block of every path (see the module docstring). Returns
    :class:`ybcal.sim.engine.BlockInputs`.

    ``miner`` (optional, ``(P, n)`` or ``(n,)`` ints) fixes who mined each block instead of drawing it
    from the pool shares: pool index ``k`` (``0 … K−1``), anything else = a stock, untagged miner. It
    replays a real block-by-block miner sequence (the pool-share log), so the day-to-day swings of
    real shares reach the medians' fill. The uniforms are still drawn (common random numbers).

    Draw order (fixed, so two configs with the same pools give common random numbers): miner
    uniforms, then per pool in index order its outage process and (if stale) its own noise, then the
    shared per-block noise.

    ``enforce_until`` > 0 models MINER-1 at the sunset: miners stop setting the signal bit for
    ``H > enforceUntilHeight`` (index.cpp:713, ``activation.sunset_signal_mask``; fact 1.5-4).
    """
    from ybcal.sim.engine import BlockInputs

    tp = np.asarray(true_price, dtype=np.int64)
    if tp.ndim == 1:
        tp = tp[None, :]
    P, n = tp.shape
    pools = config.pools
    K = len(pools)
    shares = np.array([p.share for p in pools], dtype=np.float64)
    cum = np.cumsum(shares) if K else np.zeros(0)
    u = rng.random((P, n))
    if miner is None:
        miner = np.searchsorted(cum, u, side="right").astype(np.int16)  # K = stock miner
    else:
        m = np.asarray(miner)
        m = np.broadcast_to(m if m.ndim == 2 else m[None, :], (P, n))
        miner = np.where((m >= 0) & (m < K), m, K).astype(np.int16)
    per_pool = []
    for p in pools:
        inside, begin = _outage_mask(rng, P, n, p.outage_rate_per_day, p.outage_mean_hours)
        stale = p.refresh_blocks > 1 or (inside is not None and p.outage_mode == "stale")
        z_own = rng.standard_normal((P, n)) if stale else None
        per_pool.append((inside, begin, z_own))
    z = rng.standard_normal((P, n))

    cs = np.zeros((P, n + 1), dtype=np.int64)
    np.cumsum(tp, axis=1, out=cs[:, 1:])
    cols = np.broadcast_to(np.arange(n, dtype=np.int64), (P, n))

    tag_present = np.zeros((P, n), dtype=bool)
    tag_price = np.zeros((P, n), dtype=np.int64)
    tag_pool = np.full((P, n), -1, dtype=np.int16)
    signal = np.zeros((P, n), dtype=bool)

    twap_cache: dict[int, np.ndarray] = {}  # pools quoting fresh share the TWAP at their own block
    for k, p in enumerate(pools):
        mine = miner == k
        if not p.tags or not mine.any():
            continue
        inside, begin, z_own = per_pool[k]
        present = mine.copy()
        quoting = mine & p.quotes
        t_eff = cols
        if p.refresh_blocks > 1:
            phase = k % p.refresh_blocks
            t_eff = np.maximum(cols - ((cols + phase) % p.refresh_blocks), 0)
        if inside is not None:
            out = inside & mine
            if p.outage_mode == "untagged":
                present &= ~out
                quoting &= ~out
            elif p.outage_mode == "signal":
                quoting &= ~out
            else:  # stale: frozen at the last quote before the outage
                t_eff = np.where(out, np.maximum(begin - 1, 0), t_eff)
        bias = np.full((P, n), float(p.bias_bps))
        withhold = np.zeros((P, n), dtype=bool)
        untag = np.zeros((P, n), dtype=bool)
        for a in config.attacks:
            if k not in a.pools:
                continue
            during = (cols >= a.start) & (cols < a.end)
            if a.mode == "bias":
                bias = np.where(during, bias + a.bias_bps, bias)
            elif a.mode == "withhold":
                withhold |= during
            else:
                untag |= during
        present &= ~untag
        quoting &= ~(untag | withhold)
        noise = z_own if z_own is not None else z
        zz = np.take_along_axis(noise, t_eff, axis=1) if z_own is not None else noise
        if t_eff is cols:
            if p.twap_blocks not in twap_cache:
                twap_cache[p.twap_blocks] = _twap(cs, cols, p.twap_blocks).astype(np.float64)
            base = twap_cache[p.twap_blocks]
        else:
            base = _twap(cs, t_eff, p.twap_blocks).astype(np.float64)
        q = np.rint(base * (1.0 + (bias + p.noise_bps * zz) / BPS))
        q = np.clip(q, PRICE_MIN, PRICE_MAX).astype(np.int64)
        tag_present |= present
        tag_pool[present] = k
        tag_price[quoting] = q[quoting]
        if p.signals:
            signal |= present
    if enforce_until > 0:
        from ybcal.sim.activation import sunset_signal_mask

        signal &= sunset_signal_mask(n, int(start_height), int(enforce_until))[None, :]
    return BlockInputs(
        true_price=tp,
        tag_present=tag_present,
        tag_price=tag_price,
        tag_pool=tag_pool,
        signal_bit=signal,
        start_height=int(start_height),
        attest=attest,
        meta={"oracle": config, **(meta or {})},
    )


# ---------------------------------------------------------------------------------------------------
# PRICE-1 / PRICE-2 / HALT-3


@dataclass
class PriceSeries:
    """The cross-section prices of every snapshot (``UNDEF`` = −1) and the price halts."""

    p_fast: np.ndarray
    p_mid: np.ndarray
    p_slow: np.ndarray
    x_mint: np.ndarray
    x_claim: np.ndarray
    halt3: np.ndarray  # HALT_DIVERGENCE (bool)
    no_price: np.ndarray  # HALT_NO_PRICE (bool)
    recomputed: int = 0  # heights recomputed exactly because of PIN-1 keys


def windows_and_fills(params: Mapping) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    return (
        (int(params["pFastWindow"]), int(params["pMidWindow"]), int(params["pSlowWindow"])),
        (int(params["pFastMinFill"]), int(params["pMidMinFill"]), int(params["pSlowMinFill"])),
    )


def _pinned_recompute(
    out: list[np.ndarray],
    prices: np.ndarray,
    valid: np.ndarray,
    pools: np.ndarray,
    pinned: np.ndarray,
    windows,
    fills,
) -> int:
    """Exact medians at the heights with pinned keys, skipping those keys' quote tags
    (``kernels.window_median_with_fill`` semantics: lower median, undefined below the fill)."""
    rows, cols = np.nonzero(pinned)
    for r, i in zip(rows.tolist(), cols.tolist(), strict=True):
        bits = int(pinned[r, i])
        keys = [k for k in range(MAX_POOLS) if bits >> k & 1]
        for w_i, (w, f) in enumerate(zip(windows, fills, strict=True)):
            lo = max(i - w + 1, 0)
            keep = valid[r, lo : i + 1] & ~np.isin(pools[r, lo : i + 1], keys)
            vals = prices[r, lo : i + 1][keep]
            c = vals.size
            if c < max(f, 1) or c == 0:
                out[w_i][r, i] = UNDEF
            else:
                m = (c - 1) // 2
                out[w_i][r, i] = np.partition(vals, m)[m]
    return int(rows.size)


def price_series(params: Mapping, tag_price, valid=None, *, tag_pool=None, pinned_pools=None) -> PriceSeries:
    """PRICE-1 (state.cpp:1197-1211) and HALT-1/HALT-3 (state.cpp:1235-1241) over a quote stream.

    ``tag_price``: ``(paths, n)`` int64 (``<= 0`` = no quote tag); ``valid`` optional bool mask;
    ``pinned_pools``: ``(paths, n)`` uint64 bitmask of the PIN-1 keys (pool ids, ``tag_pool``)
    pinned at each height — those heights are recomputed exactly without the pinned quotes.
    """
    tp = np.asarray(tag_price, dtype=np.int64)
    if tp.ndim == 1:
        tp = tp[None, :]
    ok = tp > 0 if valid is None else (np.asarray(valid, dtype=bool).reshape(tp.shape) & (tp > 0))
    windows, fills = windows_and_fills(params)
    rm = V.RollingMedian(tp, ok)
    meds = [rm.median(w, f) for w, f in zip(windows, fills, strict=True)]
    rec = 0
    if pinned_pools is not None:
        pin = np.asarray(pinned_pools, dtype=np.uint64).reshape(tp.shape)
        if pin.any():
            if tag_pool is None:
                raise ValueError("pinned_pools needs tag_pool")
            rec = _pinned_recompute(meds, tp, ok, np.asarray(tag_pool).reshape(tp.shape), pin, windows, fills)
    pf, pm, ps = meds
    xm = V.price_mint(pf, pm, ps)
    xc = V.price_claim(pm, ps)
    h3 = V.halt3_divergence(pf, pm, ps, int(params["divergenceBps"]))
    return PriceSeries(pf, pm, ps, xm, xc, h3, xm == UNDEF, rec)


# ---------------------------------------------------------------------------------------------------
# G1 analytics


def min_attack_quotes(honest_quotes: int, fill: int, direction: Literal["up", "down"] = "up") -> int:
    """V16, deterministic: the fewest attacker quotes in a window holding ``honest_quotes`` honest
    ones that make the lower median an attacker value (and reach the fill). Up needs ``A > H``
    (the lower median is element ``(k−1)//2``), down needs ``A ≥ H``."""
    need = honest_quotes + 1 if direction == "up" else max(honest_quotes, 1)
    return max(need, fill - honest_quotes)


def attack_success_prob(
    share: float,
    window: int,
    fill: int,
    *,
    honest_share: float | None = None,
    direction: Literal["up", "down"] = "up",
) -> float:
    """P(the median of one window is an attacker value) when every block is mined by the attacker
    with probability ``share`` (who always quotes), by an honest quoting pool with probability
    ``honest_share`` (default ``1 − share``: everyone else quotes), else by an untagged miner.
    Exact: Σ_a Bin(a; W, s) · P(H ∈ [max(fill − a, 0), a − 1 (up) | a (down)]),
    ``H ~ Bin(W − a, h / (1 − s))``."""
    from scipy.stats import binom

    s = float(share)
    h = (1.0 - s) if honest_share is None else float(honest_share)
    if s <= 0:
        return 0.0
    if s >= 1:
        return 1.0 if window >= fill else 0.0
    ph = min(max(h / (1.0 - s), 0.0), 1.0)
    a = np.arange(window + 1)
    pa = binom.pmf(a, window, s)
    hi = a - 1 if direction == "up" else a
    lo = np.maximum(fill - a, 0)
    m = window - a
    hi = np.minimum(hi, m)
    ok = hi >= lo
    cdf_hi = binom.cdf(hi, m, ph)
    cdf_lo = np.where(lo > 0, binom.cdf(lo - 1, m, ph), 0.0)
    return float(np.sum(np.where(ok, pa * (cdf_hi - cdf_lo), 0.0)))


def min_attack_share(
    window: int,
    fill: int,
    *,
    honest_share: float | None = None,
    direction: Literal["up", "down"] = "up",
    confidence: float = 0.5,
    tol: float = 1e-4,
) -> float:
    """Smallest attacker hash share whose per-block success probability (:func:`attack_success_prob`)
    reaches ``confidence``. With everyone else quoting this tends to 0.5 as W grows (V16: the
    attacker needs more than half of the window's quotes); a sparse honest quote rate lowers it;
    with ``honest_share`` fixed the search is over ``(0, 1 − honest_share]``. Returns ``nan`` if no
    share works."""
    hi = 1.0 - (honest_share or 0.0) if honest_share is not None else 1.0
    hi = min(hi, 1.0 - 1e-12)

    def f(s: float) -> float:
        return attack_success_prob(s, window, fill, honest_share=honest_share, direction=direction)

    if f(hi) < confidence:
        return float("nan")
    lo = 0.0
    while hi - lo > tol:
        mid = (lo + hi) / 2
        if f(mid) >= confidence:
            hi = mid
        else:
            lo = mid
    return hi


@dataclass(frozen=True)
class AttackEffect:
    """Effect of a coalition on the medians, measured against the same paths without the attack."""

    share: float
    bias_bps: float
    max_dev_bps: dict  # per series: max |attacked − clean| / clean over the attack (bps)
    p50_dev_bps: dict
    moved_fraction: dict  # fraction of attack blocks with |dev| ≥ |bias| / 2
    extra_halt3_blocks: float  # mean per path
    extra_no_price_blocks: float
    blocks: int


def attack_effect(
    share: float,
    bias_bps: float,
    params: Mapping,
    *,
    true_price=None,
    n_blocks: int | None = None,
    start: int | None = None,
    duration: int | None = None,
    base: OracleConfig | None = None,
    paths: int = 8,
    seed: int = 0,
    mode: AttackMode = "bias",
) -> AttackEffect:
    """Simulate a coalition of hash ``share`` biasing its quotes by ``bias_bps`` for ``duration``
    blocks and measure the medians' deviation from the clean run (common random numbers: the clean
    run has the same pools and draws, with the attack switched off)."""
    windows, _ = windows_and_fills(params)
    if true_price is None:
        n = n_blocks or 4 * max(windows)
        true_price = np.full((paths, n), 1_000_000, dtype=np.int64)  # $1, flat
    tp = np.asarray(true_price, dtype=np.int64)
    if tp.ndim == 1:
        tp = np.broadcast_to(tp, (paths, tp.size)).copy()
    n = tp.shape[1]
    st = max(windows) if start is None else start
    du = (n - st) if duration is None else duration
    cfg = (base or OracleConfig.honest()).with_coalition(share, bias_bps, start=st, end=st + du, mode=mode)
    clean_cfg = cfg.with_attacks(())
    runs = []
    for c in (clean_cfg, cfg):
        inp = generate_block_inputs(tp, c, rng=np.random.default_rng(seed))
        runs.append(price_series(params, inp.tag_price, inp.tag_present))
    clean, att = runs
    sl = slice(st, min(st + du, n))
    out_max, out_p50, moved = {}, {}, {}
    for name in ("p_fast", "p_mid", "p_slow", "x_mint", "x_claim"):
        a = getattr(att, name)[:, sl].astype(np.float64)
        c = getattr(clean, name)[:, sl].astype(np.float64)
        ok = (a > 0) & (c > 0)
        dev = np.where(ok, (a - c) / np.where(ok, c, 1) * BPS, 0.0)
        ad = np.abs(dev[ok]) if ok.any() else np.array([0.0])
        out_max[name] = float(ad.max())
        out_p50[name] = float(np.median(ad))
        moved[name] = float((np.abs(dev) >= abs(bias_bps) / 2)[ok].mean()) if ok.any() else 0.0
    extra_h3 = float((att.halt3[:, sl] & ~clean.halt3[:, sl]).sum() / tp.shape[0])
    extra_np = float((att.no_price[:, sl] & ~clean.no_price[:, sl]).sum() / tp.shape[0])
    return AttackEffect(share, bias_bps, out_max, out_p50, moved, extra_h3, extra_np, sl.stop - sl.start)


def no_price_hours_per_year(no_price, *, skip: int = 0) -> float:
    """Mean NO_PRICE hours per year over the paths (after ``skip`` warm-up blocks)."""
    a = np.asarray(no_price, dtype=bool)[..., skip:]
    return float(a.mean() * 420_480 / BLOCKS_PER_HOUR) if a.size else float("nan")


def tracking_lag_blocks(true_price, p, change_index: int, *, frac: float = 0.5) -> np.ndarray:
    """Blocks after ``change_index`` until ``p`` covers ``frac`` of a step in the true price
    (from the value at ``change_index − 1`` to the final true value), per path; ``nan`` if never."""
    t = np.asarray(true_price, dtype=np.float64).reshape(-1, np.shape(true_price)[-1])
    q = np.asarray(p, dtype=np.float64).reshape(t.shape)
    before, after = t[:, change_index - 1], t[:, -1]
    target = before + frac * (after - before)
    out = np.full(t.shape[0], np.nan)
    for r in range(t.shape[0]):
        seg = q[r, change_index:]
        okv = seg > 0
        hit = okv & ((seg >= target[r]) if after[r] >= before[r] else (seg <= target[r]))
        if hit.any():
            out[r] = float(hit.argmax())
    return out

"""Minter, owner, absentee, claimant/liquidator, YED-holder market and attacker personas (owner: WP-4).

Personas decide *what* transaction is built and *when*; the vault book (:mod:`ybcal.sim.vaults`)
decides what the rules make of it, with the exact integer rule functions. Every random draw comes
from the ``numpy.random.Generator`` the caller passes (``Env.rng_for(...)`` in a study), so a run is
a pure function of its seed (tested).

Personas (PLAN §5.3, §5.4, §5.6, fact 1.5-5):

* **Minter** — Poisson arrivals (``mints_per_day``), a size distribution between ``minMint`` and
  ``maxMint`` (log-uniform by default), a class mix and a term distribution inside the class
  (``Policy.term_distribution``), a ratio buffer over the minimum, and the refHeight choice:
  ``"adversarial"`` picks, among the snapshots in ``[H − refWindow, H − 1]`` whose MINT-4 state
  admits the mint, the one with the **highest** pMint (least collateral, fact 1.5-5);
  ``"wallet"`` uses the wallet default ``R = tip − REF_LAG`` (``DEFAULT_REF_LAG``).
* **Owner** — redeems as soon as the owner path is open (``H > lockHeight``) and the vault is worth
  redeeming at the true price (collateral net of fees ≥ the cost of buying the debt in YED);
  absence spells are a Poisson process (``owner_absence_rate_per_year``) with log-normal lengths
  (``owner_absence_median_days``, ``owner_absence_sigma``); an owner absent at lockHeight acts when
  the covering spells end. The **absentee** persona (``lost_key_prob``) never returns. The
  **defector** persona (``defector_share``) sweeps the vault without burning whenever enforcement is
  off (ACT-5 false: before activation, under the ENFORCEMENT halt, after the sunset); honest owners
  sweep only under the abandonment predicate (index.cpp:732-744, ``yed_sweep``).
* **Claimant / liquidator** — rational: claims at the first height the claim path is open and valid
  (RED-4(a), or RED-4(b) with a persisted notice when ARMED) **and** the profit — collateral it
  receives, net of FEE-1/AFEE-1 and the network fee, sold at the true price less slippage, minus
  the cost of the YED it burns — is at least ``min_profit_bps`` of the debt. It picks the refHeight
  in the window that makes the vault underwater / maximises its payout (fact 1.5-5). A **thief**
  (``thief_when_unenforced``) takes the claim path without burning when enforcement is off.
* **YED market** — anyone who burns YED buys it at ``$1 · (1 + premium_bps/10^4)``.
* **Attacker** — an oracle coalition (:class:`AttackerConfig`) plugged into WP-3's
  :class:`ybcal.sim.oracle.OracleConfig` (block mode) or applied to hour-mode medians.

Floats appear only in behaviour (probabilities, USD valuations); every amount handed to a rule is an
integer.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Literal

import numpy as np

from ybcal.units import BLOCKS_PER_DAY, BLOCKS_PER_YEAR, BPS, COIN

OWNER_WP = "WP-4"

#: Owner personas.
HONEST, DEFECTOR, LOST = 0, 1, 2
OWNER_KIND_NAMES = {HONEST: "honest", DEFECTOR: "defector", LOST: "lost"}
#: "Never" for a block delay (an absentee never returns).
NEVER = np.iinfo(np.int64).max // 4

TermDistribution = Literal["uniform", "short-heavy", "long-heavy"]
RefChoice = Literal["adversarial", "wallet"]


# ---------------------------------------------------------------------------------------------------
# Configuration


@dataclass(frozen=True)
class MinterConfig:
    """Mint demand. Sizes are cents; ``None`` bounds default to ``minMint`` / ``maxMint``."""

    mints_per_day: float = 2.0
    size_dist: Literal["loguniform", "uniform", "fixed"] = "loguniform"
    size_lo_cents: int | None = None
    size_hi_cents: int | None = None
    fixed_cents: int | None = None
    class_weights: tuple[float, float, float] = (0.4, 0.4, 0.2)
    term_distribution: TermDistribution = "uniform"
    buffer_bps_lo: int = 0  #: collateral buffer over the MINT-5 minimum, uniform in [lo, hi] bps
    buffer_bps_hi: int = 0
    ref_choice: RefChoice = "adversarial"
    ref_lag: int | None = None  #: wallet REF_LAG (default: the set's ``DEFAULT_REF_LAG``)
    start_after_blocks: int = 0  #: no attempt before this many blocks after the timeline start


@dataclass(frozen=True)
class OwnerConfig:
    """Owner behaviour and the absence model (``policy/default.toml`` [owner_absence])."""

    absence_rate_per_year: float = 1.0
    absence_median_days: float = 7.0
    absence_sigma: float = 1.0
    lost_key_prob: float = 0.0  #: absentee persona: never returns
    defector_share: float = 0.0  #: sweeps without burning whenever enforcement is off
    sweep_on_abandon: bool = True  #: honest owners sweep under the abandonment predicate
    redeem_slippage_bps: int = 0  #: haircut on the collateral the owner gets back (USD valuation)


@dataclass(frozen=True)
class ClaimantConfig:
    """Rational claimant / liquidator."""

    enabled: bool = True
    min_profit_bps: int = 200  #: ``Policy.claimant_min_profit_bps`` (of the debt)
    slippage_bps: int = 100  #: base liquidation slippage
    depth_usd: float | None = None  #: ±2 % order-book depth; adds impact proportional to size
    impact_bps_at_depth: float = 200.0  #: extra slippage when selling one full ``depth_usd``
    use_emergency: bool = True  #: post NOT-1 notices and use RED-4(b) when ARMED
    thief_when_unenforced: bool = True  #: claim path without burn when ACT-5 is off


@dataclass(frozen=True)
class YedMarket:
    """The YED an owner or claimant burns is bought at ``$1 · (1 + premium_bps / 10^4)``."""

    premium_bps: int = 0

    @property
    def yed_price_usd(self) -> float:
        return 1.0 + self.premium_bps / BPS


@dataclass(frozen=True)
class AttackerConfig:
    """An oracle coalition (WP-3 :class:`~ybcal.sim.oracle.Attack`): ``share`` of the hash biases
    its quotes by ``bias_bps`` during ``[start_block, end_block)`` (offsets from the run start)."""

    share: float
    bias_bps: float
    start_block: int = 0
    end_block: int = 2**62
    mode: Literal["bias", "withhold", "untagged"] = "bias"

    def oracle_config(self, base):
        """``base.with_coalition(...)`` (block mode)."""
        return base.with_coalition(
            self.share, self.bias_bps, start=self.start_block, end=self.end_block, mode=self.mode
        )

    def moves_median(self, quoting_share: float) -> bool:
        """Hour-mode approximation: a coalition moves a lower median once it holds more than half
        of the quoting hash (V16 at the fill limit; WP-3's ``attack_success_prob`` is exact)."""
        return quoting_share > 0 and self.share / quoting_share > 0.5

    def apply_to_hours(self, p: np.ndarray, quoting_share: float, step_blocks: int = 48) -> np.ndarray:
        """Bias an hourly median series in the attack window when :meth:`moves_median`."""
        if self.mode != "bias" or not self.moves_median(quoting_share):
            return p
        out = np.array(p, dtype=np.int64, copy=True)
        n = out.shape[-1]
        a = max(0, self.start_block // step_blocks)
        b = min(n, -(-min(self.end_block, n * step_blocks) // step_blocks))
        seg = out[..., a:b]
        ok = seg > 0
        seg[ok] = np.maximum(1, np.rint(seg[ok] * (1 + self.bias_bps / BPS))).astype(np.int64)
        return out


@dataclass(frozen=True)
class AgentsConfig:
    """Every persona of one run."""

    minter: MinterConfig = field(default_factory=MinterConfig)
    owner: OwnerConfig = field(default_factory=OwnerConfig)
    claimant: ClaimantConfig = field(default_factory=ClaimantConfig)
    market: YedMarket = field(default_factory=YedMarket)
    attacker: AttackerConfig | None = None
    tx_fee_zat: int = 1_000  #: the network fee the wallet adds (YELLOWBACK_FEE)

    @classmethod
    def from_policy(cls, policy, *, adoption: str | None = None, **changes) -> AgentsConfig:
        """Personas from the owner's policy: arrival rate from ``adoption_scenarios[adoption or
        adoption_case]``, term distribution, absence model, claimant profit threshold."""
        case = adoption or policy.adoption_case
        scen = policy.adoption_scenarios.get(case, {})
        minter = MinterConfig(
            mints_per_day=float(scen.get("mints_per_day", 2.0)),
            term_distribution=policy.term_distribution,  # type: ignore[arg-type]
        )
        owner = OwnerConfig(
            absence_rate_per_year=float(policy.owner_absence_rate_per_year),
            absence_median_days=float(policy.owner_absence_median_days),
            absence_sigma=float(policy.owner_absence_sigma),
        )
        claimant = ClaimantConfig(min_profit_bps=int(policy.claimant_min_profit_bps))
        cfg = cls(minter=minter, owner=owner, claimant=claimant)
        return replace(cfg, **changes) if changes else cfg

    def replace(self, **changes) -> AgentsConfig:
        return replace(self, **changes)


# ---------------------------------------------------------------------------------------------------
# Minter


@dataclass
class MintAttempts:
    """One path's mint attempts in step order (all int64 / int8 arrays of equal length)."""

    step: np.ndarray  #: timeline step of the attempt (confirmation step)
    cents: np.ndarray
    term_class: np.ndarray
    lock_blocks: np.ndarray
    buffer_bps: np.ndarray
    owner_kind: np.ndarray
    owner_delay_blocks: np.ndarray  #: extra blocks after lockHeight before the owner can act

    def __len__(self) -> int:
        return int(self.step.shape[0])

    def take(self, idx) -> MintAttempts:
        return MintAttempts(*(getattr(self, f)[idx] for f in self.__dataclass_fields__))

    @classmethod
    def from_rows(cls, rows) -> MintAttempts:
        """From tuples ``(step, cents, class, lock_blocks[, buffer_bps, owner_kind, delay])``."""
        cols = list(zip(*rows, strict=True)) if rows else [[] for _ in range(7)]
        n = len(cols[0])
        full = [np.asarray(c, dtype=np.int64) for c in cols]
        while len(full) < 7:
            full.append(np.zeros(n, np.int64))
        a = cls(*full)
        a.term_class = a.term_class.astype(np.int8)
        a.owner_kind = a.owner_kind.astype(np.int8)
        order = np.argsort(a.step, kind="stable")
        return a.take(order)


def class_bounds(params: Mapping) -> tuple[tuple[int, int], ...]:
    return tuple((int(params[f"classMin[{i}]"]), int(params[f"classMax[{i}]"])) for i in range(3))


def sample_lock_blocks(
    rng: np.random.Generator,
    params: Mapping,
    term_class: np.ndarray,
    distribution: TermDistribution = "uniform",
) -> np.ndarray:
    """Lock lengths inside each attempt's class range: uniform, ``short-heavy`` (Beta(1, 3)) or
    ``long-heavy`` (Beta(3, 1)) on ``[classMin, classMax]`` (inclusive integers)."""
    tc = np.asarray(term_class, dtype=np.int64)
    b = np.array(class_bounds(params), dtype=np.int64)
    lo, hi = b[tc, 0], b[tc, 1]
    if distribution == "uniform":
        u = rng.random(tc.shape)
    elif distribution == "short-heavy":
        u = rng.beta(1.0, 3.0, tc.shape)
    elif distribution == "long-heavy":
        u = rng.beta(3.0, 1.0, tc.shape)
    else:
        raise ValueError(f"unknown term distribution {distribution!r}")
    return np.minimum(hi, lo + np.floor(u * (hi - lo + 1)).astype(np.int64))


def term_grid(
    params: Mapping, term_class: int, n: int = 16, distribution: TermDistribution = "uniform"
) -> tuple[np.ndarray, np.ndarray]:
    """``n`` quantile points of the within-class term distribution and their (equal) weights —
    the deterministic counterpart of :func:`sample_lock_blocks` for the fast bad-debt path."""
    lo, hi = class_bounds(params)[term_class]
    q = (np.arange(n) + 0.5) / n
    if distribution == "short-heavy":
        u = 1 - (1 - q) ** (1 / 3)
    elif distribution == "long-heavy":
        u = q ** (1 / 3)
    else:
        u = q
    terms = np.minimum(hi, lo + np.floor(u * (hi - lo + 1)).astype(np.int64))
    return terms, np.full(n, 1.0 / n)


def sample_sizes(rng: np.random.Generator, params: Mapping, cfg: MinterConfig, n: int) -> np.ndarray:
    """Mint sizes in cents inside ``[minMint, maxMint]`` (MINT-2 bounds) unless the config asks
    for out-of-range sizes explicitly."""
    lo = int(cfg.size_lo_cents if cfg.size_lo_cents is not None else params["minMint"])
    hi = int(cfg.size_hi_cents if cfg.size_hi_cents is not None else params["maxMint"])
    if cfg.size_dist == "fixed":
        return np.full(n, int(cfg.fixed_cents if cfg.fixed_cents is not None else lo), dtype=np.int64)
    if cfg.size_dist == "uniform":
        return rng.integers(lo, hi + 1, n).astype(np.int64)
    if cfg.size_dist == "loguniform":
        x = np.exp(rng.uniform(math.log(lo), math.log(hi), n))
        return np.clip(np.rint(x), lo, hi).astype(np.int64)
    raise ValueError(f"unknown size distribution {cfg.size_dist!r}")


def sample_mint_attempts(
    rng: np.random.Generator,
    params: Mapping,
    agents: AgentsConfig,
    n_steps: int,
    step_blocks: int = 1,
    *,
    first_step: int = 1,
) -> MintAttempts:
    """Poisson mint arrivals over ``n_steps`` timeline steps of ``step_blocks`` blocks, with sizes,
    classes, terms, buffers and the owner persona of each (owner absence drawn here too, so a vault's
    fate is fixed by the seed whatever happens to other vaults)."""
    m = agents.minter
    first = max(int(first_step), -(-int(m.start_after_blocks) // max(1, step_blocks)))
    span = max(0, n_steps - first)
    lam = m.mints_per_day * step_blocks / BLOCKS_PER_DAY
    counts = rng.poisson(lam, span) if span else np.zeros(0, np.int64)
    step = first + np.repeat(np.arange(span, dtype=np.int64), counts)
    n = int(step.shape[0])
    w = np.asarray(m.class_weights, dtype=np.float64)
    tc = rng.choice(3, size=n, p=w / w.sum()).astype(np.int8)
    lock = sample_lock_blocks(rng, params, tc, m.term_distribution)
    cents = sample_sizes(rng, params, m, n)
    buf = rng.integers(m.buffer_bps_lo, m.buffer_bps_hi + 1, n).astype(np.int64)
    kind = owner_kinds(rng, n, agents.owner)
    delay = owner_return_delay_blocks(rng, n, agents.owner)
    delay = np.where(kind == LOST, NEVER, delay)
    return MintAttempts(step, cents, tc, lock, buf, kind, delay)


def adversarial_ref(p_mint: np.ndarray, ok: np.ndarray) -> int:
    """Fact 1.5-5 for one mint: the index of the admissible snapshot with the highest pMint in the
    candidate list (``-1`` if none); ties go to the first candidate (the book lists candidates
    newest first)."""
    pm = np.where(np.asarray(ok, dtype=bool), np.asarray(p_mint, dtype=np.int64), -1)
    if pm.size == 0 or pm.max() <= 0:
        return -1
    return int(np.argmax(pm))


def ref_preference(p_mint: np.ndarray, ok: np.ndarray, *, adversarial: bool = True) -> np.ndarray:
    """Candidate order for each row of a ``(m, W)`` candidate matrix (column 0 = newest): admissible
    snapshots by descending pMint (stable, so ties keep newest first), then the rest."""
    pm = np.where(ok, p_mint, -1)
    if not adversarial:
        return np.broadcast_to(np.arange(pm.shape[-1]), pm.shape).copy()
    return np.argsort(-pm, axis=-1, kind="stable")


# ---------------------------------------------------------------------------------------------------
# Owner absence


def owner_kinds(rng: np.random.Generator, n: int, cfg: OwnerConfig) -> np.ndarray:
    """HONEST / DEFECTOR / LOST per vault (lost first, then defector among the rest)."""
    u = rng.random(n)
    kind = np.full(n, HONEST, dtype=np.int8)
    kind[u < cfg.lost_key_prob + cfg.defector_share] = DEFECTOR
    kind[u < cfg.lost_key_prob] = LOST
    return kind


def _lognorm_mu(cfg: OwnerConfig) -> float:
    return math.log(max(cfg.absence_median_days, 1e-9))


def owner_return_delay_blocks(
    rng: np.random.Generator, n: int, cfg: OwnerConfig, *, lookback_days: float | None = None
) -> np.ndarray:
    """Blocks after lockHeight until the owner is present: absence spells start as a Poisson
    process (rate ``absence_rate_per_year``) in a lookback before lockHeight, last log-normal
    (median ``absence_median_days``, shape ``absence_sigma``); the owner returns at the first
    instant at or after lockHeight that no spell covers (overlapping spells chain). 0 = present."""
    if n == 0 or cfg.absence_rate_per_year <= 0:
        return np.zeros(n, dtype=np.int64)
    mu, s = _lognorm_mu(cfg), max(cfg.absence_sigma, 0.0)
    if lookback_days is None:
        lookback_days = math.exp(mu + 4.0 * s) * 1.5  # beyond the 99.997 % duration quantile
    lam = cfg.absence_rate_per_year * lookback_days / 365.0
    k = rng.poisson(lam, n)
    tot = int(k.sum())
    out = np.zeros(n, dtype=np.int64)
    if tot == 0:
        return out
    owner = np.repeat(np.arange(n), k)
    start = -rng.uniform(0.0, lookback_days, tot)  # days relative to lockHeight
    dur = np.exp(mu + s * rng.standard_normal(tot))
    end = start + dur
    order = np.lexsort((start, owner))
    owner, start, end = owner[order], start[order], end[order]
    bounds = np.searchsorted(owner, np.arange(n + 1))
    for i in np.nonzero(k)[0].tolist():
        a, b = bounds[i], bounds[i + 1]
        t = 0.0  # the owner's earliest presence candidate (days after lock)
        for st, en in zip(start[a:b].tolist(), end[a:b].tolist(), strict=True):
            if st <= t < en:
                t = en
            elif st > t:
                break
        out[i] = math.ceil(t * BLOCKS_PER_DAY)
    return out


def p_owner_miss(grace_blocks: int, cfg: OwnerConfig) -> float:
    """Analytic P(an honest owner is absent for the whole window ``[lockHeight, lockHeight +
    grace]``): spells covering the window form a thinned Poisson process with mean
    ``λ · E[(D − G)^+]`` (D log-normal), so ``P = 1 − exp(−λ E[(D − G)^+])`` — the Black–Scholes
    call formula. Ignores chains of overlapping spells (second order at λ·E[D] ≪ 1); the simulated
    :func:`owner_return_delay_blocks` includes them. Lost keys add ``lost_key_prob``."""
    g = grace_blocks / BLOCKS_PER_DAY
    lam = cfg.absence_rate_per_year / 365.0
    mu, s = _lognorm_mu(cfg), max(cfg.absence_sigma, 1e-12)
    if g <= 0:
        e = math.exp(mu + s * s / 2)
    else:
        d2 = (mu - math.log(g)) / s
        e = math.exp(mu + s * s / 2) * _ncdf(d2 + s) - g * _ncdf(d2)
    p = 1.0 - math.exp(-lam * max(e, 0.0))
    return cfg.lost_key_prob + (1 - cfg.lost_key_prob) * p


def _ncdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


# ---------------------------------------------------------------------------------------------------
# Claimant / owner economics (floats: behaviour, not rules)


def slippage_bps(value_usd, cfg: ClaimantConfig) -> np.ndarray:
    """Liquidation slippage: base ``slippage_bps`` plus ``impact_bps_at_depth · value / depth``."""
    v = np.asarray(value_usd, dtype=np.float64)
    s = np.full(v.shape, float(cfg.slippage_bps))
    if cfg.depth_usd:
        s = s + cfg.impact_bps_at_depth * v / float(cfg.depth_usd)
    return np.minimum(s, 9_999.0)


def zat_value_usd(zat, price_microusd) -> np.ndarray:
    """USD value of ``zat`` at ``price`` µUSD/YEC (float)."""
    return np.asarray(zat, dtype=np.float64) / COIN * np.asarray(price_microusd, dtype=np.float64) / 1e6


def claim_profit_usd(
    receive_zat, cost_zat, price_microusd, debt_cents, cfg: ClaimantConfig, market: YedMarket
) -> np.ndarray:
    """Claimant profit in USD: ``(receive − cost)`` YEC sold at ``price`` less slippage, minus the
    YED it burns (``debt_cents`` bought at the market price)."""
    net = np.asarray(receive_zat, dtype=np.float64) - np.asarray(cost_zat, dtype=np.float64)
    gross = zat_value_usd(net, price_microusd)
    slip = slippage_bps(np.maximum(gross, 0.0), cfg)
    return gross * (1 - slip / BPS) - np.asarray(debt_cents, dtype=np.float64) / 100 * market.yed_price_usd


def claim_price_floor(
    receive_zat, cost_zat, debt_cents, cfg: ClaimantConfig, market: YedMarket
) -> np.ndarray:
    """The smallest true price (µUSD/YEC, float) at which a claim receiving ``receive_zat`` clears
    ``min_profit_bps`` (base slippage only; the depth impact is applied when the claim happens).
    ``inf`` when the claimant receives nothing net."""
    net = (np.asarray(receive_zat, dtype=np.float64) - np.asarray(cost_zat, dtype=np.float64)) / COIN
    need = np.asarray(debt_cents, dtype=np.float64) / 100 * (market.yed_price_usd + cfg.min_profit_bps / BPS)
    keep = 1 - min(float(cfg.slippage_bps), 9_999.0) / BPS
    with np.errstate(divide="ignore", invalid="ignore"):
        lvl = np.where(net > 0, need / (net * keep) * 1e6, np.inf)
    return lvl


def redeem_price_floor(net_zat, debt_cents, cfg: OwnerConfig, market: YedMarket) -> np.ndarray:
    """The smallest true price (µUSD/YEC) at which redeeming is worth it for the owner: the
    collateral returned (net of FEE-1 and the network fee, less ``redeem_slippage_bps``) is worth at
    least the YED the owner must buy to burn (RED-2)."""
    net = np.asarray(net_zat, dtype=np.float64) / COIN
    need = np.asarray(debt_cents, dtype=np.float64) / 100 * market.yed_price_usd
    keep = 1 - cfg.redeem_slippage_bps / BPS
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(net > 0, need / (net * keep) * 1e6, np.inf)


def years_to_blocks(years: float) -> int:
    return round(years * BLOCKS_PER_YEAR)


__all__ = [
    "DEFECTOR",
    "HONEST",
    "LOST",
    "NEVER",
    "AgentsConfig",
    "AttackerConfig",
    "ClaimantConfig",
    "MintAttempts",
    "MinterConfig",
    "OwnerConfig",
    "YedMarket",
    "adversarial_ref",
    "claim_price_floor",
    "claim_profit_usd",
    "class_bounds",
    "owner_kinds",
    "owner_return_delay_blocks",
    "p_owner_miss",
    "redeem_price_floor",
    "ref_preference",
    "sample_lock_blocks",
    "sample_mint_attempts",
    "sample_sizes",
    "slippage_bps",
    "term_grid",
    "zat_value_usd",
]

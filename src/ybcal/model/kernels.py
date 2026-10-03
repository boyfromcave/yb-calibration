"""Exact scalar integer kernels of the Yellowback rules (owner: WP-1; PLAN §2.1).

Each function is the integer formula of ``src/yellowback/math.h`` (or the cited block of
``state.cpp`` / ``params.cpp``) at the pinned ycash6 commit ``7702d22``; citations are
``file:line`` at that commit. Where the vendored reference model (:mod:`ybcal.model.reference`,
:mod:`ybcal.model.reference_attest`) has the same function, the kernel delegates to it after
applying the C++ guards, so the two can never drift silently; where it does not (the model
computes PIN, ACT and REG-4 inline in ``YellowbackModel._snap`` / ``_judge``), the kernel is
transcribed from the C++ and tested against the model's state on the golden chain.

Conventions (math.h:19-24): Python integers only (no floats anywhere in this module), floor
division unless a ceiling is written, and **undefined is ``None``**. Prices are integer µUSD per
YEC, amounts zat, YED amounts cents, ratios basis points. A non-positive price is undefined, as in
the C++ (``pMint <= 0`` → nullopt). Predicates on an undefined input are ``False`` (totality,
spec §3.8, M1).

Totality differences between the C++ and the reference model are resolved in favour of the C++
(the node is the consensus); they are listed in ``docs/architecture.md`` ("Model and kernels").
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import NamedTuple

from ybcal.model import reference as _ref
from ybcal.model import reference_attest as _ref_attest

OWNER_WP = "WP-1"

COIN: int = _ref.COIN
MAX_MONEY: int = _ref.MAX_MONEY
BPS: int = _ref.BPS
INT64_MAX: int = (1 << 63) - 1

#: Activation.status (view.h declaration order; reference.SIGNALING/LOCKED_IN/ACTIVE).
SIGNALING, LOCKED_IN, ACTIVE = _ref.SIGNALING, _ref.LOCKED_IN, _ref.ACTIVE

Price = int | None


def _defined(p: Price) -> bool:
    return p is not None and p > 0


def _fits_int64(x: int) -> bool:
    """math.h:42 FitsInt64 (for the non-negative values it is applied to)."""
    return 0 <= x <= INT64_MAX


# ---------------------------------------------------------------------------------------------------
# PRICE-1 / PRICE-2 / HALT-3


def lower_median(values: Iterable[int]) -> int | None:
    """math.h:53 LowerMedian: element ``(n - 1) // 2`` of the ascending sort; ``None`` if empty."""
    return _ref.lower_median(list(values))


def window_median_with_fill(prices: Sequence[int], fill: int) -> int | None:
    """state.cpp:103-111 WindowMedian: the lower median of the quote prices in a window, undefined
    (``None``) when fewer than ``fill`` (``WINDOW_MIN_FILL``) quotes are present.

    ``prices`` are the quotes of the window ``(H - W, H]`` (heights ≥ ``startHeight``, pinned keys
    already removed), in any order.
    """
    if len(prices) < fill:
        return None
    return lower_median(prices)


def min_fill_fast(window: int) -> int:
    """``pFastMinFill`` = ceil(W / 2) (params.h WINDOW_MIN_FILL, L9; reference.Params.min_fill_fast)."""
    return -((-window) // 2)


def min_fill_slow(window: int) -> int:
    """``pMidMinFill`` / ``pSlowMinFill`` = ceil(2W / 3) (L9)."""
    return -((-2 * window) // 3)


def price_mint(p_fast: Price, p_mid: Price, p_slow: Price) -> int | None:
    """state.cpp:1205 PRICE-2: ``pMint = min(pFast, pMid, pSlow)``, undefined if any is."""
    if not (_defined(p_fast) and _defined(p_mid) and _defined(p_slow)):
        return None
    return min(p_fast, p_mid, p_slow)  # type: ignore[type-var]


def price_claim(p_mid: Price, p_slow: Price) -> int | None:
    """state.cpp:1206 PRICE-2: ``pClaim = max(pMid, pSlow)``, undefined if either is."""
    if not (_defined(p_mid) and _defined(p_slow)):
        return None
    return max(p_mid, p_slow)  # type: ignore[type-var]


def halt3_divergence(p_fast: Price, p_mid: Price, p_slow: Price, divergence_bps: int) -> bool:
    """state.cpp:1238-1241 HALT-3: with k = 10^4 − divergenceBps, set iff
    ``pFast·10^4 < k·pMid`` or ``pMid·10^4 < k·pSlow``. Fires only on a fall (fact 1.5-6);
    ``False`` unless all three medians are defined."""
    if not (_defined(p_fast) and _defined(p_mid) and _defined(p_slow)):
        return False
    k = BPS - divergence_bps
    return p_fast * BPS < k * p_mid or p_mid * BPS < k * p_slow  # type: ignore[operator]


class CombinedPrices(NamedTuple):
    """math.h:278-283: the ARMED per-transaction prices."""

    p_mint: int | None   #: min(xMint, aMint)
    p_claim: int | None  #: max(xClaim, aClaim)
    p_emerg: int | None  #: min(xClaim, aClaim)


def price_combine(x_mint: Price, x_claim: Price, a_mint: Price, a_claim: Price) -> CombinedPrices:
    """math.h:286-298 PriceCombine (PRICE-2 revised, ARMED): each output undefined iff one of its
    inputs is. Not ARMED, the caller reads ``x`` alone."""
    pm = min(x_mint, a_mint) if x_mint is not None and a_mint is not None else None
    if x_claim is not None and a_claim is not None:
        return CombinedPrices(pm, max(x_claim, a_claim), min(x_claim, a_claim))
    return CombinedPrices(pm, None, None)


# ---------------------------------------------------------------------------------------------------
# SIGMA-1


def isqrt(x: int) -> int:
    """math.h:61 IsqrtU256 (floor square root)."""
    return _ref.isqrt(x)


def sigma_mult_bps(samples: Sequence[Price], sigma_ref_bps: int, periods_per_year: int, max_bps: int) -> int:
    """math.h:80-102 SigmaMultBps (SIGMA-1, V17) over the pFast samples ``s_0 … s_n``
    (``s_0`` = the newest, ``s_k`` = pFast at ``H − k·volStep``).

    ``sigmaRefBps ≤ 0`` → 10^4 (regtest fixed multiplier); ``maxBps`` is raised to 10^4 if below;
    fewer than two samples, or any undefined / non-positive sample → ``maxBps`` (K12: a feed gap
    gives the cap, never 1×). Otherwise ``r_k = |s_k − s_{k+1}|·10^4 // s_{k+1}``,
    ``var = Σr_k² // n``, ``σ = isqrt(var · periodsPerYear)``,
    result = clamp(σ·10^4 // sigmaRefBps, 10^4, maxBps). Delegates the core to the reference.
    """
    if sigma_ref_bps <= 0:
        return BPS
    max_bps = max(max_bps, BPS)
    if len(samples) < 2 or any(not _defined(s) for s in samples):
        return max_bps
    return _ref.sigma_mult_bps(list(samples), sigma_ref_bps, max(periods_per_year, 0), max_bps)


def sigma_samples(p_fast: Sequence[Price], index: int, vol_window: int, vol_step: int) -> list[Price]:
    """state.cpp:1213-1221: the SIGMA-1 sample list at position ``index`` of a pFast series whose
    element 0 is the snapshot at ``startHeight``: ``s_k = p_fast[index − k·volStep]`` for
    ``k = 0 … volWindow // volStep``; a position before the series (below ``startHeight``) is
    undefined."""
    out: list[Price] = [p_fast[index]]
    if vol_step > 0:
        for k in range(1, vol_window // vol_step + 1):
            j = index - k * vol_step
            out.append(p_fast[j] if j >= 0 else None)
    return out


# ---------------------------------------------------------------------------------------------------
# MINT-5 / MINT-6 / HALT-2 / RED-4 / RED-5 / FEE-1 / AFEE-1


def min_ratio_bps(base_ratio_bps: int, sigma_mult_bps: int) -> int:
    """math.h:105-109 MinRatioBps = base · σmult // 10^4; 0 for a non-positive input."""
    if base_ratio_bps <= 0 or sigma_mult_bps <= 0:
        return 0
    return _ref.min_ratio_bps(base_ratio_bps, sigma_mult_bps)


def required_zat(cents: int, min_ratio: int, p_mint: Price) -> int | None:
    """math.h:115-124 RequiredCollateral (MINT-5): ``ceil(cents · minRatioBps · COIN / pMint)``;
    ``None`` for a non-positive input or when unsatisfiable (> MAX_MONEY, K14).
    Worked example: $100 at 300 % and $0.05 → 600,000,000,000 zat."""
    if cents <= 0 or min_ratio <= 0 or not _defined(p_mint):
        return None
    return _ref.required_zat(cents, min_ratio, p_mint)


def required_zat_rounded(cents: int, min_ratio: int, p_mint: Price, granularity: int = 1000) -> int | None:
    """math.h:127-136 RequiredCollateralRounded: the same, rounded up to ``granularity`` zat
    (the wallet's 1,000); ``None`` if the rounded value leaves the money range."""
    r = required_zat(cents, min_ratio, p_mint)
    if r is None or granularity <= 0:
        return r
    rem = r % granularity
    if rem:
        r += granularity - rem
    return r if r <= MAX_MONEY else None


def cap_cents(issued_zat: int, p_mint: Price) -> int | None:
    """math.h:139-145 CapCents (MINT-6) = issuedZat · pMint // (COIN · 10^4); ``None`` if the price
    is undefined or ``issuedZat < 0``. ``issuedZat`` is the subsidy since ``startHeight``
    (state.cpp:1226, fact 1.5-2)."""
    if not _defined(p_mint) or issued_zat < 0:
        return None
    c = _ref.cap_cents(issued_zat, p_mint)
    return c if _fits_int64(c) else None


def supply_cap_cents(issued_zat: int, p_mint: Price, cap_bps: int) -> int | None:
    """math.h:148-156 SupplyCapCents = capCents · capBps // 10^4; ``capBps ≤ 0`` → ``None`` (no
    cap, regtest). The cap is soft (W20): state.cpp:338-340 lets a mint at ≥ recapRatioBps pass."""
    if cap_bps <= 0:
        return None
    cap = cap_cents(issued_zat, p_mint)
    if cap is None:
        return None
    c = cap * cap_bps // BPS
    return c if _fits_int64(c) else None


def global_ratio_bps(collateral_zat: int, p_mint: Price, supply_cents: int) -> int | None:
    """math.h:162-168 GlobalRatioBps = collateralZat · pMint // (COIN · supplyCents); ``None`` when
    there is no supply ("never halts") or the price is undefined."""
    if supply_cents <= 0 or not _defined(p_mint) or collateral_zat < 0:
        return None
    r = _ref.global_ratio_bps(collateral_zat, p_mint, supply_cents)
    return r if r is not None and _fits_int64(r) else None


def halt2_global_ratio(collateral_zat: int, p_mint: Price, supply_cents: int, halt_bps: int) -> bool:
    """state.cpp:1237 HALT-2: pMint defined, supply > 0 and globalRatioBps < globalRatioHaltBps."""
    r = global_ratio_bps(collateral_zat, p_mint, supply_cents)
    return r is not None and r < halt_bps


def is_underwater(collateral_zat: int, p_claim: Price, minted_cents: int, threshold_bps: int) -> bool:
    """math.h:174-182 IsUnderwater (RED-4(a) with ``claimThresholdBps``, RED-4(b) with
    ``emergencyRatioBps``): ``collateralZat · pClaim < mintedCents · thresholdBps · COIN``.
    ``False`` when pClaim is undefined (M1) or debt/threshold ≤ 0; negative collateral counts 0."""
    if not _defined(p_claim) or minted_cents <= 0 or threshold_bps <= 0:
        return False
    return _ref.is_underwater(max(collateral_zat, 0), p_claim, minted_cents, threshold_bps)


def underwater_price(collateral_zat: int, minted_cents: int, threshold_bps: int) -> int | None:
    """The highest pClaim at which :func:`is_underwater` holds (``None`` if no positive price
    does). Derived: ``c·p < D`` ⇔ ``p < ceil(D / c)`` ⇔ ``p ≤ ceil(D / c) − 1`` with
    ``D = minted · threshold · COIN``. Worked example (math_tests:231): 6,000 YEC backing $100 at
    110 % → 18,333 µUSD. With zero collateral every price is underwater: returns ``INT64_MAX``."""
    if minted_cents <= 0 or threshold_bps <= 0:
        return None
    c = max(collateral_zat, 0)
    if c == 0:
        return INT64_MAX
    p = -((-minted_cents * threshold_bps * COIN) // c) - 1
    return p if p > 0 else None


def claimant_max_zat(minted_cents: int, margin_bps: int, p_claim: Price) -> int | None:
    """math.h:251-260 ClaimantMaxZat (RED-5, R5): ``ceil(minted · margin · COIN / pClaim)``;
    margin = claimThresholdBps under RED-4(a), 10^4 under (b) only. ``None`` if undefined or
    > MAX_MONEY. Worked example: $100 at 110 % and 18,333 µUSD → 600,010,909,290 zat."""
    if not _defined(p_claim):
        return None
    return _ref.claimant_max_zat(minted_cents, margin_bps, p_claim)


def residual_zat(collateral_zat: int, claimant_max: int | None) -> int:
    """math.h:263-267 ResidualZat = max(0, collateral − claimantMax); 0 if claimantMax is None."""
    return _ref.residual_zat(collateral_zat, claimant_max)


def fee_zat(collateral_zat: int, fee_min: int, fee_bps: int) -> int:
    """math.h:185-192 FeeZat (FEE-1) = max(feeMin, collateral · feeBps // 10^4); negative inputs
    count as 0; a product past int64 saturates at MAX_MONEY (unreachable for collateral ≤
    MAX_MONEY)."""
    c, b = max(collateral_zat, 0), max(fee_bps, 0)
    f = c * b // BPS
    if not _fits_int64(f):
        return max(fee_min, MAX_MONEY)
    return _ref.fee_zat(c, fee_min, b)


def attest_fee_zat(fee: int, attest_fee_bps: int) -> int:
    """math.h:270-275 AttestFeeZat (AFEE-1) = fee · attestFeeBps // 10^4; 0 for non-positive
    inputs. Worked example: 15 YEC → 3.75 YEC at 2,500 bps."""
    f = _ref.attest_fee_zat(fee, attest_fee_bps)
    return f if _fits_int64(f) else MAX_MONEY


def class_for_lock_blocks(lock_blocks: int, class_min: Sequence[int], class_max: Sequence[int]) -> int:
    """params.cpp:128-134 ClassForLockBlocks (MINT-2): the term class index, −1 if none."""
    for i, (lo, hi) in enumerate(zip(class_min, class_max, strict=True)):
        if lo <= lock_blocks <= hi:
            return i
    return -1


# ---------------------------------------------------------------------------------------------------
# v3: bond weight, bundle statistic, selection


def bond_weight(bond_zat: int, age_blocks: int, age_cap: int) -> int:
    """math.h:202-207 BondWeight = bondZat · clamp(age, 0, ageCap); 0 for any non-positive input.
    The caller derives ``age = H − ageOrigin`` (founding cohort: triggerHeight)."""
    if bond_zat <= 0 or age_blocks <= 0 or age_cap <= 0:
        return 0
    return _ref_attest.bond_weight(bond_zat, age_blocks, age_cap)


def weighted_quantile(entries: Sequence[tuple[int, int]], q_bps: int) -> int | None:
    """math.h:228-245 WeightedQuantile (the PRICE-2 bundle statistic) over ``(price, weight)``
    pairs: stable-sort by price, threshold = ceil(qBps · total / 10^4) (q < 0 read as 0), the first
    price whose cumulative weight reaches it. ``None`` if empty or the threshold exceeds the total
    (q > 10^4). A zero total gives the first price (threshold 0).

    The reference (``yellowback_attest.weighted_quantile``) differs on two degenerate inputs —
    zero total (None) and q > 10^4 (last price) — so this kernel is transcribed from math.h and
    equals the reference whenever total > 0 and 0 ≤ q ≤ 10^4 (property-tested).
    """
    if not entries:
        return None
    q = max(q_bps, 0)
    rows = sorted(entries, key=lambda e: e[0])   # stable: the caller's tie order is kept
    total = sum(w for _p, w in rows)
    threshold = -((-total * q) // BPS)
    cumulative = 0
    for price, w in rows:
        cumulative += w
        if cumulative >= threshold:
            return price
    return None


def bundle_stat(entries: Sequence[tuple[int, int]], q_low_bps: int, q_high_bps: int,
                m_select: int) -> tuple[int, int] | None:
    """BUNDLE-1 statistic: ``(aMint, aClaim)`` = the qLow / qHigh weighted quantiles; ``None``
    with fewer than ``mSelect`` attestations (the caller's |C| ≥ M_SELECT precondition)."""
    if len(entries) < m_select:
        return None
    lo, hi = weighted_quantile(entries, q_low_bps), weighted_quantile(entries, q_high_bps)
    if lo is None or hi is None:
        return None
    return lo, hi


def select_attestors(block_hash_hex: str, selector: bytes, pool: Sequence[tuple[int, int]],
                     m_select: int, k_slack: int) -> list[int]:
    """W9 selection (attest.cpp; reference ``yellowback_attest.select_attestors``): the
    deterministic weighted draw seeded by ``SHA256(blockHash ‖ selector ‖ "S" ‖ u8 i)`` over
    ``pool = [(seq, weight)]``, ``mSelect + kSlack`` rounds without replacement. Delegates."""
    return _ref_attest.select_attestors(block_hash_hex, bytes(selector), list(pool), m_select, k_slack)


def outpoint_selector(txid_hex: str, vout: int) -> bytes:
    """The 36-byte selector of a vault outpoint (R13; reference ``outpoint_selector``)."""
    return _ref_attest.outpoint_selector(txid_hex, vout)


# ---------------------------------------------------------------------------------------------------
# REG-4 judgement


class Judgement(NamedTuple):
    """view.h Judgement: evaluated / inBand / penalized."""

    evaluated: bool
    in_band: bool
    penalized: bool


def reg4_judgement(quote: int, peers: Sequence[int], peer_min: int, deviation_bps: int,
                   accuracy_band_bps: int) -> Judgement:
    """state.cpp:910-936 Judge (REG-4): with ``m`` = lower median of the peer quotes (the quote
    tags at ``t − lag … t + lag − 1``, ``t`` excluded) and ``dev = |quote − m|·10^4 // m``:
    evaluated iff ``len(peers) ≥ peerMin`` (and m > 0); inBand = dev ≤ accuracyBandBps;
    penalized = dev > deviationBps (negative band / deviation read as 0)."""
    if len(peers) < peer_min:
        return Judgement(False, False, False)
    m = lower_median(peers)
    if m is None or m <= 0:
        return Judgement(False, False, False)
    dev = abs(quote - m) * BPS // m
    return Judgement(True, dev <= max(0, accuracy_band_bps), dev > max(0, deviation_bps))


def reg4_peer_heights(t: int, peer_lag: int) -> range:
    """The heights whose quotes are peers of the tag at ``t`` (state.cpp:917): ``t − lag …
    t + lag − 1``; the caller skips ``t`` itself."""
    return range(t - peer_lag, t + peer_lag)


# ---------------------------------------------------------------------------------------------------
# ACT-1..6: activation and the two hysteresis halts


def signal_count(signals: Sequence[bool], index: int, signal_window: int) -> int:
    """state.cpp:959-967 SignalCount over a per-block signal series whose element 0 is
    ``startHeight``: the signal bits in ``(H − signalWindow, H]`` at or after the start."""
    return sum(1 for s in signals[max(index - signal_window + 1, 0):index + 1] if s)


class ActivationState(NamedTuple):
    status: int            #: SIGNALING / LOCKED_IN / ACTIVE
    lock_in_height: int
    activate_height: int


def activation_step(state: ActivationState, height: int, count: int, start_height: int,
                    signal_window: int, activation_threshold: int, activation_delay: int) -> ActivationState:
    """state.cpp:1080-1089 ACT-1..3 at SNAP of ``height``: SIGNALING → LOCKED_IN once
    ``H ≥ start + signalWindow − 1`` and ``count ≥ threshold`` (activate = H + delay); LOCKED_IN →
    ACTIVE at ``H ≥ activateHeight`` (possibly in the same step when the delay is 0)."""
    status, lock_in, activate = state
    window_full = height >= start_height + signal_window - 1
    if status == SIGNALING and window_full and count >= max(0, activation_threshold):
        status, lock_in, activate = LOCKED_IN, height, height + activation_delay
    if status == LOCKED_IN and height >= activate:
        status = ACTIVE
    return ActivationState(status, lock_in, activate)


def participation_halt_step(prev_halted: bool, count: int, active: bool, activation_threshold: int,
                            participation_floor: int) -> bool:
    """state.cpp:1242-1245 ACT-4 (→ MINT-4): held while ``count < activationThreshold``; set when
    ACTIVE and ``count < participationFloor``."""
    part = prev_halted and count < max(0, activation_threshold)
    return part or (active and count < max(0, participation_floor))


def enforcement_halt_step(prev_halted: bool, count: int, active: bool, enforcement_resume: int,
                          enforcement_floor: int) -> bool:
    """state.cpp:1246-1249 ACT-6 (→ ACT-5, BLK-1): held while ``count < enforcementResume``; set
    when ACTIVE and ``count < enforcementFloor``."""
    enf = prev_halted and count < max(0, enforcement_resume)
    return enf or (active and count < max(0, enforcement_floor))


def param_set_start_admissible(start_height: int, previous_enforce_until: int, signal_window: int,
                               enforcement_halted_at: Callable[[int], bool]) -> bool:
    """params.cpp:259-268 (ACT-5, L8 / W19 "freeze, then fix"): a new set may start at X iff
    X ≥ the previous sunset (> 0), or every height in [X − signalWindow, X − 1] (all ≥ 0) shows
    the ENFORCEMENT halt."""
    if previous_enforce_until > 0 and start_height >= previous_enforce_until:
        return True
    if signal_window <= 0:
        return False
    return all(h >= 0 and enforcement_halted_at(h) for h in range(start_height - signal_window, start_height))


# ---------------------------------------------------------------------------------------------------
# PIN-1 / PIN-2


def pin1_triggered(a_mints: Sequence[int | None], pin_min_bundles: int, pin_delta_bps: int) -> bool:
    """state.cpp:1130-1137 PIN-1 trigger over the BundleLog rows of ``(H − 1 − pinWindow, H − 1]``:
    at least max(1, pinMinBundles) rows, and over the *defined* aMint values (``None`` / ≤ 0 is
    never a value, audit A-4) ``(aHi − aLo)·10^4 > pinDeltaBps·aLo``."""
    if len(a_mints) < max(1, pin_min_bundles):
        return False
    defined = [a for a in a_mints if a is not None and a > 0]
    if not defined:
        return False
    lo, hi = min(defined), max(defined)
    return (hi - lo) * BPS > max(0, pin_delta_bps) * lo


def pin1_pinned_keys(quotes: Iterable[tuple[object, int]], pin_min_tags: int) -> list:
    """state.cpp:1138-1147 PIN-1 (when triggered): over the quote tags ``(payoutKey, price)`` of
    the same window, the keys with ≥ max(1, pinMinTags) quotes all at one price; sorted."""
    per_key: dict[object, list[int]] = {}
    for key, price in quotes:
        per_key.setdefault(key, []).append(price)
    return sorted(k for k, ps in per_key.items() if len(ps) >= max(1, pin_min_tags) and len(set(ps)) == 1)


def pin2_triggered(x1: Price, x0: Price, pin_delta_bps: int) -> bool:
    """state.cpp:1151-1158 PIN-2 trigger: pMint at ``H − 1`` (x1) and at ``H − 1 − pinWindow``
    (x0) both defined and ``|x1 − x0|·10^4 > pinDeltaBps·min(x1, x0)``."""
    if not (_defined(x1) and _defined(x0)):
        return False
    lo = min(x1, x0)  # type: ignore[type-var]
    return (max(x1, x0) - lo) * BPS > max(0, pin_delta_bps) * lo  # type: ignore[type-var,operator]


def pin2_pinned_seqs(rows: Iterable[tuple[Sequence[int], Sequence[int], Sequence[int]]],
                     pin_min_tags: int) -> list[int]:
    """state.cpp:1159-1172 PIN-2 (when triggered): over BundleLog rows ``(seqs, prices,
    citedHeights)``, the seqs present in ≥ max(1, pinMinTags) rows that signed exactly one price at
    ≥ max(1, pinMinTags) distinct cited heights (a reused attestation cannot pin, A-2); sorted."""
    n_rows: dict[int, int] = {}
    prices: dict[int, set[int]] = {}
    cited: dict[int, set[int]] = {}
    for seqs, ps, cs in rows:
        seen: set[int] = set()
        for i, (seq, price) in enumerate(zip(seqs, ps, strict=False)):
            if seq not in seen:
                seen.add(seq)
                n_rows[seq] = n_rows.get(seq, 0) + 1
            prices.setdefault(seq, set()).add(price)
            cited.setdefault(seq, set())
            if i < len(cs):
                cited[seq].add(cs[i])
    m = max(1, pin_min_tags)
    return sorted(s for s, n in n_rows.items() if n >= m and len(prices[s]) == 1 and len(cited[s]) >= m)

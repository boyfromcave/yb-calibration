"""Exact scalar integer kernels of the Yellowback rules (owner: WP-1; PLAN §2.1).

Each function is the integer formula of ``src/yellowback/math.h`` (or the cited block of
``state.cpp`` / ``params.cpp``) at the pinned node commit — ycash6 ``1310a41a1`` (``upgrade/vault``),
whose ``src/yellowback/`` and test framework are byte-identical to ycash-dd ``8af82551b``; the
model's ``SCHEMA_VERSION`` is 13. Citations are ``file:line`` at that commit. Where the vendored
reference model (:mod:`ybcal.model.reference`, :mod:`ybcal.model.reference_attest`) has the same
function, the kernel delegates to it after applying the C++ guards, so the two can never drift
silently; where it does not (the model computes PRICE-2, HALT-1..3, REG-4, PIN, RED-4's path and the
intent accounting inline in ``YellowbackModel._snap`` / ``_judge`` / ``_red_verdict`` /
``_apply_intent_spend``), the kernel is transcribed from the C++ and tested against the model's
state on the golden chain (:mod:`ybcal.model.golden`).

The rule set is the vault upgrade's (``UPGRADE_VAULT``; upgrade plan §15.10) with in-term claims
(IT-*), the post-quantum fee structure (FEE-1′, IT-9′, MINT-6b, RED-5′: quantum plan D-Q-23..D-Q-26)
and CAN-1 (a cancelled claim refunds its burn; audit 03/H-1). **Retired** with the upgrade (U-21) and
gone from this module: the ACT-1..6 activation state machine and its PARTICIPATION / ENFORCEMENT
halts (signal counts, lock-in, the W19 start predicate) and v2/v3's FEE-1 collateral fee
(``max(feeMin, collateral·feeBps)``). Their last exact mirrors live, frozen, in
:mod:`ybcal.model.retired` for the studies whose parameter registry is still the 7702d22 set.

Conventions (math.h:19-24): Python integers only (no floats anywhere in this module), floor
division unless a ceiling is written, and **undefined is ``None``**. Prices are integer µUSD per
YEC, amounts zat, YED amounts cents, ratios basis points. A non-positive price is undefined, as in
the C++ (``pMint <= 0`` → nullopt). Predicates on an undefined input are ``False`` (totality,
spec §3.8, M1).

Totality differences between the C++ and the reference model are resolved in favour of the C++
(the node is the consensus); they are listed in ``docs/architecture.md`` ("Model and kernels").
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import NamedTuple

from ybcal.model import reference as _ref
from ybcal.model import reference_attest as _ref_attest

OWNER_WP = "WP-1"

COIN: int = _ref.COIN
MAX_MONEY: int = _ref.MAX_MONEY
BPS: int = _ref.BPS
INT64_MAX: int = (1 << 63) - 1
#: D-Q-22: the attestation's price field is a uint32 (µUSD), so PRICE_MAX is $4,294.967295.
PRICE_MAX: int = 4_294_967_295
PRICE_MIN: int = 100

#: The §3.6 haltMask bits (state.h declaration order; reference.HALT_*). Since U-21 ``NOT_ACTIVE``
#: marks only the virtual snapshot below START_HEIGHT; ``PARTICIPATION`` / ``ENFORCEMENT`` are
#: reserved and never set.
HALT_NOT_ACTIVE: int = _ref.HALT_NOT_ACTIVE
HALT_NO_PRICE: int = _ref.HALT_NO_PRICE
HALT_GLOBAL_RATIO: int = _ref.HALT_GLOBAL_RATIO
HALT_DIVERGENCE: int = _ref.HALT_DIVERGENCE

Price = int | None


def _defined(p: Price) -> bool:
    return p is not None and p > 0


def _fits_int64(x: int) -> bool:
    """math.h:42 FitsInt64 (for the non-negative values it is applied to)."""
    return 0 <= x <= INT64_MAX


def _ceil_div(a: int, b: int) -> int:
    return -((-a) // b)


# ---------------------------------------------------------------------------------------------------
# PRICE-1 / PRICE-2 / HALT-1..3


def lower_median(values: Iterable[int]) -> int | None:
    """math.h:53 LowerMedian: element ``(n - 1) // 2`` of the ascending sort; ``None`` if empty."""
    return _ref.lower_median(list(values))


def window_median_with_fill(prices: Sequence[int], fill: int) -> int | None:
    """state.cpp:141-149 WindowMedian: the lower median of the quote prices in a window, undefined
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
    """state.cpp:1666 PRICE-2: ``pMint = min(pFast, pMid, pSlow)``, undefined if any is."""
    if not (_defined(p_fast) and _defined(p_mid) and _defined(p_slow)):
        return None
    return min(p_fast, p_mid, p_slow)  # type: ignore[type-var]


def price_claim(p_mid: Price, p_slow: Price) -> int | None:
    """state.cpp:1667 PRICE-2: ``pClaim = max(pMid, pSlow)``, undefined if either is."""
    if not (_defined(p_mid) and _defined(p_slow)):
        return None
    return max(p_mid, p_slow)  # type: ignore[type-var]


def halt3_divergence(p_fast: Price, p_mid: Price, p_slow: Price, divergence_bps: int) -> bool:
    """state.cpp:1702-1705 HALT-3: with k = 10^4 − divergenceBps, set iff
    ``pFast·10^4 < k·pMid`` or ``pMid·10^4 < k·pSlow``. Fires only on a fall (fact 1.5-6);
    ``False`` unless all three medians are defined."""
    if not (_defined(p_fast) and _defined(p_mid) and _defined(p_slow)):
        return False
    k = BPS - divergence_bps
    return p_fast * BPS < k * p_mid or p_mid * BPS < k * p_slow  # type: ignore[operator]


def halt_mask(p_fast: Price, p_mid: Price, p_slow: Price, collateral_zat: int, supply_cents: int,
              global_ratio_halt_bps: int, divergence_bps: int) -> int:
    """state.cpp:1698-1706 HALT-1..3, the whole SNAP mask of a real snapshot (U-21: no NOT_ACTIVE,
    PARTICIPATION or ENFORCEMENT bit — the activation state machine left with the upgrade):
    ``NO_PRICE`` without pMint; ``GLOBAL_RATIO`` when pMint is defined, supply > 0 and the global
    ratio is below ``globalRatioHaltBps``; ``DIVERGENCE`` by HALT-3."""
    p_mint = price_mint(p_fast, p_mid, p_slow)
    mask = 0
    if p_mint is None:
        mask |= HALT_NO_PRICE
    if halt2_global_ratio(collateral_zat, p_mint, supply_cents, global_ratio_halt_bps):
        mask |= HALT_GLOBAL_RATIO
    if halt3_divergence(p_fast, p_mid, p_slow, divergence_bps):
        mask |= HALT_DIVERGENCE
    return mask


def mint4_halt_verdict(mask: int, base_ratio_bps: int, recap_ratio_bps: int) -> str | None:
    """state.cpp:366-378 MINT-4 over the snapshot at R: the halt bits in declaration order —
    ``NOT_ACTIVE`` → ``mint-not-active``, ``NO_PRICE`` → ``mint-halted-no-price``, ``GLOBAL_RATIO``
    only for a class whose base ratio is below ``recapRatioBps`` (HALT-2 amended, W16; IT-5: class C
    alone with the in-term set), ``DIVERGENCE``, then any other bit → ``mint-not-active``. ``None``
    = MINT-4 passes (H-1's ``mint-halted-unarmed`` is the caller's, it needs ARM state)."""
    if mask & HALT_NOT_ACTIVE:
        return "mint-not-active"
    if mask & HALT_NO_PRICE:
        return "mint-halted-no-price"
    if (mask & HALT_GLOBAL_RATIO) and base_ratio_bps < recap_ratio_bps:
        return "mint-halted-global-ratio"
    if mask & HALT_DIVERGENCE:
        return "mint-halted-divergence"
    if mask & ~HALT_GLOBAL_RATIO:
        return "mint-not-active"
    return None


class CombinedPrices(NamedTuple):
    """math.h:413-418: the ARMED per-transaction prices."""

    p_mint: int | None   #: min(xMint, aMint)
    p_claim: int | None  #: max(xClaim, aClaim)
    p_emerg: int | None  #: min(xClaim, aClaim)


def price_combine(x_mint: Price, x_claim: Price, a_mint: Price, a_claim: Price) -> CombinedPrices:
    """math.h:421-433 PriceCombine (PRICE-2 revised, ARMED): each output undefined iff one of its
    inputs is. Not ARMED, the caller reads ``x`` alone."""
    pm = min(x_mint, a_mint) if x_mint is not None and a_mint is not None else None
    if x_claim is not None and a_claim is not None:
        return CombinedPrices(pm, max(x_claim, a_claim), min(x_claim, a_claim))
    return CombinedPrices(pm, None, None)


# ---------------------------------------------------------------------------------------------------
# SIGMA-1 (pinned at 1x on every network since D-IT-5: sigmaRefBps = 0; the formula is kept)


def isqrt(x: int) -> int:
    """math.h:61 IsqrtU256 (floor square root)."""
    return _ref.isqrt(x)


def sigma_mult_bps(samples: Sequence[Price], sigma_ref_bps: int, periods_per_year: int, max_bps: int) -> int:
    """math.h:80-102 SigmaMultBps (SIGMA-1, V17) over the pFast samples ``s_0 … s_n``
    (``s_0`` = the newest, ``s_k`` = pFast at ``H − k·volStep``).

    ``sigmaRefBps ≤ 0`` → 10^4 (every network since D-IT-5 / IT-4: the multiplier is pinned at 1 and
    MINT-5's ratio is the class base); ``maxBps`` is raised to 10^4 if below; fewer than two samples,
    or any undefined / non-positive sample → ``maxBps`` (K12). Otherwise
    ``r_k = |s_k − s_{k+1}|·10^4 // s_{k+1}``, ``var = Σr_k² // n``, ``σ = isqrt(var · periodsPerYear)``,
    result = clamp(σ·10^4 // sigmaRefBps, 10^4, maxBps). Delegates the core to the reference.
    """
    if sigma_ref_bps <= 0:
        return BPS
    max_bps = max(max_bps, BPS)
    if len(samples) < 2 or any(not _defined(s) for s in samples):
        return max_bps
    return _ref.sigma_mult_bps(list(samples), sigma_ref_bps, max(periods_per_year, 0), max_bps)


def sigma_samples(p_fast: Sequence[Price], index: int, vol_window: int, vol_step: int) -> list[Price]:
    """state.cpp:1674-1684: the SIGMA-1 sample list at position ``index`` of a pFast series whose
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
# MINT-5 / MINT-6 / MINT-6b / HALT-2 / RED-4 / RED-5 / RED-5'


def min_ratio_bps(base_ratio_bps: int, sigma_mult_bps: int) -> int:
    """math.h:105-109 MinRatioBps = base · σmult // 10^4; 0 for a non-positive input."""
    if base_ratio_bps <= 0 or sigma_mult_bps <= 0:
        return 0
    return _ref.min_ratio_bps(base_ratio_bps, sigma_mult_bps)


def required_zat(cents: int, min_ratio: int, p_mint: Price) -> int | None:
    """math.h:115-124 RequiredCollateral (MINT-5′): ``ceil(cents · minRatioBps · COIN / pMint)``;
    ``None`` for a non-positive input or when unsatisfiable (> MAX_MONEY, K14). Since D-Q-23 there
    is no ``4 · FEE_MIN`` floor. Worked example: $100 at 300 % and $0.05 → 600,000,000,000 zat."""
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
    """math.h:142-148 CapCents (MINT-6) = issuedZat · pMint // (COIN · 10^4); ``None`` if the price
    is undefined or ``issuedZat < 0``. Since D-Q-25 ``issuedZat`` is the YEC **circulating** at the
    snapshot (the subsidy sum from genesis: ``Params::issuedBeforeStart`` below startHeight plus every
    block's subsidy since; state.cpp:1688-1691), so capCents is the market capitalisation in cents.
    (v3 counted the subsidy since startHeight only.)"""
    if not _defined(p_mint) or issued_zat < 0:
        return None
    c = _ref.cap_cents(issued_zat, p_mint)
    return c if _fits_int64(c) else None


def supply_cap_cents(issued_zat: int, p_mint: Price, cap_bps: int) -> int | None:
    """math.h:151-159 SupplyCapCents = capCents · capBps // 10^4; ``capBps ≤ 0`` → ``None`` (no
    cap, regtest). The cap is soft (W20, IT-5): :func:`mint6_cap_refuses`."""
    if cap_bps <= 0:
        return None
    cap = cap_cents(issued_zat, p_mint)
    if cap is None:
        return None
    c = cap * cap_bps // BPS
    return c if _fits_int64(c) else None


def mint6_cap_refuses(supply_cents: int, cents: int, cap: int | None, base_ratio_bps: int,
                      recap_ratio_bps: int) -> bool:
    """state.cpp:405-411 MINT-6 (amended, W20; IT-5): a mint that would take supply over the cap is
    refused only when its class base ratio is below ``recapRatioBps`` (class C alone mints above the
    cap with the in-term set). Silent without a cap."""
    return cap is not None and supply_cents + cents > cap and base_ratio_bps < recap_ratio_bps


def max_vault_cents(cap: int | None, max_vault_share_bps: int, vault_share_floor_cents: int) -> int | None:
    """math.h:389-395 MaxVaultCents (MINT-6b, D-Q-23a): the largest debt one mint may create,
    ``max(cap · share // 10^4, floor)``; ``None`` (no limit) without a cap or with share ≤ 0. The rule
    (state.cpp:412-415) refuses ``cents > maxVaultCents`` (``mint-vault-share``), every class alike."""
    if cap is None or max_vault_share_bps <= 0:
        return None
    share = max(0, cap) * max_vault_share_bps // BPS
    if not _fits_int64(share):
        share = INT64_MAX
    return max(share, max(0, vault_share_floor_cents))


def global_ratio_bps(collateral_zat: int, p_mint: Price, supply_cents: int) -> int | None:
    """math.h:165-171 GlobalRatioBps = collateralZat · pMint // (COIN · supplyCents); ``None`` when
    there is no supply ("never halts") or the price is undefined. ``collateralZat`` is
    ``Totals.collateralZat``, which since IT-6 (D-IT-14) still counts a CLAIMING vault's collateral
    until its claimant intent is released (:func:`claim_accounting`)."""
    if supply_cents <= 0 or not _defined(p_mint) or collateral_zat < 0:
        return None
    r = _ref.global_ratio_bps(collateral_zat, p_mint, supply_cents)
    return r if r is not None and _fits_int64(r) else None


def halt2_global_ratio(collateral_zat: int, p_mint: Price, supply_cents: int, halt_bps: int) -> bool:
    """state.cpp:1701 HALT-2: pMint defined, supply > 0 and globalRatioBps < globalRatioHaltBps."""
    r = global_ratio_bps(collateral_zat, p_mint, supply_cents)
    return r is not None and r < halt_bps


def is_underwater(collateral_zat: int, p_claim: Price, minted_cents: int, threshold_bps: int) -> bool:
    """math.h:177-186 IsUnderwater (RED-4(a) with ``claimThresholdBps`` θ, 12,500 since D-IT-2;
    RED-4(b) with ``emergencyRatioBps``): ``collateralZat · pClaim < mintedCents · thresholdBps · COIN``.
    ``False`` when pClaim is undefined (M1) or debt/threshold ≤ 0; negative collateral counts 0."""
    if not _defined(p_claim) or minted_cents <= 0 or threshold_bps <= 0:
        return False
    return _ref.is_underwater(max(collateral_zat, 0), p_claim, minted_cents, threshold_bps)


def underwater_price(collateral_zat: int, minted_cents: int, threshold_bps: int) -> int | None:
    """The highest pClaim at which :func:`is_underwater` holds (``None`` if no positive price
    does). Derived: ``c·p < D`` ⇔ ``p < ceil(D / c)`` ⇔ ``p ≤ ceil(D / c) − 1`` with
    ``D = minted · threshold · COIN``. Worked example (math_tests:297-299): 6,000 YEC backing $100 at
    110 % → 18,333 µUSD. With zero collateral every price is underwater: returns ``INT64_MAX``."""
    if minted_cents <= 0 or threshold_bps <= 0:
        return None
    c = max(collateral_zat, 0)
    if c == 0:
        return INT64_MAX
    p = -((-minted_cents * threshold_bps * COIN) // c) - 1
    return p if p > 0 else None


def claim_path(collateral_zat: int, p_claim: Price, p_emerg: Price, minted_cents: int,
               claim_threshold_bps: int, emergency_ratio_bps: int, notice_persisted: bool) -> str | None:
    """state.cpp:665-686 RED-4 (amended; IT-2): ``"a"`` when the vault is underwater under pClaim at
    θ = ``claimThresholdBps``; else ``"b"`` when a standing notice persisted (``emergencyPersist ≤
    R − notice.R ≤ emergencyNoticeTtl``, ARMED only — the caller's ``notice_persisted``) and it is
    underwater under pEmerg at ``emergencyRatioBps``; else ``None`` (``vault-claim-not-underwater``).

    **There is no height clause** (IT-2): the same test decides at every height the V's APP branch
    admits — from ``appHeight = refHeight + 1``, i.e. the block after the mint (IT-1) — in term, in
    grace and after it. (v2/v3 allowed a claim only from ``claimHeight = lockHeight + GRACE``.)"""
    if is_underwater(collateral_zat, p_claim, minted_cents, claim_threshold_bps):
        return "a"
    if notice_persisted and is_underwater(collateral_zat, p_emerg, minted_cents, emergency_ratio_bps):
        return "b"
    return None


def claimant_max_zat(minted_cents: int, margin_bps: int, p_claim: Price) -> int | None:
    """math.h:345-356 ClaimantMaxZat (RED-5, R5): ``ceil(minted · margin · COIN / pClaim)``;
    margin = claimThresholdBps under RED-4(a), 10^4 under (b) only. ``None`` if undefined or
    > MAX_MONEY. Worked example: $100 at θ 125 % and 18,333 µUSD → 681,830,578,738 zat."""
    if not _defined(p_claim):
        return None
    return _ref.claimant_max_zat(minted_cents, margin_bps, p_claim)


def residual_zat(collateral_zat: int, claimant_max: int | None) -> int:
    """math.h:398-402 ResidualZat = max(0, collateral − claimantMax); 0 if claimantMax is None."""
    return _ref.residual_zat(collateral_zat, claimant_max)


def residual_min_zat(p_mint: Price, residual_min_floor_zat: int, residual_min_cents: int) -> int:
    """math.h:361-369 ResidualMinZat (RED-5′, D-Q-23a): the residual threshold,
    ``max(residualMinZat, ceil(residualMinCents · 10^4 · COIN / pMint))`` — $0.48 at the **vault's**
    mint price, never below the 100,000-zat floor; the floor alone without a price, without cents or
    when the quotient leaves the money range."""
    floor = max(0, residual_min_floor_zat)
    if not _defined(p_mint) or residual_min_cents <= 0:
        return floor
    z = _ceil_div(residual_min_cents * BPS * COIN, p_mint)  # type: ignore[arg-type]
    return floor if z > MAX_MONEY else max(floor, z)


class ClaimSplit(NamedTuple):
    """RED-5 / RED-5′: how a claim divides the collateral."""

    claimant_max_zat: int | None  #: the claimant's cap (``None``: over MAX_MONEY, residual 0)
    residual_zat: int             #: what is above the cap
    residual_due: bool            #: the residual reaches RED-5′'s threshold → an owner residual intent


def claim_split(collateral_zat: int, minted_cents: int, p_claim: Price, path: str, claim_threshold_bps: int,
                residual_threshold_zat: int, *, p_emerg: Price) -> ClaimSplit:
    """state.cpp:690-711 (ycash6 4129f22cd) RED-5 (U-23): path ``"a"`` caps at pClaim with margin θ,
    path ``"b"`` at pEmerg with margin 10^4 (RED-5b, W3-Y-5, reverses v3 R1); the residual
    ``max(0, collateral − claimantMax)`` is due — paid by the higher-index, owner intent of at least
    that value, the lower-index claimant intent carrying at least ``collateral − residual`` (RED-5a) —
    iff it is at least ``residual_threshold_zat`` (:func:`residual_min_zat` at the vault's pMint);
    otherwise the claimant's intent carries the whole collateral."""
    margin = claim_threshold_bps if path == "a" else BPS
    cap_price = p_claim if path == "a" else p_emerg
    cm = claimant_max_zat(minted_cents, margin, cap_price) if _defined(cap_price) else None
    res = residual_zat(collateral_zat, cm)
    return ClaimSplit(cm, res, res >= residual_threshold_zat)


# ---------------------------------------------------------------------------------------------------
# FEE-1' / IT-9' / AFEE-1' (quantum plan D-Q-23: the protocol fee is the debt's, in dollars, at the
# vault's own mint price; v2/v3's FEE-1 collateral fee is retired, see ybcal.model.retired)


class _FeeTable:
    """The two Params fields the reference's fee functions read (``fee_bands``, ``fee_floor_cents``)."""

    __slots__ = ("fee_bands", "fee_floor_cents")

    def __init__(self, fee_bands: Sequence[tuple[int, int]], fee_floor_cents: int) -> None:
        self.fee_bands = tuple((int(u), int(b)) for u, b in fee_bands)
        self.fee_floor_cents = int(fee_floor_cents)


#: The FEE-1′ table of every network (params.cpp, math_tests:320-353; D-Q-23): 1 % to $1,000, 0.5 % to
#: $10,000, 0.1 % above, at least $1 per action.
FEE_BANDS: tuple[tuple[int, int], ...] = ((100_000, 100), (1_000_000, 50), (0, 10))
FEE_FLOOR_CENTS: int = 100


def protocol_fee_units(debt_cents: int, fee_bands: Sequence[tuple[int, int]] = FEE_BANDS,
                       fee_floor_cents: int = FEE_FLOOR_CENTS) -> int:
    """math.h:192-204 ProtocolFeeUnits (FEE-1′): the fee of a debt in cent-bps — the sum over the bands
    ``(uptoCents, bps)`` (``uptoCents 0`` = unbounded) of the debt inside the band × its bps (a
    non-positive bps contributes nothing; a bounded last band stops at its edge), at least
    ``feeFloorCents · 10^4``; 0 for a debt ≤ 0. Delegates to the reference."""
    return _ref.protocol_fee_units(debt_cents, _FeeTable(fee_bands, fee_floor_cents))


def protocol_fee_cents(debt_cents: int, fee_bands: Sequence[tuple[int, int]] = FEE_BANDS,
                       fee_floor_cents: int = FEE_FLOOR_CENTS) -> int:
    """math.h:207 ProtocolFeeCents: ``ceil(units / 10^4)`` — the dollar fee shown by the RPCs."""
    return _ref.protocol_fee_cents(debt_cents, _FeeTable(fee_bands, fee_floor_cents))


def protocol_fee_zat(debt_cents: int, p_mint: Price, fee_bands: Sequence[tuple[int, int]] = FEE_BANDS,
                     fee_floor_cents: int = FEE_FLOOR_CENTS) -> int | None:
    """math.h:215-220 ProtocolFeeZat (FEE-1′): ``ceil(units · COIN / pMint)`` at the **vault's** mint
    price (MINT-8′ at the mint; RED-3′ on every later spend reads the stored ``VaultRecord.pMint``,
    never a live price); ``None`` when pMint is undefined (RED-1′ refuses such a vault); MAX_MONEY when
    the quotient does not fit (unreachable for a vault that exists). Worked example: $1 at $0.018 →
    5,555,555,556 zat."""
    if not _defined(p_mint):
        return None
    return _ref.protocol_fee_zat(debt_cents, p_mint, _FeeTable(fee_bands, fee_floor_cents))


def early_redeem_fee_zat(debt_cents: int, early_redeem_fee_bps: int, p_mint: Price) -> int:
    """math.h:227-232 EarlyRedeemFeeZat (IT-9′): ``ceil(debt · bps · COIN / pMint)``, no floor —
    ``earlyRedeemFeeBps[class]`` (500 / 250 / 100) of the **debt** at the vault's mint price, paid on
    top of the protocol fee by an owner redeem before ``lockHeight``; 0 when bps ≤ 0, the debt ≤ 0 or
    pMint is undefined; MAX_MONEY if it does not fit. (v3's IT-9 took the bps of the collateral.)"""
    if not _defined(p_mint):
        return 0
    return _ref.early_redeem_fee_zat(debt_cents, early_redeem_fee_bps, p_mint)


def redeem_fee_zat(minted_cents: int, p_mint: Price, *, owner_path: bool, height: int, lock_height: int,
                   early_redeem_fee_bps: int, fee_bands: Sequence[tuple[int, int]] = FEE_BANDS,
                   fee_floor_cents: int = FEE_FLOOR_CENTS) -> int | None:
    """state.cpp RED-3′ + IT-9′ (reference ``_red_verdict``): the least ``vout[feeVout]`` of a vault
    spend with E(R) non-empty — the protocol fee of the debt at the vault's pMint, plus, on an
    owner redeem below ``lockHeight``, the class's early-redeem fee; a claim pays the protocol fee
    alone. ``None`` for a vault without a mint price (RED-1′). Under FEE-0 (E(R) empty) no fee is due —
    the caller's case."""
    fee = protocol_fee_zat(minted_cents, p_mint, fee_bands, fee_floor_cents)
    if fee is None:
        return None
    if owner_path and height < lock_height:
        fee += early_redeem_fee_zat(minted_cents, early_redeem_fee_bps, p_mint)
    return fee


def attest_fee_zat(fee: int, attest_fee_bps: int) -> int:
    """math.h:405-410 AttestFeeZat (AFEE-1′) = fee · attestFeeBps // 10^4; 0 for non-positive inputs.
    ``fee`` is FEE-1′'s **protocol** fee (never the early fee). Mainnet attestFeeBps is 5,000 (H-4:
    half); regtest keeps v3's 2,500. Worked example: the $1 floor at $0.05 = 20 YEC → 10 YEC."""
    f = _ref.attest_fee_zat(fee, attest_fee_bps)
    return f if _fits_int64(f) else MAX_MONEY


def class_for_lock_blocks(lock_blocks: int, class_min: Sequence[int], class_max: Sequence[int]) -> int:
    """params.cpp:150-156 ClassForLockBlocks (MINT-2): the term class index, −1 if none. A class whose
    range is empty (``min > max``, H-5) never matches."""
    for i, (lo, hi) in enumerate(zip(class_min, class_max, strict=True)):
        if lo <= lock_blocks <= hi:
            return i
    return -1


# ---------------------------------------------------------------------------------------------------
# U-23 / IT-6 / CAN-1: what a claim, its release and its cancel do to the totals


class TotalsDelta(NamedTuple):
    """A change of ``Totals`` (supplyCents, collateralZat, activeVaults, claimedVaults)."""

    supply_cents: int
    collateral_zat: int
    active_vaults: int
    claimed_vaults: int


def claim_accounting(event: str, *, burned_cents: int = 0, claiming_collateral_zat: int = 0,
                     relock_zat: int = 0) -> TotalsDelta:
    """state.cpp:776-800 and 805-894 (reference ``_apply_vault_spend`` / ``_apply_intent_spend``):

    * ``"claim"`` — a passing claim (selector 4) burns the debt (supply −burn, IN-3) and leaves the
      vault CLAIMING: out of ``activeVaults``, but its collateral **stays** in ``collateralZat`` and so
      in the global ratio until release (IT-6, D-IT-14);
    * ``"release"`` — the claimant intent's release (after CLAIM_DELAY): the collateral leaves,
      ``claimedVaults += 1``;
    * ``"cancel"`` — the attestor set's cancel (CAN-1, audit 03/H-1; U-24 revised): the vault is
      re-locked at ``vout[0]`` as ACTIVE (collateral = the re-lock's value, so only the difference
      to the CLAIMING collateral moves) and the **whole burn is re-issued** as a YED token to the
      claim's refund holder (supply +burn). A cancel is therefore value-neutral for the claimant
      but for the fees it paid; the set's cancel and unlock thresholds are pinned constants
      (``ATTESTOR_CANCEL_THRESHOLD`` = ``ATTESTOR_UNLOCK_THRESHOLD`` = 1; state.cpp:1039-1040, 03/I-3).
    * ``"redeem"`` — an owner redeem: supply −burn, collateral −the vault's, one fewer active vault.
    """
    if event == "claim":
        return TotalsDelta(-burned_cents, 0, -1, 0)
    if event == "release":
        return TotalsDelta(0, -claiming_collateral_zat, 0, 1)
    if event == "cancel":
        return TotalsDelta(burned_cents, relock_zat - claiming_collateral_zat, 1, 0)
    if event == "redeem":
        return TotalsDelta(-burned_cents, -claiming_collateral_zat, -1, 0)
    raise ValueError(f"unknown claim event {event!r}")


def set_create_adoptable(open_set: bool, seats: int, cancel_threshold: int, unlock_threshold: int,
                         maturity: int, liveness_window: int, *, attestor_seats: int,
                         attestor_cancel_threshold: int, attestor_unlock_threshold: int, bond_maturity: int,
                         attestor_liveness_min: int) -> bool:
    """state.cpp:1035-1042 (reference ``_apply_set_act``): YED adopts its attestor set's SET_CREATE only
    when the set is OPEN with exactly ``ATTESTOR_SEATS`` seats, its cancel and unlock thresholds are the
    pinned constants (03/I-3; CAN-1 makes a cancel value-neutral), its maturity (the bond exit delay)
    is ``BOND_MATURITY`` and its liveness window at least ``ATTESTOR_LIVENESS_MIN`` (seats review B3)."""
    return (open_set and seats == attestor_seats and cancel_threshold == attestor_cancel_threshold
            and unlock_threshold == attestor_unlock_threshold and maturity == bond_maturity
            and liveness_window >= attestor_liveness_min)


# ---------------------------------------------------------------------------------------------------
# v3: bond weight, bundle statistic, selection


def bond_weight(bond_zat: int, age_blocks: int, age_cap: int) -> int:
    """math.h:242-247 BondWeight = bondZat · clamp(age, 0, ageCap); 0 for any non-positive input.
    The caller derives ``age = H − ageOrigin`` (founding cohort: triggerHeight).

    Since the owner's 2026-10-11 seat rules (quantum plan D-Q-29) this is the weight of the price
    statistic and W9 selection only; seat ranking and eviction use ``bond × fQ16(tenure)``
    (:mod:`ybcal.model.seats`, math.h:257-301 Log2Q16 / SeatAgeFactorQ16 / SeatWeight)."""
    if bond_zat <= 0 or age_blocks <= 0 or age_cap <= 0:
        return 0
    return _ref_attest.bond_weight(bond_zat, age_blocks, age_cap)


def weighted_quantile(entries: Sequence[tuple[int, int]], q_bps: int) -> int | None:
    """math.h:322-339 WeightedQuantile (the PRICE-2 bundle statistic) over ``(price, weight)``
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
    """state.cpp:1371-1396 Judge (REG-4): with ``m`` = lower median of the peer quotes (the quote
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
    """The heights whose quotes are peers of the tag at ``t`` (state.cpp:1378): ``t − lag …
    t + lag − 1``; the caller skips ``t`` itself."""
    return range(t - peer_lag, t + peer_lag)


# ---------------------------------------------------------------------------------------------------
# PIN-1 / PIN-2


def pin1_triggered(a_mints: Sequence[int | None], pin_min_bundles: int, pin_delta_bps: int) -> bool:
    """state.cpp:1589-1596 PIN-1 trigger over the BundleLog rows of ``(H − 1 − pinWindow, H − 1]``:
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
    """state.cpp:1597-1608 PIN-1 (when triggered): over the quote tags ``(payoutKey, price)`` of
    the same window, the keys with ≥ max(1, pinMinTags) quotes all at one price; sorted."""
    per_key: dict[object, list[int]] = {}
    for key, price in quotes:
        per_key.setdefault(key, []).append(price)
    return sorted(k for k, ps in per_key.items() if len(ps) >= max(1, pin_min_tags) and len(set(ps)) == 1)


def pin2_triggered(x1: Price, x0: Price, pin_delta_bps: int) -> bool:
    """state.cpp:1610-1617 PIN-2 trigger: pMint at ``H − 1`` (x1) and at ``H − 1 − pinWindow``
    (x0) both defined and ``|x1 − x0|·10^4 > pinDeltaBps·min(x1, x0)``."""
    if not (_defined(x1) and _defined(x0)):
        return False
    lo = min(x1, x0)  # type: ignore[type-var]
    return (max(x1, x0) - lo) * BPS > max(0, pin_delta_bps) * lo  # type: ignore[type-var,operator]


def pin2_pinned_seqs(rows: Iterable[tuple[Sequence[int], Sequence[int], Sequence[int]]],
                     pin_min_tags: int) -> list[int]:
    """state.cpp:1618-1634 PIN-2 (when triggered): over BundleLog rows ``(seqs, prices,
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

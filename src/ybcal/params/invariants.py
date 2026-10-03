"""PLAN §1.4 as executable checks (owner: WP-0).

Every candidate set is rejected before simulation if any invariant fails. Each invariant names the
rule it comes from. Invariants with ``scope="mainnet-scale"`` encode mainnet block counts (the
M14 release lead, one-year bond locks, the runbook length, K13's BLOCKS_PER_YEAR / volStep) and
are skipped for regtest-scale sets; regtest-only meanings of zero (``enforceUntilHeight = 0`` = no
sunset, ``sigmaRefBps = 0`` = multiplier fixed at 1, ``supplyCapBps = 0`` = no cap,
``attestArmMin = 0`` = never arms) are accepted only at regtest scale.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from ybcal.units import (
    BLOCKS_PER_YEAR,
    BPS,
    COIN,
    LOCKTIME_THRESHOLD,
    PRICE_MAX,
    RELEASE_LEAD_BLOCKS,
    ceil_div,
)

if TYPE_CHECKING:
    from ybcal.params.paramset import ParamSet

Scope = Literal["all", "mainnet-scale"]

#: Largest bundle the 520-byte push holds: 4 + 6·74 bytes (BUNDLE-1).
BUNDLE_MAX_PUSH: int = 6
#: K13: annualisation constant on every network.
VOL_PERIODS_PER_YEAR_K13: int = 8760


@dataclass(frozen=True)
class Context:
    """Facts outside the parameter set that some invariants need.

    ``release_tip``: chain tip at the planned release (M14 lead check runs only when given).
    ``next_upgrade_height``: next scheduled Ycash network upgrade (L8 cap; ``None`` = none known).
    ``runbook_buffer_blocks``: operator buffer in the freeze-then-fix runbook (PLAN §5.4).
    ``max_entity_share_bps``: largest single-entity attestor weight share the policy assumes.
    ``worst_price_microusd``: highest YEC price the policy covers (fewest zat per cent of debt).
    """

    release_tip: int | None = None
    next_upgrade_height: int | None = None
    release_lead_blocks: int = RELEASE_LEAD_BLOCKS
    runbook_buffer_blocks: int = 0
    max_entity_share_bps: int | None = None
    worst_price_microusd: int = PRICE_MAX

    @classmethod
    def from_policy(cls, policy: Any, *, release_tip: int | None = None,
                    next_upgrade_height: int | None = None) -> Context:
        """Build from a :class:`ybcal.config.Policy` (duck-typed to avoid an import cycle)."""
        return cls(
            release_tip=release_tip,
            next_upgrade_height=next_upgrade_height,
            release_lead_blocks=int(getattr(policy, "release_lead_blocks", RELEASE_LEAD_BLOCKS)),
            runbook_buffer_blocks=int(getattr(policy, "runbook_operator_buffer_blocks", 0)),
            max_entity_share_bps=round(float(policy.max_single_entity_weight_share) * BPS)
            if getattr(policy, "max_single_entity_weight_share", None) is not None else None,
            worst_price_microusd=round(
                float(getattr(policy, "worst_price_usd", PRICE_MAX / 1e6)) * 1_000_000),
        )


@dataclass(frozen=True)
class Violation:
    """One failed invariant."""

    invariant: str
    rule: str
    message: str
    params: tuple[str, ...]

    def __str__(self) -> str:
        return f"{self.invariant} ({self.rule}): {self.message}"


CheckFn = Callable[["ParamSet", Context], list[str]]


@dataclass(frozen=True)
class Invariant:
    """A named check. ``fn`` returns failure messages (empty = pass)."""

    name: str
    rule: str
    doc: str
    params: tuple[str, ...]
    fn: CheckFn
    scope: Scope = "all"
    proposed: bool = False   #: new in PLAN (not a node rule); still enforced


INVARIANTS: dict[str, Invariant] = {}


def _inv(name: str, rule: str, doc: str, params: tuple[str, ...], *, scope: Scope = "all",
         proposed: bool = False) -> Callable[[CheckFn], CheckFn]:
    def wrap(fn: CheckFn) -> CheckFn:
        INVARIANTS[name] = Invariant(name, rule, doc, params, fn, scope, proposed)
        return fn
    return wrap


def _i(ps: ParamSet, k: str) -> int:
    return ps.as_int(k)


# --- prices -----------------------------------------------------------------------------------------

@_inv("min_fill_fast", "L9", "pFastMinFill = ceil(pFastWindow / 2)", ("pFastWindow", "pFastMinFill"))
def _min_fill_fast(ps: ParamSet, _: Context) -> list[str]:
    want = ceil_div(_i(ps, "pFastWindow"), 2)
    return [] if _i(ps, "pFastMinFill") == want else [f"pFastMinFill={_i(ps, 'pFastMinFill')} != {want}"]


@_inv("min_fill_mid_slow", "L9", "pMid/pSlowMinFill = ceil(2W / 3)",
      ("pMidWindow", "pMidMinFill", "pSlowWindow", "pSlowMinFill"))
def _min_fill_mid_slow(ps: ParamSet, _: Context) -> list[str]:
    out = []
    for w in ("pMid", "pSlow"):
        want = ceil_div(2 * _i(ps, f"{w}Window"), 3)
        if _i(ps, f"{w}MinFill") != want:
            out.append(f"{w}MinFill={_i(ps, f'{w}MinFill')} != {want}")
    return out


@_inv("window_order", "PRICE-1", "pFastWindow < pMidWindow < pSlowWindow",
      ("pFastWindow", "pMidWindow", "pSlowWindow"))
def _window_order(ps: ParamSet, _: Context) -> list[str]:
    f, m, s = _i(ps, "pFastWindow"), _i(ps, "pMidWindow"), _i(ps, "pSlowWindow")
    return [] if 0 < f < m < s else [f"windows {f} / {m} / {s} not strictly increasing"]


# --- activation -------------------------------------------------------------------------------------

@_inv("activation_order", "spec:481 (ACT-4/ACT-6)",
      "enforcementFloor < participationFloor <= enforcementResume < activationThreshold <= signalWindow",
      ("enforcementFloor", "participationFloor", "enforcementResume", "activationThreshold", "signalWindow"))
def _activation_order(ps: ParamSet, _: Context) -> list[str]:
    ef, pf, er = _i(ps, "enforcementFloor"), _i(ps, "participationFloor"), _i(ps, "enforcementResume")
    at, sw = _i(ps, "activationThreshold"), _i(ps, "signalWindow")
    ok = 0 < ef < pf <= er < at <= sw
    return [] if ok else [f"{ef} < {pf} <= {er} < {at} <= {sw} does not hold"]


# --- abandonment --------------------------------------------------------------------------------------

@_inv("abandon_ge_grace", "W21", "abandonBlocks >= grace", ("abandonBlocks", "grace"))
def _abandon_ge_grace(ps: ParamSet, _: Context) -> list[str]:
    a, g = _i(ps, "abandonBlocks"), _i(ps, "grace")
    return [] if a >= g else [f"abandonBlocks {a} < grace {g}"]


def runbook_length(ps: ParamSet, ctx: Context) -> int:
    """Freeze-then-fix runbook length (PLAN §5.4): detect (≤ signalWindow) + the W19 window +
    the M14 release lead + the operator buffer."""
    sw = _i(ps, "signalWindow")
    return sw + sw + ctx.release_lead_blocks + ctx.runbook_buffer_blocks


@_inv("abandon_ge_runbook", "PLAN §5.4 (W19, M14)", "abandonBlocks >= freeze-then-fix runbook length",
      ("abandonBlocks", "signalWindow"), scope="mainnet-scale", proposed=True)
def _abandon_ge_runbook(ps: ParamSet, ctx: Context) -> list[str]:
    a, need = _i(ps, "abandonBlocks"), runbook_length(ps, ctx)
    return [] if a >= need else [f"abandonBlocks {a} < runbook length {need}"]


# --- release --------------------------------------------------------------------------------------------

@_inv("start_configured", "M2", "startHeight >= 1", ("startHeight",))
def _start_configured(ps: ParamSet, _: Context) -> list[str]:
    return [] if _i(ps, "startHeight") >= 1 else ["startHeight must be >= 1 (genesis has no height push)"]


@_inv("sunset", "L8 (ACT-5)",
      "enforceUntilHeight = startHeight + BLOCKS_PER_YEAR, never past the next network upgrade "
      "(0 = no sunset, regtest only)",
      ("startHeight", "enforceUntilHeight"))
def _sunset(ps: ParamSet, ctx: Context) -> list[str]:
    s, u = _i(ps, "startHeight"), _i(ps, "enforceUntilHeight")
    if ps.is_regtest_scale and u == 0:
        return []
    out = []
    if u != s + BLOCKS_PER_YEAR:
        out.append(f"enforceUntilHeight {u} != startHeight + {BLOCKS_PER_YEAR} = {s + BLOCKS_PER_YEAR}")
    if ctx.next_upgrade_height is not None and u > ctx.next_upgrade_height:
        out.append(f"sunset {u} past the next network upgrade at {ctx.next_upgrade_height}")
    return out


@_inv("release_lead", "M14", "startHeight >= release tip + 16,128 (checked when a tip is given)",
      ("startHeight",), scope="mainnet-scale")
def _release_lead(ps: ParamSet, ctx: Context) -> list[str]:
    if ctx.release_tip is None:
        return []
    need = ctx.release_tip + ctx.release_lead_blocks
    s = _i(ps, "startHeight")
    return [] if s >= need else [f"startHeight {s} < tip {ctx.release_tip} + {ctx.release_lead_blocks}"]


@_inv("regtest_zero_meanings", "M13 / spec §3.1",
      "sigmaRefBps, supplyCapBps, attestArmMin > 0 at mainnet scale (0 has a regtest-only meaning)",
      ("sigmaRefBps", "supplyCapBps", "attestArmMin"), scope="mainnet-scale")
def _regtest_zero_meanings(ps: ParamSet, _: Context) -> list[str]:
    return [f"{k} = 0 is a regtest-only setting" for k in ("sigmaRefBps", "supplyCapBps", "attestArmMin")
            if _i(ps, k) <= 0]


# --- classes ---------------------------------------------------------------------------------------------

@_inv("class_contiguous", "MINT-2 (V19)",
      "classMin[0] >= 1, classMin[i] <= classMax[i], classMin[i+1] = classMax[i] + 1",
      ("classMin[0]", "classMax[0]", "classMin[1]", "classMax[1]", "classMin[2]", "classMax[2]"))
def _class_contiguous(ps: ParamSet, _: Context) -> list[str]:
    lo, hi = ps.array("classMin"), ps.array("classMax")
    out = []
    if lo[0] < 1:
        out.append(f"classMin[0] {lo[0]} < 1")
    for i in range(len(lo)):
        if lo[i] > hi[i]:
            out.append(f"classMin[{i}] {lo[i]} > classMax[{i}] {hi[i]}")
        if i + 1 < len(lo) and lo[i + 1] != hi[i] + 1:
            out.append(f"classMin[{i + 1}] {lo[i + 1]} != classMax[{i}] + 1")
    return out


@_inv("class_locktime", "MINT-2 / CLTV", "classMax[2] + grace + tip < 500,000,000",
      ("classMax[2]", "grace", "startHeight"))
def _class_locktime(ps: ParamSet, ctx: Context) -> list[str]:
    tip = max(_i(ps, "startHeight"), ctx.release_tip or 0)
    top = ps.array("classMax")[-1] + _i(ps, "grace") + tip
    return [] if top < LOCKTIME_THRESHOLD else [f"claim height {top} reaches the CLTV time threshold"]


# --- volatility ------------------------------------------------------------------------------------------

@_inv("vol_step_divides", "SIGMA-1", "volWindow % volStep == 0", ("volWindow", "volStep"))
def _vol_step_divides(ps: ParamSet, _: Context) -> list[str]:
    w, s = _i(ps, "volWindow"), _i(ps, "volStep")
    return [] if s > 0 and w % s == 0 else [f"volWindow {w} not a multiple of volStep {s}"]


@_inv("vol_periods", "K13",
      "volPeriodsPerYear = BLOCKS_PER_YEAR / volStep exactly (mainnet scale); 8,760 on regtest",
      ("volPeriodsPerYear", "volStep"))
def _vol_periods(ps: ParamSet, _: Context) -> list[str]:
    v, s = _i(ps, "volPeriodsPerYear"), _i(ps, "volStep")
    if ps.is_regtest_scale:
        return [] if v == VOL_PERIODS_PER_YEAR_K13 else [f"regtest volPeriodsPerYear {v} != 8760 (K13)"]
    out = []
    if BLOCKS_PER_YEAR % s:
        out.append(f"volStep {s} does not divide BLOCKS_PER_YEAR")
    if v != BLOCKS_PER_YEAR // s:
        out.append(f"volPeriodsPerYear {v} != BLOCKS_PER_YEAR / volStep = {BLOCKS_PER_YEAR // s}")
    return out


# --- ratios ------------------------------------------------------------------------------------------------

@_inv("base_gt_halt", "HALT-2 / MINT-5", "baseRatioBps[i] > globalRatioHaltBps",
      ("baseRatioBps[0]", "baseRatioBps[1]", "baseRatioBps[2]", "globalRatioHaltBps"))
def _base_gt_halt(ps: ParamSet, _: Context) -> list[str]:
    h = _i(ps, "globalRatioHaltBps")
    return [f"baseRatioBps[{i}] {b} <= halt {h}" for i, b in enumerate(ps.array("baseRatioBps")) if b <= h]


@_inv("recap_double", "W16", "recapRatioBps = 2 × globalRatioHaltBps",
      ("recapRatioBps", "globalRatioHaltBps"))
def _recap_double(ps: ParamSet, _: Context) -> list[str]:
    r, h = _i(ps, "recapRatioBps"), _i(ps, "globalRatioHaltBps")
    return [] if r == 2 * h else [f"recapRatioBps {r} != 2 × {h}"]


@_inv("claim_emergency_order", "RED-4", "claimThresholdBps > emergencyRatioBps > 10,000",
      ("claimThresholdBps", "emergencyRatioBps"))
def _claim_emergency_order(ps: ParamSet, _: Context) -> list[str]:
    c, e = _i(ps, "claimThresholdBps"), _i(ps, "emergencyRatioBps")
    return [] if c > e > BPS else [f"{c} > {e} > {BPS} does not hold"]


# --- attestation ----------------------------------------------------------------------------------------

@_inv("bundle_size", "BUNDLE-1", "mSelect + kSlack <= bundleMax <= 6 and nSlots >= mSelect + kSlack",
      ("mSelect", "kSlack", "bundleMax", "nSlots"))
def _bundle_size(ps: ParamSet, _: Context) -> list[str]:
    m, k, b, n = _i(ps, "mSelect"), _i(ps, "kSlack"), _i(ps, "bundleMax"), _i(ps, "nSlots")
    out = []
    if not (m >= 1 and k >= 0 and m + k <= b <= BUNDLE_MAX_PUSH):
        out.append(f"mSelect + kSlack = {m + k} <= bundleMax {b} <= {BUNDLE_MAX_PUSH} does not hold")
    if n < m + k:
        out.append(f"nSlots {n} < mSelect + kSlack {m + k}")
    return out


@_inv("quantile_sum", "bundle statistic", "qLowBps + qHighBps = 10,000", ("qLowBps", "qHighBps"))
def _quantile_sum(ps: ParamSet, _: Context) -> list[str]:
    lo, hi = _i(ps, "qLowBps"), _i(ps, "qHighBps")
    return [] if lo + hi == BPS and 0 < lo < hi else [f"qLow {lo} + qHigh {hi} != {BPS} (or qLow >= qHigh)"]


@_inv("qlow_vs_entity", "PLAN §5.8 (capture)", "qLowBps > the largest single-entity weight share assumed",
      ("qLowBps",), proposed=True)
def _qlow_vs_entity(ps: ParamSet, ctx: Context) -> list[str]:
    if ctx.max_entity_share_bps is None:
        return []
    q = _i(ps, "qLowBps")
    return [] if q > ctx.max_entity_share_bps else [f"qLowBps {q} <= entity share {ctx.max_entity_share_bps}"]


@_inv("attest_max_age", "R4", "attestMaxAge = 2 × attestInterval", ("attestMaxAge", "attestInterval"))
def _attest_max_age(ps: ParamSet, _: Context) -> list[str]:
    a, k = _i(ps, "attestMaxAge"), _i(ps, "attestInterval")
    return [] if a == 2 * k else [f"attestMaxAge {a} != 2 × {k}"]


@_inv("emergency_persist_lt_ttl", "NOT-1", "emergencyPersist < emergencyNoticeTtl",
      ("emergencyPersist", "emergencyNoticeTtl"))
def _emergency_persist(ps: ParamSet, _: Context) -> list[str]:
    p, t = _i(ps, "emergencyPersist"), _i(ps, "emergencyNoticeTtl")
    return [] if 0 < p < t else [f"emergencyPersist {p} >= ttl {t}"]


@_inv("bond_lock_year", "REG-A1", "bondMinLock = BLOCKS_PER_YEAR", ("bondMinLock",), scope="mainnet-scale")
def _bond_lock_year(ps: ParamSet, _: Context) -> list[str]:
    b = _i(ps, "bondMinLock")
    return [] if b == BLOCKS_PER_YEAR else [f"bondMinLock {b} != {BLOCKS_PER_YEAR}"]


@_inv("age_cap_u32", "bond weight", "0 < ageCap < 2^32", ("ageCap",))
def _age_cap(ps: ParamSet, _: Context) -> list[str]:
    a = _i(ps, "ageCap")
    return [] if 0 < a < 2**32 else [f"ageCap {a} outside (0, 2^32)"]


# --- amounts ----------------------------------------------------------------------------------------

@_inv("amount_order", "MINT-2 / XFER-1", "minOutput <= minMint <= maxMint <= maxOutput",
      ("minOutput", "minMint", "maxMint", "maxOutput"))
def _amount_order(ps: ParamSet, _: Context) -> list[str]:
    a, b, c, d = (_i(ps, k) for k in ("minOutput", "minMint", "maxMint", "maxOutput"))
    return [] if 0 < a <= b <= c <= d else [f"{a} <= {b} <= {c} <= {d} does not hold"]


def min_mint_collateral_zat(ps: ParamSet, price_microusd: int) -> int:
    """Collateral of a ``minMint`` vault in the lowest-ratio class at σ-multiplier 1×
    (``RequiredCollateral`` = ceil(cents · ratio · COIN / pMint), math.h; cents → µUSD inside)."""
    ratio = min(ps.array("baseRatioBps"))
    cents = _i(ps, "minMint")
    # cents × bps is µUSD (1 cent at 10,000 bps = 10,000 µUSD), so no unit factor is needed.
    return ceil_div(cents * ratio * COIN, price_microusd)


@_inv("fee_floor_mintable", "MINT-5", "4·feeMin <= collateral of a minMint vault at the worst covered price",
      ("feeMin", "minMint", "baseRatioBps[2]"))
def _fee_floor(ps: ParamSet, ctx: Context) -> list[str]:
    col = min_mint_collateral_zat(ps, ctx.worst_price_microusd)
    need = 4 * _i(ps, "feeMin")
    return [] if need <= col else [f"4·feeMin {need} zat > minMint collateral {col} zat at "
                                   f"{ctx.worst_price_microusd} µUSD"]


# --- protocol constants ----------------------------------------------------------------------------------

@_inv("protocol_constants", "params.h", "tokenValue = TOKEN_VALUE, refWindow = REF_WINDOW",
      ("tokenValue", "TOKEN_VALUE", "refWindow", "REF_WINDOW"))
def _protocol_constants(ps: ParamSet, _: Context) -> list[str]:
    out = []
    if ps["tokenValue"] != ps["TOKEN_VALUE"]:
        out.append("tokenValue != TOKEN_VALUE")
    if ps["refWindow"] != ps["REF_WINDOW"]:
        out.append("refWindow != REF_WINDOW")
    if not 0 <= _i(ps, "DEFAULT_REF_LAG") <= _i(ps, "MAX_REF_LAG"):
        out.append("DEFAULT_REF_LAG outside [0, MAX_REF_LAG]")
    return out


def check_all(ps: ParamSet, context: Context | None = None, *,
              only: Mapping[str, Invariant] | None = None) -> list[Violation]:
    """Run every applicable invariant; returns the violations (empty = admissible)."""
    ctx = context or Context()
    out: list[Violation] = []
    for inv in (only or INVARIANTS).values():
        if inv.scope == "mainnet-scale" and ps.is_regtest_scale:
            continue
        for msg in inv.fn(ps, ctx):
            out.append(Violation(inv.name, inv.rule, msg, inv.params))
    return out


def applicable(ps: ParamSet) -> list[Invariant]:
    """Invariants that run for this set's scale."""
    return [i for i in INVARIANTS.values() if not (i.scope == "mainnet-scale" and ps.is_regtest_scale)]

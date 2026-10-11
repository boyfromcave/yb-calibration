"""Option D fees, the $100,000 ``maxMint`` and the supply cap (quantum plan D-Q-23, D-Q-24; §9 items 3, 5).

An analytic study like ``pq_growth``: no simulation, no price pulls, no sweeps; it reads only numbers
already in the repo (the frozen 2026-10-04 order books for depth, the policy's adoption scenarios,
``pq_growth``'s byte sizes) and runs in well under a second. Five questions:

1. **Supply cap reality.** YEC circulating supply from the emission schedule (Zcash's slow start and
   halvings, Ycash's Blossom at 1,100,000), market cap and the 15 % cap at $0.40 / $1 / $5 / $10, how
   many $100,000 vaults fit, and what MINT-6 actually caps (coinbase issued *since the start height*,
   soft above ``recapRatioBps``). Concentration: one vault at 25 % / 50 % of the cap against the same
   debt over 100 vaults, through the G3 claimant model (slippage against the frozen bid depth) and the
   idiosyncratic owner risks the policy declares.
2. **Option D protocol fee**: tiered on the debt in dollars at ``pMint`` (1.0 % ≤ $1k, 0.5 % to $10k,
   0.1 % above, floor $1 per action), charged at mint and redeem; the early-redeem fee 5 / 2.5 / 1 % of
   the debt. Effective rate by vault size, fee tables at five prices, yearly revenue to pools and
   attestors under the adoption scenarios plus a whale case, against today's ``max(0.5 YEC, 0.15 %
   of collateral)`` and the G8 attestor-cost assumptions; early fee on debt vs collateral vs the
   in-term claim penalty.
3. **Network fee** in dollars per KB ($0.02 PQ, $0.05 vault / Yellowback), floor 1× relay, cap
   ``-maxtxfee``: per path in dollars and zat, miner income per byte against P2PKH parity, and where
   the 0.1 YEC cap binds.
4. **Robustness**: ±70 % price moves between mint and redeem (the fee is fixed in YEC at ``pMint``),
   the floors, and ``bondMin`` / ``residualMinZat`` under the same dollars-at-``pMint`` pattern.
5. **Sensitivity** ±50 % on every band rate and both $/KB targets; one recommended parameter set.

Node constants are cited at ``wt/q-dd`` (ycash-dd ``upgrade/quantum-vaults`` @ ``d41026209``) and
``ref/ycash`` v4.5.0 for the emission schedule; tests pin them.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from itertools import pairwise
from pathlib import Path

from ybcal.config import Policy
from ybcal.studies import pq_growth as PQ
from ybcal.units import BLOCKS_PER_DAY, BLOCKS_PER_YEAR, BPS, COIN

OWNER_WP = "D-Q-23 / D-Q-24 (quantum plan)"

# ---------------------------------------------------------------------------------------------------
# Emission schedule (ref/ycash v4.5.0: chainparams.cpp:91-93,127,131; main.cpp:2067-2100 GetBlockSubsidy)

MAX_SUBSIDY_ZAT = 12_5 * COIN // 10  #: 12.5 YEC
SLOW_START_INTERVAL = 20_000
PRE_BLOSSOM_HALVING_INTERVAL = 840_000
BLOSSOM_HEIGHT = 1_100_000  #: Ycash's Blossom (75 s blocks, subsidy halved per block) — not Zcash's 653,600
POST_BLOSSOM_HALVING_INTERVAL = 2 * PRE_BLOSSOM_HALVING_INTERVAL  #: 1,680,000
YCASH_FORK_HEIGHT = 570_000  #: the chain is Zcash's below this; the emission is unchanged by the fork


def halving(height: int) -> int:
    """``Consensus::Params::Halving`` (consensus/params.cpp:36-47)."""
    shift = SLOW_START_INTERVAL // 2
    if height < BLOSSOM_HEIGHT:
        return (height - shift) // PRE_BLOSSOM_HALVING_INTERVAL
    scaled = (BLOSSOM_HEIGHT - shift) * 2
    return (scaled + (height - BLOSSOM_HEIGHT)) // POST_BLOSSOM_HALVING_INTERVAL


def block_subsidy(height: int) -> int:
    """``GetBlockSubsidy`` in zat (main.cpp:2067-2100)."""
    s = MAX_SUBSIDY_ZAT
    if height < SLOW_START_INTERVAL // 2:
        return s // SLOW_START_INTERVAL * height
    if height < SLOW_START_INTERVAL:
        return s // SLOW_START_INTERVAL * (height + 1)
    h = halving(height)
    if h >= 64:
        return 0
    if height >= BLOSSOM_HEIGHT:
        return (s // 2) >> h
    return s >> h


def halving_heights(upto: int) -> list[int]:
    """Heights at which the per-block subsidy changes (first block of each new value), below ``upto``."""
    out: list[int] = []
    prev = block_subsidy(SLOW_START_INTERVAL)
    h = SLOW_START_INTERVAL
    while h < upto:
        # subsidy is piecewise constant; jump to the next candidate boundary
        if h < BLOSSOM_HEIGHT:
            nxt = min(
                BLOSSOM_HEIGHT, SLOW_START_INTERVAL // 2 + (halving(h) + 1) * PRE_BLOSSOM_HALVING_INTERVAL
            )
        else:
            shift = SLOW_START_INTERVAL // 2
            scaled = (BLOSSOM_HEIGHT - shift) * 2
            nxt = BLOSSOM_HEIGHT + (halving(h) + 1) * POST_BLOSSOM_HALVING_INTERVAL - scaled
        if nxt >= upto:
            break
        cur = block_subsidy(nxt)
        if cur != prev:
            out.append(nxt)
            prev = cur
        h = nxt
    return out


def cumulative_supply_zat(height: int) -> int:
    """Coinbase issued by blocks ``0 .. height - 1`` (exact, segment sums; the slow start is summed)."""
    total = sum(block_subsidy(h) for h in range(min(height, SLOW_START_INTERVAL)))
    if height <= SLOW_START_INTERVAL:
        return total
    bounds = [SLOW_START_INTERVAL, *halving_heights(height), height]
    for a, b in pairwise(bounds):
        total += (b - a) * block_subsidy(a)
    return total


def issued_since(start: int, end: int) -> int:
    """Coinbase of blocks ``start .. end - 1`` (MINT-6's ``issuedZat`` base: state.cpp:1456, from
    ``Snapshot()``)."""
    return cumulative_supply_zat(end) - cumulative_supply_zat(start)


def height_today(policy: Policy, today: date | None = None) -> int:
    """``release_tip`` + 1,152 blocks a day since ``release_tip_date`` (policy; no node is read)."""
    d = today or date.today()
    t0 = date.fromisoformat(str(policy.release_tip_date))
    return int(policy.release_tip) + max(0, (d - t0).days) * BLOCKS_PER_DAY


# ---------------------------------------------------------------------------------------------------
# Node constants on the quantum line (wt/q-dd d41026209, src/yellowback/params.cpp)

FEE_MIN_ZAT = 50_000_000  #: feeMin 0.5 YEC (params.cpp:34)
FEE_BPS = 15  #: feeBps (H-4, params.cpp:35)
ATTEST_FEE_BPS = 5_000  #: attestFeeBps: the attestor output is this share of FEE-1, paid on top (AFEE-1)
SUPPLY_CAP_BPS = 1_500  #: params.cpp:41
RECAP_RATIO_BPS = 50_000  #: params.cpp:43; class C bypasses the cap (W20 / IT-5)
BASE_RATIO_BPS = (30_000, 40_000, 50_000)  #: params.cpp:48-50 (in-term plan §3)
EARLY_REDEEM_FEE_BPS = (500, 250, 100)  #: params.cpp:52 (D-IT-16), today on the collateral
CLAIM_THRESHOLD_BPS = 12_500  #: θ (D-IT-2)
MAX_MINT_USD_TODAY = 2_500.0  #: params.cpp:17 (H-12)
MAX_MINT_USD = 100_000.0  #: D-Q-24
MIN_MINT_USD = 100.0
BOND_MIN_YEC = 20_000  #: params.cpp:86
RESIDUAL_MIN_ZAT = 100_000  #: params.cpp:84
BOND_FLOOR_YEC_RELEASE = 2_000  #: D-Q-23a: a dollar bondMin keeps a 2,000-YEC floor (= BOND_FLOOR_YEC below)
PRICE_MAX_USD = 4_294.967295  #: D-Q-22
MAX_TX_FEE_ZAT = 10_000_000  #: -maxtxfee default 0.1 YEC (dd main.h:74, 6 main.h:77)
SEATS = 6  #: mSelect 4 + kSlack 2 (registry), the seated attestor set paid by AFEE-1
POOLS = 5  #: pools identified in the frozen pool-names.csv (2026-10-04)

PRICES_USD: tuple[float, ...] = (0.40, 1.0, 5.0, 10.0, PRICE_MAX_USD)
SHOCKS: tuple[float, ...] = (-0.70, 0.0, 0.70)  #: the devnet shock (±70 %)

# ---------------------------------------------------------------------------------------------------
# Option D (D-Q-23)


@dataclass(frozen=True)
class OptionD:
    """The protocol fee bands (upper bound in USD, marginal rate), the floor, the early fee by class,
    and the network-fee targets in $/KB."""

    bands: tuple[tuple[float, float], ...] = ((1_000.0, 0.010), (10_000.0, 0.005), (math.inf, 0.001))
    floor_usd: float = 1.0
    early_bps: tuple[int, int, int] = EARLY_REDEEM_FEE_BPS
    pq_usd_per_kb: float = 0.02
    yb_usd_per_kb: float = 0.05
    floor_mult: float = 1.0  #: network-fee floor as a multiple of the relay floor (100 zat/KB)
    maxtxfee_zat: int = MAX_TX_FEE_ZAT
    maxtxfee_usd: float | None = None  #: a dollar cap at the attested price, never below ``maxtxfee_zat``
    #: an absolute YEC ceiling on the cap (-yellowbackmaxtxfee); None = no ceiling
    maxtxfee_ceiling_zat: int | None = None

    def scaled(self, **mult: float) -> OptionD:
        """A copy with band rate ``b0..b2`` or ``pq`` / ``yb`` targets multiplied (sensitivity)."""
        bands = tuple((hi, r * mult.get(f"b{i}", 1.0)) for i, (hi, r) in enumerate(self.bands))
        return OptionD(
            bands,
            self.floor_usd,
            self.early_bps,
            self.pq_usd_per_kb * mult.get("pq", 1.0),
            self.yb_usd_per_kb * mult.get("yb", 1.0),
            self.floor_mult,
            self.maxtxfee_zat,
            self.maxtxfee_usd,
            self.maxtxfee_ceiling_zat,
        )

    def cap_zat(self, price_usd: float) -> int:
        cap = (
            max(self.maxtxfee_zat, usd_to_zat(self.maxtxfee_usd, price_usd))
            if self.maxtxfee_usd
            else self.maxtxfee_zat
        )
        return min(cap, self.maxtxfee_ceiling_zat) if self.maxtxfee_ceiling_zat is not None else cap


#: The set this study recommends (§5): the decided bands and targets, the network floor at P2PKH parity
#: (the pq study's m = 50) and a dollar -maxtxfee for the vault / Yellowback builders.
DEFAULT = OptionD()
RECOMMENDED = OptionD(floor_mult=50.0, maxtxfee_usd=2.0)


# ---------------------------------------------------------------------------------------------------
# The release constants as built (quantum plan D-Q-23..D-Q-26; ycash-dd ``q/fees`` @ ``430002b16``)

#: The network-fee set the node implements: RECOMMENDED plus review M-1's absolute ceiling
#: (``-yellowbackmaxtxfee`` 10 YEC, policy.h DEFAULT_YED_MAX_TX_FEE), which plain PQ transfers share (D-Q-26).
RELEASE = OptionD(floor_mult=50.0, maxtxfee_usd=2.0, maxtxfee_ceiling_zat=10 * COIN)


@dataclass(frozen=True)
class ReleaseConstants:
    """The Yellowback fee-line constants a release bakes in, as implemented on ycash-dd ``q/fees``
    (``430002b16``; ``src/yellowback/params.cpp`` SetCommon, ``src/policy/policy.h``), with the decision
    each comes from. The ycash6 port must carry the same values (fix both node lines)."""

    max_mint_usd: float = 100_000.0  #: D-Q-24, params.cpp:20 maxMint 10,000,000 cents (H-12 reversed)
    bands: tuple[tuple[float, float], ...] = OptionD().bands  #: D-Q-23 FEE-1', params.cpp:41-43
    floor_usd: float = 1.0  #: D-Q-23, params.cpp:44 feeFloorCents 100 (on the debt at pMint, per action)
    early_bps: tuple[int, int, int] = (500, 250, 100)  #: IT-9' of the debt at pMint, params.cpp:61
    attest_fee_bps: int = ATTEST_FEE_BPS  #: AFEE-1': half the protocol fee, params.cpp:95
    pq_usd_per_kb: float = 0.02  #: D-Q-26 plain PQ ($0.02/KB), policy.h:129
    yb_usd_per_kb: float = 0.05  #: D-Q-26 vault / Yellowback ($0.05/KB), policy.h:130
    network_floor_zat_per_kb: int = 5_000  #: D-Q-23a, policy.h:131; max(this, -pqfeerate) (review A-7)
    maxtxfee_usd: float = 2.0  #: D-Q-23a, policy.h:132; max(-maxtxfee 0.1 YEC, $2 at the price)
    maxtxfee_ceiling_zat: int = 10 * COIN  #: review M-1, policy.h:133 (-yellowbackmaxtxfee)
    plain_pq_shares_cap: bool = True  #: D-Q-26: PQMaxTxFee() = YedMaxTxFee()
    residual_min_usd: float = 0.48  #: D-Q-23a RED-5', params.cpp:94 residualMinCents 48 at pMint
    residual_min_floor_zat: int = RESIDUAL_MIN_ZAT  #: RED-5 floor, params.cpp:93
    supply_cap_bps: int = SUPPLY_CAP_BPS  #: MINT-6: 15 % ...
    #: ... of the circulating supply x xMint(R) (D-Q-25; no longer the coinbase issued since the start)
    supply_cap_base: str = "circulating"
    max_vault_share_bps: int = 1_000  #: D-Q-23a MINT-6b, params.cpp:17 (per vault, not per owner)
    max_vault_share_floor_usd: float = 10_000.0  #: params.cpp:18 vaultShareFloorCents 1,000,000
    bond_min_usd: float = 8_000.0  #: D-Q-23a / D-Q-25 recommendation at the registration price ...
    #: ... floor 2,000 YEC; NOT yet in the node (q/fees keeps bondMin 20,000 YEC, params.cpp:96)
    bond_min_floor_yec: float = float(BOND_FLOOR_YEC_RELEASE)


RELEASE_CONSTANTS = ReleaseConstants()


def protocol_fee_usd(debt_usd: float, d: OptionD = DEFAULT) -> float:
    """Marginal (tax-bracket) fee on the debt in dollars, at least ``floor_usd``; one action."""
    fee, lo = 0.0, 0.0
    for hi, rate in d.bands:
        if debt_usd <= lo:
            break
        fee += (min(debt_usd, hi) - lo) * rate
        lo = hi
    return max(fee, d.floor_usd) if debt_usd > 0 else 0.0


def effective_rate(debt_usd: float, d: OptionD = DEFAULT) -> float:
    return protocol_fee_usd(debt_usd, d) / debt_usd


def usd_to_zat(usd: float, price_usd: float) -> int:
    """Dollars to zat at ``price_usd`` per YEC (floor), the ``pMint`` conversion."""
    return int(usd / price_usd * COIN)


def early_fee_usd(debt_usd: float, cls: int, d: OptionD = DEFAULT) -> float:
    """IT-9 under Option D: ``earlyRedeemFeeBps[class]`` of the **debt**."""
    return debt_usd * d.early_bps[cls] / BPS


def early_fee_today_usd(debt_usd: float, cls: int, sigma_mult: float = 1.0) -> float:
    """IT-9 today: the same bps of the **collateral** (debt × base ratio × σ)."""
    return debt_usd * BASE_RATIO_BPS[cls] / BPS * sigma_mult * EARLY_REDEEM_FEE_BPS[cls] / BPS


def fee_today_zat(collateral_zat: int) -> int:
    """FEE-1 today: ``max(feeMin, collateral × feeBps / 10^4)`` (math.h:193-199)."""
    return max(FEE_MIN_ZAT, collateral_zat * FEE_BPS // BPS)


def collateral_zat(debt_usd: float, price_usd: float, cls: int = 0, sigma_mult: float = 1.0) -> int:
    """Collateral of a class-``cls`` vault at σ multiplier ``sigma_mult`` (MINT-5, no rounding)."""
    return int(debt_usd * BASE_RATIO_BPS[cls] / BPS * sigma_mult / price_usd * COIN)


def fee_today_usd(debt_usd: float, price_usd: float, cls: int = 0) -> float:
    """Today's FEE-1 in dollars at ``price_usd`` for a class-``cls`` vault of ``debt_usd``."""
    return fee_today_zat(collateral_zat(debt_usd, price_usd, cls)) / COIN * price_usd


def claim_penalty_max_usd(debt_usd: float) -> float:
    """The most an owner loses to an in-term claim: the claimant takes ``θ × debt`` worth at pClaim
    (RED-5), so the owner's loss above the debt is at most ``(θ − 1) × debt`` (in-term plan D-IT-16)."""
    return debt_usd * (CLAIM_THRESHOLD_BPS - BPS) / BPS


# ---------------------------------------------------------------------------------------------------
# 1. Supply, cap, concentration


@dataclass
class SupplyRow:
    price_usd: float
    supply_yec: float
    market_cap_usd: float
    cap_15pct_usd: float  #: the owner's framing: 15 % of the circulating market cap
    vaults_100k: int
    rule_cap_30d_usd: float  #: MINT-6's base: 15 % of coinbase issued since the start height
    rule_cap_365d_usd: float
    rule_cap_365d_vaults_100k: float
    rule_days_to_one_100k: (
        float  #: days of issuance before one $100k mint fits (classes A/B only; C bypasses)
    )


def supply_rows(height: int, prices: Sequence[float] = PRICES_USD) -> list[SupplyRow]:
    sup = cumulative_supply_zat(height) / COIN
    out = []
    for p in prices:
        cap = sup * p * SUPPLY_CAP_BPS / BPS
        c30 = issued_since(height, height + 30 * BLOCKS_PER_DAY) / COIN * p * SUPPLY_CAP_BPS / BPS
        c365 = issued_since(height, height + BLOCKS_PER_YEAR) / COIN * p * SUPPLY_CAP_BPS / BPS
        per_day = c30 / 30
        out.append(
            SupplyRow(
                p,
                sup,
                sup * p,
                cap,
                int(cap // MAX_MINT_USD),
                c30,
                c365,
                c365 / MAX_MINT_USD,
                MAX_MINT_USD / per_day,
            )
        )
    return out


#: Frozen 2026-10-04 order books (data/local/frozen-20261004/depth.csv, 87 snapshots, 3 venues): the
#: bid side a claimant sells into. ``p10`` is the policy's bad-side percentile (claimant_slippage_pctl 90).
BID_DEPTH_USD: dict[str, dict[str, float]] = {
    "2pct": {"p10": 37.0, "p50": 115.0},  # within 2 % of the best bid (G3's depth_p10_usd band)
    "10pct": {"p10": 791.0, "p50": 2_198.0},
    "30pct": {"p10": 3_709.0, "p50": 4_982.0},  # effectively the whole visible bid book
}
#: ``p10_daily_volume_usd`` PLACEHOLDER from policy/default.toml (no 24 h volume in the frozen books).
P10_DAILY_VOLUME_USD = 25_000.0


def slippage(value_usd: float, depth_usd: float, base_bps: float = 100.0, impact_bps: float = 200.0) -> float:
    """``sim.agents.slippage_bps`` as a fraction: base + impact × value / depth, capped at 99.99 %."""
    return min(base_bps + impact_bps * value_usd / depth_usd, 9_999.0) / BPS


def claim_executable(debt_usd: float, depth_usd: float, min_profit_bps: int = 200) -> bool:
    """G3's claimant test at the threshold: the claimant receives ``θ × debt`` worth of YEC, sells it with
    slippage and must net ``min_profit_bps`` of the debt after buying the YED it burns at par."""
    gross = debt_usd * CLAIM_THRESHOLD_BPS / BPS
    return gross * (1 - slippage(gross, depth_usd)) - debt_usd >= debt_usd * min_profit_bps / BPS


def max_executable_debt_usd(depth_usd: float, min_profit_bps: int = 200) -> float:
    """Largest debt whose in-term claim clears the claimant's profit floor at ``depth_usd`` (closed form:
    slippage ≤ 1 − (1 + π) / θ)."""
    s_max = 1 - (1 + min_profit_bps / BPS) / (CLAIM_THRESHOLD_BPS / BPS)
    if s_max <= 0.01:
        return 0.0
    value = (s_max * BPS - 100.0) / 200.0 * depth_usd
    return value / (CLAIM_THRESHOLD_BPS / BPS)


@dataclass
class Concentration:
    case: str
    n_vaults: int
    debt_each_usd: float
    total_debt_usd: float
    executable_at_p10_30pct: bool  #: one claim clears the profit floor against the whole p10 bid book
    executable_at_p50_30pct: bool
    sale_vs_p10_daily_volume: float  #: one claim's collateral sale as a multiple of p10 daily volume
    unexecutable_share: float  #: share of the debt whose claim cannot execute (p50 whole-book depth)
    owner_absence_exposure: float  #: share of the debt one owner's absence spell puts in the claim path
    lost_key_exposure: float  #: share of the debt one lost owner key makes permanent bad debt


def concentration(
    cap_usd: float, shares: Sequence[float] = (0.25, 0.50), n_spread: int = 100
) -> list[Concentration]:
    out = []
    d50 = BID_DEPTH_USD["30pct"]["p50"]
    d10 = BID_DEPTH_USD["30pct"]["p10"]
    for s in shares:
        debt = cap_usd * s
        single = Concentration(
            f"one vault at {s:.0%} of the cap",
            1,
            debt,
            debt,
            claim_executable(debt, d10),
            claim_executable(debt, d50),
            debt * CLAIM_THRESHOLD_BPS / BPS / P10_DAILY_VOLUME_USD,
            0.0 if claim_executable(debt, d50) else 1.0,
            1.0,
            1.0,
        )
        each = debt / n_spread
        ok = claim_executable(each, d50)
        spread = Concentration(
            f"the same {s:.0%} over {n_spread} vaults",
            n_spread,
            each,
            debt,
            claim_executable(each, d10),
            ok,
            each * CLAIM_THRESHOLD_BPS / BPS / P10_DAILY_VOLUME_USD,
            0.0 if ok else 1.0,
            1.0 / n_spread,
            1.0 / n_spread,
        )
        out += [single, spread]
    return out


# ---------------------------------------------------------------------------------------------------
# 2. Fee tables and revenue

SIZES_USD: tuple[float, ...] = (100, 250, 500, 1_000, 2_500, 5_000, 10_000, 25_000, 50_000, 100_000)


def fee_table(d: OptionD, prices: Sequence[float] = PRICES_USD) -> list[dict[str, object]]:
    """Per vault size: Option D mint / redeem / early fees and today's rule, in dollars and in YEC at each
    price."""
    rows = []
    for debt in SIZES_USD:
        f = protocol_fee_usd(debt, d)
        row: dict[str, object] = {
            "debt_usd": debt,
            "fee_usd": f,
            "effective_rate": f / debt,
            "mint_total_usd": f * (1 + ATTEST_FEE_BPS / BPS),  # pool FEE-1 + attestor AFEE-1 on top
            "round_trip_rate": (f * (1 + ATTEST_FEE_BPS / BPS) + f) / debt,
            "early_usd": [early_fee_usd(debt, c, d) for c in range(3)],
            "early_today_usd": [early_fee_today_usd(debt, c) for c in range(3)],
            "claim_penalty_max_usd": claim_penalty_max_usd(debt),
        }
        for p in prices:
            row[f"fee_yec@{p:g}"] = usd_to_zat(f, p) / COIN
            row[f"today_usd@{p:g}"] = fee_today_usd(debt, p)
            row[f"today_rate@{p:g}"] = fee_today_usd(debt, p) / debt
        rows.append(row)
    return rows


#: Mint-size models (ASSUMPTIONS: no real size data exists). ``supply``: the mean mint that keeps the
#: scenario's ``yed_supply_usd`` outstanding when every vault lives ``MEAN_LIFE_DAYS`` (the middle of the
#: in-term classes, 30 d – 1 y); ``h12``: the personas' log-uniform between minMint and H-12's $2,500;
#: ``d24``: log-uniform between minMint and D-Q-24's $100,000 (its mean is dominated by the top decade).
MEAN_LIFE_DAYS = 180.0
SIZE_MODELS: tuple[str, ...] = ("supply", "h12", "d24")


def _loguniform_grid(lo: float, hi: float, n: int = 2_000) -> list[float]:
    return [lo * (hi / lo) ** ((i + 0.5) / n) for i in range(n)]


def mean_debt_usd(model: str, scenario: Mapping[str, float]) -> float:
    if model == "supply":
        return float(scenario["yed_supply_usd"]) / (float(scenario["mints_per_day"]) * MEAN_LIFE_DAYS)
    hi = MAX_MINT_USD_TODAY if model == "h12" else MAX_MINT_USD
    g = _loguniform_grid(MIN_MINT_USD, hi)
    return sum(g) / len(g)


def mean_fee_usd(model: str, scenario: Mapping[str, float], d: OptionD, price_usd: float, rule: str) -> float:
    """Mean protocol fee per action under the size model: Option D or today's FEE-1 at ``price_usd``."""
    f = (lambda x: protocol_fee_usd(x, d)) if rule == "option_d" else (lambda x: fee_today_usd(x, price_usd))
    if model == "supply":
        return f(mean_debt_usd(model, scenario))
    hi = MAX_MINT_USD_TODAY if model == "h12" else MAX_MINT_USD
    g = _loguniform_grid(MIN_MINT_USD, hi)
    return sum(f(x) for x in g) / len(g)


@dataclass
class Revenue:
    scenario: str
    size_model: str
    rule: str
    price_usd: float
    mints_per_year: float
    redeems_per_year: float
    mean_debt_usd: float
    pool_usd_year: float
    attestor_usd_year: float
    per_pool_usd_month: float
    per_seat_usd_month: float
    bond_cost_usd_month: float  #: G8: bondMin × price × 5 % APR / 12
    seat_covers_bond: bool
    seat_meets_floor: bool  #: policy attestor_min_monthly_revenue_usd
    pool_meets_floor: bool  #: policy pool_min_monthly_revenue_usd


def revenue(policy: Policy, d: OptionD, prices: Sequence[float] = PRICES_USD) -> list[Revenue]:
    """Yearly FEE-1 / AFEE-1 revenue: mints pay the fee to the pool plus ``attestFeeBps`` of it to the
    seated attestors; owner redeems pay the pool only (state.cpp:591, no attestor share on the owner path).
    Early-redeem fees are left out (no early-redeem rate is declared anywhere)."""
    out = []
    sc = {k: dict(v) for k, v in policy.adoption_scenarios.items()}
    # the whale case: ten $100k vaults a year, each redeemed once (a fixed size, no model)
    sc["whale"] = {
        "yed_supply_usd": 10 * MAX_MINT_USD,
        "mints_per_day": 10 / 365,
        "redeems_per_day": 10 / 365,
    }
    apr = float(policy.bond_opportunity_cost_apr)
    a_floor = float(policy.attestor_min_monthly_revenue_usd)
    p_floor = float(policy.pool_min_monthly_revenue_usd)
    for name, v in sc.items():
        m, r = float(v["mints_per_day"]) * 365, float(v["redeems_per_day"]) * 365
        for model in ("whale",) if name == "whale" else SIZE_MODELS:
            for p in prices:
                bond = BOND_MIN_YEC * p * apr / 12
                for rule in ("option_d", "today"):
                    if name == "whale":
                        debt = MAX_MINT_USD
                        f = protocol_fee_usd(debt, d) if rule == "option_d" else fee_today_usd(debt, p)
                    else:
                        debt = mean_debt_usd(model, v)
                        f = mean_fee_usd(model, v, d, p, rule)
                    pool = (m + r) * f
                    att = m * f * ATTEST_FEE_BPS / BPS
                    per_seat = att / SEATS / 12
                    per_pool = pool / POOLS / 12
                    out.append(
                        Revenue(
                            name,
                            model,
                            rule,
                            p,
                            m,
                            r,
                            debt,
                            pool,
                            att,
                            per_pool,
                            per_seat,
                            bond,
                            per_seat >= bond,
                            per_seat >= a_floor,
                            per_pool >= p_floor,
                        )
                    )
    return out


def revenue_at(
    rows: Sequence[Revenue], scenario: str, model: str, rule: str = "option_d", price: float = 0.40
) -> Revenue:
    return next(
        r
        for r in rows
        if r.scenario == scenario and r.size_model == model and r.rule == rule and r.price_usd == price
    )


# ---------------------------------------------------------------------------------------------------
# 3. Network fee in dollars per KB

#: The paths priced by each target: plain PQ spends ($0.02/KB) vs vault / Yellowback ($0.05/KB).
PQ_PATHS = ("transfer", "residual_sweep")
YB_PATHS = ("mint", "redeem", "claim", "bridge_recover")


def network_fee_zat(size: int, usd_per_kb: float, price_usd: float, d: OptionD) -> tuple[int, str]:
    """``clamp(target, floor, cap)``: (zat, which bound applied: "target" | "floor" | "cap")."""
    target = usd_to_zat(usd_per_kb * size / 1000, price_usd)
    floor = PQ.size_fee(size, d.floor_mult)
    cap = d.cap_zat(price_usd)
    if target < floor:
        return floor, "floor"
    if target > cap:
        return cap, "cap"
    return target, "target"


def cap_bind_price_usd(size: int, usd_per_kb: float, d: OptionD) -> float:
    """The YEC price below which the cap binds for a ``size``-byte transaction (0 when the dollar cap
    exceeds the dollar target, i.e. the cap can only bind through its YEC floor)."""
    fee_usd = usd_per_kb * size / 1000
    if d.maxtxfee_usd and d.maxtxfee_usd >= fee_usd:
        return 0.0
    return fee_usd / (d.maxtxfee_zat / COIN)


P2PKH_ZAT_PER_BYTE = PQ.DEFAULT_FEE / PQ.references()["p2pkh_1in2out"].size  #: 4.08


def parity_price_usd(usd_per_kb: float) -> float:
    """The YEC price above which the $/KB target pays fewer zat per byte than a P2PKH payment."""
    return usd_per_kb / 1000 * COIN / P2PKH_ZAT_PER_BYTE


def network_rows(
    d: OptionD, variant: str = "falcon", prices: Sequence[float] = PRICES_USD
) -> list[dict[str, object]]:
    ps = PQ.paths(variant)
    rows = []
    for name in PQ_PATHS + YB_PATHS:
        tx = ps[name]
        target = d.pq_usd_per_kb if name in PQ_PATHS else d.yb_usd_per_kb
        row: dict[str, object] = {
            "path": name,
            "variant": variant,
            "size": tx.size,
            "target_usd_per_kb": target,
            "fee_usd_target": target * tx.size / 1000,
            "cap_binds_below_usd": cap_bind_price_usd(tx.size, target, d),
            "parity_fails_above_usd": parity_price_usd(target),
            "pqfeerate_m50_zat": PQ.fee_dd(tx, 50) if tx.is_pq else PQ.DEFAULT_FEE,
        }
        for p in prices:
            zat, bound = network_fee_zat(tx.size, target, p, d)
            row[f"zat@{p:g}"] = zat
            row[f"bound@{p:g}"] = bound
            row[f"usd@{p:g}"] = zat / COIN * p
            row[f"zat_per_byte@{p:g}"] = zat / tx.size
            row[f"beats_p2pkh@{p:g}"] = zat / tx.size >= P2PKH_ZAT_PER_BYTE
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------------------------------
# 4. Robustness: shocks, floors, bondMin / residualMinZat in dollars


def shock_rows(d: OptionD, p_mint: float = 0.40, debt_usd: float = 1_000.0) -> list[dict[str, float]]:
    """The redeem fee is fixed in YEC at ``pMint``; what it is worth in dollars after a move."""
    out = []
    f_usd = protocol_fee_usd(debt_usd, d)
    f_zat = usd_to_zat(f_usd, p_mint)
    for s in SHOCKS:
        p_now = p_mint * (1 + s)
        early_zat = usd_to_zat(early_fee_usd(debt_usd, 0, d), p_mint)
        out.append(
            {
                "shock": s,
                "p_now_usd": p_now,
                "fee_zat": f_zat,
                "fee_usd_now": f_zat / COIN * p_now,
                "fee_usd_at_pmint": f_usd,
                "early_A_usd_now": early_zat / COIN * p_now,
                "floor_usd_now": usd_to_zat(d.floor_usd, p_mint) / COIN * p_now,
                "collateral_ratio_A": BASE_RATIO_BPS[0] / BPS * (1 + s),
                "claimable": BASE_RATIO_BPS[0] / BPS * (1 + s) < CLAIM_THRESHOLD_BPS / BPS,
            }
        )
    return out


@dataclass
class DollarConstant:
    name: str
    today_yec: float
    today_usd_at: dict[str, float]
    proposed_usd: float
    floor_yec: float
    proposed_yec_at: dict[str, float]
    rationale: str


def dollar_constants(d: OptionD, prices: Sequence[float] = PRICES_USD) -> list[DollarConstant]:
    """``bondMin`` and ``residualMinZat`` restated as dollars at the price of the act, with a YEC floor."""
    bond_usd = BOND_MIN_YEC * 0.40  # G8 sized it at $0.40 (the 2026-10 reference price)
    sweep = PQ.paths("falcon")["residual_sweep"].size
    sweep_usd = d.pq_usd_per_kb * sweep / 1000
    resid_usd = round(3 * sweep_usd, 2)  # G9: 3 × the cost of spending it (dust_spend_multiple)
    out = [
        DollarConstant(
            "bondMin",
            BOND_MIN_YEC,
            {f"{p:g}": BOND_MIN_YEC * p for p in prices},
            bond_usd,
            2_000.0,
            {f"{p:g}": max(bond_usd / p, 2_000.0) for p in prices},
            "G8's bond_secure compares capture capital with the YED cap, both in dollars at the same "
            "price, so a dollar bond keeps the ratio; converted once at the attested price of the "
            "registration (REG-A1) and stored as the bond's YEC amount; the 2,000 YEC floor keeps a seat "
            "non-trivial if YEC ever trades near PRICE_MIN.",
        ),
        DollarConstant(
            "residualMinZat",
            RESIDUAL_MIN_ZAT / COIN,
            {f"{p:g}": RESIDUAL_MIN_ZAT / COIN * p for p in prices},
            resid_usd,
            RESIDUAL_MIN_ZAT / COIN,
            {f"{p:g}": max(resid_usd / p, RESIDUAL_MIN_ZAT / COIN) for p in prices},
            "G9's rule (residual ≥ dust_spend_multiple = 3 × the cost of spending it): the owner's residual "
            f"is a PQPKH(owner) output swept by an SLH-DSA spend of {sweep:,} bytes, ${sweep_usd:.3f} at the "
            "PQ target; converted at pMint like the fees (the vault already stores it); the 100,000-zat "
            "floor is today's value.",
        ),
    ]
    return out


# ---------------------------------------------------------------------------------------------------
# 5. Sensitivity and the recommendation


def sensitivity(policy: Policy, base: OptionD) -> list[dict[str, object]]:
    """±50 % on each band rate and each $/KB target: the effective rate at $100 / $1k / $10k / $100k, low-case
    attestor revenue per seat at $0.40, the whale round trip, and the cap-bind price of an SLH redeem."""
    out = []
    knobs = ("b0", "b1", "b2", "pq", "yb")
    redeem = PQ.paths("pq_p2pkh")["redeem"].size
    sweep = PQ.paths("pq_p2pkh")["residual_sweep"].size
    for k in ("base", *knobs):
        for mult in (1.0,) if k == "base" else (0.5, 1.5):
            d = base if k == "base" else base.scaled(**{k: mult})
            low = revenue_at(revenue(policy, d, (0.40,)), "low", "supply")
            out.append(
                {
                    "knob": k,
                    "mult": mult,
                    "rate_100": effective_rate(100, d),
                    "rate_1k": effective_rate(1_000, d),
                    "rate_10k": effective_rate(10_000, d),
                    "rate_100k": effective_rate(100_000, d),
                    "whale_round_trip_usd": protocol_fee_usd(100_000, d) * (2 + ATTEST_FEE_BPS / BPS),
                    "low_seat_usd_month_at_0.40": low.per_seat_usd_month,
                    "low_pool_usd_month_at_0.40": low.per_pool_usd_month,
                    "slh_redeem_cap_binds_below_usd": cap_bind_price_usd(redeem, d.yb_usd_per_kb, d),
                    "slh_sweep_cap_binds_below_usd": cap_bind_price_usd(sweep, d.pq_usd_per_kb, d),
                    "yb_parity_fails_above_usd": parity_price_usd(d.yb_usd_per_kb),
                    "pq_parity_fails_above_usd": parity_price_usd(d.pq_usd_per_kb),
                }
            )
    return out


@dataclass
class Recommendation:
    bands: tuple[tuple[float, float], ...]
    floor_usd: float
    early_bps: tuple[int, int, int]
    pq_usd_per_kb: float
    yb_usd_per_kb: float
    network_floor_zat_per_kb: int
    maxtxfee_usd: float
    maxtxfee_floor_yec: float
    max_vault_share_bps: int
    max_vault_share_floor_usd: float
    bond_min_usd: float
    bond_min_floor_yec: float
    residual_min_usd: float
    residual_min_floor_zat: int
    reasons: list[str] = field(default_factory=list)


def recommend(policy: Policy, d: OptionD, height: int) -> Recommendation:
    sup = supply_rows(height, (0.40,))[0]
    redeem = PQ.paths("pq_p2pkh")["redeem"].size
    rows = revenue(policy, d, (0.40,))
    low_s, low_t = revenue_at(rows, "low", "supply"), revenue_at(rows, "low", "supply", "today")
    low_h, mid_s = revenue_at(rows, "low", "h12"), revenue_at(rows, "mid", "supply")
    mid_d, whale = revenue_at(rows, "mid", "d24"), revenue_at(rows, "whale", "whale")
    parity_m = math.ceil(PQ.break_even_mult(P2PKH_ZAT_PER_BYTE))
    rt = 2 + ATTEST_FEE_BPS / BPS  # a round trip: mint (with the attestor share) + redeem
    rates = ", ".join(f"{effective_rate(x, d):.3%} at ${x:,}" for x in (100, 1_000, 10_000, 100_000))
    d0 = OptionD()
    bind = cap_bind_price_usd(redeem, d.yb_usd_per_kb, d0)
    target_yec = usd_to_zat(d.yb_usd_per_kb * redeem / 1000, 0.40) / COIN
    exec50 = max_executable_debt_usd(BID_DEPTH_USD["30pct"]["p50"])
    exec2 = max_executable_debt_usd(BID_DEPTH_USD["2pct"]["p10"])
    rec = Recommendation(
        d.bands,
        d.floor_usd,
        d.early_bps,
        d.pq_usd_per_kb,
        d.yb_usd_per_kb,
        RECOMMENDED.floor_mult * PQ.RELAY_FLOOR_ZAT_PER_KB,
        RECOMMENDED.maxtxfee_usd or 0.0,
        MAX_TX_FEE_ZAT / COIN,
        1_000,
        10_000.0,
        BOND_MIN_YEC * 0.40,
        2_000.0,
        dollar_constants(d)[1].proposed_usd,
        RESIDUAL_MIN_ZAT,
    )
    rec.reasons = [
        f"Bands as decided: effective rate {rates}; a $100k vault's round trip (mint with the attestor "
        f"share + redeem) is ${protocol_fee_usd(100_000, d) * rt:,.0f} against "
        f"${fee_today_usd(100_000, 0.40) * rt:,.0f} under today's rule at $0.40 (0.15 % of a 300 % "
        "collateral "
        "at mint and redeem, plus the attestor share); a $100 vault pays "
        f"${protocol_fee_usd(100, d) * rt:.2f} "
        f"against ${fee_today_usd(100, 0.40) * rt:.2f}. Flat in dollars at every price, where today's rule "
        f"reaches ${fee_today_usd(100, PRICE_MAX_USD):,.0f} per action at the ceiling.",
        f"Marginal bands never reward splitting a vault (ten $10k vaults pay "
        f"${10 * protocol_fee_usd(10_000, d):,.0f} where one $100k pays "
        f"${protocol_fee_usd(100_000, d):,.0f}); "
        f"the $1 floor equals the 1 % band at minMint, so it never binds above $100.",
        f"Revenue at $0.40, supply-consistent sizing (mean mint ${low_s.mean_debt_usd:,.0f} in the low "
        "case): "
        f"${low_s.pool_usd_year:,.0f}/yr to pools (${low_s.per_pool_usd_month:,.0f}/pool/month, floor $50) "
        f"and ${low_s.attestor_usd_year:,.0f}/yr to the {SEATS} seats (${low_s.per_seat_usd_month:,.2f}/seat/"
        f"month against the bond's ${low_s.bond_cost_usd_month:,.0f}/month opportunity cost and the policy's "
        f"$50 floor; today's rule gives ${low_t.per_seat_usd_month:,.2f}); mid case "
        f"${mid_s.per_seat_usd_month:,.0f}/seat/month. Log-uniform sizing to $2,500 (H-12) gives "
        f"${low_h.per_seat_usd_month:,.0f}/seat/month in the low case, to $100k "
        f"${mid_d.per_seat_usd_month:,.0f} "
        f"in the mid case; ten whales a year add ${whale.attestor_usd_year:,.0f}/yr. The bands pay the "
        "attestor "
        f"set only from the mid case on; the adequacy judgement is the owner's (the study reports the bands' "
        f"yield, as asked).",
        "Early fee on the debt: class A 5 % of the debt is a fifth of the 25 % the owner can lose to an "
        "in-term "
        "claim (θ = 125 %), where the collateral rule charged 15 % of the debt at 300 %: redeeming early "
        "beats "
        "being claimed by a wide margin in every class, so no owner is pushed toward the claim path; and the "
        "fee no longer penalises over-collateralising. No new gaming path: the owner cannot choose pMint, a "
        "claim needs the price to fall under θ, and the debt is fixed at mint.",
        f"Network fee: keep the $/KB targets but set the floor at {parity_m}× relay (P2PKH parity, the pq "
        f"study's m = 50 → 5,000 zat/KB) instead of 1×: above ${parity_price_usd(d.yb_usd_per_kb):,.0f}/YEC "
        f"the $0.05/KB target pays fewer zat per byte than a P2PKH payment, and the floor keeps the miners' "
        f"per-byte income at parity there.",
        f"-maxtxfee 0.1 YEC binds on the {redeem:,}-byte SLH redeem below ${bind:.2f}/YEC — at today's $0.40 "
        f"the target is {target_yec:.2f} YEC, ten times the cap. A fee capped at 0.1 YEC still pays "
        f"{MAX_TX_FEE_ZAT / redeem:,.0f} zat/byte, 300× parity, so the cap does no harm to relay; but the "
        f"dollar target is then fiction at any price under ${bind:.0f}. Wallet default: a dollar cap, "
        f"max($2 at the attested price, 0.1 YEC), for the vault / Yellowback builders only (policy, not "
        f"consensus); $2 covers every path's target up to a 40 KB transaction and is "
        f"{2 / PRICE_MAX_USD * COIN:,.0f} zat at the ceiling.",
        f"Supply cap: at $0.40 the circulating cap is ${sup.cap_15pct_usd:,.0f} ({sup.vaults_100k} vaults of "
        "$100k) but MINT-6 caps against coinbase issued since the start height: "
        f"${sup.rule_cap_30d_usd:,.0f} "
        f"after 30 days, ${sup.rule_cap_365d_usd:,.0f} after a year — one $100k class-A/B vault fits only "
        f"after {sup.rule_days_to_one_100k:,.0f} days; class C (500 %) bypasses the cap. $100k vaults are "
        f"class-C-only for the first years at $0.40, and one class-C whale is "
        f"{MAX_MINT_USD / sup.rule_cap_365d_usd:.1f}× the whole first-year cap of the bound classes.",
        f"Concentration: a $100k debt's claim must sell $125k of YEC into a bid book of "
        f"${BID_DEPTH_USD['30pct']['p50']:,.0f} (whole visible book, p50) — the claimant model says no claim "
        f"executes above a debt of ${exec50:,.0f} at that depth (${exec2:,.0f} in G3's own 2 % band at p10), "
        "so a whale vault that goes under θ is bad debt until the market deepens, and one owner's absence "
        "or "
        "lost key puts the whole position in the claim path; 100 vaults of the same total are each "
        "executable "
        "and expose 1 % per owner. Recommend a per-vault share-of-cap limit: maxVaultShareBps = 1,000 of "
        "the "
        "MINT-6 cap with a $10,000 floor (so the rule never binds below H-12's scale), one more MINT check "
        "on "
        f"state the snapshot already holds; and the owner should know that $100,000 is "
        f"{MAX_MINT_USD / exec50:.0f}× the largest claim the frozen books can absorb, so D-Q-24's maxMint "
        "is a "
        f"depth bet, not a parameter the data supports.",
        "bondMin and residualMinZat follow the fee pattern: $8,000 at the registration price (floor 2,000 "
        "YEC) "
        f"and ${rec.residual_min_usd:.2f} at pMint (floor 100,000 zat).",
    ]
    return rec


# ---------------------------------------------------------------------------------------------------
# 6. Attestor economics (quantum plan D-Q-25, red flag 3 of D-Q-23a): adoption tier × mint-size model ×
# YEC price × bond rule → revenue per seat and return on bond; the attestor share and the bond are the
# levers. ``ybcal fees --attestors`` writes attestors.md.

ATTEST_PRICES: tuple[float, ...] = (0.40, 1.0, 5.0, 10.0)
ATTEST_SHARES: tuple[float, ...] = (0.50, 0.75, 1.00)  #: attestFeeBps candidates (50 % today)
ATTEST_MODELS: tuple[str, ...] = ("supply", "d24")  #: supply-consistent mean; log-uniform to $100k
BOND_FLOOR_YEC = BOND_FLOOR_YEC_RELEASE  #: D-Q-23a: a dollar bondMin keeps a 2,000-YEC floor
BOND_RULES: tuple[str, ...] = ("usd2k", "usd8k", "yec20k")
BOND_RULE_LABEL = {"usd2k": "$2,000 pinned", "usd8k": "$8,000 pinned", "yec20k": "20,000 YEC"}
WHALE_MINTS_PER_YEAR = 10  #: the whale tier: ten $100k vaults a year on top of mid
HARM_SEATS = 7  #: hardening plan C-4 / D-RD-ATT-4: harmful capture needs 7 of 9 seats by splitting
LOW_MEAN_DEBT_USD = 50_000.0 / (2.0 * MEAN_LIFE_DAYS)  #: ≈ $139, the low tier's supply-consistent mean


def adoption_tiers(policy: Policy) -> dict[str, dict[str, float]]:
    """The tiers, explicit: the policy's low / mid / high (mints, redeems per day) with ``pq_growth``'s
    transfers and claims per day, plus ``whale`` = mid with ten $100k mints (and redeems) a year on top.
    Only mints pay the attestors (AFEE-1); redeems pay the pool, transfers and claims pay network fees."""
    sc = PQ.scenarios(policy)
    out: dict[str, dict[str, float]] = {}
    for name in ("low", "mid", "high"):
        v = sc[name]
        out[name] = {
            "mints_per_day": v["mints_per_day"],
            "redeems_per_day": v["redeems_per_day"],
            "transfers_per_day": v["transfers_per_day"],
            "claims_per_day": v["claims_per_day"],
            "yed_supply_usd": float(policy.adoption_scenarios[name]["yed_supply_usd"]),
            "whale_mints_per_year": 0.0,
        }
    w = dict(out["mid"])
    w["whale_mints_per_year"] = float(WHALE_MINTS_PER_YEAR)
    out["whale"] = w
    return out


def bond_yec(rule: str, price_usd: float) -> float:
    """The bond in YEC under a rule: ``yec20k`` is 20,000 YEC; ``usdNk`` is $N,000 at the registration
    price with the 2,000-YEC floor (D-Q-23a)."""
    if rule == "yec20k":
        return float(BOND_MIN_YEC)
    usd = {"usd2k": 2_000.0, "usd8k": 8_000.0}[rule]
    return max(usd / price_usd, float(BOND_FLOOR_YEC))


def bond_usd(rule: str, price_usd: float) -> float:
    return bond_yec(rule, price_usd) * price_usd


def tier_mean_fee_usd(tier: Mapping[str, float], model: str, d: OptionD = DEFAULT) -> float:
    """Mean protocol fee per ordinary mint under the size model (the whale mints are added separately)."""
    return mean_fee_usd(model, tier, d, 0.40, "option_d")


def set_revenue_usd_year(tier: Mapping[str, float], model: str, share: float, d: OptionD = DEFAULT) -> float:
    """AFEE-1 to the whole seated set per year: ``share`` of FEE-1 on every mint (price-invariant: the fee
    is dollars at pMint)."""
    ordinary = tier["mints_per_day"] * 365 * tier_mean_fee_usd(tier, model, d)
    whale = tier["whale_mints_per_year"] * protocol_fee_usd(MAX_MINT_USD, d)
    return (ordinary + whale) * share


def seat_revenue_usd_year(tier: Mapping[str, float], model: str, share: float, d: OptionD = DEFAULT) -> float:
    return set_revenue_usd_year(tier, model, share, d) / SEATS


@dataclass
class SeatRevenue:
    tier: str
    size_model: str
    mints_per_year: float
    mean_debt_usd: float
    mean_fee_usd: float
    whale_fee_usd_year: float
    set_usd_year: float  #: at the current 50 % share
    seat_usd_year: float
    seat_usd_month: float
    seat_usd_month_by_share: dict[str, float]


def seat_revenue_rows(policy: Policy, d: OptionD = DEFAULT) -> list[SeatRevenue]:
    out = []
    for name, t in adoption_tiers(policy).items():
        for model in ATTEST_MODELS:
            s = seat_revenue_usd_year(t, model, ATTEST_FEE_BPS / BPS, d)
            out.append(
                SeatRevenue(
                    name,
                    model,
                    t["mints_per_day"] * 365 + t["whale_mints_per_year"],
                    mean_debt_usd(model, t),
                    tier_mean_fee_usd(t, model, d),
                    t["whale_mints_per_year"] * protocol_fee_usd(MAX_MINT_USD, d),
                    set_revenue_usd_year(t, model, ATTEST_FEE_BPS / BPS, d),
                    s,
                    s / 12,
                    {f"{sh:.0%}": seat_revenue_usd_year(t, model, sh, d) / 12 for sh in ATTEST_SHARES},
                )
            )
    return out


@dataclass
class BondReturn:
    tier: str
    size_model: str
    price_usd: float
    bond_rule: str
    bond_yec: float
    bond_usd: float
    seat_usd_year: float  #: at the current 50 % share
    return_apr: float  #: seat revenue / bond value, per year
    cost_of_capital_usd_year: float
    clears_cost_of_capital: bool


def bond_return_rows(policy: Policy, d: OptionD = DEFAULT) -> list[BondReturn]:
    apr = float(policy.bond_opportunity_cost_apr)
    out = []
    for name, t in adoption_tiers(policy).items():
        for model in ATTEST_MODELS:
            s = seat_revenue_usd_year(t, model, ATTEST_FEE_BPS / BPS, d)
            for p in ATTEST_PRICES:
                for rule in BOND_RULES:
                    b = bond_usd(rule, p)
                    out.append(
                        BondReturn(
                            name, model, p, rule, bond_yec(rule, p), b, s, s / b, b * apr, s >= b * apr
                        )
                    )
    return out


def break_even_mints_per_day(mean_fee: float, share: float, bond: float, apr: float) -> float:
    """Mints a day at which a seat's yearly AFEE-1 revenue equals the bond's cost of capital (closed form:
    ``apr × bond × SEATS / (365 × mean_fee × share)``)."""
    return apr * bond * SEATS / (365 * mean_fee * share)


@dataclass
class BreakEven:
    size_model: str
    mean_fee_usd: float
    share: float
    bond_rule: str
    bond_usd_by_price: dict[str, float]
    mints_per_day_by_price: dict[str, float]


def break_even_rows(policy: Policy, d: OptionD = DEFAULT) -> list[BreakEven]:
    """Break-even adoption for each share × bond rule, at each price. The supply-consistent fee is the
    low tier's (mean mint ≈ $139 → $1.39), the conservative end: that model's mean mint grows with the
    tier (mid $278, high $556)."""
    apr = float(policy.bond_opportunity_cost_apr)
    low = {"yed_supply_usd": 50_000.0, "mints_per_day": 2.0}
    out = []
    for model in ATTEST_MODELS:
        fee = mean_fee_usd(model, low, d, 0.40, "option_d")
        for share in ATTEST_SHARES:
            for rule in BOND_RULES:
                out.append(
                    BreakEven(
                        model,
                        fee,
                        share,
                        rule,
                        {f"{p:g}": bond_usd(rule, p) for p in ATTEST_PRICES},
                        {
                            f"{p:g}": break_even_mints_per_day(fee, share, bond_usd(rule, p), apr)
                            for p in ATTEST_PRICES
                        },
                    )
                )
    return out


def capture_rows(height: int) -> list[dict[str, float | str]]:
    """What the bond buys as security: the capital to split into ``HARM_SEATS`` seats against the YED cap
    at each price — D-RD-ATT-4's yardstick (one year of MINT-6 cap growth, the old base) and D-Q-25's cap
    (15 % of circulating supply × price)."""
    sup = cumulative_supply_zat(height) / COIN
    year_yec = issued_since(height, height + BLOCKS_PER_YEAR) / COIN * SUPPLY_CAP_BPS / BPS
    out: list[dict[str, float | str]] = []
    for p in ATTEST_PRICES:
        cap = sup * p * SUPPLY_CAP_BPS / BPS
        for rule in BOND_RULES:
            cap_capital = HARM_SEATS * bond_usd(rule, p)
            out.append(
                {
                    "price_usd": p,
                    "bond_rule": rule,
                    "bond_usd": bond_usd(rule, p),
                    "capture_capital_usd": cap_capital,
                    "cap_d_q_25_usd": cap,
                    "capture_to_cap": cap_capital / cap,
                    "cap_year_old_rule_usd": year_yec * p,
                    "capture_to_old_cap_year": cap_capital / (year_yec * p),
                    "bond_per_seat_to_meet_d_rd_att_4_usd": cap / HARM_SEATS,
                }
            )
    return out


def attestor_economics(policy: Policy, d: OptionD = DEFAULT, today: date | None = None) -> dict[str, object]:
    height = height_today(policy, today)
    tiers = adoption_tiers(policy)
    low_fee = mean_fee_usd("supply", tiers["low"], d, 0.40, "option_d")
    return {
        "constants": {
            "height": height,
            "seats": SEATS,
            "attest_fee_bps": ATTEST_FEE_BPS,
            "shares": list(ATTEST_SHARES),
            "prices_usd": list(ATTEST_PRICES),
            "bond_rules": {r: BOND_RULE_LABEL[r] for r in BOND_RULES},
            "bond_floor_yec": BOND_FLOOR_YEC,
            "cost_of_capital_apr": float(policy.bond_opportunity_cost_apr),
            "attestor_min_monthly_revenue_usd": float(policy.attestor_min_monthly_revenue_usd),
            "harm_seats": HARM_SEATS,
            "low_mean_debt_usd": LOW_MEAN_DEBT_USD,
            "low_mean_fee_usd": low_fee,
            "d24_mean_debt_usd": mean_debt_usd("d24", tiers["low"]),
            "d24_mean_fee_usd": mean_fee_usd("d24", tiers["low"], d, 0.40, "option_d"),
            "whale_fee_usd": protocol_fee_usd(MAX_MINT_USD, d),
        },
        "tiers": tiers,
        "seat_revenue": [asdict(r) for r in seat_revenue_rows(policy, d)],
        "bond_return": [asdict(r) for r in bond_return_rows(policy, d)],
        "break_even": [asdict(r) for r in break_even_rows(policy, d)],
        "capture": capture_rows(height),
    }


def _pct(x: float) -> str:
    return f"{x:.1%}" if x < 10 else f"{x:.0%}"


def _row(rows: Sequence[Mapping[str, object]], **where: object) -> Mapping[str, object]:
    return next(r for r in rows if all(r[k] == v for k, v in where.items()))


def render_attestors_markdown(res: Mapping[str, object]) -> str:
    C: Mapping[str, object] = res["constants"]  # type: ignore[assignment]
    tiers: Mapping[str, Mapping[str, float]] = res["tiers"]  # type: ignore[assignment]
    seat_rows: list[Mapping[str, object]] = res["seat_revenue"]  # type: ignore[assignment]
    ret_rows: list[Mapping[str, object]] = res["bond_return"]  # type: ignore[assignment]
    be_rows: list[Mapping[str, object]] = res["break_even"]  # type: ignore[assignment]
    cap_rows: list[Mapping[str, object]] = res["capture"]  # type: ignore[assignment]
    apr = float(C["cost_of_capital_apr"])  # type: ignore[arg-type]
    seats = int(C["seats"])  # type: ignore[arg-type]
    share_pct = int(C["attest_fee_bps"]) / 100  # type: ignore[arg-type]
    low_fee = float(C["low_mean_fee_usd"])  # type: ignore[arg-type]
    d24_fee = float(C["d24_mean_fee_usd"])  # type: ignore[arg-type]
    prices = " / ".join(f"${p:,.2f}" for p in ATTEST_PRICES)
    L: list[str] = []
    L.append("# Attestor economics — adoption tier × YEC price × bond rule (D-Q-25, red flag 3)\n")
    L.append(
        f"Analytic, frozen data only (`ybcal fees --attestors`). {seats} seated attestors (`mSelect` 4 + "
        f"`kSlack` 2); the attestor share is `attestFeeBps` = {share_pct:.0f} % of FEE-1, paid by the "
        "minter **on top of** the pool fee (AFEE-1), on mints only — owner redeems pay the pool alone, "
        "transfers and claims pay network fees. Under Option D the fee is dollars at `pMint`, so **every "
        "revenue figure below is price-invariant**; only the bond's dollar value (and so the return on it) "
        f"moves with the YEC price. Cost of capital: {apr:.0%}/yr on the bond's dollar value (policy "
        "`bond_opportunity_cost_apr`), stated separately from revenue throughout.\n"
    )
    # 1. tiers
    L.append("## 1. Adoption tiers and mint-size models\n")
    L.append("| Tier | mints/day | redeems/day | transfers/day | claims/day | YED outstanding | on top |")
    L.append("|---|---:|---:|---:|---:|---:|---|")
    for name, t in tiers.items():
        w = t["whale_mints_per_year"]
        extra = f"{w:.0f} × ${MAX_MINT_USD:,.0f} mints a year (each redeemed once)" if w else "—"
        L.append(
            f"| {name} | {t['mints_per_day']:g} | {t['redeems_per_day']:g} | {t['transfers_per_day']:,.0f} | "
            f"{t['claims_per_day']:g} | ${t['yed_supply_usd']:,.0f} | {extra} |"
        )
    L.append(
        "\nlow / mid / high are the policy's `adoption_scenarios` (mints, redeems, YED outstanding) with "
        "`pq_growth`'s transfers and claims; whale is mid plus ten $100k vaults a year. Mint-size models "
        "(no real size data): **supply-consistent** — the mean mint that keeps the tier's YED outstanding "
        f"over {MEAN_LIFE_DAYS:.0f}-day mean lives (${LOW_MEAN_DEBT_USD:,.0f} in the low tier, "
        f"${2 * LOW_MEAN_DEBT_USD:,.0f} mid, ${4 * LOW_MEAN_DEBT_USD:,.0f} high; fee ${low_fee:.2f} / "
        f"${2 * low_fee:.2f} / ${4 * low_fee:.2f} per mint); **log-uniform to $100k** (`d24`) — mean mint "
        f"${C['d24_mean_debt_usd']:,.0f}, mean fee ${d24_fee:.2f} per mint, dominated by the top decade. "
        f"A $100k whale mint pays ${C['whale_fee_usd']:.0f}.\n"
    )
    # 2. revenue
    L.append("## 2. Revenue per seat (price-invariant)\n")
    L.append(
        "| Tier | sizes | mints/yr | mean mint | fee/mint | whale fees $/yr | set $/yr at 50 % | "
        "**$/seat/month at 50 %** | $/seat/yr at 50 % | $/seat/month at 75 % | at 100 % |"
    )
    L.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in seat_rows:
        by: Mapping[str, float] = r["seat_usd_month_by_share"]  # type: ignore[assignment]
        L.append(
            f"| {r['tier']} | {r['size_model']} | {r['mints_per_year']:,.0f} | "
            f"{_usd(float(r['mean_debt_usd']))} | ${float(r['mean_fee_usd']):.2f} | "  # type: ignore[arg-type]
            f"{_usd(float(r['whale_fee_usd_year']))} | {_usd(float(r['set_usd_year']))} | "  # type: ignore[arg-type]
            f"**{_usd(float(r['seat_usd_month']))}** | {_usd(float(r['seat_usd_year']))} | "  # type: ignore[arg-type]
            f"{_usd(by['75%'])} | {_usd(by['100%'])} |"
        )
    low_s = _row(seat_rows, tier="low", size_model="supply")
    mid_s = _row(seat_rows, tier="mid", size_model="supply")
    low_100 = low_s["seat_usd_month_by_share"]["100%"]  # type: ignore[index]
    L.append(
        f"\nThe policy's attestor floor is ${C['attestor_min_monthly_revenue_usd']:.0f}/seat/month. Raising "
        "the share raises the minter's cost (the attestor output is on top of FEE-1, so 100 % doubles what "
        "a mint pays in protocol fee) and never changes the pool's cut; it scales every row linearly and "
        f"changes no verdict: at the low tier 100 % is still {_usd(low_100)}/seat/month.\n"
    )
    # 3. return on bond
    L.append("## 3. Return on bond (% per year, at the 50 % share)\n")
    L.append(
        "Return = a seat's yearly revenue ÷ the bond's dollar value at the price shown. Bond rules: "
        "(a) **20,000 YEC** (today's `bondMin`), (b) **$8,000 pinned** at the registration price with the "
        f"{BOND_FLOOR_YEC:,}-YEC floor (D-Q-23a), and $2,000 pinned with the same floor. Bond value by "
        "price:\n"
    )
    L.append("| Bond rule | " + " | ".join(f"${p:,.2f}" for p in ATTEST_PRICES) + " |")
    L.append("|---|" + "---:|" * len(ATTEST_PRICES))
    for rule in BOND_RULES:
        cells = " | ".join(f"{bond_yec(rule, p):,.0f} YEC = {_usd(bond_usd(rule, p))}" for p in ATTEST_PRICES)
        L.append(f"| {BOND_RULE_LABEL[rule]} | {cells} |")
    L.append(
        f"\nThe {BOND_FLOOR_YEC:,}-YEC floor binds on the $2,000 bond above $1 and on the $8,000 bond above "
        "$4, so at $5 and $10 both pinned bonds are the floor ($10,000 / $20,000) and the two rules are the "
        "same bond.\n"
    )
    L.append("| Tier | sizes | $/seat/yr | " + " | ".join(BOND_RULE_LABEL[r] for r in BOND_RULES) + " |")
    L.append("|---|---|---:|" + "---|" * len(BOND_RULES))
    for s in seat_rows:
        cells = []
        for rule in BOND_RULES:
            parts = []
            for p in ATTEST_PRICES:
                x = _row(ret_rows, tier=s["tier"], size_model=s["size_model"], price_usd=p, bond_rule=rule)
                mark = "**" if x["clears_cost_of_capital"] else ""
                parts.append(f"{mark}{_pct(float(x['return_apr']))}{mark}")  # type: ignore[arg-type]
            cells.append(" / ".join(parts))
        year = _usd(float(s["seat_usd_year"]))  # type: ignore[arg-type]
        L.append(f"| {s['tier']} | {s['size_model']} | {year} | " + " | ".join(cells) + " |")
    L.append(
        f"\nCells: return at {prices}; **bold** clears the {apr:.0%} cost of capital. The 20,000-YEC bond's "
        "return falls 25× from $0.40 to $10 at the same adoption; the pinned bond's falls only through its "
        "YEC floor (2.5× from $1 to $10).\n"
    )
    # 4. break-even
    L.append(f"## 4. Break-even adoption: mints/day for return ≥ {apr:.0%}\n")
    L.append(
        "Closed form: `mints/day = apr × bond × seats / (365 × fee per mint × share)`. Fee per mint: the "
        f"low tier's supply-consistent ${low_fee:.2f} (conservative: that model's mean grows with the tier) "
        f"and log-uniform-to-$100k ${d24_fee:.2f}. For reference: low 2, mid 10, high 50 mints a day.\n"
    )
    heads = " | ".join(f"mints/day at ${p:,.2f}" for p in ATTEST_PRICES)
    L.append(f"| sizes | share | bond | {heads} |")
    L.append("|---|---:|---|" + "---:|" * len(ATTEST_PRICES))
    for r in be_rows:
        be: Mapping[str, float] = r["mints_per_day_by_price"]  # type: ignore[assignment]
        cells = " | ".join(f"{be[f'{p:g}']:,.1f}" for p in ATTEST_PRICES)
        label = BOND_RULE_LABEL[str(r["bond_rule"])]
        L.append(f"| {r['size_model']} | {r['share']:.0%} | {label} | {cells} |")
    # 5. slashing
    L.append("\n## 5. The slashing side: what a seat risks against what it earns\n")
    L.append(
        "| YEC price | bond rule | bond | capture capital (7 seats) | D-Q-25 cap (15 % of supply × price) | "
        "capture ÷ cap | old base: one year of MINT-6 growth | capture ÷ old | bond/seat to meet D-RD-ATT-4 |"
    )
    L.append("|---:|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in cap_rows:
        f = {k: float(v) for k, v in r.items() if k != "bond_rule"}  # type: ignore[arg-type]
        L.append(
            f"| ${f['price_usd']:,.2f} | {BOND_RULE_LABEL[str(r['bond_rule'])]} | {_usd(f['bond_usd'])} | "
            f"{_usd(f['capture_capital_usd'])} | {_usd(f['cap_d_q_25_usd'])} | {f['capture_to_cap']:.1%} | "
            f"{_usd(f['cap_year_old_rule_usd'])} | {f['capture_to_old_cap_year']:.2f}× | "
            f"{_usd(f['bond_per_seat_to_meet_d_rd_att_4_usd'])} |"
        )
    cap40 = _row(cap_rows, price_usd=0.40, bond_rule="usd8k")
    cap2k = _row(cap_rows, price_usd=0.40, bond_rule="usd2k")
    c8, c2 = float(cap40["capture_capital_usd"]), float(cap2k["capture_capital_usd"])  # type: ignore[arg-type]
    low_yr, mid_yr = _usd(float(low_s["seat_usd_year"])), _usd(float(mid_s["seat_usd_year"]))  # type: ignore[arg-type]
    L.append(
        "\n**Judgement.** A seat risks its whole bond — burned on equivocation (two signatures over two "
        "spends of one outpoint; upgrade plan §3.6, EQV-1 for prices) or on a `SET_REMOVE burn=1` by 7 "
        f"members — to earn {low_yr}/yr at the low tier and {mid_yr}/yr at mid: the fee never pays for a "
        "seat's misbehaviour, so the stake-at-risk argument holds at any bond in this range. What the bond's "
        "*size* buys is the cost of capturing the quantile by seat splitting (§3.2: a set referenced by id "
        f"can be slashed; C-4 / D-RD-ATT-4: 7 of 9 seats). That cost is {_usd(c8)} with the $8,000 bond and "
        f"{_usd(c2)} with $2,000 at $0.40 — against a D-Q-25 cap of {_usd(float(cap40['cap_d_q_25_usd']))}, "  # type: ignore[arg-type]
        f"i.e. {float(cap40['capture_to_cap']):.1%} / {float(cap2k['capture_to_cap']):.1%} of what a "  # type: ignore[arg-type]
        "captured price could mint. D-RD-ATT-4's yardstick (capture capital ≥ one year of cap growth) was "
        f"met by the old issuance base ({float(cap40['capture_to_old_cap_year']):.2f}× with $8,000) and "  # type: ignore[arg-type]
        "**is not met by any affordable bond under D-Q-25** — it would take "
        f"{_usd(float(cap40['bond_per_seat_to_meet_d_rd_att_4_usd']))} a seat at $0.40. So the security "  # type: ignore[arg-type]
        "argument the upgrade plan makes does not rest on the bond alone: it rests on H-1 "
        "(`pMint = min(xMint, aMint)`: a captured attestor set can raise the mint price only up to the "
        "pools' own price), the arming bar (H-2, seven matured distinct bonds), slashing, and the per-vault "
        "share limit. A lower bond weakens the one leg it does carry — the splitting cost — "
        f"{c8 / c2:.0f}× (from {_usd(c8)} to {_usd(c2)}) and the equivocation stake {c8 / c2:.0f}×, for a "
        "return-on-bond gain that still does not make the low tier pay (§3). A lower bond buys no revenue; "
        "it only changes the denominator.\n"
    )
    # 6. recommendation
    L.append("## 6. Recommendation for launch\n")
    be8 = _row(be_rows, size_model="supply", share=0.5, bond_rule="usd8k")["mints_per_day_by_price"]
    be2 = _row(be_rows, size_model="supply", share=0.5, bond_rule="usd2k")["mints_per_day_by_price"]
    low_75 = low_s["seat_usd_month_by_share"]["75%"]  # type: ignore[index]
    L.append(
        "**Share 50 % (`attestFeeBps` 5,000), bond $8,000 pinned at the registration price with the "
        f"{BOND_FLOOR_YEC:,}-YEC floor.** Reasoning: (1) **revenue is set by adoption, not by bond size** — "
        "a seat's income is mints/day × fee × share ÷ 6 and contains no bond term; the bond only sets the "
        f"hurdle, and the $8,000 bond's hurdle ({be8['0.4']:.1f} mints/day at 50 %) is the mid tier, "  # type: ignore[index]
        "exactly where the Option D bands start paying the set anyway (§2); (2) the $2,000 bond lowers the "
        f"hurdle to {be2['0.4']:.1f} mints/day but cuts the splitting cost to {_usd(c2)} for the whole set "  # type: ignore[index]
        f"(§5), and the low tier still earns {_usd(float(low_s['seat_usd_month']))}/seat/month either way; "  # type: ignore[arg-type]
        "(3) 20,000 YEC is $8,000 today but $200,000 at $10, where even the high tier (supply-consistent "
        "sizes) returns under 5 % — the pin removes the price dependence that D-Q-23a already flagged; "
        "(4) a higher share is a module "
        "parameter the owner can raise later without touching the primitive, but at launch it only taxes "
        "minters (75 % adds a quarter to every mint's protocol fee) to move the low tier from "
        f"{_usd(float(low_s['seat_usd_month']))} to {_usd(low_75)} a month. At launch the seats are run the "  # type: ignore[arg-type]
        "way the pools are at $25/pool/month: by operators who hold YEC and want the system to exist, not "
        "for the fee. The lever that pays attestors is mints per day; a renewal release can revisit the "
        "share once the real mint count is known.\n"
    )
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------------------------------
# Run and report


def run(policy: Policy, d: OptionD = DEFAULT, today: date | None = None) -> dict[str, object]:
    height = height_today(policy, today)
    sup = supply_rows(height)
    out: dict[str, object] = {
        "constants": {
            "height": height,
            "supply_yec": cumulative_supply_zat(height) / COIN,
            "subsidy_yec": block_subsidy(height) / COIN,
            "next_halving_height": next((h for h in halving_heights(10_000_000) if h > height), None),
            "halving_heights": halving_heights(height),
            "option_d": asdict(d),
            "fee_min_zat": FEE_MIN_ZAT,
            "fee_bps": FEE_BPS,
            "attest_fee_bps": ATTEST_FEE_BPS,
            "seats": SEATS,
            "pools": POOLS,
            "bid_depth_usd": BID_DEPTH_USD,
            "p10_daily_volume_usd": P10_DAILY_VOLUME_USD,
            "p2pkh_zat_per_byte": P2PKH_ZAT_PER_BYTE,
        },
        "supply": [asdict(r) for r in sup],
        "concentration": [asdict(c) for c in concentration(sup[0].cap_15pct_usd)],
        "max_executable_debt_usd": {
            f"{band}_{q}": max_executable_debt_usd(v)
            for band, qs in BID_DEPTH_USD.items()
            for q, v in qs.items()
        },
        "fee_table": fee_table(d),
        "revenue": [asdict(r) for r in revenue(policy, d)],
        "network": {v: network_rows(d, v) for v in ("pq_p2pkh", "falcon")},
        "network_recommended": {v: network_rows(RECOMMENDED, v) for v in ("pq_p2pkh", "falcon")},
        "shocks": shock_rows(d),
        "dollar_constants": [asdict(c) for c in dollar_constants(d)],
        "sensitivity": sensitivity(policy, d),
    }
    out["recommendation"] = asdict(recommend(policy, d, height))
    return out


def _usd(x: float) -> str:
    return f"${x:,.2f}" if x < 1_000 else f"${x:,.0f}"


def render_markdown(res: Mapping[str, object]) -> str:
    C: Mapping[str, object] = res["constants"]  # type: ignore[assignment]
    L: list[str] = []
    L.append(
        f"## 1. Supply and the cap (height {C['height']:,}, supply {C['supply_yec']:,.0f} YEC, "
        f"subsidy {C['subsidy_yec']} YEC/block, next halving {C['next_halving_height']:,})\n"
    )
    L.append(
        "| YEC price | market cap | 15 % of it | $100k vaults | MINT-6 cap after 30 d | after 365 d | $100k "
        "vaults in year 1 | days to one $100k (A/B) |"
    )
    L.append("|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in res["supply"]:  # type: ignore[union-attr]
        L.append(
            f"| ${r['price_usd']:,.2f} | {_usd(r['market_cap_usd'])} | {_usd(r['cap_15pct_usd'])} | "
            f"{r['vaults_100k']:,} | "
            f"{_usd(r['rule_cap_30d_usd'])} | {_usd(r['rule_cap_365d_usd'])} | "
            f"{r['rule_cap_365d_vaults_100k']:,.1f} | "
            f"{r['rule_days_to_one_100k']:,.0f} |"
        )
    L.append("\n### Concentration (G3 claimant model against the frozen bid books)\n")
    L.append(
        "| Case | vaults | debt each | one claim executes at p10 / p50 whole-book depth | one sale vs p10 "
        "daily volume | "
        "unexecutable share | one owner absent | one key lost |"
    )
    L.append("|---|---:|---:|---|---:|---:|---:|---:|")
    for c in res["concentration"]:  # type: ignore[union-attr]
        L.append(
            f"| {c['case']} | {c['n_vaults']} | {_usd(c['debt_each_usd'])} | "
            f"{'yes' if c['executable_at_p10_30pct'] else 'no'} / "
            f"{'yes' if c['executable_at_p50_30pct'] else 'no'} | "
            f"{c['sale_vs_p10_daily_volume']:.2f}× | {c['unexecutable_share']:.0%} | "
            f"{c['owner_absence_exposure']:.0%} | "
            f"{c['lost_key_exposure']:.0%} |"
        )
    L.append(
        "\nLargest executable debt per depth band: "
        + ", ".join(
            f"{k} {_usd(v)}"
            for k, v in res["max_executable_debt_usd"].items()  # type: ignore[union-attr]
        )
    )
    L.append("\n## 2. Option D protocol fee by vault size\n")
    L.append(
        "| Debt | fee / action | effective | mint total (pool + attestors) | round trip | early A / B / C "
        "(on debt) | "
        "early A / B / C today (on collateral) | claim loss ≤ | fee in YEC at $0.40 / $1 / $5 / $10 / "
        "$4,295 | "
        "today's fee at $0.40 / $1 / $5 / $10 / $4,295 |"
    )
    L.append("|---:|---:|---:|---:|---:|---|---|---:|---|---|")
    for r in res["fee_table"]:  # type: ignore[union-attr]
        yec = " / ".join(f"{r[f'fee_yec@{p:g}']:,.4f}" for p in PRICES_USD)
        today = " / ".join(_usd(r[f"today_usd@{p:g}"]) for p in PRICES_USD)
        L.append(
            f"| {_usd(r['debt_usd'])} | {_usd(r['fee_usd'])} | {r['effective_rate']:.3%} | "
            f"{_usd(r['mint_total_usd'])} | "
            f"{r['round_trip_rate']:.3%} | {' / '.join(_usd(x) for x in r['early_usd'])} | "
            f"{' / '.join(_usd(x) for x in r['early_today_usd'])} | {_usd(r['claim_penalty_max_usd'])} | "
            f"{yec} | {today} |"
        )
    L.append("\n### Revenue per year (FEE-1 to the pools, AFEE-1 to the seated attestors)\n")
    L.append(
        "| Scenario | sizes | rule | YEC price | mints/yr | redeems/yr | mean debt | pools $/yr | attestors "
        "$/yr | "
        "$/pool/month | $/seat/month | bond cost $/month | seat ≥ bond | seat ≥ $50 | pool ≥ $50 |"
    )
    L.append("|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|")
    for r in res["revenue"]:  # type: ignore[union-attr]
        L.append(
            f"| {r['scenario']} | {r['size_model']} | {r['rule']} | ${r['price_usd']:,.2f} | "
            f"{r['mints_per_year']:,.0f} | {r['redeems_per_year']:,.0f} | "
            f"{_usd(r['mean_debt_usd'])} | {_usd(r['pool_usd_year'])} | {_usd(r['attestor_usd_year'])} | "
            f"{_usd(r['per_pool_usd_month'])} | {_usd(r['per_seat_usd_month'])} | "
            f"{_usd(r['bond_cost_usd_month'])} | "
            f"{'yes' if r['seat_covers_bond'] else 'no'} | {'yes' if r['seat_meets_floor'] else 'no'} | "
            f"{'yes' if r['pool_meets_floor'] else 'no'} |"
        )
    L.append("\n## 3. Network fee per path ($/KB targets, floor 1× relay, cap -maxtxfee 0.1 YEC)\n")
    nets = [
        ("as decided", res["network"]),
        ("recommended: floor 50× relay, cap max($2, 0.1 YEC)", res["network_recommended"]),
    ]
    tables = [(f"{label}, variant `{v}`", rows) for label, by_v in nets for v, rows in by_v.items()]  # type: ignore[union-attr]
    for label, rows in tables:
        L.append(f"\n**{label}**\n")
        L.append(
            "| Path | bytes | target | $ at target | cap binds below | parity fails above | pq m=50 zat | "
            + " | ".join(f"zat (bound) at ${p:,.2f}" for p in PRICES_USD)
            + " | zat/byte at $0.40 … $4,295 | ≥ P2PKH 4.08 zat/B |"
        )
        L.append("|---|---:|---:|---:|---:|---:|---:|" + "---:|" * len(PRICES_USD) + "---|---|")
        for r in rows:
            cells = " | ".join(f"{r[f'zat@{p:g}']:,} ({r[f'bound@{p:g}']})" for p in PRICES_USD)
            zpb = " … ".join(f"{r[f'zat_per_byte@{p:g}']:,.1f}" for p in (PRICES_USD[0], PRICES_USD[-1]))
            ok = " / ".join("y" if r[f"beats_p2pkh@{p:g}"] else "N" for p in PRICES_USD)
            L.append(
                f"| {r['path']} | {r['size']:,} | ${r['target_usd_per_kb']:.2f}/KB | "
                f"${r['fee_usd_target']:.3f} | "
                f"${r['cap_binds_below_usd']:.2f} | ${r['parity_fails_above_usd']:,.0f} | "
                f"{r['pqfeerate_m50_zat']:,} | "
                f"{cells} | {zpb} | {ok} |"
            )
    L.append("\n## 4. Robustness: a ±70 % move between mint ($0.40) and redeem, $1,000 class-A vault\n")
    L.append(
        "| shock | price now | fee zat (fixed at pMint) | fee $ now | early A $ now | $1 floor now | "
        "collateral ratio | claimable |"
    )
    L.append("|---:|---:|---:|---:|---:|---:|---:|---|")
    for r in res["shocks"]:  # type: ignore[union-attr]
        L.append(
            f"| {r['shock']:+.0%} | ${r['p_now_usd']:.3f} | {r['fee_zat']:,.0f} | {_usd(r['fee_usd_now'])} | "
            f"{_usd(r['early_A_usd_now'])} | {_usd(r['floor_usd_now'])} | {r['collateral_ratio_A']:.0%} | "
            f"{'yes' if r['claimable'] else 'no'} |"
        )
    L.append("\n### YEC-denominated constants restated in dollars\n")
    L.append(
        "| constant | today (YEC) | today in $ at $0.40 / $1 / $5 / $10 / $4,295 | proposed $ | floor YEC | "
        "proposed in YEC at the same prices |"
    )
    L.append("|---|---:|---|---:|---:|---|")
    for c in res["dollar_constants"]:  # type: ignore[union-attr]
        L.append(
            f"| `{c['name']}` | {c['today_yec']:,} | "
            f"{' / '.join(_usd(v) for v in c['today_usd_at'].values())} | "
            f"{_usd(c['proposed_usd'])} | {c['floor_yec']:,} | "
            f"{' / '.join(f'{v:,.4f}' for v in c['proposed_yec_at'].values())} |"
        )
        L.append(f"\n  {c['rationale']}\n")
    L.append("\n## 5. Sensitivity (±50 % per knob)\n")
    L.append(
        "| knob | × | rate $100 | $1k | $10k | $100k | whale round trip | low (supply sizes): $/seat/month "
        "at $0.40 | $/pool/month | "
        "SLH redeem cap binds below | SLH sweep cap binds below | yb parity fails above | pq parity fails "
        "above |"
    )
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in res["sensitivity"]:  # type: ignore[union-attr]
        L.append(
            f"| {r['knob']} | {r['mult']:g} | {r['rate_100']:.2%} | {r['rate_1k']:.2%} | "
            f"{r['rate_10k']:.3%} | "
            f"{r['rate_100k']:.3%} | {_usd(r['whale_round_trip_usd'])} | "
            f"{_usd(r['low_seat_usd_month_at_0.40'])} | "
            f"{_usd(r['low_pool_usd_month_at_0.40'])} | ${r['slh_redeem_cap_binds_below_usd']:.2f} | "
            f"${r['slh_sweep_cap_binds_below_usd']:.2f} | ${r['yb_parity_fails_above_usd']:,.0f} | "
            f"${r['pq_parity_fails_above_usd']:,.0f} |"
        )
    rec: Mapping[str, object] = res["recommendation"]  # type: ignore[assignment]
    L.append("\n## Recommendation\n")
    L.append(
        f"Bands {rec['bands']}, floor ${rec['floor_usd']:g}/action, early {rec['early_bps']} bps of the "
        "debt; "
        f"network ${rec['pq_usd_per_kb']:g}/KB PQ and ${rec['yb_usd_per_kb']:g}/KB vault/Yellowback, floor "
        f"{rec['network_floor_zat_per_kb']:,} zat/KB, cap max(${rec['maxtxfee_usd']:g} at the attested "
        "price, "
        f"{rec['maxtxfee_floor_yec']:g} YEC); maxVaultShareBps {rec['max_vault_share_bps']:,} of the cap "
        "with a "
        f"${rec['max_vault_share_floor_usd']:,.0f} floor; bondMin ${rec['bond_min_usd']:,.0f} (floor "
        f"{rec['bond_min_floor_yec']:,.0f} YEC); residualMin ${rec['residual_min_usd']:.2f} at pMint (floor "
        f"{rec['residual_min_floor_zat']:,} zat).\n"
    )
    for r in rec["reasons"]:  # type: ignore[union-attr]
        L.append(f"- {r}")
    return "\n".join(L) + "\n"


def configure_fees(p: argparse.ArgumentParser) -> None:
    p.add_argument("--policy", default=None, help="policy TOML (default policy/default.toml)")
    p.add_argument("--today", default=None, help="date for the height estimate (YYYY-MM-DD; default today)")
    p.add_argument("--out", default=None, help="write fees.json and fees.md (or attestors.*) here")
    p.add_argument(
        "--attestors",
        action="store_true",
        help="attestor economics only (D-Q-25): tiers × price × bond rule → attestors.md / attestors.json",
    )


def cli_fees(args: argparse.Namespace) -> int:
    policy = Policy.load(args.policy)
    today = date.fromisoformat(args.today) if args.today else None
    if getattr(args, "attestors", False):
        res = attestor_economics(policy, today=today)
        md = render_attestors_markdown(res)
        stem = "attestors"
    else:
        res = run(policy, today=today)
        md = render_markdown(res)
        stem = "fees"
    if args.out:
        d = Path(args.out)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{stem}.json").write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
        (d / f"{stem}.md").write_text(md)
    sys.stdout.write(md)
    return 0

"""The parameter registry: PLAN §1.3 as data — the single source of truth (owner: WP-0).

One :class:`ParamSpec` per field of ``yellowback::Params`` at :data:`PINNED_COMMIT` (array fields
expanded as ``classMin[0]`` …), plus the ``params.h`` protocol constants the rules read. Values are
the mainnet column (``SetCommon`` + ``MainParams``) and the regtest column (``RegtestParams`` with
the six regtest flags at their defaults: ``startHeight=1``, ``sigmaRefBps=0``, ``supplyCapBps=0``,
``enforceUntilHeight=0``, ``attestArmMin=3``, ``bundleCarrier=SCRIPTSIG``).

``ybcal params check`` re-extracts both columns from source and fails on any difference, so this
table cannot silently drift from the node.

Vocabulary (PLAN §1.2, recorded in ``docs/decisions.md``):

* ``locked``      — consensus-shaped among enforcing miners (K10); changes only via a new
  parameter set keyed by start height (L8 / W19).
* ``excluded``    — informational, wallet default (L6), wallet/agent policy or node-local; free to
  change in a patch release.
* ``per-release`` — ``startHeight`` / ``enforceUntilHeight``; derived from the release tip (M14, L8).
* ``constant``    — protocol constants (``params.h``); verified, never tuned.
* ``derived``     — fixed by a formula from a parent (:attr:`ParamSpec.derive`); the tool tunes the
  parent. A derived value is still consensus-shaped when :attr:`ParamSpec.consensus` is true.
* ``meta``        — identity fields (network name, address version bytes); not calibrated.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Literal

from ybcal.types import ParamValue
from ybcal.units import (
    BLOCKS_PER_DAY,
    BLOCKS_PER_HOUR,
    BLOCKS_PER_YEAR,
    BPS,
    COIN,
    PRICE_MAX,
    PRICE_MIN,
    ceil_div,
)

#: ycash6 commit every value and line citation in this repo is taken at.
PINNED_COMMIT: str = "7702d22"
#: Branch the pin lives on (informational).
PINNED_BRANCH: str = "feature/yellowback"
#: Source files the registry mirrors (paths inside ycash6).
PARAMS_H: str = "src/yellowback/params.h"
PARAMS_CPP: str = "src/yellowback/params.cpp"

Klass = Literal["locked", "excluded", "per-release", "constant", "derived", "meta"]
Unit = Literal[
    "blocks", "height", "bps", "zat", "cents", "micro-usd", "count", "enum", "bool", "hex", "string",
]
Origin = Literal["field", "header"]

#: A derivation: computes a derived value from the full value mapping (which always contains
#: ``"network"``, so a derivation may differ on regtest, e.g. K13's ``volPeriodsPerYear``).
Derive = Callable[[Mapping[str, ParamValue]], ParamValue]

KLASSES: tuple[Klass, ...] = ("locked", "excluded", "per-release", "constant", "derived", "meta")
GROUPS: tuple[str, ...] = ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9", "R", "-")

#: Regtest flag defaults the registry's regtest column is built with (index.cpp ParamsFromArgs;
#: ``-yellowbackstartheight`` is required and positive, so 1 stands for "the smallest legal value").
REGTEST_FLAG_DEFAULTS: dict[str, ParamValue] = {
    "startHeight": 1,
    "sigmaRefBps": 0,
    "supplyCapBps": 0,
    "enforceUntilHeight": 0,
    "attestArmMin": 3,
    "bundleCarrier": "SCRIPTSIG",
}

#: ``BundleCarrier`` enum (params.h) — name to wire value.
BUNDLE_CARRIERS: dict[str, int] = {"SCRIPTSIG": 0, "OP_RETURN": 1, "EITHER": 2}


@dataclass(frozen=True)
class ParamSpec:
    """One row of PLAN §1.3.

    ``bounds`` are hard *search* bounds for the mainnet-scale optimiser (inclusive); ``step`` is the
    search granularity (0 = not searched). Both refer to mainnet scale; regtest values may sit
    outside them.
    """

    name: str                      #: C++ field (``"pMidWindow"``, ``"baseRatioBps[1]"``) or constant
    mainnet: ParamValue            #: mainnet column at the pin
    regtest: ParamValue            #: regtest column at the pin (flags at REGTEST_FLAG_DEFAULTS)
    klass: Klass
    group: str                     #: "G1".."G9", "R" (release study) or "-" (not studied)
    unit: Unit
    hashed: bool                   #: in the state-hash ParamsRecord (view.h)
    rules: tuple[str, ...]         #: rule identifiers that read it ("PRICE-1", "HALT-3", …)
    derive: Derive | None          #: for derived params
    bounds: tuple[int, int]        #: search bounds (hard, inclusive, mainnet scale)
    step: int                      #: search granularity; 0 = never searched
    doc: str                       #: one-line meaning
    consensus: bool = True         #: read by a consensus rule → changing it is a locked change
    origin: Origin = "field"       #: ``Params`` struct field or ``params.h`` constant
    cpp_expr: str = ""             #: the mainnet right-hand side as written in params.cpp / params.h
    parents: tuple[str, ...] = ()  #: for derived params: the fields ``derive`` reads
    regtest_flag: str | None = None  #: regtest-only runtime flag that sets it (M13)
    note: str = ""                 #: extra caveat shown in docs/parameters.md
    pinned_commit: str = PINNED_COMMIT

    @property
    def cpp_field(self) -> str:
        """The C++ member name without an array index (``"classMin"`` for ``"classMin[0]"``)."""
        return self.name.split("[", 1)[0]

    @property
    def index(self) -> int | None:
        """Array index for array fields, else ``None``."""
        if "[" not in self.name:
            return None
        return int(self.name.split("[", 1)[1].rstrip("]"))

    @property
    def change_path(self) -> str:
        """How a change to this value ships: the report's locked / patch-release note."""
        if self.klass == "excluded":
            return "patch-release"
        if self.klass == "per-release":
            return "per-release"
        if self.klass == "constant":
            return "protocol-constant"
        if self.klass == "meta":
            return "meta"
        return "locked" if self.consensus else "patch-release"

    @property
    def tunable(self) -> bool:
        """Whether a study searches this value directly (derived values follow their parent)."""
        return self.klass in ("locked", "excluded") and self.step > 0


def _ceil_half(window: str) -> Derive:
    return lambda v: ceil_div(int(v[window]), 2)


def _ceil_two_thirds(window: str) -> Derive:
    return lambda v: ceil_div(2 * int(v[window]), 3)


def _vol_periods(v: Mapping[str, ParamValue]) -> int:
    # K13: the annualisation is "an independent parameter, deliberately equal on every network".
    # At mainnet scale it equals BLOCKS_PER_YEAR / volStep (PLAN §1.3); regtest keeps 8,760.
    if v["network"] == "regtest":
        return 8760
    return BLOCKS_PER_YEAR // int(v["volStep"])


_SPECS: list[ParamSpec] = []


def _add(
    name: str,
    mainnet: ParamValue,
    regtest: ParamValue,
    klass: Klass,
    group: str,
    unit: Unit,
    rules: tuple[str, ...],
    bounds: tuple[int, int],
    step: int,
    doc: str,
    *,
    hashed: bool = False,
    derive: Derive | None = None,
    parents: tuple[str, ...] = (),
    consensus: bool | None = None,
    origin: Origin = "field",
    cpp_expr: str | None = None,
    regtest_flag: str | None = None,
    note: str = "",
) -> None:
    if consensus is None:
        consensus = klass in ("locked", "per-release", "derived", "constant")
    _SPECS.append(
        ParamSpec(
            name=name, mainnet=mainnet, regtest=regtest, klass=klass, group=group, unit=unit,
            hashed=hashed, rules=rules, derive=derive, bounds=bounds, step=step, doc=doc,
            consensus=consensus, origin=origin,
            cpp_expr=cpp_expr if cpp_expr is not None else repr(mainnet),
            parents=parents, regtest_flag=regtest_flag, note=note,
        )
    )


I32 = 2**31 - 1

# --- identity and release -------------------------------------------------------------------------
_add("network", "main", "regtest", "meta", "-", "string", (), (0, 0), 0,
     "Network id (CChainParams::NetworkIDString)", consensus=False, cpp_expr='"main"')
_add("startHeight", 3_075_000, 1, "per-release", "R", "height", ("every rule", "M2", "M14"), (1, I32), 0,
     "First height whose tags count; set per release at tip + >= 16,128 (M14)",
     hashed=True, regtest_flag="-yellowbackstartheight")
_add("enforceUntilHeight", 3_495_480, 0, "per-release", "R", "height", ("ACT-5", "L8"), (0, I32), 0,
     "Enforcement sunset: start + BLOCKS_PER_YEAR; 0 = none (regtest)",
     hashed=True, regtest_flag="-yellowbackenforceuntil")
_add("addressVersion", "1FE4", "2002", "meta", "-", "hex", ("D10",), (0, 0), 0,
     "Base58Check version bytes of Yellowback addresses (ye… / yr…)", consensus=False,
     cpp_expr="{ 0x1F, 0xE4 }")

# --- G1 prices ----------------------------------------------------------------------------------------
_add("pFastWindow", 96, 8, "locked", "G1", "blocks", ("PRICE-1", "PRICE-2", "HALT-3", "SIGMA-1", "MINT-10"),
     (48, 288), 48, "Fast price-median window")
_add("pMidWindow", 576, 24, "locked", "G1", "blocks", ("PRICE-1", "PRICE-2", "HALT-3"), (288, 1152), 48,
     "Mid price-median window")
_add("pSlowWindow", 2016, 64, "locked", "G1", "blocks", ("PRICE-1", "PRICE-2", "HALT-3"), (1152, 4032), 48,
     "Slow price-median window")
_add("pFastMinFill", 48, 4, "derived", "G1", "count", ("PRICE-1", "L9"), (24, 144), 0,
     "Minimum quote tags in the fast window: ceil(W/2)",
     derive=_ceil_half("pFastWindow"), parents=("pFastWindow",))
_add("pMidMinFill", 384, 16, "derived", "G1", "count", ("PRICE-1", "L9"), (192, 768), 0,
     "Minimum quote tags in the mid window: ceil(2W/3)",
     derive=_ceil_two_thirds("pMidWindow"), parents=("pMidWindow",))
_add("pSlowMinFill", 1344, 43, "derived", "G1", "count", ("PRICE-1", "L9"), (768, 2688), 0,
     "Minimum quote tags in the slow window: ceil(2W/3)",
     derive=_ceil_two_thirds("pSlowWindow"), parents=("pSlowWindow",))

# --- G5 activation ----------------------------------------------------------------------------------
_add("signalWindow", 2016, 64, "locked", "G5", "blocks", ("ACT-1", "ACT-2", "W19"), (1008, 8064), 288,
     "Blocks over which the signal count is taken; also the W19 freeze window")
_add("activationThreshold", 1512, 48, "locked", "G5", "count", ("ACT-2", "ACT-4"), (1008, 2016), 1,
     "Signals per window to lock in and to clear PARTICIPATION (75 %)")
_add("participationFloor", 1210, 39, "locked", "G5", "count", ("ACT-4", "MINT-4"), (806, 2016), 1,
     "Below it the PARTICIPATION halt stops minting (60 %)")
_add("activationDelay", 2016, 64, "locked", "G5", "blocks", ("ACT-2", "ACT-3"), (1152, 16128), 288,
     "Blocks from lock-in to ACTIVE")
_add("enforcementFloor", 1008, 32, "locked", "G5", "count", ("ACT-6", "ACT-5", "BLK-1"), (604, 2016), 1,
     "Below it block rejection suspends (ENFORCEMENT halt, 50 %)")
_add("enforcementResume", 1210, 39, "locked", "G5", "count", ("ACT-6",), (806, 2016), 1,
     "Signals to clear the ENFORCEMENT halt (60 %)")
_add("valveBlocks", 6, 6, "excluded", "G5", "blocks", ("ACT-7", "BLK-2"), (2, 24), 1,
     "Work valve: blocks of work over a rejected root that switch enforcement off (node-local, L7)")
_add("abandonBlocks", 34_560, 128, "locked", "G4", "blocks", ("L10", "L12", "TPL-1", "TPL-2", "MP-1", "W21"),
     (20_160, 103_680), BLOCKS_PER_DAY,
     "ENFORCEMENT set continuously this long = module abandoned (yed_sweep); >= grace (W21)",
     note="Classified locked by K10 wording: it gates TPL-1/2, MP-1 and yed_sweep (docs/decisions.md D-2).")

# --- G6 miners and fees ------------------------------------------------------------------------------
_add("nReg", 576, 24, "excluded", "G6", "blocks", ("REG-1",), (96, 2016), 48,
     "Registry look-back for yed_listminers (informational)")
_add("peerLag", 10, 4, "locked", "G6", "blocks", ("REG-4",), (2, 48), 1,
     "Peer window half-width for judgement: [t - lag, t + lag - 1]")
_add("peerMin", 5, 3, "locked", "G6", "count", ("REG-4",), (2, 12), 1,
     "Minimum peer quotes for a judgement to be made")
_add("deviationBps", 1000, 1000, "locked", "G6", "bps", ("REG-4",), (300, 3000), 100,
     "Deviation from the peer median that counts as a bad quote")
_add("accuracyBandBps", 300, 300, "locked", "G6", "bps", ("REG-4",), (100, 1000), 50,
     "Deviation band that counts as an accurate quote")
_add("payeeWindow", 100, 10, "locked", "G6", "blocks", ("FEE-2", "MINT-8", "RED-3"), (20, 576), 10,
     "Window of recent tagged blocks eligible as enforcement-fee payees")
_add("feeMin", 50_000_000, 50_000_000, "locked", "G6", "zat", ("FEE-1", "MINT-5", "AFEE-1"),
     (10_000_000, 500_000_000), 10_000_000, "Enforcement fee floor (0.5 YEC); 4·feeMin is the MINT-5 floor")
_add("feeBps", 25, 25, "locked", "G6", "bps", ("FEE-1",), (0, 200), 5,
     "Enforcement fee rate on minted/redeemed value")

# --- G3/G4/G7 vaults ----------------------------------------------------------------------------------
_add("grace", 34_560, 24, "locked", "G4", "blocks", ("MINT-2", "MINT-3"), (11_520, 103_680), BLOCKS_PER_DAY,
     "Blocks after lockHeight before the claim path opens (claimHeight = lock + grace)")
_add("claimThresholdBps", 11_000, 11_000, "locked", "G3", "bps", ("RED-4", "RED-5"), (10_500, 15_000), 250,
     "Collateral ratio below which a matured vault is claimable")
_add("supplyCapBps", 1500, 0, "locked", "G7", "bps", ("MINT-6", "W20"), (0, 5000), 250,
     "Soft supply cap as a share of issued-since-start value; 0 = none (regtest)",
     hashed=True, regtest_flag="-yellowbacksupplycapbps")
_add("globalRatioHaltBps", 25_000, 25_000, "locked", "G7", "bps", ("HALT-2",), (12_500, 30_000), 1250,
     "System collateral ratio below which HALT-2 is set")
_add("recapRatioBps", 50_000, 50_000, "derived", "G7", "bps", ("MINT-4", "MINT-6", "W16", "W20"),
     (25_000, 60_000), 0, "Class minimum a mint needs during HALT-2 / over the cap: 2 × halt (W16)",
     derive=lambda v: 2 * int(v["globalRatioHaltBps"]), parents=("globalRatioHaltBps",))
_add("divergenceBps", 2000, 2000, "locked", "G7", "bps", ("HALT-3",), (500, 5000), 250,
     "Fast-below-slow median gap that sets HALT-3 (falls only)")

_CLASS_DOC = ("A", "B", "C")
_class_vals = {
    "classMin": ((34_560, 48), (103_681, 97), (420_481, 145)),
    "classMax": ((103_680, 96), (420_480, 144), (2_102_400, 240)),
}
_class_bounds = {
    "classMin": ((BLOCKS_PER_DAY * 7, 103_680), (34_561, 420_480), (103_681, 1_051_200)),
    "classMax": ((34_560, 420_480), (103_681, 1_051_200), (420_481, 2_102_400)),
}
for fld in ("classMin", "classMax"):
    for i in range(3):
        mv, rv = _class_vals[fld][i]
        word = "Shortest" if fld == "classMin" else "Longest"
        _add(f"{fld}[{i}]", mv, rv, "locked", "G3", "blocks", ("MINT-2",), _class_bounds[fld][i],
             BLOCKS_PER_DAY, f"{word} lock length (inclusive) of term class {_CLASS_DOC[i]}")
_base_rows = ((50_000, 30_000, 80_000), (40_000, 25_000, 70_000), (30_000, 20_000, 60_000))
for i, (mv, lo, hi) in enumerate(_base_rows):
    _add(f"baseRatioBps[{i}]", mv, mv, "locked", "G3", "bps", ("MINT-5", "MINT-4", "MINT-6"), (lo, hi), 2500,
         f"Base collateral ratio of class {_CLASS_DOC[i]} (before the σ multiplier)")

# --- G2 volatility ------------------------------------------------------------------------------------
_add("volWindow", 2016, 64, "locked", "G2", "blocks", ("SIGMA-1",), (576, 8064), 48,
     "Look-back of the realised-volatility estimator")
_add("volStep", 48, 8, "locked", "G2", "blocks", ("SIGMA-1",), (12, 288), 12,
     "Sampling step of the volatility estimator (pFast samples)")
_add("volPeriodsPerYear", 8760, 8760, "derived", "G2", "count", ("SIGMA-1", "K13"), (1460, 35_040), 0,
     "Annualisation: BLOCKS_PER_YEAR / volStep at mainnet scale; 8,760 on every network (K13)",
     derive=_vol_periods, parents=("volStep",),
     note="K13 keeps 8,760 on regtest, so the derivation is network-aware (docs/decisions.md D-4).")
_add("sigmaRefBps", 10_000, 0, "locked", "G2", "bps", ("SIGMA-1", "M14"), (2500, 30_000), 500,
     "Reference annualised volatility; multiplier = σ/σref clamped; 0 = multiplier fixed at 1",
     hashed=True, regtest_flag="-yellowbacksigmaref")
_add("sigmaMultMaxBps", 30_000, 30_000, "locked", "G2", "bps", ("SIGMA-1", "K12"), (15_000, 50_000), 2500,
     "Cap of the σ multiplier (also its value when a sample is undefined, K12)")

# --- G9 amounts ---------------------------------------------------------------------------------------
_add("minMint", 10_000, 10_000, "locked", "G9", "cents", ("MINT-2",), (1000, 100_000), 1000,
     "Smallest mint ($100)")
_add("maxMint", 1_000_000, 1_000_000, "locked", "G9", "cents", ("MINT-2",), (100_000, 10_000_000), 100_000,
     "Largest mint ($10,000)")
_add("minOutput", 100, 100, "locked", "G9", "cents", ("XFER-1", "RED-1"), (1, 10_000), 50,
     "Smallest YED output ($1)")
_add("maxOutput", 10_000_000, 10_000_000, "locked", "G9", "cents", ("XFER-1", "RED-1"),
     (1_000_000, 100_000_000), 1_000_000, "Largest YED output ($100,000)")
_add("tokenValue", 10_000, 10_000, "constant", "-", "zat", ("RPC",), (10_000, 10_000), 0,
     "YEC carried by every Yellowback output (= TOKEN_VALUE)", cpp_expr="TOKEN_VALUE")
_add("refWindow", 40, 40, "constant", "-", "blocks", ("MINT-2", "RED-1", "NOT-1"), (40, 40), 0,
     "refHeight must lie in [H - refWindow, H - 1] (= REF_WINDOW)", cpp_expr="REF_WINDOW")

# --- G6 L6 wallet defaults -------------------------------------------------------------------------------
_add("nPenalty", 288, 12, "excluded", "G6", "blocks", ("REG-2", "FEE-W"), (48, 1152), 48,
     "Payee penalty length after a bad quote (L6 wallet default)")
_add("accuracyWindow", 576, 24, "excluded", "G6", "blocks", ("REG-3", "FEE-W"), (96, 2016), 48,
     "Accuracy look-back for payee weighting (L6 wallet default)")
_add("payeeTiltBps", 10_000, 10_000, "excluded", "G6", "bps", ("FEE-W", "K8"), (0, 20_000), 1000,
     "Payee weight tilt toward accurate pools (L6 wallet default)")

# --- G8 attestation -----------------------------------------------------------------------------------
_add("attestArmMin", 5, 3, "locked", "G8", "count", ("ARM-1",), (3, 15), 1,
     "Seated attestors needed to arm attestation; 0 = never arms (regtest)",
     hashed=True, regtest_flag="-yellowbackattestarmmin")
_add("attestArmDelay", 1152, 8, "locked", "G8", "blocks", ("ARM-2",), (288, 8064), 288,
     "Blocks from arm condition to ARMED")
_add("attestRequired", True, True, "locked", "G8", "bool", ("W15",), (0, 1), 0,
     "Whether ARMED rules require bundles (false: PRICE-2 reads tags only)", cpp_expr="true")
_add("bundleCarrier", "SCRIPTSIG", "SCRIPTSIG", "locked", "-", "enum", ("BUNDLE-1", "W2"), (0, 2), 0,
     "Where a transaction carries its attestation bundle (design choice, verified)",
     hashed=True, regtest_flag="-yellowbackbundlecarrier", cpp_expr="BundleCarrier::SCRIPTSIG")
_add("nSlots", 9, 5, "locked", "G8", "count", ("seating", "selection"), (5, 21), 1,
     "Attestor seats")
_add("mSelect", 4, 2, "locked", "G8", "count", ("selection", "BUNDLE-1"), (2, 6), 1,
     "Signatures a bundle needs")
_add("kSlack", 2, 1, "locked", "G8", "count", ("selection", "BUNDLE-1"), (0, 4), 1,
     "Extra selected attestors beyond mSelect (liveness slack)")
_add("bundleMax", 6, 6, "locked", "G8", "count", ("BUNDLE-1",), (2, 6), 1,
     "Largest bundle: the 520-byte push holds 4 + 6·74 bytes")
_add("qLowBps", 3333, 3333, "locked", "G8", "bps", ("bundle statistic",), (2500, 5000), 1,
     "Lower weighted quantile of the bundle statistic")
_add("qHighBps", 6667, 6667, "derived", "G8", "bps", ("bundle statistic",), (5000, 7500), 0,
     "Upper weighted quantile: 10,000 - qLowBps",
     derive=lambda v: BPS - int(v["qLowBps"]), parents=("qLowBps",))
_add("attestMaxAge", 20, 8, "derived", "G8", "blocks", ("BUNDLE-1", "REV-1", "R4"), (4, 120), 0,
     "Oldest attestation a bundle may carry: 2 · attestInterval (k)",
     derive=lambda v: 2 * int(v["attestInterval"]), parents=("attestInterval",),
     note=("Locked although its parent k (attestInterval) is excluded: changing k is therefore a "
           "locked change through attestMaxAge (docs/decisions.md D-3)."))
_add("pinWindow", 288, 16, "locked", "G8", "blocks", ("PIN-1", "PIN-2"), (96, 1152), 48,
     "Look-back of the pinned-feed detector (to calibrate, A7)")
_add("pinDeltaBps", 500, 500, "locked", "G8", "bps", ("PIN-1", "PIN-2"), (100, 1500), 50,
     "Move that arms the pinned-feed detector (to calibrate, A7)")
_add("pinMinTags", 3, 2, "locked", "G8", "count", ("PIN-1",), (1, 10), 1,
     "Constant-quote tags that mark a pool pinned (to calibrate, A7)")
_add("pinMinBundles", 2, 2, "locked", "G8", "count", ("PIN-2",), (1, 10), 1,
     "Constant bundles that mark an attestor pinned (to calibrate, A7)")
_add("divergeBpsAttest", 1500, 1500, "locked", "G8", "bps", ("MINT-10",), (300, 5000), 100,
     "Tag-vs-attestation gap above which minting refuses (to calibrate, A7)")
_add("emergencyRatioBps", 10_500, 10_500, "locked", "G3", "bps", ("NOT-1", "RED-4"), (10_100, 14_900), 100,
     "Ratio below which an emergency notice may be posted (RED-4(b))")
_add("emergencyPersist", 48, 4, "locked", "G8", "blocks", ("NOT-1", "RED-4"), (12, 576), 12,
     "Blocks the emergency condition must persist")
_add("emergencyNoticeTtl", 1152, 64, "locked", "G8", "blocks", ("NOT-1", "RED-4"), (288, 4608), 288,
     "Lifetime of an emergency notice")
_add("residualMinZat", 100_000, 100_000, "locked", "G9", "zat", ("RED-5",), (10_000, 10_000_000), 10_000,
     "Smallest collateral residual returned to the owner")
_add("attestFeeBps", 2500, 2500, "locked", "G6", "bps", ("AFEE-1",), (0, 5000), 250,
     "Attestor share of the enforcement fee (a testnet parameter, D-3)")
_add("bondMin", 20_000 * COIN, 10 * COIN, "locked", "G8", "zat", ("REG-A1",),
     (1_000 * COIN, 100_000 * COIN), 1_000 * COIN, "Smallest attestor bond (20,000 YEC)",
     cpp_expr="20000 * COIN")
_add("bondMinLock", 420_480, 200, "locked", "G8", "blocks", ("REG-A1",),
     (BLOCKS_PER_YEAR, BLOCKS_PER_YEAR), 0,
     "Shortest bond lock (one year; invariant = BLOCKS_PER_YEAR)")
_add("bondMaturity", 16_128, 8, "locked", "G8", "blocks", ("maturity",), (1152, 40_320), 1152,
     "Blocks before a bond's attestor may be seated")
_add("ageCap", 207_360, 64, "locked", "G8", "blocks", ("bond weight",), (34_560, 420_480), 11_520,
     "Bond age at which weight stops growing (placeholder)")
_add("foundingWindow", 8064, 16, "locked", "G8", "blocks", ("bond weight",), (1152, 40_320), 1152,
     "Founding window for bond weight (placeholder)")
_add("dormancyBlocks", 16_128, 16, "locked", "G8", "blocks", ("dormancy",), (4032, 40_320), 1152,
     "Look-back for dormancy ejection")
_add("dormancyMinBundles", 20, 2, "locked", "G8", "count", ("dormancy",), (1, 100), 1,
     "Bundles an attestor must appear in per dormancy window")
_add("dormancyCheck", 48, 4, "locked", "G8", "blocks", ("dormancy", "S15"), (12, 288), 12,
     "Dormancy is evaluated every this many blocks")
_add("carrierValue", 10_000, 10_000, "excluded", "G9", "zat", (), (1000, 100_000), 1000,
     "Value of the P2SH bundle-carrier output (wallet policy)")
_add("attestInterval", 10, 4, "excluded", "G8", "blocks", (), (2, 60), 1,
     "k: attestor signing interval (agent policy); attestMaxAge = 2k")
_add("walletConfirmations", 6, 1, "excluded", "G9", "count", (), (1, 30), 1,
     "Confirmations the wallet waits for (wallet policy)")

# --- params.h constants --------------------------------------------------------------------------------
_add("DEFAULT_REF_LAG", 2, 2, "excluded", "G9", "blocks", ("MINT-2", "V11"), (0, 36), 1,
     "Wallet refHeight lag (-yellowbackmintlag); a mint survives reorgs shorter than lag + 1",
     origin="header", consensus=False,
     note="A params.h constant but a wallet default: classified excluded per PLAN §1.3.")
_add("MAX_REF_LAG", 36, 36, "constant", "-", "blocks", ("§3.5",), (36, 36), 0,
     "Upper bound of -yellowbackmintlag (mempool expiring-soon rule)", origin="header", consensus=False)
_add("REF_WINDOW", 40, 40, "constant", "-", "blocks", ("MINT-2", "RED-1", "NOT-1"), (40, 40), 0,
     "Protocol constant = DEFAULT_POST_BLOSSOM_TX_EXPIRY_DELTA", origin="header")
_add("TOKEN_VALUE", 10_000, 10_000, "constant", "-", "zat", ("RPC",), (10_000, 10_000), 0,
     "YEC carried by every Yellowback output", origin="header")
_add("PRICE_MIN", PRICE_MIN, PRICE_MIN, "constant", "-", "micro-usd", ("TAG-1", "PRICE-1"),
     (PRICE_MIN, PRICE_MIN), 0, "Lowest valid quote ($0.0001)", origin="header")
_add("PRICE_MAX", PRICE_MAX, PRICE_MAX, "constant", "-", "micro-usd", ("TAG-1", "PRICE-1"),
     (PRICE_MAX, PRICE_MAX), 0, "Highest valid quote ($100)", origin="header")
_add("BLOCKS_PER_HOUR", BLOCKS_PER_HOUR, BLOCKS_PER_HOUR, "constant", "-", "blocks", ("units",),
     (BLOCKS_PER_HOUR, BLOCKS_PER_HOUR), 0, "Blocks per hour at 75 s", origin="header")
_add("BLOCKS_PER_DAY", BLOCKS_PER_DAY, BLOCKS_PER_DAY, "constant", "-", "blocks", ("units",),
     (BLOCKS_PER_DAY, BLOCKS_PER_DAY), 0, "Blocks per day at 75 s", origin="header")
_add("BLOCKS_PER_YEAR", BLOCKS_PER_YEAR, BLOCKS_PER_YEAR, "constant", "-", "blocks", ("L8", "K13"),
     (BLOCKS_PER_YEAR, BLOCKS_PER_YEAR), 0, "Blocks per year at 75 s", origin="header")

#: The registry: name → spec, in params.h declaration order (fields), then header constants.
REGISTRY: dict[str, ParamSpec] = {s.name: s for s in _SPECS}
if len(REGISTRY) != len(_SPECS):  # pragma: no cover - guards against a duplicated _add
    raise RuntimeError("duplicate ParamSpec name in registry")



def get(name: str) -> ParamSpec:
    """The spec for ``name``; ``KeyError`` with a hint if unknown."""
    try:
        return REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown parameter {name!r} (see `ybcal params show`)") from None


def specs(
    *, group: str | None = None, klass: str | None = None, origin: str | None = None
) -> Iterator[ParamSpec]:
    """Iterate specs, optionally filtered by group, klass and origin."""
    for s in REGISTRY.values():
        if group is not None and s.group != group:
            continue
        if klass is not None and s.klass != klass:
            continue
        if origin is not None and s.origin != origin:
            continue
        yield s


def params_for_group(group: str) -> tuple[str, ...]:
    """The fields a study group owns (tunable and derived), in registry order."""
    return tuple(s.name for s in REGISTRY.values() if s.group == group)


def derived_names() -> tuple[str, ...]:
    """Names of every derived spec."""
    return tuple(s.name for s in REGISTRY.values() if s.derive is not None)


def field_names() -> tuple[str, ...]:
    """Names of every ``Params`` struct field (expanded arrays), in declaration order."""
    return tuple(s.name for s in REGISTRY.values() if s.origin == "field")

"""Mainnet → regtest time scaling preserving the ratios the rules read (PLAN §6.3).

Owner: WP-9.

A regtest devnet mines a block every ~2 s, not every 75 s, and a test cannot wait 2,016 blocks for
a slow median. :func:`scale_to_regtest` therefore divides every *block count* of a mainnet-scale
set by a factor ``f`` (default: ``pSlowWindow / 64``, so the slow window lands on ycash6's regtest
64 — the shipped regtest column uses 31.5× on the windows) while keeping what the rules depend on:

* **fill fractions** — the min-fills are *derived* (``ceil(W/2)``, ``ceil(2W/3)``) and recomputed
  from the scaled windows by :meth:`ParamSet.replace`;
* **threshold fractions of** ``signalWindow`` — each count ``c`` becomes ``ceil(c · S' / S)``, then
  the strict ordering ``floor < participation ≤ resume < activation ≤ window`` is re-imposed;
* **the σ estimator's sample count** ``volWindow / volStep`` — kept exactly; ``volPeriodsPerYear``
  stays 8,760 (K13, ``docs/decisions.md`` D-4);
* **class ranges contiguous** — ``classMin[i+1] = classMax[i] + 1`` and ``classMin[0] = grace``
  when it was so on mainnet;
* **W21** ``abandonBlocks ≥ grace``; ``emergencyPersist < emergencyNoticeTtl``;
  ``mSelect + kSlack ≤ bundleMax``, ``nSlots ≥ mSelect + kSlack`` (all counts kept);
* every other PLAN §1.4 invariant that applies at regtest scale (checked; a violation raises).

Not scaled: basis points, amounts (zat, cents), selection counts, protocol constants
(``REF_WINDOW``, ``DEFAULT_REF_LAG`` …) and the runtime flags ``sigmaRefBps``, ``supplyCapBps``,
``attestArmMin``, ``bundleCarrier``. ``bondMin`` takes the shipped regtest value (10 YEC) by
default, because a devnet wallet cannot fund three 20,000-YEC bonds (``docs/decisions.md``
D-WP9-2); pass ``bond_min="mainnet"`` to keep the mainnet amount.

Two factors are available: ``factor`` for the oracle / activation / attestation cadence and
``term_factor`` (default: the same) for vault terms, grace, abandonment and bond lifetimes. A
single factor keeps every term-to-window ratio; the shipped regtest column compresses terms far
more (grace 24 vs 34,560, ~1,440×) so a vault lifecycle fits in a functional test.

Every ratio the integers could not hold exactly is returned as a :class:`RatioLoss`;
:func:`compare_to_shipped` explains every difference from ycash6's shipped regtest column.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Literal

from ybcal.params.invariants import Violation
from ybcal.params.paramset import ParamSet
from ybcal.params.paramset import regtest as shipped_regtest
from ybcal.params.registry import REGISTRY
from ybcal.types import ParamValue
from ybcal.units import COIN

OWNER_WP = "WP-9"

#: ycash6's regtest pSlowWindow; the default factor maps the mainnet slow window onto it.
REGTEST_SLOW_TARGET: int = 64

#: Shipped regtest bond floor (``RegtestParams``: ``r.bondMin = 10 * COIN``).
REGTEST_BOND_MIN: int = 10 * COIN

ScaleKind = Literal["window", "term", "threshold", "rate-count", "vol", "class", "fixed", "flag", "derived"]
LossCause = Literal["rounding", "floor", "invariant", "unscaled"]


@dataclass(frozen=True)
class Rule:
    """How one parameter is scaled."""

    kind: ScaleKind
    floor: int = 1  #: smallest value after scaling (window/term/rate-count)
    per: str | None = None  #: rate-count: the window the count is taken over
    why: str = ""  #: the reason for a non-trivial floor or kind


#: Every block-count rule. Parameters not named here are ``fixed`` (bps, amounts, counts,
#: constants) or ``derived`` (recomputed from their parents).
RULES: dict[str, Rule] = {
    # oracle / activation / attestation cadence — `factor`
    "pFastWindow": Rule("window"),
    "pMidWindow": Rule("window"),
    "pSlowWindow": Rule("window"),
    "signalWindow": Rule("window"),
    "activationDelay": Rule("window"),
    "nReg": Rule("window"),
    "payeeWindow": Rule("window"),
    "nPenalty": Rule("window"),
    "accuracyWindow": Rule("window"),
    "attestArmDelay": Rule("window"),
    "pinWindow": Rule("window"),
    "emergencyPersist": Rule("window"),
    "emergencyNoticeTtl": Rule("window"),
    "dormancyBlocks": Rule("window"),
    "dormancyCheck": Rule("window"),
    "peerLag": Rule(
        "window",
        why="floor ceil(peerMin/2): the judgement window [t-lag, t+lag-1] must "
        "be able to hold peerMin quotes",
    ),
    "attestInterval": Rule(
        "window",
        floor=4,
        why="agent cadence floor: the dir-transport agent polls every 2 s and a devnet "
        "block settles in ~2 s, so k < 4 starves bundles (attestMaxAge = 2k)",
    ),
    # activation thresholds — fractions of signalWindow
    "activationThreshold": Rule("threshold"),
    "participationFloor": Rule("threshold"),
    "enforcementFloor": Rule("threshold"),
    "enforcementResume": Rule("threshold"),
    # volatility estimator — sample count kept exactly
    "volWindow": Rule("vol"),
    "volStep": Rule("vol"),
    # counts taken over a scaled window
    "pinMinTags": Rule(
        "rate-count",
        floor=2,
        per="pinWindow",
        why="a pin needs two equal observations; one tag is no evidence of a frozen feed",
    ),
    "pinMinBundles": Rule("rate-count", floor=2, per="pinWindow", why="a pin needs two equal observations"),
    "dormancyMinBundles": Rule(
        "rate-count",
        floor=2,
        per="dormancyBlocks",
        why="a dormancy test on one bundle ejects on a single miss",
    ),
    # vault terms, abandonment, bond lifetimes — `term_factor`
    "grace": Rule("term"),
    "abandonBlocks": Rule("term", why="W21: never below grace"),
    "bondMinLock": Rule("term"),
    "bondMaturity": Rule("term"),
    "ageCap": Rule("term"),
    "foundingWindow": Rule("term"),
    "classMin[0]": Rule("class"),
    "classMax[0]": Rule("class"),
    "classMin[1]": Rule("class"),
    "classMax[1]": Rule("class"),
    "classMin[2]": Rule("class"),
    "classMax[2]": Rule("class"),
    # runtime flags (and the per-release pair)
    "startHeight": Rule("flag"),
    "enforceUntilHeight": Rule("flag"),
    "sigmaRefBps": Rule("flag"),
    "supplyCapBps": Rule("flag"),
    "attestArmMin": Rule("flag"),
    "bundleCarrier": Rule("flag"),
}


@dataclass(frozen=True)
class RatioLoss:
    """One ratio the scaled set does not hold exactly.

    ``kind`` is ``"scale"`` for a single parameter whose effective factor ``mainnet / regtest``
    differs from the target factor, ``"relation"`` for a named relation between parameters.
    ``rel_error`` is ``|regtest - target| / target`` of the ratio.
    """

    name: str
    kind: Literal["scale", "relation"]
    params: tuple[str, ...]
    mainnet: float
    regtest: float
    target: float
    rel_error: float
    cause: LossCause
    note: str = ""

    def __str__(self) -> str:
        return (
            f"{self.name}: mainnet {self.mainnet:g}, regtest {self.regtest:g} (target {self.target:g}, "
            f"{100 * self.rel_error:.1f} % off; {self.cause}){' — ' + self.note if self.note else ''}"
        )


@dataclass(frozen=True)
class ShippedDiff:
    """One difference between a scaled set and ycash6's shipped regtest column, explained."""

    param: str
    scaled: ParamValue
    shipped: ParamValue
    explanation: str


@dataclass
class ScaledSet:
    """The result of :func:`scale_to_regtest`."""

    params: ParamSet
    source: ParamSet
    factor: Fraction
    term_factor: Fraction
    losses: list[RatioLoss] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def significant_losses(self, threshold: float = 0.05) -> list[RatioLoss]:
        """Losses whose relative error exceeds ``threshold`` (default 5 %)."""
        return [x for x in self.losses if x.rel_error > threshold]

    def to_dict(self) -> dict[str, object]:
        """JSON-ready summary (the overlay plus provenance of the scaling)."""
        return {
            "format": "ybcal-scaled/1",
            "factor": str(self.factor),
            "term_factor": str(self.term_factor),
            "source_digest": self.source.digest(),
            "values": self.params.to_dict(),
            "losses": [x.__dict__ | {"params": list(x.params)} for x in self.losses],
            "notes": list(self.notes),
        }


class ScalingError(ValueError):
    """The scaled set would violate an invariant the scaler cannot repair."""


def _round_half_up(x: Fraction) -> int:
    """Nearest integer, halves away from zero (``x ≥ 0``)."""
    return int((x + Fraction(1, 2)).__floor__())


def _ceil(x: Fraction) -> int:
    return -((-x.numerator) // x.denominator)


def check_regtest(ps: ParamSet) -> list[Violation]:
    """``ps.check()`` for a regtest-scale set, minus the one mainnet-scale clause WP-0's ``sunset``
    invariant still applies there: a non-zero regtest sunset need not be ``start + BLOCKS_PER_YEAR``
    (a scaled sunset is legitimate; contract note ``docs/decisions.md`` D-WP9-3)."""
    out = ps.check()
    if ps.is_regtest_scale and int(ps["enforceUntilHeight"]) > int(ps["startHeight"]):
        out = [v for v in out if v.invariant != "sunset"]
    return out


def default_factor(mainnet: Mapping[str, ParamValue]) -> Fraction:
    """``pSlowWindow / 64`` — 31.5 for the shipped mainnet set."""
    return Fraction(int(mainnet["pSlowWindow"]), REGTEST_SLOW_TARGET)


def scale_to_regtest(
    mainnet: ParamSet,
    factor: float | Fraction | None = None,
    *,
    term_factor: float | Fraction | None = None,
    start_height: int = 1,
    bond_min: Literal["regtest", "mainnet"] = "regtest",
    keep_sunset: bool = False,
) -> ScaledSet:
    """Scale a mainnet-scale set (``network`` ``main``/``test``/``candidate``) to regtest.

    ``factor`` divides the cadence block counts (default :func:`default_factor`); ``term_factor``
    divides vault terms, grace, abandonment and bond lifetimes (default ``factor``). The runtime
    flags keep their mainnet values except ``startHeight`` (``start_height``) and
    ``enforceUntilHeight`` (0 = no sunset, the registry's regtest default; with ``keep_sunset``,
    ``start_height`` + the sunset distance divided by ``term_factor``). Raises
    :class:`ScalingError` if the result fails a
    regtest-scale invariant.
    """
    if mainnet.is_regtest_scale:
        raise ScalingError("the source set is already at regtest scale")
    f = default_factor(mainnet) if factor is None else Fraction(factor).limit_denominator(10_000)
    tf = f if term_factor is None else Fraction(term_factor).limit_denominator(10_000)
    if f <= 0 or tf <= 0:
        raise ScalingError("scale factors must be positive")
    src = {k: mainnet[k] for k in mainnet}
    out: dict[str, ParamValue] = {}
    floors_hit: dict[str, str] = {}
    fixups: dict[str, str] = {}
    notes: list[str] = []

    def i(k: str) -> int:
        return int(src[k])

    def scaled(k: str, by: Fraction, floor: int = 1) -> int:
        v = _round_half_up(Fraction(i(k)) / by)
        if v < floor:
            floors_hit[k] = f"floor {floor}"
            return floor
        return v

    # cadence windows
    for k, r in RULES.items():
        if r.kind == "window" and k not in ("peerLag", "attestInterval"):
            out[k] = scaled(k, f, r.floor)
    out["attestInterval"] = scaled("attestInterval", f, RULES["attestInterval"].floor)
    out["peerLag"] = scaled("peerLag", f, max(1, _ceil(Fraction(i("peerMin"), 2))))

    # strict price-window ordering (PLAN §1.4)
    if not out["pFastWindow"] < out["pMidWindow"]:
        out["pMidWindow"] = out["pFastWindow"] + 1
        fixups["pMidWindow"] = "raised to keep pFast < pMid"
    if not out["pMidWindow"] < out["pSlowWindow"]:
        out["pSlowWindow"] = out["pMidWindow"] + 1
        fixups["pSlowWindow"] = "raised to keep pMid < pSlow"

    # thresholds as fractions of the signal window
    s_old, s_new = i("signalWindow"), int(out["signalWindow"])
    for k in ("activationThreshold", "participationFloor", "enforcementFloor", "enforcementResume"):
        out[k] = _ceil(Fraction(i(k) * s_new, s_old))
    ef, pf, er, at = (
        int(out[k])
        for k in ("enforcementFloor", "participationFloor", "enforcementResume", "activationThreshold")
    )
    at = min(at, s_new)
    er = min(max(er, pf), at - 1)
    pf = min(pf, er)
    ef = min(ef, pf - 1)
    for k, v in (
        ("enforcementFloor", ef),
        ("participationFloor", pf),
        ("enforcementResume", er),
        ("activationThreshold", at),
    ):
        if v != out[k]:
            fixups[k] = "moved to keep floor < participation <= resume < activation <= window"
            out[k] = v

    # volatility: keep the sample count N = volWindow / volStep exactly
    n_samples = i("volWindow") // i("volStep")
    if i("volWindow") % i("volStep"):
        raise ScalingError("mainnet volWindow is not a multiple of volStep")
    target_window = Fraction(i("volWindow")) / f
    exact_step = Fraction(i("volStep")) / f
    candidates = {max(1, exact_step.__floor__()), max(1, _ceil(exact_step))}
    step = min(sorted(candidates), key=lambda st: (abs(st * n_samples - target_window), st))
    out["volStep"], out["volWindow"] = step, step * n_samples

    # counts over a scaled window (rate preserved, floored)
    for k, r in RULES.items():
        if r.kind == "rate-count":
            assert r.per is not None
            v = _round_half_up(Fraction(i(k) * int(out[r.per]), i(r.per)))
            if v < r.floor:
                floors_hit[k] = f"floor {r.floor}"
                v = r.floor
            out[k] = v
    for k, per in (
        ("pinMinTags", "pinWindow"),
        ("pinMinBundles", "pinWindow"),
        ("dormancyMinBundles", "dormancyBlocks"),
    ):
        if int(out[k]) > int(out[per]):
            out[k] = int(out[per])
            fixups[k] = f"capped at {per}"

    # terms
    for k in ("grace", "abandonBlocks", "bondMinLock", "bondMaturity", "ageCap", "foundingWindow"):
        out[k] = scaled(k, tf)
    if int(out["abandonBlocks"]) < int(out["grace"]):
        out["abandonBlocks"] = out["grace"]
        fixups["abandonBlocks"] = "raised to grace (W21)"
    cmin0_is_grace = i("classMin[0]") == i("grace")
    out["classMin[0]"] = out["grace"] if cmin0_is_grace else scaled("classMin[0]", tf)
    for c in range(3):
        if c > 0:
            out[f"classMin[{c}]"] = int(out[f"classMax[{c - 1}]"]) + 1
        mx = scaled(f"classMax[{c}]", tf)
        if mx < int(out[f"classMin[{c}]"]):
            mx = int(out[f"classMin[{c}]"])
            fixups[f"classMax[{c}]"] = "raised to classMin (non-empty class)"
        out[f"classMax[{c}]"] = mx
    if int(out["emergencyPersist"]) >= int(out["emergencyNoticeTtl"]):
        out["emergencyNoticeTtl"] = int(out["emergencyPersist"]) + 1
        fixups["emergencyNoticeTtl"] = "raised above emergencyPersist"

    # flags and identity
    out["startHeight"] = start_height
    until, start = i("enforceUntilHeight"), i("startHeight")
    out["enforceUntilHeight"] = (
        start_height + _round_half_up(Fraction(until - start) / tf) if keep_sunset and until > 0 else 0
    )
    out["network"] = "regtest"
    out["addressVersion"] = shipped_regtest()["addressVersion"]
    if bond_min == "regtest":
        out["bondMin"] = REGTEST_BOND_MIN
        notes.append(
            "bondMin = 10 YEC (shipped regtest value; a devnet wallet cannot fund mainnet bonds), "
            "docs/decisions.md D-WP9-2"
        )
    if int(src["attestArmMin"]) > 3:
        notes.append(
            f"attestArmMin = {src['attestArmMin']} (not scaled): the devnet attestation scenarios "
            "register max(3, attestArmMin) emulated seats so the layer arms (D-RD-DEV-4)"
        )

    res = mainnet.replace(out)  # derived values (min-fills, qHigh, attestMaxAge, 8,760 …) recomputed
    violations = check_regtest(res)
    if keep_sunset and int(out["enforceUntilHeight"]) > 0:
        notes.append(
            "enforceUntilHeight is a scaled sunset; the mainnet-scale `sunset` invariant was not "
            "applied (docs/decisions.md D-WP9-3)"
        )
    if violations:
        raise ScalingError(
            "scaled set violates: " + "; ".join(f"{v.invariant}: {v.message}" for v in violations)
        )
    for k, why in fixups.items():
        notes.append(f"{k}: {why}")
    losses = _losses(mainnet, res, f, tf, floors_hit, fixups)
    return ScaledSet(params=res, source=mainnet, factor=f, term_factor=tf, losses=losses, notes=notes)


# ---------------------------------------------------------------------------------------------------
# Ratio accounting

#: Named relations between parameters: (name, numerator terms, denominator terms). A term is a
#: parameter name or an integer constant; the ratio is the product of numerators over the product
#: of denominators.
RELATIONS: tuple[tuple[str, tuple[str | int, ...], tuple[str | int, ...]], ...] = (
    ("pFastMinFill/pFastWindow", ("pFastMinFill",), ("pFastWindow",)),
    ("pMidMinFill/pMidWindow", ("pMidMinFill",), ("pMidWindow",)),
    ("pSlowMinFill/pSlowWindow", ("pSlowMinFill",), ("pSlowWindow",)),
    ("pMidWindow/pFastWindow", ("pMidWindow",), ("pFastWindow",)),
    ("pSlowWindow/pMidWindow", ("pSlowWindow",), ("pMidWindow",)),
    ("activationThreshold/signalWindow", ("activationThreshold",), ("signalWindow",)),
    ("participationFloor/signalWindow", ("participationFloor",), ("signalWindow",)),
    ("enforcementFloor/signalWindow", ("enforcementFloor",), ("signalWindow",)),
    ("enforcementResume/signalWindow", ("enforcementResume",), ("signalWindow",)),
    ("activationDelay/signalWindow", ("activationDelay",), ("signalWindow",)),
    ("volWindow/volStep", ("volWindow",), ("volStep",)),
    ("volWindow/pSlowWindow", ("volWindow",), ("pSlowWindow",)),
    ("volStep/pFastWindow", ("volStep",), ("pFastWindow",)),
    ("abandonBlocks/grace", ("abandonBlocks",), ("grace",)),
    ("classMin[0]/grace", ("classMin[0]",), ("grace",)),
    ("classMax[0]/classMin[0]", ("classMax[0]",), ("classMin[0]",)),
    ("classMax[1]/classMin[1]", ("classMax[1]",), ("classMin[1]",)),
    ("classMax[2]/classMin[2]", ("classMax[2]",), ("classMin[2]",)),
    ("emergencyPersist/emergencyNoticeTtl", ("emergencyPersist",), ("emergencyNoticeTtl",)),
    ("pinMinTags/pinWindow", ("pinMinTags",), ("pinWindow",)),
    ("pinMinBundles/pinWindow", ("pinMinBundles",), ("pinWindow",)),
    ("dormancyMinBundles/dormancyBlocks", ("dormancyMinBundles",), ("dormancyBlocks",)),
    ("dormancyCheck/dormancyBlocks", ("dormancyCheck",), ("dormancyBlocks",)),
    ("2*peerLag/peerMin", (2, "peerLag"), ("peerMin",)),
    ("attestMaxAge/pFastWindow", ("attestMaxAge",), ("pFastWindow",)),
    ("payeeWindow/pFastWindow", ("payeeWindow",), ("pFastWindow",)),
    ("nPenalty/accuracyWindow", ("nPenalty",), ("accuracyWindow",)),
    ("attestArmDelay/bondMaturity", ("attestArmDelay",), ("bondMaturity",)),
    ("bondMinLock/grace", ("bondMinLock",), ("grace",)),
)

#: Relations whose regtest value is *expected* to differ because one side is deliberately not
#: scaled (a protocol constant or a selection count): reported with cause ``unscaled``.
_UNSCALED_SIDE = {"2*peerLag/peerMin"}


def _ratio(ps: Mapping[str, ParamValue], num: tuple[str | int, ...], den: tuple[str | int, ...]) -> Fraction:
    n = Fraction(1)
    for t in num:
        n *= t if isinstance(t, int) else int(ps[t])
    d = Fraction(1)
    for t in den:
        d *= t if isinstance(t, int) else int(ps[t])
    return n / d


def _losses(
    main: ParamSet,
    reg: ParamSet,
    f: Fraction,
    tf: Fraction,
    floors_hit: Mapping[str, str],
    fixups: Mapping[str, str],
) -> list[RatioLoss]:
    out: list[RatioLoss] = []
    by_factor = {
        k: (tf if RULES[k].kind in ("term", "class") else f)
        for k, r in RULES.items()
        if r.kind in ("window", "term", "class", "vol")
    }
    for k, fac in by_factor.items():
        m, r = int(main[k]), int(reg[k])
        if r == 0 or m == 0:
            continue
        eff = Fraction(m, r)
        if eff == fac:
            continue
        cause: LossCause = "floor" if k in floors_hit else "invariant" if k in fixups else "rounding"
        note = floors_hit.get(k) or fixups.get(k) or ""
        if k in ("volWindow", "volStep"):
            cause, note = "rounding", "sample count volWindow/volStep kept exactly; window follows the step"
        out.append(
            RatioLoss(
                name=f"{k} scale",
                kind="scale",
                params=(k,),
                mainnet=float(m),
                regtest=float(r),
                target=float(fac),
                rel_error=float(abs(eff - fac) / fac),
                cause=cause,
                note=note,
            )
        )
    for name, num, den in RELATIONS:
        a, b = _ratio(main, num, den), _ratio(reg, num, den)
        if a == b or a == 0:
            continue
        involved = tuple(t for t in num + den if isinstance(t, str))
        roots = set(involved) | {p for t in involved for p in REGISTRY[t].parents}
        cause = (
            "unscaled"
            if name in _UNSCALED_SIDE
            else "floor"
            if roots & set(floors_hit)
            else "invariant"
            if roots & set(fixups)
            else "rounding"
        )
        out.append(
            RatioLoss(
                name=name,
                kind="relation",
                params=involved,
                mainnet=float(a),
                regtest=float(b),
                target=float(a),
                rel_error=float(abs(b - a) / a),
                cause=cause,
            )
        )
    return out


# ---------------------------------------------------------------------------------------------------
# The shipped regtest column, explained

#: Why ycash6's hand-picked regtest column (``RegtestParams()`` at the pin) differs from a uniform
#: scaling of the shipped mainnet set. Keyed by parameter; ``compare_to_shipped`` fails loudly on a
#: difference that has no entry, so a new difference cannot pass unexplained.
SHIPPED_REGTEST_NOTES: dict[str, str] = {
    "pFastWindow": "shipped 8 (12× not 31.5×): the fast median needs >= 4 tags so three round-robin pools "
    "all appear in it; a uniform scale gives 3 (fill 2)",
    "pFastMinFill": "derived ceil(W/2) from the different fast window",
    "pMidWindow": "shipped 24 (24×): kept at 3× the fast window; a uniform scale gives 18",
    "pMidMinFill": "derived ceil(2W/3) from the different mid window",
    "nReg": "shipped 24 = pMidWindow on regtest (mainnet nReg = pMidWindow = 576); scaling gives 18",
    "accuracyWindow": "shipped 24 = pMidWindow (mainnet accuracyWindow = pMidWindow = 576); scaling gives 18",
    "nPenalty": "shipped 12 = accuracyWindow/2 (mainnet 288 = 576/2); scaling gives 9",
    "payeeWindow": "shipped 10 (10×): enough tagged blocks for three pools to stay eligible payees; "
    "scaling gives 3",
    "peerLag": "shipped 4 with peerMin 3 (devnet has three pools); the scaler keeps peerMin = 5 and "
    "floors peerLag at ceil(peerMin/2) = 3",
    "peerMin": "shipped 3: the devnet runs three pools, so judgement needs at most three peers; the "
    "scaler keeps the mainnet count 5 (counts are not scaled)",
    "volWindow": "shipped 64 = pSlowWindow with volStep 8 (8 samples); the scaler keeps the mainnet "
    "sample count 42 (2016/48), giving volStep 2 / volWindow 84",
    "volStep": "shipped 8 (8 samples per window); the scaler keeps 42 samples (see volWindow)",
    "grace": "shipped 24 (1,440×): terms are compressed far beyond the windows so a vault lifecycle fits "
    "in a functional test; the scaler uses term_factor (default = factor)",
    "abandonBlocks": "shipped 128 = 2·signalWindow (the pre-W21 rule; W21 changed mainnet only): the scaler "
    "applies W21 (= grace on mainnet) at term scale",
    "classMin[0]": "shipped 48 (2·grace); mainnet classMin[0] = grace, which the scaler keeps",
    "classMax[0]": "shipped 96: classes compressed to a 48-block ladder for tests (term compression)",
    "classMin[1]": "contiguous after the different classMax[0]",
    "classMax[1]": "shipped 144: term compression (see classMax[0])",
    "classMin[2]": "contiguous after the different classMax[1]",
    "classMax[2]": "shipped 240: term compression (see classMax[0])",
    "attestArmDelay": "shipped 8 (144×): arming in a few blocks for tests; scaling gives 37",
    "emergencyPersist": "shipped 4 (12×) to let a test see the emergency path; scaling gives 2",
    "emergencyNoticeTtl": "shipped 64 (18×) = one signal window; scaling gives 37",
    "pinWindow": "shipped 16 = 2·pFastWindow on regtest; scaling gives 9 (mainnet 288 = 3·pFast)",
    "pinMinTags": "shipped 2 — the scaler's floor (two equal observations) reproduces it",
    "bondMinLock": "shipped 200 (bonds lock a few hundred blocks in tests); mainnet = BLOCKS_PER_YEAR, "
    "scaled by term_factor",
    "bondMaturity": "shipped 8 (2,016×): attestors seat within a test; scaling gives 512",
    "ageCap": "shipped 64 (3,240×): weight saturates within a test; term-scaled otherwise",
    "foundingWindow": "shipped 16 (504×): founding cohort within a test; term-scaled otherwise",
    "dormancyBlocks": "shipped 16 (1,008×) so dormancy is observable in a test; scaling gives 512",
    "dormancyCheck": "shipped 4 (12×); scaling gives 2",
    "dormancyMinBundles": "shipped 2 — the scaler's floor reproduces it",
    "nSlots": "shipped 5: the devnet seats three to four attestors; the scaler keeps the mainnet count 9",
    "mSelect": "shipped 2 (bundles from a three-attestor devnet); the scaler keeps 4",
    "kSlack": "shipped 1; the scaler keeps 2",
    "walletConfirmations": "shipped 1 (tests do not wait for six blocks); wallet policy, kept at mainnet 6",
    "startHeight": "flag; the scaler uses start_height (default 1, the registry's regtest default)",
    "enforceUntilHeight": "flag; registry regtest default 0 = no sunset, the scaler keeps a scaled sunset "
    "(keep_sunset=False gives 0)",
    "sigmaRefBps": "flag; registry regtest default 0 (multiplier fixed at 1), the scaler keeps mainnet bps",
    "supplyCapBps": "flag; registry regtest default 0 (no cap), the scaler keeps mainnet bps",
    "attestArmMin": "flag; registry regtest default 3 (header default), the scaler keeps the mainnet count",
}


def compare_to_shipped(scaled: ScaledSet | ParamSet, shipped: ParamSet | None = None) -> list[ShippedDiff]:
    """Every difference between ``scaled`` and the shipped regtest column, with its explanation.

    Raises :class:`KeyError` naming any difference :data:`SHIPPED_REGTEST_NOTES` does not explain.
    """
    ps = scaled.params if isinstance(scaled, ScaledSet) else scaled
    ref = shipped if shipped is not None else shipped_regtest()
    diffs = ps.diff(ref)
    unexplained = [k for k in diffs if k not in SHIPPED_REGTEST_NOTES]
    if unexplained:
        raise KeyError(f"unexplained differences from the shipped regtest column: {unexplained}")
    return [ShippedDiff(k, a, b, SHIPPED_REGTEST_NOTES[k]) for k, (a, b) in diffs.items()]

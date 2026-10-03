"""The C++ worked examples, as data (owner: WP-1; PLAN §8 "Kernels").

Every numeric assertion of ``src/test/yellowback_math_tests.cpp`` @ ``7702d22`` that a kernel can
express, ported one for one (``cpp_line`` is the BOOST_CHECK line). ``tests/model`` parametrises
over :data:`EXAMPLES` and ``ybcal verify`` prints them. Mainnet values are the C++ ``MainParams()``
literals, which the registry re-checks against ``params.cpp``; the regtest ones are
``RegtestParams()`` (bondMin 10 YEC, ageCap 64).

Also: :func:`reference_param_drift` compares the reference model's own mainnet column
(``yellowback_model.Params.mainnet``) with the registry, the one place the model carries values
of its own.
"""

# ruff: noqa: E501  (a fixture table: one worked example per entry reads better unwrapped)

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ybcal.model import kernels as K
from ybcal.model import reference as ref

OWNER_WP = "WP-1"

COIN = K.COIN
MAX_MONEY = K.MAX_MONEY
PRICE_MIN, PRICE_MAX = 100, 100_000_000

# MainParams() literals (yellowback_math_tests.cpp:331-376, 425-463)
SIGMA_REF, PPY, SIGMA_MAX = 10_000, 8_760, 30_000
FEE_MIN, FEE_BPS = 50_000_000, 25
CLAIM_THR, ATTEST_FEE_BPS, RESIDUAL_MIN = 11_000, 2_500, 100_000
BOND_MIN, AGE_CAP = 20_000 * COIN, 207_360
Q_LOW, Q_HIGH = 3_333, 6_667
MIN_MINT, MAX_MINT, MAX_OUTPUT = 10_000, 1_000_000, 10_000_000
BASE_RATIO = (50_000, 40_000, 30_000)
P_FAST_WINDOW, VOL_WINDOW, VOL_STEP = 96, 2_016, 48
CLASS_MIN, CLASS_MAX = (34_560, 103_681, 420_481), (103_680, 420_480, 2_102_400)
REGTEST_CLASS_MIN, REGTEST_CLASS_MAX = (48, 97, 145), (96, 144, 240)


@dataclass(frozen=True)
class Example:
    name: str
    rule: str
    cpp_line: int
    fn: Callable[[], object]
    expected: object

    def run(self) -> tuple[bool, object]:
        got = self.fn()
        return got == self.expected, got


def _series(n: int, value: int) -> list[int | None]:
    return [value] * n


def _sigma(s, ref_bps=SIGMA_REF):
    return K.sigma_mult_bps(s, ref_bps, PPY, SIGMA_MAX)


def _with(seq, idx, val):
    s = list(seq)
    s[idx] = val
    return s


def _wild():
    w = _series(43, 50_000)
    for i in range(0, 43, 2):
        w[i] = 100_000
    return w


def _cross_pool():
    """math_tests:97-115: P_fast sampled every VOL_STEP over 2,016 + 96 alternating quotes."""
    raw = [51_000 if i % 2 else 49_000 for i in range(VOL_WINDOW + P_FAST_WINDOW)]
    pf = []
    for k in range(VOL_WINDOW // VOL_STEP + 1):
        end = len(raw) - k * VOL_STEP
        pf.append(K.lower_median(raw[end - P_FAST_WINDOW:end]))
    return len(pf), _sigma(pf), _sigma(raw[:43])


def _wp(*pairs):
    return list(pairs)


W = 1000
FOUR = _wp((10, W), (20, W), (30, W), (40, W))
SHUFFLED = _wp((40, W), (10, W), (30, W), (20, W))
THREE = _wp((10, 1), (20, 1), (30, 1))
TWO = _wp((10, 1), (20, 1))
HEAVY_LOW = _wp((10, 3), (20, 1))
WHALE = _wp((10, 1), (20, 100), (30, 1))
TIED = _wp((10, W), (20, W), (20, W), (20, W), (30, W))
ZEROS = _wp((10, 0), (20, 1))
ALL_ZERO = _wp((30, 0), (10, 0))
BIG = [(10 * (i + 1), K.bond_weight(MAX_MONEY, AGE_CAP, AGE_CAP)) for i in range(6)]
AT_THRESHOLD = 550_000_000_000


def _e(name, rule, line, fn, expected) -> Example:
    return Example(name, rule, line, fn, expected)


EXAMPLES: tuple[Example, ...] = (
    # PRICE-1 lower median (math_tests:31-44)
    _e("lower_median empty", "PRICE-1", 33, lambda: K.lower_median([]), None),
    _e("lower_median {5}", "PRICE-1", 34, lambda: K.lower_median([5]), 5),
    _e("lower_median {1,2}", "PRICE-1", 35, lambda: K.lower_median([1, 2]), 1),
    _e("lower_median {3,1,2}", "PRICE-1", 36, lambda: K.lower_median([3, 1, 2]), 2),
    _e("lower_median {4,1,3,2}", "PRICE-1", 37, lambda: K.lower_median([4, 1, 3, 2]), 2),
    _e("lower_median {7,7,7,1}", "PRICE-1", 38, lambda: K.lower_median([7, 7, 7, 1]), 7),
    _e("lower_median {PRICE_MAX,PRICE_MIN}", "PRICE-1", 39, lambda: K.lower_median([PRICE_MAX, PRICE_MIN]),
       PRICE_MIN),
    _e("lower_median 96 alternating", "PRICE-1", 43,
       lambda: K.lower_median([51_000 if i % 2 else 49_000 for i in range(96)]), 49_000),
    # SIGMA-1 isqrt (math_tests:47-65)
    _e("isqrt small", "SIGMA-1", 49, lambda: [K.isqrt(x) for x in (0, 1, 2, 3, 4, 15, 16)], [0, 1, 1, 1, 2, 3, 4]),
    _e("isqrt 1e12", "SIGMA-1", 56, lambda: (K.isqrt(10**12), K.isqrt(10**12 - 1)), (10**6, 999_999)),
    _e("isqrt K13/V17 1e4*420480", "SIGMA-1", 59, lambda: K.isqrt(10_000 * 420_480), 64_844),
    _e("isqrt 2^256-1", "SIGMA-1", 63, lambda: K.isqrt(2**256 - 1), 2**128 - 1),
    # SIGMA-1 worked example (math_tests:68-90)
    _e("sigma flat 43", "SIGMA-1", 72, lambda: _sigma(_series(43, 50_000)), 10_000),
    _e("sigma one 10% move", "SIGMA-1", 76, lambda: _sigma(_with(_series(43, 50_000), 0, 55_000)), 14_441),
    _e("sigma same move other side", "SIGMA-1", 80, lambda: _sigma(_with(_series(43, 55_000), 0, 50_000)), 13_127),
    _e("sigma clamp at cap", "SIGMA-1", 84, lambda: _sigma(_wild()), SIGMA_MAX),
    _e("sigma floored at 1x", "SIGMA-1", 86,
       lambda: _sigma(_with(_series(43, 50_000), 0, 55_000), 1_000_000), 10_000),
    _e("sigma ref 0 fixes 1x", "SIGMA-1", 88, lambda: (_sigma(_wild(), 0), _sigma([], 0)), (10_000, 10_000)),
    # SIGMA-1 V17 regression (math_tests:97-115)
    _e("sigma cross-pool noise is 1x on P_fast, cap on raw", "SIGMA-1", 111, _cross_pool, (43, 10_000, SIGMA_MAX)),
    # SIGMA-1 first sample at start height (math_tests:121-135)
    _e("sigma oldest sample undefined", "SIGMA-1", 127, lambda: _sigma(_with(_series(43, 50_000), 42, None)),
       SIGMA_MAX),
    _e("sigma middle sample undefined", "SIGMA-1", 131, lambda: _sigma(_with(_series(43, 50_000), 20, None)),
       SIGMA_MAX),
    _e("sigma fewer than two samples", "SIGMA-1", 133, lambda: (_sigma(_series(1, 50_000)), _sigma([])),
       (SIGMA_MAX, SIGMA_MAX)),
    # MINT-5 (math_tests:138-192)
    _e("min_ratio", "MINT-5", 140,
       lambda: [K.min_ratio_bps(*a) for a in ((50_000, 10_000), (50_000, 30_000), (30_000, 14_441), (0, 10_000))],
       [50_000, 150_000, 43_323, 0]),
    _e("required $100 @300% $0.05", "MINT-5", 148, lambda: K.required_zat(10_000, 30_000, 50_000), 600_000_000_000),
    _e("required ceiling $1 @300% $0.07", "MINT-5", 152, lambda: K.required_zat(100, 30_000, 70_000), 4_285_714_286),
    _e("required rounded 1000", "MINT-5", 156,
       lambda: (K.required_zat_rounded(100, 30_000, 70_000), K.required_zat_rounded(10_000, 30_000, 50_000)),
       (4_285_715_000, 600_000_000_000)),
    _e("required rounded granularity 1", "MINT-5", 162, lambda: K.required_zat_rounded(100, 30_000, 70_000, 1),
       4_285_714_286),
    _e("required undefined inputs", "MINT-5", 164,
       lambda: (K.required_zat(0, 30_000, 50_000), K.required_zat(10_000, 0, 50_000),
                K.required_zat(10_000, 30_000, 0)), (None, None, None)),
    _e("K14 worst ratio", "MINT-5", 177, lambda: K.min_ratio_bps(BASE_RATIO[0], SIGMA_MAX), 150_000),
    _e("K14 maxMint at PRICE_MIN unsatisfiable", "MINT-5", 178,
       lambda: (K.required_zat(MAX_MINT, 150_000, PRICE_MIN), K.required_zat_rounded(MAX_MINT, 150_000, PRICE_MIN)),
       (None, None)),
    _e("K14 maxMint at $0.05", "MINT-5", 183, lambda: K.required_zat(MAX_MINT, 150_000, 50_000), 300_000_000_000_000),
    _e("required exactly MAX_MONEY / one over", "MINT-5", 186,
       lambda: (K.required_zat(210_000_000_000, 10_000, PRICE_MAX), K.required_zat(210_000_000_001, 10_000, PRICE_MAX)),
       (MAX_MONEY, None)),
    _e("required maxOutput at PRICE_MAX", "MINT-5", 191, lambda: K.required_zat(MAX_OUTPUT, 30_000, PRICE_MAX),
       3000 * COIN),
    # MINT-6 (math_tests:195-211)
    _e("cap_cents 1e6 YEC @ $0.05", "MINT-6", 199, lambda: K.cap_cents(1_000_000 * COIN, 50_000), 5_000_000),
    _e("supply_cap 15%", "MINT-6", 201, lambda: K.supply_cap_cents(1_000_000 * COIN, 50_000, 1_500), 750_000),
    _e("cap floor", "MINT-6", 203, lambda: (K.cap_cents(3, 50_000), K.supply_cap_cents(1_000_000 * COIN, 50_000, 1)),
       (0, 500)),
    _e("no cap / undefined price", "MINT-6", 206,
       lambda: (K.supply_cap_cents(1_000_000 * COIN, 50_000, 0), K.cap_cents(1_000_000 * COIN, None),
                K.supply_cap_cents(1_000_000 * COIN, None, 1_500)), (None, None, None)),
    _e("cap full issuance at PRICE_MAX", "MINT-6", 210, lambda: K.cap_cents(MAX_MONEY, PRICE_MAX), 210_000_000_000),
    # HALT-2 (math_tests:214-226)
    _e("global ratio 300%", "HALT-2", 217, lambda: K.global_ratio_bps(600_000_000_000, 50_000, 10_000), 30_000),
    _e("global ratio 250% / one cent more", "HALT-2", 219,
       lambda: (K.global_ratio_bps(500_000_000_000, 50_000, 10_000), K.global_ratio_bps(500_000_000_000, 50_000, 10_001)),
       (25_000, 24_997)),
    _e("global ratio no supply / undefined", "HALT-2", 222,
       lambda: (K.global_ratio_bps(500_000_000_000, 50_000, 0), K.global_ratio_bps(500_000_000_000, None, 10_000)),
       (None, None)),
    _e("global ratio at the top", "HALT-2", 225, lambda: K.global_ratio_bps(MAX_MONEY, PRICE_MAX, 1),
       2_100_000_000_000_000),
    # RED-4 (math_tests:229-244)
    _e("underwater at 18,333 not 18,334", "RED-4", 233,
       lambda: (K.is_underwater(600_000_000_000, 18_333, 10_000, CLAIM_THR),
                K.is_underwater(600_000_000_000, 18_334, 10_000, CLAIM_THR)), (True, False)),
    _e("underwater threshold price", "RED-4", 231, lambda: K.underwater_price(600_000_000_000, 10_000, CLAIM_THR),
       18_333),
    _e("underwater bounds", "RED-4", 235,
       lambda: (K.is_underwater(600_000_000_000, PRICE_MIN, 10_000, CLAIM_THR),
                K.is_underwater(600_000_000_000, PRICE_MAX, 10_000, CLAIM_THR)), (True, False)),
    _e("underwater undefined pClaim", "RED-4", 238, lambda: K.is_underwater(600_000_000_000, None, 10_000, CLAIM_THR),
       False),
    _e("underwater zero collateral / zero debt", "RED-4", 240,
       lambda: (K.is_underwater(0, 50_000, 1, CLAIM_THR), K.is_underwater(0, 50_000, 0, CLAIM_THR)), (True, False)),
    _e("underwater big numbers", "RED-4", 243, lambda: K.is_underwater(MAX_MONEY, PRICE_MAX, MAX_MINT, CLAIM_THR),
       False),
    # FEE-1 (math_tests:247-268)
    _e("fee min dominates", "FEE-1", 251,
       lambda: [K.fee_zat(c, FEE_MIN, FEE_BPS) for c in (199 * COIN, 200 * COIN, 200 * COIN - 1, 1, 0)],
       [FEE_MIN] * 5),
    _e("fee 201 YEC", "FEE-1", 254, lambda: K.fee_zat(201 * COIN, FEE_MIN, FEE_BPS), 50_250_000),
    _e("fee 6000 YEC", "FEE-1", 258, lambda: K.fee_zat(6000 * COIN, FEE_MIN, FEE_BPS), 15 * COIN),
    _e("fee at MAX_MONEY", "FEE-1", 265,
       lambda: (K.fee_zat(MAX_MONEY, FEE_MIN, FEE_BPS), K.fee_zat(MAX_MONEY, FEE_MIN, 10_000)),
       (5_250_000_000_000, MAX_MONEY)),
    # MINT-2 classes (math_tests:271-297)
    _e("class for lock blocks (mainnet)", "MINT-2", 274,
       lambda: [K.class_for_lock_blocks(d, CLASS_MIN, CLASS_MAX)
                for d in (34_559, 34_560, 103_680, 103_681, 420_480, 420_481, 2_102_400, 2_102_401, 0, -5)],
       [-1, 0, 0, 1, 1, 2, 2, -1, -1, -1]),
    _e("class for lock blocks (regtest)", "MINT-2", 290,
       lambda: [K.class_for_lock_blocks(d, REGTEST_CLASS_MIN, REGTEST_CLASS_MAX)
                for d in (48, 96, 97, 144, 145, 240, 241)], [0, 0, 1, 1, 2, 2, -1]),
    # ACT-5 W19 (math_tests:304-328), regtest window 64, sunset 1000, halted on [200, 300)
    _e("ACT-5 param set start admissible", "ACT-5", 310,
       lambda: [K.param_set_start_admissible(x, s, 64, lambda h: 200 <= h < 300)
                for x, s in ((1000, 1000), (1001, 1000), (999, 1000), (264, 1000), (263, 1000), (300, 1000),
                             (301, 1000), (250, 1000), (5000, 0), (300, 0))],
       [True, True, False, True, False, True, False, False, False, True]),
    _e("ACT-5 window never below 0", "ACT-5", 324,
       lambda: (K.param_set_start_admissible(64, 0, 64, lambda h: True),
                K.param_set_start_admissible(63, 0, 64, lambda h: True)), (True, False)),
    # BUNDLE-1 bond weight (math_tests:529-550)
    _e("bond weight zero / negative age", "BUNDLE-1", 532,
       lambda: (K.bond_weight(BOND_MIN, 0, AGE_CAP), K.bond_weight(BOND_MIN, -5, AGE_CAP)), (0, 0)),
    _e("bond weight linear", "BUNDLE-1", 534,
       lambda: (K.bond_weight(BOND_MIN, 1, AGE_CAP), K.bond_weight(BOND_MIN, 100, AGE_CAP)), (BOND_MIN, BOND_MIN * 100)),
    _e("bond weight clamp", "BUNDLE-1", 536,
       lambda: (K.bond_weight(BOND_MIN, AGE_CAP + 1, AGE_CAP), K.bond_weight(BOND_MIN, 10**9, AGE_CAP)),
       (BOND_MIN * AGE_CAP, BOND_MIN * AGE_CAP)),
    _e("bond weight R10 4.1e17", "BUNDLE-1", 540, lambda: K.bond_weight(BOND_MIN, AGE_CAP, AGE_CAP),
       414_720_000_000_000_000),
    _e("bond weight MAX_MONEY, 9 slots past int64", "BUNDLE-1", 541,
       lambda: (K.bond_weight(MAX_MONEY, AGE_CAP, AGE_CAP) == MAX_MONEY * AGE_CAP,
                9 * K.bond_weight(MAX_MONEY, AGE_CAP, AGE_CAP) > 2**63 - 1), (True, True)),
    _e("bond weight degenerate", "BUNDLE-1", 544,
       lambda: (K.bond_weight(0, 10, AGE_CAP), K.bond_weight(-1, 10, AGE_CAP), K.bond_weight(BOND_MIN, 10, 0)),
       (0, 0, 0)),
    _e("bond weight regtest", "BUNDLE-1", 549, lambda: K.bond_weight(10 * COIN, 100, 64), 10 * COIN * 64),
    # PRICE-2 weighted quantile (math_tests:556-613)
    _e("wq single entry", "PRICE-2", 561,
       lambda: [K.weighted_quantile([(50_000, W)], q) for q in (Q_LOW, Q_HIGH, 0, 10_000)], [50_000] * 4),
    _e("wq empty / q over 1e4", "PRICE-2", 566,
       lambda: (K.weighted_quantile([], Q_LOW), K.weighted_quantile([(50_000, W)], 10_001)), (None, None)),
    _e("wq four equal", "PRICE-2", 570, lambda: (K.weighted_quantile(FOUR, Q_LOW), K.weighted_quantile(FOUR, Q_HIGH)),
       (20, 30)),
    _e("wq shuffled", "PRICE-2", 574,
       lambda: (K.weighted_quantile(SHUFFLED, Q_LOW), K.weighted_quantile(SHUFFLED, Q_HIGH)), (20, 30)),
    _e("wq exact thresholds (three)", "PRICE-2", 578,
       lambda: [K.weighted_quantile(THREE, q) for q in (3333, 3334, 6666, 6667, 10_000)], [10, 20, 20, 30, 30]),
    _e("wq first >= (two)", "PRICE-2", 585, lambda: (K.weighted_quantile(TWO, 5000), K.weighted_quantile(TWO, 5001)),
       (10, 20)),
    _e("wq heavy low", "PRICE-2", 589,
       lambda: (K.weighted_quantile(HEAVY_LOW, 7500), K.weighted_quantile(HEAVY_LOW, 7501)), (10, 20)),
    _e("wq whale", "PRICE-2", 593, lambda: (K.weighted_quantile(WHALE, Q_LOW), K.weighted_quantile(WHALE, Q_HIGH)),
       (20, 20)),
    _e("wq ties", "PRICE-2", 597, lambda: [K.weighted_quantile(TIED, q) for q in (Q_LOW, Q_HIGH, 2000, 8001)],
       [20, 20, 10, 30]),
    _e("wq zero weights / zero total", "PRICE-2", 603,
       lambda: (K.weighted_quantile(ZEROS, Q_LOW), K.weighted_quantile(ALL_ZERO, Q_LOW)), (20, 10)),
    _e("wq 256-bit weights", "PRICE-2", 609, lambda: (K.weighted_quantile(BIG, Q_LOW), K.weighted_quantile(BIG, Q_HIGH)),
       (20, 50)),
    _e("wq negative q", "PRICE-2", 612, lambda: K.weighted_quantile(FOUR, -1), 10),
    # RED-5 (math_tests:617-679)
    _e("claimant max worked example", "RED-5", 623,
       lambda: (K.claimant_max_zat(10_000, CLAIM_THR, 18_333), K.claimant_max_zat(10_000, CLAIM_THR, 18_333) // COIN),
       (600_010_909_290, 6000)),
    _e("claimant max clause (b)", "RED-5", 628, lambda: K.claimant_max_zat(10_000, 10_000, 18_333), 545_464_462_991),
    _e("at threshold: not yet underwater", "RED-4", 632,
       lambda: (K.is_underwater(AT_THRESHOLD, 20_000, 10_000, CLAIM_THR),
                K.is_underwater(AT_THRESHOLD - 1, 20_000, 10_000, CLAIM_THR)), (False, True)),
    _e("at threshold: claimant takes all", "RED-5", 636, lambda: K.claimant_max_zat(10_000, CLAIM_THR, 20_000),
       AT_THRESHOLD),
    _e("residual around threshold", "RED-5", 637,
       lambda: [K.residual_zat(AT_THRESHOLD + d, AT_THRESHOLD) for d in (0, -1, 1, RESIDUAL_MIN)],
       [0, 0, 1, RESIDUAL_MIN]),
    _e("residual worked vault at 18,333", "RED-5", 642,
       lambda: K.residual_zat(600_000_000_000, K.claimant_max_zat(10_000, CLAIM_THR, 18_333)), 0),
    _e("forced early (b) at 30,000", "RED-5", 647,
       lambda: (K.claimant_max_zat(10_000, 10_000, 30_000),
                K.residual_zat(600_000_000_000, K.claimant_max_zat(10_000, 10_000, 30_000))),
       (333_333_333_334, 266_666_666_666)),
    _e("claimant ceiling", "RED-5", 650, lambda: K.claimant_max_zat(100, 11_000, 70_000), 1_571_428_572),
    _e("claimant undefined inputs", "RED-5", 652,
       lambda: [K.claimant_max_zat(*a) for a in ((0, 11_000, 18_333), (10_000, 0, 18_333), (10_000, 11_000, 0),
                                                 (-1, 11_000, 18_333))], [None] * 4),
    _e("residual undefined claimant max", "RED-5", 657,
       lambda: (K.residual_zat(600_000_000_000, None), K.residual_zat(0, K.claimant_max_zat(10_000, 11_000, 18_333)),
                K.residual_zat(-5, K.claimant_max_zat(10_000, 11_000, 18_333))), (0, 0, 0)),
    _e("claimant overflow at PRICE_MIN", "RED-5", 667,
       lambda: (K.claimant_max_zat(MAX_MINT, CLAIM_THR, PRICE_MIN), K.claimant_max_zat(MAX_MINT, 10_000, PRICE_MIN),
                K.residual_zat(MAX_MONEY, K.claimant_max_zat(MAX_MINT, CLAIM_THR, PRICE_MIN))), (None, None, 0)),
    _e("claimant minMint at PRICE_MIN fits", "RED-5", 673, lambda: K.claimant_max_zat(MIN_MINT, CLAIM_THR, PRICE_MIN),
       110_000_000_000_000),
    _e("claimant exactly MAX_MONEY / one over", "RED-5", 675,
       lambda: (K.claimant_max_zat(210_000_000_000, 10_000, PRICE_MAX),
                K.claimant_max_zat(210_000_000_001, 10_000, PRICE_MAX)), (MAX_MONEY, None)),
    _e("claimant maxOutput at PRICE_MAX", "RED-5", 678, lambda: K.claimant_max_zat(MAX_OUTPUT, CLAIM_THR, PRICE_MAX),
       110_000_000_000),
    # AFEE-1 (math_tests:683-703)
    _e("attest fee quarter of 15 YEC", "AFEE-1", 689,
       lambda: K.attest_fee_zat(K.fee_zat(6000 * COIN, FEE_MIN, FEE_BPS), ATTEST_FEE_BPS), 375_000_000),
    _e("attest fee of feeMin", "AFEE-1", 691, lambda: K.attest_fee_zat(FEE_MIN, ATTEST_FEE_BPS), 12_500_000),
    _e("attest fee floor", "AFEE-1", 693, lambda: [K.attest_fee_zat(f, ATTEST_FEE_BPS) for f in (3, 4, 7)], [0, 1, 1]),
    _e("attest fee degenerate", "AFEE-1", 697,
       lambda: (K.attest_fee_zat(0, ATTEST_FEE_BPS), K.attest_fee_zat(-1, ATTEST_FEE_BPS), K.attest_fee_zat(15 * COIN, 0),
                K.attest_fee_zat(15 * COIN, 10_000)), (0, 0, 0, 15 * COIN)),
    _e("attest fee at MAX_MONEY", "AFEE-1", 701,
       lambda: (K.attest_fee_zat(MAX_MONEY, ATTEST_FEE_BPS), K.attest_fee_zat(MAX_MONEY, 10_000)),
       (MAX_MONEY // 4, MAX_MONEY)),
    # PRICE-2 combine (math_tests:707-750)
    _e("combine conservative side", "PRICE-2", 710,
       lambda: (tuple(K.price_combine(50_000, 52_000, 48_000, 55_000)), tuple(K.price_combine(48_000, 55_000, 50_000, 52_000)),
                tuple(K.price_combine(50_000, 50_000, 50_000, 50_000))),
       ((48_000, 55_000, 52_000), (48_000, 55_000, 52_000), (50_000, 50_000, 50_000))),
    _e("combine undefined inputs", "PRICE-2", 724,
       lambda: [tuple(K.price_combine(*a)) for a in ((None, 52_000, 48_000, 55_000), (50_000, None, 48_000, 55_000),
                                                     (50_000, 52_000, None, 55_000), (50_000, 52_000, 48_000, None),
                                                     (None, None, None, None))],
       [(None, 55_000, 52_000), (48_000, None, None), (None, 55_000, 52_000), (48_000, None, None),
        (None, None, None)]),
    _e("combine bounds", "PRICE-2", 746, lambda: tuple(K.price_combine(PRICE_MIN, PRICE_MAX, PRICE_MAX, PRICE_MIN)),
       (PRICE_MIN, PRICE_MAX, PRICE_MIN)),
)


def run_examples() -> list[tuple[Example, bool, object]]:
    """``[(example, passed, got)]``; an exception counts as a failure with the exception as ``got``."""
    out = []
    for ex in EXAMPLES:
        try:
            ok, got = ex.run()
        except Exception as e:  # pragma: no cover - reported, not raised
            ok, got = False, e
        out.append((ex, ok, got))
    return out


# ---------------------------------------------------------------------------------------------------
# the reference model's own parameter column vs the registry

_SPECIAL = {"enforce_until": "enforceUntilHeight", "price_min": "PRICE_MIN", "price_max": "PRICE_MAX",
            "ref_lag": "DEFAULT_REF_LAG"}
#: model attributes with no registry row (yellowbackFee is the network fee floor, not a Params field)
_NOT_IN_REGISTRY = {"network", "yellowback_fee", "bundle_carrier"}


def _camel(name: str) -> str:
    if name in _SPECIAL:
        return _SPECIAL[name]
    head, *rest = name.split("_")
    return head + "".join(w[:1].upper() + w[1:] for w in rest)


def reference_param_drift(params=None) -> dict[str, tuple[object, object]]:
    """``{registry key: (reference model value, registry value)}`` where the reference model's
    ``Params.mainnet`` column differs from the registry's mainnet set (default
    ``ybcal.params.paramset.mainnet()``)."""
    if params is None:
        from ybcal.params.paramset import mainnet
        params = mainnet()
    m = ref.Params.mainnet(int(params["startHeight"]), int(params["enforceUntilHeight"]))
    out: dict[str, tuple[object, object]] = {}
    for attr, val in vars(m).items():
        if attr in _NOT_IN_REGISTRY:
            continue
        if isinstance(val, list):
            for i, v in enumerate(val):
                key = f"{_camel(attr)}[{i}]"
                if key in params and params[key] != v:
                    out[key] = (v, params[key])
            continue
        key = _camel(attr)
        if key in params and params[key] != val:
            out[key] = (val, params[key])
    return out

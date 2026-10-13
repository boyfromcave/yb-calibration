"""The C++ worked examples, as data (owner: WP-1; PLAN §8 "Kernels").

Every numeric assertion of ``src/test/yellowback_math_tests.cpp`` @ ycash6 ``1310a41a1`` (byte-identical on
ycash-dd ``8af82551b``) that a kernel can express, ported one for one (``cpp_line`` is the BOOST_CHECK
line). ``tests/model`` parametrises over :data:`EXAMPLES` and ``ybcal verify`` prints them. Mainnet values
are the C++ ``MainParams()`` literals of the vault-upgrade line (in-term set, D-IT-*; fees, D-Q-23..26);
the regtest ones are ``RegtestParams()``. The rational cases of ``math_spec_rational_cases``
(``src/test/data/yellowback_math_cases.json``, vendored) are replayed by :func:`run_math_cases`.

The v2/v3 FEE-1 (``FeeZat``) and ACT-5 (``ParamSetStartAdmissible``) examples left the C++ tests with the
vault upgrade; their kernels are retired (:mod:`ybcal.model.retired`) and have no example here.

Also: :func:`reference_param_drift` compares the reference model's own mainnet column
(``yellowback_model.Params.mainnet``) with the registry, the one place the model carries values
of its own.
"""

# ruff: noqa: E501  (a fixture table: one worked example per entry reads better unwrapped)

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources

from ybcal.model import kernels as K
from ybcal.model import reference as ref
from ybcal.model import seats as S

OWNER_WP = "WP-1"

COIN = K.COIN
MAX_MONEY = K.MAX_MONEY
PRICE_MIN, PRICE_MAX = K.PRICE_MIN, K.PRICE_MAX   # D-Q-22: PRICE_MAX = UINT32_MAX µUSD
PRICE_MAX_V2 = 100_000_000                        # the pre-D-Q-22 $100 the older examples keep as a literal

# MainParams() literals (yellowback_math_tests.cpp:524-578, 320-353, 909-1043)
SIGMA_REF_V2, SIGMA_MAX_V2, PPY = 10_000, 30_000, 8_760   # math_tests:78-79: the formula at the v2 reference (live: 0, D-IT-5)
CLAIM_THR, ATTEST_FEE_BPS, RESIDUAL_MIN = 12_500, 5_000, 100_000
RESIDUAL_MIN_CENTS = 48
REGTEST_ATTEST_FEE_BPS = 2_500
BOND_MIN_CAP, AGE_CAP = 20_000 * COIN, 207_360
Q_LOW, Q_HIGH = 3_333, 6_667
MIN_MINT, MAX_MINT, MAX_OUTPUT = 10_000, 10_000_000, 10_000_000
BASE_RATIO = (30_000, 40_000, 50_000)
P_FAST_WINDOW, VOL_WINDOW, VOL_STEP = 96, 2_016, 48
CLASS_MIN, CLASS_MAX = (34_560, 103_681, 207_361), (103_680, 207_360, 420_480)
REGTEST_CLASS_MIN, REGTEST_CLASS_MAX = (48, 97, 145), (96, 144, 240)
FEE_BANDS, FEE_FLOOR = K.FEE_BANDS, K.FEE_FLOOR_CENTS
MAX_VAULT_SHARE_BPS, VAULT_SHARE_FLOOR = 1_000, 1_000_000
EARLY_BPS = (500, 250, 100)


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


def _sigma(s, ref_bps=SIGMA_REF_V2):
    return K.sigma_mult_bps(s, ref_bps, PPY, SIGMA_MAX_V2)


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
AT_THRESHOLD = 625_000_000_000   # $100 at θ 125 % and 20,000 µUSD (math_tests:926)


def _e(name, rule, line, fn, expected) -> Example:
    return Example(name, rule, line, fn, expected)


EXAMPLES: tuple[Example, ...] = (
    # PRICE-1 lower median (math_tests:39-52)
    _e("lower_median empty", "PRICE-1", 41, lambda: K.lower_median([]), None),
    _e("lower_median {5}", "PRICE-1", 42, lambda: K.lower_median([5]), 5),
    _e("lower_median {1,2}", "PRICE-1", 43, lambda: K.lower_median([1, 2]), 1),
    _e("lower_median {3,1,2}", "PRICE-1", 44, lambda: K.lower_median([3, 1, 2]), 2),
    _e("lower_median {4,1,3,2}", "PRICE-1", 45, lambda: K.lower_median([4, 1, 3, 2]), 2),
    _e("lower_median {7,7,7,1}", "PRICE-1", 46, lambda: K.lower_median([7, 7, 7, 1]), 7),
    _e("lower_median {PRICE_MAX,PRICE_MIN}", "PRICE-1", 47, lambda: K.lower_median([PRICE_MAX, PRICE_MIN]),
       PRICE_MIN),
    _e("lower_median 96 alternating", "PRICE-1", 51,
       lambda: K.lower_median([51_000 if i % 2 else 49_000 for i in range(96)]), 49_000),
    # SIGMA-1 isqrt (math_tests:55-73)
    _e("isqrt small", "SIGMA-1", 57, lambda: [K.isqrt(x) for x in (0, 1, 2, 3, 4, 15, 16)], [0, 1, 1, 1, 2, 3, 4]),
    _e("isqrt 1e12", "SIGMA-1", 64, lambda: (K.isqrt(10**12), K.isqrt(10**12 - 1)), (10**6, 999_999)),
    _e("isqrt K13/V17 1e4*420480", "SIGMA-1", 67, lambda: K.isqrt(10_000 * 420_480), 64_844),
    _e("isqrt 2^256-1", "SIGMA-1", 71, lambda: K.isqrt(2**256 - 1), 2**128 - 1),
    # SIGMA-1 worked example at the v2 reference (math_tests:81-110)
    _e("sigma flat 43", "SIGMA-1", 87, lambda: _sigma(_series(43, 50_000)), 10_000),
    _e("sigma one 10% move", "SIGMA-1", 91, lambda: _sigma(_with(_series(43, 50_000), 0, 55_000)), 14_441),
    _e("sigma same move other side", "SIGMA-1", 95, lambda: _sigma(_with(_series(43, 55_000), 0, 50_000)), 13_127),
    _e("sigma clamp at cap", "SIGMA-1", 99, lambda: _sigma(_wild()), SIGMA_MAX_V2),
    _e("sigma floored at 1x", "SIGMA-1", 101,
       lambda: _sigma(_with(_series(43, 50_000), 0, 55_000), 1_000_000), 10_000),
    _e("sigma ref 0 fixes 1x", "SIGMA-1", 103, lambda: (_sigma(_wild(), 0), _sigma([], 0)), (10_000, 10_000)),
    _e("IT-4 live set: sigma 1x, MINT-5 ratio = the class base", "SIGMA-1", 108,
       lambda: [K.min_ratio_bps(b, K.sigma_mult_bps(_wild(), 0, PPY, 10_000)) for b in BASE_RATIO], list(BASE_RATIO)),
    # SIGMA-1 V17 regression (math_tests:117-137)
    _e("sigma cross-pool noise is 1x on P_fast, cap on raw", "SIGMA-1", 133, _cross_pool, (43, 10_000, SIGMA_MAX_V2)),
    # SIGMA-1 first sample at start height (math_tests:143-159)
    _e("sigma oldest sample undefined", "SIGMA-1", 151, lambda: _sigma(_with(_series(43, 50_000), 42, None)),
       SIGMA_MAX_V2),
    _e("sigma middle sample undefined", "SIGMA-1", 155, lambda: _sigma(_with(_series(43, 50_000), 20, None)),
       SIGMA_MAX_V2),
    _e("sigma fewer than two samples", "SIGMA-1", 157, lambda: (_sigma(_series(1, 50_000)), _sigma([])),
       (SIGMA_MAX_V2, SIGMA_MAX_V2)),
    # MINT-5 (math_tests:162-253)
    _e("min_ratio", "MINT-5", 164,
       lambda: [K.min_ratio_bps(*a) for a in ((50_000, 10_000), (50_000, 30_000), (30_000, 14_441), (0, 10_000))],
       [50_000, 150_000, 43_323, 0]),
    _e("required $100 @300% $0.05", "MINT-5", 172, lambda: K.required_zat(10_000, 30_000, 50_000), 600_000_000_000),
    _e("required ceiling $1 @300% $0.07", "MINT-5", 176, lambda: K.required_zat(100, 30_000, 70_000), 4_285_714_286),
    _e("required rounded 1000", "MINT-5", 180,
       lambda: (K.required_zat_rounded(100, 30_000, 70_000), K.required_zat_rounded(10_000, 30_000, 50_000)),
       (4_285_715_000, 600_000_000_000)),
    _e("required rounded granularity 1", "MINT-5", 186, lambda: K.required_zat_rounded(100, 30_000, 70_000, 1),
       4_285_714_286),
    _e("required undefined inputs", "MINT-5", 188,
       lambda: (K.required_zat(0, 30_000, 50_000), K.required_zat(10_000, 0, 50_000),
                K.required_zat(10_000, 30_000, 0)), (None, None, None)),
    _e("K14 worst ratio (v2) / live worst (class C)", "MINT-5", 206,
       lambda: (K.min_ratio_bps(50_000, SIGMA_MAX_V2), K.min_ratio_bps(BASE_RATIO[2], 10_000)), (150_000, 50_000)),
    _e("K14 $100,000 at PRICE_MIN unsatisfiable", "MINT-5", 208,
       lambda: (K.required_zat(MAX_MINT, 150_000, PRICE_MIN), K.required_zat_rounded(MAX_MINT, 150_000, PRICE_MIN),
                K.required_zat(MAX_MINT, BASE_RATIO[0], PRICE_MIN)), (None, None, None)),
    _e("K14 satisfiability thresholds 300/400/500 %", "MINT-5", 212,
       lambda: [K.required_zat(MAX_MINT, r, p) is not None
                for r, p in ((30_000, 14_286), (30_000, 14_285), (40_000, 19_048), (40_000, 19_047),
                             (50_000, 23_810), (50_000, 23_809))],
       [True, False, True, False, True, False]),
    _e("K14 worst mint at $0.05 / $0.50", "MINT-5", 219,
       lambda: (K.required_zat(MAX_MINT, 150_000, 50_000), K.required_zat(MAX_MINT, 150_000, 500_000)),
       (None, 300_000_000_000_000)),
    _e("required exactly MAX_MONEY / one over at $100", "MINT-5", 225,
       lambda: (K.required_zat(210_000_000_000, 10_000, PRICE_MAX_V2), K.required_zat(210_000_000_001, 10_000, PRICE_MAX_V2)),
       (MAX_MONEY, None)),
    _e("required maxOutput at $100", "MINT-5", 230, lambda: K.required_zat(MAX_OUTPUT, 30_000, PRICE_MAX_V2),
       3000 * COIN),
    _e("required MAX_MONEY boundary at PRICE_MAX (D-Q-22)", "MINT-5", 242,
       lambda: (K.required_zat(9_019_431_319_500, 10_000, PRICE_MAX), K.required_zat(9_019_431_319_501, 10_000, PRICE_MAX)),
       (MAX_MONEY, None)),
    _e("required at PRICE_MAX", "MINT-5", 245,
       lambda: (K.required_zat(MAX_OUTPUT, 30_000, PRICE_MAX), K.required_zat(1_000_000, K.min_ratio_bps(50_000, SIGMA_MAX_V2), PRICE_MAX),
                K.required_zat(1, 1, PRICE_MAX), K.required_zat_rounded(MAX_OUTPUT, 30_000, PRICE_MAX),
                K.required_zat(MIN_MINT, 30_000, PRICE_MAX)),
       (6_984_919_312, 3_492_459_656, 1, 6_984_920_000, 6_984_920)),
    # MINT-6 (math_tests:256-275)
    _e("cap_cents 1e6 YEC @ $0.05", "MINT-6", 260, lambda: K.cap_cents(1_000_000 * COIN, 50_000), 5_000_000),
    _e("supply_cap 15%", "MINT-6", 262, lambda: K.supply_cap_cents(1_000_000 * COIN, 50_000, 1_500), 750_000),
    _e("cap floor", "MINT-6", 264, lambda: (K.cap_cents(3, 50_000), K.supply_cap_cents(1_000_000 * COIN, 50_000, 1)),
       (0, 500)),
    _e("no cap / undefined price", "MINT-6", 267,
       lambda: (K.supply_cap_cents(1_000_000 * COIN, 50_000, 0), K.cap_cents(1_000_000 * COIN, None),
                K.supply_cap_cents(1_000_000 * COIN, None, 1_500)), (None, None, None)),
    _e("cap full issuance at $100", "MINT-6", 271, lambda: K.cap_cents(MAX_MONEY, PRICE_MAX_V2), 210_000_000_000),
    _e("cap full issuance at PRICE_MAX", "MINT-6", 273,
       lambda: (K.cap_cents(MAX_MONEY, PRICE_MAX), K.supply_cap_cents(MAX_MONEY, PRICE_MAX, 1_500)),
       (9_019_431_319_500, 1_352_914_697_925)),
    # HALT-2 (math_tests:278-292)
    _e("global ratio 300%", "HALT-2", 281, lambda: K.global_ratio_bps(600_000_000_000, 50_000, 10_000), 30_000),
    _e("global ratio 250% / one cent more", "HALT-2", 283,
       lambda: (K.global_ratio_bps(500_000_000_000, 50_000, 10_000), K.global_ratio_bps(500_000_000_000, 50_000, 10_001)),
       (25_000, 24_997)),
    _e("global ratio no supply / undefined", "HALT-2", 286,
       lambda: (K.global_ratio_bps(500_000_000_000, 50_000, 0), K.global_ratio_bps(500_000_000_000, None, 10_000)),
       (None, None)),
    _e("global ratio at the top", "HALT-2", 289,
       lambda: (K.global_ratio_bps(MAX_MONEY, PRICE_MAX_V2, 1), K.global_ratio_bps(MAX_MONEY, PRICE_MAX, 1)),
       (2_100_000_000_000_000, 90_194_313_195_000_000)),
    # RED-4 (math_tests:295-315), the v2 worked example at 110 %
    _e("underwater at 18,333 not 18,334", "RED-4", 299,
       lambda: (K.is_underwater(600_000_000_000, 18_333, 10_000, 11_000),
                K.is_underwater(600_000_000_000, 18_334, 10_000, 11_000)), (True, False)),
    _e("underwater threshold price", "RED-4", 297, lambda: K.underwater_price(600_000_000_000, 10_000, 11_000),
       18_333),
    _e("underwater bounds", "RED-4", 301,
       lambda: (K.is_underwater(600_000_000_000, PRICE_MIN, 10_000, 11_000),
                K.is_underwater(600_000_000_000, PRICE_MAX, 10_000, 11_000)), (True, False)),
    _e("underwater undefined pClaim", "RED-4", 304, lambda: K.is_underwater(600_000_000_000, None, 10_000, 11_000),
       False),
    _e("underwater zero collateral / zero debt", "RED-4", 306,
       lambda: (K.is_underwater(0, 50_000, 1, 11_000), K.is_underwater(0, 50_000, 0, 11_000)), (True, False)),
    _e("underwater big numbers", "RED-4", 309, lambda: K.is_underwater(MAX_MONEY, PRICE_MAX, 1_000_000, 11_000),
       False),
    _e("underwater one zat at PRICE_MAX (D-Q-22)", "RED-4", 312,
       lambda: [K.is_underwater(c, PRICE_MAX, 1, 11_000) for c in (1, 256, 257)], [True, True, False]),
    # FEE-1' (math_tests:320-379)
    _e("protocol fee floor at minMint and below", "FEE-1'", 329,
       lambda: [K.protocol_fee_units(d) for d in (MIN_MINT, 1, 0, -1, 10_001)], [1_000_000, 1_000_000, 0, 0, 1_000_100]),
    _e("protocol fee band edges", "FEE-1'", 335,
       lambda: [K.protocol_fee_units(d) for d in (100_000, 100_001, 1_000_000, 1_000_001, 10_000_000)],
       [10_000_000, 10_000_050, 55_000_000, 55_000_010, 145_000_000]),
    _e("protocol fee in cents (ceil)", "FEE-1'", 340,
       lambda: [K.protocol_fee_cents(d) for d in (10_000, 100_001, 1_000_000, 10_000_000)], [100, 1_001, 5_500, 14_500]),
    _e("protocol fee table: a negative rate / a bounded last band", "FEE-1'", 351,
       lambda: (K.protocol_fee_units(1_000_000, ((100_000, 100), (1_000_000, -5), (0, 10)), FEE_FLOOR),
                K.protocol_fee_units(5_000_000, ((100_000, 100), (1_000_000, -5), (2_000_000, 10)), FEE_FLOOR)
                == K.protocol_fee_units(2_000_000, ((100_000, 100), (1_000_000, -5), (2_000_000, 10)), FEE_FLOOR)),
       (10_000_000, True)),
    _e("protocol fee zat at the mint price", "FEE-1'", 361,
       lambda: [K.protocol_fee_zat(d, p) for d, p in ((10_000, PRICE_MIN), (10_000, 18_000), (10_000, PRICE_MAX),
                                                      (100_000, 18_000), (1_000_000, 18_000), (10_000_000, PRICE_MIN),
                                                      (10_000_000, 18_000), (10_000_000, PRICE_MAX))],
       [1_000_000_000_000, 5_555_555_556, 23_284, 55_555_555_556, 305_555_555_556, 145_000_000_000_000,
        805_555_555_556, 3_376_045]),
    _e("protocol fee zat undefined price / no debt", "FEE-1'", 372,
       lambda: (K.protocol_fee_zat(10_000, 0), K.protocol_fee_zat(10_000, -1), K.protocol_fee_zat(0, 18_000)),
       (None, None, 0)),
    _e("protocol fee zat saturates at MAX_MONEY", "FEE-1'", 378,
       lambda: K.protocol_fee_zat(1, PRICE_MIN, FEE_BANDS, 300_000_000_000), MAX_MONEY),
    # IT-9' (math_tests:383-408)
    _e("early redeem fee of the debt", "IT-9'", 386,
       lambda: [K.early_redeem_fee_zat(*a) for a in ((10_000, 500, 50_000), (10_000_000, 500, 18_000),
                                                     (10_000_000, 500, PRICE_MAX), (10_000_000, 500, PRICE_MIN))],
       [10_000_000_000, 27_777_777_777_778, 116_415_322, MAX_MONEY]),
    _e("early redeem fee degenerate", "IT-9'", 390,
       lambda: [K.early_redeem_fee_zat(*a) for a in ((10_000, 0, 50_000), (0, 500, 50_000), (10_000, 500, 0),
                                                     (1, 1, PRICE_MAX))], [0, 0, 0, 1]),
    _e("K14' protocol + early <= collateral / 50 + 2", "IT-9'", 402,
       lambda: all(K.protocol_fee_zat(d, p) + K.early_redeem_fee_zat(d, EARLY_BPS[c], p)
                   <= K.required_zat(d, BASE_RATIO[c], p) // 50 + 2
                   for p in (PRICE_MIN, 14_286, 18_000, 50_000, 1_000_000, PRICE_MAX)
                   for d in (MIN_MINT, 100_000, 1_000_000, MAX_MINT) for c in range(3)
                   if K.required_zat(d, BASE_RATIO[c], p) is not None), True),
    # MINT-2 classes (math_tests:430-467)
    _e("class for lock blocks (mainnet, D-IT-9/10)", "MINT-2", 436,
       lambda: [K.class_for_lock_blocks(d, CLASS_MIN, CLASS_MAX)
                for d in (34_559, 34_560, 103_680, 103_681, 207_360, 207_361, 420_480, 420_481, 2_102_400, 0, -5)],
       [-1, 0, 0, 1, 1, 2, 2, -1, -1, -1, -1]),
    _e("class for lock blocks (regtest)", "MINT-2", 461,
       lambda: [K.class_for_lock_blocks(d, REGTEST_CLASS_MIN, REGTEST_CLASS_MAX)
                for d in (48, 96, 97, 144, 145, 240, 241)], [0, 0, 1, 1, 2, 2, -1]),
    _e("class ranges: an empty class never matches (H-5)", "MINT-2", 482,
       lambda: (K.class_for_lock_blocks(100, (48, 97, 97), (96, 96, 240)),
                K.class_for_lock_blocks(97, (48, 97, 97), (96, 96, 96)), K.class_for_lock_blocks(96, (48, 97, 97), (96, 96, 96))),
       (2, -1, 0)),
    # HALT-2 / IT-5 recap gate (math_tests:506-521; MINT-4 state.cpp:366-378)
    _e("MINT-4: the global-ratio halt stops classes A and B only", "HALT-2", 516,
       lambda: [K.mint4_halt_verdict(K.HALT_GLOBAL_RATIO, b, 50_000) for b in BASE_RATIO],
       ["mint-halted-global-ratio", "mint-halted-global-ratio", None]),
    # D-Q-30 bond minimum (math_tests:727-765)
    _e("bond minimum from collateral (mainnet)", "D-Q-30", 731,
       lambda: [S.bond_min_from_collateral_zat(c) for c in (0, -1, 900_000 * COIN, 1_800_000 * COIN, 1_800_000 * COIN + 1,
                                                            2_700_000 * COIN, 3_600_000 * COIN - 1, 3_600_000 * COIN - 180,
                                                            3_600_000 * COIN, MAX_MONEY, (1 << 63) - 1)],
       [10_000 * COIN, 10_000 * COIN, 10_000 * COIN, 10_000 * COIN, 10_000 * COIN + 1, 15_000 * COIN, 20_000 * COIN,
        20_000 * COIN - 1, 20_000 * COIN, 20_000 * COIN, 20_000 * COIN]),
    _e("bond minimum from collateral (regtest)", "D-Q-30", 744,
       lambda: [S.bond_min_from_collateral_zat(c * COIN, S.REGTEST) for c in (0, 500, 700, 1_000, 1_300)],
       [5 * COIN, 5 * COIN, 7 * COIN, 10 * COIN, 10 * COIN]),
    # BUNDLE-1 bond weight (math_tests:769-790)
    _e("bond weight zero / negative age", "BUNDLE-1", 772,
       lambda: (K.bond_weight(BOND_MIN_CAP, 0, AGE_CAP), K.bond_weight(BOND_MIN_CAP, -5, AGE_CAP)), (0, 0)),
    _e("bond weight linear", "BUNDLE-1", 774,
       lambda: (K.bond_weight(BOND_MIN_CAP, 1, AGE_CAP), K.bond_weight(BOND_MIN_CAP, 100, AGE_CAP)),
       (BOND_MIN_CAP, BOND_MIN_CAP * 100)),
    _e("bond weight clamp", "BUNDLE-1", 776,
       lambda: (K.bond_weight(BOND_MIN_CAP, AGE_CAP + 1, AGE_CAP), K.bond_weight(BOND_MIN_CAP, 10**9, AGE_CAP)),
       (BOND_MIN_CAP * AGE_CAP, BOND_MIN_CAP * AGE_CAP)),
    _e("bond weight R10 4.1e17", "BUNDLE-1", 780, lambda: K.bond_weight(BOND_MIN_CAP, AGE_CAP, AGE_CAP),
       414_720_000_000_000_000),
    _e("bond weight MAX_MONEY, 9 slots past int64", "BUNDLE-1", 781,
       lambda: (K.bond_weight(MAX_MONEY, AGE_CAP, AGE_CAP) == MAX_MONEY * AGE_CAP,
                9 * K.bond_weight(MAX_MONEY, AGE_CAP, AGE_CAP) > 2**63 - 1), (True, True)),
    _e("bond weight degenerate", "BUNDLE-1", 784,
       lambda: (K.bond_weight(0, 10, AGE_CAP), K.bond_weight(-1, 10, AGE_CAP), K.bond_weight(BOND_MIN_CAP, 10, 0)),
       (0, 0, 0)),
    _e("bond weight regtest", "BUNDLE-1", 789, lambda: K.bond_weight(10 * COIN, 100, 64), 10 * COIN * 64),
    # seat ranking (math_tests:794-842; owner 2026-10-11)
    _e("Log2Q16", "SEATS", 796,
       lambda: [S.log2_q16(x) for x in (0, 1, 2, 1024, 3, 1 << 40, (1 << 64) - 1)],
       [0, 0, 65_536, 10 << 16, 103_872, 40 << 16, (63 << 16) | 0xFFFF]),
    _e("SeatAgeFactorQ16", "SEATS", 809,
       lambda: [S.seat_age_factor_q16(a) for a in (0, -10, 420_480, 3 * 420_480, 1_152, 8_064, 210_240)]
       + [S.seat_age_factor_q16(100, 0, 420_480), S.seat_age_factor_q16(100, 1_152, 0)],
       [65_536, 65_536, 124_518, 124_518, 72_462, 86_314, 117_619, 65_536, 65_536]),
    _e("SeatWeight", "SEATS", 821,
       lambda: (S.seat_weight(8_000 * COIN, 8_064) == 8_000 * COIN * 86_314,
                S.seat_weight(8_000 * COIN, 8_064) // 65_536,
                S.seat_weight(20_000 * COIN, 0) * 10_000 > S.seat_weight(8_000 * COIN, 420_480) * 10_500,
                S.seat_weight(3_000 * COIN, 0) * 10_000 > S.seat_weight(2_000 * COIN, 8_064) * 10_500,
                S.seat_weight(3_000 * COIN, 0) < S.seat_weight(2_000 * COIN, 420_480),
                S.seat_weight(0, 10), S.seat_weight(-1, 10)),
       (True, 1_053_637_695_312, True, True, True, 0, 0)),
    # PRICE-2 weighted quantile (math_tests:848-905)
    _e("wq single entry", "PRICE-2", 853,
       lambda: [K.weighted_quantile([(50_000, W)], q) for q in (Q_LOW, Q_HIGH, 0, 10_000)], [50_000] * 4),
    _e("wq empty / q over 1e4", "PRICE-2", 858,
       lambda: (K.weighted_quantile([], Q_LOW), K.weighted_quantile([(50_000, W)], 10_001)), (None, None)),
    _e("wq four equal", "PRICE-2", 862, lambda: (K.weighted_quantile(FOUR, Q_LOW), K.weighted_quantile(FOUR, Q_HIGH)),
       (20, 30)),
    _e("wq shuffled", "PRICE-2", 866,
       lambda: (K.weighted_quantile(SHUFFLED, Q_LOW), K.weighted_quantile(SHUFFLED, Q_HIGH)), (20, 30)),
    _e("wq exact thresholds (three)", "PRICE-2", 870,
       lambda: [K.weighted_quantile(THREE, q) for q in (3333, 3334, 6666, 6667, 10_000)], [10, 20, 20, 30, 30]),
    _e("wq first >= (two)", "PRICE-2", 877, lambda: (K.weighted_quantile(TWO, 5000), K.weighted_quantile(TWO, 5001)),
       (10, 20)),
    _e("wq heavy low", "PRICE-2", 881,
       lambda: (K.weighted_quantile(HEAVY_LOW, 7500), K.weighted_quantile(HEAVY_LOW, 7501)), (10, 20)),
    _e("wq whale", "PRICE-2", 885, lambda: (K.weighted_quantile(WHALE, Q_LOW), K.weighted_quantile(WHALE, Q_HIGH)),
       (20, 20)),
    _e("wq ties", "PRICE-2", 889, lambda: [K.weighted_quantile(TIED, q) for q in (Q_LOW, Q_HIGH, 2000, 8001)],
       [20, 20, 10, 30]),
    _e("wq zero weights / zero total", "PRICE-2", 895,
       lambda: (K.weighted_quantile(ZEROS, Q_LOW), K.weighted_quantile(ALL_ZERO, Q_LOW)), (20, 10)),
    _e("wq 256-bit weights", "PRICE-2", 901, lambda: (K.weighted_quantile(BIG, Q_LOW), K.weighted_quantile(BIG, Q_HIGH)),
       (20, 50)),
    _e("wq negative q", "PRICE-2", 904, lambda: K.weighted_quantile(FOUR, -1), 10),
    # RED-5 (math_tests:909-979), θ 125 % (D-IT-2)
    _e("claimant max worked example at 125 %", "RED-5", 916,
       lambda: (K.claimant_max_zat(10_000, CLAIM_THR, 18_333), K.claimant_max_zat(10_000, CLAIM_THR, 18_333) // COIN),
       (681_830_578_738, 6818)),
    _e("claimant max v2 worked example at 110 %", "RED-5", 919, lambda: K.claimant_max_zat(10_000, 11_000, 18_333),
       600_010_909_290),
    _e("claimant max clause (b)", "RED-5", 923, lambda: K.claimant_max_zat(10_000, 10_000, 18_333), 545_464_462_991),
    _e("at threshold: not yet underwater", "RED-4", 927,
       lambda: (K.is_underwater(AT_THRESHOLD, 20_000, 10_000, CLAIM_THR),
                K.is_underwater(AT_THRESHOLD - 1, 20_000, 10_000, CLAIM_THR)), (False, True)),
    _e("at threshold: claimant takes all", "RED-5", 931, lambda: K.claimant_max_zat(10_000, CLAIM_THR, 20_000),
       AT_THRESHOLD),
    _e("residual around threshold", "RED-5", 932,
       lambda: [K.residual_zat(AT_THRESHOLD + d, AT_THRESHOLD) for d in (0, -1, 1, RESIDUAL_MIN)],
       [0, 0, 1, RESIDUAL_MIN]),
    _e("residual worked vault at 18,333", "RED-5", 937,
       lambda: K.residual_zat(600_000_000_000, K.claimant_max_zat(10_000, CLAIM_THR, 18_333)), 0),
    _e("forced early (b) at 30,000", "RED-5", 942,
       lambda: (K.claimant_max_zat(10_000, 10_000, 30_000),
                K.residual_zat(600_000_000_000, K.claimant_max_zat(10_000, 10_000, 30_000))),
       (333_333_333_334, 266_666_666_666)),
    _e("claimant ceiling", "RED-5", 945, lambda: K.claimant_max_zat(100, 11_000, 70_000), 1_571_428_572),
    _e("claimant undefined inputs", "RED-5", 947,
       lambda: [K.claimant_max_zat(*a) for a in ((0, 11_000, 18_333), (10_000, 0, 18_333), (10_000, 11_000, 0),
                                                 (-1, 11_000, 18_333))], [None] * 4),
    _e("residual undefined claimant max", "RED-5", 952,
       lambda: (K.residual_zat(600_000_000_000, None), K.residual_zat(0, K.claimant_max_zat(10_000, 11_000, 18_333)),
                K.residual_zat(-5, K.claimant_max_zat(10_000, 11_000, 18_333))), (0, 0, 0)),
    _e("claimant overflow at PRICE_MIN", "RED-5", 962,
       lambda: (K.claimant_max_zat(MAX_MINT, CLAIM_THR, PRICE_MIN), K.claimant_max_zat(MAX_MINT, 10_000, PRICE_MIN),
                K.residual_zat(MAX_MONEY, K.claimant_max_zat(MAX_MINT, CLAIM_THR, PRICE_MIN))), (None, None, 0)),
    _e("claimant minMint at PRICE_MIN fits", "RED-5", 968, lambda: K.claimant_max_zat(MIN_MINT, CLAIM_THR, PRICE_MIN),
       125_000_000_000_000),
    _e("claimant exactly MAX_MONEY / one over at $100", "RED-5", 970,
       lambda: (K.claimant_max_zat(210_000_000_000, 10_000, PRICE_MAX_V2),
                K.claimant_max_zat(210_000_000_001, 10_000, PRICE_MAX_V2)), (MAX_MONEY, None)),
    _e("claimant maxOutput at $100", "RED-5", 973, lambda: K.claimant_max_zat(MAX_OUTPUT, CLAIM_THR, PRICE_MAX_V2),
       125_000_000_000),
    _e("claimant at PRICE_MAX (D-Q-22)", "RED-5", 975,
       lambda: (K.claimant_max_zat(9_019_431_319_500, 10_000, PRICE_MAX), K.claimant_max_zat(9_019_431_319_501, 10_000, PRICE_MAX),
                K.claimant_max_zat(MAX_OUTPUT, CLAIM_THR, PRICE_MAX)), (MAX_MONEY, None, 2_910_383_047)),
    # RED-5' (math_tests:982-998)
    _e("residual threshold in dollars", "RED-5'", 987,
       lambda: [K.residual_min_zat(p, RESIDUAL_MIN, RESIDUAL_MIN_CENTS)
                for p in (18_000, 50_000, PRICE_MAX, 480_000_000, 479_999_999, 0, PRICE_MIN)],
       [2_666_666_667, 960_000_000, RESIDUAL_MIN, 100_000, 100_001, RESIDUAL_MIN, 480_000_000_000]),
    _e("residual threshold without cents", "RED-5'", 996, lambda: K.residual_min_zat(18_000, RESIDUAL_MIN, 0),
       RESIDUAL_MIN),
    # MINT-6b (math_tests:1001-1015)
    _e("max vault cents", "MINT-6b", 1006,
       lambda: [K.max_vault_cents(c, s, VAULT_SHARE_FLOOR)
                for c, s in ((None, MAX_VAULT_SHARE_BPS), (100_000_000, 0), (100_000_000, MAX_VAULT_SHARE_BPS),
                             (5_000_000, MAX_VAULT_SHARE_BPS), (10_000_000, MAX_VAULT_SHARE_BPS),
                             (10_000_010, MAX_VAULT_SHARE_BPS), (0, MAX_VAULT_SHARE_BPS),
                             (K.cap_cents(MAX_MONEY, PRICE_MAX), MAX_VAULT_SHARE_BPS))],
       [None, None, 10_000_000, 1_000_000, 1_000_000, 1_000_001, 1_000_000, 901_943_131_950]),
    # AFEE-1' (math_tests:1019-1042)
    _e("attest fee half of the $1 floor at $0.05", "AFEE-1'", 1024,
       lambda: (K.protocol_fee_zat(10_000, 50_000), K.attest_fee_zat(K.protocol_fee_zat(10_000, 50_000), ATTEST_FEE_BPS)),
       (20 * COIN, 10 * COIN)),
    _e("attest fee of $55 at $0.018", "AFEE-1'", 1027,
       lambda: K.attest_fee_zat(K.protocol_fee_zat(1_000_000, 18_000), ATTEST_FEE_BPS), 152_777_777_778),
    _e("attest fee floor", "AFEE-1'", 1029, lambda: [K.attest_fee_zat(f, ATTEST_FEE_BPS) for f in (1, 3, 7)], [0, 1, 3]),
    _e("attest fee regtest quarter", "AFEE-1'", 1033,
       lambda: (K.attest_fee_zat(15 * COIN, REGTEST_ATTEST_FEE_BPS), K.attest_fee_zat(7, REGTEST_ATTEST_FEE_BPS)),
       (375_000_000, 1)),
    _e("attest fee degenerate", "AFEE-1'", 1036,
       lambda: (K.attest_fee_zat(0, ATTEST_FEE_BPS), K.attest_fee_zat(-1, ATTEST_FEE_BPS), K.attest_fee_zat(20 * COIN, 0),
                K.attest_fee_zat(20 * COIN, 10_000)), (0, 0, 0, 20 * COIN)),
    _e("attest fee at MAX_MONEY", "AFEE-1'", 1040,
       lambda: (K.attest_fee_zat(MAX_MONEY, ATTEST_FEE_BPS), K.attest_fee_zat(MAX_MONEY, 10_000)),
       (MAX_MONEY // 2, MAX_MONEY)),
    # PRICE-2 combine (math_tests:1046-1093)
    _e("combine conservative side", "PRICE-2", 1049,
       lambda: (tuple(K.price_combine(50_000, 52_000, 48_000, 55_000)), tuple(K.price_combine(48_000, 55_000, 50_000, 52_000)),
                tuple(K.price_combine(50_000, 50_000, 50_000, 50_000))),
       ((48_000, 55_000, 52_000), (48_000, 55_000, 52_000), (50_000, 50_000, 50_000))),
    _e("combine undefined inputs", "PRICE-2", 1063,
       lambda: [tuple(K.price_combine(*a)) for a in ((None, 52_000, 48_000, 55_000), (50_000, None, 48_000, 55_000),
                                                     (50_000, 52_000, None, 55_000), (50_000, 52_000, 48_000, None),
                                                     (None, None, None, None))],
       [(None, 55_000, 52_000), (48_000, None, None), (None, 55_000, 52_000), (48_000, None, None),
        (None, None, None)]),
    _e("combine bounds", "PRICE-2", 1085, lambda: tuple(K.price_combine(PRICE_MIN, PRICE_MAX, PRICE_MAX, PRICE_MIN)),
       (PRICE_MIN, PRICE_MAX, PRICE_MIN)),
    _e("sigma at the PRICE_MAX extremes", "SIGMA-1", 1091,
       lambda: (K.sigma_mult_bps([PRICE_MAX, PRICE_MIN, PRICE_MAX], 10_000, 1_152, 30_000),
                K.sigma_mult_bps([PRICE_MAX, PRICE_MAX], 10_000, 1_152, 30_000)), (30_000, 10_000)),
)


# ---------------------------------------------------------------------------------------------------
# the spec-rational cases (audit 03/T-3; math_tests:1103-1150 math_spec_rational_cases)

#: case fn → the kernel call, with MainParams() where the C++ passes ``P`` (the fee table, RED-5')
_CASE_FNS: dict[str, Callable[..., object]] = {
    "RequiredCollateral": lambda a: K.required_zat(a[0], a[1], a[2]),
    "ProtocolFeeZat": lambda a: K.protocol_fee_zat(a[0], a[1]),
    "ProtocolFeeCents": lambda a: K.protocol_fee_cents(a[0]),
    "EarlyRedeemFeeZat": lambda a: K.early_redeem_fee_zat(a[0], a[1], a[2]),
    "AttestFeeZat": lambda a: K.attest_fee_zat(a[0], a[1]),
    "ClaimantMaxZat": lambda a: K.claimant_max_zat(a[0], a[1], a[2]),
    "ResidualMinZat": lambda a: K.residual_min_zat(a[0], RESIDUAL_MIN, RESIDUAL_MIN_CENTS),
    "GlobalRatioBps": lambda a: K.global_ratio_bps(a[0], a[1], a[2]),
    "CapCents": lambda a: K.cap_cents(a[0], a[1]),
    "SupplyCapCents": lambda a: K.supply_cap_cents(a[0], a[1], a[2]),
    "IsUnderwater": lambda a: K.is_underwater(a[0], a[1], a[2], a[3]),
}


def load_math_cases() -> list[dict]:
    """The vendored ``yellowback_math_cases.json`` rows."""
    return json.loads((resources.files("ybcal.model") / "yellowback_math_cases.json").read_text())["cases"]


def run_math_cases() -> list[tuple[str, bool, str]]:
    """One row per function: every spec-rational case (exact rationals from the spec text, never from the
    model) recomputed by the kernels."""
    per: dict[str, list[object]] = {}
    count: dict[str, int] = {}
    for c in load_math_cases():
        fn = c["fn"]
        count[fn] = count.get(fn, 0) + 1
        if fn not in _CASE_FNS:
            per.setdefault(fn, []).append("no kernel")
            continue
        got = _CASE_FNS[fn](c["args"])
        if got != c["expect"]:
            per.setdefault(fn, []).append((c["args"], got, c["expect"]))
    return [(fn, fn not in per, f"{count[fn]} cases" + (f"; e.g. {per[fn][0]}" if fn in per else ""))
            for fn in count]


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

_SPECIAL = {"price_min": "PRICE_MIN", "price_max": "PRICE_MAX", "ref_lag": "DEFAULT_REF_LAG"}
#: model attributes with no registry row (yellowbackFee is the network fee floor, not a Params field; the
#: attestor set and the Falcon height are per-network configuration, not parameters)
_NOT_IN_REGISTRY = {"network", "yellowback_fee", "bundle_carrier", "attestor_set", "pq_falcon_height"}


def _camel(name: str) -> str:
    if name in _SPECIAL:
        return _SPECIAL[name]
    head, *rest = name.split("_")
    return head + "".join(w[:1].upper() + w[1:] for w in rest)


def reference_param_drift(params=None) -> dict[str, tuple[object, object]]:
    """``{registry key: (reference model value, registry value)}`` where the reference model's
    ``Params.mainnet`` column differs from the registry's mainnet set (default
    ``ybcal.params.paramset.mainnet()``). While the registry is still the 7702d22 (v3) set this lists every
    value the vault upgrade, in-term claims and the quantum fee plan changed (e.g. claimThresholdBps 12,500
    vs 11,000); a registry key the upgrade removed (signalWindow, feeMin, …) has no model attribute and
    is not listed."""
    if params is None:
        from ybcal.params.paramset import mainnet
        params = mainnet()
    m = ref.Params.mainnet(int(params["startHeight"]))
    out: dict[str, tuple[object, object]] = {}
    for attr, val in vars(m).items():
        if attr in _NOT_IN_REGISTRY:
            continue
        if isinstance(val, dict | tuple):
            continue                       # the fee bands and the attestation sub-table have no registry row
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

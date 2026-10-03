"""Quick randomized parity sample for ``ybcal verify`` (owner: WP-1).

The test suite runs the full hypothesis property tests (``tests/model``); hypothesis is a dev
dependency, so ``ybcal verify`` uses this seeded standard-library sample instead: vectorised ==
scalar kernels, and scalar kernels == reference model functions, on random inputs that include
the undefined and boundary values.
"""

from __future__ import annotations

import random

import numpy as np

from ybcal.model import kernels as K
from ybcal.model import reference as ref
from ybcal.model import reference_attest as ref_attest
from ybcal.model import vkernels as V

OWNER_WP = "WP-1"

PRICE_MIN, PRICE_MAX = 100, 100_000_000
Check = tuple[str, bool, str]


def _price(rng: random.Random) -> int | None:
    r = rng.random()
    if r < 0.1:
        return None
    if r < 0.15:
        return rng.choice((PRICE_MIN, PRICE_MAX))
    return rng.randint(PRICE_MIN, PRICE_MAX)


def _vec(xs) -> np.ndarray:
    return V.to_array(xs)


def _opt(v) -> int | None:
    v = int(v)
    return None if v < 0 else v


def quick_parity(n: int = 300, seed: int = 0) -> list[Check]:
    rng = random.Random(seed)
    out: list[Check] = []

    def record(name: str, mismatches: list) -> None:
        out.append((name, not mismatches, f"{n} samples" + (f"; e.g. {mismatches[0]}" if mismatches else "")))

    # money kernels: vectorised == scalar
    cents = [
        rng.choice((0, -1, 1, 100, 10_000, 1_000_000, 10_000_000, rng.randint(1, 10**7))) for _ in range(n)
    ]
    ratio = [rng.choice((0, 10_000, 30_000, 150_000, rng.randint(1, 200_000))) for _ in range(n)]
    coll = [rng.choice((0, -5, K.MAX_MONEY, rng.randint(0, K.MAX_MONEY))) for _ in range(n)]
    pr = [_price(rng) for _ in range(n)]
    sup = [rng.choice((0, 1, rng.randint(1, 10**9))) for _ in range(n)]
    thr = [rng.choice((0, 10_000, 10_500, 11_000, rng.randint(1, 20_000))) for _ in range(n)]
    for name, vf, sf, args in (
        ("vk required_zat", V.required_zat, K.required_zat, (cents, ratio, pr)),
        ("vk claimant_max_zat", V.claimant_max_zat, K.claimant_max_zat, (cents, thr, pr)),
        ("vk global_ratio_bps", V.global_ratio_bps, K.global_ratio_bps, (coll, pr, sup)),
        ("vk cap_cents", lambda a, p: V.cap_cents(a, p), K.cap_cents, (coll, pr)),
    ):
        got = vf(*[_vec(a) for a in args])
        mism = [(a, int(g)) for *a, g in zip(*args, got, strict=True) if _opt(g) != sf(*a)]
        record(name, mism)
    got = V.is_underwater(_vec(coll), _vec(pr), _vec(cents), _vec(thr))
    record(
        "vk is_underwater",
        [a for *a, g in zip(coll, pr, cents, thr, got, strict=True) if bool(g) != K.is_underwater(*a)],
    )
    got = V.fee_zat(_vec(coll), 50_000_000, 25)
    record(
        "vk fee_zat", [c for c, g in zip(coll, got, strict=True) if int(g) != K.fee_zat(c, 50_000_000, 25)]
    )
    fees = [rng.randint(-1, K.MAX_MONEY) for _ in range(n)]
    got = V.attest_fee_zat(_vec(fees), 2_500)
    record(
        "vk attest_fee_zat",
        [f for f, g in zip(fees, got, strict=True) if int(g) != K.attest_fee_zat(f, 2_500)],
    )
    # medians / HALT-3
    trip = [(_price(rng), _price(rng), _price(rng)) for _ in range(n)]
    f, m, s = (_vec(x) for x in zip(*trip, strict=True))
    pm, pc, h3 = V.price_mint(f, m, s), V.price_claim(m, s), V.halt3_divergence(f, m, s, 2_000)
    record(
        "vk pMint/pClaim/HALT-3",
        [
            t
            for t, a, b, c in zip(trip, pm, pc, h3, strict=True)
            if (_opt(a), _opt(b), bool(c))
            != (K.price_mint(*t), K.price_claim(t[1], t[2]), K.halt3_divergence(*t, 2_000))
        ],
    )
    # rolling median with gaps and fills
    length = 600
    series = [_price(rng) if rng.random() < 0.7 else None for _ in range(length)]
    arr = _vec(series)
    mism = []
    for w, fill in ((8, 4), (24, 16), (96, 48), (64, 43), (700, 1)):
        med = V.rolling_lower_median(arr, w, fill)
        for i in range(length):
            want = K.window_median_with_fill([x for x in series[max(0, i - w + 1) : i + 1] if x], fill)
            if _opt(med[i]) != want:
                mism.append((w, fill, i, int(med[i]), want))
    out.append(
        (
            "vk rolling lower median (5 windows)",
            not mism,
            f"{5 * length} blocks" + (f"; {mism[0]}" if mism else ""),
        )
    )
    # sigma series vs scalar over the same series
    pf = [x if rng.random() < 0.98 else None for x in (rng.randint(40_000, 60_000) for _ in range(length))]
    vs = V.sigma_mult_series(_vec(pf), 64, 8, 10_000, 8_760, 30_000)
    mism = [
        i
        for i in range(length)
        if int(vs[i]) != K.sigma_mult_bps(K.sigma_samples(pf, i, 64, 8), 10_000, 8_760, 30_000)
    ]
    record("vk sigma series", mism)
    # scalar == reference where both exist (reference domain)
    mism = []
    for _ in range(n):
        samples = [rng.randint(PRICE_MIN, PRICE_MAX) for _ in range(rng.randint(2, 44))]
        a = (samples, rng.randint(1, 50_000), 8_760, rng.randint(10_000, 50_000))
        if K.sigma_mult_bps(*a) != ref.sigma_mult_bps(*a):
            mism.append(a)
        c, r_, p = rng.randint(1, 10**7), rng.randint(1, 200_000), rng.randint(PRICE_MIN, PRICE_MAX)
        if K.required_zat(c, r_, p) != ref.required_zat(c, r_, p):
            mism.append(("required", c, r_, p))
        entries = [(rng.randint(1, 1000), rng.randint(1, 10**18)) for _ in range(rng.randint(1, 8))]
        q = rng.randint(0, 10_000)
        if K.weighted_quantile(entries, q) != ref_attest.weighted_quantile(entries, q):
            mism.append(("wq", entries, q))
    record("kernels == reference (sigma, required, weighted quantile)", mism)
    return out

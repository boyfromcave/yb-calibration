"""numpy vectorised kernels, exactly equal to :mod:`ybcal.model.kernels` (owner: WP-1; PLAN §2.1).

These are the kernels hot in Monte Carlo. Every one is property-tested equal, element for element,
to the scalar kernel (``tests/model/test_vkernels.py``), including the undefined and min-fill
boundaries.

Conventions
-----------
* Arrays are ``int64``. **Undefined is ``-1``** (``UNDEF``) for prices and amounts; any value
  ``<= 0`` passed as a price is read as undefined, exactly like the C++ (``pMint <= 0`` →
  nullopt). Predicates return ``bool`` arrays (``False`` where undefined).
* Inputs broadcast (numpy rules); a leading "paths" axis is allowed everywhere. Series kernels
  (rolling medians, σ) work along the **last** axis (blocks); element 0 of the axis is the block at
  ``startHeight`` (nothing before it exists, so a window is truncated there, as the node does).

Rolling lower median with min-fill (PRICE-1)
--------------------------------------------
:func:`rolling_lower_median` answers, for every block ``i``, the lower median of the quotes present
in ``(i − W, i]`` (undefined when fewer than ``fill``) with a **wavelet matrix** over the
compressed quote sequence: a range-k-th-smallest query costs O(log σ) numpy operations *for all
blocks at once*, so the cost is O(n log n) for the whole series, independent of ``W``, and the
same structure answers the three windows (:class:`RollingMedian`). Missing tags simply are not in
the sequence; each block's window maps to an index range through a cumulative count. Paths are
concatenated into one sequence (ranges never cross a path), batched to bound memory.
Measured (this sandbox, one core): see ``docs/architecture.md`` "Model and kernels (WP-1)".

Overflow analysis (int64, |x| ≤ 9.22e18)
---------------------------------------
Bounds: price ≤ PRICE_MAX = 1e8 µUSD, collateral ≤ MAX_MONEY = 2.1e15 zat, cents ≤ 1e7 (maxOutput),
ratio ≤ 1.5e5 bps (class A at the 3× cap), COIN = 1e8.

* ``required_zat`` / ``claimant_max_zat``: ``cents·ratio·COIN`` reaches 1.5e19 (K14) — **overflows**.
  Exact without overflow: ``x = cents·ratio`` (≤ 1.5e12) is split as ``x = q·p + r`` so
  ``x·COIN/p = q·COIN + r·COIN/p`` with ``r·COIN < p·1e8 ≤ 1e16``; if ``q > MAX_MONEY // COIN`` the
  result is already over MAX_MONEY (undefined) and ``q·COIN`` is never formed.
* ``is_underwater``: ``collateral·p`` (2.1e23) and ``minted·thr·COIN`` (1.1e19) both overflow. Used
  instead: for integer ``c``, ``c·p < D`` ⇔ ``c < ceil(D / p)``, with the ceiling computed by the
  same split; a ceiling past MAX_MONEY means underwater for every in-range collateral.
* ``global_ratio_bps`` / ``cap_cents``: ``⌊a·p / (COIN·s)⌋ = ⌊⌊a·p / COIN⌋ / s⌋`` and
  ``a = q·COIN + r`` gives ``⌊a·p/COIN⌋ = q·p + ⌊r·p/COIN⌋`` (q ≤ 2.1e7, so q·p ≤ 2.1e15).
* ``fee_zat`` / ``attest_fee_zat``: ``collateral·bps`` reaches 2.1e19 at 10^4 bps; split by 10^4.
* σ: ``r_k = |a−b|·10^4 // b`` ≤ 1e12 fits, but ``Σr_k²·periodsPerYear`` can overflow for absurd
  moves (> ~350× per volStep at mainnet scale). Rows whose window holds a return above
  ``R_SAFE`` (``n·R_SAFE²·ppy < 2^62``) are recomputed with the exact scalar kernel (Python ints);
  everything else uses int64 and an exactly corrected float isqrt.
* HALT-3 / PIN products (``p·10^4``) fit for any price < 9.2e14 µUSD (the C++ uses int64 too).

Where a guard cannot prove the int64 path exact (inputs outside these bounds), the element falls
back to the scalar kernel. Correctness over speed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ybcal.model import kernels as K

OWNER_WP = "WP-1"

UNDEF: int = -1
BPS: int = K.BPS
COIN: int = K.COIN
MAX_MONEY: int = K.MAX_MONEY
_I64 = np.int64


def _a(x) -> np.ndarray:
    return np.asarray(x, dtype=_I64)


def to_array(values, undefined: int = UNDEF) -> np.ndarray:
    """A sequence with ``None`` for undefined → ``int64`` array with ``undefined``."""
    return np.array([undefined if v is None else v for v in values], dtype=_I64)


def to_optional(arr) -> list[int | None]:
    """The inverse of :func:`to_array` for a 1-D array (values ≤ 0 → ``None``)."""
    return [int(v) if v > 0 else None for v in np.asarray(arr).ravel()]


# ---------------------------------------------------------------------------------------------------
# Wavelet matrix: range k-th smallest, vectorised over queries


class WaveletMatrix:
    """Static wavelet matrix over non-negative integer symbols ``< 2**bits``.

    ``kth(lo, hi, k)`` returns the k-th smallest (0-based) symbol of ``seq[lo:hi]`` for arrays of
    queries at once. Construction O(n·bits), each query level O(1) numpy work per query.
    """

    def __init__(self, seq: np.ndarray, bits: int):
        n = int(seq.shape[0])
        self.n, self.bits = n, max(bits, 1)
        # int32 indices/symbols while they fit (halves memory traffic); int64 otherwise
        self.idx_t = np.int32 if n < 2**31 - 1 else _I64
        sym_t = np.int32 if self.bits <= 31 else _I64
        self.rank0: list[np.ndarray] = []
        self.zeros: list[int] = []
        cur = seq.astype(sym_t, copy=True)
        for level in range(self.bits):
            b = self.bits - 1 - level
            one = ((cur >> b) & 1).astype(bool)
            zero = ~one
            r0 = np.empty(n + 1, dtype=self.idx_t)
            r0[0] = 0
            np.cumsum(zero, out=r0[1:])
            self.rank0.append(r0)
            self.zeros.append(int(r0[-1]))
            cur = np.concatenate((cur[zero], cur[one]))

    def kth(self, lo: np.ndarray, hi: np.ndarray, k: np.ndarray) -> np.ndarray:
        t = self.idx_t
        lo, hi, k = lo.astype(t), hi.astype(t), k.astype(t)
        val = np.zeros(lo.shape, dtype=_I64)
        for level in range(self.bits):
            b = self.bits - 1 - level
            r0 = self.rank0[level]
            z_lo = r0[lo]
            z_hi = r0[hi]
            nz = z_hi - z_lo
            go1 = k >= nz
            np.subtract(k, nz, out=k, where=go1)
            zl = t(self.zeros[level])
            lo = np.where(go1, lo - z_lo + zl, z_lo)
            hi = np.where(go1, hi - z_hi + zl, z_hi)
            val[go1] |= 1 << b
        return val


@dataclass
class _Batch:
    rows: np.ndarray        # path indices in this batch
    wm: WaveletMatrix | None
    values: np.ndarray      # distinct sorted values (rank → value)
    csum: np.ndarray        # (rows, n+1) cumulative valid count, offset to global sequence index


class RollingMedian:
    """Rolling lower medians of one or many quote series, sharing one wavelet matrix per batch.

    ``prices``: int array ``(..., n)``; a block without a quote tag is ``<= 0`` (or masked out
    with ``valid``). Then :meth:`median` answers PRICE-1 for any ``(window, fill)``.
    """

    def __init__(self, prices, valid=None, *, batch_elems: int = 1_000_000):
        p = _a(prices)
        self.shape = p.shape
        self.n = p.shape[-1]
        p2 = p.reshape(-1, self.n)
        v2 = p2 > 0 if valid is None else (np.asarray(valid, dtype=bool).reshape(-1, self.n) & (p2 > 0))
        self._batches: list[_Batch] = []
        per = max(1, batch_elems // max(self.n, 1))
        for start in range(0, p2.shape[0], per):
            rows = np.arange(start, min(start + per, p2.shape[0]))
            pv, vv = p2[rows], v2[rows]
            seq = pv[vv]                                   # row-major: path by path
            cnt = np.zeros((len(rows), self.n + 1), dtype=_I64)
            np.cumsum(vv, axis=1, out=cnt[:, 1:])
            offs = np.concatenate(([0], np.cumsum(cnt[:, -1])[:-1]))
            cnt += offs[:, None]
            if seq.size:
                values, ranks = np.unique(seq, return_inverse=True)
                wm = WaveletMatrix(ranks.astype(_I64), int(len(values) - 1).bit_length())
            else:
                values, wm = np.zeros(0, dtype=_I64), None
            self._batches.append(_Batch(rows, wm, values, cnt))

    def median(self, window: int, fill: int) -> np.ndarray:
        """PRICE-1 ``median(W, H)`` at every block: ``UNDEF`` where fewer than ``fill`` quotes."""
        out = np.full((sum(len(b.rows) for b in self._batches), self.n), UNDEF, dtype=_I64)
        idx = np.arange(self.n)
        lo_idx = np.maximum(idx - window + 1, 0)
        for b in self._batches:
            hi = b.csum[:, idx + 1]
            lo = b.csum[:, lo_idx]
            count = hi - lo
            ok = (count >= max(fill, 1)) & (count > 0)
            if b.wm is None or not ok.any():
                continue
            k = (count - 1) // 2
            r = b.wm.kth(lo[ok], hi[ok], k[ok])
            res = out[b.rows]
            res[ok] = b.values[r]
            out[b.rows] = res
        return out.reshape(self.shape)


def rolling_lower_median(prices, window: int, fill: int, valid=None) -> np.ndarray:
    """PRICE-1 over a block series (``kernels.window_median_with_fill`` at every block)."""
    return RollingMedian(prices, valid).median(window, fill)


def price_medians(prices, windows: tuple[int, int, int], fills: tuple[int, int, int], valid=None):
    """``(pFast, pMid, pSlow)`` series from one quote series (shares one wavelet matrix)."""
    rm = RollingMedian(prices, valid)
    return tuple(rm.median(w, f) for w, f in zip(windows, fills, strict=True))


# ---------------------------------------------------------------------------------------------------
# PRICE-2 / HALT-3 / PRICE-2 combine


def price_mint(p_fast, p_mid, p_slow) -> np.ndarray:
    """``kernels.price_mint`` elementwise."""
    f, m, s = np.broadcast_arrays(_a(p_fast), _a(p_mid), _a(p_slow))
    ok = (f > 0) & (m > 0) & (s > 0)
    return np.where(ok, np.minimum(np.minimum(f, m), s), UNDEF)


def price_claim(p_mid, p_slow) -> np.ndarray:
    """``kernels.price_claim`` elementwise."""
    m, s = np.broadcast_arrays(_a(p_mid), _a(p_slow))
    return np.where((m > 0) & (s > 0), np.maximum(m, s), UNDEF)


def halt3_divergence(p_fast, p_mid, p_slow, divergence_bps: int) -> np.ndarray:
    """``kernels.halt3_divergence`` elementwise (bool)."""
    f, m, s = np.broadcast_arrays(_a(p_fast), _a(p_mid), _a(p_slow))
    k = BPS - int(divergence_bps)
    ok = (f > 0) & (m > 0) & (s > 0)
    return ok & ((f * BPS < k * m) | (m * BPS < k * s))


def price_combine(x_mint, x_claim, a_mint, a_claim):
    """``kernels.price_combine`` elementwise → ``(pMint, pClaim, pEmerg)`` arrays.

    Note the C++ (and the scalar kernel) treat only *missing* as undefined here; prices are
    positive whenever defined, so ``<= 0`` as undefined is the same thing.
    """
    xm, xc, am, ac = np.broadcast_arrays(_a(x_mint), _a(x_claim), _a(a_mint), _a(a_claim))
    okm = (xm > 0) & (am > 0)
    okc = (xc > 0) & (ac > 0)
    return (np.where(okm, np.minimum(xm, am), UNDEF), np.where(okc, np.maximum(xc, ac), UNDEF),
            np.where(okc, np.minimum(xc, ac), UNDEF))


# ---------------------------------------------------------------------------------------------------
# SIGMA-1 series


def _isqrt_i64(x: np.ndarray) -> np.ndarray:
    """Exact floor sqrt for 0 ≤ x < 2**62 (float estimate, then integer correction)."""
    s = np.floor(np.sqrt(x.astype(np.float64))).astype(_I64)
    for _ in range(2):
        s = np.where(s * s > x, s - 1, s)
        s = np.where((s + 1) * (s + 1) <= x, s + 1, s)
    return s


def _strided_window_sum(a: np.ndarray, step: int, count: int) -> np.ndarray:
    """``out[..., i] = Σ_{k=0}^{count-1} a[..., i − k·step]`` with out-of-range terms 0."""
    if count <= 0:
        return np.zeros_like(a)
    n = a.shape[-1]
    cs = np.zeros(a.shape, dtype=_I64)
    for i0 in range(step):   # cumulative sum along each residue class: cs[i] = a[i] + cs[i - step]
        cs[..., i0::step] = np.cumsum(a[..., i0::step], axis=-1)
    out = cs.copy()
    shift = count * step
    if shift < n:
        out[..., shift:] -= cs[..., :n - shift]
    return out


def sigma_mult_series(p_fast, vol_window: int, vol_step: int, sigma_ref_bps: int, periods_per_year: int,
                      max_bps: int) -> np.ndarray:
    """SIGMA-1 multiplier at every block of a pFast series (``kernels.sigma_mult_bps`` over
    ``kernels.sigma_samples(p_fast, i, volWindow, volStep)``). ``p_fast`` ``(..., n)``, undefined
    ``<= 0``; element 0 is ``startHeight``. Returns int64 bps."""
    pf = _a(p_fast)
    if sigma_ref_bps <= 0:
        return np.full(pf.shape, BPS, dtype=_I64)
    max_bps = max(int(max_bps), BPS)
    n = pf.shape[-1]
    nret = vol_window // vol_step if vol_step > 0 else 0
    if nret < 1:   # fewer than two samples
        return np.full(pf.shape, max_bps, dtype=_I64)
    step = vol_step
    defined = pf > 0
    # ret[j]: return between samples at j and j - step (needs j >= step and both defined)
    prev = np.zeros_like(pf)
    if step < n:
        prev[..., step:] = pf[..., :n - step]
    pair_ok = defined.copy()
    pair_ok[..., :step] = False
    pair_ok &= prev > 0
    safe_prev = np.where(pair_ok, prev, 1)
    r = np.where(pair_ok, np.abs(pf - prev) * BPS // safe_prev, 0)
    ppy = max(int(periods_per_year), 0)
    r_safe = int(np.sqrt((2**62) // (nret * max(ppy, 1)))) - 1
    big = r > r_safe
    sq = np.where(big, 0, r) ** 2
    bad = (~pair_ok).astype(_I64)
    sum_sq = _strided_window_sum(sq, step, nret)
    n_bad = _strided_window_sum(bad, step, nret)
    n_big = _strided_window_sum(big.astype(_I64), step, nret)
    # the window also needs the oldest sample index i - nret*step >= 0 (covered by pair_ok at that
    # pair: ret at i-(nret-1)*step needs i-(nret-1)*step >= step) and every pair term in range
    idx = np.arange(n)
    in_range = idx - (nret - 1) * step >= step
    undefined = (n_bad > 0) | ~in_range
    var = sum_sq // nret
    sigma = _isqrt_i64(var * ppy)
    mult = sigma * BPS // int(sigma_ref_bps)
    out = np.clip(mult, BPS, max_bps)
    out = np.where(undefined, max_bps, out)
    fix = (n_big > 0) & ~undefined
    if fix.any():
        flat_pf = pf.reshape(-1, n)
        flat_out = out.reshape(-1, n)
        for row, i in zip(*np.nonzero(fix.reshape(-1, n)), strict=True):
            series = [int(v) if v > 0 else None for v in flat_pf[row]]
            flat_out[row, i] = K.sigma_mult_bps(K.sigma_samples(series, int(i), vol_window, vol_step),
                                                sigma_ref_bps, periods_per_year, max_bps)
        out = flat_out.reshape(pf.shape)
    return out


def min_ratio_bps(base_ratio_bps, sigma_mult_bps) -> np.ndarray:
    b, s = np.broadcast_arrays(_a(base_ratio_bps), _a(sigma_mult_bps))
    return np.where((b > 0) & (s > 0), b * s // BPS, 0)


# ---------------------------------------------------------------------------------------------------
# money kernels (exact int64 via the splits documented above)

_Q_MAX = MAX_MONEY // COIN + 1   # a quotient above this puts q·COIN over MAX_MONEY


def _ceil_mul_coin_div(x: np.ndarray, p: np.ndarray):
    """``ceil(x·COIN / p)`` for x ≥ 0, p > 0 as int64, plus a mask where it exceeds MAX_MONEY."""
    q, r = np.divmod(x, p)
    over = q > _Q_MAX
    qs = np.where(over, 0, q)
    rc = r * COIN                    # r < p: needs p ≤ 9.2e10 (guarded by the caller)
    f, rem = np.divmod(rc, p)
    val = qs * COIN + f + (rem > 0)
    return val, over | (val > MAX_MONEY)


def _ceil_ratio(a, b, p):
    """Shared body of required_zat / claimant_max_zat: ceil(a·b·COIN / p), UNDEF when undefined
    (a, b, p ≤ 0) or > MAX_MONEY; elements outside the provable int64 range use the scalar
    kernel."""
    a, b, p = np.broadcast_arrays(_a(a), _a(b), _a(p))
    ok = (a > 0) & (b > 0) & (p > 0)
    guard = ok & ((p > 9 * 10**10) | (a > (2**62) // np.maximum(b, 1)))
    aa, bb, pp = np.where(ok & ~guard, a, 0), np.where(ok & ~guard, b, 0), np.where(ok & ~guard, p, 1)
    val, over = _ceil_mul_coin_div(aa * bb, pp)
    out = np.where(ok & ~guard & ~over, val, UNDEF)
    return out, guard


def required_zat(cents, min_ratio, p_mint) -> np.ndarray:
    """``kernels.required_zat`` elementwise (UNDEF = None)."""
    out, guard = _ceil_ratio(cents, min_ratio, p_mint)
    if guard.any():
        c, m, p = np.broadcast_arrays(_a(cents), _a(min_ratio), _a(p_mint))
        for ix in zip(*np.nonzero(guard), strict=True):
            v = K.required_zat(int(c[ix]), int(m[ix]), int(p[ix]))
            out[ix] = UNDEF if v is None else v
    return out


def claimant_max_zat(minted_cents, margin_bps, p_claim) -> np.ndarray:
    """``kernels.claimant_max_zat`` elementwise (UNDEF = None)."""
    out, guard = _ceil_ratio(minted_cents, margin_bps, p_claim)
    if guard.any():
        c, m, p = np.broadcast_arrays(_a(minted_cents), _a(margin_bps), _a(p_claim))
        for ix in zip(*np.nonzero(guard), strict=True):
            v = K.claimant_max_zat(int(c[ix]), int(m[ix]), int(p[ix]))
            out[ix] = UNDEF if v is None else v
    return out


def residual_zat(collateral_zat, claimant_max) -> np.ndarray:
    """``kernels.residual_zat`` elementwise (claimant_max UNDEF/negative = None → 0)."""
    c, m = np.broadcast_arrays(_a(collateral_zat), _a(claimant_max))
    return np.where((m >= 0) & (c > m), c - m, 0)


def is_underwater(collateral_zat, p_claim, minted_cents, threshold_bps) -> np.ndarray:
    """``kernels.is_underwater`` elementwise: ``c < ceil(minted·thr·COIN / pClaim)``."""
    c, p, m, t = np.broadcast_arrays(_a(collateral_zat), _a(p_claim), _a(minted_cents), _a(threshold_bps))
    ok = (p > 0) & (m > 0) & (t > 0)
    c = np.maximum(c, 0)
    guard = ok & ((p > 9 * 10**10) | (m > (2**62) // np.maximum(t, 1)) | (c > 2**62))
    use = ok & ~guard
    x = np.where(use, m * np.where(use, t, 0), 0)
    pp = np.where(use, p, 1)
    q, r = np.divmod(x, pp)
    over = q > (2**62) // COIN          # ceiling ≥ ~4.6e18 > any guarded collateral
    qs = np.where(over, 0, q)
    f, rem = np.divmod(r * COIN, pp)
    thr = qs * COIN + f + (rem > 0)
    out = use & (over | (c < thr))
    if guard.any():
        for ix in zip(*np.nonzero(guard), strict=True):
            out[ix] = K.is_underwater(int(c[ix]), int(p[ix]), int(m[ix]), int(t[ix]))
    return out


def _floor_mul_div_coin(a: np.ndarray, p: np.ndarray) -> np.ndarray:
    """⌊a·p / COIN⌋ for 0 ≤ a ≤ ~9.2e18 and 0 < p with ⌊a/COIN⌋·p in int64 (caller guards)."""
    q, r = np.divmod(a, COIN)
    return q * p + (r * p) // COIN


def global_ratio_bps(collateral_zat, p_mint, supply_cents) -> np.ndarray:
    """``kernels.global_ratio_bps`` elementwise (UNDEF = None)."""
    a, p, s = np.broadcast_arrays(_a(collateral_zat), _a(p_mint), _a(supply_cents))
    ok = (s > 0) & (p > 0) & (a >= 0)
    guard = ok & ((p > 9 * 10**10) | ((a // COIN) > (2**62) // np.maximum(p, 1)))
    use = ok & ~guard
    v = _floor_mul_div_coin(np.where(use, a, 0), np.where(use, p, 1)) // np.where(use, s, 1)
    out = np.where(use, v, UNDEF)
    if guard.any():
        for ix in zip(*np.nonzero(guard), strict=True):
            r = K.global_ratio_bps(int(a[ix]), int(p[ix]), int(s[ix]))
            out[ix] = UNDEF if r is None else r
    return out


def halt2_global_ratio(collateral_zat, p_mint, supply_cents, halt_bps: int) -> np.ndarray:
    r = global_ratio_bps(collateral_zat, p_mint, supply_cents)
    return (r != UNDEF) & (r < int(halt_bps))


def cap_cents(issued_zat, p_mint) -> np.ndarray:
    """``kernels.cap_cents`` elementwise (UNDEF = None)."""
    a, p = np.broadcast_arrays(_a(issued_zat), _a(p_mint))
    ok = (p > 0) & (a >= 0)
    guard = ok & ((p > 9 * 10**10) | ((a // COIN) > (2**62) // np.maximum(p, 1)))
    use = ok & ~guard
    v = _floor_mul_div_coin(np.where(use, a, 0), np.where(use, p, 1)) // BPS
    out = np.where(use, v, UNDEF)
    if guard.any():
        for ix in zip(*np.nonzero(guard), strict=True):
            r = K.cap_cents(int(a[ix]), int(p[ix]))
            out[ix] = UNDEF if r is None else r
    return out


def supply_cap_cents(issued_zat, p_mint, cap_bps: int) -> np.ndarray:
    """``kernels.supply_cap_cents`` elementwise (UNDEF = None, including capBps ≤ 0)."""
    c = cap_cents(issued_zat, p_mint)
    if cap_bps <= 0:
        return np.full(c.shape, UNDEF, dtype=_I64)
    q, r = np.divmod(np.maximum(c, 0), BPS)
    if int(q.max(initial=0)) > (2**62) // int(cap_bps):
        a, p = np.broadcast_arrays(_a(issued_zat), _a(p_mint))
        pairs = zip(a.ravel(), p.ravel(), strict=True)
        vals = [K.supply_cap_cents(int(x), int(y), cap_bps) for x, y in pairs]
        return np.array([UNDEF if v is None else v for v in vals], dtype=_I64).reshape(c.shape)
    return np.where(c >= 0, q * int(cap_bps) + r * int(cap_bps) // BPS, UNDEF)


def fee_zat(collateral_zat, fee_min: int, fee_bps: int) -> np.ndarray:
    """``kernels.fee_zat`` elementwise (exact; ``fee_bps`` scalar)."""
    c = np.maximum(_a(collateral_zat), 0)
    b = max(int(fee_bps), 0)
    q, r = np.divmod(c, BPS)
    if b and int(q.max(initial=0)) > (2**62) // b:
        return np.array([K.fee_zat(int(x), fee_min, fee_bps) for x in c.ravel()], dtype=_I64).reshape(c.shape)
    return np.maximum(int(fee_min), q * b + r * b // BPS)


def attest_fee_zat(fee, attest_fee_bps: int) -> np.ndarray:
    """``kernels.attest_fee_zat`` elementwise (``attest_fee_bps`` scalar)."""
    f = _a(fee)
    b = int(attest_fee_bps)
    if b <= 0:
        return np.zeros(f.shape, dtype=_I64)
    q, r = np.divmod(np.maximum(f, 0), BPS)
    if int(q.max(initial=0)) > (2**62) // b:
        return np.array([K.attest_fee_zat(int(x), b) for x in f.ravel()], dtype=_I64).reshape(f.shape)
    return np.where(f > 0, q * b + r * b // BPS, 0)


# ---------------------------------------------------------------------------------------------------
# ACT series


def signal_counts(signals, signal_window: int) -> np.ndarray:
    """``kernels.signal_count`` at every block of a bool signal series ``(..., n)``."""
    s = np.asarray(signals, dtype=_I64)
    c = np.cumsum(s, axis=-1)
    out = c.copy()
    if signal_window < s.shape[-1]:
        out[..., signal_window:] -= c[..., :-signal_window]
    return out


def hysteresis(set_mask, hold_mask, initial: bool = False) -> np.ndarray:
    """``state_t = set_t or (state_{t−1} and hold_t)`` along the last axis, vectorised by
    forward-filling the last decisive event (set → True; not set and not hold → False)."""
    s = np.asarray(set_mask, dtype=bool)
    h = np.asarray(hold_mask, dtype=bool)
    s, h = np.broadcast_arrays(s, h)
    n = s.shape[-1]
    decisive = s | ~h
    idx = np.where(decisive, np.arange(n), -1)
    last = np.maximum.accumulate(idx, axis=-1)
    val_at = np.take_along_axis(s, np.maximum(last, 0), axis=-1)
    return np.where(last >= 0, val_at, bool(initial))


def participation_halt_series(counts, active, activation_threshold: int, participation_floor: int,
                              initial: bool = False) -> np.ndarray:
    """ACT-4 over a series (``kernels.participation_halt_step`` iterated)."""
    c = _a(counts)
    return hysteresis(np.asarray(active, dtype=bool) & (c < max(0, participation_floor)),
                      c < max(0, activation_threshold), initial)


def enforcement_halt_series(counts, active, enforcement_resume: int, enforcement_floor: int,
                            initial: bool = False) -> np.ndarray:
    """ACT-6 over a series (``kernels.enforcement_halt_step`` iterated)."""
    c = _a(counts)
    return hysteresis(np.asarray(active, dtype=bool) & (c < max(0, enforcement_floor)),
                      c < max(0, enforcement_resume), initial)

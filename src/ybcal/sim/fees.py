"""FEE-1/2, AFEE-1, eligible payees, the FEE-W wallet default, pool and attestor revenue
(owner: WP-4; PLAN §5.6, §5.9).

Rules (src/yellowback/math.h, state.cpp @ 7702d22, through :mod:`ybcal.model.kernels`):

* **FEE-1** ``feeZat(collateral) = max(feeMin, collateral · feeBps / 10^4)`` — the same formula for a
  MINT (MINT-8, on ``vout[0]``), an owner redeem and a claim (RED-3, on the vault's collateral).
* **AFEE-1** ``attestFeeZat = feeZat · attestFeeBps / 10^4``, paid *in addition* to an attestor of
  the bundle when ARMED (MINT-8 / RED-3 attestor clause; the owner path never pays it).
* **FEE-2** ``E(R)`` = payout keys of the quote tags at ``h ∈ (R − payeeWindow, R]`` minus
  ``Snapshots[R].pinnedKeys``; empty ⇒ **FEE-0** (no fee).
* **MINT-5 floor** a mint's collateral must be ``≥ 4 · feeMin`` (so the redeem fee is affordable).

Wallet policy (L6, *excluded* parameters ``nPenalty``, ``accuracyWindow``, ``payeeTiltBps``):
**FEE-W** picks the payee among the non-penalised quote tags of the window with weight
``10^4 + payeeTiltBps · accuracyBps(key, R) / 10^4`` (REG-2/3 over the REG-4 judgements,
:func:`ybcal.model.kernels.reg4_judgement`). :func:`fee_w_weights` returns the expected share of
each pool (the SHA-256 pick is uniform over the cumulative weight).

Revenue helpers turn a vault book (:class:`ybcal.sim.vaults.VaultBookResult`) into pool revenue per
pool per day and attestor revenue per seated attestor per month.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from ybcal.model import kernels as K
from ybcal.model import vkernels as V
from ybcal.units import BLOCKS_PER_DAY, BPS, COIN

OWNER_WP = "WP-4"


# ---------------------------------------------------------------------------------------------------
# FEE-1 / AFEE-1


@dataclass(frozen=True)
class FeeBreakdown:
    """The fees one transaction pays, zat."""

    pool_zat: int  #: FEE-1 to an E(R) payee (0 under FEE-0)
    attest_zat: int  #: AFEE-1 to a bundle attestor (0 unless ARMED on a mint or a claim)

    @property
    def total_zat(self) -> int:
        return self.pool_zat + self.attest_zat


def tx_fees(
    params: Mapping,
    collateral_zat: int,
    *,
    armed: bool = False,
    eligible: bool = True,
    owner_path: bool = False,
) -> FeeBreakdown:
    """Fees of a MINT (``collateral`` = ``vout[0]``), an owner redeem or a claim (the vault's
    collateral). ``armed`` adds AFEE-1 except on the owner path (RED-1 amended reads no bundle)."""
    base = K.fee_zat(int(collateral_zat), int(params["feeMin"]), int(params["feeBps"]))
    pool = base if eligible else 0
    att = K.attest_fee_zat(base, int(params["attestFeeBps"])) if armed and not owner_path else 0
    return FeeBreakdown(pool, att)


def fees_zat(params: Mapping, collateral_zat, *, armed=False, eligible=True, owner_path=False):
    """Vectorised :func:`tx_fees`: ``(pool_zat, attest_zat)`` int64 arrays."""
    c = np.asarray(collateral_zat, dtype=np.int64)
    base = V.fee_zat(c, int(params["feeMin"]), int(params["feeBps"]))
    pool = np.where(np.asarray(eligible, dtype=bool), base, 0)
    att = V.attest_fee_zat(base, int(params["attestFeeBps"]))
    arm = np.asarray(armed, dtype=bool) & ~np.asarray(owner_path, dtype=bool)
    return pool.astype(np.int64), np.where(arm, att, 0).astype(np.int64)


def min_collateral_floor(params: Mapping) -> int:
    """MINT-5's ``4 · feeMin`` floor, zat."""
    return 4 * int(params["feeMin"])


# ---------------------------------------------------------------------------------------------------
# FEE-2: eligible payees


def eligible_payees(
    params: Mapping,
    tag_pool: Sequence[int],
    tag_price: Sequence[int],
    r_index: int,
    pinned: int = 0,
    *,
    start_index: int = 0,
) -> list[int]:
    """``E(R)`` (reference ``eligible_payees``) on one path's tag stream: pool ids of the quote tags
    at indices ``(r − payeeWindow, r]`` (not before ``start_index`` = startHeight), first-seen
    order, deduplicated, minus the pools in the PIN-1 bitmask ``pinned`` (Snapshots[R])."""
    w = int(params["payeeWindow"])
    out: list[int] = []
    for i in range(max(r_index - w + 1, start_index), r_index + 1):
        k = int(tag_pool[i])
        if k >= 0 and int(tag_price[i]) > 0 and k not in out and not (pinned >> k) & 1:
            out.append(k)
    return out


def eligible_nonempty_series(
    params: Mapping, tag_price, tag_present, tag_pool=None, pinned_pools=None
) -> np.ndarray:
    """``E(R) ≠ ∅`` at every column (``(paths, n)`` bool): a rolling count of quote tags over
    ``(R − payeeWindow, R]``; columns with pinned keys are recomputed exactly (a pinned pool's tags
    do not count)."""
    tp = np.atleast_2d(np.asarray(tag_price, dtype=np.int64))
    q = np.atleast_2d(np.asarray(tag_present, dtype=bool)) & (tp > 0)
    w = int(params["payeeWindow"])
    c = np.cumsum(q, axis=-1, dtype=np.int64)
    cnt = c.copy()
    if w < c.shape[-1]:
        cnt[:, w:] -= c[:, :-w]
    out = cnt > 0
    if pinned_pools is not None and tag_pool is not None:
        pin = np.atleast_2d(np.asarray(pinned_pools, dtype=np.uint64))
        pool = np.atleast_2d(np.asarray(tag_pool))
        for p, j in zip(*np.nonzero(pin), strict=True):
            out[p, j] = bool(eligible_payees(params, pool[p], tp[p], int(j), int(pin[p, j])))
    return out


# ---------------------------------------------------------------------------------------------------
# REG-4 judgements, REG-2/3, FEE-W


@dataclass
class JudgementSeries:
    """REG-4 for the quote tag at every column ``t`` (``(paths, n)`` bool). ``evaluated`` is False
    where there is no quote tag, too few peers, or the judgement height ``t + peerLag`` lies beyond
    the series."""

    evaluated: np.ndarray
    in_band: np.ndarray
    penalized: np.ndarray


def judgement_series(params: Mapping, tag_price, tag_present) -> JudgementSeries:
    """Vectorised REG-4 (state.cpp:910-936): peers = the quote tags at ``[t − lag, t + lag − 1]``
    other than ``t`` (not before column 0 = startHeight); evaluated iff ≥ ``peerMin`` peers;
    ``dev = |q − m|·10^4 // m`` with ``m`` the lower median of the peers. Property-tested equal to
    :func:`ybcal.model.kernels.reg4_judgement`."""
    tp = np.atleast_2d(np.asarray(tag_price, dtype=np.int64))
    q = np.atleast_2d(np.asarray(tag_present, dtype=bool)) & (tp > 0)
    P, n = tp.shape
    lag = int(params["peerLag"])
    big = np.iinfo(np.int64).max
    vals = np.where(q, tp, big)
    pad = np.full((P, lag), big, dtype=np.int64)
    ext = np.concatenate([pad, vals, pad], axis=1)  # ext[:, i + lag] = vals[:, i]
    offs = [o for o in range(-lag, lag) if o != 0]
    if not offs:
        z = np.zeros((P, n), bool)
        return JudgementSeries(z, z.copy(), z.copy())
    peers = np.stack([ext[:, lag + o : lag + o + n] for o in offs], axis=-1)  # (P, n, 2lag-1)
    cnt = (peers != big).sum(axis=-1)
    srt = np.sort(peers, axis=-1)
    k = np.maximum(cnt - 1, 0) // 2
    m = np.take_along_axis(srt, k[..., None], axis=-1)[..., 0]
    judged = np.arange(n)[None, :] + lag < n
    ev = q & (cnt >= int(params["peerMin"])) & (cnt > 0) & judged
    m_s = np.where(ev, m, 1)
    dev = np.abs(np.where(ev, tp, 0) - m_s) * BPS // m_s
    in_band = ev & (dev <= max(0, int(params["accuracyBandBps"])))
    pen = ev & (dev > max(0, int(params["deviationBps"])))
    return JudgementSeries(ev, in_band, pen)


def fee_w_weights(
    params: Mapping,
    tag_pool: Sequence[int],
    tag_price: Sequence[int],
    judgements: JudgementSeries | None,
    r_index: int,
    *,
    path: int = 0,
    pinned: int = 0,
    n_penalty: int | None = None,
    accuracy_window: int | None = None,
    payee_tilt_bps: int | None = None,
) -> dict[int, float]:
    """FEE-W (spec §3.7, wallet default, L6): the expected payee share of each pool at ``R``.

    Candidates are the quote tags at ``(R − payeeWindow, R]`` whose pool is neither penalised
    (REG-2: a penalised judgement of that pool's tag at ``t`` with ``t + peerLag < R ≤ t + peerLag
    + nPenalty``) nor pinned; each tag weighs ``10^4 + tilt · accuracyBps(pool, R) / 10^4`` (REG-3
    over ``(R − peerLag − accuracyWindow, R − peerLag]``). All penalised → every key of ``E(R)``
    with equal weight. Returns ``{pool: share}`` (empty under FEE-0). ``nPenalty`` /
    ``accuracyWindow`` / ``payeeTiltBps`` default to the set's values (the excluded L6 params G6
    tunes)."""
    n_pen = int(params["nPenalty"] if n_penalty is None else n_penalty)
    acc_w = int(params["accuracyWindow"] if accuracy_window is None else accuracy_window)
    tilt = int(params["payeeTiltBps"] if payee_tilt_bps is None else payee_tilt_bps)
    lag = int(params["peerLag"])
    w = int(params["payeeWindow"])
    E = eligible_payees(params, tag_pool, tag_price, r_index, pinned)
    if not E:
        return {}
    pen = judgements.penalized[path] if judgements is not None else None
    ev = judgements.evaluated[path] if judgements is not None else None
    ib = judgements.in_band[path] if judgements is not None else None

    def penalized(k: int) -> bool:
        if pen is None:
            return False
        lo = max(0, r_index - lag - n_pen)
        for t in range(lo, max(lo, r_index - lag)):
            if int(tag_pool[t]) == k and int(tag_price[t]) > 0 and pen[t]:
                return True
        return False

    def accuracy(k: int) -> int:
        if ev is None:
            return 0
        quoted = inb = 0
        for t in range(max(0, r_index - lag - acc_w + 1), r_index - lag + 1):
            if int(tag_pool[t]) == k and int(tag_price[t]) > 0 and ev[t]:
                quoted += 1
                inb += int(bool(ib[t]))
        return BPS * inb // quoted if quoted else 0

    pen_k = {k: penalized(k) for k in E}
    acc_k = {k: accuracy(k) for k in E}
    weights: dict[int, int] = {}
    for i in range(max(r_index - w + 1, 0), r_index + 1):
        k = int(tag_pool[i])
        if k in pen_k and not pen_k[k] and int(tag_price[i]) > 0:
            weights[k] = weights.get(k, 0) + BPS + tilt * acc_k[k] // BPS
    if not weights:
        weights = {k: 1 for k in E}
    tot = sum(weights.values())
    return {k: v / tot for k, v in weights.items()}


# ---------------------------------------------------------------------------------------------------
# Revenue and affordability


def pool_revenue(
    fee_zat,
    step_blocks: int,
    n_steps: int,
    payee_share: Mapping[int, float] | None = None,
    price_microusd=None,
) -> dict:
    """Pool fee revenue per day: ``fee_zat`` is the per-step FEE-1 total (``(paths, n)`` or a flat
    total over ``n_steps`` steps of ``step_blocks`` blocks). Returns YEC/day (and USD/day at
    ``price_microusd``, step-aligned), total and per pool by ``payee_share``."""
    f = np.asarray(fee_zat, dtype=np.float64)
    days = n_steps * step_blocks / BLOCKS_PER_DAY
    yec_day = float(f.sum()) / COIN / max(days, 1e-12) / (f.shape[0] if f.ndim == 2 else 1)
    out: dict = {"yec_per_day": yec_day}
    if price_microusd is not None:
        usd = (f / COIN * np.asarray(price_microusd, dtype=np.float64) / 1e6).sum()
        out["usd_per_day"] = float(usd) / max(days, 1e-12) / (f.shape[0] if f.ndim == 2 else 1)
    if payee_share:
        out["per_pool_yec_per_day"] = {k: yec_day * s for k, s in payee_share.items()}
        if "usd_per_day" in out:
            out["per_pool_usd_per_day"] = {k: out["usd_per_day"] * s for k, s in payee_share.items()}
    return out


def attestor_revenue_month(attest_fee_usd_total: float, days: float, n_seated: int) -> float:
    """AFEE-1 revenue per seated attestor per 30 days (fees spread evenly over the seated set — the
    bundle's members rotate with W9 selection)."""
    if days <= 0 or n_seated <= 0:
        return 0.0
    return attest_fee_usd_total / days * 30.0 / n_seated


def fee_share_of_value(
    params: Mapping, collateral_zat, *, armed: bool = False, include_close: bool = True
) -> np.ndarray:
    """Fees of a vault's life as a share of its locked collateral: the MINT's FEE-1 (+ AFEE-1 when
    ARMED) plus, with ``include_close``, the owner redeem's FEE-1. Both legs are paid in YEC on the
    same collateral, so the share is price-free; multiply by the collateral ratio for the share of
    the debt (:func:`fee_table` does that for given sizes)."""
    c = np.asarray(collateral_zat, dtype=np.int64)
    pool, att = fees_zat(params, c, armed=armed)
    tot = pool + att
    if include_close:
        tot = tot + fees_zat(params, c, armed=False, owner_path=True)[0]
    return tot / np.maximum(c, 1)


def fee_usd(params: Mapping, collateral_zat, price_microusd, *, armed=False, owner_path=False) -> np.ndarray:
    """FEE-1 + AFEE-1 of one transaction in USD at ``price``."""
    pool, att = fees_zat(params, collateral_zat, armed=armed, owner_path=owner_path)
    return (pool + att).astype(np.float64) / COIN * np.asarray(price_microusd, dtype=np.float64) / 1e6


@dataclass(frozen=True)
class Affordability:
    """Redemption affordability of a vault at a crash price (PLAN §5.6 last bullet, §5.9)."""

    collateral_zat: int
    fee_zat: int
    floor_binds: bool  #: the MINT-5 ``4·feeMin`` floor set the collateral
    fee_share_of_collateral: float
    fee_usd_at_crash: float
    collateral_usd_at_crash: float
    debt_usd: float
    redeem_worth_it: bool  #: collateral net of the fee still covers the debt at the crash price


def redemption_affordability(
    params: Mapping, cents: int, term_class: int, p_mint: int, crash_price: int, *, sigma_mult_bps: int = BPS
) -> Affordability:
    """A vault of ``cents`` minted at ``p_mint`` (wallet rounding: max(required, 4·feeMin) up to
    1,000 zat), valued at ``crash_price``: the redeem fee in USD and as a share of the collateral, and
    whether the owner still redeems (collateral − fee ≥ debt)."""
    mr = K.min_ratio_bps(int(params[f"baseRatioBps[{term_class}]"]), int(sigma_mult_bps))
    if int(cents) <= 0 or mr <= 0 or int(p_mint) <= 0:
        # math.h RequiredCollateral: nullopt for a non-positive input ("undefined"), distinct from K14
        raise ValueError(f"mint undefined: non-positive input (cents {cents}, minRatio {mr}, pMint {p_mint})")
    req = K.required_zat_rounded(int(cents), mr, int(p_mint))
    if req is None:
        raise ValueError("mint unsatisfiable (K14): the collateral requirement exceeds MAX_MONEY")
    floor = min_collateral_floor(params)
    coll = max(req, floor)
    if coll % 1000:
        coll += 1000 - coll % 1000
    fee = K.fee_zat(coll, int(params["feeMin"]), int(params["feeBps"]))
    to_usd = crash_price / 1e6 / COIN
    return Affordability(
        collateral_zat=coll,
        fee_zat=fee,
        floor_binds=req < floor,
        fee_share_of_collateral=fee / coll,
        fee_usd_at_crash=fee * to_usd,
        collateral_usd_at_crash=coll * to_usd,
        debt_usd=cents / 100,
        redeem_worth_it=(coll - fee) * to_usd >= cents / 100,
    )


def fee_table(
    params: Mapping,
    sizes_cents: Sequence[int],
    p_mint: int,
    *,
    term_class: int = 0,
    sigma_mult_bps: int = BPS,
    armed: bool = False,
) -> list[dict]:
    """G6/G9 table: for each size, collateral, mint and redeem fees (zat and USD at ``p_mint``) and
    the round-trip fee share of the debt."""
    rows = []
    for cents in sizes_cents:
        a = redemption_affordability(
            params, int(cents), term_class, p_mint, p_mint, sigma_mult_bps=sigma_mult_bps
        )
        mint = tx_fees(params, a.collateral_zat, armed=armed)
        red = tx_fees(params, a.collateral_zat, owner_path=True)
        usd = p_mint / 1e6 / COIN
        rows.append(
            {
                "cents": int(cents),
                "collateral_zat": a.collateral_zat,
                "floor_binds": a.floor_binds,
                "mint_fee_zat": mint.total_zat,
                "redeem_fee_zat": red.total_zat,
                "round_trip_fee_usd": (mint.total_zat + red.total_zat) * usd,
                "fee_share_of_debt": (mint.total_zat + red.total_zat) * usd / (int(cents) / 100),
            }
        )
    return rows


__all__ = [
    "Affordability",
    "FeeBreakdown",
    "JudgementSeries",
    "attestor_revenue_month",
    "eligible_nonempty_series",
    "eligible_payees",
    "fee_share_of_value",
    "fee_table",
    "fee_usd",
    "fee_w_weights",
    "fees_zat",
    "judgement_series",
    "min_collateral_floor",
    "pool_revenue",
    "redemption_affordability",
    "tx_fees",
]

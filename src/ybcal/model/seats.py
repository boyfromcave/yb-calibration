"""Attestor seat ranking, eviction and the collateral-based bond minimum (quantum plan D-Q-29..D-Q-31).

Exact integer mirrors of the owner's 2026-10-11 attestor rules as built on ycash-dd ``q/attest``
(``f07a9aef6``; the ycash6 port follows on the same branch name). Citations are ``file:line`` there:

* ``src/yellowback/math.h:257`` ``Log2Q16`` — log2 in Q16, deterministic but *not* exact (one below the
  true floor for a handful of inputs; the node, its Python model and this module compute the same bits).
* ``math.h:282`` ``SeatAgeFactorQ16`` — the tenure factor, 65,536 (×1.0) at no tenure rising on a log
  scale to 124,518 (×1.9) at ``SEAT_AGE_CAP`` (one year).
* ``math.h:297`` ``SeatWeight`` = bond × factor; ``state.cpp:1839`` ``SeatRank`` = SeatWeight × (10,000 +
  ``SEAT_MARGIN_BPS`` when seated) — seat ranking and eviction **only**. The price statistic and W9
  selection keep ``BondWeight`` = bond × min(age, AGE_CAP) (:func:`ybcal.model.kernels.bond_weight`).
* ``state.cpp:1890`` ``EvictionVictim`` — a join into a full set evicts the not-live member of least seat
  weight first, else the member of least seat weight when the joiner's (tenure 0) beats it by more than
  ``EVICT_MARGIN_BPS``; ties by key bytes; never for a bond below the bond floor (seats review B2).
* ``math.h:375`` ``BondMinFromCollateralZat`` (D-Q-30) — clamp(ceil(collateral × BOND_SHARE_BPS /
  (10^4 × N_SLOTS)), 10,000 YEC, 20,000 YEC); no price is read. The node reads the collateral from the
  snapshot before the join, once, at registration.
* D-Q-30 / D-Q-31: exit any time; the bond returns ``BOND_MATURITY`` (16,128 blocks, 14 days) after the
  exit and stays slashable until then; ``BOND_MIN_LOCK`` is 0.

The vendored reference model (:mod:`ybcal.model.reference`, ycash6 ``7702d22``) predates these rules, so
the mirrors here are transcribed from the C++ and held bit for bit to vectors computed by the node's own
Python model at ``f07a9aef6`` (``seats_vectors.json`` beside this module, written by
``gen_seats_vectors.py``; ``ybcal verify`` section ``seats``) and to the C++ unit-test values
(``src/test/yellowback_math_tests.cpp`` ``seat_weight_log_tenure_factor``). Python integers only.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

OWNER_WP = "WP-1"

#: The node commit the mirrors are transcribed from (ycash-dd ``q/attest``).
SOURCE_COMMIT = "f07a9aef6866145318a20aaa3c985e27b5043f35"

COIN = 100_000_000
BPS = 10_000
Q16 = 65_536
UINT64_MAX = (1 << 64) - 1
#: ×1.9 at the cap: 65,536 + floor(0.9 × 65,536).
FACTOR_MAX_Q16 = Q16 + (9 * Q16) // 10


@dataclass(frozen=True)
class SeatParams:
    """The attestor-seat constants of ``yellowback::Params`` (params.cpp SetCommon / RegtestParams)."""

    n_slots: int = 9  #: N_SLOTS (params.cpp:80)
    bond_min_floor_zat: int = 10_000 * COIN  #: D-Q-30 floor (params.cpp:98; owner 2026-10-11, was 5,000)
    bond_min_cap_zat: int = 20_000 * COIN  #: D-Q-30 cap (params.cpp:99)
    bond_share_bps: int = 500  #: D-Q-30 BOND_SHARE_BPS (params.cpp:100)
    bond_min_lock: int = 0  #: BOND_MIN_LOCK (params.cpp:101; was 420,480)
    bond_maturity: int = 16_128  #: BOND_MATURITY: maturity and the exit delay (params.cpp:102)
    age_cap: int = 207_360  #: AGE_CAP of the price weight (params.cpp:103), unchanged
    seat_age_unit: int = 1_152  #: SEAT_AGE_UNIT u (params.cpp:105), every network
    seat_age_cap: int = 420_480  #: SEAT_AGE_CAP (params.cpp:106), every network
    seat_margin_bps: int = 500  #: SEAT_MARGIN_BPS hysteresis (params.cpp:107)
    evict_margin_bps: int = 500  #: EVICT_MARGIN_BPS (params.cpp:108)
    attestor_seats: int = 15  #: ATTESTOR_SEATS: members of the OPEN set (params.cpp:109)
    attestor_liveness_min: int = 1_152  #: ATTESTOR_LIVENESS_MIN (params.cpp:110)


MAINNET = SeatParams()
#: RegtestParams: D-Q-30 at 1/2,000 of mainnet, maturity 8, N_SLOTS 5, liveness 16 (params.cpp:243-257).
REGTEST = SeatParams(
    n_slots=5,
    bond_min_floor_zat=5 * COIN,
    bond_min_cap_zat=10 * COIN,
    bond_maturity=8,
    age_cap=64,
    attestor_liveness_min=16,
)


def log2_q16(x: int) -> int:
    """math.h:257 Log2Q16: ``(bit length − 1) << 16 | 16 fraction bits`` from a Q31 mantissa squared 16
    times in uint64 with truncation; 0 for 0. Domain uint64."""
    if x < 0 or x > UINT64_MAX:
        raise ValueError("Log2Q16 takes a uint64")
    if x == 0:
        return 0
    n = x.bit_length() - 1
    m = (x << (31 - n)) if n <= 31 else (x >> (n - 31))  # Q31, in [2^31, 2^32)
    frac = 0
    for _ in range(16):
        m = (m * m) >> 31  # < 2^64: no uint64 wrap
        frac <<= 1
        if m >= 1 << 32:
            m >>= 1
            frac |= 1
    return (n << 16) | frac


def seat_age_factor_q16(
    age_blocks: int, u: int = MAINNET.seat_age_unit, cap: int = MAINNET.seat_age_cap
) -> int:
    """math.h:282 SeatAgeFactorQ16: ``65536 + floor(9·65536·(lg(u+a) − lg u) / (10·(lg(u+cap) − lg u)))``,
    ``a = clamp(age, 0, cap)``, ``lg = Log2Q16``; 65,536 when ``u`` or ``cap`` is not positive or the
    denominator is 0."""
    if u <= 0 or cap <= 0:
        return Q16
    a = max(0, min(age_blocks, cap))
    lu = log2_q16(u)
    num = log2_q16(u + a) - lu
    den = log2_q16(u + cap) - lu
    if den == 0:
        return Q16
    return Q16 + (9 * Q16 * num) // (10 * den)


def seat_weight(
    bond_zat: int, age_blocks: int, u: int = MAINNET.seat_age_unit, cap: int = MAINNET.seat_age_cap
) -> int:
    """math.h:297 SeatWeight = bondZat × SeatAgeFactorQ16(age) (Q16-scaled; arith_uint256 in the C++,
    exact here); 0 for a bond ≤ 0."""
    if bond_zat <= 0:
        return 0
    return bond_zat * seat_age_factor_q16(age_blocks, u, cap)


def seat_rank(bond_zat: int, age_blocks: int, seated: bool, p: SeatParams = MAINNET) -> int:
    """state.cpp:1839 SeatRank: SeatWeight × (10,000 + SEAT_MARGIN_BPS if currently seated else 10,000).
    ``age_blocks`` = H − ageOrigin (the founding cohort's origin is the trigger height, as for the
    price weight). The ``N_SLOTS`` ELIGIBLE records of greatest rank are seated, ties by seq."""
    w = seat_weight(bond_zat, age_blocks, p.seat_age_unit, p.seat_age_cap)
    return w * (BPS + (max(0, p.seat_margin_bps) if seated else 0))


@dataclass(frozen=True)
class SeatCandidate:
    """A member of the attestor set as the primitive records it (state.h SeatCandidate)."""

    key: bytes  #: the member's serialized public key (ties order by these bytes)
    bond_zat: int
    join_height: int
    last_act: int


def eviction_victim(
    members: Sequence[SeatCandidate],
    height: int,
    new_bond_zat: int,
    liveness_window: int,
    p: SeatParams = MAINNET,
) -> bytes | None:
    """state.cpp:1890 EvictionVictim: the key a SET_JOIN of ``new_bond_zat`` into the full set evicts at
    ``height``, or ``None``. Order (not live first, least SeatWeight at ``height − join``, key bytes);
    a not-live first is evicted outright, a live one only when ``SeatWeight(new, 0) × 10,000 >
    its weight × (10,000 + EVICT_MARGIN_BPS)``. No eviction by a bond below the floor (review B2)."""
    if new_bond_zat < max(0, p.bond_min_floor_zat):
        return None
    best: tuple[int, int, bytes] | None = None
    for c in members:
        dead = c.last_act < height - liveness_window
        w = seat_weight(c.bond_zat, height - c.join_height, p.seat_age_unit, p.seat_age_cap)
        k = (0 if dead else 1, w, bytes(c.key))
        if best is None or k < best:
            best = k
    if best is None:
        return None
    if best[0] == 0:
        return best[2]
    joiner = seat_weight(new_bond_zat, 0, p.seat_age_unit, p.seat_age_cap) * BPS
    if joiner > best[1] * (BPS + max(0, p.evict_margin_bps)):
        return best[2]
    return None


def bond_min_from_collateral_zat(collateral_zat: int, p: SeatParams = MAINNET) -> int:
    """math.h:375 BondMinFromCollateralZat (D-Q-30): ``clamp(ceil(collateral × BOND_SHARE_BPS / (10^4 ×
    N_SLOTS)), floor, max(floor, cap))``; a negative collateral counts as 0; ``N_SLOTS ≤ 0`` gives the
    cap. No price."""
    floor = max(0, p.bond_min_floor_zat)
    cap = max(floor, p.bond_min_cap_zat)
    if p.n_slots <= 0:
        return cap
    num = max(0, collateral_zat) * max(0, p.bond_share_bps)
    den = BPS * p.n_slots
    z = -(-num // den)
    return cap if z > cap else max(floor, z)


def bond_return_height(exit_height: int, p: SeatParams = MAINNET) -> int:
    """D-Q-30 / D-Q-31: the height from which an exited (or evicted) member's bond can be spent: the
    exit plus the set's maturity, which the module requires to be ``BOND_MATURITY``. Slashable until
    then."""
    return exit_height + p.bond_maturity


def bond_neutral_collateral_zat(p: SeatParams = MAINNET) -> tuple[int, int]:
    """The vault collateral (zat) at which the floor stops binding and at which the cap starts binding:
    the least ``c`` with ``bond_min(c) > floor`` and the least with ``bond_min(c) == cap``."""
    den = BPS * p.n_slots
    share = max(1, p.bond_share_bps)
    floor_c = p.bond_min_floor_zat * den // share + 1
    while bond_min_from_collateral_zat(floor_c - 1, p) > p.bond_min_floor_zat:
        floor_c -= 1
    cap_c = -(-((p.bond_min_cap_zat - 1) * den) // share) + 1
    while cap_c > 0 and bond_min_from_collateral_zat(cap_c - 1, p) >= p.bond_min_cap_zat:
        cap_c -= 1
    return floor_c, cap_c


VECTORS_FILE = "seats_vectors.json"


def check_vectors() -> list[tuple[str, bool, str]]:
    """``ybcal verify`` rows: every vector the node's Python model computed (``seats_vectors.json``)
    recomputed here; one row per function."""
    import json
    from pathlib import Path

    v = json.loads((Path(__file__).resolve().parent / VECTORS_FILE).read_text())
    nets = {"main": MAINNET, "regtest": REGTEST}
    rows: list[tuple[str, bool, str]] = []

    def row(name: str, bad: list[object], n: int) -> None:
        rows.append(
            (
                name,
                not bad,
                f"{n - len(bad)}/{n} @ {v['commit'][:9]}" + (f" first bad {bad[0]!r}" if bad else ""),
            )
        )

    bad_p = [
        f"{net}.{k}" for net, d in v["params"].items() for k, x in d.items() if getattr(nets[net], k) != x
    ]
    row("SeatParams = node model", bad_p, sum(len(d) for d in v["params"].values()))
    row("Log2Q16", [x for x, w in v["log2_q16"] if log2_q16(x) != w], len(v["log2_q16"]))
    f = v["seat_age_factor_q16"]
    row("SeatAgeFactorQ16", [r for r in f if seat_age_factor_q16(r[0], r[1], r[2]) != r[3]], len(f))
    w = v["seat_weight"]
    row("SeatWeight", [r for r in w if seat_weight(r[0], r[1], r[2], r[3]) != r[4]], len(w))
    bad_e = []
    for e in v["eviction_victim"]:
        ms = [SeatCandidate(bytes.fromhex(k), b, j, la) for k, b, j, la in e["members"]]
        got = eviction_victim(ms, e["height"], e["new_bond_zat"], e["liveness_window"], nets[e["net"]])
        if (None if got is None else got.hex()) != e["victim"]:
            bad_e.append(e["height"])
    row("EvictionVictim", bad_e, len(v["eviction_victim"]))
    b = v["bond_min_from_collateral_zat"]
    row(
        "BondMinFromCollateralZat",
        [r for r in b if bond_min_from_collateral_zat(r[1], nets[r[0]]) != r[2]],
        len(b),
    )
    return rows

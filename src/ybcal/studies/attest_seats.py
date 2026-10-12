"""Attestor seats under the 2026-10-11 rules: tenure, eviction and the bond minimum (quantum D-Q-29..D-Q-31).

An analytic study like ``fees_option_d``: no simulation engine, no price pulls, no sweeps; every number is
an exact integer evaluation of the node's own rules through :mod:`ybcal.model.seats` (bit for bit with
ycash-dd ``q/attest`` ``f07a9aef6``), and the only market input is the $0.40 reference price the fees
study took from the frozen 2026-10-04 data. Runs in about a second. ``ybcal seats [--out DIR]``.

Questions:

1. **Tenure factor.** ×1.0 → ×1.9 over a year on a log scale (SEAT_AGE_UNIT one day): what each day of
   tenure is worth, and the bond multiple a newcomer needs against an incumbent.
2. **Time to a seat.** A newcomer of ``r`` × an incumbent's bond becomes ELIGIBLE ``BOND_MATURITY`` after
   joining and is seated once ``r · f(a) · 10⁴ > f(T + a) · (10⁴ + SEAT_MARGIN)`` (both tenures grow):
   days to a seat by ``r`` and the incumbent's tenure ``T``, or never.
3. **Eviction.** The least bond that evicts a live member of bond ``b`` and tenure ``t`` from a full set
   (tenure 0 for the joiner, EVICT_MARGIN); a not-live member goes to any bond ≥ the floor.
4. **Filling and capturing nine seats.** How many 20,000-YEC and floor-sized (10,000-YEC) bonders it
   takes to fill N_SLOTS, and the capital to take 7 (harmful capture, D-RD-ATT-4) or 9 seats from an
   honest set by bond and tenure, against the D-Q-30 minimum.
5. **A trace.** A full 15-member set of floor-sized (10,000-YEC) members (9 seated at one year, 6
   waiting at 30 days) and an entrant who joins one bond a day: who is evicted, when the entrant holds
   the seats.
6. **Bond minimum.** D-Q-30 against vault collateral, with the YED outstanding it implies at 300 %.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path

from ybcal.model import seats as S
from ybcal.units import BLOCKS_PER_DAY, BPS, COIN

OWNER_WP = "Q-attest"

P = S.MAINNET
REF_PRICE_USD = 0.40  #: the 2026-10 reference price of the fees study (frozen-20261004)
HARM_SEATS = 7  #: hardening plan C-4 / D-RD-ATT-4: harmful capture needs 7 of 9 seats by splitting
TENURE_DAYS: tuple[int, ...] = (0, 1, 7, 14, 30, 90, 180, 365, 730)
RATIOS: tuple[float, ...] = (1.0, 1.05, 1.06, 1.1, 1.25, 1.5, 2.0, 4.0)
INCUMBENT_DAYS: tuple[int, ...] = (0, 30, 180, 365, 730)
#: the D-Q-30 floor (10,000 YEC since the owner amended D-Q-30, 2026-10-11; was 5,000)
FLOOR_YEC = P.bond_min_floor_zat // COIN
BONDS_YEC: tuple[int, ...] = (FLOOR_YEC, 15_000, 20_000)
SEAT_SCAN_DAYS = 3 * 365  #: horizon of the time-to-seat scan
TRACE_DAYS = 30


def factor(age_blocks: int) -> int:
    return S.seat_age_factor_q16(age_blocks, P.seat_age_unit, P.seat_age_cap)


def seat_w(bond_zat: int, age_blocks: int) -> int:
    return S.seat_weight(bond_zat, age_blocks, P.seat_age_unit, P.seat_age_cap)


# ---------------------------------------------------------------------------------------------------
# 1. tenure factor


def tenure_rows() -> list[dict[str, float]]:
    """f(t) for the listed tenures, and the bond multiple a fresh ELIGIBLE newcomer (tenure =
    BOND_MATURITY) needs to outrank a seated incumbent of that tenure at once."""
    f_new = factor(P.bond_maturity)
    out = []
    for d in TENURE_DAYS:
        f = factor(d * BLOCKS_PER_DAY)
        out.append(
            {
                "days": d,
                "factor_q16": f,
                "factor": f / S.Q16,
                "seat_multiple_vs_seated": f * (BPS + P.seat_margin_bps) / (f_new * BPS),
                "evict_multiple": f * (BPS + P.evict_margin_bps) / (S.Q16 * BPS),
            }
        )
    return out


# ---------------------------------------------------------------------------------------------------
# 2. time to a seat


def days_to_seat(ratio: float, incumbent_days: int, bond_zat: int = P.bond_min_floor_zat) -> float | None:
    """Days from the join until a newcomer of ``ratio`` × the seated incumbent's bond outranks it
    (SeatRank, the incumbent ×1.05), scanning per block-day; ``None`` if not within SEAT_SCAN_DAYS. The
    newcomer is ELIGIBLE from BOND_MATURITY; the incumbent's tenure advances in step."""
    nb = round(bond_zat * ratio)
    t0 = incumbent_days * BLOCKS_PER_DAY
    step = BLOCKS_PER_DAY // 24  # one hour
    a = P.bond_maturity
    while a <= SEAT_SCAN_DAYS * BLOCKS_PER_DAY:
        if S.seat_rank(nb, a, False, P) > S.seat_rank(bond_zat, t0 + a, True, P):
            return a / BLOCKS_PER_DAY
        a += step
    return None


def time_to_seat_rows() -> list[dict[str, object]]:
    return [
        {"ratio": r, "days_by_incumbent_tenure": {str(t): days_to_seat(r, t) for t in INCUMBENT_DAYS}}
        for r in RATIOS
    ]


# ---------------------------------------------------------------------------------------------------
# 3. eviction thresholds


def least_evicting_bond_zat(bond_zat: int, tenure_blocks: int) -> int:
    """The least joiner bond that evicts a live member (EvictionVictim's margin test, joiner tenure 0),
    never below the floor (review B2)."""
    w = seat_w(bond_zat, tenure_blocks) * (BPS + P.evict_margin_bps)
    # joiner: B * 65536 * BPS > w  ->  B = floor(w / (65536 * BPS)) + 1
    b = w // (S.Q16 * BPS) + 1
    return max(b, P.bond_min_floor_zat)


def evict_rows() -> list[dict[str, object]]:
    return [
        {
            "member_bond_yec": b,
            "least_evicting_bond_yec_by_tenure": {
                str(d): least_evicting_bond_zat(b * COIN, d * BLOCKS_PER_DAY) / COIN for d in TENURE_DAYS
            },
        }
        for b in BONDS_YEC
    ]


# ---------------------------------------------------------------------------------------------------
# 4. filling and capturing the nine seats


def least_seating_bond_zat(bond_zat: int, tenure_blocks: int) -> int:
    """The least bond that outranks a seated member (×1.05) at the newcomer's first SNAP as ELIGIBLE
    (newcomer tenure BOND_MATURITY, the incumbent's advanced by as much)."""
    target = S.seat_rank(bond_zat, tenure_blocks + P.bond_maturity, True, P)
    unit = factor(P.bond_maturity) * BPS
    return max(target // unit + 1, P.bond_min_floor_zat)


def fill_rows() -> list[dict[str, object]]:
    """Seats held by ``m`` fresh 20,000-YEC bonders among ``n`` floor-sized (10,000-YEC) bonders of any
    tenure (the set holds 15 members, N_SLOTS 9 seats): the ranking is exact, so a 20,000 bond (×f(14 d)
    at eligibility) beats a floor bond at any tenure (×1.9 ×1.05)."""
    a20 = S.seat_rank(20_000 * COIN, P.bond_maturity, False, P)
    a5_max = S.seat_rank(FLOOR_YEC * COIN, P.seat_age_cap, True, P)
    out = []
    for m in range(0, P.attestor_seats + 1, 3):
        n = P.attestor_seats - m
        seats20 = min(m, P.n_slots)
        out.append(
            {
                "bonders_20k": m,
                "bonders_floor": n,
                "seats_20k": seats20,
                "seats_floor": min(n, P.n_slots - seats20),
            }
        )
    return [{"fresh_20k_beats_year_old_seated_floor": a20 > a5_max}, *out]


def capture_rows() -> list[dict[str, object]]:
    """Capital to take HARM_SEATS and N_SLOTS seats from an honest seated set of equal bonds ``b`` at
    tenure ``t``: per seat, the larger of the seating bond and (into a full set) the evicting bond."""
    out = []
    for b in BONDS_YEC:
        for d in INCUMBENT_DAYS:
            t = d * BLOCKS_PER_DAY
            seat = least_seating_bond_zat(b * COIN, t) / COIN
            per = max(seat * COIN, least_evicting_bond_zat(b * COIN, t)) / COIN
            out.append(
                {
                    "honest_bond_yec": b,
                    "honest_tenure_days": d,
                    "per_seat_yec": per,
                    "per_seat_open_set_yec": seat,
                    "harm_capital_open_set_yec": HARM_SEATS * seat,
                    "harm_capital_yec": HARM_SEATS * per,
                    "all_seats_capital_yec": P.n_slots * per,
                    "harm_capital_usd_at_ref": HARM_SEATS * per * REF_PRICE_USD,
                }
            )
    return out


# ---------------------------------------------------------------------------------------------------
# 5. the trace


def _key(i: int) -> bytes:
    return bytes([2]) + i.to_bytes(32, "big")


def trace(entrant_bond_yec: int, days: int = TRACE_DAYS) -> dict[str, object]:
    """15 honest floor-sized (10,000-YEC) members (9 at one year's tenure, seated; 6 at 30 days, waiting),
    all live; the entrant joins one bond of ``entrant_bond_yec`` a day. Each join into the full set runs
    EvictionVictim; seats are re-ranked daily with SeatRank (hysteresis on the seated) over the ELIGIBLE
    members (BOND_MATURITY after joining). Returns the day-by-day counts."""
    h0 = 3_100_000
    members: dict[bytes, dict[str, int | bool]] = {}
    for i in range(15):
        ten = 365 if i < 9 else 30
        members[_key(i)] = {
            "bond": FLOOR_YEC * COIN,
            "join": h0 - ten * BLOCKS_PER_DAY,
            "honest": True,
            "seated": i < 9,
        }
    events = []
    nk = 1000
    for day in range(1, days + 1):
        h = h0 + day * BLOCKS_PER_DAY
        cands = [S.SeatCandidate(k, int(m["bond"]), int(m["join"]), h) for k, m in members.items()]
        bond = entrant_bond_yec * COIN
        joined = False
        evicted = None
        if len(members) < P.attestor_seats:
            joined = True
        else:
            v = S.eviction_victim(cands, h, bond, P.attestor_liveness_min, P)
            if v is not None:
                evicted = "honest" if members[v]["honest"] else "entrant"
                del members[v]
                joined = True
        if joined:
            members[_key(nk)] = {"bond": bond, "join": h, "honest": False, "seated": False}
            nk += 1
        elig = [(k, m) for k, m in members.items() if h - int(m["join"]) >= P.bond_maturity]
        ranked = sorted(
            elig,
            key=lambda km: -S.seat_rank(int(km[1]["bond"]), h - int(km[1]["join"]), bool(km[1]["seated"]), P),
        )
        seated = {k for k, _ in ranked[: P.n_slots]}
        for k, m in members.items():
            m["seated"] = k in seated
        events.append(
            {
                "day": day,
                "joined": joined,
                "evicted": evicted,
                "honest_members": sum(1 for m in members.values() if m["honest"]),
                "entrant_members": sum(1 for m in members.values() if not m["honest"]),
                "entrant_seats": sum(1 for k in seated if not members[k]["honest"]),
            }
        )
    first_harm = next((e["day"] for e in events if int(e["entrant_seats"]) >= HARM_SEATS), None)
    return {
        "entrant_bond_yec": entrant_bond_yec,
        "events": events,
        "first_day_harm_seats": first_harm,
        "final": events[-1],
    }


def trace_cases() -> list[dict[str, object]]:
    year = least_evicting_bond_zat(FLOOR_YEC * COIN, 365 * BLOCKS_PER_DAY) / COIN
    return [trace(b) for b in (FLOOR_YEC * (BPS + P.evict_margin_bps) // BPS, int(year) + 1, 20_000)]


# ---------------------------------------------------------------------------------------------------
# 6. the bond minimum


def bond_min_rows() -> list[dict[str, float]]:
    out = []
    for c in (0, 500_000, 1_000_000, 1_800_000, 2_000_000, 3_600_000, 5_000_000, 10_000_000):
        b = S.bond_min_from_collateral_zat(c * COIN, P) / COIN
        out.append(
            {
                "collateral_yec": c,
                "bond_min_yec": b,
                "yed_usd_at_300pct_ref": c * REF_PRICE_USD / 3.0,
                "seats_total_yec": b * P.n_slots,
                "seats_share_of_collateral": (b * P.n_slots / c) if c else float("nan"),
            }
        )
    return out


def run() -> dict[str, object]:
    lo, hi = S.bond_neutral_collateral_zat(P)
    return {
        "constants": {
            "source_commit": S.SOURCE_COMMIT,
            "n_slots": P.n_slots,
            "attestor_seats": P.attestor_seats,
            "seat_age_unit": P.seat_age_unit,
            "seat_age_cap": P.seat_age_cap,
            "seat_margin_bps": P.seat_margin_bps,
            "evict_margin_bps": P.evict_margin_bps,
            "bond_maturity": P.bond_maturity,
            "bond_min_floor_yec": P.bond_min_floor_zat / COIN,
            "bond_min_cap_yec": P.bond_min_cap_zat / COIN,
            "bond_share_bps": P.bond_share_bps,
            "floor_binds_below_collateral_yec": (lo - 1) / COIN,
            "cap_binds_from_collateral_yec": hi / COIN,
            "ref_price_usd": REF_PRICE_USD,
            "harm_seats": HARM_SEATS,
        },
        "tenure": tenure_rows(),
        "time_to_seat": time_to_seat_rows(),
        "evict": evict_rows(),
        "fill": fill_rows(),
        "capture": capture_rows(),
        "trace": trace_cases(),
        "bond_min": bond_min_rows(),
    }


# ---------------------------------------------------------------------------------------------------
# report


def _y(x: float) -> str:
    return f"{x:,.0f}"


def render_markdown(res: Mapping[str, object]) -> str:
    C: Mapping[str, object] = res["constants"]  # type: ignore[assignment]
    L: list[str] = ["# Attestor seats — tenure, eviction and the bond minimum (D-Q-29..D-Q-31)\n"]
    L.append(
        f"Exact integer evaluation of the node's rules (`ybcal seats`; ycash-dd `q/attest` "
        f"`{str(C['source_commit'])[:9]}`, bit for bit through `ybcal.model.seats`). {C['attestor_seats']} "
        f"members, {C['n_slots']} seats; tenure factor u = {C['seat_age_unit']} blocks, ×1.9 at "
        f"{C['seat_age_cap']:,} blocks; seated ×{1 + int(C['seat_margin_bps']) / BPS:.2f}, eviction margin "  # type: ignore[call-overload]
        f"{int(C['evict_margin_bps']) / 100:.0f} %; maturity and exit delay {C['bond_maturity']:,} blocks; "  # type: ignore[call-overload]
        f"dollars at ${float(C['ref_price_usd']):.2f}/YEC (frozen 2026-10-04 reference). The price median is "
        "untouched: it keeps bond × min(age, AGE_CAP).\n"
    )
    L.append("## 1. Tenure factor\n")
    L.append(
        "| tenure (days) | f (Q16) | × | newcomer multiple to outrank a seated member at once | "
        "joiner multiple to evict |"
    )
    L.append("|---:|---:|---:|---:|---:|")
    for r in res["tenure"]:  # type: ignore[union-attr]
        L.append(
            f"| {r['days']} | {r['factor_q16']:,} | {r['factor']:.3f} | {r['seat_multiple_vs_seated']:.3f} | "
            f"{r['evict_multiple']:.3f} |"
        )
    L.append(
        "\nThe newcomer column: the bond multiple an ELIGIBLE newcomer needs at its first SNAP (its tenure "
        "is "
        "then BOND_MATURITY, ×1.413) to outrank a seated member of the row's tenure at that moment (×1.05 "
        "hysteresis). Against a year-old seat it is ×1.41, against a 14-day-old one ×1.05. "
        "The evict column is the joiner's multiple at tenure 0 (§3).\n"
    )
    L.append("## 2. Days from joining to a seat (newcomer = ratio × the incumbent's bond)\n")
    L.append("| ratio | " + " | ".join(f"incumbent {d} d" for d in INCUMBENT_DAYS) + " |")
    L.append("|---:|" + "---:|" * len(INCUMBENT_DAYS))
    for r in res["time_to_seat"]:  # type: ignore[union-attr]
        by = r["days_by_incumbent_tenure"]
        cells = " | ".join("never" if by[str(d)] is None else f"{by[str(d)]:.1f}" for d in INCUMBENT_DAYS)
        L.append(f"| {r['ratio']:g} | {cells} |")
    L.append(
        f'\n"never" = not within {SEAT_SCAN_DAYS // 365} years. An equal bond never displaces a seated '
        "member "
        "(the hysteresis); 1.05× never does either (strict >); from about 1.06× a newcomer is seated once "
        "both tenures saturate near a year. Seats change hands by bigger bonds or vacancies, not by "
        "waiting.\n"
    )
    L.append("## 3. Least bond that evicts a live member (YEC)\n")
    L.append("| member bond | " + " | ".join(f"{d} d" for d in TENURE_DAYS) + " |")
    L.append("|---:|" + "---:|" * len(TENURE_DAYS))
    for r in res["evict"]:  # type: ignore[union-attr]
        by = r["least_evicting_bond_yec_by_tenure"]
        L.append(f"| {_y(r['member_bond_yec'])} | " + " | ".join(_y(by[str(d)]) for d in TENURE_DAYS) + " |")
    L.append(
        "\nA member that is not live (no act within the set's liveness window) is evicted by any bond at or "
        "above the 10,000-YEC floor; an evicted member is not slashed and gets its bond back 14 days later.\n"
    )
    fill: list[Mapping[str, object]] = res["fill"]  # type: ignore[assignment]
    L.append("## 4. Filling and capturing the nine seats\n")
    L.append(
        f"A fresh 20,000-YEC bond outranks a year-old seated floor bond (10,000 YEC): "
        f"**{'yes' if fill[0]['fresh_20k_beats_year_old_seated_floor'] else 'no'}** (20,000 × 1.413 = 28,260 "
        "against 10,000 × 1.9 × 1.05 = 19,950). So the seats go to the biggest bonds: nine 20,000-YEC "
        "bonders fill all nine seats whatever the number of floor-sized members, and floor-sized bonders "
        "hold only the seats the bigger bonds leave.\n"
    )
    L.append("| 20,000-YEC bonders | 10,000-YEC bonders | seats to 20,000 | seats to 10,000 |")
    L.append("|---:|---:|---:|---:|")
    for r in fill[1:]:
        L.append(f"| {r['bonders_20k']} | {r['bonders_floor']} | {r['seats_20k']} | {r['seats_floor']} |")
    L.append(
        f"\nCapture capital (an entrant taking {C['harm_seats']} or {C['n_slots']} seats from an honest "
        "seated "
        "set of equal bonds, per seat the larger of the seating and the evicting bond):\n"
    )
    L.append(
        "| honest bond | honest tenure (d) | per seat, full set (YEC) | 7 seats (YEC) | 9 seats (YEC) | "
        "7 seats ($) | per seat, set with room (YEC) | 7 seats, room (YEC) |"
    )
    L.append("|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in res["capture"]:  # type: ignore[union-attr]
        L.append(
            f"| {_y(r['honest_bond_yec'])} | {r['honest_tenure_days']} | {_y(r['per_seat_yec'])} | "
            f"{_y(r['harm_capital_yec'])} | {_y(r['all_seats_capital_yec'])} | "
            f"${_y(r['harm_capital_usd_at_ref'])} | {_y(r['per_seat_open_set_yec'])} | "
            f"{_y(r['harm_capital_open_set_yec'])} |"
        )
    L.append(
        "\nA full set (15 members) must also be entered by eviction (§3, joiner at tenure 0), which binds "
        "over the seating bond at every tenure here; with room in the set only the seating bond binds (the "
        "entrant ranked at 14 days' tenure). For comparison, v3's flat 20,000-YEC minimum made 7 seats "
        "cost at least 140,000 YEC; the 10,000-YEC floor (owner, 2026-10-11) puts 7 seats from a year-old "
        "floor-sized full set back at about that (139,650 YEC).\n"
    )
    L.append("\n## 5. Trace: an entrant joining one bond a day into a full set of 10,000-YEC members\n")
    L.append(
        "| entrant bond | day 7: honest / entrant members, entrant seats | day 30 | first day with 7 seats |"
    )
    L.append("|---:|---|---|---:|")
    for t in res["trace"]:  # type: ignore[union-attr]
        ev = t["events"]
        d7, d30 = ev[6], ev[-1]
        L.append(
            f"| {_y(t['entrant_bond_yec'])} | {d7['honest_members']} / {d7['entrant_members']}, "
            f"{d7['entrant_seats']} | "
            f"{d30['honest_members']} / {d30['entrant_members']}, {d30['entrant_seats']} | "
            f"{t['first_day_harm_seats'] if t['first_day_harm_seats'] is not None else 'never'} |"
        )
    L.append(
        "\nThe 9 honest members are seated at one year's tenure, the 6 others waiting at 30 days; all live. "
        "EvictionVictim takes the least seat weight first: the youngest honest members, then the entrant's "
        "own newest bonds once they weigh least (a bond just above 10,000 × 1.05 only evicts honest members "
        "younger than about a day, then its own). Seats follow 14 days after each join.\n"
    )
    L.append("## 6. Bond minimum against vault collateral (D-Q-30)\n")
    L.append(
        "| YED vault collateral (YEC) | bondMin (YEC) | YED at 300 % ($, at the reference) | 9 seats (YEC) | "
        "÷ collateral |"
    )
    L.append("|---:|---:|---:|---:|---:|")
    for r in res["bond_min"]:  # type: ignore[union-attr]
        share = "—" if not r["collateral_yec"] else f"{r['seats_share_of_collateral']:.1%}"
        L.append(
            f"| {_y(r['collateral_yec'])} | {r['bond_min_yec']:,.2f} | ${_y(r['yed_usd_at_300pct_ref'])} | "
            f"{_y(r['seats_total_yec'])} | {share} |"
        )
    L.append(
        f"\nThe floor binds up to {_y(float(C['floor_binds_below_collateral_yec']))} YEC of collateral "  # type: ignore[arg-type]
        f"(${_y(float(C['floor_binds_below_collateral_yec']) * REF_PRICE_USD / 3)} of YED at 300 % and "  # type: ignore[arg-type]
        f"${REF_PRICE_USD:.2f}), the cap from {_y(float(C['cap_binds_from_collateral_yec']))} YEC. "  # type: ignore[arg-type]
        "The minimum gates entry only: seats are decided by the ranking above, so in a contested set the "
        "seated bonds sit well above it.\n"
    )
    return "\n".join(L) + "\n"


def configure_seats(p: argparse.ArgumentParser) -> None:
    p.add_argument("--out", default=None, help="write seats.json and seats.md here")


def cli_seats(args: argparse.Namespace) -> int:
    res = run()
    md = render_markdown(res)
    if args.out:
        d = Path(args.out)
        d.mkdir(parents=True, exist_ok=True)
        (d / "seats.json").write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
        (d / "seats.md").write_text(md)
    sys.stdout.write(md)
    return 0

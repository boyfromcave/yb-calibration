"""ybcal seats: the attestor seat study (quantum D-Q-29..D-Q-31) on the exact node rules."""

from __future__ import annotations

import json

from ybcal import cli
from ybcal.studies import attest_seats as A
from ybcal.units import BLOCKS_PER_DAY, COIN


def test_tenure_and_eviction_thresholds():
    rows = {r["days"]: r for r in A.tenure_rows()}
    assert rows[0]["factor_q16"] == 65_536 and rows[365]["factor_q16"] == rows[730]["factor_q16"] == 124_518
    assert rows[14]["factor_q16"] == 92_596  # the newcomer's factor at BOND_MATURITY
    # a live 10,000-YEC (floor) member: 10,500 YEC evicts it on day 0, ~19,950 YEC at a year
    # (10,000 x 124,518/65,536 x 1.05: the Q16 factor at the cap is 1.89999, not 1.9)
    assert A.least_evicting_bond_zat(10_000 * COIN, 0) == 10_500 * COIN + 1
    assert (
        A.least_evicting_bond_zat(10_000 * COIN, 365 * BLOCKS_PER_DAY)
        == 10_000 * COIN * 124_518 * 10_500 // (65_536 * 10_000) + 1
    )
    # never below the floor (review B2)
    assert A.least_evicting_bond_zat(1 * COIN, 0) == 10_000 * COIN


def test_time_to_seat_hysteresis():
    assert A.days_to_seat(1.0, 0) is None and A.days_to_seat(1.05, 365) is None
    assert A.days_to_seat(1.5, 365) == 14.0  # at eligibility: 1.5 x 1.413 > 1.9 x 1.05
    d = A.days_to_seat(1.1, 365)
    assert d is not None and 150 < d < 250  # both tenures grow; the newcomer catches up within a year


def test_fill_and_capture():
    fill = A.fill_rows()
    assert fill[0]["fresh_20k_beats_year_old_seated_floor"] is True
    by_m = {r["bonders_20k"]: r for r in fill[1:]}
    assert by_m[9]["seats_20k"] == 9 and by_m[9]["seats_floor"] == 0 and by_m[3]["seats_floor"] == 6
    cap = {(r["honest_bond_yec"], r["honest_tenure_days"]): r for r in A.capture_rows()}
    # against year-old 20,000-YEC seats: 39,900 YEC a seat (eviction binds: 20,000 x 1.9 x 1.05)
    assert round(cap[(20_000, 365)]["per_seat_yec"]) == 39_900
    assert cap[(10_000, 0)]["per_seat_yec"] == 10_500.00000001
    # the owner's 10,000-YEC floor: 7 seats from a year-old floor-sized full set ~ v3's 140,000 YEC
    assert round(cap[(10_000, 365)]["harm_capital_yec"]) == 139_650


def test_trace_and_bond_min():
    low, year, big = A.trace_cases()
    assert low["first_day_harm_seats"] is None and low["final"]["honest_members"] == 15
    assert big["first_day_harm_seats"] == 21 and big["final"]["entrant_seats"] == 9
    assert year["final"]["honest_members"] == 0  # just above 10,000 x 1.9 x 1.05 evicts a year-old seat
    bm = {r["collateral_yec"]: r["bond_min_yec"] for r in A.bond_min_rows()}
    assert bm[0] == bm[1_800_000] == 10_000 and bm[3_600_000] == bm[10_000_000] == 20_000
    assert bm[2_000_000] == 11_111.11111112  # ceil in zat


def test_cli_writes_report(tmp_path):
    assert cli.main(["seats", "--out", str(tmp_path)]) == 0
    res = json.loads((tmp_path / "seats.json").read_text())
    from ybcal.model import seats

    # the vendored pin (the rules of f07a9aef6, unchanged)
    assert res["constants"]["source_commit"] == seats.SOURCE_COMMIT
    assert res["constants"]["floor_binds_below_collateral_yec"] == 1_800_000
    md = (tmp_path / "seats.md").read_text()
    assert "## 4. Filling and capturing the nine seats" in md and "**yes**" in md

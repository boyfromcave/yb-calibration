# Attestor seats — tenure, eviction and the bond minimum (D-Q-29..D-Q-31)

Exact integer evaluation of the node's rules (`ybcal seats`; ycash-dd `q/attest` `f07a9aef6`, bit for bit through `ybcal.model.seats`). 15 members, 9 seats; tenure factor u = 1152 blocks, ×1.9 at 420,480 blocks; seated ×1.05, eviction margin 5 %; maturity and exit delay 16,128 blocks; dollars at $0.40/YEC (frozen 2026-10-04 reference). The price median is untouched: it keeps bond × min(age, AGE_CAP).

## 1. Tenure factor

| tenure (days) | f (Q16) | × | newcomer multiple to outrank a seated member at once | joiner multiple to evict |
|---:|---:|---:|---:|---:|
| 0 | 65,536 | 1.000 | 0.743 | 1.050 |
| 1 | 72,462 | 1.106 | 0.822 | 1.161 |
| 7 | 86,314 | 1.317 | 0.979 | 1.383 |
| 14 | 92,596 | 1.413 | 1.050 | 1.484 |
| 30 | 99,850 | 1.524 | 1.132 | 1.600 |
| 90 | 110,611 | 1.688 | 1.254 | 1.772 |
| 180 | 117,482 | 1.793 | 1.332 | 1.882 |
| 365 | 124,518 | 1.900 | 1.412 | 1.995 |
| 730 | 124,518 | 1.900 | 1.412 | 1.995 |

The newcomer column: the bond multiple an ELIGIBLE newcomer needs at its first SNAP (its tenure is then BOND_MATURITY, ×1.413) to outrank a seated member of the row's tenure at that moment (×1.05 hysteresis). Against a year-old seat it is ×1.41, against a 14-day-old one ×1.05. The evict column is the joiner's multiple at tenure 0 (§3).

## 2. Days from joining to a seat (newcomer = ratio × the incumbent's bond)

| ratio | incumbent 0 d | incumbent 30 d | incumbent 180 d | incumbent 365 d | incumbent 730 d |
|---:|---:|---:|---:|---:|---:|
| 1 | never | never | never | never | never |
| 1.05 | never | never | never | never | never |
| 1.06 | 14.0 | 245.3 | 324.4 | 324.4 | 324.4 |
| 1.1 | 14.0 | 45.8 | 206.8 | 206.8 | 206.8 |
| 1.25 | 14.0 | 14.0 | 30.5 | 48.9 | 48.9 |
| 1.5 | 14.0 | 14.0 | 14.0 | 14.0 | 14.0 |
| 2 | 14.0 | 14.0 | 14.0 | 14.0 | 14.0 |
| 4 | 14.0 | 14.0 | 14.0 | 14.0 | 14.0 |

"never" = not within 3 years. An equal bond never displaces a seated member (the hysteresis); 1.05× never does either (strict >); from about 1.06× a newcomer is seated once both tenures saturate near a year. Seats change hands by bigger bonds or vacancies, not by waiting.

## 3. Least bond that evicts a live member (YEC)

| member bond | 0 d | 1 d | 7 d | 14 d | 30 d | 90 d | 180 d | 365 d | 730 d |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 10,000 | 10,500 | 11,610 | 13,829 | 14,835 | 15,998 | 17,722 | 18,823 | 19,950 | 19,950 |
| 15,000 | 15,750 | 17,414 | 20,743 | 22,253 | 23,997 | 26,583 | 28,234 | 29,925 | 29,925 |
| 20,000 | 21,000 | 23,219 | 27,658 | 29,671 | 31,995 | 35,444 | 37,645 | 39,900 | 39,900 |

A member that is not live (no act within the set's liveness window) is evicted by any bond at or above the 10,000-YEC floor; an evicted member is not slashed and gets its bond back 14 days later.

## 4. Filling and capturing the nine seats

A fresh 20,000-YEC bond outranks a year-old seated floor bond (10,000 YEC): **yes** (20,000 × 1.413 = 28,260 against 10,000 × 1.9 × 1.05 = 19,950). So the seats go to the biggest bonds: nine 20,000-YEC bonders fill all nine seats whatever the number of floor-sized members, and floor-sized bonders hold only the seats the bigger bonds leave.

| 20,000-YEC bonders | 10,000-YEC bonders | seats to 20,000 | seats to 10,000 |
|---:|---:|---:|---:|
| 0 | 15 | 0 | 9 |
| 3 | 12 | 3 | 6 |
| 6 | 9 | 6 | 3 |
| 9 | 6 | 9 | 0 |
| 12 | 3 | 9 | 0 |
| 15 | 0 | 9 | 0 |

Capture capital (an entrant taking 7 or 9 seats from an honest seated set of equal bonds, per seat the larger of the seating and the evicting bond):

| honest bond | honest tenure (d) | per seat, full set (YEC) | 7 seats (YEC) | 9 seats (YEC) | 7 seats ($) | per seat, set with room (YEC) | 7 seats, room (YEC) |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 10,000 | 0 | 10,500 | 73,500 | 94,500 | $29,400 | 10,500 | 73,500 |
| 10,000 | 30 | 15,998 | 111,984 | 143,979 | $44,794 | 11,745 | 82,214 |
| 10,000 | 180 | 18,823 | 131,759 | 169,404 | $52,703 | 13,406 | 93,844 |
| 10,000 | 365 | 19,950 | 139,650 | 179,549 | $55,860 | 14,120 | 98,839 |
| 10,000 | 730 | 19,950 | 139,650 | 179,549 | $55,860 | 14,120 | 98,839 |
| 15,000 | 0 | 15,750 | 110,250 | 141,750 | $44,100 | 15,750 | 110,250 |
| 15,000 | 30 | 23,997 | 167,976 | 215,969 | $67,190 | 17,617 | 123,321 |
| 15,000 | 180 | 28,234 | 197,638 | 254,106 | $79,055 | 20,110 | 140,767 |
| 15,000 | 365 | 29,925 | 209,474 | 269,324 | $83,790 | 21,180 | 148,258 |
| 15,000 | 730 | 29,925 | 209,474 | 269,324 | $83,790 | 21,180 | 148,258 |
| 20,000 | 0 | 21,000 | 147,000 | 189,000 | $58,800 | 21,000 | 147,000 |
| 20,000 | 30 | 31,995 | 223,968 | 287,959 | $89,587 | 23,490 | 164,428 |
| 20,000 | 180 | 37,645 | 263,517 | 338,808 | $105,407 | 26,813 | 187,689 |
| 20,000 | 365 | 39,900 | 279,299 | 359,099 | $111,720 | 28,240 | 197,678 |
| 20,000 | 730 | 39,900 | 279,299 | 359,099 | $111,720 | 28,240 | 197,678 |

A full set (15 members) must also be entered by eviction (§3, joiner at tenure 0), which binds over the seating bond at every tenure here; with room in the set only the seating bond binds (the entrant ranked at 14 days' tenure). For comparison, v3's flat 20,000-YEC minimum made 7 seats cost at least 140,000 YEC; the 10,000-YEC floor (owner, 2026-10-11) puts 7 seats from a year-old floor-sized full set back at about that (139,650 YEC).


## 5. Trace: an entrant joining one bond a day into a full set of 10,000-YEC members

| entrant bond | day 7: honest / entrant members, entrant seats | day 30 | first day with 7 seats |
|---:|---|---|---:|
| 10,500 | 15 / 0, 0 | 15 / 0, 0 | never |
| 19,950 | 8 / 7, 0 | 0 / 15, 9 | 21 |
| 20,000 | 8 / 7, 0 | 0 / 15, 9 | 21 |

The 9 honest members are seated at one year's tenure, the 6 others waiting at 30 days; all live. EvictionVictim takes the least seat weight first: the youngest honest members, then the entrant's own newest bonds once they weigh least (a bond just above 10,000 × 1.05 only evicts honest members younger than about a day, then its own). Seats follow 14 days after each join.

## 6. Bond minimum against vault collateral (D-Q-30)

| YED vault collateral (YEC) | bondMin (YEC) | YED at 300 % ($, at the reference) | 9 seats (YEC) | ÷ collateral |
|---:|---:|---:|---:|---:|
| 0 | 10,000.00 | $0 | 90,000 | — |
| 500,000 | 10,000.00 | $66,667 | 90,000 | 18.0% |
| 1,000,000 | 10,000.00 | $133,333 | 90,000 | 9.0% |
| 1,800,000 | 10,000.00 | $240,000 | 90,000 | 5.0% |
| 2,000,000 | 11,111.11 | $266,667 | 100,000 | 5.0% |
| 3,600,000 | 20,000.00 | $480,000 | 180,000 | 5.0% |
| 5,000,000 | 20,000.00 | $666,667 | 180,000 | 3.6% |
| 10,000,000 | 20,000.00 | $1,333,333 | 180,000 | 1.8% |

The floor binds up to 1,800,000 YEC of collateral ($240,000 of YED at 300 % and $0.40), the cap from 3,600,000 YEC. The minimum gates entry only: seats are decided by the ranking above, so in a contested set the seated bonds sit well above it.


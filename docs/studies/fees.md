# FEES — Option D protocol fee, the $100,000 `maxMint` and the supply cap

Code: `src/ybcal/studies/fees_option_d.py` (quantum plan D-Q-23 / D-Q-24, §9 items 3 and 5). CLI:
`ybcal fees [--today YYYY-MM-DD] [--policy FILE] [--out DIR]`. Tests: `tests/studies/test_fees_option_d.py`.
Report: [docs/reports/2026-10-fees/](../reports/2026-10-fees/README.md). Inputs: the decisions in
`docs/plans/yellowback-quantum-plan.md` §2 (D-Q-23, D-Q-24), the byte sizes of `pq_growth`, the frozen
2026-10-04 order books (`data/local/frozen-20261004/depth.csv`, summarised into `BID_DEPTH_USD`), the
policy's adoption scenarios and attestor-cost assumptions (`policy/default.toml` `[fees]`,
`[adoption]`), and the node constants of ycash-dd `upgrade/quantum-vaults` (`wt/q-dd` d41026209).

An analytic study like `pq`: no simulation, no sweep, no data pull; it runs in under a second. It
changes no registry parameter. Its output is a parameter set for the owner's fee decision (the bands,
floor, early fee, $/KB targets, the network-fee floor and cap, and the dollar restatements of
`bondMin` / `residualMinZat`) plus the supply-cap and concentration facts D-Q-24 asked for.

## Method

**Supply.** The emission schedule of ref/ycash v4.5.0 (`GetBlockSubsidy`, `Consensus::Params::Halving`):
12.5 YEC with Zcash's 20,000-block slow start, pre-Blossom halving interval 840,000, Ycash's Blossom at
1,100,000 (the per-block subsidy halves with the 75 s spacing), post-Blossom interval 1,680,000. Halving
heights 850,000 / 1,100,000 / 2,280,000 / 3,960,000. Today's height is the policy's `release_tip` plus
1,152 blocks a day. Two caps are computed: the owner's framing (15 % of the circulating market cap) and
the rule's (MINT-6: `SupplyCapCents(issuedZat, …)` where `issuedZat` is the coinbase since the start
height — `state.cpp:1456`, from an empty `Snapshot()`; soft above `recapRatioBps`, so class C bypasses).

**Concentration.** The G3 claimant test (`sim.agents.slippage_bps`: base 100 bps plus 200 bps per
`depth_usd` sold, capped at 99.99 %; `claimant_min_profit_bps` 200) against the frozen bid books: a
claim at θ = 125 % executes only if the claimant nets 2 % of the debt after selling the seized YEC. The
closed form gives the largest executable debt per depth band; one vault at 25 % / 50 % of the cap is
compared with the same debt over 100 vaults (executability, sale size vs the policy's placeholder p10
daily volume, and the share one owner's absence or lost key puts in the claim path).

**Fees.** `protocol_fee_usd` is the marginal band fee with the $1 floor; it is converted to YEC at
`pMint`. Mints pay it to the pool plus `attestFeeBps` (50 %) of it to the attestors (AFEE-1, on top);
owner redeems pay the pool only (`state.cpp:591`). Today's rule is `max(0.5 YEC, 0.15 % × collateral)`
with the collateral at the class base ratio. Revenue uses three mint-size models — supply-consistent
(the scenario's `yed_supply_usd` over 180-day mean lives), log-uniform to H-12's $2,500, log-uniform to
D-Q-24's $100,000 — and a whale case of ten $100k vaults a year; per-pool and per-seat figures divide by
the 5 pools of the frozen `pool-names.csv` and the 6 seats (`mSelect` + `kSlack`), and are compared with
the policy's $50 floors and G8's bond opportunity cost (20,000 YEC × price × 5 % / 12).

**Network fee.** `clamp(target $/KB × size at the attested price, floor × relay, cap)`, per path of
`pq_growth.paths` (variants `pq_p2pkh` and `falcon`), with the cap-bind price and the price above which
the $/KB target pays fewer zat per byte than a 245-byte P2PKH payment (4.08 zat/B).

**Robustness.** The fee in YEC fixed at `pMint` after ±70 %; the $1 floor; `bondMin` and
`residualMinZat` restated in dollars with a YEC floor. **Sensitivity** ±50 % on each band rate and each
$/KB target.

**Attestor economics** (`ybcal fees --attestors`, quantum plan D-Q-25 red flag 3; report
[attestors.md](../reports/2026-10-fees/attestors.md)). The adoption tiers made explicit (the policy's
low / mid / high with `pq_growth`'s transfers and claims, plus `whale` = mid with ten $100k mints a year)
× two mint-size models (supply-consistent; log-uniform to $100k) → AFEE-1 revenue per seat per month and
year at shares 50 / 75 / 100 % (price-invariant: the fee is dollars at `pMint`); return on bond per year
under 20,000 YEC and the dollar-pinned $8,000 / $2,000 bonds (2,000-YEC floor) at $0.40 / $1 / $5 / $10,
against the policy's 5 %/yr cost of capital; the break-even mints/day in closed form
(`apr × bond × seats / (365 × fee × share)`); and the capture side — seven seats' worth of bond against
the D-Q-25 cap (15 % of circulating supply × price) and against D-RD-ATT-4's old yardstick (one year of
MINT-6 growth) — with one recommendation.

## Assumptions to re-check

- Mint sizes: no real data; three models are reported side by side.
- Depth: three venues' visible bid books on 2026-10-04 (87 snapshots). 24 h volume is the policy's
  placeholder `p10_daily_volume_usd` = $25,000.
- Early-redeem volumes are unknown; early fees are left out of the revenue.
- The attestor share stays `attestFeeBps` of the (new) FEE-1, as the module computes it today.

## Release constants as built (2026-10-11)

`RELEASE_CONSTANTS` / `RELEASE` in `fees_option_d.py` record the fee-line constants as implemented on ycash-dd
`q/fees` @ `430002b16` (quantum plan D-Q-23..D-Q-26; the ycash6 port must carry the same values), and
`test_release_constants_are_the_recommendation_as_built` holds them to this study's recommendation:

| Constant | Value | Source |
|---|---|---|
| `maxMint` | $100,000 | D-Q-24 (H-12's $2,500 reversed on the quantum line) |
| Protocol fee | 1.0 % ≤ $1k, 0.5 % to $10k, 0.1 % above; $1 floor per action; on the debt at `pMint` | D-Q-23 FEE-1′ |
| Early-redeem fee | 5 / 2.5 / 1 % of the debt at `pMint` | IT-9′ |
| Attestor share | 50 % of the protocol fee | AFEE-1′ (D-Q-25 recommendation kept) |
| Network fee | $0.02/KB plain PQ, $0.05/KB vault / Yellowback, at the max of the tip's window medians (none under a halt) | D-Q-26, review M-1 |
| Network floor | max(5,000 zat/KB, `-pqfeerate`) | D-Q-23a, review A-7 |
| Network cap | min(max(`-maxtxfee` 0.1 YEC, $2 at the price), `-yellowbackmaxtxfee` 10 YEC); plain PQ transfers share it | D-Q-23a, review M-1, D-Q-26 |
| Residual | $0.48 at `pMint`, floor 100,000 zat | D-Q-23a RED-5′ |
| Supply cap | 15 % of circulating supply × `xMint(R)` | D-Q-25 MINT-6 |
| Per-vault share | 10 % of the cap, floor $10,000 (per vault, not per owner) | D-Q-23a MINT-6b |
| `bondMin` | clamp(ceil(YED vault collateral × 500 bps ÷ 9), 5,000 YEC, 20,000 YEC) from the snapshot before the join, no price; exit any time, the bond returns 16,128 blocks (14 days) after exit, `bondMinLock` 0 — as built on ycash-dd `q/attest` `4bb710b4c` (this study's $8,000 pinned bond was superseded) | D-Q-30, D-Q-31 |

The parameter registry (`src/ybcal/params/registry.py`) is still pinned to ycash6 `7702d22` (the v3 values:
`feeMin` / `feeBps`, `maxMint` $10,000, `attestFeeBps` 2,500, `bondMin` 20,000 YEC, `bondMinLock` 420,480) and is checked against
that source by `ybcal params check`; moving it to the fee line is a re-pin onto the merged node (new fields:
the bands, floor, `maxVaultShareBps`, `vaultShareFloorCents`, `residualMinCents`, and from D-Q-29/30 `bondMinFloorZat`,
`bondMinCapZat`, `bondShareBps`, `seatAgeUnit`, `seatAgeCap`, `seatMarginBps`, `evictMarginBps`, `attestorSeats`,
`attestorLivenessMin`; `feeMin` / `feeBps` / `bondMin` gone),
not done here. No policy TOML on this branch encodes release values (`policy/harden-2026-10.toml` exists only
on `harden/yellowback`, which keeps H-12's $2,500).

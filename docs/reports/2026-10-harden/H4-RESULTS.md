# Review: H4 complete — all 20 hardening runs (standard, windows, stress)

2026-10-07 · read-only review by agent h4-review2 · policy `policy/harden-2026-10.toml` · data `data/local/frozen-20261004`
· sweeps `.work/robust/harden-std/` (6 × standard: full, last365 × seeds 03/04/05, bootstrap), `harden-quick-windows/`
(6 × quick: 2021-22, 2025-26 × 3 seeds, bootstrap), `harden-quick-stress/` (8 × quick: full, last365 × regime, martingale
× seeds 03/04). All 20 finished with a result table; every run reads **Lock-ready: no** (each `report.md` §1, §7). Paths
below are relative to `.work/robust/`; "§3.N" is the parameter section of a run's `report.md`; the H4-a reduced run is
`docs/reports/2026-10-harden/README.md`; the two-seed review is `harden-std/REVIEW-full-history-2-seeds.md`.

**Short version.** The worse-window rule locks `baseRatioBps[0]` at **192,500 (1,925 %)**, set by one seed of the last-365
window with 0.006 pp to spare. Every other parameter's verdict is the same in all 20 runs except `signalWindow`,
`dormancyMinBundles` (CHANGE everywhere, to seed-dependent values), the two sigma pins (evidence argues against them in
15/20 runs) and `divergenceBps` (BLOCKED 18/20). The four contradictions of the reduced run — fee split, 300 % halt,
`maxMint`, divergence recall — fail in every bootstrap run and are worse, not better, under the stress models. The stress
models say class A cannot meet 0.5 % at any ratio in the lattice on 7 of 8 runs.

## 1. The worse-window lock: 192,500

Rule (plan §3 H-3, policy `ratio_lock_rule = "worse-window"`, `harden-2026-10.toml:227`): per window the smallest lattice
value whose P(bad debt at claim opening) ≤ 0.5 % in **every** run of that window; the lock is the larger need
(`harden-std/robust.md`, "Worse-window lock rule"). Aggregate = worst ensemble member (§3.22 "aggregate: worst").

| Window (standard, bootstrap) | Need per seed 03 / 04 / 05 | Window need | P(bad) at 725 % | P(bad) at 192,500 | Source |
|---|---|---|---|---|---|
| full history | 72,500 / 72,500 / 72,500 (KEEP) | **72,500** | 0.458 / 0.404 / 0.323 % | 0.013 / 0.012 / 0.015 % | §3.22; `evidence/g3/g3_tradeoff.csv` |
| last 365 days | 162,500 / 170,000 / 192,500 (CHANGE) | **192,500** | 3.79 / 4.74 / 4.85 % | 0.303 / 0.353 / **0.494 %** | same |
| **lock** | | **192,500 bps (1,925 %)** | | margin 0.006 pp (s05) | `harden-std/robust.md` |

The seed spread on the last-365 need is 30,000 bps (162,500–192,500); the lock is the s05 value, which clears 0.5 % by
0.0056 pp (0.4944 %, `last365__bootstrap__s20261005/evidence/g3/g3_tradeoff.csv`). A fourth seed could move the lock to
the next lattice point (195,000+) or leave it; the margin is noise-sized. The `history` member (the real path) is 0 at every
ratio in every run; the need is entirely the bootstrap members' (§3.22 `per_member_current`). The full-history leg's
margin at 725 % is 0.04–0.18 pp; at the lock it is three orders inside.

What the other windows say about 192,500 (`harden-quick-windows/`, quick budget, so indicative):

- **2021-22** (the crash year): class A needs only 35,000 / 40,000 / 42,500 (seeds 03/04/05; `robust.csv`); P(bad) at
  725 % is 0.02–0.04 %, and 0 at 192,500 in all three seeds. The lock is 4.5–5.5× what this window needs.
- **2025-26** (the last year with daily starts): needs 112,500 / 142,500 / 122,500; P(bad) at 725 % 2.28 / 2.97 / 2.43 %,
  and at 192,500 0.105 / 0.173 / 0.100 % (`g3_tradeoff.csv`). The lock covers this window 3–5× over.
- The windows sweep's own lock table prints "— (no finished run with a result table)" because it looks for `full`/`last365`
  (`harden-quick-windows/robust.md`, last table) — cosmetic, §5 item 5.

What the stress models say (`harden-quick-stress/`, §3.22 of each run; `g3_tradeoff.csv`):

| Model, window | Seeds 03 / 04: recommended (verdict) | P(bad) at 192,500 | P(bad) at the top of the lattice (230,000) |
|---|---|---|---|
| regime, full | 230,000 (CHANGE env) / 180,000 (CHANGE) | 1.01 % / 0.41 % | 0.73 % / 0.25 % |
| regime, last365 | 230,000 / 225,000 (both env) | 1.2 % / 1.5 % exposure at the chosen value | 1.2 / 1.5 % (bad vault short 33–34 %) |
| martingale, full | 215,000 / 222,500 (both env) | 24.2 % / 23.9 % | 19.8 % / 18.7 % |
| martingale, last365 | 200,000 / 205,000 (both env) | 44.0 % / 38.0 % | 38.6 % / 32.9 % |

In 7 of 8 stress runs the G3 rule reports "no value can meet bad_debt_A in this environment" (design note G3-DN1: no
liquidation before `lockHeight + grace`, so the base ratio alone carries the whole term's drawdown) and picks least harm.
Only regime/full/s04 finds a feasible value (180,000, which 192,500 covers). The martingale model (drift removed) puts
class A at 20–44 % bad debt at any ratio: that is the model saying a 30–90-day zero-drift YEC with ≈ 160–180 %/yr σ cannot be
over-collateralised into 0.5 % within a 23× lattice. Gate G-7 asks only that regime/martingale be *reported*; they are
reported here, and they report that the lock is a bootstrap-model result.

## 2. Every parameter across the 20 runs

Plan = the hardening plan's value (§3 H-n). "20/20" = identical verdict in every run. Constraint readings per
`robust-summary.csv` of each sweep and §3.N of the runs; gate thresholds from plan §7 and `policy/harden-2026-10.toml`.

| Parameter | Plan | Verdict (runs) | Stability | Gate / margin |
|---|---|---|---|---|
| `baseRatioBps[0]` | ≥ 72,500 by H-3 | KEEP 3, CHANGE 17; lock **192,500** | window-sensitive, seed-noise; env-limited 7/8 stress | G-7: full 0.013–0.015 %, last365 0.30–0.49 % (margin 0.006 pp); regime 0.4–1.5 %, martingale 24–44 % at the lock |
| `claimThresholdBps` | 12,500 | KEEP (pin) 20/20 | stable; `claim_incentive` fails in regime/full (p10 margin 199.8 / 147.4 bps vs 200 floor, §3.15) | bootstrap p10 margin 359–961 bps; sell-model claimant never shows up (mean ≈ −2,000 bps) |
| `sigmaRefBps` | 18,000 | KEEP (pin) 20/20 | evidence argues against in 15/20: 16,500–17,500 (last365, 2025-26), 24,500–29,500 (2021-22), 8,500–23,000 (stress) (§3.6) | M14 ratio 0.49–1.65× vs band [1, 1.5] |
| `sigmaMultMaxBps` | 47,500 | KEEP (pin) 20/20 | p99 not covered in 13/20 → evidence 50,000; 2021-22 → 30,000–32,500 (§3.7) | — |
| `feeBps` / `attestFeeBps` | 15 / 5,000 | KEEP (pin) 20/20, **none-feasible 20/20** | `fee_share` fails 17/20 (passes only 2021-22), `attestor_revenue` fails 6/20 (full, 2021-22) | §3 below |
| `globalRatioHaltBps` / `recapRatioBps` | 30,000 / 60,000 | KEEP (pin) 20/20, none-feasible; recap listed BLOCKED 20/20 | `halt_sys_bad` fails 20/20; `halt2_timely` also fails 2025-26/s03 | §3 below |
| `maxMint` | $2,500 | KEEP (pin) 20/20, env-limited (G9-ENV-1) | identical numbers 20/20 | §3 below |
| `supplyCapBps` | 1,500 | KEEP (pin) 20/20 | stable; depth bound uninformative (no cap-bound debt with B/C off, §3.28) | class-A worst-day demand $1,997 vs $70 budget (design note 15) |
| `attestArmMin` / `attestArmDelay` | 7 / 1,152 | KEEP (pin) 20/20 | stable, judgement provenance | every check passes (§3.43) |
| `classMin/Max[0..2]` | A 30–90 d; B, C empty | KEEP (pin) 20/20 | stable | invariants pass (§7 "every invariant") |
| `grace` / `abandonBlocks` | 34,560 / 34,560 (W21) | KEEP (pin) 20/20 | stable | grace binds the abandon lower bound (§3.26–27) |
| `valveBlocks` | 6 (H-6 retired) | KEEP (pin) 20/20, none-feasible | identical 20/20 | `valve_minority` fails: attack_trip 1, minority_trip 0.0092; evidence → 12 (§3.14). G-6 retired |
| `signalWindow` (+ L3 thresholds) | 2,592 (1,944/1,556/1,296/1,556) | **CHANGE 20/20** to 4,032 (s03 std), 3,456 (s04 std), 3,744 (s05 std + all 14 quick runs), 3,168 (2 quick s05) | seed-unstable at standard; quick budget collapses to 3,744 | 2,592 meets every constraint (feasible 20/20); moves on false-halt only. Spurious lock 0.875–0.984 at any value (report-only, H-7 retired) |
| `dormancyMinBundles` | not decided | **CHANGE 20/20**: 12 (16 runs), 10 (4 runs: s05 std, s05 quick-windows) | mostly stable | false-eject/yr 10⁻¹⁴ → 10⁻¹²–10⁻¹⁰; dead-detect p95 22,752 → 16,128 blocks (§3.62) |
| `divergenceBps` | not decided | **BLOCKED 18/20**, KEEP 2 (last365 regime/martingale s04) | recall 65–96 % | §3 below |
| `emergencyRatioBps` / `emergencyPersist` | — | PROVISIONAL 20/20 (synthetic) | stable; stress sweep flags `bad_debt_A`/`claim_incentive` at 10,500 (8/8) | uncovered debt 28.9–32.1 % (§3.25) |
| `deviationBps` | — | KEEP 19/20; **BLOCKED** in 2021-22/s05 (`false_penalty_stale`, §3.34) | one-run outlier at quick budget | — |
| G1 windows (`pFast/Mid/Slow`), fills | — | KEEP (env) 20/20 | identical | NO_PRICE 1,203 h/yr vs 6 h/yr policy; G-3's 24 h/30 d is not read here (G1-ENV-1/2) |
| `walletConfirmations` | — | KEEP (env) 20/20 | identical | q = 30 % catch-up 8.1e-04 at z = 24; majority pool reorgs any depth (G9-ENV-2) |
| remaining 40 G2/G4/G6/G8/G9 values | as 2026-10 set | KEEP 20/20 | stable, 19 on synthetic/judgement provenance | §7 check 2 FAIL in every run for that reason |

Totals per run: standard full KEEP 63 / CHANGE 6 / PROV 2 / BLOCKED 1; standard last365 62/7/2/1; quick runs 59–62 KEEP,
7–10 CHANGE, BLOCKED 0–2 (§1 of each report). The delta from the shipped set is `signalWindow` and its four L3 thresholds,
`dormancyMinBundles`, and (last365 and all quick runs) `baseRatioBps[0]`.

Gates as the 20 runs read them: **G-1 red** (lock-readiness fails 3 checks in 20/20: 19 synthetic/judgement values; BLOCKED
recap + divergence; `valveBlocks` missing its exposure note — §7 of every report; spread-log and depth-series lengths not
checked). **G-2**: invariants pass 20/20; devnet differential and patch-applies skipped 20/20 (no ycashd, no ycash6 path,
§6/§7). **G-7**: passes on the full and last-365 standard runs at 192,500 (0.015 %, 0.494 %); reported on regime (0.4–1.5 %)
and martingale (24–44 %). G-3/4/5/8/10 are not ybcal readings; G-6 retired (upgrade plan §7).

## 3. What failed everywhere — the numbers

**Fee split 15 / 5,000 bps (H-4).** `fee_share` (ARMED round trip on a `minMint` class-A vault, cap 2.25 %,
`max_fee_share_small`, policy line 107) and `attestor_revenue` (≥ $50 per seat per month) — §3.38 and §3.42 of every run:

| Sweep, window | Ratio the fee study ran at | fee share (ARMED / unarmed) | attestor $/seat/month | pool $/month |
|---|---|---|---|---|
| std full (3 seeds) | 72,500 | **2.72 %** / 2.17 % | **$43.0 / 42.2 / 43.4** | $407 / 390 / 425 |
| std last365 (3 seeds) | 162,500 / 170,000 / 192,500 | **6.09 / 6.38 / 7.22 %** | $96 / 100.5 / 116.1 (pass) | $1,088–1,360 |
| quick 2021-22 | 35,000–42,500 | 1.31–1.59 % (pass) | **$17.3 / 19.5 / 22.5** | $161–193 |
| quick 2025-26 | 112,500–142,500 | **4.22 / 5.34 / 4.59 %** | $62–80 (pass) | $687–919 |
| stress (8 runs) | 180,000–230,000 | **6.75–8.62 %** | $74–133 (pass) | $663–1,612 |

FEE-1 is levied on collateral, so the share scales with the ratio: the two constraints are in tension through
`baseRatioBps[0]` and no (ratio, fee) point in the 20 runs satisfies both. At the locked 1,925 % the minter's round trip is
**7.22 %** against the plan's ≈ 2.1 % and the 2.25 % cap, and a seat earns ≈ $116 against the plan's ≈ $80 and the $50 floor
(`last365__bootstrap__s20261005` §3.38). Pool revenue passes 20/20. The plan's H-4 numbers do not hold at any ratio ≥ 725 %.

**300 % halt (H-11).** `halt_sys_bad` = P(the true price falls to 1/halt within grace) ≤ 0.5 %, §3.29. Fails 20/20:
bootstrap 2.66 / 2.77 / 2.75 % (full), 6.51 / 6.15 / 6.60 % (last365), 4.29–4.80 % (2021-22), 3.26–3.84 % (2025-26); real
path 3.4 % (full), 2.68 % (last365), 6.14 % (2021-22), 1.96 % (2025-26); regime 8.1–8.6 % (full), 11.9–12.0 % (last365);
martingale 2.9–3.1 % / 6.3–6.9 %. The plan's "meets the 1.6 % tolerance" (H-11) is against a different tolerance than the
policy's 0.5 %; neither is met in the last-365 window. `halt_below_floor` passes 20/20; `halt2_timely` fails once
(2025-26/s03, recall_alarm 0.889). `recapRatioBps` is derived (2× halt) yet listed BLOCKED in §1 "Top remaining risks" and §7
of all 20 reports.

**`maxMint` $2,500 (H-12).** Identical in 20/20 (§3.66, design note G9-ENV-1): a $2,500 vault liquidates **$3,125** at
θ = 125 %, which is **4.46×** the depth budget (`max_depth_fraction` 10 % of p10 daily volume $700 = $70) and 17× the ±2 %
book depth ($188); the depth check would allow a **$56** vault, below `minMint` $100 and the registry floor $1,000. Volume
needed to clear $31,250. The plan's "≈ 4.5 days of p10 volume" matches; the constraint still fails by construction.

**Divergence recall (`divergenceBps` 2,500).** `halt_recall_floor` 0.90 (policy line 124), §3.30. Recall: std full
88.3 / 81.0 / 79.7 %, std last365 82.1 / 82.4 / 81.1 %, 2021-22 82.6 / 90.9 / 95.7 %, 2025-26 78.3 / 81.8 / 65.2 %, regime
78.3 / 72.7 (full), 87.0 / 90.9 (last365), martingale 78.3 / 81.8 (full), 82.6 / 90.9 (last365). The miss is the pump-dump
scenario (recall 29–86 %); crash-70-1d is 100 % everywhere. `calm_availability` also fails in 4 runs (std full s05 39 h/yr;
all three 2021-22). KEEP only where recall happens to clear 90 % (two last-365 stress runs, s04). 2,250 fails calm
availability (two-seed review §1).

## 4. What the stress models change against bootstrap

1. **Class A feasibility.** Bootstrap: a lattice value meets 0.5 % in every window (§1). Regime: needs 180,000–230,000 and
   is infeasible within the lattice in 3/4 runs (exposure 0.7–1.5 %). Martingale: infeasible in 4/4, 20–44 % at any ratio.
   The lock rule is defined over bootstrap only; the plan's G-7 "reported on regime/martingale" is satisfied by these
   figures, which are adverse.
2. **`claimThresholdBps` 12,500** loses its claimant under regime/full: p10 margin 199.8 / 147.4 bps against the 200-bps
   floor (`claim_incentive` fails, §3.15); bootstrap margins are 359–961 bps. Regime/last365 passes (267 / 241 bps).
3. **`emergencyRatioBps` 10,500** moves from PROVISIONAL-clean to PROVISIONAL with `bad_debt_A` (7/8) and `claim_incentive`
   (2/8) violations at the current value (`harden-quick-stress/robust.md`).
4. **Halt**: regime roughly triples the bootstrap P(sys bad) (8–12 % vs 2.7–6.6 %); martingale matches bootstrap (§3).
5. **Divergence**: unchanged in character; two last-365 s04 stress runs clear 90 % recall, the rest do not.
6. **`signalWindow` / `dormancyMinBundles`**: stable at 3,744 / 12 in 8/8 — the quick budget, not the model, drives this
   (the 2-seed quick windows sweep gives the same values).
7. **sigma pins**: regime/s04 evidence points to 9,000 (full) and 8,500 (last365), the widest departure from 18,000 in the
   set (M14 ratio 0.52× / 0.49×, §3.6).

## 5. ybcal report-text defects (cosmetic; numbers unaffected) — bug list for yb-calibration

1. §3.38 and §3.42 notes: "attestFeeBps = 5000 (D-3 …: the attestor fee is additive, **25 % of feeZat**)" — value is 5,000
   = 50 %, and the decision is H-4 not D-3 (20/20 reports, e.g. `harden-std/runs/full__bootstrap__s20261003/report.md:2214,
   2362`).
2. §3.66 prose: "**Keep 250000**" — cents printed where the parameter is shown as $2,500.00 everywhere else (20/20,
   `report.md:3281`).
3. §1 header counts **BLOCKED 1** while "Top remaining risks" item 1 and §7 list **2** (recapRatioBps, divergenceBps);
   recap is derived (2× halt, Appendix A) and should not be counted BLOCKED at all (20/20; the two last-365 stress s04 runs
   show BLOCKED 0 with recap still listed).
4. `params.cpp.patch` / Appendix C comment: "on every network **only class A (500 %)** mints through a halt" carried from
   the pin's 50,000 line onto the 60,000 line (20/20, `params.cpp.patch:56,62`).
5. `robust.md` worse-window table hard-codes `full`/`last365` columns: the windows sweep prints "— (no finished run with a
   result table)" for 2021-22/2025-26 and the stress sweep prints "no candidate feasible in all 4 run(s)" without naming the
   model (`harden-quick-windows/robust.md`, `harden-quick-stress/robust.md`).
6. §1 "Owner decisions the evidence argues against", `globalRatioHaltBps` row: "halt.required_bps **nan → nan**" (20/20).
7. §5 design note 14 (G7, HALT-2): "needs a halt near 300 %, which §1.4 forbids **while class C's base ratio is 300 %**" and
   "the 500 % soft-cap gate" — stale under H-5 (C off) and H-11 (halt is 300 %, recap 600 %) (20/20, `report.md:3620`).
8. §5 design note 15: "Class A at σ = 1 **sits exactly at recapRatioBps**" — true at 500/500, not at 725 or 1,925 vs 600
   (16/20).
9. §3.22 sensitivity sentence in `full__bootstrap__s20261005`: "pbad.A is flat between **2025 and 2075 %**" — lattice points
   printed as percent of a different scale (1/20; check the formatter).
10. `harden-quick-windows/robust.md` consolidated column for `baseRatioBps[0]` prints "—" although every evaluated candidate
    ≥ 42,500 is feasible in 6/6 (the consolidation skips the ratio when the lock rule is active).

## 6. What H4 decided and what it did not

Decided: the lock value under H-3 (192,500), the `signalWindow` family and `dormancyMinBundles` as CHANGE, and that every
H-4/H-11/H-12 pin fails its own constraint on every window and model. Not decided by these runs: the fee split at the lock
(no feasible point exists; owner must relax `max_fee_share_small` or drop the attestor floor), `divergenceBps` (no value meets
recall ≥ 90 % without breaking calm availability), the devnet differential (G-2, skipped 20/20), the G-1 data checks, and
whether a bootstrap-only lock is acceptable when the regime and martingale models say 0.5 % is unreachable for class A.

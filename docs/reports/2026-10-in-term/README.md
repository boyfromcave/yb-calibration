# In-term claims: P(bad debt) for the plan's classes (T5, 2026-10-07)

**Status: STUDY, no decision.** The in-term claims plan (workspace
`docs/plans/yellowback-in-term-claims-plan.md`, §2–§4) makes every Yellowback vault claimable by anyone
at the first height where its collateral is worth less than **θ = 125 %** of its debt at the attested
price; the claim completes after the attestor cancel window (**1,152 blocks = 1 day**) and the vault is
bad debt if the collateral is worth less than the debt then. Until now every P(bad debt) number
(`docs/reports/2026-10-real`, `2026-10-tolerance`, `LOW-RATIOS.md`) was computed under today's
**post-term** rule: one test when the claim path opens at `lockHeight + grace`. This report adds the
in-term claim path to the G3 collateral study's solvency statistic (`claim_path = "in-term"`,
default `"post-term"` so every earlier number reproduces bit for bit), validates it against the ZEC
study's lever harness, and re-reads bad debt for the plan's parameter set with the owner's revised
term bounds (D-IT-10, 2026-10-07): **A 30–90 d at 300 %, B 91–180 d at 400 %, C 181–365 d at 500 %**.

Data: `yb-calibration/data/local/frozen-20261004` only (SHA256SUMS verified, nothing fetched) and
the ZEC pull of 2026-10-07 (`wt/calib-study/data/local/zec`, SHA256SUMS verified). Policy
[`policy/in-term-2026-10.toml`](../../../policy/in-term-2026-10.toml) (§3 parameters, `claim_path =
"in-term"`, `claim_delay_blocks = 1152`; `ybcal params check --policy` passes with two waivers, §7).
Budget **standard** (100 paths per member, 6 h start stride, 16 grid terms, 6-year horizon);
bootstrap model 3 seeds, regime 2 seeds; `minMint`-class test vault ($1,000 debt); the
σ multiplier is 1 (`sigmaRefBps = 0`, D-IT-5). Worst ensemble member (hourly bootstrap, hourly
history, daily bootstrap, daily history), max over seeds, seed min–max in parentheses — the same
scoring as every earlier report. Everything was computed with
[`in_term_bad_debt.py`](in_term_bad_debt.py); raw rows in [`data/`](data/) (`curve.csv`, `sys.csv`,
`fee.csv`, `zec_validation.csv`), every table in full in [`data/tables.md`](data/tables.md).
Runtime ≈ 20 min wall at three `nice -n 15` jobs.

## 1. The model

`ybcal.sim.metrics.p_bad_debt_fast` (the definition behind `G3.bad_debt` and `G3Study.evaluate`'s
`bad_debt_<class>` constraints) gains `claim_path`:

* **`post-term`** (default, unchanged): the vault minted at hour `s` is tested once at the claim-path
  opening `e = s + ceil((T + grace + 1) / 48)`: bad iff `collateral · truePrice(e) < debt`.
* **`in-term`**: `u` is the first hour after `s` with `collateral · pClaim(u) < θ · debt`
  (`claimThresholdBps`, the exact integer level of RED-4(a), on the hour-mode oracle's `pClaim`); if
  `u < e` the vault is claimed in term and completes at `min(u + delay, e)` (the cancel window; capped
  at `e`, where today's claim is available at once): bad iff `collateral · truePrice(completion) <
  debt`. A vault never under θ before `e` is tested at `e` as before. The population and sample counts
  are identical in both modes; `in_term` reports the share claimed in term and `gaps` the uncovered
  share of every bad sample (loss given bad debt).

Validated three ways: a plain-loop implementation on random paths agrees to the last digit
(`tests/sim/test_vault_metrics.py::test_in_term_*`); the ZEC lever harness (§5); and a constructed
slide-then-crash path where the in-term claim closes the vault before the crash.

## 2. The plan's classes at their ratios: P(bad debt), post-term vs in-term (bootstrap)

| class · ratio | window | post-term (today) | **in-term (θ 125 %, 24 h)** | claimed in term | 1 % | 2 % | 5 % |
|---|---|---|---|---|---|---|---|
| A · 300 % | full | 7.06 % (6.66–7.06) | **1.65 % (1.47–1.65)** | 19 % | ✗ | ✓ | ✓ |
| A · 300 % | last365 | 21.42 % (19.05–21.42) | **3.16 % (2.49–3.16)** | 49 % | ✗ | ✗ | ✓ |
| A · 300 % | 2021-22 | 7.75 % | **1.94 % (1.85–1.94)** | 19 % | ✗ | ✓ | ✓ |
| A · 300 % | 2025-26 | 18.51 % (16.39–18.51) | **2.06 % (1.91–2.06)** | 37 % | ✗ | ✗ | ✓ |
| B · 400 % | full | 9.11 % (8.55–9.11) | **1.77 % (1.65–1.77)** | 18 % | ✗ | ✓ | ✓ |
| B · 400 % | last365 | 22.21 % (19.09–22.21) | **3.39 % (2.73–3.39)** | 46 % | ✗ | ✗ | ✓ |
| B · 400 % | 2021-22 | 8.65 % | **2.20 % (1.99–2.20)** | 26 % | ✗ | ✗ | ✓ |
| B · 400 % | 2025-26 | 21.99 % (18.72–21.99) | **2.45 % (2.21–2.45)** | 31 % | ✗ | ✗ | ✓ |
| C · 500 % | full | 13.24 % (12.48–13.24) | **2.40 % (1.98–2.40)** | 24 % | ✗ | ✗ | ✓ |
| C · 500 % | last365 | 25.99 % (21.70–25.99) | **3.96 % (3.26–3.96)** | 50 % | ✗ | ✗ | ✓ |
| C · 500 % | 2021-22 | 15.26 % | **4.58 %** (history member, n 1,689) | 37 % | ✗ | ✗ | ✓ |
| C · 500 % | 2025-26 | 27.52 % (22.08–27.52) | **2.98 % (2.72–2.98)** | 33 % | ✗ | ✗ | ✓ |

**Plain reading per class** (worse-window rule = max of full and last365, H-3):

* **Class A, 30–90 d at 300 %:** in-term 1.65 % (full) / 3.16 % (last365). Meets **5 %** on every
  window, **2 %** on the full history and 2021-22 only, **1 %** nowhere. Worse-window: passes 5 %,
  needs 3.5 % for the two-window rule. Today's rule at the same ratio is 7–21 %: the in-term path cuts
  P(bad debt) by **4–9×**.
* **Class B, 91–180 d at 400 %:** 1.77 % / 3.39 %. Meets 5 % everywhere, 2 % on the full history
  only, 1 % nowhere. Worse-window 3.39 %.
* **Class C, 181–365 d at 500 %:** 2.40 % / 3.96 % (4.58 % on 2021-22, a one-crash verdict from the
  two-year history member). Meets 5 % everywhere, 2 % nowhere. Worse-window 3.96 %.
* **Severity collapses** (§3): a bad in-term vault is under water by **6–8 % of its debt on average**
  (median 4–6 %, one in ten > 14–19 %), against 25–60 % under today's rule — the claim converts a
  drawdown-to-opening problem into a one-day one, as the ZEC study predicted.
* **The price of it:** on last365-like volatility **about half of all vaults are claimed in term**
  (19–24 % on the full history, 46–50 % on last365). The product promise "locked for the term" becomes
  a promise for roughly half the vaults in a volatile year. This share is the plan's disclosure fact
  (IT-8) as much as the bad-debt figure is.
* Ratio buys little in term (§4 and `tables.md` §5a): going from 300 % to 500 % in class A moves the
  full-history P(bad) from 1.65 % to 0.40 % and last365 from 3.16 % to 1.58 %; the claim threshold and
  the cancel delay move it far more (§4).

Expected loss per 100 YED minted (P × mean loss given bad), in-term: A full ≈ 0.13 YED, last365
≈ 0.21 YED; C last365 ≈ 0.27 YED — against 1.8 / 8.4 / 16 YED under today's rule.

## 3. Loss given bad debt (in-term; the worst member's bad samples, seed with the highest P)

| class | window | P(bad) | mean gap | median | p90 | worst | post-term mean gap |
|---|---|---|---|---|---|---|---|
| A | full | 1.65 % | 7.7 % | 5.7 % | 17.6 % | 54 % | 26.0 % |
| A | last365 | 3.16 % | 6.6 % | 4.5 % | 14.5 % | 56 % | 39.4 % |
| A | 2025-26 | 2.06 % | 6.4 % | 4.4 % | 14.3 % | 52 % | 32.7 % |
| B | full | 1.77 % | 7.9 % | 5.3 % | 18.9 % | 58 % | 32.9 % |
| B | last365 | 3.39 % | 7.0 % | 4.7 % | 15.1 % | 56 % | 50.6 % |
| C | full | 2.40 % | 6.7 % | 5.3 % | 13.6 % | 54 % | 42.9 % |
| C | last365 | 3.96 % | 6.9 % | 4.8 % | 14.7 % | 51 % | 62.3 % |

(2021-22 and the remaining rows: `data/tables.md` §2.) The gap is `1 − collateral value / debt` at
the claim's completion: how far the price fell in the 24 hours after crossing 125 %, beyond the 20 %
margin. The tail (> 50 %) is the one-day crash: the collateral at 125 % loses more than half its
value within the cancel window.

## 4. Sensitivity: the cancel delay and the threshold θ (plan ratios)

**Cancel delay**, hourly members only (`bootstrap`, `history`). The daily members hold the price
constant within a day and cannot resolve a sub-day claim window: a 6 h completion lands in the same
post-jump day as the (lagging) trigger and reads as worse than 24 h (`tables.md` §3 vs §3b); read the
delay on the hourly series.

| class | window | 6 h | 12 h | **24 h (plan)** | 48 h | post-term |
|---|---|---|---|---|---|---|
| A | full | 1.20 % | 1.26 % | **1.65 %** | 2.05 % | 7.06 % |
| A | last365 | 1.19 % | 2.07 % | **3.16 %** | 5.32 % | 19.11 % |
| A | 2025-26 | 1.40 % | 1.69 % | **2.06 %** | 3.27 % | 14.55 % |
| B | full | 1.30 % | 1.36 % | **1.77 %** | 2.09 % | 9.11 % |
| B | last365 | 1.28 % | 2.15 % | **3.39 %** | 5.84 % | 21.72 % |
| B | 2025-26 | 1.55 % | 1.99 % | **2.45 %** | 3.72 % | 17.09 % |
| C | full | 1.69 % | 1.85 % | **2.40 %** | 2.81 % | 13.24 % |
| C | last365 | 1.55 % | 2.68 % | **3.96 %** | 6.52 % | 25.99 % |
| C | 2025-26 | 2.75 % | 2.75 % | **2.98 %** | 4.73 % | 20.83 % |

The delay is close to a linear knob on the volatile windows: each halving of the window takes
roughly a third off P(bad debt) on last365 (48 h 5.3 % → 24 h 3.2 % → 12 h 2.1 % → 6 h 1.2 % for
class A). **With the plan's ratios, 2 % on every window needs ≤ 12 h for A and B and 6 h for C; 24 h
meets 5 %; 48 h fails 5 % on last365 for every class.** A shorter window is also less time for the
attestor set to cancel a bad claim (plan §6 risk 2): the delay trades solvency against the cancel
safeguard, and this study only measures the solvency side.

**Claim threshold θ** (24 h delay, all members):

| class | window | θ 115 % | **θ 125 % (plan)** | θ 150 % |
|---|---|---|---|---|
| A | full | 3.15 % | **1.65 %** | 0.25 % |
| A | last365 | 8.35 % | **3.16 %** | 0.45 % |
| A | 2025-26 | 5.61 % | **2.06 %** | 0.27 % |
| B | full | 3.61 % | **1.77 %** | 0.29 % |
| B | last365 | 9.29 % | **3.39 %** | 0.49 % |
| C | full | 5.08 % | **2.40 %** | 0.36 % |
| C | last365 | 11.17 % | **3.96 %** | 0.59 % |
| C | 2021-22 | 7.22 % | **4.58 %** | 0.95 % |

θ is the strongest lever in the set: **at θ = 150 % every class meets 1 % on every window at the
plan's ratios** (0.25–0.95 %); at 115 % none meets 5 % on last365. The cost of a higher θ is borne by
the owner (the vault is closed earlier and more often; the claimant's take is capped at θ × debt, so
the owner's residual is what θ protects, RED-5) and it widens the claimant's margin from 25 % to
50 % — which is also the slippage budget §6 cannot model.

**Smallest grid ratio within each tolerance in every seed** (in-term, θ 125 %, 24 h; grid
150–725 %; `tables.md` §5a): class A needs 300 % (full) / 450 % (last365) for 2 % and 400 % / 725 %
for 1 %; class B 400 % / none ≤ 725 % for 2 %; class C 600 % (full) and none ≤ 725 % on the other
windows for 2 %; 5 % is met at 150–350 % everywhere but C 2021-22 (500 %). Under the post-term rule
(`tables.md` §5b) B and C meet 2 % nowhere on the grid and A needs 500 % (full) / > 725 % (last365).

**Regime stress** (2 seeds, plan ratios; `tables.md` §6): in-term 7–13 % against 16–28 % post-term;
nothing passes 5 % under the regime model, as nothing ≤ 725 % did under the post-term rule either.

## 5. Validation against the ZEC study's lever harness

`zec_level_shift.lever_study` scores the same lever on a single real path (ZEC 2017-04 →, hourly;
YEC frozen hourly), one term, market price for both the trigger and the outcome, skip 400 d, stride
6 h, completion at `min(u + d, H − 1)`. Re-run here with its inner loop (`_harness`) next to
`p_bad_debt_fast` on the same series, first with an **ideal oracle** (pMint = pClaim = market price,
multiplier 1), then with the **hour-mode kernel** the G3 ensemble uses (`data/zec_validation.csv`;
`tables.md` §10):

| series | term | ratio | mode | harness | ybcal, ideal oracle | ybcal, hour-mode kernel |
|---|---|---|---|---|---|---|
| YEC | 90 d | 300 % | post-term | 6.10 % | 6.17 % | 5.44 % |
| YEC | 90 d | 300 % | in-term | 0.44 % | 0.44 % | 2.03 % |
| YEC | 60 d | 300 % | post-term | 4.58 % | 4.70 % | 4.31 % |
| YEC | 60 d | 300 % | in-term | 0.38 % | 0.38 % | 0.94 % |
| YEC | 30 d | 300 % | post-term | 2.64 % | 2.64 % | 2.40 % |
| YEC | 30 d | 300 % | in-term | 0.19 % | 0.19 % | 0.55 % |
| YEC | 90 d | 400 % | in-term | 0.01 % | 0.01 % | 0.09 % |
| ZEC | 90 d | 300 % | post-term | 0.85 % | 0.86 % | 0.04 % |
| ZEC | 90 d | 300 % | in-term | 0.00 % | 0.00 % | 0.00 % |
| ZEC | 60 d | 300 % | post-term | 0.45 % | 0.47 % | 0.01 % |

* With the ideal oracle the two implementations **agree to the last digit on every in-term cell** and
  within 0.1 pp post-term (the harness tests at hour `s + H`, ybcal at the opening block
  `R + T + grace + 1` → hour `s + H + 1`, and ybcal rounds the collateral up to a zatoshi).
* With the hour-mode kernel the direction and magnitude agree (in-term removes 60–100 % of the
  post-term bad debt in every cell) but the levels differ for two known reasons: post-term is **lower**
  because the oracle's HALT-3 / NO_PRICE bits refuse mints during a crash and pMint is a lagged
  median (fewer vaults are minted at the top); in-term is **higher** because pClaim lags the market in
  a crash, so the claim fires later and completes lower (YEC 90 d / 300 %: 0.44 % → 2.03 %). This lag
  is a real property of the attested price, so the G3 figures (§2) are the ones to read; the harness
  is the floor an ideal oracle would reach.
* The G3 numbers in §2 are higher again than this single-path check because they are the **worst of
  four members over 100 bootstrap paths × 16 terms × 3 seeds**, not the one realised path; the realised
  `history` member is 0.0–1.5 % in-term for A and B on every window (1.52 % on 2021-22, 0.0–0.9 % elsewhere; `data/curve.csv`).

## 6. HALT-2 at 200 %, the recap at 500 %, the fee share

**`halt_sys_bad` does not pass at 200 %** as G7 defines it (`data/sys.csv`; `tables.md` §8): the
metric is P(the price falls to ≤ 1/2 of its level within `grace`) — what a post-term book sitting at the
halt ratio needs to stay solvent — and on YEC that is 15.4 % (full) / 26–27 % (last365) / 19–20 %
(2021-22, 2025-26) over daily starts, 11–22 % on the real history, against a system tolerance of 2 %
(the class tolerances weighted by debt share). It did not pass at 300 % either (≈ 2.9 % against 1.6 %, 6/6 runs; the hardening report's H-11 kept
300 % by owner decision); at 200 % the fall it measures is to one half rather than one third, hence
the jump to 15–27 %. Under in-term claims the question changes and G7's
metric is no longer the right one: a book at 200 % is **claimed**, not left to go under. P(the price
falls to the θ level, ×0.625, within grace) is 35–46 % (30–40 % real) — a halted book will see its
vaults closed by claimants within the month in a third to half of the cases — and the vault model
says a vault sitting at 200 % when claims start goes bad **3.6–6.0 %** of the time in term against
18–53 % post-term (`tables.md` §8, second table). A G7 counterpart of this in-term reading
(P(bad at completion | halt) against the class tolerance) is the open item; this report does not
redefine `halt_sys_bad`. The structural checks hold: 200 % < every class base (300 %); the recap
floor 500 % equals class C's base, so **exactly one class (C) recapitalises** under HALT_GLOBAL_RATIO
and above the soft cap (IT-5, `recap_classes = 1`); W16's recap = 2 × halt is given up (waived
invariant, §7).

**FEE-1 round-trip share** (mint while ARMED, 15 bps + 50 % attestor share, plus the owner redeem; at
the last frozen price $0.3609; `data/fee.csv`): **1.13 % at 300 %, 1.50 % at 400 %, 1.88 % at
500 %** of the debt, the same for a $100 (`minMint`) and a $1,000 vault (the floor does not bind) —
all inside H-4's 2.25 % (2.72 % at 725 %, as H4 found). The fee scales with the collateral, so the
lower ratios are also the cheaper vaults.

## 7. What the model cannot capture, and what to flag

1. **Claimant supply of YED.** Every crossing of θ is a claim at that hour. Nobody needs to hold YED,
   see the vault, or want it. The share claimed in term (19–50 %) is the *demand* for claimant capital
   the plan creates; the model has no supply side. If claims lag, the in-term numbers drift toward
   the post-term column.
2. **Depth and slippage.** The claimant sells the seized YEC: at $700 p10 daily volume, one `minMint`
   class-A vault's collateral ($300) is 43 % of a day's volume and a $1,000-debt vault's ($3,000) is
   four days' worth. The 25 % margin is the whole slippage budget (plan §6 risk 3); the 150 % θ that
   meets 1 % here doubles it. Correlated claims in a crash (many vaults crossing θ in the same hour)
   are a fire sale the single-vault statistic does not see.
3. **The owner's incentive to redeem before θ.** An owner who redeems (burns YED) at 130 % avoids the
   claim; the model has no owner. That makes the in-term P(bad) an upper bound on the claim path's
   bad debt given the price path, and the claimed share an upper bound on forced closures.
4. **Oracle lag and cancel.** pClaim lags the market (§5): a claim fires later than the market would
   suggest. The cancel window is modelled only as a delay; a cancelled bad claim, or an attestor set
   that cannot agree within the window, is outside the model.
5. **Data limits.** The daily members cannot resolve a sub-day window (§4). Class C terms (181–365 d
   + 30 d grace) do not fit a 365-day window: on last365 the two history members have **3 of 16 grid
   terms censored and 247 samples** (`tables.md` §7); on 2021-22 the history members have 1,689–1,692
   samples and one crash. Only the bootstrap members (6-year horizon, 727–747 k samples) carry class
   C; the 2021-22 class-C figure (4.58 %) is a one-event verdict. Classes A and B are uncensored
   everywhere.
6. **Registry gaps.** There is no `claimDelay` in the parameter registry (the node at the ybcal pin
   has `attestArmDelay` 1,152 and `emergencyNoticeTtl` 1,152, not a claim completion delay), so the
   delay is a policy assumption (`claim_delay_blocks`) until the node carries the field. The policy
   waives two invariants with the plan's references: `recap_double` (W16, replaced by D-IT-12) and
   `regtest_zero_meanings` (M13 reads `sigmaRefBps = 0` as regtest-only; D-IT-5 uses it on mainnet to
   pin the multiplier at 1 — the node must honour that reading, which is the registry's documented
   convention but was an invariant violation here).
7. **One template.** One YEC series (bootstrapped), one ZEC path for validation; the ensemble is a
   demeaned block bootstrap, not a model of regimes (the regime stress is in `tables.md` §6).

## 8. Commands (worktree root; `yb-calibration/.venv`, `PYTHONPATH=src`; `nice -n 15`, ≤ 3 jobs)

```bash
S=../../yb-calibration/data/local/frozen-20261004; (cd $S && shasum -a 256 -c SHA256SUMS)
Z=../calib-study/data/local/zec; (cd $Z && shasum -a 256 -c SHA256SUMS)
PY=../../yb-calibration/.venv/bin/python; T=docs/reports/2026-10-in-term; export PYTHONPATH=src
$PY -c "from ybcal.cli import main; import sys; sys.exit(main())" params check --policy policy/in-term-2026-10.toml
nice -n 15 $PY $T/in_term_bad_debt.py curve --window full --window last365 --seeds 20261003,20261004,20261005 \
  --budget standard --models bootstrap --sensitivity --data $S --out $T/data/a                       # ≈ 14 min
nice -n 15 $PY $T/in_term_bad_debt.py curve --window 2021-22 --window 2025-26 --seeds 20261003,20261004,20261005 \
  --budget standard --models bootstrap --sensitivity --data $S --out $T/data/b                       # ≈ 14 min
nice -n 15 $PY $T/in_term_bad_debt.py curve --window full --window last365 --window 2021-22 --window 2025-26 \
  --seeds 20261003,20261004 --budget standard --models regime --grid '' --data $S --out $T/data/c    # ≈ 7 min
nice -n 15 $PY $T/in_term_bad_debt.py sys --window full --window last365 --window 2021-22 --window 2025-26 \
  --seeds 20261003,20261004,20261005 --budget standard --data $S --out $T/data/sys                   # ≈ 9 min
nice -n 15 $PY $T/in_term_bad_debt.py fee --data $S --out $T/data/fee                                 # seconds
nice -n 15 $PY $T/in_term_bad_debt.py zec --zec-dir $Z --data $S --out $T/data/zec                    # ≈ 3 min
# merge a/b/c curve.csv into $T/data/curve.csv (header once), copy sys.csv fee.csv zec_validation.csv up, then
$PY $T/in_term_bad_debt.py tables $T/data                                                            # data/tables.md
```

## 9. Not done

- No lock run: the owner has not chosen the tolerance (1–5 %); the policy carries 2 % per class as a
  placeholder and §2 reads all three.
- The regime model was run at the plan ratios only (2 seeds), no grid or sensitivity.
- No G7 redefinition of `halt_sys_bad` for the in-term path (§6); the in-term reading is reported next
  to the stock metric.
- The claimant-incentive statistic (`claim_stats`, θ grid) and the emergency path were not re-read;
  they do not depend on the claim-path mode as modelled here.

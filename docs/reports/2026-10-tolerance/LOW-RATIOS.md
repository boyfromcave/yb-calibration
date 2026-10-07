# Class A at 300–725 %: the low-ratio extension of the tolerance study (2026-10-07)

**Status: STUDY, no decision.** Owner reaction to [README.md](README.md) (2026-10-07): 725 % is
acceptable with disclosure — what about 500 %? The published grid stopped at 500 % and the owner
consequences were tabulated for 725 % and 1,925 % only. This report extends the curve **downward
and finer** (300 … 725 %, 50-bps-of-ratio steps), on all four windows, and gives the owner a
**disclosure table**: how often a class-A vault is under water when its claim path opens, how far
under water it is when that happens, and what the ratio costs in YED minted per USD locked. **It
does not pick the tolerance or the ratio.**

Data: `data/local/frozen-20261004` only (SHA256SUMS verified, nothing fetched). Policy
`policy/harden-2026-10.toml` (class A 30–90 d, grace 30 d, `sigma_mult_at = "median"`, `minMint`
vault). Budget **standard** (100 paths per member, 6 h start stride, 16 grid terms, 6-year
horizon); bootstrap model 3 seeds, regime 2 seeds; worst ensemble member (hourly bootstrap, hourly
history, daily bootstrap, daily history). Everything was computed directly over the grid with
[`bad_debt_curve.py`](bad_debt_curve.py) (same scorer as the lock, validated bit for bit against H4
in README §6; the 500 / 600 / 725 % values here equal the README's to the last digit). Runtime
≈ 25 min wall at three `nice -n 15` jobs. Raw rows: [`data/low/`](data/low/) (`curve.csv`,
`terms.csv`, `sigma.csv`, `loss.csv`, `loss_hist.csv`); every table in full:
[`data/low/low-tables.md`](data/low/low-tables.md) and [`data/low/tables.md`](data/low/tables.md).

## 1. The curve, 300–725 % (bootstrap, worst member, max over 3 seeds; seed min–max in parentheses)

| `baseRatioBps[0]` | full history | last 365 d | 2021-22 | 2025-26 |
|---|---|---|---|---|
| 30,000 (300 %) | 6.37 % (5.90–6.37) | 21.42 % (19.05–21.42) | 1.78 % (1.50–1.78) | 18.51 % (16.39–18.51) |
| 35,000 (350 %) | 4.18 % (3.84–4.18) | 17.81 % (15.60–17.81) | 1.11 % (0.94–1.11) | 14.09 % (12.08–14.09) |
| 40,000 (400 %) | 2.86 % (2.62–2.86) | 14.77 % (12.78–14.77) | 0.73 % (0.61–0.73) | 10.79 % (8.98–10.79) |
| 45,000 (450 %) | 2.04 % (1.85–2.04) | 12.24 % (10.47–12.24) | 0.51 % (0.38–0.51) | 8.35 % (6.72–8.35) |
| **50,000 (500 %)** | **1.48 %** (1.33–1.48) | **10.19 %** (8.60–10.19) | **0.37 %** (0.24–0.37) | **6.53 %** (5.14–6.53) |
| 55,000 (550 %) | 1.08 % (0.95–1.08) | 8.51 % (7.08–8.51) | 0.27 % (0.16–0.27) | 5.16 % (4.09–5.16) |
| **60,000 (600 %)** | **0.80 %** (0.69–0.80) | **7.16 %** (5.87–7.16) | **0.21 %** (0.11–0.21) | **4.11 %** (3.20–4.11) |
| 65,000 (650 %) | 0.63 % (0.50–0.63) | 6.06 % (4.89–6.06) | 0.16 % (0.08–0.16) | 3.31 % (2.60–3.31) |
| **72,500 (725 %)** | **0.46 %** (0.32–0.46) | **4.85 %** (3.79–4.85) | **0.10 %** (0.05–0.10) | **2.43 %** (1.92–2.43) |

The curve is smooth and roughly log-linear: on every window, each 100 percentage points of ratio
between 400 % and 725 % removes about a third of the remaining P(bad debt). Going from 725 % to
500 % roughly **doubles** it everywhere (full 0.46 → 1.48 %, last365 4.85 → 10.19 %, 2025-26
2.43 → 6.53 %). The worst member is the hourly bootstrap on full / 2021-22 and the **daily**
bootstrap on last365 / 2025-26 (at 725 % on last365 the hourly bootstrap).

## 2. At 500 %, what tolerance would each window pass? And what ratio does each tolerance need?

| ratio | full | last365 | 2021-22 | 2025-26 |
|---|---|---|---|---|
| 500 % | 1.48 % → passes **1.5 %** | 10.19 % → passes **10.5 %** | 0.37 % → passes **0.5 %** | 6.53 % → passes **7 %** |
| 600 % | 0.80 % → passes 1 % | 7.16 % → passes 7.5 % | 0.21 % → passes 0.5 % | 4.11 % → passes 4.5 % |
| 725 % | 0.46 % → passes 0.5 % | 4.85 % → passes 5 % | 0.10 % → passes 0.5 % | 2.43 % → passes 2.5 % |

Smallest grid ratio within the tolerance in **every** seed (bootstrap):

| window | 1 % | 2 % | 3 % | 5 % | 7.5 % | 10 % |
|---|---|---|---|---|---|---|
| full | 600 % | **500 %** | 400 % | 350 % | 300 % | 300 % |
| last365 | none ≤ 725 % | none ≤ 725 % | none ≤ 725 % | 725 % | 600 % | 550 % |
| 2021-22 | 400 % | 300 % | 300 % | 300 % | 300 % | 300 % |
| 2025-26 | none ≤ 725 % | none ≤ 725 % | 725 % | 600 % | **500 %** | 450 % |

**Plain reading.** 500 % passes 2 % on the full history and 0.5 % on 2021-22, but on the last 365
days it is a 1-in-10 vault and on 2025-26 a 1-in-15 vault. Under the worse-window rule (max of
full, last365) 500 % needs a **10.5 %** tolerance; 600 % needs 7.5 %; 725 % needs 5 %. Under the
full-history-only rule 500 % passes 1.5 % (so 2 %), 600 % passes 1 %.

## 3. Disclosure table — P(bad debt), seed spread, loss given bad debt, YED per USD locked

How to read it: *P(bad debt)* is the share of `minMint` class-A vaults minted in a calm hour
(σ multiplier ×1.0, the policy's `median` sizing) whose collateral is worth less than their debt
when the claim path opens (lock term + 30 d grace); worst ensemble member, max over 3 seeds,
min–max in parentheses. *Loss given bad debt* is the distribution of `1 − collateral/debt` over
those bad samples (pooled over seeds, same evaluation — `bad_debt_curve.py loss` replicates
`p_bad_debt_fast`'s loop and keeps the per-sample gaps that the library only sums into
`shortfall`; its per-term counts match `G3.bad_debt` exactly, the script aborts otherwise).
*YED per USD locked* = 10,000 / ratio at σ ×1.0 (0.138 at 725 % as in the H4 README); the
round-trip fee share of a `minMint` vault scales linearly from H4's 2.7 % at 725 % (FEE-1 is levied
on the collateral). The terms shown are the shortest, median and longest of the 16 grid terms
(32, 62 and 88 d); "class" is the term-averaged value the lock rule scores. Full tables (every
window, both other ratios): `data/low/low-tables.md`.

### 500 % — 0.200 YED per USD locked ($500 of YEC per 100 YED); round-trip fee ≈ 1.9 %

| window (worst member) | term | P(bad debt) | loss given bad: mean | median | p90 | worst |
|---|---|---|---|---|---|---|
| full (hourly bootstrap) | 32 d | 0.37 % (0.29–0.37) | 17 % | 14 % | 37 % | 72 % |
| | 62 d | 1.44 % (1.31–1.44) | 22 % | 18 % | 46 % | 86 % |
| | 88 d | 2.87 % (2.53–2.87) | 25 % | 22 % | 52 % | 91 % |
| | **class 30–90 d** | **1.48 % (1.33–1.48)** | ≈ 23 % | | | |
| last365 (daily bootstrap) | 32 d | 3.79 % (3.36–3.79) | 18 % | 15 % | 38 % | 79 % |
| | 62 d | 10.99 % (9.27–10.99) | 29 % | 27 % | 56 % | 90 % |
| | 88 d | 15.12 % (12.61–15.12) | 38 % | 38 % | 68 % | 94 % |
| | **class 30–90 d** | **10.19 % (8.60–10.19)** | ≈ 31 % | | | |
| 2021-22 (hourly bootstrap) | 32 d | 0.09 % (0.03–0.09) | 15 % | 13 % | 32 % | 60 % |
| | 62 d | 0.35 % (0.21–0.35) | 21 % | 18 % | 42 % | 75 % |
| | 88 d | 0.77 % (0.62–0.77) | 23 % | 20 % | 49 % | 83 % |
| | **class 30–90 d** | **0.37 % (0.24–0.37)** | ≈ 23 % | | | |
| 2025-26 (daily bootstrap) | 32 d | 1.52 % (1.12–1.52) | 15 % | 12 % | 32 % | 78 % |
| | 62 d | 6.87 % (5.12–6.87) | 23 % | 20 % | 47 % | 80 % |
| | 88 d | 11.64 % (9.65–11.64) | 30 % | 27 % | 58 % | 88 % |
| | **class 30–90 d** | **6.53 % (5.05–6.53)** | ≈ 26 % | | | |

### 600 % — 0.167 YED per USD locked ($600 of YEC per 100 YED); round-trip fee ≈ 2.2 %

| window (worst member) | term | P(bad debt) | loss given bad: mean | median | p90 | worst |
|---|---|---|---|---|---|---|
| full (hourly bootstrap) | 32 d | 0.16 % (0.13–0.16) | 17 % | 13 % | 41 % | 67 % |
| | 62 d | 0.81 % (0.63–0.81) | 21 % | 17 % | 44 % | 83 % |
| | 88 d | 1.72 % (1.56–1.72) | 24 % | 21 % | 49 % | 89 % |
| | **class 30–90 d** | **0.80 % (0.69–0.80)** | ≈ 24 % | | | |
| last365 (daily bootstrap) | 32 d | 1.75 % (1.51–1.75) | 16 % | 13 % | 35 % | 75 % |
| | 62 d | 7.68 % (6.34–7.68) | 26 % | 24 % | 52 % | 88 % |
| | 88 d | 12.16 % (9.82–12.16) | 35 % | 34 % | 65 % | 93 % |
| | **class 30–90 d** | **7.16 % (5.87–7.16)** | ≈ 29 % | | | |
| 2021-22 (hourly bootstrap) | **class 30–90 d** | **0.21 % (0.11–0.21)** | ≈ 21 % | 16 % (62 d) | 38 % (62 d) | 79 % |
| 2025-26 (daily bootstrap) | 32 d | 0.62 % (0.36–0.62) | 13 % | 11 % | 30 % | 73 % |
| | 62 d | 4.22 % (2.79–4.22) | 21 % | 18 % | 43 % | 76 % |
| | 88 d | 8.32 % (6.37–8.32) | 27 % | 25 % | 55 % | 86 % |
| | **class 30–90 d** | **4.11 % (2.93–4.11)** | ≈ 25 % | | | |

### 725 % — 0.138 YED per USD locked ($725 of YEC per 100 YED); round-trip fee ≈ 2.7 %

| window (worst member) | term | P(bad debt) | loss given bad: mean | median | p90 | worst |
|---|---|---|---|---|---|---|
| full (hourly bootstrap) | 32 d | 0.07 % (0.03–0.07) | 19 % | 16 % | 42 % | 60 % |
| | 62 d | 0.44 % (0.27–0.44) | 20 % | 17 % | 42 % | 80 % |
| | 88 d | 0.99 % (0.85–0.99) | 23 % | 19 % | 46 % | 87 % |
| | **class 30–90 d** | **0.46 % (0.32–0.46)** | ≈ 24 % | | | |
| last365 (hourly bootstrap) | 32 d | 1.53 % (1.01–1.53) | 21 % | 17 % | 44 % | 76 % |
| | 62 d | 5.09 % (3.96–5.09) | 29 % | 26 % | 58 % | 90 % |
| | 88 d | 8.17 % (6.69–8.17) | 36 % | 34 % | 67 % | 96 % |
| | **class 30–90 d** | **4.85 % (3.79–4.85)** | ≈ 31 % | | | |
| 2021-22 (hourly bootstrap) | **class 30–90 d** | **0.10 % (0.05–0.10)** | ≈ 20 % | 13 % (62 d) | 34 % (62 d) | 75 % |
| 2025-26 (daily bootstrap) | 32 d | 0.21 % (0.08–0.21) | 12 % | 10 % | 27 % | 67 % |
| | 62 d | 2.38 % (1.40–2.38) | 19 % | 16 % | 39 % | 71 % |
| | 88 d | 5.61 % (3.85–5.61) | 25 % | 22 % | 51 % | 83 % |
| | **class 30–90 d** | **2.43 % (1.56–2.43)** | ≈ 23 % | | | |

**How far under water, when it happens.** The severity barely depends on the ratio: a bad vault
at 500 % is under water by about the same share as one at 725 % (mean 20–30 % of the debt, median
15–25 %, one in ten bad vaults > 45–65 %). Why: at these ratios a vault is bad only after a price
collapse of ≥ 80–86 % within term + grace, and *given* such a collapse the extra depth is set by the
tail of the return distribution, not by the starting ratio. The ratio buys **frequency**, not
depth. Severity rises with term (32 d ≈ 15–20 %, 88 d ≈ 25–38 %) and is worst on last365, whose
bootstrapped tail is fattest. Distribution over all bad samples (worst member, all terms and seeds):

| ratio · window | 0–5 % | 5–10 % | 10–15 % | 15–20 % | 20–25 % | 25–30 % | 30–40 % | 40–50 % | 50–60 % | 60–80 % | > 80 % |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 500 % · full | 15 | 14 | 12 | 11 | 9 | 8 | 13 | 9 | 5 | 3 | 0 |
| 500 % · last365 | 9 | 9 | 9 | 9 | 9 | 8 | 15 | 13 | 10 | 9 | 1 |
| 500 % · 2025-26 | 13 | 12 | 11 | 11 | 10 | 9 | 14 | 10 | 7 | 4 | 0 |
| 725 % · full | 17 | 15 | 13 | 11 | 10 | 8 | 12 | 7 | 4 | 3 | 0 |
| 725 % · last365 | 10 | 10 | 9 | 9 | 8 | 8 | 14 | 12 | 9 | 9 | 1 |
| 725 % · 2025-26 | 15 | 14 | 13 | 11 | 10 | 9 | 13 | 9 | 5 | 2 | 0 |

(percent of bad samples per bin; 600 % and 2021-22 in `data/low/low-tables.md`.) Expected loss
per 100 YED minted (P × mean loss given bad) at 500 %: full ≈ 0.34 YED, last365 ≈ 3.1 YED,
2025-26 ≈ 1.7 YED; at 725 %: 0.11 / 1.5 / 0.56 YED.

## 4. Term limit: what cap on class A makes 500 % pass 2 % / 5 %?

`classMax[0]` capped (class A uniform over 30 d … cap, 16 grid terms, same scorer), worst
member, max over 3 seeds, bootstrap, standard:

| ratio · window | 30 d only | ≤ 35 d | ≤ 40 d | ≤ 45 d | ≤ 50 d | ≤ 60 d | ≤ 75 d | ≤ 90 d (now) |
|---|---|---|---|---|---|---|---|---|
| **500 % · last365** | **4.04 %** | 4.48 % | **4.97 %** | 5.44 % | 5.93 % | 7.10 % | 8.76 % | 10.19 % |
| 500 % · 2025-26 | 1.70 % | **1.95 %** | 2.21 % | 2.49 % | 2.82 % | 3.73 % | 5.14 % | 6.53 % |
| 500 % · full | 0.32 % | — | — | 0.54 % | — | 0.81 % | 1.12 % | 1.48 % |
| 500 % · 2021-22 | 0.08 % | — | — | 0.12 % | — | 0.19 % | 0.27 % | 0.37 % |
| 600 % · last365 | 2.40 % | — | — | 3.47 % | — | 4.63 % | 5.76 % | 7.16 % |
| 600 % · 2025-26 | 0.81 % | — | — | 1.32 % | — | 1.98 % | 3.00 % | 4.11 % |
| 725 % · last365 | 1.33 % | — | — | 2.08 % | — | 2.99 % | 3.92 % | 4.85 % |
| 725 % · 2025-26 | 0.36 % | — | — | 0.64 % | — | 1.05 % | 1.61 % | 2.43 % |

- **500 % at 5 % on last365:** passes with class A capped at **40 days** (4.97 %, the worst seed
  just under 5 %; 45 d is 5.44 %). A 30-day-only class is 4.04 %.
- **500 % at 2 % on last365:** **no term cap achieves it** — even a 30-day-only vault is 4.04 %.
  Only a higher ratio (none ≤ 725 % passes 2 % on last365 — §2) or a different σ sizing (§5) does.
- 500 % at 2 % on 2025-26: cap 35 d (1.95 %); at 5 %: cap 60 d (3.73 %; 75 d is 5.14 %).
- 600 % on last365: 5 % at cap 60 d (4.63 %), 725 % at the current 90 d (4.85 %). 600 % at 2 %
  is not reachable by term alone either (30 d only: 2.40 %).

A term cap is a policy knob (`classMax[0]`), not a node change; 30 d is the hard floor (D-R-6:
class A keeps 30–90 d — a cap amends H-5, which the owner would have to accept).

## 5. Two more levers, for completeness (first seed, worst member; `data/low/sigma.csv`)

| ratio | last365 median σ (×1.0) | p90 | clamp ×4.75 | 2025-26 median | p90 | full median | p90 |
|---|---|---|---|---|---|---|---|
| 500 % | 8.60 % | 0.76 % | 0.17 % | 6.53 % | 0.31 % | 1.36 % | 0.05 % |
| 600 % | 5.87 % | 0.45 % | 0.10 % | 4.11 % | 0.17 % | 0.79 % | 0.03 % |
| 725 % | 3.79 % | 0.27 % | 0.05 % | 2.43 % | 0.09 % | 0.46 % | 0.01 % |

With `sigma_mult_at = "p90"` (sizing the vault as minted in a volatile hour) 500 % meets 1 % on every
window; as in README §5 this is a modelling assumption about *when* vaults are minted, not a node
change, and the base ratio is by construction the collateral of a vault minted in a calm hour.

**Regime stress** (2 seeds, `data/low/low-tables.md`): 500 % is 7.5 % (full) / 12.9 % (last365) /
10.8 % (2021-22) / 8.8 % (2025-26); 725 % is 4.2 / 8.0 / 6.7 / 5.2 %. Under regime nothing ≤ 725 %
passes 3 % on any window, and last365 passes nothing ≤ 7.5 %. The martingale stress was not rerun
(README §3: 60–70 % at every ratio; cheap but uninformative at the low end).

## 6. Commands (repo root; `yb-calibration/.venv`, `PYTHONPATH=src` of the worktree; `nice -n 15`, ≤ 3 jobs)

```bash
S=../../yb-calibration/data/local/frozen-20261004; (cd $S && shasum -a 256 -c SHA256SUMS)
PY=../../yb-calibration/.venv/bin/python; T=docs/reports/2026-10-tolerance; export PYTHONPATH=src
G=30000,35000,40000,45000,50000,55000,60000,65000,72500
nice -n 15 $PY $T/bad_debt_curve.py curve --window full --window last365 --seeds 20261003,20261004,20261005 \
  --budget standard --models bootstrap --sensitivity --grid $G --data $S --out $T/data/low/direct-a   # ≈ 6 min
nice -n 15 $PY $T/bad_debt_curve.py curve --window 2021-22 --window 2025-26 --seeds 20261003,20261004,20261005 \
  --budget standard --models bootstrap --sensitivity --grid $G --data $S --out $T/data/low/direct-b   # ≈ 6 min
nice -n 15 $PY $T/bad_debt_curve.py loss --window full --window last365 --window 2021-22 --window 2025-26 \
  --seeds 20261003,20261004,20261005 --budget standard --grid 50000,60000,72500 --classmax-days 30,45,60,75 \
  --data $S --out $T/data/low/loss                                                                    # ≈ 10 min
nice -n 15 $PY $T/bad_debt_curve.py loss --window last365 --window 2025-26 --seeds 20261003,20261004,20261005 \
  --budget standard --grid 50000 --classmax-days 35,40,50 --data $S --out $T/data/low/loss-caps       # ≈ 3 min
nice -n 15 $PY $T/bad_debt_curve.py curve --window full --window last365 --window 2021-22 --window 2025-26 \
  --seeds 20261003,20261004 --budget standard --models regime --grid $G --data $S --out $T/data/low/direct-c  # ≈ 7 min
# merge direct-*/curve.csv, terms.csv, sigma.csv and loss/*.csv (+ the cap rows of loss-caps) into $T/data/low/, then
$PY $T/bad_debt_curve.py tables $T/data/low --grid $G      # data/low/tables.md (curve, need, terms, σ)
$PY $T/bad_debt_curve.py disclosure $T/data/low            # data/low/low-tables.md (this report's tables)
```

## 7. Not done

- No lock run under any tolerance (the owner has not chosen one); the curve does not depend on it.
- The term-cap refinement (35 / 40 / 50 d) was run for last365 and 2025-26 at 500 % only; the
  other cells are the 30 / 45 / 60 / 75 d grid.
- Severity is reported for the worst member only; the hourly `history` member (the real path) has
  no bad class-A vault at ≥ 725 % on last365 (README §1) and few at 500 %.
- The round-trip fee share is scaled linearly from H4's 725 % figure, not recomputed through the
  fee study.

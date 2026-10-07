# Class A's collateral ratio against the bad-debt tolerance (study, 2026-10-07)

**Status: STUDY, no decision.** The owner's direction of 2026-10-07: the 1,925 % that H4 locked
under the hardening plan's 0.5 % tolerance ([2026-10-harden/README.md](../2026-10-harden/README.md) §1)
is unacceptable, and 0.5 % P(bad debt) is too strict for a chain of this market cap; 1–5 % is
practical. This report gives the owner the curve to choose on. **It does not pick the tolerance.**

Data: the frozen snapshot `data/local/frozen-20261004` only (SHA256SUMS verified: 7/7 OK), nothing
fetched. Policy: `policy/harden-2026-10.toml` (the tolerance variant
[`policy/harden-2026-10-tol.toml`](../../../policy/harden-2026-10-tol.toml) is byte-for-byte the same
policy — identical `Policy.digest()` — with the knob documented in its header). Budget **standard**
everywhere (100 paths per member, 6 h start stride, 16 grid terms, 6-year horizon); 3 seeds for the
bootstrap model, 2 for the regime and martingale stresses. Runtime ≈ 20 min wall at three `nice -n 15`
jobs — the curve was computed **directly** over the ratio grid, not through the optimiser.

## 0. What P(bad debt) is here, and what moves it

`ybcal.sim.metrics.p_bad_debt_fast`, as `G3Study.evaluate` scores the `bad_debt_A` constraint
(`src/ybcal/studies/g3_collateral.py`): for every start hour, path and grid term of class A
(30–90 d, 16 equal-probability terms), a `minMint` vault is minted at pMint with the wallet
collateral at `baseRatioBps[0] × σ-multiplier`, and it is **bad** when its collateral is worth less
than its debt at the true price when the claim path opens (`lockHeight + grace + 1`, grace 30 d).
The class value is the **worst ensemble member** (`ensemble_agg = "worst"`); the members are the
hourly block bootstrap of the window, the window's own history, the daily block bootstrap and the
daily history (2020-01 →). The lock rule needs the value within the tolerance **in every seed**.

What moves it at a fixed ratio (§4, §5):

- **Term length** — strongly. At 725 % on the last 365 days, a 32-day vault is bad 1.0 % of the
  time, a 62-day one 4.0 %, an 88-day one 7.2 %; the class value (4.9 % worst seed) is the term
  average. Capping class A at 60 days would roughly halve the class's P(bad debt); at 30 days it
  would meet 1 % at 725 % on every window.
- **The σ multiplier** — decisively. The policy sizes at `sigma_mult_at = "median"`, and the
  median multiplier of the hourly history is **10,000 bps (×1.0): the floor**. A vault minted when
  the chain's multiplier is at its p90 is ≈ 15× safer at the same base ratio (last365, 725 %: 3.8 %
  → 0.27 %); at the clamp `sigmaMultMaxBps` 47,500 (×4.75) it is ≈ 75× safer (0.05 %). The base
  ratio is therefore the collateral of a vault minted in a *calm* hour; the chain itself demands
  more in volatile ones. (`sigma_mult_at = "p90"` is an existing policy knob; it is not a node change.)
- **The claim threshold — not at all.** `claimThresholdBps` does not enter `p_bad_debt_fast`: the
  test is collateral < 100 % of debt (`is_underwater(coll, truePrice, cents, BPS)`). θ moves when a
  vault becomes claimable and the claimant's margin (the claimant-incentive study, D-RD-COL-6), not
  whether it is under water when the claim path opens. Raising θ does not buy down this probability.
- **The price model.** The regime switch fitted on the daily history is 5–10× worse than the
  bootstrap at every ratio (full, 725 %: 4.2 % vs 0.46 %), and the `martingale` drift stress
  (−σ²/2 per year; the median path halves yearly) puts P(bad debt) at 60–70 % at **every** ratio on
  the grid: under that convention no ratio ≤ 2,500 % meets any tolerance. Both were run at quick
  budget in H4 (`harden-quick-stress`) with the same reading; they are stresses, not the sizing model.

## 1. The curve (bootstrap model, standard, worst member, max over 3 seeds)

| `baseRatioBps[0]` | full history | last 365 d | 2021-22 | 2025-26 |
|---|---|---|---|---|
| 50,000 (500 %) | 1.48 % | 10.19 % | 0.37 % | 6.53 % |
| 60,000 (600 %) | 0.80 % | 7.16 % | 0.21 % | 4.11 % |
| 72,500 (725 %) | 0.46 % | 4.85 % | 0.10 % | 2.43 % |
| 75,000 (750 %) | 0.41 % | 4.55 % | 0.09 % | 2.20 % |
| 100,000 (1,000 %) | 0.17 % | 2.52 % | 0.03 % | 0.90 % |
| 125,000 (1,250 %) | 0.08 % | 1.50 % | 0.01 % | 0.45 % |
| 150,000 (1,500 %) | 0.04 % | 0.95 % | 0.00 % | 0.25 % |
| 192,500 (1,925 %) | 0.02 % | 0.49 % | 0.00 % | 0.12 % |
| 250,000 (2,500 %) | 0.01 % | 0.24 % | 0.00 % | 0.05 % |

Per seed, per member and for the regime / martingale models: [`data/tables.md`](data/tables.md)
(§"Curve"); raw rows [`data/curve.csv`](data/curve.csv). Seed spread on last365 at 725 % is
3.8–4.9 %, at 1,925 % 0.30–0.49 %. The real last-365 path itself (`history`) has no bad vault at
≥ 725 %; the need is set by the bootstraps of that year's returns.

## 2. The inversion: smallest grid ratio within the tolerance in every seed

| tolerance | full history only | last 365 d | worse-window rule (max of full, last365) | all four windows |
|---|---|---|---|---|
| **0.5 %** (H-3 as written) | 72,500 (725 %) | 192,500 (1,925 %) | **192,500 (1,925 %)** | 192,500 (1,925 %) |
| **1 %** | 60,000 (600 %) | 150,000 (1,500 %) | **150,000 (1,500 %)** | 150,000 (1,500 %) |
| **2 %** | 50,000 (500 %) | 125,000 (1,250 %) | **125,000 (1,250 %)** | 125,000 (1,250 %) |
| **3 %** | 50,000 (500 %) | 100,000 (1,000 %) | **100,000 (1,000 %)** | 100,000 (1,000 %) |
| **5 %** | 50,000 (500 %) | 72,500 (725 %) | **72,500 (725 %)** | 72,500 (725 %) |

Grid: 50,000 · 60,000 · 72,500 · 75,000 · 100,000 · 125,000 · 150,000 · 192,500 · 250,000. The
lattice the robust harness searches is 2,500 bps wide, so a lock run with a given tolerance can land
one lattice step below these grid points (e.g. 2 % on last365 may lock between 100,000 and 125,000;
read the exact value off `data/curve.csv` or the H4 `g3_tradeoff.csv`, which has every lattice
point for full and last365). 2021-22 is the easy window (500 % meets 0.5 %); 2025-26 sits between
full and last365 and never binds under either rule.

**Plain reading.** The full-history rule says class A is fine at 500–725 % for any tolerance from
1 % up. The worse-window rule is governed entirely by the last 365 days, whose bootstrapped returns
are about ten times worse than the long history's: every 1 percentage point of tolerance buys
roughly 250 bps of ratio there — 1,500 % at 1 %, 1,250 % at 2 %, 1,000 % at 3 %, and the current
725 % only at 5 %. With `sigma_mult_at = "p90"` instead of `"median"` (sizing the vault as minted in
a volatile hour) every window meets 1 % at 725 %; that is a different modelling assumption, not a
node change, and it is listed for the owner, not recommended.

## 3. Regime and martingale stresses (standard, 2 seeds, worst member, max over seeds)

| ratio | regime · full | regime · last365 | martingale · full | martingale · last365 |
|---|---|---|---|---|
| 72,500 | 4.15 % | 7.97 % | 59.8 % | 62.5 % |
| 192,500 | 0.57 % | 1.72 % | 23.7 % | 37.0 % |
| 250,000 | 0.31 % | 1.07 % | 16.6 % | 30.3 % |

Regime needs: full 1,925 % at 1 %, 1,250 % at 2 %, 725 % at 5 %; last365 1,925 % at 2 %, 1,500 % at
3 %, 1,250 % at 5 %; martingale meets nothing ≤ 2,500 %. Full tables in `data/tables.md`.

## 4. Term length (first seed, worst member)

| ratio | last365 32 d | 62 d | 88 d | full 32 d | 62 d | 88 d | 2025-26 32 d | 62 d | 88 d |
|---|---|---|---|---|---|---|---|---|---|
| 72,500 | 1.01 % | 3.96 % | 7.23 % | 0.07 % | 0.44 % | 0.98 % | 0.35 % | 2.38 % | 5.61 % |
| 100,000 | 0.32 % | 1.81 % | 3.91 % | 0.02 % | 0.16 % | 0.42 % | 0.11 % | 0.82 % | 2.56 % |
| 125,000 | 0.12 % | 0.95 % | 2.54 % | 0.01 % | 0.07 % | 0.22 % | 0.04 % | 0.38 % | 1.36 % |
| 192,500 | 0.01 % | 0.22 % | 0.88 % | 0.00 % | 0.01 % | 0.04 % | 0.00 % | 0.10 % | 0.34 % |

Every grid term: [`data/terms.csv`](data/terms.csv).

## 5. σ multiplier (first seed, worst member)

| ratio | last365 median (×1.0) | p90 | clamp 47,500 (×4.75) | full median | p90 | clamp |
|---|---|---|---|---|---|---|
| 72,500 | 3.79 % | 0.27 % | 0.05 % | 0.46 % | 0.01 % | 0.00 % |
| 100,000 | 1.83 % | 0.10 % | 0.02 % | 0.17 % | 0.00 % | 0.00 % |
| 125,000 | 1.04 % | 0.05 % | 0.01 % | 0.08 % | 0.00 % | 0.00 % |
| 192,500 | 0.30 % | 0.01 % | 0.00 % | 0.01 % | 0.00 % | 0.00 % |

All ratios and windows: [`data/sigma.csv`](data/sigma.csv).

## 6. Validation

The direct computation reproduces the robust harness bit for bit: 216 (window, seed, ratio, member)
values of the H4 standard runs (`.work/robust/harden-std/runs/*/evidence/G3/g3_tradeoff.csv`, ingested
into [`data/robust-ingest/curve.csv`](data/robust-ingest/curve.csv) together with the quick-budget
window and stress sweeps) match `data/curve.csv` with max |Δ| = 0, and the quick smoke run matched
`harden-quick-windows` to the last digit. Standard vs quick agree in direction (quick last365 need
at 0.5 %: 215,000 vs standard 192,500).

## 7. Commands (repo root, the repo's `.venv`; `nice -n 15`, ≤ 3 jobs)

```bash
S=data/local/frozen-20261004; (cd $S && shasum -a 256 -c SHA256SUMS)
PY=.venv/bin/python; T=docs/reports/2026-10-tolerance
# the curve, directly over the grid (≈ 45 s per window×seed×model at standard)
nice -n 15 $PY $T/bad_debt_curve.py curve --window full --window last365 --seeds 20261003,20261004,20261005 \
  --budget standard --models bootstrap --sensitivity --data $S --out $T/data/direct-a
nice -n 15 $PY $T/bad_debt_curve.py curve --window 2021-22 --window 2025-26 --seeds 20261003,20261004,20261005 \
  --budget standard --models bootstrap --sensitivity --data $S --out $T/data/direct-b
nice -n 15 $PY $T/bad_debt_curve.py curve --window full --window last365 --window 2021-22 --window 2025-26 \
  --seeds 20261003,20261004 --budget standard --models regime,martingale --data $S --out $T/data/direct-c
# merge direct-*/curve.csv, terms.csv, sigma.csv into $T/data/, then
$PY $T/bad_debt_curve.py ingest .work/robust/harden-std --budget standard --out $T/data/robust-ingest
$PY $T/bad_debt_curve.py tables $T/data        # writes data/tables.md
# to LOCK under a chosen tolerance with the full harness (≈ 6 h, as H4): either edit A in the
# variant policy or override the table on the command line
ybcal robust --out .work/robust/harden-tol --budget standard --seeds 3 --windows full,last365 --models bootstrap \
  --policy policy/harden-2026-10-tol.toml --policy-set "max_bad_debt_prob={A=0.02,B=0.01,C=0.02}" \
  --data $S/yec-hourly.csv --data $S/yec-daily.csv --data $S/spreads-reconstructed.csv \
  --data $S/pool-shares.csv --data $S/depth.csv --workers 1 --jobs 3 --cache .work/cache
```

## 8. Not done

- No lock run under a new tolerance (the owner has not chosen one); the table in §2 reads it off
  the curve, which does not depend on the tolerance, so a lock run only confirms the lattice point.
- The owner-level consequences at each ratio (YED minted per USD locked, the FEE-1 round-trip share)
  are in the H4 README §1 for 725 % and 1,925 % and scale linearly with the ratio; not re-tabulated.
- The 2,500-bps lattice between grid points is not computed for 2021-22 / 2025-26 (only the grid).

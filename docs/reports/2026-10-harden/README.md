# Hardening re-run (H4-a, partial): the October 2026 set under `policy/harden-2026-10.toml`

**Status: REDUCED.** This is the first, reduced part of H4-a of the evidence-based hardening plan
(workspace `docs/plans/yellowback-evidence-based-hardening-plan.md` §9). It is **not** the lock run:
the standard all-group sweep (≈ 6 h per run) and the devnet differential with the H1–H3 binaries
were not run. What was run, on the frozen snapshot `data/local/frozen-20261004`
([`data-SHA256SUMS`](data-SHA256SUMS), verified):

| Sweep | Runs | Budget | Groups | Tables |
|---|---|---|---|---|
| harden-std-g3 | full + last365 × 3 seeds, bootstrap | **standard** | **G3 only** | [robust-std-g3.md](robust-std-g3.md) |
| harden-quick | full + last365 × 3 seeds, bootstrap | **quick** | all | [robust-quick-all.md](robust-quick-all.md) |

Base set: `docs/reports/2026-10-real/recommended.json` with the H decisions applied (`base_values`,
docs/decisions.md D-HD-1..4). Workers 4, one sweep at a time.

## 1. Class A's ratio under the "worse window decides" rule (H-3)

| Window | Need (smallest `baseRatioBps[0]` with P(bad debt at claim opening) ≤ 0.5 % in every run) |
|---|---|
| full history (standard, 3 seeds) | **72,500 (725 %)** — 0.32–0.46 % at 725 % |
| last 365 days (standard, 3 seeds) | **192,500 (1,925 %)** — per seed 162,500 / 170,000 / 192,500; 3.8–4.9 % at 725 % |
| **locked value (worse window)** | **192,500 bps (1,925 %)** |

The quick sweep agrees in direction (full 75,000, last365 215,000 → 215,000). The need is set by the
bootstrap members (the hourly and daily block bootstraps of the last year, resampled over the
study horizon); the real last-365-day path itself (`history`) has no bad vault at 725 %.

What 1,925 % means at σ multiplier 1: a $100 class-A vault locks ≈ $1,925 of YEC (0.052 YED per USD
locked, against 0.138 at 725 %), and the round-trip fee share of a `minMint` vault rises from 2.7 % to
**7.2 %** (ARMED, 15 bps + 50 % attestor share) because FEE-1 is levied on the collateral. Both are
owner-level consequences; the rule does not weigh them.

## 2. Owner-set values the evidence does not support (quick sweep, all groups)

Pinned values are kept; the studies report where they fail their own constraints:

- **H-4 fee split (15 / 5,000 bps): `fee_share` and `attestor_revenue` fail in 6/6 runs.** At 725 % the
  ARMED round trip on a `minMint` vault is 2.72 % (policy 2.25 %; the plan's ≈ 2.1 % estimate is low)
  and a seat earns ≈ $41/month against the $50 floor (the plan expected ≈ $80).
- **H-11 halt 300 %: `halt_sys_bad` fails in 6/6 runs** — P(system under water within grace) ≈ 2.9 %
  (real path 3.4 %). With classes B and C disabled the system tolerance is class A's 0.5 %, not the
  1.6 % blend the plan's "300 % meets it" referred to.
- **H-12 `maxMint` $2,500: `maxMint_ok` fails (environment limit G9-ENV-1)**, as the plan expected:
  ≈ 4.5 days of p10 volume at θ 125 %.
- `signalWindow` moves 2,592 → 3,744 in 4/6 quick runs (3,168 consolidated; L3 fractions follow); not
  a hardening decision, re-check in the standard run.
- `divergenceBps`, `divergeBpsAttest`, `dormancyMinBundles`, `deviationBps` are unstable at quick
  budget; see the table.

## 3. Not modelled (upgrade plan §7)

H-6 (valve), H-7 (two-window lock-in), H-9.1 (sunset stand-down) and F-3 are retired; G-6 is not read.
H-10 is moot with B/C disabled. H-1 is modelled in `mint_verdict`; studies without an attestation model
treat the layer as ARMED (D-HD-2).

## 4. To finish H4-a

```bash
S=data/local/frozen-20261004
A="--policy policy/harden-2026-10.toml --data $S/yec-hourly.csv --data $S/yec-daily.csv \
   --data $S/spreads-reconstructed.csv --data $S/pool-shares.csv --data $S/depth.csv"
ybcal robust --out .work/robust/harden-std --budget standard --seeds 3 --windows full,last365 \
  --models bootstrap $A --workers 1 --jobs 6 --cache .work/cache          # ≈ 6 h on 10 cores
ybcal robust --out .work/robust/harden-quick-windows --budget quick --seeds 3 --windows 2021-22,2025-26 \
  --models bootstrap $A --workers 2 --jobs 1
ybcal robust --out .work/robust/harden-quick-stress --budget quick --seeds 2 --windows full,last365 \
  --models regime,martingale $A --workers 2 --jobs 1
```

then the devnet differential (`ybcal devnet validate --strict`) on both node lines with the H1–H3
binaries, and G-1's completed data (spread log to 2026-10-18, longer depth series).

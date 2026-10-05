# Ycash Yellowback (YED) parameters — real-data calibration, October 2026

The first calibration of the Yellowback parameter set on **real** YEC market data, the **real**
mining-pool landscape and **real `ycashd` nodes**, made with `ybcal` (this repository) between
2026-10-03 and 2026-10-05. It supersedes the synthetic example in `docs/example-report/`.

- **Base:** `yellowback::Params` at ycash6 `7702d22` (identical values on ycash-dd v4.5.0 HEAD
  `f78a5f8` and ycash6 HEAD `a862a8a06`; one parameter set serves both node lines).
- **Recommended set:** [`recommended.json`](recommended.json) (digest `0d56f848a635`), the consensus
  patch [`params.cpp.patch`](params.cpp.patch) and the wallet-policy patch
  [`params-patch-release.patch`](params-patch-release.patch). Nothing is applied to either node.
- **How it was chosen:** [`consolidate.py`](consolidate.py) (reproducible; it encodes every value with
  the evidence that chose it) from the robustness sweeps below.

## 1. Summary

**20 of 72 tunable values change; every PLAN §1.4 invariant holds; the set is validated block by
block against real nodes on both node lines (§6).** The changes fall into three kinds:

1. **The price and volatility plumbing was calibrated for a much calmer coin than YEC.** YEC's
   volatility is ≈ 235 %/yr on daily returns (≈ 160–180 % on the node's own σ̂ of the rolling
   median), not the 100 % the shipped `sigmaRefBps` assumes, so the σ multiplier sat at ≈ 1.9×
   permanently. The reference moves to 180 %, the cap to 4.75×, and class A's base ratio rises to
   725 % so that the *locked* collateral is about what the shipped set already demanded in practice,
   but now for the right reason.
2. **The judgement and halt thresholds were tighter than YEC's thin venues allow.** Honest pools
   quoting the median of three venues that routinely disagree by 2–4 % were penalised and halted
   (false REG-4 penalties, false HALT-3 halts, a false PARTICIPATION halt in the real pool mix).
   `deviationBps` 10 → 18 %, `divergenceBps` 20 → 25 %, `peerMin` 5 → 12, `signalWindow`
   1.75 → 2.25 days (thresholds keep their L3 fractions).
3. **Several policy tolerances cannot be met by any parameter value in YEC's real environment.**
   Those are reported as environment limits with the least-harm value and the exposure (§3), not
   hidden as parameter failures. They are the most important part of this report.

**The set is not lock-ready** (§7): two inputs need more calendar time (the two-week live spread log,
the depth series), and five owner-level findings (§3) should be decided before a lock.

## 2. Recommended changes

| Parameter | Shipped | Recommended | Basis (runs: 6 standard + 14 quick) |
|---|---|---|---|
| `pMidWindow` | 576 (12 h) | **1,008 (21 h)** | least harm on NO_PRICE, 6/6 standard and 12/12 of the earlier standard sweep |
| `pSlowWindow` | 2,016 (1.75 d) | **1,152 (1 d)** | same |
| `sigmaRefBps` | 10,000 | **18,000** | median of the standard runs; full-history window 17,000–18,000, last 365 d 23,500–24,500 — the lower (more conservative) end |
| `sigmaMultMaxBps` | 30,000 | **47,500** | the real-history p99 of σ̂ needs 4.5–4.7× |
| `baseRatioBps[0]` (A) | 50,000 | **72,500** | feasible in the most standard runs; the last 365 days need more (§4) |
| `baseRatioBps[1]` (B) | 40,000 | **62,500** | least harm — policy unmeetable (§3.1) |
| `claimThresholdBps` | 11,000 | **12,500** | feasible in every run but three under the excluded regime model |
| `signalWindow` | 2,016 | **2,592** | feasible 6/6 standard; devnet-confirmed (§6) |
| `activationThreshold` / `participationFloor` / `enforcementFloor` / `enforcementResume` | 1,512 / 1,210 / 1,008 / 1,210 | **1,944 / 1,556 / 1,296 / 1,556** | the owner's L3 fractions (75/60/50/60 %) of the new window |
| `peerMin` | 5 | **12** | 20/20 runs |
| `deviationBps` | 1,000 | **1,800** | feasible 19/20; 1,700 falsely penalises stale-venue pools in the 2021–22 regime |
| `accuracyBandBps` | 300 | **100** | 20/20 runs (wallet payee weighting only) |
| `feeMin` | 0.5 YEC | **0.4 YEC** | feasible 20/20 |
| `feeBps` | 25 | **10** | least harm — the attestor revenue floor is unmeetable at today's adoption (§3.4) |
| `divergenceBps` | 2,000 | **2,500** | 2,000 fails the availability budget in 20/20 runs; 2,500 is the narrow band between false halts and crash recall |
| `dormancyMinBundles` | 20 | **15** | feasible 20/20 |
| `walletConfirmations` (patch release) | 6 | **24** | sized against the 21 % second pool (§3.2) |

Everything else stays: the remaining values KEEP on evidence, and the owner-pinned values stay by decision
(`grace` and `abandonBlocks` 30 d — W21; the lock-class bounds — D-R-6; `supplyCapBps` — W20;
`attestFeeBps` — D-3; `attestArmMin`/`attestArmDelay` — D-4; `valveBlocks` — L7;
`globalRatioHaltBps` — W16 + W20).

## 3. Findings no parameter can fix (owner decisions)

### 3.1 Classes B and C are not viable at YEC's volatility
Nothing can liquidate a vault before `lockHeight + grace` (ycash6 `src/yellowback/script.cpp:79-93`,
RED-2 burns the whole debt `state.cpp:515-517`), so a class's base ratio must cover the whole term's
drawdown. At the recommended ratios, P(bad debt when the claim path opens) is **A ≤ 0.5 % over the
full history (tolerance 0.5 %), B ≈ 7 % (1 %), C ≈ 34 % (2 %)**; a bad C vault is short ≈ 70 % of its debt.
Class B meets 1 % only for terms up to ≈ 100 days; class C meets 2 % at no ratio up to 10,000 % in
any price model that reproduces YEC's volatility by horizon (frontier tables: D-RD-COL-1..9,
`docs/evidence/collateral-2026-10/`). Class C's ratio is kept at 300 %: within the registry bounds,
raising it buys only 34 % → 26 % bad-debt probability at half the capital efficiency.
**Levers (owner):** shorten `classMax[1]`/`classMax[2]` (pinned by D-R-6), add a rule that acts
before maturity, or accept the frontier as the price list and relax `max_bad_debt_prob` for B/C.

### 3.2 One pool mines 52 % of blocks
ninjaraider mines 52 % (top-3 87 %, HHI 0.34, 70,000 blocks to height 3,053,805). It holds 72 % of
the price quotes and sets **every** median alone whatever the windows (G1-ENV-1): the policy's
34 % attack tolerance cannot be met; longer windows only delay a rogue majority (pMid captured in
≈ 15 h at 1,008 blocks instead of ≈ 9 h at 576). It can also reorg at will, so no `walletConfirmations`
protects against it (24 protects against the 21 % second pool). Activation is impossible without
it (the rest of the chain is 48 %; D-RD-ACT-1). The attestation layer (PRICE-2, the attestors'
minimum) is the real defence once armed.

### 3.3 Prices are missing for about 1,200 hours a year with three quoting pools
With the three identified operators quoting, background NO_PRICE is ≈ 1,220 h/yr at the
recommended windows (1,750 at the shipped set) against a 6 h/yr budget: no mints and no claims in
those hours. With the four largest payout keys quoting it drops to ≈ 75 h/yr, with every key to ≈ 0.
**Pool adoption and the L9 two-thirds minimum fill decide price availability far more than any
window.** At a 0.6 W fill the real sequence alone drops from 1,526 to 144 h/yr (D-RD-ORA).

### 3.4 Fee revenue cannot meet the attestor floor at today's adoption
At low adoption a seat earns ≈ $33/month at 10 bps against the policy's $50 floor (the bond's
opportunity cost is ≈ $30/month at 20,000 YEC). 20 bps would meet the floor at a 2.25 % round-trip
fee share on a `minMint` mint; the tool chose the user side (D-RD-ATT-8 has the frontier).

### 3.5 The valve trips within hours under a sustained, free attack (`valveBlocks`, pinned L7)
Any matured-vault owner can keep a no-burn sweep in every stock pool's mempool at no cost; at 6
blocks the valve trips within hours with certainty on the real block sequence. 12 is the least-harm
length (trip probability within 30 days ≈ 0.88, a 4 % chance of a node stuck at the 64-note cap).
The real fix is a node-local change (`VALVE_NOTE_CAP`, `index.h:65`, or requiring the heavier
branch's lead to persist) or wider enforcing coverage (G5-DN-VALVE).

### 3.6 Also for the owner
- **Attestor sample configs fail closed on the real venues** (D-RD-ATT-2): `min_sources = 3` with
  exactly three venues and a 1 h `max_age` fails on 53 % of blocks simultaneously; `min_sources = 2`
  fails on 0.2 %. Change the samples in `contrib/yellowback/attest/` (both node lines).
- **`globalRatioHaltBps` (pinned):** P(the system goes under water within grace) at 250 % is
  2.5–6.4 % against a 1.6 % tolerance; ≈ 300 % would meet it but moves W20's 500 % recap gate.
- **The soft-cap gate admits classes B and C above the cap** once σref rises (their locked ratios
  pass 500 %), inverting W20's intent; a class-based gate would restore it (D-RD-COL).
- **`maxMint` vs liquidity:** a $10,000 vault liquidates $12,500 at the claim threshold = ≈ 18 days
  of p10 daily volume and ≈ 65× the ±2 % book depth ($188). Claimants can rarely sell; the
  realistic claimant is a YEC holder (D-RD-COL-6).
- **A wallet bug on both node lines** (F-DEV-1): a mint's carrier change output is not marked spent;
  a second mint or `yed_send` within a few blocks fails with "transaction commit failed".

## 4. How robust is it

| Sweep | Runs | Budget | Purpose |
|---|---|---|---|
| [final-std](robust-final-std.md) | full history + last 365 d × 3 seeds = 6 | standard | the decision |
| [final-quick-windows](robust-final-quick-windows.md) | 2021–22 + 2025–26 regimes × 3 seeds = 6 | quick | regime stability |
| [final-quick-stress](robust-final-quick-stress.md) | regime + martingale models × 2 windows × 2 seeds = 8 | quick | model stress |
| [final-all](robust-final-all.md) | all 20 | — | the consolidation table |

Stable in every run: `peerMin`, `accuracyBandBps`, `dormancyMinBundles`, `walletConfirmations`,
`feeMin`, the pins. Consistent direction, value varies by run: `signalWindow` (2,592–4,032; 2,592 is
feasible in all standard runs and fails only one quick seed), `deviationBps` (1,400–1,800),
`claimThresholdBps` (11,750–13,250). **Sensitive to the data window:** `sigmaRefBps` (the last 365
days were more volatile: 23,500–24,500 vs 17,000–18,000 over the full history) and therefore class
A's ratio (the last 365 days need more than 725 %). The recommendation takes the full history and
the conservative σ reference; a renewal set should re-run on the then-latest year.

## 5. What was fixed in the tool to get here
The synthetic run (`docs/example-report/`) was wrong in ways only real data and real nodes showed.
The decision log (`docs/decisions.md`, D-RD-*) has each; the ones that changed verdicts:
drift was carried from the sample into 5-year bootstraps (D-RD-D1, D-RD-AUD-1); honest pools were
modelled reading one venue instead of the agents' filtered median (D-RD-ATT-1); the bond was priced
at the 2020 price (D-RD-ATT-4); the G5 coalition included a key that left mid-sample
(D-RD-ACT-1); `abandonBlocks` 90 d was an artefact of that (D-RD-COL); a daily series on an hourly
grid was filtered to nothing (D-RD-INF-1); the simulator walked attestation one block behind and the
scenarios assigned every block to one miner (D-RD-DEV-2, -5); owner decisions are now pinned and
environment limits are their own verdict (D-RD-INF-2, -3). The evaluation cache keyed on memory
addresses and the report crashed on short environment records (both fixed 2026-10-04).

## 6. Devnet validation

The recommended set was compiled into `ycashd` on both node lines (ycash6 `7702d22`, binary
`ov-39439d434415c829`; ycash-dd HEAD `f78a5f8`, binary `ov-e40c61bb25e588ae`; mainnet values scaled
to regtest, terms ÷ 1,440) and run through the full differential suite (`ybcal devnet validate
--strict`). Node and simulator agree at every height and every compared field, identically on both
lines ([ycash6](devnet-validate-ycash6.json), [ycash-dd](devnet-validate-ycash-dd.json)):

| Scenario | ycash6 | ycash-dd | Compared |
|---|---|---|---|
| calm | PASS | PASS | 551 heights × 13 fields |
| crash-70 | PASS | PASS | 556 heights |
| hashrate-drop | PASS | PASS | 935 heights |
| attestor-outage-1 | PASS | PASS | 566 heights, 22 vaults, 22 bundles |
| oracle-attack-34 | PASS | PASS | 599 heights |
| feed-outage | PASS | PASS | 635 heights |
| vault-cycle | PASS | PASS | 729 heights, 3 vaults, 990 claimability rows |
| pin | PASS | PASS | 566 heights, 16 / 21 vaults and bundles |
| **suite** | **VALIDATED** | **VALIDATED** | |

Scaling limits (D-WP9-1, D-RD-DEV-7): counts floored at regtest scale (`bondMin` 10 YEC,
`attestArmMin` 5) and ratios such as `attestMaxAge/pFastWindow` cannot keep their mainnet value, so
the devnet confirms the rules' timing and arithmetic at the recommended set, not the mainnet counts.

Earlier confirmations: the simulator matches real nodes on both lines for the shipped set (32/32,
D-RD-DEV); `signalWindow` 2,592 removes a 246-block false PARTICIPATION halt in the real pool mix and
still detects the majority pool going offline (D-RD-ACT-8); the oracle, collateral and attestation
group sets each validated on both lines (D-RD-ORA, D-RD-COL, D-RD-ATT).

## 7. Lock readiness

- [x] Real data behind every price-driven value (6.5 years hourly, 7 years daily, 70,000 blocks of
      pool shares, reconstructed venue spreads; frozen snapshot, [`data-SHA256SUMS`](data-SHA256SUMS))
- [x] Every invariant holds; owner pins honoured; the set validated on real nodes on both lines
- [ ] Two weeks of the live attestor-source spread log (`spreads-live.csv`, running until
      2026-10-18; the reconstruction from candle closes inflates the tail, D-RD-D3)
- [ ] A longer order-book depth series (`depth.csv`, accumulating every 15 minutes)
- [ ] Owner decisions on §3.1–3.6
- [ ] A standard-budget re-run on the final data before the lock (≈ 6 h on 10 cores)

## 8. Reproduce

```bash
S=data/local/frozen-20261004          # verify with: (cd $S && shasum -a 256 -c SHA256SUMS)
A="--policy policy/real-data-2026-10.toml --data $S/yec-hourly.csv --data $S/yec-daily.csv \
   --data $S/spreads-reconstructed.csv --data $S/pool-shares.csv --data $S/depth.csv"
ybcal robust --out .work/robust/final-std --budget standard --seeds 3 --windows full,last365 \
  --models bootstrap $A --workers 1 --jobs 6 --cache .work/cache
ybcal robust --out .work/robust/final-quick-windows --budget quick --seeds 3 --windows 2021-22,2025-26 \
  --models bootstrap $A --workers 2 --jobs 1
ybcal robust --out .work/robust/final-quick-stress --budget quick --seeds 2 --windows full,last365 \
  --models regime,martingale $A --workers 2 --jobs 1
ybcal robust --out .work/robust/final-all --runs .work/robust/final-*/runs/*
.venv/bin/python docs/reports/2026-10-real/consolidate.py
ybcal devnet build    --ycash6 <ycash6 | ycash-dd --ref HEAD> --overlay docs/reports/2026-10-real/recommended.json --term-factor 1440
ybcal devnet validate --ycash6 <…> --overlay docs/reports/2026-10-real/recommended.json --term-factor 1440 --ycashd <built> --parallel 4 --strict
```

One standard run's full report is kept as [`report-std-full-s20261003.md`](report-std-full-s20261003.md)
(every parameter's rule, metrics, neighbours and evidence).

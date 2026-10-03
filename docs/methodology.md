# ybcal methodology

How `ybcal` turns the Ycash Yellowback (YED) parameter set, price data and an owner's risk policy
into a recommendation for every parameter, and how far each recommendation can be trusted. This is
the reasoning document; the code map is [architecture.md](architecture.md), the reader's guide to
the output is [report-guide.md](report-guide.md), and every individual choice is logged in
[decisions.md](decisions.md).

Contents:

1. [The problem](#1-the-problem)
2. [Model fidelity](#2-model-fidelity)
3. [The two-resolution simulator](#3-the-two-resolution-simulator)
4. [Data and scenarios](#4-data-and-scenarios)
5. [Per-group studies](#5-per-group-studies)
6. [Verdicts: materiality, KEEP, CHANGE, PROVISIONAL, BLOCKED](#6-verdicts-materiality-keep-change-provisional-blocked)
7. [Search, robustness and sensitivity](#7-search-robustness-and-sensitivity) (WP-6)
8. [The joint pass and joint sensitivity](#8-the-joint-pass-and-joint-sensitivity)
9. [Design notes versus parameter recommendations](#9-design-notes-versus-parameter-recommendations)
10. [Provenance and lock readiness](#10-provenance-and-lock-readiness)
11. [Known limitations](#11-known-limitations)

---

## 1. The problem

`yellowback::Params` (ycash6 `src/yellowback/params.cpp` at the pin `7702d22`) holds 96 values the
tool tracks: 87 struct fields plus the `params.h` constants the rules read
([parameters.md](parameters.md), generated from the registry). They are not all alike, and the
tool's first job is to say which ones it may touch and what a change costs (D-1):

| Class | Count | What a change costs |
|---|---|---|
| **locked** | 66 | Consensus-shaped among enforcing miners (K10). Changes only through a new parameter set keyed by start height: at or after the previous sunset (L8), or after a full ENFORCEMENT-halted signal window ("freeze, then fix", W19). |
| **excluded** | 9 | Informational, wallet default (L6), wallet/agent policy or node-local. Never hashed or read by a consensus rule, so a patch release can change them. |
| **per-release** | 2 | `startHeight`, `enforceUntilHeight`: derived from the release tip (M14, L8), verified, never optimised. |
| **derived** | 7 | Fixed by a formula from a parent (min-fills, `qHighBps`, `recapRatioBps`, `volPeriodsPerYear`, `attestMaxAge`). The tool tunes the parent and reports the child. |
| **constant** / **meta** | 10 / 2 | Protocol constants and identity. Verified, never tuned. |

Because a locked set is expensive to change after launch, the first set should be right, and
because the owner and the Ycash team are risk-averse, the tool follows the workspace's **minimal
change principle** for parameters too (PLAN §2.3): a value moves off its shipped setting only when
the evidence shows a *material* improvement on its primary metric with no policy violation
anywhere, or when the shipped value violates the policy. Otherwise the answer is KEEP, with the
evidence. The tool never recommends a rule change: findings that tuning cannot fix are reported as
design notes (§9).

The value of the exercise is **risk mitigation** (each locked value is tested against crashes,
oracle attacks, outages and hashrate loss before it is frozen) and **continuity** (owners, pools,
attestors and the developers each get a quantified window: grace, false-halt hours, abandonment
runbook, renewal deadline).

Every decision is a coded rule, never an opinion: the report quotes the rule, the binding
constraint and the metrics at the current and recommended values. Every tolerance in those rules
comes from the owner's policy file ([policy.md](policy.md)).

## 2. Model fidelity

The simulator must compute what the node computes. Three layers make sure it does (WP-1, WP-3..5):

- **Vendored reference.** ycash6's own pure-Python model of the rules
  (`qa/rpc-tests/test_framework/yellowback_model.py`, its attestation helpers and the golden vector
  `yellowback_golden.json`) is copied into `src/ybcal/model/` at the pin, byte for byte except for
  import lines. Each vendored file carries a pin header with the upstream sha256, and
  `ybcal verify` re-hashes it, so any edit fails loudly (D-WP1-1).
- **Exact kernels.** `model/kernels.py` re-expresses every formula of `src/yellowback/math.h` and
  `state.cpp` as Python integers (µUSD, zat, cents, bps; no floats). Where the reference and the
  C++ disagree on degenerate inputs the kernels follow the C++ (D-WP1-2, listed in
  architecture.md). `model/vkernels.py` is the numpy twin, property-tested equal to the scalar
  kernels, with an explicit int64 overflow analysis.
- **Exactness tests.** `ybcal verify` runs 126 checks: the golden replay (state hash, 440
  snapshots), the 97 numeric assertions of `src/test/yellowback_math_tests.cpp`, a seeded parity
  sample and the vendoring hashes. The simulator layers add per-height equality tests against the
  reference model: block-mode medians, σ, haltMask, activation and supply
  (`tests/sim/test_engine_exact.py`); activation and attestation on the golden chain's 440
  snapshots; vault verdicts on 40 crafted cases and three end-to-end chains replayed through
  `YellowbackModel.feed_block`.

`ybcal params check` closes the loop with the source: it re-extracts both the mainnet and regtest
columns from ycash6 (or the committed snapshot) and fails on any drift from the registry, then runs
the PLAN §1.4 invariants.

## 3. The two-resolution simulator

The longest window is 2,016 blocks and the longest vault life is 5 years plus grace (≈ 2.14 M
blocks); Monte Carlo at block resolution over 5 years is out of reach in Python. The engine
therefore has two modes (PLAN §3.3):

- **Block mode** (75-second steps, horizons up to 120 days) runs the SNAP stages in node order:
  REG-4 judgement, activation (ACT-1..6), attestation (seating, selection, bundles, PIN-2,
  dormancy), PIN-1, the three PRICE-1 medians (a wavelet-matrix rolling lower median, D-WP1-4),
  SIGMA-1, MINT-6 supply and the six halt bits. When attestation is simulated, PIN-1/PIN-2 are
  iterated to a fixed point (D-WP8-6). Used by G1, G2, G5, G6, G7 (halts) and G8. 1,000 paths × 90
  days take about 90 s on 4 cores.
- **Hour mode** (48-block steps, horizons up to 6 years) drives the vault book (mints, redemptions,
  claims, emergency claims, sweeps, VOIDs, with the node's exact verdicts) through an **oracle
  transfer kernel**: each median is the rolling lower median of the interpolated true price at a
  fitted lag, bias and noise. The kernel is calibrated from block-mode runs and its error is
  tested on held-out paths (pMint p95 ≈ 50 bps against a 300-bps tolerance). Used by G3, G4, G7
  (supply and crash book) and G9.

Personas (minters with an adversarial `refHeight` choice, absent owners, claimants with a profit
floor, defectors, attackers) are policy-driven (D-WP4-3). G5 and parts of G8 do not simulate at
all where exact mathematics exists: the signal count at a constant share is exactly binomial, and
bundle liveness is a (beta-)binomial tail. Those results carry provenance **judgement** (§10).

## 4. Data and scenarios

**Real data** (needed for a non-provisional recommendation; [data/README.md](../data/README.md)):
≥ 1 year of hourly YEC/USD from CoinGecko, ≥ 2 weeks of `spreads.py log` output, and optionally a
pool-share series and order-book depth. The development sandbox cannot reach the price APIs, so
the owner fetches on a networked machine and drops the CSVs into `data/local/`. Every file's
sha256 goes into the run manifest.

**Synthetic data** (always available): GBM, Merton jump-diffusion, GARCH(1,1)-t, a two-state regime
switch, and a block bootstrap when real returns exist. Presets are YEC-like placeholders (about
120 % annualised volatility, fat tails, multi-month drawdowns over 80 %) with the drift removed;
`ybcal data synth --calibrate FILE` fits any model to real data (D-WP2-4). A correlated per-source
exchange-spread model, a pool model and an attestor outage model complete the inputs
([data.md](data.md)).

**Stress scenarios** ([scenarios.md](scenarios.md)): 15 TOML files under `scenarios/` expand to 36
named scenarios (crashes, slow bleed, pump-and-dump, flash wick, feed outage, stale pools, oracle
attack, attestor outage and capture, hashrate drop, developer and owner absence, sunset without
renewal). The `quick` budget uses the core set; `standard` and `deep` use all of them.

## 5. Per-group studies

Each parameter group has one study module (`src/ybcal/studies/`) implementing the Study protocol:
`space()` (candidates, always including the current set), `evaluate()` (metrics),
`decide()` (verdicts through the shared materiality rule) and `explain()` (the report text). The
detail of each method, metric and rule is in the linked page.

| Study | Parameters owned | Primary metric | Decision rule (short) |
|---|---|---|---|
| [G1](studies/g1.md) price medians | `pFastWindow`, `pMidWindow`, `pSlowWindow` (+ derived min-fills) | J = CVaR₉₅ of the pClaim crash lag + λ · pump overpricing, both normalised by the current windows | minimise J subject to attack share ≥ `attack_share_min`, NO_PRICE ≤ `max_no_price_hours`, HALT-3 recall ≥ `halt_recall_floor` |
| [G2](studies/g2.md) volatility | `volWindow`, `volStep`, `sigmaRefBps`, `sigmaMultMaxBps` (+ derived `volPeriodsPerYear`) | CV of σ̂ in a calm regime | window/step: minimise σ̂ noise subject to responsiveness and K12 recovery ≤ `max_sigma_lag_blocks`; `sigmaRefBps` = median realised σ̂ rounded down, KEEP inside the M14 band; cap = smallest multiple of 2,500 covering the p99 turbulent multiplier |
| [G3](studies/g3.md) collateral and classes | `baseRatioBps[3]`, `classMin/Max[3]`, `claimThresholdBps`, `emergencyRatioBps` | P(bad debt) at claim opening, per class (ratio as tie-break) | smallest ratio (2,500-bps steps) with P(bad debt) ≤ `max_bad_debt_prob[c]`; BLOCKED → least-violating value; class bounds verified; θ = smallest margin that pays claimants; e minimises shortfall below θ |
| [G4](studies/g4.md) grace and abandonment | `grace`, `abandonBlocks` | J = w_owner · P(owner miss) + w_debt · ΔP(bad debt) | grace: minimise J subject to P(miss) ≤ `max_owner_miss_prob`; abandonBlocks: smallest whole-day value ≥ max(grace, runbook) with P(false abandon) ≤ `max_false_abandon_prob` |
| [G5](studies/g5.md) activation and enforcement | `signalWindow`, `activationThreshold`, `participationFloor`, `activationDelay`, `enforcementFloor`, `enforcementResume`, excluded `valveBlocks` | expected false-halt hours/year (exact binomial) | minimise false halts subject to flaps, detection of a drop to `detection_drop_share` within `max_detection_blocks`, lock-in reliability and the §1.4 ordering; `activationDelay` ≥ `operator_upgrade_window_blocks` |
| [G6](studies/g6.md) miner judgement and fees | `peerLag`, `peerMin`, `deviationBps`, `accuracyBandBps`, `payeeWindow`, `feeMin`, `feeBps`, `attestFeeBps`; excluded `nPenalty`, `accuracyWindow`, `payeeTiltBps`, `nReg` | per family: honest p99 deviation; fee share of a `minMint` vault; honest exclusion | judgement: `deviationBps` ≥ `k_dev` × honest p99 and catches a ±`liar_bias_bps` liar; `peerMin` largest with P(not evaluated) ≤ 5 %; fees: lowest that pay pools and attestors under `adoption_case` with fee share ≤ `max_fee_share_small` |
| [G7](studies/g7.md) supply cap and halts | `supplyCapBps`, `globalRatioHaltBps`, `divergenceBps` (+ derived `recapRatioBps`) | cap-bound liquidation demand vs depth budget; false HALT-2 in calm; HALT-3 F1 | largest cap whose liquidation stays under `max_depth_fraction` of p10 volume; halt ratio minimises false halts subject to system tolerance and recall; divergence maximises F1 subject to recall ≥ `halt_recall_floor` |
| [G8](studies/g8.md) price attestation | every v3 attestation field: `divergeBpsAttest`, PIN fields, `nSlots`/`mSelect`/`kSlack`/`bundleMax`, `qLowBps`, `attestInterval` (+`attestMaxAge`), arming, bonds, `ageCap`, dormancy, emergency persistence | per family: ported `spreads.py`/`pinrate.py` targets, bundle bytes, dead-attestor detection time, griefing capital | ported proposal §16 rules (KEEP within materiality); liveness ≤ `max_attest_unavailability`; capture ≤ `max_harmful_capture_prob`; dormancy and emergency trade-offs; others verified |
| [G9](studies/g9.md) amounts and wallet policy | `minMint`, `maxMint`, `minOutput`, `maxOutput`, `residualMinZat`; excluded `carrierValue`, `walletConfirmations`, `DEFAULT_REF_LAG` | none (admissible ranges) | verify: KEEP while the value lies in its closed-form admissible range; else the nearest admissible value; BLOCKED if the range is empty |
| [R](studies/release.md) release | `startHeight`, `enforceUntilHeight` | none (derived) | `startHeight ≥ tip + 16,128` (M14); sunset = start + 420,480 (L8), never past the next network upgrade; renewal deadline and runbook slack reported |

Three decision **kinds** recur (G5–G8 organise each parameter as a *family*, D-WP7c-1):
*optimize* (a primary metric under policy constraints, through the materiality rule), *verify*
(KEEP unless a constraint fails, then the nearest passing value — used where the policy gives a
bound but no cost to trade against) and *rule* (a closed-form target, KEEP within materiality of
it). Excluded parameters get the same analysis but their verdicts carry a **patch-release** note.

## 6. Verdicts: materiality, KEEP, CHANGE, PROVISIONAL, BLOCKED

Every study passes its candidate table through `studies.base.decide_with_materiality`, then
through `final_verdict`:

1. **No feasible candidate** (every evaluated value violates some policy constraint) → **BLOCKED**.
   The study reports the *least-violating* value (G3, G6 and G4 say how they measure it) and the
   binding constraint; the report puts it in a box in the executive summary, and the patch line is
   marked `BLOCKED: least-violating value`. BLOCKED means: relax the policy, or the rule must change
   (see the design notes). It is never softened by provenance.
2. **The current value violates the policy** → **CHANGE** to the best feasible value, whatever the
   materiality (a violation is never "kept").
3. **The best feasible value improves the primary metric by more than `materiality`** (relative,
   default 20 %) → **CHANGE**.
4. Otherwise → **KEEP**. Ties go to the current value, then to the smallest change.
5. `final_verdict`: if the evidence was **synthetic** only, KEEP and CHANGE both become
   **PROVISIONAL** (the recommended value may still differ from the current one; the direction is
   shown, the value is not lock-grade). BLOCKED stays BLOCKED. Evidence with provenance
   `real-data` or `judgement` keeps its verdict.

A group that cannot be imported or raises is reported as **NOT RUN** and its current values stand.

## 7. Search, robustness and sensitivity

*Owner: WP-6 (`src/ybcal/optimize/`). Decisions: D-WP6-1 … D-WP6-7.*

**Candidate sets.** Each parameter is searched on a lattice of its registry step anchored on the
current value, inside the registry's hard bounds, so the current value is always a candidate and
"one step" is a fixed, documented quantity. A study either supplies its own candidate list
(`space()`), or the optimizer generates one: a full factorial grid (thinned to the budget's points
per axis, capped), a Latin hypercube (stratified on every axis, snapped to the lattice,
deduplicated), or a successive-halving run on either. Coupled parameters move together: derived
values are recomputed from their parents, and explicit couplings (e.g. adjacent class bounds) are
declared as rules. Every candidate is checked against the PLAN §1.4 invariants and any feasibility
rule before it is simulated; rejected candidates are counted by the invariant that rejected them and
reported.

**Fidelity.** Successive halving scores all candidates with few Monte-Carlo paths, keeps the best
third (always including the current set), and re-scores the survivors with three times the paths,
ending at the full budget. All candidates see the same random paths (common random numbers), so
differences between them are not sampling noise, and results do not depend on how many processes
evaluated them.

**Robustness.** Over a scenario ensemble a candidate is summarised by its mean, a quantile, its
CVaR (the mean of the worst `1 − α` probability mass) or its worst case. The default final rule is
**minimax regret**: in each scenario, regret is the gap to the best policy-feasible candidate in that
scenario; the chosen value minimises the largest regret. Policy tolerances are hard constraints on
an aggregate of a metric (e.g. CVaR of bad-debt probability ≤ the class limit). If no candidate meets
every constraint, the least-violating one is reported as **BLOCKED**. Ties go to the current value,
then to the smallest change.

**Trade-offs.** Where a study balances several objectives, the tool reports the Pareto front
(non-dominated candidates), its knee (the best balance after normalising each objective), and the
policy-feasible candidate that is best on the parameter's primary metric — the one recommended.

**Why not the neighbours.** After a study decides, each tunable parameter is moved one step up and
one step down from the recommended set (all else equal) and re-scored. The report shows those
scores; a neighbour that scores better says whether the gain is within materiality (so the
minimal-change rule kept the recommendation) or beyond it.

**Sensitivity.**

* *One at a time:* sweep a parameter, classify each segment by its elasticity — flat (|e| < 0.1),
  moderate, or steep (|e| ≥ 1) — and state it in a sentence such as "P(owner miss) is flat between
  20 and 40 days; P(miss) dominates below 20 days."
* *Morris screening:* random one-factor-at-a-time trajectories on the parameters' integer lattices;
  μ* (mean absolute effect) ranks influence, σ flags non-linearity or interaction.
* *Sobol indices:* first-order S1 (share of output variance explained by a parameter alone) and
  total-order ST (including interactions), estimated with Saltelli's design and Jansen's estimators
  on ±1 step around the joint recommended set, with bootstrap confidence intervals. A parameter
  whose ST is below `policy.insensitive_total_order` is labelled *insensitive* (in the joint pass
  the label is informational, §8). The estimators are validated on the Ishigami function, whose
  indices are known exactly.

## 8. The joint pass and joint sensitivity

The groups are coupled (G1's windows feed G3's oracle kernel, G2 scales G3's ratios, G6's fees
enter G3 claimant profit and G9's floors, G5 drives G4's false abandonment). `ybcal recommend`
therefore runs a **coordinate descent** (D-WP8-1):

1. Groups run in dependency order G1 → G2 → G5 → G3 → G4 → G7 → G6 → G8 → G9 → R, each starting from
   the shipped set with every earlier group's recommendation applied.
2. A group whose recommendation would break an invariant of the joint set is *not applied*, and the
   history says why.
3. Rounds repeat until nothing moves, at most `max_rounds_joint` (3) times. One evaluation cache is
   shared, so repeated sets are free.
4. At the end every Recommendation is **restated against the shipped set**: `current` is the
   shipped value, `recommended` the joint value, and KEEP/CHANGE are re-labelled accordingly.

Robust (minimax-regret) selection happens inside each study over its own scenario ensemble; there is
no second cross-group robust pass (D-WP8-7).

**Joint sensitivity** (D-WP8-2..4) ranks the recommended set's parameters by their influence on four
system metrics computed by a fast top-risk model: system P(bad debt) (mean over classes), hours of
price halts (NO_PRICE or HALT-3), false activation-halt hours, and the oracle attack share. Factors
are ±1 registry step, with coupled and greedily repaired moves so that every design row is
admissible. At the quick budget the factors are whole study groups (per-parameter Sobol needs a
larger budget, or `ybcal sensitivity --params …` on a subset), with a per-parameter tornado inside
each group. The *insensitive* label never overrides a study's verdict (D-WP8-3): most attestation
and fee parameters do not enter the four system metrics at all, and their own study's evidence
stands.

## 9. Design notes versus parameter recommendations

A parameter recommendation answers "which value of this field, under the current rules". Some
findings are about the rules themselves, and no value can fix them. Those are **design notes**,
reported in their own report section (§5 of the report) with an id, finding, evidence, consequence
and a suggested fix, and never emitted as a parameter change (PLAN §11, D-WP7b-7, D-WP8-10).
Examples raised on synthetic data at the shipped values (all PROVISIONAL, from WP-4/WP-7):

- **No liquidation before `claimHeight`** (fact 1.5-1): collateral must cover the debt for the whole
  term plus grace, so classes B and C need far higher base ratios than shipped to meet 1 % / 2 %
  P(bad debt) — G3 reports BLOCKED with the least-violating ratio.
- **Early supply cap** (fact 1.5-2): `issuedZat` counts subsidy since `startHeight`, so MINT-6
  keeps classes B and C mostly closed for a long time while class A bypasses the soft cap (W16/W20).
- **RED-5 residual is always 0 under RED-4(a)**, and RED-4(b) pays a claimant only when YED trades
  at a discount.
- **FEE-1 is charged on collateral**, so the fee share of the debt is `2·feeBps·ratio` regardless of
  vault size.
- **Classes cannot be split or merged** (`NUM_CLASSES = 3`), so within-class heterogeneity is a note.

Whether to act on a design note is the owner's decision (PLAN §12.4).

## 10. Provenance and lock readiness

Every metric and recommendation carries one provenance tag (PLAN §2.5):

- **real-data** — the decision used data the owner supplied (prices, spreads log, pool shares,
  depth) or, for the release study, a supplied tip;
- **synthetic** — only the placeholder models and scenarios; the verdict becomes PROVISIONAL;
- **judgement** — exact mathematics given an assumed input from the policy (e.g. the G5 binomial at
  `expected_enforcing_share`); neither measured nor synthetic.

Every run writes a manifest (ybcal version, ycash6 pin, data sha256s, policy hash, seed, budget,
command), and `ybcal recommend --manifest FILE` reproduces it, reporting any input whose hash
changed.

The report's **lock-readiness checklist** (report-guide.md) requires: every locked parameter
recommended, non-provisional and backed by real data (while `require_real_data_for_lock = true`);
nothing BLOCKED; the recommended set passing every §1.4 invariant; the release study clean. The
patch check and devnet validation are advisory. **A synthetic-only run is never lock-ready, by
design.** Milestone M7 (real data, no PROVISIONAL on locked parameters) is the point at which the
report can be used to lock a set.

## 11. Known limitations

- **Synthetic placeholders.** Until the owner supplies real data, every price-driven verdict is
  PROVISIONAL and rests on YEC-like presets, not YEC. The worst-case aggregation across presets
  (`ensemble_agg = "worst"`) makes the synthetic answers deliberately conservative.
- **Assumptions are policy.** Behavioural and market inputs the tool cannot measure (owner absence,
  adoption, pool and attestor outages, enforcing share, YEC volume, YED premium, defectors) are
  policy keys with placeholder values; [policy.md](policy.md) lists them under "Placeholders to
  replace before a lock". `yec_daily_volume_p10_usd` is unset by default, so the G7 supply-cap and
  G9 `maxMint` depth checks fall back to a $25k placeholder or are skipped.
- **Policy wiring gaps.** `diverge_spread_multiplier` is documented but the ported `spreads.py`
  analysis uses its built-in 3.0, and `hour_kernel_tolerance_bps` is not read by any study (both
  in policy.md "Known gaps").
- **Devnet validation was not run in this sandbox** (milestone M6). The depends download hosts are
  blocked here, so `ybcal devnet build` reports *skipped*; the simulator's fidelity rests on the
  reference-model exactness tests (§2), and every report shows "devnet: skipped / not validated".
  The owner runs `ybcal devnet validate` on a machine that can build or download `ycashd`
  ([devnet.md](devnet.md)).
- **CI binary version skew.** The only prebuilt binary (CI run 37081639884) is built from
  `94bafa4`, eight commits before the pin `7702d22`. Its regtest column is identical, but mainnet
  `abandonBlocks` differs (4,032 vs 34,560) and it lacks the W20 soft supply cap and the
  `mintingAllowed`/`supplyCapReached` RPC fields. It is usable for stock-column runs with
  `--allow-version-skew` and `-yellowbacksupplycapbps=0`, not for runs with a cap (D-WP9-6).
- **Hour-mode resolution.** In hour mode the 40-block `refHeight` choice collapses to one snapshot
  (the kernel noise stands in for it), and ARMED vault-book runs use a bundle proxy for attested
  prices (D-WP4-4).
- **Joint pass cost.** Cache keys are whole-set digests, so a later round re-evaluates groups whose
  inputs did not really change (D-WP8-9); the quick budget is close to its 10-minute target with
  all ten studies. `--max-rounds 1` halves it.
- **Sensitivity at quick budget** is per study group, not per parameter (D-WP8-4), and is computed
  on a fast top-risk model meant for ranking, not for deciding values (D-WP8-2).
- **Header constants** (`DEFAULT_REF_LAG` in `params.h`) are recommended and listed, never patched.

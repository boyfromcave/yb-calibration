# The risk policy (`policy/default.toml`)

The policy is the owner's risk appetite, written down. Every tolerance a study checks, every weight
in an objective and every behavioural assumption the simulator needs comes from it, so the tool
never decides how much risk is acceptable on its own (PLAN §2.4). The report prints the policy it
used verbatim (§2 of the report) and records its hash in `manifest.json`.

**Every value in the shipped file is a starting tolerance for the owner to confirm, not a
decision** (PLAN §12). Several of them are placeholders that real data or an owner decision must
replace before a lock; they are flagged below.

```bash
cp policy/default.toml policy/mine.toml          # edit
ybcal params check --policy policy/mine.toml     # policy-dependent invariants (qLow vs entity share, 4·feeMin floor)
ybcal recommend --policy policy/mine.toml --budget standard
```

## How the file is read

- `ybcal.config.Policy` is a frozen dataclass whose defaults equal `policy/default.toml`
  (`tests/test_config.py` keeps the two equal). `Policy.load(path)` flattens the TOML: **section
  headers only group keys**, every key is unique across sections, and an unknown or duplicated key
  raises (a typo never passes silently).
- A table whose name is itself a field is that field's value: `[risk.max_bad_debt_prob]` and
  `[adoption.adoption_scenarios]`.
- Three keys default to *unknown* (`None`): `yec_daily_volume_p10_usd`, `reference_price_usd`,
  `next_upgrade_height`. TOML has no null, so they are commented out in the shipped file; add the
  line to set them.
- Probabilities and shares are fractions (`0.01` = 1 %). Money is USD. Floats are fine: a policy
  value is a tolerance, never a consensus quantity.
- Studies read some assumption keys with `getattr(policy, key, default)`, so a policy file written
  for an older ybcal still loads (missing keys take the default).

Columns below: **Default** as shipped; **Read by** names the study (G1–G9, R = release) or layer
that reads the key; **How to choose** is guidance for the owner. "Tighter" means a smaller
tolerated risk, which usually pushes recommendations towards more collateral, longer windows or
BLOCKED verdicts.

The fastest way to see how much a key matters is to rerun one group with a changed policy
(`ybcal study G3 --policy policy/mine.toml`) and compare the two mini reports.

---

## `[general]`

| Key | Default | Unit | Meaning | Read by | How to choose |
|---|---|---|---|---|---|
| `materiality` | 0.20 | fraction | Minimum relative improvement of a parameter's primary metric before the tool recommends moving off the current value (PLAN §2.3). Below it the verdict is KEEP. A current value that violates the policy is always moved, whatever the materiality. | every study (`decide_with_materiality`), optimizer runner, report | The minimal-change dial. Raise it (0.3–0.5) when a locked change is expensive to coordinate; lower it (0.05–0.1) only for a first set that is not yet deployed. It never affects a value that breaks the policy. |
| `seed` | 20261003 | integer | Default RNG seed of every run; `--seed` overrides. All draws come from `Env.rng_for(...)`, so results do not depend on worker count or order. | every run | Keep it fixed for reproducibility. To check that a verdict is not sampling luck, rerun with two or three other seeds. |
| `require_real_data_for_lock` | true | bool | Lock-readiness requires every locked parameter's recommendation to rest on real data (non-PROVISIONAL). | report (lock-readiness) | Leave `true`. Setting it `false` makes a synthetic-only report look lock-ready, which defeats the provenance rule. |

## `[risk]` and `[risk.max_bad_debt_prob]` — collateral (G3)

| Key | Default | Unit | Meaning | Read by | How to choose |
|---|---|---|---|---|---|
| `max_bad_debt_prob` | A 0.005, B 0.01, C 0.02 | probability per class | Maximum P(collateral is worth less than the debt when the claim path opens at `lockHeight + grace`) for a class's vaults. The core solvency tolerance (PLAN §5.3, §12.1). | G3 (`baseRatioBps`), G7 (system tolerance for HALT-2), robust constraints | The single most consequential key. Longer classes get looser limits because their terms are longer. At the shipped ratios B and C miss these limits by an order of magnitude on synthetic data (fact 1.5-1), so G3 reports BLOCKED; choose between looser limits, higher ratios or a rule change knowingly. |
| `class_heterogeneity_max` | 0.50 | fraction | Largest relative spread of P(bad debt) between the shortest and longest term within one class before G3 suggests splitting or merging classes. With three fixed classes that suggestion is a design note. | G3 | Lower it to be warned about uneven classes earlier. It never changes a value. |
| `term_distribution` | `"uniform"` | enum: uniform, short-heavy, long-heavy | How lock terms are spread within each class when P(bad debt) is averaged over terms. | G3, G4, joint pass (top-risk model), agents | Use real wallet data if any exists. `long-heavy` is the conservative choice (long terms carry most drawdown). |
| `sigma_mult_at` | `"median"` | enum: median, p90 | The σ multiplier assumed when sizing base ratios: the path's median or its 90th percentile. | G3, G4 | `median` matches what a typical mint pays. `p90` credits the multiplier more and so recommends lower base ratios; use it only if you trust the σ multiplier to be high when it matters. |
| `price_drift` | `"centred"` | enum: centred, martingale, model | Drift convention of the long-horizon solvency price paths (the G3/G4 hour ensemble and the joint top-risk model; every other study is centred, D-WP2-5). `centred`: expected log drift 0 (the median price is flat). `martingale`: expected price flat, so the log price bleeds σ²/2 a year (−72 %/yr at 120 % vol). `model`: each preset's own drift, and a real bootstrap's sample drift (D-RD-AUD-1). | G3, G4, joint pass | `centred` takes no view on direction. `martingale` is a stress: at YEC-like volatility it makes multi-year classes look near-certain to fail whatever the ratio. Never use `model` with real data: one year's drift (+176 %/yr to 2026-10) would decide the ratios. |
| `real_price_model` | "bootstrap" | name | How studies model real price data: `bootstrap` = demeaned stationary block bootstrap of the hourly series (default); `regime` / `garch` = that model fitted on the longest real series (`price_daily` when given) and centred. Synthetic runs ignore it. | G1, G2, G3, G4, G6, joint risk model (`g1_price_windows.real_model`) | The robustness harness (`ybcal robust --models`) varies it; a lock-grade value should not depend on it (D-RD-INF-5). |

## `[claims]` — claimant incentive (G3)

| Key | Default | Unit | Meaning | Read by | How to choose |
|---|---|---|---|---|---|
| `claimant_min_profit_bps` | 200 | bps of vault value | Net profit a claimant must expect after fees and slippage before they act. `claimThresholdBps` is the smallest margin that clears it. | G3, agents | Ask likely claimants (pools, arbitrageurs). Higher values push `claimThresholdBps` up. |
| `claimant_slippage_pctl` | 90 | percentile | Percentile of liquidation slippage used for claimant profit (worse = higher percentile). With a depth file, G3 reads the bad-side percentile of ±2 % depth. | G3 | 90 is a stress choice. 50 describes a typical claim; 95–99 sizes for thin markets. |

## `[oracle]` — price medians and volatility (G1, G2)

| Key | Default | Unit | Meaning | Read by | How to choose |
|---|---|---|---|---|---|
| `attack_share_min` | 0.34 | hash share | Smallest colluding hash share that may be able to move a median. Windows must resist any coalition below it (V16 binomial, plus a simulated check). | G1 | Set it from the observed pool distribution: comfortably above the largest single pool, and ideally above any plausible two-pool coalition. Raising it lengthens the windows (more lag). |
| `max_no_price_hours` | 6.0 | hours/year | Maximum expected hours a year with NO_PRICE set (minting stops) under the assumed per-pool outages. | G1 | A continuity budget. Tighter values favour fewer required tags (shorter fills). |
| `attack_moved_tol` | 0.05 | fraction of attack blocks | Largest share of attack blocks in which a coalition at `attack_share_min` moves the harmful price by half its bias (D-WP7a-2). | G1 | 0.05 accepts rare, brief nudges. Use 0.01 for a strict reading of "cannot move". |
| `pool_outage_rate_per_day` | 0.0333 (1 per 30 days) | events/day per pool | Per-pool feed outage frequency used for NO_PRICE availability and the σ cap trap (D-WP7a-3). | G1, G2 | Measure from `spreads.py` logs or pool operators' history. Placeholder until then. |
| `pool_outage_mean_hours` | 4.0 | hours | Mean length of a per-pool feed outage. | G1, G2 | As above. Long outages hurt availability and K12 much more than frequent short ones. |
| `pump_overpricing_lambda` | 0.5 | weight | Weight of pump overpricing (mint at the top) relative to crash-lag CVaR in G1's objective. | G1 | Higher favours longer windows (slower to follow a rally). 0 cares only about crash tracking. |
| `crash_lag_cvar_alpha` | 0.95 | quantile level | CVaR level of the pClaim crash tracking lag in G1's objective. | G1 | 0.95 averages the worst 5 % of crash paths. 0.99 is more tail-focused but noisier at quick budget. |
| `max_sigma_lag_blocks` | 4032 | blocks (2 × 2,016) | Longest time the σ multiplier may take to reach 90 % of a new regime, and to recover from a feed-outage cap trap (K12). | G2 | Shorter forces shorter `volWindow` (noisier σ̂). About one to two weeks is the plan's range. |
| `sigma_ref_round_bps` | 500 | bps | Rounding step for the recommended `sigmaRefBps`. | G2 | Presentation only; 500 matches the shipped value's granularity. |
| `sigma_mult_cap_pctl` | 99 | percentile | Percentile of the turbulent-regime multiplier that `sigmaMultMaxBps` must cover. | G2 | Lower values give a smaller cap (less collateral in a K12 trap) but leave extreme regimes under-collateralised. |
| `sigma_accept_band` | [1.0, 1.5] | multiplier range | M14 acceptance band for the median multiplier at realised volatility: `sigmaRefBps` is kept while σ̂₅₀ / σref lies inside it. | G2 | The spec's own band; change only with a spec change. |
| `hour_kernel_tolerance_bps` | 300.0 | bps (p95 relative error) | Tolerance of the hour-mode oracle transfer kernel against block mode (D-WP3-5). | `engine.kernel_error(tolerance_bps=…)` → `within_tolerance`; the kernel test passes it | Leave as is. Measured error is about 50 bps. |

## `[activation]` — activation and enforcement (G5)

| Key | Default | Unit | Meaning | Read by | How to choose |
|---|---|---|---|---|---|
| `max_false_halt_hours_per_year` | 24.0 | hours/year | Maximum expected hours a year of false PARTICIPATION or ENFORCEMENT halts at `expected_enforcing_share`. | G5 | A continuity budget: a false PARTICIPATION halt stops minting for everyone. |
| `expected_enforcing_share` | 0.80 | hash share | Hash share expected to run enforcing (signalling) nodes. Also the tagging share in the G1/G2 oracle model and the share used for false abandonment. | G1, G2, G4, G5, G6, oracle model | The most important activation input. Survey pools before a lock; use a conservative (lower) figure. |
| `detection_drop_share` | 0.45 | hash share | A real drop of the enforcing share to this level must be detected… | G5 | Just below one half: enforcement without a majority is what ENFORCEMENT is for. |
| `max_detection_blocks` | 4032 | blocks | …within this many blocks (p95). | G5 | Longer allows bigger windows (fewer false halts); shorter protects against a slow-moving minority. |
| `max_flaps_per_year` | 2.0 | cycles/year | Maximum halt/clear cycles a year at the expected share. | G5 | Each flap is an operator-visible event; keep it low. |
| `operator_upgrade_window_blocks` | 2016 | blocks | `activationDelay` must be at least this (time for operators to upgrade after lock-in). | G5 | Ask pool operators how long an upgrade takes; one week is common. |
| `orphan_rate` | 0.005 | fraction of blocks | Natural orphan rate, used for valve false trips and for VOID risk of `DEFAULT_REF_LAG`. | G5, G9 | Measure from a node's stale-block count. |
| `max_valve_false_trips_per_year` | 1.0 | trips/year | Tolerated natural false trips of the work valve (`valveBlocks`, excluded / node-local). | G5 | The valve is local, so a trip costs one node; one a year is mild. |
| `enforcing_pools` | [] | payout-key prefixes | The operators expected to enforce, matched against the pool-share log's payout keys; G5 replays their real per-block signal sequence (`ybcal.sim.landscape`). Empty: the largest operators in order until their share reaches `expected_enforcing_share`. | G5 | Name the operators who have committed (D-RD-ACT-1). The auto rule can pick a key that leaves the chain mid-sample. |
| `activation_reach_days` | 0.0 | days | `activation_reliability` is the probability of lock-in within this many days of the start height (on the real landscape: the bootstrapped signal sequence). 0 = at the first eligible height (the original rule). | G5 | A launch window: how long the owner will wait for lock-in after the start height (D-RD-ACT-3). |
| `max_spurious_lock_prob` | 1.0 | probability | With a pool-share log: the largest probability that a coalition of the largest operators whose mean share is below 60 % (it cannot hold the floors once ACTIVE) locks in within the reach (e.g. a multi-day hop of an auto-switching pool). 1 = not a constraint. | G5 | A launch-safety tolerance; ACT-2 is a single-window test, so only a longer window lowers it (D-RD-ACT-4). |
| `valve_attack_days` | 0.0 | days | With a pool-share log: `valve_minority_trip_max` bounds the probability that a sustained ACT-7 race attack (a rule-breaking transaction kept in every stock mempool, races back to back) trips the work valve within this many days, on the real block sequence. 0 = per race (gambler's ruin). | G5 | The attacker's patience; a matured-vault owner can keep a sweep in stock mempools for free (D-RD-ACT-5). |

## `[abandonment]` — abandonment window and release cadence (G4, R)

| Key | Default | Unit | Meaning | Read by | How to choose |
|---|---|---|---|---|---|
| `max_false_abandon_prob` | 0.001 | probability/year | Maximum P(ENFORCEMENT stays set for `abandonBlocks` without a real abandonment). | G4 | A false abandonment lets every owner sweep collateral without burning YED; keep it very small. |
| `runbook_operator_buffer_blocks` | 4608 | blocks (4 days) | Operator buffer in the freeze-then-fix runbook (detect + W19 window + M14 lead + buffer). `abandonBlocks` must exceed the runbook. | invariants (`abandon_ge_runbook`), G4, G5, R | How long the developers need, beyond protocol minimums, to ship a fix. Be honest; this is the dev team's own continuity commitment. |
| `release_lead_blocks` | 16128 | blocks (two weeks) | M14 release lead: `startHeight ≥ release tip + lead`. A protocol fact mirrored here for the runbook. | invariants (`release_lead`), G4, G5, R | Do not change unless the spec's M14 changes. |
| `renewal_lead_blocks` | 210240 | blocks (~6 months) | W18 renewal deadline: a renewal must ship this long before the sunset. | R | Shorter gives more time on one set but less time to react if renewal slips. |

## `[owner_absence]` — honest owners and grace (G4)

| Key | Default | Unit | Meaning | Read by | How to choose |
|---|---|---|---|---|---|
| `owner_absence_median_days` | 7.0 | days | Median length of an absence spell (log-normal). | G4, agents | A behavioural guess. Survey users or use wallet telemetry if any. Placeholder. |
| `owner_absence_sigma` | 1.0 | σ of ln(days) | Log-normal shape of absence spells: larger means heavier tails (more very long absences). | G4, agents | 1.0 puts about 2 % of spells above 60 days. Raise it if users are casual holders. |
| `owner_absence_rate_per_year` | 1.0 | spells/year | Expected absence spells per owner per year. | G4, agents | As above. |
| `max_owner_miss_prob` | 0.01 | probability | Maximum P(an honest owner is absent through the whole window between lock and claim). Grace must meet it. | G4 | The continuity promise to owners. Tighter lengthens grace, which adds drawdown exposure (G3/G4 trade-off). |
| `w_owner` | 1.0 | weight | Weight of P(miss) in G4's grace objective. | G4 | Raise to favour owners over solvency. |
| `w_debt` | 1.0 | weight | Weight of the incremental P(bad debt) from grace in G4's objective. | G4 | Raise to favour solvency over owners. |

## `[judgement]` — miner judgement (G6)

| Key | Default | Unit | Meaning | Read by | How to choose |
|---|---|---|---|---|---|
| `k_dev` | 1.5 | multiplier | `deviationBps` must be at least `k_dev` × the p99 honest deviation. | G6 | Safety margin against honest pools being penalised. 1.2–2.0 is the sensible range. |
| `max_not_evaluated_prob` | 0.05 | probability | Maximum P(a quote has fewer than `peerMin` peers and is not judged). | G6 | Lower forces a smaller `peerMin`, which weakens the peer comparison. |
| `expected_pool_count` | 6 | pools | Tagging pools expected on mainnet. | G1, G2, G6, oracle model | Count pools from a pool-share CSV (`data/local/pool-shares.csv`). |
| `max_false_penalty_rate` | 0.01 | fraction of honest pool-blocks | Maximum share of honest pool-blocks judged bad by REG-4. | G6 | False penalties cost honest pools fee income; 1 % is the plan's default. |
| `pool_feed` | "agent" | mode | How an honest pool builds its quote in the REG-4 and MINT-10 models: `agent` = the shipped agent's median of the venues (`yellowback_price.py` `PriceFeed.aggregate`), `venue` = one venue per pool (the WP-7d model). | G6, G8 | Keep `agent` (what the shipped software does, D-RD-ATT-1); `venue` is the misconfigured-pool sensitivity. |
| `agent_min_sources` | 3 | sources | The agents' `min_sources` (fail closed below it; `min_venues` = min(2, this)). | G6, G8 | The sample configs ship 3; with three venues of which one is often stale that fails closed often — see D-RD-ATT-2. |
| `agent_outlier_bps` | 1000 | bps | The agents' outlier filter: a source further than this from the median is dropped. | G6, G8 | The shipped value. |

## `[fees]` — fee levels and incentives (G6, G8, G9)

| Key | Default | Unit | Meaning | Read by | How to choose |
|---|---|---|---|---|---|
| `max_fee_share_small` | 0.02 | fraction | Maximum round-trip fee as a share of a `minMint` vault's debt. | G6, G9 | A user-experience limit. Note FEE-1 is charged on collateral, so the round-trip fee as a share of debt is `2·feeBps·ratio` at any size: class A pays 2.5 % at the shipped `feeBps`, above the 2 % default. Expect the fee family to be BLOCKED or to cut `feeBps` unless this is loosened (G6 design note). |
| `pool_min_monthly_revenue_usd` | 50.0 | USD/month per pool | Minimum fee revenue an enforcing pool must earn under `adoption_case`. | G6 | Ask pool operators. Combined with `pool_operating_cost_usd_month`. |
| `attestor_min_monthly_revenue_usd` | 50.0 | USD/month per seated attestor | Minimum attestation-fee revenue a seated attestor must earn. Also bounds the bond's opportunity cost in G8. | G6, G8 | Ask likely attestors. |
| `pool_operating_cost_usd_month` | 20.0 | USD/month | A pool's marginal cost of running enforcement; revenue must clear it. | G6 | Pool operators' estimate. |
| `bond_opportunity_cost_apr` | 0.05 | per year | Annual opportunity cost of capital locked in an attestor bond. | G6, G8 | A market rate for idle YEC capital. |

## `[adoption]` and `[adoption.adoption_scenarios]` (G6, G8)

| Key | Default | Unit | Meaning | Read by | How to choose |
|---|---|---|---|---|---|
| `adoption_case` | `"low"` | enum: low, mid, high | The adoption scenario fee decisions must clear. | G6, G8, agents | `low` asks fees to work even if Yellowback stays small, which is the safe default. |
| `adoption_scenarios` | low: $50k supply, 2 mints/day, 1 redeem/day; mid: $500k, 10, 6; high: $5M, 50, 30 | USD, per day | YED outstanding and daily mint/redeem activity per scenario. | G6, G8, agents | Placeholder demand. Replace with the owner's honest expectation. |

## `[supply]` — supply cap and halts (G7, G9)

| Key | Default | Unit | Meaning | Read by | How to choose |
|---|---|---|---|---|---|
| `max_depth_fraction` | 0.10 | fraction | Largest liquidation volume (worst crash, or one `maxMint` vault) as a share of p10 daily YEC volume. | G7, G9, metrics | How much of a bad day's volume a forced sale may take. Lower is safer; needs real volume data to mean anything. |
| `max_class_closed_days` | 90.0 | days | Longest time classes B/C may stay closed by the early supply cap (fact 1.5-2) before a design note is raised. | G7 | A continuity tolerance; it only controls whether a note is raised. |
| `halt_recall_floor` | 0.90 | fraction | Minimum recall of HALT-3 on crash scenarios (G7 `divergenceBps`) and of HALT-2 against the alarm ratio; G1 also keeps HALT-3 working at this recall. | G1, G7 | Higher demands halts that always fire in crashes, at the cost of more false halts in calm markets. |

## `[attestation]` — price attestation (G8)

| Key | Default | Unit | Meaning | Read by | How to choose |
|---|---|---|---|---|---|
| `attestor_uptime` | 0.95 | fraction | Per-attestor uptime assumed for bundle liveness. | G8, scenario library | Measure on testnet attestors. Lower uptime pushes `kSlack` up. |
| `attestor_outage_correlation` | 0.10 | correlation | Pairwise correlation of attestor outages (beta-binomial liveness). | G8 | Raise it if attestors share hosting or a price source. |
| `max_attest_unavailability` | 0.01 | probability | Maximum P(minting refuses for want of a bundle). | G8 | A continuity budget once ARMED. |
| `max_single_entity_weight_share` | 0.25 | fraction of weight | Largest attestor weight share one entity is assumed to hold. `qLowBps` must exceed it (invariant `qlow_vs_entity`). | invariants, G8 | Set from the expected attestor roster. If one entity may hold more than 1/3, the shipped `qLowBps` fails. |
| `diverge_spread_multiplier` | 3.0 | multiplier | The proposal's spreads.py rule, reported as `div.target_spreads`: this × the worst source pair's p95 spread (proposal §16); no longer the decision rule (D-RD-ATT-3). | G8 (`spreads_inputs` → ported `analyze_spreads(multiple=…)`) | Keep 3.0 (the proposal's value) unless the owner wants more headroom. |
| `max_mint10_refusal_prob` | 0.01 | probability | Maximum share of honest mints in calm that MINT-10 refuses, measured on the aggregated feeds (pools' pFast vs attestors' aMint, both from the shipped agents). | G8 | A refused mint waits for fresher attestations (the wallet evaluates MINT-10 first); 1 % costs a minter one block in a hundred. |
| `min_bond_cap_years` | 1.0 | years | `bondMin` security test: the capital a single attestor needs to move aMint up (seats of just over `bondMin`, as many as it takes for P(aMint up 10 %) ≥ ½) must cover this many years of the MINT-6 cap's growth (supplyCapBps × subsidy since startHeight). Both sides are in YEC, so the test does not move with the YEC price. | G8 | One year: a parameter set lives one year (enforceUntilHeight = start + 420,480), so its successor can re-tune the bond with observed attestor revenue before the cap outgrows it. Theft also needs the pools' xMint pushed up (pMint = min), so this is a one-population bound. |
| `min_harm_capture_seats_share` | 0.75 | share of nSlots | `qLowBps` theft test: an adversary splitting its YEC into seats of just over `bondMin` must need at least this share of the seats to move aMint up 10 % with probability ½ (exact W9 selection and bundle kernels). | G8 | 7 of 9 seats (0.78) at `qLowBps` 3,333; any qLow above one third lets 6 of 9 do it. |
| `pin_low_move_fraction` | 0.05 | fraction of windows | If fewer `pinWindow` windows than this have a ≥ `pinDeltaBps` move, G8 lowers `pinDeltaBps` to 200–300. | G8 | The proposal's 5 %. |
| `max_false_pin_prob` | 0.01 | probability/day | Maximum P(an honest pool or attestor is marked pinned) per day. | G8 | Tighter raises `pinMinTags`/`pinMinBundles` (slower detection of frozen feeds). |
| `max_false_ejection_prob` | 0.01 | probability/year | Maximum P(an honest attestor is ejected for dormancy) per year. | G8 | Ejection costs the honest attestor its seat; 1 %/yr is mild. |
| `max_dead_detection_blocks` | 32256 | blocks (28 days) | Longest time to eject a dead seated attestor. | G8 | Shorter evicts dead attestors faster but raises false ejections. |
| `attest_relay_budget_per_block` | 2.0 | attestations/block per attestor | Relay load the network tolerates; bounds `attestInterval` from below. | G8 | A network-capacity judgement. |

## `[amounts]` — amounts and wallet policy (G6, G9, invariants)

| Key | Default | Unit | Meaning | Read by | How to choose |
|---|---|---|---|---|---|
| `worst_price_usd` | 100.0 | USD/YEC | Highest YEC price the policy covers: `4·feeMin` must fit a `minMint` vault there (invariant `fee_floor_mintable`), and dust/fee bounds are taken there. | invariants, G6, G9 | `PRICE_MAX` ($100) is the most conservative. A lower, realistic ceiling loosens `minMint`/`feeMin` bounds. |
| `dust_zat` | 546 | zat | Dust threshold for residual and output checks. | G9 | The node's relay dust rule. |
| `reorg_attacker_share` | 0.30 | hash share | Hash share of a reorging adversary when sizing `walletConfirmations`. | G9 | Use the largest plausible single-pool or coalition share. |
| `max_reorg_prob` | 0.001 | probability | Maximum P(a reorg deeper than the wallet's confirmation wait). | G9 | Tighter raises `walletConfirmations` (excluded, patch release). |
| `max_void_prob` | 0.01 | probability | Maximum P(a mint is VOIDed by a natural reorg past `DEFAULT_REF_LAG`). | G9 | Tighter raises the default ref lag (excluded, patch release). |

## `[optimizer]` — joint pass and sensitivity

| Key | Default | Unit | Meaning | Read by | How to choose |
|---|---|---|---|---|---|
| `max_rounds_joint` | 3 | rounds | Coordinate-descent rounds in the joint pass; `--max-rounds` overrides. | joint pass, optimizer runner, CLI | 1 halves the quick run (couplings then reported as not iterated). 3 matches the plan. |
| `insensitive_total_order` | 0.01 | Sobol index | Total-order index below which a parameter is labelled *insensitive*. The label never changes a verdict (D-WP8-3). | joint sensitivity | Presentation threshold; 0.01 = under 1 % of output variance. |

## `[owner_pinned]` — owner decisions (D-RD-INF-2)

| Key | Default | Unit | Meaning | Read by | How to choose |
|---|---|---|---|---|---|
| `owner_pinned` | 17 parameters (below) | param → decision reference | Parameters whose value the owner fixed by decision. The study still runs and its evidence is kept, but the value is **kept**: verdict `KEEP (owner decision <ref>)`, never CHANGE, never in `params.cpp.patch`; the group's other parameters are decided with the pin held (the joint pass treats it as fixed). Where the evidence points elsewhere, the executive summary and the parameter section say "evidence points to X because …; risk of keeping: …". | optimizer runner (`optimize/pins.py`), report, lock-readiness | Remove a line to let the study move that value; add one (a tunable registry name) when the owner decides another. A value may be `{ ref = "L3", of = "signalWindow" }`: the shipped *fraction* of the parent is kept, so the value follows the parent (the decision fixes a share, not a count). |

The shipped pins, with their plan citations (workspace `docs/plans/`; "proposal" =
`docs/reference/yellowback-price-attestation.md`):

| Parameter | Value | Decision | Where |
|---|---|---|---|
| abandonBlocks | 34,560 (30 d) | W21 / D-R-12 (2026-10-02) | v3 §2 W21, §3.1, §6.2 |
| grace | 34,560 (30 d) | D-R-6 (2026-09-21), reaffirmed in revision 4 / W21 | v3 §0 rev. 4, §6.2 |
| classMin[0..2], classMax[0..2] | 30–90 d, 90–365 d, 1–5 y | D-R-6 / W21: "GRACE and the lock classes stay as they are" | v3 §0 rev. 4, §6.2 D-R-12 |
| supplyCapBps | 1,500 | W20 / D-R-11: stays 1,500, soft above `RECAP_RATIO_BPS` | v3 §2 W20, §3.1, §6.2 |
| attestFeeBps | 2,500 | D-3: additive, 25 % of `feeZat` | proposal §16, v3 §3.1 |
| attestArmMin, attestArmDelay | 5, 1,152 | D-4: automatic arming, 5 ELIGIBLE attestors then one day | proposal §16, v3 §1 item 5, §3.1 |
| activationThreshold, participationFloor | 75 %, 60 % of `signalWindow` (1,512, 1,210 of 2,016) | L3: "the mint halt keeps 60 % / 75 %" — pinned as fractions | v2 §0 revision 4 |
| enforcementFloor, enforcementResume | 50 %, 60 % of `signalWindow` (1,008, 1,210 of 2,016) | L3: suspend below 50 %, resume at 60 % — pinned as fractions | v2 §0 revision 4 |
| valveBlocks | 6 | L7: work valve at 6 blocks | v2 §0 revision 5 |

Owner decisions on values that are not tunable registry fields are honoured by derivation, not
pinned: `recapRatioBps` = 2 × `globalRatioHaltBps` (W16 / D-R-3, the owner chose 2×), the window
minimum fills ⌈W/2⌉ and ⌈2W/3⌉ (L9), the sunset span ≈ 420,480 blocks (L8, release study).

## `[studies_g5_g8]` — G5/G8 assumptions (D-WP7c-2)

| Key | Default | Unit | Meaning | Read by | How to choose |
|---|---|---|---|---|---|
| `activation_reliability` | 0.99 | probability | Probability activation locks in within the first eligible window at the expected share (`activationThreshold` constraint). | G5 | Lower accepts a slower start. |
| `valve_minority_trip_max` | 0.01 | probability | Maximum probability the work valve trips when enforcers are a minority. | G5 | Judgement; the valve is node-local. |
| `attestor_mean_outage_blocks` | 48 | blocks | Mean attestor outage length (Markov outages, liveness sensitivity and dormancy). | G8 | Measure on testnet. |
| `max_harmful_capture_prob` | 0.01 | probability | Maximum probability an entity at `max_single_entity_weight_share` moves a bundle price 10 % in the harmful direction (aMint up). | G8 | The capture-resistance tolerance; tighter needs more seats or a higher `qLowBps`. |
| `max_grief_capture_prob` | 0.05 | probability | Maximum probability of griefing-only capture (moving aMint down 10 %). | G8 | Griefing refuses mints but steals nothing, so it may be looser than the harmful limit. |
| `max_premature_claim_prob` | 0.01 | probability | Maximum probability a one-hour wick (`flash-wick-50-1h`) turns into an emergency claim. | G8 (`emergencyPersist`) | Owners lose collateral to a premature claim; keep it small. |
| `claim_reaction_blocks` | 576 | blocks (12 h) | Time a claimant needs between an emergency notice persisting and its expiry (`emergencyNoticeTtl − emergencyPersist`). | G8 | Ask likely claimants. |
| `registration_notice_blocks` | 8064 | blocks (1 week) | Public notice before a new bond counts (`bondMaturity` lower bound). | G8 | Longer gives the community time to react to a large new bond. |
| `min_capture_days` | 90 | days | Minimum time for a capital-matched newcomer to reach the harmful weight share under `ageCap`. | G8 | Longer protects against fast capture but slows honest newcomers. |
| `max_newcomer_seat_days` | 365 | days | Longest time a new honest bond may wait for a seat. | G8 | The other side of `min_capture_days`. |

## `[agents]` — vault-book personas (D-WP4-3)

| Key | Default | Unit | Meaning | Read by | How to choose |
|---|---|---|---|---|---|
| `yed_premium_bps` | 0 | bps | Premium (+) or discount (−) of YED against $1 when burners buy it. Under RED-4(b) a claimant profits only when YED trades at a discount. | G3, G6, agents | 0 assumes a working peg. Try −2,000 (YED at $0.80) to see the stressed case. |
| `claimant_slippage_bps` | 100 | bps | Base slippage a claimant pays selling seized YEC. | G3, G6, agents | Set from order-book depth. |
| `defector_share` | 0.0 | fraction | Share of owners who sweep without burning when enforcement is off. | G3, G6, agents | 0 is optimistic; 0.1–0.3 tests the unenforced case. |
| `lost_key_prob` | 0.0 | probability | Probability an owner's key is lost for good. | G3, G4, G6, agents | A small positive value (0.005) is realistic. Lost keys never redeem; no grace helps them. |

## `[studies_g3_g4_g9]` — G3/G4/G9/release assumptions (D-WP7b-7)

| Key | Default | Unit | Meaning | Read by | How to choose |
|---|---|---|---|---|---|
| `ensemble_agg` | `"worst"` | enum: worst, mean | How G3/G4 aggregate P(bad debt) across the synthetic presets (or bootstrap members). | G3, G4 | `worst` is conservative: one fat-tailed model can block a class. `mean` averages model risk away. With real data the bootstrap dominates. |
| `dev_absence_tolerance_days` | 0 | days | Developer absence the abandonment window must tolerate beyond the runbook. | G4 | 0 means "the runbook is all we promise". Add days if the dev team may be unreachable longer. |
| `dust_spend_multiple` | 3 | multiple | A residual or output must be worth at least this many times the cost of spending it. | G9 | 3 is the usual economic-dust rule. |
| `residual_max_share` | 0.01 | fraction | Largest share of a `minMint` vault's debt that may be left unpaid below `residualMinZat`. | G9 | Upper bound for `residualMinZat`. |
| `carrier_max_share` | 0.001 | fraction | Largest carrier output value as a share of a `minMint` vault. | G9 | Upper bound for `carrierValue` (excluded). |
| `mint_inclusion_slack_blocks` | 10 | blocks | Blocks a mint may wait for inclusion; `DEFAULT_REF_LAG` must leave this much of `refWindow`. | G9 | Larger in congested conditions. |
| `release_tip` | 3052055 | height | Mainnet tip at the release; the M14 lead counts from here. | params check, invariants, R | **Update at every release.** The default is the 6.21.0-rc1 tip quoted in `params.cpp`. |
| `release_tip_date` | `"2026-10-02"` | date | Date of `release_tip`, for converting heights to dates in the release study. | R | Update with `release_tip`. |
| `yec_daily_volume_p10_usd` | unset (None) | USD/day | p10 daily YEC trading volume. Enables the `maxMint` depth bound (G9) and replaces G7's placeholder volume. | G7, G9 | Compute from a fetched history (`volume_24h_usd`) or a depth file. **Required for a meaningful supply-cap and `maxMint` answer.** |
| `reference_price_usd` | unset (None) | USD/YEC | YEC/USD reference price for fee and bond judgements. Default: from the data, else the synthetic start price. | G9 | Set it when running without price data, or to judge at a deliberately stressed price. |
| `next_upgrade_height` | unset (None) | height | Next scheduled Ycash network upgrade; the sunset must precede it. Default: read from `chainparams.cpp` (none scheduled at the pin). | invariants (`sunset`), R | Set it as soon as a network upgrade is scheduled. |

## `[studies_g6_g7]` — G6/G7 assumptions (D-WP7d-6)

| Key | Default | Unit | Meaning | Read by | How to choose |
|---|---|---|---|---|---|
| `liar_bias_bps` | 2000 | bps | Quote bias of the liar REG-4 must catch. | G6 | Smaller liars are harder to catch without penalising honest pools. |
| `min_liar_detection` | 0.9 | probability | Minimum share of that liar's tags REG-4 penalises. | G6 | Bounds `deviationBps` from above. |
| `max_fee0_prob` | 0.001 | probability | Maximum probability a block has no eligible payee (FEE-0). | G6 (`payeeWindow`) | Small: FEE-0 refuses fee-paying transactions. |
| `min_registered_prob` | 0.999 | probability | Minimum probability an honest pool is registered (REG-1) within `nReg`. | G6 | Informational parameter; mild. |
| `min_liar_exclusion` | 0.99 | probability | Minimum probability the wallet payee rule (L6) excludes a liar. | G6 (`nPenalty`) | Wallet default; patch-release. |
| `max_honest_exclusion` | 0.01 | probability | Maximum probability the wallet payee rule excludes an honest pool. | G6 | As above. |
| `max_accuracy_sd_bps` | 500 | bps | Maximum standard deviation of honest pools' accuracy score. | G6 (`accuracyWindow`) | Wallet default; patch-release. |
| `max_honest_payee_spread` | 1.5 | ratio | Maximum ratio between honest pools' payee weights. | G6 (`payeeTiltBps`) | Fairness between honest pools. |
| `min_accuracy_premium` | 0.25 | fraction | Minimum payee-weight premium of an accurate over a sloppy pool. | G6 (`payeeTiltBps`) | Incentive for accuracy. |
| `p10_daily_volume_usd` | 25000.0 | USD/day | **Placeholder** p10 daily YEC volume, used by G7 only when there is no depth file and no `yec_daily_volume_p10_usd`. | G7 | Do not tune this; set `yec_daily_volume_p10_usd` or supply a depth file instead. |
| `system_ratio_alarm_bps` | 15000 | bps | HALT-2 must fire before the true system collateral ratio falls to this. | G7 (`globalRatioHaltBps`) | The system-level safety margin; higher demands earlier halts. |

---

## Placeholders to replace before a lock

These keys carry guesses, not measurements, and the report flags the results that rest on them:

- **Market data** (supply real files instead): `pool_outage_rate_per_day`, `pool_outage_mean_hours`,
  `expected_pool_count`, `orphan_rate`, `yec_daily_volume_p10_usd` (unset), `p10_daily_volume_usd`.
- **Behaviour**: `owner_absence_*`, `adoption_scenarios`, `adoption_case`, `[agents]`,
  `attestor_uptime`, `attestor_outage_correlation`, `attestor_mean_outage_blocks`.
- **Owner tolerances** (PLAN §12.1): `max_bad_debt_prob`, `max_false_halt_hours_per_year`,
  `attack_share_min`, `expected_enforcing_share`, `max_fee_share_small`, `materiality`.
- **Per release**: `release_tip`, `release_tip_date`, `next_upgrade_height`.

## Known gaps

None open. (Fixed 2026-10-03: `diverge_spread_multiplier` now reaches the ported spreads analysis;
`hour_kernel_tolerance_bps` is passed to `engine.kernel_error(tolerance_bps=…)`, which reports
`within_tolerance`.)

`tests/test_docs_policy.py` fails if a `Policy` field is missing from this page.

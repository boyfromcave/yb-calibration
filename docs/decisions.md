# Decision log

One entry per decision that shapes results or contracts. Newest last. Format: id, date, owner,
decision, reason, consequences.

## D-1 (2026-10-03, WP-0) — "locked" / "excluded" vocabulary mapping

**Decision.** The tool uses the owner's words and records how they map onto the plan's terms
(PLAN §1.2):

| ybcal class | Plan / spec term | Change path |
|---|---|---|
| `locked` | consensus-shaped among enforcing miners (v2 K10, spec §3.1; v3 delta) | new parameter set keyed by start height, at/after the previous sunset (L8) or after a full ENFORCEMENT-halted signal window (W19); a sunset-only change is a renewal (W18) |
| `excluded` | rows marked informational, wallet default (L6), wallet/agent policy, node-local | patch release |
| `per-release` | `startHeight`, `enforceUntilHeight` (M14, L8) | every release, from the tip |
| `constant` | protocol constants (`params.h`) | verified, never tuned |
| `derived` | fixed by a formula from a parent | follows the parent; locked when `consensus` is true |
| `meta` | `network`, `addressVersion` (D10) | not calibrated |

**Consequence.** `ParamSpec.change_path` turns this into the report's locked / patch-release note.

## D-2 (2026-10-03, WP-0) — `abandonBlocks` is locked

**Decision.** Classified `locked` (PLAN §1.3), not node-local.
**Reason.** The spec does not mark the row informational or wallet default, so K10's wording makes
it consensus-shaped; it gates TPL-1/2, MP-1 and the `yed_sweep` abandonment predicate, which every
release must answer alike (spec `ABANDON_BLOCKS` row, W21).
**Consequence.** G4 recommendations for it carry the locked note. The W21 invariant
`abandonBlocks ≥ grace` and the plan's proposed `abandonBlocks ≥ runbook length` are both enforced
(margin at current values: 34,560 − (2,016 + 2,016 + 16,128 + 4,608 buffer) = 9,792 blocks).

## D-3 (2026-10-03, WP-0) — `attestMaxAge` is derived from `k` but locked

**Decision.** `attestMaxAge` has `klass="derived"`, `derive = 2 · attestInterval`,
`consensus=True` (change path `locked`), while its parent `attestInterval` (k) is `excluded`
(agent policy).
**Reason.** The spec marks k as agent policy but `ATTEST_MAX_AGE = 2·k` (R4) is read by BUNDLE-1 and
REV-1. `ParamSet.replace(attestInterval=…)` therefore moves a locked value.
**Consequence.** G8 must treat a change of k as a *locked* change whenever it moves `attestMaxAge`;
changing k alone (holding `attestMaxAge` explicitly) violates the `attest_max_age` invariant, so the
two always move together.

## D-4 (2026-10-03, WP-0) — `volPeriodsPerYear` derivation is network-aware

**Decision.** At mainnet scale `volPeriodsPerYear = BLOCKS_PER_YEAR / volStep` (exact division
required); on regtest it stays 8,760.
**Reason.** PLAN §1.3 lists it as derived (K13), but the spec says the annualisation is "an
independent parameter, deliberately equal on every network" — regtest uses `volStep = 8` with
8,760, so the formula only holds at mainnet scale.
**Consequence.** The `derive` callable receives the full value mapping (including `network`); the
`vol_periods` invariant checks the formula at mainnet scale and 8,760 at regtest scale. WP-9's
scaler must keep 8,760.

## D-5 (2026-10-03, WP-0) — regtest column and scale-dependent invariants

**Decision.** The registry's regtest column is `RegtestParams()` with the six flags at their
defaults: `startHeight=1` (the flag is required and must be positive), `sigmaRefBps=0`,
`supplyCapBps=0`, `enforceUntilHeight=0`, `attestArmMin=3` (the header default), `bundleCarrier=
SCRIPTSIG`. Invariants that encode mainnet block counts (`abandon_ge_runbook`, `release_lead`,
`bond_lock_year`, `regtest_zero_meanings`) are skipped for regtest-scale sets, and the `sunset`
invariant accepts `enforceUntilHeight = 0` only at regtest scale.
**Reason.** These are the regtest column's legitimate differences (spec §3.1: 0 = none / fixed
multiplier / never arms; regtest bonds lock 200 blocks).
**Consequence.** Both shipped columns pass every applicable invariant at the pin; no invariant was
weakened for mainnet.

## D-6 (2026-10-03, WP-0) — `DEFAULT_REF_LAG` is excluded, other header constants are constants

**Decision.** `DEFAULT_REF_LAG` (a `params.h` constant) is classified `excluded` (G9, wallet
default `-yellowbackmintlag`) per PLAN §1.3; `REF_WINDOW`, `TOKEN_VALUE`, `PRICE_MIN/MAX`,
`BLOCKS_PER_*` and `MAX_REF_LAG` are `constant`.
**Consequence.** G9 recommends `DEFAULT_REF_LAG` with a patch-release note.

## D-7 (2026-10-03, WP-0) — policy-dependent invariants and defaults

**Decision.** Two §1.4 checks depend on the policy: `qlow_vs_entity` uses
`max_single_entity_weight_share` (default 0.25 → 2,500 bps < qLow 3,333) and `fee_floor_mintable`
uses `worst_price_usd` (default $100 = `PRICE_MAX`: a $100 class-C minMint vault at 300 % needs 3 YEC
≥ 4·feeMin = 2 YEC). Without a policy, `Context()` skips the entity check and uses `PRICE_MAX`.
The runbook buffer defaults to 4,608 blocks (4 days) and `operator_upgrade_window_blocks` to 2,016
(so the current `activationDelay` meets it). These are starting tolerances for the owner (PLAN §12).

## D-8 (2026-10-03, WP-0) — `study` / `recommend` / `report` CLI owned by WP-8

**Decision.** The `ybcal study` driver (`ybcal.studies.cli.cli_study`) is assigned to WP-8 with
`recommend`, so the three parallel WP-7 agents do not collide on one file; WP-7 agents test their
studies through the library API.

## D-WP6-1 (2026-10-03, WP-6) — per-candidate RNG: common random numbers by default

**Decision.** `evaluate_many` scores every candidate with a copy of the run `Env` whose `rng` is
reset to `env.rng_for("evaluate")` — the *same* stream for every candidate (common random numbers).
`crn=False` switches to `env.rng_for("evaluate", ParamSet.digest())`.
**Reason.** Results must not depend on evaluation order or worker count (architecture
"Seeds"), and comparisons between candidates are far less noisy when they face the same simulated
paths — which matters for the 20 % materiality rule and for successive halving at low fidelity.
**Consequence.** `workers=1` and `workers=N` give bit-identical tables (tested). Studies should
still draw scenario paths from `env.rng_for(scenario, …)`; a study that wants independent noise per
candidate passes `crn=False` through `run_group`.

## D-WP6-2 (2026-10-03, WP-6) — search axes are anchored on the current value

**Decision.** An axis is `base + k·step` inside the registry `bounds` (anchored on the lower
bound only when the base lies outside them, i.e. a regtest-scale set). Grids are thinned to
`budget.grid_points` per axis keeping the base value; full grids above `cap` (4,096) are thinned
further, and if even two points per axis do not fit, LHS with `cap` samples is used. Both
fallbacks warn.
**Reason.** The current value must be a candidate (materiality), and "±1 step" has to mean the
same thing in a grid, a neighbourhood check and the joint Sobol pass.
**Consequence.** Some grid points sit closer to a bound than one step without touching it; the
bounds themselves are only included when they are on the base lattice.

## D-WP6-3 (2026-10-03, WP-6) — successive halving over Monte-Carlo paths ("Hyperband-lite")

**Decision.** Fidelity is `Budget.paths`. Rung `r` of `R = budget.halving_rounds` runs at
`max(min_paths, ceil(paths / eta^(R−1−r)))` paths (`eta = 3`, `min_paths = 8`), the last at the full
budget; each rung keeps the best `ceil(n/eta)` (feasible first) **plus the current set**. A single
bracket is run (no Hyperband bracket sweep).
**Reason.** The candidate lists are small and fixed by each study, so the Hyperband hedge over
starting fidelities buys little; keeping the current set guarantees the materiality comparison is
made at full fidelity.
**Consequence.** `optimize_group(method="halving")` returns a table of full-fidelity survivors only;
the low-fidelity scores are kept in `GroupRun.halving.low_fidelity` for the report.

## D-WP6-4 (2026-10-03, WP-6) — rejected candidates are counted, base is always evaluated

**Decision.** Candidates failing an invariant, a feasibility predicate, a coupled-bounds check, or
construction are recorded as `Rejected(changes, reason, detail)` and summarised by invariant name
(`CandidateSet.invalid_counts()`); the summary goes into every Recommendation's "Search:" note. The
base set is always evaluated, even if it violates an invariant (a warning is recorded).
**Reason.** "Why was this value not considered?" must have an answer in the report; and
`decide_with_materiality` needs the current row.

## D-WP6-5 (2026-10-03, WP-6) — robust selection and tie-breaking

**Decision.** `robust_select` builds the feasible set from `Constraint`s (policy bounds on an
aggregate — worst / CVaR / mean / quantile — of a metric over scenarios) and the per-scenario
`Metrics.constraints`; minimax regret is measured against the best *feasible* candidate per
scenario. If nothing is feasible it returns the least-violating candidate (sum of relative
violations) with `blocked=True`. Ties within `1e-12·max(1,|best|)` go to the current set, then to
the smallest step distance, then to input order. CVaR uses the fractional-atom (Rockafellar–Uryasev)
definition; for a loss the tail is the upper `1 − alpha` mass.

## D-WP6-6 (2026-10-03, WP-6) — sensitivity estimators

**Decision.** Sobol: Saltelli design on a scrambled Sobol' sequence (`scipy.stats.qmc`; `n` rounded
up to a power of two), Jansen estimators for both `S1` and `ST`, 95 % percentile bootstrap CIs; rows
with a NaN output (an invariant-violating set) are dropped and counted. Morris: random trajectories
with a jump of `⌊n_levels/2⌋` levels (Δ = p/(2(p−1)) for even p), effects per unit of the factor's
range. OAT slope classes use arc elasticity with a 5 %-of-max floor on the denominator: flat
`|e| < 0.1`, steep `|e| ≥ 1`.
**Reason.** Validated against the Ishigami analytic indices (S1 ≈ 0.314, 0.442, 0; ST ≈ 0.558,
0.442, 0.244 — within 0.02 at n = 8,192) and an additive linear model (S1 = ST, exact Morris μ*).
**Consequence.** Morris with 4 levels ranks Ishigami's x1 and x2 together ahead of x3 (x3's effect
is pure interaction: σ > μ*); that is the known behaviour of the method, so Sobol `ST` is the
quantity the joint pass uses for "insensitive" (`policy.insensitive_total_order`).

## D-WP6-7 (2026-10-03, WP-6) — `ybcal sensitivity` CLI deferred

**Decision.** `ybcal.optimize.cli.cli_sensitivity` is not implemented by WP-6: it needs the joint
recommended set and the study evaluators (WP-7/WP-8). The command keeps exiting 2 ("not implemented
yet (WP-6)"). The library pieces (`ParamSetObjective`, `factors_for_params`, `morris`, `sobol`) are
ready for whoever wires it (suggested: WP-8 alongside `recommend`).

## D-WP1-1 (2026-10-03, WP-1) — how the reference model is vendored

**Decision.** `yellowback_model.py` and `yellowback_attest.py` are vendored whole (as
`model/reference.py`, `model/reference_attest.py`) with exact, counted import-line rewrites only;
from `yellowback_util.py` / `util.py` only the needed top-level definitions are copied verbatim by
AST (`model/reference_util.py`). Each `.py` carries a pin header; `VENDOR.json` lists hashes;
`vendor.VENDORED_COMMIT` holds the full pin.
**Reason.** The golden replay needs the attestation helpers (bundle parsing, signature check, W9
selection); `yellowback_util.py` imports the node test framework and cannot be imported standalone.
**Consequence.** `ybcal verify` fails on any edit of a vendored file; re-pinning is
`ybcal verify --revendor --ycash6 PATH --ref NEW` plus bumping `VENDORED_COMMIT` (and the WP-0
snapshot, D-log entry). Node-driver functions of `reference_attest` are intentionally unusable.

## D-WP1-2 (2026-10-03, WP-1) — kernels follow the C++ where the reference model differs

**Decision.** On degenerate inputs where `yellowback_model.py` and `math.h` disagree (weighted
quantile with zero total or q > 10^4; σ with non-positive samples; non-positive amounts), the
kernels implement `math.h`, and the property tests compare with the reference on its domain only.
**Reason.** The node is the consensus; the differences are listed in architecture.md ("Model and
kernels") and never arise on the golden chain or in the worked examples.

## D-WP1-3 (2026-10-03, WP-1) — undefined encodings

**Decision.** Scalar kernels use `None` for undefined (predicates `False`); vectorised kernels use
int64 with `-1` (`vkernels.UNDEF`) and read any price ≤ 0 as undefined, exactly as the C++ does.
**Consequence.** Simulator code (WP-3..5) stores prices as int64 arrays with `-1` gaps.

## D-WP1-4 (2026-10-03, WP-1) — rolling medians by wavelet matrix

**Decision.** PRICE-1 rolling lower medians with min-fill are computed by a wavelet matrix over the
compressed quote sequence (exact range k-th smallest, vectorised over all blocks), not the PLAN
§3.3 sliding sorted window.
**Reason.** O(n log n) independent of W, no per-block Python loop: 1 path × 100k × W=2016 in
≈ 0.07 s (target 2 s); one structure serves all three windows. PIN-1's per-height key exclusion is
not expressible as a static mask, so pinned heights must use the scalar kernel.

## D-WP1-5 (2026-10-03, WP-1) — reference model's stale mainnet `abandon_blocks`

**Decision.** Recorded, not patched: `yellowback_model.Params.mainnet()` has `abandon_blocks =
4,032` while `params.cpp` @ 7702d22 has 34,560 (W21). The kernels never read the model's Params
(parameters are arguments; the registry is the source), and `ybcal verify` prints the drift.
**Consequence.** Worth reporting upstream (ycash6 test framework); a re-vendor after a fix
removes the note, and `test_reference_mainnet_column_drift_is_only_the_known_w21_one` will then
need updating.

## D-WP2-1 (2026-10-03, WP-2) — `PricePath` stays in `ybcal.types`; helpers in `ybcal.data.pricepath`

**Decision.** The frozen `PricePath` (WP-0, `types.py`) is used unchanged; `ybcal.data.pricepath`
re-exports it and adds units/clamp, block ↔ hour resampling (hour → block holds each price for
its 48 blocks; block → hour samples blocks 0, 48, …, so a round trip is exact), slicing, log
returns and CSV/NPZ persistence. Per-step masks travel in `meta` (notably `meta["filled"]`).
**Consequence.** No contract change was needed.

## D-WP2-2 (2026-10-03, WP-2) — loaders: last duplicate wins, as-of resampling with a filled mask

**Decision.** Every loader sorts by time and keeps the **last** row of a repeated timestamp
(`pinrate.py`'s rule; `spreads.py read_log` keeps both, ybcal dedupes so a log is a function of
time), counting dropped and conflicting duplicates. Grid resampling is an as-of join (last
observation at or before the grid point) and flags points with no observation in their cell;
fitting and `describe` use observed points only.
**Reason.** Forward-filled hourly prices on a block grid would otherwise read as 47 zero returns
and one large one, biasing every volatility and tail estimate.

## D-WP2-3 (2026-10-03, WP-2) — fetch: chunked hourly history, explicit network-blocked error

**Decision.** `ybcal data fetch --source coingecko --granularity hourly` fetches more than 90 days
of hourly data through consecutive `market_chart/range` calls of ≤ 89 days; `auto` keeps
CoinGecko's own granularity (hourly ≤ 90 days, daily beyond). Unreachable hosts raise
`NetworkBlockedError` (exit 3) pointing to `docs/data.md`; every fetch writes a provenance sidecar
with the CSV's sha256. `tickers` and `nonkyc` are snapshots appended per call (cron-driven logs).
**Reason.** The owner needs ≥ 1 year of hourly YEC/USD (PLAN §12.3) and a plain `days=365` call
returns daily points. The sandbox cannot reach the APIs, so fetchers are tested on recorded
fixtures with `urlopen` mocked.

## D-WP2-4 (2026-10-03, WP-2) — synthetic presets are placeholders; fitting methods

**Decision.** Presets (all ≈ 115–120 % annualised vol, $0.40 start): GBM σ 1.20; Merton σ 0.95,
λ 12/yr, jumps N(−2 %, 20 %); GARCH(1,1)-t hourly α 0.06, β 0.93, ν 4; regime switch calm σ 0.80 /
turbulent σ 2.00 (mean spells 120 d / 30 d, turbulent drift −150 %/yr); bootstrap has no preset.
Fitting: GBM moments; Merton threshold moments (4 robust sds) polished by a one-jump-per-step
mixture MLE; GARCH-t MLE (L-BFGS-B, three starts); regime switch Baum–Welch EM; bootstrap stores
returns (mean block one week). Discrete-time models (GARCH, bootstrap) refine to finer grids with
a variance-matched Brownian bridge.
**Reason.** PLAN §4.2 asks for moment matching for non-GARCH models; the Merton threshold
estimator alone undercounts small jumps (≈ 25 % low on a test with λ = 50/yr), so the MLE polish
is added and documented. Presets are labelled in every path's `meta` and stay `synthetic`.

## D-WP2-5 (2026-10-03, WP-2) — scenario bases are centred; families via `[[variants]]`

**Decision.** A scenario's base process has its expected log drift removed by default
(`[base] center = true`), so the price program alone sets the trend. Parameterised families
(`oracle-attack-{p}`, `attestor-outage-{n}`, `attestor-capture-{w}`, `hashrate-drop-{to}`,
`dev-absence-{days}`) are one file each with `[[variants]]` and dotted-path `set` overrides.
Behaviour schedules use a fixed, documented name list (unknown names are rejected); a schedule is
`(n,)` when deterministic and `(n_paths, n)` when it has stochastic outages. The `core` tag defines
`Budget.scenario_set = "core"` (11 scenarios).
**Reason.** An uncentred 120 %-vol GBM adds −72 %/yr of log drift, which would turn every
scenario into a bleed. Shipped variants: oracle-attack 10/20/25/34/40/51, attestor-outage 1/2/3/5,
attestor-capture 10/20/25/33/40/50, hashrate-drop 70/60/50/45/30, dev-absence 14/30/60/90/180.

## D-WP2-6 (2026-10-03, WP-2) — `--kind hashrate` is the pool-share CSV

**Decision.** WP-0's `ybcal data import --kind hashrate` reads the PLAN §4.1 pool-share CSV
(`height,payout_key`); shares, rolling shares and a `HashrateDrift` fit come from it. Depth has
two accepted forms: summary `ts,depth_2pct_usd,volume_24h_usd[,bid_depth_2pct_usd]` and book
levels `ts,side,price_usd,size_yec` (summarised to USD depth within ±2 % of the mid).

## D-WP9-1 (2026-10-03, WP-9) — time-scaling rules

**Decision.** `scale_to_regtest` divides block counts by one cadence factor (default
`pSlowWindow / 64` = 31.5) and, optionally, a separate `term_factor` for grace, classes,
abandonment and bond lifetimes (default: the same). Rounding is half-up for block counts. Thresholds
are `⌈c·S′/S⌉` of `signalWindow` with the §1.4 ordering re-imposed. The σ sample count
`volWindow/volStep` is kept exactly: `volStep` is whichever of ⌊volStep/f⌋ or ⌈volStep/f⌉ gives a
window nearer `volWindow/f`, giving 2 / 84 at the default. Counts over a scaled window
(`pinMinTags`, `pinMinBundles`, `dormancyMinBundles`) keep their rate, floored at 2 (two equal
observations). Two more floors: `attestInterval` ≥ 4 (agent cadence vs ~2 s blocks) and
`peerLag` ≥ ⌈peerMin/2⌉. Selection counts, `peerMin`, `valveBlocks`, bps, amounts and constants are
not scaled.
**Reason.** PLAN §6.3: keep the ratios the rules read, and report what integers cannot hold. One
factor keeps term-to-window ratios. The shipped regtest column instead compresses terms ~1,440×, so
`--term-factor` exposes that choice rather than hiding it.
**Consequence.** At the default factor, 37 values differ from the shipped regtest column. Every
difference is explained in `SHIPPED_REGTEST_NOTES`, and the test suite fails on an unexplained one.
Ratio losses over 5 % are listed in `docs/devnet.md` §3.

## D-WP9-2 (2026-10-03, WP-9) — `bondMin` on a scaled set is the regtest 10 YEC

**Decision.** By default the scaler sets `bondMin = 10 · COIN` (the shipped regtest value) and leaves
every other amount unscaled; `bond_min="mainnet"` keeps 20,000 YEC.
**Reason.** A devnet wallet cannot fund three 20,000-YEC bonds in a reasonable number of blocks, and
bond size only enters bond weight, which the devnet does not calibrate.
**Consequence.** Weight-capture studies (G8) must not read bond economics off devnet runs.

## D-WP9-3 (2026-10-03, WP-9) — contract note: the `sunset` invariant at regtest scale

**Request to WP-0.** `invariants._sunset` accepts `enforceUntilHeight = 0` at regtest scale, but a
non-zero regtest sunset must still equal `startHeight + BLOCKS_PER_YEAR`, which is a mainnet-scale
clause. A scaled sunset (`start + 420,480 / term_factor`) or a flag such as
`-yellowbackenforceuntil=500` is legitimate on regtest.
**Interim.** `scaling.check_regtest(ps)` drops only that clause for regtest-scale sets with a
non-zero sunset. The scaler and the overlay split use it. The scaler defaults to
`enforceUntilHeight = 0` (the registry's regtest default), so the default path never needs it.
**Proposed fix.** Scope the `u == s + BLOCKS_PER_YEAR` clause to mainnet scale (keep
`u > startHeight` at regtest scale).

## D-WP9-4 (2026-10-03, WP-9) — own minimal launcher beside `yellowback-devnet`

**Decision.** `ybcal devnet run` starts its nodes itself by default (`runner.MinimalDevnet`), using
the single-node configuration of `doc/yellowback-devnet.md` §2 plus the devnet's fixed pool keys
and port-seed scheme. `--launcher` drives `contrib/yellowback/devnet/yellowback-devnet up` only when
the overlay's six runtime flags equal what that launcher hard-codes.
**Reason.** At the pin the launcher builds every node's arguments with `yellowback_node_args`
(`-yellowbackstartheight=1 -yellowbacksigmaref=0`) and has no option to pass other node arguments,
so the six runtime parameters could not be varied through it (PLAN §6.2 item 3). The minimal
launcher also has a deterministic bootstrap (101 funding blocks plus a full activation) that the
simulator can replay exactly.
**Consequence.** `attestor-outage-1` needs attestor seats with real `yellowback-attest` agents, so it
runs only under `--launcher`, and is skipped otherwise. Personas (`yellowback-sim`) need the
launcher's role presets and are not driven in v1; `replay(on_step=…)` is the hook for them.

## D-WP9-5 (2026-10-03, WP-9) — differential contract and pass criterion

**Decision.** The simulator entry point is
`ybcal.sim.engine.simulate_devnet(params, path, schedule) -> list[dict]` (records as
`scrape.HISTORY_FIELDS`). `diff.compare` requires exact equality with type discipline: `None ≠ 0`
and `bool ≠ int`. An allowlist entry is `field`, `field@h` or `field@lo-hi`. Keys present on one
side only fail the comparison. `pending` (no simulator) and `skipped` (no node) never count as a
pass.
**Consequence.** WP-3..5 implement `simulate_devnet` against `devnet.scenarios.ReplayStep`
semantics. Until then `ybcal devnet validate` reports every scenario `pending WP-3..5` and exits 0
(3 with `--strict`).

## D-WP9-6 (2026-10-03, WP-9) — version-skew policy

**Decision.** A binary whose commit is not the pin, or cannot be determined, is refused unless
`--allow-version-skew` is passed. This covers the relations predates, postdates, diverged and
unknown. The refusal lists the commits in between and the parameter values that differ on both
networks. A running node's `yed_getinfo.params` and `yed_getactivation` must equal the overlay,
except the node-overridable wallet policy (`nPenalty`, `accuracyWindow`, `payeeTiltBps`).
**Finding.** CI run 37081639884 is built from `94bafa4`, eight commits before `7702d22`. Its regtest
column is identical; mainnet `abandonBlocks` differs (4,032 vs 34,560). The rule changes are the
W20 soft supply cap (`deff6f5`) and the `mintingAllowed` / `supplyCapReached` RPC predicate
(`a8291a0`). With `-yellowbacksupplycapbps=0` W20 has no effect, so that binary is usable for
stock-column runs under `--allow-version-skew`. It is not usable for runs with a cap, such as a
scaled mainnet overlay's 1,500 bps.

## D-WP9-3 resolution (2026-10-03, integrator)

`invariants._sunset` now applies the `startHeight + BLOCKS_PER_YEAR` clause at mainnet scale only;
at regtest scale a non-zero sunset must merely lie after `startHeight`. `scaling.check_regtest`'s
filter is now redundant but harmless and is kept.

## D-WP3-1 (2026-10-03, WP-3) — engine stages: two hook-only additions

**Decision.** The stage list agreed with WP-5 (`activation, attest, pin, price, sigma, supply,
halts, vaults`) is kept and extended with `judge` (first; REG-4 runs first in SNAP) and `dormancy`
(last). Neither has a built-in step. Hooks run after each stage's built-in step and may mutate the
series. After the `vaults` hooks the engine recomputes `global_ratio_bps` and the HALT-2 bit from
`supply_cents` / `collateral_zat`, so WP-4 only has to fill those arrays.
**Consequence.** Additive; hooks that ignore unknown stages are unaffected.

## D-WP3-2 (2026-10-03, WP-3) — internal activation is exact, not "always ACTIVE"

**Decision.** When WP-5's `activation.simulate` is absent the engine uses the exact ACT-1..3 and
ACT-4/6 kernels (`vkernels.signal_counts` / `hysteresis` series), tested equal to the reference
model; the "always ACTIVE, no ACT halts" behaviour is an explicit flag (`activation_mode=
"always_active"`) for price-only studies. `series.activation_source` records which ran.

## D-WP3-3 (2026-10-03, WP-3) — PIN-1 lives in the engine; pinned keys are pool ids

**Decision.** The `pin` stage computes PIN-1 itself from BundleLog rows (`pin1_triggered`, or
`bundle_present` + `bundle_a_mint` on WP-5's AttestSeries / in `inputs.attest`) and the quote tags;
pinned keys are pool ids stored as a uint64 bitmask (≤ 64 pools). PIN-2 (`pinned_seqs`) stays WP-5's.
Heights with pinned keys take the exact scalar median path (D-WP1-4); others the wavelet fast path.

## D-WP3-4 (2026-10-03, WP-3) — Ycash subsidy schedule and issuedZat origin

**Decision.** `issuedZat` = Σ `GetBlockSubsidy(h)` over `[startHeight, H]` (state.cpp:1224; the
virtual snapshot carries 0). Mainnet schedule from ycash6 @ 7702d22: slow start 20,000, halving
840,000 pre-Blossom / 1,680,000 post, Blossom 1,100,000, `UPGRADE_YCASH` 570,000 changes nothing in
the subsidy (the YDF is paid out of it), no funding streams. Regtest uses Blossom at 1 (the
functional tests' `nuparams`, `reference.regtest_subsidy`).
**Consequence.** At `startHeight` 3,075,000: 1.5625 YEC/block until 3,960,000 → 657,000 YEC per
sunset year. Feeds fact 1.5-2 / G7.

## D-WP3-5 (2026-10-03, WP-3) — hour-mode kernel tolerance (request to WP-0/WP-8)

**Decision.** `engine.KERNEL_TOLERANCE_P95_BPS = 300` (p95 relative error of hourly pMint/pClaim vs
block mode) is the default acceptance; measured ≈ 50 bps on held-out GBM paths.
**Request.** Add a policy key (e.g. `kernel_tolerance_p95_bps`) so the owner sets it; WP-3 did not
edit `config.py` / `policy/default.toml` (WP-0 files).

## D-WP3-6 (2026-10-03, WP-3) — oracle model choices

**Decision.** A pool's quote is the integer TWAP of the true price over `twap_blocks` (default 12 ≈
15 min) × (1 + (bias + noise·z)/10^4), clamped to [PRICE_MIN, PRICE_MAX]; stale feeds repeat a
quote for `refresh_blocks`; outages are Poisson starts with exponential lengths. Floats are used
only to generate behaviour; the stream handed to the rules is integers. `BlockInputs` applies TAG-2:
a quote outside the price range makes the whole tag absent (as `find_tag` does).
**Finding for G2 (WP-7a).** The σ estimate SIGMA-1 sees is measured on pFast (a 96-block median of
TWAP quotes), which smooths returns: on GBM at 120 % true volatility the median σ̂ is ≈ 8,200 bps
against ≈ 11,900 bps measured on the true price. `sigmaRefBps` should be calibrated against σ̂ of
simulated/real *pFast*, not raw price volatility (`sigma.sigma_hat_bps` on `series.p_fast`).

## D-WP3-7 (2026-10-03, WP-3) — `simulate_devnet` and a WP-9 test adjustment

**Decision.** Implemented WP-9's contract (D-WP9-5) as `engine.simulate_devnet`, reusing WP-9's
`block_miners` / `jittered_quote` so the replayed tag stream is identical (pools signal-only while a
step's price is 0, dark miner untagged, block i = height 1 + i). Its runner defaults (3 pools,
10 bps jitter, run seed = schedule seed) are keyword arguments and must match the devnet run.
**Consequence.** `ybcal devnet validate` now finds a simulator, so scenarios report SKIPPED (no
ycashd) instead of PENDING; `tests/devnet/test_diff_cli.py::test_cli_validate_pending` was relaxed
by one line to accept either (WP-9's file — flagged for the integrator).

## D-WP3-5 resolution (2026-10-03, integrator)

Added `Policy.hour_kernel_tolerance_bps` (default 300) and the matching `policy/default.toml` key;
`engine.KERNEL_TOLERANCE_P95_BPS` stays as the library default. D-WP3-7 (WP-9 test accepting
SKIPPED as well as PENDING) is accepted as is.

## D-WP5-1 (2026-10-03, WP-5) — attestation walk: segments of constant status + sparse points

**Decision.** `attest.simulate` walks each path through segments between status events
(registration, maturity, EQV-1, bond spend, REV-1); seating for a whole segment is one numpy
argsort (or constant when every ELIGIBLE seq fits in `nSlots`), and a per-path Python loop visits
only demand, PIN-2-trigger and dormancy-check heights. A dormancy that fires cuts the segment.
**Reason.** Exact node semantics (block order: transactions, BundleLog[H], SNAP) at ≈ 0.1 s per
mainnet path-month; attestation events are sparse.
**Consequence.** Exactness is shown by replaying the golden chain and by a reference harness that
drives `YellowbackModel`'s own SNAP/selection code with synthetic transactions (8 randomised
scenarios); seated/selected/pinned sets are uint64 bitmasks, so ≤ 64 registered attestors per path.

## D-WP5-2 (2026-10-03, WP-5) — attestor behaviour and bundle assembly model

**Decision.** An online attestor signs every `k` blocks at its phase (`attestInterval`); a bundle for
`(R, selector)` contains every selected seq whose newest attestation is cited in
`(R − attestMaxAge, R]` (BuildBundle, index.cpp:1174) and verifies iff `mSelect ≤ |C| ≤ bundleMax`.
Attestations cited at or after a seq's ejection / bond spend are not usable (the pool refuses
EJECTED/WITHDRAWN, index.cpp:1132). Availability: iid or two-state Markov per attestor plus an
optional common outage; prices `true·(1 + bias + noise)`, optional stuck feed. REV-1 is sent at the
first height ≥ dormancy + `revive_delay` with an own attestation cited in `(H − attestMaxAge, H − 1]`.
Default demand: Poisson(1/48) bundles per block with `ref = H − DEFAULT_REF_LAG` (WP-4 replaces it).
Bond spends are clamped to `registerHeight + bondMinLock + 1` (the earliest CLTV spend); an EQV-1 at
the spend height is ordered first.
**Reason.** Agent policy is not consensus; these are the simplest models consistent with the node's
wallet path and the G8 questions (liveness, dormancy, capture).

## D-WP5-3 (2026-10-03, WP-5) — PIN coupling with the oracle engine

**Decision.** `attest.simulate` reads the engine's xMint (`series.p_mint`) for PIN-2 and emits the
PIN-1 trigger (`pin1_triggered`, from the BundleLog rows) for the engine to apply the key exclusion.
The exact coupled result is the fixed point of oracle → attest → re-median at PIN-1 heights → attest.
**Reason.** PIN-1 at H reads rows < H and PIN-2 at H reads pMint < H, so only the rare pinned heights
couple the two layers; a block-interleaved engine would be much slower for no gain elsewhere.
**Consequence.** Recorded as a contract note for WP-3 (architecture.md, "Activation & attestation
simulator").

## D-WP5-4 (2026-10-03, WP-5) — mid-chain starts and height frames

**Decision.** Activation needs `ActivationInit` (incl. the last `W − 1` signal bits) when
`height0 > start_height`; attestation applies pre-series events at `height0` and accepts
`initial_trigger_height` / `initial_seated_since`. The sunset is re-based:
`enforce_until = start_height + (P.enforceUntilHeight − P.startHeight)` (0 stays "none").
**Reason.** The engine may run relative heights or a window of a longer chain; the rules depend only
on distances from the start.

## D-WP5-5 (2026-10-03, WP-5) — G5 analytic estimators

**Decision.** For iid Bernoulli(p) signals the exact per-block downcrossing probability
`P(Bin(W − 1, p) = floor − 1)·p·(1 − p)` is the expected rate of entering `count < floor`; it bounds
the halt-episode rate from above (an episode starts only at a downcrossing while not halted). The
halted fraction is bracketed by `P(count < floor)` and `P(count < resume)`; `P(any per year)` uses
`1 − exp(−rate)`. Share drift uses the exact Poisson-binomial; detection delay is Monte Carlo with a
fluid approximation; the ACT-7 valve uses the gambler's-ruin `(q/(1 − q))^(valveBlocks − 1)`.
**Reason.** Overlapping windows make per-window binomials double-count; crossings are exact and
cheap. All estimators are checked against simulation in `tests/sim/test_activation_analytic.py`.

## D-WP5-6 (2026-10-03, WP-5) — G8 analytic estimators

**Decision.** Liveness: binomial in the per-attestor freshness `1 − (1 − u)^⌈maxAge/k⌉` (Markov:
`1 − (1 − u)(1 − 1/L)^((s − 1)k)`), beta-binomial with intra-class correlation ρ for correlated
outages. False dormancy: closed form for iid availability, an exact forward recursion over the
window for Markov outages; per-year figures are a union bound over checks. Capture: bundle-level
thresholds (qLow / 1 − qLow) plus Monte Carlo over W9 selections with the exact kernels.
**Reason.** Matches PLAN §5.8; each helper is validated against Monte Carlo and the liveness formula
against the simulator's own bundle success rate.

## Integration of WP-3 and WP-5 (2026-10-03, integrator)

- The engine reads `activation.simulate`'s `ActivationSeries` fields (it had assumed a tuple) and keeps
  the series on `BlockSeries.activation_series`.
- `attest.simulate` runs only when `inputs.attest` is given; without it the run is unarmed.
- D-WP5-3 item 1: `oracle.generate_block_inputs(..., enforce_until=)` applies the sunset signal mask
  (miners drop the bit past `enforceUntilHeight`). The engine itself does not mask, so replayed tag
  streams stay exact.
- D-WP5-3 item 2 (iterating PIN-1/PIN-2 coupling to a fixed point) is not yet done: the engine runs
  one pass (oracle → attest → PIN-1 → medians). Assigned to WP-8.

## D-WP7a-1 (2026-10-03, WP-7a) — G1 objective is normalised by the current windows

**Decision.** PLAN §5.1's "crash-lag CVaR₉₅ plus λ × pump overpricing" adds hours to bps, so λ would
mean nothing. The study uses J = CVaR₉₅(pClaim 90 % crash lag)/current + λ · E[pMint − true | pump-dump]/current,
both normalised by the current windows' values; λ = `pump_overpricing_lambda` is then a relative
weight (0.5: half a crash lag's worth of relative change). The crash-lag term is the **pClaim** 90 %
lag (pClaim = max(pMid, pSlow) is the price that lags a fall); pMint lags are reported.
**Consequence.** J(current) = 1 + λ; materiality applies to J.

## D-WP7a-2 (2026-10-03, WP-7a) — G1 manipulation constraint: analytic V16 plus a simulated check

**Decision.** `attack_share_min` holds when (a) every window's V16 threshold (smallest coalition
share whose quotes are the lower median with probability ≥ ½, exact binomial, coalition carved out of
the tagging share) is ≥ `attack_share_min`, and (b) a coalition of exactly that share, biasing ±10 %,
moves pMint up or pClaim down by ≥ half its bias in ≤ 5 % of attack blocks (`ATTACK_MOVED_TOL`).
**Reason.** (a) is nearly window-independent (≈ 40 % of hash at 80 % tagging), so on its own it never
discriminates; (b) catches the rank-shift effect (a minority pushes the lower median up the honest
quotes, whose spread grows with the window). Only the harmful directions count (over-minting,
premature claims); griefing moves are reported.
**Request to WP-0.** Add a policy key `attack_moved_tol` (default 0.05) so the owner sets (b).

## D-WP7a-3 (2026-10-03, WP-7a) — G1/G2 pool-outage model and NO_PRICE availability

**Decision.** Background availability (`no_price_h_per_year`, the `max_no_price_hours` constraint)
is measured in calm with independent per-pool feed outages: each of the policy's equal pools has
1 outage per 30 days, exponential length with mean 4 h, signal-only tags while out. A 6-hour outage of
every feed is reported per event (`no_price_h_per_feed_outage`), not annualised.
**Reason.** The policy has no outage frequency; annualising the all-feeds event at any assumed rate
≥ 1/yr makes every window set infeasible (≥ 6 h per event), which says nothing about windows.
**Finding.** At 80 % tagging with 6 equal pools, one pool out leaves a 66.7 % tag rate — exactly the
⌈2W/3⌉ fill — so pMid at 576 blocks flickers to NO_PRICE during long single-pool outages
(≈ 16–20 h/yr under this model; 1,152 blocks ≈ 0.4–4 h/yr). The constraint, and with it the G1
recommendation, rests on this placeholder; measured pool tagging data should replace it.
**Request to WP-0.** Policy keys `pool_outage_rate_per_day` / `pool_outage_mean_hours` (and
optionally `feed_outages_per_year`).

## D-WP7a-4 (2026-10-03, WP-7a) — G1 keeps HALT-3 working: recall constraint on crash-70-1d

**Decision.** A window set must keep HALT-3 firing within one day of the crash start on ≥
`halt_recall_floor` of `crash-70-1d` paths at the current `divergenceBps`.
**Reason.** HALT-3 compares the medians, so G1 can silently disable it (G7 coupling). The slow
30-day `crash-90-30d` never trips HALT-3 at 20 % divergence for any window set; its recall is
reported, not constrained (that is G7's question).

## D-WP7a-5 (2026-10-03, WP-7a) — G2 window rule also bounds the K12 trap; responsiveness on σ̂

**Decision.** volWindow/volStep must keep both the regime-shift responsiveness and the K12 cap trap
after a 6-hour feed outage (blocks with an undefined sample after the feeds return, ≈ volWindow +
pFast recovery) within `max_sigma_lag_blocks`. Responsiveness is measured on the unclamped σ̂ (median
over paths), not the clamped multiplier, which is flat at 1× whenever both regimes sit below the
reference.
**Reason.** The trap is the other way the multiplier fails to reflect the market (fact 1.5-3), with
the same lag tolerance; without it the CV rule always prefers the longest admissible window.

## D-WP7a-6 (2026-10-03, WP-7a) — sigmaRefBps: round down; KEEP inside the M14 band

**Decision.** The rule value is the realised median pFast-based σ̂ rounded **down** to 500 bps (so
the unclamped median multiplier is ≥ 1×, inside the M14 band); the band test uses the unclamped
ratio σ̂₅₀/sigmaRef (the clamped median is ≥ 1× by construction). The current value is kept while that
ratio lies in `sigma_accept_band` and the rule value is within `materiality`; the cap is kept while it
covers the p99 turbulent multiplier and is within `materiality` above the rule value.
**Reason.** PLAN §2.3 (minimal change) applied to rules that compute a value directly rather than
search for one.

## D-WP7a-7 (2026-10-03, WP-7a) — evidence directory and shared realisations

**Decision.** Studies write evidence to `env.data["out_dir"]/<group>/` (or `env.data["workdir"]`), else
a fresh temp dir. `decide()` has no `Env`, so `evaluate` puts `out_dir` (and the budget name) into
`Metrics.meta`. G1 and G2 share one per-process memo of scenario realisations and medians
(`g1_price_windows.realise` / `median`), keyed by seed, scenario, paths, horizon, data hash and the
policy's pool parameters.
**Request to WP-0/WP-8.** An `Env.out_dir` field would make this explicit.

## Contract changes from WP-7a (2026-10-03, integrator)

- `Policy` gains `attack_moved_tol` (0.05), `pool_outage_rate_per_day` (1/30) and
  `pool_outage_mean_hours` (4.0); G1/G2 read them (module constants remain as fallbacks).
  `feed_outages_per_year` was not added: the all-feeds outage stays reported per event (D-WP7a-3).
- `Env.out_dir` added; `g1_price_windows.out_dir_of` prefers it over `env.data["out_dir"]`.

## D-WP7c-1 (2026-10-03, WP-7c) — G5/G8 as families of one-at-a-time rules

**Decision.** G5 and G8 each run one candidate table of one-at-a-time sweeps around the current set
and apply one decision rule per *family* of parameters (`g5_activation.Family` / `FamilyStudy`, shared
by G8): the family's rows are those whose delta lies in its params, its metrics are re-projected onto
its primary and its own constraints, then `decide_with_materiality` (optimize), "KEEP unless a
constraint fails, then the nearest feasible" (verify, primary ≡ 0), or a ported closed-form target with
a materiality tie to current (rule). `adjust_changes` reconciles families that touch the same thing
(G5: a new signalWindow carries its thresholds at their fractions; G8: when both dormancy families fix
one violation only the faster fix is applied, the other is KEEP with a note).
**Reason.** The groups hold many loosely coupled parameters (26 in G8); a joint grid is wasteful and
mixes unrelated constraints into one feasibility test. The WP-8 joint pass handles cross-group coupling.
**Consequence.** Simulation confirmation (exact `activation.simulate` / `attest.simulate`) runs inside
`decide` from `Metrics.meta` (seed, budget name/paths/horizon, out_dir), since `decide` has no `Env`.

## D-WP7c-2 (2026-10-03, WP-7c) — judgement constants; requested policy keys

**Decision.** Tolerances the policy lacks are module constants (`g5_activation.JUDGEMENT`,
`g8_attestation.JUDGEMENT`), documented in docs/studies/g5.md and g8.md. Requested policy keys
(integrator: add to `Policy`/`default.toml`; the studies would read them with `getattr(policy, key,
JUDGEMENT[...])`): `activation_reliability` (0.99), `valve_minority_trip_max` (0.01),
`attestor_mean_outage_blocks` (48), `max_harmful_capture_prob` (0.01), `max_grief_capture_prob` (0.05),
`max_premature_claim_prob` (0.01), `claim_reaction_blocks` (576), `registration_notice_blocks` (8,064),
`min_capture_days` (90), `max_newcomer_seat_days` (365).

## D-WP7c-3 (2026-10-03, WP-7c) — G5 objective: false halts, detection as a constraint

**Decision.** Every G5 threshold family minimises expected false-halt hours/year (0.01-h resolution, a
drift mixture of window-mean shares) subject to the policy's false-halt, flap and detection budgets, an
enforcement majority (`enforcementFloor ≥ W/2`, L3) and reliable activation. Detection delay is a hard
constraint, not the objective. The halted fraction is rate × fluid duration capped by `P(count <
resume)` (conservative near the resume level).
**Reason.** The valve bounds the cost of minority enforcement ("the node rejoins within six blocks"),
whereas a false halt stops minting for everyone; with detection as the objective the rule would shrink
signalWindow to its bound on any policy. The 0.01-h resolution keeps 10⁻²⁰-hour differences from
counting as "material improvements".
**Consequence.** G5 provenance is `judgement` (exact math given an assumed share) unless a pool-share log
is in `env.data["pool_shares"]`/`["hashrate"]` (`real-data`). `false_abandon_probability(params, share,
abandon_blocks)` is the G4 import (an upper bound, tested against Monte Carlo).

## D-WP7c-4 (2026-10-03, WP-7c) — G8 capture is evaluated at bundle level

**Decision.** The proposal §7.2 rule ("Q_LOW must exceed the largest single-entity weight share among the
selected attestors") is checked with the exact selection and weighted-quantile kernels: an entity with
`max_single_entity_weight_share` of the *seated* weight in one seat, honest seats equal. Theft (aMint up)
must stay ≤ 0.01; griefing (spanning qLow, aMint down) ≤ 0.05 — the latter only in the qLow family.
**Reason.** With 6 of 9 seats selected a 25 % seat holds 34.8 % of a bundle's weight, above qLow 3,333,
in 94 % of bundles. That is griefing (over-collateralisation), not theft, which needs > 2/3.
**Consequence.** qLowBps → 3,500 (qHigh 6,500) at the default policy — or the owner lowers the assumed
entity share to ≤ ~0.22. Reported as a design note in the qLowBps recommendation.

## D-WP7c-5 (2026-10-03, WP-7c) — ported spreads.py/pinrate.py; the "200–300" reading

**Decision.** `_g8_ports.py` keeps upstream names and arithmetic (`analyze` renamed `analyze_spreads` /
`analyze_pinrate`); the README's fallback "pick the smaller of the two whose rate clears 20 %" is read
literally (the smallest of 200/300 with arming rate > 20 %, 200 if neither). `pooled_pinrate` pools the
windows of several synthetic histories. The equivalence test imports the upstream scripts from a temp
copy (`git show` at 7702d22 — ycash6 untouched) and skips without a clone; hand-computed cases always run.

## D-WP7c-6 (2026-10-03, WP-7c) — attest.simulate notes found while confirming G8

- `seatedSince ≤ 0` reads as "not seated" (the node's 0 sentinel), so a mid-chain run with a negative
  `initial_seated_since` never makes anyone DORMANT. The G8 confirmation uses positive heights
  (start 1, height0 50,400, a multiple of every dormancyCheck on the grid).
- BundleLog rows before `height0` are unknown, so dormancy in a mid-chain run counts rows from `height0`.
- With `kSlack = 0` a dead selected attestor makes every bundle that picks it fail, so no row lists it and
  dormancy can never eject it (a protocol property, not a simulator artefact): G8 adds the
  `dead_detectable` (k ≥ 1) constraint to the kSlack family and counts a dead attestor's rows only when
  the other `m + k − 1` still make the bundle.

## Contract changes from WP-7c (2026-10-03, integrator)

The ten G5/G8 assumption constants are now `Policy` keys under `[studies_g5_g8]` in
`policy/default.toml`; the studies already read them with `getattr(policy, key, default)`.

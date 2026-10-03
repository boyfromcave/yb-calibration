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

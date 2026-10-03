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

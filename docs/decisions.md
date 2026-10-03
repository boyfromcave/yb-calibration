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

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

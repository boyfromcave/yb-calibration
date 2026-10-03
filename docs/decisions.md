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

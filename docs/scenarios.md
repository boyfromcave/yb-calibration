# Stress-scenario library

Every study evaluates candidates on an **ensemble** of scenarios, not one path (PLAN §4.3). A
scenario is a TOML file in `scenarios/` that sets a **price program** on top of a **base** return
process, plus **behaviour schedules** and **constants** the studies read. Code:
`src/ybcal/data/scenarios.py` (WP-2). Data models: [data.md](data.md).

```python
from ybcal.data import scenarios
lib = scenarios.load_library()                 # name → Scenario (36 shipped)
core = scenarios.scenario_set("core")          # the quick budget's set (Budget.scenario_set)
run = lib["crash-70-1d"].generate(env.rng_for("G3", "crash-70-1d"), n_paths=64)
run.paths        # PricePath, provenance "scenario", shape (64, n)
run.schedules    # {"enforcing_share": (n,), "feed_up": (n,), …}; stochastic ones (64, n)
run.constants    # free-form scalars from the file
```

`generate(rng, n_paths, *, data=None, base_path=None, resolution=None, horizon_days=None, p0=None)`
is deterministic for a given generator state. `resolution`/`horizon_days` override the file: times
are written in days/hours, so one file serves block mode and hour mode, and a study can shorten a
scenario to its budget's horizon (later segments are dropped). `data` (real prices) feeds a
`bootstrap` base; `base_path` replays given paths' returns as the base (e.g. a window of real
history) instead of any model. Large block-mode ensembles should be generated in batches with
independent generators (`Env.rng_for(name, batch)`): 120 days at block resolution is 138,241 steps
per path.

## Schema

| Key | Meaning |
|---|---|
| `name`, `description`, `stresses` | identity and what it stresses (the PLAN §4.3 column) |
| `tags` | labels; `"core"` puts the scenario in the quick budget's set |
| `horizon_days`, `resolution` | horizon; `"block"` (≤ 120 days, PLAN §3.3) or `"hour"` |
| `p0_usd` | start price (default $0.40) |
| `[base]` | `model` (`gbm`, `merton`, `garch`, `regime`, `bootstrap`, `flat`), `params` (overrides on the YEC-like preset), `fallback` (bootstrap without data; default `garch`), `center` (default true) |
| `[[segments]]` | the price program (below) |
| `[schedules.<name>]` | `default`, `changes = [{at_*, value}]`, `ramps = [{start_*, duration_*, to}]`, optional `outages = {rate_per_day, mean_hours, value}` (stochastic, per path) |
| `[constants]` | scalars for the studies (e.g. `attacker_bias_bps`, `dev_absence_days`) |
| `[[variants]]` | a family: each variant has `name`, optional `description`/`tags`/`horizon_days`, and `set = {"dotted.path" = value}` overrides on the body |

**Centring.** With `center = true` the base's expected log drift (e.g. −σ²/2 for a zero-μ GBM) is
removed, so the program alone sets the trend and the base only adds noise around it. Without it a
120 %-vol GBM would add a −72 %/yr log drift to every program.

**Segments** (`pct` = a percent move of the price, applied as `log(1 + pct/100)`; times take
`_days`, `_hours` or `_blocks`). Order of application: `vol` first (it scales base returns only),
then the additive kinds in file order, then `hold` windows.

| Kind | Fields | Effect |
|---|---|---|
| `hold` | `start`, `duration` | returns zero: the price is frozen, then resumes from the held level |
| `drift` | `start`, `duration` (default: to the end), `rate` | adds `rate` (log per year) |
| `vol` | `start`, `duration`, `mult` | scales base returns |
| `jump` | `start`, `duration`, `rate_per_day`, `mean_pct`, `sd_pct` | random log jumps |
| `shock` | `at`, `pct` | instantaneous move |
| `ramp` | `start`, `duration`, `pct` | total move spread evenly (log-linear) over the window |
| `wick` | `at`, `pct`, `duration`, `recover` (default 1) | instantaneous move, then linear recovery of `recover × move` over `duration` |

**Schedules** (unknown names are rejected as typos):

| Name | Meaning | Unit | Main reader |
|---|---|---|---|
| `enforcing_share` | hash share running enforcing nodes (ACT-4/ACT-6 counts) | fraction | G5, G4 |
| `signal_share` | share of blocks setting the signal bit (dropped after the sunset) | fraction | G5, release |
| `tagging_share` | hash share whose blocks carry a price tag (PRICE-1 fill) | fraction | G1 |
| `attacker_share` / `attacker_bias_bps` | colluding coalition share and quote bias | fraction / bps | G1, G6 |
| `stale_pool_share` / `stale_lag_blocks` | share of pools quoting stale, and how stale | fraction / blocks | G6 |
| `frozen_pool_share` | share of pools repeating one constant quote | fraction | G8 (PIN-1) |
| `feed_up` | 1 while exchange feeds are reachable | flag | G1, G2, G8 |
| `attestor_uptime` / `attestors_down` | per-attestor availability; attestors forced offline | probability / count | G8 |
| `attestor_capture_weight` / `attestor_bias_bps` | adversary's bond-weight share and bias | fraction / bps | G8 |
| `dev_present` | 1 while developers can ship a release | flag | G4 |
| `owner_present_fraction` | fraction of vault owners reachable | fraction | G4 |
| `renewal` | 1 once a renewal set is released (W18) | flag | release |
| `mint_demand` | minting demand multiplier vs the policy adoption case | multiplier | G6, G7 |

Schedules are behaviour *inputs*; the simulator (WP-3..5) turns them into blocks, tags, bundles and
counts.

## Scenarios

All bases are centred GBMs (σ in the table); "core" = in the quick budget's set.

| Scenario | Res. | Days | Core | Base σ | Program | Schedules |
|---|---|---|---|---|---|---|
| `calm-90d` | block | 90 | yes | 0.60 | — | enforcing 0.80, tagging 0.80, feeds up, attestor uptime 0.95 |
| `crash-70-1d` | block | 60 | yes | 0.80 | ramp −70 % over 1 day at day 20; vol ×1.5 days 21–31 | enforcing 0.80 |
| `crash-90-30d` | block | 90 | yes | 0.90 | ramp −90 % over days 15–45; vol ×1.5; jumps 0.2/day of −8 % ± 5 % | enforcing 0.80 |
| `slow-bleed-95-2y` | hour | 900 | yes | 0.80 | ramp −95 % over days 30–760 | — |
| `pump-dump-3x` | block | 60 | yes | 0.70 | ramp +200 % over days 15–25; ramp −66.7 % over days 27–29 | enforcing 0.80 |
| `flash-wick-50-1h` | block | 30 | yes | 0.60 | wick −50 % at day 10, recovered within 1 h | — |
| `feed-outage-6h` | block | 30 | yes | 0.60 | — | `feed_up` 0 and tagging 0 for 6 h from day 10 |
| `stale-pools` | block | 30 | | 0.90 | — | from day 5: 30 % of hash 1 h stale, 10 % frozen |
| `oracle-attack-{10,20,25,34,40,51}` | block | 30 | 34 | 0.60 | — | coalition share p, bias 1,000 bps, days 5–25 (`attacker_direction = "both"`) |
| `attestor-outage-{1,2,3,5}` | block | 30 | 1 | 0.60 | — | n attestors down days 5–20; uptime 0.95 |
| `attestor-capture-{10,20,25,33,40,50}` | block | 30 | | 0.60 | — | adversary weight w, bias 1,000 bps from day 5 |
| `hashrate-drop-{70,60,50,45,30}` | block | 120 | 45 | 0.80 | — | enforcing/signal/tagging 0.80 → `to` at day 30, back to 0.80 over days 75–80 |
| `dev-absence-{14,30,60,90,180}` | hour | 104–270 | | 1.00 | — | enforcing 0.80 → 0.40 at day 30 (a defect halts ENFORCEMENT); `dev_present` 0 for `days`; both restored on return |
| `owner-absence` | hour | 400 | yes | 1.00 | — | `owner_present_fraction` 0.98, 0.90 for 3 weeks from day 170; constants: log-normal absence median 7 d, σ 1, 1/yr |
| `sunset-no-renewal` | hour | 450 | | 1.00 | — | signal 0.80 → 0 at day 365 (the sunset), `renewal` 0 |
| `rogue-major-pool-{15,5}` | block | 30 | | 0.90 | — | the pool nearest 52 % (real landscape) quotes +15 % / +5 % from day 5 (`attacker_share`, `attacker_bias_bps`) |
| `major-pool-offline` | block | 30 | | 0.90 | — | `offline_pool_share` 0.52 from day 5: its hash leaves, the rest mine every block |
| `venue-pool` | block | 30 | | 0.90 | — | `venue_pool_share` 0.10: that pool's agent reads SafeTrade alone (`constants.venue_pool_source`) |
| `attestor-capture-capital` | block | 30 | | flat | — | constants: adversary capital 10k–500k USD of YEC bonds, split over ≤ 6 seats, against 9 minimum honest seats |

### Notes per scenario

- **calm-90d** — the baseline for every false-alarm metric: false halts (HALT-3, ACT), false pins
  (PIN-1 on honest jittered quotes), false REG-4 penalties, and fee drag. 60 % volatility is
  deliberately calmer than the YEC-like 120 %.
- **crash-70-1d / crash-90-30d** — collateral sufficiency and claimability (fact 1.5-1: no
  liquidation before `claimHeight`), HALT-2/HALT-3 timeliness against when the system is truly
  under 100 % / 150 %. The 30-day crash adds turbulence and downward jumps.
- **slow-bleed-95-2y** — no single event trips a halt; class B/C vaults must stay solvent over the
  whole term plus grace while the price bleeds 95 %. Hour mode.
- **pump-dump-3x** — pMint is the minimum of the medians, so the question is how much a minter
  can over-mint near the top; HALT-3 fires only on a fall (fact 1.5-6), so the dump, not the pump,
  is where divergence matters.
- **flash-wick-50-1h** — the medians should ignore a one-hour wick; MINT-10 (attestation
  divergence) and PIN-1 should neither fire falsely nor be fooled.
- **feed-outage-6h** — the K12 σ-cap trap: one undefined pFast sample pins the multiplier at its
  cap for up to `volWindow` blocks; measures the minting freeze. The true price keeps moving.
- **stale-pools** — honest-but-slow pools against REG-4's `deviationBps`/`peerLag`; a frozen pool
  against PIN-1.
- **oracle-attack-{p}** — V16 manipulation resistance: a coalition of hash share p biases its
  quotes ±10 % for 20 days. The 34 % variant is the policy's `attack_share_min`; 51 % is the
  majority bound. Studies run both signs.
- **attestor-outage-{n}** — bundle liveness with `nSlots` 9, `mSelect` 4, `kSlack` 2 at the pin:
  1–2 down is within slack, 3 exceeds it, 5 leaves fewer than `mSelect`. Also dormancy ejection.
- **attestor-capture-{w}** — weighted-quantile robustness: 25 % is the policy's
  `max_single_entity_weight_share`, 33 % sits just under `qLowBps` = 3,333.
- **hashrate-drop-{to}** — ACT-4/ACT-6 hysteresis and flapping: 70 % is above every floor, 60 % at
  `participationFloor`/`enforcementResume`, 50 % at `enforcementFloor`, 45 % the policy's
  `detection_drop_share`, 30 % a deep loss. Also the abandonment trigger (G4).
- **dev-absence-{days}** — abandonment (`abandonBlocks`, 30 days at the pin) against the
  freeze-then-fix runbook: a defect halts ENFORCEMENT at day 30 and the developers return after
  `days`. Short absences must not abandon; a 180-day absence should.
- **owner-absence** — absentee owners against `grace`: G4 combines the log-normal absence model
  (constants, defaulting to the policy's `owner_absence_*`) with the reachability schedule.
- **sunset-no-renewal** — after `enforceUntilHeight` (start + 1 year) miners drop the signal bit
  (index.cpp:713) and ACT-4 trips within about a signal window; measures the continuity cost of a
  missed renewal (W18).

- **rogue-major-pool-{15,5}** (D-RD-ATT-6) — REG-4 when one pool supplies most peer quotes: at +15 %
  the peers' median follows the liar in its own windows, so G6 reports both the honest pools' and
  the rogue's penalised share; +5 % stays under `deviationBps` (invisible to REG-4 by design — the
  attestors' `min` is the protection).
- **major-pool-offline** — the 52 % pool's hash leaves; peer counts and FEE-0 with the remaining
  pools (G6 `adv.major_pool_offline.*`).
- **venue-pool** — an honest pool mis-configured with one thin venue (SafeTrade): REG-4 should mark
  it inaccurate without the honest pools paying (G6 `adv.venue_pool.*`).
- **attestor-capture-capital** (D-RD-ATT-9) — attestor capture priced in money at the real YEC price:
  G8 reports P(aMint up 10 %) and P(down 10 %) per capital level, the adversary choosing the best
  split of its YEC into seats of at least `bondMin`.

## Adding a scenario

Write `scenarios/<name>.toml` with the keys above (or add a `[[variants]]` entry to a family),
then run `pytest tests/data/test_scenarios.py`: every file must load, generate deterministically
at both resolutions, and use only known schedule names. Tag it `core` only if the quick budget
must run it.

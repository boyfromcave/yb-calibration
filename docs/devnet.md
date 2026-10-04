# Devnet validation (WP-9)

`ybcal devnet` checks the simulator against real `ycashd` regtest nodes (PLAN §6). It answers two
questions the simulator cannot answer alone: does the simulator agree with the node on the same
inputs (**fidelity**), and does a parameter set behave end to end under the real node code
(**sanity**). It never sweeps mainnet-scale windows; it runs a time-scaled copy of a set.

Everything here is optional. Where no node can be built or run, every command prints
`skipped: <reason>` and exits 0 (`--strict` makes that exit 3). Nothing is ever faked: a skipped
suite never earns the report's "validated" badge.

Nothing is ever written to the ycash6 clone. The only write is `git worktree add --detach` of a
throwaway worktree under `yb-calibration/.work/` (and `git worktree remove`/`prune` of it). No
branch is created, checked out or moved, and nothing is fetched.

---

## 1. Get a `ycashd`

You need a `ycashd` built from the pinned commit **`7702d22`**. Pick one of three ways.

### 1a. Use a binary you already have

```bash
export YBCAL_YCASHD=/path/to/ycashd          # or pass --ycashd PATH to any devnet command
ybcal devnet build --ycashd "$YBCAL_YCASHD"   # registers it and runs the version check (§4)
```

### 1b. Download a CI artifact (GitHub CLI)

The release workflow (`.github/workflows/yellowback-release.yml`) uploads one artifact per
platform: `release-linux_x86_64`, `release-linux_aarch64`, `release-macos_aarch64`,
`release-macos_x86_64`, `release-windows`. Each holds `ycashd_v<version>_<platform>.tar.gz`.

```bash
gh auth login                                               # once
ybcal devnet build --from-ci-run 37081639884                # artifact = release-<this platform>
ybcal devnet build --from-ci-run 37081639884 --artifact release-macos_aarch64
```

This runs `gh run download <run> -R boyfromcave/ycash6 -n <artifact>`. If that fails it falls
back to `gh api repos/boyfromcave/ycash6/actions/artifacts/<id>/zip`. It unpacks the tarball,
then copies `ycashd` and `ycash-cli` into `.work/bin/ci-<sha12>/` with `chmod 755`. On macOS it
also removes `com.apple.quarantine` where it can. A `manifest.json` next to the binaries records
the run, the artifact and the commit (`headSha`).

> **Run 37081639884 was built from `94bafa4`, eight commits *before* the pin.** See §4 before
> using it.

### 1c. Build it

```bash
ybcal devnet build --ycash6 ~/src/ycash6                    # stock regtest column
ybcal devnet build --ycash6 ~/src/ycash6 --overlay recommended.json
ybcal devnet build --ycash6 ~/src/ycash6 --overlay o.json --dry-run   # plan, flags, patch, preflight
```

A build runs these steps:

1. **Preflight.** It checks the toolchain (`make git autoconf automake libtoolize|glibtoolize
   pkg-config curl m4 python3 g++|clang++`) and free disk. Unless `depends/` is already built in
   the worktree, it also checks that every depends download host is reachable:
   `archives.boost.io`, `download.z.cash`, `static.rust-lang.org`, `github.com`,
   `download.libsodium.org` and `download.oracle.com`. If anything is missing it prints
   `skipped (build): cannot build ycashd here: …` and creates nothing.
2. **Worktree.** It runs `git worktree add --detach .work/ycash6-<commit12> 7702d22`. The commit
   must already be in your clone; ybcal never fetches.
3. **Overlay patch.** It writes the overlay's compiled values into `RegtestParams()` in
   `src/yellowback/params.cpp`, inside the worktree only (§3).
4. **Build.** When the clone you pass already has a built `depends/<triple>` and a cargo `target/`
   (any clone you have built once), the worktree borrows them: the depends prefix is symlinked
   (read-only use), the cargo target is cloned copy-on-write (`cp -cpR`; nothing is written into
   your clone), then `autogen.sh` and `configure` against the borrowed `config.site`. The first
   build of a worktree then takes about 4 minutes (cargo recompiles once because the vendored crate
   paths differ); every later overlay build is a `params.cpp` recompile and relink, about 10 s to
   2 min. `--no-reuse` (or a clone without a built depends) falls back to `./zcutil/build.sh -j N`
   (35–60 min cold). The cxx bridge headers are generated where `src/Makefile.am` has them (ycash6;
   v4.5.0 has none), then `make -C src -j N ycashd ycash-cli`. Every step runs with GNU
   libtool/coreutils first in `PATH` (Homebrew), `LIBTOOLIZE=glibtoolize` when needed and
   `CARGO_TARGET_DIR=<worktree>/target` (a shell-profile shared target would break the link).
   `--ycash6` may name a ycash-dd clone with `--ref HEAD`: the same patch and build work on v4.5.0.
5. **Cache.** It copies the binaries to `.work/bin/<key>/` with a `manifest.json` (commit, compiled
   values, patch). `key` is `stock-<commit12>` for the stock column, or `ov-<sha16>` of the
   compiled values. The six runtime flags are not part of the key, so varying them never rebuilds.
   `--force` rebuilds.

Logs go to `.work/logs/build-<key>-<time>.log`.

---

## 2. Run a scenario

```bash
ybcal devnet run --scenario calm
ybcal devnet run --scenario crash-70 --overlay recommended.json --seed 7
ybcal devnet run --scenario prices.csv --blocks-per-step 4     # your own µUSD series
ybcal devnet run --scenario crash-70 --dry-run                 # schedule, flags, node0 ycash.conf
```

A run works through these stages:

1. **Binary.** It picks `--ycashd`, then `$YBCAL_YCASHD`, then the build cache for this overlay.
2. **Version check.** It compares the binary with the pin (§4).
3. **Start.** It starts 3 pool nodes in `.work/devnet/<scenario>-<time>/node{0,1,2}`, plus a
   non-signalling "dark" miner for `hashrate-drop`. They are started directly from the single-node
   configuration in `doc/yellowback-devnet.md` §2:
   - `regtest=1`, `experimentalfeatures=1`, `yellowback=1`;
   - the six `nuparams=…:1`;
   - the six Yellowback runtime flags;
   - the devnet's fixed pool payout keys (`-yellowbackpayoutaddress`, `-yellowbacksignal=1`).
4. **Parameter check.** It compares node 0's `yed_getinfo.params` and `yed_getactivation` with the
   overlay. A difference refuses the run (§4).
5. **Replay.** It runs the bootstrap first: 101 funding blocks, then
   `signalWindow + activationDelay + 2` quoted blocks, which leaves the chain ACTIVE. Then it runs
   the scenario. Before every block it calls `yed_setquote` on the pool that mines it, jittered by
   ±`--jitter-bps` (default 10). That pool's quote never repeats from one of its blocks to the
   next, so PIN-1 never pins a "frozen" feed. It then calls `generate 1` on that pool and waits
   until every node, and node 0's Yellowback index, reach the new height. A price of 0 means a feed
   outage: every pool's quote is cleared and blocks carry signal-only tags. The pool mix, the
   attacker's bias and the signalling share all come from the schedule.
6. **Scrape.** It reads node 0 into `<run>/scrape/` (§5).
7. **Stop.** It stops the nodes and wipes their datadirs (`--keep` keeps them). `<run>/run.json`
   records the binary, the skew report, the node check, the heights and the files.

Other options:
- `--portseed` (default 101) and `--pools 1-3` choose the ports and the number of pools.
- `--port-base B` (or `$YBCAL_DEVNET_PORT_BASE`) keeps every port of the devnet in `B … B+999`:
  port seed `s` (0–40) takes p2p `B+24s+n` and RPC `B+24s+12+n`. Busy ports are refused before any
  node starts. On a shared machine use your reserved band (this workspace: `41000`).
- `--node-arg LINE` adds a `ycash.conf` line to every node (e.g. `debug=mempool`).
- `--keep` keeps the datadirs (`debug.log` for a post-mortem); a failed run still writes
  `scrape-partial/`.
- `--launcher` drives `contrib/yellowback/devnet/yellowback-devnet up --no-viz` from a worktree
  instead. That gives you the attestor seats with real `yellowback-attest` agents, which
  `attestor-outage-1` needs. The launcher hard-codes `-yellowbackstartheight=1
  -yellowbacksigmaref=0` and passes no other runtime flag, so it is only used when the overlay's
  flags equal those.

### Built-in scenarios (regtest scale, deterministic in `--seed`)

Miners follow one smooth weighted round robin across the whole schedule (`MinerPlan`), so
`pool_weights` and `signal_share_bps` hold even when every step is one block (D-RD-DEV-2).

| Scenario | Program | Exercises |
|---|---|---|
| `calm` | random walk ±0.5 %/block for 3 slow windows | PRICE-1/2, activation, NO_PRICE warm-up |
| `crash-70` | a calm slow window, a linear fall to 30 % over one fast window, 2 slow windows | HALT-3 divergence; σ with `{"sigmaRefBps": 100000}` |
| `hashrate-drop` | signalling share 80 % → 45 % for two signal windows → 80 % (dark miner) | PARTICIPATION, ENFORCEMENT halts; abandonment at small `abandonBlocks` |
| `attestor-outage-1` | `max(3, attestArmMin)` seats arm the layer; node 0 mints every `attestInterval + 2` blocks; seat 0's agent stopped for the middle third | ARM-1/2, seating, selection, BUNDLE-1, MINT-9/10, AFEE-1, dormancy |
| `oracle-attack-34` | a pool with 34 % of the tagged blocks quotes +25 % for two mid windows | median robustness |
| `feed-outage` | every feed dark for 1.5 mid windows (signal-only tags), resuming 8 % lower | NO_PRICE from staleness, recovery |
| `vault-cycle` | mint A/B/C at the class minimum locks, YED to a liquidator, redeem A, crash to 20 %, a mint into the halt, claim what is claimable, C left claimable | MINT-2..8, wallet collateral, RED-1..5, HALT-2, claim timing |
| `pin` | seats + mints every `attestInterval`; pool 1's feed frozen through a 15 % climb, then a seat's agent frozen through another | PIN-1 keys, PIN-2 seqs |

The attestor seats are emulated (D-RD-DEV-4): each pool node registers seats with
`yed_registerattestor` and, every `attestInterval` cited heights (`cited = tip − REF_LAG`, phase
= seq), signs the step's price with `yed_signattestation` and hands it to every node with
`yed_addattestation` — the job of `yellowback-attest attest` (`contrib/yellowback/attest/src/attest.rs`).
`--launcher` (real agents) remains for runs where the launcher's ports are acceptable.

Wallet actions (`ReplayStep.actions`, run by `ybcal.devnet.actions.WalletDriver`): `mint`, `send`,
`redeem`, `claim`, `claim_all`, `register`, `freeze`. Before every block the driver pushes each
transaction any node holds into the miner's mempool (p2p relay is seconds and once dropped a
carrier); after every block it waits for each two-step completion (the transaction spending the
carrier's output 0 as its last input) using the chain's view (`gettxout`), not the wallet's.

## 3. Overlays and time scaling

An overlay is a regtest-scale parameter set. `--overlay FILE` accepts any of these:
- a `ybcal-paramset/1` document (e.g. `recommended.json` once it is regtest scale);
- the scaler's `ybcal-scaled/1` output;
- `{"format": "ybcal-overlay/1", "values": {…}}`;
- a plain delta such as `{"grace": 30}`, applied over the shipped regtest column with derived values
  recomputed.

A **mainnet-scale** set (`network` `main`/`candidate`) is scaled automatically and the scaler's
report is printed.

The overlay is split two ways (`ybcal.devnet.overlay.split`):

- **Runtime flags**, which need no rebuild: `-yellowbackstartheight`, `-yellowbacksigmaref`,
  `-yellowbacksupplycapbps`, `-yellowbackenforceuntil`, `-yellowbackattestarmmin`,
  `-yellowbackbundlecarrier` (`ParamsFromArgs`, `src/yellowback/index.cpp:1229-1259`). They are
  range-checked as the node checks them.
- **Compiled values**: every other field that differs from the shipped column. These become a
  unified diff against `params.cpp` at the pin that touches only `RegtestParams()`. An existing
  `r.<field> = …;` is rewritten in place. A field `RegtestParams()` does not set (it inherits
  `SetCommon`) gets a new line before `// the six flags`. Header constants and identity fields are
  refused. The patch is checked with `git apply --check` and applied only in the `.work` worktree.

### Why and how blocks are scaled (`ybcal.params.scaling.scale_to_regtest`)

A devnet block takes about 2 s, not 75 s, and a test cannot wait 2,016 blocks for a slow median.
The scaler divides block counts by a factor `f`. By default `f = pSlowWindow / 64`, which is 31.5
for the shipped set, so the slow window lands on regtest's 64. It keeps the ratios the rules read:

- **Fill fractions.** The min-fills are derived (`⌈W/2⌉`, `⌈2W/3⌉`) and recomputed.
- **Thresholds as fractions of `signalWindow`.** Each becomes `⌈c·S′/S⌉`, and the strict ordering
  `floor < participation ≤ resume < activation ≤ window` is re-imposed.
- **σ estimator.** The sample count `volWindow / volStep` (42) is kept exactly.
  `volPeriodsPerYear` stays 8,760 (K13, D-4).
- **Classes.** Class ranges stay contiguous, `classMin[0] = grace`, `abandonBlocks ≥ grace` (W21)
  and `emergencyPersist < emergencyNoticeTtl`.
- **Counts over a scaled window** (`pinMinTags`, `pinMinBundles`, `dormancyMinBundles`) keep their
  rate, floored at 2.
- **Selection counts** (`nSlots`, `mSelect`, `kSlack`, `bundleMax`, `peerMin`) are kept.
- **Not scaled:** basis points, amounts, protocol constants and the four rate/carrier flags.
  `bondMin` takes the regtest 10 YEC (D-WP9-2).

There are two factors:
- `factor` scales the oracle, activation and attestation cadence;
- `--term-factor` scales terms, grace, abandonment and bond lifetimes (default: the same factor).

With a single factor every term-to-window ratio is kept, and a 5-year class C vault becomes 66,743
blocks (about 37 h). The shipped regtest column compresses terms about 1,440× instead, so a vault
lifecycle fits in a functional test. Use `--term-factor 1440` for runs where vaults must mature.

Every ratio the integers cannot hold comes back as a `RatioLoss`. `compare_to_shipped` explains
every difference from ycash6's hand-picked regtest column; a new, unexplained difference fails the
test suite. At the default factor:

| Parameter | Mainnet | Scaled (31.5×) | Shipped regtest |
|---|---:|---:|---:|
| `pFastWindow` / `pFastMinFill` | 96 / 48 | 3 / 2 | 8 / 4 |
| `pMidWindow` / `pMidMinFill` | 576 / 384 | 18 / 12 | 24 / 16 |
| `pSlowWindow` / `pSlowMinFill` | 2,016 / 1,344 | 64 / 43 | 64 / 43 |
| `signalWindow`, `activationDelay` | 2,016 | 64 | 64 |
| thresholds (act / part / enf / resume) | 1,512 / 1,210 / 1,008 / 1,210 | 48 / 39 / 32 / 39 | 48 / 39 / 32 / 39 |
| `volWindow` / `volStep` | 2,016 / 48 | 84 / 2 | 64 / 8 |
| `payeeWindow`, `nReg`, `nPenalty`, `accuracyWindow` | 100, 576, 288, 576 | 3, 18, 9, 18 | 10, 24, 12, 24 |
| `peerLag` / `peerMin` | 10 / 5 | 3 (floor ⌈peerMin/2⌉) / 5 | 4 / 3 |
| `attestInterval` → `attestMaxAge` | 10 → 20 | 4 (floor) → 8 | 4 → 8 |
| `attestArmDelay` | 1,152 | 37 | 8 |
| `pinWindow` / `pinMinTags` / `pinMinBundles` | 288 / 3 / 2 | 9 / 2 / 2 | 16 / 2 / 2 |
| `emergencyPersist` / `emergencyNoticeTtl` | 48 / 1,152 | 2 / 37 | 4 / 64 |
| `dormancyBlocks` / `MinBundles` / `Check` | 16,128 / 20 / 48 | 512 / 2 / 2 | 16 / 2 / 4 |
| `grace` = `classMin[0]`, `abandonBlocks` | 34,560 | 1,097 | 24 / 48, 128 |
| `classMax[0..2]` | 103,680 / 420,480 / 2,102,400 | 3,291 / 13,349 / 66,743 | 96 / 144 / 240 |
| `bondMinLock` / `bondMaturity` / `ageCap` / `foundingWindow` | 420,480 / 16,128 / 207,360 / 8,064 | 13,349 / 512 / 6,583 / 256 | 200 / 8 / 64 / 16 |

The ratio losses over 5 % are:
- `pFastMinFill/pFastWindow`: 0.5 → 0.667;
- `volStep/pFastWindow`: 0.5 → 0.667;
- `volWindow/pSlowWindow`: 1 → 1.31;
- `emergencyPersist/emergencyNoticeTtl`: 0.042 → 0.054;
- `dormancyCheck/dormancyBlocks`: +31 %;
- `payeeWindow` (5.8 % off the factor);
- floors: `pinMinTags`, `pinMinBundles` and `dormancyMinBundles` per window, `attestMaxAge/pFastWindow`
  (k floored at 4), `peerLag`;
- by design: `2·peerLag/peerMin` (4 → 1.2; peerMin is a count and is not scaled).

The other losses are rounding below 2 %. The full list is `ScaledSet.losses`.

What the shipped column does differently, and why, is in `SHIPPED_REGTEST_NOTES`
(`src/ybcal/params/scaling.py`). In short:
- fast and mid windows are wider so three round-robin pools all appear in them;
- `nReg`, `accuracyWindow` and `nPenalty` follow the mid window;
- terms, bonds, arming and dormancy are compressed far more, to fit a functional test;
- selection counts are smaller for a three-attestor devnet;
- `abandonBlocks = 2·signalWindow`, because W21 changed mainnet only;
- the flags sit at their regtest defaults.

---

## 4. Version skew

**The binary must match the pin.** `ycashd -version` prints, for example,
`Ycash Daemon version v6.21.0-rc1-94bafa4`, or `…-dirty` for an overlay build. ybcal reads the
commit from the build manifest or from that banner, then relates it to the pin in your clone:

- **equal**: proceed.
- **predates / postdates / diverged / unknown**: refused (exit 4) unless you pass
  `--allow-version-skew`. The report lists the commits in between and every parameter value that
  differs at the two commits, on both networks. Rule changes in those commits are *not* visible in
  parameters, so read the commit list.

Once a node is up, its `yed_getinfo.params` (and `yed_getactivation`) are compared field by field
with the overlay. Any difference refuses the run, except wallet-policy values a node flag may
override (`nPenalty`, `accuracyWindow`, `payeeTiltBps`).

**CI run 37081639884 (`94bafa4`) vs the pin `7702d22`.** Eight commits separate them:
- `4a824da` W21: mainnet `abandonBlocks` 4,032 → 34,560 (regtest stays 128);
- `deff6f5` W20: the supply cap becomes *soft* above `recapRatioBps` (MINT-6);
- `a8291a0`: `yed_getstats.mintingAllowed` uses the W20 predicate, and `yed_getinfo` gains
  `supplyCapReached`;
- `29f5b1c`: the W19/W18 release rule (release-time only);
- `a789255`: the `VOID` → `VOIDED` enum rename (internal);
- release/depends/doc commits.

The **regtest parameter column is identical** at both commits. With `-yellowbacksupplycapbps=0`
(the regtest default: no cap) W20 has nothing to act on, so a run of the stock column with that
binary behaves like the pin. It is usable with `--allow-version-skew`.

With a nonzero supply cap, which includes any scaled mainnet overlay (1,500 bps), the CI binary
still enforces the old **hard** cap. A differential run will then mismatch the simulator, which
follows the pin's W20 rule, wherever supply nears the cap. Use a binary built at `7702d22` for
those runs.

---

## 5. Scrape output

`<run>/scrape/` holds the following files.

- `history.csv` / `history.json`: one record per block from `startHeight`, read with
  `yed_gethistory` in chunks of at most 2,016 rows. Fields follow `doc/yellowback-rpc.md`:
  - `height`, `blockHash`, `tagged`, `quote` (0/1), `signalCount`;
  - `activationStatus`, `activationCode` (0 signaling / 1 locked_in / 2 active), `lockInHeight`,
    `activateHeight`;
  - `pFast` `pMid` `pSlow` `pMint` `pClaim` (empty when undefined), `sigmaMultBps`, `issuedZat`,
    `supplyCents`, `collateralZat`, `globalRatioBps`;
  - `haltMask` as the §3.6 bit integer (NOT_ACTIVE 1, NO_PRICE 2, PARTICIPATION 4, GLOBAL_RATIO 8,
    DIVERGENCE 16, ENFORCEMENT 32), and `haltNames`.
- `vaults.csv` / `vaults.json`: every vault (paged `yed_listvaults`), keyed `vault = txid:vout`,
  with integer zat/cents/heights. The decimal `collateral` twin is dropped.
- `info.json`, `stats.json`, `activation.json`, `attestors.json` (`weight` as an integer) and
  `scrape.json` (range and errors). A node without `yed_listattestors` is recorded as an error,
  not a failure.

- `actions.json` (runs with wallet actions): the driver's events (each action with its RPC result
  or error, each two-step completion), per-block vault rows (`status`, `claimable`), per-block
  attestor rows (seq `status`, `pinned`), the layer status and `yed_getprice` pinned keys/seqs per
  block, every seat and signature, and `yed_gettxinfo` of every MINT and closing transaction.

`ybcal.devnet.scrape.load_history_csv` reads `history.csv` back into typed records.

---

## 6. The differential suite

```bash
ybcal devnet validate                       # all eight scenarios
ybcal devnet validate --scenario calm --scenario crash-70 --overlay o.json --out suite.json
```

For each scenario the same schedule goes to the devnet and to the block-mode simulator, and the
per-block records are compared with `ybcal.devnet.diff.compare`. The fields compared are:
- `pFast`, `pMid`, `pSlow`, `pMint`, `pClaim`;
- `sigmaMultBps`, `haltMask`, `activationCode`, `signalCount`;
- `issuedZat`, `supplyCents`, `collateralZat`, `globalRatioBps`.

Vault rows can be compared with `key="vault"`. The pass criterion is exact equality on integer
state, with an allowlist (`"field"`, `"field@h"`, `"field@lo-hi"`) only for behaviour the
simulator does not model. Each field reports its first mismatching height and its counts.

For a run with wallet actions the comparison also replays the node's *transactions* through the
simulator's rule layer (`ybcal.devnet.vaultreplay`, `ybcal.devnet.attestreplay`): each MINT and
spend at its node height and refHeight, the seats' registrations and signatures, the block hashes.
The simulator re-derives everything the rules decide from them and the suite compares:

- vault rows (`status`, `voidReason`, `feePaidZat`, `closeHeight`, `burnedCents`, …, key `vault`);
- per height and vault, `status` and `claimable` as `yed_listvaults` reports them (unarmed heights);
- the wallet's collateral against `wallet_collateral_int` at R (ARMED: at `min(xMint, aMint)`);
- every mint the wallet refused must be refused by the simulator's verdict at the same R;
- the layer status (UNARMED/TRIGGERED/ARMED), each seq's status and `pinned`, PIN-1 pinned keys,
  and each MINT's bundle statistic `aMint` and seqs (`yed_gettxinfo`).

`ybcal devnet diff RUN_DIR` repeats the whole comparison for a kept run (no node needed) and writes
`RUN_DIR/diff.json` with the mismatching rows. `ybcal devnet validate --parallel N` runs N scenario
devnets at once on consecutive port seeds.

Each scenario ends in one status:
- `pending WP-3..5` only if `ybcal.sim.engine.simulate_devnet` cannot be imported (it exists since
  WP-3, D-WP3-7; the contract is in D-WP9-5);
- `skipped: <reason>` without a binary;
- `pass`, `fail` or `error`.

The suite report goes to `.work/devnet/validate-<time>.json`. Only an all-pass suite is
"validated".

---

## 7. Validation results 2026-10 (M6)

Run 2026-10-03 on the owner's machine (arm64 macOS, 10 cores), ports 41000–41999, by the devnet
agent. Every row is an exact block-by-block comparison (D-WP9-5) with no allowlist; "vaults",
"claimable" and "attest" are the extra parts of §6 for scenarios with wallet actions. Reports:
`.work/devnet/validate-{y6,dd,ov-y6,ov-dd}.json` in the `ybcal/devnet` worktree.

**Binaries (D-RD-DEV-6).** ycash6: built by `ybcal devnet build` at the pin, `v6.21.0-rc1-7702d2260`
(the workspace's `ycash6/src/ycashd` is a `94bafa4` build that predates W19–W21 and was not used for
the record). ycash-dd: `ycash-dd/src/ycashd` (`v4.5.0-cdfc4945f-dirty`, pre-W20) for the shipped
regtest column under `--allow-version-skew`; for the overlay a binary built from ycash-dd `HEAD`
(`f78a5f8`) with the same patch.

**Parameter sets.** *Regtest*: the shipped regtest column (runtime flags at their regtest defaults).
*Scaled mainnet*: the shipped mainnet set scaled by `ybcal.params.scaling` (factor 31.5, terms
`--term-factor 1440`; 33 compiled values patched into `RegtestParams()`, flags
`-yellowbacksigmaref=10000 -yellowbacksupplycapbps=1500 -yellowbackattestarmmin=5`).

| Scenario | ycash6, regtest | ycash-dd, regtest | ycash6, scaled mainnet | ycash-dd, scaled mainnet |
|---|---|---|---|---|
| calm | PASS 423 | PASS 423 | PASS 423 | PASS 423 |
| crash-70 | PASS 431 | PASS 431 | PASS 426 | PASS 426 |
| crash-70, `sigmaRefBps` 100000 | PASS 431 (43 σ multipliers) | PASS 431 (43) | — | — |
| hashrate-drop | PASS 487 | PASS 487 | PASS 487 | PASS 487 |
| attestor-outage-1 | PASS 379; 22 vaults, 22 bundles | PASS 379; 22, 22 | PASS 411; 22, 22 | PASS 411; 22, 22 |
| oracle-attack-34 | PASS 407 | PASS 407 | PASS 395 | PASS 395 |
| feed-outage | PASS 459 | PASS 459 | PASS 450 | PASS 450 |
| vault-cycle | PASS 453; 3 vaults, 546 claimable rows | PASS 453; 3, 546 | PASS 601; 3, 990 | PASS 601; 3, 990 |
| pin | PASS 379; 21 vaults, 21 bundles | PASS 379; 30, 30 | PASS 355; 17, 17 | PASS 355; 13, 13 |
| **suite** | **VALIDATED** | **VALIDATED** | **VALIDATED** | **VALIDATED** |

Numbers are blocks compared × 13 per-height fields (prices, σ multiplier, halt mask, activation,
signal count, issued, supply, collateral, global ratio), then vault rows and bundles compared.

**What the runs exercised** (node-side, identical in the simulator): NOT_ACTIVE and NO_PRICE
warm-up; HALT-3 divergence for 32–33 blocks after the crash; PARTICIPATION (147 blocks) and
ENFORCEMENT (101) under the hashrate drop; NO_PRICE from staleness for ~74 blocks in the feed outage;
HALT-2 global ratio for 120 (regtest) / 296 (scaled) blocks after the vault-cycle crash; σ with 43
distinct multipliers; ARM-1/2 with 3 or 5 seats; DORMANT for the stopped seat (regtest
`dormancyBlocks` 16; the scaled 512 is longer than the scenario); PIN-1 pinning pool 1's payout key
for 16 (regtest) / 9 (scaled) heights and PIN-2 pinning the frozen seat for 11–16 / 2–9 heights;
MINT-2..10 with and without bundles, RED owner and claim paths, a wallet refusal into HALT-3
(simulator verdict `mint-halted-divergence` as well), and per-height claimability of every vault.
The oracle attacker (34 % of tagged blocks at +25 %) moved no median and tripped no halt.

**Divergences found and resolved.**

| # | Where | Divergence | Resolution |
|---|---|---|---|
| 1 | harness | start-up hung: index reports `height: -1` at genesis | `index_reached` accepts heights below `startHeight` (D-RD-DEV-1) |
| 2 | harness | ports outside the shared band; TIME_WAIT counted as busy | `--port-base`, SO_REUSEADDR bind test (D-RD-DEV-1) |
| 3 | harness | carriers dropped from the next block (p2p trickle) and two-step completions fired blocks late (wallet lag) | mempool push before each block; completion detected on `gettxout` and matched on the carrier outpoint (D-RD-DEV-3) |
| 4 | harness / scenarios | every one-block step mined by pool 0 (no pool mix, no dark share) | `MinerPlan` across the schedule (D-RD-DEV-2) |
| 5 | harness | concurrent suites shared run directories | random run-dir suffix |
| 6 | simulator | attestation frame one block behind the engine (`height0` 0): a bundle in the last block "no snapshot" | `BlockSeries.height0` (D-RD-DEV-5) |
| 7 | simulator | DORMANT recorded one block late in `attestor_status` | recorded post-SNAP (D-RD-DEV-5) |
| 8 | replay model | wallet collateral under ARMED priced at xMint | MINT-5 at `min(xMint, aMint)` in the vault replay |

No divergence remains. Limitations that are not compared (timing of wallet transactions, ARMED
claimability flags, abandonment/notices, σ at regtest scale) and the node-wallet finding F-DEV-1 are
in D-RD-DEV-7.

**Reproduce.**

```bash
cd wt/ybcal-devnet
export YBCAL_DEVNET_PORT_BASE=41000
Y6=/path/to/ycash6 DD=/path/to/ycash-dd
.venv/bin/ybcal devnet build --ycash6 $Y6                       # stock column at the pin
B=.work/bin/stock-7702d22606d1/ycashd
.venv/bin/ybcal devnet validate --ycashd $B --ycash6 $Y6 --portseed 0 --parallel 4
.venv/bin/ybcal devnet validate --ycashd $DD/src/ycashd --ycash6 $Y6 --allow-version-skew \
    --portseed 10 --parallel 4
python -c "from ybcal.params.paramset import mainnet; open('mainnet.json','w').write(mainnet().to_json())"
.venv/bin/ybcal devnet build --ycash6 $Y6 --overlay mainnet.json --term-factor 1440
.venv/bin/ybcal devnet validate --ycash6 $Y6 --overlay mainnet.json --term-factor 1440 \
    --ycashd .work/bin/ov-<key>/ycashd --portseed 0 --parallel 4
.venv/bin/ybcal devnet build --ycash6 $DD --ref HEAD --overlay mainnet.json --term-factor 1440
.venv/bin/ybcal devnet validate --ycash6 $DD --ref HEAD --overlay mainnet.json --term-factor 1440 \
    --ycashd .work/bin/ov-<key>/ycashd --portseed 10 --parallel 4
.venv/bin/ybcal devnet diff .work/devnet/<run>       # re-compare one kept run, no node needed
```

About 15 minutes per suite with `--parallel 4` (8 scenarios, 355–601 blocks each).

**Validating a recommended set later.** Pass the report's `recommended.json` (or the report
directory) as the overlay; a mainnet-scale set is scaled automatically:

```bash
ybcal devnet build    --ycash6 $Y6 --overlay reports/<run>/ --term-factor 1440
ybcal devnet validate --ycash6 $Y6 --overlay reports/<run>/ --term-factor 1440 \
    --ycashd .work/bin/ov-<key>/ycashd --portseed 0 --parallel 4 --strict
```

`--term-factor 1440` keeps terms short enough that `vault-cycle` reaches claim heights; the price
scenarios do not depend on it. Repeat with `--ycash6 $DD --ref HEAD` for the v4.5.0 line. A runtime
flag outside the node's accepted range, or a compiled value `RegtestParams()` cannot take, is
refused before anything is built.

---

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `401 Unauthorized`, or `Unable to start HTTP server` | Port clash with another devnet or a functional test. A devnet port seed `s` takes RPC ports 16000 + 12·s … (p2p 5000 lower). Pick another `--portseed`. Beside functional tests, use about ⅔ of the test seed band (doc/yellowback-devnet.md §4). |
| `-28` / connection refused for ~12 s after start | 6.20.0 loads its Orchard parameters before answering RPC. The runner waits up to 180 s per node. |
| A run is slow | About 2 s per block on 6.20.0 (the wallet notifier needs two ticks), times the scenario's block count. Use `--dry-run` to see the count first. |
| `xMint` undefined, pools "pinned" | PIN-1 caught a constant quote. Keep `--jitter-bps` > 0 (the default 10 is enough); a price file with long flat stretches is still fine. |
| `node parameters differ from the overlay` | The binary was built from another commit or without the overlay patch. Rebuild (`ybcal devnet build --overlay …`) or point `--ycashd` at the right cache entry. |
| `a prebuilt binary carries the stock RegtestParams()` | Your overlay changes compiled values; only a build can carry them. |
| `skipped (build): … unreachable` | The depends hosts are blocked (proxy or firewall). Build on a networked machine, or use a CI artifact. |
| `skipped (fetch-binary): … blob.core.windows.net` | GitHub serves artifacts from Azure blob storage. Allow that host, or download in a browser and pass `--ycashd`. |
| A stale run holds ports | Every node runs with `-datadir=<run>/nodeN`. Find leftovers with `pgrep -f 'ycashd.*\.work/devnet'`, stop them, then delete the run dir. |
| `ports [...] are in use` | Another devnet (or another agent's node) holds the slot. Use another `--portseed`; with `--port-base` seeds are 0–40. |
| `yed_mint: transaction commit failed: the transaction was rejected by the mempool` | The wallet re-selected the previous carrier's change output that the previous MINT already spent (finding F-DEV-1, both node lines). Recorded as the action's outcome; the scenario continues. |
| Live tests | `YBCAL_YCASHD=/path/to/ycashd pytest -m devnet` (add `YBCAL_ALLOW_SKEW=1` for the CI binary). |

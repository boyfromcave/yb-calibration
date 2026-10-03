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
4. **Build.** The first build runs `./zcutil/build.sh -j N` (depends, configure and a full build:
   35–60 min cold). After that it generates the cxx bridge headers, then runs
   `make -C src -j N ycashd ycash-cli` (about 2 min incremental), as `doc/yellowback-devnet.md` §0
   describes.
5. **Cache.** It copies the binaries to `.work/bin/<key>/` with a `manifest.json` (commit, compiled
   values, patch). `key` is `stock-<commit12>` for the stock column, or `ov-<sha16>` of the
   compiled values. The six runtime flags are not part of the key, so varying them never rebuilds.
   `--force` rebuilds.

On macOS, put GNU `libtool`/`coreutils` first in `PATH`, as the ycash6 devnet guide shows. Logs go
to `.work/logs/build-<key>-<time>.log`.

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
- `--launcher` drives `contrib/yellowback/devnet/yellowback-devnet up --no-viz` from a worktree
  instead. That gives you the attestor seats with real `yellowback-attest` agents, which
  `attestor-outage-1` needs. The launcher hard-codes `-yellowbackstartheight=1
  -yellowbacksigmaref=0` and passes no other runtime flag, so it is only used when the overlay's
  flags equal those.

### Built-in scenarios (regtest scale, deterministic in `--seed`)

| Scenario | Program | Needs |
|---|---|---|
| `calm` | random walk ±0.5 %/block for 3 slow windows | — |
| `crash-70` | a calm slow window, then a linear fall to 30 % over one fast window, then 2 slow windows | — |
| `hashrate-drop` | signalling share 80 % → 45 % for two signal windows → 80 % | dark miner (automatic) |
| `attestor-outage-1` | one attestor's agent stopped for the middle third | `--launcher` + `yellowback-attest` |
| `oracle-attack-34` | a pool holding 34 % of the tagged blocks quotes +25 % for two mid windows | — |

At the shipped regtest windows a scenario is 359–487 blocks (bootstrap included), about 12–17 minutes at
~2 s a block.

---

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

`ybcal.devnet.scrape.load_history_csv` reads `history.csv` back into typed records.

---

## 6. The differential suite

```bash
ybcal devnet validate                       # all five scenarios
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

Each scenario ends in one status:
- `pending WP-3..5` only if `ybcal.sim.engine.simulate_devnet` cannot be imported (it exists since
  WP-3, D-WP3-7; the contract is in D-WP9-5);
- `skipped: <reason>` without a binary;
- `pass`, `fail` or `error`.

The suite report goes to `.work/devnet/validate-<time>.json`. Only an all-pass suite is
"validated".

---

## 7. Troubleshooting

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
| Live tests | `YBCAL_YCASHD=/path/to/ycashd pytest -m devnet` (add `YBCAL_ALLOW_SKEW=1` for the CI binary). |

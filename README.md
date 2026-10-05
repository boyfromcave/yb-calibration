# yb-calibration

`ybcal` is the parameter calibration tool for **Ycash Yellowback (YED)**, the decentralized dollar
overlay on Ycash. It takes the Yellowback parameter set (`yellowback::Params` in
`boyfromcave/ycash6`, `src/yellowback/params.cpp`, branch `feature/yellowback`, pinned at commit
`7702d22`) and produces, for **every** parameter, a recommended value, the current value, a verdict,
the decision rule that produced it, the evidence and a plain-English explanation, plus a
ready-to-review `params.cpp` patch.

It does that by:

- reading the parameter set straight from source and classifying each field as **locked**
  (consensus-shaped; changes only with a new parameter set), **excluded** (wallet/agent/node-local;
  a patch release can change it), per-release, derived or constant;
- simulating the overlay with the node's **exact integer arithmetic** (checked against ycash6's own
  reference model, golden vector and C++ worked examples);
- stressing each parameter group against real or synthetic YEC price histories and a library of
  stress scenarios (crashes, oracle attacks, feed outages, hashrate loss, attestor capture, absent
  owners and developers);
- scoring every candidate against a **risk policy the owner writes** (`policy/default.toml`);
- optionally checking the results on a regtest devnet of real `ycashd` nodes.

The report stays in this repo. `ybcal` never writes to ycash6; applying the patch is a human
decision.

## Why

- **Risk mitigation.** A locked value is frozen among enforcing miners for a whole parameter-set
  year, and the first set is the one most likely to stick. Each value is tested, before it is
  frozen, against the failure it guards: bad debt when the claim path opens, oracle manipulation,
  false halts, attestor capture, liquidation larger than the market can absorb.
- **Continuity.** Owners, pools, attestors and the developers each get a quantified window:
  owner grace against P(miss), false-halt hours per year, bundle liveness, the abandonment window
  against the freeze-then-fix runbook, the renewal deadline before the sunset.
- **Minimal change.** A value moves off its shipped setting only when the evidence shows a
  *material* improvement (policy `materiality`, 20 % by default) or the shipped value violates the
  policy. Otherwise the verdict is KEEP, with the evidence. Findings that no parameter can fix are
  reported as design notes, never disguised as parameter changes.

## Status

**The first real-data recommendation is in [docs/reports/2026-10-real/](docs/reports/2026-10-real/README.md)**
(October 2026: real YEC prices, the real pool landscape, validated on regtest devnets of both node
lines; not yet lock-ready — see its §7).

All work packages of [the plan](docs/PLAN.md) are built (see its "Status (implementation)"
section). `ybcal recommend --budget quick --synthetic` produces a complete report covering all 96
registry entries in about 10 minutes on 4 cores (milestone M5).

Two milestones need the owner's machine:

- **M6, devnet validation**: done 2026-10-03. The differential suite (8 scenarios, incl. vaults,
  claims, attestation and PIN) passes block for block on ycash6 (pin `7702d22`) and ycash-dd, with
  the shipped regtest column and the scaled shipped mainnet set. See
  [docs/devnet.md §7](docs/devnet.md#7-validation-results-2026-10-m6) and D-RD-DEV-1..7.
- **M7, real data**: until real YEC prices and exchange spreads are supplied, every price-driven
  verdict is **PROVISIONAL** and the lock-readiness checklist fails by design. See
  [Real-data workflow](#real-data-workflow).

## Quickstart (synthetic data, no network)

Requires Python 3.11 or later and `make`.

```bash
make setup                      # .venv + pip install -e '.[dev]'
source .venv/bin/activate       # puts `ybcal` on PATH (or call .venv/bin/ybcal)

ybcal verify                    # 126 exactness checks: golden replay, C++ worked examples, parity
ybcal params show --group G3    # the parameter table (from the committed snapshot of ycash6 @ 7702d22)
ybcal params show --class excluded
ybcal params check              # drift vs registry + PLAN §1.4 invariants; non-zero on failure

ybcal recommend --budget quick --synthetic --out reports/quick-synthetic   # ≈ 10 min on 4 cores
ybcal report open reports/quick-synthetic           # prints the path of report.html
ybcal report open reports/quick-synthetic --serve   # or serve it on http://127.0.0.1:8000/
```

`make quick` runs the same recommend command (`make quick OUT=reports/x` to choose the directory).
With a ycash6 clone, point the tool at it to read live source and to check the patch with
`git apply --check` in a throwaway worktree:

```bash
export YBCAL_YCASH6=~/src/ycash6
ybcal params check --ycash6 "$YBCAL_YCASH6" --policy policy/default.toml --release-tip 3052055
ybcal params extract --out params-7702d22.json
```

What the report contains and how to read the verdicts (KEEP / CHANGE / PROVISIONAL / BLOCKED) is
in [docs/report-guide.md](docs/report-guide.md).

### Smaller runs

```bash
ybcal study G3 --synthetic --out reports/g3              # one group, one round, mini report
ybcal study all --synthetic                              # every group, one round, no sensitivity
ybcal recommend --synthetic --groups G1,G2 --no-sensitivity --workers 2
ybcal sensitivity --set reports/quick-synthetic --params grace,feeBps --method sobol
ybcal recommend --manifest reports/quick-synthetic/manifest.json   # reproduce a run exactly
```

## Your risk policy

Every tolerance is a key in `policy/default.toml`, and every key is explained, with guidance on how
to choose it, in [docs/policy.md](docs/policy.md). The shipped values are starting points, and
several are placeholders (demand, outages, YEC volume) that you should replace.

```bash
cp policy/default.toml policy/mine.toml
ybcal params check --policy policy/mine.toml
ybcal recommend --policy policy/mine.toml --budget quick --synthetic
```

## Real-data workflow

The final mainnet recommendation needs real YEC data (milestone M7). The full checklist, formats and
durations are in [data/README.md](data/README.md); models and provenance rules in
[docs/data.md](docs/data.md).

**1. Fetch on a networked machine** (this repo's development sandbox cannot reach the price APIs;
`ybcal data fetch` exits 3 with a clear message when the network is blocked). Files go to
`data/local/`, which is gitignored; keep the `.provenance.json` sidecars next to them.

```bash
mkdir -p data/local
ybcal data fetch --source coingecko --days 365 --granularity hourly --out data/local/yec-hourly.csv
ybcal data fetch --source coingecko --days 3650 --granularity daily --out data/local/yec-daily.csv
# optional venue snapshots, e.g. from cron every 15 minutes:
ybcal data fetch --source tickers --exchange safe_trade --out data/local/tickers.csv
ybcal data fetch --source nonkyc --symbol YEC_USDT --out data/local/nonkyc.csv
```

**2. Log exchange spreads for at least two weeks** with ycash6's own logger (the attestor agents'
sources), then copy the CSV in:

```bash
# in a ycash6 checkout
python3 contrib/yellowback/attest/calibrate/spreads.py log --out spreads.csv --interval 300 --duration 14
cp spreads.csv /path/to/yb-calibration/data/local/spreads.csv
```

Optional: a pool-share series (`height,payout_key`, one row per block, ≥ 1 month) as
`data/local/pool-shares.csv`, and order-book depth as `data/local/depth.csv`.

**Several price files.** Give the hourly and the daily series together; each is assigned a role by
its own native granularity, not by argument order: the finest series is `price` (every study), a
daily one beside it is `price_daily` (the long history for long-horizon evidence). Two files of the
same granularity are refused. `--window full|last365|2021-22|2025-26|lastN|YYYY-MM-DD:YYYY-MM-DD`
restricts every price series to a date range (spreads, depth and pool shares are not windowed):

```bash
D=data/local
ybcal recommend --budget quick --data $D/yec-hourly.csv --data $D/yec-daily.csv \
    --data $D/spreads-reconstructed.csv --data $D/pool-shares.csv --data $D/depth.csv --window last365
```

**Robustness across seeds, windows and price models** (`ybcal robust`, D-RD-INF-5). Each
combination is an ordinary `recommend` run in `<out>/runs/<window>__<model>__s<seed>/`; finished runs
are skipped on a re-run (resumable); CPU = `--jobs` × `--workers` at `nice` 10. Models: `bootstrap`
(default), `regime` / `garch` (fitted on the daily series, centred: policy `real_price_model`),
`martingale` (policy `price_drift`), or `a+b`. Any policy key can be overridden per run with
`--policy-set KEY=VALUE` (also on `recommend` and `study`).

```bash
D=data/local
nice ybcal robust --out .work/robust/g9 --budget standard --groups G9 --seeds 3 \
    --windows full,last365,2021-22,2025-26 --models bootstrap,regime,martingale \
    --policy policy/real-data-2026-10.toml --data $D/yec-hourly.csv --data $D/yec-daily.csv \
    --data $D/spreads-reconstructed.csv --data $D/pool-shares.csv --data $D/depth.csv --workers 2 --jobs 2
# → .work/robust/g9/robust.md (unstable parameters first), robust-summary.csv, robust.csv, robust.json
ybcal robust ... --table-only    # re-tabulate what has finished
ybcal robust --out .work/robust/x --runs reports/a reports/b   # tabulate any finished recommend dirs
```

Besides the agreement table, each parameter gets a **consolidated** value: the candidate whose own
rule's constraints hold in the most runs (every evaluated candidate counts, not only each run's
winner), ties → closest to current, reported as "feasible in k/N runs" with the per-run violations.

**3. Import and inspect** (row counts, duplicates, gaps, fitted models):

```bash
ybcal data import data/local/yec-hourly.csv --kind price
ybcal data import data/local/spreads.csv --kind spreads
ybcal data import data/local/pool-shares.csv --kind hashrate
ybcal data import data/local/depth.csv --kind depth
ybcal data describe data/local/yec-hourly.csv          # realised vol, drawdowns, tail index, gaps
ybcal data synth --model garch --calibrate data/local/yec-hourly.csv --paths 1000 --years 5 \
    --out data/local/garch-5y.npz                      # synthetic ensemble fitted to real YEC
```

**4. Set the policy keys real data informs**, at least `yec_daily_volume_p10_usd` (enables the
supply-cap and `maxMint` depth checks), `release_tip` / `release_tip_date` for the planned release,
and `expected_enforcing_share` / `expected_pool_count` from the pool survey
([docs/policy.md](docs/policy.md), "Placeholders to replace before a lock").

**5. Recommend.** Without `--synthetic`, every file in `data/local/` is read and its kind sniffed
from its header; `--data` (repeatable, files or directories) selects explicitly.

```bash
ybcal recommend --budget standard --policy policy/mine.toml          # ≈ 1 hour
ybcal recommend --budget deep --policy policy/mine.toml --cache .work/cache   # overnight
ybcal recommend --data data/local/yec-hourly.csv --data data/local/spreads.csv --budget standard
```

The lock-readiness checklist at the end of the report says whether the set can be locked: every
locked parameter non-provisional and backed by real data, nothing BLOCKED, every invariant passing,
the release study clean.

## Devnet validation (optional)

`ybcal devnet` replays scenarios on a regtest devnet of real `ycashd` nodes and compares the node
with the simulator block by block. Where no node can be built or run, every command prints
`skipped: <reason>` and exits 0 (`--strict` exits 3); nothing is faked. The full guide is
[docs/devnet.md](docs/devnet.md).

```bash
ybcal devnet build --ycash6 ~/src/ycash6                              # stock regtest column (35–60 min cold)
ybcal devnet build --ycash6 ~/src/ycash6 --overlay reports/<run>/recommended.json --dry-run
ybcal devnet build --from-ci-run 37081639884                          # CI binary: built from 94bafa4, needs --allow-version-skew
ybcal devnet run --scenario crash-70 --overlay reports/<run>/recommended.json --seed 7
ybcal devnet validate                                                 # the differential suite
ybcal devnet validate --scenario calm --scenario crash-70 --out suite.json
```

## Make targets

```
make setup            create .venv and install ybcal with dev extras
make test / test-all  pytest without / with devnet and slow tests
make lint             ruff
make verify           ybcal verify
make params-check     ybcal params check (YCASH6=path reads live source)
make quick            recommend --budget quick --synthetic (OUT=dir)
make recommend        recommend (BUDGET=quick|standard|deep, OUT=dir)
make docs / docs-check  regenerate / check docs/parameters.md from the registry
make devnet-build     ybcal devnet build (YCASH6=path)
make devnet-validate  ybcal devnet validate
make clean            remove caches (never touches data/local or reports)
```

## Environment variables

| Variable | Effect |
|---|---|
| `YBCAL_YCASH6` | path of a ycash6 clone (live source reads, patch check, devnet builds); otherwise the committed snapshot is used |
| `YBCAL_YCASHD` | a `ycashd` binary for the devnet commands |
| `YBCAL_COINGECKO_API_KEY` | optional CoinGecko demo key for `data fetch` |
| `YBCAL_WORK` | work directory for worktrees, caches and devnet runs (default `.work/`) |
| `YBCAL_NO_YCASH6=1` | force the no-clone (CI) path in tests |
| `YBCAL_ALLOW_SKEW=1` | let live devnet tests use a binary that predates the pin |
| `YBCAL_DEVNET_PYTHON` | Python interpreter for ycash6's `yellowback-devnet` launcher (`devnet run --launcher`) |

## Repository map

```
README.md                 this file
Makefile                  common tasks (`make` lists them)
pyproject.toml            package metadata; console script `ybcal`
policy/default.toml       the owner's risk policy (copy and edit)
scenarios/*.toml          stress-scenario library
data/README.md            what real data to gather; user data goes in data/local/ (gitignored)
src/ybcal/
  cli.py                  the `ybcal` command tree (ROUTES)
  config.py               Policy, RunManifest
  params/                 registry, extraction, invariants, doc generator, scaling, patch emitter
  model/                  vendored reference model, exact and vectorised kernels, `ybcal verify`
  data/                   fetchers, loaders, synthetic models, scenarios, describe
  sim/                    block-mode engine, oracle, σ, supply, activation, attestation;
                          hour-mode vault book, agents, fees, metrics
  studies/                G1–G9 and release studies, `ybcal study`
  optimize/               search, evaluation, robustness, Pareto, sensitivity, joint pass
  report/                 report builder, explanations, plots, templates, `recommend` / `report open`
  devnet/                 worktree, overlay, build, runner, scrape, differential check
tests/                    unit, property, golden, simulator exactness, studies, CLI, devnet (marked)
docs/                     see below
reports/                  report output (gitignored)
```

## Documentation

| Document | What it covers |
|---|---|
| [docs/PLAN.md](docs/PLAN.md) | the implementation plan, with an implementation status section at the top |
| [docs/methodology.md](docs/methodology.md) | how recommendations are made and how far to trust them; known limitations |
| [docs/report-guide.md](docs/report-guide.md) | reading the report: sections, verdicts, provenance, lock readiness, the patch |
| [docs/policy.md](docs/policy.md) | every policy key: meaning, unit, default, which study reads it, how to choose it |
| [docs/parameters.md](docs/parameters.md) | every parameter (generated from the registry by `ybcal params doc`) |
| [docs/studies/](docs/studies/) | one page per study: [G1](docs/studies/g1.md) price medians, [G2](docs/studies/g2.md) volatility, [G3](docs/studies/g3.md) collateral, [G4](docs/studies/g4.md) grace and abandonment, [G5](docs/studies/g5.md) activation, [G6](docs/studies/g6.md) judgement and fees, [G7](docs/studies/g7.md) supply and halts, [G8](docs/studies/g8.md) attestation, [G9](docs/studies/g9.md) amounts, [release](docs/studies/release.md) |
| [docs/data.md](docs/data.md) | data sources, synthetic models, fitting, provenance |
| [data/README.md](data/README.md) | the real-data checklist |
| [docs/scenarios.md](docs/scenarios.md) | the stress-scenario library and its schema |
| [docs/devnet.md](docs/devnet.md) | devnet build, run, validation, version skew, troubleshooting |
| [docs/architecture.md](docs/architecture.md) | module map, frozen contracts, APIs, performance |
| [docs/decisions.md](docs/decisions.md) | decision log with an index |

## Development

```bash
make test lint          # pytest (no devnet) + ruff
make docs-check         # docs/parameters.md matches the registry (= ybcal params doc --check)
ybcal params doc        # regenerate docs/parameters.md after a registry change
YBCAL_YCASH6=~/src/ycash6 make test       # also run the live-source tests
YBCAL_YCASHD=/path/to/ycashd make test-all   # include devnet tests
```

Conventions (integers for every consensus quantity, seeded randomness, provenance tags, read-only
towards ycash6, naming: *Yellowback* is the system, *YED* the unit) are in
[docs/architecture.md](docs/architecture.md).

## License

MIT (see [LICENSE](LICENSE)).

# ybcal architecture

How the code is laid out, which contracts are frozen, and the conventions every work package
follows. The plan is [PLAN.md](PLAN.md); the parameter table is [parameters.md](parameters.md)
(generated); decisions are logged in [decisions.md](decisions.md).

## Module map and owners

| Path (`src/ybcal/`) | Owner | Role |
|---|---|---|
| `__init__.py`, `units.py`, `types.py` | WP-0 | version; integer units (`COIN`, `BPS`, `BLOCKS_PER_*`, `ceil_div`, `lower_median`); shared types (`ParamValue`, `Provenance`, `PricePath`) |
| `cli.py` | WP-0 | argparse tree of PLAN §3.2 and the dispatch table `ROUTES` |
| `config.py` | WP-0 | `Policy` (from `policy/default.toml`), `RunManifest`, `sha256_file` |
| `params/registry.py` | WP-0 | `ParamSpec`, `REGISTRY` (single source of truth), `PINNED_COMMIT` |
| `params/paramset.py` | WP-0 | immutable `ParamSet`, `mainnet()`, `regtest()`, `candidate()` |
| `params/extract.py` | WP-0 | parse params.h/params.cpp at a git ref → `Extracted`; `check_drift`; snapshot loader |
| `params/invariants.py` | WP-0 | PLAN §1.4 checks: `INVARIANTS`, `Context`, `Violation`, `check_all` |
| `params/doc.py`, `params/cli.py` | WP-0 | `docs/parameters.md` generator; `ybcal params …` |
| `params/snapshot_7702d22.json` | WP-0 | extraction at the pin, used when no ycash6 clone is present (CI) |
| `params/scaling.py` | WP-9 | mainnet → regtest time scaling (§6.3) |
| `params/emit.py` | WP-8 | recommended set → `params.cpp` patch + JSON (§7) |
| `model/` | WP-1 | vendored reference model, exact kernels, vectorised kernels, `ybcal verify` |
| `data/` | WP-2 | loaders, fetchers, synthetic models, scenarios |
| `sim/engine,oracle,sigma,supply` | WP-3 | block-mode simulator core |
| `sim/vaults,agents,fees` | WP-4 | vault book, personas, fees; hour mode + oracle transfer kernel |
| `sim/activation,attest` | WP-5 | ACT-1..7; attestor set, bundles, PIN, dormancy |
| `optimize/search,robust,pareto,sensitivity` | WP-6 | search, robust aggregation, Pareto, Morris/Sobol |
| `optimize/joint.py` | WP-8 | coordinate descent over groups (§5.10) |
| `studies/base.py` | WP-0 | the Study contract (below) |
| `studies/g1,g2,g5` | WP-7a | price windows, volatility, activation |
| `studies/g3,g4,g9,release` | WP-7b | collateral, grace/abandon, amounts, release |
| `studies/g6,g7,g8` | WP-7c | miners/fees, supply/halts, attestation |
| `devnet/` | WP-9 | worktree, overlay, build, run, scrape, differential check |
| `report/` | WP-8 | report assembly, explanations, plots, templates |

Every stub module carries a docstring and `OWNER_WP = "WP-n"`. A WP edits only its own files;
anything shared goes through the WP-0 types. If a WP needs a change to a frozen contract, it records
the request in `docs/decisions.md` rather than editing another WP's file.

## Frozen contracts (WP-0)

### Parameters

```python
# ybcal.params.registry
PINNED_COMMIT = "7702d22"
@dataclass(frozen=True)
class ParamSpec:
    name: str; mainnet: ParamValue; regtest: ParamValue
    klass: Literal["locked","excluded","per-release","constant","derived","meta"]
    group: str            # "G1".."G9", "R", "-"
    unit: str             # blocks height bps zat cents micro-usd count enum bool hex string
    hashed: bool; rules: tuple[str, ...]
    derive: Callable[[Mapping[str, ParamValue]], ParamValue] | None
    bounds: tuple[int, int]; step: int; doc: str
    consensus: bool       # read by a consensus rule → a change is a locked change
    origin: Literal["field","header"]; cpp_expr: str; parents: tuple[str, ...]
    regtest_flag: str | None; note: str; pinned_commit: str
    # properties: cpp_field, index, change_path ("locked"|"patch-release"|"per-release"|
    #             "protocol-constant"|"meta"), tunable
REGISTRY: dict[str, ParamSpec]
get(name) / specs(group=, klass=, origin=) / params_for_group(g) / field_names() / derived_names()

# ybcal.params.paramset
class ParamSet(Mapping[str, ParamValue]):        # every registry key, immutable, hashable
    network: str; is_regtest_scale: bool
    as_int(key) -> int; array("baseRatioBps") -> tuple[int, ...]
    replace(changes: Mapping | None = None, /, **kw) -> ParamSet   # recomputes derived unless given
    derived_mismatches() -> dict[str, tuple[held, formula]]
    check(context: Context | None = None) -> list[Violation]
    diff(other) -> dict[str, tuple[a, b]]; delta(base) -> dict[str, value]
    to_dict() / to_json() / digest() / from_dict() / from_json() / from_extracted(ex, network)
mainnet() / regtest() / candidate(base=None, **changes)   # candidate: network="candidate"

# ybcal.params.invariants
Context(release_tip=None, next_upgrade_height=None, release_lead_blocks=16128,
        runbook_buffer_blocks=0, max_entity_share_bps=None, worst_price_microusd=PRICE_MAX)
Context.from_policy(policy, release_tip=None, next_upgrade_height=None)
Violation(invariant, rule, message, params); Invariant(name, rule, doc, params, fn, scope, proposed)
INVARIANTS: dict[str, Invariant]; check_all(ps, context=None) -> list[Violation]

# ybcal.params.extract
extract(repo, ref=PINNED_COMMIT, *, regtest_flags=None) -> Extracted
Extracted(ref, commit, fields, constants, networks={"main","test","regtest"}, regtest_flags)
    .values(network) -> registry-keyed dict; .to_json(); .from_dict()
check_drift(registry, extracted) -> list[Drift]; load_snapshot() -> Extracted
```

Array fields are addressed as `"classMin[0]"`; pass them to `replace` through the mapping argument.
`network` is an ordinary key: `"main"`, `"test"`, `"regtest"` for sets read from source,
`"candidate"` for mainnet-scale proposals. Only `"regtest"` is regtest scale.

### Studies (`ybcal.studies.base`)

```python
class Study(Protocol):
    group: str; params: tuple[str, ...]           # = registry.params_for_group(group)
    def space(self, base: ParamSet, budget: Budget) -> Iterable[ParamSet]   # must include base
    def evaluate(self, cand: ParamSet, env: Env) -> Metrics
    def decide(self, results: ResultTable, policy: Policy) -> list[Recommendation]
    def explain(self, rec: Recommendation, results: ResultTable) -> str
Budget(name, paths, block_horizon_days, hour_horizon_years, grid_points, lhs_samples,
       halving_rounds, scenario_set, morris_trajectories, sobol_samples, max_minutes); BUDGETS
Env(policy, budget, seed, data={}, scenarios={}, provenance="synthetic"); .rng; .rng_for(*keys); .price()
Metrics(values, primary, minimize=True, constraints={}, provenance="synthetic", meta={})
ResultRow(delta, params, metrics)
ResultTable(base): add, filter, feasible, current, distance, argmin, argmax, best, column,
                   param_values, to_csv
Recommendation(param, current, recommended, verdict, rule, binding, metrics, sensitivity,
               confidence, provenance, evidence, group, notes, explanation)
    .changed, .change_path, .klass_note, .to_dict()
decide_with_materiality(table, materiality|policy, *, params=None, metric=None, minimize=None)
    -> MaterialityDecision(row, verdict KEEP|CHANGE|BLOCKED, improvement, reason)
final_verdict(verdict, provenance) -> KEEP|CHANGE|PROVISIONAL|BLOCKED
STUDY_MODULES, GROUP_ORDER, load_study(group), missing_recommendations(study, recs)
```

Each study module exposes `make_study() -> Study`. Every study evaluates the current set (the
materiality rule needs it), returns a `Recommendation` for **every** name in `params` (derived
ones included: recommend the parent, report the derived value), and passes the verdict through
`final_verdict` so synthetic-only evidence becomes PROVISIONAL. Groups `"-"` (identity, design
choice `bundleCarrier`, protocol constants) get verification rows from the report, not a study.

### Policy and manifests (`ybcal.config`)

`Policy` is a frozen dataclass whose defaults equal `policy/default.toml` (a test enforces it).
TOML sections are organisational only; keys are unique; unknown keys raise. Use
`Policy.load(path)`, `policy.replace(...)`, `policy.digest()`, `policy.max_bad_debt(cls)`.
`RunManifest.create(budget=, seed=, policy=, data_files=, …).save(path)` / `RunManifest.load(path)`.

## CLI dispatch convention

`ybcal.cli.ROUTES` maps each command path to a `Route(module, func, wp)`:

| Command | Implemented in | Owner |
|---|---|---|
| `params show/extract/check/doc` | `ybcal.params.cli.cli_<sub>` | WP-0 |
| `data fetch/import/synth/describe` | `ybcal.data.cli.cli_<sub>` | WP-2 |
| `verify` | `ybcal.model.cli.cli_verify` | WP-1 |
| `sensitivity` | `ybcal.optimize.cli.cli_sensitivity` | WP-6 |
| `study`, `recommend`, `report open` | `ybcal.studies.cli.cli_study`, `ybcal.report.cli.cli_recommend` / `cli_open` | WP-8 |
| `devnet build/run/validate` | `ybcal.devnet.cli.cli_<sub>` | WP-9 |

Rules:

1. The handler signature is `cli_<name>(args: argparse.Namespace) -> int` (the exit code).
2. `cli.py` already declares the PLAN §3.2 arguments. To add more, define
   `configure_<name>(parser: argparse.ArgumentParser) -> None` in the same module; it is called
   when the parser is built. Do not edit `cli.py`.
3. While the module or function is missing, the command prints
   `ybcal <cmd>: not implemented yet (WP-n)` and exits **2**. An `ImportError` raised *inside* an
   existing module is not swallowed.
4. `args.command_path` holds the command words (e.g. `["data", "fetch"]`).

WP-7 study agents test through the library (`load_study`, `ResultTable`, …); the `study` command's
driver lands with WP-8.

## Conventions

- **Integers for every consensus quantity**: zat, cents, µUSD per YEC, bps, blocks. Floats are
  allowed only in statistics, metrics and policy tolerances, never in a kernel that mirrors a rule.
  Use `units.ceil_div` and `units.lower_median` rather than float rounding.
- **Seeds**: every random draw comes from `Env.rng_for(*keys)` (a `SeedSequence` of the run seed
  and stable CRC32 keys), so a result does not depend on evaluation order or process.
- **Provenance**: every `Metrics`/`Recommendation` carries `real-data`, `synthetic` or
  `judgement`; synthetic-only recommendations are PROVISIONAL.
- **Source reads**: ycash6 is read with `git show <ref>:<path>` at `PINNED_COMMIT`, never from a
  working tree, never written to. Re-pinning is a decision (log it) plus a snapshot regeneration:
  `ybcal params extract --ycash6 PATH --ref NEW --out src/ybcal/params/snapshot_<NEW>.json`.
- **Naming**: Yellowback is the system, YED the unit. No `DigiDollar` / `ydollar` names.
- **Tests**: `pytest -m "not devnet"` must pass without a ycash6 clone (tests fall back to the
  snapshot); set `YBCAL_YCASH6` to run the live-source tests; `YBCAL_NO_YCASH6=1` forces the
  CI path locally.

## Data (WP-2)

Package `ybcal.data` (docs: [data.md](data.md), [scenarios.md](scenarios.md),
[data/README.md](../data/README.md)). `PricePath` is the frozen WP-0 type; nothing in the
contracts changed. Public API:

```python
# ybcal.data.pricepath — helpers around ybcal.types.PricePath
DT_BLOCK = 1/420_480; DT_HOUR = 1/8_760; STEP_SECONDS = {"block": 75, "hour": 3600}
steps_per_year(res) / resolution_for_dt(dt)
usd_to_micro(usd, *, clamp=True) -> int64; micro_to_usd(micro); clamp_prices(prices, *, keep_gaps=True)
make_path(t0, res, prices, provenance, meta=None) / from_usd(t0, res, usd, provenance, meta=None)
timestamps(pp); slice_steps(pp, start, stop=None); select_paths(pp, idx); with_meta(pp, **meta)
resample(pp, "block"|"hour", *, method="hold"|"loglinear"); log_returns(pp) -> (paths, n-1), NaN at gaps
to_csv / from_csv / to_npz / from_npz / save / load      # CSV + .meta.json sidecar, or .npz

# ybcal.data.loaders
parse_ts(value) -> unix s; gap_report(ts, expected_step=None, factor=1.5) -> GapReport
load_price_csv(src) -> PriceSeries(ts, price_usd, …, duplicates, conflicting_duplicates, volume_usd)
resample_to_grid(series, res="hour", *, t0=None, n_steps=None, max_ffill_seconds=None)
    -> Resampled(path, filled, gaps)          # path.meta["filled"] = the forward-fill mask
load_spreads_csv(src) -> SpreadsLog(ts, prices (n, 3) µUSD 0=missing, names, errors)   # spreads.py columns
    .pair_spreads_bps(), .missing(), .gaps(300, 3.0); write_spreads_csv(log, file)
load_pool_shares_csv(src) -> PoolShareLog(heights, pool, keys) .shares() .rolling_shares(w)
load_depth_csv(src) -> DepthSeries(ts, depth_2pct_usd, volume_24h_usd, bid_depth_2pct_usd, form)
import_file(path, kind)                         # kind: price | spreads | hashrate | depth

# ybcal.data.fetch (network; tests patch fetch.urlopen)
HttpClient(timeout, retries, backoff, min_interval, max_body, sleep, clock).get_json(url, headers)
fetch_coingecko_market_chart(days, *, coin, vs, granularity="auto"|"hourly"|"daily", api_key, client, now)
fetch_coingecko_tickers(*, coin, exchanges, api_key, client, now); fetch_nonkyc_market(*, symbol, client, now)
fetch_to_csv(source, out, …) -> FetchResult     # + <out>.provenance.json (source, urls, fetched_at, sha256)
FetchError, NetworkBlockedError

# ybcal.data.synthetic
GBM, Merton, Garch, RegimeSwitch, BlockBootstrap  (PriceModel):
    .simulate(n_paths, n_steps, dt, rng, p0=DEFAULT_P0) -> PricePath("synthetic")
    .log_returns(n_paths, n_steps, dt, rng); .expected_log_drift(); .params()
    cls.fit(pp, path=0); cls.fit_returns(r, dt); cls.preset()
MODELS; preset(name, **overrides); fit(name, pp); preset_table(); simulate_years(model, n, years, res, rng)
SpreadModel(...).generate(true, rng) -> SourceQuotes; SpreadModel.fit(SpreadsLog)
PoolModel(...).assign(n_paths, n_blocks, rng, shares=None, shares_step_blocks=1)
          .generate(true_blocks, rng, *, shares=None) -> PoolBlocks(miner, tagged, quote); PoolModel.fit(PoolShareLog)
HashrateDrift(...).simulate(shares0, n_steps, rng, *, n_paths, step_blocks=48); HashrateDrift.fit(PoolShareLog)
renewal_outages(n_paths, n_units, n_steps, step_seconds, rate_per_day, mean_hours, rng)

# ybcal.data.scenarios
SCENARIO_DIR; SCHEDULES; load_library(dir=None) -> {name: Scenario}; scenario_set("core"|"all"); get(name)
Scenario.generate(rng, n_paths=1, *, data=None, base_path=None, resolution=None, horizon_days=None, p0=None)
    -> ScenarioRun(scenario, paths: PricePath("scenario"), schedules: {name: (n,) | (n_paths, n)}, constants)
Scenario.n_steps(res=None, horizon_days=None); .apply_program(base_r, res, rng); .core

# ybcal.data.describe
describe(pp) -> dict; format_description(d); realised_vol(pp); rolling_vol(pp, w); max_drawdown(prices)
drawdown_distribution(pp, horizons_days); hill_tail_index(r, tail, k); autocorrelation(x, lags); gap_stats(pp)
```

`Env.scenarios` is expected to hold `Scenario` objects (from `scenario_set(budget.scenario_set)`);
`Env.data["price"]` a real `PricePath` from `loaders.resample_to_grid(...).path` (with
`meta["filled"]`). CLI: `ybcal.data.cli.cli_{fetch,import,synth,describe}` with
`configure_{fetch,import,synth,describe}` extras; `data fetch` exits 3 when the network is blocked.

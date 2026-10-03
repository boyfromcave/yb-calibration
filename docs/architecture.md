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

## Optimizer (WP-6)

`src/ybcal/optimize/` turns a Study into candidates, scores, and evidence. Everything is built on the
frozen contracts above; nothing here changes them.

| Module | Public API |
|---|---|
| `evaluate.py` | `evaluate_many(fn, cands, env, *, workers=1, cache=None, crn=True, evaluator=None, stats=None) -> list[Metrics]`; `evaluate_set(study_or_fn, ps, env, *, cache=None)`; `candidate_env(env, cand, *, crn=True)`; `with_budget(env, b)`, `with_paths(env, n)`; `EvalCache(directory=None)` / `EvalCache.on_disk(".work/cache")`; `env_fingerprint(env)`, `fingerprint(obj)`, `evaluator_id(fn)`; `EvalStats`; `rank_key(m)` |
| `search.py` | `axis(name, base, *, bounds=, step=, values=) -> Axis`; `SearchSpace.for_params(base, params, *, bounds=, steps=, values=, couple=, feasible=, context=, network=)`; `grid(space, *, points=, cap=4096)`; `latin_hypercube(space, n, *, seed=)`; `neighbourhood(space, around=None, *, k=1, params=, mode="axis"\|"full")`; `screen(sets, base, *, context=, feasible=)`; `successive_halving(fn, cands, env, *, rounds=, eta=3, min_paths=8, keep=, workers=, cache=) -> HalvingResult`; `halving_schedule(full, rounds, eta, min_paths)`; `Tie(target, source, scale, div, offset)`; `CandidateSet` (`candidates`, `rejected`, `invalid_counts()`, `summary()`), `Rejected` |
| `robust.py` | `cvar(x, alpha, *, minimize, weights)`, `quantile`, `weighted_mean`, `worst_case`, `aggregate(V, how)`; `regret_matrix(V, *, minimize, among)`, `max_regret`; `ScenarioTable(labels, scenarios, values, feasible, weights)` / `.from_tables({scenario: ResultTable})`; `Constraint(name, metric, op, bound, agg)` / `.from_policy(policy, field, metric, key=)`; `robust_select(table, metric, *, rule="minimax_regret"\|"mean"\|"cvar"\|"worst"\|"quantile", constraints=, current=, distance=) -> RobustChoice` (`blocked` flag); `tie_break`, `step_distance` |
| `pareto.py` | `non_dominated_sort(F, directions)`, `pareto_front`, `ranks`, `normalize`, `knee_point`, `select_feasible(F, directions, primary, feasible, *, current=)`, `objectives_from_table(table, metrics)`, `front_for_plot(F, directions, names, labels, *, feasible, primary, current) -> dict` |
| `sensitivity.py` | `Factor(name, lo, hi, levels)` / `Factor.discrete`; `factors_for_params(base, names, *, k_steps=1)`; `ParamSetObjective(fn, env, base, names, *, metric, workers, cache, context)`; `oat(fn, env, base, param, values=None, *, metric, components, points) -> OATResult`; `oat_from_table(table, param, metric)`; `sensitivity_sentence(param, oat_result, metric_name) -> str`; `morris(f, factors, r, *, levels=4, seed) -> MorrisResult`, `morris_design`; `sobol(f, factors, n, *, seed, n_boot, conf) -> SobolResult` (`S1`, `ST`, CIs, `insensitive(thr)`), `saltelli_design`; `ishigami`, `ishigami_indices` |
| `runner.py` | `optimize_group(study, base, env, budget=None, policy=None, *, method="space"\|"grid"\|"lhs"\|"halving", workers=None, **kw) -> (ResultTable, list[Recommendation])`; `run_group(...) -> GroupRun` (table, recommendations, candidates with rejection counts, recommended set, neighbours, halving history, stats, timings, warnings); `recommended_set(base, recs)`; `NeighbourPoint` |

**Flow of `optimize_group`.** Candidates come from `study.space(base, budget)` (`method="space"`,
the default) or from a registry grid / LHS over the study's tunable params; `screen`/`SearchSpace`
reject sets that fail `ParamSet.check(Context.from_policy(policy))` or a feasibility predicate and
count them by invariant; the base is always kept first. Candidates are scored with
`evaluate_many` (or `successive_halving` for `method="halving"`, whose table holds the
full-fidelity survivors), `study.decide` produces the Recommendations, and the runner then scores
±1 step around the *recommended* set for every tunable param. Each Recommendation receives a
`"Search: …"` note (method, counts, rejections, cache use, workers, seconds), and
`sensitivity["neighbours"]` / `sensitivity["local_slope"]` / `sensitivity["local_elasticity"]`
(set only if the study did not set them). A neighbour that beats the recommendation adds a note
saying whether the gain is within or beyond materiality.

**Determinism.** `evaluate_many` scores each candidate with a fresh `Env` copy whose `rng` is
`env.rng_for("evaluate")` — common random numbers across candidates — or, with `crn=False`,
`env.rng_for("evaluate", digest)`. Results are identical for any `workers` value and any order.

**Parallelism.** `workers=None` means `os.cpu_count()`; `workers=1` runs in-process. With
`workers > 1` the evaluation callable and the `Env` go to each worker once (pool initializer), so
**studies must be module-level classes and `Env.data` picklable**; otherwise the evaluation falls
back to serial with a `RuntimeWarning`.

**Cache.** `EvalCache` keys are `sha256(evaluator_id | ParamSet.digest() | env_fingerprint)`, where
the fingerprint covers seed, budget, policy digest, data and scenarios (arrays hashed by content),
provenance and CRN mode. Memory always; JSON files under `.work/cache/<k[:2]>/<k>.json` with
`EvalCache.on_disk()`. Pass one cache to every `run_group` call of the joint pass so repeated sets
are free.

**For WP-8 (`joint.py`).** Coordinate descent is a loop over `GROUP_ORDER` calling
`run_group(load_study(g), current, env, cache=cache)` and `current = recommended_set(current,
run.recommendations)` until nothing moves (`policy.max_rounds_joint`); the Sobol pass is
`sobol(ParamSetObjective(fn, env, current, names, metric=m), factors_for_params(current, names,
k_steps=1), env.budget.sobol_samples)` with `SobolResult.insensitive(policy.insensitive_total_order)`;
the robust pass is `robust_select(ScenarioTable.from_tables(per_scenario_tables), metric,
constraints=[Constraint.from_policy(...)], current=current)`.

## Model and kernels (WP-1)

`src/ybcal/model/` is the exact arithmetic of the overlay. Everything here is checked by
`ybcal verify` (and by `tests/model/`, which run the same checks plus hypothesis property tests).

### Files

| File | Role |
|---|---|
| `reference.py` | **vendored** `qa/rpc-tests/test_framework/yellowback_model.py` @ `7702d22` (MIT header kept). Whole file; the only edit is the 8 lazy `from . import yellowback_attest as ya` lines → `reference_attest` |
| `reference_attest.py` | **vendored** `yellowback_attest.py` (BUNDLE-1 parsing, signature check, W9 `select_attestors`, `bundle_stat` — the golden replay needs them). Edits: its 4 relative-import lines |
| `reference_util.py` | **extracted** verbatim top-level definitions of `yellowback_util.py` / `util.py` that `reference_attest` needs (constants, secp256k1 helpers, `fee_zat` …), with the transitive closure of the names they use. `yellowback_util.py` itself imports the whole node test framework, so it cannot be vendored whole; node drivers are absent on purpose |
| `yellowback_golden.json`, `SERIALISATION.md` | vendored verbatim (package data) |
| `VENDOR.json` | commit + sha256 of every vendored file |
| `vendor.py` | `SPECS` (what is vendored and the exact rewrites), `revendor(ycash6, ref, dest)`, `check_vendored()`, `check_against_source(ycash6)` |
| `kernels.py` | exact scalar kernels (Python ints, `None` = undefined) |
| `vkernels.py` | numpy kernels (int64, `-1` = undefined), property-tested equal to `kernels` |
| `golden.py` | golden replay (state hash `ad7129…49a6`, tip 440, totals) + kernels/vkernels recomputing all 440 stored snapshots |
| `examples.py` | the 97 numeric assertions of `src/test/yellowback_math_tests.cpp` as data |
| `parity.py` | seeded stdlib parity sample (hypothesis is a dev dependency) |
| `cli.py` | `cli_verify`, `configure_verify` (`--revendor`, `--ycash6`, `--ref`, `--samples`, `--seed`, `--quiet`) |

Every vendored `.py` starts with a pin header (source path, full commit, sha256 of the upstream
file, sha256 of the body as written, the rewrites) and `# ruff: noqa`. `check_vendored()` undoes
the rewrites and re-hashes, so any edit fails `ybcal verify`. Re-vendoring:
`ybcal verify --revendor --ycash6 PATH --ref REF` (read-only towards ycash6; a rewrite that no
longer matches upstream fails loudly). After a re-pin also bump `vendor.VENDORED_COMMIT` (D-WP1-1).

### Kernel API (`ybcal.model.kernels`)

Prices µUSD/YEC, amounts zat, YED cents, ratios bps; a price ≤ 0 is undefined like the C++.

| Kernel | Source @ 7702d22 | Delegates to |
|---|---|---|
| `lower_median(values) -> int\|None` | math.h:53 | `reference.lower_median` |
| `window_median_with_fill(prices, fill) -> int\|None` | state.cpp:103 | |
| `min_fill_fast(W)`, `min_fill_slow(W)` | params.h L9 | |
| `price_mint(pf, pm, ps)`, `price_claim(pm, ps)` | state.cpp:1205-1206 | |
| `halt3_divergence(pf, pm, ps, divergence_bps) -> bool` | state.cpp:1238 | |
| `price_combine(xMint, xClaim, aMint, aClaim) -> CombinedPrices(p_mint, p_claim, p_emerg)` | math.h:286 | |
| `isqrt(x)`; `sigma_mult_bps(samples, sigma_ref_bps, periods_per_year, max_bps) -> int` | math.h:61, 80 | `reference.sigma_mult_bps` (after the C++ guards) |
| `sigma_samples(p_fast_series, index, vol_window, vol_step) -> list` | state.cpp:1213 | |
| `min_ratio_bps(base, sigma)` | math.h:105 | `reference.min_ratio_bps` |
| `required_zat(cents, min_ratio, p_mint)`, `required_zat_rounded(…, granularity=1000)` | math.h:115, 127 (MINT-5) | `reference.required_zat` |
| `cap_cents(issued, p_mint)`, `supply_cap_cents(issued, p_mint, cap_bps)` | math.h:139, 148 (MINT-6) | `reference.cap_cents` / `supply_cap_cents` |
| `global_ratio_bps(coll, p_mint, supply)`, `halt2_global_ratio(…, halt_bps)` | math.h:162; state.cpp:1237 | `reference.global_ratio_bps` |
| `is_underwater(coll, p_claim, minted, threshold_bps) -> bool` | math.h:174 (RED-4 a/b) | `reference.is_underwater` |
| `underwater_price(coll, minted, threshold_bps)` (largest underwater pClaim; 18,333 in the worked example) | derived from math.h:174 | |
| `claimant_max_zat(minted, margin_bps, p_claim)`, `residual_zat(coll, claimant_max)` | math.h:251, 263 (RED-5) | reference |
| `fee_zat(coll, fee_min, fee_bps)`, `attest_fee_zat(fee, attest_fee_bps)` | math.h:185, 270 (FEE-1, AFEE-1) | reference (+ int64 saturation) |
| `class_for_lock_blocks(lock, class_min, class_max)` | params.cpp:128 (MINT-2) | |
| `bond_weight(bond, age, age_cap)` | math.h:202 | `reference_attest.bond_weight` |
| `weighted_quantile([(price, weight)], q_bps)`, `bundle_stat(entries, q_low, q_high, m_select)` | math.h:228 (PRICE-2 statistic) | transcribed (see differences) |
| `select_attestors(block_hash_hex, selector, [(seq, weight)], m_select, k_slack)`, `outpoint_selector(txid, vout)` | W9 | `reference_attest` |
| `reg4_judgement(quote, peers, peer_min, deviation_bps, accuracy_band_bps) -> Judgement`, `reg4_peer_heights(t, lag)` | state.cpp:910 (REG-4) | |
| `signal_count(signals, index, window)`; `activation_step(ActivationState, h, count, start, window, threshold, delay)` | state.cpp:959, 1080 (ACT-1..3) | |
| `participation_halt_step(prev, count, active, threshold, floor)`, `enforcement_halt_step(prev, count, active, resume, floor)` | state.cpp:1242, 1246 (ACT-4, ACT-6) | |
| `param_set_start_admissible(start, prev_sunset, window, halted_at)` | params.cpp:259 (ACT-5, W19) | |
| `pin1_triggered(a_mints, min_bundles, delta_bps)`, `pin1_pinned_keys(quotes, min_tags)`, `pin2_triggered(x1, x0, delta_bps)`, `pin2_pinned_seqs(rows, min_tags)` | state.cpp:1130-1172 | |

The kernels with no reference counterpart (the model computes them inline in
`YellowbackModel._snap/_judge`) are checked by `golden.check_chain_kernels`: at every one of the 440
golden snapshots they reproduce the stored medians, pMint/pClaim, haltMask (all six bits),
activation, signal count, global ratio, PIN-1/2 sets and every REG-4 judgement. (The golden chain
never pins, so PIN is additionally unit-tested.)

### Differences between the reference model and the C++ (kernels follow the C++)

Found while porting; none affects the golden vector or the worked examples, all are degenerate
inputs the node never produces, and on the reference's domain the kernels equal it (property-tested).

1. `weighted_quantile`: zero total weight → C++ the first price (threshold 0), reference `None`;
   `q > 10^4` → C++ `None`, reference the last price.
2. `sigma_mult_bps`: C++ treats a non-positive sample and `sigmaRefBps < 0` as undefined / fixed,
   and raises `maxBps` to ≥ 10^4; the reference divides by a zero sample and only special-cases
   `sigmaRefBps == 0`.
3. `required_zat`, `cap_cents`, `global_ratio_bps`, `fee_zat`, `bond_weight`: the C++ returns
   undefined / 0 / clamps for non-positive or negative inputs (and `FitsInt64` overflows) where the
   reference computes a number.
4. The reference's own `Params.mainnet()` column has `abandon_blocks = 4,032`; spec rev 4 (W21) and
   `params.cpp` have 34,560 (= grace). It is the only drift from the registry
   (`examples.reference_param_drift()`, reported by `ybcal verify`). Kernels take every parameter
   as an argument, so this cannot leak into results.

### Vectorised kernels (`ybcal.model.vkernels`)

Undefined = `-1` (`UNDEF`); arrays broadcast; series kernels run along the last axis (element 0 =
`startHeight`), with an optional leading paths axis.

- `RollingMedian(prices, valid=None, batch_elems=1_000_000).median(window, fill)`,
  `rolling_lower_median(prices, window, fill)`, `price_medians(prices, windows, fills)` — PRICE-1
  with min-fill over a series with missing tags, via a **wavelet matrix** over the compressed quote
  sequence: every block's range-k-th-smallest query runs in O(log σ) numpy steps for all blocks at
  once, so cost is O(n log n), independent of W, and one structure serves the three windows. PIN-1
  key exclusion varies per height and is not modelled here (use the scalar path when pinning).
- `price_mint`, `price_claim`, `halt3_divergence`, `price_combine`, `min_ratio_bps`.
- `sigma_mult_series(p_fast, vol_window, vol_step, sigma_ref, ppy, max_bps)` — strided rolling sums
  of squared returns per residue class.
- `required_zat`, `claimant_max_zat`, `residual_zat`, `is_underwater`, `global_ratio_bps`,
  `halt2_global_ratio`, `cap_cents`, `supply_cap_cents`, `fee_zat`, `attest_fee_zat`.
- `signal_counts`, `hysteresis`, `participation_halt_series`, `enforcement_halt_series`.

**Overflow analysis** (int64 max 9.2e18): `cents·ratio·COIN` reaches 1.5e19 (K14),
`collateral·pClaim` 2.1e23, `minted·threshold·COIN` 1.1e19, `collateral·feeBps` 2.1e19, Σr²·ppy for
absurd σ. Each is computed exactly in int64 by splitting the dividend (`x = q·p + r`,
`x·COIN/p = q·COIN + r·COIN/p`; underwater as `c < ceil(D/p)`; `⌊a·p/(COIN·s)⌋ = ⌊⌊a·p/COIN⌋/s⌋`),
with explicit guards; any element outside the provable range (prices > 9e10 µUSD, σ returns above
the int64-safe bound) is recomputed by the scalar kernel. Correctness over speed; the property tests
draw from those ranges on purpose.

**Measured speed** (this sandbox, one core, numpy 2.4): one path × 100,000 blocks × W = 2,016 with
30 % missing tags: **0.06–0.07 s** (target ≤ 2 s); all three windows 0.16 s; 20 paths × 103,680
blocks (90 days) × three windows 3.5–6 s (≈ 0.2–0.3 s per path, so 1,000 paths × 90 days ≈ 3–5 min
on one core before WP-3's process parallelism); σ series 0.01 s per 100k blocks; money kernels
≈ 0.09 s per 10^6 elements.

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

## Devnet (WP-9)

The devnet layer is optional and never writes to ycash6 beyond `git worktree add --detach` under
`.work/`. Every environmental failure becomes `Skipped(reason, step)` (`ybcal.devnet.status`) and
prints `skipped: …`, never a fake success. The owner's guide is [devnet.md](devnet.md).

| Module (`src/ybcal/`) | Role |
|---|---|
| `params/scaling.py` | `scale_to_regtest(mainnet, factor=None, *, term_factor=None, start_height=1, bond_min="regtest", keep_sunset=False) -> ScaledSet(params, source, factor, term_factor, losses: list[RatioLoss], notes)`; `default_factor` (= pSlowWindow/64); `RULES`; `RELATIONS`; `compare_to_shipped(scaled, shipped=None) -> list[ShippedDiff]` (raises on an unexplained difference); `check_regtest(ps)` |
| `devnet/worktree.py` | `create_worktree(repo, commit, *, base=None, path=None) -> Worktree`, `remove_worktree`, `temp_worktree` (context manager), `resolve_commit` (refuses a missing commit; never fetches), `is_ancestor`, `work_dir()` (`$YBCAL_WORK` or `.work`), `ycash6_repo()` |
| `devnet/overlay.py` | `load_overlay(src, base=None) -> ParamSet`; `split(overlay, base=None) -> OverlaySplit(params, runtime, compiled, …)` with `.node_args()`/`.conf_lines()`; `make_patch(src, compiled)` / `patch_source` (RegtestParams() only); `apply_patch(worktree, patch, check_only=False)`; `overlay_hash`, `build_key` (`stock-<commit12>` / `ov-<sha16>`; runtime flags excluded) |
| `devnet/build.py` | `preflight(worktree=None, …) -> Ready \| Skipped`; `plan_build`; `build(repo, split, …) -> BuildResult \| Skipped` (cache `.work/bin/<key>/`); `fetch_ci_binary(run, artifact=None, …) -> BinaryInfo \| Skipped`; `resolve_binary(ycashd=None, *, key=None)`; `binary_version` / `parse_version_banner`; `check_skew(repo, binary_commit, pin, *, allow) -> SkewReport`; `compare_node_params(getinfo_params, expected, activation=None)`; `check_node_params(client, expected, *, allow) -> NodeCheck` |
| `devnet/rpc.py` | `RpcClient(url, user, password)` (urllib; `.call(method, *params)`, attribute calls, `wait_ready` through `-28`), `read_cookie`, `RpcError(code, message)` |
| `devnet/keys.py` | the devnet's fixed pool WIFs and their regtest P2PKH addresses (secp256k1 + HASH160 + Base58Check, pure-Python RIPEMD-160 fallback) |
| `devnet/scenarios.py` | `ReplayStep(price, blocks, pool_bias_bps, pool_weights, signal_share_bps, attestors_down, label)`, `Schedule`, `bootstrap_steps`, `make_schedule(name_or_file, params, *, seed)`, `SCENARIOS`, `SUITE`, `steps_from_path(PricePath)`, `schedule_prices(schedule) -> PricePath` |
| `devnet/runner.py` | `MinimalDevnet(DevnetConfig)` (own launcher: the launcher cannot pass runtime flags), `LauncherDevnet` (drives `yellowback-devnet up`), `replay(devnet, steps, *, seed, jitter_bps, …) -> ReplayLog`, `run_devnet(schedule, split, …) -> RunResult \| Skipped`, `p2p_port`/`rpc_port` |
| `devnet/scrape.py` | `scrape(client, out_dir=None, *, from_height=None, to_height=None) -> ScrapeResult`; `normalize_history_row` / `normalize_vault` / `normalize_attestor`; `HISTORY_FIELDS`, `HALT_BITS`; `load_history_csv` |
| `devnet/diff.py` | `compare(node_records, sim_records, fields=DEFAULT_FIELDS, allowlist=(), *, key="height") -> DiffReport`; `validate_suite(scenarios=SUITE, simulator=None, *, params=None, node_runner=None, …) -> SuiteReport`; `resolve_simulator()` |
| `devnet/cli.py` | `cli_build` / `cli_run` / `cli_validate` + `configure_*` (extra flags incl. `build --from-ci-run RUN --artifact NAME`, `--ycashd`, `--allow-version-skew`, `--strict`, `--dry-run`, `--json`) |

**Simulator contract requested from WP-3..5** (D-WP9-5): `ybcal.sim.engine.simulate_devnet(params:
ParamSet, path: PricePath, schedule: Schedule) -> list[dict]`, one record per block from
`startHeight` with the `HISTORY_FIELDS` names and integer types (`None` for undefined prices,
`haltMask` as the §3.6 bit integer, `activationCode` 0/1/2). `schedule` carries the replay semantics
(`ReplayStep`): price 0 = no quote, per-pool bias/weights, signalling share, the funding/activation
bootstrap. `ybcal devnet validate` reports `pending` until this function exists.

**CLI exit codes** (devnet only): 0 done or skipped (3 with `--strict`), 1 error / failed suite,
4 refused (version or parameter skew without `--allow-version-skew`, or a compiled overlay on a
prebuilt binary).

## Simulator core (WP-3)

`src/ybcal/sim/{engine,oracle,sigma,supply}.py`: block mode (exact against the node's integer
rules), hour-mode scaffolding, and the devnet differential entry point. Tests: `tests/sim/`.

### Engine contract (`ybcal.sim.engine`, agreed with WP-5)

```python
BlockInputs(true_price, tag_present, tag_price, tag_pool, signal_bit, start_height,
            attest=None, subsidy_zat=None, meta={})          # (paths, n); column j = start_height + j
    .perfect(true_price, start_height) / .slice_paths(sl) / .heights
BlockSeries  # (paths, n): p_fast p_mid p_slow x_mint x_claim sigma_mult_bps halt_mask(uint16)
             # activation_status(int8) signal_count participation_halt enforcement_halt issued_zat
             # supply_cap_cents supply_cents collateral_zat global_ratio_bps pin1_triggered
             # pinned_pools(uint64 bitmask of pool ids) a_mint a_claim armed; pinned_seqs, attest,
             # activation_source, attest_source, pinned_recomputed, extras, rng
    .pinned / .combined_prices() / .snapshot(path, j) / BlockSeries.concat(parts)
simulate_blocks(params, inputs, *, hooks=(), rng=None, workers=1, chunk_paths=None,
                activation_mode="auto"|"internal"|"always_active", attest_mode="auto"|"unarmed") -> BlockSeries
run_paths(params, make_inputs(rng, n, chunk_idx), n_paths, *, reducer(series, inputs), hooks=(),
          seed=0, chunk_paths=16, workers=1, ...) -> list          # streaming; identical for any workers
paths_for_budget(budget, *, days=None, workers=None, fraction=1.0) -> int
STAGES = ("judge", "activation", "attest", "pin", "price", "sigma", "supply", "halts", "vaults", "dormancy")
HALT_NOT_ACTIVE=1, HALT_NO_PRICE=2, HALT_PARTICIPATION=4, HALT_GLOBAL_RATIO=8, HALT_DIVERGENCE=16,
HALT_ENFORCEMENT=32  (view.h:565-571);  SIGNALING, LOCKED_IN, ACTIVE = 0, 1, 2
```

Undefined prices and ratios are `-1` (`vkernels.UNDEF`). Stages run once each over the whole
`(paths, n)` arrays, in SNAP order (state.cpp:1074-1281); after each stage's built-in step every hook
`hook(stage, params, inputs, series)` runs and may mutate `series` (later stages read it).

| Stage | Built-in step | Plug-in |
|---|---|---|
| `judge` | none (REG-4 is first in SNAP) | hook only |
| `activation` | `activation.simulate(params, signal_bit, start_height) -> (status, signal_count, participation_halt, enforcement_halt)` if WP-5 provides it, else the exact internal ACT-1..3/ACT-4/6 kernels; `"always_active"` = flag for price-only studies | WP-5 |
| `attest` | `attest.simulate(params, inputs, series) -> AttestSeries(a_mint, a_claim, armed, pinned_seqs, …)` if present; else unarmed | WP-5 |
| `pin` | PIN-1: `AttestSeries.pin1_triggered`, or the trigger computed from `bundle_present` / `bundle_a_mint` (on the AttestSeries or in `inputs.attest`); keys = pools with ≥ `pinMinTags` identical quotes in `[H − pinWindow, H − 1]` | WP-5 |
| `price` | PRICE-1/2 via `vkernels.RollingMedian`; heights with pinned keys are recomputed exactly without those pools' quote tags | |
| `sigma` | `vkernels.sigma_mult_series` | |
| `supply` | `issuedZat` = Σ `GetBlockSubsidy` since `startHeight`; MINT-6 cap at xMint | |
| `halts` | haltMask (all six bits; HALT-2 from `supply_cents`/`collateral_zat`) | |
| `vaults` | after the hooks, `global_ratio_bps` and HALT-2 recomputed from the arrays the hook filled | WP-4 |
| `dormancy` | none (last in SNAP) | hook only (WP-5) |

`PricePath` adapter: `engine.prices_from(obj)` / `supply._price_blocks(obj)` duck-type `.prices`
and `.resolution`, so WP-2's `ybcal.types.PricePath` works unchanged.

### Oracle (`ybcal.sim.oracle`)

`Pool(share, tags, quotes, signals, twap_blocks=12, noise_bps=30, bias_bps, refresh_blocks,
outage_rate_per_day, outage_mean_hours, outage_mode="signal"|"untagged"|"stale")`,
`Attack(pools, bias_bps, start, end, mode="bias"|"withhold"|"untagged")`,
`OracleConfig(pools, attacks)` (`.honest(n, tagging_share)`, `.from_policy(policy)`,
`.with_coalition(share, bias_bps, start=, end=)`); the stock untagged share is `1 − Σ share`; pool
id = PIN-1 key (≤ 64 pools). `generate_block_inputs(true_price, config, *, rng, start_height)`.
`price_series(params, tag_price, valid, *, tag_pool, pinned_pools) -> PriceSeries(p_fast, p_mid,
p_slow, x_mint, x_claim, halt3, no_price)`. Analytics: `attack_success_prob(share, W, fill, *,
honest_share, direction)` (exact binomial), `min_attack_share(W, fill, …)`, `min_attack_quotes`,
`attack_effect(share, bias_bps, params, …) -> AttackEffect` (CRN against the clean run),
`no_price_hours_per_year`, `tracking_lag_blocks`.

### σ (`ybcal.sim.sigma`) and supply (`ybcal.sim.supply`)

`sigma_series(params, p_fast)`, `undefined_sample_mask` (K12), `multiplier_stats(...) ->
MultiplierStats` (quantiles, time at cap split into undefined-sample vs volatility),
`time_at_cap`, `sigma_hat_bps` (unclamped σ̂), `estimator_cv`, `responsiveness_blocks`,
`k12_trap_blocks`.

`SubsidySchedule` with `MAINNET` (slow start 20,000, halving 840,000 / 1,680,000, Blossom
1,100,000; chainparams.cpp:94-96, 134), `TESTNET` (Blossom 661,610), `REGTEST` (144 / 288, Blossom
at 1 = `reference.regtest_subsidy`); `.subsidy(h)` = `GetBlockSubsidy` (consensus/params.cpp:125),
`.subsidy_array`, `.halving`, `.halving_height`; `issued_zat_series(start, n, sched)`,
`issued_between(a, b, sched)` (closed form), `supply_cap_cents`, `cap_admits` (W20 soft cap),
`global_ratio_bps`, `halt2_mask`, `days_until_cap_admits(cents, params, price_path, …)`,
`days_until_cap_admits_const`. At the current `startHeight` 3,075,000 the subsidy is 1.5625 YEC
(halving index 2) until 3,960,000, so `issuedZat` reaches 657,000 YEC after one sunset year; at
$1/YEC and `supplyCapBps` 1,500 a $10,000 cap-bound mint waits ≈ 37 days.

### Hour mode

`OracleTransferKernel(fast, mid, slow: WindowFit(window_blocks, span, lag, bias, noise_sd,
no_price_prob), substeps=12)`: each median = rolling lower median (wavelet kernel) of the
log-linearly interpolated hourly true path over the window, at a fitted lag, × exp(bias + noise).
`calibrate_kernel(params, scenarios_block_paths, *, oracle, seed)` fits it from block-mode runs;
`simulate_hours(params, hourly_true, kernel, *, rng, noise) -> HourSeries(p_fast, p_mid, p_slow,
p_mint, p_claim, sigma_mult_bps, halt_mask[NO_PRICE|DIVERGENCE])` (σ is exact on hourly pFast when
`volStep` is a multiple of 48); `kernel_error(kernel, block_series) -> {series: {p50, p95, max}}`.
Measured on held-out GBM paths (calibrated on σ = 120 %, tested on 150 %): pMint p95 ≈ 50 bps,
pClaim ≈ 42 bps against `KERNEL_TOLERANCE_P95_BPS` = 300.

### Devnet (WP-9 contract)

`simulate_devnet(params, path, schedule, *, n_pools=3, jitter_bps=10, seed=None) -> list[dict]`
rebuilds the replay's tag stream with WP-9's own `block_miners` / `jittered_quote` (same RNG
order), so its records (`series_records`, `HISTORY_FIELDS` names) are comparable field by field.
`devnet_inputs(...)` exposes the `BlockInputs`.

### Exactness coverage (`tests/sim/test_engine_exact.py`)

Per-height equality with `YellowbackModel.feed_block` (real coinbase tags through `find_tag`) of
pFast/pMid/pSlow/pMint/pClaim, σ multiplier, the full haltMask, activation status, signal count,
issuedZat, global ratio and the PIN-1 key set, on: six random streams (sparse tags, signal rates,
outages, crashes, TAG-2-invalid prices, varied startHeight / sigmaRefBps), fill boundaries,
outage → NO_PRICE + K12 cap + HALT-3, ACT-4/ACT-6 hysteresis, and PIN-1 armed through BundleLog
rows (masked medians on the scalar path). HALT-2 is checked against the scalar kernels through a
`vaults` hook; chunked and 3-worker runs equal the serial run array for array.

### Performance (this sandbox, 4 cores, numpy 2.4)

| Run | Time |
|---|---|
| 1,000 paths × 90 days, mainnet windows, oracle generation + full engine, `run_paths(workers=4)` | **89 s** (target ≤ 5 min) |
| same, one core (64 paths measured) | 0.35 s per path → ≈ 6 min per 1,000 |
| 1 path × 90 days | ≈ 0.45 s |

≈ 60 % of the time is the three rolling medians (WP-1's wavelet kernel), ≈ 20 % quote generation.
`SECONDS_PER_PATH_DAY = 0.0045` (measured 0.0039 + headroom) drives `paths_for_budget`; the
`quick` budget (64 paths × 30 days) costs ≈ 8 core-seconds per candidate. Memory: `run_paths`
holds `chunk_paths` (16) paths per worker (≈ 0.3 GB at 90 days).

## Activation & attestation simulator (WP-5)

`src/ybcal/sim/activation.py` and `src/ybcal/sim/attest.py` are the block-mode components for ACT-1..7
and the v3 attestation layer. Both are pure functions over `(paths, n_blocks)` arrays; column `j` is
height `height0 + j`. Consensus arithmetic is never re-implemented: counts and halts go through
`vkernels.signal_counts`/`hysteresis` semantics, W19 through `kernels.param_set_start_admissible`,
selection through `kernels.select_attestors` (W9), the bundle statistic through `kernels.bundle_stat`,
PIN-2 through `kernels.pin2_pinned_seqs`, bond weight through `kernels.bond_weight` semantics.

### Activation API (`ybcal.sim.activation`)

| Function | Meaning |
|---|---|
| `simulate(params, signal_bit, start_height, height0=0, *, enforce_until=None, initial=None, chunk_paths=64) -> ActivationSeries` | ACT-1..6 exactly (window clipped at the start, lock-in at the first `H ≥ start + W − 1` with `count ≥ threshold`, same-SNAP ACTIVE at delay 0, both hysteresis halts, ACT-5 from snapshot H − 1 and the sunset) |
| `ActivationSeries` | `status` int8 (`SIGNALING/LOCKED_IN/ACTIVE` = node enum), `signal_count` int32, `participation_halt`, `enforcement_halt`, `enforcement_on` (bool), `lock_in_height`/`activate_height` per path (−1 = never), `enforce_until`; `.halt_bits` (NOT_ACTIVE / PARTICIPATION / ENFORCEMENT for the engine's haltMask), `.abandoned(abandon_blocks)` |
| `ActivationInit(status, lock_in_height, activate_height, participation_halt, enforcement_halt, prior_signals)` | carried state when `height0 > start_height` (the last `W − 1` signal bits before `height0`) |
| `effective_enforce_until(params, start_height)` | the sunset re-based onto the series' start (absolute and relative height frames read the same rule) |
| `sunset_signal_mask(n, height0, enforce_until)` | miners signal only while `H ≤ enforceUntil` (index.cpp:727); the engine ANDs it into its signal draws |
| `abandoned(enforcement_halt, abandon_blocks)` | L10/L12 (index.cpp:732): ENFORCEMENT at every snapshot of `[tip − abandonBlocks + 1, tip]` |
| `start_admissible(enf, start, signal_window, *, height0, previous_enforce_until)`, `earliest_fix_start(enf, signal_window, *, height0)` | ACT-5 / W19 freeze-then-fix (kernel delegate; vectorised earliest start) |
| G5 analytics | `p_count_below`, `poisson_binomial_pmf`, `p_count_below_poisson_binomial`, `downcrossing_rate`, `false_halt_rate(...) -> FalseHaltEstimate`, `simulate_halts`, `flapping_rate`, `detection_delay` (MC), `detection_delay_approx` (fluid), `p_lock_in_first_window`, `time_to_activation`, `valve_trip_probability` (ACT-7, gambler's ruin), `natural_fork_trip_rate` |

### Attestation API (`ybcal.sim.attest`)

`simulate(params, inputs, series=None) -> AttestSeries`. `inputs` carries an `attest` dict (key or
attribute); `series` (mapping, object or `ActivationSeries`) may provide `p_mint` (the engine's
cross-section xMint, ≤ 0 = undefined; needed for PIN-2), `height0`, `start_height`.

**`attest` schema** (full text in the module docstring):

| Key | Meaning |
|---|---|
| `roster` | list of attestor dicts (shared) or `callable(path, rng) -> list`. Per attestor: `bond_zat`, `register_height` (required); `uptime`, `mean_outage_blocks` (Markov outages; None = iid), `outages` [(start, end)), `common` (member of the common outage), `bias_bps`, `noise_bps`, `phase` (signing phase mod k), `frozen_from` (stuck feed), `equivocate_at`, `withdraw_height` (clamped to `register + bondMinLock + 1`), `revive`, `revive_delay`, `revive_at`; replay: `sign_heights` / `sign_prices` |
| `true_price` | µUSD `(paths, n)` or `(n,)`; ≤ 0 = no source |
| `attest_interval` | k (default `params["attestInterval"]`); an online attestor signs heights `≡ phase (mod k)` |
| `uptime`, `mean_outage_blocks`, `noise_bps`, `common_noise_bps`, `common_outage {uptime, mean_outage_blocks}`, `revive`, `revive_delay` | defaults / shared processes |
| `demands` (explicit `{height, ref_height, kind, selector}`), `demand_counts` (`(paths, n)` bundles per block — WP-4 supplies this), `demand_heights`, else Poisson(`demand_rate`, 1/48) | blocks whose MINT / NOT-1 / claim needs a bundle; `kind` mint (empty selector, MINT-9) / notice / claim (36-byte outpoint selector); `claim_fraction`; `ref_lag` (default `DEFAULT_REF_LAG`) |
| `block_hashes` | `callable(path, h) -> hex` or `{h: hex}`; default synthetic `SHA256(path key ‖ h)` |
| `seed` / `rng` | path `i` uses `default_rng([seed, i])` (independent of the number of paths) |
| `height0`, `start_height`, `initial_trigger_height`, `initial_seated_since`, `record_status`, `paths`, `n_blocks` | frame, mid-chain start, diagnostics |

**`AttestSeries`**: `status` (UNARMED/TRIGGERED/ARMED per block), `trigger_height`/`arm_height`
(per path, −1 = never), `eligible_count`, `seated` / `pinned_seqs` (uint64 bitmasks by seq; at most
64 registered attestors per path), `pin2_triggered`, `bundle_row`, `row_a_mint`/`row_a_claim`
(BundleLog[H]; −1 = none/undefined), `pin1_triggered` (state.cpp:1130 trigger from the rows — the
engine applies PIN-1's key exclusion), `demands` (`DEMAND_DTYPE`: path, height, ref_height, kind,
armed, reason ok/unarmed/no-snapshot/insufficient/count, success, n_selected, n_fresh, selected and
signed bitmasks, aMint, aClaim), `transitions` (`TRANSITION_DTYPE`: REGISTER, MATURE, DORMANT, REVIVE,
EJECT, WITHDRAW), `bundle_log` (per path `{h: BundleLogRow}`), `attestors` (final records incl.
`seated_since`), `seq_of_roster`, optional `attestor_status` `(paths, A, n)`. Helpers:
`seated_at`, `pinned_at`, `bundle_success_rate`, `seqs_of`, `mask_of`, `pin1_trigger_series`,
`markov_online`.

**How the walk works.** Per path, a heap of status events (registration, maturity, EQV-1, bond spend,
REV-1) splits time into segments of constant statuses. For each segment the seating is computed
with numpy (weights `bond · clamp(H − ageOrigin, 0, ageCap)`, stable argsort by weight then seq; O(1)
when every ELIGIBLE seq fits in `nSlots`), and a Python loop visits only demand heights, PIN-2
trigger heights and dormancy-check heights in node order (transactions, BundleLog[H], SNAP). A
dormancy that fires ends the segment at H. Weights are int64 unless `bond · ageCap ≥ 2^62`
(object arithmetic, exact but slower).

**G8 analytics**: `liveness_probability(m, k, uptime, rho_correlation=0)` (binomial / beta-binomial),
`fresh_probability(uptime, k, attest_max_age, mean_outage_blocks=None)` (the per-attestor input to
liveness), `capture_threshold_shares`, `capture_probability` (MC with the exact kernels),
`capture_share_needed(q_low_bps, weights_distribution=None, ...)`, `false_dormancy_probability(uptime,
dormancy_blocks, min_bundles, demand_rate, selected_prob, *, mean_outage_blocks=None)` (closed form
iid / exact forward recursion for Markov outages), `false_dormancy_per_year` (union bound),
`griefing_cost_usd(bond_min, price_paths, ...)`.

### Exactness coverage (`tests/sim/`)

| Test | Against |
|---|---|
| `test_activation.py` | the golden chain's 440 snapshots (status, count, both halts, haltMask bits, ACT-5 per block, lock-in/activate); synthetic tag streams fed through `YellowbackModel` at regtest params (share drops through floors, recoveries inside the hysteresis bands, untagged blocks, `is_abandoned` at every tip, a sunset with miners dropping the bit, a later start); 4 mainnet-parameter paths × 9,000 blocks vs the scalar ACT kernels iterated; mid-chain start = tail of the full run; `abandoned` / W19 vs brute force |
| `test_attest_reference.py` | the golden chain's 440 snapshots (attest status, seated, pinnedSeqs, every BundleLog row, final attestor records incl. seatedSince and bondSpentHeight, trigger/arm, PIN-1 trigger vs the kernel); 8 randomised 360-block scenarios through a reference harness (`tests/sim/_harness.py`: the model's own SNAP/selection/weight code with synthetic REG-A1/EQV-1/bond-spend/REV-1/bundle transactions) with ranking beyond `nSlots`, founding window and ageCap, PIN-2 pins, dormancy, dynamic and explicit REV-1, EQV-1, withdrawals, a sub-minimum bond, failed bundles |
| `test_attest_units.py` | founding-window boundary and ageCap re-ranking without a status event, ties by seq, dormancy timing and revival, PIN-2 exclusion from selection, `pin1_trigger_series` vs kernel, determinism and path independence, dead-attestor detection, bias, Markov outage statistics, edge inputs |
| `test_*_analytic.py` | binomial/Poisson-binomial tails, the exact downcrossing expectation, halt-fraction brackets, detection delay, valve walk, liveness (incl. vs the simulator's bundle success rate), freshness, dormancy (iid and Markov), capture thresholds, griefing — each vs Monte Carlo |

### Runtime (this sandbox, one core)

| Workload | Time |
|---|---|
| `attest.simulate`, mainnet, 100 paths × 30 days (34,560 blocks), 9–15 attestors, u = 0.95, 48-block outages, 1 bundle demand/hour | 7–10 s (≈ 0.1 s per path) |
| same at 0.2 demands/block (≈ 230/day, 690k bundles) | ≈ 37 s (≈ 50 µs per bundle: W9 SHA-256 draws + statistic) |
| `activation.simulate`, mainnet, 100 paths × 30 days / 1,000 paths × 90 days | 0.16 s / 11 s (chunks of 64 paths) |

### Contract notes for the engine (WP-3) and WP-4

1. The engine passes `signal_bit & sunset_signal_mask(...)` (miners stop signalling after the sunset).
2. `attest.simulate` reads the engine's xMint as `series.p_mint`; PIN-2 at H needs pMint at H − 1 and
   H − 1 − pinWindow. PIN-1 at H needs the BundleLog rows before H. The two layers are mutually
   dependent only through pinned heights: run oracle → attest → recompute the medians at the
   `pin1_triggered` heights with the scalar path (PIN-1 key exclusion) → if any pMint changed, run
   attest again (a fixed point; usually one pass, since triggers are rare).
3. WP-4 supplies bundle demand as `demand_counts` (or explicit `demands` with adversarial
   `ref_height`s); a demand is counted only if the transaction would be mined (the node logs a
   verified bundle even when the MINT later fails another check).
4. Heights in the outputs use −1 for "never"; run the series at heights ≥ 0 (relative heights are
   fine: `effective_enforce_until` and `start_height` re-base the rules).

## Vaults, agents and fees (WP-4)

`src/ybcal/sim/{vaults,agents,fees,metrics}.py`: the vault book with the node's exact verdicts, the
personas that drive it, fees and revenue, and the metrics the G3/G4/G6/G7/G9 studies call.
Tests: `tests/sim/test_vault_verdicts.py`, `test_vault_book_replay.py`, `test_agents_fees.py`,
`test_vault_metrics.py` (helpers in `tests/sim/refbuild.py`).

### API

```python
# ybcal.sim.vaults — rule layer (Python ints, None = undefined; state.cpp @ 7702d22)
RuleParams.of(params)                     # the ints the vault rules read (+ .fee(c), .attest_fee(c))
Snap(height, active, halt_mask, x_mint, x_claim, p_fast, sigma_mult_bps, issued_zat, armed, eligible)
Bundle(a_mint, a_claim, ok=True, reason="", carrier_present=True);  NO_BUNDLE
MintTx(term_class, cents, lock_height, ref_height, collateral_zat, fee_zat=None, bundle=None,
       attest_fee_zat=None, structure="ok", token_output_ok=True)
SpendTx(path "owner"|"claim"|None, ref_height, yed_in=0, assigned=(), redeem_payload=True,
        single_vault=True, fee_zat=None, fee_payee_ok=True, bundle=None, attest_fee_zat=None, owner_paid_zat=0)
mint_verdict(rp, height, tx, snap, supply_cents) -> str               # MintVerdict 293-381
red_verdict(rp, height, vault, tx, snap, notice_ref_height=None) -> RedOutcome(verdict, claim_path, residual_zat, p_claim, p_emerg)
notice_verdict(rp, height, vault, ref, snap, bundle, standing_height=None) -> bool   # NOT-1
path_open(vault, height, "owner"|"claim") -> bool                      # CLTV: height > lock / claim
wallet_collateral(rp, cents, cls, sigma, p_mint, buffer_bps=0) -> int64 array   # txbuilder 1095-1103
wallet_collateral_int(...) -> int                                      # scalar twin (-1 = unsatisfiable)
VaultRecord, Totals, VaultBook(params).mint / .spend(enforcing=) / .void_release / .notice
# book runner and engine integration
Timeline(...); timeline_from_blocks(params, inputs, series, path); timeline_from_hours(params, hs, path,
         hourly_true, *, issued_zat, options, rng=None, attacker=None)
BookOptions(emergency=True, supply_cap=True, record_events=False, traj_every=1, max_ref_tries=40)
run_book(params, timeline, attempts, agents=None, *, options=None) -> PathBook(vaults, traj_steps,
         supply_cents, collateral_zat, unbacked_cents, counters, events, totals)
VaultHook(agents=AgentsConfig(), options=BookOptions(), seed=None, attempts=None)   # WP-3 "vaults" hook
HourOptions(start_offset_blocks=0, activation_blocks=None, assume_renewal=True, armed=False,
            attest_lag_hours=0, attest_noise_bps=0.0, enforcement_halt=None, book=BookOptions(traj_every=24))
simulate_vault_book_hours(params, price_paths, agents_cfg=None, kernel=None, rng=None, *, options=None,
                          workers=1, chunk_paths=8) -> VaultBookResult
VaultBookResult(vaults: dict[str, array] (+ "path"), supply_cents, collateral_zat, unbacked_cents,
                traj_steps, traj_heights, true_price, resolution, step_blocks, n_steps, params, counters, meta)
    .accepted() .column(name, mask) .days; from_books(...), concat(parts)
hourly_issued_zat(params, n_hours, start_offset_blocks=0); DEFAULT_HOUR_SUBSTEPS = 4

# ybcal.sim.agents
MinterConfig(mints_per_day, size_dist, size_lo/hi_cents, fixed_cents, class_weights, term_distribution,
             buffer_bps_lo/hi, ref_choice "adversarial"|"wallet", ref_lag, start_after_blocks)
OwnerConfig(absence_rate_per_year, absence_median_days, absence_sigma, lost_key_prob, defector_share,
            sweep_on_abandon, redeem_slippage_bps)
ClaimantConfig(enabled, min_profit_bps, slippage_bps, depth_usd, impact_bps_at_depth, use_emergency,
               thief_when_unenforced)
YedMarket(premium_bps); AttackerConfig(share, bias_bps, start_block, end_block, mode)
    .oracle_config(base OracleConfig) .apply_to_hours(p, quoting_share)
AgentsConfig(minter, owner, claimant, market, attacker, tx_fee_zat=1000).from_policy(policy, adoption=None)
MintAttempts(step, cents, term_class, lock_blocks, buffer_bps, owner_kind, owner_delay_blocks).from_rows(rows)
sample_mint_attempts(rng, params, agents, n_steps, step_blocks=1) -> MintAttempts
sample_lock_blocks / term_grid / sample_sizes / owner_kinds / owner_return_delay_blocks
p_owner_miss(grace_blocks, owner_cfg) -> float; adversarial_ref(p_mint, ok); ref_preference(...)
claim_profit_usd(...), claim_price_floor(...), redeem_price_floor(...), slippage_bps(...), zat_value_usd(...)

# ybcal.sim.fees
tx_fees(params, collateral, *, armed, eligible, owner_path) -> FeeBreakdown(pool_zat, attest_zat)
fees_zat(...) (vectorised); min_collateral_floor(params)
eligible_payees(params, tag_pool, tag_price, r, pinned=0); eligible_nonempty_series(params, tag_price,
    tag_present, tag_pool=None, pinned_pools=None)
judgement_series(params, tag_price, tag_present) -> JudgementSeries(evaluated, in_band, penalized)   # REG-4
fee_w_weights(params, tag_pool, tag_price, judgements, r, *, pinned, n_penalty, accuracy_window,
              payee_tilt_bps) -> {pool: share}                          # FEE-W (L6 params)
pool_revenue(...), attestor_revenue_month(...), fee_share_of_value(...), fee_usd(...)
redemption_affordability(params, cents, cls, p_mint, crash_price) -> Affordability; fee_table(...)

# ybcal.sim.metrics
bad_debt_prob(res, at="claim_open"|"lock"); incremental_bad_debt_from_grace(res); owner_miss(res, params, owner)
claimant_profit(res); capital_efficiency(res); liquidation_volume(res, depth_usd=None)
emergency_recovery(res); emergency_benefit(res_on, res_off); system_shortfall(res); supply_trajectory(res)
refusal_breakdown(res); fee_revenue(res, *, payee_share, n_pools, n_seated); time_until_cap_admits(params, pmint_path, cents)
p_bad_debt_fast(params, hourly_true, *, hour_series=None, kernel=None, rng=None, classes=(0,1,2),
                n_terms=16, term_distribution="uniform", sigma="series"|"median"|"p90"|int,
                cents=100_000, graces=None, start_stride=1, skip_hours=None, chunk_paths=32) -> FastBadDebt
```

### How the book works

1. **Rules.** `mint_verdict` / `red_verdict` / `notice_verdict` are the C++ in the C++'s order, on an
   abstract transaction: MINT-3 (outputs, owner key, vault script) and MINT-7 are a `structure` /
   `token_output_ok` field, fee outputs are "a valid output paying an `E(R)` member worth X". A
   wallet-built mint whose verdict fails is **VOID** (`vout[0]` is P2SH); a spend that fails RED is
   *mined* only when ACT-5 enforcement is off at that height, and then closes the vault with
   `unbacked = burned < minted` and `unbackedCents += minted − burned` (IN-3).
2. **Timeline.** One path's snapshot fields per step plus `conf[t]`, the confirmation height of a
   transaction at step `t`, whose refHeight candidates are steps `[t − ref_steps, t − 1]`. The book
   adds HALT-2 to `base_halt` from its own totals (`Snapshots[R]` holds the totals after block R's
   transactions), exactly as `ComputeSnapshot` does.
3. **Planning (vectorised).** For every mint attempt: the candidate snapshots ordered by the
   persona (adversarial: admissible ones by descending pMint, newest first on ties; wallet:
   `tip − REF_LAG`), the wallet collateral at the preferred one, and the vault's lifecycle, which
   does not depend on other vaults — first passages over sparse tables (`_Lift`, O(log n) numpy
   steps for all vaults): the owner's redeem (first step ≥ owner presence with `(collateral − FEE-1
   − tx fee) · price ≥ debt · YED price`), sweeps (defector: first step with ACT-5 off; honest owner:
   the abandonment predicate), RED-4(a) claims (first step ≥ claim open where the window's lowest
   pClaim is below the underwater level **and** the true price clears the claimant's profit floor —
   alternating the two searches), RED-4(b) (per vault, when ARMED: NOT-1 posted as soon as pEmerg
   is under the emergency level, then the first persisted, profitable R), thieves (claim path while
   ACT-5 is off). Earliest wins; ties go owner, sweep, claim, thief (a defector prefers its sweep).
4. **Sequential pass (exact).** Attempts in step order; closures due at or before a step are
   applied first (within a step: closures, then mints in arrival order). The minter **preflights**
   each candidate against the tip state (`totals` after step `t − 1`, HALT-2 at R from the totals
   history; a vectorised MINT-6 pre-screen skips candidates the cap rejects) and sends the first
   that passes; nothing passes → *refused* (no transaction; the reason is the preferred
   candidate's verdict). The sent mint is then judged against the **live** totals (same-step
   earlier mints included, MINT-6 W20) → ACTIVE or VOID. Every closure goes through `VaultBook.spend`
   with the exact verdict; a planned honest spend the rules refuse would be counted in
   `counters["plan_mismatch"]` (0 in every test and benchmark).
5. **Block mode** (`VaultHook`): the timeline is the engine's `BlockSeries` (HALT-2 stripped, E(R)
   from the tags and PIN-1 bitmask, ACT-5 and abandonment from `activation_status` /
   `enforcement_halt`, `enforceUntilHeight`); the hook writes `supply_cents` / `collateral_zat` and
   the engine's `refresh_halt2` then equals the book's HALT-2 at every height (replay-tested).
   `attempts=` overrides sampling per path index — use it only with unchunked runs.
6. **Hour mode** (`simulate_vault_book_hours`): step `t` = the snapshot at the last block of hour
   `t` (`start + offset + 48t + 47`); a transaction at step `t` reads `R = heights[t − 1]` and
   confirms at `R + DEFAULT_REF_LAG + 1`, so MINT-2's window holds; the 40-block refHeight choice is
   below the resolution and collapses to that snapshot (the kernel's noise covers it). ACTIVE from
   `startHeight + signalWindow − 1 + activationDelay` unless set; E(R) always non-empty; ACT-5 never
   lapses unless `assume_renewal=False` (then the sunset opens sweeps and thefts); `issuedZat` exact
   from `supply.issued_zat_series`. ARMED studies use a bundle proxy (`aMint = aClaim` = the true
   price `attest_lag_hours` earlier, with optional noise).

### Metric definitions

| Metric | Definition |
|---|---|
| `bad_at_claim_open` (per vault) | ACTIVE vault whose claim path opened in the horizon; `collateralZat · P < mintedCents · 10^12` with P the **true** price at the first block above `claimHeight` (exact integers via `is_underwater(c, P, cents, 10^4)`) |
| `bad_at_lock` | the same at the first block above `lockHeight` |
| `bad_debt_prob(res)` | per class and all: share of bad vaults among accepted vaults with the event inside the horizon (censored ones excluded) |
| `incremental_bad_debt_from_grace` | `P(bad at claim open) − P(bad at lock)` on the vaults where both are observed |
| `p_bad_debt_fast` | no agents/cap/HALT-2: every (start hour after the σ/slow-median warm-up, path, grid term of the class) mints `cents` at hour-mode pMint with the wallet collateral at the σ multiplier (`series`, or the path's median / p90, or fixed); bad iff collateral at the true price at hour `s + ceil((T + grace + 1)/48)` < debt; censored ends dropped; `p` = mean over the equal-probability term grid; `p_lock`, `increment`, `by_grace` likewise |
| `system_shortfall` | per path and trajectory sample: `unbackedCents` (sweeps, thefts) + `Σ max(0, debt − collateral value)` over vaults still ACTIVE; `p_any` = share of paths ever positive |
| `owner_miss` | `analytic`: P(absent through `[lock, lock + grace]`) = `1 − exp(−λ·E[(D − G)^+])` (log-normal D; lost keys added); `simulated`: share of accepted non-lost vaults whose return delay > grace; `loss`: share claimed/stolen while the owner was away |
| `claimant_profit` | over executed claims: `(received − FEE-1 − AFEE-1 − tx fee)` YEC at the true price less slippage, minus the YED burned at the market price; USD and bps of debt quantiles; share via RED-4(b) |
| `capital_efficiency` | debt / collateral value at the mint (YED per USD locked) and YED per YEC, mean/median per class |
| `liquidation_volume` | per path and day: Σ collateral received by claimants × true price; max, p99, and max ÷ ±2 % depth |
| `emergency_recovery` / `emergency_benefit` | RED-4(b) claims: count, collateral value and residual returned; benefit = final shortfall with (b) off − on (CRN) |
| `fee_revenue` | FEE-1 per path-day (YEC, USD at the true price of each payment), per pool by payee share, AFEE-1 per seated attestor per 30 days, median fee / debt per class |
| `refusal_breakdown` | attempts, accepted, VOID and refusal reasons per class |
| `time_until_cap_admits` | days after `startHeight` until MINT-6 admits `cents` per class (W20 gate: a class at ≥ `recapRatioBps` → 0) |
| `supply_trajectory` | YED supply (USD) per sample and its quantiles over paths |

### Exactness coverage

* **Verdicts** (`test_vault_verdicts.py`, 40 cases) against the vendored model's `_mint_verdict`,
  `_red_verdict` and `_apply_notice` on crafted states with real transactions: every MINT failure
  reason (class, amount both bounds, ref window three ways, lock range/ordering, NOT_ACTIVE,
  NO_PRICE, PARTICIPATION/ENFORCEMENT, GLOBAL_RATIO with the W16 recap exemption at its exact
  boundary — class C at σ 16,667 vs 16,666 — DIVERGENCE, unknown bit, MINT-5 at the boundary, the
  `4·feeMin` floor, K14 unsatisfiable, MINT-6 at the cap and one cent over, the W20 exemption for A
  and for σ-lifted C, MINT-8 missing/short fee and FEE-0, ARMED: no bundle, bad bundle, no
  statistic, AFEE-1, MINT-5 at `min(xMint, aMint)`, MINT-10 at its ±15 % boundary, MINT-6 before
  MINT-9); RED-1 malformed four ways, RED-2 short/missing burn with assignments, RED-3 fee/payee/
  FEE-0, RED-4(a) at the 18,333 µUSD worked-example boundary and +1, undefined pClaim, RED-5
  (zero under (a) — see findings — and the residual under (b) with margin 10^4), ARMED RED-1
  bundle reasons, AFEE-1, RED-4(b) at `emergencyPersist − 1 / persist / ttl / ttl + 1`, pEmerg ±1,
  (b) unarmed; NOT-1 five ways; the CLTV path rule; wallet collateral = txbuilder's formula.
* **End to end** (`test_vault_book_replay.py`): three 640-block regtest chains (cap + claims +
  HALT-2; ENFORCEMENT halt with defectors' sweeps and thefts; wallet refHeight) and a same-block
  cap race, every book event rebuilt as a real transaction and fed to `YellowbackModel.feed_block`:
  per-height supply, collateral, unbacked and the full haltMask (HALT-2 included) equal, every
  transaction verdict equal, final vault statuses equal, no BLK-1 violation, claims only above
  `claimHeight`, owner spends only above `lockHeight`.
* **ARMED book** (`test_vault_metrics.py::test_armed_book_notices_and_red4b`): NOT-1 notices and
  RED-4(b) claims go through the exact verdicts with zero plan mismatches; notice persistence is
  inside `[emergencyPersist, emergencyNoticeTtl]`. (The reference cannot verify bundles without
  attestor signatures; the ARMED rule order is covered by the crafted cases.)

### Performance (this sandbox, 4 cores)

| Run | Time |
|---|---|
| hour mode, 200 GARCH paths × 5 years, low adoption (2 mints/day ≈ 730k attempts), `workers=4`, default 4-sub-step kernel | **44 s** wall (172 CPU-s; target ≤ 2 min) |
| same with WP-3's 12-sub-step kernel | 90 s wall (346 CPU-s) |
| book alone, one 5-year path (≈ 3,650 attempts) | ≈ 0.4 s |
| block mode, mainnet, 90 days, 50 mints/day: engine 0.27 s + book 0.31 s | 0.58 s per path |
| `p_bad_debt_fast`, 64 paths × 6 years, stride 6 h, 3 classes × 16 terms × 6 graces | ≈ 13 s (+ the hour series, ≈ 0.45 s per path) |

The 4-sub-step kernel moved the 200-path P(bad debt) by < 0.1 percentage point against 12 sub-steps
(A 0.68 % vs 0.67 %, B 6.31 % vs 6.26 %, C 22.97 % vs 22.73 %).

### Findings at the shipped mainnet values (synthetic, PROVISIONAL)

1. **P(bad debt) at the claim-path opening** (fast path, 64 paths × 6 years per preset, σ series):
   GARCH-t A 0.52 % / B 6.9 % / C 27 %; regime switch 2.1 % / 18 % / 63 %; Merton 0.84 % / 16 % /
   72 %; GBM 0.71 % / 16 % / 68 % — against the policy's 0.5 / 1 / 2 %. Only class A is near its
   tolerance; B and C are an order of magnitude off (fact 1.5-1: no liquidation before
   `claimHeight`, so the base ratio must cover the whole term's drawdown). Grace adds A +0.26 pp
   (GARCH) to +1.3 pp (regime), B +1–2.7 pp. The book run (200 × 5 y GARCH, agents) agrees: A 0.68 %,
   B 6.3 %, C 23 %.
2. **The σ multiplier does not help**: on the presets its median is exactly 1× (σ̂ on the smoothed
   pFast stays below `sigmaRefBps` = 10,000, D-WP3-6) and p90 1.0–1.4×.
3. **Claimants barely exist**: pClaim (max of the mid and slow medians) lags a falling price, so by
   the time a vault is underwater at 110 % its collateral is worth about the debt at the true price;
   48 paths × 5 years: 8 profitable claims against ~1,500 vaults bad at claim opening (most wait
   for a recovery and are redeemed later; ~400 stay open and under water at the horizon).
4. **RED-5 residual is always 0 under RED-4(a)** (the claimant cap uses the same threshold that
   made the vault underwater), and **RED-4(b) never pays a claimant who buys YED at par**: under (b)
   the claimant receives the debt's worth at pClaim — the *higher* of the two prices — so it is a
   loss at the market. (b) recovers collateral only when YED trades at a discount (it acts as a
   par exit for YED holders); with YED at $0.80 the ARMED test chain runs 79 (b) claims, at par 0.
5. **The early supply cap closes B and C for good under the default demand**: from `startHeight`
   with 2 mints/day (200 paths × 5 years), MINT-6 refuses 70 % of B and 74 % of C attempts; on 48
   paths only 24 % of B / 20 % of C attempts are accepted from `startHeight` and 27 % / 23 % when the
   window starts a year later. Class A at σ = 1 is exactly `recapRatioBps` and bypasses the soft cap
   (W16/W20), its supply alone outgrows the cap, and the cap counts **all** supply.
6. **FEE-1 is on the collateral**, so the round-trip fee share of the *debt* is `2 · feeBps · ratio`
   regardless of size: A 2.5 %, B 2.0 %, C 1.5 % (+ AFEE-1 when ARMED) — class A breaches
   `max_fee_share_small` (2 %) at every size. The `4·feeMin` floor never binds for `minMint` below
   $150/YEC (D-7).
7. **Owner absence** at the policy defaults (7-day median, σ 1, one spell a year): P(miss) is
   0.43 % at 30 days of grace (3.1 % at 0, 1.0 % at 14 days, 0.14 % at 60); simulated 0.43 %.

## Report & joint pass (WP-8)

`ybcal recommend` = load data → joint pass → joint sensitivity → devnet status → report. Code:
`optimize/joint.py`, `params/emit.py`, `report/{build,explain,plots,cli}.py` + `report/templates/`,
`studies/cli.py` (`ybcal study`), `optimize/cli.py` (`ybcal sensitivity`, D-WP6-7). Reader's guide:
[report-guide.md](report-guide.md).

### API

```python
# ybcal.optimize.joint
joint_pass(base, env, *, groups=None, max_rounds=None, cache=None, workers=None, loader=load_study,
           method="space", context=None, on_event=None) -> JointResult
JointResult(base, recommended: ParamSet, recommendations: {param: Recommendation}, outcomes: {group:
            GroupOutcome}, rounds: [RoundRecord], converged, design_notes: [DesignNote], warnings, cache)
GroupOutcome(group, status "ok"|"not-run"|"error", reason, run: GroupRun, seconds, round, applied,
             design_notes, rec_metrics)
collect_design_notes(outcomes)          # metrics["design_notes"] of every rec + module design_notes(table)
TOP_METRICS; TopRiskModel(n_terms=4, start_stride_h=24)(cand, env) -> Metrics   # the four system metrics
joint_sensitivity(base, env, *, method="sobol"|"morris", params=None, metrics=None, fn=None,
                  grouping="auto"|"param"|"group", max_evals=None, n=None, workers=None, cache=None,
                  context=None, tornado=True) -> SensitivityResult(indices, tornado, per_param, …)
attach_sensitivity(recs, sens); build_move(base, changes, context) -> ParamSet | None

# ybcal.params.emit
params_cpp_source(ycash6=None, ref=PIN) -> Source; vendored_params_cpp()   # params_cpp_7702d22.json
recommended_document(rec, base=, extra=) / write_recommended(path, rec, base=, extra=)   # ybcal-extract/1
make_patch(recommended, base, *, sections=None, source=None) -> PatchResult(locked, patch_release,
           changes, release_changes, header_changes, unpatched)
check_patch(text, ycash6=None, commit=PIN) -> PatchCheck(status applies|fails|skipped|empty, detail)

# ybcal.report.build
RecommendConfig(budget, policy, policy_path, seed, data_files, out, workers, groups, sensitivity,
                sensitivity_method, sensitivity_params, ycash6, command, max_rounds, cache_dir, title, mini)
run_recommend(cfg, *, loader=None, on_event=None, sensitivity_fn=None) -> RecommendResult
write_report(ReportContext, out) -> {name: Path}; load_data(files) -> (env.data, provenance, [DataInfo])
lock_readiness(...), top_risks(...), devnet_status(...), tunable_params(), section_numbers()
```

### Flow

1. **Data.** `--data` files or directories (default `data/local/` unless `--synthetic`). Each file is
   sniffed: a spreads log → `env.data["spreads"]`, a pool-share CSV → `["pool_shares"]`, a depth
   CSV → `["depth"]`, anything else is a price series resampled to an hourly real `PricePath` →
   `["price"]`. `Env.out_dir = <out>/evidence`, so studies write their evidence straight into the
   report.
2. **Joint pass.** Groups run in `GROUP_ORDER`, each with `run_group(study, current, env,
   cache=shared)`. After each group, `current = recommended_set(current, recs)`, unless that set
   violates an invariant: then the group is not applied and the outcome says so. Rounds repeat until
   nothing moves, at most `policy.max_rounds_joint` times. A study that cannot be loaded is
   `not-run`; one that raises is `error`. Neither stops the run. At the end every Recommendation is
   restated against the shipped set (D-WP8-1).
3. **Sensitivity.** `joint_sensitivity(recommended, env)` on `TopRiskModel` (D-WP8-2): Sobol over
   per-parameter factors when `n·(k+2) ≤ 20·sobol_samples`, otherwise one factor per study group
   (D-WP8-4), plus a ±1-step tornado per parameter. The results go into
   `rec.sensitivity["joint"]`; the verdict is left alone (D-WP8-3).
4. **Outputs.** The page contract is in report-guide.md. `params.cpp.patch` is checked with
   `git apply --check` in a temporary detached worktree (`devnet.worktree.temp_worktree`, under
   `$YBCAL_WORK` or `.work/`), which is removed and pruned afterwards. `manifest.json` is the
   `RunManifest` plus counts, joint history, patch check, devnet status and timings;
   `--manifest FILE` replays it.
5. **`ybcal study G`.** A one-round joint pass over group G only, with a mini report restricted to
   G's parameters. `ybcal study all` runs every group, one round, as a full report without
   sensitivity.

### Engine: PIN fixed point (D-WP5-3 item 2, D-WP8-6)

When `attest.simulate` runs inside `simulate_blocks`, the engine iterates: attest (pass 1 has no
pMint, so no PIN-2), then PIN-1, then the medians, then attest again with `p_mint = xMint`. It
stops when the PIN-2 trigger mask that attestation reads (`engine.pin2_trigger_mask`, the same
formula as `attest._pin2_trigger_mask`) no longer changes, after at most `MAX_PIN_PASSES` (8)
passes. `series.extras["pin_passes"]` and `["pin_fixed_point"]` record the outcome. Runs without
attestation are unchanged. Hooks run once, on the first pass for `attest` and `pin` and on the
final prices for `price`.

### Measured runtime (this sandbox, 4 workers, `--budget quick --synthetic`)

With all ten studies: 2 rounds of 499 s together (round 1 ≈ 232 s, round 2 ≈ 267 s; G3 ≈ 78 s and
G1 ≈ 57 s per round dominate). Sensitivity (Sobol over grouped factors, TopRiskModel) takes 57 s and
the report a few seconds, so **≈ 9.3 min in total**, just inside the 10-minute target. Round 2
re-evaluates every group whose inputs moved, because cache keys are whole-set digests (D-WP8-9).
`--max-rounds 1` halves the joint pass.

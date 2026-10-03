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

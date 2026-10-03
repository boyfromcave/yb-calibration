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

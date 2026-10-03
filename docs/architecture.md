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

# yb-calibration — implementation plan

**Status:** plan, revision 1 (2026-10-03), **implemented** through WP-10 — see
[Status (implementation)](#status-implementation) below. The plan text that follows is kept as
written; where the build deviates, the status section names the decision.
**Audience:** the subagent team that builds this repo, and the owner who signs off the
recommended parameter set.
**Subject:** the mainnet `yellowback::Params` set in
`boyfromcave/ycash6`, branch `feature/yellowback`, file `src/yellowback/params.cpp`
(pinned for this plan at commit **`7702d22`**, "docs: trust statement and spec copy at plan v3
revision 4").

---

## Status (implementation)

*Updated by WP-10, 2026-10-03.*

### Built

| WP | Delivered | Where |
|---|---|---|
| WP-0 | registry of all 96 entries (87 `Params` fields + `params.h` constants), extraction from source or the committed snapshot, drift check, every §1.4 invariant, frozen types, policy loader, run manifest, CLI dispatch | `params/`, `config.py`, `cli.py`, `studies/base.py` |
| WP-1 | vendored reference model + golden vector with pin headers, exact scalar and vectorised kernels, `ybcal verify` (126 checks: golden replay, the 97 C++ worked examples, parity sample, vendoring hashes) | `model/` |
| WP-2 | CoinGecko / tickers / nonkyc fetchers (fixture-tested), importers for price, spreads, pool shares, depth; GBM, Merton, GARCH-t, regime switch, block bootstrap with `--calibrate`; 36-scenario library | `data/`, `scenarios/` |
| WP-3, WP-4, WP-5 | block-mode engine exact against the reference per height; hour mode with a calibrated oracle transfer kernel; vault book with exact verdicts and personas; ACT-1..7 and the full attestation layer; performance targets met (1,000 paths × 90 days ≈ 90 s on 4 cores) | `sim/` |
| WP-6 | grid / LHS / successive halving, CRN evaluation with cache and workers, CVaR / minimax regret, Pareto, OAT / Morris / Sobol (validated on Ishigami) | `optimize/` |
| WP-7a/b/c/d | all ten studies (G1–G9, release) with a recommendation, rule and explanation for every owned parameter; per-group docs | `studies/`, `docs/studies/` |
| WP-8 | joint pass, joint sensitivity, `params.cpp.patch` with `git apply --check`, `recommended.json`, HTML/Markdown report, `study` / `sensitivity` / `recommend` / `report open` | `optimize/joint.py`, `params/emit.py`, `report/` |
| WP-9 | time scaling, worktree + overlay + build (or CI artifact), minimal 3-pool launcher, replay, scrape, differential suite with honest "skipped" | `params/scaling.py`, `devnet/` |
| WP-10 | README quickstart and real-data workflow, methodology, policy reference (with a completeness test), architecture overview, decision index, this section | `README.md`, `docs/` |

Milestones: **M1–M5 done** (M5 = `recommend --budget quick --synthetic` produces a full report
with explanations, PROVISIONAL tags and the patch, in about 9–10 minutes on 4 cores). The
definition of done for the request (M5 + documentation) is met.

### Deviations from the plan

| Plan | As built | Decision |
|---|---|---|
| §3.3 rolling median by sliding sorted window | wavelet-matrix range quantiles, O(n log n) independent of W | D-WP1-4 |
| §5.2 σ̂ on the true price | σ̂ measured on simulated pFast (what the node does) | D-WP3-6, D-WP7a-5 |
| §5.3 class boundaries searched | verified only; heterogeneity raised as a design note (three classes are fixed in the rules) | D-WP7b-3 |
| §5.3 P(bad debt) from the full book everywhere | G3/G4 use WP-4's fast vectorised P(bad debt) on a shared hour ensemble; the book run is the cross-check | D-WP7b-1 |
| §5.5, §5.8 one search per group | G5, G6, G7, G8 decide each parameter as a *family* of one-at-a-time rules (optimize / verify / rule) | D-WP7c-1, D-WP7d-1 |
| §5.9 G9 optimised | admissible-range "verify" rules (KEEP inside the range) | D-WP7b-5 |
| §5.10 insensitive ⇒ KEEP | *insensitive* is a label; it never overrides a study's verdict | D-WP8-3 |
| §5.10 per-parameter Sobol | per study group at quick budget, with per-parameter tornado; per-parameter via `ybcal sensitivity --params` | D-WP8-4 |
| §5.10 item 3 cross-group robust selection | robust selection inside each study only | D-WP8-7 |
| §5.10 four system metrics from the studies | fast top-risk model for ranking only | D-WP8-2 |
| §6.2 drive `yellowback-devnet` | own minimal launcher (the stock launcher cannot pass runtime flags); `--launcher` keeps the stock path | D-WP9-4 |
| §6.2 build only | also `--from-ci-run` (download a CI artifact), with a version-skew refusal | D-WP9-6 |
| §8 CI runs `recommend --budget quick --synthetic` | CI (`.github/workflows/ci.yml`) runs ruff, pytest, `params doc --check` and `params check`; the quick report run is `make quick`, not yet a CI step | — (open) |
| §3.2 CLI | extra flags (`--groups`, `--workers`, `--max-rounds`, `--cache`, `--no-sensitivity`, `--set`, `--params`, devnet `--dry-run`, …) | D-WP8-8, WP-2/WP-9 `configure_*` hooks |
| §1.3 `abandonBlocks` | locked (K10 wording), reference model's stale 4,032 ignored | D-2, D-WP1-5 |

### What remains

- **M6 — devnet live run.** The devnet layer is complete but has never run against a node: this
  sandbox cannot build `ycashd` (depends hosts blocked), and the only prebuilt binary (CI run
  37081639884) is from `94bafa4`, eight commits before the pin (W20 soft cap missing, mainnet
  `abandonBlocks` 4,032). On a networked machine: `ybcal devnet build --ycash6 PATH` then
  `ybcal devnet validate` (see `docs/devnet.md`).
- **M7 — real data.** Fetch ≥ 1 year of hourly YEC/USD and ≥ 2 weeks of `spreads.py` logs on a
  networked machine (`data/README.md`), set `yec_daily_volume_p10_usd`, confirm the policy
  placeholders (`docs/policy.md`), then `ybcal recommend --budget standard` (or `deep`). Until then
  every price-driven verdict is PROVISIONAL and the lock-readiness checklist fails by design.
- **Owner decisions** (§12): the risk tolerances, materiality, and what to do with the design
  notes (early supply cap, no liquidation before `claimHeight`, fee on collateral, RED-5/RED-4(b)).
- **Small open items:** `diverge_spread_multiplier` is not wired into the ported spreads analysis
  (default 3.0 only); `hour_kernel_tolerance_bps` is not read by a study; cache keys are whole-set
  digests, so later joint rounds re-evaluate unchanged groups (D-WP8-9); the quick report run is
  not in CI.

---

## 0. What we are building, in one paragraph

`ybcal` is a Python command-line tool plus a library. It reads the Yellowback parameter set
straight from the ycash6 source and classifies every field. It runs a fast simulator of the
overlay that matches the node's integer arithmetic exactly. It studies each parameter group
against price histories and stress scenarios, scoring risk and continuity metrics against a
risk policy the owner sets. Where the simulator alone isn't trustworthy, it checks the results
on a regtest devnet of real `ycashd` nodes. It then writes a report giving, for **every**
parameter, a recommended value, the current value, the decision rule that produced it, the
evidence, its sensitivity and a plain-English explanation, plus a ready-to-review
`params.cpp` diff. The report stays in this repo and is never applied to the node repo by the
tool.

**Success criterion (from the request):** a runnable, well-documented calibration tool whose
final output gives the owner the best value for every parameter, with an explanation for each.

---

## 1. Ground truth this plan is built on

### 1.1 Where the parameters live

| Item | Location (ycash6 @ `7702d22`) |
|---|---|
| Field list and comments | `src/yellowback/params.h:110-209` |
| Values shared by mainnet and testnet (`SetCommon`) | `src/yellowback/params.cpp:14-101` |
| Mainnet `startHeight` 3,075,000 / `enforceUntilHeight` 3,495,480 | `params.cpp:136-154` |
| Regtest column (`RegtestParams`) | `params.cpp:189-247` |
| State-hash `ParamsRecord` (six hashed fields) | `src/yellowback/view.h:671-695` |
| Regtest runtime flags (`ParamsFromArgs`) | `src/yellowback/index.cpp:1229-1259` |
| Param-change admissibility (ACT-5, W19 "freeze, then fix") | `params.cpp:258-268` |
| Normative spec (plan v3 rev 4 copy) | `doc/yellowback-spec.md`, `doc/yellowback-release.md` |
| Independent pure-Python implementation of the rules | `qa/rpc-tests/test_framework/yellowback_model.py` (2,972 lines, standard library only, covers v2 and v3) |
| Existing measurement scripts (prior work) | `contrib/yellowback/attest/calibrate/{spreads.py,pinrate.py}` |
| Devnet launcher | `contrib/yellowback/devnet/yellowback-devnet`, `doc/yellowback-devnet.md` |

The workspace copy of the v3 plan (`yellowback/docs/plans/yellowback-v3-development-plan.md`)
is still at **revision 3**. Revision 4 (W18–W21) exists only as the spec copy in ycash6. `ybcal`
therefore cites `ycash6/doc/yellowback-spec.md` as the normative text.

### 1.2 "Locked" versus "excluded"

The plans don't use the words *locked* and *excluded*. They use these terms instead (the tool
uses the owner's words and records the mapping):

- **Locked** = *consensus-shaped among enforcing miners* (v2 K10, spec:64-76; v3 delta
  spec:746-748). "Every value in the table that a rule in §3.7–3.9 reads is consensus-shaped …
  The exceptions are the rows marked informational and wallet default (L6)." A locked set
  changes only through a new parameter set keyed by start height. That set may start at or after
  the previous sunset (L8). From rev 4 it may also start after a full signal window with
  ENFORCEMENT halted ("freeze, then fix", W19). A set that differs only in the sunset is a
  *renewal* (W18).
- **Excluded** = rows marked **informational**, **wallet default (L6)**, **wallet / agent
  policy**, or **node-local**. Never hashed, never read by a consensus rule, so free to change
  in a patch release.
- **Per-release** = `startHeight`, `enforceUntilHeight`. Derived from the release date and tip
  (M14, L8), not optimised.
- **Protocol constant** = `refWindow`, `tokenValue`, the `params.h` constants. Verified, not
  tuned.
- **Derived** = values fixed by a formula from another parameter, e.g. min-fills, `qHighBps`,
  `recapRatioBps`. The tool tunes the parent and computes the child.

### 1.3 The full parameter inventory

`ybcal params` must carry this table as data (`src/ybcal/params/registry.py`) and regenerate
`docs/parameters.md` from it. Group codes refer to the studies in §5.

| Field | Mainnet | Class | Group | Rules |
|---|---|---|---|---|
| `startHeight` | 3,075,000 | per-release (hashed) | R | every rule; M14 lead ≥ 16,128 blocks |
| `enforceUntilHeight` | 3,495,480 | per-release (hashed) | R | ACT-5 sunset = start + 420,480 (L8) |
| `addressVersion` | `1F E4` | meta | — | D10 |
| `pFastWindow` / `pFastMinFill` | 96 / 48 | locked / derived ⌈W/2⌉ | G1 | PRICE-1/2, HALT-3, SIGMA-1, MINT-10 |
| `pMidWindow` / `pMidMinFill` | 576 / 384 | locked / derived ⌈2W/3⌉ | G1 | PRICE-1/2, HALT-3 |
| `pSlowWindow` / `pSlowMinFill` | 2,016 / 1,344 | locked / derived ⌈2W/3⌉ | G1 | PRICE-1/2, HALT-3 |
| `signalWindow` | 2,016 | locked | G5 | ACT-1/2, W19 freeze window |
| `activationThreshold` | 1,512 (75 %) | locked | G5 | ACT-2, ACT-4 clear |
| `participationFloor` | 1,210 (60 %) | locked | G5 | ACT-4 → MINT-4 |
| `activationDelay` | 2,016 | locked | G5 | ACT-2/3 |
| `enforcementFloor` | 1,008 (50 %) | locked | G5 | ACT-6 → ACT-5, BLK-1 |
| `enforcementResume` | 1,210 (60 %) | locked | G5 | ACT-6 |
| `valveBlocks` | 6 | **excluded** (node-local) | G5 | ACT-7, BLK-2 |
| `abandonBlocks` | 34,560 (= grace) | locked (by K10 wording; gates TPL-1/2, MP-1, `yed_sweep`) | G4 | L10/L12, W21 invariant ≥ grace |
| `nReg` | 576 | **excluded** (informational) | G6 | REG-1 |
| `peerLag` / `peerMin` | 10 / 5 | locked | G6 | REG-4 (hashed `Judgements`) |
| `deviationBps` / `accuracyBandBps` | 1,000 / 300 | locked | G6 | REG-4 |
| `payeeWindow` | 100 | locked | G6 | FEE-2 → MINT-8, RED-3 |
| `feeMin` / `feeBps` | 0.5 YEC / 25 | locked | G6 | FEE-1, MINT-5 (4·feeMin floor), AFEE-1 |
| `grace` | 34,560 (30 d) | locked | G4 | MINT-2/3 (claimHeight = lock + grace) |
| `claimThresholdBps` | 11,000 | locked | G3 | RED-4(a), RED-5 |
| `supplyCapBps` | 1,500 | locked (hashed) | G7 | MINT-6 (soft, W20) |
| `globalRatioHaltBps` | 25,000 | locked | G7 | HALT-2 |
| `recapRatioBps` | 50,000 | locked (= 2 × halt, W16) | G7 | MINT-4, MINT-6 |
| `divergenceBps` | 2,000 | locked | G7 | HALT-3 |
| `classMin/Max[A,B,C]` | 34,560–103,680 / 103,681–420,480 / 420,481–2,102,400 | locked | G3 | MINT-2 (contiguous) |
| `baseRatioBps[A,B,C]` | 50,000 / 40,000 / 30,000 | locked | G3 | MINT-5, MINT-4, MINT-6 |
| `volWindow` / `volStep` | 2,016 / 48 | locked | G2 | SIGMA-1 |
| `volPeriodsPerYear` | 8,760 | locked (= BLOCKS_PER_YEAR / volStep, K13) | G2 | SIGMA-1 |
| `sigmaRefBps` | 10,000 | locked (hashed) | G2 | SIGMA-1; M14 acceptance band [1×, 1.5×] |
| `sigmaMultMaxBps` | 30,000 | locked | G2 | SIGMA-1 clamp, K12 |
| `minMint` / `maxMint` | $100 / $10,000 | locked | G9 | MINT-2 |
| `minOutput` / `maxOutput` | $1 / $100,000 | locked | G9 | XFER-1, RED-1 |
| `tokenValue` | 10,000 zat | constant | — | RPC only |
| `refWindow` | 40 | constant | — | MINT-2, RED-1, NOT-1 |
| `nPenalty` / `accuracyWindow` / `payeeTiltBps` | 288 / 576 / 10,000 | **excluded** (L6 wallet default) | G6 | REG-2/3, FEE-W |
| `attestArmMin` / `attestArmDelay` | 5 / 1,152 | locked (`attestArmMin` hashed) | G8 | ARM-1/2 |
| `attestRequired` | true | locked | G8 | every ARMED rule (W15) |
| `bundleCarrier` | SCRIPTSIG | locked (hashed) | — (design choice, verified) | BUNDLE-1 |
| `nSlots` / `mSelect` / `kSlack` / `bundleMax` | 9 / 4 / 2 / 6 | locked | G8 | seating, selection, BUNDLE-1 |
| `qLowBps` / `qHighBps` | 3,333 / 6,667 | locked / derived (10⁴ − qLow) | G8 | bundle statistic |
| `attestMaxAge` | 20 | locked (= 2·k) | G8 | BUNDLE-1, REV-1 |
| `pinWindow` / `pinDeltaBps` / `pinMinTags` / `pinMinBundles` | 288 / 500 / 3 / 2 | locked (**to calibrate**, A7) | G8 | PIN-1/2 |
| `divergeBpsAttest` | 1,500 | locked (**to calibrate**, A7) | G8 | MINT-10 |
| `emergencyRatioBps` / `emergencyPersist` / `emergencyNoticeTtl` | 10,500 / 48 / 1,152 | locked | G3/G8 | NOT-1, RED-4(b) |
| `residualMinZat` | 100,000 | locked | G9 | RED-5 |
| `attestFeeBps` | 2,500 | locked ("a testnet parameter") | G6 | AFEE-1 |
| `bondMin` / `bondMinLock` / `bondMaturity` | 20,000 YEC / 420,480 / 16,128 | locked | G8 | REG-A1, maturity |
| `ageCap` / `foundingWindow` | 207,360 / 8,064 | locked ("placeholders") | G8 | bond weight |
| `dormancyBlocks` / `dormancyMinBundles` / `dormancyCheck` | 16,128 / 20 / 48 | locked | G8 | dormancy |
| `carrierValue` | 10,000 zat | **excluded** (wallet policy) | G9 | none |
| `attestInterval` (k) | 10 | **excluded** (agent policy; defines attestMaxAge = 2k) | G8 | none |
| `walletConfirmations` | 6 | **excluded** (wallet policy) | G9 | none |
| `DEFAULT_REF_LAG` (params.h) | 2 | **excluded** (wallet, `-yellowbackmintlag`) | G9 | MINT-2 choice of R |

### 1.4 Invariants the tool must enforce on every candidate set

The tool rejects a candidate set before simulating it if any of these fail. Each check names the
rule it comes from.

- `minFill(fast) = ⌈W/2⌉`, `minFill(mid,slow) = ⌈2W/3⌉` (L9); `pFast < pMid < pSlow`.
- `enforcementFloor < participationFloor ≤ enforcementResume < activationThreshold ≤ signalWindow` (spec:481).
- `abandonBlocks ≥ grace` (W21). New, proposed by this plan: `abandonBlocks ≥ freeze-then-fix
  runbook length` (§5.4).
- `enforceUntilHeight = startHeight + BLOCKS_PER_YEAR`, and never past the next scheduled
  network upgrade (L8). `startHeight ≥ release tip + 16,128` (M14).
- `classMin[0] ≥ 1`, `classMin[i+1] = classMax[i] + 1`; `classMax[2] + grace + tip < 500,000,000`.
- `volWindow % volStep == 0`; `volPeriodsPerYear = BLOCKS_PER_YEAR / volStep` (K13).
- `baseRatioBps[i] > globalRatioHaltBps`; `recapRatioBps = 2 × globalRatioHaltBps` (W16);
  `claimThresholdBps > emergencyRatioBps > 10,000`.
- `mSelect + kSlack ≤ bundleMax ≤ 6` (520-byte push: 4 + 6·74 bytes); `nSlots ≥ mSelect + kSlack`.
- `qLowBps + qHighBps = 10,000`; `qLowBps >` the largest single-entity weight share the policy
  assumes.
- `attestMaxAge = 2·attestInterval`; `emergencyPersist < emergencyNoticeTtl`.
- `bondMinLock = BLOCKS_PER_YEAR`; `ageCap < 2³²`.
- `minOutput ≤ minMint ≤ maxMint ≤ maxOutput`; `4·feeMin ≤` the collateral of a `minMint` vault
  at the worst price the policy covers.

### 1.5 Facts surfaced during research that the studies must model

These come from reading the rule code. Each one changes what "optimal" means.

1. **There is no liquidation before `claimHeight`.** The vault script
   (`script.cpp:79-90`) is `IF <lock> CLTV … CHECKSIG ELSE <lock+grace> CLTV … TRUE`. Anyone
   can take the claim path, but only from `lockHeight + grace`. An emergency notice (NOT-1 /
   RED-4(b)) is still a claim-path spend. So a vault's collateral has to stay above its debt for
   the **whole term plus grace**, not just through a short liquidation lag. The class base
   ratios are a 30-day-to-5-year drawdown budget, and the G3 study is built around that.
2. **`issuedZat` is the subsidy since `startHeight`, not all YEC ever issued** (state.cpp:1224,
   SERIALISATION.md §2). The MINT-6 supply cap starts near zero and grows linearly. Because the
   cap is soft (W20), only mints at ≥ `recapRatioBps` (class A at σ = 1) bypass it early on.
   G7 must measure how long classes B and C stay effectively closed after start.
3. **The σ multiplier is pinned at the 3× cap until `start + volWindow`**, and again whenever
   one pFast sample is undefined (K12). Any price-feed gap of a few hours sets minting
   collateral to 150 / 120 / 90 % above base for up to `volWindow` blocks.
4. **After the sunset, minting halts within about one signal window**, because miners drop the
   signal bit (index.cpp:713) and ACT-4 trips. The renewal deadline (W18, about 6 months before
   the sunset) is a continuity parameter in its own right.
5. **A minter can choose `refHeight` anywhere in the 40-block `refWindow`**, picking the most
   favourable snapshot. The same goes for a claimant. Simulated adversaries must do the same.
6. **HALT-3 (divergence) fires only on a fall**: a fast median below a slower one. It never fires
   on a rally.
7. **`ParamsHash` covers only the six hashed fields**, and only the first parameter set is ever
   written (state.cpp:1302). A change to any other locked value is caught only by the K10
   start-height discipline. This is one more reason to get the first set right.

---

## 2. Design principles

1. **Exact where it matters, fast where it can be.** The arithmetic kernels (medians, σ,
   required collateral, supply cap, global ratio, underwater test, residual, fees, weighted
   quantile, selection) are the integer formulas of `src/yellowback/math.h`. They are tested
   byte-for-byte against the vendored `yellowback_model.py` and the C++ worked examples. Monte
   Carlo runs use numpy vectorised equivalents, and every vectorised kernel has an
   exact-equivalence property test against the scalar reference.
2. **Every recommendation is a decision rule, not an opinion.** Each parameter has a rule
   written in code and echoed in the report. Example: "the smallest `sigmaRefBps` (multiple of
   500) such that the median multiplier over the realised-volatility ensemble lies in
   [1.0×, 1.5×] and the p95 is ≤ 2.0×."
3. **Minimal change by default.** The workspace's prime directive applies to parameters too. A
   recommendation to move a value off its current setting needs a material improvement, set by
   a policy knob (`materiality`), on the parameter's primary metric with no policy violation
   elsewhere. Otherwise the verdict is **KEEP**, with the evidence.
4. **Risk appetite is the owner's input, not the tool's.** All tolerances live in
   `policy/default.toml`: maximum bad-debt probability, maximum expected false-halt hours per
   year, minimum oracle-attack cost, attestor uptime assumption, and so on. The owner copies and
   edits it. The report prints the policy it used.
5. **Data provenance is explicit.** Every result is tagged `real-data`, `synthetic`, or
   `judgement`. A parameter whose recommendation rests only on synthetic data is flagged
   **PROVISIONAL** in the report. The final mainnet recommendation needs real YEC data (§4).
6. **Read-only towards every other repo.** `ybcal` reads ycash6 (and the other repos) at a pinned
   commit. Devnet builds happen in a throwaway `git worktree` under `yb-calibration/.work/`.
   Nothing is committed or pushed anywhere but this repo.
7. **Reproducible.** Every run writes a manifest: ybcal version, ycash6 commit, data file
   hashes, policy hash, seed and budget. `ybcal recommend --manifest X` reproduces a report.

---

## 3. Architecture

```
yb-calibration/
├── pyproject.toml            # python ≥ 3.11; deps: numpy, scipy, matplotlib, jinja2 (+ dev: pytest, hypothesis, ruff)
├── README.md                 # quickstart + link to docs/
├── Makefile                  # make setup | test | quick | recommend | devnet-* | docs
├── policy/default.toml       # risk appetite (owner-editable), documented inline
├── scenarios/*.toml          # stress-scenario library (§4.3)
├── data/                     # README + synthetic seeds; user data in data/local/ (gitignored)
├── src/ybcal/
│   ├── cli.py                # `ybcal` entry point (argparse; subcommands in §3.2)
│   ├── config.py             # policy + run-manifest loading
│   ├── params/
│   │   ├── registry.py       # ParamSpec table (§1.3) — the single source of truth
│   │   ├── extract.py        # parse params.cpp/params.h at a git ref → ParamSet; drift check
│   │   ├── invariants.py     # §1.4 as executable checks
│   │   ├── scaling.py        # mainnet → regtest time scaling preserving ratios (§6.3)
│   │   └── emit.py           # recommended set → params.cpp SetCommon() patch + JSON
│   ├── model/
│   │   ├── reference.py      # vendored yellowback_model.py (pinned header, unmodified body)
│   │   ├── kernels.py        # exact scalar kernels (thin wrappers over reference)
│   │   └── vkernels.py       # numpy vectorised kernels (property-tested == kernels)
│   ├── data/
│   │   ├── loaders.py        # CSV/JSON import, resample to 75 s blocks / hourly, gap report
│   │   ├── fetch.py          # CoinGecko market_chart / nonkyc / spreads.csv importer (needs network)
│   │   ├── synthetic.py      # GBM, Merton jump-diffusion, GARCH(1,1), regime switch, block bootstrap
│   │   └── scenarios.py      # scenario TOML → price paths + behaviour schedules
│   ├── sim/
│   │   ├── engine.py         # block clock, two resolutions (§3.3), path batching, seeds
│   │   ├── oracle.py         # pool quote generation, tag cadence, PRICE-1 medians, HALT-3
│   │   ├── sigma.py          # SIGMA-1 series and multiplier
│   │   ├── activation.py     # ACT-1..7 state machine over a hashrate-share process
│   │   ├── vaults.py         # vault book: mint, mature, redeem, claim, void, unbacked
│   │   ├── agents.py         # minter, owner, absentee, claimant, liquidator, pool, attacker personas
│   │   ├── attest.py         # attestor set, uptime, bias, seating, selection, bundles, PIN-1/2, dormancy
│   │   ├── fees.py           # FEE-1/2, AFEE-1, pool revenue
│   │   └── supply.py         # issuedZat, MINT-6 cap, HALT-2 global ratio
│   ├── studies/              # one module per parameter group (§5), each a Study
│   │   ├── base.py           # Study protocol: space(), evaluate(), decide(), explain()
│   │   ├── g1_price_windows.py   g2_volatility.py   g3_collateral.py
│   │   ├── g4_grace_abandon.py   g5_activation.py   g6_miners_fees.py
│   │   ├── g7_supply_halts.py    g8_attestation.py  g9_amounts.py
│   │   └── release.py        # startHeight / enforceUntil / renewal schedule (no tuning)
│   ├── optimize/
│   │   ├── search.py         # grid, Latin hypercube, successive halving
│   │   ├── robust.py         # scenario-ensemble aggregation, minimax regret, CVaR
│   │   ├── pareto.py         # multi-objective front + policy-constrained selection
│   │   ├── sensitivity.py    # one-at-a-time, Morris screening, Sobol (Saltelli) on the joint set
│   │   └── joint.py          # coordinate-descent over groups with coupling (§5.10)
│   ├── devnet/
│   │   ├── worktree.py       # throwaway ycash6 worktree at the pinned commit
│   │   ├── overlay.py        # regtest param overlay → RegtestParams() patch (in the worktree only)
│   │   ├── build.py          # incremental ycashd build per overlay hash, cached binaries
│   │   ├── runner.py         # drive contrib/yellowback/devnet/yellowback-devnet; replay a price path
│   │   ├── scrape.py         # yed_gethistory / yed_getstats / yed_listvaults → parquet-free CSV
│   │   └── diff.py           # simulator vs node differential check (§6.4)
│   └── report/
│       ├── build.py          # assemble results → report.md, report.html, recommended.json, params.patch
│       ├── explain.py        # per-parameter explanation generator (templated from decision rules)
│       ├── plots.py          # matplotlib figures (dataviz palette, light/dark safe)
│       └── templates/        # jinja2
├── tests/                    # unit, property, golden, CLI smoke, devnet (marked slow)
└── docs/
    ├── PLAN.md               # this file
    ├── architecture.md  methodology.md  parameters.md (generated)  data.md
    ├── policy.md  scenarios.md  devnet.md  report-guide.md  decisions.md
    └── studies/g1.md … g9.md # per-group method, metrics and decision rules
```

### 3.1 Core data types (frozen in WP-0, everyone codes against them)

```python
@dataclass(frozen=True)
class ParamSpec:            # one row of §1.3
    name: str               # C++ field, e.g. "pMidWindow" or "baseRatioBps[1]"
    mainnet: int | bool | str
    regtest: int | bool | str
    klass: Literal["locked", "excluded", "per-release", "constant", "derived", "meta"]
    group: str              # "G1".."G9", "R", "-"
    unit: str               # "blocks", "bps", "zat", "cents", "count", "enum"
    hashed: bool            # in ParamsRecord
    rules: tuple[str, ...]  # ("PRICE-1", "HALT-3")
    derive: Callable | None # for derived params
    bounds: tuple[int, int] # search bounds (hard)
    step: int               # search granularity
    doc: str                # one-line meaning

class ParamSet(Mapping[str, int|bool|str]):   # immutable; .replace(**kw); .check() -> list[Violation]

@dataclass
class PricePath:            # one or many paths, block-indexed or hourly
    t0: datetime; resolution: Literal["block", "hour"]; prices: np.ndarray  # shape (paths, n) µUSD int64
    provenance: Literal["real", "synthetic", "scenario"]; meta: dict

class Study(Protocol):
    group: str
    params: tuple[str, ...]                     # the fields it owns
    def space(self, base: ParamSet, budget: Budget) -> Iterable[ParamSet]: ...
    def evaluate(self, cand: ParamSet, env: Env) -> Metrics: ...      # Env = data + scenarios + policy + rng
    def decide(self, results: ResultTable, policy: Policy) -> list[Recommendation]: ...
    def explain(self, rec: Recommendation, results: ResultTable) -> str: ...

@dataclass
class Recommendation:
    param: str; current: Any; recommended: Any; verdict: Literal["KEEP", "CHANGE", "PROVISIONAL", "BLOCKED"]
    rule: str               # the decision rule, human-readable
    binding: str            # which constraint/metric decided it
    metrics: dict           # primary + secondary at current and recommended
    sensitivity: dict       # d(metric)/d(param), Morris mu*/sigma, flat/steep
    confidence: Literal["high", "medium", "low"]; provenance: str; evidence: list[Path]  # figures/tables
```

### 3.2 CLI surface

```
ybcal params show [--group G3] [--class locked]       # the §1.3 table, live from source
ybcal params extract --ycash6 ../ycash6 --ref feature/yellowback [--out params.json]
ybcal params check  --ycash6 ../ycash6                # drift vs registry + invariants; non-zero on drift
ybcal data fetch    --source coingecko --days 365 --out data/local/yec-hourly.csv      # needs network
ybcal data import   FILE [--kind price|spreads|hashrate|depth]
ybcal data synth    --model garch --calibrate data/local/yec-hourly.csv --paths 1000 --years 5
ybcal data describe FILE                              # realised vol, drawdowns, gaps, tail index
ybcal study G1 [--budget quick|standard|deep] [--data …] [--policy …] [--seed N]
ybcal study all
ybcal sensitivity [--method morris|sobol]
ybcal recommend     [--budget …] [--out reports/2026-10-03/]   # runs all studies, joint pass, report
ybcal verify                                          # kernel parity vs reference model + C++ worked examples
ybcal devnet build  [--overlay recommended.json]      # worktree + patch + build (cached by overlay hash)
ybcal devnet run    --scenario crash-70 [--overlay …] # up, replay, scrape, down
ybcal devnet validate                                 # the §6.4 differential suite
ybcal report open   reports/<run>/                    # print path / serve HTML
```

`--budget quick` must finish `recommend` in **≤ 10 minutes** on a 4-core laptop (default for
CI and smoke tests). `standard` should take about an hour; `deep` runs overnight.

### 3.3 Two-resolution simulator

The longest window is `pSlowWindow` = 2,016 blocks, and the longest vault term is 5 years +
grace ≈ 2.14 M blocks. Running 1,000 Monte-Carlo paths at block resolution over 5 years isn't
practical in Python, so the engine has two modes:

- **Block mode** (75 s steps, horizon ≤ 120 days): exact PRICE-1 medians with min-fill, tag
  cadence (which pools tag which blocks), quote noise, pinning, attestor bundles, ACT counts,
  HALT bits, σ. Rolling lower-median uses a sliding sorted window per path (`bisect`-based,
  O(n log W)), vectorised across paths where possible. Used by G1, G2, G5, G6, G7, G8.
- **Hour mode** (48-block steps, horizon ≤ 6 years): vault book economics with an *oracle
  transfer kernel*, a per-scenario lag/smoothing response measured in block mode
  (pMint = f(true price history), pClaim = g(…)). Used by G3, G4, G7-supply, G9.

Block mode calibrates the hour-mode kernel, and a test asserts the kernel's error stays below
policy tolerance on held-out scenarios.

---

## 4. Data

### 4.1 Real data (needed for a non-provisional recommendation)

The cloud sandbox can't reach the price APIs: CoinGecko, Kraken and CryptoCompare are all
blocked by the egress proxy. PyPI is reachable. So:

- `ybcal data fetch` is written and unit-tested against recorded fixtures. The owner runs it on
  their own machine. It reuses the same sources as the node's agents, ported from
  `contrib/yellowback/yellowback_price.py` (not imported, so this repo stays standalone):
  - CoinGecko `market_chart`, hourly for 90 days and daily for all history;
  - nonkyc `YEC_USDT`;
  - CoinGecko tickers per exchange (SafeTrade).
- `ybcal data import` accepts:
  - a price CSV `ts,price_usd`;
  - the `spreads.csv` that `contrib/yellowback/attest/calibrate/spreads.py log` writes;
  - an optional pool-share CSV (`height,payout_key`), e.g. scraped from a Ycash explorer or
    `yed_listminers`, for G5/G6;
  - an optional order-book depth CSV, for G7/G9 liquidity.
- `data/README.md` explains exactly which files to gather, for how long, and where they go
  (`data/local/`, gitignored).

### 4.2 Synthetic data (always available, so the tool runs end to end out of the box)

- **Models:** GBM; Merton jump-diffusion; GARCH(1,1) with Student-t innovations; a two-state
  regime switch (calm/turbulent); a stationary block bootstrap of real returns when real data
  is present.
- **Defaults** are "YEC-like" (annualised volatility around 120 %, fat tails, multi-month
  drawdowns above 80 %), tagged **synthetic**, and documented as placeholders.
- **`--calibrate FILE`** fits any model to real data (MLE for GARCH; moment matching for the
  others).
- **Exchange-spread model** (G6/G8): a correlated per-source noise model with staleness and
  outage processes, fitted to `spreads.csv` when present.

### 4.3 Stress-scenario library (`scenarios/*.toml`)

Every study evaluates candidates on the scenario ensemble, not just one path. Each scenario
sets a price program and, optionally, behaviour schedules (hashrate share, attestor uptime,
attacker actions). Initial set:

| Scenario | What it stresses |
|---|---|
| `calm-90d` | false halts, false pins, false penalties, fee drag |
| `crash-70-1d`, `crash-90-30d` | collateral sufficiency, HALT-3/HALT-2 timeliness, claimability |
| `slow-bleed-95-2y` | long-term class B/C solvency (no liquidation before claimHeight) |
| `pump-dump-3x` | mint-at-the-top risk (pMint = min of medians), divergence asymmetry |
| `flash-wick-50-1h` | median robustness, MINT-10, PIN-1 |
| `feed-outage-6h` | σ cap trap (K12), minting freeze duration |
| `stale-pools` | REG-4 false penalties, PIN-1 on constant quotes |
| `oracle-attack-{p}` | a colluding pool coalition with share p biases quotes ±X% |
| `attestor-outage-{n}` | K_SLACK liveness, dormancy ejection |
| `attestor-capture-{w}` | weight share w biases attestations (quantile robustness) |
| `hashrate-drop-{to}` | ACT-4/ACT-6 hysteresis, flapping, abandonment trigger |
| `dev-absence-{days}` | abandonment window vs freeze-then-fix runbook |
| `owner-absence` | absentee owners vs grace length |
| `sunset-no-renewal` | post-sunset minting halt and continuity |

---

## 5. Studies — one per parameter group

Each study module ships with `docs/studies/gN.md`, which covers its method, metrics and decision
rules, and the reasoning behind each. All studies share the policy file. Defaults below are
**starting tolerances for the owner to confirm**, not decisions.

### 5.1 G1 — Price medians (`pFast/Mid/SlowWindow`, min-fills)

*Question:* how long should the fast, mid and slow medians be?

- **Metrics:**
  - **Manipulation resistance.** The smallest colluding hash share that can move each median
    by X % for Y blocks. Computed analytically from the fill rule (V16: at fill f, a pool needs
    > f/2 of the window's blocks) and checked by simulation (`oracle-attack-*`).
  - **Tracking lag.** The delay from a true move until pMint/pClaim reaches 50 % / 90 % of it.
  - **Mint-at-the-top exposure.** E[pMint − true price | pump].
  - **Under-collateralisation exposure during crashes.** Blocks for which pClaim > true price.
  - **Availability.** The fraction of time NO_PRICE is set, given tag cadence and outages.
  - **HALT-3 coupling.** False and true divergence halts.
- **Search:** fast ∈ {48…288}, mid ∈ {288…1,152}, slow ∈ {1,152…4,032}, with fast < mid < slow,
  in steps of 48. Min-fills are derived.
- **Decision rule:** among windows meeting `attack_share_min` (default 34 %) and `max_no_price_hours`,
  minimise crash-lag CVaR₉₅ plus a lambda times pump overpricing. Keep the current values
  unless the improvement exceeds `materiality`.

### 5.2 G2 — Volatility (`volWindow`, `volStep`, `volPeriodsPerYear`, `sigmaRefBps`, `sigmaMultMaxBps`)

*Question:* is the σ multiplier responsive without being jumpy, and does the reference sit at
YEC's realised volatility?

- **Metrics:**
  - Multiplier distribution over calm and turbulent regimes (the M14 acceptance band is
    [1×, 1.5×] median at realised volatility).
  - Responsiveness: blocks to reach 90 % of the new steady-state multiplier after a regime
    change.
  - Cap-trap exposure: hours per year spent at the cap because of undefined samples (K12).
  - Estimator noise: the coefficient of variation of σ̂ in a stationary regime as a function of
    `volWindow / volStep`.
- **Decision rule:**
  - `sigmaRefBps` = the realised annualised volatility p50 (fitted on real data), rounded to
    500 bps.
  - `sigmaMultMaxBps` = the smallest cap that covers the p99 turbulent multiplier.
  - `volWindow`/`volStep` minimise the CV of σ̂ subject to a responsiveness ≤ `max_sigma_lag`.
  - `volPeriodsPerYear` is derived.

### 5.3 G3 — Collateral and classes (`baseRatioBps[3]`, `classMin/Max[3]`, `claimThresholdBps`, `emergencyRatioBps`)

*Question:* what ratio keeps each class solvent across its whole term plus grace, given fact
1.5-1?

- **Core metric: P(bad debt).** For a vault minted at pMint with ratio r·σ-mult, lock T and grace
  G, the probability that collateral value is below debt at `lock + G`, when the claim path
  opens. Also, for vaults the owner abandons, below 100 % at the moment a rational claimant
  would act.
  - Computed in hour mode over (a) the block bootstrap of real history at every start date and
    (b) the synthetic ensemble.
  - Weighted by a term distribution within each class (uniform by default; configurable).
- **Secondary metrics:** capital efficiency (YED per YEC locked); claimant incentive (expected
  profit of a claim at threshold, net of fees, so claimants actually show up); emergency-path
  benefit (how much bad debt RED-4(b) recovers when armed).
- **Decision rules:**
  - `baseRatioBps[c]` = the smallest ratio, in steps of 2,500, such that P(bad debt) ≤
    `max_bad_debt_prob[c]` (default 0.5 % A, 1 % B, 2 % C) under the ensemble, with σ-mult at
    its median.
  - Class boundaries are checked against the T-dependence of the drawdown distribution. The
    study recommends splitting or merging only if a class's within-range bad-debt varies by
    more than `class_heterogeneity_max`.
  - `claimThresholdBps` = the smallest margin where E[claim profit] > fee + `claimant_min_profit`
    at p90 slippage.
  - `emergencyRatioBps` must sit between 10,000 and `claimThresholdBps`, picked to maximise
    recovered collateral in `crash-*`.

### 5.4 G4 — Grace and abandonment (`grace`, `abandonBlocks`)

*Question:* how long do owners get after `lockHeight`, and how long does the module wait for
its developers?

**Grace.** Two forces pull in opposite directions:

- **Owner continuity:** P(an honest owner misses the window), from the `owner-absence` model
  (log-normal absence durations, configurable). This falls as grace grows.
- **Extra drawdown exposure:** the incremental P(bad debt) from the grace period adding to the
  term (fact 1.5-1). This rises with grace, and is worst for class A, where 30 days of grace is
  33–100 % of the term.

Decision rule: minimise `w_owner · P(miss) + w_debt · ΔP(bad debt)` subject to both being under
policy, in steps of 1,152 blocks (1 day).

**Abandonment.** Lower bound = max(grace, **freeze-then-fix runbook length**). The runbook
length is `detect` (up to `signalWindow`) + `signalWindow` (the W19 window) + the M14 release
lead of 16,128 blocks + an operator buffer. That is 20,160 blocks plus the buffer at current
values, which is under 34,560, so the constraint holds today. The tool states the margin.

Other metrics:
- false-abandonment probability, from the G5 hashrate model (ENFORCEMENT set for
  `abandonBlocks` straight without a real abandonment);
- the cost of abandoning too late: vaults that stay unsweepable while devs are truly gone, from
  `dev-absence-*`.

Decision rule: the smallest `abandonBlocks` ≥ the lower bound with P(false abandon) ≤
`max_false_abandon_prob`, rounded up to a whole number of days.

**Release cadence.** Also reported (from the release study): the renewal deadline (W18) and the
runbook's slack against the sunset.

### 5.5 G5 — Activation and enforcement (`signalWindow`, thresholds, floors, `activationDelay`, `valveBlocks`)

*Question:* do the hysteresis bands give stable enforcement with few false halts at realistic
hashrate shares?

- **Method:** the signal count in a window is a sum of block draws. With pool shares from data
  (or scenarios), the exact binomial / Poisson-binomial tail gives P(count < floor | true share
  p). This is exact math, not simulation. Simulation adds share drift and pool churn.
- **Metrics:**
  - P(false participation halt per year) and P(false enforcement halt per year) at share p.
  - Flapping rate (halt/clear cycles per year).
  - Detection delay for a real drop to p′.
  - Time to activation.
  - Valve false trips at given orphan rates (excluded param: reported, verdict tagged
    patch-release).
- **Decision rule:** thresholds are expressed as fractions of `signalWindow`. Pick the fractions
  so that P(false halt/yr) ≤ `max_false_halt_per_year` at `expected_enforcing_share` (default
  0.80) and detection ≤ `max_detection_blocks` at a drop to 0.45. Keep the strict ordering of
  §1.4. `signalWindow` trades estimator variance against detection delay. `activationDelay` ≥
  the operator upgrade window from policy.

### 5.6 G6 — Miner judgement and fees (`peerLag`, `peerMin`, `deviationBps`, `accuracyBandBps`, `payeeWindow`, `feeMin`, `feeBps`, `attestFeeBps`, plus excluded L6 `nPenalty`, `accuracyWindow`, `payeeTiltBps`, `nReg`)

- **Judgement metrics:**
  - False-penalty rate for honest pools, given the fitted spread/staleness model (a 15-minute
    TWAP versus peers).
  - Detection rate for a liar at ±Y %.
  - P(not evaluated), from fewer than `peerMin` peers at a realistic pool count.
- **Decision rule:** `deviationBps` ≥ `k_dev` × the p99 honest deviation (default k = 1.5), as
  small as that allows. `accuracyBandBps` ≈ the honest p75. `peerMin` is the largest value where
  P(not evaluated) ≤ 5 % at the expected pool count.
- **Fee metrics:**
  - Fee as a share of vault value across sizes (the `feeMin` floor dominates small vaults).
  - Pool revenue per day at modelled adoption, versus a configurable operating cost.
  - Attestor revenue per seated attestor per month versus `bondMin` opportunity cost (G8
    coupling).
  - Redemption affordability under the `4·feeMin` floor at crash prices.
- **Decision rule:** the lowest fees at which pool and attestor incentives clear the policy's
  minimum monthly revenue under `adoption_low`, with the fee share of a `minMint` vault ≤
  `max_fee_share_small`.
- Excluded L6 values get recommendations tagged **patch-release**.

### 5.7 G7 — Supply cap and halts (`supplyCapBps`, `globalRatioHaltBps`, `recapRatioBps`, `divergenceBps`)

- **Supply cap metrics:**
  - The cap trajectory from `startHeight`, using `issuedZat` = subsidy since start (fact 1.5-2,
    computed from Ycash chainparams). The tool hard-codes the Ycash subsidy schedule and cites
    `chainparams.cpp`.
  - Days until the cap admits a typical class-B/C mint.
  - YED supply versus YEC market depth (liquidation absorbability).
- **Halt metrics:**
  - HALT-2 trigger times in `crash-*` versus when the system is truly under 100 % / 150 %.
  - HALT-3 false positives in `calm` and `pump-dump`; true positives in `crash`.
- **Decision rule:**
  - `supplyCapBps` = the largest cap where the liquidation volume of the worst crash scenario
    stays under `max_depth_fraction` of p10 daily volume.
  - `globalRatioHaltBps` ≥ the global ratio below which P(system bad debt) passes policy, and
    below the smallest class floor (§1.4).
  - `recapRatioBps` is derived (2×, W16).
  - `divergenceBps` gives the best F1 of halting during crashes versus calm, at a recall floor.
- **Flag for the owner:** if the early-supply-cap finding (fact 1.5-2) means B/C are closed for
  more than `max_class_closed_days`, the report raises it as a **design note**. The fix may be
  a rule change (e.g. a different `issuedZat` origin), which is out of scope for parameter
  tuning, so the tool reports it and leaves it to the owner.

### 5.8 G8 — Price attestation (all v3 attest fields)

This builds on and supersedes the existing `spreads.py` / `pinrate.py` methods (ported, with
results cross-checked against them on the same CSV).

- **`divergeBpsAttest`:** 3 × the worst-pair p95 spread, rounded up to 100 bps (proposal §16).
  The tool also reports the MINT-10 false-refusal rate in calm and crash.
- **`pinWindow`, `pinDeltaBps`:** the PIN-1 arming rate on real hourly data (proposal rule: if
  fewer than about 5 % of windows have a ≥ 5 % move, drop `pinDeltaBps` to 200–300). Plus the
  false-pin rate of honest pools with jittered quotes, and the detection delay of a frozen feed.
  `pinMinTags`/`pinMinBundles` come from the false-pin versus detection trade-off.
- **Liveness (`nSlots`, `mSelect`, `kSlack`):** P(a bundle of ≥ mSelect valid signatures exists
  among mSelect + kSlack selected) with per-attestor uptime u, using the binomial with a
  correlated-outage extension. Decision: the smallest kSlack with P(minting refuses for want of
  a bundle) ≤ `max_attest_unavailability` at u = `attestor_uptime` (default 0.95), within
  `bundleMax` ≤ 6.
- **Capture resistance (`qLowBps`, `qHighBps`, `nSlots`, `ageCap`, `foundingWindow`):** the
  smallest weight share an adversary needs to move aMint/aClaim by X % (simulated with the exact
  selection + weighted-quantile kernels), and the time-to-capture for a new large bond under
  `ageCap`. The rule enforces `qLow` > the largest assumed entity share.
- **`attestMaxAge` / `attestInterval`:** keep `= 2k`. Choose k to balance freshness against
  relay load and the bundle-not-found rate.
- **Dormancy (`dormancyBlocks`, `dormancyMinBundles`, `dormancyCheck`):** the false-ejection
  probability for an honest attestor at uptime u, versus the detection time for a dead one.
- **Arming (`attestArmMin`, `attestArmDelay`):** the probability founders can arm with a set too
  small for the capture bound, versus time to arm under realistic recruitment.
- **Bonds (`bondMin`, `bondMinLock`, `bondMaturity`):** griefing cost in USD under YEC price
  scenarios (the bond is in YEC by design). Seq-exhaustion check (`doc/yellowback.md:80-85`).
- **Emergency (`emergencyPersist`, `emergencyNoticeTtl`):** recovered collateral versus
  premature-claim risk in `crash-*`.

### 5.9 G9 — Amounts and wallet policy (`minMint`, `maxMint`, `minOutput`, `maxOutput`, `residualMinZat`, plus excluded `carrierValue`, `walletConfirmations`, `DEFAULT_REF_LAG`)

These are mostly constraint-driven:

- `minMint`: fee share at `feeMin` stays ≤ policy, and the vault collateral clears `4·feeMin`
  at the policy's worst price.
- `maxMint`: a single vault's crash liquidation stays ≤ `max_depth_fraction` of depth.
- `minOutput`/`maxOutput`: dust and UX checks.
- `residualMinZat`: above the dust and fee cost of an extra output.
- `walletConfirmations`: from a reorg-depth model at a given hash share.
- `DEFAULT_REF_LAG`: from the reorg model, versus mint VOID risk.

Excluded values are tagged **patch-release**.

### 5.10 Joint pass and sensitivity

The groups are coupled: G1 windows feed G3's oracle kernel; G2 scales G3 ratios; G6 fees enter
G3 claimant incentives and G9 floors; G5 drives G4's false abandonment. After the per-group
studies:

1. **Coordinate descent** over groups in dependency order G1 → G2 → G5 → G3 → G4 → G7 → G6 →
   G8 → G9, until no recommendation moves (at most 3 rounds).
2. **Sobol sensitivity** over the joint recommended set (±1 step per parameter). It reports, for
   each top-level risk metric (system bad-debt probability, minting availability, false-halt
   hours, oracle-attack cost), which parameters dominate. A parameter with negligible total-
   order index is labelled *insensitive*: KEEP the current value, and say so.
3. **Robust selection:** final values minimise worst-case regret across the scenario ensemble,
   subject to every policy constraint. Ties break toward the current value.

### 5.11 Release study (no tuning)

Computes and checks:
- `startHeight` (tip + ≥ 16,128 at a planned release date);
- `enforceUntilHeight` = start + 420,480;
- the renewal deadline (sunset − ~6 months);
- that the sunset precedes any scheduled network upgrade (read from ycash6 `chainparams.cpp`);
- the W19 runbook slack.

---

## 6. Devnet validation

### 6.1 Why and what

The simulator is the workhorse. The devnet answers two questions it can't answer by itself:

1. **Fidelity.** Does the simulator agree with real nodes on the same inputs?
2. **End-to-end sanity.** Does the recommended set behave under the real wallet, miner and
   attestor code paths? That covers VOID races, carrier-then-mint sequencing, the template
   filter, the valve and REF_LAG.

The devnet is **not** used to sweep mainnet-scale windows. That would need weeks of block time.

### 6.2 Mechanics (no commits to ycash6)

1. `ybcal devnet build` creates `git worktree add .work/ycash6-<hash> <pinned commit>` from the
   local ycash6 clone. It never touches the clone's branches. For overlays that change compiled
   values, it writes a patch to `RegtestParams()` *in the worktree only*. The patch is generated
   from the overlay JSON and recorded in the run manifest.
2. It builds with ycash6's own instructions (`./zcutil/build.sh` once, then
   `make -C src -j$(nproc) ycashd ycash-cli`, about 2 min incremental). Binaries are cached by
   overlay hash under `.work/bin/<hash>/`. A cold build of `depends` takes 35–60 min and needs
   network access for the toolchain downloads. If the environment can't build, the devnet layer
   is reported as **skipped**, never faked, and the core recommendation still runs.
3. The six runtime flags (`-yellowbackstartheight`, `-yellowbacksigmaref`,
   `-yellowbacksupplycapbps`, `-yellowbackenforceuntil`, `-yellowbackattestarmmin`,
   `-yellowbackbundlecarrier`) are varied **without** a rebuild.
4. `ybcal devnet run` drives `contrib/yellowback/devnet/yellowback-devnet up --dir .work/devnet/<run>`
   with the scenario's price path. Prices are injected through the launcher's mock-price files
   and `yed_setquote`, with jitter to avoid PIN-1 on constant quotes. Personas come from
   `yellowback-sim`. It then scrapes `yed_gethistory`, `yed_getstats`, `yed_listvaults`,
   `yed_getactivation` and `yed_listattestors` to CSV, and runs `down --wipe`. If a chain-viz
   binary is present it may record the session (`--record`) for replay; this is optional.

### 6.3 Time scaling

`params/scaling.py` maps a mainnet set to a regtest set by dividing block counts by a scale
factor (ycash6's own regtest column uses roughly 31.5× on the windows) while keeping the ratios
the rules depend on:
- fill fractions;
- threshold fractions of `signalWindow`;
- `volWindow / volStep` sample count, with `volPeriodsPerYear` held at 8,760 as K13 requires;
- the ordering invariants.

bps values and amounts are not scaled. The scaler reports every ratio it couldn't preserve
because of integer rounding (e.g. `pinMinTags` = 2 vs 3).

### 6.4 Differential suite (`ybcal devnet validate`)

The suite runs `calm`, `crash-70`, `hashrate-drop`, `attestor-outage-1` and `oracle-attack-34`
at regtest scale. Each run feeds the **same** price path and behaviour schedule to the devnet
and to the block-mode simulator, then compares per-block:
- pFast, pMid, pSlow, pMint, pClaim;
- the σ multiplier;
- haltMask;
- activation status;
- supply and collateral totals;
- vault statuses.

Pass criterion: exact equality on integer state (the simulator uses the node's arithmetic), with
an allowlist only for behaviour randomness the simulator doesn't model (mempool timing). Any
mismatch fails the suite and blocks the report's "validated" badge.

A cheaper second check runs in CI without nodes: replay `yellowback_golden.json` through
`model/reference.py` and the vectorised kernels.

---

## 7. The report (`ybcal recommend`)

Output directory `reports/<date>-<shorthash>/`:

- **`report.md` and `report.html`** (the HTML is self-contained, with figures inlined):
  1. **Executive summary**: the recommended set as one table (param, current, recommended,
     verdict, class, confidence, provenance), counts of KEEP / CHANGE / PROVISIONAL, and the top
     3 risks remaining.
  2. **Policy used** (verbatim) and data provenance (file hashes, spans, gaps).
  3. **One section per parameter**, generated by `explain.py` from the decision rule and
     results. Each covers:
     - what it controls, in plain English, and the rules that read it;
     - the decision rule;
     - the binding constraint;
     - metrics at the current and recommended values;
     - a sensitivity sentence ("flat between 20 and 40 days; P(miss) dominates below 20");
     - a figure;
     - why not the neighbouring values;
     - a locked or patch-release note.
  4. **Joint sensitivity** (Sobol table and tornado chart).
  5. **Design notes**: findings that tuning can't fix (e.g. fact 1.5-2), raised for the owner.
  6. **Devnet validation** status, or "skipped" with the reason.
  7. **Lock-readiness checklist**: every locked parameter has a non-provisional recommendation
     backed by real data; invariants pass; the release study passes.
- **`recommended.json`**: machine-readable, the same shape as `ybcal params extract`.
- **`params.cpp.patch`**: a unified diff against `src/yellowback/params.cpp` at the pinned
  commit, `SetCommon()` / `MainParams()` only, with each changed line commented with its report
  section. It is a proposal for a human to apply in ycash6, never applied by the tool.
- **`manifest.json`**: everything needed to reproduce the run.

---

## 8. Testing strategy

| Layer | Tests |
|---|---|
| Registry | every `Params` field in `params.h` is in the registry (parsed list == registry keys); values equal those extracted from `params.cpp`; invariants pass on the current mainnet and regtest columns |
| Kernels | scalar kernels == `reference.py` on hypothesis-generated inputs; reproduce the C++ worked examples (σ 14441 / 13127, MINT-5 6e11 zat, RED-4 threshold 18333, RED-5, AFEE-1 quarter, weighted-quantile thresholds — `src/test/yellowback_math_tests.cpp`) |
| Vectorised kernels | `vkernels.f(batch) == [kernels.f(x) for x in batch]`, property-tested |
| Golden | `yellowback_golden.json` replays through the reference model (vendored copy) |
| Simulator | deterministic per seed; block mode vs hour-mode kernel error ≤ tolerance; analytic cross-checks (binomial halt probabilities vs simulated frequencies; GBM drawdown vs closed form) |
| Studies | each study's `decide()` on a toy result table; each returns a Recommendation for every owned param; `quick` budget completes |
| CLI | smoke test of every subcommand in `--budget quick` with synthetic data |
| Devnet | `pytest -m devnet` (skipped unless `YBCAL_YCASHD` or a built worktree exists) |
| Docs | `docs/parameters.md` regenerates byte-identical from the registry (CI fails on drift) |

CI (GitHub Actions in this repo): ruff, pytest (non-devnet), and `ybcal recommend --budget quick
--synthetic` producing a report artefact.

---

## 9. Work packages for the subagent swarm

WP-0 lands first, because it freezes the interfaces in §3.1. After that, packages run in
parallel in separate git worktrees (`isolation: worktree`) on short-lived branches. Each one
merges into `claude/yb-calibration-tool-plan-brrrul` once its tests pass. Each WP owns its
files; anything shared goes through WP-0's types.

| WP | Owner scope (files) | Depends on | Acceptance |
|---|---|---|---|
| **WP-0 Scaffold & contracts** | `pyproject.toml`, `Makefile`, `src/ybcal/{cli,config}.py`, `params/{registry,extract,invariants}.py`, `studies/base.py`, `optimize/` stubs, CI, `policy/default.toml` skeleton | — | `ybcal params show/extract/check` work against `../ycash6`; registry covers every field; invariants pass; types frozen |
| **WP-1 Reference model & kernels** | `model/`, `ybcal verify` | WP-0 | vendored model with pin header; scalar + vectorised kernels; C++ worked examples and golden pass |
| **WP-2 Data** | `data/`, `scenarios/`, `data/README.md`, `docs/data.md`, `docs/scenarios.md` | WP-0 | fetchers tested on fixtures; importers for price / spreads / pool-share / depth; 5 synthetic models with `--calibrate`; full scenario library |
| **WP-3 Simulator: oracle, σ, halts, supply** | `sim/{engine,oracle,sigma,supply}.py` | WP-0, WP-1 | block mode exact vs reference on fixtures; performance target: 1,000 paths × 90 days in block mode ≤ 5 min on 4 cores (or documented shortfall + fallback) |
| **WP-4 Simulator: vaults, agents, fees** | `sim/{vaults,agents,fees}.py`, hour mode + oracle transfer kernel | WP-1, WP-3 (kernel calibration) | vault lifecycle exact vs reference; agents incl. adversarial refHeight choice; kernel error test |
| **WP-5 Simulator: activation & attestation** | `sim/{activation,attest}.py` | WP-1, WP-3 | ACT-1..7 with hysteresis vs reference; seating/selection/bundles/PIN/dormancy vs reference & `yellowback_attest.select_attestors` |
| **WP-6 Optimizer & sensitivity** | `optimize/` | WP-0 | grid/LHS/successive halving; robust (CVaR, minimax regret); Pareto; Morris + Sobol validated on analytic test functions (Ishigami) |
| **WP-7a Studies G1, G2, G5** | `studies/g1,g2,g5*.py`, `docs/studies/g1,g2,g5.md` | WP-3, WP-5, WP-6 | each recommends every owned param with rule + explanation in `quick` |
| **WP-7b Studies G3, G4, G9, release** | `studies/g3,g4,g9,release.py`, docs | WP-4, WP-6 | same |
| **WP-7c Studies G6, G7, G8** | `studies/g6,g7,g8.py`, docs; port + cross-check `spreads.py`/`pinrate.py` | WP-4, WP-5, WP-6 | same; on the same CSV, G8 reproduces `spreads.py analyze` / `pinrate.py` numbers |
| **WP-8 Report & joint pass** | `report/`, `optimize/joint.py`, `ybcal recommend`, `params/emit.py` | WP-7* | full report from `recommend --budget quick --synthetic`; patch applies cleanly to ycash6 @ pin (checked in a temp worktree, never committed) |
| **WP-9 Devnet** | `devnet/`, `params/scaling.py`, `docs/devnet.md` | WP-0, WP-3..5 (for diff) | worktree + overlay + build + run + scrape; differential suite; graceful "skipped" path. Runs only where a build is possible |
| **WP-10 Docs & integration** | `README.md`, `docs/{architecture,methodology,policy,report-guide,decisions}.md`, generated `docs/parameters.md`, final end-to-end run | all | a new user can go from clone to report following README alone; every CLI command documented with an example |

**Suggested waves:** wave 1 WP-0 → wave 2 WP-1, WP-2, WP-6 → wave 3 WP-3, then WP-4 and WP-5
→ wave 4 WP-7a/b/c, WP-9 → wave 5 WP-8, WP-10. That is about 6 agents in flight at most.

**Rules for every agent:**
- Read this plan and `docs/parameters.md` first.
- Never edit any repo but `yb-calibration`; read ycash6 at the pinned commit only.
- Integers for every consensus quantity (µUSD, zat, cents, bps); no floats in kernels.
- Every new decision gets a line in `docs/decisions.md`.
- Follow the workspace naming rule (Yellowback is the system, YED is the unit; no `DigiDollar`
  or `ydollar` names).

---

## 10. Milestones and definition of done

| M | Deliverable | Done when |
|---|---|---|
| M1 | Contracts | `ybcal params check` passes against ycash6 @ `7702d22`; registry complete |
| M2 | Faithful kernels | `ybcal verify` green (golden + C++ worked examples + property tests) |
| M3 | Simulator | block and hour modes pass reference parity and performance targets |
| M4 | Studies | every parameter in §1.3 receives a Recommendation in `quick` |
| M5 | Report v1 (synthetic) | `make recommend` → full report with explanations, PROVISIONAL tags, patch |
| M6 | Devnet validation | differential suite passes on at least `calm` and `crash-70` (or documented skip) |
| M7 | Report v2 (real data) | owner supplies fetched data; report has no PROVISIONAL on locked params; lock-readiness checklist green |

**Definition of done for the request** = M5 + M10 docs in this cloud session (runnable,
documented, recommendation and explanation for every parameter). M6 and M7 depend on a buildable
environment and on real price data that only the owner's machine can fetch. The tool must make
both one command each.

---

## 11. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Simulator drifts from the node as ycash6 evolves | pinned commit; `ybcal params check` + golden replay fail loudly on drift; re-pin is a deliberate act recorded in `docs/decisions.md` |
| Synthetic data leads to false confidence | provenance tags; PROVISIONAL verdicts; lock-readiness requires real data |
| Over-fitting to one price history | block bootstrap over all start dates + synthetic ensemble + stress scenarios; robust (regret) selection |
| Python too slow at block resolution | two-resolution design; vectorisation; budgets; optional `numba` only if needed, behind a flag |
| Devnet can't build in the sandbox | devnet optional; clean "skipped"; instructions for the owner's machine |
| Tool recommends a rule change disguised as a parameter | design notes are a separate report section; the tool never emits rule changes |
| "Optimal" depends on risk appetite | the policy file is the owner's; the report shows how recommendations move under a conservative and a lenient policy preset |

---

## 12. Open decisions for the owner (defaults used until answered)

1. **Risk tolerances** in `policy/default.toml`: bad-debt probability per class (0.5 / 1 / 2 %),
   false-halt budget (24 h/yr), minimum oracle-attack share (34 %), attestor uptime (95 %),
   expected enforcing share (80 %).
2. **Materiality threshold** for moving a value off its current setting (default: 20 % relative
   improvement on the primary metric).
3. **Real data** to gather: ≥ 1 year of hourly YEC/USD (CoinGecko), ≥ 2 weeks of `spreads.py`
   logging, and optionally a pool-share series from a Ycash explorer.
4. Whether design notes (e.g. the early supply cap, fact 1.5-2) should also be written up as
   issues in the yellowback workspace repo. Default: the report only.

# Reading the ybcal report

`ybcal recommend` writes one directory per run, by default `reports/<date>-<hash>/`, where the hash
covers the ybcal version, the ycash6 pin, the budget, the seed, the policy and the data files. The
report is a **proposal for the owner**. ybcal never applies anything to ycash6.

```
report.html                  self-contained page (figures inlined; works offline; light and dark)
report.md                    the same content; figures link into evidence/
recommended.json             the recommended set, in the same shape as `ybcal params extract`
params.cpp.patch             proposed edit to src/yellowback/params.cpp at the pin (locked + per-release lines)
params-patch-release.patch   excluded (patch-release) field changes, only when there are any
manifest.json                everything needed to reproduce the run (RunManifest + run details)
evidence/                    per-group CSVs and figures, sensitivity tables, joint.json, recommendations.json
```

`ybcal report open DIR` prints the path of `report.html`. `ybcal report open DIR --serve` serves
the directory on `http://127.0.0.1:8000/`.

## The sections

1. **Executive summary.** Every *tunable* registry parameter appears once in the table: current
   (shipped) value, recommended value, verdict, class and change path, confidence and provenance.
   Values that move off the shipped set are in bold. Above the table are the verdict counts and the
   three most important remaining risks, worst first: BLOCKED parameters, a solvency gap in the fast
   model, studies that did not run, synthetic-only evidence, design notes, missing devnet
   validation, and a joint pass that did not converge.
2. **Policy and data provenance.** The policy file is printed verbatim, with the hash of the parsed
   policy. Each data file is listed with its sha256, row count, time span and gap summary. The
   scenario library files are listed too. With no data files, the run is **synthetic**.
3. **Parameters.** One subsection per tunable parameter, grouped by study (G1 → G9, then the release
   check R). Each subsection gives:
   - **What it controls**: the registry's one-line meaning and the consensus rules that read the
     parameter.
   - **Decision rule**: the study's rule, as it is coded.
   - **Binding constraint**: the constraint or metric that decided the value.
   - **Sensitivity**: the study's own sentence, such as "flat between 20 and 40 days", plus the joint
     sensitivity sentence (§4).
   - **Why not the neighbours**: the ±1-step values around the recommendation, each either worse,
     better but within materiality, in violation of the policy, or inadmissible under an invariant.
   - **Change path**: *locked* (a new parameter set keyed by start height: K10, L8, W19) or
     *patch-release* (excluded rows: informational, wallet or agent policy, node-local).
   - The metrics table, current against recommended, then the study's explanation, figures and notes.
     A figure shared by several parameters is printed once and referred to elsewhere.
4. **Joint sensitivity.** Sobol (or Morris) indices at ±1 registry step around the recommended set,
   for four system metrics:
   - system P(bad debt): the mean over classes A, B and C of P(collateral is below the debt when the
     claim path opens);
   - minting halted by price (hours per year with NO_PRICE or HALT-3 set);
   - false activation halts (hours per year at the policy's enforcing share);
   - oracle attack share (the smallest colluding hash share that controls a median).

   The section also has a tornado chart per metric and the list of *insensitive* parameters.
5. **Design notes.** Problems that parameter tuning cannot fix, collected from every study and
   de-duplicated. One example is the early supply cap, which keeps classes B and C closed.
6. **Devnet validation.** Whether the simulator was checked against real `ycashd` nodes. Here
   *skipped* means no binary was available. *Not validated* means a binary exists but
   `ybcal devnet validate` has not been run on this overlay.
7. **Lock-readiness checklist.** See below.

The appendices list the non-tunable parameters (derived, per-release, constants and identity
fields) with how each is set, the joint-pass history (rounds, moves, group status, warnings), the
patch with its `git apply --check` result, and the command and timings.

## Verdicts

| Verdict | Meaning |
|---|---|
| **KEEP** | The current value stays. No candidate improved the primary metric by more than the policy's `materiality` (20 % by default) without violating a constraint. Ties go to the current value. |
| **CHANGE** | Move to the recommended value. It satisfies every policy constraint and improves the primary metric materially, or the current value violates the policy. |
| **PROVISIONAL** | The study's answer rests on synthetic data only. It shows a direction, not a value to lock. The recommended value may still equal the current one. |
| **BLOCKED** | No evaluated value satisfies the policy. The owner must relax the policy, or the rule needs to change (see the design notes). |
| **NOT RUN** | The group's study is missing from this build or raised an error. There is no recommendation, and the current value stands until the study runs. |

Confidence (*high / medium / low*) is the study's own judgement of its evidence. It depends on the
budget, the provenance and how close the decision was.

## Provenance

- **real-data**: the decision used data the owner supplied: hourly YEC/USD prices, a `spreads.py`
  log, or a pool-share series.
- **synthetic**: the decision used only the WP-2 synthetic presets and the scenario library. These
  are placeholders tuned to look YEC-like (about 120 % annualised volatility, fat tails). The study
  verdict is therefore PROVISIONAL.
- **judgement**: exact mathematics given an assumed input, such as the G5 binomial halt rates at the
  policy's expected enforcing share. It is neither a measurement nor synthetic.

## The joint pass, in one paragraph

The studies run in dependency order: G1 → G2 → G5 → G3 → G4 → G7 → G6 → G8 → G9 → R. Each one starts
from the shipped set with every earlier group's recommendation applied, so later groups see the
earlier choices. After a full round, the pass stops if nothing moved. Otherwise it runs another
round, up to `max_rounds_joint` (3) rounds. Some studies, in a later round, compare candidates
against an intermediate value rather than the shipped one. The report restates those
recommendations against the shipped value. If a value was decided in round 1 and only confirmed
later, the round-1 decision (metrics, explanation) is shown, with a note. A group whose
recommendation would break an invariant of the joint set is not applied, and the history says so.

## Insensitive parameters

A parameter is *insensitive* when moving it ±1 step changes none of the four system metrics: its
total-order index is below `insensitive_total_order` (0.01). At the quick budget the factors are
whole study groups, because the per-parameter Saltelli design would be too large. Inside a
sensitive group, a parameter's own ±1-step tornado share decides. The label **does not override a
study's verdict**. The four metrics are system-level, and most attestation or fee parameters do not
enter them at all. When the label sits on a changed value, a note says that the change rests on the
group study's own metric. Run `ybcal sensitivity --params a,b,c --method sobol` for per-parameter
indices on a subset.

## Lock-readiness

The set is lock-ready when every **required** item passes:

1. every locked parameter has a recommendation (its study ran);
2. every locked recommendation is non-provisional and backed by real data (required while
   `require_real_data_for_lock = true`);
3. no parameter is BLOCKED;
4. the recommended set passes every PLAN §1.4 invariant;
5. the release study ran and nothing in it is BLOCKED.

Advisory items, which never block: the joint pass converged, `params.cpp.patch` applies at the pin,
and the devnet differential suite passed (milestone M6).

A synthetic-only run is never lock-ready, by design.

## The patch

`params.cpp.patch` is a unified diff against `src/yellowback/params.cpp` at the pinned commit. It
touches only lines inside `SetCommon()` and `MainParams()`. Every changed line carries a
`ybcal: name old -> new (report §3.n)` comment.

- `SetCommon()` is shared by mainnet and testnet, so a change there moves the testnet column too
  (`recommended.json` mirrors this).
- Derived lines (min-fills, `qHighBps`, `attestMaxAge`, `recapRatioBps`, `volPeriodsPerYear`) change
  with their parent.
- Excluded rows go to `params-patch-release.patch`.
- `params.h` constants such as `DEFAULT_REF_LAG` are only listed.

When a ycash6 clone is available (`--ycash6` or `$YBCAL_YCASH6`), the run checks the patch with
`git apply --check` in a throwaway detached worktree under `.work/`, then removes it. Apply it by
hand in ycash6 after review.

## Rerunning with real data

1. Gather the data on a machine with network access (see `docs/data.md`):

   ```
   ybcal data fetch --source coingecko --days 365 --out data/local/yec-hourly.csv
   python contrib/yellowback/attest/calibrate/spreads.py log …   # ≥ 2 weeks → data/local/spreads.csv
   # optional: pool shares (height,payout_key) → data/local/pool-shares.csv; depth → data/local/depth.csv
   ```

2. Run:

   ```
   ybcal recommend --budget standard            # reads every file in data/local/
   ybcal recommend --data data/local/yec-hourly.csv --data data/local/spreads.csv --budget standard
   ```

   The format of each file is detected from its contents: a spreads log, a pool-share CSV, a depth
   CSV, otherwise a price series (resampled to hours). `--synthetic` ignores `data/local/`.

3. Reproduce a run exactly with `ybcal recommend --manifest reports/<run>/manifest.json`. A data
   file or policy whose hash changed since the run is reported.

Other useful flags:

- `--groups G1,G2` runs a subset.
- `--workers N` sets the number of processes.
- `--max-rounds N` overrides the joint-pass round limit.
- `--no-sensitivity` skips §4.
- `--cache .work/cache` keeps evaluations between runs.
- `ybcal study G3` runs one group, one round, and writes a mini report.

Budgets: `quick` (the CI smoke run; minutes), `standard` (about an hour), `deep` (overnight).

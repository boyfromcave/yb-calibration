# Robustness of the recommendation

6 run(s) of `ybcal recommend --budget standard` --groups G3: windows full, last365 × models bootstrap × seeds 20261003, 20261004, 20261005. Agreement threshold 80%.

Flags: **unstable** = the modal value or verdict is shared by fewer runs than the threshold; *window-sensitive* / *model-sensitive* = the modal value differs between windows / price models; *none-feasible* = no candidate meets its rule in any run (the consolidated value is then the closest to current; see BLOCKED / environment limits in the runs' reports); *seed-noise* = seeds disagree within one window and model. `(pin)` = owner-pinned, `(env)` = policy unmeetable in this environment.

**Consolidated** = the value whose own rule's constraints are met in the most runs (every evaluated candidate of every run counts, not only each run's winner), ties → closest to current; *feasible k/N* counts those runs, and the violations column names, per run, what fails there.

| Parameter | Group | Current | Consolidated (feasible k/N) | Modal value | Agreement | Verdicts | By window | By model | Flags | Violations at the consolidated value |
|---|---|---|---|---|---|---|---|---|---|---|
| `baseRatioBps[0]` | G3 | 72,500 bps (725 %) | 192,500 bps (1925 %) (worse-window) | 72,500 bps (725 %) | 50% | KEEP 3, CHANGE 3 | full: 72,500 bps (725 %); last365: 162,500 bps (1625 %) | bootstrap: 72,500 bps (725 %) | **unstable**, window-sensitive, seed-noise | none |
| `claimThresholdBps` | G3 | 12,500 bps (125 %) | 12,500 bps (125 %) (median) | 12,500 bps (125 %) | 100% | KEEP (pin) 6 | full: 12,500 bps (125 %); last365: 12,500 bps (125 %) | bootstrap: 12,500 bps (125 %) | stable | none |
| `classMin[0]` | G3 | 34,560 blocks (30 d) | 34,560 blocks (30 d) (median) | 34,560 blocks (30 d) | 100% | KEEP (pin) 6 | full: 34,560 blocks (30 d); last365: 34,560 blocks (30 d) | bootstrap: 34,560 blocks (30 d) | stable | none |
| `classMin[1]` | G3 | 103,681 blocks (90 d) | 103,681 blocks (90 d) (median) | 103,681 blocks (90 d) | 100% | KEEP (pin) 6 | full: 103,681 blocks (90 d); last365: 103,681 blocks (90 d) | bootstrap: 103,681 blocks (90 d) | stable | none |
| `classMin[2]` | G3 | 420,481 blocks (365 d) | 420,481 blocks (365 d) (median) | 420,481 blocks (365 d) | 100% | KEEP (pin) 6 | full: 420,481 blocks (365 d); last365: 420,481 blocks (365 d) | bootstrap: 420,481 blocks (365 d) | stable | none |
| `classMax[0]` | G3 | 103,680 blocks (90 d) | 103,680 blocks (90 d) (median) | 103,680 blocks (90 d) | 100% | KEEP (pin) 6 | full: 103,680 blocks (90 d); last365: 103,680 blocks (90 d) | bootstrap: 103,680 blocks (90 d) | stable | none |
| `classMax[1]` | G3 | 103,680 blocks (90 d) | 103,680 blocks (90 d) (median) | 103,680 blocks (90 d) | 100% | KEEP (pin) 6 | full: 103,680 blocks (90 d); last365: 103,680 blocks (90 d) | bootstrap: 103,680 blocks (90 d) | stable | none |
| `classMax[2]` | G3 | 420,480 blocks (365 d) | 420,480 blocks (365 d) (median) | 420,480 blocks (365 d) | 100% | KEEP (pin) 6 | full: 420,480 blocks (365 d); last365: 420,480 blocks (365 d) | bootstrap: 420,480 blocks (365 d) | stable | none |
| `baseRatioBps[1]` | G3 | 62,500 bps (625 %) | 62,500 bps (625 %) (median) | 62,500 bps (625 %) | 100% | KEEP 6 | full: 62,500 bps (625 %); last365: 62,500 bps (625 %) | bootstrap: 62,500 bps (625 %) | stable | none |
| `baseRatioBps[2]` | G3 | 30,000 bps (300 %) | 30,000 bps (300 %) (median) | 30,000 bps (300 %) | 100% | KEEP 6 | full: 30,000 bps (300 %); last365: 30,000 bps (300 %) | bootstrap: 30,000 bps (300 %) | stable | none |
| `emergencyRatioBps` | G3 | 10,500 bps (105 %) | 10,500 bps (105 %) (median) | 10,500 bps (105 %) | 100% | PROVISIONAL 6 | full: 10,500 bps (105 %); last365: 10,500 bps (105 %) | bootstrap: 10,500 bps (105 %) | stable | none |

## Worse-window lock rule (hardening plan H-3)

Per window, the smallest value whose own rule's constraints hold in every run of that window; the locked value is the largest of those needs (`ratio_lock_rule = "worse-window"`).

| Parameter | Need (full) | Need (last365) | Locked value |
|---|---|---|---|
| `baseRatioBps[0]` | 72,500 bps (725 %) (smallest value feasible in all 3 run(s)) | 192,500 bps (1925 %) (smallest value feasible in all 3 run(s)) | **192,500 bps (1925 %)** |

Per-run values: `robust.csv`; per-parameter detail (values, by seed): `robust-summary.csv`, `robust.json`. Each run's full report: `runs/<window>__<model>__s<seed>/report.md`.

# Robustness of the recommendation

3 run(s) of `ybcal recommend --budget standard` --groups G6,G8: windows full × models bootstrap × seeds 20261003, 20261004, 20261005. Agreement threshold 80%.

Flags: **unstable** = the modal value or verdict is shared by fewer runs than the threshold; *window-sensitive* / *model-sensitive* = the modal value differs between windows / price models; *none-feasible* = no candidate meets its rule in any run (the consolidated value is then the closest to current; see BLOCKED / environment limits in the runs' reports); *seed-noise* = seeds disagree within one window and model. `(pin)` = owner-pinned, `(env)` = policy unmeetable in this environment.

**Consolidated** = the value whose own rule's constraints are met in the most runs (every evaluated candidate of every run counts, not only each run's winner), ties → closest to current; *feasible k/N* counts those runs, and the violations column names, per run, what fails there.

| Parameter | Group | Current | Consolidated (feasible k/N) | Modal value | Agreement | Verdicts | By window | By model | Flags | Violations at the consolidated value |
|---|---|---|---|---|---|---|---|---|---|---|
| `deviationBps` | G6 | 1,000 bps (10 %) | 1,600 bps (16 %) (3/3) | 1,500 bps (15 %) | 67% | CHANGE 3 | full: 1,500 bps (15 %) | bootstrap: 1,500 bps (15 %) | **unstable**, seed-noise | none |
| `payeeWindow` | G6 | 100 blocks (2.1 h) | 200 blocks (4.2 h) (3/3) | 150 blocks (3.1 h) | 67% | CHANGE 3 | full: 150 blocks (3.1 h) | bootstrap: 150 blocks (3.1 h) | **unstable**, seed-noise | none |
| `accuracyBandBps` | G6 | 300 bps (3 %) | 300 bps (3 %) (0/3) | 100 bps (1 %) | 100% | CHANGE 3 | full: 100 bps (1 %) | bootstrap: 100 bps (1 %) | none-feasible | full__bootstrap__s20261003: fee0, fee_share, edge_redeem; full__bootstrap__s20261004: fee0, fee_share, edge_redeem; full__bootstrap__s20261005: fee0, fee_share, edge_redeem |
| `feeBps` | G6 | 25 bps (0.25 %) | 25 bps (0.25 %) (0/3) | 15 bps (0.15 %) | 100% | CHANGE (env) 3 | full: 15 bps (0.15 %) | bootstrap: 15 bps (0.15 %) | none-feasible | full__bootstrap__s20261003: fee_share; full__bootstrap__s20261004: fee_share; full__bootstrap__s20261005: fee_share |
| `attestFeeBps` | G6 | 2,500 bps (25 %) | 2,500 bps (25 %) (0/3) | 2,500 bps (25 %) | 100% | KEEP (pin) 3 | full: 2,500 bps (25 %) | bootstrap: 2,500 bps (25 %) | none-feasible | full__bootstrap__s20261003: fee_share; full__bootstrap__s20261004: fee_share; full__bootstrap__s20261005: fee_share |
| `pinDeltaBps` | G8 | 500 bps (5 %) | 500 bps (5 %) (0/3) | 500 bps (5 %) | 100% | KEEP 3 | full: 500 bps (5 %) | bootstrap: 500 bps (5 %) | none-feasible | full__bootstrap__s20261003: grief_capture; full__bootstrap__s20261004: grief_capture; full__bootstrap__s20261005: grief_capture |
| `divergeBpsAttest` | G8 | 1,500 bps (15 %) | 1,500 bps (15 %) (0/3) | 1,500 bps (15 %) | 100% | KEEP 3 | full: 1,500 bps (15 %) | bootstrap: 1,500 bps (15 %) | none-feasible | full__bootstrap__s20261003: grief_capture; full__bootstrap__s20261004: grief_capture; full__bootstrap__s20261005: grief_capture |
| `nReg` | G6 | 576 blocks (12 h) | 576 blocks (12 h) (3/3) | 576 blocks (12 h) | 100% | KEEP 3 | full: 576 blocks (12 h) | bootstrap: 576 blocks (12 h) | stable | none |
| `peerLag` | G6 | 10 blocks | 10 blocks (3/3) | 10 blocks | 100% | KEEP 3 | full: 10 blocks | bootstrap: 10 blocks | stable | none |
| `peerMin` | G6 | 5 | 5 (3/3) | 12 | 100% | CHANGE 3 | full: 12 | bootstrap: 12 | stable | none |
| `feeMin` | G6 | 50,000,000 zat (0.5 YEC) | 20,000,000 zat (0.2 YEC) (3/3) | 20,000,000 zat (0.2 YEC) | 100% | CHANGE 3 | full: 20,000,000 zat (0.2 YEC) | bootstrap: 20,000,000 zat (0.2 YEC) | stable | none |
| `nPenalty` | G6 | 288 blocks (6 h) | 288 blocks (6 h) (3/3) | 192 blocks (4 h) | 100% | CHANGE 3 | full: 192 blocks (4 h) | bootstrap: 192 blocks (4 h) | stable | none |
| `accuracyWindow` | G6 | 576 blocks (12 h) | 576 blocks (12 h) (3/3) | 576 blocks (12 h) | 100% | KEEP 3 | full: 576 blocks (12 h) | bootstrap: 576 blocks (12 h) | stable | none |
| `payeeTiltBps` | G6 | 10,000 bps (100 %) | 10,000 bps (100 %) (3/3) | 10,000 bps (100 %) | 100% | KEEP 3 | full: 10,000 bps (100 %) | bootstrap: 10,000 bps (100 %) | stable | none |
| `attestArmMin` | G8 | 5 | 5 (3/3) | 5 | 100% | KEEP (pin) 3 | full: 5 | bootstrap: 5 | stable | none |
| `attestArmDelay` | G8 | 1,152 blocks (1 d) | 1,152 blocks (1 d) (3/3) | 1,152 blocks (1 d) | 100% | KEEP (pin) 3 | full: 1,152 blocks (1 d) | bootstrap: 1,152 blocks (1 d) | stable | none |
| `nSlots` | G8 | 9 | 9 (3/3) | 9 | 100% | KEEP 3 | full: 9 | bootstrap: 9 | stable | none |
| `mSelect` | G8 | 4 | 4 (3/3) | 4 | 100% | KEEP 3 | full: 4 | bootstrap: 4 | stable | none |
| `kSlack` | G8 | 2 | 2 (3/3) | 2 | 100% | KEEP 3 | full: 2 | bootstrap: 2 | stable | none |
| `bundleMax` | G8 | 6 | 6 (3/3) | 6 | 100% | KEEP 3 | full: 6 | bootstrap: 6 | stable | none |
| `qLowBps` | G8 | 3,333 bps (33.33 %) | 3,333 bps (33.33 %) (3/3) | 3,333 bps (33.33 %) | 100% | KEEP 3 | full: 3,333 bps (33.33 %) | bootstrap: 3,333 bps (33.33 %) | stable | none |
| `pinWindow` | G8 | 288 blocks (6 h) | 288 blocks (6 h) (3/3) | 288 blocks (6 h) | 100% | KEEP 3 | full: 288 blocks (6 h) | bootstrap: 288 blocks (6 h) | stable | none |
| `pinMinTags` | G8 | 3 | 3 (3/3) | 3 | 100% | KEEP 3 | full: 3 | bootstrap: 3 | stable | none |
| `pinMinBundles` | G8 | 2 | 2 (3/3) | 2 | 100% | KEEP 3 | full: 2 | bootstrap: 2 | stable | none |
| `emergencyPersist` | G8 | 48 blocks (1 h) | 48 blocks (1 h) (3/3) | 48 blocks (1 h) | 100% | PROVISIONAL 3 | full: 48 blocks (1 h) | bootstrap: 48 blocks (1 h) | stable | none |
| `emergencyNoticeTtl` | G8 | 1,152 blocks (1 d) | 1,152 blocks (1 d) (3/3) | 1,152 blocks (1 d) | 100% | KEEP 3 | full: 1,152 blocks (1 d) | bootstrap: 1,152 blocks (1 d) | stable | none |
| `bondMin` | G8 | 2,000,000,000,000 zat (20000 YEC) | 2,000,000,000,000 zat (20000 YEC) (3/3) | 2,000,000,000,000 zat (20000 YEC) | 100% | KEEP 3 | full: 2,000,000,000,000 zat (20000 YEC) | bootstrap: 2,000,000,000,000 zat (20000 YEC) | stable | none |
| `bondMaturity` | G8 | 16,128 blocks (14 d) | 16,128 blocks (14 d) (3/3) | 16,128 blocks (14 d) | 100% | KEEP 3 | full: 16,128 blocks (14 d) | bootstrap: 16,128 blocks (14 d) | stable | none |
| `ageCap` | G8 | 207,360 blocks (180 d) | 207,360 blocks (180 d) (3/3) | 207,360 blocks (180 d) | 100% | KEEP 3 | full: 207,360 blocks (180 d) | bootstrap: 207,360 blocks (180 d) | stable | none |
| `foundingWindow` | G8 | 8,064 blocks (7 d) | 8,064 blocks (7 d) (3/3) | 8,064 blocks (7 d) | 100% | KEEP 3 | full: 8,064 blocks (7 d) | bootstrap: 8,064 blocks (7 d) | stable | none |
| `dormancyBlocks` | G8 | 16,128 blocks (14 d) | 16,128 blocks (14 d) (3/3) | 16,128 blocks (14 d) | 100% | KEEP 3 | full: 16,128 blocks (14 d) | bootstrap: 16,128 blocks (14 d) | stable | none |
| `dormancyMinBundles` | G8 | 20 | 15 (3/3) | 12 | 100% | CHANGE 3 | full: 12 | bootstrap: 12 | stable | none |
| `dormancyCheck` | G8 | 48 blocks (1 h) | 48 blocks (1 h) (3/3) | 48 blocks (1 h) | 100% | KEEP 3 | full: 48 blocks (1 h) | bootstrap: 48 blocks (1 h) | stable | none |
| `attestInterval` | G8 | 10 blocks | 10 blocks (3/3) | 10 blocks | 100% | KEEP 3 | full: 10 blocks | bootstrap: 10 blocks | stable | none |

Per-run values: `robust.csv`; per-parameter detail (values, by seed): `robust-summary.csv`, `robust.json`. Each run's full report: `runs/<window>__<model>__s<seed>/report.md`.

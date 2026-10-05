| Parameter | Shipped | Recommended | Basis |
|---|---|---|---|
| `pMidWindow` | 576 | **1,008** | least-harm, 6/6 standard (and 12/12 of the earlier standard sweep) |
| `pSlowWindow` | 2,016 | **1,152** | least-harm, 6/6 standard (and 12/12 of the earlier standard sweep) |
| `sigmaRefBps` | 10,000 | **18,000** | median of 6 standard runs (17,000-24,500; full window 17,000-18,000) |
| `sigmaMultMaxBps` | 30,000 | **47,500** | median of 20 runs; real-history p99 need 4.52-4.71x (D-RD-ORA) |
| `baseRatioBps[0]` | 50,000 | **72,500** | feasible in the most standard runs (3/6; last365 needs more) |
| `baseRatioBps[1]` | 40,000 | **62,500** | least-harm (environment limit G3-DN1), modal of 6 standard |
| `claimThresholdBps` | 11,000 | **12,500** | feasible in every run except 3 under the excluded regime model |
| `signalWindow` | 2,016 | **2,592** | feasible 6/6 standard; fails false_halt only in quick seed 20261004; devnet-confirmed on both lines (D-RD-ACT-8) |
| `activationThreshold` | 1,512 | **1,944** | 75 % of signalWindow (L3 pin) |
| `participationFloor` | 1,210 | **1,556** | 60 % of signalWindow (L3 pin) |
| `enforcementFloor` | 1,008 | **1,296** | 50 % of signalWindow (L3 pin) |
| `enforcementResume` | 1,210 | **1,556** | 60 % of signalWindow (L3 pin) |
| `peerMin` | 5 | **12** | median, 20/20 runs |
| `deviationBps` | 1,000 | **1,800** | feasible 19/20 (1,700 fails false_penalty_stale in all three 2021-22 runs) |
| `accuracyBandBps` | 300 | **100** | least-harm, 20/20 runs |
| `feeMin` | 50,000,000 | **40,000,000** | feasible 20/20 (0.5 YEC fails in some runs) |
| `feeBps` | 25 | **10** | least-harm (environment limit G6-ENV-1), 17/20 runs |
| `divergenceBps` | 2,000 | **2,500** | feasible in the most runs (7/20); 2,000 fails calm_availability in 20/20 |
| `dormancyMinBundles` | 20 | **15** | feasible 20/20, closest to current |
| `walletConfirmations` | 6 | **24** | 20/20 runs; sized against the 21 % second pool (G9-ENV-2) |

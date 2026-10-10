## Sizes per path (bytes, serialized v4)

| Path | today (EC owner, P2PKH holder) | PQ, P2PKH holders | (b) SLH holders | (a) Falcon holders |
|---|---:|---:|---:|---:|
| carrier | 243 | 243 | 243 | 243 |
| mint | 1,018 | 1,018 | 1,028 | 1,028 |
| transfer | 453 | 453 | 8,306 | 1,945 |
| redeem | 382 | 8,249 | 16,082 | 9,721 |
| claim | 1,321 | 1,321 | 9,154 | 2,793 |
| release_claimant | 105 | 105 | 105 | 105 |
| release_residual | 105 | 115 | 115 | 115 |
| residual_sweep | 211 | 8,044 | 8,044 | 8,044 |
| bridge_lock | 437 | 437 | 437 | 437 |
| bridge_recover | 178 | 8,045 | 8,045 | 8,045 |

| Reference | bytes |
|---|---:|
| p2pkh_1in2out | 245 |
| p2pkh_2in2out | 393 |
| sapling_z2z | 2,373 |

## Fees per PQ path (zat): ycash-dd / ycash6 (max form) / ycash6 additive

| Path / variant | m = 1 | m = 10 | m = 34 | m = 50 | m = 100 | relay minimum |
|---|---:|---:|---:|---:|---:|---:|
| redeem/pq_p2pkh | 1,000 / 27,500 / 28,324 | 8,249 / 27,500 / 35,749 | 28,046 / 28,046 / 55,546 | 41,245 / 41,245 / 68,745 | 82,490 / 82,490 / 109,990 | 824 |
| residual_sweep/pq_p2pkh | 1,000 / 27,000 / 27,804 | 8,044 / 27,000 / 35,044 | 27,349 / 27,349 / 54,349 | 40,220 / 40,220 / 67,220 | 80,440 / 80,440 / 107,440 | 804 |
| bridge_recover/pq_p2pkh | 1,000 / 27,000 / 27,804 | 8,045 / 27,000 / 35,045 | 27,353 / 27,353 / 54,353 | 40,225 / 40,225 / 67,225 | 80,450 / 80,450 / 107,450 | 804 |
| transfer/slh | 1,000 / 27,500 / 28,330 | 8,306 / 27,500 / 35,806 | 28,240 / 28,240 / 55,740 | 41,530 / 41,530 / 69,030 | 83,060 / 83,060 / 110,560 | 830 |
| redeem/slh | 1,608 / 53,500 / 55,108 | 16,082 / 53,500 / 69,582 | 54,678 / 54,678 / 108,178 | 80,410 / 80,410 / 133,910 | 160,820 / 160,820 / 214,320 | 1,000 |
| claim/slh | 1,000 / 29,000 / 29,915 | 9,154 / 29,000 / 38,154 | 31,123 / 31,123 / 60,123 | 45,770 / 45,770 / 74,770 | 91,540 / 91,540 / 120,540 | 915 |
| residual_sweep/slh | 1,000 / 27,000 / 27,804 | 8,044 / 27,000 / 35,044 | 27,349 / 27,349 / 54,349 | 40,220 / 40,220 / 67,220 | 80,440 / 80,440 / 107,440 | 804 |
| bridge_recover/slh | 1,000 / 27,000 / 27,804 | 8,045 / 27,000 / 35,045 | 27,353 / 27,353 / 54,353 | 40,225 / 40,225 / 67,225 | 80,450 / 80,450 / 107,450 | 804 |
| transfer/falcon | 1,000 / 6,000 / 6,194 | 1,945 / 6,000 / 7,945 | 6,613 / 6,613 / 12,613 | 9,725 / 9,725 / 15,725 | 19,450 / 19,450 / 25,450 | 194 |
| redeem/falcon | 1,000 / 32,500 / 33,472 | 9,721 / 32,500 / 42,221 | 33,051 / 33,051 / 65,551 | 48,605 / 48,605 / 81,105 | 97,210 / 97,210 / 129,710 | 972 |
| claim/falcon | 1,000 / 8,000 / 8,279 | 2,793 / 8,000 / 10,793 | 9,496 / 9,496 / 17,496 | 13,965 / 13,965 / 21,965 | 27,930 / 27,930 / 35,930 | 279 |
| residual_sweep/falcon | 1,000 / 27,000 / 27,804 | 8,044 / 27,000 / 35,044 | 27,349 / 27,349 / 54,349 | 40,220 / 40,220 / 67,220 | 80,440 / 80,440 / 107,440 | 804 |
| bridge_recover/falcon | 1,000 / 27,000 / 27,804 | 8,045 / 27,000 / 35,045 | 27,353 / 27,353 / 54,353 | 40,225 / 40,225 / 67,225 | 80,450 / 80,450 / 107,450 | 804 |

## Chain growth

| Scenario | Variant | bytes/day | PQ bytes/day | GB/year | share of block space | PQ verifies/day |
|---|---|---:|---:|---:|---:|---:|
| low | today | 8,268 | 0 | 0.003 | 0.00036% | 0.0 |
| low | pq_p2pkh | 17,704 | 9,527 | 0.006 | 0.00077% | 1.2 |
| low | slh | 105,653 | 98,432 | 0.039 | 0.00459% | 12.4 |
| low | falcon | 34,410 | 27,189 | 0.013 | 0.00149% | 12.4 |
| mid | today | 64,390 | 0 | 0.024 | 0.00279% | 0.0 |
| mid | pq_p2pkh | 120,222 | 56,366 | 0.044 | 0.00522% | 7.1 |
| mid | slh | 960,452 | 905,732 | 0.351 | 0.04169% | 114.1 |
| mid | falcon | 279,826 | 225,105 | 0.102 | 0.01215% | 114.1 |
| high | today | 546,353 | 0 | 0.199 | 0.02371% | 0.0 |
| high | pq_p2pkh | 829,445 | 285,799 | 0.303 | 0.03600% | 36.0 |
| high | slh | 8,957,100 | 8,501,629 | 3.269 | 0.38876% | 1,071.0 |
| high | falcon | 2,373,465 | 1,917,994 | 0.866 | 0.10301% | 1,071.0 |
| stress | today | 4,741,460 | 0 | 1.731 | 0.20579% | 0.0 |
| stress | pq_p2pkh | 5,409,675 | 674,795 | 1.975 | 0.23479% | 85.0 |
| stress | slh | 84,567,315 | 80,689,835 | 30.867 | 3.67046% | 10,165.0 |
| stress | falcon | 20,448,435 | 16,570,955 | 7.464 | 0.88752% | 10,165.0 |

## Filling one 2 MB block (total fees, zat)

| Block of | dd / 6 at m = 1 | dd / 6 at m = 10 | dd / 6 at m = 34 | dd / 6 at m = 50 | dd / 6 at m = 100 | relay minimum |
|---|---:|---:|---:|---:|---:|---:|
| p2pkh_1in2out | 8,163,000 / 8,163,000 | 8,163,000 / 8,163,000 | 8,163,000 / 8,163,000 | 8,163,000 / 8,163,000 | 8,163,000 / 8,163,000 | 195,912 |
| p2pkh_2in2out | 5,089,000 / 5,089,000 | 5,089,000 / 5,089,000 | 5,089,000 / 5,089,000 | 5,089,000 / 5,089,000 | 5,089,000 / 5,089,000 | 198,471 |
| sapling_z2z | 842,000 / 842,000 | 842,000 / 842,000 | 842,000 / 842,000 | 842,000 / 842,000 | 842,000 / 842,000 | 199,554 |
| redeem/slh | 199,392 / 6,634,000 | 1,994,168 / 6,634,000 | 6,780,072 / 6,780,072 | 9,970,840 / 9,970,840 | 19,941,680 / 19,941,680 | 124,000 |
| transfer/slh | 240,000 / 6,600,000 | 1,993,440 / 6,600,000 | 6,777,600 / 6,777,600 | 9,967,200 / 9,967,200 | 19,934,400 / 19,934,400 | 199,200 |
| bridge_recover/slh | 248,000 / 6,696,000 | 1,995,160 / 6,696,000 | 6,783,544 / 6,783,544 | 9,975,800 / 9,975,800 | 19,951,600 / 19,951,600 | 199,392 |
| redeem/falcon | 205,000 / 6,662,500 | 1,992,805 / 6,662,500 | 6,775,455 / 6,775,455 | 9,964,025 / 9,964,025 | 19,928,050 / 19,928,050 | 199,260 |
| transfer/falcon | 1,028,000 / 6,168,000 | 1,999,460 / 6,168,000 | 6,798,164 / 6,798,164 | 9,997,300 / 9,997,300 | 19,994,600 / 19,994,600 | 199,432 |
| bridge_recover/falcon | 248,000 / 6,696,000 | 1,995,160 / 6,696,000 | 6,783,544 / 6,783,544 | 9,975,800 / 9,975,800 | 19,951,600 / 19,951,600 | 199,392 |

## Dust (G9 minOutput lower bound, 3 x a token spend at worst_price_usd)

- `mult`: 50
- `today_transfer_fee_zat`: 1,000
- `today_minOutput_bound_usd_at_worst`: 0.0030
- `slh_transfer_fee_zat`: 41,530
- `slh_minOutput_bound_usd_at_worst`: 0.1246
- `falcon_transfer_fee_zat`: 9,725
- `falcon_minOutput_bound_usd_at_worst`: 0.0292

## Recommendation

**-pqfeerate multiplier m = 50** (rate 5,000 zat/KB).

- P2PKH parity needs m >= 40.82: at 1x a PQ byte pays 0.1 zat against 4.08 zat for an ordinary payment's byte, so block templates (fee-rate ordered) would place PQ spends last.
- Cross-line identity needs m >= 33.57: below it ycash6's ZIP-317 conventional fee (500 zat per 150 input bytes = 3,333 zat/KB) is the max and the two lines charge different fees.
- Sapling parity needs only m >= 4.22 (a Sapling z->z pays 0.421 zat/byte).
- At m = 50 the dearest PQ path costs $0.0804 at the policy's worst price $100/YEC (cap $1).

## Validation (Q1 benchmarks, µs/op: ECDSA 18.9, SLH 235.0, Falcon 18.9)

- `ecdsa_inputs_per_block`: 13513
- `policy_pq_checks_per_block`: 1000
- `slh_per_block`: 248
- `slh_per_block_policy`: 248
- `slh_break_even_ratio`: 54.4879
- `slh_block_verify_s`: 0.0583
- `ecdsa_block_verify_s`: 0.2554
- `slh_measured_ratio`: 12.4339
- `falcon_per_block`: 1188
- `falcon_per_block_policy`: 1000
- `falcon_break_even_ratio`: 11.3746
- `falcon_block_verify_s`: 0.0225
- `falcon_measured_ratio`: 1.0000
- `sapling_z2z_per_block`: 842
- `sapling_block_verify_s`: MISSING (no figure in the repo or the Q1 table)

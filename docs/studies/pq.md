# PQ — post-quantum transaction sizes, chain growth and the `-pqfeerate` multiplier

Code: `src/ybcal/studies/pq_growth.py` (quantum plan Q7, yb-calibration row). CLI: `ybcal pq
[--multipliers 1,10,34,50,100] [--n-yed 1] [--k-attest 3] [--policy FILE] [--out DIR]`.
Tests: `tests/studies/test_pq_growth.py`. Report: [docs/reports/2026-10-pq/](../reports/2026-10-pq/README.md).
Inputs: `docs/plans/yellowback-quantum-plan.md` §4.5, §6, §9 item 3 and the frozen byte layouts of
`docs/plans/yellowback-quantum-spec.md` (workspace repo).

An analytic study, not a group study: no price data, no simulation, no devnet; it runs in under a
second. It changes no registry parameter. Its output is the release value of the node option
`-pqfeerate` (both node lines) and the growth figures the plan's §6 risk row asks for.

## Method

**Sizes.** Every path is a `TxShape` (scriptSig length per transparent input, scriptPubKey length per
output, Sapling descriptions) serialized on the v4 format: header 8, CompactSize counts, inputs
`32 + 4 + cs + scriptSig + 4`, outputs `8 + cs + script`, nLockTime, nExpiryHeight, valueBalance, the
Sapling vectors, `nJoinSplit = 0`, bindingSig only with Sapling descriptions. PQ scriptSigs are
rebuilt from the chunk rule (520-byte chunks, minimal pushes, hashtype on the last signature chunk)
and pinned against the spec: SLH-DSA owner 7,939, Falcon owner 1,578, holder spends 7,938 / 1,577,
`TX_PQPKH` 35. Shapes follow ycash-dd `src/yellowback/txbuilder.cpp` (`MintOutputs`, `PlanVaultSpend`,
`BuildTransfer`): a MINT spends funding + the bundle carrier and pays V, token, payload, pool fee,
attestor fee and change; a TRANSFER spends `n_yed` tokens + funding; the owner REDEEM spends V
(selector 2) + `n_yed` tokens; a CLAIM spends V (selector 4) + claimant tokens + funding + carrier and
pays two intents; each claim adds two intent releases and the owner's one sweep of the residual,
which is PQPKH(owner) on the PQ line (spec F-3) and therefore an SLH-DSA spend.

**Variants.** `today` (EC owner, P2PKH holders: the `upgrade/vault` line), `pq_p2pkh` (SLH owner,
P2PKH holders while Falcon is inactive: the spec §3.6 wallet default), `slh` (D-Q-2 option b: every
holder on SLH-DSA) and `falcon` (option a: Falcon holders).

**Fees.** ycash-dd: a PQ transaction (one carrying a PQ scriptSig) pays `max(DEFAULT_FEE,
m × 100 zat/KB × size)`, others the flat 1,000. ycash6: `max(-yellowbackfee, ZIP-317 conventional fee)`
(P-2) plus the size term for a PQ transaction, as a third `max` argument (the smallest reading of plan
§4.5) and, for comparison, additive. ZIP-317 is reproduced from `zip317.cpp`. The relay minimum is
`GetFeeForRelay = min(100 zat/KB × size, DEFAULT_FEE)` on both lines.

**Recommendation rule.** The smallest round candidate (`CANDIDATES`) that is at least
(1) the multiplier at which every PQ path pays at least an ordinary P2PKH payment's fee per byte on
ycash-dd (P2PKH parity: PQ spends are not placed last by fee-rate-ordered templates), and
(2) the multiplier from which the size term binds over ZIP-317 on ycash6 for every PQ path
(cross-line identity: both wallets charge the same fee for the same transaction), subject to the
dearest PQ path's fee at the policy's `worst_price_usd` staying under $1. Sapling parity is reported
but implied.

**Growth.** Scenarios are the policy's `adoption_scenarios` (mints and redeems per day) plus volumes
declared in `EXTRA_VOLUMES` (transfers, claims, bridge locks and recovers per day: **assumptions**,
not data) and a `stress` scenario (10,000 transfers a day, the plan §6 figure). Bytes per day are
`Σ count × size`, a year is 365 days, block space is 2 MB × 1,152 blocks a day.

**Validation.** Structural only: SLH/Falcon spends per full block, the policy cap of 1,000
`OP_CHECKPQSIG` per template (20,000 sigops ÷ 20, spec F-5), and the verify-time ratio below which a
PQ-filled block verifies no slower than a block of P2PKH inputs. Measured times are `VERIFY_US`
placeholders (`None`) until the Q1 benchmarks exist.

## Limits

- Sizes are computed, not measured. A devnet hook that records `getrawtransaction(...).size` per
  action on a quantum build would confirm them (clients survey §9 item 3).
- The transfer/claim/bridge volumes are assumptions; the growth table scales linearly in each.
- `n_yed = 1`: a redeem or transfer that gathers more token inputs adds one holder spend each.
